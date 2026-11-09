import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import {
  WorldKnowledgePanel,
  createFakeWorldKnowledgeTransport,
  filterNewsItems,
  formatConfidenceLevel,
  formatLearnedTime,
  mergeNewsItems,
} from '../../../src/features/world-knowledge';
import type { WorldNewsItem } from '../../../src/features/world-knowledge';

const SAMPLE_PLAYER_ITEM: WorldNewsItem = {
  fact_id: 'fact_player_from_innkeeper',
  holder_id: 'player',
  knowledge_type: 'confirmed',
  title: '掌柜告知灰羽商队改道东谷',
  content: '酒馆掌柜老周告诉你，灰羽商队因邻国加税改走东谷山道，生铁将推迟三天运抵小镇。',
  channel: 'conversation',
  source_person_id: 'npc:innkeeper',
  source_label: '当面交谈 · 酒馆掌柜老周',
  learned_minute: 50,
  confidence: 0.85,
  status: 'active',
  revision: 1,
};

const SAMPLE_RUMOR_ITEM: WorldNewsItem = {
  fact_id: 'fact_player_tavern_rumor',
  holder_id: 'player',
  knowledge_type: 'rumor',
  title: '东谷山道近期多雾难行',
  content: '酒客们议论东谷山道入冬后常有浓雾，车马通行比往常更慢。',
  channel: 'rumor',
  source_person_id: null,
  source_label: '道听途说',
  learned_minute: 55,
  confidence: 0.4,
  status: 'active',
  revision: 2,
};

describe('WorldKnowledgePanel mobile UI & isolation tests', () => {
  it('1. 玩家尚未获知任何消息时展示零泄漏空态，不暴露后台隐藏事件数', async () => {
    const transport = createFakeWorldKnowledgeTransport({
      adv_empty: [],
    });

    render(<WorldKnowledgePanel adventureId="adv_empty" transport={transport} />);

    expect(await screen.findByTestId('world-knowledge-empty')).toBeInTheDocument();
    expect(screen.getByText(/当前尚未通过任何渠道获知外界消息/)).toBeInTheDocument();
    expect(screen.getByText('世界消息与传闻（已获知 0 条）')).toBeInTheDocument();
  });

  it('2. 玩家通过渠道获知消息前后对比：刷新后展示消息标题、内容、来源渠道与可信度', async () => {
    const transport = createFakeWorldKnowledgeTransport({
      adv_north: [],
    });

    render(
      <WorldKnowledgePanel
        adventureId="adv_north"
        startsAt="0312-10-01T08:00"
        transport={transport}
      />,
    );

    expect(await screen.findByTestId('world-knowledge-empty')).toBeInTheDocument();

    // 玩家与酒馆掌柜交谈后获知消息
    transport.appendAdventureNews('adv_north', SAMPLE_PLAYER_ITEM);
    fireEvent.click(screen.getByRole('button', { name: '刷新消息' }));

    expect(await screen.findByText('掌柜告知灰羽商队改道东谷')).toBeInTheDocument();
    expect(screen.getByText(/生铁将推迟三天运抵小镇/)).toBeInTheDocument();
    expect(screen.getByText('来源：当面交谈 · 酒馆掌柜老周')).toBeInTheDocument();
    expect(screen.getByText('可信度 85% · 渠道确凿')).toBeInTheDocument();
    expect(screen.getByText(/0312-10-01 08:50（第 50 分钟）/)).toBeInTheDocument();
  });

  it('3. 检查点恢复分支切换：从已获知消息的主路线切换到获知前的旧分支，仍看不到后续消息', async () => {
    const transport = createFakeWorldKnowledgeTransport({
      adv_main: [],
    });
    // 在玩家获知消息前拍下检查点快照
    const preKnowSnapshot = transport.snapshotAdventureNews('adv_main');

    // 主路线获知消息
    transport.appendAdventureNews('adv_main', SAMPLE_PLAYER_ITEM);
    // 恢复到新分支 adv_branch_early
    transport.restoreAdventureNews('adv_branch_early', preKnowSnapshot);

    const view = render(<WorldKnowledgePanel adventureId="adv_main" transport={transport} />);
    expect(await screen.findByText('掌柜告知灰羽商队改道东谷')).toBeInTheDocument();

    // 切换到从旧检查点恢复的分支
    view.rerender(<WorldKnowledgePanel adventureId="adv_branch_early" transport={transport} />);
    expect(await screen.findByTestId('world-knowledge-empty')).toBeInTheDocument();
    expect(screen.queryByText('掌柜告知灰羽商队改道东谷')).toBeNull();
  });

  it('4. 支持按认知类型筛选与不透明游标分页加载更早消息', async () => {
    const transport = createFakeWorldKnowledgeTransport({
      adv_paged: [SAMPLE_RUMOR_ITEM, SAMPLE_PLAYER_ITEM],
    });

    render(<WorldKnowledgePanel adventureId="adv_paged" transport={transport} pageSize={1} />);

    // 第 1 页只显示最新的一条（传闻）
    expect(await screen.findByText('东谷山道近期多雾难行')).toBeInTheDocument();
    expect(screen.queryByText('掌柜告知灰羽商队改道东谷')).toBeNull();

    // 点击加载更早消息（使用不透明游标 kcur1_p1）
    fireEvent.click(screen.getByRole('button', { name: '加载更早消息' }));
    expect(await screen.findByText('掌柜告知灰羽商队改道东谷')).toBeInTheDocument();

    // 按“确认事实”筛选
    fireEvent.click(screen.getByRole('button', { name: '确认事实' }));
    expect(screen.getByText('掌柜告知灰羽商队改道东谷')).toBeInTheDocument();
    expect(screen.queryByText('东谷山道近期多雾难行')).toBeNull();
  });

  it('5. 失败状态显示错误提示并支持重试恢复，且迟到的旧旅程响应不污染新旅程', async () => {
    let resolveDelayed!: (value: { items: WorldNewsItem[]; next_before: string | null }) => void;
    const listWorldNews = vi
      .fn()
      .mockRejectedValueOnce(new Error('网络连接中断'))
      .mockResolvedValueOnce({ items: [SAMPLE_PLAYER_ITEM], next_before: null })
      .mockImplementationOnce(
        () =>
          new Promise(resolve => {
            resolveDelayed = resolve;
          }),
      )
      .mockResolvedValueOnce({ items: [], next_before: null });

    const view = render(
      <WorldKnowledgePanel adventureId="adv_err" transport={{ listWorldNews }} />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('网络连接中断');
    fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
    expect(await screen.findByText('掌柜告知灰羽商队改道东谷')).toBeInTheDocument();

    // 切换到 adv_slow（挂起），再迅速切换到 adv_clean（空）
    view.rerender(<WorldKnowledgePanel adventureId="adv_slow" transport={{ listWorldNews }} />);
    view.rerender(<WorldKnowledgePanel adventureId="adv_clean" transport={{ listWorldNews }} />);

    expect(await screen.findByTestId('world-knowledge-empty')).toBeInTheDocument();

    // adv_slow 的迟到数据返回，不得污染 adv_clean
    await act(async () => {
      resolveDelayed({ items: [SAMPLE_RUMOR_ITEM], next_before: null });
    });
    expect(screen.queryByText('东谷山道近期多雾难行')).toBeNull();
  });

  it('6. 纯逻辑格式化与去重合并函数在窄屏与边界输入下稳定工作', () => {
    expect(formatConfidenceLevel(0.85, 'confirmed')).toBe('可信度 85% · 渠道确凿');
    expect(formatConfidenceLevel(0.4, 'rumor')).toBe('可信度 40% · 传闻待核');
    expect(formatLearnedTime(1505)).toBe('第 2 天 01:05（1505 分钟）');
    const merged = mergeNewsItems([SAMPLE_PLAYER_ITEM], [SAMPLE_PLAYER_ITEM, SAMPLE_RUMOR_ITEM]);
    expect(merged).toHaveLength(2);
    expect(filterNewsItems(merged, 'rumor')).toEqual([SAMPLE_RUMOR_ITEM]);
  });
});

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  WorldStartJourneyHost,
  WorldStartPanel,
  createFakeWorldStartTransport,
} from '../../../src/features/world-start';
import { createFakeWorldEncountersTransport } from '../../../src/features/world-encounters';

describe('WorldStartPanel & WorldStartJourneyHost mobile UI tests (390px viewport, light/dark themes)', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 390,
    });
    document.documentElement.dataset.theme = 'light';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('1. 两份不同世界资料的完整流程验证：选择起点 -> 说明 API 用途 -> 启动 AI 分析 -> 查看玩家应知摘要（不含 NPC 私事或未获知事件）-> 确认进入旅程，无固定世界内容混入', async () => {
    const transport = createFakeWorldStartTransport();
    const onStarted = vi.fn();

    const view = render(
      <WorldStartPanel
        worldId="world_celain_harbor"
        transport={transport}
        mode="live"
        sessionId="sess_live_01"
        modelName="deepseek-chat"
        onJourneyStarted={onStarted}
      />,
    );

    // 验证第一份资料世界（青瓷海岬志）加载与真实调用前用途说明
    expect(await screen.findByText(/青瓷海岬志 · 根据资料生成开局/)).toBeInTheDocument();
    expect(screen.getByTestId('world-start-api-disclosure')).toHaveTextContent(
      /导入文档本身只保存资料，不会自动调用 AI/,
    );
    expect(screen.getByTestId('world-start-api-disclosure')).toHaveTextContent(/deepseek-chat/);
    expect(screen.getByTestId('world-start-batch-hint')).toHaveTextContent(/分 3 批提取并合并结果/);

    // 切换选择第二个候选起点并修改后切回第一个起点
    const dockPointBtn = screen.getByTestId('world-start-point-option-sp_celain_dock');
    fireEvent.click(dockPointBtn);
    expect(screen.getByTestId('world-start-location-input')).toHaveValue('白鸥灯塔下栈桥');

    // 启动 AI 开局分析
    fireEvent.click(screen.getByTestId('world-start-analyze-btn'));

    const draftCardA = await screen.findByTestId('world-start-draft-card');
    expect(draftCardA).toHaveTextContent('苏晚照');
    expect(draftCardA).toHaveTextContent('顾承钧');
    expect(draftCardA).toHaveTextContent('海盐公所晨间告示');
    expect(screen.getByTestId('world-start-conflict-warnings')).toHaveTextContent(/灯塔夜税/);
    expect(screen.getByTestId('world-start-inferred-notes')).toHaveTextContent(/旅程内推演补写/);

    // 严格验证：玩家摘要绝不显示后台 NPC 私事或未获知事件，且无固定世界名字混入
    expect(draftCardA.textContent).not.toMatch(/私盐船|暗灯信号|三号盐仓亏空|总督府密使/);
    expect(draftCardA.textContent).not.toMatch(/雾港|霜木镇|刻希弗|月渡|黎斯/);

    // 确认前未写入已提交旅程
    expect(transport.getCommittedAdventures('world_celain_harbor')).toHaveLength(0);

    // 确认进入旅程
    fireEvent.click(screen.getByTestId('world-start-confirm-btn'));
    expect(await screen.findByTestId('world-start-applied-card')).toHaveTextContent(
      /白鸥灯塔下栈桥/,
    );
    expect(onStarted).toHaveBeenCalledTimes(1);

    // 切换到深色主题与第二份完全不同的资料世界（赤砂星晷录）
    document.documentElement.dataset.theme = 'dark';
    view.rerender(
      <WorldStartPanel
        worldId="world_sand_clockwork"
        transport={transport}
        mode="fake"
        onJourneyStarted={onStarted}
      />,
    );

    expect(await screen.findByText(/赤砂星晷录 · 根据资料生成开局/)).toBeInTheDocument();
    expect(screen.getByTestId('world-start-location-input')).toHaveValue('第三分水闸集市');

    fireEvent.click(screen.getByTestId('world-start-analyze-btn'));
    const draftCardB = await screen.findByTestId('world-start-draft-card');
    expect(draftCardB).toHaveTextContent('娜迪娅');
    expect(draftCardB).toHaveTextContent('卡维');
    expect(draftCardB).toHaveTextContent('分水闸铜牌通告');
    expect(draftCardB.textContent).not.toMatch(/地下工坊|旱季将提前|王庭密使|苏晚照|白鸥灯塔/);
  });

  it('2. 状态覆盖：资料不足、普通冒险先行（减少设置步骤）、分析失败重试与恢复', async () => {
    const transport = createFakeWorldStartTransport();
    const onImport = vi.fn();
    const onStarted = vi.fn();

    const view = render(
      <WorldStartPanel
        worldId="world_empty_materials"
        transport={transport}
        onRequestImportMaterials={onImport}
        onJourneyStarted={onStarted}
      />,
    );

    // 1) 资料不足状态
    expect(await screen.findByTestId('world-start-phase-badge')).toHaveTextContent('资料不足');
    expect(screen.getByTestId('world-start-analyze-btn')).toBeDisabled();

    fireEvent.click(screen.getByTestId('world-start-go-import-btn'));
    expect(onImport).toHaveBeenCalledTimes(1);

    // 普通冒险先行：在资料不足时也可一键直接开启普通冒险
    fireEvent.click(screen.getByTestId('world-start-quick-start-insufficient-btn'));
    expect(await screen.findByTestId('world-start-applied-card')).toHaveTextContent(/普通冒险/);
    expect(onStarted).toHaveBeenCalledTimes(1);

    // 2) 分析失败与重试恢复
    transport.setSimulatedFailure('world_sand_clockwork', true, '上游模型连接超时');
    view.rerender(
      <WorldStartPanel
        worldId="world_sand_clockwork"
        transport={transport}
        onJourneyStarted={onStarted}
      />,
    );

    await screen.findByText(/赤砂星晷录 · 根据资料生成开局/);
    fireEvent.click(screen.getByTestId('world-start-analyze-btn'));

    expect(await screen.findByTestId('world-start-error-alert')).toHaveTextContent(
      '上游模型连接超时',
    );
    expect(screen.getByTestId('world-start-phase-badge')).toHaveTextContent('失败可重试');

    // 修复故障后点击重试按钮
    transport.setSimulatedFailure('world_sand_clockwork', false);
    fireEvent.click(screen.getByTestId('world-start-retry-btn'));

    const recoveredDraft = await screen.findByTestId('world-start-draft-card');
    expect(recoveredDraft).toHaveTextContent('重试分析开局');
  });

  it('3. 宿主最小挂载与返回旅程联动：确认进入旅程后可继续自由输入，且已有世界消息查证仍能填入同一编辑框', async () => {
    const startTransport = createFakeWorldStartTransport();
    const encounterTransport = createFakeWorldEncountersTransport({
      adv_world_celain_harbor_2: {
        adventure_id: 'adv_world_celain_harbor_2',
        player_location: '白鸥灯塔下栈桥',
        world_minute: 0,
        world_revision: 1,
        news_items: [
          {
            fact_id: 'fact_celain_notice_01',
            holder_id: 'player',
            knowledge_type: 'confirmed',
            title: '海盐公所晨间告示：今日优先核验外海大引盐船',
            content: '早班先核验持甲字盐引的船只。',
            channel: 'notice',
            source_person_id: null,
            source_label: '公开告示 · 海盐公所木牌',
            learned_minute: 0,
            confidence: 0.95,
            status: 'active',
            revision: 1,
          },
        ],
        npcs: [
          {
            npc_id: 'npc_su_lighthouse',
            name: '苏晚照',
            role: '白鸥灯塔守夜人',
            location: '白鸥灯塔下栈桥',
            current_activity: '整理铜油灯',
            current_action_kind: 'work',
            schedule_slot: 'morning',
            contactable: true,
            reason_code: 'NPC_CONTACTABLE',
            reason: '苏晚照在栈桥石阶旁可交谈。',
            knowledge_records: [],
          },
        ],
      },
    });
    const onActionSubmitted = vi.fn();

    render(
      <WorldStartJourneyHost
        worldId="world_celain_harbor"
        startTransport={startTransport}
        encounterTransport={encounterTransport}
        onActionSubmitted={onActionSubmitted}
      />,
    );

    await screen.findByText(/青瓷海岬志 · 根据资料生成开局/);
    fireEvent.click(screen.getByTestId('world-start-analyze-btn'));
    await screen.findByTestId('world-start-draft-card');
    fireEvent.click(screen.getByTestId('world-start-confirm-btn'));

    // 确认后激活旅程工作区
    expect(await screen.findByTestId('host-active-journey-workspace')).toBeInTheDocument();

    // 1) 点击世界消息查证建议，验证其能直接填入宿主的行动编辑框
    const askSuBtn = await screen.findByTestId(
      'encounter-option-opt_fact_celain_notice_01_ask_npc_su_lighthouse',
    );
    fireEvent.click(askSuBtn);

    const hostTextarea = screen.getByTestId('host-free-action-textarea') as HTMLTextAreaElement;
    expect(hostTextarea.value).toContain('向苏晚照');

    // 2) 玩家继续在编辑框自由补充文字并发送行动
    fireEvent.change(hostTextarea, {
      target: { value: `${hostTextarea.value}，顺便请教早潮退去的时刻。` },
    });
    fireEvent.click(screen.getByTestId('host-send-free-action-btn'));

    expect(onActionSubmitted).toHaveBeenCalledTimes(1);
    expect(onActionSubmitted.mock.calls[0][0]).toContain('顺便请教早潮退去的时刻');
  });

  it('4. 离开或切换世界时清理过期响应：旧世界的慢分析请求不会污染新世界界面', async () => {
    const transport = createFakeWorldStartTransport();
    const view = render(
      <WorldStartPanel worldId="world_celain_harbor" transport={transport} />,
    );

    await screen.findByText(/青瓷海岬志 · 根据资料生成开局/);

    // 设置青瓷海岬志的分析响应延迟 140ms，赤砂星晷录无延迟
    transport.setResponseDelay('world_celain_harbor', 140);
    transport.setResponseDelay('world_sand_clockwork', 0);

    fireEvent.click(screen.getByTestId('world-start-analyze-btn'));
    expect(screen.getByTestId('world-start-phase-badge')).toHaveTextContent('分析中');

    // 在青瓷海岬志分析尚未返回前，立即切换到赤砂星晷录
    view.rerender(<WorldStartPanel worldId="world_sand_clockwork" transport={transport} />);

    expect(await screen.findByText(/赤砂星晷录 · 根据资料生成开局/)).toBeInTheDocument();
    await new Promise(resolve => setTimeout(resolve, 180));

    // 旧世界的慢响应已被中止并丢弃，当前页面仍为赤砂星晷录的等待分析状态
    expect(screen.getByTestId('world-start-phase-badge')).toHaveTextContent('等待分析');
    expect(screen.queryByTestId('world-start-draft-card')).toBeNull();
    expect(screen.queryByText(/苏晚照/)).toBeNull();
  });
});

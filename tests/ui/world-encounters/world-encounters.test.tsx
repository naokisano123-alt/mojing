import React, { useState } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  WorldEncounterPanel,
  createInnkeeperCaravanEncounterScenario,
} from '../../../src/features/world-encounters';

describe('WorldEncounterPanel mobile UI & active inquiry flow tests (390px mobile width)', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 390,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('1. 手机端折叠、触控与中文阅读：展示传闻卡片与三类行动建议（询问人物、查阅公告、自由编辑）', async () => {
    const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_ui_01');

    render(<WorldEncounterPanel adventureId={adventureId} transport={transport} />);

    expect(
      await screen.findByText('市井传闻：灰鬃商队迟迟未至'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('encounter-world-minute')).toHaveTextContent(
      '当前地点：霜木镇 · 第 240 分钟',
    );

    const martaBtn = screen.getByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_marta_innkeeper',
    );
    const noticeBtn = screen.getByTestId(
      'encounter-option-opt_fact_player_initial_rumor_check_notice',
    );
    const freeEditBtn = screen.getByTestId(
      'encounter-option-opt_fact_player_initial_rumor_free_edit',
    );

    expect(martaBtn).toHaveTextContent('询问玛塔老板娘');
    expect(noticeBtn).toHaveTextContent('查阅霜木镇公告栏');
    expect(freeEditBtn).toHaveTextContent('自由编辑查证行动');
  });

  it('2. 点击建议项先写入宿主现有输入区供编辑，选项本身绝不推进时间或自动确认回合', async () => {
    const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_ui_02');
    const prepareSpy = vi.spyOn(transport, 'prepareDraft');
    const commitSpy = vi.spyOn(transport, 'commitDraft');
    const onDraftChange = vi.fn();
    const onExpand = vi.fn();

    render(
      <WorldEncounterPanel
        adventureId={adventureId}
        transport={transport}
        onComposerDraftChange={onDraftChange}
        onExpandComposer={onExpand}
      />,
    );

    const martaBtn = await screen.findByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_marta_innkeeper',
    );
    fireEvent.click(martaBtn);

    expect(onExpand).toHaveBeenCalledWith(true);
    expect(onDraftChange).toHaveBeenCalledTimes(1);
    expect(onDraftChange.mock.calls[0][0]).toContain('向玛塔老板娘');

    // 选项点击本身不发起草案或确认请求，世界时间仍保持第 240 分钟
    expect(prepareSpy).not.toHaveBeenCalled();
    expect(commitSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('encounter-world-minute')).toHaveTextContent('第 240 分钟');
  });

  it('3. 旅店店主转述商队改道完整闭环：选项 → 编辑行动 → 生成草案 → 确认回合 → 世界消息新增转述记录', async () => {
    const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_ui_03');

    function HostHarness() {
      const [draftText, setDraftText] = useState('');
      return (
        <WorldEncounterPanel
          adventureId={adventureId}
          transport={transport}
          composerDraft={draftText}
          onComposerDraftChange={next => setDraftText(next)}
        />
      );
    }

    render(<HostHarness />);

    const martaBtn = await screen.findByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_marta_innkeeper',
    );
    fireEvent.click(martaBtn);

    const editor = screen.getByTestId('encounter-action-editor') as HTMLTextAreaElement;
    fireEvent.change(editor, {
      target: {
        value: `${editor.value}并向她核实灰鬃商队改走白河谷地的具体原因。`,
      },
    });

    fireEvent.click(screen.getByTestId('encounter-prepare-draft-btn'));

    const draftCard = await screen.findByTestId('encounter-draft-card');
    expect(draftCard).toHaveAttribute('data-outcome', 'clue');
    expect(screen.getByTestId('encounter-draft-reply')).toHaveTextContent(
      /因灰岩边关新加三成生铁税，商队已改走东侧白河谷地水陆联运线/,
    );
    // 确认前世界消息仍为 1 条
    expect(screen.getByText('世界消息与主动查证（已获知 1 条）')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('encounter-commit-draft-btn'));

    await waitFor(() => {
      expect(screen.getByText('世界消息与主动查证（已获知 2 条）')).toBeInTheDocument();
    });
    expect(
      screen.getByText('玛塔掌柜转述：灰鬃商队因边关加税改走白河谷地'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('encounter-world-minute')).toHaveTextContent('第 250 分钟');
  });

  it('4. NPC 不知情与途中离开：询问不知情的雷恩队长或已前往寒铁城的韩掌柜，不产生伪造消息', async () => {
    const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_ui_04');

    render(<WorldEncounterPanel adventureId={adventureId} transport={transport} />);

    // 询问不知情的雷恩队长
    const rennBtn = await screen.findByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_renn_captain',
    );
    fireEvent.click(rennBtn);
    fireEvent.click(screen.getByTestId('encounter-prepare-draft-btn'));

    const unknownCard = await screen.findByTestId('encounter-draft-card');
    expect(unknownCard).toHaveAttribute('data-outcome', 'unknown');
    expect(screen.queryByTestId('encounter-transmissions-preview')).toBeNull();

    // 寻找已动身前往寒铁城的韩掌柜（途中离开）
    const hanBtn = screen.getByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_han_shopkeeper',
    );
    expect(hanBtn).toHaveAttribute('data-contactable', 'false');
    fireEvent.click(hanBtn);
    fireEvent.click(screen.getByTestId('encounter-prepare-draft-btn'));

    const departedCard = await screen.findByTestId('encounter-draft-card');
    expect(departedCard).toHaveAttribute('data-outcome', 'npc_departed');
    expect(screen.getByTestId('encounter-draft-reply')).toHaveTextContent(/已动身前往「寒铁城」/);
  });

  it('5. 互相冲突的说法：先听取老巴克误信传言，再向玛塔老板娘核实，展示冲突提示并同时保留两份认知', async () => {
    const { adventureId, transport } = createInnkeeperCaravanEncounterScenario('adv_ui_05');

    render(<WorldEncounterPanel adventureId={adventureId} transport={transport} />);

    const buckBtn = await screen.findByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_buck_porter',
    );
    fireEvent.click(buckBtn);
    fireEvent.click(screen.getByTestId('encounter-prepare-draft-btn'));
    await screen.findByTestId('encounter-draft-card');
    fireEvent.click(screen.getByTestId('encounter-commit-draft-btn'));

    await waitFor(() => {
      expect(screen.getByText('世界消息与主动查证（已获知 2 条）')).toBeInTheDocument();
    });

    const martaBtn = screen.getByTestId(
      'encounter-option-opt_fact_player_initial_rumor_ask_npc_marta_innkeeper',
    );
    fireEvent.click(martaBtn);
    fireEvent.click(screen.getByTestId('encounter-prepare-draft-btn'));

    const conflictCard = await screen.findByTestId('encounter-draft-card');
    expect(conflictCard).toHaveAttribute('data-outcome', 'conflicting');
    expect(screen.getByTestId('encounter-conflict-note')).toHaveTextContent(/存在冲突/);
  });

  it('6. 晚到响应隔离与分支切换：切换旅程后丢弃旧旅程慢响应，且旧分支恢复后看不到主线后续获知的消息', async () => {
    const { transport } = createInnkeeperCaravanEncounterScenario('adv_slow');
    const preKnowSnap = transport.snapshotBranch('adv_slow');
    transport.restoreBranch('adv_fast_branch', preKnowSnap);

    // 主线完成交谈使消息变为 2 条，而 adv_fast_branch 仍为 1 条
    const d = await transport.prepareDraft('adv_slow', {
      fact_id: 'fact_player_initial_rumor',
      option_kind: 'ask_npc',
      target_npc_id: 'npc_marta_innkeeper',
      action_text: '询问玛塔老板娘',
    });
    await transport.commitDraft('adv_slow', d.draft_id, { commit_world_minute: 250 });

    transport.setResponseDelay('adv_slow', 120);
    transport.setResponseDelay('adv_fast_branch', 0);

    const view = render(<WorldEncounterPanel adventureId="adv_slow" transport={transport} />);
    // 在 adv_slow 尚未返回前立即切换到旧分支 adv_fast_branch
    view.rerender(<WorldEncounterPanel adventureId="adv_fast_branch" transport={transport} />);

    expect(
      await screen.findByText('世界消息与主动查证（已获知 1 条）'),
    ).toBeInTheDocument();
    await new Promise(r => setTimeout(r, 160));
    // 迟到的 adv_slow 响应绝不污染当前 adv_fast_branch 页面
    expect(screen.getByText('世界消息与主动查证（已获知 1 条）')).toBeInTheDocument();
    expect(
      screen.queryByText('玛塔掌柜转述：灰鬃商队因边关加税改走白河谷地'),
    ).toBeNull();
  });
});

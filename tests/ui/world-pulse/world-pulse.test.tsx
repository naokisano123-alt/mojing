import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  WorldPulsePanel,
  createFakeWorldPulseTransport,
  formatPulseUsageSummary,
  sanitizePulseStatus,
} from '../../../src/features/world-pulse';
import type { WorldPulseStatus, WorldPulseTransport } from '../../../src/features/world-pulse';

describe('WorldPulsePanel mobile UI & lifecycle contract tests (390px mobile width)', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 390,
    });
    Object.defineProperty(document, 'visibilityState', {
      writable: true,
      configurable: true,
      value: 'visible',
    });
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('1. 独立连接优先展示：提交后仅绑定会话不自动开启脉动，且立即清空内存与 DOM 中的 API Key', async () => {
    const transport = createFakeWorldPulseTransport({
      adv_live_1: { enabled: false, mode: 'unbound', state: 'off' },
    });
    const connectSpy = vi.spyOn(transport, 'connect');
    const setEnabledSpy = vi.spyOn(transport, 'setEnabled');

    render(<WorldPulsePanel adventureId="adv_live_1" adventureMode="live" transport={transport} />);

    // 默认优先展示独立 API 连接标签页
    const indTab = await screen.findByTestId('pulse-tab-independent');
    expect(indTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('pulse-state-badge')).toHaveTextContent('已关闭');

    const urlInput = screen.getByTestId('pulse-base-url-input');
    const modelInput = screen.getByTestId('pulse-model-input');
    const keyInput = screen.getByTestId('pulse-api-key-input');

    expect(modelInput).toHaveValue('deepseek-flash');
    fireEvent.change(urlInput, { target: { value: 'https://api.deepseek.com' } });
    fireEvent.change(modelInput, { target: { value: 'deepseek-chat' } });
    fireEvent.change(keyInput, { target: { value: 'sk-pulse-secret-987654' } });

    fireEvent.click(screen.getByTestId('pulse-connect-independent-btn'));

    await waitFor(() => {
      expect(connectSpy).toHaveBeenCalledWith(
        'adv_live_1',
        {
          mode: 'independent',
          base_url: 'https://api.deepseek.com',
          model: 'deepseek-chat',
          api_key: 'sk-pulse-secret-987654',
        },
        expect.any(Object),
      );
    });

    // 连接本身绝不自动调用 setEnabled(true)，脉动保持关闭
    expect(setEnabledSpy).not.toHaveBeenCalled();
    expect(await screen.findByTestId('pulse-bound-model')).toHaveTextContent('deepseek-chat');
    expect(screen.getByTestId('pulse-state-badge')).toHaveTextContent('已关闭');

    // 提交成功后密码框卸载，DOM 与存储中均不含 Key
    expect(screen.queryByTestId('pulse-api-key-input')).toBeNull();
    expect(document.body.innerHTML).not.toContain('sk-pulse-secret-987654');
    expect(JSON.stringify(localStorage)).not.toContain('sk-pulse-secret-987654');
    expect(JSON.stringify(sessionStorage)).not.toContain('sk-pulse-secret-987654');
  });

  it('2. 支持复用当前叙事会话，且显式启停时首次启用明确提示额外消耗，关闭保留已有会话', async () => {
    const transport = createFakeWorldPulseTransport({
      adv_reuse: { enabled: false, mode: 'unbound', state: 'off' },
    });
    const connectSpy = vi.spyOn(transport, 'connect');
    const setEnabledSpy = vi.spyOn(transport, 'setEnabled');

    render(
      <WorldPulsePanel
        adventureId="adv_reuse"
        adventureMode="live"
        narrationSessionId="narr_sess_888"
        narrationModel="deepseek-reasoner"
        transport={transport}
      />,
    );

    // 切换到复用当前叙事会话
    fireEvent.click(await screen.findByTestId('pulse-tab-reuse'));
    expect(screen.getByTestId('pulse-reuse-panel')).toHaveTextContent('deepseek-reasoner');

    fireEvent.click(screen.getByTestId('pulse-connect-reuse-btn'));

    await waitFor(() => {
      expect(connectSpy).toHaveBeenCalledWith(
        'adv_reuse',
        {
          mode: 'reuse_narration',
          narration_session_id: 'narr_sess_888',
        },
        expect.any(Object),
      );
    });
    expect(setEnabledSpy).not.toHaveBeenCalled();

    // 点击“开启世界脉动”，首次启用弹出额外消耗确认框
    fireEvent.click(screen.getByTestId('pulse-enable-btn'));
    const confirmDialog = await screen.findByTestId('pulse-cost-confirm-dialog');
    expect(confirmDialog).toHaveTextContent('产生 Token 消耗');

    // 点击确认开启后才真正调用 setEnabled(true)
    fireEvent.click(screen.getByTestId('pulse-confirm-enable-btn'));
    await waitFor(() => {
      expect(setEnabledSpy).toHaveBeenCalledWith('adv_reuse', true, expect.any(Object));
    });
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');

    // 点击“关闭世界脉动”，关闭后状态回到已关闭，但复用的会话模式仍然保留
    fireEvent.click(screen.getByTestId('pulse-disable-btn'));
    await waitFor(() => {
      expect(setEnabledSpy).toHaveBeenCalledWith('adv_reuse', false, expect.any(Object));
    });
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('已关闭');
    expect(screen.getByTestId('pulse-status-card')).toHaveTextContent('复用叙事会话');
  });

  it('3. 会话失效或服务重启后进入 waiting_for_model 状态，明确提示等待重连且不静默切换 Key', async () => {
    const transport = createFakeWorldPulseTransport({
      adv_exp: {
        enabled: true,
        mode: 'independent',
        state: 'ready',
        model: 'deepseek-chat',
      },
    });

    render(<WorldPulsePanel adventureId="adv_exp" adventureMode="live" transport={transport} />);
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');

    // 模拟后端重启或会话过期
    transport.expireSession('adv_exp', 'MODEL_SESSION_EXPIRED');
    fireEvent.click(screen.getByTestId('pulse-refresh-status-btn'));

    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('等待重连');
    expect(screen.getByTestId('pulse-reconnect-banner')).toHaveTextContent(
      '不会静默切换到其他 Key',
    );
    // 自动展开重连表单供用户重新绑定
    expect(screen.getByTestId('pulse-independent-form')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('pulse-api-key-input'), {
      target: { value: 'sk-reconnected-key' },
    });
    fireEvent.click(screen.getByTestId('pulse-connect-independent-btn'));

    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');
    expect(screen.queryByTestId('pulse-api-key-input')).toBeNull();
  });

  it('4. 展示当游戏日实际/估算 Token、可能计费请求、超时提示与预算不足状态', async () => {
    const transport = createFakeWorldPulseTransport({
      adv_budget: {
        enabled: true,
        mode: 'independent',
        state: 'budget_limited',
        model: 'deepseek-chat',
        reason_code: 'DAILY_TOKEN_BUDGET_EXHAUSTED',
        day_usage: {
          game_day: 2,
          actual_tokens: 18500,
          estimated_tokens: 5500,
          billed_calls: 9,
          timeout_maybe_billed_calls: 1,
          timeout_maybe_billed_tokens: 1200,
          daily_call_budget: 12,
          daily_token_budget: 24000,
          remaining_calls: 3,
          remaining_tokens: 0,
        },
      },
    });

    render(<WorldPulsePanel adventureId="adv_budget" adventureMode="live" transport={transport} />);

    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('预算不足已延后');
    expect(screen.getByTestId('pulse-budget-banner')).toHaveTextContent(
      '普通叙事不受影响',
    );
    expect(screen.getByTestId('pulse-actual-tokens')).toHaveTextContent('18,500');
    expect(screen.getByTestId('pulse-estimated-tokens')).toHaveTextContent('5,500');
    expect(screen.getByTestId('pulse-billed-calls')).toHaveTextContent('9 次');
    expect(screen.getByTestId('pulse-timeout-warning')).toHaveTextContent('含 1 次超时');
  });

  it('5. 页面可见性与断网处理：进入后台或断网结束活动租约，恢复前台重新续期', async () => {
    const transport = createFakeWorldPulseTransport({
      adv_vis: {
        enabled: true,
        mode: 'independent',
        state: 'ready',
        model: 'deepseek-chat',
      },
    });

    render(<WorldPulsePanel adventureId="adv_vis" adventureMode="live" transport={transport} />);
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');

    // 切入后台（hidden）触发 setActivity(false)
    Object.defineProperty(document, 'visibilityState', {
      writable: true,
      configurable: true,
      value: 'hidden',
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(transport.isAdventureActive('adv_vis')).toBe(false);
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('离线已暂停');

    // 回到前台（visible）触发 setActivity(true)
    Object.defineProperty(document, 'visibilityState', {
      writable: true,
      configurable: true,
      value: 'visible',
    });
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    await waitFor(() => {
      expect(transport.isAdventureActive('adv_vis')).toBe(true);
    });
    expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');
  });

  it('6. 切旅程与并发隔离：切换旅程时结束旧旅程活动租约、清空未提交 Key，并丢弃旧旅程迟到响应', async () => {
    let resolveSlowA!: (value: WorldPulseStatus) => void;
    const setActivity = vi.fn().mockResolvedValue({ active: false });
    const getStatus = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<WorldPulseStatus>(resolve => {
            resolveSlowA = resolve;
          }),
      )
      .mockResolvedValueOnce({
        enabled: false,
        mode: 'unbound',
        state: 'off',
        model: null,
      });

    const customTransport: WorldPulseTransport = {
      getStatus,
      connect: vi.fn(),
      setEnabled: vi.fn(),
      setActivity,
    };

    const view = render(
      <WorldPulsePanel adventureId="adv_slow_a" adventureMode="live" transport={customTransport} />,
    );

    // 在 adv_slow_a 输入了一半的 API Key
    fireEvent.change(screen.getByTestId('pulse-api-key-input'), {
      target: { value: 'sk-half-typed-secret' },
    });
    expect(screen.getByTestId('pulse-api-key-input')).toHaveValue('sk-half-typed-secret');

    // 切换到 adv_fast_b
    view.rerender(
      <WorldPulsePanel adventureId="adv_fast_b" adventureMode="live" transport={customTransport} />,
    );

    // 验证旧旅程 adv_slow_a 的活动租约被主动结束，且未提交的 Key 被立即清空
    expect(setActivity).toHaveBeenCalledWith('adv_slow_a', false);
    expect(await screen.findByTestId('pulse-api-key-input')).toHaveValue('');

    // 此时 adv_slow_a 的迟到响应才返回（显示已启用且模型为 old-model-a），绝不能污染 adv_fast_b
    await act(async () => {
      resolveSlowA({
        enabled: true,
        mode: 'independent',
        state: 'ready',
        model: 'old-model-a',
      });
    });

    expect(screen.queryByText('old-model-a')).toBeNull();
    expect(screen.getByTestId('pulse-state-badge')).toHaveTextContent('已关闭');
  });

  it('7. 纯函数状态清洗与用量格式化验证', () => {
    const dirty = {
      enabled: true,
      mode: 'independent',
      state: 'ready',
      model: 'deepseek-chat',
      api_key: 'sk-must-be-stripped',
      session_id: 'sess-must-be-stripped',
      hidden_events: ['evt_secret'],
    };
    const clean = sanitizePulseStatus(dirty);
    expect('api_key' in (clean as unknown as Record<string, unknown>)).toBe(false);
    expect('session_id' in (clean as unknown as Record<string, unknown>)).toBe(false);
    expect('hidden_events' in (clean as unknown as Record<string, unknown>)).toBe(false);

    const formatted = formatPulseUsageSummary(
      {
        actual_tokens: 1024,
        estimated_tokens: 256,
        billed_calls: 2,
        simulated_calls: 3,
        simulated_tokens: 600,
      },
      'fake',
    );
    expect(formatted.actualTokensText).toContain('1,024');
    expect(formatted.simulatedUsageText).toContain('不计费');
  });

  it('显示后端提供的失败类别，刷新恢复后清除旧错误说明', async () => {
    let status = sanitizePulseStatus({
      enabled: true, mode: 'independent', state: 'ready', model: 'deepseek-flash',
      reason_code: 'PULSE_JOB_FAILED',
      reason_summary: '最近一次推演失败：模型回复达到输出长度上限（MODEL_OUTPUT_TRUNCATED）。结果未写入世界。',
      recent_jobs: [{ error_summary: '隐藏事件正文不应进入界面' }],
    });
    const transport: WorldPulseTransport = {
      getStatus: vi.fn(async () => status),
      connect: vi.fn(async () => status),
      setEnabled: vi.fn(async () => status),
      setActivity: vi.fn(async () => undefined),
    };
    render(<WorldPulsePanel adventureId="failure" adventureMode="live" transport={transport} />);
    expect(await screen.findByText(/MODEL_OUTPUT_TRUNCATED/)).toHaveTextContent('未写入世界');
    expect(document.body.textContent).not.toContain('隐藏事件正文');
    status = { ...status, reason_code: null, reason_summary: null };
    fireEvent.click(screen.getByRole('button', { name: '刷新脉动状态' }));
    await waitFor(() => expect(screen.queryByText(/MODEL_OUTPUT_TRUNCATED/)).toBeNull());
  });
});

it('只根据已保存的事件展示远景成功与模糊名，不拿用量或就绪状态猜成功', async () => {
  let status: WorldPulseStatus = sanitizePulseStatus({
    enabled: true, mode: 'independent', state: 'ready',
    day_usage: { actual_tokens: 3335, estimated_tokens: 0, billed_calls: 1 },
    simulation: { initialized: false, events: [], updated_minute: 0 },
  });
  const transport: WorldPulseTransport = {
    getStatus: vi.fn(async () => status), connect: vi.fn(async () => status),
    setEnabled: vi.fn(async () => status), setActivity: vi.fn(async () => undefined),
  };
  render(<WorldPulsePanel adventureId="saved-world" adventureMode="live" transport={transport} />);
  expect(await screen.findByTestId('pulse-state-badge')).toHaveTextContent('就绪运行');
  expect(screen.queryByText('远景模拟成功')).toBeNull();
  status = sanitizePulseStatus({ ...status, simulation: { initialized: true, updated_minute: 15,
    events: [{ kind: 'thread', label: '躁动的狮群', title: '不该传入正文的秘密计划' }, { kind: 'incident', label: '城内契约' }] } });
  fireEvent.click(screen.getByRole('button', { name: '刷新脉动状态' }));
  expect(await screen.findByText('远景模拟成功')).toBeInTheDocument();
  expect(screen.getByLabelText('远景模拟结果')).toHaveTextContent('大事件：躁动的狮群');
  expect(screen.getByLabelText('远景模拟结果')).toHaveTextContent('小事件：城内契约');
  expect(document.body.textContent).not.toContain('不该传入正文的秘密计划');
});

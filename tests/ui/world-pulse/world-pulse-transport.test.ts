import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PULSE_MODE_LABELS,
  PULSE_STATE_LABELS,
  createFakeWorldPulseTransport,
  formatPulseUsageSummary,
  sanitizePulseStatus,
} from '../../../src/features/world-pulse/api.ts';

test('world-pulse contract covers all 4 connection modes and 6 runtime states', () => {
  assert.equal(PULSE_MODE_LABELS.independent, '独立 API 会话（推荐）');
  assert.equal(PULSE_MODE_LABELS.reuse_narration, '复用叙事会话');
  assert.equal(PULSE_MODE_LABELS.fake, '模拟演示（无费用）');
  assert.equal(PULSE_MODE_LABELS.unbound, '未绑定模型会话');

  assert.equal(PULSE_STATE_LABELS.off, '已关闭');
  assert.equal(PULSE_STATE_LABELS.ready, '就绪运行');
  assert.equal(PULSE_STATE_LABELS.waiting_for_model, '等待重连');
  assert.equal(PULSE_STATE_LABELS.offline, '离线已暂停');
  assert.equal(PULSE_STATE_LABELS.budget_limited, '预算不足已延后');
  assert.equal(PULSE_STATE_LABELS.working, '后台推演中');
});

test('fake pulse transport enforces connect/enable separation, session expiry, activity lease, and credential scrubbing', async () => {
  const transport = createFakeWorldPulseTransport({
    adv_1: { enabled: false, mode: 'unbound', state: 'off' },
  });

  // 1. 初始状态关闭
  const initStatus = await transport.getStatus('adv_1');
  assert.equal(initStatus.enabled, false);
  assert.equal(initStatus.mode, 'unbound');
  assert.equal(initStatus.state, 'off');

  // 2. 独立连接只绑定会话，不自动启用脉动，也不暴露 api_key
  const connected = await transport.connect('adv_1', {
    mode: 'independent',
    base_url: 'https://api.deepseek.com',
    model: 'deepseek-chat',
    api_key: 'sk-test-only-in-memory',
  });
  assert.equal(connected.enabled, false);
  assert.equal(connected.mode, 'independent');
  assert.equal(connected.state, 'off');
  assert.equal(connected.model, 'deepseek-chat');
  assert.equal('api_key' in (connected as unknown as Record<string, unknown>), false);

  // 3. 显式启用脉动后进入 ready
  const enabled = await transport.setEnabled('adv_1', true);
  assert.equal(enabled.enabled, true);
  assert.equal(enabled.state, 'ready');

  // 4. 切后台或断网结束活动租约 -> 进入 offline
  const offline = await transport.setActivity('adv_1', false);
  assert.equal(offline.state, 'offline');
  assert.equal(transport.isAdventureActive('adv_1'), false);

  // 5. 恢复前台在线 -> 回到 ready
  const online = await transport.setActivity('adv_1', true);
  assert.equal(online.state, 'ready');
  assert.equal(transport.isAdventureActive('adv_1'), true);

  // 6. 会话过期或重启后 -> 进入 waiting_for_model，不静默切换 Key
  transport.expireSession('adv_1', 'MODEL_SESSION_EXPIRED');
  const waiting = await transport.getStatus('adv_1');
  assert.equal(waiting.enabled, true);
  assert.equal(waiting.state, 'waiting_for_model');
  assert.equal(waiting.model, null);

  // 7. 复用叙事会话恢复重连
  const reused = await transport.connect('adv_1', {
    mode: 'reuse_narration',
    narration_session_id: 'sess_narration_001',
  });
  assert.equal(reused.mode, 'reuse_narration');
  assert.equal(reused.state, 'ready');
  assert.equal('narration_session_id' in (reused as unknown as Record<string, unknown>), false);
});

test('sanitizePulseStatus strips credentials and formatPulseUsageSummary distinguishes actual vs estimated tokens', () => {
  const clean = sanitizePulseStatus({
    enabled: true,
    mode: 'independent',
    state: 'budget_limited',
    model: 'deepseek-chat',
    api_key: 'sk-leak',
    session_id: 'sess-leak',
    day_usage: {
      game_day: 1,
      actual_tokens: 4096,
      estimated_tokens: 1024,
      billed_calls: 4,
      timeout_maybe_billed_calls: 1,
      timeout_maybe_billed_tokens: 512,
      daily_call_budget: 12,
      daily_token_budget: 24000,
      remaining_calls: 8,
      remaining_tokens: 18880,
    },
  });

  const rawRecord = clean as unknown as Record<string, unknown>;
  assert.equal('api_key' in rawRecord, false);
  assert.equal('session_id' in rawRecord, false);

  const summary = formatPulseUsageSummary(clean.day_usage, clean.mode);
  assert.equal(summary.actualTokensText.includes('4,096'), true);
  assert.equal(summary.estimatedTokensText.includes('1,024'), true);
  assert.equal(summary.billedCallsText.includes('4 次'), true);
  assert.equal(Boolean(summary.timeoutWarningText?.includes('1 次')), true);
});

/**
 * M3 第二轮手机端世界脉动传输层、状态脱敏归一、用量格式化与独立 Fake 替身
 */

import { request } from '../../api/client.ts';
import type {
  FakeWorldPulseTransport,
  PulseActivityResponse,
  PulseConnectConfig,
  PulseConnectionMode,
  PulseDayUsageSummary,
  PulseRequestOptions,
  PulseRuntimeState,
  WorldPulseStatus,
  WorldPulseTransport,
} from './types.ts';

export const VALID_PULSE_MODES: ReadonlyArray<PulseConnectionMode> = [
  'fake',
  'independent',
  'reuse_narration',
  'unbound',
];

export const VALID_PULSE_STATES: ReadonlyArray<PulseRuntimeState> = [
  'off',
  'ready',
  'waiting_for_model',
  'offline',
  'budget_limited',
  'working',
];

export const PULSE_MODE_LABELS: Record<PulseConnectionMode, string> = {
  independent: '独立 API 会话（推荐）',
  reuse_narration: '复用叙事会话',
  fake: '模拟演示（无费用）',
  unbound: '未绑定模型会话',
};

export const PULSE_STATE_LABELS: Record<PulseRuntimeState, string> = {
  off: '已关闭',
  ready: '就绪运行',
  waiting_for_model: '等待重连',
  offline: '离线已暂停',
  budget_limited: '预算不足已延后',
  working: '后台推演中',
};

export const PULSE_STATE_DESCRIPTIONS: Record<PulseRuntimeState, string> = {
  off: '世界脉动当前关闭，后台不会发起模型调用；普通冒险叙事可正常继续。',
  ready: '模型连接与前台在线状态就绪，确认回合推进游戏时间且存在到期事件时将在后台推演。',
  waiting_for_model: '模型会话未绑定、已过期或服务重启后已清理，请重新连接后继续脉动（不会静默切换到其他 Key）。',
  offline: '当前处于后台、断网或活动租约已结束，后台已暂停付费请求；恢复前台不会推进游戏时间或补演离线期间事件。',
  budget_limited: '当前游戏日或本轮模拟预算已达上限（或处于冷却期），后台脉动已自动延后，普通冒险叙事不受影响。',
  working: '后台正在推演到期世界事件，不阻塞前台回合确认与阅读。',
};

export const PULSE_REASON_LABELS: Record<string, string> = {
  PULSE_JOB_FAILED: '最近一次推演失败。普通冒险仍可继续，可关闭后重新开启脉动。',
  PULSE_SOURCE_REQUIRED: '请导入一份世界资料或确认至少一条设定，再重新开启脉动。',
  PULSE_DISABLED: '玩家已主动关闭世界脉动。',
  PULSE_DISABLED_OR_PAUSED: '世界脉动处于关闭或暂停状态。',
  MODEL_SESSION_UNBOUND: '尚未绑定脉动模型会话，请先配置独立连接或复用叙事会话。',
  MODEL_SESSION_EXPIRED: '模型会话已过期或服务已重启，请重新连接。',
  MODEL_AUTH_FAILED: '模型 API Key 认证失败，请重新输入有效凭据连接。',
  CLIENT_OFFLINE: '当前页面离线或处于后台，已暂停付费推演。',
  PULSE_DAILY_CALL_BUDGET_EXHAUSTED: '当游戏日脉动调用次数已达上限。',
  PULSE_DAILY_TOKEN_BUDGET_EXHAUSTED: '当游戏日脉动 Token 预算已达上限。',
  PULSE_ROUND_CALL_BUDGET_EXCEEDED: '本轮脉动调用次数已达上限。',
  PULSE_ROUND_TOKEN_BUDGET_EXCEEDED: '本轮脉动 Token 预算已达上限。',
  PULSE_COOLDOWN_ACTIVE: '脉动调用冷却中。',
  DAILY_CALL_BUDGET_EXHAUSTED: '当游戏日脉动调用次数已达上限，将在下一游戏日恢复。',
  DAILY_TOKEN_BUDGET_EXHAUSTED: '当游戏日脉动 Token 预算已达上限，将在下一游戏日恢复。',
  ROUND_BUDGET_EXHAUSTED: '本轮脉动预算已达上限，已自动延后。',
  COOLDOWN_ACTIVE: '脉动调用冷却中，稍后按已确认时间继续。',
};

function clampNonNegativeInt(val: unknown, fallback = 0): number {
  const num = Number(val);
  return Number.isFinite(num) && num >= 0 ? Math.floor(num) : fallback;
}

export function normalizeDayUsage(raw: unknown): PulseDayUsageSummary | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;
  return {
    game_day: obj.game_day !== undefined ? clampNonNegativeInt(obj.game_day, 1) : undefined,
    actual_tokens: clampNonNegativeInt(obj.actual_tokens),
    estimated_tokens: clampNonNegativeInt(obj.estimated_tokens),
    billed_calls: clampNonNegativeInt(obj.billed_calls ?? obj.total_calls),
    timeout_maybe_billed_calls:
      obj.timeout_maybe_billed_calls !== undefined
        ? clampNonNegativeInt(obj.timeout_maybe_billed_calls)
        : undefined,
    timeout_maybe_billed_tokens:
      obj.timeout_maybe_billed_tokens !== undefined
        ? clampNonNegativeInt(obj.timeout_maybe_billed_tokens)
        : undefined,
    simulated_calls:
      obj.simulated_calls !== undefined ? clampNonNegativeInt(obj.simulated_calls) : undefined,
    simulated_tokens:
      obj.simulated_tokens !== undefined ? clampNonNegativeInt(obj.simulated_tokens) : undefined,
    daily_call_budget:
      obj.daily_call_budget !== undefined ? clampNonNegativeInt(obj.daily_call_budget) : undefined,
    daily_token_budget:
      obj.daily_token_budget !== undefined ? clampNonNegativeInt(obj.daily_token_budget) : undefined,
    remaining_calls:
      obj.remaining_calls !== undefined ? clampNonNegativeInt(obj.remaining_calls) : undefined,
    remaining_tokens:
      obj.remaining_tokens !== undefined ? clampNonNegativeInt(obj.remaining_tokens) : undefined,
  };
}

/**
 * 清洗并归一化状态响应，剔除任何可能误传的 api_key、session_id、隐藏事件或任务负载
 */
export function sanitizePulseStatus(raw: unknown): WorldPulseStatus {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const enabled = Boolean(obj.enabled);

  const sessionObj =
    obj.session && typeof obj.session === 'object'
      ? (obj.session as Record<string, unknown>)
      : null;

  const rawMode = String(obj.mode ?? sessionObj?.mode ?? (obj.available === true && enabled ? 'fake' : 'unbound'));
  const mode: PulseConnectionMode = (VALID_PULSE_MODES as readonly string[]).includes(rawMode)
    ? (rawMode as PulseConnectionMode)
    : 'unbound';

  const rawState = String(obj.state ?? '');
  let state: PulseRuntimeState;
  if ((VALID_PULSE_STATES as readonly string[]).includes(rawState)) {
    state = rawState as PulseRuntimeState;
  } else if (!enabled) {
    state = 'off';
  } else if (obj.client_online === false) {
    state = 'offline';
  } else if (mode === 'unbound' || sessionObj?.ready === false) {
    state = 'waiting_for_model';
  } else {
    state = 'ready';
  }

  const rawModel = obj.model ?? sessionObj?.model;
  const model = typeof rawModel === 'string' && rawModel.trim() ? rawModel.trim() : null;

  const rawReasonCode = obj.reason_code ?? obj.error_code;
  const reason_code =
    typeof rawReasonCode === 'string' && rawReasonCode.trim() ? rawReasonCode.trim() : null;

  const rawReasonSummary = obj.reason_summary ?? obj.error_summary;
  const reason_summary =
    typeof rawReasonSummary === 'string' && rawReasonSummary.trim()
      ? rawReasonSummary.trim()
      : reason_code
        ? PULSE_REASON_LABELS[reason_code] ?? null
        : null;

  const usageRaw = obj.day_usage ?? obj.current_day_usage ?? obj.usage;
  const day_usage = normalizeDayUsage(usageRaw);

  const safeStatus: WorldPulseStatus = {
    enabled,
    mode,
    state,
    model,
    reason_code,
    reason_summary,
    day_usage,
  };
  if (typeof obj.world_minute === 'number' && Number.isFinite(obj.world_minute)) {
    safeStatus.world_minute = Math.max(0, Math.floor(obj.world_minute));
  }
  if (typeof obj.world_revision === 'number' && Number.isFinite(obj.world_revision)) {
    safeStatus.world_revision = Math.max(0, Math.floor(obj.world_revision));
  }
  return safeStatus;
}

export interface FormattedPulseUsage {
  actualTokensText: string;
  estimatedTokensText: string;
  billedCallsText: string;
  simulatedUsageText: string | null;
  budgetRemainingText: string | null;
  timeoutWarningText: string | null;
}

/**
 * 格式化当游戏日模型用量摘要，明确区分实际 Token、估算 Token、可能计费请求与模拟用量
 */
export function formatPulseUsageSummary(
  usage: PulseDayUsageSummary | null | undefined,
  mode: PulseConnectionMode,
): FormattedPulseUsage {
  const u = usage ?? {
    actual_tokens: 0,
    estimated_tokens: 0,
    billed_calls: 0,
  };
  const actualTokensText = `实际 Token：${u.actual_tokens.toLocaleString('zh-CN')}`;
  const estimatedTokensText = `估算 Token：${u.estimated_tokens.toLocaleString('zh-CN')}`;
  const billedCallsText = `可能计费请求：${u.billed_calls} 次`;

  const isSimulated = mode === 'fake' || (u.simulated_calls ?? 0) > 0 || (u.simulated_tokens ?? 0) > 0;
  const simulatedUsageText = isSimulated
    ? `模拟演示用量：${u.simulated_calls ?? 0} 次 / ${(u.simulated_tokens ?? 0).toLocaleString('zh-CN')} Token（不计费）`
    : null;

  let budgetRemainingText: string | null = null;
  if (u.remaining_tokens !== undefined || u.remaining_calls !== undefined) {
    const parts: string[] = [];
    if (u.remaining_calls !== undefined) {
      parts.push(
        u.daily_call_budget !== undefined
          ? `剩余调用 ${u.remaining_calls}/${u.daily_call_budget} 次`
          : `剩余调用 ${u.remaining_calls} 次`,
      );
    }
    if (u.remaining_tokens !== undefined) {
      parts.push(
        u.daily_token_budget !== undefined
          ? `剩余额度 ${u.remaining_tokens.toLocaleString('zh-CN')}/${u.daily_token_budget.toLocaleString('zh-CN')} Token`
          : `剩余额度 ${u.remaining_tokens.toLocaleString('zh-CN')} Token`,
      );
    }
    budgetRemainingText = parts.join(' · ');
  }

  const timeoutCalls = u.timeout_maybe_billed_calls ?? 0;
  const timeoutTokens = u.timeout_maybe_billed_tokens ?? 0;
  const timeoutWarningText =
    timeoutCalls > 0
      ? `含 ${timeoutCalls} 次超时/状态未明请求（估算 ${timeoutTokens.toLocaleString('zh-CN')} Token，上游可能已计费）`
      : null;

  return {
    actualTokensText,
    estimatedTokensText,
    billedCallsText,
    simulatedUsageText,
    budgetRemainingText,
    timeoutWarningText,
  };
}

/**
 * 默认同源 HTTP 传输实现（供 DeepSeekHarness 挂载至 /api/v1/adventures/{id}/world-pulse*）
 */
export const worldPulseApi: WorldPulseTransport = {
  async getStatus(adventureId: string, options?: PulseRequestOptions): Promise<WorldPulseStatus> {
    const raw = await request< unknown>(
      'GET',
      `/adventures/${encodeURIComponent(adventureId)}/world-pulse`,
      { signal: options?.signal },
    );
    return sanitizePulseStatus(raw);
  },

  async connect(
    adventureId: string,
    config: PulseConnectConfig,
    options?: PulseRequestOptions,
  ): Promise<WorldPulseStatus> {
    const payload =
      config.mode === 'independent'
        ? {
            mode: 'independent' as const,
            base_url: config.base_url.trim(),
            model: config.model.trim(),
            api_key: config.api_key.trim(),
          }
        : config.mode === 'reuse_narration'
          ? {
              mode: 'reuse_narration' as const,
              narration_session_id: config.narration_session_id.trim(),
            }
          : { mode: 'fake' as const };

    const raw = await request<unknown>(
      'POST',
      `/adventures/${encodeURIComponent(adventureId)}/world-pulse/session`,
      { body: payload, signal: options?.signal },
    );
    return sanitizePulseStatus(raw);
  },

  async setEnabled(
    adventureId: string,
    enabled: boolean,
    options?: PulseRequestOptions,
  ): Promise<WorldPulseStatus> {
    const raw = await request<unknown>(
      'PUT',
      `/adventures/${encodeURIComponent(adventureId)}/world-pulse`,
      { body: { enabled }, signal: options?.signal },
    );
    return sanitizePulseStatus(raw);
  },

  async setActivity(
    adventureId: string,
    active: boolean,
    options?: PulseRequestOptions,
  ): Promise<PulseActivityResponse> {
    const raw = await request<unknown>(
      'POST',
      `/adventures/${encodeURIComponent(adventureId)}/world-pulse/activity`,
      { body: { active }, signal: options?.signal },
    );
    if (raw && typeof raw === 'object' && 'state' in (raw as Record<string, unknown>)) {
      return sanitizePulseStatus(raw);
    }
    return raw as PulseActivityResponse;
  },
};

interface InternalFakePulseRecord {
  enabled: boolean;
  mode: PulseConnectionMode;
  model: string | null;
  sessionValid: boolean;
  clientActive: boolean;
  budgetLimited: boolean;
  working: boolean;
  reasonCode: string | null;
  dayUsage: PulseDayUsageSummary;
}

function deriveFakeState(rec: InternalFakePulseRecord): PulseRuntimeState {
  if (!rec.enabled) return 'off';
  if (!rec.sessionValid || rec.mode === 'unbound') return 'waiting_for_model';
  if (!rec.clientActive) return 'offline';
  if (rec.budgetLimited) return 'budget_limited';
  if (rec.working) return 'working';
  return 'ready';
}

/**
 * 供独立开发、契约自测与手机端 UI 测试使用的确定性内存 Fake Transport
 * 严格遵守契约：
 * - 连接与启用分离（connect 只绑定会话，不自动开启 enabled，不发测试计费请求）；
 * - 关闭脉动保留已有叙事会话与历史；
 * - 会话过期后转入 waiting_for_model，绝不静默切换到其他 Key；
 * - getStatus 不续期活动租约，setActivity(adventureId, active) 独立维护在线状态；
 * - 绝不在状态中返回 api_key 或 narration_session_id。
 */
export function createFakeWorldPulseTransport(
  initialByAdventure: Record<string, Partial<WorldPulseStatus>> = {},
): FakeWorldPulseTransport {
  const store = new Map<string, InternalFakePulseRecord>();
  const activityLog: Array<{ adventureId: string; active: boolean }> = [];

  function ensureRecord(adventureId: string): InternalFakePulseRecord {
    const existing = store.get(adventureId);
    if (existing) return existing;
    const init = initialByAdventure[adventureId] ?? {};
    const mode: PulseConnectionMode = init.mode ?? 'unbound';
    const enabled = Boolean(init.enabled ?? false);
    const state = init.state;
    const rec: InternalFakePulseRecord = {
      enabled,
      mode,
      model: init.model ?? (mode === 'fake' ? 'fake-pulse-v1' : null),
      sessionValid: mode !== 'unbound' && state !== 'waiting_for_model',
      clientActive: state !== 'offline',
      budgetLimited: state === 'budget_limited',
      working: state === 'working',
      reasonCode: init.reason_code ?? null,
      dayUsage: normalizeDayUsage(init.day_usage) ?? {
        game_day: 1,
        actual_tokens: 0,
        estimated_tokens: 0,
        billed_calls: 0,
        timeout_maybe_billed_calls: 0,
        timeout_maybe_billed_tokens: 0,
        simulated_calls: 0,
        simulated_tokens: 0,
        daily_call_budget: 12,
        daily_token_budget: 24000,
        remaining_calls: 12,
        remaining_tokens: 24000,
      },
    };
    store.set(adventureId, rec);
    return rec;
  }

  function buildStatus(rec: InternalFakePulseRecord): WorldPulseStatus {
    const state = deriveFakeState(rec);
    return sanitizePulseStatus({
      enabled: rec.enabled,
      mode: rec.mode,
      state,
      model: rec.sessionValid ? rec.model : null,
      reason_code: rec.reasonCode,
      day_usage: { ...rec.dayUsage },
    });
  }

  function checkSignal(options?: PulseRequestOptions) {
    if (options?.signal?.aborted) {
      const err = new Error('请求已取消');
      err.name = 'AbortError';
      throw err;
    }
  }

  return {
    async getStatus(adventureId: string, options?: PulseRequestOptions): Promise<WorldPulseStatus> {
      checkSignal(options);
      const rec = ensureRecord(adventureId);
      return buildStatus(rec);
    },

    async connect(
      adventureId: string,
      config: PulseConnectConfig,
      options?: PulseRequestOptions,
    ): Promise<WorldPulseStatus> {
      checkSignal(options);
      const rec = ensureRecord(adventureId);

      if (config.mode === 'independent') {
        if (!config.base_url.trim() || !config.model.trim() || !config.api_key.trim()) {
          throw new Error('请填写完整的 API 地址、模型名称和 API Key。');
        }
        if (!/^https?:\/\//i.test(config.base_url.trim())) {
          throw new Error('API 基础地址必须以 http:// 或 https:// 开头。');
        }
        rec.mode = 'independent';
        rec.model = config.model.trim();
        rec.sessionValid = true;
        rec.reasonCode = null;
      } else if (config.mode === 'reuse_narration') {
        if (!config.narration_session_id.trim()) {
          throw new Error('当前无可用的叙事模型会话，请先在冒险页连接叙事模型或改用独立 API 连接。');
        }
        rec.mode = 'reuse_narration';
        rec.model = rec.model || 'narration-shared-model';
        rec.sessionValid = true;
        rec.reasonCode = null;
      } else {
        rec.mode = 'fake';
        rec.model = 'fake-pulse-v1';
        rec.sessionValid = true;
        rec.reasonCode = null;
      }

      // 契约：connect 只绑定有效会话，本身不启用脉动、不测试付费请求
      return buildStatus(rec);
    },

    async setEnabled(
      adventureId: string,
      enabled: boolean,
      options?: PulseRequestOptions,
    ): Promise<WorldPulseStatus> {
      checkSignal(options);
      const rec = ensureRecord(adventureId);
      if (enabled && (!rec.sessionValid || rec.mode === 'unbound')) {
        rec.enabled = true;
        rec.reasonCode = 'MODEL_SESSION_UNBOUND';
        return buildStatus(rec);
      }
      rec.enabled = Boolean(enabled);
      if (!rec.enabled) {
        rec.working = false;
        rec.reasonCode = 'PULSE_DISABLED';
      } else {
        rec.clientActive = true;
        rec.reasonCode = null;
      }
      return buildStatus(rec);
    },

    async setActivity(
      adventureId: string,
      active: boolean,
      options?: PulseRequestOptions,
    ): Promise<WorldPulseStatus> {
      checkSignal(options);
      const rec = ensureRecord(adventureId);
      rec.clientActive = Boolean(active);
      if (!active && rec.enabled) {
        rec.reasonCode = 'CLIENT_OFFLINE';
      } else if (active && rec.reasonCode === 'CLIENT_OFFLINE') {
        rec.reasonCode = null;
      }
      activityLog.push({ adventureId, active: Boolean(active) });
      return buildStatus(rec);
    },

    expireSession(adventureId: string, reasonCode = 'MODEL_SESSION_EXPIRED') {
      const rec = ensureRecord(adventureId);
      rec.sessionValid = false;
      rec.model = null;
      rec.working = false;
      rec.reasonCode = reasonCode;
    },

    setBudgetLimited(
      adventureId: string,
      limited: boolean,
      reasonCode = 'DAILY_TOKEN_BUDGET_EXHAUSTED',
    ) {
      const rec = ensureRecord(adventureId);
      rec.budgetLimited = limited;
      rec.reasonCode = limited ? reasonCode : null;
      if (limited) {
        rec.dayUsage.remaining_tokens = 0;
      }
    },

    setWorking(adventureId: string, working: boolean) {
      const rec = ensureRecord(adventureId);
      rec.working = working;
    },

    recordUsage(adventureId: string, delta: Partial<PulseDayUsageSummary>) {
      const rec = ensureRecord(adventureId);
      rec.dayUsage = {
        ...rec.dayUsage,
        actual_tokens: rec.dayUsage.actual_tokens + (delta.actual_tokens ?? 0),
        estimated_tokens: rec.dayUsage.estimated_tokens + (delta.estimated_tokens ?? 0),
        billed_calls: rec.dayUsage.billed_calls + (delta.billed_calls ?? 0),
        timeout_maybe_billed_calls:
          (rec.dayUsage.timeout_maybe_billed_calls ?? 0) + (delta.timeout_maybe_billed_calls ?? 0),
        timeout_maybe_billed_tokens:
          (rec.dayUsage.timeout_maybe_billed_tokens ?? 0) + (delta.timeout_maybe_billed_tokens ?? 0),
        simulated_calls: (rec.dayUsage.simulated_calls ?? 0) + (delta.simulated_calls ?? 0),
        simulated_tokens: (rec.dayUsage.simulated_tokens ?? 0) + (delta.simulated_tokens ?? 0),
      };
      const usedTokens = rec.dayUsage.actual_tokens + rec.dayUsage.estimated_tokens;
      if (rec.dayUsage.daily_token_budget !== undefined) {
        rec.dayUsage.remaining_tokens = Math.max(0, rec.dayUsage.daily_token_budget - usedTokens);
      }
      if (rec.dayUsage.daily_call_budget !== undefined) {
        rec.dayUsage.remaining_calls = Math.max(
          0,
          rec.dayUsage.daily_call_budget - rec.dayUsage.billed_calls,
        );
      }
    },

    getActivityLog() {
      return [...activityLog];
    },

    isAdventureActive(adventureId: string) {
      return ensureRecord(adventureId).clientActive;
    },
  };
}

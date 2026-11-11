/**
 * M3 第二轮手机端世界脉动控制组件类型契约
 * 严格对应 docs/contracts/M3_WAVE2_API.md §手机端控制契约 与 M3_WAVE2_GEMINI_MA.md
 */

export type PulseConnectionMode = 'fake' | 'independent' | 'reuse_narration' | 'unbound';

export type PulseRuntimeState =
  | 'off'
  | 'ready'
  | 'waiting_for_model'
  | 'offline'
  | 'budget_limited'
  | 'working';

/**
 * 当游戏日模型用量汇总（区分实际/估算 Token、可能计费请求与模拟用量）
 */
export interface PulseDayUsageSummary {
  game_day?: number;
  actual_tokens: number;
  estimated_tokens: number;
  billed_calls: number;
  timeout_maybe_billed_calls?: number;
  timeout_maybe_billed_tokens?: number;
  simulated_calls?: number;
  simulated_tokens?: number;
  daily_call_budget?: number;
  daily_token_budget?: number;
  remaining_calls?: number;
  remaining_tokens?: number;
}

/**
 * getStatus(adventureId) 返回的安全脱敏状态 DTO
 * 严禁包含 api_key、session_id、narration_session_id、隐藏事件或任务负载
 */
export interface WorldPulseStatus {
  enabled: boolean;
  mode: PulseConnectionMode;
  state: PulseRuntimeState;
  model?: string | null;
  reason_code?: string | null;
  reason_summary?: string | null;
  world_minute?: number;
  world_revision?: number;
  day_usage?: PulseDayUsageSummary | null;
  simulation?: {
    initialized: boolean;
    events: { kind: 'thread' | 'incident'; label: string }[];
    updated_minute: number;
  };
}

/**
 * connect(adventureId, config) 提交的连接配置
 * - 独立模式提交 mode, base_url, model, api_key
 * - 复用模式提交 mode, narration_session_id
 */
export type PulseConnectConfig =
  | {
      mode: 'independent';
      base_url: string;
      model: string;
      api_key: string;
    }
  | {
      mode: 'reuse_narration';
      narration_session_id: string;
    }
  | {
      mode: 'fake';
    };

export interface PulseRequestOptions {
  signal?: AbortSignal;
}

export type PulseActivityResponse = WorldPulseStatus | { active?: boolean } | void;

/**
 * 可注入的手机端世界脉动传输层契约（支持同源 HTTP 与独立 Fake 验收）
 */
export interface WorldPulseTransport {
  getStatus(adventureId: string, options?: PulseRequestOptions): Promise<WorldPulseStatus>;
  connect(
    adventureId: string,
    config: PulseConnectConfig,
    options?: PulseRequestOptions,
  ): Promise<WorldPulseStatus>;
  setEnabled(
    adventureId: string,
    enabled: boolean,
    options?: PulseRequestOptions,
  ): Promise<WorldPulseStatus>;
  setActivity(
    adventureId: string,
    active: boolean,
    options?: PulseRequestOptions,
  ): Promise<PulseActivityResponse>;
}

export interface FakeWorldPulseTransport extends WorldPulseTransport {
  expireSession(adventureId: string, reasonCode?: string): void;
  setBudgetLimited(adventureId: string, limited: boolean, reasonCode?: string): void;
  setWorking(adventureId: string, working: boolean): void;
  recordUsage(adventureId: string, delta: Partial<PulseDayUsageSummary>): void;
  getActivityLog(): Array<{ adventureId: string; active: boolean }>;
  isAdventureActive(adventureId: string): boolean;
}

/**
 * M3 Wave 4 · 手机开局与游玩体验前端类型契约（Gemini Ma）
 * 严格对应 docs/collaboration/M3_WAVE4_GEMINI_MA.md 与 docs/contracts/M3_WAVE4_API.md
 *
 * 交付五类核心数据：
 * 1. 资料选择（WorldStartMaterialItem / WorldStartMaterialSelection）
 * 2. 起点（WorldStartPointOption / WorldStartPointInput）
 * 3. 分析状态（WorldStartPhase / WorldStartAnalysisStatus）
 * 4. 草案摘要（WorldStartDraftSummaryDto —— 仅含玩家应知内容，剥离后台 NPC 私事与未获知事件）
 * 5. 确认结果（WorldStartCommitResultDto —— 原子建立旅程并绑定来源，返回旅程后可自由输入与查证）
 */

import type { EncounterNewsItem } from '../world-encounters/types.ts';

/**
 * 开局分析与初始化阶段状态
 * 同时完整覆盖：
 * - M3_WAVE4_API.md：等待(waiting)、分析中(analyzing)、可确认(ready_to_confirm)、失败可重试(failed_retryable)、已应用(applied)
 * - M3_WAVE4_GEMINI_MA.md：资料不足(insufficient_materials)、分析中(analyzing)、失败重试(failed_retryable)、成功(applied)
 */
export type WorldStartPhase =
  | 'insufficient_materials'
  | 'waiting'
  | 'analyzing'
  | 'ready_to_confirm'
  | 'failed_retryable'
  | 'applied';

/**
 * 区分初次建立、失败后重试、重新开局与检查点分支开局
 */
export type WorldStartRunKind = 'initial' | 'retry' | 'reopen' | 'branch';

/**
 * 内容来源性质：资料明文支持 vs 资料空白处的合理推演补写
 */
export type WorldStartContentOrigin = 'canon' | 'inferred';

/**
 * 允许引用的世界资料来源项（1. 资料选择）
 */
export interface WorldStartMaterialItem {
  source_id: string;
  world_id: string;
  kind: 'document' | 'lore_entry';
  title: string;
  char_count: number;
  chunk_count: number;
  selected_by_default: boolean;
  excerpt_preview?: string;
}

/**
 * 资料选择汇总状态（1. 资料选择）
 */
export interface WorldStartMaterialSelection {
  world_id: string;
  has_usable_materials: boolean;
  selected_source_ids: string[];
  total_char_count: number;
  requires_batch_extraction: boolean;
  estimated_batches: number;
}

/**
 * 资料引用标注（仅指向当前世界内合法的 source_id）
 */
export interface WorldStartSourceCitation {
  source_id: string;
  title: string;
  kind: 'document' | 'lore_entry';
  snippet?: string;
}

/**
 * 候选起点项（2. 起点 —— 从当前世界资料或人工设定提取，禁止混入固定世界名字）
 */
export interface WorldStartPointOption {
  start_id: string;
  label: string;
  location: string;
  starts_at: string;
  character_name: string;
  character_role: string;
  summary: string;
  origin: WorldStartContentOrigin;
  source_refs: WorldStartSourceCitation[];
}

/**
 * 玩家提交的起点与开局输入（2. 起点）
 */
export interface WorldStartPointInput {
  start_id?: string | null;
  location: string;
  starts_at: string;
  character_name: string;
  character_role?: string;
  adventure_name: string;
}

/**
 * 分析状态与进度描述（3. 分析状态）
 */
export interface WorldStartAnalysisStatus {
  phase: WorldStartPhase;
  run_kind: WorldStartRunKind;
  attempt_count: number;
  uses_api_Notice_acknowledged: boolean;
  batch_progress?: {
    current_batch: number;
    total_batches: number;
    merged_chunks: number;
  } | null;
  error_code?: string | null;
  error_message?: string | null;
  can_retry: boolean;
  committed: boolean;
}

/**
 * 玩家应知的开局已知地点项
 */
export interface WorldStartKnownPlace {
  place_id: string;
  name: string;
  summary: string;
  origin: WorldStartContentOrigin;
  source_refs: WorldStartSourceCitation[];
}

/**
 * 玩家应知的在场或已认识人物摘要
 * 安全红线：仅保留公开身份、当前可见位置与玩家已知关系；
 * 严禁包含后台 NPC 私事（private_motivation / secret_goal / hidden_schedule / private_knowledge）
 */
export interface WorldStartKnownContact {
  npc_id: string;
  name: string;
  role: string;
  location: string;
  public_impression: string;
  known_relation: string;
  contactable: boolean;
  origin: WorldStartContentOrigin;
  source_refs: WorldStartSourceCitation[];
}

/**
 * 玩家在开局时刻有渠道获知的公开消息或传闻
 * 安全红线：严禁包含未获知的后台 major_threads 或隐藏 world_events
 */
export interface WorldStartPlayerKnownFact {
  fact_id: string;
  title: string;
  content: string;
  knowledge_type: 'confirmed' | 'suspected' | 'rumor' | 'mistaken';
  channel: 'observed' | 'notice' | 'conversation' | 'rumor';
  source_label: string;
  origin: WorldStartContentOrigin;
  source_refs: WorldStartSourceCitation[];
}

/**
 * 既有设定冲突提示项（契约要求：已有设定冲突要保留或提示）
 */
export interface WorldStartConflictWarning {
  conflict_id: string;
  summary: string;
  resolution_hint: string;
  source_refs: WorldStartSourceCitation[];
}

/**
 * 待玩家确认的开局局势草案摘要（4. 草案摘要）
 * 契约保证：
 * - 未确认前 committed 恒为 false，不写入正式事件、人物、知识与时间；
 * - 摘要仅包含玩家应知内容，绝不泄露后台 NPC 私事或未获知事件。
 */
export interface WorldStartDraftSummaryDto {
  draft_id: string;
  world_id: string;
  world_name: string;
  mode: 'fake' | 'live';
  run_kind: WorldStartRunKind;
  attempt_number: number;
  selected_source_ids: string[];
  start_point: WorldStartPointInput;
  opening_situation_summary: string;
  known_places: WorldStartKnownPlace[];
  known_contacts: WorldStartKnownContact[];
  player_known_facts: WorldStartPlayerKnownFact[];
  inferred_notes: string[];
  conflict_warnings: WorldStartConflictWarning[];
  suggested_first_actions: string[];
  batch_info: {
    total_batches: number;
    merged_chunks: number;
  };
  committed: false;
}

/**
 * 确认进入旅程后的原子结果（5. 确认结果）
 */
export interface WorldStartCommitResultDto {
  adventure_id: string;
  world_id: string;
  world_instance_id: string;
  draft_id: string | null;
  adventure_name: string;
  mode: 'fake' | 'live';
  run_kind: WorldStartRunKind;
  quick_start: boolean;
  committed: true;
  starts_at: string;
  initial_world_minute: number;
  world_revision: number;
  bound_source_ids: string[];
  player_character: {
    name: string;
    role?: string;
    location: string;
    coins: number;
    stamina: number;
    spirit: number;
    elapsed_minutes: number;
  };
  opening_narrative: string;
  initial_player_news: EncounterNewsItem[];
  suggested_actions: string[];
}

/**
 * 开局组件初始加载数据包
 */
export interface WorldStartBootstrapDto {
  world_id: string;
  world_name: string;
  world_description: string;
  phase: WorldStartPhase;
  materials: WorldStartMaterialItem[];
  start_points: WorldStartPointOption[];
  latest_draft: WorldStartDraftSummaryDto | null;
  applied_result?: WorldStartCommitResultDto | null;
}

/**
 * 玩家主动发起 AI 分析的请求载荷
 */
export interface AnalyzeWorldStartRequest {
  selected_source_ids: string[];
  start_point: WorldStartPointInput;
  mode: 'fake' | 'live';
  session_id?: string | null;
  run_kind?: WorldStartRunKind;
  retry_of_draft_id?: string | null;
  parent_adventure_id?: string | null;
}

/**
 * 确认采用开局草案的请求载荷
 */
export interface ConfirmWorldStartRequest {
  adventure_name?: string;
  run_kind?: WorldStartRunKind;
}

/**
 * 普通冒险先行（跳过 AI 局势分析，减少设置步骤）请求载荷
 */
export interface QuickStartAdventureRequest {
  adventure_name: string;
  mode: 'fake' | 'live';
  session_id?: string | null;
  location?: string;
  starts_at?: string;
  character_name?: string;
  selected_source_ids?: string[];
}

export interface WorldStartRequestOptions {
  signal?: AbortSignal;
}

/**
 * 可注入的手机端开局与初始化传输层契约（方法名由本包定义并记录）
 */
export interface WorldStartTransport {
  /**
   * 1. 读取当前世界的已导入资料、候选起点、当前分析状态与未确认草案
   */
  getBootstrap(
    worldId: string,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartBootstrapDto>;

  /**
   * 2. 玩家在界面主动发起 AI 开局分析，生成仅含玩家应知摘要的待确认草案（不直接写入正式账本）
   */
  analyzeStart(
    worldId: string,
    payload: AnalyzeWorldStartRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartDraftSummaryDto>;

  /**
   * 3. 确认开局草案，原子建立旅程与世界实例状态并绑定来源
   */
  confirmStart(
    worldId: string,
    draftId: string,
    payload?: ConfirmWorldStartRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartCommitResultDto>;

  /**
   * 4. 放弃未确认草案，返回起点与资料选择态（未确认草案不留存为正式事件/人物/知识）
   */
  discardDraft(
    worldId: string,
    draftId: string,
    options?: WorldStartRequestOptions,
  ): Promise<void>;

  /**
   * 5. 普通冒险先行：无需等待 AI 局势分析，直接以最小步骤开启旅程
   */
  startQuickAdventure(
    worldId: string,
    payload: QuickStartAdventureRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartCommitResultDto>;
}

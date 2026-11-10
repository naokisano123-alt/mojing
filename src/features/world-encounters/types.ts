/**
 * M3.3 / M3.6 玩家主动查证、交谈与世界消息行动入口前端类型契约（M3 Wave 3）
 * 严格对应 docs/collaboration/M3_WAVE3_GEMINI_MA.md 与 docs/collaboration/M3_WAVE3.md
 */

export type KnowledgeType = 'confirmed' | 'suspected' | 'rumor' | 'mistaken';

export type KnowledgeChannel = 'observed' | 'notice' | 'conversation' | 'rumor';

export type EncounterOptionKind = 'ask_npc' | 'check_notice' | 'free_edit';

export type EncounterResponseOutcome =
  | 'clue'
  | 'refused'
  | 'vague'
  | 'conflicting'
  | 'unknown'
  | 'npc_departed';

export type NpcResponsePolicy = 'cooperative' | 'reluctant' | 'evasive';

/**
 * 玩家已获知的消息卡片（与 world-knowledge 保持统一公开投影，不含隐藏 event_id）
 */
export interface EncounterNewsItem {
  fact_id: string;
  holder_id: 'player';
  knowledge_type: KnowledgeType;
  title: string;
  content: string;
  channel: KnowledgeChannel;
  source_person_id?: string | null;
  source_label: string;
  learned_minute: number;
  confidence: number;
  status?: 'active' | 'superseded' | 'retracted';
  revision: number;
}

/**
 * 可注入的 NPC 可接触性状态项
 */
export interface EncounterNpcContactability {
  npc_id: string;
  name: string;
  role: string;
  location: string;
  current_activity: string;
  current_action_kind?: string;
  schedule_slot?: string;
  contactable: boolean;
  reason_code: string;
  reason: string;
}

/**
 * 从一则已获知的传闻出发生成的行动建议项
 * 契约要求：选项本身只写入现有输入区，绝不推进时间（advances_clock: false）或自动确认回合（auto_commits_turn: false）
 */
export interface EncounterActionOption {
  option_id: string;
  fact_id: string;
  kind: EncounterOptionKind;
  label: string;
  description: string;
  suggested_action_text: string;
  target_location: string;
  target_npc_id?: string | null;
  target_npc_name?: string | null;
  target_npc_role?: string | null;
  contactable: boolean;
  reason_code: string;
  disabled_reason?: string | null;
  advances_clock: false;
  auto_commits_turn: false;
}

/**
 * 查证/交谈草案中待确认的传播提议（严禁直接改写玩家金钱或背包账本）
 */
export interface EncounterTransmissionPreview {
  op: 'record';
  holder_id: 'player';
  knowledge_type: KnowledgeType;
  channel: KnowledgeChannel;
  title: string;
  content: string;
  source_person_id?: string | null;
  source_fact_id?: string | null;
  learned_minute: number;
  confidence: number;
  can_settle_ledger: false;
}

/**
 * 待确认的查证/交谈草案（尚未写入回合、未推进时钟）
 */
export interface EncounterDraftDto {
  draft_id: string;
  adventure_id: string;
  fact_id: string;
  option_kind: EncounterOptionKind;
  target_npc_id?: string | null;
  player_location: string;
  action_text: string;
  outcome: EncounterResponseOutcome;
  outcome_label: string;
  reason_code: string;
  narrative_reply: string;
  conflict_note?: string | null;
  conflicting_fact_ids: string[];
  proposed_transmissions: EncounterTransmissionPreview[];
  world_minute: number;
  base_world_revision: number;
  clock_advanced: false;
  committed: boolean;
  can_settle_ledger: false;
}

/**
 * 确认回合后提交查证草案的响应
 */
export interface EncounterCommitResultDto {
  adventure_id: string;
  draft_id: string;
  committed: true;
  outcome: EncounterResponseOutcome;
  world_minute: number;
  world_revision: number;
  new_messages: EncounterNewsItem[];
  all_player_news: EncounterNewsItem[];
  ledger_modified: false;
}

/**
 * 「世界消息行动入口」完整数据包
 */
export interface WorldEncounterEntrypointBundle {
  adventure_id: string;
  player_location: string;
  world_minute: number;
  world_revision: number;
  advances_clock: false;
  auto_commits_turn: false;
  news_items: EncounterNewsItem[];
  contactable_npcs: EncounterNpcContactability[];
  options_by_fact_id: Record<string, EncounterActionOption[]>;
}

export interface PrepareEncounterDraftRequest {
  fact_id: string;
  option_kind: EncounterOptionKind;
  target_npc_id?: string | null;
  action_text: string;
  player_location?: string;
}

export interface WorldEncountersRequestOptions {
  signal?: AbortSignal;
}

/**
 * 可注入的「世界消息行动入口与查证交谈」传输层契约
 */
export interface WorldEncountersTransport {
  getEntrypointBundle(
    adventureId: string,
    options?: WorldEncountersRequestOptions,
  ): Promise<WorldEncounterEntrypointBundle>;

  prepareDraft(
    adventureId: string,
    payload: PrepareEncounterDraftRequest,
    options?: WorldEncountersRequestOptions,
  ): Promise<EncounterDraftDto>;

  commitDraft(
    adventureId: string,
    draftId: string,
    options?: WorldEncountersRequestOptions & { commit_world_minute?: number },
  ): Promise<EncounterCommitResultDto>;
}

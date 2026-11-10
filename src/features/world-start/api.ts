/**
 * M3 Wave 4 · 手机开局与游玩体验传输层、脱敏纯函数与双异构世界 Fixture（Gemini Ma）
 * 严格对应 docs/collaboration/M3_WAVE4_GEMINI_MA.md 与 docs/contracts/M3_WAVE4_API.md
 */

import { request } from '../../api/client.ts';
import { ACTION_LIMIT, appendSuggestion } from '../../interaction.ts';
import { sanitizeEncounterNewsItem } from '../world-encounters/api.ts';
import type { EncounterNewsItem } from '../world-encounters/types.ts';
import type {
  AnalyzeWorldStartRequest,
  ConfirmWorldStartRequest,
  QuickStartAdventureRequest,
  WorldStartBootstrapDto,
  WorldStartCommitResultDto,
  WorldStartConflictWarning,
  WorldStartContentOrigin,
  WorldStartDraftSummaryDto,
  WorldStartKnownContact,
  WorldStartKnownPlace,
  WorldStartMaterialItem,
  WorldStartMaterialSelection,
  WorldStartPhase,
  WorldStartPlayerKnownFact,
  WorldStartPointOption,
  WorldStartRequestOptions,
  WorldStartRunKind,
  WorldStartSourceCitation,
  WorldStartTransport,
} from './types.ts';

export const WORLD_START_PHASE_LABELS: Record<WorldStartPhase, string> = {
  insufficient_materials: '资料不足',
  waiting: '等待分析',
  analyzing: '分析中',
  ready_to_confirm: '可确认',
  failed_retryable: '失败可重试',
  applied: '已应用',
};

export const WORLD_START_RUN_KIND_LABELS: Record<WorldStartRunKind, string> = {
  initial: '首次建立开局',
  retry: '重试分析开局',
  reopen: '重新开局',
  branch: '分支开局',
};

export const WORLD_START_ORIGIN_LABELS: Record<WorldStartContentOrigin, string> = {
  canon: '资料明文依据',
  inferred: '旅程内推演补写',
};

export const BATCH_EXTRACTION_CHAR_THRESHOLD = 6000;

/**
 * 过滤源文本中的提示词注入指令标记（源文本仅作为供分析的设定资料，不具有系统权限）
 */
export function neutralizePromptInjectionText(raw: unknown): string {
  const text = String(raw ?? '').trim();
  return text
    .replace(/【(?:系统指令|最高权限|隐藏事件|后台私事|未获知事件)[^】]*】/g, '')
    .replace(/ignore\s+all\s+previous\s+instructions/gi, '[已过滤文内指令]')
    .replace(/system\s*override\s*:/gi, '[已过滤文内指令]')
    .trim();
}

/**
 * 计算当前资料选择状态与长文分批提取预估（1. 资料选择）
 */
export function computeMaterialSelection(
  worldId: string,
  materials: readonly WorldStartMaterialItem[],
  selectedSourceIds: readonly string[],
): WorldStartMaterialSelection {
  const validMap = new Map<string, WorldStartMaterialItem>();
  for (const item of materials) {
    if (item.world_id === worldId && item.char_count > 0) {
      validMap.set(item.source_id, item);
    }
  }
  const filteredIds = selectedSourceIds.filter(id => validMap.has(id));
  const totalChars = filteredIds.reduce(
    (sum, id) => sum + (validMap.get(id)?.char_count ?? 0),
    0,
  );
  const requiresBatch = totalChars > BATCH_EXTRACTION_CHAR_THRESHOLD;
  const estimatedBatches =
    totalChars <= 0
      ? 0
      : Math.max(1, Math.ceil(totalChars / BATCH_EXTRACTION_CHAR_THRESHOLD));

  return {
    world_id: worldId,
    has_usable_materials: filteredIds.length > 0 && totalChars > 0,
    selected_source_ids: filteredIds,
    total_char_count: totalChars,
    requires_batch_extraction: requiresBatch,
    estimated_batches: estimatedBatches,
  };
}

/**
 * 校验并清洗来源引用：仅保留属于当前世界合法资料集合的引用，拒绝越界引用
 */
export function sanitizeSourceRefs(
  rawRefs: unknown,
  allowedSourceIds?: ReadonlySet<string>,
): WorldStartSourceCitation[] {
  if (!Array.isArray(rawRefs)) return [];
  const clean: WorldStartSourceCitation[] = [];
  const seen = new Set<string>();

  for (const item of rawRefs) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const sourceId = String(rec.source_id ?? '').trim();
    if (!sourceId || seen.has(sourceId)) continue;
    if (allowedSourceIds && !allowedSourceIds.has(sourceId)) {
      continue;
    }
    seen.add(sourceId);
    clean.push({
      source_id: sourceId,
      title: neutralizePromptInjectionText(rec.title) || sourceId,
      kind: rec.kind === 'lore_entry' ? 'lore_entry' : 'document',
      ...(rec.snippet ? { snippet: neutralizePromptInjectionText(rec.snippet) } : {}),
    });
  }
  return clean;
}

/**
 * 清洗玩家应知的在场或已知人物摘要
 * 严格剥离：private_motivation, secret_goal, hidden_schedule, private_knowledge, secret_agenda 等后台 NPC 私事
 */
export function sanitizeWorldStartKnownContact(
  raw: unknown,
  allowedSourceIds?: ReadonlySet<string>,
): WorldStartKnownContact | null {
  if (!raw || typeof raw !== 'object') return null;
  const rec = raw as Record<string, unknown>;
  if (rec.visibility === 'hidden' || rec.is_secret_only === true) {
    return null;
  }
  const npcId = String(rec.npc_id ?? '').trim();
  const name = neutralizePromptInjectionText(rec.name);
  if (!npcId || !name) return null;

  const sourceRefs = sanitizeSourceRefs(rec.source_refs, allowedSourceIds);
  const origin: WorldStartContentOrigin =
    rec.origin === 'inferred' || sourceRefs.length === 0 ? 'inferred' : 'canon';

  return {
    npc_id: npcId,
    name,
    role: neutralizePromptInjectionText(rec.role) || '当地人物',
    location: neutralizePromptInjectionText(rec.location),
    public_impression: neutralizePromptInjectionText(rec.public_impression),
    known_relation: neutralizePromptInjectionText(rec.known_relation) || '初识',
    contactable: rec.contactable !== false,
    origin,
    source_refs: sourceRefs,
  };
}

/**
 * 清洗玩家开局应知的消息/传闻项
 * 严格过滤未获知事件（unlearned / hidden / holder_id !== 'player'）与后台主线事件 ID
 */
export function sanitizeWorldStartPlayerKnownFact(
  raw: unknown,
  allowedSourceIds?: ReadonlySet<string>,
): WorldStartPlayerKnownFact | null {
  if (!raw || typeof raw !== 'object') return null;
  const rec = raw as Record<string, unknown>;
  if (
    rec.player_known === false ||
    rec.visibility === 'hidden' ||
    (rec.holder_id !== undefined && rec.holder_id !== 'player')
  ) {
    return null;
  }
  const factId = String(rec.fact_id ?? '').trim();
  const title = neutralizePromptInjectionText(rec.title);
  const content = neutralizePromptInjectionText(rec.content);
  if (!factId || !title || !content) return null;

  const sourceRefs = sanitizeSourceRefs(rec.source_refs, allowedSourceIds);
  const origin: WorldStartContentOrigin =
    rec.origin === 'inferred' || sourceRefs.length === 0 ? 'inferred' : 'canon';

  const kType = rec.knowledge_type;
  const validType: WorldStartPlayerKnownFact['knowledge_type'] =
    kType === 'confirmed' || kType === 'suspected' || kType === 'rumor' || kType === 'mistaken'
      ? kType
      : 'observed' === rec.channel
        ? 'confirmed'
        : 'rumor';

  const ch = rec.channel;
  const validChannel: WorldStartPlayerKnownFact['channel'] =
    ch === 'observed' || ch === 'notice' || ch === 'conversation' || ch === 'rumor'
      ? ch
      : 'observed';

  return {
    fact_id: factId,
    title,
    content,
    knowledge_type: validType,
    channel: validChannel,
    source_label: neutralizePromptInjectionText(rec.source_label) || '开局见闻',
    origin,
    source_refs: sourceRefs,
  };
}

/**
 * 清洗开局分析草案摘要（4. 草案摘要）
 * - 强制 committed: false（未确认草案不写入正式账本）
 * - 剥离任何后台 NPC 私事、未获知事件、越界来源引用与模型密钥字段
 */
export function sanitizeWorldStartDraftSummary(
  raw: WorldStartDraftSummaryDto,
  allowedSourceIds?: ReadonlySet<string>,
): WorldStartDraftSummaryDto {
  const effectiveAllowed =
    allowedSourceIds ??
    (Array.isArray(raw?.selected_source_ids) ? new Set(raw.selected_source_ids) : undefined);

  const knownPlaces: WorldStartKnownPlace[] = Array.isArray(raw?.known_places)
    ? raw.known_places
        .map(p => {
          if (!p || typeof p !== 'object') return null;
          const rec = p as unknown as Record<string, unknown>;
          if (rec.visibility === 'hidden') return null;
          const refs = sanitizeSourceRefs(rec.source_refs, effectiveAllowed);
          const origin: WorldStartContentOrigin =
            rec.origin === 'inferred' || refs.length === 0 ? 'inferred' : 'canon';
          return {
            place_id: String(rec.place_id ?? ''),
            name: neutralizePromptInjectionText(rec.name),
            summary: neutralizePromptInjectionText(rec.summary),
            origin,
            source_refs: refs,
          };
        })
        .filter((x): x is WorldStartKnownPlace => Boolean(x && x.place_id && x.name))
    : [];

  const knownContacts: WorldStartKnownContact[] = Array.isArray(raw?.known_contacts)
    ? raw.known_contacts
        .map(c => sanitizeWorldStartKnownContact(c, effectiveAllowed))
        .filter((x): x is WorldStartKnownContact => x !== null)
    : [];

  const playerKnownFacts: WorldStartPlayerKnownFact[] = Array.isArray(raw?.player_known_facts)
    ? raw.player_known_facts
        .map(f => sanitizeWorldStartPlayerKnownFact(f, effectiveAllowed))
        .filter((x): x is WorldStartPlayerKnownFact => x !== null)
    : [];

  const conflictWarnings: WorldStartConflictWarning[] = Array.isArray(raw?.conflict_warnings)
    ? raw.conflict_warnings
        .map(w => {
          if (!w || typeof w !== 'object') return null;
          const rec = w as unknown as Record<string, unknown>;
          const summary = neutralizePromptInjectionText(rec.summary);
          if (!summary) return null;
          return {
            conflict_id: String(rec.conflict_id ?? 'conflict_1'),
            summary,
            resolution_hint:
              neutralizePromptInjectionText(rec.resolution_hint) ||
              '已保留两处设定记载，可在旅程中自由查证核实。',
            source_refs: sanitizeSourceRefs(rec.source_refs, effectiveAllowed),
          };
        })
        .filter((x): x is WorldStartConflictWarning => x !== null)
    : [];

  const inferredNotes = Array.isArray(raw?.inferred_notes)
    ? raw.inferred_notes
        .map(n => neutralizePromptInjectionText(n))
        .filter(Boolean)
    : [];

  const suggestedFirstActions = Array.isArray(raw?.suggested_first_actions)
    ? raw.suggested_first_actions
        .map(a => neutralizePromptInjectionText(a))
        .filter(Boolean)
    : [];

  return {
    draft_id: String(raw?.draft_id ?? ''),
    world_id: String(raw?.world_id ?? ''),
    world_name: neutralizePromptInjectionText(raw?.world_name),
    mode: raw?.mode === 'live' ? 'live' : 'fake',
    run_kind:
      raw?.run_kind === 'retry' || raw?.run_kind === 'reopen' || raw?.run_kind === 'branch'
        ? raw.run_kind
        : 'initial',
    attempt_number: Math.max(1, Math.floor(Number(raw?.attempt_number) || 1)),
    selected_source_ids: Array.isArray(raw?.selected_source_ids)
      ? raw.selected_source_ids.map(String)
      : [],
    start_point: {
      start_id: raw?.start_point?.start_id ?? null,
      location: neutralizePromptInjectionText(raw?.start_point?.location),
      starts_at: String(raw?.start_point?.starts_at ?? ''),
      character_name: neutralizePromptInjectionText(raw?.start_point?.character_name),
      character_role: neutralizePromptInjectionText(raw?.start_point?.character_role),
      adventure_name: neutralizePromptInjectionText(raw?.start_point?.adventure_name),
    },
    opening_situation_summary: neutralizePromptInjectionText(raw?.opening_situation_summary),
    known_places: knownPlaces,
    known_contacts: knownContacts,
    player_known_facts: playerKnownFacts,
    inferred_notes: inferredNotes,
    conflict_warnings: conflictWarnings,
    suggested_first_actions: suggestedFirstActions,
    batch_info: {
      total_batches: Math.max(1, Math.floor(Number(raw?.batch_info?.total_batches) || 1)),
      merged_chunks: Math.max(1, Math.floor(Number(raw?.batch_info?.merged_chunks) || 1)),
    },
    committed: false,
  };
}

/**
 * 清洗确认进入旅程的结果（5. 确认结果）
 */
export function sanitizeWorldStartCommitResult(
  raw: WorldStartCommitResultDto,
): WorldStartCommitResultDto {
  const initialNews: EncounterNewsItem[] = Array.isArray(raw?.initial_player_news)
    ? raw.initial_player_news.map(sanitizeEncounterNewsItem)
    : [];

  return {
    adventure_id: String(raw?.adventure_id ?? ''),
    world_id: String(raw?.world_id ?? ''),
    world_instance_id: String(raw?.world_instance_id ?? ''),
    draft_id: raw?.draft_id ? String(raw.draft_id) : null,
    adventure_name: neutralizePromptInjectionText(raw?.adventure_name),
    mode: raw?.mode === 'live' ? 'live' : 'fake',
    run_kind:
      raw?.run_kind === 'retry' || raw?.run_kind === 'reopen' || raw?.run_kind === 'branch'
        ? raw.run_kind
        : 'initial',
    quick_start: Boolean(raw?.quick_start),
    committed: true,
    starts_at: String(raw?.starts_at ?? ''),
    initial_world_minute: Math.max(0, Math.floor(Number(raw?.initial_world_minute) || 0)),
    world_revision: Math.max(1, Math.floor(Number(raw?.world_revision) || 1)),
    bound_source_ids: Array.isArray(raw?.bound_source_ids)
      ? raw.bound_source_ids.map(String)
      : [],
    player_character: {
      name: neutralizePromptInjectionText(raw?.player_character?.name),
      ...(raw?.player_character?.role
        ? { role: neutralizePromptInjectionText(raw.player_character.role) }
        : {}),
      location: neutralizePromptInjectionText(raw?.player_character?.location),
      coins: Math.max(0, Math.floor(Number(raw?.player_character?.coins) || 0)),
      stamina: Math.max(0, Math.min(100, Math.floor(Number(raw?.player_character?.stamina ?? 100)))),
      spirit: Math.max(0, Math.min(100, Math.floor(Number(raw?.player_character?.spirit ?? 100)))),
      elapsed_minutes: Math.max(
        0,
        Math.floor(Number(raw?.player_character?.elapsed_minutes) || 0),
      ),
    },
    opening_narrative: neutralizePromptInjectionText(raw?.opening_narrative),
    initial_player_news: initialNews,
    suggested_actions: Array.isArray(raw?.suggested_actions)
      ? raw.suggested_actions.map(a => neutralizePromptInjectionText(a)).filter(Boolean)
      : [],
  };
}

/**
 * 清洗初始加载数据包
 */
export function sanitizeWorldStartBootstrap(raw: WorldStartBootstrapDto): WorldStartBootstrapDto {
  const worldId = String(raw?.world_id ?? '');
  const materials: WorldStartMaterialItem[] = Array.isArray(raw?.materials)
    ? raw.materials
        .filter(m => m && String(m.world_id ?? worldId) === worldId)
        .map(m => ({
          source_id: String(m.source_id),
          world_id: worldId,
          kind: m.kind === 'lore_entry' ? 'lore_entry' : 'document',
          title: neutralizePromptInjectionText(m.title),
          char_count: Math.max(0, Math.floor(Number(m.char_count) || 0)),
          chunk_count: Math.max(1, Math.floor(Number(m.chunk_count) || 1)),
          selected_by_default: m.selected_by_default !== false,
          ...(m.excerpt_preview
            ? { excerpt_preview: neutralizePromptInjectionText(m.excerpt_preview) }
            : {}),
        }))
    : [];

  const allowedIds = new Set(materials.map(m => m.source_id));

  const startPoints: WorldStartPointOption[] = Array.isArray(raw?.start_points)
    ? raw.start_points.map(sp => {
        const refs = sanitizeSourceRefs(sp.source_refs, allowedIds);
        return {
          start_id: String(sp.start_id),
          label: neutralizePromptInjectionText(sp.label),
          location: neutralizePromptInjectionText(sp.location),
          starts_at: String(sp.starts_at ?? ''),
          character_name: neutralizePromptInjectionText(sp.character_name),
          character_role: neutralizePromptInjectionText(sp.character_role),
          summary: neutralizePromptInjectionText(sp.summary),
          origin: sp.origin === 'inferred' || refs.length === 0 ? 'inferred' : 'canon',
          source_refs: refs,
        };
      })
    : [];

  const hasUsableMaterials = materials.some(m => m.char_count > 0);
  const latestDraft = raw?.latest_draft
    ? sanitizeWorldStartDraftSummary(raw.latest_draft, allowedIds)
    : null;
  const appliedResult = raw?.applied_result
    ? sanitizeWorldStartCommitResult(raw.applied_result)
    : null;

  let phase: WorldStartPhase = raw?.phase ?? 'waiting';
  if (!hasUsableMaterials && phase !== 'applied') {
    phase = 'insufficient_materials';
  } else if (appliedResult && phase === 'applied') {
    phase = 'applied';
  } else if (latestDraft && phase === 'ready_to_confirm') {
    phase = 'ready_to_confirm';
  }

  return {
    world_id: worldId,
    world_name: neutralizePromptInjectionText(raw?.world_name),
    world_description: neutralizePromptInjectionText(raw?.world_description),
    phase,
    materials,
    start_points: startPoints,
    latest_draft: latestDraft,
    applied_result: appliedResult,
  };
}

/**
 * 将开局后的自由行动建议写入宿主输入区（不推进时钟、不自动提交回合）
 */
export function applyStartSuggestionToComposerDraft(
  currentDraft: string,
  suggestionText: string,
  limit = ACTION_LIMIT,
): { nextDraft: string | null; error: string | null } {
  const next = appendSuggestion(currentDraft, suggestionText, limit);
  if (next === null) {
    return {
      nextDraft: null,
      error: '草稿空间不足，请先精简文字。已有内容未改变。',
    };
  }
  return {
    nextDraft: next,
    error: null,
  };
}

/**
 * 默认同源 HTTP 传输实现（供 DeepSeekHarness 挂载至 /api/v1/worlds/{world_id}/world-start）
 */
export const worldStartApi: WorldStartTransport = {
  async getBootstrap(
    worldId: string,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartBootstrapDto> {
    const raw = await request<WorldStartBootstrapDto>(
      'GET',
      `/worlds/${encodeURIComponent(worldId)}/world-start`,
      { signal: options?.signal },
    );
    return sanitizeWorldStartBootstrap(raw);
  },

  async analyzeStart(
    worldId: string,
    payload: AnalyzeWorldStartRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartDraftSummaryDto> {
    const raw = await request<WorldStartDraftSummaryDto>(
      'POST',
      `/worlds/${encodeURIComponent(worldId)}/world-start/analyze`,
      { body: payload, signal: options?.signal, timeoutMs: 75000 },
    );
    return sanitizeWorldStartDraftSummary(raw);
  },

  async confirmStart(
    worldId: string,
    draftId: string,
    payload?: ConfirmWorldStartRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartCommitResultDto> {
    const raw = await request<WorldStartCommitResultDto>(
      'POST',
      `/worlds/${encodeURIComponent(worldId)}/world-start/drafts/${encodeURIComponent(draftId)}/confirm`,
      { body: payload ?? {}, signal: options?.signal },
    );
    return sanitizeWorldStartCommitResult(raw);
  },

  async discardDraft(
    worldId: string,
    draftId: string,
    options?: WorldStartRequestOptions,
  ): Promise<void> {
    await request<void>(
      'DELETE',
      `/worlds/${encodeURIComponent(worldId)}/world-start/drafts/${encodeURIComponent(draftId)}`,
      { signal: options?.signal },
    );
  },

  async startQuickAdventure(
    worldId: string,
    payload: QuickStartAdventureRequest,
    options?: WorldStartRequestOptions,
  ): Promise<WorldStartCommitResultDto> {
    const raw = await request<WorldStartCommitResultDto>(
      'POST',
      `/worlds/${encodeURIComponent(worldId)}/world-start/quick-start`,
      { body: payload, signal: options?.signal },
    );
    return sanitizeWorldStartCommitResult(raw);
  },
};

/**
 * 内部种子结构：包含模拟服务端完整世界数据（含会被脱敏拦截的 NPC 私事与未获知后台事件）
 */
export interface FakeWorldStartSeed {
  world_id: string;
  world_name: string;
  world_description: string;
  materials: WorldStartMaterialItem[];
  start_points: WorldStartPointOption[];
  known_places_by_start: Record<string, WorldStartKnownPlace[]>;
  raw_npcs_by_start: Record<
    string,
    Array<
      WorldStartKnownContact & {
        private_motivation?: string;
        secret_schedule?: string;
        private_knowledge?: string[];
      }
    >
  >;
  raw_facts_by_start: Record<
    string,
    Array<
      WorldStartPlayerKnownFact & {
        player_known?: boolean;
        holder_id?: string;
        hidden_event_id?: string;
      }
    >
  >;
  inferred_notes_by_start: Record<string, string[]>;
  conflict_warnings: WorldStartConflictWarning[];
  suggested_actions_by_start: Record<string, string[]>;
}

/**
 * 构造两套人物、地理、年代与矛盾完全不同的测试世界资料 Fixture（另附一套空资料世界用于验证资料不足）
 */
export function createTwoDistinctWorldStartSeeds(): Record<string, FakeWorldStartSeed> {
  const worldAId = 'world_celain_harbor';
  const worldBId = 'world_sand_clockwork';
  const worldEmptyId = 'world_empty_materials';

  const seedA: FakeWorldStartSeed = {
    world_id: worldAId,
    world_name: '青瓷海岬志',
    world_description: '潮汐礁屿间的青瓷窑口与海盐栈桥，各方围绕盐引核验与灯塔航道角力。',
    materials: [
      {
        source_id: 'doc:celain_chronicle',
        world_id: worldAId,
        kind: 'document',
        title: '青瓷海岬风物志.md',
        char_count: 4200,
        chunk_count: 6,
        selected_by_default: true,
        excerpt_preview: '白鸥灯塔立于海岬北端栈桥，守夜人苏晚照掌管夜航雾钟；南侧为青磁南窑坊。',
      },
      {
        source_id: 'doc:salt_guild_ledger',
        world_id: worldAId,
        kind: 'document',
        title: '海盐公所税则与航道章程.txt',
        char_count: 8600,
        chunk_count: 12,
        selected_by_default: true,
        excerpt_preview: '凡入港盐船须经海盐公所掌案顾承钧复核盐引，灯塔夜税全额解送公所库房。',
      },
      {
        source_id: 'entry:beacon_treaty',
        world_id: worldAId,
        kind: 'lore_entry',
        title: '人工设定：白鸥灯塔守夜公约',
        char_count: 680,
        chunk_count: 1,
        selected_by_default: true,
        excerpt_preview: '雾季期间灯塔每逢寅卯交接鸣钟三次，引外海盐船避开暗礁入栈桥。',
      },
    ],
    start_points: [
      {
        start_id: 'sp_celain_dock',
        label: '白鸥灯塔下栈桥 · 晨雾验盐',
        location: '白鸥灯塔下栈桥',
        starts_at: '1642-05-12T06:30',
        character_name: '林砚',
        character_role: '青年验盐学徒',
        summary: '清晨海雾未散，你带着验盐木牌站在白鸥灯塔下的栈桥边，等候早班盐船靠岸。',
        origin: 'canon',
        source_refs: [
          {
            source_id: 'doc:celain_chronicle',
            title: '青瓷海岬风物志.md',
            kind: 'document',
          },
        ],
      },
      {
        start_id: 'sp_celain_kiln',
        label: '青磁南窑坊 · 开窑清点',
        location: '青磁南窑坊',
        starts_at: '1642-05-12T08:00',
        character_name: '林砚',
        character_role: '窑坊记账学徒',
        summary: '南窑坊刚启封一窑青釉盐罐，你负责核对今日待运往栈桥的瓷器数目。',
        origin: 'canon',
        source_refs: [
          {
            source_id: 'doc:celain_chronicle',
            title: '青瓷海岬风物志.md',
            kind: 'document',
          },
        ],
      },
    ],
    known_places_by_start: {
      sp_celain_dock: [
        {
          place_id: 'place_celain_dock',
          name: '白鸥灯塔下栈桥',
          summary: '青瓷海岬北端的主要停泊栈桥，设有验盐棚与雾钟石阶。',
          origin: 'canon',
          source_refs: [
            { source_id: 'doc:celain_chronicle', title: '青瓷海岬风物志.md', kind: 'document' },
          ],
        },
        {
          place_id: 'place_salt_office',
          name: '海盐公所前厅',
          summary: '栈桥东侧的值房，张贴着每日盐引核验次序告示。',
          origin: 'canon',
          source_refs: [
            {
              source_id: 'doc:salt_guild_ledger',
              title: '海盐公所税则与航道章程.txt',
              kind: 'document',
            },
          ],
        },
      ],
      sp_celain_kiln: [
        {
          place_id: 'place_celain_kiln',
          name: '青磁南窑坊',
          summary: '海岬南坡烧制储盐青瓷罐的窑场，清晨烟火正旺。',
          origin: 'canon',
          source_refs: [
            { source_id: 'doc:celain_chronicle', title: '青瓷海岬风物志.md', kind: 'document' },
          ],
        },
      ],
    },
    raw_npcs_by_start: {
      sp_celain_dock: [
        {
          npc_id: 'npc_su_lighthouse',
          name: '苏晚照',
          role: '白鸥灯塔守夜人',
          location: '白鸥灯塔下栈桥',
          public_impression: '刚走下灯塔石阶的守夜人，手里提着尚有余温的铜油灯。',
          known_relation: '常在早班验盐时照面的守塔前辈',
          contactable: true,
          origin: 'canon',
          source_refs: [
            { source_id: 'doc:celain_chronicle', title: '青瓷海岬风物志.md', kind: 'document' },
          ],
          private_motivation: '【后台NPC私事】苏晚照暗中替外海私盐船留了三短一长的暗灯信号',
          secret_schedule: '【后台私密日程】子夜独自前往断礁洞穴交接密信',
          private_knowledge: ['外海私盐船将于明日丑时偷渡北湾'],
        },
        {
          npc_id: 'npc_gu_salt',
          name: '顾承钧',
          role: '海盐公所掌案',
          location: '白鸥灯塔下栈桥',
          public_impression: '站在验盐棚下翻看木牌账册，神色严谨。',
          known_relation: '负责督查你今日验盐记录的公所掌案',
          contactable: true,
          origin: 'canon',
          source_refs: [
            {
              source_id: 'doc:salt_guild_ledger',
              title: '海盐公所税则与航道章程.txt',
              kind: 'document',
            },
          ],
          private_motivation: '【后台NPC私事】顾承钧正私下调查三号盐仓亏空案',
        },
      ],
      sp_celain_kiln: [
        {
          npc_id: 'npc_master_tao',
          name: '陶老匠',
          role: '南窑坊督陶师',
          location: '青磁南窑坊',
          public_impression: '正在窑口敲击刚出窑的青釉盐罐听声辨质。',
          known_relation: '指点你辨认瓷罐裂纹的老匠人',
          contactable: true,
          origin: 'inferred',
          source_refs: [],
          private_motivation: '【后台NPC私事】陶老匠偷偷扣下了两只贡品青瓷瓶',
        },
      ],
    },
    raw_facts_by_start: {
      sp_celain_dock: [
        {
          fact_id: 'fact_celain_notice_01',
          title: '海盐公所晨间告示：今日优先核验外海大引盐船',
          content: '公所木牌告示写明：因晨雾较浓，早班先核验持甲字盐引的船只，散户盐舟顺延半个时辰。',
          knowledge_type: 'confirmed',
          channel: 'notice',
          source_label: '公开告示 · 海盐公所木牌',
          origin: 'canon',
          player_known: true,
          holder_id: 'player',
          source_refs: [
            {
              source_id: 'doc:salt_guild_ledger',
              title: '海盐公所税则与航道章程.txt',
              kind: 'document',
            },
          ],
        },
        {
          fact_id: 'fact_celain_rumor_01',
          title: '栈桥水手耳语：昨夜外海雾钟似乎比平日晚响了一刻',
          content: '几名早起候船的水手议论，昨夜寅时的灯塔雾钟比往常迟了一刻才敲响。',
          knowledge_type: 'rumor',
          channel: 'rumor',
          source_label: '道听途说 · 栈桥水手',
          origin: 'inferred',
          player_known: true,
          holder_id: 'player',
          source_refs: [],
        },
        {
          fact_id: 'fact_celain_hidden_secret_99',
          title: '【未获知事件】总督府密使昨夜已潜入海盐公所后堂封存旧账',
          content: '总督府密使深夜密会顾承钧，下令秘密彻查三号盐仓。',
          knowledge_type: 'confirmed',
          channel: 'conversation',
          source_label: '后台隐藏事件',
          origin: 'inferred',
          player_known: false,
          holder_id: 'npc_gu_salt',
          hidden_event_id: 'evt_secret_governor_audit',
          source_refs: [],
        },
      ],
      sp_celain_kiln: [
        {
          fact_id: 'fact_celain_kiln_notice_01',
          title: '南窑坊出窑单：本批青釉盐罐共一百二十只待运栈桥',
          content: '窑坊木牌记录本窑共出青釉盐罐一百二十只，须在午时前送抵白鸥灯塔下栈桥装船。',
          knowledge_type: 'confirmed',
          channel: 'observed',
          source_label: '亲眼目睹 · 南窑坊木牌',
          origin: 'canon',
          player_known: true,
          holder_id: 'player',
          source_refs: [
            { source_id: 'doc:celain_chronicle', title: '青瓷海岬风物志.md', kind: 'document' },
          ],
        },
      ],
    },
    inferred_notes_by_start: {
      sp_celain_dock: [
        '资料未详述今晨早潮的具体退潮时刻，已按海岬雾季常规推演为辰时初退潮（记录为本旅程推演补写）。',
      ],
      sp_celain_kiln: [
        '资料未列明窑坊至栈桥的板车运费，已按海岬日常劳务推演为每车十文铜币（记录为本旅程推演补写）。',
      ],
    },
    conflict_warnings: [
      {
        conflict_id: 'conflict_beacon_tax',
        summary:
          '《青瓷海岬风物志.md》记载灯塔夜航油费由守夜人自收自支，而《海盐公所税则与航道章程.txt》规定灯塔夜税须全额上缴公所库房。',
        resolution_hint: '已同时保留两处原始设定记载，玩家可在旅程中向苏晚照或顾承钧当面核实实际执行方式。',
        source_refs: [
          { source_id: 'doc:celain_chronicle', title: '青瓷海岬风物志.md', kind: 'document' },
          {
            source_id: 'doc:salt_guild_ledger',
            title: '海盐公所税则与航道章程.txt',
            kind: 'document',
          },
        ],
      },
    ],
    suggested_actions_by_start: {
      sp_celain_dock: [
        '向刚走下石阶的苏晚照打招呼，询问今晨海雾与早班盐船靠岸的情况',
        '前往海盐公所木牌前仔细核对今日甲字盐引的核验次序',
      ],
      sp_celain_kiln: [
        '随陶老匠清点刚出窑的青釉盐罐，挑出无裂纹的良器登记入册',
      ],
    },
  };

  const seedB: FakeWorldStartSeed = {
    world_id: worldBId,
    world_name: '赤砂星晷录',
    world_description: '横跨赤砂断谷的发条分水渠与星晷高塔城邦，水源配额由星象历法严格裁定。',
    materials: [
      {
        source_id: 'doc:oasis_water_law',
        world_id: worldBId,
        kind: 'document',
        title: '赤砂绿洲分水律.md',
        char_count: 3500,
        chunk_count: 5,
        selected_by_default: true,
        excerpt_preview: '第三分水闸每逢日落凭黄铜水筹放水，巡检官娜迪娅执掌闸门齿轨。',
      },
      {
        source_id: 'doc:astronomy_tower',
        world_id: worldBId,
        kind: 'document',
        title: '星晷高塔观测手札.txt',
        char_count: 2900,
        chunk_count: 4,
        selected_by_default: true,
        excerpt_preview: '星晷高塔底庭陈列着主发条钟摆，学士卡维负责每日黄昏校准赤砂历刻度。',
      },
    ],
    start_points: [
      {
        start_id: 'sp_oasis_sluice',
        label: '第三分水闸集市 · 黄昏候水',
        location: '第三分水闸集市',
        starts_at: '0389-11-04T17:00',
        character_name: '阿斯兰',
        character_role: '绿洲修表匠',
        summary: '落日余晖映在分水闸巨大的黄铜齿轮上，你背着修表工具箱来到集市水渠旁。',
        origin: 'canon',
        source_refs: [
          { source_id: 'doc:oasis_water_law', title: '赤砂绿洲分水律.md', kind: 'document' },
        ],
      },
      {
        start_id: 'sp_oasis_observatory',
        label: '星晷高塔底庭 · 钟摆校时',
        location: '星晷高塔底庭',
        starts_at: '0389-11-04T19:00',
        character_name: '阿斯兰',
        character_role: '绿洲修表匠',
        summary: '高塔底庭的主发条钟摆发出沉稳的滴答声，你受邀前来协助检查副轮游丝。',
        origin: 'canon',
        source_refs: [
          {
            source_id: 'doc:astronomy_tower',
            title: '星晷高塔观测手札.txt',
            kind: 'document',
          },
        ],
      },
    ],
    known_places_by_start: {
      sp_oasis_sluice: [
        {
          place_id: 'place_oasis_sluice_3',
          name: '第三分水闸集市',
          summary: '绿洲西渠与商队集市交汇处的发条水闸广场。',
          origin: 'canon',
          source_refs: [
            { source_id: 'doc:oasis_water_law', title: '赤砂绿洲分水律.md', kind: 'document' },
          ],
        },
      ],
      sp_oasis_observatory: [
        {
          place_id: 'place_oasis_tower_court',
          name: '星晷高塔底庭',
          summary: '安放城邦主发条与赤砂日晷青铜盘的石砌底庭。',
          origin: 'canon',
          source_refs: [
            {
              source_id: 'doc:astronomy_tower',
              title: '星晷高塔观测手札.txt',
              kind: 'document',
            },
          ],
        },
      ],
    },
    raw_npcs_by_start: {
      sp_oasis_sluice: [
        {
          npc_id: 'npc_nadia_warden',
          name: '娜迪娅',
          role: '分水闸巡检官',
          location: '第三分水闸集市',
          public_impression: '正握着黄铜扳手检查三号闸门的止水棘轮。',
          known_relation: '曾委托你修理过巡检怀表的闸门官员',
          contactable: true,
          origin: 'canon',
          source_refs: [
            { source_id: 'doc:oasis_water_law', title: '赤砂绿洲分水律.md', kind: 'document' },
          ],
          private_motivation: '【后台NPC私事】娜迪娅私下截留了旧水芯齿轮准备送往地下工坊',
        },
        {
          npc_id: 'npc_kaveh_astrologer',
          name: '卡维',
          role: '星晷观测司学士',
          location: '第三分水闸集市',
          public_impression: '手持星盘站在水渠石栏边记录日落折射角。',
          known_relation: '常来找你定制精密铜尺的观测司学者',
          contactable: true,
          origin: 'canon',
          source_refs: [
            {
              source_id: 'doc:astronomy_tower',
              title: '星晷高塔观测手札.txt',
              kind: 'document',
            },
          ],
          private_motivation: '【后台NPC私事】卡维隐瞒了今年赤砂旱季将提前十日到来的星象推算',
        },
      ],
      sp_oasis_observatory: [
        {
          npc_id: 'npc_kaveh_astrologer',
          name: '卡维',
          role: '星晷观测司学士',
          location: '星晷高塔底庭',
          public_impression: '守在主发条钟摆旁核对刻漏沙壶。',
          known_relation: '邀请你前来检修副轮游丝的观测司学者',
          contactable: true,
          origin: 'canon',
          source_refs: [
            {
              source_id: 'doc:astronomy_tower',
              title: '星晷高塔观测手札.txt',
              kind: 'document',
            },
          ],
        },
      ],
    },
    raw_facts_by_start: {
      sp_oasis_sluice: [
        {
          fact_id: 'fact_oasis_notice_01',
          title: '分水闸铜牌通告：西渠今晚戌时起减流检修两个时辰',
          content: '三号水闸铜牌写明：因主齿轨需涂抹防沙脂，今晚戌时至亥时西渠放水量减半。',
          knowledge_type: 'confirmed',
          channel: 'notice',
          source_label: '公开告示 · 分水闸铜牌',
          origin: 'canon',
          player_known: true,
          holder_id: 'player',
          source_refs: [
            { source_id: 'doc:oasis_water_law', title: '赤砂绿洲分水律.md', kind: 'document' },
          ],
        },
        {
          fact_id: 'fact_oasis_hidden_99',
          title: '【未获知事件】赤砂王庭密使今夜潜入星晷高塔顶层销毁旧历',
          content: '王庭密使已密令高塔封锁顶层星盘。',
          knowledge_type: 'confirmed',
          channel: 'conversation',
          source_label: '后台隐藏事件',
          origin: 'inferred',
          player_known: false,
          holder_id: 'npc_kaveh_astrologer',
          hidden_event_id: 'evt_secret_royal_envoy',
          source_refs: [],
        },
      ],
      sp_oasis_observatory: [
        {
          fact_id: 'fact_oasis_tower_01',
          title: '底庭检修记录：副轮游丝每昼夜偏慢三息',
          content: '高塔底庭的值班木牍记着副轮游丝近期受风沙磨损，每昼夜偏慢三息。',
          knowledge_type: 'confirmed',
          channel: 'observed',
          source_label: '亲眼目睹 · 高塔值班木牍',
          origin: 'canon',
          player_known: true,
          holder_id: 'player',
          source_refs: [
            {
              source_id: 'doc:astronomy_tower',
              title: '星晷高塔观测手札.txt',
              kind: 'document',
            },
          ],
        },
      ],
    },
    inferred_notes_by_start: {
      sp_oasis_sluice: [
        '资料未指明第三分水闸集市晚间点灯规制，已按赤砂城邦习俗推演为日落鸣锣后点亮铜油风灯（记录为本旅程推演补写）。',
      ],
      sp_oasis_observatory: [
        '资料未详述高塔底庭夜间出入凭证，已推演为你持有卡维签发的临时检修木牌（记录为本旅程推演补写）。',
      ],
    },
    conflict_warnings: [],
    suggested_actions_by_start: {
      sp_oasis_sluice: [
        '向分水闸巡检官娜迪娅询问今晚西渠减流检修是否需要修表匠协助校准棘轮',
        '走近星晷观测司学士卡维，打听他正在测量的日落折射角',
      ],
      sp_oasis_observatory: [
        '打开工具箱与卡维一起检查主发条钟摆的副轮游丝磨损情况',
      ],
    },
  };

  const seedEmpty: FakeWorldStartSeed = {
    world_id: worldEmptyId,
    world_name: '空白星图世界',
    world_description: '刚创建但尚未导入任何文档或人工设定条目的世界资料库。',
    materials: [],
    start_points: [],
    known_places_by_start: {},
    raw_npcs_by_start: {},
    raw_facts_by_start: {},
    inferred_notes_by_start: {},
    conflict_warnings: [],
    suggested_actions_by_start: {},
  };

  return {
    [worldAId]: seedA,
    [worldBId]: seedB,
    [worldEmptyId]: seedEmpty,
  };
}

export function createFakeWorldStartTransport(
  initialSeeds: Record<string, FakeWorldStartSeed> = createTwoDistinctWorldStartSeeds(),
) {
  const seeds = new Map<string, FakeWorldStartSeed>();
  const unconfirmedDrafts = new Map<string, WorldStartDraftSummaryDto>();
  const latestDraftByWorld = new Map<string, string>();
  const committedAdventuresByWorld = new Map<string, WorldStartCommitResultDto[]>();
  const attemptCountByWorld = new Map<string, number>();
  const simulatedFailures = new Map<string, { fail: boolean; message: string }>();
  const responseDelays = new Map<string, number>();
  let seq = 1;

  for (const [wid, seed] of Object.entries(initialSeeds)) {
    seeds.set(wid, structuredClone(seed));
    committedAdventuresByWorld.set(wid, []);
    attemptCountByWorld.set(wid, 0);
  }

  function requireSeed(worldId: string): FakeWorldStartSeed {
    const s = seeds.get(worldId);
    if (!s) {
      throw new Error(`未找到世界资料库 ${worldId}。`);
    }
    return s;
  }

  async function maybeDelay(worldId: string, signal?: AbortSignal) {
    if (signal?.aborted) {
      const err = new Error('请求已取消');
      err.name = 'AbortError';
      throw err;
    }
    const ms = responseDelays.get(worldId) ?? 0;
    if (ms > 0) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, ms);
        signal?.addEventListener(
          'abort',
          () => {
            clearTimeout(timer);
            const abortErr = new Error('请求已取消');
            abortErr.name = 'AbortError';
            reject(abortErr);
          },
          { once: true },
        );
      });
    }
    if (signal?.aborted) {
      const err = new Error('请求已取消');
      err.name = 'AbortError';
      throw err;
    }
  }

  const transport: WorldStartTransport & {
    setSimulatedFailure(worldId: string, shouldFail: boolean, message?: string): void;
    setResponseDelay(worldId: string, ms: number): void;
    addWorldMaterial(worldId: string, material: WorldStartMaterialItem, startPoint?: WorldStartPointOption): void;
    getCommittedAdventures(worldId: string): WorldStartCommitResultDto[];
    getUnconfirmedDraft(draftId: string): WorldStartDraftSummaryDto | undefined;
  } = {
    setSimulatedFailure(worldId: string, shouldFail: boolean, message = 'AI 局势分析服务暂时超时，请重试。') {
      simulatedFailures.set(worldId, { fail: shouldFail, message });
    },

    setResponseDelay(worldId: string, ms: number) {
      responseDelays.set(worldId, ms);
    },

    addWorldMaterial(worldId: string, material: WorldStartMaterialItem, startPoint?: WorldStartPointOption) {
      const seed = requireSeed(worldId);
      seed.materials.push(structuredClone(material));
      if (startPoint) {
        seed.start_points.push(structuredClone(startPoint));
      }
    },

    getCommittedAdventures(worldId: string): WorldStartCommitResultDto[] {
      return structuredClone(committedAdventuresByWorld.get(worldId) ?? []);
    },

    getUnconfirmedDraft(draftId: string): WorldStartDraftSummaryDto | undefined {
      const d = unconfirmedDrafts.get(draftId);
      return d ? structuredClone(d) : undefined;
    },

    async getBootstrap(
      worldId: string,
      options?: WorldStartRequestOptions,
    ): Promise<WorldStartBootstrapDto> {
      await maybeDelay(worldId, options?.signal);
      const seed = requireSeed(worldId);
      const hasMaterials = seed.materials.some(m => m.char_count > 0);
      const latestDraftId = latestDraftByWorld.get(worldId);
      const latestDraft = latestDraftId ? unconfirmedDrafts.get(latestDraftId) ?? null : null;

      const phase: WorldStartPhase = !hasMaterials
        ? 'insufficient_materials'
        : latestDraft
          ? 'ready_to_confirm'
          : 'waiting';

      return sanitizeWorldStartBootstrap({
        world_id: seed.world_id,
        world_name: seed.world_name,
        world_description: seed.world_description,
        phase,
        materials: seed.materials,
        start_points: seed.start_points,
        latest_draft: latestDraft,
        applied_result: null,
      });
    },

    async analyzeStart(
      worldId: string,
      payload: AnalyzeWorldStartRequest,
      options?: WorldStartRequestOptions,
    ): Promise<WorldStartDraftSummaryDto> {
      await maybeDelay(worldId, options?.signal);
      const seed = requireSeed(worldId);

      const allowedMaterials = seed.materials.filter(
        m => m.world_id === worldId && payload.selected_source_ids.includes(m.source_id),
      );
      const selection = computeMaterialSelection(
        worldId,
        seed.materials,
        payload.selected_source_ids,
      );

      if (!selection.has_usable_materials || allowedMaterials.length === 0) {
        throw new Error('INSUFFICIENT_MATERIALS: 当前未选择可用的世界设定资料，请先导入或勾选至少一份资料，或直接开启普通冒险。');
      }

      const simFail = simulatedFailures.get(worldId);
      if (simFail?.fail) {
        throw new Error(simFail.message);
      }

      const attempt = (attemptCountByWorld.get(worldId) ?? 0) + 1;
      attemptCountByWorld.set(worldId, attempt);

      const allowedIds = new Set(allowedMaterials.map(m => m.source_id));
      const startId =
        payload.start_point.start_id ||
        seed.start_points[0]?.start_id ||
        'sp_custom';

      const matchedOption = seed.start_points.find(sp => sp.start_id === startId);
      const loc =
        payload.start_point.location.trim() ||
        matchedOption?.location ||
        seed.start_points[0]?.location ||
        seed.world_name;
      const charName =
        payload.start_point.character_name.trim() ||
        matchedOption?.character_name ||
        '旅人';
      const charRole =
        payload.start_point.character_role?.trim() ||
        matchedOption?.character_role ||
        '探索者';
      const startsAt =
        payload.start_point.starts_at.trim() ||
        matchedOption?.starts_at ||
        '1000-01-01T08:00';
      const advName =
        payload.start_point.adventure_name.trim() ||
        `${seed.world_name} · ${loc}之行`;

      const rawPlaces = seed.known_places_by_start[startId] ?? [
        {
          place_id: `place_${worldId}_start`,
          name: loc,
          summary: `根据《${allowedMaterials[0].title}》建立的起始地点「${loc}」。`,
          origin: 'canon' as const,
          source_refs: [
            {
              source_id: allowedMaterials[0].source_id,
              title: allowedMaterials[0].title,
              kind: allowedMaterials[0].kind,
            },
          ],
        },
      ];

      const rawNpcs = seed.raw_npcs_by_start[startId] ?? [];
      const rawFacts = seed.raw_facts_by_start[startId] ?? [];
      const inferredNotes = seed.inferred_notes_by_start[startId] ?? [
        `已根据所选的 ${allowedMaterials.length} 份《${seed.world_name}》资料补齐「${loc}」的开局环境细节（记录为本旅程推演补写）。`,
      ];
      const suggestedActions = seed.suggested_actions_by_start[startId] ?? [
        `在${loc}观察四周动静，盘算下一步去向`,
      ];

      const totalChunks = allowedMaterials.reduce((acc, m) => acc + m.chunk_count, 0);
      const draftId = `ws_draft_${worldId}_${seq++}`;
      const runKind: WorldStartRunKind =
        payload.run_kind ?? (attempt > 1 ? 'retry' : 'initial');

      const rawDraft: WorldStartDraftSummaryDto = {
        draft_id: draftId,
        world_id: worldId,
        world_name: seed.world_name,
        mode: payload.mode,
        run_kind: runKind,
        attempt_number: attempt,
        selected_source_ids: [...allowedIds],
        start_point: {
          start_id: startId,
          location: loc,
          starts_at: startsAt,
          character_name: charName,
          character_role: charRole,
          adventure_name: advName,
        },
        opening_situation_summary:
          matchedOption?.summary ||
          `在《${seed.world_name}》的「${loc}」，${charName}（${charRole}）于 ${startsAt.replace('T', ' ')} 踏入局势之中。`,
        known_places: rawPlaces,
        known_contacts: rawNpcs as unknown as WorldStartKnownContact[],
        player_known_facts: rawFacts as unknown as WorldStartPlayerKnownFact[],
        inferred_notes: inferredNotes,
        conflict_warnings: seed.conflict_warnings,
        suggested_first_actions: suggestedActions,
        batch_info: {
          total_batches: Math.max(1, selection.estimated_batches),
          merged_chunks: Math.max(1, totalChunks),
        },
        committed: false,
      };

      const cleanDraft = sanitizeWorldStartDraftSummary(rawDraft, allowedIds);
      unconfirmedDrafts.set(draftId, cleanDraft);
      latestDraftByWorld.set(worldId, draftId);
      return cleanDraft;
    },

    async confirmStart(
      worldId: string,
      draftId: string,
      payload?: ConfirmWorldStartRequest,
      options?: WorldStartRequestOptions,
    ): Promise<WorldStartCommitResultDto> {
      await maybeDelay(worldId, options?.signal);
      const seed = requireSeed(worldId);
      const draft = unconfirmedDrafts.get(draftId);
      if (!draft || draft.world_id !== worldId) {
        throw new Error('待确认的开局草案不存在或已失效，请重新发起分析。');
      }

      const simFail = simulatedFailures.get(worldId);
      if (simFail?.fail) {
        throw new Error(simFail.message);
      }

      const advId = `adv_${worldId}_${seq++}`;
      const instId = `winst_${worldId}_${seq++}`;
      const finalAdvName =
        payload?.adventure_name?.trim() || draft.start_point.adventure_name;
      const finalRunKind = payload?.run_kind ?? draft.run_kind;

      const initialNews: EncounterNewsItem[] = draft.player_known_facts.map((fact, idx) =>
        sanitizeEncounterNewsItem({
          fact_id: fact.fact_id,
          holder_id: 'player',
          knowledge_type: fact.knowledge_type,
          title: fact.title,
          content: fact.content,
          channel: fact.channel,
          source_person_id: null,
          source_label: fact.source_label,
          learned_minute: 0,
          confidence: fact.knowledge_type === 'confirmed' ? 0.95 : 0.65,
          status: 'active',
          revision: idx + 1,
        }),
      );

      const committedResult = sanitizeWorldStartCommitResult({
        adventure_id: advId,
        world_id: worldId,
        world_instance_id: instId,
        draft_id: draftId,
        adventure_name: finalAdvName,
        mode: draft.mode,
        run_kind: finalRunKind,
        quick_start: false,
        committed: true,
        starts_at: draft.start_point.starts_at,
        initial_world_minute: 0,
        world_revision: 1,
        bound_source_ids: [...draft.selected_source_ids],
        player_character: {
          name: draft.start_point.character_name,
          role: draft.start_point.character_role,
          location: draft.start_point.location,
          coins: 20,
          stamina: 100,
          spirit: 100,
          elapsed_minutes: 0,
        },
        opening_narrative: `${draft.opening_situation_summary}（世界：${seed.world_name} · 已绑定 ${draft.selected_source_ids.length} 份设定来源）`,
        initial_player_news: initialNews,
        suggested_actions: [...draft.suggested_first_actions],
      });

      unconfirmedDrafts.delete(draftId);
      if (latestDraftByWorld.get(worldId) === draftId) {
        latestDraftByWorld.delete(worldId);
      }
      const list = committedAdventuresByWorld.get(worldId) ?? [];
      list.unshift(committedResult);
      committedAdventuresByWorld.set(worldId, list);

      return committedResult;
    },

    async discardDraft(
      worldId: string,
      draftId: string,
      options?: WorldStartRequestOptions,
    ): Promise<void> {
      await maybeDelay(worldId, options?.signal);
      unconfirmedDrafts.delete(draftId);
      if (latestDraftByWorld.get(worldId) === draftId) {
        latestDraftByWorld.delete(worldId);
      }
    },

    async startQuickAdventure(
      worldId: string,
      payload: QuickStartAdventureRequest,
      options?: WorldStartRequestOptions,
    ): Promise<WorldStartCommitResultDto> {
      await maybeDelay(worldId, options?.signal);
      const seed = requireSeed(worldId);
      const advId = `adv_quick_${worldId}_${seq++}`;
      const instId = `winst_quick_${worldId}_${seq++}`;
      const defaultSp = seed.start_points[0];
      const loc =
        payload.location?.trim() ||
        defaultSp?.location ||
        `${seed.world_name}起点`;
      const charName =
        payload.character_name?.trim() ||
        defaultSp?.character_name ||
        '旅人';
      const startsAt =
        payload.starts_at?.trim() ||
        defaultSp?.starts_at ||
        '1000-01-01T08:00';
      const advName =
        payload.adventure_name.trim() ||
        `${seed.world_name} · 自由旅程`;

      const boundIds = Array.isArray(payload.selected_source_ids)
        ? payload.selected_source_ids
        : seed.materials.map(m => m.source_id);

      const quickResult = sanitizeWorldStartCommitResult({
        adventure_id: advId,
        world_id: worldId,
        world_instance_id: instId,
        draft_id: null,
        adventure_name: advName,
        mode: payload.mode,
        run_kind: 'initial',
        quick_start: true,
        committed: true,
        starts_at: startsAt,
        initial_world_minute: 0,
        world_revision: 1,
        bound_source_ids: boundIds,
        player_character: {
          name: charName,
          location: loc,
          coins: 0,
          stamina: 100,
          spirit: 100,
          elapsed_minutes: 0,
        },
        opening_narrative: `${charName}在「${loc}」开启了一段普通冒险，可随时自由输入行动。`,
        initial_player_news: [],
        suggested_actions: [`在${loc}四处走走，了解周围的情况`],
      });

      const list = committedAdventuresByWorld.get(worldId) ?? [];
      list.unshift(quickResult);
      committedAdventuresByWorld.set(worldId, list);
      return quickResult;
    },
  };

  return transport;
}

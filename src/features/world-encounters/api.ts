/**
 * M3.3 / M3.6 玩家主动查证、交谈与世界消息行动入口传输实现、纯函数与独立 Fake 场景辅助器（M3 Wave 3）
 */

import { request } from '../../api/client.ts';
import { ACTION_LIMIT, appendSuggestion } from '../../interaction.ts';
import type {
  EncounterActionOption,
  EncounterCommitResultDto,
  EncounterDraftDto,
  EncounterNewsItem,
  EncounterNpcContactability,
  EncounterOptionKind,
  EncounterResponseOutcome,
  EncounterTransmissionPreview,
  KnowledgeChannel,
  KnowledgeType,
  NpcResponsePolicy,
  PrepareEncounterDraftRequest,
  WorldEncounterEntrypointBundle,
  WorldEncountersRequestOptions,
  WorldEncountersTransport,
} from './types.ts';

export const KNOWLEDGE_TYPE_LABELS: Record<KnowledgeType, string> = {
  confirmed: '确认事实',
  suspected: '怀疑推测',
  rumor: '市井传闻',
  mistaken: '误信传言',
};

export const CHANNEL_LABELS: Record<KnowledgeChannel, string> = {
  observed: '亲眼目睹',
  notice: '公开告示',
  conversation: '当面交谈',
  rumor: '道听途说',
};

export const OPTION_KIND_LABELS: Record<EncounterOptionKind, string> = {
  ask_npc: '当面询问人物',
  check_notice: '查阅本地公告',
  free_edit: '自由编辑行动',
};

export const OUTCOME_LABELS: Record<EncounterResponseOutcome, string> = {
  clue: '获得线索',
  refused: '婉拒回答',
  vague: '含混回应',
  conflicting: '说法冲突',
  unknown: '并不知情',
  npc_departed: '人物不在场',
};

/**
 * 清洗单条玩家公开消息，确保剥离 source_event_id、隐藏标题或任何账本修改字段
 */
export function sanitizeEncounterNewsItem(raw: EncounterNewsItem): EncounterNewsItem {
  return {
    fact_id: String(raw.fact_id),
    holder_id: 'player',
    knowledge_type: raw.knowledge_type,
    title: String(raw.title ?? '').replace(/【(?:隐藏|绝密)[^】]*】/g, '').trim() || '已知消息',
    content: String(raw.content ?? '').trim(),
    channel: raw.channel,
    source_person_id: raw.source_person_id ?? null,
    source_label: String(raw.source_label || CHANNEL_LABELS[raw.channel] || '未知渠道'),
    learned_minute: Math.max(0, Math.floor(Number(raw.learned_minute) || 0)),
    confidence: Math.max(0, Math.min(1, Number(raw.confidence) || 0)),
    status: raw.status ?? 'active',
    revision: Math.max(1, Math.floor(Number(raw.revision) || 1)),
  };
}

/**
 * 清洗单条行动建议项，强制保证 advances_clock=false 与 auto_commits_turn=false
 */
export function sanitizeEncounterActionOption(raw: EncounterActionOption): EncounterActionOption {
  return {
    option_id: String(raw.option_id),
    fact_id: String(raw.fact_id),
    kind: raw.kind,
    label: String(raw.label ?? '').trim(),
    description: String(raw.description ?? '').trim(),
    suggested_action_text: String(raw.suggested_action_text ?? '').trim(),
    target_location: String(raw.target_location ?? '').trim(),
    target_npc_id: raw.target_npc_id ?? null,
    target_npc_name: raw.target_npc_name ?? null,
    target_npc_role: raw.target_npc_role ?? null,
    contactable: Boolean(raw.contactable),
    reason_code: String(raw.reason_code ?? 'OPTION_READY'),
    disabled_reason: raw.disabled_reason ?? null,
    advances_clock: false,
    auto_commits_turn: false,
  };
}

/**
 * 清洗完整的行动入口视图包，剥离任何隐藏事件计数或凭据
 */
export function sanitizeEncounterEntrypointBundle(
  raw: WorldEncounterEntrypointBundle,
): WorldEncounterEntrypointBundle {
  const news_items = Array.isArray(raw?.news_items)
    ? raw.news_items.map(sanitizeEncounterNewsItem)
    : [];
  const contactable_npcs: EncounterNpcContactability[] = Array.isArray(raw?.contactable_npcs)
    ? raw.contactable_npcs.map(npc => ({
        npc_id: String(npc.npc_id),
        name: String(npc.name),
        role: String(npc.role ?? ''),
        location: String(npc.location ?? ''),
        current_activity: String(npc.current_activity ?? ''),
        current_action_kind: npc.current_action_kind ?? 'idle',
        schedule_slot: npc.schedule_slot ?? 'day_shift',
        contactable: Boolean(npc.contactable),
        reason_code: String(npc.reason_code ?? 'NPC_CONTACTABLE'),
        reason: String(npc.reason ?? ''),
      }))
    : [];

  const options_by_fact_id: Record<string, EncounterActionOption[]> = {};
  if (raw?.options_by_fact_id && typeof raw.options_by_fact_id === 'object') {
    for (const [fid, opts] of Object.entries(raw.options_by_fact_id)) {
      options_by_fact_id[fid] = Array.isArray(opts)
        ? opts.map(sanitizeEncounterActionOption)
        : [];
    }
  }

  return {
    adventure_id: String(raw?.adventure_id ?? ''),
    player_location: String(raw?.player_location ?? ''),
    world_minute: Math.max(0, Math.floor(Number(raw?.world_minute) || 0)),
    world_revision: Math.max(0, Math.floor(Number(raw?.world_revision) || 0)),
    advances_clock: false,
    auto_commits_turn: false,
    news_items,
    contactable_npcs,
    options_by_fact_id,
  };
}

/**
 * 将玩家在「世界消息」中点击的建议项写入或追加到现有行动输入区（复用 appendSuggestion 规则）
 * 严格保证：只返回更新后的输入框草稿文本，绝不推进时间或自动发送回合。
 */
export function applyEncounterOptionToComposerDraft(
  currentDraft: string,
  option: Pick<EncounterActionOption, 'suggested_action_text' | 'contactable'>,
  limit = ACTION_LIMIT,
): { nextDraft: string | null; error: string | null; advancesClock: false; autoCommitsTurn: false } {
  if (!option.contactable) {
    return {
      nextDraft: null,
      error: '该人物当前不在场或已动身离开，无法当面询问。',
      advancesClock: false,
      autoCommitsTurn: false,
    };
  }
  const next = appendSuggestion(currentDraft, option.suggested_action_text, limit);
  if (next === null) {
    return {
      nextDraft: null,
      error: '草稿空间不足，请先精简文字。已有内容未改变。',
      advancesClock: false,
      autoCommitsTurn: false,
    };
  }
  return {
    nextDraft: next,
    error: null,
    advancesClock: false,
    autoCommitsTurn: false,
  };
}

/**
 * 默认同源 HTTP 传输实现（供 DeepSeekHarness 挂载至 /api/v1/adventures/{id}/world-encounters）
 */
export const worldEncountersApi: WorldEncountersTransport = {
  async getEntrypointBundle(
    adventureId: string,
    options?: WorldEncountersRequestOptions,
  ): Promise<WorldEncounterEntrypointBundle> {
    const raw = await request<WorldEncounterEntrypointBundle>(
      'GET',
      `/adventures/${encodeURIComponent(adventureId)}/world-encounters`,
      { signal: options?.signal },
    );
    return sanitizeEncounterEntrypointBundle(raw);
  },

  async prepareDraft(
    adventureId: string,
    payload: PrepareEncounterDraftRequest,
    options?: WorldEncountersRequestOptions,
  ): Promise<EncounterDraftDto> {
    return request<EncounterDraftDto>(
      'POST',
      `/adventures/${encodeURIComponent(adventureId)}/world-encounters/drafts`,
      { body: payload, signal: options?.signal },
    );
  },

  async commitDraft(
    adventureId: string,
    draftId: string,
    options?: WorldEncountersRequestOptions & { commit_world_minute?: number },
  ): Promise<EncounterCommitResultDto> {
    return request<EncounterCommitResultDto>(
      'POST',
      `/adventures/${encodeURIComponent(adventureId)}/world-encounters/drafts/${encodeURIComponent(draftId)}/commit`,
      {
        body: options?.commit_world_minute !== undefined
          ? { commit_world_minute: options.commit_world_minute }
          : {},
        signal: options?.signal,
      },
    );
  },
};

export interface FakeNpcKnowledgeRecord {
  fact_id: string;
  knowledge_type: KnowledgeType;
  title: string;
  content: string;
  source_event_id: string;
  source_person_id?: string | null;
  learned_minute: number;
  confidence: number;
}

export interface FakeEncounterNpcState extends EncounterNpcContactability {
  response_policy?: NpcResponsePolicy;
  refusal_reason?: string;
  knowledge_records: FakeNpcKnowledgeRecord[];
}

export interface FakeEncounterAdventureState {
  adventure_id: string;
  player_location: string;
  world_minute: number;
  world_revision: number;
  news_items: EncounterNewsItem[];
  npcs: FakeEncounterNpcState[];
  notices?: Array<{
    notice_id: string;
    location: string;
    title: string;
    content: string;
    knowledge_type: KnowledgeType;
    posted_minute: number;
    confidence: number;
  }>;
}

function buildOptionsForNewsItem(
  newsItem: EncounterNewsItem,
  playerLocation: string,
  npcs: readonly FakeEncounterNpcState[],
): EncounterActionOption[] {
  const opts: EncounterActionOption[] = [];
  for (const npc of npcs) {
    const isSameLoc = npc.location === playerLocation;
    const isCont = isSameLoc && npc.contactable;
    const reasonCode = !isSameLoc
      ? npc.current_action_kind === 'travel'
        ? 'NPC_LEFT_FOR_TRAVEL'
        : 'NPC_AT_DIFFERENT_LOCATION'
      : npc.contactable
        ? 'NPC_CONTACTABLE'
        : 'NPC_BUSY_UNCONTACTABLE';
    const reasonText = !isSameLoc
      ? npc.current_action_kind === 'travel'
        ? `${npc.name}已动身前往「${npc.location}」（${npc.current_activity}），当前不在「${playerLocation}」。`
        : `${npc.name}当前位于「${npc.location}」，不在「${playerLocation}」。`
      : npc.reason || `${npc.name}在「${playerLocation}」可交谈。`;

    opts.push({
      option_id: `opt_${newsItem.fact_id}_ask_${npc.npc_id}`,
      fact_id: newsItem.fact_id,
      kind: 'ask_npc',
      label: isCont ? `询问${npc.name}` : `寻找${npc.name}（不在场）`,
      description: reasonText,
      suggested_action_text: `在${playerLocation}向${npc.name}（${npc.role}）当面打听关于「${newsItem.title}」的实情，询问其所知的来龙去脉。`,
      target_location: playerLocation,
      target_npc_id: npc.npc_id,
      target_npc_name: npc.name,
      target_npc_role: npc.role,
      contactable: isCont,
      reason_code: reasonCode,
      disabled_reason: isCont ? null : reasonText,
      advances_clock: false,
      auto_commits_turn: false,
    });
  }

  opts.push({
    option_id: `opt_${newsItem.fact_id}_check_notice`,
    fact_id: newsItem.fact_id,
    kind: 'check_notice',
    label: `查阅${playerLocation}公告栏`,
    description: `前往${playerLocation}公告栏查阅与「${newsItem.title}」相关的正式通告。`,
    suggested_action_text: `前往${playerLocation}公告栏仔细查阅近期张贴的告示，核实「${newsItem.title}」是否有官府或商栈通告。`,
    target_location: playerLocation,
    contactable: true,
    reason_code: 'NOTICE_BOARD_AVAILABLE',
    disabled_reason: null,
    advances_clock: false,
    auto_commits_turn: false,
  });

  opts.push({
    option_id: `opt_${newsItem.fact_id}_free_edit`,
    fact_id: newsItem.fact_id,
    kind: 'free_edit',
    label: '自由编辑查证行动',
    description: `围绕「${newsItem.title}」在输入区自由编写调查行动。`,
    suggested_action_text: `围绕「${newsItem.title}」展开查证：`,
    target_location: playerLocation,
    contactable: true,
    reason_code: 'FREE_EDIT_READY',
    disabled_reason: null,
    advances_clock: false,
    auto_commits_turn: false,
  });

  return opts;
}

/**
 * 供独立开发与 UI 验收使用的确定性内存 Transport
 * 支持：NPC 可接触性变更（途中离开）、NPC 自身知情边界、草案与确认分离、晚到响应模拟及分支快照恢复
 */
export function createFakeWorldEncountersTransport(
  initialStates: Record<string, FakeEncounterAdventureState> = {},
) {
  const states = new Map<string, FakeEncounterAdventureState>();
  const drafts = new Map<string, EncounterDraftDto & { source_event_id: string }>();
  const delays = new Map<string, number>();
  let seq = 1;

  for (const [advId, st] of Object.entries(initialStates)) {
    states.set(advId, structuredClone(st));
  }

  function requireState(adventureId: string): FakeEncounterAdventureState {
    const st = states.get(adventureId);
    if (!st) {
      throw new Error(`未找到旅程 ${adventureId} 的互动状态。`);
    }
    return st;
  }

  async function maybeDelay(adventureId: string, signal?: AbortSignal) {
    if (signal?.aborted) {
      const err = new Error('请求已取消');
      err.name = 'AbortError';
      throw err;
    }
    const ms = delays.get(adventureId) ?? 0;
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

  const transport: WorldEncountersTransport & {
    setResponseDelay(adventureId: string, ms: number): void;
    updateNpcState(
      adventureId: string,
      npcId: string,
      patch: Partial<FakeEncounterNpcState>,
    ): void;
    snapshotBranch(adventureId: string): FakeEncounterAdventureState;
    restoreBranch(targetAdventureId: string, snapshotState: FakeEncounterAdventureState): void;
    getState(adventureId: string): FakeEncounterAdventureState;
  } = {
    setResponseDelay(adventureId: string, ms: number) {
      delays.set(adventureId, ms);
    },

    updateNpcState(adventureId: string, npcId: string, patch: Partial<FakeEncounterNpcState>) {
      const st = requireState(adventureId);
      const npc = st.npcs.find(n => n.npc_id === npcId);
      if (!npc) throw new Error(`NPC ${npcId} 不存在`);
      Object.assign(npc, patch);
    },

    snapshotBranch(adventureId: string): FakeEncounterAdventureState {
      return structuredClone(requireState(adventureId));
    },

    restoreBranch(targetAdventureId: string, snapshotState: FakeEncounterAdventureState) {
      const cloned = structuredClone(snapshotState);
      cloned.adventure_id = targetAdventureId;
      states.set(targetAdventureId, cloned);
    },

    getState(adventureId: string): FakeEncounterAdventureState {
      return structuredClone(requireState(adventureId));
    },

    async getEntrypointBundle(
      adventureId: string,
      options?: WorldEncountersRequestOptions,
    ): Promise<WorldEncounterEntrypointBundle> {
      await maybeDelay(adventureId, options?.signal);
      const st = requireState(adventureId);
      const options_by_fact_id: Record<string, EncounterActionOption[]> = {};
      for (const item of st.news_items) {
        options_by_fact_id[item.fact_id] = buildOptionsForNewsItem(
          item,
          st.player_location,
          st.npcs,
        );
      }
      return sanitizeEncounterEntrypointBundle({
        adventure_id: adventureId,
        player_location: st.player_location,
        world_minute: st.world_minute,
        world_revision: st.world_revision,
        advances_clock: false,
        auto_commits_turn: false,
        news_items: st.news_items,
        contactable_npcs: st.npcs.map(npc => {
          const sameLoc = npc.location === st.player_location;
          return {
            ...npc,
            contactable: sameLoc && npc.contactable,
            reason_code: !sameLoc
              ? npc.current_action_kind === 'travel'
                ? 'NPC_LEFT_FOR_TRAVEL'
                : 'NPC_AT_DIFFERENT_LOCATION'
              : npc.contactable
                ? 'NPC_CONTACTABLE'
                : 'NPC_BUSY_UNCONTACTABLE',
          };
        }),
        options_by_fact_id,
      });
    },

    async prepareDraft(
      adventureId: string,
      payload: PrepareEncounterDraftRequest,
      options?: WorldEncountersRequestOptions,
    ): Promise<EncounterDraftDto> {
      await maybeDelay(adventureId, options?.signal);
      const st = requireState(adventureId);
      const originItem = st.news_items.find(x => x.fact_id === payload.fact_id);
      if (!originItem) {
        throw new Error('玩家尚未获知该消息，无法从该条目发起查证。');
      }

      const draftId = `edraft_fake_${seq++}`;
      const loc = payload.player_location || st.player_location;

      if (payload.option_kind === 'check_notice') {
        const notice = (st.notices ?? []).find(
          n => n.location === loc && n.posted_minute <= st.world_minute,
        );
        if (!notice) {
          const emptyDraft: EncounterDraftDto & { source_event_id: string } = {
            draft_id: draftId,
            adventure_id: adventureId,
            fact_id: payload.fact_id,
            option_kind: 'check_notice',
            target_npc_id: null,
            player_location: loc,
            action_text: payload.action_text,
            outcome: 'unknown',
            outcome_label: OUTCOME_LABELS.unknown,
            reason_code: 'NOTICE_NOT_FOUND',
            narrative_reply: `你在「${loc}」公告栏仔细翻找，暂未发现相关正式通告。`,
            conflict_note: null,
            conflicting_fact_ids: [],
            proposed_transmissions: [],
            world_minute: st.world_minute,
            base_world_revision: st.world_revision,
            clock_advanced: false,
            committed: false,
            can_settle_ledger: false,
            source_event_id: 'evt_north_thread_2',
          };
          drafts.set(draftId, emptyDraft);
          return emptyDraft;
        }
        const tx: EncounterTransmissionPreview = {
          op: 'record',
          holder_id: 'player',
          knowledge_type: notice.knowledge_type,
          channel: 'notice',
          title: notice.title,
          content: notice.content,
          source_person_id: null,
          source_fact_id: null,
          learned_minute: st.world_minute,
          confidence: notice.confidence,
          can_settle_ledger: false,
        };
        const noticeDraft: EncounterDraftDto & { source_event_id: string } = {
          draft_id: draftId,
          adventure_id: adventureId,
          fact_id: payload.fact_id,
          option_kind: 'check_notice',
          target_npc_id: null,
          player_location: loc,
          action_text: payload.action_text,
          outcome: 'clue',
          outcome_label: OUTCOME_LABELS.clue,
          reason_code: 'NOTICE_VERIFIED',
          narrative_reply: `你在「${loc}」公告栏找到了正式通告：《${notice.title}》——${notice.content}`,
          conflict_note: null,
          conflicting_fact_ids: [],
          proposed_transmissions: [tx],
          world_minute: st.world_minute,
          base_world_revision: st.world_revision,
          clock_advanced: false,
          committed: false,
          can_settle_ledger: false,
          source_event_id: 'evt_north_thread_2',
        };
        drafts.set(draftId, noticeDraft);
        return noticeDraft;
      }

      if (payload.option_kind === 'free_edit' && !payload.target_npc_id) {
        const freeDraft: EncounterDraftDto & { source_event_id: string } = {
          draft_id: draftId,
          adventure_id: adventureId,
          fact_id: payload.fact_id,
          option_kind: 'free_edit',
          target_npc_id: null,
          player_location: loc,
          action_text: payload.action_text,
          outcome: 'vague',
          outcome_label: OUTCOME_LABELS.vague,
          reason_code: 'FREE_INQUIRY_RECORDED',
          narrative_reply: `你在「${loc}」按自己的计划展开查证（${payload.action_text}）。`,
          conflict_note: null,
          conflicting_fact_ids: [],
          proposed_transmissions: [],
          world_minute: st.world_minute,
          base_world_revision: st.world_revision,
          clock_advanced: false,
          committed: false,
          can_settle_ledger: false,
          source_event_id: 'evt_north_thread_2',
        };
        drafts.set(draftId, freeDraft);
        return freeDraft;
      }

      const npc = st.npcs.find(n => n.npc_id === payload.target_npc_id);
      if (!npc) {
        throw new Error('目标人物不存在。');
      }

      // 1. 检查是否途中离开或不在同一地点
      if (npc.location !== loc || !npc.contactable) {
        const depReason =
          npc.location !== loc
            ? `${npc.name}已动身前往「${npc.location}」（${npc.current_activity}），当前不在「${loc}」。`
            : `${npc.name}当前正忙，无法接待交谈。`;
        const depDraft: EncounterDraftDto & { source_event_id: string } = {
          draft_id: draftId,
          adventure_id: adventureId,
          fact_id: payload.fact_id,
          option_kind: payload.option_kind,
          target_npc_id: npc.npc_id,
          player_location: loc,
          action_text: payload.action_text,
          outcome: 'npc_departed',
          outcome_label: OUTCOME_LABELS.npc_departed,
          reason_code: npc.location !== loc ? 'NPC_LEFT_FOR_TRAVEL' : 'NPC_BUSY_UNCONTACTABLE',
          narrative_reply: depReason,
          conflict_note: null,
          conflicting_fact_ids: [],
          proposed_transmissions: [],
          world_minute: st.world_minute,
          base_world_revision: st.world_revision,
          clock_advanced: false,
          committed: false,
          can_settle_ledger: false,
          source_event_id: 'evt_north_thread_2',
        };
        drafts.set(draftId, depDraft);
        return depDraft;
      }

      // 2. 严格只读取该 NPC 在当前 world_minute 及之前已获知的记录
      const knownRecords = npc.knowledge_records.filter(
        r => r.learned_minute <= st.world_minute,
      );
      if (knownRecords.length === 0) {
        const unknownDraft: EncounterDraftDto & { source_event_id: string } = {
          draft_id: draftId,
          adventure_id: adventureId,
          fact_id: payload.fact_id,
          option_kind: payload.option_kind,
          target_npc_id: npc.npc_id,
          player_location: loc,
          action_text: payload.action_text,
          outcome: 'unknown',
          outcome_label: OUTCOME_LABELS.unknown,
          reason_code: 'NPC_HAS_NO_KNOWLEDGE',
          narrative_reply: `${npc.name}听罢摇了摇头，表示自己目前也未听说关于「${originItem.title}」的确切消息。`,
          conflict_note: null,
          conflicting_fact_ids: [],
          proposed_transmissions: [],
          world_minute: st.world_minute,
          base_world_revision: st.world_revision,
          clock_advanced: false,
          committed: false,
          can_settle_ledger: false,
          source_event_id: 'evt_north_thread_2',
        };
        drafts.set(draftId, unknownDraft);
        return unknownDraft;
      }

      if (npc.response_policy === 'reluctant') {
        const refusedDraft: EncounterDraftDto & { source_event_id: string } = {
          draft_id: draftId,
          adventure_id: adventureId,
          fact_id: payload.fact_id,
          option_kind: payload.option_kind,
          target_npc_id: npc.npc_id,
          player_location: loc,
          action_text: payload.action_text,
          outcome: 'refused',
          outcome_label: OUTCOME_LABELS.refused,
          reason_code: 'NPC_REFUSED_TO_ANSWER',
          narrative_reply:
            npc.refusal_reason || `${npc.name}婉拒透露详情，只推说事关商号机密不便多言。`,
          conflict_note: null,
          conflicting_fact_ids: [],
          proposed_transmissions: [],
          world_minute: st.world_minute,
          base_world_revision: st.world_revision,
          clock_advanced: false,
          committed: false,
          can_settle_ledger: false,
          source_event_id: knownRecords[0].source_event_id,
        };
        drafts.set(draftId, refusedDraft);
        return refusedDraft;
      }

      const topRecord = knownRecords[0];
      const conflictingIds = st.news_items
        .filter(
          item =>
            item.content !== topRecord.content &&
            ((item.knowledge_type === 'mistaken' && topRecord.knowledge_type === 'confirmed') ||
              (item.knowledge_type === 'confirmed' && topRecord.knowledge_type === 'mistaken') ||
              (item.source_person_id && item.source_person_id !== npc.npc_id)),
        )
        .map(item => item.fact_id);

      const outcome: EncounterResponseOutcome =
        npc.response_policy === 'evasive' || topRecord.confidence < 0.55
          ? 'vague'
          : conflictingIds.length > 0
            ? 'conflicting'
            : 'clue';

      const tx: EncounterTransmissionPreview = {
        op: 'record',
        holder_id: 'player',
        knowledge_type: outcome === 'vague' ? 'suspected' : topRecord.knowledge_type,
        channel: 'conversation',
        title: topRecord.title,
        content: topRecord.content,
        source_person_id: npc.npc_id,
        source_fact_id: topRecord.fact_id,
        learned_minute: st.world_minute,
        confidence: Number((topRecord.confidence * 0.95).toFixed(2)),
        can_settle_ledger: false,
      };

      const conflictNote =
        outcome === 'conflicting'
          ? `${npc.name}提供的说法（${KNOWLEDGE_TYPE_LABELS[topRecord.knowledge_type]}）与你此前听到的版本存在冲突，两条记录均会保留供你甄别。`
          : null;

      const clueDraft: EncounterDraftDto & { source_event_id: string } = {
        draft_id: draftId,
        adventure_id: adventureId,
        fact_id: payload.fact_id,
        option_kind: payload.option_kind,
        target_npc_id: npc.npc_id,
        player_location: loc,
        action_text: payload.action_text,
        outcome,
        outcome_label: OUTCOME_LABELS[outcome],
        reason_code:
          outcome === 'conflicting'
            ? 'CONFLICTING_ACCOUNTS_DETECTED'
            : outcome === 'vague'
              ? 'NPC_VAGUE_RESPONSE'
              : 'NPC_SHARED_CLUE',
        narrative_reply: `${npc.name}向你回应道：${topRecord.content}`,
        conflict_note: conflictNote,
        conflicting_fact_ids: conflictingIds,
        proposed_transmissions: [tx],
        world_minute: st.world_minute,
        base_world_revision: st.world_revision,
        clock_advanced: false,
        committed: false,
        can_settle_ledger: false,
        source_event_id: topRecord.source_event_id,
      };
      drafts.set(draftId, clueDraft);
      return clueDraft;
    },

    async commitDraft(
      adventureId: string,
      draftId: string,
      options?: WorldEncountersRequestOptions & { commit_world_minute?: number },
    ): Promise<EncounterCommitResultDto> {
      await maybeDelay(adventureId, options?.signal);
      const st = requireState(adventureId);
      const draft = drafts.get(draftId);
      if (!draft || draft.adventure_id !== adventureId) {
        throw new Error('待确认的查证草案不存在。');
      }

      if (draft.option_kind === 'ask_npc' && draft.target_npc_id && draft.proposed_transmissions.length > 0) {
        const liveNpc = st.npcs.find(n => n.npc_id === draft.target_npc_id);
        if (!liveNpc || liveNpc.location !== draft.player_location || !liveNpc.contactable) {
          throw new Error(
            `NPC_DEPARTED_BEFORE_COMMIT: ${liveNpc?.name ?? draft.target_npc_id}在回合确认前已离开「${draft.player_location}」，本次交谈未能完成。`,
          );
        }
      }

      const commitMin = options?.commit_world_minute ?? st.world_minute + 10;
      st.world_minute = Math.max(st.world_minute, commitMin);
      st.world_revision += 1;
      draft.committed = true;

      const newMessages: EncounterNewsItem[] = [];
      for (const tx of draft.proposed_transmissions) {
        const speakerNpc = st.npcs.find(n => n.npc_id === tx.source_person_id);
        const sourceLabel =
          tx.channel === 'conversation' && speakerNpc
            ? `当面交谈 · ${speakerNpc.name}`
            : CHANNEL_LABELS[tx.channel];
        const msg: EncounterNewsItem = sanitizeEncounterNewsItem({
          fact_id: `fact_player_committed_${seq++}`,
          holder_id: 'player',
          knowledge_type: tx.knowledge_type,
          title: tx.title,
          content: tx.content,
          channel: tx.channel,
          source_person_id: tx.source_person_id ?? null,
          source_label: sourceLabel,
          learned_minute: st.world_minute,
          confidence: tx.confidence,
          status: 'active',
          revision: 1,
        });
        st.news_items = [msg, ...st.news_items];
        newMessages.push(msg);
      }

      return {
        adventure_id: adventureId,
        draft_id: draftId,
        committed: true,
        outcome: draft.outcome,
        world_minute: st.world_minute,
        world_revision: st.world_revision,
        new_messages: newMessages,
        all_player_news: [...st.news_items],
        ledger_modified: false,
      };
    },
  };

  return transport;
}

/**
 * 构建北境霜木镇「旅店店主转述商队改道」标准前端验收场景
 */
export function createInnkeeperCaravanEncounterScenario(adventureId = 'adv_north_encounter_01') {
  const initialState: FakeEncounterAdventureState = {
    adventure_id: adventureId,
    player_location: '霜木镇',
    world_minute: 240,
    world_revision: 2,
    news_items: [
      {
        fact_id: 'fact_player_initial_rumor',
        holder_id: 'player',
        knowledge_type: 'rumor',
        title: '市井传闻：灰鬃商队迟迟未至',
        content: '镇上铁匠与行商议论纷纷，原定今日运抵霜木镇的灰鬃商队生铁车队不见踪影，不知在南道出了什么变故。',
        channel: 'rumor',
        source_person_id: null,
        source_label: '道听途说',
        learned_minute: 200,
        confidence: 0.4,
        status: 'active',
        revision: 1,
      },
    ],
    npcs: [
      {
        npc_id: 'npc_marta_innkeeper',
        name: '玛塔老板娘',
        role: '霜木镇灰鹿酒馆掌柜',
        location: '霜木镇',
        current_activity: '在灰鹿酒馆吧台擦拭酒杯，整理过往商旅留下的口信',
        current_action_kind: 'idle',
        schedule_slot: 'day_shift',
        contactable: true,
        reason_code: 'NPC_CONTACTABLE',
        reason: '玛塔老板娘正在灰鹿酒馆当班，可以上前交谈。',
        response_policy: 'cooperative',
        knowledge_records: [
          {
            fact_id: 'fact_marta_from_garret',
            knowledge_type: 'confirmed',
            title: '玛塔掌柜转述：灰鬃商队因边关加税改走白河谷地',
            content: '玛塔老板娘告诉你，灰鬃商队首领加雷特昨夜派快马捎来准信：因灰岩边关新加三成生铁税，商队已改走东侧白河谷地水陆联运线，生铁批次会晚三天到镇。',
            source_event_id: 'evt_north_thread_2',
            source_person_id: 'npc_garret_caravan',
            learned_minute: 180,
            confidence: 0.9,
          },
        ],
      },
      {
        npc_id: 'npc_renn_captain',
        name: '雷恩队长',
        role: '北境护路队队长',
        location: '霜木镇',
        current_activity: '在霜木镇南门哨卡核对巡防名册',
        current_action_kind: 'idle',
        schedule_slot: 'town_gate_watch',
        contactable: true,
        reason_code: 'NPC_CONTACTABLE',
        reason: '雷恩队长正在南门哨卡值守，可以上前询问。',
        response_policy: 'cooperative',
        knowledge_records: [],
      },
      {
        npc_id: 'npc_han_shopkeeper',
        name: '韩掌柜',
        role: '霜木镇铁料与矿产原料商',
        location: '寒铁城',
        current_activity: '乘马车赶往寒铁城矿务司洽谈零批生铁调货',
        current_action_kind: 'travel',
        schedule_slot: 'morning_shift',
        contactable: false,
        reason_code: 'NPC_LEFT_FOR_TRAVEL',
        reason: '韩掌柜已动身前往「寒铁城」，当前不在「霜木镇」。',
        response_policy: 'cooperative',
        knowledge_records: [],
      },
      {
        npc_id: 'npc_buck_porter',
        name: '老巴克',
        role: '霜木镇东门货栈搬运工',
        location: '霜木镇',
        current_activity: '在货栈门口捆扎空麻袋',
        current_action_kind: 'idle',
        schedule_slot: 'day_shift',
        contactable: true,
        reason_code: 'NPC_CONTACTABLE',
        reason: '老巴克正在东门货栈歇脚，可以交谈。',
        response_policy: 'cooperative',
        knowledge_records: [
          {
            fact_id: 'fact_buck_mistaken',
            knowledge_type: 'mistaken',
            title: '老巴克误信：灰鬃商队在灰岩边关被全数扣押',
            content: '老巴克信誓旦旦地声称灰鬃商队是因为走私生铁在灰岩边关被守军全数扣押，半块铁锭也运不进北境了。',
            source_event_id: 'evt_north_thread_2',
            learned_minute: 195,
            confidence: 0.65,
          },
        ],
      },
    ],
    notices: [
      {
        notice_id: 'notice_frostwood_01',
        location: '霜木镇',
        title: '霜木镇商栈通告：白河谷地转运班次说明',
        content: '镇公所与灰鬃商栈联合通告：受灰岩边关新税影响，本旬大宗生铁改由白河谷地水陆线转运入镇，预计顺延三日。',
        knowledge_type: 'confirmed',
        posted_minute: 220,
        confidence: 0.92,
      },
    ],
  };

  const transport = createFakeWorldEncountersTransport({
    [adventureId]: initialState,
  });

  return {
    adventureId,
    transport,
  };
}

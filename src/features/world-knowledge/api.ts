/**
 * M3.3 玩家世界消息传输层、纯逻辑格式化函数、零泄漏清洗与独立 Fake 验收替身
 */

import { request } from '../../api/client.ts';
import type {
  KnowledgeChannel,
  KnowledgeType,
  ListWorldNewsOptions,
  FakeWorldKnowledgeTransport,
  WorldKnowledgeTransport,
  WorldNewsItem,
  WorldNewsPage,
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

/**
 * 清洗单条玩家消息，剥离任何可能越界混入的隐藏事件 ID、未来时刻或内部字段
 */
export function sanitizeWorldNewsItem(raw: WorldNewsItem): WorldNewsItem {
  const safe: WorldNewsItem = {
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
  return safe;
}

/**
 * 清洗分页结果，确保绝不暴露 total / hidden_count / source_event_id 等全知信息
 */
export function sanitizeWorldNewsPage(raw: WorldNewsPage): WorldNewsPage {
  const items = Array.isArray(raw?.items) ? raw.items.map(sanitizeWorldNewsItem) : [];
  const next_before = typeof raw?.next_before === 'string' && raw.next_before.trim()
    ? raw.next_before.trim()
    : null;
  return { items, next_before };
}

export function formatConfidencePercent(confidence: number): string {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(confidence) ? confidence : 0));
  return `${Math.round(clamped * 100)}%`;
}

export function formatConfidenceLevel(confidence: number, type: KnowledgeType): string {
  const pct = formatConfidencePercent(confidence);
  if (type === 'rumor') return `可信度 ${pct} · 传闻待核`;
  if (type === 'mistaken') return `可信度 ${pct} · 存疑说法`;
  if (type === 'suspected') return `可信度 ${pct} · 尚属推测`;
  return `可信度 ${pct} · 渠道确凿`;
}

export function formatLearnedTime(learnedMinute: number, startsAt?: string): string {
  const safeMin = Math.max(0, Math.floor(Number.isFinite(learnedMinute) ? learnedMinute : 0));
  if (startsAt) {
    const parsed = Date.parse(startsAt.endsWith('Z') ? startsAt : `${startsAt}Z`);
    if (!Number.isNaN(parsed)) {
      const iso = new Date(parsed + safeMin * 60_000)
        .toISOString()
        .slice(0, 16)
        .replace('T', ' ');
      return `${iso}（第 ${safeMin} 分钟）`;
    }
  }
  const days = Math.floor(safeMin / 1440);
  const rem = safeMin % 1440;
  const hours = Math.floor(rem / 60);
  const mins = rem % 60;
  if (days > 0) {
    return `第 ${days + 1} 天 ${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}（${safeMin} 分钟）`;
  }
  return `第 ${safeMin} 分钟`;
}

export function filterNewsItems(
  items: readonly WorldNewsItem[],
  filter: KnowledgeType | 'all',
): WorldNewsItem[] {
  if (filter === 'all') return [...items];
  return items.filter(item => item.knowledge_type === filter);
}

export function mergeNewsItems(
  existing: readonly WorldNewsItem[],
  incoming: readonly WorldNewsItem[],
): WorldNewsItem[] {
  const seen = new Set<string>();
  const merged: WorldNewsItem[] = [];
  for (const item of [...existing, ...incoming]) {
    if (!seen.has(item.fact_id)) {
      seen.add(item.fact_id);
      merged.push(sanitizeWorldNewsItem(item));
    }
  }
  return merged;
}

/**
 * 默认 HTTP 传输实现：调用 GET /api/v1/adventures/{id}/world-news
 * 客户端不传 holder_id，由服务端根据当前旅程绑定实例与 player 身份；读取消息不会续期活动租约
 */
export const worldKnowledgeApi: WorldKnowledgeTransport = {
  async listWorldNews(
    adventureId: string,
    options?: ListWorldNewsOptions,
  ): Promise<WorldNewsPage> {
    const raw = await request<WorldNewsPage>(
      'GET',
      `/adventures/${encodeURIComponent(adventureId)}/world-news`,
      {
        queryParams: {
          before: options?.before ?? undefined,
          limit: options?.limit ?? undefined,
        },
        signal: options?.signal,
      },
    );
    return sanitizeWorldNewsPage(raw);
  },
};

function encodeFakeOpaqueCursor(index: number): string {
  return `kcur1_p${index}`;
}

function decodeFakeOpaqueCursor(cursor?: string | null): number | null {
  if (!cursor) return null;
  const match = /^kcur1_p(\d+)$/.exec(cursor.trim());
  if (!match) {
    throw new Error('分页游标无效。');
  }
  return Number(match[1]);
}

/**
 * 供独立验收与单元测试使用的内存 Fake 传输实现
 * 支持多旅程/分支隔离、不透明游标分页与获知前后状态切换
 */
export function createFakeWorldKnowledgeTransport(
  initialByAdventure: Record<string, WorldNewsItem[]> = {},
): FakeWorldKnowledgeTransport {
  const store = new Map<string, WorldNewsItem[]>();
  for (const [advId, items] of Object.entries(initialByAdventure)) {
    store.set(advId, items.map(item => sanitizeWorldNewsItem(item)));
  }

  return {
    async listWorldNews(
      adventureId: string,
      options?: ListWorldNewsOptions,
    ): Promise<WorldNewsPage> {
      if (options?.signal?.aborted) {
        const abortErr = new Error('请求已取消');
        abortErr.name = 'AbortError';
        throw abortErr;
      }
      const all = store.get(adventureId) ?? [];
      const limit = Math.max(1, Math.min(100, options?.limit ?? 30));
      const startIndex = decodeFakeOpaqueCursor(options?.before) ?? 0;
      const slice = all.slice(startIndex, startIndex + limit);
      const nextIndex = startIndex + slice.length;
      const next_before = nextIndex < all.length ? encodeFakeOpaqueCursor(nextIndex) : null;
      return sanitizeWorldNewsPage({
        items: slice.map(item => ({ ...item })),
        next_before,
      });
    },
    setAdventureNews(adventureId: string, items: WorldNewsItem[]) {
      store.set(adventureId, items.map(item => sanitizeWorldNewsItem(item)));
    },
    appendAdventureNews(adventureId: string, item: WorldNewsItem) {
      const current = store.get(adventureId) ?? [];
      const clean = sanitizeWorldNewsItem(item);
      store.set(adventureId, [clean, ...current.filter(x => x.fact_id !== clean.fact_id)]);
    },
    snapshotAdventureNews(adventureId: string): WorldNewsItem[] {
      return (store.get(adventureId) ?? []).map(item => ({ ...item }));
    },
    restoreAdventureNews(targetAdventureId: string, snapshotItems: readonly WorldNewsItem[]) {
      store.set(targetAdventureId, snapshotItems.map(item => sanitizeWorldNewsItem(item)));
    },
  };
}

/**
 * M3 Wave 2 端到端前端多跳传播替身：「商队首领先得知改道 → 店主转述 → 玩家才看见」
 */
export function createNorthTownMultiHopKnowledgeScenario(adventureId = 'adv_north_01') {
  const transport = createFakeWorldKnowledgeTransport({ [adventureId]: [] });
  let stage: 1 | 2 | 3 = 1;

  const leaderFact = {
    holder_id: 'npc:caravan_leader',
    knowledge_type: 'confirmed' as const,
    title: '商队改走东谷山道',
    content: '因邻国关卡加征生铁关税，我带灰羽商队改走东谷山道，预计晚三日到北境小镇。',
    channel: 'observed' as const,
    learned_minute: 20,
    confidence: 1.0,
  };

  const innkeeperFact = {
    holder_id: 'npc:innkeeper',
    knowledge_type: 'confirmed' as const,
    title: '灰羽商队捎信将晚三日抵镇',
    content: '商队首领林策托先遣伙计带话，因邻国加税改走东谷山道，这批生铁要晚三天才能运到。',
    channel: 'conversation' as const,
    source_person_id: 'npc:caravan_leader',
    learned_minute: 35,
    confidence: 0.85,
  };

  const playerFact: WorldNewsItem = {
    fact_id: 'fact_player_from_innkeeper',
    holder_id: 'player',
    knowledge_type: 'confirmed',
    title: '掌柜告知灰羽商队改道东谷',
    content: '酒馆掌柜老周告诉你，灰羽商队首领林策捎来消息：因邻国加征关税，商队改走东谷山道，生铁原料将推迟三天运抵小镇。',
    channel: 'conversation',
    source_person_id: 'npc:innkeeper',
    source_label: '当面交谈 · 酒馆掌柜老周',
    learned_minute: 50,
    confidence: 0.85,
    status: 'active',
    revision: 1,
  };

  return {
    transport,
    getStage: () => stage,
    /** 跳 1：商队首领先得知改道（玩家此时看不到） */
    step1CaravanLeaderLearns() {
      stage = 1;
      transport.setAdventureNews(adventureId, []);
      return { leaderFact, playerVisibleCount: 0 };
    },
    /** 跳 2：商队首领转述给酒馆店主（玩家此时仍看不到） */
    step2InnkeeperRelayed() {
      stage = 2;
      transport.setAdventureNews(adventureId, []);
      return { innkeeperFact, playerVisibleCount: 0 };
    },
    /** 跳 3：酒馆店主转述给玩家（玩家此时才看见） */
    step3PlayerTalksToInnkeeper() {
      stage = 3;
      transport.setAdventureNews(adventureId, [playerFact]);
      return { playerFact, playerVisibleCount: 1 };
    },
  };
}

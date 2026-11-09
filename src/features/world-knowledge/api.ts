/**
 * M3.3 玩家世界消息传输层、纯逻辑格式化函数与独立 Fake 验收替身
 */

import { request } from '../../api/client';
import type {
  KnowledgeChannel,
  KnowledgeType,
  ListWorldNewsOptions,
  WorldKnowledgeTransport,
  WorldNewsItem,
  WorldNewsPage,
} from './types';

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
      merged.push(item);
    }
  }
  return merged;
}

/**
 * 默认 HTTP 传输实现：调用 GET /api/v1/adventures/{id}/world-news
 * 客户端不传 holder_id，由服务端根据当前旅程绑定实例与 player 身份
 */
export const worldKnowledgeApi: WorldKnowledgeTransport = {
  listWorldNews(
    adventureId: string,
    options?: ListWorldNewsOptions,
  ): Promise<WorldNewsPage> {
    return request<WorldNewsPage>(
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
): WorldKnowledgeTransport & {
  setAdventureNews(adventureId: string, items: WorldNewsItem[]): void;
  appendAdventureNews(adventureId: string, item: WorldNewsItem): void;
  snapshotAdventureNews(adventureId: string): WorldNewsItem[];
  restoreAdventureNews(targetAdventureId: string, snapshotItems: readonly WorldNewsItem[]): void;
} {
  const store = new Map<string, WorldNewsItem[]>();
  for (const [advId, items] of Object.entries(initialByAdventure)) {
    store.set(advId, items.map(item => ({ ...item })));
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
      return {
        items: slice.map(item => ({ ...item })),
        next_before,
      };
    },
    setAdventureNews(adventureId: string, items: WorldNewsItem[]) {
      store.set(adventureId, items.map(item => ({ ...item })));
    },
    appendAdventureNews(adventureId: string, item: WorldNewsItem) {
      const current = store.get(adventureId) ?? [];
      store.set(adventureId, [{ ...item }, ...current.filter(x => x.fact_id !== item.fact_id)]);
    },
    snapshotAdventureNews(adventureId: string): WorldNewsItem[] {
      return (store.get(adventureId) ?? []).map(item => ({ ...item }));
    },
    restoreAdventureNews(targetAdventureId: string, snapshotItems: readonly WorldNewsItem[]) {
      store.set(targetAdventureId, snapshotItems.map(item => ({ ...item })));
    },
  };
}

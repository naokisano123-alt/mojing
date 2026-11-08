/**
 * 人工设定条目（Lore Entries）前端数据类型定义
 * 依据 M1_R3_API.md (1.0-r3) §1.1、§1.4 及 R3_GEMINI_MARCIN.md
 */

export type EntryType = 'character' | 'location' | 'faction' | 'rule' | 'other';
export type EntryStatus = 'draft' | 'confirmed';
export type SourceAvailability = 'active' | 'deleted' | 'unavailable';

export interface LoreEntrySource {
  document_id: string;
  chunk_id: string;
  start_char: number; // 全文 UTF-16 半开区间起点
  end_char: number;   // 全文 UTF-16 半开区间终点
  excerpt: string;    // 服务端生成的真实文本摘要（≤120 UTF-16）
  availability: SourceAvailability;
}

export interface LoreEntry {
  id: string;
  world_id: string;
  type: EntryType;
  title: string;
  summary: string;
  status: EntryStatus;
  revision: number;
  origin: 'manual';
  source_links: LoreEntrySource[];
  needs_source_review: boolean;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface LoreEntryDraft {
  type: EntryType;
  title: string;
  summary: string;
  status: EntryStatus;
  source_links: Array<{
    document_id: string;
    chunk_id: string;
    start_char: number;
    end_char: number;
    excerpt?: string;
  }>;
}

export interface SourceCandidate {
  world_id: string;
  document_id: string;
  chunk_id: string;
  chunk_index: number;
  document_filename?: string;
  filename?: string;
  heading_path: string[];
  start_char: number;
  end_char: number;
  snippet: string;
  match_field?: string;
}

export interface LoreEntriesPanelApi {
  list(params: {
    worldId: string;
    type?: string;
    status?: string;
    limit?: number;
    offset?: number;
    signal?: AbortSignal;
  }): Promise<{ items: LoreEntry[]; total: number }>;
  get(entryId: string, signal?: AbortSignal): Promise<LoreEntry>;
  create(worldId: string, payload: LoreEntryDraft): Promise<LoreEntry>;
  update(
    entryId: string,
    payload: LoreEntryDraft,
    expectedRevision: number
  ): Promise<LoreEntry>;
  remove(entryId: string, expectedRevision: number): Promise<void>;
  searchSources(params: {
    worldId: string;
    query: string;
    signal?: AbortSignal;
  }): Promise<SourceCandidate[]>;
}

export interface LoreEntriesPanelProps {
  worldId: string;
  api: LoreEntriesPanelApi;
  onOpenSource?: (target: {
    documentId: string;
    chunkIndex: number;
    startChar: number;
    endChar: number;
  }) => void;
  className?: string;
}

export const ENTRY_TYPE_LABELS: Record<EntryType, string> = {
  character: '人物',
  location: '地点',
  faction: '势力',
  rule: '规则',
  other: '其他',
};

export const ENTRY_STATUS_LABELS: Record<EntryStatus, string> = {
  draft: '草稿',
  confirmed: '已确认',
};

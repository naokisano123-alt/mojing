/**
 * 中文资料检索面板前端类型定义。
 * 遵循 ROUND2.md 与 R2_GEMINI_MA.md 规范。
 */

export type MatchField = 'text' | 'heading' | 'filename';

export interface LoreSearchItem {
  world_id: string;
  document_id: string;
  chunk_id: string;
  chunk_index: number;
  filename: string;
  heading_path: string[];
  start_char: number;
  end_char: number;
  snippet: string;
  match_field: MatchField;
  match_start_char: number;
  match_end_char: number;
  match_chunk_start_char: number;
  match_chunk_end_char: number;
  match_coords_scope: string;
}

export interface LoreSearchResult {
  items: LoreSearchItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface LoreSearchParams {
  worldId: string;
  query: string;
  limit?: number;
  offset?: number;
}

export interface LoreSourceTarget {
  documentId: string;
  chunkIndex: number;
  startChar: number;
  endChar: number;
}

export interface LoreSearchPanelProps {
  worldId: string;
  search: (params: LoreSearchParams, signal?: AbortSignal) => Promise<LoreSearchResult>;
  onOpenSource?: (target: LoreSourceTarget) => void;
  placeholder?: string;
  pageSize?: number;
  className?: string;
}

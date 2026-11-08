/**
 * 墨境 M1-A/B 前端 API 类型定义
 * 严格遵照契约 docs/contracts/M1_AB_API.md v0.2 §8.2 冻结规范
 */

export type IsoTime = string;
export type Uuid = string;

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export interface WorldSummary {
  id: Uuid;
  name: string;
  description: string;
  document_count: number;
  created_at: IsoTime;
  updated_at: IsoTime;
  deleted_at: IsoTime | null;
}

export type DocumentStatus = 'ready' | 'processing' | 'failed';
export type DocumentEncoding =
  | 'utf-8' | 'utf-8-sig' | 'utf-16-le' | 'utf-16-be' | 'gb18030' | 'big5';
export type SourceKind = 'user_import' | 'manual';

export interface WorldDocument {
  id: Uuid;
  world_id: Uuid;
  filename: string;
  media_type: 'text/plain' | 'text/markdown';
  byte_size: number;
  encoding: DocumentEncoding;
  content_sha256: string;
  text_length: number;
  line_count: number;
  chunk_count: number;
  source_kind: SourceKind;
  status: DocumentStatus;
  created_at: IsoTime;
  updated_at: IsoTime;
  deleted_at: IsoTime | null;
}

export interface WorldDetail extends WorldSummary {
  documents: WorldDocument[];
}

export interface LoreChunk {
  id: Uuid;
  document_id: Uuid;
  world_id: Uuid;
  chunk_index: number;
  heading_path: string[];
  start_char: number;
  end_char: number;
  text_length: number;
  text_preview: string;
  /** 仅 include_text=true 或单块接口存在 */
  text?: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface RawTextPage {
  document_id: Uuid;
  encoding: DocumentEncoding;
  offset: number;
  length: number;
  total_length: number;
  text: string;
  next_offset: number | null;
}

/* ================= 辅助请求参数与接口 ================= */

export interface CreateWorldRequest {
  name: string;
  description?: string;
}

export interface UpdateWorldRequest {
  name?: string;
  description?: string;
}

export interface ListWorldsParams {
  limit?: number;
  offset?: number;
}

export interface ListDocumentsParams {
  limit?: number;
  offset?: number;
  include_deleted?: boolean;
}

export interface GetRawTextParams {
  offset?: number;
  limit?: number;
}

export interface ListChunksParams {
  limit?: number;
  offset?: number;
  include_text?: boolean;
}

export interface UploadDocumentOptions {
  allow_duplicate?: boolean;
  onProgress?: (percentage: number) => void;
  signal?: AbortSignal;
}

export interface HealthResponse {
  ok: boolean;
  version: string;
  db: string;
}

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
  /** 契约 M1_R2_API §1.2：TXT/MD 为 source_bytes，PDF/DOCX 为 extracted_text */
  encoding_semantics?: 'source_bytes' | 'extracted_text';
  /** 契约 M1_R2_API §1.3：解析警告；TXT/MD 恒为空数组 */
  warnings?: DocumentWarning[];
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
  /** 仅 PDF 且解析器能提供页码时存在（契约 M1_R2_API §1.6） */
  page_range?: [number, number] | null;
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
export interface DocumentWarning {
  code: string;
  message: string;
  count?: number;
}

export interface SearchResultItem {
  world_id: Uuid;
  document_id: Uuid;
  document_filename: string;
  chunk_id: Uuid;
  chunk_index: number;
  heading_path: string[];
  /** 块在全文中的区间（UTF-16） */
  start_char: number;
  end_char: number;
  snippet: string;
  /** 命中字段：正文 / 标题 / 文件名（契约 M1_R3_API §2.3） */
  match_field: 'text' | 'heading' | 'filename';
  /** 命中在块内的区间（UTF-16），非正文命中时为 null */
  match_start: number | null;
  match_end: number | null;
}

export interface SearchResponse {
  query: string;
  items: SearchResultItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface CapabilitiesResponse {
  formats: string[];
  max_bytes: Record<string, number>;
  search: boolean;
}

/* ================= M1-D 人工条目（M1_R3_API §1.1） ================= */

export type LoreEntryType = 'character' | 'location' | 'faction' | 'rule' | 'other';
export type LoreEntryStatus = 'draft' | 'confirmed';
export type SourceAvailability = 'active' | 'deleted' | 'unavailable';

export interface LoreEntrySourceLink {
  document_id: Uuid;
  /** 真实块序号；查不到时为 null（契约 M1_R4_API §1.2） */
  chunk_index?: number | null;
  chunk_id: Uuid;
  start_char: number;
  end_char: number;
  excerpt: string;
  availability: SourceAvailability;
}

export interface LoreEntryDto {
  id: Uuid;
  world_id: Uuid;
  type: LoreEntryType;
  title: string;
  summary: string;
  status: LoreEntryStatus;
  revision: number;
  origin: 'manual';
  source_links: LoreEntrySourceLink[];
  needs_source_review: boolean;
  created_at: IsoTime;
  updated_at: IsoTime;
  deleted_at: IsoTime | null;
}

export interface LoreEntryPage {
  items: LoreEntryDto[];
  total: number;
  limit: number;
  offset: number;
}

export interface LoreEntryDraftPayload {
  type: LoreEntryType;
  title: string;
  summary: string;
  status: LoreEntryStatus;
  source_links: Array<{
    document_id: Uuid;
    chunk_id: Uuid;
    start_char: number;
    end_char: number;
  }>;
}

/* ================= M2-A 叙事实验（M2_A_API v1.0） ================= */

export type NarrationMode = 'fake' | 'live';

export interface ModelSessionCreated {
  session_id: string;
  model: string;
  expires_at: string;
}

export interface NarrationSourceRef {
  source_id: string;
  kind: 'chunk' | 'entry';
  title: string;
  document_id?: string;
  chunk_id?: string;
  chunk_index?: number;
  entry_id?: string;
}

export interface NarrationPreviewRequest {
  action: string;
  mode: NarrationMode;
  session_id?: string;
}

export interface NarrationPreview {
  request_id: string;
  mode: NarrationMode;
  narrative: string;
  references: string[];
  sources: NarrationSourceRef[];
}

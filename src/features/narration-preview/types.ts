/**
 * M2-A 模型叙事实验类型定义与接口契约
 * 依据: docs/contracts/M2_A_API.md v1.0 及 docs/collaboration/R6_GEMINI_MARCIN.md
 */

export type NarrationMode = 'fake' | 'live';

export type SourceKind = 'chunk' | 'entry';

export interface NarrationSource {
  selection_reason?: 'literal_match' | 'confirmed_fallback' | 'document_fallback';
  truncated?: boolean;
  source_id: string; // "chunk:<id>" 或 "entry:<id>"
  kind: SourceKind;
  title: string;
  document_id?: string;
  chunk_id?: string;
  chunk_index?: number;
  entry_id?: string;
}

export interface ModelSessionConfig {
  base_url: string;
  model: string;
  api_key: string;
}

export interface ModelSessionResponse {
  session_id: string;
  model: string;
  expires_at: string;
}

export interface ModelSessionTestResponse {
  ok: boolean;
  model: string;
}

export interface NarrationPreviewPayload {
  action: string;
  mode: NarrationMode;
  session_id?: string;
}

export interface NarrationPreviewResponse {
  request_id: string;
  mode: NarrationMode;
  narrative: string;
  references: string[]; // 引用的 source_id 列表，为 sources 的子集
  sources: NarrationSource[]; // 本次提供给模型的可用设定资料
}

export interface SourceJumpTarget {
  source_id: string;
  kind: SourceKind;
  document_id?: string;
  chunk_id?: string;
  chunk_index?: number;
  entry_id?: string;
}

export interface NarrationApiError {
  code: string;
  message: string;
  details?: unknown;
}

export interface NarrationPreviewApi {
  createSession(config: ModelSessionConfig, signal?: AbortSignal): Promise<ModelSessionResponse>;
  deleteSession(id: string, signal?: AbortSignal): Promise<void>;
  testSession(id: string, signal?: AbortSignal): Promise<ModelSessionTestResponse>;
  preview(
    worldId: string,
    payload: NarrationPreviewPayload,
    signal?: AbortSignal
  ): Promise<NarrationPreviewResponse>;
}

export interface NarrationPreviewProps {
  worldId: string;
  api: NarrationPreviewApi;
  onOpenSource?: (target: SourceJumpTarget) => void;
  initialMode?: NarrationMode;
  initialSession?: ModelSessionResponse | null;
  onSessionChange?: (session: ModelSessionResponse | null) => void;
}

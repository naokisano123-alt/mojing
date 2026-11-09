/**
 * 墨境 M1-A/B API 客户端
 * 严格遵照契约 docs/contracts/M1_AB_API.md v0.2
 * 
 * 约定：
 * 1. 基址与前缀：/api/v1（支持 import.meta.env.VITE_API_BASE_URL）
 * 2. 超时：查询 30s，上传 60s
 * 3. 取消：支持 AbortSignal
 * 4. 真实失败直接抛出 ApiClientError，严禁伪造模拟数据成功
 */

import type {
  ApiErrorBody,
  CreateWorldRequest,
  GetRawTextParams,
  HealthResponse,
  ListChunksParams,
  ListDocumentsParams,
  ListWorldsParams,
  LoreChunk,
  Page,
  RawTextPage,
  UpdateWorldRequest,
  UploadDocumentOptions,
  Uuid,
  WorldDetail,
  WorldDocument,
  WorldSummary,
  SearchResponse,
  CapabilitiesResponse,
} from './types';
import type { LoreEntryDto, LoreEntryPage, LoreEntryDraftPayload } from './types';

export const DEFAULT_QUERY_TIMEOUT_MS = 30_000;
export const DEFAULT_UPLOAD_TIMEOUT_MS = 60_000;

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: Record<string, unknown>;
  readonly friendlyMessage: string;

  constructor(
    code: string,
    message: string,
    status: number,
    details?: Record<string, unknown>,
  ) {
    super(`[${code}] ${message}`);
    this.name = 'ApiClientError';
    this.code = code;
    this.status = status;
    this.details = details;
    this.friendlyMessage = formatFriendlyMessage(code, message, details);
  }
}

/**
 * 契约 §8.3 友好错误提示映射
 */
export function formatFriendlyMessage(
  code: string,
  serverMessage: string,
  details?: Record<string, unknown>,
): string {
  switch (code) {
    case 'DUPLICATE_DOCUMENT': {
      const existingId = details?.existing_document_id as string | undefined;
      return existingId
        ? '该文件内容已存在于当前世界（重复导入）。'
        : '该文件内容已存在，请勿重复上传。';
    }
    case 'ENCODING_UNSUPPORTED': {
      const hint = details?.hint as string | undefined;
      return hint || '文本编码无法识别，请在电脑端以 UTF-8 格式另存后重新导入。';
    }
    case 'FILE_TOO_LARGE': {
      const maxMb = details?.max_bytes
        ? `${Math.round((details.max_bytes as number) / (1024 * 1024))} MiB`
        : '2 MiB';
      return `文件超出大小限制（最大支持 ${maxMb}）。`;
    }
    case 'UNSUPPORTED_MEDIA_TYPE':
      return '不支持的文件格式。仅允许上传 .txt 或 .md 文件。';
    case 'BINARY_CONTENT':
      return '检测到二进制内容，请上传纯文本 .txt 或 .md 文件。';
    case 'WORLD_DOCUMENT_LIMIT': {
      const limit = details?.limit ?? 100;
      return `当前世界文档数已达上限（最多 ${limit} 个），请先清理不再需要的文档。`;
    }
    case 'EMPTY_DOCUMENT':
      return '文件内容为空，请选择有效的世界观资料。';
    case 'WORLD_NAME_INVALID':
      return '世界名称不符合要求（不能为空且最多 40 个字符）。';
    case 'WORLD_NOT_FOUND':
      return '未找到指定的世界，可能已被删除。';
    case 'DOCUMENT_NOT_FOUND':
      return '未找到指定的文档，可能已被删除。';
    case 'CHUNK_NOT_FOUND':
      return '未找到指定的分块内容。';
    case 'INVALID_RANGE':
      return '读取范围超出文档边界。';
    case 'DB_UNAVAILABLE':
      return '数据库暂时不可用，请稍后重试。';
    case 'INTERNAL_ERROR':
      return '服务端发生异常，请稍后重试。';
    case 'NETWORK_ERROR':
      return '暂时连不上服务，请检查网络或后端服务状态。';
    case 'TIMEOUT_ERROR':
      return '请求超时，网络较慢或服务响应延迟，请重试。';
    case 'ABORT_ERROR':
      return '操作已取消。';
    default:
      return serverMessage || '操作失败，请重试。';
  }
}

/**
 * 获取 API 根路径，不含末尾斜杠
 */
export function getApiBaseUrl(): string {
  const envBase = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL)
    ? String(import.meta.env.VITE_API_BASE_URL).trim()
    : '';
  return envBase.replace(/\/+$/, '');
}

export function buildUrl(endpoint: string, queryParams?: Record<string, unknown>): string {
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const base = `${getApiBaseUrl()}/api/v1${cleanEndpoint}`;

  if (!queryParams) return base;

  const sp = new URLSearchParams();
  for (const [key, val] of Object.entries(queryParams)) {
    if (val !== undefined && val !== null && val !== '') {
      sp.set(key, String(val));
    }
  }
  const qs = sp.toString();
  return qs ? `${base}?${qs}` : base;
}

/**
 * 统一网络请求封装（带超时、取消与错误提取）
 */
export async function request<T>(
  method: string,
  endpoint: string,
  options?: {
    queryParams?: Record<string, unknown>;
    body?: unknown;
    signal?: AbortSignal;
    timeoutMs?: number;
  },
): Promise<T> {
  const url = buildUrl(endpoint, options?.queryParams);
  const timeoutMs = options?.timeoutMs ?? DEFAULT_QUERY_TIMEOUT_MS;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // 联动外部传入的 signal
  const onExternalAbort = () => controller.abort();
  if (options?.signal) {
    if (options.signal.aborted) {
      clearTimeout(timer);
      throw new ApiClientError('ABORT_ERROR', '操作已取消', 0);
    }
    options.signal.addEventListener('abort', onExternalAbort, { once: true });
  }

  try {
    const headers: Record<string, string> = {
      Accept: 'application/json',
    };
    let bodyPayload: BodyInit | undefined = undefined;

    if (options?.body !== undefined) {
      if (options.body instanceof FormData) {
        bodyPayload = options.body;
      } else {
        headers['Content-Type'] = 'application/json';
        bodyPayload = JSON.stringify(options.body);
      }
    }

    const res = await fetch(url, {
      method,
      headers,
      body: bodyPayload,
      signal: controller.signal,
    });

    if (res.status === 204) {
      return undefined as T;
    }

    if (!res.ok) {
      let code = `HTTP_${res.status}`;
      let message = res.statusText || '请求失败';
      let details: Record<string, unknown> | undefined = undefined;

      try {
        const errJson = (await res.json()) as ApiErrorBody;
        if (errJson?.error) {
          code = errJson.error.code || code;
          message = errJson.error.message || message;
          details = errJson.error.details;
        }
      } catch {
        // 非 JSON 格式错误体，保留状态码
      }

      throw new ApiClientError(code, message, res.status, details);
    }

    return (await res.json()) as T;
  } catch (err: unknown) {
    if (err instanceof ApiClientError) {
      throw err;
    }
    if (err instanceof DOMException && err.name === 'AbortError') {
      if (options?.signal?.aborted) {
        throw new ApiClientError('ABORT_ERROR', '请求已取消', 0);
      }
      throw new ApiClientError('TIMEOUT_ERROR', `请求超时（超过 ${timeoutMs / 1000} 秒未响应）`, 0);
    }
    throw new ApiClientError('NETWORK_ERROR', (err as Error)?.message || '网络连接异常', 0);
  } finally {
    clearTimeout(timer);
    if (options?.signal) {
      options.signal.removeEventListener('abort', onExternalAbort);
    }
  }
}

/* ================= 契约 §4 接口清单实现 ================= */

export function importWorldFiles(files: File[]): Promise<WorldSummary> {
  const body = new FormData();
  for (const file of files) body.append('files', file);
  return request('POST', '/world-transfers', { body, timeoutMs: 180_000 });
}

/** 4.1 健康检查 */
export function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('GET', '/health', { signal });
}

/** 4.2 创建世界 */
export function createWorld(
  data: CreateWorldRequest,
  signal?: AbortSignal,
): Promise<WorldSummary> {
  return request<WorldSummary>('POST', '/worlds', { body: data, signal });
}

/** 4.3 世界列表 */
export function listWorlds(
  params?: ListWorldsParams,
  signal?: AbortSignal,
): Promise<Page<WorldSummary>> {
  return request<Page<WorldSummary>>('GET', '/worlds', {
    queryParams: params as Record<string, unknown>,
    signal,
  });
}

/** 4.4 世界详情（含文档列表） */
export function getWorld(
  worldId: Uuid,
  signal?: AbortSignal,
): Promise<WorldDetail> {
  return request<WorldDetail>('GET', `/worlds/${encodeURIComponent(worldId)}`, { signal });
}

/** 4.5 修改世界名称/描述 */
export function updateWorld(
  worldId: Uuid,
  data: UpdateWorldRequest,
  signal?: AbortSignal,
): Promise<WorldSummary> {
  return request<WorldSummary>('PATCH', `/worlds/${encodeURIComponent(worldId)}`, {
    body: data,
    signal,
  });
}

/** 4.6 软删除世界 */
export function deleteWorld(
  worldId: Uuid,
  signal?: AbortSignal,
): Promise<void> {
  return request<void>('DELETE', `/worlds/${encodeURIComponent(worldId)}`, { signal });
}

/** 4.7 上传 TXT/MD 文档（带进度监听与 60s 超时） */
export function uploadDocument(
  worldId: Uuid,
  file: File,
  options?: UploadDocumentOptions,
): Promise<WorldDocument> {
  return new Promise((resolve, reject) => {
    const url = buildUrl(`/worlds/${encodeURIComponent(worldId)}/documents`, {
      allow_duplicate: options?.allow_duplicate ? 'true' : undefined,
    });

    const xhr = new XMLHttpRequest();
    const timeoutMs = DEFAULT_UPLOAD_TIMEOUT_MS;
    xhr.timeout = timeoutMs;

    // 进度监听
    if (options?.onProgress) {
      xhr.upload.onprogress = (evt) => {
        if (evt.lengthComputable && options.onProgress) {
          const pct = Math.round((evt.loaded / evt.total) * 100);
          options.onProgress(pct);
        }
      };
    }

    // 取消支持
    const onAbort = () => {
      xhr.abort();
      reject(new ApiClientError('ABORT_ERROR', '上传已取消', 0));
    };

    if (options?.signal) {
      if (options.signal.aborted) {
        return reject(new ApiClientError('ABORT_ERROR', '上传已取消', 0));
      }
      options.signal.addEventListener('abort', onAbort, { once: true });
    }

    xhr.onload = () => {
      if (options?.signal) {
        options.signal.removeEventListener('abort', onAbort);
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText) as WorldDocument;
          resolve(data);
        } catch {
          reject(new ApiClientError('INTERNAL_ERROR', '返回数据格式异常', xhr.status));
        }
      } else {
        let code = `HTTP_${xhr.status}`;
        let message = xhr.statusText || '上传失败';
        let details: Record<string, unknown> | undefined = undefined;

        try {
          const errData = JSON.parse(xhr.responseText) as ApiErrorBody;
          if (errData?.error) {
            code = errData.error.code || code;
            message = errData.error.message || message;
            details = errData.error.details;
          }
        } catch {
          // 非 JSON
        }
        reject(new ApiClientError(code, message, xhr.status, details));
      }
    };

    xhr.onerror = () => {
      if (options?.signal) options.signal.removeEventListener('abort', onAbort);
      reject(new ApiClientError('NETWORK_ERROR', '网络故障或无法连接到后端服务器', 0));
    };

    xhr.ontimeout = () => {
      if (options?.signal) options.signal.removeEventListener('abort', onAbort);
      reject(
        new ApiClientError(
          'TIMEOUT_ERROR',
          `上传超时（超过 ${timeoutMs / 1000} 秒未完成）`,
          0,
        ),
      );
    };

    const formData = new FormData();
    formData.append('file', file, file.name);

    xhr.open('POST', url, true);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.send(formData);
  });
}

/** 4.8 获取世界文档列表 */
export function listDocuments(
  worldId: Uuid,
  params?: ListDocumentsParams,
  signal?: AbortSignal,
): Promise<Page<WorldDocument>> {
  return request<Page<WorldDocument>>('GET', `/worlds/${encodeURIComponent(worldId)}/documents`, {
    queryParams: params as Record<string, unknown>,
    signal,
  });
}

/** 4.9 获取单个文档元信息 */
export function getDocument(
  documentId: Uuid,
  signal?: AbortSignal,
): Promise<WorldDocument> {
  return request<WorldDocument>('GET', `/documents/${encodeURIComponent(documentId)}`, { signal });
}

/** 4.10 原文分页读取 */
export function getDocumentRaw(
  documentId: Uuid,
  params?: GetRawTextParams,
  signal?: AbortSignal,
): Promise<RawTextPage> {
  return request<RawTextPage>('GET', `/documents/${encodeURIComponent(documentId)}/raw`, {
    queryParams: params as Record<string, unknown>,
    signal,
  });
}

/** 4.11 获取分块列表（默认瘦身，不包含完整 text） */
export function getDocumentChunks(
  documentId: Uuid,
  params?: ListChunksParams,
  signal?: AbortSignal,
): Promise<Page<LoreChunk>> {
  return request<Page<LoreChunk>>('GET', `/documents/${encodeURIComponent(documentId)}/chunks`, {
    queryParams: params as Record<string, unknown>,
    signal,
  });
}

/** 4.12 按序号取分块（恒返回完整 text） */
export function getChunkByIndex(
  documentId: Uuid,
  chunkIndex: number,
  signal?: AbortSignal,
): Promise<LoreChunk> {
  return request<LoreChunk>(
    'GET',
    `/documents/${encodeURIComponent(documentId)}/chunks/${chunkIndex}`,
    { signal },
  );
}

/** 4.13 按 ID 取分块（来源回跳入口，恒返回完整 text） */
export function getChunkById(
  chunkId: Uuid,
  signal?: AbortSignal,
): Promise<LoreChunk> {
  return request<LoreChunk>('GET', `/chunks/${encodeURIComponent(chunkId)}`, { signal });
}

/** 4.14 软删除文档 */
export function deleteDocument(
  documentId: Uuid,
  signal?: AbortSignal,
): Promise<void> {
  return request<void>('DELETE', `/documents/${encodeURIComponent(documentId)}`, { signal });
}

/** R2：资料检索（契约 M1_R2_API §2） */
export function searchLore(
  params: { worldId: string; query: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request<SearchResponse>('GET', '/search', {
    queryParams: {
      world_id: params.worldId,
      q: params.query,
      limit: params.limit,
      offset: params.offset,
    },
    signal,
  });
}

/** R2：能力探测（契约 M1_R2_API §1.7） */
export function getCapabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
  return request<CapabilitiesResponse>('GET', '/capabilities', { signal });
}

/* ================= M1-D 人工条目（M1_R3_API §1.3） ================= */

export function listLoreEntries(
  params: { worldId: string; type?: string; status?: string; limit?: number; offset?: number },
  signal?: AbortSignal,
): Promise<LoreEntryPage> {
  return request<LoreEntryPage>('GET', '/worlds/' + encodeURIComponent(params.worldId) + '/lore-entries', {
    queryParams: { type: params.type, status: params.status, limit: params.limit, offset: params.offset },
    signal,
  });
}

export function createLoreEntry(
  worldId: string,
  payload: LoreEntryDraftPayload,
  signal?: AbortSignal,
): Promise<LoreEntryDto> {
  return request<LoreEntryDto>('POST', '/worlds/' + encodeURIComponent(worldId) + '/lore-entries', { body: payload, signal });
}

export function getLoreEntry(entryId: string, signal?: AbortSignal): Promise<LoreEntryDto> {
  return request<LoreEntryDto>('GET', '/lore-entries/' + encodeURIComponent(entryId), { signal });
}

export function updateLoreEntry(
  entryId: string,
  payload: Partial<LoreEntryDraftPayload>,
  expectedRevision: number,
  signal?: AbortSignal,
): Promise<LoreEntryDto> {
  return request<LoreEntryDto>('PATCH', '/lore-entries/' + encodeURIComponent(entryId), {
    body: { ...payload, expected_revision: expectedRevision },
    signal,
  });
}

export function deleteLoreEntry(
  entryId: string,
  expectedRevision: number,
  signal?: AbortSignal,
): Promise<void> {
  return request<void>('DELETE', '/lore-entries/' + encodeURIComponent(entryId), {
    queryParams: { expected_revision: expectedRevision },
    signal,
  });
}

import type { ModelSessionCreated, NarrationPreview, NarrationPreviewRequest } from './types';

/* ================= M2-A 叙事实验（M2_A_API v1.0） ================= */

export function createModelSession(
  payload: { base_url: string; model: string; api_key: string },
  signal?: AbortSignal,
): Promise<ModelSessionCreated> {
  return request('POST', '/model-sessions', { body: payload, signal });
}

export function restoreModelProfile(signal?: AbortSignal): Promise<{ session: ModelSessionCreated | null }> {
  return request('POST', '/model-profile/restore', { signal });
}

export function deleteModelProfile(signal?: AbortSignal): Promise<void> {
  return request('DELETE', '/model-profile', { signal });
}

export function deleteModelSession(sessionId: string, signal?: AbortSignal): Promise<void> {
  return request<void>('DELETE', '/model-sessions/' + encodeURIComponent(sessionId), { signal });
}

export function testModelSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ ok: boolean; model: string }> {
  return request('POST', '/model-sessions/' + encodeURIComponent(sessionId) + '/test', { signal });
}

export function previewNarration(
  worldId: string,
  payload: NarrationPreviewRequest,
  signal?: AbortSignal,
): Promise<NarrationPreview> {
  return request('POST', '/worlds/' + encodeURIComponent(worldId) + '/narration-preview', {
    body: payload,
    signal,
    timeoutMs: 60000,
  });
}

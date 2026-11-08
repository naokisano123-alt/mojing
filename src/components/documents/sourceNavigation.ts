import type { LoreChunk } from '../../api/types.ts';

export interface SourceRequest {
  chunkId?: string;
  chunkIndex?: number;
  startChar?: number;
  endChar?: number;
}

interface ChunkReader {
  byId(id: string, signal?: AbortSignal): Promise<LoreChunk>;
  byIndex(documentId: string, index: number, signal?: AbortSignal): Promise<LoreChunk>;
}

/** Resolve persisted source identity; never guess the first chunk. */
export async function loadSourceChunk(
  documentId: string, source: SourceRequest, reader: ChunkReader, signal?: AbortSignal,
): Promise<LoreChunk | null> {
  signal?.throwIfAborted();
  if (!source.chunkId && source.chunkIndex === undefined) return null;
  const chunk = source.chunkId
    ? await reader.byId(source.chunkId, signal)
    : await reader.byIndex(documentId, source.chunkIndex!, signal);
  signal?.throwIfAborted();
  if (chunk.document_id !== documentId || typeof chunk.text !== 'string' ||
      (source.chunkId && chunk.id !== source.chunkId) ||
      (!source.chunkId && chunk.chunk_index !== source.chunkIndex)) {
    throw new Error('来源与文档不匹配或全文不可用');
  }
  const { startChar: start, endChar: end } = source;
  if (start !== undefined || end !== undefined) {
    if (start === undefined || end === undefined || !Number.isSafeInteger(start) ||
        !Number.isSafeInteger(end) || start < chunk.start_char || end <= start || end > chunk.end_char) {
      throw new Error('来源区间已失效');
    }
    for (const offset of [start - chunk.start_char, end - chunk.start_char]) {
      const prev = chunk.text.charCodeAt(offset - 1), next = chunk.text.charCodeAt(offset);
      if (prev >= 0xd800 && prev <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
        throw new Error('来源区间切开了完整字符');
      }
    }
  }
  return chunk;
}

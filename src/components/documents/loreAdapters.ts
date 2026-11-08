import type { CapabilitiesResponse, SearchResultItem } from '../../api/types.ts';
import type { LoreSearchItem } from '../../features/lore-search/types.ts';

const SAFE_UPLOAD_BYTES = 2 * 1024 * 1024;

/** Use each advertised format's limit; missing capabilities stay conservative. */
export function getUploadPolicy(capabilities?: CapabilitiesResponse) {
  const supportedFormats = capabilities?.formats ?? ['.txt', '.md'];
  const maxBytesByFormat: Record<string, number> = {};
  for (const format of supportedFormats) {
    const limit = capabilities?.max_bytes?.[format];
    maxBytesByFormat[format] =
      typeof limit === 'number' && Number.isSafeInteger(limit) && limit > 0
        ? limit
        : SAFE_UPLOAD_BYTES;
  }
  return { supportedFormats, maxBytes: SAFE_UPLOAD_BYTES, maxBytesByFormat };
}

/** Convert chunk-relative UTF-16 offsets without inventing a body match. */
export function toLoreSearchItem(item: SearchResultItem): LoreSearchItem {
  const start = item.match_start;
  const end = item.match_end;
  const hasBodyMatch =
    item.match_field === 'text' &&
    typeof start === 'number' && Number.isSafeInteger(start) &&
    typeof end === 'number' && Number.isSafeInteger(end) &&
    start >= 0 && end > start && end <= item.end_char - item.start_char;

  return {
    world_id: item.world_id,
    document_id: item.document_id,
    chunk_id: item.chunk_id,
    chunk_index: item.chunk_index,
    filename: item.document_filename,
    heading_path: item.heading_path,
    start_char: item.start_char,
    end_char: item.end_char,
    snippet: item.snippet,
    match_field: item.match_field,
    match_start_char: hasBodyMatch ? item.start_char + start : null,
    match_end_char: hasBodyMatch ? item.start_char + end : null,
    match_chunk_start_char: hasBodyMatch ? start : null,
    match_chunk_end_char: hasBodyMatch ? end : null,
    match_coords_scope: 'document_and_chunk_utf16',
  };
}

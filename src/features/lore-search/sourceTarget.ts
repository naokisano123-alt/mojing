import type { LoreSearchItem, LoreSourceTarget } from './types.ts';

/** Non-body hits open their real chunk, with no fabricated body highlight. */
export function toLoreSourceTarget(item: LoreSearchItem): LoreSourceTarget {
  const target: LoreSourceTarget = {
    documentId: item.document_id,
    chunkIndex: item.chunk_index,
  };
  const start = item.match_start_char;
  const end = item.match_end_char;
  if (
    item.match_field === 'text' &&
    typeof start === 'number' && Number.isSafeInteger(start) &&
    typeof end === 'number' && Number.isSafeInteger(end) &&
    start >= item.start_char && end > start && end <= item.end_char
  ) {
    target.startChar = start;
    target.endChar = end;
  }
  return target;
}

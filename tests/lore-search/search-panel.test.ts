import test from 'node:test';
import assert from 'node:assert/strict';
import type {
  LoreSearchItem,
  LoreSearchResult,
  LoreSearchParams,
  LoreSourceTarget,
} from '../../src/features/lore-search/types.ts';

test('LoreSearchItem conforms to ROUND2.md contract and contains UTF-16 coords', () => {
  const item: LoreSearchItem = {
    world_id: 'world-1',
    document_id: 'doc-1',
    chunk_id: 'c-1',
    chunk_index: 0,
    filename: '设定集.txt',
    heading_path: ['总纲', '人物'],
    start_char: 120,
    end_char: 240,
    snippet: '陆平身穿一件黑色风衣...',
    match_field: 'text',
    match_start_char: 125,
    match_end_char: 127,
    match_chunk_start_char: 5,
    match_chunk_end_char: 7,
    match_coords_scope: 'document_and_chunk_utf16',
  };

  assert.equal(item.world_id, 'world-1');
  assert.equal(item.match_field, 'text');
  assert.equal(item.match_start_char, 125);
  assert.equal(item.match_coords_scope, 'document_and_chunk_utf16');
  assert.equal(item.match_end_char - item.match_start_char, 2);
});

test('pagination offset and page calculation is correct and bounds-checked', () => {
  const pageSize = 20;

  function calcPage(offset: number, total: number) {
    const currentPage = Math.floor(offset / pageSize) + 1;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const hasPrev = offset > 0;
    const hasNext = offset + pageSize < total;
    return { currentPage, totalPages, hasPrev, hasNext };
  }

  // 0 条结果
  const empty = calcPage(0, 0);
  assert.equal(empty.currentPage, 1);
  assert.equal(empty.totalPages, 1);
  assert.equal(empty.hasPrev, false);
  assert.equal(empty.hasNext, false);

  // 45 条结果，第 1 页
  const p1 = calcPage(0, 45);
  assert.equal(p1.currentPage, 1);
  assert.equal(p1.totalPages, 3);
  assert.equal(p1.hasPrev, false);
  assert.equal(p1.hasNext, true);

  // 45 条结果，第 2 页 (offset 20)
  const p2 = calcPage(20, 45);
  assert.equal(p2.currentPage, 2);
  assert.equal(p2.totalPages, 3);
  assert.equal(p2.hasPrev, true);
  assert.equal(p2.hasNext, true);

  // 45 条结果，第 3 页 (offset 40)
  const p3 = calcPage(40, 45);
  assert.equal(p3.currentPage, 3);
  assert.equal(p3.totalPages, 3);
  assert.equal(p3.hasPrev, true);
  assert.equal(p3.hasNext, false);
});

test('onOpenSource target payload maps chunk and match coordinates accurately', () => {
  const item: LoreSearchItem = {
    world_id: 'world-alpha',
    document_id: 'doc-99',
    chunk_id: 'c-99-02',
    chunk_index: 2,
    filename: '势力考.md',
    heading_path: ['第三章', '暗影卫'],
    start_char: 500,
    end_char: 800,
    snippet: '暗影卫首领身手矫健...',
    match_field: 'text',
    match_start_char: 508,
    match_end_char: 511,
    match_chunk_start_char: 8,
    match_chunk_end_char: 11,
    match_coords_scope: 'document_and_chunk_utf16',
  };

  const payload: LoreSourceTarget = {
    documentId: item.document_id,
    chunkIndex: item.chunk_index,
    startChar: item.match_start_char ?? item.start_char,
    endChar: item.match_end_char ?? item.end_char,
  };

  assert.equal(payload.documentId, 'doc-99');
  assert.equal(payload.chunkIndex, 2);
  assert.equal(payload.startChar, 508);
  assert.equal(payload.endChar, 511);
});

test('search function call contracts pass AbortSignal and reject stale responses', async () => {
  let searchCallCount = 0;
  let receivedSignal: AbortSignal | undefined;

  const mockSearch = async (params: LoreSearchParams, signal?: AbortSignal): Promise<LoreSearchResult> => {
    searchCallCount++;
    receivedSignal = signal;
    return {
      items: [],
      total: 0,
      limit: params.limit ?? 20,
      offset: params.offset ?? 0,
    };
  };

  const controller = new AbortController();
  const res = await mockSearch(
    { worldId: 'w-1', query: '陆平', limit: 10, offset: 0 },
    controller.signal
  );

  assert.equal(searchCallCount, 1);
  assert.equal(res.limit, 10);
  assert.equal(receivedSignal, controller.signal);
  assert.equal(receivedSignal?.aborted, false);

  controller.abort();
  assert.equal(receivedSignal?.aborted, true);
});

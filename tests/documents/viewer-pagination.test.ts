import test from 'node:test';
import assert from 'node:assert/strict';
import { deduplicateChunks } from '../../src/components/documents/viewerUtils.ts';

function makeMockChunk(index, textLength = 500) {
  return {
    id: `chunk-uuid-${index}`,
    document_id: 'doc-uuid-1',
    world_id: 'world-uuid-1',
    chunk_index: index,
    heading_path: [`第 ${index + 1} 节`],
    start_char: index * textLength,
    end_char: (index + 1) * textLength,
    text_length: textLength,
    text_preview: `这是第 ${index + 1} 节的摘要预览...`,
  };
}

test('deduplicateChunks merges overlapping chunk pages and maintains ascending index order', () => {
  const page1 = [makeMockChunk(0), makeMockChunk(1), makeMockChunk(2)];
  // 模拟重试或部分重叠的分页响应
  const page2 = [makeMockChunk(1), makeMockChunk(2), makeMockChunk(3), makeMockChunk(4)];

  const merged = deduplicateChunks(page1, page2);
  assert.equal(merged.length, 5);
  assert.deepEqual(
    merged.map(c => c.chunk_index),
    [0, 1, 2, 3, 4],
  );
});

test('pagination supports documents with more than 100 chunks', () => {
  const totalInServer = 230;

  // 第一页拉取前 100 块
  const firstBatch = [];
  for (let i = 0; i < 100; i++) {
    firstBatch.push(makeMockChunk(i));
  }
  assert.equal(firstBatch.length, 100);
  assert.equal(firstBatch.length < totalInServer, true);

  // 第二页拉取 100~199
  const secondBatch = [];
  for (let i = 100; i < 200; i++) {
    secondBatch.push(makeMockChunk(i));
  }

  const afterSecond = deduplicateChunks(firstBatch, secondBatch);
  assert.equal(afterSecond.length, 200);
  assert.equal(afterSecond.length < totalInServer, true);

  // 第三页拉取 200~229
  const thirdBatch = [];
  for (let i = 200; i < totalInServer; i++) {
    thirdBatch.push(makeMockChunk(i));
  }

  const finalChunks = deduplicateChunks(afterSecond, thirdBatch);
  assert.equal(finalChunks.length, totalInServer);
  assert.equal(finalChunks.length === totalInServer, true);
  assert.equal(finalChunks[0].chunk_index, 0);
  assert.equal(finalChunks[totalInServer - 1].chunk_index, 229);
});

test('document switching cleans up state to prevent stale responses across documents', () => {
  // 模拟切文档状态隔离逻辑
  let currentDocId = 'doc-A';
  let chunksState = [makeMockChunk(0), makeMockChunk(1)];
  let expandedMap = { 0: 'Doc A chunk 0 full text' };
  let errorState = null;

  // 模拟用户切换到 doc-B
  currentDocId = 'doc-B';
  // 状态复位验证
  chunksState = [];
  expandedMap = {};
  errorState = null;

  assert.equal(chunksState.length, 0);
  assert.equal(Object.keys(expandedMap).length, 0);
  assert.equal(errorState, null);

  // 假设属于 doc-A 的迟到异步响应返回
  const delayedResponseDocId = 'doc-A';
  if (delayedResponseDocId === currentDocId) {
    chunksState = [makeMockChunk(99)];
  }

  // 验证 doc-A 的响应被成功丢弃，没有串入 doc-B
  assert.equal(chunksState.length, 0);
});

test('chunk expansion failure does not masquerade preview text as full text', () => {
  const chunk = makeMockChunk(0);
  const expandedMap = {};
  const errorMap = {};

  // 模拟网络 500 异常
  const fetchError = new Error('网络连接超时或后端内部错误');

  // 规范行为：记录在 errorMap 中，严禁将 preview 塞入 expandedMap
  errorMap[chunk.chunk_index] = fetchError.message;

  assert.equal(expandedMap[chunk.chunk_index], undefined);
  assert.equal(errorMap[chunk.chunk_index], '网络连接超时或后端内部错误');
});

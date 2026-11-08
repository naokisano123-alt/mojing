import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSourceChunk } from '../../src/components/documents/sourceNavigation.ts';
import { deduplicateChunks } from '../../src/components/documents/viewerUtils.ts';
import type { LoreChunk } from '../../src/api/types.ts';

const chunk: LoreChunk = { id: 'late', document_id: 'doc', world_id: 'world', chunk_index: 120,
  heading_path: [], start_char: 1000, end_char: 1004, text: '甲😀乙', text_preview: '甲', text_length: 4 };
const reader = { byId: async () => chunk, byIndex: async () => chunk };

test('persisted chunk ID resolves a late chunk without guessing index zero', async () => {
  const actual = await loadSourceChunk('doc', { chunkId: 'late', startChar: 1001, endChar: 1003 }, {
    byId: async id => { assert.equal(id, 'late'); return chunk; },
    byIndex: async () => { throw new Error('must not guess index'); },
  });
  assert.equal(actual?.chunk_index, 120);
  const first = { ...chunk, id: 'first', chunk_index: 0 };
  const visible = deduplicateChunks([first], [actual!]);
  assert.deepEqual(visible.map(c => c.chunk_index), [0, 120]);
  assert.equal(deduplicateChunks(visible, [actual!]).length, 2);
});

test('index-only search targets resolve and ordinary opening needs no extra request', async () => {
  assert.equal(await loadSourceChunk('doc', { chunkIndex: 120 }, reader), chunk);
  assert.equal(await loadSourceChunk('doc', {}, reader), null);
});

test('wrong document, missing full text and wrong identity are rejected', async () => {
  await assert.rejects(loadSourceChunk('other', { chunkId: 'late' }, reader));
  await assert.rejects(loadSourceChunk('doc', { chunkId: 'wrong' }, reader));
  await assert.rejects(loadSourceChunk('doc', { chunkIndex: 0 }, reader));
  await assert.rejects(loadSourceChunk('doc', { chunkId: 'late' }, {
    ...reader, byId: async () => ({ ...chunk, text: undefined }),
  }));
});

test('UTF-16 range rejects surrogate splits, incomplete and out-of-block references', async () => {
  for (const range of [{ startChar: 1002, endChar: 1003 }, { startChar: 0, endChar: 1 },
    { startChar: 1000 }, { startChar: 1001, endChar: 1001 }]) {
    await assert.rejects(loadSourceChunk('doc', { chunkId: 'late', ...range }, reader));
  }
});

test('late responses after cancellation cannot become a resolved source', async () => {
  const controller = new AbortController();
  let resolve!: (value: LoreChunk) => void;
  const pending = loadSourceChunk('doc', { chunkId: 'late' }, {
    ...reader, byId: () => new Promise(r => { resolve = r; }),
  }, controller.signal);
  controller.abort(); resolve(chunk);
  await assert.rejects(pending, { name: 'AbortError' });
});

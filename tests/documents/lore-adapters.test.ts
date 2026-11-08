import test from 'node:test';
import assert from 'node:assert/strict';
import { getUploadPolicy, toLoreSearchItem } from '../../src/components/documents/loreAdapters.ts';
import { toLoreSourceTarget } from '../../src/features/lore-search/sourceTarget.ts';
import type { SearchResultItem } from '../../src/api/types.ts';

const bodyMatch: SearchResultItem = {
  world_id: 'world',
  document_id: 'doc',
  document_filename: 'harbor.md',
  chunk_id: 'chunk-2',
  chunk_index: 2,
  heading_path: ['港口'],
  start_char: 100,
  end_char: 200,
  snippet: '甲😀港口',
  match_field: 'text',
  // JS/UTF-16: 甲=1, 😀=2, so 港口 starts at unit 3.
  match_start: 3,
  match_end: 5,
};

test('upload policy preserves each server format limit, including custom values', () => {
  const policy = getUploadPolicy({
    formats: ['.txt', '.md', '.pdf', '.docx'],
    max_bytes: { '.txt': 1024, '.md': 2048, '.pdf': 8388608, '.docx': 4194304 },
    search: true,
  });
  assert.deepEqual(policy.maxBytesByFormat, {
    '.txt': 1024, '.md': 2048, '.pdf': 8388608, '.docx': 4194304,
  });
  assert.equal(policy.maxBytes, 2097152);
});

test('missing capabilities or invalid limits retain conservative upload defaults', () => {
  assert.deepEqual(getUploadPolicy(), {
    supportedFormats: ['.txt', '.md'],
    maxBytes: 2097152,
    maxBytesByFormat: { '.txt': 2097152, '.md': 2097152 },
  });
  const policy = getUploadPolicy({
    formats: ['.txt', '.md', '.pdf', '.docx'],
    max_bytes: { '.txt': -1, '.pdf': Infinity, '.docx': 0.5 },
    search: true,
  });
  assert.ok(Object.values(policy.maxBytesByFormat).every(value => value === 2097152));
});

test('real body hit converts UTF-16 offsets and forwards the actual chunk target', () => {
  const mapped = toLoreSearchItem(bodyMatch);
  assert.equal(mapped.match_start_char, 103);
  assert.equal(mapped.match_end_char, 105);
  assert.equal(mapped.match_chunk_start_char, 3);
  assert.equal(mapped.match_chunk_end_char, 5);
  assert.deepEqual(toLoreSourceTarget(mapped), {
    documentId: 'doc', chunkIndex: 2, startChar: 103, endChar: 105,
  });
  const atStart = toLoreSourceTarget(toLoreSearchItem({
    ...bodyMatch, start_char: 0, match_start: 0, match_end: 1,
  }));
  assert.equal(atStart.startChar, 0);
  assert.equal(atStart.endChar, 1);
});

test('heading and filename hits preserve null coordinates and open only their chunk', () => {
  for (const match_field of ['heading', 'filename'] as const) {
    const mapped = toLoreSearchItem({
      ...bodyMatch, match_field, match_start: null, match_end: null,
    });
    assert.equal(mapped.match_field, match_field);
    assert.equal(mapped.match_start_char, null);
    assert.equal(mapped.match_end_char, null);
    assert.equal(mapped.match_chunk_start_char, null);
    assert.equal(mapped.match_chunk_end_char, null);
    assert.deepEqual(toLoreSourceTarget(mapped), { documentId: 'doc', chunkIndex: 2 });
    // Even old data with numeric fallback coordinates cannot fabricate a body hit.
    assert.deepEqual(toLoreSourceTarget({
      ...mapped, match_start_char: 100, match_end_char: 150,
    }), { documentId: 'doc', chunkIndex: 2 });
  }
});

test('missing, reversed, fractional or out-of-bounds offsets never become a highlight', () => {
  for (const [match_start, match_end] of [
    [null, null], [null, 5], [3, null], [4, 4], [5, 3], [-1, 5], [3, 101], [0.5, 5],
  ] as const) {
    const mapped = toLoreSearchItem({ ...bodyMatch, match_start, match_end });
    assert.equal(mapped.match_start_char, null);
    assert.equal(mapped.match_end_char, null);
    assert.deepEqual(toLoreSourceTarget(mapped), { documentId: 'doc', chunkIndex: 2 });
  }
});

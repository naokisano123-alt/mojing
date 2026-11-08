import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ApiClientError,
  formatFriendlyMessage,
  buildUrl,
} from '../../src/api/client.ts';
import type {
  WorldSummary,
  WorldDocument,
  LoreChunk,
  RawTextPage,
} from '../../src/api/types.ts';

test('buildUrl formats endpoints and query parameters properly', () => {
  const url1 = buildUrl('/health');
  assert.equal(url1, '/api/v1/health');

  const url2 = buildUrl('worlds/123/documents', {
    limit: 10,
    offset: 20,
    include_deleted: false,
    empty_val: undefined,
  });
  assert.equal(url2, '/api/v1/worlds/123/documents?limit=10&offset=20&include_deleted=false');
});

test('formatFriendlyMessage maps contract §8.3 error codes to user-friendly messages', () => {
  const dupMsg = formatFriendlyMessage('DUPLICATE_DOCUMENT', 'dup', {
    existing_document_id: 'doc-123',
  });
  assert.match(dupMsg, /重复导入/);

  const encMsg = formatFriendlyMessage('ENCODING_UNSUPPORTED', 'enc', {
    hint: '请另存为 UTF-8',
  });
  assert.equal(encMsg, '请另存为 UTF-8');

  const sizeMsg = formatFriendlyMessage('FILE_TOO_LARGE', 'large', {
    max_bytes: 2097152,
  });
  assert.match(sizeMsg, /2 MiB/);

  const mediaMsg = formatFriendlyMessage('UNSUPPORTED_MEDIA_TYPE', 'media');
  assert.match(mediaMsg, /\.txt 或 \.md/);

  const limitMsg = formatFriendlyMessage('WORLD_DOCUMENT_LIMIT', 'limit', {
    limit: 20,
  });
  assert.match(limitMsg, /20 个/);

  const timeoutMsg = formatFriendlyMessage('TIMEOUT_ERROR', 'timeout');
  assert.match(timeoutMsg, /超时/);

  const abortMsg = formatFriendlyMessage('ABORT_ERROR', 'abort');
  assert.match(abortMsg, /取消/);
});

test('ApiClientError carries status, code, details and friendly message', () => {
  const err = new ApiClientError(
    'FILE_TOO_LARGE',
    'Payload too large',
    413,
    { max_bytes: 2097152, received_bytes: 3000000 },
  );

  assert.equal(err.name, 'ApiClientError');
  assert.equal(err.code, 'FILE_TOO_LARGE');
  assert.equal(err.status, 413);
  assert.equal(err.details?.received_bytes, 3000000);
  assert.match(err.friendlyMessage, /2 MiB/);
  assert.match(err.message, /\[FILE_TOO_LARGE\]/);
});

test('LoreChunk DTO conforms to contract §8.2 constraints', () => {
  const chunk: LoreChunk = {
    id: 'e71a8f90-1234-4567-89ab-cdef01234567',
    document_id: '5c2a1b3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d',
    world_id: '0f3b2c1a-9d4e-4a71-8c55-2b1e6a7d9f10',
    chunk_index: 0,
    heading_path: ['第一卷 雾港', '第一章 钟声'],
    start_char: 0,
    end_char: 1372,
    text_length: 1372,
    text_preview: '# 第一卷 雾港\n\n潮水退去的时候……',
  };

  assert.equal(chunk.chunk_index, 0);
  assert.equal(chunk.end_char - chunk.start_char, chunk.text_length);
  assert.equal(chunk.heading_path.length, 2);
  assert.equal(chunk.text, undefined); // 默认瘦身
});

test('RawTextPage DTO pagination calculation is consistent', () => {
  const rawPage: RawTextPage = {
    document_id: '5c2a1b3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d',
    encoding: 'utf-8',
    offset: 0,
    length: 20000,
    total_length: 23814,
    text: 'A'.repeat(20000),
    next_offset: 20000,
  };

  assert.equal(rawPage.next_offset, 20000);
  assert.equal(rawPage.total_length - rawPage.length, 3814);
});

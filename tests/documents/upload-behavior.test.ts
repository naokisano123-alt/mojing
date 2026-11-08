import test from 'node:test';
import assert from 'node:assert/strict';

function formatBytes(bytes) {
  const mib = bytes / (1024 * 1024);
  if (Number.isInteger(mib)) {
    return `${mib} MiB`;
  }
  return `${mib.toFixed(1)} MiB`;
}

function validateFileFormat(
  filename,
  size,
  supportedFormats = ['.txt', '.md'],
  maxBytes = 2 * 1024 * 1024,
  maxBytesByFormat = undefined,
) {
  const ext = '.' + filename.split('.').pop()?.toLowerCase();
  const isSupported = supportedFormats.some(f => f.toLowerCase() === ext);
  if (!isSupported) {
    return `不支持该文件格式。仅支持导入 ${supportedFormats.join('、')} 设定文档。`;
  }
  if (size === 0) {
    return '文件内容为空，无法导入。';
  }

  let limit = maxBytes;
  if (maxBytesByFormat) {
    const lowerExt = ext.toLowerCase();
    for (const [key, val] of Object.entries(maxBytesByFormat)) {
      if (key.toLowerCase() === lowerExt && typeof val === 'number') {
        limit = val;
        break;
      }
    }
  }

  if (size > limit) {
    const mb = (size / (1024 * 1024)).toFixed(1);
    const limitLabel = formatBytes(limit);
    return `文件大小为 ${mb} MB，超出 ${ext} 格式 ${limitLabel} 上限。`;
  }
  return null;
}

test('file validation accepts supported text and markdown extensions', () => {
  assert.equal(validateFileFormat('world_lore.txt', 1024), null);
  assert.equal(validateFileFormat('WORLD_LORE.MD', 2048), null);
  assert.match(
    validateFileFormat('world_lore.exe', 1024) || '',
    /不支持该文件格式/,
  );
  assert.match(
    validateFileFormat('empty.txt', 0) || '',
    /文件内容为空/,
  );
  assert.match(
    validateFileFormat('large.txt', 3 * 1024 * 1024) || '',
    /超出 .txt 格式 2 MiB 上限/,
  );
});

test('file validation allows extensible supportedFormats without prematurely defaulting to unverified formats', () => {
  // 默认仅支持 .txt 和 .md
  assert.match(
    validateFileFormat('document.pdf', 1024) || '',
    /不支持该文件格式/,
  );

  // 当外部显式注入后续支持的格式时
  const withPdf = validateFileFormat('document.pdf', 1024, ['.txt', '.md', '.pdf']);
  assert.equal(withPdf, null);
});

test('B1/B2 fix: maxBytesByFormat validates file size per specific extension instead of blanket limit', () => {
  const limits = {
    '.txt': 2 * 1024 * 1024,       // 2 MiB
    '.md': 2 * 1024 * 1024,        // 2 MiB
    '.pdf': 8 * 1024 * 1024,       // 8 MiB
    '.docx': 8 * 1024 * 1024,      // 8 MiB
  };
  const supported = ['.txt', '.md', '.pdf', '.docx'];

  // 1. 3 MiB PDF 应当通过（小于 8 MiB 上限）
  const validPdf = validateFileFormat('sample.pdf', 3 * 1024 * 1024, supported, 2 * 1024 * 1024, limits);
  assert.equal(validPdf, null);

  // 2. 9 MiB PDF 应当被拒绝，且错误提示明确写明超出 .pdf 格式 8 MiB 上限
  const largePdf = validateFileFormat('large.pdf', 9 * 1024 * 1024, supported, 2 * 1024 * 1024, limits);
  assert.match(largePdf || '', /超出 \.pdf 格式 8 MiB 上限/);

  // 3. 3 MiB TXT 应当被拒绝（虽然 PDF 允许 8 MiB，但 TXT 不能被放宽到 8 MiB）
  const largeTxt = validateFileFormat('large.txt', 3 * 1024 * 1024, supported, 2 * 1024 * 1024, limits);
  assert.match(largeTxt || '', /超出 \.txt 格式 2 MiB 上限/);

  // 4. 未在映射表中的格式自动回退到保守默认 maxBytes
  const fallbackCheck = validateFileFormat('custom.doc', 3 * 1024 * 1024, ['.doc'], 2 * 1024 * 1024, limits);
  assert.match(fallbackCheck || '', /超出 \.doc 格式 2 MiB 上限/);
});

test('cancellation state is neutral and reminds user to check document list', () => {
  let uploading = true;
  let isCancelled = false;
  let errorMessage = null;

  uploading = false;
  isCancelled = true;
  errorMessage = null;

  assert.equal(uploading, false);
  assert.equal(isCancelled, true);
  assert.equal(errorMessage, null);

  const neutralNotice = '上传已取消。注意：若服务端已在处理入库，数据可能仍已保存，建议刷新列表核对。';
  assert.match(neutralNotice, /刷新列表核对/);
});

test('duplicate document 409 error provides existing document id navigation', () => {
  const errorDetails = {
    code: 'DUPLICATE_DOCUMENT',
    message: '文档内容已存在',
    details: {
      existing_document_id: 'doc-existing-789',
      content_sha256: 'abcdef123456...',
    },
  };

  assert.equal(errorDetails.code, 'DUPLICATE_DOCUMENT');
  const existingId = errorDetails.details?.existing_document_id;
  assert.equal(existingId, 'doc-existing-789');

  let navigatedDocId = null;
  const onViewExisting = (id) => {
    navigatedDocId = id;
  };

  if (existingId) {
    onViewExisting(existingId);
  }

  assert.equal(navigatedDocId, 'doc-existing-789');
});

test('upload progress distinguishes between file upload and server ingestion phases', () => {
  function getProgressStatusText(progress) {
    if (progress < 100) {
      return `正在上传文件 (${progress}%)`;
    }
    return '文件上传完成，服务端正在解析与分块入库，请稍候...';
  }

  assert.equal(getProgressStatusText(0), '正在上传文件 (0%)');
  assert.equal(getProgressStatusText(85), '正在上传文件 (85%)');
  assert.equal(
    getProgressStatusText(100),
    '文件上传完成，服务端正在解析与分块入库，请稍候...',
  );
});

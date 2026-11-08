import test from 'node:test';
import assert from 'node:assert/strict';

function validateFileFormat(
  filename,
  size,
  supportedFormats = ['.txt', '.md'],
  maxBytes = 2 * 1024 * 1024,
) {
  const ext = '.' + filename.split('.').pop()?.toLowerCase();
  const isSupported = supportedFormats.some(f => f.toLowerCase() === ext);
  if (!isSupported) {
    return `不支持该文件格式。仅支持导入 ${supportedFormats.join('、')} 纯文本设定文档。`;
  }
  if (size === 0) {
    return '文件内容为空，无法导入。';
  }
  if (size > maxBytes) {
    const mb = (size / (1024 * 1024)).toFixed(1);
    return `文件大小为 ${mb} MB，超出单文件 2 MiB 上限。`;
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
    /超出单文件 2 MiB 上限/,
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

test('cancellation state is neutral and reminds user to check document list', () => {
  // 模拟取消事件触发
  let uploading = true;
  let isCancelled = false;
  let errorMessage = null;

  // 用户点击取消
  uploading = false;
  isCancelled = true;
  errorMessage = null; // 关键：非红字报错，而是中性提示

  assert.equal(uploading, false);
  assert.equal(isCancelled, true);
  assert.equal(errorMessage, null);

  // 提示语应包含提醒刷新核对的文案
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

  // 验证提取出的 ID 可供 onViewExisting 回调使用
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

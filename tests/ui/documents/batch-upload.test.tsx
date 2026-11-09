import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { ApiClientError, uploadDocument } from '../../../src/api/client';
import { DocumentUpload } from '../../../src/components/documents/DocumentUpload';

vi.mock('../../../src/api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../../../src/api/client')>(),
  uploadDocument: vi.fn(),
}));

const upload = vi.mocked(uploadDocument);
const success = { id: 'document', chunk_count: 1 } as Awaited<ReturnType<typeof uploadDocument>>;

function select(files: File[]) {
  fireEvent.change(screen.getByLabelText('选择世界观资料'), { target: { files } });
}

beforeEach(() => {
  upload.mockReset().mockResolvedValue(success);
});

test('selects and imports twenty files, continuing past a duplicate', async () => {
  upload.mockRejectedValueOnce(new ApiClientError('DUPLICATE_DOCUMENT', '重复', 409, {
    existing_document_id: 'existing',
  }));
  const onSuccess = vi.fn();
  render(<DocumentUpload worldId="world" onSuccess={onSuccess} />);
  const files = Array.from({ length: 20 }, (_, index) =>
    new File([`内容 ${index}`], `设定-${index}.txt`));
  expect(screen.getByLabelText('选择世界观资料')).toHaveAttribute('multiple');
  select(files);
  fireEvent.click(screen.getByRole('button', { name: '导入 20 份资料' }));
  await screen.findByText('共 20 份 · 成功 19 · 未导入 1 · 待处理 0');
  expect(upload.mock.calls.map(([, file]) => file.name)).toEqual(files.map(file => file.name));
  expect(onSuccess).toHaveBeenCalledTimes(19);
  fireEvent.click(screen.getByRole('button', { name: '仍然导入这份文件' }));
  await screen.findByText('共 20 份 · 成功 20 · 未导入 0 · 待处理 0');
  expect(upload).toHaveBeenLastCalledWith('world', files[0], expect.objectContaining({ allow_duplicate: true }));
});

test('keeps completed files and stops the queue when the user cancels', async () => {
  upload.mockResolvedValueOnce(success).mockImplementationOnce((_id, _file, options) =>
    new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error('cancelled')), { once: true });
    }));
  const onSuccess = vi.fn();
  render(<DocumentUpload worldId="world" onSuccess={onSuccess} />);
  select(['a', 'b', 'c'].map(name => new File([name], `${name}.md`)));
  fireEvent.click(screen.getByRole('button', { name: '导入 3 份资料' }));
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: '停止导入' }));
  await screen.findByText('共 3 份 · 成功 1 · 未导入 0 · 待处理 2');
  await waitFor(() => expect(screen.getByRole('button', { name: '导入 2 份资料' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '导入 2 份资料' }));
  await screen.findByText('共 3 份 · 成功 3 · 未导入 0 · 待处理 0');
  expect(upload.mock.calls.map(([, file]) => file.name)).toEqual(['a.md', 'b.md', 'b.md', 'c.md']);
  expect(onSuccess).toHaveBeenCalledTimes(3);
});

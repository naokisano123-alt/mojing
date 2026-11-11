import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { DocumentViewer } from '../../../src/components/documents/DocumentViewer';

afterEach(() => vi.unstubAllGlobals());

function backend(text: string, conflict = false) {
  const meta = { id: 'doc', world_id: 'world', filename: '世界.md', encoding: 'utf-8',
    media_type: 'text/markdown', content_sha256: 'a'.repeat(64), text_length: text.length,
    byte_size: text.length, line_count: 3, chunk_count: 1, source_kind: 'user_import',
    status: 'ready', created_at: '2026-11-11T00:00:00Z', updated_at: '2026-11-11T00:00:00Z' };
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json' },
  });
  const fetcher = vi.fn(async (input, options) => {
    const url = new URL(String(input), 'http://localhost');
    if (options?.method === 'PUT') {
      if (conflict) return json({ error: { code: 'DOCUMENT_EDIT_CONFLICT', message: '文档已经改变，草稿已保留。' } }, 409);
      const body = JSON.parse(options.body);
      text = body.text;
      meta.content_sha256 = 'b'.repeat(64);
      return json(meta);
    }
    if (url.pathname.endsWith('/raw')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 20000);
      const part = text.slice(offset, offset + limit);
      return json({ document_id: 'doc', offset, length: part.length, total_length: text.length,
        next_offset: offset + part.length < text.length ? offset + part.length : null, text: part });
    }
    if (url.pathname.endsWith('/chunks')) return json({ items: [], total: 0, limit: 100, offset: 0 });
    return json(meta);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

test('pencil loads every text page, eye saves the complete edit and returns to read-only', async () => {
  const text = '海港'.repeat(28000) + '文末🌊';
  const fetcher = backend(text);
  const saved = vi.fn();
  render(<DocumentViewer documentId="doc" onClose={() => {}} onSaved={saved} />);
  await screen.findByRole('button', { name: '编辑文档' });
  fireEvent.click(screen.getByRole('button', { name: '编辑文档' }));
  const editor = await screen.findByRole('textbox', { name: '世界观文档正文' });
  expect(editor).toHaveValue(text);
  fireEvent.change(editor, { target: { value: text + '新增约定' } });
  fireEvent.click(screen.getByRole('button', { name: '保存并切换只读' }));
  await waitFor(() => expect(screen.queryByRole('textbox', { name: '世界观文档正文' })).toBeNull());
  expect(saved).toHaveBeenCalledOnce();
  const call = fetcher.mock.calls.find(([, options]) => options?.method === 'PUT')!;
  expect(JSON.parse(call[1].body)).toEqual({ text: text + '新增约定', expected_content_sha256: 'a'.repeat(64) });
  expect(screen.getByRole('button', { name: '编辑文档' })).toBeInTheDocument();
});

test('a save conflict preserves editable text and prevents closing with an unsaved edit', async () => {
  backend('初始正文', true);
  const close = vi.fn();
  render(<DocumentViewer documentId="doc" onClose={close} />);
  fireEvent.click(await screen.findByRole('button', { name: '编辑文档' }));
  const editor = await screen.findByRole('textbox', { name: '世界观文档正文' });
  fireEvent.change(editor, { target: { value: '我写下的正文' } });
  fireEvent.click(screen.getByRole('button', { name: '关闭查看器' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('草稿已保留');
  expect(editor).toHaveValue('我写下的正文');
  expect(close).not.toHaveBeenCalled();
});

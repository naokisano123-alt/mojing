import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../../src/App';

const original = { id: 'source', name: '同一个世界', description: '原始世界', document_count: 0,
  created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', deleted_at: null };
const imported = { ...original, id: 'imported', name: '同一个世界（导入）' };
const result = (text: string) => ({ request_id: text, mode: 'fake', narrative: text, references: [], sources: [] });
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '#home');
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});

it('导入入口归入新世界，ZIP 续玩后退出再进入仍选中独立副本，并读取超过首页50个世界', async () => {
  let copied = false;
  let detail = { id: 'journey', world_id: 'imported', name: '原旅程', mode: 'fake', revision: 1,
    turns: [{ sequence: 1, action: '最初的行动', result: result('导入前的故事。') }], latest_draft: null as unknown };
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const path = url.pathname;
    if (path.endsWith('/model-profile')) return json({ session: null });
    if (path.endsWith('/capabilities')) return json({});
    if (path.endsWith('/world-transfers')) { copied = true; return json(imported, 201); }
    if (path.endsWith('/worlds')) {
      const all = copied ? [original, ...Array.from({ length: 49 }, (_, i) => ({ ...original, id: `old-${i}`, name: `旧世界${i}` })), imported] : [original];
      const offset = Number(url.searchParams.get('offset') || 0);
      return json({ items: all.slice(offset, offset + 50), total: all.length, offset, limit: 50 });
    }
    if (path.endsWith('/worlds/imported')) return json({ ...imported, documents: [] });
    if (path.endsWith('/worlds/imported/adventures')) return json({ items: [detail] });
    if (path.endsWith('/openings')) return json({ items: [] });
    if (path.endsWith('/adventures/journey')) return json(detail);
    if (path.endsWith('/drafts/next/commit')) {
      detail = { ...detail, revision: 2, turns: [...detail.turns, { sequence: 2, action: '继续探索', result: result('续玩的新故事。') }], latest_draft: null };
      return json(detail);
    }
    if (path.endsWith('/adventures/journey/drafts') && options?.method === 'POST') {
      const body = JSON.parse(String(options.body));
      expect(body.expected_revision).toBe(1);
      return json({ id: 'next', status: 'ready', action: body.action, result: result('续玩的新故事。') });
    }
    return json({ error: { code: 'NOT_FOUND', message: 'not mocked' } }, 404);
  });
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<App />);
  expect((await screen.findByRole('heading', { name: '同一个世界', exact: true })).closest('details')).toBeNull();
  expect(screen.queryByRole('button', { name: '导入世界或存档', exact: true })).toBeNull();
  await user.click(screen.getByRole('button', { name: '新的世界与冒险', exact: true }));
  expect(screen.getByRole('dialog', { name: '新的世界与冒险' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '导入世界或存档', exact: true }));
  await user.upload(screen.getByLabelText('选择迁移文件'), new File(['mock-zip'], 'backup.zip', { type: 'application/zip' }));
  await user.click(screen.getByRole('button', { name: '导入为独立副本' }));
  await user.click(await screen.findByRole('button', { name: '继续冒险' }));
  await screen.findByText('导入前的故事。');
  expect(location.hash).toBe('#lore/imported');
  await user.click(screen.getByRole('button', { name: '自由编辑行动' }));
  await user.type(screen.getByLabelText('你的行动'), '继续探索');
  await user.click(screen.getByRole('button', { name: '发送行动' }));
  await screen.findByText('待确认草案 · 尚未写入回合');
  await user.click(screen.getByRole('button', { name: '确认并写入回合' }));
  await screen.findByText(/第 2 回合/);
  await user.click(screen.getByRole('button', { name: '返回世界列表' }));
  await screen.findByRole('heading', { name: imported.name, exact: true });
  await waitFor(() => expect(fetcher.mock.calls.some(args => String(args[0]).includes('offset=50'))).toBe(true));
  expect(document.querySelector('.world-card h2')?.textContent).toBe(imported.name);
  await user.click(screen.getByRole('button', { name: `查看${imported.name}的世界资料库` }));
  await user.click(await screen.findByRole('button', { name: '继续冒险' }));
  expect(await screen.findByText('续玩的新故事。')).toBeVisible();
  expect(location.hash).toBe('#lore/imported');
  expect(localStorage.getItem('mojing:active-adventure:imported')).toBe('journey');
  expect(localStorage.getItem('mojing:last-world')).toBe('imported');
});

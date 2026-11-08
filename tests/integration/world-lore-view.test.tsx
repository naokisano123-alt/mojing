import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WorldLoreView } from '../../src/components/documents/WorldLoreView';

const WORLD = {
  id: 'w1',
  name: '潮汐档案馆',
  description: '端到端装配测试用世界',
  document_count: 1,
  created_at: '2026-11-08T00:00:00Z',
  updated_at: '2026-11-08T00:00:00Z',
  deleted_at: null,
  documents: [
    {
      id: 'd1',
      world_id: 'w1',
      filename: '云山纪.md',
      media_type: 'text/markdown',
      byte_size: 1200,
      encoding: 'utf-8',
      content_sha256: 'abc',
      text_length: 800,
      line_count: 20,
      chunk_count: 3,
      source_kind: 'user_import',
      status: 'ready',
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      deleted_at: null,
    },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('WorldLoreView 装配', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/capabilities')) {
          return json({
            formats: ['.txt', '.md', '.pdf', '.docx'],
            max_bytes: { '.txt': 2097152, '.md': 2097152, '.pdf': 8388608, '.docx': 8388608 },
            search: true,
            lore_entries: true,
          });
        }
        if (url.includes('/lore-entries')) {
          return json({ items: [], total: 0, limit: 20, offset: 0 });
        }
        if (url.includes('/worlds/w1')) {
          return json(WORLD);
        }
        return json({ error: { code: 'NOT_FOUND', message: 'not mocked' } }, 404);
      }),
    );
  });

  it('加载世界详情并渲染诚实横幅与文档列表', async () => {
    render(<WorldLoreView worldId="w1" worldName="潮汐档案馆" onBack={() => {}} />);

    expect(await screen.findByText('云山纪.md')).toBeTruthy();
    expect(screen.getByText(/世界资料库模式/)).toBeTruthy();
    expect(screen.getByText(/AI 角色与剧情生成将在 M2 开启/)).toBeTruthy();
  });

  it('四个子页签都能真实挂载', async () => {
    const user = userEvent.setup();
    render(<WorldLoreView worldId="w1" worldName="潮汐档案馆" onBack={() => {}} />);
    await screen.findByText('云山纪.md');

    await user.click(screen.getByRole('button', { name: /检索资料/ }));
    expect(await screen.findByRole('textbox')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /人工设定/ }));
    expect(await screen.findByText('新建设定条目')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /导入新文档/ }));
    await waitFor(() => expect(document.querySelector('input[type="file"]')).toBeTruthy());
  });

  it('切换世界会重新拉取且不残留旧标题', async () => {
    const { rerender } = render(<WorldLoreView worldId="w1" worldName="旧世界" onBack={() => {}} />);
    await screen.findByText('云山纪.md');

    rerender(<WorldLoreView worldId="w2" worldName="新世界" onBack={() => {}} />);
    await waitFor(() => expect(screen.queryByText('云山纪.md')).toBeNull());
  });
});
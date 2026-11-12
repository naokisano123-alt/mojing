import { useState } from 'react';
import type { ModelSessionResponse } from '../../src/features/narration-preview/types';
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
        if (url.endsWith('/model-sessions')) {
          return json({ session_id: 'session', model: 'test', expires_at: '2099-01-01T00:00:00Z' }, 201);
        }
        if (['/adventures', '/openings', '/journeys'].some(path => url.split('?')[0].endsWith(path))) {
          return json({ items: [] });
        }
        if (url.includes('/lore-entries')) {
          return json({ items: [], total: 0, limit: 20, offset: 0 });
        }
        if (url.endsWith('/world-start')) return json({ materials: [], start_points: [] });
        if (url.includes('/worlds/w1')) {
          return json(WORLD);
        }
        return json({ error: { code: 'NOT_FOUND', message: 'not mocked' } }, 404);
      }),
    );
  });

  it('世界观内提供世界导出，旅程可选择并包含全部存档', async () => {
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, options) => String(input).endsWith('/journeys')
      ? json({ items: [{id:'j1', name:'过客', active_adventure_id:'j1'}, {id:'j2', name:'归人', active_adventure_id:'j2'}] }) : original(input, options));
    const user = userEvent.setup();
    render(<WorldLoreView worldId="w1" worldName="潮汐档案馆" onBack={() => {}} />);
    await screen.findByText('云山纪.md');
    await user.click(screen.getByText('导出世界与旅程'));
    expect(screen.getByRole('link', {name:'仅导出世界观'})).toHaveAttribute('href','/api/v1/worlds/w1/transfer?part=world');
    const all = await screen.findByRole('checkbox', {name:'全选旅程'});
    expect(all).toBeChecked();
    await user.click(screen.getByRole('checkbox', {name:'归人'}));
    expect(screen.getByRole('link', {name:'导出世界与所选旅程（ZIP）'})).toHaveAttribute('href','/api/v1/worlds/w1/transfer?part=zip&journeys=j1');
    await user.click(screen.getByRole('checkbox', {name:'过客'}));
    expect(screen.queryByRole('link', {name:'导出世界与所选旅程（ZIP）'})).toBeNull();
  });

  it('三个底部栏目和文档内的设定及导入能真实挂载', async () => {
    const user = userEvent.setup();
    render(<WorldLoreView worldId="w1" worldName="潮汐档案馆" onBack={() => {}} />);
    await screen.findByText('云山纪.md');

    expect(screen.getByRole('navigation', { name: '世界栏目' }).querySelectorAll('button')).toHaveLength(3);
    expect(screen.getByRole('textbox', { name: '检索关键词' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '检索资料' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '叙事实验' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /人工设定/ }));
    expect(await screen.findByText('新建设定条目')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: '返回世界观文档' }));
    await user.click(screen.getByRole('button', { name: '导入', exact: true }));
    await waitFor(() => expect(document.querySelector('input[type="file"]')).toBeTruthy());
  });

  it('模型连接归入设置，返回冒险后默认创建真实模型旅程', async () => {
    function Play() {
      const [session, setSession] = useState<ModelSessionResponse | null>(null);
      return <WorldLoreView worldId="w1" worldName={WORLD.name} onBack={() => {}}
        initialTab="journal" modelSession={session} onModelSessionChange={setSession} />;
    }
    const user = userEvent.setup();
    render(<Play />);
    await user.click(screen.getByRole('button', { name: '设置', exact: true }));
    expect(screen.queryByTestId('action-input')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/API Key/), 'test-key');
    await user.click(screen.getByRole('button', { name: '连接并建立会话' }));
    await screen.findByText('会话已激活');
    await user.click(screen.getByRole('button', { name: '冒险', exact: true }));
    expect(screen.queryByLabelText('新冒险模式')).toBeNull();
    await user.click(await screen.findByRole('button', { name: '开始旅程' }));
    await user.click(screen.getByText('游玩方式', {selector:'summary'}));
    expect(await screen.findByLabelText('游玩方式')).toHaveValue('live');
    expect(screen.queryByLabelText('初始位置')).toBeNull();
    expect(screen.getByLabelText('你的开局')).toBeVisible();
    expect(screen.queryByRole('button', { name: '管理模型连接' })).not.toBeInTheDocument();
  });

  it('切换世界会重新拉取且不残留旧标题', async () => {
    const { rerender } = render(<WorldLoreView worldId="w1" worldName="旧世界" onBack={() => {}} />);
    await screen.findByText('云山纪.md');

    rerender(<WorldLoreView worldId="w2" worldName="新世界" onBack={() => {}} />);
    await waitFor(() => expect(screen.queryByText('云山纪.md')).toBeNull());
  });
});
it('switching to documents and settings preserves the pending adventure action and moves pulse out of the story', async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  const run = { id: 'run1', world_id: 'w1', name: '海港旅程', mode: 'live', revision: 1,
    turns: [], latest_draft: null, memory: { cursor: 0, items: [], events: [] },
    character: { name: '旅人', location: '海港', coins: 0, stamina: 100, spirit: 100, elapsed_minutes: 15 } };
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    const path = String(input).split('?')[0];
    if (path.endsWith('/worlds/w1/adventures')) return json({ items: [run] });
    if (path.endsWith('/adventures/run1')) return json(run);
    if (path.endsWith('/world-pulse')) return json({ enabled: false, mode: 'unbound', state: 'off' });
    if (path.endsWith('/checkpoints')) return json({ items: [] });
    return original(input, options);
  });
  const user = userEvent.setup();
  render(<WorldLoreView worldId="w1" worldName={WORLD.name} onBack={() => {}}
    initialTab="journal" modelSession={{ session_id: 'session', model: 'test', expires_at: null }} />);
  await user.click(await screen.findByRole('button', {name:'继续冒险'}));
  await screen.findByText('旅程尚未落笔。写下第一个行动。');
  expect(screen.queryByRole('button', { name: '管理模型连接' })).toBeNull();
  expect(screen.queryByRole('region', { name: '存档与分支' })).toBeNull();
  await user.click(screen.getByRole('button', { name: '自由编辑行动' }));
  await user.type(screen.getByLabelText('你的行动'), '我还没有写完的行动');
  await user.click(screen.getByRole('button', { name: '世界观文档', exact: true }));
  expect(screen.getByRole('textbox', { name: '检索关键词' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '设置', exact: true }));
  expect(screen.getByText('世界脉动设置')).toBeVisible();
  expect(screen.queryByRole('button', { name: '发送行动' })).toBeNull();
  await user.click(screen.getByRole('button', { name: '冒险', exact: true }));
  await user.click(await screen.findByRole('button', {name:'继续冒险'}));
  expect(screen.getByLabelText('你的行动')).toHaveValue('我还没有写完的行动');
  expect(screen.getByText('世界脉动设置')).not.toBeVisible();
  await user.click(screen.getByRole('button', {name:'返回冒险入口'}));
  await user.click(screen.getByRole('button', {name:'存档与读档'}));
  expect(await screen.findByRole('region', {name:'存档与读档'})).toBeVisible();
});

it('存档归入冒险，读档后返回对应正文，待确认故事跨栏目仍保留', async () => {
  localStorage.clear();
  const turn = (narrative: string) => ({ sequence: 1, action: '走进街道', result: {
    request_id: 'confirmed', mode: 'fake', narrative, references: [], sources: [],
  } });
  const current = { id: 'current', world_id: 'w1', name: '当前旅程', mode: 'fake', revision: 1,
    turns: [turn('当前路线的正文。')], latest_draft: { id: 'pending', status: 'ready', action: '看看窗外',
      result: { request_id: 'pending', mode: 'fake', narrative: '还未确认的故事。', references: [], sources: [] } } };
  const restored = { ...current, id: 'restored', origin_checkpoint_id: 'cp', latest_draft: null,
    turns: [turn('存档恢复后的正文。')] };
  const checkpoint = { id: 'cp', adventure_id: 'current', name: '雨夜之前', revision: 1, mode: 'fake', created_at: 1 };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).split('?')[0];
    if (path.endsWith('/worlds/w1')) return json(WORLD);
    if (path.endsWith('/capabilities')) return json({});
    if (path.endsWith('/worlds/w1/adventures')) return json({ items: [current] });
    if (path.endsWith('/adventures/current')) return json(current);
    if (path.endsWith('/adventures/restored')) return json(restored);
    if (path.endsWith('/journeys/current/load/cp')) return json(restored);
    if (path.endsWith('/journeys')) return json({items:[{id:'current',name:'当前旅程',active_adventure_id:'current',revision:1,mode:'fake'}]});
    if (path.endsWith('/journeys/current/saves')) return json({items:[{...checkpoint,kind:'manual'}]});
    if (path.endsWith('/checkpoints')) return json({ items: [checkpoint] });
    if (path.endsWith('/openings')) return json({ items: [] });
    return json({ error: { code: 'NOT_FOUND', message: 'not mocked' } }, 404);
  });
  vi.stubGlobal('fetch', fetcher);
  const user = userEvent.setup();
  render(<WorldLoreView worldId="w1" worldName={WORLD.name} onBack={() => {}} initialTab="journal" />);
  await user.click(await screen.findByRole('button', {name:'继续冒险'}));
  await screen.findByText('还未确认的故事。');
  expect(screen.queryByLabelText('选择冒险记录')).toBeNull();
  await user.click(screen.getByRole('button', {name:'返回冒险入口'}));
  await user.click(screen.getByRole('button', {name:'存档与读档'}));
  expect(await screen.findByRole('region', {name:'存档与读档'})).toBeVisible();
  expect(screen.queryByText('还未确认的故事。')).toBeNull();
  await user.click(screen.getByRole('button', { name: '冒险', exact: true }));
  await user.click(await screen.findByRole('button', {name:'继续冒险'}));
  expect(screen.getByText('还未确认的故事。')).toBeVisible();
  await user.click(screen.getByRole('button', {name:'返回冒险入口'}));
  await user.click(screen.getByRole('button', {name:'存档与读档'}));
  await user.click(await screen.findByRole('button', {name:/当前旅程.*第 1 回合/}));
  await user.click(await screen.findByRole('button', {name:'读取存档'}));
  expect(await screen.findByText('存档恢复后的正文。')).toBeVisible();
  expect(screen.getByRole('button', { name: '冒险', exact: true })).toHaveAttribute('aria-current', 'page');
  expect(screen.queryByRole('region', { name: '存档与分支' })).toBeNull();
  expect(localStorage.getItem('mojing:active-adventure:w1')).toBe('restored');
});

it('新建世界先整理文档，完成后才出现主栏目与冒险入口', async () => {
  const fetcher = vi.fn(async input => {
    const path=String(input).split('?')[0];
    if(path.endsWith('/capabilities')) return json({});
    if(path.endsWith('/adventures') || path.endsWith('/openings')) return json({items:[]});
    if(path.endsWith('/worlds/w1')) return json(WORLD);
    return json({items:[]});
  });
  vi.stubGlobal('fetch',fetcher);
  localStorage.setItem('mojing:prepare-world:w1','1');
  const user=userEvent.setup();
  render(<WorldLoreView worldId="w1" worldName={WORLD.name} onBack={() => {}} initialTab="list" prepareWorld />);
  expect(await screen.findByRole('heading',{name:'整理你的世界'})).toBeVisible();
  expect(screen.queryByRole('navigation',{name:'世界栏目'})).toBeNull();
  await screen.findByText('云山纪.md');
  await user.click(screen.getByRole('button',{name:'完成准备，进入世界'}));
  expect(screen.getByRole('navigation',{name:'世界栏目'}).querySelectorAll('button')).toHaveLength(3);
  expect(await screen.findByRole('button',{name:'开始旅程'})).toBeVisible();
  expect(localStorage.getItem('mojing:prepare-world:w1')).toBeNull();
});

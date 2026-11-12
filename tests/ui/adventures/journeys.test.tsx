import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { JourneyOpening } from '../../../src/features/adventures/JourneyOpening';
import { JourneyManager } from '../../../src/features/adventures/JourneyManager';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => { localStorage.clear(); });

it('推荐与自写共用开局框，修改后提交完整描述；网络重试复用开局请求标识', async () => {
  const starts: Record<string, unknown>[] = [];
  const fetcher = vi.fn(async (input, options) => {
    const path = String(input).split('?')[0];
    if (path.endsWith('/world-start')) return json({materials:[{source_id:'doc:d', title:'河港资料'}],start_points:[]});
    if (path.endsWith('/opening-options')) return json({options:[{title:'漂泊者',description:'我带着旧铜灯抵达河港。'},{title:'修灯人',description:'我在河港修灯。'}]});
    if (path.endsWith('/journey-start')) {
      const body = JSON.parse(options.body); starts.push(body);
      if (starts.length === 1) throw new Error('网络中断');
      return json({id:'new-run',name:body.name,world_id:'w',mode:'live',revision:0});
    }
    throw new Error('Unmocked ' + path);
  });
  vi.stubGlobal('fetch', fetcher);
  const started = vi.fn(async () => {}), user = userEvent.setup();
  render(<JourneyOpening worldId="w" sessionId="session" onStarted={started} onCancel={() => {}} onConfigure={() => {}} />);
  await user.click(await screen.findByRole('button',{name:'根据资料推荐开局'}));
  await user.click(await screen.findByRole('button',{name:/漂泊者/}));
  const editor = screen.getByLabelText('你的开局');
  expect(editor).toHaveValue('我带着旧铜灯抵达河港。');
  await user.type(editor,'灯罩有一道裂纹，灯里还剩半盏油。');
  expect(screen.queryByLabelText('初始位置')).toBeNull();
  expect(screen.queryByLabelText('初始铜币')).toBeNull();
  await user.click(screen.getByRole('button',{name:'以此开局开始旅程'}));
  await screen.findByRole('alert');
  expect(editor).toHaveValue('我带着旧铜灯抵达河港。灯罩有一道裂纹，灯里还剩半盏油。');
  await user.click(screen.getByRole('button',{name:'以此开局开始旅程'}));
  await waitFor(() => expect(started).toHaveBeenCalledTimes(1));
  expect(starts[0].request_id).toBe(starts[1].request_id);
  expect(starts[1].description).toBe('我带着旧铜灯抵达河港。灯罩有一道裂纹，灯里还剩半盏油。');
  expect(starts[1].selected_source_ids).toEqual(['doc:d']);
});

it('当前旅程优先，进入后自动存档在前；手动存档网络重试不重复创建', async () => {
  let saved = false;
  const saves: Record<string, unknown>[] = [];
  const current = {id:'current-run',world_id:'w',name:'河灯',mode:'fake' as const,revision:3};
  const folders = [{id:'other',name:'异乡',active_adventure_id:'other',revision:2,mode:'fake'}, {id:'folder',name:'河灯',active_adventure_id:current.id,revision:3,mode:'fake'}];
  const fetcher = vi.fn(async (input, options) => {
    const path = String(input).split('?')[0];
    if (path.endsWith('/journeys')) return json({items:folders});
    if (path.endsWith('/adventures/current-run')) return json({...current,turns:[],latest_draft:null});
    if (path.endsWith('/journeys/folder/saves') && options?.method === 'POST') {
      const body = JSON.parse(options.body); saves.push(body);
      if (saves.length===1) throw new Error('网络中断'); saved=true;
      return json({id:'manual',name:body.name,revision:3,kind:'manual',created_at:2});
    }
    if (path.endsWith('/journeys/folder/saves')) return json({items:[{id:'auto',name:'自动存档',kind:'auto',revision:3,created_at:1}, ...(saved ? [{id:'manual',name:'过桥之前',kind:'manual',revision:3,created_at:2}] : [])]});
    throw new Error('Unmocked '+path);
  });
  vi.stubGlobal('fetch',fetcher);
  const user=userEvent.setup();
  render(<JourneyManager worldId="w" currentRun={current} onResume={vi.fn(async () => {})} onCreate={() => {}} onBack={() => {}} />);
  const currentRow=await screen.findByRole('button',{name:/河灯.*第 3 回合/});
  expect(currentRow.compareDocumentPosition(screen.getByRole('button',{name:/异乡/})) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await user.click(currentRow);
  await screen.findByText('自动存档');
  await user.type(screen.getByLabelText('新存档名称'),'过桥之前');
  await user.click(screen.getByRole('button',{name:'新建手动存档'}));
  await screen.findByRole('alert');
  await user.click(screen.getByRole('button',{name:'新建手动存档'}));
  await screen.findByText('过桥之前');
  expect(saves[0].request_id).toBe(saves[1].request_id);
  expect(screen.getByText('1/20 个手动存档')).toBeVisible();
});

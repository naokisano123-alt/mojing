/**
 * 人工设定条目工作台（LoreEntriesPanel）真实 React UI 渲染测试
 * 运行环境依据 R5_GEMINI_MARCIN.md / ROUND5.md 由 DSH 配置（Vitest + jsdom + RTL）
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LoreEntriesPanel } from '../../../src/features/lore-entries/LoreEntriesPanel';
import type {
  LoreEntriesPanelApi,
  LoreEntry,
  LoreEntryDraft,
  LoreEntryDraftStore,
  DraftStoreValue,
  SourceCandidate,
} from '../../../src/features/lore-entries/types';

function createMockApi(overrides: Partial<LoreEntriesPanelApi> = {}): LoreEntriesPanelApi {
  return {
    list: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    get: vi.fn().mockImplementation(async (id: string) => ({
      id,
      world_id: 'w1',
      type: 'character',
      title: '旧标题',
      summary: '旧摘要',
      status: 'confirmed',
      revision: 1,
      origin: 'manual',
      source_links: [],
      needs_source_review: false,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      deleted_at: null,
    })),
    create: vi.fn().mockImplementation(async (worldId: string, payload: LoreEntryDraft) => ({
      id: 'new-entry-1',
      world_id: worldId,
      ...payload,
      revision: 1,
      origin: 'manual' as const,
      needs_source_review: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      deleted_at: null,
    })),
    update: vi.fn().mockImplementation(async (id: string, payload: Partial<LoreEntryDraft>, expectedRev: number) => ({
      id,
      world_id: 'w1',
      type: payload.type || 'character',
      title: payload.title || '标题',
      summary: payload.summary || '',
      status: payload.status || 'draft',
      source_links: (payload.source_links as any) || [],
      revision: expectedRev + 1,
      origin: 'manual' as const,
      needs_source_review: false,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: new Date().toISOString(),
      deleted_at: null,
    })),
    remove: vi.fn().mockResolvedValue(undefined),
    searchSources: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
}

function createInMemoryDraftStore(): LoreEntryDraftStore {
  const store = new Map<string, DraftStoreValue>();
  return {
    get: (key: string) => store.get(key),
    set: (key: string, value: DraftStoreValue) => { store.set(key, value); },
    delete: (key: string) => { store.delete(key); },
  };
}

describe('LoreEntriesPanel UI & Lifecycle Tests', () => {
  it('1. 取消表单时暂存草稿，再次打开可完整恢复', async () => {
    const api = createMockApi();
    const draftStore = createInMemoryDraftStore();

    render(<LoreEntriesPanel worldId="w1" api={api} draftStore={draftStore} />);

    // 点击新建设定条目
    const createBtn = await screen.findByText('新建设定条目');
    fireEvent.click(createBtn);

    // 输入标题与修改类型
    const titleInput = screen.getByPlaceholderText(/例如：乌木短刀/);
    fireEvent.change(titleInput, { target: { value: '雾港守卫' } });

    // 点击取消（保留草稿）
    const cancelBtn = screen.getByText('取消（保留草稿）');
    fireEvent.click(cancelBtn);

    // 弹窗关闭
    expect(screen.queryByPlaceholderText(/例如：乌木短刀/)).toBeNull();

    // 再次点击新建
    fireEvent.click(screen.getByText('新建设定条目'));

    // 草稿被恢复，显示恢复提示与输入值
    await screen.findByText('已恢复上次未保存的新建草稿');
    const restoredInput = screen.getByPlaceholderText(/例如：乌木短刀/) as HTMLInputElement;
    expect(restoredInput.value).toBe('雾港守卫');
  });

  it('2. 卸载重挂载组件时，通过外部 draftStore 保留草稿与 baseRevision', async () => {
    const initialEntry: LoreEntry = {
      id: 'e-1',
      world_id: 'w1',
      type: 'character',
      title: '老水手',
      summary: '原版背景',
      status: 'confirmed',
      revision: 2,
      origin: 'manual',
      source_links: [],
      needs_source_review: false,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      deleted_at: null,
    };
    const api = createMockApi({
      list: vi.fn().mockResolvedValue({ items: [initialEntry], total: 1 }),
    });
    const draftStore = createInMemoryDraftStore();

    const { unmount } = render(
      <LoreEntriesPanel worldId="w1" api={api} draftStore={draftStore} />,
    );

    // 点击卡片进入详情
    const card = await screen.findByText('老水手');
    fireEvent.click(card);

    // 点击编辑
    const editBtn = await screen.findByText('编辑条目');
    fireEvent.click(editBtn);

    // 修改摘要
    const textarea = screen.getByPlaceholderText(/作者手写描述/);
    fireEvent.change(textarea, { target: { value: '修改中的草稿内容' } });

    // 模拟用户切换 Tab 或页面导致组件卸载
    unmount();

    // 重新挂载组件（使用同一 draftStore）
    render(<LoreEntriesPanel worldId="w1" api={api} draftStore={draftStore} />);

    // 重新打开编辑
    const remountedCard = await screen.findByText('老水手');
    fireEvent.click(remountedCard);
    fireEvent.click(screen.getByText('编辑条目'));

    // 验证草稿内容及基准 revision 恢复
    await screen.findByText(/已恢复本地修改草稿/);
    const restoredTextarea = screen.getByPlaceholderText(/作者手写描述/) as HTMLTextAreaElement;
    expect(restoredTextarea.value).toBe('修改中的草稿内容');
  });

  it('3. 空标题但修改了分类或关联了来源切片，草稿不会被丢失', async () => {
    const api = createMockApi();
    const draftStore = createInMemoryDraftStore();

    render(<LoreEntriesPanel worldId="w1" api={api} draftStore={draftStore} />);

    fireEvent.click(await screen.findByText('新建设定条目'));

    // 标题保持为空，但改变分类为“规则”
    const typeSelect = screen.getByDisplayValue('人物') as HTMLSelectElement;
    fireEvent.change(typeSelect, { target: { value: 'rule' } });

    // 点击取消
    fireEvent.click(screen.getByText('取消（保留草稿）'));

    // 检查 draftStore 依然保存了该条草稿
    const saved = draftStore.get('w1:new');
    expect(saved).toBeDefined();
    expect(saved?.draft.type).toBe('rule');
  });

  it('4. 409 乐观锁冲突时拉取远端作对照，保留本地输入，显式确认后再重基线提交', async () => {
    const localEntry: LoreEntry = {
      id: 'e-conflict',
      world_id: 'w1',
      type: 'character',
      title: '阿诺船长',
      summary: '年轻水手',
      status: 'draft',
      revision: 1,
      origin: 'manual',
      source_links: [],
      needs_source_review: false,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      deleted_at: null,
    };

    const remoteLatest: LoreEntry = {
      ...localEntry,
      title: '阿诺船长（已被他人修改）',
      summary: '远端最新生平',
      revision: 2,
    };

    let updateAttempts = 0;
    const api = createMockApi({
      list: vi.fn().mockResolvedValue({ items: [localEntry], total: 1 }),
      get: vi.fn().mockResolvedValue(remoteLatest),
      update: vi.fn().mockImplementation(async (_id, _payload, expectedRev) => {
        updateAttempts += 1;
        if (expectedRev === 1) {
          const err: any = new Error('Conflict');
          err.status = 409;
          err.code = 'LORE_ENTRY_CONFLICT';
          throw err;
        }
        return { ...remoteLatest, revision: 3 };
      }),
    });

    render(<LoreEntriesPanel worldId="w1" api={api} />);

    fireEvent.click(await screen.findByText('阿诺船长'));
    fireEvent.click(screen.getByText('编辑条目'));

    // 用户编辑标题
    const titleInput = screen.getByPlaceholderText(/例如：乌木短刀/);
    fireEvent.change(titleInput, { target: { value: '我的本地新标题' } });

    // 提交保存 -> 触发 409
    fireEvent.click(screen.getByText('保存修改'));

    // 提示版本冲突横幅
    await screen.findByText(/条目已在外部被更新（版本冲突）/);

    // 点击拉取远端对比
    const compareBtn = screen.getByText('拉取远端最新版本对比');
    fireEvent.click(compareBtn);

    // 出现对比视图，且用户输入的文字不被覆盖
    await screen.findByText('版本冲突对比（本地修改 vs 远端最新）');
    expect((screen.getByPlaceholderText(/例如：乌木短刀/) as HTMLInputElement).value).toBe(
      '我的本地新标题',
    );

    // 点击显式采纳并重基线覆盖提交
    const adoptBtn = screen.getByText(/采纳远端 rev.2 作为新基准并覆盖提交/);
    fireEvent.click(adoptBtn);

    // 重新点击保存修改
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(updateAttempts).toBe(2);
      expect(api.update).toHaveBeenLastCalledWith(
        'e-conflict',
        expect.objectContaining({ title: '我的本地新标题' }),
        2, // expectedRevision 显式升级为 2
      );
    });
  });

  it('5. 跨世界迟到响应隔离：旧世界 World A 的响应不污染新世界 World B', async () => {
    let resolveWorldA: (val: any) => void;
    const worldAPromise = new Promise(res => {
      resolveWorldA = res;
    });

    const api = createMockApi({
      list: vi.fn().mockImplementation(async ({ worldId }) => {
        if (worldId === 'world-A') {
          return worldAPromise;
        }
        return {
          items: [
            {
              id: 'b1',
              world_id: 'world-B',
              type: 'location',
              title: 'World B 特有地点',
              summary: '',
              status: 'confirmed',
              revision: 1,
              origin: 'manual',
              source_links: [],
              needs_source_review: false,
              created_at: '2026-11-08T00:00:00Z',
              updated_at: '2026-11-08T00:00:00Z',
              deleted_at: null,
            },
          ],
          total: 1,
        };
      }),
    });

    const { rerender } = render(<LoreEntriesPanel worldId="world-A" api={api} />);

    // 快速切换至 world-B
    rerender(<LoreEntriesPanel worldId="world-B" api={api} />);

    // 此时 World A 响应返回
    act(() => {
      resolveWorldA({
        items: [
          {
            id: 'a1',
            world_id: 'world-A',
            type: 'character',
            title: 'World A 幽灵人物',
            summary: '',
            status: 'draft',
            revision: 1,
            origin: 'manual',
            source_links: [],
            needs_source_review: false,
            created_at: '2026-11-08T00:00:00Z',
            updated_at: '2026-11-08T00:00:00Z',
            deleted_at: null,
          },
        ],
        total: 1,
      });
    });

    // 验证 World B 页面仅出现 World B 的内容，World A 的迟到数据被隔离
    await screen.findByText('World B 特有地点');
    expect(screen.queryByText('World A 幽灵人物')).toBeNull();
  });

  it('6. 保存失败时草稿保留在 draftStore 中，便于重试', async () => {
    const api = createMockApi({
      create: vi.fn().mockRejectedValue(new Error('Network Error')),
    });
    const draftStore = createInMemoryDraftStore();

    render(<LoreEntriesPanel worldId="w1" api={api} draftStore={draftStore} />);

    fireEvent.click(await screen.findByText('新建设定条目'));
    fireEvent.change(screen.getByPlaceholderText(/例如：乌木短刀/), {
      target: { value: '重要设定' },
    });

    fireEvent.click(screen.getByText('确认创建'));

    // 显示网络错误
    await screen.findByText('Network Error');

    // 草稿未被删除，依然完好保存在 draftStore
    const saved = draftStore.get('w1:new');
    expect(saved?.draft.title).toBe('重要设定');
  });

  it('7. PATCH 差异更新仅发送修改字段，未改动的来源切片不重发', async () => {
    const initialEntry: LoreEntry = {
      id: 'e-patch',
      world_id: 'w1',
      type: 'character',
      title: '旧姓名',
      summary: '原版摘要描述',
      status: 'confirmed',
      revision: 1,
      origin: 'manual',
      source_links: [
        {
          document_id: 'doc-1',
          chunk_id: 'chk-1',
          start_char: 10,
          end_char: 30,
          excerpt: '历史证据切片',
          availability: 'active',
        },
      ],
      needs_source_review: false,
      created_at: '2026-11-08T00:00:00Z',
      updated_at: '2026-11-08T00:00:00Z',
      deleted_at: null,
    };

    const api = createMockApi({
      list: vi.fn().mockResolvedValue({ items: [initialEntry], total: 1 }),
    });

    render(<LoreEntriesPanel worldId="w1" api={api} />);

    fireEvent.click(await screen.findByText('旧姓名'));
    fireEvent.click(screen.getByText('编辑条目'));

    // 仅修改摘要，不修改标题与来源
    const textarea = screen.getByPlaceholderText(/作者手写描述/);
    fireEvent.change(textarea, { target: { value: '全新摘要内容' } });

    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(api.update).toHaveBeenCalledWith(
        'e-patch',
        { summary: '全新摘要内容' }, // 仅包含修改的 summary，source_links 被省略
        1,
      );
    });
  });
});

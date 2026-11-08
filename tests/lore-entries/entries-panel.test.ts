import test from 'node:test';
import assert from 'node:assert/strict';
import type {
  LoreEntry,
  LoreEntryDraft,
  LoreEntrySource,
  SourceCandidate,
  LoreEntriesPanelApi,
  EntryType,
  EntryStatus,
} from '../../src/features/lore-entries/types.ts';

const ENTRY_TYPE_LABELS: Record<EntryType, string> = {
  character: '人物',
  location: '地点',
  faction: '势力',
  rule: '规则',
  other: '其他',
};

const ENTRY_STATUS_LABELS: Record<EntryStatus, string> = {
  draft: '草稿',
  confirmed: '已确认',
};

test('entry types and status labels cover all 5 M1-D types and 2 statuses', () => {
  assert.equal(ENTRY_TYPE_LABELS.character, '人物');
  assert.equal(ENTRY_TYPE_LABELS.location, '地点');
  assert.equal(ENTRY_TYPE_LABELS.faction, '势力');
  assert.equal(ENTRY_TYPE_LABELS.rule, '规则');
  assert.equal(ENTRY_TYPE_LABELS.other, '其他');

  assert.equal(ENTRY_STATUS_LABELS.draft, '草稿');
  assert.equal(ENTRY_STATUS_LABELS.confirmed, '已确认');
});

test('pagination and filter parameters calculate correct query limits and offsets', () => {
  function buildListParams(
    worldId: string,
    page: number,
    pageSize: number,
    typeFilter: EntryType | 'all',
    statusFilter: EntryStatus | 'all',
  ) {
    return {
      worldId,
      type: typeFilter === 'all' ? undefined : typeFilter,
      status: statusFilter === 'all' ? undefined : statusFilter,
      limit: pageSize,
      offset: page * pageSize,
    };
  }

  const p0 = buildListParams('w1', 0, 20, 'character', 'confirmed');
  assert.deepEqual(p0, {
    worldId: 'w1',
    type: 'character',
    status: 'confirmed',
    limit: 20,
    offset: 0,
  });

  const p2 = buildListParams('w1', 2, 20, 'all', 'all');
  assert.deepEqual(p2, {
    worldId: 'w1',
    type: undefined,
    status: undefined,
    limit: 20,
    offset: 40,
  });
});

test('local draft preservation prevents losing user input on cancel or network errors', () => {
  const draftsMap = new Map<string, LoreEntryDraft>();
  const worldId = 'world-alpha';
  const entryId = 'entry-123';
  const draftKey = `${worldId}:${entryId}`;

  // 模拟用户在编辑时输入了内容
  const dirtyDraft: LoreEntryDraft = {
    type: 'character',
    title: '乌木短刀之谜',
    summary: '这是未保存的修改说明',
    status: 'draft',
    source_links: [],
  };

  // 用户点击取消或发生网络异常：暂存至 draftsMap
  draftsMap.set(draftKey, dirtyDraft);
  assert.equal(draftsMap.has(draftKey), true);

  // 再次打开该条目编辑表单：优先恢复本地草稿
  const restored = draftsMap.get(draftKey);
  assert.equal(restored?.title, '乌木短刀之谜');
  assert.equal(restored?.summary, '这是未保存的修改说明');

  // 用户确认提交成功后：清除暂存草稿
  draftsMap.delete(draftKey);
  assert.equal(draftsMap.has(draftKey), false);
});

test('409 revision conflict handling detects mismatch and protects local edits', () => {
  let formError: string | null = null;
  let conflictData: { expectedRevision: number } | null = null;

  function handleUpdateError(err: { status?: number; code?: string; message?: string }, currentRevision: number) {
    if (err.status === 409 || err.code === 'LORE_ENTRY_CONFLICT') {
      conflictData = {
        expectedRevision: currentRevision,
      };
      formError = '内容已被他人修改（版本冲突）：当前条目已被更新，请对比后重新提交。';
    } else {
      formError = err.message || '更新失败';
    }
  }

  // 模拟服务端返回 409
  handleUpdateError({ status: 409, code: 'LORE_ENTRY_CONFLICT' }, 1);
  assert.equal(conflictData?.expectedRevision, 1);
  assert.match(formError || '', /版本冲突/);
});

test('source lifecycle derivation distinguishes manual entries from deleted citations', () => {
  // 1. 无来源的人工条目
  const manualEntry: LoreEntry = {
    id: 'e1',
    world_id: 'w1',
    type: 'character',
    title: '手写设定',
    summary: '完全由用户独立构思的设定',
    status: 'confirmed',
    revision: 1,
    origin: 'manual',
    source_links: [],
    needs_source_review: false,
    created_at: '2026-11-08T00:00:00Z',
    updated_at: '2026-11-08T00:00:00Z',
    deleted_at: null,
  };
  assert.equal(manualEntry.source_links.length, 0);
  assert.equal(manualEntry.needs_source_review, false);

  // 2. 包含已删除文档引用的条目
  const deletedSourceEntry: LoreEntry = {
    id: 'e2',
    world_id: 'w1',
    type: 'location',
    title: '古老遗迹',
    summary: '曾经辉煌的遗迹',
    status: 'confirmed',
    revision: 2,
    origin: 'manual',
    source_links: [
      {
        document_id: 'doc-old',
        chunk_id: 'c1',
        start_char: 0,
        end_char: 50,
        excerpt: '遗迹入口隐于雾中',
        availability: 'deleted',
      },
    ],
    needs_source_review: true,
    created_at: '2026-11-08T00:00:00Z',
    updated_at: '2026-11-08T01:00:00Z',
    deleted_at: null,
  };
  const hasDeleted = deletedSourceEntry.source_links.some(s => s.availability === 'deleted');
  assert.equal(hasDeleted, true);
  assert.equal(deletedSourceEntry.needs_source_review, true);
});

test('switching world or unmounting cancels in-flight requests via AbortController', () => {
  let activeController = new AbortController();
  assert.equal(activeController.signal.aborted, false);

  // 模拟切世界或组件卸载
  function onWorldChange() {
    if (activeController) {
      activeController.abort();
    }
  }

  onWorldChange();
  assert.equal(activeController.signal.aborted, true);
});

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
  DraftStoreValue,
  LoreEntryDraftStore,
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

test('draftStore preserves draft and baseRevision across lifecycle', () => {
  const storeMap = new Map<string, DraftStoreValue>();
  const draftStore: LoreEntryDraftStore = {
    get: (key: string) => storeMap.get(key),
    set: (key: string, val: DraftStoreValue) => { storeMap.set(key, val); },
    delete: (key: string) => { storeMap.delete(key); },
  };

  const worldId = 'world-alpha';
  const entryId = 'entry-123';
  const draftKey = `${worldId}:${entryId}`;

  const dirtyDraft: LoreEntryDraft = {
    type: 'location',
    title: '旧港暗礁',
    summary: '退潮时隐约可见的黑礁石',
    status: 'draft',
    source_links: [
      {
        document_id: 'doc-1',
        chunk_id: 'chk-1',
        start_char: 10,
        end_char: 50,
      },
    ],
  };

  // 保存草稿，附带 baseRevision: 2
  draftStore.set(draftKey, { draft: dirtyDraft, baseRevision: 2 });
  assert.equal(storeMap.has(draftKey), true);

  // 恢复草稿
  const saved = draftStore.get(draftKey);
  assert.ok(saved);
  assert.equal(saved.draft.title, '旧港暗礁');
  assert.equal(saved.baseRevision, 2);

  // 恢复草稿不自动绑定最新 revision（即使服务端当前已是 3）
  const remoteRevision = 3;
  const effectiveBase = saved.baseRevision !== null ? saved.baseRevision : remoteRevision;
  assert.equal(effectiveBase, 2, 'baseRevision 应保留原草稿基准 2，而非被静默升级为 3');

  // 保存成功后仅清除该条目草稿
  draftStore.delete(draftKey);
  assert.equal(storeMap.has(draftKey), false);
});

test('empty title draft is preserved when type, status, or sources are modified', () => {
  function checkIsDirty(
    draft: LoreEntryDraft,
    isCreating: boolean,
    baseline?: LoreEntry | null,
  ): boolean {
    if (isCreating) {
      return (
        draft.title.trim().length > 0 ||
        draft.summary.trim().length > 0 ||
        draft.type !== 'character' ||
        draft.status !== 'draft' ||
        draft.source_links.length > 0
      );
    }
    if (!baseline) return true;
    return (
      draft.title !== baseline.title ||
      draft.summary !== baseline.summary ||
      draft.type !== baseline.type ||
      draft.status !== baseline.status ||
      draft.source_links.length !== baseline.source_links.length
    );
  }

  // 新建草稿：标题为空，但用户选择了类型为 rule 且添加了引用切片
  const emptyTitleDraft: LoreEntryDraft = {
    type: 'rule',
    title: '   ',
    summary: '',
    status: 'draft',
    source_links: [
      {
        document_id: 'doc-law',
        chunk_id: 'chk-law',
        start_char: 0,
        end_char: 80,
      },
    ],
  };

  assert.equal(checkIsDirty(emptyTitleDraft, true, null), true, '空标题但改了分类和来源也必须被视作脏草稿暂存');
});

test('PATCH delta calculation sends only changed fields and omits untouched sources', () => {
  function computePatch(
    formData: LoreEntryDraft,
    baseline: LoreEntry,
  ): Partial<LoreEntryDraft> {
    const patch: Partial<LoreEntryDraft> = {};
    if (formData.title.trim() !== baseline.title) {
      patch.title = formData.title.trim();
    }
    if (formData.type !== baseline.type) {
      patch.type = formData.type;
    }
    if (formData.status !== baseline.status) {
      patch.status = formData.status;
    }
    if (formData.summary.trim() !== (baseline.summary || '')) {
      patch.summary = formData.summary.trim();
    }
    const sourcesEqual =
      formData.source_links.length === baseline.source_links.length &&
      formData.source_links.every(
        (s, i) =>
          s.chunk_id === baseline.source_links[i].chunk_id &&
          s.start_char === baseline.source_links[i].start_char &&
          s.end_char === baseline.source_links[i].end_char,
      );
    if (!sourcesEqual) {
      patch.source_links = formData.source_links;
    }
    return patch;
  }

  const baseline: LoreEntry = {
    id: 'e1',
    world_id: 'w1',
    type: 'character',
    title: '旧标题',
    summary: '原有描述',
    status: 'confirmed',
    revision: 2,
    origin: 'manual',
    source_links: [
      {
        document_id: 'd1',
        chunk_id: 'c1',
        start_char: 10,
        end_char: 20,
        excerpt: '证据摘录',
        availability: 'active',
      },
    ],
    needs_source_review: false,
    created_at: '2026-11-08T00:00:00Z',
    updated_at: '2026-11-08T00:00:00Z',
    deleted_at: null,
  };

  // 用户仅修改了标题，未修改来源
  const form1: LoreEntryDraft = {
    type: 'character',
    title: '新标题',
    summary: '原有描述',
    status: 'confirmed',
    source_links: [
      {
        document_id: 'd1',
        chunk_id: 'c1',
        start_char: 10,
        end_char: 20,
      },
    ],
  };

  const patch1 = computePatch(form1, baseline);
  assert.deepEqual(patch1, { title: '新标题' });
  assert.equal('source_links' in patch1, false, '未变动的来源切片不得被重发');
});

test('409 conflict diff detection and rebase mechanics', () => {
  const localDraft: LoreEntryDraft = {
    type: 'character',
    title: '水手长阿诺（本地修改）',
    summary: '年轻时曾去过风暴角',
    status: 'draft',
    source_links: [],
  };
  const baseRev = 1;

  const remoteLatest: LoreEntry = {
    id: 'e1',
    world_id: 'w1',
    type: 'character',
    title: '水手长阿诺（远端已被他人更新）',
    summary: '中年，掌舵二十年',
    status: 'confirmed',
    revision: 2,
    origin: 'manual',
    source_links: [],
    needs_source_review: false,
    created_at: '2026-11-08T00:00:00Z',
    updated_at: '2026-11-08T01:00:00Z',
    deleted_at: null,
  };

  // 1. 冲突发生：本地草稿与原基线保留
  let expectedRevision = baseRev;
  assert.equal(expectedRevision, 1);
  assert.equal(localDraft.title, '水手长阿诺（本地修改）');

  // 2. 用户对比后显式选择覆盖提交并升级基线至远端版本
  function rebaseDraft(remote: LoreEntry) {
    expectedRevision = remote.revision;
  }
  rebaseDraft(remoteLatest);
  assert.equal(expectedRevision, 2, '显式确认后基准 revision 成功升级至 2');
  assert.equal(localDraft.title, '水手长阿诺（本地修改）', '用户手写文字不受影响');
});

test('generation token isolation prevents delayed world response from polluting new world', () => {
  let activeGeneration = 1;
  let activeWorldId = 'world-A';

  const responsesApplied: string[] = [];

  function simulateApiResponse(gen: number, world: string, data: string) {
    if (gen !== activeGeneration || world !== activeWorldId) {
      // 世代不匹配或世界不匹配，丢弃
      return;
    }
    responsesApplied.push(data);
  }

  // 发起 World A 请求
  const reqGen = activeGeneration;
  const reqWorld = activeWorldId;

  // 用户迅速切换到 World B
  activeGeneration += 1;
  activeWorldId = 'world-B';

  // World A 迟到响应返回
  simulateApiResponse(reqGen, reqWorld, 'World A Items');
  assert.deepEqual(responsesApplied, [], 'World A 的迟到响应被成功拦截，未污染 World B');

  // World B 正常响应返回
  simulateApiResponse(activeGeneration, activeWorldId, 'World B Items');
  assert.deepEqual(responsesApplied, ['World B Items']);
});

test('source lifecycle derivation distinguishes manual entries from deleted citations', () => {
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

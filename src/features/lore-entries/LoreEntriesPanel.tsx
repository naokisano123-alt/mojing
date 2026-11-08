import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  FileText,
  Plus,
  Search,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  X,
  ChevronLeft,
  ChevronRight,
  Edit2,
  Trash2,
  ExternalLink,
  RefreshCw,
  Info,
  GitCompare,
  ArrowRight,
} from 'lucide-react';
import type {
  EntryStatus,
  EntryType,
  LoreEntriesPanelProps,
  LoreEntry,
  LoreEntryDraft,
  LoreEntryDraftStore,
  DraftStoreValue,
  SourceCandidate,
} from './types';
import { ENTRY_STATUS_LABELS, ENTRY_TYPE_LABELS } from './types';
import './lore-entries.css';

const DEFAULT_PAGE_SIZE = 20;

const EMPTY_DRAFT: LoreEntryDraft = {
  type: 'character',
  title: '',
  summary: '',
  status: 'draft',
  source_links: [],
};

function areSourceLinksEqual(
  a: Array<{ chunk_id: string; start_char: number; end_char: number }>,
  b: Array<{ chunk_id: string; start_char: number; end_char: number }>,
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].chunk_id !== b[i].chunk_id ||
      a[i].start_char !== b[i].start_char ||
      a[i].end_char !== b[i].end_char
    ) {
      return false;
    }
  }
  return true;
}

export function LoreEntriesPanel({
  worldId,
  api,
  draftStore,
  onOpenSource,
  initialEntryId,
  className = '',
}: LoreEntriesPanelProps) {
  // 列表与分页状态
  const [entries, setEntries] = useState<LoreEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedType, setSelectedType] = useState<EntryType | 'all'>('all');
  const [selectedStatus, setSelectedStatus] = useState<EntryStatus | 'all'>('all');
  const [page, setPage] = useState(0);

  // 模态弹窗与工作态
  const [viewingEntry, setViewingEntry] = useState<LoreEntry | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [deletingEntry, setDeletingEntry] = useState<LoreEntry | null>(null);

  // 表单状态与基准版本
  const [formData, setFormData] = useState<LoreEntryDraft>(EMPTY_DRAFT);
  const [baseRevision, setBaseRevision] = useState<number | null>(null);
  const [baselineEntry, setBaselineEntry] = useState<LoreEntry | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 草稿防丢与 409 冲突对比
  const [localDraftNotice, setLocalDraftNotice] = useState<string | null>(null);
  const [conflictData, setConflictData] = useState<{
    expectedRevision: number;
    currentEntry?: LoreEntry;
  } | null>(null);
  const [conflictRemoteEntry, setConflictRemoteEntry] = useState<LoreEntry | null>(null);
  const [fetchingConflictRemote, setFetchingConflictRemote] = useState(false);

  // 来源检索状态
  const [showSourceSearch, setShowSourceSearch] = useState(false);
  const [sourceQuery, setSourceQuery] = useState('');
  const [sourceCandidates, setSourceCandidates] = useState<SourceCandidate[]>([]);
  const [searchingSources, setSearchingSources] = useState(false);
  const [sourceSearchError, setSourceSearchError] = useState<string | null>(null);

  // 内部默认草稿存储区（当外部未注入 draftStore 时使用）
  const fallbackDraftStoreRef = useRef<Map<string, DraftStoreValue>>(new Map());

  // 世代控制与生命周期取消
  const generationRef = useRef<number>(0);
  const worldIdRef = useRef<string>(worldId);
  const listAbortRef = useRef<AbortController | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const submittingRef = useRef<boolean>(false);
  const isMountedRef = useRef<boolean>(true);

  // 统一草稿存取辅助函数
  const getStoredDraft = useCallback(
    (key: string): DraftStoreValue | undefined => {
      return draftStore ? draftStore.get(key) : fallbackDraftStoreRef.current.get(key);
    },
    [draftStore],
  );

  const setStoredDraft = useCallback(
    (key: string, value: DraftStoreValue): void => {
      if (draftStore) {
        draftStore.set(key, value);
      } else {
        fallbackDraftStoreRef.current.set(key, value);
      }
    },
    [draftStore],
  );

  const deleteStoredDraft = useCallback(
    (key: string): void => {
      if (draftStore) {
        draftStore.delete(key);
      } else {
        fallbackDraftStoreRef.current.delete(key);
      }
    },
    [draftStore],
  );

  // 计算当前草稿 key
  const getCurrentDraftKey = useCallback((): string | null => {
    if (isCreating) return `${worldId}:new`;
    if (isEditing && viewingEntry) return `${worldId}:${viewingEntry.id}`;
    return null;
  }, [worldId, isCreating, isEditing, viewingEntry]);

  // 切换世界时，重置所有视图状态、递增世代，并取消在途请求，彻底防止跨世界串扰
  useEffect(() => {
    isMountedRef.current = true;
    generationRef.current += 1;
    worldIdRef.current = worldId;

    // 清理在途请求
    if (listAbortRef.current) {
      listAbortRef.current.abort();
      listAbortRef.current = null;
    }
    if (searchAbortRef.current) {
      searchAbortRef.current.abort();
      searchAbortRef.current = null;
    }

    // 重置所有内部工作态
    setPage(0);
    setViewingEntry(null);
    setIsEditing(false);
    setIsCreating(false);
    setDeletingEntry(null);
    setFormData(EMPTY_DRAFT);
    setBaseRevision(null);
    setBaselineEntry(null);
    setFormError(null);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setLocalDraftNotice(null);
    setShowSourceSearch(false);
    setSourceCandidates([]);

    // 重新拉取新世界条目列表
    loadEntries(0, selectedType, selectedStatus);

    return () => {
      isMountedRef.current = false;
      if (listAbortRef.current) listAbortRef.current.abort();
      if (searchAbortRef.current) searchAbortRef.current.abort();
    };
  }, [worldId]);

  // Sources can point to an entry outside the current list page.
  useEffect(() => {
    if (!initialEntryId) return;
    const controller = new AbortController();
    void api.get(initialEntryId, controller.signal).then(entry => {
      if (controller.signal.aborted) return;
      if (entry.world_id !== worldId) throw new Error('条目不属于当前世界');
      setViewingEntry(entry);
    }).catch(error => {
      if (!controller.signal.aborted) setListError(error?.message || '引用条目已不可用');
    });
    return () => controller.abort();
  }, [worldId, initialEntryId]);

  // 筛选器变化时重新拉取
  function handleFilterChange(newType: EntryType | 'all', newStatus: EntryStatus | 'all') {
    setSelectedType(newType);
    setSelectedStatus(newStatus);
    setPage(0);
    loadEntries(0, newType, newStatus);
  }

  // 加载条目列表（带世代判断）
  async function loadEntries(
    targetPage: number,
    typeFilter: EntryType | 'all',
    statusFilter: EntryStatus | 'all',
  ) {
    if (listAbortRef.current) {
      listAbortRef.current.abort();
    }
    const controller = new AbortController();
    listAbortRef.current = controller;

    const currentGen = generationRef.current;
    const currentWorld = worldId;

    setLoading(true);
    setListError(null);

    try {
      const res = await api.list({
        worldId,
        type: typeFilter === 'all' ? undefined : typeFilter,
        status: statusFilter === 'all' ? undefined : statusFilter,
        limit: DEFAULT_PAGE_SIZE,
        offset: targetPage * DEFAULT_PAGE_SIZE,
        signal: controller.signal,
      });

      // 世代与挂载检查：旧世界的迟到响应不能污染当前状态
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }

      setEntries(res.items || []);
      setTotal(res.total || 0);
      setPage(targetPage);
    } catch (err: any) {
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld ||
        err?.name === 'AbortError' ||
        err?.code === 'ABORT_ERROR'
      ) {
        return;
      }
      setListError(err?.message || '获取设定条目列表失败，请重试。');
    } finally {
      if (
        isMountedRef.current &&
        generationRef.current === currentGen &&
        worldIdRef.current === currentWorld
      ) {
        setLoading(false);
      }
    }
  }

  // 打开新建表单
  function handleOpenCreate() {
    setFormError(null);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setLocalDraftNotice(null);
    setShowSourceSearch(false);
    setSourceCandidates([]);
    setBaselineEntry(null);

    const draftKey = `${worldId}:new`;
    const saved = getStoredDraft(draftKey);
    if (saved) {
      setFormData(saved.draft);
      setBaseRevision(null);
      setLocalDraftNotice('已恢复上次未保存的新建草稿');
    } else {
      setFormData(EMPTY_DRAFT);
      setBaseRevision(null);
    }

    setIsCreating(true);
    setIsEditing(false);
    setViewingEntry(null);
  }

  // 打开编辑表单
  function handleOpenEdit(entry: LoreEntry) {
    setFormError(null);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setLocalDraftNotice(null);
    setShowSourceSearch(false);
    setSourceCandidates([]);
    setBaselineEntry(entry);

    const draftKey = `${worldId}:${entry.id}`;
    const saved = getStoredDraft(draftKey);

    if (saved) {
      setFormData(saved.draft);
      // 恢复草稿不自动绑定最新 revision，保留原草稿的 baseRevision
      const effectiveBase = saved.baseRevision !== null ? saved.baseRevision : entry.revision;
      setBaseRevision(effectiveBase);
      if (effectiveBase !== entry.revision) {
        setLocalDraftNotice(
          `已恢复本地修改草稿（原基准 rev.${effectiveBase}，远端当前为 rev.${entry.revision}）`,
        );
      } else {
        setLocalDraftNotice(`已恢复本地修改草稿（基于 rev.${effectiveBase}）`);
      }
    } else {
      setFormData({
        type: entry.type,
        title: entry.title,
        summary: entry.summary,
        status: entry.status,
        source_links: entry.source_links.map(s => ({
          document_id: s.document_id,
          chunk_id: s.chunk_id,
          start_char: s.start_char,
          end_char: s.end_char,
          excerpt: s.excerpt,
          availability: s.availability,
          document_filename: s.document_filename,
        })),
      });
      setBaseRevision(entry.revision);
    }

    setIsEditing(true);
    setIsCreating(false);
  }

  // 内部表单字段修改处理函数：每次编辑及时保存到 draftStore
  function handleFormChange(updated: LoreEntryDraft) {
    setFormData(updated);

    const key = getCurrentDraftKey();
    if (!key) return;

    // 脏检查：空标题但修改了分类、状态、摘要或来源引用，均视为脏草稿，不丢失
    let isDirty = false;
    if (isCreating) {
      isDirty =
        updated.title.trim().length > 0 ||
        updated.summary.trim().length > 0 ||
        updated.type !== 'character' ||
        updated.status !== 'draft' ||
        updated.source_links.length > 0;
    } else if (isEditing && baselineEntry) {
      isDirty =
        updated.title !== baselineEntry.title ||
        updated.summary !== baselineEntry.summary ||
        updated.type !== baselineEntry.type ||
        updated.status !== baselineEntry.status ||
        !areSourceLinksEqual(updated.source_links, baselineEntry.source_links);
    }

    if (isDirty) {
      setStoredDraft(key, { draft: updated, baseRevision });
    }
  }

  // 取消表单：草稿已在编辑过程中及时暂存，无需重复设置
  function handleCancelForm() {
    setIsCreating(false);
    setIsEditing(false);
    setFormError(null);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setShowSourceSearch(false);
  }

  // 放弃暂存草稿：显式从 draftStore 中移除
  function handleDiscardDraft() {
    const key = getCurrentDraftKey();
    if (key) {
      deleteStoredDraft(key);
    }

    if (viewingEntry && isEditing && baselineEntry) {
      setFormData({
        type: baselineEntry.type,
        title: baselineEntry.title,
        summary: baselineEntry.summary,
        status: baselineEntry.status,
        source_links: baselineEntry.source_links.map(s => ({
          document_id: s.document_id,
          chunk_id: s.chunk_id,
          start_char: s.start_char,
          end_char: s.end_char,
          excerpt: s.excerpt,
          availability: s.availability,
          document_filename: s.document_filename,
        })),
      });
      setBaseRevision(baselineEntry.revision);
    } else {
      setFormData(EMPTY_DRAFT);
      setBaseRevision(null);
    }
    setLocalDraftNotice(null);
    setConflictData(null);
    setConflictRemoteEntry(null);
  }

  // 检索切片来源
  async function handleSearchSources() {
    if (!sourceQuery.trim() || searchingSources) return;

    if (searchAbortRef.current) {
      searchAbortRef.current.abort();
    }
    const controller = new AbortController();
    searchAbortRef.current = controller;

    const currentGen = generationRef.current;
    const currentWorld = worldId;

    setSearchingSources(true);
    setSourceSearchError(null);

    try {
      const results = await api.searchSources({
        worldId,
        query: sourceQuery.trim(),
        signal: controller.signal,
      });

      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }
      setSourceCandidates(results || []);
    } catch (err: any) {
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld ||
        err?.name === 'AbortError' ||
        err?.code === 'ABORT_ERROR'
      ) {
        return;
      }
      setSourceSearchError(err?.message || '搜索资料切片失败');
    } finally {
      if (
        isMountedRef.current &&
        generationRef.current === currentGen &&
        worldIdRef.current === currentWorld
      ) {
        setSearchingSources(false);
      }
    }
  }

  // 选取切片作为来源引用
  function handleSelectSource(candidate: SourceCandidate) {
    const alreadyExists = formData.source_links.some(
      s => s.chunk_id === candidate.chunk_id && s.start_char === candidate.start_char,
    );
    if (alreadyExists) return;

    const newLink = {
      document_id: candidate.document_id,
      chunk_id: candidate.chunk_id,
      start_char: candidate.start_char,
      end_char: candidate.end_char,
      excerpt: candidate.snippet,
      availability: 'active' as const,
      document_filename: candidate.document_filename || candidate.filename,
    };

    const nextForm = {
      ...formData,
      source_links: [...formData.source_links, newLink],
    };
    handleFormChange(nextForm);
  }

  // 移除引用
  function handleRemoveSource(index: number) {
    const nextForm = {
      ...formData,
      source_links: formData.source_links.filter((_, i) => i !== index),
    };
    handleFormChange(nextForm);
  }

  // 保存（新建或差异更新 PATCH）
  async function handleSaveEntry() {
    if (submittingRef.current) return;

    const cleanTitle = formData.title.trim();
    if (!cleanTitle) {
      setFormError('条目标题不能为空。');
      return;
    }
    if (cleanTitle.length > 80) {
      setFormError('条目标题不能超过 80 个字符。');
      return;
    }
    if (formData.summary.trim().length > 500) {
      setFormError('条目摘要不能超过 500 个字符。');
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    setFormError(null);
    setConflictData(null);

    const currentGen = generationRef.current;
    const currentWorld = worldId;
    const currentDraftKey = getCurrentDraftKey();

    try {
      if (isCreating) {
        const created = await api.create(worldId, {
          ...formData,
          title: cleanTitle,
          summary: formData.summary.trim(),
        });

        if (
          !isMountedRef.current ||
          generationRef.current !== currentGen ||
          worldIdRef.current !== currentWorld
        ) {
          return;
        }

        // 保存成功，仅清空对应条目的草稿
        if (currentDraftKey) {
          deleteStoredDraft(currentDraftKey);
        }
        setIsCreating(false);
        setViewingEntry(created);
        loadEntries(page, selectedType, selectedStatus);
      } else if (isEditing && viewingEntry) {
        // 计算差异 PATCH Payload，仅发送变更字段；来源未变不重发
        const patchPayload: Partial<LoreEntryDraft> = {};
        if (cleanTitle !== (baselineEntry?.title || '')) {
          patchPayload.title = cleanTitle;
        }
        if (formData.type !== baselineEntry?.type) {
          patchPayload.type = formData.type;
        }
        if (formData.status !== baselineEntry?.status) {
          patchPayload.status = formData.status;
        }
        if (formData.summary.trim() !== (baselineEntry?.summary || '')) {
          patchPayload.summary = formData.summary.trim();
        }

        const sourcesChanged = !areSourceLinksEqual(
          formData.source_links,
          baselineEntry?.source_links || [],
        );
        if (sourcesChanged) {
          patchPayload.source_links = formData.source_links.map(s => ({
            document_id: s.document_id,
            chunk_id: s.chunk_id,
            start_char: s.start_char,
            end_char: s.end_char,
            excerpt: s.excerpt,
          }));
        }

        // 若完全未做任何修改，直接关闭
        if (Object.keys(patchPayload).length === 0) {
          if (currentDraftKey) {
            deleteStoredDraft(currentDraftKey);
          }
          setIsEditing(false);
          return;
        }

        const expectedRevision = baseRevision ?? viewingEntry.revision;
        const updated = await api.update(viewingEntry.id, patchPayload, expectedRevision);

        if (
          !isMountedRef.current ||
          generationRef.current !== currentGen ||
          worldIdRef.current !== currentWorld
        ) {
          return;
        }

        // 保存成功，仅清空本条目草稿
        if (currentDraftKey) {
          deleteStoredDraft(currentDraftKey);
        }
        setIsEditing(false);
        setViewingEntry(updated);
        loadEntries(page, selectedType, selectedStatus);
      }
    } catch (err: any) {
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }

      if (err?.status === 409 || err?.code === 'LORE_ENTRY_CONFLICT') {
        // 捕获 409 乐观锁冲突：用户文字与原 baseRevision 均保留
        const conflictBase = baseRevision ?? viewingEntry?.revision ?? 1;
        setConflictData({
          expectedRevision: conflictBase,
        });
        setFormError('内容已被他人修改（版本冲突）：当前条目已被更新，请对比后重新提交。');
      } else {
        setFormError(err?.friendlyMessage || err?.message || '保存条目失败，请检查网络后重试。');
      }
    } finally {
      submittingRef.current = false;
      if (
        isMountedRef.current &&
        generationRef.current === currentGen &&
        worldIdRef.current === currentWorld
      ) {
        setSubmitting(false);
      }
    }
  }

  // 冲突解决：拉取远端最新版本仅作对照，用户文字和原 baseRevision 保持不动
  async function handleFetchLatestConflict() {
    if (!viewingEntry || fetchingConflictRemote) return;

    setFetchingConflictRemote(true);
    const currentGen = generationRef.current;
    const currentWorld = worldId;

    try {
      const latest = await api.get(viewingEntry.id);
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }

      setConflictRemoteEntry(latest);
      setConflictData(prev => (prev ? { ...prev, currentEntry: latest } : null));
    } catch (err: any) {
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }
      setFormError('获取远端最新条目失败：' + (err?.message || '未知错误'));
    } finally {
      if (
        isMountedRef.current &&
        generationRef.current === currentGen &&
        worldIdRef.current === currentWorld
      ) {
        setFetchingConflictRemote(false);
      }
    }
  }

  // 冲突解决显式确认：用户确认以当前草稿覆盖，并将 revision 基准升级到远端最新版本
  function handleAdoptRemoteRevisionAndRebase() {
    if (!conflictRemoteEntry) return;

    // 显式将基线更新为远端最新 revision
    const newBaseRev = conflictRemoteEntry.revision;
    setBaseRevision(newBaseRev);
    setBaselineEntry(conflictRemoteEntry);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setFormError(null);

    // 及时同步更新草稿中的 baseRevision
    const key = getCurrentDraftKey();
    if (key) {
      setStoredDraft(key, { draft: formData, baseRevision: newBaseRev });
    }
    setLocalDraftNotice(`已确认采纳 rev.${newBaseRev} 作为新提交基准；可重新点击“保存修改”。`);
  }

  // 冲突解决显式放弃：直接采纳远端最新版本，放弃本地修改
  function handleDiscardAndUseRemote() {
    if (!conflictRemoteEntry) return;

    const key = getCurrentDraftKey();
    if (key) {
      deleteStoredDraft(key);
    }

    setFormData({
      type: conflictRemoteEntry.type,
      title: conflictRemoteEntry.title,
      summary: conflictRemoteEntry.summary,
      status: conflictRemoteEntry.status,
      source_links: conflictRemoteEntry.source_links.map(s => ({
        document_id: s.document_id,
        chunk_id: s.chunk_id,
        start_char: s.start_char,
        end_char: s.end_char,
        excerpt: s.excerpt,
        availability: s.availability,
        document_filename: s.document_filename,
      })),
    });
    setBaseRevision(conflictRemoteEntry.revision);
    setBaselineEntry(conflictRemoteEntry);
    setViewingEntry(conflictRemoteEntry);
    setConflictData(null);
    setConflictRemoteEntry(null);
    setFormError(null);
    setLocalDraftNotice(null);
  }

  // 确认软删除
  async function handleConfirmDelete() {
    if (!deletingEntry || submittingRef.current) return;

    submittingRef.current = true;
    setSubmitting(true);
    const currentGen = generationRef.current;
    const currentWorld = worldId;

    try {
      await api.remove(deletingEntry.id, deletingEntry.revision);

      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }

      // 清除本地草稿
      deleteStoredDraft(`${worldId}:${deletingEntry.id}`);
      setDeletingEntry(null);
      setViewingEntry(null);
      loadEntries(page, selectedType, selectedStatus);
    } catch (err: any) {
      if (
        !isMountedRef.current ||
        generationRef.current !== currentGen ||
        worldIdRef.current !== currentWorld
      ) {
        return;
      }

      if (err?.status === 409 || err?.code === 'LORE_ENTRY_CONFLICT') {
        setFormError('删除失败：该条目已被其他操作修改，请刷新列表后再操作。');
      } else {
        setFormError(err?.message || '删除条目失败。');
      }
    } finally {
      submittingRef.current = false;
      if (
        isMountedRef.current &&
        generationRef.current === currentGen &&
        worldIdRef.current === currentWorld
      ) {
        setSubmitting(false);
      }
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));

  return (
    <div className={`lore-entries-panel ${className}`}>
      {/* 顶部操作与筛选 */}
      <div className="lore-entries-header">
        <div className="lore-entries-top-row">
          <div className="lore-entries-title-block">
            <h3>世界观设定条目（人工录入）</h3>
            <p>维护人物、地点、势力、规则等设定；支持同名独立条目与真实文档引用。</p>
          </div>
          <button
            type="button"
            className="primary"
            style={{ fontSize: '12px', minHeight: '36px', padding: '6px 14px' }}
            onClick={handleOpenCreate}
          >
            <Plus size={15} /> 新建设定条目
          </button>
        </div>

        {/* 筛选器 */}
        <div className="lore-entries-filters">
          <div className="lore-entries-filter-group">
            <span className="lore-entries-filter-label">分类:</span>
            <button
              type="button"
              className={`lore-entries-filter-pill ${selectedType === 'all' ? 'active' : ''}`}
              onClick={() => handleFilterChange('all', selectedStatus)}
            >
              全部
            </button>
            {(Object.keys(ENTRY_TYPE_LABELS) as EntryType[]).map(t => (
              <button
                key={t}
                type="button"
                className={`lore-entries-filter-pill ${selectedType === t ? 'active' : ''}`}
                onClick={() => handleFilterChange(t, selectedStatus)}
              >
                {ENTRY_TYPE_LABELS[t]}
              </button>
            ))}
          </div>

          <div className="lore-entries-filter-group">
            <span className="lore-entries-filter-label">状态:</span>
            <button
              type="button"
              className={`lore-entries-filter-pill ${selectedStatus === 'all' ? 'active' : ''}`}
              onClick={() => handleFilterChange(selectedType, 'all')}
            >
              全部
            </button>
            <button
              type="button"
              className={`lore-entries-filter-pill ${selectedStatus === 'draft' ? 'active' : ''}`}
              onClick={() => handleFilterChange(selectedType, 'draft')}
            >
              草稿 (Draft)
            </button>
            <button
              type="button"
              className={`lore-entries-filter-pill ${selectedStatus === 'confirmed' ? 'active' : ''}`}
              onClick={() => handleFilterChange(selectedType, 'confirmed')}
            >
              已确认 (Confirmed)
            </button>
          </div>
        </div>
      </div>

      {/* 列表错误横幅 */}
      {listError && (
        <div className="lore-entries-banner error" role="alert">
          <AlertCircle size={16} />
          <span style={{ flex: 1 }}>{listError}</span>
          <button
            type="button"
            className="text-button"
            style={{ color: '#ba6452' }}
            onClick={() => loadEntries(page, selectedType, selectedStatus)}
          >
            重试
          </button>
        </div>
      )}

      {/* 列表加载状态 */}
      {loading && entries.length === 0 && (
        <div className="lore-entries-empty">
          <RefreshCw size={24} className="spin" style={{ color: 'var(--green)' }} />
          <p>正在读取设定条目...</p>
        </div>
      )}

      {/* 空列表 */}
      {!loading && entries.length === 0 && (
        <div className="lore-entries-empty">
          <FileText size={32} />
          <h4>暂无符合条件的设定条目</h4>
          <p>当前世界下暂无条目，或没有符合当前分类与状态筛选的内容。点击上方“新建设定条目”开始录入。</p>
        </div>
      )}

      {/* 条目卡片列表 */}
      <div className="lore-entries-list">
        {entries.map(entry => {
          const hasDeletedSource = entry.source_links.some(s => s.availability === 'deleted');
          const hasUnavailableSource = entry.source_links.some(
            s => s.availability === 'unavailable',
          );

          return (
            <div
              key={entry.id}
              className="lore-entry-card"
              onClick={() => {
                setViewingEntry(entry);
                setIsEditing(false);
                setIsCreating(false);
              }}
            >
              <div className="lore-entry-card-header">
                <div className="lore-entry-card-title-row">
                  <h4 className="lore-entry-card-title">{entry.title}</h4>
                  <div className="lore-entry-badges">
                    <span className="lore-badge-type">
                      {ENTRY_TYPE_LABELS[entry.type] || entry.type}
                    </span>
                    <span
                      className={
                        entry.status === 'confirmed' ? 'lore-badge-confirmed' : 'lore-badge-draft'
                      }
                    >
                      {ENTRY_STATUS_LABELS[entry.status]}
                    </span>
                    <span className="lore-badge-rev">rev.{entry.revision}</span>
                    {entry.needs_source_review && (
                      <span className="lore-badge-review" title="部分引用来源失效，需人工复核">
                        <AlertTriangle size={11} /> 需复核
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {entry.summary ? (
                <div className="lore-entry-summary-text">{entry.summary}</div>
              ) : (
                <div className="muted text-xs">（暂无摘要描述）</div>
              )}

              <div className="lore-entry-card-footer">
                <div>
                  {entry.source_links.length === 0 ? (
                    <span className="lore-entry-manual-tag">
                      <Info size={11} /> 人工录入 · 无文档引用
                    </span>
                  ) : (
                    <span>引用 {entry.source_links.length} 处文档切片</span>
                  )}
                  {hasDeletedSource && (
                    <span className="lore-source-badge-deleted" style={{ marginLeft: '8px' }}>
                      含已删除来源
                    </span>
                  )}
                  {hasUnavailableSource && (
                    <span className="lore-source-badge-unavailable" style={{ marginLeft: '8px' }}>
                      含不可用切片
                    </span>
                  )}
                </div>
                <span>更新于 {new Date(entry.updated_at).toLocaleDateString()}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* 分页控制 */}
      {totalPages > 1 && (
        <div className="lore-entries-pagination">
          <span>
            第 {page + 1} / {totalPages} 页（共 {total} 项）
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              className="secondary"
              style={{ fontSize: '11px', minHeight: '30px', padding: '3px 10px' }}
              disabled={page === 0 || loading}
              onClick={() => loadEntries(page - 1, selectedType, selectedStatus)}
            >
              <ChevronLeft size={13} /> 上一页
            </button>
            <button
              type="button"
              className="secondary"
              style={{ fontSize: '11px', minHeight: '30px', padding: '3px 10px' }}
              disabled={page + 1 >= totalPages || loading}
              onClick={() => loadEntries(page + 1, selectedType, selectedStatus)}
            >
              下一页 <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}

      {/* 详情模态弹窗 */}
      {viewingEntry && !isEditing && (
        <div className="modal-backdrop" onClick={() => setViewingEntry(null)}>
          <div
            className="modal lore-entry-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="lore-entry-modal-header">
              <div>
                <div className="flex items-center gap-2">
                  <h3 style={{ fontSize: '18px', margin: 0 }}>{viewingEntry.title}</h3>
                  <span className="lore-badge-type">
                    {ENTRY_TYPE_LABELS[viewingEntry.type] || viewingEntry.type}
                  </span>
                  <span
                    className={
                      viewingEntry.status === 'confirmed'
                        ? 'lore-badge-confirmed'
                        : 'lore-badge-draft'
                    }
                  >
                    {ENTRY_STATUS_LABELS[viewingEntry.status]}
                  </span>
                  <span className="lore-badge-rev">rev.{viewingEntry.revision}</span>
                </div>
                <div className="muted text-xs mt-1">
                  创建于 {new Date(viewingEntry.created_at).toLocaleString()} · 更新于{' '}
                  {new Date(viewingEntry.updated_at).toLocaleString()}
                </div>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭"
                onClick={() => setViewingEntry(null)}
              >
                <X size={20} />
              </button>
            </div>

            <div className="lore-entry-modal-body">
              {/* 需复核醒目提示横幅 */}
              {viewingEntry.needs_source_review && (
                <div className="lore-entries-banner conflict" role="alert">
                  <AlertTriangle size={18} style={{ color: '#ba6452' }} />
                  <div>
                    <strong style={{ color: '#ba6452' }}>提示：该条目部分引用来源已被删除或失效</strong>
                    <p style={{ margin: '4px 0 0 0', fontSize: '11px' }}>
                      虽然已确认的条目内容得以保留，但因其引用的原文档已不存在，建议人工核实设定真伪或重新补充来源。
                    </p>
                  </div>
                </div>
              )}

              {/* 摘要与说明 */}
              <div>
                <h4 style={{ fontSize: '13px', margin: '0 0 8px 0', color: 'var(--muted)' }}>
                  设定描述 / 摘要
                </h4>
                <div
                  style={{
                    fontSize: '14px',
                    lineHeight: '1.8',
                    whiteSpace: 'pre-wrap',
                    background: 'var(--bg)',
                    padding: '12px 14px',
                    borderRadius: '8px',
                    border: '1px solid var(--line)',
                  }}
                >
                  {viewingEntry.summary || '（未填写摘要描述）'}
                </div>
              </div>

              {/* 来源引用列表 */}
              <div className="lore-sources-section">
                <div className="lore-sources-header">
                  <h4 style={{ fontSize: '13px', margin: 0 }}>
                    原文证据与切片引用 ({viewingEntry.source_links.length})
                  </h4>
                  {viewingEntry.source_links.length === 0 && (
                    <span className="lore-entry-manual-tag">
                      <Info size={12} /> 人工录入 · 无文档引用
                    </span>
                  )}
                </div>

                {viewingEntry.source_links.length === 0 ? (
                  <div className="muted text-xs">
                    该条目为作者直接手工录入，未关联任何上传的文档切片。
                  </div>
                ) : (
                  <div className="flex flex-col gap-2">
                    {viewingEntry.source_links.map((src, idx) => (
                      <div key={idx} className="lore-source-item">
                        <div className="lore-source-top">
                          <span className="muted text-xs">
                            切片 ID: {src.chunk_id.slice(0, 8)}... (区间 {src.start_char}..{src.end_char})
                          </span>
                          <div>
                            {src.availability === 'deleted' && (
                              <span className="lore-source-badge-deleted">原文档已被删除</span>
                            )}
                            {src.availability === 'unavailable' && (
                              <span className="lore-source-badge-unavailable">切片不可用</span>
                            )}
                            {src.availability === 'active' && onOpenSource && (
                              <button
                                type="button"
                                className="text-button"
                                style={{ fontSize: '11px', padding: '2px 6px' }}
                                onClick={() => {
                                  onOpenSource({
                                    documentId: src.document_id,
                                    chunkId: src.chunk_id,
                                    startChar: src.start_char,
                                    endChar: src.end_char,
                                  });
                                  setViewingEntry(null);
                                }}
                              >
                                <ExternalLink size={12} /> 定位原文
                              </button>
                            )}
                          </div>
                        </div>
                        {src.excerpt && (
                          <div className="lore-source-excerpt">“{src.excerpt}”</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="lore-entry-modal-footer">
              <button
                type="button"
                className="secondary"
                style={{ color: '#ba6452', marginRight: 'auto' }}
                onClick={() => setDeletingEntry(viewingEntry)}
              >
                <Trash2 size={14} /> 删除条目
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => handleOpenEdit(viewingEntry)}
              >
                <Edit2 size={14} /> 编辑条目
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建与编辑表单模态框 */}
      {(isCreating || isEditing) && (
        <div className="modal-backdrop" onClick={handleCancelForm}>
          <div
            className="modal lore-entry-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="lore-entry-modal-header">
              <div>
                <h3 style={{ fontSize: '17px', margin: 0 }}>
                  {isCreating ? '新建设定条目' : `编辑条目：${viewingEntry?.title}`}
                </h3>
                <p className="muted text-xs mt-1">
                  人工录入与维护，所有分类与确认状态由作者自主决定，不做假 AI 抽取。
                </p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭"
                onClick={handleCancelForm}
              >
                <X size={20} />
              </button>
            </div>

            <div className="lore-entry-modal-body">
              {/* 草稿恢复提示 */}
              {localDraftNotice && (
                <div className="lore-entries-banner info">
                  <Info size={15} />
                  <span style={{ flex: 1 }}>{localDraftNotice}</span>
                  <button
                    type="button"
                    className="text-button"
                    style={{ fontSize: '11px' }}
                    onClick={handleDiscardDraft}
                  >
                    放弃草稿
                  </button>
                </div>
              )}

              {/* 409 乐观锁冲突告警与对照区 */}
              {conflictData && (
                <div className="lore-entries-banner conflict" role="alert">
                  <AlertTriangle size={18} style={{ color: '#ba6452' }} />
                  <div style={{ flex: 1 }}>
                    <strong>条目已在外部被更新（版本冲突）</strong>
                    <p style={{ margin: '4px 0 8px 0', fontSize: '11px' }}>
                      你当前基于版本 rev.{conflictData.expectedRevision} 进行修改，但服务端已有更新版本。禁止盲目覆盖提交。
                    </p>

                    {!conflictRemoteEntry && (
                      <button
                        type="button"
                        className="secondary"
                        style={{ fontSize: '11px', minHeight: '28px', padding: '2px 8px' }}
                        disabled={fetchingConflictRemote}
                        onClick={handleFetchLatestConflict}
                      >
                        <GitCompare size={12} /> {fetchingConflictRemote ? '正在拉取远端...' : '拉取远端最新版本对比'}
                      </button>
                    )}

                    {/* 远端最新对照视图 */}
                    {conflictRemoteEntry && (
                      <div className="lore-conflict-diff-box">
                        <div className="lore-conflict-diff-title">
                          <GitCompare size={13} /> 版本冲突对比（本地修改 vs 远端最新）
                        </div>
                        <div className="lore-conflict-diff-table">
                          <div className="lore-conflict-diff-row header">
                            <span className="diff-col-field">字段</span>
                            <span className="diff-col-val">你的草稿修改</span>
                            <span className="diff-col-val">远端最新 (rev.{conflictRemoteEntry.revision})</span>
                          </div>

                          <div className="lore-conflict-diff-row">
                            <span className="diff-col-field">标题</span>
                            <span className={`diff-col-val ${formData.title !== conflictRemoteEntry.title ? 'changed' : ''}`}>
                              {formData.title}
                            </span>
                            <span className="diff-col-val">{conflictRemoteEntry.title}</span>
                          </div>

                          <div className="lore-conflict-diff-row">
                            <span className="diff-col-field">分类</span>
                            <span className={`diff-col-val ${formData.type !== conflictRemoteEntry.type ? 'changed' : ''}`}>
                              {ENTRY_TYPE_LABELS[formData.type]}
                            </span>
                            <span className="diff-col-val">{ENTRY_TYPE_LABELS[conflictRemoteEntry.type]}</span>
                          </div>

                          <div className="lore-conflict-diff-row">
                            <span className="diff-col-field">状态</span>
                            <span className={`diff-col-val ${formData.status !== conflictRemoteEntry.status ? 'changed' : ''}`}>
                              {ENTRY_STATUS_LABELS[formData.status]}
                            </span>
                            <span className="diff-col-val">{ENTRY_STATUS_LABELS[conflictRemoteEntry.status]}</span>
                          </div>

                          <div className="lore-conflict-diff-row">
                            <span className="diff-col-field">摘要</span>
                            <span className={`diff-col-val ${formData.summary !== conflictRemoteEntry.summary ? 'changed' : ''}`}>
                              {formData.summary || '（空）'}
                            </span>
                            <span className="diff-col-val">{conflictRemoteEntry.summary || '（空）'}</span>
                          </div>

                          <div className="lore-conflict-diff-row">
                            <span className="diff-col-field">引用切片</span>
                            <span className="diff-col-val">{formData.source_links.length} 处</span>
                            <span className="diff-col-val">{conflictRemoteEntry.source_links.length} 处</span>
                          </div>
                        </div>

                        {/* 显式确认按钮 */}
                        <div className="flex gap-2 mt-3 flex-wrap">
                          <button
                            type="button"
                            className="primary"
                            style={{ fontSize: '11px', minHeight: '30px', padding: '4px 10px' }}
                            onClick={handleAdoptRemoteRevisionAndRebase}
                          >
                            <ArrowRight size={12} /> 采纳远端 rev.{conflictRemoteEntry.revision} 作为新基准并覆盖提交
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            style={{ fontSize: '11px', minHeight: '30px', padding: '4px 10px' }}
                            onClick={handleDiscardAndUseRemote}
                          >
                            放弃我的修改，完全加载远端内容
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 表单错误 */}
              {formError && !conflictData && (
                <div className="lore-entries-banner error" role="alert">
                  <AlertCircle size={16} />
                  <span>{formError}</span>
                </div>
              )}

              {/* 标题 */}
              <div className="lore-form-group">
                <label className="lore-form-label">
                  <span>设定名称 / 标题 <span style={{ color: 'var(--accent)' }}>*</span></span>
                  <span className="lore-form-counter">{formData.title.length} / 80</span>
                </label>
                <input
                  type="text"
                  className="lore-form-input"
                  placeholder="例如：乌木短刀、旧港巷口、苍龙行会..."
                  value={formData.title}
                  maxLength={80}
                  onChange={e => handleFormChange({ ...formData, title: e.target.value })}
                />
              </div>

              {/* 分类与状态 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="lore-form-group">
                  <label className="lore-form-label">分类类型</label>
                  <select
                    className="lore-form-select"
                    value={formData.type}
                    onChange={e =>
                      handleFormChange({ ...formData, type: e.target.value as EntryType })
                    }
                  >
                    {(Object.keys(ENTRY_TYPE_LABELS) as EntryType[]).map(t => (
                      <option key={t} value={t}>
                        {ENTRY_TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="lore-form-group">
                  <label className="lore-form-label">条目确认状态</label>
                  <select
                    className="lore-form-select"
                    value={formData.status}
                    onChange={e =>
                      handleFormChange({ ...formData, status: e.target.value as EntryStatus })
                    }
                  >
                    <option value="draft">草稿 (Draft · 正在构思)</option>
                    <option value="confirmed">已确认 (Confirmed · 正式设定)</option>
                  </select>
                </div>
              </div>

              {/* 摘要 */}
              <div className="lore-form-group">
                <label className="lore-form-label">
                  <span>设定描述 / 详细摘要</span>
                  <span className="lore-form-counter">{formData.summary.length} / 500</span>
                </label>
                <textarea
                  className="lore-form-textarea"
                  placeholder="作者手写描述或背景设定（最多 500 字）..."
                  value={formData.summary}
                  maxLength={500}
                  onChange={e => handleFormChange({ ...formData, summary: e.target.value })}
                />
              </div>

              {/* 来源引用区 */}
              <div className="lore-sources-section">
                <div className="lore-sources-header">
                  <label className="lore-form-label" style={{ margin: 0 }}>
                    关联文档切片证据 ({formData.source_links.length})
                  </label>
                  <button
                    type="button"
                    className="secondary"
                    style={{ fontSize: '11px', minHeight: '28px', padding: '2px 10px' }}
                    onClick={() => setShowSourceSearch(!showSourceSearch)}
                  >
                    <Search size={12} /> {showSourceSearch ? '关闭切片检索' : '搜索并添加切片'}
                  </button>
                </div>

                <div className="muted text-xs" style={{ margin: '4px 0 8px 0', lineHeight: '1.6' }}>
                  提示：已有历史引用（即使原文档已删除或失效）仍将完整保留其历史证据；您无需强制移除历史引用即可修改标题或摘要。
                </div>

                {formData.source_links.length === 0 && (
                  <div className="muted text-xs">
                    当前未关联任何文档切片（保存后将标注为“人工录入 · 无文档引用”）。
                  </div>
                )}

                {/* 已选切片列表 */}
                {formData.source_links.map((src, idx) => (
                  <div key={idx} className="lore-source-item">
                    <div className="lore-source-top">
                      <span className="muted text-xs">
                        切片: {src.chunk_id.slice(0, 8)}... (区间 {src.start_char}..{src.end_char})
                        {src.document_filename && ` · ${src.document_filename}`}
                      </span>
                      <div className="flex items-center gap-2">
                        {src.availability === 'deleted' && (
                          <span className="lore-source-badge-deleted">原文档已删除</span>
                        )}
                        {src.availability === 'unavailable' && (
                          <span className="lore-source-badge-unavailable">切片不可用</span>
                        )}
                        <button
                          type="button"
                          className="text-button"
                          style={{ color: '#ba6452', fontSize: '11px' }}
                          onClick={() => handleRemoveSource(idx)}
                        >
                          <X size={12} /> 移除引用
                        </button>
                      </div>
                    </div>
                    {src.excerpt && (
                      <div className="lore-source-excerpt">“{src.excerpt}”</div>
                    )}
                  </div>
                ))}

                {/* 内嵌来源切片检索 */}
                {showSourceSearch && (
                  <div className="lore-search-sources-box">
                    <div className="flex gap-2">
                      <input
                        type="text"
                        className="lore-form-input"
                        style={{ flex: 1, padding: '7px 10px', fontSize: '12px' }}
                        placeholder="输入人名、地名或词条在资料库中检索切片..."
                        value={sourceQuery}
                        onChange={e => setSourceQuery(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleSearchSources();
                          }
                        }}
                      />
                      <button
                        type="button"
                        className="secondary"
                        style={{ fontSize: '12px', minHeight: '34px', padding: '0 12px' }}
                        disabled={searchingSources || !sourceQuery.trim()}
                        onClick={handleSearchSources}
                      >
                        {searchingSources ? '搜索中...' : '检索'}
                      </button>
                    </div>

                    {sourceSearchError && (
                      <div className="error text-xs mt-1">{sourceSearchError}</div>
                    )}

                    {sourceCandidates.length > 0 && (
                      <div
                        className="flex flex-col gap-2 mt-2"
                        style={{ maxHeight: '180px', overflowY: 'auto' }}
                      >
                        {sourceCandidates.map((c, i) => (
                          <div
                            key={i}
                            className="lore-candidate-item"
                            onClick={() => handleSelectSource(c)}
                          >
                            <div className="flex justify-between items-center text-xs">
                              <strong>{c.filename || c.document_filename || '文档切片'}</strong>
                              <span className="muted">区间 {c.start_char}..{c.end_char}</span>
                            </div>
                            <div className="muted text-xs mt-1">{c.snippet}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div className="lore-entry-modal-footer">
              <button
                type="button"
                className="secondary"
                onClick={handleCancelForm}
                disabled={submitting}
              >
                取消（保留草稿）
              </button>
              <button
                type="button"
                className="primary"
                onClick={handleSaveEntry}
                disabled={submitting || (conflictData !== null && !conflictRemoteEntry)}
              >
                {submitting ? '正在提交...' : isCreating ? '确认创建' : '保存修改'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认对话框 */}
      {deletingEntry && (
        <div className="modal-backdrop" onClick={() => setDeletingEntry(null)}>
          <div
            className="modal"
            style={{ maxWidth: '420px', padding: '24px' }}
            onClick={e => e.stopPropagation()}
            role="alertdialog"
          >
            <h3
              style={{
                fontSize: '17px',
                color: '#ba6452',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <AlertTriangle size={18} /> 确认软删除该设定条目？
            </h3>
            <p className="mt-3 text-xs" style={{ lineHeight: '1.7', color: 'var(--text)' }}>
              条目 <strong>“{deletingEntry.title}”</strong>（rev.{deletingEntry.revision}）将被软删除。
              删除后该条目将不再出现在列表中，但其历史数据和关联引用仍然保留，不破坏已有文档。
            </p>
            {formError && <div className="error text-xs mt-2">{formError}</div>}
            <div className="flex justify-end gap-2 mt-4">
              <button
                type="button"
                className="secondary"
                disabled={submitting}
                onClick={() => setDeletingEntry(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="primary"
                style={{ background: '#ba6452', borderColor: '#ba6452' }}
                disabled={submitting}
                onClick={handleConfirmDelete}
              >
                {submitting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

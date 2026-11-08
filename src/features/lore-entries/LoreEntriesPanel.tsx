import React, { useState, useEffect, useRef } from 'react';
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
} from 'lucide-react';
import type {
  EntryStatus,
  EntryType,
  LoreEntriesPanelProps,
  LoreEntry,
  LoreEntryDraft,
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

export function LoreEntriesPanel({
  worldId,
  api,
  onOpenSource,
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

  // 表单状态
  const [formData, setFormData] = useState<LoreEntryDraft>(EMPTY_DRAFT);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 草稿防丢与冲突处理
  const [localDraftNotice, setLocalDraftNotice] = useState<string | null>(null);
  const [conflictData, setConflictData] = useState<{
    expectedRevision: number;
    currentEntry?: LoreEntry;
  } | null>(null);

  // 来源检索状态
  const [showSourceSearch, setShowSourceSearch] = useState(false);
  const [sourceQuery, setSourceQuery] = useState('');
  const [sourceCandidates, setSourceCandidates] = useState<SourceCandidate[]>([]);
  const [searchingSources, setSearchingSources] = useState(false);
  const [sourceSearchError, setSourceSearchError] = useState<string | null>(null);

  // 草稿暂存区 (worldId:entryId -> draft)
  const draftsMapRef = useRef<Map<string, LoreEntryDraft>>(new Map());
  const listAbortRef = useRef<AbortController | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);

  // 切换世界时，重置所有视图状态并取消在途请求，防止跨世界串扰
  useEffect(() => {
    isMountedRef.current = true;

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
    setFormError(null);
    setConflictData(null);
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

  // 筛选器变化时重新拉取
  function handleFilterChange(newType: EntryType | 'all', newStatus: EntryStatus | 'all') {
    setSelectedType(newType);
    setSelectedStatus(newStatus);
    setPage(0);
    loadEntries(0, newType, newStatus);
  }

  // 加载条目列表
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

      if (!isMountedRef.current) return;
      setEntries(res.items || []);
      setTotal(res.total || 0);
      setPage(targetPage);
    } catch (err: any) {
      if (!isMountedRef.current || err?.name === 'AbortError' || err?.code === 'ABORT_ERROR') {
        return;
      }
      setListError(err?.message || '获取设定条目列表失败，请重试。');
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  }

  // 打开新建表单
  function handleOpenCreate() {
    setFormError(null);
    setConflictData(null);
    setLocalDraftNotice(null);
    setShowSourceSearch(false);
    setSourceCandidates([]);

    const draftKey = `${worldId}:new`;
    const saved = draftsMapRef.current.get(draftKey);
    if (saved) {
      setFormData(saved);
      setLocalDraftNotice('已恢复上次未保存的新建草稿');
    } else {
      setFormData(EMPTY_DRAFT);
    }

    setIsCreating(true);
    setIsEditing(false);
    setViewingEntry(null);
  }

  // 打开编辑表单
  function handleOpenEdit(entry: LoreEntry) {
    setFormError(null);
    setConflictData(null);
    setLocalDraftNotice(null);
    setShowSourceSearch(false);
    setSourceCandidates([]);

    const draftKey = `${worldId}:${entry.id}`;
    const saved = draftsMapRef.current.get(draftKey);

    if (saved) {
      setFormData(saved);
      setLocalDraftNotice('已恢复上次未保存的本地修改草稿');
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
        })),
      });
    }

    setIsEditing(true);
    setIsCreating(false);
  }

  // 取消表单，暂存未提交内容到内存
  function handleCancelForm() {
    const isDirty = formData.title.trim().length > 0 || formData.summary.trim().length > 0;
    if (isDirty) {
      const draftKey = isCreating ? `${worldId}:new` : `${worldId}:${viewingEntry?.id}`;
      draftsMapRef.current.set(draftKey, formData);
    }
    setIsCreating(false);
    setIsEditing(false);
    setFormError(null);
    setConflictData(null);
    setShowSourceSearch(false);
  }

  // 放弃暂存草稿
  function handleDiscardDraft() {
    const draftKey = isCreating ? `${worldId}:new` : `${worldId}:${viewingEntry?.id}`;
    draftsMapRef.current.delete(draftKey);
    if (viewingEntry && isEditing) {
      setFormData({
        type: viewingEntry.type,
        title: viewingEntry.title,
        summary: viewingEntry.summary,
        status: viewingEntry.status,
        source_links: viewingEntry.source_links.map(s => ({
          document_id: s.document_id,
          chunk_id: s.chunk_id,
          start_char: s.start_char,
          end_char: s.end_char,
          excerpt: s.excerpt,
        })),
      });
    } else {
      setFormData(EMPTY_DRAFT);
    }
    setLocalDraftNotice(null);
  }

  // 检索切片来源
  async function handleSearchSources() {
    if (!sourceQuery.trim() || searchingSources) return;

    if (searchAbortRef.current) {
      searchAbortRef.current.abort();
    }
    const controller = new AbortController();
    searchAbortRef.current = controller;

    setSearchingSources(true);
    setSourceSearchError(null);

    try {
      const results = await api.searchSources({
        worldId,
        query: sourceQuery.trim(),
        signal: controller.signal,
      });
      if (!isMountedRef.current) return;
      setSourceCandidates(results || []);
    } catch (err: any) {
      if (!isMountedRef.current || err?.name === 'AbortError' || err?.code === 'ABORT_ERROR') {
        return;
      }
      setSourceSearchError(err?.message || '搜索资料切片失败');
    } finally {
      if (isMountedRef.current) {
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
    };

    setFormData(prev => ({
      ...prev,
      source_links: [...prev.source_links, newLink],
    }));
  }

  // 移除引用
  function handleRemoveSource(index: number) {
    setFormData(prev => ({
      ...prev,
      source_links: prev.source_links.filter((_, i) => i !== index),
    }));
  }

  // 保存（新建或编辑）
  async function handleSaveEntry() {
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

    setSubmitting(true);
    setFormError(null);
    setConflictData(null);

    try {
      if (isCreating) {
        const created = await api.create(worldId, {
          ...formData,
          title: cleanTitle,
          summary: formData.summary.trim(),
        });
        // 清理新建草稿
        draftsMapRef.current.delete(`${worldId}:new`);
        setIsCreating(false);
        setViewingEntry(created);
        loadEntries(page, selectedType, selectedStatus);
      } else if (isEditing && viewingEntry) {
        const updated = await api.update(
          viewingEntry.id,
          {
            ...formData,
            title: cleanTitle,
            summary: formData.summary.trim(),
          },
          viewingEntry.revision,
        );
        // 清理编辑草稿
        draftsMapRef.current.delete(`${worldId}:${viewingEntry.id}`);
        setIsEditing(false);
        setViewingEntry(updated);
        loadEntries(page, selectedType, selectedStatus);
      }
    } catch (err: any) {
      if (!isMountedRef.current) return;

      if (err?.status === 409 || err?.code === 'LORE_ENTRY_CONFLICT') {
        // 发生 409 乐观锁冲突
        setConflictData({
          expectedRevision: viewingEntry?.revision || 1,
        });
        setFormError('内容已被他人修改（版本冲突）：当前条目已被更新，请对比后重新提交。');
      } else {
        setFormError(err?.friendlyMessage || err?.message || '保存条目失败，请检查网络后重试。');
      }
    } finally {
      if (isMountedRef.current) {
        setSubmitting(false);
      }
    }
  }

  // 冲突解决：拉取最新远程版本
  async function handleFetchLatestConflict() {
    if (!viewingEntry) return;
    try {
      const latest = await api.get(viewingEntry.id);
      setViewingEntry(latest);
      setConflictData(prev => (prev ? { ...prev, currentEntry: latest } : null));
    } catch (err: any) {
      setFormError('获取最新条目失败：' + (err?.message || '未知错误'));
    }
  }

  // 确认软删除
  async function handleConfirmDelete() {
    if (!deletingEntry) return;
    setSubmitting(true);
    try {
      await api.remove(deletingEntry.id, deletingEntry.revision);
      // 清除本地草稿
      draftsMapRef.current.delete(`${worldId}:${deletingEntry.id}`);
      setDeletingEntry(null);
      setViewingEntry(null);
      loadEntries(page, selectedType, selectedStatus);
    } catch (err: any) {
      if (err?.status === 409 || err?.code === 'LORE_ENTRY_CONFLICT') {
        setFormError('删除失败：该条目已被其他操作修改，请刷新列表后再操作。');
      } else {
        setFormError(err?.message || '删除条目失败。');
      }
    } finally {
      setSubmitting(false);
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

              {/* 409 乐观锁冲突告警 */}
              {conflictData && (
                <div className="lore-entries-banner conflict" role="alert">
                  <AlertTriangle size={18} style={{ color: '#ba6452' }} />
                  <div style={{ flex: 1 }}>
                    <strong>条目已在外部被更新（版本冲突）</strong>
                    <p style={{ margin: '4px 0 8px 0', fontSize: '11px' }}>
                      你当前基于版本 rev.{conflictData.expectedRevision} 进行修改，但服务端已有更新版本。禁止覆盖提交。
                    </p>
                    <button
                      type="button"
                      className="secondary"
                      style={{ fontSize: '11px', minHeight: '28px', padding: '2px 8px' }}
                      onClick={handleFetchLatestConflict}
                    >
                      拉取并查看最新版本
                    </button>
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
                  onChange={e => setFormData({ ...formData, title: e.target.value })}
                />
              </div>

              {/* 分类与状态 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="lore-form-group">
                  <label className="lore-form-label">分类类型</label>
                  <select
                    className="lore-form-select"
                    value={formData.type}
                    onChange={e => setFormData({ ...formData, type: e.target.value as EntryType })}
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
                      setFormData({ ...formData, status: e.target.value as EntryStatus })
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
                  onChange={e => setFormData({ ...formData, summary: e.target.value })}
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
                      </span>
                      <button
                        type="button"
                        className="text-button"
                        style={{ color: '#ba6452', fontSize: '11px' }}
                        onClick={() => handleRemoveSource(idx)}
                      >
                        <X size={12} /> 移除引用
                      </button>
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
                      <div className="error text-xs">{sourceSearchError}</div>
                    )}

                    {sourceCandidates.length > 0 && (
                      <div className="flex flex-col gap-2 mt-2" style={{ maxHeight: '180px', overflowY: 'auto' }}>
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
                            <div className="muted text-xs">{c.snippet}</div>
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
                disabled={submitting}
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
            <h3 style={{ fontSize: '17px', color: '#ba6452', display: 'flex', alignItems: 'center', gap: '8px' }}>
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


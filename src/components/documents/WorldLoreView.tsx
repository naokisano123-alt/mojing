import { useState, useEffect, useRef } from 'react';
import {
  Upload,
  BookOpen,
  Search,
  ClipboardList,
  FlaskConical,
  AlertCircle,
} from 'lucide-react';
import type { WorldDetail, WorldDocument } from '../../api/types';
import {
  getWorld,
  deleteDocument,
  getCapabilities,
  searchLore,
  ApiClientError,
} from '../../api/client';
import { Back, PageTitle, Tag } from '../UI';
import { DocumentUpload } from './DocumentUpload';
import { DocumentList } from './DocumentList';
import { DocumentViewer } from './DocumentViewer';
import { LoreSearchPanel } from '../../features/lore-search';
import { LoreEntriesPanel } from '../../features/lore-entries';
import { AdventureJournal } from '../../features/adventures/AdventureJournal';
import type { ModelSessionResponse } from '../../features/narration-preview/types';
import { NarrationPreview } from '../../features/narration-preview';
import type { NarrationPreviewApi } from '../../features/narration-preview/types';
import type {
  LoreEntriesPanelApi,
  LoreEntry as PanelLoreEntry,
  LoreEntryDraftStore,
  DraftStoreValue,
} from '../../features/lore-entries/types';
import {
  listLoreEntries,
  getLoreEntry,
  createLoreEntry,
  updateLoreEntry,
  deleteLoreEntry,
  createModelSession,
  deleteModelSession,
  deleteModelProfile,
  testModelSession,
  previewNarration,
} from '../../api/client';
import './documents.css';
import { getUploadPolicy, toLoreSearchItem } from './loreAdapters';

export interface WorldLoreViewProps {
  worldId: string;
  worldName: string;
  onBack: () => void;
  initialTab?: 'list' | 'journal';
  modelSession?: ModelSessionResponse | null;
  modelProfileEnabled?: boolean;
  onModelSessionChange?: (session: ModelSessionResponse | null) => void;
}

type Tab = 'list' | 'search' | 'entries' | 'narration' | 'journal' | 'upload';

export function WorldLoreView({
  worldId,
  worldName,
  onBack,
  initialTab = 'list',
  modelSession = null,
  modelProfileEnabled = false,
  onModelSessionChange,
}: WorldLoreViewProps) {
  const [worldDetail, setWorldDetail] = useState<WorldDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [configuring, setConfiguring] = useState(false);
  function switchTab(next: Tab) {
    setConfiguring(false);
    setTab(next);
  }
  const [targetEntryId, setTargetEntryId] = useState<string>();
  const [uploadPolicy, setUploadPolicy] = useState(() => getUploadPolicy());
  const [notice, setNotice] = useState('');

  // 稳定的草稿容器：切子页签不会丢草稿，不同世界 key 隔离（契约 M1_R4_API §6）
  const draftStoreRef = useRef<LoreEntryDraftStore | null>(null);
  if (!draftStoreRef.current) {
    const drafts = new Map<string, DraftStoreValue>();
    draftStoreRef.current = {
      get: key => drafts.get(key),
      set: (key, value) => {
        drafts.set(key, value);
      },
      delete: key => {
        drafts.delete(key);
      },
    };
  }
  const draftStore = draftStoreRef.current;

  // 当前打开查看器的文档（可带来源区间，用于检索回跳高亮）
  const [viewingDoc, setViewingDoc] = useState<{
    documentId: string;
    mode?: 'chunks' | 'raw';
    source?: { chunkId?: string; chunkIndex?: number; startChar?: number; endChar?: number };
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setConfiguring(false);
    setViewingDoc(null);
    setTargetEntryId(undefined);
    void loadWorld(controller.signal);
    return () => controller.abort();
  }, [worldId]);

  // 能力探测：只启用后端实际可用的格式（契约 M1_R2_API §1.7）
  useEffect(() => {
    let alive = true;
    getCapabilities()
      .then(caps => {
        if (alive) setUploadPolicy(getUploadPolicy(caps));
      })
      .catch(() => {
        if (alive) setUploadPolicy(getUploadPolicy());
      });
    return () => {
      alive = false;
    };
  }, []);

  async function loadWorld(signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    setWorldDetail(null);
    try {
      const data = await getWorld(worldId, signal);
      if (signal?.aborted) return;
      setWorldDetail(data);
      setLoading(false);
    } catch (err: unknown) {
      if (signal?.aborted) return;
      setLoading(false);
      if (err instanceof ApiClientError) {
        setError(err.friendlyMessage);
      } else {
        setError('无法连接后端服务或世界不存在。');
      }
    }
  }

  async function handleDeleteDocument(documentId: string) {
    try {
      await deleteDocument(documentId);
      setWorldDetail(prev => {
        if (!prev) return null;
        return {
          ...prev,
          document_count: Math.max(0, prev.document_count - 1),
          documents: prev.documents.filter(d => d.id !== documentId),
        };
      });
    } catch (err: unknown) {
      if (err instanceof ApiClientError) {
        alert(err.friendlyMessage);
      } else {
        alert('删除文档失败，请重试。');
      }
    }
  }

  function handleUploadSuccess(newDoc: WorldDocument) {
    setWorldDetail(prev => {
      if (!prev) return null;
      return {
        ...prev,
        document_count: prev.document_count + 1,
        documents: [newDoc, ...prev.documents],
      };
    });
    setTab('list');
  }

  // 模型叙事实验 API 适配（M2_A_API v1.0）
  const narrationApi: NarrationPreviewApi = {
    createSession: (config, signal) => createModelSession(config, signal),
    deleteSession: (id, signal) => deleteModelSession(id, signal),
    deleteProfile: modelProfileEnabled || modelSession?.remembered ? deleteModelProfile : undefined,
    testSession: (id, signal) => testModelSession(id, signal),
    preview: (targetWorldId, payload, signal) =>
      previewNarration(targetWorldId, payload, signal),
  };

  // 条目面板 API 适配：面板只认注入接口，HTTP 细节留在这一层
  const loreApi: LoreEntriesPanelApi = {
    list: async params => {
      const page = await listLoreEntries(
        {
          worldId: params.worldId,
          type: params.type,
          status: params.status,
          limit: params.limit,
          offset: params.offset,
        },
        params.signal,
      );
      return { items: page.items as unknown as PanelLoreEntry[], total: page.total };
    },
    get: async (entryId, signal) =>
      (await getLoreEntry(entryId, signal)) as unknown as PanelLoreEntry,
    create: async (worldId, payload) =>
      (await createLoreEntry(worldId, payload as never)) as unknown as PanelLoreEntry,
    update: async (entryId, payload, expectedRevision) =>
      (await updateLoreEntry(entryId, payload as never, expectedRevision)) as unknown as PanelLoreEntry,
    remove: (entryId, expectedRevision) => deleteLoreEntry(entryId, expectedRevision),
    searchSources: async ({ worldId, query, signal }) => {
      const result = await searchLore({ worldId, query, limit: 20 }, signal);
      return result.items.map(item => ({
        world_id: item.world_id,
        document_id: item.document_id,
        chunk_id: item.chunk_id,
        chunk_index: item.chunk_index,
        document_filename: item.document_filename,
        filename: item.document_filename,
        heading_path: item.heading_path,
        start_char: item.start_char,
        end_char: item.end_char,
        snippet: item.snippet,
        match_field: item.match_field,
      }));
    },
  };

  // 契约 DTO → 检索面板 DTO 的薄适配层（面板类型由 Ma 维护，不共用 src/api/types）
  async function runSearch(
    params: { worldId: string; query: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) {
    const result = await searchLore(params, signal);
    return {
      total: result.total,
      limit: result.limit,
      offset: result.offset,
      items: result.items.map(toLoreSearchItem),
    };
  }

  const documents = worldDetail?.documents || [];

  return (
    <div className="narrow doc-hub">
      <Back onClick={onBack} label="返回世界列表" />

      <PageTitle
        eyebrow="YOUR WORLD"
        title={worldDetail?.name || worldName}
        description={worldDetail?.description || '在这个世界开启旅程，或先导入你的世界设定。'}
        action={<Tag>世界资料库</Tag>}
      />

      {/* 切换 Tab */}
      <div className="tabs" style={{ marginTop: 0 }}>
        <button type="button" className={tab === 'journal' ? 'active' : ''} onClick={() => switchTab('journal')}>冒险记录</button>
        <button
          type="button"
          className={tab === 'list' ? 'active' : ''}
          onClick={() => switchTab('list')}
        >
          <BookOpen size={15} /> 已导入资料 ({documents.length})
        </button>
        <button
          type="button"
          className={tab === 'search' ? 'active' : ''}
          onClick={() => switchTab('search')}
        >
          <Search size={15} /> 检索资料
        </button>
        <button
          type="button"
          className={tab === 'entries' ? 'active' : ''}
          onClick={() => switchTab('entries')}
        >
          <ClipboardList size={15} /> 人工设定
        </button>
        <button
          type="button"
          className={tab === 'narration' ? 'active' : ''}
          onClick={() => switchTab('narration')}
        >
          <FlaskConical size={15} /> 叙事实验
        </button>
        <button
          type="button"
          className={tab === 'upload' ? 'active' : ''}
          onClick={() => switchTab('upload')}
        >
          <Upload size={15} /> 导入新文档
        </button>
      </div>

      {error && (
        <div className="upload-error-callout mb-4" role="alert">
          <AlertCircle size={18} />
          <div>
            <div>{error}</div>
            <button
              type="button"
              className="text-button mt-1"
              style={{ color: '#ba6452', textDecoration: 'underline' }}
              onClick={() => void loadWorld()}
            >
              重新加载
            </button>
          </div>
        </div>
      )}

      {notice && (
        <p className="muted text-xs" role="status">
          {notice}
        </p>
      )}

      {tab === 'list' && (
        <DocumentList
          documents={documents}
          loading={loading}
          onSelectDocument={(doc, mode) => setViewingDoc({ documentId: doc.id, mode })}
          onDeleteDocument={handleDeleteDocument}
        />
      )}

      {tab === 'search' && (
        <LoreSearchPanel
          worldId={worldId}
          search={runSearch}
          onOpenSource={target =>
            setViewingDoc({
              documentId: target.documentId,
              mode: 'chunks',
              source: {
                chunkIndex: target.chunkIndex,
                startChar: target.startChar,
                endChar: target.endChar,
              },
            })
          }
        />
      )}

      {tab === 'entries' && (
        <LoreEntriesPanel
          initialEntryId={targetEntryId}
          worldId={worldId}
          api={loreApi}
          draftStore={draftStore}
          onOpenSource={target =>
            setViewingDoc({
              documentId: target.documentId,
              mode: 'chunks',
              source: {
                chunkId: target.chunkId,
                chunkIndex: target.chunkIndex,
                startChar: target.startChar,
                endChar: target.endChar,
              },
            })
          }
        />
      )}

      {tab === 'narration' && (
        <NarrationPreview
          worldId={worldId}
          api={narrationApi}
          initialSession={modelSession}
          onSessionChange={onModelSessionChange}
          onOpenSource={target => {
            if (target.kind === 'entry') {
              setTargetEntryId(target.entry_id);
              setTab('entries');
              return;
            }
            setViewingDoc({
              documentId: target.document_id || '',
              mode: 'chunks',
              source: {
                chunkId: target.chunk_id,
                chunkIndex: target.chunk_index,
              },
            });
          }}
        />
      )}

      {tab === 'journal' && configuring && (
        <section aria-label="冒险模型设置">
          <NarrationPreview worldId={worldId} api={narrationApi} initialMode="live" connectionOnly
            initialSession={modelSession} onSessionChange={onModelSessionChange} />
          <button type="button" className="secondary mt-3" onClick={() => setConfiguring(false)}>返回旅程</button>
        </section>
      )}
      {tab === 'journal' && <div hidden={configuring}><AdventureJournal worldId={worldId} sessionId={modelSession?.session_id}
        onConfigure={() => setConfiguring(true)}
        onSessionExpired={() => onModelSessionChange?.(null)}
        onOpenSource={source => {
          if (source.kind === 'entry') { setTargetEntryId(source.entry_id); setTab('entries'); }
          else if (source.document_id) setViewingDoc({documentId:source.document_id,mode:'chunks',source:{chunkId:source.chunk_id,chunkIndex:source.chunk_index}});
        }} /></div>}

      {tab === 'upload' && (
        <DocumentUpload
          worldId={worldId}
          onSuccess={handleUploadSuccess}
          onCancel={() => setTab('list')}
          {...uploadPolicy}
          onViewExisting={id => setViewingDoc({ documentId: id, mode: 'chunks' })}
          onRefreshRequest={() => void loadWorld()}
        />
      )}

      {/* 文档切片与原文查看器（可带检索来源区间） */}
      {viewingDoc && (
        <DocumentViewer
          documentId={viewingDoc.documentId}
          initialMode={viewingDoc.mode || 'chunks'}
          initialSource={viewingDoc.source}
          onClose={() => setViewingDoc(null)}
        />
      )}

    </div>
  );
}


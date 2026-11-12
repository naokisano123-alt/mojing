import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import {
  Upload, BookOpen, ClipboardList, Settings as SettingsIcon, ArrowLeft, AlertCircle,
} from 'lucide-react';
import type { WorldDetail, WorldDocument } from '../../api/types';
import {
  getWorld,
  deleteDocument,
  getCapabilities,
  searchLore,
  ApiClientError,
} from '../../api/client';
import { Back } from '../UI';
import { DocumentUpload } from './DocumentUpload';
import { DocumentList } from './DocumentList';
import { DocumentViewer } from './DocumentViewer';
import { LoreSearchPanel } from '../../features/lore-search';
import { LoreEntriesPanel } from '../../features/lore-entries';
import { AdventureJournal } from '../../features/adventures/AdventureJournal';
import { WorldPulsePanel } from '../../features/world-pulse';
import { PulsePreview } from '../../features/adventures/PulsePreview';
import type { Run } from '../../features/adventures/api';
import Preferences from '../../pages/Settings';
import type { State } from '../../model';
import type { LoreEntryDto } from '../../api/types';
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
import { WorldExport } from './WorldExport';
import { getUploadPolicy, toLoreSearchItem } from './loreAdapters';

export interface WorldLoreViewProps {
  worldId: string;
  worldName: string;
  onBack: () => void;
  initialTab?: 'list' | 'journal';
  prepareWorld?: boolean;
  modelSession?: ModelSessionResponse | null;
  modelProfileEnabled?: boolean;
  onModelSessionChange?: (session: ModelSessionResponse | null) => void;
  preferences?: State;
  onPreferencesChange?: (value: Partial<State>) => void;
}

type Tab = 'documents' | 'journal' | 'settings';

export function WorldLoreView({
  worldId,
  worldName,
  onBack,
  initialTab = 'list',
  prepareWorld = false,
  modelSession = null,
  modelProfileEnabled = false,
  onModelSessionChange,
  preferences,
  onPreferencesChange,
}: WorldLoreViewProps) {
  const [worldDetail, setWorldDetail] = useState<WorldDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab === 'list' ? 'documents' : 'journal');
  const [journalPage, setJournalPage] = useState<'landing' | 'adventure' | 'saves'>('landing');
  const [preparing, setPreparing] = useState(prepareWorld);
  const scrollPositions = useRef(new Map<Tab, number>());
  const nextScroll = useRef<number | null>(null);
  function navigate(next: Tab, reset = false) {
    scrollPositions.current.set(tab, window.scrollY);
    nextScroll.current = reset ? 0 : scrollPositions.current.get(next) ?? 0;
    if (next === tab) {
      window.scrollTo({ top: nextScroll.current ?? 0, behavior: 'instant' });
      nextScroll.current = null;
    }
    setTab(next);
  }
  useLayoutEffect(() => {
    if (nextScroll.current === null) return;
    window.scrollTo({ top: nextScroll.current, behavior: 'instant' });
    nextScroll.current = null;
  }, [tab]);
  const [docSection, setDocSection] = useState<'list' | 'entries' | 'upload'>('list');
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [query, setQuery] = useState('');
  const [searchVersion, setSearchVersion] = useState(0);
  const [entryMatches, setEntryMatches] = useState<LoreEntryDto[]>([]);
  const [entryTotal, setEntryTotal] = useState(0);
  const [entryError, setEntryError] = useState('');
  const [entryOffset, setEntryOffset] = useState(0);
  const [targetEntryId, setTargetEntryId] = useState<string>();
  const [uploadPolicy, setUploadPolicy] = useState(() => getUploadPolicy());

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
    setTab(initialTab === 'list' ? 'documents' : 'journal');
    scrollPositions.current.clear();
    nextScroll.current = null;
    setActiveRun(null);
    setDocSection('list');
    setQuery('');
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
      if (!prev || prev.documents.some(document => document.id === newDoc.id)) return prev;
      return {
        ...prev,
        document_count: prev.document_count + 1,
        documents: [newDoc, ...prev.documents],
      };
    });
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
  const runSearch = useCallback(async (
    params: { worldId: string; query: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => {
    const result = await searchLore(params, signal);
    return {
      total: result.total,
      limit: result.limit,
      offset: result.offset,
      items: result.items.map(toLoreSearchItem),
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setEntryMatches([]);
    setEntryTotal(0);
    setEntryError('');
    if (!query.trim()) { setEntryTotal(0); return; }
    listLoreEntries({ worldId, q: query, limit: 20, offset: entryOffset }, controller.signal).then(page => {
      if (!controller.signal.aborted) { setEntryMatches(page.items); setEntryTotal(page.total); }
    }).catch(error => {
      if (!controller.signal.aborted) setEntryError(error instanceof Error ? error.message : '设定搜索失败，请重试。');
    });
    return () => controller.abort();
  }, [worldId, query, entryOffset, searchVersion]);

  const onQueryChange = useCallback((value: string) => { setQuery(value); setEntryOffset(0); }, []);
  function openEntry(id?: string) {
    setTargetEntryId(id);
    setDocSection('entries');
    navigate('documents');
  }
  function openDocuments() {
    setDocSection('list');
    navigate('documents');
  }

  const documents = worldDetail?.documents || [];

  return (
    <div className="narrow doc-hub world-workspace" aria-label={worldDetail?.name || worldName}>
      <header className="workspace-header">
        <Back onClick={onBack} label="返回世界列表" />
      </header>
      {!preparing && <nav className="workspace-tabs" aria-label="世界栏目">
        {([
          { id: 'journal', label: '冒险', icon: BookOpen },
          { id: 'documents', label: '世界观文档', icon: ClipboardList },
          { id: 'settings', label: '设置', icon: SettingsIcon },
        ] as const).map(({ id, label, icon: Icon }) => <button key={id} type="button"
          aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'active' : ''}
          onClick={() => { if (id === 'journal') setJournalPage('landing'); navigate(id, id === 'journal'); }}><Icon size={17} />{label}</button>)}
      </nav>}

      {error && <div className="upload-error-callout" role="alert">
        <AlertCircle size={18} /><span>{error}</span>
        <button type="button" className="text-button" onClick={() => void loadWorld()}>重新加载</button>
      </div>}

      <section hidden={tab !== 'documents'} aria-label="世界观文档">
        {preparing && <div className="world-preparation"><h1>整理你的世界</h1><p className="muted">导入或编写世界观文档，准备好后开始旅程。也可以稍后补充。</p></div>}
        {docSection === 'list' && <>
          <LoreSearchPanel key={`${worldId}:${searchVersion}`} compact worldId={worldId}
            search={runSearch} onQueryChange={onQueryChange} placeholder="搜索文档与人工设定…"
            onOpenSource={target => setViewingDoc({
              documentId: target.documentId, mode: 'raw',
              source: { chunkIndex: target.chunkIndex, startChar: target.startChar, endChar: target.endChar },
            })} />
          <div className="document-toolbar">
            <span className="muted">{documents.length} 份文档</span>
            <button type="button" className="secondary" onClick={() => setDocSection('upload')}><Upload size={15} />导入</button>
            <button type="button" className="secondary" onClick={() => openEntry()}><ClipboardList size={15} />人工设定</button>

          </div>
          <WorldExport worldId={worldId} />
          {!query && <DocumentList documents={documents} loading={loading}
            onSelectDocument={doc => setViewingDoc({ documentId: doc.id, mode: 'raw' })}
            onDeleteDocument={handleDeleteDocument} />}
          {query && <>
            <h3 className="document-section-title">人工设定 · {entryTotal} 条匹配</h3>
            {entryError && <p role="alert" className="error">{entryError}</p>}
            <div className="document-entry-matches">{entryMatches.map(entry => <button type="button"
              key={entry.id} className="document-entry-result" onClick={() => openEntry(entry.id)}>
              <strong>{entry.title}</strong><span>{entry.summary}</span>
            </button>)}</div>
            {entryTotal > 20 && <div className="document-toolbar">
              <button type="button" className="text-button" disabled={entryOffset === 0}
                onClick={() => setEntryOffset(value => Math.max(0, value - 20))}>上一页设定</button>
              <button type="button" className="text-button" disabled={entryOffset + 20 >= entryTotal}
                onClick={() => setEntryOffset(value => value + 20)}>下一页设定</button>
            </div>}
          </>}
        </>}
        {docSection !== 'list' && <button type="button" className="text-button workspace-subback"
          onClick={openDocuments}><ArrowLeft size={16} />返回世界观文档</button>}
        {docSection === 'entries' && <LoreEntriesPanel initialEntryId={targetEntryId} worldId={worldId}
          api={loreApi} draftStore={draftStore}
          onOpenSource={target => setViewingDoc({ documentId: target.documentId, mode: 'raw',
            source: { chunkId: target.chunkId, chunkIndex: target.chunkIndex, startChar: target.startChar, endChar: target.endChar } })} />}
        {docSection === 'upload' && <DocumentUpload worldId={worldId} onSuccess={handleUploadSuccess}
          onCancel={openDocuments} {...uploadPolicy}
          onViewExisting={id => setViewingDoc({ documentId: id, mode: 'raw' })}
          onRefreshRequest={() => void loadWorld()} />}
        {preparing && <button className="primary" type="button" onClick={() => {
          try { localStorage.removeItem(`mojing:prepare-world:${worldId}`); } catch { /* Optional preference. */ }
          setPreparing(false); setDocSection('list'); navigate('journal', true);
        }}>完成准备，进入世界</button>}
      </section>

      <div hidden={tab !== 'journal'}>
        <AdventureJournal worldId={worldId} sessionId={modelSession?.session_id}
          view={journalPage} onViewChange={view => { setJournalPage(view); window.scrollTo({ top: 0, behavior: 'instant' }); }}
          onActiveRunChange={setActiveRun}
          onConfigure={() => navigate('settings')}
          onSessionExpired={() => onModelSessionChange?.(null)}
          onOpenSource={source => {
            if (source.kind === 'entry') openEntry(source.entry_id);
            else if (source.document_id) setViewingDoc({ documentId: source.document_id, mode: 'raw',
              source: { chunkId: source.chunk_id, chunkIndex: source.chunk_index } });
          }} />
      </div>

      <section hidden={tab !== 'settings'} aria-label="世界设置" className="workspace-settings">
        <h2>模型连接</h2>
        <NarrationPreview worldId={worldId} api={narrationApi} initialMode="live" connectionOnly
          initialSession={modelSession} onSessionChange={onModelSessionChange} />
        {activeRun && <section aria-label="当前旅程的世界脉动">
          <h2>世界脉动 <small className="muted">· {activeRun.name}</small></h2>
          {activeRun.mode === 'live'
            ? <WorldPulsePanel key={activeRun.id} adventureId={activeRun.id}
                narrationSessionId={modelSession?.session_id} narrationModel={modelSession?.model} defaultOpen={false} />
            : tab === 'settings' && <PulsePreview adventureId={activeRun.id} />}
        </section>}
        {!activeRun && <section><h2>世界脉动</h2><p className="muted">开启旅程后，可在这里设置该旅程的世界脉动。</p></section>}
        {preferences && onPreferencesChange && <Preferences embedded state={preferences}
          update={onPreferencesChange} back={() => navigate('journal')} />}
      </section>

      {viewingDoc && <DocumentViewer key={viewingDoc.documentId}
        documentId={viewingDoc.documentId} initialMode={viewingDoc.mode || 'raw'}
        initialSource={viewingDoc.source} onClose={() => setViewingDoc(null)}
        onSaved={saved => {
          setWorldDetail(current => current && { ...current,
            documents: current.documents.map(doc => doc.id === saved.id ? saved : doc) });
          setQuery('');
          setSearchVersion(value => value + 1);
        }} />}
    </div>
  );
}

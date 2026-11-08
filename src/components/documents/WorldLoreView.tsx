import { useState, useEffect, useRef } from 'react';
import {
  Upload,
  BookOpen,
  Search,
  ClipboardList,
  FlaskConical,
  Info,
  Compass,
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
  testModelSession,
  previewNarration,
} from '../../api/client';
import './documents.css';
import { getUploadPolicy, toLoreSearchItem } from './loreAdapters';

export interface WorldLoreViewProps {
  worldId: string;
  worldName: string;
  onBack: () => void;
  onPlayDemo?: () => void;
}

type Tab = 'list' | 'search' | 'entries' | 'narration' | 'journal' | 'upload';

export function WorldLoreView({
  worldId,
  worldName,
  onBack,
  onPlayDemo,
}: WorldLoreViewProps) {
  const [worldDetail, setWorldDetail] = useState<WorldDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('list');
  const [modelSession, setModelSession] = useState<ModelSessionResponse | null>(null);
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

  // M0 降级试玩确认弹窗 (契约 §7.1)
  const [showDemoConfirm, setShowDemoConfirm] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
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
        eyebrow="WORLD LORE ARCHIVE"
        title={worldDetail?.name || worldName}
        description={worldDetail?.description || '已持久化的世界资料库，包含已导入的设定集与章节切片。'}
        action={<Tag>世界资料库 · 已就绪</Tag>}
      />

      {/* 契约 §7.1 核心要求：诚实 UI 呈现横幅 */}
      <div className="lore-honest-banner" role="note">
        <Info size={18} />
        <div>
          <strong>【世界资料库模式】</strong>
          资料已持久化。可在“叙事实验”测试模型草案；正式冒险与角色状态提交尚未开启。
        </div>
      </div>

      {/* 契约 §7.1 降级体验入口 */}
      {onPlayDemo && (
        <div className="lore-demo-bar">
          <div className="flex items-center gap-2">
            <Compass size={16} />
            <span>想试试交互？可开启本地模拟试玩（不会使用本世界的设定）。</span>
          </div>
          <button
            type="button"
            className="lore-demo-btn"
            onClick={() => setShowDemoConfirm(true)}
          >
            以演示模式试玩 (M0 体验)
          </button>
        </div>
      )}

      {/* 切换 Tab */}
      <div className="tabs" style={{ marginTop: 0 }}>
        <button
          type="button"
          className={tab === 'list' ? 'active' : ''}
          onClick={() => setTab('list')}
        >
          <BookOpen size={15} /> 已导入资料 ({documents.length})
        </button>
        <button
          type="button"
          className={tab === 'search' ? 'active' : ''}
          onClick={() => setTab('search')}
        >
          <Search size={15} /> 检索资料
        </button>
        <button
          type="button"
          className={tab === 'entries' ? 'active' : ''}
          onClick={() => setTab('entries')}
        >
          <ClipboardList size={15} /> 人工设定
        </button>
        <button
          type="button"
          className={tab === 'narration' ? 'active' : ''}
          onClick={() => setTab('narration')}
        >
          <FlaskConical size={15} /> 叙事实验
        </button>
        <button type="button" className={tab === 'journal' ? 'active' : ''} onClick={() => setTab('journal')}>冒险记录</button>
        <button
          type="button"
          className={tab === 'upload' ? 'active' : ''}
          onClick={() => setTab('upload')}
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
          onSessionChange={setModelSession}
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

      {tab === 'journal' && <AdventureJournal worldId={worldId} sessionId={modelSession?.session_id}
        onConfigure={() => setTab('narration')}
        onOpenSource={source => {
          if (source.kind === 'entry') { setTargetEntryId(source.entry_id); setTab('entries'); }
          else if (source.document_id) setViewingDoc({documentId:source.document_id,mode:'chunks',source:{chunkId:source.chunk_id,chunkIndex:source.chunk_index}});
        }} />}

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

      {/* 契约 §7.1 降级试玩确认对话框 */}
      {showDemoConfirm && (
        <div className="modal-backdrop" onClick={() => setShowDemoConfirm(false)}>
          <div
            className="modal"
            style={{ maxWidth: '420px' }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ fontSize: '18px', marginBottom: '12px' }}>开启演示模式试玩？</h3>
            <p className="muted text-xs leading-relaxed mb-4">
              当前剧情为本地关键词模拟演示，不会根据您导入的文档内容生成回答。是否继续？
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className="primary full"
                onClick={() => {
                  setShowDemoConfirm(false);
                  onPlayDemo?.();
                }}
              >
                确认进入试玩
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setShowDemoConfirm(false)}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


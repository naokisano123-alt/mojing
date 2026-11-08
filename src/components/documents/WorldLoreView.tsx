import { useState, useEffect } from 'react';
import {
  Upload,
  BookOpen,
  Search,
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
import './documents.css';

export interface WorldLoreViewProps {
  worldId: string;
  worldName: string;
  onBack: () => void;
  onPlayDemo?: () => void;
}

type Tab = 'list' | 'search' | 'upload';

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
  const [formats, setFormats] = useState<string[]>(['.txt', '.md']);

  // 当前打开查看器的文档（可带来源区间，用于检索回跳高亮）
  const [viewingDoc, setViewingDoc] = useState<{
    documentId: string;
    mode?: 'chunks' | 'raw';
    source?: { chunkIndex?: number; startChar?: number; endChar?: number };
  } | null>(null);

  // M0 降级试玩确认弹窗 (契约 §7.1)
  const [showDemoConfirm, setShowDemoConfirm] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setViewingDoc(null);
    void loadWorld(controller.signal);
    return () => controller.abort();
  }, [worldId]);

  // 能力探测：只启用后端实际可用的格式（契约 M1_R2_API §1.7）
  useEffect(() => {
    let alive = true;
    getCapabilities()
      .then(caps => {
        if (alive) setFormats(caps.formats);
      })
      .catch(() => {
        if (alive) setFormats(['.txt', '.md']);
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
      items: result.items.map(item => ({
        world_id: item.world_id,
        document_id: item.document_id,
        chunk_id: item.chunk_id,
        chunk_index: item.chunk_index,
        filename: item.document_filename,
        heading_path: item.heading_path,
        start_char: item.start_char,
        end_char: item.end_char,
        snippet: item.snippet,
        match_field: 'text' as const,
        match_start_char: item.start_char + (item.match_start ?? 0),
        match_end_char: item.start_char + (item.match_end ?? 0),
        match_chunk_start_char: item.match_start ?? 0,
        match_chunk_end_char: item.match_end ?? 0,
        match_coords_scope: 'chunk',
      })),
    };
  }

  const documents = worldDetail?.documents || [];
  const supportsRichFormats = formats.includes('.pdf') || formats.includes('.docx');

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
          设定已导入并持久化；AI 角色与剧情生成将在 M2 开启。当前可检索资料、浏览章节切片、核对原文。
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

      {tab === 'upload' && (
        <DocumentUpload
          worldId={worldId}
          onSuccess={handleUploadSuccess}
          onCancel={() => setTab('list')}
          supportedFormats={formats}
          maxBytes={supportsRichFormats ? 8 * 1024 * 1024 : 2 * 1024 * 1024}
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

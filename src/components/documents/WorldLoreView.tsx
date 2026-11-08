import { useState, useEffect } from 'react';
import {
  FileText,
  Upload,
  BookOpen,
  Info,
  Compass,
  AlertCircle,
} from 'lucide-react';
import type { WorldDetail, WorldDocument } from '../../api/types';
import { getWorld, deleteDocument, ApiClientError } from '../../api/client';
import { Back, PageTitle, Tag } from '../UI';
import { DocumentUpload } from './DocumentUpload';
import { DocumentList } from './DocumentList';
import { DocumentViewer } from './DocumentViewer';
import './documents.css';

export interface WorldLoreViewProps {
  worldId: string;
  worldName: string;
  onBack: () => void;
  onPlayDemo?: () => void;
}

export function WorldLoreView({
  worldId,
  worldName,
  onBack,
  onPlayDemo,
}: WorldLoreViewProps) {
  const [worldDetail, setWorldDetail] = useState<WorldDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'list' | 'upload'>('list');

  // 当前打开查看器的文档
  const [viewingDoc, setViewingDoc] = useState<{ doc: WorldDocument; mode?: 'chunks' | 'raw' } | null>(null);

  // M0 降级试玩确认弹窗 (契约 §7.1)
  const [showDemoConfirm, setShowDemoConfirm] = useState(false);

  useEffect(() => {
    loadWorld();
  }, [worldId]);

  async function loadWorld() {
    setLoading(true);
    setError(null);
    try {
      const data = await getWorld(worldId);
      setWorldDetail(data);
      setLoading(false);
    } catch (err: unknown) {
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
      // 局部刷新或重新拉取世界详情
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
          设定已导入并持久化；AI 角色与剧情生成将在 M2 开启。当前可检索设定、浏览章节切片并核对原文。
        </div>
      </div>

      {/* 契约 §7.1 降级体验入口 */}
      {onPlayDemo && (
        <div className="lore-demo-bar">
          <div className="flex items-center gap-2">
            <Compass size={16} />
            <span>想在此世界观下体验交互？可开启本地模拟试玩。</span>
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
              onClick={loadWorld}
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
          onSelectDocument={(doc, mode) => setViewingDoc({ doc, mode })}
          onDeleteDocument={handleDeleteDocument}
        />
      )}

      {tab === 'upload' && (
        <DocumentUpload
          worldId={worldId}
          onSuccess={handleUploadSuccess}
          onCancel={() => setTab('list')}
        />
      )}

      {/* 文档切片与原文查看器 */}
      {viewingDoc && (
        <DocumentViewer
          documentId={viewingDoc.doc.id}
          initialMode={viewingDoc.mode || 'chunks'}
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

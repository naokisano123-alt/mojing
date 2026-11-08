import { useState } from 'react';
import {
  FileText,
  Trash2,
  Layers,
  BookOpen,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import type { WorldDocument } from '../../api/types';
import { Tag } from '../UI';

export interface DocumentListProps {
  documents: WorldDocument[];
  onSelectDocument: (doc: WorldDocument, mode?: 'chunks' | 'raw') => void;
  onDeleteDocument: (documentId: string) => Promise<void>;
  loading?: boolean;
}

export function DocumentList({
  documents,
  onSelectDocument,
  onDeleteDocument,
  loading = false,
}: DocumentListProps) {
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDeleteDoc, setConfirmDeleteDoc] = useState<WorldDocument | null>(null);

  async function handleConfirmDelete() {
    if (!confirmDeleteDoc) return;
    setDeletingId(confirmDeleteDoc.id);
    try {
      await onDeleteDocument(confirmDeleteDoc.id);
      setConfirmDeleteDoc(null);
    } finally {
      setDeletingId(null);
    }
  }

  function formatTime(isoStr: string) {
    try {
      const d = new Date(isoStr);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    } catch {
      return isoStr;
    }
  }

  return (
    <div className="document-list-container">
      {loading && (
        <div className="empty" style={{ padding: '30px 0' }}>
          <div className="status-dot" style={{ width: '10px', height: '10px' }} />
          <p>正在读取已导入文档...</p>
        </div>
      )}

      {!loading && documents.length === 0 && (
        <div className="empty panel" style={{ padding: '40px 20px' }}>
          <BookOpen size={32} style={{ color: 'var(--muted)' }} />
          <h3>尚未导入世界观资料</h3>
          <p>导入 TXT 或 Markdown 设定集后，系统将自动识别章节与切片。</p>
        </div>
      )}

      {!loading && documents.map(doc => {
        const isReady = doc.status === 'ready';
        const isProcessing = doc.status === 'processing';
        const isFailed = doc.status === 'failed';

        return (
          <article key={doc.id} className="panel doc-card">
            <div className="doc-card-header">
              <div className="doc-title-row">
                <FileText size={20} style={{ color: 'var(--green)' }} />
                <h3 title={doc.filename}>{doc.filename}</h3>
              </div>
              <span
                className={`doc-status-badge ${
                  isReady ? 'status-ready' : isProcessing ? 'status-processing' : 'status-failed'
                }`}
              >
                {isReady ? '已入库' : isProcessing ? '解析中' : '解析失败'}
              </span>
            </div>

            <div className="doc-meta-row">
              <Tag>{doc.encoding.toUpperCase()}</Tag>
              <span>{(doc.byte_size / 1024).toFixed(1)} KB</span>
              <span>{doc.line_count} 行</span>
              <span>{doc.chunk_count} 个章节切片</span>
              <span className="flex items-center gap-1">
                <Clock size={12} /> {formatTime(doc.created_at)}
              </span>
            </div>

            <div className="doc-actions">
              <button
                type="button"
                className="text-button"
                style={{ fontWeight: 500 }}
                onClick={() => onSelectDocument(doc, 'chunks')}
              >
                <Layers size={14} /> 查看章节切片
              </button>
              <button
                type="button"
                className="text-button"
                style={{ color: 'var(--muted)' }}
                onClick={() => onSelectDocument(doc, 'raw')}
              >
                <BookOpen size={14} /> 原文分页
              </button>
              <div style={{ marginLeft: 'auto' }}>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`删除文档 ${doc.filename}`}
                  title="删除文档"
                  style={{ minWidth: '34px', minHeight: '34px' }}
                  onClick={() => setConfirmDeleteDoc(doc)}
                >
                  <Trash2 size={15} style={{ color: '#ba6452' }} />
                </button>
              </div>
            </div>
          </article>
        );
      })}

      {confirmDeleteDoc && (
        <div className="modal-backdrop" onClick={() => setConfirmDeleteDoc(null)}>
          <div
            className="modal"
            style={{ maxWidth: '400px' }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-3" style={{ color: '#ba6452' }}>
              <AlertTriangle size={20} />
              <h3 style={{ fontSize: '17px' }}>确认删除此文档？</h3>
            </div>
            <p className="muted text-xs leading-relaxed mb-4">
              文档「{confirmDeleteDoc.filename}」将被软删除，其生成的 {confirmDeleteDoc.chunk_count} 个切片将不再出现在设定列表中。
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className="primary full"
                style={{ background: '#ba6452' }}
                disabled={deletingId === confirmDeleteDoc.id}
                onClick={handleConfirmDelete}
              >
                {deletingId === confirmDeleteDoc.id ? '正在删除...' : '确认删除'}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setConfirmDeleteDoc(null)}
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

import { useState } from 'react';
import {
  FileText,
  Trash2,
  Layers,
  BookOpen,
  MoreHorizontal,
  AlertTriangle,
} from 'lucide-react';
import type { WorldDocument } from '../../api/types';

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
          <p>导入你的世界设定，或添加人工设定。</p>
        </div>
      )}

      {!loading && documents.map(doc => {
        const isReady = doc.status === 'ready';
        const isProcessing = doc.status === 'processing';
        const isFailed = doc.status === 'failed';

        return (
          <article key={doc.id} className="panel doc-card">
            <div className="doc-card-header">
              <button type="button" className="document-open" onClick={() => onSelectDocument(doc, 'raw')}>
                <FileText size={20} /><span><strong>{doc.filename}</strong>
                  <small className="muted">{doc.text_length.toLocaleString()} 字符 · {formatTime(doc.updated_at)}</small>
                </span>
              </button>
              <details className="document-menu">
                <summary aria-label={`文档操作 ${doc.filename}`}><MoreHorizontal size={20} /></summary>
                <div>
                  <button type="button" onClick={() => onSelectDocument(doc, 'chunks')}><Layers size={15} />章节索引</button>
                  <button type="button" onClick={() => setConfirmDeleteDoc(doc)}><Trash2 size={15} />删除文档</button>
                </div>
              </details>
            </div>
            {!isReady && <p className="muted">{isProcessing ? "正在解析…" : isFailed ? "解析失败" : "准备中"}</p>}
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

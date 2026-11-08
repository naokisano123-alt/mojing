import { useState, useRef, useEffect } from 'react';
import { Upload, FileText, AlertCircle, CheckCircle2, X, RefreshCw } from 'lucide-react';
import type { WorldDocument } from '../../api/types';
import { uploadDocument, ApiClientError } from '../../api/client';
import { Tag } from '../UI';

export interface DocumentUploadProps {
  worldId: string;
  onSuccess: (document: WorldDocument) => void;
  onCancel?: () => void;
}

const MAX_FILE_BYTES = 2 * 1024 * 1024; // 2 MiB (契约 §4.7)

export function DocumentUpload({ worldId, onSuccess, onCancel }: DocumentUploadProps) {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [successDoc, setSuccessDoc] = useState<WorldDocument | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      // 组件卸载时取消未完成请求 (契约 §8.4)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  function validateFile(file: File): string | null {
    if (!/\.(txt|md)$/i.test(file.name)) {
      return '仅支持导入 .txt 或 .md 格式的纯文本设定文档。';
    }
    if (file.size === 0) {
      return '文件内容为空，无法导入。';
    }
    if (file.size > MAX_FILE_BYTES) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      return `文件大小为 ${mb} MB，超出单文件 2 MiB 上限。`;
    }
    return null;
  }

  function handleFileSelected(file: File) {
    setError(null);
    setErrorCode(null);
    setSuccessDoc(null);
    setAllowDuplicate(false);

    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      setSelectedFile(null);
      return;
    }
    setSelectedFile(file);
  }

  async function handleStartUpload(overrideAllowDuplicate?: boolean) {
    if (!selectedFile || uploading) return;

    setUploading(true);
    setProgress(0);
    setError(null);
    setErrorCode(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const useAllowDuplicate = overrideAllowDuplicate ?? allowDuplicate;

    try {
      const doc = await uploadDocument(worldId, selectedFile, {
        allow_duplicate: useAllowDuplicate,
        onProgress: pct => setProgress(pct),
        signal: controller.signal,
      });

      setSuccessDoc(doc);
      setUploading(false);
      setProgress(100);
      onSuccess(doc);
    } catch (err: unknown) {
      setUploading(false);
      if (err instanceof ApiClientError) {
        setErrorCode(err.code);
        setError(err.friendlyMessage);
      } else {
        setError('上传失败，请检查网络后重试。');
      }
    } finally {
      abortControllerRef.current = null;
    }
  }

  function handleCancelUpload() {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setUploading(false);
    setProgress(0);
    setError('上传已取消。');
  }

  function resetState() {
    setSelectedFile(null);
    setSuccessDoc(null);
    setError(null);
    setErrorCode(null);
    setProgress(0);
    setAllowDuplicate(false);
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <div className="panel upload-panel">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3>导入世界观资料</h3>
          <p className="muted text-xs mt-1">
            支持 UTF-8 / GB18030 中文纯文本及 Markdown
          </p>
        </div>
        <Tag>M1 · 资料库</Tag>
      </div>

      {!selectedFile && (
        <div
          className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
          onDragOver={e => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={e => {
            e.preventDefault();
            setDragActive(false);
            const file = e.dataTransfer.files?.[0];
            if (file) handleFileSelected(file);
          }}
          onClick={() => inputRef.current?.click()}
        >
          <Upload size={32} />
          <h3>点击或拖拽文件到此处</h3>
          <p>单文件最大 2 MiB · 自动按章节断块与字符定位</p>
          <div className="upload-limits">
            <Tag>.txt 纯文本</Tag>
            <Tag>.md Markdown</Tag>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".txt,.md"
            style={{ display: 'none' }}
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) handleFileSelected(file);
            }}
          />
        </div>
      )}

      {selectedFile && (
        <div className="selected-file-box">
          <div className="file-row">
            <FileText size={20} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <strong style={{ fontSize: '13px', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {selectedFile.name}
              </strong>
              <span className="muted text-xs">
                {(selectedFile.size / 1024).toFixed(1)} KB
              </span>
            </div>
            {!uploading && !successDoc && (
              <button
                type="button"
                className="icon-button"
                aria-label="移除文件"
                onClick={resetState}
              >
                <X size={16} />
              </button>
            )}
          </div>

          {uploading && (
            <div className="upload-progress-box">
              <div className="upload-progress-header">
                <span>{progress < 100 ? `正在上传与解析 (${progress}%)` : '正在分块入库...'}</span>
                <button
                  type="button"
                  className="text-button"
                  style={{ color: '#ba6452' }}
                  onClick={handleCancelUpload}
                >
                  取消
                </button>
              </div>
              <div className="upload-progress-track">
                <div
                  className="upload-progress-fill"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {successDoc && (
            <div
              className="flex items-center gap-2 mt-3"
              style={{ color: 'var(--green)', fontSize: '12px' }}
            >
              <CheckCircle2 size={16} />
              <span>导入成功！已生成 {successDoc.chunk_count} 个章节切片（{successDoc.encoding}）。</span>
            </div>
          )}

          {error && (
            <div className="upload-error-callout" role="alert">
              <AlertCircle size={16} />
              <div style={{ flex: 1 }}>
                <div>{error}</div>
                {errorCode === 'DUPLICATE_DOCUMENT' && !allowDuplicate && (
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="primary"
                      style={{ fontSize: '11px', minHeight: '32px', padding: '4px 10px' }}
                      onClick={() => {
                        setAllowDuplicate(true);
                        handleStartUpload(true);
                      }}
                    >
                      <RefreshCw size={12} /> 允许重复并强制导入
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="flex gap-2 mt-4">
            {!uploading && !successDoc && (
              <>
                <button
                  type="button"
                  className="primary full"
                  onClick={() => handleStartUpload()}
                >
                  确认导入
                </button>
                {onCancel && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={onCancel}
                  >
                    取消
                  </button>
                )}
              </>
            )}
            {successDoc && (
              <button
                type="button"
                className="secondary full"
                onClick={resetState}
              >
                继续导入下一份文档
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

import { useState, useRef, useEffect } from 'react';
import {
  Upload,
  FileText,
  AlertCircle,
  CheckCircle2,
  X,
  RefreshCw,
  Info,
  Eye,
} from 'lucide-react';
import type { WorldDocument } from '../../api/types';
import { uploadDocument, ApiClientError } from '../../api/client';
import { Tag } from '../UI';

export interface DocumentUploadProps {
  worldId: string;
  onSuccess: (document: WorldDocument) => void;
  onCancel?: () => void;
  /** 可选：发生 409 重复导入时，点击跳转查看已有文档 */
  onViewExisting?: (documentId: string) => void;
  /** 可选：请求外部刷新文档列表（例如取消上传后建议核对） */
  onRefreshRequest?: () => void;
  /** 可选支持的格式扩展名，默认 ['.txt', '.md']，严禁默认启用未接通的格式 */
  supportedFormats?: string[];
  /** 可选：单文件大小全局默认上限（字节），默认 2 MiB；当未传入 maxBytesByFormat 或未匹配时兜底 */
  maxBytes?: number;
  /**
   * 可选：逐格式单文件大小上限映射表（字节），例如 { '.txt': 2097152, '.pdf': 8388608 }。
   * 修复 B1 / B2：按具体扩展名动态判定上限，不再粗暴用单一格式决定全部格式上限。
   */
  maxBytesByFormat?: Record<string, number>;
}

const DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024; // 2 MiB 默认保底 (契约 §4.7)

function formatBytes(bytes: number): string {
  const mib = bytes / (1024 * 1024);
  if (Number.isInteger(mib)) {
    return `${mib} MiB`;
  }
  return `${mib.toFixed(1)} MiB`;
}

export function DocumentUpload({
  worldId,
  onSuccess,
  onCancel,
  onViewExisting,
  onRefreshRequest,
  supportedFormats = ['.txt', '.md'],
  maxBytes = DEFAULT_MAX_FILE_BYTES,
  maxBytesByFormat,
}: DocumentUploadProps) {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  // 状态明确隔离：真实错误 vs 中性取消状态
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [existingDocId, setExistingDocId] = useState<string | null>(null);
  const [isCancelled, setIsCancelled] = useState(false);

  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [successDoc, setSuccessDoc] = useState<WorldDocument | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      // 组件卸载时安全取消未完成请求 (契约 §8.4)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  function getMaxBytesForExt(ext: string): number {
    if (maxBytesByFormat) {
      const lowerExt = ext.toLowerCase();
      for (const [key, val] of Object.entries(maxBytesByFormat)) {
        if (key.toLowerCase() === lowerExt && typeof val === 'number') {
          return val;
        }
      }
    }
    return maxBytes;
  }

  function validateFile(file: File): string | null {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    const isSupported = supportedFormats.some(f => f.toLowerCase() === ext);
    if (!isSupported) {
      return `不支持该文件格式。仅支持导入 ${supportedFormats.join('、')} 设定文档。`;
    }
    if (file.size === 0) {
      return '文件内容为空，无法导入。';
    }

    const formatLimit = getMaxBytesForExt(ext);
    if (file.size > formatLimit) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      const limitLabel = formatBytes(formatLimit);
      return `文件大小为 ${mb} MB，超出 ${ext} 格式 ${limitLabel} 上限。`;
    }
    return null;
  }

  function handleFileSelected(file: File) {
    setError(null);
    setErrorCode(null);
    setExistingDocId(null);
    setIsCancelled(false);
    setSuccessDoc(null);
    setAllowDuplicate(false);

    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      setSelectedFile(null);
      // 清空 input 值，保证重新选取相同文件时仍能触发 onChange
      if (inputRef.current) inputRef.current.value = '';
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
    setExistingDocId(null);
    setIsCancelled(false);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    const useAllowDuplicate = overrideAllowDuplicate ?? allowDuplicate;

    try {
      const doc = await uploadDocument(worldId, selectedFile, {
        allow_duplicate: useAllowDuplicate,
        onProgress: pct => {
          if (isMountedRef.current) {
            setProgress(pct);
          }
        },
        signal: controller.signal,
      });

      if (!isMountedRef.current) return;
      setSuccessDoc(doc);
      setUploading(false);
      setProgress(100);
      onSuccess(doc);
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      setUploading(false);

      if (err instanceof ApiClientError) {
        if (err.code === 'ABORT_ERROR') {
          // 显式捕获取消动作，作为中性状态呈现，不当作网络崩溃或服务器错误
          setIsCancelled(true);
          return;
        }

        setErrorCode(err.code);
        setError(err.friendlyMessage);

        if (err.code === 'DUPLICATE_DOCUMENT') {
          const dupId = err.details?.existing_document_id as string | undefined;
          if (dupId) setExistingDocId(dupId);
        }
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
    setIsCancelled(true);
  }

  function resetState() {
    setSelectedFile(null);
    setSuccessDoc(null);
    setError(null);
    setErrorCode(null);
    setExistingDocId(null);
    setIsCancelled(false);
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
            支持设定文档导入，自动按章节切片与坐标定位
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
          onClick={() => {
            if (inputRef.current) {
              inputRef.current.value = ''; // 允许再次选取同一个文件
              inputRef.current.click();
            }
          }}
        >
          <Upload size={32} />
          <h3>点击或拖拽文件到此处</h3>
          <p>单文件按格式上限受限 · 自动按章节断块与字符定位</p>
          <div className="upload-limits">
            {supportedFormats.map(fmt => {
              const limit = getMaxBytesForExt(fmt);
              return (
                <Tag key={fmt}>{fmt} (≤{formatBytes(limit)})</Tag>
              );
            })}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={supportedFormats.join(',')}
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

          {/* 上传进度与阶段精确展示 */}
          {uploading && (
            <div className="upload-progress-box">
              <div className="upload-progress-header">
                <span>
                  {progress < 100
                    ? `正在上传文件 (${progress}%)`
                    : '文件上传完成，服务端正在解析与分块入库，请稍候...'}
                </span>
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

          {/* 成功状态 */}
          {successDoc && (
            <div
              className="flex items-center gap-2 mt-3"
              style={{ color: 'var(--green)', fontSize: '12px' }}
            >
              <CheckCircle2 size={16} />
              <span>导入成功！已生成 {successDoc.chunk_count} 个章节切片（{successDoc.encoding}）。</span>
            </div>
          )}

          {/* 中性取消提示横幅（非破坏性红字错误） */}
          {isCancelled && !uploading && !successDoc && (
            <div className="upload-neutral-callout" role="status">
              <Info size={16} />
              <div style={{ flex: 1 }}>
                <div>上传已取消。</div>
                <div className="muted text-xs mt-1">
                  注意：若服务端已在处理入库，数据可能仍已保存，建议刷新列表核对。
                </div>
                <div className="flex gap-2 mt-2">
                  <button
                    type="button"
                    className="secondary"
                    style={{ fontSize: '11px', minHeight: '30px', padding: '3px 10px' }}
                    onClick={() => handleStartUpload()}
                  >
                    重新上传该文件
                  </button>
                  {onRefreshRequest && (
                    <button
                      type="button"
                      className="text-button"
                      style={{ fontSize: '11px' }}
                      onClick={onRefreshRequest}
                    >
                      刷新列表核对
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* 真实错误提示与重复文件处理 */}
          {error && (
            <div className="upload-error-callout" role="alert">
              <AlertCircle size={16} />
              <div style={{ flex: 1 }}>
                <div>{error}</div>
                {errorCode === 'DUPLICATE_DOCUMENT' && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {existingDocId && onViewExisting && (
                      <button
                        type="button"
                        className="primary"
                        style={{ fontSize: '11px', minHeight: '32px', padding: '4px 12px' }}
                        onClick={() => onViewExisting(existingDocId)}
                      >
                        <Eye size={13} /> 查看已有文档
                      </button>
                    )}
                    {!allowDuplicate && (
                      <button
                        type="button"
                        className="secondary"
                        style={{ fontSize: '11px', minHeight: '32px', padding: '4px 10px' }}
                        onClick={() => {
                          setAllowDuplicate(true);
                          handleStartUpload(true);
                        }}
                      >
                        <RefreshCw size={12} /> 允许重复并强制导入
                      </button>
                    )}
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

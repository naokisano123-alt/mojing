import { useEffect, useRef, useState } from 'react';
import { FileText, Upload, X } from 'lucide-react';
import { ApiClientError, uploadDocument } from '../../api/client';
import type { WorldDocument } from '../../api/types';
import { Tag } from '../UI';

export interface DocumentUploadProps {
  worldId: string;
  onSuccess: (document: WorldDocument) => void;
  onCancel?: () => void;
  onViewExisting?: (documentId: string) => void;
  onRefreshRequest?: () => void;
  supportedFormats?: string[];
  maxBytes?: number;
  maxBytesByFormat?: Record<string, number>;
}

type Result =
  | { kind: 'done'; chunks: number }
  | { kind: 'failed'; message: string; existingId?: string; duplicate?: boolean }
  | { kind: 'invalid'; message: string };

interface SelectedFile {
  file: File;
  result?: Result;
}

const BATCH_LIMIT = 20;
const DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024;

function formatBytes(bytes: number) {
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
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
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [active, setActive] = useState<{ index: number; progress: number } | null>(null);
  const [selectionError, setSelectionError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => () => {
    const controller = controllerRef.current;
    controllerRef.current = null;
    controller?.abort();
  }, []);

  function maxFor(format: string) {
    return maxBytesByFormat?.[format] ?? maxBytes;
  }

  function validate(file: File) {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!supportedFormats.includes(ext)) return '不支持该文件格式。';
    if (!file.size) return '文件内容为空，无法导入。';
    if (file.size > maxFor(ext)) return `超出 ${ext} 格式的 ${formatBytes(maxFor(ext))} 上限。`;
    return '';
  }

  function select(selection: FileList | null) {
    if (controllerRef.current || !selection?.length) return;
    if (selection.length > BATCH_LIMIT) {
      setSelectionError(`一次最多选择 ${BATCH_LIMIT} 个文件，请分批选择。`);
      return;
    }
    setSelectionError('');
    setCancelled(false);
    setFiles(Array.from(selection, file => {
      const message = validate(file);
      return message ? { file, result: { kind: 'invalid', message } } : { file };
    }));
  }

  async function start(indices: number[], allowDuplicate = false) {
    if (controllerRef.current || !indices.length) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setCancelled(false);

    try {
      for (const index of indices) {
        if (controller.signal.aborted) break;
        setActive({ index, progress: 0 });
        let result: Result;
        try {
          const doc = await uploadDocument(worldId, files[index].file, {
            allow_duplicate: allowDuplicate,
            signal: controller.signal,
            onProgress: progress => {
              if (!controller.signal.aborted) setActive({ index, progress });
            },
          });
          if (controller.signal.aborted) break;
          onSuccess(doc);
          result = { kind: 'done', chunks: doc.chunk_count };
        } catch (error) {
          if (controller.signal.aborted) break;
          result = error instanceof ApiClientError ? {
            kind: 'failed',
            message: error.friendlyMessage,
            duplicate: error.code === 'DUPLICATE_DOCUMENT',
            existingId: typeof error.details?.existing_document_id === 'string' ? error.details.existing_document_id : undefined,
          } : { kind: 'failed', message: '上传失败，请重试。' };
        }
        setFiles(current => current.map((item, row) => row === index ? { ...item, result } : item));
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setActive(null);
      }
    }
  }

  const pending = files.flatMap((item, index) => item.result ? [] : [index]);
  const successful = files.filter(item => item.result?.kind === 'done').length;
  const unsuccessful = files.length - pending.length - successful;

  return (
    <div className="panel upload-panel">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3>导入世界观资料</h3>
          <p className="muted text-xs mt-1">一次最多 {BATCH_LIMIT} 份，按文件依次导入和切片</p>
        </div>
        <Tag>M1 · 资料库</Tag>
      </div>
      <input ref={inputRef} type="file" multiple hidden aria-label="选择世界观资料"
        accept={supportedFormats.join(',')} onChange={event => {
          select(event.currentTarget.files);
          event.currentTarget.value = '';
        }} />
      {!files.length && <button type="button" className="upload-dropzone full"
        onDragOver={event => event.preventDefault()}
        onDrop={event => { event.preventDefault(); select(event.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}>
        <Upload size={26} />
        <strong>选择或拖入文件</strong>
        <span className="upload-limits">
          {supportedFormats.map(format => <Tag key={format}>{format} ≤ {formatBytes(maxFor(format))}</Tag>)}
        </span>
      </button>}
      {selectionError && <p role="alert" className="upload-error-callout">{selectionError}</p>}
      {!!files.length && <>
        <p role="status" className="muted text-sm">
          共 {files.length} 份 · 成功 {successful} · 未导入 {unsuccessful} · 待处理 {pending.length}
        </p>
        <ul className="grid gap-2 mt-3">
          {files.map(({ file, result }, index) => (
            <li key={index} className="selected-file-box">
              <div className="file-row">
                <FileText size={17} aria-hidden="true" />
                <strong className="min-w-0 flex-1 break-all text-sm">{file.name}</strong>
                {!active && !result && <button type="button" className="icon-button" aria-label={`移除 ${file.name}`}
                  onClick={() => setFiles(current => current.filter((_, row) => row !== index))}><X size={16} /></button>}
              </div>
              {active?.index === index && <div className="upload-progress-box">
                <p className="muted text-xs">{active.progress < 100 ? `上传中 ${active.progress}%` : '正在解析并保存…'}</p>
                <progress className="w-full" max={100} value={active.progress} aria-label={`${file.name} 上传进度`} />
              </div>}
              {result?.kind === 'done' && <p className="muted text-xs">已导入 · {result.chunks} 个切片</p>}
              {result && result.kind !== 'done' && <div className="text-sm">
                <p>{result.message}</p>
                {!active && result.kind === 'failed' && <div className="flex flex-wrap gap-2 mt-2">
                  {result.existingId && <button type="button" className="text-button"
                    onClick={() => onViewExisting?.(result.existingId!)}>查看已有文档</button>}
                  <button type="button" className="secondary" onClick={() => void start([index], result.duplicate)}>
                    {result.duplicate ? '仍然导入这份文件' : '重试这份文件'}
                  </button>
                </div>}
              </div>}
            </li>
          ))}
        </ul>
        {cancelled && <div role="status" className="upload-neutral-callout mt-3">
          <span>已停止本批。已导入的文件会保留，正在解析的文件可能已保存。</span>
          {onRefreshRequest && <button type="button" className="text-button" onClick={onRefreshRequest}>刷新列表核对</button>}
        </div>}
        <div className="flex flex-wrap gap-2 mt-4">
          {active ? <button type="button" className="secondary" onClick={() => {
            controllerRef.current?.abort();
            setCancelled(true);
          }}>停止导入</button> : <>
            {!!pending.length && <button type="button" className="primary" onClick={() => void start(pending)}>
              导入 {pending.length} 份资料
            </button>}
            <button type="button" className="secondary" onClick={() => inputRef.current?.click()}>重新选择</button>
            {onCancel && <button type="button" className="secondary" onClick={onCancel}>返回资料列表</button>}
          </>}
        </div>
      </>}
    </div>
  );
}

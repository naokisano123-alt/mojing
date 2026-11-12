import { useState } from 'react';
import { Upload } from 'lucide-react';
import { ApiClientError, importWorldFiles } from '../../api/client';
import type { WorldSummary } from '../../api/types';

export function WorldTransferImport({ onImported, embedded = false, onBusyChange, targetWorldId }: {
  onImported: (world: WorldSummary) => void;
  embedded?: boolean;
  targetWorldId?: string;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(embedded);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function restore() {
    if (busy || !files.length) return;
    setBusy(true);
    onBusyChange?.(true);
    setError('');
    try {
      onImported(await importWorldFiles(files, targetWorldId));
    } catch (failure) {
      setError(failure instanceof ApiClientError ? failure.friendlyMessage : '导入失败，请重试。');
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  }

  const content = <>
      {!embedded && <h2>导入世界或存档</h2>}
      <p className="muted text-sm">{targetWorldId ? '导入同世界观版本的存档 JSON 或 ZIP，将增加独立旅程，不覆盖现有进度。' : '可选择世界观 JSON、同一次导出的两份 JSON，或一个墨境 ZIP。单独导入存档时会查找匹配的世界观版本。每次导入都会创建独立副本。'}</p>
      <label htmlFor="world-transfer-files">选择迁移文件</label>
      <input id="world-transfer-files" type="file" multiple disabled={busy}
        accept=".json,.zip,application/json,application/zip" onChange={event => {
          const selected = Array.from(event.currentTarget.files ?? []);
          const valid = selected.length <= 2 && !(selected.length === 2 && selected.some(file => /\.zip$/i.test(file.name)));
          setFiles(valid ? selected : []);
          setError(valid ? '' : '请选择一个 ZIP，或同时选择对应的两份 JSON。');
          event.currentTarget.value = '';
        }} />
      {!!files.length && <ul className="muted text-sm break-all">{files.map((file, index) => <li key={index}>{file.name}</li>)}</ul>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="flex flex-wrap gap-2 mt-3">
        <button className="primary" type="button" onClick={() => void restore()} disabled={!files.length || busy}>
          {busy ? '正在检查并导入…' : targetWorldId ? '导入为新的旅程' : '导入为独立副本'}
        </button>
        <button className="secondary" type="button" disabled={busy} onClick={() => {
          if (!embedded) setOpen(false);
          setFiles([]);
          setError('');
        }}>{embedded ? '清空选择' : '取消'}</button>
      </div>
  </>;
  if (embedded) return <section aria-label="导入备份">{content}</section>;
  return <>
    <button className="secondary mb-3" type="button" onClick={() => setOpen(true)}>
      <Upload size={16} />导入世界或存档
    </button>
    {open && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="导入世界或存档">{content}</section></div>}
  </>;
}

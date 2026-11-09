import { useState } from 'react';
import { Upload } from 'lucide-react';
import { ApiClientError, importWorldFiles } from '../../api/client';
import type { WorldSummary } from '../../api/types';

export function WorldTransferImport({ onImported }: { onImported: (world: WorldSummary) => void }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function restore() {
    if (busy || !files.length) return;
    setBusy(true);
    setError('');
    try {
      onImported(await importWorldFiles(files));
    } catch (failure) {
      setError(failure instanceof ApiClientError ? failure.friendlyMessage : '导入失败，请重试。');
    } finally {
      setBusy(false);
    }
  }

  return <>
    <button className="secondary mb-3" type="button" onClick={() => setOpen(true)}>
      <Upload size={16} />导入世界或存档
    </button>
    {open && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="导入世界或存档">
      <h2>导入世界或存档</h2>
      <p className="muted text-sm">可选择世界观 JSON、同一次导出的两份 JSON，或一个墨境 ZIP。单独导入存档时会查找匹配的世界观版本。每次导入都会创建独立副本。</p>
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
          {busy ? '正在检查并导入…' : '导入为独立副本'}
        </button>
        <button className="secondary" type="button" disabled={busy} onClick={() => {
          setOpen(false);
          setFiles([]);
          setError('');
        }}>取消</button>
      </div>
    </section></div>}
  </>;
}

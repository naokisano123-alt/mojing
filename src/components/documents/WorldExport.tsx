import { useEffect, useState } from 'react';
import { buildUrl } from '../../api/client';
import { journeyApi, type Journey } from '../../features/adventures/journeyApi';

export function WorldExport({ worldId }: { worldId: string }) {
  const [open, setOpen] = useState(false), [loading, setLoading] = useState(false);
  const [items, setItems] = useState<Journey[]>([]), [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    void journeyApi.list(worldId, controller.signal).then(page => { if (!controller.signal.aborted) { setItems(page.items); setSelected(page.items.map(item => item.id)); } })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [worldId, open]);
  return <details className="world-export-panel journal-sources" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>导出世界与旅程</summary>
    <div className="journal-create">
      <a className="secondary" href={buildUrl(`/worlds/${encodeURIComponent(worldId)}/transfer`, { part: 'world' })}>仅导出世界观</a>
      <p className="muted">携带旅程时，会包含该旅程的完整记录、自动存档和所有手动存档。</p>
      {loading && <p role="status">正在读取旅程…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {items.length > 0 && <>
        <label className="journal-apply"><input type="checkbox" checked={selected.length === items.length} onChange={e => setSelected(e.target.checked ? items.map(item => item.id) : [])} />全选旅程</label>
        {items.map(item => <label className="journal-apply" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id))} />{item.name}</label>)}
        {!loading && !error && selected.length > 0 && <a className="primary" href={buildUrl(`/worlds/${encodeURIComponent(worldId)}/transfer`, { part: 'zip', journeys: selected.join(',') })}>导出世界与所选旅程（ZIP）</a>}
      </>}
    </div>
  </details>;
}

import { useEffect, useRef, useState } from 'react';
import type { Run } from './api';
import { adventureApi } from './api';
import { journeyApi, requestId, type Journey, type SaveSlot } from './journeyApi';
import { WorldTransferImport } from '../../components/documents/WorldTransferImport';

export function JourneyManager({ worldId, currentRun, onResume, onCreate, onBack }: {
  worldId: string; currentRun: Run | null; onResume: (id: string) => Promise<void>;
  onCreate: () => void; onBack: () => void;
}) {
  const [folders, setFolders] = useState<Journey[]>([]), [selected, setSelected] = useState<Journey | null>(null);
  const [saves, setSaves] = useState<SaveSlot[]>([]), [name, setName] = useState('');
  const [rename, setRename] = useState(''), [renaming, setRenaming] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false), [newFromSave, setNewFromSave] = useState<SaveSlot | null>(null);
  const [newName, setNewName] = useState('');
  const lock = useRef(false), epoch = useRef(0), saveRequest = useRef<{ signature: string; id: string } | undefined>(undefined);
  const loadRequests = useRef(new Map<string, string>());
  const reload = async () => { const page = await journeyApi.list(worldId); setFolders(page.items); };
  useEffect(() => {
    const controller = new AbortController();
    void journeyApi.list(worldId, controller.signal).then(page => setFolders(page.items))
      .catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { epoch.current++; controller.abort(); };
  }, [worldId]);
  useEffect(() => {
    const controller = new AbortController();
    const generation = ++epoch.current;
    setSaves([]); setRenaming(false); setNewFromSave(null); setError('');
    if (selected) {
      setLoading(true); setRename(selected.name);
      void journeyApi.saves(selected.id, controller.signal).then(page => { if (epoch.current === generation) setSaves(page.items); })
        .catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }
    return () => controller.abort();
  }, [selected?.id]);
  async function perform(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，请重试。'); }
    finally { lock.current = false; setBusy(false); }
  }
  const currentFolder = folders.find(item => item.active_adventure_id === currentRun?.id);
  const sorted = currentFolder ? [currentFolder, ...folders.filter(item => item.id !== currentFolder.id)] : folders;
  const manual = saves.filter(item => item.kind === 'manual');
  const loadSave = (slot: SaveSlot, asNew = false) => void perform(async () => {
    if (!selected) return;
    const signature = JSON.stringify([slot.id, asNew, asNew ? newName : selected.name]);
    if (!loadRequests.current.has(signature)) loadRequests.current.set(signature, requestId());
    const run = await journeyApi.load(selected.id, slot.id, loadRequests.current.get(signature)!, asNew, asNew ? newName : undefined);
    await onResume(run.id);
  });
  return <section className="journal-save-manager" aria-label="存档与读档">
    <div className="journal-heading"><h2>{selected ? selected.name : '存档与读档'}</h2><button className="text-button" disabled={busy} onClick={() => selected ? setSelected(null) : onBack()}>{selected ? '返回旅程列表' : '返回冒险'}</button></div>
    {loading && <p role="status">正在读取…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!selected ? <>
      {sorted.map((item, index) => <div key={item.id} className="journey-folder">
        <small className="muted">{item === currentFolder ? '现游玩旅程' : index === (currentFolder ? 1 : 0) ? '其他旅程' : ''}</small>
        <button className="journey-row secondary" disabled={busy} onClick={() => setSelected(item)}><strong>{item.name}</strong><span>第 {item.revision} 回合 →</span></button>
      </div>)}
      {!loading && !folders.length && <p className="muted">还没有旅程。可以新建，也可以导入同世界的存档。</p>}
      <footer className="journey-bottom"><button className="primary" disabled={busy} onClick={onCreate}>新建旅程</button><button className="secondary" disabled={busy} onClick={() => setImporting(value => !value)}>导入旅程</button></footer>
      {importing && <WorldTransferImport embedded targetWorldId={worldId} onBusyChange={setBusy} onImported={() => { setImporting(false); void perform(reload); }} />}
    </> : <>
      <div className="journal-heading"><span className="muted">{manual.length}/20 个手动存档</span><button className="text-button" disabled={busy} onClick={() => setRenaming(value => !value)}>修改旅程名称</button></div>
      {renaming && <div className="checkpoint-controls"><input aria-label="旅程新名称" maxLength={80} value={rename} onChange={e => setRename(e.target.value)} /><button className="secondary" disabled={busy || !rename.trim()} onClick={() => void perform(async () => {
        await journeyApi.rename(selected.id, rename); setSelected({ ...selected, name: rename }); setRenaming(false); await reload();
      })}>保存名称</button></div>}
      {!loading && !saves.some(item => item.kind === 'auto') && <p className="muted">这段旧旅程尚无自动存档，继续后会自动保存。</p>}
      {saves.map(slot => <article className="journey-slot" key={slot.id}>
        <div className="journal-heading"><strong>{slot.kind === 'auto' ? '自动存档' : slot.name}</strong><span className="muted">第 {slot.revision} 回合</span></div>
        <small className="muted">{new Date(slot.created_at * 1000).toLocaleString()}</small>
        <div className="journal-heading"><button className="secondary" disabled={busy} onClick={() => loadSave(slot)}>读取存档</button>
          <button className="text-button" disabled={busy} onClick={() => { setNewFromSave(slot); setNewName(selected.name + ' · 新旅程'); }}>以此建立新旅程</button>
          {slot.kind === 'manual' && <button className="text-button" disabled={busy} onClick={() => { if (window.confirm(`删除“${slot.name}”？`)) void perform(async () => { await journeyApi.remove(selected.id, slot.id); setSaves(items => items.filter(item => item.id !== slot.id)); }); }}>删除</button>}
        </div>
      </article>)}
      {newFromSave && <div className="journal-create"><label>新旅程名称<input maxLength={80} value={newName} onChange={e => setNewName(e.target.value)} /></label><button className="secondary" disabled={busy || !newName.trim()} onClick={() => loadSave(newFromSave, true)}>从所选存档建立旅程</button><button className="text-button" onClick={() => setNewFromSave(null)}>取消</button></div>}
      <footer className="journey-bottom journal-create">
        <button className="text-button" disabled={busy} onClick={() => void onResume(selected.active_adventure_id)}>继续这段旅程</button>
        <label>新存档名称<input value={name} maxLength={80} placeholder={`第 ${selected.revision} 回合的进度`} disabled={busy} onChange={e => setName(e.target.value)} /></label>
        <button className="primary" disabled={busy || manual.length >= 20} onClick={() => void perform(async () => {
          const run = await adventureApi.detail(selected.active_adventure_id);
          const label = name.trim() || `第 ${run.revision} 回合的进度`;
          const signature = JSON.stringify([run.id, run.revision, label]);
          if (saveRequest.current?.signature !== signature) saveRequest.current = { signature, id: requestId() };
          await journeyApi.save(selected.id, run, label, saveRequest.current.id);
          saveRequest.current = undefined; setName(''); setSaves((await journeyApi.saves(selected.id)).items);
        })}>新建手动存档</button>
      </footer>
    </>}
  </section>;
}

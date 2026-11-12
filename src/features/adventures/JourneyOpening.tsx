import { useEffect, useRef, useState } from 'react';
import { Feather } from 'lucide-react';
import { request, ApiClientError } from '../../api/client';
import type { Run } from './api';
import { requestId } from './journeyApi';
import type { WorldStartBootstrapDto } from '../world-start/types';

export function JourneyOpening({ worldId, sessionId, onStarted, onCancel, onConfigure }: {
  worldId: string; sessionId?: string; onStarted: (run: Run) => Promise<void>;
  onCancel: () => void; onConfigure: () => void;
}) {
  const [name, setName] = useState('新的旅程');
  const [description, setDescription] = useState('');
  const [bootstrap, setBootstrap] = useState<WorldStartBootstrapDto>();
  const [selected, setSelected] = useState<string[]>([]);
  const [options, setOptions] = useState<{title: string; description: string}[]>([]);
  const [mode, setMode] = useState<'fake' | 'live'>('live');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [errorCode, setErrorCode] = useState('');
  const alive = useRef(true), locked = useRef(false);
  const startRequest = useRef<{ signature: string; id: string } | undefined>(undefined);
  const editor = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void request<WorldStartBootstrapDto>('GET', `/worlds/${encodeURIComponent(worldId)}/world-start`, { signal: controller.signal })
      .then(data => { if (alive.current) { setBootstrap(data); setSelected(data.materials.map(item => item.source_id)); } })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => { alive.current = false; controller.abort(); };
  }, [worldId]);
  async function perform(work: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setErrorCode('');
    try { await work(); }
    catch (e) {
      if (alive.current) {
        if (e instanceof ApiClientError && e.details?.model_output_issue) {
          setError('模型这次没有整理好完整开局，尚未创建旅程。你的文字已保留，可以稍后重试。');
          setErrorCode(e.code);
        } else if (e instanceof ApiClientError && e.code === 'CROSS_WORLD_SOURCE_FORBIDDEN') {
          setError('模型混入了其他世界的资料标识，本次已停止。你的开局文字已保留，可以重新尝试。');
          setErrorCode(e.code);
        } else setError(e instanceof ApiClientError ? e.friendlyMessage : e instanceof Error ? e.message : '开局失败，请重试。');
      }
    }
    finally { if (alive.current) { locked.current = false; setBusy(false); } }
  }
  const body = { mode, session_id: sessionId, selected_source_ids: selected, description };
  const unavailable = mode === 'live' && !sessionId;
  return <section className="journey-opening journal-create" aria-label="编写开局">
    <div className="journal-heading"><h2>开始新的旅程</h2><button className="text-button" disabled={busy} onClick={onCancel}>返回</button></div>
    <label>旅程名称<input maxLength={80} value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
    <p className="muted">让 AI 提供几个开局，点选后可以继续修改；也可以从零写下你的开局。</p>
    <details className="journal-sources"><summary>开局依据 · {selected.length} 份资料</summary>
      {!bootstrap && <p role="status">正在读取世界资料…</p>}
      {bootstrap?.materials.map(item => <label className="journal-apply" key={item.source_id}><input type="checkbox" disabled={busy} checked={selected.includes(item.source_id)}
        onChange={e => setSelected(items => e.target.checked ? [...items, item.source_id] : items.filter(id => id !== item.source_id))} />{item.title}</label>)}
      {bootstrap?.materials.length === 0 && <p>可以直接描述你的世界与开局。</p>}
    </details>
    <div className="opening-options">{options.map((option, i) => <button className="secondary" key={i} disabled={busy} onClick={() => {
      const next = description.trim() ? `${description.trim()}\n\n${option.description}` : option.description;
      if (next.length > 6000) { setError('开局最多6000字，请先精简编辑框中的内容。'); return; }
      setDescription(next); setError('');
      editor.current?.focus();
    }}><strong>{option.title}</strong><span>{option.description}</span></button>)}</div>
    <button type="button" className="secondary" disabled={busy || unavailable || !bootstrap} onClick={() => void perform(async () => {
      const data = await request<{options: typeof options}>('POST', `/worlds/${encodeURIComponent(worldId)}/opening-options`, { body, timeoutMs: 90000 });
      if (alive.current) setOptions(data.options);
    })}>{busy ? '正在准备…' : options.length ? '重新生成候选开局' : '根据资料推荐开局'}</button>
    <label className="opening-description"><span><Feather size={17} />你的开局</span>
      <textarea ref={editor} rows={9} maxLength={6000} value={description} disabled={busy}
        placeholder="描述你的角色、所处境况、地点与随身物品。物品的介绍、数量和属性都可以用自己的话写。"
        onChange={e => { setDescription(e.target.value); startRequest.current = undefined; }} /></label>
    <details className="journal-sources"><summary>游玩方式</summary><label>游玩方式<select value={mode} disabled={busy} onChange={e => setMode(e.target.value as typeof mode)}>
      <option value="live">使用已连接的模型</option><option value="fake">本地模拟体验（不调用模型）</option>
    </select></label></details>
    {mode === 'live' && <p className="muted text-sm">推荐开局和开始旅程会调用模型，可能消耗 Token。开始时会理解最终描述，并根据所选资料建立人物与初始局势；长文可能分多次调用。</p>}
    {unavailable && <button className="text-button" onClick={onConfigure}>前往设置连接模型</button>}
    {error && <p role="alert" className="error">{error}</p>}
    {errorCode && <details className="journal-sources"><summary>查看错误代码</summary><p>{errorCode}</p></details>}
    <button className="primary" disabled={busy || unavailable || !name.trim() || !description.trim() || !bootstrap} onClick={() => void perform(async () => {
      const signature = JSON.stringify({ ...body, name, session_id: undefined });
      if (startRequest.current?.signature !== signature) startRequest.current = { signature, id: requestId() };
      const run = await request<Run>('POST', `/worlds/${encodeURIComponent(worldId)}/journey-start`, { body: { ...body, name, request_id: startRequest.current.id }, timeoutMs: 240000 });
      if (alive.current) await onStarted(run);
    })}>{busy ? '正在准备旅程…' : '以此开局开始旅程'}</button>
  </section>;
}

import { useEffect, useRef, useState } from 'react';
import { ArrowUp, ChevronDown, Feather, MoreHorizontal, Square } from 'lucide-react';
import type { Branch, State, World } from '../model';
import { clock } from '../model';
import { ACTION_LIMIT, appendSuggestion } from '../interaction';

interface Props {
  world: World; branch: Branch; state: State; send: (action: string) => void;
  busy: boolean; stop: () => void; draft: string; setDraft: (value: string) => void;
  expanded: boolean; setExpanded: (value: boolean) => void;
}

export default function Story({ world, branch, state, send, busy, stop, draft, setDraft, expanded, setExpanded }: Props) {
  const [composing, setComposing] = useState(false);
  const [notice, setNotice] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const composer = useRef<HTMLDivElement>(null);
  const [composerHeight, setComposerHeight] = useState(140);
  const lastTurn = useRef(branch.turns.length);
  const focusRequested = useRef(false);
  const suggestions = branch.location.includes('码头')
    ? ['沿着石阶走回旧街', '买一杯热茶，暖暖手', '在屋檐下休息片刻']
    : ['询问店主有什么工作，看看自己能帮上什么忙', '走到码头，看看往来的船只', '在屋檐下休息片刻'];

  useEffect(() => {
    const observer = new ResizeObserver(entries => setComposerHeight(entries[0].contentRect.height + 28));
    if (composer.current) observer.observe(composer.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (branch.turns.length > lastTurn.current) {
      end.current?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'end' });
    }
    lastTurn.current = branch.turns.length;
  }, [branch.turns.length]);
  useEffect(() => {
    if (input.current) {
      input.current.style.height = 'auto';
      input.current.style.height = `${Math.min(160, Math.max(64, input.current.scrollHeight))}px`;
    }
  }, [draft, expanded]);

  function openEditor() {
    focusRequested.current = true;
    setExpanded(true);
    if (input.current) {
      input.current.focus({ preventScroll: true });
      input.current.setSelectionRange(input.current.value.length, input.current.value.length);
      focusRequested.current = false;
    }
  }
  function append(value: string) {
    const result = appendSuggestion(draft, value);
    if (result === null) { setNotice('草稿空间不足，请先精简文字。已有内容未改变。'); return; }
    setNotice(''); setDraft(result); openEditor();
  }
  function submit() {
    if (!draft.trim() || busy || composing) return;
    input.current?.blur();
    setNotice(''); send(draft.trim());
  }

  return <div className="story-layout">
    <div className="chapter-opening"><h1 className="chapter-title">雾中的第一盏灯</h1>
      <p className="chapter-subtitle">{world.name} · {branch.name} · 演示开篇</p>
    </div>
    <article className="narrative" aria-live="polite">
      {branch.turns.map((turn, index) => <section className="turn" key={turn.id}>
        {turn.action && <div className="player-action"><Feather size={16} /><div><small>你的行动</small><p>{turn.action}</p></div></div>}
        {index > 0 && <div className="turn-marker">{clock(turn.time)} · 第 {index} 回合</div>}
        {turn.text.map((paragraph, i) => <p className="prose" key={i}>{paragraph}</p>)}
        {turn.changes.length > 0 && (state.mode === 'mixed'
          ? <div className="change-card"><span className="eyebrow">这一刻，世界发生了变化</span><div>{turn.changes.map(change => <span key={change}>{change}</span>)}</div></div>
          : <details className="change-card"><summary>查看本回合状态变化<ChevronDown size={14} /></summary><p>{turn.changes.join(' · ')}</p></details>)}
      </section>)}
      {busy && <div className="thinking" role="status"><span className="status-dot" />正在翻开故事的下一页<span>本地模拟</span></div>}
      <div ref={end} style={{ scrollMarginBottom: composerHeight + 64 }} />
    </article>
    <div className={`composer action-dock ${expanded ? 'is-expanded' : ''}`} ref={composer} data-no-swipe>
      <div className="suggestions action-options" aria-label="行动建议">
        {suggestions.map(suggestion => <button disabled={busy} key={suggestion} onClick={() => append(suggestion)}>{suggestion}</button>)}
        <button className={`more-action ${draft && !expanded ? 'has-draft' : ''}`} disabled={busy} onClick={openEditor}
          aria-label={draft ? '展开行动草稿' : '自由编辑行动'} aria-expanded={expanded} aria-controls="action-editor" title={draft ? '继续编辑草稿' : '自由编辑行动'}>
          <MoreHorizontal size={23} />
        </button>
      </div>
      {expanded && <div className="editor-drawer" id="action-editor">
        <div className="editor-topline"><span>你的行动</span><button className="text-button" disabled={busy} onClick={() => {
          input.current?.blur(); setExpanded(false); setNotice('');
        }} aria-label="收起输入框，保留草稿"><ChevronDown size={16} />收起</button></div>
        <div className="input-shell"><textarea ref={element => {
          input.current = element;
          if (element && focusRequested.current) { element.focus({ preventScroll: true }); focusRequested.current = false; }
        }} aria-label="你的行动" value={draft} maxLength={ACTION_LIMIT} rows={2} readOnly={busy}
          placeholder="写下想做的事，或想说的话……" onChange={event => { setDraft(event.target.value); setNotice(''); }}
          onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)}
          onKeyDown={event => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
          }} />
          {busy ? <button className="send-button" onClick={stop} aria-label="停止模拟"><Square size={16} /></button>
            : <button className="send-button" disabled={!draft.trim() || composing} onClick={submit} aria-label="发送行动"><ArrowUp size={21} /></button>}
        </div>
        <div className="composer-hint"><span>建议可继续追加，编辑后发送</span><span>{draft.length}/{ACTION_LIMIT}</span></div>
      </div>}
      {notice && <p className="error" role="status">{notice}</p>}
    </div>
  </div>;
}

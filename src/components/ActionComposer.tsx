import { useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowUp, ChevronDown, MoreHorizontal, Square } from 'lucide-react';
import { appendSuggestion } from '../interaction';

export function ActionComposer({
  id, draft, setDraft, expanded, setExpanded, suggestions, limit,
  busy, disabled = false, stopping = false, send, stop,
}: {
  id: string;
  draft: string;
  setDraft: (value: string) => void;
  expanded: boolean;
  setExpanded: (value: boolean) => void;
  suggestions: readonly string[];
  limit: number;
  busy: boolean;
  disabled?: boolean;
  stopping?: boolean;
  send: (value: string) => void;
  stop: () => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [notice, setNotice] = useState('');
  const composing = useRef(false);

  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = 'auto';
    input.current.style.height = `${Math.min(160, Math.max(64, input.current.scrollHeight))}px`;
  }, [draft, expanded]);

  function open(suggestion?: string) {
    const next = suggestion ? appendSuggestion(draft, suggestion, limit) : draft;
    if (next === null) {
      setNotice('草稿空间不足，请先精简文字。已有内容未改变。');
      return;
    }
    setNotice('');
    // Keep focus in the user gesture, including when the editor first mounts.
    flushSync(() => {
      if (next !== draft) setDraft(next);
      setExpanded(true);
    });
    input.current?.focus();
    input.current?.setSelectionRange(next.length, next.length);
  }

  function submit() {
    const editor = input.current;
    if (!editor || busy || disabled) return;
    editor.blur();
    const value = editor.value.trim();
    if (!value || value.length > limit) return;
    setNotice('');
    send(value);
  }

  return <div className="composer in-flow-composer" data-no-swipe>
    <div className="suggestions action-options" aria-label="行动建议">
      {suggestions.map(suggestion => <button type="button" disabled={busy} key={suggestion}
        onClick={() => open(suggestion)}>{suggestion}</button>)}
      <button type="button" className={`more-action ${draft && !expanded ? 'has-draft' : ''}`}
        disabled={busy} onClick={() => open()} aria-label={draft ? '展开行动草稿' : '自由编辑行动'}
        aria-expanded={expanded} aria-controls={id}>
        <MoreHorizontal size={23} />
      </button>
    </div>
    {expanded && <div className="editor-drawer" id={id}>
      <div className="editor-topline">
        <label htmlFor={`${id}-input`}>你的行动</label>
        <button type="button" className="text-button" disabled={busy} onClick={() => {
          input.current?.blur(); setExpanded(false); setNotice('');
        }} aria-label="收起输入框，保留草稿"><ChevronDown size={16} />收起</button>
      </div>
      <div className="input-shell">
        <textarea ref={input} id={`${id}-input`} value={draft} maxLength={limit} rows={2} readOnly={busy}
          placeholder="写下想做的事，或想说的话……"
          onChange={event => { setDraft(event.target.value); setNotice(''); }}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={event => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)
              && !event.nativeEvent.isComposing && !composing.current) {
              event.preventDefault(); submit();
            }
          }} />
        {stopping ? <button type="button" className="send-button" onClick={stop} aria-label="停止生成"><Square size={16} /></button>
          : <button type="button" className="send-button" disabled={!draft.trim() || busy || disabled}
            onPointerDown={event => { if (document.activeElement === input.current) event.preventDefault(); }}
            onClick={submit} aria-label="发送行动"><ArrowUp size={21} /></button>}
      </div>
      <div className="composer-hint">
        <span>{suggestions.length > 0 ? '建议可继续追加，编辑后发送' : '编辑后发送'}</span>
        <span>{draft.length}/{limit}</span>
      </div>
    </div>}
    {notice && <p className="error" role="status">{notice}</p>}
  </div>;
}

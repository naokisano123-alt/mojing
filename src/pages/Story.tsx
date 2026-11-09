import { useEffect, useRef } from 'react';
import { ChevronDown, Feather } from 'lucide-react';
import type { Branch, State, World } from '../model';
import { clock } from '../model';
import { ACTION_LIMIT } from '../interaction';
import { ActionComposer } from '../components/ActionComposer';

interface Props {
  world: World; branch: Branch; state: State; send: (action: string) => void;
  busy: boolean; stop: () => void; draft: string; setDraft: (value: string) => void;
  expanded: boolean; setExpanded: (value: boolean) => void;
}

export default function Story({ world, branch, state, send, busy, stop, draft, setDraft, expanded, setExpanded }: Props) {
  const latest = useRef<HTMLElement>(null);
  const lastTurn = useRef(branch.turns.length);
  const suggestions = branch.location.includes('码头')
    ? ['沿着石阶走回旧街', '买一杯热茶，暖暖手', '在屋檐下休息片刻']
    : ['询问店主有什么工作，看看自己能帮上什么忙', '走到码头，看看往来的船只', '在屋檐下休息片刻'];

  useEffect(() => {
    if (branch.turns.length > lastTurn.current) latest.current?.scrollIntoView?.({ block: 'start' });
    lastTurn.current = branch.turns.length;
  }, [branch.turns.length]);

  return <div className="story-layout">
    <div className="chapter-opening"><h1 className="chapter-title">雾中的第一盏灯</h1>
      <p className="chapter-subtitle">{world.name} · {branch.name} · 演示开篇</p>
    </div>
    <article className="narrative" aria-live="polite">
      {branch.turns.map((turn, index) => <section className="turn" ref={index === branch.turns.length - 1 ? latest : undefined} key={turn.id}>
        {turn.action && <div className="player-action"><Feather size={16} /><div><small>你的行动</small><p>{turn.action}</p></div></div>}
        {index > 0 && <div className="turn-marker">{clock(turn.time)} · 第 {index} 回合</div>}
        {turn.text.map((paragraph, i) => <p className="prose" key={i}>{paragraph}</p>)}
        {turn.changes.length > 0 && (state.mode === 'mixed'
          ? <div className="change-card"><span className="eyebrow">这一刻，世界发生了变化</span><div>{turn.changes.map(change => <span key={change}>{change}</span>)}</div></div>
          : <details className="change-card"><summary>查看本回合状态变化<ChevronDown size={14} /></summary><p>{turn.changes.join(' · ')}</p></details>)}
      </section>)}
      {busy && <div className="thinking" role="status"><span className="status-dot" />正在翻开故事的下一页<span>本地模拟</span></div>}
    </article>
    <ActionComposer id="action-editor" draft={draft} setDraft={setDraft}
      expanded={expanded} setExpanded={setExpanded} suggestions={suggestions} limit={ACTION_LIMIT}
      busy={busy} stopping={busy} send={send} stop={stop} />
  </div>;
}

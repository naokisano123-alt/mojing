import { Check, Clock3, MapPin, Sparkles } from 'lucide-react';
import type { Branch, Fate } from '../model';
import { clock, fateNames } from '../model';
import { Modal } from './UI';

export function WorldBar({ branch, showFate, openFate }: { branch: Branch; showFate: boolean; openFate: () => void }) {
  return <header className="world-bar">
    <div className="world-bar-info">
      <span><MapPin size={15} aria-hidden="true" />{branch.location}</span>
      <span><Clock3 size={15} aria-hidden="true" />{clock(branch.time)}</span>
    </div>
    {showFate && <button className="fate-corner" aria-label={`主角光环，当前${fateNames[branch.fate]}`} title="主角光环" onClick={openFate}>
      <Sparkles size={20} aria-hidden="true" />
    </button>}
  </header>;
}

export function FateDialog({ branch, busy, change, close }: { branch: Branch; busy: boolean; change: (f: Fate) => void; close: () => void }) {
  return <Modal title="主角光环" close={close}>
    <p className="muted fate-description">当前：{fateNames[branch.fate]}。只影响未来，不改写已经发生的故事。</p>
    <div className="fate-options">
      {(['off', 'soft', 'strong'] as Fate[]).map(mode => <button key={mode} disabled={busy} aria-pressed={mode === branch.fate}
        className={mode === branch.fate ? 'active' : ''} onClick={() => change(mode)}>
        <span>{fateNames[mode]}{mode === branch.fate && <Check size={15} />}</span>
        <small>{{ off: '自然推进，接受得失', soft: '增加合理的转机', strong: '更多机遇，仍有边界' }[mode]}</small>
      </button>)}
    </div>
    <p className="setting-note">{busy ? '当前回合正在进行，结束后可调整。' : '调整从下一回合生效，并记录到时间线。'}</p>
  </Modal>;
}

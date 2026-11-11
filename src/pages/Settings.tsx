import { useState } from 'react';
import { BookOpen, Feather, Monitor, Moon, Sun } from 'lucide-react';
import type { State } from '../model';
import { Back, PageTitle } from '../components/UI';

type Section = 'reading' | 'narration';
export default function Settings({ state, update, back, embedded = false }: { state: State; update: (s: Partial<State>) => void; back: () => void; embedded?: boolean }) {
  const [section, setSection] = useState<Section>('reading');
  return <div className="narrow">
    {!embedded && <><Back onClick={back} label="我的世界" />
      <PageTitle eyebrow="MAKE THE JOURNEY YOURS" title="以你喜欢的方式" description="调整阅读外观与叙事偏好。" /></>}
    <div className="tabs settings-tabs" role="tablist" aria-label="设置分类">
      {([{ id: 'reading', title: '阅读', icon: BookOpen }, { id: 'narration', title: '叙事', icon: Feather }] as const).map(({ id, title, icon: Icon }) =>
        <button key={id} id={`tab-${id}`} role="tab" aria-selected={section === id} aria-controls={`panel-${id}`}
          className={section === id ? 'active' : ''} onClick={() => setSection(id)}><Icon size={17} />{title}</button>)}
    </div>
    <section className="panel settings-section" role="tabpanel" id={`panel-${section}`} aria-labelledby={`tab-${section}`}>
      {section === 'reading' ? <>
        <h2><BookOpen size={19} />阅读</h2>
        <div className="setting-row theme-row"><div><h3>外观主题</h3><p>应用于所有世界</p></div>
          <div className="theme-options">{(['light', 'dark', 'system'] as const).map((theme, i) => {
            const Icon = [Sun, Moon, Monitor][i];
            return <button key={theme} aria-pressed={state.theme === theme} className={state.theme === theme ? 'active' : ''}
              onClick={() => update({ theme })} aria-label={['浅色', '深色', '跟随系统'][i]}><Icon size={17} /><span>{['浅色', '深色', '系统'][i]}</span></button>;
          })}</div>
        </div>
        <div className="setting-row"><div><h3>正文字号</h3><p>只调整故事正文</p></div>
          <select aria-label="正文字号" value={state.fontSize ?? 16} onChange={e => update({ fontSize: Number(e.target.value) as State['fontSize'] })}>
            <option value="16">标准 · 16</option><option value="18">较大 · 18</option><option value="20">大字 · 20</option>
          </select></div>
        <div className="setting-row"><div><h3>段落行距</h3><p>让中文阅读更舒适</p></div>
          <select aria-label="段落行距" value={state.lineHeight ?? 2.2} onChange={e => update({ lineHeight: Number(e.target.value) as State['lineHeight'] })}>
            <option value="1.9">紧凑</option><option value="2.2">舒适</option><option value="2.5">宽松</option>
          </select></div>
        <div className="setting-row"><div><h3>状态变化</h3><p>小说模式中点击展开</p></div>
          <select aria-label="展示方式" value={state.mode} onChange={e => update({ mode: e.target.value as State['mode'] })}>
            <option value="mixed">混合叙事</option><option value="novel">小说模式</option>
          </select></div>
        <div className="reading-sample"><small>阅读预览</small><p style={{ fontSize: state.fontSize ?? 16, lineHeight: state.lineHeight ?? 2.2 }}>风穿过旧街，翻动手记的一页。你还没有决定今天的去向，远处的船钟已经响了。</p></div>
      </> : <>
        <h2><Feather size={19} />叙事</h2>
        <p>控制故事如何讲述，不改变阅读外观。</p>
        <div className="setting-row"><div><h3>叙事风格</h3><p>从后续模拟回合生效</p></div>
          <select aria-label="叙事风格" value={state.style} onChange={e => update({ style: e.target.value as State['style'] })}>
            <option value="literary">文学细腻</option><option value="concise">简洁明快</option>
          </select></div>
        <p className="setting-note">当前演示用预置文本展示篇幅差异，正式叙事控制将在后续阶段接入。</p>
      </>}
    </section>
    {!embedded && <p className="footnote">阅读与叙事偏好会保留在当前设备</p>}
  </div>;
}

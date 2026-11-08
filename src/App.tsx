import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowRight, BookOpen, Bookmark, ScrollText, Settings as SettingsIcon, UserRound } from 'lucide-react';
import type { Branch, Fate, Page, Save, World } from './model';
import { changeFate, forkSave, loadState, makeWorld, simulateTurn, snapshot, uid } from './model';
import { Home, CreateWorld } from './pages/WorldPages';
import Story from './pages/Story';
import Settings from './pages/Settings';
import { Atlas, Character, Saves } from './pages/DetailPages';
import { FateDialog, WorldBar } from './components/WorldBar';
import { usePageSwipe } from './hooks/usePageSwipe';
import { WORLD_PAGES } from './interaction';
import type { SwipeDirection } from './interaction';

const nav = [
  { page: 'story' as Page, label: '故事', icon: BookOpen },
  { page: 'character' as Page, label: '角色', icon: UserRound },
  { page: 'atlas' as Page, label: '图鉴', icon: ScrollText },
  { page: 'saves' as Page, label: '存档', icon: Bookmark },
];
const allowed: Page[] = ['cover', 'home', 'create', 'story', 'character', 'atlas', 'saves', 'settings'];
function hashPage(): Page {
  const page = location.hash.slice(1) as Page;
  return allowed.includes(page) ? page : 'home';
}

export default function App() {
  const [state, setState] = useState(loadState);
  const [page, setPage] = useState<Page>(hashPage);
  const [direction, setDirection] = useState<SwipeDirection>('left');
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editors, setEditors] = useState<Record<string, boolean>>({});
  const [fateOpen, setFateOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);
  const pending = useRef<{ key: string; action: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const world = state.worlds.find(w => w.id === state.current) || state.worlds[0];
  const branch = world.branches.find(b => b.id === world.active)!;
  const dark = state.theme === 'dark' || state.theme === 'system' && systemDark;
  const inWorld = WORLD_PAGES.includes(page);

  function go(target: Page, move: SwipeDirection = 'left') {
    if (target === page) return;
    // Synchronous route changes avoid stale hash events after fast navigation.
    history.pushState(null, '', `#${target}`);
    setDirection(move);
    setPage(target);
    setFateOpen(false);
    window.scrollTo(0, 0);
  }
  const swipe = usePageSwipe(page, go);

  useEffect(() => {
    const changed = () => {
      setDirection('right');
      setPage(hashPage());
      setFateOpen(false);
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', changed);
    window.addEventListener('popstate', changed);
    return () => { window.removeEventListener('hashchange', changed); window.removeEventListener('popstate', changed); };
  }, []);
  useEffect(() => {
    try { localStorage.setItem('mojing-m0-v1', JSON.stringify(state)); }
    catch { setToast('浏览器存储不可用，刷新可能丢失进度。'); }
  }, [state]);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#171e1d' : '#f5f3ed');
  }, [dark]);
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const changed = () => setSystemDark(media.matches);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(''), 3500);
    return () => clearTimeout(timeout);
  }, [toast]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  useEffect(() => {
    const viewport = window.visualViewport;
    let baseline = window.innerHeight;
    let width = window.innerWidth;
    const update = () => {
      if (width !== window.innerWidth) { width = window.innerWidth; baseline = window.innerHeight; }
      const editing = document.activeElement?.matches('input, textarea, [contenteditable="true"]');
      if (!editing) baseline = Math.max(baseline, window.innerHeight);
      document.body.classList.toggle('keyboard-open', !!editing && baseline - (viewport?.height ?? window.innerHeight) > 120);
    };
    viewport?.addEventListener('resize', update);
    window.addEventListener('resize', update);
    document.addEventListener('focusin', update);
    document.addEventListener('focusout', update);
    return () => {
      viewport?.removeEventListener('resize', update);
      window.removeEventListener('resize', update);
      document.removeEventListener('focusin', update);
      document.removeEventListener('focusout', update);
      document.body.classList.remove('keyboard-open');
    };
  }, []);

  function updateWorld(id: string, change: (world: World) => World) {
    setState(s => ({ ...s, worlds: s.worlds.map(w => w.id === id ? change(w) : w) }));
  }
  function updateBranch(change: (branch: Branch) => Branch) {
    updateWorld(world.id, w => ({ ...w, branches: w.branches.map(b => b.id === w.active ? change(b) : b) }));
  }
  function stop() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setBusy(false);
    if (pending.current) {
      const p = pending.current;
      setDrafts(d => ({ ...d, [p.key]: p.action }));
      setEditors(e => ({ ...e, [p.key]: true }));
      pending.current = null;
    }
    setToast('已停止模拟，行动文字已保留。');
  }
  function send(action: string) {
    if (pending.current || !action.trim()) return;
    setBusy(true);
    pending.current = { key: branch.id, action };
    const worldId = world.id, branchId = branch.id, concise = state.style === 'concise';
    timer.current = setTimeout(() => {
      updateWorld(worldId, w => ({ ...w, branches: w.branches.map(b => b.id === branchId ? simulateTurn(b, action, concise) : b) }));
      setDrafts(d => ({ ...d, [branchId]: '' }));
      setEditors(e => ({ ...e, [branchId]: false }));
      setBusy(false);
      timer.current = null;
      pending.current = null;
    }, 900);
  }
  function setFate(mode: Fate) {
    if (busy) return;
    updateBranch(b => changeFate(b, mode));
    if (mode !== branch.fate) setToast('主角光环已调整，将从下一回合生效。');
  }
  function save(name: string) {
    if (busy) { setToast('当前回合尚未完成，请稍后存档。'); return; }
    updateWorld(world.id, w => ({ ...w, saves: [{ id: uid(), title: name, branchName: branch.name, snapshot: snapshot(branch) }, ...w.saves] }));
    setToast('此刻已保存。');
  }
  function fork(saved: Save) {
    if (busy) { setToast('请等待当前回合结束。'); return; }
    const next = forkSave(saved, branch.id);
    updateWorld(world.id, w => ({ ...w, active: next.id, branches: [...w.branches, next] }));
    setToast('已建立新分支，原旅程仍然保留。');
    go('story', 'right');
  }

  return <div className={`app-shell ${inWorld ? 'immersive' : ''}`} {...swipe.bind}
    style={{ '--reading-size': `${state.fontSize ?? 16}px`, '--reading-leading': state.lineHeight ?? 2.2 } as CSSProperties}>
    {page === 'home' && <header className="app-header">
      <button className="brand" onClick={() => go('home')} aria-label="墨境 · 我的世界"><span className="brand-mark">墨</span><span>墨境<small>MOJING</small></span></button>
      <div className="header-center">在文字中，遇见另一种人生</div>
      <div className="header-actions"><span className="demo-badge">M0 · 演示</span>
        <button className="icon-button" aria-label="游戏设置" title="游戏设置" onClick={() => go('settings')}><SettingsIcon size={20} /></button>
      </div>
    </header>}
    {inWorld && <WorldBar branch={branch} showFate={page === 'story'} openFate={() => { swipe.cancel(); setFateOpen(true); }} />}
    <main className={`${inWorld ? 'world-main' : ''} ${page === 'story' ? 'story-main' : ''}`}>
      <div className="swipe-stage" style={{ transform: swipe.offset ? `translateX(${swipe.offset}px)` : undefined }}>
        <div key={page} className={`page-motion slide-${direction} ${page === 'story' ? 'story-container' : 'content-container'}`}>
          {page === 'cover' && <section className="cover-page">
            <div className="cover-emblem" aria-hidden="true">墨</div>
            <p className="eyebrow">MOJING</p><h1>万千世界，静候翻阅。</h1><p>旅途暂歇，故事还在。</p>
            <button className="primary" onClick={() => go('home')}>进入我的世界<ArrowRight size={18} /></button>
            <small>进度已保留在当前浏览器</small>
          </section>}
          {page === 'home' && <Home worlds={state.worlds} create={() => go('create')} enter={id => {
            if (busy) { setToast('请等待当前回合结束。'); return; }
            setState(s => ({ ...s, current: id })); go('story');
          }} />}
          {page === 'create' && <CreateWorld cancel={() => go('home', 'right')} submit={(name, description, docs) => {
            const next = makeWorld(name, description, docs);
            setState(s => ({ ...s, worlds: [...s.worlds, next], current: next.id }));
            go('story'); setToast('世界已创建，进入统一演示开局。');
          }} />}
          {page === 'story' && <Story key={`${world.id}:${branch.id}`} world={world} branch={branch} state={state}
            send={send} busy={busy} stop={stop} draft={drafts[branch.id] || ''}
            setDraft={value => setDrafts(d => ({ ...d, [branch.id]: value }))}
            expanded={!!editors[branch.id]} setExpanded={value => setEditors(e => ({ ...e, [branch.id]: value }))} />}
          {page === 'character' && <Character branch={branch} />}
          {page === 'atlas' && <Atlas />}
          {page === 'saves' && <Saves world={world} branch={branch} save={save} fork={fork} switchBranch={id => {
            if (busy) { setToast('请等待当前回合结束。'); return; }
            updateWorld(world.id, w => ({ ...w, active: id })); setToast('已切换旅程分支。');
          }} />}
          {page === 'settings' && <Settings state={state} update={s => setState(prev => ({ ...prev, ...s }))} back={() => go('home', 'right')} />}
        </div>
      </div>
    </main>
    {inWorld && <nav className="bottom-nav compact-nav" aria-label="冒险导航">
      {nav.map(({ page: target, label, icon: Icon }) => <button key={target} aria-label={label} title={label}
        aria-current={page === target ? 'page' : undefined} className={page === target ? 'active' : ''}
        onClick={() => go(target, WORLD_PAGES.indexOf(target) > WORLD_PAGES.indexOf(page) ? 'left' : 'right')}>
        <Icon size={22} aria-hidden="true" />
      </button>)}
      <button className="accessible-exit" onClick={() => go('home', 'right')}>返回我的世界</button>
    </nav>}
    {page === 'home' && <button className="accessible-exit" onClick={() => go('cover', 'right')}>返回封面</button>}
    {fateOpen && page === 'story' && <FateDialog branch={branch} busy={busy} change={setFate} close={() => setFateOpen(false)} />}
    {(swipe.hint || toast) && <div className={`toast ${swipe.hint ? 'exit-hint' : ''}`} role="status">
      {swipe.hint ? <span>{swipe.hint}<small>2 秒内再次右滑 · 当前进度保留</small></span> : toast}
    </div>}
  </div>;
}

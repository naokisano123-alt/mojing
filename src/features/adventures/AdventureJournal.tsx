import { UserRound, MessagesSquare, MapPin, Clock3, Feather, ArrowLeft } from "lucide-react";
import { JournalOverlay } from "./JournalOverlay";
import { flushSync } from "react-dom";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  adventureApi,
  type Detail,
  type Draft,
  type Run,
  type Turn,
  type ClockResult,
} from "./api";
import { ActionComposer } from "../../components/ActionComposer";
import { ApiClientError } from "../../api/client";
import { JourneyManager } from "./JourneyManager";
import { JourneyOpening } from "./JourneyOpening";
import { MemoryChanges, MemoryPanel } from "./MemoryPanel";
import { CharacterCard } from "./CharacterCard";
import type { NarrationSource } from "../narration-preview/types";
import "./adventures.css";
import { formatStoryTime, elapsedAt } from "./storyTime.ts";

const WorldKnowledgePanel = lazy(() => import("../world-knowledge/WorldKnowledgePanel").then(module => ({ default: module.WorldKnowledgePanel })));
const WorldEncounterPanel = lazy(() => import("../world-encounters/WorldEncounterPanel").then(module => ({ default: module.WorldEncounterPanel })));


function ClockSummary({ clock, pending, start }: { clock: ClockResult; pending?: boolean; start?: string }) {
  return <aside className="journal-character" aria-label={pending ? "待确认时间结算" : "时间结算"}>
    <strong>{pending ? "待确认 · " : ""}{clock.interrupted ? "遇到需要决定的事件，时间停在" : "世界时间推进至"} {formatStoryTime(start, elapsedAt(start, clock.to))}</strong>
    {clock.events.length > 0 && <ul>
      {clock.events.map((event, index) => <li key={index}>
        <strong>{event.title}{event.count > 1 ? ` × ${event.count}` : ""}</strong>
        <p>{event.description}</p>
      </li>)}
    </ul>}
  </aside>;
}

export function AdventureJournal({
  worldId,
  view = "adventure",
  onViewChange,
  sessionId,
  onConfigure,
  onActiveRunChange,
  onSessionExpired,
  onOpenSource,
  api = adventureApi,
}: {
  worldId: string;
  view?: "adventure" | "saves" | "landing";
  onViewChange?: (view: "adventure" | "saves" | "landing") => void;
  sessionId?: string;
  onConfigure: () => void;
  onActiveRunChange?: (run: Run | null) => void;
  onSessionExpired?: () => void;
  onOpenSource: (source: NarrationSource) => void;
  api?: typeof adventureApi;
}) {
  const [acceptedDraft, setAcceptedDraft] = useState<{ key: string | null; value: boolean }>();
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [run, setRun] = useState<Detail | null>(null);
  useEffect(() => { onActiveRunChange?.(run); }, [run, onActiveRunChange]);
  useEffect(() => { setPanel(null); setNewsFact(null); }, [view]);
  useEffect(() => {
    if (run?.world_id !== worldId) return;
    try { localStorage.setItem(`mojing:active-adventure:${worldId}`, run.id); } catch { /* Optional device preference. */ }
  }, [run?.id, worldId]);
  const [expanded, setExpanded] = useState(false);
  const [panel, setPanel] = useState<"character" | "news" | null>(null);
  const [newsFact, setNewsFact] = useState<string | null>(null);
  const [newsRefresh, setNewsRefresh] = useState(0);
  const [action, setAction] = useState(""),
    [draft, setDraft] = useState<Draft | null>(null);
  const draftKey = run && draft ? `${run.id}:${draft.id}` : null;
  const hasMemoryProposal = Boolean(draft?.result?.memory_proposal?.length);
  const requiredClock = Boolean(draft?.result?.clock_required);
  const applyChanges = draftKey !== null && (requiredClock || (acceptedDraft?.key === draftKey ? acceptedDraft.value : hasMemoryProposal));
  const staleMemory = draft?.result?.memory_cursor !== undefined && draft.result.memory_cursor !== run?.memory?.cursor;
  useEffect(() => {
    if (draft?.status === "ready") draftCard.current?.scrollIntoView?.({ block: "start" });
  }, [draft?.id, draft?.status]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const draftCard = useRef<HTMLElement>(null);
  const epoch = useRef(0),
    active = useRef<AbortController | null>(null),
    locked = useRef(false);
  const valid = (generation: number) => epoch.current === generation;
  const refresh = async (id: string) => {
    const generation = epoch.current;
    const detail = await api.detail(id);
    if (valid(generation)) {
      setRun(detail);
      setDraft(detail.latest_draft);
    }
  };
  useEffect(() => {
    const generation = ++epoch.current;
    const controller = new AbortController();
    setLoadingRuns(true);
    setShowCreate(false);
    setPanel(null);
    setNewsFact(null);
    setRun(null);
    setDraft(null);
    setExpanded(false);
    setAction("");
    setError("");
    setBusy(false);
    locked.current = false;
    void api
      .list(worldId, controller.signal)
      .then(async (data) => {
        if (!valid(generation)) return;
        let remembered: string | null = null;
        try { remembered = localStorage.getItem(`mojing:active-adventure:${worldId}`); } catch { /* Storage may be disabled. */ }
        if (remembered) {
          try {
            const detail = await api.detail(remembered);
            if (valid(generation) && detail.world_id === worldId) {
              setRun(detail);
              setDraft(detail.latest_draft);
              return;
            }
          } catch { /* Removed or unavailable journey: fall back to the latest one. */ }
        }
        if (data.items[0]) await refresh(data.items[0].id);
      })
      .catch((e) => {
        if (valid(generation) && !controller.signal.aborted)
          setError(e.message);
      })
      .finally(() => {
        if (valid(generation) && !controller.signal.aborted)
          setLoadingRuns(false);
      });
    return () => {
      epoch.current++;
      controller.abort();
      active.current?.abort();
      active.current = null;
    };
  }, [worldId]);
  const operate = async (work: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    const generation = epoch.current;
    try {
      await work();
    } catch (e) {
      if (valid(generation)) {
        if (e instanceof ApiClientError && e.code === "MODEL_SESSION_EXPIRED") {
          onSessionExpired?.();
          setError("模型会话已失效。行动已保留，重新连接后可以继续。");
        } else {
          setError(e instanceof ApiClientError ? e.friendlyMessage
            : e instanceof Error ? e.message : "操作失败，请重试");
        }
      }
    } finally {
      if (valid(generation)) {
        setBusy(false);
        locked.current = false;
      }
    }
  };
  const choose = async (id: string) => {
    setPanel(null);
    setNewsFact(null);
    epoch.current++;
    active.current?.abort();
    active.current = null;
    locked.current = false;
    setBusy(false);
    setRun(null);
    setDraft(null);
    setExpanded(false);
    setAction("");
    const generation = epoch.current;
    setError("");
    try {
      const detail = await api.detail(id);
      if (valid(generation)) {
        setRun(detail);
        setDraft(detail.latest_draft);
        onViewChange?.("adventure");
      }
    } catch (e) {
      if (valid(generation)) setError(String(e));
    }
  };
  const generate = (submittedAction: string) =>
    operate(async () => {
      if (!run || !submittedAction.trim()) return;
      const generation = epoch.current,
        controller = new AbortController();
      active.current = controller;
      const id =
        globalThis.crypto?.randomUUID?.() ||
        Date.now().toString(36) + "_" + Math.random().toString(36).slice(2);
      const result = await api.prepare(
        run.id,
        {
          request_id: id,
          action: submittedAction,
          expected_revision: run.revision,
          session_id: run.mode === "live" ? sessionId : undefined,
        },
        controller.signal,
      );
      if (valid(generation) && !controller.signal.aborted) {
        setDraft(result);
        setExpanded(false);
        active.current = null;
      }
    });
  const stop = () => {
    epoch.current++;
    active.current?.abort();
    active.current = null;
    locked.current = false;
    setBusy(false);
    setError(
      "已停止等待，行动已保留。可到存档栏目恢复未完成内容；上游仍可能计费。",
    );
  };
  const accept = () =>
    operate(async () => {
      if (!run || !draft) return;
      const generation = epoch.current;
      if (applyChanges)
        await api.commit(run.id, draft.id, { apply_changes: true });
      else await api.commit(run.id, draft.id);
      if (valid(generation)) {
        await refresh(run.id);
        if (valid(generation)) { setExpanded(false); setAction(""); }
      }
    });
  const older = () =>
    operate(async () => {
      if (!run?.turns[0]) return;
      const generation = epoch.current,
        detail = await api.detail(run.id, undefined, run.turns[0].sequence);
      if (valid(generation))
        setRun({ ...run, turns: [...detail.turns, ...run.turns] });
    });
  const renderSources = (result: Turn["result"]) => {
    const sources = result.references.flatMap(id => {
      const source = result.sources.find(item => item.source_id === id);
      return source ? [source] : [];
    });
    return sources.length > 0 && (
      <details className="journal-sources">
        <summary>来源：{sources.length} 条引用</summary>
        <div>{sources.map(source => (
          <button type="button" key={source.source_id} className="text-button"
            onClick={() => onOpenSource(source)}>{source.title}</button>
        ))}</div>
      </details>
    );
  };
  const renderResult = (result: Turn["result"]) => (
    <>
      <div className="journal-prose">{result.narrative}</div>
      {renderSources(result)}
    </>
  );

  return (
    <section className="adventure-journal" aria-label="冒险记录">
      {loadingRuns && <p role="status" className="muted">正在读取旅程…</p>}
      {!loadingRuns && view === "landing" && !showCreate && <section className="journey-landing" aria-label="冒险入口">
        <button className="primary" disabled={busy} onClick={() => run ? onViewChange?.("adventure") : setShowCreate(true)}>{run ? "继续冒险" : "开始旅程"}</button>
        <button className="secondary" disabled={busy} onClick={() => onViewChange?.("saves")}>存档与读档</button>
      </section>}
      {!loadingRuns && view === "adventure" && !run && !showCreate && <div className="journal-empty">
        <button className="primary" onClick={() => setShowCreate(true)}>开始旅程</button>
      </div>}
      {showCreate && <JourneyOpening worldId={worldId} sessionId={sessionId} onConfigure={onConfigure}
        onCancel={() => setShowCreate(false)} onStarted={async created => {
          setShowCreate(false); await choose(created.id);
        }} />}
      {view === "saves" && !showCreate && <>
        <JourneyManager worldId={worldId} currentRun={run} onResume={choose}
          onCreate={() => setShowCreate(true)} onBack={() => onViewChange?.("landing")} />

      </>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {run && view === "adventure" && !showCreate && (
        <>
          <div className="journal-tools" aria-label="冒险工具">
            <div className="journal-scene">
              {run.character?.location && <span><MapPin size={14} />{run.character.location}</span>}
              {run.opening && run.character && <span><Clock3 size={14} />{formatStoryTime(run.opening.starts_at, run.character.elapsed_minutes)}</span>}
            </div>
            <div className="journal-tool-buttons">
              <button type="button" className="text-button" aria-label="返回冒险入口" onClick={() => onViewChange?.("landing")}><ArrowLeft size={17} /></button>
              <button type="button" className="text-button" aria-haspopup="dialog" onClick={() => setPanel("character")}><UserRound size={17} />角色</button>
              <button type="button" className="text-button" aria-haspopup="dialog" onClick={() => setPanel("news")}><MessagesSquare size={17} />消息</button>
            </div>
          </div>
          {run.opening && <details open={run.revision === 0} className="journal-sources">
            <summary>开场 · {run.opening.name}</summary>
            <p className="journal-prose">{run.opening.intro}</p>
          </details>}
          {run.turns[0]?.sequence > 1 && (
            <button className="text-button" disabled={busy} onClick={older}>
              更早的回合
            </button>
          )}
          {run.turns.map((turn) => (
            <article className="journal-turn" key={turn.sequence}>
              <div className="journal-action"><Feather size={19} aria-hidden="true" /><div><small>你的行动</small><p>{turn.action}</p></div></div>
              <p className="journal-turn-marker muted">{formatStoryTime(run.opening?.starts_at, turn.result.state_after?.elapsed_minutes ?? 0)} · 第 {turn.sequence} 回合</p>
              {renderResult(turn.result)}
              {turn.result.state_after && <details className="journal-sources">
                <summary>查看本回合状态</summary>
                <CharacterCard state={turn.result.state_after} title="本回合确认后的状态" />
              </details>}
              {turn.result.clock && <ClockSummary clock={turn.result.clock} start={run.opening?.starts_at} />}
              <MemoryChanges changes={turn.result.memory_changes} title="本回合日志" />
            </article>
          ))}
          {run.turns.length === 0 && (
            <p className="muted">旅程尚未落笔。写下第一个行动。</p>
          )}
          {draft?.status === "pending" && (
            <p role="status">
              故事正在生成。<button type="button" className="text-button" disabled={busy} onClick={() => operate(() => refresh(run.id))}>检查生成结果</button>
            </p>
          )}
          {draft?.status === "ready" && draft.result && (
            <article className="journal-turn journal-draft" ref={draftCard}>
              <strong>待确认草案 · 尚未写入回合</strong>
<div className="journal-action"><Feather size={19} /><div><small>你的行动</small><p>{draft.action}</p></div></div>
              {renderResult(draft.result)}
              {Array.isArray(
                draft.result.proposed_changes?.inventory_changes,
              ) && (
                <div className="inventory-proposal" aria-label="物品增减提议">
                  <strong>待确认物品变化</strong>
                  <ul>
                    {draft.result.proposed_changes.inventory_changes.map(
                      (item) => (
                        <li key={item.name}>
                          {item.name}：{item.delta > 0 ? "+" : ""}
                          {item.delta}
                        </li>
                      ),
                    )}
                  </ul>
                </div>
              )}
              {draft.result.projected_state && <CharacterCard
                state={draft.result.projected_state} title="模型状态提议 · 尚未生效"
              />}
              {draft.result.clock && <ClockSummary clock={draft.result.clock} start={run.opening?.starts_at} pending />}
              <MemoryChanges changes={draft.result.memory_proposal} title="待确认日志提议" />
              {requiredClock && <p className="muted">确认后会一并保存时间、事件和本回合的状态变化。</p>}
              {!requiredClock && (draft.result.projected_state || hasMemoryProposal) && <label className="journal-apply">
                <input type="checkbox" checked={applyChanges} disabled={busy || staleMemory}
                  onChange={event => setAcceptedDraft({ key: draftKey, value: event.target.checked })} />
                {hasMemoryProposal ? "一并采用日志与结算提议" : "采用这份状态提议（默认只保存叙事）"}
              </label>}
              {staleMemory && <p className="muted text-sm">日志已更正，请重新生成这段草案。</p>}
              <button className="primary" disabled={busy || staleMemory} onClick={accept}>
                确认并写入回合
              </button>
              <button
                className="text-button"
                disabled={busy}
                onClick={() =>
                  operate(async () => {
                    const generation = epoch.current;
                    await api.discard(run.id, draft.id);
                    if (valid(generation)) {
                      setAction((current) => current || draft.action);
                      setDraft(null);
                      setExpanded(true);
                    }
                  })
                }
              >
                暂不采用，重新编辑
              </button>
            </article>
          )}
          {run.mode === "live" && !sessionId && (
            <p className="muted">连接模型后即可继续。<button type="button" className="text-button"
              onClick={onConfigure}>前往设置</button></p>
          )}
          {draft?.status !== "ready" && draft?.status !== "pending" && <ActionComposer id="journal-action-editor" draft={action} setDraft={setAction}
            expanded={expanded} setExpanded={setExpanded}
            suggestions={[...new Set([
              ...(run.turns[run.turns.length - 1]?.result.suggestions ?? []),
              ...(run.opening?.shortcut ? [run.opening.shortcut] : []),
            ])]} limit={2000}
            busy={busy} stopping={busy && active.current !== null}
            disabled={run.mode === "live" && !sessionId}
            send={generate} stop={stop} />}
          {run.memory && <MemoryPanel key={`memory-${run.id}`} memory={run.memory} disabled={busy}
            onCorrect={async correction => {
              const generation = epoch.current;
              const memory = await api.correctMemory(run.id, correction);
              if (valid(generation)) setRun(current => current && { ...current, memory });
            }} />}

          {panel === "character" && <JournalOverlay title="角色" onClose={() => setPanel(null)}>
          {run.character ? (
            <CharacterCard key={`character-${run.id}`} state={run.character} title="角色信息"
              descriptions={run.item_descriptions} disabled={busy}
              onSaveDescription={async (name, description) => {
                const generation = epoch.current;
                const saved = await api.saveItemDescription(run.id, name, description);
                if (valid(generation)) {
                  setRun(current => current && {
                    ...current,
                    item_descriptions: { ...current.item_descriptions, [saved.name]: saved.description },
                  });
                }
              }} />
          ) : (
            <p className="muted">
              这段旅程尚无角色资料。
            </p>
          )}
          </JournalOverlay>}
          {panel === "news" && <JournalOverlay title="世界消息" onClose={() => setPanel(null)}>
            <Suspense fallback={<p role="status" className="muted">正在打开消息…</p>}>
              {!newsFact ? <WorldKnowledgePanel adventureId={run.id} startsAt={run.opening?.starts_at}
                refreshTrigger={newsRefresh} onVerify={setNewsFact} /> : <>
              <div className="journal-heading">
                <button type="button" className="text-button" onClick={() => setNewsFact(null)}>返回消息列表</button>
                <button type="button" className="text-button" onClick={() => {
                  flushSync(() => { setPanel(null); setExpanded(true); });
                  const input = document.getElementById("journal-action-editor-input") as HTMLTextAreaElement | null;
                  input?.focus();
                  input?.setSelectionRange(input.value.length, input.value.length);
                }}>回到冒险编辑行动</button>
              </div>
              <WorldEncounterPanel key={`encounters-${run.id}-${newsFact}`} adventureId={run.id} defaultOpen focusFactId={newsFact}
                composerDraft={action} onComposerDraftChange={setAction} onExpandComposer={setExpanded}
                refreshTrigger={`${run.revision}:${newsRefresh}`}
                disabled={busy || draft?.status === "ready" || draft?.status === "pending"}
                onTurnCommitted={() => {
                  setAction("");
                  setExpanded(false);
                  setNewsRefresh(value => value + 1);
                  setPanel(null);
                  setNewsFact(null);
                  void operate(() => refresh(run.id));
                }} />
              </>}
            </Suspense>
          </JournalOverlay>}

        </>
      )}
    </section>
  );
}

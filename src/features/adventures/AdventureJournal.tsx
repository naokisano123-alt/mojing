import { useEffect, useRef, useState } from "react";
import {
  adventureApi,
  type Detail,
  type Draft,
  type Run,
  type Turn,
  type InventoryItem,
  type Opening,
  type ClockResult,
} from "./api";
import { ActionComposer } from "../../components/ActionComposer";
import { ApiClientError } from "../../api/client";
import { InventoryEditor } from "./InventoryEditor";
import { checkpointApi as defaultCheckpointApi } from "./checkpointApi";
import { CheckpointPanel } from "./CheckpointPanel";
import { MemoryChanges, MemoryPanel } from "./MemoryPanel";
import { WorldKnowledgePanel } from "../world-knowledge";
import { WorldPulsePanel } from "../world-pulse";
import { PulsePreview } from "./PulsePreview";
import { CharacterCard } from "./CharacterCard";
import type { NarrationSource } from "../narration-preview/types";
import "./adventures.css";

function worldTime(start: string, minutes: number) {
  return new Date(Date.parse(start + "Z") + minutes * 60000).toISOString().slice(0, 16).replace("T", " ");
}

function ClockSummary({ clock, pending }: { clock: ClockResult; pending?: boolean }) {
  return <aside className="journal-character" aria-label={pending ? "待确认时间结算" : "时间结算"}>
    <strong>{pending ? "待确认 · " : ""}{clock.interrupted ? "遇到需要决定的事件，时间停在" : "世界时间推进至"} {clock.to.replace("T", " ")}</strong>
    <p>本回合经过 {clock.elapsed_minutes} 分钟{clock.elapsed_minutes < clock.requested_minutes ? `（原计划 ${clock.requested_minutes} 分钟）` : ""}</p>
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
  sessionId,
  onConfigure,
  onSessionExpired,
  onOpenSource,
  api = adventureApi,
  checkpointApi = defaultCheckpointApi,
}: {
  worldId: string;
  sessionId?: string;
  onConfigure: () => void;
  onSessionExpired?: () => void;
  onOpenSource: (source: NarrationSource) => void;
  api?: typeof adventureApi;
  checkpointApi?: typeof defaultCheckpointApi;
}) {
  const [openings, setOpenings] = useState<(Opening & { id: string })[]>([]);
  const [openingId, setOpeningId] = useState("");
  const [advanceHours, setAdvanceHours] = useState<number>();
  const selectedOpening = openings.find(item => item.id === openingId);
  const [characterName, setCharacterName] = useState("旅人");
  const [initialInventory, setInitialInventory] = useState<InventoryItem[]>([]);
  const [initialCoins, setInitialCoins] = useState(0);
  const [initialLocation, setInitialLocation] = useState("起点");
  const [acceptedDraft, setAcceptedDraft] = useState<{ key: string | null; value: boolean }>();
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [hasMoreRuns, setHasMoreRuns] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]),
    [run, setRun] = useState<Detail | null>(null);
  const [name, setName] = useState("新的旅程"),
    [mode, setMode] = useState<"fake" | "live">();
  const [expanded, setExpanded] = useState(false);
  const [newsOpen, setNewsOpen] = useState(false);
  const [pulseOpen, setPulseOpen] = useState(false);
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
  const nextMode = mode ?? (sessionId ? "live" : "fake");
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
    setOpenings([]);
    setOpeningId("");
    setAdvanceHours(undefined);
    void api.listOpenings?.(worldId, controller.signal).then(data => {
      if (valid(generation) && Array.isArray(data.items)) setOpenings(data.items);
    }).catch(() => { /* Older endpoints still allow freeform creation. */ });
    setShowCreate(false);
    setRuns([]);
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
        setRuns(data.items);
        setHasMoreRuns(data.items.length === 30);
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
    epoch.current++;
    active.current?.abort();
    active.current = null;
    locked.current = false;
    setBusy(false);
    setRun(null);
    setDraft(null);
    setExpanded(false);
    setAdvanceHours(undefined);
    setAction("");
    const generation = epoch.current;
    setError("");
    try {
      const detail = await api.detail(id);
      if (valid(generation)) {
        setRun(detail);
        setDraft(detail.latest_draft);
      }
    } catch (e) {
      if (valid(generation)) setError(String(e));
    }
  };
  const generate = (submittedAction: string) =>
    operate(async () => {
      if (!run || !submittedAction.trim()) return;
      if (advanceHours !== undefined && (!Number.isInteger(advanceHours) || advanceHours < 1 || advanceHours > 168)) {
        setError("请输入 1 至 168 小时的快进时长。");
        return;
      }
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
          ...(advanceHours === undefined ? {} : { advance_minutes: advanceHours * 60 }),
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
      "已停止等待，未确认的内容不会写入回合。可点击“刷新记录”查询草案；上游仍可能计费。",
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
        if (valid(generation)) { setExpanded(false); setAction(""); setAdvanceHours(undefined); }
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
      {result.state_after && (
        <CharacterCard
          state={result.state_after}
          title={
            result.changes_accepted
              ? "本回合确认后的状态"
              : "本回合仅记录叙事，状态保持原样"
          }
        />
      )}
      {renderSources(result)}
    </>
  );
  return (
    <section className="adventure-journal" aria-label="冒险记录">
      <button type="button" className="secondary" disabled={busy} onClick={onConfigure}>
        {sessionId ? "管理模型连接" : "连接模型 API"}
      </button>
      {loadingRuns && <p role="status" className="muted">正在读取旅程…</p>}
      {runs.length > 0 && (
        <button type="button" className="text-button" disabled={busy}
          aria-expanded={showCreate} onClick={() => setShowCreate(value => !value)}>
          {showCreate ? "收起新旅程" : "开启另一段旅程"}
        </button>
      )}
      {!loadingRuns && (showCreate || runs.length === 0) && (
        <div className="journal-create">
          <label>冒险名称
          <input
            aria-label="冒险名称"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
          </label>
          {openings.length > 0 && <label>选择开局
            <select value={openingId} onChange={event => setOpeningId(event.target.value)}>
              <option value="">自由开局</option>
              {openings.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>}
          {selectedOpening ? <details className="journal-sources">
            <summary>预览开局 · {selectedOpening.starts_at.replace("T", " ")}</summary>
            <p className="journal-prose">{selectedOpening.intro}</p>
            <CharacterCard state={selectedOpening.character} title="初始状态" />
          </details> : <>
          <label>角色姓名
          <input
            aria-label="角色姓名"
            value={characterName}
            maxLength={80}
            onChange={(e) => setCharacterName(e.target.value)}
          />
          </label>
          <label>初始位置
          <input
            aria-label="初始位置"
            value={initialLocation}
            maxLength={80}
            onChange={(e) => setInitialLocation(e.target.value)}
          />
          </label>
          <label>
            初始铜币
            <input
              aria-label="初始铜币"
              type="number"
              min={0}
              max={1000000000}
              step={1}
              value={initialCoins}
              onChange={(e) => setInitialCoins(Number(e.target.value))}
            />
          </label>
          <p className="muted">
            新角色体力100、精神100，累计耗时从0分钟开始。初始化只在创建旅程时生效。
          </p>
          <InventoryEditor
            items={initialInventory}
            onChange={setInitialInventory}
          />
          </>}
          <select
            aria-label="新冒险模式"
            value={nextMode}
            onChange={(e) => setMode(e.target.value as "fake" | "live")}
          >
            <option value="fake">模拟旅程（免 API Key）</option>
            <option value="live">真实模型旅程</option>
          </select>
          <button
            className="primary"
            disabled={busy || !name.trim()}
            onClick={() =>
              operate(async () => {
                const generation = epoch.current,
                  created = selectedOpening
                    ? await api.create(worldId, name, nextMode, undefined, selectedOpening.id)
                    : await api.create(worldId, name, nextMode, {
                      name: characterName, location: initialLocation, coins: initialCoins, inventory: initialInventory,
                    });
                if (valid(generation)) {
                  setRuns((prev) => [created, ...prev]);
                  setShowCreate(false);
                  await refresh(created.id);
                  if (valid(generation)) { setExpanded(false); setAction(""); setAdvanceHours(undefined); }
                }
              })
            }
          >
            新建旅程
          </button>
        </div>
      )}
      {runs.length > 0 && (
        <select
          aria-label="选择冒险记录"
          value={run?.id || ""}
          disabled={busy}
          onChange={(e) => void choose(e.target.value)}
        >
          {runs.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} · {item.mode === "fake" ? "Fake测试" : "Live模型"}
            </option>
          ))}
        </select>
      )}
      {hasMoreRuns && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() =>
            operate(async () => {
              const generation = epoch.current,
                page = await api.list(worldId, undefined, runs.length);
              if (valid(generation)) {
                setRuns((prev) => [
                  ...prev,
                  ...page.items.filter(
                    (item) => !prev.some((old) => old.id === item.id),
                  ),
                ]);
                setHasMoreRuns(page.items.length === 30);
              }
            })
          }
        >
          读取更多旅程
        </button>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {run && (
        <>
          <div className="journal-heading">
            <h3>{run.name}</h3>
            <span>
              {run.mode === "fake" ? "Fake · 无真实AI" : "Live · 模型叙事"} ·
              已确认 {run.revision} 回合
            </span>
          </div>
          {run.opening && run.character && <p className="muted">
            世界时间 · {worldTime(run.opening.starts_at, run.character.elapsed_minutes)}
          </p>}
          {run.opening && <details open={run.revision === 0} className="journal-sources">
            <summary>开场 · {run.opening.name}</summary>
            <p className="journal-prose">{run.opening.intro}</p>
          </details>}
          <button
            className="text-button"
            disabled={busy}
            onClick={() => operate(() => refresh(run.id))}
          >
            刷新记录 / 恢复草案
          </button>
          {run.origin_checkpoint_id && (
            <p className="muted">
              当前为存档分支，继承检查点之前的确认历史，原路线保持不变。
            </p>
          )}
          <CheckpointPanel
            api={checkpointApi}
            run={run}
            disabled={busy}
            onRestored={async (branch) => {
              setRuns((prev) => [
                branch,
                ...prev.filter((item) => item.id !== branch.id),
              ]);
              if (locked.current) {
                setError("分支已创建，请在当前操作结束后从旅程列表切换。");
                return;
              }
              await choose(branch.id);
            }}
          />
          {run.character ? (
            <CharacterCard key={run.id} state={run.character} title="当前角色状态"
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
              旧旅程未初始化角色，继续仅记录叙事。新建旅程可使用角色状态。
            </p>
          )}
          {run.memory && <MemoryPanel key={run.id} memory={run.memory} disabled={busy}
            onCorrect={async correction => {
              const generation = epoch.current;
              const memory = await api.correctMemory(run.id, correction);
              if (valid(generation)) setRun(current => current && { ...current, memory });
            }} />}
          {run.turns[0]?.sequence > 1 && (
            <button className="text-button" disabled={busy} onClick={older}>
              更早的回合
            </button>
          )}
          {run.turns.map((turn) => (
            <article className="journal-turn" key={turn.sequence}>
              <span className="muted">第 {turn.sequence} 回合</span>
              <p className="journal-action">{turn.action}</p>
              {renderResult(turn.result)}
              {turn.result.clock && <ClockSummary clock={turn.result.clock} />}
              <MemoryChanges changes={turn.result.memory_changes} title="本回合日志" />
            </article>
          ))}
          {run.turns.length === 0 && (
            <p className="muted">旅程尚未落笔。写下第一个行动。</p>
          )}
          {draft?.status === "pending" && (
            <p role="status">
              草案仍在生成；请稍后刷新。中断的请求90秒后失效。
            </p>
          )}
          {draft?.status === "ready" && draft.result && (
            <article className="journal-turn journal-draft" ref={draftCard}>
              <strong>待确认草案 · 尚未写入回合</strong>
              <p>{draft.action}</p>
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
              {draft.result.clock && <ClockSummary clock={draft.result.clock} pending />}
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
            <p className="error">连接模型 API 后，即可继续这段旅程。</p>
          )}
          {run.opening && draft?.status !== "ready" && draft?.status !== "pending" && <div className="flex flex-wrap gap-2">
            <button type="button" className="secondary" disabled={busy} onClick={() => {
              setAdvanceHours(value => value === undefined ? 24 : undefined);
              setExpanded(true);
            }}>{advanceHours === undefined ? "指定快进时长" : "取消指定时长"}</button>
            {advanceHours !== undefined && <label>快进小时数 <input type="number" min={1} max={168}
              value={Number.isNaN(advanceHours) ? "" : advanceHours} disabled={busy}
              onChange={event => setAdvanceHours(event.target.valueAsNumber)} /></label>}
          </div>}
          {draft?.status !== "ready" && draft?.status !== "pending" && <ActionComposer id="journal-action-editor" draft={action} setDraft={setAction}
            expanded={expanded} setExpanded={setExpanded}
            suggestions={[...new Set([
              ...(run.turns[run.turns.length - 1]?.result.suggestions ?? []),
              ...(run.opening?.shortcut ? [run.opening.shortcut] : []),
            ])]} limit={2000}
            busy={busy} stopping={busy && active.current !== null}
            disabled={run.mode === "live" && !sessionId}
            send={generate} stop={stop} />}
          <details
            className="journal-sources"
            aria-label="世界消息"
            onToggle={event => setNewsOpen(event.currentTarget.open)}
          >
            <summary>世界消息 · 只显示你有渠道得知的消息</summary>
            {newsOpen && <>
              {run.mode === "fake" && <PulsePreview key={run.id} adventureId={run.id} />}
              <WorldKnowledgePanel adventureId={run.id} startsAt={run.opening?.starts_at} />
            </>}
          </details>
        </>
      )}
    </section>
  );
}
import { useEffect, useRef, useState } from "react";
import {
  adventureApi,
  type Detail,
  type Draft,
  type Run,
  type Turn,
} from "./api";
import type { NarrationSource } from "../narration-preview/types";
import "./adventures.css";

export function AdventureJournal({
  worldId,
  sessionId,
  onConfigure,
  onOpenSource,
  api = adventureApi,
}: {
  worldId: string;
  sessionId?: string;
  onConfigure: () => void;
  onOpenSource: (source: NarrationSource) => void;
  api?: typeof adventureApi;
}) {
  const [hasMoreRuns, setHasMoreRuns] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]),
    [run, setRun] = useState<Detail | null>(null);
  const [name, setName] = useState("新的旅程"),
    [mode, setMode] = useState<"fake" | "live">("fake");
  const [action, setAction] = useState(""),
    [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
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
    setRuns([]);
    setRun(null);
    setDraft(null);
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
      if (valid(generation))
        setError(e instanceof Error ? e.message : "操作失败，请重试");
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
  const generate = () =>
    operate(async () => {
      if (!run || !action.trim()) return;
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
          action,
          expected_revision: run.revision,
          session_id: run.mode === "live" ? sessionId : undefined,
        },
        controller.signal,
      );
      if (valid(generation) && !controller.signal.aborted) {
        setDraft(result);
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
      await api.commit(run.id, draft.id);
      if (valid(generation)) {
        await refresh(run.id);
        if (valid(generation)) setAction("");
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
  const renderResult = (result: Turn["result"]) => (
    <>
      <div className="journal-prose">{result.narrative}</div>
      <div className="journal-sources">
        {result.references.map((id) => {
          const source = result.sources.find((s) => s.source_id === id);
          return source ? (
            <button
              type="button"
              key={id}
              className="text-button"
              onClick={() => onOpenSource(source)}
            >
              来源：{source.title}
            </button>
          ) : null;
        })}
      </div>
    </>
  );
  return (
    <section className="adventure-journal" aria-label="冒险记录">
      <p className="muted">
        连续剧情仅参考已确认回合。此阶段记录叙事，不结算属性、时间或财物。
      </p>
      <div className="journal-create">
        <input
          aria-label="冒险名称"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          aria-label="新冒险模式"
          value={mode}
          onChange={(e) => setMode(e.target.value as "fake" | "live")}
        >
          <option value="fake">Fake 测试旅程</option>
          <option value="live">Live 模型旅程</option>
        </select>
        <button
          className="primary"
          disabled={busy || !name.trim()}
          onClick={() =>
            operate(async () => {
              const generation = epoch.current,
                created = await api.create(worldId, name, mode);
              if (valid(generation)) {
                setRuns((prev) => [created, ...prev]);
                await refresh(created.id);
                if (valid(generation)) setAction("");
              }
            })
          }
        >
          新建旅程
        </button>
      </div>
      <button className="text-button" onClick={onConfigure}>
        配置 / 测试模型连接
      </button>
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
          <button
            className="text-button"
            disabled={busy}
            onClick={() => operate(() => refresh(run.id))}
          >
            刷新记录 / 恢复草案
          </button>
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
            <article className="journal-turn journal-draft">
              <strong>待确认草案 · 尚未写入回合</strong>
              <p>{draft.action}</p>
              {renderResult(draft.result)}
              <button className="primary" disabled={busy} onClick={accept}>
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
                    }
                  })
                }
              >
                暂不采用，重新编辑
              </button>
            </article>
          )}
          {run.mode === "live" && !sessionId && (
            <p className="error">请先到“叙事实验”建立模型会话，再返回继续。</p>
          )}
          <textarea
            aria-label="下一步行动"
            placeholder="接下来，你打算做些什么？"
            value={action}
            maxLength={2000}
            disabled={busy}
            onChange={(e) => setAction(e.target.value)}
          />
          <div className="journal-footer">
            <span className="muted">{action.length}/2000</span>
            {busy && active.current ? (
              <button className="secondary" onClick={stop}>
                停止生成
              </button>
            ) : (
              <button
                className="primary"
                disabled={
                  busy ||
                  !action.trim() ||
                  draft?.status === "ready" ||
                  (run.mode === "live" && !sessionId)
                }
                onClick={generate}
              >
                生成下一段草案
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

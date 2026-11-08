import { useEffect, useRef, useState } from "react";
import { checkpointApi, type Checkpoint } from "./checkpointApi";
import type { Run } from "./api";
const id = () =>
  globalThis.crypto?.randomUUID?.() ||
  Date.now().toString(36) + "_" + Math.random().toString(36).slice(2);

export function CheckpointPanel({
  run,
  disabled = false,
  onRestored,
  api = checkpointApi,
}: {
  run: Run;
  disabled?: boolean;
  onRestored: (run: Run) => Promise<void>;
  api?: typeof checkpointApi;
}) {
  const [items, setItems] = useState<Checkpoint[]>([]),
    [more, setMore] = useState(false);
  const [name, setName] = useState("旅程存档"),
    [branchName, setBranchName] = useState("新的分支");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const generation = useRef(0),
    locked = useRef(false),
    saveRequest = useRef<{ signature: string; id: string } | null>(null),
    restoreRequests = useRef(new Map<string, string>());
  useEffect(() => {
    const epoch = ++generation.current,
      controller = new AbortController();
    setItems([]);
    setError("");
    setBusy(false);
    locked.current = false;
    saveRequest.current = null;
    restoreRequests.current.clear();
    setBranchName(Array.from(run.name).slice(0, 70).join("") + " · 分支");
    void api
      .list(run.id, controller.signal)
      .then((page) => {
        if (generation.current === epoch) {
          setItems(page.items);
          setMore(page.items.length === 30);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && generation.current === epoch)
          setError(e.message);
      });
    return () => {
      generation.current++;
      controller.abort();
    };
  }, [run.id]);
  const operate = async (work: (epoch: number) => Promise<void>) => {
    if (disabled || locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    try {
      await work(epoch);
    } catch (e) {
      if (generation.current === epoch)
        setError(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      if (generation.current === epoch) {
        setBusy(false);
        locked.current = false;
      }
    }
  };
  const save = () =>
    operate(async (epoch) => {
      const signature = JSON.stringify([run.id, name.trim(), run.revision]);
      if (saveRequest.current?.signature !== signature)
        saveRequest.current = { signature, id: id() };
      const saved = await api.save(run.id, {
        name: name.trim(),
        request_id: saveRequest.current.id,
        expected_revision: run.revision,
      });
      if (generation.current !== epoch) return;
      saveRequest.current = null;
      setItems((prev) => [
        saved,
        ...prev.filter((item) => item.id !== saved.id),
      ]);
    });
  const restore = (checkpoint: Checkpoint) =>
    operate(async (epoch) => {
      const signature = JSON.stringify([checkpoint.id, branchName.trim()]);
      let requestId = restoreRequests.current.get(signature);
      if (!requestId) {
        requestId = id();
        restoreRequests.current.set(signature, requestId);
      }
      const branch = await api.restore(checkpoint.id, {
        name: branchName.trim(),
        request_id: requestId,
      });
      if (generation.current !== epoch) return;
      if (branch.world_id !== run.world_id)
        throw new Error("返回的分支不属于当前世界");
      restoreRequests.current.delete(signature);
      await onRestored(branch);
    });
  return (
    <section className="journal-checkpoints" aria-label="存档与分支">
      <h3>存档与分支</h3>
      <p className="muted">
        只保存已确认进度，不含未确认草案。恢复会新建分支，保留原路线；世界设定资料仍共享。
      </p>
      <div className="checkpoint-controls">
        <input
          aria-label="存档名称"
          value={name}
          maxLength={80}
          disabled={disabled || busy}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          className="secondary"
          disabled={disabled || busy || !name.trim()}
          onClick={save}
        >
          保存当前检查点（第{run.revision}回合）
        </button>
      </div>
      <input
        aria-label="恢复后的分支名称"
        value={branchName}
        maxLength={80}
        disabled={disabled || busy}
        onChange={(e) => setBranchName(e.target.value)}
      />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items.length === 0 && (
        <p className="muted">还没有检查点，可在第0回合保存初始角色。</p>
      )}
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <div>
              <strong>{item.name}</strong>
              <p className="muted">
                第{item.revision}回合 ·{" "}
                {item.mode === "fake" ? "Fake测试" : "Live模型"} ·{" "}
                {new Date(item.created_at * 1000).toLocaleString()}
              </p>
            </div>
            <button
              className="secondary"
              disabled={disabled || busy || !branchName.trim()}
              onClick={() => restore(item)}
              aria-label={"从" + item.name + "创建分支"}
            >
              从此处创建分支
            </button>
          </li>
        ))}
      </ul>
      {more && (
        <button
          className="text-button"
          disabled={disabled || busy}
          onClick={() =>
            operate(async (epoch) => {
              const page = await api.list(run.id, undefined, items.length);
              if (generation.current === epoch) {
                setItems((prev) => [
                  ...prev,
                  ...page.items.filter(
                    (item) => !prev.some((old) => old.id === item.id),
                  ),
                ]);
                setMore(page.items.length === 30);
              }
            })
          }
        >
          更多检查点
        </button>
      )}
    </section>
  );
}

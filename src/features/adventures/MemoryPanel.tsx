import { useState } from "react";
import type { FormEvent } from "react";
import { ApiClientError } from "../../api/client";
import type { MemoryChange, MemoryCorrection, MemoryFields, MemoryKind, MemoryStatus, MemoryView } from "./api";

const kinds: Record<MemoryKind, string> = { deposit: "押金", commitment: "约定", secret: "秘密" };
const statuses: Record<MemoryStatus, string> = {
  active: "未结", refund: "已退还", forfeit: "已扣留", done: "已完成",
};
const empty: MemoryFields = { kind: "commitment", title: "", detail: "", status: "active", person: "" };

function label(item: MemoryFields) {
  const tag = kinds[item.kind];
  if (item.kind === "secret") return `${tag} · ${item.title} · 知情：${item.knowers?.join("、") || "无已知知情人"}`;
  const amount = item.amount ? ` · ${item.amount} 币` : "";
  return `${tag} · ${item.title} · ${item.person}${amount} · ${statuses[item.status]}`;
}

export function MemoryChanges({ changes, title }: { changes?: MemoryChange[]; title: string }) {
  if (!changes?.length) return null;
  return <details className="journal-sources">
    <summary>{title}（{changes.length}）</summary>
    <ul className="grid gap-2 text-sm">
      {changes.map(change => <li key={change.id}>
        {change.item ? label(change.item) : "撤销条目"}
        {change.reason && <p className="muted">{change.reason}</p>}
      </li>)}
    </ul>
  </details>;
}

export function MemoryPanel({ memory, disabled, onCorrect }: {
  memory: MemoryView;
  disabled: boolean;
  onCorrect: (correction: MemoryCorrection) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [editor, setEditor] = useState<{ id?: string; item: MemoryFields; remove?: boolean }>();
  const [reason, setReason] = useState("");
  const [people, setPeople] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  function edit(item: MemoryFields = empty, id?: string, remove = false) {
    setReason("");
    setPeople(item.knowers?.join("、") ?? "");
    setError("");
    setEditor({ item, id, remove });
  }

  function change(fields: Partial<MemoryFields>) {
    setEditor(current => current && { ...current, item: { ...current.item, ...fields } });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!editor || saving || disabled) return;
    setSaving(true);
    setError("");
    try {
      const item = editor.item.kind === "secret"
        ? { ...editor.item, knowers: people.split(/[、，,\n]/).map(name => name.trim()).filter(Boolean) }
        : editor.item;
      await onCorrect({
        expected_cursor: memory.cursor, id: editor.id, item: editor.remove ? null : item, reason,
      });
      setEditor(undefined);
    } catch (failure) {
      setError(failure instanceof ApiClientError ? failure.friendlyMessage : "日志更正未保存，请重试。");
    } finally {
      setSaving(false);
    }
  }

  return <details className="journal-sources memory-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>世界日志与更正（{memory.items.length}）</summary>
    {open && <>
    <p className="muted text-sm">{memory.items.length ? "需要时可更正下列长期记录。" : "暂无长期记录，游戏会随旅程自动整理。"}</p>
    <ul className="grid gap-3 mt-3">
      {memory.items.map(item => <li key={item.id}>
        <strong>{label(item)}</strong>
        {item.detail && <p className="text-sm">{item.detail}</p>}
        <p className="muted text-xs">{item.turn ? `最近更新于第 ${item.turn} 回合` : "开局记录"}</p>
        <div className="flex flex-wrap gap-2">
          <button className="text-button" type="button" disabled={disabled || saving} onClick={() => edit(item, item.id)}>更正</button>
          <button className="text-button" type="button" disabled={disabled || saving} onClick={() => edit(item, item.id, true)}>撤销</button>
        </div>
      </li>)}
    </ul>
    <button className="secondary mt-3" type="button" disabled={disabled || saving} onClick={() => edit()}>补记一条</button>
    {!!memory.events.length && <details className="journal-sources">
      <summary>最近变更记录</summary>
      <ul className="grid gap-2">
        {memory.events.map(event => <li key={event.sequence} className="text-sm">
          <span className="muted">第 {event.turn} 回合 · {event.origin === "correction" ? "玩家更正" : "确认回合"} · </span>
          {event.item ? label(event.item) : "撤销条目"}{event.reason && ` · ${event.reason}`}
        </li>)}
      </ul>
    </details>}
    {editor && <form className="grid gap-2 mt-3" onSubmit={submit}>
      <strong>{editor.remove ? `撤销「${editor.item.title}」` : editor.id ? "更正日志" : "补记日志"}</strong>
      {!editor.remove && <>
        <label>类型 <select value={editor.item.kind} disabled={saving || disabled} onChange={event => {
          change({ kind: event.target.value as MemoryKind, status: "active" });
        }}>
          {Object.entries(kinds).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
        </select></label>
        <label>标题 <input required maxLength={100} value={editor.item.title} onChange={event => change({ title: event.target.value })} /></label>
        <label>说明 <textarea rows={2} maxLength={500} value={editor.item.detail} onChange={event => change({ detail: event.target.value })} /></label>
        {editor.item.kind === "secret"
          ? <label>知情人（用顿号分开）<input value={people} onChange={event => setPeople(event.target.value)} /></label>
          : <label>关联人物 <input required maxLength={80} value={editor.item.person ?? ""} onChange={event => change({ person: event.target.value })} /></label>}
        {editor.item.kind === "deposit" && <label>金额 <input required type="number" min={1} max={1000}
          value={editor.item.amount ?? ""} onChange={event => change({ amount: event.target.valueAsNumber })} /></label>}
        {editor.item.kind !== "secret" && <label>状态 <select value={editor.item.status} onChange={event => change({ status: event.target.value as MemoryStatus })}>
          <option value="active">未结</option>
          {editor.item.kind === "deposit" ? <>
            <option value="refund">已退还</option><option value="forfeit">已扣留</option>
          </> : <option value="done">已完成</option>}
        </select></label>}
      </>}
      {editor.item.kind === "deposit" && <p className="muted text-sm">更正押金记录只调整记忆，角色余额单独结算。</p>}
      <label>更正说明 <input required maxLength={300} value={reason} onChange={event => setReason(event.target.value)} /></label>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button className="primary" disabled={disabled || saving} type="submit">保存更正</button>
        <button className="secondary" disabled={saving} type="button" onClick={() => setEditor(undefined)}>取消</button>
      </div>
    </form>}
    </>}
  </details>;
}

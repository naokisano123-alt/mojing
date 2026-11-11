import { useEffect, useRef, useState } from "react";
import { InventoryEditor } from "./InventoryEditor";
import { CharacterCard } from "./CharacterCard";
import { openingApi, type OpeningSettings, type ScenarioEvent } from "./api";

const blank: OpeningSettings = {
  name: "", intro: "", starts_at: "1200-01-01T08:00", shortcut: "",
  character: { name: "旅人", location: "起点", coins: 0, stamina: 100, spirit: 100, elapsed_minutes: 0 },
  events: [],
};
const workshop: OpeningSettings = {
  ...blank, name: "细器铺试工",
  intro: "早上八点，黎斯来到刻希弗的南窗细器铺。塞文腾出半张工作台，让他先做几枚黄铜扣片，合格后按件结算。黎斯可以留下试工，也可以去别处找活。",
  character: { ...blank.character, name: "黎斯", location: "南窗细器铺", coins: 10 },
  shortcut: "接下来三天照常在细器铺做工，遇到需要我决定的事情就停下。",
  events: [
    { title: "细器铺开始营业", description: "门板卸下，铺面开张。", at_minute: 60, every_minutes: 1440, interrupt: false },
    { title: "急单到店", description: "第二天中午，一位客人送来急件。塞文拿着订单过来问黎斯愿不愿意接。", at_minute: 1680, every_minutes: null, interrupt: true },
    { title: "约定交货", description: "原先约定的验收时间到了，塞文准备查看扣片。", at_minute: 3120, every_minutes: null, interrupt: true },
  ],
};
const arrival: OpeningSettings = {
  ...blank, name: "初到刻希弗",
  intro: "黎斯沿着河岸走进刻希弗。黑鹿角酒馆刚开门，店门外还能看见通往东河货栈的路。",
  character: { ...blank.character, name: "黎斯", location: "黑鹿角酒馆门外" },
};

export function OpeningEditor({ worldId }: { worldId: string }) {
  const [items, setItems] = useState<(OpeningSettings & { id: string })[]>([]);
  const [draft, setDraft] = useState<OpeningSettings | null>(null);
  const [id, setId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const locked = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void openingApi.settings(worldId, controller.signal).then(data => {
      if (!Array.isArray(data.items)) throw new Error("开局列表格式无效。");
      if (!controller.signal.aborted) { setItems(data.items); setLoaded(true); }
    }).catch(failure => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "开局读取失败。");
    });
    return () => controller.abort();
  }, [worldId]);

  const edit = (value: OpeningSettings, openingId?: string) => {
    setId(openingId); setDraft(value); setError(""); setNotice("");
  };
  const patch = (values: Partial<OpeningSettings>) => setDraft(value => value && ({ ...value, ...values }));
  const patchEvent = (index: number, change: Partial<ScenarioEvent>) => setDraft(value => value && ({
    ...value, events: value.events.map((event, position) => position === index ? { ...event, ...change } : event),
  }));
  async function perform(work: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); setNotice("");
    try { await work(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "开局保存失败。"); }
    finally { locked.current = false; setBusy(false); }
  }

  return <section className="journal-create" aria-label="开局与日程">
    <h3>开局与日程</h3>
    <p className="muted">日程在这里编辑。玩家只会看到已经发生的事件。</p>
    {!loaded && !error && <p role="status">正在读取开局…</p>}
    <div className="flex flex-wrap gap-2">
      <button className="secondary" type="button" disabled={!loaded || busy} onClick={() => edit(blank)}>新建开局</button>
      <button className="text-button" type="button" disabled={!loaded || busy} onClick={() => edit(arrival)}>试用初到刻希弗</button>
      <button className="text-button" type="button" disabled={!loaded || busy} onClick={() => edit(workshop)}>试用细器铺试工</button>
    </div>
    {items.map(item => <button className="secondary" type="button" key={item.id} disabled={busy}
      onClick={() => edit(item, item.id)}>{item.name} · 编辑</button>)}
    {draft && <form className="journal-create" onSubmit={event => {
      event.preventDefault();
      void perform(async () => {
        const saved = await openingApi.save(worldId, draft, id);
        setItems(current => id ? current.map(item => item.id === id ? saved : item) : [...current, saved]);
        setId(saved.id); setDraft(saved); setNotice("开局已保存，可在新旅程中选择。");
      });
    }}>
      <fieldset disabled={busy} className="journal-create">
        <label>开局名称<input required maxLength={80} value={draft.name} onChange={event => patch({ name: event.target.value })} /></label>
        <label>开场正文<textarea required rows={5} maxLength={6000} value={draft.intro} onChange={event => patch({ intro: event.target.value })} /></label>
        <label>起始时间<input required type="datetime-local" max="7999-12-31T23:59" value={draft.starts_at} onChange={event => patch({ starts_at: event.target.value })} /></label>
        <label>角色姓名<input required maxLength={80} value={draft.character.name} onChange={event => patch({ character: { ...draft.character, name: event.target.value } })} /></label>
        <label>起始地点<input required maxLength={80} value={draft.character.location} onChange={event => patch({ character: { ...draft.character, location: event.target.value } })} /></label>
        <label>初始铜币<input required type="number" min={0} max={1000000000} value={draft.character.coins} onChange={event => patch({ character: { ...draft.character, coins: event.target.valueAsNumber } })} /></label>
        <label>角色近况<textarea maxLength={500} value={draft.character.condition ?? ""}
          placeholder="例如：右臂有旧伤，长途跋涉后略感疲惫。"
          onChange={event => patch({ character: { ...draft.character, condition: event.target.value } })} /></label>
        <InventoryEditor key={id ?? draft.name} items={draft.character.inventory ?? []}
          onChange={inventory => patch({ character: { ...draft.character, inventory } })} />
        <label>剧本预设行动<textarea maxLength={300} value={draft.shortcut} onChange={event => patch({ shortcut: event.target.value })} /></label>
        <details className="journal-sources">
          <summary>预览开场与角色</summary>
          <p className="journal-prose">{draft.intro}</p>
          <p>{draft.starts_at.replace("T", " ")}</p>
          <CharacterCard state={draft.character} title="初始状态" />
        </details>
        <h4>事件日程</h4>
        <p className="muted">时间以开局后经过的分钟数填写。1 天等于 1440 分钟。</p>
        {draft.events.map((event, index) => <fieldset className="journal-create" key={index}>
          <legend>事件 {index + 1}</legend>
          <label>事件名称<input required maxLength={80} value={event.title} onChange={e => patchEvent(index, { title: e.target.value })} /></label>
          <label>发生后的描述<textarea required maxLength={500} value={event.description} onChange={e => patchEvent(index, { description: e.target.value })} /></label>
          <label>开局后第几分钟发生<input required type="number" min={1} max={5256000} value={event.at_minute}
            onChange={e => patchEvent(index, { at_minute: e.target.valueAsNumber })} /></label>
          <label>重复间隔（分钟，留空为一次性）<input type="number" min={60} max={525600} value={event.every_minutes ?? ""}
            onChange={e => patchEvent(index, { every_minutes: e.target.value ? e.target.valueAsNumber : null })} /></label>
          <label className="journal-apply"><input type="checkbox" checked={event.interrupt}
            onChange={e => patchEvent(index, { interrupt: e.target.checked })} />发生时停下来等玩家决定</label>
          <button className="text-button" type="button" onClick={() => patch({ events: draft.events.filter((_, i) => i !== index) })}>移除此事件</button>
        </fieldset>)}
        <button className="secondary" type="button" disabled={draft.events.length >= 20} onClick={() => patch({
          events: [...draft.events, { title: "", description: "", at_minute: 60, every_minutes: null, interrupt: true }],
        })}>添加事件</button>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className="primary">保存开局</button>
          <button type="button" className="secondary" onClick={() => setDraft(null)}>关闭编辑</button>
          {id && <button type="button" className="text-button" onClick={() => {
            if (window.confirm("移除这个开局方案？已开始的旅程仍可继续。")) void perform(async () => {
              await openingApi.remove(worldId, id);
              setItems(current => current.filter(item => item.id !== id)); setDraft(null); setId(undefined);
            });
          }}>移除开局</button>}
        </div>
      </fieldset>
    </form>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}

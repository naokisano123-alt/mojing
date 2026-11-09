import { useRef, useState } from "react";
import type { CharacterState } from "./api";
export function CharacterCard({
  state,
  title,
  descriptions,
  disabled,
  onSaveDescription,
}: {
  state: CharacterState;
  title: string;
  descriptions?: Record<string, string>;
  disabled?: boolean;
  onSaveDescription?: (name: string, description: string) => Promise<void>;
}) {
  return (
    <aside className="journal-character" aria-label={title}>
      <strong>
        {title} · {state.name}
      </strong>
      <dl>
        <div>
          <dt>体力</dt>
          <dd>{state.stamina}/100</dd>
        </div>
        <div>
          <dt>精神</dt>
          <dd>{state.spirit}/100</dd>
        </div>
        <div>
          <dt>铜币</dt>
          <dd>{state.coins}</dd>
        </div>
        <div>
          <dt>累计耗时</dt>
          <dd>{state.elapsed_minutes}分钟</dd>
        </div>
        <div>
          <dt>位置</dt>
          <dd>{state.location}</dd>
        </div>
      </dl>
      <div className="inventory-list">
        <strong>背包</strong>
        {state.inventory?.length ? (
          <ul>
            {state.inventory.map((item) => (
              <li key={item.name}>
                {onSaveDescription ? (
                  <ItemDetails name={item.name} quantity={item.quantity} disabled={disabled}
                    description={descriptions && Object.hasOwn(descriptions, item.name) ? descriptions[item.name] : ""}
                    onSave={text => onSaveDescription(item.name, text)} />
                ) : <><span>{item.name}</span><span>× {item.quantity}</span></>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">暂无物品</p>
        )}
      </div>
    </aside>
  );
}


function ItemDetails({ name, quantity, description, disabled, onSave }: {
  name: string;
  quantity: number;
  description: string;
  disabled?: boolean;
  onSave: (value: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const savingRef = useRef(false);
  return <details className="item-details">
    <summary>{name} × {quantity}</summary>
    {draft === null ? <>
      <p className="item-description">{description || "还没有物品介绍。"}</p>
      <button type="button" className="text-button" disabled={disabled} onClick={() => {
        setError("");
        setDraft(description);
      }}>编辑介绍</button>
    </> : <form onSubmit={async event => {
      event.preventDefault();
      if (disabled || savingRef.current) return;
      savingRef.current = true;
      setSaving(true);
      setError("");
      try {
        await onSave(draft);
        setDraft(null);
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "物品介绍保存失败。");
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    }}>
      <textarea aria-label={name + "的介绍"} rows={4} value={draft} maxLength={2000}
        placeholder="可以写外观、来历、用途或使用限制……"
        disabled={saving || disabled} onChange={event => setDraft(event.target.value)} />
      <p className="muted">同一世界的同名物品共用介绍。留空保存可清除介绍。</p>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="item-description-actions">
        <button type="submit" className="secondary" disabled={saving || disabled}>保存介绍</button>
        <button type="button" className="text-button" disabled={saving} onClick={() => setDraft(null)}>取消</button>
      </div>
    </form>}
  </details>;
}

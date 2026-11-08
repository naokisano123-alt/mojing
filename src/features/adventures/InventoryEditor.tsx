import { useState } from "react";
import type { InventoryItem } from "./api";

export function InventoryEditor({
  items,
  onChange,
}: {
  items: InventoryItem[];
  onChange: (items: InventoryItem[]) => void;
}) {
  const [name, setName] = useState(""),
    [quantity, setQuantity] = useState(1),
    [error, setError] = useState("");
  const add = () => {
    const clean = name.trim().normalize("NFC");
    const existing = items.find((item) => item.name === clean);
    if (
      !clean ||
      clean.length > 80 ||
      !Number.isInteger(quantity) ||
      quantity < 1 ||
      quantity > 9999 ||
      (!existing && items.length >= 40) ||
      (existing && existing.quantity + quantity > 9999)
    ) {
      setError("请输入1–80字的名称和1–9999的整数数量，背包最多40种。");
      return;
    }
    onChange(
      existing
        ? items.map((item) =>
            item === existing
              ? { ...item, quantity: item.quantity + quantity }
              : item,
          )
        : [...items, { name: clean, quantity }],
    );
    setName("");
    setQuantity(1);
    setError("");
  };
  return (
    <fieldset className="inventory-editor">
      <legend>起始物品（可选）</legend>
      <p className="muted">同名物品作为一组数量记录，不含耐久或装备属性。</p>
      <input
        aria-label="物品名称"
        maxLength={80}
        value={name}
        placeholder="例如：干粮"
        onChange={(e) => setName(e.target.value)}
      />
      <input
        aria-label="物品数量"
        type="number"
        min={1}
        max={9999}
        step={1}
        value={quantity}
        onChange={(e) => setQuantity(Number(e.target.value))}
      />
      <button type="button" className="secondary" onClick={add}>
        加入起始背包
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <ul>
        {items.map((item) => (
          <li key={item.name}>
            <span>
              {item.name} × {item.quantity}
            </span>
            <button
              type="button"
              className="text-button"
              aria-label={"移除起始物品" + item.name}
              onClick={() =>
                onChange(items.filter((old) => old.name !== item.name))
              }
            >
              移除
            </button>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

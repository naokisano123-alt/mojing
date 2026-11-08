import React, { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect } from "vitest";
import { InventoryEditor } from "../../../src/features/adventures/InventoryEditor";
import { CharacterCard } from "../../../src/features/adventures/CharacterCard";
import type { InventoryItem } from "../../../src/features/adventures/api";
function Editor() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  return <InventoryEditor items={items} onChange={setItems} />;
}
it("starting items merge normalized names and can be removed", () => {
  render(<Editor />);
  fireEvent.change(screen.getByLabelText("物品名称"), {
    target: { value: " 干粮 " },
  });
  fireEvent.change(screen.getByLabelText("物品数量"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByText("加入起始背包"));
  expect(screen.getByText("干粮 × 2")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("物品名称"), {
    target: { value: "干粮" },
  });
  fireEvent.click(screen.getByText("加入起始背包"));
  expect(screen.getByText("干粮 × 3")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "移除起始物品干粮" }));
  expect(screen.queryByText("干粮 × 3")).toBeNull();
});
it("invalid quantities cannot silently enter initial inventory", () => {
  render(<Editor />);
  fireEvent.change(screen.getByLabelText("物品名称"), {
    target: { value: "干粮" },
  });
  fireEvent.change(screen.getByLabelText("物品数量"), {
    target: { value: "-1" },
  });
  fireEvent.click(screen.getByText("加入起始背包"));
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "移除起始物品干粮" })).toBeNull();
});
it("legacy characters without inventory render an empty backpack", () => {
  render(
    <CharacterCard
      title="当前角色"
      state={{
        name: "旧角色",
        stamina: 80,
        spirit: 90,
        coins: 5,
        elapsed_minutes: 10,
        location: "旧街",
      }}
    />,
  );
  expect(screen.getByText("暂无物品")).toBeTruthy();
});

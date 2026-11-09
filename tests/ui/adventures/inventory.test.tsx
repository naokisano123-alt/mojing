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

it("edits the description of a carried item without editing its quantity", async () => {
  function Backpack() {
    const [description, setDescription] = useState("旧介绍");
    return <CharacterCard title="当前角色" descriptions={{ 罗盘: description }}
      state={{ name: "旅人", stamina: 100, spirit: 100, coins: 0, elapsed_minutes: 0,
        location: "起点", inventory: [{ name: "罗盘", quantity: 1 }] }}
      onSaveDescription={async (_name, value) => { setDescription(value); }} />;
  }
  render(<Backpack />);
  fireEvent.click(screen.getByText("罗盘 × 1"));
  fireEvent.click(screen.getByRole("button", { name: "编辑介绍" }));
  const editor = screen.getByRole("textbox", { name: "罗盘的介绍" });
  expect(editor).toHaveValue("旧介绍");
  fireEvent.change(editor, { target: { value: "只在霜河边转动。" } });
  fireEvent.click(screen.getByRole("button", { name: "保存介绍" }));
  expect(await screen.findByText("只在霜河边转动。", { selector: "p" })).toBeVisible();
  expect(screen.getByText("罗盘 × 1")).toBeVisible();
});

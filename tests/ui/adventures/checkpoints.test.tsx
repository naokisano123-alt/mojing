import React from "react";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { CheckpointPanel } from "../../../src/features/adventures/CheckpointPanel";
import type { Run } from "../../../src/features/adventures/api";
const run: Run = {
  id: "original",
  world_id: "world",
  name: "原路线",
  mode: "fake",
  revision: 1,
};
const checkpoint = {
  id: "saved",
  adventure_id: "original",
  name: "灯塔前",
  revision: 1,
  mode: "fake" as const,
  created_at: 1,
};
const branch: Run = {
  ...run,
  id: "branch",
  name: "新路线",
  origin_checkpoint_id: "saved",
};

it("save retry keeps the request key rather than creating another checkpoint", async () => {
  const api = {
    list: vi.fn(async () => ({ items: [] })),
    save: vi
      .fn()
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValue(checkpoint),
    restore: vi.fn(async () => branch),
  };
  render(
    <CheckpointPanel run={run} api={api} onRestored={vi.fn(async () => {})} />,
  );
  await screen.findByText(/还没有检查点/);
  fireEvent.click(screen.getByText("保存当前检查点（第1回合）"));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByText("保存当前检查点（第1回合）"));
  await screen.findByText("灯塔前");
  expect(api.save).toHaveBeenCalledTimes(2);
  expect(api.save.mock.calls[0][1]).toEqual(api.save.mock.calls[1][1]);
});

it("restore retry is idempotent and only switches after the server succeeds", async () => {
  const onRestored = vi.fn(async () => {});
  const api = {
    list: vi.fn(async () => ({ items: [checkpoint] })),
    save: vi.fn(),
    restore: vi
      .fn()
      .mockRejectedValueOnce(new Error("响应丢失"))
      .mockResolvedValue(branch),
  };
  render(<CheckpointPanel run={run} api={api} onRestored={onRestored} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "从灯塔前创建分支" }),
  );
  await screen.findByRole("alert");
  expect(onRestored).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "从灯塔前创建分支" }));
  await waitFor(() => expect(onRestored).toHaveBeenCalledWith(branch));
  expect(api.restore.mock.calls[0]).toEqual(api.restore.mock.calls[1]);
});

it("an old restore result cannot switch a newly selected journey", async () => {
  let resolve!: (run: Run) => void;
  const onRestored = vi.fn(async () => {});
  const api = {
    list: vi.fn(async (id: string) => ({
      items: id === "original" ? [checkpoint] : [],
    })),
    save: vi.fn(),
    restore: vi.fn(
      () =>
        new Promise<Run>((r) => {
          resolve = r;
        }),
    ),
  };
  const view = render(
    <CheckpointPanel run={run} api={api} onRestored={onRestored} />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "从灯塔前创建分支" }),
  );
  view.rerender(
    <CheckpointPanel
      run={{ ...run, id: "different" }}
      api={api}
      onRestored={onRestored}
    />,
  );
  await act(async () => resolve(branch));
  expect(onRestored).not.toHaveBeenCalled();
});

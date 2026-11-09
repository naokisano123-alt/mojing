import React from "react";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { AdventureJournal } from "../../../src/features/adventures/AdventureJournal";
import { ApiClientError } from "../../../src/api/client";
import type { Detail, Draft } from "../../../src/features/adventures/api";

function setup(withState = false) {
  let detail: Detail = {
    id: "run",
    world_id: "w",
    name: "灯塔之旅",
    mode: "fake",
    revision: 0,
    turns: [],
    latest_draft: null,
  };
  const ready: Draft = {
    id: "draft",
    action: "敲门",
    status: "ready",
    result: {
      request_id: "d",
      mode: "fake",
      narrative: "守望者打开门。",
      references: [],
      sources: [],
    },
  };
  if (withState) {
    const state = {
      name: "旅人",
      stamina: 100,
      spirit: 100,
      coins: 5,
      elapsed_minutes: 0,
      location: "旧街",
    };
    detail.character = state;
    ready.result!.state_before = state;
    ready.result!.proposed_changes = { coins_delta: -3, minutes: 10 };
    ready.result!.projected_state = { ...state, coins: 2, elapsed_minutes: 10 };
  }
  const api = {
    list: vi.fn(async () => ({ items: [detail] })),
    create: vi.fn(async () => detail),
    detail: vi.fn(async () => detail),
    status: vi.fn(async () => ready),
    prepare: vi.fn(async () => {
      detail = { ...detail, latest_draft: ready };
      return ready;
    }),
    discard: vi.fn(async () => {
      detail = { ...detail, latest_draft: null };
    }),
    commit: vi.fn(async () => {
      detail = {
        ...detail,
        revision: 1,
        latest_draft: null,
        turns: [{ sequence: 1, action: "敲门", result: ready.result! }],
      };
      return detail;
    }),
  };
  return { api, ready };
}
const props = {
  worldId: "w",
  onConfigure: vi.fn(),
  onOpenSource: vi.fn(),
  checkpointApi: {
    list: vi.fn(async () => ({ items: [] })),
    save: vi.fn(),
    restore: vi.fn(),
  },
};

it("generation is a draft until the player explicitly confirms it", async () => {
  const { api } = setup();
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByText("旅程尚未落笔。写下第一个行动。");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "敲门" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  await screen.findByText("待确认草案 · 尚未写入回合");
  expect(api.commit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("确认并写入回合"));
  await screen.findByText("第 1 回合");
  expect(api.commit).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("你的行动")).not.toBeInTheDocument();
});

it("stopping preserves action and suppresses late draft responses", async () => {
  const { api, ready } = setup();
  let resolve!: (draft: Draft) => void;
  api.prepare = vi.fn(
    () =>
      new Promise<Draft>((r) => {
        resolve = r;
      }),
  );
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByText("旅程尚未落笔。写下第一个行动。");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "敲门" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  fireEvent.click(await screen.findByRole("button", { name: "停止生成" }));
  await act(async () => resolve(ready));
  expect(screen.queryByText("待确认草案 · 尚未写入回合")).toBeNull();
  expect(screen.getByLabelText("你的行动")).toHaveValue("敲门");
  expect(api.commit).not.toHaveBeenCalled();
});

it("failed commit keeps the draft so retry uses the same idempotent endpoint", async () => {
  const { api } = setup();
  api.commit.mockRejectedValueOnce(new Error("网络中断"));
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByText("旅程尚未落笔。写下第一个行动。");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "敲门" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  fireEvent.click(await screen.findByText("确认并写入回合"));
  await screen.findByRole("alert");
  expect(screen.getByText("待确认草案 · 尚未写入回合")).toBeTruthy();
  fireEvent.click(screen.getByText("确认并写入回合"));
  await screen.findByText("第 1 回合");
  expect(api.commit.mock.calls).toEqual([
    ["run", "draft"],
    ["run", "draft"],
  ]);
});

it("state proposal defaults to not adopted, and adoption is explicit", async () => {
  const { api } = setup(true);
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByLabelText("当前角色状态");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "买茶" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  await screen.findByLabelText("模型状态提议 · 尚未生效");
  const checkbox = screen.getByRole("checkbox", { name: /采用这份状态提议/ });
  expect(checkbox).not.toBeChecked();
  expect(api.commit).not.toHaveBeenCalled();
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByText("确认并写入回合"));
  await waitFor(() =>
    expect(api.commit).toHaveBeenCalledWith("run", "draft", {
      apply_changes: true,
    }),
  );
});

it("a state proposal can be saved as narrative without changing character state", async () => {
  const { api } = setup(true);
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByLabelText("当前角色状态");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "买茶" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  await screen.findByLabelText("模型状态提议 · 尚未生效");
  fireEvent.click(screen.getByText("确认并写入回合"));
  await waitFor(() => expect(api.commit).toHaveBeenCalledWith("run", "draft"));
});

it("item changes appear in the same optional state confirmation", async () => {
  const { api, ready } = setup(true);
  ready.result!.proposed_changes!.inventory_changes = [
    { name: "干粮", delta: 1 },
  ];
  ready.result!.projected_state!.inventory = [{ name: "干粮", quantity: 1 }];
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByLabelText("当前角色状态");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), {
    target: { value: "买干粮" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  expect(await screen.findByLabelText("物品增减提议")).toHaveTextContent(
    "干粮：+1",
  );
  expect(
    screen.getByRole("checkbox", { name: /采用这份状态提议/ }),
  ).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: /采用这份状态提议/ }));
  fireEvent.click(screen.getByText("确认并写入回合"));
  await waitFor(() =>
    expect(api.commit).toHaveBeenCalledWith("run", "draft", {
      apply_changes: true,
    }),
  );
});

it("an expired model session can be replaced without losing the typed action", async () => {
  const { api, ready } = setup();
  const run = { ...(await api.detail()), mode: "live" as const };
  api.list.mockResolvedValue({ items: [run] });
  api.detail.mockResolvedValue(run);
  api.prepare.mockRejectedValueOnce(new ApiClientError("MODEL_SESSION_EXPIRED", "expired", 409));
  api.prepare.mockResolvedValueOnce(ready);
  const expired = vi.fn();
  const journal = (sessionId?: string) => <AdventureJournal {...props} api={api}
    sessionId={sessionId} onSessionExpired={expired} />;
  const { rerender } = render(journal("old-session"));
  await screen.findByText("旅程尚未落笔。写下第一个行动。");
  fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
  fireEvent.change(screen.getByLabelText("你的行动"), { target: { value: "敲门" } });
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("行动已保留");
  expect(expired).toHaveBeenCalledOnce();
  rerender(journal());
  expect(screen.getByRole("button", { name: "发送行动" })).toBeDisabled();
  rerender(journal("new-session"));
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  await screen.findByText("待确认草案 · 尚未写入回合");
  expect(api.prepare).toHaveBeenLastCalledWith("run",
    expect.objectContaining({ action: "敲门", session_id: "new-session" }), expect.any(AbortSignal));
});

it("confirmed suggestions append at the caret and a focused editor sends in one click", async () => {
  const { api, ready } = setup();
  const detail = await api.detail();
  detail.turns = [{
    sequence: 1, action: "进门",
    result: { ...ready.result!, suggestions: ["问候店主", "去窗边看看"],
      references: ["entry:market"],
      sources: [{ source_id: "entry:market", kind: "entry", title: "街市" }] },
  }];
  render(<AdventureJournal {...props} api={api} />);
  const choice = await screen.findByRole("button", { name: "问候店主" });
  expect(screen.getByText("来源：1 条引用").closest("details")).not.toHaveAttribute("open");
  fireEvent.click(choice);
  const input = screen.getByLabelText<HTMLTextAreaElement>("你的行动");
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe("问候店主".length);
  fireEvent.click(screen.getByRole("button", { name: "去窗边看看" }));
  expect(input.value).toBe("问候店主\n去窗边看看");
  expect(input.selectionStart).toBe(input.value.length);
  const send = screen.getByRole("button", { name: "发送行动" });
  fireEvent.pointerDown(send);
  expect(input).toHaveFocus();
  fireEvent.click(send);
  await screen.findByText("待确认草案 · 尚未写入回合");
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(api.prepare.mock.calls[0]).toContainEqual(expect.objectContaining({ action: "问候店主\n去窗边看看" }));
});

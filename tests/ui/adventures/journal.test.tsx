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
  await screen.findByText(/第 1 回合/);
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
  await screen.findByText(/第 1 回合/);
  expect(api.commit.mock.calls).toEqual([
    ["run", "draft"],
    ["run", "draft"],
  ]);
});

it("state proposal defaults to not adopted, and adoption is explicit", async () => {
  const { api } = setup(true);
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByRole("button", { name: "角色", exact: true });
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
  await screen.findByRole("button", { name: "角色", exact: true });
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
  await screen.findByRole("button", { name: "角色", exact: true });
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
  expect(screen.queryByText("世界脉动设置")).not.toBeInTheDocument();
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

it("shows a configured opening and commits an interrupted advance with its clock", async () => {
  const { api, ready } = setup(true);
  const original = await api.detail();
  const opening = {
    name: "细器铺试工", intro: "黎斯站在南窗细器铺里。", starts_at: "1200-01-01T08:00",
    character: original.character!, shortcut: "接下来三天照常做工。",
  };
  api.detail.mockResolvedValueOnce({ ...original, opening });
  ready.result!.clock_required = true;
  ready.result!.clock = {
    requested_minutes: 4320, elapsed_minutes: 1680,
    from: "1200-01-01T08:00", to: "1200-01-02T12:00", interrupted: true,
    events: [{ title: "急单", description: "塞文问你接不接急件。", at: "1200-01-02T12:00", count: 1, interrupt: true }],
  };
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByText("黎斯站在南窗细器铺里。");
  expect(screen.queryByText("急单")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: opening.shortcut }));
  expect(screen.getByLabelText("你的行动")).toHaveValue(opening.shortcut);
  fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
  await screen.findByText("塞文问你接不接急件。");
  expect(api.commit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("确认并写入回合"));
  await waitFor(() => expect(api.commit).toHaveBeenCalledWith("run", "draft", { apply_changes: true }));
});

it("refreshing a journey keeps a single current character and memory panel", async () => {
  const { api } = setup(true);
  let revision = 0;
  api.detail.mockImplementation(async () => ({
    id: "run", world_id: "w", name: "灯塔之旅", mode: "fake", revision,
    character: { name: "旅人", coins: revision, stamina: 100, spirit: 100,
      location: "旧街", elapsed_minutes: revision * 15 },
    memory: { cursor: revision, items: [], events: [] },
    turns: Array.from({ length: revision }, (_, i) => ({
      sequence: i + 1, action: "在街上走走",
      result: {
        request_id: String(i), mode: "fake", narrative: "灯光亮着。",
        references: [], sources: [],
        state_after: { name: "旅人", coins: i, stamina: 100, spirit: 100,
          location: "旧街", elapsed_minutes: i * 15 },
      },
    })),
    latest_draft: null,
  }));
  const messages = vi.spyOn(console, "error").mockImplementation(() => {});
  render(<AdventureJournal {...props} api={api} />);
  await screen.findByText("世界日志与更正（0）");
  for (revision = 1; revision <= 4; revision++) {
    // A confirmed server refresh replaces the journal detail, retaining one current panel.
    api.prepare.mockResolvedValueOnce({ id: "d" + revision, status: "ready", action: "在街上走走", result: {
      request_id: "d" + revision, mode: "fake", narrative: "新的灯光。", references: [], sources: [],
    } });
    fireEvent.click(screen.getByRole("button", { name: "自由编辑行动" }));
    fireEvent.change(screen.getByLabelText("你的行动"), { target: { value: "在街上走走" } });
    fireEvent.click(screen.getByRole("button", { name: "发送行动" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认并写入回合" }));
    await screen.findByText(new RegExp("第 " + revision + " 回合"));
    expect(screen.queryByLabelText("角色信息")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "角色", exact: true }));
    expect(screen.getAllByLabelText("角色信息")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭角色" }));
    expect(screen.getAllByText("世界日志与更正（0）")).toHaveLength(1);
  }
  expect(messages.mock.calls.some(args => args.some(arg => String(arg).includes("same key")))).toBe(false);
});

it("adventure keeps management out of the story, with qualitative character details in an accessible overlay", async () => {
  const { api, ready } = setup(true);
  const detail = await api.detail();
  detail.character = { ...detail.character!, condition: "右臂仍有旧伤，精神尚好。" };
  detail.memory = { cursor: 0, items: [], events: [] };
  detail.turns = [{ sequence: 1, action: "走进小镇", result: { ...ready.result!, narrative: "街灯在雨中摇曳。" } }];
  render(<AdventureJournal {...props} api={api} />);
  const story = await screen.findByText("街灯在雨中摇曳。");
  expect(screen.queryByLabelText("选择冒险记录")).toBeNull();
  expect(screen.queryByRole("region", { name: "存档与分支" })).toBeNull();
  expect(screen.queryByLabelText("角色信息")).toBeNull();
  const log = screen.getByText("世界日志与更正（0）");
  expect(story.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const trigger = screen.getByRole("button", { name: "角色", exact: true });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "角色", exact: true });
  expect(dialog).toHaveTextContent("右臂仍有旧伤");
  expect(dialog).toHaveTextContent("铜币");
  expect(dialog).not.toHaveTextContent("100/100");
  expect(dialog).not.toHaveTextContent("体力");
  expect(document.body.style.overflow).toBe("hidden");
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.body.style.overflow).not.toBe("hidden");
  expect(trigger).toHaveFocus();
});

it("remembered journey resumes per world and ignores a mismatched saved journey", async () => {
  const { api } = setup();
  const first = await api.detail();
  const other = { ...first, id: "saved", name: "旧分支" };
  localStorage.setItem("mojing:active-adventure:w", "saved");
  api.detail.mockImplementation(async (id?: string) => id === "saved" ? other : first);
  const onActiveRunChange = vi.fn();
  const { rerender } = render(<AdventureJournal {...props} api={api} onActiveRunChange={onActiveRunChange} />);
  await waitFor(() => expect(onActiveRunChange).toHaveBeenLastCalledWith(other));
  localStorage.setItem("mojing:active-adventure:w2", "saved");
  const second = { ...first, id: "second", world_id: "w2" };
  api.list.mockResolvedValue({ items: [second] });
  api.detail.mockImplementation(async (id?: string) => id === "saved" ? other : second);
  rerender(<AdventureJournal {...props} worldId="w2" api={api} onActiveRunChange={onActiveRunChange} />);
  await waitFor(() => expect(onActiveRunChange).toHaveBeenLastCalledWith(second));
  expect(localStorage.getItem("mojing:active-adventure:w")).toBe("saved");
  expect(localStorage.getItem("mojing:active-adventure:w2")).toBe("second");
});

it("消息只展示一份列表，查证建议可带回同一行动输入框", async () => {
  const { createInnkeeperCaravanEncounterScenario } = await import("../../../src/features/world-encounters");
  const { transport } = createInnkeeperCaravanEncounterScenario("run");
  const bundle = await transport.getEntrypointBundle("run");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).split("?")[0];
    const body = path.endsWith("/world-news") ? { items: bundle.news_items, next_before: null }
      : path.endsWith("/world-encounters") ? bundle : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetcher);
  const { api } = setup(true);
  render(<AdventureJournal {...props} api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "消息", exact: true }));
  await screen.findByText("市井传闻：灰鬃商队迟迟未至");
  expect(screen.getAllByText("市井传闻：灰鬃商队迟迟未至")).toHaveLength(1);
  expect(fetcher.mock.calls.some(args => String(args[0]).includes("/world-encounters"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "查证：市井传闻：灰鬃商队迟迟未至" }));
  fireEvent.click(await screen.findByTestId("encounter-option-opt_fact_player_initial_rumor_ask_npc_marta_innkeeper"));
  expect((screen.getByTestId("encounter-action-editor") as HTMLTextAreaElement).value).toContain("向玛塔老板娘");
  expect(screen.getByRole("dialog", { name: "世界消息" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "回到冒险编辑行动" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect((screen.getByLabelText("你的行动") as HTMLTextAreaElement).value).toContain("向玛塔老板娘");
  expect(screen.getByLabelText("你的行动")).toHaveFocus();
  expect(api.prepare).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

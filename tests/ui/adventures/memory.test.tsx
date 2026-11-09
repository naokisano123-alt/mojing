import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { MemoryPanel } from "../../../src/features/adventures/MemoryPanel";

test("opens the collapsed log and saves an auditable correction", async () => {
  const user = userEvent.setup();
  const onCorrect = vi.fn().mockResolvedValue(undefined);
  render(<MemoryPanel disabled={false} onCorrect={onCorrect} memory={{
    cursor: 3, events: [], items: [{
      id: "secret", kind: "secret", title: "黎斯会塑形", detail: "藏起来", status: "active", knowers: ["黎斯"], turn: 2,
    }],
  }} />);
  const summary = screen.getByText("世界日志与更正（1）");
  expect(summary.parentElement).not.toHaveAttribute("open");
  await user.click(summary);
  await user.click(screen.getByRole("button", { name: "更正", exact: true }));
  const people = screen.getByRole("textbox", { name: "知情人（用顿号分开）" });
  await user.clear(people);
  await user.type(people, "黎斯、同伴");
  await user.type(screen.getByRole("textbox", { name: "更正说明" }), "同伴也在现场");
  await user.click(screen.getByRole("button", { name: "保存更正" }));
  expect(onCorrect).toHaveBeenCalledWith(expect.objectContaining({
    expected_cursor: 3, id: "secret", reason: "同伴也在现场",
    item: expect.objectContaining({ knowers: ["黎斯", "同伴"] }),
  }));
});

import { request } from "../../api/client";
import type { NarrationPreviewResponse } from "../narration-preview/types";
export interface InventoryItem {
  name: string;
  quantity: number;
}
export interface ItemDelta {
  name: string;
  delta: number;
}
export interface CharacterState {
  inventory?: InventoryItem[];
  name: string;
  stamina: number;
  spirit: number;
  coins: number;
  elapsed_minutes: number;
  location: string;
}
export interface JournalResult extends NarrationPreviewResponse {
  state_before?: CharacterState | null;
  state_after?: CharacterState | null;
  projected_state?: CharacterState | null;
  proposed_changes?: Record<string, number | string | ItemDelta[]> | null;
  changes_accepted?: boolean;
}
export interface Run {
  origin_checkpoint_id?: string | null;
  character?: CharacterState | null;
  id: string;
  world_id: string;
  name: string;
  mode: "fake" | "live";
  revision: number;
}
export interface Turn {
  sequence: number;
  action: string;
  result: JournalResult;
}
export interface Draft {
  id: string;
  action: string;
  status: "pending" | "ready" | "committed" | "failed" | "discarded";
  result: JournalResult | null;
}
export interface Detail extends Run {
  turns: Turn[];
  latest_draft: Draft | null;
}
const path = (id: string) => "/adventures/" + encodeURIComponent(id);
export const adventureApi = {
  list: (world: string, signal?: AbortSignal, offset = 0) =>
    request<{ items: Run[] }>(
      "GET",
      "/worlds/" + encodeURIComponent(world) + "/adventures",
      { signal, queryParams: { offset } },
    ),
  create: (
    world: string,
    name: string,
    mode: "fake" | "live",
    character?: Partial<CharacterState>,
  ) =>
    request<Run>(
      "POST",
      "/worlds/" + encodeURIComponent(world) + "/adventures",
      { body: { name, mode, character } },
    ),
  detail: (id: string, signal?: AbortSignal, before?: number) =>
    request<Detail>("GET", path(id), { signal, queryParams: { before } }),
  prepare: (
    id: string,
    body: {
      request_id: string;
      action: string;
      expected_revision: number;
      session_id?: string;
    },
    signal?: AbortSignal,
  ) =>
    request<Draft>("POST", path(id) + "/drafts", {
      body,
      signal,
      timeoutMs: 75000,
    }),
  status: (id: string, draft: string) =>
    request<Draft>("GET", path(id) + "/drafts/" + encodeURIComponent(draft)),
  discard: (id: string, draft: string) =>
    request<void>("DELETE", path(id) + "/drafts/" + encodeURIComponent(draft)),
  commit: (id: string, draft: string, choice?: { apply_changes: boolean }) =>
    request<Run>(
      "POST",
      path(id) + "/drafts/" + encodeURIComponent(draft) + "/commit",
      { body: choice },
    ),
};

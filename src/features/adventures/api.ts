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
export type MemoryKind = "deposit" | "commitment" | "secret";
export type MemoryStatus = "active" | "refund" | "forfeit" | "done";
export interface MemoryFields {
  kind: MemoryKind;
  title: string;
  detail: string;
  status: MemoryStatus;
  person?: string;
  amount?: number;
  knowers?: string[];
}
export interface MemoryChange {
  id: string;
  item: (MemoryFields & { id: string }) | null;
  reason: string;
}
export interface MemoryView {
  cursor: number;
  items: (MemoryFields & { id: string; turn: number })[];
  events: (MemoryChange & { sequence: number; turn: number; origin: "turn" | "correction" })[];
}
export interface MemoryCorrection {
  expected_cursor: number;
  id?: string;
  item: MemoryFields | null;
  reason: string;
}
export interface Opening {
  name: string;
  intro: string;
  starts_at: string;
  character: CharacterState;
  shortcut: string;
}
export interface ScenarioEvent {
  title: string;
  description: string;
  at_minute: number;
  every_minutes: number | null;
  interrupt: boolean;
}
export interface OpeningSettings extends Opening {
  events: ScenarioEvent[];
}
export interface ClockResult {
  requested_minutes: number;
  elapsed_minutes: number;
  from: string;
  to: string;
  interrupted: boolean;
  events: { title: string; description: string; at: string; count: number; interrupt: boolean }[];
}
const openingsPath = (id: string) => "/worlds/" + encodeURIComponent(id) + "/opening-settings";
export const openingApi = {
  settings: (world: string, signal?: AbortSignal) =>
    request<{ items: (OpeningSettings & { id: string })[] }>("GET", openingsPath(world), { signal }),
  save: (world: string, body: OpeningSettings, id?: string) =>
    request<OpeningSettings & { id: string }>(id ? "PUT" : "POST",
      openingsPath(world) + (id ? "/" + encodeURIComponent(id) : ""), { body }),
  remove: (world: string, id: string) =>
    request<void>("DELETE", openingsPath(world) + "/" + encodeURIComponent(id)),
};
export interface JournalResult extends NarrationPreviewResponse {
  suggestions?: string[];
  state_before?: CharacterState | null;
  state_after?: CharacterState | null;
  projected_state?: CharacterState | null;
  proposed_changes?: Record<string, number | string | ItemDelta[]> | null;
  changes_accepted?: boolean;
  memory_cursor?: number;
  memory_proposal?: MemoryChange[];
  memory_changes?: MemoryChange[];
  clock?: ClockResult;
  clock_required?: boolean;
}
export interface Run {
  origin_checkpoint_id?: string | null;
  opening?: Opening;
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
  item_descriptions?: Record<string, string>;
  memory?: MemoryView;
}
const path = (id: string) => "/adventures/" + encodeURIComponent(id);
export const adventureApi = {
  listOpenings: (world: string, signal?: AbortSignal) => request<{ items: (Opening & { id: string })[] }>(
    "GET", "/worlds/" + encodeURIComponent(world) + "/openings", { signal },
  ),
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
    openingId?: string,
  ) =>
    request<Run>(
      "POST",
      "/worlds/" + encodeURIComponent(world) + "/adventures",
      { body: { name, mode, character, opening_id: openingId } },
    ),
  detail: (id: string, signal?: AbortSignal, before?: number) =>
    request<Detail>("GET", path(id), { signal, queryParams: { before } }),
  correctMemory: (id: string, body: MemoryCorrection) =>
    request<MemoryView>("PATCH", path(id) + "/memory", { body }),
  saveItemDescription: (id: string, name: string, description: string) =>
    request<{ name: string; description: string }>("PATCH", path(id) + "/item-description", {
      body: { name, description },
    }),
  prepare: (
    id: string,
    body: {
      request_id: string;
      action: string;
      expected_revision: number;
      session_id?: string;
      advance_minutes?: number;
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

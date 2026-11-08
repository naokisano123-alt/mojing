import { request } from "../../api/client";
import type { NarrationPreviewResponse } from "../narration-preview/types";
export interface Run {
  id: string;
  world_id: string;
  name: string;
  mode: "fake" | "live";
  revision: number;
}
export interface Turn {
  sequence: number;
  action: string;
  result: NarrationPreviewResponse;
}
export interface Draft {
  id: string;
  action: string;
  status: "pending" | "ready" | "committed" | "failed" | "discarded";
  result: NarrationPreviewResponse | null;
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
  create: (world: string, name: string, mode: "fake" | "live") =>
    request<Run>(
      "POST",
      "/worlds/" + encodeURIComponent(world) + "/adventures",
      { body: { name, mode } },
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
  commit: (id: string, draft: string) =>
    request<Run>(
      "POST",
      path(id) + "/drafts/" + encodeURIComponent(draft) + "/commit",
    ),
};

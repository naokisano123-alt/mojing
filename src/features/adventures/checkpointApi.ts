import { request } from "../../api/client";
import type { Run } from "./api";
export interface Checkpoint {
  id: string;
  adventure_id: string;
  name: string;
  revision: number;
  mode: "fake" | "live";
  created_at: number;
}
export const checkpointApi = {
  list: (run: string, signal?: AbortSignal, offset = 0) =>
    request<{ items: Checkpoint[] }>(
      "GET",
      `/adventures/${encodeURIComponent(run)}/checkpoints`,
      { signal, queryParams: { offset } },
    ),
  save: (
    run: string,
    body: { name: string; request_id: string; expected_revision: number },
  ) =>
    request<Checkpoint>(
      "POST",
      `/adventures/${encodeURIComponent(run)}/checkpoints`,
      { body },
    ),
  restore: (id: string, body: { name: string; request_id: string }) =>
    request<Run>("POST", `/checkpoints/${encodeURIComponent(id)}/restore`, {
      body,
    }),
};

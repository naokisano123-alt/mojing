import { request } from '../../api/client';
import type { Run } from './api';
import type { Checkpoint } from './checkpointApi';
export interface Journey { id: string; name: string; active_adventure_id: string; revision: number; mode: 'fake' | 'live' }
export interface SaveSlot extends Checkpoint { kind: 'auto' | 'manual' }
export const requestId = () => crypto.randomUUID?.() ?? `${Date.now()}_${Math.random().toString(36).slice(2)}`;
export const journeyApi = {
  list: (world: string, signal?: AbortSignal) => request<{ items: Journey[] }>('GET', `/worlds/${encodeURIComponent(world)}/journeys`, { signal }),
  saves: (journey: string, signal?: AbortSignal) => request<{ items: SaveSlot[] }>('GET', `/journeys/${encodeURIComponent(journey)}/saves`, { signal }),
  save: (journey: string, run: Run, name: string, id: string) => request<SaveSlot>('POST', `/journeys/${encodeURIComponent(journey)}/saves`, { body: { adventure_id: run.id, expected_revision: run.revision, name, request_id: id } }),
  remove: (journey: string, save: string) => request<void>('DELETE', `/journeys/${encodeURIComponent(journey)}/saves/${encodeURIComponent(save)}`),
  rename: (journey: string, name: string) => request('PATCH', `/journeys/${encodeURIComponent(journey)}`, { body: { name } }),
  load: (journey: string, save: string, id: string, asNew = false, name?: string) => request<Run>('POST', `/journeys/${encodeURIComponent(journey)}/load/${encodeURIComponent(save)}`, { body: { request_id: id, as_new_journey: asNew, name } }),
};

// ck3_chronicler-yrv3: shared singleton EventSource transport for the
// /api/sse/logs channel, scoped to a single non-campaign-keyed channel
// (logs are process-wide, not per-campaign). Returns a rolling buffer
// capped at the same MAX_BUFFERED entries the backend deque uses, so
// the FE can't drift unboundedly when the user leaves the tab open
// all day.
//
// M-F12 (27ov.65): the transport (ref-counting, dead-after-3-errors,
// jjqf cold-start grace, dead-channel reconnect probing) lives in
// sseChannelStore.ts, shared with useEventStream. This module owns
// only the log frame reducer + the seed-from-backfill merge. The
// shared transport also gives the logs channel the 27ov.30 reconnect
// probing it never had — a dead LogsPage now heals after a backend
// restart instead of requiring a manual reload.

import { useSyncExternalStore } from 'react';

import { createSseChannelStore } from './sseChannelStore';
import type { LogEnvelope } from './types';

const MAX_BUFFERED = 2_000;

export interface LogStreamState {
  envelopes: LogEnvelope[];
  // Highest seq we've seen. Lets the page dedupe between the cold-load
  // (/api/logs/recent) result and live-stream frames.
  lastSeenSeq: number;
  connected: boolean;
  dead: boolean;
}

const EMPTY_STATE: LogStreamState = {
  envelopes: [],
  lastSeenSeq: 0,
  connected: false,
  dead: false,
};

function reduceLogFrame(state: LogStreamState, frame: unknown): LogStreamState {
  const env = frame as LogEnvelope;
  // Dedup by seq — a frame whose seq we've already seen is a
  // server-side replay / poll-window overlap, drop it.
  if (env.seq <= state.lastSeenSeq) return state;
  const next = [...state.envelopes, env];
  const trimmed =
    next.length > MAX_BUFFERED ? next.slice(next.length - MAX_BUFFERED) : next;
  return { ...state, envelopes: trimmed, lastSeenSeq: env.seq };
}

// Single process-wide channel — the key is a constant.
const LOGS_KEY = 'logs';

const store = createSseChannelStore<LogStreamState, undefined>({
  url: () => '/api/sse/logs',
  initialState: () => ({ ...EMPTY_STATE }),
  defaultContext: undefined,
  reduce: reduceLogFrame,
});

// ck3_chronicler-yrv3: seed the buffer from a cold-load fetch. Called
// by LogsPage's initial-load effect so the user sees backfilled
// envelopes without a render flash of "empty buffer → streaming".
//
// Dedupes against envelopes already present in the buffer by seq —
// backfill may overlap with live frames that arrived between the
// cold-load request and seedLogStream() returning. Preserves the
// invariant that envelopes are sorted ascending by seq.
export function seedLogStream(envelopes: LogEnvelope[]): void {
  if (envelopes.length === 0) return;
  store.update(LOGS_KEY, (state) => {
    const seenSeqs = new Set(state.envelopes.map((e) => e.seq));
    const fresh = envelopes.filter((e) => !seenSeqs.has(e.seq));
    if (fresh.length === 0) return state;
    const merged = [...state.envelopes, ...fresh].sort((a, b) => a.seq - b.seq);
    const trimmed =
      merged.length > MAX_BUFFERED
        ? merged.slice(merged.length - MAX_BUFFERED)
        : merged;
    const maxSeq = Math.max(state.lastSeenSeq, ...fresh.map((e) => e.seq));
    return { ...state, envelopes: trimmed, lastSeenSeq: maxSeq };
  });
}

export function __resetLogChannelForTesting(): void {
  store.reset();
}

function subscribe(onStoreChange: () => void): () => void {
  return store.subscribe(LOGS_KEY, onStoreChange);
}

function getSnapshot(): LogStreamState {
  return store.getState(LOGS_KEY) ?? EMPTY_STATE;
}

export function useLogStream(): LogStreamState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

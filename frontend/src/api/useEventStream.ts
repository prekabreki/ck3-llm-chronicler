// useEventStream — minimal EventSource hook for the chronicler SSE
// channel. Subscribes to /api/sse/ingest/{campaignName} and returns a
// rolling buffer of the most recent N events plus the latest cache
// state.
//
// Singleton transport (audit F-08 / ck3_chronicler-62u8): under the
// hood, all callers for the same campaignName share one EventSource —
// previously each call site (App, NarrativeQueueStrip, ChroniclePage,
// CodexPage, QueuePage, IngestPage) opened its own socket, breaching
// the browser's ~6-per-origin cap on a heavy nav walk. The hook keeps
// the same return shape so call sites are unchanged.
//
// 4xx short-circuit (audit F-45 / ck3_chronicler-ilss): EventSource
// itself never exposes the response status to JS, so we approximate by
// counting consecutive errors with no successful onopen. After the
// threshold the connection is closed and ``dead`` flips true; the
// AppShell renders "Tail unavailable" instead of "Tail idle".
//
// Recoverable dead (audit M-F13 / ck3_chronicler-27ov.30): dead is no
// longer forever — a dead channel keeps probing with slow exponential
// backoff; a successful open resets everything. save_tail_stopped
// keeps its immediate badge flip but leaves the transport open — a
// healthy channel's next tail-evidencing frame clears dead without
// any reconnect.
//
// M-F12 (27ov.65): the transport itself (ref-counting, error/grace/
// dead/reconnect handling) lives in sseChannelStore.ts, shared with
// useLogStream. This module owns only the ingest frame REDUCER and
// the public state shape.

import { useCallback, useSyncExternalStore } from 'react';

import { createSseChannelStore } from './sseChannelStore';

// ck3_chronicler-k91o (audit L33): per-kind discriminated union for the
// ingest SSE frames. Each interface mirrors the BE envelope dict that
// publishes it (save/tick.py, save/ingest.py, api/app.py) — keep field
// names + literals in lockstep with those publishers (F-44 / F-58: the
// BE is the source of truth). The reducer switches on ``kind`` and each
// branch narrows without casts. F-59 dropped the old ``| string``
// forward-compat tail, so a new BE kind the FE doesn't model surfaces as
// a TS error here rather than silently passing through.

// A single ingested timeline event (save/tick.py ~253). The ONLY kind
// buffered in EventStreamState.events — every other kind is consumed
// into a dedicated state field by the reducer below.
export interface EventIngestedFrame {
  kind: 'event_ingested';
  event_id: number;
  event_type: string;
  event_date: string;
  character_id: number | null;
}

// Cache observability counters: save/ingest.py publishes
// {kind, **cache.snapshot()} and cache.py snapshot() returns exactly
// pending / bytes / gc_drops_lifetime.
export interface CacheStateFrame {
  kind: 'cache_state';
  pending: number;
  bytes: number;
  gc_drops_lifetime: number;
}

// End-of-tick summary for the IngestActivityStrip (save/tick.py ~136).
export interface SavePairCompletedFrame {
  kind: 'save_pair_completed';
  save_filename: string;
  in_game_date: string | null;
  completed_at: string;
  event_count: number;
  event_type_tally: Record<string, number>;
}

// ck3_chronicler-kgqa: a save was dropped because its playthrough doesn't
// match this campaign's pin (save/tick.py ~162). Drives the red 'wrong
// game' tail-status badge.
export interface SaveDroppedForeignFrame {
  kind: 'save_dropped_foreign';
  observed_playthrough_id: string;
  campaign_playthrough_id: string | null;
  save_filename: string;
  observed_at: string;
}

// ck3_chronicler-elll: save-tail recovery window opens (save/ingest.py
// ~765). Emitted once on startup when a persisted baseline is found.
// Forensic fields are nullable for legacy v1 baselines that predate them.
export interface ChroniclerRecoveringFrame {
  kind: 'chronicler_recovering';
  baseline_persisted_at: string | null;
  baseline_date: string | null;
  baseline_generation: number | null;
  pending_cache_count: number;
}

// ck3_chronicler-elll: recovery window closes once _drain_pending_cache +
// _catch_up_to_current finish (save/ingest.py ~751).
export interface ChroniclerRecoveredFrame {
  kind: 'chronicler_recovered';
  events_ingested: number;
}

// ck3_chronicler-tail-pip: emitted from run_save_ingest's finally block
// when the save-tail loop exits (halt-save-tail route, process shutdown,
// or natural drain; save/ingest.py ~1104). The reducer flips dead on
// receipt so the AppShell badge reads "Tail unavailable" immediately,
// rather than waiting for the SSE channel to error 3 times — the channel
// itself stays healthy while uvicorn does.
export interface SaveTailStoppedFrame {
  kind: 'save_tail_stopped';
  stopped_at: string;
}

// ck3_chronicler-n52f: a save belonging to ANOTHER active campaign landed
// on this campaign's tail and triggered auto-resume of that campaign's
// ingest (save/tick.py ~580). The AppShell renders a toast with a
// one-click [Switch] action into the resumed campaign's Codex.
export interface CampaignAutoResumedFrame {
  kind: 'campaign_auto_resumed';
  campaign_id: string;
  campaign_name: string;
  save_path_name: string;
  in_game_date: string | null;
}

// Narrative-queue observability (api/app.py ~60: kind=f"narrative_{status}").
// The FE only bumps a refetch counter off these; the queue endpoint is the
// source of truth for the items themselves. ``narrative_<status>`` mirrors
// NarrativeQueueItem's status Literal: queued / generating / completed /
// failed. Modelled as four single-literal members (not one interface with a
// union-typed ``kind``) so the reducer's discriminant narrowing collapses
// them to `never` once handled, leaving event_ingested as the sole buffer
// fallthrough.
interface NarrativeFrameBase {
  item_id: number;
  character_id: number;
  task_kind: string;
  duration_ms: number | null;
  error: string | null;
}
export interface NarrativeQueuedFrame extends NarrativeFrameBase {
  kind: 'narrative_queued';
}
export interface NarrativeGeneratingFrame extends NarrativeFrameBase {
  kind: 'narrative_generating';
}
export interface NarrativeCompletedFrame extends NarrativeFrameBase {
  kind: 'narrative_completed';
}
export interface NarrativeFailedFrame extends NarrativeFrameBase {
  kind: 'narrative_failed';
}

export type IngestEvent =
  | EventIngestedFrame
  | CacheStateFrame
  | SavePairCompletedFrame
  | SaveDroppedForeignFrame
  | ChroniclerRecoveringFrame
  | ChroniclerRecoveredFrame
  | SaveTailStoppedFrame
  | CampaignAutoResumedFrame
  | NarrativeQueuedFrame
  | NarrativeGeneratingFrame
  | NarrativeCompletedFrame
  | NarrativeFailedFrame;

// ck3_chronicler-n52f: latest auto-resume notification. Populated on
// every campaign_auto_resumed frame; cleared by the AppShell banner
// when the user clicks [Switch] or dismisses. ``seq`` increments on
// each new frame so repeat arrivals (drain storm: many foreign saves
// resolved to the same campaign) re-trigger the toast even if the
// other fields are identical.
export interface AutoResumeState {
  campaignId: string;
  campaignName: string;
  savePathName: string;
  inGameDate: string | null;
  seq: number;
}

// ck3_chronicler-elll: save-tail recovery state. Populated when a
// chronicler_recovering frame arrives on save-tail startup; cleared
// when chronicler_recovered arrives after drain + catch-up finish.
// The AppShell banner renders while this is non-null.
export interface RecoveryState {
  // ISO timestamp the persisted baseline was last written. Null for
  // legacy v1 baselines that predate the forensic fields.
  baselinePersistedAt: string | null;
  // CK3 in-game date of the persisted baseline (e.g. "867.4.12").
  baselineDate: string | null;
  // Monotonic per-campaign persist counter. Null for legacy v1.
  baselineGeneration: number | null;
  // Count of cached (un-ingested) saves to drain before catch-up.
  pendingCacheCount: number;
}

export interface EventStreamState {
  // Most-recent-first list of ingested events. ck3_chronicler-k91o: the
  // reducer only ever buffers event_ingested frames here (all other kinds
  // return early into a dedicated field), so the element type is narrow.
  events: EventIngestedFrame[];
  // Latest cache_state snapshot; null until one arrives.
  cacheState: CacheStateFrame | null;
  // ck3_chronicler-bges: latest end-of-tick frame for the
  // IngestActivityStrip. Null until a frame arrives.
  lastSavePairCompleted: SavePairCompletedFrame | null;
  // ck3_chronicler-eev: monotonically incrementing seq each time a
  // `narrative_*` frame arrives. The AppShell strip uses this as a
  // dependency to trigger a TanStack Query refetch — cheaper than
  // duplicating the queue state in this hook.
  narrativeSeq: number;
  // True from the moment the EventSource opens until it errors out.
  connected: boolean;
  // True once the channel dies — the AppShell surfaces this as "Tail
  // unavailable" so the user knows their save tail is dead, not just
  // temporarily disconnected.
  dead: boolean;
  // ck3_chronicler-elll: non-null while save-tail is in its
  // recovery / catch-up phase. App.tsx renders a banner under the
  // AppShell while this is set.
  recovering: RecoveryState | null;
  // ck3_chronicler-n52f: most recent campaign_auto_resumed frame, or
  // null if none has arrived since the user last dismissed.
  autoResumed: AutoResumeState | null;
  // ck3_chronicler-kgqa: most recent save_dropped_foreign frame, or null.
  // deriveTailStatus compares its observed_at against
  // lastSavePairCompleted.completed_at to decide green vs red.
  lastForeignDrop: SaveDroppedForeignFrame | null;
  // Issue #2: bumped on every cache_state frame. The reconnect resync
  // fetch reads it before requesting and applies its (older) result only
  // if it has not moved — so a REST response that lands after a fresher
  // SSE frame is discarded rather than overwriting it. Newest wins, not
  // last-arriving. Not rendered by anything.
  cacheStateSeq: number;
}

const DEFAULT_BUFFER_SIZE = 200;

// ck3_chronicler-27ov.30: frames only a live save-tail (or its startup
// sequence) can produce. Receiving one while dead=true proves the tail
// is back — e.g. re-adopting a save spawned a fresh run_save_ingest
// while the SSE transport itself stayed healthy after
// save_tail_stopped. narrative_* frames are deliberately excluded: the
// queue can complete a generation while the tail is stopped.
const TAIL_ALIVE_KINDS = new Set([
  'event_ingested',
  'cache_state',
  'save_pair_completed',
  'chronicler_recovering',
  'chronicler_recovered',
]);

const EMPTY_STATE: EventStreamState = {
  events: [],
  cacheState: null,
  lastSavePairCompleted: null,
  narrativeSeq: 0,
  connected: false,
  dead: false,
  recovering: null,
  autoResumed: null,
  lastForeignDrop: null,
  cacheStateSeq: 0,
};

function reduceIngestFrame(
  state: EventStreamState,
  frame: unknown,
  context: { bufferSize: number },
): EventStreamState {
  // ck3_chronicler-k91o: ``frame`` is unvalidated wire JSON; assert it to
  // the discriminated union once and let each branch below narrow on the
  // ``kind`` discriminant — no per-field cast-by-faith.
  const parsed = frame as IngestEvent;
  // ck3_chronicler-27ov.30: a tail-evidencing frame while dead means
  // the tail is back on a transport that stayed healthy (the
  // save_tail_stopped path below no longer closes it). Revive —
  // connected too, since a delivered frame proves the socket is up.
  let next = state;
  if (next.dead && TAIL_ALIVE_KINDS.has(parsed.kind)) {
    next = { ...next, dead: false, connected: true };
  }
  if (parsed.kind === 'cache_state') {
    // Issue #2: the seq bump is what lets a slower REST resync know it
    // has been overtaken.
    return { ...next, cacheState: parsed, cacheStateSeq: next.cacheStateSeq + 1 };
  }
  if (parsed.kind === 'save_pair_completed') {
    return { ...next, lastSavePairCompleted: parsed };
  }
  if (parsed.kind === 'save_dropped_foreign') {
    return { ...next, lastForeignDrop: parsed };
  }
  if (parsed.kind === 'chronicler_recovering') {
    // ck3_chronicler-elll: save-tail recovery window opens. The
    // AppShell banner reads this off useEventStream and renders
    // "Recovering from <persisted_at>" until chronicler_recovered
    // clears it. Forensic fields are nullable for legacy v1
    // baselines (pre-elll persists).
    return {
      ...next,
      recovering: {
        // ?? keeps the legacy-frame defense (a pre-elll frame may omit a
        // forensic key entirely → undefined at runtime), without the cast.
        baselinePersistedAt: parsed.baseline_persisted_at ?? null,
        baselineDate: parsed.baseline_date ?? null,
        baselineGeneration: parsed.baseline_generation ?? null,
        pendingCacheCount: parsed.pending_cache_count ?? 0,
      },
    };
  }
  if (parsed.kind === 'chronicler_recovered') {
    // ck3_chronicler-elll: recovery window closes. Drop the banner.
    // events_ingested is logged server-side; the banner doesn't
    // surface it on dismissal (the IngestActivityStrip will
    // immediately pick up the resulting save_pair_completed).
    return { ...next, recovering: null };
  }
  if (parsed.kind === 'campaign_auto_resumed') {
    // ck3_chronicler-n52f: auto-resume fired for another campaign.
    // Surface the latest frame; the AppShell banner clears it on
    // [Switch] or [Dismiss]. seq bumps so a repeat arrival from
    // the same source still re-renders.
    const prevSeq = next.autoResumed?.seq ?? 0;
    return {
      ...next,
      autoResumed: {
        campaignId: parsed.campaign_id,
        campaignName: parsed.campaign_name,
        savePathName: parsed.save_path_name,
        inGameDate: parsed.in_game_date,
        seq: prevSeq + 1,
      },
    };
  }
  if (parsed.kind === 'save_tail_stopped') {
    // ck3_chronicler-tail-pip: save-tail has exited (halt-save-tail
    // route, process shutdown, or natural drain). Flip dead=true so
    // the AppShell pip immediately reads "Tail unavailable" rather
    // than waiting for the SSE channel to error out.
    // ck3_chronicler-27ov.30: the transport stays OPEN — uvicorn and
    // the SSE bus are still healthy; only the tail loop exited.
    // Re-adopting a save spawns a fresh run_save_ingest whose first
    // cache_state frame on this same channel clears dead above.
    // connected still flips false: its consumer semantic is "tail
    // live" (pip click offers halt only while connected).
    return { ...next, connected: false, dead: true };
  }
  if (
    parsed.kind === 'narrative_queued' ||
    parsed.kind === 'narrative_generating' ||
    parsed.kind === 'narrative_completed' ||
    parsed.kind === 'narrative_failed'
  ) {
    // narrative_* frames are observability for the queue strip;
    // we don't buffer the events themselves (the queue endpoint
    // is the source of truth) — just bump a counter so consumers
    // can refetch on change. ck3_chronicler-k91o: enumerated rather
    // than a startsWith() guard so TS narrows ``parsed`` to
    // EventIngestedFrame at the buffer fallthrough below.
    return { ...next, narrativeSeq: next.narrativeSeq + 1 };
  }
  // ck3_chronicler-k91o: only event_ingested remains in the union here —
  // TS has narrowed ``parsed`` accordingly, so it slots into the typed
  // events buffer without a cast.
  const buffered = [parsed, ...next.events];
  const trimmed =
    buffered.length > context.bufferSize
      ? buffered.slice(0, context.bufferSize)
      : buffered;
  return { ...next, events: trimmed };
}

// Issue #2: the ingest strip was SSE-only, and cache_state is published on
// ingest *activity* — so after a backend restart a reconnected client got
// no frame at all until the next autosave and kept rendering a count from a
// process that no longer existed ("4 to ingest" forever against an empty
// cache). This route is the resync backstop; SSE stays the live source of
// truth during play.
async function resyncCacheState(
  campaignName: string,
  seqAtRequest: number,
  apply: (fn: (state: EventStreamState) => EventStreamState) => void,
): Promise<void> {
  let snapshot: { pending: number; bytes: number };
  try {
    const resp = await fetch(
      `/api/campaigns/${encodeURIComponent(campaignName)}/ingest-state`,
    );
    if (!resp.ok) return;
    snapshot = (await resp.json()) as { pending: number; bytes: number };
  } catch {
    // A failed resync leaves the stream in charge, which is where it was
    // before this existed. Never let it disturb the channel.
    return;
  }
  apply((state) => {
    // The REST read was taken before any frame that arrived while it was
    // in flight, so a moved seq means we are holding stale news.
    if (state.cacheStateSeq !== seqAtRequest) return state;
    return {
      ...state,
      cacheState: {
        kind: 'cache_state',
        pending: snapshot.pending,
        bytes: snapshot.bytes,
        // Not readable from disk — it is a counter inside the running
        // ingest loop. Carry forward what the stream last said rather
        // than inventing a number.
        gc_drops_lifetime: state.cacheState?.gc_drops_lifetime ?? 0,
      },
    };
  });
}

const store = createSseChannelStore<EventStreamState, { bufferSize: number }>({
  url: (campaignName) => `/api/sse/ingest/${encodeURIComponent(campaignName)}`,
  initialState: () => ({ ...EMPTY_STATE }),
  defaultContext: { bufferSize: DEFAULT_BUFFER_SIZE },
  reduce: reduceIngestFrame,
  // Issue #2: a confirmed-dead channel can no longer vouch for its last
  // cache_state. null is the honest reading — IngestActivityStrip takes
  // `pendingCount: number | null` and renders nothing for null, so the
  // strip disappears instead of asserting a count from a dead process.
  // Cleared here rather than on the first error so a routine blip does
  // not flap the strip.
  onDead: (state) => ({ ...state, cacheState: null }),
  onOpen: (campaignName, apply) => {
    // Capture the seq synchronously, before the request goes out. Two
    // resyncs in flight each hold their own, so neither can be fooled by
    // the other's bookkeeping.
    let seq = 0;
    apply((state) => {
      seq = state.cacheStateSeq;
      return state;
    });
    void resyncCacheState(campaignName, seq, apply);
  },
});

// Test-only: reset all channels. Vitest runs files in isolation but
// cross-test contamination within a file (multiple useEventStream
// suites sharing a campaign name) would otherwise carry over state.
export function __resetChannelsForTesting(): void {
  store.reset();
}

export function useEventStream(
  campaignName: string | null,
  options: { bufferSize?: number } = {},
): EventStreamState {
  const bufferSize = options.bufferSize ?? DEFAULT_BUFFER_SIZE;
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (!campaignName) return () => {};
      // First subscriber wins on bufferSize; in practice only
      // IngestPage cares about the events buffer and only it
      // overrides the default.
      return store.subscribe(campaignName, onStoreChange, { bufferSize });
    },
    [campaignName, bufferSize],
  );
  const getSnapshot = useCallback(
    () => (campaignName ? (store.getState(campaignName) ?? EMPTY_STATE) : EMPTY_STATE),
    [campaignName],
  );
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

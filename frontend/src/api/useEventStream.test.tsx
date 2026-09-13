// Regression coverage for the useEventStream singleton transport
// (audit F-08 / ck3_chronicler-62u8) and the dead-channel state
// (audit F-45 / ck3_chronicler-ilss).
//
// Pre-fix, every call site mounted its own EventSource — App,
// NarrativeQueueStrip, and any of the page-level hooks together would
// open 4-6 sockets on one nav walk and breach the browser's per-origin
// cap. These tests instrument a fake EventSource ctor and assert that
// N concurrent subscribers for one campaign open exactly one socket.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook, screen } from '@testing-library/react';

import { IngestActivityStrip } from '../shell/IngestActivityStrip';
import {
  __resetChannelsForTesting,
  useEventStream,
} from './useEventStream';

interface FakeEventSource {
  url: string;
  readyState: number;
  close: () => void;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  // test helpers
  fireOpen: () => void;
  fireError: () => void;
  fireMessage: (data: unknown) => void;
}

let constructed: FakeEventSource[] = [];
const closed: FakeEventSource[] = [];

class FakeEventSourceImpl implements FakeEventSource {
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    constructed.push(this);
  }

  close = (): void => {
    this.readyState = 2;
    closed.push(this);
  };

  fireOpen = (): void => this.onopen?.(new Event('open'));
  fireError = (): void => this.onerror?.(new Event('error'));
  fireMessage = (data: unknown): void => {
    this.onmessage?.(
      new MessageEvent('message', { data: JSON.stringify(data) }),
    );
  };
}

describe('useEventStream — singleton + dead state', () => {
  beforeEach(() => {
    constructed = [];
    closed.length = 0;
    __resetChannelsForTesting();
    vi.stubGlobal('EventSource', FakeEventSourceImpl);
    // Issue #2: every channel open now fires an ingest-state resync
    // fetch. Left unstubbed it escapes into happy-dom's teardown as an
    // AbortError; stubbed to a never-settling promise it stays inert for
    // the tests that aren't about it, and the resync tests below install
    // their own resolving stub.
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetChannelsForTesting();
  });

  it('opens exactly one EventSource for N concurrent subscribers on the same campaign (audit F-08)', () => {
    const a = renderHook(() => useEventStream('saga'));
    const b = renderHook(() => useEventStream('saga'));
    const c = renderHook(() => useEventStream('saga'));

    expect(constructed).toHaveLength(1);
    expect(constructed[0]!.url).toBe('/api/sse/ingest/saga');

    a.unmount();
    b.unmount();
    expect(closed).toHaveLength(0); // c still subscribed
    c.unmount();
    expect(closed).toHaveLength(1); // refcount hit zero
  });

  it('opens distinct EventSources per campaign', () => {
    renderHook(() => useEventStream('saga'));
    renderHook(() => useEventStream('hraevn'));
    expect(constructed).toHaveLength(2);
    expect(constructed.map((s) => s.url).sort()).toEqual([
      '/api/sse/ingest/hraevn',
      '/api/sse/ingest/saga',
    ]);
  });

  it('reopens a closed channel when a new subscriber arrives later', () => {
    const a = renderHook(() => useEventStream('saga'));
    a.unmount();
    expect(closed).toHaveLength(1);

    renderHook(() => useEventStream('saga'));
    expect(constructed).toHaveLength(2);
  });

  it('shares state across subscribers — second mount sees what the first received (audit F-08)', () => {
    const a = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireOpen();
      constructed[0]!.fireMessage({ kind: 'narrative_completed' });
      constructed[0]!.fireMessage({ kind: 'narrative_completed' });
    });
    expect(a.result.current.narrativeSeq).toBe(2);
    expect(a.result.current.connected).toBe(true);

    // Late subscriber — same channel — sees the prior seq.
    const b = renderHook(() => useEventStream('saga'));
    expect(b.result.current.narrativeSeq).toBe(2);
    expect(b.result.current.connected).toBe(true);
  });

  it('flips dead=true after MAX_CONSECUTIVE_ERRORS and stops the underlying source (audit F-45)', () => {
    // ck3_chronicler-jjqf: errors within STARTUP_GRACE_MS of channel
    // mount are intentionally ignored to survive the cold-start race.
    // Advance Date.now past the grace window before firing errors so
    // the dead-state semantics under test still kick in.
    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const a = renderHook(() => useEventStream('saga'));
    expect(constructed).toHaveLength(1);
    dateSpy.mockReturnValue(t0 + 10_000);

    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    // Two errors — still alive, just disconnected.
    expect(a.result.current.connected).toBe(false);
    expect(a.result.current.dead).toBe(false);
    expect(closed).toHaveLength(0);

    act(() => {
      constructed[0]!.fireError();
    });
    expect(a.result.current.dead).toBe(true);
    expect(a.result.current.connected).toBe(false);
    // Source is closed — no further reconnect-storming the server.
    expect(closed).toHaveLength(1);
    dateSpy.mockRestore();
  });

  it('resets the consecutive-error counter on a successful onopen', () => {
    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const a = renderHook(() => useEventStream('saga'));
    dateSpy.mockReturnValue(t0 + 10_000);
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireOpen();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    // 2 errors then reset then 2 errors — under the cap; not dead.
    expect(a.result.current.dead).toBe(false);
    dateSpy.mockRestore();
  });

  // ck3_chronicler-jjqf: cold-start race tests.
  it('ignores errors that fire within the STARTUP_GRACE_MS window', () => {
    // Simulates the vite-proxies-ECONNREFUSED-before-uvicorn-binds
    // case: three errors in milliseconds before the backend ever
    // responds. Without the grace window, the channel would mark
    // itself permanently dead. With it, the channel stays alive
    // waiting for the backend to come up.
    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const a = renderHook(() => useEventStream('saga'));
    // All errors fire within the grace window (Date.now hasn't moved).
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    expect(a.result.current.dead).toBe(false);
    expect(closed).toHaveLength(0);
    dateSpy.mockRestore();
  });

  it('counts errors normally after the grace window expires', () => {
    // Same channel, but the third error lands after the grace expires.
    // Confirms the grace doesn't permanently disable the dead-state
    // detection — only suppresses it during cold start.
    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const a = renderHook(() => useEventStream('saga'));
    // Two errors during grace — ignored.
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    expect(a.result.current.dead).toBe(false);
    // Advance past the grace window.
    dateSpy.mockReturnValue(t0 + 10_000);
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    expect(a.result.current.dead).toBe(true);
    dateSpy.mockRestore();
  });

  // ck3_chronicler-bges: save_pair_completed handling
  it('initialises lastSavePairCompleted as null', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    expect(result.current.lastSavePairCompleted).toBeNull();
  });

  it('stores the latest save_pair_completed frame on state', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    const frame = {
      kind: 'save_pair_completed',
      save_filename: 'autosave.ck3',
      in_game_date: '1066.4.11',
      completed_at: '2026-05-10T12:34:56+00:00',
      event_count: 3,
      event_type_tally: { marriage: 1, birth: 2 },
    };
    act(() => {
      constructed[0]!.fireMessage(frame);
    });
    expect(result.current.lastSavePairCompleted).toEqual(frame);
    // Should not appear in the events buffer.
    expect(result.current.events).toHaveLength(0);
  });

  it('overwrites lastSavePairCompleted when a second frame arrives', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage({
        kind: 'save_pair_completed',
        save_filename: 'autosave.ck3',
        in_game_date: '1066.4.10',
        completed_at: '2026-05-10T12:00:00+00:00',
        event_count: 1,
        event_type_tally: {},
      });
    });
    act(() => {
      constructed[0]!.fireMessage({
        kind: 'save_pair_completed',
        save_filename: 'autosave.ck3',
        in_game_date: '1066.4.11',
        completed_at: '2026-05-10T12:34:56+00:00',
        event_count: 3,
        event_type_tally: { marriage: 1, birth: 2 },
      });
    });
    // ck3_chronicler-k91o: lastSavePairCompleted is typed as
    // SavePairCompletedFrame now — in_game_date reads without a cast.
    expect(result.current.lastSavePairCompleted?.in_game_date).toBe(
      '1066.4.11',
    );
  });

  // ck3_chronicler-elll: recovery state framing.
  it('initialises recovering as null', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    expect(result.current.recovering).toBeNull();
  });

  it('populates recovering on chronicler_recovering and clears on chronicler_recovered', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage({
        kind: 'chronicler_recovering',
        baseline_persisted_at: '2026-05-15T14:23:17+00:00',
        baseline_date: '867.4.12',
        baseline_generation: 42,
        pending_cache_count: 3,
      });
    });
    expect(result.current.recovering).toEqual({
      baselinePersistedAt: '2026-05-15T14:23:17+00:00',
      baselineDate: '867.4.12',
      baselineGeneration: 42,
      pendingCacheCount: 3,
    });

    act(() => {
      constructed[0]!.fireMessage({
        kind: 'chronicler_recovered',
        events_ingested: 17,
      });
    });
    expect(result.current.recovering).toBeNull();
  });

  it('tolerates legacy v1 baselines that send null forensic fields', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage({
        kind: 'chronicler_recovering',
        baseline_persisted_at: null,
        baseline_date: '867.4.12',
        baseline_generation: null,
        pending_cache_count: 0,
      });
    });
    expect(result.current.recovering).toEqual({
      baselinePersistedAt: null,
      baselineDate: '867.4.12',
      baselineGeneration: null,
      pendingCacheCount: 0,
    });
  });

  // ck3_chronicler-tail-pip: save_tail_stopped frame flips dead=true so
  // the AppShell pip immediately reads "Tail unavailable" without
  // waiting for the SSE channel to error 3 times.
  // ck3_chronicler-27ov.30: the transport stays OPEN — uvicorn and the
  // SSE bus are still healthy; only the tail loop exited.
  it('flips dead=true but keeps the EventSource open on a save_tail_stopped frame', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireOpen();
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.dead).toBe(false);
    expect(closed).toHaveLength(0);

    act(() => {
      constructed[0]!.fireMessage({
        kind: 'save_tail_stopped',
        stopped_at: '2026-05-28T10:00:57+00:00',
      });
    });

    expect(result.current.connected).toBe(false);
    expect(result.current.dead).toBe(true);
    expect(closed).toHaveLength(0);
  });

  // ck3_chronicler-27ov.30: recoverable dead.
  it('clears dead when a tail-evidencing frame arrives on the still-open channel', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireOpen();
      constructed[0]!.fireMessage({
        kind: 'save_tail_stopped',
        stopped_at: '2026-05-28T10:00:57+00:00',
      });
    });
    expect(result.current.dead).toBe(true);

    // Re-adopt spawns a fresh run_save_ingest; its first cache_state
    // frame on the same healthy channel revives the badge.
    act(() => {
      constructed[0]!.fireMessage({ kind: 'cache_state', pending: 0 });
    });
    expect(result.current.dead).toBe(false);
  });

  it('does NOT clear dead on narrative_* frames (queue can run while tail is stopped)', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireOpen();
      constructed[0]!.fireMessage({
        kind: 'save_tail_stopped',
        stopped_at: '2026-05-28T10:00:57+00:00',
      });
      constructed[0]!.fireMessage({ kind: 'narrative_completed' });
    });
    expect(result.current.dead).toBe(true);
  });

  it('probes a dead channel on exponential backoff and revives on a successful open (audit M-F13)', () => {
    vi.useFakeTimers();
    try {
      const t0 = Date.now();
      const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
      const a = renderHook(() => useEventStream('saga'));
      dateSpy.mockReturnValue(t0 + 10_000);
      act(() => {
        constructed[0]!.fireError();
        constructed[0]!.fireError();
        constructed[0]!.fireError();
      });
      expect(a.result.current.dead).toBe(true);
      expect(constructed).toHaveLength(1);

      // First probe fires after the initial 10s delay.
      act(() => {
        vi.advanceTimersByTime(10_000);
      });
      expect(constructed).toHaveLength(2);

      // Probe fails (server still down): closes and reschedules at 20s.
      act(() => {
        constructed[1]!.fireError();
      });
      expect(a.result.current.dead).toBe(true);
      act(() => {
        vi.advanceTimersByTime(10_000);
      });
      expect(constructed).toHaveLength(2); // 20s not elapsed yet
      act(() => {
        vi.advanceTimersByTime(10_000);
      });
      expect(constructed).toHaveLength(3);

      // Backend is back: the probe opens, dead clears.
      act(() => {
        constructed[2]!.fireOpen();
      });
      expect(a.result.current.dead).toBe(false);
      expect(a.result.current.connected).toBe(true);
      dateSpy.mockRestore();
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels the pending reconnect probe when the last subscriber unmounts', () => {
    vi.useFakeTimers();
    try {
      const t0 = Date.now();
      const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0);
      const a = renderHook(() => useEventStream('saga'));
      dateSpy.mockReturnValue(t0 + 10_000);
      act(() => {
        constructed[0]!.fireError();
        constructed[0]!.fireError();
        constructed[0]!.fireError();
      });
      expect(a.result.current.dead).toBe(true);

      a.unmount(); // refCount hits 0 — channel deleted, timer cancelled
      act(() => {
        vi.advanceTimersByTime(600_000);
      });
      expect(constructed).toHaveLength(1); // no zombie probe
      dateSpy.mockRestore();
    } finally {
      vi.useRealTimers();
    }
  });

  // ck3_chronicler-n52f: campaign_auto_resumed frame consumption
  it('surfaces a campaign_auto_resumed frame on state.autoResumed', () => {
    const { result } = renderHook(() => useEventStream('genji'));
    act(() => {
      constructed[0]!.fireOpen();
      constructed[0]!.fireMessage({
        kind: 'campaign_auto_resumed',
        campaign_id: 'uuid-sleggja',
        campaign_name: 'Sleggja 867',
        save_path_name: 'autosave.ck3',
        in_game_date: '930.1.1',
      });
    });
    expect(result.current.autoResumed).toEqual({
      campaignId: 'uuid-sleggja',
      campaignName: 'Sleggja 867',
      savePathName: 'autosave.ck3',
      inGameDate: '930.1.1',
      seq: 1,
    });
  });

  it('bumps autoResumed.seq on each new frame (drain-storm pattern)', () => {
    const { result } = renderHook(() => useEventStream('genji'));
    act(() => {
      constructed[0]!.fireOpen();
      constructed[0]!.fireMessage({
        kind: 'campaign_auto_resumed',
        campaign_id: 'uuid-sleggja',
        campaign_name: 'Sleggja 867',
        save_path_name: 'autosave_1.ck3',
        in_game_date: '930.1.1',
      });
      constructed[0]!.fireMessage({
        kind: 'campaign_auto_resumed',
        campaign_id: 'uuid-sleggja',
        campaign_name: 'Sleggja 867',
        save_path_name: 'autosave_2.ck3',
        in_game_date: '930.2.1',
      });
    });
    expect(result.current.autoResumed?.seq).toBe(2);
    expect(result.current.autoResumed?.savePathName).toBe('autosave_2.ck3');
  });

  // ck3_chronicler-kgqa: save_dropped_foreign drives the red 'wrong game'
  // tail-status badge. The hook stores the latest frame on lastForeignDrop.
  it('initialises lastForeignDrop as null', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    expect(result.current.lastForeignDrop).toBeNull();
  });

  it('stores the latest save_dropped_foreign frame on state.lastForeignDrop', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    const frame = {
      kind: 'save_dropped_foreign',
      observed_playthrough_id: '3ad8d836',
      campaign_playthrough_id: 'ea60b6e9',
      save_filename: 'autosave.ck3',
      observed_at: '2026-05-31T20:30:00Z',
    };
    act(() => {
      constructed[0]!.fireMessage(frame);
    });
    expect(result.current.lastForeignDrop).toEqual(frame);
    // Should not appear in the events buffer.
    expect(result.current.events).toHaveLength(0);
  });

  it('overwrites lastForeignDrop when a second frame arrives', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage({
        kind: 'save_dropped_foreign',
        observed_playthrough_id: 'aaaaaaaa',
        campaign_playthrough_id: 'ea60b6e9',
        save_filename: 'autosave.ck3',
        observed_at: '2026-05-31T20:00:00Z',
      });
    });
    const secondFrame = {
      kind: 'save_dropped_foreign',
      observed_playthrough_id: 'bbbbbbbb',
      campaign_playthrough_id: 'ea60b6e9',
      save_filename: 'autosave.ck3',
      observed_at: '2026-05-31T20:30:00Z',
    };
    act(() => {
      constructed[0]!.fireMessage(secondFrame);
    });
    expect(result.current.lastForeignDrop).toEqual(secondFrame);
  });

  // ── Issue #2: stale ingest counts after an SSE drop ────────────────
  //
  // The reported symptom was "4 to ingest" forever against a provably
  // empty cache: the page had outlived several backend restarts, the
  // strip is SSE-only, and cache_state is published on ingest activity —
  // so nothing ever corrected the last frame from a dead process.

  const CACHE_STATE = (pending: number) => ({
    kind: 'cache_state',
    pending,
    bytes: pending * 1000,
    gc_drops_lifetime: 2,
  });

  /** Fire enough errors to cross MAX_CONSECUTIVE_ERRORS, past the
   * jjqf startup grace. Returns the Date.now spy for restoration. */
  const killChannel = (): void => {
    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0 + 10_000);
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    dateSpy.mockRestore();
  };

  it('clears a stale cacheState when the channel is confirmed dead', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(4));
    });
    expect(result.current.cacheState?.pending).toBe(4);

    killChannel();

    expect(result.current.dead).toBe(true);
    // null, not 0: "unknown" is the honest reading, and
    // IngestActivityStrip renders nothing for it. Asserting 0 here would
    // be claiming knowledge a dead channel does not have.
    expect(result.current.cacheState).toBeNull();
  });

  it('does not clear cacheState on a transient disconnect below the dead threshold', () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(4));
    });

    const t0 = Date.now();
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(t0 + 10_000);
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    dateSpy.mockRestore();

    // Two errors is a blip the browser usually heals in milliseconds.
    // Clearing here would flap the strip on every hiccup.
    expect(result.current.dead).toBe(false);
    expect(result.current.cacheState?.pending).toBe(4);
  });

  it('resyncs the count from REST on reconnect, with no new save required', async () => {
    // The acceptance case: the strip shows 4, the backend restarts, the
    // cache is empty, and no autosave arrives to trigger a cache_state
    // frame. Pre-fix the count sat at 4 until the user reloaded.
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(4));
    });
    expect(result.current.cacheState?.pending).toBe(4);

    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ pending: 0, bytes: 0 }),
        }),
      ),
    );

    await act(async () => {
      constructed[0]!.fireOpen();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(globalThis.fetch).toHaveBeenCalledWith('/api/campaigns/saga/ingest-state');
    expect(result.current.cacheState?.pending).toBe(0);
    // gc_drops_lifetime is not readable from disk — the REST merge keeps
    // what the stream last reported rather than inventing a 0.
    expect(result.current.cacheState?.gc_drops_lifetime).toBe(2);
  });

  it('discards a resync response that a fresher cache_state frame overtook', async () => {
    const { result } = renderHook(() => useEventStream('saga'));

    let resolveFetch: (v: unknown) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            resolveFetch = resolve;
          }),
      ),
    );

    act(() => {
      constructed[0]!.fireOpen();
    });

    // While the REST read is in flight, a live frame lands. It is newer
    // than the snapshot the server took, so it must win.
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(7));
    });

    await act(async () => {
      resolveFetch({ ok: true, json: () => Promise.resolve({ pending: 0, bytes: 0 }) });
      await Promise.resolve();
      await Promise.resolve();
    });

    // Newest wins, not last-arriving.
    expect(result.current.cacheState?.pending).toBe(7);
  });

  it('leaves the stream in charge when the resync fetch fails', async () => {
    const { result } = renderHook(() => useEventStream('saga'));
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(3));
    });

    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))));

    await act(async () => {
      constructed[0]!.fireOpen();
      await Promise.resolve();
      await Promise.resolve();
    });

    // A failed resync is a no-op, never a channel-level failure.
    expect(result.current.cacheState?.pending).toBe(3);
    expect(result.current.dead).toBe(false);
  });

  it('leaves no stale "4 to ingest" in the DOM once a dead channel reconnects', () => {
    // The reported symptom, asserted end-to-end through App.tsx's actual
    // wiring (`tailDead={dead}`, `pendingCount={cacheState?.pending ??
    // null}`) rather than per-layer.
    //
    // The window under test is the one the user sees. While the channel
    // is dead the strip is hidden anyway (tailDead short-circuits it), so
    // clearing cacheState there proves nothing on its own. It pays off on
    // the *reconnect*: the strip comes back immediately, and without the
    // clear it comes back rendering the count from the dead process until
    // the resync resolves — which here it never does, standing in for a
    // slow or failed one.
    function Harness(): React.JSX.Element {
      const { cacheState, dead } = useEventStream('saga');
      return (
        <IngestActivityStrip
          campaignName="saga"
          lastTickProps={{
            save_filename: 'autosave.ck3',
            in_game_date: '1066.4.11',
            ingested_at: new Date().toISOString(),
            event_count: 3,
            event_type_tally: { birth: 3 },
          }}
          tailDead={dead}
          pendingCount={cacheState?.pending ?? null}
        />
      );
    }

    render(<Harness />);
    act(() => {
      constructed[0]!.fireMessage(CACHE_STATE(4));
    });
    expect(screen.getByText(/4 to ingest/)).toBeInTheDocument();

    killChannel();
    // Dead: the strip is hidden wholesale by tailDead.
    expect(screen.queryByText(/to ingest/)).not.toBeInTheDocument();

    // The reconnect probe opens. fetch is the never-settling stub from
    // beforeEach, so no resync arrives to paper over a stale value.
    act(() => {
      constructed[0]!.fireOpen();
    });

    expect(screen.queryByText(/4 to ingest/)).not.toBeInTheDocument();
    expect(screen.queryByText(/to ingest/)).not.toBeInTheDocument();
  });
});

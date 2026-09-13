// M-F12 (27ov.65): generic ref-counted SSE channel store. The
// transport machinery — singleton EventSource per key (audit F-08),
// dead-after-3-errors with the jjqf cold-start grace window (F-45),
// slow-backoff dead-channel reconnect probing (M-F13/27ov.30),
// ref-counted release, notifier fan-out, test reset — used to be
// duplicated between useEventStream.ts and useLogStream.ts (~120
// lines, with the jjqf fix applied twice). One owner now; the domain
// hooks supply a frame reducer and consume state via
// useSyncExternalStore (which also removes the render-from-mutable-
// module-state tearing risk the hand-rolled setTick pattern had).

export interface SseChannelBaseState {
  // True from the moment the EventSource opens until it errors out.
  connected: boolean;
  // True once the channel hits MAX_CONSECUTIVE_ERRORS. Kept probing on
  // a slow backoff (27ov.30) so a routine backend restart heals
  // without a manual page reload. Reducers may also clear it when a
  // delivered frame proves the underlying service is back.
  dead: boolean;
}

const MAX_CONSECUTIVE_ERRORS = 3;
// ck3_chronicler-jjqf: errors that fire within this window after the
// channel is constructed don't count toward MAX_CONSECUTIVE_ERRORS.
// This is the cold-start race: in `chronicler dev`, vite (5173) starts
// before uvicorn (8000) binds; the browser EventSource hits
// ECONNREFUSED through the vite proxy and auto-reconnects in
// milliseconds, racing past the dead-threshold before uvicorn comes up.
const STARTUP_GRACE_MS = 5_000;
// ck3_chronicler-27ov.30: dead-channel reconnect probing. First probe
// fires 10s after the channel dies; each failed probe doubles the wait
// up to the 5-minute ceiling.
const RECONNECT_INITIAL_DELAY_MS = 10_000;
const RECONNECT_MAX_DELAY_MS = 300_000;

interface Channel<TState, TContext> {
  state: TState;
  source: EventSource | null;
  // First subscriber wins on context (e.g. the events buffer size).
  context: TContext;
  consecutiveErrors: number;
  // jjqf: wall-clock at channel construction (NOT per reconnect — a
  // probe's failure is past the grace and counts immediately).
  mountedAt: number;
  notifiers: Set<() => void>;
  refCount: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  reconnectDelayMs: number;
}

export interface SseChannelStoreConfig<
  TState extends SseChannelBaseState,
  TContext,
> {
  url: (key: string) => string;
  initialState: () => TState;
  defaultContext: TContext;
  // Reduce one parsed frame into the next state. Must be pure and
  // return a NEW object when anything changed (consumers compare by
  // reference via useSyncExternalStore).
  reduce: (state: TState, frame: unknown, context: TContext) => TState;
  // Issue #2: drop state that only a live stream can vouch for, at the
  // moment the channel is confirmed dead. Deliberately NOT called on a
  // transient error — `connected` flips false on the first failure and
  // the browser usually reconnects within milliseconds, so clearing
  // there would flap the UI on every blip. This fires once, after
  // MAX_CONSECUTIVE_ERRORS, when we have actually stopped believing the
  // last frame. Must be pure.
  onDead?: (state: TState) => TState;
  // Issue #2: called on every EventSource open — first connect and each
  // successful reconnect probe alike. For resync fetches whose result is
  // applied through `apply`, which is a no-op if the channel closed in
  // the meantime. Fire-and-forget: a rejected promise must not take the
  // channel down, so implementations swallow their own errors.
  onOpen?: (key: string, apply: (fn: (state: TState) => TState) => void) => void;
}

export interface SseChannelStore<
  TState extends SseChannelBaseState,
  TContext,
> {
  /** Open (or join) the channel for ``key`` and register a change
   * notifier. Returns the unsubscribe function — shaped for direct use
   * as a useSyncExternalStore subscribe. */
  subscribe(key: string, notifier: () => void, context?: TContext): () => void;
  /** Current state for ``key``; undefined when no channel is open. */
  getState(key: string): TState | undefined;
  /** External state injection (e.g. seeding a backfill). No-op when
   * the channel isn't open. */
  update(key: string, fn: (state: TState) => TState): void;
  /** Test-only: close every channel, cancel timers, clear the map. */
  reset(): void;
}

export function createSseChannelStore<
  TState extends SseChannelBaseState,
  TContext = undefined,
>(config: SseChannelStoreConfig<TState, TContext>): SseChannelStore<TState, TContext> {
  const channels = new Map<string, Channel<TState, TContext>>();

  function notify(ch: Channel<TState, TContext>): void {
    // Snapshot to a new array so a notifier that unsubscribes (and
    // removes itself from the set) doesn't mutate the iteration.
    for (const n of [...ch.notifiers]) n();
  }

  // 27ov.30: schedule a single reconnect probe for a dead channel,
  // doubling the delay per consecutive failure. A probe that fails
  // re-enters the dead path in onerror (the error counter is already
  // past the threshold) and reschedules itself with the longer delay;
  // a probe that opens resets both the counter and the delay.
  function scheduleReconnect(key: string, ch: Channel<TState, TContext>): void {
    if (ch.reconnectTimer !== null) return;
    const delay = ch.reconnectDelayMs;
    ch.reconnectDelayMs = Math.min(ch.reconnectDelayMs * 2, RECONNECT_MAX_DELAY_MS);
    ch.reconnectTimer = setTimeout(() => {
      ch.reconnectTimer = null;
      // The channel may have been released (refCount 0 deletes it) or
      // revived by another path while the timer was pending.
      if (channels.get(key) !== ch || ch.source !== null) return;
      connect(key, ch);
    }, delay);
  }

  function connect(key: string, ch: Channel<TState, TContext>): void {
    const es = new EventSource(config.url(key));
    ch.source = es;

    es.onopen = (): void => {
      ch.consecutiveErrors = 0;
      ch.reconnectDelayMs = RECONNECT_INITIAL_DELAY_MS;
      ch.state = { ...ch.state, connected: true, dead: false };
      notify(ch);
      // Issue #2: resync after the state update, so a synchronous
      // implementation sees the channel already open.
      config.onOpen?.(key, (fn) => {
        // The channel may have been released or replaced while the fetch
        // was in flight; applying to a stale channel would resurrect
        // state nobody is watching.
        if (channels.get(key) !== ch) return;
        ch.state = fn(ch.state);
        notify(ch);
      });
    };

    es.onerror = (): void => {
      // jjqf: within the startup grace we still let the browser keep
      // retrying — we just don't count it against the dead threshold.
      if (Date.now() - ch.mountedAt < STARTUP_GRACE_MS) {
        return;
      }
      ch.consecutiveErrors += 1;
      if (ch.consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
        // Likely 404, server gone, or CORS. Close to stop the
        // browser's tight auto-reconnect loop and surface the dead
        // chip — but keep probing on a slow backoff (27ov.30).
        es.close();
        ch.source = null;
        ch.state = { ...ch.state, connected: false, dead: true };
        // Issue #2: confirmed dead is the transition that invalidates
        // stream-only state. Applied after the dead flag so the hook sees
        // one coherent update, not two renders.
        if (config.onDead) ch.state = config.onDead(ch.state);
        scheduleReconnect(key, ch);
      } else {
        ch.state = { ...ch.state, connected: false };
      }
      notify(ch);
    };

    es.onmessage = (msg: MessageEvent): void => {
      try {
        const parsed: unknown = JSON.parse(msg.data as string);
        ch.state = config.reduce(ch.state, parsed, ch.context);
        notify(ch);
      } catch {
        // Malformed frames are observability artefacts on a corrupt
        // wire; quietly drop. The bus log surface is server-side.
      }
    };
  }

  function openChannel(key: string, context: TContext): Channel<TState, TContext> {
    const existing = channels.get(key);
    if (existing) {
      existing.refCount += 1;
      return existing;
    }

    const ch: Channel<TState, TContext> = {
      state: config.initialState(),
      source: null,
      context,
      consecutiveErrors: 0,
      mountedAt: Date.now(),
      notifiers: new Set(),
      refCount: 1,
      reconnectTimer: null,
      reconnectDelayMs: RECONNECT_INITIAL_DELAY_MS,
    };
    channels.set(key, ch);

    // EventSource isn't available in JSDOM/happy-dom by default. Tests
    // inject their own; the channel exists with initial state so the
    // hook still has something to return.
    if (typeof window === 'undefined' || typeof window.EventSource !== 'function') {
      return ch;
    }

    connect(key, ch);
    return ch;
  }

  function releaseChannel(key: string): void {
    const ch = channels.get(key);
    if (!ch) return;
    ch.refCount -= 1;
    if (ch.refCount <= 0) {
      ch.source?.close();
      ch.source = null;
      // 27ov.30: cancel any pending reconnect probe so a released
      // channel can't resurrect itself.
      if (ch.reconnectTimer !== null) {
        clearTimeout(ch.reconnectTimer);
        ch.reconnectTimer = null;
      }
      channels.delete(key);
    }
  }

  return {
    subscribe(key, notifier, context) {
      const ch = openChannel(key, context ?? config.defaultContext);
      ch.notifiers.add(notifier);
      return (): void => {
        ch.notifiers.delete(notifier);
        releaseChannel(key);
      };
    },
    getState(key) {
      return channels.get(key)?.state;
    },
    update(key, fn) {
      const ch = channels.get(key);
      if (!ch) return;
      ch.state = fn(ch.state);
      notify(ch);
    },
    reset() {
      for (const ch of channels.values()) {
        ch.source?.close();
        if (ch.reconnectTimer !== null) {
          clearTimeout(ch.reconnectTimer);
          ch.reconnectTimer = null;
        }
      }
      channels.clear();
    },
  };
}

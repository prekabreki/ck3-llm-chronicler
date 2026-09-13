// ck3_chronicler-yrv3: useLogStream singleton transport tests.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import {
  __resetLogChannelForTesting,
  seedLogStream,
  useLogStream,
} from './useLogStream';
import type { LogEnvelope } from './types';

interface FakeEventSource {
  url: string;
  close: () => void;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  fireOpen: () => void;
  fireError: () => void;
  fireFrame: (env: LogEnvelope) => void;
}

let constructed: FakeEventSource[] = [];
const closed: FakeEventSource[] = [];

class FakeES implements FakeEventSource {
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    constructed.push(this);
  }
  close = (): void => {
    closed.push(this);
  };
  fireOpen = (): void => this.onopen?.(new Event('open'));
  fireError = (): void => this.onerror?.(new Event('error'));
  fireFrame = (env: LogEnvelope): void => {
    this.onmessage?.(
      new MessageEvent('message', { data: JSON.stringify(env) }),
    );
  };
}

function env(seq: number, message = `m${seq}`): LogEnvelope {
  return {
    seq,
    ts: '2026-05-13T20:00:00.000Z',
    level: 'INFO',
    logger: 'chronicler.test',
    message,
  };
}

describe('useLogStream', () => {
  beforeEach(() => {
    constructed = [];
    closed.length = 0;
    __resetLogChannelForTesting();
    vi.stubGlobal('EventSource', FakeES);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetLogChannelForTesting();
  });

  it('opens exactly one EventSource for N concurrent subscribers', () => {
    renderHook(() => useLogStream());
    renderHook(() => useLogStream());
    renderHook(() => useLogStream());
    expect(constructed).toHaveLength(1);
    expect(constructed[0]!.url).toBe('/api/sse/logs');
  });

  it('appends frames to the rolling buffer and bumps lastSeenSeq', () => {
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireFrame(env(1, 'first'));
      constructed[0]!.fireFrame(env(2, 'second'));
    });
    expect(result.current.envelopes.map((e) => e.message)).toEqual([
      'first',
      'second',
    ]);
    expect(result.current.lastSeenSeq).toBe(2);
  });

  it('dedupes frames whose seq is at or below the cursor', () => {
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireFrame(env(5, 'first'));
      // Replay of a seq we already saw — must be dropped.
      constructed[0]!.fireFrame(env(5, 'duplicate'));
      // Out-of-order older seq — also dropped.
      constructed[0]!.fireFrame(env(3, 'stale'));
    });
    expect(result.current.envelopes.map((e) => e.message)).toEqual(['first']);
    expect(result.current.lastSeenSeq).toBe(5);
  });

  it('seedLogStream prepends backfill envelopes and advances cursor', () => {
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireFrame(env(10, 'live'));
      seedLogStream([env(7, 'backfill-a'), env(8, 'backfill-b')]);
    });
    // Order: backfill envelopes are older — they prepend, live frame
    // stays at the tail.
    expect(result.current.envelopes.map((e) => e.message)).toEqual([
      'backfill-a',
      'backfill-b',
      'live',
    ]);
    expect(result.current.lastSeenSeq).toBe(10);
  });

  it('seedLogStream dedupes against seqs already present in the buffer', () => {
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireFrame(env(5, 'live'));
      // env(5) is already in the buffer — must be skipped to avoid a
      // duplicate row. env(3) / env(4) aren't in the buffer (they're
      // older than the live frame), so they prepend.
      seedLogStream([env(3, 'older-a'), env(4, 'older-b'), env(5, 'dup')]);
    });
    expect(result.current.envelopes.map((e) => e.message)).toEqual([
      'older-a',
      'older-b',
      'live',
    ]);
  });

  it('ignores SSE errors within the startup grace window (jjqf-style)', () => {
    const t0 = Date.now();
    const spy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    expect(result.current.dead).toBe(false);
    expect(closed).toHaveLength(0);
    spy.mockRestore();
  });

  it('flips dead after MAX_CONSECUTIVE_ERRORS past the grace window', () => {
    const t0 = Date.now();
    const spy = vi.spyOn(Date, 'now').mockReturnValue(t0);
    const { result } = renderHook(() => useLogStream());
    spy.mockReturnValue(t0 + 10_000);
    act(() => {
      constructed[0]!.fireError();
      constructed[0]!.fireError();
      constructed[0]!.fireError();
    });
    expect(result.current.dead).toBe(true);
    expect(closed).toHaveLength(1);
    spy.mockRestore();
  });

  it('sets connected=true on onopen', () => {
    const { result } = renderHook(() => useLogStream());
    act(() => {
      constructed[0]!.fireOpen();
    });
    expect(result.current.connected).toBe(true);
  });
});

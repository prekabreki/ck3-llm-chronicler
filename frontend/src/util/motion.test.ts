import { renderHook, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { prefersReducedMotion, useReducedMotion, REDUCED_MOTION_QUERY } from './motion';

interface FakeMql {
  matches: boolean;
  media: string;
  fire: (matches: boolean) => void;
  addEventListener?: ReturnType<typeof vi.fn>;
  removeEventListener?: ReturnType<typeof vi.fn>;
  addListener?: ReturnType<typeof vi.fn>;
  removeListener?: ReturnType<typeof vi.fn>;
}

/** A matchMedia double. `legacy` exposes only addListener/removeListener, the
 *  older WebKit shape the hook still has to survive. */
function stubMatchMedia(matches: boolean, { legacy = false } = {}): FakeMql {
  const listeners: ((e: MediaQueryListEvent) => void)[] = [];
  const mql: FakeMql = {
    matches,
    media: REDUCED_MOTION_QUERY,
    fire: (next: boolean) => {
      mql.matches = next;
      listeners.forEach((l) => l({ matches: next } as MediaQueryListEvent));
    },
  };
  if (legacy) {
    mql.addListener = vi.fn((l) => { listeners.push(l); });
    mql.removeListener = vi.fn();
  } else {
    mql.addEventListener = vi.fn((_e, l) => { listeners.push(l); });
    mql.removeEventListener = vi.fn();
  }
  vi.stubGlobal('matchMedia', vi.fn(() => mql));
  return mql;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('prefersReducedMotion', () => {
  it('reports what the media query says', () => {
    stubMatchMedia(true);
    expect(prefersReducedMotion()).toBe(true);
  });

  it('is false when the user has not asked for reduced motion', () => {
    stubMatchMedia(false);
    expect(prefersReducedMotion()).toBe(false);
  });

  it('defaults to animating when matchMedia does not exist', () => {
    // "Cannot know" must match what CSS does in the same situation, which is to
    // animate. Returning true here would silently kill motion on any environment
    // without matchMedia.
    vi.stubGlobal('matchMedia', undefined);
    expect(prefersReducedMotion()).toBe(false);
  });

  it('asks for the reduced-motion query specifically', () => {
    stubMatchMedia(false);
    prefersReducedMotion();
    expect(window.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)');
  });
});

describe('useReducedMotion', () => {
  it('starts from the current setting', () => {
    stubMatchMedia(true);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
  });

  it('follows the setting when it changes mid-session', () => {
    const mql = stubMatchMedia(false);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
    act(() => { mql.fire(true); });
    expect(result.current).toBe(true);
  });

  it('unsubscribes on unmount', () => {
    const mql = stubMatchMedia(false);
    const { unmount } = renderHook(() => useReducedMotion());
    unmount();
    expect(mql.removeEventListener).toHaveBeenCalled();
  });

  it('survives a browser that only has the legacy addListener', () => {
    const mql = stubMatchMedia(false, { legacy: true });
    const { result, unmount } = renderHook(() => useReducedMotion());
    expect(mql.addListener).toHaveBeenCalled();
    act(() => { mql.fire(true); });
    expect(result.current).toBe(true);
    unmount();
    expect(mql.removeListener).toHaveBeenCalled();
  });

  it('renders without a matchMedia at all', () => {
    vi.stubGlobal('matchMedia', undefined);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
  });
});

// audit L28 (27ov.81): tracked-membership mutations (add / delete /
// auto-track) must invalidate BOTH the tracked list and the
// suggested-candidates list. Before this fix only TrackedPage's own
// Track button refreshed the suggestions; tracking via the Codex or
// auto-track left "Suggested souls" showing the now-tracked character
// until a manual refetch. These tests pin the dual invalidation so a
// future refactor can't silently drop the suggested-candidates key.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import {
  queryKeys,
  useAddTracked,
  useAutoTrack,
  useDeleteTracked,
} from './queries';
import * as client from './client';
import type { TrackedResponse } from './types';

const CAMPAIGN = 'Wessex';

const A_TRACKED: TrackedResponse = {
  character_id: 555,
  first_name: 'Athelstan',
  nickname: null,
  role: null,
  added_at: '2026-04-15T08:00:00+00:00',
  biography_count: 0,
  monthly_token_spend: 0,
  paused_at: null,
  bumped_at: null,
  coa_json: null,
};

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }): React.JSX.Element {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function freshClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function expectBothInvalidated(spy: ReturnType<typeof vi.spyOn>): void {
  expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.tracked(CAMPAIGN) });
  expect(spy).toHaveBeenCalledWith({
    queryKey: queryKeys.suggestedCandidates(CAMPAIGN),
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('tracked-membership invalidation (L28)', () => {
  it('useAddTracked invalidates tracked AND suggested-candidates', async () => {
    vi.spyOn(client, 'addTracked').mockResolvedValue(A_TRACKED);
    const qc = freshClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useAddTracked(CAMPAIGN), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ character_id: 555 });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expectBothInvalidated(spy);
  });

  it('useDeleteTracked invalidates tracked AND suggested-candidates', async () => {
    vi.spyOn(client, 'deleteTracked').mockResolvedValue(undefined);
    const qc = freshClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useDeleteTracked(CAMPAIGN), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate(555);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expectBothInvalidated(spy);
  });

  it('useAutoTrack invalidates tracked AND suggested-candidates', async () => {
    vi.spyOn(client, 'autoTrackFromSave').mockResolvedValue({
      added: [A_TRACKED],
      already_tracked: [],
      save_path: 'C:/saves/autosave.ck3',
    });
    const qc = freshClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useAutoTrack(CAMPAIGN), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate(undefined);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expectBothInvalidated(spy);
  });
});

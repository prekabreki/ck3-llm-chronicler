// ck3_chronicler-sz4t: QueuePage — verify the dedicated narrative-queue
// surface renders the four sections (in flight / queued / failed /
// completed), summary counts, and falls back to character-id when names
// aren't loaded.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { QueuePage } from './QueuePage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { NarrativeQueueResponse } from '../api/types';

const EMPTY: NarrativeQueueResponse = {
  queued: [],
  active: [],
  recent: [],
  completed_count: 0,
  failed_count: 0,
  avg_duration_ms: null,
};

const BUSY: NarrativeQueueResponse = {
  queued: [
    {
      item_id: 2,
      character_id: 5678,
      character_name: 'Astrid',
      kind: 'biography',
      status: 'queued',
      enqueued_at: '2026-05-04T12:00:00+00:00',
      started_at: null,
      completed_at: null,
      duration_ms: null,
      error: null,
    },
    {
      item_id: 3,
      character_id: 5678,
      character_name: 'Astrid',
      kind: 'biography',
      status: 'queued',
      enqueued_at: '2026-05-04T12:00:01+00:00',
      started_at: null,
      completed_at: null,
      duration_ms: null,
      error: null,
    },
  ],
  active: [
    {
      item_id: 1,
      character_id: 1234,
      character_name: 'Erik',
      kind: 'biography',
      status: 'generating',
      enqueued_at: '2026-05-04T11:59:00+00:00',
      started_at: '2026-05-04T11:59:30+00:00',
      completed_at: null,
      duration_ms: null,
      error: null,
    },
  ],
  recent: [
    {
      item_id: 99,
      character_id: 1234,
      character_name: 'Erik',
      kind: 'biography',
      status: 'failed' as const,
      enqueued_at: '2026-05-04T11:55:00+00:00',
      started_at: '2026-05-04T11:55:01+00:00',
      completed_at: '2026-05-04T11:55:30+00:00',
      duration_ms: 29000,
      error: 'Ollama timed out after 3 attempts',
    },
    {
      item_id: 98,
      character_id: 5678,
      character_name: 'Astrid',
      kind: 'biography',
      status: 'completed' as const,
      enqueued_at: '2026-05-04T11:50:00+00:00',
      started_at: '2026-05-04T11:50:01+00:00',
      completed_at: '2026-05-04T11:51:00+00:00',
      duration_ms: 60000,
      error: null,
    },
  ],
  completed_count: 5,
  failed_count: 1,
  avg_duration_ms: 60_000,
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'queue',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
  // ck3_chronicler-27ov.81 (audit L30): no listCharacters mock — names
  // now ride on the queue items + stats rows (character_name), so the
  // page no longer fetches the character list.
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('QueuePage', () => {
  it('renders summary counts and active item with name', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(BUSY);
    renderWithClient(<QueuePage />);
    // Erik (1234) appears in both active and recent-failed; Astrid (5678)
    // appears in queued and recent-completed. Wait for any Erik to land.
    await waitFor(() => expect(screen.getAllByText('Erik').length).toBeGreaterThan(0));
    expect(screen.getByText('×2')).toBeInTheDocument();
    expect(screen.getAllByText('Astrid').length).toBeGreaterThan(0);
  });

  it('renders failure error string when items have failed', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(BUSY);
    renderWithClient(<QueuePage />);
    await waitFor(() => screen.getByText('Recently failed'));
    expect(
      screen.getByText('Ollama timed out after 3 attempts'),
    ).toBeInTheDocument();
  });

  it('shows empty state when nothing queued', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(EMPTY);
    renderWithClient(<QueuePage />);
    // Wait for the empty-state copy itself to land — it only renders
    // post-load and is unique to the loaded state (the loading branch
    // says 'Loading…').
    await waitFor(() => screen.getByText(/Nothing queued/));
    expect(screen.getByText('No backlog.')).toBeInTheDocument();
  });

  it('falls back to character-id when the item has no resolved name', async () => {
    // ck3_chronicler-27ov.81 (audit L30): null character_name (BE couldn't
    // resolve it at enqueue) falls back to "character {id}".
    const noNames: NarrativeQueueResponse = {
      ...BUSY,
      active: BUSY.active.map((i) => ({ ...i, character_name: null })),
      recent: BUSY.recent.map((i) => ({ ...i, character_name: null })),
    };
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(noNames);
    renderWithClient(<QueuePage />);
    await waitFor(() => screen.getAllByText(/character 1234/));
    // Two rows reference char 1234 (active + recently-failed); both
    // exist when the name is unresolved.
    expect(screen.getAllByText(/character 1234/).length).toBeGreaterThanOrEqual(1);
  });

  // ck3_chronicler-fiv6 (was kdf): "Drain to GPU" button + drainDeferredNarratives
  // client wrapper were removed in 2026-05-08 alongside the local-tier
  // providers. Hosted Claude Code has no GPU contention with CK3, so
  // deferred-narrative mode no longer has a use case. Tests for the
  // button removed accordingly.

  it('renders per-character stats panel when stats are present (fjln)', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(EMPTY);
    vi.spyOn(client, 'getNarrativeCharacterStats').mockResolvedValue({
      rows: [
        {
          character_id: 1234,
          character_name: 'Erik',
          kind: 'biography',
          completed_count: 4,
          failed_count: 1,
          median_duration_ms: 12_500,
          last_success_at: '2026-05-07T11:00:00+00:00',
        },
        {
          character_id: 5678,
          character_name: 'Astrid',
          kind: 'biography',
          completed_count: 2,
          failed_count: 0,
          median_duration_ms: 800,
          last_success_at: '2026-05-07T11:30:00+00:00',
        },
      ],
    });
    renderWithClient(<QueuePage />);
    await waitFor(() => screen.getByText('Per-character stats'));
    // ck3_chronicler-27ov.81 (audit L30): names ride on the stats rows.
    expect(screen.getAllByText('Erik').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Astrid').length).toBeGreaterThan(0);
    // Median duration column renders via fmtDuration ('12.5s' for 12500ms).
    expect(screen.getByText('12.5s')).toBeInTheDocument();
    // Failed count = 1 highlighted on Erik's row.
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('hides per-character stats panel when no stats available (fjln)', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(EMPTY);
    vi.spyOn(client, 'getNarrativeCharacterStats').mockResolvedValue({
      rows: [],
    });
    renderWithClient(<QueuePage />);
    await waitFor(() => screen.getByText(/Nothing queued/));
    expect(screen.queryByText('Per-character stats')).toBeNull();
  });
});

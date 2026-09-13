// ck3_chronicler-eev: NarrativeQueueStrip — verify the strip is hidden
// when the queue is empty and renders the head/queued/eta line when work
// is in flight. Mocks the queue fetcher; doesn't open an EventSource
// (the JSDOM environment doesn't expose one) so the SSE-driven
// invalidation path is exercised by inspection rather than by a real
// frame. ck3_chronicler-27ov.81 (audit L30): names now ride on the queue
// item (character_name) — the strip no longer fetches the character list.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import * as client from '../api/client';
import { NarrativeQueueStrip } from './NarrativeQueueStrip';
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
  recent: [],
  completed_count: 3,
  failed_count: 0,
  avg_duration_ms: 60_000,
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('NarrativeQueueStrip', () => {
  it('renders nothing when queue is empty', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(EMPTY);
    const { container } = renderWithClient(
      <NarrativeQueueStrip campaignName="Wessex" />,
    );
    // Wait one tick so the empty fetch resolves; assert nothing rendered.
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('[data-testid="narrative-queue-strip"]')).toBeNull();
  });

  it('renders the active head, queued count, and eta when busy', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(BUSY);
    renderWithClient(<NarrativeQueueStrip campaignName="Wessex" />);

    const strip = await waitFor(() =>
      screen.getByTestId('narrative-queue-strip'),
    );
    expect(strip).toHaveTextContent('Generating biography of Erik');
    expect(strip).toHaveTextContent('1 more queued');
    // ETA: avg=60s × depth=2 = 120s → "~2m remaining"
    expect(strip).toHaveTextContent('~2m remaining');
  });

  it('shows failed count when failures have accumulated', async () => {
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue({
      ...BUSY,
      failed_count: 2,
    });
    renderWithClient(<NarrativeQueueStrip campaignName="Wessex" />);

    const strip = await waitFor(() =>
      screen.getByTestId('narrative-queue-strip'),
    );
    expect(strip).toHaveTextContent('2 failed');
  });

  it('falls back to character-id when the item has no resolved name', async () => {
    // ck3_chronicler-27ov.81 (audit L30): a null character_name (the BE
    // couldn't resolve it at enqueue) falls back to "character {id}".
    const noName: NarrativeQueueResponse = {
      ...BUSY,
      active: [{ ...BUSY.active[0]!, character_name: null }],
    };
    vi.spyOn(client, 'getNarrativeQueue').mockResolvedValue(noName);
    renderWithClient(<NarrativeQueueStrip campaignName="Wessex" />);

    const strip = await waitFor(() =>
      screen.getByTestId('narrative-queue-strip'),
    );
    expect(strip).toHaveTextContent('Generating biography of character 1234');
  });
});

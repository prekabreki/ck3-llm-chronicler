// ck3_chronicler-c5wq: queue page Cancel + Regenerate button behaviour.

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { QueuePage } from './QueuePage';
import * as client from '../api/client';

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof client>('../api/client');
  return {
    ...actual,
    getNarrativeQueue: vi.fn(),
    getNarrativeCharacterStats: vi.fn(),
    listCharacters: vi.fn(),
    cancelNarrativeQueueItem: vi.fn(),
    regenerateNarrativeQueueItem: vi.fn(),
  };
});

function withQueryClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  vi.mocked(client.listCharacters).mockResolvedValue([]);
  vi.mocked(client.getNarrativeCharacterStats).mockResolvedValue({ rows: [] });
});

describe('QueuePage mutations', () => {
  it('shows a Cancel button on each active row and calls the API on click', async () => {
    vi.mocked(client.getNarrativeQueue).mockResolvedValue({
      queued: [],
      active: [
        {
          item_id: 7,
          character_id: 1234,
          character_name: null,
          kind: 'biography',
          status: 'generating',
          enqueued_at: '2026-05-15T00:00:00Z',
          started_at: '2026-05-15T00:00:05Z',
          completed_at: null,
          duration_ms: null,
          error: null,
        },
      ],
      recent: [],
      completed_count: 0,
      failed_count: 0,
      avg_duration_ms: null,
    });
    vi.mocked(client.cancelNarrativeQueueItem).mockResolvedValue({
      cancelled: true,
      item_id: 7,
    });

    render(withQueryClient(<QueuePage />));
    await waitFor(() => {
      expect(screen.getByText('character 1234')).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(client.cancelNarrativeQueueItem).toHaveBeenCalledWith(7);
    });
  });

  it('shows a Regenerate button on each recent failed row and calls the API on click', async () => {
    vi.mocked(client.getNarrativeQueue).mockResolvedValue({
      queued: [],
      active: [],
      recent: [
        {
          item_id: 9,
          character_id: 5678,
          character_name: null,
          kind: 'biography',
          status: 'failed',
          enqueued_at: '2026-05-15T00:00:00Z',
          started_at: '2026-05-15T00:00:05Z',
          completed_at: '2026-05-15T00:00:30Z',
          duration_ms: 25000,
          error: 'rate-limited',
        },
      ],
      completed_count: 0,
      failed_count: 1,
      avg_duration_ms: null,
    });
    vi.mocked(client.regenerateNarrativeQueueItem).mockResolvedValue({
      character_id: 5678,
      kind: 'biography',
      item_id: 42,
    });

    render(withQueryClient(<QueuePage />));
    await waitFor(() => {
      expect(screen.getByText('character 5678')).toBeInTheDocument();
    });

    const regen = screen.getByRole('button', { name: /regenerate/i });
    fireEvent.click(regen);

    await waitFor(() => {
      expect(client.regenerateNarrativeQueueItem).toHaveBeenCalledWith(9);
    });
  });
});

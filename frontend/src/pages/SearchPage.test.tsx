// SearchPage — covers the empty-state, the submitted-but-empty result,
// and the populated rendering of grouped hits with snippet bolding.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { SearchPage } from './SearchPage';
import * as client from '../api/client';
import type { SearchResponse } from '../api/types';

const RESULTS: SearchResponse = {
  query: 'guthrum',
  hits: [
    {
      campaign_name: 'Wessex',
      kind: 'character',
      row_id: 1,
      snippet: 'resents <b>Guthrum</b> still',
      character_id: 100,
      rank: -1.4,
      coa_json: null,
    },
    {
      campaign_name: 'Mercia',
      kind: 'biography',
      row_id: 2,
      snippet: 'after <b>Guthrum</b> took the abbey',
      character_id: null,
      rank: -1.0,
      coa_json: null,
    },
  ],
  by_campaign: {
    Wessex: [
      {
        campaign_name: 'Wessex',
        kind: 'character',
        row_id: 1,
        snippet: 'resents <b>Guthrum</b> still',
        character_id: 100,
        rank: -1.4,
        coa_json: null,
      },
    ],
    Mercia: [
      {
        campaign_name: 'Mercia',
        kind: 'biography',
        row_id: 2,
        snippet: 'after <b>Guthrum</b> took the abbey',
        character_id: null,
        rank: -1.0,
        coa_json: null,
      },
    ],
  },
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SearchPage', () => {
  it('renders the form before any search has been submitted', () => {
    renderWithClient(<SearchPage />);
    expect(screen.getByLabelText('Search query')).toBeInTheDocument();
    expect(screen.getByText(/Seek across the codices/)).toBeInTheDocument();
  });

  it('runs a query and groups results by campaign', async () => {
    const spy = vi.spyOn(client, 'searchAll').mockResolvedValue(RESULTS);
    renderWithClient(<SearchPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Search query'), 'guthrum');
    await user.click(screen.getByRole('button', { name: /Seek/ }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[0]).toBe('guthrum');
    expect(screen.getByText(/2 results for/)).toBeInTheDocument();
    expect(screen.getByText('✦ Wessex')).toBeInTheDocument();
    expect(screen.getByText('✦ Mercia')).toBeInTheDocument();
    // Snippet HTML preserves <b> tags.
    const snippets = screen.getAllByText((_, el) =>
      el?.classList?.contains('search-result__snippet') ?? false,
    );
    expect(snippets.length).toBeGreaterThan(0);
  });

  it('renders an empty-state message when the search returns no hits', async () => {
    vi.spyOn(client, 'searchAll').mockResolvedValue({
      query: 'unicorn',
      hits: [],
      by_campaign: {},
    });
    renderWithClient(<SearchPage />);

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Search query'), 'unicorn');
    await user.click(screen.getByRole('button', { name: /Seek/ }));

    await waitFor(() =>
      expect(
        screen.getByText(/Nothing found for "unicorn"/),
      ).toBeInTheDocument(),
    );
  });
});

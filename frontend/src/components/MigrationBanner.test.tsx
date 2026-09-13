// Tests for MigrationBanner (ck3_chronicler-72a).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { MigrationBanner } from './MigrationBanner';
import * as migrateClient from '../api/migrateClient';

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('MigrationBanner', () => {
  it('renders nothing when nothing needs migration', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    const { container } = renderWithClient(<MigrationBanner />);
    // Wait one tick for the query to resolve.
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it('renders the banner when N campaigns need migration', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [
        { campaign_id: 'a', name: 'Wessex', current_head: null, target_head: 'h1' },
        { campaign_id: 'b', name: 'Mercia', current_head: 'old', target_head: 'h1' },
        { campaign_id: 'c', name: 'Anglia', current_head: 'old', target_head: 'h1' },
      ],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    renderWithClient(<MigrationBanner />);
    await waitFor(() =>
      expect(screen.getByText(/3 campaigns need migration/)).toBeInTheDocument(),
    );
    expect(
      screen.getByRole('button', { name: /Open Settings/ }),
    ).toBeInTheDocument();
  });

  it('mentions the registry separately when only the registry needs migration', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [],
      registry_needs_migration: true,
      registry_missing_columns: ['campaigns.bookmark_date'],
    });
    renderWithClient(<MigrationBanner />);
    await waitFor(() =>
      expect(screen.getByText(/registry needs migration/i)).toBeInTheDocument(),
    );
  });
});

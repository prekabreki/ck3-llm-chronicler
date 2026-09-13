// Tests for MigrationPanel (ck3_chronicler-72a).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { MigrationPanel } from './MigrationPanel';
import * as migrateClient from '../api/migrateClient';

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('MigrationPanel', () => {
  it('shows the clean state when nothing needs migration and no backups', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    vi.spyOn(migrateClient, 'listMigrationBackups').mockResolvedValue([]);
    renderWithClient(<MigrationPanel />);
    await waitFor(() =>
      expect(screen.getByText(/All schemas current/)).toBeInTheDocument(),
    );
  });

  it('lists pending campaigns + Backup & Migrate button when work is needed', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [
        { campaign_id: 'a', name: 'Wessex', current_head: null, target_head: 'h1' },
      ],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    vi.spyOn(migrateClient, 'listMigrationBackups').mockResolvedValue([]);
    renderWithClient(<MigrationPanel />);
    await waitFor(() => screen.getByText('Wessex'));
    expect(
      screen.getByRole('button', { name: /Backup & Migrate/ }),
    ).toBeInTheDocument();
  });

  it('clicking Backup & Migrate triggers /run + renders results', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [
        { campaign_id: 'a', name: 'Wessex', current_head: null, target_head: 'h1' },
      ],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    vi.spyOn(migrateClient, 'listMigrationBackups').mockResolvedValue([]);
    const runSpy = vi.spyOn(migrateClient, 'runMigration').mockResolvedValue({
      success: true,
      backup_dir: '/tmp/backup/2026-05-06T14-22-31',
      results: [{ id: 'a', ok: true, error: null }],
    });
    renderWithClient(<MigrationPanel />);
    await waitFor(() => screen.getByRole('button', { name: /Backup & Migrate/ }));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Backup & Migrate/ }));
    await waitFor(() => expect(runSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/Backup at \/tmp\/backup/)).toBeInTheDocument(),
    );
  });

  it('Restore dropdown calls /restore with the chosen path', async () => {
    vi.spyOn(migrateClient, 'getMigrationStatus').mockResolvedValue({
      needs_migration: [],
      registry_needs_migration: false,
      registry_missing_columns: [],
    });
    vi.spyOn(migrateClient, 'listMigrationBackups').mockResolvedValue([
      {
        timestamp: '2026-05-06T14-22-31',
        path: '/tmp/backups/2026-05-06T14-22-31',
        campaign_count: 2,
      },
    ]);
    const restoreSpy = vi
      .spyOn(migrateClient, 'restoreFromBackup')
      .mockResolvedValue({ restored: 3 });
    renderWithClient(<MigrationPanel />);
    await waitFor(() => screen.getByText(/All schemas current/));
    const user = userEvent.setup();
    const dropdown = screen.getByLabelText(/Restore from backup/i);
    await user.selectOptions(dropdown, '/tmp/backups/2026-05-06T14-22-31');
    await user.click(screen.getByRole('button', { name: /Restore/ }));
    await waitFor(() =>
      expect(restoreSpy).toHaveBeenCalledWith('/tmp/backups/2026-05-06T14-22-31'),
    );
  });
});

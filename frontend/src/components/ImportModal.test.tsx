// Tests for ImportModal save-picker (ck3_chronicler-mb6q).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ImportModal } from './ImportModal';
import * as client from '../api/client';

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

describe('ImportModal save-picker', () => {
  it('renders recent saves and submits when a row is clicked', async () => {
    vi.spyOn(client, 'listSaves').mockResolvedValue({
      save_dir: 'C:\\Users\\you\\saves',
      save_dir_exists: true,
      save_dir_source: 'override',
      saves: [
        {
          filename: 'autosave.ck3',
          abs_path: 'C:\\Users\\you\\saves\\autosave.ck3',
          size_bytes: 76 * 1024 * 1024,
          mtime_iso: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
        },
        {
          filename: 'manual_1066.ck3',
          abs_path: 'C:\\Users\\you\\saves\\manual_1066.ck3',
          size_bytes: 80 * 1024 * 1024,
          mtime_iso: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
        },
      ],
    });
    const startSpy = vi
      .spyOn(client, 'startImportSave')
      .mockResolvedValue({
        import_id: 'imp-1',
        campaign_name: 'demo',
        sse_url: '/api/sse/import/imp-1',
      });

    renderWithClient(
      <ImportModal campaignName="demo" open={true} onClose={vi.fn()} />,
    );

    // Wait for the listing to land.
    await waitFor(() =>
      expect(screen.getByText('autosave.ck3')).toBeInTheDocument(),
    );
    expect(screen.getByText('manual_1066.ck3')).toBeInTheDocument();

    // Click the first row → starts the import.
    const user = userEvent.setup();
    await user.click(screen.getByText('autosave.ck3'));

    await waitFor(() =>
      expect(startSpy).toHaveBeenCalledWith(
        'demo',
        'C:\\Users\\you\\saves\\autosave.ck3',
      ),
    );
  });

  it('shows the empty-state hint when save_dir is empty', async () => {
    vi.spyOn(client, 'listSaves').mockResolvedValue({
      save_dir: 'C:\\Users\\you\\saves',
      save_dir_exists: true,
      save_dir_source: 'override',
      saves: [],
    });
    renderWithClient(
      <ImportModal campaignName="demo" open={true} onClose={vi.fn()} />,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/No \.ck3 files found in/i),
      ).toBeInTheDocument(),
    );
  });

  it('shows the missing-dir hint when save_dir_exists is false', async () => {
    vi.spyOn(client, 'listSaves').mockResolvedValue({
      save_dir: 'C:\\Users\\you\\saves',
      save_dir_exists: false,
      save_dir_source: 'override',
      saves: [],
    });
    renderWithClient(
      <ImportModal campaignName="demo" open={true} onClose={vi.fn()} />,
    );
    await waitFor(() =>
      expect(
        screen.getByText(/configured save folder doesn't exist/i),
      ).toBeInTheDocument(),
    );
  });
});

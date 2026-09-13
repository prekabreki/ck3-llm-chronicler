// Tests for FirstRunWizard (kze6 / f9w.3).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { FirstRunWizard } from './FirstRunWizard';
import * as client from '../api/client';
import type { NarrativeBackendResponse } from '../api/types';

const BACKEND_DEFAULT: NarrativeBackendResponse = {
  backend: 'claude-code',
  source: 'default',
  valid_backends: ['anthropic', 'claude-code', 'openai-compatible'],
  valid_presets: ['deepseek', 'lmstudio', 'ollama', 'openai', 'openrouter'],
  presets: [],
  openai_preset: null,
  openai_base_url: null,
  openai_model: null,
  openai_key: { present: false, source: null },
  anthropic_key: { present: false, source: null },
  usable: true,
  error: null,
};

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

describe('FirstRunWizard', () => {
  it('does not render when needs_wizard is false', async () => {
    vi.spyOn(client, 'getFirstRunStatus').mockResolvedValue({
      needs_wizard: false,
      library_empty: false,
      save_dir_configured: true,
      ck3_install_dir_configured: true,
      heraldry_extracted: true,
      prose_repo_ready: true,
      wizard_dismissed_at: null,
    });
    renderWithClient(<FirstRunWizard />);
    // Wait one tick for the query to resolve and confirm no dialog appears.
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('auto-opens when needs_wizard is true and walks through the strip', async () => {
    vi.spyOn(client, 'getFirstRunStatus').mockResolvedValue({
      needs_wizard: true,
      library_empty: true,
      save_dir_configured: false,
      ck3_install_dir_configured: false,
      heraldry_extracted: false,
      prose_repo_ready: false,
      wizard_dismissed_at: null,
    });
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      save_dir: { resolved: '~/saves', source: 'default', exists: true, override: null },
      ck3_install_dir: { resolved: '', source: 'probe', exists: false, override: null },
      // Issue #51: the wizard doesn't use the archive row, but the wire
      // type carries it, so the fixture has to be complete.
      archive_dir: { resolved: '~/archived', source: 'default', exists: true, override: null },
      archive_git_root: null,
    });
    vi.spyOn(client, 'getHeraldryStatus').mockResolvedValue({
      extracted: false,
      last_extraction_at: null,
      palette_colors: 0,
      patterns_count: 0,
      emblems_count: 0,
      ck3_install_dir_resolved: null,
      ck3_install_dir_exists: false,
      is_stale: false,
    });
    vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue({
      path: '/home/u/.local/share/chronicler/prose',
      source: 'default',
      override: null,
      exists: false,
      git_initialized: false,
      claude_md_present: false,
    });
    vi.spyOn(client, 'getNarrativeBackend').mockResolvedValue(BACKEND_DEFAULT);

    renderWithClient(<FirstRunWizard />);

    await waitFor(() =>
      expect(screen.getByRole('dialog')).toBeInTheDocument(),
    );
    expect(screen.getByText('Welcome to chronicler')).toBeInTheDocument();
    // Step 1 visible — save dir.
    expect(screen.getByText(/CK3 writes its save files/)).toBeInTheDocument();

    // Advance through Next x3 → Done.
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/Where CK3 is installed/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/coat-of-arms textures/)).toBeInTheDocument();
    // Issue #23: the chronicle step. A missing prose dir offers Initialize
    // rather than leaving the public user with nothing to write into.
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(
      await screen.findByTestId('wizard-prose-init'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/setup done/)).toBeInTheDocument();
  });

  it('"I\'ll do this myself" calls dismiss and closes the wizard', async () => {
    vi.spyOn(client, 'getFirstRunStatus').mockResolvedValue({
      needs_wizard: true,
      library_empty: true,
      save_dir_configured: false,
      ck3_install_dir_configured: false,
      heraldry_extracted: false,
      prose_repo_ready: false,
      wizard_dismissed_at: null,
    });
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      save_dir: { resolved: '', source: 'default', exists: false, override: null },
      ck3_install_dir: { resolved: '', source: 'probe', exists: false, override: null },
      // Issue #51: the wizard doesn't use the archive row, but the wire
      // type carries it, so the fixture has to be complete.
      archive_dir: { resolved: '~/archived', source: 'default', exists: true, override: null },
      archive_git_root: null,
    });
    const dismissSpy = vi
      .spyOn(client, 'dismissFirstRunWizard')
      .mockResolvedValue({ wizard_dismissed_at: '2026-05-06T22:00:00+00:00' });

    renderWithClient(<FirstRunWizard />);

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /I'll do this myself/i }));
    await waitFor(() => expect(dismissSpy).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

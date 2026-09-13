// Tests for RegenerateBiographyButton (ck3_chronicler-7gw).
// ck3_chronicler-cs1o: cost-estimate panel revived with dual transports
// (Direct API real-money vs Claude Code pool credit).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { RegenerateBiographyButton } from './RegenerateBiographyButton';
import * as client from '../api/client';
import type { BiographyCostEstimate } from '../api/client';

// Free local hand — no transport spend at all.
const FREE_ESTIMATE: BiographyCostEstimate = {
  estimated_input_tokens: 4200,
  estimated_output_tokens: 1100,
  estimated_total_tokens: 5300,
  est_usd: 0,
  kind: 'biography',
  would_route_to: 'ollama:qwen3:14b',
  model: 'qwen3:14b',
  method: 'from_prior_generation',
  pool_billed: false,
};

// Direct API — real money, carmine warning.
const PAID_ESTIMATE: BiographyCostEstimate = {
  estimated_input_tokens: 4200,
  estimated_output_tokens: 1100,
  estimated_total_tokens: 5300,
  est_usd: 0.0285,
  kind: 'biography_woven',
  would_route_to: 'anthropic:claude-sonnet-4-6',
  model: 'claude-sonnet-4-6',
  method: 'from_prior_generation',
  pool_billed: false,
};

// Claude Code — draws monthly programmatic pool credit, not a card.
const POOL_ESTIMATE: BiographyCostEstimate = {
  estimated_input_tokens: 4200,
  estimated_output_tokens: 1100,
  estimated_total_tokens: 5300,
  est_usd: 0.0285,
  kind: 'biography_woven',
  would_route_to: 'claude-code:claude-opus-4-7',
  model: 'claude-opus-4-7',
  method: 'from_prior_generation',
  pool_billed: true,
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

beforeEach(() => {
  // Default the cost-estimate fetch to "free local" so the original 7gw
  // tests don't have to know about the cost panel.
  vi.spyOn(client, 'getBiographyCostEstimate').mockResolvedValue(FREE_ESTIMATE);
});

describe('RegenerateBiographyButton', () => {
  it('uses "Regenerate" copy when a biography already exists', () => {
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );
    expect(screen.getByRole('button', { name: 'Regenerate biography' })).toBeInTheDocument();
  });

  it('uses "Generate" copy when no biography exists yet', () => {
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Generate biography' })).toBeInTheDocument();
  });

  it('opens a confirmation dialog and submits via the API on confirm', async () => {
    const spy = vi
      .spyOn(client, 'regenerateBiography')
      .mockResolvedValue({ character_id: 100, item_id: 7 });
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Regenerate biography' }));
    // Modal opens with the character's name woven into the prompt.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/Re-inscribe the vita of Alfred/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(spy).toHaveBeenCalledWith('Wessex', 100);
  });

  it('cancel closes the modal without firing the mutation', async () => {
    const spy = vi.spyOn(client, 'regenerateBiography');
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Regenerate biography' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it('falls back to "character N" when the name is missing', async () => {
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={42}
        characterName={null}
        hasExistingBiography={false}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Generate biography' }));
    expect(screen.getByText(/Inscribe a vita for character 42/)).toBeInTheDocument();
  });

  // ck3_chronicler-7bi5 (revived by cs1o): cost estimate panel.
  it('renders the cost estimate inside the modal once it loads', async () => {
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: 'Regenerate biography' }),
    );
    // Tokens render as compact mono pair (4.2k in · 1.1k out).
    await waitFor(() =>
      expect(screen.getByText(/4\.2k in/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/1\.1k out/)).toBeInTheDocument();
    expect(screen.getByText('Free · local hand')).toBeInTheDocument();
    // Local transport reads "Local hand", with the model after a separator.
    expect(screen.getByText(/Local hand/)).toBeInTheDocument();
    expect(screen.getByText(/qwen3:14b/)).toBeInTheDocument();
  });

  it('flags the paid Direct API route with a carmine USD estimate', async () => {
    vi.spyOn(client, 'getBiographyCostEstimate').mockResolvedValue(PAID_ESTIMATE);
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: 'Regenerate biography' }),
    );
    const cost = await screen.findByText('~$0.03');
    expect(cost).toBeInTheDocument();
    // Real money ⇒ carmine cost-warning styling.
    expect(cost.closest('.cost-estimate')).toHaveClass('cost-estimate--paid');
    expect(screen.getByText(/Direct API/)).toBeInTheDocument();
    expect(screen.getByText(/claude-sonnet-4-6/)).toBeInTheDocument();
  });

  it('shows pool-credit wording (not carmine) for a Claude Code route', async () => {
    vi.spyOn(client, 'getBiographyCostEstimate').mockResolvedValue(POOL_ESTIMATE);
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: 'Regenerate biography' }),
    );
    const cost = await screen.findByText(/of monthly pool credit/);
    expect(cost).toBeInTheDocument();
    expect(cost).toHaveTextContent('~$0.03 of monthly pool credit');
    // Pool credit is informational — NOT the carmine real-money warning.
    expect(cost.closest('.cost-estimate')).not.toHaveClass('cost-estimate--paid');
    expect(screen.getByText(/Claude Code/)).toBeInTheDocument();
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
  });

  it('shows the first-time-generation hint when method is estimate', async () => {
    vi.spyOn(client, 'getBiographyCostEstimate').mockResolvedValue({
      ...FREE_ESTIMATE,
      method: 'estimate',
    });
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography={false}
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: 'Generate biography' }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/First-time generation/),
      ).toBeInTheDocument(),
    );
  });

  it('does not fetch the estimate until the modal is opened', async () => {
    const spy = vi
      .spyOn(client, 'getBiographyCostEstimate')
      .mockResolvedValue(FREE_ESTIMATE);
    renderWithClient(
      <RegenerateBiographyButton
        campaignName="Wessex"
        ck3Id={100}
        characterName="Alfred"
        hasExistingBiography
      />,
    );
    // Modal closed by default ⇒ no network call.
    expect(spy).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: 'Regenerate biography' }),
    );
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  });
});

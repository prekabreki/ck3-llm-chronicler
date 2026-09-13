// Tests for NoCampaignSelected — the shared no-campaign gate placeholder.
// Audit M-F1 / ck3_chronicler-27ov.56: nine campaign-scoped pages each
// hand-rolled this ~18-line block; the gate now lives in App's view
// switch and renders this once.

import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { NoCampaignSelected } from './NoCampaignSelected';
import { useAppStore } from '../store/appStore';

afterEach(cleanup);

describe('NoCampaignSelected', () => {
  it('renders the prompt with a Library link', () => {
    render(<NoCampaignSelected />);
    expect(screen.getByText(/No campaign selected/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Library' })).toBeInTheDocument();
  });

  it('the Library link switches the view to library', async () => {
    useAppStore.setState({ view: 'codex', activeCampaign: null });
    render(<NoCampaignSelected />);
    await userEvent.click(screen.getByRole('button', { name: 'Library' }));
    expect(useAppStore.getState().view).toBe('library');
  });
});

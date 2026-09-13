// AppShell — vysp.5 reskin: nine primary tabs, right-cell pill +
// tail-live pip + tools cluster. Tests cover nav routing, the
// tools-cluster routes (Settings / Closing / theme), and conditional
// rendering of the campaign pill + in-game date + tail pip.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AppShell } from './AppShell';
import { useAppStore } from '../store/appStore';
import * as migrateClient from '../api/migrateClient';

function renderWithClient(node: React.ReactNode): ReturnType<typeof render> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAppStore.setState({
    view: 'library',
    theme: 'light',
    activeCampaign: null,
    selectedCharacterId: null,
  });
});

describe('AppShell', () => {
  it('renders all nine primary nav tabs in spec order', () => {
    // ck3_chronicler-8e0w: campaign-scoped tabs only render when a
    // campaign is active; pass campaignName here so the full nine-tab
    // spec layout is exercised. The hidden-without-campaign behaviour
    // has its own dedicated test below.
    renderWithClient(<AppShell campaignName="Wessex" />);
    const tabs = [
      'Library',
      'Codex',
      'Chronicle',
      'Dynasty',
      'Hall',
      'Lineage',
      'Tracked',
      'Search',
      'Save tail',
    ];
    for (const label of tabs) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
  });

  it('marks the active nav tab via aria-current', () => {
    useAppStore.setState({ view: 'codex' });
    renderWithClient(<AppShell campaignName="Wessex" />);
    expect(screen.getByRole('button', { name: 'Codex' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByRole('button', { name: 'Library' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('clicking a nav tab updates the store view', async () => {
    const user = userEvent.setup();
    // ck3_chronicler-8e0w: 'Tracked' is a campaign-scoped tab, so pass
    // a campaignName for it to render.
    renderWithClient(<AppShell campaignName="Wessex" />);
    await user.click(screen.getByRole('button', { name: 'Tracked' }));
    expect(useAppStore.getState().view).toBe('tracked');
  });

  it('Dynasty + Hall tabs route to their placeholder views', async () => {
    const user = userEvent.setup();
    // ck3_chronicler-8e0w: Dynasty needs a campaign; Hall is global.
    renderWithClient(<AppShell campaignName="Wessex" />);
    await user.click(screen.getByRole('button', { name: 'Dynasty' }));
    expect(useAppStore.getState().view).toBe('dynasty');
    await user.click(screen.getByRole('button', { name: 'Hall' }));
    expect(useAppStore.getState().view).toBe('hall');
  });

  it('hides campaign-scoped nav tabs when no campaign is active (ck3_chronicler-8e0w)', () => {
    renderWithClient(<AppShell />);
    // Global / cross-campaign tabs stay visible.
    expect(screen.getByRole('button', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hall' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Search' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save tail' })).toBeInTheDocument();
    // Campaign-scoped tabs hidden.
    expect(screen.queryByRole('button', { name: 'Codex' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Chronicle' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dynasty' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Lineage' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tracked' })).not.toBeInTheDocument();
  });

  it('campaign-scoped nav tabs appear when a campaign is active', () => {
    renderWithClient(<AppShell campaignName="Wessex" />);
    expect(screen.getByRole('button', { name: 'Codex' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chronicle' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dynasty' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lineage' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tracked' })).toBeInTheDocument();
  });

  it('tools cluster routes to closing / settings + flips theme', async () => {
    const user = userEvent.setup();
    renderWithClient(<AppShell />);
    await user.click(screen.getByLabelText('Closing ceremony'));
    expect(useAppStore.getState().view).toBe('closing');
    await user.click(screen.getByLabelText('Narrative providers & settings'));
    expect(useAppStore.getState().view).toBe('settings');
    expect(useAppStore.getState().theme).toBe('light');
    await user.click(screen.getByLabelText('Toggle theme'));
    expect(useAppStore.getState().theme).toBe('dark');
  });

  it('renders the campaign pill only when one is active', () => {
    const { rerender } = renderWithClient(<AppShell />);
    expect(screen.queryByText('Wessex')).not.toBeInTheDocument();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <AppShell campaignName="Wessex" campaignRange="last seen 1066-09-15" />
      </QueryClientProvider>,
    );
    // Without a player name the campaign name occupies the proud line.
    expect(screen.getByText('Wessex')).toBeInTheDocument();
    expect(screen.getByText('last seen 1066-09-15')).toBeInTheDocument();
  });

  it('hides pill + campaign-scoped nav when the active campaign is archived (ck3_chronicler-8e0w)', () => {
    // User clicked into a sealed campaign from the Library; the
    // overview / closing pages should still load, but the header
    // chrome reflects that the campaign is no longer in play.
    renderWithClient(
      <AppShell
        campaignName="Wessex"
        campaignArchived={true}
        currentPlayerName="Almodis"
      />,
    );
    expect(screen.queryByText('Almodis')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Codex' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tracked' })).not.toBeInTheDocument();
    // Global tabs stay.
    expect(screen.getByRole('button', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hall' })).toBeInTheDocument();
  });

  it('uses the campaign name as the pill subline when a player is set', () => {
    renderWithClient(
      <AppShell
        campaignName="aragon-1066"
        currentPlayerName="Almodis"
      />,
    );
    expect(screen.getByText('Almodis')).toBeInTheDocument();
    expect(screen.getByText('aragon-1066')).toBeInTheDocument();
  });

  it('shows the in-game date when provided', () => {
    renderWithClient(
      <AppShell
        campaignName="Aragón"
        currentInGameDate="1080.4.16"
      />,
    );
    expect(screen.getByText('1080.4.16')).toBeInTheDocument();
  });

  it('tail pip flips between live and idle text', () => {
    const { rerender } = renderWithClient(
      <AppShell campaignName="Aragón" tailLive={false} />,
    );
    expect(screen.getByText(/Tail idle/i)).toBeInTheDocument();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <AppShell
          campaignName="Aragón"
          tailLive
          tailStatus={{ level: 'live', label: 'Live · safe to play', detail: null, tone: 'green' }}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByText(/Live · safe to play/i)).toBeInTheDocument();
  });

  it('clicking the tail pip routes to Save tail (with a campaign)', async () => {
    const user = userEvent.setup();
    renderWithClient(<AppShell campaignName="Aragón" tailLive={false} />);
    await user.click(screen.getByRole('button', { name: /Tail idle/i }));
    expect(useAppStore.getState().view).toBe('ingest');
  });

  it('clicking the tail pip routes to Save tail (no campaign too)', async () => {
    useAppStore.setState({ view: 'library' });
    const user = userEvent.setup();
    renderWithClient(<AppShell />);
    await user.click(screen.getByRole('button', { name: /Tail idle/i }));
    expect(useAppStore.getState().view).toBe('ingest');
  });

  it('ck3_chronicler-p6qo: clicking the pip while live confirms then halts ingest', async () => {
    const haltSpy = vi
      .spyOn(migrateClient, 'haltSaveTail')
      .mockResolvedValue({ halted: true });
    const confirmCalls: string[] = [];
    const originalConfirm = window.confirm;
    window.confirm = (msg?: string) => {
      confirmCalls.push(msg ?? '');
      return true;
    };

    useAppStore.setState({ view: 'library' });
    const user = userEvent.setup();
    renderWithClient(
      <AppShell
        campaignName="Aragón"
        tailLive
        tailStatus={{ level: 'live', label: 'Live · safe to play', detail: null, tone: 'green' }}
      />,
    );

    try {
      await user.click(screen.getByRole('button', { name: /Live · safe to play/i }));
      expect(confirmCalls[0] ?? '').toMatch(/Stop the save-tail ingest/);
      await waitFor(() => expect(haltSpy).toHaveBeenCalledTimes(1));
      // Should NOT navigate to Ingest — the click is a halt action.
      expect(useAppStore.getState().view).toBe('library');
    } finally {
      window.confirm = originalConfirm;
    }
  });

  it('ck3_chronicler-p6qo: declining the confirm dialog keeps ingest running', async () => {
    const haltSpy = vi
      .spyOn(migrateClient, 'haltSaveTail')
      .mockResolvedValue({ halted: true });
    const originalConfirm = window.confirm;
    window.confirm = () => false;

    try {
      const user = userEvent.setup();
      renderWithClient(
        <AppShell
          campaignName="Aragón"
          tailLive
          tailStatus={{ level: 'live', label: 'Live · safe to play', detail: null, tone: 'green' }}
        />,
      );
      await user.click(screen.getByRole('button', { name: /Live · safe to play/i }));
      expect(haltSpy).not.toHaveBeenCalled();
      // No navigation either — the click was a no-op once the user said no.
      expect(useAppStore.getState().view).toBe('library');
    } finally {
      window.confirm = originalConfirm;
    }
  });

  it('player name occupies the proud pill line, campaign name the subline', () => {
    renderWithClient(
      <AppShell
        campaignName="Aragón"
        currentPlayerName="Almodis de Barcelona"
      />,
    );
    const playerEl = screen.getByText('Almodis de Barcelona');
    const campaignEl = screen.getByText('Aragón');
    expect(playerEl).toHaveClass('appbar__pill-name');
    expect(campaignEl).toHaveClass('appbar__pill-id');
  });
});

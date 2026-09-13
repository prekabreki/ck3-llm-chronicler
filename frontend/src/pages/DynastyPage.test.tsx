// DynastyPage — ck3_chronicler-thpz.1.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { DynastyPage } from './DynastyPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { DynastyResponse } from '../api/client';

const PALETTE = {
  red: [200, 30, 30] as [number, number, number],
  blue: [30, 60, 160] as [number, number, number],
};

const DYNASTY: DynastyResponse = {
  dynasty_name: 'Wessex',
  member_count: 3,
  chronicled_count: 2,
  founding_date: '978.1.1',
  founding_paragraph:
    'And so the Wessex line endured through the long winter of 1066.',
  founder: {
    ck3_id: 99,
    first_name: 'Egbert',
    dynasty_name: 'Wessex',
    nickname: null,
    birth_date: '978.1.1',
    death_date: '1010.5.4',
    is_player: false,
    has_biography: true,
    coa_json: { pattern: 'egbert.dds' },
  },
  current_head: {
    ck3_id: 100,
    first_name: 'Alfred',
    dynasty_name: 'Wessex',
    nickname: 'the Great',
    birth_date: '1010.1.1',
    death_date: null,
    is_player: true,
    has_biography: false,
    coa_json: { pattern: 'alfred.dds' },
  },
  members: [
    {
      ck3_id: 99,
      first_name: 'Egbert',
      dynasty_name: 'Wessex',
      nickname: null,
      birth_date: '978.1.1',
      death_date: '1010.5.4',
      is_player: false,
      has_biography: true,
      coa_json: { pattern: 'egbert.dds' },
    },
    {
      ck3_id: 100,
      first_name: 'Alfred',
      dynasty_name: 'Wessex',
      nickname: 'the Great',
      birth_date: '1010.1.1',
      death_date: null,
      is_player: true,
      has_biography: false,
      coa_json: { pattern: 'alfred.dds' },
    },
    {
      ck3_id: 101,
      first_name: 'Edward',
      dynasty_name: 'Wessex',
      nickname: null,
      birth_date: '1040.6.6',
      death_date: '1085.2.3',
      is_player: false,
      has_biography: true,
      coa_json: { pattern: 'edward.dds' },
    },
  ],
  vita_roll: [
    {
      ck3_id: 101,
      first_name: 'Edward',
      nickname: null,
      death_date: '1085.2.3',
      biography_excerpt:
        'Edward inherited the realm in 1010 and held it for 75 years.',
      generated_at: '2026-04-20T10:00:00+00:00',
    },
    {
      ck3_id: 99,
      first_name: 'Egbert',
      nickname: null,
      death_date: '1010.5.4',
      biography_excerpt:
        'Egbert was crowned in 962 and laid the foundations of the Wessex dynasty.',
      generated_at: '2026-04-15T10:00:00+00:00',
    },
  ],
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'dynasty',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
  vi.spyOn(client, 'getDynasty').mockResolvedValue(DYNASTY);
  vi.spyOn(client, 'getHeraldryPalette').mockResolvedValue(PALETTE);
  vi.spyOn(client, 'listTracked').mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('DynastyPage', () => {
  it('renders the hero with dynasty name + founding line + paragraph', async () => {
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('WESSEX'));
    expect(screen.getByText('WESSEX')).toBeInTheDocument();
    expect(screen.getByText(/Founded 978\.1\.1/)).toBeInTheDocument();
    // 3 members · 2 chronicled
    expect(
      screen.getByText(/3 members · 2 chronicled souls/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Wessex line endured through the long winter/),
    ).toBeInTheDocument();
  });

  it('renders one lineage row per member with role + bio pip', async () => {
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    // 'Egbert' appears twice (lineage + vita roll) — wait on Alfred
    // who only renders in the lineage strip.
    await waitFor(() => screen.getByText('Alfred'));
    expect(screen.getAllByText('Egbert').length).toBe(2);
    expect(screen.getByText('Alfred')).toBeInTheDocument();
    // Alfred is the current head (is_player=true)
    expect(screen.getByText('Current head')).toBeInTheDocument();
    // Egbert + Edward both have biographies — the bio pip appears
    // exactly twice in the lineage strip.
    expect(screen.getAllByText('✓ vita').length).toBe(2);
  });

  it('flags tracked members with carmine accent class', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([
      {
        character_id: 100,
        first_name: 'Alfred',
        nickname: 'the Great',
        role: 'player',
        added_at: '2026-04-15T08:00:00+00:00',
        biography_count: 0,
        monthly_token_spend: 0,
        paused_at: null,
        bumped_at: null,
        coa_json: null,
      },
    ]);
    const { container } = renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));
    const trackedRow = container.querySelector('.dynasty-row--tracked');
    expect(trackedRow).not.toBeNull();
    expect(trackedRow?.textContent).toContain('Alfred');
  });

  it('vita roll surfaces the death date and biography excerpt', async () => {
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/Edward inherited the realm/));
    expect(
      screen.getByText(/Edward inherited the realm in 1010/),
    ).toBeInTheDocument();
    expect(screen.getByText('Died 1085.2.3')).toBeInTheDocument();
  });

  it('clicking a lineage Open folio button routes to the chronicle', async () => {
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));
    const user = userEvent.setup();
    // First Open folio button is in Egbert's lineage row (founder, sorted
    // first by birth date).
    const openButtons = screen.getAllByRole('button', { name: /Open folio/ });
    await user.click(openButtons[0]!);
    const state = useAppStore.getState();
    expect(state.selectedCharacterId).toBe(99);
    expect(state.view).toBe('chronicle');
  });

  it('shows the no-dynasty empty state on 404', async () => {
    vi.spyOn(client, 'getDynasty').mockRejectedValue(
      Object.assign(new Error('No dynasty resolved'), { status: 404 }),
    );
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() =>
      expect(screen.getByText('No dynasty resolved')).toBeInTheDocument(),
    );
  });

  it('shows the no-vita branch when there are no biographies yet', async () => {
    vi.spyOn(client, 'getDynasty').mockResolvedValue({
      ...DYNASTY,
      vita_roll: [],
    });
    renderWithClient(<DynastyPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Vita roll'));
    expect(
      screen.getByText(/No biographies have been inscribed yet/),
    ).toBeInTheDocument();
  });
});

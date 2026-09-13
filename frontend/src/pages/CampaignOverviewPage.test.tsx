// CampaignOverviewPage — ck3_chronicler-3tke (2026-05-08).
// Verifies hero/stats/nav rendering for active and sealed campaigns,
// and that nav-panel buttons route through setView.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { CampaignOverviewPage } from './CampaignOverviewPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { CampaignResponse } from '../api/types';

const ACTIVE: CampaignResponse = {
  id: 'a-1',
  name: 'Thrugot',
  ck3_version: '1.19.0',
  created_at: '2026-04-15T08:00:00+00:00',
  last_event_at: '2026-05-08T14:00:00+00:00',
  archived: false,
  db_path: 'E:/data/campaigns/thrugot.db',
  counts: { characters: 142, biographies: 18 },
  closing_chronicle_blurb: null,
  bookmark_date: '1066.9.15',
  current_in_game_date: '1106.6.6',
  current_player_character_id: 35748,
  current_player_name: 'Christoffer',
  current_player_nickname: null,
  current_house_name: 'House Thrugot',
  founding_dynasty_name: 'Thrugot',
  current_player_coa_json: null,
  current_player_gold: 1234.5,
  current_player_prestige_lifetime: 12500,
  current_player_piety_lifetime: 480,
  current_dynasty_renown: 7350,
  last_save_filename: null,
  last_save_ingested_at: null,
  last_save_in_game_date: null,
  last_tick_event_count: null,
  last_tick_event_type_tally: null,
  last_event_in_game_date: null,
};

const SEALED: CampaignResponse = {
  ...ACTIVE,
  id: 'b-2',
  name: 'Wessex',
  archived: true,
  closing_chronicle_blurb: 'Here endeth the chronicle of the House of Cerdic.',
  current_player_name: 'Edward',
  current_house_name: 'House of Cerdic',
  founding_dynasty_name: 'Cerdic',
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'campaign-overview',
    activeCampaign: 'Thrugot',
    selectedCharacterId: null,
  });
  vi.spyOn(client, 'getHeraldryPalette').mockResolvedValue({
    black: [0, 0, 0],
  });
  vi.spyOn(client, 'listTracked').mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CampaignOverviewPage', () => {
  it('renders the hero with dynasty name, ruler line, and stats', async () => {
    vi.spyOn(client, 'getCampaign').mockResolvedValue(ACTIVE);
    renderWithClient(<CampaignOverviewPage campaignName="Thrugot" />);

    await waitFor(() => screen.getByText('House Thrugot'));
    expect(screen.getByText('Active campaign')).toBeInTheDocument();
    expect(screen.getByText('Christoffer')).toBeInTheDocument();
    // Stats from CampaignCounts (Souls + Vitæ — Memories was dropped
    // with the rest of the LLM-memory pipeline in plan
    // cozy-coalescing-shannon).
    expect(screen.getByText('142')).toBeInTheDocument();
    expect(screen.getByText('18')).toBeInTheDocument();
    // ck3_chronicler-wdhe: realm row — gold rounds, prestige/renown
    // group thousands.
    expect(screen.getByText('1,235')).toBeInTheDocument();
    expect(screen.getByText('12,500')).toBeInTheDocument();
    expect(screen.getByText('480')).toBeInTheDocument();
    expect(screen.getByText('7,350')).toBeInTheDocument();
    // Span derived from bookmark_date → last_event_at.
    expect(screen.getByText(/40 years/)).toBeInTheDocument();
  });

  it('sealed campaign shows the "Read closing chronicle" CTA in the nav', async () => {
    useAppStore.setState({
      view: 'campaign-overview',
      activeCampaign: 'Wessex',
      selectedCharacterId: null,
    });
    vi.spyOn(client, 'getCampaign').mockResolvedValue(SEALED);
    renderWithClient(<CampaignOverviewPage campaignName="Wessex" />);

    await waitFor(() => screen.getByText('Sealed campaign'));
    expect(screen.getByText('Read closing chronicle')).toBeInTheDocument();
    // Active label should not appear on a sealed card.
    expect(screen.queryByText('Closing ceremony')).not.toBeInTheDocument();
  });

  it('clicking the Codex slot routes the view to codex', async () => {
    vi.spyOn(client, 'getCampaign').mockResolvedValue(ACTIVE);
    renderWithClient(<CampaignOverviewPage campaignName="Thrugot" />);
    const user = userEvent.setup();
    await waitFor(() => screen.getByText('Codex of Souls'));
    await user.click(screen.getByText('Codex of Souls'));
    expect(useAppStore.getState().view).toBe('codex');
  });
});

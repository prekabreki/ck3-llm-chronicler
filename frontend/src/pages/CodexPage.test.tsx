// CodexPage — tracked vs court split, search filtering, selection
// flow into the right pane.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { CodexPage } from './CodexPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { CharacterDetail, CharacterSummary, TrackedResponse } from '../api/types';

const CHARS: CharacterSummary[] = [
  { ck3_id: 100, first_name: 'Alfred', dynasty_name: 'Wessex', birth_date: '0848.10.1', death_date: '0899.10.26', coa_json: null, is_played: false },
  { ck3_id: 200, first_name: 'Eadgyth', dynasty_name: 'Wessex', birth_date: '0901.1.1', death_date: null, coa_json: null, is_played: false },
  { ck3_id: 300, first_name: 'Bjorn', dynasty_name: 'Lothbrok', birth_date: '0820.1.1', death_date: '0877.5.5', coa_json: null, is_played: false },
];

const TRACKED: TrackedResponse[] = [
  { character_id: 100, first_name: 'Alfred', nickname: null, role: 'player', added_at: '2026-04-15T08:00:00+00:00', biography_count: 1, monthly_token_spend: 0, paused_at: null, bumped_at: null, coa_json: null },
];

const ALFRED_DETAIL: CharacterDetail = {
  ck3_id: 100,
  first_name: 'Alfred',
  dynasty_name: 'Wessex',
  house_name: 'House of Cerdic',
  nickname: 'the Great',
  female: false,
  birth_date: '0848.10.1',
  death_date: '0899.10.26',
  culture: 'anglo_saxon',
  faith: 'catholic',
  events: [],
  coa_json: null,
  primary_title: null,
};

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'codex',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CodexPage', () => {
  // ck3_chronicler-5oyz: mock listCharacters to branch on the options
  // shape — ids fetch returns only the matching chars, q-search hits
  // the BE-side ranking, default returns the relevance-ranked window.
  function mockSmartListCharacters() {
    return vi
      .spyOn(client, 'listCharacters')
      .mockImplementation(async (_campaign, options = {}) => {
        if (options.ids !== undefined && options.ids.length > 0) {
          const wanted = new Set(options.ids);
          return CHARS.filter((c) => wanted.has(c.ck3_id));
        }
        if (options.q !== undefined && options.q !== '') {
          const needle = options.q.toLowerCase();
          return CHARS.filter(
            (c) =>
              (c.first_name?.toLowerCase().includes(needle) ?? false) ||
              (c.dynasty_name?.toLowerCase().includes(needle) ?? false),
          );
        }
        return CHARS;
      });
  }

  it('renders Tracked and "Of the court" sections from the queries', async () => {
    mockSmartListCharacters();
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: '1', name: 'Wessex', ck3_version: '1.19.0',
      created_at: '2026-04-15T08:00:00+00:00', last_event_at: null,
      archived: false, db_path: 'x', counts: null, closing_chronicle_blurb: null,
      bookmark_date: null,
      current_in_game_date: null,
      current_player_character_id: null,
      current_player_name: null,
      current_player_nickname: null,
      current_house_name: null,
      founding_dynasty_name: null,
      current_player_coa_json: null,
      current_player_gold: null,
      current_player_prestige_lifetime: null,
      current_player_piety_lifetime: null,
      current_dynasty_renown: null,
      last_save_filename: null,
      last_save_ingested_at: null,
      last_save_in_game_date: null,
      last_tick_event_count: null,
      last_tick_event_type_tally: null,
      last_event_in_game_date: null,
    });
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => {
      expect(screen.getByText(/✦ Tracked · 1/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Of the court · 2/)).toBeInTheDocument();
    // Alfred (tracked) and Eadgyth/Bjorn (court) all rendered
    expect(screen.getByText('Alfred')).toBeInTheDocument();
    expect(screen.getByText('Eadgyth')).toBeInTheDocument();
    expect(screen.getByText('Bjorn')).toBeInTheDocument();
  });

  it('search routes to the BE-driven endpoint and renders matches across the campaign', async () => {
    // ck3_chronicler-5oyz: pre-fix, search was a client-side filter
    // against the relevance-ranked top-N window — typing "Ramon" on a
    // 80k-character campaign returned nothing because Ramon wasn't in
    // the loaded window. Now the search field hits ?q=, so the BE's
    // ranked search surfaces matches across every soul.
    const listSpy = mockSmartListCharacters();
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: '1', name: 'Wessex', ck3_version: null, created_at: '2026-04-15T08:00:00+00:00',
      last_event_at: null, archived: false, db_path: 'x', counts: null, closing_chronicle_blurb: null,
      bookmark_date: null,
      current_in_game_date: null,
      current_player_character_id: null,
      current_player_name: null,
      current_player_nickname: null,
      current_house_name: null,
      founding_dynasty_name: null,
      current_player_coa_json: null,
      current_player_gold: null,
      current_player_prestige_lifetime: null,
      current_player_piety_lifetime: null,
      current_dynasty_renown: null,
      last_save_filename: null,
      last_save_ingested_at: null,
      last_save_in_game_date: null,
      last_tick_event_count: null,
      last_tick_event_type_tally: null,
      last_event_in_game_date: null,
    });
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Bjorn'));

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Search characters'), 'eadg');
    await waitFor(() => {
      expect(screen.queryByText('Alfred')).not.toBeInTheDocument();
      expect(screen.queryByText('Bjorn')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Eadgyth')).toBeInTheDocument();

    // Confirm the search query flowed through to the BE — the regression
    // we're guarding is "search was client-side only".
    expect(listSpy).toHaveBeenCalledWith(
      'Wessex',
      expect.objectContaining({ q: 'eadg' }),
    );
  });

  // ck3_chronicler-w1t3 follow-up: played-to-top sort + Played / Tracked filters.
  // A campaign with a played soul in the court and one tracked soul.
  const PLAYED_CHARS: CharacterSummary[] = [
    { ck3_id: 100, first_name: 'Alfred', dynasty_name: 'Wessex', birth_date: '0848.10.1', death_date: '0899.10.26', coa_json: null, is_played: false },
    { ck3_id: 200, first_name: 'Eadgyth', dynasty_name: 'Wessex', birth_date: '0901.1.1', death_date: null, coa_json: null, is_played: false },
    { ck3_id: 300, first_name: 'Bjorn', dynasty_name: 'Lothbrok', birth_date: '0820.1.1', death_date: '0877.5.5', coa_json: null, is_played: true },
  ];

  function mockPlayedCampaign() {
    vi.spyOn(client, 'listCharacters').mockImplementation(async (_campaign, options = {}) => {
      if (options.ids !== undefined && options.ids.length > 0) {
        const wanted = new Set(options.ids);
        return PLAYED_CHARS.filter((c) => wanted.has(c.ck3_id));
      }
      return PLAYED_CHARS;
    });
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: '1', name: 'Wessex', ck3_version: null, created_at: '2026-04-15T08:00:00+00:00',
      last_event_at: null, archived: false, db_path: 'x', counts: null, closing_chronicle_blurb: null,
      bookmark_date: null, current_in_game_date: null, current_player_character_id: null,
      current_player_name: null, current_player_nickname: null, current_house_name: null,
      founding_dynasty_name: null, current_player_coa_json: null, current_player_gold: null,
      current_player_prestige_lifetime: null, current_player_piety_lifetime: null,
      current_dynasty_renown: null, last_save_filename: null, last_save_ingested_at: null,
      last_save_in_game_date: null, last_tick_event_count: null, last_tick_event_type_tally: null,
      last_event_in_game_date: null,
    });
  }

  it('floats played souls to the top of the court table', async () => {
    mockPlayedCampaign();
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Bjorn'));
    // Court table holds the two untracked souls (Eadgyth #200, Bjorn #300).
    // Bjorn is_played, so despite the higher id he renders first.
    const courtListbox = screen.getByRole('listbox', { name: 'Of the court' });
    const names = Array.from(
      courtListbox.querySelectorAll('.codex-row__name'),
    ).map((el) => el.textContent?.replace(/✦/g, '').trim());
    expect(names[0]).toBe('Bjorn');
  });

  it('the Played filter narrows the tables to played souls', async () => {
    mockPlayedCampaign();
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Eadgyth'));

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Played/ }));
    await waitFor(() => {
      // Non-played court soul drops out; the played one stays.
      expect(screen.queryByText('Eadgyth')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Bjorn')).toBeInTheDocument();
  });

  it('the Tracked filter collapses the page to the Tracked table', async () => {
    mockPlayedCampaign();
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Bjorn'));

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Tracked/ }));
    await waitFor(() => {
      // "Of the court" table and its souls are gone; Tracked remains.
      expect(screen.queryByRole('listbox', { name: 'Of the court' })).not.toBeInTheDocument();
    });
    expect(screen.queryByText('Bjorn')).not.toBeInTheDocument();
    expect(screen.getByText('Alfred')).toBeInTheDocument();
  });

  it('clicking a row selects the character and loads detail', async () => {
    vi.spyOn(client, 'listCharacters').mockResolvedValue(CHARS);
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: '1', name: 'Wessex', ck3_version: null, created_at: '2026-04-15T08:00:00+00:00',
      last_event_at: null, archived: false, db_path: 'x', counts: null, closing_chronicle_blurb: null,
      bookmark_date: null,
      current_in_game_date: null,
      current_player_character_id: null,
      current_player_name: null,
      current_player_nickname: null,
      current_house_name: null,
      founding_dynasty_name: null,
      current_player_coa_json: null,
      current_player_gold: null,
      current_player_prestige_lifetime: null,
      current_player_piety_lifetime: null,
      current_dynasty_renown: null,
      last_save_filename: null,
      last_save_ingested_at: null,
      last_save_in_game_date: null,
      last_tick_event_count: null,
      last_tick_event_type_tally: null,
      last_event_in_game_date: null,
    });
    const detailSpy = vi
      .spyOn(client, 'getCharacterDetail')
      .mockResolvedValue(ALFRED_DETAIL);
    renderWithClient(<CodexPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));

    const user = userEvent.setup();
    await user.click(screen.getByText('Alfred'));
    expect(useAppStore.getState().selectedCharacterId).toBe(100);
    await waitFor(() => {
      expect(detailSpy).toHaveBeenCalledWith('Wessex', 100);
    });
    await waitFor(() => {
      expect(screen.getByText('called the Great')).toBeInTheDocument();
    });
    expect(screen.getByText('House of Cerdic')).toBeInTheDocument();
    expect(screen.getByText('Anglo Saxon')).toBeInTheDocument();
  });
});

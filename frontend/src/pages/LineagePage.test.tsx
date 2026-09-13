// LineagePage — empty/loading/error branches and the click-to-refocus
// flow that takes the user back to a Chronicle for the picked node.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { LineagePage } from './LineagePage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { FamilyTreeResponse } from '../api/types';

const TREE: FamilyTreeResponse = {
  self_node: {
    ck3_id: 100,
    first_name: 'Alfred',
    nickname: 'the Great',
    birth_date: '0848.10.1',
    death_date: '0899.10.26',
    relation: 'self',
    depth: 0,
    coa_json: null,
  },
  ancestors: [
    {
      ck3_id: 50,
      first_name: 'Æthelwulf',
      nickname: null,
      birth_date: '0795.1.1',
      death_date: '0858.1.13',
      relation: 'father',
      depth: 1,
      coa_json: null,
    },
    {
      ck3_id: 51,
      first_name: 'Osburh',
      nickname: null,
      birth_date: '0810.1.1',
      death_date: '0855.1.1',
      relation: 'mother',
      depth: 1,
      coa_json: null,
    },
  ],
  descendants: [
    {
      ck3_id: 200,
      first_name: 'Edward',
      nickname: 'the Elder',
      birth_date: '0874.1.1',
      death_date: '0924.7.17',
      relation: 'child',
      depth: 1,
      coa_json: null,
    },
  ],
  spouses: [
    {
      ck3_id: 150,
      first_name: 'Ealhswith',
      nickname: null,
      birth_date: '0852.1.1',
      death_date: '0902.12.5',
      relation: 'spouse',
      depth: 1,
      coa_json: null,
    },
  ],
  siblings: [],
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'tree',
    activeCampaign: 'Wessex',
    selectedCharacterId: 100,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('LineagePage', () => {
  it('shows the empty hint when the campaign has no pinned player and nothing is selected', async () => {
    // ck3_chronicler (2026-05-09): empty state only renders as the
    // final fallback — when the campaign has no current_player_character_id
    // AND the user hasn't picked a seed. Mock the campaign query to
    // return null so we exercise that path.
    useAppStore.setState({ selectedCharacterId: null });
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: 'a',
      name: 'Wessex',
      ck3_version: '1.19.0',
      created_at: '2026-04-15T00:00:00+00:00',
      last_event_at: null,
      archived: false,
      db_path: 'x',
      counts: null,
      closing_chronicle_blurb: null,
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
    renderWithClient(<LineagePage campaignName="Wessex" />);
    await waitFor(() =>
      expect(screen.getByText(/No player resolved/)).toBeInTheDocument(),
    );
  });

  it('falls back to the campaign\'s current player when nothing is selected', async () => {
    // ck3_chronicler (2026-05-09): the new default behaviour. When the
    // campaign has a pinned player, root the tree there instead of
    // forcing the user back to the Codex to pick a seed.
    useAppStore.setState({ selectedCharacterId: null });
    vi.spyOn(client, 'getCampaign').mockResolvedValue({
      id: 'a',
      name: 'Wessex',
      ck3_version: '1.19.0',
      created_at: '2026-04-15T00:00:00+00:00',
      last_event_at: null,
      archived: false,
      db_path: 'x',
      counts: null,
      closing_chronicle_blurb: null,
      bookmark_date: null,
      current_in_game_date: null,
      current_player_character_id: 100,
      current_player_name: 'Alfred',
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
    const treeSpy = vi.spyOn(client, 'getFamilyTree').mockResolvedValue(TREE);
    renderWithClient(<LineagePage campaignName="Wessex" />);
    await waitFor(() =>
      expect(treeSpy).toHaveBeenCalledWith('Wessex', 100, expect.anything()),
    );
  });

  it('renders the seed plus parents, spouse, child', async () => {
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(TREE);

    renderWithClient(<LineagePage campaignName="Wessex" />);

    await waitFor(() => {
      expect(screen.getByText(/The Kin of Alfred/)).toBeInTheDocument();
    });
    // The seed and one of each generation render in the SVG.
    expect(screen.getByText('Alfred')).toBeInTheDocument();
    expect(screen.getByText('Æthelwulf')).toBeInTheDocument();
    expect(screen.getByText('Osburh')).toBeInTheDocument();
    expect(screen.getByText('Ealhswith')).toBeInTheDocument();
    expect(screen.getByText('Edward')).toBeInTheDocument();

    // Focus indicator only on the seed.
    const focusLabels = screen.getAllByText('✦ FOCUS');
    expect(focusLabels).toHaveLength(1);
  });

  it('clicking a node selects that character and routes to chronicle', async () => {
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(TREE);

    renderWithClient(<LineagePage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Edward'));

    const user = userEvent.setup();
    // Click the <g> around the Edward text.
    const edwardGroup = screen.getByText('Edward').closest('g');
    expect(edwardGroup).not.toBeNull();
    await user.click(edwardGroup!);

    expect(useAppStore.getState().selectedCharacterId).toBe(200);
    expect(useAppStore.getState().view).toBe('chronicle');
  });

  it('shows a friendly empty state when only the seed is present', async () => {
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue({
      ...TREE,
      ancestors: [],
      descendants: [],
      spouses: [],
      siblings: [],
    });

    renderWithClient(<LineagePage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/The Kin of Alfred/));
    expect(screen.getByText(/No kin recorded for this soul/)).toBeInTheDocument();
  });
});

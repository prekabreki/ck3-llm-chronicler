// LibraryPage — verify campaign cards render from the query, Active vs
// Completed split is correct, and clicking a card sets activeCampaign +
// routes to Codex.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { LibraryPage } from './LibraryPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { CampaignResponse } from '../api/types';

const FULL_CAMPAIGN: CampaignResponse = {
  id: 'a',
  name: 'Erik 1066-9-15',
  ck3_version: '1.19.0.4',
  created_at: '2026-05-01T00:00:00+00:00',
  last_event_at: '2026-05-03T00:00:00+00:00',
  archived: false,
  db_path: 'C:/x/erik.db',
  counts: { characters: 3, biographies: 0 },
  closing_chronicle_blurb: null,
  bookmark_date: '1066.9.15',
  current_in_game_date: '1075.5.9',
  current_player_character_id: 32943,
  current_player_name: 'Erik',
  current_player_nickname: 'the Heathen',
  current_house_name: 'house_munso',
  founding_dynasty_name: 'Munso',
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
};

const SAMPLE_CAMPAIGNS: CampaignResponse[] = [
  {
    id: 'a-1',
    name: 'Wessex',
    ck3_version: '1.19.0',
    created_at: '2026-04-15T08:00:00+00:00',
    last_event_at: '2026-05-02T14:00:00+00:00',
    archived: false,
    db_path: 'E:/data/campaigns/wessex.db',
    counts: { characters: 12, biographies: 7 },
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
  },
  {
    id: 'b-2',
    name: 'Munster',
    ck3_version: '1.19.0',
    created_at: '2026-04-20T08:00:00+00:00',
    last_event_at: null,
    archived: false,
    db_path: 'E:/data/campaigns/munster.db',
    counts: { characters: 5, biographies: 1 },
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
  },
  {
    id: 'c-3',
    name: 'Old Northumbria',
    ck3_version: '1.18.0',
    created_at: '2026-01-01T08:00:00+00:00',
    last_event_at: '2026-03-10T08:00:00+00:00',
    archived: true,
    db_path: 'E:/data/campaigns/north.db',
    counts: { characters: 28, biographies: 22 },
    closing_chronicle_blurb:
      'Here endeth the chronicle of the House of Wærhelm — its sons and daughters, their wars and their weddings, written down by the chronicler against the day all such things are forgotten.',
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
  },
];

function renderWithClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'library',
    activeCampaign: null,
    selectedCharacterId: null,
  });
  // ck3_chronicler-a3f: default palette stub so usePalette resolves with
  // something realistic. Tests that care can override; tests that don't
  // touch heraldry just see the fallback Banner because no fixture has a
  // current_player_coa_json. retry:false on the hook means a missing
  // mock would fail-fast and leave palette undefined — fine, but
  // stubbing makes the test environment resemble production.
  vi.spyOn(client, 'getHeraldryPalette').mockResolvedValue({
    black: [0, 0, 0],
    green: [0, 128, 0],
    yellow: [255, 200, 0],
    red: [200, 30, 30],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('LibraryPage', () => {
  it('renders Active and Completed sections from the campaigns query', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Wessex')[0]).toBeInTheDocument();
    });
    expect(screen.getByText('Munster')).toBeInTheDocument();
    expect(screen.getByText('Old Northumbria')).toBeInTheDocument();
    // Section headers render as h2 — disambiguate from the per-card
    // status chip that also reads "Active".
    expect(screen.getByRole('heading', { name: 'Active', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Completed', level: 2 })).toBeInTheDocument();
    // Sealed badge only on the archived card
    expect(screen.getByText('Sealed')).toBeInTheDocument();
  });

  it('renders character + biography counts in the discrete count cells', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    // Wessex card has counts { characters: 12, biographies: 7 } —
    // each surfaces as a separate count cell with its label.
    await waitFor(() => screen.getAllByText('Wessex')[0]);
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getAllByText('Souls').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Vitæ').length).toBeGreaterThan(0);
  });

  it('clicking a card sets active campaign and routes to the campaign overview', async () => {
    // ck3_chronicler-3tke (2026-05-08): Library cards now land on the
    // campaign-overview hero rather than dropping the user straight
    // into the Codex of Souls. Codex is reachable in one click from
    // the overview's nav panel.
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);
    await user.click(screen.getByLabelText('Open campaign Wessex'));
    expect(useAppStore.getState().activeCampaign).toBe('Wessex');
    expect(useAppStore.getState().view).toBe('campaign-overview');
  });

  it('shows the loading state initially', () => {
    vi.spyOn(client, 'listCampaigns').mockReturnValue(new Promise(() => {}));
    renderWithClient(<LibraryPage />);
    expect(screen.getByText(/Drawing the volumes/)).toBeInTheDocument();
  });

  it('renders an error state on query failure', async () => {
    vi.spyOn(client, 'listCampaigns').mockRejectedValue(new Error('backend down'));
    renderWithClient(<LibraryPage />);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/backend down/);
    });
  });

  it('renders an empty hint when no active campaigns', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => {
      expect(screen.getByText(/No active campaigns/)).toBeInTheDocument();
    });
  });

  it('passes include_archived=true so the Completed shelf is reachable', async () => {
    const spy = vi.spyOn(client, 'listCampaigns').mockResolvedValue([]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const opts = spy.mock.calls[0]?.[0];
    expect(opts).toMatchObject({
      includeCounts: true,
      includeArchived: true,
    });
  });

  it('renders the closing-chronicle blurb on Completed cards when present', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getByText('Old Northumbria'));
    expect(
      screen.getByText(
        /Here endeth the chronicle of the House of Wærhelm/,
      ),
    ).toBeInTheDocument();
  });
});

describe('CampaignCard byline (ck3_chronicler-cqo)', () => {
  it('renders the all-four-parts byline', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([FULL_CAMPAIGN]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(
      screen.getByText(/Erik, called the Heathen · House house_munso/),
    ).toBeInTheDocument();
  });

  it('omits nickname segment when nickname is null', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, current_player_nickname: null },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(
      screen.getByText(/Erik · House house_munso/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/called/)).not.toBeInTheDocument();
  });

  it('falls back to dynasty when house is null', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, current_house_name: null, founding_dynasty_name: 'Munso' },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(
      screen.getByText(/Erik, called the Heathen · of the Munso/),
    ).toBeInTheDocument();
  });

  it('omits byline entirely when all identity fields are null', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      {
        ...FULL_CAMPAIGN,
        current_player_name: null,
        current_player_nickname: null,
        current_house_name: null,
        founding_dynasty_name: null,
      },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.queryByText(/called/)).not.toBeInTheDocument();
    expect(screen.queryByText(/House/)).not.toBeInTheDocument();
    expect(screen.queryByText(/of the/)).not.toBeInTheDocument();
  });
});

describe('CampaignCard In-game pair (ck3_chronicler-cqo)', () => {
  it('renders the both-dates pair', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([FULL_CAMPAIGN]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.getByText(/1066\.9 → 1075\.5/)).toBeInTheDocument();
  });

  it('renders only-bookmark pair', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, current_in_game_date: null },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.getByText(/since 1066\.9/)).toBeInTheDocument();
  });

  it('renders only-current pair', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, bookmark_date: null },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.getByText(/as of 1075\.5/)).toBeInTheDocument();
  });

  it('omits In-game pair when both dates are null', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, bookmark_date: null, current_in_game_date: null },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.queryByText(/In-game/)).not.toBeInTheDocument();
  });
});

describe('CampaignCard heraldry (ck3_chronicler-a3f)', () => {
  it('renders RealHeraldry when the campaign has a coa and palette resolves', async () => {
    const withCoa: CampaignResponse = {
      ...FULL_CAMPAIGN,
      current_player_coa_json: {
        pattern: 'pattern_solid.dds',
        color1: 'black',
        color2: 'green',
        color3: 'yellow',
      },
    };
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([withCoa]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    // RealHeraldry sets aria-label="Arms of <name>" on its <svg>. Scope to
    // the card itself — the polish-pass hero band also renders RealHeraldry
    // for the most-recently-touched campaign, so a document-wide query
    // would match both shield and hero.
    // vgcp: aria-label now lives on the inner Open <button>, not the
    // <article>. Walk up so `within(card)` still sees sibling subtrees.
    const card = screen
      .getByLabelText('Open campaign Erik 1066-9-15')
      .closest('article') as HTMLElement;
    await waitFor(() => {
      expect(within(card).getByLabelText(/Arms of Erik/)).toBeInTheDocument();
    });
  });

  it('falls back to procedural Banner when the campaign has no coa', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      { ...FULL_CAMPAIGN, current_player_coa_json: null },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    // vgcp: aria-label now lives on the inner Open <button>, not the
    // <article>. Walk up so `within(card)` still sees sibling subtrees.
    const card = screen
      .getByLabelText('Open campaign Erik 1066-9-15')
      .closest('article') as HTMLElement;
    expect(within(card).queryByLabelText(/Arms of/)).not.toBeInTheDocument();
  });

  it('falls back to Banner when palette fetch fails', async () => {
    // Override the default-stub palette with a failure for this test.
    vi.spyOn(client, 'getHeraldryPalette').mockRejectedValue(
      new Error('palette unavailable'),
    );
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([
      {
        ...FULL_CAMPAIGN,
        current_player_coa_json: {
          pattern: 'pattern_solid.dds',
          color1: 'black',
        },
      },
    ]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    // No real heraldry rendered on the card because palette never resolved.
    // Scope to card — the polish-pass hero band always renders a shield
    // for the most-recently-touched campaign.
    // vgcp: aria-label now lives on the inner Open <button>, not the
    // <article>. Walk up so `within(card)` still sees sibling subtrees.
    const card = screen
      .getByLabelText('Open campaign Erik 1066-9-15')
      .closest('article') as HTMLElement;
    expect(within(card).queryByLabelText(/Arms of/)).not.toBeInTheDocument();
  });
});

describe('CampaignCard rename (ck3_chronicler-bly)', () => {
  it('clicking Rename… opens an inline editor pre-filled with the name', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const button = screen.getByRole('button', {
      name: /Rename campaign Wessex/i,
    });
    await user.click(button);
    const input = screen.getByLabelText(
      /Campaign name for Wessex/i,
    ) as HTMLInputElement;
    expect(input.value).toBe('Wessex');
  });

  it('Enter commits the rename via the API and clears the editor', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    const renameSpy = vi
      .spyOn(client, 'renameCampaign')
      .mockResolvedValue({
        campaign: { ...SAMPLE_CAMPAIGNS[0]!, name: 'Norse Smoke' },
        warning: null,
      });
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const renameButton = screen.getByRole('button', {
      name: /Rename campaign Wessex/i,
    });
    await user.click(renameButton);
    const input = screen.getByLabelText(
      /Campaign name for Wessex/i,
    ) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, 'Norse Smoke');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(renameSpy).toHaveBeenCalledWith('Wessex', 'Norse Smoke');
    });
    expect(renameSpy).toHaveBeenCalledTimes(1);
  });

  it('Escape cancels and does not call the API', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    const renameSpy = vi.spyOn(client, 'renameCampaign');
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const renameButton = screen.getByRole('button', {
      name: /Rename campaign Wessex/i,
    });
    await user.click(renameButton);
    const input = screen.getByLabelText(
      /Campaign name for Wessex/i,
    ) as HTMLInputElement;
    await user.click(input);
    await user.keyboard('{Escape}');

    expect(renameSpy).not.toHaveBeenCalled();
  });

  it('renders the API warning beneath the title on collision', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    vi.spyOn(client, 'renameCampaign').mockResolvedValue({
      campaign: { ...SAMPLE_CAMPAIGNS[0]!, name: 'Munster' },
      warning: "name collides with existing campaign 'Munster'",
    });
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const renameButton = screen.getByRole('button', {
      name: /Rename campaign Wessex/i,
    });
    await user.click(renameButton);
    const input = screen.getByLabelText(
      /Campaign name for Wessex/i,
    ) as HTMLInputElement;
    await user.tripleClick(input);
    await user.type(input, 'Munster');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(
        /collides with existing campaign 'Munster'/,
      );
    });
  });

  it('clicking Rename… does not open the card', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const renameButton = screen.getByRole('button', {
      name: /Rename campaign Wessex/i,
    });
    await user.click(renameButton);
    expect(useAppStore.getState().activeCampaign).toBeNull();
    expect(useAppStore.getState().view).toBe('library');
  });
});

describe('LibraryPage empty-state copy (ck3_chronicler-v2a / nji)', () => {
  it("guides the user to the '+ Adopt save' button on empty Library", async () => {
    // ck3_chronicler-nji: empty state replaced the legacy
    // 'chronicler save-tail' CLI hint with the v2a Adopt-save UX.
    // The button stays in the page actions even when empty so the
    // first-launch flow has a clear next step.
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => {
      expect(screen.getByText(/No active campaigns yet/)).toBeInTheDocument();
    });
    // The label 'Adopt save' shows on the button AND in the empty-state
    // copy that points at it; both occurrences are intentional.
    expect(screen.getAllByText(/Adopt save/).length).toBeGreaterThan(0);
    // Sanity: the legacy CLI hint should not resurface.
    expect(
      screen.queryByText(/chronicler campaign create/),
    ).not.toBeInTheDocument();
  });
});

// ck3_chronicler-ezpc: delete-campaign affordance on the Library card.
describe('CampaignCard delete (ck3_chronicler-ezpc)', () => {
  it('Delete… button opens a confirm modal', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Wessex')[0]);

    const user = userEvent.setup();
    const deleteBtn = screen.getAllByRole('button', {
      name: /Delete campaign Wessex/,
    })[0]!;
    await user.click(deleteBtn);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/Erase Wessex\?/)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Delete permanently' }),
    ).toBeInTheDocument();
  });

  it('Confirm button calls deleteCampaign and closes the modal', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    const delSpy = vi
      .spyOn(client, 'deleteCampaign')
      .mockResolvedValue(undefined);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Wessex')[0]);
    const user = userEvent.setup();
    await user.click(
      screen.getAllByRole('button', { name: /Delete campaign Wessex/ })[0]!,
    );
    await user.click(
      screen.getByRole('button', { name: 'Delete permanently' }),
    );
    await waitFor(() => expect(delSpy).toHaveBeenCalledWith('Wessex'));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
  });

  it('Cancel button leaves the campaign untouched', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue(SAMPLE_CAMPAIGNS);
    const delSpy = vi.spyOn(client, 'deleteCampaign');
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Wessex')[0]);
    const user = userEvent.setup();
    await user.click(
      screen.getAllByRole('button', { name: /Delete campaign Wessex/ })[0]!,
    );
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(delSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

// ck3_chronicler-bges: per-card last-tick activity line.
describe('CampaignCard last-tick line (ck3_chronicler-bges)', () => {
  it('renders the last-tick line on a card with persisted last-tick fields', async () => {
    const withTick: CampaignResponse = {
      ...FULL_CAMPAIGN,
      last_save_ingested_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      last_tick_event_count: 3,
    };
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([withTick]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.getByText(/3 events in last save tick/)).toBeInTheDocument();
  });

  it('hides the last-tick line when last_save_ingested_at is null', async () => {
    vi.spyOn(client, 'listCampaigns').mockResolvedValue([FULL_CAMPAIGN]);
    renderWithClient(<LibraryPage />);
    await waitFor(() => screen.getAllByText('Erik 1066-9-15')[0]);
    expect(screen.queryByText(/last save tick/)).not.toBeInTheDocument();
  });
});

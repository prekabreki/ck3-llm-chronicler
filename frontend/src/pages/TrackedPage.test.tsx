// TrackedPage — covers no-campaign hint, the populated list, and the
// click-to-open flow that takes the user to the chronicle.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { TrackedPage } from './TrackedPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { CharacterSummary, TrackedResponse } from '../api/types';

const TRACKED: TrackedResponse[] = [
  {
    character_id: 100,
    first_name: 'Alfred',
    nickname: 'the Great',
    role: 'player',
    added_at: '2026-04-15T08:00:00+00:00',
    biography_count: 1,
    monthly_token_spend: 4521,
    paused_at: null,
    bumped_at: null,
    coa_json: null,
  },
  {
    character_id: 150,
    first_name: 'Ealhswith',
    nickname: null,
    role: 'spouse',
    added_at: '2026-04-16T08:00:00+00:00',
    biography_count: 0,
    monthly_token_spend: 1200,
    paused_at: null,
    bumped_at: null,
    coa_json: null,
  },
];

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'tracked',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('TrackedPage', () => {
  it('renders one row per tracked character with role badges', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));

    expect(screen.getByText('Alfred')).toBeInTheDocument();
    expect(screen.getByText('Ealhswith')).toBeInTheDocument();
    expect(screen.getByText('Player')).toBeInTheDocument();
    expect(screen.getByText('Spouse')).toBeInTheDocument();
    // 4521 → "4.5k tok / mo"
    expect(screen.getByText(/4\.5k tok \/ mo/)).toBeInTheDocument();
  });

  it('Open button selects the character and routes to chronicle', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));

    const user = userEvent.setup();
    const openButtons = screen.getAllByRole('button', { name: /Open/ });
    await user.click(openButtons[0]!);

    expect(useAppStore.getState().selectedCharacterId).toBe(100);
    expect(useAppStore.getState().view).toBe('chronicle');
  });

  it('shows a friendly empty state when nothing is tracked', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() =>
      expect(
        screen.getByText(/No characters yet tracked/),
      ).toBeInTheDocument(),
    );
  });

  // ck3_chronicler-ogi: auto-track + add-by-id + untrack actions wired
  // against POST /tracked, POST /tracked/auto-track, DELETE /tracked/{id}.

  it('auto-track button calls the endpoint and surfaces a toast', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const autoSpy = vi.spyOn(client, 'autoTrackFromSave').mockResolvedValue({
      added: [
        { ...TRACKED[0]! },
        { ...TRACKED[1]! },
      ],
      already_tracked: [],
      save_path: 'C:\\Users\\me\\Documents\\Paradox\\autosave.ck3',
    });
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() => screen.getByRole('button', { name: /Auto-track from save/ }));

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Auto-track from save/ }));

    expect(autoSpy).toHaveBeenCalledWith('Wessex', {});
    await waitFor(() =>
      expect(screen.getByText(/Tracked 2 new souls from autosave\.ck3/)).toBeInTheDocument(),
    );
  });

  it('Add-by-ID modal submits a POST to /tracked', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const addSpy = vi.spyOn(client, 'addTracked').mockResolvedValue(TRACKED[0]!);
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Add by ID/ }));
    await user.type(screen.getByLabelText(/CK3 character ID/i), '100');
    await user.type(screen.getByLabelText(/Role/i), 'rival');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(addSpy).toHaveBeenCalledWith('Wessex', {
      character_id: 100,
      role: 'rival',
      note: null,
    });
  });

  it('Add-by-ID rejects non-numeric input before calling the API', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const addSpy = vi.spyOn(client, 'addTracked');
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Add by ID/ }));
    await user.type(screen.getByLabelText(/CK3 character ID/i), 'abc');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    expect(addSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/positive integer/i)).toBeInTheDocument();
  });

  it('Untrack confirms then deletes', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    const delSpy = vi.spyOn(client, 'deleteTracked').mockResolvedValue();
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));

    const user = userEvent.setup();
    const untrackButtons = screen.getAllByRole('button', { name: 'Untrack' });
    await user.click(untrackButtons[0]!);
    // Modal opens with the character's name.
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Stop tracking Alfred/)).toBeInTheDocument();

    // Click the modal's confirm button (scoped via within so we don't
    // grab the row-level Untrack buttons that are still in the DOM).
    await user.click(within(dialog).getByRole('button', { name: 'Untrack' }));

    expect(delSpy).toHaveBeenCalledWith('Wessex', 100);
  });

  // ck3_chronicler-4i33: name-search typeahead.
  it('search-by-name typeahead calls list-characters with q after debounce', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const matches: CharacterSummary[] = [
      {
        ck3_id: 200,
        first_name: 'Edward',
        dynasty_name: 'Wessex',
        birth_date: '1010.1.1',
        death_date: null,
        coa_json: null,
        is_played: false,
      },
    ];
    const listSpy = vi
      .spyOn(client, 'listCharacters')
      .mockResolvedValue(matches);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const user = userEvent.setup();

    const input = await waitFor(() =>
      screen.getByPlaceholderText('Search the codex by name…'),
    );
    await user.type(input, 'Edw');

    await waitFor(() =>
      expect(listSpy).toHaveBeenCalledWith('Wessex', { q: 'Edw', limit: 8 }),
    );
    await waitFor(() => expect(screen.getByText('Edward')).toBeInTheDocument());
    expect(screen.getByText('#200')).toBeInTheDocument();
  });

  it('clicking a typeahead result tracks that character', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'listCharacters').mockResolvedValue([
      {
        ck3_id: 200,
        first_name: 'Edward',
        dynasty_name: 'Wessex',
        birth_date: '1010.1.1',
        death_date: null,
        coa_json: null,
        is_played: false,
      },
    ]);
    const addSpy = vi
      .spyOn(client, 'addTracked')
      .mockResolvedValue(TRACKED[0]!);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const user = userEvent.setup();
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Search the codex by name…'),
    );
    await user.type(input, 'Edw');
    await waitFor(() => screen.getByText('Edward'));
    const option = screen.getByRole('option');
    await user.click(within(option).getByRole('button'));
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('Wessex', {
        character_id: 200,
        role: null,
        note: null,
      }),
    );
  });

  // ck3_chronicler-wd7x: WAI-ARIA combobox 1.2 keyboard contract.
  it('typeahead Enter commits the highlighted result without mouse', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'listCharacters').mockResolvedValue([
      {
        ck3_id: 200,
        first_name: 'Edward',
        dynasty_name: 'Wessex',
        birth_date: '1010.1.1',
        death_date: null,
        coa_json: null,
        is_played: false,
      },
      {
        ck3_id: 201,
        first_name: 'Edmund',
        dynasty_name: 'Wessex',
        birth_date: '1012.1.1',
        death_date: null,
        coa_json: null,
        is_played: false,
      },
    ]);
    const addSpy = vi
      .spyOn(client, 'addTracked')
      .mockResolvedValue(TRACKED[0]!);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const user = userEvent.setup();
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Search the codex by name…'),
    );
    await user.type(input, 'Edw');
    await waitFor(() => screen.getByText('Edward'));
    // First result is auto-highlighted; ArrowDown moves to second.
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('Wessex', {
        character_id: 201,
        role: null,
        note: null,
      }),
    );
  });

  it('typeahead exposes combobox + aria-activedescendant contract', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'listCharacters').mockResolvedValue([
      {
        ck3_id: 200,
        first_name: 'Edward',
        dynasty_name: 'Wessex',
        birth_date: '1010.1.1',
        death_date: null,
        coa_json: null,
        is_played: false,
      },
    ]);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const user = userEvent.setup();
    const input = (await waitFor(() =>
      screen.getByPlaceholderText('Search the codex by name…'),
    )) as HTMLInputElement;
    expect(input.getAttribute('role')).toBe('combobox');
    expect(input.getAttribute('aria-autocomplete')).toBe('list');
    expect(input.getAttribute('aria-controls')).toBeTruthy();
    await user.type(input, 'Edw');
    await waitFor(() => screen.getByText('Edward'));
    // First result auto-highlighted -> activedescendant points at its id.
    const option = screen.getByRole('option');
    expect(option.getAttribute('aria-selected')).toBe('true');
    expect(input.getAttribute('aria-activedescendant')).toBe(option.id);
    expect(input.getAttribute('aria-expanded')).toBe('true');
  });

  it('skips fetching while query is shorter than 2 chars', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const listSpy = vi.spyOn(client, 'listCharacters');
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const user = userEvent.setup();
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Search the codex by name…'),
    );
    await user.type(input, 'E');
    // Even after the debounce, a 1-char query is below the floor.
    await new Promise((r) => setTimeout(r, 350));
    expect(listSpy).not.toHaveBeenCalled();
  });

  // ck3_chronicler-gw16: rule checkboxes.
  it('renders auto-track rule checkboxes from the persisted rules', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'getAutoTrackRules').mockResolvedValue({
      include_heirs: false,
      include_spouses: true,
      include_grandchildren: false,
      include_county_vassals: false,
    });
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    // Wait for the rules query to land — the checkbox starts at the
    // optimistic default (true) until then.
    await waitFor(() => {
      const heirs = screen.getByLabelText('Direct heirs') as HTMLInputElement;
      expect(heirs.checked).toBe(false);
    });
    const spouses = screen.getByLabelText('Spouses') as HTMLInputElement;
    expect(spouses.checked).toBe(true);
    const vassals = screen.getByLabelText('County-tier vassals') as HTMLInputElement;
    expect(vassals.checked).toBe(false);
  });

  it('toggling a checkbox PUTs the rule update', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'getAutoTrackRules').mockResolvedValue({
      include_heirs: true,
      include_spouses: true,
      include_grandchildren: false,
      include_county_vassals: false,
    });
    const updateSpy = vi
      .spyOn(client, 'updateAutoTrackRules')
      .mockResolvedValue({
        include_heirs: false,
        include_spouses: true,
        include_grandchildren: false,
        include_county_vassals: false,
      });
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const heirs = await screen.findByLabelText('Direct heirs');
    const user = userEvent.setup();
    await user.click(heirs);
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith('Wessex', { include_heirs: false }),
    );
  });

  // ck3_chronicler-gw16: suggested-souls one-tap track.
  it('renders suggested souls and one-tap-tracks them', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'getAutoTrackRules').mockResolvedValue({
      include_heirs: true,
      include_spouses: true,
      include_grandchildren: false,
      include_county_vassals: false,
    });
    vi.spyOn(client, 'listSuggestedCandidates').mockResolvedValue([
      {
        ck3_id: 555,
        first_name: 'Athelstan',
        nickname: null,
        relation: 'child',
        birth_date: '1080.1.1',
        death_date: null,
        coa_json: null,
      },
    ]);
    const addSpy = vi
      .spyOn(client, 'addTracked')
      .mockResolvedValue(TRACKED[0]!);
    renderWithClient(<TrackedPage campaignName="Wessex" />);
    const trackBtn = await screen.findByRole('button', { name: 'Track' });
    const user = userEvent.setup();
    await user.click(trackBtn);
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('Wessex', {
        character_id: 555,
        note: 'child',
      }),
    );
  });

  // ck3_chronicler-tmjn: County-tier vassals checkbox wired up.
  it('ck3_chronicler-tmjn: County-tier vassals checkbox is enabled and persists', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    vi.spyOn(client, 'getAutoTrackRules').mockResolvedValue({
      include_heirs: true,
      include_spouses: true,
      include_grandchildren: false,
      include_county_vassals: false,
    });
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    // Wait for the rules query to settle — checkbox is disabled while loading.
    await waitFor(() => {
      const checkbox = screen.getByRole('checkbox', {
        name: /County-tier vassals/i,
      });
      expect(checkbox).not.toBeDisabled();
    });

    expect(
      screen.queryByText(/split to gw16\.2/i),
    ).not.toBeInTheDocument();
  });

  // ck3_chronicler-0224: session-boundary buttons.

  it('End session button calls /cost/end-session and toasts', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    const endSpy = vi
      .spyOn(client, 'endSession')
      .mockResolvedValue({ campaign_id: 'uuid-wessex', tracked_considered: 0 });

    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText('Alfred'));

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /End session/ }));

    expect(endSpy).toHaveBeenCalledWith('Wessex');
    await waitFor(() =>
      expect(
        screen.getByText(/Session boundary marked\./),
      ).toBeInTheDocument(),
    );
  });

  // ck3_chronicler-nele: tracked-count display + soft warning at >30.

  it('shows the tracked count chip beside the title when any are tracked', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue(TRACKED);
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    await waitFor(() => screen.getByText('Alfred'));
    const heading = screen.getByRole('heading', { name: /Tracked Souls/ });
    expect(heading).toHaveTextContent('2');
  });

  it('omits the count chip when nothing is tracked', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    await waitFor(() =>
      expect(screen.getByText(/No characters yet tracked/)).toBeInTheDocument(),
    );
    const heading = screen.getByRole('heading', { name: /Tracked Souls/ });
    // The title text is exactly "Tracked Souls" — no trailing digit.
    expect(heading.textContent?.trim()).toBe('Tracked Souls');
  });

  it('shows the soft warning banner when the tracked set exceeds 30', async () => {
    const many: TrackedResponse[] = Array.from({ length: 31 }, (_, i) => ({
      ...TRACKED[0]!,
      character_id: 1000 + i,
      first_name: `Soul ${i}`,
      role: 'manual',
    }));
    vi.spyOn(client, 'listTracked').mockResolvedValue(many);
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    await waitFor(() => screen.getByText('Soul 0'));
    expect(
      screen.getByText(/31 souls is a generous flock/),
    ).toBeInTheDocument();
  });

  it('does not show the soft warning banner at exactly 30', async () => {
    const exactly30: TrackedResponse[] = Array.from(
      { length: 30 },
      (_, i) => ({
        ...TRACKED[0]!,
        character_id: 2000 + i,
        first_name: `Soul ${i}`,
        role: 'manual',
      }),
    );
    vi.spyOn(client, 'listTracked').mockResolvedValue(exactly30);
    renderWithClient(<TrackedPage campaignName="Wessex" />);

    await waitFor(() => screen.getByText('Soul 0'));
    expect(
      screen.queryByText(/is a generous flock/),
    ).not.toBeInTheDocument();
  });

  it('Reset session counter button calls /cost/reset-session and toasts', async () => {
    vi.spyOn(client, 'listTracked').mockResolvedValue([]);
    const resetSpy = vi
      .spyOn(client, 'resetSessionCounter')
      .mockResolvedValue({ since: '2026-05-28T09:00:00+00:00' });

    renderWithClient(<TrackedPage campaignName="Wessex" />);
    await waitFor(() =>
      screen.getByRole('button', { name: /Reset session counter/ }),
    );

    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: /Reset session counter/ }),
    );

    expect(resetSpy).toHaveBeenCalledWith('Wessex');
    await waitFor(() =>
      expect(
        screen.getByText(/Session counter reset — lifetime unchanged\./),
      ).toBeInTheDocument(),
    );
  });
});

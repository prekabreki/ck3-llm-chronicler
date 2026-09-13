// ClosingPage — covers no-campaign, no-chronicle (CTA), and the
// populated chronicle-prose render once one exists.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ClosingPage } from './ClosingPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type {
  CampaignResponse,
  ClosingChronicleResponse,
} from '../api/types';

const CAMPAIGN: CampaignResponse = {
  id: '1',
  name: 'Wessex',
  ck3_version: '1.19.0',
  created_at: '2026-04-15T08:00:00+00:00',
  last_event_at: '2026-04-30T18:00:00+00:00',
  archived: false,
  db_path: 'x',
  counts: { characters: 5, biographies: 2 },
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
};

const CHRONICLE: ClosingChronicleResponse = {
  campaign_name: 'Wessex',
  body: 'First came Alfred the Great.\n\nThen came Edward the Elder.',
  generated_at: '2026-05-02T10:00:00+00:00',
  archived: true,
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'closing',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ClosingPage', () => {
  it('shows the seal CTA when no chronicle exists yet (null)', async () => {
    // F-60: BE returns 200 + null for not-yet-sealed; FE renders the
    // seal CTA on null. The pre-F-60 contract was 404 + ApiError throw.
    vi.spyOn(client, 'getCampaign').mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(null);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    await waitFor(() =>
      expect(screen.getByText(/Seal this campaign/)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/No closing chronicle has been generated/),
    ).toBeInTheDocument();
  });

  it('two-step seal: button reveals confirm UI', async () => {
    vi.spyOn(client, 'getCampaign').mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(null);

    renderWithClient(<ClosingPage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/Seal this campaign/));

    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: /Begin closing ceremony/ }),
    );
    expect(screen.getByText(/This is final/)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Yes, seal it/ }),
    ).toBeInTheDocument();
  });

  it('renders the chronicle prose when one exists', async () => {
    vi.spyOn(client, 'getCampaign').mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    await waitFor(() =>
      expect(
        screen.getByText(/First came Alfred the Great\./),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Then came Edward the Elder\./),
    ).toBeInTheDocument();
  });

  it('frontispiece carries Founded · Closed · Span mono summary', async () => {
    vi.spyOn(client, 'getCampaign').mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    await waitFor(() =>
      expect(screen.getByText('Founded')).toBeInTheDocument(),
    );
    expect(screen.getByText('Closed')).toBeInTheDocument();
    // CAMPAIGN was created 2026-04-15 and CHRONICLE generated 2026-05-02 →
    // span = 0 years (same calendar year). The "Span 0 years" entry is
    // optional; this assertion just confirms the keys render.
  });

  it('ck3_chronicler-9xa6: Closed reads last_event_in_game_date, never wall-clock last_event_at', async () => {
    // Smoke 2026-05-10 surfaced this: last_event_at is wall-clock
    // (datetime.now(UTC) at ingest), so 'Closed' rendered as the
    // ingestion timestamp (2026-...) against a 1066-founded campaign,
    // producing 'Span 960 years'. The 9xa6 fix adds a separate in-game
    // column and the frontispiece reads that instead. The usec attempt
    // (previous test, same name) tried to fix this at the FE layer but
    // ended up reading one wall-clock source instead of the right one.
    const campaign: CampaignResponse = {
      ...CAMPAIGN,
      bookmark_date: '1066.9.15',
      last_event_at: '2026-05-10T18:32:11+00:00', // wall-clock (red herring)
      last_event_in_game_date: '1106.6.6',         // the truth
    };
    vi.spyOn(client, 'getCampaign').mockResolvedValue(campaign);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    await screen.findByText('1066.9.15');
    // The closed date is the in-game date column.
    expect(screen.getByText('1106.6.6')).toBeInTheDocument();
    // Neither the wall-clock last_event_at nor the chronicle.generated_at
    // should leak into the visible Closed line.
    expect(screen.queryByText('2026-05-10')).not.toBeInTheDocument();
    expect(screen.queryByText('2026-05-02')).not.toBeInTheDocument();
    // Span is in-game years (40), not centuries.
    expect(screen.getByText('40 years')).toBeInTheDocument();
  });

  it('ck3_chronicler-9xa6: Closed falls back to current_in_game_date when last_event_in_game_date is null', async () => {
    // Pre-9xa6 rows whose backfill hasn't run yet still need to render a
    // reasonable Closed value. current_in_game_date (written by the cqo
    // identity denormalisation, present on most rows) is the next-best
    // source — same units as the in-game column, just less precise about
    // which event triggered the close.
    const campaign: CampaignResponse = {
      ...CAMPAIGN,
      bookmark_date: '1066.9.15',
      last_event_at: '2026-05-10T18:32:11+00:00',
      last_event_in_game_date: null,        // not yet backfilled
      current_in_game_date: '1100.12.31',    // cqo snapshot of current state
    };
    vi.spyOn(client, 'getCampaign').mockResolvedValue(campaign);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    await screen.findByText('1066.9.15');
    expect(screen.getByText('1100.12.31')).toBeInTheDocument();
    expect(screen.queryByText('2026-05-10')).not.toBeInTheDocument();
    expect(screen.getByText('34 years')).toBeInTheDocument();
  });

  it('ck3_chronicler-7xrj: H1 is the dynasty / house, current ruler appears below as smallcaps', async () => {
    const campaign: CampaignResponse = {
      ...CAMPAIGN,
      current_player_name: 'Christoffer',
      current_house_name: 'Estrid',
      founding_dynasty_name: 'Estrid',
    };
    vi.spyOn(client, 'getCampaign').mockResolvedValue(campaign);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    // Wait for the campaign payload to land — the H1 starts as '—'
    // before useCampaign resolves; assert against the eyebrow first.
    await waitFor(() =>
      expect(
        screen.getByText(/Last seen in Christoffer's hand/),
      ).toBeInTheDocument(),
    );
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveTextContent('Estrid');
  });

  it('ck3_chronicler-7xrj: omits last-ruler line when ruler equals dynasty (rare collapse)', async () => {
    // Belt-and-braces: if the dynasty resolves to the same string as
    // the current ruler (or is null and we fall through to lastRuler),
    // the eyebrow line collapses so we don't get 'Last seen in
    // <self>'s hand' redundancy under the H1.
    const campaign: CampaignResponse = {
      ...CAMPAIGN,
      current_player_name: 'Wessex',  // matches campaign.name fallback
    };
    vi.spyOn(client, 'getCampaign').mockResolvedValue(campaign);
    vi.spyOn(client, 'getClosingChronicle').mockResolvedValue(CHRONICLE);

    renderWithClient(<ClosingPage campaignName="Wessex" />);

    // Wait for the Founded line so we know the campaign payload landed.
    await waitFor(() => screen.getByText('Founded'));
    expect(screen.queryByText(/Last seen in/)).not.toBeInTheDocument();
  });
});

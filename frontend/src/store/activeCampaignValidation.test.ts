// ck3_chronicler-sgy8: unit tests for the stale-activeCampaign predicate.
// App.tsx itself isn't easy to test as a unit (renders the whole view
// router + needs EventSource shims), but the validation rule is a pure
// function and is the only thing in scope for this ticket.

import { describe, expect, it } from 'vitest';

import { shouldClearStaleActiveCampaign } from './activeCampaignValidation';
import type { CampaignResponse } from '../api/types';

function campaign(name: string, archived = false): CampaignResponse {
  return {
    id: 'id-' + name,
    name,
    ck3_version: '1.19.0',
    created_at: '2026-04-15T08:00:00+00:00',
    last_event_at: null,
    archived,
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
  };
}

describe('shouldClearStaleActiveCampaign', () => {
  it('returns false when no active campaign is persisted', () => {
    expect(shouldClearStaleActiveCampaign(null, [campaign('Saar')])).toBe(
      false,
    );
  });

  it('returns false while the campaign list is still loading', () => {
    // undefined means the useQuery hasn't resolved yet — defer the
    // decision so we don't clear prematurely during the loading flash.
    expect(shouldClearStaleActiveCampaign('Thrugot', undefined)).toBe(false);
  });

  it('returns false when the active campaign is present in the list', () => {
    expect(
      shouldClearStaleActiveCampaign('Saar', [
        campaign('Saar'),
        campaign('Thrugot'),
      ]),
    ).toBe(false);
  });

  it('returns false when the active campaign is present but archived', () => {
    // ck3_chronicler-8e0w: archived campaigns remain viewable; the
    // pill + scoped nav hide via campaignArchived prop but the user
    // can still browse the sealed history. Don't bounce them back.
    expect(
      shouldClearStaleActiveCampaign('Old Northumbria', [
        campaign('Old Northumbria', true),
      ]),
    ).toBe(false);
  });

  it('returns true when the active campaign is absent from a populated list', () => {
    // The marquee sgy8 scenario: persisted 'Thrugot' from a prior
    // session, server now has only 'Saar' (auto-detect switch or
    // post-rename).
    expect(
      shouldClearStaleActiveCampaign('Thrugot', [campaign('Saar')]),
    ).toBe(true);
  });

  it('returns true when the active campaign is absent from an empty list', () => {
    // Fresh-install / post-delete edge: server has no campaigns at all
    // but a persisted name lingers in localStorage. Same call to action.
    expect(shouldClearStaleActiveCampaign('Thrugot', [])).toBe(true);
  });
});

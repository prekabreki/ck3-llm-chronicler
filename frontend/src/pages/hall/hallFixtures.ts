// audit L34 (27ov.81): test-only Hall-of-Fame fixtures.
//
// These ~110 lines of prose rollups used to live (exported) in
// useHallOfFame.ts, which is imported by the live HallPage — so they
// rode along into the production bundle despite only ever being consumed
// by HallPage.test.tsx. Moving them into their own module that no prod
// code imports means the bundler tree-shakes them out of the app build;
// only the test pulls them in.
//
// Keep in sync with the DynastyRollup wire shape in useHallOfFame.ts.

import type { DynastyRollup } from './useHallOfFame';

export const HALL_FIXTURES: DynastyRollup[] = [
  {
    id: 'fixture-barcelona',
    playthrough_id: 'pt-1',
    dynasty_name: 'Barcelona',
    span_label: '1066 — 1184',
    span_end_label: '1184.7.4',
    span_days: 43_000,
    last_event_iso: '2026-05-04T18:30:00+00:00',
    campaigns_count: 4,
    tracked_count: 47,
    biographies_count: 18,
    blurb:
      'They united the marches of Catalonia under one Senyera, lost it twice, and reforged it once.',
    is_active: true,
    sealed_at_label: null,
    primary_campaign_name: 'Barcelona',
    coa_json: null,
    heraldry_seed: 'barcelona-senyera',
  },
  {
    id: 'fixture-ljos',
    playthrough_id: 'pt-2',
    dynasty_name: 'Ljósvetningar',
    span_label: '1066 — 1109',
    span_end_label: '1109.3.21',
    span_days: 15_700,
    last_event_iso: '2026-05-02T11:00:00+00:00',
    campaigns_count: 1,
    tracked_count: 9,
    biographies_count: 4,
    blurb:
      'A short, sharp Norse chronicle: nine souls, three exiles, one ram-horn shield.',
    is_active: true,
    sealed_at_label: null,
    primary_campaign_name: 'Ljósvetningar',
    coa_json: null,
    heraldry_seed: 'ljosvetningar',
  },
  {
    id: 'fixture-hauteville',
    playthrough_id: 'pt-3',
    dynasty_name: 'Hauteville',
    span_label: '1066 — 1147',
    span_end_label: '1147.5.1',
    span_days: 29_500,
    last_event_iso: '2026-04-28T22:00:00+00:00',
    campaigns_count: 2,
    tracked_count: 23,
    biographies_count: 11,
    blurb:
      'From Norman mercenaries to Sicilian kings — a dynasty written in iron and oranges.',
    is_active: false,
    sealed_at_label: '2026-04-28',
    primary_campaign_name: 'Hauteville',
    coa_json: null,
    heraldry_seed: 'hauteville',
  },
  {
    id: 'fixture-kotromanic',
    playthrough_id: 'pt-4',
    dynasty_name: 'Kotromanić',
    span_label: '867 — 1100',
    span_end_label: '1100.1.18',
    span_days: 85_100,
    last_event_iso: '2026-04-15T08:00:00+00:00',
    campaigns_count: 1,
    tracked_count: 18,
    biographies_count: 7,
    blurb:
      'Bosnian counts who crowned themselves twice and were excommunicated for it.',
    is_active: false,
    sealed_at_label: '2026-04-15',
    primary_campaign_name: 'Kotromanić',
    coa_json: null,
    heraldry_seed: 'kotromanic',
  },
  {
    id: 'fixture-piast',
    playthrough_id: 'pt-5',
    dynasty_name: 'Piast',
    span_label: '1066 — 1098',
    span_end_label: '1098.10.4',
    span_days: 11_800,
    last_event_iso: '2026-05-05T10:30:00+00:00',
    campaigns_count: 1,
    tracked_count: 11,
    biographies_count: 3,
    blurb:
      'Polish dukes whose eagle was painted from a real one shot above the cathedral.',
    is_active: true,
    sealed_at_label: null,
    primary_campaign_name: 'Piast',
    coa_json: null,
    heraldry_seed: 'piast',
  },
  {
    id: 'fixture-munso',
    playthrough_id: 'pt-6',
    dynasty_name: 'Munsö',
    span_label: '867 — 962',
    span_end_label: '962.6.18',
    span_days: 34_700,
    last_event_iso: '2026-04-02T09:15:00+00:00',
    campaigns_count: 1,
    tracked_count: 14,
    biographies_count: 6,
    blurb:
      'Three crowns, three kingdoms, one short-lived North Sea empire.',
    is_active: false,
    sealed_at_label: '2026-04-02',
    primary_campaign_name: 'Munsö',
    coa_json: null,
    heraldry_seed: 'munso',
  },
];

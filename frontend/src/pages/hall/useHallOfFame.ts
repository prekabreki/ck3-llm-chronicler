// useHallOfFame — TanStack Query hook for ck3_chronicler-467k.1.
//
// The Hall of Fame UI takes its data via this hook, shaped per the
// brief at design/design_handoff_chronicler_redesign/README.md §6.
//
// 467k aggregator landed: this hook now calls /api/hall-of-fame, which
// walks the registry and groups every campaign into one rollup per
// (playthrough_id, dynasty_name) tuple. The wire format is 1:1 with
// the DynastyRollup interface here so swapping the BE didn't disturb
// any consumer.
//
// audit L34 (27ov.81): the HALL_FIXTURES test fixtures moved to the
// test-only ./hallFixtures module so they're tree-shaken out of the prod
// bundle. The aggregator's grouping logic has its own coverage in
// tests/unit/test_hall_aggregator.py.

import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { fetchJson } from '../../api/client';
import { queryKeys } from '../../api/queries';
import type { CoaDefinition } from '../../components/CoaTypes';

export interface DynastyRollup {
  /** Stable identifier for the rollup. The aggregator (467k) keys
   * this off ``(playthrough_id, dynasty_name)`` joined by '|'. */
  id: string;
  /** Tied to the cross-playthrough merge UI option in the brief. Two
   * rollups with the same dynasty_name but different playthrough_ids
   * remain distinct cards by default. */
  playthrough_id: string | null;
  dynasty_name: string;
  /** Pre-formatted "1066 — 1184" style span. Computed BE-side so the
   * UI doesn't need to know about CK3 dates. */
  span_label: string;
  /** ISO end of span (or wall-clock last_event_at) for sort comparison. */
  span_end_label: string | null;
  /** Total span in days — for the "longest played" sort. None when
   * either bound is unknown. */
  span_days: number | null;
  /** Last save-tail wall-clock for the dynasty (any campaign). For
   * "most-recent" sort. */
  last_event_iso: string | null;
  /** Headline numbers — the brief calls for campaigns / souls / vitae. */
  campaigns_count: number;
  tracked_count: number;
  biographies_count: number;
  /** Closing-chronicle first paragraph if any campaign is sealed; else
   * null and the card renders the "(chronicle in progress)" placeholder. */
  blurb: string | null;
  /** True when at least one campaign for this dynasty is non-archived. */
  is_active: boolean;
  /** ISO seal date of the most-recently-archived campaign (only set
   * when is_active = false). */
  sealed_at_label: string | null;
  /** Campaign the user lands on when they click the card. */
  primary_campaign_name: string;
  /** Resolved CoA from the most-recent campaign's player character.
   * When present, the Hall card renders the real CK3 heraldry; null
   * falls through to the procedural shield via heraldry_seed. */
  coa_json: CoaDefinition | null;
  /** Procedural-shield fallback seed — ``"<playthrough_id>|<dynasty_name>"``
   * by default. Stable so the procedural renderer's output is
   * deterministic per dynasty. */
  heraldry_seed: string;
}

interface HallOfFameWire {
  rollups: DynastyRollup[];
}

const HALL_OF_FAME_URL = '/api/hall-of-fame';

export function useHallOfFame(): UseQueryResult<DynastyRollup[], Error> {
  return useQuery({
    queryKey: queryKeys.hallOfFame(),
    queryFn: async () => {
      const wire = await fetchJson<HallOfFameWire>(HALL_OF_FAME_URL);
      return wire.rollups;
    },
    // Re-fetch on focus is overkill for the Hall — the underlying
    // registry only changes on save-tail ticks, which fire roughly
    // once per in-game month. Default 30s staleTime is plenty.
    staleTime: 30_000,
  });
}

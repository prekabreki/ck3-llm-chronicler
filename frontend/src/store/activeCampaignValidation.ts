// ck3_chronicler-sgy8 (2026-05-11): boot-time validation of the
// persisted `activeCampaign` against the live registry list.
//
// Before this guard: a user whose previous session pinned
// activeCampaign='Thrugot' would, on next boot against a registry that
// has been auto-detected onto 'Saar' (or renamed via bly), see the FE
// fire three simultaneous requests against the stale name —
// /api/campaigns/<stale>, .../characters, /api/sse/ingest/<stale> —
// before the per-campaign 404 useEffect (F-02/bxte) could clear the
// store. The SSE channel was the worst hit: useEventStream needs
// MAX_CONSECUTIVE_ERRORS=3 attempts before flipping `dead`, so the user
// got a partially-loaded UI with no recovery hint.
//
// The list endpoint resolves faster than the per-campaign-with-counts
// fetch (no per-DB session opens), so validating against it cuts the
// stale-name window from "until the 404 lands" down to "until the
// fast list lands". Once cleared, the downstream queries see null and
// short-circuit (useCampaign.enabled, useEventStream.guard).
//
// Archived campaigns are still considered valid — they're viewable per
// 8e0w. The predicate keys on name presence only.

import type { CampaignResponse } from '../api/types';

export function shouldClearStaleActiveCampaign(
  activeCampaign: string | null,
  campaigns: CampaignResponse[] | undefined,
): boolean {
  if (!activeCampaign) return false;
  if (!campaigns) return false; // list still loading — defer
  return !campaigns.some((c) => c.name === activeCampaign);
}

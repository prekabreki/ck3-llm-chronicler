// ck3_chronicler-zx2l: gender-aware title-text transform. Extracted from
// PrimaryTitleLine.tsx so that component file exports only components
// (react-refresh/only-export-components — ck3_chronicler-6nhb).

import type { PrimaryTitleSummary } from '../api/types';

// Map a CK3 title-name-data string ("Kingdom of England") + tier +
// holder gender to the holder's title ("Queen of England"). Exported
// for unit testing.
//
// Strategy:
//   1. Strip the leading tier descriptor (Empire/Kingdom/Duchy/County/
//      Barony) if present, take the remainder as the geographic name.
//   2. Compose "<Title> of <Place>" using the gendered title table.
//   3. Fallback: if the source name doesn't match a known tier prefix
//      (rare localised exceptions, modded titles), use the raw name as
//      the entire display string so we always render *something* for a
//      held title. Fallback when ``name`` is null: derive from the
//      engine key (``k_england`` → "Kingdom of England" → "King of
//      England"), stripping the "k_" / "d_" prefix and title-casing.
export function formatHolderTitle(
  primaryTitle: PrimaryTitleSummary,
  female: boolean | null,
): string | null {
  const tierEntry = HOLDER_TITLE_BY_TIER[primaryTitle.tier];
  const titleWord = tierEntry ? tierEntry[female ? 'f' : 'm'] : undefined;
  if (!titleWord) {
    // Non-tiered title (e.g. court positions that slipped into
    // landed_titles). Fall back to whatever raw name we have.
    return primaryTitle.name ?? null;
  }

  const place = extractPlace(primaryTitle);
  if (!place) return titleWord;
  return `${titleWord} of ${place}`;
}

const HOLDER_TITLE_BY_TIER: Record<
  PrimaryTitleSummary['tier'],
  { m: string; f: string } | undefined
> = {
  empire: { m: 'Emperor', f: 'Empress' },
  kingdom: { m: 'King', f: 'Queen' },
  duchy: { m: 'Duke', f: 'Duchess' },
  county: { m: 'Count', f: 'Countess' },
  barony: { m: 'Baron', f: 'Baroness' },
  other: undefined,
};

const TIER_PREFIX_RE = /^(Empire|Kingdom|Duchy|County|Barony)\s+of\s+(.+)$/i;

function extractPlace(primaryTitle: PrimaryTitleSummary): string | null {
  const name = primaryTitle.name;
  if (name) {
    const m = TIER_PREFIX_RE.exec(name.trim());
    if (m && m[2]) return m[2];
    // The localised name didn't lead with a tier word — keep the
    // whole thing as the place (e.g. "Mar of Kerman", "Empire of God").
    return name;
  }
  // ``name`` absent — derive from engine key. Strip the tier prefix
  // (k_/d_/c_/b_/e_) and title-case the remainder.
  const key = primaryTitle.key;
  const stripped = /^[a-z]_(.+)$/.exec(key);
  const slug = stripped?.[1] ?? key;
  return slug
    .split('_')
    .map((part: string) =>
      part.length === 0 ? '' : part.charAt(0).toUpperCase() + part.slice(1),
    )
    .join(' ');
}

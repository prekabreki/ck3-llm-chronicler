// ck3_chronicler-zx2l: title-with-tier-crown line shared by the
// Biographies list row and the Chronicle aside namebar. Pure
// presentational; no data fetching.
//
// Gender-aware: "King of England" vs "Queen of England" driven by the
// character's ``female`` flag. Unknown gender falls back to the
// masculine form to match the rest of the folio's gender-unknown
// behaviour (biography prompt header omits the Gender line rather
// than guessing).
//
// Tier-crown source: ``/api/heraldry/assets/title_icons/<tier>.png``,
// extracted from the CK3 install by ``chronicler heraldry extract``.
// When the icon is absent (extraction not run, or the user has a
// modded install missing the tier), the <img> hides itself on error
// and the title text stands alone.

import { useState } from 'react';

import type { PrimaryTitleSummary } from '../api/types';
import { formatHolderTitle } from './titleFormatting';

interface PrimaryTitleLineProps {
  primaryTitle: PrimaryTitleSummary | null;
  female: boolean | null;
  /** Visual variant — biographies list row vs chronicle aside namebar. */
  variant?: 'biographies-row' | 'chronicle-aside';
}

export function PrimaryTitleLine({
  primaryTitle,
  female,
  variant = 'chronicle-aside',
}: PrimaryTitleLineProps): React.JSX.Element | null {
  if (!primaryTitle) return null;
  const displayText = formatHolderTitle(primaryTitle, female);
  if (!displayText) return null;
  return (
    <div className={`primary-title primary-title--${variant}`}>
      <TitleCrown tier={primaryTitle.tier} />
      <span className="primary-title__text">{displayText}</span>
    </div>
  );
}

interface HeldTitlesLinesProps {
  /** All titles held at death (grandest-first). May be undefined for rows
   *  persisted before held_titles_json existed. */
  heldTitles: PrimaryTitleSummary[] | undefined;
  /** Back-compat fallback when heldTitles is empty/undefined. */
  primaryTitle: PrimaryTitleSummary | null;
  female: boolean | null;
  variant?: 'biographies-row' | 'chronicle-aside';
}

/**
 * ck3_chronicler-9ngy: render EVERY title held at death, one crowned line
 * each (grandest-first), so a multi-realm ruler lists all their titles
 * instead of just the primary. Falls back to the single primaryTitle for
 * rows persisted before held_titles_json existed; renders nothing when
 * neither is present.
 */
export function HeldTitlesLines({
  heldTitles,
  primaryTitle,
  female,
  variant = 'chronicle-aside',
}: HeldTitlesLinesProps): React.JSX.Element | null {
  const titles =
    heldTitles && heldTitles.length > 0
      ? heldTitles
      : primaryTitle
        ? [primaryTitle]
        : [];
  if (titles.length === 0) return null;
  return (
    <>
      {titles.map((t) => (
        <PrimaryTitleLine
          key={t.key}
          primaryTitle={t}
          female={female}
          variant={variant}
        />
      ))}
    </>
  );
}

function TitleCrown({
  tier,
}: {
  tier: PrimaryTitleSummary['tier'];
}): React.JSX.Element | null {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  if (tier === 'other') return null;
  return (
    <img
      className="primary-title__crown"
      src={`/api/heraldry/assets/title_icons/${tier}.png`}
      alt=""
      aria-hidden
      onError={() => setHidden(true)}
    />
  );
}


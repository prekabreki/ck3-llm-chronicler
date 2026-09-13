// CoaTimeline — ck3_chronicler-7b8d.
//
// Renders the character's heraldry history as a small horizontal
// strip of medallions with dates underneath. Returns null when the
// history is 0 or 1 entries (no transition to display) so the strip
// only ever appears for cadet-branch / facelift transitions.

import { HeraldryWithFallback } from './RealHeraldry';
import { useCharacterCoaHistory } from '../api/queries';
import type { Palette } from './CoaTypes';

interface CoaTimelineProps {
  campaignName: string;
  ck3Id: number;
  palette: Palette | null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toISOString().slice(0, 10);
}

export function CoaTimeline({
  campaignName,
  ck3Id,
  palette,
}: CoaTimelineProps): React.JSX.Element | null {
  const historyQ = useCharacterCoaHistory(campaignName, ck3Id);
  const entries = historyQ.data?.entries ?? [];

  // Single-shield case = no story to tell, hide the timeline entirely.
  if (entries.length < 2) return null;

  return (
    <div className="coa-timeline" aria-label="Coat of arms history">
      <div className="smallcaps coa-timeline__heading">Arms over time</div>
      <ol className="coa-timeline__strip">
        {entries.map((entry, i) => (
          <li key={`${entry.observed_at}-${i}`} className="coa-timeline__item">
            <HeraldryWithFallback
              coa={entry.coa}
              palette={palette}
              seed={ck3Id + i}
              size={56}
              ring={false}
              label={`shield-history-${ck3Id}-${i}`}
            />
            <span className="coa-timeline__date">
              {formatDate(entry.observed_at)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

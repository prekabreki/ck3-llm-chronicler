// TrackedPage — the page shell. The 5-col tracked-row presentation lives
// in pages/tracked/TrackRow.tsx and the "Track new souls" rail (with its
// session-boundary / auto-track-rules / suggested-souls groups and the
// toast banner) in pages/tracked/TrackSideRail.tsx. Extracted per the
// pages/chronicle/ precedent (audit M-F10 / ck3_chronicler-27ov.64).

import { usePalette, useNarrativeQueue, useTracked } from '../api/queries';
import { TrackRow } from './tracked/TrackRow';
import { TrackSideRail } from './tracked/TrackSideRail';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function TrackedPage({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const trackedQ = useTracked(campaignName);
  const queueQ = useNarrativeQueue();
  // audit L36 (27ov.81): fetch the process-static palette once here and
  // pass it to each TrackRow, rather than every row subscribing to
  // usePalette() itself (RQ deduped it, but per-row subscription broke
  // the documented fetch-once-at-page heraldry pattern).
  const { data: palette } = usePalette();

  const tracked = trackedQ.data ?? [];

  // ck3_chronicler-nele: surface the tracked-set size + a soft warning
  // once the user has crossed a generous-but-not-bottomless threshold.
  // Soft because nothing breaks at 30+ — but the closing-chronicle pass
  // weaves a thread per tracked soul, and a 50-tracked campaign will
  // produce a sprawling closing that takes minutes of Opus to render.
  const TRACKED_WARN_THRESHOLD = 30;
  const count = tracked.length;
  const overWarn = count > TRACKED_WARN_THRESHOLD;

  return (
    <div className="tracked-page">
      <div className="tracked-page__inner">
        <header className="tracked-page__header">
          <div className="smallcaps tracked-page__eyebrow">
            The chronicler's flock
          </div>
          <h1 className="uncial tracked-page__title">
            Tracked Souls
            {count > 0 && (
              <span
                className={
                  'tracked-page__count' +
                  (overWarn ? ' tracked-page__count--warn' : '')
                }
                aria-label={`${count} souls tracked`}
              >
                {count}
              </span>
            )}
          </h1>
          <p className="italic-fell tracked-page__lede">
            Auto-track watches the player and their immediate kin.
            Add others by name; remove any you no longer wish followed.
          </p>
          {overWarn && (
            <p
              className="italic-fell tracked-page__warn"
              role="status"
              aria-live="polite"
            >
              {count} souls is a generous flock — the closing chronicle
              weaves a thread per tracked soul, so a final pass at this
              scale may sprawl. Consider pausing or untracking the
              quietest before completion.
            </p>
          )}
        </header>

        <div className="tracked-grid">
          <div className="tracked-grid__main">
            {trackedQ.isLoading && (
              <p className="italic-fell tracked-page__status">
                Drawing the flock…
              </p>
            )}
            {trackedQ.isError && (
              <p
                className="italic-fell tracked-page__status tracked-page__status--error"
                role="alert"
              >
                Failed to read the tracked roster.
              </p>
            )}
            {!trackedQ.isLoading && !trackedQ.isError && tracked.length === 0 && (
              <p className="italic-fell tracked-page__empty">
                No characters yet tracked in this campaign. Use{' '}
                <strong>Auto-track from save</strong> in the rail to add
                the player and immediate kin, or{' '}
                <strong>Add by ID…</strong> to track a specific character.
              </p>
            )}
            {!trackedQ.isLoading && !trackedQ.isError && tracked.length > 0 && (
              <div className="tracked-list">
                {tracked.map((t) => (
                  <TrackRow
                    key={t.character_id}
                    campaignName={campaignName}
                    t={t}
                    queue={queueQ.data}
                    palette={palette ?? null}
                  />
                ))}
              </div>
            )}
          </div>
          <TrackSideRail campaignName={campaignName} count={tracked.length} />
        </div>
      </div>
    </div>
  );
}

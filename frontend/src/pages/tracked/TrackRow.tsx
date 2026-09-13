// ck3_chronicler-27ov.64 (audit M-F10): the tracked-row presentation —
// 5-col grid (shield | name+sub | status | progress | actions) plus the
// contextual Pause/Resume/Bump buttons. Extracted from TrackedPage per
// the pages/chronicle/ precedent.
import { HeraldryWithFallback } from '../../components/RealHeraldry';
import type { Palette } from '../../components/CoaTypes';
import { deriveCharacterStatus } from '../../util/characterStatus';
import { formatDate, formatTokens } from '../../util/format';
import type { CharacterStatus } from '../../util/characterStatus';
import { UntrackButton } from '../../components/UntrackButton';
import {
  useBumpTracked,
  usePauseTracked,
  useResumeTracked,
} from '../../api/queries';
import { useAppStore } from '../../store/appStore';
import type {
  NarrativeQueueResponse,
  TrackedResponse,
} from '../../api/types';

const ROLE_BADGE: Record<
  string,
  { label: string; cls: string }
> = {
  player: { label: 'Player', cls: 'tracked-badge--player' },
  spouse: { label: 'Spouse', cls: 'tracked-badge--spouse' },
  heir: { label: 'Heir', cls: 'tracked-badge--heir' },
  family: { label: 'Family', cls: 'tracked-badge--family' },
  manual: { label: 'By hand', cls: 'tracked-badge--manual' },
};

type RowState = 'live' | 'good' | 'warn' | 'muted';

// Presentation-only mapping of the shared character status onto the
// 5-col row grid (pip tint + label + progress). The status semantics
// live in ONE place — StatusChip.deriveCharacterStatus (M-F5/27ov.60).
// Progress: bio drafted = 100%, queued/generating = 60%, paused = 0%,
// tracking-no-bio = 5% so the bar still reads as "armed".
const ROW_VIEW: Record<
  CharacterStatus,
  { state: RowState; label: string; progressPct: number }
> = {
  live: { state: 'warn', label: 'Generating', progressPct: 60 },
  queued: { state: 'warn', label: 'In queue', progressPct: 60 },
  paused: { state: 'muted', label: 'Paused', progressPct: 0 },
  drafted: { state: 'good', label: 'Bio drafted', progressPct: 100 },
  tracking: { state: 'live', label: 'Tracking', progressPct: 5 },
};

export function TrackRow({
  campaignName,
  t,
  queue,
  palette,
}: {
  campaignName: string;
  t: TrackedResponse;
  queue: NarrativeQueueResponse | undefined;
  // audit L36 (27ov.81): palette fetched once by the page, passed down.
  palette: Palette | null;
}): React.JSX.Element {
  const setSelected = useAppStore((s) => s.setSelectedCharacterId);
  const setView = useAppStore((s) => s.setView);
  // ck3_chronicler-4y0v slice 1: real CoA on the tracked-row shield.
  // t is always a tracked row, so the derivation never returns null.
  const status = deriveCharacterStatus(t.character_id, queue, t) ?? 'tracking';
  const view = ROW_VIEW[status];
  const badge = ROLE_BADGE[t.role ?? ''] ?? ROLE_BADGE.manual!;

  const onOpen = (): void => {
    setSelected(t.character_id);
    setView('chronicle');
  };

  return (
    <div className="track-row">
      <div className="track-row__shield">
        <HeraldryWithFallback
          coa={t.coa_json}
          palette={palette ?? null}
          seed={t.character_id}
          size={56}
          label={`shield-${t.character_id}`}
        />
      </div>
      <div className="track-row__head">
        <div className="track-row__heading">
          <span className="track-row__name">
            {t.first_name ?? `Character ${t.character_id}`}
          </span>
          {t.nickname && (
            <span className="italic-fell track-row__epithet">
              {t.nickname}
            </span>
          )}
          <span className={`tracked-badge ${badge.cls}`}>{badge.label}</span>
        </div>
        <div className="track-row__sub">
          <b>#{t.character_id}</b>
          <span>since {formatDate(t.added_at)}</span>
          {' · '}
          {formatTokens(t.monthly_token_spend)} tok / mo
        </div>
      </div>
      <div className={`track-row__status track-row__status--${view.state}`}>
        <span className="pip">
          <span className={`pip__dot pip__dot--${pipDotClass(view.state)}`} />
          {view.label}
        </span>
      </div>
      <div className="track-row__progress">
        <div
          className={`track-row__progress-bar track-row__progress-bar--${view.state}`}
        >
          <i style={{ width: `${view.progressPct}%` }} />
        </div>
        <div className="track-row__progress-label">
          {t.biography_count > 0
            ? `${t.biography_count} vit${t.biography_count === 1 ? 'a' : 'æ'}`
            : 'Vita pending'}
        </div>
      </div>
      <div className="track-row__actions">
        <ContextualActions campaignName={campaignName} t={t} status={status} />
        <button
          type="button"
          className="btn btn--quiet track-row__open"
          onClick={onOpen}
        >
          Open
        </button>
        <UntrackButton campaignName={campaignName} tracked={t} />
      </div>
    </div>
  );
}

function pipDotClass(state: RowState): string {
  switch (state) {
    case 'live':
      return 'alive';
    case 'good':
      return 'azure';
    case 'warn':
      return 'gold';
    case 'muted':
      return 'idle';
  }
}

function ContextualActions({
  campaignName,
  t,
  status,
}: {
  campaignName: string;
  t: TrackedResponse;
  status: CharacterStatus;
}): React.JSX.Element | null {
  const pauseMu = usePauseTracked(campaignName);
  const resumeMu = useResumeTracked(campaignName);
  const bumpMu = useBumpTracked(campaignName);

  if (status === 'paused') {
    // ck3_chronicler-qk84: paused rows offer both Resume (clears
    // paused_at only) and Bump (resume + bumped_at priority for the
    // deferred-drain queue). Without Bump here the t2v5 path
    // pause→bump→drain-replay-first cannot be exercised from the UI.
    return (
      <>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={() => resumeMu.mutate(t.character_id)}
          disabled={resumeMu.isPending}
        >
          {resumeMu.isPending ? 'Resuming…' : 'Resume'}
        </button>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={() => bumpMu.mutate(t.character_id)}
          disabled={bumpMu.isPending}
        >
          {bumpMu.isPending ? 'Bumping…' : 'Bump'}
        </button>
      </>
    );
  }
  if (status === 'live' || status === 'queued') {
    return (
      <button
        type="button"
        className="btn btn--quiet"
        onClick={() => bumpMu.mutate(t.character_id)}
        disabled={bumpMu.isPending}
      >
        {bumpMu.isPending ? 'Bumping…' : 'Bump'}
      </button>
    );
  }
  // tracking or drafted → offer Pause
  return (
    <button
      type="button"
      className="btn btn--quiet"
      onClick={() => pauseMu.mutate(t.character_id)}
      disabled={pauseMu.isPending}
    >
      {pauseMu.isPending ? 'Pausing…' : 'Pause'}
    </button>
  );
}

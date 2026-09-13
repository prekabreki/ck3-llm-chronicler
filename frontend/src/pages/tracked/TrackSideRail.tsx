// ck3_chronicler-27ov.64 (audit M-F10): the "Track new souls" right rail
// and its groups (session boundary, auto-track rules, suggested souls),
// extracted from TrackedPage per the pages/chronicle/ precedent. The rail
// owns the toast state and renders the banner; child groups raise toasts
// via useToast() rather than the onToast/onError props the audit flagged.
import { useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';

import { AddTrackedModal } from '../../components/AddTrackedModal';
import { NameSearchTypeahead } from '../../components/NameSearchTypeahead';
import {
  addTracked,
  getAutoTrackRules,
  listSuggestedCandidates,
  updateAutoTrackRules,
} from '../../api/client';
import {
  queryKeys,
  useAutoTrack,
  useEndSession,
  useResetSession,
} from '../../api/queries';
import type {
  AutoTrackRules,
  AutoTrackRulesUpdate,
  SuggestedCandidate,
} from '../../api/types';
import { ToastContext, useToast, useToastState } from './useToast';

export function TrackSideRail({
  campaignName,
  count,
}: {
  campaignName: string;
  count: number;
}): React.JSX.Element {
  const autoTrackMutation = useAutoTrack(campaignName);
  const [addOpen, setAddOpen] = useState(false);
  // Rail owns the toast state; child groups consume it via useToast().
  const { toast, toastError, api } = useToastState();

  const onAutoTrack = (): void => {
    autoTrackMutation.mutate(undefined, {
      onSuccess: (result) => {
        const addedCount = result.added.length;
        const skipped = result.already_tracked.length;
        const fileLabel = result.save_path.split(/[\\/]/).pop() ?? result.save_path;
        if (addedCount === 0 && skipped === 0) {
          api.showToast(`No candidates found in ${fileLabel}.`);
        } else if (addedCount === 0) {
          api.showToast(`No new souls — all ${skipped} candidates already tracked.`);
        } else {
          api.showToast(
            `Tracked ${addedCount} new soul${addedCount === 1 ? '' : 's'} from ${fileLabel}` +
              (skipped > 0 ? ` (${skipped} already tracked).` : '.'),
          );
        }
      },
      onError: (err) => {
        api.showError(err instanceof Error ? err.message : String(err));
      },
    });
  };

  return (
    <ToastContext.Provider value={api}>
      <aside className="track-side">
        <div className="smallcaps track-side__eyebrow">+ Track new souls</div>
        <h3 className="track-side__title">{count} tracked</h3>

        <div className="track-side__grp">
          <label htmlFor="track-name-search">Search by name</label>
          <NameSearchTypeahead
            campaignName={campaignName}
            onTracked={(name) => api.showToast(`Tracking ${name}.`)}
            onError={(msg) => api.showError(msg)}
          />
        </div>

        <div className="track-side__grp">
          <button
            type="button"
            className="btn track-side__primary"
            onClick={onAutoTrack}
            disabled={autoTrackMutation.isPending}
          >
            <span className="tracked-toolbar__glyph">✦</span>
            {autoTrackMutation.isPending ? 'Reading save…' : 'Auto-track from save'}
          </button>
          <button
            type="button"
            className="btn btn--quiet track-side__secondary"
            onClick={() => setAddOpen(true)}
          >
            Add by ID…
          </button>
        </div>

        <SuggestedCandidatesGroup campaignName={campaignName} />

        <AutoTrackRulesGroup campaignName={campaignName} />

        <SessionBoundaryGroup campaignName={campaignName} />

        {toast && (
          <p className="italic-fell tracked-page__toast" role="status">
            {toast}
          </p>
        )}
        {toastError && (
          <p
            className="italic-fell tracked-page__toast tracked-page__toast--error"
            role="alert"
          >
            {toastError}
          </p>
        )}
        {addOpen && (
          <AddTrackedModal
            campaignName={campaignName}
            onClose={() => setAddOpen(false)}
          />
        )}
      </aside>
    </ToastContext.Provider>
  );
}

// ck3_chronicler-0224: session-boundary controls on the Tracked rail.
// "End session" marks the session boundary — the consolidation sweep was
// removed (plan: cozy-coalescing-shannon); returns 0 tracked_considered.
// "Reset session" zeros the dual-meter's session bucket (lifetime stays
// untouched) so the user can mark a fresh play session manually.
function SessionBoundaryGroup({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const { showToast, showError } = useToast();
  const endMu = useEndSession(campaignName);
  const resetMu = useResetSession(campaignName);

  const onEnd = (): void => {
    endMu.mutate(undefined, {
      onSuccess: () => {
        showToast('Session boundary marked.');
      },
      onError: (err) => showError(err instanceof Error ? err.message : String(err)),
    });
  };

  const onReset = (): void => {
    resetMu.mutate(undefined, {
      onSuccess: () => showToast('Session counter reset — lifetime unchanged.'),
      onError: (err) => showError(err instanceof Error ? err.message : String(err)),
    });
  };

  return (
    <div className="track-side__grp">
      <div className="smallcaps track-side__eyebrow">Session boundary</div>
      <button
        type="button"
        className="btn track-side__primary"
        onClick={onEnd}
        disabled={endMu.isPending}
        title="Mark the session boundary"
      >
        <span className="tracked-toolbar__glyph">❦</span>
        {endMu.isPending ? 'Ending…' : 'End session'}
      </button>
      <button
        type="button"
        className="btn btn--quiet track-side__secondary"
        onClick={onReset}
        disabled={resetMu.isPending}
        title="Zero the session-bucket of the dual meter (lifetime unaffected)"
      >
        {resetMu.isPending ? 'Resetting…' : 'Reset session counter'}
      </button>
    </div>
  );
}

// ck3_chronicler-gw16: per-campaign auto-track rule checkboxes.
// Reads the persisted JSON via GET /auto-track-rules; toggling a
// checkbox writes through PUT with optimistic UI. The third checkbox
// (county-tier vassals) defaults off — tracking the player's entire
// de-jure county-tier vassalage can balloon the tracked-character
// roll, so opt-in.
function AutoTrackRulesGroup({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const qc = useQueryClient();
  const rulesKey = queryKeys.autoTrackRules(campaignName);
  const rulesQ = useQuery<AutoTrackRules>({
    queryKey: rulesKey,
    queryFn: () => getAutoTrackRules(campaignName),
  });
  const updateMu = useMutation({
    mutationFn: (body: AutoTrackRulesUpdate) =>
      updateAutoTrackRules(campaignName, body),
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: rulesKey });
      const previous = qc.getQueryData<AutoTrackRules>(rulesKey);
      if (previous) {
        qc.setQueryData<AutoTrackRules>(rulesKey, { ...previous, ...body });
      }
      return { previous };
    },
    onError: (_err, _body, ctx) => {
      // Roll back on failure so the checkbox snaps back to its real state.
      if (ctx?.previous) {
        qc.setQueryData(rulesKey, ctx.previous);
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: rulesKey });
    },
  });

  const rules = rulesQ.data;
  const loading = rulesQ.isLoading;

  return (
    <div className="track-side__grp">
      <label>Auto-track rules</label>
      <div className="track-side__rules">
        <label className="track-side__rule">
          <input
            type="checkbox"
            checked={rules?.include_heirs ?? true}
            disabled={loading || updateMu.isPending}
            onChange={(e) =>
              updateMu.mutate({ include_heirs: e.target.checked })
            }
          />
          <span>Direct heirs</span>
        </label>
        <label className="track-side__rule">
          <input
            type="checkbox"
            checked={rules?.include_grandchildren ?? true}
            disabled={loading || updateMu.isPending}
            onChange={(e) =>
              updateMu.mutate({ include_grandchildren: e.target.checked })
            }
          />
          <span>Grandchildren</span>
        </label>
        <label className="track-side__rule">
          <input
            type="checkbox"
            checked={rules?.include_spouses ?? true}
            disabled={loading || updateMu.isPending}
            onChange={(e) =>
              updateMu.mutate({ include_spouses: e.target.checked })
            }
          />
          <span>Spouses</span>
        </label>
        <label className="track-side__rule">
          <input
            type="checkbox"
            checked={rules?.include_county_vassals ?? false}
            disabled={loading || updateMu.isPending}
            onChange={(e) =>
              updateMu.mutate({ include_county_vassals: e.target.checked })
            }
          />
          <span>County-tier vassals</span>
        </label>
      </div>
    </div>
  );
}

// ck3_chronicler-gw16: living-untracked relatives surfaced for one-tap
// tracking. Hidden when there are none, when the campaign has no
// player ID resolved yet, or while the query is loading.
function SuggestedCandidatesGroup({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element | null {
  const { showToast, showError } = useToast();
  const qc = useQueryClient();
  const candQ = useQuery<SuggestedCandidate[]>({
    queryKey: queryKeys.suggestedCandidates(campaignName),
    queryFn: () => listSuggestedCandidates(campaignName, { limit: 5 }),
  });
  const trackMu = useMutation({
    mutationFn: (cand: SuggestedCandidate) =>
      addTracked(campaignName, {
        character_id: cand.ck3_id,
        note: cand.relation,
      }),
    onSuccess: (_resp, cand) => {
      showToast(`Tracking ${cand.first_name ?? `#${cand.ck3_id}`}.`);
      qc.invalidateQueries({ queryKey: queryKeys.tracked(campaignName) });
      qc.invalidateQueries({
        queryKey: queryKeys.suggestedCandidates(campaignName),
      });
    },
    onError: (err) => {
      showError(err instanceof Error ? err.message : String(err));
    },
  });

  const data = candQ.data ?? [];
  if (candQ.isLoading || data.length === 0) {
    return null;
  }

  return (
    <div className="track-side__grp">
      <label>Suggested souls</label>
      <ul className="suggested-souls">
        {data.map((cand) => (
          <li key={cand.ck3_id} className="suggested-souls__row">
            <div className="suggested-souls__who">
              <span className="suggested-souls__name">
                {cand.first_name ?? `#${cand.ck3_id}`}
              </span>
              <span className="suggested-souls__relation">
                {cand.relation}
              </span>
            </div>
            <button
              type="button"
              className="btn btn--quiet suggested-souls__track"
              onClick={() => trackMu.mutate(cand)}
              disabled={trackMu.isPending}
            >
              Track
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

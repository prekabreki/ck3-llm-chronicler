// CampaignCard — one shelf entry on the Library, with its inline rename,
// reimport/export, reset-baseline, and delete affordances. Extracted from
// LibraryPage.tsx by audit M-F3 / ck3_chronicler-27ov.58 (the card alone
// was ~318 lines with three mutation flows). The two confirm modals it
// owns ride on the shared ConfirmDialog (audit M-F2 / 27ov.57).

import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { deleteCampaign } from '../../api/client';
import {
  queryKeys,
  useRenameCampaign,
  useResetBaseline,
} from '../../api/queries';
import type { CampaignResponse } from '../../api/types';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import type { Palette } from '../../components/CoaTypes';
import { ExportChronicleButton } from '../../components/ExportChronicleButton';
import { Banner } from '../../components/Heraldry';
import { RealHeraldry } from '../../components/RealHeraldry';
import { useAppStore } from '../../store/appStore';
import { formatRelativeIso } from '../../util/format';
import { formatByline, formatInGameSpan, truncate } from './formatters';

interface CampaignCardProps {
  campaign: CampaignResponse;
  palette: Palette | null;
  onOpen: () => void;
  onImport?: () => void;
  completed?: boolean;
  needsMigration?: boolean;
}

export function CampaignCard({
  campaign: c,
  palette,
  onOpen,
  onImport,
  completed = false,
  needsMigration = false,
}: CampaignCardProps): React.JSX.Element {
  const byline = formatByline(c);
  const inGame = formatInGameSpan(c);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(c.name);
  const [warning, setWarning] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const renameMutation = useRenameCampaign();

  const qc = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const activeCampaign = useAppStore((s) => s.activeCampaign);
  const deleteMutation = useMutation({
    mutationFn: () => deleteCampaign(c.name),
    onSuccess: () => {
      if (activeCampaign === c.name) {
        setActiveCampaign(null);
      }
      qc.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
      setConfirmingDelete(false);
    },
    onError: (err) => {
      setDeleteError(err instanceof Error ? err.message : String(err));
    },
  });

  // ck3_chronicler-yv8q: button-driven recovery for wedged save-tail
  // baselines. The confirm modal explains the intent; on success the
  // toast persists until the user dismisses (or until the next render
  // cycle clears it through interaction).
  const [confirmingResetBaseline, setConfirmingResetBaseline] = useState(false);
  const [baselineToast, setBaselineToast] = useState<string | null>(null);
  const [baselineError, setBaselineError] = useState<string | null>(null);
  const resetBaselineMutation = useResetBaseline();
  const onConfirmResetBaseline = (): void => {
    resetBaselineMutation.mutate(c.name, {
      onSuccess: (resp) => {
        setBaselineToast(
          resp.deleted
            ? 'Baseline cleared. Save-tail will re-baseline on the next save.'
            : 'No baseline file was present — nothing to clear.',
        );
        setBaselineError(null);
        setConfirmingResetBaseline(false);
      },
      onError: (err) => {
        setBaselineError(err instanceof Error ? err.message : String(err));
      },
    });
  };

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const startEditing = (): void => {
    setDraft(c.name);
    setWarning(null);
    setIsEditing(true);
  };
  const cancelEditing = (): void => {
    setIsEditing(false);
    setDraft(c.name);
  };
  const commitEditing = (): void => {
    const next = draft.trim();
    if (!next || next === c.name) {
      cancelEditing();
      return;
    }
    renameMutation.mutate(
      { currentName: c.name, newName: next },
      {
        onSuccess: (resp) => {
          setWarning(resp.warning);
          setIsEditing(false);
        },
        onError: () => { /* leave open on error */ },
      },
    );
  };

  const chipLabel = completed ? 'Sealed' : 'Active';
  const chipClass = completed ? 'lib-card__chip--archived' : '';

  // ck3_chronicler-vgcp: card previously declared role="button" + tabIndex
  // on the <article> but nested four interactive controls (Rename, Reimport,
  // Export, Delete) — a WAI-ARIA violation. Screen readers either refuse to
  // announce or collapse the children. Pattern now: keep the article semantic,
  // overlay a single transparent <button> on the body region (chip → counts)
  // for the Open affordance, and let the action menu live as a higher-z-index
  // sibling so its buttons remain interactive. The overlay is suppressed
  // while the name input is open so editing keystrokes reach the input.
  return (
    <article
      className={'lib-card' + (completed ? ' lib-card--completed' : '')}
    >
      {!isEditing && (
        <button
          type="button"
          className="lib-card__open"
          onClick={onOpen}
          aria-label={`Open campaign ${c.name}`}
        />
      )}
      <span className={`lib-card__chip ${chipClass}`}>{chipLabel}</span>

      <div className="lib-card__head">
        <div className="lib-card__shield">
          {c.current_player_coa_json && palette ? (
            <RealHeraldry
              coa={c.current_player_coa_json}
              palette={palette}
              size={72}
              label={`Arms of ${c.current_player_name ?? c.name}`}
            />
          ) : (
            <Banner seed={c.name} size={64} />
          )}
        </div>
        <div className="lib-card__head-text">
          {isEditing ? (
            <input
              ref={inputRef}
              type="text"
              className="lib-card__name lib-card__name-input"
              value={draft}
              aria-label={`Campaign name for ${c.name}`}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitEditing();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelEditing();
                }
              }}
              onBlur={commitEditing}
            />
          ) : (
            <div className="lib-card__name">{c.name}</div>
          )}
          {byline && <div className="lib-card__byline">{byline}</div>}
          {warning && (
            <div className="italic-fell lib-card__rename-warning" role="status">
              {warning}
            </div>
          )}
        </div>
      </div>

      {inGame && <span className="lib-card__span">{inGame}</span>}

      {completed && c.closing_chronicle_blurb ? (
        <p className="lib-card__blurb">
          {truncate(c.closing_chronicle_blurb, 280)}
        </p>
      ) : (
        <p className="lib-card__blurb italic-fell">
          {c.last_event_at
            ? `Last event ${formatRelativeIso(c.last_event_at)}.`
            : 'No events yet — adopt a save to begin chronicling.'}
        </p>
      )}

      {c.last_save_ingested_at && (
        <p className="italic-fell lib-card__last-tick">
          {c.last_tick_event_count ?? 0} event
          {(c.last_tick_event_count ?? 0) === 1 ? '' : 's'} in last save tick ·{' '}
          {formatRelativeIso(c.last_save_ingested_at)}
        </p>
      )}

      <div className="lib-card__counts">
        <div className="lib-card__count">
          <span className="lib-card__count-n">
            {c.counts ? c.counts.characters : '—'}
          </span>
          <span className="lib-card__count-l">Souls</span>
        </div>
        <div className="lib-card__count">
          <span className="lib-card__count-n">
            {c.counts ? c.counts.biographies : '—'}
          </span>
          <span className="lib-card__count-l">Vitæ</span>
        </div>
      </div>

      {needsMigration && (
        <span className="lib-card__pill lib-card__pill--migrate">
          Needs migration
        </span>
      )}

      <div className="lib-card__menu">
        <button
          type="button"
          className="btn btn--quiet btn--sm"
          onClick={(e) => {
            e.stopPropagation();
            startEditing();
          }}
          aria-label={`Rename campaign ${c.name}`}
          title="Rename the display name only — closing-chronicle text is unaffected."
        >
          Rename…
        </button>
        {onImport && !completed && (
          <button
            type="button"
            className="btn btn--quiet btn--sm"
            onClick={(e) => {
              e.stopPropagation();
              onImport();
            }}
          >
            Reimport
          </button>
        )}
        {completed && (
          <>
            <ExportChronicleButton
              campaignName={c.name}
              variant="quiet"
              size="sm"
              compact
              format="markdown"
            />
            <ExportChronicleButton
              campaignName={c.name}
              variant="quiet"
              size="sm"
              compact
              format="pdf"
            />
          </>
        )}
        <button
          type="button"
          className="btn btn--quiet btn--sm"
          onClick={(e) => {
            e.stopPropagation();
            setBaselineError(null);
            setBaselineToast(null);
            setConfirmingResetBaseline(true);
          }}
          aria-label={`Reset save-tail baseline for ${c.name}`}
          title="Clear the chronicler's memory of the previous save state. Use only if you see 'playthrough mismatch' errors."
        >
          Reset baseline…
        </button>
        <button
          type="button"
          className="btn btn--quiet btn--sm lib-card__delete"
          onClick={(e) => {
            e.stopPropagation();
            setDeleteError(null);
            setConfirmingDelete(true);
          }}
          aria-label={`Delete campaign ${c.name}`}
          title="Permanently delete this campaign — biographies will be lost."
        >
          Delete…
        </button>
      </div>
      {baselineToast && (
        <p
          className="italic-fell lib-card__rename-warning"
          role="status"
          onClick={(e) => {
            e.stopPropagation();
            setBaselineToast(null);
          }}
        >
          {baselineToast}
        </p>
      )}
      {confirmingDelete && (
        <DeleteCampaignConfirm
          campaignName={c.name}
          onCancel={() => {
            setConfirmingDelete(false);
            setDeleteError(null);
          }}
          onConfirm={() => deleteMutation.mutate()}
          submitting={deleteMutation.isPending}
          error={deleteError}
        />
      )}
      {confirmingResetBaseline && (
        <ResetBaselineConfirm
          campaignName={c.name}
          onCancel={() => {
            setConfirmingResetBaseline(false);
            setBaselineError(null);
          }}
          onConfirm={onConfirmResetBaseline}
          submitting={resetBaselineMutation.isPending}
          error={baselineError}
        />
      )}
    </article>
  );
}

function DeleteCampaignConfirm({
  campaignName,
  onCancel,
  onConfirm,
  submitting,
  error,
}: {
  campaignName: string;
  onCancel: () => void;
  onConfirm: () => void;
  submitting: boolean;
  error: string | null;
}): React.JSX.Element {
  return (
    <ConfirmDialog
      titleId="delete-modal-title"
      eyebrow="⚠ Permanently delete"
      title={`Erase ${campaignName}?`}
      confirmLabel="Delete permanently"
      busyLabel="Deleting…"
      onConfirm={onConfirm}
      onCancel={onCancel}
      busy={submitting}
      error={error}
    >
      <p className="italic-fell modal__lede">
        This will remove the campaign's registry row, biographies,
        tracked souls, and the per-campaign database file. The
        action cannot be undone.
      </p>
    </ConfirmDialog>
  );
}

// ck3_chronicler-yv8q: confirm modal for the wedged-baseline recovery
// button. Shares the ConfirmDialog shape with the destructive delete
// confirm so the Library card has a single visual vocabulary — but the
// copy and eyebrow are intentionally non-alarming because this action
// loses no event data; the baseline is a re-derivable memo, not state.
function ResetBaselineConfirm({
  campaignName,
  onCancel,
  onConfirm,
  submitting,
  error,
}: {
  campaignName: string;
  onCancel: () => void;
  onConfirm: () => void;
  submitting: boolean;
  error: string | null;
}): React.JSX.Element {
  return (
    <ConfirmDialog
      titleId="reset-baseline-modal-title"
      eyebrow="Reset save-tail baseline"
      title={`Clear baseline for ${campaignName}?`}
      confirmLabel="Clear baseline"
      busyLabel="Clearing…"
      onConfirm={onConfirm}
      onCancel={onCancel}
      busy={submitting}
      error={error}
    >
      <p className="italic-fell modal__lede">
        This clears the chronicler's memory of the previous save
        state. Save-tail will re-baseline on the next matching save.
        Use only if you see &lsquo;playthrough mismatch&rsquo;
        errors in the boot log.
      </p>
      <p className="italic-fell modal__lede">
        No event data is lost — events live in the campaign
        database, not the baseline file.
      </p>
    </ConfirmDialog>
  );
}

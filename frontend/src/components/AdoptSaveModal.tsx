// AdoptSaveModal — ck3_chronicler-v2a / nji "+ Adopt save" button.
//
// Wire flow:
//   1. User pastes a CK3 save path.
//   2. POST /api/campaigns/adopt-from-save → resolves a campaign
//      (existing or new), runs alembic + import_save, and (when
//      chronicler dev is running in nji's lazy-bind mode) spawns the
//      save_ingest task for the new campaign.
//   3. On success: invalidate the campaign list query so the new card
//      materialises in the Library; close the modal.
//
// Errors map to user-actionable copy:
//   404: file not found at that path on the server
//   400: rakaly couldn't parse it (corrupt save / mod schema drift)
//   409: playthrough mismatch — UI offers a "force reset" retry
//   500: alembic / import_save crash; show the server message verbatim
// Anything else: the raw error message.

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { ApiError, adoptSaveFromPath } from '../api/client';
import { queryKeys } from '../api/queries';
import type { SaveFileInfo } from '../api/types';
import { ModalShell } from './ModalShell';
import { SavePicker } from './SavePicker';

interface AdoptSaveModalProps {
  open: boolean;
  onClose: () => void;
}

export function AdoptSaveModal({
  open,
  onClose,
}: AdoptSaveModalProps): React.JSX.Element | null {
  const qc = useQueryClient();
  const [savePath, setSavePath] = useState('');
  const [allowReset, setAllowReset] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusCode, setStatusCode] = useState<number | null>(null);

  const adoptMutation = useMutation({
    mutationFn: (path: string) => adoptSaveFromPath(path, allowReset),
    onSuccess: (result) => {
      // Invalidate every shape of campaigns query the Library uses.
      void qc.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
      void qc.invalidateQueries({
        queryKey: queryKeys.campaignAll(result.campaign.name),
      });
      // Close on success — the new card will appear in the Library
      // grid from the invalidated query.
      closeAll();
    },
    onError: (err: unknown) => {
      // audit F-10: was parsing the leading status code out of err.message
      // via /^(\d{3})[:\s]/ — but client.ts throws ApiError whose .message
      // is the bare detail string with no "409 " prefix, so the 409 branch
      // (force-reset checkbox) never rendered. Read err.status directly.
      const code = err instanceof ApiError ? err.status : null;
      const message = err instanceof Error ? err.message : String(err);
      setStatusCode(code);
      setError(message);
    },
  });

  // ck3_chronicler-mb6q follow-up: clicking a SavePicker row fills the
  // input + auto-submits. setSavePath alone wouldn't kick off the
  // adopt mutation; pass the path explicitly so we don't race the
  // setState round-trip.
  const onPickRow = (info: SaveFileInfo): void => {
    setSavePath(info.abs_path);
    setError(null);
    setStatusCode(null);
    adoptMutation.mutate(info.abs_path);
  };

  const closeAll = (): void => {
    setSavePath('');
    setAllowReset(false);
    setError(null);
    setStatusCode(null);
    onClose();
  };

  if (!open) return null;

  const onSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    setError(null);
    setStatusCode(null);
    adoptMutation.mutate(savePath.trim());
  };

  return (
    <ModalShell
      titleId="adopt-modal-title"
      eyebrow="+ Adopt a save"
      title="Onboard a CK3 save"
      onClose={closeAll}
    >
        <form className="modal__body" onSubmit={onSubmit}>
          <p className="italic-fell modal__lede">
            Point chronicler at a CK3 save file. We'll resolve it to an
            existing campaign by playthrough id, or create a new one if it's
            unrecognised. The file must be readable by the chronicler
            process (this is single-user, so "your machine").
          </p>
          <SavePicker
            enabled={open && !adoptMutation.isPending}
            onPick={onPickRow}
          />
          <label className="modal__field">
            <span className="smallcaps modal__field-label">
              Or paste a path to a save outside that folder
            </span>
            <input
              type="text"
              className="modal__input"
              value={savePath}
              onChange={(e) => setSavePath(e.target.value)}
              placeholder="C:\Users\you\Documents\Paradox Interactive\…\autosave.ck3"
              disabled={adoptMutation.isPending}
            />
          </label>
          {statusCode === 409 && (
            <label className="modal__field">
              <input
                type="checkbox"
                checked={allowReset}
                onChange={(e) => setAllowReset(e.target.checked)}
                disabled={adoptMutation.isPending}
              />
              <span className="italic-fell modal__field-hint">
                {' '}Force playthrough reset (overwrites the existing pin)
              </span>
            </label>
          )}
          <div className="modal__actions">
            <button
              type="submit"
              className="btn"
              disabled={adoptMutation.isPending || !savePath.trim()}
            >
              {adoptMutation.isPending ? 'Adopting…' : 'Adopt save'}
            </button>
            <button
              type="button"
              className="btn btn--quiet"
              onClick={closeAll}
              disabled={adoptMutation.isPending}
            >
              Cancel
            </button>
          </div>
          {error && (
            <p className="italic-fell modal__error" role="alert">
              {error}
            </p>
          )}
        </form>
    </ModalShell>
  );
}

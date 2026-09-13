// audit F-29 / ck3_chronicler-vgzs: extracted from TrackedPage.tsx.
// The button + its confirm dialog co-locate so callers (the row grid)
// just render <UntrackButton tracked={t} campaignName={...} />. The
// confirm shell + a11y wiring live in ConfirmDialog (audit M-F2 /
// ck3_chronicler-27ov.57).

import { useState } from 'react';

import { useDeleteTracked } from '../api/queries';
import type { TrackedResponse } from '../api/types';
import { ConfirmDialog } from './ConfirmDialog';

interface UntrackButtonProps {
  campaignName: string;
  tracked: TrackedResponse;
}

export function UntrackButton({
  campaignName,
  tracked,
}: UntrackButtonProps): React.JSX.Element {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutation = useDeleteTracked(campaignName);
  const subject = tracked.first_name ?? `character ${tracked.character_id}`;

  const onConfirm = (): void => {
    setError(null);
    mutation.mutate(tracked.character_id, {
      onSuccess: () => setConfirmOpen(false),
      onError: (err) =>
        setError(err instanceof Error ? err.message : String(err)),
    });
  };

  return (
    <>
      <button
        type="button"
        className="btn btn--quiet track-row__untrack"
        onClick={() => setConfirmOpen(true)}
        title="Stop tracking this soul"
      >
        Untrack
      </button>
      {confirmOpen && (
        <ConfirmDialog
          titleId="untrack-modal-title"
          eyebrow="Untrack"
          title={`Stop tracking ${subject}?`}
          confirmLabel="Untrack"
          busyLabel="Removing…"
          onConfirm={onConfirm}
          onCancel={() => setConfirmOpen(false)}
          busy={mutation.isPending}
          error={error}
        >
          <p className="italic-fell modal__lede">
            Their existing biographies remain in the chronicle —
            only future automatic generation stops.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}

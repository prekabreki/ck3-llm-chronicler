// ConfirmDialog — the confirm-shaped modal (lede + confirm/cancel
// actions + optional error) built on ModalShell. Audit M-F2 /
// ck3_chronicler-27ov.57: four surfaces re-implemented this exact shape
// (delete campaign, reset baseline, untrack, regenerate biography). The
// body content (the lede, and anything richer like the regenerate cost
// estimate) is passed as children; this component owns the actions row,
// the busy/disabled wiring, and the error line.
//
// While `busy`, the dialog refuses to dismiss via Escape or backdrop —
// you can't bail out mid-submit, matching the prior per-modal behaviour.

import { ModalShell } from './ModalShell';

interface ConfirmDialogProps {
  /** id wired to the <h2> and the dialog's aria-labelledby. */
  titleId: string;
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  /** Primary action label when idle. */
  confirmLabel: string;
  /** Primary action label while `busy`. Falls back to confirmLabel. */
  busyLabel?: string;
  /** Secondary action label. Default "Cancel". */
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Submitting — disables both buttons and pins the dialog open. */
  busy?: boolean;
  error?: string | null;
  /** Body content above the actions row (lede, cost estimate, …). */
  children?: React.ReactNode;
}

export function ConfirmDialog({
  titleId,
  eyebrow,
  title,
  confirmLabel,
  busyLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  busy = false,
  error = null,
  children,
}: ConfirmDialogProps): React.JSX.Element {
  return (
    <ModalShell
      titleId={titleId}
      eyebrow={eyebrow}
      title={title}
      onClose={onCancel}
      closeOnEscape={!busy}
      closeOnBackdrop={!busy}
    >
      <div className="modal__body">
        {children}
        <div className="modal__actions">
          <button type="button" className="btn" onClick={onConfirm} disabled={busy}>
            {busy ? (busyLabel ?? confirmLabel) : confirmLabel}
          </button>
          <button
            type="button"
            className="btn btn--quiet"
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
        </div>
        {error && (
          <p className="italic-fell modal__error" role="alert">
            {error}
          </p>
        )}
      </div>
    </ModalShell>
  );
}

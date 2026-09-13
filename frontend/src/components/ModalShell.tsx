// ModalShell — the shared modal scaffold extracted by audit M-F2 /
// ck3_chronicler-27ov.57. Seven surfaces (two LibraryPage confirms,
// UntrackButton, RegenerateBiographyButton, AddTrackedModal, ImportModal,
// AdoptSaveModal) hand-rolled the same backdrop + paper + head markup;
// the two LibraryPage confirms even did it WITHOUT the focus-trap/Escape/
// restore the others wired via useModalA11y, so keyboard users could Tab
// out of the delete-campaign confirm. This component owns that scaffold
// (and the a11y wiring) once; callers pass the eyebrow, title, and body.
//
// The body is left to the caller because it varies — a <form> for the
// input modals, a <div className="modal__body"> for the confirms. Pass
// whichever; ModalShell renders the backdrop, paper frame, and head, then
// drops your body in beneath it.

import { useRef } from 'react';

import { useModalA11y } from './useModalA11y';

interface ModalShellProps {
  /** Called on Escape (when closeOnEscape) and backdrop click (when closeOnBackdrop). */
  onClose: () => void;
  /** id wired to both the <h2> and the dialog's aria-labelledby. */
  titleId: string;
  eyebrow: React.ReactNode;
  title: React.ReactNode;
  /** The modal body — typically a <form> or <div className="modal__body">. */
  children: React.ReactNode;
  /** Escape dismisses the modal. Default true; set false mid-submit. */
  closeOnEscape?: boolean;
  /** Clicking the backdrop dismisses the modal. Default true; set false mid-submit. */
  closeOnBackdrop?: boolean;
}

export function ModalShell({
  onClose,
  titleId,
  eyebrow,
  title,
  children,
  closeOnEscape = true,
  closeOnBackdrop = true,
}: ModalShellProps): React.JSX.Element {
  // audit F-14: focus-trap + Escape + restore-focus, once, for everyone.
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useModalA11y({ open: true, onClose, dialogRef, closeOnEscape });

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        // stopPropagation: confirm modals render inside the CampaignCard
        // <article>, which carries an overlay "open" button — a backdrop
        // click must not also trip the card.
        e.stopPropagation();
        if (closeOnBackdrop) onClose();
      }}
    >
      <div
        className="modal paper paper--edged"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className="modal__head">
          <div className="smallcaps modal__eyebrow">{eyebrow}</div>
          <h2 id={titleId} className="uncial modal__title">
            {title}
          </h2>
        </div>
        {children}
      </div>
    </div>
  );
}

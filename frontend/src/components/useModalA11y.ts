// audit F-14 / ck3_chronicler-jdkr: shared focus-trap + Escape + focus-
// restore behaviour for our modal stack. Every modal had role="dialog"
// + aria-modal="true" but no actual a11y wiring — tab escaped into the
// AppShell, Escape didn't dismiss, focus didn't move in on open or
// restore on close.
//
// Adoption is intentionally lightweight: rather than pulling in
// Radix or headlessui (and rewriting five modals' markup), this hook
// attaches behaviour to whatever container the caller already uses.
// Pass it the dialog ref + onClose; it handles the rest.

import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

interface UseModalA11yOptions {
  open: boolean;
  onClose: () => void;
  // Pass the same ref you set on the dialog container; we read its
  // descendants to compute the focus loop.
  dialogRef: React.RefObject<HTMLElement | null>;
  // When true (default), Escape calls onClose. Set false for modals
  // whose dismissal must go through a confirm step.
  closeOnEscape?: boolean;
}

export function useModalA11y({
  open,
  onClose,
  dialogRef,
  closeOnEscape = true,
}: UseModalA11yOptions): void {
  // Remember the element that had focus when the modal opened so we
  // can restore it on close — important for keyboard users who
  // triggered the modal from a button.
  const triggerRef = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;

    triggerRef.current = document.activeElement;
    const dialog = dialogRef.current;
    if (!dialog) return;

    // Move initial focus inside the dialog. Prefer the first focusable
    // descendant; fall back to focusing the dialog itself (which needs
    // tabIndex=-1 on the container — callers add that to their root
    // div). We do this in a microtask so the dialog markup has actually
    // mounted.
    const focusInside = (): void => {
      const focusables = dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      const target = focusables[0] ?? dialog;
      if (target instanceof HTMLElement) {
        target.focus();
      }
    };
    queueMicrotask(focusInside);

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && closeOnEscape) {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusables = Array.from(
        dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hasAttribute('aria-hidden'));
      if (focusables.length === 0) {
        // Nothing to tab to — keep focus pinned to the dialog itself.
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusables[0]!;
      const last = focusables[focusables.length - 1]!;
      const active = document.activeElement;
      // Wrap forward / backward so Tab never escapes the dialog.
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return (): void => {
      document.removeEventListener('keydown', onKeyDown);
      // Restore focus on close, but only if our tracked trigger is
      // still attached to the document — guards against a re-render
      // that swapped the trigger out.
      const trigger = triggerRef.current;
      if (
        trigger instanceof HTMLElement &&
        document.contains(trigger)
      ) {
        trigger.focus();
      }
    };
  }, [open, onClose, dialogRef, closeOnEscape]);
}

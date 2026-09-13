// ck3_chronicler-27ov.64 (audit M-F10): a tiny toast context for the
// Tracked rail. Replaces the hand-threaded onToast/onError prop pairs the
// audit flagged — child groups (SessionBoundaryGroup, SuggestedCandidates-
// Group) now call useToast() instead of receiving onToast/onError
// callbacks from TrackSideRail. The rail owns the state via useToastState()
// and renders the banner; everything below it consumes useToast().
import { createContext, useContext, useMemo, useState } from 'react';

export interface ToastApi {
  // info/success line (role=status) — clears any pending error.
  showToast: (msg: string) => void;
  // error line (role=alert) — clears any pending info toast.
  showError: (msg: string) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

// Consumer hook — child groups raise a toast without threading props.
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error('useToast must be used within a ToastContext.Provider');
  }
  return ctx;
}

// Provider state — TrackSideRail owns this, wraps its subtree in
// <ToastContext.Provider value={api}>, and renders `toast`/`toastError`
// as the rail banner. Setting one line clears the other (mutually
// exclusive status vs. alert), matching the pre-split behaviour.
export function useToastState(): {
  toast: string | null;
  toastError: string | null;
  api: ToastApi;
} {
  const [toast, setToast] = useState<string | null>(null);
  const [toastError, setToastError] = useState<string | null>(null);

  const api = useMemo<ToastApi>(
    () => ({
      showToast: (msg) => {
        setToast(msg);
        setToastError(null);
      },
      showError: (msg) => {
        setToastError(msg);
        setToast(null);
      },
    }),
    [],
  );

  return { toast, toastError, api };
}

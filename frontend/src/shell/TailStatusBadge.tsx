// ck3_chronicler-kgqa: single persistent save-tail status badge in the
// AppShell header. Pure presentational — status in, JSX out. Replaces
// the old text pip and the RecoveryBanner. The click behaviour (halt
// when live, navigate otherwise) lives in AppShell and is passed as
// onClick; this component only renders.

import type { TailStatus } from './tailStatus';

import './TailStatusBadge.css';

interface TailStatusBadgeProps {
  status: TailStatus;
  onClick?: () => void;
  busy?: boolean;
  title?: string;
}

export function TailStatusBadge({
  status,
  onClick,
  busy = false,
  title,
}: TailStatusBadgeProps): React.JSX.Element {
  return (
    <button
      type="button"
      className={`tail-badge tail-badge--${status.tone}`}
      onClick={onClick}
      disabled={busy}
      title={title}
      aria-label={busy ? 'Halting… please wait' : status.label}
    >
      <span className="tail-badge__dot" aria-hidden />
      <span className="tail-badge__label">{busy ? 'Halting…' : status.label}</span>
    </button>
  );
}

// IngestActivityStrip — ck3_chronicler-bges. Thin strip below the
// AppShell with the most recent save-tail tick. Pure presentational —
// props in, JSX out. App.tsx merges live SSE + persisted-fields and
// passes the result here.
//
// Hidden when: no active campaign, no last-tick data, or tailDead
// (the AppShell pip already says "Tail unavailable" loudly enough).

import { useEffect, useState } from 'react';

import './IngestActivityStrip.css';

export interface IngestActivityLastTick {
  save_filename: string;
  in_game_date: string | null;
  ingested_at: string;
  event_count: number;
  event_type_tally: Record<string, number>;
}

interface IngestActivityStripProps {
  campaignName: string | null;
  lastTickProps: IngestActivityLastTick | null;
  tailDead: boolean;
  // ck3_chronicler-cjtx: live backlog — cached saves still awaiting
  // ingestion (cache_state.pending). null when unknown (no cache_state
  // frame yet). Rendered only when > 0 so a caught-up tail stays clean;
  // during active play CK3 keeps dropping autosaves, so 1-2 is normal
  // churn — a larger number means the consumer is genuinely behind.
  pendingCount?: number | null;
}

export function IngestActivityStrip({
  campaignName,
  lastTickProps,
  tailDead,
  pendingCount = null,
}: IngestActivityStripProps): React.JSX.Element | null {
  // Re-render every 5s so "ago" stays accurate; no ago state — derived.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!lastTickProps) return undefined;
    const id = window.setInterval(() => setTick((t) => t + 1), 5_000);
    return (): void => window.clearInterval(id);
  }, [lastTickProps]);

  if (!campaignName) return null;
  if (tailDead) return null;
  if (!lastTickProps) return null;

  const ago = formatAgo(lastTickProps.ingested_at);
  const tally = formatTally(
    lastTickProps.event_count,
    lastTickProps.event_type_tally,
  );

  return (
    <div className="ingest-activity-strip" role="status" aria-live="polite">
      <span className="ingest-activity-strip__label">Last save:</span>{' '}
      <span className="ingest-activity-strip__filename">
        {lastTickProps.save_filename}
      </span>
      {lastTickProps.in_game_date && (
        <>
          {' · '}
          <span className="ingest-activity-strip__date">
            {lastTickProps.in_game_date}
          </span>
        </>
      )}
      {' · '}
      <span className="ingest-activity-strip__ago">{ago}</span>
      {' · '}
      <span className="ingest-activity-strip__events">{tally}</span>
      {pendingCount !== null && pendingCount > 0 && (
        <>
          {' · '}
          <span className="ingest-activity-strip__pending">
            {pendingCount} to ingest
          </span>
        </>
      )}
    </div>
  );
}

function formatAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const seconds = Math.floor((Date.now() - t) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

const TALLY_CAP = 3;

function formatTally(count: number, tally: Record<string, number>): string {
  if (count === 0) return '0 events';
  const entries = Object.entries(tally).sort(([, a], [, b]) => b - a);
  const head = entries.slice(0, TALLY_CAP);
  const headSum = head.reduce((acc, [, n]) => acc + n, 0);
  const remainder = count - headSum;
  const headStr = head.map(([k, n]) => `${n} ${prettyType(k)}`).join(' · ');
  if (remainder > 0) {
    return `${count} events (${headStr} · and ${remainder} more)`;
  }
  return `${count} events (${headStr})`;
}

function prettyType(t: string): string {
  return t.replace(/_/g, ' ');
}

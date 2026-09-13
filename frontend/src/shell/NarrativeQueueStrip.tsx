// ck3_chronicler-eev: live activity strip for the in-process narrative
// generation queue. Renders below the AppShell whenever the queue has
// active or pending work; hidden otherwise. The user's stated UX target
// is "glance at the second monitor and see what chronicler is working on"
// — this is the surface that delivers it.
//
// Data flow:
// - Initial state via TanStack Query (`useNarrativeQueue`).
// - SSE-driven invalidation: when a `narrative_*` frame arrives,
//   `narrativeSeq` ticks and we refetch. The same SSE channel that
//   already drives Save-tail is reused.

import {
  queryKeys,
  useNarrativeQueue,
  useNarrativeInvalidation,
} from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { NarrativeQueueItem } from '../api/types';

interface NarrativeQueueStripProps {
  campaignName: string | null;
}

const KIND_LABEL: Record<string, string> = {
  biography: 'biography',
};

function formatEta(ms: number | null, depth: number): string | null {
  if (ms === null || depth <= 0) return null;
  const seconds = Math.round((ms * depth) / 1000);
  if (seconds < 60) return `~${seconds}s remaining`;
  const minutes = Math.round(seconds / 60);
  return `~${minutes}m remaining`;
}

function describeItem(item: NarrativeQueueItem): string {
  // ck3_chronicler-27ov.81 (audit L30): name comes from the queue item
  // itself (resolved BE-side at enqueue) — no FE join against the
  // active campaign's character window, which missed tracked souls on
  // large campaigns and never named cross-campaign items.
  const subject = item.character_name ?? `character ${item.character_id}`;
  const label = KIND_LABEL[item.kind] ?? item.kind;
  return `Generating ${label} of ${subject}`;
}

export function NarrativeQueueStrip({
  campaignName,
}: NarrativeQueueStripProps): React.JSX.Element | null {
  const queueQ = useNarrativeQueue();
  // ck3_chronicler-sz4t: clicking the strip jumps to the full queue page
  // so users can see the full backlog + failure history without navigating
  // through the meta buttons.
  const setView = useAppStore((s) => s.setView);

  // Refetch the snapshot on every narrative_* frame so the strip stays
  // in sync without polling. Initial mount fetches once via the query.
  useNarrativeInvalidation(campaignName, (qc) => {
    void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
  });

  const queue = queueQ.data;
  if (!queue) return null;

  const total = queue.active.length + queue.queued.length;
  if (total === 0) return null;

  const head = queue.active[0] ?? queue.queued[0];
  if (!head) return null;

  const queuedCount = queue.active.length === 0
    ? Math.max(queue.queued.length - 1, 0)
    : queue.queued.length;
  const eta = formatEta(queue.avg_duration_ms, total);

  return (
    <button
      type="button"
      className="narrative-strip"
      role="status"
      aria-live="polite"
      data-testid="narrative-queue-strip"
      onClick={() => setView('queue')}
      title="Open narrative queue"
    >
      <span className="narrative-strip__pulse" aria-hidden />
      <span className="narrative-strip__head">
        {describeItem(head)}
      </span>
      {queuedCount > 0 && (
        <span className="narrative-strip__queued">
          {' · '}
          {queuedCount} more queued
        </span>
      )}
      {eta && (
        <span className="narrative-strip__eta">
          {' · '}
          {eta}
        </span>
      )}
      {queue.failed_count > 0 && (
        <span className="narrative-strip__failed" title="recent failures">
          {' · '}
          {queue.failed_count} failed
        </span>
      )}
    </button>
  );
}

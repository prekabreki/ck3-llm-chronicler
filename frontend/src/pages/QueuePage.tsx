// QueuePage — ck3_chronicler-sz4t mature narrative-queue surface.
//
// The AppShell strip (NarrativeQueueStrip) is one line: "Generating memory
// of <name> · N more queued". That's enough as a glance-at-the-second-monitor
// indicator but not enough when the user wants to know *what's* in the
// backlog — which characters, why a generation failed, when something
// rolled off as completed. This page surfaces the full snapshot:
//
//   - Active: in-flight items with elapsed time since started_at
//   - Queued: FIFO order, count by character
//   - Recent: last completed/failed roll-off (capped on the backend at 20)
//   - Failure detail: error string + which character + when
//
// The page reuses the same NarrativeQueueState snapshot the strip already
// reads via /api/settings/narrative-queue, plus the SSE narrativeSeq tick
// for live invalidation. No new endpoints required.

import { useEffect, useState } from 'react';

import {
  useNarrativeQueue,
  useNarrativeCharacterStats,
  useNarrativeInvalidation,
  useCancelNarrativeQueueItem,
  useRegenerateNarrativeQueueItem,
  useReorderNarrativeQueue,
  queryKeys,
} from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { NarrativeQueueItem } from '../api/types';

const KIND_LABEL: Record<string, string> = {
  biography: 'biography',
};

function fmtDuration(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return `${m}m ${s}s`;
}

function fmtElapsedSince(iso: string | null, nowMs: number): string {
  if (!iso) return '—';
  const startedMs = new Date(iso).getTime();
  if (Number.isNaN(startedMs)) return '—';
  const elapsed = Math.max(0, nowMs - startedMs);
  return fmtDuration(elapsed);
}

function fmtAbsoluteTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString();
}

function nameOf(item: NarrativeQueueItem): string {
  // ck3_chronicler-27ov.81 (audit L30): name carried on the item from
  // the BE (resolved at enqueue); no FE join against the active
  // campaign's character window, which missed tracked souls on large
  // campaigns and never named cross-campaign items.
  return item.character_name ?? `character ${item.character_id}`;
}

export function QueuePage(): React.JSX.Element {
  const activeCampaign = useAppStore((s) => s.activeCampaign);
  const queueQ = useNarrativeQueue();
  const statsQ = useNarrativeCharacterStats();
  const cancelM = useCancelNarrativeQueueItem();
  const regenM = useRegenerateNarrativeQueueItem();
  const reorderM = useReorderNarrativeQueue();

  // SSE-driven live updates: every narrative_* frame ticks the seq and
  // we invalidate so both the snapshot AND the per-character stats
  // refetch. Mirrors the strip's pattern.
  useNarrativeInvalidation(activeCampaign, (qc) => {
    void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
    void qc.invalidateQueries({
      queryKey: queryKeys.narrativeCharacterStats(),
    });
  });

  // Tick `now` once a second so the active items' elapsed-time column
  // ticks without a backend round-trip. Cheap — one setInterval, one
  // useState. Avoids the unnecessary load of polling the queue endpoint.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // ck3_chronicler-fiv6 (was kdf): the "Drain to GPU" button + drain
  // mutation were removed in 2026-05-08 alongside the local-tier
  // providers. Hosted Claude Code has no GPU contention with CK3, so
  // there's nothing to drain.
  const queue = queueQ.data;

  if (!queue) {
    return (
      <div className="queue-page">
        <div className="queue-page__inner">
          <header className="queue-page__header">
            <div className="smallcaps queue-page__eyebrow">The scriptorium queue</div>
            <h1 className="uncial queue-page__title">Narrative queue</h1>
          </header>
          <p className="italic-fell">Loading…</p>
        </div>
      </div>
    );
  }

  // Group queued items by character so a fan-out reads as "Foo (×6)"
  // instead of six identical rows.
  const queuedByCharacter = new Map<number, NarrativeQueueItem[]>();
  for (const item of queue.queued) {
    const list = queuedByCharacter.get(item.character_id) ?? [];
    list.push(item);
    queuedByCharacter.set(item.character_id, list);
  }
  const queuedGroups = Array.from(queuedByCharacter.entries()).sort(
    ([, a], [, b]) => (a[0]?.item_id ?? 0) - (b[0]?.item_id ?? 0),
  );

  const recentCompleted = queue.recent.filter((i) => i.status === 'completed');
  const recentFailed = queue.recent.filter((i) => i.status === 'failed');

  const total = queue.active.length + queue.queued.length;

  return (
    <div className="queue-page">
      <div className="queue-page__inner">
        <header className="queue-page__header">
          <div className="smallcaps queue-page__eyebrow">The scriptorium queue</div>
          <h1 className="uncial queue-page__title">Narrative queue</h1>
          <p className="italic-fell queue-page__lede">
            What the chronicler is writing right now, what's pending, and
            what most recently rolled off — including failures.
          </p>
        </header>

        <section className="queue-summary">
          <div className="queue-summary__cell">
            <div className="smallcaps queue-summary__label">In flight</div>
            <div className="queue-summary__value">{queue.active.length}</div>
          </div>
          <div className="queue-summary__cell">
            <div className="smallcaps queue-summary__label">Queued</div>
            <div className="queue-summary__value">{queue.queued.length}</div>
          </div>
          <div className="queue-summary__cell">
            <div className="smallcaps queue-summary__label">Completed</div>
            <div className="queue-summary__value">{queue.completed_count}</div>
          </div>
          <div className="queue-summary__cell">
            <div className="smallcaps queue-summary__label">Failed</div>
            <div
              className={
                'queue-summary__value' +
                (queue.failed_count > 0 ? ' queue-summary__value--alert' : '')
              }
            >
              {queue.failed_count}
            </div>
          </div>
          <div className="queue-summary__cell">
            <div className="smallcaps queue-summary__label">Avg duration</div>
            <div className="queue-summary__value">
              {fmtDuration(queue.avg_duration_ms)}
            </div>
          </div>
        </section>

        <section className="queue-section">
          <h2 className="queue-section__title">In flight</h2>
          {queue.active.length === 0 ? (
            <p className="queue-section__empty italic-fell">
              {total === 0
                ? 'Nothing queued. The chronicler is at rest.'
                : 'Nothing dispatched yet — items below will pick up as a worker becomes free.'}
            </p>
          ) : (
            <ul className="queue-list">
              {queue.active.map((item) => (
                <li key={item.item_id} className="queue-list__row queue-list__row--active">
                  <span className="queue-list__pulse" aria-hidden />
                  <span className="queue-list__name">
                    {nameOf(item)}
                  </span>
                  <span className="queue-list__kind">
                    {KIND_LABEL[item.kind] ?? item.kind}
                  </span>
                  <span className="queue-list__time">
                    {fmtElapsedSince(item.started_at, nowMs)} elapsed
                  </span>
                  <button
                    type="button"
                    className="queue-list__action"
                    onClick={() => cancelM.mutate(item.item_id)}
                    disabled={cancelM.isPending}
                  >
                    Cancel
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="queue-section">
          <h2 className="queue-section__title">Queued</h2>
          {queuedGroups.length === 0 ? (
            <p className="queue-section__empty italic-fell">No backlog.</p>
          ) : (
            <ul className="queue-list">
              {queuedGroups.map(([characterId, items]) => {
                const head = items[0];
                if (!head) return null;
                return (
                  <li key={characterId} className="queue-list__row">
                    <span className="queue-list__name">
                      {nameOf(head)}
                    </span>
                    {items.length > 1 && (
                      <span className="queue-list__multiplier">
                        ×{items.length}
                      </span>
                    )}
                    <span className="queue-list__kind">
                      {Array.from(new Set(items.map((i) => KIND_LABEL[i.kind] ?? i.kind))).join(
                        ' + ',
                      )}
                    </span>
                    <span className="queue-list__time">
                      since {fmtAbsoluteTime(head.enqueued_at)}
                    </span>
                    <button
                      type="button"
                      className="queue-list__action"
                      onClick={() => {
                        // Cancel every item_id in this character's group.
                        for (const i of items) {
                          cancelM.mutate(i.item_id);
                        }
                      }}
                      disabled={cancelM.isPending}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="queue-list__action"
                      onClick={() => {
                        // Move this character's items to the front of
                        // the queue. Reorder takes item_ids in dispatch
                        // order; we send this group's ids first and let
                        // the backend append the rest.
                        reorderM.mutate(items.map((i) => i.item_id));
                      }}
                      disabled={reorderM.isPending}
                    >
                      Move to top
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {recentFailed.length > 0 && (
          <section className="queue-section queue-section--alert">
            <h2 className="queue-section__title">Recently failed</h2>
            <ul className="queue-list">
              {recentFailed.map((item) => (
                <li key={item.item_id} className="queue-list__row queue-list__row--failed">
                  <span className="queue-list__name">
                    {nameOf(item)}
                  </span>
                  <span className="queue-list__kind">
                    {KIND_LABEL[item.kind] ?? item.kind}
                  </span>
                  <span className="queue-list__time">
                    {fmtAbsoluteTime(item.completed_at)}
                  </span>
                  {item.error && (
                    <span className="queue-list__error" title={item.error}>
                      {item.error}
                    </span>
                  )}
                  <button
                    type="button"
                    className="queue-list__action"
                    onClick={() => regenM.mutate(item.item_id)}
                    disabled={regenM.isPending}
                  >
                    Regenerate
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="queue-section">
          <h2 className="queue-section__title">Recently completed</h2>
          {recentCompleted.length === 0 ? (
            <p className="queue-section__empty italic-fell">
              No completions yet this session.
            </p>
          ) : (
            <ul className="queue-list">
              {recentCompleted.map((item) => (
                <li key={item.item_id} className="queue-list__row">
                  <span className="queue-list__name">
                    {nameOf(item)}
                  </span>
                  <span className="queue-list__kind">
                    {KIND_LABEL[item.kind] ?? item.kind}
                  </span>
                  <span className="queue-list__time">
                    {fmtAbsoluteTime(item.completed_at)} · {fmtDuration(item.duration_ms)}
                  </span>
                  <button
                    type="button"
                    className="queue-list__action"
                    onClick={() => regenM.mutate(item.item_id)}
                    disabled={regenM.isPending}
                  >
                    Regenerate
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {statsQ.data && statsQ.data.rows.length > 0 && (
          <section className="queue-section queue-section--stats">
            <h2 className="queue-section__title">Per-character stats</h2>
            <p className="italic-fell queue-section__lede">
              Lifetime aggregates for the current chronicler process.
              Stats reset on restart.
            </p>
            <table className="queue-stats-table">
              <thead>
                <tr>
                  <th>Character</th>
                  <th>Kind</th>
                  <th>Completed</th>
                  <th>Failed</th>
                  <th>Median latency</th>
                  <th>Last success</th>
                </tr>
              </thead>
              <tbody>
                {statsQ.data.rows.map((row) => (
                  <tr key={`${row.character_id}-${row.kind}`}>
                    <td>
                      {row.character_name ?? `character ${row.character_id}`}
                    </td>
                    <td>{KIND_LABEL[row.kind] ?? row.kind}</td>
                    <td>{row.completed_count}</td>
                    <td
                      className={
                        row.failed_count > 0 ? 'queue-stats-table__failed' : ''
                      }
                    >
                      {row.failed_count}
                    </td>
                    <td>{fmtDuration(row.median_duration_ms)}</td>
                    <td>{fmtAbsoluteTime(row.last_success_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </div>
    </div>
  );
}

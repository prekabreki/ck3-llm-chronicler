// IngestPage — the live save-tail screen. Streams events from the
// in-process EventBus via /api/sse/ingest/{campaign} and renders them
// in reverse-chronological order with a connection pip and the
// latest cache-state snapshot.
//
// The rendered list is an observability surface, not a full timeline —
// the bus drops oldest on full subscribers (BUFFER_SIZE=100 server
// side, 200 client side), and a slow tail can silently miss events.
// That's fine for the "is anything happening?" question this screen
// answers; the canonical record lives in the campaign DB.

import { useEventStream } from '../api/useEventStream';
import type { CacheStateFrame, IngestEvent } from '../api/useEventStream';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function IngestPage({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const { events, cacheState, connected } = useEventStream(campaignName);

  return (
    <div className="ingest-page">
      <div className="ingest-page__inner">
        <header className="ingest-page__header">
          <div className="smallcaps ingest-page__eyebrow">
            The save-tail
          </div>
          <h1 className="uncial ingest-page__title">
            What the watcher sees
          </h1>
          <p className="italic-fell ingest-page__lede">
            Live ingest events for <strong>{campaignName}</strong>, streamed
            from the in-process bus. The canonical record is the campaign
            database — this list is the chronicler's shoulder, not the
            chronicle.
          </p>
        </header>

        <div className="ingest-page__toolbar">
          <span
            className={
              'pip ' +
              (connected
                ? 'ingest-status--connected'
                : 'ingest-status--idle')
            }
          >
            <span
              className={
                'pip__dot ' +
                (connected ? 'pip__dot--alive' : 'pip__dot--idle')
              }
            />
            {connected ? 'Connected' : 'Disconnected'}
          </span>
          <span className="pip">
            <span className="pip__dot pip__dot--idle" /> {events.length} events buffered
          </span>
          <CacheStateBadge state={cacheState} />
        </div>

        <div className="paper paper--edged ingest-list">
          {events.length === 0 ? (
            <p className="italic-fell ingest-list__empty">
              The chronicler watches in silence. Events will appear here
              as the watcher detects new autosaves.
            </p>
          ) : (
            <ol className="ingest-list__rows">
              {events.map((e, i) => (
                <IngestRow key={i} event={e} index={events.length - i} />
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

function CacheStateBadge({
  state,
}: {
  state: CacheStateFrame | null;
}): React.JSX.Element {
  if (!state) {
    return (
      <span className="pip">
        <span className="pip__dot pip__dot--idle" /> cache: unknown
      </span>
    );
  }
  // ck3_chronicler-k91o: cache.snapshot() carries `pending` — the count of
  // cached saves still awaiting ingestion. The prior `cached`/`latest`
  // reads were phantom (the BE never sent those keys, so they always
  // rendered '?'/hidden); typing the frame surfaced the drift. Render the
  // one real counter.
  return (
    <span className="pip ingest-status--cache">
      <span className="pip__dot pip__dot--alive" /> cache: {state.pending} pending
    </span>
  );
}

function IngestRow({
  event,
  index,
}: {
  event: IngestEvent;
  index: number;
}): React.JSX.Element {
  if (event.kind === 'event_ingested') {
    const eventType = String(event.event_type ?? '?');
    const eventDate = String(event.event_date ?? '');
    const eventId = String(event.event_id ?? '');
    const characterId = String(event.character_id ?? '?');
    return (
      <li className="ingest-row">
        <span className="smallcaps ingest-row__index">#{index}</span>
        <span className="ingest-row__bead">✦</span>
        <span className="ingest-row__type">{eventType}</span>
        <span className="italic-fell ingest-row__date">{eventDate}</span>
        <span className="smallcaps ingest-row__refs">
          char #{characterId} · evt #{eventId}
        </span>
      </li>
    );
  }
  return (
    <li className="ingest-row ingest-row--meta">
      <span className="smallcaps ingest-row__index">#{index}</span>
      <span className="ingest-row__bead">·</span>
      <span className="ingest-row__type">{event.kind}</span>
      <span className="italic-fell ingest-row__date">
        {JSON.stringify(event)}
      </span>
    </li>
  );
}

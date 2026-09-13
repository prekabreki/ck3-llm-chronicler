// Audit F-27: plain event-roll panel. Glyph-coded marginalia bead per
// event, color-coded by type. Extracted from ChroniclePage along with
// the type-key helpers.
//
// audit L34 (27ov.81): the cross-pane `highlightedId` prop was always
// passed null by the sole caller (the memories panel that drove it was
// removed), so the highlight branch was dead. Dropped.

import type { EventResponse } from '../../api/types';
import { eventColor, eventGlyph } from './eventGlyphs';

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

function monthDay(date: string): string {
  const parts = date.split('.');
  if (parts.length < 3) return '';
  const m = parseInt(parts[1] ?? '', 10);
  const d = parseInt(parts[2] ?? '', 10);
  if (Number.isNaN(m) || Number.isNaN(d)) return '';
  return `${d} ${MONTHS[m - 1] ?? ''}`;
}

function eventTitle(e: EventResponse): string {
  const payload = e.payload as Record<string, unknown>;
  const title = payload['title'];
  if (typeof title === 'string' && title.length > 0) return title;
  return e.type.replace(/_/g, ' ');
}

interface EventsPanelProps {
  events: EventResponse[];
}

export function EventsPanel({
  events,
}: EventsPanelProps): React.JSX.Element {
  if (events.length === 0) {
    return (
      <p className="italic-fell chronicle-page__pending">
        No events recorded for this soul.
      </p>
    );
  }
  return (
    <div className="chronicle-events">
      <p className="italic-fell chronicle-events__lede">
        The plain record. What the engine reports; the chronicler will
        not embellish here.
      </p>
      <ol className="chronicle-events__list">
        <span className="chronicle-events__rule" aria-hidden />
        {events.map((e) => {
          return (
            <li
              key={e.id}
              className="chronicle-event"
              data-event-id={e.id}
            >
              <div className="chronicle-event__date">
                <div className="smallcaps chronicle-event__year">
                  {e.date.split('.')[0]}
                </div>
                <div className="italic-fell chronicle-event__monthday">
                  {monthDay(e.date)}
                </div>
              </div>
              <div
                className="chronicle-event__bead"
                style={{
                  borderColor: eventColor(e.type),
                  color: eventColor(e.type),
                }}
              >
                {eventGlyph(e.type)}
              </div>
              <div className="chronicle-event__body">
                <div className="uncial chronicle-event__title">
                  {eventTitle(e)}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

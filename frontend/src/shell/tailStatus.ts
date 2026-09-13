// ck3_chronicler-kgqa: derive a single go/no-go save-tail status from the
// SSE stream state. Pure function — unit tested with a precedence truth
// table. Consumed by TailStatusBadge in the AppShell header.

import type { EventStreamState } from '../api/useEventStream';

export type TailLevel =
  | 'live'
  | 'watching'
  | 'catching-up'
  | 'wrong-game'
  | 'unavailable'
  | 'idle';

export interface TailStatus {
  level: TailLevel;
  label: string;
  detail: string | null;
  tone: 'green' | 'amber' | 'red' | 'gray';
}

function readTime(iso: unknown): number | null {
  if (typeof iso !== 'string') return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

export function deriveTailStatus(state: EventStreamState): TailStatus {
  // Precedence, highest first.
  if (state.dead) {
    return { level: 'unavailable', label: 'Tail unavailable', detail: null, tone: 'gray' };
  }
  if (state.recovering) {
    const n = state.recovering.pendingCacheCount;
    return {
      level: 'catching-up',
      label: n > 0 ? `Catching up · ${n} left` : 'Catching up',
      detail: 'Hold on — replaying cached saves before watching live.',
      tone: 'amber',
    };
  }
  const foreignAt = readTime(state.lastForeignDrop?.observed_at);
  const tickAt = readTime(state.lastSavePairCompleted?.completed_at);
  if (foreignAt !== null && (tickAt === null || foreignAt > tickAt)) {
    return {
      level: 'wrong-game',
      label: 'Not recording — wrong game',
      detail:
        'This save belongs to a different playthrough. Wrong campaign open, or a new game?',
      tone: 'red',
    };
  }
  if (state.lastSavePairCompleted) {
    return { level: 'live', label: 'Live · safe to play', detail: null, tone: 'green' };
  }
  if (state.connected) {
    return { level: 'watching', label: 'Live · watching for saves', detail: null, tone: 'green' };
  }
  return { level: 'idle', label: 'Tail idle', detail: null, tone: 'gray' };
}

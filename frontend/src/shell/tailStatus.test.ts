import { describe, expect, it } from 'vitest';

import type {
  EventStreamState,
  SaveDroppedForeignFrame,
  SavePairCompletedFrame,
} from '../api/useEventStream';
import { deriveTailStatus } from './tailStatus';

const BASE: EventStreamState = {
  events: [],
  cacheState: null,
  lastSavePairCompleted: null,
  lastForeignDrop: null,
  narrativeSeq: 0,
  connected: false,
  dead: false,
  recovering: null,
  autoResumed: null,
  cacheStateSeq: 0,
};

// ck3_chronicler-k91o: build fully-typed frames (deriveTailStatus only
// reads completed_at / observed_at, but the discriminated union requires
// the rest) — no `as` cast onto a partial object.
const tick = (completedAt: string): SavePairCompletedFrame => ({
  kind: 'save_pair_completed',
  save_filename: 'autosave.ck3',
  in_game_date: null,
  completed_at: completedAt,
  event_count: 0,
  event_type_tally: {},
});
const drop = (observedAt: string): SaveDroppedForeignFrame => ({
  kind: 'save_dropped_foreign',
  observed_playthrough_id: 'foreign-pt',
  campaign_playthrough_id: 'campaign-pt',
  save_filename: 'autosave.ck3',
  observed_at: observedAt,
});

describe('deriveTailStatus', () => {
  it('dead → unavailable (gray) outranks everything', () => {
    const s = deriveTailStatus({ ...BASE, dead: true, recovering: {
      baselinePersistedAt: null, baselineDate: null,
      baselineGeneration: null, pendingCacheCount: 9,
    } });
    expect(s.level).toBe('unavailable');
    expect(s.tone).toBe('gray');
  });

  it('recovering → catching-up (amber) with count, outranks a foreign drop', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      recovering: {
        baselinePersistedAt: null, baselineDate: null,
        baselineGeneration: null, pendingCacheCount: 18,
      },
      lastForeignDrop: drop('2026-05-31T20:30:00Z'),
    });
    expect(s.level).toBe('catching-up');
    expect(s.tone).toBe('amber');
    expect(s.label).toMatch(/18/);
  });

  it('live foreign drop newer than last tick → wrong-game (red)', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      lastSavePairCompleted: tick('2026-05-31T20:00:00Z'),
      lastForeignDrop: drop('2026-05-31T20:30:00Z'),
    });
    expect(s.level).toBe('wrong-game');
    expect(s.tone).toBe('red');
  });

  it('drain-era foreign drop superseded by a later successful tick → live (green)', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      lastForeignDrop: drop('2026-05-31T20:00:00Z'),
      lastSavePairCompleted: tick('2026-05-31T20:30:00Z'),
    });
    expect(s.level).toBe('live');
    expect(s.tone).toBe('green');
  });

  it('successful tick, no foreign drop → live (green)', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      lastSavePairCompleted: tick('2026-05-31T20:30:00Z'),
    });
    expect(s.level).toBe('live');
  });

  it('connected, nothing yet → watching (green)', () => {
    const s = deriveTailStatus({ ...BASE, connected: true });
    expect(s.level).toBe('watching');
    expect(s.tone).toBe('green');
  });

  it('disconnected, no campaign activity → idle (gray)', () => {
    const s = deriveTailStatus(BASE);
    expect(s.level).toBe('idle');
    expect(s.tone).toBe('gray');
  });

  it('foreign drop with no tick at all → wrong-game (red)', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      lastForeignDrop: drop('2026-05-31T20:30:00Z'),
    });
    expect(s.level).toBe('wrong-game');
    expect(s.tone).toBe('red');
  });

  it('equal foreign/tick timestamps → live (green)', () => {
    const ts = '2026-05-31T20:30:00Z';
    const s = deriveTailStatus({
      ...BASE, connected: true,
      lastSavePairCompleted: tick(ts),
      lastForeignDrop: drop(ts),
    });
    expect(s.level).toBe('live');
    expect(s.tone).toBe('green');
  });

  it('recovering with pendingCacheCount 0 → label \'Catching up\' without a count', () => {
    const s = deriveTailStatus({
      ...BASE, connected: true,
      recovering: {
        baselinePersistedAt: null, baselineDate: null,
        baselineGeneration: null, pendingCacheCount: 0,
      },
    });
    expect(s.level).toBe('catching-up');
    expect(s.label).toBe('Catching up');
  });
});

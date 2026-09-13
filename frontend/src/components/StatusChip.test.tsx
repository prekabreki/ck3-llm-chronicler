// ck3_chronicler-0px: StatusChip + pickChipState unit tests.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StatusChip } from './StatusChip';
import { deriveCharacterStatus, pickChipState } from '../util/characterStatus';
import type { NarrativeQueueResponse } from '../api/types';

const EMPTY_QUEUE: NarrativeQueueResponse = {
  queued: [],
  active: [],
  recent: [],
  completed_count: 0,
  failed_count: 0,
  avg_duration_ms: null,
};

function _queueItem(
  character_id: number,
  kind: 'biography' = 'biography',
  status: 'queued' | 'generating' | 'completed' | 'failed' = 'queued',
) {
  return {
    item_id: character_id,
    character_id,
    character_name: null,
    kind,
    status,
    enqueued_at: '2026-05-06T10:00:00Z',
    started_at: null,
    completed_at: null,
    duration_ms: null,
    error: null,
  };
}

describe('StatusChip', () => {
  it('renders label + dot class for each state', () => {
    const states = [
      { state: 'live' as const, label: 'Drafting', cls: 'pip__dot--alive' },
      { state: 'queued' as const, label: 'In queue', cls: 'pip__dot--gold' },
      { state: 'drafted' as const, label: 'Bio drafted', cls: 'pip__dot--azure' },
      { state: 'paused' as const, label: 'Paused', cls: 'pip__dot--idle' },
    ];
    for (const { state, label, cls } of states) {
      const { container, unmount } = render(<StatusChip state={state} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(container.querySelector(`.${cls}`)).not.toBeNull();
      unmount();
    }
  });

  it('annotates the rendered chip with data-chip-state for selectors / tests', () => {
    const { container } = render(<StatusChip state="queued" />);
    expect(container.querySelector('[data-chip-state="queued"]')).not.toBeNull();
  });
});

describe('pickChipState', () => {
  it('returns null when nothing notable is happening', () => {
    expect(pickChipState(42, EMPTY_QUEUE, null)).toBeNull();
  });

  it('returns "live" when the character has an active biography task', () => {
    const queue: NarrativeQueueResponse = {
      ...EMPTY_QUEUE,
      active: [_queueItem(42, 'biography', 'generating')],
    };
    expect(pickChipState(42, queue, null)).toBe('live');
  });

  it('returns "queued" when the character is enqueued but not yet active', () => {
    const queue: NarrativeQueueResponse = {
      ...EMPTY_QUEUE,
      queued: [_queueItem(42, 'biography')],
    };
    expect(pickChipState(42, queue, null)).toBe('queued');
  });

  it('prioritizes "live" over "queued" when both exist (rare race)', () => {
    const queue: NarrativeQueueResponse = {
      active: [_queueItem(42, 'biography', 'generating')],
      queued: [_queueItem(42, 'biography')],
      recent: [],
      completed_count: 0,
      failed_count: 0,
      avg_duration_ms: null,
    };
    expect(pickChipState(42, queue, null)).toBe('live');
  });

  it('returns "paused" when the tracked row has paused_at set and no live work', () => {
    expect(
      pickChipState(42, EMPTY_QUEUE, { paused_at: '2026-05-06T10:00:00Z', biography_count: 1 }),
    ).toBe('paused');
  });

  it('paused outranks drafted', () => {
    expect(
      pickChipState(42, EMPTY_QUEUE, { paused_at: '2026-05-06T10:00:00Z', biography_count: 1 }),
    ).toBe('paused');
  });

  it('returns "drafted" when the tracked row has biography_count > 0 and is not paused', () => {
    expect(
      pickChipState(42, EMPTY_QUEUE, { paused_at: null, biography_count: 1 }),
    ).toBe('drafted');
  });

  it('returns null for tracked rows with no biography and no live work', () => {
    expect(
      pickChipState(42, EMPTY_QUEUE, { paused_at: null, biography_count: 0 }),
    ).toBeNull();
  });

  it('handles undefined queue (loading state) gracefully', () => {
    expect(pickChipState(42, undefined, { paused_at: null, biography_count: 1 })).toBe(
      'drafted',
    );
  });
});

// M-F5 (27ov.60): one derivation, one decided priority — TrackedPage's
// deriveStatus used to put paused above generating, so a paused
// character mid-generation read 'Paused' on Tracked and 'Drafting' on
// Codex for the same instant.
describe('deriveCharacterStatus', () => {
  it('live outranks paused — what is actually running wins', () => {
    const queue: NarrativeQueueResponse = {
      ...EMPTY_QUEUE,
      active: [_queueItem(42, 'biography', 'generating')],
    };
    expect(
      deriveCharacterStatus(42, queue, {
        paused_at: '2026-05-06T10:00:00Z',
        biography_count: 1,
      }),
    ).toBe('live');
  });

  it('queued outranks paused', () => {
    const queue: NarrativeQueueResponse = {
      ...EMPTY_QUEUE,
      queued: [_queueItem(42, 'biography')],
    };
    expect(
      deriveCharacterStatus(42, queue, {
        paused_at: '2026-05-06T10:00:00Z',
        biography_count: 0,
      }),
    ).toBe('queued');
  });

  it('returns "tracking" for a tracked row with no other signal', () => {
    expect(
      deriveCharacterStatus(42, EMPTY_QUEUE, { paused_at: null, biography_count: 0 }),
    ).toBe('tracking');
  });

  it('returns null for an untracked character with no queue activity', () => {
    expect(deriveCharacterStatus(42, EMPTY_QUEUE, null)).toBeNull();
  });
});

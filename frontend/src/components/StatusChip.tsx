// ck3_chronicler-0px: per-character generation-status chip.
//
// Surfaces (a) what the narrative-queue is doing for this character
// right now (live / queued) and (b) whether the character has a
// biography on record (drafted) or is paused. Used as a row-level
// signal on Codex + Tracked. Reuses the existing .pip class for
// alignment + size; adds two dot tints (gold for queued, azure for
// drafted).
//
// The status derivation lives in util/characterStatus.ts (M-F5) —
// this file only renders the chip.

import type { ChipState } from '../util/characterStatus';

interface StatusChipProps {
  state: ChipState;
}

const LABEL: Record<ChipState, string> = {
  live: 'Drafting',
  queued: 'In queue',
  drafted: 'Bio drafted',
  paused: 'Paused',
};

const DOT_CLASS: Record<ChipState, string> = {
  live: 'pip__dot--alive',
  queued: 'pip__dot--gold',
  drafted: 'pip__dot--azure',
  paused: 'pip__dot--idle',
};

export function StatusChip({ state }: StatusChipProps): React.JSX.Element {
  return (
    <span className="pip" data-chip-state={state}>
      <span className={`pip__dot ${DOT_CLASS[state]}`} />
      {LABEL[state]}
    </span>
  );
}

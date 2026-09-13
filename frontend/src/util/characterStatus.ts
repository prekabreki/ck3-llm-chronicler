// THE character-status derivation (M-F5/27ov.60). Every surface
// (Codex chip, Tracked pip/progress/actions) derives from this one
// function so the same character can never read two different statuses
// at once — TrackedPage used to run a second derivation with paused
// above generating, so a paused character mid-generation read 'Paused'
// on Tracked and 'Drafting' on Codex for the same instant.
//
// Lives outside StatusChip.tsx so that file only exports a component
// (react-refresh/only-export-components).

import type { NarrativeQueueResponse } from '../api/types';

// The four states worth a StatusChip.
export type ChipState = 'live' | 'queued' | 'drafted' | 'paused';

// The full per-character status: chip-worthy states plus "tracking"
// (tracked, nothing else going on — the resting state).
export type CharacterStatus = ChipState | 'tracking';

export interface TrackedChipInputs {
  paused_at: string | null;
  biography_count: number;
}

// Priority: live > queued > paused > drafted > tracking > null. "live"
// wins over "paused" because live mid-flight means the user just
// unpaused or the task started before pause landed; we want the user
// to see what's actually running. Returns null only for an untracked
// character with no queue activity.
export function deriveCharacterStatus(
  characterId: number,
  queue: NarrativeQueueResponse | undefined,
  tracked: TrackedChipInputs | null,
): CharacterStatus | null {
  if (queue) {
    for (const item of queue.active) {
      if (item.character_id === characterId && item.kind === 'biography') {
        return 'live';
      }
    }
    for (const item of queue.queued) {
      if (item.character_id === characterId && item.kind === 'biography') {
        return 'queued';
      }
    }
  }
  if (tracked?.paused_at) return 'paused';
  if (tracked && tracked.biography_count > 0) return 'drafted';
  return tracked ? 'tracking' : null;
}

// Chip-level view of the status: "tracking" isn't worth a chip (the
// row itself already conveys it), so it maps to null.
export function pickChipState(
  characterId: number,
  queue: NarrativeQueueResponse | undefined,
  tracked: TrackedChipInputs | null,
): ChipState | null {
  const status = deriveCharacterStatus(characterId, queue, tracked);
  return status === 'tracking' ? null : status;
}

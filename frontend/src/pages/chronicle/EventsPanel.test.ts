// ck3_chronicler-w2ux: renderer coverage parity test on the frontend.
// Every event type declared in chronicler.schema.events.EventPayload
// (re-emitted as EVENT_TYPE_LITERALS via scripts/codegen_event_types.py)
// must either have a bespoke glyph + color in EventsPanel.tsx or be
// on the explicit fallback allowlist below.
//
// Adding a new event type without wiring the FE glyph now fails this
// test — the regression net x2cc didn't have when it shipped.

import { describe, it, expect } from 'vitest';

import { EVENT_TYPE_LITERALS } from '../../api/eventTypes.generated';
import {
  EVENT_GLYPHS,
  EVENT_COLOR_VAR,
  eventCanonicalKey,
  eventGlyph,
  eventColor,
} from './eventGlyphs';

// Event types that intentionally fall through to the bare-glyph
// fallback (✦ + gold). The 30 emitted-by-save-diff types added since
// v0.6 should all be specifically glyphed; these listed here are the
// v0.2-era debug-log-only types that haven't been wired through the
// save-diff pipeline. Aligned with the BE parity test's
// _KNOWN_MISSING_RENDERERS allowlist (tests/unit/test_event_rendering.py).
const EXPECTED_FALLBACK: ReadonlySet<string> = new Set([
  // v0.2 debug-log-only — handled by the BE allowlist; FE has glyphs
  // for some of these (under similar but different keys) because of
  // the historic 'imprison' vs 'imprisoned' / 'war_won' vs
  // 'war_won_attacker' drift documented in the audit report.
  // ck3_chronicler-w2ux follow-up: pick canonical names and stop the
  // drift; for now we accept the current FE table and document the
  // gap here.
  'activity_completed',
  'adventurer_ended',
  'adventurer_started',
  'alliance_broken',
  'alliance_formed',
  'artifact_acquired',
  'artifact_lost',
  'building_completed',
  'culture_change',
  'decision_taken',
  'dynasty_legacy_unlocked',
  'epidemic_outbreak',
  'faith_change',
  'house_change',
  // 'imprison' falls through; FE has 'imprisoned' (drift).
  'imprison',
  'miscarriage',
  'nickname',
  // 'release' falls through; FE has 'released' (drift).
  'release',
  'title_acquired',
  'title_created',
  'title_relinquished',
  'trait_gained',
  'trait_lost',
  'travel',
  'vanilla_memory',
  'war_concluded',
  'war_declared',
  'war_joined',
  'war_left',
  // 'war_won_attacker' / 'war_won_defender' fall through; FE has
  // 'war_won' (drift).
  'war_won_attacker',
  'war_won_defender',
]);

describe('EventsPanel renderer coverage parity', () => {
  it('every backend event type either has a glyph or is on the fallback allowlist', () => {
    const unaccounted: string[] = [];
    for (const t of EVENT_TYPE_LITERALS) {
      const glyph = eventGlyph(t);
      const isFallback = glyph === '✦';
      if (isFallback && !EXPECTED_FALLBACK.has(t)) {
        unaccounted.push(t);
      }
    }
    expect(unaccounted, 'event types missing a glyph and not on the allowlist').toEqual(
      [],
    );
  });

  it('every entry in EVENT_GLYPHS has a matching color entry', () => {
    for (const key of Object.keys(EVENT_GLYPHS)) {
      expect(EVENT_COLOR_VAR[key], `missing color for ${key}`).toBeDefined();
    }
  });

  it('eventCanonicalKey returns "" for an unknown type', () => {
    expect(eventCanonicalKey('totally_made_up_event')).toBe('');
  });

  it('eventCanonicalKey returns exact match before prefix match', () => {
    // "marriage" must return "marriage", not be tempted into a prefix scan.
    expect(eventCanonicalKey('marriage')).toBe('marriage');
  });

  it('eventColor falls back to gold for unknown types', () => {
    expect(eventColor('not_a_real_type')).toBe('var(--gold-deep)');
  });
});

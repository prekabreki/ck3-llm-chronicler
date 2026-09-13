// Glyph + color lookup for the chronicle event roll. Extracted from
// EventsPanel.tsx so that component file exports only its component
// (react-refresh/only-export-components — ck3_chronicler-6nhb).
//
// The EventsPanel coverage test asserts parity against the schema's
// EVENT_TYPE_LITERALS — ck3_chronicler-w2ux.

export const EVENT_GLYPHS: Record<string, string> = {
  birth: '☩',
  death: '†',
  marriage: '⚭',
  divorce: '⚮',
  child_born: '☉',
  imprisoned: '⚷',
  released: '⚷',
  title_gain: '♕',
  title_lost: '♕',
  war_started: '⚔',
  war_won: '⚔',
};

export const EVENT_COLOR_VAR: Record<string, string> = {
  birth: 'var(--vert)',
  death: 'var(--carmine-deep)',
  marriage: 'var(--azure)',
  divorce: 'var(--carmine)',
  child_born: 'var(--vert)',
  imprisoned: 'var(--ink-muted)',
  released: 'var(--ink-soft)',
  title_gain: 'var(--gold-deep)',
  title_lost: 'var(--carmine)',
  war_started: 'var(--carmine)',
  war_won: 'var(--gold-deep)',
};

export function eventCanonicalKey(type: string): string {
  // CK3 event_type values vary across pipelines; tolerate underscores
  // and prefix forms (e.g. "death_natural" still keys off "death").
  if (type in EVENT_GLYPHS) return type;
  for (const key of Object.keys(EVENT_GLYPHS)) {
    if (type.startsWith(key)) return key;
  }
  return '';
}

export function eventGlyph(type: string): string {
  return EVENT_GLYPHS[eventCanonicalKey(type)] ?? '✦';
}

export function eventColor(type: string): string {
  return EVENT_COLOR_VAR[eventCanonicalKey(type)] ?? 'var(--gold-deep)';
}

// M-F9 (27ov.63): the canonical formatters. The decided behaviors are
// pinned here — especially the collisions that used to differ by file.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  formatDate,
  formatDateRange,
  formatRelativeIso,
  formatTokens,
  formatYearSpan,
} from './format';

describe('formatRelativeIso', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-13T12:00:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('buckets by age: just now / min / h / d', () => {
    expect(formatRelativeIso('2026-06-13T11:59:30Z')).toBe('just now');
    expect(formatRelativeIso('2026-06-13T11:45:00Z')).toBe('15 min ago');
    expect(formatRelativeIso('2026-06-13T07:00:00Z')).toBe('5 h ago');
    expect(formatRelativeIso('2026-06-10T12:00:00Z')).toBe('3 d ago');
  });

  it('falls back to the ISO date at 30+ days', () => {
    expect(formatRelativeIso('2026-04-01T12:00:00Z')).toBe('2026-04-01');
  });

  it('clamps future timestamps (clock skew) to "just now"', () => {
    expect(formatRelativeIso('2026-06-13T12:05:00Z')).toBe('just now');
  });

  it('returns unparseable input as-is', () => {
    expect(formatRelativeIso('not-a-date')).toBe('not-a-date');
  });
});

describe('formatDate', () => {
  it('takes the date part as written, no timezone shifting', () => {
    expect(formatDate('2026-06-13T01:00:00+10:00')).toBe('2026-06-13');
    expect(formatDate('2026-06-13')).toBe('2026-06-13');
  });
});

describe('formatYearSpan', () => {
  it('handles CK3 dotted dates and ISO dates', () => {
    expect(formatYearSpan('1066.9.15', '1278.3.1')).toBe('212 years');
    expect(formatYearSpan('1066-09-15', '1278-03-01')).toBe('212 years');
  });

  it('returns null for missing sides, junk, or a zero-year span', () => {
    expect(formatYearSpan(null, '1278.3.1')).toBeNull();
    expect(formatYearSpan('1066.9.15', null)).toBeNull();
    expect(formatYearSpan('junk', '1278.3.1')).toBeNull();
    expect(formatYearSpan('1066.9.15', '1066.12.1')).toBeNull();
  });
});

describe('formatDateRange', () => {
  it('joins both sides with an em dash', () => {
    expect(formatDateRange('1042.1.1', '1099.4.2')).toBe('1042.1.1 — 1099.4.2');
  });

  it('placeholders for one missing side, null when both missing', () => {
    expect(formatDateRange(null, '1099.4.2')).toBe('???? — 1099.4.2');
    expect(formatDateRange('1042.1.1', null)).toBe('1042.1.1 — —');
    expect(formatDateRange(null, null)).toBeNull();
  });
});

describe('formatTokens', () => {
  it('one decimal at every magnitude — 12500 is "12.5k" everywhere now', () => {
    expect(formatTokens(950)).toBe('950');
    expect(formatTokens(12_500)).toBe('12.5k');
    expect(formatTokens(1_200_000)).toBe('1.2m');
  });
});

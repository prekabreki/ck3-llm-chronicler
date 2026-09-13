// Shared display formatters (M-F9/27ov.63). These existed as 7 private
// copies across pages/components, with live same-name-different-behavior
// collisions (two formatSpan semantics; formatTokens rounding 12500 as
// '12.5k' in one file and '13k' in another). One canonical home; the
// decided behaviors are documented per function.

/** Wall-clock timestamp → compact relative time: "just now", "N min
 * ago", "N h ago", "N d ago"; anything 30+ days old shows the ISO date
 * instead (a "1543 d ago" reads worse than the date). Unparseable
 * input is returned as-is. */
export function formatRelativeIso(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return `${days} d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

/** ISO timestamp → its date part, as written (no timezone shifting). */
export function formatDate(iso: string): string {
  return iso.split('T')[0] ?? iso;
}

/** Year distance between two dates ("212 years"). Accepts CK3 dates
 * ("1066.9.15") and ISO dates ("1066-09-15"). Returns null when either
 * side is missing, unparseable, or the span rounds to zero years —
 * render nothing rather than "0 years". */
export function formatYearSpan(
  fromDate: string | null,
  toDate: string | null,
): string | null {
  if (!fromDate || !toDate) return null;
  const fy = parseInt(fromDate.split('.')[0] ?? fromDate.split('-')[0] ?? '', 10);
  const ty = parseInt(toDate.split('.')[0] ?? toDate.split('-')[0] ?? '', 10);
  if (!Number.isFinite(fy) || !Number.isFinite(ty)) return null;
  const years = Math.max(0, ty - fy);
  if (years === 0) return null;
  return `${years} years`;
}

/** Birth/death (or any from/to) pair → "from — to", with "????" for a
 * missing start and "—" for a missing end. Null when both missing. */
export function formatDateRange(
  from: string | null,
  to: string | null,
): string | null {
  if (!from && !to) return null;
  return `${from ?? '????'} — ${to ?? '—'}`;
}

/** Token count → compact magnitude: 950 → "950", 12500 → "12.5k",
 * 1_200_000 → "1.2m". One decimal everywhere — the old second variant
 * rounded 12500 to "13k", so the same spend read differently on
 * Tracked vs the regenerate estimate. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

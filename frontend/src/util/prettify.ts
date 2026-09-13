// CK3 surfaces culture / faith / similar tags as lowercase underscored
// ids (`estonian`, `finnish_pagan`, `roman_catholic`,
// `eastern_orthodox`). For display, replace underscores with spaces and
// title-case each word.
export function prettifyTag(value: string | null | undefined): string {
  if (!value) return '—';
  return value
    .split('_')
    .map((word) => (word ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(' ');
}

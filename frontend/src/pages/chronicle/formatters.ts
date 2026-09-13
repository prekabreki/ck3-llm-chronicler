// Audit F-27: shared text formatters extracted from ChroniclePage.
// Used by the main folio (provider byline). Generic date/relative-time
// formatters live in util/format.ts (M-F9) — only the chronicle-
// specific provider byline remains here.

export function prettyProvider(provider: string): string {
  // "ollama:qwen3:14b" → "Ollama · qwen3:14b"
  const idx = provider.indexOf(':');
  if (idx < 0) return provider;
  const head = provider.slice(0, idx);
  const tail = provider.slice(idx + 1);
  const cap = head.charAt(0).toUpperCase() + head.slice(1);
  return `${cap} · ${tail}`;
}

// ck3_chronicler-441 + r8i: blob downloads for chronicle exports.
// fetchJson can't be reused — we need the raw Response so we can
// (a) read the body as a Blob and (b) parse the filename out of
// Content-Disposition.

import { readApiError } from './client';

export type ExportFormat = 'markdown' | 'pdf';

export interface ExportResult {
  blob: Blob;
  filename: string;
}

export async function exportChronicle(
  campaignName: string,
  format: ExportFormat = 'markdown',
): Promise<ExportResult> {
  const resp = await fetch(
    `/api/campaigns/${encodeURIComponent(campaignName)}/export/${format}`,
    { method: 'POST' },
  );
  // audit F-12 + F-30: shared ApiError shape, shared body-parse path.
  if (!resp.ok) throw await readApiError(resp);
  const blob = await resp.blob();
  // Server sends both filename= and filename*=UTF-8''… ; prefer the
  // RFC-5987 form when present (handles non-ASCII campaign names).
  const cd = resp.headers.get('content-disposition') ?? '';
  const fallbackExt = format === 'pdf' ? 'pdf' : 'zip';
  let filename = `${campaignName}-chronicle.${fallbackExt}`;
  const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match && utf8Match[1]) {
    filename = decodeURIComponent(utf8Match[1].trim());
  } else {
    const plainMatch = cd.match(/filename="?([^";]+)"?/i);
    if (plainMatch && plainMatch[1]) {
      filename = plainMatch[1].trim();
    }
  }
  return { blob, filename };
}

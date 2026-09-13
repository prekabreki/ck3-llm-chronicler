// ck3_chronicler-441 + r8i: trigger the chronicle export download.
//
// Reusable across ClosingPage (primary) and LibraryPage Completed-card
// kebab (quiet variant). Owns its own pending + error state — no parent
// state management required.
//
// The `format` prop selects between the markdown bundle (zip) and the
// PDF (single file). Each page renders one button per format.

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { exportChronicle, type ExportFormat } from '../api/exportClient';

interface ExportChronicleButtonProps {
  campaignName: string;
  variant?: 'primary' | 'quiet';
  // ck3_chronicler: 'sm' applies the .btn--sm sizing token; 'md' is the
  // default 9px-16px padding. Library card menu uses 'sm' so the
  // export buttons match Rename/Delete siblings.
  size?: 'sm' | 'md';
  format?: ExportFormat;
  // Optional shorter labels for tight contexts (library card menu).
  // When omitted, the full 'Export chronicle…' / 'Export PDF…' labels
  // are used (preserving ClosingPage's primary-button presentation).
  compact?: boolean;
}

const LABEL_BY_FORMAT: Record<ExportFormat, { idle: string; pending: string }> = {
  markdown: { idle: 'Export chronicle…', pending: 'Preparing bundle…' },
  pdf: { idle: 'Export PDF…', pending: 'Rendering PDF…' },
};
const COMPACT_LABEL_BY_FORMAT: Record<ExportFormat, { idle: string; pending: string }> = {
  markdown: { idle: 'Export .md', pending: 'Preparing…' },
  pdf: { idle: 'Export .pdf', pending: 'Rendering…' },
};

export function ExportChronicleButton({
  campaignName,
  variant = 'primary',
  size = 'md',
  format = 'markdown',
  compact = false,
}: ExportChronicleButtonProps): React.JSX.Element {
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => exportChronicle(campaignName, format),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      // Some browsers require the anchor to be in the DOM before click.
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    },
  });

  const onClick = (e: React.MouseEvent): void => {
    // Stop propagation so clicks on the export button in a card-shaped
    // parent (e.g. LibraryPage's clickable card) don't also trigger
    // the parent's onClick (which would open the campaign).
    e.stopPropagation();
    setError(null);
    mutation.mutate();
  };

  const klass =
    (variant === 'quiet' ? 'btn btn--quiet' : 'btn') +
    (size === 'sm' ? ' btn--sm' : '');
  const labels = compact ? COMPACT_LABEL_BY_FORMAT[format] : LABEL_BY_FORMAT[format];
  const label = mutation.isPending ? labels.pending : labels.idle;

  return (
    <span className="export-chronicle">
      <button
        type="button"
        className={klass}
        onClick={onClick}
        disabled={mutation.isPending}
      >
        {label}
      </button>
      {error && (
        <span
          className="italic-fell export-chronicle__error"
          role="alert"
        >
          {error}
        </span>
      )}
    </span>
  );
}

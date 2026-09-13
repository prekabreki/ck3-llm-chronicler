// SavePicker — recent .ck3 saves from the configured save_dir, click
// a row to select. Used by ImportModal (per-campaign re-import) and
// AdoptSaveModal (new-campaign adoption) so both flows get the same
// "no typing required" affordance. Backend is /api/save/list (mb6q).
//
// Three render states:
//   - loading:        skeleton hint
//   - error / missing dir / empty: explanatory hint, no list
//   - populated:      mtime-desc list of clickable rows
//
// On click: onPick(info) — caller fills the path input + (typically)
// auto-submits.

import { useQuery } from '@tanstack/react-query';

import { listSaves } from '../api/client';
import { queryKeys } from '../api/queries';
import { formatRelativeIso } from '../util/format';
import type { SaveFileInfo, SaveListResponse } from '../api/types';

export function SavePicker({
  enabled = true,
  onPick,
  limit = 10,
}: {
  enabled?: boolean;
  onPick: (info: SaveFileInfo) => void;
  limit?: number;
}): React.JSX.Element {
  const query = useQuery<SaveListResponse>({
    queryKey: queryKeys.saveList(limit),
    queryFn: () => listSaves(limit),
    enabled,
    staleTime: 0, // fresh each time the modal opens — saves move fast
  });
  const { data, isLoading, isError } = query;

  if (isLoading) {
    return (
      <div className="save-picker">
        <div className="smallcaps save-picker__eyebrow">Recent saves</div>
        <div className="save-picker__hint italic-fell">loading…</div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="save-picker">
        <div className="smallcaps save-picker__eyebrow">Recent saves</div>
        <div className="save-picker__hint italic-fell">
          Couldn't read save_dir — type a path below.
        </div>
      </div>
    );
  }

  if (!data.save_dir_exists) {
    return (
      <div className="save-picker">
        <div className="smallcaps save-picker__eyebrow">Recent saves</div>
        <div className="save-picker__hint italic-fell">
          The configured save folder doesn't exist on this machine. Set
          a path in Settings, or paste one below.
        </div>
      </div>
    );
  }

  if (data.saves.length === 0) {
    return (
      <div className="save-picker">
        <div className="smallcaps save-picker__eyebrow">Recent saves</div>
        <div className="save-picker__hint italic-fell">
          No .ck3 files found in <code>{data.save_dir}</code>.
        </div>
      </div>
    );
  }

  return (
    <div className="save-picker">
      <div className="smallcaps save-picker__eyebrow">
        Recent saves in <code>{data.save_dir}</code>
      </div>
      <ul className="save-picker__list">
        {data.saves.map((info) => (
          <li key={info.abs_path}>
            <button
              type="button"
              className="save-picker__row"
              onClick={() => onPick(info)}
            >
              <span className="save-picker__name">{info.filename}</span>
              <span className="save-picker__meta">
                {formatRelativeIso(info.mtime_iso)} · {formatSize(info.size_bytes)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(1)} MB`;
}

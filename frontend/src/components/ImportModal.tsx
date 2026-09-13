// ImportModal — server-side save path picker that kicks off a save
// import + tracks progress via SSE. Triggered from the LibraryPage's
// "Import save" button.
//
// Wire flow:
//   1. POST /api/campaigns/{name}/import-save with {save_path} →
//      { import_id, sse_url }.
//   2. Open EventSource against sse_url; render stage/fraction/message.
//   3. When ``stage === 'done'`` close the connection and refresh
//      the campaign list so the import's effects show up.
//
// Stage names mirror the backend `ImportStage` literal in
// `src/chronicler/save/importer.py`. Drift here = silent regression
// (every import shows 0/N forever); see audit F-01.

import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { startImportSave } from '../api/client';
import { queryKeys } from '../api/queries';
import type {
  ImportStartedResponse,
  SaveFileInfo,
} from '../api/types';
import { ModalShell } from './ModalShell';
import { SavePicker } from './SavePicker';

type ImportStage =
  | 'read_save'
  | 'parse_history'
  | 'backfill_events'
  | 'generate_biographies'
  | 'done'
  | 'error';

interface ImportProgress {
  import_id: string;
  stage: ImportStage;
  fraction: number;
  message: string;
}

const STAGE_ORDER: ImportStage[] = [
  'read_save',
  'parse_history',
  'backfill_events',
  'generate_biographies',
  'done',
];

const STAGE_LABEL: Record<ImportStage, string> = {
  read_save: 'read save',
  parse_history: 'parse history',
  backfill_events: 'backfill events',
  generate_biographies: 'biographies',
  done: 'done',
  error: 'error',
};

interface ImportModalProps {
  campaignName: string;
  open: boolean;
  onClose: () => void;
}

export function ImportModal({
  campaignName,
  open,
  onClose,
}: ImportModalProps): React.JSX.Element | null {
  const qc = useQueryClient();
  const [savePath, setSavePath] = useState('');
  const [started, setStarted] = useState<ImportStartedResponse | null>(null);
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sseRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!started) return;
    if (
      typeof window === 'undefined' ||
      typeof window.EventSource !== 'function'
    ) {
      return;
    }
    const es = new EventSource(started.sse_url);
    sseRef.current = es;
    es.onmessage = (msg: MessageEvent): void => {
      try {
        const parsed = JSON.parse(msg.data) as ImportProgress;
        setProgress(parsed);
        if (parsed.stage === 'done') {
          qc.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
          qc.invalidateQueries({ queryKey: queryKeys.campaignAll(campaignName) });
          es.close();
        }
      } catch {
        // Malformed SSE frames are observability only.
      }
    };
    es.onerror = (): void => {
      // Browsers reconnect automatically; we just surface the state.
      setError('SSE connection interrupted — server may have completed.');
    };
    return (): void => {
      es.close();
      sseRef.current = null;
    };
  }, [started, qc, campaignName]);

  const closeAll = (): void => {
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
    setStarted(null);
    setProgress(null);
    setSavePath('');
    setError(null);
    onClose();
  };

  if (!open) return null;

  const submitWith = async (rawPath: string): Promise<void> => {
    setError(null);
    try {
      const result = await startImportSave(campaignName, rawPath.trim());
      setStarted(result);
      setProgress({
        import_id: result.import_id,
        stage: 'read_save',
        fraction: 0,
        message: 'Import queued',
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    void submitWith(savePath);
  };

  const onPickRow = (info: SaveFileInfo): void => {
    setSavePath(info.abs_path);
    void submitWith(info.abs_path);
  };

  return (
    <ModalShell
      titleId="import-modal-title"
      eyebrow="✦ Import a save"
      title="Add a chronicle leaf"
      onClose={closeAll}
    >
        {!started && (
          <form className="modal__body" onSubmit={onSubmit}>
            <p className="italic-fell modal__lede">
              Point the chronicler at a CK3 save file. The file must be
              accessible from the server (this is single-user, so that's
              just "your machine").
            </p>
            <SavePicker
              enabled={open && !started}
              onPick={onPickRow}
            />
            <div className="modal__dropzone">
              <div className="modal__dropzone-eyebrow">
                Or paste a path to a save outside that folder
              </div>
              <label className="modal__field">
                <span className="smallcaps modal__field-label">Save path</span>
                <input
                  type="text"
                  className="modal__input"
                  value={savePath}
                  onChange={(e) => setSavePath(e.target.value)}
                  placeholder="C:\Users\you\Documents\Paradox Interactive\…\autosave.ck3"
                />
              </label>
            </div>
            <div className="modal__actions">
              <button type="submit" className="btn" disabled={!savePath.trim()}>
                Begin import
              </button>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={closeAll}
              >
                Cancel
              </button>
            </div>
            {error && (
              <p
                className="italic-fell modal__error"
                role="alert"
              >
                {error}
              </p>
            )}
          </form>
        )}

        {started && progress && (
          <div className="modal__body">
            <div className="import-progress">
              <ProgressStages current={progress.stage} />
              <div className="import-progress__bar">
                <div
                  className="import-progress__fill"
                  style={{
                    width: `${Math.max(2, Math.round(progress.fraction * 100))}%`,
                  }}
                />
              </div>
              <p className="import-progress__message italic-fell">
                {progress.message}
              </p>
              <p className="smallcaps import-progress__id">
                #{started.import_id}
              </p>
            </div>
            {progress.stage === 'done' && (
              <div className="modal__actions">
                <button type="button" className="btn" onClick={closeAll}>
                  Done
                </button>
              </div>
            )}
            {error && (
              <p
                className="italic-fell modal__error"
                role="alert"
              >
                {error}
              </p>
            )}
          </div>
        )}
    </ModalShell>
  );
}

function ProgressStages({
  current,
}: {
  current: ImportStage;
}): React.JSX.Element {
  const idx = STAGE_ORDER.indexOf(current);
  const allDone = current === 'done';
  return (
    <ol className="import-stages">
      {STAGE_ORDER.map((stage, i) => {
        const isActive = stage === current;
        const isDone = (idx >= 0 && i < idx) || (allDone && stage !== 'done');
        return (
          <li
            key={stage}
            className={
              'import-stage' +
              (isActive ? ' import-stage--active' : '') +
              (isDone ? ' import-stage--done' : '')
            }
          >
            <span className="import-stage__bead">
              {isDone ? '✦' : isActive ? '·' : '○'}
            </span>
            <span className="smallcaps import-stage__label">
              {STAGE_LABEL[stage]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

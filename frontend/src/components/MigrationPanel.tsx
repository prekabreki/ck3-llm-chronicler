// ck3_chronicler-72a: Settings → Migration panel.
//
// Three states (clean, pending, running/done) plus a Restore-from-backup
// dropdown that's always present when a backup exists.

import { useState } from 'react';

import {
  useHaltSaveTail,
  useListMigrationBackups,
  useMigrationStatus,
  useRestoreBackup,
  useRunMigration,
} from '../api/queries';

export function MigrationPanel(): React.JSX.Element {
  const statusQ = useMigrationStatus();
  const backupsQ = useListMigrationBackups();
  const runMu = useRunMigration();
  const restoreMu = useRestoreBackup();
  const haltMu = useHaltSaveTail();
  const [restoreSelection, setRestoreSelection] = useState<string>('');

  if (statusQ.isLoading) {
    return (
      <div className="migration-panel paper paper--edged">
        <p className="italic-fell">Checking schemas…</p>
      </div>
    );
  }
  const status = statusQ.data;
  const backups = backupsQ.data ?? [];
  const hasPending =
    !!status &&
    (status.needs_migration.length > 0 || status.registry_needs_migration);

  // ck3_chronicler-72a: when /run returns 409 (save-tail running), the
  // mutation surfaces error.message starting with '409:'. Detect that
  // shape and offer a "Halt save-tail and retry" button.
  const runErrorMsg = runMu.error
    ? (runMu.error as Error).message
    : null;
  const isSaveTailRunningError = runErrorMsg?.startsWith('409:') ?? false;

  const onHaltAndRetry = (): void => {
    haltMu.mutate(undefined, {
      onSuccess: () => {
        // Give the orchestrator a beat to drain, then retry.
        setTimeout(() => runMu.mutate(), 500);
      },
    });
  };

  return (
    <div className="migration-panel paper paper--edged">
      <header className="migration-panel__head">
        <h3 className="uncial migration-panel__title">Schema migration</h3>
      </header>

      {!hasPending && !runMu.data && (
        <p className="italic-fell migration-panel__ok">
          All schemas current.
          {backups.length > 0 ? ` Last backup: ${backups[0]!.timestamp}.` : ''}
        </p>
      )}

      {hasPending && status && !runMu.data && (
        <>
          <p className="migration-panel__lede">
            The following need migration. A file-copy backup will be taken
            first; on failure the affected DB is automatically restored.
          </p>
          <ul className="migration-panel__list">
            {status.needs_migration.map((p) => (
              <li key={p.campaign_id}>
                <strong>{p.name}</strong>{' '}
                <span className="smallcaps">
                  ({p.current_head ?? 'unstamped'} → {p.target_head})
                </span>
              </li>
            ))}
            {status.registry_needs_migration && (
              <li>
                <strong>Registry</strong>{' '}
                <span className="smallcaps">
                  (missing columns: {status.registry_missing_columns.join(', ')})
                </span>
              </li>
            )}
          </ul>
          <button
            type="button"
            className="btn"
            disabled={runMu.isPending}
            onClick={() => {
              runMu.reset();
              runMu.mutate();
            }}
          >
            {runMu.isPending ? 'Migrating…' : 'Backup & Migrate'}
          </button>
          {isSaveTailRunningError && (
            <span className="migration-panel__halt">
              <button
                type="button"
                className="btn btn--quiet"
                disabled={haltMu.isPending}
                onClick={onHaltAndRetry}
              >
                {haltMu.isPending ? 'Halting…' : 'Halt save-tail and retry'}
              </button>
            </span>
          )}
          {runErrorMsg && (
            <p className="italic-fell migration-panel__err" role="alert">
              {runErrorMsg}
            </p>
          )}
        </>
      )}

      {runMu.data && (
        <div className="migration-panel__results">
          <p>
            {runMu.data.success
              ? `${runMu.data.results.length} migration${runMu.data.results.length === 1 ? '' : 's'} succeeded.`
              : 'Some migrations failed; affected DBs were restored from the backup taken seconds earlier.'}
          </p>
          {runMu.data.backup_dir && (
            <p className="smallcaps">Backup at {runMu.data.backup_dir}</p>
          )}
          <ul>
            {runMu.data.results.map((r) => (
              <li key={r.id}>
                {r.ok ? '✓' : '✗'} {r.id}
                {r.error && <span className="italic-fell"> — {r.error}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {backups.length > 0 && (
        <div className="migration-panel__restore">
          <label htmlFor="restore-backup-select" className="smallcaps">
            Restore from backup
          </label>
          <select
            id="restore-backup-select"
            value={restoreSelection}
            onChange={(e) => setRestoreSelection(e.target.value)}
          >
            <option value="">— select a backup —</option>
            {backups.map((b) => (
              <option key={b.path} value={b.path}>
                {b.timestamp} ({b.campaign_count} campaign
                {b.campaign_count === 1 ? '' : 's'})
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn--quiet"
            disabled={!restoreSelection || restoreMu.isPending}
            onClick={() => restoreSelection && restoreMu.mutate(restoreSelection)}
          >
            {restoreMu.isPending ? 'Restoring…' : 'Restore'}
          </button>
          {restoreMu.data && (
            <p className="italic-fell">
              Restored {restoreMu.data.restored} file(s).
            </p>
          )}
          {restoreMu.error && (
            <p className="italic-fell migration-panel__err" role="alert">
              {(restoreMu.error as Error).message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

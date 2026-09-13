// ck3_chronicler-72a: schema migration tool client helpers.

// audit F-30: re-uses the shared fetchJson from client.ts so all three
// clients have one error-parsing path. Was duplicating the same
// body-parse + ApiError construction inline.
import { fetchJson } from './client';
import type {
  MigrationBackupEntry,
  MigrationRunResponse,
  MigrationStatusResponse,
} from './types';

export function getMigrationStatus(): Promise<MigrationStatusResponse> {
  return fetchJson('/api/migrate/status');
}

export function runMigration(): Promise<MigrationRunResponse> {
  return fetchJson('/api/migrate/run', { method: 'POST' });
}

export function restoreFromBackup(backupDir: string): Promise<{ restored: number }> {
  return fetchJson('/api/migrate/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ backup_dir: backupDir }),
  });
}

export function listMigrationBackups(): Promise<MigrationBackupEntry[]> {
  return fetchJson('/api/migrate/backups');
}

export function haltSaveTail(): Promise<{ halted: boolean }> {
  return fetchJson('/api/migrate/halt-save-tail', { method: 'POST' });
}

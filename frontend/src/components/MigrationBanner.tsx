// ck3_chronicler-72a: Library banner that surfaces pending schema
// migrations. Click jumps to Settings → Migration panel.

import { useMigrationStatus } from '../api/queries';
import { useAppStore } from '../store/appStore';

export function MigrationBanner(): React.JSX.Element | null {
  const statusQ = useMigrationStatus();
  const setView = useAppStore((s) => s.setView);

  if (statusQ.isLoading || statusQ.isError || !statusQ.data) return null;
  const { needs_migration, registry_needs_migration } = statusQ.data;
  if (needs_migration.length === 0 && !registry_needs_migration) return null;

  const campaignBit =
    needs_migration.length > 0
      ? `${needs_migration.length} ${needs_migration.length === 1 ? 'campaign needs' : 'campaigns need'} migration`
      : null;
  const registryBit = registry_needs_migration ? 'the registry needs migration' : null;
  const message = [campaignBit, registryBit].filter(Boolean).join('; ');

  return (
    <div className="migration-banner" role="alert">
      <span className="migration-banner__msg">
        {message} before they can be safely opened.
      </span>
      <button
        type="button"
        className="btn btn--quiet"
        onClick={() => setView('settings')}
      >
        Open Settings →
      </button>
    </div>
  );
}

// Audit F-28: paths card — CK3 save dir + install dir overrides
// (ck3_chronicler-f9w.1). Extracted from SettingsPage.
//
// Issue #51 added the archive dir. It is the one row whose target
// chronicler *writes* rather than reads, so it carries two things the
// other rows don't need: that repointing it moves no files, and whether
// it sits inside a git checkout.

import { usePathsSettings, useUpdatePathsSettings } from '../../api/queries';
import { EditablePathRow } from './EditablePathRow';
import type { PathInfo, PathsSettingsUpdate } from '../../api/client';

export function PathsCard(): React.JSX.Element {
  const pathsQ = usePathsSettings();
  const data = pathsQ.data ?? null;

  return (
    <section id="paths" className="settings-card">
      <div className="settings-card__head">
        <h2 className="settings-card__title">Paths &amp; saves</h2>
        <p className="italic-fell settings-card__description">
          Where the chronicler reads from and writes to. Detected
          automatically; override here if your install is non-standard.
        </p>
      </div>
      <div className="settings-card__body">
        {pathsQ.isLoading && (
          <div className="paths-card__loading italic-fell">Reading…</div>
        )}
        {pathsQ.isError && (
          <div className="paths-card__error">
            Could not load path settings. {String(pathsQ.error)}
          </div>
        )}
        {data && (
          <div className="paths-card__rows">
            <PathRow
              field="save_dir"
              label="CK3 save directory"
              hint="Where CK3 writes .ck3 save files. Used by save-tail to discover and ingest games."
              info={data.save_dir}
            />
            <PathRow
              field="ck3_install_dir"
              label="CK3 install directory"
              hint="Steam library root for Crusader Kings III. Needed to extract heraldry assets."
              info={data.ck3_install_dir}
            />
            <PathRow
              field="archive_dir"
              label="Sealed campaign archive"
              hint="Where sealed campaigns are written as .db + .json snapshots — often the only copy of a finished game, and syncing them is your own vehicle (docs/archived-campaigns.md). Changing this moves nothing: existing snapshots stay where they are, and the Sealed shelf re-homes itself on the next startup."
              info={data.archive_dir}
              gitRoot={data.archive_git_root}
            />
          </div>
        )}
      </div>
    </section>
  );
}

interface PathRowProps {
  field: 'save_dir' | 'ck3_install_dir' | 'archive_dir';
  label: string;
  hint: string;
  info: PathInfo;
  // Only the archive row passes this; undefined on the read-only paths.
  gitRoot?: string | null;
}

function PathRow({
  field,
  label,
  hint,
  info,
  gitRoot,
}: PathRowProps): React.JSX.Element {
  const updateMutation = useUpdatePathsSettings();

  // F-56: hand-typed branch instead of `as never`. The dynamic
  // `{ [field]: value }` form widens to Record<string, …> which doesn't
  // satisfy PathsSettingsUpdate, so we build the body field-by-field.
  const buildPathUpdate = (value: string | null): PathsSettingsUpdate => {
    if (field === 'save_dir') return { save_dir: value };
    if (field === 'ck3_install_dir') return { ck3_install_dir: value };
    return { archive_dir: value };
  };

  return (
    <EditablePathRow
      label={label}
      hint={hint}
      source={info.source}
      resolved={info.resolved}
      override={info.override}
      emptyText="— not found —"
      resetTitle="Clear override; fall back to default"
      meta={
        <>
          <ExistsPip exists={info.exists} resolved={info.resolved} />
          {gitRoot !== undefined && <GitRootPip gitRoot={gitRoot} />}
        </>
      }
      isPending={updateMutation.isPending}
      errorText={updateMutation.isError ? String(updateMutation.error) : null}
      onSubmit={(value) => updateMutation.mutateAsync(buildPathUpdate(value))}
    />
  );
}

function ExistsPip({
  exists,
  resolved,
}: {
  exists: boolean;
  resolved: string;
}): React.JSX.Element {
  if (!resolved) {
    return (
      <span className="pip pip--missing" title="Path not configured">
        <span className="pip__dot pip__dot--missing" /> Not found
      </span>
    );
  }
  if (exists) {
    return (
      <span className="pip" title="Directory exists on disk">
        <span className="pip__dot pip__dot--alive" /> Exists
      </span>
    );
  }
  return (
    <span className="pip pip--warn" title="Directory does not exist">
      <span className="pip__dot pip__dot--warn" /> Missing
    </span>
  );
}

// Issue #51: sitting inside a checkout is not cosmetic — chronicler
// commits and pushes the snapshots it writes there. The default location
// is not in a repo, so say which it is rather than letting the user find
// out at seal time.
function GitRootPip({
  gitRoot,
}: {
  gitRoot: string | null;
}): React.JSX.Element {
  if (!gitRoot) {
    return (
      <span
        className="pip"
        title="Not inside a git repository — snapshots are written and left alone"
      >
        <span className="pip__dot" /> Not a git repo
      </span>
    );
  }
  return (
    <span
      className="pip pip--warn"
      title={`Inside the git checkout at ${gitRoot} — snapshots are committed and pushed there`}
    >
      <span className="pip__dot pip__dot--warn" /> Git repo
    </span>
  );
}

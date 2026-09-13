# Archived campaign snapshots

When you seal a campaign (the closing ceremony), chronicler writes two files into
the **archive dir**:

```
<archive dir>/<campaign-uuid>.db      a slimmed, consistent SQLite snapshot
<archive dir>/<campaign-uuid>.json    a metadata sidecar (name, dates, dynasty, …)
```

A third kind appears on deletion: `<campaign-uuid>.deleted`, a tombstone.

The snapshot is what makes a sealed campaign readable on another machine: on
startup, chronicler scans the archive dir and inserts any sidecar it does not
already have into its local registry, so the campaign appears on the Library's
Sealed shelf with its dynasties, biographies and family trees intact. Active
campaigns are never snapshotted; save-tail stays on the machine that plays them.

## Where it lives

Resolution order, the same as every other chronicler path:

1. the `archive_dir` setting in `settings.json`
2. the `CHRONICLER_ARCHIVE_DIR` environment variable
3. `<data dir>/archived` (the default), so
   `~/Documents/chronicler/archived` on Windows and macOS or
   `~/.local/share/chronicler/archived` on Linux

## Syncing it, with whatever you already use

Chronicler does not ship a sync service. The archive dir is a directory of small
files, and any of these work:

- **A synced folder.** Point `CHRONICLER_ARCHIVE_DIR` at a path inside Dropbox,
  Syncthing, iCloud Drive or similar. Sealing is a one-shot write, so there is no
  live-writer conflict to worry about the way there is for the live campaign DBs.
  Do not do this for the *data dir* as a whole: an in-flight save-tail fights
  cloud-sync's atomic-rename pattern.
- **A private git repo.** If the archive dir sits anywhere inside a git checkout,
  chronicler notices and commits the snapshot pair for you, then tries to push.
  Every step is best-effort: no remote, no network or no git binary leaves the
  files on disk and the seal successful. This is the closest thing to the old
  behaviour, and it is the only mode where chronicler runs git commands at all.
- **A NAS or a network share.** Same as a synced folder, if it is mounted when you
  seal.
- **Nothing.** One machine needs no sync. The snapshots are still worth having:
  they are a compact, portable copy of a finished campaign.

If you choose git, know what you are storing. SQLite files are binary, so git
cannot delta-compress them: every re-seal of a campaign appends a full new blob to
history, permanently. A handful of campaigns is fine; a repo you re-seal often
will grow. Only the `.db` and `.json` are committed, never the SQLite transients
(`*.db-wal`, `*.db-shm`, `*.db-journal`), which fold back into the `.db` on a
clean close and would otherwise be pure churn.

## Deletion, and why tombstones exist

Deleting a sealed campaign from the Library removes the local registry row, the
live DB, and the snapshot pair, then writes a `.deleted` tombstone into the
archive dir.

The tombstone is load-bearing, not bookkeeping. Without it, the other machine
still holds a registry row pointing at a snapshot, sees no tombstone, and
re-exports the campaign on its next startup: the deletion bounces back and the
campaign resurrects. With it, that machine prunes its row and its copy instead.
So a tombstone is written whatever your sync vehicle is, and never depends on git
being involved.

## Migrating from the old in-tree location

Before v1.0 the snapshots lived at `<repo>/data/archived/` and were committed to
the chronicler repo itself, which was the sync transport. That worked because the
remote was private and single-user; it cannot survive a public origin, where a user
cannot push and the maintainer's pushes would publish personal campaign data.

If you have snapshots from that era, chronicler logs a warning at startup naming
the exact command. It never moves them for you: they are your only copy of those
campaigns, and a silent relocation into a half-configured override is how that
copy gets lost.

```bash
# Linux / macOS
mkdir -p ~/.local/share/chronicler/archived
mv <repo>/data/archived/*.db <repo>/data/archived/*.json \
   ~/.local/share/chronicler/archived/
```

```powershell
# Windows
mkdir "$HOME\Documents\chronicler\archived"
move <repo>\data\archived\*.db "$HOME\Documents\chronicler\archived\"
move <repo>\data\archived\*.json "$HOME\Documents\chronicler\archived\"
```

Restart chronicler and the campaigns reappear on the Sealed shelf. The old
directory can then be deleted; `data/` as a whole is git-ignored now.

Snapshots already in that repo's history are not removed by moving the files. To
reclaim the space, rewrite history once with
`git filter-repo --path data/archived/ --invert-paths`.

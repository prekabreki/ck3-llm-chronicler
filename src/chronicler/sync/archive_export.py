"""Snapshot a sealed campaign so other machines can read it (ck3_chronicler-txuo).

Two halves:

- :func:`export_sealed_campaign` is called at the moment of seal (after
  the closing chronicle is persisted and the registry row is flipped to
  archived). It writes a consistent SQLite snapshot into
  ``<archive dir>/<id>.db`` plus a ``<id>.json`` metadata sidecar. When
  that directory happens to live inside a git repo, it also `git add` /
  `git commit` / `git push` so the new archived state reaches the user's
  other machines through that remote.

- :func:`bootstrap_archived_snapshots` is the read-side companion. On
  startup the secondary machine re-scans the archive dir and
  INSERT-OR-IGNOREs the sidecars into its local registry, so newly-arrived
  archived campaigns appear on the Library's Sealed shelf.

Issue #24 moved the archive dir out of the chronicler checkout. It used to
be ``<repo>/data/archived/``, committed to this repo on purpose: git was
the sync transport, which worked precisely because the remote was private
and single-user. A public origin breaks both halves of that (a user cannot
push to it; the owner's pushes would publish personal campaign data), so
the location is now configurable, defaults to the platform data dir, and
git is one sync vehicle among several rather than the mechanism.

That is why the two halves are decoupled here: the snapshot is always
written, and the git ladder runs only when the archive dir sits in a repo.
A user syncing that directory with a synced folder, a NAS or a private
repo of their own gets the same cross-machine behaviour with no git calls
from chronicler at all.

Failure semantics: every git step is best-effort. The seal succeeds
(closing chronicle is persisted, campaign row is archived) regardless
of whether the snapshot, commit, or push lands. The
:class:`ExportResult` returned to the API layer carries per-step state
so the FE can surface a "pending push" banner without blocking.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from chronicler.db.registry import Campaign
from chronicler.sync.git import _run_git, _stage_commit_push
from chronicler.sync.snapshot import (
    _serialize_campaign_sidecar,
    _serialize_tombstone,
    _slim_for_archive,
)

log = logging.getLogger(__name__)


# Issue #24: the pre-move in-tree location, kept only so an existing
# install can be told where its old snapshots are. Nothing writes here.
_LEGACY_ARCHIVE_SUBPATH = ("data", "archived")


@dataclass(frozen=True)
class ExportResult:
    """Per-step outcome of :func:`export_sealed_campaign`.

    Each flag answers "did this step land". The seal is considered
    successful regardless — these are diagnostics for the FE, not
    invariants. ``message`` carries a short human-readable summary;
    intended for log lines and a pending-push banner if any step is
    False.
    """

    snapshot_written: bool
    sidecar_written: bool
    committed: bool
    pushed: bool
    message: str


# Env var that disables archive-sync entirely. Tests set this in
# conftest.py so a stray /complete-route call from test_api can't write
# into the chronicler repo's working tree just because pytest happens
# to be running from inside the checkout. Production users never set
# it; the default behaviour is "auto-detect the chronicler checkout".
_DISABLE_ENV = "CHRONICLER_ARCHIVE_SYNC_DISABLED"


def archive_sync_enabled() -> bool:
    """Whether archive snapshotting runs at all.

    The disable switch exists for the test suite (conftest sets it) so a
    stray ``/complete`` call cannot write snapshots into a real archive
    dir just because pytest happens to be running. Production users never
    set it. Explicitly passing ``archive_dir=`` bypasses this, which is
    how the sync tests drive the real code paths against a tmp dir.
    """
    return not os.environ.get(_DISABLE_ENV)


def archived_campaigns_dir() -> Path:
    """The directory sealed-campaign snapshots live in.

    Issue #24: resolved from the ``archive_dir`` setting, then
    ``CHRONICLER_ARCHIVE_DIR``, then ``<data dir>/archived`` — the same
    precedence as every other structural path. Created on demand by the
    export step; safe to call before it exists.
    """
    from chronicler.config import default_archive_dir, resolve_archive_dir

    return resolve_archive_dir().value or default_archive_dir()


def resolve_archive_git_root(archive_dir: Path) -> Path | None:
    """The git checkout containing ``archive_dir``, or None.

    Issue #24: git is now an *optional* sync vehicle for the archive dir
    rather than the mechanism, so this asks a different question than the
    old ``resolve_repo_root`` did. That one located the *chronicler*
    checkout, which is why it had to be anchored on ``__file__`` and not
    the CWD (ck3_chronicler-27ov.5, audit H11: walking up from
    ``os.getcwd()`` meant launching ``chronicler dev`` from inside another
    checkout silently pushed multi-MB campaign DBs to that repo's remote).
    Anchoring on the archive dir itself removes that class of accident
    entirely: the only repo chronicler can ever commit into is the one the
    user pointed their archive dir at.

    Returns None when the archive dir is not inside a repo, which is the
    normal case for the default location. Callers then write the snapshot
    and leave syncing to whatever the user uses.

    No ``git`` invocation — a filesystem walk, so this works with no git
    binary installed.
    """
    try:
        resolved = archive_dir.resolve()
    except OSError:
        return None
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def legacy_in_tree_archive_dir() -> Path | None:
    """The pre-#24 ``<repo>/data/archived/`` if it still holds snapshots.

    Returns None when chronicler is not running from a checkout, when the
    directory is gone, or when it holds no ``*.db`` snapshot (a bare
    README or an empty dir is not unmigrated state).
    """
    from chronicler.config import find_repo_root

    try:
        root = find_repo_root()
    except RuntimeError:
        return None
    legacy = root.joinpath(*_LEGACY_ARCHIVE_SUBPATH)
    if not legacy.is_dir():
        return None
    return legacy if any(legacy.glob("*.db")) else None


def warn_if_legacy_archive_unmigrated(archive_dir: Path | None = None) -> str | None:
    """Log a prominent warning when old in-tree snapshots are stranded.

    Issue #24 chose *warn, never move*: the snapshots are the user's only
    copy of sealed campaigns, and a silent relocation at startup is the
    kind of helpfulness that loses data when the destination turns out to
    be a half-configured override. The warning carries the exact command.

    Only fires when the new location has nothing in it — once the user has
    sealed anything post-move, the old directory is history rather than a
    pending migration. Returns the logged message, or None.
    """
    legacy = legacy_in_tree_archive_dir()
    if legacy is None:
        return None
    if archive_dir is None:
        archive_dir = archived_campaigns_dir()
    if archive_dir.is_dir() and any(archive_dir.glob("*.db")):
        return None
    message = (
        f"issue #24: sealed-campaign snapshots are still in the old in-tree "
        f"location {legacy} and the archive dir {archive_dir} is empty. Those "
        f"campaigns will not appear in the Library until you move them:\n"
        f"    mkdir -p '{archive_dir}' && mv '{legacy}'/*.db '{legacy}'/*.json "
        f"'{archive_dir}'/\n"
        f"(then remove the emptied {legacy}). See docs/archived-campaigns.md."
    )
    log.warning("%s", message)
    return message


def _is_in_archive_dir(db_path: str, archive_dir: Path) -> bool:
    """True when ``db_path`` resolves to inside ``archive_dir`` — i.e. the
    registry row is a bootstrapped projection of an in-repo snapshot, not a
    genuine live campaign DB. ck3_chronicler-wvrm."""
    try:
        return Path(db_path).resolve().parent == archive_dir.resolve()
    except OSError:
        return False


def _is_snapshot_projection(db_path: str, archive_dir: Path) -> bool:
    """True when a registry row is a snapshot projection rather than a live
    campaign this machine owns — counting the pre-#24 in-tree location.

    The legacy arm is what makes the migration self-healing. A machine that
    bootstrapped rows before the move has ``db_path`` pointing at
    ``<repo>/data/archived/<id>.db``; once the user moves the files out,
    that row references nothing. Judged only by the *current* archive dir
    it stops looking like a projection, so nothing prunes it (Library shows
    a campaign that 500s on open) and nothing re-inserts it either, because
    the sidecar's id is already present. Counting the legacy path means the
    row is pruned and then re-inserted from the moved sidecar with a
    correct ``db_path``.

    It also keeps the resurrection guard honest: :func:`backfill_archived_campaigns`
    must never re-export a legacy-path row, since that would push another
    machine's campaign back out under this machine's hand.
    """
    if _is_in_archive_dir(db_path, archive_dir):
        return True
    legacy = _legacy_archive_dir_unchecked()
    return legacy is not None and _is_in_archive_dir(db_path, legacy)


def _legacy_archive_dir_unchecked() -> Path | None:
    """The pre-#24 in-tree archive path, whether or not it exists.

    :func:`legacy_in_tree_archive_dir` answers "is there unmigrated state
    to warn about" and so requires snapshots to be present; this one
    answers "what did that path used to be", which a post-move row needs
    precisely when the directory is already gone.
    """
    from chronicler.config import find_repo_root

    try:
        return find_repo_root().joinpath(*_LEGACY_ARCHIVE_SUBPATH)
    except RuntimeError:
        return None


def _tombstone_path(archive_dir: Path, campaign_id: str) -> Path:
    """Location of a campaign's deletion tombstone. ck3_chronicler-wvrm."""
    return archive_dir / f"{campaign_id}.deleted"


def export_sealed_campaign(
    campaign: Campaign,
    *,
    archive_dir: Path | None = None,
) -> ExportResult:
    """Snapshot ``campaign`` into the archive dir, and push it if that
    directory is inside a git repo.

    Designed to be called immediately after :func:`archive_campaign` in
    the closing-ceremony flow. Pure side-effect; returns an
    :class:`ExportResult` describing how far we got.

    ``archive_dir`` is resolved from settings/env/default when omitted.
    Passing it explicitly also bypasses the test-suite disable switch,
    which is how the sync tests exercise the real path.
    """
    if archive_dir is None:
        if not archive_sync_enabled():
            return ExportResult(False, False, False, False, "archive sync disabled")
        archive_dir = archived_campaigns_dir()

    out_dir = archive_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    db_target = out_dir / f"{campaign.id}.db"
    sidecar_target = out_dir / f"{campaign.id}.json"

    # Step 1: SQLite consistent snapshot. Use Connection.backup() — it
    # holds a read lock on the source while copying so a save-tail tick
    # mid-export can't smear the bytes. Doesn't matter today (the seal
    # flow stops save-tail on this campaign before we get here) but is
    # correct under any concurrent-writer scenario, which is cheap
    # insurance.
    #
    # After the backup, _slim_for_archive prunes data the read-only
    # browse surface doesn't need (events log + per-character
    # save_snapshot_json on non-protagonists). For a 60k-character
    # campaign this is the difference between a 130 MB blob (over
    # GitHub's 100 MB hard limit) and ~16 MB — verified live on the
    # Thrugot smoke 2026-05-09.
    try:
        live_db_path = Path(campaign.db_path)
        if not live_db_path.exists():
            # ck3_chronicler-wvrm: never open a missing source read-write —
            # sqlite3.connect would CREATE an empty file (this is what
            # materialised the stray empty Saar snapshot). A missing source
            # means the campaign was deleted/half-synced; report it, don't
            # forge a snapshot.
            log.info(
                "txuo: source DB %s missing; skipping export for %s",
                live_db_path,
                campaign.id,
            )
            return ExportResult(
                snapshot_written=False,
                sidecar_written=False,
                committed=False,
                pushed=False,
                message="source DB missing",
            )
        # Issue #54: `closing`, because a sqlite3 connection used directly as
        # a context manager COMMITS on exit — it does not close. The handles
        # then survive until GC, which on POSIX is invisible (an open file
        # renames fine) but on Windows locks `db_target` for the life of the
        # process: WinError 32 for anyone moving the snapshot afterwards,
        # including a user following docs/archived-campaigns.md while
        # chronicler runs.
        with (
            closing(sqlite3.connect(str(live_db_path))) as src,
            closing(sqlite3.connect(str(db_target))) as dst,
        ):
            src.backup(dst)
        _slim_for_archive(db_target)
    except sqlite3.Error as exc:
        log.warning(
            "txuo: failed to snapshot %s -> %s: %s",
            campaign.db_path,
            db_target,
            exc,
        )
        return ExportResult(
            snapshot_written=False,
            sidecar_written=False,
            committed=False,
            pushed=False,
            message=f"snapshot failed: {exc}",
        )

    # Step 2: metadata sidecar. Mirrors the registry row's identity +
    # state columns so the secondary machine can populate its local
    # registry without opening every .db. Stored as plain JSON for
    # diff-friendliness.
    try:
        sidecar_target.write_text(
            _serialize_campaign_sidecar(campaign),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("txuo: failed to write sidecar %s: %s", sidecar_target, exc)
        return ExportResult(
            snapshot_written=True,
            sidecar_written=False,
            committed=False,
            pushed=False,
            message=f"sidecar write failed: {exc}",
        )

    # Step 3: git add + commit + push via the shared ladder, IF the archive
    # dir lives in a repo. Best-effort throughout — no repo, a branch with
    # no remote, or simply being offline must not block sealing. Snapshot +
    # sidecar are on disk by now, so every outcome reports them as written.
    #
    # Issue #24: the no-repo case is now the normal one (the default archive
    # dir is under the data dir), and it is a success, not a skip: the files
    # the user's own sync vehicle needs are written.
    repo_root = resolve_archive_git_root(out_dir)
    if repo_root is None:
        log.info(
            "txuo: snapshotted sealed campaign %s (%s) -> %s (archive dir is "
            "not in a git repo; sync it with your own vehicle)",
            campaign.name,
            campaign.id,
            db_target,
        )
        return ExportResult(True, True, False, False, f"snapshot written to {db_target}")

    rel_db = db_target.relative_to(repo_root).as_posix()
    rel_sidecar = sidecar_target.relative_to(repo_root).as_posix()
    committed, pushed, detail = _stage_commit_push(
        repo_root, [rel_db, rel_sidecar], f"archive: seal {campaign.name}"
    )

    if detail == "nothing to commit":
        # Snapshot byte-identical to the previous push (e.g. backfill of an
        # already-exported campaign) — a no-op success, not a failure.
        log.info(
            "txuo: nothing to commit for %s (snapshot already up-to-date)",
            campaign.id,
        )
        return ExportResult(True, True, False, False, "snapshot already up-to-date")
    if not committed:
        log.warning("txuo: %s for campaign %s", detail, campaign.id)
        return ExportResult(True, True, False, False, detail)
    if not pushed:
        log.warning(
            "txuo: %s for %s — commit is local; next invocation can retry",
            detail,
            campaign.id,
        )
        return ExportResult(True, True, True, False, f"snapshot committed locally; {detail}")

    log.info(
        "txuo: exported sealed campaign %s (%s) -> %s + push",
        campaign.name,
        campaign.id,
        rel_db,
    )
    return ExportResult(True, True, True, True, f"exported and pushed {rel_db}")


def tombstone_campaign(
    campaign: Campaign,
    *,
    archive_dir: Path | None = None,
) -> ExportResult:
    """Record a campaign deletion so it propagates to other machines
    (ck3_chronicler-wvrm).

    Writes ``<archive dir>/<id>.deleted``, and when the archive dir is in a
    git repo also stages it plus the (already unlinked) snapshot pair,
    commits ``archive: delete <name>``, and pushes. Best-effort git with
    the same semantics as :func:`export_sealed_campaign` — the local delete
    is authoritative and succeeds regardless of commit/push outcome. The
    returned :class:`ExportResult` reuses the per-step flags
    (``snapshot_written`` means "tombstone written" here).

    Issue #24: the tombstone is written whether or not git is involved.
    That is the load-bearing half of ck3_chronicler-wvrm — a machine that
    pulls a snapshot whose tombstone never arrived re-exports it and
    resurrects the campaign — so it must not become conditional on the
    sync vehicle being git.
    """
    if archive_dir is None:
        if not archive_sync_enabled():
            return ExportResult(False, False, False, False, "archive sync disabled")
        archive_dir = archived_campaigns_dir()

    out_dir = archive_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tomb = _tombstone_path(out_dir, campaign.id)
    deleted_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        tomb.write_text(_serialize_tombstone(campaign, deleted_at), encoding="utf-8")
    except OSError as exc:
        log.warning("wvrm: failed to write tombstone %s: %s", tomb, exc)
        return ExportResult(False, False, False, False, f"tombstone write failed: {exc}")

    repo_root = resolve_archive_git_root(out_dir)
    if repo_root is None:
        log.info(
            "wvrm: tombstoned %s (%s) at %s (archive dir is not in a git repo)",
            campaign.name,
            campaign.id,
            tomb,
        )
        return ExportResult(True, False, False, False, f"tombstone written to {tomb}")

    rel_tomb = tomb.relative_to(repo_root).as_posix()
    rel_db = (out_dir / f"{campaign.id}.db").relative_to(repo_root).as_posix()
    rel_sidecar = (out_dir / f"{campaign.id}.json").relative_to(repo_root).as_posix()

    # Stage the tombstone plus the snapshot-pair REMOVALS. The endpoint has
    # already unlinked the snapshot pair on disk. Only reference the .db /
    # .json paths if git actually TRACKS them: a never-sealed campaign (or a
    # half-synced one whose snapshot was never committed) has no tracked
    # pair, and naming an absent+untracked pathspec made `git add`/`git
    # commit` fail with exit 128 — leaving the tombstone written-but-
    # uncommitted as untracked litter (ck3_chronicler-0ewl). A tracked-but-
    # now-deleted path is kept so `git add -A` records its removal.
    paths = [rel_tomb]
    for rel in (rel_db, rel_sidecar):
        tracked = _run_git(repo_root, "ls-files", "--error-unmatch", "--", rel)
        if tracked.returncode == 0:
            paths.append(rel)

    committed, pushed, detail = _stage_commit_push(
        repo_root, paths, f"archive: delete {campaign.name}"
    )
    if detail == "nothing to commit":
        return ExportResult(True, False, False, False, "tombstone already committed")
    if not committed:
        log.warning("wvrm: %s for %s", detail, campaign.id)
        return ExportResult(True, False, False, False, detail)
    if not pushed:
        log.warning("wvrm: %s for %s — commit is local", detail, campaign.id)
    log.info(
        "wvrm: tombstoned %s (%s)%s",
        campaign.name,
        campaign.id,
        "" if pushed else " (push pending)",
    )
    return ExportResult(True, False, True, pushed, f"tombstoned {campaign.id}")

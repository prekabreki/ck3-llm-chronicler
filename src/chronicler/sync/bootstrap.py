"""Read-side bootstrap + prune of pulled archive snapshots (ck3_chronicler-27ov.54).

``bootstrap_archived_snapshots`` projects pulled sidecars into the local
registry and ``backfill_archived_campaigns`` exports already-sealed rows
that predate archive-sync. Split out of archive_export.py (M-P5).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from chronicler.db.registry._core import _connect
from chronicler.sync.archive_export import (
    _is_snapshot_projection,
    _tombstone_path,
    archive_sync_enabled,
    archived_campaigns_dir,
    export_sealed_campaign,
)
from chronicler.sync.snapshot import _INSERT_FROM_SIDECAR_SQL, _row_from_sidecar

log = logging.getLogger(__name__)


def backfill_archived_campaigns(
    *,
    registry: Path | None = None,
    archive_dir: Path | None = None,
) -> int:
    """Export every archived campaign that doesn't yet have a snapshot in
    the archive dir. Returns the count of campaigns newly exported.

    Designed to be called from the FastAPI lifespan startup so that
    sealed campaigns predating txuo land in the archive dir without the
    user running a manual command. Idempotent — campaigns whose snapshot
    already exists at ``<archive dir>/<id>.db`` are skipped without
    touching git.

    Note: when the archive dir is in a git repo, each export does its own
    commit + push attempt, so a backfill of 5 already-sealed campaigns
    produces 5 commits. This is a one-time event per machine; subsequent
    boots are no-ops.
    """
    if archive_dir is None:
        if not archive_sync_enabled():
            return 0
        archive_dir = archived_campaigns_dir()
    out_dir = archive_dir

    # Lazy import to keep the registry → sync dependency edge directed
    # through callsites; same rationale as in
    # registry.campaigns.list_campaigns.
    from chronicler.db.registry import list_campaigns

    exported = 0
    for campaign in list_campaigns(include_archived=True, registry=registry):
        if not campaign.archived:
            continue
        if (out_dir / f"{campaign.id}.db").exists():
            continue
        # ck3_chronicler-wvrm: never re-export a campaign that was deleted
        # or is a bootstrapped snapshot projection — that is the
        # cross-machine resurrection bug. Only genuine live-DB seals on
        # THIS machine should ever be exported.
        if _tombstone_path(out_dir, campaign.id).exists():
            continue
        if _is_snapshot_projection(campaign.db_path, out_dir):
            continue
        if not Path(campaign.db_path).exists():
            log.debug(
                "txuo: backfill skip %s — live DB missing (%s)",
                campaign.name,
                campaign.db_path,
            )
            continue
        result = export_sealed_campaign(campaign, archive_dir=out_dir)
        if result.snapshot_written:
            exported += 1
            log.info(
                "txuo: backfilled sealed campaign %s -> %s",
                campaign.name,
                out_dir / f"{campaign.id}.db",
            )
        else:
            log.warning(
                "txuo: backfill failed for %s: %s",
                campaign.name,
                result.message,
            )
    return exported


def bootstrap_archived_snapshots(
    *,
    registry: Path | None = None,
    archive_dir: Path | None = None,
) -> int:
    """Insert sidecar rows from the archive dir into the local registry.
    Returns the number of rows newly inserted.

    Idempotent: any campaign id already present in the registry is
    left alone. Cheap to call repeatedly — typical archived count is
    single-digit and each sidecar is a small JSON. Designed so a secondary
    machine picks up newly-arrived archived campaigns on startup, whatever
    delivered them.
    """
    if archive_dir is None:
        if not archive_sync_enabled():
            return 0
        archive_dir = archived_campaigns_dir()
    out_dir = archive_dir
    if not out_dir.exists():
        return 0

    # ck3_chronicler-wvrm: prune pass — honor deletions before inserting.
    # Read campaign rows DIRECTLY via _connect; do NOT call list_campaigns
    # here — list_campaigns calls THIS function, so that would recurse
    # infinitely. delete_campaign is safe (it opens its own _connect and
    # does not call back into the sync layer).
    from chronicler.db.registry import delete_campaign

    tombstoned_ids = {p.stem for p in out_dir.glob("*.deleted")}

    with _connect(registry) as conn:
        prune_rows = conn.execute("SELECT id, name, db_path FROM campaigns").fetchall()

    for row in prune_rows:
        cid, cname, db_path = row["id"], row["name"], row["db_path"]
        # Two prunes with different blast radii, and conflating them would
        # delete data. A tombstoned campaign is really gone, so its files go
        # too. A row pointing at a snapshot that is no longer at that path is
        # merely stale, and the insert pass below re-adds it from whatever
        # sidecar IS present (which is why the prune runs first) — so its
        # files must be left exactly where they are.
        tombstoned = cid in tombstoned_ids
        # Both arms require the row's DB file to be GONE, which is what keeps
        # a live campaign out of reach of this branch entirely. Arm one: the
        # path is a known snapshot location (the current archive dir, or the
        # pre-#24 in-tree one). Arm two: wherever it pointed, the archive dir
        # has this campaign now — that covers an archive dir moved between
        # two unrelated paths, which the legacy arm alone cannot recognise.
        stale_projection = (
            not tombstoned
            and not Path(db_path).exists()
            and (_is_snapshot_projection(db_path, out_dir) or (out_dir / f"{cid}.db").exists())
        )
        if not (tombstoned or stale_projection):
            continue

        delete_campaign(cid, registry=registry)
        if stale_projection:
            # Issue #24: the common cause is the archive dir having moved
            # (the old in-tree location, or a changed override) — the row
            # is repaired on the insert pass with a correct db_path.
            log.info(
                "issue #24: re-homed stale snapshot row %s (%s); its db_path %s no longer exists",
                cname,
                cid,
                db_path,
            )
            continue

        # Best-effort file cleanup; tolerate locks (retry next pass).
        for suffix in (".db", ".json"):
            snap = out_dir / f"{cid}{suffix}"
            try:
                if snap.exists():
                    snap.unlink()
            except OSError as exc:
                log.warning("wvrm: could not unlink %s: %s", snap, exc)
        try:
            live = Path(db_path)
            if live.exists() and not _is_snapshot_projection(db_path, out_dir):
                live.unlink()
        except OSError as exc:
            log.warning("wvrm: could not unlink live DB %s: %s", db_path, exc)
        log.info("wvrm: pruned deleted campaign %s (%s)", cname, cid)

    inserted = 0
    with _connect(registry) as conn:
        for sidecar_path in sorted(out_dir.glob("*.json")):
            try:
                payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning(
                    "txuo: skipping malformed sidecar %s: %s",
                    sidecar_path,
                    exc,
                )
                continue
            campaign_id = payload.get("id")
            if not isinstance(campaign_id, str) or not campaign_id:
                log.warning(
                    "txuo: skipping sidecar %s with missing/empty id",
                    sidecar_path,
                )
                continue
            if campaign_id in tombstoned_ids:
                # wvrm: a tombstone overrides a lingering sidecar — never
                # re-insert a deleted campaign.
                continue
            existing = conn.execute(
                "SELECT id FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if existing is not None:
                # Already in the registry — either this is the primary
                # machine (live row predates the snapshot) or a
                # secondary that has already bootstrapped this id.
                # Either way, leave the row alone; the local registry
                # is the source of truth past first-discovery.
                continue
            db_target = out_dir / f"{campaign_id}.db"
            if not db_target.exists():
                log.warning(
                    "txuo: sidecar %s has no companion .db at %s; skipping bootstrap",
                    sidecar_path,
                    db_target,
                )
                continue
            try:
                conn.execute(_INSERT_FROM_SIDECAR_SQL, _row_from_sidecar(payload, db_target))
                inserted += 1
                log.info(
                    "txuo: bootstrapped archived campaign %s (%s) from %s",
                    payload.get("name"),
                    campaign_id,
                    sidecar_path.name,
                )
            except sqlite3.Error as exc:
                log.warning(
                    "txuo: insert failed for %s: %s",
                    sidecar_path,
                    exc,
                )
    return inserted

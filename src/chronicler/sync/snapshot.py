"""Archive snapshot slimming + sidecar serde (ck3_chronicler-27ov.54 / M-P5).

``_slim_for_archive`` (prunes the snapshot SQLite to the browse-only
surface) and the JSON sidecar <-> registry-row serialisers, split out of
archive_export.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from chronicler.db.registry import Campaign

log = logging.getLogger(__name__)


# --- internal helpers ---
# Mirror of the registry's identity / state columns. Listed explicitly so
# a future schema bump on either side surfaces as a clean failure (KeyError
# on serialise, missing column on insert) rather than silent drift.
_SIDECAR_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "ck3_version",
    "ck3_playthrough_id",
    "created_at",
    "last_event_at",
    "founding_dynasty_name",
    "closing_chronicle",
    "closing_chronicle_generated_at",
    "bookmark_date",
    "current_in_game_date",
    "current_player_character_id",
    "current_player_name",
    "current_player_nickname",
    "current_house_name",
    "current_player_gold",
    "current_player_prestige_lifetime",
    "current_player_piety",
    "current_dynasty_renown",
)


def _serialize_campaign_sidecar(campaign: Campaign) -> str:
    """Build the JSON sidecar payload for ``campaign``. Mirrors
    :data:`_SIDECAR_FIELDS` exactly so the import side can rely on the
    schema being stable across versions of chronicler that ship the
    same _SIDECAR_FIELDS tuple."""
    payload = {field: getattr(campaign, field) for field in _SIDECAR_FIELDS}
    # JSON for stable diff output: sorted keys, 2-space indent, trailing
    # newline. The file is committed-and-pulled, so diff-friendliness
    # matters more than wire size.
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _serialize_tombstone(campaign: Campaign, deleted_at: str) -> str:
    """JSON body for a deletion tombstone. ck3_chronicler-wvrm. Same
    formatting as the sidecar (sorted keys, 2-space indent, trailing
    newline) for diff-friendliness."""
    payload = {"id": campaign.id, "name": campaign.name, "deleted_at": deleted_at}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


_INSERT_FROM_SIDECAR_SQL = """
    INSERT INTO campaigns (
        id, name, ck3_version, ck3_playthrough_id, created_at, last_event_at,
        archived, db_path, founding_dynasty_name,
        closing_chronicle, closing_chronicle_generated_at,
        bookmark_date, current_in_game_date,
        current_player_character_id, current_player_name,
        current_player_nickname, current_house_name,
        current_player_gold, current_player_prestige_lifetime,
        current_player_piety, current_dynasty_renown
    ) VALUES (
        ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
"""


def _row_from_sidecar(payload: dict, db_target: Path) -> tuple:
    """Build the positional INSERT tuple matching :data:`_INSERT_FROM_SIDECAR_SQL`.

    ``archived`` is hard-coded to 1: the sidecar would not exist if the
    campaign weren't sealed. ``db_path`` points at the in-repo snapshot
    so registry consumers (CampaignOverview, Codex, …) read straight
    from the committed file."""
    return (
        payload.get("id"),
        payload.get("name"),
        payload.get("ck3_version"),
        payload.get("ck3_playthrough_id"),
        payload.get("created_at"),
        payload.get("last_event_at"),
        str(db_target),
        payload.get("founding_dynasty_name"),
        payload.get("closing_chronicle"),
        payload.get("closing_chronicle_generated_at"),
        payload.get("bookmark_date"),
        payload.get("current_in_game_date"),
        payload.get("current_player_character_id"),
        payload.get("current_player_name"),
        payload.get("current_player_nickname"),
        payload.get("current_house_name"),
        payload.get("current_player_gold"),
        payload.get("current_player_prestige_lifetime"),
        payload.get("current_player_piety"),
        payload.get("current_dynasty_renown"),
    )


# --- snapshot slimming ---
# Tables whose rows are pure ingest-time machinery (per-tick event log,
# the FTS5 docsize/idx shadows that biographies_fts depends on, etc.)
# — read-only sealed-browse surfaces never query them. Wiping them at
# export time reclaims the bulk of the typical campaign DB bytes.
_DROPPABLE_ROWS_TABLES: tuple[str, ...] = (
    "events",
    "event_participants",
)


def _slim_for_archive(db_path: Path) -> None:
    """Reduce a snapshot DB to the read-only-browse footprint.

    Two passes:

    1. Walk biography rows to find every "protagonist" character (the
       ones the sealed Library actually surfaces) and collect their
       immediate family from each protagonist's
       ``save_snapshot_json.family_data``. NULL out
       ``save_snapshot_json`` on every other character — preserves the
       basic identity columns the Codex/Tracked listings render but
       drops the ~1 KB-per-row save tree dump that dominates DB size
       on a 60k-character campaign.
    2. Truncate the per-tick event tables (``events``,
       ``event_participants``). The closing ceremony's chronicle text
       is on the registry row, not in events, so dropping events
       doesn't affect any read-only surface.

    Final ``VACUUM`` reclaims the freed pages so the file size on
    disk actually shrinks (otherwise SQLite keeps the pages around
    for reuse and the file stays its original size — committing a
    sparsely-occupied 130 MB SQLite is what GitHub rejects).

    Plan cozy-coalescing-shannon: the protagonist walk previously also
    unioned ``memories.character_id`` into the keep-set; with the
    memories table dropped, biographies are the only protagonist
    signal left. The slim pass no longer touches ``memories`` in
    either direction, so the table is simply left as-is in pre-v0.12
    snapshots until the next alembic upgrade drops it.
    """
    # First pass: identify protagonists + 1-hop family.
    keep_ids: set[int] = set()
    # Issue #54: `closing` — a sqlite3 connection as a bare context manager
    # commits but never closes, and the surviving handle locks this file on
    # Windows. See the note in archive_export.py.
    with closing(sqlite3.connect(str(db_path))) as conn:
        existing_tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        # Older campaign DBs (or test fixtures) may lack one of these
        # tables. Skip what isn't there rather than aborting the whole
        # slim — the goal is "shrink as much as we can," not "fail
        # closed on schema drift."
        if "biographies" in existing_tables:
            for row in conn.execute("SELECT DISTINCT character_id FROM biographies"):
                if isinstance(row[0], int):
                    keep_ids.add(row[0])
        if "characters" not in existing_tables:
            # Nothing to slim. The marker-only fixtures and
            # pre-schema-init DBs land here.
            return
        # Walk one hop of family for each protagonist so the Lineage
        # tree on the sealed page still resolves immediate parents,
        # spouses, and children. Deeper trees fall back to the
        # "skipped because save_snapshot_json was pruned" path; the
        # FE renders the seed + 1 generation cleanly.
        family_seeds = list(keep_ids)
        for cid in family_seeds:
            row = conn.execute(
                "SELECT save_snapshot_json FROM characters WHERE ck3_id = ?",
                (cid,),
            ).fetchone()
            if not row or not row[0]:
                continue
            try:
                snap = json.loads(row[0])
            except json.JSONDecodeError:
                continue
            family = snap.get("family_data") if isinstance(snap, dict) else None
            if not isinstance(family, dict):
                continue
            for k in ("mother", "father", "primary_spouse", "concubinist"):
                v = family.get(k)
                if isinstance(v, dict):
                    v = v.get("id")
                if isinstance(v, int):
                    keep_ids.add(v)
            for k in (
                "spouse",
                "concubine",
                "betrothed",
                "former_spouses",
                "child",
            ):
                v = family.get(k)
                if not isinstance(v, list):
                    continue
                for item in v:
                    if isinstance(item, dict):
                        item = item.get("id")
                    if isinstance(item, int):
                        keep_ids.add(item)

        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            conn.execute(
                f"UPDATE characters SET save_snapshot_json = NULL "
                f"WHERE ck3_id NOT IN ({placeholders})",
                tuple(keep_ids),
            )
        else:
            # No biographies → no protagonists to anchor on, so wipe
            # save_snapshot_json wholesale. The Codex / Tracked basic
            # listing still works; the Lineage tree is just empty.
            conn.execute("UPDATE characters SET save_snapshot_json = NULL")

        for table in _DROPPABLE_ROWS_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError as exc:
                # Schema drift between chronicler versions — older
                # campaign DBs may not have all the tables we know
                # about today. That's fine; nothing to delete means
                # nothing to reclaim.
                log.debug(
                    "txuo: slim skip drop %s on %s: %s",
                    table,
                    db_path,
                    exc,
                )
        conn.commit()

    # Final pass: VACUUM in autocommit mode so SQLite reclaims free
    # pages and the on-disk file actually shrinks.
    with closing(sqlite3.connect(str(db_path), isolation_level=None)) as conn:
        conn.execute("VACUUM")

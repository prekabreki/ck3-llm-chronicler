"""Per-campaign DB backfills run lazily at app startup.

Each backfill is a one-shot fix-existing-rows pass — it patches data
that was written under an older code path so the FE consumers see
the same shape they would after a fresh save-tail tick. Designed to
be:

- Idempotent — re-running on an already-backfilled DB does no work.
- Exactly-once per campaign (ck3_chronicler-27ov.50, audit M-B4) — a
  ``backfill_completions`` marker table in the registry records which
  (backfill, campaign) pairs have run, so a pass like x2qj's ~70k-row
  Python decode doesn't rescan every character row on every boot
  forever. A campaign that arrives later (bootstrapped snapshot from
  another machine) has no marker and gets its one pass on the next
  startup. Forward-looking writes use the corrected code paths, so one
  pass per campaign is sufficient.
- Fire-and-forget — the FastAPI lifespan dispatches them in a worker
  thread so a slow backfill can't delay first paint of the SPA.

Each helper returns a count of rows touched so the lifespan handler
can log a summary line on startup.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from chronicler.db.engine import make_engine_for_existing_path
from chronicler.db.registry import list_campaigns

log = logging.getLogger(__name__)


# --- ck3_chronicler-27ov.50: exactly-once completion markers ---

_MARKERS_DDL = (
    "CREATE TABLE IF NOT EXISTS backfill_completions ("
    "  backfill_key TEXT NOT NULL,"
    "  campaign_id TEXT NOT NULL,"
    "  completed_at TEXT NOT NULL,"
    "  PRIMARY KEY (backfill_key, campaign_id)"
    ")"
)


def _registry_connect(registry_path: Path | None):
    # Lazy import to keep the registry → backfill dependency one-way at
    # module load.
    from chronicler.db.registry.campaigns import (
        _connect,  # type: ignore[attr-defined]
    )

    return _connect(registry_path)


def _completed_campaign_ids(backfill_key: str, registry_path: Path | None) -> set[str]:
    with _registry_connect(registry_path) as conn:
        conn.execute(_MARKERS_DDL)
        rows = conn.execute(
            "SELECT campaign_id FROM backfill_completions WHERE backfill_key = ?",
            (backfill_key,),
        ).fetchall()
        return {row[0] for row in rows}


def _mark_completed(backfill_key: str, campaign_id: str, registry_path: Path | None) -> None:
    with _registry_connect(registry_path) as conn:
        conn.execute(_MARKERS_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO backfill_completions "
            "(backfill_key, campaign_id, completed_at) VALUES (?, ?, ?)",
            (backfill_key, campaign_id, datetime.now(UTC).isoformat()),
        )


def campaign_db_is_usable(db_path: Path, *required_tables: str) -> bool:
    """Return True iff ``db_path`` exists as a real SQLite DB containing
    every name in ``required_tables``.

    ck3_chronicler-wvrm: opens read-only via the ``file:...?mode=ro`` URI
    so a missing path raises instead of SQLite silently CREATING an empty
    file (the root cause of the Saar "no such table" startup warnings —
    a half-synced archived campaign whose snapshot was deleted on another
    machine). Never creates a file; never raises.

    Issue #8: this is a check, so it is inherently time-of-check /
    time-of-use. The backfills run on a background thread from the API
    lifespan, concurrently with request handling, so a campaign can be
    deleted between this returning True and the caller connecting. The
    caller must therefore *also* be unable to create the file — hence
    :func:`chronicler.db.engine.make_engine_for_existing_path`. Neither
    half is sufficient alone: without this guard every missing DB raises
    and logs, and without the non-creating engine the race resurrects the
    deleted DB as a stub.
    """
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        present = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return all(t in present for t in required_tables)


def backfill_dynasty_name_from_house_name(
    *,
    registry_path: Path | None = None,
) -> int:
    """ck3_chronicler-te8s (2026-05-09): set ``dynasty_name = house_name``
    on every Character row where the dynasty column is NULL but the
    house column is populated.

    Background: ~30% of characters across every campaign DB have a
    NULL dynasty_name today because their dynasty record in the save
    is unresolvable (engine-defined dynasty whose only id is an
    integer locale key, OR a dynasty referenced by dynasty_house but
    absent from dynasties.dynasties). CK3 itself displays the house
    name as the dynasty in those cases, so the same fallback restores
    the Dynasty page header, the briefing's 'Known names' line, and
    every other surface that keys on dynasty_name.

    The forward-looking fix lives in save/ingest.py + save/importer.py
    (apply the same fallback at write time). This function is the
    retroactive sibling that fixes existing rows on machines where
    the campaign was sealed or the original save file is gone, so
    re-running save-tail isn't an option.

    Returns the total count of rows updated across all campaigns
    (active + archived). Skips campaigns whose db_path is missing on
    disk — that case is normal during a half-cloned repo state.
    """
    total = 0
    done = _completed_campaign_ids("te8s", registry_path)
    for campaign in list_campaigns(include_archived=True, registry=registry_path):
        if campaign.id in done:
            continue  # 27ov.50: exactly-once per campaign
        db_path = Path(campaign.db_path)
        if not campaign_db_is_usable(db_path, "characters"):
            log.debug(
                "te8s backfill: campaign %s db unusable (%s); skipping",
                campaign.name,
                db_path,
            )
            continue
        try:
            updated = _backfill_one_campaign(db_path)
        except Exception as exc:  # noqa: BLE001 — observability, never block boot
            log.warning(
                "te8s backfill: failed for campaign %s (%s): %s",
                campaign.name,
                db_path,
                exc,
            )
            continue
        if updated:
            log.info(
                "te8s backfill: %s — set dynasty_name = house_name on %d row(s)",
                campaign.name,
                updated,
            )
        total += updated
        _mark_completed("te8s", campaign.id, registry_path)
    return total


def _backfill_one_campaign(db_path: Path) -> int:
    """Run the actual UPDATE on one campaign DB. Returns rows touched.

    Uses a fresh engine + dispose() — the EngineCache caches engines
    long-term, but the backfill runs once at startup and we don't need
    to keep the connection around. Disposing here keeps the pool
    pressure bounded when the user has many archived campaigns.
    """
    engine = make_engine_for_existing_path(db_path)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE characters "
                    "SET dynasty_name = house_name "
                    "WHERE dynasty_name IS NULL "
                    "  AND house_name IS NOT NULL"
                )
            )
            return result.rowcount or 0
    finally:
        engine.dispose()


def backfill_last_event_in_game_date(
    *,
    registry_path: Path | None = None,
) -> int:
    """ck3_chronicler-9xa6 (2026-05-11): populate the registry's
    ``last_event_in_game_date`` column from each campaign's per-DB
    ``MAX(event_date_iso) FROM events`` for any campaign that doesn't
    have it set yet.

    Background: pre-9xa6, the registry only carried a wall-clock
    ``last_event_at``. The Closing page used it for "Closed" + Span,
    giving "Span 960 years" for a 1066-founded campaign. The new
    column carries the in-game date so Span lands in CK3 time. Existing
    campaigns need a one-shot backfill because no save-tail tick has
    populated the column yet (and may never run again for sealed
    campaigns).

    Forward-looking write happens in save/ingest.py via the in_game_date
    kwarg on touch_last_event_at. This function is the retroactive
    sibling for rows that pre-date the change.

    Returns the total count of registry rows updated. Skips campaigns
    whose db_path is missing on disk, whose events table has no rows
    with a non-null event_date_iso, or whose ``last_event_in_game_date``
    is already populated (idempotent).

    27ov.50 note: this backfill needs no ``backfill_completions``
    marker — the populated registry column IS its completion marker,
    and the residual per-boot cost for a still-empty campaign is one
    MAX() query (the campaign is active, so the forward-looking write
    populates the column on its next tick anyway).
    """
    total = 0
    for campaign in list_campaigns(include_archived=True, registry=registry_path):
        if campaign.last_event_in_game_date is not None:
            continue  # idempotent — skip already-populated rows
        db_path = Path(campaign.db_path)
        if not campaign_db_is_usable(db_path, "events"):
            log.debug(
                "9xa6 backfill: campaign %s db unusable (%s); skipping",
                campaign.name,
                db_path,
            )
            continue
        try:
            max_iso = _max_event_date_iso(db_path)
        except Exception as exc:  # noqa: BLE001 — observability, never block boot
            log.warning(
                "9xa6 backfill: read failed for campaign %s (%s): %s",
                campaign.name,
                db_path,
                exc,
            )
            continue
        if max_iso is None:
            continue  # no events with a parseable date yet
        with _registry_connect(registry_path) as conn:
            conn.execute(
                "UPDATE campaigns SET last_event_in_game_date = ? WHERE id = ?",
                (max_iso, campaign.id),
            )
        log.info(
            "9xa6 backfill: %s — set last_event_in_game_date = %s",
            campaign.name,
            max_iso,
        )
        total += 1
    return total


def _max_event_date_iso(db_path: Path) -> str | None:
    """Return MAX(event_date_iso) from one campaign DB, or None when the
    table is empty / has no parseable dates.

    Engine pattern mirrors _backfill_one_campaign — fresh engine,
    explicit dispose, so the EngineCache isn't polluted by one-shot
    backfill connections.
    """
    engine = make_engine_for_existing_path(db_path)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT MAX(event_date_iso) FROM events")).fetchone()
    finally:
        engine.dispose()
    if row is None:
        return None
    value = row[0]
    return str(value) if value is not None else None


def backfill_redecode_character_names(
    *,
    registry_path: Path | None = None,
) -> int:
    """ck3_chronicler-x2qj: re-decode every character's ``first_name`` and
    ``nickname`` with their persisted culture, so the post-decode
    Unicode fixup table catches engine-decoded forms (e.g. "Ørvar" →
    "Örvar" for norse culture).

    Background: ``decode_ck3_name`` originally only handled CK3's
    letter+underscore escape form (``O_rvar`` → ``Örvar``). When CK3
    pre-localised a name to Unicode before serializing (``Ørvar``),
    the decoder was a no-op and the upsert overwrote the previously-
    correct ``Örvar`` with ``Ørvar``. The new
    :data:`_CULTURE_UNICODE_FIXUP` table normalizes both forms; this
    backfill applies that fixup to existing rows so the user doesn't
    have to wait for a save-tail tick (and so it works for non-tracked
    characters that save-tail never touches again).

    Idempotent: characters whose decoded name matches the stored value
    are not updated. Per-row Python iteration (we need to call
    ``decode_ck3_name`` per row, which can't be expressed in SQL).
    Cheap in practice — campaigns top out at ~70k characters; one decode
    per row is microseconds.

    Returns the total count of rows updated across all campaigns
    (active + archived). Skips campaigns whose db_path is missing.
    """
    # Lazy import to avoid a save→db cycle at module load.
    from chronicler.save.localization import decode_ck3_name

    total = 0
    done = _completed_campaign_ids("x2qj", registry_path)
    for campaign in list_campaigns(include_archived=True, registry=registry_path):
        if campaign.id in done:
            continue  # 27ov.50: exactly-once — this is the expensive pass
        db_path = Path(campaign.db_path)
        if not campaign_db_is_usable(db_path, "characters"):
            log.debug(
                "x2qj backfill: campaign %s db unusable (%s); skipping",
                campaign.name,
                db_path,
            )
            continue
        try:
            updated = _redecode_names_one_campaign(db_path, decode_ck3_name)
        except Exception as exc:  # noqa: BLE001 — observability, never block boot
            log.warning(
                "x2qj backfill: failed for campaign %s (%s): %s",
                campaign.name,
                db_path,
                exc,
            )
            continue
        if updated:
            log.info(
                "x2qj backfill: %s — re-decoded names on %d row(s)",
                campaign.name,
                updated,
            )
        total += updated
        _mark_completed("x2qj", campaign.id, registry_path)
    return total


def _redecode_names_one_campaign(db_path: Path, decode) -> int:  # type: ignore[no-untyped-def]
    """Run the per-row re-decode on one campaign DB. Returns rows touched.

    Reads ``(ck3_id, culture, first_name, nickname)`` for every
    character with a non-null culture, runs the decoder, writes back
    only the rows whose decoded value differs from the stored value.
    Skips rows whose culture is NULL since the culture-aware fixup
    can't fire without one.
    """
    engine = make_engine_for_existing_path(db_path)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT ck3_id, culture, first_name, nickname "
                    "FROM characters "
                    "WHERE culture IS NOT NULL"
                )
            ).fetchall()
            updated = 0
            for ck3_id, culture, first_name, nickname in rows:
                new_first = decode(first_name, culture=culture)
                new_nick = decode(nickname, culture=culture)
                if new_first == first_name and new_nick == nickname:
                    continue
                conn.execute(
                    text(
                        "UPDATE characters "
                        "SET first_name = :first_name, nickname = :nickname "
                        "WHERE ck3_id = :ck3_id"
                    ),
                    {
                        "first_name": new_first,
                        "nickname": new_nick,
                        "ck3_id": ck3_id,
                    },
                )
                updated += 1
            return updated
    finally:
        engine.dispose()

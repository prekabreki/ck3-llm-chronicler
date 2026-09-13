"""Campaign rows + cqo identity denormalisation.

One of three concerns in the chronicler registry package (audit F-23):
this module owns the ``campaigns`` table — create / list / lookup,
ck3_chronicler-cqo identity denormalisation
(:func:`update_campaign_overview`), and the ck3_chronicler-z7l
closing-chronicle persistence (:func:`set_campaign_closing_chronicle`).

The ``Campaign`` dataclass and all CRUD helpers re-export from
``chronicler.db.registry`` for backwards compatibility — callers
should keep importing from the top-level package.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import MISSING, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chronicler.db.registry._core import (
    _connect,
    _ensure_columns,
    campaign_db_path,
)

_CREATE_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ck3_playthrough_id TEXT,
    ck3_version TEXT,
    created_at TEXT NOT NULL,
    last_event_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    db_path TEXT NOT NULL,
    founding_dynasty_name TEXT,
    tail_offset INTEGER NOT NULL DEFAULT 0,
    closing_chronicle TEXT,
    closing_chronicle_generated_at TEXT,
    closing_chronicle_input_tokens INTEGER,
    closing_chronicle_output_tokens INTEGER,
    bookmark_date TEXT,
    current_in_game_date TEXT,
    current_player_character_id INTEGER,
    current_player_name TEXT,
    current_player_nickname TEXT,
    current_house_name TEXT,
    current_player_gold REAL,
    current_player_prestige_lifetime REAL,
    current_player_piety REAL,
    current_dynasty_renown REAL,
    auto_track_rules TEXT,
    last_event_in_game_date TEXT
)
"""

# ck3_chronicler-z7l: lazy migration for the v0.7 closing-ceremony.
# Pre-existing registry DBs were created without these columns. Same
# rationale as tracked-character columns (registry isn't Alembic-managed).
_CAMPAIGNS_REQUIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("closing_chronicle", "TEXT"),
    ("closing_chronicle_generated_at", "TEXT"),
    # ck3_chronicler-li0z: token spend of the closing chronicle. It lives in
    # the registry (not a Biography row), so cost-summary folds these in
    # explicitly; without them the meter under-reports the most expensive call.
    ("closing_chronicle_input_tokens", "INTEGER"),
    ("closing_chronicle_output_tokens", "INTEGER"),
    # ck3_chronicler-cs1o: closing-chronicle attribution + cache/cost
    # breakdown, mirroring the biographies columns.
    ("closing_chronicle_provider", "TEXT"),
    ("closing_chronicle_cache_read_tokens", "INTEGER"),
    ("closing_chronicle_cache_write_tokens", "INTEGER"),
    ("closing_chronicle_cost_usd", "REAL"),
    # ck3_chronicler-cqo: identity / state denormalised onto the registry
    # row so the Library page is one query per card and so completed
    # campaigns retain a frozen byline after archive.
    ("bookmark_date", "TEXT"),
    ("current_in_game_date", "TEXT"),
    ("current_player_character_id", "INTEGER"),
    ("current_player_name", "TEXT"),
    ("current_player_nickname", "TEXT"),
    ("current_house_name", "TEXT"),
    # ck3_chronicler-wdhe: player stats surfaced on the campaign-overview
    # welcome page. Prestige and piety are lifetime accrued counters (not
    # current balances); gold is the current balance at last save tick.
    # Renown is a dynasty-level stat (none in adventurer mode).
    # Column is named current_player_piety but stores the lifetime value
    # (5r5t fix — avoids a migration; semantics visible in the API field
    # current_player_piety_lifetime).
    ("current_player_gold", "REAL"),
    ("current_player_prestige_lifetime", "REAL"),
    ("current_player_piety", "REAL"),
    ("current_dynasty_renown", "REAL"),
    # ck3_chronicler-gw16: per-campaign auto-track rule flags. JSON blob
    # with include_heirs / include_spouses / include_county_vassals
    # bools. NULL = "use legacy default" (everything-on, matches the
    # behavior shipped before the rules existed). The TrackSideRail
    # checkboxes write here; auto_track_candidates reads it via the
    # endpoint wrapper.
    ("auto_track_rules", "TEXT"),
    # ck3_chronicler-bges: persisted last-tick info so the Library card +
    # IngestActivityStrip have something to show on cold load (before
    # the next live save_pair_completed SSE frame arrives). Five nullable
    # columns; populated atomically by set_campaign_last_tick at the end
    # of each ingest tick.
    ("last_save_filename", "TEXT"),
    ("last_save_ingested_at", "TEXT"),
    ("last_save_in_game_date", "TEXT"),
    ("last_tick_event_count", "INTEGER"),
    ("last_tick_event_type_tally", "TEXT"),
    # ck3_chronicler-9xa6: in-game date of the most recent ingested event,
    # written by touch_last_event_at alongside the wall-clock column when
    # the caller has a snap.current_date in hand. The Closing page uses
    # this to compute Span (1066 → 1126 = 60 years) instead of the
    # wall-clock value that produced "Span 960 years" in the 2026-05-10
    # smoke. Pre-9xa6 rows stay NULL until backfill_last_event_in_game_date
    # runs at startup.
    ("last_event_in_game_date", "TEXT"),
)


def _setup(conn: sqlite3.Connection) -> None:
    """Schema setup for the campaigns table. Called by :func:`_core._connect`
    once per registry path per process; F-49 hardens the cross-process case."""
    conn.execute(_CREATE_CAMPAIGNS)
    _ensure_columns(conn, "campaigns", _CAMPAIGNS_REQUIRED_COLUMNS)


@dataclass(frozen=True)
class Campaign:
    id: str
    name: str
    ck3_playthrough_id: str | None
    ck3_version: str | None
    created_at: str
    last_event_at: str | None
    archived: bool
    db_path: str
    founding_dynasty_name: str | None
    tail_offset: int
    closing_chronicle: str | None = None
    closing_chronicle_generated_at: str | None = None
    # ck3_chronicler-li0z: closing-chronicle token spend (it's not a Biography
    # row, so cost-summary reads these to avoid under-reporting). None until a
    # closing chronicle is generated.
    closing_chronicle_input_tokens: int | None = None
    closing_chronicle_output_tokens: int | None = None
    # ck3_chronicler-cs1o: closing-chronicle attribution + cache/cost
    # breakdown, mirroring the biographies columns. The provider tag
    # was never persisted before — the chronicle's spend bucketed under
    # an assumed provider in cost aggregation.
    closing_chronicle_provider: str | None = None
    closing_chronicle_cache_read_tokens: int | None = None
    closing_chronicle_cache_write_tokens: int | None = None
    closing_chronicle_cost_usd: float | None = None
    # ck3_chronicler-cqo: per-campaign identity + state, written by save-tail.
    bookmark_date: str | None = None
    current_in_game_date: str | None = None
    current_player_character_id: int | None = None
    current_player_name: str | None = None
    current_player_nickname: str | None = None
    current_house_name: str | None = None
    # ck3_chronicler-wdhe: campaign-overview stats. None when the
    # registry row is fresh / pre-migration / no save-tail tick has run
    # since the columns were added — the FE renders "—" for any null.
    current_player_gold: float | None = None
    current_player_prestige_lifetime: float | None = None
    current_player_piety: float | None = None
    current_dynasty_renown: float | None = None
    # ck3_chronicler-gw16: per-campaign auto-track rules (JSON blob).
    # None = "use legacy default" (all categories on); a JSON string
    # like '{"include_heirs": true, "include_spouses": false}' overrides.
    auto_track_rules: str | None = None
    # ck3_chronicler-bges: persisted last-tick info — null only when the
    # campaign has never been ingested. A zero-event tick still populates
    # all five fields so the strip can show "0 events" as a heartbeat.
    last_save_filename: str | None = None
    last_save_ingested_at: str | None = None
    last_save_in_game_date: str | None = None
    last_tick_event_count: int | None = None
    last_tick_event_type_tally: str | None = None  # JSON-encoded dict
    # ck3_chronicler-9xa6: in-game date string of the most recent event
    # ingested for this campaign (e.g. "1126.5.18"). Used by the Closing
    # page to compute Span without the wall-clock confusion. NULL until
    # the first post-9xa6 save-tail tick (or the startup backfill from
    # max(event_date_iso) over the per-campaign events table) populates it.
    last_event_in_game_date: str | None = None


def _row_to_campaign(row: sqlite3.Row) -> Campaign:
    """Build a :class:`Campaign` from a registry row, derived from the dataclass
    fields (ck3_chronicler-27ov.49 / J4).

    Adding a column to the schema used to mean editing four hand-spelled places;
    deriving the mapper here means the dataclass is the single source of truth
    for the row→object mapping. A column absent from the row — a legacy DB read
    before lazy migration ran — falls back to the field's dataclass default
    (the old per-field ``"x" in keys else None`` branches, now one loop).
    ``archived`` is coerced from SQLite's integer to bool.
    """
    keys = set(row.keys())
    values: dict[str, Any] = {}
    for f in fields(Campaign):
        if f.name in keys:
            values[f.name] = row[f.name]
        elif f.default is not MISSING:
            values[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            values[f.name] = f.default_factory()
        else:
            # A non-defaulted (required) column genuinely absent — surface it,
            # matching the old mapper's direct row[...] access.
            values[f.name] = row[f.name]
    values["archived"] = bool(values["archived"])
    return Campaign(**values)


def create_campaign(
    name: str,
    *,
    db_path: str | None = None,
    ck3_version: str | None = None,
    founding_dynasty_name: str | None = None,
    ck3_playthrough_id: str | None = None,
    registry: Path | None = None,
) -> Campaign:
    campaign_id = str(uuid.uuid4())
    resolved_db_path = db_path or str(campaign_db_path(campaign_id))
    created_at = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        conn.execute(
            """
            INSERT INTO campaigns (
                id, name, ck3_version, created_at, db_path,
                founding_dynasty_name, ck3_playthrough_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                name,
                ck3_version,
                created_at,
                resolved_db_path,
                founding_dynasty_name,
                ck3_playthrough_id,
            ),
        )
    return get_campaign_by_id(campaign_id, registry=registry)  # type: ignore[return-value]


def list_campaigns(
    *, include_archived: bool = False, registry: Path | None = None
) -> list[Campaign]:
    """Pure SELECT over the campaigns table.

    ck3_chronicler-27ov.15 (audit H12 + M-B3): this used to run
    ``bootstrap_archived_snapshots`` (sidecar import + the wvrm prune
    pass, which delete_campaign()s and unlinks files) on every
    ``include_archived=True`` call — a read endpoint that deletes files,
    inherited by every caller (Library page, migrate detector, doctor),
    with a db→sync dependency cycle papered over by lazy imports.
    Bootstrap+prune now runs explicitly: API lifespan startup
    (``api.app._run_backfill``) and the ``chronicler campaign list
    --all`` CLI command. Callers that need fresher pickup of
    newly-pulled archived snapshots call
    ``chronicler.sync.bootstrap_archived_snapshots`` themselves.
    """
    with _connect(registry) as conn:
        if include_archived:
            cur = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC")
        else:
            cur = conn.execute(
                "SELECT * FROM campaigns WHERE archived = 0 ORDER BY created_at DESC"
            )
        return [_row_to_campaign(r) for r in cur.fetchall()]


def get_campaign_by_id(campaign_id: str, *, registry: Path | None = None) -> Campaign | None:
    with _connect(registry) as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return _row_to_campaign(row) if row is not None else None


def get_campaign_by_name(
    name: str,
    *,
    include_archived: bool = False,
    registry: Path | None = None,
) -> Campaign | None:
    """Look up a campaign by name. By default archived campaigns are
    excluded (sealed campaigns are terminal — see resolve_campaign_for_save).
    Pass ``include_archived=True`` to find a row regardless of its archive
    state — the un-archive flow (ck3_chronicler-w2s) is the canonical caller."""
    with _connect(registry) as conn:
        if include_archived:
            row = conn.execute("SELECT * FROM campaigns WHERE name = ?", (name,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE name = ? AND archived = 0", (name,)
            ).fetchone()
        return _row_to_campaign(row) if row is not None else None


def set_tail_offset(campaign_id: str, offset: int, *, registry: Path | None = None) -> None:
    with _connect(registry) as conn:
        conn.execute("UPDATE campaigns SET tail_offset = ? WHERE id = ?", (offset, campaign_id))


def get_tail_offset(campaign_id: str, *, registry: Path | None = None) -> int:
    with _connect(registry) as conn:
        row = conn.execute(
            "SELECT tail_offset FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        return int(row["tail_offset"]) if row is not None else 0


def touch_last_event_at(
    campaign_id: str,
    *,
    in_game_date: str | None = None,
    registry: Path | None = None,
) -> None:
    """Bump ``last_event_at`` (wall-clock) and optionally ``last_event_in_game_date``.

    ck3_chronicler-9xa6: the wall-clock column is the "last ingested"
    indicator (Library card timestamp); the in-game column is the source
    of truth for the Closing page's "Closed" date and Span computation.
    Callers that have a snap in hand (save-tail's _apply_campaign_overview_*)
    pass ``in_game_date=snap.current_date`` to populate both atomically.
    Callers without a snap (the pure debug-log tailer) omit the kwarg —
    only wall-clock is touched and a prior in-game value is preserved.
    """
    ts = datetime.now(UTC).isoformat()
    with _connect(registry) as conn:
        if in_game_date is None:
            conn.execute(
                "UPDATE campaigns SET last_event_at = ? WHERE id = ?",
                (ts, campaign_id),
            )
        else:
            conn.execute(
                "UPDATE campaigns SET last_event_at = ?, last_event_in_game_date = ? WHERE id = ?",
                (ts, in_game_date, campaign_id),
            )


# ck3_chronicler-cqo: sentinel that distinguishes "caller did not pass
# this kwarg" from "caller explicitly passed None to write SQL NULL".
# Internal to update_campaign_overview — never expose to other modules.
class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


_UNSET: Any = _Unset()


def update_campaign_overview(
    campaign_id: str,
    *,
    ck3_playthrough_id: Any = _UNSET,
    bookmark_date: Any = _UNSET,
    current_in_game_date: Any = _UNSET,
    current_player_character_id: Any = _UNSET,
    current_player_name: Any = _UNSET,
    current_player_nickname: Any = _UNSET,
    current_house_name: Any = _UNSET,
    current_player_gold: Any = _UNSET,
    current_player_prestige_lifetime: Any = _UNSET,
    current_player_piety: Any = _UNSET,
    current_dynasty_renown: Any = _UNSET,
    registry: Path | None = None,
) -> None:
    """ck3_chronicler-cqo: denormalise per-campaign identity onto the
    registry row so the Library page renders one query per card and
    so archived campaigns retain a frozen byline.

    Each identity kwarg defaults to the internal ``_UNSET`` sentinel.
    Three semantics:

    - **Omit kwarg (or pass ``_UNSET``)**: column is not touched.
    - **Pass ``None``**: column is written to SQL NULL. This is what
      lets save-tail clear a previously-populated nickname when the
      player succeeds to an heir without one.
    - **Pass a value**: column is written to that value.

    The earlier none-skip implementation conflated "omit" and "None"
    which silently leaked stale identity data through heir succession
    — fixed here so the call site in ``_advance_baseline`` can pass
    all six identity fields unconditionally and have None mean
    'clear this column'."""
    fields = {
        "ck3_playthrough_id": ck3_playthrough_id,
        "bookmark_date": bookmark_date,
        "current_in_game_date": current_in_game_date,
        "current_player_character_id": current_player_character_id,
        "current_player_name": current_player_name,
        "current_player_nickname": current_player_nickname,
        "current_house_name": current_house_name,
        "current_player_gold": current_player_gold,
        "current_player_prestige_lifetime": current_player_prestige_lifetime,
        "current_player_piety": current_player_piety,
        "current_dynasty_renown": current_dynasty_renown,
    }
    provided = {k: v for k, v in fields.items() if v is not _UNSET}
    if not provided:
        return
    set_clause = ", ".join(f"{col} = ?" for col in provided)
    values = list(provided.values()) + [campaign_id]
    with _connect(registry) as conn:
        conn.execute(
            f"UPDATE campaigns SET {set_clause} WHERE id = ?",
            values,
        )


def set_auto_track_rules(
    campaign_id: str,
    rules_json: str | None,
    *,
    registry: Path | None = None,
) -> None:
    """ck3_chronicler-gw16: write the auto-track rules JSON blob.

    Pass ``None`` to clear the override (revert to legacy default).
    Caller is responsible for JSON-encoding the rules dict — kept as
    a free-form string here so future rule additions don't require
    a schema migration.
    """
    with _connect(registry) as conn:
        conn.execute(
            "UPDATE campaigns SET auto_track_rules = ? WHERE id = ?",
            (rules_json, campaign_id),
        )


def set_campaign_last_tick(
    campaign_id: str,
    *,
    save_filename: str,
    ingested_at: str,
    in_game_date: str | None,
    event_count: int,
    event_type_tally: dict[str, int],
    registry: Path | None = None,
) -> None:
    """ck3_chronicler-bges: write the last save-pair tick info, called
    from `_advance_baseline` (production save-tail tick). Tally is
    JSON-encoded here (the sibling `auto_track_rules` setter relies on
    the caller to encode; this one self-encodes because the caller
    always has a dict)."""
    tally_json = json.dumps(event_type_tally, sort_keys=True)
    with _connect(registry) as conn:
        conn.execute(
            """
            UPDATE campaigns
            SET last_save_filename = ?,
                last_save_ingested_at = ?,
                last_save_in_game_date = ?,
                last_tick_event_count = ?,
                last_tick_event_type_tally = ?
            WHERE id = ?
            """,
            (
                save_filename,
                ingested_at,
                in_game_date,
                event_count,
                tally_json,
                campaign_id,
            ),
        )


def rename_campaign(campaign_id: str, new_name: str, *, registry: Path | None = None) -> None:
    """ck3_chronicler-bly: change a campaign's display name.

    Names are intentionally not unique-constrained — cqo's auto-detect
    and resolve_campaign_for_save both work off ``ck3_playthrough_id``,
    so name collisions are cosmetic, not load-bearing. The API layer
    surfaces a soft warning on collision instead of rejecting.

    Closing-chronicle frozen narrative is unaffected: rename only
    updates the registry display name, not anything embedded in the
    chronicle body. Document this on the rename UI."""
    with _connect(registry) as conn:
        conn.execute("UPDATE campaigns SET name = ? WHERE id = ?", (new_name, campaign_id))


def archive_campaign(campaign_id: str, *, registry: Path | None = None) -> None:
    with _connect(registry) as conn:
        conn.execute("UPDATE campaigns SET archived = 1 WHERE id = ?", (campaign_id,))


def delete_campaign(campaign_id: str, *, registry: Path | None = None) -> bool:
    """ck3_chronicler-ezpc: hard-delete a campaign row + its dependent
    registry rows (tracked_characters, suppressed_event_kinds).

    Returns True when a row was removed. Does NOT touch the per-campaign
    SQLite file or the archive snapshot — file unlinking is the
    endpoint's responsibility (the registry layer doesn't know the data
    dir layout). Idempotent: a second call with the same id returns False.

    There is no FK cascade to lean on: the child tables dropped their
    ON DELETE CASCADE clauses and ``_connect`` leaves ``foreign_keys`` OFF
    (audit L18 / ck3_chronicler-27ov.79), so this explicit cleanup is the
    sole mechanism that keeps tracked_characters / suppressed_event_kinds
    from outliving their campaign.
    """
    with _connect(registry) as conn:
        conn.execute(
            "DELETE FROM tracked_characters WHERE campaign_id = ?",
            (campaign_id,),
        )
        conn.execute(
            "DELETE FROM suppressed_event_kinds WHERE campaign_id = ?",
            (campaign_id,),
        )
        cur = conn.execute(
            "DELETE FROM campaigns WHERE id = ?",
            (campaign_id,),
        )
        return cur.rowcount > 0


def unarchive_campaign(campaign_id: str, *, registry: Path | None = None) -> None:
    """ck3_chronicler-w2s: inverse of archive_campaign. Returns a sealed
    campaign to the active list. The closing chronicle (if any) stays on
    the row — it is frozen narrative; if the user ingests further events
    after un-archiving, the in-card blurb may diverge from the actual
    in-game state. The frontend rename/un-archive surface (filed for v0.9)
    surfaces that warning to the user."""
    with _connect(registry) as conn:
        conn.execute("UPDATE campaigns SET archived = 0 WHERE id = ?", (campaign_id,))


def _auto_name_for_save(
    *,
    player_first_name: str | None,
    dynasty_name: str | None,
    bookmark_date: str | None,
) -> str:
    """ck3_chronicler-cqo / ck3_chronicler 2026-05-09: format the
    auto-name for a campaign created from a save without a
    user-supplied name. Format: '<dynasty> <bookmark_date>' (preferred)
    with bookmark_date '.' replaced by '-' (CK3-native '1066.9.15' →
    'Munso 1066-9-15'). Falls back to the player's first name when no
    dynasty is known (e.g. adventurer mode), and to 'Unknown player'
    if both are missing — matches CK3's own date formatting + the
    user's 2026-05-09 preference (default to dynasty, not first
    character, since the first character changes via succession but
    the dynasty is the chronicled spine).

    Both halves fall back symmetrically when the input is None,
    empty, or whitespace-only — caught at strip-then-or."""
    dynasty_clean = (dynasty_name or "").strip()
    player_clean = (player_first_name or "").strip()
    name_part = dynasty_clean or player_clean or "Unknown player"
    date_clean = (bookmark_date or "").strip()
    date_part = date_clean.replace(".", "-") or "unknown date"
    return f"{name_part} {date_part}"


def _create_from_snap(
    snap,
    *,
    ck3_version: str | None,
    registry: Path | None,
) -> Campaign:
    """ck3_chronicler-cqo: shared create path used by
    resolve_campaign_for_save when a save has no matching campaign."""
    name = _auto_name_for_save(
        player_first_name=getattr(snap, "founding_player_first_name", None),
        dynasty_name=getattr(snap, "founding_dynasty_name", None),
        bookmark_date=getattr(snap, "bookmark_date", None),
    )
    return create_campaign(
        name,
        ck3_version=ck3_version,
        ck3_playthrough_id=getattr(snap, "playthrough_id", None) or None,
        founding_dynasty_name=getattr(snap, "founding_dynasty_name", None),
        registry=registry,
    )


class ArchivedCampaignConflict(Exception):
    """ck3_chronicler-obds: raised by :func:`resolve_campaign_for_save`
    when the only campaigns matching a save's ``playthrough_id`` are
    archived.

    Refusing to silently fork is the policy. Archived campaigns are
    sealed-and-terminal in the standard flow, but a save still hitting
    that playthrough_id means the user has either resumed playing past
    the seal (likely accidental archive) or restored a snapshot. Either
    way, silently creating a new campaign with the same playthrough
    destroys the user's data visibility — the new campaign starts empty
    and the old campaign's events stop accumulating.

    Callers surface the decision: the CLI exits with a help message
    pointing at the un-archive endpoint; the API returns 409 with the
    archived campaign's id + name so the SPA can offer 'un-archive and
    resume' as a one-click action.
    """

    def __init__(self, archived_campaign: Campaign) -> None:
        self.archived_campaign = archived_campaign
        super().__init__(
            f"playthrough_id {archived_campaign.ck3_playthrough_id!r} "
            f"matches archived campaign {archived_campaign.name!r} "
            f"(id={archived_campaign.id}); un-archive it to resume, or "
            f"explicitly start a fresh playthrough with --campaign <new-name>"
        )


def find_active_campaign_for_playthrough(
    playthrough_id: str | None, *, registry: Path | None = None
) -> Campaign | None:
    """ck3_chronicler-3v0s: non-mutating lookup of an active campaign by
    ``playthrough_id``. Returns the most recently active match, or None.

    Mirrors the active-row half of :func:`resolve_campaign_for_save` but
    never creates a new row. Used by save-tail's auto-resume path
    (foreign playthrough whose id matches a known-active campaign →
    spawn a concurrent ingest for it). Archived matches and missing
    matches both return None — the caller decides whether to fall back
    to silent-drop or surface an un-archive prompt.
    """
    if not playthrough_id:
        return None
    with _connect(registry) as conn:
        row = conn.execute(
            """
            SELECT * FROM campaigns
             WHERE ck3_playthrough_id = ? AND archived = 0
             ORDER BY COALESCE(last_event_at, '') DESC, created_at DESC
             LIMIT 1
            """,
            (playthrough_id,),
        ).fetchone()
        return _row_to_campaign(row) if row is not None else None


def find_archived_match_for_playthrough(
    playthrough_id: str | None, *, registry: Path | None = None
) -> Campaign | None:
    """ck3_chronicler-obds: return the most recent archived campaign
    matching ``playthrough_id``, or None.

    Active matches are ignored — :func:`resolve_campaign_for_save`
    handles those on the active path. This helper exists so callers can
    distinguish 'no campaign at all yet, fresh fork is correct' from
    'an archived campaign matches, refuse to silently fork'.
    """
    if not playthrough_id:
        return None
    with _connect(registry) as conn:
        row = conn.execute(
            """
            SELECT * FROM campaigns
             WHERE ck3_playthrough_id = ? AND archived = 1
             ORDER BY COALESCE(last_event_at, '') DESC, created_at DESC
             LIMIT 1
            """,
            (playthrough_id,),
        ).fetchone()
        return _row_to_campaign(row) if row is not None else None


def resolve_campaign_for_save(
    snap,
    *,
    ck3_version: str | None = None,
    registry: Path | None = None,
) -> Campaign:
    """ck3_chronicler-cqo: match a parsed save's playthrough_id to an
    existing *active* campaign, or create a new one named for the player.

    ``snap`` is duck-typed to expose four attributes:
    - ``playthrough_id`` (str): required for the match query.
    - ``bookmark_date`` (str | None): used in the auto-name format.
    - ``founding_player_first_name`` (str | None): the snap's player
      character's first_name, resolved by the caller from the
      per-campaign characters table at create time. None falls back to
      'Unknown player' in the auto-name.
    - ``founding_dynasty_name`` (str | None): same resolution rule;
      None means we don't know yet.

    **Archived campaigns are intentionally excluded from the active
    match** — the "Sealed" closing-ceremony state is terminal in the
    normal flow. But ck3_chronicler-obds: when the *only* match is
    archived we now raise :class:`ArchivedCampaignConflict` rather than
    silently forking. Silent-fork would erase the user's data
    visibility (events keep landing on a fresh empty campaign while
    the populated archived one stops updating). The caller decides
    whether to un-archive the original (preferred) or genuinely start a
    fresh playthrough under a new explicit name.

    The defensive 'two campaigns share a playthrough_id' tiebreak picks
    the active one with the highest ``last_event_at``. Shouldn't
    happen because pinning enforces uniqueness on the per-campaign DB
    side, but registry can drift from per-campaign DBs in rare cases
    (manual db_path edits, partially-migrated installs).

    The newly-created campaign has ``ck3_playthrough_id`` populated up
    front so the next save-tail tick / next call to this function
    finds it."""
    pt = getattr(snap, "playthrough_id", None)
    if not pt:
        # Empty playthrough_id — older save formats or partial parses.
        # Refuse to match (matching against NULL would alias every
        # mystery save together) and create a campaign with the
        # auto-name path. Recoverable via --campaign foo.
        return _create_from_snap(snap, ck3_version=ck3_version, registry=registry)

    with _connect(registry) as conn:
        rows = conn.execute(
            """
            SELECT * FROM campaigns
             WHERE ck3_playthrough_id = ? AND archived = 0
             ORDER BY COALESCE(last_event_at, '') DESC, created_at DESC
            """,
            (pt,),
        ).fetchall()
    if rows:
        return _row_to_campaign(rows[0])
    archived_match = find_archived_match_for_playthrough(pt, registry=registry)
    if archived_match is not None:
        raise ArchivedCampaignConflict(archived_match)
    return _create_from_snap(snap, ck3_version=ck3_version, registry=registry)


def set_campaign_closing_chronicle(
    campaign_id: str,
    body: str,
    *,
    generated_at: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider: str | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cost_usd: float | None = None,
    registry: Path | None = None,
) -> None:
    """Persist a campaign's closing chronicle (ck3_chronicler-z7l).

    Overwrites any prior chronicle on the campaign. The complete-endpoint
    flow archives the campaign separately via :func:`archive_campaign`
    once the chronicle is safely written.

    ck3_chronicler-li0z: ``input_tokens`` / ``output_tokens`` record the
    closing chronicle's LLM spend so cost-summary can include it (the
    chronicle isn't a Biography row, so the aggregate would otherwise miss it).

    ck3_chronicler-cs1o: ``provider`` (kind-resolved transport tag) +
    cache breakdown + ``cost_usd`` mirror the biographies columns so
    per-provider USD attribution covers the chronicle too.
    """
    with _connect(registry) as conn:
        conn.execute(
            """
            UPDATE campaigns
            SET closing_chronicle = ?,
                closing_chronicle_generated_at = ?,
                closing_chronicle_input_tokens = ?,
                closing_chronicle_output_tokens = ?,
                closing_chronicle_provider = ?,
                closing_chronicle_cache_read_tokens = ?,
                closing_chronicle_cache_write_tokens = ?,
                closing_chronicle_cost_usd = ?
            WHERE id = ?
            """,
            (
                body,
                generated_at,
                input_tokens,
                output_tokens,
                provider,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                campaign_id,
            ),
        )

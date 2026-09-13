"""Save-import orchestrator with progress hooks (ck3_chronicler-u3m).

Factored out of the original ``cmd_import_save`` so the import flow can
be driven from either the CLI or the HTTP layer (POST
/api/campaigns/{name}/import-save). The HTTP path runs the import in a
background task and publishes :class:`ImportProgress` events to the
shared EventBus so the SSE endpoint can stream them to the browser.

Five-stage progress contract:

==============================  =========
Stage                           Fraction
------------------------------  ---------
``read_save``                   ``0.0``
``parse_history``               ``0.25``
``backfill_events``             ``0.5``
``generate_biographies``        ``0.75``
``done``                        ``1.0``
==============================  =========

``error`` is the catch-all stage emitted on any exception with
``fraction=1.0`` so the UI can show a final-failed state alongside
``done``-on-success.

Biography generation (``generate_biographies`` stage) is currently a
no-op placeholder — the original cmd_import_save didn't generate
biographies inline. The progress hook still fires so the v0.7 import
modal's stage-by-stage UI doesn't have to change when the future
auto-biography pass lands.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import sessionmaker

from chronicler.db.engine import session_scope
from chronicler.db.repository import (
    PlaythroughMismatchError,
    assert_playthrough_or_pin,
    insert_event_idempotent,
    upsert_character,
)
from chronicler.save.character_columns import hydrate_character_columns
from chronicler.save.overview import apply_campaign_overview_from_snap
from chronicler.save.parse import parse_save
from chronicler.save.rakaly import RakalyError, convert_save_to_json
from chronicler.schema import VanillaMemoryEvent, VanillaMemoryPayload
from chronicler.util.dates import parse_ck3_date

log = logging.getLogger(__name__)


ImportStage = Literal[
    "read_save",
    "parse_history",
    "backfill_events",
    "generate_biographies",
    "done",
    "error",
]


@dataclass(frozen=True, slots=True)
class ImportProgress:
    stage: ImportStage
    fraction: float  # 0.0 .. 1.0
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    success: bool
    chars_upserted: int
    memories_inserted: int
    memories_duplicate: int
    error: str | None = None


_STAGE_FRACTIONS: dict[ImportStage, float] = {
    "read_save": 0.0,
    "parse_history": 0.25,
    "backfill_events": 0.5,
    "generate_biographies": 0.75,
    "done": 1.0,
    "error": 1.0,
}


def _emit(
    callback: Callable[[ImportProgress], None] | None,
    stage: ImportStage,
    message: str,
) -> None:
    if callback is None:
        return
    try:
        callback(ImportProgress(stage=stage, fraction=_STAGE_FRACTIONS[stage], message=message))
    except Exception:
        # Progress callbacks are observability — never break the import.
        log.exception("import progress callback failed at stage=%s", stage)


def import_save(
    save_path: Path,
    *,
    factory: sessionmaker,
    campaign_id: str | None = None,
    registry_path: Path | None = None,
    progress: Callable[[ImportProgress], None] | None = None,
    force_reset_playthrough: bool = False,
) -> ImportResult:
    """Run the full backfill pipeline for a single .ck3 save.

    Equivalent to the original ``cmd_import_save`` minus the typer
    plumbing. Idempotent — re-running with the same save produces no
    new event rows (vanilla_memory unique constraint).

    Returns :class:`ImportResult`. On error returns success=False with
    the message in ``error`` and emits a final ``error`` progress
    event — the caller is responsible for surfacing it.
    """
    if not save_path.is_file():
        msg = f"save file not found: {save_path}"
        _emit(progress, "error", msg)
        return ImportResult(False, 0, 0, 0, error=msg)

    _emit(progress, "read_save", f"reading {save_path.name}")
    try:
        raw_save_data = convert_save_to_json(save_path)
    except (RakalyError, FileNotFoundError) as e:
        msg = f"rakaly failed: {e}"
        _emit(progress, "error", msg)
        return ImportResult(False, 0, 0, 0, error=msg)

    _emit(progress, "parse_history", "parsing snapshot")
    try:
        snap = parse_save(raw_save_data)
    except Exception as e:
        msg = f"parse_save failed: {type(e).__name__}: {e}"
        log.exception("import parse_save failed")
        _emit(progress, "error", msg)
        return ImportResult(False, 0, 0, 0, error=msg)

    chars_upserted = 0
    memories_inserted = 0
    memories_duplicate = 0

    # Pin / verify playthrough before touching event rows.
    with session_scope(factory) as session:
        try:
            assert_playthrough_or_pin(
                session, observed=snap.playthrough_id, allow_reset=force_reset_playthrough
            )
        except PlaythroughMismatchError as e:
            msg = (
                f"playthrough mismatch: {e}. Re-run with force_reset_playthrough=True "
                "if you intend to switch this campaign DB to the new playthrough."
            )
            _emit(progress, "error", msg)
            return ImportResult(False, 0, 0, 0, error=msg)

    _emit(
        progress,
        "backfill_events",
        f"upserting {len(snap.characters)} characters and their vanilla memories",
    )

    name_lookup: dict[int, str] = {
        cid: char.first_name for cid, char in snap.characters.items() if char.first_name
    }

    # Pass 1: upsert characters.
    # ck3_chronicler-vbfa: column derivation shared with save-tail's
    # _refresh_tracked_characters via save/character_columns.py.
    with session_scope(factory) as session:
        for cid, char in snap.characters.items():
            columns = hydrate_character_columns(
                snap=snap,
                char=char,
                cid=cid,
                raw_save_data=raw_save_data,
                name_lookup=name_lookup,
            )
            upsert_character(session, **columns)
            chars_upserted += 1

    # Pass 2: vanilla memories.
    with session_scope(factory) as session:
        for cid, char in snap.characters.items():
            for mem in char.memories:
                for _role, pid in mem.participants:
                    upsert_character(session, ck3_id=pid)
                pydantic_event = VanillaMemoryEvent(
                    d=mem.creation_date,
                    c=cid,
                    p=VanillaMemoryPayload(
                        memory_type=mem.memory_type,
                        end_date=mem.end_date,
                        participants=dict(mem.participants),
                    ),
                )
                payload_json = json.dumps(
                    pydantic_event.model_dump(exclude_none=True),
                    separators=(",", ":"),
                    sort_keys=True,
                )
                eid = insert_event_idempotent(
                    session,
                    schema_version=1,
                    event_type="vanilla_memory",
                    event_date=mem.creation_date,
                    event_date_iso=parse_ck3_date(mem.creation_date),
                    wall_clock_at=datetime.now(UTC).isoformat(),
                    primary_character_id=cid,
                    payload_json=payload_json,
                    raw_line=f"import-save: {save_path.name}",
                    participants=[(pid, role) for role, pid in mem.participants],
                )
                if eid is None:
                    memories_duplicate += 1
                else:
                    memories_inserted += 1

    # Biography generation is a placeholder for now — the legacy import
    # path did not generate biographies inline. The stage emission keeps
    # the SSE contract stable so the UI can subscribe without retrofit.
    _emit(
        progress,
        "generate_biographies",
        "skipping inline biography generation (deferred to scheduler)",
    )

    # ck3_chronicler-7ao: snapshot per-campaign identity onto the
    # registry row. save-tail does this on every tick; the importer
    # does it once at the end of the import so a fresh campaign that
    # has never seen save-tail (Library card was empty) immediately
    # picks up its real player + house + bookmark + CoA byline.
    if campaign_id is not None:
        apply_campaign_overview_from_snap(campaign_id, snap, registry=registry_path)

    _emit(
        progress,
        "done",
        f"upserted {chars_upserted} characters; "
        f"{memories_inserted} new vanilla memories ({memories_duplicate} duplicate)",
    )
    return ImportResult(
        success=True,
        chars_upserted=chars_upserted,
        memories_inserted=memories_inserted,
        memories_duplicate=memories_duplicate,
    )

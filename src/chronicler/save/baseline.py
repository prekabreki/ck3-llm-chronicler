"""Persistent SaveSnapshot baseline for save-tail (ck3_chronicler-96m).

The save-tail loop holds its diff baseline (the last SaveSnapshot it
ingested events against) only in memory. Stop the loop, play a different
campaign for hours, restart against the original campaign — the loop
silently rebaselines on the now-much-later autosave and loses every
state-diff event between runs (travel, marriage, divorce). Vanilla
memory events survive via import-save's idempotent backfill, but
state-diff events do not.

This module persists the SaveSnapshot to ``<campaign_db>.baseline.json``
after each ingested save. On the next save-tail startup, the loop loads
the persisted baseline and the first new diff catches up across the gap.

Storage shape: plain JSON (~1-5 MB for a 40k-character world bookmark,
verified manually). One file per campaign, lives next to the campaign
DB so archival/cleanup is one filesystem operation.

Serialization (ck3_chronicler-27ov.10 / audit H1/J2): the SaveSnapshot ⇄
JSON mapping is handled generically by
:mod:`chronicler.save.dataclass_codec` — there are no hand-written
per-field encoders/decoders here. Adding a field to SaveSnapshot (or any
nested snapshot dataclass) round-trips automatically, retiring the
recurring "field added without a serde override is silently dropped /
crashes json.dump" bug class (qug, zm0q, g56m, kig9, audit H2).

Layering: this module owns the on-disk format. Callers (``ingest.py``)
own the policy of *when* to persist and *whether* to trust a persisted
baseline (the playthrough_id match check lives there).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from chronicler.save.dataclass_codec import from_jsonable, to_jsonable
from chronicler.save.parse import SaveSnapshot

log = logging.getLogger(__name__)

# Format version for the on-disk baseline. v2 (ck3_chronicler-elll) adds
# generation + persisted_at forensic fields next to format_version. v1
# files load successfully (forensics fields default to None); the next
# persist upgrades them to v2 starting at generation=1.
_BASELINE_FORMAT_VERSION = 2


@dataclass(frozen=True)
class BaselineLoad:
    """ck3_chronicler-elll: snapshot + forensic fields returned by
    :func:`load_baseline`.

    ``generation`` and ``persisted_at`` are ``None`` for legacy v1 files
    that predate the forensic-fields commit. The next :func:`save_baseline`
    call upgrades the on-disk file to v2 starting at ``generation=1``.
    """

    snapshot: SaveSnapshot
    generation: int | None
    persisted_at: str | None


def baseline_path_for(db_path: Path) -> Path:
    """Derive the baseline path for a campaign DB.

    ``/x/eadmund.db`` → ``/x/eadmund.baseline.json``. A path with no
    suffix (rare) gets ``.baseline.json`` appended.
    """
    if db_path.suffix:
        return db_path.with_suffix(".baseline.json")
    return db_path.parent / f"{db_path.name}.baseline.json"


def save_baseline(path: Path, snapshot: SaveSnapshot) -> None:
    """Persist a SaveSnapshot to ``path`` atomically.

    Writes to ``<path>.tmp`` first, then renames over ``path``. A crash
    or serialization failure mid-write leaves the previous baseline (if
    any) intact and removes the partial file.

    ck3_chronicler-elll: writes ``format_version=2`` with a monotonic
    ``generation`` counter (read off disk + 1) and a wall-clock
    ``persisted_at`` ISO timestamp. The counter starts at 1 for fresh
    baselines and for v1 → v2 upgrades.
    """
    from datetime import UTC, datetime

    path.parent.mkdir(parents=True, exist_ok=True)
    next_generation = _next_generation(path)
    payload = {
        "format_version": _BASELINE_FORMAT_VERSION,
        "generation": next_generation,
        "persisted_at": datetime.now(UTC).isoformat(),
        "snapshot": _snapshot_to_dict(snapshot),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        # Clean up the temp file so it doesn't leak across retries.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _next_generation(path: Path) -> int:
    """ck3_chronicler-elll: read the existing baseline's generation
    counter off disk and return ``existing + 1``. Returns 1 when no
    file exists, when the file is unreadable, or when the file is v1
    (no generation field). Never raises — a corrupt prior baseline
    must not block the new persist."""
    if not path.exists():
        return 1
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return 1
    if not isinstance(payload, dict):
        return 1
    gen = payload.get("generation")
    if isinstance(gen, int) and gen >= 0:
        return gen + 1
    return 1


def load_baseline(path: Path) -> BaselineLoad | None:
    """Load a previously-persisted baseline. Returns None if missing,
    corrupt, or written by a future format we don't recognise.

    Never raises — a corrupt baseline must not block the save-tail loop
    from starting fresh.

    ck3_chronicler-elll: returns a :class:`BaselineLoad` carrying the
    snapshot plus forensic fields. Legacy v1 files load successfully
    with ``generation`` and ``persisted_at`` set to ``None``.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("baseline %s unreadable (%s); ignoring", path.name, e)
        return None
    if not isinstance(payload, dict):
        log.warning("baseline %s wrong shape (top-level not dict); ignoring", path.name)
        return None
    version = payload.get("format_version")
    if version not in (1, _BASELINE_FORMAT_VERSION):
        log.warning(
            "baseline %s format_version=%r (expected 1 or %d); ignoring",
            path.name,
            version,
            _BASELINE_FORMAT_VERSION,
        )
        return None
    snap_dict = payload.get("snapshot")
    if not isinstance(snap_dict, dict):
        log.warning("baseline %s missing 'snapshot' dict; ignoring", path.name)
        return None
    # ck3_chronicler-27ov.19 (audit M-D2): model "index absent vs empty"
    # ONCE, here at the load boundary. The generic decoder is deliberately
    # tolerant of a missing key (it falls back to the field's default), so a
    # baseline persisted by an older build would decode into empty prev
    # indexes while curr is fully populated — and the first post-restart tick
    # re-fires whole histories (every dynasty perk ever unlocked, every
    # ongoing war, the whole artifact vault) as phantom events dated to the
    # diff window, where the UNIQUE dedup can't drop them. A current-build
    # persist always writes every SaveSnapshot field, so a missing top-level
    # field means "written by an older build": discard so save-tail
    # rebaselines instead of diffing against the hollow prev.
    missing = [f.name for f in dataclass_fields(SaveSnapshot) if f.name not in snap_dict]
    if missing:
        log.warning(
            "baseline %s predates SaveSnapshot field(s) %s; discarding so "
            "save-tail rebaselines instead of emitting phantom events "
            "against hollow prev indexes (27ov.19)",
            path.name,
            ", ".join(missing),
        )
        return None
    try:
        snapshot = _snapshot_from_dict(snap_dict)
    except (KeyError, TypeError, ValueError) as e:
        log.warning("baseline %s shape mismatch (%s); ignoring", path.name, e)
        return None
    generation = payload.get("generation") if version == _BASELINE_FORMAT_VERSION else None
    persisted_at = payload.get("persisted_at") if version == _BASELINE_FORMAT_VERSION else None
    if not isinstance(generation, int):
        generation = None
    if not isinstance(persisted_at, str):
        persisted_at = None
    return BaselineLoad(snapshot=snapshot, generation=generation, persisted_at=persisted_at)


def delete_baseline_if_exists(path: Path) -> bool:
    """Remove a baseline file if it exists. Returns True iff a file was
    removed. Use when a campaign is archived/deleted."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


# --- serialization (ck3_chronicler-27ov.10 / audit H1/J2) ---
#
# Both directions delegate to the generic dataclass codec. These thin
# wrappers exist to (a) bind the concrete SaveSnapshot type for the decode
# side and (b) keep the on-disk format an explicit, named seam.


def _snapshot_to_dict(snap: SaveSnapshot) -> dict[str, Any]:
    """Encode a SaveSnapshot to its JSON-friendly dict form."""
    return to_jsonable(snap)


def _snapshot_from_dict(data: dict[str, Any]) -> SaveSnapshot:
    """Rebuild a SaveSnapshot from its JSON dict form. Raises on malformed
    input; :func:`load_baseline` catches and discards (rebaseline)."""
    return from_jsonable(SaveSnapshot, data)

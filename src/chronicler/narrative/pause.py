"""Narrative-work gating: the global LLM-pause flag + death-bio drain
(ck3_chronicler-27ov.40 / M-I4).

The policy for *whether* and *when* autonomous narrative generation runs,
lifted out of ``save/ingest.py`` (it is narrative's concern, not the save
pipeline's). :func:`_is_llm_paused` is the cached settings gate the
scheduler consults; :func:`drain_for_campaign` re-fires biography work lost
to a shutdown or an LLM pause. A leaf module — imported by both
``save/tick.py`` and ``save/ingest.py``, importing neither.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic as time_monotonic
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Runtime import would be a save → api circular; type-check only.
    pass

from chronicler.db.engine import (
    session_scope,
)
from chronicler.db.registry import (
    list_tracked_characters,
)
from chronicler.db.repository import (
    get_latest_biography_for_character,
)
from chronicler.narrative.scheduler import NarrativeScheduler
from chronicler.settings_store import load_settings

log = logging.getLogger(__name__)


# ck3_chronicler-gx7b: cache the llm_paused settings read for a short
# window so a single save-tick (which can ingest dozens of events) doesn't
# re-open ~/Documents/chronicler/settings.json once per event. 1 second
# is long enough to amortise a tick, short enough that a Settings-page
# toggle takes effect on the next event the user sees fire.
_LLM_PAUSED_CACHE_TTL_SECONDS = 1.0
_llm_paused_cache: dict[str, Any] = {"checked_at": 0.0, "value": False}


def _is_llm_paused(*, _now: Callable[[], float] = time_monotonic) -> bool:
    """ck3_chronicler-gx7b: read the ``llm_paused`` flag from settings.json
    with a 1-second TTL cache.

    Save-tail keeps ingesting and persisting events regardless. This gate
    only short-circuits the autonomous LLM scheduling call sites
    (DeathEvent → biography, threshold-cross → consolidation). User-driven
    paths (closing chronicle, manual regenerate) check this directly
    or bypass it explicitly.
    """
    now = _now()
    if now - _llm_paused_cache["checked_at"] < _LLM_PAUSED_CACHE_TTL_SECONDS:
        return bool(_llm_paused_cache["value"])
    settings = load_settings()
    paused = bool(settings.get("llm_paused", False))
    _llm_paused_cache["checked_at"] = now
    _llm_paused_cache["value"] = paused
    return paused


def _reset_llm_paused_cache() -> None:
    """Tests + the PUT-toggle handler call this so the next read is fresh."""
    _llm_paused_cache["checked_at"] = 0.0
    _llm_paused_cache["value"] = False


@dataclass(frozen=True, slots=True)
class DrainReport:
    """ck3_chronicler-gx7b: how much work an unpause-drain scheduled.

    Returned by :func:`drain_for_campaign` so the API handler can report
    drain activity after an unpause. ``biographies_scheduled`` reflects
    what was actually enqueued.
    """

    biographies_scheduled: int


def drain_for_campaign(
    *,
    factory,
    scheduler: NarrativeScheduler,
    campaign_id: str,
    registry_path: Path | None,
) -> DrainReport:
    """ck3_chronicler-gx7b: re-fire any biography work lost when chronicler
    last shut down or while LLM was paused.

    Walks the tracked set and schedules biographies for dead-without-bio
    characters. Consolidation scheduling removed (plan: cozy-coalescing-shannon).

    Returns a :class:`DrainReport` so the API handler can surface the
    activity back to the FE ('scheduled N biographies').
    """
    tracked = list_tracked_characters(campaign_id, registry=registry_path)
    if not tracked:
        return DrainReport(biographies_scheduled=0)
    tracked_ids = [t.character_id for t in tracked]

    queue_state = getattr(scheduler, "_queue_state", None)
    biographies_before = _queue_size(queue_state, kind="biography")

    # Death-biography catch-up: schedule any dead tracked character that
    # has no biography on disk. Scheduler-side gates (tracked / paused /
    # per-character lock) keep this safe under races with the live tick.
    with session_scope(factory) as session:
        from sqlalchemy import select as _select

        from chronicler.db.models import Character

        rows = session.execute(
            _select(Character.ck3_id, Character.death_date)
            .where(Character.ck3_id.in_(tracked_ids))
            .where(Character.death_date.is_not(None))
        ).all()
        for ck3_id, death_date in rows:
            if get_latest_biography_for_character(session, ck3_id) is None:
                log.info(
                    "drain: scheduling biography for tracked character %d "
                    "(died %s, no biography on disk)",
                    ck3_id,
                    death_date,
                )
                scheduler.schedule(ck3_id)

    biographies_after = _queue_size(queue_state, kind="biography")
    return DrainReport(
        biographies_scheduled=max(0, biographies_after - biographies_before),
    )


def _queue_size(queue_state, *, kind: str) -> int:
    """Best-effort 'queued + generating' count for a kind. Returns 0 when
    no queue_state is wired (CLI standalone, tests) — drain still works,
    just can't report what landed."""
    if queue_state is None:
        return 0
    try:
        snapshot = queue_state.snapshot()
    except Exception:
        return 0
    queued = getattr(snapshot, "queued", None) or []
    active = getattr(snapshot, "active", None) or []
    return sum(1 for item in queued if getattr(item, "kind", None) == kind) + sum(
        1 for item in active if getattr(item, "kind", None) == kind
    )

"""Registry-backed character predicates shared by the ingest loops.

Both the save-file consumer (:mod:`chronicler.save.ingest`) and the
debug_log tailer (:mod:`chronicler.tailer.ingest`) need the same three
answers about a character: is it tracked, is its biography paused, and
has it been bumped. They used to build their own closures with different
caching; :class:`TrackedPolicy` is the single implementation.

Caching choice (applied to both loops): ``is_tracked`` is TTL-cached for
:data:`REGISTRY_PREDICATE_TTL_SECONDS`. Tracking is a durable,
human-timescale change and a busy tail otherwise opens a fresh sqlite
connection per event (F008 / 81v). ``is_paused`` and ``bumped_at`` are
deliberately NOT cached: Pause/Resume/Bump from the Tracked page must
take effect on the next event tick (ck3_chronicler-t2v5).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from chronicler.db.registry import get_tracked_status, is_character_tracked

_T = TypeVar("_T")

# F008 / 81v: how long to trust a cached registry-predicate result before
# re-querying. Two seconds: the user adds tracked characters / suppressions
# on a human timescale via `chronicler track` from another terminal; a 2s
# lag before the tail picks up the change is imperceptible. A typical
# debug_log tail fires hundreds of CHRONICLER lines per minute, so even
# this short TTL collapses ~99% of the registry-DB open/close churn.
REGISTRY_PREDICATE_TTL_SECONDS = 2.0


def ttl_cached(fn: Callable[..., _T], *, ttl_seconds: float) -> Callable[..., _T]:
    """Wrap ``fn`` so identical-argument calls within ``ttl_seconds`` reuse the
    last result. Uses :func:`time.monotonic` so wall-clock changes can't
    expire the cache early. Argument tuples must be hashable.
    """
    cache: dict[tuple[Any, ...], tuple[float, _T]] = {}

    def _check(*args: Any) -> _T:
        now = time.monotonic()
        cached = cache.get(args)
        if cached is not None and now - cached[0] < ttl_seconds:
            return cached[1]
        result = fn(*args)
        cache[args] = (now, result)
        return result

    return _check


class TrackedPolicy:
    """The campaign's tracked/paused/bumped registry predicates."""

    def __init__(self, campaign_id: str, registry_path: Path | None = None) -> None:
        self.campaign_id = campaign_id
        self.registry_path = registry_path
        self._is_tracked_cached = ttl_cached(
            lambda character_id: is_character_tracked(
                campaign_id, character_id, registry=registry_path
            ),
            ttl_seconds=REGISTRY_PREDICATE_TTL_SECONDS,
        )

    def is_tracked(self, character_id: int) -> bool:
        return self._is_tracked_cached(character_id)

    def is_paused(self, character_id: int) -> bool:
        paused, _ = get_tracked_status(self.campaign_id, character_id, registry=self.registry_path)
        return paused

    def bumped_at(self, character_id: int) -> str | None:
        _, bumped = get_tracked_status(self.campaign_id, character_id, registry=self.registry_path)
        return bumped

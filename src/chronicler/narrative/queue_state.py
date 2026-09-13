"""Live queue state for narrative tasks (ck3_chronicler-eev).

Tracks every biography the :class:`NarrativeScheduler` dispatches,
exposes a snapshot for the
``GET /api/campaigns/{name}/narrative/queue`` endpoint, and notifies
listeners on each state transition so the SSE channel can fan out
``narrative_queued`` / ``narrative_started`` / ``narrative_completed``
/ ``narrative_failed`` events for the AppShell activity strip.

The scheduler holds a :class:`NarrativeQueueState` and calls
:meth:`enqueue`, :meth:`mark_started`, :meth:`mark_completed`,
:meth:`mark_failed` at each lifecycle point. The state object is the
shared blackboard between the scheduler (writer) and the FastAPI
handlers (reader). When the standalone CLI (``chronicler save-tail``)
runs without an app, the scheduler still updates an in-memory state
object so the data is available; the SSE side is simply absent.

Recent-completions ring is capped at ``recent_capacity`` so memory
doesn't grow unbounded over a long marathon — completed items beyond
the cap roll off the snapshot but the totals (``completed_count``,
``failed_count``) keep climbing.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

QueueStatus = Literal["queued", "generating", "completed", "failed"]
QueueKind = Literal["biography"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class QueueItem:
    """One narrative task tracked by the scheduler.

    ``item_id`` is monotonically assigned by the queue state — it lets
    the scheduler refer back to the same row across status transitions
    even when the same character has multiple tasks (biography +
    consolidation, or duplicate-death debug regenerations).
    """

    item_id: int
    character_id: int
    kind: QueueKind
    status: QueueStatus
    enqueued_at: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    # ck3_chronicler-27ov.81 (audit L30): the character's first name,
    # resolved by the scheduler at enqueue time from the per-campaign DB
    # (the scheduler knows its own campaign) and carried on the wire so
    # the queue strip + page render names without a FE join. That join
    # only saw the active campaign's top-250 relevance window, so it
    # missed tracked souls on large campaigns and could never name
    # cross-campaign items at all. None when the name couldn't be
    # resolved — the FE falls back to "character {id}".
    character_name: str | None = None
    # ck3_chronicler-u0eu (2026-05-08): the campaign that owns this
    # queue item. Lets the SSE-fanout listener publish to the right
    # per-campaign channel without depending on ambient
    # ``bound_campaign_for_narrative`` state on the orchestrator —
    # which only exists when a save-tail loop is running, and so
    # silenced lazy-regen frames against sealed campaigns. None
    # means 'no campaign context' (back-compat for older callers
    # and tests); listeners should skip publishing in that case.
    campaign_uuid: str | None = None


@dataclass
class QueueSnapshot:
    """Point-in-time view of the queue, returned by the HTTP endpoint."""

    queued: list[QueueItem] = field(default_factory=list)
    active: list[QueueItem] = field(default_factory=list)
    recent: list[QueueItem] = field(default_factory=list)
    completed_count: int = 0
    failed_count: int = 0
    avg_duration_ms: int | None = None


@dataclass
class CharacterStats:
    """ck3_chronicler-fjln: per-character lifetime aggregates surfaced
    on the queue page. Keyed on (character_id, kind).

    ``median_duration_ms`` is computed over a bounded sliding window
    (most-recent 20 successful completions) so memory doesn't grow
    unbounded over a marathon and median tracks recent provider
    behaviour rather than ancient warm-up samples. Failed runs don't
    contribute to the duration window."""

    character_id: int
    kind: QueueKind
    completed_count: int
    failed_count: int
    median_duration_ms: int | None
    last_success_at: str | None
    # ck3_chronicler-27ov.81 (audit L30): first name, stashed from the
    # QueueItem on each completion/failure so the queue page's stats
    # table renders names without the same FE join the queue items shed.
    # None until the first transition stamps it (or if the name was never
    # resolved at enqueue).
    character_name: str | None


class NarrativeQueueState:
    """Mutable shared state tracking the live narrative-generation queue.

    Thread-safe via an internal lock so the scheduler (asyncio task
    context) and the HTTP read path (anyio threadpool for sync handlers)
    can both poke at it without races. Listeners are invoked while
    holding the lock — keep them cheap; the scheduler's listener is a
    one-line event_bus.publish().
    """

    def __init__(
        self,
        *,
        recent_capacity: int = 20,
        per_character_sample_window: int = 20,
    ) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._items: dict[int, QueueItem] = {}
        self._recent: deque[QueueItem] = deque(maxlen=recent_capacity)
        self._completed_count = 0
        self._failed_count = 0
        self._duration_total_ms = 0
        self._duration_samples = 0
        self._listeners: list[Callable[[QueueItem], None]] = []
        # ck3_chronicler-fjln: per-character aggregates. Keyed on
        # (character_id, kind). Each value is a small dict so we can
        # update completed/failed counts and append durations without
        # rebuilding immutable structures on every transition.
        # ``_per_character_sample_window`` bounds the duration deque per
        # key so a long marathon can't grow this dict's memory
        # unboundedly per character.
        self._per_character_sample_window = per_character_sample_window
        self._per_char: dict[tuple[int, QueueKind], dict[str, object]] = {}

    def add_listener(self, listener: Callable[[QueueItem], None]) -> None:
        """Register a callback fired on every status transition.

        The listener receives the QueueItem in its post-transition state
        — inspect ``item.status`` to discriminate. Exceptions in
        listeners are caught and dropped so a busted SSE publish can't
        wedge the scheduler.
        """
        with self._lock:
            self._listeners.append(listener)

    def enqueue(
        self,
        character_id: int,
        kind: QueueKind,
        *,
        campaign_uuid: str | None = None,
        character_name: str | None = None,
    ) -> int:
        with self._lock:
            item_id = self._next_id
            self._next_id += 1
            item = QueueItem(
                item_id=item_id,
                character_id=character_id,
                kind=kind,
                status="queued",
                enqueued_at=_now_iso(),
                character_name=character_name,
                campaign_uuid=campaign_uuid,
            )
            self._items[item_id] = item
            self._notify_locked(item)
            return item_id

    def mark_started(self, item_id: int) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.status != "queued":
                return
            item.status = "generating"
            item.started_at = _now_iso()
            self._notify_locked(item)

    def mark_completed(self, item_id: int, duration_ms: int) -> None:
        with self._lock:
            item = self._items.pop(item_id, None)
            if item is None:
                return
            item.status = "completed"
            item.completed_at = _now_iso()
            item.duration_ms = max(0, int(duration_ms))
            self._completed_count += 1
            self._duration_total_ms += item.duration_ms
            self._duration_samples += 1
            self._recent.append(item)
            # ck3_chronicler-fjln: per-character aggregate update.
            stats = self._per_char_locked(item.character_id, item.kind)
            stats["completed_count"] = int(stats["completed_count"]) + 1  # type: ignore[arg-type]
            durations: deque[int] = stats["durations"]  # type: ignore[assignment]
            durations.append(item.duration_ms)
            stats["last_success_at"] = item.completed_at
            if item.character_name is not None:
                stats["character_name"] = item.character_name
            self._notify_locked(item)

    def mark_failed(self, item_id: int, error: str) -> None:
        with self._lock:
            item = self._items.pop(item_id, None)
            if item is None:
                return
            item.status = "failed"
            item.completed_at = _now_iso()
            item.error = error
            self._failed_count += 1
            self._recent.append(item)
            # ck3_chronicler-fjln: per-character aggregate update.
            # Failures do NOT touch the duration window or last_success_at.
            stats = self._per_char_locked(item.character_id, item.kind)
            stats["failed_count"] = int(stats["failed_count"]) + 1  # type: ignore[arg-type]
            if item.character_name is not None:
                stats["character_name"] = item.character_name
            self._notify_locked(item)

    def remove(self, item_id: int) -> bool:
        """Drop an item WITHOUT a terminal transition. Returns True if present.

        Unlike :meth:`mark_failed` / :meth:`mark_completed`, this records no
        stats, appends nothing to the recent ring, and fires no listener frame.
        Used by the reorder path (cancel-and-respawn): a reordered item 'left
        the queue', it did not fail (ck3_chronicler-27ov.42 / audit M-N3).
        """
        with self._lock:
            return self._items.pop(item_id, None) is not None

    def _per_char_locked(self, character_id: int, kind: QueueKind) -> dict[str, object]:
        """ck3_chronicler-fjln: lookup-or-create the per-(character, kind)
        aggregates dict. Caller must hold ``self._lock``. The dict shape
        is opaque — :meth:`character_stats` assembles the public
        :class:`CharacterStats` view from it on demand."""
        key = (character_id, kind)
        existing = self._per_char.get(key)
        if existing is not None:
            return existing
        fresh: dict[str, object] = {
            "completed_count": 0,
            "failed_count": 0,
            "durations": deque(maxlen=self._per_character_sample_window),
            "last_success_at": None,
            "character_name": None,
        }
        self._per_char[key] = fresh
        return fresh

    def character_stats(self) -> list[CharacterStats]:
        """ck3_chronicler-fjln: snapshot per-character aggregates. One
        :class:`CharacterStats` row per (character_id, kind) pair the
        scheduler has touched. Empty list when no work has completed
        or failed yet — frontend uses that to skip rendering the panel
        cleanly. Sorted (character_id, kind) so callers get a stable
        order without an extra sort."""
        with self._lock:
            rows: list[CharacterStats] = []
            for (character_id, kind), stats in self._per_char.items():
                durations: deque[int] = stats["durations"]  # type: ignore[assignment]
                if durations:
                    sorted_d = sorted(durations)
                    median = sorted_d[len(sorted_d) // 2]
                else:
                    median = None
                rows.append(
                    CharacterStats(
                        character_id=character_id,
                        kind=kind,
                        completed_count=int(stats["completed_count"]),  # type: ignore[arg-type]
                        failed_count=int(stats["failed_count"]),  # type: ignore[arg-type]
                        median_duration_ms=median,
                        last_success_at=stats["last_success_at"],  # type: ignore[arg-type]
                        character_name=stats["character_name"],  # type: ignore[arg-type]
                    )
                )
            rows.sort(key=lambda r: (r.character_id, r.kind))
            return rows

    def snapshot(self) -> QueueSnapshot:
        with self._lock:
            queued: list[QueueItem] = []
            active: list[QueueItem] = []
            for item in self._items.values():
                if item.status == "generating":
                    active.append(item)
                elif item.status == "queued":
                    queued.append(item)
            queued.sort(key=lambda i: i.item_id)
            active.sort(key=lambda i: i.item_id)
            avg = (
                self._duration_total_ms // self._duration_samples
                if self._duration_samples > 0
                else None
            )
            return QueueSnapshot(
                queued=queued,
                active=active,
                recent=list(self._recent),
                completed_count=self._completed_count,
                failed_count=self._failed_count,
                avg_duration_ms=avg,
            )

    def find_recent(self, item_id: int) -> QueueItem | None:
        """ck3_chronicler-c5wq: lookup a completed/failed item by id so
        the regenerate endpoint can resolve (character_id, kind) without
        the caller needing the full ring snapshot.

        Returns ``None`` for unknown ids and for items still queued or
        generating (those live in ``_items``, not ``_recent``)."""
        with self._lock:
            for item in self._recent:
                if item.item_id == item_id:
                    return item
            return None

    def _notify_locked(self, item: QueueItem) -> None:
        # Listener exceptions are swallowed — the queue mutation is
        # already committed; observability hooks must not fail closed.
        # We deliberately call listeners while still holding the lock so
        # SSE order matches state-mutation order; listeners are expected
        # to be cheap (single put_nowait on the event bus).
        for listener in self._listeners:
            # Listener exceptions are swallowed — observability hooks
            # must not fail closed. The queue mutation has already been
            # committed before this loop runs.
            with suppress(Exception):
                listener(item)

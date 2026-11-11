"""Shared types for the snapshot diff package.

:class:`DiffEvent` wraps one emitted event with its participants;
:class:`DiffContext` bundles the inputs every differ receives. Snapshot-
level differ functions all take a single :class:`DiffContext` so the
registry can call them uniformly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from chronicler.save.parse import CharacterSnapshot, MemorySnapshot, SaveSnapshot
from chronicler.schema import EventPayload


@dataclass(frozen=True, slots=True)
class DiffEvent:
    """One event emitted by :func:`chronicler.save.diff.diff_snapshots`.

    Wraps the Pydantic event payload with the participant tuple
    (compatible with the existing
    :class:`chronicler.tailer.parser.ParsedEvent` shape so the
    repository layer ingests both transports identically).
    """

    event: EventPayload
    # role -> character_id pairs from named scopes (similar to the
    # debug_log scope-dump participants but derived from the save state)
    participants: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class DiffContext:
    """Everything one differ needs for a single character pair.

    ``pregnant_trait_id`` is resolved once per diff and ``new_memories``
    once per character pair by :func:`diff_snapshots`; differs read them
    from here instead of recomputing (ck3_chronicler-27ov.38).
    """

    cid: int
    prev_char: CharacterSnapshot | None
    curr_char: CharacterSnapshot
    prev: SaveSnapshot
    curr: SaveSnapshot
    date: str
    pregnant_trait_id: int | None = None
    new_memories: tuple[MemorySnapshot, ...] = ()


# (ctx) -> events
StreamDiffer = Callable[[DiffContext], list[DiffEvent]]

# (cid, element_id, prev_snapshot, curr_snapshot, date) -> one event or None.
# Returning None skips this element (e.g. a missing record, or a transition
# this stream doesn't emit on). Receives BOTH snapshots so the builder reads
# metadata from whichever side carries it (curr for acquisitions, prev for
# completions where CK3 pruned the record).
SetDiffBuilder = Callable[[int, int, SaveSnapshot, SaveSnapshot, str], DiffEvent | None]


@dataclass(frozen=True)
class SetDiffSpec:
    """One per pure character_to_X set-diff stream. ``on_added`` /
    ``on_removed`` are None when the stream emits nothing on that
    transition (e.g. epidemics: outbreak only; activities: completion
    only)."""

    name: str
    index: Callable[[SaveSnapshot], dict[int, frozenset[int]]]
    on_added: SetDiffBuilder | None = None
    on_removed: SetDiffBuilder | None = None


def make_set_differ(spec: SetDiffSpec) -> StreamDiffer:
    """Build a StreamDiffer that runs the shared set-diff skeleton for
    ``spec``. Behavior matches the hand-written _diff_* helpers exactly:
    dead characters short-circuit, an unchanged index short-circuits,
    added/removed ids are processed in sorted() order, and a builder that
    returns None contributes no event.

    The returned callable accepts a single :class:`DiffContext`. The
    legacy positional form ``(cid, char, prev, curr, date)`` is still
    accepted for direct callers that build the skeleton inputs by hand.
    """

    def differ(ctx: DiffContext, *legacy: object) -> list[DiffEvent]:
        if legacy:
            char, prev, curr, date = legacy
            ctx = DiffContext(
                cid=ctx,
                prev_char=None,
                curr_char=char,
                prev=prev,
                curr=curr,
                date=date,
            )
        if ctx.curr_char.is_dead:
            return []
        prev_set = spec.index(ctx.prev).get(ctx.cid, frozenset())
        curr_set = spec.index(ctx.curr).get(ctx.cid, frozenset())
        if prev_set == curr_set:
            return []
        events: list[DiffEvent] = []
        if spec.on_added is not None:
            for eid in sorted(curr_set - prev_set):
                ev = spec.on_added(ctx.cid, eid, ctx.prev, ctx.curr, ctx.date)
                if ev is not None:
                    events.append(ev)
        if spec.on_removed is not None:
            for eid in sorted(prev_set - curr_set):
                ev = spec.on_removed(ctx.cid, eid, ctx.prev, ctx.curr, ctx.date)
                if ev is not None:
                    events.append(ev)
        return events

    return differ

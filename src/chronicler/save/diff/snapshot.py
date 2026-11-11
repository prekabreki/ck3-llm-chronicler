"""Compare two snapshots and emit events."""

from __future__ import annotations

import logging

from chronicler.save.diff.character import (
    _PREGNANT_TRAIT_NAME,
    _make_death,
    _trait_id_by_name,
)
from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.save.diff.registry import STREAM_DIFFERS
from chronicler.save.parse import SaveSnapshot

log = logging.getLogger(__name__)


def diff_snapshots(
    prev: SaveSnapshot,
    curr: SaveSnapshot,
    *,
    tracked_filter: set[int] | None = None,
) -> list[DiffEvent]:
    """Compare two snapshots; return events that occurred between them.

    Both snapshots must come from the same campaign (same
    ``playthrough_id``). The caller is responsible for pairing
    chronologically-adjacent saves; this function does not validate
    ordering or contiguity.

    ``tracked_filter`` is a set of character IDs the caller cares
    about. When provided, events whose primary character is not in the
    set are dropped at source — load-bearing for save-tail against
    bookmarks with tens of thousands of characters where the per-month
    diff produces ~2000 travel/title events (most of which are NPCs the
    user will never look at). Pass ``None`` (the default) to retain
    every event — that's right for ``import-save``-style explicit
    backfills where the user is opting into full historical context.

    The filter applies to the primary character ID only. Tracked
    characters' family relationships are still recorded as event
    participants regardless of whether each family member is itself
    tracked.
    """
    if prev.playthrough_id and curr.playthrough_id and prev.playthrough_id != curr.playthrough_id:
        raise ValueError(
            f"playthrough_id mismatch: {prev.playthrough_id!r} vs {curr.playthrough_id!r}"
        )

    out: list[DiffEvent] = []
    date = curr.current_date

    # ck3_chronicler-27ov.38 (audit M-D3): resolve the pregnant trait id
    # once per diff — _diff_miscarriage used to linear-scan the
    # ~600-entry traits_lookup per character pair, ≈24M string compares
    # over a 40k-character full diff during an import backlog drain.
    pregnant_trait_id = _trait_id_by_name(_PREGNANT_TRAIT_NAME, curr.traits_lookup)

    # Iterate over the union of character IDs in both snapshots — but
    # short-circuit to the tracked set when one is provided. With ~40k
    # characters in a populous bookmark, this turns the per-pair work
    # from O(world) into O(tracked) which is what makes save-tail
    # tractable on large worlds.
    if tracked_filter is None:
        all_ids: set[int] | frozenset[int] = set(prev.characters) | set(curr.characters)
    else:
        all_ids = tracked_filter
    for cid in all_ids:
        p = prev.characters.get(cid)
        c = curr.characters.get(cid)
        if c is None:
            # Character disappeared from both living + dead_unprunable.
            # CK3 prunes minor dead NPCs to manage save size — not an
            # event, just GC.
            continue
        if p is None:
            # ck3_chronicler-dwg0: Character is in curr but not prev.
            # When the character is on the tracked filter (the only ones
            # we iterate when tracked_filter is set), this is the first
            # observation — likely _auto_track_new_candidates added them
            # mid-campaign and they're missing from the in-memory
            # baseline. We skip the per-character diff (no prev to
            # compare against) but log it so silent first-tick event
            # loss has a search-engine hit.
            #
            # A tracked character first observed already DEAD is the one
            # exception: death is terminal and fully described by curr
            # alone, and skipping it leaves death_date NULL so the
            # biography is never scheduled. This is how campaigns whose
            # baseline was persisted by a parser that dropped grouped
            # dead_unprunable records recover their missed deaths.
            # Scoped to the tracked path: unfiltered, every dead character
            # absent from a stale baseline would turn into a DeathEvent.
            if tracked_filter is not None and (c.is_dead or c.death_date is not None):
                log.info(
                    "diff_snapshots: tracked character %d first observed dead "
                    "(died %s); emitting the missed death",
                    cid,
                    c.death_date,
                )
                out.append(_make_death(c, date))
                continue
            log.info(
                "diff_snapshots: character %d in curr but not prev — first "
                "observation; events on this tick are skipped (tracked=%s)",
                cid,
                tracked_filter is not None,
            )
            continue

        # ck3_chronicler-27ov.38: the newly-visible memory window is
        # needed by both the vanilla-memory emission and miscarriage
        # detection — compute it once per pair instead of twice.
        prev_mem_ids = {m.memory_id for m in p.memories}
        new_memories = tuple(m for m in c.memories if m.memory_id not in prev_mem_ids)

        ctx = DiffContext(
            cid=cid,
            prev_char=p,
            curr_char=c,
            prev=prev,
            curr=curr,
            date=date,
            pregnant_trait_id=pregnant_trait_id,
            new_memories=new_memories,
        )
        for differ in STREAM_DIFFERS:
            out.extend(differ(ctx))

    # Stable order: by character ID, then event type. Makes replay
    # deterministic for testing and for the biography prompt.
    out.sort(key=lambda d: (d.event.c, d.event.t))
    return out

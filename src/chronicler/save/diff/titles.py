"""Title-held deltas (ck3_chronicler-dr9)."""

from __future__ import annotations

from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.schema import (
    TitleAcquiredEvent,
    TitleAcquiredPayload,
    TitleCreatedEvent,
    TitleCreatedPayload,
    TitleRelinquishedEvent,
    TitleRelinquishedPayload,
)


def _diff_titles(ctx: DiffContext) -> list[DiffEvent]:
    """Emit TitleAcquired / TitleRelinquished / TitleCreated events for
    one character's holdings between snapshots (ck3_chronicler-dr9).

    For ``TitleAcquired``: title was held by someone else (or no one) in
    prev, by ``character_id`` in curr.
    For ``TitleRelinquished``: title was held by ``character_id`` in
    prev, by someone else (or no one) in curr.
    For ``TitleCreated``: title doesn't exist in prev at all and is held
    by ``character_id`` in curr (decision-driven creation, e.g. unify
    petty kingdom).

    No noise mitigation at this layer per the issue notes — emit per
    title, defer coalescing until live testing shows the firehose hurts.

    ck3_chronicler-hk9i: reads from the precomputed
    :data:`SaveSnapshot.title_holders` reverse index — set lookup per
    character instead of a full scan over every title row in the
    snapshot, which dominated diff cost on populous bookmarks.
    """
    character_id = ctx.cid
    prev = ctx.prev
    curr = ctx.curr
    date = ctx.date
    prev_titles = prev.titles
    curr_titles = curr.titles
    # ck3_chronicler-qz5y: when prev has no titles at all the diff
    # window's "baseline" is empty (stale or crash-recovery state). Every
    # currently-held title would otherwise be flagged as freshly-created,
    # generating a phantom title_created storm for titles that have
    # existed since 867 AD.
    if not prev_titles:
        return []
    events: list[DiffEvent] = []
    # ck3_chronicler-hk9i: prefer the precomputed reverse index built at
    # parse time; fall back to a one-shot scan for hand-constructed
    # SaveSnapshot instances in tests where ``title_holders`` is empty.
    if prev.title_holders:
        prev_held = prev.title_holders.get(character_id, frozenset())
    else:
        prev_held = frozenset(tid for tid, t in prev_titles.items() if t.holder_id == character_id)
    if curr.title_holders:
        curr_held = curr.title_holders.get(character_id, frozenset())
    else:
        curr_held = frozenset(tid for tid, t in curr_titles.items() if t.holder_id == character_id)

    for tid in sorted(curr_held - prev_held):
        title = curr_titles[tid]
        prev_title = prev_titles.get(tid)
        if prev_title is None:
            # title didn't exist in prev — decision-driven creation
            events.append(
                DiffEvent(
                    event=TitleCreatedEvent(
                        d=date,
                        c=character_id,
                        p=TitleCreatedPayload(
                            title_id=tid,
                            title_key=title.key,
                            title_name=title.name,
                            tier=title.tier,
                        ),
                    ),
                    participants=(),
                )
            )
        else:
            # title existed; we just took it over
            events.append(
                DiffEvent(
                    event=TitleAcquiredEvent(
                        d=date,
                        c=character_id,
                        p=TitleAcquiredPayload(
                            title_id=tid,
                            title_key=title.key,
                            title_name=title.name,
                            tier=title.tier,
                            from_holder_id=prev_title.holder_id,
                        ),
                    ),
                    participants=(
                        (("former_holder", prev_title.holder_id),)
                        if prev_title.holder_id is not None
                        else ()
                    ),
                )
            )

    for tid in sorted(prev_held - curr_held):
        prev_title = prev_titles[tid]
        curr_title = curr_titles.get(tid)
        events.append(
            DiffEvent(
                event=TitleRelinquishedEvent(
                    d=date,
                    c=character_id,
                    p=TitleRelinquishedPayload(
                        title_id=tid,
                        title_key=prev_title.key,
                        title_name=prev_title.name,
                        tier=prev_title.tier,
                        to_holder_id=curr_title.holder_id if curr_title else None,
                    ),
                ),
                participants=(
                    (("new_holder", curr_title.holder_id),)
                    if curr_title and curr_title.holder_id is not None
                    else ()
                ),
            )
        )

    return events

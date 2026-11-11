"""Dynasty legacy and splendor deltas."""

from __future__ import annotations

from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.save.parse import CharacterSnapshot
from chronicler.schema import (
    DynastyLegacyPayload,
    DynastyLegacyUnlockedEvent,
    SplendorIncreasedEvent,
    SplendorPayload,
)


def _diff_dynasty_legacies(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-d83: emit dynasty_legacy_unlocked when the
    character's dynasty advances a perk between snapshots.

    Resolves the character's dynasty via house_to_dynasty (their
    dynasty_house_id → dynasty_id). When the character has no house
    or the house has no dynasty edge, no events emit. Skipped on dead
    characters as a matter of principle (their dynasty's state
    transitions belong to the living)."""
    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_perks = ctx.prev.dynasty_perks
    curr_perks = ctx.curr.dynasty_perks
    house_to_dynasty = ctx.curr.house_to_dynasty
    dynasties_lookup = ctx.curr.dynasties_lookup
    date = ctx.date
    if char.is_dead:
        return []
    if char.dynasty_house_id is None:
        return []
    dynasty_id = house_to_dynasty.get(char.dynasty_house_id)
    if dynasty_id is None:
        return []
    prev_set = prev_perks.get(dynasty_id, frozenset())
    curr_set = curr_perks.get(dynasty_id, frozenset())
    new_perks = curr_set - prev_set
    if not new_perks:
        return []
    dynasty_name = dynasties_lookup.get(dynasty_id)
    events: list[DiffEvent] = []
    for legacy_key in sorted(new_perks):
        events.append(
            DiffEvent(
                event=DynastyLegacyUnlockedEvent(
                    d=date,
                    c=ck3_id,
                    p=DynastyLegacyPayload(
                        dynasty_id=dynasty_id,
                        dynasty_name=dynasty_name,
                        legacy_key=legacy_key,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _diff_dynasty_splendor(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-ei8t: emit splendor_increased when the character's
    dynasty crosses a splendor tier upward between snapshots.

    Mirrors :func:`_diff_dynasty_legacies`'s character→dynasty
    resolution via house_to_dynasty. Tier comes from
    :func:`derive_splendor_tier` applied to lifetime accumulated
    renown. Increases-only — tier drops (rare; renown-refund paths)
    produce no event. Skipped on dead characters: their dynasty's
    state transitions belong to the living."""
    from chronicler.save.dynasty_splendor import derive_splendor_tier

    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_renown = ctx.prev.dynasty_renown
    curr_renown = ctx.curr.dynasty_renown
    house_to_dynasty = ctx.curr.house_to_dynasty
    dynasties_lookup = ctx.curr.dynasties_lookup
    dynasty_heads = ctx.curr.dynasty_heads
    date = ctx.date
    if char.is_dead:
        return []
    if char.dynasty_house_id is None:
        return []
    dynasty_id = house_to_dynasty.get(char.dynasty_house_id)
    if dynasty_id is None:
        return []
    prev_acc = prev_renown.get(dynasty_id, 0.0)
    curr_acc = curr_renown.get(dynasty_id, 0.0)
    prev_tier = derive_splendor_tier(prev_acc)
    curr_tier = derive_splendor_tier(curr_acc)
    if curr_tier <= prev_tier:
        return []
    return [
        DiffEvent(
            event=SplendorIncreasedEvent(
                d=date,
                c=ck3_id,
                p=SplendorPayload(
                    dynasty_id=dynasty_id,
                    dynasty_name=dynasties_lookup.get(dynasty_id),
                    dynasty_head_id=dynasty_heads.get(dynasty_id),
                    old_tier=prev_tier,
                    new_tier=curr_tier,
                    total_renown_at_change=curr_acc,
                ),
            ),
            participants=(),
        )
    ]

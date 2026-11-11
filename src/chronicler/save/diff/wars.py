"""War deltas (ck3_chronicler-o7j)."""

from __future__ import annotations

from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.save.localization import strip_loca_markup
from chronicler.save.parse import CharacterSnapshot, WarSnapshot
from chronicler.schema import (
    WarConcludedEvent,
    WarDeclaredEvent,
    WarJoinedEvent,
    WarLeftEvent,
    WarSidePayload,
)


def _war_side_payload(
    war: WarSnapshot,
    *,
    side: str | None,
) -> WarSidePayload:
    """Build the shared WarSidePayload from a WarSnapshot + a resolved
    side. ``side`` is None for ``war_concluded`` events because the
    concluded war's participant breakdown isn't always preserved
    across the diff (CK3 deletes concluded wars from the snapshot)."""
    return WarSidePayload(
        war_id=war.war_id,
        war_name=strip_loca_markup(war.name),
        casus_belli_type=war.casus_belli_type,
        targeted_titles=war.targeted_titles,
        side=side if side in ("attacker", "defender") else None,
        primary_attacker_id=war.primary_attacker_id,
        primary_defender_id=war.primary_defender_id,
        claimant_id=war.claimant_id,
    )


def _resolve_war_side(war: WarSnapshot, ck3_id: int) -> str | None:
    """Decide which side a tracked character is on, given the war
    snapshot's attacker/defender participant sets. Returns None when
    the character isn't in either set — shouldn't normally happen
    since the call site already established membership via
    character_to_wars, but the snapshots can be out of sync if rakaly
    truncates a side mid-parse."""
    if ck3_id in war.attacker_participants:
        return "attacker"
    if ck3_id in war.defender_participants:
        return "defender"
    return None


def _war_participants(war: WarSnapshot, side: str | None) -> tuple[tuple[str, int], ...]:
    """Build the participants tuple for a war event. Surfaces the
    primaries on each side (with role labels matching the side
    semantics) so the consolidation prompt's names_map glossary picks
    them up — "Erik joined Sigurd's holy war against Æthelred" is
    only possible if Sigurd and Æthelred are in the participants
    list. The claimant gets a separate role when present (claimant
    wars name a third party who'd benefit)."""
    out: list[tuple[str, int]] = []
    if war.primary_attacker_id is not None:
        out.append(("primary_attacker", war.primary_attacker_id))
    if war.primary_defender_id is not None:
        out.append(("primary_defender", war.primary_defender_id))
    if war.claimant_id is not None and war.claimant_id not in (
        war.primary_attacker_id,
        war.primary_defender_id,
    ):
        out.append(("claimant", war.claimant_id))
    return tuple(out)


def _diff_wars(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-o7j: emit War[Declared|Joined|Left|Concluded]
    events from per-character war-set deltas + war_id existence check.

    Logic mirrors :func:`_diff_alliances` — set-diff on
    character_to_wars[ck3_id]. The added wrinkle is distinguishing the
    four event types:

    - war_id new in curr_wars + char is the cb.attacker/cb.defender
      → ``war_declared`` (this character is the principal)
    - war_id new in curr_wars + char is a non-principal participant
      → ``war_joined`` (called in via alliance/vassalage)
    - war_id existed in prev + char is newly a participant
      → ``war_joined``
    - war_id present in prev_wars but absent from curr_wars entirely
      → ``war_concluded`` (CK3 deletes concluded wars from active_wars)
    - war_id still in curr_wars + char left the participant set
      → ``war_left`` (separate peace, side switch, or released)

    Skipped on dead characters; deaths produce a flood of
    ``war_left`` events as the engine cleans up participation, all of
    which we'd already capture at the ``DeathEvent`` boundary.
    """
    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_wars = ctx.prev.wars
    curr_wars = ctx.curr.wars
    prev_char_to_wars = ctx.prev.character_to_wars
    curr_char_to_wars = ctx.curr.character_to_wars
    date = ctx.date
    if char.is_dead:
        return []
    prev_set = prev_char_to_wars.get(ck3_id, frozenset())
    curr_set = curr_char_to_wars.get(ck3_id, frozenset())
    if prev_set == curr_set:
        return []

    events: list[DiffEvent] = []

    for war_id in sorted(curr_set - prev_set):
        war = curr_wars.get(war_id)
        if war is None:
            continue
        side = _resolve_war_side(war, ck3_id)
        is_principal = ck3_id in (
            war.primary_attacker_id,
            war.primary_defender_id,
        )
        # War newly appeared this tick AND character is the principal
        # on either side → declared. Otherwise (existing war, or new
        # war but called-in vassal) → joined.
        if war_id not in prev_wars and is_principal:
            events.append(
                DiffEvent(
                    event=WarDeclaredEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(war, side=side),
                    ),
                    participants=_war_participants(war, side),
                )
            )
        else:
            events.append(
                DiffEvent(
                    event=WarJoinedEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(war, side=side),
                    ),
                    participants=_war_participants(war, side),
                )
            )

    for war_id in sorted(prev_set - curr_set):
        prev_war = prev_wars.get(war_id)
        if prev_war is None:
            # Shouldn't happen — the set was built from prev_wars — but
            # be defensive: skip rather than emit a bogus payload.
            continue
        if war_id not in curr_wars:
            # War vanished — concluded. Side may be unknowable on the
            # current tick (war is gone), but we can still resolve from
            # prev_war's snapshot.
            side = _resolve_war_side(prev_war, ck3_id)
            events.append(
                DiffEvent(
                    event=WarConcludedEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(prev_war, side=side),
                    ),
                    participants=_war_participants(prev_war, side),
                )
            )
        else:
            # War still active, character left it
            side = _resolve_war_side(prev_war, ck3_id)
            events.append(
                DiffEvent(
                    event=WarLeftEvent(
                        d=date,
                        c=ck3_id,
                        p=_war_side_payload(prev_war, side=side),
                    ),
                    participants=_war_participants(prev_war, side),
                )
            )

    return events

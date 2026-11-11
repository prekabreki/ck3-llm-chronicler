"""Domicile, contract and construction deltas."""

from __future__ import annotations

from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.save.parse import CharacterSnapshot
from chronicler.schema import (
    BuildingCompletedEvent,
    BuildingCompletedPayload,
    ContractCompletedEvent,
    ContractPayload,
    DomicileMovedEvent,
    DomicilePayload,
)

_CONTRACT_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "invalidated"})


def _diff_contracts(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-621o (8aie slice 6): emit contract_completed
    when a tracked adventurer's contract transitions into a terminal
    status (``completed`` or ``invalidated``).

    Status lifecycle observed live (2026-05-17 Genji smoke):
        available -> accepted -> completed | invalidated

    Diff signal: for each contract_id owned by ck3_id (union of prev
    and curr reverse indexes), compare prev_status with curr_status:
        - prev non-terminal AND curr terminal -> emit
        - prev terminal -> skip (already emitted on the prior tick)
        - curr missing entirely is currently NOT emitted: contracts
          that disappear without ever surfacing a terminal status are
          most likely 'available' offers that expired silently — too
          low-signal to spend an event on. A future enhancement could
          treat ``accepted -> gone`` as an inferred completion if the
          live save shows this pattern.

    Skipped on dead chars (mirrors _diff_activities): death-tick
    cleanup may purge contract participation entries.
    """
    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_contracts = ctx.prev.task_contracts
    curr_contracts = ctx.curr.task_contracts
    prev_char_to_contracts = ctx.prev.character_to_contracts
    curr_char_to_contracts = ctx.curr.character_to_contracts
    date = ctx.date
    if char.is_dead:
        return []
    prev_ids = prev_char_to_contracts.get(ck3_id, frozenset())
    curr_ids = curr_char_to_contracts.get(ck3_id, frozenset())
    # Examine the union — a contract may transition into terminal in
    # curr without changing reverse-index membership (status flips but
    # owner stays the same).
    candidate_ids = prev_ids | curr_ids
    if not candidate_ids:
        return []
    events: list[DiffEvent] = []
    for contract_id in sorted(candidate_ids):
        curr_snap = curr_contracts.get(contract_id)
        if curr_snap is None:
            continue
        if curr_snap.status not in _CONTRACT_TERMINAL_STATUSES:
            continue
        prev_snap = prev_contracts.get(contract_id)
        if prev_snap is not None and prev_snap.status in _CONTRACT_TERMINAL_STATUSES:
            # Already terminal in prev — emitted on a prior tick (or
            # was terminal at baseline adoption). Don't re-emit.
            continue
        outcome: str = curr_snap.status  # type: ignore[assignment]
        events.append(
            DiffEvent(
                event=ContractCompletedEvent(
                    d=date,
                    c=ck3_id,
                    p=ContractPayload(
                        contract_type=curr_snap.contract_type,
                        name=curr_snap.name,
                        tier=curr_snap.tier,
                        employer_id=curr_snap.employer_id,
                        location_province_id=curr_snap.location_province_id,
                        outcome=outcome,  # type: ignore[arg-type]
                        acceptance_date=curr_snap.acceptance_date,
                        completion_date=curr_snap.completion_date,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _diff_domicile(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-r343 (8aie slice 5): emit domicile_moved when
    a tracked character's domicile's province_id changes between
    snapshots.

    Look up the domicile via character_to_domicile in BOTH prev and
    curr. If either side has no domicile, skip — the chronicler has
    no narrative beat for "first ever domicile" (game-start) or "lost
    your domicile" (rare; covered by death or government-transition
    events).

    Province changes from None to a value (or vice versa) are also
    skipped — those are usually parse anomalies (CK3 occasionally
    emits a malformed location field mid-recalc).

    Skipped on dead chars (mirrors _diff_activities).
    """
    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_domiciles = ctx.prev.domiciles
    curr_domiciles = ctx.curr.domiciles
    prev_char_to_domicile = ctx.prev.character_to_domicile
    curr_char_to_domicile = ctx.curr.character_to_domicile
    date = ctx.date
    if char.is_dead:
        return []
    prev_did = prev_char_to_domicile.get(ck3_id)
    curr_did = curr_char_to_domicile.get(ck3_id)
    if prev_did is None or curr_did is None:
        return []
    prev_dom = prev_domiciles.get(prev_did)
    curr_dom = curr_domiciles.get(curr_did)
    if prev_dom is None or curr_dom is None:
        return []
    if prev_dom.province_id is None or curr_dom.province_id is None:
        return []
    if prev_dom.province_id == curr_dom.province_id:
        return []
    return [
        DiffEvent(
            event=DomicileMovedEvent(
                d=date,
                c=ck3_id,
                p=DomicilePayload(
                    domicile_id=curr_dom.domicile_id,
                    domicile_type=curr_dom.domicile_type,
                    from_province_id=prev_dom.province_id,
                    to_province_id=curr_dom.province_id,
                ),
            ),
            participants=(),
        )
    ]


def _diff_constructions(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-2ur: emit ``building_completed`` when an in-flight
    construction the tracked character initiated has shipped.

    Per-character set-diff over ``character_to_constructions``: a
    (province_id, slot_index) pair that was in ``prev_char_to_cons[ck3_id]``
    but is NOT in ``curr_char_to_cons[ck3_id]`` left the in-flight set —
    the construction either completed or was cancelled. Disambiguate via
    ``curr_buildings_by_slot``: if the slot now carries the construction's
    ``building`` type, the building shipped. If not (slot still empty,
    or some other type — rare race where a different construction
    completed in the same slot first), treat as cancelled and skip.

    Skipped on dead chars by symmetry with :func:`_diff_epidemics` —
    while a dead character can't *start* construction, the engine's
    cleanup pass on death may pull a construction record while we're
    diffing across the death window. Skipping avoids false-positive
    completions on a deceased constructor.

    No ``building_started`` event in v1: the user's bd 2026-05-09
    framing is on the *completion* moment as the biographically
    load-bearing signal ('during his reign, the Hospices were built at
    Tartu'). Starts and cancellations are filed as v2 follow-ups."""
    ck3_id = ctx.cid
    char: CharacterSnapshot = ctx.curr_char
    prev_in_flight = ctx.prev.in_flight_constructions
    prev_char_to_cons = ctx.prev.character_to_constructions
    curr_char_to_cons = ctx.curr.character_to_constructions
    curr_buildings_by_slot = ctx.curr.holding_buildings_by_slot
    date = ctx.date
    if char.is_dead:
        return []
    prev_set = prev_char_to_cons.get(ck3_id, frozenset())
    curr_set = curr_char_to_cons.get(ck3_id, frozenset())
    departed = prev_set - curr_set
    if not departed:
        return []
    events: list[DiffEvent] = []
    for key in sorted(departed):
        cons = prev_in_flight.get(key)
        if cons is None:
            # Defensive — prev_set was built from prev_char_to_cons which
            # was built from prev.in_flight_constructions. Skip rather
            # than emit a payload missing its building name.
            continue
        slot_building = curr_buildings_by_slot.get(key)
        if slot_building != cons.building:
            # Cancelled (slot empty or different type) — skip in v1.
            continue
        events.append(
            DiffEvent(
                event=BuildingCompletedEvent(
                    d=date,
                    c=ck3_id,
                    p=BuildingCompletedPayload(
                        building=cons.building,
                        province_id=cons.province_id,
                        slot_index=cons.slot_index,
                        start_date=cons.start_date,
                    ),
                ),
                participants=(),
            )
        )
    return events

"""Character, family and character-state deltas."""

from __future__ import annotations

from chronicler.save.diff.context import DiffContext, DiffEvent
from chronicler.save.parse import CharacterSnapshot, MemorySnapshot
from chronicler.schema import (
    AdventurerEndedEvent,
    AdventurerStartedEvent,
    ConcubinePayload,
    ConcubineTakenEvent,
    CultureChangeEvent,
    CultureChangePayload,
    DeathEvent,
    DeathPayload,
    DecisionTakenEvent,
    DecisionTakenPayload,
    DivorceEvent,
    DivorcePayload,
    FaithChangeEvent,
    FaithChangePayload,
    GovernmentChangedEvent,
    GovernmentChangedPayload,
    HouseChangeEvent,
    HouseChangePayload,
    LifestyleCommittedEvent,
    LifestyleCommittedPayload,
    MarriageEvent,
    MarriagePayload,
    MiscarriageEvent,
    MiscarriagePayload,
    ModifierAcquiredEvent,
    ModifierPayload,
    NicknameEvent,
    NicknamePayload,
    PerkAcquiredEvent,
    PerkPayload,
    TraitGainedEvent,
    TraitLostEvent,
    TraitPayload,
    TravelEvent,
    TravelPayload,
    VanillaMemoryEvent,
    VanillaMemoryPayload,
)
from chronicler.schema.events import _EmptyPayload
from chronicler.util.dates import parse_ck3_date


def _diff_one_character(ctx: DiffContext) -> list[DiffEvent]:
    """Emit events for a single character's state delta.

    ``characters`` is the curr-snapshot's full character map; per-char
    code reaches into it for cross-references (e.g. resolving the
    deceased relative on a relative_died memory; ck3_chronicler-7jwu).
    ``new_memories`` is the precomputed newly-visible memory window
    (curr minus prev by memory_id) — diff_snapshots computes it once
    per pair and shares it with _diff_miscarriage (27ov.38).
    """
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    characters = ctx.curr.characters
    new_memories = ctx.new_memories
    events: list[DiffEvent] = []

    # 1. Death — newly dead in curr. Two signals can mark a death and they
    #    don't always move together: `is_dead` flips when CK3 moves the char
    #    into a dead collection, while `death_date` is populated from
    #    dead_data regardless of which collection holds the char. At
    #    game-exit CK3 can record the dying ruler with dead_data set (so
    #    death_date appears) while still listing them in `living`
    #    (is_dead=False). Emit on either newly-appearing signal so a
    #    death-at-exit still fires (ck3_chronicler-l8h2) — death_date is what
    #    the DB column and startup catch-up already trust. `_make_death`
    #    prefers death_date, so the payload is correct either way.
    #    ck3_chronicler-27ov.37 (audit M-D1): became_dead additionally
    #    requires prev.death_date to be unset — in the l8h2 sequence,
    #    tick N already fired via death_date_appeared (death_date set
    #    while still listed living) and tick N+1's is_dead flip must not
    #    fire a second DeathEvent. The DB UNIQUE absorbed the duplicate,
    #    but dedup-free consumers (per-tick tally, smoke tooling) counted
    #    the death twice.
    became_dead = not prev.is_dead and curr.is_dead and prev.death_date is None
    death_date_appeared = prev.death_date is None and curr.death_date is not None
    if became_dead or death_date_appeared:
        events.append(_make_death(curr, date))

    # 2. Travel — location changed (only for living chars; dead chars
    #    don't travel and a None→None or whatever-→None transition on
    #    death is noise we don't want)
    if (
        not curr.is_dead
        and prev.location_id is not None
        and curr.location_id is not None
        and prev.location_id != curr.location_id
    ):
        events.append(_make_travel(curr, prev.location_id, curr.location_id, date))

    # 3. Marriage — new spouse appeared
    new_spouses = set(curr.family.spouses) - set(prev.family.spouses)
    # primary_spouse counts too; but only emit one event per unique new ID.
    # ck3_chronicler-bcbx: don't fire when primary_spouse is just being
    # *promoted* from the existing spouses tuple — e.g. polygamist's
    # primary spouse dies and CK3 promotes a non-primary spouse to
    # primary. That's not a new marriage; it's bookkeeping.
    if (
        curr.family.primary_spouse
        and curr.family.primary_spouse != prev.family.primary_spouse
        and curr.family.primary_spouse not in prev.family.spouses
    ):
        new_spouses.add(curr.family.primary_spouse)
    for spouse_id in sorted(new_spouses):
        events.append(_make_marriage(curr, spouse_id, date))

    # 4. Divorce — spouse moved to former_spouses
    new_former = set(curr.family.former_spouses) - set(prev.family.former_spouses)
    for former_id in sorted(new_former):
        events.append(_make_divorce(curr, former_id, date))

    # 5. Nickname change — gained, lost, or replaced an epithet. Skipped
    #    on the death-snapshot since the change couldn't have happened
    #    between the moment of death and the next save; either it was
    #    caught earlier or it's CK3 retroactively writing data.
    if not curr.is_dead and prev.nickname != curr.nickname:
        events.append(_make_nickname(curr, prev.nickname, curr.nickname, date))

    # 6. Adventurer mode transitions — government type changing into or
    #    out of 'landless_adventurer_government' (Roads to Power DLC).
    #    Skipped on dead chars (death may clear landed_data).
    if not curr.is_dead and prev.government != curr.government:
        was_adv = prev.government == _LANDLESS_ADVENTURER_GOVERNMENT
        is_adv = curr.government == _LANDLESS_ADVENTURER_GOVERNMENT
        if not was_adv and is_adv:
            events.append(_make_adventurer_started(curr, date))
        elif was_adv and not is_adv:
            events.append(_make_adventurer_ended(curr, date))
        elif (
            not was_adv
            and not is_adv
            and prev.government is not None
            and curr.government is not None
        ):
            # ck3_chronicler-3v0s follow-up: non-adventurer government
            # transition (tribal→feudal, feudal→administrative, etc.).
            # Both branches above guard the adventurer-mode beats; this
            # branch covers everything else. None-on-either-side is
            # skipped because that's typically the engine clearing
            # landed_data on a non-landed character, not a regime shift.
            events.append(_make_government_changed(curr, prev.government, curr.government, date))

    # 7. Vanilla memories — new entries in the memory list (precomputed
    #    by diff_snapshots, see the docstring).
    for mem in new_memories:
        events.append(_make_vanilla_memory(curr, mem, characters, date))

    # 8. ck3_chronicler-jrwe: narratively interesting decisions taken
    #    since the previous tick. CK3 doesn't emit a vanilla memory for
    #    most decisions (the runestone-raise live signal is one
    #    confirmed example — the fp1 event chain has zero
    #    ``add_character_memory`` calls). Synthesise from the
    #    decision_cooldowns set delta, filtered through the
    #    _NARRATIVELY_INTERESTING_DECISIONS allowlist so administrative
    #    decisions don't drown the event log.
    # ck3_chronicler-70e8: key on (decision_id, end_date) so a re-take
    # that lengthens / resets the cooldown isn't silently absorbed as
    # "already taken". CK3 overwrites the cooldown entry on a re-take.
    prev_decisions = {(did, end) for did, end in prev.decisions_taken}
    for decision_id, end_date in curr.decisions_taken:
        if (decision_id, end_date) in prev_decisions:
            continue
        if decision_id not in _NARRATIVELY_INTERESTING_DECISIONS:
            continue
        events.append(_make_decision_taken(curr, decision_id, end_date, date))

    return events


# --- event constructors ---


def _make_death(c: CharacterSnapshot, date: str) -> DiffEvent:
    # The death_date from the snapshot is more accurate than the diff
    # window's current_date; prefer it.
    death_date = c.death_date or date
    # ck3_chronicler-caxv: cause + killer come from CharacterSnapshot's
    # death_cause / death_killer fields, which the parser pulls from
    # dead_data.reason / dead_data.killer. cause is engine-string form
    # ('death_old_age', 'death_battle', 'death_assassination', etc. —
    # 137 distinct values observed live); biography prompts can map
    # these to flavour copy. killer is a char id when CK3 records one
    # (battle deaths, executions, murders) — None for natural causes.
    participants = list(_family_participants(c))
    if c.death_killer is not None and not any(
        role == "killer" and pid == c.death_killer for role, pid in participants
    ):
        participants.append(("killer", c.death_killer))
    return DiffEvent(
        event=DeathEvent(
            d=death_date,
            c=c.ck3_id,
            p=DeathPayload(
                cause=c.death_cause,
                killer=c.death_killer,
                death_age=_compute_death_age(c.birth_date, death_date),
            ),
        ),
        participants=tuple(participants),
    )


def _compute_death_age(birth_date: str | None, death_date: str | None) -> int | None:
    """ck3_chronicler-caxv: deceased's age at death in whole years.

    Returns None when either date is missing or unparseable. Uses the
    short-form CK3 date (YYYY.M.D) → ISO normaliser, then falls back
    to a year-only subtraction if full-date parse fails (e.g. CK3
    occasionally writes 'AD 1066' without a month — defensive).
    """
    if not birth_date or not death_date:
        return None
    birth_iso = parse_ck3_date(birth_date)
    death_iso = parse_ck3_date(death_date)
    if birth_iso is None or death_iso is None:
        return None
    birth_year = int(birth_iso[:4])
    death_year = int(death_iso[:4])
    age = death_year - birth_year
    # If death MM-DD is before birth MM-DD, subtract one year — they
    # hadn't had their birthday yet that year.
    if death_iso[5:] < birth_iso[5:]:
        age -= 1
    return max(age, 0)


def _make_travel(c: CharacterSnapshot, from_loc: int, to_loc: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=TravelEvent(
            d=date,
            c=c.ck3_id,
            p=TravelPayload(from_location=from_loc, to_location=to_loc),
        ),
        participants=(),
    )


def _make_marriage(c: CharacterSnapshot, spouse_id: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=MarriageEvent(
            d=date,
            c=c.ck3_id,
            p=MarriagePayload(spouse_character_id=spouse_id),
        ),
        participants=(("spouse", spouse_id),),
    )


def _make_divorce(c: CharacterSnapshot, former_id: int, date: str) -> DiffEvent:
    return DiffEvent(
        event=DivorceEvent(
            d=date,
            c=c.ck3_id,
            p=DivorcePayload(former_spouse_character_id=former_id),
        ),
        participants=(("former_spouse", former_id),),
    )


def _diff_modifiers(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-n0s4: emit modifier_acquired when a new engine
    modifier key appears on the character's ``character_modifier``
    list. Acquired-only — modifier removals (event-modifier expiry,
    state-mod end) are out of scope; the noise/value ratio of expiry
    events is poor and the rest of the engine state usually carries
    the same information (e.g. a mourning modifier ends without
    needing a separate event when life moves on).

    Skipped on dead chars — the death tick may reshuffle modifier
    state as the engine cleans up. Same defensive guard as
    :func:`_diff_traits`."""
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    if curr.is_dead:
        return []
    new_mods = set(curr.modifiers) - set(prev.modifiers)
    if not new_mods:
        return []
    events: list[DiffEvent] = []
    for key in sorted(new_mods):
        events.append(
            DiffEvent(
                event=ModifierAcquiredEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=ModifierPayload(modifier_key=key),
                ),
                participants=(),
            )
        )
    return events


def _diff_perks(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-rgay: emit perk_acquired when a new lifestyle
    perk key appears on ``alive_data.perk``. Acquired-only — perk
    resets (full tree reroll, in-game refund decision) are structural
    enough to be out of scope here; they fall under their own
    decision-level signal if it surfaces narratively.

    Skipped on dead chars — the death tick may zero perk state as the
    engine cleans up. Same defensive guard as :func:`_diff_modifiers`."""
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    if curr.is_dead:
        return []
    new_perks = set(curr.perks) - set(prev.perks)
    if not new_perks:
        return []
    events: list[DiffEvent] = []
    for key in sorted(new_perks):
        events.append(
            DiffEvent(
                event=PerkAcquiredEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=PerkPayload(perk_key=key),
                ),
                participants=(),
            )
        )
    return events


def _diff_lifestyle_commits(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-9p2g: emit ``lifestyle_committed`` when a new
    perk's lifestyle is one the character had no representation in
    previously.

    Builds the set of lifestyles the character had a perk in BEFORE
    this tick, then for every new perk this tick checks whether its
    lifestyle (via :func:`lifestyle_of_perk`) is in that set. New
    lifestyles fire exactly one event each, sorted by lifestyle_key
    for deterministic replay; ``first_perk_key`` is the lex-smallest
    KNOWN perk in that new lifestyle from this tick.

    Unknown perks (not in the static lifestyle_perks map) are silently
    skipped — graceful degrade to "no event" until the map is extended.

    Skipped on dead chars — same defensive guard as :func:`_diff_perks`.
    """
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    if curr.is_dead:
        return []
    # Import here to avoid a circular-ish module dependency at top.
    from chronicler.save.lifestyle_perks import lifestyle_of_perk

    new_perks = set(curr.perks) - set(prev.perks)
    if not new_perks:
        return []
    prev_lifestyles = {ls for ls in (lifestyle_of_perk(p) for p in prev.perks) if ls is not None}
    # Group new perks by lifestyle, dropping unknowns.
    new_perks_by_lifestyle: dict[str, list[str]] = {}
    for perk in new_perks:
        ls = lifestyle_of_perk(perk)
        if ls is None or ls in prev_lifestyles:
            continue
        new_perks_by_lifestyle.setdefault(ls, []).append(perk)
    if not new_perks_by_lifestyle:
        return []
    events: list[DiffEvent] = []
    for lifestyle in sorted(new_perks_by_lifestyle.keys()):
        first_perk = sorted(new_perks_by_lifestyle[lifestyle])[0]
        events.append(
            DiffEvent(
                event=LifestyleCommittedEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=LifestyleCommittedPayload(
                        lifestyle_key=lifestyle,
                        first_perk_key=first_perk,
                    ),
                ),
                participants=(),
            )
        )
    return events


def _diff_concubines(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-gu7j: emit concubine_taken when a new id appears
    in family.concubines between snapshots. Taken-only — release on
    death is covered by the death event; manumission/repatriation is
    out of scope for v1. Skipped on dead chars defensively (engine
    pruning could re-shuffle relations on the death tick)."""
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    if curr.is_dead:
        return []
    characters = ctx.curr.characters
    new_concubines = set(curr.family.concubines) - set(prev.family.concubines)
    if not new_concubines:
        return []
    events: list[DiffEvent] = []
    for cid in sorted(new_concubines):
        partner = characters.get(cid)
        name = partner.first_name if partner is not None else None
        events.append(
            DiffEvent(
                event=ConcubineTakenEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=ConcubinePayload(
                        concubine_id=cid,
                        concubine_name=name,
                    ),
                ),
                participants=(("concubine", cid),),
            )
        )
    return events


def _diff_traits(ctx: DiffContext) -> list[DiffEvent]:
    """Emit TraitGainedEvent / TraitLostEvent for trait set deltas.

    Skipped on dead chars — death may clear or reshuffle traits in
    engine bookkeeping (haven't observed it but cheap defense).

    No noise filter at v1 — emit every trait transition. Some are
    obvious noise (educator-level changes once per character) but the
    user explicitly wanted narratively-named traits like 'faltering_heart'
    surfaced and a denylist may filter the wrong things. Defer noise
    control to a follow-up issue once prompt iteration shows what hurts.
    """
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    traits_lookup = ctx.curr.traits_lookup
    date = ctx.date
    if curr.is_dead:
        return []
    prev_set = set(prev.traits)
    curr_set = set(curr.traits)
    if prev_set == curr_set:
        return []
    events: list[DiffEvent] = []
    for trait_id in sorted(curr_set - prev_set):
        events.append(
            DiffEvent(
                event=TraitGainedEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=TraitPayload(
                        trait_id=trait_id,
                        trait_name=_lookup_trait_name(trait_id, traits_lookup),
                    ),
                ),
                participants=(),
            )
        )
    for trait_id in sorted(prev_set - curr_set):
        events.append(
            DiffEvent(
                event=TraitLostEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=TraitPayload(
                        trait_id=trait_id,
                        trait_name=_lookup_trait_name(trait_id, traits_lookup),
                    ),
                ),
                participants=(),
            )
        )
    return events


# ck3_chronicler-4pj: name of the CK3 trait CK3 attaches to a pregnant
# character. The integer id depends on the save's traits_lookup ordering
# (observed live as 102 in v09-smoke); we resolve by name at runtime so
# the detection survives different mods / DLC orderings.
_PREGNANT_TRAIT_NAME = "pregnant"


def _trait_id_by_name(name: str, traits_lookup: tuple[str, ...]) -> int | None:
    """Reverse-lookup helper: find a trait_id given the engine name.

    Returns None when the lookup table doesn't contain the name (mod
    drift, missing DLC, empty lookup). Linear scan is fine — the lookup
    fires at most once per character per diff and the table is bounded
    (~600 entries in vanilla)."""
    for idx, n in enumerate(traits_lookup):
        if n == name:
            return idx
    return None


def _diff_miscarriage(ctx: DiffContext) -> list[DiffEvent]:
    """ck3_chronicler-4pj: emit a miscarriage event when the ``pregnant``
    trait was lost without a corresponding ``child_born`` vanilla
    memory landing in the same diff window.

    Detection is set-membership based:

    - ``pregnant_trait_id`` is the integer trait id of ``pregnant``,
      resolved once per diff by :func:`diff_snapshots` (27ov.38). None
      means the save's lookup table doesn't include it (e.g.
      minimal-mod test fixtures); silently skip.
    - The trait must have been in ``prev.traits`` and not in
      ``curr.traits`` — i.e. the pregnancy ended this tick.
    - No newly-observed memory of type ``child_born`` for this
      character (``new_memories`` is the precomputed window shared
      with the vanilla_memory emission). CK3 emits the memory on
      successful birth, so its absence (combined with the trait loss)
      is the signal we use to distinguish miscarriage from normal
      birth.

    Skipped on dead chars — the engine clears traits at death, which
    would otherwise spuriously fire on women who happened to be
    pregnant when killed (their death is the dominant event we
    already capture via :func:`_make_death`).

    The payload's ``assumed_father_id`` is the character's
    ``family.primary_spouse`` — same heuristic CK3 uses for default
    paternity. None when there's no recorded primary spouse."""
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    if curr.is_dead:
        return []
    pregnant_trait_id = ctx.pregnant_trait_id
    if pregnant_trait_id is None:
        return []
    was_pregnant = pregnant_trait_id in prev.traits
    is_pregnant = pregnant_trait_id in curr.traits
    if not (was_pregnant and not is_pregnant):
        return []
    if any(m.memory_type == "child_born" for m in ctx.new_memories):
        return []
    return [
        DiffEvent(
            event=MiscarriageEvent(
                d=date,
                c=curr.ck3_id,
                p=MiscarriagePayload(
                    assumed_father_id=curr.family.primary_spouse,
                ),
            ),
            participants=(
                (("assumed_father", curr.family.primary_spouse),)
                if curr.family.primary_spouse is not None
                else ()
            ),
        )
    ]


def _diff_state_changes(ctx: DiffContext) -> list[DiffEvent]:
    """Emit HouseChangeEvent / CultureChangeEvent / FaithChangeEvent.

    Skipped on dead chars — engine bookkeeping may zero state on death
    (haven't seen it but cheap defense; same as nickname / adventurer
    diffs). Each event carries both numeric IDs (always present) and
    the resolved engine-key names from the lookup tables (None when
    the lookup didn't have an entry — usually means a fresh house ID
    that was just created and the snapshot doesn't index it, in
    which case the next save will fill it in).
    """
    prev = ctx.prev_char
    if prev is None:
        return []
    curr = ctx.curr_char
    date = ctx.date
    houses_lookup = ctx.curr.houses_lookup
    cultures_lookup = ctx.curr.cultures_lookup
    faiths_lookup = ctx.curr.faiths_lookup
    if curr.is_dead:
        return []
    events: list[DiffEvent] = []

    if prev.dynasty_house_id != curr.dynasty_house_id:
        events.append(
            DiffEvent(
                event=HouseChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=HouseChangePayload(
                        from_house_id=prev.dynasty_house_id,
                        from_house_name=(
                            houses_lookup.get(prev.dynasty_house_id)
                            if prev.dynasty_house_id is not None
                            else None
                        ),
                        to_house_id=curr.dynasty_house_id,
                        to_house_name=(
                            houses_lookup.get(curr.dynasty_house_id)
                            if curr.dynasty_house_id is not None
                            else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    if prev.culture_id != curr.culture_id:
        events.append(
            DiffEvent(
                event=CultureChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=CultureChangePayload(
                        from_culture_id=prev.culture_id,
                        from_culture_name=(
                            cultures_lookup.get(prev.culture_id)
                            if prev.culture_id is not None
                            else None
                        ),
                        to_culture_id=curr.culture_id,
                        to_culture_name=(
                            cultures_lookup.get(curr.culture_id)
                            if curr.culture_id is not None
                            else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    if prev.faith_id != curr.faith_id:
        events.append(
            DiffEvent(
                event=FaithChangeEvent(
                    d=date,
                    c=curr.ck3_id,
                    p=FaithChangePayload(
                        from_faith_id=prev.faith_id,
                        from_faith_name=(
                            faiths_lookup.get(prev.faith_id) if prev.faith_id is not None else None
                        ),
                        to_faith_id=curr.faith_id,
                        to_faith_name=(
                            faiths_lookup.get(curr.faith_id) if curr.faith_id is not None else None
                        ),
                    ),
                ),
                participants=(),
            )
        )

    return events


def _lookup_trait_name(trait_id: int, traits_lookup: tuple[str, ...]) -> str | None:
    """Resolve a trait_id to its name via the save's traits_lookup list.
    Returns None for out-of-range IDs (defensive — shouldn't happen on
    a well-formed save but the lookup may be empty if the snapshot was
    constructed without one)."""
    if 0 <= trait_id < len(traits_lookup):
        return traits_lookup[trait_id]
    return None


# ck3_chronicler-mcu: the canonical CK3 government string for Roads to
# Power's landless-adventurer state. Verified live against Ælla 12267's
# raw record — landed_data.government == this exact value.
_LANDLESS_ADVENTURER_GOVERNMENT = "landless_adventurer_government"


def _make_adventurer_started(c: CharacterSnapshot, date: str) -> DiffEvent:
    return DiffEvent(
        event=AdventurerStartedEvent(
            d=date,
            c=c.ck3_id,
            p=_EmptyPayload(),
        ),
        participants=(),
    )


def _make_adventurer_ended(c: CharacterSnapshot, date: str) -> DiffEvent:
    return DiffEvent(
        event=AdventurerEndedEvent(
            d=date,
            c=c.ck3_id,
            p=_EmptyPayload(),
        ),
        participants=(),
    )


def _make_government_changed(c: CharacterSnapshot, previous: str, new: str, date: str) -> DiffEvent:
    return DiffEvent(
        event=GovernmentChangedEvent(
            d=date,
            c=c.ck3_id,
            p=GovernmentChangedPayload(
                previous_government=previous,
                new_government=new,
            ),
        ),
        participants=(),
    )


def _make_nickname(
    c: CharacterSnapshot, from_nick: str | None, to_nick: str | None, date: str
) -> DiffEvent:
    return DiffEvent(
        event=NicknameEvent(
            d=date,
            c=c.ck3_id,
            p=NicknamePayload(from_nickname=from_nick, to_nickname=to_nick),
        ),
        participants=(),
    )


# ck3_chronicler-jrwe: decisions whose recently-taken transition the
# diff layer should surface as a synthesised :class:`DecisionTakenEvent`.
# Curated rather than open: most CK3 decisions (hold court, train for
# tournament, extract gold from treasury, author book, ...) are
# administrative noise that would drown the prose-relevant beats.
#
# Adding to this list:
# - The decision must produce a state change a biographer would weave
#   ("raised a runestone to his father") rather than a quality-of-life
#   action ("called his court").
# - Empirical signal preferred — observe the decision_id in a real save
#   before adding, since CK3 modders may rename or shadow built-in
#   decisions.
_NARRATIVELY_INTERESTING_DECISIONS: frozenset[str] = frozenset(
    {
        "raise_stele_decision",
    }
)


def _make_decision_taken(
    c: CharacterSnapshot,
    decision_id: str,
    cooldown_end_date: str,
    diff_date: str,
) -> DiffEvent:
    """Synthesise a DecisionTakenEvent for a freshly-observed entry in
    landed_data.decision_cooldowns. Date is the diff window's
    current_date — the actual taken-date is roughly cooldown_end_date
    minus the decision's cooldown duration; biographies should
    interpret the field as 'this was the recent past'."""
    return DiffEvent(
        event=DecisionTakenEvent(
            d=diff_date,
            c=c.ck3_id,
            p=DecisionTakenPayload(
                decision_id=decision_id,
                cooldown_end_date=cooldown_end_date or None,
            ),
        ),
        participants=(),
    )


def _make_vanilla_memory(
    c: CharacterSnapshot,
    mem: MemorySnapshot,
    characters: dict[int, CharacterSnapshot],
    diff_window_date: str,
) -> DiffEvent:
    """Emit a VanillaMemoryEvent for a newly-observed memory.

    Date is the memory's own creation_date (more accurate than the diff
    window). Participants are extracted from the memory record's
    participants dict.

    ck3_chronicler-05va: when creation_date is missing CK3 sometimes
    leaves it empty on engine-generated memories (founder-of-dynasty
    backfill, e.g.). Fall back to the diff window's date so the event
    has a non-empty date string; otherwise two distinct memories of the
    same memory_type for the same character on the same tick collapse
    to the same dedup key and one is silently dropped via the events
    UNIQUE constraint.

    ck3_chronicler-7jwu: for memory_type == "relative_died", look up the
    deceased's snapshot row and thread cause-of-death + age + first name
    into the payload so the surviving relative's biography can weave the
    death into prose. Silent no-op when the deceased has been pruned
    from the snapshot.

    ck3_chronicler-dn7a: when the deceased's record carries a
    death_killer (murder / execution / battle death), thread the
    killer's id + first_name through too. Resolves the killer's name
    via the same characters lookup; both fields stay None for
    natural-cause deaths and for the rare case where the killer has
    been pruned but the deceased hasn't.
    """
    participants_dict = dict(mem.participants)
    deceased_cause: str | None = None
    deceased_age: int | None = None
    deceased_first_name: str | None = None
    deceased_killer_id: int | None = None
    deceased_killer_first_name: str | None = None
    if mem.memory_type == "relative_died":
        dead_id = participants_dict.get("dead_relation")
        if dead_id is not None:
            deceased = characters.get(dead_id)
            if deceased is not None:
                deceased_cause = deceased.death_cause
                deceased_age = _compute_death_age(deceased.birth_date, deceased.death_date)
                deceased_first_name = deceased.first_name
                if deceased.death_killer is not None:
                    deceased_killer_id = deceased.death_killer
                    killer = characters.get(deceased.death_killer)
                    if killer is not None:
                        deceased_killer_first_name = killer.first_name
    return DiffEvent(
        event=VanillaMemoryEvent(
            d=mem.creation_date or diff_window_date,
            c=c.ck3_id,
            p=VanillaMemoryPayload(
                memory_type=mem.memory_type,
                end_date=mem.end_date,
                participants=participants_dict,
                deceased_cause=deceased_cause,
                deceased_age=deceased_age,
                deceased_first_name=deceased_first_name,
                deceased_killer_id=deceased_killer_id,
                deceased_killer_first_name=deceased_killer_first_name,
            ),
        ),
        participants=tuple(mem.participants),
    )


def _family_participants(c: CharacterSnapshot) -> tuple[tuple[str, int], ...]:
    """Pull family members as (role, char_id) tuples for inclusion in
    event_participants. Useful on death events so biographies can find
    the deceased's spouse, children, and parents from the DB."""
    out: list[tuple[str, int]] = []
    if c.family.mother is not None:
        out.append(("mother", c.family.mother))
    if c.family.father is not None:
        out.append(("father", c.family.father))
    if c.family.primary_spouse is not None:
        out.append(("primary_spouse", c.family.primary_spouse))
    for sp in c.family.spouses:
        out.append(("spouse", sp))
    for ch in c.family.children:
        out.append(("child", ch))
    return tuple(out)

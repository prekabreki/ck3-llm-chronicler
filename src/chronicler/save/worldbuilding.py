"""ck3_chronicler-8ek: world-context summariser for biography prompts.

Produces a structured fact dict describing a character's neighbourhood
at the moment a save was parsed: their realm, the peer kingdoms in
their de jure empire, and any cross-currents (holdings whose actual
realm differs from their de jure parent kingdom).

Slice 1 of 8ek: snapshot at death only. Lifetime-span deltas are filed
as 8ek-span. Output is JSON-serializable so it can be persisted on
the per-character row by save-tail and surfaced into the death-
biography prompt later, without re-parsing the save.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from chronicler.save.parse import (
    _PRIMARY_TIER_RANK as _TIER_RANK,
)
from chronicler.save.parse import (
    get_held_titles_sorted,
)

if TYPE_CHECKING:
    from chronicler.save.parse import SaveSnapshot, TitleSnapshot, WarSnapshot


# Cap to keep the biography prompt bounded — most de jure empires have
# 3–7 peer kingdoms in vanilla, but mods or unusual maps could explode
# the list. The cap orders by stable kingdom key, not by salience —
# we lean on the LLM to compose interestingly from whatever lands.
_MAX_PEERS = 8

# Cross-currents are typically rare (one or two per region) but in
# turbulent periods or modded saves can multiply. Cap defensively.
_MAX_CROSS_CURRENTS = 6


class RealmFacts(TypedDict):
    """Facts about one kingdom-tier realm — used for both self_realm
    and each peer entry. ``ruler_*`` resolves the kingdom holder's
    character data for the LLM's prose."""

    kingdom_key: str
    kingdom_name: str | None
    culture: str | None
    faith: str | None
    ruler_first_name: str | None
    ruler_nickname: str | None


class CrossCurrent(TypedDict):
    """One holding (typically duchy-tier) whose holder's primary
    kingdom differs from the holding's de jure parent kingdom — the
    "Norway owns Sjælland" flavour, surfaced as a structured fact.

    ``held_*`` is the holding itself. ``de_jure_*`` is the kingdom
    that *would* contain it on the engine's structural map.
    ``holder_realm_*`` is the kingdom the holder *actually* belongs
    to — i.e. their highest-tier title.
    """

    held_title_key: str
    held_title_name: str | None
    held_tier: str
    de_jure_kingdom_key: str
    de_jure_kingdom_name: str | None
    holder_realm_key: str
    holder_realm_name: str | None
    holder_first_name: str | None


class RegionSummary(TypedDict):
    """Top-level world-context structure persisted on a tracked
    character's row and surfaced into their death-biography prompt."""

    region_empire_key: str
    region_empire_name: str | None
    self_realm: RealmFacts
    peers: list[RealmFacts]
    cross_currents: list[CrossCurrent]


def _walk_de_jure_to_tier(
    snap: SaveSnapshot, title: TitleSnapshot, target_tier: str
) -> TitleSnapshot | None:
    """Walk de_jure_liege upward until a title of ``target_tier`` is
    reached. Returns None if the chain breaks before the tier is
    reached, or if ``title`` is already higher than the target."""
    target_rank = _TIER_RANK.get(target_tier, -1)
    if _TIER_RANK.get(title.tier, -1) > target_rank:
        return None
    seen: set[int] = set()
    current: TitleSnapshot | None = title
    while current is not None and current.title_id not in seen:
        if current.tier == target_tier:
            return current
        seen.add(current.title_id)
        if current.de_jure_liege_id is None:
            return None
        current = snap.titles.get(current.de_jure_liege_id)
    return None


def _de_jure_empire_of(snap: SaveSnapshot, title: TitleSnapshot) -> TitleSnapshot | None:
    return _walk_de_jure_to_tier(snap, title, "empire")


def _de_jure_kingdom_of(snap: SaveSnapshot, title: TitleSnapshot) -> TitleSnapshot | None:
    return _walk_de_jure_to_tier(snap, title, "kingdom")


def _primary_realm_of_character(snap: SaveSnapshot, character_id: int) -> TitleSnapshot | None:
    """The realm the character belongs to, expressed as a kingdom-tier
    (or empire-tier) :class:`TitleSnapshot`.

    For kingdom+/empire+ holders, this is their highest-tier held
    title. ck3_chronicler-95n7: for sub-kingdom holders (the typical
    1066 player — Danish duke, Catalan count, Barcelona vassal), we
    walk de_jure_liege upward from their highest-tier holding to find
    the de jure kingdom they sit inside. The "primary realm" is the
    realm they belong to, not necessarily a title they personally
    hold — Thrugot of Sjælland *belongs to* Denmark even though he
    doesn't hold k_denmark himself.

    ck3_chronicler-ggqn: for tracked non-player characters who hold no
    titles directly (spouses, courtiers, unlanded children of landed
    parents — Bodil sitting in Svend's court is the canonical case),
    fall back to inheriting the realm from a family relation:
    primary_spouse → father → mother. Without this fallback, every
    spouse and unlanded relative gets ``rsj=NULL`` and their death
    biographies have no region context to weave.

    Returns ``None`` only when the character holds no titles AND has
    no landed family ties (truly unrooted courtier with neither
    spouse nor parents in the snapshot), or when the de_jure_liege
    chain breaks before reaching kingdom tier (orphan titles, mod
    content, dynamic kingdoms with no de jure parent).

    Tiebreaker among same-tier holdings: the title with the smallest
    title_id (deterministic ordering across runs).

    Returned title is always tier kingdom or higher when non-None —
    callers downstream (summarise_region, _detect_cross_currents)
    rely on that invariant."""
    return _primary_realm_with_fallback(snap, character_id, _seen=set())


def _primary_realm_with_fallback(
    snap: SaveSnapshot,
    character_id: int,
    *,
    _seen: set[int],
) -> TitleSnapshot | None:
    if character_id in _seen:
        return None
    _seen.add(character_id)
    # ck3_chronicler-27ov.13 (audit H8): resolve via the title_holders
    # reverse index (with the empty-index fallback for hand-built test
    # snapshots) instead of a full O(titles) scan — this path runs per
    # tracked character per tick, per duchy holder, and per crusade
    # participant.
    held = get_held_titles_sorted(snap, character_id)
    if held:
        primary = held[0]
        if _TIER_RANK.get(primary.tier, -1) >= _TIER_RANK["kingdom"]:
            return primary
        # ck3_chronicler-95n7: sub-kingdom holder — walk to the de jure
        # kingdom of their highest-tier holding. Returns None when the
        # de_jure_liege chain doesn't reach kingdom tier.
        kingdom = _de_jure_kingdom_of(snap, primary)
        if kingdom is not None:
            return kingdom
        # Fall through to family fallback when the de_jure walk fails —
        # better to inherit from a parent than collapse to None for a
        # tracked relative.
    # ck3_chronicler-ggqn: unlanded (or orphan-titled) character —
    # inherit realm from family. Order: primary_spouse first (spouses
    # share their partner's realm by convention), then father, then
    # mother. Cycle-safe via _seen so a sibling marriage or other
    # circular family graph doesn't loop forever.
    char = snap.characters.get(character_id)
    if char is None:
        return None
    for relation_id in (
        char.family.primary_spouse,
        char.family.father,
        char.family.mother,
    ):
        if relation_id is None:
            continue
        relation_realm = _primary_realm_with_fallback(snap, relation_id, _seen=_seen)
        if relation_realm is not None:
            return relation_realm
    return None


def _realm_facts(snap: SaveSnapshot, kingdom: TitleSnapshot) -> RealmFacts:
    holder = snap.characters.get(kingdom.holder_id) if kingdom.holder_id is not None else None
    culture: str | None = None
    faith: str | None = None
    if holder is not None:
        if holder.culture_id is not None:
            culture = snap.cultures_lookup.get(holder.culture_id)
        if holder.faith_id is not None:
            faith = snap.faiths_lookup.get(holder.faith_id)
    return RealmFacts(
        kingdom_key=kingdom.key,
        kingdom_name=kingdom.name,
        culture=culture,
        faith=faith,
        ruler_first_name=holder.first_name if holder is not None else None,
        ruler_nickname=holder.nickname if holder is not None else None,
    )


def _detect_cross_currents(
    snap: SaveSnapshot,
    empire: TitleSnapshot,
    self_kingdom_id: int,
) -> list[CrossCurrent]:
    """Walk every duchy whose de jure parent chain leads to ``empire``,
    and flag those whose holder's primary realm differs from the
    duchy's de jure parent kingdom. Duchy granularity strikes the
    Sjælland flavour without flooding the prompt with county-level
    minutiae. Excludes the self-realm's own holdings — those aren't
    "cross-currents" from the character's perspective, just the natural
    state of their realm."""
    out: list[CrossCurrent] = []
    for title in snap.titles.values():
        if title.tier != "duchy":
            continue
        if title.holder_id is None:
            continue
        de_jure_kingdom = _de_jure_kingdom_of(snap, title)
        if de_jure_kingdom is None:
            continue
        de_jure_empire = _de_jure_empire_of(snap, de_jure_kingdom)
        if de_jure_empire is None or de_jure_empire.title_id != empire.title_id:
            continue
        holder_primary = _primary_realm_of_character(snap, title.holder_id)
        if holder_primary is None:
            continue
        if holder_primary.title_id == de_jure_kingdom.title_id:
            continue  # held by the rightful king — no cross-current
        if de_jure_kingdom.title_id == self_kingdom_id:
            # Foreign-held duchy *of the self realm* IS narratively
            # interesting from the self-character's perspective — keep.
            pass
        elif holder_primary.title_id == self_kingdom_id:
            # Self-realm holding a foreign duchy is also interesting.
            pass
        # Other cases (peer-A holds duchy-of-peer-B) are also surfaced;
        # the LLM gets the full lay of the land.
        holder = snap.characters.get(title.holder_id)
        out.append(
            CrossCurrent(
                held_title_key=title.key,
                held_title_name=title.name,
                held_tier=title.tier,
                de_jure_kingdom_key=de_jure_kingdom.key,
                de_jure_kingdom_name=de_jure_kingdom.name,
                holder_realm_key=holder_primary.key,
                holder_realm_name=holder_primary.name,
                holder_first_name=holder.first_name if holder is not None else None,
            )
        )
    out.sort(key=lambda c: c["held_title_key"])
    return out[:_MAX_CROSS_CURRENTS]


def summarise_region(snap: SaveSnapshot, character_id: int) -> RegionSummary | None:
    """ck3_chronicler-8ek: build a region-summary fact dict for the
    given character.

    Returns ``None`` when the character holds no titles at all, when
    the de_jure_liege chain from their holdings doesn't reach kingdom
    tier (orphan titles / mods), or when their resolved kingdom sits
    outside any de jure empire (early eras, mod content). The biography
    pipeline falls through to today's prompt in those cases.

    ck3_chronicler-95n7: sub-kingdom-tier characters (Danish dukes,
    Catalan counts, Barcelona vassals — the typical 1066 player class)
    are now resolved via :func:`_primary_realm_of_character`'s
    de_jure_kingdom walk. The "self_realm" is the kingdom they belong
    to even when they don't personally hold its capital.

    The output is JSON-serializable (TypedDict over plain values), so
    save-tail can persist it on the Character row and the biography
    pipeline can read it back without re-parsing the save."""
    primary = _primary_realm_of_character(snap, character_id)
    if primary is None:
        return None

    # If the character holds an empire directly, that *is* the region;
    # otherwise walk to the de jure empire from their kingdom.
    if primary.tier == "empire":
        empire: TitleSnapshot | None = primary
    else:
        empire = _de_jure_empire_of(snap, primary)
    if empire is None:
        return None

    # Self-realm — for an emperor, fall back to their first kingdom-
    # tier holding (they're "the king of X plus the emperor of Y" in
    # narrative voice). When no kingdom is held, use the empire title
    # itself as the realm — odd but legible.
    #
    # ck3_chronicler-hk9i: use the precomputed kingdoms_by_de_jure_empire
    # + title_holders indexes built at parse time. The previous full
    # snap.titles.values() scan ran twice per region summary (own
    # kingdoms + peers) which dominated worldbuilding cost on populous
    # bookmarks (5k-15k titles). Falls back to the legacy scan when the
    # index is empty for hand-constructed SaveSnapshot instances in
    # tests.
    kingdoms_in_empire = snap.kingdoms_by_de_jure_empire.get(empire.title_id, frozenset())
    self_kingdom: TitleSnapshot
    if primary.tier == "kingdom":
        self_kingdom = primary
    else:
        # Pick a kingdom held by this character within the empire.
        if kingdoms_in_empire and snap.title_holders:
            held = snap.title_holders.get(character_id, frozenset())
            own_kingdom_ids = sorted(held & kingdoms_in_empire)
            own_kingdoms = [snap.titles[tid] for tid in own_kingdom_ids]
        else:
            own_kingdoms = [
                t
                for t in snap.titles.values()
                if t.holder_id == character_id
                and t.tier == "kingdom"
                and (_de_jure_empire_of(snap, t) or empire).title_id == empire.title_id
            ]
            own_kingdoms.sort(key=lambda t: t.title_id)
        self_kingdom = own_kingdoms[0] if own_kingdoms else primary

    # Peer kingdoms: every kingdom whose de jure empire is this one,
    # excluding the self_kingdom. Stable ordered by key for
    # deterministic prompts.
    if kingdoms_in_empire:
        peers_titles = [
            snap.titles[tid]
            for tid in kingdoms_in_empire
            if tid != self_kingdom.title_id and tid in snap.titles
        ]
    else:
        peers_titles = []
        for t in snap.titles.values():
            if t.tier != "kingdom":
                continue
            if t.title_id == self_kingdom.title_id:
                continue
            kingdom_empire = _de_jure_empire_of(snap, t)
            if kingdom_empire is None or kingdom_empire.title_id != empire.title_id:
                continue
            peers_titles.append(t)
    peers_titles.sort(key=lambda t: t.key)
    peers_titles = peers_titles[:_MAX_PEERS]

    self_realm_facts = _realm_facts(snap, self_kingdom)
    # ck3_chronicler-081b: when the self-realm's kingdom holder is pruned
    # from the snapshot (typical for upstream rulers of a sub-kingdom-tier
    # player), culture/faith come back None and the briefing renders
    # "(unknown culture), (unknown faith)" — which the model reconciles
    # against the contradictory header by inventing a culture/faith change
    # at inheritance. The subject's own row is always present (the briefing
    # is built FOR them); fall back to their culture/faith for the self
    # realm so the prompt never carries a contradiction.
    subject = snap.characters.get(character_id)
    if subject is not None:
        if self_realm_facts["culture"] is None and subject.culture_id is not None:
            self_realm_facts["culture"] = snap.cultures_lookup.get(subject.culture_id)
        if self_realm_facts["faith"] is None and subject.faith_id is not None:
            self_realm_facts["faith"] = snap.faiths_lookup.get(subject.faith_id)

    return RegionSummary(
        region_empire_key=empire.key,
        region_empire_name=empire.name,
        self_realm=self_realm_facts,
        peers=[_realm_facts(snap, k) for k in peers_titles],
        cross_currents=_detect_cross_currents(snap, empire, self_kingdom.title_id),
    )


# ck3_chronicler-7md7: great-cause detection
#
# CK3 casus_belli_type strings empirically observed for great-cause wars.
# Live signal: 2026-05-08 Thrugot smoke produced a war_joined event with
# casus_belli_type=undirected_great_holy_war when Svend joined the First
# Crusade. Sibling shapes (directed_great_holy_war, papal_crusade) are
# documented in the design — extend this set when a real save shows a
# new value (jihads, great pagan invasions, modded great-causes).
_GREAT_CAUSE_CB_TYPES: frozenset[str] = frozenset(
    {
        "undirected_great_holy_war",
        "directed_great_holy_war",
        "papal_crusade",
    }
)

# Cap on number of allied sovereigns surfaced in the prompt — keeps the
# briefing bounded when a vanilla crusade pulls in dozens of Catholic
# kings. Order is deterministic (smallest character_id first) so prompt
# output stays stable across runs.
_MAX_GREAT_CAUSE_ALLIES = 5


class GreatCauseAlly(TypedDict):
    """One allied sovereign on the same side as the character in the
    great cause. Resolved from war.attacker_participants /
    defender_participants intersected with kingdom-tier holders."""

    character_id: int
    first_name: str | None
    realm_key: str
    realm_name: str | None


class GreatCauseFacts(TypedDict):
    """ck3_chronicler-7md7: structured world-context for a tracked
    character currently bound to a great cause (crusade / great holy
    war / papal crusade). Persisted on Character.great_cause_json by
    save-tail and surfaced into the biography briefing as a "Current
    great cause" block.

    ``side`` mirrors the WarSidePayload semantics: "attacker" or
    "defender" depending on which side the character fights on.
    ``target_kingdom_*`` resolves the war's first targeted_titles entry
    (the prize — Kingdom of Jerusalem in a vanilla First Crusade).
    ``allies`` are other top-tier sovereigns on the same side.

    Snapshot-derived: when the war disappears from active_wars (the
    next save-tail tick after war_concluded), the field auto-clears
    to None on the persisted row. No explicit lifecycle bookkeeping
    needed.
    """

    war_id: int
    casus_belli_type: str
    war_name: str | None
    side: str  # "attacker" | "defender" | "unknown"
    start_date: str | None
    target_kingdom_key: str | None
    target_kingdom_name: str | None
    allies: list[GreatCauseAlly]


def _resolve_side_for_character(war: WarSnapshot, character_id: int) -> str:
    if character_id in war.attacker_participants:
        return "attacker"
    if character_id in war.defender_participants:
        return "defender"
    return "unknown"


def _great_cause_target(snap: SaveSnapshot, war: WarSnapshot) -> tuple[str | None, str | None]:
    """Resolve the great cause's target kingdom from war.targeted_titles.

    Crusades have a single target (the contested kingdom — Jerusalem,
    Iberia, etc.). When CK3 records multiple titles the first id is the
    canonical one. Falls through to (None, None) when targeted_titles
    is empty (unusual but defensive)."""
    if not war.targeted_titles:
        return None, None
    target = snap.titles.get(war.targeted_titles[0])
    if target is None:
        return None, None
    # Walk to kingdom-tier when the target is sub-kingdom — crusades are
    # almost always recorded at kingdom tier directly, but mods or odd
    # CB shapes might target a duchy. Surface what we have.
    if target.tier == "kingdom" or target.tier == "empire":
        return target.key, target.name
    walked = _walk_de_jure_to_tier(snap, target, "kingdom")
    if walked is not None:
        return walked.key, walked.name
    return target.key, target.name


def _great_cause_allies(
    snap: SaveSnapshot,
    war: WarSnapshot,
    *,
    side: str,
    exclude_character_id: int,
) -> list[GreatCauseAlly]:
    """Pull other top-tier sovereigns on the same side. Filters down to
    kingdom+ realm holders — rank-and-file participants (knights, levy
    captains) aren't narratively useful for the briefing."""
    if side == "attacker":
        participants = war.attacker_participants
    elif side == "defender":
        participants = war.defender_participants
    else:
        return []
    allies: list[GreatCauseAlly] = []
    for cid in sorted(participants):  # deterministic ordering
        if cid == exclude_character_id:
            continue
        realm = _primary_realm_of_character(snap, cid)
        if realm is None:
            continue
        # Only surface kingdom+ holders — skip courtiers / minor vassals
        # whose primary "realm" resolves up via the de jure walk to
        # somebody else's kingdom.
        if _TIER_RANK.get(realm.tier, -1) < _TIER_RANK["kingdom"]:
            continue
        # Confirm this character actually holds the realm directly (not
        # inherited via family-fallback or de_jure walk) — otherwise
        # we'd surface every courtier of every allied king as a
        # separate "ally".
        if realm.holder_id != cid:
            continue
        char = snap.characters.get(cid)
        allies.append(
            GreatCauseAlly(
                character_id=cid,
                first_name=char.first_name if char is not None else None,
                realm_key=realm.key,
                realm_name=realm.name,
            )
        )
        if len(allies) >= _MAX_GREAT_CAUSE_ALLIES:
            break
    return allies


def detect_great_cause(snap: SaveSnapshot, character_id: int) -> GreatCauseFacts | None:
    """ck3_chronicler-7md7: when the character is currently a participant
    in a war whose casus_belli_type is in :data:`_GREAT_CAUSE_CB_TYPES`,
    build a :class:`GreatCauseFacts` payload. Returns None when the
    character is in no such war.

    Snapshot-derived: when the war concludes (CK3 deletes the war_id
    from active_wars on the next tick), this function naturally returns
    None and the persisted column clears. No diff layer hookup is
    needed — same lifecycle as :func:`summarise_region`.

    Multi-cause edge case (rare — overlapping crusades on the same
    character): returns the war with the smallest war_id for stable
    cross-run output. The other crusade is invisible to the prompt;
    file a follow-up if a real save shows this matters.
    """
    war_ids = snap.character_to_wars.get(character_id) or frozenset()
    if not war_ids:
        return None
    candidates: list[WarSnapshot] = []
    for war_id in war_ids:
        war = snap.wars.get(war_id)
        if war is None:
            continue
        if war.casus_belli_type in _GREAT_CAUSE_CB_TYPES:
            candidates.append(war)
    if not candidates:
        return None
    candidates.sort(key=lambda w: w.war_id)
    war = candidates[0]
    side = _resolve_side_for_character(war, character_id)
    target_key, target_name = _great_cause_target(snap, war)
    allies = _great_cause_allies(snap, war, side=side, exclude_character_id=character_id)
    return GreatCauseFacts(
        war_id=war.war_id,
        casus_belli_type=war.casus_belli_type or "",
        war_name=war.name,
        side=side,
        start_date=war.start_date,
        target_kingdom_key=target_key,
        target_kingdom_name=target_name,
        allies=allies,
    )

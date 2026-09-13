"""Natural-language rendering of event payloads for consolidation briefings.

ck3_chronicler-mayy (Phase 4 of the LLM pipeline audit). Previously
``memories._format_event_line`` inlined the raw JSON payload verbatim,
e.g.::

    [id=2322] [0884-05-01] trait_gained — 884.5.1;
        payload={"c":38137,"d":"884.5.1","p":{"trait_id":33,
        "trait_name":"gallant"},"t":"trait_gained","v":1}

The model had to do two things to use that line: parse JSON in its
head and then interpret it. The JSON-pinned format also repeated the
date, event type, and character id across every event, costing tokens
without giving the model anything it couldn't infer from a tight
sentence. This module converts each event into a single human-readable
fragment that's shorter, scannable, and matches the voice the
memory_consolidation prompt asks for.

Public surface: :func:`render_event_body` takes the event's ``type`` +
the inner ``p`` payload dict + a names_map and (optionally) a
titles_map and returns just the descriptive body. The caller adds the
``[id=N]`` + ISO-date prefix (those are load-bearing —
``trigger_event_id`` resolution depends on them).

Unknown event types fall back to the bare type name, deliberately
without the payload. If a new event type starts showing up in real
campaigns and the chronicler needs richer context, add a renderer
here — better than leaking JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

NamesMap = dict[int, str]
# ck3_chronicler-7t88: title_key (engine string) → current tier resolved
# from the latest snapshot's TitleSnapshot. Lets the title-event
# renderers override a stale payload tier='other' with the q1ai-recovered
# tier when a player-formed x_script_* title's de_jure_liege chain wires
# up after the title_created/relinquished event was already persisted.
TitlesMap = dict[str, str]


def build_titles_map_from_primary_title(
    primary_title_json: str | None,
) -> TitlesMap:
    """Decode ``Character.primary_title_json`` into a ``{key: tier}`` map.

    ck3_chronicler-7t88: the briefing layer feeds the result into
    :func:`render_event_body` via ``titles_map=`` so title-event
    renderers can override a stale payload ``tier='other'`` with the
    q1ai-recovered tier from the latest snapshot. Single-entry map
    (just the subject's current primary title) — enough for the
    common "player formed a kingdom and still holds it" case;
    characters who held an x_script kingdom briefly and then lost it
    are out of scope for this helper and would need a richer
    lookup (a follow-up if the gap surfaces in practice).

    Returns an empty map for ``None`` input, malformed JSON, or
    rows where ``key`` / ``tier`` are missing or non-string —
    matching the shape callers already expect from optional snapshot
    fields.
    """
    if not primary_title_json:
        return {}
    try:
        decoded = json.loads(primary_title_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    key = decoded.get("key")
    tier = decoded.get("tier")
    if not isinstance(key, str) or not isinstance(tier, str):
        return {}
    return {key: tier}


def _name(cid: Any, names_map: NamesMap) -> str:
    """Resolve a character id to a display name, with a stable fallback."""
    if cid is None:
        return "(unknown)"
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return str(cid)
    return names_map.get(cid_int, f"id {cid_int}")


def _participants_str(participants: Any, names_map: NamesMap) -> str:
    """Render a ``{role: id}`` or ``{role: [ids]}`` participant block.

    CK3 vanilla-memory payloads put role-keyed character refs under
    ``participants``. We emit ``role=name`` pairs, dropping the literal
    ``id`` fallback when no name is on file.
    """
    if not isinstance(participants, dict):
        return ""
    parts: list[str] = []
    for role, ref in sorted(participants.items()):
        if isinstance(ref, list):
            names = ", ".join(_name(r, names_map) for r in ref)
            parts.append(f"{role}={names}")
        else:
            parts.append(f"{role}={_name(ref, names_map)}")
    return ", ".join(parts)


def _resolve_tier(payload: dict, titles: TitlesMap) -> str:
    """Pick the best tier for a title event.

    ck3_chronicler-7t88: when CK3 first persists a player-decision-formed
    title (Norse 'Form Kingdom of X', custom-empire, etc.), the
    de_jure_liege chain isn't always wired yet. The parser's
    :func:`chronicler.save.parse._infer_tier_for_title` falls through to
    ``'other'`` (step 4), the diff layer copies that into the event
    payload, and the value freezes forever. Render time is the last
    chance to recover: the briefing builder rebuilds titles_map from
    the *current* snapshot every call, so the q1ai-recovered tier is
    available even when the payload's frozen value isn't.

    Override only when the payload tier is missing or ``'other'`` — a
    real tier in the payload represents what was true *at the time the
    event happened* and shouldn't be retconned. (E.g. a duchy title
    that later got demoted in CK3 should still render as 'duchy' for
    its original acquisition event.)
    """
    payload_tier = payload.get("tier")
    if payload_tier in (None, "", "other"):
        key = payload.get("title_key")
        if isinstance(key, str) and key in titles:
            return titles[key]
    return payload_tier or "?"


# ----- per-type renderers ---------------------------------------------------


def _r_travel(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    f = p.get("from_location")
    t = p.get("to_location")
    return f"travelled from province {f} to province {t}"


def _r_trait_gained(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    return f"gained trait '{p.get('trait_name', p.get('trait_id', '?'))}'"


def _r_trait_lost(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    return f"lost trait '{p.get('trait_name', p.get('trait_id', '?'))}'"


def _r_war_concluded(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    name = p.get("war_name", "(unnamed war)")
    side = p.get("side", "?")
    return f"war concluded: '{name}' (as {side})"


def _r_war_joined(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    name = p.get("war_name", "(unnamed war)")
    side = p.get("side", "?")
    return f"war joined: '{name}' (as {side})"


def _r_war_declared(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    name = p.get("war_name", "(unnamed war)")
    side = p.get("side", "?")
    return f"war declared: '{name}' (as {side})"


def _r_war_left(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: separate peace, side switch, or release from
    # the call to war. Mirrors war_joined / war_declared.
    name = p.get("war_name", "(unnamed war)")
    side = p.get("side", "?")
    return f"left war: '{name}' (as {side})"


def _r_title_acquired(p: dict, names: NamesMap, titles: TitlesMap) -> str:
    tier = _resolve_tier(p, titles)
    title_name = p.get("title_name") or p.get("title_key") or f"id {p.get('title_id')}"
    from_holder = _name(p.get("from_holder_id"), names) if p.get("from_holder_id") else None
    if from_holder:
        return f"acquired {tier} title '{title_name}' from {from_holder}"
    return f"acquired {tier} title '{title_name}'"


def _r_title_created(p: dict, _names: NamesMap, titles: TitlesMap) -> str:
    # ck3_chronicler-y9p3: decision-driven creation (custom kingdom,
    # empire formation, unify-petty-kingdom). "Founded" carries the
    # narrative weight the model needs to write "he founded the
    # Kingdom of X" rather than dropping the formation entirely.
    # ck3_chronicler-7t88: tier may be a stale 'other' for x_script_*
    # keys created before CK3 wired their de_jure chain — _resolve_tier
    # recovers from titles_map when present.
    tier = _resolve_tier(p, titles)
    title_name = p.get("title_name") or p.get("title_key") or f"id {p.get('title_id')}"
    return f"founded {tier} title '{title_name}'"


def _r_title_relinquished(p: dict, names: NamesMap, titles: TitlesMap) -> str:
    # ck3_chronicler-7t88 / x2cc: inverse of title_acquired. The
    # to_holder_id field is the new holder when known (None when CK3
    # destroyed the title outright or transferred it to an untracked
    # NPC). Same _resolve_tier fallback as title_created — at death,
    # a player-formed x_script kingdom relinquishes with the same
    # frozen tier='other' as its creation event.
    tier = _resolve_tier(p, titles)
    title_name = p.get("title_name") or p.get("title_key") or f"id {p.get('title_id')}"
    to_holder = _name(p.get("to_holder_id"), names) if p.get("to_holder_id") else None
    if to_holder:
        return f"relinquished {tier} title '{title_name}' to {to_holder}"
    return f"relinquished {tier} title '{title_name}'"


def _r_alliance_formed(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    return f"formed alliance with {_name(p.get('ally_character_id'), names)}"


def _r_alliance_broken(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    return f"broke alliance with {_name(p.get('ally_character_id'), names)}"


def _r_marriage(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    return f"married {_name(p.get('spouse_character_id'), names)}"


def _r_divorce(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: inverse of marriage. former_spouse_character_id
    # mirrors marriage's spouse_character_id. Defensive None handling for
    # the rare case where CK3 reports a divorce without preserving the
    # ex-spouse id in the snapshot.
    ex_id = p.get("former_spouse_character_id")
    if ex_id is None:
        return "divorced"
    return f"divorced from {_name(ex_id, names)}"


def _r_death(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    cause = p.get("cause", "?")
    age = p.get("death_age")
    if age is not None:
        return f"died (cause: {cause}, age: {age})"
    return f"died (cause: {cause})"


def _r_miscarriage(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    father = p.get("assumed_father_id")
    if father:
        return f"miscarriage (assumed father: {_name(father, names)})"
    return "miscarriage"


def _r_nickname(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    return f"received nickname '{p.get('to_nickname', '?')}'"


def _r_adventurer_started(_p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: Roads to Power landless-adventurer transition.
    # Empty payload — the narrative weight is the state change itself.
    return "became a landless adventurer"


def _r_adventurer_ended(_p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: inverse of adventurer_started — settled,
    # granted land, or conquered a holding. Empty payload.
    return "settled from landless adventuring"


# Map of canonical CK3 government IDs to short, biography-friendly
# labels. Unknown IDs fall through to a generic "{id}" phrasing so the
# event still renders something readable.
_GOVERNMENT_LABELS = {
    "tribal_government": "tribal",
    "feudal_government": "feudal",
    "clan_government": "clan",
    "administrative_government": "administrative",
    "republic_government": "republican",
    "theocracy_government": "theocratic",
    "nomadic_government": "nomadic",
    "mercenary_government": "mercenary",
    "holy_order_government": "holy-order",
    "landless_adventurer_government": "landless-adventurer",
}


def _gov_label(government_id: str) -> str:
    return _GOVERNMENT_LABELS.get(government_id, government_id)


def _r_government_changed(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    prev = _gov_label(p.get("previous_government", ""))
    new = _gov_label(p.get("new_government", ""))
    return f"adopted {new} government (from {prev})"


def _r_house_change(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: dynasty_house transition. Cadet-branch founding
    # is the canonical case. Names can be None when the house lookup
    # table didn't have an entry; fall back to a generic phrasing rather
    # than printing 'None'.
    from_name = p.get("from_house_name")
    to_name = p.get("to_house_name")
    if from_name and to_name:
        return f"changed house from '{from_name}' to '{to_name}'"
    if to_name:
        return f"joined house '{to_name}'"
    if from_name:
        return f"left house '{from_name}'"
    return "changed house"


def _r_culture_change(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: cultural conversion / hybrid-culture adoption.
    from_name = p.get("from_culture_name")
    to_name = p.get("to_culture_name")
    if to_name and from_name:
        return f"adopted {to_name} culture (from {from_name})"
    if to_name:
        return f"adopted {to_name} culture"
    return "culture changed"


def _r_faith_change(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: religious conversion.
    from_name = p.get("from_faith_name")
    to_name = p.get("to_faith_name")
    if to_name and from_name:
        return f"converted to {to_name} (from {from_name})"
    if to_name:
        return f"converted to {to_name}"
    return "faith changed"


def _r_decision_taken(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    return f"took decision '{p.get('decision_id', '?')}'"


def _r_building_completed(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    building = p.get("building", "?")
    province = p.get("province_id", "?")
    return f"completed building '{building}' in province {province}"


def _r_artifact_acquired(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    name = p.get("name", "?")
    rarity = p.get("rarity", "?")
    return f"acquired artifact '{name}' ({rarity})"


def _r_artifact_lost(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: inverse of artifact_acquired — gifted, stolen,
    # death-transferred, or destroyed. Shared payload, same field shape.
    name = p.get("name", "?")
    rarity = p.get("rarity", "?")
    return f"lost artifact '{name}' ({rarity})"


def _r_epidemic_outbreak(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-x2cc: tracked character newly infected. ``name`` is
    # the in-game display name (e.g. 'Pope Alexander's Boils'); fall back
    # to ``epidemic_type`` when only the engine string is known.
    name = p.get("name") or p.get("epidemic_type") or "an epidemic"
    intensity = p.get("intensity", "?")
    return f"fell ill with {name} ({intensity})"


def _r_dynasty_legacy_unlocked(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    return f"unlocked dynasty legacy '{p.get('legacy_key', '?')}'"


def _r_modifier_acquired(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-n0s4: a character_modifier engine key was added
    to the character. Renders the raw key — humanising the underscore
    form happens at the briefing/biography layer where context can
    pick the right tone (deity devotions read differently from
    event-granted mood mods)."""
    return f"acquired modifier '{p.get('modifier_key', '?')}'"


def _r_perk_acquired(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-rgay: a lifestyle perk was added to the
    character's alive_data.perk list. Same raw-key approach as
    modifier_acquired — the biography layer is responsible for
    mapping the perk back to its lifestyle tree and humanising
    the name."""
    return f"acquired perk '{p.get('perk_key', '?')}'"


def _r_lifestyle_committed(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-9p2g: first perk in a previously-empty lifestyle.
    Strips the ``_lifestyle`` suffix for prose ("martial_lifestyle" →
    "martial") so the rendered string reads naturally."""
    key = p.get("lifestyle_key", "?")
    label = key[: -len("_lifestyle")] if key.endswith("_lifestyle") else key
    return f"committed to the {label} lifestyle"


def _r_contract_completed(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-621o (8aie slice 6): an RtP adventurer contract
    just transitioned into a terminal status. Renders the display
    name + outcome verb + employer attribution when available.

    Examples:
      "completed the contract 'Perform in a Play' for Eadwine"
      "failed the contract 'Hobnob with Ruler' (tier 2)"
    """
    outcome = p.get("outcome", "?")
    verb = "completed" if outcome == "completed" else "failed"
    name = p.get("name") or p.get("contract_type") or "?"
    employer_id = p.get("employer_id")
    employer = _name(employer_id, names) if employer_id else None
    if employer and not employer.startswith("id "):
        return f"{verb} the contract '{name}' for {employer}"
    tier = p.get("tier")
    if tier is not None:
        return f"{verb} the contract '{name}' (tier {tier})"
    return f"{verb} the contract '{name}'"


def _humanise_court_position(key: str | None) -> str:
    """Turn an engine court_position key into a short readable phrase.

    Strips the conventional ``_court_position`` suffix and converts
    underscores to spaces. RtP camp-officer roles already drop the
    suffix in the engine — pass through unchanged in that case.

    Examples: ``"travel_leader_court_position"`` -> ``"travel leader"``,
    ``"second_camp_officer"`` -> ``"second camp officer"``.
    """
    if not key:
        return "an unknown role"
    stripped = key[: -len("_court_position")] if key.endswith("_court_position") else key
    return stripped.replace("_", " ")


def _r_camp_companion_joined(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-mke9 (8aie slice 7): a new court_positions row
    binds an employee to the tracked character's camp/court. Name
    pre-resolved on the payload by the diff layer (gu7j convention)."""
    eid = p.get("employee_id")
    name = p.get("employee_name") or _name(eid, names)
    role = _humanise_court_position(p.get("court_position"))
    return f"{name} joined the court as {role}"


def _r_camp_companion_left(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-mke9 (8aie slice 7): a court_positions row was
    removed for the tracked character's camp/court — companion
    departed, was dismissed, or died. Cause-of-departure not
    distinguishable from this diff alone; phrasing stays neutral."""
    eid = p.get("employee_id")
    name = p.get("employee_name") or _name(eid, names)
    role = _humanise_court_position(p.get("court_position"))
    return f"{name} left the court ({role})"


def _r_domicile_moved(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-r343 (8aie slice 5): the tracked character's
    domicile changed province. Province names aren't resolved here
    (province-name lookup is a separate index that adventurer-mode
    saves frequently churn through hundreds of provinces). The
    biography layer can map IDs to names if desired."""
    dom_type = p.get("domicile_type") or "domicile"
    src = p.get("from_province_id")
    dst = p.get("to_province_id")
    return f"moved {dom_type} from province {src} to province {dst}"


def _r_concubine_taken(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-gu7j: a new concubine on the character's
    family.concubine list. Prefer the payload-decoded name (resolved
    at diff time from the snapshot); fall back to the names map if
    the briefing layer has a fresher resolve, then to "id N"."""
    cid = p.get("concubine_id")
    name = p.get("concubine_name") or _name(cid, names)
    return f"took {name} as a concubine"


def _r_splendor_increased(p: dict, _names: NamesMap, _titles: TitlesMap) -> str:
    """ck3_chronicler-ei8t: dynasty crossed a splendor tier upward.
    Tier name table imported from save.dynasty_splendor so the curve
    and names stay coupled — adding a tier extends both together.
    Falls through to ``"tier N"`` for engine tiers beyond what we
    have a documented name for."""
    from chronicler.save.dynasty_splendor import SPLENDOR_TIER_NAMES

    new_tier = int(p.get("new_tier", 0))
    if 0 <= new_tier < len(SPLENDOR_TIER_NAMES):
        tier_label = SPLENDOR_TIER_NAMES[new_tier]
    else:
        tier_label = f"tier {new_tier}"
    name = p.get("dynasty_name") or f"Dynasty #{p.get('dynasty_id', '?')}"
    renown = int(p.get("total_renown_at_change", 0))
    return f"House {name} reached splendor level {tier_label} ({renown:,} renown)"


# ck3_chronicler-3abc: CK3 activity engine key → article+noun phrase for
# prose. Mirrors the _INSPIRATION_CRAFT precedent below. Only the common,
# narratively-central activities are mapped with an article; everything
# else (modded/RtP/future types) falls through to the prefix-strip path in
# _humanize_activity, which still reads fine without an article
# ("activity_adult_education" → "adult education").
_ACTIVITY_NOUN = {
    "activity_feast": "a feast",
    "activity_hunt": "a hunt",
    "activity_tournament": "a tournament",
    "activity_pilgrimage": "a pilgrimage",
    "activity_tour": "a tour",
    "activity_grand_tour": "a grand tour",
    "activity_chariot_race": "a chariot race",
    "activity_coronation": "a coronation",
    "activity_hold_court": "a court session",
}


def _humanize_activity(a_type: Any) -> str:
    """ck3_chronicler-3abc: turn a CK3 activity engine key into a noun
    phrase. Known types map to article+noun ("activity_feast" → "a feast");
    unmapped/modded types fall back to stripping the "activity_" prefix and
    the underscores so the raw engine key never leaks into prose
    ("activity_some_mod_thing" → "some mod thing"). Missing type → the
    generic "an activity"."""
    if not a_type:
        return "an activity"
    mapped = _ACTIVITY_NOUN.get(a_type)
    if mapped is not None:
        return mapped
    stripped = a_type[len("activity_") :] if a_type.startswith("activity_") else a_type
    return stripped.replace("_", " ")


def _r_activity_completed(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-qx7n (8aie slice 1): tournament/hunt/pilgrimage/
    # feast/hold-court ended. role distinguishes hosted vs attended;
    # host_id resolves to a name for the attendee path so prose reads
    # "attended X hosted by Y" rather than "id N". ck3_chronicler-3abc:
    # the activity_type is humanized into a noun phrase rather than
    # leaking the raw "activity_" engine key.
    noun = _humanize_activity(p.get("activity_type"))
    if p.get("role") == "host":
        return f"hosted {noun}"
    host_id = p.get("host_id")
    if host_id is not None:
        host = _name(host_id, names)
        if not host.startswith("id "):
            return f"attended {noun} hosted by {host}"
    return f"attended {noun}"


# Inspiration type → short engine-string-to-craft-noun for the renderer.
# Unknown engine strings fall through to a generic "inspiration" phrasing.
_INSPIRATION_CRAFT = {
    "weapon_inspiration": "weapon",
    "armor_inspiration": "armor",
    "smith_inspiration": "metalwork",
    "bow_inspiration": "bow",
    "weaver_inspiration": "textile",
    "book_inspiration": "book",
    "artisan_inspiration": "craft work",
    "adventure_inspiration": "adventure",
    "wonder_inspiration": "wonder",
}


def _r_inspiration_sponsored(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    # ck3_chronicler-m658 (8aie slice 2): tracked character committed
    # gold to fund an NPC artisan's inspiration. Phrase the artisan by
    # name when we can resolve it, fall back to "an artisan" otherwise.
    insp_type = p.get("inspiration_type") or ""
    craft = _INSPIRATION_CRAFT.get(insp_type, "inspiration")
    artisan_id = p.get("artisan_character_id")
    artisan_phrase = "an artisan"
    if artisan_id is not None:
        artisan_name = _name(artisan_id, names)
        if not artisan_name.startswith("id "):
            artisan_phrase = artisan_name
    cost = p.get("total_cost")
    cost_suffix = f" ({cost} gold)" if isinstance(cost, int) and cost > 0 else ""
    return f"sponsored {artisan_phrase} to make a {craft}{cost_suffix}"


# ----- vanilla_memory dispatch ----------------------------------------------

# Vanilla memories carry CK3's own narrative tags (married, became_friends,
# battle_won_memory, …). Most translate to short fragments; the long tail
# falls back to a generic shape so the model still sees the type.

_VANILLA_PHRASE: dict[str, str] = {
    "married": "vanilla memory: married",
    "ascended_throne_memory": "vanilla memory: ascended a throne",
    "battle_won_memory": "vanilla memory: won a battle",
    "battle_lost_memory": "vanilla memory: lost a battle",
    "war_won": "vanilla memory: won a war",
    "war_lost": "vanilla memory: lost a war",
    "offensive_war": "vanilla memory: fought an offensive war",
    "defensive_war": "vanilla memory: fought a defensive war",
    "joined_allys_war": "vanilla memory: joined an ally's war",
    "became_friends": "vanilla memory: became friends",
    "became_rivals": "vanilla memory: became rivals",
    "became_nemesis": "vanilla memory: became nemesis",
    "became_lovers": "vanilla memory: became lovers",
    "became_soulmates": "vanilla memory: became soulmates",
    "developed_crush": "vanilla memory: developed a crush",
    "relative_died": "vanilla memory: a relative died",
    "rival_died": "vanilla memory: a rival died",
    "child_born": "vanilla memory: had a child",
    "first_born": "vanilla memory: firstborn child",
    "twins_born": "vanilla memory: twins born",
    "child_premature": "vanilla memory: child born premature",
    "child_stillborn": "vanilla memory: child stillborn",
    "miscarriage": "vanilla memory: miscarriage",
    "imprisoned": "vanilla memory: imprisoned",
    "released_from_prison_memory": "vanilla memory: released from prison",
    "childhood_education_guardian": "vanilla memory: childhood with guardian",
    "ward_education_completed": "vanilla memory: ward education complete",
    "failed_provincial_exam_memory": "vanilla memory: failed provincial exam",
    # ck3_chronicler-9neo: high-frequency variants surfaced by the
    # 2026-05-17 adventurer-smoke memory audit (top 7 by count in a
    # live save). Long-tail variants beyond this batch keep falling
    # through to the generic "vanilla memory: {mt}" template.
    "lost_title_memory": "vanilla memory: lost a title",
    "passed_provincial_exam_memory": "vanilla memory: passed provincial exam",
    "ce1_contracted_epidemic": "vanilla memory: contracted an epidemic",
    "accolade_created": "vanilla memory: an accolade was created",
    "became_grudge": "vanilla memory: developed a grudge",
    "childhood_education_no_guardian": "vanilla memory: childhood without a guardian",
    "spouse_died": "vanilla memory: spouse died",
}


def _r_vanilla_memory(p: dict, names: NamesMap, _titles: TitlesMap) -> str:
    mt = p.get("memory_type", "?")
    base = _VANILLA_PHRASE.get(mt) or f"vanilla memory: {mt}"
    participants = _participants_str(p.get("participants"), names)
    if participants:
        return f"{base} ({participants})"
    return base


# ----- dispatch table -------------------------------------------------------

_RENDERERS: dict[str, Callable[[dict, NamesMap, TitlesMap], str]] = {
    "travel": _r_travel,
    "trait_gained": _r_trait_gained,
    "trait_lost": _r_trait_lost,
    "war_concluded": _r_war_concluded,
    "war_joined": _r_war_joined,
    "war_declared": _r_war_declared,
    "war_left": _r_war_left,
    "title_acquired": _r_title_acquired,
    "title_created": _r_title_created,
    "title_relinquished": _r_title_relinquished,
    "alliance_formed": _r_alliance_formed,
    "alliance_broken": _r_alliance_broken,
    "marriage": _r_marriage,
    "divorce": _r_divorce,
    "death": _r_death,
    "miscarriage": _r_miscarriage,
    "nickname": _r_nickname,
    "adventurer_started": _r_adventurer_started,
    "adventurer_ended": _r_adventurer_ended,
    "government_changed": _r_government_changed,
    "house_change": _r_house_change,
    "culture_change": _r_culture_change,
    "faith_change": _r_faith_change,
    "decision_taken": _r_decision_taken,
    "building_completed": _r_building_completed,
    "artifact_acquired": _r_artifact_acquired,
    "artifact_lost": _r_artifact_lost,
    "epidemic_outbreak": _r_epidemic_outbreak,
    "dynasty_legacy_unlocked": _r_dynasty_legacy_unlocked,
    "splendor_increased": _r_splendor_increased,
    "concubine_taken": _r_concubine_taken,
    "modifier_acquired": _r_modifier_acquired,
    "perk_acquired": _r_perk_acquired,
    "lifestyle_committed": _r_lifestyle_committed,
    "contract_completed": _r_contract_completed,
    "camp_companion_joined": _r_camp_companion_joined,
    "camp_companion_left": _r_camp_companion_left,
    "domicile_moved": _r_domicile_moved,
    "activity_completed": _r_activity_completed,
    "inspiration_sponsored": _r_inspiration_sponsored,
    "vanilla_memory": _r_vanilla_memory,
}


def render_event_body(
    event_type: str,
    payload: dict[str, Any],
    *,
    names_map: NamesMap,
    titles_map: TitlesMap | None = None,
) -> str:
    """Render the descriptive body of one event in natural language.

    The caller prepends the ``[id=N] [date]`` framing; this function
    returns just the human-readable middle. ``payload`` is the inner
    ``p`` dict from the persisted event JSON (already deserialised).
    Unknown event types fall back to the type name with underscores
    stripped (ck3_chronicler-2etd: engine keys are snake_case, so
    ``unknown_future_event`` reads as ``unknown future event`` rather
    than leaking a raw key into briefings / the export roll), and
    deliberately drop the payload rather than leaking JSON noise.

    ``titles_map`` (ck3_chronicler-7t88) is an optional ``{title_key:
    tier}`` lookup that title-event renderers consult to override a
    stale payload ``tier='other'``. Built by the briefing layer from
    the latest snapshot's primary_title_json; safe to omit (defaults
    to an empty dict) for callers that don't have title-state context.
    """
    renderer = _RENDERERS.get(event_type)
    if renderer is None:
        return event_type.replace("_", " ")
    try:
        return renderer(payload, names_map, titles_map or {})
    except Exception:  # pragma: no cover — defence in depth
        # If a payload doesn't match its expected shape (older save
        # version, partial data), fall back to the type rather than
        # bubbling the exception up into a consolidation pass.
        return event_type.replace("_", " ")

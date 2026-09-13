"""Pure prompt assembly for biography generation.

Everything in this module is side-effect free: it turns plain-data
structs (a :class:`CharacterSnapshot`, a list of :class:`EventSnapshot`,
and the name/relation lookups the read phase gathered) into the four
fields of a :class:`Briefing` — ``kind``, ``prompt_version``,
``system_prompt`` and ``user_prompt``. No database session, no provider,
no clock.

This is the half of the old ``narrative.pipeline`` that used to be fused
with the three-phase transaction orchestrator (ck3_chronicler-05rs).
Splitting it out means a biography template change (v3 → v5 →
world-context) edits *this* module only, and the prompt logic is
unit-testable on hand-built structs without a live DB or LLM.

The single composition entry point is :func:`build_briefing`. The
orchestrator (:func:`chronicler.narrative.pipeline.generate_biography`)
gathers a :class:`BiographyInputs` in its read phase and calls
``build_briefing(inputs, mode=..., include_raw_record=...)`` between the
read and the provider call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from chronicler.narrative.clusters import (
    detect_death_clusters,
    format_death_cluster_block,
)
from chronicler.narrative.provider import PromptKind

# Bumped v1 → v2 in ck3_chronicler-lv7: opening framing dropped the
# hard-coded "medieval Europe" anchor in favor of culture-aware voice.
# Bumped v2 → v3 in ck3_chronicler-8ek slice 1: optional world-context
# scene-setter at the top of the biography when save-tail resolved the
# character's de jure region (Character.region_summary_json non-NULL).
# v3 → v5 in ck3_chronicler-me4 slice 2: same world-context data, but
# threaded through the biography body via constraint-11 rewrite instead
# of a discrete top paragraph. Selected at generation time by
# chronicler.config.BIOGRAPHY_WORLDBUILDING_MODE. (v4 is occupied by
# the withdrawn ck3_chronicler-j7a experiment; v5 is the next free
# active-prompt slot.) Existing biography rows retain the older tags
# so we can tell which prompt produced which output.
# Issue #20: these used to be (version, path) pairs pointing at
# ``narrative/prompts/*.md``. Issue #19 stopped loading those files (every
# provider discarded the result), and the 0224 migration had already
# copied their bodies into the prose dir's ``voice/<kind>.md`` — verified
# byte-identical against prose-template/voice/ before the files were
# removed. So only the VERSION TAG survives, and it must: persisted
# ``Biography.prompt_template_version`` rows reference these strings, and
# they are how you tell which rules produced which biography on a
# regeneration. The rules themselves now live in the user's prose
# directory, where they are editable and actually reach the model.
_VERSION_BY_MODE: dict[str, str] = {
    "scene_setter": "biography_v3",
    "woven": "biography_v5",
}
# Default-mode version is re-exported from chronicler.narrative.
PROMPT_TEMPLATE_VERSION = _VERSION_BY_MODE["scene_setter"]


@dataclass(frozen=True, slots=True)
class CharacterSnapshot:
    """Plain-data view of a character for prompt construction.

    Detached from any SQLAlchemy session so it survives session close.
    """

    ck3_id: int
    first_name: str | None
    dynasty_name: str | None
    nickname: str | None
    female: bool | None
    birth_date: str | None
    death_date: str | None
    culture: str | None
    faith: str | None
    # ck3_chronicler-6ui: optional raw rakaly record for the character.
    # Populated by import-save and save-tail's tracked-character refresh
    # (with `dna` stripped). Surfaced in the user prompt only when the
    # caller passes ``include_raw_record=True`` to :func:`generate_biography`.
    raw_record_json: str | None = None
    # ck3_chronicler-8ek: structured world-context (RegionSummary)
    # persisted by save-tail at last refresh. None for landless / sub-
    # realm characters; falls through to today's prompt cleanly.
    region_summary_json: str | None = None
    # ck3_chronicler-7md7: structured "current great cause" facts when
    # the character is currently bound to a crusade / great holy war /
    # papal crusade. Snapshot-derived; clears when the war concludes.
    # None for the common case (no active great cause) — biography
    # pipeline omits the block.
    great_cause_json: str | None = None
    # ck3_chronicler-7t88: persisted ``{key, name, tier}`` of the
    # character's current primary title. Used at render time to
    # override a stale ``tier='other'`` on title-event payloads
    # (x_script_* keys whose de_jure chain wasn't wired when the diff
    # tick fired). None for landless characters / pre-7t88 rows.
    primary_title_json: str | None = None


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    id: int
    event_type: str
    event_date: str
    event_date_iso: str | None
    payload_json: str


@dataclass(frozen=True, slots=True)
class BiographyInputs:
    """Everything the prompt builder needs, gathered by the read phase.

    All plain data — no SQLAlchemy objects — so :func:`build_briefing`
    stays pure and testable. ``name_genders`` and ``related_snapshots``
    are the raw materials for relation-label inference; the label
    computation itself lives in :func:`build_briefing`, not in the read
    phase (ck3_chronicler-05rs: relation labels are prompt shaping).
    """

    character: CharacterSnapshot
    events: list[EventSnapshot]
    names_map: dict[int, str]
    name_genders: dict[int, bool | None]
    related_snapshots: dict[int, str]


@dataclass(frozen=True, slots=True)
class Briefing:
    """The assembled prompt, ready to wrap in a ``NarrativeRequest``.

    Issue #19 removed the ``system_prompt`` field: it was loaded from
    ``narrative/prompts/*.md`` and then discarded by every provider —
    the register that actually shaped the prose has lived in the prose
    dir since tbrm. The orchestrator now assembles the real system
    prompt (:func:`chronicler.narrative.prose_io.assemble_system_prompt`)
    at request-construction time, so this stays pure: no filesystem.
    """

    kind: PromptKind
    prompt_version: str
    user_prompt: str


def _resolve_prompt_version(mode: str) -> str:
    """Map a ``BIOGRAPHY_WORLDBUILDING_MODE`` value to its version tag.

    Unknown values fall back to the scene-setter (v3) tag — keeps a
    typo'd config from breaking generation. Issue #20 dropped the path
    half of the old ``(version, path)`` tuple along with the prompt files
    it pointed at.
    """
    return _VERSION_BY_MODE.get(mode, _VERSION_BY_MODE["scene_setter"])


def _build_relation_labels(
    save_snapshot_json: str | None,
    *,
    subject_id: int | None = None,
    subject_female: bool | None,
    name_genders: dict[int, bool | None] | None = None,
    related_snapshots: dict[int, str] | None = None,
) -> dict[int, str]:
    """ck3_chronicler-nwoc: extract a map of related-character-id →
    relation-label from the subject's persisted ck3_chronicler-60m
    {id, name}-resolved family_data.

    Same semantics as :func:`closing._format_relations`, scoped to
    one subject and emitted as a flat lookup so the briefing
    glossary can decorate each glossary line in place. Without this,
    the per-character briefing surfaces just "33186 = Thrugot, called
    the Timid"; the LLM has only events (alliance + inheritance) to
    interpret the connection and lands on cautious 'kinsman' even
    when the relation is father.

    Children are labelled "son"/"daughter" when ``name_genders`` knows
    the gender, else "child". Spouses are labelled "spouse" (current)
    or "former spouse" (terminated marriage). Parents render as
    "father" / "mother". Bare ``concubine`` and ``concubinist`` are
    surfaced as such — the briefing should distinguish them from
    formal marriage.

    ck3_chronicler-u0eu reverse-scan (2026-05-08): CK3 doesn't always
    record father/mother on a character's own ``family_data`` (live
    smoke evidence: Svend 36957's saved record listed only spouse +
    child, no parents). When ``related_snapshots`` is supplied —
    keyed by character id with each value a save_snapshot_json
    string for that related character — we scan their ``child``
    lists for ``subject_id`` and back-derive the parent relation.
    Gender of the parent (father vs mother) comes from
    ``name_genders``; defaults to "parent" if unknown.

    Returns an empty dict when the JSON is missing/malformed/lacks
    family_data and no reverse signal lands either."""
    del subject_female  # reserved; see docstring

    out: dict[int, str] = {}

    def _id_of(entry: object) -> int | None:
        if isinstance(entry, dict):
            cid = entry.get("id")
            return cid if isinstance(cid, int) else None
        if isinstance(entry, int):
            return entry
        return None

    def _set_if_unset(cid: int | None, label: str) -> None:
        if cid is None or cid in out:
            return
        out[cid] = label

    # Pass 1: subject's own family_data.
    family: dict | None = None
    if save_snapshot_json:
        try:
            record = json.loads(save_snapshot_json)
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict):
            f = record.get("family_data")
            if isinstance(f, dict):
                family = f

    if family is not None:
        _set_if_unset(_id_of(family.get("father")), "father")
        _set_if_unset(_id_of(family.get("mother")), "mother")
        primary_spouse_id = _id_of(family.get("primary_spouse"))
        _set_if_unset(primary_spouse_id, "spouse")
        for entry in family.get("spouse") or []:
            _set_if_unset(_id_of(entry), "spouse")
        for entry in family.get("former_spouses") or []:
            _set_if_unset(_id_of(entry), "former spouse")
        _set_if_unset(_id_of(family.get("concubinist")), "concubinist")
        for entry in family.get("concubine") or []:
            _set_if_unset(_id_of(entry), "concubine")
        for entry in family.get("betrothed") or []:
            _set_if_unset(_id_of(entry), "betrothed")
        for entry in family.get("child") or []:
            cid = _id_of(entry)
            if cid is None:
                continue
            gender_known = name_genders.get(cid) if name_genders is not None else None
            if gender_known is True:
                _set_if_unset(cid, "daughter")
            elif gender_known is False:
                _set_if_unset(cid, "son")
            else:
                _set_if_unset(cid, "child")

    # Pass 2: reverse-scan related snapshots for parents the subject's
    # own record didn't list. CK3's family_data is asymmetric — a
    # character may know its children without listing its parents.
    if related_snapshots and subject_id is not None:
        for related_cid, related_json in related_snapshots.items():
            if related_cid == subject_id or not related_json:
                continue
            try:
                related_rec = json.loads(related_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(related_rec, dict):
                continue
            related_family = related_rec.get("family_data")
            if not isinstance(related_family, dict):
                continue
            children = related_family.get("child") or []
            if not any(_id_of(entry) == subject_id for entry in children):
                continue
            # related_cid is a parent of the subject. Discriminate
            # father/mother by the related character's recorded gender.
            parent_gender = name_genders.get(related_cid) if name_genders is not None else None
            if parent_gender is True:
                _set_if_unset(related_cid, "mother")
            elif parent_gender is False:
                _set_if_unset(related_cid, "father")
            else:
                _set_if_unset(related_cid, "parent")

    return out


def _format_event_line(
    e: EventSnapshot,
    names_map: dict[int, str] | None = None,
    titles_map: dict[str, str] | None = None,
) -> str:
    """Render one event line for the biography briefing.

    ck3_chronicler-mayy (Phase 4 sweep): pre-fix this inlined the raw
    JSON payload verbatim. Now delegates to
    :func:`chronicler.narrative.event_rendering.render_event_body` for
    a natural-language description; matches the memory_consolidation
    path. Biographies don't carry an [id=N] prefix because the
    biography prompt has no equivalent to ``trigger_event_id`` — just
    the date + body.

    ``titles_map`` (ck3_chronicler-7t88) is the optional ``{key: tier}``
    override consulted by title-event renderers; safe to omit for
    legacy call sites and tests that don't thread it.
    """
    from chronicler.narrative.event_rendering import render_event_body

    iso = e.event_date_iso or "????-??-??"
    payload: dict = {}
    if e.payload_json:
        try:
            outer = json.loads(e.payload_json)
            if isinstance(outer, dict):
                inner = outer.get("p")
                if isinstance(inner, dict):
                    payload = inner
        except json.JSONDecodeError:
            payload = {}
    body = render_event_body(
        e.event_type,
        payload,
        names_map=names_map or {},
        titles_map=titles_map,
    )
    return f"[{iso}] {body}"


def _format_world_context_block(
    region_summary_json: str | None,
    *,
    subject_culture: str | None = None,
    subject_faith: str | None = None,
) -> str:
    """ck3_chronicler-8ek: render the persisted RegionSummary JSON as a
    plain-text "World context" block for the biography prompt. Returns
    empty string when the JSON is missing or malformed — caller composes
    the rest of the user prompt unchanged, and prompt rule 11 in
    biography_v3 falls through to a v2-style opening.

    Format mirrors the structure described in the spec
    (``2026-05-04-8ek-...``): self realm + peers + cross-currents as
    bullet lists with explicit "use names exactly as given" instruction.
    Same shape as 2wv's hardened closing-chronicle prompt — the LLM
    receives structured truth, not free invitation to invent.

    ck3_chronicler-081b layered fallback (2026-05-08, u0eu follow-on):
    when the persisted self_realm.culture/faith are None — typically
    because the campaign was sealed before the summarise_region
    fallback shipped, so its region_summary_json is frozen at the
    pre-fix Nones — fall back to the subject character's own
    ``culture``/``faith`` strings. Without this, the rendered block
    says just "Self: Denmark." (no qualifier), and the model
    interprets the gap as evidence the subject "took on" a culture
    or faith at inheritance. Passing the subject's culture/faith
    through closes that loop offline, without depending on a
    save-tail re-tick to refresh the persisted JSON."""
    if not region_summary_json:
        return ""

    try:
        summary = json.loads(region_summary_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(summary, dict):
        return ""

    region_name = summary.get("region_empire_name") or summary.get("region_empire_key")
    if not region_name:
        return ""

    self_realm = summary.get("self_realm") or {}
    peers = summary.get("peers") or []
    cross_currents = summary.get("cross_currents") or []

    lines: list[str] = []
    lines.append(f"World context ({region_name}, at the time of his/her death):")
    lines.append("")

    if self_realm:
        self_name = self_realm.get("kingdom_name") or self_realm.get("kingdom_key") or "(unknown)"
        culture = self_realm.get("culture") or subject_culture
        faith = self_realm.get("faith") or subject_faith
        # ck3_chronicler-081b: rather than render "(unknown culture),
        # (unknown faith)" — which the model reconciles against the
        # rest of the briefing by fabricating a culture/faith change —
        # omit the culture+faith segment when they're missing, or skip
        # the whole Self line if even the realm name didn't resolve.
        # The subject's own culture/faith are still in the briefing
        # header; absence is silence, not contradiction.
        if culture and faith:
            lines.append(f"Self: {self_name} — {culture}, {faith}.")
        elif culture or faith:
            known = culture or faith
            lines.append(f"Self: {self_name} — {known}.")
        else:
            lines.append(f"Self: {self_name}.")

    if peers:
        lines.append("")
        lines.append("Other realms in the region:")
        for peer in peers:
            name = peer.get("kingdom_name") or peer.get("kingdom_key") or "(unknown)"
            culture = peer.get("culture") or "(unknown culture)"
            faith = peer.get("faith") or "(unknown faith)"
            ruler = peer.get("ruler_first_name")
            if peer.get("ruler_nickname"):
                ruler = f"{ruler}, called {peer.get('ruler_nickname')}"
            ruler_segment = f", ruled by {ruler}" if ruler else ""
            lines.append(f"- {name} — {culture}, {faith}{ruler_segment}")

    if cross_currents:
        lines.append("")
        lines.append("Cross-currents in the region (treat as factual; do not invent or alter):")
        for cc in cross_currents:
            held = cc.get("held_title_name") or cc.get("held_title_key") or "(unknown)"
            de_jure = cc.get("de_jure_kingdom_name") or cc.get("de_jure_kingdom_key") or "(unknown)"
            holder_realm = cc.get("holder_realm_name") or cc.get("holder_realm_key") or "(unknown)"
            holder = cc.get("holder_first_name")
            ruler_segment = f" {holder}, " if holder else " "
            lines.append(
                f"- {held} (de jure of {de_jure}) is held by{ruler_segment}"
                f"whose realm is {holder_realm}"
            )

    lines.append("")
    lines.append(
        "Open the biography with one short scene-setter paragraph (3-5 sentences) "
        "framing the character's neighbourhood from these facts. Use the kingdom "
        "and ruler names exactly as given. Do not invent additional realms, "
        "rulers, or holdings. After the paragraph, continue with the biography proper."
    )

    return "\n".join(lines)


def _format_great_cause_block(
    great_cause_json: str | None,
    *,
    death_date: str | None = None,
) -> str:
    """ck3_chronicler-7md7: render the persisted GreatCauseFacts JSON
    as a great-cause block for the biography briefing. Returns empty
    string when the JSON is missing/malformed/empty — callers compose
    the rest of the prompt unchanged and the LLM sees no great-cause
    language unless one is genuinely active.

    ck3_chronicler-85mk (2026-05-09): when ``death_date`` is non-null
    AND great_cause_json is set, the headline + closing instruction
    shift from "Current great cause" framing to a "died bound to this
    great cause" framing. The biography pipeline runs both on living
    characters (current cause is genuinely current) and on dead ones
    (great_cause_json was preserved at death, since
    ``_CHARACTER_NULLABLE_ON_UPDATE_FIELDS`` only auto-clears it on
    war_concluded — death without conclusion keeps the row). The
    different framing tells the LLM to frame the death scene with the
    cause context ("fell while still bound for Antioch", not
    "currently riding for the cause").

    Surfaces the war's name, target kingdom, side, start date, and any
    known kingdom-tier allies. Names are passed verbatim (the model is
    explicitly told to use them as given) — same convention as the
    world-context block to anchor prose against fabrication."""
    if not great_cause_json:
        return ""

    try:
        facts = json.loads(great_cause_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(facts, dict):
        return ""

    cb_type = facts.get("casus_belli_type") or "great cause"
    war_name = facts.get("war_name")
    target = facts.get("target_kingdom_name") or facts.get("target_kingdom_key")
    side = facts.get("side") or "unknown"
    start_date = facts.get("start_date")
    allies = facts.get("allies") or []

    lines: list[str] = []
    if death_date:
        # 85mk: dead-with-active-cause framing. The cause did not
        # complete in this character's lifetime — that's the narrative
        # hook. Closing instruction explicitly asks the LLM to frame
        # the death scene against the cause.
        headline = (
            f"Great cause active at death ({cb_type}; treat as factual, do not invent details):"
        )
    else:
        headline = f"Current great cause ({cb_type}; treat as factual, do not invent details):"
    lines.append(headline)
    lines.append("")

    if war_name:
        lines.append(f"- Cause: {war_name}")
    if target:
        lines.append(f"- Target: {target}")
    lines.append(f"- Side: {side}")
    if start_date:
        lines.append(f"- Joined: {start_date}")
    if death_date:
        lines.append(f"- Died: {death_date} (cause unresolved at this character's death)")

    if allies:
        lines.append("- Other sovereigns on the same side (use names exactly as given):")
        for ally in allies:
            ally_name = ally.get("first_name") or "(unnamed)"
            ally_realm = ally.get("realm_name") or ally.get("realm_key") or "(unknown realm)"
            lines.append(f"  - {ally_name} of {ally_realm}")

    lines.append("")
    if death_date:
        lines.append(
            "This character died while bound to the cause above. Frame the death "
            "biography with that fact — they did not see the cause through. Weave "
            "the cause name and target verbatim when describing their final years "
            "or the death scene; do not name a pope, holy site, treaty, or battle "
            "unless the events below already do."
        )
    else:
        lines.append(
            "When biographical events touch this great cause, weave the cause name and "
            "target verbatim. Do not name a pope, holy site, or treaty unless the events "
            "below already do."
        )

    return "\n".join(lines)


def _build_user_prompt(
    c: CharacterSnapshot,
    events: list[EventSnapshot],
    names_map: dict[int, str],
    *,
    include_raw_record: bool = False,
    relation_labels: dict[int, str] | None = None,
    world_block_text: str = "",
) -> str:
    """Assemble the user-content portion of the prompt.

    The character's Internal ID is intentionally NOT included — the v1
    biography draft showed the LLM treating "Character 36715" as if it
    were a name when first_name was null. The ID is only useful for our
    internal bookkeeping; it has no narrative meaning to the chronicler.

    ``names_map`` resolves character IDs that appear in event payloads
    to readable names (with epithets). Same mechanism as the
    consolidator (V05-N02 prompt v3): without it, the model copies raw
    IDs from event payloads into prose ("married 38379"). With it, the
    LLM gets a glossary it can draw on for actual names like
    "Beorhtgyth" and "Ivar the Boneless".
    """
    name = " ".join(part for part in [c.first_name, c.dynasty_name] if part)
    subject = name or "(unknown)"
    if c.nickname:
        subject = f"{subject}, called {c.nickname}"
    header_lines = []
    header_lines.append(f"Known names: {subject}")
    if c.female is not None:
        # ck3_chronicler-30s: spell gender out as a word so the model
        # doesn't pattern-match pronouns from spouse names ("Beorhtgyth"
        # → 'she/her') or events like 'had_sex' / 'child_born'. Omit the
        # line when unknown rather than emitting "(unknown)" — that
        # invites the model to flag the gap in prose.
        header_lines.append(f"Gender: {'woman' if c.female else 'man'}")
    header_lines.append(f"Birth date (if known): {c.birth_date or '(unknown)'}")
    header_lines.append(f"Death date (if known): {c.death_date or '(unknown)'}")
    header_lines.append(f"Culture: {c.culture or '(unknown)'}; Faith: {c.faith or '(unknown)'}")
    header = "\n".join(header_lines)

    # ck3_chronicler-nwoc: decorate each glossary line with a relation
    # label when the subject's family_data names this character. Without
    # it, biographies fell back to "kinsman" / "ally" instead of
    # "father" / "spouse" — a load-bearing factual gap surfaced by the
    # tbrm.5 marquee smoke. Same shape as closing.py's _format_relations,
    # scoped to one subject so the briefing line stays in-place.
    relation_labels = relation_labels or {}
    if names_map:
        glossary_entries = sorted(
            (cid, label) for cid, label in names_map.items() if cid != c.ck3_id
        )
        rendered = []
        for cid, label in glossary_entries:
            relation = relation_labels.get(cid)
            line = f"  {cid} = {label}"
            if relation:
                line = f"{line} ({relation})"
            rendered.append(line)
        glossary = "\n".join(rendered) if rendered else "(none)"
    else:
        glossary = "(none)"
    glossary_block = (
        "Other characters referenced in this person's events "
        f"(use these names, never the IDs):\n{glossary}"
    )

    # ck3_chronicler-6ui: optional raw character record. Opt-in because
    # the JSON blob can be ~5-15 KB per character and adds materially to
    # the prompt token cost on a 14B local model. When enabled, the LLM
    # gets fields the structured parser doesn't extract (culture-specific
    # data, adventurer state, title hierarchies, etc.).
    #
    # ck3_chronicler-60m: explicit precedence clause prevents the model
    # from trusting the raw record's first_name / female / nickname over
    # the resolved structured header — a failure mode observed in the
    # 2026-05-02 live A/B against Ælla 12267 where include_raw_record
    # caused regressions to "E_lla" + they/their pronouns.
    raw_record_block = ""
    if include_raw_record and c.raw_record_json:
        raw_record_block = (
            "\n\nFull character record from save (source of additional detail "
            "— traits, perks, adventurer state, location history, intrigue "
            "context). The structured header above (Known names, Gender, "
            "Birth, Death, Culture, Faith) takes precedence over any "
            "conflicting field in the raw record; use the raw record only "
            "for fields the header doesn't cover. Family relationships in "
            "the raw record are expanded to {id, name} dicts — write the "
            "name, never the id:\n"
            f"{c.raw_record_json}"
        )

    # ck3_chronicler-8ek: world-context scene-setter block, rendered by
    # build_briefing (which also derives the woven/plain kind from it —
    # 27ov.77 L11). Empty string when region_summary_json is missing or
    # malformed; biography_v3 prompt falls through to v2-style opening.
    world_block = f"{world_block_text}\n\n" if world_block_text else ""

    # ck3_chronicler-7md7: current great cause block — surfaces only
    # when the character is currently bound to a crusade / great holy
    # war / papal crusade. Composes after the world-context block so
    # the LLM sees the broad neighbourhood first, then the specific
    # cause they are riding for.
    # ck3_chronicler-85mk (2026-05-09): pass death_date so the block
    # switches headline + closing instruction when the character died
    # with the cause still active — death-on-great-cause framing.
    great_cause_text = _format_great_cause_block(
        c.great_cause_json,
        death_date=c.death_date,
    )
    great_cause_block = f"{great_cause_text}\n\n" if great_cause_text else ""

    if not events:
        return (
            f"{world_block}{great_cause_block}{header}\n\n{glossary_block}\n\n"
            f"Recorded events: (none){raw_record_block}"
        )
    # ck3_chronicler-7t88: build a {title_key: tier} override map from
    # the subject's current primary title so the title-event renderers
    # recover the q1ai-inferred tier on x_script_* events whose payload
    # froze on 'other'. See event_rendering._resolve_tier.
    from chronicler.narrative.event_rendering import build_titles_map_from_primary_title

    titles_map = build_titles_map_from_primary_title(c.primary_title_json)
    lines = [_format_event_line(e, names_map, titles_map) for e in events]
    cluster_block_text = format_death_cluster_block(detect_death_clusters(events))
    cluster_suffix = f"\n\n{cluster_block_text}" if cluster_block_text else ""
    return (
        f"{world_block}{great_cause_block}{header}\n\n{glossary_block}\n\n"
        "Recorded events (chronological):\n" + "\n".join(lines) + cluster_suffix + raw_record_block
    )


def build_briefing(
    inputs: BiographyInputs,
    *,
    mode: str,
    include_raw_record: bool = False,
) -> Briefing:
    """Compose a complete :class:`Briefing` from read-phase inputs.

    Pure: no DB, no provider, no clock. This is the seam the
    orchestrator calls between its read and generate phases.

    ``mode`` is the active ``BIOGRAPHY_WORLDBUILDING_MODE``; it selects
    the prompt template version + path via :func:`_resolve_prompt`.

    ``kind`` is ``"biography_woven"`` when the character carries a
    renderable world-context block, else ``"biography"`` — this still
    gates prompt rendering even though, post-tbrm.3, it no longer drives
    provider routing (single ClaudeCodeProvider). See
    ck3_chronicler-erx.

    Relation labels are inferred here (not in the read phase) — they are
    prompt shaping. The read phase only gathers the raw materials
    (``name_genders`` + ``related_snapshots``).
    """
    c = inputs.character

    # ck3_chronicler-nwoc + u0eu: relation labels from the subject's own
    # family_data, with a reverse-scan over related snapshots for parents
    # the subject's record omits.
    relation_labels = _build_relation_labels(
        c.raw_record_json,
        subject_id=c.ck3_id,
        subject_female=c.female,
        name_genders=inputs.name_genders,
        related_snapshots=inputs.related_snapshots,
    )

    # ck3_chronicler-erx: tag the request as "biography_woven" when 8ek
    # world-context data renders, so prompt-version selection picks the
    # woven template. ck3_chronicler-27ov.77 (L11): rendered exactly once
    # — the kind is derived from the same text the user prompt embeds,
    # so the two can never diverge.
    world_block_text = _format_world_context_block(
        c.region_summary_json,
        subject_culture=c.culture,
        subject_faith=c.faith,
    )
    kind: PromptKind = "biography_woven" if world_block_text else "biography"

    # Issue #19: only the version tag is used now — it is persisted on
    # each biography row so we can tell which template era produced it.
    # Issue #20 removed the template files themselves; the rules live in
    # the user's prose dir (voice/<kind>.md), read per generation.
    version = _resolve_prompt_version(mode)

    return Briefing(
        kind=kind,
        prompt_version=version,
        user_prompt=_build_user_prompt(
            c,
            inputs.events,
            inputs.names_map,
            include_raw_record=include_raw_record,
            relation_labels=relation_labels,
            world_block_text=world_block_text,
        ),
    )

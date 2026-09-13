"""Tests for chronicler.narrative.event_rendering (ck3_chronicler-mayy / Phase 4).

Table-driven: one golden case per event type that's actually emitted by
the v0.6+ save-parse path. Vanilla_memory subtypes get their own table
since CK3 emits ~25 distinct ones — covering the most common five
exercises the dispatch + the participants-block path; the rest fall
through to the generic shape, which is covered by ``test_unknown_*``.

ck3_chronicler-w2ux: parametrised parity test below
(`test_every_event_type_has_a_renderer`) walks EventPayload's union
and asserts every event type has a dispatch entry. Adding a new event
class to schema/events.py without wiring its renderer fails this test.
"""

from __future__ import annotations

import typing

import pytest

from chronicler.narrative.event_rendering import _RENDERERS, render_event_body
from chronicler.schema.events import EventPayload

NAMES = {
    38137: "Örvar Sleggja",
    15052: "Saga the Truthspeaker",
    46434: "Hólmgeir",
    14570: "Causantin",
    16207: "Mael-Brigte",
}


# ----- per-type tables ------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        # Movement.
        (
            "travel",
            {"from_location": 8, "to_location": 989},
            "travelled from province 8 to province 989",
        ),
        # Traits.
        (
            "trait_gained",
            {"trait_id": 33, "trait_name": "gallant"},
            "gained trait 'gallant'",
        ),
        (
            "trait_lost",
            {"trait_id": 87, "trait_name": "bossy"},
            "lost trait 'bossy'",
        ),
        # Wars.
        (
            "war_concluded",
            {
                "war_name": "Norðreyjer Conquest of the Mormaerdom of Sutherland",
                "side": "attacker",
                "primary_attacker_id": 38137,
                "primary_defender_id": 14570,
            },
            "war concluded: 'Norðreyjer Conquest of the Mormaerdom of Sutherland' (as attacker)",
        ),
        (
            "war_joined",
            {"war_name": "Jórvík Conquest of the Earldom of Cambridgeshire", "side": "attacker"},
            "war joined: 'Jórvík Conquest of the Earldom of Cambridgeshire' (as attacker)",
        ),
        (
            "war_declared",
            {"war_name": "test war", "side": "defender"},
            "war declared: 'test war' (as defender)",
        ),
        # ck3_chronicler-x2cc: war_left — separate peace, side switch,
        # or release. Mirrors war_joined / war_declared shape.
        (
            "war_left",
            {"war_name": "Holy War for Aquitaine", "side": "defender"},
            "left war: 'Holy War for Aquitaine' (as defender)",
        ),
        # Title acquired with named former holder.
        (
            "title_acquired",
            {
                "from_holder_id": 16207,
                "tier": "barony",
                "title_id": 455,
                "title_key": "b_dornoch",
                "title_name": "Hjálmundalr",
            },
            "acquired barony title 'Hjálmundalr' from Mael-Brigte",
        ),
        # ck3_chronicler-y9p3: decision-driven title creation (custom
        # kingdom, empire formation). Pre-fix this fell through to the
        # bare-type fallback and the LLM had nothing to write the
        # formation from — Örvar's custom kingdom went unmentioned in
        # his obituary biography.
        (
            "title_created",
            {
                "tier": "kingdom",
                "title_id": 822,
                "title_key": "k_orvars_dominion",
                "title_name": "Örvar's Dominion",
            },
            "founded kingdom title 'Örvar's Dominion'",
        ),
        # ck3_chronicler-7t88 / x2cc: title_relinquished — symmetric to
        # title_acquired. Renderer added alongside the titles_map
        # plumbing so the inverse-of-creation event no longer falls
        # through to the bare-type fallback. to_holder_id resolves to
        # the named new holder when known.
        (
            "title_relinquished",
            {
                "tier": "duchy",
                "title_id": 405,
                "title_key": "d_western_isles",
                "title_name": "Suðreyjar",
                "to_holder_id": 14570,
            },
            "relinquished duchy title 'Suðreyjar' to Causantin",
        ),
        # title_relinquished with no recorded new holder (title
        # destroyed or transferred to an untracked NPC).
        (
            "title_relinquished",
            {
                "tier": "duchy",
                "title_id": 405,
                "title_key": "d_western_isles",
                "title_name": "Suðreyjar",
                "to_holder_id": None,
            },
            "relinquished duchy title 'Suðreyjar'",
        ),
        # Alliances (with named ally).
        (
            "alliance_formed",
            {"ally_character_id": 15052},
            "formed alliance with Saga the Truthspeaker",
        ),
        (
            "alliance_broken",
            {"ally_character_id": 15052},
            "broke alliance with Saga the Truthspeaker",
        ),
        # Marriage.
        (
            "marriage",
            {"spouse_character_id": 15052},
            "married Saga the Truthspeaker",
        ),
        # ck3_chronicler-x2cc: divorce with named former spouse.
        (
            "divorce",
            {"former_spouse_character_id": 15052},
            "divorced from Saga the Truthspeaker",
        ),
        # x2cc: divorce where CK3 didn't preserve the ex-spouse id.
        (
            "divorce",
            {"former_spouse_character_id": None},
            "divorced",
        ),
        # Death with cause + age.
        (
            "death",
            {"cause": "death_sickly", "death_age": 1},
            "died (cause: death_sickly, age: 1)",
        ),
        # Miscarriage with assumed father.
        (
            "miscarriage",
            {"assumed_father_id": 38137},
            "miscarriage (assumed father: Örvar Sleggja)",
        ),
        # Nickname.
        (
            "nickname",
            {"to_nickname": "the Prince of Fashion"},
            "received nickname 'the Prince of Fashion'",
        ),
        # ck3_chronicler-x2cc: adventurer arc transitions (empty payloads).
        (
            "adventurer_started",
            {},
            "became a landless adventurer",
        ),
        (
            "adventurer_ended",
            {},
            "settled from landless adventuring",
        ),
        # x2cc: house_change — cadet-branch founding, both names present.
        (
            "house_change",
            {
                "from_house_id": 12,
                "from_house_name": "Sleggja",
                "to_house_id": 88,
                "to_house_name": "Sleggja-Lid",
            },
            "changed house from 'Sleggja' to 'Sleggja-Lid'",
        ),
        # x2cc: house_change with the from-name unresolved by the lookup.
        (
            "house_change",
            {
                "from_house_id": None,
                "from_house_name": None,
                "to_house_id": 88,
                "to_house_name": "Sleggja-Lid",
            },
            "joined house 'Sleggja-Lid'",
        ),
        # x2cc: culture_change — common shape with both sides named.
        (
            "culture_change",
            {
                "from_culture_id": 4,
                "from_culture_name": "anglo-saxon",
                "to_culture_id": 7,
                "to_culture_name": "norse",
            },
            "adopted norse culture (from anglo-saxon)",
        ),
        # x2cc: faith_change — conversion with both faiths named.
        (
            "faith_change",
            {
                "from_faith_id": 12,
                "from_faith_name": "catholic",
                "to_faith_id": 33,
                "to_faith_name": "asatru",
            },
            "converted to asatru (from catholic)",
        ),
        # Decision.
        (
            "decision_taken",
            {"cooldown_end_date": "904.11.6", "decision_id": "raise_stele_decision"},
            "took decision 'raise_stele_decision'",
        ),
        # Building.
        (
            "building_completed",
            {
                "building": "palisades_01",
                "province_id": 8,
                "slot_index": 1,
                "start_date": "868.5.30",
            },
            "completed building 'palisades_01' in province 8",
        ),
        # Artifact.
        (
            "artifact_acquired",
            {
                "artifact_id": 77,
                "name": "Íñiga House Banner",
                "rarity": "common",
                "type": "wall_big",
            },
            "acquired artifact 'Íñiga House Banner' (common)",
        ),
        # ck3_chronicler-x2cc: artifact_lost mirrors artifact_acquired.
        (
            "artifact_lost",
            {
                "artifact_id": 77,
                "name": "Íñiga House Banner",
                "rarity": "common",
                "type": "wall_big",
            },
            "lost artifact 'Íñiga House Banner' (common)",
        ),
        # ck3_chronicler-x2cc: epidemic_outbreak — character newly infected.
        (
            "epidemic_outbreak",
            {
                "epidemic_id": 451,
                "epidemic_type": "smallpox",
                "name": "Pope Alexander's Boils",
                "intensity": "major",
                "start_date": "905.4.1",
                "num_infected_provinces": 17,
                "num_character_deaths": 0,
            },
            "fell ill with Pope Alexander's Boils (major)",
        ),
        # ck3_chronicler-qx7n (8aie slice 1) + 3abc: activity_completed —
        # host path. The renderer humanizes the CK3 engine key into a
        # noun phrase ("activity_feast" -> "a feast") rather than leaking
        # the raw "activity_" prefix into the prose.
        (
            "activity_completed",
            {
                "activity_id": 1124073474,
                "activity_type": "activity_feast",
                "role": "host",
                "host_id": 38137,  # Örvar Sleggja from NAMES
                "start_province_id": 1328,
                "start_date": "915.4.16",
            },
            "hosted a feast",
        ),
        # 3abc: a multi-word engine key still humanizes cleanly via the
        # known-type map ("activity_chariot_race" -> "a chariot race").
        (
            "activity_completed",
            {
                "activity_id": 1124073475,
                "activity_type": "activity_chariot_race",
                "role": "host",
                "host_id": 38137,
                "start_province_id": 496,
                "start_date": "915.5.1",
            },
            "hosted a chariot race",
        ),
        # qx7n + 3abc: activity_completed — attendee path. host_id resolves
        # to a name in the NAMES dict so the prose reads "attended X
        # hosted by Y".
        (
            "activity_completed",
            {
                "activity_id": 1157627904,
                "activity_type": "activity_pilgrimage",
                "role": "attendee",
                "host_id": 15052,  # Saga the Truthspeaker
                "start_province_id": 6223,
                "start_date": "916.1.4",
            },
            "attended a pilgrimage hosted by Saga the Truthspeaker",
        ),
        # qx7n + 3abc: activity_completed — attendee where the host id isn't
        # in the names map. Renderer must fall back gracefully to just
        # "attended X" rather than emitting "id 99999".
        (
            "activity_completed",
            {
                "activity_id": 99,
                "activity_type": "activity_hunt",
                "role": "attendee",
                "host_id": None,  # unresolved
                "start_province_id": None,
                "start_date": None,
            },
            "attended a hunt",
        ),
        # 3abc: an activity_type with no entry in the known-type map falls
        # back to stripping the "activity_" prefix and the underscores —
        # never leaking the raw engine key, even for modded/future types.
        (
            "activity_completed",
            {
                "activity_id": 100,
                "activity_type": "activity_some_mod_thing",
                "role": "attendee",
                "host_id": None,
                "start_province_id": None,
                "start_date": None,
            },
            "attended some mod thing",
        ),
        # Dynasty.
        (
            "dynasty_legacy_unlocked",
            {"dynasty_id": 1504, "legacy_key": "erudition_legacy_1"},
            "unlocked dynasty legacy 'erudition_legacy_1'",
        ),
        # ck3_chronicler-n0s4: modifier_acquired — raw engine key in
        # prose. Live anchor: 2026-05-12 gx7b smoke, player chose
        # the deity Ullr and received 'devoted_to_ullr'.
        (
            "modifier_acquired",
            {"modifier_key": "devoted_to_ullr"},
            "acquired modifier 'devoted_to_ullr'",
        ),
        # ck3_chronicler-rgay: perk_acquired mirrors modifier_acquired.
        # Live anchor: Genji (RtP adventurer) started with
        # bellum_justum_perk and parthian_tactics_perk from
        # ruler-designer points.
        (
            "perk_acquired",
            {"perk_key": "bellum_justum_perk"},
            "acquired perk 'bellum_justum_perk'",
        ),
        # ck3_chronicler-9p2g: first perk in a lifestyle → narrative beat.
        # Renderer drops the '_lifestyle' suffix for prose phrasing.
        (
            "lifestyle_committed",
            {
                "lifestyle_key": "martial_lifestyle",
                "first_perk_key": "bellum_justum_perk",
            },
            "committed to the martial lifestyle",
        ),
        # ck3_chronicler-621o: contract_completed with employer name
        # resolved from the names map.
        (
            "contract_completed",
            {
                "contract_type": "laamp_base_6021",
                "name": "Perform in a Play",
                "tier": 2,
                "employer_id": 38137,
                "location_province_id": 10621,
                "outcome": "completed",
                "acceptance_date": "1069.4.28",
                "completion_date": "1070.1.1",
            },
            "completed the contract 'Perform in a Play' for Örvar Sleggja",
        ),
        # 621o: outcome=invalidated -> 'failed', and unknown employer
        # falls back to the tier-suffixed form.
        (
            "contract_completed",
            {
                "contract_type": "laamp_base_2031",
                "name": "Settle Boundary Dispute",
                "tier": 1,
                "employer_id": 99999999,  # not in NAMES
                "location_province_id": 9903,
                "outcome": "invalidated",
                "acceptance_date": "1067.6.1",
                "completion_date": "1068.2.1",
            },
            "failed the contract 'Settle Boundary Dispute' (tier 1)",
        ),
        # ck3_chronicler-mke9: camp_companion_joined with the standard
        # _court_position suffix — humaniser strips it.
        (
            "camp_companion_joined",
            {
                "position_id": 16780472,
                "court_position": "court_physician_court_position",
                "employee_id": 38137,
                "employee_name": None,  # resolved through names map
                "hire_date": "1069.1.20",
            },
            "Örvar Sleggja joined the court as court physician",
        ),
        # mke9: RtP camp-officer keys drop the suffix in the engine —
        # humaniser pass-through. Payload-side name wins over names map.
        (
            "camp_companion_joined",
            {
                "position_id": 155,
                "court_position": "second_camp_officer",
                "employee_id": 63546,
                "employee_name": "Shigemoto",
                "hire_date": "1066.9.15",
            },
            "Shigemoto joined the court as second camp officer",
        ),
        # mke9: left event — companion departed. role suffix in parens
        # for context.
        (
            "camp_companion_left",
            {
                "position_id": 154,
                "court_position": "travel_leader_court_position",
                "employee_id": 38137,
                "employee_name": "Tadanushi",
                "hire_date": "1066.9.15",
            },
            "Tadanushi left the court (travel leader)",
        ),
        # ck3_chronicler-r343: domicile_moved with type-aware verb
        # phrasing. Province IDs are rendered raw — the briefing
        # layer can map them to names if desired.
        (
            "domicile_moved",
            {
                "domicile_id": 852,
                "domicile_type": "camp",
                "from_province_id": 9822,
                "to_province_id": 9903,
            },
            "moved camp from province 9822 to province 9903",
        ),
        (
            "domicile_moved",
            {
                "domicile_id": 852,
                "domicile_type": "east_asian_estate",
                "from_province_id": 1234,
                "to_province_id": 5678,
            },
            "moved east_asian_estate from province 1234 to province 5678",
        ),
        # ck3_chronicler-gu7j: concubine_taken — name resolved on the
        # payload by the diff layer. Live anchor: player Örvar took
        # Leofwynn (id 42865) after raiding Wessex.
        (
            "concubine_taken",
            {"concubine_id": 42865, "concubine_name": "Leofwynn"},
            "took Leofwynn as a concubine",
        ),
        # gu7j: payload missing the name (rare; diff couldn't resolve)
        # but the names map carries it — renderer falls back to that.
        (
            "concubine_taken",
            {"concubine_id": 15052, "concubine_name": None},
            "took Saga the Truthspeaker as a concubine",
        ),
        # gu7j: name completely unresolvable — renderer leaves the
        # bare "id N" form rather than emitting "took None".
        (
            "concubine_taken",
            {"concubine_id": 99999, "concubine_name": None},
            "took id 99999 as a concubine",
        ),
        # ck3_chronicler-ei8t: splendor_increased — tier 0 → 1 crossing.
        # Sleggja's actual live-save crossing at accumulated=1010.485.
        # Tier name pulled from CK3 vanilla loc (game_concepts L1270+).
        (
            "splendor_increased",
            {
                "dynasty_id": 11283,
                "dynasty_name": "Sleggja",
                "dynasty_head_id": 60494,
                "old_tier": 0,
                "new_tier": 1,
                "total_renown_at_change": 1010.485,
            },
            "House Sleggja reached splendor level Obscure (1,010 renown)",
        ),
        # ei8t: splendor_increased — tier 3, no dynasty_name resolved.
        # Falls back to "Dynasty #<id>" rather than emitting raw id.
        (
            "splendor_increased",
            {
                "dynasty_id": 999,
                "dynasty_name": None,
                "dynasty_head_id": None,
                "old_tier": 2,
                "new_tier": 3,
                "total_renown_at_change": 15_421.0,
            },
            "House Dynasty #999 reached splendor level Noteworthy (15,421 renown)",
        ),
        # ei8t: splendor_increased — crossing into the highest tier we
        # have a name for (tier 6 / "Significant"). Verifies the
        # name-table boundary.
        (
            "splendor_increased",
            {
                "dynasty_id": 11283,
                "dynasty_name": "Sleggja",
                "dynasty_head_id": 60494,
                "old_tier": 5,
                "new_tier": 6,
                "total_renown_at_change": 105_000.0,
            },
            "House Sleggja reached splendor level Significant (105,000 renown)",
        ),
    ],
)
def test_render_event_body_known_types(event_type: str, payload: dict, expected: str) -> None:
    assert render_event_body(event_type, payload, names_map=NAMES) == expected


# ----- vanilla_memory subtypes ----------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Common phrasing, no participants.
        (
            {"memory_type": "ascended_throne_memory"},
            "vanilla memory: ascended a throne",
        ),
        # Common phrasing with participants.
        (
            {"memory_type": "war_won", "participants": {"loser": 14570, "winner": 38137}},
            "vanilla memory: won a war (loser=Causantin, winner=Örvar Sleggja)",
        ),
        # Unmapped memory_type → generic prefix; participants still flow through.
        (
            {"memory_type": "some_new_subtype", "participants": {"target": 46434}},
            "vanilla memory: some_new_subtype (target=Hólmgeir)",
        ),
        # No memory_type at all.
        (
            {},
            "vanilla memory: ?",
        ),
        # ck3_chronicler-9neo: high-frequency variants added in the
        # 2026-05-17 audit batch. Each must resolve to a non-generic
        # phrase rather than falling through to "vanilla memory: {mt}".
        (
            {"memory_type": "lost_title_memory"},
            "vanilla memory: lost a title",
        ),
        (
            {"memory_type": "passed_provincial_exam_memory"},
            "vanilla memory: passed provincial exam",
        ),
        (
            {"memory_type": "ce1_contracted_epidemic"},
            "vanilla memory: contracted an epidemic",
        ),
        (
            {"memory_type": "accolade_created"},
            "vanilla memory: an accolade was created",
        ),
        (
            {"memory_type": "became_grudge", "participants": {"target": 46434}},
            "vanilla memory: developed a grudge (target=Hólmgeir)",
        ),
        (
            {"memory_type": "childhood_education_no_guardian"},
            "vanilla memory: childhood without a guardian",
        ),
        (
            {"memory_type": "spouse_died", "participants": {"spouse": 14570}},
            "vanilla memory: spouse died (spouse=Causantin)",
        ),
    ],
)
def test_render_vanilla_memory(payload: dict, expected: str) -> None:
    assert render_event_body("vanilla_memory", payload, names_map=NAMES) == expected


# ----- ck3_chronicler-7t88: titles_map tier override ------------------------


# Match Örvar's live save: a player-formed kingdom named after his
# pre-existing duchy. The new title's tier='other' in the payload
# because CK3 hadn't wired its de_jure_liege chain by the diff tick.
ORVAR_NORDREYJAR_PAYLOAD = {
    "tier": "other",
    "title_id": 19138,
    "title_key": "x_script_2517",
    "title_name": "Norðreyjar",
}


def test_title_created_titles_map_override_recovers_kingdom_tier() -> None:
    """ck3_chronicler-7t88: when the payload froze on 'other' (x_script_*
    title before CK3 wired its de_jure chain), the renderer must consult
    titles_map and substitute the q1ai-recovered tier. Without this
    override, the LLM sees 'founded other title' and drops the kingdom
    formation from the biography."""
    titles_map = {"x_script_2517": "kingdom"}
    out = render_event_body(
        "title_created",
        ORVAR_NORDREYJAR_PAYLOAD,
        names_map=NAMES,
        titles_map=titles_map,
    )
    assert out == "founded kingdom title 'Norðreyjar'"


def test_title_created_titles_map_omitted_falls_through_to_payload_tier() -> None:
    """When titles_map is absent (callers without title-state context,
    legacy call sites, tests), the renderer uses the payload tier
    verbatim. 'other' still surfaces — that's the documented bug — but
    no crash and the rest of the line is intact."""
    out = render_event_body(
        "title_created",
        ORVAR_NORDREYJAR_PAYLOAD,
        names_map=NAMES,
    )
    assert out == "founded other title 'Norðreyjar'"


def test_titles_map_does_not_override_real_payload_tier() -> None:
    """The override only fires when the payload tier is 'other' / missing.
    A real tier in the payload represents the title's state *at the
    event's date* and must not be retconned — a title that was a duchy
    when acquired stays 'acquired duchy ...' even if CK3 later raised
    it to kingdom-tier."""
    payload = {
        "from_holder_id": 16207,
        "tier": "duchy",
        "title_id": 455,
        "title_key": "d_munster",
        "title_name": "Duchy of Munster",
    }
    # titles_map says this title is currently a kingdom, but payload
    # tier='duchy' wins (the event was an acquisition AS a duchy).
    titles_map = {"d_munster": "kingdom"}
    out = render_event_body(
        "title_acquired",
        payload,
        names_map=NAMES,
        titles_map=titles_map,
    )
    assert out == "acquired duchy title 'Duchy of Munster' from Mael-Brigte"


def test_title_relinquished_titles_map_override() -> None:
    """Symmetric to title_created: at Örvar's death he relinquishes
    the player-formed kingdom and the same x_script_* key carries the
    same stale tier='other'. titles_map must apply here too so the
    obituary briefing reads 'relinquished kingdom title' rather than
    'relinquished other title'."""
    payload = {
        "tier": "other",
        "title_id": 19138,
        "title_key": "x_script_2517",
        "title_name": "Norðreyjar",
        "to_holder_id": 14570,
    }
    titles_map = {"x_script_2517": "kingdom"}
    out = render_event_body(
        "title_relinquished",
        payload,
        names_map=NAMES,
        titles_map=titles_map,
    )
    assert out == "relinquished kingdom title 'Norðreyjar' to Causantin"


# ----- fallbacks + safety ---------------------------------------------------


def test_unknown_event_type_falls_back_to_humanized_type() -> None:
    out = render_event_body("brand_new_event_we_dont_handle", {"foo": "bar"}, names_map=NAMES)
    # Crucially: no JSON, no payload leak — just the type. ck3_chronicler-2etd:
    # underscores stripped so the fallback reads as prose (and the export
    # roll's first-letter capitalize yields "Brand new event..." not
    # "Brand_new_event...") — engine keys are snake_case.
    assert out == "brand new event we dont handle"


def test_malformed_payload_does_not_raise() -> None:
    """Defence in depth: a real-world payload that doesn't match the
    expected shape (e.g. integer where a dict is expected) must not
    propagate an exception into the consolidation pass — the renderer
    catches and falls back to the bare type."""
    # title_acquired with a string where tier is expected normally works,
    # but missing the .get keys altogether shouldn't crash either.
    out = render_event_body("title_acquired", {}, names_map=NAMES)
    assert "title" in out  # Some sane fragment, no traceback.


def test_unknown_character_id_falls_back_to_id_label() -> None:
    out = render_event_body("marriage", {"spouse_character_id": 999_999}, names_map=NAMES)
    assert out == "married id 999999"


# ----- size comparison vs legacy JSON-inline format -------------------------


def test_phase4_size_savings_vs_legacy_format() -> None:
    """Verify the Phase 4 renderer actually saves tokens vs the pre-fix
    JSON-inline format. Uses a representative mix of events from the
    Sleggja 884-885 window. The exact ratio is incidental — what matters
    is that the new format is materially shorter."""
    import json

    sample_events = [
        (
            "trait_gained",
            {
                "c": 38137,
                "d": "884.5.1",
                "p": {"trait_id": 33, "trait_name": "gallant"},
                "t": "trait_gained",
                "v": 1,
            },
        ),
        (
            "travel",
            {
                "c": 38137,
                "d": "884.7.1",
                "p": {"from_location": 8, "to_location": 993},
                "t": "travel",
                "v": 1,
            },
        ),
        (
            "vanilla_memory",
            {
                "c": 38137,
                "d": "884.12.31",
                "p": {
                    "end_date": "1234.12.31",
                    "memory_type": "child_premature",
                    "participants": {"mother": 15052},
                },
                "t": "vanilla_memory",
                "v": 1,
            },
        ),
        (
            "war_concluded",
            {
                "c": 38137,
                "d": "885.9.1",
                "p": {
                    "casus_belli_type": "county_conquest_cb",
                    "claimant_id": 4294967295,
                    "primary_attacker_id": 16856,
                    "primary_defender_id": 12721,
                    "side": "defender",
                    "targeted_titles": [141],
                    "war_id": 134217833,
                    "war_name": "Suðreyjer Conquest of the Earldom of Northumberland",
                },
                "t": "war_concluded",
                "v": 1,
            },
        ),
    ]

    legacy_size = 0
    new_size = 0
    for et, full in sample_events:
        # Legacy: payload=<full JSON of the outer event>
        legacy_line = f"[id=2322] [????-??-??] {et} — d; payload={json.dumps(full)}"
        new_body = render_event_body(et, full["p"], names_map=NAMES)
        new_line = f"[id=2322] [????-??-??] {new_body}"
        legacy_size += len(legacy_line)
        new_size += len(new_line)

    # Empirically the renderer collapses events to ~40-60% of the JSON
    # format. Assert a conservative 30% savings so the test doesn't
    # break on minor format tweaks.
    assert new_size < legacy_size * 0.7, (
        f"Phase 4 should cut event-block size by >=30% on the sample; "
        f"got legacy={legacy_size} new={new_size}"
    )


# --- ck3_chronicler-w2ux: renderer coverage parity ---


def _all_event_type_literals() -> list[str]:
    """Extract every ``t`` discriminator value from EventPayload's union."""
    union_args = typing.get_args(EventPayload)[0]
    members = typing.get_args(union_args)
    out: list[str] = []
    for cls in members:
        literal_args = typing.get_args(cls.model_fields["t"].annotation)
        if literal_args:
            out.append(literal_args[0])
    return out


# 8 v0.2-era debug-log-only event types currently have no renderer in
# event_rendering._RENDERERS. They're declared in the schema for back-
# compat with v0.2 ingest fixtures but the save-parse pipeline (v0.6+)
# never emits them. Leaving them on this allowlist intentionally —
# pending a decision to either (a) drop the v0.2 types entirely or (b)
# add explicit renderers + glyphs.
_KNOWN_MISSING_RENDERERS: frozenset[str] = frozenset(
    {
        "birth",
        "title_gain",
        "title_lost",
        "war_started",
        "war_won_attacker",
        "war_won_defender",
        "imprison",
        "release",
    }
)


@pytest.mark.parametrize("event_type", _all_event_type_literals())
def test_every_event_type_has_a_renderer(event_type: str) -> None:
    """Every member of EventPayload's discriminated union must have a
    dispatch entry in _RENDERERS, or be explicitly listed on the
    _KNOWN_MISSING_RENDERERS allowlist with a documented reason.

    Adding a new event class to schema/events.py without wiring its
    renderer fails this test — the regression net that ck3_chronicler-
    x2cc didn't have when it shipped (and that w2ux installs)."""
    if event_type in _KNOWN_MISSING_RENDERERS:
        # Documented exception — see the allowlist's comment.
        assert event_type not in _RENDERERS, (
            f"event type '{event_type}' is on _KNOWN_MISSING_RENDERERS but "
            f"also has a renderer; remove it from the allowlist."
        )
        return
    assert event_type in _RENDERERS, (
        f"event type '{event_type}' is declared in schema.events."
        f"EventPayload but has no renderer in event_rendering._RENDERERS. "
        f"Either add a renderer or list it on _KNOWN_MISSING_RENDERERS "
        f"with a documented reason."
    )

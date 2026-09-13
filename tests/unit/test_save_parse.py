"""Tests for chronicler.save.parse — the rakaly-JSON → SaveSnapshot layer.

Uses synthetic JSON dicts mirroring real CK3 1.19 shape (verified against
the user's autosave during V06-S01). The real-fixture integration test
lives separately at tests/unit/test_save_real_fixture.py.
"""

from __future__ import annotations

import logging

from chronicler.save.parse import (
    _REAL_SAVE_MIN_TOP_LEVEL_KEYS,
    AUTO_TRACK_RULES_DEFAULT,
    FamilySnapshot,
    SaveSnapshot,
    TitleSnapshot,
    _infer_tier_for_title,
    _resolve_county_vassals,
    auto_track_candidates,
    extract_character_record,
    parse_save,
    resolve_auto_track_rules,
)


def _minimal_save(**overrides) -> dict:
    """Synthetic CK3-shaped JSON with overridable fields."""
    base = {
        "meta_data": {
            "version": "1.19.0.4",
            "meta_date": "1067.2.1",
            "meta_main_portrait": {"id": 1234},
        },
        "playthrough_id": "test-uuid-abc",
        "bookmark_date": "1066.9.15",
        "living": {},
        "dead_unprunable": {},
        "character_memory_manager": {"database": {}},
    }
    for k, v in overrides.items():
        base[k] = v
    return base


def test_minimal_save_parses() -> None:
    snap = parse_save(_minimal_save())
    assert snap.playthrough_id == "test-uuid-abc"
    assert snap.ck3_version == "1.19.0.4"
    assert snap.bookmark_date == "1066.9.15"
    assert snap.current_date == "1067.2.1"
    assert snap.player_character_id == 1234
    assert snap.characters == {}


def test_playthrough_id_synthesized_from_seed_when_canonical_missing() -> None:
    """ck3_chronicler-ayu: real CK3 1.18.3 saves don't carry a top-level
    playthrough_id. parse must fall back to a fingerprint built from
    random_seed + bookmark_date + founding-character id so save-tail's
    auto-resolve match still works across saves of the same run."""
    save = _minimal_save(
        random_seed=690927921,
        bookmark_date="867.1.1",
        played_character={
            "name": "testplayer",
            "character": 38204,
            "legacy": [{"character": 38204, "date": "867.1.1"}],
        },
    )
    save.pop("playthrough_id")
    snap = parse_save(save)
    assert snap.playthrough_id == "synth:690927921:867.1.1:38204"


def test_playthrough_id_synth_is_stable_across_saves_of_same_run() -> None:
    """Two saves of the same playthrough at different in-game dates
    (e.g. yearly autosaves) must resolve to the same synthetic id —
    that's the property registry.resolve_campaign_for_save relies on."""
    common = dict(
        random_seed=12345,
        bookmark_date="867.1.1",
        played_character={"legacy": [{"character": 9001}]},
    )
    a = _minimal_save(**common)
    a.pop("playthrough_id")
    a["meta_data"]["meta_date"] = "874.1.1"
    b = _minimal_save(**common)
    b.pop("playthrough_id")
    b["meta_data"]["meta_date"] = "875.1.1"
    assert parse_save(a).playthrough_id == parse_save(b).playthrough_id


def test_placeholder_shaped_canonical_playthrough_id_is_used_verbatim() -> None:
    """Issue #6: a fresh 867 start on 1.19.0.6 carries
    ``00000000-0000-4000-859f-47fe4aa6a23c`` — twelve leading hex zeros,
    which reads as a partially-initialised placeholder rather than a real
    per-playthrough id.

    It is not one. Measured 2026-08-13 across five saves of that same
    campaign (the never-played start at 867.1.1 through autosave_exit at
    873.3.16, rakaly 0.8.15): the value is byte-identical in all five. It
    never gets filled in, so the campaign pin taken from a fresh-start
    save keeps matching and nothing is dropped as foreign.

    This test pins the *decision* that follows: no degenerate-id
    heuristic. A rule rejecting placeholder-shaped ids would push this
    campaign onto the ``synth:`` path — a different id for the same
    playthrough, i.e. the very breakage it was meant to prevent.
    """
    save = _minimal_save(
        random_seed=690927921,
        bookmark_date="867.1.1",
        played_character={"legacy": [{"character": 38204}]},
    )
    save["playthrough_id"] = "00000000-0000-4000-859f-47fe4aa6a23c"
    snap = parse_save(save)
    assert snap.playthrough_id == "00000000-0000-4000-859f-47fe4aa6a23c"
    assert not snap.playthrough_id.startswith("synth:")


def test_playthrough_id_canonical_field_wins_when_present() -> None:
    """Backward-compat: if a save (real or modded) does carry the
    canonical playthrough_id, prefer it over the synthetic fallback."""
    save = _minimal_save(
        random_seed=12345,
        played_character={"legacy": [{"character": 9001}]},
    )
    save["playthrough_id"] = "canonical-uuid-xyz"
    snap = parse_save(save)
    assert snap.playthrough_id == "canonical-uuid-xyz"


def test_living_character_extracted() -> None:
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Eadmund",
                "birth": "1049.12.10",
                "culture": 231,
                "faith": 23,
                "dynasty_house": 1615,
                "ethnicity": "english",
                "traits": [65, 57, 60],
                "family_data": {"mother": 31175, "father": 31175, "child": [9999]},
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    char = snap.characters[1234]
    assert char.first_name == "Eadmund"
    assert char.is_dead is False
    assert char.female is False
    assert char.birth_date == "1049.12.10"
    assert char.death_date is None
    assert char.culture_id == 231
    assert char.faith_id == 23
    assert char.dynasty_house_id == 1615
    assert char.traits == (65, 57, 60)
    assert char.family.mother == 31175
    assert char.family.children == (9999,)


def test_dead_character_extracted() -> None:
    save = _minimal_save(
        dead_unprunable={
            "31175": {
                "first_name": "Harold",
                "birth": "1022.6.1",
                "culture": 231,
                "faith": 23,
                "dead_data": {"date": "1066.10.14", "memories": []},
                "family_data": {"child": [1234]},
            }
        }
    )
    snap = parse_save(save)
    char = snap.characters[31175]
    assert char.first_name == "Harold"
    assert char.is_dead is True
    assert char.death_date == "1066.10.14"
    assert char.family.children == (1234,)
    # Defaults when reason / killer are absent — old saves and natural
    # deaths without a recorded reason fall through here.
    assert char.death_cause is None
    assert char.death_killer is None


def test_dead_character_carries_cause_and_killer_when_present() -> None:
    """ck3_chronicler-caxv: parser surfaces dead_data.reason and
    dead_data.killer onto CharacterSnapshot for the diff layer to thread
    into DeathPayload."""
    save = _minimal_save(
        dead_unprunable={
            "44044": {
                "first_name": "Leszek",
                "birth": "1042.3.10",
                "culture": 5,
                "faith": 9,
                "dead_data": {
                    "date": "1076.8.30",
                    "reason": "death_battle",
                    "killer": 16818646,
                    "memories": [],
                },
                "family_data": {},
            }
        }
    )
    snap = parse_save(save)
    char = snap.characters[44044]
    assert char.death_cause == "death_battle"
    assert char.death_killer == 16818646
    assert char.death_date == "1076.8.30"


def test_dead_character_killer_optional_when_natural_cause() -> None:
    save = _minimal_save(
        dead_unprunable={
            "31341": {
                "first_name": "Ramon",
                "birth": "1023.1.1",
                "culture": 12,
                "faith": 4,
                "dead_data": {
                    "date": "1083.12.23",
                    "reason": "death_old_age",
                    "memories": [],
                },
                "family_data": {},
            }
        }
    )
    snap = parse_save(save)
    char = snap.characters[31341]
    assert char.death_cause == "death_old_age"
    assert char.death_killer is None


def test_death_cause_without_the_death_prefix_is_kept_verbatim() -> None:
    """Issue #9: ``blind`` is a real CK3 death reason, not trait bleed.

    It is defined in ``common/deathreasons/00_natural_deaths.txt`` under
    MISC (``natural_death_trigger`` = ``has_trait = blind`` OR
    ``clouded_eyes``), which is why it shares a trait's name. Vanilla
    1.19 defines 313 death reasons and exactly two of them lack the
    ``death_`` prefix: ``blind`` and ``debug``.

    The prefix is therefore NOT a validity rule, and a filter built on it
    would silently blank real causes — 1,106 of them in the single save
    this was measured against, against 3,440 ``death_old_age``. This test
    exists to make that filter fail loudly if anyone adds one.
    """
    save = _minimal_save(
        dead_unprunable={
            "16841916": {
                "first_name": "Elin",
                "birth": "1071.4.4",
                "culture": 5,
                "faith": 9,
                "dead_data": {"date": "1141.2.17", "reason": "blind", "memories": []},
                "family_data": {},
            }
        }
    )
    snap = parse_save(save)
    assert snap.characters[16841916].death_cause == "blind"


def test_living_to_dead_prunable_emits_death_event() -> None:
    """ck3_chronicler-izgr end-to-end: a character alive in the prev save
    and present only in characters.dead_prunable in the curr save must
    produce a DeathEvent. This is the exact succession sequence that
    silently dropped the player ruler's own death before the parse fix
    (autosave_1.ck3 living@1142.3.1 -> autosave.ck3 dead_prunable@1142.4.1).
    """
    from chronicler.save.diff import diff_snapshots
    from chronicler.schema import DeathEvent

    prev_save = _minimal_save(
        living={
            "33618293": {
                "first_name": "Nobuhiro",
                "birth": "1078.4.14",
                "alive_data": {"memories": []},
                "family_data": {},
            }
        }
    )
    prev_save["meta_data"]["meta_date"] = "1142.3.1"
    curr_save = _minimal_save(
        characters={
            "dead_prunable": {
                "33618293": {
                    "first_name": "Nobuhiro",
                    "birth": "1078.4.14",
                    "dead_data": {
                        "date": "1142.3.31",
                        "reason": "death_heart_attack",
                        "memories": [],
                    },
                    "was_playable": True,
                    "family_data": {},
                }
            }
        }
    )
    curr_save["meta_data"]["meta_date"] = "1142.4.1"

    events = diff_snapshots(parse_save(prev_save), parse_save(curr_save))
    deaths = [e for e in events if isinstance(e.event, DeathEvent)]
    assert len(deaths) == 1
    assert deaths[0].event.c == 33618293
    assert deaths[0].event.d == "1142.3.31"


def test_dead_prunable_character_extracted() -> None:
    """ck3_chronicler-izgr: freshly-dead characters (incl. just-succeeded
    player rulers) land in the NESTED ``characters.dead_prunable`` bucket,
    not the top-level ``dead_unprunable``. parse_save must load them too,
    or their death never reaches snap.characters and the diff drops the
    DeathEvent / biography (root-caused live on campaign cf5201ff: ruler
    Nobuhiro #33618293 died 1142.3.31, landed in dead_prunable, death lost).
    """
    save = _minimal_save(
        characters={
            "dead_prunable": {
                "33618293": {
                    "first_name": "Nobuhiro",
                    "birth": "1078.4.14",
                    "culture": 5,
                    "faith": 9,
                    "dead_data": {
                        "date": "1142.3.31",
                        "reason": "death_heart_attack",
                        "memories": [],
                    },
                    "was_playable": True,
                    "family_data": {},
                }
            }
        }
    )
    snap = parse_save(save)
    char = snap.characters[33618293]
    assert char.first_name == "Nobuhiro"
    assert char.is_dead is True
    assert char.death_date == "1142.3.31"
    assert char.death_cause == "death_heart_attack"


def test_female_flag_handled() -> None:
    save = _minimal_save(
        living={
            "5678": {
                "first_name": "Edith",
                "female": True,
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    assert snap.characters[5678].female is True
    # Default-when-missing
    save2 = _minimal_save(living={"9999": {"first_name": "Other", "alive_data": {"memories": []}}})
    assert parse_save(save2).characters[9999].female is False


def test_memories_linked_via_index() -> None:
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Eadmund",
                "alive_data": {"memories": [10, 20]},
            }
        },
        character_memory_manager={
            "database": {
                "10": {
                    "type": "memory_grand_wedding",
                    "creation_date": "1063.5.1",
                    "end_date": "1099.5.1",
                    "participants": {"spouse": 5678},
                },
                "20": {
                    "type": "memory_won_battle",
                    "creation_date": "1066.10.14",
                    "participants": {"opponent": 31175},
                },
                # Memory not referenced by anyone — still in DB but
                # shouldn't be attached to any character
                "30": {
                    "type": "memory_orphan",
                    "creation_date": "1066.1.1",
                    "participants": {},
                },
            }
        },
    )
    snap = parse_save(save)
    char = snap.characters[1234]
    assert len(char.memories) == 2
    types = {m.memory_type for m in char.memories}
    assert types == {"memory_grand_wedding", "memory_won_battle"}
    # Wedding memory has the spouse participant
    wedding = next(m for m in char.memories if m.memory_type == "memory_grand_wedding")
    assert wedding.participants == (("spouse", 5678),)


def test_family_data_empty_list_treated_as_no_family() -> None:
    """rakaly emits family_data as [] (empty list, not dict) when char has
    no family at all. parse_save must accept both shapes."""
    save = _minimal_save(
        living={"1234": {"first_name": "Loner", "family_data": [], "alive_data": {"memories": []}}}
    )
    char = parse_save(save).characters[1234]
    assert char.family == FamilySnapshot()


def test_single_spouse_as_int_normalises_to_tuple() -> None:
    """rakaly emits family_data.spouse as an int when there's one spouse,
    a list when multiple. parse_save normalises to tuple."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "X",
                "family_data": {"spouse": 5678},
                "alive_data": {"memories": []},
            }
        }
    )
    char = parse_save(save).characters[1234]
    assert char.spouses if hasattr(char, "spouses") else char.family.spouses == (5678,)


def test_as_int_tuple_skips_junk_entries() -> None:
    """ck3_chronicler-27ov.7 (audit M-S2): a malformed list entry (CK3 has
    been observed emitting sentinel strings inside lists) must be dropped, not
    raise. parse.py's contract is 'permissive — never stop on bad data'; one
    junk field used to make ingest permanently dead for the campaign."""
    from chronicler.save.parse import _as_int_tuple

    assert _as_int_tuple(["12", "abc", 3]) == (12, 3)
    assert _as_int_tuple([None, "x", 7]) == (7,)
    assert _as_int_tuple(["junk"]) == ()


def test_family_data_with_junk_child_id_still_parses() -> None:
    """End-to-end: a junk id in a family_data list must not abort the whole
    character parse (27ov.7 / audit M-S2)."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Parent",
                "family_data": {"child": [5678, "garbage", 9012]},
                "alive_data": {"memories": []},
            }
        }
    )
    char = parse_save(save).characters[1234]
    assert char.family.children == (5678, 9012)


def test_missing_optional_fields_default_to_none() -> None:
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Sparse",
                # No birth, no culture, no faith, no traits, no family_data,
                # no alive_data — all should default cleanly
            }
        }
    )
    char = parse_save(save).characters[1234]
    assert char.birth_date is None
    assert char.culture_id is None
    assert char.faith_id is None
    assert char.dynasty_house_id is None
    assert char.traits == ()
    assert char.family == FamilySnapshot()
    assert char.memories == ()


def test_player_id_optional() -> None:
    """If a save has no main_portrait (rare — observer mode?), parser
    leaves player_character_id as None instead of crashing."""
    save = _minimal_save(meta_data={"version": "1.19.0.4", "meta_date": "1067.1.1"})
    snap = parse_save(save)
    assert snap.player_character_id is None


# --- auto-track ---


def test_auto_track_returns_empty_when_no_player() -> None:
    snap = SaveSnapshot(
        playthrough_id="x",
        ck3_version="1.19.0.4",
        bookmark_date=None,
        current_date="1066.9.15",
        player_character_id=None,
        characters={},
    )
    assert auto_track_candidates(snap) == []


def test_auto_track_includes_player_spouse_children_parents() -> None:
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {
                    "primary_spouse": 5678,
                    "spouse": 5678,
                    "child": [9001, 9002],
                    "mother": 4001,
                    "father": 4002,
                },
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    out = auto_track_candidates(snap)
    out_dict = dict(out)
    assert 1234 in out_dict and out_dict[1234] == "player"
    assert 5678 in out_dict
    assert 9001 in out_dict and out_dict[9001] == "child"
    assert 9002 in out_dict
    assert 4001 in out_dict and out_dict[4001] == "mother"
    assert 4002 in out_dict and out_dict[4002] == "father"


def test_auto_track_dedupes_when_spouse_appears_twice() -> None:
    """primary_spouse + spouse list often overlap. auto_track should
    return each ID once."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {"primary_spouse": 5678, "spouse": 5678},
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    out = auto_track_candidates(snap)
    ids = [cid for cid, _ in out]
    assert ids.count(5678) == 1


# --- ck3_chronicler-gw16: rule-gated auto-track ---


def _gw16_player_snap():
    """Helper: player + spouse + child + parents."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {
                    "primary_spouse": 5678,
                    "child": [9001],
                    "mother": 4001,
                    "father": 4002,
                },
                "alive_data": {"memories": []},
            }
        }
    )
    return parse_save(save)


def test_auto_track_rules_off_for_heirs_excludes_children() -> None:
    """include_heirs=False drops children; spouse + parents survive."""
    snap = _gw16_player_snap()
    out = auto_track_candidates(snap, rules={"include_heirs": False})
    ids = [cid for cid, _ in out]
    assert 9001 not in ids
    # Spouse + parents still in.
    assert 5678 in ids
    assert 4001 in ids and 4002 in ids


def test_auto_track_rules_off_for_spouses_excludes_spouses() -> None:
    """include_spouses=False drops the primary spouse and spouses
    list; children + parents survive."""
    snap = _gw16_player_snap()
    out = auto_track_candidates(snap, rules={"include_spouses": False})
    ids = [cid for cid, _ in out]
    assert 5678 not in ids
    assert 9001 in ids  # child still in
    assert 4001 in ids and 4002 in ids  # parents always in


def test_auto_track_rules_default_matches_legacy_behavior() -> None:
    """rules=None must match the pre-gw16 shape exactly so callers that
    don't pass rules retain identical output."""
    snap = _gw16_player_snap()
    legacy = auto_track_candidates(snap, rules=None)
    explicit_default = auto_track_candidates(
        snap,
        rules={"include_heirs": True, "include_spouses": True},
    )
    assert legacy == explicit_default


# --- resolve_auto_track_rules (ck3_chronicler-vlw3) -------------------------
#
# One resolver shared by the GUI auto-track route and the CLI command, so
# the same persisted rules JSON yields identical candidates on either
# surface (the CLI used to pass no rules at all).


def test_resolve_auto_track_rules_none_returns_defaults() -> None:
    assert resolve_auto_track_rules(None) == AUTO_TRACK_RULES_DEFAULT
    # A distinct dict, not the module-level constant (callers may mutate).
    assert resolve_auto_track_rules(None) is not AUTO_TRACK_RULES_DEFAULT


def test_resolve_auto_track_rules_malformed_json_falls_back_to_defaults() -> None:
    assert resolve_auto_track_rules("not json") == AUTO_TRACK_RULES_DEFAULT
    assert resolve_auto_track_rules("[1, 2, 3]") == AUTO_TRACK_RULES_DEFAULT


def test_resolve_auto_track_rules_layers_partial_over_defaults() -> None:
    resolved = resolve_auto_track_rules('{"include_heirs": false}')
    assert resolved["include_heirs"] is False
    # Untouched flags keep their default.
    assert resolved["include_spouses"] is True
    assert resolved["include_county_vassals"] is False


def test_resolve_auto_track_rules_ignores_non_bool_values() -> None:
    # Defensive: a junk value for a known flag must not poison the dict.
    resolved = resolve_auto_track_rules('{"include_heirs": "yes", "include_spouses": false}')
    assert resolved["include_heirs"] is True  # junk ignored -> default
    assert resolved["include_spouses"] is False


def test_resolve_auto_track_rules_feeds_candidates_identically_to_inline_rules() -> None:
    """The CLI path (resolve from JSON) and a hand-built rules dict must
    produce the same candidates — that parity is the whole point of the
    shared resolver."""
    snap = _gw16_player_snap()
    from_json = auto_track_candidates(
        snap, rules=resolve_auto_track_rules('{"include_spouses": false}')
    )
    from_dict = auto_track_candidates(snap, rules={"include_spouses": False})
    assert from_json == from_dict


# --- ck3_chronicler-6ui: extract_character_record helper ---


# --- ck3_chronicler-6ui: extract_character_record helper ---


def test_extract_character_record_pulls_living_record() -> None:
    raw = {
        "first_name": "Eadmund",
        "birth": "1049.12.10",
        "culture": 231,
        "alive_data": {"memories": []},
    }
    save = _minimal_save(living={"36892": raw})
    out = extract_character_record(save, 36892)
    assert out is not None
    # first_name now stripped per ck3_chronicler-60m — header surfaces it
    assert "first_name" not in out
    assert out["culture"] == 231


def test_extract_character_record_pulls_dead_record() -> None:
    raw = {
        "first_name": "Edward",
        "birth": "1003.1.1",
        "dead_data": {"date": "1066.1.5"},
    }
    save = _minimal_save(dead_unprunable={"100": raw})
    out = extract_character_record(save, 100)
    assert out is not None
    assert out["dead_data"]["date"] == "1066.1.5"


def test_extract_character_record_pulls_dead_prunable_record() -> None:
    """A character present only in characters.dead_prunable (nested) must
    be found — this is the freshly-dead bucket ck3_chronicler-izgr."""
    raw = {
        "first_name": "Nobuhiro",
        "birth": "1100.1.1",
        "dead_data": {"date": "1142.3.31"},
    }
    save = _minimal_save(characters={"dead_prunable": {"42": raw}})
    out = extract_character_record(save, 42)
    assert out is not None
    assert out["dead_data"]["date"] == "1142.3.31"


def test_extract_character_record_precedence_living_over_dead_prunable() -> None:
    """living wins over dead_unprunable over characters.dead_prunable."""
    save = _minimal_save(
        living={"42": {"culture": 1, "alive_data": {"memories": []}}},
        dead_unprunable={"42": {"culture": 2, "dead_data": {"date": "1066.1.1"}}},
        characters={"dead_prunable": {"42": {"culture": 3, "dead_data": {"date": "1142.3.31"}}}},
    )
    out = extract_character_record(save, 42)
    assert out is not None
    assert out["culture"] == 1


def test_extract_character_record_precedence_dead_unprunable_over_dead_prunable() -> None:
    """dead_unprunable takes priority over characters.dead_prunable."""
    save = _minimal_save(
        dead_unprunable={"42": {"culture": 2, "dead_data": {"date": "1066.1.1"}}},
        characters={"dead_prunable": {"42": {"culture": 3, "dead_data": {"date": "1142.3.31"}}}},
    )
    out = extract_character_record(save, 42)
    assert out is not None
    assert out["culture"] == 2


def test_extract_character_record_returns_none_for_unknown() -> None:
    save = _minimal_save()
    assert extract_character_record(save, 99999) is None


def test_extract_character_record_strips_dna_field() -> None:
    """``dna`` is a 100+ trait-ID array with no narrative value — must
    be stripped to keep the prompt token cost manageable."""
    raw = {
        "dna": [1, 2, 3, 4, 5] * 50,  # would be ~kilobytes in real saves
        "alive_data": {"memories": []},
    }
    save = _minimal_save(living={"42": raw})
    out = extract_character_record(save, 42)
    assert out is not None
    assert "dna" not in out


# --- ck3_chronicler-60m: drop header-duplicates + resolve family IDs ---


def test_extract_character_record_strips_header_duplicate_fields() -> None:
    """ck3_chronicler-60m: first_name / nickname_text / female are surfaced
    in the structured prompt header. Leaving them in the raw record made
    the model trust the un-decoded raw form ("E_lla", female=No) over the
    resolved header ("Ælla", Gender: man). Strip them at extract time."""
    raw = {
        "first_name": "E_lla",
        "nickname_text": "the Impaler",
        "female": "no",
        "alive_data": {"memories": []},
        "traits": [54, 67, 81],
    }
    save = _minimal_save(living={"12267": raw})
    out = extract_character_record(save, 12267)
    assert out is not None
    assert "first_name" not in out
    assert "nickname_text" not in out
    assert "female" not in out
    # Non-header fields preserved
    assert out["traits"] == [54, 67, 81]


def test_extract_character_record_resolves_family_ids_when_lookup_provided() -> None:
    """ck3_chronicler-60m: family_data IDs expand to {id, name} dicts so
    the LLM has names inline. Without this the model writes "married
    38379" verbatim from the raw blob."""
    raw = {
        "alive_data": {"memories": []},
        "family_data": {
            "primary_spouse": 38379,
            "spouse": [38379, 39000],
            "child": [45395, 45396],
            "mother": 1000,
        },
    }
    save = _minimal_save(living={"29160": raw})
    name_lookup = {38379: "Beorhtgyth", 45395: "Tadg", 1000: "Sadb"}
    out = extract_character_record(save, 29160, name_lookup=name_lookup)
    assert out is not None
    fam = out["family_data"]
    assert fam["primary_spouse"] == {"id": 38379, "name": "Beorhtgyth"}
    assert fam["mother"] == {"id": 1000, "name": "Sadb"}
    # Lists handled
    assert fam["spouse"] == [
        {"id": 38379, "name": "Beorhtgyth"},
        {"id": 39000, "name": None},  # unknown id → name=None, not raw int
    ]
    assert fam["child"] == [
        {"id": 45395, "name": "Tadg"},
        {"id": 45396, "name": None},
    ]


def test_extract_character_record_no_lookup_leaves_family_unchanged() -> None:
    """When name_lookup is None, family_data passes through as raw IDs.
    Lets save-tail callers opt in incrementally."""
    raw = {
        "alive_data": {"memories": []},
        "family_data": {"primary_spouse": 38379, "spouse": [38379]},
    }
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(save, 1)  # no name_lookup
    assert out is not None
    assert out["family_data"] == {"primary_spouse": 38379, "spouse": [38379]}


def test_extract_character_record_coerces_list_family_data_to_empty_dict() -> None:
    """ck3_chronicler-63yw slice 1: rakaly emits family_data as an empty
    list ([]) when a character has no recorded family relations (typical
    for young children). The persisted save_snapshot_json must use a
    canonical dict shape so downstream consumers (tree.py, prompts) can
    rely on .get('child') / .get('mother') without a per-call isinstance
    check. Coerce list-shape to {} at extract time; non-empty dicts pass
    through untouched."""
    raw = {"alive_data": {"memories": []}, "family_data": []}
    save = _minimal_save(living={"42": raw})
    out = extract_character_record(save, 42)
    assert out is not None
    assert out["family_data"] == {}


def test_extract_character_record_list_family_data_normalised_before_name_lookup() -> None:
    """The list→dict normalisation must happen before _resolve_family_names
    inspects the field, so the name_lookup path doesn't silently skip
    list-shape records."""
    raw = {"alive_data": {"memories": []}, "family_data": []}
    save = _minimal_save(living={"42": raw})
    out = extract_character_record(save, 42, name_lookup={1: "Anyone"})
    assert out is not None
    # Empty input → empty (normalised) dict, no key expansion needed.
    assert out["family_data"] == {}


def test_extract_character_record_resolves_only_known_family_keys() -> None:
    """Don't resolve int values under unrelated keys — only the allowlisted
    family-relation keys (mother, father, spouse, child, etc.). Other
    int values in family_data (e.g. some hypothetical count field) pass
    through untouched."""
    raw = {
        "alive_data": {"memories": []},
        "family_data": {
            "primary_spouse": 38379,
            "some_count": 42,  # not a character ID
        },
    }
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(save, 1, name_lookup={38379: "X"})
    assert out is not None
    fam = out["family_data"]
    assert fam["primary_spouse"] == {"id": 38379, "name": "X"}
    assert fam["some_count"] == 42


# --- ck3_chronicler-5ty: traits decoded into traits_named ---


def test_extract_character_record_decodes_traits_when_lookup_provided() -> None:
    raw = {"alive_data": {"memories": []}, "traits": [0, 2, 4]}
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(
        save,
        1,
        traits_lookup=("diligent", "lazy", "brave", "craven", "education_diplomacy_3"),
    )
    assert out is not None
    assert out["traits"] == [0, 2, 4]  # raw ids preserved
    assert out["traits_named"] == ["diligent", "brave", "education_diplomacy_3"]


def test_extract_character_record_traits_lookup_handles_unknown_id() -> None:
    """Mod-added trait IDs beyond the base lookup must fall through as a
    placeholder rather than dropping silently or crashing."""
    raw = {"alive_data": {"memories": []}, "traits": [0, 999]}
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(save, 1, traits_lookup=("diligent",))
    assert out is not None
    assert out["traits_named"] == ["diligent", "trait_999"]


def test_extract_character_record_no_traits_lookup_leaves_record_unchanged() -> None:
    """traits_named is only injected when the caller provides a
    traits_lookup — older callers must be unaffected."""
    raw = {"alive_data": {"memories": []}, "traits": [1, 2, 3]}
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(save, 1)
    assert out is not None
    assert "traits_named" not in out


def test_extract_character_record_traits_lookup_handles_scalar_traits() -> None:
    """CK3 emits a scalar (not a list) when there's exactly one trait —
    same shape rule as parse._as_int_tuple."""
    raw = {"alive_data": {"memories": []}, "traits": 0}
    save = _minimal_save(living={"1": raw})
    out = extract_character_record(save, 1, traits_lookup=("diligent",))
    assert out is not None
    assert out["traits_named"] == ["diligent"]


# --- ck3_chronicler-cc3: alliance parsing from relations.active_relations ---


def test_alliances_extracted_from_active_relations() -> None:
    save = _minimal_save(
        relations={
            "active_relations": [
                {
                    "first": 100,
                    "second": 200,
                    "alliances": [{"allied_through_0": 100, "allied_through_1": 200}],
                },
                {"first": 300, "second": 400},  # not allied — no 'alliances' key
            ]
        }
    )
    snap = parse_save(save)
    assert snap.alliances[100] == frozenset({200})
    # Bidirectional: B's perspective must also list A
    assert snap.alliances[200] == frozenset({100})
    # Non-allied characters must not appear
    assert 300 not in snap.alliances
    assert 400 not in snap.alliances


def test_alliances_dedupe_when_save_lists_both_directions() -> None:
    """Real saves list each alliance pair from both sides
    (first=A, second=B, alliances=[...]) AND (first=B, second=A, alliances=[...]).
    Parse should still produce a single set per character."""
    save = _minimal_save(
        relations={
            "active_relations": [
                {"first": 100, "second": 200, "alliances": [{"a": 1}]},
                {"first": 200, "second": 100, "alliances": [{"a": 1}]},
            ]
        }
    )
    snap = parse_save(save)
    assert snap.alliances[100] == frozenset({200})
    assert snap.alliances[200] == frozenset({100})


def test_alliances_empty_when_no_relations_key() -> None:
    snap = parse_save(_minimal_save())
    assert snap.alliances == {}


def test_alliances_handles_multiple_per_character() -> None:
    """Toirrdelbach's autosave had ~3 alliance entries — each with a
    different second character. All must end up in his ally set."""
    save = _minimal_save(
        relations={
            "active_relations": [
                {"first": 100, "second": 200, "alliances": [{}]},
                {"first": 100, "second": 300, "alliances": [{}]},
                {"first": 100, "second": 400, "alliances": [{}]},
            ]
        }
    )
    snap = parse_save(save)
    assert snap.alliances[100] == frozenset({200, 300, 400})
    assert snap.alliances[200] == frozenset({100})
    assert snap.alliances[400] == frozenset({100})


# --- ck3_chronicler-o7j: war parsing from wars.active_wars ---


def test_wars_parsed_with_full_metadata() -> None:
    """Verifies the real CK3 1.19 shape captured from the v0.7 smoke
    save: each war record at wars.active_wars[<id>] has attacker +
    defender sides with participants arrays, casus_belli with type +
    targeted_titles + the principals' character IDs, plus name and
    start_date."""
    save = _minimal_save(
        wars={
            "active_wars": {
                "67108864": {
                    "name": "War for Aquitaine",
                    "start_date": "1074.11.29",
                    "attacker": {
                        "participants": [
                            {"character": 100, "casualties": 0},
                            {"character": 101, "casualties": 0},
                        ]
                    },
                    "defender": {
                        "participants": [
                            {"character": 200, "casualties": 0},
                            {"character": 201, "casualties": 0},
                        ]
                    },
                    "casus_belli": {
                        "type": "claimant_faction_war",
                        "targeted_titles": [972],
                        "attacker": 100,
                        "defender": 200,
                        "claimant": 101,
                    },
                }
            }
        }
    )
    snap = parse_save(save)
    assert 67108864 in snap.wars
    war = snap.wars[67108864]
    assert war.name == "War for Aquitaine"
    assert war.start_date == "1074.11.29"
    assert war.casus_belli_type == "claimant_faction_war"
    assert war.targeted_titles == (972,)
    assert war.primary_attacker_id == 100
    assert war.primary_defender_id == 200
    assert war.claimant_id == 101
    assert war.attacker_participants == frozenset({100, 101})
    assert war.defender_participants == frozenset({200, 201})


def test_character_to_wars_lookup_built_from_both_sides() -> None:
    """The parallel character_to_wars map is what the diff layer set-
    diffs against. Every participant of every war must appear keyed
    to that war's id, regardless of which side they're on."""
    save = _minimal_save(
        wars={
            "active_wars": {
                "1": {
                    "attacker": {"participants": [{"character": 100}]},
                    "defender": {"participants": [{"character": 200}]},
                    "casus_belli": {"type": "x", "attacker": 100, "defender": 200},
                },
                "2": {
                    "attacker": {"participants": [{"character": 100}]},
                    "defender": {"participants": [{"character": 300}]},
                    "casus_belli": {"type": "x", "attacker": 100, "defender": 300},
                },
            }
        }
    )
    snap = parse_save(save)
    # Char 100 is in both wars (different attacker each time)
    assert snap.character_to_wars[100] == frozenset({1, 2})
    assert snap.character_to_wars[200] == frozenset({1})
    assert snap.character_to_wars[300] == frozenset({2})


def test_wars_empty_when_no_wars_section() -> None:
    snap = parse_save(_minimal_save())
    assert snap.wars == {}
    assert snap.character_to_wars == {}


def test_artifacts_parsed_with_owner_and_metadata() -> None:
    """ck3_chronicler-d83: artifact records carry name/type/rarity +
    bare-int owner. Both the artifact index and the inverse
    character_to_artifacts map are populated."""
    save = _minimal_save(
        artifacts={
            "artifacts": {
                "0": {
                    "name": "Old Itinerary",
                    "type": "journal",
                    "rarity": "famed",
                    "owner": 100,
                },
                "1": {
                    "name": "Sword of Light",
                    "type": "weapon",
                    "rarity": "illustrious",
                    "owner": 200,
                },
                "2": {
                    "name": "Unowned Relic",
                    # no owner — still indexed but contributes no
                    # character_to_artifacts entry
                },
            }
        }
    )
    snap = parse_save(save)
    assert snap.artifacts[0].name == "Old Itinerary"
    assert snap.artifacts[0].owner_id == 100
    assert snap.artifacts[1].rarity == "illustrious"
    assert snap.artifacts[2].owner_id is None
    assert snap.character_to_artifacts == {
        100: frozenset({0}),
        200: frozenset({1}),
    }


def test_artifacts_empty_when_no_section() -> None:
    snap = parse_save(_minimal_save())
    assert snap.artifacts == {}
    assert snap.character_to_artifacts == {}


def test_artifact_name_strips_loca_markup_at_parse_time() -> None:
    """ck3_chronicler-4u14: artifact names occasionally carry CK3
    tooltip/link markup (referencing creators / inscriptions). The
    parser strips defensively at ingest so prose-layer consumers see
    only the visible text."""
    save = _minimal_save(
        artifacts={
            "artifacts": {
                "0": {
                    "name": "Sword of \x15ONCLICK:CHARACTER,123 \x15L; King Arthur\x15!\x15!",
                    "type": "weapon",
                    "rarity": "illustrious",
                    "owner": 100,
                },
            }
        }
    )
    snap = parse_save(save)
    assert snap.artifacts[0].name == "Sword of King Arthur"


def test_dynasty_perks_parsed_per_dynasty() -> None:
    """ck3_chronicler-d83: dynasty_perks captures ``dynasties.dynasties[id].perk``
    arrays so the diff layer can emit DynastyLegacyUnlocked when a
    dynasty advances a tier."""
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "1": {"perk": ["blood_legacy_1", "blood_legacy_2"]},
                "2": {"perk": ["fp1_pillage_legacy_1"]},
                "3": {"perk": []},  # empty list — skipped
                "4": {},  # no perk field — skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasty_perks == {
        1: frozenset({"blood_legacy_1", "blood_legacy_2"}),
        2: frozenset({"fp1_pillage_legacy_1"}),
    }


def test_character_modifiers_parsed_per_character() -> None:
    """ck3_chronicler-n0s4: character_modifier records carry engine
    modifier keys like 'devoted_to_ullr' (deity choice), 'mourning_*',
    'event_*' temporary mods. We capture every entry's ``modifier``
    field as a tuple; the diff layer turns adjacent snapshots into
    modifier_acquired events."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Erik",
                "alive_data": {},
                "character_modifier": [
                    {"modifier": "devoted_to_ullr", "date": "884.5.1"},
                    {"modifier": "mourning_son", "date": "885.6.1"},
                ],
            },
            "2": {
                "first_name": "Saga",
                "alive_data": {},
                "character_modifier": [],  # empty list — empty tuple
            },
            "3": {
                "first_name": "Bjorn",
                "alive_data": {},
                # no character_modifier field — empty tuple
            },
        }
    )
    snap = parse_save(save)
    assert snap.characters[1].modifiers == ("devoted_to_ullr", "mourning_son")
    assert snap.characters[2].modifiers == ()
    assert snap.characters[3].modifiers == ()


def test_character_modifiers_handles_malformed_entries() -> None:
    """Entries without a ``modifier`` field or with non-string values
    are skipped — defensive against rakaly emitting partial records."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Erik",
                "alive_data": {},
                "character_modifier": [
                    {"modifier": "devoted_to_ullr", "date": "884.5.1"},
                    {"date": "885.1.1"},  # no modifier key
                    {"modifier": None, "date": "885.2.1"},
                    {"modifier": 42, "date": "885.3.1"},
                    {"modifier": "valid_mod"},  # missing date is fine
                ],
            },
        }
    )
    snap = parse_save(save)
    assert snap.characters[1].modifiers == ("devoted_to_ullr", "valid_mod")


def test_character_perks_parsed_per_character() -> None:
    """ck3_chronicler-rgay: alive_data.perk is a flat list of lifestyle
    perk engine keys. Captured verbatim; defensive against missing
    field, empty list, and non-string entries."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Genji",
                "alive_data": {
                    "perk": ["bellum_justum_perk", "parthian_tactics_perk"],
                },
            },
            "2": {
                "first_name": "Saga",
                "alive_data": {"perk": []},  # empty list
            },
            "3": {
                "first_name": "Bjorn",
                "alive_data": {},  # missing field
            },
            "4": {
                "first_name": "Mixed",
                "alive_data": {
                    "perk": [
                        "schemer_perk",
                        None,  # skipped
                        42,  # skipped
                        "",  # skipped
                        "intrigue_perk",
                    ],
                },
            },
        }
    )
    snap = parse_save(save)
    assert snap.characters[1].perks == ("bellum_justum_perk", "parthian_tactics_perk")
    assert snap.characters[2].perks == ()
    assert snap.characters[3].perks == ()
    assert snap.characters[4].perks == ("schemer_perk", "intrigue_perk")


def test_dynasty_renown_parsed_per_dynasty() -> None:
    """ck3_chronicler-ei8t: dynasty_renown captures the lifetime
    accumulated renown that drives splendor-tier crossings. Field path
    is ``dynasties.dynasties[id].prestige.accumulated`` (verified live
    on Sleggja 2026-05-14)."""
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "1": {"prestige": {"currency": 250.5, "accumulated": 1010.485}},
                "2": {"prestige": {"accumulated": 50_000.0}},
                "3": {"prestige": {"currency": 200.0}},  # no accumulated — skipped
                "4": {"prestige": None},  # malformed — skipped
                "5": {},  # no prestige block — skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasty_renown == {
        1: 1010.485,
        2: 50_000.0,
    }


def test_dynasty_renown_handles_malformed_accumulated() -> None:
    """``accumulated`` as null, a string, or missing should yield no
    entry — defensive against rakaly emitting unexpected shapes."""
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "1": {"prestige": {"accumulated": None}},
                "2": {"prestige": {"accumulated": "nope"}},
                "3": {"prestige": {"accumulated": 999.0}},
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasty_renown == {3: 999.0}


def test_dynasty_heads_parsed_per_dynasty() -> None:
    """ck3_chronicler-ei8t: dynasty_heads captures the current head
    character id per dynasty, used to annotate splendor crossings."""
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "1": {"dynasty_head": 60494},
                "2": {"dynasty_head": 11283},
                "3": {},  # no head — skipped
                "4": {"dynasty_head": None},  # malformed — skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasty_heads == {1: 60494, 2: 11283}


def test_wars_skip_malformed_entries() -> None:
    """A war record missing both attacker and defender participants
    parses cleanly but contributes no character_to_wars entries —
    we don't pretend to know who's in a war we can't read sides
    from."""
    save = _minimal_save(
        wars={
            "active_wars": {
                "5": {
                    "casus_belli": {"type": "x"},
                    # No attacker/defender blocks at all — malformed
                },
                "6": "not even a dict",
            }
        }
    )
    snap = parse_save(save)
    # War 5 still makes it as a record (CB metadata survives) but
    # contributes no participants
    assert 5 in snap.wars
    assert snap.wars[5].attacker_participants == frozenset()
    assert snap.wars[5].defender_participants == frozenset()
    assert snap.character_to_wars == {}
    # War 6 (non-dict) skipped entirely
    assert 6 not in snap.wars


# --- ck3_chronicler-mcu: government / adventurer-mode parsing ---


def test_government_extracted_from_landed_data() -> None:
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Bjorn",
                "alive_data": {"memories": []},
                "landed_data": {"government": "feudal_government"},
            }
        }
    )
    char = parse_save(save).characters[1]
    assert char.government == "feudal_government"


def test_adventurer_government_recognised() -> None:
    """Live-test against Aella 12267 confirmed the canonical string for
    Roads to Power adventurer mode is 'landless_adventurer_government'."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Aella",
                "alive_data": {"memories": []},
                "landed_data": {"government": "landless_adventurer_government"},
            }
        }
    )
    char = parse_save(save).characters[1]
    assert char.government == "landless_adventurer_government"


def test_government_none_when_no_landed_data() -> None:
    save = _minimal_save(living={"1": {"first_name": "Unlanded", "alive_data": {"memories": []}}})
    char = parse_save(save).characters[1]
    assert char.government is None


# --- ck3_chronicler-jrwe: decision_cooldowns extracted from landed_data ---


def test_decisions_taken_extracted_from_landed_data() -> None:
    """ck3_chronicler-jrwe: landed_data.decision_cooldowns is a per-
    character {decision_id: cooldown_end_date} dict. The parser
    surfaces each entry as a sorted (id, end_date) tuple so the diff
    layer can detect freshly-taken decisions."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Svend",
                "alive_data": {"memories": []},
                "landed_data": {
                    "government": "feudal_government",
                    "decision_cooldowns": {
                        "raise_stele_decision": "1095.4.1",
                        "hold_court_decision": "1086.7.2",
                    },
                },
            }
        }
    )
    char = parse_save(save).characters[1]
    assert char.decisions_taken == (
        ("hold_court_decision", "1086.7.2"),
        ("raise_stele_decision", "1095.4.1"),
    )


def test_decisions_taken_empty_when_no_cooldowns_block() -> None:
    """Unlanded characters and characters with no decisions on
    cooldown have an empty decisions_taken tuple."""
    save = _minimal_save(
        living={
            "1": {
                "first_name": "Unlanded",
                "alive_data": {"memories": []},
            },
            "2": {
                "first_name": "Landed",
                "alive_data": {"memories": []},
                "landed_data": {"government": "feudal_government"},
            },
        }
    )
    snap = parse_save(save)
    assert snap.characters[1].decisions_taken == ()
    assert snap.characters[2].decisions_taken == ()


# --- ck3_chronicler-nzr: traits_lookup parsed from top-level ---


def test_traits_lookup_parsed_from_top_level() -> None:
    save = _minimal_save(traits_lookup=["education_intrigue_1", "diligent", "ambitious"])
    snap = parse_save(save)
    assert snap.traits_lookup == ("education_intrigue_1", "diligent", "ambitious")


def test_traits_lookup_empty_when_missing() -> None:
    snap = parse_save(_minimal_save())
    assert snap.traits_lookup == ()


# --- ck3_chronicler-667: house / culture / faith name lookups ---


def test_houses_lookup_parsed_from_dynasty_house() -> None:
    save = _minimal_save(
        dynasties={
            "dynasty_house": {
                "100": {"name": "dynn_Briain"},
                "200": {"name": "dynn_Ruaidri"},
                "300": {"head_of_house": 5},  # no name → skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.houses_lookup == {100: "dynn_Briain", 200: "dynn_Ruaidri"}


def test_culture_aware_name_decoding_threads_through_parse_save() -> None:
    """ck3_chronicler-57j end-to-end: parse_save resolves each character's
    culture template via cultures_lookup and threads it into
    decode_ck3_name, so a Breton character's ``HoE_l`` first_name lands
    in the snapshot as ``Hoël`` instead of the lossy ``Hoel``."""
    save = _minimal_save(
        culture_manager={
            "cultures": {
                "42": {"name": "breton", "culture_template": "breton"},
                "43": {"name": "castilian", "culture_template": "castilian"},
            }
        },
        living={
            "1001": {
                "first_name": "HoE_l",
                "birth": "1040.1.1",
                "culture": 42,
                "alive_data": {"memories": []},
            },
            "1002": {
                "first_name": "MarI_a",
                "birth": "1040.1.1",
                "culture": 43,
                "female": True,
                "alive_data": {"memories": []},
            },
        },
    )
    snap = parse_save(save)
    assert snap.characters[1001].first_name == "Hoël"
    assert snap.characters[1002].first_name == "María"


def test_culture_aware_decoding_does_not_regress_norse_at_position_zero() -> None:
    """A Norse character's name with a position-0 capital escape must
    keep decoding via the Anglo-Saxon/Norse map even with culture
    threading active."""
    save = _minimal_save(
        culture_manager={"cultures": {"7": {"name": "norse", "culture_template": "norse"}}},
        living={
            "2001": {
                "first_name": "T_orsteinn",
                "birth": "1020.1.1",
                "culture": 7,
                "alive_data": {"memories": []},
            }
        },
    )
    snap = parse_save(save)
    assert snap.characters[2001].first_name == "Þorsteinn"


def test_cultures_lookup_prefers_name_over_template() -> None:
    save = _minimal_save(
        culture_manager={
            "cultures": {
                "10": {"name": "anglo_saxon", "culture_template": "anglo_saxon"},
                "11": {"culture_template": "norse"},  # name missing → falls back
                "12": {"culture_era_data": []},  # neither → skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.cultures_lookup == {10: "anglo_saxon", 11: "norse"}


def test_faiths_lookup_parsed_from_religion_faiths() -> None:
    save = _minimal_save(
        religion={
            "faiths": {
                "5": {"faith_type": "catholic", "tag": "catholic"},
                "6": {"tag": "asatru"},  # faith_type missing
                "7": {"color": "red"},  # neither
            }
        }
    )
    snap = parse_save(save)
    assert snap.faiths_lookup == {5: "catholic", 6: "asatru"}


def test_lookups_default_empty_when_sections_missing() -> None:
    snap = parse_save(_minimal_save())
    assert snap.houses_lookup == {}
    assert snap.cultures_lookup == {}
    assert snap.faiths_lookup == {}
    assert snap.dynasties_lookup == {}
    assert snap.house_to_dynasty == {}


# --- ck3_chronicler-45i: name fallbacks for custom-named dynasties + houses ---


def test_houses_lookup_falls_back_through_localized_name_custom_name_key() -> None:
    """Verifies the priority chain against the four real shapes seen in CK3
    1.19 saves: engine slug ``name`` (most houses), ``localized_name``
    (Korean-style and player-renamed houses), string ``key`` (Munso etc.),
    and pure ``custom_name``."""
    save = _minimal_save(
        dynasties={
            "dynasty_house": {
                "100": {"name": "dynn_Briain"},
                "200": {"localized_name": "Jiksan Baek"},
                "300": {"key": "house_munso"},
                "400": {"custom_name": "Player Custom"},
                "500": {"key": 12345},  # int key — no localisation layer → skipped
                "600": {"head_of_house": 5},  # no name fields → skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.houses_lookup == {
        100: "dynn_Briain",
        200: "Jiksan Baek",
        300: "house_munso",
        400: "Player Custom",
    }


def test_houses_lookup_localized_name_wins_over_engine_name() -> None:
    """When both fields are present, localized_name (the user-facing string)
    must beat the engine slug — that's the whole point of customisation."""
    save = _minimal_save(
        dynasties={
            "dynasty_house": {
                "100": {"name": "dynn_Briain", "localized_name": "House of Brian"},
            }
        }
    )
    snap = parse_save(save)
    assert snap.houses_lookup == {100: "House of Brian"}


def test_dynasties_lookup_parses_with_same_priority_as_houses() -> None:
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "1": {"name": "dynn_Enochos"},
                "2": {"localized_name": "Séptimo"},
                "3": {"key": "VIET_dreamer_dynasty"},
                "4": {"key": 490},  # integer localisation index — skipped
                "5": {"prestige": {"currency": 100}},  # no name fields — skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasties_lookup == {
        1: "dynn_Enochos",
        2: "Séptimo",
        3: "VIET_dreamer_dynasty",
    }


def test_house_to_dynasty_edge_map_built_from_dynasty_house() -> None:
    """Importer chains house→dynasty to populate Character.dynasty_name."""
    save = _minimal_save(
        dynasties={
            "dynasty_house": {
                "100": {"name": "dynn_Briain", "dynasty": 50},
                "200": {"name": "dynn_Other", "dynasty": 51},
                "300": {"name": "dynn_Orphan"},  # no dynasty edge → skipped
            }
        }
    )
    snap = parse_save(save)
    assert snap.house_to_dynasty == {100: 50, 200: 51}


# --- ck3_chronicler-wdhe: campaign-overview welcome page stats ---


def test_player_currencies_parsed_from_alive_data_dict_shape() -> None:
    """CK3 1.18+ save stores gold/prestige/piety as
    ``{value, accrued}`` dicts. Running balances populate .gold / .prestige
    / .piety; accrued counters populate .prestige_lifetime / .piety_lifetime
    (the welcome page's "gathered" stats). When accrued is missing (piety
    here), piety_lifetime falls back to the running balance."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Erik",
                "alive_data": {
                    "memories": [],
                    "gold": {"value": 240.5, "accrued": 8000.0},
                    "prestige": {"value": 1200.0, "accrued": 12500.0},
                    "piety": {"value": 480.0, "accrued": 3600.0},
                },
            }
        }
    )
    snap = parse_save(save)
    erik = snap.characters[1234]
    assert erik.gold == 240.5
    assert erik.prestige == 1200.0
    assert erik.prestige_lifetime == 12500.0
    assert erik.piety == 480.0
    assert erik.piety_lifetime == 3600.0


def test_player_currencies_parsed_from_alive_data_scalar_shape() -> None:
    """Older save shapes store currency as a flat scalar. Parser falls
    back to coercing the raw number; prestige_lifetime equals the same
    scalar since no ``accrued`` counter is available."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Erik",
                "alive_data": {
                    "memories": [],
                    "gold": 240,
                    "prestige": 1500,
                    "piety": 100,
                },
            }
        }
    )
    snap = parse_save(save)
    erik = snap.characters[1234]
    assert erik.gold == 240.0
    assert erik.prestige == 1500.0
    assert erik.prestige_lifetime == 1500.0
    assert erik.piety == 100.0
    assert erik.piety_lifetime == 100.0


def test_player_currencies_default_none_when_alive_data_missing() -> None:
    """No alive_data at all (dead chars use dead_data, which doesn't
    carry currencies) → all five currency fields stay None."""
    save = _minimal_save(
        living={"1234": {"first_name": "Erik"}}  # no alive_data
    )
    snap = parse_save(save)
    erik = snap.characters[1234]
    assert erik.gold is None
    assert erik.prestige is None
    assert erik.prestige_lifetime is None
    assert erik.piety is None
    assert erik.piety_lifetime is None


def test_dynasties_renown_extracted_from_dynasty_prestige_currency() -> None:
    """Renown is the engine's "prestige" on the dynasty record (renamed
    in-game). Parser keys on dynasties.dynasties[<id>].prestige.currency
    so the welcome page can render the player's dynasty renown."""
    save = _minimal_save(
        dynasties={
            "dynasties": {
                "10": {"name": "dynn_Munso", "prestige": {"currency": 7350.0}},
                "11": {"name": "dynn_Wessex", "prestige": {"currency": 0}},
                "12": {"name": "dynn_Norenown"},  # no prestige record → omitted
            }
        }
    )
    snap = parse_save(save)
    assert snap.dynasties_renown == {10: 7350.0, 11: 0.0}


# --- ck3_chronicler-dr9: titles ---


def test_titles_parsed_with_tier_inferred_from_key() -> None:
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {
                    "key": "e_byzantium",
                    "holder": 100,
                    "title_name_data": {"name": "Byzantium"},
                },
                "2": {"key": "k_france", "holder": 200},
                "3": {
                    "key": "d_munster",
                    "holder": 300,
                    "title_name_data": {"name": "Duchy of Munster"},
                },
                "4": {"key": "c_thomond", "holder": 300},
                "5": {"key": "b_limerick", "holder": 300},
                "6": {"key": "h_holy_special"},  # 'h_' prefix → 'other' tier
            }
        }
    )
    snap = parse_save(save)
    assert len(snap.titles) == 6
    assert snap.titles[1].tier == "empire"
    assert snap.titles[1].name == "Byzantium"
    assert snap.titles[2].tier == "kingdom"
    assert snap.titles[2].name is None  # no title_name_data
    assert snap.titles[3].tier == "duchy"
    assert snap.titles[4].tier == "county"
    assert snap.titles[5].tier == "barony"
    assert snap.titles[6].tier == "other"


def test_title_name_strips_loca_markup_at_parse_time() -> None:
    """ck3_chronicler-4u14: title display names from title_name_data
    can include holder-tooltip wrappers in the same \\x15 markup
    format 90tv surfaced for war_name. Defensive strip at parse time
    so the worldbuilding summariser, biography pipeline, and any
    other downstream consumer see only the clean visible string."""
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {
                    "key": "k_jerusalem",
                    "title_name_data": {
                        "name": (
                            "\x15ONCLICK:TITLE,8755 \x15TOOLTIP:LANDED_TITLE,8755 "
                            "\x15L; Kingdom of Jerusalem\x15!\x15!\x15!"
                        )
                    },
                },
            }
        }
    )
    snap = parse_save(save)
    assert snap.titles[1].name == "Kingdom of Jerusalem"


def test_titles_capture_de_jure_liege() -> None:
    """ck3_chronicler-8ek: de_jure_liege ids on titles are surfaced so
    the worldbuilding summariser can walk the kingdom → empire chain."""
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {"key": "e_scandinavia"},
                "2": {"key": "k_sweden", "de_jure_liege": 1, "holder": 100},
                "3": {"key": "d_uppland", "de_jure_liege": 2, "holder": 100},
                "4": {"key": "c_uppsala", "de_jure_liege": 3, "holder": 100},
                "5": {"key": "k_norway", "de_jure_liege": 1, "holder": 200},
                "6": {"key": "k_orphan", "holder": 300},  # no de_jure_liege
            }
        }
    )
    snap = parse_save(save)
    assert snap.titles[1].de_jure_liege_id is None
    assert snap.titles[2].de_jure_liege_id == 1
    assert snap.titles[4].de_jure_liege_id == 3
    assert snap.titles[6].de_jure_liege_id is None


def test_titles_skipped_when_key_missing() -> None:
    """CK3 backstore rows without a key field are skipped (not surfaced
    as a snapshot entry)."""
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {"key": "k_france", "holder": 100},
                "2": {"holder": 999},  # no key — backstore sentinel
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.titles) == {1}


def test_get_titles_held_by_returns_only_direct_holdings() -> None:
    from chronicler.save.parse import get_titles_held_by

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {"key": "d_munster", "holder": 100},
                "2": {"key": "c_thomond", "holder": 100},
                "3": {"key": "c_other", "holder": 200},
            }
        }
    )
    snap = parse_save(save)
    held = get_titles_held_by(snap, 100)
    assert sorted(t.title_id for t in held) == [1, 2]
    assert get_titles_held_by(snap, 999) == ()


def test_get_primary_title_picks_highest_tier_and_breaks_ties_by_id() -> None:
    """ck3_chronicler-zx2l: empire > kingdom > duchy > county > barony.
    When two titles share the top tier, the smaller title_id wins for
    deterministic ordering across runs."""
    from chronicler.save.parse import get_primary_title_held_by

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                # Mixed-tier holder: kingdom should win.
                "10": {"key": "c_kent", "holder": 100},
                "11": {"key": "d_kent", "holder": 100},
                "12": {"key": "k_england", "holder": 100},
                # Tie-break: two kingdoms; smaller id wins.
                "20": {"key": "k_norway", "holder": 200},
                "19": {"key": "k_denmark", "holder": 200},
            }
        }
    )
    snap = parse_save(save)
    primary_100 = get_primary_title_held_by(snap, 100)
    assert primary_100 is not None
    assert primary_100.key == "k_england"
    assert primary_100.tier == "kingdom"

    primary_200 = get_primary_title_held_by(snap, 200)
    assert primary_200 is not None
    assert primary_200.key == "k_denmark"  # title_id 19 < 20


def test_get_held_titles_sorted_returns_all_grandest_first() -> None:
    """ck3_chronicler-9ngy: the Biographies sidebar/overview must list ALL
    titles held at death, not just the single primary. A ruler holding
    several kingdoms (including a CUSTOM runtime kingdom with a high
    title_id) plus a duchy and a county must surface every one, ordered
    grandest-first. The old get_primary_title_held_by returned only the
    smallest-id top-tier title, which dropped both the other kingdoms and —
    because custom titles get high ids — the player's custom kingdom."""
    from chronicler.save.parse import get_held_titles_sorted

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "5": {"key": "c_kent", "holder": 100},
                "6": {"key": "d_kent", "holder": 100},
                "7": {"key": "k_scotland", "holder": 100},
                "8": {"key": "k_ireland", "holder": 100},
                # custom runtime kingdom — high id, must NOT be dropped
                "90001": {"key": "k_nordreyjar", "holder": 100},
            }
        }
    )
    snap = parse_save(save)
    held = get_held_titles_sorted(snap, 100)
    keys = [t.key for t in held]
    # Every held title present — nothing dropped.
    assert set(keys) == {
        "k_scotland",
        "k_ireland",
        "k_nordreyjar",
        "d_kent",
        "c_kent",
    }
    # Grandest-first; kingdoms (ties by id) before duchy before county. The
    # high-id custom kingdom still ranks among the kingdoms.
    assert keys == ["k_scotland", "k_ireland", "k_nordreyjar", "d_kent", "c_kent"]
    # The primary helper agrees with the head of the sorted list.
    from chronicler.save.parse import get_primary_title_held_by

    primary = get_primary_title_held_by(snap, 100)
    assert primary is not None and primary.key == keys[0]


def test_get_held_titles_sorted_empty_for_landless() -> None:
    from chronicler.save.parse import get_held_titles_sorted

    save = _minimal_save(landed_titles={"landed_titles": {"1": {"key": "k_france", "holder": 50}}})
    snap = parse_save(save)
    assert get_held_titles_sorted(snap, 999) == []


def test_get_primary_title_returns_none_for_landless_character() -> None:
    """Landless / unobserved character → None, so the FE just omits the
    title line rather than rendering a placeholder."""
    from chronicler.save.parse import get_primary_title_held_by

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "1": {"key": "k_france", "holder": 50},
            }
        }
    )
    snap = parse_save(save)
    assert get_primary_title_held_by(snap, 999) is None


def test_x_script_title_under_empire_is_inferred_as_kingdom() -> None:
    """ck3_chronicler-q1ai: player 'Form Kingdom' decisions produce
    runtime ``x_script_*`` titles whose engine key lacks the ``k_``
    prefix. Tier must be inferred from the de_jure_liege chain — an
    x_script title sitting under an empire is a kingdom, and so wins
    the primary-title pick over the holder's subordinate duchies.

    Mirrors the live save of Örvar Sleggja who player-formed the
    'Kingdom of Norðreyjar' (key=x_script_2517) on top of
    d_northern_isles."""
    from chronicler.save.parse import get_primary_title_held_by

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                # Empire — engine-defined, anchors the chain.
                "100": {"key": "e_britannia"},
                # Player-formed kingdom (x_script). Vassals are duchies;
                # de_jure_liege is the empire. Holder is Örvar.
                "200": {
                    "key": "x_script_42",
                    "de_jure_liege": 100,
                    "de_jure_vassals": [300],
                    "holder": 38137,
                    "title_name_data": {"name": "Norðreyjar"},
                },
                # Duchy held by the same character.
                "300": {
                    "key": "d_northern_isles",
                    "de_jure_liege": 200,
                    "holder": 38137,
                },
            }
        }
    )
    snap = parse_save(save)
    assert snap.titles[200].tier == "kingdom"
    primary = get_primary_title_held_by(snap, 38137)
    assert primary is not None
    assert primary.key == "x_script_42"
    assert primary.tier == "kingdom"


def test_x_script_title_with_no_known_parent_falls_back_to_vassal_walk() -> None:
    """Player-formed top-level title (no de_jure_liege, or liege also
    unknown) — fall back to promoting one tier above the grandest
    de_jure_vassals. A custom title whose vassals are duchies is
    still a kingdom."""
    from chronicler.save.parse import get_primary_title_held_by

    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                # Custom title with NO de_jure_liege at all; vassals
                # are duchies — must infer kingdom.
                "200": {
                    "key": "x_script_42",
                    "de_jure_vassals": [300],
                    "holder": 38137,
                },
                "300": {
                    "key": "d_northern_isles",
                    "holder": 38137,
                },
            }
        }
    )
    snap = parse_save(save)
    assert snap.titles[200].tier == "kingdom"
    primary = get_primary_title_held_by(snap, 38137)
    assert primary is not None
    assert primary.tier == "kingdom"


def test_non_tiered_title_with_no_hierarchy_signal_stays_other() -> None:
    """A truly non-tiered custom title (court position, etc.) with no
    de_jure relationships should remain ``"other"`` — these shouldn't
    pretend to be ranked landed titles."""
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "100": {"key": "x_court_chaplain", "holder": 50},
            }
        }
    )
    snap = parse_save(save)
    assert snap.titles[100].tier == "other"


def test_infer_tier_memoises_completed_walk_other() -> None:
    """ck3_chronicler-27ov.76 (audit L3): a title that resolves to
    ``"other"`` only after a full parent+vassal walk caches that result,
    so it isn't re-walked each time it's referenced as another title's
    liege/vassal (quadratic on modded saves full of non-tiered titles)."""
    raws = {1: {"key": "x_court_position"}}  # no liege, no vassals
    memo: dict[int, str] = {}
    assert _infer_tier_for_title(1, raws, memo, set()) == "other"
    assert memo == {1: "other"}  # the completed-walk fall-through is cached


def test_infer_tier_breaks_de_jure_cycle_without_recursion_error() -> None:
    """A pathological de_jure cycle (1 -> 2 -> 1, neither key-tiered)
    must terminate via the inflight guard rather than recursing forever;
    both titles settle to ``"other"``."""
    raws = {
        1: {"key": "x_a", "de_jure_liege": 2},
        2: {"key": "x_b", "de_jure_liege": 1},
    }
    memo: dict[int, str] = {}
    assert _infer_tier_for_title(1, raws, memo, set()) == "other"
    assert memo[1] == "other"


# --- ck3_chronicler-9wrd: epidemics ---


def test_parse_epidemics_builds_metadata_and_per_character_lookup() -> None:
    """Verified against autosave.ck3: epidemics.database holds active
    plagues with type/name/intensity + a 'characters' list of infected
    char IDs. Parser builds (epidemics, character_to_epidemics) the
    same way wars / character_to_wars work."""
    save = _minimal_save(
        epidemics={
            "database": {
                "67108866": {
                    "type": "measles",
                    "intensity": "major",
                    "creation_date": "1083.4.4",
                    "name": "Yamato Boils",
                    "start_province": 10678,
                    "max_provinces": 149,
                    "num_infected_provinces": 155,
                    "num_infected_characters": 880,
                    "num_character_deaths": 121,
                    "characters": [31341, 37993, 39943],
                }
            }
        }
    )
    snap = parse_save(save)
    assert len(snap.epidemics) == 1
    ep = snap.epidemics[67108866]
    assert ep.epidemic_type == "measles"
    assert ep.name == "Yamato Boils"
    assert ep.intensity == "major"
    assert ep.creation_date == "1083.4.4"
    assert ep.num_infected_provinces == 155
    assert ep.num_character_deaths == 121
    # All three listed chars are now in the per-char lookup, all
    # pointing at the same epidemic_id.
    assert snap.character_to_epidemics[31341] == frozenset({67108866})
    assert snap.character_to_epidemics[37993] == frozenset({67108866})
    assert snap.character_to_epidemics[39943] == frozenset({67108866})


def test_parse_epidemics_skips_non_dict_database_entries() -> None:
    """epidemics.database carries sentinel string entries alongside the
    dict ones (observed live: 4 entries / 3 dicts / 1 bare string).
    Non-dict values must be silently skipped, not crash."""
    save = _minimal_save(
        epidemics={
            "database": {
                "83886080": "sentinel",
                "67108866": {
                    "type": "measles",
                    "intensity": "major",
                    "creation_date": "1083.4.4",
                    "name": "Yamato Boils",
                    "characters": [],
                    "num_infected_provinces": 0,
                    "num_infected_characters": 0,
                    "num_character_deaths": 0,
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.epidemics) == {67108866}


def test_parse_epidemics_empty_when_no_block() -> None:
    """No epidemics in save → empty parallel structures, not crash."""
    snap = parse_save(_minimal_save())
    assert snap.epidemics == {}
    assert snap.character_to_epidemics == {}


# --- ck3_chronicler-2ur: in-flight constructions + completed buildings ---


def test_parse_constructions_builds_in_flight_and_per_character_lookup() -> None:
    """Verified against autosave.ck3 1067.4: per-holding constructions
    live at provinces[pid].holding.constructions with
    {building, index, start_time, days, character} — the parser builds
    a flat (province_id, slot_index) keyed map plus a per-character
    reverse index, mirroring the wars / epidemics shape."""
    save = _minimal_save(
        provinces={
            "280": {
                "holding": {
                    "type": "tribal_holding",
                    "buildings": [{"type": "tribe_02"}, [], [], []],
                    "constructions": {
                        "building": "longhouses_01",
                        "index": 3,
                        "start_time": "1066.9.24",
                        "days": 1782.7,
                        "cost": {"gold": 69.375, "prestige": 200},
                        "character": 36700,
                    },
                }
            },
            "461": {
                "holding": {
                    "type": "tribal_holding",
                    "buildings": [],
                    "constructions": {
                        "building": "pastures_01",
                        "index": 3,
                        "start_time": "1066.9.24",
                        "days": 654,
                        "character": 32502,
                    },
                }
            },
        }
    )
    snap = parse_save(save)
    assert (280, 3) in snap.in_flight_constructions
    cons = snap.in_flight_constructions[(280, 3)]
    assert cons.building == "longhouses_01"
    assert cons.start_date == "1066.9.24"
    assert cons.character_id == 36700
    # Per-character reverse index points the constructor at their
    # in-flight slot key.
    assert snap.character_to_constructions[36700] == frozenset({(280, 3)})
    assert snap.character_to_constructions[32502] == frozenset({(461, 3)})


def test_parse_holding_buildings_by_slot_skips_empty_placeholders() -> None:
    """CK3 emits empty slots as ``[]`` (or ``{}``) inside the buildings
    list. The completed-buildings flat lookup must skip those so a slot
    only appears when something is actually built — that's what the
    diff layer keys against to distinguish a shipped construction from
    a cancelled one."""
    save = _minimal_save(
        provinces={
            "2": {
                "holding": {
                    "type": "tribal_holding",
                    "buildings": [
                        {"type": "tribe_02"},
                        {"type": "common_tradeport_01"},
                        {"type": "longhouses_01"},
                        [],  # empty slot 3 — must be skipped
                    ],
                }
            }
        }
    )
    snap = parse_save(save)
    assert snap.holding_buildings_by_slot == {
        (2, 0): "tribe_02",
        (2, 1): "common_tradeport_01",
        (2, 2): "longhouses_01",
    }


def test_parse_constructions_skips_malformed_entries() -> None:
    """Province / holding / construction entries missing required
    fields (building or index) are silently skipped — chronicler's
    'never stop on bad data' rule. Only well-formed records land in
    the in-flight map."""
    save = _minimal_save(
        provinces={
            "1": {"holding": {}},  # no buildings, no constructions
            "2": "not-a-dict",  # malformed province
            "3": {
                "holding": {
                    "constructions": {"building": "x"},  # missing index
                }
            },
            "4": {
                "holding": {
                    "constructions": {"index": 0},  # missing building
                }
            },
            "5": {
                "holding": {
                    "buildings": [],
                    "constructions": {
                        "building": "longhouses_01",
                        "index": 3,
                        "character": 36700,
                    },
                }
            },
        }
    )
    snap = parse_save(save)
    # Only province 5's well-formed record survives.
    assert set(snap.in_flight_constructions) == {(5, 3)}


def test_parse_constructions_empty_when_no_provinces_block() -> None:
    """No provinces in save → empty parallel structures, not crash."""
    snap = parse_save(_minimal_save())
    assert snap.in_flight_constructions == {}
    assert snap.character_to_constructions == {}
    assert snap.holding_buildings_by_slot == {}


# --- ck3_chronicler-qx7n: 8aie slice 1, activity_manager.database ---


def test_parse_activities_builds_metadata_and_per_character_lookup() -> None:
    """Verified against autosave.ck3 914.2.1: activity_manager.database
    holds active activities with type/host/attending/phases. Parser
    mirrors the wars / epidemics shape: a metadata dict keyed by
    activity_id and a per-character reverse index (host + attendees).
    """
    save = _minimal_save(
        activity_manager={
            "database": {
                "1157627904": {
                    "type": "activity_pilgrimage",
                    "host": 16795788,
                    "attending": [67208, 33605696],
                    "creation_date": "914.10.21",
                    "active_start_date": "916.1.4",
                    "phases": [{"phase": "pilgrimage_phase_solo", "province": 6223}],
                },
                "1124073474": {
                    "type": "activity_feast",
                    "host": 16802487,
                    "attending": [45445, 48600, 62249],
                    "creation_date": "914.11.29",
                    "active_start_date": "915.4.16",
                    "phases": [{"phase": "feast_phase", "province": 1328}],
                },
            }
        }
    )
    snap = parse_save(save)
    assert len(snap.activities) == 2

    pilgrimage = snap.activities[1157627904]
    assert pilgrimage.activity_type == "activity_pilgrimage"
    assert pilgrimage.host_id == 16795788
    assert pilgrimage.creation_date == "914.10.21"
    assert pilgrimage.active_start_date == "916.1.4"
    assert pilgrimage.start_province_id == 6223
    assert pilgrimage.attendees == frozenset({67208, 33605696})

    feast = snap.activities[1124073474]
    assert feast.activity_type == "activity_feast"
    assert feast.host_id == 16802487
    assert feast.start_province_id == 1328

    # Per-character reverse index covers BOTH host and attendees.
    assert snap.character_to_activities[16795788] == frozenset({1157627904})
    assert snap.character_to_activities[67208] == frozenset({1157627904})
    assert snap.character_to_activities[33605696] == frozenset({1157627904})
    assert snap.character_to_activities[16802487] == frozenset({1124073474})
    assert snap.character_to_activities[45445] == frozenset({1124073474})


def test_parse_activities_handles_missing_attending_and_phases() -> None:
    """Verified shape gaps from autosave.ck3 914.2.1: some activity
    records (e.g. activity_adult_education) omit ``attending`` entirely
    and have minimal phases. Parser must tolerate both — fall back to
    just the host in the reverse index, leave start_province_id None
    when no phases or no province field. No crash on missing keys."""
    save = _minimal_save(
        activity_manager={
            "database": {
                "1157627905": {
                    "type": "activity_adult_education",
                    "host": 16836824,
                    # no 'attending' key
                    "creation_date": "914.10.13",
                    "active_start_date": "915.3.15",
                    "phases": [{"phase": "education_study_phase"}],  # no province
                },
                "1157627906": {
                    "type": "activity_hunt",
                    "host": 12345,
                    "attending": [],
                    # no 'phases' key
                },
            }
        }
    )
    snap = parse_save(save)
    assert len(snap.activities) == 2
    edu = snap.activities[1157627905]
    assert edu.host_id == 16836824
    assert edu.start_province_id is None
    assert edu.attendees == frozenset()
    hunt = snap.activities[1157627906]
    assert hunt.host_id == 12345
    assert hunt.start_province_id is None
    assert hunt.attendees == frozenset()
    # Only the hosts land in the reverse index — no attendees to add.
    assert snap.character_to_activities[16836824] == frozenset({1157627905})
    assert snap.character_to_activities[12345] == frozenset({1157627906})


def test_parse_activities_skips_non_dict_entries() -> None:
    """Same defensive-parse pattern as _parse_epidemics: if
    activity_manager.database carries a non-dict sentinel alongside
    the real records, skip it silently rather than crash."""
    save = _minimal_save(
        activity_manager={
            "database": {
                "99": "sentinel",
                "1157627904": {
                    "type": "activity_feast",
                    "host": 100,
                    "attending": [],
                    "phases": [],
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.activities) == {1157627904}


def test_parse_activities_empty_when_no_block() -> None:
    """No activity_manager in save → empty parallel structures, not crash."""
    snap = parse_save(_minimal_save())
    assert snap.activities == {}
    assert snap.character_to_activities == {}


# --- ck3_chronicler-621o: 8aie slice 6, task_contracts.database ---


def test_parse_contracts_builds_metadata_and_owner_lookup() -> None:
    """Verified shape from 2026-05-17 Genji adventurer smoke: a
    task_contracts.database entry has type, name, tier, employer,
    owner, location, status, and (when applicable) acceptance_date /
    completion_date. The reverse index keys by owner_id (the
    adventurer holding the contract)."""
    save = _minimal_save(
        task_contracts={
            "database": {
                "0": {
                    "type": "laamp_base_6021",
                    "name": "Perform in a Play",
                    "tier": 2,
                    "employer": 27944,
                    "owner": 61302,
                    "location": 10621,
                    "status": "accepted",
                    "acceptance_date": "1069.4.28",
                },
                "1": {
                    "type": "laamp_base_1001",
                    "name": "Settle Boundary Dispute",
                    "tier": 1,
                    "employer": 45877,
                    "owner": 61302,
                    "location": 9903,
                    "status": "completed",
                    "acceptance_date": "1066.12.5",
                    "completion_date": "1067.4.1",
                },
                "2": {
                    "type": "laamp_join_war_contract",
                    "name": "Join the War",
                    "tier": 3,
                    "employer": 30240,
                    "owner": 99999,  # different adventurer
                    "location": 1901,
                    "status": "available",
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.task_contracts) == {0, 1, 2}
    play = snap.task_contracts[0]
    assert play.contract_type == "laamp_base_6021"
    assert play.name == "Perform in a Play"
    assert play.tier == 2
    assert play.employer_id == 27944
    assert play.owner_id == 61302
    assert play.location_province_id == 10621
    assert play.status == "accepted"
    assert play.acceptance_date == "1069.4.28"
    assert play.completion_date is None
    done = snap.task_contracts[1]
    assert done.status == "completed"
    assert done.completion_date == "1067.4.1"
    # Reverse index keys by owner_id; player has both contracts 0 and 1.
    assert snap.character_to_contracts[61302] == frozenset({0, 1})
    assert snap.character_to_contracts[99999] == frozenset({2})


def test_parse_contracts_skips_records_without_status() -> None:
    """status is the load-bearing diff signal — a record without one
    can't be classified, so the parser drops it (same defensive-skip
    contract as the sibling parsers)."""
    save = _minimal_save(
        task_contracts={
            "database": {
                "0": {"type": "laamp_base_0001", "owner": 1},  # no status
                "1": {"type": "laamp_base_0001", "owner": 1, "status": ""},  # empty status
                "2": {"type": "laamp_base_0001", "owner": 1, "status": "accepted"},
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.task_contracts) == {2}


def test_parse_contracts_skips_non_dict_entries() -> None:
    """Same defensive-parse pattern as _parse_activities."""
    save = _minimal_save(
        task_contracts={
            "database": {
                "0": "sentinel",
                "1": {"type": "laamp_base_0001", "owner": 1, "status": "accepted"},
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.task_contracts) == {1}


def test_parse_contracts_empty_when_no_block() -> None:
    """No task_contracts in save → empty parallel structures."""
    snap = parse_save(_minimal_save())
    assert snap.task_contracts == {}
    assert snap.character_to_contracts == {}


# --- ck3_chronicler-mke9: 8aie slice 7, court_positions.database ---


def test_parse_court_positions_builds_metadata_and_employer_lookup() -> None:
    """Verified shape from 2026-05-17 Genji adventurer smoke: court
    positions live at top-level court_positions.database with
    {court_position, employee, employer, hire_date}. The reverse
    index keys by employer_id (the camp/court owner)."""
    save = _minimal_save(
        court_positions={
            "database": {
                "154": {
                    "court_position": "travel_leader_court_position",
                    "employee": 63547,
                    "employer": 61302,
                    "hire_date": "1066.9.15",
                },
                "155": {
                    "court_position": "second_camp_officer",
                    "employee": 63546,
                    "employer": 61302,
                    "hire_date": "1066.9.15",
                },
                "16779388": {
                    "court_position": "bodyguard_court_position",
                    "employee": 63546,
                    "employer": 61302,
                    "hire_date": "1069.1.20",
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.court_positions) == {154, 155, 16779388}
    leader = snap.court_positions[154]
    assert leader.court_position == "travel_leader_court_position"
    assert leader.employee_id == 63547
    assert leader.employer_id == 61302
    assert leader.hire_date == "1066.9.15"
    # Same employee (63546) holds two positions simultaneously — both
    # land in the reverse index keyed by employer.
    assert snap.character_to_court_positions[61302] == frozenset({154, 155, 16779388})


def test_parse_court_positions_skips_records_without_employer() -> None:
    """employer is the load-bearing anchor for the reverse index —
    drop records that don't have one (orphaned slot data)."""
    save = _minimal_save(
        court_positions={
            "database": {
                "1": {"court_position": "x", "employee": 100},  # no employer
                "2": {
                    "court_position": "y",
                    "employee": 200,
                    "employer": 99,
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.court_positions) == {2}


def test_parse_court_positions_skips_non_dict_entries() -> None:
    """Defensive-parse pattern."""
    save = _minimal_save(
        court_positions={
            "database": {
                "1": "sentinel",
                "2": {
                    "court_position": "bodyguard_court_position",
                    "employee": 200,
                    "employer": 99,
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.court_positions) == {2}


def test_parse_court_positions_empty_when_no_block() -> None:
    """No court_positions in save → empty parallel structures."""
    snap = parse_save(_minimal_save())
    assert snap.court_positions == {}
    assert snap.character_to_court_positions == {}


# --- ck3_chronicler-r343: 8aie slice 5, domiciles.database ---


def test_parse_domiciles_builds_metadata_and_character_lookup() -> None:
    """Verified shape from 2026-05-17 Genji adventurer smoke: domicile
    entries have province / owner_title / domicile_type. The reverse
    index joins via owner_title -> title-holder so consumers don't
    have to walk landed_data.domain themselves."""
    save = _minimal_save(
        landed_titles={
            "landed_titles": {
                "18017": {
                    "key": "x_adventurer_genji",
                    "holder": 61302,
                },
            }
        },
        domiciles={
            "database": {
                "852": {
                    "owner_title": 18017,
                    "domicile_type": "camp",
                    "province": 9822,
                },
                "853": {
                    "owner_title": 99999,  # no matching title -> still parsed
                    "domicile_type": "east_asian_estate",
                    "province": 1234,
                },
            }
        },
    )
    snap = parse_save(save)
    assert set(snap.domiciles) == {852, 853}
    cam = snap.domiciles[852]
    assert cam.owner_title_id == 18017
    assert cam.domicile_type == "camp"
    assert cam.province_id == 9822
    # Reverse index joins through title -> holder. Player (61302) has
    # a domicile via title 18017.
    assert snap.character_to_domicile[61302] == 852
    # Domicile 853 has no resolvable holder — no character entry.
    assert 853 not in snap.character_to_domicile.values()


def test_parse_domiciles_skips_records_without_owner_title() -> None:
    """owner_title is the load-bearing join key for the reverse index
    — orphan records (no owner_title) are dropped."""
    save = _minimal_save(
        domiciles={
            "database": {
                "1": {"domicile_type": "camp", "province": 1000},  # no owner_title
                "2": {
                    "owner_title": 5,
                    "domicile_type": "camp",
                    "province": 2000,
                },
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.domiciles) == {2}


def test_parse_domiciles_skips_non_dict_entries() -> None:
    """Defensive-parse pattern."""
    save = _minimal_save(
        domiciles={
            "database": {
                "1": "sentinel",
                "2": {"owner_title": 5, "domicile_type": "camp", "province": 1000},
            }
        }
    )
    snap = parse_save(save)
    assert set(snap.domiciles) == {2}


def test_parse_domiciles_empty_when_no_block() -> None:
    """No domiciles in save → empty parallel structures."""
    snap = parse_save(_minimal_save())
    assert snap.domiciles == {}
    assert snap.character_to_domicile == {}


# --- ck3_chronicler-tmjn: _resolve_county_vassals ---


def _make_title(
    *,
    title_id: int,
    key: str,
    tier: str,
    holder_id: int | None,
    de_jure_liege_id: int | None = None,
) -> TitleSnapshot:
    """tmjn test helper."""
    return TitleSnapshot(
        title_id=title_id,
        key=key,
        name=None,
        tier=tier,
        holder_id=holder_id,
        de_jure_liege_id=de_jure_liege_id,
    )


def _snap_with_titles(
    *,
    player_id: int,
    titles: dict[int, TitleSnapshot],
) -> SaveSnapshot:
    """tmjn test helper — minimal SaveSnapshot with given titles.

    _resolve_county_vassals only reads snap.titles and player_id, so
    characters can be empty here."""
    return SaveSnapshot(
        playthrough_id="tmjn-test",
        ck3_version="1.19.0.4",
        bookmark_date=None,
        current_date="1066.9.15",
        player_character_id=player_id,
        characters={},
        titles=titles,
    )


def test_resolve_county_vassals_no_titles_returns_empty() -> None:
    snap = _snap_with_titles(player_id=100, titles={})
    assert _resolve_county_vassals(snap, player_id=100) == []


def test_resolve_county_vassals_player_holds_only_counties_returns_empty() -> None:
    titles = {
        1: _make_title(title_id=1, key="c_munster", tier="county", holder_id=100),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert _resolve_county_vassals(snap, player_id=100) == []


def test_resolve_county_vassals_player_holds_duchy_with_two_county_vassals() -> None:
    titles = {
        1: _make_title(title_id=1, key="d_munster", tier="duchy", holder_id=100),
        2: _make_title(title_id=2, key="c_cork", tier="county", holder_id=201, de_jure_liege_id=1),
        3: _make_title(title_id=3, key="c_kerry", tier="county", holder_id=202, de_jure_liege_id=1),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert sorted(_resolve_county_vassals(snap, player_id=100)) == [201, 202]


def test_resolve_county_vassals_player_holds_kingdom_with_mixed_realm() -> None:
    # Player holds k_france. Realm:
    #   d_normandy (held by 200) → de_jure_liege k_france
    #     c_rouen  (held by 401) → de_jure_liege d_normandy
    #     c_caen   (held by 402) → de_jure_liege d_normandy
    #   d_anjou    (held by 200) → de_jure_liege k_france
    #     c_angers (held by 403) → de_jure_liege d_anjou
    # Vassals: 401, 402, 403. Duke 200 NOT a vassal (only county-tier).
    titles = {
        1: _make_title(title_id=1, key="k_france", tier="kingdom", holder_id=100),
        2: _make_title(
            title_id=2, key="d_normandy", tier="duchy", holder_id=200, de_jure_liege_id=1
        ),
        3: _make_title(title_id=3, key="d_anjou", tier="duchy", holder_id=200, de_jure_liege_id=1),
        4: _make_title(title_id=4, key="c_rouen", tier="county", holder_id=401, de_jure_liege_id=2),
        5: _make_title(title_id=5, key="c_caen", tier="county", holder_id=402, de_jure_liege_id=2),
        6: _make_title(
            title_id=6, key="c_angers", tier="county", holder_id=403, de_jure_liege_id=3
        ),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert sorted(_resolve_county_vassals(snap, player_id=100)) == [401, 402, 403]


def test_resolve_county_vassals_player_held_county_excluded() -> None:
    titles = {
        1: _make_title(title_id=1, key="d_munster", tier="duchy", holder_id=100),
        2: _make_title(title_id=2, key="c_cork", tier="county", holder_id=100, de_jure_liege_id=1),
        3: _make_title(title_id=3, key="c_kerry", tier="county", holder_id=201, de_jure_liege_id=1),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert _resolve_county_vassals(snap, player_id=100) == [201]


def test_resolve_county_vassals_cycle_does_not_loop() -> None:
    titles = {
        1: _make_title(title_id=1, key="d_munster", tier="duchy", holder_id=100),
        2: _make_title(title_id=2, key="c_a", tier="county", holder_id=201, de_jure_liege_id=3),
        3: _make_title(title_id=3, key="c_b", tier="county", holder_id=202, de_jure_liege_id=2),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert _resolve_county_vassals(snap, player_id=100) == []


def test_resolve_county_vassals_deep_chain_terminates_at_max_depth() -> None:
    titles = {
        1: _make_title(title_id=1, key="d_munster", tier="duchy", holder_id=100),
        2: _make_title(title_id=2, key="c_a", tier="county", holder_id=201, de_jure_liege_id=3),
        3: _make_title(title_id=3, key="x_b", tier="other", holder_id=None, de_jure_liege_id=4),
        4: _make_title(title_id=4, key="x_c", tier="other", holder_id=None, de_jure_liege_id=5),
        5: _make_title(title_id=5, key="x_d", tier="other", holder_id=None, de_jure_liege_id=6),
        6: _make_title(title_id=6, key="x_e", tier="other", holder_id=None, de_jure_liege_id=7),
        7: _make_title(title_id=7, key="x_f", tier="other", holder_id=None, de_jure_liege_id=None),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)
    assert _resolve_county_vassals(snap, player_id=100) == []


# --- ck3_chronicler-8wa5: grandchild auto-track ---


def test_auto_track_includes_grandchildren_by_default() -> None:
    """include_grandchildren=True (default) walks player's children's children."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {"child": [9001], "mother": 4001, "father": 4002},
                "alive_data": {"memories": []},
            },
            "9001": {
                "first_name": "Child",
                "family_data": {"child": [9901, 9902]},
                "alive_data": {"memories": []},
            },
        }
    )
    snap = parse_save(save)
    out = auto_track_candidates(snap)
    ids = [cid for cid, _ in out]
    assert 9901 in ids
    assert 9902 in ids
    notes = dict(out)
    assert notes[9901] == "grandchild"
    assert notes[9902] == "grandchild"


def test_auto_track_grandchildren_off_excludes_grandchildren() -> None:
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {"child": [9001]},
                "alive_data": {"memories": []},
            },
            "9001": {
                "first_name": "Child",
                "family_data": {"child": [9901]},
                "alive_data": {"memories": []},
            },
        }
    )
    snap = parse_save(save)
    out = auto_track_candidates(snap, rules={"include_grandchildren": False})
    ids = [cid for cid, _ in out]
    assert 9001 in ids
    assert 9901 not in ids


def test_auto_track_grandchild_deduped_if_also_child() -> None:
    """Edge case: if a grandchild ID somehow also appears as a direct child,
    deduplication must keep only one entry."""
    save = _minimal_save(
        living={
            "1234": {
                "first_name": "Player",
                "family_data": {"child": [9001, 9901]},
                "alive_data": {"memories": []},
            },
            "9001": {
                "first_name": "Child",
                "family_data": {"child": [9901]},
                "alive_data": {"memories": []},
            },
        }
    )
    snap = parse_save(save)
    out = auto_track_candidates(snap)
    ids = [cid for cid, _ in out]
    assert ids.count(9901) == 1


# --- ck3_chronicler-tmjn: auto_track_candidates vassal support ---


def test_auto_track_candidates_includes_vassals_when_rule_set() -> None:
    """ck3_chronicler-tmjn: include_county_vassals=True surfaces vassals
    in the auto-track output with note='vassal'. False (default) leaves
    them out."""
    titles = {
        1: _make_title(title_id=1, key="d_munster", tier="duchy", holder_id=100),
        2: _make_title(title_id=2, key="c_cork", tier="county", holder_id=201, de_jure_liege_id=1),
        3: _make_title(title_id=3, key="c_kerry", tier="county", holder_id=202, de_jure_liege_id=1),
    }
    snap = _snap_with_titles(player_id=100, titles=titles)

    # Default rules: vassals excluded.
    default_result = auto_track_candidates(snap)
    assert not any(note == "vassal" for _cid, note in default_result)

    # Flag on: vassals included with note='vassal'.
    on_result = auto_track_candidates(snap, rules={"include_county_vassals": True})
    vassal_ids = sorted(cid for cid, note in on_result if note == "vassal")
    assert vassal_ids == [201, 202]


def test_auto_track_candidates_dedupes_vassal_who_is_also_relative() -> None:
    """ck3_chronicler-tmjn: a vassal who's also the player's child is
    added once with the first-attached note (heirs branch runs before
    vassals, so 'child' wins)."""
    # Build via parse_save so we get a real CharacterSnapshot for the player
    # with child=201, combined with landed_titles that make 201 a county vassal.
    save = _minimal_save(
        meta_data={
            "version": "1.19.0.4",
            "meta_date": "1066.9.15",
            "meta_main_portrait": {"id": 100},
        },
        living={
            "100": {
                "first_name": "Player",
                "family_data": {"child": [201]},
                "alive_data": {"memories": []},
            }
        },
        landed_titles={
            "landed_titles": {
                "1": {"key": "d_munster", "holder": 100},
                "2": {"key": "c_cork", "holder": 201, "de_jure_liege": 1},
            }
        },
    )
    snap = parse_save(save)

    result = auto_track_candidates(
        snap, rules={"include_county_vassals": True, "include_heirs": True}
    )
    entries_for_201 = [(cid, note) for cid, note in result if cid == 201]
    assert entries_for_201 == [(201, "child")]


# --- ck3_chronicler-r3fs: CK3 canonical name localization lookup ---


def test_first_name_uses_ck3_loca_when_available(monkeypatch) -> None:
    """When CK3's name localization resolves the escape token, the parser
    uses the canonical Unicode name instead of the lossy heuristic.

    'A_slaug' with no culture decodes heuristically to 'Aslaug' (the
    underscore-escape is stripped lossily); the loca says 'Áslaug'. The
    loca must win.
    """
    import chronicler.save.ck3_names as ck3_names_mod

    # Patch the loca-map lookup at its source; resolve_character_name
    # (called by the parser) composes it with the heuristic fallback.
    monkeypatch.setattr(
        ck3_names_mod,
        "resolve_name",
        lambda token: "Áslaug" if token == "A_slaug" else None,
    )
    save = _minimal_save(
        living={
            "7001": {
                "first_name": "A_slaug",
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    assert snap.characters[7001].first_name == "Áslaug"


def test_first_name_falls_back_to_heuristic_on_loca_miss(monkeypatch) -> None:
    """When the loca has no entry for the token (mod name, missing game
    install, CI), the parser falls back to decode_ck3_name. 'E_lla' →
    'Ælla' via the culture-blind escape map."""
    import chronicler.save.ck3_names as ck3_names_mod

    monkeypatch.setattr(ck3_names_mod, "resolve_name", lambda token: None)
    save = _minimal_save(
        living={
            "7002": {
                "first_name": "E_lla",
                "alive_data": {"memories": []},
            }
        }
    )
    snap = parse_save(save)
    assert snap.characters[7002].first_name == "Ælla"


# ---------------------------------------------------------------------------
# _sanity_log (M-S3, ck3_chronicler-27ov.18): hollow-parse observability
# ---------------------------------------------------------------------------


def _real_sized_save(**overrides) -> dict:
    """A _minimal_save padded to real-save top-level key count so the
    sanity log treats it as a genuine save rather than a fixture."""
    save = _minimal_save(**overrides)
    for i in range(25):
        save.setdefault(f"padding_section_{i}", {})
    return save


def test_sanity_log_warns_on_hollow_real_sized_save(caplog) -> None:
    """A real-looking save (many top-level keys) that parses to zero
    characters/titles must emit a WARNING pointing at format drift."""
    import logging

    save = _real_sized_save()
    save["meta_data"] = {}  # no player portrait either
    with caplog.at_level(logging.WARNING, logger="chronicler.save.parse"):
        parse_save(save)
    drift_warnings = [r for r in caplog.records if "format drift" in r.message]
    assert len(drift_warnings) == 1
    msg = drift_warnings[0].message
    assert "0 characters" in msg
    assert "0 titles" in msg
    assert "player_character_id" in msg


def test_sanity_log_quiet_on_small_fixture(caplog) -> None:
    """Synthetic test fixtures (few top-level keys) never warn."""
    import logging

    with caplog.at_level(logging.WARNING, logger="chronicler.save.parse"):
        parse_save(_minimal_save())
    assert not [r for r in caplog.records if "format drift" in r.message]


def test_sanity_log_quiet_on_healthy_real_sized_save(caplog) -> None:
    """A real-sized save with characters + titles + player id stays quiet."""
    import logging

    save = _real_sized_save(
        living={"1234": {"first_name": "Test"}},
        landed_titles={"landed_titles": {"1": {"key": "k_test", "name": "Test"}}},
    )
    with caplog.at_level(logging.WARNING, logger="chronicler.save.parse"):
        parse_save(save)
    assert not [r for r in caplog.records if "format drift" in r.message]


# --- #64: a player-less save is not format drift ----------------------------


def _real_sized_save(**overrides) -> dict:
    """A save with enough top-level roots to clear the hollow-check guard.

    ``_check_snapshot_sanity`` ignores anything smaller, so a synthetic
    save must be padded before it can assert on the warning at all.
    """
    base = _minimal_save(**overrides)
    for n in range(_REAL_SAVE_MIN_TOP_LEVEL_KEYS + 5):
        base.setdefault(f"_pad_{n}", {})
    return base


def _populated_save(**overrides) -> dict:
    """A save whose character and title roots parsed fine."""
    base = _real_sized_save(
        living={"1234": {"first_name": "Ancel", "birth": "1040.1.1"}},
        landed_titles={"landed_titles": {"1": {"key": "c_rethel"}}},
    )
    for k, v in overrides.items():
        base[k] = v
    return base


def test_missing_player_alone_is_not_reported_as_drift(caplog) -> None:
    """CK3 writes autosave_exit.ck3 with no player. That is normal.

    Calling it "format drift" sent one investigation hunting a renamed
    save format that did not exist, so the two cases are now distinct.
    """
    save = _populated_save()
    save["meta_data"] = {k: v for k, v in save["meta_data"].items() if k != "meta_main_portrait"}
    save.pop("played_character", None)

    with caplog.at_level(logging.INFO):
        snap = parse_save(save)

    assert snap.player_character_id is None
    text = caplog.text
    assert "no player character in this save" in text
    assert "format drift" not in text


def test_empty_character_root_still_reports_drift(caplog) -> None:
    """A root that every save must carry coming back empty is drift."""
    save = _real_sized_save()  # no living/landed_titles content at all

    with caplog.at_level(logging.WARNING):
        parse_save(save)

    assert "format drift" in caplog.text


def test_saves_by_recency_is_newest_first(tmp_path) -> None:
    """#64: auto-track needs more than the single newest save.

    The newest file is ``autosave_exit.ck3`` whenever CK3 has just been
    quit, and that one never carries a player.
    """
    import os

    from chronicler.save import DEFAULT_SAVE_PATTERN, latest_save, saves_by_recency

    for n, name in enumerate(["oldest.ck3", "middle.ck3", "autosave_exit.ck3"]):
        f = tmp_path / name
        f.write_text("x")
        os.utime(f, (1_700_000_000 + n * 60, 1_700_000_000 + n * 60))

    ordered = saves_by_recency(tmp_path, DEFAULT_SAVE_PATTERN)
    assert [p.name for p in ordered] == ["autosave_exit.ck3", "middle.ck3", "oldest.ck3"]
    # the newest entry still agrees with the single-answer helper
    assert ordered[0] == latest_save(tmp_path, DEFAULT_SAVE_PATTERN)
    assert saves_by_recency(tmp_path / "nope", DEFAULT_SAVE_PATTERN) == []

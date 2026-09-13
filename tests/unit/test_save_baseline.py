"""Unit tests for save-tail baseline persistence (ck3_chronicler-96m).

Covers SaveSnapshot ↔ JSON roundtrip, atomic writes, and graceful
handling of missing or corrupt baseline files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronicler.save.baseline import (
    baseline_path_for,
    delete_baseline_if_exists,
    load_baseline,
    save_baseline,
)
from chronicler.save.parse import (
    ActivitySnapshot,
    ArtifactSnapshot,
    CharacterSnapshot,
    ConstructionSnapshot,
    ContractSnapshot,
    CourtPositionSnapshot,
    DomicileSnapshot,
    EpidemicSnapshot,
    FamilySnapshot,
    InspirationSnapshot,
    MemorySnapshot,
    SaveSnapshot,
    TitleSnapshot,
    WarSnapshot,
)
from tests.helpers.snapshots import make_char


def _char(cid: int, **overrides) -> CharacterSnapshot:
    return make_char(cid, **overrides)


def _snap(chars: dict[int, CharacterSnapshot], **overrides) -> SaveSnapshot:
    base = dict(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=next(iter(chars), None) if chars else None,
        characters=chars,
    )
    base.update(overrides)
    return SaveSnapshot(**base)


def test_baseline_path_for_replaces_db_suffix(tmp_path: Path) -> None:
    db = tmp_path / "campaigns" / "eadmund.db"
    assert baseline_path_for(db) == tmp_path / "campaigns" / "eadmund.baseline.json"


def test_baseline_path_for_no_suffix(tmp_path: Path) -> None:
    db = tmp_path / "campaign-no-ext"
    # with_suffix on a no-suffix path appends; verify the result is still .baseline.json
    out = baseline_path_for(db)
    assert out.name == "campaign-no-ext.baseline.json"


def test_save_then_load_roundtrip_minimal(tmp_path: Path) -> None:
    # ck3_chronicler-elll: load_baseline returns a BaselineLoad wrapper
    # carrying snapshot + forensic fields. Tests that previously did
    # ``assert loaded == snap`` now compare ``loaded.snapshot``.
    snap = _snap({})
    path = tmp_path / "x.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot == snap


def test_save_then_load_roundtrip_with_characters(tmp_path: Path) -> None:
    fam = FamilySnapshot(
        mother=10,
        father=11,
        primary_spouse=20,
        spouses=(20, 21),
        children=(30, 31),
    )
    mem = MemorySnapshot(
        memory_id=42,
        memory_type="memory_grand_wedding",
        creation_date="1066.6.1",
        end_date="1099.1.1",
        participants=(("spouse", 20), ("host", 99)),
    )
    chars = {
        1234: _char(1234, family=fam, memories=(mem,)),
        5678: _char(5678, is_dead=True, death_date="1067.1.15", female=True),
    }
    snap = _snap(chars, current_date="1067.5.1", player_character_id=1234)

    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    # Spot-check that integer character keys round-tripped (JSON stringifies dict keys)
    assert 1234 in loaded.snapshot.characters
    assert isinstance(next(iter(loaded.snapshot.characters.keys())), int)
    # Family tuples preserved
    assert loaded.snapshot.characters[1234].family.children == (30, 31)
    # Memory participants preserved as tuples-of-tuples
    assert loaded.snapshot.characters[1234].memories[0].participants == (
        ("spouse", 20),
        ("host", 99),
    )


# ck3_chronicler-g56m: every dict[int, frozenset[...]] reverse-index field on
# SaveSnapshot must survive a save_baseline -> load_baseline round-trip. A field
# added without a _snapshot_to_dict override makes json.dump raise ("Object of
# type frozenset is not JSON serializable"); a field added without a
# _snapshot_from_dict decoder is silently dropped on load. Either way the
# baseline never persists and every restart re-imports from scratch. The sample
# value is type-correct per field (int sets, the dynasty_perks str set, and the
# character_to_constructions tuple set).
_FROZENSET_FIELD_SAMPLES: dict[str, object] = {
    "alliances": {1: frozenset({2, 3})},
    "title_holders": {100: frozenset({1})},
    "kingdoms_by_de_jure_empire": {200: frozenset({100})},
    "character_to_wars": {1: frozenset({5})},
    "character_to_artifacts": {1: frozenset({9})},
    "dynasty_perks": {300: frozenset({"perk_berserker"})},
    "character_to_epidemics": {1: frozenset({3})},
    "character_to_constructions": {1: frozenset({(10, 2)})},
    "character_to_activities": {1: frozenset({4})},
    "character_to_sponsored_inspirations": {1: frozenset({7})},
    "character_to_contracts": {1: frozenset({6})},
    "character_to_court_positions": {1: frozenset({8})},
}


@pytest.mark.parametrize("field_name", sorted(_FROZENSET_FIELD_SAMPLES))
def test_frozenset_reverse_index_roundtrips(tmp_path: Path, field_name: str) -> None:
    sample = _FROZENSET_FIELD_SAMPLES[field_name]
    snap = _snap({1: _char(1)}, **{field_name: sample})
    path = tmp_path / "g.baseline.json"
    save_baseline(path, snap)  # raises if the field has no serializer
    loaded = load_baseline(path)
    assert loaded is not None
    assert getattr(loaded.snapshot, field_name) == sample  # fails if dropped


def test_frozenset_field_coverage_is_complete() -> None:
    """Keep _FROZENSET_FIELD_SAMPLES in lockstep with the dataclass so a newly
    added frozenset field forces a sample (which then gets round-trip-tested)
    instead of silently regressing baseline persistence."""
    import dataclasses

    declared = {
        f.name for f in dataclasses.fields(SaveSnapshot) if "frozenset" in str(f.type).lower()
    }
    covered = set(_FROZENSET_FIELD_SAMPLES)
    assert declared == covered, (
        "SaveSnapshot frozenset fields drifted from the round-trip guard. "
        f"missing samples: {declared - covered}; stale samples: {covered - declared}"
    )


# ck3_chronicler-27ov.1 (audit H2): _character_from_dict restored only 18 of
# CharacterSnapshot's 26 fields — decisions_taken, death_cause, death_killer,
# gold, prestige, prestige_lifetime, piety, piety_lifetime were serialized by
# asdict but never decoded. After a restart prev.decisions_taken was empty, so
# every cooldown decision re-emitted with the new tick's date, duplicating
# decision_taken event rows for up to 10 in-game years. This fixture sets EVERY
# default-bearing field to a distinctive non-default value; the coverage test
# below fails if a future field is added without a sample (which then gets
# round-trip-tested).
def _full_char() -> CharacterSnapshot:
    return CharacterSnapshot(
        ck3_id=1234,
        first_name="Ingrid",
        nickname="the Bold",
        is_dead=True,
        female=True,
        birth_date="1020.1.1",
        death_date="1075.6.2",
        culture_id=7,
        faith_id=8,
        dynasty_house_id=9,
        ethnicity="north_germanic",
        traits=(1, 2, 3),
        family=FamilySnapshot(mother=10, father=11, children=(30, 31)),
        location_id=100,
        memories=(
            MemorySnapshot(
                memory_id=42,
                memory_type="memory_grand_wedding",
                creation_date="1066.1.1",
                end_date=None,
                participants=(("spouse", 20),),
            ),
        ),
        death_cause="death_murder",
        death_killer=999,
        government="landless_adventurer_government",
        decisions_taken=(
            ("raise_stele_decision", "1075.3.2"),
            ("another_decision", "1076.1.1"),
        ),
        gold=123.5,
        prestige=456.0,
        prestige_lifetime=789.0,
        piety=12.5,
        piety_lifetime=34.0,
        modifiers=("devoted_to_ullr", "mourning_son"),
        perks=("schemer_perk",),
    )


def test_full_char_sets_every_default_bearing_field() -> None:
    """27ov.1 / audit H2: keep _full_char exhaustive. Every default-bearing
    CharacterSnapshot field must be non-default in the fixture so the
    round-trip test below actually exercises it — a new field with a default
    fails here until a sample is added."""
    import dataclasses

    full = _full_char()
    for f in dataclasses.fields(CharacterSnapshot):
        value = getattr(full, f.name)
        if f.default is not dataclasses.MISSING:
            assert value != f.default, (
                f"_full_char must set {f.name} to a non-default value so the "
                "round-trip guard exercises it (audit H2)"
            )
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            assert value != f.default_factory(), (
                f"_full_char must set {f.name} to a non-default value (audit H2)"
            )


def test_save_then_load_roundtrip_preserves_all_character_fields(
    tmp_path: Path,
) -> None:
    """27ov.1 / audit H2: decode(encode(char)) must preserve EVERY field."""
    full = _full_char()
    snap = _snap({full.ck3_id: full})
    path = tmp_path / "full.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot.characters[full.ck3_id] == full


def test_save_then_load_roundtrip_with_inspirations_and_dynasties_renown(
    tmp_path: Path,
) -> None:
    """27ov.1 / audit H2: SaveSnapshot.inspirations and dynasties_renown were
    serialized by asdict but never decoded; TitleSnapshot.de_jure_liege_id too.
    inspirations and dynasties_renown ARE consumed downstream (diff.py), so
    dropping them on restore corrupted the first post-restart diff tick."""
    insp = InspirationSnapshot(
        inspiration_id=7,
        inspiration_type="book_inspiration",
        sponsored="1075.1.1",
        total_cost=500,
        progress=120,
        artisan_character_id=88,
    )
    title = TitleSnapshot(
        title_id=100,
        key="d_munster",
        name="Munster",
        tier="duchy",
        holder_id=1,
        de_jure_liege_id=200,
    )
    snap = _snap(
        {1: _char(1)},
        inspirations={7: insp},
        dynasties_renown={300: 4321.0},
        titles={100: title},
    )
    path = tmp_path / "insp.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot.inspirations == {7: insp}
    assert loaded.snapshot.dynasties_renown == {300: 4321.0}
    assert loaded.snapshot.titles[100].de_jure_liege_id == 200


def test_save_then_load_roundtrip_with_lookup_tables(tmp_path: Path) -> None:
    """ck3_chronicler-qug: the loader used to drop houses_lookup,
    cultures_lookup, faiths_lookup, dynasties_lookup, house_to_dynasty,
    and titles silently on the way back from disk. Effect: every
    save-tail restart's first diff tick emitted name=None on
    state-change events until the next rakaly parse refilled the
    lookups. Round-tripping all six locks the regression."""
    fam = FamilySnapshot(primary_spouse=20)
    chars = {1234: _char(1234, family=fam)}
    snap = _snap(
        chars,
        houses_lookup={100: "dynn_Briain", 200: "House of Erik"},
        cultures_lookup={10: "norse", 11: "anglo_saxon"},
        faiths_lookup={5: "catholic", 6: "asatru"},
        dynasties_lookup={50: "dynn_Briain", 51: "Custom Dynasty"},
        house_to_dynasty={100: 50, 200: 51},
        titles={
            1: TitleSnapshot(
                title_id=1,
                key="k_france",
                name="Kingdom of France",
                tier="kingdom",
                holder_id=1234,
            ),
            2: TitleSnapshot(
                title_id=2,
                key="d_munster",
                name=None,  # name optional — must round-trip as None, not the empty string
                tier="duchy",
                holder_id=None,
            ),
        },
    )
    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    # Spot-check that integer keys recovered (JSON stringifies all dict keys)
    assert loaded.snapshot.houses_lookup == {100: "dynn_Briain", 200: "House of Erik"}
    assert loaded.snapshot.cultures_lookup == {10: "norse", 11: "anglo_saxon"}
    assert loaded.snapshot.faiths_lookup == {5: "catholic", 6: "asatru"}
    assert loaded.snapshot.dynasties_lookup == {50: "dynn_Briain", 51: "Custom Dynasty"}
    assert loaded.snapshot.house_to_dynasty == {100: 50, 200: 51}
    assert loaded.snapshot.titles[1].key == "k_france"
    assert loaded.snapshot.titles[1].holder_id == 1234
    assert loaded.snapshot.titles[2].name is None
    assert loaded.snapshot.titles[2].holder_id is None


def test_save_then_load_roundtrip_with_wars(tmp_path: Path) -> None:
    """ck3_chronicler-o7j: wars + character_to_wars survive baseline
    round-trip. Without this, a save-tail restart sees the next tick
    as 'all wars are new' and emits spurious war_declared events for
    every active war the tracked characters are in."""
    war = WarSnapshot(
        war_id=42,
        name="War for Aquitaine",
        start_date="1066.10.1",
        casus_belli_type="claimant_faction_war",
        targeted_titles=(972,),
        primary_attacker_id=100,
        primary_defender_id=200,
        claimant_id=101,
        attacker_participants=frozenset({100, 101}),
        defender_participants=frozenset({200, 201}),
    )
    chars = {1234: _char(1234)}
    snap = _snap(
        chars,
        wars={42: war},
        character_to_wars={
            100: frozenset({42}),
            101: frozenset({42}),
            200: frozenset({42}),
            201: frozenset({42}),
        },
    )
    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    assert loaded.snapshot.wars[42].name == "War for Aquitaine"
    assert loaded.snapshot.wars[42].casus_belli_type == "claimant_faction_war"
    assert loaded.snapshot.wars[42].targeted_titles == (972,)
    assert loaded.snapshot.wars[42].attacker_participants == frozenset({100, 101})
    assert loaded.snapshot.wars[42].defender_participants == frozenset({200, 201})
    assert loaded.snapshot.character_to_wars[100] == frozenset({42})
    assert loaded.snapshot.character_to_wars[201] == frozenset({42})


def test_save_then_load_roundtrip_with_epidemics(tmp_path: Path) -> None:
    """ck3_chronicler-zm0q: epidemics + character_to_epidemics must
    survive baseline round-trip. The 9wrd field add introduced a
    frozenset on SaveSnapshot that asdict() can't unwrap, so the very
    first save-tail persist crashed json.dump on any save with an
    active epidemic."""
    epi = EpidemicSnapshot(
        epidemic_id=7,
        epidemic_type="bubonic",
        name="Pope Alexander's Boils",
        intensity="major",
        creation_date="1083.4.1",
        start_province=44,
        num_infected_provinces=12,
        num_infected_characters=37,
        num_character_deaths=4,
    )
    chars = {1234: _char(1234)}
    snap = _snap(
        chars,
        epidemics={7: epi},
        character_to_epidemics={
            1234: frozenset({7}),
            5678: frozenset({7}),
        },
    )
    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    assert loaded.snapshot.epidemics[7].name == "Pope Alexander's Boils"
    assert loaded.snapshot.epidemics[7].intensity == "major"
    assert loaded.snapshot.epidemics[7].num_infected_characters == 37
    assert loaded.snapshot.character_to_epidemics[1234] == frozenset({7})
    assert loaded.snapshot.character_to_epidemics[5678] == frozenset({7})


def test_save_then_load_roundtrip_with_constructions(tmp_path: Path) -> None:
    """ck3_chronicler-2ur: in_flight_constructions /
    character_to_constructions / holding_buildings_by_slot use
    tuple[int, int] keys (province_id, slot_index). JSON has no tuple
    keys, so the very first save-tail persist crashed json.dump on any
    save with active constructions — the wedge user 2026-05-09 hit
    when their cached 52-save catch-up tried to checkpoint after
    draining the first save. Round-trip must preserve all three fields
    intact."""
    cons_a = ConstructionSnapshot(
        province_id=280,
        slot_index=3,
        building="longhouses_01",
        start_date="1066.9.24",
        character_id=36700,
    )
    cons_b = ConstructionSnapshot(
        province_id=461,
        slot_index=2,
        building="pastures_01",
        start_date="1066.10.5",
        character_id=32502,
    )
    snap = _snap(
        {1234: _char(1234)},
        in_flight_constructions={(280, 3): cons_a, (461, 2): cons_b},
        character_to_constructions={
            36700: frozenset({(280, 3)}),
            32502: frozenset({(461, 2)}),
        },
        holding_buildings_by_slot={
            (280, 0): "tribe_02",
            (280, 1): "common_tradeport_01",
            (461, 0): "tribe_01",
        },
    )
    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    assert loaded.snapshot.in_flight_constructions[(280, 3)].building == "longhouses_01"
    assert loaded.snapshot.in_flight_constructions[(461, 2)].character_id == 32502
    assert loaded.snapshot.character_to_constructions[36700] == frozenset({(280, 3)})
    assert loaded.snapshot.holding_buildings_by_slot[(280, 0)] == "tribe_02"
    assert loaded.snapshot.holding_buildings_by_slot[(280, 1)] == "common_tradeport_01"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "does-not-exist.json") is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """Corrupt JSON must not raise — save-tail must always be able to start."""
    path = tmp_path / "bad.baseline.json"
    path.write_text("{not valid json")
    assert load_baseline(path) is None


def test_load_wrong_shape_returns_none(tmp_path: Path) -> None:
    """JSON parses but isn't a SaveSnapshot dict — return None, don't crash."""
    path = tmp_path / "wrong.baseline.json"
    path.write_text(json.dumps({"not": "a snapshot"}))
    assert load_baseline(path) is None


def test_save_baseline_is_atomic_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If serialization fails mid-write, the destination must remain unchanged
    (no half-written file). We verify this by injecting a failure in the
    serialization step after the temp file exists but before the rename.
    """
    path = tmp_path / "atomic.baseline.json"
    # Pre-existing valid baseline that must NOT be replaced by a partial write
    existing = _snap({1: _char(1)}, current_date="1066.9.15")
    save_baseline(path, existing)
    original_bytes = path.read_bytes()

    # Force os.replace to fail to simulate a crash mid-write
    import os as _os

    real_replace = _os.replace

    def _boom(src, dst):  # noqa: ANN001
        raise OSError("simulated failure")

    monkeypatch.setattr("chronicler.save.baseline.os.replace", _boom)

    later = _snap({1: _char(1)}, current_date="1099.1.1")
    with pytest.raises(OSError):
        save_baseline(path, later)

    # Original baseline still intact and parseable
    assert path.read_bytes() == original_bytes
    monkeypatch.setattr("chronicler.save.baseline.os.replace", real_replace)
    loaded_after = load_baseline(path)
    assert loaded_after is not None
    assert loaded_after.snapshot == existing
    # Temp file must not be left behind
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"temp file leaked: {leftover}"


def test_save_then_load_roundtrip_with_title_holders_and_kingdoms_by_empire(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-kig9: title_holders + kingdoms_by_de_jure_empire
    (added by hk9i) are dict[int, frozenset[int]] — asdict leaves the
    frozensets in place, so the first live save-tail tick crashed
    json.dump on every save with at least one landed title. Lock the
    round-trip so any future addition to SaveSnapshot can't reintroduce
    the gap silently."""
    chars = {1: _char(1)}
    snap = _snap(
        chars,
        title_holders={
            1: frozenset({100, 101, 102}),
            2: frozenset({200}),
        },
        kingdoms_by_de_jure_empire={
            999: frozenset({500, 501}),
            998: frozenset({600}),
        },
    )
    path = tmp_path / "campaign.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.snapshot == snap
    assert loaded.snapshot.title_holders[1] == frozenset({100, 101, 102})
    assert loaded.snapshot.title_holders[2] == frozenset({200})
    assert loaded.snapshot.kingdoms_by_de_jure_empire[999] == frozenset({500, 501})
    assert loaded.snapshot.kingdoms_by_de_jure_empire[998] == frozenset({600})


def test_save_then_load_roundtrip_with_dynasty_renown_and_heads(
    tmp_path: Path,
) -> None:
    """ck3_chronicler-ei8t: dynasty_renown (dict[int, float]) and
    dynasty_heads (dict[int, int]) survive the JSON round-trip. The
    kig9 lesson — every new SaveSnapshot field needs to/from-dict
    coverage to avoid wedging save-tail on the next live tick."""
    chars = {1: _char(1)}
    snap = _snap(
        chars,
        dynasty_renown={11283: 1010.485, 999: 0.0},
        dynasty_heads={11283: 60494, 999: 200},
    )
    path = tmp_path / "ei8t.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot == snap
    assert loaded.snapshot.dynasty_renown == {11283: 1010.485, 999: 0.0}
    assert loaded.snapshot.dynasty_heads == {11283: 60494, 999: 200}


def test_save_baseline_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "overwrite.baseline.json"
    first = _snap({}, current_date="1066.9.15")
    second = _snap({}, current_date="1067.2.1")
    save_baseline(path, first)
    save_baseline(path, second)
    loaded_after = load_baseline(path)
    assert loaded_after is not None
    assert loaded_after.snapshot == second


def test_delete_baseline_when_present(tmp_path: Path) -> None:
    path = tmp_path / "to-delete.baseline.json"
    save_baseline(path, _snap({}))
    assert path.exists()
    assert delete_baseline_if_exists(path) is True
    assert not path.exists()


def test_delete_baseline_when_absent(tmp_path: Path) -> None:
    assert delete_baseline_if_exists(tmp_path / "never.json") is False


# ---------------------------------------------------------------------------
# ck3_chronicler-27ov.10 (audit H1/J2): the hand-mirrored serde (24 encode
# overrides + 14 bespoke decoders, ~700 lines) is gone — SaveSnapshot ⇄ JSON
# now rides the generic dataclass codec. The whole-snapshot round-trip below
# is the load-bearing guard the audit asks for: it populates EVERY
# SaveSnapshot field with a distinctive non-default value (the coverage test
# proves exhaustiveness) and asserts a save → load round-trip restores them
# byte-for-byte. A new field on SaveSnapshot (or any nested snapshot) is
# serialized automatically by the codec AND forced into the fixture by the
# coverage guard, so the "field added without a serde override" bug class
# (qug / zm0q / g56m / kig9 / audit H2) cannot recur silently.
# ---------------------------------------------------------------------------


def _full_snapshot() -> SaveSnapshot:
    """A SaveSnapshot with every field set to a distinctive non-default value.
    Reuses :func:`_full_char` for the exhaustive CharacterSnapshot graph."""
    return SaveSnapshot(
        playthrough_id="uuid-A",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1067.2.1",
        player_character_id=1234,
        characters={1234: _full_char()},
        alliances={1: frozenset({2, 3})},
        traits_lookup=("brave", "craven"),
        houses_lookup={100: "dynn_Briain"},
        cultures_lookup={10: "norse"},
        faiths_lookup={5: "catholic"},
        dynasties_lookup={50: "dynn_Briain"},
        house_to_dynasty={100: 50},
        titles={
            1: TitleSnapshot(
                title_id=1,
                key="k_france",
                name="Kingdom of France",
                tier="kingdom",
                holder_id=1234,
                de_jure_liege_id=2,
            )
        },
        title_holders={1234: frozenset({1, 2})},
        kingdoms_by_de_jure_empire={9: frozenset({1})},
        wars={
            42: WarSnapshot(
                war_id=42,
                name="War for Aquitaine",
                start_date="1066.10.1",
                casus_belli_type="claimant_faction_war",
                targeted_titles=(972,),
                primary_attacker_id=100,
                primary_defender_id=200,
                claimant_id=101,
                attacker_participants=frozenset({100, 101}),
                defender_participants=frozenset({200, 201}),
            )
        },
        character_to_wars={100: frozenset({42})},
        artifacts={
            7: ArtifactSnapshot(
                artifact_id=7, name="Ulfberht", type="weapon", rarity="famed", owner_id=1234
            )
        },
        character_to_artifacts={1234: frozenset({7})},
        dynasty_perks={300: frozenset({"perk_berserker"})},
        dynasty_renown={300: 1010.485},
        dynasty_heads={300: 1234},
        epidemics={
            9: EpidemicSnapshot(
                epidemic_id=9,
                epidemic_type="bubonic",
                name="Pope Alexander's Boils",
                intensity="major",
                creation_date="1083.4.1",
                start_province=44,
                num_infected_provinces=12,
                num_infected_characters=37,
                num_character_deaths=4,
            )
        },
        character_to_epidemics={1234: frozenset({9})},
        in_flight_constructions={
            (280, 3): ConstructionSnapshot(
                province_id=280,
                slot_index=3,
                building="longhouses_01",
                start_date="1066.9.24",
                character_id=36700,
            )
        },
        character_to_constructions={36700: frozenset({(280, 3)})},
        holding_buildings_by_slot={(280, 0): "tribe_02"},
        activities={
            4: ActivitySnapshot(
                activity_id=4,
                activity_type="activity_feast",
                host_id=1234,
                creation_date="1066.1.1",
                active_start_date="1066.2.1",
                start_province_id=44,
                attendees=frozenset({1, 2}),
            )
        },
        character_to_activities={1234: frozenset({4})},
        inspirations={
            7: InspirationSnapshot(
                inspiration_id=7,
                inspiration_type="book_inspiration",
                sponsored="1075.1.1",
                total_cost=500,
                progress=120,
                artisan_character_id=88,
            )
        },
        character_to_sponsored_inspirations={1234: frozenset({7})},
        task_contracts={
            6: ContractSnapshot(
                contract_id=6,
                contract_type="laamp_base_6021",
                name="Perform in a Play",
                tier=2,
                employer_id=100,
                owner_id=1234,
                location_province_id=44,
                status="accepted",
                acceptance_date="1075.1.1",
                completion_date=None,
            )
        },
        character_to_contracts={1234: frozenset({6})},
        court_positions={
            8: CourtPositionSnapshot(
                position_id=8,
                court_position="bodyguard_court_position",
                employee_id=2,
                employer_id=1234,
                hire_date="1075.1.1",
            )
        },
        character_to_court_positions={1234: frozenset({8})},
        domiciles={
            5: DomicileSnapshot(
                domicile_id=5, owner_title_id=1, domicile_type="camp", province_id=44
            )
        },
        character_to_domicile={1234: 5},
        dynasties_renown={300: 4321.0},
        tracked_raw_records={1234: {"first_name": "Eadmund", "gold": 123.0}},
        tracked_coa={1234: {"pattern": "pattern_solid.dds", "color1": "red"}},
    )


def test_full_snapshot_sets_every_field() -> None:
    """Keep _full_snapshot exhaustive. Every SaveSnapshot field must hold a
    non-default value so the round-trip test below actually exercises it — a
    new field added with a default fails here until a sample is supplied, and
    a new required field fails construction. This is the coverage-over-fields()
    enforcement the audit (H1/J2) calls for."""
    import dataclasses

    snap = _full_snapshot()
    for f in dataclasses.fields(SaveSnapshot):
        value = getattr(snap, f.name)
        if f.default is not dataclasses.MISSING:
            assert value != f.default, (
                f"_full_snapshot must set {f.name} to a non-default value so the "
                "round-trip guard exercises it (audit H1/J2)"
            )
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            assert value != f.default_factory(), (
                f"_full_snapshot must set {f.name} to a non-empty value (audit H1/J2)"
            )


def test_full_snapshot_roundtrips_every_field(tmp_path: Path) -> None:
    """The property test the audit asks for: a fully-populated SaveSnapshot
    survives save_baseline → load_baseline intact, field for field."""
    snap = _full_snapshot()
    path = tmp_path / "full-snapshot.baseline.json"
    save_baseline(path, snap)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot == snap


def test_decode_falls_back_to_default_for_absent_nested_field(tmp_path: Path) -> None:
    """A baseline whose nested CharacterSnapshot predates a default-bearing
    field (here: perks) still loads — the codec applies the dataclass default
    rather than raising. (The top-level missing-field guard only discards when
    a *top-level* SaveSnapshot field is absent; see 27ov.19.)"""
    snap = _snap({1: _char(1, perks=("schemer_perk",))})
    path = tmp_path / "legacy-nested.baseline.json"
    save_baseline(path, snap)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["snapshot"]["characters"]["1"]["perks"]  # simulate older build
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.snapshot.characters[1].perks == ()  # dataclass default applied


def test_load_discards_when_top_level_field_absent(tmp_path: Path) -> None:
    """27ov.19 guard survives the codec rewrite: a baseline missing any
    top-level SaveSnapshot field is discarded (rebaseline) rather than decoded
    into hollow prev indexes."""
    snap = _snap({1: _char(1)})
    path = tmp_path / "legacy-toplevel.baseline.json"
    save_baseline(path, snap)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["snapshot"]["wars"]  # a field an older build wouldn't have written
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_baseline(path) is None

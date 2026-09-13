import dataclasses
import json

from chronicler.save.character_columns import hydrate_character_columns
from chronicler.save.snapshot import SaveSnapshot
from tests.helpers.snapshots import make_char


def _snap(**extra) -> SaveSnapshot:
    base = SaveSnapshot(
        playthrough_id="p",
        ck3_version="1.19",
        bookmark_date=None,
        current_date="1067.2.1",
        player_character_id=1,
        characters={7: make_char(7, first_name="Eadmund")},
    )
    return dataclasses.replace(base, **extra) if extra else base


def test_hydrate_uses_snapshot_extractions_when_raw_is_none():
    snap = _snap(
        tracked_raw_records={7: {"first_name": "Eadmund", "extra": 1}},
        tracked_coa={7: {"pattern": "solid.dds"}},
    )
    cols = hydrate_character_columns(
        snap=snap,
        char=snap.characters[7],
        cid=7,
        raw_save_data=None,
        name_lookup={7: "Eadmund"},
    )
    assert json.loads(cols["save_snapshot_json"])["extra"] == 1
    assert json.loads(cols["coa_json"])["pattern"] == "solid.dds"


def test_hydrate_leaves_json_columns_none_when_no_extraction_available():
    snap = _snap()  # empty tracked extractions, no raw dict
    cols = hydrate_character_columns(
        snap=snap,
        char=snap.characters[7],
        cid=7,
        raw_save_data=None,
        name_lookup={7: "Eadmund"},
    )
    assert cols["save_snapshot_json"] is None
    assert cols["coa_json"] is None


def test_hydrate_populates_json_columns_for_dead_prunable_character():
    """A freshly-dead character in characters.dead_prunable must get their
    save_snapshot_json and coa_json populated rather than silently retaining
    the previous value (ck3_chronicler-e68d)."""
    snap = _snap()
    raw_save = {
        "living": {},
        "dead_unprunable": {},
        "characters": {"dead_prunable": {"7": {"extra": 42, "dynasty_house": 200}}},
        "dynasties": {"dynasty_house": {"200": {"coat_of_arms_id": 300}}},
        "coat_of_arms": {"coat_of_arms_manager_database": {"300": {"pattern": "solid.dds"}}},
    }
    cols = hydrate_character_columns(
        snap=snap,
        char=snap.characters[7],
        cid=7,
        raw_save_data=raw_save,
        name_lookup={7: "Eadmund"},
    )
    assert json.loads(cols["save_snapshot_json"])["extra"] == 42
    assert json.loads(cols["coa_json"])["pattern"] == "solid.dds"

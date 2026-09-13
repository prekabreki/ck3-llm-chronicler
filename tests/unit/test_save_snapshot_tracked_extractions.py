import dataclasses

from chronicler.save.snapshot import SaveSnapshot


def _bare_snap() -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="p",
        ck3_version="1.19.0.4",
        bookmark_date=None,
        current_date="1067.2.1",
        player_character_id=1,
    )


def test_tracked_extraction_fields_default_empty():
    snap = _bare_snap()
    assert snap.tracked_raw_records == {}
    assert snap.tracked_coa == {}


def test_tracked_extractions_set_via_replace():
    snap = dataclasses.replace(
        _bare_snap(),
        tracked_raw_records={42: {"first_name": "X"}},
        tracked_coa={42: {"pattern": "p.dds"}},
    )
    assert snap.tracked_raw_records[42]["first_name"] == "X"
    assert snap.tracked_coa[42]["pattern"] == "p.dds"

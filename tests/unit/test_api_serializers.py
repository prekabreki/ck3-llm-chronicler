"""Unit tests for chronicler.api.serializers (ck3_chronicler-27ov.45 / audit M-A1).

The five JSON-decode helpers used to be duplicated verbatim across the route
modules and had drifted — characters.py skipped the isinstance(dict) guard,
and two of its coa call sites parsed unguarded. These tests pin the canonical
guarded behaviour: happy path plus malformed-JSON / None / wrong-shape paths
for every helper.
"""

from __future__ import annotations

import json

from chronicler.api.serializers import (
    blurb_from_chronicle,
    extract_family_ids,
    parse_coa_json,
    parse_family_data,
    parse_held_titles,
    parse_primary_title,
)

# ---------------------------------------------------------------- coa_json


def test_parse_coa_json_happy_path() -> None:
    coa = {"pattern": "pattern_solid.dds", "color1": "red_light"}
    assert parse_coa_json(json.dumps(coa)) == coa


def test_parse_coa_json_none_and_empty_return_none() -> None:
    assert parse_coa_json(None) is None
    assert parse_coa_json("") is None


def test_parse_coa_json_malformed_json_returns_none() -> None:
    assert parse_coa_json("{not json!") is None


def test_parse_coa_json_non_dict_payload_returns_none() -> None:
    # The drifted characters.py copy skipped this guard — a JSON-valid
    # non-dict (list/int/str) must come back None, not poison the
    # response model.
    assert parse_coa_json("[1, 2, 3]") is None
    assert parse_coa_json("42") is None
    assert parse_coa_json('"a string"') is None


# ---------------------------------------------------------- primary_title


def test_parse_primary_title_happy_path() -> None:
    raw = json.dumps({"key": "k_england", "name": "England", "tier": "kingdom"})
    title = parse_primary_title(raw)
    assert title is not None
    assert title.key == "k_england"
    assert title.name == "England"
    assert title.tier == "kingdom"


def test_parse_primary_title_name_optional() -> None:
    title = parse_primary_title(json.dumps({"key": "d_x", "tier": "duchy"}))
    assert title is not None
    assert title.name is None
    # A non-str name is dropped, not served.
    title = parse_primary_title(json.dumps({"key": "d_x", "tier": "duchy", "name": 7}))
    assert title is not None
    assert title.name is None


def test_parse_primary_title_guard_paths_return_none() -> None:
    assert parse_primary_title(None) is None
    assert parse_primary_title("") is None
    assert parse_primary_title("{broken") is None
    assert parse_primary_title("[]") is None  # non-dict payload
    assert parse_primary_title(json.dumps({"tier": "duchy"})) is None  # no key
    assert parse_primary_title(json.dumps({"key": "d_x"})) is None  # no tier
    # key/tier present but wrong type
    assert parse_primary_title(json.dumps({"key": 1, "tier": "duchy"})) is None
    assert parse_primary_title(json.dumps({"key": "d_x", "tier": 2})) is None


# ------------------------------------------------------------ held_titles


def test_parse_held_titles_happy_path_preserves_order() -> None:
    raw = json.dumps(
        [
            {"key": "k_england", "name": "England", "tier": "kingdom"},
            {"key": "d_kent", "name": None, "tier": "duchy"},
        ]
    )
    titles = parse_held_titles(raw)
    assert [t.key for t in titles] == ["k_england", "d_kent"]
    assert titles[0].name == "England"
    assert titles[1].name is None


def test_parse_held_titles_guard_paths_return_empty() -> None:
    assert parse_held_titles(None) == []
    assert parse_held_titles("") == []
    assert parse_held_titles("{broken") == []
    assert parse_held_titles("{}") == []  # non-list payload


def test_parse_held_titles_skips_malformed_entries() -> None:
    raw = json.dumps(
        [
            "junk",
            {"key": "d_kent"},  # missing tier
            {"tier": "duchy"},  # missing key
            {"key": 9, "tier": "duchy"},  # wrong-typed key
            {"key": "c_dover", "tier": "county"},  # good
        ]
    )
    titles = parse_held_titles(raw)
    assert [t.key for t in titles] == ["c_dover"]


# ------------------------------------------------------------ family_data


def test_parse_family_data_happy_path() -> None:
    snap = {"family_data": {"child": [1, 2], "primary_spouse": {"id": 3}}}
    fam = parse_family_data(json.dumps(snap))
    assert fam == {"child": [1, 2], "primary_spouse": {"id": 3}}


def test_parse_family_data_guard_paths_return_empty_dict() -> None:
    assert parse_family_data(None) == {}
    assert parse_family_data("") == {}
    assert parse_family_data("{broken") == {}
    assert parse_family_data("[1, 2]") == {}  # snapshot not a dict
    assert parse_family_data(json.dumps({"other": 1})) == {}  # no family_data
    # family_data present but the wrong shape
    assert parse_family_data(json.dumps({"family_data": [1, 2]})) == {}
    assert parse_family_data(json.dumps({"family_data": "nope"})) == {}


# ------------------------------------------------------- extract_family_ids


def test_extract_family_ids_all_four_shapes() -> None:
    assert extract_family_ids(7) == [7]
    assert extract_family_ids({"id": 7, "name": "Ælfflæd"}) == [7]
    assert extract_family_ids([1, 2, 3]) == [1, 2, 3]
    assert extract_family_ids([{"id": 1}, {"id": 2, "name": "x"}]) == [1, 2]


def test_extract_family_ids_mixed_list() -> None:
    assert extract_family_ids([1, {"id": 2}, 3]) == [1, 2, 3]


def test_extract_family_ids_guard_paths() -> None:
    assert extract_family_ids(None) == []
    assert extract_family_ids("junk") == []
    assert extract_family_ids(3.14) == []
    assert extract_family_ids({"name": "no id"}) == []
    assert extract_family_ids({"id": "not-an-int"}) == []
    # Junk entries inside lists contribute nothing but don't poison
    # the good ones.
    assert extract_family_ids([1, "junk", {"id": None}, 2]) == [1, 2]


# --------------------------------------------------- blurb_from_chronicle


def test_blurb_from_chronicle_first_paragraph() -> None:
    body = "First paragraph.\n\nSecond paragraph."
    assert blurb_from_chronicle(body) == "First paragraph."


def test_blurb_from_chronicle_guard_paths() -> None:
    assert blurb_from_chronicle(None) is None
    assert blurb_from_chronicle("") is None
    assert blurb_from_chronicle("\n\nrest") is None  # empty first paragraph

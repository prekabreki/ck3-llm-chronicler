"""Unit tests for the generic dataclass codec (ck3_chronicler-27ov.10 / audit H1/J2).

These exercise the codec's type machinery in isolation (independent of
SaveSnapshot): the awkward shapes JSON can't represent natively — int and
tuple dict keys, frozensets, fixed/variadic tuples, nested dataclasses,
``X | None`` optionals — plus its leniency (absent key → default) and
strictness (malformed value → raise) contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from chronicler.save.dataclass_codec import from_jsonable, to_jsonable


@dataclass(frozen=True)
class _Leaf:
    name: str
    score: float | None = None


@dataclass(frozen=True)
class _Node:
    nid: int
    leaf: _Leaf
    tags: tuple[str, ...] = ()
    pairs: tuple[tuple[str, int], ...] = ()
    members: frozenset[int] = frozenset()
    by_int: dict[int, str] = field(default_factory=dict)
    by_tuple: dict[tuple[int, int], str] = field(default_factory=dict)
    sets_by_int: dict[int, frozenset[int]] = field(default_factory=dict)
    optional_child: _Leaf | None = None


def _full_node() -> _Node:
    return _Node(
        nid=1,
        leaf=_Leaf(name="root", score=1.5),
        tags=("a", "b"),
        pairs=(("x", 1), ("y", 2)),
        members=frozenset({3, 1, 2}),
        by_int={10: "ten", 20: "twenty"},
        by_tuple={(1, 2): "onetwo", (3, 4): "threefour"},
        sets_by_int={5: frozenset({6, 7})},
        optional_child=_Leaf(name="kid"),
    )


def test_roundtrip_full_node() -> None:
    node = _full_node()
    assert from_jsonable(_Node, to_jsonable(node)) == node


def test_int_keys_are_stringified_and_restored() -> None:
    encoded = to_jsonable(_full_node())
    assert set(encoded["by_int"].keys()) == {"10", "20"}  # JSON keys are strings
    assert from_jsonable(_Node, encoded).by_int == {10: "ten", 20: "twenty"}


def test_tuple_keys_use_colon_format() -> None:
    encoded = to_jsonable(_full_node())
    assert set(encoded["by_tuple"].keys()) == {"1:2", "3:4"}
    assert from_jsonable(_Node, encoded).by_tuple == {(1, 2): "onetwo", (3, 4): "threefour"}


def test_frozensets_encode_sorted_for_determinism() -> None:
    encoded = to_jsonable(_full_node())
    assert encoded["members"] == [1, 2, 3]  # sorted, not insertion order
    assert isinstance(from_jsonable(_Node, encoded).members, frozenset)


def test_optional_none_roundtrips() -> None:
    node = _Node(nid=2, leaf=_Leaf(name="x"), optional_child=None)
    decoded = from_jsonable(_Node, to_jsonable(node))
    assert decoded.optional_child is None
    assert decoded.leaf.score is None


def test_absent_key_falls_back_to_default() -> None:
    # A dict missing a default-bearing key decodes to the dataclass default.
    decoded = from_jsonable(_Node, {"nid": 9, "leaf": {"name": "x"}})
    assert decoded.tags == ()
    assert decoded.members == frozenset()
    assert decoded.by_tuple == {}


def test_absent_required_field_raises() -> None:
    with pytest.raises(KeyError):
        from_jsonable(_Node, {"leaf": {"name": "x"}})  # nid is required


def test_non_dict_for_dataclass_raises() -> None:
    with pytest.raises(TypeError):
        from_jsonable(_Node, ["not", "a", "dict"])


def test_float_field_coerces_integral_json_value() -> None:
    # JSON may carry an integral value for a float field (e.g. 0); decode to float.
    decoded = from_jsonable(_Leaf, {"name": "x", "score": 0})
    assert decoded.score == 0.0
    assert isinstance(decoded.score, float)


def test_encode_rejects_unsupported_value() -> None:
    with pytest.raises(TypeError):
        to_jsonable(object())

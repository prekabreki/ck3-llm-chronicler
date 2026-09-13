"""ck3_chronicler-27ov.11: unit coverage for the generic set-diff
skeleton behind the StreamDiffer registry. The full per-stream behavior
is characterized by tests/unit/test_save_diff.py +
tests/integration/test_real_save_golden.py; this pins the skeleton
itself (dead-guard, added/removed loops, None-skip, asymmetric specs)."""

from types import SimpleNamespace

from chronicler.save.diff import DiffEvent, SetDiffSpec, make_set_differ
from chronicler.schema.events import AllianceFormedEvent, AlliancePayload


def _added(cid, eid, _prev, _curr, date):
    return DiffEvent(
        event=AllianceFormedEvent(d=date, c=cid, p=AlliancePayload(ally_character_id=eid)),
        participants=(),
    )


def test_make_set_differ_emits_added_in_sorted_order():
    spec = SetDiffSpec(name="t", index=lambda s: s.idx, on_added=_added, on_removed=None)
    differ = make_set_differ(spec)
    prev = SimpleNamespace(idx={1: frozenset()})
    curr = SimpleNamespace(idx={1: frozenset({9, 3})})
    out = differ(1, SimpleNamespace(is_dead=False), prev, curr, "1066.1.1")
    assert [e.event.p.ally_character_id for e in out] == [3, 9]


def test_make_set_differ_dead_character_short_circuits():
    spec = SetDiffSpec(name="t", index=lambda s: s.idx, on_added=_added, on_removed=None)
    differ = make_set_differ(spec)
    prev = SimpleNamespace(idx={1: frozenset()})
    curr = SimpleNamespace(idx={1: frozenset({7})})
    assert differ(1, SimpleNamespace(is_dead=True), prev, curr, "1066.1.1") == []


def test_make_set_differ_unchanged_set_short_circuits():
    spec = SetDiffSpec(name="t", index=lambda s: s.idx, on_added=_added, on_removed=None)
    differ = make_set_differ(spec)
    same = SimpleNamespace(idx={1: frozenset({7})})
    assert differ(1, SimpleNamespace(is_dead=False), same, same, "1066.1.1") == []


def test_make_set_differ_on_removed_none_ignores_removals():
    spec = SetDiffSpec(name="t", index=lambda s: s.idx, on_added=_added, on_removed=None)
    differ = make_set_differ(spec)
    prev = SimpleNamespace(idx={1: frozenset({7})})
    curr = SimpleNamespace(idx={1: frozenset()})
    assert differ(1, SimpleNamespace(is_dead=False), prev, curr, "1066.1.1") == []


def test_make_set_differ_builder_returning_none_is_skipped():
    spec = SetDiffSpec(
        name="t",
        index=lambda s: s.idx,
        on_added=lambda *a: None,
        on_removed=None,
    )
    differ = make_set_differ(spec)
    prev = SimpleNamespace(idx={1: frozenset()})
    curr = SimpleNamespace(idx={1: frozenset({7})})
    assert differ(1, SimpleNamespace(is_dead=False), prev, curr, "1066.1.1") == []

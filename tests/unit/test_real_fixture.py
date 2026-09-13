"""Regression test against a real captured CK3 1.19.0.4 debug.log fragment.

The fragment was captured during V01-S04 (the v0.1 end-to-end smoke test):
``console kill 32134`` → William de Normandie's death. Re-running the
parser against this fragment is our defense against CK3 patches changing
the scope-dump format under us. When this test fails, the patch playbook
in docs/patch-playbook.md kicks in.
"""

from __future__ import annotations

from pathlib import Path

from chronicler.tailer.parser import IncrementalParser, ParsedEvent

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "debug_log_samples"
    / "1.19.0.4"
    / "death_william_normandy.log"
)


def test_william_de_normandie_death_parses() -> None:
    parser = IncrementalParser()
    results: list = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        results.extend(parser.feed(line))
    results.extend(parser.flush())

    parsed = [r for r in results if isinstance(r, ParsedEvent)]
    assert len(parsed) == 1
    event = parsed[0].event
    assert event.t == "death"
    assert event.c == 32134
    assert event.d == "16th of September, 1066 AD"
    assert event.v == 1


def test_william_fixture_extracts_named_scope_participants() -> None:
    """V02-P01: the William fixture has a full Saved event targets block that
    should produce participants for surviving_consort, dead_character,
    liege_of_dead_character_councillor, and old_house_head."""
    parser = IncrementalParser()
    results: list = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        results.extend(parser.feed(line))
    results.extend(parser.flush())

    parsed = [r for r in results if isinstance(r, ParsedEvent)]
    assert len(parsed) == 1
    participants = dict(parsed[0].participants)
    # Wife — Mathilde van Vlaanderen
    assert participants["surviving_consort"] == 33007
    # Dying character itself shows up as a named scope too
    assert participants["dead_character"] == 32134
    # Their liege councillor — Philippe Capet
    assert participants["liege_of_dead_character_councillor"] == 37409
    # Self again as old house head
    assert participants["old_house_head"] == 32134


def test_william_fixture_extracts_list_target_participants() -> None:
    """V02-P01: the 'send_death_management_0002' list contains the dying
    character's family + liege; each entry becomes a participant under
    that list label."""
    parser = IncrementalParser()
    results: list = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        results.extend(parser.feed(line))
    results.extend(parser.flush())

    parsed = [r for r in results if isinstance(r, ParsedEvent)]
    assert len(parsed) == 1
    list_role = "send_death_management_0002"
    list_members = {char_id for role, char_id in parsed[0].participants if role == list_role}
    # Spouse (33007) is already in saved event targets too — should appear in the list as well.
    expected = {33007, 39543, 37916, 38092, 38232, 39045, 32425, 32669, 32751, 32932, 37409}
    assert list_members == expected


def test_william_fixture_does_not_capture_character_memory_scope() -> None:
    """new_memory: ... (Character_memory - 2274)! must not yield a participant
    — it's not a Character scope."""
    parser = IncrementalParser()
    results: list = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        results.extend(parser.feed(line))
    results.extend(parser.flush())

    parsed = [r for r in results if isinstance(r, ParsedEvent)]
    participants = dict(parsed[0].participants)
    assert "new_memory" not in participants

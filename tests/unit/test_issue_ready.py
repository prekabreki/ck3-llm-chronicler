"""Tests for tools/issue-ready.py's blocked-by parsing.

The ready view is what a session (or an autonomous executor wave) treats
as safe-to-claim work, so a dependency it fails to parse is not a
cosmetic bug: it advertises work whose prerequisites are still open. This
repo's #28 is "build the public duplicate and flip the repo public" —
exactly the issue that must never surface early.

Loaded via importlib because the filename is hyphenated.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "issue_ready", Path(__file__).parent.parent.parent / "tools" / "issue-ready.py"
)
assert _SPEC and _SPEC.loader
issue_ready = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(issue_ready)


def _issue(number: int, body: str = "", **kw) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "body": body,
        "assignees": kw.get("assignees", []),
        "labels": [{"name": n} for n in kw.get("labels", [])],
    }


def _ready_numbers(issues: list[dict]) -> set[int]:
    return {i["number"] for i in issue_ready.filter_ready(issues)}


def test_single_blocker_hides_the_issue() -> None:
    issues = [_issue(1), _issue(2, "Blocked by #1")]
    assert _ready_numbers(issues) == {1}


def test_colon_form_is_parsed() -> None:
    """The form the gh-issues-writing body template emits."""
    issues = [_issue(1), _issue(2, "Blocked by: #1")]
    assert _ready_numbers(issues) == {1}


def test_every_blocker_in_a_comma_separated_list_counts() -> None:
    """A dependency list on one line must be read WHOLE.

    Reading only the first entry means the issue unblocks as soon as its
    first prerequisite closes, while the rest are still open.
    """
    issues = [
        _issue(19, labels=["P1"]),
        _issue(20, labels=["P1"]),
        _issue(28, "- Blocked by: #19, #20, #21", labels=["P1"]),
        _issue(21, labels=["P1"]),
    ]
    assert 28 not in _ready_numbers(issues)


def test_remaining_blockers_still_hide_it_after_the_first_one_closes() -> None:
    """The live regression: #19 closed, #20-#27 open, #28 offered as ready.

    ``filter_ready`` derives the open set from the list it is given, so a
    closed blocker is simply absent — #28 must stay hidden on the others.
    """
    issues = [
        _issue(20, labels=["P1"]),
        _issue(21, labels=["P1"]),
        _issue(28, "- Blocked by: #19, #20, #21, #22", labels=["P1"]),
    ]
    assert _ready_numbers(issues) == {20, 21}


def test_prose_after_the_ref_list_is_not_scanned() -> None:
    """Reversed from the earlier behaviour, matching the shared workspace template.

    This repo previously counted every ``#N`` on a dependency line, so #28's real
    body ('..., and any pre-flip bugs #25 files.') treated #25 as a blocker. That
    over-blocking cost more than it saved elsewhere: a trailing 'Part of #15.'
    recorded an open epic as a dependency and hid ready work for days. The rule is
    now that the blocker list is the LEADING run of refs after the marker, and prose
    after it is commentary. Declare real dependencies as a comma list instead.
    """
    issues = [
        _issue(25, labels=["P1"]),
        _issue(28, "- Blocked by: #24, and any pre-flip bugs #25 files.", labels=["P1"]),
    ]
    assert _ready_numbers(issues) == {25, 28}

    # The supported way to say it, which still blocks:
    declared = [
        _issue(25, labels=["P1"]),
        _issue(28, "- Blocked by: #24, #25", labels=["P1"]),
    ]
    assert _ready_numbers(declared) == {25}


def test_the_marker_must_open_its_line() -> None:
    """Also from #35: prose ABOUT blockers must not declare blockers.

    The cost is real in both directions — ck3 #29 carried 'Part of #18. Blocked by
    #28.' mid-line and stops being blocked under this rule until the body puts the
    marker on its own line.
    """
    mid_line = [
        _issue(28, labels=["P1"]),
        _issue(29, "- Executor tier: owner. Part of #18. Blocked by #28.", labels=["P1"]),
    ]
    assert _ready_numbers(mid_line) == {28, 29}

    own_line = [
        _issue(28, labels=["P1"]),
        _issue(29, "- Executor tier: owner.\n- Blocked by: #28", labels=["P1"]),
    ]
    assert _ready_numbers(own_line) == {28}


def test_issue_numbers_on_other_lines_are_not_blockers() -> None:
    """Scoped to the Blocked-by line: a reference elsewhere in the body
    (context, 'part of epic #18', a Blocks line) must not block."""
    body = "## Context\n\nPart of epic #18; see #99.\n\n- Blocked by: #20\n\n- Blocks #30\n"
    issues = [_issue(18), _issue(20), _issue(30), _issue(99), _issue(29, body)]
    assert 29 not in _ready_numbers(issues)

    # With only the real blocker closed, the other references must not hold it.
    issues_unblocked = [_issue(18), _issue(30), _issue(99), _issue(29, body)]
    assert 29 in _ready_numbers(issues_unblocked)


def test_a_closed_blocker_does_not_hide_the_issue() -> None:
    """Only OPEN issues block — the open set is derived from the input."""
    issues = [_issue(2, "Blocked by #1")]
    assert _ready_numbers(issues) == {2}


def test_assigned_and_deferred_issues_stay_out_of_ready() -> None:
    issues = [
        _issue(1, assignees=[{"login": "someone"}]),
        _issue(2, labels=["deferred"]),
        _issue(3),
    ]
    assert _ready_numbers(issues) == {3}


def test_a_deferred_blocker_still_blocks() -> None:
    """Documented contract: held issues stay in the open set on purpose."""
    issues = [_issue(1, labels=["deferred"]), _issue(2, "Blocked by #1")]
    assert _ready_numbers(issues) == set()

"""Pytest fixtures shared by every test module.

Currently just one job: disable cross-machine archive sync
(:mod:`chronicler.sync`) for the entire test session. Without this
guard, any test that flows through the closing-ceremony /complete
route auto-detects the chronicler git checkout and writes archived
campaign snapshots into the developer's working tree — fine for the
seal flow's normal path, but a nightmare in CI / a developer
sandbox where every test run leaks UUID-named .db / .json pairs into
``data/archived/``. Setting the disable env var keeps
:func:`chronicler.sync.archive_export.resolve_repo_root` returning
None for the duration of the test session; production runs (the
user's actual chronicler launches) leave it unset and continue to
auto-sync.

Tests that explicitly want to exercise the sync path
(``test_sync_archive_export.py``) pass ``repo_root`` keyword
arguments directly so they bypass the env-var gate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db.repository import (
    insert_biography,
    insert_event_idempotent,
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# ── Issue #41: skips stop being silent ───────────────────────────────────
#
# Eight tests used to execute only on the owner's machine. They were gated
# on artifacts that are untracked by design, so every automated environment
# skipped them and reported green — and `pytest -q` prints skips as a bare
# count with no baseline, so nobody could tell a correct-by-design skip from
# a hole in the coverage.
#
# Every skip this suite is allowed to emit is declared below, by a stable
# substring of its reason. Two categories:
#
#   BY_DESIGN  — the test *cannot* apply here and no coverage is lost.
#                A platform branch, or the untaken side of an either/or.
#   LOCAL_ONLY — real coverage that no automated environment can provide.
#                CI is deliberately hermetic (no rakaly binary, no tracked
#                *.ck3), so these run on a developer machine or nowhere.
#                Declared, listed by name at the end of every run, and
#                never allowed to grow silently.
#
# Anything else is UNDECLARED, and under CHRONICLER_STRICT_SKIPS=1 (which
# CI sets) an undeclared skip fails the run. That is what makes the CI
# frontend build load-bearing: if it stops producing static/app/index.html,
# the four SPA-serving tests skip, land in UNDECLARED, and CI goes red
# instead of quietly shedding four tests.

_SKIP_BY_DESIGN: dict[str, str] = {
    "Windows-only console-flag behaviour": "platform branch; the Windows CI leg runs it",
    "symlink/junction creation not permitted": "environment lacks symlink privilege",
    # Issue #7: SIGTERM/SIGINT disposition, uvicorn's signal re-raise, and
    # process-group semantics are POSIX. The Linux CI leg runs these.
    "POSIX signal semantics": "platform branch; the Linux CI leg runs it",
    # Issue #54: these assert on open file descriptors via /proc/self/fd,
    # because the defect they catch has no behavioural symptom on POSIX at
    # all. Windows has no /proc — and there the *behaviour* fails instead,
    # which is what the archive-move tests cover on that leg.
    "needs /proc": "fd-level assertion; Linux is where it can be made",
}

_SKIP_LOCAL_ONLY: dict[str, str] = {
    "save fixture missing": "needs tests/fixtures/saves/autosave_exit.ck3 (untracked, ~20-200MB)",
    "no real-save fixture under": "needs any tests/fixtures/saves/*.ck3 (untracked)",
    "rakaly binary not found": "needs the rakaly binary; CI never fetches it (hermetic)",
}
# Issue #53 removed the `could not import 'xhtml2pdf'` entry that used to sit
# here: CI now syncs `--extra pdf` on both legs, so real PDF generation is
# proven off the owner's machine and an xhtml2pdf skip is once again UNDECLARED
# — i.e. a red run. A local `uv sync` without the extra will trip that gate;
# that is the intended reading, not a false alarm.


def _classify_skip(reason: str) -> tuple[str, str]:
    """Map a skip reason to ``(category, note)``. Substring match, because
    pytest renders the reason with its own prefixes and locations."""
    for needle, note in _SKIP_BY_DESIGN.items():
        if needle in reason:
            return "BY-DESIGN", note
    for needle, note in _SKIP_LOCAL_ONLY.items():
        if needle in reason:
            return "LOCAL-ONLY", note
    return "UNDECLARED", "not in the declared skip table — coverage may have gone missing"


def _collect_skips(terminalreporter) -> list[tuple[str, str, str, str]]:
    """``(category, note, nodeid, reason)`` for every skip in this run."""
    rows: list[tuple[str, str, str, str]] = []
    for report in terminalreporter.stats.get("skipped", []):
        reason = ""
        if isinstance(getattr(report, "longrepr", None), tuple) and len(report.longrepr) == 3:
            reason = str(report.longrepr[2])
        else:
            reason = str(getattr(report, "longrepr", ""))
        category, note = _classify_skip(reason)
        rows.append((category, note, report.nodeid, reason))
    return rows


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    """Print the skip ledger. Always — a count with no names is exactly the
    silence this replaces."""
    rows = _collect_skips(terminalreporter)
    if not rows:
        return
    write = terminalreporter.write_line
    terminalreporter.write_sep("=", "skip ledger (issue #41)")
    for category in ("UNDECLARED", "LOCAL-ONLY", "BY-DESIGN"):
        matching = [r for r in rows if r[0] == category]
        if not matching:
            continue
        write(f"{category}: {len(matching)}")
        for _cat, note, nodeid, reason in matching:
            write(f"  {nodeid}")
            write(f"    reason: {reason.strip()}")
            write(f"    → {note}")
    if any(r[0] == "UNDECLARED" for r in rows):
        write("")
        if os.environ.get("CHRONICLER_STRICT_SKIPS"):
            write("CHRONICLER_STRICT_SKIPS is set — undeclared skips fail this run.")
        else:
            write(
                "Set CHRONICLER_STRICT_SKIPS=1 (as CI does) to make undeclared skips "
                "fail rather than warn."
            )


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Under strict mode an undeclared skip is a failure. A green run that
    quietly dropped tests is the failure mode this whole block exists for."""
    if not os.environ.get("CHRONICLER_STRICT_SKIPS"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:  # pragma: no cover — -p no:terminal
        return
    if any(row[0] == "UNDECLARED" for row in _collect_skips(reporter)):
        session.exitstatus = 1


def pytest_configure(config) -> None:  # noqa: ARG001 — pytest hook signature
    os.environ.setdefault("CHRONICLER_ARCHIVE_SYNC_DISABLED", "1")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CampaignHarness]:
    """API test harness — see :mod:`tests.helpers.api` (audit M-T2).

    Owns ``CHRONICLER_DATA_DIR`` isolation and tears down every TestClient
    it built. Use ``api.campaign(...)`` / ``api.client(...)`` for
    multi-campaign or explicit-provider tests; ``make_campaign`` for the
    single-campaign one-liner.
    """
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    harness = CampaignHarness(data_dir=tmp_path)
    yield harness
    harness.close()


@pytest.fixture
def make_campaign(api: CampaignHarness) -> Callable[..., TestClient]:
    """Single-campaign convenience: ``client = make_campaign("name", seed=...)``."""
    return api.make_campaign


def _seed_base_campaign(s: Session) -> None:
    """Populate the base ``test-campaign`` DB: one character with a
    vanilla_memory event and a biography that references it."""
    upsert_character(
        s,
        ck3_id=36892,
        first_name="Eadmund",
        birth_date="1049.12.10",
        death_date=None,
    )
    eid = insert_event_idempotent(
        s,
        schema_version=1,
        event_type="vanilla_memory",
        event_date="1066.9.18",
        event_date_iso="1066-09-18",
        wall_clock_at="2026-05-01T00:00:00+00:00",
        primary_character_id=36892,
        payload_json=json.dumps(
            {
                "v": 1,
                "t": "vanilla_memory",
                "d": "1066.9.18",
                "c": 36892,
                "p": {
                    "memory_type": "relative_died",
                    "participants": {"dead_relation": 31175},
                },
            }
        ),
        raw_line="test fixture",
    )
    assert eid is not None
    insert_biography(
        s,
        character_id=36892,
        body="Eadmund was born in 1049 and inherited twice.",
        prompt_template_version="biography_v1",
        provider="test:fake",
        generated_at="2026-05-01T12:00:00+00:00",
        events_through_event_id=eid,
    )


@pytest.fixture
def registry_and_campaign(api: CampaignHarness) -> tuple[Path, str]:
    """Temp registry + one populated ``test-campaign`` DB.

    Returns (registry_path, campaign_name). The DB file is pinned to
    ``campaigns/test.db`` because a couple of tests reopen it by path.
    """
    api.campaign("test-campaign", seed=_seed_base_campaign, db_name="test")
    return api.registry, "test-campaign"


@pytest.fixture
def client(api: CampaignHarness, registry_and_campaign: tuple[Path, str]) -> TestClient:
    """The base ``test-campaign`` + an entered TestClient (no provider)."""
    return api.client()


@pytest.fixture
def paths_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the settings store at a temp file and clear path env vars so
    paths-API and save-picker tests don't read the dev box's real
    settings.json. Shared by the settings + io (save-picker) suites."""
    target = tmp_path / "chronicler_settings.json"
    monkeypatch.setattr("chronicler.settings_store.DEFAULT_SETTINGS_PATH", target)
    monkeypatch.delenv("CHRONICLER_SAVE_DIR", raising=False)
    monkeypatch.delenv("CHRONICLER_CK3_INSTALL_DIR", raising=False)
    # Issue #51: the paths panel serves the archive dir too, and this box
    # may well export the env var it resolves from.
    monkeypatch.delenv("CHRONICLER_ARCHIVE_DIR", raising=False)
    # find_ck3_install probes Steam libraries on the dev box; stub it to
    # None so install-dir tests get deterministic source="probe", exists=False
    # unless they explicitly set an override. Tests that need a hit can
    # re-monkeypatch.
    monkeypatch.setattr(
        "chronicler.heraldry.extractor.find_ck3_install",
        lambda override=None: None,
    )
    return target


@pytest.fixture(autouse=True)
def _isolate_settings_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the dev box's real ``settings.json`` out of the whole suite.

    Same machine-dependence problem as :func:`_isolate_ck3_name_map`. Model
    resolution reads durable ``narrative_model`` keys from the settings
    store, so once a user persists a model choice (which is the point of
    those keys) 13 model-resolution tests across three files started
    resolving that value instead of their expected default — green on a
    clean box, red on a configured one, and failing while naming the wrong
    cause. Any settings-backed resolver has the same exposure, so isolate
    the store globally rather than per-suite.

    ``paths_isolation`` re-points this at its own tmp file for tests that
    read and write settings; it runs after this autouse fixture, so its
    override wins.
    """
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        tmp_path / "isolated_settings.json",
    )


@pytest.fixture(autouse=True)
def _isolate_ck3_name_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """ck3_chronicler-r3fs: keep the CK3 name-localization lookup out of the
    test suite by default.

    :func:`chronicler.save.ck3_names.get_name_map` autodetects a real CK3
    install (Steam paths) and caches 55k canonical names process-wide. That
    would make ``parse_save``-based tests machine-dependent — passing on a
    dev box with the game installed, behaving differently in CI without it.
    Forcing an empty map makes the heuristic ``decode_ck3_name`` the
    deterministic default everywhere. Tests that exercise the loca path
    set ``ck3_names._NAME_MAP`` (or monkeypatch ``parse.resolve_name``)
    themselves, overriding this.

    ck3_chronicler-6rgx: the dynasty/house-name loca (``_DYNASTY_MAP``)
    autodetects the same install, so pin it empty for the same reason —
    ``resolve_house_name`` then falls back to the deterministic
    ``decode_house_name`` heuristic.

    Issue #14: both maps now also merge loca from CK3's *enabled mods*,
    discovered by reading the real ``dlc_load.json`` under the user's CK3
    dir. That is a second machine-dependency of the same kind — a test that
    builds a fake install would otherwise silently absorb whatever the dev
    box happens to have subscribed — so pin the discovery to "no mods".
    Tests exercising the merge monkeypatch ``find_enabled_mod_dirs``
    themselves, overriding this.
    """
    from chronicler.save import ck3_names

    monkeypatch.setattr(ck3_names, "_NAME_MAP", {})
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", {})
    monkeypatch.setattr(ck3_names, "find_enabled_mod_dirs", lambda: [])

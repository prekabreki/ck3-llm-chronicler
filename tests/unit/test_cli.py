"""Tests for the Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chronicler.cli.main import app
from chronicler.db import Base, make_engine_for_path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "campaign" in result.stdout
    assert "tail" in result.stdout
    assert "dump-character" in result.stdout


def test_campaign_create_then_list(runner: CliRunner, env: Path) -> None:
    result = runner.invoke(app, ["campaign", "create", "Aquitaine"])
    assert result.exit_code == 0, result.stdout
    assert "created campaign" in result.stdout
    assert "Aquitaine" in result.stdout

    listed = runner.invoke(app, ["campaign", "list"])
    assert listed.exit_code == 0
    assert "Aquitaine" in listed.stdout


def test_campaign_list_empty(runner: CliRunner, env: Path) -> None:
    result = runner.invoke(app, ["campaign", "list"])
    assert result.exit_code == 0
    assert "(no campaigns)" in result.stdout


def test_dump_character_unknown_campaign(runner: CliRunner, env: Path) -> None:
    result = runner.invoke(app, ["dump-character", "1234", "--campaign", "nope"])
    assert result.exit_code == 2
    assert "campaign not found" in result.stdout or "campaign not found" in (result.stderr or "")


def test_dump_character_unknown_character(runner: CliRunner, env: Path) -> None:
    create_result = runner.invoke(app, ["campaign", "create", "Test"])
    assert create_result.exit_code == 0

    result = runner.invoke(app, ["dump-character", "9999", "--campaign", "Test"])
    assert result.exit_code == 1


def test_dump_character_known_round_trip(
    runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: create a campaign, populate it directly, dump via CLI."""
    create_result = runner.invoke(app, ["campaign", "create", "Test"])
    assert create_result.exit_code == 0

    # Locate the campaign DB and add a character + event directly.
    list_result = runner.invoke(app, ["campaign", "list"])
    db_path_line = next(
        line for line in list_result.stdout.splitlines() if line.strip().startswith("db:")
    )
    db_path = Path(db_path_line.split("db:", 1)[1].strip())

    engine = make_engine_for_path(db_path)
    # Migrations have already created the schema, so tables exist.
    from sqlalchemy.orm import Session

    from chronicler.db.repository import insert_event_idempotent, upsert_character

    with Session(engine) as session:
        upsert_character(session, ck3_id=1234, first_name="Harold", dynasty_name="Godwin")
        insert_event_idempotent(
            session,
            schema_version=1,
            event_type="death",
            event_date="1066.10.14",
            wall_clock_at="2026-04-29T18:54:27+00:00",
            primary_character_id=1234,
            payload_json='{"v":1,"t":"death","d":"1066.10.14","c":1234,"p":{}}',
            raw_line="raw",
        )
        session.commit()
    engine.dispose()

    # Schema must include the alembic_version table from the upgrade.
    Base.metadata.bind = None  # noqa: SLF001 — paranoia in case of cross-test bleed

    dump_result = runner.invoke(app, ["dump-character", "1234", "--campaign", "Test", "--raw"])
    assert dump_result.exit_code == 0, dump_result.stdout
    payload = json.loads(dump_result.stdout.strip())
    assert payload["ck3_id"] == 1234
    assert payload["first_name"] == "Harold"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["type"] == "death"


def _stub_probe(name: str, ok: bool, detail: str = ""):
    """ck3_chronicler-a38: build a one-off ProbeResult-returning callable
    so tests can swap any individual probe without driving real httpx /
    filesystem state."""
    from chronicler.cli.doctor import ProbeResult

    def _stub(*_args, **_kwargs) -> ProbeResult:
        return ProbeResult(name, ok, detail)

    return _stub


def test_doctor_probe_narrative_backend_anthropic_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ck3_chronicler-cs1o: backend=anthropic without ANTHROPIC_API_KEY
    is a red — the factory would refuse to construct the provider."""
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "ANTHROPIC_API_KEY" in result.detail

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = doctor.probe_narrative_backend()
    assert result.ok is True
    assert "anthropic" in result.detail


def test_doctor_probe_narrative_backend_claude_code_bin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chronicler.cli import doctor

    monkeypatch.delenv("CHRONICLER_NARRATIVE_BACKEND", raising=False)
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIN", "definitely-not-a-real-bin-cs1o")
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "not on PATH" in result.detail

    # Any binary guaranteed on PATH works to prove the green path; git
    # is a hard dependency of the prose-repo flow already.
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_BIN", "git")
    result = doctor.probe_narrative_backend()
    assert result.ok is True
    assert "claude-code" in result.detail


def test_doctor_probe_narrative_backend_unknown_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "ollama")
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "ollama" in result.detail


# --- issue #43: the prose-register probe ---


@pytest.fixture
def prose_probe_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Isolate the prose-repo resolver: no settings override, no env var,
    so each test drives the path it wants and the dev box's real
    settings.json cannot decide the outcome."""
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        tmp_path / "chronicler_settings.json",
    )
    monkeypatch.delenv("CHRONICLER_PROSE_REPO_PATH", raising=False)
    return tmp_path


def _fake_prose_dir(root, *, register: bool = True, voice: bool = True):
    """A directory shaped like a scaffolded chronicle dir."""
    from typing import get_args

    from chronicler.narrative.prose_io import voice_file_for_kind
    from chronicler.narrative.provider import PromptKind

    root.mkdir(parents=True, exist_ok=True)
    if register:
        (root / "CLAUDE.md").write_text("# register\n", encoding="utf-8")
    if voice:
        for kind in get_args(PromptKind):
            rel = voice_file_for_kind(kind)
            if not rel:
                continue
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# voice {kind}\n", encoding="utf-8")
    return root


def test_doctor_probe_prose_repo_green(prose_probe_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scaffolded chronicle dir passes, and the detail names both the
    path and where the path came from."""
    from chronicler.cli import doctor

    repo = _fake_prose_dir(prose_probe_env / "prose")
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(repo))

    result = doctor.probe_prose_repo()
    assert result.ok is True
    assert str(repo) in result.detail
    # Provenance is the whole point of #43 — the live failure hid because
    # a plausible-looking path gave no hint it came from a stale setting.
    assert "CHRONICLER_PROSE_REPO_PATH" in result.detail


def test_doctor_probe_prose_repo_reports_settings_provenance(
    prose_probe_env,
) -> None:
    """An override from settings.json is labelled as such, so a stale one
    is visible in the output rather than merely implied."""
    from chronicler.cli import doctor
    from chronicler.settings_store import update_settings

    repo = _fake_prose_dir(prose_probe_env / "from-settings")
    update_settings({"prose_repo_path": str(repo)})

    result = doctor.probe_prose_repo()
    assert result.ok is True
    assert "settings.json" in result.detail


def test_doctor_probe_prose_repo_missing_dir(
    prose_probe_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live 2026-08-12 case: the resolved path is not on disk."""
    from chronicler.cli import doctor

    missing = prose_probe_env / "gone"
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(missing))

    result = doctor.probe_prose_repo()
    assert result.ok is False
    assert str(missing) in result.detail
    assert "init-prose" in result.detail


def test_doctor_probe_prose_repo_missing_dir_from_settings_names_the_setting(
    prose_probe_env,
) -> None:
    """When the bad path came from settings.json, the fix line says to
    correct THAT — telling the user to run init-prose alone would leave
    the stale override still winning."""
    from chronicler.cli import doctor
    from chronicler.settings_store import update_settings

    update_settings({"prose_repo_path": str(prose_probe_env / "stale")})

    result = doctor.probe_prose_repo()
    assert result.ok is False
    assert "prose_repo_path" in result.detail
    assert "settings.json" in result.detail


def test_doctor_probe_prose_repo_missing_register(
    prose_probe_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory without CLAUDE.md is a red: assemble_system_prompt
    refuses rather than generating register-less prose."""
    from chronicler.cli import doctor

    repo = _fake_prose_dir(prose_probe_env / "no-register", register=False)
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(repo))

    result = doctor.probe_prose_repo()
    assert result.ok is False
    assert "CLAUDE.md" in result.detail


def test_doctor_probe_prose_repo_missing_voice_file(
    prose_probe_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing per-kind voice file fails the kind it belongs to, so the
    probe names the file rather than passing the directory as healthy."""
    from chronicler.cli import doctor
    from chronicler.narrative.prose_io import voice_file_for_kind

    repo = _fake_prose_dir(prose_probe_env / "no-voice")
    (repo / voice_file_for_kind("biography_woven")).unlink()
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(repo))

    result = doctor.probe_prose_repo()
    assert result.ok is False
    assert "biography-woven.md" in result.detail


def test_doctor_probe_prose_repo_enumerates_kinds_not_a_hardcoded_list(
    prose_probe_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The voice check must ask voice_file_for_kind for every real kind.
    A kind with no registered voice file is allowed and must not fail;
    a newly-registered one must be covered without editing the probe."""
    from chronicler.cli import doctor
    from chronicler.narrative import prose_io

    repo = _fake_prose_dir(prose_probe_env / "extra-kind")
    monkeypatch.setenv("CHRONICLER_PROSE_REPO_PATH", str(repo))
    assert doctor.probe_prose_repo().ok is True

    # Register a voice file for a kind the scaffold doesn't ship.
    monkeypatch.setitem(prose_io._VOICE_FILES_BY_KIND, "biography", "voice/invented.md")
    result = doctor.probe_prose_repo()
    assert result.ok is False
    assert "voice/invented.md" in result.detail


def test_doctor_prose_repo_probe_is_critical_and_gates_the_exit_code(
    prose_probe_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Informational classification would make the probe cosmetic: doctor
    must exit non-zero on a broken prose dir alone."""
    from chronicler.cli import doctor

    assert doctor.PROBE_PROSE_REPO not in doctor.INFORMATIONAL_PROBES

    results = [
        doctor.ProbeResult(doctor.PROBE_PROSE_REPO, False, "no chronicle directory"),
        doctor.ProbeResult(doctor.PROBE_CK3_INSTALL, True, "ok"),
    ]
    assert doctor.has_critical_failure(results) is True


def test_doctor_probe_names_cover_all_probes() -> None:
    """ck3_chronicler-27ov.80 (audit L27): the crash-fallback name map
    must stay in sync with ALL_PROBES, else a crashed probe gets a wrong
    (or default) name and could lose its informational classification."""
    from chronicler.cli import doctor

    assert set(doctor.ALL_PROBES) == set(doctor._PROBE_NAMES)


def test_run_all_probes_isolates_a_raising_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """ck3_chronicler-27ov.80 (audit L27): a probe that raises must not
    abort the diagnostic. It becomes a failed ProbeResult (named, so exit
    gating still works) and the remaining probes still run."""
    from chronicler.cli import doctor

    def _boom() -> doctor.ProbeResult:
        raise RuntimeError("malformed Steam library file")

    monkeypatch.setattr(doctor, "probe_ck3_install", _boom)
    monkeypatch.setattr(
        doctor, "probe_heraldry_palette", _stub_probe(doctor.PROBE_HERALDRY, True, "ok")
    )
    monkeypatch.setattr(
        doctor,
        "ALL_PROBES",
        (doctor.probe_ck3_install, doctor.probe_heraldry_palette),
    )
    monkeypatch.setattr(
        doctor, "_PROBE_NAMES", {doctor.probe_ck3_install: doctor.PROBE_CK3_INSTALL}
    )

    results = doctor.run_all_probes()

    assert len(results) == 2
    crashed = results[0]
    assert crashed.name == doctor.PROBE_CK3_INSTALL
    assert crashed.ok is False
    assert "malformed Steam library file" in crashed.detail
    # The probe after the crash still ran.
    assert results[1].ok is True
    # A crashed required probe gates the exit code.
    assert doctor.has_critical_failure(results) is True


def test_doctor_all_green(runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ck3_chronicler-a38: when every probe passes, the doctor exits 0
    with all green checks."""
    from chronicler.cli import doctor

    monkeypatch.setattr(
        doctor, "probe_ck3_install", _stub_probe(doctor.PROBE_CK3_INSTALL, True, "C:/CK3")
    )
    monkeypatch.setattr(
        doctor, "probe_heraldry_palette", _stub_probe(doctor.PROBE_HERALDRY, True, "…/palette.json")
    )
    monkeypatch.setattr(
        doctor,
        "probe_known_campaigns",
        _stub_probe(doctor.PROBE_CAMPAIGNS, True, "1 total (1 active)"),
    )
    monkeypatch.setattr(
        doctor,
        "ALL_PROBES",
        (
            doctor.probe_ck3_install,
            doctor.probe_heraldry_palette,
            doctor.probe_known_campaigns,
        ),
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.count("[OK]") == 3
    assert "[!!]" not in result.stdout


def test_doctor_ck3_install_missing_fails(
    runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red on a required probe gates the exit code so CI / scripts can
    treat doctor's exit as a setup-completeness gate."""
    from chronicler.cli import doctor

    monkeypatch.setattr(
        doctor,
        "probe_ck3_install",
        _stub_probe(doctor.PROBE_CK3_INSTALL, False, "Steam default not found"),
    )
    monkeypatch.setattr(
        doctor, "probe_heraldry_palette", _stub_probe(doctor.PROBE_HERALDRY, True, "ok")
    )
    monkeypatch.setattr(
        doctor, "probe_known_campaigns", _stub_probe(doctor.PROBE_CAMPAIGNS, True, "ok")
    )
    monkeypatch.setattr(
        doctor,
        "ALL_PROBES",
        (
            doctor.probe_ck3_install,
            doctor.probe_heraldry_palette,
            doctor.probe_known_campaigns,
        ),
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "CK3 install located" in result.stdout
    assert "Steam default not found" in result.stdout


def test_doctor_heraldry_freshness_probe_skips_when_not_extracted(
    runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-8jz: probe_heraldry_freshness defers silently when
    no heraldry has been extracted yet — the palette probe owns that
    failure mode."""
    from chronicler.cli import doctor

    # No heraldry dir under tmp_path; install dir won't be found either.
    result = doctor.probe_heraldry_freshness()
    assert result.ok is True
    assert "skipped" in result.detail


def test_doctor_heraldry_freshness_probe_flags_stale_extract(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-8jz: when the CK3 source dir is newer than the
    recorded extracted_at, the probe reports informational red so the
    rendered table nudges a re-extract without gating exit."""
    import json
    from datetime import UTC, datetime, timedelta

    from chronicler.cli import doctor
    from chronicler.heraldry.extractor import _COA_SUBPATH

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))

    # Fake heraldry dir with a manifest dated 30 days ago + non-empty
    # palette.json + at least one pattern PNG (so extracted=True).
    heraldry_dir = tmp_path / "heraldry"
    (heraldry_dir / "patterns").mkdir(parents=True)
    (heraldry_dir / "patterns" / "p_1.png").write_bytes(b"\x89PNG")
    old_iso = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    (heraldry_dir / "manifest.json").write_text(
        json.dumps({"extracted_at": old_iso, "patterns": [], "emblems_colored": []})
    )
    (heraldry_dir / "palette.json").write_text(json.dumps({"red": "#FF0000"}))

    # Fake CK3 install dir with a fresh CoA subdir mtime (now).
    fake_install = tmp_path / "ck3_install"
    coa_dir = fake_install / _COA_SUBPATH
    coa_dir.mkdir(parents=True)
    monkeypatch.setattr(doctor, "find_ck3_install", lambda: fake_install)

    result = doctor.probe_heraldry_freshness()
    assert result.ok is False
    assert "newer than the last extract" in result.detail
    # Informational — the doctor command's exit code stays 0 in
    # all-other-probes-green when only this one is red.
    assert doctor.PROBE_HERALDRY_FRESH in doctor.INFORMATIONAL_PROBES


def test_smoke_yearly_help_lists_required_options() -> None:
    """ck3_chronicler-8jz: the chronicler smoke-yearly CLI alias surfaces
    the same flags the legacy scripts/smoke_yearly_diff.py took. The
    runbook in docs/patch-playbook.md references them by name.

    Introspects the command's declared options instead of parsing the
    rendered ``--help`` text: under CI's forced-color rich rendering the
    Options panel interleaves ANSI/box characters that broke a literal
    ``"--baseline" in out`` match (ck3_chronicler-27ov.16). The declared
    params are the contract ``--help`` is generated from anyway.
    """
    import typer

    smoke_yearly = typer.main.get_command(app).commands["smoke-yearly"]
    declared = {opt for param in smoke_yearly.params for opt in param.opts}
    for required in ("--baseline", "--save", "--db", "--campaign-id"):
        assert required in declared, f"smoke-yearly must declare {required}; got {sorted(declared)}"


def test_smoke_yearly_delegates_to_run_smoke_yearly(
    runner: CliRunner, env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-8jz: the typer command is a thin wrapper around
    ``run_smoke_yearly``. Stub the real implementation and confirm the
    wrapper translates flags into kwargs verbatim and propagates the
    return code as the typer exit code."""
    captured: dict[str, object] = {}

    def fake_run(*, baseline, save, db, campaign_id):  # type: ignore[no-untyped-def]
        captured["baseline"] = baseline
        captured["save"] = save
        captured["db"] = db
        captured["campaign_id"] = campaign_id
        return 0

    from chronicler.smoke import smoke_yearly

    monkeypatch.setattr(smoke_yearly, "run_smoke_yearly", fake_run)

    result = runner.invoke(
        app,
        [
            "smoke-yearly",
            "--baseline",
            str(env / "b.pkl"),
            "--save",
            str(env / "s.ck3"),
            "--db",
            str(env / "c.db"),
            "--campaign-id",
            "abc-123",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["baseline"] == env / "b.pkl"
    assert captured["save"] == env / "s.ck3"
    assert captured["db"] == env / "c.db"
    assert captured["campaign_id"] == "abc-123"


def test_save_tail_auto_detects_existing_campaign_when_campaign_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-cqo: 'chronicler save-tail' (no --campaign) parses
    the latest save in the configured save-dir and resolves to the
    matching campaign by ck3_playthrough_id."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from chronicler.cli.main import app
    from chronicler.db.registry import create_campaign

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "campaigns" / "erik.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-cqo-test",
        registry=tmp_path / "registry.db",
    )

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    save_file = save_dir / "autosave.ck3"
    save_file.write_bytes(b"opaque")

    fake_snap = SimpleNamespace(
        playthrough_id="uuid-cqo-test",
        bookmark_date="1066.9.15",
        current_date="1067.1.1",
        ck3_version="1.19.0.4",
        player_character_id=32943,
        characters={32943: SimpleNamespace(first_name="Erik", dynasty_house_id=10566)},
        houses_lookup={},
        dynasties_lookup={1504: "Munso"},
        house_to_dynasty={10566: 1504},
    )
    monkeypatch.setattr(
        "chronicler.cli._shared._parse_save_at_with_raw_for_resolve",
        lambda path: ({}, fake_snap),
    )
    # Don't actually run the save-tail loop.
    monkeypatch.setattr("chronicler.cli.ingest.asyncio.run", lambda coro: coro.close() or None)

    runner = CliRunner()
    result = runner.invoke(app, ["save-tail", "--save-dir", str(save_dir)])
    assert result.exit_code == 0, result.stdout
    assert camp.name in result.stdout


def test_save_tail_auto_detect_errors_when_no_save_in_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-cqo: 'chronicler save-tail' (no --campaign) with
    an empty save dir errors out cleanly with a useful message."""
    from typer.testing import CliRunner

    from chronicler.cli.main import app

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    save_dir = tmp_path / "saves"
    save_dir.mkdir()  # empty

    runner = CliRunner()
    result = runner.invoke(app, ["save-tail", "--save-dir", str(save_dir)])
    assert result.exit_code == 1
    assert "no save found" in result.output


def test_save_tail_auto_detect_creates_campaign_with_auto_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-cqo: with no matching campaign, save-tail auto-
    creates one with the '<player> <bookmark-with-dashes>' name."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from chronicler.cli.main import app
    from chronicler.db.registry import list_campaigns

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    fake_snap = SimpleNamespace(
        playthrough_id="uuid-fresh",
        bookmark_date="1066.9.15",
        ck3_version="1.19.0.4",
        player_character_id=32943,
        characters={32943: SimpleNamespace(first_name="Erik", dynasty_house_id=10566)},
        houses_lookup={},
        dynasties_lookup={1504: "Munso"},
        house_to_dynasty={10566: 1504},
    )
    monkeypatch.setattr(
        "chronicler.cli._shared._parse_save_at_with_raw_for_resolve",
        lambda path: ({}, fake_snap),
    )
    monkeypatch.setattr("chronicler.cli.ingest.asyncio.run", lambda coro: coro.close() or None)

    runner = CliRunner()
    result = runner.invoke(app, ["save-tail", "--save-dir", str(save_dir)])
    assert result.exit_code == 0, result.stdout

    # Registry should have one campaign with auto-name + populated
    # founding_dynasty_name + ck3_playthrough_id.
    # ck3_chronicler 2026-05-09: auto-name now prefers the dynasty
    # name over the first character's name — succession changes the
    # latter, but the dynasty is the chronicled spine that stays
    # stable across the campaign's lifetime.
    campaigns = list_campaigns(registry=tmp_path / "registry.db")
    assert len(campaigns) == 1
    assert campaigns[0].name == "Munso 1066-9-15"
    assert campaigns[0].ck3_playthrough_id == "uuid-fresh"
    assert campaigns[0].founding_dynasty_name == "Munso"

    # ck3_chronicler-ylt: the auto-resolve path must also migrate the
    # per-campaign DB file. Without alembic upgrade, run_save_ingest's
    # first repository call hits 'no such table: schema_meta'.
    import sqlite3

    db_path = Path(campaigns[0].db_path)
    assert db_path.exists()
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "schema_meta" in tables
    assert "characters" in tables


def test_dev_auto_detects_when_campaign_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-cqo: 'chronicler dev' with no --campaign exercises
    the same auto-detect path as save-tail. cmd_dev's branching mirrors
    cmd_save_tail; this test guards against drift between the two."""
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from chronicler.cli.main import app
    from chronicler.db.registry import create_campaign

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "campaigns" / "erik.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    camp = create_campaign(
        "Erik 1066-9-15",
        db_path=str(db_path),
        ck3_playthrough_id="uuid-cqo-dev",
        registry=tmp_path / "registry.db",
    )

    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque")

    fake_snap = SimpleNamespace(
        playthrough_id="uuid-cqo-dev",
        bookmark_date="1066.9.15",
        current_date="1067.1.1",
        ck3_version="1.19.0.4",
        player_character_id=32943,
        characters={32943: SimpleNamespace(first_name="Erik", dynasty_house_id=10566)},
        houses_lookup={},
        dynasties_lookup={1504: "Munso"},
        house_to_dynasty={10566: 1504},
    )
    monkeypatch.setattr(
        "chronicler.cli._shared._parse_save_at_with_raw_for_resolve",
        lambda path: ({}, fake_snap),
    )
    # Don't actually start the web server / save-tail loop.
    monkeypatch.setattr("chronicler.cli.ingest.asyncio.run", lambda coro: coro.close() or None)

    runner = CliRunner()
    result = runner.invoke(app, ["dev", "--save-dir", str(save_dir)])
    assert result.exit_code == 0, result.output
    # The auto-detected campaign name appears in the dev startup output
    assert camp.name in result.output


def test_dev_default_pattern_matches_save_tail(runner: CliRunner) -> None:
    """ck3_chronicler-pxk regression: chronicler dev's --pattern default
    must include every entry of DEFAULT_SAVE_PATTERN as a literal
    filename — no wildcard 'autosave*.ck3' (which would catch the
    rolling backups).

    The wildcard form was the v0.5 bug fixed by v05 takeaway #1: it
    matched CK3's rolling backups (autosave_1.ck3 / autosave_2.ck3),
    each carrying a stale playthrough_id; picking one of those as
    baseline poisoned the campaign so every legitimate user save then
    got dropped by the 7on playthrough check.

    ck3_chronicler-fi7 update: DEFAULT_SAVE_PATTERN is now a tuple
    ('autosave.ck3', 'autosave_exit.ck3'); the CLI surfaces it as the
    comma-separated string the watcher splits back into a tuple.
    """
    from chronicler.save import DEFAULT_SAVE_PATTERN

    dev_help = runner.invoke(app, ["dev", "--help"]).output
    save_tail_help = runner.invoke(app, ["save-tail", "--help"]).output

    for pat in DEFAULT_SAVE_PATTERN:
        assert pat in dev_help, (
            f"chronicler dev --help must advertise the {pat!r} default; "
            f"DEFAULT_SAVE_PATTERN={DEFAULT_SAVE_PATTERN!r}"
        )
        assert pat in save_tail_help, (
            f"chronicler save-tail --pattern default drifted; missing {pat!r}; "
            f"DEFAULT_SAVE_PATTERN={DEFAULT_SAVE_PATTERN!r}"
        )
    assert "autosave*.ck3" not in dev_help, (
        "chronicler dev --help still advertises wildcard 'autosave*.ck3' "
        "as the --pattern default; that's the v0.5/pxk regression."
    )


def test_doctor_probe_narrative_backend_openai_compatible_requires_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #21: the endpoint decides which models exist, so an
    unconfigured model is a red — the factory refuses to construct."""
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.delenv("CHRONICLER_OPENAI_MODEL", raising=False)
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "CHRONICLER_OPENAI_MODEL" in result.detail

    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend()
    assert result.ok is True
    assert "ollama" in result.detail
    assert "qwen3:14b" in result.detail


def test_doctor_probe_narrative_backend_openai_compatible_paid_preset_needs_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.delenv("CHRONICLER_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "CHRONICLER_OPENAI_API_KEY" in result.detail

    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", "sk-test")
    result = doctor.probe_narrative_backend()
    assert result.ok is True


def test_doctor_probe_narrative_backend_openai_compatible_never_prints_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`chronicler doctor` output gets pasted into bug reports."""
    from chronicler.cli import doctor

    secret = "sk-doctor-secret-4b2e"
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", secret)
    result = doctor.probe_narrative_backend()
    assert result.ok is True
    assert secret not in result.detail


def test_doctor_probe_narrative_backend_openai_compatible_needs_a_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No preset and no base URL: there is nothing to POST to."""
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "my-model")
    monkeypatch.delenv("CHRONICLER_OPENAI_PRESET", raising=False)
    monkeypatch.delenv("CHRONICLER_OPENAI_BASE_URL", raising=False)
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "CHRONICLER_OPENAI_BASE_URL" in result.detail

    monkeypatch.setenv("CHRONICLER_OPENAI_BASE_URL", "http://localhost:8000/v1")
    result = doctor.probe_narrative_backend()
    assert result.ok is True
    assert "http://localhost:8000/v1" in result.detail


def test_doctor_probe_narrative_backend_openai_compatible_rejects_a_bad_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "not-a-vendor")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "m")
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    assert "not-a-vendor" in result.detail


def test_doctor_probe_narrative_backend_lists_all_three_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unknown-backend red must name every valid backend — a stale
    list is how a user concludes a backend that exists does not."""
    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "gpt4all")
    result = doctor.probe_narrative_backend()
    assert result.ok is False
    for backend in ("claude-code", "anthropic", "openai-compatible"):
        assert backend in result.detail


# ── Issue #30: --check-endpoints reachability probe ──────────────────────


def test_doctor_default_makes_no_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #30: with no flag (check_endpoints=False), the probe must
    not attempt any network request. Assert with a transport that raises
    on any call, not by reading the code."""
    import httpx

    from chronicler.cli import doctor

    def _boom_transport(_request):
        raise AssertionError("default doctor must not make network calls")

    boom_client = httpx.Client(transport=httpx.MockTransport(_boom_transport))

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(_client=boom_client)
    assert result.ok is True
    assert "endpoint reachability is not probed" in result.detail


def test_doctor_check_endpoints_reachable_200_model_in_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(200, json={"data": [{"id": "qwen3:14b"}]})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (200)" in result.detail
    assert "is in the server list" in result.detail


def test_doctor_check_endpoints_reachable_200_model_not_in_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(200, json={"data": [{"id": "other-model"}, {"id": "gpt-4"}]})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (200)" in result.detail
    assert "not found in server model list" in result.detail


def test_doctor_check_endpoints_reachable_401_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(401)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (401)" in result.detail


def test_doctor_check_endpoints_reachable_403_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (403)" in result.detail


def test_doctor_check_endpoints_reachable_404_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (404)" in result.detail
    assert "not implemented" in result.detail


def test_doctor_check_endpoints_reachable_405_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(405)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "reachable (405)" in result.detail
    assert "not implemented" in result.detail


def test_doctor_check_endpoints_reachable_500_is_green_with_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert "responded (500)" in result.detail
    assert "server may be unhealthy" in result.detail


def test_doctor_check_endpoints_connection_refused_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is False
    assert "connection refused" in result.detail
    assert "probably not running" in result.detail


def test_doctor_check_endpoints_connection_refused_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", "sk-test")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is False
    assert "connection refused" in result.detail
    assert "probably not running" not in result.detail


def test_doctor_check_endpoints_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    def _handler(request):
        raise httpx.TimeoutException("timed out")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "ollama")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "qwen3:14b")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is False
    assert "timed out" in result.detail


def test_doctor_check_endpoints_key_sent_in_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    captured_headers: list[dict] = []

    def _handler(request):
        captured_headers.append(dict(request.headers))
        return httpx.Response(200, json={"data": [{"id": "gpt-5.6-luna"}]})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", "sk-test-key")
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is True
    assert len(captured_headers) == 1
    assert captured_headers[0].get("authorization") == "Bearer sk-test-key"


def test_doctor_check_endpoints_key_never_in_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from chronicler.cli import doctor

    secret = "sk-doctor-secret-probe-30"

    def _handler(request):
        raise httpx.TimeoutException("timed out")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", secret)

    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is False
    assert secret not in result.detail

    # Connect error path
    def _handler2(request):
        raise httpx.ConnectError("connection refused")

    client2 = httpx.Client(transport=httpx.MockTransport(_handler2))
    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client2)
    assert result.ok is False
    assert secret not in result.detail


def test_doctor_check_endpoints_transport_error_carries_no_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #34: the transport-error branch reports the exception *type*,
    never its message. A proxy or TLS layer chooses that string, and this
    output is made to be pasted into bug reports."""
    import httpx

    from chronicler.cli import doctor

    leaked = "sk-proxy-echoed-the-header-9f31"

    def _handler(request):
        raise httpx.ProxyError(f"upstream said: authorization=Bearer {leaked}")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", leaked)

    result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
    assert result.ok is False
    assert leaked not in result.detail
    assert "upstream said" not in result.detail
    # The type name is what survives — it is what distinguishes the
    # failure families, and it is bounded.
    assert "ProxyError" in result.detail


def test_doctor_check_endpoints_base_url_trailing_slash_yields_one_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #34: a user-set base URL ending in '/' must probe
    '.../v1/models', not '.../v1//models'."""
    import httpx

    from chronicler.cli import doctor

    requested: list[str] = []

    def _handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "my-model"}]})

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.delenv("CHRONICLER_OPENAI_PRESET", raising=False)
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "my-model")

    for base in ("http://localhost:8000/v1/", "http://localhost:8000/v1"):
        requested.clear()
        monkeypatch.setenv("CHRONICLER_OPENAI_BASE_URL", base)
        client = httpx.Client(transport=httpx.MockTransport(_handler))
        result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
        assert result.ok is True
        assert requested == ["http://localhost:8000/v1/models"]


def test_doctor_check_endpoints_taxonomy_unchanged_by_issue_34(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #34 changed message construction only. 401 and 403 stay green
    — the false-red that bounced #30's first attempt lived two lines away."""
    import httpx

    from chronicler.cli import doctor

    monkeypatch.setenv("CHRONICLER_NARRATIVE_BACKEND", "openai-compatible")
    monkeypatch.setenv("CHRONICLER_OPENAI_PRESET", "openai")
    monkeypatch.setenv("CHRONICLER_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CHRONICLER_OPENAI_API_KEY", "sk-test-key")

    for status in (200, 401, 403, 404, 405):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda _r, s=status: httpx.Response(s, json={}))
        )
        result = doctor.probe_narrative_backend(check_endpoints=True, _client=client)
        assert result.ok is True, f"status {status} must stay green"


def test_doctor_check_endpoints_flag_accepted_by_cli(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, env: Path
) -> None:
    """Issue #30: --check-endpoints is accepted by the doctor CLI."""
    from chronicler.cli import doctor

    monkeypatch.setattr(
        doctor, "probe_ck3_install", _stub_probe(doctor.PROBE_CK3_INSTALL, True, "ok")
    )
    monkeypatch.setattr(
        doctor, "probe_heraldry_palette", _stub_probe(doctor.PROBE_HERALDRY, True, "ok")
    )
    monkeypatch.setattr(
        doctor, "probe_known_campaigns", _stub_probe(doctor.PROBE_CAMPAIGNS, True, "ok")
    )
    monkeypatch.setattr(
        doctor,
        "probe_narrative_backend",
        _stub_probe(doctor.PROBE_NARRATIVE_BACKEND, True, "backend ok"),
    )
    monkeypatch.setattr(
        doctor,
        "ALL_PROBES",
        (
            doctor.probe_ck3_install,
            doctor.probe_narrative_backend,
            doctor.probe_heraldry_palette,
            doctor.probe_known_campaigns,
        ),
    )

    result = runner.invoke(app, ["doctor", "--check-endpoints"])
    assert result.exit_code == 0, result.stdout

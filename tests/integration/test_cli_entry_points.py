"""Integration tests for the Typer CLI entry-point bodies.

ck3_chronicler-ned5: pre-fix, the only CLI test that touched the
``dev`` command monkeypatched ``asyncio.run`` to ``coro.close()``,
which closes the coroutine without running any of the body. The test
asserted stdout — but stdout was written by the resolver *before*
``asyncio.run`` was reached, so the actual orchestration was never
exercised. The launcher regression (baq5) was a divergence between
``dev`` and ``serve`` bodies; a test that called the resolver but
not the orchestrator could never have caught it.

These tests stub the external boundaries at their natural seam:

- ``chronicler.cli.main.run_dev`` (the orchestrator entry point used
  by the ``dev`` command) is replaced with an async no-op that records
  its kwargs.
- ``chronicler.cli.ingest.run_save_ingest`` (used by ``save-tail``) is
  replaced with an async no-op that records its kwargs.
- ``chronicler.cli.main.uvicorn.run`` (used by ``serve``) is replaced
  with a sync no-op that records its args / kwargs.
- ``make_narrative_provider`` is stubbed to a sentinel so we don't
  try to actually wire Claude Code in unit tests.

Then assertions on which kwargs each command body passed verify the
binary "did this command construct a save-ingest task?" question
that the audit named load-bearing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chronicler.cli import ingest as cli_ingest
from chronicler.cli import main as cli_main
from chronicler.db import Base, make_engine_for_path
from chronicler.db.registry import create_campaign
from chronicler.narrative.provider import NarrativeProvider


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def _seeded_campaign(env: Path) -> tuple[str, Path]:
    """A registered campaign + its per-campaign DB, so the CLI commands
    that resolve a campaign by name find one."""
    db_path = env / "campaigns" / "seed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()
    name = "seed-campaign"
    create_campaign(name, db_path=str(db_path))
    return name, db_path


@pytest.fixture
def captured() -> dict[str, Any]:
    return {}


@pytest.fixture
def _stub_boundaries(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Stub uvicorn.run, run_dev, run_save_ingest, and the provider
    constructor so the CLI command bodies run to completion without
    spinning up real subsystems."""

    async def _fake_run_dev(**kwargs: Any) -> None:
        captured["run_dev_kwargs"] = kwargs

    async def _fake_run_save_ingest(**kwargs: Any) -> None:
        captured["run_save_ingest_kwargs"] = kwargs

    def _fake_uvicorn_run(*args: Any, **kwargs: Any) -> None:
        captured["uvicorn_run_args"] = args
        captured["uvicorn_run_kwargs"] = kwargs

    def _fake_provider() -> Any:
        return _FakeProvider()

    # ``run_dev`` is imported inside ``cmd_dev``'s body via
    # ``from chronicler.orchestrator import run_dev``, so we patch the
    # source module — the CLI body's resolved binding will read from
    # there.
    from chronicler import orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "run_dev", _fake_run_dev)
    # ck3_chronicler-64vp: save-tail / serve / dev verbs live in
    # chronicler.cli.ingest now; patch run_save_ingest + the provider
    # constructor there so the command bodies' bindings resolve to the fakes.
    monkeypatch.setattr(cli_ingest, "run_save_ingest", _fake_run_save_ingest)
    monkeypatch.setattr(cli_ingest, "make_narrative_provider", _fake_provider)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", _fake_uvicorn_run)


class _FakeProvider(NarrativeProvider):
    """Stand-in for a NarrativeProvider. ``generate`` is never invoked, but
    ``aclose`` now is: issue #46 made these CLI verbs close the transport they
    construct, so a bare object here would have its missing aclose() logged
    and swallowed and the tests would prove nothing."""

    closes = 0

    @property
    def name(self) -> str:
        return "fake"

    async def generate(self, req):  # pragma: no cover - never invoked
        raise NotImplementedError

    async def aclose(self) -> None:
        type(self).closes += 1


# --- dev ---


def test_dev_constructs_save_ingest_via_orchestrator(
    runner: CliRunner,
    _seeded_campaign: tuple[str, Path],
    _stub_boundaries: None,
    captured: dict[str, Any],
    tmp_path: Path,
) -> None:
    """``chronicler dev --campaign X`` must call orchestrator.run_dev
    with campaign_id set + a non-None biography_provider — that's what
    actually wires save-tail. baq5 was the launcher calling ``serve``
    instead and silently losing this orchestration."""
    name, db_path = _seeded_campaign
    save_dir = tmp_path / "ck3-saves"
    save_dir.mkdir()

    result = runner.invoke(
        cli_main.app,
        [
            "dev",
            "--campaign",
            name,
            "--save-dir",
            str(save_dir),
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
        ],
    )
    assert result.exit_code == 0, result.stdout

    kwargs = captured["run_dev_kwargs"]
    assert kwargs["campaign_id"] is not None
    assert kwargs["db_path"] == Path(db_path)
    assert kwargs["save_dir"] == save_dir
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8123
    # The load-bearing binary: dev wires a biography_provider unless
    # --no-biography is passed; save-tail observes it.
    assert kwargs["biography_provider"] is not None
    # Conversely, save-tail's headless path should NOT have fired —
    # dev runs save-tail via the orchestrator, not via the CLI's own
    # asyncio.run(run_save_ingest(...)).
    assert "run_save_ingest_kwargs" not in captured


def test_dev_no_biography_passes_none_provider(
    runner: CliRunner,
    _seeded_campaign: tuple[str, Path],
    _stub_boundaries: None,
    captured: dict[str, Any],
    tmp_path: Path,
) -> None:
    """--no-biography flips biography_provider to None on the way into
    run_dev. The save-tail loop still spawns; just no LLM work."""
    name, _ = _seeded_campaign
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    result = runner.invoke(
        cli_main.app,
        ["dev", "--campaign", name, "--save-dir", str(save_dir), "--no-biography"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["run_dev_kwargs"]["biography_provider"] is None


# --- serve ---


def test_serve_runs_uvicorn_does_not_spawn_save_ingest(
    runner: CliRunner,
    env: Path,
    _stub_boundaries: None,
    captured: dict[str, Any],
) -> None:
    """``chronicler serve`` must call uvicorn.run but NEVER construct
    a save-tail task. baq5: the headless launcher used serve when it
    should have used dev, and save-tail was silently absent."""
    result = runner.invoke(cli_main.app, ["serve", "--port", "8543"])
    assert result.exit_code == 0, result.stdout
    assert "uvicorn_run_args" in captured
    # Specifically: serve does NOT path through run_dev (the orchestrator)
    # OR run_save_ingest directly. Both should be untouched.
    assert "run_dev_kwargs" not in captured
    assert "run_save_ingest_kwargs" not in captured
    # Port flowed through.
    assert captured["uvicorn_run_kwargs"]["port"] == 8543


def test_serve_with_reload_uses_factory_with_default_provider(
    runner: CliRunner,
    env: Path,
    _stub_boundaries: None,
    captured: dict[str, Any],
) -> None:
    """``serve --reload`` must invoke uvicorn.run with the
    create_app_with_default_provider factory string (ck3_chronicler-
    pr7m). Pre-fix it used 'chronicler.api:create_app' which took no
    kwargs and left narrative_provider unset."""
    result = runner.invoke(cli_main.app, ["serve", "--reload"])
    assert result.exit_code == 0, result.stdout
    args = captured["uvicorn_run_args"]
    assert args, "uvicorn.run must have been called"
    assert args[0] == "chronicler.api:create_app_with_default_provider"
    assert captured["uvicorn_run_kwargs"]["factory"] is True
    assert captured["uvicorn_run_kwargs"]["reload"] is True


# --- save-tail ---


def test_save_tail_invokes_run_save_ingest_with_no_event_bus(
    runner: CliRunner,
    _seeded_campaign: tuple[str, Path],
    _stub_boundaries: None,
    captured: dict[str, Any],
    tmp_path: Path,
) -> None:
    """``chronicler save-tail`` is the standalone diagnostic — it
    calls run_save_ingest WITHOUT an event_bus argument (so the
    save-tail loop's call site sees event_bus=None). This is the
    documented twin to dev; the WARNING ck3_chronicler-9qiw added
    surfaces the intent on stderr."""
    name, db_path = _seeded_campaign
    save_dir = tmp_path / "saves2"
    save_dir.mkdir()

    result = runner.invoke(
        cli_main.app,
        ["save-tail", "--campaign", name, "--save-dir", str(save_dir)],
    )
    assert result.exit_code == 0, result.stdout

    kwargs = captured["run_save_ingest_kwargs"]
    assert kwargs["save_dir"] == save_dir
    assert kwargs["db_path"] == Path(db_path)
    assert kwargs["campaign_id"] is not None
    assert kwargs["biography_provider"] is not None
    # The salient binary: event_bus was NOT passed (the CLI body
    # deliberately omits the kwarg, defaulting it to None inside
    # run_save_ingest; see ck3_chronicler-9qiw).
    assert "event_bus" not in kwargs
    # baq5 warning must surface on stderr.
    assert "save-tail" in result.output.lower()
    # run_dev must NOT have been called from save-tail's body.
    assert "run_dev_kwargs" not in captured


# --- sanity check: the anti-pattern is not used ---


def test_no_test_in_this_file_stubs_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documentation test: the bd named ``coro.close()``-style
    asyncio.run stubbing as the anti-pattern that masked baq5. This
    test asserts our own conftest doesn't accidentally reintroduce it
    via a monkeypatch on asyncio.run — the real asyncio.run remains
    untouched."""
    assert asyncio.run is asyncio.run  # tautology — asserts the import was real


def test_dev_closes_the_narrative_transport_it_constructed(
    runner: CliRunner,
    _seeded_campaign: tuple[str, Path],
    _stub_boundaries: None,
    captured: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Issue #46: these verbs own the provider they build, and the two HTTP
    transports hold an httpx.AsyncClient. Nothing closed it before."""
    name, _db_path = _seeded_campaign
    save_dir = tmp_path / "ck3-saves"
    save_dir.mkdir()
    _FakeProvider.closes = 0

    result = runner.invoke(
        cli_main.app,
        ["dev", "--campaign", name, "--save-dir", str(save_dir)],
    )

    assert result.exit_code == 0, result.output
    assert captured.get("run_dev_kwargs") is not None, "run_dev was never reached"
    assert _FakeProvider.closes == 1, "the CLI left the narrative transport open"

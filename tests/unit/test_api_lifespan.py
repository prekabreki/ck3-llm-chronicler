"""App-lifespan lifecycle tests (ck3_chronicler-g0ku).

The startup backfill (archived-snapshot bootstrap + the one-time column
backfills) runs in a thread fired from the lifespan via
``run_in_executor(None, _run_backfill)``. Pre-fix that future was never
awaited, so the lifespan's shutdown (``__aexit__``) returned while the worker
thread was still doing multi-DB work — it only got joined later, implicitly,
when the ASGI server tore the event loop down. Relying on that implicit join
left the backfill racing ``cache.dispose_all()`` (in the lifespan ``finally``)
and any teardown the server runs before the loop closes — the established (if
rare) mechanism class behind the
``test_llm_pause_put_false_triggers_drain_across_open_campaigns`` flake.

The lifespan now owns its backfill thread: it tracks the future and joins it
on shutdown, before ``dispose_all``, so the thread can never outlive the
lifespan regardless of the server's executor handling.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from chronicler.api import create_app
from chronicler.api.app import _lifespan


def test_lifespan_shutdown_joins_startup_backfill_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lifespan shutdown must not return while the backfill thread is still
    running. Driving ``_lifespan`` directly (rather than through TestClient)
    isolates the lifespan's own join from the ASGI server's implicit
    executor-join at loop teardown — which otherwise masks the difference.

    The first backfill step (archived-snapshot bootstrap) is stubbed slow so
    the race window is wide: pre-fix ``__aexit__`` returns mid-sleep and
    ``finished`` is unset; the fix joins the future so it is always set.
    """
    from chronicler import sync

    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))

    started = threading.Event()
    finished = threading.Event()

    def _slow_bootstrap(*_args: object, **_kwargs: object) -> int:
        started.set()
        time.sleep(0.5)
        finished.set()
        return 0

    # _run_backfill does `from chronicler.sync import bootstrap_archived_snapshots`
    # at call time, so patching the module attribute swaps in our slow stub.
    monkeypatch.setattr(sync, "bootstrap_archived_snapshots", _slow_bootstrap)

    app = create_app(registry_path=tmp_path / "registry.db")

    async def _drive() -> bool:
        cm = _lifespan(app)
        await cm.__aenter__()  # startup fires the backfill thread
        # The thread is running our slow stub but has NOT finished — confirms
        # we are genuinely inside the race window the bug exploited.
        assert started.wait(timeout=2.0), "startup backfill thread never started"
        assert not finished.is_set()
        await cm.__aexit__(None, None, None)  # shutdown
        # The assert happens here, inside the loop, BEFORE asyncio.run's own
        # shutdown_default_executor join — so it measures the lifespan's join,
        # not the loop's.
        return finished.is_set()

    joined = asyncio.run(_drive())

    assert joined, (
        "lifespan shutdown returned before the startup backfill thread "
        "finished - the executor future was not joined in __aexit__ "
        "(g0ku flake mechanism)"
    )


# --- issue #46: aclose() on every shutdown path ---


class _ClosingProvider:
    """Minimal stand-in that records aclose() calls.

    Deliberately not a NarrativeProvider subclass — the lifespan must not
    care what it got, only that it can be closed.
    """

    def __init__(self) -> None:
        self.closes = 0

    @property
    def name(self) -> str:
        return "recording:v1"

    async def aclose(self) -> None:
        self.closes += 1


def test_lifespan_closes_the_narrative_provider_on_clean_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two HTTP transports own an httpx.AsyncClient and nothing used to
    call aclose() — the leak this fixes."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    provider = _ClosingProvider()
    app = create_app(registry_path=tmp_path / "registry.db", narrative_provider=provider)

    async def _drive() -> None:
        cm = _lifespan(app)
        await cm.__aenter__()
        assert provider.closes == 0
        await cm.__aexit__(None, None, None)

    asyncio.run(_drive())
    assert provider.closes == 1


def test_lifespan_closes_the_provider_on_the_exception_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaked connection pool matters MOST when shutdown was not clean, so
    the close lives in the finally, not after the yield."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    provider = _ClosingProvider()
    app = create_app(registry_path=tmp_path / "registry.db", narrative_provider=provider)

    async def _drive() -> None:
        cm = _lifespan(app)
        await cm.__aenter__()
        boom = RuntimeError("server died mid-serve")
        # __aexit__ with exception info is how an ASGI server reports a
        # crash to the lifespan. It returns False rather than raising —
        # the generator lets the thrown exception propagate instead of
        # swallowing it, so `async with` is what re-raises. False here is
        # the assertion that matters: the lifespan did not suppress it.
        suppressed = await cm.__aexit__(type(boom), boom, boom.__traceback__)
        assert suppressed is not True, "the lifespan swallowed the app's exception"

    asyncio.run(_drive())
    assert provider.closes == 1, "aclose() was skipped when the app raised"


def test_lifespan_survives_a_provider_whose_aclose_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure closing the client must not mask the original exception or
    strand the engine cache."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))

    class _Hostile(_ClosingProvider):
        async def aclose(self) -> None:
            self.closes += 1
            raise OSError("socket already gone")

    provider = _Hostile()
    app = create_app(registry_path=tmp_path / "registry.db", narrative_provider=provider)

    async def _drive() -> None:
        cm = _lifespan(app)
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)  # must not raise

    asyncio.run(_drive())
    assert provider.closes == 1


def test_lifespan_without_a_provider_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    app = create_app(registry_path=tmp_path / "registry.db")

    async def _drive() -> None:
        cm = _lifespan(app)
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)

    asyncio.run(_drive())

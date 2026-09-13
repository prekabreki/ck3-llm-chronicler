"""Issue #7: signal-driven shutdown, tested by delivering real signals to a
real process.

The bug these cover ran for months underneath a passing test. ``j86v``
verified teardown by calling ``parse_pool.shutdown()`` in-process and
concluded "0 rakaly orphans survive" — true, and irrelevant, because the
failure was that the teardown code *never ran*. ``uvicorn.Server.serve()``
wraps its work in ``capture_signals()``, which restores the pre-existing
handler on the way out and then re-raises the captured signal so the caller
gets the behaviour it originally asked for. For SIGTERM that restored
handler was ``SIG_DFL``: the process died inside ``await server.serve()``
and every enclosing ``finally`` — including the one that shuts the parse
pool down — was skipped. SIGINT was fine, because its default raises
``KeyboardInterrupt``, an ordinary exception the stack can unwind through.

Measured on Linux 2026-08-13 against `chronicler dev`: SIGTERM left all 3
parse workers alive and reparented to init; SIGINT on the same build reaped
them. Hence "only ever seen from pkill, never from Ctrl-C" in the report.

So these tests spawn a real subprocess, put a real ``uvicorn.Server``'s
``capture_signals()`` around a wait loop, and send a real signal. Nothing is
mocked, which means they also pin uvicorn's contract: if a future uvicorn
stops re-raising, ``test_uvicorn_still_re_raises_sigterm`` fails and tells us
the workaround can go.

A port is never bound — ``capture_signals`` is the whole mechanism under
test, and binding one would make these flaky for no coverage.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

# Generous: these spawn a fresh interpreter and import uvicorn. The assertion
# is never "it finished fast", only "it finished" — a hang is the failure.
_TIMEOUT = 60

_PROGRAM = textwrap.dedent(
    """
    import asyncio, os, signal, sys, time
    import uvicorn
    from chronicler.orchestrator import absorb_sigterm_reraise

    marker = sys.argv[1]
    absorb = sys.argv[2].startswith("absorb")
    # Stand-in for real teardown taking real time (the first-save auto-import
    # ran ~2 minutes on the owner's machine).
    teardown_delay = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    async def main():
        server = uvicorn.Server(uvicorn.Config(app=None, log_config=None))

        async def serve_until_signalled():
            # The real capture_signals context manager: installs uvicorn's
            # handler, restores the previous one on exit, then re-raises.
            with server.capture_signals():
                while not server.should_exit:
                    await asyncio.sleep(0.02)

        try:
            if absorb:
                with absorb_sigterm_reraise():
                    await serve_until_signalled()
            else:
                await serve_until_signalled()
        finally:
            # Stands in for the three nested teardown blocks in the real
            # stack (_run_server, run_dev, run_save_ingest). If the process
            # is killed by the re-raised SIGTERM, this never runs and the
            # marker never appears — which is exactly what orphaned the
            # parse-pool workers.
            if teardown_delay:
                time.sleep(teardown_delay)
            with open(marker, "w") as fh:
                fh.write("teardown ran")

    asyncio.run(main())
    print("READY-EXITED", flush=True)
    """
)


def _spawn(
    tmp_path: Path, mode: str, teardown_delay: float = 0.0
) -> tuple[subprocess.Popen[str], Path]:
    marker = tmp_path / f"teardown-{mode}.marker"
    proc = subprocess.Popen(
        [sys.executable, "-c", _PROGRAM, str(marker), mode, str(teardown_delay)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Give the child time to import uvicorn and enter capture_signals.
    # Signalling before the handlers are installed would exercise the default
    # disposition and pass for the wrong reason — the precise failure mode
    # this file exists to rule out.
    time.sleep(2.0)
    if proc.poll() is not None:
        out, err = proc.communicate()
        pytest.fail(f"helper exited before it could be signalled: {proc.returncode}\n{out}\n{err}")
    return proc, marker


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_sigterm_reaches_teardown_with_the_absorber(tmp_path: Path) -> None:
    """The fix: teardown runs on SIGTERM."""
    proc, marker = _spawn(tmp_path, "absorb")
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=_TIMEOUT)

    assert marker.is_file(), (
        "teardown did not run — the process was killed by the re-raised "
        "SIGTERM before its finally blocks could execute"
    )
    assert marker.read_text() == "teardown ran"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_uvicorn_still_re_raises_sigterm(tmp_path: Path) -> None:
    """Without the absorber, the same program dies to the re-raised signal
    and never reaches teardown.

    This is the bug, pinned. It doubles as a contract test on uvicorn: the
    day this starts passing a marker file, uvicorn has changed its
    capture_signals behaviour and `absorb_sigterm_reraise` can be retired.
    """
    proc, marker = _spawn(tmp_path, "plain")
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=_TIMEOUT)

    assert not marker.is_file(), (
        "teardown ran without the absorber — uvicorn no longer re-raises the "
        "captured SIGTERM, so absorb_sigterm_reraise is obsolete (see #7)"
    )
    # Killed by the signal, not a clean exit.
    assert proc.returncode == -signal.SIGTERM


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_second_sigterm_during_a_slow_teardown_exits_immediately(tmp_path: Path) -> None:
    """Escalation: shutdown must not become the new hang.

    Real teardown can take real time — the first-save auto-import of a 26MB
    save ran ~2 minutes on the owner's machine, and ``asyncio.run`` joins that
    thread before the process exits. A user who will not wait, or an init
    system past its stop timeout, must still be able to end it.

    They can, and by scope rather than by any counter: the absorber's context
    ends when ``server.serve()`` returns, restoring ``SIG_DFL`` *before* the
    long teardown begins. So the absorber covers exactly the window it needs
    to — uvicorn's re-raise — and hands the kill switch straight back.

    Asserted with a deliberately slow teardown, because with an instant one
    the process is already gone before a second signal could land.
    """
    proc, marker = _spawn(tmp_path, "absorb", teardown_delay=30.0)
    proc.send_signal(signal.SIGTERM)
    time.sleep(1.0)  # let it get into the slow teardown
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=_TIMEOUT)

    assert proc.returncode == -signal.SIGTERM, (
        f"second SIGTERM did not kill the process (rc={proc.returncode}) — "
        "the absorber is still installed during teardown, which would make "
        "shutdown unkillable"
    )
    # Never finished its 30s teardown, so the marker was never written.
    assert not marker.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_sigint_reaches_teardown_either_way(tmp_path: Path) -> None:
    """SIGINT was never the broken path, and must stay unbroken — the
    absorber touches SIGTERM only.
    """
    proc, marker = _spawn(tmp_path, "absorb")
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=_TIMEOUT)
    assert marker.is_file()

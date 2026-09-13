"""Subprocess wrapper around the ``rakaly`` CLI.

rakaly (https://github.com/rakaly/cli) converts CK3 ``.ck3`` saves to
JSON. We use it as a subprocess rather than building our own Rust
crate or PyO3 binding — rakaly publishes pre-built binaries for every
major platform, so the chronicler install reduces to "drop the
binary in the repo or PATH and go."

Performance (ck3_chronicler-ve26, measured warm-cache on a real 74 MB
autosave → ~97 MB JSON): the ``json`` subcommand melts in ~1.7 s
(~15 %); decoding the blob is the dominant cost at ~8.4 s with
stdlib ``json`` (~73 %); ``parse_save`` is ~1.5 s (~13 %). So the
decode — not rakaly — is the parse floor. We capture the full output
into memory and decode via :func:`msgspec.json.decode` (~35–40 %
faster, ~25 % lower peak heap, byte-identical ``dict``). Streaming
(``ijson`` keyed to the top-level paths ``parse_save`` needs) was
evaluated and rejected: it was *slower* than a C decoder, only
marginally cut peak RSS (the kept sections — ``living`` /
``dead_unprunable`` / ``landed_titles`` — dominate), and broke output
parity. A PyO3 jomini binding (rakaly "library mode") was rejected too:
no maintained Python binding exists, and a custom one would attack only
the ~15 % rakaly slice while breaking the "drop the binary and go" +
PyInstaller packaging story.

Limitation: ironman saves require external token data not distributed
with rakaly. chronicler scope excludes ironman per docs/architecture.md, so
ironman support is not implemented.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import msgspec

DEFAULT_RAKALY_TIMEOUT_SECONDS = 300.0  # 5 min — generous for late-game saves

# F-50: keep N most-recent rakaly-failure dumps under <data-dir>/rakaly-failures/.
# Tuned high enough to capture a debugging session's worth of repeats and
# low enough that a runaway loop can't fill the disk.
RAKALY_FAILURE_KEEP = 5

log = logging.getLogger(__name__)


def _subprocess_creationflags() -> int:
    """ck3_chronicler-3v0s follow-up: on Windows, suppress the console
    window that the rakaly subprocess otherwise pops for every save
    parse. With monthly autosaves + auto-resume draining a backlog,
    each parse flashes a black terminal that interrupts CK3 gameplay.

    Pairs with :func:`_subprocess_startupinfo` — flags alone aren't
    sufficient for .cmd / .bat shims (CK3 mod tooling, claude.cmd) that
    spawn cmd.exe internally; STARTF_USESHOWWINDOW + SW_HIDE prevents
    the cmd.exe flash.
    """
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS


def _subprocess_startupinfo():
    """ck3_chronicler-3v0s follow-up: belt-and-suspenders for the
    creationflags helper above. Returns a STARTUPINFO that explicitly
    hides any console window the subprocess (or grandchildren) tries
    to open — covers the .cmd-shim flash case that CREATE_NO_WINDOW +
    DETACHED_PROCESS doesn't always catch on Windows.
    """
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


class RakalyError(RuntimeError):
    """Raised when rakaly returns a non-zero exit or unparseable output."""


class RakalyNotFoundError(RakalyError):
    """Raised when no rakaly binary can be located on PATH or in the repo."""


def _dump_rakaly_failure(stdout: bytes, save_name: str, decode_error: str) -> Path | None:
    """F-50: persist the full failing rakaly stdout for debugging.

    The original error message clipped stdout to 200 bytes, which is
    almost never enough to diagnose JSON-decode failures (the error
    typically lands hundreds of KB into a 72 MB blob). Writes the full
    output plus a small metadata header to
    ``<data-dir>/rakaly-failures/<timestamp>.json`` and rotates older
    dumps so only :data:`RAKALY_FAILURE_KEEP` survive.

    Best-effort: any exception during dump (no data dir, permission
    denied, disk full) is logged and swallowed — the caller still gets
    a :class:`RakalyError`. Imports ``get_data_dir`` lazily to avoid a
    save→db circular at module load.
    """
    try:
        from chronicler.db.registry import get_data_dir

        failures_dir = get_data_dir() / "rakaly-failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = failures_dir / f"{ts}-{save_name}.bin"
        # Minimal header line + raw stdout. We don't try to coerce the
        # bytes into text — by definition this stdout failed JSON decode,
        # so it may not be valid UTF-8 either.
        header = f"# rakaly failure {ts} save={save_name} decode_error={decode_error!r}\n"
        with target.open("wb") as f:
            f.write(header.encode("utf-8", errors="replace"))
            f.write(stdout)
        # Rotate: keep only the N most-recent dumps by mtime.
        dumps = sorted(failures_dir.glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in dumps[RAKALY_FAILURE_KEEP:]:
            with contextlib.suppress(OSError):
                stale.unlink()
        return target
    except Exception as e:
        log.warning("could not persist rakaly failure dump: %s", e)
        return None


def find_rakaly() -> str | None:
    """Locate the rakaly executable.

    Preference order:
    1. ``rakaly`` / ``rakaly.exe`` on PATH (system install)
    2. Repo-local extracted release at ``rakaly-*/<platform>/rakaly[.exe]``
       or ``rakaly-*/rakaly[.exe]`` (flat layout)

    Returns the absolute path to the binary, or ``None`` if not found.
    """
    on_path = shutil.which("rakaly") or shutil.which("rakaly.exe")
    if on_path:
        return on_path

    repo_root = Path(__file__).resolve().parents[3]
    for release_dir in sorted(repo_root.glob("rakaly-*")):
        if not release_dir.is_dir():
            continue
        for exe_name in ("rakaly.exe", "rakaly"):
            direct = release_dir / exe_name
            if direct.is_file():
                return str(direct)
            for inner in release_dir.iterdir():
                if not inner.is_dir():
                    continue
                nested = inner / exe_name
                if nested.is_file():
                    return str(nested)
    return None


# ck3_chronicler-27ov.76 (audit L2): the sync and async converters shared
# ~60 lines of preamble / argv / error handling that had already drifted
# (the non-zero-exit stderr decode differed between them). These helpers
# are the single source of truth for both paths.


def _resolve_rakaly_binary(rakaly_path: str | None) -> str:
    """Return the rakaly binary path or raise :class:`RakalyNotFoundError`."""
    binary = rakaly_path or find_rakaly()
    if binary is None:
        raise RakalyNotFoundError(
            "rakaly binary not found on PATH or in repo-local rakaly-*/. "
            "Download from https://github.com/rakaly/cli/releases/latest."
        )
    return binary


def _rakaly_argv(binary: str, save_path: Path) -> list[str]:
    """The ``rakaly json`` command line.

    ck3_chronicler-7ao: ``--duplicate-keys=group`` preserves repeated keys
    as arrays. CK3's coat_of_arms section uses Paradox's multi-key syntax
    for multi-charge shields ("colored_emblem={ instance={...}
    instance={...} ...}" yields 6 bars on the Barcelona dynasty arms); the
    default mode keeps only the last value, collapsing 6 bars into 1.
    """
    return [binary, "json", "--duplicate-keys", "group", str(save_path)]


def _raise_for_returncode(returncode: int | None, stderr: bytes | None) -> None:
    """Raise :class:`RakalyError` if rakaly exited non-zero."""
    if returncode == 0:
        return
    detail = (stderr or b"").decode("utf-8", errors="replace").strip()
    raise RakalyError(f"rakaly exited with code {returncode}: {detail or '(no stderr)'}")


def _loads_or_raise(stdout: bytes, save_name: str) -> dict:
    """Parse rakaly's stdout as JSON or raise a debuggable :class:`RakalyError`.

    Uses :func:`msgspec.json.decode` rather than the stdlib ``json``
    (ck3_chronicler-ve26): on real 8–74 MB autosaves it decodes the
    ~70–97 MB rakaly blob ~35–40 % faster and with ~25 % lower peak heap
    (it caches repeated object keys, of which CK3 saves have many),
    producing a byte-identical ``dict``. The decode is the dominant
    per-save cost — ~73 % of wall-clock — so this is the single biggest
    lever on the parse floor; the rakaly subprocess is only ~15 %.

    On a decode failure, surface the first 200 bytes inline and dump the
    full payload to a rotated file under ``<data-dir>/rakaly-failures/``
    so the error (typically deep into a ~72 MB blob, well past 200 bytes)
    can be inspected post-hoc (F-50). Avoids logging the full blob.
    """
    try:
        return msgspec.json.decode(stdout)
    except msgspec.DecodeError as e:
        head = stdout[:200].decode("utf-8", errors="replace")
        dump_path = _dump_rakaly_failure(stdout, save_name, str(e))
        location = f"; full output at {dump_path}" if dump_path is not None else ""
        raise RakalyError(
            f"rakaly output was not valid JSON: {e}; first 200 bytes: {head!r}{location}"
        ) from e


def convert_save_to_json(
    save_path: Path,
    *,
    rakaly_path: str | None = None,
    timeout: float = DEFAULT_RAKALY_TIMEOUT_SECONDS,
) -> dict:
    """Run ``rakaly json`` against ``save_path``; return the parsed dict.

    :param save_path: path to a ``.ck3`` save file (regular plaintext or
        binary; ironman not supported — see module docstring).
    :param rakaly_path: optional explicit path to the rakaly binary.
        If omitted, calls :func:`find_rakaly`.
    :param timeout: subprocess timeout in seconds.

    :raises RakalyNotFoundError: if no rakaly binary can be located.
    :raises FileNotFoundError: if ``save_path`` doesn't exist.
    :raises RakalyError: if rakaly returns non-zero or output isn't JSON.
    """
    if not save_path.is_file():
        raise FileNotFoundError(f"save file not found: {save_path}")

    binary = _resolve_rakaly_binary(rakaly_path)

    try:
        completed = subprocess.run(
            _rakaly_argv(binary, save_path),
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=_subprocess_creationflags(),
            startupinfo=_subprocess_startupinfo(),
        )
    except subprocess.TimeoutExpired as e:
        raise RakalyError(f"rakaly timed out after {timeout}s on {save_path.name}") from e

    _raise_for_returncode(completed.returncode, completed.stderr)
    return _loads_or_raise(completed.stdout, save_path.name)


async def convert_save_to_json_async(
    save_path: Path,
    *,
    rakaly_path: str | None = None,
    timeout: float = DEFAULT_RAKALY_TIMEOUT_SECONDS,
) -> dict:
    """Async variant of :func:`convert_save_to_json` using
    ``asyncio.create_subprocess_exec`` so the rakaly child is killable
    on cancellation (ck3_chronicler-aerw slice 2).

    The previous async path wrapped the sync :func:`convert_save_to_json`
    in :func:`asyncio.to_thread`. That unsticks the event loop during the
    parse but is uncancellable: a Ctrl+C hit during rakaly waited for
    the child's 300 s timeout before the loop tore down. Using
    ``create_subprocess_exec`` lets cancellation (and the explicit
    timeout) terminate the rakaly process immediately.

    The JSON-decode step still runs in a worker thread via
    :func:`asyncio.to_thread` — it's CPU-bound for a ~70 MB blob and
    blocks the loop otherwise; not cancellable, but bounded.

    :raises RakalyNotFoundError: rakaly binary not found.
    :raises FileNotFoundError: ``save_path`` does not exist.
    :raises RakalyError: rakaly returned non-zero or output isn't JSON.
    :raises asyncio.CancelledError: re-raised after killing the child.
    """
    if not save_path.is_file():
        raise FileNotFoundError(f"save file not found: {save_path}")

    binary = _resolve_rakaly_binary(rakaly_path)

    proc = await asyncio.create_subprocess_exec(
        *_rakaly_argv(binary, save_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_subprocess_creationflags(),
        startupinfo=_subprocess_startupinfo(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        # asyncio.wait_for raises TimeoutError on Python 3.11+
        # (asyncio.TimeoutError is the same class).
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise RakalyError(f"rakaly timed out after {timeout}s on {save_path.name}") from e
    except asyncio.CancelledError:
        # Ctrl+C / task cancellation: kill the child synchronously so we
        # don't strand a 70 MB-stdout subprocess outliving the loop.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise

    _raise_for_returncode(proc.returncode, stderr)
    # The decode (CPU-bound on a ~70 MB blob) and the on-failure dump
    # (disk I/O) both run in a worker thread so neither blocks the loop.
    return await asyncio.to_thread(_loads_or_raise, stdout, save_path.name)

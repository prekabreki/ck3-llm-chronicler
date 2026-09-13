"""Benchmark the save-ingest parse floor and the process-pool IPC tradeoff.

ck3_chronicler-j86v groundwork. Reproduces, on-machine, the numbers the
process-pool design rests on so they can be re-confirmed instead of trusted
from a since-deleted scratchpad script:

  1. The per-save cost floor, split into its three stages:
       - rakaly melt (subprocess)      -- already async + parallelizable
       - msgspec JSON decode           -- GIL-held, the real bottleneck
       - parse_save (dict -> snapshot) -- GIL-bound Python
  2. The decode GIL test: N-way threaded decode vs N serial decodes. A
     speedup of ~1.0x proves the decode serialises onto one core under the
     GIL, which is why asyncio.to_thread + parse_concurrency never spread it.
  3. The process-pool IPC tradeoff that decides the worker's return value:
     pickling the raw decoded dict (what a naive ProcessPoolExecutor returns)
     vs pickling only the SaveSnapshot. The dict round-trip was measured to
     exceed the entire parse floor -- hence "worker returns ONLY the snapshot".
  4. ck3_chronicler-lw47: how that snapshot crosses the boundary. j86v
     returned the SaveSnapshot object, so the executor PICKLED it and the
     parent-side UNPICKLE became the serial/GIL-bound tail of a backlog drain.
     msgspec.msgpack encodes the frozen dataclass directly (no Struct
     conversion) to a bytes blob the worker returns and the parent decodes --
     the executor only (un)pickles `bytes` (near-free), and the parent decode
     is ~2.6x cheaper than the pickle load. The row below quantifies it.

This is a measurement tool, not a test: it prints a table and asserts
nothing. Run it on the target box; numbers are hardware-specific.

Usage:
    uv run python scripts/bench_parse.py
    uv run python scripts/bench_parse.py --save tests/fixtures/saves/autosave_exit.ck3 \
        --repeat 3 --threads 4
"""

from __future__ import annotations

import argparse
import pickle
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import msgspec

from chronicler.save.parse import parse_save
from chronicler.save.rakaly import (
    _loads_or_raise,
    _raise_for_returncode,
    _rakaly_argv,
    _resolve_rakaly_binary,
    _subprocess_creationflags,
    _subprocess_startupinfo,
)

DEFAULT_SAVE = Path("tests/fixtures/saves/autosave_exit.ck3")
HIGHEST = pickle.HIGHEST_PROTOCOL  # what ProcessPoolExecutor uses for IPC


def _melt(save_path: Path, binary: str) -> bytes:
    """Run `rakaly json` and return the raw JSON stdout bytes (melt only)."""
    completed = subprocess.run(
        _rakaly_argv(binary, save_path),
        capture_output=True,
        check=False,
        creationflags=_subprocess_creationflags(),
        startupinfo=_subprocess_startupinfo(),
    )
    _raise_for_returncode(completed.returncode, completed.stderr)
    return completed.stdout


def _timed(label: str, fn, repeat: int):
    """Run fn() `repeat` times, return (result_of_last_run, median_seconds)."""
    times: list[float] = []
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    med = statistics.median(times)
    print(
        f"  {label:<34} {med:6.2f} s  (median of {repeat}: {', '.join(f'{t:.2f}' for t in times)})"
    )
    return result, med


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--save",
        type=Path,
        default=DEFAULT_SAVE,
        help=f"path to a .ck3 save (default: {DEFAULT_SAVE})",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="repetitions per stage; reports the median (default: 3)",
    )
    ap.add_argument(
        "--threads", type=int, default=4, help="thread count for the decode GIL test (default: 4)"
    )
    args = ap.parse_args()

    save_path: Path = args.save
    if not save_path.is_file():
        print(f"save not found: {save_path}", file=sys.stderr)
        return 1
    binary = _resolve_rakaly_binary(None)

    print(f"save: {save_path} ({_mb(save_path.stat().st_size)} on disk)")
    print(f"rakaly: {binary}\n")

    # --- Stage 1: melt (subprocess) ---
    print("STAGE TIMINGS (per save)")
    stdout, t_melt = _timed(
        "rakaly melt (subprocess)", lambda: _melt(save_path, binary), args.repeat
    )
    assert stdout is not None
    print(f"  -> melted JSON is {_mb(len(stdout))}")

    # --- Stage 2: decode (GIL-held) ---
    data, t_decode = _timed(
        "msgspec decode (bytes -> dict)",
        lambda: _loads_or_raise(stdout, save_path.name),
        args.repeat,
    )
    assert data is not None

    # --- Stage 3: parse_save ---
    snap, t_parse = _timed("parse_save (dict -> snapshot)", lambda: parse_save(data), args.repeat)
    assert snap is not None

    floor = t_melt + t_decode + t_parse
    print(f"\n  per-save floor (melt+decode+parse): {floor:.2f} s")
    print(
        f"    melt   {t_melt:5.2f} s  ({100 * t_melt / floor:4.1f}%)  [parallelizable subprocess]"
    )
    print(
        f"    decode {t_decode:5.2f} s  ({100 * t_decode / floor:4.1f}%)  [GIL-held -- bottleneck]"
    )
    print(f"    parse  {t_parse:5.2f} s  ({100 * t_parse / floor:4.1f}%)  [GIL-bound Python]")

    # --- Decode GIL test: N threads vs N serial ---
    print(f"\nDECODE GIL TEST ({args.threads} decodes)")
    serial_t0 = time.perf_counter()
    for _ in range(args.threads):
        _loads_or_raise(stdout, save_path.name)
    serial = time.perf_counter() - serial_t0

    pool_t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(lambda _: _loads_or_raise(stdout, save_path.name), range(args.threads)))
    threaded = time.perf_counter() - pool_t0

    speedup = serial / threaded if threaded else float("nan")
    print(f"  {args.threads}x serial decode:   {serial:6.2f} s")
    print(f"  {args.threads}x threaded decode: {threaded:6.2f} s")
    verdict = "GIL-bound (~1.0x) -- threads do NOT help" if speedup < 1.5 else "scales with threads"
    print(f"  speedup: {speedup:.2f}x  ({verdict})")

    # --- Process-pool IPC tradeoff: pickle dict vs snapshot vs msgpack blob ---
    print(f"\nPROCESS-POOL IPC TRADEOFF (pickle protocol {HIGHEST} vs msgpack)")
    print(
        f"  (dump/enc runs in the WORKER process -- parallel; load/dec runs in the "
        f"PARENT\n   -- serial/GIL. median of {args.repeat})"
    )
    for label, obj in (("raw dict (naive return)", data), ("SaveSnapshot", snap)):
        dumps, loads, size = [], [], 0
        for _ in range(args.repeat):
            d0 = time.perf_counter()
            blob = pickle.dumps(obj, protocol=HIGHEST)
            dumps.append(time.perf_counter() - d0)
            size = len(blob)
            l0 = time.perf_counter()
            pickle.loads(blob)
            loads.append(time.perf_counter() - l0)
        dump, load = statistics.median(dumps), statistics.median(loads)
        print(
            f"  {label:<28} {_mb(size):>9}   dump {dump:5.2f} s (worker) + "
            f"load {load:5.2f} s (parent) = {dump + load:5.2f} s"
        )

    # ck3_chronicler-lw47: the SHIPPED path -- worker encodes the snapshot to a
    # msgpack blob, parent decodes. enc runs in the worker (parallel); dec runs
    # in the parent (serial/GIL) and is the cost that matters for a drain tail.
    enc = msgspec.msgpack.Encoder()
    dec = msgspec.msgpack.Decoder(type(snap))
    encs, decs, size = [], [], 0
    for _ in range(args.repeat):
        e0 = time.perf_counter()
        blob = enc.encode(snap)
        encs.append(time.perf_counter() - e0)
        size = len(blob)
        d0 = time.perf_counter()
        dec.decode(blob)
        decs.append(time.perf_counter() - d0)
    e, d = statistics.median(encs), statistics.median(decs)
    print(
        f"  {'SaveSnapshot (msgpack, SHIPPED)':<28} {_mb(size):>9}   enc  {e:5.2f} s (worker) + "
        f"dec  {d:5.2f} s (parent) = {e + d:5.2f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

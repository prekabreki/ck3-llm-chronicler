import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import msgspec
import pytest

from chronicler.save import ingest
from chronicler.save.parse import parse_save
from chronicler.save.parse_worker import (
    ParseTimings,
    decode_snapshot,
    parse_save_in_worker,
)
from chronicler.save.rakaly import convert_save_to_json
from tests.helpers.ingest import _snap

FIXTURE = Path("tests/fixtures/saves/autosave_exit.ck3")


@pytest.mark.skipif(not FIXTURE.is_file(), reason="save fixture missing")
def test_worker_returns_snapshot_with_tracked_extractions():
    # Pick a tracked id that the save actually contains as a living/dead char.
    base = parse_save(convert_save_to_json(FIXTURE))
    some_id = next(iter(base.characters))

    blob, timings = parse_save_in_worker(FIXTURE, frozenset({some_id}))

    # ck3_chronicler-lw47: the worker returns a msgpack blob the parent decodes.
    assert isinstance(blob, bytes)
    snap = decode_snapshot(blob)

    # Structured snapshot is intact through the encode/decode round-trip.
    assert snap.playthrough_id == base.playthrough_id
    assert some_id in snap.characters
    # Tracked extraction resolved in-worker for the requested id.
    assert some_id in snap.tracked_raw_records
    assert snap.tracked_raw_records[some_id]  # non-empty record
    # ck3_chronicler-jgsg: per-stage timings ride back for the parent to log.
    assert isinstance(timings, ParseTimings)
    assert timings.rakaly_s > 0
    assert timings.total_s == pytest.approx(timings.rakaly_s + timings.parse_s + timings.extract_s)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="save fixture missing")
def test_worker_only_extracts_requested_ids():
    base = parse_save(convert_save_to_json(FIXTURE))
    ids = list(base.characters)
    wanted = frozenset({ids[0]})
    blob, _timings = parse_save_in_worker(FIXTURE, wanted)
    snap = decode_snapshot(blob)
    assert set(snap.tracked_raw_records).issubset(wanted)
    assert set(snap.tracked_coa).issubset(wanted)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="save fixture missing")
def test_worker_runs_through_process_pool():
    async def run():
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as pool:
            return await loop.run_in_executor(pool, parse_save_in_worker, FIXTURE, frozenset())

    blob, timings = asyncio.run(run())
    # round-tripped a msgpack blob across the pool boundary, then decoded it
    snap = decode_snapshot(blob)
    assert snap.playthrough_id
    assert timings.total_s > 0


def test_dispatch_parse_logs_per_save_timings(monkeypatch, caplog):
    """ck3_chronicler-jgsg: the parent logs the per-stage timing line the
    worker returns, since worker-process logs aren't captured."""
    snap = _snap({})  # empty-characters snapshot is fine for the log-format check
    timings = ParseTimings(rakaly_s=5.0, parse_s=1.5, extract_s=0.25)
    blob = msgspec.msgpack.encode(snap)

    def _fake_worker(path, tracked_ids):
        return blob, timings

    # ThreadPoolExecutor runs in-process so the monkeypatch on the module-level
    # worker symbol actually applies (a real ProcessPoolExecutor would not).
    monkeypatch.setattr(ingest, "parse_save_in_worker", _fake_worker)

    async def run():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return await ingest._dispatch_parse(pool, Path("autosave_test.ck3"), frozenset())

    with caplog.at_level(logging.INFO, logger=ingest.log.name):
        result = asyncio.run(run())

    # _dispatch_parse decodes the blob back to an equal snapshot (lw47).
    assert result == snap
    assert (
        "parsed autosave_test.ck3 in 6.75s (rakaly 5.00s + parse 1.50s + extract 0.25s)"
        in caplog.text
    )

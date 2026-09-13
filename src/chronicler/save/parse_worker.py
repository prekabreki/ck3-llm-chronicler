"""ck3_chronicler-j86v: the process-pool parse worker.

Runs entirely inside a worker process. Does the GIL-bound melt + decode +
parse, then resolves the per-tracked-character raw extractions (the only
two consumers of the raw save dict on the hot path) so the parent never
needs the 127 MB dict back over the pool boundary. Returns ONLY the
SaveSnapshot — see scripts/bench_parse.py for why returning the raw dict
regresses (parent-side pickle load of the dict + a re-parse costs ~4.7 s/save
vs ~1.9 s for the snapshot).

Every name here must stay top-level importable + picklable: Windows uses the
spawn start method, so the pool re-imports this module per worker.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from time import monotonic
from typing import NamedTuple

import msgspec

from chronicler.heraldry import resolve_character_coa
from chronicler.save.parse import parse_save
from chronicler.save.rakaly import convert_save_to_json
from chronicler.save.raw_record import extract_character_record
from chronicler.save.snapshot import SaveSnapshot

# ck3_chronicler-lw47: the worker→parent IPC codec. j86v returned the
# SaveSnapshot object directly, so ProcessPoolExecutor pickled it — and the
# parent-side UNPICKLE (~2.5 s/save, serial, GIL-bound) became the new tail of
# a backlog drain. msgspec.msgpack encodes the snapshot in the worker
# (~0.3 s, parallel) to a bytes blob; the executor only has to (un)pickle
# `bytes` (a near-free memcpy), and the parent decodes the blob (~1.0 s) —
# cutting the serial parent-side cost ~2.6x. msgspec encodes/decodes the
# existing frozen dataclasses directly (incl. tuple-key dicts and frozensets),
# so no Struct conversion of the data model is needed. See scripts/bench_parse.py.
_SNAPSHOT_ENCODER = msgspec.msgpack.Encoder()
_SNAPSHOT_DECODER = msgspec.msgpack.Decoder(SaveSnapshot)


def decode_snapshot(blob: bytes) -> SaveSnapshot:
    """Decode a worker's msgpack blob back into a SaveSnapshot (parent side)."""
    return _SNAPSHOT_DECODER.decode(blob)


class ParseTimings(NamedTuple):
    """ck3_chronicler-jgsg: per-stage wall-clock for one worker parse.

    Measured on the worker's own monotonic clock and returned to the parent,
    which logs it after the future resolves — worker-process logs aren't
    captured by the parent, so without this the live save-tail loses the
    ``parsed <save> in X.XXs`` visibility the in-process path used to emit.
    Picklable (plain floats) so it rides back over the pool boundary cheaply.
    """

    rakaly_s: float
    parse_s: float
    extract_s: float

    @property
    def total_s(self) -> float:
        return self.rakaly_s + self.parse_s + self.extract_s


def parse_save_in_worker(
    save_path: Path, tracked_ids: frozenset[int]
) -> tuple[bytes, ParseTimings]:
    """Melt + decode + parse a save, attaching tracked-character extractions.

    Returns the SaveSnapshot encoded as a msgpack blob (ck3_chronicler-lw47 —
    the parent decodes it via :func:`decode_snapshot`, far cheaper than the
    pickle round-trip ProcessPoolExecutor would do on the object itself) plus
    per-stage :class:`ParseTimings` for the parent to log (ck3_chronicler-jgsg).
    The blob carries tracked_raw_records / tracked_coa for the subset of
    ``tracked_ids`` present in the save. Raises the same RakalyError / OSError
    as convert_save_to_json on melt failure; the caller catches and logs.
    """
    t0 = monotonic()
    data = convert_save_to_json(save_path)
    t_rakaly = monotonic()
    snap = parse_save(data)
    t_parse = monotonic()

    # Build the id -> name map once (ck3_chronicler-60m): extract_character_record
    # expands family-member ids to {id, name} using it.
    name_lookup = {cid: char.first_name for cid, char in snap.characters.items() if char.first_name}
    traits = snap.traits_lookup or None

    raw_records: dict[int, dict] = {}
    coa: dict[int, dict] = {}
    for cid in tracked_ids:
        record = extract_character_record(data, cid, name_lookup=name_lookup, traits_lookup=traits)
        if record is not None:
            raw_records[cid] = record
        resolved = resolve_character_coa(data, cid)
        if resolved is not None:
            coa[cid] = resolved

    snap = dataclasses.replace(snap, tracked_raw_records=raw_records, tracked_coa=coa)
    timings = ParseTimings(
        rakaly_s=t_rakaly - t0,
        parse_s=t_parse - t_rakaly,
        extract_s=monotonic() - t_parse,
    )
    return _SNAPSHOT_ENCODER.encode(snap), timings

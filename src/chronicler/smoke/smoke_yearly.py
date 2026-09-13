"""Yearly-diff smoke: take two yearly autosaves and report the events.

Bypasses the watcher path entirely. Loads a persisted baseline snapshot,
parses a target save, calls :func:`process_save_pair`, and prints what
the diff layer emitted plus a per-event-type tally and a sample of the
last events written.

ck3_chronicler-8jz: lifted out of ``scripts/smoke_yearly_diff.py`` so
the same code path can drive the ``chronicler smoke-yearly`` CLI alias
that the patch-playbook runbook recommends. The legacy script is now a
thin entrypoint that delegates here.

The function takes ``Path`` arguments (rather than ``argparse.Namespace``)
so typer can pass typed values straight through without the extra
unwrapping shim. Returns 0 on success, 1 on failure (mirrors a CLI exit
code so the caller can ``sys.exit(rc)``).
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from chronicler.db import Event, make_engine_for_path, make_session_factory
from chronicler.db.engine import session_scope
from chronicler.save.baseline import load_baseline, save_baseline
from chronicler.save.ingest import process_save_pair
from chronicler.save.parse import parse_save
from chronicler.save.rakaly import convert_save_to_json


def run_smoke_yearly(
    *,
    baseline: Path,
    save: Path,
    db: Path,
    campaign_id: str,
) -> int:
    """Run the yearly-diff smoke against ``baseline`` + ``save`` against
    the per-campaign DB at ``db``. Returns a CLI-style exit code.

    The campaign_id is currently informational (printed in headers) — the
    diff is keyed off the baseline+save pair, not the registry. Kept on
    the signature so future refinements (e.g. cross-checking against
    registry rows) don't need a parameter break.
    """
    print(f"loading baseline {baseline.name} ...")
    # ck3_chronicler-elll: load_baseline returns a BaselineLoad wrapper;
    # smoke_yearly only consumes the snapshot, so unwrap here.
    loaded = load_baseline(baseline)
    if loaded is None:
        print("ERROR: baseline could not be loaded", file=sys.stderr)
        return 1
    snap_prev = loaded.snapshot
    print(f"  date={snap_prev.current_date}, chars={len(snap_prev.characters)}")

    t0 = time.time()
    print(f"parsing {save.name} (rakaly + parse_save_data, this is slow) ...")
    raw = convert_save_to_json(save)
    snap_curr = parse_save(raw)
    print(
        f"  date={snap_curr.current_date}, chars={len(snap_curr.characters)}, "
        f"parsed in {time.time() - t0:.1f}s"
    )

    engine = make_engine_for_path(db)
    factory = make_session_factory(engine)
    print(f"running process_save_pair against campaign {campaign_id} ...")
    t1 = time.time()
    with session_scope(factory) as session:
        results = process_save_pair(
            snap_prev,
            snap_curr,
            session=session,
            save_path_name=save.name,
        )
    print(f"  ingested in {time.time() - t1:.1f}s")

    inserted = sum(1 for r in results if r.outcome == "ingested")
    duplicate = sum(1 for r in results if r.outcome == "duplicate")
    errors = sum(1 for r in results if r.outcome == "error")
    print(
        f"\ndiff produced {len(results)} candidate events "
        f"({inserted} inserted, {duplicate} dedup-skipped, {errors} errors)"
    )

    inserted_ids = [
        r.event_id for r in results if r.outcome == "ingested" and r.event_id is not None
    ]
    if inserted_ids:
        with session_scope(factory) as session:
            rows = session.execute(select(Event.event_type).where(Event.id.in_(inserted_ids))).all()
        kinds: Counter[str] = Counter(row[0] for row in rows)
        print("by event_type:")
        for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {n:6d}  {kind}")

    with session_scope(factory) as session:
        sample = session.execute(select(Event).order_by(Event.id.desc()).limit(8)).scalars().all()
        print(f"\nlast {len(sample)} events in DB:")
        for ev in sample:
            print(
                f"  #{ev.id}  {ev.event_date or '?':12s}  "
                f"{ev.event_type:30s}  prim={ev.primary_character_id}"
            )

    save_baseline(baseline, snap_curr)
    print(f"\nbaseline rewritten to {baseline.name} (now at {snap_curr.current_date})")
    return 0

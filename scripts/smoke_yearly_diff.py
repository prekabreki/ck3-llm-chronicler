"""One-shot smoke: diff two yearly autosaves and report the events.

Bypasses the watcher path. Loads the persisted baseline snapshot for a
campaign, parses a target save, calls :func:`process_save_pair`, and
prints what the diff layer emitted. Intended for manual smoke against a
real save during the v0.7+ machine-bring-up cycle.

ck3_chronicler-8jz: the implementation lives in
:func:`chronicler.smoke.smoke_yearly.run_smoke_yearly` so the
``chronicler smoke-yearly`` CLI alias and this script share one
codepath. This script stays as a thin entrypoint for backwards-compat
with anything that still imports or shells out to it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chronicler.smoke.smoke_yearly import run_smoke_yearly


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--baseline", required=True, type=Path)
    p.add_argument("--save", required=True, type=Path)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--campaign-id", required=True)
    args = p.parse_args()
    return run_smoke_yearly(
        baseline=args.baseline,
        save=args.save,
        db=args.db,
        campaign_id=args.campaign_id,
    )


if __name__ == "__main__":
    sys.exit(main())

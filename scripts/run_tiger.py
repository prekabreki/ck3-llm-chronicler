"""Run amtep/ck3-tiger against the chronicler mod.

ck3-tiger is the static validator for CK3 mod files (research/ck3-data-
extraction.md §4.3). This wrapper finds the binary on PATH, points it at
``mod/chronicler``, and surfaces a friendly install hint if it's missing.

Usage::

    uv run python scripts/run_tiger.py
    uv run python scripts/run_tiger.py --vanilla /path/to/CK3/game

The vanilla CK3 game directory is typically auto-detected by ck3-tiger
itself from the registry on Windows. Pass ``--vanilla`` if it can't.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_HINT = """\
ck3-tiger not found on PATH.

Install from https://github.com/amtep/ck3-tiger/releases (binary builds for
Windows, macOS, Linux). Drop the executable somewhere on PATH and re-run.

"""


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_tiger() -> str | None:
    on_path = shutil.which("ck3-tiger") or shutil.which("ck3-tiger.exe")
    if on_path:
        return on_path
    # Fall back to a repo-local extracted release: ck3-tiger-*/ck3-tiger.exe
    for candidate in sorted(repo_root().glob("ck3-tiger-*")):
        if not candidate.is_dir():
            continue
        for exe in ("ck3-tiger.exe", "ck3-tiger"):
            local = candidate / exe
            if local.is_file():
                return str(local)
    return None


def run(
    *,
    tiger: str,
    mod_dir: Path,
    vanilla: Path | None,
) -> int:
    cmd: list[str] = [tiger]
    if vanilla is not None:
        cmd += ["--game-dir", str(vanilla)]
    cmd.append(str(mod_dir))
    print(f"running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mod",
        type=Path,
        default=repo_root() / "mod" / "chronicler",
        help="Mod directory to validate.",
    )
    p.add_argument(
        "--vanilla",
        type=Path,
        default=None,
        help="CK3 install directory (auto-detected by ck3-tiger if omitted).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tiger = find_tiger()
    if tiger is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 127
    if not args.mod.is_dir():
        print(f"error: mod dir not found: {args.mod}", file=sys.stderr)
        return 2
    return run(tiger=tiger, mod_dir=args.mod, vanilla=args.vanilla)


if __name__ == "__main__":
    sys.exit(main())

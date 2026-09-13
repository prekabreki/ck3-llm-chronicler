"""Run the rakaly CLI for save parsing.

rakaly (https://github.com/rakaly/cli) converts CK3 binary/plaintext saves
to JSON, used by the v0.6 architectural pivot. This wrapper finds the
binary on PATH first, then falls back to a repo-local extracted release
(``rakaly-<version>/<platform>/rakaly[.exe]``) — same pattern as
``scripts/run_tiger.py``.

Usage::

    uv run python scripts/run_rakaly.py json /path/to/save.ck3
    uv run python scripts/run_rakaly.py melt /path/to/save.ck3
    uv run python scripts/run_rakaly.py --version
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_HINT = """\
rakaly not found on PATH.

Download from https://github.com/rakaly/cli/releases/latest (binary builds
for Windows, macOS, Linux). Extract into the repo root so the layout looks
like:

    rakaly-<version>/
        <platform-triple>/
            rakaly[.exe]

…and re-run.
"""


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_rakaly() -> str | None:
    """Locate the rakaly binary, preferring PATH then repo-local release."""
    on_path = shutil.which("rakaly") or shutil.which("rakaly.exe")
    if on_path:
        return on_path

    # Repo-local extracted release: rakaly-*/<platform>/rakaly[.exe]
    for release_dir in sorted(repo_root().glob("rakaly-*")):
        if not release_dir.is_dir():
            continue
        for exe_name in ("rakaly.exe", "rakaly"):
            # Some releases extract to <release>/rakaly (flat) and others to
            # <release>/<platform-triple>/rakaly (nested). Search both.
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rakaly = find_rakaly()
    if rakaly is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 127
    return subprocess.run([rakaly, *args]).returncode


if __name__ == "__main__":
    sys.exit(main())

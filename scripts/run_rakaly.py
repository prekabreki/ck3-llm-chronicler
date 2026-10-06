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

import subprocess
import sys

from chronicler.save.rakaly import find_rakaly

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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rakaly = find_rakaly()
    if rakaly is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 127
    return subprocess.run([rakaly, *args]).returncode


if __name__ == "__main__":
    sys.exit(main())

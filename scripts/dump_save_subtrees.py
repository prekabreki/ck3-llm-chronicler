"""Dump the structure of three uncharted CK3 save subtrees.

Covers ck3_chronicler-c3eg (barter_missions), ck3_chronicler-49lf (legends),
and ck3_chronicler-m658 (inspirations_manager). Each is a new event-type
candidate whose field layout we haven't audited yet — running this against
a live save lets the design phase target the right paths instead of
guessing at the rakaly output.

The script is deliberately read-only: it runs rakaly to produce JSON,
caches the JSON next to the .ck3 (same pattern as the integration test
fixtures), and prints the top-level keys + one sample entry + count for
each subtree. No DB writes, no chronicler imports, no network. Safe to
run against a live autosave.

Usage:
    uv run python scripts/dump_save_subtrees.py <path-to-.ck3>

Typical paste-back to a design conversation:
    1. Run the script.
    2. Copy everything between the BEGIN/END markers in the output.
    3. Paste into the chat.

The design conversation will use that paste to design the parser /
SaveSnapshot fields / DiffEvent shape for each subtree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SUBTREE_NAMES = ("barter_missions", "inspirations_manager", "legends")


def _run_rakaly_json(save_path: Path, cache_path: Path) -> dict:
    """Produce rakaly JSON for ``save_path``, caching to ``cache_path``.

    Reuses an existing cache file when present — keeps re-runs fast when
    iterating on the script. Pass ``--no-cache`` (which the script honours
    via argv) to force re-extraction.
    """
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    runner = Path(__file__).parent / "run_rakaly.py"
    result = subprocess.run(
        [sys.executable, str(runner), "json", str(save_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"rakaly failed with exit code {result.returncode}")
    cache_path.write_text(result.stdout, encoding="utf-8")
    return json.loads(result.stdout)


def _summarise_value(value, depth: int = 0, max_depth: int = 3) -> str:
    """Render a value with type/size annotations, recursing up to ``max_depth``.

    Designed for paste-back legibility: dicts show their keys, lists show
    their length + first-element type, scalars show their value verbatim.
    """
    indent = "  " * depth
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            if depth >= max_depth:
                lines.append(f"{indent}  {k!r}: <{_short_type(v)}>")
            else:
                rendered = _summarise_value(v, depth + 1, max_depth)
                if "\n" in rendered:
                    lines.append(f"{indent}  {k!r}: {rendered}")
                else:
                    lines.append(f"{indent}  {k!r}: {rendered}")
        return "{\n" + "\n".join(lines) + f"\n{indent}}}"
    if isinstance(value, list):
        n = len(value)
        if n == 0:
            return "[]"
        sample_type = _short_type(value[0])
        if depth >= max_depth:
            return f"[list × {n}, item-type={sample_type}]"
        sample = _summarise_value(value[0], depth + 1, max_depth)
        return f"[list × {n}, sample[0]: {sample}]"
    if isinstance(value, str):
        if len(value) > 80:
            return f"{value[:77]!r}…"
        return repr(value)
    return repr(value)


def _short_type(value) -> str:
    if isinstance(value, dict):
        keys = list(value.keys())[:3]
        more = "…" if len(value) > 3 else ""
        return f"dict[{', '.join(repr(k) for k in keys)}{more}]"
    if isinstance(value, list):
        if value:
            return f"list[{_short_type(value[0])} × {len(value)}]"
        return "list[empty]"
    return type(value).__name__


def _print_subtree(name: str, root: dict) -> None:
    print(f"\n--- BEGIN {name} ---")
    if name not in root:
        print(f"(absent from save — key {name!r} not present at top level)")
        print(f"--- END {name} ---")
        return
    value = root[name]
    if isinstance(value, dict):
        keys = list(value.keys())
        print(f"top-level: dict with {len(keys)} key(s)")
        print(f"keys: {keys[:30]}{' …' if len(keys) > 30 else ''}")
        # Find the first entry that looks like an actual record (not a
        # scalar metadata field). For barter_missions, the structure is
        # often {"database": [...]} or {"<id>": {...}}; render the first
        # one that's a dict or list.
        sample_key = None
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                sample_key = k
                break
        if sample_key is not None:
            print(f"sample entry [{sample_key!r}]:")
            print(_summarise_value(value[sample_key], depth=1))
        else:
            # All scalars — render the whole dict.
            print("(all scalar values; full dict:)")
            print(_summarise_value(value, depth=1))
    elif isinstance(value, list):
        print(f"top-level: list × {len(value)}")
        if value:
            print(f"sample[0]: {_summarise_value(value[0], depth=1)}")
    else:
        print(f"top-level: scalar {_short_type(value)} = {value!r}")
    print(f"--- END {name} ---")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args or not args:
        print(__doc__)
        return 0
    save_path = Path(args[0]).resolve()
    if not save_path.is_file():
        sys.stderr.write(f"save file not found: {save_path}\n")
        return 1
    cache_path = save_path.with_suffix(save_path.suffix + ".rakaly.json")
    print(f"# Save subtree dump for {save_path.name}")
    print(f"# rakaly JSON cache: {cache_path}")
    print("# Covers beads: c3eg (barter_missions), m658 (inspirations_manager), 49lf (legends)")
    raw = _run_rakaly_json(save_path, cache_path)
    for name in SUBTREE_NAMES:
        _print_subtree(name, raw)
    print("\n# Done. Paste the BEGIN/END blocks above into the design conversation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

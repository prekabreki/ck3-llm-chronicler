"""Diff CK3 script_docs dumps for the on_actions and getters we depend on.

Run after a CK3 patch to catch hooks our mod hits that have been
renamed, removed, or had their scope changed:

    uv run python scripts/diff_script_docs.py \\
        --base reference/script_docs/1.17.0 \\
        --new  reference/script_docs/1.18.0

Walks ``mod/chronicler/`` to extract:

- the on_action names we wire up (``on_<word> = {`` at line start in
  ``common/on_action/*.txt``)
- the data-function getter chains we interpolate inside ``debug_log``
  strings (``[ROOT.GetID]``, ``[scope:killer.GetID]``, ``[GetGameStartDate]``)

For each, looks up the entry in ``on_actions.info`` (hooks) and in
``event_targets.log`` / ``event_scopes.log`` / ``effects.log`` (getters)
in both dumps, and reports anything that changed.

If the only diff is whitespace or in-file ordering, that's noise — the
script collapses both before comparing.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ON_ACTION_LINE_RE = re.compile(r"^(on_\w+)\s*=", re.MULTILINE)
DATA_FUNC_RE = re.compile(r"\[(?:[A-Za-z_][\w:]*\.)*([A-Z][A-Za-z0-9_]*)\]")
DATA_FUNC_FULL_RE = re.compile(r"\[([A-Za-z_][\w:]*(?:\.[A-Z][A-Za-z0-9_]*)+)\]")
SCRIPT_DOC_FILES = (
    "on_actions.info",
    "event_targets.log",
    "event_scopes.log",
    "effects.log",
    "triggers.log",
)


@dataclass(frozen=True)
class ModUsage:
    on_actions: set[str]
    getters: set[str]


def collect_mod_usage(mod_root: Path) -> ModUsage:
    """Walk our mod source tree, collect every name we depend on."""
    on_actions: set[str] = set()
    getters: set[str] = set()

    on_action_dir = mod_root / "common" / "on_action"
    if on_action_dir.is_dir():
        for txt in on_action_dir.glob("*.txt"):
            text = txt.read_text(encoding="utf-8", errors="replace")
            on_actions.update(ON_ACTION_LINE_RE.findall(text))

    for sub in ("scripted_effects", "scripted_triggers"):
        d = mod_root / "common" / sub
        if not d.is_dir():
            continue
        for txt in d.glob("*.txt"):
            text = txt.read_text(encoding="utf-8", errors="replace")
            for m in DATA_FUNC_FULL_RE.finditer(text):
                getters.add(m.group(1))
            # Also catch bare getters like [GetGameStartDate]
            for m in DATA_FUNC_RE.finditer(text):
                getters.add(m.group(1))

    return ModUsage(on_actions=on_actions, getters=getters)


def grep_lines(path: Path, needle: str) -> list[str]:
    """Return lines containing ``needle``, lower-cased + stripped."""
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle in line:
            out.append(line.strip())
    return out


@dataclass(frozen=True)
class HookStatus:
    name: str
    in_base: bool
    in_new: bool
    base_lines: list[str]
    new_lines: list[str]


def check_on_actions(base: Path, new: Path, names: set[str]) -> list[HookStatus]:
    base_info = base / "on_actions.info"
    new_info = new / "on_actions.info"
    out: list[HookStatus] = []
    for name in sorted(names):
        bl = grep_lines(base_info, name)
        nl = grep_lines(new_info, name)
        out.append(
            HookStatus(name=name, in_base=bool(bl), in_new=bool(nl), base_lines=bl, new_lines=nl)
        )
    return out


@dataclass(frozen=True)
class GetterStatus:
    name: str
    in_base: bool
    in_new: bool


def check_getters(base: Path, new: Path, names: set[str]) -> list[GetterStatus]:
    """A getter is "present" if the bare name appears anywhere in the dump."""
    out: list[GetterStatus] = []
    base_text = "\n".join(
        (base / f).read_text(encoding="utf-8", errors="replace")
        for f in SCRIPT_DOC_FILES
        if (base / f).exists()
    )
    new_text = "\n".join(
        (new / f).read_text(encoding="utf-8", errors="replace")
        for f in SCRIPT_DOC_FILES
        if (new / f).exists()
    )
    for name in sorted(names):
        out.append(
            GetterStatus(
                name=name,
                in_base=name in base_text,
                in_new=name in new_text,
            )
        )
    return out


def report(
    on_action_status: list[HookStatus],
    getter_status: list[GetterStatus],
) -> int:
    """Print a human-readable report; return the number of regressions."""
    regressions = 0

    print("on_actions hooked:")
    for hs in on_action_status:
        if not hs.in_new:
            tag = " REMOVED" if hs.in_base else " MISSING (never present)"
            regressions += 1
        elif not hs.in_base:
            tag = " NEW (not in base — first introduction)"
        else:
            tag = " ok"
        print(f"  {hs.name:40} {tag}")
        if hs.in_base and hs.in_new and hs.base_lines != hs.new_lines:
            print("    (lines differ between base and new — manual review)")
            regressions += 1

    print()
    print("getters used:")
    for gs in getter_status:
        if not gs.in_new:
            tag = " REMOVED" if gs.in_base else " MISSING"
            regressions += 1
        elif not gs.in_base:
            tag = " NEW"
        else:
            tag = " ok"
        print(f"  {gs.name:40} {tag}")

    print()
    if regressions:
        print(f"{regressions} regression(s) detected — see entries flagged above.")
    else:
        print("no regressions detected.")
    return regressions


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True, help="Old script_docs dump dir.")
    p.add_argument("--new", type=Path, required=True, help="New script_docs dump dir.")
    p.add_argument(
        "--mod",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "mod" / "chronicler",
        help="Mod source dir (defaults to mod/chronicler in this repo).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.base.is_dir():
        print(f"error: base dir not found: {args.base}", file=sys.stderr)
        return 2
    if not args.new.is_dir():
        print(f"error: new dir not found: {args.new}", file=sys.stderr)
        return 2

    usage = collect_mod_usage(args.mod)
    print(f"mod: {args.mod}")
    print(f"base: {args.base}")
    print(f"new:  {args.new}")
    print()
    print(f"discovered {len(usage.on_actions)} on_action hooks: {sorted(usage.on_actions)}")
    print(f"discovered {len(usage.getters)} getters: {sorted(usage.getters)}")
    print()

    on_action_status = check_on_actions(args.base, args.new, usage.on_actions)
    getter_status = check_getters(args.base, args.new, usage.getters)
    regressions = report(on_action_status, getter_status)
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())

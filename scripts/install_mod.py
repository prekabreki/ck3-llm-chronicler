"""Install the chronicler mod into the user's CK3 mod folder.

CK3 reads mods from ``%USERPROFILE%/Documents/Paradox Interactive/Crusader Kings III/mod/``.
We symlink ``mod/chronicler`` from this repo into that folder so edits to the
in-repo mod source are picked up live by the game (especially the
hot-reloadable scripted_effects).

Two artefacts are created:

1. ``<user mod folder>/chronicler/`` — a directory symlink (or junction
   on Windows when symlinks aren't permitted) pointing at this repo's
   ``mod/chronicler``.
2. ``<user mod folder>/chronicler.mod`` — the outer descriptor file the
   launcher reads to discover the mod. Written fresh on each install so
   the absolute path inside it stays current with the repo location.

Idempotent: re-running fixes a stale symlink target or descriptor, but
will not clobber a non-symlink directory at the install path without
``--force``.

Usage::

    python scripts/install_mod.py [--mod-folder PATH] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_USER_MOD_FOLDER = (
    Path.home() / "Documents" / "Paradox Interactive" / "Crusader Kings III" / "mod"
)
MOD_NAME = "chronicler"
SUPPORTED_VERSION = "1.19.*"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def mod_source_dir() -> Path:
    return repo_root() / "mod" / MOD_NAME


def descriptor_text(mod_path: Path) -> str:
    return (
        'version="0.1.0"\n'
        "tags={\n"
        '\t"Utilities"\n'
        '\t"Events"\n'
        "}\n"
        f'name="Chronicler"\n'
        f'supported_version="{SUPPORTED_VERSION}"\n'
        f'path="{mod_path.as_posix()}"\n'
    )


def install(
    *,
    mod_folder: Path,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    src = mod_source_dir()
    if not src.is_dir():
        print(f"error: mod source directory not found: {src}", file=sys.stderr)
        return 2

    if not mod_folder.exists():
        print(f"error: CK3 user mod folder not found: {mod_folder}", file=sys.stderr)
        print("       (have you launched CK3 at least once?)", file=sys.stderr)
        return 2

    install_link = mod_folder / MOD_NAME
    descriptor = mod_folder / f"{MOD_NAME}.mod"

    print(f"repo:        {repo_root()}")
    print(f"mod source:  {src}")
    print(f"mod folder:  {mod_folder}")
    print(f"install at:  {install_link}")
    print(f"descriptor:  {descriptor}")

    # Handle existing install_link. On Windows, junctions are not detected
    # by Path.is_symlink() but are still readable via os.readlink, so we
    # probe with readlink first.
    existing_target = _read_link_or_junction(install_link)
    if existing_target is not None:
        current_target = Path(existing_target).resolve()
        if current_target == src.resolve():
            print("symlink: already correct, leaving in place")
        else:
            print(f"symlink: replacing stale target ({current_target} -> {src})")
            if not dry_run:
                install_link.unlink()
                _create_symlink(src, install_link)
    elif install_link.exists():
        if not force:
            print(
                f"error: {install_link} exists and is not a symlink — pass --force to remove",
                file=sys.stderr,
            )
            return 3
        print(f"symlink: removing existing non-symlink dir at {install_link} (--force)")
        if not dry_run:
            _remove_tree(install_link)
            _create_symlink(src, install_link)
    else:
        print("symlink: creating")
        if not dry_run:
            _create_symlink(src, install_link)

    text = descriptor_text(src)
    if descriptor.exists() and descriptor.read_text(encoding="utf-8") == text:
        print("descriptor: already current")
    else:
        print("descriptor: writing")
        if not dry_run:
            descriptor.write_text(text, encoding="utf-8")

    if dry_run:
        print("(dry run — no changes made)")
    else:
        print("install complete.")
    return 0


def _create_symlink(target: Path, link: Path) -> None:
    """Create a directory symlink. Falls back to a Windows junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError as e:
        if sys.platform != "win32":
            raise
        # Windows: try a directory junction (no privilege required)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"error: could not create symlink or junction at {link}",
                file=sys.stderr,
            )
            print(f"       symlink error: {e}", file=sys.stderr)
            print(f"       mklink error:  {result.stderr.strip()}", file=sys.stderr)
            print(
                "       hint: enable Windows Developer Mode or run elevated.",
                file=sys.stderr,
            )
            raise


def _remove_tree(path: Path) -> None:
    """Best-effort removal of a possibly-large directory tree."""
    import shutil

    if path.is_symlink() or _read_link_or_junction(path) is not None:
        path.unlink()
        return
    shutil.rmtree(path)


def _read_link_or_junction(p: Path) -> str | None:
    """Return the link target if ``p`` is a symlink or Windows junction, else None."""
    try:
        return os.readlink(p)
    except OSError:
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mod-folder",
        type=Path,
        default=DEFAULT_USER_MOD_FOLDER,
        help=f"Override CK3 user mod folder (default: {DEFAULT_USER_MOD_FOLDER})",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would happen, do nothing.")
    p.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing non-symlink directory at the install path.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return install(mod_folder=args.mod_folder, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())

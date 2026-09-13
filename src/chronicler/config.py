"""Path defaults for chronicler.

Override via env vars where listed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def ck3_user_dir() -> Path:
    """Base dir where CK3 stores per-user data (saves, logs), per-OS.

      - Linux:          ~/.local/share/Paradox Interactive/Crusader Kings III
      - Windows/macOS:  ~/Documents/Paradox Interactive/Crusader Kings III

    These match where the *native* game writes. CK3 run through Proton writes
    inside the Proton prefix instead — point ``CK3_DEBUG_LOG`` /
    ``CHRONICLER_SAVE_DIR`` at the prefix in that case.
    """
    if sys.platform.startswith("linux"):
        base = Path.home() / ".local" / "share"
    else:
        base = Path.home() / "Documents"
    return base / "Paradox Interactive" / "Crusader Kings III"


CHRONICLER_DATA_DIR_ENV = "CHRONICLER_DATA_DIR"


def chronicler_data_dir() -> Path:
    """Base dir for the chronicler's own state — registry DB, per-campaign
    DBs, settings.json, logs, heraldry. Single source of truth so every
    consumer agrees on one location.

    Precedence: ``CHRONICLER_DATA_DIR`` env var, else per-OS default:
      - Linux:          $XDG_DATA_HOME/chronicler  (default ~/.local/share/chronicler)
      - Windows/macOS:  ~/Documents/chronicler
    """
    override = os.environ.get(CHRONICLER_DATA_DIR_ENV)
    if override:
        return Path(override)
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        return base / "chronicler"
    return Path.home() / "Documents" / "chronicler"


CK3_DEBUG_LOG_ENV = "CK3_DEBUG_LOG"
CK3_DEFAULT_LOG_PATH = ck3_user_dir() / "logs" / "debug.log"

# ck3_chronicler-f9w.1: env vars for save dir + CK3 install dir.
# Settings file overrides take priority over env vars; env still works
# for ad-hoc CLI runs without a settings file.
CHRONICLER_SAVE_DIR_ENV = "CHRONICLER_SAVE_DIR"
CHRONICLER_CK3_INSTALL_DIR_ENV = "CHRONICLER_CK3_INSTALL_DIR"

# ck3_chronicler-tbrm.1: prose repo path for the narrative backend.
# Settings file overrides take priority over env vars.
CHRONICLER_PROSE_REPO_PATH_ENV = "CHRONICLER_PROSE_REPO_PATH"

# Issue #24: where sealed-campaign snapshots live. Same precedence.
CHRONICLER_ARCHIVE_DIR_ENV = "CHRONICLER_ARCHIVE_DIR"

# ck3_chronicler-me4 (8ek slice 2): A/B switch for the death-biography
# prompt path. "scene_setter" (default) selects biography_v3 — slice 1's
# discrete scene-setter paragraph at the top of the biography. "woven"
# selects biography_v5 — region facts threaded through the biography
# body wherever a recorded event touches them. Flip to "woven" to
# evaluate; flip back to revert. (v4 is occupied by the withdrawn j7a
# experiment; the active prompt set picked v5 as the next free slot.)
BiographyWorldbuildingMode = Literal["scene_setter", "woven"]
BIOGRAPHY_WORLDBUILDING_MODE: BiographyWorldbuildingMode = "woven"


def get_ck3_debug_log() -> Path:
    """CK3's resolved debug.log path.

    Precedence (ck3_chronicler-27ov.80, audit L24 — brought in line with
    the other resolvers, which all consult the settings file first):
      1. ``ck3_debug_log`` from the user settings file.
      2. ``CK3_DEBUG_LOG`` environment variable.
      3. The CK3 default under ``~/Documents``.

    Previously this skipped step 1, so it was the one path the Settings
    layer could never override.
    """
    from chronicler.settings_store import load_settings

    override = load_settings().get("ck3_debug_log")
    if isinstance(override, str) and override:
        return Path(override)
    env = os.environ.get(CK3_DEBUG_LOG_ENV)
    if env:
        return Path(env)
    return CK3_DEFAULT_LOG_PATH


CK3_DEFAULT_SAVE_DIR = ck3_user_dir() / "save games"

PathSource = Literal["override", "env", "sibling", "default", "probe"]


@dataclass(frozen=True)
class ResolvedPath:
    """A resolved path with provenance.

    ``value`` is empty when nothing resolved (e.g. CK3 install dir with
    no override and no Steam library found). ``exists`` reflects whether
    ``value`` is a directory on disk *now* — useful for the Settings UI
    so a stale override flags itself as broken.
    """

    value: Path | None
    source: PathSource
    exists: bool


def _existing_path_result(value: Path, source: PathSource) -> ResolvedPath:
    """ResolvedPath whose ``exists`` is probed from disk now."""
    return ResolvedPath(value=value, source=source, exists=value.is_dir())


def _resolve(
    settings_key: str,
    env_var: str,
    *fallbacks: Callable[[], ResolvedPath | None],
) -> ResolvedPath:
    """Shared settings -> env -> fallbacks path resolution (audit L24).

    The three structural resolvers (save dir, prose repo, CK3 install)
    were near-identical copies of this settings-file-then-env-var
    preamble (ck3_chronicler-27ov.80). Each non-trivial tail step now
    lives in a ``fallbacks`` callable that returns a :class:`ResolvedPath`
    (owning its own ``source`` label) or ``None`` to defer to the next
    fallback; the terminal fallback must always return one.

    Settings is imported lazily so the module doesn't take a hard dep on
    the store for callers that just want a default path.
    """
    from chronicler.settings_store import load_settings

    override = load_settings().get(settings_key)
    if isinstance(override, str) and override:
        return _existing_path_result(Path(override), "override")

    env = os.environ.get(env_var)
    if env:
        return _existing_path_result(Path(env), "env")

    for fallback in fallbacks:
        result = fallback()
        if result is not None:
            return result

    raise RuntimeError(  # pragma: no cover — terminal fallback always returns
        f"no fallback resolved {settings_key!r}"
    )


def get_ck3_save_dir() -> Path:
    """CK3's resolved save-games directory.

    Precedence (ck3_chronicler-f9w.1):
      1. ``save_dir`` from the user settings file.
      2. ``CHRONICLER_SAVE_DIR`` environment variable.
      3. The CK3 default under ``~/Documents``.

    The CLI commands also accept ``--save-dir`` for ad-hoc one-off use;
    that flag wins over the resolution chain at the call site.
    """
    return resolve_save_dir().value or CK3_DEFAULT_SAVE_DIR


def resolve_save_dir() -> ResolvedPath:
    """Same precedence as :func:`get_ck3_save_dir` but returns provenance.

    Used by the Settings API so the UI can label the source (override /
    env / default) and show whether the path actually exists on disk.
    """
    return _resolve(
        "save_dir",
        CHRONICLER_SAVE_DIR_ENV,
        lambda: _existing_path_result(CK3_DEFAULT_SAVE_DIR, "default"),
    )


def default_prose_repo_path() -> Path:
    """Terminal default for the prose directory: the scaffold target.

    Issue #20: this used to be a hard-coded ``C:/git/ck3_chronicler_prose``
    (with a Linux variant) plus a sibling-checkout autodetect, both of
    which encode the maintainer's two-repo layout. A public user has no
    sibling repo and no C: drive, so the default is now the directory
    ``chronicler init-prose`` creates — under the platform data dir,
    beside the campaign databases.

    A function rather than a module constant because the data dir honours
    ``CHRONICLER_DATA_DIR`` at call time; a constant would freeze whatever
    was set at import.
    """
    return chronicler_data_dir() / "prose"


def default_archive_dir() -> Path:
    """Terminal default for sealed-campaign snapshots: beside the
    campaign databases under the platform data dir.

    Issue #24: this used to be ``<repo>/data/archived/`` inside the
    chronicler checkout, with the snapshots deliberately git-committed as
    the cross-machine transport. That cannot survive a public origin — a
    user cannot push to it, and the owner's pushes would publish personal
    campaign data. The snapshots move to the data dir, and syncing them is
    the user's own choice of vehicle (see ``docs/archived-campaigns.md``).

    A function, not a constant, because the data dir honours
    ``CHRONICLER_DATA_DIR`` at call time.
    """
    return chronicler_data_dir() / "archived"


def resolve_archive_dir() -> ResolvedPath:
    """Locate the sealed-campaign snapshot directory.

    Precedence, matching every other structural path here: the
    ``archive_dir`` setting, then ``CHRONICLER_ARCHIVE_DIR``, then
    :func:`default_archive_dir`.
    """
    return _resolve(
        "archive_dir",
        CHRONICLER_ARCHIVE_DIR_ENV,
        lambda: _existing_path_result(default_archive_dir(), "default"),
    )


def resolve_prose_repo_path() -> ResolvedPath:
    """Locate the prose directory holding the chronicler's register.

    Precedence:
      1. ``prose_repo_path`` from the user settings file (what
         ``chronicler init-prose`` writes).
      2. ``CHRONICLER_PROSE_REPO_PATH`` environment variable.
      3. :func:`default_prose_repo_path` — the ``init-prose`` target.

    Issue #20 removed the sibling-checkout autodetect (ck3_chronicler-jnze)
    that sat between env and default. It resolved
    ``../ck3_chronicler_prose`` relative to this checkout, which is the
    maintainer's layout and nobody else's — and worse, it resolved
    *silently*, so a public user with a coincidentally-named neighbour
    directory would generate against it. Settings and env overrides are
    unchanged, so an existing install that set either keeps working.

    The directory holds the register + voice files that the shared
    system-prompt assembly reads (``prose_io.assemble_system_prompt``);
    every transport fails loud when it is missing rather than generating
    register-less prose.
    """
    return _resolve(
        "prose_repo_path",
        CHRONICLER_PROSE_REPO_PATH_ENV,
        lambda: _existing_path_result(default_prose_repo_path(), "default"),
    )


def resolve_ck3_install_dir() -> ResolvedPath:
    """Locate the CK3 install directory with provenance.

    Precedence (ck3_chronicler-f9w.1):
      1. ``ck3_install_dir`` from the user settings file.
      2. ``CHRONICLER_CK3_INSTALL_DIR`` environment variable.
      3. Steam-library probe via :func:`chronicler.heraldry.find_ck3_install`.

    When the probe fails and nothing is configured, returns
    ``ResolvedPath(value=None, source="probe", exists=False)`` so the
    Settings UI can flag "not found, please set".
    """

    def _probe() -> ResolvedPath:
        # Probe Steam libraries. find_ck3_install already validates the
        # candidate looks like a CK3 root, so a non-None return implies
        # exists=True. Terminal fallback: always returns a ResolvedPath
        # (value=None when the probe misses).
        from chronicler.heraldry.extractor import find_ck3_install

        probed = find_ck3_install()
        if probed is not None:
            return ResolvedPath(value=probed, source="probe", exists=True)
        return ResolvedPath(value=None, source="probe", exists=False)

    return _resolve("ck3_install_dir", CHRONICLER_CK3_INSTALL_DIR_ENV, _probe)


def find_repo_root() -> Path:
    """Locate the chronicler repo root (where ``alembic.ini`` lives).

    Walks up from this file. Used by the CLI to invoke ``alembic`` against
    the project's migrations regardless of the user's cwd.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "alembic.ini").exists():
            return parent
    raise RuntimeError("could not locate chronicler repo root (no alembic.ini found)")

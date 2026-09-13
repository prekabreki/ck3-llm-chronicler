"""Resolve CK3 character + dynasty/house names into readable Unicode.

ck3_chronicler-3kyg: this is the single home for "given a raw save
record, what name do we render?". Three flavours of resolution used to
spread across parse.py, adoption.py and character_columns.py:

* **Character first names** — :func:`resolve_character_name` prefers the
  game's own localization (the authoritative diacritic source, see
  below) and falls back to the heuristic decoder on a miss.
* **Dynasty / house names** — :func:`resolve_dynasty_name` walks the
  house → dynasty chain with the te8s house-name fallback, stated once
  here instead of re-typed in every caller.

The two genuinely-deep decoders (:func:`chronicler.save.localization.\
decode_ck3_name`, :func:`~chronicler.save.localization.decode_house_name`)
stay in ``localization.py``; this module composes them behind the
resolution interface callers actually want.

ck3_chronicler-r3fs (character-name localization, the original reason
this module exists):

CK3 stores names as letter+underscore escapes (``A_slaug``, ``T_O_runn``)
in both the save and the ``name_lists`` .txt files. That form is *lossy*:
the same ``O_`` escape is Ö in ``O_rvar`` (Örvar) but ó in ``O_lafr``
(Ólafr), and ``A_`` is Á in ``A_slaug`` (Áslaug) — no per-culture map can
recover the right glyph. The game disambiguates via the localization
YAMLs under ``game/localization/<lang>/names/*.yml``, which map each
escape token to the canonical Unicode string::

    A_slaug:0 "Áslaug"
    O_lafr:0  "Ólafr"
    O_rvar:0  "Örvar"
    OrdoN_o:0 "Ordoño"

rakaly returns that same escape token as ``first_name``, so it keys
directly into this map. :func:`resolve_name` is the lookup the ingest
parser consults *before* falling back to the heuristic
:func:`chronicler.save.localization.decode_ck3_name`.

When the game install can't be located (a machine without CK3, CI), the
map is empty and ``resolve_name`` returns ``None`` — callers then use the
heuristic decoder, so nothing regresses.

Issue #14 (enabled-mod loca, 2026-08-13): base+DLC english loca is no
longer the whole story. Mods ship the same
``localization/english/{names,dynasties}/*.yml`` layout (minus the
``game/`` prefix), and a name they provide has exactly the diacritic
problem ``r3fs`` was filed for — "More Character Names" alone contributes
keys like ``dynn_Bai_767D: "Bái"``. So :func:`find_enabled_mod_dirs`
reads CK3's own ``dlc_load.json`` and the enabled mods' loca is appended
*after* the base files, letting a mod override a base key exactly as it
does in-game (:func:`build_name_map` is last-wins, and ``dlc_load.json``
order is load order, so a later mod also beats an earlier one).

**Non-english locales remain out of scope** — deliberately, not
pending. The whole pipeline downstream reads english (prompt rails, prose,
the 55k-entry base english set), so resolving a Polish name here would
hand the narrative layer a name in a language nothing else speaks.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from chronicler.save.localization import decode_ck3_name, decode_house_name

log = logging.getLogger(__name__)

# Env override pointing at the CK3 install root (the dir containing
# ``game/``). Takes precedence over autodetection.
_ENV_GAME_DIR = "CHRONICLER_CK3_DIR"

# Glob (relative to the install root) for the english character-name loca.
_NAME_LOCA_GLOB = "game/localization/english/names/*.yml"

# ck3_chronicler-6rgx: english dynasty/house-name loca. Two files ship —
# dynasty_names_l_english.yml + chinese_dynasty_names_l_english.yml — so
# glob the directory. The save's dynasty_house[].name (``dynn_*``) keys
# straight into this map, verified against a real install.
_DYNASTY_LOCA_GLOB = "game/localization/english/dynasties/*.yml"

# Issue #14: the same two globs relative to a MOD root. A mod mirrors the
# game's tree without the ``game/`` prefix, so these are the base globs
# minus that segment.
_MOD_NAME_LOCA_GLOB = "localization/english/names/*.yml"
_MOD_DYNASTY_LOCA_GLOB = "localization/english/dynasties/*.yml"

# CK3's own record of which mods are enabled, relative to ck3_user_dir().
_DLC_LOAD_JSON = "dlc_load.json"

# ``path="…"`` out of a .mod descriptor. Anchored per-line so it cannot
# match the ``supported_version`` or ``remote_file_id`` lines beside it.
_MOD_PATH_RE = re.compile(r'^path="([^"]+)"', re.MULTILINE)

# Last-resort Windows-only install locations, tried after
# chronicler.config.resolve_ck3_install_dir (whose Steam-library probe is
# cross-platform and validates the candidate). The user's Windows library
# lives on E:\; the rest are the usual Steam defaults. These never match on
# Linux/macOS — hence the resolver step, see find_game_dir.
_DEFAULT_INSTALL_CANDIDATES: tuple[str, ...] = (
    r"E:\SteamLibrary\steamapps\common\Crusader Kings III",
    r"C:\Program Files (x86)\Steam\steamapps\common\Crusader Kings III",
    r"C:\Program Files\Steam\steamapps\common\Crusader Kings III",
    r"D:\SteamLibrary\steamapps\common\Crusader Kings III",
)

# Process-wide cache of the parsed token -> canonical name map. ``None``
# means "not loaded yet"; an empty dict is a valid loaded-but-no-data state
# (e.g. no game install) and is NOT reloaded. Tests reset this to None.
_NAME_MAP: dict[str, str] | None = None

# ck3_chronicler-6rgx: token -> canonical dynasty/house name. Same lifecycle
# as ``_NAME_MAP`` (``None`` = unloaded; ``{}`` = loaded-but-no-install;
# tests reset to ``{}`` via the conftest autouse fixture).
_DYNASTY_MAP: dict[str, str] | None = None


def find_game_dir() -> Path | None:
    """Locate the CK3 install root, or ``None`` if it can't be found.

    Precedence: ``settings.json`` (``ck3_game_dir``) →
    ``CHRONICLER_CK3_DIR`` env →
    :func:`chronicler.config.resolve_ck3_install_dir` (which adds the
    ``ck3_install_dir`` setting, ``CHRONICLER_CK3_INSTALL_DIR``, and a
    cross-platform Steam-library probe) → the Windows-only
    :data:`_DEFAULT_INSTALL_CANDIDATES` as a last resort.
    """
    try:
        from chronicler.settings_store import load_settings

        override = load_settings().get("ck3_game_dir")
    except Exception:  # noqa: BLE001 — settings are best-effort, never fatal
        override = None
    if isinstance(override, str) and override:
        p = Path(override)
        if p.exists():
            return p

    env = os.environ.get(_ENV_GAME_DIR)
    if env:
        p = Path(env)
        return p if p.exists() else None

    # Defer to the per-OS resolver before the candidate list below, which is
    # Windows-only: on Linux/macOS every candidate missed, so canonical name
    # lookup silently degraded to the heuristic decoder even when the Steam
    # probe could see the install. resolve_ck3_install_dir covers the
    # ck3_install_dir setting, CHRONICLER_CK3_INSTALL_DIR, and a
    # cross-platform Steam-library probe. Imported inside the function to
    # keep the settings/config import lazy, as with load_settings above.
    try:
        from chronicler.config import resolve_ck3_install_dir

        resolved = resolve_ck3_install_dir()
    except Exception:  # noqa: BLE001 — autodetection is best-effort, never fatal
        resolved = None
    if resolved is not None and resolved.value is not None and resolved.exists:
        return resolved.value

    for cand in _DEFAULT_INSTALL_CANDIDATES:
        p = Path(cand)
        if p.exists():
            return p
    return None


def find_enabled_mod_dirs() -> list[Path]:
    """Roots of the mods CK3 currently has enabled, in load order.

    Issue #14. Reads CK3's own ``dlc_load.json`` under
    :func:`chronicler.config.ck3_user_dir` — ``enabled_mods`` is a list of
    descriptor paths relative to that dir (``"mod/ugc_3451288368.mod"``),
    and each descriptor carries the actual content root::

        name="More Character Names"
        path="/home/…/steamapps/workshop/content/1158310/3451288368"

    Order is preserved: ``dlc_load.json`` lists mods in load order, so a
    later entry's loca must win over an earlier one, which falls out of
    :func:`build_name_map` being last-wins.

    Best-effort throughout, like :func:`find_game_dir` — a missing file,
    unreadable descriptor, unsubscribed mod (``path`` pointing nowhere) or
    malformed JSON yields fewer dirs, never an exception. Name resolution
    is a nice-to-have with a working heuristic fallback; it must never be
    able to break ingest.

    Zipped mods (descriptor carries ``archive=`` instead of ``path=``) are
    skipped — reading loca out of an archive is not worth it for a form
    Steam workshop mods do not use.
    """
    try:
        from chronicler.config import ck3_user_dir

        user_dir = ck3_user_dir()
        raw = (user_dir / _DLC_LOAD_JSON).read_text(encoding="utf-8-sig")
        enabled = json.loads(raw).get("enabled_mods")
    except (OSError, ValueError, ImportError) as exc:
        log.debug("ck3_names: no enabled-mod list (%s)", exc)
        return []
    if not isinstance(enabled, list):
        return []

    dirs: list[Path] = []
    for entry in enabled:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            descriptor = (user_dir / entry).read_text(encoding="utf-8-sig")
        except OSError as exc:
            log.debug("ck3_names: skipping unreadable mod descriptor %s (%s)", entry, exc)
            continue
        match = _MOD_PATH_RE.search(descriptor)
        if match is None:
            continue
        root = Path(match.group(1))
        if root.is_dir():
            dirs.append(root)
        else:
            log.debug("ck3_names: enabled mod %s has no content dir at %s", entry, root)
    return dirs


def _mod_loca_paths(glob: str) -> list[Path]:
    """Loca files matching ``glob`` across every enabled mod, in load order."""
    return [p for root in find_enabled_mod_dirs() for p in sorted(root.glob(glob))]


def build_name_map(yml_paths: Iterable[Path]) -> dict[str, str]:
    """Parse CK3 name-loca files into a ``token -> canonical name`` dict.

    Each data line is ``  key:<version> "Value"`` (the ``l_<language>:``
    header has no quoted value and is skipped; ``#`` comments and blank
    lines don't match). Files are UTF-8 with a BOM (``utf-8-sig``). On key
    collision across files, the last file wins. Unreadable paths are
    skipped rather than raising — a missing DLC file must not break ingest.
    """
    out: dict[str, str] = {}
    for path in yml_paths:
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            log.debug("ck3_names: skipping unreadable loca file %s (%s)", path, exc)
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, sep, rest = stripped.partition(":")
            if not sep:
                continue
            # rest is ``<version> "Value"`` — take the quoted span.
            q1 = rest.find('"')
            if q1 == -1:
                continue  # header line (e.g. ``l_english:``) has no value
            q2 = rest.rfind('"')
            if q2 <= q1:
                continue
            out[key.strip()] = rest[q1 + 1 : q2]
    return out


def get_name_map() -> dict[str, str]:
    """Return the cached name map, loading it once from the game install.

    Returns an empty dict (cached) when no install is reachable.
    """
    global _NAME_MAP
    if _NAME_MAP is None:
        game_dir = find_game_dir()
        if game_dir is None:
            log.info(
                "ck3_names: no CK3 install found (set %s to enable canonical "
                "name lookup); falling back to heuristic name decoding",
                _ENV_GAME_DIR,
            )
            _NAME_MAP = {}
        else:
            # Issue #14: mod loca last so a mod key overrides the base one.
            mod_paths = _mod_loca_paths(_MOD_NAME_LOCA_GLOB)
            _NAME_MAP = build_name_map([*sorted(game_dir.glob(_NAME_LOCA_GLOB)), *mod_paths])
            log.info(
                "ck3_names: loaded %d canonical name(s) from %s (+%d mod loca file(s))",
                len(_NAME_MAP),
                game_dir,
                len(mod_paths),
            )
    return _NAME_MAP


def resolve_name(token: str | None) -> str | None:
    """Canonical Unicode name for a CK3 escape token, or ``None`` on miss.

    ``None`` / empty tokens and any token absent from the loca map return
    ``None`` so the caller falls back to ``decode_ck3_name``.
    """
    if not token:
        return None
    return get_name_map().get(token)


def get_dynasty_map() -> dict[str, str]:
    """Return the cached dynasty/house-name map, loading it once from the
    game install (``dynn_* -> canonical name``).

    Returns an empty dict (cached) when no install is reachable — callers
    then fall back to the
    :func:`~chronicler.save.localization.decode_house_name` heuristic, so
    nothing regresses. ck3_chronicler-6rgx.
    """
    global _DYNASTY_MAP
    if _DYNASTY_MAP is None:
        game_dir = find_game_dir()
        if game_dir is None:
            _DYNASTY_MAP = {}
        else:
            # Issue #14: same mod-after-base precedence as the name map.
            mod_paths = _mod_loca_paths(_MOD_DYNASTY_LOCA_GLOB)
            _DYNASTY_MAP = build_name_map([*sorted(game_dir.glob(_DYNASTY_LOCA_GLOB)), *mod_paths])
            log.info(
                "ck3_names: loaded %d dynasty name(s) from %s (+%d mod loca file(s))",
                len(_DYNASTY_MAP),
                game_dir,
                len(mod_paths),
            )
    return _DYNASTY_MAP


def resolve_house_name(raw: str | None, culture: str | None = None) -> str | None:
    """Render a CK3 dynasty/house key (``dynn_*``) into a display name.

    ck3_chronicler-6rgx: prefer CK3's own dynasty-name localization — the
    authoritative diacritic source, exactly as :func:`resolve_character_name`
    does for first names. ``dynn_A_rpA_d`` resolves to "Árpád"; the heuristic
    can only strip the prefix and lossily decode the escape to "Árpad" (the
    same ó-vs-ö ambiguity no per-culture map can recover). Falls back to the
    :func:`~chronicler.save.localization.decode_house_name` heuristic on a
    miss — custom/player-named dynasties (whose value isn't a ``dynn_*`` loca
    key), mod dynasties, or no game install. ``None``/``""`` pass through via
    the heuristic's own passthrough.
    """
    if raw:
        hit = get_dynasty_map().get(raw)
        if hit is not None:
            return hit
    return decode_house_name(raw, culture=culture)


def resolve_character_name(token: str | None, culture: str | None = None) -> str | None:
    """Render a CK3 ``first_name`` escape token into a display name.

    Prefers CK3's own name localization (:func:`resolve_name`), which
    disambiguates the lossy letter+underscore escape (``O_lafr`` → Ólafr
    vs ``O_rvar`` → Örvar — same escape, different glyph, unrecoverable
    by any per-culture map). Falls back to the heuristic
    :func:`~chronicler.save.localization.decode_ck3_name` on a miss (mod
    names, no game install reachable).

    This is the single place that decides how a raw token becomes a
    name; the parser calls it instead of inlining the resolve-or-decode
    sequence (ck3_chronicler-3kyg). ``None`` passes through to ``None``;
    the parser strips empty tokens to ``None`` before calling.
    """
    return resolve_name(token) or decode_ck3_name(token, culture=culture)


def resolve_dynasty_name(
    *,
    dynasty_house_id: int | None,
    houses_lookup: Mapping[int, str],
    house_to_dynasty: Mapping[int, int],
    dynasties_lookup: Mapping[int, str],
    culture: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve ``(dynasty_name, house_name)`` from a character's house id.

    Walks house → dynasty: ``house_name`` from ``houses_lookup`` and
    ``dynasty_name`` from ``house_to_dynasty`` → ``dynasties_lookup``,
    each resolved by :func:`resolve_house_name` — CK3's dynasty-name loca
    when the ``dynn_*`` key is known (authoritative diacritics, 6rgx),
    falling back to the
    :func:`~chronicler.save.localization.decode_house_name` heuristic
    (strips ``dynn_``/``house_`` prefixes, lossily decodes escapes;
    idempotent on already-resolved values).

    ck3_chronicler-te8s / -45i / -0r44 fallback: when the dynasty chain
    can't be walked — engine-defined dynasties whose only identifier is
    an integer locale key are skipped by ``dynasties_lookup`` — the
    house name stands in as the dynasty name, matching CK3's own
    rendering. Before ck3_chronicler-3kyg this fallback was re-typed in
    both ``character_columns.py`` and ``adoption.py``; it now lives here.

    A ``None`` ``dynasty_house_id`` (landless / early-playthrough
    characters) yields ``(None, None)``.
    """
    if dynasty_house_id is None:
        return None, None
    house_name = resolve_house_name(houses_lookup.get(dynasty_house_id), culture=culture)
    dynasty_id = house_to_dynasty.get(dynasty_house_id)
    raw_dynasty_name = dynasties_lookup.get(dynasty_id) if dynasty_id is not None else None
    dynasty_name = resolve_house_name(raw_dynasty_name, culture=culture)
    if not dynasty_name and house_name:
        dynasty_name = house_name
    return dynasty_name, house_name

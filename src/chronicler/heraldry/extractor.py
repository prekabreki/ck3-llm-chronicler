"""CK3 install discovery + coat-of-arms asset extraction.

Locates the user's CK3 install via a sequence of probes (Steam library
folders + explicit override), parses the named-color palette CK3 uses
for coat-of-arms tinctures (``common/named_colors/default_colors.txt``),
and converts the DDS pattern + emblem textures under
``gfx/coat_of_arms/`` to PNG via Pillow.

Output layout (under ``<chronicler-data-dir>/heraldry/``):

::

    palette.json                  {color_name: [r, g, b]} (RGB 0-255)
    manifest.json                 {patterns: [...], emblems_colored: [...], extracted_at: iso8601}
    patterns/<name>.png           one PNG per pattern_*.dds
    colored_emblems/<name>.png    one PNG per ce_*.dds

Re-runnable on CK3 patches — re-extracting is the canonical way to pick
up new emblems / colors after a game update.
"""

from __future__ import annotations

import colorsys
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)


# Schema version for the on-disk manifest. Bump if the layout under
# <output>/heraldry/ changes incompatibly so older extractions are
# detected (re-extract required).
MANIFEST_SCHEMA_VERSION = 1


# Common Steam library locations probed when no explicit --ck3-dir is given.
# Order matters — first hit wins.
DEFAULT_STEAM_LIBRARIES: tuple[Path, ...] = (
    Path("C:/Program Files (x86)/Steam/steamapps/common/Crusader Kings III"),
    Path("C:/Program Files/Steam/steamapps/common/Crusader Kings III"),
    Path("D:/SteamLibrary/steamapps/common/Crusader Kings III"),
    Path("E:/SteamLibrary/steamapps/common/Crusader Kings III"),
    Path("F:/SteamLibrary/steamapps/common/Crusader Kings III"),
    # Linux Steam — several layouts depending on install method.
    Path.home() / ".steam/steam/steamapps/common/Crusader Kings III",
    Path.home() / ".steam/root/steamapps/common/Crusader Kings III",
    Path.home() / ".local/share/Steam/steamapps/common/Crusader Kings III",
    # Flatpak
    Path.home()
    / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Crusader Kings III",
)

# Subpath inside the install dir that holds CoA assets we care about.
_COA_SUBPATH = Path("game/gfx/coat_of_arms")
_NAMED_COLORS_FILE = Path("game/common/named_colors/default_colors.txt")

# ck3_chronicler-zx2l: top-level interface icon dir containing the five
# tier crowns (``empire_crown.dds`` etc). Not nested under a per-tier
# folder — the five files sit alongside other miscellany at the root of
# ``gfx/interface/icons``, so this extraction picks them by exact name
# rather than globbing the directory.
_INTERFACE_ICONS_SUBPATH = Path("game/gfx/interface/icons")

# Tier → source DDS filename. Mapping is exhaustive across the CK3 tier
# system (no "other" — non-tiered titles don't get a crown). Frontend
# fetches ``/api/heraldry/assets/title_icons/<tier>.png`` so the keys
# here ARE the canonical tier slugs the parser emits.
_TITLE_TIER_TO_DDS: dict[str, str] = {
    "empire": "empire_crown.dds",
    "kingdom": "kingdom_crown.dds",
    "duchy": "duchy_crown.dds",
    "county": "county_crown.dds",
    "barony": "barony_crown.dds",
}


@dataclass(frozen=True, slots=True)
class NamedColor:
    """One entry in CK3's named-color palette.

    HSV stored as the values from the source file (CK3 normalizes hue
    + saturation + value to 0-1 in ``hsv {}`` form, or uses 0-360 / 0-100
    in ``hsv360 {}`` form). RGB is the converted form (0-255 ints) the
    extractor writes to ``palette.json``.
    """

    name: str
    h: float
    s: float
    v: float
    rgb: tuple[int, int, int]


def find_ck3_install(override: Path | None = None) -> Path | None:
    """Locate the CK3 install directory.

    If ``override`` is given (typically from ``--ck3-dir``) and looks
    like a CK3 root, return it. Otherwise probe the default Steam
    library locations. Returns ``None`` if nothing is found — caller
    should error with an actionable message.
    """
    candidates: list[Path] = []
    if override is not None:
        candidates.append(override)
    candidates.extend(DEFAULT_STEAM_LIBRARIES)
    for candidate in candidates:
        if _looks_like_ck3_install(candidate):
            log.info("found CK3 install: %s", candidate)
            return candidate
    return None


def _looks_like_ck3_install(path: Path) -> bool:
    """Heuristic: a CK3 root has both the CoA dir and the named-color file."""
    return (path / _COA_SUBPATH).is_dir() and (path / _NAMED_COLORS_FILE).is_file()


# Matches ``red = hsv { 0.02 0.8 0.45 }`` and ``brown = hsv360 { 21 74 45 }``.
# Captures: name, mode (hsv|hsv360), three numeric values.
_COLOR_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<mode>hsv360|hsv)\s*\{\s*"
    r"(?P<h>[0-9.]+)\s+"
    r"(?P<s>[0-9.]+)\s+"
    r"(?P<v>[0-9.]+)\s*\}",
    re.MULTILINE,
)


def parse_named_colors(file_path: Path) -> dict[str, NamedColor]:
    """Parse CK3's ``default_colors.txt`` into a name → NamedColor map.

    Supports both ``hsv {}`` (0-1 normalized HSV) and ``hsv360 {}``
    (hue in degrees 0-360, S/V in percentages 0-100). Output RGB is
    always 0-255 ints. Comment lines (``#``) are ignored.

    Names not in this file (e.g. mod-added colors) are silently
    skipped — only the vanilla palette is parsed here.
    """
    text = file_path.read_text(encoding="utf-8-sig")
    out: dict[str, NamedColor] = {}
    for match in _COLOR_RE.finditer(text):
        name = match.group("name")
        mode = match.group("mode")
        h_raw = float(match.group("h"))
        s_raw = float(match.group("s"))
        v_raw = float(match.group("v"))
        # Normalize H/S/V to 0-1 for colorsys.
        if mode == "hsv360":
            h = h_raw / 360.0
            s = s_raw / 100.0
            v = v_raw / 100.0
        else:
            h = h_raw
            s = s_raw
            v = v_raw
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        rgb = (round(r * 255), round(g * 255), round(b * 255))
        out[name] = NamedColor(name=name, h=h, s=s, v=v, rgb=rgb)
    return out


# --- bulk DDS extraction ---


# Files we deliberately skip during extraction. CK3 ships designer-mode
# UI textures in the same directories as the actual heraldry assets
# (``pattern__solid_designer.dds``, ``ce__empty_designer.dds``, etc.) —
# they're for the in-game CoA designer, never referenced as
# ``pattern_solid.dds`` / ``ce_*.dds`` in real coat-of-arms data.
_DESIGNER_FILE_MARKER = "_designer"


@dataclass(frozen=True, slots=True)
class ExtractionSummary:
    """Result of one ``extract_assets`` run."""

    output_dir: Path
    palette_colors: int
    patterns_extracted: int
    emblems_extracted: int
    skipped_designer: int
    reused_existing: int  # files that were already present and not re-converted
    title_icons_extracted: int = 0  # zx2l: tier crowns (empire/kingdom/duchy/county/barony)
    failed: int = 0  # M-P6: conversions that errored — logged and skipped, run continues


# Image-conversion strategy. Production passes ``_convert_via_pillow``;
# tests pass a mock so we can exercise the orchestration without
# constructing real DDS files. Source path → destination path.
ImageConverter = Callable[[Path, Path], None]


def _convert_via_pillow(src: Path, dst: Path) -> None:
    """Default DDS → PNG converter via Pillow."""
    from PIL import Image  # local import — keeps Pillow optional for code paths that don't extract

    with Image.open(src) as img:
        img.load()
        # Explicit format — dst may be a ``*.png.tmp`` staging path
        # (see _convert_atomic), so extension sniffing can't be trusted.
        img.save(dst, format="PNG")


def _convert_atomic(src: Path, dst: Path, converter: ImageConverter) -> bool:
    """Run ``converter`` with crash-safe, contained semantics (M-P6).

    The converter writes to a sibling ``.tmp`` path that is renamed
    into place only on success, so an interrupted or failing
    conversion can never leave a partial file at ``dst`` — which a
    later ``force=False`` run would count as already extracted and
    embed in shields indefinitely. A failed conversion is logged and
    reported as ``False`` rather than raised, so one bad DDS doesn't
    abort the rest of the ~1600-file walk.
    """
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        converter(src, tmp)
        os.replace(tmp, dst)
        return True
    except (OSError, ValueError) as exc:
        log.warning("heraldry: failed to convert %s: %s", src, exc)
        tmp.unlink(missing_ok=True)
        return False


def extract_assets(
    ck3_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
    converter: ImageConverter = _convert_via_pillow,
    progress: Callable[[str, int, int], None] | None = None,
) -> ExtractionSummary:
    """Extract CK3 coat-of-arms assets into ``output_dir``.

    Walks ``<ck3_dir>/game/gfx/coat_of_arms/{patterns,colored_emblems}/``,
    converts each ``.dds`` to ``.png`` via ``converter`` (Pillow by
    default), parses the named-color palette, and writes ``palette.json``
    and ``manifest.json`` alongside the asset directories.

    Output layout::

        <output_dir>/
          palette.json                  {color_name: [r, g, b]}
          manifest.json                 schema_version, ck3_dir, extracted_at, lists
          patterns/<name>.png           one per pattern_*.dds (designer files skipped)
          colored_emblems/<name>.png    one per ce_*.dds (designer files skipped)

    ``force=False`` (default) is idempotent — files that already exist
    in ``output_dir`` are not re-converted. Pass ``force=True`` to
    re-extract from scratch (e.g. after a CK3 patch).

    ``progress(group, current, total)`` is called for every file that
    is *converted* (not for reused / skipped). ``group`` is one of
    ``"patterns"`` / ``"colored_emblems"``.
    """
    coa = ck3_dir / _COA_SUBPATH
    palette_file = ck3_dir / _NAMED_COLORS_FILE

    palette = parse_named_colors(palette_file)

    output_dir.mkdir(parents=True, exist_ok=True)
    patterns_out = output_dir / "patterns"
    emblems_out = output_dir / "colored_emblems"
    title_icons_out = output_dir / "title_icons"
    patterns_out.mkdir(exist_ok=True)
    emblems_out.mkdir(exist_ok=True)
    title_icons_out.mkdir(exist_ok=True)

    # palette.json — always rewritten so a new CK3 patch's palette
    # changes propagate even when --force is off.
    palette_dict = {name: list(c.rgb) for name, c in palette.items()}
    (output_dir / "palette.json").write_text(
        json.dumps(palette_dict, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    patterns_extracted, patterns_reused, patterns_skipped, patterns_failed = _extract_dir(
        coa / "patterns",
        patterns_out,
        force=force,
        converter=converter,
        progress=lambda i, n: progress("patterns", i, n) if progress else None,
    )
    emblems_extracted, emblems_reused, emblems_skipped, emblems_failed = _extract_dir(
        coa / "colored_emblems",
        emblems_out,
        force=force,
        converter=converter,
        progress=lambda i, n: progress("colored_emblems", i, n) if progress else None,
    )

    # ck3_chronicler-zx2l: copy + convert the five tier-crown DDS files
    # to ``title_icons/<tier>.png``. Missing source DDS is non-fatal —
    # logged and skipped — so a partial mod install doesn't break the
    # whole extraction. Tiers with no PNG present at render time fall
    # back to a tier-name text label on the FE.
    title_icons_extracted, title_icons_failed = _extract_title_icons(
        ck3_dir / _INTERFACE_ICONS_SUBPATH,
        title_icons_out,
        force=force,
        converter=converter,
        progress=lambda i, n: progress("title_icons", i, n) if progress else None,
    )

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "ck3_dir": str(ck3_dir),
        "extracted_at": datetime.now(UTC).isoformat(),
        "patterns": sorted(p.stem for p in patterns_out.glob("*.png")),
        "colored_emblems": sorted(e.stem for e in emblems_out.glob("*.png")),
        "title_icons": sorted(t.stem for t in title_icons_out.glob("*.png")),
        "palette_colors": sorted(palette.keys()),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    return ExtractionSummary(
        output_dir=output_dir,
        palette_colors=len(palette),
        patterns_extracted=patterns_extracted + patterns_reused,
        emblems_extracted=emblems_extracted + emblems_reused,
        skipped_designer=patterns_skipped + emblems_skipped,
        reused_existing=patterns_reused + emblems_reused,
        title_icons_extracted=title_icons_extracted,
        failed=patterns_failed + emblems_failed + title_icons_failed,
    )


def _extract_title_icons(
    src: Path,
    dst: Path,
    *,
    force: bool,
    converter: ImageConverter,
    progress: Callable[[int, int], None] | None,
) -> tuple[int, int]:
    """Copy + convert the five tier crowns to ``<dst>/<tier>.png``.

    Source is the top-level ``gfx/interface/icons`` directory (no
    per-tier subdir — the five DDS files sit at the root alongside
    miscellany). Output name is the *tier slug* (``empire.png`` etc.),
    not the original ``empire_crown.dds`` stem, so the FE can fetch
    ``title_icons/<tier>.png`` directly from the parser's tier output.

    Idempotent when ``force=False``. Missing source files are warned
    and skipped — a partial install or mod stripping a tier doesn't
    break the whole extraction. Returns ``(converted, failed)``
    (reused / missing files count as neither).
    """
    converted = 0
    failed = 0
    total = len(_TITLE_TIER_TO_DDS)
    for i, (tier, dds_name) in enumerate(_TITLE_TIER_TO_DDS.items(), start=1):
        source = src / dds_name
        target = dst / f"{tier}.png"
        if not source.is_file():
            log.warning("title icon missing in CK3 install: %s", source)
            if progress:
                progress(i, total)
            continue
        if not force and target.exists():
            if progress:
                progress(i, total)
            continue
        if _convert_atomic(source, target, converter):
            converted += 1
        else:
            failed += 1
        if progress:
            progress(i, total)
    return converted, failed


def _extract_dir(
    src: Path,
    dst: Path,
    *,
    force: bool,
    converter: ImageConverter,
    progress: Callable[[int, int], None] | None,
) -> tuple[int, int, int]:
    """Extract every ``*.dds`` from ``src`` into ``dst`` as PNG.

    Returns ``(converted, reused, skipped, failed)``. Skipped covers
    designer files. Reused covers files already present when
    ``force=False``. Failed covers conversions that errored (logged
    and skipped — the walk continues, M-P6).
    """
    dds_files = sorted(p for p in src.glob("*.dds") if _DESIGNER_FILE_MARKER not in p.name)
    skipped = sum(1 for _ in src.glob("*.dds") if _DESIGNER_FILE_MARKER in _.name)
    converted = 0
    reused = 0
    failed = 0
    for i, dds in enumerate(dds_files, start=1):
        out = dst / f"{dds.stem}.png"
        if not force and out.exists():
            reused += 1
        elif _convert_atomic(dds, out, converter):
            converted += 1
        else:
            failed += 1
        if progress:
            progress(i, len(dds_files))
    return converted, reused, skipped, failed


@dataclass(frozen=True, slots=True)
class HeraldryStatus:
    """Summary of the heraldry-extraction state for the Settings UI.

    Returned by :func:`compute_heraldry_status` and serialised by the
    f9w.2 (a3jc) endpoint. ``is_stale`` is True when the source CoA
    directory under the CK3 install has a later mtime than the
    recorded extraction time — a soft hint to re-extract after a CK3
    patch.
    """

    extracted: bool
    last_extraction_at: str | None
    palette_colors: int
    patterns_count: int
    emblems_count: int
    is_stale: bool


def compute_heraldry_status(
    heraldry_dir: Path,
    ck3_install_dir: Path | None,
) -> HeraldryStatus:
    """Inspect ``heraldry_dir`` to summarise extraction state.

    Reads ``manifest.json`` for the timestamp + lists, falls back to
    counting files on disk if the manifest is missing/unreadable.
    Compares the timestamp against ``<ck3_install_dir>/<_COA_SUBPATH>``
    mtime to decide ``is_stale``. ``ck3_install_dir`` may be ``None``
    when the install isn't configured — stale check is skipped.
    """
    manifest_path = heraldry_dir / "manifest.json"
    palette_path = heraldry_dir / "palette.json"

    manifest: dict | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None

    last_extraction_at: str | None = None
    if isinstance(manifest, dict):
        ts = manifest.get("extracted_at")
        if isinstance(ts, str) and ts:
            last_extraction_at = ts

    palette_colors = 0
    if palette_path.is_file():
        try:
            palette_data = json.loads(palette_path.read_text(encoding="utf-8"))
            if isinstance(palette_data, dict):
                palette_colors = len(palette_data)
        except (OSError, ValueError):
            palette_colors = 0

    patterns_dir = heraldry_dir / "patterns"
    emblems_dir = heraldry_dir / "colored_emblems"
    patterns_count = sum(1 for _ in patterns_dir.glob("*.png")) if patterns_dir.is_dir() else 0
    emblems_count = sum(1 for _ in emblems_dir.glob("*.png")) if emblems_dir.is_dir() else 0
    extracted = palette_colors > 0 and (patterns_count > 0 or emblems_count > 0)

    is_stale = False
    if extracted and last_extraction_at and ck3_install_dir is not None:
        coa_dir = ck3_install_dir / _COA_SUBPATH
        if coa_dir.is_dir():
            try:
                last_dt = datetime.fromisoformat(last_extraction_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=UTC)
                source_mtime = datetime.fromtimestamp(coa_dir.stat().st_mtime, tz=UTC)
                is_stale = source_mtime > last_dt
            except (OSError, ValueError):
                is_stale = False

    return HeraldryStatus(
        extracted=extracted,
        last_extraction_at=last_extraction_at,
        palette_colors=palette_colors,
        patterns_count=patterns_count,
        emblems_count=emblems_count,
        is_stale=is_stale,
    )

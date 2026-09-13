"""Unit tests for chronicler.heraldry.extractor.

CK3 install discovery + named-color palette parsing + bulk DDS
extraction orchestration (with a fake converter — Pillow's actual DDS
support is exercised manually + by live extraction against the real
CK3 install).
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from chronicler.heraldry.extractor import (
    MANIFEST_SCHEMA_VERSION,
    ExtractionSummary,
    NamedColor,
    extract_assets,
    find_ck3_install,
    parse_named_colors,
)

# Mirrors the format CK3 ships at common/named_colors/default_colors.txt.
_SAMPLE_PALETTE = """\
﻿colors = {
\t# used by coat of arms
\tred 		= hsv {		0.02 	0.8 	0.45 	}
\tblue 		= hsv {		0.58 	0.8 	0.4 	}
\tgreen		= hsv {		0.35 	0.6 	0.30 	}
\tblack		= hsv {		0.1 	0.25 	0.10 	}
\twhite		= hsv {		0.08 	0.02 	0.8 	}

\tbrown 		= hsv360 {	021 	074 	045		}
\tyellow_light= hsv {		0.1 	0.80 	1.0 	}
}
"""


@pytest.fixture
def palette_file(tmp_path: Path) -> Path:
    p = tmp_path / "default_colors.txt"
    p.write_text(_SAMPLE_PALETTE, encoding="utf-8")
    return p


def test_parse_named_colors_returns_one_entry_per_color(palette_file: Path) -> None:
    palette = parse_named_colors(palette_file)
    assert set(palette.keys()) == {
        "red",
        "blue",
        "green",
        "black",
        "white",
        "brown",
        "yellow_light",
    }


def test_parse_named_colors_normalizes_hsv360_to_unit_range(palette_file: Path) -> None:
    """brown is the only hsv360 entry in the fixture; verify H/S/V are
    converted from (degrees, percent, percent) to the 0-1 form colorsys
    expects."""
    palette = parse_named_colors(palette_file)
    brown = palette["brown"]
    # 21° / 360° == 0.0583... ; 74% == 0.74; 45% == 0.45
    assert brown.h == pytest.approx(21 / 360, abs=1e-4)
    assert brown.s == pytest.approx(0.74, abs=1e-4)
    assert brown.v == pytest.approx(0.45, abs=1e-4)


def test_parse_named_colors_converts_hsv_to_rgb_0_255(palette_file: Path) -> None:
    """red = hsv { 0.02 0.8 0.45 } → known RGB by colorsys."""
    palette = parse_named_colors(palette_file)
    red = palette["red"]
    # Hand-computed via colorsys.hsv_to_rgb(0.02, 0.8, 0.45) → (0.45, 0.117, 0.090)
    # → rounded: (115, 30, 23)
    r, g, b = red.rgb
    assert 100 <= r <= 130
    assert 20 <= g <= 40
    assert 15 <= b <= 35


def test_parse_named_colors_ignores_unknown_lines(tmp_path: Path) -> None:
    """Comment lines + non-matching syntax are silently skipped."""
    p = tmp_path / "default_colors.txt"
    p.write_text(
        "# leading comment\n"
        "colors = {\n"
        "  red = hsv { 0.02 0.8 0.45 }\n"
        "  # comment in the middle\n"
        "  invalid_garbage\n"
        "  green = hsv { 0.35 0.6 0.30 }\n"
        "}\n",
        encoding="utf-8",
    )
    palette = parse_named_colors(p)
    assert set(palette.keys()) == {"red", "green"}


def test_named_color_dataclass_carries_all_fields(palette_file: Path) -> None:
    palette = parse_named_colors(palette_file)
    red = palette["red"]
    assert isinstance(red, NamedColor)
    assert red.name == "red"
    assert isinstance(red.rgb, tuple) and len(red.rgb) == 3


def test_find_ck3_install_returns_none_when_nothing_present(tmp_path: Path) -> None:
    """An override path that doesn't look like CK3 + no Steam libraries
    on disk → None. (We can't make Steam libraries appear/disappear in
    a test, so this exercises the override-rejection path.)"""
    fake = tmp_path / "not-ck3"
    fake.mkdir()
    # find_ck3_install will reject the override AND probe defaults; on
    # a CI machine without CK3 installed the result is None.
    result = find_ck3_install(override=fake)
    # Note: on the dev box this returns the real install. We can only
    # assert "not the fake we passed in".
    assert result != fake


def test_find_ck3_install_accepts_well_shaped_override(tmp_path: Path) -> None:
    """A path that has both game/gfx/coat_of_arms/ and the named-colors
    file is recognised as a CK3 root."""
    fake = tmp_path / "fake-ck3"
    (fake / "game" / "gfx" / "coat_of_arms").mkdir(parents=True)
    (fake / "game" / "common" / "named_colors").mkdir(parents=True)
    (fake / "game" / "common" / "named_colors" / "default_colors.txt").write_text(
        "colors = { red = hsv { 0.0 1.0 1.0 } }", encoding="utf-8"
    )
    result = find_ck3_install(override=fake)
    assert result == fake


# --- bulk extraction (orchestration tested with a fake converter; live
#     DDS conversion is exercised manually + by the integration test below) ---


def _build_fake_ck3(root: Path) -> Path:
    """Synthesise a CK3-shaped directory tree with a few .dds + the
    palette file. Returns the install root."""
    coa = root / "game" / "gfx" / "coat_of_arms"
    (coa / "patterns").mkdir(parents=True)
    (coa / "colored_emblems").mkdir(parents=True)
    # Real assets — empty files; the test passes a no-op converter so
    # we never actually open them via Pillow.
    (coa / "patterns" / "pattern_solid.dds").touch()
    (coa / "patterns" / "pattern_per_pale.dds").touch()
    # Designer file — must be skipped.
    (coa / "patterns" / "pattern__solid_designer.dds").touch()
    (coa / "colored_emblems" / "ce_lion.dds").touch()
    (coa / "colored_emblems" / "ce_eagle.dds").touch()
    (coa / "colored_emblems" / "ce__empty_designer.dds").touch()

    # ck3_chronicler-zx2l: title-tier crown DDS files live at the top
    # level of gfx/interface/icons (not in a per-tier subdir). Touch
    # all five so extract_assets has them to copy.
    icons = root / "game" / "gfx" / "interface" / "icons"
    icons.mkdir(parents=True)
    for crown in (
        "empire_crown.dds",
        "kingdom_crown.dds",
        "duchy_crown.dds",
        "county_crown.dds",
        "barony_crown.dds",
    ):
        (icons / crown).touch()

    nc = root / "game" / "common" / "named_colors"
    nc.mkdir(parents=True)
    (nc / "default_colors.txt").write_text(
        "colors = {\n  red = hsv { 0.02 0.8 0.45 }\n  green = hsv { 0.35 0.6 0.30 }\n}\n",
        encoding="utf-8",
    )
    return root


def _fake_converter(src: Path, dst: Path) -> None:
    """Test stand-in for Pillow's DDS → PNG conversion: just write a
    placeholder PNG header so downstream existence checks behave."""
    dst.write_bytes(b"\x89PNG\r\n\x1a\n")


def test_extract_assets_writes_palette_json(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    summary = extract_assets(ck3, out, converter=_fake_converter)

    palette_path = out / "palette.json"
    assert palette_path.exists()
    palette = _json.loads(palette_path.read_text())
    # Sorted keys (we always sort_keys=True for stable diffs).
    assert list(palette.keys()) == ["green", "red"]
    assert all(len(rgb) == 3 for rgb in palette.values())
    assert summary.palette_colors == 2


def test_extract_assets_converts_real_files_and_skips_designer(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    summary = extract_assets(ck3, out, converter=_fake_converter)

    assert summary.patterns_extracted == 2  # solid + per_pale
    assert summary.emblems_extracted == 2  # lion + eagle
    assert summary.skipped_designer == 2  # pattern__solid_designer + ce__empty_designer
    assert summary.reused_existing == 0  # first run

    assert (out / "patterns" / "pattern_solid.png").exists()
    assert (out / "patterns" / "pattern_per_pale.png").exists()
    assert (out / "colored_emblems" / "ce_lion.png").exists()
    # Designer files must NOT have been converted.
    assert not (out / "patterns" / "pattern__solid_designer.png").exists()


def test_extract_assets_writes_manifest_with_schema_version(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    extract_assets(ck3, out, converter=_fake_converter)

    manifest = _json.loads((out / "manifest.json").read_text())
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["ck3_dir"] == str(ck3)
    assert manifest["patterns"] == ["pattern_per_pale", "pattern_solid"]
    assert manifest["colored_emblems"] == ["ce_eagle", "ce_lion"]
    assert manifest["palette_colors"] == ["green", "red"]
    assert "extracted_at" in manifest


def test_extract_assets_idempotent_without_force(tmp_path: Path) -> None:
    """Re-running without --force reuses existing PNGs (doesn't call
    the converter again). The conversion is the slow part on real
    DDS files (~1500 emblems, several seconds each on first run)."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"

    call_count = 0

    def _counting_converter(src: Path, dst: Path) -> None:
        nonlocal call_count
        call_count += 1
        _fake_converter(src, dst)

    extract_assets(ck3, out, converter=_counting_converter)
    first_calls = call_count
    assert first_calls == 9  # 2 patterns + 2 emblems + 5 title icons

    # Second run — output PNGs already exist, converter must NOT be
    # called for them.
    extract_assets(ck3, out, converter=_counting_converter)
    assert call_count == first_calls  # no new calls


def test_extract_assets_force_re_converts_existing(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"

    call_count = 0

    def _counting_converter(src: Path, dst: Path) -> None:
        nonlocal call_count
        call_count += 1
        _fake_converter(src, dst)

    extract_assets(ck3, out, converter=_counting_converter)
    assert call_count == 9

    # Re-run with force=True → all 9 reconverted.
    extract_assets(ck3, out, converter=_counting_converter, force=True)
    assert call_count == 18

    # On the force run nothing was reused.
    summary = extract_assets(ck3, out, converter=_counting_converter, force=True)
    assert summary.reused_existing == 0


def test_extract_assets_progress_callback_fires(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    progress_calls: list[tuple[str, int, int]] = []
    extract_assets(
        ck3,
        out,
        converter=_fake_converter,
        progress=lambda group, i, n: progress_calls.append((group, i, n)),
    )
    # 2 patterns + 2 emblems + 5 title icons (zx2l) = 9 progress events
    assert len(progress_calls) == 9
    groups = [c[0] for c in progress_calls]
    assert groups.count("patterns") == 2
    assert groups.count("colored_emblems") == 2
    assert groups.count("title_icons") == 5


def test_extract_assets_returns_summary_dataclass(tmp_path: Path) -> None:
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    summary = extract_assets(ck3, out, converter=_fake_converter)
    assert isinstance(summary, ExtractionSummary)
    assert summary.output_dir == out


def test_extract_assets_writes_title_icons_keyed_by_tier(tmp_path: Path) -> None:
    """ck3_chronicler-zx2l: the five tier-crown DDS files are copied
    out as ``<tier>.png`` (no ``_crown`` suffix) so the frontend can
    fetch them by tier slug directly."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    summary = extract_assets(ck3, out, converter=_fake_converter)

    title_icons = out / "title_icons"
    assert title_icons.is_dir()
    expected = {"empire.png", "kingdom.png", "duchy.png", "county.png", "barony.png"}
    assert {p.name for p in title_icons.glob("*.png")} == expected
    assert summary.title_icons_extracted == 5


def test_extract_assets_records_title_icons_in_manifest(tmp_path: Path) -> None:
    """Manifest gains a ``title_icons`` list so the FE can detect
    extraction having run (and which tiers are available)."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"
    extract_assets(ck3, out, converter=_fake_converter)
    manifest = _json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest["title_icons"]) == [
        "barony",
        "county",
        "duchy",
        "empire",
        "kingdom",
    ]


def test_extract_assets_continues_past_failing_dds(tmp_path: Path) -> None:
    """M-P6 (27ov.55): one unconvertible DDS must not abort the run —
    it is logged, counted as failed, and every other file (including
    the manifest) still lands."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"

    def _failing_converter(src: Path, dst: Path) -> None:
        if src.name == "ce_lion.dds":
            raise OSError("truncated DDS")
        _fake_converter(src, dst)

    summary = extract_assets(ck3, out, converter=_failing_converter)

    assert summary.failed == 1
    assert not (out / "colored_emblems" / "ce_lion.png").exists()
    assert (out / "colored_emblems" / "ce_eagle.png").exists()
    assert (out / "patterns" / "pattern_solid.png").exists()
    assert (out / "manifest.json").exists()


def test_extract_assets_failed_convert_leaves_no_partial_png(tmp_path: Path) -> None:
    """M-P6 (27ov.55): a converter crash mid-write must not leave a
    partial PNG at the final path — every later force=False run would
    count it as 'reused' and the truncated file would be embedded in
    shields indefinitely."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"

    def _crashing_converter(src: Path, dst: Path) -> None:
        if src.name == "ce_lion.dds":
            dst.write_bytes(b"\x89PN")  # partial write, then the crash
            raise OSError("disk full")
        _fake_converter(src, dst)

    extract_assets(ck3, out, converter=_crashing_converter)
    emblems = out / "colored_emblems"
    assert not (emblems / "ce_lion.png").exists()
    assert list(emblems.glob("*.tmp")) == []

    # A later normal (force=False) run retries the failed file instead
    # of treating it as already extracted.
    summary = extract_assets(ck3, out, converter=_fake_converter)
    assert (emblems / "ce_lion.png").exists()
    assert summary.failed == 0


def test_extract_assets_title_icon_failure_is_contained(tmp_path: Path) -> None:
    """M-P6 (27ov.55): same containment for the tier-crown conversions."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    out = tmp_path / "heraldry"

    def _failing_converter(src: Path, dst: Path) -> None:
        if src.name == "kingdom_crown.dds":
            raise OSError("bad DDS header")
        _fake_converter(src, dst)

    summary = extract_assets(ck3, out, converter=_failing_converter)

    assert summary.failed == 1
    assert summary.title_icons_extracted == 4
    assert not (out / "title_icons" / "kingdom.png").exists()
    assert (out / "title_icons" / "empire.png").exists()


def test_extract_assets_tolerates_missing_title_icon_dds(tmp_path: Path) -> None:
    """A partial install / mod stripping a tier shouldn't break the
    extraction — missing DDS is warned and skipped, the rest still
    convert."""
    ck3 = _build_fake_ck3(tmp_path / "ck3")
    (ck3 / "game" / "gfx" / "interface" / "icons" / "empire_crown.dds").unlink()
    out = tmp_path / "heraldry"
    summary = extract_assets(ck3, out, converter=_fake_converter)

    title_icons = out / "title_icons"
    assert not (title_icons / "empire.png").exists()
    assert (title_icons / "kingdom.png").exists()
    assert summary.title_icons_extracted == 4

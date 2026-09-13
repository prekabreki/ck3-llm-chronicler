"""Tests for chronicler.export.coa_svg — Python port of RealHeraldry."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from chronicler.export.coa_svg import (
    pick_slot,
    recolour_texture_to_data_uri,
    render_coa_svg,
)


def _write_synthetic_pattern_png(path: Path) -> Path:
    """Build a 4-pixel test pattern: each pixel encodes one slot."""
    img = Image.new("RGBA", (4, 1))
    # slot1 (R only), slot2 (R+G), slot3 (B), unused (zero)
    img.putpixel((0, 0), (255, 0, 0, 255))
    img.putpixel((1, 0), (255, 255, 0, 255))
    img.putpixel((2, 0), (0, 0, 255, 255))
    img.putpixel((3, 0), (0, 0, 0, 0))
    img.save(path)
    return path


def test_pick_slot_classifies_channel_encoding() -> None:
    slots = {"color1": (1, 1, 1), "color2": (2, 2, 2), "color3": (3, 3, 3)}
    assert pick_slot(255, 0, 0, slots) == (1, 1, 1)
    assert pick_slot(255, 255, 0, slots) == (2, 2, 2)
    assert pick_slot(0, 0, 255, slots) == (3, 3, 3)


def test_pick_slot_falls_back_when_slot_missing() -> None:
    # Texture wants slot3 but only color1 specified → falls back to color1
    # (matches the JS heuristic in useTintedTexture.ts).
    slots = {"color1": (9, 9, 9)}
    assert pick_slot(0, 0, 255, slots) == (9, 9, 9)


def test_pick_slot_returns_none_for_zero_pixel() -> None:
    assert pick_slot(0, 0, 0, {"color1": (1, 1, 1)}) is None


def test_recolour_substitutes_palette_colors(tmp_path: Path) -> None:
    png = _write_synthetic_pattern_png(tmp_path / "p.png")
    slots = {
        "color1": (10, 20, 30),
        "color2": (40, 50, 60),
        "color3": (70, 80, 90),
    }
    data_uri = recolour_texture_to_data_uri(png, slots)
    assert data_uri.startswith("data:image/png;base64,")
    # Decode the base64 back, walk the pixels, check substitution.
    import base64

    payload = data_uri.split(",", 1)[1]
    img = Image.open(BytesIO(base64.b64decode(payload)))
    assert img.getpixel((0, 0))[:3] == (10, 20, 30)
    assert img.getpixel((1, 0))[:3] == (40, 50, 60)
    assert img.getpixel((2, 0))[:3] == (70, 80, 90)
    # Zero-alpha pixel stays transparent.
    assert img.getpixel((3, 0))[3] == 0


def test_render_coa_svg_emits_self_contained_svg(tmp_path: Path) -> None:
    """A minimal CoA structure produces an SVG with at least one
    <image href="data:..."> layer."""
    # render_coa_svg looks up patterns under heraldry_dir/patterns/<stem>.png
    patterns_dir = tmp_path / "patterns"
    patterns_dir.mkdir()
    _write_synthetic_pattern_png(patterns_dir / "p.png")
    coa = {
        "pattern": "p.dds",  # extractor strips .dds → looks up p.png
        "color1": "black",
        "color2": "green",
    }
    palette = {"black": [0, 0, 0], "green": [0, 128, 0]}
    svg = render_coa_svg(
        coa,
        palette=palette,
        heraldry_dir=tmp_path,
        size=80,
        label="Alfred",
    )
    assert svg.startswith("<svg ")
    assert "data:image/png;base64," in svg
    assert 'aria-label="Alfred"' in svg


def test_render_coa_svg_handles_missing_texture(tmp_path: Path) -> None:
    """A pattern referencing a missing PNG must NOT crash — the layer
    is silently dropped (the bundle assembler records a warning at a
    higher level)."""
    coa = {"pattern": "missing.dds", "color1": "black"}
    palette = {"black": [0, 0, 0]}
    svg = render_coa_svg(coa, palette=palette, heraldry_dir=tmp_path, size=80)
    # Still emits a valid SVG envelope, just without the pattern image.
    assert svg.startswith("<svg ")
    assert "data:image/png;base64," not in svg

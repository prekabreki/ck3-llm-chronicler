"""Python port of frontend/src/components/RealHeraldry.tsx and
useTintedTexture.ts (ck3_chronicler-441).

Composes a character's coat of arms as a self-contained SVG string —
texture pixels are tinted in-place via Pillow and embedded as base64
data URIs so the resulting SVG works offline without referencing any
external asset files. Used by the export bundle to ship per-character
shields alongside the markdown.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

# Heater shield path mirrors RealHeraldry.tsx exactly so the Python +
# in-app shields are pixel-equivalent at the silhouette level.
_SHIELD_PATH = "M 4 4 L 76 4 L 76 36 C 76 60 60 78 40 92 C 20 78 4 60 4 36 Z"
_SHIELD_VB = "0 0 80 96"
_SHIELD_W = 80.0
_SHIELD_H = 96.0

RGB = tuple[int, int, int]
SlotColors = dict[str, RGB]  # keys: "color1", "color2", "color3"
Palette = dict[str, list[int]]


def pick_slot(r: int, g: int, b: int, slots: SlotColors) -> RGB | None:
    """Mirror of useTintedTexture.ts:pickSlot. Returns the palette RGB
    for whichever channel-encoded slot this pixel belongs to, or None
    when the pixel is fully zero."""
    if r == 0 and g == 0 and b == 0:
        return None
    preferred: RGB | None
    if r > 0 and g > 0 and b == 0:
        preferred = slots.get("color2")
    elif r > 0 and g == 0 and b == 0:
        preferred = slots.get("color1")
    elif b > 0 and r == 0:
        preferred = slots.get("color3")
    elif r >= g and r >= b:
        preferred = slots.get("color1")
    elif g >= r and g >= b:
        preferred = slots.get("color2")
    else:
        preferred = slots.get("color3")
    return preferred or slots.get("color1") or slots.get("color2") or slots.get("color3")


def recolour_texture_to_data_uri(texture_path: Path, slots: SlotColors) -> str:
    """Load a CK3 heraldry PNG, walk its pixels substituting the
    channel-encoded slot value with the actual palette RGB, and return
    a base64 data URI suitable for an SVG <image href="...">.

    ck3_chronicler-dppy: pixel walk uses tobytes()/frombytes() instead
    of getdata()/putdata() — the latter pair is deprecated in Pillow
    and slated for removal in Pillow 14 (Oct 2027).
    """
    with Image.open(texture_path) as img:
        rgba = img.convert("RGBA")
        raw = rgba.tobytes()
        out = bytearray(len(raw))  # zero-initialized → transparent for skipped pixels
        for i in range(0, len(raw), 4):
            a = raw[i + 3]
            if a == 0:
                continue
            picked = pick_slot(raw[i], raw[i + 1], raw[i + 2], slots)
            if picked is None:
                continue
            out[i] = picked[0]
            out[i + 1] = picked[1]
            out[i + 2] = picked[2]
            out[i + 3] = a
        result = Image.frombytes("RGBA", rgba.size, bytes(out))
        buf = BytesIO()
        result.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _resolve_slots(node: dict[str, Any], palette: Palette) -> SlotColors:
    """Look up color1/color2/color3 names on a CoA node into RGB tuples."""
    out: SlotColors = {}
    for key in ("color1", "color2", "color3"):
        name = node.get(key)
        if isinstance(name, str):
            rgb = palette.get(name)
            if rgb and len(rgb) >= 3:
                out[key] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    return out


def _texture_path(filename: str, kind: str, heraldry_dir: Path) -> Path:
    """CK3 stores filenames with .dds; the extractor renames to .png."""
    stem = filename.rsplit(".", 1)[0] if filename.lower().endswith(".dds") else filename
    return heraldry_dir / kind / f"{stem}.png"


def _emit_pattern_layer(
    pattern: str,
    slots: SlotColors,
    heraldry_dir: Path,
    x: float,
    y: float,
    w: float,
    h: float,
) -> str:
    path = _texture_path(pattern, "patterns", heraldry_dir)
    if not path.is_file():
        log.debug("pattern texture missing: %s", path)
        return ""
    href = recolour_texture_to_data_uri(path, slots)
    return (
        f'<image href="{href}" x="{x}" y="{y}" width="{w}" height="{h}" '
        f'preserveAspectRatio="none"/>'
    )


def _emit_emblem(
    emblem: dict[str, Any],
    palette: Palette,
    heraldry_dir: Path,
    parent_w: float,
    parent_h: float,
) -> str:
    texture = emblem.get("texture")
    if not isinstance(texture, str):
        return ""
    path = _texture_path(texture, "colored_emblems", heraldry_dir)
    if not path.is_file():
        log.debug("emblem texture missing: %s", path)
        return ""
    slots = _resolve_slots(emblem, palette)
    href = recolour_texture_to_data_uri(path, slots)

    raw_instances = emblem.get("instance")
    instances: list[dict[str, Any]]
    if isinstance(raw_instances, list):
        instances = [i for i in raw_instances if isinstance(i, dict)]
    elif isinstance(raw_instances, dict):
        instances = [raw_instances]
    else:
        instances = [{}]

    chunks: list[str] = []
    for inst in instances:
        pos = inst.get("position") or [0.5, 0.5]
        scale = inst.get("scale") or [1, 1]
        sx, sy = float(scale[0]), float(scale[1])
        px, py = float(pos[0]), float(pos[1])
        w = parent_w * sx
        h = parent_h * sy
        x = px * parent_w - w / 2
        y = py * parent_h - h / 2
        aspect = "none" if abs(sx - sy) > 0.001 else "xMidYMid meet"
        rotation = inst.get("rotation")
        cx = px * parent_w
        cy = py * parent_h
        transform_attr = (
            f' transform="rotate({rotation} {cx} {cy})"'
            if isinstance(rotation, (int, float)) and rotation
            else ""
        )
        chunks.append(
            f'<image href="{href}" x="{x}" y="{y}" width="{w}" height="{h}" '
            f'preserveAspectRatio="{aspect}"{transform_attr}/>'
        )
    return "".join(chunks)


def _emit_subshield(
    sub: dict[str, Any],
    palette: Palette,
    heraldry_dir: Path,
    parent_w: float,
    parent_h: float,
) -> str:
    instance = sub.get("instance") or {}
    scale = instance.get("scale") or [1, 1]
    offset = instance.get("offset") or [0, 0]
    sx, sy = float(scale[0]), float(scale[1])
    ox, oy = float(offset[0]), float(offset[1])
    tx = ox * parent_w
    ty = oy * parent_h
    w = parent_w * sx
    h = parent_h * sy
    sub_slots = _resolve_slots(sub, palette)
    body: list[str] = []
    if isinstance(sub.get("pattern"), str):
        body.append(_emit_pattern_layer(sub["pattern"], sub_slots, heraldry_dir, 0, 0, w, h))
    nested = sub.get("sub")
    if isinstance(nested, dict):
        body.append(_emit_subshield(nested, palette, heraldry_dir, w, h))
    for s in sub.get("subs") or []:
        if isinstance(s, dict):
            body.append(_emit_subshield(s, palette, heraldry_dir, w, h))
    if isinstance(sub.get("colored_emblem"), dict):
        body.append(_emit_emblem(sub["colored_emblem"], palette, heraldry_dir, w, h))
    for e in sub.get("colored_emblems") or []:
        if isinstance(e, dict):
            body.append(_emit_emblem(e, palette, heraldry_dir, w, h))
    return f'<g transform="translate({tx} {ty})">{"".join(body)}</g>'


def render_coa_svg(
    coa: dict[str, Any],
    *,
    palette: Palette,
    heraldry_dir: Path,
    size: int = 80,
    label: str | None = None,
    ring: bool = False,
) -> str:
    """Compose a self-contained SVG string for a character's coat of
    arms. Mirrors RealHeraldry.tsx layer order: clipped pattern → subs
    → emblems → unclipped rim. Missing textures drop their layer
    silently (caller surfaces a warning)."""
    slots = _resolve_slots(coa, palette)
    layers: list[str] = []
    pattern = coa.get("pattern")
    if isinstance(pattern, str):
        layers.append(_emit_pattern_layer(pattern, slots, heraldry_dir, 0, 0, _SHIELD_W, _SHIELD_H))
    sub = coa.get("sub")
    if isinstance(sub, dict):
        layers.append(_emit_subshield(sub, palette, heraldry_dir, _SHIELD_W, _SHIELD_H))
    for s in coa.get("subs") or []:
        if isinstance(s, dict):
            layers.append(_emit_subshield(s, palette, heraldry_dir, _SHIELD_W, _SHIELD_H))
    if isinstance(coa.get("colored_emblem"), dict):
        layers.append(
            _emit_emblem(coa["colored_emblem"], palette, heraldry_dir, _SHIELD_W, _SHIELD_H)
        )
    for e in coa.get("colored_emblems") or []:
        if isinstance(e, dict):
            layers.append(_emit_emblem(e, palette, heraldry_dir, _SHIELD_W, _SHIELD_H))

    label_attr = f' aria-label="{label}"' if label else ""
    ring_layer = (
        f'<path d="{_SHIELD_PATH}" fill="none" stroke="#C9A24A" stroke-width="0.6"/>'
        if ring
        else ""
    )
    h = int(size * 1.15)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_SHIELD_VB}" '
        f'width="{size}" height="{h}"{label_attr}>'
        '<defs><clipPath id="coa-clip"><path d="' + _SHIELD_PATH + '"/></clipPath></defs>'
        f'<g clip-path="url(#coa-clip)">{"".join(layers)}</g>'
        f'<path d="{_SHIELD_PATH}" fill="none" stroke="#7C5A14" stroke-width="1.5"/>'
        f"{ring_layer}"
        "</svg>"
    )

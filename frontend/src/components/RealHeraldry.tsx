/**
 * Real CK3 heraldry renderer (ck3_chronicler-7ao subsystem 3, v2 — tinted).
 *
 * Composes a character's actual in-game coat of arms via SVG layers
 * pointing at *tinted* versions of the extracted PNG assets. The raw
 * PNGs are channel-encoded (R-only=slot1, R+G=slot2, B=slot3) per the
 * CK3 shader convention; useTintedTexture walks each pixel and
 * substitutes the actual palette colour for that slot. Result:
 * Toirrdelbach's shield in his actual black/green/yellow + red/yellow
 * + black-leopard colours, not the raw shader-input colours.
 *
 * Drop-in API replacement for the procedural Heraldry component — when
 * CoA data is available, use this; when it isn't (untracked character,
 * save-tail hasn't refreshed yet, etc.), fall back to procedural via
 * <HeraldryWithFallback />.
 *
 * Remaining caveats:
 *  - **Sub-shield instance.scale/offset semantics are approximated.**
 *    Per-bend / per-chevron / quarterly divisions may not align
 *    pixel-perfectly with the in-game shield until we examine more
 *    samples and refine the transform conventions.
 *  - **Tinting heuristic is the dominant-channel rule** (works for
 *    every vanilla pattern + emblem we've seen). A few rare emblems
 *    with an explicit `mask` field may need the mask honoured exactly;
 *    that's a small follow-up if it bites.
 */

import { useId } from 'react';

import type { CoaColoredEmblem, CoaDefinition, Palette } from './CoaTypes';
import {
  Heraldry as ProceduralHeraldry,
  HeraldryShieldSvg as HeraldryShieldSvgFallback,
} from './Heraldry';
import type { RGB, SlotColors } from './useTintedTexture';
import { emblemSlots, useTintedTexture } from './useTintedTexture';

// Heater shield path — same as the procedural Heraldry component so
// shields are interchangeable visually at the silhouette level.
const SHIELD_PATH = 'M 4 4 L 76 4 L 76 36 C 76 60 60 78 40 92 C 20 78 4 60 4 36 Z';
const SHIELD_VB = '0 0 80 96';
const SHIELD_W = 80;
const SHIELD_H = 96;

/** Base URL for the extracted heraldry PNG assets. Served by the
 * FastAPI app from <chronicler-data-dir>/heraldry/ at /api/heraldry/assets/. */
const ASSETS_BASE = '/api/heraldry/assets';

function assetUrl(textureFilename: string, kind: 'patterns' | 'colored_emblems'): string {
  // CK3 stores filenames with .dds; the extractor renames to .png.
  const stem = textureFilename.replace(/\.dds$/i, '');
  return `${ASSETS_BASE}/${kind}/${stem}.png`;
}

export interface RealHeraldryProps {
  /** The character's CoA structure (from /api/.../coa). */
  coa: CoaDefinition;
  /** The named-color palette (from /api/heraldry/assets/palette.json).
   * Currently unused by the MVP renderer but reserved for the tinting
   * iteration. */
  palette: Palette;
  /** Outer width in px; height is 1.15× this. Default 80. */
  size?: number;
  /** Accessible label (character name). */
  label?: string;
  /** When true, a thin gilded ring is added to the rim. */
  ring?: boolean;
}

export function RealHeraldry({
  coa,
  palette,
  size = 80,
  label,
  ring = false,
}: RealHeraldryProps): React.JSX.Element {
  return (
    <span className="shield" style={{ width: size, height: size * 1.15, display: 'inline-block' }}>
      <RealHeraldrySvg coa={coa} palette={palette} size={size} label={label} ring={ring} />
    </span>
  );
}

/** SVG-only variant of {@link RealHeraldry}: returns the bare ``<svg>``
 * element (no ``<span>`` wrapper) so it can be embedded inside a parent
 * ``<svg>`` via ``x``/``y`` positional attributes — the SVG-in-SVG
 * pattern the LineagePage tree already uses for the procedural shield
 * (ck3_chronicler-h9u6 / 4y0v slice 2.1).
 */
export function RealHeraldrySvg({
  coa,
  palette,
  size = 80,
  label,
  ring = false,
  x,
  y,
}: RealHeraldryProps & { x?: number; y?: number }): React.JSX.Element {
  const clipId = `coa-clip-${useId()}`;
  return (
    <svg
      viewBox={SHIELD_VB}
      width={size}
      height={size * 1.15}
      aria-label={label}
      x={x}
      y={y}
    >
      <defs>
        <clipPath id={clipId}>
          <path d={SHIELD_PATH} />
        </clipPath>
      </defs>

      {/* Field: top-level pattern, then sub-shields, then charges. */}
      <g clipPath={`url(#${clipId})`}>
        {coa.pattern && (
          <PatternLayer
            pattern={coa.pattern}
            slots={resolveSlots(coa, palette)}
            x={0}
            y={0}
            width={SHIELD_W}
            height={SHIELD_H}
          />
        )}

        {coa.sub && (
          <SubShield sub={coa.sub} palette={palette} parentW={SHIELD_W} parentH={SHIELD_H} depth={1} />
        )}
        {coa.subs?.map((s, i) => (
          <SubShield key={i} sub={s} palette={palette} parentW={SHIELD_W} parentH={SHIELD_H} depth={1} />
        ))}

        {/* Some CK3 saves emit `colored_emblem: [array]` (plural meaning,
            singular field name) when a node has multiple charges — see
            vysp.5 smoke session. Treat array under the singular name as a
            list, single object as a list-of-one. Combined with the
            already-list `colored_emblems` so caller-shape doesn't matter. */}
        {normalizeEmblems(coa).map((e, i) => (
          <Emblem
            key={i}
            emblem={e}
            palette={palette}
            parentW={SHIELD_W}
            parentH={SHIELD_H}
          />
        ))}
      </g>

      {/* Outer rim — same as procedural for visual consistency. */}
      <path d={SHIELD_PATH} fill="none" stroke="#7C5A14" strokeWidth="1.5" />
      {ring && <path d={SHIELD_PATH} fill="none" stroke="#C9A24A" strokeWidth="0.6" />}
    </svg>
  );
}

/** Resolve color1/color2/color3 *string names* on a CoA node to RGB
 * triples via the palette. Missing names → undefined slot (the tinter
 * makes those pixels transparent rather than rendering shader-input
 * colours). */
function resolveSlots(node: CoaDefinition | CoaColoredEmblem, palette: Palette): SlotColors {
  return {
    color1: lookupColor(node.color1, palette),
    color2: lookupColor(node.color2, palette),
    color3: lookupColor(node.color3, palette),
  };
}

function lookupColor(name: string | undefined, palette: Palette): RGB | undefined {
  if (!name) return undefined;
  const rgb = palette[name];
  return rgb ? [rgb[0], rgb[1], rgb[2]] : undefined;
}

/** Collect every emblem on a node, normalising the two save-format
 * quirks: `colored_emblem` may arrive as a single object OR (when CK3
 * emitted multiple charges under that singular key) an array; the
 * already-plural `colored_emblems` is appended. Anything that isn't a
 * dict-shaped emblem is dropped so a malformed entry can't crash the
 * renderer. */
function normalizeEmblems(node: {
  colored_emblem?: CoaColoredEmblem | readonly CoaColoredEmblem[];
  colored_emblems?: readonly CoaColoredEmblem[];
}): CoaColoredEmblem[] {
  const candidates: readonly CoaColoredEmblem[] = [
    ...(Array.isArray(node.colored_emblem)
      ? node.colored_emblem
      : node.colored_emblem
      ? [node.colored_emblem as CoaColoredEmblem]
      : []),
    ...(node.colored_emblems ?? []),
  ];
  return candidates.filter(
    (e): e is CoaColoredEmblem =>
      !!e && typeof e === 'object' && typeof e.texture === 'string',
  );
}

/** A single tinted SVG <image> for a pattern texture. */
function PatternLayer({
  pattern,
  slots,
  x,
  y,
  width,
  height,
}: {
  pattern: string;
  slots: SlotColors;
  x: number;
  y: number;
  width: number;
  height: number;
}): React.JSX.Element {
  const tinted = useTintedTexture(assetUrl(pattern, 'patterns'), slots);
  return (
    <image
      href={tinted}
      x={x}
      y={y}
      width={width}
      height={height}
      preserveAspectRatio="none"
    />
  );
}

// audit F-36 / ck3_chronicler-4k3b: depth guard. CK3 quartered arms
// nest at most 3-4 levels in the wild; a malformed CoA blob with a
// cycle (sub.sub === sub) would otherwise blow the JS stack and brick
// the entire UI (the FE has no error boundary — see F-15). Cap at 8
// which is comfortably above any real CoA. Beyond the cap we render
// nothing instead of recursing.
const SUBSHIELD_MAX_DEPTH = 8;

/** Sub-shield layer: a nested CoA composition with its own pattern +
 * (optionally) charge, transformed per its instance.scale/offset. */
function SubShield({
  sub,
  palette,
  parentW,
  parentH,
  depth,
}: {
  sub: CoaDefinition;
  palette: Palette;
  parentW: number;
  parentH: number;
  depth: number;
}): React.JSX.Element | null {
  if (depth > SUBSHIELD_MAX_DEPTH) {
    return null;
  }
  // CK3 instance values are normalized [0, 1] within the parent.
  const [sx, sy] = sub.instance?.scale ?? [1, 1];
  const [ox, oy] = sub.instance?.offset ?? [0, 0];
  const tx = ox * parentW;
  const ty = oy * parentH;
  const w = parentW * sx;
  const h = parentH * sy;
  const subSlots = resolveSlots(sub, palette);

  return (
    <g transform={`translate(${tx} ${ty})`}>
      {sub.pattern && (
        <PatternLayer pattern={sub.pattern} slots={subSlots} x={0} y={0} width={w} height={h} />
      )}
      {sub.sub && (
        <SubShield
          sub={sub.sub}
          palette={palette}
          parentW={w}
          parentH={h}
          depth={depth + 1}
        />
      )}
      {sub.subs?.map((s, i) => (
        <SubShield
          key={i}
          sub={s}
          palette={palette}
          parentW={w}
          parentH={h}
          depth={depth + 1}
        />
      ))}
      {normalizeEmblems(sub).map((e, i) => (
        <Emblem key={i} emblem={e} palette={palette} parentW={w} parentH={h} />
      ))}
    </g>
  );
}

/** Charge: an emblem texture placed at (position) with (scale). */
function Emblem({
  emblem,
  palette,
  parentW,
  parentH,
}: {
  emblem: CoaColoredEmblem;
  palette: Palette;
  parentW: number;
  parentH: number;
}): React.JSX.Element {
  // Emblems author their primary shape in the BLUE channel (color1),
  // unlike patterns which use RED — so the resolved slots are rotated
  // before tinting. Without this, an emblem with an explicit color3
  // (e.g. ce_lion_guardant's color3="black") painted its body color3
  // instead of the color1 CK3 uses. ck3_chronicler-4da3.
  const tinted = useTintedTexture(
    assetUrl(emblem.texture, 'colored_emblems'),
    emblemSlots(resolveSlots(emblem, palette)),
  );
  // CK3 emblems can repeat the same texture at multiple positions on the
  // shield — Paradox source uses duplicate "instance={...}" entries which
  // rakaly's --duplicate-keys=group flag preserves as an array. The
  // Barcelona Senyera renders six vertical red bars this way: one
  // colored_emblem with six instances. Single object → wrap to a list of
  // one so the loop handles both shapes.
  const instances = Array.isArray(emblem.instance)
    ? emblem.instance
    : [emblem.instance ?? {}];
  return (
    <>
      {instances.map((inst, i) => {
        const [px, py] = inst?.position ?? [0.5, 0.5];
        // ck3_chronicler-3agj: CK3 stores horizontal/vertical mirroring
        // by sending negative scale (e.g. ce_bear_head with
        // scale=[-1.0, 1.0] mirrors the bear head left-to-right).
        // Plugging a negative value straight into SVG ``<image width>``
        // renders nothing — the spec treats it as an error and the
        // browser silently drops the image, which manifested as a
        // pure-red field with the charge invisible. Use the absolute
        // scale for width/height and apply a ``scale(±1)`` transform
        // around the emblem centre to flip the texture in place.
        const [rawSx, rawSy] = inst?.scale ?? [1, 1];
        const signX = rawSx < 0 ? -1 : 1;
        const signY = rawSy < 0 ? -1 : 1;
        const sx = Math.abs(rawSx);
        const sy = Math.abs(rawSy);
        const w = parentW * sx;
        const h = parentH * sy;
        const x = px * parentW - w / 2;
        const y = py * parentH - h / 2;
        // Non-uniform scale (e.g. a thin vertical bar at scale [0.058,
        // 1.0]) means the artist intended the texture to stretch to
        // fill its slot; uniform scale means a charge whose proportions
        // matter (leopard, cross, eagle) and should be letterboxed.
        const aspect = Math.abs(sx - sy) > 0.001 ? 'none' : 'xMidYMid meet';
        // CK3 rotation is in degrees around the emblem's centre. SVG's
        // rotate() is also degree-based but pivots around (0,0) by
        // default — pass the centre as the second/third args so the
        // texture spins in place rather than orbits the origin. Mirror
        // pivots around the same centre via the explicit
        // translate-scale-translate trick (``transform-origin`` on
        // individual SVG ``<image>`` elements isn't reliable across
        // browsers).
        const rot = inst?.rotation;
        const cx = px * parentW;
        const cy = py * parentH;
        const transformParts: string[] = [];
        if (rot !== undefined && rot !== 0) {
          transformParts.push(`rotate(${rot} ${cx} ${cy})`);
        }
        if (signX === -1 || signY === -1) {
          transformParts.push(
            `translate(${cx} ${cy}) scale(${signX} ${signY}) translate(${-cx} ${-cy})`,
          );
        }
        const transform =
          transformParts.length > 0 ? transformParts.join(' ') : undefined;
        return (
          <image
            key={i}
            href={tinted}
            x={x}
            y={y}
            width={w}
            height={h}
            preserveAspectRatio={aspect}
            transform={transform}
          />
        );
      })}
    </>
  );
}

/**
 * Drop-in fallback wrapper. When real CoA data is provided, renders
 * <RealHeraldry />; otherwise falls back to procedural <Heraldry />.
 * Pages use one component shape regardless of whether the character
 * has a persisted CoA yet.
 */
export interface HeraldryWithFallbackProps {
  coa: CoaDefinition | null | undefined;
  palette: Palette | null | undefined;
  /** Procedural-mode seed (string or numeric character id). */
  seed: string | number;
  size?: number;
  label?: string;
  ring?: boolean;
}

export function HeraldryWithFallback({
  coa,
  palette,
  seed,
  size,
  label,
  ring,
}: HeraldryWithFallbackProps): React.JSX.Element {
  if (coa && palette) {
    return <RealHeraldry coa={coa} palette={palette} size={size} label={label} ring={ring} />;
  }
  return <ProceduralHeraldry seed={seed} size={size} label={label} ring={ring} />;
}

/** SVG-positionable companion to {@link HeraldryWithFallback}. Same
 * semantics — render real CoA when both ``coa`` and ``palette`` are
 * present, else procedural — but returns the bare ``<svg>`` so it can
 * be nested inside a parent ``<svg>`` (LineagePage tree, future Hall
 * mosaic). ``x`` / ``y`` position within the parent viewBox.
 * (ck3_chronicler-h9u6 / 4y0v slice 2.1.)
 */
export function HeraldryWithFallbackSvg({
  coa,
  palette,
  seed,
  size,
  label,
  ring,
  x,
  y,
}: HeraldryWithFallbackProps & { x?: number; y?: number }): React.JSX.Element {
  if (coa && palette) {
    return (
      <RealHeraldrySvg
        coa={coa}
        palette={palette}
        size={size}
        label={label}
        ring={ring}
        x={x}
        y={y}
      />
    );
  }
  // The procedural HeraldryShieldSvg is already SVG-positionable.
  // Lazy-import to avoid pulling Heraldry into the RealHeraldry module
  // surface unnecessarily — the fallback path is only reached for
  // characters without a resolved CoA, and the bundle is shared.
  return (
    <HeraldryShieldSvgFallback
      seed={seed}
      size={size}
      label={label}
      ring={ring}
      x={x}
      y={y}
    />
  );
}


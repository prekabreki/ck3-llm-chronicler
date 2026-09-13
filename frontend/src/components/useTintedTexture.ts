/**
 * useTintedTexture — fetches a CK3 heraldry PNG and produces a recoloured
 * data-URL by replacing each pixel's slot-encoded value with the actual
 * palette colour for that slot (ck3_chronicler-7ao subsystem 3 v2).
 *
 * Why we need this: the extracted PNG textures store *which colour slot
 * each pixel uses* via channel-coding, not the final colours. The CK3
 * shader does the substitution at render time. We do the same in JS:
 *
 *   - R-only pixel  (R=255, G=0,   B=0)   → slot 1
 *   - R+G pixel     (R=255, G=255, B=0)   → slot 2
 *   - B pixel       (R=0,   G=0,   B>0)   → slot 3
 *   - alpha=0       → fully transparent
 *
 * For each layer the renderer passes a `[color1, color2, color3]` triple
 * (resolved RGB from the palette via the CoA's color name strings); the
 * hook returns a data-URL with substituted pixels.
 *
 * Cached by (textureUrl, slot1RGB, slot2RGB, slot3RGB) — same shield
 * rendered twice on a page hits the cache. Cache lives at module scope
 * so it survives component remounts.
 *
 * audit F-37 / ck3_chronicler-c716: cache capped via simple LRU
 * (Map iteration is insertion-ordered; touching a key deletes-then-
 * sets to move it to the end). Without the cap a long Codex scroll
 * of 1000+ tinted PNG data-URLs would balloon memory unboundedly.
 *
 * audit F-38 / ck3_chronicler-wpcm: the effect deps key only off the
 * stringified cache key. ``slots`` was a fresh object reference every
 * render (see resolveSlots in RealHeraldry.tsx), which retriggered
 * the effect 200x on every search keystroke even though the cache
 * already had the entry.
 *
 * audit M-F4 / ck3_chronicler-27ov.59: the returned value is derived
 * at render time from the module LRU instead of mirrored into state —
 * a cache hit on key change no longer needs a sync setState in the
 * effect (and its one-render stale flash), and the slots ref is gone
 * because the cache key fully encodes the slot colours.
 */

import { useEffect, useState } from 'react';

/** RGB triple, each 0-255. */
export type RGB = readonly [number, number, number];

/** Three slot colours. Any may be undefined if the texture doesn't use that slot. */
export interface SlotColors {
  color1?: RGB;
  color2?: RGB;
  color3?: RGB;
}

// (textureUrl|color1|color2|color3) → data URL. Bounded LRU.
const _CACHE_LIMIT = 256;
const _cache = new Map<string, string>();

function cacheGet(key: string): string | undefined {
  const value = _cache.get(key);
  if (value !== undefined) {
    // Touch: move to most-recently-used position.
    _cache.delete(key);
    _cache.set(key, value);
  }
  return value;
}

function cacheSet(key: string, value: string): void {
  if (_cache.has(key)) _cache.delete(key);
  _cache.set(key, value);
  while (_cache.size > _CACHE_LIMIT) {
    const oldest = _cache.keys().next().value;
    if (oldest === undefined) break;
    _cache.delete(oldest);
  }
}

function cacheKey(url: string, slots: SlotColors): string {
  const c1 = slots.color1?.join(',') ?? '_';
  const c2 = slots.color2?.join(',') ?? '_';
  const c3 = slots.color3?.join(',') ?? '_';
  return `${url}|${c1}|${c2}|${c3}`;
}

/**
 * Fetch + tint a heraldry texture. Returns a data-URL once ready, or
 * the original URL until the recolour has finished (so the SVG image
 * shows *something* immediately rather than blank).
 */
export function useTintedTexture(url: string, slots: SlotColors): string {
  const key = cacheKey(url, slots);
  // Re-render trigger for async recolours. The returned value is
  // derived at render time below — no state mirror of the module LRU.
  const [result, setResult] = useState<{ key: string; url: string } | null>(null);

  // F-38 intent, no ref needed: the cache key fully encodes url + the
  // three slot colours, so deps [key, url] can never run this effect
  // against stale slots — any slots change also changes key.
  useEffect(() => {
    if (cacheGet(key) !== undefined) return; // hit — render derives it
    let cancelled = false;
    void recolour(url, slots).then((dataUrl) => {
      if (cancelled) return;
      cacheSet(key, dataUrl);
      setResult({ key, url: dataUrl });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- slots is encoded in key
  }, [key, url]);

  // LRU first; the result state covers the (rare) window where the
  // entry was already evicted again. Until the recolour lands, show
  // the raw texture so the SVG image renders *something* immediately.
  return cacheGet(key) ?? (result?.key === key ? result.url : url);
}

/** Alpha threshold (0-255) for inclusion in the maxBOnly pre-pass. CK3
 * heraldry textures encode soft-edged shapes via low-alpha antialiasing
 * — some of those edge pixels read at B=255 in the browser canvas
 * (alpha=1, 2, ...) even when the actual ink body is at a lower B value
 * (B=128 for the kamon donut, B=156 for some leopard outline pixels).
 *
 * Counting those near-transparent edge pixels would drag maxBOnly up to
 * 255, halving the rendered intensity of the actual body. Gate the
 * pre-pass on alpha being meaningfully opaque (≥ half).
 * (ck3_chronicler-71x0.) */
const MAX_B_ALPHA_THRESHOLD = 128;

/** Compute the maximum B value among "B-only" pixels (R=0) whose alpha
 * passes the antialiasing-edge gate. Exported so a vitest can exercise
 * it directly — the full recolour() pipeline requires real canvas +
 * ImageData round-trip which jsdom doesn't support. */
export function computeMaxBOnly(data: Uint8ClampedArray | number[]): number {
  let maxBOnly = 0;
  for (let i = 0; i < data.length; i += 4) {
    if ((data[i + 3] ?? 0) < MAX_B_ALPHA_THRESHOLD) continue;
    if ((data[i] ?? 0) === 0 && (data[i + 2] ?? 0) > maxBOnly) {
      maxBOnly = data[i + 2] ?? 0;
    }
  }
  return maxBOnly;
}

/**
 * Recolour a CK3 heraldry texture by walking its pixels and substituting
 * each one's slot-encoded value with the actual palette RGB.
 *
 * Exported for direct use in tests; production code should call the
 * useTintedTexture hook which adds caching.
 */
export async function recolour(url: string, slots: SlotColors): Promise<string> {
  const img = await loadImage(url);
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas 2d context unavailable');
  ctx.drawImage(img, 0, 0);

  const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const data = imgData.data;

  // Pre-pass: find the maximum B value among B-only pixels (r=0, b>0).
  // Used to normalise per-pixel intensity so that:
  //   - Uniform-B emblems (head, hammer — one fixed B throughout) always
  //     render at full brightness: every pixel hits b/maxB = 1.0.
  //   - Variable-B emblems (mjolnir — B ranges 82–148) show relative
  //     engraving detail: brightest pixel = 1.0, others scale down from it.
  // Normalising by 255 instead would darken any emblem whose uniform B < 255.
  const maxBOnly = computeMaxBOnly(data);

  // Walk RGBA quadruples in place.
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] ?? 0;
    const g = data[i + 1] ?? 0;
    const b = data[i + 2] ?? 0;
    const a = data[i + 3] ?? 0;
    if (a === 0) continue; // already transparent — leave it alone

    let slotColor = pickSlot(r, g, b, slots);
    if (slotColor) {
      // For B-only pixels, apply relative intensity so engraving detail
      // in variable-B emblems survives tinting without darkening uniform ones.
      if (r === 0 && b > 0 && maxBOnly > 0 && b < maxBOnly) {
        const intensity = b / maxBOnly;
        slotColor = [
          Math.round(slotColor[0] * intensity),
          Math.round(slotColor[1] * intensity),
          Math.round(slotColor[2] * intensity),
        ];
      }
      data[i] = slotColor[0];
      data[i + 1] = slotColor[1];
      data[i + 2] = slotColor[2];
      // Use the texture's own alpha as the opacity signal — it already
      // encodes antialiasing for edge-softened emblems (leopard alphas
      // range 1..255). Earlier we modulated by max-channel intensity,
      // but that broke uniform-channel textures like ce_block_02 (B=127
      // everywhere, alpha=255 everywhere) which the shader treats as
      // fully opaque — not 50% transparent.
      data[i + 3] = a;
    } else {
      // No matching slot (rare — texture has unexpected encoding).
      // Make it transparent rather than leave the raw shader-input
      // colour visible.
      data[i + 3] = 0;
    }
  }
  ctx.putImageData(imgData, 0, 0);
  return canvas.toDataURL('image/png');
}

/**
 * Classify a pixel's (R, G, B) channels and return the matching slot's
 * RGB. Returns ``undefined`` only if the pixel is fully zero or no slot
 * colours at all are provided.
 *
 * Heuristic (validated against vanilla CK3 patterns + emblems):
 *   - R dominant, G == 0   → slot 1
 *   - R dominant, G > 0    → slot 2 (R+G encoding)
 *   - B dominant, R == 0   → slot 3
 *
 * "Dominant" means the channel is the largest non-zero value; this
 * handles soft-edged emblems where a leopard's outline has B values
 * around 128 (not 255) for antialiasing.
 *
 * If the matched slot's colour wasn't specified on the CoA (e.g.
 * ``ce_block_02`` is B-channel-encoded but the colored_emblem only sets
 * color1/color2), we fall back to the first available slot — color1
 * preferred, then color2, then color3. This matches the in-game CK3
 * behaviour where a single-channel texture renders in whatever colour
 * the CoA does specify, instead of vanishing into transparency.
 */
export function pickSlot(r: number, g: number, b: number, slots: SlotColors): RGB | undefined {
  if (r === 0 && g === 0 && b === 0) return undefined;
  // Determine which encoded slot dominates. Test in priority order
  // so R+G correctly maps to slot 2 (not slot 1).
  let preferred: RGB | undefined;
  if (r > 0 && r === g && g === b) {
    // R=G=B all equal and nonzero → slot 3. CK3 encodes the third colour
    // layer in patterns (e.g. the red inner cross on pattern_cross_02) as
    // equal channels (255,255,255). Distinct from the dominant-channel
    // mixed fallback below where channels are unequal.
    preferred = slots.color3;
  } else if (r > 0 && g > 0 && b === 0) {
    preferred = slots.color2;
  } else if (r > 0 && g === 0 && b === 0) {
    preferred = slots.color1;
  } else if (b > 0 && r === 0) {
    preferred = slots.color3;
  } else if (r >= g && r >= b) {
    // Mixed cases: pick the dominant channel.
    preferred = slots.color1;
  } else if (g >= r && g >= b) {
    preferred = slots.color2;
  } else {
    preferred = slots.color3;
  }
  return preferred ?? slots.color1 ?? slots.color2 ?? slots.color3;
}

/**
 * Rotate a resolved {@link SlotColors} for EMBLEM textures.
 *
 * Patterns encode their primary region in the RED channel (→ color1) and a
 * secondary in GREEN (R+G → color2). Emblems use the opposite convention:
 * their primary shape is authored in the BLUE channel — 1556 of 1585
 * vanilla colored_emblems are blue-only. Because the shared {@link pickSlot}
 * maps B→slot3, feeding the resolved colours straight through made an
 * emblem's primary body resolve to color3: ce_lion_guardant carries an
 * explicit color3="black", so the lion rendered black where CK3 itself
 * paints it color1 (golden yellow). The bug stayed hidden for ~95% of
 * emblems because color3 is usually absent and pickSlot falls back to
 * color1 — it only surfaced when color3 was explicitly set.
 *
 * Rotate so the slot each channel lands in carries the emblem's intended
 * colour: blue(slot3)→color1, red(slot1)→color2, R+G(slot2)→color3.
 * ck3_chronicler-4da3.
 */
export function emblemSlots(slots: SlotColors): SlotColors {
  return { color1: slots.color2, color2: slots.color3, color3: slots.color1 };
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load ${url}`));
    img.src = url;
  });
}

/** Test-only: clear the cache between test runs. */
export function _clearTintedTextureCache(): void {
  _cache.clear();
}

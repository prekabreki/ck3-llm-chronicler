import { describe, expect, it } from 'vitest';
import { computeMaxBOnly, emblemSlots, pickSlot } from './useTintedTexture';

// `recolour()` and `useTintedTexture()` themselves require a real
// HTMLImageElement + canvas, which happy-dom partially supports but
// not for ImageData round-trips. The pure classification logic in
// pickSlot is the load-bearing piece — verify it covers every encoding
// we've observed in vanilla CK3 textures.

describe('pickSlot — pixel → slot classification', () => {
  const slots = {
    color1: [10, 20, 30] as const, // distinguishable
    color2: [100, 110, 120] as const,
    color3: [200, 210, 220] as const,
  };

  it('R-only pixel → slot 1 (pattern_solid encoding)', () => {
    expect(pickSlot(255, 0, 0, slots)).toEqual([10, 20, 30]);
  });

  it('R+G pixel → slot 2 (pattern_vertical_split_01 right half encoding)', () => {
    expect(pickSlot(255, 255, 0, slots)).toEqual([100, 110, 120]);
  });

  it('B-only pixel full intensity → slot 3 at full brightness', () => {
    expect(pickSlot(0, 0, 255, slots)).toEqual([200, 210, 220]);
  });

  it('partial-B pixel → slot 3 at full brightness (pickSlot has no intensity logic)', () => {
    // pickSlot is a pure classifier — it maps channels to slot colours at full
    // intensity. Relative B-intensity (for engraving detail in variable-B
    // emblems like ce_norse_mjolnir_odeshog) is applied by recolour() using
    // b/maxB normalisation so uniform-B emblems (heads, hammers, ce_block_02)
    // always render at full brightness.
    expect(pickSlot(0, 0, 128, slots)).toEqual([200, 210, 220]);
    expect(pickSlot(0, 0, 82, slots)).toEqual([200, 210, 220]);
  });

  it('all-zero pixel → undefined (transparent / unused)', () => {
    expect(pickSlot(0, 0, 0, slots)).toBeUndefined();
  });

  it('falls back to the first available slot when the matched slot is missing', () => {
    // Real-world case: ce_block_02 is B-channel-only but the Barcelona
    // dynasty CoA specifies only color1 + color2. In-game CK3 still tints
    // the block with the available colour rather than dropping the layer
    // — so we fall back to color1 (then color2 → color3) to match.
    expect(pickSlot(255, 0, 0, { color2: [1, 2, 3] })).toEqual([1, 2, 3]);
    expect(pickSlot(0, 0, 255, { color1: [1, 2, 3] })).toEqual([1, 2, 3]);
    // ce_block_02 has B=127 everywhere — must return full color1 unscaled.
    expect(pickSlot(0, 0, 127, { color1: [1, 2, 3] })).toEqual([1, 2, 3]);
    expect(pickSlot(0, 0, 255, { color1: [9, 9, 9], color2: [1, 2, 3] })).toEqual([9, 9, 9]);
  });

  it('returns undefined when no slot colours at all are provided', () => {
    expect(pickSlot(255, 0, 0, {})).toBeUndefined();
    expect(pickSlot(0, 0, 255, {})).toBeUndefined();
  });

  it('mixed-channel pixel → dominant channel wins (rare-encoding fallback)', () => {
    // R=200, G=50, B=10 — R clearly dominant
    expect(pickSlot(200, 50, 10, slots)).toEqual([10, 20, 30]);
    // R=50, G=200, B=10 — G dominant
    expect(pickSlot(50, 200, 10, slots)).toEqual([100, 110, 120]);
    // R=10, G=50, B=200 — B dominant
    expect(pickSlot(10, 50, 200, slots)).toEqual([200, 210, 220]);
  });

  it('R=G=B all equal nonzero → slot 3 (pattern_cross_02 red cross encoding)', () => {
    // CK3 encodes the third colour layer (e.g. the red inner cross on
    // pattern_cross_02) as equal channels (255,255,255). Previously
    // mis-classified as slot 1 via dominant-channel fallback.
    expect(pickSlot(255, 255, 255, slots)).toEqual([200, 210, 220]);
    expect(pickSlot(100, 100, 100, slots)).toEqual([200, 210, 220]);
  });
});

describe('computeMaxBOnly — antialiasing-edge gate (ck3_chronicler-71x0)', () => {
  // Pack (r,g,b,a) quadruples into a flat array, the shape recolour()
  // sees from ctx.getImageData().data.
  const px = (...pixels: Array<[number, number, number, number]>): number[] =>
    pixels.flatMap(([r, g, b, a]) => [r, g, b, a]);

  it('returns the highest B among r=0, alpha-opaque pixels', () => {
    expect(
      computeMaxBOnly(px([0, 0, 80, 255], [0, 0, 128, 255], [0, 0, 100, 255])),
    ).toBe(128);
  });

  it('ignores near-transparent antialiasing edges that read at B=255', () => {
    // The kamon donut: main ring B=128 alpha=255, edge antialiasing
    // B=255 alpha=1. Pre-fix the alpha=1 pixel dragged maxBOnly to 255,
    // halving the rendered intensity of the entire ring. Gate rejects.
    expect(
      computeMaxBOnly(
        px([0, 0, 128, 255], [0, 0, 128, 255], [0, 0, 255, 1], [0, 0, 255, 2]),
      ),
    ).toBe(128);
  });

  it('counts mid-opacity pixels (alpha >= threshold) as ink', () => {
    // A softly anti-aliased emblem can have legitimate body pixels at
    // alpha well below 255 — those still inform maxBOnly.
    expect(computeMaxBOnly(px([0, 0, 150, 128], [0, 0, 100, 255]))).toBe(150);
    // Just below the threshold: excluded.
    expect(computeMaxBOnly(px([0, 0, 200, 127], [0, 0, 100, 255]))).toBe(100);
  });

  it('skips pixels with nonzero R (those use slot 1 or 2, not slot 3)', () => {
    expect(
      computeMaxBOnly(px([255, 0, 200, 255], [0, 0, 128, 255])),
    ).toBe(128);
  });

  it('returns 0 when no B-only opaque pixels exist', () => {
    expect(computeMaxBOnly(px([255, 0, 0, 255], [255, 255, 0, 255]))).toBe(0);
    expect(computeMaxBOnly([])).toBe(0);
  });
});

describe('emblemSlots — emblem channel convention (blue = primary/color1)', () => {
  // Patterns encode their primary region in the RED channel; emblems encode
  // it in the BLUE channel (1556/1585 vanilla emblems are blue-only). The
  // shared pickSlot maps B→slot3, so feeding resolved colours straight
  // through made an emblem's primary body resolve to color3 — e.g.
  // ce_lion_guardant with an explicit color3="black" rendered a black lion
  // that CK3 itself paints color1 (golden yellow). Rotating realigns each
  // channel's slot to the emblem's intended colour. ck3_chronicler-4da3.
  const yellow = [191, 134, 48] as const;
  const red = [115, 34, 23] as const;
  const black = [26, 23, 19] as const;

  it('blue (emblem primary) resolves to color1, not color3', () => {
    const slots = emblemSlots({ color1: yellow, color2: yellow, color3: black });
    // The lion body is a blue-channel pixel (0, 0, 126).
    expect(pickSlot(0, 0, 126, slots)).toEqual(yellow);
  });

  it('red (emblem secondary) resolves to color2', () => {
    const slots = emblemSlots({ color1: yellow, color2: red, color3: black });
    expect(pickSlot(255, 0, 0, slots)).toEqual(red);
  });

  it('blue-only emblem with color3 absent still renders color1', () => {
    const slots = emblemSlots({ color1: yellow });
    expect(pickSlot(0, 0, 126, slots)).toEqual(yellow);
  });
});

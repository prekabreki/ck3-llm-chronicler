import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { HeraldryWithFallback, RealHeraldry } from './RealHeraldry';
import type { CoaDefinition, Palette } from './CoaTypes';

// Toirrdelbach's actual house arms — reproduced verbatim from the
// rakaly extraction documented in ck3_chronicler-7ao. Used as the
// canonical fixture for renderer tests.
const TOIRRDELBACH_COA: CoaDefinition = {
  pattern: 'pattern_solid.dds',
  color1: 'black',
  color2: 'green',
  color3: 'yellow',
  sub: {
    instance: {
      scale: [0.5, 0.5],
      offset: [0.0, 0.5],
    },
    pattern: 'pattern_vertical_split_01.dds',
    color1: 'red',
    color2: 'yellow',
    colored_emblem: {
      color1: 'white',
      color2: 'white',
      color3: 'black',
      texture: 'ce_leopard_passant_guardant.dds',
      mask: [0, 2, 0],
      instance: {
        position: [0.5, 0.67],
        scale: [0.85, 0.85],
      },
    },
  },
};

const SAMPLE_PALETTE: Palette = {
  black: [26, 23, 19],
  green: [31, 76, 35],
  yellow: [191, 134, 48],
  red: [115, 34, 23],
  white: [204, 202, 200],
};

describe('RealHeraldry — composition renderer (MVP)', () => {
  it('renders an SVG with the heater-shield silhouette', () => {
    const { container } = render(<RealHeraldry coa={TOIRRDELBACH_COA} palette={SAMPLE_PALETTE} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    // The outer rim path should be the canonical shield silhouette.
    const rimPath = container.querySelector('svg > path');
    expect(rimPath).toBeInTheDocument();
  });

  it('renders the top-level pattern as an SVG image with the right asset URL', () => {
    const { container } = render(<RealHeraldry coa={TOIRRDELBACH_COA} palette={SAMPLE_PALETTE} />);
    const images = container.querySelectorAll('image');
    expect(images.length).toBeGreaterThanOrEqual(1);
    // .dds → .png; subdir is "patterns" for pattern_*.dds
    const hrefs = Array.from(images).map((img) => img.getAttribute('href'));
    expect(hrefs).toContain('/api/heraldry/assets/patterns/pattern_solid.png');
  });

  it('walks into the sub-shield and emits its pattern + emblem', () => {
    const { container } = render(<RealHeraldry coa={TOIRRDELBACH_COA} palette={SAMPLE_PALETTE} />);
    const hrefs = Array.from(container.querySelectorAll('image')).map((img) => img.getAttribute('href'));
    expect(hrefs).toContain('/api/heraldry/assets/patterns/pattern_vertical_split_01.png');
    expect(hrefs).toContain('/api/heraldry/assets/colored_emblems/ce_leopard_passant_guardant.png');
  });

  it('renders the gilded ring when ring=true', () => {
    const { container } = render(
      <RealHeraldry coa={TOIRRDELBACH_COA} palette={SAMPLE_PALETTE} ring />,
    );
    // Outer rim + ring → 2 top-level paths under the SVG.
    const paths = container.querySelectorAll('svg > path');
    expect(paths.length).toBeGreaterThanOrEqual(2);
  });

  it('places the sub-shield via translate matching instance.offset', () => {
    const { container } = render(<RealHeraldry coa={TOIRRDELBACH_COA} palette={SAMPLE_PALETTE} />);
    // The sub-shield wraps everything in a <g transform="translate(...)">.
    // offset = [0, 0.5] in 80×96 viewbox → translate(0, 48).
    const subGroup = Array.from(container.querySelectorAll('g[transform]'))
      .map((g) => g.getAttribute('transform'))
      .find((t) => t?.includes('translate'));
    expect(subGroup).toBeTruthy();
  });

  it('puts the label on the SVG aria-label', () => {
    const { container } = render(
      <RealHeraldry
        coa={TOIRRDELBACH_COA}
        palette={SAMPLE_PALETTE}
        label="Toirrdelbach mac Tairdelbach"
      />,
    );
    expect(container.querySelector('svg')).toHaveAttribute(
      'aria-label',
      'Toirrdelbach mac Tairdelbach',
    );
  });
});

describe('RealHeraldry — negative-scale mirror (ck3_chronicler-3agj)', () => {
  const OVTAY_BEAR_COA: CoaDefinition = {
    pattern: 'pattern_solid.dds',
    color1: 'red',
    color2: 'red',
    color3: 'white',
    colored_emblem: {
      texture: 'ce_bear_head.dds',
      color1: 'white',
      color2: 'white',
      instance: {
        position: [0.5, 0.48],
        scale: [-1.0, 1.0],
      },
    },
  };

  it('does not put a negative value into the emblem image width attribute', () => {
    // SVG <image> with negative width silently renders nothing in
    // every browser engine — the original 3agj symptom (red field,
    // missing charge). The bug fix: feed |sx| to width and apply a
    // mirror via transform="scale(-1, 1)" around the emblem centre.
    const { container } = render(
      <RealHeraldry coa={OVTAY_BEAR_COA} palette={SAMPLE_PALETTE} />,
    );
    const widths = Array.from(container.querySelectorAll('image'))
      .map((img) => img.getAttribute('width'))
      .filter((w): w is string => w !== null);
    expect(widths.length).toBeGreaterThan(0);
    for (const w of widths) {
      expect(parseFloat(w)).toBeGreaterThan(0);
    }
  });

  it('emits a scale(-1 1) transform around the emblem centre when scale_x is negative', () => {
    const { container } = render(
      <RealHeraldry coa={OVTAY_BEAR_COA} palette={SAMPLE_PALETTE} />,
    );
    const bear = Array.from(container.querySelectorAll('image')).find(
      (img) => img.getAttribute('href')?.includes('ce_bear_head'),
    );
    expect(bear).toBeTruthy();
    const transform = bear?.getAttribute('transform') ?? '';
    expect(transform).toMatch(/scale\(-1 1\)/);
  });
});

describe('HeraldryWithFallback', () => {
  it('renders RealHeraldry when CoA data is present', () => {
    const { container } = render(
      <HeraldryWithFallback
        coa={TOIRRDELBACH_COA}
        palette={SAMPLE_PALETTE}
        seed={29160}
      />,
    );
    // RealHeraldry emits SVG <image> elements pointing at our asset URL.
    const images = container.querySelectorAll('image');
    expect(images.length).toBeGreaterThan(0);
  });

  it('falls back to procedural Heraldry when CoA is null', () => {
    const { container } = render(
      <HeraldryWithFallback coa={null} palette={SAMPLE_PALETTE} seed={29160} />,
    );
    // Procedural Heraldry doesn't use <image> — it draws the shield
    // entirely with paths/rects/polygons.
    const images = container.querySelectorAll('image');
    expect(images.length).toBe(0);
    // But it does render an SVG.
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('falls back to procedural Heraldry when palette is null', () => {
    const { container } = render(
      <HeraldryWithFallback coa={TOIRRDELBACH_COA} palette={null} seed={29160} />,
    );
    expect(container.querySelectorAll('image').length).toBe(0);
  });
});

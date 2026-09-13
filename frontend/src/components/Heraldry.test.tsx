import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Banner, Heraldry } from './Heraldry';

// F-55: one canary snapshot (seed=12267 / Ælla 'the Impaler' — the
// live-session character) locks the deterministic SVG output against
// silent breakage by future refactors or the real-CK3-CoA renderer
// (ck3_chronicler-7ao). Other seeds get structural assertions
// (clip-path + background rect + at least one charge group) — those
// catch shape regressions without bloating the snap file with redundant
// 80-line SVG dumps that all assert the same property in different
// disguises.

describe('Heraldry — procedural shield', () => {
  it('renders a deterministic snapshot for the canary seed (Ælla, 12267)', () => {
    const { container } = render(
      <Heraldry seed={12267} size={80} label="shield-12267" />,
    );
    expect(container.querySelector('svg')).toBeInTheDocument();
    expect(container.firstChild).toMatchSnapshot();
  });

  // Other seeds (Toirrdelbach 29160, Eadmund 36892, 'unknown' fallback,
  // numeric 0 edge) — structural shape, not byte-exact.
  const STRUCT_SEEDS: Array<string | number> = [29160, 36892, 'unknown', 0];

  it.each(STRUCT_SEEDS)('renders a structurally-valid shield for seed %s', (seed) => {
    const { container } = render(<Heraldry seed={seed} size={80} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute('viewBox', '0 0 80 96');
    // Background fill rect inside the clipped group — every shield has
    // a field colour even when no charges land on it.
    expect(svg?.querySelector('g[clip-path] > rect')).toBeInTheDocument();
    // Outer rim path (the "g[clip-path]" group + the rim path are the
    // two fixed components; charges layer on top when the seed picks any).
    expect(svg?.querySelector('path')).toBeInTheDocument();
  });

  it('produces identical output for the same seed across renders', () => {
    const a = render(<Heraldry seed="Toirrdelbach" />);
    const b = render(<Heraldry seed="Toirrdelbach" />);
    expect(a.container.innerHTML).toBe(b.container.innerHTML);
  });

  it('produces different output for different seeds', () => {
    const a = render(<Heraldry seed="Toirrdelbach" />);
    const b = render(<Heraldry seed="Ælla" />);
    expect(a.container.innerHTML).not.toBe(b.container.innerHTML);
  });

  it('passes through int seeds without hashing', () => {
    // Numeric 12267 should NOT be hashed via fnv1a; it's used directly.
    // Easiest assertion: numeric and string-of-the-number must differ.
    const numeric = render(<Heraldry seed={12267} />);
    const strNum = render(<Heraldry seed="12267" />);
    expect(numeric.container.innerHTML).not.toBe(strNum.container.innerHTML);
  });

  it('renders the optional gilded ring when ring=true', () => {
    const { container } = render(<Heraldry seed={1} ring />);
    const paths = container.querySelectorAll('svg > path');
    // With ring=true: outer rim (1) + ring (1) = at least 2 top-level paths
    expect(paths.length).toBeGreaterThanOrEqual(2);
  });

  it('omits the ring when ring is omitted', () => {
    const { container } = render(<Heraldry seed={1} />);
    const paths = container.querySelectorAll('svg > path');
    expect(paths.length).toBe(1); // just the outer rim
  });

  it('respects a custom size', () => {
    const { container } = render(<Heraldry seed="x" size={120} />);
    const span = container.querySelector('span.shield');
    expect(span).toHaveStyle({ width: '120px' });
  });

  it('puts the label on the SVG aria-label', () => {
    const { container } = render(<Heraldry seed="x" label="Eadmund's shield" />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-label', "Eadmund's shield");
  });
});

describe('Banner — tiny campaign flag', () => {
  it('renders an SVG with the right viewBox', () => {
    const { container } = render(<Banner seed="House of Wærhelm" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('viewBox', '0 0 22 26');
  });

  it('is deterministic across renders', () => {
    const a = render(<Banner seed="House of Wærhelm" />);
    const b = render(<Banner seed="House of Wærhelm" />);
    expect(a.container.innerHTML).toBe(b.container.innerHTML);
  });
});

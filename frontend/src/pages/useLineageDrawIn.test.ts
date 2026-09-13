import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const animate = vi.hoisted(() => vi.fn());
const createDrawable = vi.hoisted(() => vi.fn((els: unknown) => els));
const stagger = vi.hoisted(() => vi.fn((n: number) => n));

vi.mock('animejs', () => ({
  animate,
  stagger,
  svg: { createDrawable },
}));

import { useLineageDrawIn, MAX_STAGGER_TOTAL_MS, EDGE_STAGGER_MS } from './useLineageDrawIn';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** A stand-in for what LineagePage renders: N stem paths and N node groups. */
function buildTree(edges: number, nodes: number): SVGSVGElement {
  const root = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
  for (let i = 0; i < edges; i++) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('class', 'lineage-svg__edge');
    root.appendChild(p);
  }
  for (let i = 0; i < nodes; i++) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'lineage-node');
    root.appendChild(g);
  }
  document.body.appendChild(root);
  return root;
}

function ref(el: SVGSVGElement | null) {
  return { current: el } as React.RefObject<SVGSVGElement | null>;
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} })));
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('useLineageDrawIn', () => {
  it('draws the stems and settles the nodes', () => {
    const root = buildTree(3, 4);
    renderHook(() => useLineageDrawIn(ref(root), { key: 12 }));
    expect(animate).toHaveBeenCalledTimes(2);
    expect(createDrawable).toHaveBeenCalledTimes(1);
    // Indexed access is checked in this tsconfig, so the call has to be proven to
    // exist before it is destructured.
    const edgeCall = animate.mock.calls[0];
    expect(edgeCall).toBeDefined();
    expect(edgeCall![1].draw).toEqual(['0 0', '0 1']);
  });

  it('animates nothing when reduced motion is requested', () => {
    // The whole point of the accessibility pass: the tree must still be there,
    // just static. `disabled` and the OS setting are two routes to the same skip.
    const root = buildTree(3, 4);
    renderHook(() => useLineageDrawIn(ref(root), { key: 12, disabled: true }));
    expect(animate).not.toHaveBeenCalled();
  });

  it('animates nothing when the OS asks for reduced motion', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {} })));
    const root = buildTree(3, 4);
    renderHook(() => useLineageDrawIn(ref(root), { key: 12 }));
    expect(animate).not.toHaveBeenCalled();
  });

  it('does nothing while the tree is still loading', () => {
    // key === null is the loading render. Running then would animate an SVG that
    // is not in the DOM yet, and would burn the one draw-in on nothing.
    renderHook(() => useLineageDrawIn(ref(null), { key: null }));
    expect(animate).not.toHaveBeenCalled();
  });

  it('re-inks when the root character changes', () => {
    // One ref for the life of the harness, the way useRef behaves in the page. A
    // fresh ref object per render would change the effect's own dependency and
    // re-run it every time, which would hide the thing this pair of tests checks.
    const stable = ref(buildTree(2, 2));
    const { rerender } = renderHook(
      ({ k }: { k: number }) => useLineageDrawIn(stable, { key: k }),
      { initialProps: { k: 1 } },
    );
    expect(animate).toHaveBeenCalledTimes(2);
    rerender({ k: 2 });
    expect(animate).toHaveBeenCalledTimes(4);
  });

  it('does not re-ink when nothing about the tree changed', () => {
    const stable = ref(buildTree(2, 2));
    const { rerender } = renderHook(
      ({ k }: { k: number }) => useLineageDrawIn(stable, { key: k }),
      { initialProps: { k: 1 } },
    );
    rerender({ k: 1 });
    expect(animate).toHaveBeenCalledTimes(2);
  });

  it('compresses the stagger so a large dynasty does not crawl', () => {
    const root = buildTree(300, 300);
    renderHook(() => useLineageDrawIn(ref(root), { key: 1 }));
    // 300 edges at the uncompressed 18ms each would be 5.4s of stagger alone.
    // toBeCloseTo, not a bare <=: MAX/(n-1)*(n-1) lands on 900.0000000000001 in
    // binary floating point, and a test that fails by 1e-13 is testing IEEE 754.
    const staggerCall = stagger.mock.calls[0];
    expect(staggerCall).toBeDefined();
    const perEdge = staggerCall![0] as number;
    expect(perEdge).toBeLessThan(EDGE_STAGGER_MS);
    expect(perEdge * 299).toBeCloseTo(MAX_STAGGER_TOTAL_MS, 6);
  });

  it('survives an empty tree', () => {
    const root = buildTree(0, 0);
    expect(() =>
      renderHook(() => useLineageDrawIn(ref(root), { key: 1 })),
    ).not.toThrow();
    expect(animate).not.toHaveBeenCalled();
  });

  it('leaves the tree standing when the animation library throws', () => {
    // A failed flourish must never cost the user the page.
    animate.mockImplementationOnce(() => { throw new Error('boom'); });
    const root = buildTree(2, 2);
    expect(() =>
      renderHook(() => useLineageDrawIn(ref(root), { key: 1 })),
    ).not.toThrow();
    expect(root.querySelectorAll('.lineage-node')).toHaveLength(2);
  });
});

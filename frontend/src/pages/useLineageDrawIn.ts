// The lineage tree inks itself in: stems draw along their own paths, then the nodes
// settle onto them. The one screen in the app where motion says something true, a
// dynasty extending rather than a list appearing, so it is the one screen that gets
// a scripted animation rather than a CSS transition.
//
// Kept out of LineagePage.tsx so that file stays a render component and this can be
// tested on its own. It talks to the DOM through the passed-in ref only, and every
// element it touches is one the page already renders.

import { useEffect } from 'react';
import { animate, svg, stagger } from 'animejs';

import { prefersReducedMotion } from '../util/motion';

// Long enough to read as ink travelling, short enough that a deep dynasty is not a
// loading screen. The stagger is per-edge, so total time grows with the tree; the
// cap keeps a 200-node dynasty from taking a minute to settle.
export const EDGE_DURATION_MS = 520;
export const EDGE_STAGGER_MS = 18;
export const NODE_DURATION_MS = 420;
export const MAX_STAGGER_TOTAL_MS = 900;

export interface LineageDrawInOptions {
  /** Re-run when this changes: a new root means a genuinely new tree to ink in. */
  key: string | number | null;
  /** Skip the animation (already true when the OS asks for reduced motion). */
  disabled?: boolean;
}

/** Animate the tree inside `root` on mount and whenever `key` changes. */
export function useLineageDrawIn(
  root: React.RefObject<SVGSVGElement | null>,
  { key, disabled = false }: LineageDrawInOptions,
): void {
  useEffect(() => {
    const el = root.current;
    if (!el || key === null) return;

    const edges = Array.from(
      el.querySelectorAll<SVGGeometryElement>('.lineage-svg__edge, .lineage-svg__marriage'),
    );
    const nodes = Array.from(el.querySelectorAll<SVGGElement>('.lineage-node'));

    // Reduced motion, or a browser with no animation support: leave the tree exactly
    // as rendered. Nothing to undo, because the visible state IS the rendered state
    // and the animation only ever departs from it temporarily.
    if (disabled || prefersReducedMotion()) return;

    // Guard the whole thing: an animation failing must never cost the user the tree
    // itself. anime.js touching an element mid-unmount is the realistic case.
    try {
      // Per-edge stagger, compressed so a large tree does not crawl.
      const edgeStagger = edges.length > 1
        ? Math.min(EDGE_STAGGER_MS, MAX_STAGGER_TOTAL_MS / (edges.length - 1))
        : 0;

      if (edges.length) {
        animate(svg.createDrawable(edges), {
          draw: ['0 0', '0 1'],
          duration: EDGE_DURATION_MS,
          delay: stagger(edgeStagger),
          ease: 'inOut(2)',
        });
      }

      if (nodes.length) {
        const nodeStagger = nodes.length > 1
          ? Math.min(EDGE_STAGGER_MS, MAX_STAGGER_TOTAL_MS / (nodes.length - 1))
          : 0;
        animate(nodes, {
          opacity: [0, 1],
          duration: NODE_DURATION_MS,
          // Nodes follow their stems rather than racing them.
          delay: stagger(nodeStagger, { start: EDGE_DURATION_MS * 0.45 }),
          ease: 'out(2)',
        });
      }
    } catch {
      // Fall through to the static tree. Deliberately silent: a failed flourish is
      // not something to put in the user's console on a page that rendered fine.
    }
  }, [root, key, disabled]);
}

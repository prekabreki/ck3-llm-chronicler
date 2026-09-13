// LineagePage — multi-generational family tree centred on the selected
// character. Port of design/tree-tracked-pages.jsx (TreePage section).
// Renamed from StemmaPage 2026-05-07 (ck3_chronicler-eyot).
//
// Layout: depth = row. Depth-N ancestors at the top, the seed at row 0,
// depth-N descendants below. Spouses rendered to the right of the seed
// at row 0, siblings to the left. Edges drawn from each character's
// known parent (when both are positioned) and a dashed marriage line
// for the seed↔spouse pair.
//
// Clicking a node sets it as the selected character and routes to the
// Chronicle.

import { useMemo, useRef } from 'react';

import { HeraldryWithFallbackSvg } from '../components/RealHeraldry';
import { useLineageDrawIn } from './useLineageDrawIn';
import { useReducedMotion } from '../util/motion';
import { useCampaign, useFamilyTree, usePalette } from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { FamilyTreeNode, FamilyTreeResponse } from '../api/types';

// vysp.9: 220×56 nodes per the brief.
const COL_W = 260;
const ROW_H = 130;
const NODE_W = 220;
const NODE_H = 56;
const PAD_X = 60;
const PAD_Y = 60;

interface PositionedNode extends FamilyTreeNode {
  x: number;
  y: number;
  generation: number; // ancestors negative, seed 0, descendants positive
  focus: boolean;
  parentIds?: number[];
}

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign. The
// no-player guard below stays — it's a distinct routing concern.
export function LineagePage({ campaignName }: { campaignName: string }): React.JSX.Element {
  const setView = useAppStore((s) => s.setView);
  const selectedCharacterId = useAppStore((s) => s.selectedCharacterId);
  // ck3_chronicler (2026-05-09): when no character is explicitly chosen
  // (fresh load, navigated here from the campaign-overview nav, etc.)
  // default the lineage root to the campaign's current player. Forcing
  // the user back to the Codex to pick a seed when the obvious default
  // is right there was a UX seam the user surfaced 2026-05-09.
  const campaignQ = useCampaign(campaignName, false);
  const fallbackPlayerId =
    campaignQ.data?.current_player_character_id ?? null;
  const rootCk3Id = selectedCharacterId ?? fallbackPlayerId;

  if (rootCk3Id === null) {
    // Final fallback: campaign has no pinned player AND the user hasn't
    // chosen one. This is the rare pre-tail-tick / pre-import state.
    return (
      <div className="lineage-page lineage-page--empty">
        <p className="italic-fell">
          {campaignQ.isLoading
            ? 'Reading the campaign…'
            : (
              <>
                No player resolved for this campaign yet. Return to the{' '}
                <button
                  type="button"
                  className="link"
                  onClick={() => setView('codex')}
                >
                  Codex
                </button>{' '}
                and pick a soul to root the lineage.
              </>
            )}
        </p>
      </div>
    );
  }

  return (
    <LineagePageBody
      campaignName={campaignName}
      ck3Id={rootCk3Id}
    />
  );
}

interface LineagePageBodyProps {
  campaignName: string;
  ck3Id: number;
}

function LineagePageBody({
  campaignName,
  ck3Id,
}: LineagePageBodyProps): React.JSX.Element {
  // ck3_chronicler-32ks: 5+5 default depth (BE supports up to 10+10).
  const treeQ = useFamilyTree(campaignName, ck3Id, 5, 5);
  // ck3_chronicler-h9u6 (4y0v slice 2.1): one palette fetch per page
  // — every node shares it through the SVG-positionable
  // HeraldryWithFallbackSvg. Procedural fallback kicks in when the
  // palette load fails (network) or the per-node coa_json is null.
  const { data: palette } = usePalette();
  const setSelected = useAppStore((s) => s.setSelectedCharacterId);
  const setView = useAppStore((s) => s.setView);

  const { positioned, edges, marriageLines, viewW, viewH, byId } =
    useMemo(() => layoutTree(treeQ.data ?? null), [treeQ.data]);

  // Both hooks sit above every early return, because a hook that some renders
  // skip is a hook order violation. The draw-in keys on "a tree is on screen":
  // null while loading, so the effect re-runs on the render that actually mounts
  // the SVG, and the ck3 id after that, so choosing a new root re-inks but a
  // background refetch of the same tree does not.
  const svgRef = useRef<SVGSVGElement | null>(null);
  const reducedMotion = useReducedMotion();
  useLineageDrawIn(svgRef, {
    key: treeQ.data ? ck3Id : null,
    disabled: reducedMotion,
  });

  if (treeQ.isLoading) {
    return (
      <div className="lineage-page lineage-page--empty">
        <p className="italic-fell">Drawing the boughs…</p>
      </div>
    );
  }

  if (treeQ.isError || !treeQ.data) {
    return (
      <div className="lineage-page lineage-page--empty">
        <p
          className="italic-fell lineage-page__error"
          role="alert"
        >
          Could not draw the lineage.
        </p>
      </div>
    );
  }

  const seed = treeQ.data.self_node;
  const onOpen = (n: FamilyTreeNode): void => {
    setSelected(n.ck3_id);
    setView('chronicle');
  };

  return (
    <div className="lineage-page">
      <div className="lineage-page__inner">
        <header className="lineage-page__header">
          <div className="smallcaps lineage-page__eyebrow">
            The dynastic boughs
          </div>
          <h1 className="uncial lineage-page__title">
            {seed.first_name
              ? `The Kin of ${seed.first_name}`
              : `The Kin of #${seed.ck3_id}`}
          </h1>
          <p className="italic-fell lineage-page__lede">
            Up to five generations of ancestors and descendants, with
            spouses and siblings drawn alongside. Click any soul to
            re-root the chronicle on them.
          </p>
        </header>

        <div className="paper paper--edged lineage-page__paper">
          {positioned.length === 1 && (
            <p className="italic-fell lineage-page__pending">
              No kin recorded for this soul yet — the family graph is
              persisted from save data when the character is tracked
              (or imported).
            </p>
          )}
          {/* ck3_chronicler-32ks: render the SVG at its natural pixel
              size so 5+5-depth trees stay legible. Parent paper has
              overflow:auto, so the page scrolls horizontally when the
              tree is wider than the viewport. Was width="100%" which
              squeezed deep dynasties to single-digit-px text. */}
          <svg
            ref={svgRef}
            viewBox={`0 0 ${viewW} ${viewH}`}
            width={viewW}
            height={viewH}
            className="lineage-svg"
            role="img"
            aria-label="Family tree"
          >
            {edges.map((e, i) => {
              // vysp.9: orthogonal stems per the brief —
              // M ax ay L ax my L bx my L bx by
              const x1 = e.from.x + NODE_W / 2;
              const y1 = e.from.y + NODE_H;
              const x2 = e.to.x + NODE_W / 2;
              const y2 = e.to.y;
              const my = (y1 + y2) / 2;
              return (
                <path
                  key={i}
                  d={`M ${x1} ${y1} L ${x1} ${my} L ${x2} ${my} L ${x2} ${y2}`}
                  className="lineage-svg__edge"
                />
              );
            })}
            {marriageLines.map((m, i) => (
              <line
                key={i}
                x1={m.a.x + NODE_W}
                y1={m.a.y + NODE_H / 2}
                x2={m.b.x}
                y2={m.b.y + NODE_H / 2}
                className="lineage-svg__marriage"
              />
            ))}
            {positioned.map((n) => {
              const isFocus = n.focus;
              const isDeceased = !!n.death_date;
              return (
                <g
                  key={n.ck3_id}
                  transform={`translate(${n.x}, ${n.y})`}
                  className={
                    'lineage-node' +
                    (isFocus ? ' lineage-node--focus' : '') +
                    (isDeceased ? ' lineage-node--deceased' : '')
                  }
                  onClick={() => onOpen(n)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onOpen(n);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-label={`Open ${n.first_name ?? `character ${n.ck3_id}`}`}
                >
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    className="lineage-node__rect"
                  />
                  {/* audit F-35 / ck3_chronicler-kzqt: was wrapped in
                      <foreignObject> + <div> + <span> + <svg>. SVG-in-SVG
                      is legal so we can embed the shield directly,
                      saving one DOM-context switch per node — Firefox
                      especially benefits on a deep dynasty.
                      ck3_chronicler-h9u6 (4y0v slice 2.1): real CoA
                      via HeraldryWithFallbackSvg when the per-node
                      coa_json + page-level palette are both
                      available; falls through to the procedural
                      seeded shield otherwise. */}
                  <HeraldryWithFallbackSvg
                    coa={n.coa_json}
                    palette={palette ?? null}
                    seed={n.ck3_id}
                    size={48}
                    label={`shield-${n.ck3_id}`}
                    x={4}
                    y={4}
                  />
                  <text
                    x={62}
                    y={22}
                    className="lineage-node__name"
                  >
                    {(n.first_name ?? `#${n.ck3_id}`).slice(0, 22)}
                  </text>
                  <text
                    x={62}
                    y={38}
                    className="lineage-node__dates"
                  >
                    {(n.birth_date ?? '????').split('.')[0] ?? '????'} —{' '}
                    {n.death_date
                      ? n.death_date.split('.')[0] ?? '?'
                      : '—'}
                  </text>
                  <text
                    x={62}
                    y={50}
                    className="lineage-node__relation"
                  >
                    {labelFor(n)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>

        <Legend />
        <div className="lineage-page__counts">
          <span className="italic-fell">
            {byId.size - 1} relative{byId.size - 1 === 1 ? '' : 's'} drawn
            from the chronicle's record.
          </span>
        </div>
      </div>
    </div>
  );
}

function Legend(): React.JSX.Element {
  return (
    <div className="lineage-legend">
      <LegendItem swatch="var(--gold-deep)" label="Lineage" />
      <LegendItem swatch="var(--carmine)" label="Marriage" dashed />
      <LegendItem swatch="var(--gold-bright)" label="Focus soul" />
    </div>
  );
}

function LegendItem({
  swatch,
  label,
  dashed = false,
}: {
  swatch: string;
  label: string;
  dashed?: boolean;
}): React.JSX.Element {
  return (
    <span className="lineage-legend__item">
      <span
        className="lineage-legend__swatch"
        style={{
          borderTop: dashed
            ? `1.5px dashed ${swatch}`
            : `1.5px solid ${swatch}`,
        }}
      />
      <span className="smallcaps lineage-legend__label">{label}</span>
    </span>
  );
}

function labelFor(n: PositionedNode): string {
  if (n.focus) return '✦ FOCUS';
  return n.relation.replace(/_/g, ' ').toUpperCase();
}

interface LayoutResult {
  positioned: PositionedNode[];
  edges: { from: PositionedNode; to: PositionedNode }[];
  marriageLines: { a: PositionedNode; b: PositionedNode }[];
  byId: Map<number, PositionedNode>;
  viewW: number;
  viewH: number;
}

function layoutTree(tree: FamilyTreeResponse | null): LayoutResult {
  if (!tree) {
    return {
      positioned: [],
      edges: [],
      marriageLines: [],
      byId: new Map(),
      viewW: 600,
      viewH: 200,
    };
  }

  // Group everyone by row.
  // - generation 0: the seed + spouses + siblings.
  // - generation -k: ancestors at depth k (k=1: parents).
  // - generation +k: descendants at depth k (k=1: children).
  const byGen = new Map<number, FamilyTreeNode[]>();
  const seed = tree.self_node;

  pushGen(byGen, 0, { ...seed, depth: 0, relation: 'self' });
  for (const a of tree.ancestors) pushGen(byGen, -a.depth, a);
  for (const d of tree.descendants) pushGen(byGen, d.depth, d);
  for (const sp of tree.spouses) pushGen(byGen, 0, sp);
  for (const sb of tree.siblings) pushGen(byGen, 0, sb);

  // Assign x positions per row, centred. The seed should sit close to
  // centre at row 0 with siblings to its left and spouses to its right.
  const positioned: PositionedNode[] = [];
  const sortedGens = [...byGen.keys()].sort((a, b) => a - b);

  for (const gen of sortedGens) {
    const row = byGen.get(gen) ?? [];
    if (gen === 0) {
      const seeds = row.filter((n) => n.relation === 'self');
      const sp = row.filter((n) =>
        n.relation === 'spouse' ||
          n.relation === 'former_spouse' ||
          n.relation === 'concubine' ||
          n.relation === 'betrothed',
      );
      const sb = row.filter((n) =>
        n.relation === 'sibling' || n.relation === 'half_sibling',
      );
      const ordered = [...sb.reverse(), ...seeds, ...sp];
      placeRow(ordered, gen, positioned, seed.ck3_id);
    } else {
      placeRow(row, gen, positioned, seed.ck3_id);
    }
  }

  const byId = new Map(positioned.map((n) => [n.ck3_id, n]));

  // Edges: each child is connected to its parent if we have positioned
  // both. Parents come from the children's family-tree response — but
  // we don't have that here. Approximate: connect every depth-(k-1)
  // ancestor to every depth-k ancestor on the side that matches their
  // mother/father relation, and connect the seed to depth-1 ancestors.
  const edges: { from: PositionedNode; to: PositionedNode }[] = [];
  // Seed → parents
  const seedNode = byId.get(seed.ck3_id);
  if (seedNode) {
    for (const a of tree.ancestors) {
      if (a.depth === 1) {
        const p = byId.get(a.ck3_id);
        if (p) edges.push({ from: p, to: seedNode });
      }
    }
    // Children → seed
    for (const d of tree.descendants) {
      if (d.depth === 1) {
        const ch = byId.get(d.ck3_id);
        if (ch) edges.push({ from: seedNode, to: ch });
      }
    }
  }
  // Deeper generations: we don't have direct parent links, but
  // visually pairing each row to the centre of the row above is the
  // common medieval-lineage idiom and reads correctly enough for a
  // single-character-rooted tree.
  //
  // audit F-34 / ck3_chronicler-fggw: bucket positioned nodes by
  // generation once instead of doing positioned.filter() per depth
  // (was 18 O(N) scans per layout). Iterate the buckets that
  // actually exist rather than the hard-coded depth <= 10.
  // (Note: ``byGen`` higher up holds the unpositioned FamilyTreeNode;
  // this index is keyed by the same generation but holds the
  // post-layout PositionedNode references the edge code needs.)
  const positionedByGen = new Map<number, PositionedNode[]>();
  for (const node of positioned) {
    const bucket = positionedByGen.get(node.generation);
    if (bucket) {
      bucket.push(node);
    } else {
      positionedByGen.set(node.generation, [node]);
    }
  }
  const ancestorGens = [...positionedByGen.keys()]
    .filter((g) => g < 0)
    .sort((a, b) => b - a);
  for (const lowerGen of ancestorGens) {
    if (lowerGen === -1) continue; // direct parents wired above
    const upper = positionedByGen.get(lowerGen + 1);
    const lower = positionedByGen.get(lowerGen);
    if (!upper || !lower) continue;
    for (const child of lower) {
      const nearest = nearestByX(upper, child);
      if (nearest) edges.push({ from: child, to: nearest });
    }
  }
  const descendantGens = [...positionedByGen.keys()]
    .filter((g) => g > 1)
    .sort((a, b) => a - b);
  for (const lowerGen of descendantGens) {
    const upper = positionedByGen.get(lowerGen - 1);
    const lower = positionedByGen.get(lowerGen);
    if (!upper || !lower) continue;
    for (const child of lower) {
      const nearest = nearestByX(upper, child);
      if (nearest) edges.push({ from: nearest, to: child });
    }
  }

  // Marriage lines: connect seed to first spouse if both positioned.
  const marriageLines: { a: PositionedNode; b: PositionedNode }[] = [];
  if (seedNode) {
    const firstSpouse = tree.spouses[0];
    if (firstSpouse) {
      const sp = byId.get(firstSpouse.ck3_id);
      if (sp) {
        // a must be left, b must be right
        if (sp.x > seedNode.x) {
          marriageLines.push({ a: seedNode, b: sp });
        } else {
          marriageLines.push({ a: sp, b: seedNode });
        }
      }
    }
  }

  // Compute viewport.
  const xs = positioned.map((n) => n.x);
  const ys = positioned.map((n) => n.y);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) + NODE_W : 600;
  const minY = ys.length ? Math.min(...ys) : 0;
  const maxY = ys.length ? Math.max(...ys) + NODE_H : 200;
  const viewW = Math.max(800, maxX - minX + PAD_X * 2);
  const viewH = Math.max(200, maxY - minY + PAD_Y * 2);

  // Shift everything so minX/minY → PAD_X/PAD_Y.
  const dx = PAD_X - minX;
  const dy = PAD_Y - minY;
  for (const n of positioned) {
    n.x += dx;
    n.y += dy;
  }

  return { positioned, edges, marriageLines, byId, viewW, viewH };
}

function pushGen(
  byGen: Map<number, FamilyTreeNode[]>,
  gen: number,
  node: FamilyTreeNode,
): void {
  const arr = byGen.get(gen);
  if (arr) arr.push(node);
  else byGen.set(gen, [node]);
}

function placeRow(
  row: FamilyTreeNode[],
  gen: number,
  positioned: PositionedNode[],
  seedId: number,
): void {
  const total = row.length;
  row.forEach((n, i) => {
    const x = (i - (total - 1) / 2) * COL_W;
    const y = gen * ROW_H;
    positioned.push({
      ...n,
      x,
      y,
      generation: gen,
      focus: n.ck3_id === seedId,
    });
  });
}

function nearestByX(
  upper: PositionedNode[],
  child: PositionedNode,
): PositionedNode | null {
  if (upper.length === 0) return null;
  let best = upper[0] ?? null;
  let bestDist = best ? Math.abs(best.x - child.x) : Infinity;
  for (const u of upper) {
    const d = Math.abs(u.x - child.x);
    if (d < bestDist) {
      best = u;
      bestDist = d;
    }
  }
  return best;
}

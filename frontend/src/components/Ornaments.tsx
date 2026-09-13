// Ornaments — illuminated-manuscript flourish family.
//
// A reusable family of 4 corner ornaments + dividers + a heraldic
// cartouche, used across hero surfaces (Library, Overview, Biographies,
// Closing). Drawn once for top-left and mirrored to the other three
// corners via SVG transform, so a single source of truth handles all
// four placements correctly (this fixes the previous Biographies/
// Chronicle corner that was rendered top-left-pointing in the top-right
// slot).

import type React from 'react';

export type CornerPlacement = 'tl' | 'tr' | 'bl' | 'br';

export type CornerVariant =
  /** Leafy vine spray — graceful, manuscript-margin feel. Default. */
  | 'vine'
  /** Hiberno-Saxon interlace knot — for big hero moments. */
  | 'knot'
  /** Renaissance scrollwork — formal, for sealed / closing surfaces. */
  | 'scroll'
  /** Minimal angular filigree — low visual weight, chrome contexts. */
  | 'filigree';

const PLACEMENT_TRANSFORM: Record<CornerPlacement, string> = {
  tl: 'none',
  tr: 'scaleX(-1)',
  bl: 'scaleY(-1)',
  br: 'scale(-1, -1)',
};

export interface CornerOrnamentProps {
  placement: CornerPlacement;
  variant?: CornerVariant;
  /** px. Default 72. */
  size?: number;
  /** Multiplies the gold fill. Default 0.75. */
  opacity?: number;
  className?: string;
}

export function CornerOrnament({
  placement,
  variant = 'vine',
  size = 72,
  opacity = 0.75,
  className,
}: CornerOrnamentProps): React.JSX.Element {
  const Body = ORNAMENT_BODIES[variant];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 80 80"
      className={'corner-orn corner-orn--' + placement + (className ? ' ' + className : '')}
      aria-hidden
      style={{ transform: PLACEMENT_TRANSFORM[placement], opacity }}
    >
      <Body />
    </svg>
  );
}

/** Render all four corners at once. Wraps them in a single
 *  position-absolute layer so it works even if the host element is not
 *  `position: relative` (the wrapper establishes its own bounding box
 *  via `inset: 0` against the nearest positioned ancestor — and we
 *  upgrade the host to relative defensively in CSS). */
export function CornerOrnamentFrame({
  variant,
  size,
  opacity,
}: {
  variant?: CornerVariant;
  size?: number;
  opacity?: number;
}): React.JSX.Element {
  return (
    <div className="corner-frame" aria-hidden>
      <CornerOrnament placement="tl" variant={variant} size={size} opacity={opacity} />
      <CornerOrnament placement="tr" variant={variant} size={size} opacity={opacity} />
      <CornerOrnament placement="bl" variant={variant} size={size} opacity={opacity} />
      <CornerOrnament placement="br" variant={variant} size={size} opacity={opacity} />
    </div>
  );
}

// ---------- Ornament bodies (designed for top-left, 80×80 viewBox) ----------

const G_STROKE = 'var(--gold-deep)';
const G_FILL = 'var(--gold-deep)';
const R_DOT = 'var(--carmine)';

function VineBody(): React.JSX.Element {
  return (
    <g fill="none" stroke={G_STROKE} strokeWidth="1.1" strokeLinecap="round">
      {/* main vine sweep */}
      <path d="M 4 44 C 4 20, 20 4, 44 4" />
      {/* paired leaves */}
      <path
        d="M 12 32 Q 2 26, 6 16 Q 16 22, 12 32 Z"
        fill={G_FILL}
        fillOpacity="0.55"
        stroke={G_STROKE}
      />
      <path
        d="M 32 12 Q 26 2, 16 6 Q 22 16, 32 12 Z"
        fill={G_FILL}
        fillOpacity="0.55"
        stroke={G_STROKE}
      />
      {/* tendril curl into corner */}
      <path d="M 4 4 Q 16 8, 18 20" />
      <circle cx="18" cy="20" r="1.4" fill={G_FILL} stroke="none" />
      {/* terminal jewel */}
      <circle cx="4" cy="4" r="2.4" fill={G_FILL} stroke="none" />
      <circle cx="4" cy="4" r="1.1" fill={R_DOT} stroke="none" />
      {/* trailing dots along the curve */}
      <circle cx="24" cy="6" r="0.9" fill={G_FILL} stroke="none" />
      <circle cx="6" cy="24" r="0.9" fill={G_FILL} stroke="none" />
      <circle cx="38" cy="10" r="0.6" fill={G_FILL} stroke="none" />
      <circle cx="10" cy="38" r="0.6" fill={G_FILL} stroke="none" />
    </g>
  );
}

function KnotBody(): React.JSX.Element {
  return (
    <g fill="none" stroke={G_STROKE} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round">
      {/* faint shaded backdrop */}
      <path d="M 4 4 L 4 40 Q 4 4, 40 4 Z" fill={G_FILL} fillOpacity="0.08" stroke="none" />
      {/* interlace loops */}
      <path d="M 10 22 Q 10 8, 24 8 Q 24 22, 14 28 Q 10 28, 10 22 Z" fill={G_FILL} fillOpacity="0.16" />
      <path d="M 14 14 Q 24 16, 22 26" />
      <path d="M 14 14 Q 26 24, 38 16" />
      <path d="M 14 14 Q 16 26, 26 38" />
      <circle cx="14" cy="14" r="1.6" fill={G_FILL} stroke="none" />
      {/* terminal jewel */}
      <circle cx="4" cy="4" r="2.4" fill={G_FILL} stroke="none" />
      <circle cx="4" cy="4" r="1.1" fill={R_DOT} stroke="none" />
      {/* edge tick marks */}
      <path d="M 48 4 L 52 4 M 56 4 L 60 4 M 64 4 L 68 4" />
      <path d="M 4 48 L 4 52 M 4 56 L 4 60 M 4 64 L 4 68" />
    </g>
  );
}

function ScrollBody(): React.JSX.Element {
  return (
    <g fill="none" stroke={G_STROKE} strokeWidth="1" strokeLinecap="round">
      <path d="M 4 36 Q 4 4, 36 4" />
      <path d="M 4 36 Q 14 26, 18 18 Q 22 10, 36 4" />
      <path d="M 10 26 Q 18 30, 26 22 Q 30 18, 26 12" />
      {/* central bud */}
      <circle cx="22" cy="20" r="2.4" fill={G_FILL} fillOpacity="0.6" stroke={G_STROKE} />
      {/* trailing whips */}
      <path d="M 36 4 Q 50 6, 50 16" />
      <path d="M 4 36 Q 6 50, 16 50" />
      <circle cx="50" cy="16" r="1" fill={G_FILL} stroke="none" />
      <circle cx="16" cy="50" r="1" fill={G_FILL} stroke="none" />
      {/* terminal jewel */}
      <circle cx="4" cy="4" r="2.4" fill={G_FILL} stroke="none" />
      <circle cx="4" cy="4" r="1.1" fill={R_DOT} stroke="none" />
    </g>
  );
}

function FiligreeBody(): React.JSX.Element {
  // Direct successor to the original chronicle-page corner — same
  // visual vocabulary, but placement-aware via transform.
  return (
    <g fill="none" stroke={G_STROKE} strokeWidth="1" strokeLinecap="round">
      <path d="M 4 4 L 68 4 M 4 4 L 4 68" />
      <path d="M 12 4 Q 24 12, 12 24" />
      <path d="M 4 12 Q 12 24, 24 12" />
      <circle cx="4" cy="4" r="2.5" fill={G_FILL} stroke="none" />
      <circle cx="20" cy="20" r="1.4" fill={R_DOT} stroke="none" />
      <path d="M 28 4 L 32 4 M 36 4 L 40 4 M 44 4 L 48 4" />
      <path d="M 4 28 L 4 32 M 4 36 L 4 40 M 4 44 L 4 48" />
    </g>
  );
}

const ORNAMENT_BODIES: Record<CornerVariant, () => React.JSX.Element> = {
  vine: VineBody,
  knot: KnotBody,
  scroll: ScrollBody,
  filigree: FiligreeBody,
};

// ---------- Fleuron dividers ----------

export type FleuronVariant = 'stars' | 'trefoil' | 'diamond';

const FLEURON_GLYPHS: Record<FleuronVariant, string> = {
  stars: '✦   ✦   ✦',
  trefoil: '❦   ✦   ❦',
  diamond: '◆   ✦   ◆',
};

export function FleuronRule({
  variant = 'stars',
  className,
}: {
  variant?: FleuronVariant;
  className?: string;
}): React.JSX.Element {
  return (
    <div className={'fleuron' + (className ? ' ' + className : '')} aria-hidden>
      <span className="fleuron__glyph">{FLEURON_GLYPHS[variant]}</span>
    </div>
  );
}

// ---------- Heraldic cartouche (hero frame for big arms) ----------

/** A framed slot for a large heraldry render — radial sunburst rays,
 *  warm glow, drop shadow. Used for the big hero moments on Library
 *  and Overview. Pure decoration — pass the heraldry node as child. */
export function HeraldicCartouche({
  children,
  size = 220,
  className,
}: {
  children: React.ReactNode;
  /** Total cartouche width in px (rays extend beyond it). */
  size?: number;
  className?: string;
}): React.JSX.Element {
  return (
    <div
      className={'cartouche' + (className ? ' ' + className : '')}
      style={{ width: size, height: size }}
    >
      <svg
        className="cartouche__rays"
        viewBox="0 0 200 200"
        aria-hidden
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <radialGradient id="cart-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--gold-glow)" stopOpacity="0.35" />
            <stop offset="55%" stopColor="var(--gold-glow)" stopOpacity="0.10" />
            <stop offset="100%" stopColor="var(--gold-glow)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx="100" cy="100" r="98" fill="url(#cart-glow)" />
        {/* radial sunburst */}
        {Array.from({ length: 36 }).map((_, i) => {
          const a = (i / 36) * Math.PI * 2;
          const r1 = 78;
          const r2 = i % 3 === 0 ? 96 : i % 3 === 1 ? 88 : 84;
          return (
            <line
              key={i}
              x1={100 + Math.cos(a) * r1}
              y1={100 + Math.sin(a) * r1}
              x2={100 + Math.cos(a) * r2}
              y2={100 + Math.sin(a) * r2}
              stroke="var(--gold-deep)"
              strokeWidth={i % 3 === 0 ? 0.9 : 0.5}
              strokeLinecap="round"
              opacity={i % 3 === 0 ? 0.6 : 0.3}
            />
          );
        })}
        {/* inner gold ring */}
        <circle
          cx="100"
          cy="100"
          r="76"
          fill="none"
          stroke="var(--gold-deep)"
          strokeWidth="0.6"
          opacity="0.5"
        />
      </svg>
      <div className="cartouche__inner">{children}</div>
    </div>
  );
}

/**
 * Procedural heraldry — deterministic shield avatars from a seed.
 *
 * Ports design/heraldry.jsx to React + TypeScript. Pure / stateless;
 * the same seed always renders the same shield. Used everywhere a
 * character or campaign is shown.
 *
 * This is a *fallback* — see ck3_chronicler-7ao for the planned
 * replacement that renders real CK3 coat-of-arms structural data
 * (extracted DDS textures + named-color palette). When that lands,
 * this component's prop API stays the same; the renderer swaps
 * underneath.
 *
 * Algorithm:
 *   1. FNV-1a hash the seed (or pass through int seeds).
 *   2. Pick a field tincture, second tincture, division pattern (6
 *      variants), charge (12 variants), and charge metal (or/argent)
 *      from the hash bits.
 *   3. Render an SVG heater shield with an outer rim, the field
 *      composition clipped to the shape, the charge centered, and a
 *      subtle sheen overlay.
 */

const TINCTURES = {
  azure: '#1E3A8A',
  azureLt: '#2C4DB0',
  gules: '#9C2E2A',
  gulesLt: '#B8413C',
  sable: '#1B1714',
  vert: '#2F5A36',
  purpure: '#4A2C56',
  or: '#C9A24A', // gold
  argent: '#EFE3C2', // silver/cream
  brunatre: '#6F4A24', // brown
} as const;

type Tincture = keyof typeof TINCTURES;

const COLORS: readonly Tincture[] = [
  'azure',
  'gules',
  'sable',
  'vert',
  'purpure',
  'azureLt',
  'gulesLt',
  'brunatre',
];
const METALS: readonly Tincture[] = ['or', 'argent'];

const CHARGES = [
  'cross',
  'lion',
  'eagle',
  'fleur',
  'star',
  'rose',
  'bend',
  'chevron',
  'pale',
  'crown',
  'sword',
  'tower',
] as const;

type ChargeKind = (typeof CHARGES)[number];

// FNV-1a string hash → uint32. Matches design/heraldry.jsx exactly so
// the same seed renders the same shield in both implementations.
function fnv1a(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

function pick<T>(arr: readonly T[], n: number): T {
  // Indexing into a non-empty readonly array; bounds are bounded by `% length`.
  // noUncheckedIndexedAccess pessimistically types this as T | undefined; cast.
  return arr[n % arr.length] as T;
}

const SHIELD_PATH = 'M 4 4 L 76 4 L 76 36 C 76 60 60 78 40 92 C 20 78 4 60 4 36 Z';
const SHIELD_VB = '0 0 80 96';

export interface HeraldryProps {
  /** Seed for the deterministic generator. Ints pass through; strings are hashed. */
  seed: string | number;
  /** Outer width in px; height is 1.15× this. Default 80. */
  size?: number;
  /** Accessible label (e.g. character name). */
  label?: string;
  /** When true, a thin gilded ring is added to the rim. */
  ring?: boolean;
}

/** Inner shield body — emitted as a self-contained <svg> element so it
 * can be used (a) inside the HTML wrapper :func:`Heraldry` returns and
 * (b) nested directly into another <svg> context (audit F-35 /
 * ck3_chronicler-kzqt) without going through a <foreignObject> bridge.
 *
 * SVG-in-SVG is legal per the spec and avoids one DOM context switch
 * per node — visible on the LineagePage where 20+ shields used to mount
 * inside foreignObject + a wrapping <div> just to host the
 * Heraldry component's <span> wrapper.
 */
export function HeraldryShieldSvg({
  seed,
  size = 80,
  label,
  ring = false,
  x,
  y,
}: HeraldryProps & { x?: number; y?: number }): React.JSX.Element {
  const seedInt = typeof seed === 'number' ? seed : fnv1a(String(seed));
  const division = seedInt % 6; // 0 plain, 1 per pale, 2 per fess, 3 quarterly, 4 per bend, 5 per chevron
  const fieldA = pick(COLORS, seedInt >> 3);
  const fieldB = pick(COLORS, (seedInt >> 7) + 3);
  const charge = pick(CHARGES, seedInt >> 11);
  const chargeColor = pick(METALS, seedInt >> 14);

  const fa = TINCTURES[fieldA];
  const fb = TINCTURES[fieldB];
  const cc = TINCTURES[chargeColor];
  const clipId = `clip-${seedInt}`;
  const sheenId = `sheen-${seedInt}`;

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
        <linearGradient id={sheenId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="rgba(255,255,255,0.18)" />
          <stop offset="50%" stopColor="rgba(255,255,255,0)" />
          <stop offset="100%" stopColor="rgba(0,0,0,0.18)" />
        </linearGradient>
      </defs>

      {/* Field — base color */}
      <g clipPath={`url(#${clipId})`}>
        <rect x="0" y="0" width="80" height="96" fill={fa} />

        {/* Division (per pale / per fess / quarterly / per bend / per chevron) */}
        {division === 1 && <rect x="40" y="0" width="40" height="96" fill={fb} />}
        {division === 2 && <rect x="0" y="48" width="80" height="48" fill={fb} />}
        {division === 3 && (
          <>
            <rect x="40" y="0" width="40" height="48" fill={fb} />
            <rect x="0" y="48" width="40" height="48" fill={fb} />
          </>
        )}
        {division === 4 && <polygon points="0,0 80,96 0,96" fill={fb} />}
        {division === 5 && <polygon points="0,96 40,56 80,96" fill={fb} />}

        {/* Charge */}
        <Charge kind={charge} color={cc} seed={seedInt} />

        {/* Sheen overlay for subtle depth */}
        <rect x="0" y="0" width="80" height="96" fill={`url(#${sheenId})`} />
      </g>

      {/* Outer rim */}
      <path d={SHIELD_PATH} fill="none" stroke="#7C5A14" strokeWidth="1.5" />
      {ring && <path d={SHIELD_PATH} fill="none" stroke="#C9A24A" strokeWidth="0.6" />}
    </svg>
  );
}

export function Heraldry(props: HeraldryProps): React.JSX.Element {
  const { size = 80 } = props;
  return (
    <span
      className="shield"
      style={{ width: size, height: size * 1.15, display: 'inline-block' }}
    >
      <HeraldryShieldSvg {...props} />
    </span>
  );
}

function Charge({
  kind,
  color,
  seed,
}: {
  kind: ChargeKind;
  color: string;
  seed: number;
}): React.JSX.Element | null {
  const altMetal = color === '#C9A24A' ? '#7C5A14' : '#C9A24A';
  switch (kind) {
    case 'cross':
      return (
        <g fill={color}>
          <rect x="36" y="14" width="8" height="50" />
          <rect x="20" y="32" width="40" height="8" />
        </g>
      );
    case 'pale':
      return <rect x="34" y="6" width="12" height="84" fill={color} />;
    case 'bend':
      return <polygon points="6,6 24,6 76,82 60,90" fill={color} />;
    case 'chevron':
      return <polygon points="40,22 76,70 66,76 40,42 14,76 4,70" fill={color} />;
    case 'fleur':
      return (
        <g fill={color} transform="translate(40 48) scale(0.55)">
          <path d="M 0 -36 C -10 -28 -14 -16 -8 -6 C -16 -10 -26 -8 -28 2 C -22 6 -14 6 -8 2 C -8 12 -2 22 0 28 C 2 22 8 12 8 2 C 14 6 22 6 28 2 C 26 -8 16 -10 8 -6 C 14 -16 10 -28 0 -36 Z" />
          <rect x="-22" y="2" width="44" height="4" />
        </g>
      );
    case 'star':
      return <Star cx={40} cy={48} r={20} color={color} points={seed % 3 === 0 ? 6 : 5} />;
    case 'rose':
      return (
        <g transform="translate(40 48)" fill={color}>
          {Array.from({ length: 5 }).map((_, i) => {
            const a = (i / 5) * Math.PI * 2 - Math.PI / 2;
            const cx = Math.cos(a) * 12;
            const cy = Math.sin(a) * 12;
            return <circle key={i} cx={cx} cy={cy} r="9" />;
          })}
          <circle r="6" fill={altMetal} />
        </g>
      );
    case 'lion':
      return (
        <g fill={color} transform="translate(40 48)">
          <path d="M -22 -8 Q -22 -22 -8 -22 Q 0 -28 8 -22 Q 22 -22 22 -8 L 22 8 Q 22 22 8 22 Q 0 28 -8 22 Q -22 22 -22 8 Z" />
          <circle cx="-6" cy="-6" r="2" fill="#1B1714" />
          <circle cx="6" cy="-6" r="2" fill="#1B1714" />
          <path d="M -3 6 Q 0 10 3 6" stroke="#1B1714" strokeWidth="1.5" fill="none" />
        </g>
      );
    case 'eagle':
      return (
        <g fill={color} transform="translate(40 48)">
          <path d="M 0 -22 L 8 -8 L 28 -2 L 12 6 L 18 22 L 0 14 L -18 22 L -12 6 L -28 -2 L -8 -8 Z" />
        </g>
      );
    case 'crown':
      return (
        <g fill={color} transform="translate(40 50)">
          <path d="M -22 8 L -22 -4 L -14 4 L -8 -10 L 0 4 L 8 -10 L 14 4 L 22 -4 L 22 8 Z" />
          <circle cx="-14" cy="-6" r="2" />
          <circle cx="0" cy="-12" r="2" />
          <circle cx="14" cy="-6" r="2" />
        </g>
      );
    case 'sword':
      return (
        <g fill={color} transform="translate(40 48)">
          <rect x="-2" y="-30" width="4" height="48" />
          <rect x="-12" y="14" width="24" height="4" />
          <rect x="-3" y="18" width="6" height="10" />
          <polygon points="-2,-30 2,-30 0,-36" />
        </g>
      );
    case 'tower':
      return (
        <g fill={color} transform="translate(40 50)">
          <rect x="-16" y="-2" width="32" height="22" />
          <rect x="-20" y="-8" width="6" height="6" />
          <rect x="-10" y="-8" width="6" height="6" />
          <rect x="2" y="-8" width="6" height="6" />
          <rect x="14" y="-8" width="6" height="6" />
          <rect x="-4" y="6" width="8" height="14" fill={altMetal} />
        </g>
      );
    default:
      return null;
  }
}

function Star({
  cx,
  cy,
  r,
  color,
  points = 5,
}: {
  cx: number;
  cy: number;
  r: number;
  color: string;
  points?: number;
}): React.JSX.Element {
  const pts: string[] = [];
  for (let i = 0; i < points * 2; i++) {
    const a = (i / (points * 2)) * Math.PI * 2 - Math.PI / 2;
    const rr = i % 2 === 0 ? r : r * 0.45;
    pts.push(`${cx + Math.cos(a) * rr},${cy + Math.sin(a) * rr}`);
  }
  return <polygon points={pts.join(' ')} fill={color} />;
}

// Tiny banner — used as a campaign flag in the appbar / cards.
// Same hash → same banner; uses field + metal pair instead of two fields.

export interface BannerProps {
  seed: string | number;
  size?: number;
}

export function Banner({ seed, size = 22 }: BannerProps): React.JSX.Element {
  const seedInt = typeof seed === 'number' ? seed : fnv1a(String(seed));
  const fa = TINCTURES[pick(COLORS, seedInt)];
  const fb = TINCTURES[pick(METALS, seedInt >> 4)];
  return (
    <svg width={size} height={size * 1.18} viewBox="0 0 22 26">
      <path
        d="M 1 1 L 21 1 L 21 18 L 11 25 L 1 18 Z"
        fill={fa}
        stroke="#7C5A14"
        strokeWidth="0.8"
      />
      <path d="M 11 5 L 14 11 L 11 17 L 8 11 Z" fill={fb} />
    </svg>
  );
}

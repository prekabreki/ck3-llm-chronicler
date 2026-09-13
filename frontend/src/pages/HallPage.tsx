// Hall of Fame — ck3_chronicler-467k.1.
//
// 4-up grid of dynasty cards (cross-campaign rollups), with sort and
// filter chips and a distinct empty-state path. Each card is a
// scale-down of the Dynasty Wall (thpz) hero: shield medallion + name
// + span + italic blurb + 3-stat counts row.
//
// Active vs archived differentiation is the load-bearing design call:
// NOT via fade. Both stay full-saturation; archived cards carry the
// purple ARCHIVED pip + a hairline purpure double-rule along the
// bottom edge (echoing the closing-ceremony double rule).
//
// Data: `useHallOfFame()` is a stub returning fixture rows shaped per
// the brief. The cross-DB aggregator that populates this for real is
// out of scope for 467k.1 — see ck3_chronicler-467k for the data
// layer. Click → setActiveCampaign + view='dynasty'.

import { useMemo, useState } from 'react';

import { Banner } from '../components/Heraldry';
import { HeraldryWithFallback } from '../components/RealHeraldry';
import { CornerOrnamentFrame } from '../components/Ornaments';
import { usePalette } from '../api/queries';
import { useAppStore } from '../store/appStore';
import { useHallOfFame, type DynastyRollup } from './hall/useHallOfFame';

type SortMode = 'most-recent' | 'longest-played' | 'most-tracked';
type FilterMode = 'all' | 'active' | 'archived';

const SORT_OPTIONS: { id: SortMode; label: string }[] = [
  { id: 'most-recent', label: 'Most-recent' },
  { id: 'longest-played', label: 'Longest played' },
  { id: 'most-tracked', label: 'Most tracked' },
];

export function HallPage(): React.JSX.Element {
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const setView = useAppStore((s) => s.setView);
  const hallQ = useHallOfFame();
  const paletteQ = usePalette();
  const palette = paletteQ.data ?? null;

  const [sort, setSort] = useState<SortMode>('most-recent');
  const [filter, setFilter] = useState<FilterMode>('all');

  const dynasties = hallQ.data ?? null;

  const sorted = useMemo(() => {
    if (!dynasties) return [];
    return sortDynasties(filterDynasties(dynasties, filter), sort);
  }, [dynasties, sort, filter]);

  const counts = useMemo(() => {
    if (!dynasties) return { total: 0, active: 0, archived: 0, playthroughs: 0 };
    const playthroughIds = new Set<string>();
    let active = 0;
    let archived = 0;
    for (const d of dynasties) {
      if (d.is_active) active += 1;
      else archived += 1;
      if (d.playthrough_id) playthroughIds.add(d.playthrough_id);
    }
    return {
      total: dynasties.length,
      active,
      archived,
      playthroughs: playthroughIds.size,
    };
  }, [dynasties]);

  const onOpen = (d: DynastyRollup): void => {
    setActiveCampaign(d.primary_campaign_name, 'dynasty');
    // setActiveCampaign already navigates when a view is passed; setView
    // is a belt-and-suspenders for the case where the campaign name
    // hasn't changed (no-op in setActiveCampaign path).
    setView('dynasty');
  };

  if (hallQ.isLoading) {
    return (
      <section className="hall-page">
        <PageHeader />
        <p className="hall-state">Gathering the chronicle of dynasties…</p>
      </section>
    );
  }

  if (hallQ.isError || !dynasties) {
    return (
      <section className="hall-page">
        <PageHeader />
        <p className="hall-state" role="alert">
          Could not read the hall.
        </p>
      </section>
    );
  }

  if (dynasties.length === 0) {
    return (
      <section className="hall-page">
        <PageHeader />
        <HallEmpty
          onAdopt={() => setView('library')}
        />
      </section>
    );
  }

  return (
    <section className="hall-page">
      <PageHeader counts={counts} />

      <div className="hall-toolbar" role="toolbar" aria-label="Sort and filter dynasties">
        <span className="hall-toolbar__group-label">Sort</span>
        {SORT_OPTIONS.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={'chip' + (sort === opt.id ? ' is-active' : '')}
            aria-pressed={sort === opt.id}
            onClick={() => setSort(opt.id)}
          >
            {opt.label}
          </button>
        ))}

        <span className="hall-toolbar__divider" aria-hidden />

        <span className="hall-toolbar__group-label">Filter</span>
        <button
          type="button"
          className={'chip' + (filter === 'all' ? ' is-active' : '')}
          aria-pressed={filter === 'all'}
          onClick={() => setFilter('all')}
        >
          All
        </button>
        <button
          type="button"
          className={'chip' + (filter === 'active' ? ' is-active' : '')}
          aria-pressed={filter === 'active'}
          onClick={() => setFilter('active')}
        >
          <span className="pip__dot pip__dot--alive" /> Active · {counts.active}
        </button>
        <button
          type="button"
          className={'chip' + (filter === 'archived' ? ' is-active' : '')}
          aria-pressed={filter === 'archived'}
          onClick={() => setFilter('archived')}
        >
          <span className="pip__dot pip__dot--archived" /> Archived · {counts.archived}
        </button>

        <span className="hall-toolbar__spacer" />
        <span className="hall-toolbar__ribbon">
          {counts.total} {plural('dynasty', 'dynasties', counts.total)} ·{' '}
          {counts.playthroughs}{' '}
          {plural('playthrough', 'playthroughs', counts.playthroughs)}
        </span>
      </div>

      <div className="hall-grid">
        {sorted.map((d) => (
          <DynastyCard
            key={d.id}
            dynasty={d}
            palette={palette}
            onOpen={onOpen}
          />
        ))}
      </div>
    </section>
  );
}

interface PageHeaderProps {
  counts?: { total: number; playthroughs: number };
}

function PageHeader({ counts }: PageHeaderProps): React.JSX.Element {
  return (
    <header className="page-header ornamented">
      <CornerOrnamentFrame variant="knot" size={72} opacity={0.7} />
      <div className="page-header__eyebrow">Cross-campaign gallery</div>
      <h1 className="page-header__title">Hall of Fame</h1>
      <p className="page-header__lede">
        {counts && counts.total > 0
          ? `${counts.total} ${plural('dynasty', 'dynasties', counts.total)} across ${counts.playthroughs} ${plural('playthrough', 'playthroughs', counts.playthroughs)}. Every shield is the work of a real save; every span the actual founding-to-final-tick distance.`
          : 'A gallery of every dynasty you have chronicled. Each shield is the work of a real save; each span the actual founding-to-final-tick distance.'}
      </p>
    </header>
  );
}

interface DynastyCardProps {
  dynasty: DynastyRollup;
  palette: import('../components/CoaTypes').Palette | null;
  onOpen: (d: DynastyRollup) => void;
}

function DynastyCard({
  dynasty: d,
  palette,
  onOpen,
}: DynastyCardProps): React.JSX.Element {
  // The 467k aggregator passes the player's coa_json on the most-recent
  // campaign in each rollup. When present, render the real CK3
  // heraldry; HeraldryWithFallback handles the procedural-shield branch
  // when coa_json or palette is missing.
  const badgeText = d.is_active
    ? `Active · ${d.span_end_label ?? '—'}`
    : `Archived · sealed ${d.sealed_at_label ?? d.span_end_label ?? '—'}`;

  return (
    <button
      type="button"
      className={
        'dyn-card' + (d.is_active ? '' : ' is-archived')
      }
      onClick={() => onOpen(d)}
      aria-label={`Open ${d.dynasty_name} on the Dynasty Wall`}
    >
      <span
        className={
          'dyn-card__badge ' +
          (d.is_active ? 'dyn-card__badge--active' : 'dyn-card__badge--archived')
        }
      >
        {badgeText}
      </span>

      <div className="dyn-card__shield-cell">
        <HeraldryWithFallback
          coa={d.coa_json}
          palette={palette}
          seed={d.heraldry_seed}
          size={120}
          ring
          label={`shield-${d.dynasty_name}`}
        />
      </div>

      <div>
        <h3 className="dyn-card__name">{d.dynasty_name}</h3>
        <div className="dyn-card__span">{d.span_label}</div>
        {d.blurb ? (
          <p className="dyn-card__blurb">"{d.blurb}"</p>
        ) : (
          <p className="dyn-card__blurb dyn-card__blurb--missing">
            (chronicle in progress)
          </p>
        )}
      </div>

      <div className="dyn-card__counts">
        <Count n={d.campaigns_count} label="campaigns" />
        <Count n={d.tracked_count} label="souls" />
        <Count n={d.biographies_count} label="vitae" />
      </div>
    </button>
  );
}

function Count({
  n,
  label,
}: {
  n: number;
  label: string;
}): React.JSX.Element {
  return (
    <div className="dyn-card__count">
      <span className="dyn-card__count-n">{n}</span>
      <span className="dyn-card__count-l">{label}</span>
    </div>
  );
}

function HallEmpty({ onAdopt }: { onAdopt: () => void }): React.JSX.Element {
  return (
    <div className="hall-empty">
      <div className="hall-empty__shield" aria-hidden>
        <Banner seed="Hall of Fame placeholder" size={140} />
      </div>
      <h2 className="hall-empty__title">No dynasties yet.</h2>
      <p className="hall-empty__lede">
        The hall waits for its first chronicle. Adopt a save, play through
        to a death, and the first soul will hang their shield here.
      </p>
      <button
        type="button"
        className="hall-empty__cta"
        onClick={onAdopt}
      >
        + Adopt a save
      </button>
      <p className="hall-empty__hint">
        Drop a CK3 save into your save folder, or point chronicler at one
        explicitly. The Library page does both.
      </p>
    </div>
  );
}

function filterDynasties(
  dynasties: readonly DynastyRollup[],
  filter: FilterMode,
): DynastyRollup[] {
  if (filter === 'all') return [...dynasties];
  if (filter === 'active') return dynasties.filter((d) => d.is_active);
  return dynasties.filter((d) => !d.is_active);
}

function sortDynasties(
  dynasties: DynastyRollup[],
  sort: SortMode,
): DynastyRollup[] {
  const out = [...dynasties];
  if (sort === 'most-recent') {
    // Most-recently-touched first; missing timestamps sort last.
    out.sort((a, b) => {
      const av = a.last_event_iso ?? '';
      const bv = b.last_event_iso ?? '';
      if (av === bv) return a.dynasty_name.localeCompare(b.dynasty_name);
      return bv.localeCompare(av);
    });
  } else if (sort === 'longest-played') {
    // Largest span (in days) first; nulls last.
    out.sort((a, b) => (b.span_days ?? -1) - (a.span_days ?? -1));
  } else {
    // most-tracked: most souls first.
    out.sort((a, b) => b.tracked_count - a.tracked_count);
  }
  return out;
}

function plural(one: string, many: string, n: number): string {
  return n === 1 ? one : many;
}

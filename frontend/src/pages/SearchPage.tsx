// SearchPage — vysp.11 reskin: hero input (display 24px on a 720px
// rule-bordered field with ⌕ icon + ↵ kbd chip), filter pills (type +
// campaign with mono counts), and result cards (56px shield · body
// with display name + provenance crumbs + Garamond <mark>-tinted
// excerpt · mono meta column with kind-tag pill).
//
// Submits q to GET /api/search. Hits arrive grouped by campaign +
// pre-ranked server-side; client-side filters narrow the visible set
// without re-querying.

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { searchAll } from '../api/client';
import { queryKeys, usePalette } from '../api/queries';
import { HeraldryWithFallback } from '../components/RealHeraldry';
import { useAppStore } from '../store/appStore';
import type { SearchHitResponse } from '../api/types';

type Kind = 'all' | 'character' | 'event' | 'biography';

const KIND_PILLS: { id: Kind; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'character', label: 'Souls' },
  { id: 'event', label: 'Events' },
  { id: 'biography', label: 'Biographies' },
];

const KIND_TAG_TONE: Record<string, string> = {
  character: 'carmine',
  event: 'gold',
  biography: 'ink',
};

export function SearchPage(): React.JSX.Element {
  const [draft, setDraft] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [kindFilter, setKindFilter] = useState<Kind>('all');
  const [campaignFilter, setCampaignFilter] = useState<string | null>(null);
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const setSelectedCharacterId = useAppStore((s) => s.setSelectedCharacterId);

  const searchQ = useQuery({
    queryKey: queryKeys.search(submitted),
    queryFn: () => searchAll(submitted),
    enabled: submitted.length > 0,
    retry: false,
  });

  const onSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    setSubmitted(draft.trim());
    setKindFilter('all');
    setCampaignFilter(null);
  };

  const onJump = (hit: SearchHitResponse): void => {
    // audit F-33 / ck3_chronicler-v53j: setActiveCampaign now takes
    // an explicit view so the campaign + view + selectedCharacterId
    // updates are atomic. Setting selectedCharacterId after lets
    // chronicle render the right pane immediately.
    if (hit.character_id !== null) {
      setActiveCampaign(hit.campaign_name, 'chronicle');
      setSelectedCharacterId(hit.character_id);
    } else {
      setActiveCampaign(hit.campaign_name, 'codex');
    }
  };

  const data = searchQ.data;

  // Counts feed the filter pills' mono badges. Computed once across the
  // raw hit set so toggling filters doesn't shift the badge counts.
  const kindCounts = useMemo(() => {
    const m: Record<Kind, number> = {
      all: 0,
      character: 0,
      event: 0,
      biography: 0,
    };
    for (const h of data?.hits ?? []) {
      m.all += 1;
      const k = h.kind as Kind;
      if (k in m) m[k] += 1;
    }
    return m;
  }, [data]);

  const campaignCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const h of data?.hits ?? []) {
      m[h.campaign_name] = (m[h.campaign_name] ?? 0) + 1;
    }
    return m;
  }, [data]);

  const filteredHits = useMemo(() => {
    const all = data?.hits ?? [];
    return all.filter((h) => {
      if (kindFilter !== 'all' && h.kind !== kindFilter) return false;
      if (campaignFilter !== null && h.campaign_name !== campaignFilter) return false;
      return true;
    });
  }, [data, kindFilter, campaignFilter]);

  const filteredByCampaign = useMemo(() => {
    const out: Record<string, SearchHitResponse[]> = {};
    for (const h of filteredHits) {
      if (!out[h.campaign_name]) out[h.campaign_name] = [];
      out[h.campaign_name]!.push(h);
    }
    return out;
  }, [filteredHits]);

  return (
    <div className="search-page">
      <div className="search-page__inner">
        <header className="search-page__header">
          <div className="smallcaps search-page__eyebrow">
            ✦ ✦ ✦  Cross-campaign search  ✦ ✦ ✦
          </div>
          <h1 className="uncial search-page__title">
            Seek across the codices
          </h1>
          <p className="italic-fell search-page__lede">
            Full-text over every biography, character, and event in
            every campaign on this machine.
          </p>
        </header>

        <form className="search-page__hero" onSubmit={onSubmit} role="search">
          <div className="search-page__big">
            <span className="search-page__glyph" aria-hidden>⌕</span>
            <input
              type="search"
              className="search-page__input"
              placeholder="A name, a phrase, an event…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Search query"
              autoFocus
            />
            <button type="submit" className="search-page__kbd">
              <span aria-hidden>↵</span>
              <span className="search-page__kbd-label">Seek</span>
            </button>
          </div>

          {data && data.hits.length > 0 && (
            <div className="search-page__filters">
              {KIND_PILLS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={
                    'search-pill' + (kindFilter === p.id ? ' search-pill--active' : '')
                  }
                  onClick={() => setKindFilter(p.id)}
                >
                  {p.label}
                  <span className="search-pill__count">{kindCounts[p.id]}</span>
                </button>
              ))}
              <span className="search-pill__sep" aria-hidden />
              <button
                type="button"
                className={
                  'search-pill' + (campaignFilter === null ? ' search-pill--active' : '')
                }
                onClick={() => setCampaignFilter(null)}
              >
                All campaigns
                <span className="search-pill__count">{data.hits.length}</span>
              </button>
              {Object.entries(campaignCounts).map(([name, count]) => (
                <button
                  key={name}
                  type="button"
                  className={
                    'search-pill' + (campaignFilter === name ? ' search-pill--active' : '')
                  }
                  onClick={() => setCampaignFilter(name)}
                >
                  {name}
                  <span className="search-pill__count">{count}</span>
                </button>
              ))}
            </div>
          )}
        </form>

        {searchQ.isLoading && submitted && (
          <p className="italic-fell search-page__status">
            Reading every leaf…
          </p>
        )}
        {searchQ.isError && (
          <p
            className="italic-fell search-page__status search-page__status--error"
            role="alert"
          >
            The search failed. Older campaigns may need an FTS5 reindex.
          </p>
        )}
        {data && data.hits.length === 0 && (
          <p className="italic-fell search-page__status">
            Nothing found for "{data.query}".
          </p>
        )}

        {data && data.hits.length > 0 && (
          <div className="search-results">
            <p className="italic-fell search-results__summary">
              {data.hits.length} result
              {data.hits.length === 1 ? '' : 's'} for{' '}
              <strong>"{data.query}"</strong>, across{' '}
              {Object.keys(data.by_campaign).length} campaign
              {Object.keys(data.by_campaign).length === 1 ? '' : 's'}.
              {filteredHits.length !== data.hits.length && (
                <>
                  {' '}Showing {filteredHits.length} after filters.
                </>
              )}
            </p>
            {Object.entries(filteredByCampaign)
              .filter(([, hits]) => hits.length > 0)
              .map(([campaign, hits]) => (
                <section key={campaign} className="search-results__group">
                  <div className="search-results__campaign">
                    <span className="smallcaps search-results__campaign-name">
                      ✦ {campaign}
                    </span>
                    <span className="smallcaps search-results__campaign-count">
                      · {hits.length} hit{hits.length === 1 ? '' : 's'}
                    </span>
                  </div>
                  <div className="search-results__list">
                    {hits.map((hit, i) => (
                      <ResultCard
                        key={`${hit.kind}-${hit.row_id}-${i}`}
                        hit={hit}
                        onJump={onJump}
                      />
                    ))}
                  </div>
                </section>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ResultCard({
  hit,
  onJump,
}: {
  hit: SearchHitResponse;
  onJump: (h: SearchHitResponse) => void;
}): React.JSX.Element {
  const tone = KIND_TAG_TONE[hit.kind] ?? 'ink';
  const seed = hit.character_id ?? hit.row_id;
  // ck3_chronicler-3yd9 (2026-05-08): swap procedural Banner for the
  // HeraldryWithFallback path so search hits render the same real arms
  // as Codex/Tracked rows. Backend now batch-resolves coa_json per hit;
  // when coa+palette resolve we render real CoA, otherwise the fallback
  // routes back to the procedural-by-id shield (same look as before).
  const { data: palette } = usePalette();
  const targetSurface =
    hit.kind === 'character'
      ? 'codex'
      : hit.character_id !== null
      ? 'chronicle'
      : 'codex';
  const crumbs = `${hit.campaign_name} › ${capitalize(hit.kind)}`;
  return (
    <article
      className="result-card"
      onClick={() => onJump(hit)}
      onKeyDown={(e) => {
        // audit F-13 / ck3_chronicler-xjm9: result cards advertise
        // role="button" + tabIndex=0 but had no keyboard activation,
        // so they were focusable but non-functional via keyboard.
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onJump(hit);
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="result-card__shield" aria-hidden>
        <HeraldryWithFallback
          coa={hit.coa_json}
          palette={palette ?? null}
          seed={seed}
          size={56}
          label={`shield-${seed}`}
        />
      </div>
      <div className="result-card__body">
        <div className="result-card__name">
          {hit.character_id !== null ? `Character #${hit.character_id}` : hit.campaign_name}
        </div>
        <div className="result-card__crumbs">
          <b>{hit.campaign_name}</b> › {capitalize(hit.kind)} › {targetSurface}
        </div>
        <div className="search-result__snippet result-card__excerpt">
          {renderHighlightedSnippet(hit.snippet)}
        </div>
      </div>
      <div className="result-card__meta">
        <span className={`search-tag search-tag--${tone}`}>{hit.kind}</span>
        <span>#{hit.row_id}</span>
        {hit.rank !== null && <span>rank {hit.rank.toFixed(2)}</span>}
        <span>
          {crumbs}
        </span>
      </div>
    </article>
  );
}

function capitalize(s: string): string {
  return s.length ? s[0]!.toUpperCase() + s.slice(1) : s;
}

// FTS5 snippet() uses <b>...</b> tags; the brief wants <mark> highlights.
// audit F-11 / ck3_chronicler-qzd1: was building an HTML string and
// piping through dangerouslySetInnerHTML, with placeholder substitution
// (OPENMARK / CLOSEMARK) to defend against re-introducing raw HTML.
// Now: split on <b>...</b> boundaries and emit <mark> React nodes
// directly. Text between tags goes through the normal React text
// path (auto-escaped); no innerHTML assignment, no placeholder dance.
function renderHighlightedSnippet(snippet: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /<b>([^]*?)<\/b>/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(snippet)) !== null) {
    if (m.index > last) {
      parts.push(snippet.slice(last, m.index));
    }
    parts.push(<mark key={key++}>{m[1] ?? ''}</mark>);
    last = m.index + m[0].length;
  }
  if (last < snippet.length) {
    parts.push(snippet.slice(last));
  }
  return parts;
}

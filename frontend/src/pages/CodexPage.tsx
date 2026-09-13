// CodexPage — character list for the active campaign + selected
// character's right-pane card. Port of design/codex-page.jsx.
//
// Three TanStack queries: campaign metadata (header), character list
// (left rail), tracked-set (to highlight tracked rows). The right pane
// fetches CharacterDetail for the selected ck3_id.

import React, { useMemo, useRef, useState } from 'react';

import {
  queryKeys,
  useBiography,
  useCampaign,
  useCharacters,
  useCharactersByIds,
  useCharactersSearch,
  useCharacterDetail,
  useNarrativeInvalidation,
  useNarrativeQueue,
  useTracked,
} from '../api/queries';
import { HeraldryWithFallback } from '../components/RealHeraldry';
import { usePalette } from '../api/queries';
import { RegenerateBiographyButton } from '../components/RegenerateBiographyButton';
import { StatusChip } from '../components/StatusChip';
import { useAppStore } from '../store/appStore';
import type { CharacterSummary, NarrativeQueueResponse, TrackedResponse } from '../api/types';
import type { Palette } from '../components/CoaTypes';
import { pickChipState } from '../util/characterStatus';
import { prettifyTag } from '../util/prettify';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function CodexPage({ campaignName }: { campaignName: string }): React.JSX.Element {
  const selectedId = useAppStore((s) => s.selectedCharacterId);
  const onSelect = useAppStore((s) => s.setSelectedCharacterId);
  const campaignQ = useCampaign(campaignName, true);
  const trackedQ = useTracked(campaignName);
  const queueQ = useNarrativeQueue();
  const [search, setSearch] = useState('');
  const trimmedSearch = search.trim();
  // ck3_chronicler-w1t3 follow-up: toolbar filters. `playedOnly` narrows
  // both tables to souls who have been the campaign player (is_played);
  // `trackedOnly` collapses the page to the Tracked table alone. Played
  // souls are always floated to the top of each table regardless (see
  // sortPlayedFirst) — the "or at least sort" half of the request.
  //
  // Window caveat: the "Of the court" list is the first MAX_LIMIT souls by
  // ck3_id (list_characters_unfiltered), so `playedOnly` only surfaces
  // played-but-untracked souls that fall inside that window. Tracked souls
  // are fetched in full by id, and the player line is almost always tracked,
  // so played+tracked souls always appear.
  const [playedOnly, setPlayedOnly] = useState(false);
  const [trackedOnly, setTrackedOnly] = useState(false);

  // ck3_chronicler-7gw: when a narrative_* SSE frame arrives, invalidate
  // the biography query for the selected character so a regenerate
  // (or any save-tail-driven generation) lands in the right pane the
  // moment it completes. Scoped to the selected character; sibling
  // characters' biography queries refetch lazily on next selection.
  useNarrativeInvalidation(campaignName, (qc) => {
    if (selectedId === null) return;
    void qc.invalidateQueries({
      queryKey: queryKeys.biography(campaignName, selectedId),
    });
  });

  // ck3_chronicler-5oyz: tracked rail no longer intersects the relevance-
  // ranked top-N window — large campaigns push tracked souls out of it
  // entirely. Fetch tracked details by id directly so the rail always
  // renders the actual tracked set.
  const trackedIdList = useMemo(
    () => (trackedQ.data ?? []).map((t) => t.character_id),
    [trackedQ.data],
  );
  const trackedCharsQ = useCharactersByIds(campaignName, trackedIdList);

  const trackedIds = useMemo(
    () => new Set(trackedIdList),
    [trackedIdList],
  );

  // ck3_chronicler-0px: per-character lookup for paused / drafted state
  // — the queue alone doesn't carry these signals.
  const trackedById = useMemo(() => {
    const m = new Map<number, TrackedResponse>();
    for (const t of trackedQ.data ?? []) m.set(t.character_id, t);
    return m;
  }, [trackedQ.data]);

  // ck3_chronicler-5oyz: the of-court rail switches between two data
  // sources. With no search, show the relevance-ranked default top
  // (useCharacters). With a non-empty search, hit the BE's ranked
  // search across every soul in the DB (useCharactersSearch) so a
  // typed name finds matches even outside the top window.
  const charsDefaultQ = useCharacters(trimmedSearch === '' ? campaignName : null);
  const charsSearchQ = useCharactersSearch(campaignName, trimmedSearch);
  const courtRailIsLoading = trimmedSearch === ''
    ? charsDefaultQ.isLoading
    : charsSearchQ.isLoading;
  const courtRailIsError = trimmedSearch === ''
    ? charsDefaultQ.isError
    : charsSearchQ.isError;
  const courtRailData = trimmedSearch === ''
    ? charsDefaultQ.data
    : charsSearchQ.data;

  const tracked = useMemo(() => {
    const all = trackedCharsQ.data ?? [];
    const needle = trimmedSearch.toLowerCase();
    // Tracked is small — client-side filter is fine and keeps the rail
    // responsive while the of-court BE search is in flight.
    let rows = needle
      ? all.filter(
          (c) =>
            (c.first_name?.toLowerCase().includes(needle) ?? false) ||
            (c.dynasty_name?.toLowerCase().includes(needle) ?? false) ||
            String(c.ck3_id).includes(needle),
        )
      : all;
    if (playedOnly) rows = rows.filter((c) => c.is_played);
    return sortPlayedFirst(rows);
  }, [trackedCharsQ.data, trimmedSearch, playedOnly]);

  const others = useMemo(() => {
    let rows = (courtRailData ?? []).filter((c) => !trackedIds.has(c.ck3_id));
    if (playedOnly) rows = rows.filter((c) => c.is_played);
    return sortPlayedFirst(rows);
  }, [courtRailData, trackedIds, playedOnly]);

  return (
    <div className="codex-page">
      <div className="codex-page__inner">
        <header className="codex-page__header">
          <div className="smallcaps codex-page__eyebrow">
            {campaignQ.data?.ck3_version ? `CK3 ${campaignQ.data.ck3_version}` : campaignName}
          </div>
          <h1 className="uncial codex-page__title">The Codex of Souls</h1>
          <p className="italic-fell codex-page__lede">
            Every soul recorded across {campaignName}. Tracked souls
            accrue a biography upon their death.
          </p>
        </header>

        <div className="codex-page__toolbar">
          <SearchField
            placeholder="Seek a name or ID…"
            value={search}
            onChange={setSearch}
          />
          <span className="pip">
            <span className="pip__dot pip__dot--alive" /> {tracked.length} tracked
          </span>
          {!trackedOnly && (
            <span className="pip">
              <span className="pip__dot pip__dot--idle" /> {others.length} of court
            </span>
          )}
          <FilterToggle
            label="Played"
            dot="gold"
            active={playedOnly}
            onToggle={() => setPlayedOnly((v) => !v)}
          />
          <FilterToggle
            label="Tracked"
            dot="alive"
            active={trackedOnly}
            onToggle={() => setTrackedOnly((v) => !v)}
          />
        </div>

        {courtRailIsLoading && (
          <p className="italic-fell codex-page__status">Drawing the codex…</p>
        )}
        {courtRailIsError && (
          <p
            className="italic-fell codex-page__status codex-page__status--error"
            role="alert"
          >
            Failed to read the codex.
          </p>
        )}

        {!courtRailIsLoading && !courtRailIsError && (
          <div className="codex-layout">
            <div className="paper paper--edged codex-tables">
              <CodexTable
                title="Tracked"
                gold
                characters={tracked}
                selectedId={selectedId}
                onSelect={onSelect}
                queue={queueQ.data}
                trackedById={trackedById}
              />
              {!trackedOnly && (
                <>
                  <div className="codex-tables__rule" />
                  <CodexTable
                    title="Of the court"
                    characters={others}
                    selectedId={selectedId}
                    onSelect={onSelect}
                    queue={queueQ.data}
                    trackedById={trackedById}
                  />
                </>
              )}
            </div>
            <CharacterPane campaignName={campaignName} ck3Id={selectedId} />
          </div>
        )}
      </div>
    </div>
  );
}

// ck3_chronicler-w1t3 follow-up: float played souls to the top of a table,
// preserving the relative order of everyone else. Array.prototype.sort is
// stable (ES2019+), so this is a clean partition — played first, the original
// id / tracked ordering intact within each partition.
function sortPlayedFirst(chars: CharacterSummary[]): CharacterSummary[] {
  return [...chars].sort((a, b) => Number(b.is_played) - Number(a.is_played));
}

interface FilterToggleProps {
  label: string;
  dot: string;
  active: boolean;
  onToggle: () => void;
}

function FilterToggle({ label, dot, active, onToggle }: FilterToggleProps): React.JSX.Element {
  return (
    <button
      type="button"
      className={'codex-filter' + (active ? ' codex-filter--active' : '')}
      aria-pressed={active}
      onClick={onToggle}
    >
      <span className={`pip__dot pip__dot--${dot}`} /> {label}
    </button>
  );
}

interface SearchFieldProps {
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
}

function SearchField({ placeholder, value, onChange }: SearchFieldProps): React.JSX.Element {
  return (
    <label className="codex-search">
      <span className="smallcaps codex-search__glyph">❧</span>
      <input
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="codex-search__input"
        aria-label="Search characters"
      />
    </label>
  );
}

interface CodexTableProps {
  characters: CharacterSummary[];
  title: string;
  gold?: boolean;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  queue: NarrativeQueueResponse | undefined;
  trackedById: Map<number, TrackedResponse>;
}

function CodexTable({
  characters,
  title,
  gold = false,
  selectedId,
  onSelect,
  queue,
  trackedById,
}: CodexTableProps): React.JSX.Element {
  return (
    <div className="codex-table">
      <div
        className={
          'codex-table__head' + (gold ? ' codex-table__head--gold' : '')
        }
      >
        <span className="smallcaps codex-table__title">
          {gold ? '✦ ' : ''}
          {title} · {characters.length}
        </span>
      </div>
      {characters.length === 0 ? (
        <p className="italic-fell codex-table__empty">none</p>
      ) : (
        <CodexListbox
          characters={characters}
          title={title}
          selectedId={selectedId}
          onSelect={onSelect}
          queue={queue}
          trackedById={trackedById}
        />
      )}
    </div>
  );
}

// ck3_chronicler-vgcp: CodexPage rows previously declared role="listitem"
// with aria-selected — illegal (aria-selected is only valid on
// option/row/gridcell/treeitem) and parent role="list" reads as a static
// list, hiding interactivity. New pattern: parent role="listbox" +
// children role="option" with WAI-ARIA-1.2 roving tabindex (only the
// selected option, or the first row when nothing is selected, is in the
// tab order; arrow keys move focus + selection). Selection follows focus
// so this stays a "pane driver" listbox — clicking or arrowing onto a
// row updates the right pane.
interface CodexListboxProps {
  characters: CharacterSummary[];
  title: string;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  queue: NarrativeQueueResponse | undefined;
  trackedById: Map<number, TrackedResponse>;
}

function CodexListbox({
  characters,
  title,
  selectedId,
  onSelect,
  queue,
  trackedById,
}: CodexListboxProps): React.JSX.Element {
  const rowRefs = useRef<Array<HTMLDivElement | null>>([]);
  // audit L36 (27ov.81): fetch the process-static palette ONCE for the
  // whole list and hand it to each row, rather than every CodexRow
  // subscribing to usePalette() on its own. RQ deduped the duplicate
  // subscriptions so this is behaviourally a no-op, but the per-row call
  // contradicted the documented fetch-once-at-page heraldry pattern.
  const { data: palette } = usePalette();

  const selectedIndex = useMemo(() => {
    if (selectedId === null) return -1;
    return characters.findIndex((c) => c.ck3_id === selectedId);
  }, [characters, selectedId]);

  // Tab-order anchor: the selected option, or the first one when nothing
  // in *this* table is selected. Without an anchor the listbox would
  // either trap multiple tab stops (one per row) or none.
  const anchorIndex = selectedIndex >= 0 ? selectedIndex : 0;

  const focusAndSelect = (nextIndex: number) => {
    const clamped = Math.max(0, Math.min(characters.length - 1, nextIndex));
    const target = characters[clamped];
    if (!target) return;
    onSelect(target.ck3_id);
    const el = rowRefs.current[clamped];
    if (el) el.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        focusAndSelect((selectedIndex >= 0 ? selectedIndex : -1) + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        focusAndSelect(
          (selectedIndex >= 0 ? selectedIndex : characters.length) - 1,
        );
        break;
      case 'Home':
        e.preventDefault();
        focusAndSelect(0);
        break;
      case 'End':
        e.preventDefault();
        focusAndSelect(characters.length - 1);
        break;
    }
  };

  return (
    <div
      className="codex-rows"
      role="listbox"
      aria-label={title}
      aria-multiselectable={false}
      onKeyDown={handleKeyDown}
    >
      {characters.map((c, i) => (
        <CodexRow
          key={c.ck3_id}
          ref={(el) => {
            rowRefs.current[i] = el;
          }}
          c={c}
          selected={selectedId === c.ck3_id}
          tabbable={i === anchorIndex}
          onSelect={onSelect}
          queue={queue}
          tracked={trackedById.get(c.ck3_id) ?? null}
          palette={palette ?? null}
        />
      ))}
    </div>
  );
}

interface CodexRowProps {
  c: CharacterSummary;
  selected: boolean;
  // ck3_chronicler-vgcp: roving tabindex anchor — true only for the
  // listbox's tab-stop row (selected option, or first row when nothing
  // is selected). Other rows live at tabIndex=-1 per WAI-ARIA listbox 1.2.
  tabbable: boolean;
  onSelect: (id: number) => void;
  queue: NarrativeQueueResponse | undefined;
  tracked: TrackedResponse | null;
  // audit L36 (27ov.81): the process-static palette, fetched once by the
  // listbox and passed down — the row no longer subscribes itself.
  palette: Palette | null;
}

const CodexRow = React.forwardRef<HTMLDivElement, CodexRowProps>(function CodexRow(
  { c, selected, tabbable, onSelect, queue, tracked, palette },
  ref,
) {
  const isTracked = tracked !== null;
  const isPlayed = c.is_played;
  const chipState = pickChipState(c.ck3_id, queue, tracked);
  // ck3_chronicler-4y0v slice 1: render the persisted CoA when present,
  // fall through to the procedural seeded shield when not. Without
  // this the codex grid showed a hash-of-id shield even when the
  // chronicle folio had the real arms.
  const titleParts: string[] = [];
  titleParts.push(c.death_date ? 'Departed' : 'Living');
  // ck3_chronicler-w1t3 (2026-05-08): mark played characters distinctly
  // from tracked. A character can be both (the user-tracked the player
  // line). When both are true, "Played · Tracked" renders in that order
  // — played is the stronger signal, tracked the secondary.
  if (isPlayed) titleParts.push('Played');
  if (isTracked) titleParts.push('Tracked');
  return (
    <div
      ref={ref}
      role="option"
      className={
        'codex-row' +
        (selected ? ' codex-row--selected' : '') +
        (isTracked ? ' is-tracked' : '') +
        (isPlayed ? ' is-played' : '')
      }
      onClick={() => onSelect(c.ck3_id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(c.ck3_id);
        }
      }}
      tabIndex={tabbable ? 0 : -1}
      aria-selected={selected}
    >
      <div className="codex-row__shield-cell">
        <HeraldryWithFallback
          coa={c.coa_json}
          palette={palette ?? null}
          seed={c.ck3_id}
          size={56}
          label={`shield-${c.ck3_id}`}
        />
      </div>
      <div className="codex-row__head">
        <span className="codex-row__name">
          {isPlayed && (
            <span
              className="codex-row__played-mark"
              aria-label="played character"
              title="A player character at some point in this campaign"
            >
              ✦
            </span>
          )}
          {c.first_name ?? `Character ${c.ck3_id}`}
        </span>
        <div className="codex-row__sub">
          <span className="codex-row__id">#{c.ck3_id}</span>
          {c.dynasty_name && (
            <span className="codex-row__dyn">{c.dynasty_name}</span>
          )}
        </div>
      </div>
      <div className="codex-row__dates">
        <b>{c.birth_date ?? '????'}</b> — <b>{c.death_date ?? '—'}</b>
      </div>
      <div className="codex-row__title">
        {chipState ? (
          <StatusChip state={chipState} />
        ) : (
          <span
            className={
              'codex-row__title-text' +
              (isTracked ? ' codex-row__title-text--tracked' : '')
            }
          >
            {titleParts.join(' · ')}
          </span>
        )}
      </div>
      <div className="codex-row__vitae">
        {tracked && tracked.biography_count > 0
          ? `${tracked.biography_count} vit${tracked.biography_count === 1 ? 'a' : 'æ'}`
          : '—'}
      </div>
    </div>
  );
});

interface CharacterPaneProps {
  campaignName: string;
  ck3Id: number | null;
}

function CharacterPane({ campaignName, ck3Id }: CharacterPaneProps): React.JSX.Element {
  const detailQ = useCharacterDetail(campaignName, ck3Id);
  const bioQ = useBiography(campaignName, ck3Id);
  // ck3_chronicler-4y0v slice 1: same real-CoA-with-fallback as the row.
  const { data: palette } = usePalette();

  if (ck3Id === null) {
    return (
      <aside className="paper paper--edged character-pane character-pane--empty">
        <p className="italic-fell">Select a soul from the codex.</p>
      </aside>
    );
  }

  if (detailQ.isLoading) {
    return (
      <aside className="paper paper--edged character-pane">
        <p className="italic-fell">Drawing the leaf…</p>
      </aside>
    );
  }

  if (detailQ.isError || !detailQ.data) {
    return (
      <aside className="paper paper--edged character-pane">
        <p className="italic-fell character-pane__error" role="alert">
          Could not read this leaf.
        </p>
      </aside>
    );
  }

  const c = detailQ.data;
  return (
    <aside className="paper paper--edged character-pane">
      <div className="character-pane__shield">
        <HeraldryWithFallback
          coa={c.coa_json}
          palette={palette ?? null}
          seed={c.ck3_id}
          size={88}
          ring
          label={`big-shield-${c.ck3_id}`}
        />
      </div>
      <h2 className="uncial character-pane__name">
        {c.first_name ?? `Character ${c.ck3_id}`}
      </h2>
      {c.nickname && (
        <div className="italic-fell character-pane__nick">
          called {c.nickname}
        </div>
      )}
      <div className="rule-thin character-pane__rule" />
      <dl className="character-pane__pairs">
        <PaneRow k="Dynasty" v={c.dynasty_name} />
        <PaneRow k="House" v={c.house_name} />
        <PaneRow k="Culture" v={prettifyTag(c.culture)} />
        <PaneRow k="Faith" v={prettifyTag(c.faith)} />
        <PaneRow k="Born" v={c.birth_date} />
        <PaneRow k="Died" v={c.death_date} />
        <PaneRow k="Events" v={String(c.events.length)} />
      </dl>
      <CharacterPaneActions
        campaignName={campaignName}
        ck3Id={c.ck3_id}
        firstName={c.first_name}
        hasExistingBiography={bioQ.data != null}
      />
    </aside>
  );
}

interface CharacterPaneActionsProps {
  campaignName: string;
  ck3Id: number;
  firstName: string | null;
  hasExistingBiography: boolean;
}

function CharacterPaneActions({
  campaignName,
  ck3Id,
  firstName,
  hasExistingBiography,
}: CharacterPaneActionsProps): React.JSX.Element {
  const setView = useAppStore((s) => s.setView);
  return (
    <div className="character-pane__actions">
      <button
        type="button"
        className="btn"
        onClick={() => setView('chronicle')}
      >
        Open Chronicle
      </button>
      <button
        type="button"
        className="btn btn--quiet"
        onClick={() => setView('tree')}
      >
        Lineage
      </button>
      <RegenerateBiographyButton
        campaignName={campaignName}
        ck3Id={ck3Id}
        characterName={firstName}
        hasExistingBiography={hasExistingBiography}
      />
    </div>
  );
}

function PaneRow({ k, v }: { k: string; v: string | null }): React.JSX.Element {
  return (
    <div className="character-pane__pair">
      <dt className="smallcaps character-pane__pair-key">{k}</dt>
      <dd className="character-pane__pair-value">{v ?? '—'}</dd>
    </div>
  );
}

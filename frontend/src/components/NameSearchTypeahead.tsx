// ck3_chronicler-4i33 — typeahead that hits /characters?q=… with a
// 200ms debounce and renders an inline result list. Selecting a row
// calls add-tracked and surfaces the result via the rail's toast props.
//
// audit F-29 / ck3_chronicler-vgzs: extracted from TrackedPage.tsx so
// the page component stops mixing this with the row grid.
//
// ck3_chronicler-wd7x: the markup previously claimed WAI-ARIA combobox
// semantics (role=listbox + role=option) without shipping any of the
// contract — no role=combobox on the input, no aria-expanded, no
// activedescendant, no ArrowDown/Up/Enter/Escape handling, and
// aria-selected literally hard-coded to false. Keyboard users could
// type but had to reach for the mouse to select a result. This module
// now implements the WAI-ARIA-1.2 combobox-with-listbox pattern with
// aria-activedescendant and full keyboard navigation.

import { useEffect, useMemo, useRef, useState } from 'react';

import { useAddTracked, useCharactersSearch } from '../api/queries';
import type { CharacterSummary } from '../api/types';

interface NameSearchTypeaheadProps {
  campaignName: string;
  onTracked: (characterName: string) => void;
  onError: (message: string) => void;
}

const LISTBOX_ID = 'track-name-search-listbox';
const optionId = (idx: number): string => `track-name-search-option-${idx}`;

export function NameSearchTypeahead({
  campaignName,
  onTracked,
  onError,
}: NameSearchTypeaheadProps): React.JSX.Element {
  const [raw, setRaw] = useState('');
  const [debounced, setDebounced] = useState('');
  const [open, setOpen] = useState(false);
  // Highlighted option index for the activedescendant pattern. null when
  // no option is highlighted (initial state / no results); otherwise an
  // index into `results`.
  const [highlightedIndex, setHighlightedIndex] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const addMutation = useAddTracked(campaignName);

  // Debounce the query so a fast typist doesn't fire one fetch per
  // keystroke. 200ms is the typeahead industry-standard sweet spot.
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(raw.trim()), 200);
    return () => window.clearTimeout(id);
  }, [raw]);

  // M-F14 (27ov.66): same registry key family as the codex search —
  // the old hand-rolled 'characterSearch' key was one letter away from
  // the registry's 'charactersSearch' for the same endpoint. A brief
  // staleTime keeps the result fresh between keystrokes.
  const searchQ = useCharactersSearch(campaignName, debounced, {
    limit: 8,
    minLength: 2,
    staleTime: 30 * 1000,
  });

  const results = useMemo<CharacterSummary[]>(
    () => searchQ.data ?? [],
    [searchQ.data],
  );
  const showDropdown =
    open && debounced.length >= 2 && (searchQ.isFetching || results.length > 0);

  // Reset highlight when the result list changes (typing or fetching
  // settling). Highlight the first hit so ArrowDown isn't required to
  // start navigating — Enter immediately commits the top match. Adjust
  // during render via the previous-value pattern rather than in an
  // effect, so there's no extra commit / one-frame stale highlight.
  const [prevResults, setPrevResults] = useState(results);
  if (results !== prevResults) {
    setPrevResults(results);
    setHighlightedIndex(results.length > 0 ? 0 : null);
  }

  const onPick = (c: CharacterSummary): void => {
    addMutation.mutate(
      { character_id: c.ck3_id, role: null, note: null },
      {
        onSuccess: () => {
          onTracked(c.first_name ?? `character ${c.ck3_id}`);
          setRaw('');
          setOpen(false);
          setHighlightedIndex(null);
          inputRef.current?.blur();
        },
        onError: (err) => {
          onError(err instanceof Error ? err.message : String(err));
        },
      },
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    switch (e.key) {
      case 'ArrowDown': {
        if (!showDropdown || results.length === 0) {
          // Open on first ArrowDown when results exist but the dropdown
          // is closed (e.g. after re-focusing the input).
          if (results.length > 0) setOpen(true);
          return;
        }
        e.preventDefault();
        setHighlightedIndex((idx) => {
          if (idx === null) return 0;
          return idx + 1 < results.length ? idx + 1 : 0;
        });
        break;
      }
      case 'ArrowUp': {
        if (!showDropdown || results.length === 0) return;
        e.preventDefault();
        setHighlightedIndex((idx) => {
          if (idx === null) return results.length - 1;
          return idx > 0 ? idx - 1 : results.length - 1;
        });
        break;
      }
      case 'Home': {
        if (!showDropdown || results.length === 0) return;
        e.preventDefault();
        setHighlightedIndex(0);
        break;
      }
      case 'End': {
        if (!showDropdown || results.length === 0) return;
        e.preventDefault();
        setHighlightedIndex(results.length - 1);
        break;
      }
      case 'Enter': {
        if (highlightedIndex === null) return;
        const target = results[highlightedIndex];
        if (!target) return;
        e.preventDefault();
        onPick(target);
        break;
      }
      case 'Escape': {
        if (!open) return;
        e.preventDefault();
        setOpen(false);
        setHighlightedIndex(null);
        break;
      }
    }
  };

  const activeDescendantId =
    showDropdown && highlightedIndex !== null
      ? optionId(highlightedIndex)
      : undefined;

  return (
    <div className="track-side__typeahead">
      <input
        ref={inputRef}
        id="track-name-search"
        type="text"
        placeholder="Search the codex by name…"
        autoComplete="off"
        value={raw}
        role="combobox"
        aria-expanded={showDropdown}
        aria-controls={LISTBOX_ID}
        aria-autocomplete="list"
        aria-activedescendant={activeDescendantId}
        onChange={(e) => {
          setRaw(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          // Tiny delay so a click on a result lands before close.
          window.setTimeout(() => setOpen(false), 120);
        }}
        onKeyDown={handleKeyDown}
      />
      {showDropdown && (
        <ul
          id={LISTBOX_ID}
          className="typeahead-results"
          role="listbox"
          aria-label="Character matches"
        >
          {searchQ.isFetching && results.length === 0 && (
            <li className="typeahead-results__loading italic-fell">
              Searching…
            </li>
          )}
          {results.map((c, i) => {
            const highlighted = i === highlightedIndex;
            return (
              <li
                key={c.ck3_id}
                id={optionId(i)}
                className={
                  'typeahead-result' +
                  (highlighted ? ' typeahead-result--highlighted' : '')
                }
                role="option"
                aria-selected={highlighted}
                onMouseEnter={() => setHighlightedIndex(i)}
              >
                <button
                  type="button"
                  className="typeahead-result__btn"
                  // Keep mouse-click path working; tab order stays on the
                  // input per the activedescendant pattern.
                  tabIndex={-1}
                  onMouseDown={(e) => {
                    // Prevent the input's onBlur from firing before click.
                    e.preventDefault();
                  }}
                  onClick={() => onPick(c)}
                  disabled={addMutation.isPending}
                >
                  <span className="typeahead-result__name">
                    {c.first_name ?? `character ${c.ck3_id}`}
                  </span>
                  <span className="typeahead-result__meta">
                    {c.dynasty_name && (
                      <span className="typeahead-result__dynasty">
                        {c.dynasty_name}
                      </span>
                    )}
                    <span className="typeahead-result__id">#{c.ck3_id}</span>
                  </span>
                </button>
              </li>
            );
          })}
          {!searchQ.isFetching && results.length === 0 && (
            <li className="typeahead-results__empty italic-fell">
              No souls match.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// BiographiesPage — polish pass: four corner ornaments framing the
// header, restored to all four corners properly mirrored (fixes the
// old single-corner flourish that was rendered top-left-pointing in
// the top-right slot).

import { useCampaignBiographies, usePalette } from '../api/queries';
import { CornerOrnamentFrame, FleuronRule } from '../components/Ornaments';
import { HeldTitlesLines } from '../components/PrimaryTitleLine';
import { HeraldryWithFallback } from '../components/RealHeraldry';
import type { Palette } from '../components/CoaTypes';
import { useAppStore } from '../store/appStore';
import { formatDateRange } from '../util/format';
import type { BiographyListEntry } from '../api/types';

// ck3_chronicler-lmex: group the flat list by role so the page reads as
// an overview. Rulers + Consorts open; Kin + Others collapse to fight
// volume (native <details>, no JS state).
const ROLE_SECTIONS: ReadonlyArray<{
  role: string;
  label: string;
  collapsed: boolean;
}> = [
  { role: 'ruler', label: 'Rulers', collapsed: false },
  { role: 'consort', label: 'Consorts', collapsed: false },
  { role: 'kin', label: 'Kin', collapsed: true },
  { role: 'other', label: 'Others', collapsed: true },
];

function groupByRole(
  entries: BiographyListEntry[],
): Map<string, BiographyListEntry[]> {
  const m = new Map<string, BiographyListEntry[]>();
  for (const e of entries) {
    const role = e.role ?? 'other';
    const list = m.get(role) ?? [];
    list.push(e);
    m.set(role, list);
  }
  return m;
}

function SectionRows({
  rows,
  onOpen,
  palette,
}: {
  rows: BiographyListEntry[];
  onOpen: (ck3Id: number) => void;
  // audit L36 (27ov.81): palette fetched once by the page, threaded down.
  palette: Palette | null;
}): React.JSX.Element {
  return (
    <ol className="biographies-page__list" role="list">
      {rows.map((entry) => (
        <BiographyRow
          key={entry.ck3_id}
          entry={entry}
          onOpen={() => onOpen(entry.ck3_id)}
          palette={palette}
        />
      ))}
    </ol>
  );
}

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function BiographiesPage({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const bioQ = useCampaignBiographies(campaignName);
  // audit L36 (27ov.81): one palette subscription for the page; passed
  // to each BiographyRow via SectionRows instead of per-row usePalette().
  const { data: palette } = usePalette();
  const setView = useAppStore((s) => s.setView);
  const setSelectedCharacterId = useAppStore((s) => s.setSelectedCharacterId);
  const onOpen = (ck3Id: number): void => {
    setSelectedCharacterId(ck3Id);
    setView('chronicle');
  };

  if (bioQ.isLoading) {
    return (
      <div className="biographies-page biographies-page--empty">
        <p className="italic-fell">Drawing the vitæ…</p>
      </div>
    );
  }
  if (bioQ.isError) {
    return (
      <div className="biographies-page biographies-page--empty">
        <p className="italic-fell" role="alert">
          Could not read the biographies.
        </p>
      </div>
    );
  }

  const entries = bioQ.data ?? [];
  const grouped = groupByRole(entries);

  return (
    <div className="biographies-page">
      <div className="biographies-page__inner">
        <header className="biographies-page__header ornamented">
          <CornerOrnamentFrame variant="filigree" size={64} opacity={0.75} />
          <div className="smallcaps biographies-page__eyebrow">
            <button
              type="button"
              className="link"
              onClick={() => setView('campaign-overview')}
            >
              ← Overview
            </button>
            <span className="biographies-page__sep">·</span>
            <span>{campaignName}</span>
          </div>
          <h1 className="uncial biographies-page__title">Biographies</h1>
          <p className="italic-fell biographies-page__lede">
            Every chronicled life in this campaign, in the order their
            stories closed. {entries.length === 1
              ? '1 vita.'
              : `${entries.length} vitæ.`}
          </p>
        </header>

        <FleuronRule variant="diamond" />

        {entries.length === 0 ? (
          <p className="italic-fell biographies-page__none">
            No biographies generated yet. Track a character and let the
            chronicler do its work, or wait for the next death event.
          </p>
        ) : (
          ROLE_SECTIONS.filter(
            (s) => (grouped.get(s.role)?.length ?? 0) > 0,
          ).map((s) => {
            const rows = grouped.get(s.role)!;
            const heading = `${s.label} (${rows.length})`;
            return s.collapsed ? (
              <details key={s.role} className="biographies-page__section">
                <summary className="smallcaps biographies-page__section-title">
                  {heading}
                </summary>
                <SectionRows rows={rows} onOpen={onOpen} palette={palette ?? null} />
              </details>
            ) : (
              <section key={s.role} className="biographies-page__section">
                <h2 className="smallcaps biographies-page__section-title">
                  {heading}
                </h2>
                <SectionRows rows={rows} onOpen={onOpen} palette={palette ?? null} />
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}

function BiographyRow({
  entry,
  onOpen,
  palette,
}: {
  entry: BiographyListEntry;
  onOpen: () => void;
  // audit L36 (27ov.81): palette fetched once by the page, passed down.
  palette: Palette | null;
}): React.JSX.Element {
  const name = entry.first_name ?? `Character ${entry.ck3_id}`;
  const span = formatDateRange(entry.birth_date, entry.death_date);
  return (
    <li className="biographies-row">
      <button
        type="button"
        className="biographies-row__btn"
        onClick={onOpen}
        aria-label={`Open the chronicle of ${name}`}
      >
        <div className="biographies-row__shield" aria-hidden>
          <HeraldryWithFallback
            coa={entry.coa_json}
            palette={palette ?? null}
            seed={entry.ck3_id}
            size={64}
            label={`shield-${entry.ck3_id}`}
          />
        </div>
        <div className="biographies-row__head">
          <div className="biographies-row__name-line">
            <span className="biographies-row__name">{name}</span>
            {entry.nickname && (
              <span className="italic-fell biographies-row__nick">
                called {entry.nickname}
              </span>
            )}
          </div>
          <HeldTitlesLines
            heldTitles={entry.held_titles}
            primaryTitle={entry.primary_title}
            female={entry.female}
            variant="biographies-row"
          />
          {entry.relation && (
            <p className="italic-fell biographies-row__relation">
              {entry.relation}
            </p>
          )}
          <div className="biographies-row__sub">
            {entry.dynasty_name && (
              <span className="biographies-row__dyn">{entry.dynasty_name}</span>
            )}
            {span && <span className="biographies-row__span">{span}</span>}
          </div>
          <p className="biographies-row__excerpt">{entry.biography_excerpt}</p>
        </div>
      </button>
    </li>
  );
}


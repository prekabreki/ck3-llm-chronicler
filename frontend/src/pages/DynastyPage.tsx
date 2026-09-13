// Dynasty Wall — ck3_chronicler-thpz.1.
//
// Three stacked surfaces: hero band (current head's arms + dynasty
// metadata), lineage strip (ordered by birth date with each member's
// own CoA at 48px), vita roll (chronicled members with biography
// excerpts ordered by death date desc). The page reads through the
// useDynasty hook against /api/campaigns/{name}/dynasty; tracking
// status comes via useTracked() and is merged client-side so the
// dynasty endpoint stays pure-campaign-DB.

import { HeraldryWithFallback } from '../components/RealHeraldry';
import { HeraldicCartouche } from '../components/Ornaments';
import { useAppStore } from '../store/appStore';
import {
  useDynasty,
  usePalette,
  useTracked,
} from '../api/queries';
import type {
  DynastyMember,
  DynastyResponse,
  DynastyVitaEntry,
} from '../api/client';
import type { Palette } from '../components/CoaTypes';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now (DynastyPage's hand-rolled guard had drifted — no Library
// link). This page is only mounted with a non-null campaign.
export function DynastyPage({ campaignName }: { campaignName: string }): React.JSX.Element {
  const setView = useAppStore((s) => s.setView);
  const setSelectedCharacterId = useAppStore((s) => s.setSelectedCharacterId);

  const dynastyQ = useDynasty(campaignName);
  const trackedQ = useTracked(campaignName);
  const paletteQ = usePalette();

  if (dynastyQ.isLoading) {
    return (
      <section className="dynasty-page dynasty-page--loading">
        <p className="italic-fell">Reading the lineage…</p>
      </section>
    );
  }

  if (dynastyQ.isError || !dynastyQ.data) {
    return (
      <section className="dynasty-page dynasty-page--empty">
        <h1 className="dynasty-page__empty-title">No dynasty resolved</h1>
        <p className="italic-fell">
          Adopt a save first, or wait for the next save-tail tick to
          populate the dynasty roster.
        </p>
      </section>
    );
  }

  const data = dynastyQ.data;
  const trackedIds = new Set(
    (trackedQ.data ?? []).map((t) => t.character_id),
  );
  const palette = paletteQ.data ?? null;

  const openCharacter = (ck3Id: number): void => {
    setSelectedCharacterId(ck3Id);
    setView('chronicle');
  };

  return (
    <section className="dynasty-page">
      <DynastyHero data={data} palette={palette} />
      <LineageStrip
        members={data.members}
        palette={palette}
        trackedIds={trackedIds}
        onOpen={openCharacter}
      />
      <VitaRoll
        roll={data.vita_roll}
        onOpen={openCharacter}
      />
    </section>
  );
}

interface DynastyHeroProps {
  data: DynastyResponse;
  palette: Palette | null;
}

function DynastyHero({ data, palette }: DynastyHeroProps): React.JSX.Element {
  const head = data.current_head ?? data.founder;
  const heroSeed = head?.ck3_id ?? data.dynasty_name;
  const founding = data.founding_date
    ? `Founded ${data.founding_date}`
    : 'Founding date unknown';
  const heads = data.member_count;
  const chronicled = data.chronicled_count;
  return (
    <header className="dynasty-hero">
      <div className="dyn-wall-hero">
        <HeraldicCartouche size={300}>
          <HeraldryWithFallback
            coa={head?.coa_json ?? null}
            palette={palette}
            seed={heroSeed}
            size={220}
            ring
          />
        </HeraldicCartouche>
      </div>
      <div className="dynasty-hero__body">
        <div className="smallcaps dynasty-hero__eyebrow">Of the dynasty of</div>
        <h1 className="dynasty-hero__name">
          {data.dynasty_name.toUpperCase()}
        </h1>
        <p className="dynasty-hero__summary">
          {founding} · {heads} member{heads === 1 ? '' : 's'} ·{' '}
          {chronicled} chronicled soul{chronicled === 1 ? '' : 's'}
        </p>
        {data.founding_paragraph && (
          <p className="italic-fell dynasty-hero__paragraph">
            {data.founding_paragraph}
          </p>
        )}
      </div>
    </header>
  );
}

interface LineageStripProps {
  members: DynastyMember[];
  palette: Palette | null;
  trackedIds: Set<number>;
  onOpen: (ck3Id: number) => void;
}

function LineageStrip({
  members,
  palette,
  trackedIds,
  onOpen,
}: LineageStripProps): React.JSX.Element {
  if (members.length === 0) {
    return (
      <section className="dynasty-strip dynasty-strip--empty">
        <p className="italic-fell">No lineage members yet.</p>
      </section>
    );
  }
  return (
    <section className="dynasty-strip">
      <h2 className="smallcaps dynasty-strip__title">Lineage</h2>
      <ol className="dynasty-strip__list">
        {members.map((m) => (
          <LineageRow
            key={m.ck3_id}
            member={m}
            palette={palette}
            isTracked={trackedIds.has(m.ck3_id)}
            onOpen={() => onOpen(m.ck3_id)}
          />
        ))}
      </ol>
    </section>
  );
}

interface LineageRowProps {
  member: DynastyMember;
  palette: Palette | null;
  isTracked: boolean;
  onOpen: () => void;
}

function LineageRow({
  member,
  palette,
  isTracked,
  onOpen,
}: LineageRowProps): React.JSX.Element {
  const lifespan = formatLifespan(member.birth_date, member.death_date);
  const role = member.is_player
    ? 'Current head'
    : member.death_date
      ? 'Departed'
      : 'Living';
  return (
    <li
      className={
        'dynasty-row' +
        (isTracked ? ' dynasty-row--tracked' : '') +
        (member.is_player ? ' dynasty-row--player' : '')
      }
    >
      <div className="dynasty-row__shield">
        <HeraldryWithFallback
          coa={member.coa_json}
          palette={palette}
          seed={member.ck3_id}
          size={48}
        />
      </div>
      <div className="dynasty-row__body">
        <div className="dynasty-row__name">
          {member.first_name ?? `character ${member.ck3_id}`}
          {member.nickname && (
            <span className="italic-fell dynasty-row__epithet">
              {' '}— {member.nickname}
            </span>
          )}
        </div>
        <div className="dynasty-row__meta">
          <span className="dynasty-row__lifespan">{lifespan}</span>
          <span className="dynasty-row__role smallcaps">{role}</span>
          {member.has_biography && (
            <span className="dynasty-row__bio-pip" title="Biography written">
              ✓ vita
            </span>
          )}
        </div>
      </div>
      <div className="dynasty-row__actions">
        <button type="button" className="btn btn--quiet" onClick={onOpen}>
          Open folio
        </button>
      </div>
    </li>
  );
}

interface VitaRollProps {
  roll: DynastyVitaEntry[];
  onOpen: (ck3Id: number) => void;
}

function VitaRoll({ roll, onOpen }: VitaRollProps): React.JSX.Element {
  if (roll.length === 0) {
    return (
      <section className="dynasty-vita dynasty-vita--empty">
        <h2 className="smallcaps dynasty-vita__title">Vita roll</h2>
        <p className="italic-fell">
          No biographies have been inscribed yet — once a tracked soul
          dies, their vita will land here.
        </p>
      </section>
    );
  }
  return (
    <section className="dynasty-vita">
      <h2 className="smallcaps dynasty-vita__title">Vita roll</h2>
      <ul className="dynasty-vita__list">
        {roll.map((v) => (
          <li key={v.ck3_id} className="dynasty-vita__entry">
            <header className="dynasty-vita__head">
              <h3 className="dynasty-vita__name">
                {v.first_name ?? `character ${v.ck3_id}`}
                {v.nickname && (
                  <span className="italic-fell dynasty-vita__epithet">
                    {' '}— {v.nickname}
                  </span>
                )}
              </h3>
              {v.death_date && (
                <span className="dynasty-vita__death">
                  Died {v.death_date}
                </span>
              )}
            </header>
            <p className="dynasty-vita__excerpt">{v.biography_excerpt}</p>
            <button
              type="button"
              className="btn btn--quiet dynasty-vita__open"
              onClick={() => onOpen(v.ck3_id)}
            >
              Open folio
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatLifespan(
  birth: string | null,
  death: string | null,
): string {
  if (!birth && !death) return 'Dates unknown';
  const left = birth ?? '?';
  const right = death ?? '—';
  return `${left} — ${right}`;
}

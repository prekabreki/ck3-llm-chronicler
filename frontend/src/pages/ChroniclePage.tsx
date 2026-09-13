// ChroniclePage — the centerpiece. Folio spread: left aside (heraldry +
// vitals + kin), right page (Vita / Events tabs).
//
// Audit F-27: this file used to bundle 4 panels + 6 helpers in 972
// lines. Split into ./chronicle/{ChronicleAside,VitaPanel,
// EventsPanel,Ornaments,formatters}; what's left here
// is the data-fetch shell + the folio frame that wires the panels
// to their props.

import { useState } from 'react';

import {
  queryKeys,
  useBiography,
  useCharacterCoa,
  useCharacterDetail,
  useFamilyTree,
  useNarrativeInvalidation,
  usePalette,
} from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { CoaDefinition, Palette } from '../components/CoaTypes';
import type {
  BiographyResponse,
  CharacterDetail,
  FamilyTreeResponse,
} from '../api/types';
import { ChronicleAside } from './chronicle/ChronicleAside';
import { EventsPanel } from './chronicle/EventsPanel';
import { CornerOrnamentFrame } from '../components/Ornaments';
import { HeraldryWithFallback } from '../components/RealHeraldry';
import { VitaPanel } from './chronicle/VitaPanel';
import { prettyProvider } from './chronicle/formatters';
import { formatRelativeIso } from '../util/format';

type TabId = 'vita' | 'events';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign. The
// no-soul guard below stays — it's a distinct routing concern.
export function ChroniclePage({ campaignName }: { campaignName: string }): React.JSX.Element {
  const setView = useAppStore((s) => s.setView);
  const selectedCharacterId = useAppStore((s) => s.selectedCharacterId);

  if (selectedCharacterId === null) {
    return (
      <div className="chronicle-page chronicle-page--empty">
        <p className="italic-fell">
          No soul chosen. Return to the{' '}
          <button
            type="button"
            className="link"
            onClick={() => setView('codex')}
          >
            Codex
          </button>{' '}
          and pick a character to chronicle.
        </p>
      </div>
    );
  }

  return (
    <ChroniclePageBody
      campaignName={campaignName}
      ck3Id={selectedCharacterId}
    />
  );
}

interface ChroniclePageBodyProps {
  campaignName: string;
  ck3Id: number;
}

function ChroniclePageBody({
  campaignName,
  ck3Id,
}: ChroniclePageBodyProps): React.JSX.Element {
  // Fire all queries in parallel — useBiography / useFamilyTree don't
  // depend on the detail response, only on the campaign + ck3_id, so
  // there's no reason to gate them on detailQ. (Earlier structures put
  // them inside the post-detail-load sub-component which serialised the
  // network — see ck3_chronicler-gcn.)
  const detailQ = useCharacterDetail(campaignName, ck3Id);
  const bioQ = useBiography(campaignName, ck3Id);
  const treeQ = useFamilyTree(campaignName, ck3Id);
  // ck3_chronicler-7ao: per-character resolved CoA + the process-static
  // palette. Both feed the heraldry medallion in the aside; missing
  // either flips it to the procedural fallback shield.
  const coaQ = useCharacterCoa(campaignName, ck3Id);
  const paletteQ = usePalette();

  // ck3_chronicler-7gw: refetch this character's biography when a
  // narrative_* SSE frame arrives — covers both regenerate clicks and
  // save-tail-driven death biographies landing while the user is on
  // this folio.
  useNarrativeInvalidation(campaignName, (qc) => {
    void qc.invalidateQueries({
      queryKey: queryKeys.biography(campaignName, ck3Id),
    });
  });

  if (detailQ.isLoading) {
    return (
      <div className="chronicle-page chronicle-page--empty">
        <p className="italic-fell">Drawing the folio…</p>
      </div>
    );
  }

  if (detailQ.isError || !detailQ.data) {
    return (
      <div className="chronicle-page chronicle-page--empty">
        <p
          className="italic-fell chronicle-page__error"
          role="alert"
        >
          Could not read this folio.
        </p>
      </div>
    );
  }

  // Fan out to a sub-component once detail data is ready so the
  // initial-tab decision (ck3_chronicler-gcn) can read death_date
  // synchronously instead of fighting useState's once-per-mount semantics.
  return (
    // audit L35 / ck3_chronicler-27ov.81: key by ck3Id so switching
    // characters remounts the folio — otherwise the Vita/Events tab state
    // survives the switch and defeats the gcn alive-defaults-to-events rule.
    <ChronicleFolio
      key={ck3Id}
      campaignName={campaignName}
      character={detailQ.data}
      bio={bioQ.data ?? null}
      bioLoading={bioQ.isLoading}
      tree={treeQ.data ?? null}
      coa={coaQ.data ?? null}
      palette={paletteQ.data ?? null}
    />
  );
}

interface ChronicleFolioProps {
  campaignName: string;
  character: CharacterDetail;
  bio: BiographyResponse | null;
  bioLoading: boolean;
  tree: FamilyTreeResponse | null;
  coa: CoaDefinition | null;
  palette: Palette | null;
}

function ChronicleFolio({
  campaignName,
  character: c,
  bio,
  bioLoading,
  tree,
  coa,
  palette,
}: ChronicleFolioProps): React.JSX.Element {
  // Task 12: living characters default to Event roll (substantive content
  // that always exists). Dead characters default to Vita (the bio).
  const isAlive = !c.death_date;
  const [tab, setTab] = useState<TabId>(isAlive ? 'events' : 'vita');

  // ck3_chronicler-gcn: surface the pending state on the Vita tab itself
  // for living characters so the user knows the empty state is intentional.
  const vitaLabel = isAlive ? 'Vita (pending)' : 'Vita';

  return (
    <div className="chronicle-page">
      <div className="chronicle-page__inner">
        <div className="chronicle-page__folio-bar">
          <div className="folio">Folio · recto</div>
          <div className="ribbon">
            {c.dynasty_name ? `Liber ${c.dynasty_name}` : 'Chronicle'}
          </div>
          <div className="folio">
            {c.death_date
              ? c.death_date.split('.')[0]
              : c.birth_date
                ? c.birth_date.split('.')[0]
                : '——'}
          </div>
        </div>

        <div className="paper paper--edged chronicle-page__folio">
          <div className="chronicle-page__gutter" aria-hidden />

          <ChronicleAside
            campaignName={campaignName}
            character={c}
            tree={tree}
            coa={coa}
            palette={palette}
          />

          <section className="chronicle-page__main ornamented">
            <CornerOrnamentFrame variant="vine" size={64} opacity={0.7} />
            <div className="chronicle-page__watermark" aria-hidden>
              <HeraldryWithFallback
                coa={coa}
                palette={palette}
                seed={c.ck3_id}
                size={360}
                label=""
              />
            </div>

            <div className="chronicle-page__title-block">
              <div className="smallcaps chronicle-page__eyebrow">
                The chronicler's hand
              </div>
              <h1 className="uncial chronicle-page__title">
                The Life of{' '}
                {c.first_name ?? `Character ${c.ck3_id}`}
                {c.nickname && (
                  <>
                    , called{' '}
                    <span className="chronicle-page__title-epithet">
                      {c.nickname}
                    </span>
                  </>
                )}
              </h1>
              {bio && (
                <div className="italic-fell chronicle-page__byline">
                  Set down by {prettyProvider(bio.provider)}, version{' '}
                  {bio.version}.
                </div>
              )}
            </div>

            <div
              className="tabs chronicle-page__tabs"
              role="tablist"
              aria-label="Chronicle sections"
            >
              {(
                [
                  { id: 'vita', label: vitaLabel },
                  { id: 'events', label: 'Event roll' },
                ] as { id: TabId; label: string }[]
              ).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="tab"
                  role="tab"
                  aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            {tab === 'vita' && (
              <VitaPanel
                bio={bio}
                loading={bioLoading}
                isAlive={isAlive}
                campaignName={campaignName}
                ck3Id={c.ck3_id}
                firstName={c.first_name}
              />
            )}
            {tab === 'events' && <EventsPanel events={c.events} />}
          </section>
        </div>

        <div className="chronicle-page__foot">
          <div className="folio">— xxiv —</div>
          {bio && (
            <div className="folio chronicle-page__foot-source italic-fell">
              {prettyProvider(bio.provider)} ·{' '}
              {formatRelativeIso(bio.generated_at)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

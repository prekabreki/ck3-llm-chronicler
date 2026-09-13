// LibraryPage — campaign shelf. Polish pass: hero frontispiece, gilded
// counts, corner ornaments. Wires to GET /api/campaigns?include_counts=true.
// Selecting a campaign sets it active and routes to campaign overview.
//
// audit M-F3 / ck3_chronicler-27ov.58: the campaign card (with its rename/
// reset-baseline/delete flows) and the byline/span formatters live in
// ./library/* now; this file is the page shell — frontispiece, section
// headers, toolbar, and the active/completed grids.

import { useState } from 'react';

import { AdoptSaveModal } from '../components/AdoptSaveModal';
import type { Palette } from '../components/CoaTypes';
import { Heraldry } from '../components/Heraldry';
import { ImportModal } from '../components/ImportModal';
import { MigrationBanner } from '../components/MigrationBanner';
import {
  CornerOrnamentFrame,
  FleuronRule,
  HeraldicCartouche,
} from '../components/Ornaments';
import { RealHeraldry } from '../components/RealHeraldry';
import {
  useCampaigns,
  useMigrationStatus,
  usePalette,
} from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { CampaignResponse } from '../api/types';
import { CampaignCard } from './library/CampaignCard';

export function LibraryPage(): React.JSX.Element {
  const { data, isLoading, isError, error, failureCount } = useCampaigns({
    includeCounts: true,
    includeArchived: true,
  });
  const { data: palette } = usePalette();
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const [importingFor, setImportingFor] = useState<string | null>(null);
  const [adoptOpen, setAdoptOpen] = useState(false);

  const campaigns = data ?? [];
  const active = campaigns.filter((c) => !c.archived);
  const completed = campaigns.filter((c) => c.archived);

  const migrationStatusQ = useMigrationStatus();
  const needsMigrationIds = new Set(
    migrationStatusQ.data?.needs_migration.map((p) => p.campaign_id) ?? [],
  );

  const onOpen = (c: CampaignResponse): void => {
    setActiveCampaign(c.name, 'campaign-overview');
  };
  const onImport = (c: CampaignResponse): void => {
    setImportingFor(c.name);
  };

  // Hero arms: prefer the most recently-touched active campaign so the
  // frontispiece feels alive rather than picking a random shelf entry.
  const heroCampaign =
    [...active].sort((a, b) => {
      const aT = a.last_event_at ? Date.parse(a.last_event_at) : 0;
      const bT = b.last_event_at ? Date.parse(b.last_event_at) : 0;
      return bT - aT;
    })[0] ?? null;

  return (
    <div className="library-page">
      <div className="library-page__inner">
        <MigrationBanner />

        <LibraryFrontispiece heroCampaign={heroCampaign} palette={palette ?? null} />

        <FleuronRule variant="stars" />

        {isLoading && (
          <p className="italic-fell library-page__status">
            {failureCount > 0
              ? `Waiting for the chronicler to wake (attempt ${failureCount + 1})…`
              : 'Drawing the volumes from the shelf…'}
          </p>
        )}

        {isError && (
          <p
            className="italic-fell library-page__status library-page__status--error"
            role="alert"
          >
            The shelf is silent: {error instanceof Error ? error.message : 'unknown error'}
          </p>
        )}

        {!isLoading && !isError && (
          <>
            <SectionHeader title="Active" count={active.length} />
            <LibraryToolbar onAdopt={() => setAdoptOpen(true)} />
            <div className="lib-grid">
              {active.length === 0 ? (
                <div className="library-page__empty">
                  <p className="italic-fell">
                    No active campaigns yet. Drop a CK3 save into your save
                    folder, or click <strong>+ Adopt save</strong> above to
                    point chronicler at a specific file.
                  </p>
                </div>
              ) : (
                active.map((c) => (
                  <CampaignCard
                    key={c.id}
                    campaign={c}
                    palette={palette ?? null}
                    onOpen={() => onOpen(c)}
                    onImport={() => onImport(c)}
                    needsMigration={needsMigrationIds.has(c.id)}
                  />
                ))
              )}
              <AdoptCtaCard onClick={() => setAdoptOpen(true)} />
            </div>

            {completed.length > 0 && (
              <div className="library-page__completed">
                <SectionHeader title="Completed" count={completed.length} />
                <div className="lib-grid">
                  {completed.map((c) => (
                    <CampaignCard
                      key={c.id}
                      campaign={c}
                      palette={palette ?? null}
                      onOpen={() => onOpen(c)}
                      completed
                      needsMigration={needsMigrationIds.has(c.id)}
                    />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
      {importingFor && (
        <ImportModal
          campaignName={importingFor}
          open
          onClose={() => setImportingFor(null)}
        />
      )}
      {adoptOpen && <AdoptSaveModal open onClose={() => setAdoptOpen(false)} />}
    </div>
  );
}

// Hero frontispiece — the polish-pass headline moment for the
// chronicler's front door. A large cartouche-framed shield on the left
// (the most recently-touched campaign's arms, falling back to the
// chronicler's house mark when no campaigns exist yet), a big display
// title, an italic lede, and a meta line with the active/completed
// tally. Four corner ornaments anchor the band.
function LibraryFrontispiece({
  heroCampaign,
  palette,
}: {
  heroCampaign: CampaignResponse | null;
  palette: Palette | null;
}): React.JSX.Element {
  const armsLabel = heroCampaign
    ? `Arms of ${heroCampaign.current_player_name ?? heroCampaign.name}`
    : 'The chronicler\'s mark';
  return (
    <section className="hero-band ornamented library-frontispiece" aria-label="The Library">
      <CornerOrnamentFrame variant="vine" size={84} opacity={0.8} />
      <div className="hero-band__shield">
        <HeraldicCartouche size={240}>
          {heroCampaign?.current_player_coa_json && palette ? (
            <RealHeraldry
              coa={heroCampaign.current_player_coa_json}
              palette={palette}
              size={180}
              ring
              label={armsLabel}
            />
          ) : heroCampaign ? (
            <Heraldry seed={heroCampaign.name} size={180} ring label={armsLabel} />
          ) : (
            <Heraldry seed="the chronicler" size={180} ring label={armsLabel} />
          )}
        </HeraldicCartouche>
      </div>
      <div className="hero-band__body">
        <div className="smallcaps hero-band__eyebrow">The Chronicler's Library</div>
        <h1 className="hero-band__title">Campaigns &amp; Chronicles</h1>
        <p className="hero-band__lede">
          Each campaign carries its own per-campaign codex of characters and
          biographies. Open a volume to step into its chronicle.
        </p>
        {heroCampaign && (
          <div className="hero-band__meta">
            <span>
              <span className="hero-band__meta-key">Lately</span>
              {heroCampaign.name}
            </span>
            {heroCampaign.current_in_game_date && (
              <>
                <span className="hero-band__meta-sep">·</span>
                <span>
                  <span className="hero-band__meta-key">Tide</span>
                  {heroCampaign.current_in_game_date}
                </span>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

interface SectionHeaderProps {
  title: string;
  count: number;
}

function SectionHeader({ title, count }: SectionHeaderProps): React.JSX.Element {
  return (
    <div className="library-section-header">
      <span className="library-section-header__glyph" aria-hidden>✦</span>
      <h2 className="library-section-header__title">{title}</h2>
      <span className="library-section-header__count">
        {count} {count === 1 ? 'campaign' : 'campaigns'}
      </span>
      <span className="library-section-header__rule" aria-hidden />
    </div>
  );
}

function LibraryToolbar({ onAdopt }: { onAdopt: () => void }): React.JSX.Element {
  return (
    <div className="library-toolbar">
      <button
        type="button"
        className="btn library-toolbar__primary"
        onClick={onAdopt}
      >
        + Adopt save
      </button>
      <span className="library-toolbar__kbd" aria-hidden>⌘K</span>
    </div>
  );
}

function AdoptCtaCard({ onClick }: { onClick: () => void }): React.JSX.Element {
  return (
    <button
      type="button"
      className="adopt-card"
      onClick={onClick}
      aria-label="Adopt a save"
    >
      <span className="adopt-card__glyph" aria-hidden>+</span>
      <span className="adopt-card__title">Adopt a save</span>
      <span className="adopt-card__sub italic-fell">
        Point chronicler at a CK3 save file to begin a new campaign.
      </span>
    </button>
  );
}

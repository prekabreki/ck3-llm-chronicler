// CampaignOverviewPage — landing page after a Library card click.
// Polish pass: cartouche-framed hero, watermark ghost-shield behind the
// band, corner ornaments at all four corners. Big heraldry + dynasty
// stats + nav into the campaign's per-character / per-dynasty surfaces.

import { useCampaign, usePalette, useTracked } from '../api/queries';
import { Banner, Heraldry } from '../components/Heraldry';
import {
  CornerOrnamentFrame,
  HeraldicCartouche,
  FleuronRule,
} from '../components/Ornaments';
import { RealHeraldry } from '../components/RealHeraldry';
import { useAppStore } from '../store/appStore';
import { formatYearSpan } from '../util/format';
import type { ViewId } from '../store/appStore';
import type { CampaignResponse } from '../api/types';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function CampaignOverviewPage({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const campaignQ = useCampaign(campaignName, true);
  const trackedQ = useTracked(campaignName);
  const setView = useAppStore((s) => s.setView);

  if (campaignQ.isLoading) {
    return (
      <div className="overview-page">
        <p className="italic-fell overview-page__status">
          Drawing the campaign…
        </p>
      </div>
    );
  }
  if (campaignQ.isError || !campaignQ.data) {
    return (
      <div className="overview-page">
        <p className="italic-fell overview-page__status" role="alert">
          Could not read this campaign. Return to the{' '}
          <button
            type="button"
            className="link"
            onClick={() => setView('library')}
          >
            Library
          </button>
          .
        </p>
      </div>
    );
  }

  const campaign = campaignQ.data;
  const trackedCount = trackedQ.data?.length ?? null;

  return (
    <div className="overview-page">
      <div className="overview-page__inner">
        <Hero campaign={campaign} />
        <FleuronRule variant="trefoil" />
        <StatsStrip campaign={campaign} trackedCount={trackedCount} />
        <NavPanel campaign={campaign} onNavigate={setView} />
      </div>
    </div>
  );
}

function Hero({ campaign }: { campaign: CampaignResponse }): React.JSX.Element {
  const { data: palette } = usePalette();
  const dynasty =
    campaign.current_house_name ?? campaign.founding_dynasty_name ?? null;
  const founded =
    campaign.bookmark_date ?? campaign.created_at?.split('T')[0] ?? null;
  const inGameLast = campaign.current_in_game_date ?? null;
  const wallClockLast = campaign.last_event_at?.split('T')[0] ?? null;
  const lastDisplay = inGameLast ?? wallClockLast;
  const currentRuler = campaign.current_player_name ?? null;
  const nickname = campaign.current_player_nickname ?? null;
  const rulerLine = currentRuler
    ? nickname
      ? `${currentRuler}, called ${nickname}`
      : currentRuler
    : null;
  const span = formatYearSpan(founded, inGameLast);
  const armsLabel = `Arms of ${dynasty ?? campaign.name}`;

  return (
    <section className="hero-band ornamented overview-hero paper--deckle">
      <CornerOrnamentFrame variant="knot" size={88} opacity={0.78} />
      {/* Giant ghosted heraldry watermark behind the band */}
      <div className="heraldry-watermark overview-hero__watermark" aria-hidden>
        {campaign.current_player_coa_json && palette ? (
          <RealHeraldry
            coa={campaign.current_player_coa_json}
            palette={palette}
            size={520}
            label=""
          />
        ) : (
          <Heraldry seed={campaign.name} size={520} label="" />
        )}
      </div>

      <div className="hero-band__shield" aria-hidden>
        <HeraldicCartouche size={260}>
          {campaign.current_player_coa_json && palette ? (
            <RealHeraldry
              coa={campaign.current_player_coa_json}
              palette={palette}
              size={200}
              ring
              label={armsLabel}
            />
          ) : (
            <Banner seed={campaign.name} size={180} />
          )}
        </HeraldicCartouche>
      </div>

      <div className="hero-band__body">
        <div className="smallcaps hero-band__eyebrow">
          {campaign.archived ? 'Sealed campaign' : 'Active campaign'}
        </div>
        <h1 className="hero-band__title overview-hero__title">
          {dynasty ?? campaign.name}
        </h1>
        {rulerLine && (
          <div className="italic-fell overview-hero__ruler">{rulerLine}</div>
        )}
        <div className="hero-band__meta overview-hero__summary">
          {founded && (
            <span>
              <span className="hero-band__meta-key">Founded</span>
              {founded}
            </span>
          )}
          {lastDisplay && (
            <>
              <span className="hero-band__meta-sep">·</span>
              <span>
                <span className="hero-band__meta-key">
                  {campaign.archived ? 'Sealed' : 'Last seen'}
                </span>
                {lastDisplay}
              </span>
            </>
          )}
          {span && (
            <>
              <span className="hero-band__meta-sep">·</span>
              <span>
                <span className="hero-band__meta-key">Span</span>
                {span}
              </span>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function StatsStrip({
  campaign,
  trackedCount,
}: {
  campaign: CampaignResponse;
  trackedCount: number | null;
}): React.JSX.Element {
  const counts = campaign.counts;
  return (
    <section className="overview-stats">
      <Stat label="Souls" value={counts ? counts.characters : null} />
      <Stat label="Tracked" value={trackedCount} />
      <Stat label="Vitæ" value={counts ? counts.biographies : null} />
      <Stat label="Gold" value={formatStat(campaign.current_player_gold)} />
      <Stat
        label="Prestige"
        value={formatStat(campaign.current_player_prestige_lifetime)}
      />
      <Stat label="Piety" value={formatStat(campaign.current_player_piety_lifetime)} />
      <Stat label="Renown" value={formatStat(campaign.current_dynasty_renown)} />
    </section>
  );
}

function formatStat(value: number | null): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return null;
  }
  return Math.round(value).toLocaleString('en-US');
}

function Stat({
  label,
  value,
}: {
  label: string;
  value: number | string | null;
}): React.JSX.Element {
  return (
    <div className="overview-stat">
      <div className="overview-stat__n">{value ?? '—'}</div>
      <div className="smallcaps overview-stat__l">{label}</div>
    </div>
  );
}

interface NavSlot {
  view: ViewId;
  label: string;
  description: string;
}

function NavPanel({
  campaign,
  onNavigate,
}: {
  campaign: CampaignResponse;
  onNavigate: (view: ViewId) => void;
}): React.JSX.Element {
  const closingSlot: NavSlot = campaign.archived
    ? {
        view: 'closing',
        label: 'Read closing chronicle',
        description:
          'The dynasty\'s sealed chronicle and final tally.',
      }
    : {
        view: 'closing',
        label: 'Closing ceremony',
        description:
          'Wrap the campaign and seal the chronicle when you\'re ready.',
      };

  const slots: NavSlot[] = [
    ...(campaign.archived
      ? []
      : [
          {
            view: 'codex' as const,
            label: 'Codex of Souls',
            description: 'Browse every character the campaign has touched.',
          },
        ]),
    {
      view: 'biographies',
      label: 'Biographies',
      description: 'Every chronicled life in this campaign, in order.',
    },
    {
      view: 'tracked',
      label: 'Tracked',
      description: "The souls you've opted into for biographies.",
    },
    {
      view: 'tree',
      label: 'Lineage',
      description: 'Family tree across the player line.',
    },
    {
      view: 'dynasty',
      label: 'Dynasty',
      description: 'House heraldry and dynastic membership.',
    },
    closingSlot,
  ];

  return (
    <section className="overview-nav" aria-label="Campaign sections">
      {slots.map((s) => (
        <button
          type="button"
          key={s.view + ':' + s.label}
          className="overview-nav__slot"
          onClick={() => onNavigate(s.view)}
        >
          <span className="overview-nav__slot-glyph" aria-hidden>✦</span>
          <span className="overview-nav__slot-label">{s.label}</span>
          <span className="italic-fell overview-nav__slot-desc">
            {s.description}
          </span>
        </button>
      ))}
    </section>
  );
}


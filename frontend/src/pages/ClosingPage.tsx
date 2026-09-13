// ClosingPage — vysp.12 reskin: 200px hero shield (heaviest drop-shadow
// stack, ringed) + small-caps dynasty line + display-56 name + mono
// "Founded · Closed · Span" summary; double-rule between hero and the
// chronicle prose; carmine-tinted action card with the destructive
// "Affix wax seal" button. When no chronicle exists yet, the action
// card carries the two-step seal CTA.

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { completeCampaign, getClosingChronicle } from '../api/client';
import { queryKeys, useCampaign, usePalette } from '../api/queries';
import { Banner } from '../components/Heraldry';
import { RealHeraldry } from '../components/RealHeraldry';
import {
  CornerOrnamentFrame,
  HeraldicCartouche,
} from '../components/Ornaments';
import { ExportChronicleButton } from '../components/ExportChronicleButton';
import { useAppStore } from '../store/appStore';
import { formatDate, formatYearSpan } from '../util/format';
import type { CampaignResponse, ClosingChronicleResponse } from '../api/types';

// audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate lives in App's
// view switch now; this page is only mounted with a non-null campaign.
export function ClosingPage({
  campaignName,
}: {
  campaignName: string;
}): React.JSX.Element {
  const qc = useQueryClient();
  // ck3_chronicler-8e0w: after the user seals a chronicle, the active
  // campaign should clear so the chrome's character pill + campaign-
  // scoped nav drop back to a no-campaign state. The user explicitly
  // 'closed' this campaign — keeping it pinned in the header was sticky-
  // state behaviour they reported as broken.
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const campaignQ = useCampaign(campaignName, true);
  const chronicleQ = useQuery({
    queryKey: queryKeys.closingChronicle(campaignName),
    queryFn: () => getClosingChronicle(campaignName),
  });
  const completeMu = useMutation({
    mutationFn: () => completeCampaign(campaignName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.closingChronicle(campaignName) });
      qc.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.campaignAll(campaignName) });
      // ck3_chronicler-8e0w: clear the pinned campaign and route to
      // Library. setActiveCampaign(null) auto-selects the library view
      // via its default-view arg (appStore.ts:113).
      setActiveCampaign(null);
    },
  });

  // F-60: BE returns null (200) when no chronicle yet, so chronicle ===
  // null is the canonical "show seal CTA" signal — no separate 404 branch.
  const chronicle: ClosingChronicleResponse | null = chronicleQ.data ?? null;

  return (
    <div className="closing-page">
      <div className="closing-page__inner">
        <Frontispiece campaign={campaignQ.data ?? null} chronicle={chronicle} />

        {chronicle && <ChronicleProse chronicle={chronicle} />}
        {chronicle && (
          <div className="closing-page__export">
            <ExportChronicleButton campaignName={campaignName} format="markdown" />
            <ExportChronicleButton campaignName={campaignName} format="pdf" />
          </div>
        )}

        {!chronicle && !chronicleQ.isLoading && (
          <SealCta
            campaignName={campaignName}
            onSeal={() => completeMu.mutate()}
            pending={completeMu.isPending}
            error={
              completeMu.isError
                ? (completeMu.error as Error).message
                : null
            }
          />
        )}

        {chronicleQ.isLoading && (
          <p className="italic-fell closing-page__status">
            Drawing the chronicle…
          </p>
        )}
      </div>
    </div>
  );
}

function Frontispiece({
  campaign,
}: {
  campaign: CampaignResponse | null;
  // chronicle prop intentionally unused — the frontispiece reads the
  // dynasty + ruler from the campaign row, not the chronicle body.
  // Kept for source-shape parity with sibling components if ever needed.
  chronicle: ClosingChronicleResponse | null;
}): React.JSX.Element {
  const { data: palette } = usePalette();
  const dynasty =
    campaign?.current_house_name ??
    campaign?.founding_dynasty_name ??
    null;
  // ck3_chronicler-7xrj: the closing frontispiece is a dynastic
  // headstone, not a portrait of the latest character. Title is the
  // dynasty / house; the current ruler appears in a smaller line below.
  const lastRuler = campaign?.current_player_name ?? null;
  const title = dynasty ?? campaign?.name ?? lastRuler ?? '—';
  const founded = campaign?.bookmark_date ?? campaign?.created_at?.split('T')[0] ?? '—';
  // ck3_chronicler-9xa6: prefer the in-game date column. The previous
  // implementation read last_event_at and stripped the time off — but
  // last_event_at is wall-clock (datetime.now(UTC) at ingest), so the
  // span resolved to "1066 → 2026 = 960 years". last_event_in_game_date
  // is populated by save-tail on every tick (and backfilled from
  // events.event_date_iso for legacy rows); falling back to current_in_game_date
  // covers the edge case of an old row whose backfill hasn't run yet.
  const closed: string | null =
    campaign?.last_event_in_game_date ??
    campaign?.current_in_game_date ??
    null;
  const span = formatYearSpan(founded, closed);

  return (
    <section className="paper paper--edged closing-frontispiece ornamented">
      <CornerOrnamentFrame variant="scroll" size={84} opacity={0.78} />
      <div className="closing-frontispiece__inner">
        <div className="closing-frontispiece__shield-wrap">
          <HeraldicCartouche size={260}>
            <div className="closing-frontispiece__shield" aria-hidden>
              {campaign?.current_player_coa_json && palette ? (
                <RealHeraldry
                  coa={campaign.current_player_coa_json}
                  palette={palette}
                  size={200}
                  ring
                  label={`Arms of ${title}`}
                />
              ) : (
                <Banner seed={campaign?.name ?? title} size={200} />
              )}
            </div>
          </HeraldicCartouche>
        </div>

        <div className="closing-frontispiece__rule-double" aria-hidden />

        <h1 className="closing-frontispiece__title">{title}</h1>
        {lastRuler && lastRuler !== title && (
          <div className="smallcaps closing-frontispiece__eyebrow">
            Last seen in {lastRuler}'s hand
          </div>
        )}
        <div className="closing-frontispiece__summary">
          <span>
            <span className="closing-frontispiece__summary-key">Founded</span>{' '}
            {founded}
          </span>
          <span className="closing-frontispiece__summary-sep">·</span>
          <span>
            <span className="closing-frontispiece__summary-key">Closed</span>{' '}
            {closed ?? '—'}
          </span>
          {span && (
            <>
              <span className="closing-frontispiece__summary-sep">·</span>
              <span>
                <span className="closing-frontispiece__summary-key">Span</span>{' '}
                {span}
              </span>
            </>
          )}
        </div>

        <div className="closing-frontispiece__rule-double" aria-hidden />
      </div>
    </section>
  );
}

function ChronicleProse({
  chronicle,
}: {
  chronicle: ClosingChronicleResponse;
}): React.JSX.Element {
  const paragraphs = chronicle.body
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);

  return (
    <article className="paper paper--edged closing-prose">
      <div className="smallcaps closing-prose__eyebrow">
        ✦ The chronicle in full
      </div>
      {paragraphs.map((p, i) => (
        <p
          key={i}
          className={
            i === 0 ? 'closing-prose__para dropcap' : 'closing-prose__para'
          }
        >
          {p}
        </p>
      ))}
      <div className="closing-prose__attribution smallcaps">
        Generated {formatDate(chronicle.generated_at)}
        {chronicle.archived ? ' · campaign sealed' : ''}
      </div>
    </article>
  );
}

interface SealCtaProps {
  campaignName: string;
  onSeal: () => void;
  pending: boolean;
  error: string | null;
}

function SealCta({
  campaignName,
  onSeal,
  pending,
  error,
}: SealCtaProps): React.JSX.Element {
  const [confirming, setConfirming] = useState(false);
  return (
    <section className="closing-cta">
      <div className="smallcaps closing-cta__eyebrow">
        ✦ Seal this campaign
      </div>
      <p className="closing-cta__lede">
        No closing chronicle has been generated for{' '}
        <strong>{campaignName}</strong>. Sealing the campaign synthesises every
        tracked-soul biography into a one-page meta-chronicle and archives the
        campaign — a deliberate ritual, not a silent close.
      </p>
      {!confirming && (
        <button
          type="button"
          className="btn closing-cta__button"
          onClick={() => setConfirming(true)}
        >
          Begin closing ceremony…
        </button>
      )}
      {confirming && (
        <div className="closing-cta__confirm">
          <p className="italic-fell">
            This is final. The campaign will be archived once the chronicle is
            written. Continue?
          </p>
          <div className="closing-cta__confirm-buttons">
            <button
              type="button"
              className="btn closing-cta__seal"
              onClick={onSeal}
              disabled={pending}
            >
              {pending ? 'Affixing wax…' : 'Yes, seal it · Affix wax seal'}
            </button>
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => setConfirming(false)}
              disabled={pending}
            >
              Not yet
            </button>
          </div>
        </div>
      )}
      {error && (
        <p
          className="italic-fell closing-cta__error"
          role="alert"
        >
          The chronicler stayed his hand: {error}
        </p>
      )}
    </section>
  );
}


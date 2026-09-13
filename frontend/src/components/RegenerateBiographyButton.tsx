// ck3_chronicler-7gw: "Regenerate biography" trigger surface.
//
// Used from CodexPage's character pane and ChroniclePage's Vita panel.
// Opens a small confirmation modal because regeneration shells out to
// claude --print and can run for a few minutes — accidental clicks
// burn time + tokens. ck3_chronicler-cs1o revived the cost-estimate
// panel (dropped in tbrm.6) now that dual transports exist: a Direct
// API hit is real money, while a Claude Code hit draws monthly
// programmatic pool credit. The panel lets a user back out before
// spending real dollars.

import { useState } from 'react';

import {
  useBiographyCostEstimate,
  useRegenerateBiography,
} from '../api/queries';
import type { BiographyCostEstimate } from '../api/client';
import { formatTokens } from '../util/format';
import { ConfirmDialog } from './ConfirmDialog';

interface RegenerateBiographyButtonProps {
  campaignName: string;
  ck3Id: number;
  characterName: string | null;
  hasExistingBiography: boolean;
  variant?: 'primary' | 'quiet';
}

export function RegenerateBiographyButton({
  campaignName,
  ck3Id,
  characterName,
  hasExistingBiography,
  variant = 'quiet',
}: RegenerateBiographyButtonProps): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutation = useRegenerateBiography(campaignName);
  // Only fetch the cost estimate when the modal is open — saves an
  // endpoint call per character page that nobody clicks Regenerate on.
  const estimateQ = useBiographyCostEstimate(campaignName, ck3Id, {
    enabled: open,
  });

  const subject = characterName ?? `character ${ck3Id}`;
  const label = hasExistingBiography ? 'Regenerate biography' : 'Generate biography';

  const onConfirm = (): void => {
    setError(null);
    mutation.mutate(ck3Id, {
      onSuccess: () => {
        setOpen(false);
      },
      onError: (err) => {
        setError(err instanceof Error ? err.message : String(err));
      },
    });
  };

  const onCancel = (): void => {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
    mutation.reset();
  };

  return (
    <>
      <button
        type="button"
        className={variant === 'primary' ? 'btn' : 'btn btn--quiet'}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
      {open && (
        <ConfirmDialog
          titleId="regen-modal-title"
          eyebrow={label}
          title={
            hasExistingBiography
              ? `Re-inscribe the vita of ${subject}?`
              : `Inscribe a vita for ${subject}?`
          }
          confirmLabel="Confirm"
          busyLabel="Submitting…"
          onConfirm={onConfirm}
          onCancel={onCancel}
          busy={mutation.isPending}
          error={error}
        >
          <p className="italic-fell modal__lede">
            The chronicler will draw fresh ink. Generation typically
            takes one to three minutes; the new vita will land as
            version{' '}
            {hasExistingBiography ? '+ 1' : '1'} once complete and
            will replace the page below.
          </p>
          <CostEstimatePanel
            estimate={estimateQ.data ?? null}
            loading={estimateQ.isLoading}
            error={estimateQ.error}
          />
        </ConfirmDialog>
      )}
    </>
  );
}

interface CostEstimatePanelProps {
  estimate: BiographyCostEstimate | null;
  loading: boolean;
  error: Error | null;
}

// ck3_chronicler-7bi5 (revived by cs1o): render the token + USD estimate
// inside the regenerate modal. Three states: loading (italic
// placeholder), error (silent — never block the user from confirming on
// an estimate failure), or rendered. The "routes to" line names the
// transport so a user can tell a real-money Direct API hit from a
// pool-credit Claude Code hit before spending. When pool_billed is true
// the dollar line reads as informational pool-credit draw (neutral); a
// non-pool paid hit keeps the carmine real-money styling.
function CostEstimatePanel({
  estimate,
  loading,
  error,
}: CostEstimatePanelProps): React.JSX.Element | null {
  if (loading) {
    return (
      <div className="cost-estimate cost-estimate--loading italic-fell">
        Estimating…
      </div>
    );
  }
  if (error || !estimate) {
    return null;
  }

  // cs1o: a non-pool hit with a positive estimate is real money — keep
  // the carmine cost-warning. A pool-billed hit (Claude Code) draws
  // monthly programmatic credit, so it reads neutral/informational.
  const realMoney = !estimate.pool_billed && estimate.est_usd > 0;
  const tier = estimate.would_route_to
    ? estimate.would_route_to.startsWith('anthropic')
      ? 'Direct API'
      : estimate.would_route_to.startsWith('claude-code')
        ? 'Claude Code'
        : 'Local hand'
    : 'Local hand';

  return (
    <div
      className={
        'cost-estimate' +
        (realMoney ? ' cost-estimate--paid' : ' cost-estimate--free')
      }
    >
      <div className="cost-estimate__row">
        <span className="smallcaps cost-estimate__label">Routes to</span>
        <span className="cost-estimate__value">
          {tier}
          {estimate.model && (
            <span className="cost-estimate__model"> · {estimate.model}</span>
          )}
        </span>
      </div>
      <div className="cost-estimate__row">
        <span className="smallcaps cost-estimate__label">Tokens</span>
        <span className="cost-estimate__value cost-estimate__tokens">
          {formatTokens(estimate.estimated_input_tokens)} in ·{' '}
          {formatTokens(estimate.estimated_output_tokens)} out
        </span>
      </div>
      <div className="cost-estimate__row cost-estimate__row--cost">
        <span className="smallcaps cost-estimate__label">Estimated</span>
        <span className="cost-estimate__value cost-estimate__cost">
          {estimate.pool_billed && estimate.est_usd > 0
            ? `~$${estimate.est_usd.toFixed(2)} of monthly pool credit`
            : estimate.est_usd > 0
              ? `~$${estimate.est_usd.toFixed(2)}`
              : 'Free · local hand'}
        </span>
      </div>
      {estimate.method === 'estimate' && (
        <p className="italic-fell cost-estimate__hint">
          First-time generation — based on event count, not a prior run.
          Actual usage may vary.
        </p>
      )}
    </div>
  );
}

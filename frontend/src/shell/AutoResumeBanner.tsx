// ck3_chronicler-n52f: auto-resume banner. Surfaces a campaign_auto_
// resumed SSE frame as a low-key strip with a [Switch] action and a
// [Dismiss]. Modeled on RecoveryBanner — same chrome tier, same a11y
// shape (role=status, aria-live=polite). The Switch action calls
// setActiveCampaign(name, 'codex') from the app store so the user
// can jump into the newly-resumed campaign with one click.
//
// Dismissal model: the banner tracks the last-dismissed seq locally;
// when a NEW frame arrives (drain storm — many foreign saves resolve
// to the same campaign), its seq is greater than the dismissed one
// and the banner re-renders. No setter in useEventStream.

import { useState } from 'react';

import type { AutoResumeState } from '../api/useEventStream';

import './AutoResumeBanner.css';

interface AutoResumeBannerProps {
  campaignName: string | null;
  autoResumed: AutoResumeState | null;
  onSwitch: (campaignName: string) => void;
}

export function AutoResumeBanner({
  campaignName,
  autoResumed,
  onSwitch,
}: AutoResumeBannerProps): React.JSX.Element | null {
  const [dismissedSeq, setDismissedSeq] = useState(0);

  if (!campaignName) return null;
  if (!autoResumed) return null;
  if (autoResumed.seq <= dismissedSeq) return null;
  // Don't surface a "switch to <current campaign>" toast — happens when
  // the SPA is focused on the auto-resumed campaign already.
  if (autoResumed.campaignName === campaignName) return null;

  const handleSwitch = (): void => {
    onSwitch(autoResumed.campaignName);
    setDismissedSeq(autoResumed.seq);
  };
  const handleDismiss = (): void => {
    setDismissedSeq(autoResumed.seq);
  };

  return (
    <div className="auto-resume-banner" role="status" aria-live="polite">
      <span className="auto-resume-banner__label">Auto-resumed</span>
      <span className="auto-resume-banner__sep">·</span>
      <span className="auto-resume-banner__campaign">
        {autoResumed.campaignName}
      </span>
      <span className="auto-resume-banner__sep">·</span>
      <span className="auto-resume-banner__detail">
        a save from this campaign just landed
        {autoResumed.inGameDate ? ` (${autoResumed.inGameDate})` : ''}
      </span>
      <span className="auto-resume-banner__spacer" />
      <button
        type="button"
        className="auto-resume-banner__switch"
        onClick={handleSwitch}
      >
        Switch
      </button>
      <button
        type="button"
        className="auto-resume-banner__dismiss"
        onClick={handleDismiss}
        aria-label="Dismiss auto-resume notification"
      >
        ×
      </button>
    </div>
  );
}

// ck3_chronicler-cs1o (ma96 slice 4, finally built): TokenMeter — a
// compact appbar strip chip showing how much of the monthly programmatic
// credit pool the narrative generation has burned. Reads month_usd vs
// target_monthly_usd from the session-summary endpoint (polled at 15s by
// useSessionSummary, which was built for exactly this component) and
// escalates an accent band as the ratio crosses 75 / 100 / 150%.
//
// Self-contained: takes no props, reads the active campaign from the
// store (the same activeCampaign App.tsx threads into AppShell), so it
// drops in next to the other appbar chips and renders nothing when no
// campaign is in play.

import { useEffect, useRef } from 'react';

import { useSessionSummary } from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { CostSessionSummary } from '../api/types';
import '../styles/token-meter.css';

const WARN_RATIO = 0.75;
const OVER_RATIO = 1.0;
const SEVERE_RATIO = 1.5;

function ratioOf(summary: CostSessionSummary): number {
  if (summary.target_monthly_usd <= 0) return 0;
  return summary.month_usd / summary.target_monthly_usd;
}

function bandClass(ratio: number): string {
  // Order matters: most-severe first so a 1.5× spend reads as severe,
  // not merely over.
  if (ratio >= SEVERE_RATIO) return 'token-meter token-meter--severe';
  if (ratio >= OVER_RATIO) return 'token-meter token-meter--over';
  if (ratio >= WARN_RATIO) return 'token-meter token-meter--warn';
  return 'token-meter';
}

function usd(value: number): string {
  return value.toFixed(2);
}

// The monthly target is a round figure ($100 by default), so drop the
// trailing ".00" for it — matches the "$12.40 / $100" spec example while
// the spent figure stays at cent precision.
function usdTarget(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

// Detail tooltip — lifetime tokens in/out + the three USD figures the
// session-summary endpoint exposes. \n keeps it a readable multi-line
// native title rather than a wall of text.
function buildTitle(summary: CostSessionSummary): string {
  const { lifetime, lifetime_usd, session_usd, month_usd } = summary;
  return [
    `Pool this month: $${usd(month_usd)} / $${usdTarget(summary.target_monthly_usd)}`,
    `Lifetime tokens: ${lifetime.input.toLocaleString()} in · ` +
      `${lifetime.output.toLocaleString()} out`,
    `Lifetime spend: $${usd(lifetime_usd)}`,
    `Session spend: $${usd(session_usd)}`,
  ].join('\n');
}

// Fire one OS notification the first time the pool crosses 100% in a
// given month. No helper exists in the repo (grepped "Notification"),
// so use the browser API guarded by an already-granted permission —
// this component never *requests* permission, per the task contract.
function notifyCrossed(campaign: string): void {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission !== 'granted') return;
  try {
    new Notification('Chronicler — monthly pool exhausted', {
      body: `${campaign} has spent its monthly narrative credit pool.`,
    });
  } catch {
    // Some environments throw on direct construction (e.g. service-worker
    // only). The meter still renders; the notification is best-effort.
  }
}

export function TokenMeter(): React.JSX.Element | null {
  const campaignName = useAppStore((s) => s.activeCampaign);
  const { data: summary } = useSessionSummary(campaignName);

  // Track the last month we fired the 100%-crossing notification for,
  // keyed by `${campaign}-${YYYY-MM}` so a new month (or a campaign
  // switch) re-arms it exactly once.
  const notifiedKey = useRef<string | null>(null);

  const ratio = summary ? ratioOf(summary) : 0;

  useEffect(() => {
    if (!campaignName || !summary) return;
    if (ratio < OVER_RATIO) return;
    const month = new Date().toISOString().slice(0, 7);
    const key = `${campaignName}-${month}`;
    if (notifiedKey.current === key) return;
    notifiedKey.current = key;
    notifyCrossed(campaignName);
  }, [campaignName, summary, ratio]);

  if (!campaignName) return null;
  if (!summary) return null;

  return (
    <div
      className={bandClass(ratio)}
      role="status"
      aria-live="polite"
      title={buildTitle(summary)}
    >
      <span className="token-meter__label">pool</span>
      <span className="token-meter__figure">
        ${usd(summary.month_usd)} / ${usdTarget(summary.target_monthly_usd)}
      </span>
    </div>
  );
}

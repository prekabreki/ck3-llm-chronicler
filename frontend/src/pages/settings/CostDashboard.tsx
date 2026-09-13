// Audit F-28: per-campaign + per-month token totals. Extracted from
// SettingsPage. ck3_chronicler-tbrm.6 dropped the dollar amounts: the
// chronicler is on a Claude Code premium subscription with no
// per-call billing, so the USD column was misleading. Token throughput
// stays — useful telemetry independent of billing.

import type { CostBucket } from '../../api/types';

interface CostDashboardProps {
  campaignName: string | null;
  summary: { this_campaign: CostBucket; this_month: CostBucket } | null;
  loading: boolean;
}

export function CostDashboard({
  campaignName,
  summary,
  loading,
}: CostDashboardProps): React.JSX.Element {
  if (!campaignName) {
    return (
      <section className="paper paper--edged cost-dashboard cost-dashboard--empty">
        <p className="italic-fell">
          Select a campaign to view its token spend.
        </p>
      </section>
    );
  }
  if (loading || !summary) {
    return (
      <section className="paper paper--edged cost-dashboard cost-dashboard--empty">
        <p className="italic-fell">Counting tokens…</p>
      </section>
    );
  }
  return (
    <div className="cost-grid">
      <CostCard
        title="✦ Tokens this campaign"
        eyebrow={campaignName}
        bucket={summary.this_campaign}
      />
      <CostCard
        title="✦ This calendar month"
        eyebrow={`${monthYear()} · UTC`}
        bucket={summary.this_month}
      />
    </div>
  );
}

function CostCard({
  title,
  eyebrow,
  bucket,
}: {
  title: string;
  eyebrow: string;
  bucket: CostBucket;
}): React.JSX.Element {
  const total = bucket.input_tokens + bucket.output_tokens;
  return (
    <section className="paper paper--edged cost-card">
      <div className="smallcaps cost-card__title">{title}</div>
      <div className="cost-card__amount-row">
        <span className="uncial cost-card__amount">{formatNumber(total)}</span>
        <span className="italic-fell cost-card__sub">{eyebrow}</span>
      </div>
      <div className="rule-thin cost-card__rule" />
      <dl className="cost-card__pairs">
        <CostPair k="Input tokens" v={formatNumber(bucket.input_tokens)} />
        <CostPair k="Output tokens" v={formatNumber(bucket.output_tokens)} />
      </dl>
    </section>
  );
}

function CostPair({ k, v }: { k: string; v: string }): React.JSX.Element {
  return (
    <div className="cost-card__pair">
      <dt className="smallcaps cost-card__pair-key">{k}</dt>
      <dd className="cost-card__pair-value">{v}</dd>
    </div>
  );
}

function monthYear(): string {
  const d = new Date();
  return d.toLocaleString('en-US', { month: 'long', year: 'numeric' });
}

// Locale-stable thousands separator. Avoids the non-breaking thin
// space that toLocaleString() emits under some node configurations
// (which broke deterministic snapshots in test).
function formatNumber(n: number): string {
  return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

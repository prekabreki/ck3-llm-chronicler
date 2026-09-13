// IngestActivityStrip — ck3_chronicler-bges. Unit tests for the
// purely presentational save-tail activity strip. No data-fetching
// or SSE — just prop-in / JSX-out surface coverage.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { IngestActivityStrip } from './IngestActivityStrip';

describe('IngestActivityStrip', () => {
  it('renders nothing when no campaign is active', () => {
    const { container } = render(
      <IngestActivityStrip
        campaignName={null}
        lastTickProps={null}
        tailDead={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when campaign has no last-tick data', () => {
    const { container } = render(
      <IngestActivityStrip
        campaignName="Fresh"
        lastTickProps={null}
        tailDead={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when tail is dead', () => {
    const { container } = render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 3,
          event_type_tally: { marriage: 1, birth: 2 },
        }}
        tailDead
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders filename, in-game date, ago, count and tally', () => {
    const tenSecondsAgo = new Date(Date.now() - 10_000).toISOString();
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: tenSecondsAgo,
          event_count: 3,
          event_type_tally: { marriage: 1, birth: 2 },
        }}
        tailDead={false}
      />,
    );
    expect(screen.getByText(/autosave\.ck3/)).toBeInTheDocument();
    expect(screen.getByText(/1066\.4\.11/)).toBeInTheDocument();
    expect(screen.getByText(/10s ago|just now/)).toBeInTheDocument();
    expect(screen.getByText(/3 events/)).toBeInTheDocument();
    expect(screen.getByText(/1 marriage/)).toBeInTheDocument();
    expect(screen.getByText(/2 birth/)).toBeInTheDocument();
  });

  it('caps tally at top 3 with "and N more"', () => {
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 47,
          event_type_tally: {
            marriage: 12,
            birth: 8,
            death: 6,
            title_gain: 5,
            traveling: 16,
          },
        }}
        tailDead={false}
      />,
    );
    expect(screen.getByText(/47 events/)).toBeInTheDocument();
    expect(screen.getByText(/16 traveling/)).toBeInTheDocument();
    expect(screen.getByText(/12 marriage/)).toBeInTheDocument();
    expect(screen.getByText(/8 birth/)).toBeInTheDocument();
    expect(screen.getByText(/and 11 more/)).toBeInTheDocument();
  });

  it('renders zero-event tick as "0 events" without a tally clause', () => {
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave_quiet.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 0,
          event_type_tally: {},
        }}
        tailDead={false}
      />,
    );
    expect(screen.getByText(/0 events/)).toBeInTheDocument();
    expect(screen.queryByText(/\(/)).not.toBeInTheDocument();
  });

  it('shows the pending backlog count when saves are still queued', () => {
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 3,
          event_type_tally: { marriage: 1, birth: 2 },
        }}
        tailDead={false}
        pendingCount={31}
      />,
    );
    expect(screen.getByText(/31 to ingest/)).toBeInTheDocument();
  });

  it('hides the pending segment when caught up (0 pending)', () => {
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 3,
          event_type_tally: { marriage: 1, birth: 2 },
        }}
        tailDead={false}
        pendingCount={0}
      />,
    );
    expect(screen.queryByText(/to ingest/)).not.toBeInTheDocument();
  });

  it('hides the pending segment when the count is unknown (null)', () => {
    render(
      <IngestActivityStrip
        campaignName="Test"
        lastTickProps={{
          save_filename: 'autosave.ck3',
          in_game_date: '1066.4.11',
          ingested_at: new Date().toISOString(),
          event_count: 3,
          event_type_tally: { marriage: 1, birth: 2 },
        }}
        tailDead={false}
        pendingCount={null}
      />,
    );
    expect(screen.queryByText(/to ingest/)).not.toBeInTheDocument();
  });

  it('formats ago across thresholds', () => {
    const now = Date.now();
    const cases: { iso: string; pattern: RegExp }[] = [
      { iso: new Date(now - 5_000).toISOString(), pattern: /5s ago|just now/ },
      { iso: new Date(now - 90_000).toISOString(), pattern: /1m ago/ },
      { iso: new Date(now - 4_000_000).toISOString(), pattern: /1h ago/ },
      { iso: new Date(now - 200_000_000).toISOString(), pattern: /2d ago/ },
    ];
    for (const { iso, pattern } of cases) {
      const { unmount } = render(
        <IngestActivityStrip
          campaignName="Test"
          lastTickProps={{
            save_filename: 'autosave.ck3',
            in_game_date: '1066.4.11',
            ingested_at: iso,
            event_count: 1,
            event_type_tally: { marriage: 1 },
          }}
          tailDead={false}
        />,
      );
      expect(screen.getByText(pattern)).toBeInTheDocument();
      unmount();
    }
  });
});

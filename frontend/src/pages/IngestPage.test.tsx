// IngestPage — covers the no-campaign hint and the empty-state list.
// Live SSE testing happens in the e2e suite (the unit env doesn't have
// a real EventSource); the hook safely no-ops when EventSource is
// missing, so the empty render path is what we verify here.

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { IngestPage } from './IngestPage';
import { useAppStore } from '../store/appStore';

beforeEach(() => {
  useAppStore.setState({
    view: 'ingest',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
});

afterEach(() => {
  cleanup();
});

describe('IngestPage', () => {
  it('renders the live header and empty list when no events have arrived', () => {
    render(<IngestPage campaignName="Wessex" />);
    expect(screen.getByText(/What the watcher sees/)).toBeInTheDocument();
    expect(
      screen.getByText(/The chronicler watches in silence/),
    ).toBeInTheDocument();
    // Cache state defaults to "unknown" before any frame arrives.
    expect(screen.getByText(/cache: unknown/)).toBeInTheDocument();
  });
});

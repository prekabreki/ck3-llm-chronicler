// Tests for CoaTimeline (ck3_chronicler-7b8d).

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { CoaTimeline } from './CoaTimeline';
import type { CoaDefinition } from './CoaTypes';
import * as client from '../api/client';

// F-56: cast partial fixtures through Partial<CoaDefinition> rather than
// `as never`. The tests only exercise `pattern`, but CoaTimeline's prop
// typing requires the full shape; Partial is the narrowest correct cast.

function renderWithClient(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CoaTimeline', () => {
  it('renders nothing for an empty history', async () => {
    vi.spyOn(client, 'getCharacterCoaHistory').mockResolvedValue({
      entries: [],
    });
    const { container } = renderWithClient(
      <CoaTimeline campaignName="Wessex" ck3Id={100} palette={null} />,
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector('.coa-timeline')).toBeNull();
  });

  it('renders nothing when only one entry exists (single-shield case)', async () => {
    vi.spyOn(client, 'getCharacterCoaHistory').mockResolvedValue({
      entries: [
        {
          observed_at: '2026-05-06T10:00:00+00:00',
          coa: { pattern: 'pattern_solid' } as Partial<CoaDefinition> as CoaDefinition,
        },
      ],
    });
    const { container } = renderWithClient(
      <CoaTimeline campaignName="Wessex" ck3Id={100} palette={null} />,
    );
    await waitFor(
      () => expect(container.querySelector('.coa-timeline')).toBeNull(),
      { timeout: 1000 },
    );
  });

  it('renders a strip with date labels for two or more entries', async () => {
    vi.spyOn(client, 'getCharacterCoaHistory').mockResolvedValue({
      entries: [
        {
          observed_at: '2026-04-01T10:00:00+00:00',
          coa: { pattern: 'pattern_solid' } as Partial<CoaDefinition> as CoaDefinition,
        },
        {
          observed_at: '2026-05-06T10:00:00+00:00',
          coa: { pattern: 'pattern_quartered' } as Partial<CoaDefinition> as CoaDefinition,
        },
      ],
    });
    renderWithClient(
      <CoaTimeline campaignName="Wessex" ck3Id={100} palette={null} />,
    );
    await waitFor(() =>
      expect(screen.getByText('Arms over time')).toBeInTheDocument(),
    );
    expect(screen.getByText('2026-04-01')).toBeInTheDocument();
    expect(screen.getByText('2026-05-06')).toBeInTheDocument();
  });
});

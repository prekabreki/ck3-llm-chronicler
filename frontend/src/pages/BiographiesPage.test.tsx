// BiographiesPage — ck3_chronicler (2026-05-09).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { BiographiesPage } from './BiographiesPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { BiographyListEntry } from '../api/types';

const ENTRIES: BiographyListEntry[] = [
  {
    ck3_id: 11,
    first_name: 'Thrugot',
    nickname: null,
    dynasty_name: 'Thrugot',
    birth_date: '1040.1.1',
    death_date: '1075.4.1',
    biography_excerpt: 'Thrugot rose to the duchy in his middle years…',
    generated_at: '2026-05-01T00:00:00+00:00',
    version: 1,
    coa_json: null,
    female: null,
    primary_title: null,
    role: 'ruler',
    relation: null,
  },
  {
    ck3_id: 12,
    first_name: 'Svend',
    nickname: 'the Bold',
    dynasty_name: 'Thrugot',
    birth_date: '1066.9.15',
    death_date: '1102.6.6',
    biography_excerpt: 'Svend inherited and pressed the family south…',
    generated_at: '2026-05-02T00:00:00+00:00',
    version: 1,
    coa_json: null,
    female: null,
    primary_title: null,
    role: 'ruler',
    relation: null,
  },
];

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'biographies',
    activeCampaign: 'Thrugot',
    selectedCharacterId: null,
  });
  vi.spyOn(client, 'getHeraldryPalette').mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('BiographiesPage', () => {
  it('renders rows in BE-supplied order with names + excerpts + dynasty', async () => {
    vi.spyOn(client, 'listCampaignBiographies').mockResolvedValue(ENTRIES);
    renderWithClient(<BiographiesPage campaignName="Thrugot" />);
    await waitFor(() => screen.getByText('Thrugot', { selector: '.biographies-row__name' }));
    expect(
      screen.getByText('Svend', { selector: '.biographies-row__name' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/called the Bold/)).toBeInTheDocument();
    expect(screen.getByText(/Thrugot rose to the duchy/)).toBeInTheDocument();
    expect(screen.getByText(/Svend inherited/)).toBeInTheDocument();
    // Lede shows the count.
    expect(screen.getByText(/2 vitæ/)).toBeInTheDocument();
  });

  it('clicking a row routes to the chronicle for that character', async () => {
    vi.spyOn(client, 'listCampaignBiographies').mockResolvedValue(ENTRIES);
    renderWithClient(<BiographiesPage campaignName="Thrugot" />);
    const user = userEvent.setup();
    const row = await waitFor(() =>
      screen.getByLabelText(/Open the chronicle of Svend/),
    );
    await user.click(row);
    const state = useAppStore.getState();
    expect(state.selectedCharacterId).toBe(12);
    expect(state.view).toBe('chronicle');
  });

  it('groups by role with counts; Kin collapsed by default; relation lines render', async () => {
    const base = ENTRIES[0]!;
    const grouped: BiographyListEntry[] = [
      { ...base, ck3_id: 1, first_name: 'Arnljotur', role: 'ruler',
        relation: 'm. Malmfridr',
        primary_title: { key: 'k_x', name: 'Realm', tier: 'kingdom' } },
      { ...base, ck3_id: 2, first_name: 'Malmfridr', role: 'consort',
        relation: 'wife of Arnljotur', primary_title: null },
      { ...base, ck3_id: 3, first_name: 'Halla', role: 'kin',
        relation: 'm. Sigtrygg', primary_title: null },
    ];
    vi.spyOn(client, 'listCampaignBiographies').mockResolvedValue(grouped);
    renderWithClient(<BiographiesPage campaignName="Thrugot" />);
    await waitFor(() =>
      screen.getByText('Arnljotur', { selector: '.biographies-row__name' }),
    );
    // Open sections with counts.
    expect(screen.getByText(/Rulers \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Consorts \(1\)/)).toBeInTheDocument();
    // Kin is a collapsed <details> (content in DOM, but not open).
    const kinSummary = screen.getByText(/Kin \(1\)/);
    expect(kinSummary.closest('details')).not.toHaveAttribute('open');
    // Relation lines render.
    expect(screen.getByText('m. Malmfridr')).toBeInTheDocument();
    expect(screen.getByText('wife of Arnljotur')).toBeInTheDocument();
    expect(screen.getByText('m. Sigtrygg')).toBeInTheDocument();
  });

  it('shows the empty hint when no biographies exist', async () => {
    vi.spyOn(client, 'listCampaignBiographies').mockResolvedValue([]);
    renderWithClient(<BiographiesPage campaignName="Thrugot" />);
    await waitFor(() =>
      expect(screen.getByText(/No biographies generated yet/)).toBeInTheDocument(),
    );
  });
});

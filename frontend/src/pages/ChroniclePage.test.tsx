// ChroniclePage — covers the empty-state branches and the two tabs
// (Vita + Event roll).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ChroniclePage } from './ChroniclePage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import { ApiError } from '../api/client';
import type {
  BiographyResponse,
  CharacterDetail,
  FamilyTreeResponse,
} from '../api/types';

const ALFRED: CharacterDetail = {
  ck3_id: 100,
  first_name: 'Alfred',
  dynasty_name: 'Wessex',
  house_name: 'Cerdic',
  nickname: 'the Great',
  female: false,
  primary_title: null,
  birth_date: '0848.10.1',
  death_date: '0899.10.26',
  culture: 'anglo_saxon',
  faith: 'catholic',
  events: [
    {
      id: 7001,
      type: 'birth',
      date: '0848.10.1',
      date_iso: '0848-10-01',
      wall_clock_at: '2026-04-15T08:00:00+00:00',
      schema_version: 1,
      payload: { title: 'Born at Wantage' },
    },
    {
      id: 7042,
      type: 'marriage',
      date: '0868.4.10',
      date_iso: '0868-04-10',
      wall_clock_at: '2026-04-15T08:01:00+00:00',
      schema_version: 1,
      payload: { title: 'Wedded Ealhswith' },
    },
  ],
  coa_json: null,
};

const BIO: BiographyResponse = {
  id: 1,
  character_id: 100,
  version: 1,
  body:
    'Alfred was a king of Wessex who learned letters late in life and never forgot the lesson.\n\nHis treaties with the Danes shaped a kingdom for his heirs.',
  prompt_template_version: 'biography_v2',
  provider: 'ollama:qwen3:14b',
  generated_at: '2026-04-30T10:00:00+00:00',
  events_through_event_id: 7042,
  prompt_tokens: 1200,
  completion_tokens: 800,
};

const EMPTY_TREE: FamilyTreeResponse = {
  self_node: {
    ck3_id: 100,
    first_name: 'Alfred',
    nickname: 'the Great',
    birth_date: '0848.10.1',
    death_date: '0899.10.26',
    relation: 'self',
    depth: 0,
    coa_json: null,
  },
  ancestors: [],
  descendants: [],
  spouses: [],
  siblings: [],
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'chronicle',
    activeCampaign: 'Wessex',
    selectedCharacterId: 100,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ChroniclePage', () => {
  it('shows the no-soul hint when no character is selected', () => {
    useAppStore.setState({ selectedCharacterId: null });
    renderWithClient(<ChroniclePage campaignName="Wessex" />);
    expect(screen.getByText(/No soul chosen/)).toBeInTheDocument();
  });

  it('renders the folio with vita prose when biography exists', async () => {
    vi.spyOn(client, 'getCharacterDetail').mockResolvedValue(ALFRED);
    vi.spyOn(client, 'getCharacterBiography').mockResolvedValue(BIO);
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(EMPTY_TREE);

    renderWithClient(<ChroniclePage campaignName="Wessex" />);

    await waitFor(() => {
      expect(screen.getByText(/The Life of/)).toBeInTheDocument();
    });
    // Title block contains Alfred's first name, "called" + nickname.
    expect(screen.getByText('Alfred')).toBeInTheDocument();
    expect(screen.getAllByText(/the Great/).length).toBeGreaterThan(0);
    // Vita prose first paragraph drop-cap rendered.
    expect(
      screen.getByText(
        /Alfred was a king of Wessex who learned letters late in life/,
      ),
    ).toBeInTheDocument();
    // Foot byline shows the provider.
    expect(screen.getAllByText(/Ollama · qwen3:14b/).length).toBeGreaterThan(0);
  });

  it('shows the no-vita pending message when biography 404s', async () => {
    vi.spyOn(client, 'getCharacterDetail').mockResolvedValue(ALFRED);
    vi.spyOn(client, 'getCharacterBiography').mockRejectedValue(
      new ApiError(404, 'no biography for character 100'),
    );
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(EMPTY_TREE);

    renderWithClient(<ChroniclePage campaignName="Wessex" />);

    await waitFor(() => {
      expect(
        screen.getByText(/No vita has yet been written/),
      ).toBeInTheDocument();
    });
  });

  it('events tab lists the chronological roll', async () => {
    vi.spyOn(client, 'getCharacterDetail').mockResolvedValue(ALFRED);
    vi.spyOn(client, 'getCharacterBiography').mockResolvedValue(BIO);
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(EMPTY_TREE);

    renderWithClient(<ChroniclePage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/The Life of/));

    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: /Event roll/ }));
    expect(screen.getByText('Born at Wantage')).toBeInTheDocument();
    expect(screen.getByText('Wedded Ealhswith')).toBeInTheDocument();
  });

  // ck3_chronicler-gcn / Task 12: living-character UX. The memories tab is
  // gone; living characters now default to the Event roll instead.

  it('living character defaults to the Event roll tab and labels Vita as (pending)', async () => {
    const ALFRED_ALIVE: CharacterDetail = { ...ALFRED, death_date: null };
    vi.spyOn(client, 'getCharacterDetail').mockResolvedValue(ALFRED_ALIVE);
    vi.spyOn(client, 'getCharacterBiography').mockRejectedValue(
      new ApiError(404, 'no biography for character 100'),
    );
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(EMPTY_TREE);

    renderWithClient(<ChroniclePage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/The Life of/));

    // Vita tab is relabeled — the empty state on living chars is intentional.
    const vitaTab = screen.getByRole('tab', { name: /Vita \(pending\)/ });
    expect(vitaTab).toBeInTheDocument();
    expect(vitaTab.getAttribute('aria-selected')).toBe('false');

    // The default landing tab is Event roll for living characters.
    const eventsTab = screen.getByRole('tab', { name: /Event roll/ });
    expect(eventsTab.getAttribute('aria-selected')).toBe('true');

    // No Living memories tab exists any more.
    expect(screen.queryByRole('tab', { name: /Living memories/ })).toBeNull();
  });

  it('living character on the Vita tab shows the alive-specific empty-state copy', async () => {
    const ALFRED_ALIVE: CharacterDetail = { ...ALFRED, death_date: null };
    vi.spyOn(client, 'getCharacterDetail').mockResolvedValue(ALFRED_ALIVE);
    vi.spyOn(client, 'getCharacterBiography').mockRejectedValue(
      new ApiError(404, 'no biography for character 100'),
    );
    vi.spyOn(client, 'getFamilyTree').mockResolvedValue(EMPTY_TREE);

    renderWithClient(<ChroniclePage campaignName="Wessex" />);
    await waitFor(() => screen.getByText(/The Life of/));

    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: /Vita \(pending\)/ }));

    // The alive-specific copy explains why the page is empty by design,
    // distinguishing it from a "biography failed to generate" failure.
    expect(
      screen.getByText(/while the subject still lives/),
    ).toBeInTheDocument();
    // The dead-specific copy is NOT shown here — that one would imply the
    // character is dead and we just haven't generated yet.
    expect(
      screen.queryByText(/No vita has yet been written/),
    ).not.toBeInTheDocument();
  });
});

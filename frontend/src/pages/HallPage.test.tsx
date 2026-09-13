// HallPage — covers fixture grid render, sort + filter chip behaviour,
// active/archived visual differentiation, click → setActiveCampaign +
// view='dynasty' navigation, and the empty-state path.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { HallPage } from './HallPage';
import { useAppStore } from '../store/appStore';
import * as useHallOfFameModule from './hall/useHallOfFame';
import type { DynastyRollup } from './hall/useHallOfFame';
import { HALL_FIXTURES } from './hall/hallFixtures';

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({ view: 'hall', activeCampaign: null, selectedCharacterId: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockHallQuery(data: DynastyRollup[] | null, opts?: { isLoading?: boolean; isError?: boolean }): void {
  const stub = {
    data: data ?? undefined,
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
    error: null,
  } as unknown as ReturnType<typeof useHallOfFameModule.useHallOfFame>;
  vi.spyOn(useHallOfFameModule, 'useHallOfFame').mockReturnValue(stub);
}

describe('HallPage', () => {
  it('renders one card per dynasty fixture in the 4-up grid', async () => {
    mockHallQuery(HALL_FIXTURES);
    renderWithClient(<HallPage />);
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: /Hall of Fame/ })).toBeInTheDocument(),
    );
    // One <button class="dyn-card"> per fixture — assert a sample.
    expect(screen.getByText('Barcelona')).toBeInTheDocument();
    expect(screen.getByText('Hauteville')).toBeInTheDocument();
    expect(screen.getByText('Munsö')).toBeInTheDocument();
    // Spans render in the mono caption row.
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it('marks active vs archived cards distinguishably (not via fade)', async () => {
    mockHallQuery(HALL_FIXTURES);
    renderWithClient(<HallPage />);
    // Hauteville is archived in the fixtures.
    const archivedCard = await screen.findByLabelText(
      /Open Hauteville on the Dynasty Wall/,
    );
    expect(archivedCard).toHaveClass('is-archived');
    expect(within(archivedCard).getByText(/Archived/)).toBeInTheDocument();
    // Barcelona is active.
    const activeCard = screen.getByLabelText(/Open Barcelona on the Dynasty Wall/);
    expect(activeCard).not.toHaveClass('is-archived');
    expect(within(activeCard).getByText(/Active/)).toBeInTheDocument();
  });

  it('filters to active when the Active chip is clicked', async () => {
    mockHallQuery(HALL_FIXTURES);
    renderWithClient(<HallPage />);
    const user = userEvent.setup();
    await screen.findByText('Barcelona');
    await user.click(screen.getByRole('button', { name: /Active · 3/ }));
    // Active dynasties remain.
    expect(screen.getByText('Barcelona')).toBeInTheDocument();
    expect(screen.getByText('Ljósvetningar')).toBeInTheDocument();
    expect(screen.getByText('Piast')).toBeInTheDocument();
    // Archived ones gone.
    expect(screen.queryByText('Hauteville')).toBeNull();
    expect(screen.queryByText('Kotromanić')).toBeNull();
  });

  it('sorts by tracked-count when "Most tracked" is selected', async () => {
    mockHallQuery(HALL_FIXTURES);
    renderWithClient(<HallPage />);
    const user = userEvent.setup();
    await screen.findByText('Barcelona');
    await user.click(screen.getByRole('button', { name: /Most tracked/ }));
    // First card in the grid order should be Barcelona (47 souls — top).
    const cards = screen.getAllByRole('button', { name: /Open .* on the Dynasty Wall/ });
    expect(cards[0]).toHaveTextContent('Barcelona');
  });

  it('clicking a card sets the active campaign and navigates to dynasty', async () => {
    mockHallQuery(HALL_FIXTURES);
    renderWithClient(<HallPage />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByLabelText(/Open Barcelona on the Dynasty Wall/),
    );
    expect(useAppStore.getState().activeCampaign).toBe('Barcelona');
    expect(useAppStore.getState().view).toBe('dynasty');
  });

  it('renders the empty-state when there are zero rollups', async () => {
    mockHallQuery([]);
    renderWithClient(<HallPage />);
    await waitFor(() =>
      expect(screen.getByText(/No dynasties yet/)).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Adopt a save/ })).toBeInTheDocument();
  });

  it('empty-state Adopt button jumps to the Library', async () => {
    mockHallQuery([]);
    renderWithClient(<HallPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Adopt a save/ }));
    expect(useAppStore.getState().view).toBe('library');
  });
});

// ck3_chronicler-cs1o: TokenMeter unit tests. The component is driven by
// two inputs — the active campaign (store) and the session-summary query
// — so both are mocked at the module boundary. Mocking the queries module
// lets each test pin an exact CostSessionSummary without standing up a
// QueryClient or a fetch; mocking the store toggles the no-campaign path.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { TokenMeter } from './TokenMeter';
import { useSessionSummary } from '../api/queries';
import { useAppStore } from '../store/appStore';
import type { CostSessionSummary } from '../api/types';

vi.mock('../api/queries', () => ({
  useSessionSummary: vi.fn(),
}));

vi.mock('../store/appStore', () => ({
  useAppStore: vi.fn(),
}));

const mockUseSessionSummary = vi.mocked(useSessionSummary);
const mockUseAppStore = vi.mocked(useAppStore);

function summary(overrides: Partial<CostSessionSummary> = {}): CostSessionSummary {
  return {
    lifetime: { input: 1_234_567, output: 89_012 },
    session: { input: 1000, output: 200 },
    since: null,
    target_lifetime_input: 1_000_000,
    lifetime_usd: 42.5,
    session_usd: 1.25,
    month_usd: 12.4,
    target_monthly_usd: 100,
    ...overrides,
  };
}

// useSessionSummary returns a TanStack UseQueryResult; the component only
// reads `.data`, so a thin stand-in suffices.
function withData(data: CostSessionSummary | undefined): void {
  mockUseSessionSummary.mockReturnValue({ data } as ReturnType<typeof useSessionSummary>);
}

// The component subscribes via `useAppStore((s) => s.activeCampaign)`, so
// the mock applies the selector to a minimal state object. AppState isn't
// exported, so the stand-in state is cast to the selector's parameter
// type — only `activeCampaign` is read here.
function withCampaign(name: string | null): void {
  mockUseAppStore.mockImplementation((selector) =>
    selector({ activeCampaign: name } as Parameters<typeof selector>[0]),
  );
}

beforeEach(() => {
  withCampaign('Wessex');
  withData(summary());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('TokenMeter', () => {
  it('renders the month/target dollar figures from the summary', () => {
    render(<TokenMeter />);
    expect(screen.getByText('$12.40 / $100')).toBeInTheDocument();
    expect(screen.getByText('pool')).toBeInTheDocument();
  });

  it('applies token-meter--warn at 75%+ of the monthly target', () => {
    withData(summary({ month_usd: 75, target_monthly_usd: 100 }));
    const { container } = render(<TokenMeter />);
    const chip = container.querySelector('.token-meter');
    expect(chip).toHaveClass('token-meter--warn');
    expect(chip).not.toHaveClass('token-meter--over');
  });

  it('applies token-meter--over at 100%+ of the monthly target', () => {
    withData(summary({ month_usd: 110, target_monthly_usd: 100 }));
    const { container } = render(<TokenMeter />);
    const chip = container.querySelector('.token-meter');
    expect(chip).toHaveClass('token-meter--over');
    expect(chip).not.toHaveClass('token-meter--severe');
  });

  it('applies token-meter--severe at 150%+ of the monthly target', () => {
    withData(summary({ month_usd: 160, target_monthly_usd: 100 }));
    const { container } = render(<TokenMeter />);
    expect(container.querySelector('.token-meter')).toHaveClass(
      'token-meter--severe',
    );
  });

  it('stays on the base class below 75%', () => {
    withData(summary({ month_usd: 10, target_monthly_usd: 100 }));
    const { container } = render(<TokenMeter />);
    const chip = container.querySelector('.token-meter');
    expect(chip).toBeInTheDocument();
    expect(chip).not.toHaveClass('token-meter--warn');
  });

  it('renders nothing when there is no active campaign', () => {
    withCampaign(null);
    const { container } = render(<TokenMeter />);
    expect(container.querySelector('.token-meter')).toBeNull();
  });

  it('renders nothing while the summary is still loading', () => {
    withData(undefined);
    const { container } = render(<TokenMeter />);
    expect(container.querySelector('.token-meter')).toBeNull();
  });

  it('exposes lifetime token counts in the tooltip', () => {
    const { container } = render(<TokenMeter />);
    const chip = container.querySelector('.token-meter');
    const title = chip?.getAttribute('title') ?? '';
    expect(title).toContain('Lifetime tokens');
    // 1_234_567 input, formatted with locale grouping.
    expect(title).toContain((1_234_567).toLocaleString());
    expect(title).toContain((89_012).toLocaleString());
  });
});

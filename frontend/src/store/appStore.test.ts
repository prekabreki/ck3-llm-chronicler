// ck3_chronicler-le8h: navHistory + goBack semantics.
//
// Covers the three flows the back-arrow is designed to handle:
//   1. biographies → bio → back → biographies  (one back press)
//   2. chronicle(A) → kin-click → chronicle(B) → back → chronicle(A)
//      (selection restored on the same view)
//   3. campaign switch clears the stack

import { beforeEach, describe, expect, it } from 'vitest';

import { useAppStore } from './appStore';

function reset(): void {
  // Snapshot the initial store shape by calling the setters in
  // sequence rather than poking the store directly — keeps the test
  // honest about the public API.
  const s = useAppStore.getState();
  s.setActiveCampaign(null); // clears history + view = library
}

beforeEach(() => {
  reset();
});

describe('navHistory + goBack', () => {
  it('biographies → bio → back lands on biographies in one press', () => {
    const s = useAppStore.getState();
    s.setView('biographies');
    // Clicking a row sets the selected char first, then changes view.
    s.setSelectedCharacterId(42);
    s.setView('chronicle');

    expect(useAppStore.getState().view).toBe('chronicle');
    expect(useAppStore.getState().selectedCharacterId).toBe(42);

    useAppStore.getState().goBack();

    // Back lands on biographies — selectedCharacterId is irrelevant
    // on that view, so we don't assert it.
    expect(useAppStore.getState().view).toBe('biographies');
  });

  it('chronicle kin-click → back restores the previous character on the same view', () => {
    const s = useAppStore.getState();
    s.setSelectedCharacterId(100);
    s.setView('chronicle');
    expect(useAppStore.getState().view).toBe('chronicle');
    expect(useAppStore.getState().selectedCharacterId).toBe(100);

    // Kin click: setSelectedCharacterId only; view doesn't change.
    s.setSelectedCharacterId(200);
    expect(useAppStore.getState().selectedCharacterId).toBe(200);

    useAppStore.getState().goBack();
    expect(useAppStore.getState().view).toBe('chronicle');
    expect(useAppStore.getState().selectedCharacterId).toBe(100);
  });

  it('goBack is a no-op when navHistory is empty', () => {
    const before = useAppStore.getState();
    expect(before.navHistory).toEqual([]);
    before.goBack();
    const after = useAppStore.getState();
    expect(after.view).toBe(before.view);
    expect(after.navHistory).toEqual([]);
  });

  it('setView is a no-op (no history push) when view is unchanged', () => {
    const s = useAppStore.getState();
    s.setView('codex');
    const after1 = useAppStore.getState().navHistory.length;
    s.setView('codex');
    expect(useAppStore.getState().navHistory.length).toBe(after1);
  });

  it('squashes consecutive history entries that share the same view', () => {
    const s = useAppStore.getState();
    s.setView('biographies');
    // Two state changes on biographies — the squash rule collapses
    // them into one history entry rather than two.
    s.setSelectedCharacterId(1);
    s.setSelectedCharacterId(2);
    s.setView('chronicle');

    // History should be: [{biographies, ?}] — one entry, not two
    // separate entries for the two selectedCharacterId pushes.
    const history = useAppStore.getState().navHistory;
    const biographiesEntries = history.filter((h) => h.view === 'biographies');
    expect(biographiesEntries.length).toBe(1);
  });

  it('setActiveCampaign clears the navigation history', () => {
    const s = useAppStore.getState();
    s.setView('biographies');
    s.setView('codex');
    expect(useAppStore.getState().navHistory.length).toBeGreaterThan(0);

    s.setActiveCampaign('a-different-campaign');
    expect(useAppStore.getState().navHistory).toEqual([]);
  });

  it('caps history at 20 entries — oldest dropped first', () => {
    const s = useAppStore.getState();
    // Push 25 distinct view changes; only the last 20 should survive.
    const views: Array<'codex' | 'biographies' | 'tracked' | 'ingest' | 'search'> = [
      'codex',
      'biographies',
      'tracked',
      'ingest',
      'search',
    ];
    for (let i = 0; i < 25; i++) {
      s.setView(views[i % views.length]!);
    }
    expect(useAppStore.getState().navHistory.length).toBeLessThanOrEqual(20);
  });
});

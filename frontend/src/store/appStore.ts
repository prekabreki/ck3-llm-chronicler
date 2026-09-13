// Process-wide UI state: theme + active view + selected campaign.
// Server state goes through TanStack Query (queries.ts) — keep this
// store strictly client-side concerns so SSR-style stale state can't
// leak in.

import { create } from 'zustand';

export type ViewId =
  | 'library'
  // ck3_chronicler-3tke (2026-05-08): the explicit Library → Overview →
  // (Codex / Tracked / Lineage / …) seam. Lets a Library card click land
  // on a campaign-as-an-entity surface (big heraldry, dynasty name,
  // span, stats) rather than dumping the user straight into the
  // character browse view.
  | 'campaign-overview'
  | 'codex'
  | 'chronicle'
  | 'tree'
  | 'tracked'
  | 'ingest'
  | 'search'
  | 'closing'
  // ck3_chronicler (2026-05-09): all-biographies-in-order surface
  // reachable from the campaign-overview nav.
  | 'biographies'
  | 'settings'
  // ck3_chronicler-sz4t: dedicated narrative-queue page surfacing
  // active / queued / failed / completed work beyond the AppShell strip.
  | 'queue'
  // vysp.5: topbar gates Dynasty (thpz.1) + Hall (467k.1) tabs. The
  // destinations are unbuilt; the views render placeholder stubs.
  | 'dynasty'
  | 'hall'
  // ck3_chronicler-yrv3: in-app Logs tab. Surfaces the chronicler
  // backend's Python log stream so the user can run pythonw headless
  // (no console window) and still see what the process is doing.
  | 'logs';

export type Theme = 'light' | 'dark';

// ck3_chronicler-le8h: snapshot pushed onto navHistory before each
// navigation. Capturing both view + selectedCharacterId lets goBack
// restore the kin-click flow (chronicle(charA) → kin → chronicle(charB)
// → back goes to chronicle(charA)) as well as the simpler
// biographies → bio → back flow.
export interface NavSnapshot {
  view: ViewId;
  selectedCharacterId: number | null;
}

const MAX_NAV_HISTORY = 20;

interface AppState {
  view: ViewId;
  setView: (view: ViewId) => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  // Active campaign by name (route-style — survives reloads via
  // localStorage hydration, see hydrate()). Null when on Library.
  activeCampaign: string | null;
  // setActiveCampaign accepts an optional ``view`` so callers can
  // co-set both atomically (audit F-33 / ck3_chronicler-v53j).
  // Without ``view``, the setter resets to 'codex' on a fresh
  // selection and 'library' on null, since clearing
  // selectedCharacterId would otherwise land the user on whatever
  // page they came from with the wrong character — most visibly
  // chronicle's "no soul chosen" empty state.
  setActiveCampaign: (name: string | null, view?: ViewId) => void;
  // Selected character within the active campaign for Codex right-pane.
  selectedCharacterId: number | null;
  setSelectedCharacterId: (id: number | null) => void;
  // ck3_chronicler-le8h: navigation history stack. Most recent at end.
  // setView / setSelectedCharacterId push the pre-action {view,
  // selectedCharacterId} when it changes. goBack pops the top and
  // applies it. setActiveCampaign clears the stack since a campaign
  // switch is a fresh context — back across that boundary would land
  // on stale character ids.
  navHistory: NavSnapshot[];
  goBack: () => void;
}

const STORAGE_KEY = 'chronicler:appstate';

interface PersistedState {
  theme?: Theme;
  activeCampaign?: string | null;
}

function loadPersisted(): PersistedState {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as PersistedState;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function persist(partial: PersistedState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(partial));
  } catch {
    // localStorage may be disabled / full — silent failure is fine
  }
}

const initial = loadPersisted();

// ck3_chronicler-le8h: push a snapshot onto the navHistory stack,
// coalescing consecutive entries that share the same view. The squash
// rule turns "biographies → setSelectedCharacterId(x) → setView('chronicle')"
// into a single history entry rather than two, so one back press
// returns to biographies (instead of two: one to "biographies with
// selected x" then another to "biographies"). The squash retains the
// most-recent same-view selectedCharacterId so the kin-click flow
// (different view comes back to the chronicle the user was on) still
// works.
function _pushSnap(history: NavSnapshot[], snap: NavSnapshot): NavSnapshot[] {
  const top = history[history.length - 1];
  if (top && top.view === snap.view) {
    return [...history.slice(0, -1), snap];
  }
  return [...history.slice(-(MAX_NAV_HISTORY - 1)), snap];
}

export const useAppStore = create<AppState>((set, get) => ({
  // ck3_chronicler (2026-05-09): always land on Library on cold-load,
  // even when a campaign is restored from localStorage. The Library
  // is the user's chosen entry point — re-opening the SPA should put
  // them in the spot where they pick which campaign to enter, not
  // drop them straight into the last campaign's codex. activeCampaign
  // is still restored below so a single click on the campaign card
  // resumes session state without re-fetching.
  view: 'library',
  setView: (view) =>
    set((state) => {
      if (state.view === view) return {};
      const snap: NavSnapshot = {
        view: state.view,
        selectedCharacterId: state.selectedCharacterId,
      };
      return {
        view,
        navHistory: _pushSnap(state.navHistory, snap),
      };
    }),
  // vysp.3: dark is the default (per user OK on the design-handoff
  // open question). Users with an explicit persisted theme keep their
  // preference; only no-preference cold-load picks dark.
  theme: initial.theme ?? 'dark',
  setTheme: (theme) => {
    set({ theme });
    persist({ theme, activeCampaign: get().activeCampaign });
  },
  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
    get().setTheme(next);
  },
  activeCampaign: initial.activeCampaign ?? null,
  setActiveCampaign: (name, view) => {
    const nextView: ViewId = view ?? (name ? 'codex' : 'library');
    // ck3_chronicler-le8h: campaign switch is a fresh context — clear
    // history so a back press doesn't land on the previous campaign's
    // chronicle (which would re-trigger a 404 dance on a stale
    // selectedCharacterId from a different DB).
    set({
      activeCampaign: name,
      selectedCharacterId: null,
      view: nextView,
      navHistory: [],
    });
    persist({ theme: get().theme, activeCampaign: name });
  },
  selectedCharacterId: null,
  setSelectedCharacterId: (id) =>
    set((state) => {
      if (state.selectedCharacterId === id) return {};
      // ck3_chronicler-le8h: many call sites pair
      // setSelectedCharacterId(x) with setView('chronicle'); the squash
      // rule in _pushSnap coalesces those into one entry. When the
      // selection changes WITHOUT a view change (e.g. a kin-click on
      // the same chronicle page), the push stands alone and back
      // restores the previous character.
      const snap: NavSnapshot = {
        view: state.view,
        selectedCharacterId: state.selectedCharacterId,
      };
      return {
        selectedCharacterId: id,
        navHistory: _pushSnap(state.navHistory, snap),
      };
    }),
  navHistory: [],
  goBack: () =>
    set((state) => {
      if (state.navHistory.length === 0) return {};
      const next = state.navHistory[state.navHistory.length - 1]!;
      return {
        view: next.view,
        selectedCharacterId: next.selectedCharacterId,
        navHistory: state.navHistory.slice(0, -1),
      };
    }),
}));

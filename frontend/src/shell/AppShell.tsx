// AppShell — vysp.5 topbar reskin: 64px three-column grid
// (260px brand · 1fr nav · auto right cell). Nine primary tabs;
// the right cell holds the in-game date, a tail-live pip, and a
// heraldry+name+campaign-id pill keyed off the active campaign.
// A small overflow tools cluster keeps Settings + Closing + theme
// reachable from the chrome (the brief is silent on these access
// paths — the cluster is the smallest add that preserves them).

import { useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { Banner } from '../components/Heraldry';
import { RealHeraldry } from '../components/RealHeraldry';
import { TokenMeter } from '../components/TokenMeter';
import { queryKeys, usePalette } from '../api/queries';
import { haltChronicler } from '../api/client';
import { haltSaveTail } from '../api/migrateClient';
import { useAppStore, type ViewId } from '../store/appStore';
import type { CoaDefinition } from '../components/CoaTypes';
import { TailStatusBadge } from './TailStatusBadge';
import type { TailStatus } from './tailStatus';

interface NavTab {
  id: ViewId;
  label: string;
}

// Order matches the design brief §1: Library, Codex, Chronicle,
// Dynasty, Hall, Lineage, Tracked, Search, Save tail.
const NAV_TABS: NavTab[] = [
  { id: 'library', label: 'Library' },
  { id: 'codex', label: 'Codex' },
  { id: 'chronicle', label: 'Chronicle' },
  { id: 'dynasty', label: 'Dynasty' },
  { id: 'hall', label: 'Hall' },
  { id: 'tree', label: 'Lineage' },
  { id: 'tracked', label: 'Tracked' },
  { id: 'search', label: 'Search' },
  { id: 'ingest', label: 'Save tail' },
];

// ck3_chronicler-8e0w: nav tabs that are meaningful ONLY with an active
// campaign — they 404 or render empty placeholders when none is
// selected. Library / Hall / Search / Save tail are cross-campaign or
// global surfaces and stay visible.
const CAMPAIGN_SCOPED_TAB_IDS: ReadonlySet<ViewId> = new Set<ViewId>([
  'codex',
  'chronicle',
  'dynasty',
  'tree',
  'tracked',
]);

const DEFAULT_TAIL_STATUS: TailStatus = {
  level: 'idle',
  label: 'Tail idle',
  detail: null,
  tone: 'gray',
};

interface AppShellProps {
  campaignName?: string | null;
  // ck3_chronicler-8e0w: archived campaigns are viewable (Library /
  // campaign-overview / closing chronicle) but no longer 'in play' —
  // pill + campaign-scoped nav hide for archived just like for no
  // campaign at all.
  campaignArchived?: boolean;
  campaignRange?: string | null;
  currentInGameDate?: string | null;
  currentPlayerName?: string | null;
  currentPlayerCoa?: CoaDefinition | null;
  // Live = save-tail SSE channel is open OR the campaign saw activity
  // recently. Wired through from App.tsx; defaults to false so static
  // renders (tests, snapshots) don't claim a live tail.
  tailLive?: boolean;
  // ck3_chronicler-kgqa: derived go/no-go save-tail status. Defaults to
  // idle so static/test renders show a neutral badge.
  tailStatus?: TailStatus;
}

export function AppShell({
  campaignName,
  campaignArchived = false,
  campaignRange,
  currentInGameDate,
  currentPlayerName,
  currentPlayerCoa,
  tailLive = false,
  tailStatus = DEFAULT_TAIL_STATUS,
}: AppShellProps): React.JSX.Element {
  // ck3_chronicler-8e0w: treat archived campaigns as 'no live campaign'
  // for header chrome purposes. The user can still navigate into the
  // archived campaign's overview / closing chronicle from the Library,
  // but the always-visible chrome (pill, scoped nav) reflects the
  // not-in-play state.
  const hasLiveCampaign = !!campaignName && !campaignArchived;
  const view = useAppStore((s) => s.view);
  const setView = useAppStore((s) => s.setView);
  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  // ck3_chronicler-le8h: back-arrow state + handler. Subscribing to
  // navHistory.length keeps the button's disabled state in sync; the
  // arrow renders disabled-greyed when there's nothing to pop.
  const goBack = useAppStore((s) => s.goBack);
  const canGoBack = useAppStore((s) => s.navHistory.length > 0);
  const { data: palette } = usePalette();

  // ck3_chronicler-p6qo: clicking the Tail pip while the channel is
  // live offers to halt the save-tail. The /halt-save-tail endpoint
  // exists (migrate.py) but was previously only reachable from the
  // migration flow; surfacing it here means the user doesn't have to
  // close LAUNCH.bat or curl an internal route to stop ingest.
  // Resume requires a process restart by design — halting is the
  // common operation, resuming is rare; the asymmetry is acceptable.
  const qc = useQueryClient();
  const haltMu = useMutation({
    mutationFn: haltSaveTail,
    onSettled: () => {
      // Invalidate any campaign / event-stream queries so the pip and
      // dependent surfaces refresh once the BE stops emitting.
      qc.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.campaignAll() });
    },
  });

  // ck3_chronicler-ghfi: topbar Quit button mirrors the Logs page's
  // Halt-backend action. Same shape: confirm, POST /api/halt, then
  // window.close() so the chromeless --app launcher window closes
  // alongside the process. Defined at the top of the AppShell so the
  // button's JSX stays clean below.
  const onQuitClick = (): void => {
    const ok = window.confirm(
      'Quit chronicler?\n\n' +
        'This shuts down the backend process (uvicorn + save-tail). ' +
        'Any in-flight biography generation is lost. ' +
        'You can restart from the Start Menu shortcut.',
    );
    if (!ok) return;
    void haltChronicler()
      .catch(() => undefined)
      .finally(() => {
        window.close();
      });
  };

  const onPipClick = (): void => {
    if (tailLive && !haltMu.isPending) {
      const ok = window.confirm(
        'Stop the save-tail ingest?\n\n' +
          'This halts the chronicler\'s background watcher. ' +
          'You can resume by restarting the chronicler dev process.'
      );
      if (ok) {
        haltMu.mutate();
      }
      return;
    }
    setView('ingest');
  };

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // ck3_chronicler-le8h: Alt+Left as a browser-back analogue. Listens
  // at document level so any page (including form fields) responds —
  // matches user muscle memory from web browsers. Calling goBack with
  // an empty history is a no-op (handled in the store), so the global
  // binding is safe.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.altKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        goBack();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goBack]);

  // The pill name is the player's character (the proudest piece of
  // identity in the chrome); the second line is the campaign slug to
  // disambiguate when the user has multiple campaigns open across
  // sessions. Falls back to last-seen-date when nothing tracks the
  // chronicle (pre-cqo campaigns, unimported saves).
  const pillName = currentPlayerName ?? campaignName ?? null;
  const pillSub = currentPlayerName && campaignName
    ? campaignName
    : campaignRange ?? null;

  return (
    <div className="appbar" role="banner">
      <div className="appbar__brand">
        <button
          type="button"
          className="appbar__back"
          onClick={goBack}
          disabled={!canGoBack}
          aria-label="Go back"
          title={canGoBack ? 'Back (Alt+Left)' : 'Nowhere to go back to'}
        >
          ←
        </button>
        <div>
          <div className="appbar__brand-name">Chronicler</div>
          <div className="appbar__brand-sub">v1.0 · ck3</div>
        </div>
      </div>

      <nav className="appbar__nav" aria-label="Primary navigation">
        {NAV_TABS.filter(
          (tab) => hasLiveCampaign || !CAMPAIGN_SCOPED_TAB_IDS.has(tab.id)
        ).map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={
              'appbar__navitem' +
              (view === tab.id ? ' appbar__navitem--active' : '')
            }
            onClick={() => setView(tab.id)}
            aria-current={view === tab.id ? 'page' : undefined}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="appbar__right">
        {currentInGameDate && (
          <span className="appbar__date" title="Current in-game date">
            {currentInGameDate}
          </span>
        )}
        {/* ck3_chronicler-cs1o: monthly pool-spend chip. Self-contained —
            reads the active campaign from the store and renders nothing
            when none is in play, matching the sibling strip components. */}
        <TokenMeter />
        {/* ck3_chronicler-kgqa: single go/no-go tail-status badge.
            Click behaviour preserved from the old pip — halt when the
            tail is live, otherwise open the Save tail surface. */}
        <TailStatusBadge
          status={tailStatus}
          onClick={onPipClick}
          busy={haltMu.isPending}
          title={tailStatus.detail ?? (tailLive ? 'Click to stop ingest' : 'Open Save tail')}
        />
        {/* ck3_chronicler-ghfi: Quit button mirrors the Logs page's
            Halt-backend action. Always visible on the topbar so the
            user doesn't need to navigate to Logs to stop chronicler. */}
        <button
          type="button"
          className="appbar__quit"
          onClick={onQuitClick}
          title="Quit chronicler (stops the backend process)"
        >
          Quit
        </button>
        {/* ck3_chronicler-8e0w: pill is gated on hasLiveCampaign so a
            closed/cleared campaign drops the chip even if a stale
            currentPlayerName lingers in props for a render. Archived
            campaigns are viewable through the Library card → overview
            path; they just don't get the always-visible header chrome.
            Wrapping in a button lets the user jump to the campaign-
            overview screen from any view. */}
        {hasLiveCampaign && pillName && (
          <button
            type="button"
            className="appbar__pill"
            onClick={() => setView('campaign-overview')}
            aria-label={`Open campaign overview for ${pillName}`}
            title="Open campaign overview"
          >
            <span className="appbar__pill-shield" aria-hidden>
              {currentPlayerCoa && palette ? (
                <RealHeraldry
                  coa={currentPlayerCoa}
                  palette={palette}
                  size={28}
                  label={`Arms of ${pillName}`}
                />
              ) : (
                <Banner seed={pillName} size={28} />
              )}
            </span>
            <span className="appbar__pill-text">
              <span className="appbar__pill-name">{pillName}</span>
              {pillSub && <span className="appbar__pill-id">{pillSub}</span>}
            </span>
          </button>
        )}
        <div className="appbar__tools" role="group" aria-label="Tools">
          <button
            type="button"
            className="appbar__tool"
            onClick={() => setView('closing')}
            title="Closing ceremony"
            aria-label="Closing ceremony"
          >
            ❦
          </button>
          <button
            type="button"
            className="appbar__tool"
            onClick={() => setView('logs')}
            title="Backend logs"
            aria-label="Backend logs"
          >
            ☷
          </button>
          <button
            type="button"
            className="appbar__tool"
            onClick={() => setView('settings')}
            title="Narrative providers & settings"
            aria-label="Narrative providers & settings"
          >
            ☩
          </button>
          <button
            type="button"
            className="appbar__tool"
            onClick={toggleTheme}
            title="Toggle vellum / dark vellum"
            aria-label="Toggle theme"
          >
            {theme === 'dark' ? '☾' : '☀'}
          </button>
        </div>
      </div>
    </div>
  );
}

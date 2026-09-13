// View router. All primary views wired (Library, Codex, Chronicle,
// Lineage, Tracked, Save-tail, Search, Closing, Settings, Queue) plus
// vysp.5 placeholders for Dynasty (thpz) and Hall (467k).

import { useEffect } from 'react';

import { AppShell } from './shell/AppShell';
import { AutoResumeBanner } from './shell/AutoResumeBanner';
import { FirstRunWizard } from './components/FirstRunWizard';
import { NoCampaignSelected } from './components/NoCampaignSelected';
import { IngestActivityStrip } from './shell/IngestActivityStrip';
import { NarrativeQueueStrip } from './shell/NarrativeQueueStrip';
import { BiographiesPage } from './pages/BiographiesPage';
import { CampaignOverviewPage } from './pages/CampaignOverviewPage';
import { ChroniclePage } from './pages/ChroniclePage';
import { ClosingPage } from './pages/ClosingPage';
import { CodexPage } from './pages/CodexPage';
import { DynastyPage } from './pages/DynastyPage';
import { HallPage } from './pages/HallPage';
import { IngestPage } from './pages/IngestPage';
import { LibraryPage } from './pages/LibraryPage';
import { LogsPage } from './pages/LogsPage';
import { QueuePage } from './pages/QueuePage';
import { SearchPage } from './pages/SearchPage';
import { SettingsPage } from './pages/SettingsPage';
import { LineagePage } from './pages/LineagePage';
import { TrackedPage } from './pages/TrackedPage';
import { ApiError } from './api/client';
import { useCampaign, useCampaigns } from './api/queries';
import { useEventStream } from './api/useEventStream';
import { deriveTailStatus } from './shell/tailStatus';
import { shouldClearStaleActiveCampaign } from './store/activeCampaignValidation';
import { useAppStore } from './store/appStore';

export default function App(): React.JSX.Element {
  const view = useAppStore((s) => s.view);
  const activeCampaign = useAppStore((s) => s.activeCampaign);
  const setActiveCampaign = useAppStore((s) => s.setActiveCampaign);
  const setView = useAppStore((s) => s.setView);
  // vysp.5: include_counts=true so the topbar pill can render the
  // player's actual CK3 arms (current_player_coa_json is only
  // populated under that flag — see useCampaign / API behaviour).
  const campaignQ = useCampaign(activeCampaign, true);

  // audit F-02 / ck3_chronicler-bxte: a persisted activeCampaign whose
  // row has been deleted (manual DB tidy, fresh checkout, etc.) used
  // to fire 6+ 404s on cold load before the user clicked anything,
  // showing "Failed to read the codex." with no recovery affordance.
  // Detect the 404 on the boot fetch and bounce the user back to the
  // Library so they can pick a real campaign.
  useEffect(() => {
    if (
      activeCampaign &&
      campaignQ.error instanceof ApiError &&
      campaignQ.error.status === 404
    ) {
      setActiveCampaign(null);
      setView('library');
    }
  }, [activeCampaign, campaignQ.error, setActiveCampaign, setView]);

  // ck3_chronicler-sgy8: belt to the bxte/F-02 braces. The per-campaign
  // 404 useEffect above only clears once the per-campaign-with-counts
  // fetch resolves (slow — opens a per-campaign DB session for the
  // aggregate counts). Meanwhile useEventStream(activeCampaign) has
  // already opened an SSE socket against the stale name and starts
  // burning through its MAX_CONSECUTIVE_ERRORS budget. Pre-emptively
  // clear the moment the registry-wide list resolves and the persisted
  // name isn't in it — that hits before the per-campaign fetch even
  // touches the per-DB engine cache.
  const campaignsListQ = useCampaigns({ includeArchived: true });
  useEffect(() => {
    if (shouldClearStaleActiveCampaign(activeCampaign, campaignsListQ.data)) {
      setActiveCampaign(null);
      setView('library');
    }
  }, [activeCampaign, campaignsListQ.data, setActiveCampaign, setView]);
  // vysp.5: tail-live pip is wired to whether the SSE event channel is
  // currently connected. The hook also drives narrative-queue refresh
  // already; one connection serves both. F-45: ``dead`` flips true
  // when the channel hits its consecutive-error cap so the AppShell
  // can render "Tail unavailable" rather than the indistinguishable
  // "Tail idle".
  const eventStream = useEventStream(activeCampaign);
  const {
    connected: tailLive,
    dead: tailDead,
    lastSavePairCompleted,
    // ck3_chronicler-cjtx: latest cache_state frame — its `pending` is the
    // live save backlog surfaced in the IngestActivityStrip.
    cacheState,
    // ck3_chronicler-n52f: latest campaign_auto_resumed frame; the
    // AutoResumeBanner surfaces it with a one-click [Switch] action.
    autoResumed,
  } = eventStream;
  const tailStatus = deriveTailStatus(eventStream);

  const campaign = campaignQ.data ?? null;

  // ck3_chronicler-bges: live SSE wins; persisted fields fall back so a
  // cold load (or post-refresh) still has something to show before the
  // next save_pair_completed frame arrives.
  const lastTickProps = (() => {
    if (lastSavePairCompleted) {
      // ck3_chronicler-k91o: lastSavePairCompleted is now typed as
      // SavePairCompletedFrame, so these fields read without casts.
      return {
        save_filename: lastSavePairCompleted.save_filename,
        in_game_date: lastSavePairCompleted.in_game_date,
        ingested_at: lastSavePairCompleted.completed_at,
        event_count: lastSavePairCompleted.event_count,
        event_type_tally: lastSavePairCompleted.event_type_tally,
      };
    }
    if (campaign?.last_save_ingested_at) {
      return {
        save_filename: campaign.last_save_filename ?? '',
        in_game_date: campaign.last_save_in_game_date,
        ingested_at: campaign.last_save_ingested_at,
        event_count: campaign.last_tick_event_count ?? 0,
        event_type_tally: campaign.last_tick_event_type_tally ?? {},
      };
    }
    return null;
  })();

  return (
    <div className="app-shell">
      <AppShell
        campaignName={activeCampaign}
        campaignArchived={campaign?.archived === true}
        currentInGameDate={campaign?.current_in_game_date ?? null}
        currentPlayerName={campaign?.current_player_name ?? null}
        currentPlayerCoa={campaign?.current_player_coa_json ?? null}
        campaignRange={
          campaign?.last_event_at
            ? `last seen ${campaign.last_event_at.split('T')[0]}`
            : null
        }
        tailLive={tailLive}
        tailStatus={tailStatus}
      />
      <IngestActivityStrip
        campaignName={activeCampaign}
        lastTickProps={lastTickProps}
        tailDead={tailDead}
        pendingCount={cacheState?.pending ?? null}
      />
      <AutoResumeBanner
        campaignName={activeCampaign}
        autoResumed={autoResumed}
        onSwitch={(name) => setActiveCampaign(name, 'codex')}
      />
      <NarrativeQueueStrip campaignName={activeCampaign} />
      <FirstRunWizard />
      <main>
        {/* audit M-F1 / ck3_chronicler-27ov.56: the no-campaign gate for
            campaign-scoped views lives here, not in nine per-page guards.
            Each scoped view renders its page only with a non-null campaign
            (the ternary narrows the type), else the shared placeholder. */}
        {view === 'library' && <LibraryPage />}
        {view === 'campaign-overview' &&
          (activeCampaign ? (
            <CampaignOverviewPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'biographies' &&
          (activeCampaign ? (
            <BiographiesPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'codex' &&
          (activeCampaign ? (
            <CodexPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'chronicle' &&
          (activeCampaign ? (
            <ChroniclePage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'tree' &&
          (activeCampaign ? (
            <LineagePage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'tracked' &&
          (activeCampaign ? (
            <TrackedPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'settings' && <SettingsPage />}
        {view === 'ingest' &&
          (activeCampaign ? (
            <IngestPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'queue' && <QueuePage />}
        {view === 'search' && <SearchPage />}
        {view === 'closing' &&
          (activeCampaign ? (
            <ClosingPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'dynasty' &&
          (activeCampaign ? (
            <DynastyPage campaignName={activeCampaign} />
          ) : (
            <NoCampaignSelected />
          ))}
        {view === 'hall' && <HallPage />}
        {view === 'logs' && <LogsPage />}
      </main>
    </div>
  );
}

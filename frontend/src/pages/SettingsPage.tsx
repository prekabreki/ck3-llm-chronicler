// SettingsPage — chronicler configuration shell.
//
// Audit F-28: this file used to bundle six cards + helpers in 956
// lines. Split into ./settings/{SettingsCard,PathsCard,HeraldryCard,
// ProseRepoCard,CostDashboard,DebugLogPanel}; what's left here is the
// page shell + the SettingsCard wrappers that pin each section to its
// nav anchor.
//
// ck3_chronicler-tbrm.4: the v0.9 three-card provider grid (Local /
// Remote / Hybrid) collapsed into a single ProseRepoCard once the
// in-process providers were ripped out. Issue #23 put a third card
// back — but a BackendCard, not a provider grid: the three transports
// of #45/#46 are one process-wide choice of who gets billed, not a
// route. The other two cards keep their concerns (where the chronicle
// lives; which model each kind runs).

import { useCostSummary } from '../api/queries';
import { MigrationPanel } from '../components/MigrationPanel';
import { useAppStore } from '../store/appStore';

import { CostDashboard } from './settings/CostDashboard';
import { DebugLogPanel } from './settings/DebugLogPanel';
import { BackendCard } from './settings/BackendCard';
import { HeraldryPipelineCard } from './settings/HeraldryCard';
import { LLMEngineCard } from './settings/LLMEngineCard';
import { PathsCard } from './settings/PathsCard';
import { ProseRepoCard } from './settings/ProseRepoCard';
import { SettingsCard, SettingsNav } from './settings/SettingsCard';

export function SettingsPage(): React.JSX.Element {
  const activeCampaign = useAppStore((s) => s.activeCampaign);
  const costQ = useCostSummary(activeCampaign);

  return (
    <div className="settings-page">
      <div className="settings-page__inner">
        <header className="settings-page__header">
          <div className="smallcaps settings-page__eyebrow">
            The chronicler's hand
          </div>
          <h1 className="uncial settings-page__title">
            Whose voice writes the chronicles
          </h1>
          <p className="italic-fell settings-page__lede">
            Biographies run through one of three backends against a
            chronicle directory whose <code>CLAUDE.md</code> reshapes the
            assistant into a chronicler. Choose the backend and point the
            chronicler at the directory here.
          </p>
        </header>

        <div className="settings-shell">
          <SettingsNav />
          <div className="settings-stack">
            <PathsCard />
            <HeraldryPipelineCard />
            <SettingsCard
              id="provider"
              title="Provider & LLM"
              description="The hand that writes. Whichever backend is selected is invoked headlessly against the chronicle directory; the role-reshape lives in that directory's CLAUDE.md."
            >
              <div className="settings-providers settings-providers--single">
                <BackendCard />
                <ProseRepoCard />
                <LLMEngineCard />
              </div>
            </SettingsCard>
            <SettingsCard
              id="cost"
              title="Cost guardrail"
              description="Token + USD totals for the active campaign and the calendar month."
            >
              <CostDashboard
                campaignName={activeCampaign}
                summary={costQ.data ?? null}
                loading={costQ.isLoading}
              />
            </SettingsCard>
            <SettingsCard
              id="migrations"
              title="Migrations"
              description="Per-campaign schema upgrades. The chronicler takes a folder backup before applying any migration."
            >
              <MigrationPanel />
            </SettingsCard>
            <SettingsCard
              id="keyboard"
              title="Keyboard"
              description="Shortcuts for the chrome. Cross-surface chord mappings (⌘K, ⌘P) live here."
              stub
              stubHint="Keyboard customisation is filed as vysp.13-keyboard."
            />
            <SettingsCard
              id="advanced"
              title="Advanced"
              description="Diagnostic toggles, debug log rotation, telemetry — exposed only when something is going wrong."
            >
              <DebugLogPanel />
            </SettingsCard>
          </div>
        </div>
      </div>
    </div>
  );
}

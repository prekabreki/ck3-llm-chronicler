// NoCampaignSelected — the shared placeholder for campaign-scoped views
// reached with no active campaign. Audit M-F1 / ck3_chronicler-27ov.56:
// nine pages each hand-rolled this ~18-line block (and DynastyPage's had
// already drifted — different copy, no Library link). The gate now lives
// in App's view switch and renders this once.

import { useAppStore } from '../store/appStore';

export function NoCampaignSelected(): React.JSX.Element {
  const setView = useAppStore((s) => s.setView);
  return (
    <div className="page-gate page-gate--empty">
      <p className="italic-fell">
        No campaign selected. Return to the{' '}
        <button
          type="button"
          className="link"
          onClick={() => setView('library')}
        >
          Library
        </button>{' '}
        to choose a volume.
      </p>
    </div>
  );
}

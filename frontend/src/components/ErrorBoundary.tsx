// audit F-15 / ck3_chronicler-2isd: a render-time exception anywhere
// in the React tree used to blank the entire app — the FE had no
// boundary at all. Combined with the SubShield depth-overflow risk
// (F-36) and any future malformed-API-payload bug, that meant a
// single bad CoA blob could brick the UI.
//
// This boundary:
// - Catches render / lifecycle / hook-init errors (classic React error
//   boundary semantics — async errors and event handlers still
//   propagate to window.onerror).
// - Renders a paper-styled fallback that names the error + offers
//   "Reload" and "Reset" affordances. Reset clears the persisted
//   appStore localStorage entry (so a corrupt `activeCampaign` or
//   `theme` can't trap the user in the failure mode) and reloads.
// - Logs to console.error so the chronicler dev workflow surfaces the
//   stack at the terminal.

import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

const APP_STORAGE_KEY = 'chronicler:appstate';

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The componentStack helps locate which subtree exploded; React
    // doesn't surface it any other way once the fallback renders.
    console.error('chronicler ErrorBoundary caught', error, info.componentStack);
  }

  private onReload = (): void => {
    window.location.reload();
  };

  private onReset = (): void => {
    try {
      window.localStorage.removeItem(APP_STORAGE_KEY);
    } catch {
      // localStorage may be disabled — reload alone is the next-best.
    }
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }
    const err = this.state.error;
    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary__paper paper paper--edged">
          <div className="smallcaps error-boundary__eyebrow">✦ Chronicle interrupted</div>
          <h1 className="uncial error-boundary__title">Something went amiss.</h1>
          <p className="italic-fell error-boundary__lede">
            The chronicler hit a snag while rendering this view. The
            error has been logged to the developer console.
          </p>
          <pre className="error-boundary__stack">
            {err.name}: {err.message}
          </pre>
          <div className="error-boundary__actions">
            <button type="button" className="btn" onClick={this.onReload}>
              Reload
            </button>
            <button type="button" className="btn btn--quiet" onClick={this.onReset}>
              Reset & reload
            </button>
          </div>
          <p className="error-boundary__hint italic-fell">
            "Reset" clears the saved campaign + theme so a corrupt
            cached selection can't keep the chronicle stuck.
          </p>
        </div>
      </div>
    );
  }
}

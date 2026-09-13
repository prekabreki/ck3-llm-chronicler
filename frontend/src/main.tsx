import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Self-hosted fonts (registers @font-face for every weight we use).
import './styles/fonts';

// Design tokens (CSS custom properties — :root + [data-theme="dark"]).
import './styles/tokens.css';

// Base styles + design system components (paper, btn, tabs, dropcap, ...).
import './styles/globals.css';

// Per-page styles split out of globals.css by audit F-16
// (ck3_chronicler-npf1). Order matches the historical phase numbering
// so cascading specificity stays identical to the pre-split file.
import './styles/library.css';
import './styles/codex.css';
import './styles/chronicle.css';
import './styles/lineage.css';
import './styles/tracked.css';
import './styles/settings.css';
import './styles/ingest.css';
import './styles/search.css';
import './styles/closing.css';
import './styles/modal.css';
import './styles/hall.css';
import './styles/overview.css';
import './styles/biographies.css';
import './styles/logs.css';

// Polish pass (ck3_chronicler-vm3v). Must import AFTER the per-page
// stylesheets above so polish-additions.css wins the cascade.
import './styles/ornaments.css';
import './styles/polish-additions.css';
// v3 (ck3_chronicler-zw49): extends the polish vocabulary to
// closing / chronicle / dynasty / hall.
import './styles/polish-pages-v3.css';

import App from './App.tsx';
import { ErrorBoundary } from './components/ErrorBoundary';

// Single TanStack Query client for the whole app. Default options favour
// the personal-tool deployment: one user, low request rate, server-side
// data is the source of truth so we don't aggressively re-fetch.
//
// Retry policy is tuned for the `LAUNCH.bat` cold-start race: the
// browser opens as soon as Vite binds :5173, but the FastAPI backend
// can take ~10s to baseline-parse the latest autosave before binding
// :8000. Without retries the very first /api/* call latches onto a 502
// from the proxy and the user stares at "shelf is silent: Bad Gateway"
// until they refresh. Five retries with a 500ms→4s backoff covers ~12s
// of backend boot time without stretching real-failure feedback.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 5,
      retryDelay: (attemptIndex) => Math.min(500 * 2 ** attemptIndex, 4000),
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root element not found in index.html');

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);

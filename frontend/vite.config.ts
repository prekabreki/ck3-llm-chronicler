import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Vite config — proxies /api/* and /sse/* to the FastAPI dev server on :8000.
// Production build emits to ../src/chronicler/api/static/app/ so the
// existing FastAPI install serves it directly with no extra packaging.

// ck3_chronicler-dsjo: Vite's underlying connect http.Server inherits
// Node's default keepAliveTimeout=5000ms and closes the downstream
// socket to Chrome at 5s regardless of the proxy's own timeout settings,
// forcing EventSource to reconnect every ~8s on quiet ingest channels.
// This configureServer plugin runs before listen() and bumps the
// keep-alive to 120s (matches the uvicorn timeout_keep_alive=120 fix
// that landed BE-side as part of the same investigation). headersTimeout
// must be at least keepAliveTimeout + a small buffer or Node logs a
// warning at startup.
const dsjoKeepAlivePlugin = {
  name: 'ck3-chronicler-dsjo-keepalive',
  configureServer(server: {
    httpServer: { keepAliveTimeout: number; headersTimeout: number } | null;
  }) {
    if (server.httpServer) {
      server.httpServer.keepAliveTimeout = 120_000;
      server.httpServer.headersTimeout = 130_000;
    }
  },
};

export default defineConfig({
  base: '/',
  plugins: [react(), dsjoKeepAlivePlugin],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // ck3_chronicler-j32g: explicit /api/sse rule sits BEFORE /api so
      // SSE traffic gets long-lived-connection settings (no idle/proxy
      // timeouts) without bleeding those onto regular /api requests.
      // The previous /sse rule was dead code — actual route is /api/sse,
      // so the /api rule was capturing SSE traffic with default timeouts
      // that were closing the connection every ~18s and forcing
      // EventSource to reconnect (losing narrative_completed frames in
      // the disconnect window).
      '/api/sse': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        ws: false,
        // No idle timeout: SSE connections live until the client
        // disconnects (or the BE heartbeat loop fails). Both
        // http-proxy-middleware values must be 0 — `timeout` is the
        // incoming-request timeout, `proxyTimeout` is the upstream-
        // request timeout.
        timeout: 0,
        proxyTimeout: 0,
        // ck3_chronicler-dsjo: Node-side keepAliveTimeout fix lives in
        // the dsjoKeepAlivePlugin at the top of this file (bumps
        // server.httpServer.keepAliveTimeout 5s → 120s before listen).
        // The fix is defense-in-depth alongside the BE heartbeat 2s +
        // uvicorn timeout_keep_alive=120 changes from commit 745ca92.
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: '../src/chronicler/api/static/app',
    emptyOutDir: true,
    // audit F-57 / ck3_chronicler-hgnb: 'hidden' source maps are
    // emitted alongside the bundle but not referenced by a //# sourceMappingURL
    // comment, so production users don't fetch them but `chronicler dev`
    // can still resolve stack traces against the original sources when
    // diagnosing field reports.
    sourcemap: 'hidden',
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Vitest's default test pattern would otherwise sweep up the
    // Playwright suite under e2e/ — those run via `npm run e2e`, not vitest.
    exclude: ['node_modules', 'dist', 'e2e/**', 'test-results/**'],
  },
});

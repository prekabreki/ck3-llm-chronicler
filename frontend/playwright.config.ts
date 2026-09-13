import { defineConfig, devices } from '@playwright/test';

// Playwright config — boots `npm run dev` (Vite) before tests, runs the
// smoke + interaction E2E suite against http://localhost:5173.
//
// We don't yet need the FastAPI backend running for Phase 1 tests since
// the React shell renders without any /api/ calls. Phase 2+ will add a
// `webServer` array with both the Vite dev server and `chronicler serve`.

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});

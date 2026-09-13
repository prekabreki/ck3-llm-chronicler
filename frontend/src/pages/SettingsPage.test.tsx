// SettingsPage — paths card + ProseRepoCard wiring + cost / debug-log
// surfaces. ck3_chronicler-tbrm.4 collapsed the v0.9 three-card
// provider grid into a single ProseRepoCard.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { SettingsPage } from './SettingsPage';
import { useAppStore } from '../store/appStore';
import * as client from '../api/client';
import type { PathsSettings } from '../api/client';
import type {
  CostSummaryResponse,
  LLMPauseResponse,
  NarrativeBackendResponse,
  ProseRepoStatusResponse,
  ProviderStatusResponse,
  ResolvedModelsResponse,
} from '../api/types';

const STATUS: ProviderStatusResponse = {
  mode: 'claude-code',
  model: 'claude-opus-4-7[1m]',
  recent_biographies: 4,
  avg_biography_ms: 84200,
};

const COST: CostSummaryResponse = {
  this_campaign: { input_tokens: 36000, output_tokens: 24000, usd: 1.23 },
  this_month: { input_tokens: 12000, output_tokens: 8000, usd: 0.41 },
};

const PATHS: PathsSettings = {
  save_dir: {
    resolved: 'C:/Users/test/Documents/Paradox Interactive/Crusader Kings III/save games',
    source: 'default',
    exists: true,
    override: null,
  },
  ck3_install_dir: {
    resolved: 'C:/Program Files (x86)/Steam/steamapps/common/Crusader Kings III',
    source: 'probe',
    exists: true,
    override: null,
  },
  archive_dir: {
    resolved: 'C:/Users/test/AppData/Local/chronicler/archived',
    source: 'default',
    exists: true,
    override: null,
  },
  archive_git_root: null,
};

const PROSE_REPO_GREEN: ProseRepoStatusResponse = {
  path: 'C:/git/ck3_chronicler_prose',
  source: 'default',
  override: null,
  exists: true,
  git_initialized: true,
  claude_md_present: true,
};

const MODELS_DEFAULT: ResolvedModelsResponse = {
  biography: 'claude-opus-4-7[1m]',
  closing: 'claude-opus-4-7[1m]',
  global_override: null,
};

// Issue #23: the backend picker's payload. presets carries the endpoint +
// key requirement per preset, which is what the picker prefills from.
const BACKEND_CLAUDE_CODE: NarrativeBackendResponse = {
  backend: 'claude-code',
  source: 'default',
  valid_backends: ['anthropic', 'claude-code', 'openai-compatible'],
  valid_presets: ['deepseek', 'lmstudio', 'ollama', 'openai', 'openrouter'],
  presets: [
    { id: 'deepseek', base_url: 'https://api.deepseek.com/v1', requires_key: true },
    { id: 'lmstudio', base_url: 'http://localhost:1234/v1', requires_key: false },
    { id: 'ollama', base_url: 'http://localhost:11434/v1', requires_key: false },
    { id: 'openai', base_url: 'https://api.openai.com/v1', requires_key: true },
    { id: 'openrouter', base_url: 'https://openrouter.ai/api/v1', requires_key: true },
  ],
  openai_preset: null,
  openai_base_url: null,
  openai_model: null,
  openai_key: { present: false, source: null },
  anthropic_key: { present: false, source: null },
  usable: true,
  error: null,
};

const PAUSE_LIVE: LLMPauseResponse = {
  paused: false,
  paused_at: null,
  last_drain: null,
};

function renderWithClient(ui: React.ReactNode): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  useAppStore.setState({
    view: 'settings',
    activeCampaign: 'Wessex',
    selectedCharacterId: null,
  });
  // Default the paths + prose-repo + models + pause queries so cost /
  // migration tests don't have to know about every dependency.
  vi.spyOn(client, 'getPathsSettings').mockResolvedValue(PATHS);
  vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue(PROSE_REPO_GREEN);
  vi.spyOn(client, 'getResolvedModels').mockResolvedValue(MODELS_DEFAULT);
  vi.spyOn(client, 'getLLMPause').mockResolvedValue(PAUSE_LIVE);
  vi.spyOn(client, 'getNarrativeBackend').mockResolvedValue(BACKEND_CLAUDE_CODE);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Issue #23: same shape for the backend card — the shell mounts with a
// "Reading backend…" note, so wait for a data-dependent node.
async function awaitBackendCardLoaded(): Promise<HTMLElement> {
  await screen.findByTestId('backend-choice-claude-code');
  return screen.getByTestId('backend-card');
}

async function awaitProseCardLoaded(): Promise<HTMLElement> {
  // Wait for the path to render (data-dependent) before scoping further
  // queries. The card itself mounts synchronously with a "Reading…"
  // placeholder; we want the post-data state.
  await screen.findByText('On disk');
  return screen.getByTestId('prose-repo-card');
}

describe('SettingsPage provider card', () => {
  it('renders the prose repo card with the resolved model from provider-status', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);

    renderWithClient(<SettingsPage />);

    const card = await awaitProseCardLoaded();
    const utils = within(card);
    expect(utils.getByText('The Prose Repo')).toBeInTheDocument();
    expect(utils.getByText('claude-opus-4-7[1m]')).toBeInTheDocument();
    expect(utils.getByText('Active')).toBeInTheDocument();
    expect(utils.getByText('84.2 s')).toBeInTheDocument();
  });

  it('shows three readiness pips green when the prose repo is fully set up', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);

    renderWithClient(<SettingsPage />);

    const card = await awaitProseCardLoaded();
    const utils = within(card);
    expect(utils.getByText('On disk')).toBeInTheDocument();
    expect(utils.getByText('Git initialised')).toBeInTheDocument();
    expect(utils.getByText('CLAUDE.md present')).toBeInTheDocument();
  });

  it('flags a missing CLAUDE.md with the warn pip', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
    vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue({
      ...PROSE_REPO_GREEN,
      claude_md_present: false,
    });

    renderWithClient(<SettingsPage />);

    const card = await awaitProseCardLoaded();
    const claudeMdPip = within(card).getByText('CLAUDE.md present');
    // The matched element is the wrapper span (text is its direct
    // child), so the warn class is on that element, not parentElement.
    expect(claudeMdPip).toHaveClass('pip--warn');
  });

  it('shows the prose repo path under the readiness pips', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);

    renderWithClient(<SettingsPage />);

    const card = await awaitProseCardLoaded();
    const utils = within(card);
    expect(utils.getByText('C:/git/ck3_chronicler_prose')).toBeInTheDocument();
    expect(utils.getByText('Default')).toBeInTheDocument();
  });

  it('Edit button reveals an input pre-filled with the resolved path', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitProseCardLoaded();
    await user.click(within(card).getByRole('button', { name: 'Edit' }));
    const input = within(card).getByDisplayValue('C:/git/ck3_chronicler_prose');
    expect(input).toBeInTheDocument();
  });

  it('Save button calls updateProseRepoSettings with the new path', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
    const updateSpy = vi
      .spyOn(client, 'updateProseRepoSettings')
      .mockResolvedValue({
        ...PROSE_REPO_GREEN,
        path: 'D:/forks/prose',
        source: 'override',
        override: 'D:/forks/prose',
      });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitProseCardLoaded();
    await user.click(within(card).getByRole('button', { name: 'Edit' }));
    const input = within(card).getByDisplayValue(
      'C:/git/ck3_chronicler_prose',
    );
    await user.clear(input);
    await user.type(input, 'D:/forks/prose');
    await user.click(within(card).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ path: 'D:/forks/prose' }),
    );
  });

  it('Reset button sends a null path to clear the override', async () => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
    vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue({
      ...PROSE_REPO_GREEN,
      source: 'override',
      override: 'D:/forks/prose',
      path: 'D:/forks/prose',
    });
    const updateSpy = vi
      .spyOn(client, 'updateProseRepoSettings')
      .mockResolvedValue(PROSE_REPO_GREEN);

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitProseCardLoaded();
    await user.click(within(card).getByRole('button', { name: 'Edit' }));
    const resetBtn = within(card).getByRole('button', {
      name: /Reset to default/,
    });
    await user.click(resetBtn);

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ path: null }),
    );
  });
});

// ck3_chronicler-5d9o: LLMEngineCard renders the per-kind resolved model
// snapshot from GET /api/settings/models. Sits alongside ProseRepoCard in
// the Provider & LLM section.
describe('SettingsPage LLM engine card', () => {
  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('renders the resolved per-kind models with Opus for bio+closing', async () => {
    renderWithClient(<SettingsPage />);

    // Wait for the biography row to mount — it only appears once the
    // models query resolves, so finding it confirms data-loaded state.
    const biographyRow = await screen.findByTestId('llm-model-biography');
    expect(biographyRow).toHaveTextContent('claude-opus-4-7[1m]');

    const card = screen.getByTestId('llm-engine-card');
    const utils = within(card);
    expect(utils.getByText('The engine')).toBeInTheDocument();
    expect(utils.getByTestId('llm-model-closing')).toHaveTextContent(
      'claude-opus-4-7[1m]',
    );
    // Memory row is gone.
    expect(utils.queryByTestId('llm-model-memory')).not.toBeInTheDocument();
    // The global-override callout must NOT appear when no legacy env is set —
    // its presence would be a UX bug (the user'd think they had an active
    // override when they didn't).
    expect(utils.queryByTestId('llm-global-override')).not.toBeInTheDocument();
  });

  it('surfaces the global override callout when a global model is set', async () => {
    vi.spyOn(client, 'getResolvedModels').mockResolvedValue({
      biography: 'claude-haiku-4-5',
      closing: 'claude-haiku-4-5',
      global_override: 'claude-haiku-4-5',
    });

    renderWithClient(<SettingsPage />);

    // Wait for the override callout itself — it only renders once data
    // arrives, so finding it confirms the post-load state.
    const callout = await screen.findByTestId('llm-global-override');
    // The callout names the env var so a user who set it there (rather
    // than in the UI) knows what to unset.
    expect(callout).toHaveTextContent(/CHRONICLER_NARRATIVE_MODEL/);
    expect(callout).toHaveTextContent('claude-haiku-4-5');

    const card = screen.getByTestId('llm-engine-card');
    const utils = within(card);
    // Memory row is gone; bio + closing rows still render and resolve to the override.
    expect(utils.queryByTestId('llm-model-memory')).not.toBeInTheDocument();
    expect(utils.getByTestId('llm-model-biography')).toHaveTextContent(
      'claude-haiku-4-5',
    );
    expect(utils.getByTestId('llm-model-closing')).toHaveTextContent(
      'claude-haiku-4-5',
    );
  });

  // ck3_chronicler-gx7b: pause toggle.

  it('renders the Live status when pause is off', async () => {
    renderWithClient(<SettingsPage />);

    const pip = await screen.findByTestId('llm-pause-status-pip');
    expect(pip).toHaveTextContent('Live');
    expect(pip).not.toHaveClass('pip--warn');

    // The toggle button reads "Pause LLM generation" while Live.
    const toggle = screen.getByTestId('llm-pause-toggle');
    expect(toggle).toHaveTextContent('Pause LLM generation');
  });

  it('renders the Paused status + paused_at when pause is on', async () => {
    vi.spyOn(client, 'getLLMPause').mockResolvedValue({
      paused: true,
      paused_at: '2026-05-11T19:00:00+02:00',
      last_drain: null,
    });

    renderWithClient(<SettingsPage />);

    const pip = await screen.findByTestId('llm-pause-status-pip');
    expect(pip).toHaveTextContent('Paused');
    expect(pip).toHaveClass('pip--warn');

    const since = screen.getByTestId('llm-paused-since');
    expect(since).toHaveTextContent('2026-05-11T19:00:00+02:00');

    // Button copy flips to "Resume" while paused.
    const toggle = screen.getByTestId('llm-pause-toggle');
    expect(toggle).toHaveTextContent('Resume LLM generation');
  });

  it('PUTs paused=true when toggling from Live', async () => {
    const putSpy = vi.spyOn(client, 'putLLMPause').mockResolvedValue({
      paused: true,
      paused_at: '2026-05-11T19:30:00+02:00',
      last_drain: null,
    });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();

    const toggle = await screen.findByTestId('llm-pause-toggle');
    await user.click(toggle);

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({ paused: true }),
    );
  });

  it('PUTs paused=false when toggling from Paused, then renders the last_drain counts', async () => {
    vi.spyOn(client, 'getLLMPause').mockResolvedValue({
      paused: true,
      paused_at: '2026-05-11T19:00:00+02:00',
      last_drain: null,
    });
    const putSpy = vi.spyOn(client, 'putLLMPause').mockResolvedValue({
      paused: false,
      paused_at: null,
      last_drain: {
        biographies_scheduled: 4,
      },
    });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();

    const toggle = await screen.findByTestId('llm-pause-toggle');
    await user.click(toggle);

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({ paused: false }),
    );

    // After the PUT resolves, the cache patches in the new state. The
    // last_drain section renders the biography count so the user knows
    // what the unpause kicked off.
    const drainSummary = await screen.findByTestId('llm-last-drain');
    expect(drainSummary).toHaveTextContent('4');
  });
});

describe('SettingsPage cost dashboard', () => {
  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('renders the cost cards from the summary endpoint', async () => {
    renderWithClient(<SettingsPage />);

    // tbrm.6: token totals replaced the USD column. 36k + 24k = 60k
    // for this_campaign; 12k + 8k = 20k for this_month.
    await waitFor(() => screen.getByText('60,000'));
    expect(screen.getByText('60,000')).toBeInTheDocument();
    expect(screen.getByText('20,000')).toBeInTheDocument();
    expect(screen.getByText('36,000')).toBeInTheDocument();
  });

  it('shows the empty-state when no campaign is active for cost', async () => {
    useAppStore.setState({ activeCampaign: null });
    renderWithClient(<SettingsPage />);
    await waitFor(() =>
      expect(
        screen.getByText(/Select a campaign to view its token spend/),
      ).toBeInTheDocument(),
    );
  });
});

// ck3_chronicler-f9w.1: Settings paths panel. These tests pre-date
// tbrm.4 and survive the provider-card rewrite — the paths card and
// the prose-repo card live next to each other but their concerns are
// independent.
describe('SettingsPage paths card', () => {
  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('renders the resolved save dir + install dir from the API', async () => {
    renderWithClient(<SettingsPage />);
    await waitFor(() =>
      screen.getByText(/save games$/, { selector: 'code' }),
    );
    expect(screen.getByText('CK3 save directory')).toBeInTheDocument();
    expect(screen.getByText('CK3 install directory')).toBeInTheDocument();
  });

  // Issue #51: the archive row. Its git-root pip is the part worth a
  // test — repointing the dir into a checkout silently turns on
  // commit-and-push, and the row is the only place that says so.
  it('renders the sealed archive row and reports it is not in a git repo', async () => {
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText('Sealed campaign archive'));
    expect(
      screen.getByText('C:/Users/test/AppData/Local/chronicler/archived'),
    ).toBeInTheDocument();
    expect(screen.getByText('Not a git repo')).toBeInTheDocument();
  });

  it('warns when the archive dir sits inside a git checkout', async () => {
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      ...PATHS,
      archive_dir: {
        resolved: 'C:/git/backups/sealed',
        source: 'override',
        exists: true,
        override: 'C:/git/backups/sealed',
      },
      archive_git_root: 'C:/git/backups',
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText('Git repo'));
    expect(screen.getByText('Git repo')).toBeInTheDocument();
    expect(screen.queryByText('Not a git repo')).not.toBeInTheDocument();
  });

  it('saving the archive row sends only archive_dir', async () => {
    const updateSpy = vi
      .spyOn(client, 'updatePathsSettings')
      .mockResolvedValue(PATHS);
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();

    await waitFor(() => screen.getByText('Sealed campaign archive'));
    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    // Third paths row: save dir, install dir, archive dir.
    await user.click(editButtons[2]!);
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Absolute path…'),
    );
    await user.clear(input);
    await user.type(input, 'D:/sealed');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ archive_dir: 'D:/sealed' }),
    );
  });

  it('labels the override source on a user-set save dir', async () => {
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      ...PATHS,
      save_dir: {
        resolved: 'D:/games/saves',
        source: 'override',
        exists: true,
        override: 'D:/games/saves',
      },
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getAllByText('Override').length > 0);
    expect(screen.getAllByText('Override').length).toBeGreaterThan(0);
    expect(screen.getByText('D:/games/saves')).toBeInTheDocument();
  });

  it('flags exists=false with a warning pip', async () => {
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      ...PATHS,
      save_dir: {
        resolved: 'D:/typo/saves',
        source: 'override',
        exists: false,
        override: 'D:/typo/saves',
      },
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText('Missing'));
    expect(screen.getByText('Missing')).toBeInTheDocument();
  });

  it('reports "not found" when the install probe fails', async () => {
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      ...PATHS,
      ck3_install_dir: {
        resolved: '',
        source: 'probe',
        exists: false,
        override: null,
      },
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText('— not found —'));
    expect(screen.getByText('Not found')).toBeInTheDocument();
  });

  it('clicking save dir Edit reveals an input pre-filled with the current path', async () => {
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByText('Edit').length);
    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[0]!);
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Absolute path…'),
    );
    expect(input).toHaveValue(PATHS.save_dir.resolved);
  });

  it('Save button calls updatePathsSettings with the new path', async () => {
    const updateSpy = vi
      .spyOn(client, 'updatePathsSettings')
      .mockResolvedValue({
        ...PATHS,
        save_dir: {
          resolved: 'E:/new/path',
          source: 'override',
          exists: true,
          override: 'E:/new/path',
        },
      });
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();

    await waitFor(() => screen.getAllByRole('button', { name: 'Edit' }).length);
    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[0]!);

    const input = await waitFor(() =>
      screen.getByPlaceholderText('Absolute path…'),
    );
    await user.clear(input);
    await user.type(input, 'E:/new/path');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ save_dir: 'E:/new/path' }),
    );
  });

  it('Reset button sends a null override to the backend', async () => {
    vi.spyOn(client, 'getPathsSettings').mockResolvedValue({
      ...PATHS,
      save_dir: {
        resolved: 'D:/games/saves',
        source: 'override',
        exists: true,
        override: 'D:/games/saves',
      },
    });
    const updateSpy = vi
      .spyOn(client, 'updatePathsSettings')
      .mockResolvedValue(PATHS);
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();

    await waitFor(() => screen.getAllByRole('button', { name: 'Edit' }).length);
    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[0]!);

    const resetButton = await waitFor(() =>
      screen.getByRole('button', { name: /Reset to default/ }),
    );
    await user.click(resetButton);

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ save_dir: null }),
    );
  });

  it('Cancel reverts the editor back to the resolved value', async () => {
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    await waitFor(() => screen.getAllByRole('button', { name: 'Edit' }).length);
    const editButtons = screen.getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[0]!);
    const input = await waitFor(() =>
      screen.getByPlaceholderText('Absolute path…'),
    );
    await user.clear(input);
    await user.type(input, 'something else');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    // Editor should be gone; the resolved path is back as <code>.
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('Absolute path…')).toBeNull(),
    );
  });

  // ck3_chronicler-z6jm slice 1: debug-log panel
  it('shows debug-log size and path under threshold (z6jm)', async () => {
    vi.spyOn(client, 'getDebugLogStatus').mockResolvedValue({
      exists: true,
      size_bytes: 50 * 1024 * 1024,
      threshold_bytes: 200 * 1024 * 1024,
      exceeded: false,
      path: 'C:/fake/debug.log',
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText('C:/fake/debug.log'));
    expect(screen.getByText('50.0 MB')).toBeInTheDocument();
    expect(
      screen.queryByText(/exceeded the rotation threshold/),
    ).toBeNull();
  });

  it('renders an alert when debug log exceeds threshold (z6jm)', async () => {
    vi.spyOn(client, 'getDebugLogStatus').mockResolvedValue({
      exists: true,
      size_bytes: 250 * 1024 * 1024,
      threshold_bytes: 200 * 1024 * 1024,
      exceeded: true,
      path: 'C:/fake/debug.log',
    });
    renderWithClient(<SettingsPage />);
    await waitFor(() => screen.getByText(/exceeded the rotation threshold/));
    expect(screen.getByText('250.0 MB')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rotate now' })).not.toBeDisabled();
  });

  it('rotate button calls API and surfaces the result message (z6jm)', async () => {
    vi.spyOn(client, 'getDebugLogStatus').mockResolvedValue({
      exists: true,
      size_bytes: 250 * 1024 * 1024,
      threshold_bytes: 200 * 1024 * 1024,
      exceeded: true,
      path: 'C:/fake/debug.log',
    });
    const rotateSpy = vi.spyOn(client, 'rotateDebugLog').mockResolvedValue({
      rotated: true,
      archive_path: 'C:/fake/archives/debug-20260507-1300.log.gz',
      offsets_reset: 2,
      message: 'archived to C:/fake/archives/debug-20260507-1300.log.gz',
    });
    const user = userEvent.setup();
    renderWithClient(<SettingsPage />);
    const button = await waitFor(() =>
      screen.getByRole('button', { name: 'Rotate now' }),
    );
    await user.click(button);
    await waitFor(() => expect(rotateSpy).toHaveBeenCalled());
    await waitFor(() =>
      screen.getByText(/archived to C:\/fake\/archives/),
    );
    expect(screen.getByText(/2 campaign offsets reset/)).toBeInTheDocument();
  });
});

// --- issue #23: backend picker, editable models, prose Initialize ---

describe('SettingsPage backend card', () => {
  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('offers all three backends and marks the one in use', async () => {
    renderWithClient(<SettingsPage />);

    const card = await awaitBackendCardLoaded();
    const utils = within(card);
    expect(utils.getByTestId('backend-choice-claude-code')).toHaveTextContent(
      'in use',
    );
    expect(utils.getByTestId('backend-choice-anthropic')).toBeInTheDocument();
    expect(
      utils.getByTestId('backend-choice-openai-compatible'),
    ).toBeInTheDocument();
    // No target fields until a backend that has one is selected.
    expect(utils.queryByTestId('backend-openai-fields')).toBeNull();
  });

  it('PUTs the chosen backend and nothing else', async () => {
    const putSpy = vi
      .spyOn(client, 'updateNarrativeBackend')
      .mockResolvedValue({ ...BACKEND_CLAUDE_CODE, backend: 'anthropic', source: 'settings' });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitBackendCardLoaded();

    await user.click(
      within(card).getByTestId('backend-choice-anthropic').querySelector('input')!,
    );
    await user.click(within(card).getByTestId('backend-apply'));

    // Exactly one field: an untouched key field must not be sent at all.
    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({ backend: 'anthropic' }),
    );
  });

  it('prefills the base-URL placeholder from the chosen preset', async () => {
    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitBackendCardLoaded();

    await user.click(
      within(card)
        .getByTestId('backend-choice-openai-compatible')
        .querySelector('input')!,
    );
    await user.selectOptions(
      within(card).getByTestId('openai-preset'),
      'deepseek',
    );

    expect(within(card).getByTestId('openai-base-url')).toHaveAttribute(
      'placeholder',
      'https://api.deepseek.com/v1',
    );
    expect(within(card).getByTestId('openai-key-field')).toHaveTextContent(
      /Required by this preset/,
    );
  });

  it('saving the endpoint sends the target fields but never a key field', async () => {
    const putSpy = vi
      .spyOn(client, 'updateNarrativeBackend')
      .mockResolvedValue(BACKEND_CLAUDE_CODE);

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitBackendCardLoaded();

    await user.click(
      within(card)
        .getByTestId('backend-choice-openai-compatible')
        .querySelector('input')!,
    );
    await user.selectOptions(within(card).getByTestId('openai-preset'), 'ollama');
    await user.type(within(card).getByTestId('openai-model'), 'qwen3:14b');
    await user.click(within(card).getByTestId('openai-save'));

    // The blank-on-save bug: the payload must carry no *_api_key key at
    // all, because a missing field is what the backend leaves untouched.
    await waitFor(() => expect(putSpy).toHaveBeenCalled());
    const body = putSpy.mock.calls[0]![0];
    expect(body).toEqual({
      backend: 'openai-compatible',
      openai_preset: 'ollama',
      openai_base_url: '',
      openai_model: 'qwen3:14b',
    });
    expect('openai_api_key' in body).toBe(false);
  });

  it('reports a stored key by presence only, and can forget it', async () => {
    vi.spyOn(client, 'getNarrativeBackend').mockResolvedValue({
      ...BACKEND_CLAUDE_CODE,
      backend: 'anthropic',
      source: 'settings',
      anthropic_key: { present: true, source: 'settings' },
    });
    const putSpy = vi
      .spyOn(client, 'updateNarrativeBackend')
      .mockResolvedValue(BACKEND_CLAUDE_CODE);

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitBackendCardLoaded();

    // Presence + tier, never the key itself; the input starts empty so
    // there is no masked value that could be re-submitted.
    expect(within(card).getByTestId('anthropic-key-pip')).toHaveTextContent(
      'Stored (this app)',
    );
    expect(within(card).getByTestId('anthropic-key-input')).toHaveValue('');
    // Save is disabled until something is typed — no accidental blanking.
    expect(within(card).getByTestId('anthropic-key-save')).toBeDisabled();

    await user.click(within(card).getByTestId('anthropic-key-forget'));
    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({ anthropic_api_key: '' }),
    );
  });

  it('surfaces an unusable configuration verbatim', async () => {
    vi.spyOn(client, 'getNarrativeBackend').mockResolvedValue({
      ...BACKEND_CLAUDE_CODE,
      backend: 'openai-compatible',
      source: 'settings',
      usable: false,
      error: 'backend=openai-compatible but no endpoint is configured',
    });

    renderWithClient(<SettingsPage />);
    const err = await screen.findByTestId('backend-error');
    expect(err).toHaveTextContent('no endpoint is configured');
  });
});

describe('SettingsPage editable model rows', () => {
  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('saves one kind without touching the others', async () => {
    const putSpy = vi
      .spyOn(client, 'updateResolvedModels')
      .mockResolvedValue({ ...MODELS_DEFAULT, biography: 'claude-sonnet-5' });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const rows = await screen.findByTestId('llm-model-rows');

    // The Biography row's own Edit button (rows render in KIND_ROWS order
    // after the global row).
    const editButtons = within(rows).getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[1]!);
    const input = within(rows).getByPlaceholderText('e.g. claude-opus-5');
    await user.clear(input);
    await user.type(input, 'claude-sonnet-5');
    await user.click(within(rows).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(putSpy).toHaveBeenCalledWith({ biography: 'claude-sonnet-5' }),
    );
  });

  it('clearing a row sends an empty string, the backend clear signal', async () => {
    const putSpy = vi
      .spyOn(client, 'updateResolvedModels')
      .mockResolvedValue(MODELS_DEFAULT);

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const rows = await screen.findByTestId('llm-model-rows');

    const editButtons = within(rows).getAllByRole('button', { name: 'Edit' });
    await user.click(editButtons[2]!);
    await user.click(
      within(rows).getByRole('button', { name: /Reset to default/ }),
    );

    await waitFor(() => expect(putSpy).toHaveBeenCalledWith({ closing: '' }));
  });

  it('renders the resolved value each row will actually run', async () => {
    renderWithClient(<SettingsPage />);
    const bio = await screen.findByTestId('llm-model-biography');
    expect(bio).toHaveTextContent('claude-opus-4-7[1m]');
  });
});

describe('SettingsPage prose repo initialize', () => {
  const PROSE_MISSING: ProseRepoStatusResponse = {
    path: '/home/u/.local/share/chronicler/prose',
    source: 'default',
    override: null,
    exists: false,
    git_initialized: false,
    claude_md_present: false,
  };

  beforeEach(() => {
    vi.spyOn(client, 'getProviderStatus').mockResolvedValue(STATUS);
    vi.spyOn(client, 'getCostSummary').mockResolvedValue(COST);
  });

  it('offers no Initialize button when the chronicle directory is ready', async () => {
    renderWithClient(<SettingsPage />);
    await awaitProseCardLoaded();
    expect(screen.queryByTestId('prose-repo-init-button')).toBeNull();
  });

  it('offers Initialize when the directory is missing, then repaints the pips', async () => {
    vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue(PROSE_MISSING);
    const initSpy = vi.spyOn(client, 'initProseRepo').mockResolvedValue({
      status: {
        ...PROSE_MISSING,
        exists: true,
        git_initialized: true,
        claude_md_present: true,
        source: 'override',
        override: PROSE_MISSING.path,
      },
      created: true,
      already_initialised: false,
      git_initialised: true,
      notes: ['scaffolded the chronicle template into /home/u/prose'],
    });

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitProseCardLoaded();

    await user.click(within(card).getByTestId('prose-repo-init-button'));
    await waitFor(() => expect(initSpy).toHaveBeenCalledWith({}));

    // The mutation writes the post-scaffold status into the cache, so the
    // button is gone and the notes explain what happened.
    await waitFor(() =>
      expect(screen.queryByTestId('prose-repo-init-button')).toBeNull(),
    );
    expect(screen.getByTestId('prose-repo-init-notes')).toHaveTextContent(
      /scaffolded the chronicle template/,
    );
    expect(within(card).getByText('On disk')).not.toHaveClass('pip--warn');
  });

  it('a failed scaffold shows the error instead of a stuck spinner', async () => {
    vi.spyOn(client, 'getProseRepoStatus').mockResolvedValue(PROSE_MISSING);
    vi.spyOn(client, 'initProseRepo').mockRejectedValue(
      new Error('is not empty and does not look like a chronicle directory'),
    );

    renderWithClient(<SettingsPage />);
    const user = userEvent.setup();
    const card = await awaitProseCardLoaded();
    await user.click(within(card).getByTestId('prose-repo-init-button'));

    const err = await screen.findByTestId('prose-repo-init-error');
    expect(err).toHaveTextContent(/does not look like a chronicle directory/);
    // The button is back to its resting label, not "Scaffolding…".
    expect(screen.getByTestId('prose-repo-init-button')).not.toBeDisabled();
  });

  it('names the live backend in the card subtitle', async () => {
    vi.spyOn(client, 'getNarrativeBackend').mockResolvedValue({
      ...BACKEND_CLAUDE_CODE,
      backend: 'openai-compatible',
      source: 'settings',
      openai_preset: 'deepseek',
    });

    renderWithClient(<SettingsPage />);
    await awaitProseCardLoaded();
    // The old hardcoded "ClaudeCodeProvider · claude --print" was wrong on
    // two of the three backends.
    const sub = screen.getByTestId('prose-repo-backend-sub');
    expect(sub).toHaveTextContent('openai-compatible');
    expect(sub).toHaveTextContent('deepseek');
    expect(sub).not.toHaveTextContent('ClaudeCodeProvider');
  });
});

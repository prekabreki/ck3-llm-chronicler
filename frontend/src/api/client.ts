// Thin fetch wrapper around the chronicler HTTP API. All endpoints go
// through here so component code stays free of URL strings + error
// handling sprinkles. Errors are normalised into ApiError so TanStack
// Query renders them uniformly.

import type { CoaDefinition, Palette } from '../components/CoaTypes';
import type {
  AdoptSaveResponse,
  AutoTrackResponseBody,
  AutoTrackRules,
  AutoTrackRulesUpdate,
  BaselineResetResponse,
  BiographyCostEstimate,
  BiographyListEntry,
  BiographyResponse,
  CampaignRenameResponse,
  CampaignResponse,
  CharacterDetail,
  CharacterSummary,
  ClosingChronicleResponse,
  CoaHistoryResponse,
  CostSessionResetResponse,
  CostSessionSummary,
  CostSummaryResponse,
  DebugLogRotateResponse,
  DebugLogStatusResponse,
  DynastyResponse,
  EndSessionResponse,
  FamilyTreeResponse,
  FirstRunStatus,
  HeraldryExtractStarted,
  HeraldryStatus,
  ImportStartedResponse,
  ModelsUpdateRequest,
  NarrativeBackendResponse,
  NarrativeBackendUpdateRequest,
  NarrativeCharacterStatsResponse,
  NarrativeQueueCancelResponse,
  NarrativeQueueRegenerateResponse,
  NarrativeQueueReorderRequest,
  NarrativeQueueReorderResponse,
  NarrativeQueueResponse,
  PathsSettings,
  PathsSettingsUpdate,
  LLMPauseResponse,
  LLMPauseUpdate,
  ProseRepoInitRequest,
  ProseRepoInitResponse,
  ProseRepoStatusResponse,
  ProseRepoUpdate,
  ProviderStatusResponse,
  RegenerateBiographyResponse,
  LogEnvelope,
  ResolvedModelsResponse,
  SaveListResponse,
  SearchResponse,
  SuggestedCandidate,
  TrackedResponse,
} from './types';

// Re-export for callers that did `import {…} from './client'` before
// the types moved (audit F-32). Internal imports should reach into
// './types' directly; this keeps the delta-cost zero for old call sites.
export type {
  AdoptSaveResponse,
  AutoTrackResponseBody,
  BaselineResetResponse,
  BiographyCostEstimate,
  CoaHistoryEntry,
  CoaHistoryResponse,
  DebugLogRotateResponse,
  DynastyMember,
  DynastyResponse,
  DynastyVitaEntry,
  FirstRunStatus,
  HeraldryExtractStarted,
  HeraldryStatus,
  PathInfo,
  PathsSettings,
  PathsSettingsUpdate,
  ProseRepoStatusResponse,
  ProseRepoUpdate,
  RegenerateBiographyResponse,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

// audit F-30 / ck3_chronicler-zfhd: shared error parser. Was inlined
// in three FE clients; now exported so migrateClient + exportClient
// can reuse the same body-parse path (FastAPI emits {detail: string}
// uniformly; treat anything else as opaque text).
export async function readApiError(resp: Response): Promise<ApiError> {
  let detail = resp.statusText;
  try {
    const body = await resp.json();
    if (body && typeof body.detail === 'string') {
      detail = body.detail;
    }
  } catch {
    // body wasn't JSON — keep statusText
  }
  return new ApiError(resp.status, detail);
}

export async function fetchJson<T>(
  input: RequestInfo,
  init?: RequestInit,
): Promise<T> {
  const resp = await fetch(input, init);
  if (!resp.ok) throw await readApiError(resp);
  return resp.json() as Promise<T>;
}

// audit F-31: shared error-handling for endpoints that return 204
// (DELETE in particular). Was duplicated inline; now a one-liner.
async function fetchNoContent(input: RequestInfo, init?: RequestInit): Promise<void> {
  const resp = await fetch(input, init);
  if (!resp.ok) throw await readApiError(resp);
}

// audit L32 (ck3_chronicler-27ov.81): every JSON-body mutation repeated
// the same method + Content-Type header + JSON.stringify triple. One
// helper for the lot — the return type is driven by the caller's
// annotation, exactly as fetchJson is.
function sendJson<T>(
  url: string,
  method: 'POST' | 'PUT' | 'PATCH',
  body: unknown,
): Promise<T> {
  return fetchJson<T>(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// --- Campaigns ---

export function listCampaigns(
  options: { includeCounts?: boolean; includeArchived?: boolean } = {},
): Promise<CampaignResponse[]> {
  // audit L31 / ck3_chronicler-27ov.81: the original `| boolean` back-compat
  // arm had zero remaining callers (queries.ts passes the options object).
  const params = new URLSearchParams();
  if (options.includeCounts) params.set('include_counts', 'true');
  if (options.includeArchived) params.set('include_archived', 'true');
  const qs = params.toString();
  return fetchJson(`/api/campaigns${qs ? '?' + qs : ''}`);
}

export function getCampaign(name: string, includeCounts = false): Promise<CampaignResponse> {
  const url = includeCounts
    ? `/api/campaigns/${encodeURIComponent(name)}?include_counts=true`
    : `/api/campaigns/${encodeURIComponent(name)}`;
  return fetchJson(url);
}

// ck3_chronicler-bly
export function renameCampaign(
  currentName: string,
  newName: string,
): Promise<CampaignRenameResponse> {
  return sendJson(
    `/api/campaigns/${encodeURIComponent(currentName)}/rename`,
    'POST',
    { name: newName },
  );
}

// ck3_chronicler-ezpc: hard-delete a campaign. Removes the registry
// row + tracked rows + the per-campaign SQLite file + (when running
// in a git checkout) the archive snapshot pair under data/archived/.
// Irreversible — UI wraps in a confirm modal.
export function deleteCampaign(name: string): Promise<void> {
  return fetchNoContent(`/api/campaigns/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

// ck3_chronicler-yv8q: clear a wedged save-tail baseline. Deletes
// <campaign_db>.baseline.json so save-tail re-baselines on the next
// matching save. Recovery affordance for the "playthrough mismatch"
// boot-log error class — no event data is touched. Idempotent: a
// campaign with no baseline file returns deleted=false + 200.
export function resetBaseline(
  campaignName: string,
): Promise<BaselineResetResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/baseline`,
    { method: 'DELETE' },
  );
}

// --- Characters ---

export function listCharacters(
  campaignName: string,
  options: { limit?: number; offset?: number; q?: string; ids?: number[] } = {},
): Promise<CharacterSummary[]> {
  const params = new URLSearchParams();
  // ck3_chronicler-5oyz: ids takes precedence; the BE ignores limit/q
  // when ids is set, but we omit them anyway so the URL stays minimal.
  if (options.ids !== undefined && options.ids.length > 0) {
    params.set('ids', options.ids.join(','));
  } else {
    if (options.limit !== undefined) params.set('limit', String(options.limit));
    if (options.offset !== undefined) params.set('offset', String(options.offset));
    // ck3_chronicler-4i33: name-search filter feeding the Track-new-souls
    // typeahead. The backend handles whitespace normalisation; we only
    // skip empty.
    if (options.q !== undefined && options.q !== '') params.set('q', options.q);
  }
  const qs = params.toString();
  const url = `/api/campaigns/${encodeURIComponent(campaignName)}/characters${qs ? '?' + qs : ''}`;
  return fetchJson(url);
}

export function getCharacterDetail(
  campaignName: string,
  ck3Id: number,
): Promise<CharacterDetail> {
  return fetchJson(`/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}`);
}

// --- Biography ---

export function getCharacterBiography(
  campaignName: string,
  ck3Id: number,
): Promise<BiographyResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/biography`,
  );
}

// ck3_chronicler-7gw: regenerate a character's biography. Returns 202
// with the queue item_id so the caller can correlate SSE narrative_*
// frames; the new biography row is inserted as version+1 once the
// scheduler's task finishes. Type lives in types.ts (audit F-32).

export function regenerateBiography(
  campaignName: string,
  ck3Id: number,
): Promise<RegenerateBiographyResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/biography/regenerate`,
    { method: 'POST' },
  );
}

// --- Family tree ---

export function getFamilyTree(
  campaignName: string,
  ck3Id: number,
  options: { ancestorDepth?: number; descendantDepth?: number } = {},
): Promise<FamilyTreeResponse> {
  const params = new URLSearchParams();
  if (options.ancestorDepth !== undefined) {
    params.set('ancestor_depth', String(options.ancestorDepth));
  }
  if (options.descendantDepth !== undefined) {
    params.set('descendant_depth', String(options.descendantDepth));
  }
  const qs = params.toString();
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/family-tree${qs ? '?' + qs : ''}`,
  );
}

// --- Tracked ---

export function listTracked(campaignName: string): Promise<TrackedResponse[]> {
  return fetchJson(`/api/campaigns/${encodeURIComponent(campaignName)}/tracked`);
}

// ck3_chronicler-ogi: add a single character to the tracked list.
// Idempotent on the server side — re-adding updates note/role without
// resetting added_at. Returns the new (or refreshed) TrackedResponse.
export function addTracked(
  campaignName: string,
  body: { character_id: number; note?: string | null; role?: string | null },
): Promise<TrackedResponse> {
  return sendJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked`,
    'POST',
    body,
  );
}

// ck3_chronicler-ogi: remove a tracked character. 204 on success;
// persisted biographies are preserved — untracking only stops future
// auto-generation.
export function deleteTracked(
  campaignName: string,
  characterId: number,
): Promise<void> {
  return fetchNoContent(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/${characterId}`,
    { method: 'DELETE' },
  );
}

// ck3_chronicler-ogi: parse the latest CK3 autosave and add player +
// immediate family. Returns the per-candidate breakdown so the UI can
// say "added 3 souls (2 already tracked)".

export function autoTrackFromSave(
  campaignName: string,
  options: { forceResetPlaythrough?: boolean } = {},
): Promise<AutoTrackResponseBody> {
  const params = options.forceResetPlaythrough
    ? '?force_reset_playthrough=true'
    : '';
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/auto-track${params}`,
    { method: 'POST' },
  );
}

// ck3_chronicler-gw16: per-campaign auto-track rule flags. GET resolves
// missing fields against AUTO_TRACK_RULES_DEFAULT so the UI always sees
// concrete bools; PUT accepts a partial body so toggling one checkbox
// doesn't have to re-send the others.
export function getAutoTrackRules(
  campaignName: string,
): Promise<AutoTrackRules> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/auto-track-rules`,
  );
}

export function updateAutoTrackRules(
  campaignName: string,
  body: AutoTrackRulesUpdate,
): Promise<AutoTrackRules> {
  return sendJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/auto-track-rules`,
    'PUT',
    body,
  );
}

// ck3_chronicler-gw16: untracked-but-near-the-player living relatives
// for the Tracked rail's "Suggested souls" list. character_id defaults
// to the campaign's current player on the BE.
export function listSuggestedCandidates(
  campaignName: string,
  options: { characterId?: number; limit?: number } = {},
): Promise<SuggestedCandidate[]> {
  const params = new URLSearchParams();
  if (options.characterId !== undefined) {
    params.set('character_id', String(options.characterId));
  }
  if (options.limit !== undefined) {
    params.set('limit', String(options.limit));
  }
  const qs = params.toString();
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/suggested-candidates${qs ? '?' + qs : ''}`,
  );
}

// vysp.10: pause / resume / bump a tracked character. Each returns the
// updated TrackedResponse so the frontend can patch its TanStack Query
// cache in-place rather than refetching the full list.
export function pauseTracked(
  campaignName: string,
  characterId: number,
): Promise<TrackedResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/${characterId}/pause`,
    { method: 'POST' },
  );
}

export function resumeTracked(
  campaignName: string,
  characterId: number,
): Promise<TrackedResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/${characterId}/resume`,
    { method: 'POST' },
  );
}

export function bumpTracked(
  campaignName: string,
  characterId: number,
): Promise<TrackedResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/tracked/${characterId}/bump`,
    { method: 'POST' },
  );
}

// --- Settings / cost ---

export function getProviderStatus(): Promise<ProviderStatusResponse> {
  return fetchJson('/api/settings/provider-status');
}

// ck3_chronicler-5d9o: per-kind resolved model snapshot for the Settings
// "Models" panel — the values after settings-then-env-then-default
// resolution, not the raw stored ones.
export function getResolvedModels(): Promise<ResolvedModelsResponse> {
  return fetchJson('/api/settings/models');
}

// Issue #45/#23: writable models. Only the fields sent are applied, so a
// card that saves one row cannot blank its neighbours; '' clears a row
// back to env-then-default. The response is the re-resolved snapshot, so
// a global override still shadowing the row just saved shows up at once.
export function updateResolvedModels(
  body: ModelsUpdateRequest,
): Promise<ResolvedModelsResponse> {
  return sendJson('/api/settings/models', 'PUT', body);
}

// Issue #45/#23: backend selection + its target. The GET never returns a
// stored API key (only whether one is present, and from which tier); the
// PUT is partial for the same reason — see NarrativeBackendUpdateRequest.
export function getNarrativeBackend(): Promise<NarrativeBackendResponse> {
  return fetchJson('/api/settings/narrative-backend');
}

export function updateNarrativeBackend(
  body: NarrativeBackendUpdateRequest,
): Promise<NarrativeBackendResponse> {
  return sendJson('/api/settings/narrative-backend', 'PUT', body);
}

// ck3_chronicler-gx7b: global LLM pause toggle. PUT triggers a drain across
// every open campaign on the paused→unpaused edge; the returned payload's
// last_drain carries the aggregated counts so the FE can surface them.
export function getLLMPause(): Promise<LLMPauseResponse> {
  return fetchJson('/api/settings/llm-pause');
}

export function putLLMPause(
  body: LLMPauseUpdate,
): Promise<LLMPauseResponse> {
  return sendJson('/api/settings/llm-pause', 'PUT', body);
}

// ck3_chronicler-tbrm.4: prose repo settings — readiness status + path
// override. The Settings ProseRepoCard reads on mount + after every PUT.
export function getProseRepoStatus(): Promise<ProseRepoStatusResponse> {
  return fetchJson('/api/settings/prose-repo');
}

export function updateProseRepoSettings(
  body: ProseRepoUpdate,
): Promise<ProseRepoStatusResponse> {
  return sendJson('/api/settings/prose-repo', 'PUT', body);
}

// Issue #23: scaffold a chronicle directory (the GUI's `chronicler
// init-prose`). Slow enough to need a pending state — it copies the
// template and runs git init + one commit.
export function initProseRepo(
  body: ProseRepoInitRequest = {},
): Promise<ProseRepoInitResponse> {
  return sendJson('/api/settings/prose-repo/init', 'POST', body);
}

// ck3_chronicler-eev: process-wide narrative-generation queue snapshot.
// AppShell strip refetches on each `narrative_*` SSE frame.
export function getNarrativeQueue(): Promise<NarrativeQueueResponse> {
  return fetchJson('/api/settings/narrative-queue');
}

// ck3_chronicler-c5wq: queue-page mutations. All three operate on the
// process-wide narrative queue and return immediately (cancel is async
// at the scheduler but the endpoint awaits it before responding).

export function cancelNarrativeQueueItem(
  itemId: number,
): Promise<NarrativeQueueCancelResponse> {
  return fetchJson(`/api/settings/narrative-queue/${itemId}`, {
    method: 'DELETE',
  });
}

export function regenerateNarrativeQueueItem(
  itemId: number,
): Promise<NarrativeQueueRegenerateResponse> {
  return fetchJson(
    `/api/settings/narrative-queue/${itemId}/regenerate`,
    { method: 'POST' },
  );
}

export function reorderNarrativeQueue(
  body: NarrativeQueueReorderRequest,
): Promise<NarrativeQueueReorderResponse> {
  return sendJson('/api/settings/narrative-queue/reorder', 'PATCH', body);
}

// ck3_chronicler-fjln: per-character lifetime aggregates for the queue
// page's stats panel. Type defs live in types.ts; this client function
// just fetches them.
export function getNarrativeCharacterStats(): Promise<NarrativeCharacterStatsResponse> {
  return fetchJson('/api/settings/narrative-queue/character-stats');
}

// ck3_chronicler-z6jm slice 1: debug.log status + manual rotate.
// Both response types live in types.ts (audit F-32).

export function getDebugLogStatus(): Promise<DebugLogStatusResponse> {
  return fetchJson('/api/settings/debug-log-status');
}

export function rotateDebugLog(): Promise<DebugLogRotateResponse> {
  return fetchJson('/api/settings/debug-log-rotate', { method: 'POST' });
}

// ck3_chronicler-thpz.1: Dynasty Wall data — types in types.ts (audit F-32).

export function getDynasty(campaignName: string): Promise<DynastyResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/dynasty`,
  );
}

// ck3_chronicler (2026-05-09): Biographies tab on campaign-overview.
export function listCampaignBiographies(
  campaignName: string,
): Promise<BiographyListEntry[]> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/biographies`,
  );
}

// ck3_chronicler-f9w.1: GUI-set save dir + CK3 install dir overrides.
// Types live in types.ts (audit F-32).

export function getPathsSettings(): Promise<PathsSettings> {
  return fetchJson('/api/settings/paths');
}

export function updatePathsSettings(
  body: PathsSettingsUpdate,
): Promise<PathsSettings> {
  return sendJson('/api/settings/paths', 'PUT', body);
}

// ck3_chronicler-v2a: adopt a CK3 save into a campaign (existing or
// new). Server-side path; the file must be readable by the chronicler
// process. Returns the resolved CampaignResponse + import counts.
// Type lives in types.ts (audit F-32).

export function adoptSaveFromPath(
  savePath: string,
  forceResetPlaythrough = false,
): Promise<AdoptSaveResponse> {
  return sendJson('/api/campaigns/adopt-from-save', 'POST', {
    save_path: savePath,
    force_reset_playthrough: forceResetPlaythrough,
  });
}

// ck3_chronicler-fiv6 (was kdf): drainDeferredNarratives +
// setDeferNarratives were removed in 2026-05-08 alongside the
// local-tier providers. Hosted Claude Code has no GPU contention with
// CK3, so deferred-narrative mode no longer has a use case.

// ck3_chronicler-a3jc (f9w.2): heraldry-pipeline Settings card.
// Types live in types.ts (audit F-32).

export function getHeraldryStatus(): Promise<HeraldryStatus> {
  return fetchJson('/api/settings/heraldry');
}

export function startHeraldryExtract(
  force = false,
): Promise<HeraldryExtractStarted> {
  return sendJson('/api/settings/heraldry/extract', 'POST', { force });
}

// ck3_chronicler-7b8d: CoA history timeline. Types live in types.ts
// (audit F-32).

export function getCharacterCoaHistory(
  campaignName: string,
  ck3Id: number,
): Promise<CoaHistoryResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/coa-history`,
  );
}

// ck3_chronicler-kze6 (f9w.3): first-run wizard detection + dismiss.
// Type lives in types.ts (audit F-32).

export function getFirstRunStatus(): Promise<FirstRunStatus> {
  return fetchJson('/api/settings/first-run');
}

export function dismissFirstRunWizard(): Promise<{ wizard_dismissed_at: string }> {
  return fetchJson('/api/settings/first-run/dismiss', { method: 'POST' });
}

export function getCostSummary(
  campaignName: string,
): Promise<CostSummaryResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/cost-summary`,
  );
}

// Mark the session boundary for cost metering. Consolidation sweep
// removed (plan: cozy-coalescing-shannon); always returns 0 tracked_considered.
export function endSession(campaignName: string): Promise<EndSessionResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/cost/end-session`,
    { method: 'POST' },
  );
}

// Dual-meter snapshot — lifetime (all-time) and session (since the user's
// last reset) token buckets, plus the target the lifetime input bar is
// compared against (default 1_000_000; banners trigger at 75/100/150%).
export function getSessionSummary(
  campaignName: string,
): Promise<CostSessionSummary> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/cost/session-summary`,
  );
}

// Reset the session counter — writes a fresh ISO timestamp to the
// settings file's session_started_at. Lifetime numbers are unaffected.
// The reset is global (single user, single settings file): resetting
// from any campaign resets the meter view for all campaigns.
export function resetSessionCounter(
  campaignName: string,
): Promise<CostSessionResetResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/cost/reset-session`,
    { method: 'POST' },
  );
}

// ck3_chronicler-7bi5 (revived by cs1o): pre-regeneration token + USD
// estimate for the regenerate modal's cost panel.
export function getBiographyCostEstimate(
  campaignName: string,
  ck3Id: number,
): Promise<BiographyCostEstimate> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/biography/cost-estimate`,
  );
}

// --- Search ---

export function searchAll(
  q: string,
  options: { scope?: string; campaign?: string } = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q });
  if (options.scope) params.set('scope', options.scope);
  if (options.campaign) params.set('campaign', options.campaign);
  return fetchJson(`/api/search?${params.toString()}`);
}

// --- Heraldry ---

// ck3_chronicler-a3f: the named-color palette extracted from the CK3
// install. Static for the process lifetime, served from the chronicler
// data dir at /api/heraldry/assets/palette.json. Cached forever in the
// query layer.
export function getHeraldryPalette(): Promise<Palette> {
  return fetchJson('/api/heraldry/assets/palette.json');
}

// ck3_chronicler-7ao subsystem 2: per-character resolved CoA. Resolves
// the chain character → dynasty_house → coat_of_arms_id; returns null
// (404 → null) when the chain has gaps or save-tail hasn't persisted
// the row yet. The Chronicle folio falls back to the procedural shield
// in that case.
export function getCharacterCoa(
  campaignName: string,
  ck3Id: number,
): Promise<CoaDefinition | null> {
  return fetchJson<CoaDefinition>(
    `/api/campaigns/${encodeURIComponent(campaignName)}/characters/${ck3Id}/coa`,
  ).catch((err) => {
    // 404 is the documented "not yet persisted" shape — surface as null
    // so the consumer flips to the procedural fallback. Other errors
    // (network, 5xx) still propagate. audit F-09: was matching err.message
    // for the substring '404' — but ApiError.message is the bare detail
    // string, so real 404s missed the test (and unrelated errors whose
    // detail happened to contain '404' got silently swallowed).
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  });
}

// --- Closing chronicle ---

export function getClosingChronicle(
  campaignName: string,
): Promise<ClosingChronicleResponse | null> {
  // audit F-60: BE returns 200 + null when no chronicle has been
  // generated yet (not 404), so callers can branch on null without
  // an ApiError throw + retry-suppression dance.
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/closing-chronicle`,
  );
}

export function completeCampaign(
  campaignName: string,
): Promise<ClosingChronicleResponse> {
  return fetchJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/complete`,
    { method: 'POST' },
  );
}

// --- Save listing (ck3_chronicler-mb6q) ---

// Recent .ck3 files in the configured save_dir, mtime desc. Powers the
// ImportModal save-picker so users don't have to hand-type Windows
// paths. Returns 200 with empty saves[] when save_dir doesn't exist.
export function listSaves(limit = 10): Promise<SaveListResponse> {
  return fetchJson(`/api/save/list?limit=${limit}`);
}

// --- Import save ---

export function startImportSave(
  campaignName: string,
  savePath: string,
  options: { forceResetPlaythrough?: boolean } = {},
): Promise<ImportStartedResponse> {
  return sendJson(
    `/api/campaigns/${encodeURIComponent(campaignName)}/import-save`,
    'POST',
    {
      save_path: savePath,
      force_reset_playthrough: options.forceResetPlaythrough ?? false,
    },
  );
}


// --- Logs (yrv3) ---

export function getRecentLogs(opts: {
  limit?: number;
  minLevel?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
} = {}): Promise<LogEnvelope[]> {
  const params = new URLSearchParams();
  if (opts.limit != null) params.set('limit', String(opts.limit));
  if (opts.minLevel) params.set('min_level', opts.minLevel);
  const qs = params.toString();
  return fetchJson<LogEnvelope[]>(
    `/api/logs/recent${qs ? '?' + qs : ''}`,
  );
}

export function getKnownLoggers(): Promise<string[]> {
  return fetchJson<string[]>('/api/logs/loggers');
}

// ck3_chronicler-ghfi: ask the backend to exit. Returns once the BE
// has acknowledged with 202 — the process exits ~400 ms later on its
// own. The caller is expected to follow up with window.close() so
// the chromeless launcher window goes away alongside chronicler.
export async function haltChronicler(): Promise<void> {
  const resp = await fetch('/api/halt', { method: 'POST' });
  if (!resp.ok) throw await readApiError(resp);
}

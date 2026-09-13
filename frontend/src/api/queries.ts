// TanStack Query hooks per endpoint. Component code consumes only these
// hooks — never raw client.ts fetchers — so cache invalidation + retry +
// loading-state contracts are uniform across the app.

import { useEffect, useRef } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { useEventStream } from './useEventStream';

import {
  addTracked,
  autoTrackFromSave,
  bumpTracked,
  deleteTracked,
  getCampaign,
  getCharacterBiography,
  getCharacterCoa,
  getCharacterDetail,
  dismissFirstRunWizard,
  getCharacterCoaHistory,
  getBiographyCostEstimate,
  getCostSummary,
  getSessionSummary,
  getFamilyTree,
  getFirstRunStatus,
  getHeraldryPalette,
  getHeraldryStatus,
  getDynasty,
  cancelNarrativeQueueItem,
  listCampaignBiographies,
  getDebugLogStatus,
  getNarrativeCharacterStats,
  getNarrativeQueue,
  regenerateNarrativeQueueItem,
  reorderNarrativeQueue,
  getLLMPause,
  getNarrativeBackend,
  getPathsSettings,
  getProseRepoStatus,
  getProviderStatus,
  getResolvedModels,
  initProseRepo,
  putLLMPause,
  startHeraldryExtract,
  listCampaigns,
  listCharacters,
  listTracked,
  pauseTracked,
  endSession,
  resetSessionCounter,
  regenerateBiography,
  renameCampaign,
  resetBaseline,
  resumeTracked,
  updateNarrativeBackend,
  updatePathsSettings,
  updateProseRepoSettings,
  updateResolvedModels,
  type AutoTrackResponseBody,
  type DynastyResponse,
  type PathsSettings,
  type PathsSettingsUpdate,
  type ProseRepoStatusResponse,
  type ProseRepoUpdate,
  type CoaHistoryResponse,
  type FirstRunStatus,
  type HeraldryExtractStarted,
  type HeraldryStatus,
  type RegenerateBiographyResponse,
} from './client';
import {
  getMigrationStatus,
  haltSaveTail,
  listMigrationBackups,
  restoreFromBackup,
  runMigration,
} from './migrateClient';
import type {
  MigrationBackupEntry,
  MigrationRunResponse,
  MigrationStatusResponse,
} from './types';
import type { CoaDefinition, Palette } from '../components/CoaTypes';
import type {
  BaselineResetResponse,
  BiographyCostEstimate,
  BiographyListEntry,
  BiographyResponse,
  CampaignRenameResponse,
  CampaignResponse,
  CharacterDetail,
  CharacterSummary,
  CostSessionResetResponse,
  CostSessionSummary,
  CostSummaryResponse,
  DebugLogStatusResponse,
  EndSessionResponse,
  FamilyTreeResponse,
  LLMPauseResponse,
  LLMPauseUpdate,
  ModelsUpdateRequest,
  NarrativeBackendResponse,
  NarrativeBackendUpdateRequest,
  NarrativeCharacterStatsResponse,
  NarrativeQueueCancelResponse,
  NarrativeQueueRegenerateResponse,
  NarrativeQueueReorderResponse,
  NarrativeQueueResponse,
  ProseRepoInitRequest,
  ProseRepoInitResponse,
  ProviderStatusResponse,
  ResolvedModelsResponse,
  TrackedResponse,
} from './types';

export const queryKeys = {
  campaigns: (
    includeCounts: boolean,
    includeArchived: boolean = false,
  ) => ['campaigns', { includeCounts, includeArchived }] as const,
  campaign: (name: string, includeCounts: boolean) =>
    ['campaign', name, { includeCounts }] as const,
  characters: (campaignName: string) => ['characters', campaignName] as const,
  charactersSearch: (campaignName: string, q: string, limit: number) =>
    ['charactersSearch', campaignName, q, limit] as const,
  charactersByIds: (campaignName: string, ids: readonly number[]) =>
    ['charactersByIds', campaignName, ids.join(',')] as const,
  characterDetail: (campaignName: string, ck3Id: number) =>
    ['characterDetail', campaignName, ck3Id] as const,
  tracked: (campaignName: string) => ['tracked', campaignName] as const,
  biography: (campaignName: string, ck3Id: number) =>
    ['biography', campaignName, ck3Id] as const,
  familyTree: (
    campaignName: string,
    ck3Id: number,
    ancestorDepth: number | null = null,
    descendantDepth: number | null = null,
  ) =>
    [
      'familyTree',
      campaignName,
      ck3Id,
      { ancestorDepth, descendantDepth },
    ] as const,
  providerStatus: () => ['providerStatus'] as const,
  resolvedModels: () => ['resolvedModels'] as const,
  narrativeBackend: () => ['narrativeBackend'] as const,
  llmPause: () => ['llmPause'] as const,
  costSummary: (campaignName: string) =>
    ['costSummary', campaignName] as const,
  // ck3_chronicler-0224: dual-meter snapshot — separate key so the
  // 75/100/150% banner state machine can refetch on its own cadence
  // without churning the cost-summary cache (which the Settings page
  // also reads).
  sessionSummary: (campaignName: string) =>
    ['sessionSummary', campaignName] as const,
  // ck3_chronicler-7bi5 (revived by cs1o): per-character regenerate
  // estimate, fetched only while the modal is open.
  biographyCostEstimate: (campaignName: string, ck3Id: number) =>
    ['biographyCostEstimate', campaignName, ck3Id] as const,
  heraldryPalette: () => ['heraldryPalette'] as const,
  characterCoa: (campaignName: string, ck3Id: number) =>
    ['characterCoa', campaignName, ck3Id] as const,
  narrativeQueue: () => ['narrativeQueue'] as const,
  narrativeCharacterStats: () => ['narrativeCharacterStats'] as const,
  debugLogStatus: () => ['debugLogStatus'] as const,
  migrationStatus: () => ['migrationStatus'] as const,
  migrationBackups: () => ['migrationBackups'] as const,
  pathsSettings: () => ['pathsSettings'] as const,
  proseRepoStatus: () => ['proseRepoStatus'] as const,
  heraldryStatus: () => ['heraldryStatus'] as const,
  firstRunStatus: () => ['firstRunStatus'] as const,
  characterCoaHistory: (campaignName: string, ck3Id: number) =>
    ['characterCoaHistory', campaignName, ck3Id] as const,
  dynasty: (campaignName: string) => ['dynasty', campaignName] as const,
  campaignBiographies: (campaignName: string) =>
    ['campaignBiographies', campaignName] as const,
  // M-F14 (27ov.66): former stragglers — every key the app uses lives
  // here so a shape change is a compile error, not a dead invalidation.
  search: (q: string) => ['search', q] as const,
  closingChronicle: (campaignName: string) =>
    ['closing-chronicle', campaignName] as const,
  autoTrackRules: (campaignName: string) =>
    ['auto-track-rules', campaignName] as const,
  suggestedCandidates: (campaignName: string) =>
    ['suggested-candidates', campaignName] as const,
  hallOfFame: () => ['hall-of-fame'] as const,
  saveList: (limit: number | undefined) => ['save-list', { limit }] as const,
  // Invalidation prefixes — TanStack matches by prefix, so these hit
  // every parameterised variant of their family. Call-site literals
  // like ['campaigns'] are exactly what drifted before.
  campaignsAll: () => ['campaigns'] as const,
  campaignAll: (name?: string) =>
    name === undefined ? (['campaign'] as const) : (['campaign', name] as const),
};

// audit L32 (27ov.81): nearly every campaign-scoped read repeated the
// same nullable-campaign gate trio — `campaignName ?? ''` in the key,
// `campaignName as string` in the fetcher, and `enabled: campaignName
// !== null`. This helper centralises all three: the cast lives here once
// instead of at ~15 call sites, and a hook with a second gate (a ck3Id,
// a search needle) just ANDs its own condition via `options.enabled`.
function useCampaignQuery<T>(
  campaignName: string | null,
  keyFor: (campaignName: string) => readonly unknown[],
  fetcher: (campaignName: string) => Promise<T>,
  options: {
    enabled?: boolean;
    retry?: boolean;
    staleTime?: number;
    gcTime?: number;
    refetchInterval?: number | false;
  } = {},
): UseQueryResult<T> {
  const { enabled, ...rest } = options;
  return useQuery({
    queryKey: keyFor(campaignName ?? ''),
    queryFn: () => fetcher(campaignName as string),
    enabled: campaignName !== null && (enabled ?? true),
    ...rest,
  });
}

export function useCampaigns(
  options: { includeCounts?: boolean; includeArchived?: boolean } | boolean = false,
): UseQueryResult<CampaignResponse[]> {
  const opts =
    typeof options === 'boolean'
      ? { includeCounts: options, includeArchived: false }
      : options;
  const includeCounts = opts.includeCounts ?? false;
  const includeArchived = opts.includeArchived ?? false;
  return useQuery({
    queryKey: queryKeys.campaigns(includeCounts, includeArchived),
    queryFn: () => listCampaigns({ includeCounts, includeArchived }),
  });
}

export function useCampaign(
  name: string | null,
  includeCounts = false,
): UseQueryResult<CampaignResponse> {
  return useCampaignQuery(
    name,
    (n) => queryKeys.campaign(n, includeCounts),
    (n) => getCampaign(n, includeCounts),
  );
}

// audit F-41 / ck3_chronicler-6hjk: pulled in well under MAX_LIMIT
// (500 inclusive on the BE) so the codex doesn't 400 silently if the
// cap is ever lowered. 250 is plenty for the codex page's relevance-
// sorted top-of-list use case; the typeahead handles deeper queries.
const CODEX_LIST_LIMIT = 250;

export function useCharacters(
  campaignName: string | null,
): UseQueryResult<CharacterSummary[]> {
  return useCampaignQuery(
    campaignName,
    queryKeys.characters,
    (n) => listCharacters(n, { limit: CODEX_LIST_LIMIT }),
  );
}

// ck3_chronicler-5oyz: BE-driven search. Existing useCharacters returns
// the relevance-ranked top 250 — fine for the of-court rail's default
// view, but on large campaigns (80k+ characters) tracked souls and
// search needles like "Ramon" never make it into that window. This
// hook hits the same endpoint with ?q= so the BE's ranked search
// surfaces matches across every soul in the DB.
export function useCharactersSearch(
  campaignName: string | null,
  q: string,
  options: { limit?: number; minLength?: number; staleTime?: number } = {},
): UseQueryResult<CharacterSummary[]> {
  const trimmed = q.trim();
  const limit = options.limit ?? CODEX_LIST_LIMIT;
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.charactersSearch(n, trimmed, limit),
    (n) => listCharacters(n, { q: trimmed, limit }),
    {
      enabled: trimmed.length >= (options.minLength ?? 1),
      staleTime: options.staleTime ?? 0,
    },
  );
}

// ck3_chronicler-5oyz: batched lookup keyed on tracked-character ids.
// Backs the Codex tracked rail so it renders independently of the
// relevance-ranked top-N window. Cache key is the joined id list, so
// re-ordering or adding/removing a tracked character invalidates
// cleanly. Empty input bypasses the query (returns disabled).
export function useCharactersByIds(
  campaignName: string | null,
  ids: readonly number[],
): UseQueryResult<CharacterSummary[]> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.charactersByIds(n, ids),
    (n) => listCharacters(n, { ids: ids.slice() }),
    { enabled: ids.length > 0 },
  );
}

export function useCharacterDetail(
  campaignName: string | null,
  ck3Id: number | null,
): UseQueryResult<CharacterDetail> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.characterDetail(n, ck3Id ?? -1),
    (n) => getCharacterDetail(n, ck3Id as number),
    { enabled: ck3Id !== null },
  );
}

export function useTracked(
  campaignName: string | null,
): UseQueryResult<TrackedResponse[]> {
  return useCampaignQuery(campaignName, queryKeys.tracked, listTracked);
}

// vysp.10: pause / resume / bump mutations. Each patches the tracked
// list cache in-place with the returned TrackedResponse so the UI
// reflects the new state without a refetch.
function _patchTrackedCache(
  qc: ReturnType<typeof useQueryClient>,
  campaignName: string,
  updated: TrackedResponse,
): void {
  qc.setQueryData<TrackedResponse[]>(
    queryKeys.tracked(campaignName),
    (rows) => rows?.map((r) => (r.character_id === updated.character_id ? updated : r)),
  );
}

// audit L28 (27ov.81): mutations that change tracked *membership* (add /
// delete / auto-track) must refresh the suggested-candidates list too —
// tracking a soul removes them from the suggestions, untracking can
// re-surface them. Previously only TrackedPage's own Track button did
// this; tracking via the Codex or auto-track left the "Suggested souls"
// list showing the now-tracked character until a manual refetch.
// (pause / resume / bump don't change membership, so they patch in place
// via _patchTrackedCache and skip this.)
function _invalidateTrackedMembership(
  qc: ReturnType<typeof useQueryClient>,
  campaignName: string,
): void {
  void qc.invalidateQueries({ queryKey: queryKeys.tracked(campaignName) });
  void qc.invalidateQueries({
    queryKey: queryKeys.suggestedCandidates(campaignName),
  });
}

export function usePauseTracked(
  campaignName: string,
): UseMutationResult<TrackedResponse, Error, number> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (characterId: number) => pauseTracked(campaignName, characterId),
    onSuccess: (updated) => _patchTrackedCache(qc, campaignName, updated),
  });
}

export function useResumeTracked(
  campaignName: string,
): UseMutationResult<TrackedResponse, Error, number> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (characterId: number) => resumeTracked(campaignName, characterId),
    onSuccess: (updated) => _patchTrackedCache(qc, campaignName, updated),
  });
}

export function useBumpTracked(
  campaignName: string,
): UseMutationResult<TrackedResponse, Error, number> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (characterId: number) => bumpTracked(campaignName, characterId),
    onSuccess: (updated) => _patchTrackedCache(qc, campaignName, updated),
  });
}

// Biography is 404 when none has been generated yet — that's the
// expected "no vita written" state, not a hard error. Suppress retries
// so the UI flips to its empty-state branch immediately.
export function useBiography(
  campaignName: string | null,
  ck3Id: number | null,
): UseQueryResult<BiographyResponse> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.biography(n, ck3Id ?? -1),
    (n) => getCharacterBiography(n, ck3Id as number),
    { enabled: ck3Id !== null, retry: false },
  );
}

export function useFamilyTree(
  campaignName: string | null,
  ck3Id: number | null,
  ancestorDepth?: number,
  descendantDepth?: number,
): UseQueryResult<FamilyTreeResponse> {
  return useCampaignQuery(
    campaignName,
    (n) =>
      queryKeys.familyTree(
        n,
        ck3Id ?? -1,
        ancestorDepth ?? null,
        descendantDepth ?? null,
      ),
    (n) => getFamilyTree(n, ck3Id as number, { ancestorDepth, descendantDepth }),
    { enabled: ck3Id !== null },
  );
}

// Provider status polls every 5s — rolling latency stats are genuinely
// live state and the settings UI is the only consumer, so a background
// poll is cheaper than a second SSE channel for one screen.
export function useProviderStatus(): UseQueryResult<ProviderStatusResponse> {
  return useQuery({
    queryKey: queryKeys.providerStatus(),
    queryFn: getProviderStatus,
    refetchInterval: 5000,
  });
}

// ck3_chronicler-5d9o: per-kind resolved model snapshot for the Settings
// "Models" panel. No polling — env-driven config changes are rare, so we
// just fetch once per Settings mount.
export function useResolvedModels(): UseQueryResult<ResolvedModelsResponse> {
  return useQuery({
    queryKey: queryKeys.resolvedModels(),
    queryFn: getResolvedModels,
  });
}

// Issue #45/#23: writable per-kind models. The PUT response is the
// re-resolved snapshot, so writing it straight into the cache is what
// shows the user a global override still shadowing the row they saved.
export function useUpdateResolvedModels(): UseMutationResult<
  ResolvedModelsResponse,
  Error,
  ModelsUpdateRequest
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ModelsUpdateRequest) => updateResolvedModels(body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.resolvedModels(), data);
    },
  });
}

// Issue #45/#23: the narrative backend picker. No polling — this changes
// only when the user saves here, and the PUT response is the post-write
// state (including whether the new combination would actually construct).
export function useNarrativeBackend(): UseQueryResult<NarrativeBackendResponse> {
  return useQuery({
    queryKey: queryKeys.narrativeBackend(),
    queryFn: getNarrativeBackend,
    staleTime: 5 * 60_000,
  });
}

export function useUpdateNarrativeBackend(): UseMutationResult<
  NarrativeBackendResponse,
  Error,
  NarrativeBackendUpdateRequest
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NarrativeBackendUpdateRequest) =>
      updateNarrativeBackend(body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.narrativeBackend(), data);
      // The switch is process-wide but takes effect on restart, so
      // /provider-status still describes the *running* provider. Refetch
      // it anyway: the model the running provider reports is resolved
      // from the same settings this write may have touched.
      qc.invalidateQueries({ queryKey: queryKeys.providerStatus() });
      qc.invalidateQueries({ queryKey: queryKeys.resolvedModels() });
    },
  });
}

// ck3_chronicler-gx7b: global LLM pause state. Fetched on Settings mount;
// no polling — the FE owns the writes (no other surface flips this), and
// the PUT mutation patches the cache directly with the response payload.
export function useLLMPause(): UseQueryResult<LLMPauseResponse> {
  return useQuery({
    queryKey: queryKeys.llmPause(),
    queryFn: getLLMPause,
  });
}

// Toggle mutation. On the paused→unpaused edge the backend's PUT handler
// runs the drain across every open campaign before returning, so on
// success we invalidate the narrative-queue + character-stats caches to
// surface the surge immediately on the Queue page (otherwise the user
// has to wait for the next SSE-driven refetch).
export function useToggleLLMPause(): UseMutationResult<
  LLMPauseResponse,
  Error,
  LLMPauseUpdate
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LLMPauseUpdate) => putLLMPause(body),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.llmPause(), updated);
      // Drain may have surged the queue — refetch its derived views.
      qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
      qc.invalidateQueries({ queryKey: queryKeys.narrativeCharacterStats() });
    },
  });
}

export function useCostSummary(
  campaignName: string | null,
): UseQueryResult<CostSummaryResponse> {
  return useCampaignQuery(campaignName, queryKeys.costSummary, getCostSummary);
}

// ck3_chronicler-a3f: heraldry palette. The named-color → RGB map is
// process-static, so cache forever (staleTime: Infinity) — refetching
// would only re-download the same JSON. Library cards consume it once
// at the page level and pass it down.
export function usePalette(): UseQueryResult<Palette> {
  return useQuery({
    queryKey: queryKeys.heraldryPalette(),
    queryFn: getHeraldryPalette,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });
}

// ck3_chronicler-7ao subsystem 2: per-character resolved CoA. The
// endpoint returns 404 when the chain (character → dynasty_house →
// coat_of_arms_id) has a gap or save-tail hasn't persisted the row yet;
// the client maps that to data=null so HeraldryWithFallback flips to
// the procedural shield without surfacing an error.
export function useCharacterCoa(
  campaignName: string | null,
  ck3Id: number | null,
): UseQueryResult<CoaDefinition | null> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.characterCoa(n, ck3Id ?? -1),
    (n) => getCharacterCoa(n, ck3Id as number),
    {
      enabled: ck3Id !== null,
      staleTime: 60 * 1000, // CoA changes only when save-tail re-resolves
      retry: false,
    },
  );
}

// ck3_chronicler-eev: narrative-generation queue. Initial fetch + refetch
// on every `narrative_*` SSE frame keeps the AppShell strip in sync with
// in-flight transitions. The hook itself only owns the query state; the
// SSE-driven invalidation is wired in the component (the hook stays a
// pure read so its consumers keep the simple TanStack Query lifecycle).
//
// audit F-46: explicit 30s staleTime so cross-page navigation between
// consumers (CodexPage, QueuePage, TrackedPage) doesn't trigger a
// refetch on every mount when the SSE-driven invalidator is the
// authoritative refresh.
//
// ck3_chronicler-sezy (2026-05-08): also poll every 30s as a fallback.
// SSE-driven invalidation is fast when frames arrive, but a single
// missed narrative_completed frame (j32g reconnect window during a
// 3-5 min Opus generation, or any transient SSE drop) used to leave
// the strip showing 'generating' with elapsed-time ticking up forever.
// The poll guarantees self-healing within 30s without depending on
// SSE infrastructure being correct.
export function useNarrativeQueue(): UseQueryResult<NarrativeQueueResponse> {
  return useQuery({
    queryKey: queryKeys.narrativeQueue(),
    queryFn: getNarrativeQueue,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
}

// ck3_chronicler-27ov.81 (audit L29): the `narrativeSeq → invalidate`
// effect was copy-pasted across NarrativeQueueStrip, QueuePage,
// CodexPage, and ChroniclePage, each invalidating a different key set.
// This hook owns the wiring — subscribe to the campaign's SSE seq, fire
// on every tick after the first — so each consumer only declares WHICH
// queries to invalidate. The callback is read through a ref so a
// consumer can close over live props (selectedId, ck3Id) without
// re-subscribing or going stale; the effect itself depends only on the
// seq, so it fires exactly once per arriving frame.
export function useNarrativeInvalidation(
  campaignName: string | null,
  invalidate: (qc: QueryClient) => void,
): void {
  const qc = useQueryClient();
  const { narrativeSeq } = useEventStream(campaignName);
  // Hold the latest callback in a ref so the invalidation effect can
  // depend only on the seq (fire once per arriving frame) without going
  // stale or re-subscribing. The ref is updated in an effect, never
  // during render (react-hooks/refs); this effect is declared first so
  // it commits before the seq-driven one below on every render.
  const invalidateRef = useRef(invalidate);
  useEffect(() => {
    invalidateRef.current = invalidate;
  });
  useEffect(() => {
    if (narrativeSeq === 0) return;
    invalidateRef.current(qc);
  }, [narrativeSeq, qc]);
}

// ck3_chronicler-c5wq: queue mutations. All three invalidate the queue
// snapshot + per-character stats on success so the page refreshes
// immediately instead of waiting on the next SSE tick.

export function useCancelNarrativeQueueItem(): UseMutationResult<
  NarrativeQueueCancelResponse,
  Error,
  number
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) => cancelNarrativeQueueItem(itemId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
      void qc.invalidateQueries({
        queryKey: queryKeys.narrativeCharacterStats(),
      });
    },
  });
}

export function useRegenerateNarrativeQueueItem(): UseMutationResult<
  NarrativeQueueRegenerateResponse,
  Error,
  number
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) => regenerateNarrativeQueueItem(itemId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
    },
  });
}

export function useReorderNarrativeQueue(): UseMutationResult<
  NarrativeQueueReorderResponse,
  Error,
  number[]
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemIds: number[]) =>
      reorderNarrativeQueue({ item_ids: itemIds }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
    },
  });
}

// ck3_chronicler-fjln: per-character lifetime aggregates surfaced in
// the queue page's stats panel. Same SSE-driven invalidation as the
// queue snapshot — every narrative_* tick can change these aggregates
// (a completion adds to median; a failure bumps failed_count). Wired
// in QueuePage to invalidate on narrativeSeq.
export function useNarrativeCharacterStats(): UseQueryResult<NarrativeCharacterStatsResponse> {
  return useQuery({
    queryKey: queryKeys.narrativeCharacterStats(),
    queryFn: getNarrativeCharacterStats,
  });
}

// ck3_chronicler-z6jm slice 1: CK3 debug.log size + rotation threshold.
// SettingsPage's Advanced card polls this once a minute (the value
// changes slowly during a long playthrough). When ``exceeded`` flips
// true, the UI surfaces the rotate-now alert.
export function useDebugLogStatus(): UseQueryResult<DebugLogStatusResponse> {
  return useQuery({
    queryKey: queryKeys.debugLogStatus(),
    queryFn: getDebugLogStatus,
    refetchInterval: 60_000,
  });
}

// ck3_chronicler-7b8d: CoA history timeline for one character. Empty
// entries list = no transitions ever observed (the common case); the
// chronicle folio renders nothing in that case so the medallion above
// stays the sole visual.
export function useCharacterCoaHistory(
  campaignName: string | null,
  ck3Id: number | null,
): UseQueryResult<CoaHistoryResponse> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.characterCoaHistory(n, ck3Id ?? -1),
    (n) => getCharacterCoaHistory(n, ck3Id as number),
    { enabled: ck3Id !== null, retry: false },
  );
}

// ck3_chronicler-a3jc (f9w.2): heraldry pipeline status for the
// Settings card. The card invalidates this query at the end of each
// extract via SSE so the counts + last_extraction_at refresh.
//
// audit F-46: between extracts the status is fixed; long staleTime so
// re-mounting Settings doesn't refetch unnecessarily.
export function useHeraldryStatus(): UseQueryResult<HeraldryStatus> {
  return useQuery({
    queryKey: queryKeys.heraldryStatus(),
    queryFn: getHeraldryStatus,
    staleTime: 5 * 60_000,
  });
}

export function useStartHeraldryExtract(): UseMutationResult<
  HeraldryExtractStarted,
  Error,
  boolean
> {
  return useMutation({
    mutationFn: (force: boolean) => startHeraldryExtract(force),
  });
}

// ck3_chronicler-kze6 (f9w.3): first-run wizard detection.
export function useFirstRunStatus(): UseQueryResult<FirstRunStatus> {
  return useQuery({
    queryKey: queryKeys.firstRunStatus(),
    queryFn: getFirstRunStatus,
    // Don't retry: 404 on an old server should fall through silently.
    retry: false,
  });
}

export function useDismissFirstRunWizard(): UseMutationResult<
  { wizard_dismissed_at: string },
  Error,
  void
> {
  return useMutation({
    mutationFn: () => dismissFirstRunWizard(),
  });
}

// ck3_chronicler-ogi: add / remove / auto-track tracked characters.
// Each invalidates the tracked-list query so the page-level rendering
// re-fetches from the source of truth. (Patching the cache in-place
// would also work, but the auto-track flow can add multiple rows
// atomically — simpler to just invalidate.)
export function useAddTracked(
  campaignName: string,
): UseMutationResult<
  TrackedResponse,
  Error,
  { character_id: number; note?: string | null; role?: string | null }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) => addTracked(campaignName, body),
    onSuccess: () => _invalidateTrackedMembership(qc, campaignName),
  });
}

export function useDeleteTracked(
  campaignName: string,
): UseMutationResult<void, Error, number> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (characterId: number) => deleteTracked(campaignName, characterId),
    onSuccess: () => _invalidateTrackedMembership(qc, campaignName),
  });
}

export function useAutoTrack(
  campaignName: string,
): UseMutationResult<
  AutoTrackResponseBody,
  Error,
  { forceResetPlaythrough?: boolean } | void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args) =>
      autoTrackFromSave(campaignName, args ?? {}),
    onSuccess: () => _invalidateTrackedMembership(qc, campaignName),
  });
}

// ck3_chronicler-7gw: regenerate a biography for a character. The 202
// response means the scheduler enqueued the task — the new bio row
// won't exist until the SSE narrative_completed frame arrives. The
// page-level narrativeSeq → invalidate hook (CodexPage, ChroniclePage)
// is what actually refetches the body; this mutation just kicks off
// the work and surfaces success/failure to the button.
export function useRegenerateBiography(
  campaignName: string,
): UseMutationResult<RegenerateBiographyResponse, Error, number> {
  return useMutation({
    mutationFn: (ck3Id: number) => regenerateBiography(campaignName, ck3Id),
  });
}

export function useEndSession(
  campaignName: string,
): UseMutationResult<EndSessionResponse, Error, void> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => endSession(campaignName),
    onSuccess: () => {
      // Refresh the session summary so the user sees the updated meter.
      void qc.invalidateQueries({ queryKey: queryKeys.narrativeQueue() });
      void qc.invalidateQueries({
        queryKey: queryKeys.sessionSummary(campaignName),
      });
    },
  });
}

export function useSessionSummary(
  campaignName: string | null,
  options: { pollMs?: number } = {},
): UseQueryResult<CostSessionSummary> {
  return useCampaignQuery(
    campaignName,
    queryKeys.sessionSummary,
    getSessionSummary,
    // Default 15s poll keeps the meter close to live without thundering
    // the BE. Callers (e.g. the future token-meter component) can
    // override; passing 0/false disables the poll for one-shot reads.
    { refetchInterval: options.pollMs ?? 15_000 },
  );
}

// ck3_chronicler-7bi5 (revived by cs1o): cost estimate for the
// regenerate modal. Gated by `enabled` (modal-open) so character pages
// don't pay an endpoint call nobody acts on.
export function useBiographyCostEstimate(
  campaignName: string | null,
  ck3Id: number | null,
  options: { enabled?: boolean } = {},
): UseQueryResult<BiographyCostEstimate> {
  return useCampaignQuery(
    campaignName,
    (n) => queryKeys.biographyCostEstimate(n, ck3Id ?? -1),
    (n) => getBiographyCostEstimate(n, ck3Id as number),
    {
      enabled: (options.enabled ?? true) && ck3Id !== null,
      retry: false,
      // Cache briefly — the estimate doesn't change between mounts of the
      // same modal, but it shouldn't go stale across a save-tail tick.
      staleTime: 30 * 1000,
    },
  );
}

export function useResetSession(
  campaignName: string,
): UseMutationResult<CostSessionResetResponse, Error, void> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resetSessionCounter(campaignName),
    onSuccess: () => {
      // since timestamp shifted; the meter's session bucket needs to
      // re-aggregate from the new boundary.
      void qc.invalidateQueries({
        queryKey: queryKeys.sessionSummary(campaignName),
      });
    },
  });
}

// ck3_chronicler-bly: rename a campaign. Invalidates every campaign-list
// query on success so the Library card picks up the new name without a
// page reload.
export function useRenameCampaign(): UseMutationResult<
  CampaignRenameResponse,
  Error,
  { currentName: string; newName: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ currentName, newName }) =>
      renameCampaign(currentName, newName),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.campaignsAll() });
    },
  });
}

// ck3_chronicler-yv8q: clear a wedged save-tail baseline. The DELETE
// against the per-campaign baseline file is a recovery affordance for
// "playthrough mismatch" errors — no event data is lost (events live
// in the per-campaign DB, not the baseline JSON), and the next matching
// save naturally re-baselines. Idempotent; backend returns 200 either way.
//
// We don't auto-invalidate ['campaigns'] here — the campaign row itself
// doesn't change. The Library card consumer handles toast + local state.
export function useResetBaseline(): UseMutationResult<
  BaselineResetResponse,
  Error,
  string
> {
  return useMutation({
    mutationFn: (campaignName: string) => resetBaseline(campaignName),
  });
}

// ck3_chronicler-72a: schema migration tool.
//
// audit F-46: migration status only changes when the user runs an
// upgrade or restores a backup; the mutation hooks invalidate
// explicitly. A long staleTime avoids the "panel-mounts-on-every-nav"
// refetch storm.
export function useMigrationStatus(): UseQueryResult<MigrationStatusResponse> {
  return useQuery({
    queryKey: queryKeys.migrationStatus(),
    queryFn: getMigrationStatus,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useRunMigration(): UseMutationResult<MigrationRunResponse, Error, void> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => runMigration(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.migrationStatus() });
      void qc.invalidateQueries({ queryKey: queryKeys.migrationBackups() });
    },
  });
}

export function useRestoreBackup(): UseMutationResult<{ restored: number }, Error, string> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (backupDir: string) => restoreFromBackup(backupDir),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.migrationStatus() });
    },
  });
}

export function useListMigrationBackups(): UseQueryResult<MigrationBackupEntry[]> {
  return useQuery({
    queryKey: queryKeys.migrationBackups(),
    queryFn: listMigrationBackups,
  });
}

export function useHaltSaveTail(): UseMutationResult<{ halted: boolean }, Error, void> {
  return useMutation({ mutationFn: () => haltSaveTail() });
}

// ck3_chronicler-thpz.1: Dynasty Wall hook.
export function useDynasty(
  campaignName: string | null,
): UseQueryResult<DynastyResponse> {
  return useCampaignQuery(campaignName, queryKeys.dynasty, getDynasty, {
    retry: false,
  });
}

// ck3_chronicler (2026-05-09): Biographies tab on campaign-overview.
export function useCampaignBiographies(
  campaignName: string | null,
): UseQueryResult<BiographyListEntry[]> {
  return useCampaignQuery(
    campaignName,
    queryKeys.campaignBiographies,
    listCampaignBiographies,
  );
}

// ck3_chronicler-f9w.1: Settings paths panel.
//
// audit F-46: paths only change via the user pressing Save in the
// Settings panel — the mutation hook's onSuccess writes the new value
// directly into the cache, so we can hold a long staleTime and skip
// the per-mount refetch.
export function usePathsSettings(): UseQueryResult<PathsSettings> {
  return useQuery({
    queryKey: queryKeys.pathsSettings(),
    queryFn: getPathsSettings,
    staleTime: 5 * 60_000,
  });
}

export function useUpdatePathsSettings(): UseMutationResult<
  PathsSettings,
  Error,
  PathsSettingsUpdate
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PathsSettingsUpdate) => updatePathsSettings(body),
    onSuccess: (data) => {
      // The PUT response IS the post-update state — write it directly so
      // the UI reflects the new resolution without an extra round-trip.
      qc.setQueryData(queryKeys.pathsSettings(), data);
    },
  });
}

// ck3_chronicler-tbrm.4: prose repo status + override.
//
// The status fetch is cheap (just stat() calls) but doesn't change
// often; long staleTime so re-mounting Settings doesn't refetch. The
// mutation writes the PUT response directly into the cache so the UI
// reflects the new readiness pips without an extra round-trip.
export function useProseRepoStatus(): UseQueryResult<ProseRepoStatusResponse> {
  return useQuery({
    queryKey: queryKeys.proseRepoStatus(),
    queryFn: getProseRepoStatus,
    staleTime: 5 * 60_000,
  });
}

export function useUpdateProseRepoSettings(): UseMutationResult<
  ProseRepoStatusResponse,
  Error,
  ProseRepoUpdate
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProseRepoUpdate) => updateProseRepoSettings(body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.proseRepoStatus(), data);
    },
  });
}

// Issue #23: the Initialize button. The response carries the post-scaffold
// status, so the readiness pips repaint from the same round trip; the
// first-run status is invalidated because a scaffolded chronicle is one of
// the steps the wizard labels done.
export function useInitProseRepo(): UseMutationResult<
  ProseRepoInitResponse,
  Error,
  ProseRepoInitRequest
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProseRepoInitRequest) => initProseRepo(body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.proseRepoStatus(), data.status);
      qc.invalidateQueries({ queryKey: queryKeys.firstRunStatus() });
    },
  });
}

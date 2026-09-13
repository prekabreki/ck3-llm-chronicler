// Mirror of src/chronicler/api/models.py — keep field names + nullability
// in lockstep. This file is the contract: any backend Pydantic model the
// frontend consumes lands here, NOT inlined in components.
//
// Field-name drift against the backend is now caught by a red test:
// tests/unit/test_types_ts_drift.py diffs every interface here against
// the same-named FastAPI OpenAPI schema (ck3_chronicler-27ov.29).

import type { CoaDefinition } from '../components/CoaTypes';

export interface CampaignCounts {
  characters: number;
  biographies: number;
}

export interface CampaignResponse {
  id: string;
  name: string;
  ck3_version: string | null;
  created_at: string;
  last_event_at: string | null;
  archived: boolean;
  db_path: string;
  counts: CampaignCounts | null;
  closing_chronicle_blurb: string | null;
  // ck3_chronicler-cqo:
  bookmark_date: string | null;
  current_in_game_date: string | null;
  current_player_character_id: number | null;
  current_player_name: string | null;
  current_player_nickname: string | null;
  current_house_name: string | null;
  founding_dynasty_name: string | null;
  // ck3_chronicler-a3f: parsed CoA dict for the player character. Only
  // populated when the request was made with include_counts=true (the
  // backend piggybacks on the per-campaign DB session that opens for
  // counts). Null when the campaign lacks a player id, the row is
  // missing, or save-tail hasn't refreshed coa_json yet.
  current_player_coa_json: CoaDefinition | null;
  // ck3_chronicler-wdhe: campaign-overview welcome-page stats sourced
  // from the player char's alive_data + the player dynasty's prestige
  // (= renown). prestige is the *lifetime* accrued counter — surfaced
  // on the welcome page as "Prestige" / "gathered". current_dynasty_renown
  // is null in adventurer mode (no dynasty). Older campaigns without a
  // save-tail tick since the columns were added are also null until the
  // next ingest.
  current_player_gold: number | null;
  current_player_prestige_lifetime: number | null;
  current_player_piety_lifetime: number | null;
  current_dynasty_renown: number | null;
  // ck3_chronicler-bges: persisted last save-pair tick info — null
  // only when the campaign has never been ingested. Read by the
  // IngestActivityStrip on cold load and by the Library card per-row
  // line for the activity signal.
  last_save_filename: string | null;
  last_save_ingested_at: string | null;
  last_save_in_game_date: string | null;
  last_tick_event_count: number | null;
  last_tick_event_type_tally: Record<string, number> | null;
  // ck3_chronicler-9xa6: in-game date of the most recent ingested event
  // ("1126.5.18"). Closing page reads this for "Closed" + Span instead of
  // the wall-clock last_event_at (which produced "Span 960 years" in the
  // 2026-05-10 smoke). Pre-9xa6 rows stay null until the startup backfill
  // populates from MAX(event_date_iso).
  last_event_in_game_date: string | null;
}

// ck3_chronicler-bly: rename a campaign.
export interface CampaignRenameRequest {
  name: string;
}

export interface CampaignRenameResponse {
  campaign: CampaignResponse;
  // Non-null when the new name collides with another non-archived
  // campaign — the rename still succeeds but the UI surfaces this.
  warning: string | null;
}

// ck3_chronicler-yv8q: response from DELETE /api/campaigns/{name}/baseline.
// deleted=true when a baseline file existed and was removed; false on the
// no-op branch (already-missing baseline). path is always populated so
// the toast can name what the chronicler would have touched.
export interface BaselineResetResponse {
  deleted: boolean;
  path: string;
  message: string;
}

export interface CharacterSummary {
  ck3_id: number;
  first_name: string | null;
  dynasty_name: string | null;
  birth_date: string | null;
  death_date: string | null;
  // ck3_chronicler-4y0v (slice 1, 2026-05-08): persisted CoA so the
  // codex / tracked / search browse surfaces render the same heraldry
  // as the chronicle folio. null when no coa is on the row yet — FE
  // falls back to procedural via HeraldryWithFallback.
  coa_json: CoaDefinition | null;
  // ck3_chronicler-w1t3 (2026-05-08): true when this character has
  // been (or currently is) the campaign player. Derived per-request
  // by walking title_acquired events backward from the registry's
  // current_player_character_id. Codex marks these rows so the
  // Thrugot → Svend → Christoffer arc reads as the protagonists
  // against the supporting cast.
  is_played: boolean;
}

export interface EventResponse {
  id: number;
  type: string;
  date: string;
  date_iso: string | null;
  wall_clock_at: string;
  schema_version: number;
  payload: Record<string, unknown>;
}

// ck3_chronicler-zx2l: highest-tier directly-held title at the moment
// of the last save-tail refresh. Preserved through the death tick so
// this is the "title at death" on a deceased character and the
// "current title" on a still-tracked living one. ``tier`` is the slug
// the FE uses to fetch the matching crown from
// ``/api/heraldry/assets/title_icons/<tier>.png``.
export interface PrimaryTitleSummary {
  key: string;
  name: string | null;
  tier: 'empire' | 'kingdom' | 'duchy' | 'county' | 'barony' | 'other';
}

export interface CharacterDetail {
  ck3_id: number;
  first_name: string | null;
  dynasty_name: string | null;
  house_name: string | null;
  nickname: string | null;
  female: boolean | null;
  birth_date: string | null;
  death_date: string | null;
  culture: string | null;
  faith: string | null;
  events: EventResponse[];
  // ck3_chronicler-4y0v slice 1 follow-up: persisted CoA so the Codex
  // right-pane shield matches the chronicle folio.
  coa_json: CoaDefinition | null;
  // ck3_chronicler-zx2l: rendered below the dynasty line in the folio
  // aside namebar with a tier crown.
  primary_title: PrimaryTitleSummary | null;
  // ck3_chronicler-9ngy: every title held at death (grandest-first);
  // the sidebar lists all of them. held_titles[0] === primary_title.
  held_titles?: PrimaryTitleSummary[];
}

export interface TrackedResponse {
  character_id: number;
  first_name: string | null;
  nickname: string | null;
  role: string | null;
  added_at: string;
  biography_count: number;
  monthly_token_spend: number;
  // vysp.10: pause / bump state. paused_at non-null = scheduler skips;
  // bumped_at non-null = manual priority lift.
  paused_at: string | null;
  bumped_at: string | null;
  // ck3_chronicler-4y0v (slice 1, 2026-05-08): persisted CoA so the
  // TrackedPage shield matches the chronicle folio.
  coa_json: CoaDefinition | null;
}

export interface BiographyResponse {
  id: number;
  character_id: number;
  version: number;
  body: string;
  prompt_template_version: string;
  provider: string;
  generated_at: string;
  events_through_event_id: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface FamilyTreeNode {
  ck3_id: number;
  first_name: string | null;
  nickname: string | null;
  birth_date: string | null;
  death_date: string | null;
  relation: string;
  depth: number;
  // ck3_chronicler-h9u6 (4y0v slice 2.1, 2026-05-09): persisted CoA
  // from the per-character row, attached on the BE side without an
  // extra query (Character.coa_json is already pulled by the
  // family-tree's batch loader). Null when the character has no
  // resolved CoA — FE falls through to HeraldryWithFallback's
  // procedural seeded shield.
  coa_json: CoaDefinition | null;
}

export interface FamilyTreeResponse {
  self_node: FamilyTreeNode;
  ancestors: FamilyTreeNode[];
  descendants: FamilyTreeNode[];
  spouses: FamilyTreeNode[];
  siblings: FamilyTreeNode[];
}

export interface ProviderStatusResponse {
  mode: string;
  model: string | null;
  recent_biographies: number;
  avg_biography_ms: number | null;
}

// ck3_chronicler-tbrm.4: status of the chronicle directory every backend
// generates against (its CLAUDE.md is the role-reshape, its briefings are
// the per-campaign input). All three pips (exists / git_initialized /
// claude_md_present) must be green for biography generation to behave.
export interface ProseRepoStatusResponse {
  path: string;
  source: 'override' | 'env' | 'default';
  override: string | null;
  exists: boolean;
  git_initialized: boolean;
  claude_md_present: boolean;
}

export interface ProseRepoUpdate {
  path?: string | null;
}

// Issue #23: POST /api/settings/prose-repo/init — the GUI counterpart of
// `chronicler init-prose`. `path` omitted scaffolds wherever the resolver
// currently points. `notes` are the CLI's own human-facing lines, which is
// how a partial success (files in place, git unavailable) reaches the user.
export interface ProseRepoInitRequest {
  path?: string | null;
}

export interface ProseRepoInitResponse {
  status: ProseRepoStatusResponse;
  created: boolean;
  already_initialised: boolean;
  git_initialised: boolean;
  notes: string[];
}

// Issue #45/#23: the narrative backend and what it points at.
//
// The stored API keys never round-trip — `present` plus which tier the
// key came from is all the UI ever learns. That is also why every field
// of NarrativeBackendUpdate is optional: an omitted field is left
// untouched server-side, so saving a base URL cannot blank a stored key.
// Send an empty string to deliberately clear one.
export interface BackendKeyState {
  present: boolean;
  source: string | null;
}

export interface BackendPresetInfo {
  id: string;
  base_url: string;
  requires_key: boolean;
}

export interface NarrativeBackendResponse {
  backend: string;
  source: string;
  valid_backends: string[];
  valid_presets: string[];
  presets: BackendPresetInfo[];
  openai_preset: string | null;
  openai_base_url: string | null;
  openai_model: string | null;
  openai_key: BackendKeyState;
  anthropic_key: BackendKeyState;
  usable: boolean;
  error: string | null;
}

export interface NarrativeBackendUpdateRequest {
  backend?: string;
  openai_preset?: string;
  openai_base_url?: string;
  openai_model?: string;
  openai_api_key?: string;
  anthropic_api_key?: string;
}

// ck3_chronicler-5d9o: per-kind model snapshot for the Settings "Models"
// panel — what each kind routes to right now, after settings-then-env-then-
// default resolution. `global_override` carries the value that pins every
// kind to one tag when set (issue #23 made all three editable in the UI;
// the env vars remain as a fallback tier).
export interface ResolvedModelsResponse {
  biography: string;
  closing: string;
  global_override: string | null;
}

// Issue #45/#23: writable counterpart. Same omit-vs-empty-string contract
// as NarrativeBackendUpdateRequest — omit to leave a row alone, send '' to clear
// it back to env-then-default.
export interface ModelsUpdateRequest {
  global_override?: string;
  biography?: string;
  closing?: string;
}

// ck3_chronicler-gx7b: global LLM pause state. While paused, save-tail keeps
// ingesting + persisting events but no biographies auto-fire.
// paused_at is the ISO timestamp the user flipped pause ON (null when not
// paused). last_drain carries the counts from the most recent unpause-drain.
export interface LLMPauseDrainReport {
  biographies_scheduled: number;
}

export interface LLMPauseResponse {
  paused: boolean;
  paused_at: string | null;
  last_drain: LLMPauseDrainReport | null;
}

export interface LLMPauseUpdate {
  paused: boolean;
}

// ck3_chronicler-eev: narrative-generation queue for the AppShell strip.
// audit F-58 / ck3_chronicler-hgnb: kind + status tightened to literal
// unions matching the BE Pydantic model.
export interface NarrativeQueueItem {
  item_id: number;
  character_id: number;
  // ck3_chronicler-27ov.81 (audit L30): first name resolved BE-side at
  // enqueue. Null when unresolved; consumers fall back to the id. The FE
  // no longer joins character_id against the campaign's character window.
  character_name: string | null;
  kind: 'biography';
  status: 'queued' | 'generating' | 'completed' | 'failed';
  enqueued_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface NarrativeQueueResponse {
  queued: NarrativeQueueItem[];
  active: NarrativeQueueItem[];
  recent: NarrativeQueueItem[];
  completed_count: number;
  failed_count: number;
  avg_duration_ms: number | null;
}

// ck3_chronicler-c5wq: queue-page mutation envelopes.

export interface NarrativeQueueCancelResponse {
  cancelled: boolean;
  item_id: number;
}

export interface NarrativeQueueRegenerateResponse {
  character_id: number;
  kind: 'biography';
  item_id: number | null;
}

export interface NarrativeQueueReorderRequest {
  item_ids: number[];
}

export interface NarrativeQueueReorderResponse {
  new_item_ids: number[];
}

// ck3_chronicler-fjln: per-character lifetime aggregates for the queue
// page's stats panel. Mirrors NarrativeCharacterStatsResponse in
// chronicler.api.models.
export interface NarrativeCharacterStatsRow {
  character_id: number;
  // ck3_chronicler-27ov.81 (audit L30): name stamped BE-side; no FE join.
  character_name: string | null;
  kind: 'biography';
  completed_count: number;
  failed_count: number;
  median_duration_ms: number | null;
  last_success_at: string | null;
}

export interface NarrativeCharacterStatsResponse {
  rows: NarrativeCharacterStatsRow[];
}

// ck3_chronicler-z6jm slice 1: debug.log size + rotation status.
export interface DebugLogStatusResponse {
  exists: boolean;
  size_bytes: number;
  threshold_bytes: number;
  exceeded: boolean;
  path: string;
}

export interface CostBucket {
  input_tokens: number;
  output_tokens: number;
  // ck3_chronicler-cs1o: per-window USD, revived after the 2026-06-15
  // billing split. Mirrors backend CostBucket.usd (cost.py).
  usd: number;
}

export interface CostSummaryResponse {
  this_campaign: CostBucket;
  this_month: CostBucket;
}

export interface SearchHitResponse {
  campaign_name: string;
  kind: string; // "biography" | "memory" | "character" | "event"
  row_id: number;
  snippet: string;
  character_id: number | null;
  rank: number | null;
  // ck3_chronicler-3yd9 (2026-05-08): persisted CoA for the hit's
  // character (when one is associated). Mirrors slice 1's CharacterSummary
  // / TrackedResponse shape so the SearchPage card can render real arms
  // via HeraldryWithFallback. Null when no character_id, or when CoA has
  // not been persisted (untracked + unrefreshed character).
  coa_json: CoaDefinition | null;
}

export interface SearchResponse {
  query: string;
  hits: SearchHitResponse[];
  by_campaign: Record<string, SearchHitResponse[]>;
}

export interface ClosingChronicleResponse {
  campaign_name: string;
  body: string;
  generated_at: string;
  archived: boolean;
}

export interface ImportStartedResponse {
  import_id: string;
  campaign_name: string;
  sse_url: string;
}

// ck3_chronicler-mb6q: save-picker listing for ImportModal. Mirrors
// SaveListResponse + SaveFileInfo in chronicler.api.routes.save.
export interface SaveFileInfo {
  filename: string;
  abs_path: string;
  size_bytes: number;
  mtime_iso: string;
}

export interface SaveListResponse {
  save_dir: string;
  save_dir_exists: boolean;
  save_dir_source: 'override' | 'env' | 'default';
  saves: SaveFileInfo[];
}

// ck3_chronicler-72a: schema migration tool.

export interface PendingCampaignMigration {
  campaign_id: string;
  name: string;
  current_head: string | null;
  target_head: string;
}

export interface MigrationStatusResponse {
  needs_migration: PendingCampaignMigration[];
  registry_needs_migration: boolean;
  registry_missing_columns: string[];
}

export interface MigrationRowResult {
  id: string;
  ok: boolean;
  error: string | null;
}

export interface MigrationRunResponse {
  success: boolean;
  backup_dir: string | null;
  results: MigrationRowResult[];
}

export interface MigrationBackupEntry {
  timestamp: string;
  path: string;
  campaign_count: number;
}

// audit F-32 / ck3_chronicler-yf2p: types previously inlined in
// client.ts moved here so the contract lives in one file. client.ts
// imports them; new response shapes should land here too.

export interface RegenerateBiographyResponse {
  character_id: number;
  item_id: number | null;
}

export interface EndSessionResponse {
  campaign_id: string;
  tracked_considered: number;
}

export interface TokenBucket {
  input: number;
  output: number;
}

export interface CostSessionSummary {
  lifetime: TokenBucket;
  session: TokenBucket;
  // ISO8601 timestamp of the last reset, or null if the user has
  // never reset (meter shows session === lifetime in that case).
  since: string | null;
  target_lifetime_input: number;
  // ck3_chronicler-cs1o: USD revival. month_usd vs target_monthly_usd
  // (default $100 — the monthly programmatic credit) drives the meter's
  // 75/100/150% bands; lifetime/session mirror the token buckets.
  lifetime_usd: number;
  session_usd: number;
  month_usd: number;
  target_monthly_usd: number;
}

// ck3_chronicler-7bi5 (revived by cs1o): pre-regeneration estimate for
// the modal's cost panel. pool_billed=true → claude-code transport, the
// cost draws monthly programmatic pool credit, not a payment card.
export interface BiographyCostEstimate {
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_total_tokens: number;
  est_usd: number;
  kind: string;
  would_route_to: string | null;
  model: string | null;
  method: string;
  pool_billed: boolean;
}

export interface CostSessionResetResponse {
  since: string;
}

export interface AutoTrackResponseBody {
  added: TrackedResponse[];
  already_tracked: number[];
  save_path: string;
}

// ck3_chronicler-gw16: per-campaign auto-track rule flags. Mirrors
// AutoTrackRulesBody in chronicler.api.models. PUT body fields are
// optional (partial update); GET response always returns concrete bools.
export interface AutoTrackRules {
  include_heirs: boolean;
  include_spouses: boolean;
  include_grandchildren: boolean;
  // Reserved for gw16.2 (county-tier vassals) — the snapshot doesn't
  // expose a character→liege relation today. Persisting True is
  // harmless until the underlying support lands.
  include_county_vassals: boolean;
}

export type AutoTrackRulesUpdate = Partial<AutoTrackRules>;

// ck3_chronicler-gw16: one untracked living relative the rail's
// "Suggested souls" list offers as a one-tap track. Mirrors
// SuggestedCandidateResponse in chronicler.api.models.
export interface SuggestedCandidate {
  ck3_id: number;
  first_name: string | null;
  nickname: string | null;
  relation: string;
  birth_date: string | null;
  death_date: string | null;
  coa_json: CoaDefinition | null;
}

export interface DebugLogRotateResponse {
  rotated: boolean;
  archive_path: string | null;
  offsets_reset: number;
  message: string;
}

export interface DynastyMember {
  ck3_id: number;
  first_name: string | null;
  dynasty_name: string | null;
  nickname: string | null;
  birth_date: string | null;
  death_date: string | null;
  is_player: boolean;
  has_biography: boolean;
  coa_json: CoaDefinition | null;
}

export interface DynastyVitaEntry {
  ck3_id: number;
  first_name: string | null;
  nickname: string | null;
  death_date: string | null;
  biography_excerpt: string;
  generated_at: string;
}

export interface DynastyResponse {
  dynasty_name: string;
  member_count: number;
  chronicled_count: number;
  founding_date: string | null;
  founding_paragraph: string | null;
  founder: DynastyMember | null;
  current_head: DynastyMember | null;
  members: DynastyMember[];
  vita_roll: DynastyVitaEntry[];
}

// ck3_chronicler (2026-05-09): Biographies tab on the campaign-overview
// page. One row per character with the latest biography body excerpt.
// Backend orders chronologically by death date so the surface reads as
// a chronicle of departures with the still-active player(s) at the end.
export interface BiographyListEntry {
  ck3_id: number;
  first_name: string | null;
  nickname: string | null;
  dynasty_name: string | null;
  female: boolean | null;
  birth_date: string | null;
  death_date: string | null;
  biography_excerpt: string;
  generated_at: string;
  version: number;
  coa_json: CoaDefinition | null;
  primary_title: PrimaryTitleSummary | null;
  /** ck3_chronicler-9ngy: every title held at death (grandest-first);
   * the overview row lists all of them. held_titles[0] === primary_title. */
  held_titles?: PrimaryTitleSummary[];
  /** ck3_chronicler-lmex: "ruler" | "consort" | "kin" | "other". */
  role: string | null;
  /** One-line relationship fact, e.g. "m. Malmfridr", "wife of Bjorn". */
  relation: string | null;
}

export interface PathInfo {
  resolved: string;
  source: 'override' | 'env' | 'default' | 'probe';
  exists: boolean;
  override: string | null;
}

export interface PathsSettings {
  save_dir: PathInfo;
  ck3_install_dir: PathInfo;
  archive_dir: PathInfo;
  // Issue #51: the checkout containing archive_dir, or null. Inside a
  // repo, chronicler commits and pushes sealed snapshots there.
  archive_git_root: string | null;
}

export interface PathsSettingsUpdate {
  save_dir?: string | null;
  ck3_install_dir?: string | null;
  archive_dir?: string | null;
}

export interface AdoptSaveResponse {
  campaign: CampaignResponse;
  chars_upserted: number;
  memories_inserted: number;
  memories_duplicate: number;
}

export interface HeraldryStatus {
  extracted: boolean;
  last_extraction_at: string | null;
  palette_colors: number;
  patterns_count: number;
  emblems_count: number;
  ck3_install_dir_resolved: string | null;
  ck3_install_dir_exists: boolean;
  is_stale: boolean;
}

export interface HeraldryExtractStarted {
  extract_id: string;
  sse_url: string;
}

export interface CoaHistoryEntry {
  observed_at: string;
  coa: CoaDefinition;
}

export interface CoaHistoryResponse {
  entries: CoaHistoryEntry[];
}

export interface FirstRunStatus {
  needs_wizard: boolean;
  library_empty: boolean;
  save_dir_configured: boolean;
  ck3_install_dir_configured: boolean;
  heraldry_extracted: boolean;
  // Issue #23: prose dir on disk WITH its CLAUDE.md. Not a term in
  // needs_wizard (see the BE model) — the chronicle step reads it to
  // label itself done.
  prose_repo_ready: boolean;
  wizard_dismissed_at: string | null;
}

// audit F-44 / ck3_chronicler-w3wk: mirrors of the BE SSE frame
// envelopes in src/chronicler/api/routes/{importer,settings}.py.
// These are the contract for the three SSE channels — keep field
// names + literals in lockstep with the Pydantic models. F-01 was
// caused by exactly this drift; ImportModal.tsx is the canonical
// FE consumer of ImportProgressFrame.

export type ImportStage =
  | 'read_save'
  | 'parse_history'
  | 'backfill_events'
  | 'generate_biographies'
  | 'done'
  | 'error';

export interface ImportProgressFrame {
  import_id: string;
  stage: ImportStage;
  fraction: number;
  message: string;
}

// Discriminated union — switch on ``stage`` and the type narrows.
export type HeraldryProgressFrame =
  | {
      extract_id: string;
      stage: 'started';
      force: boolean;
    }
  | {
      extract_id: string;
      stage: 'progress';
      group: string;
      current: number;
      total: number;
    }
  | {
      extract_id: string;
      stage: 'done';
      palette_colors: number;
      patterns: number;
      emblems: number;
      skipped_designer: number;
    }
  | {
      extract_id: string;
      stage: 'error';
      message: string;
    };



// ck3_chronicler-yrv3: in-app Logs tab envelopes. Mirrors the
// backend's TypedDict LogEnvelope in src/chronicler/api/log_buffer.py.
// `seq` is process-monotonic — the FE dedupes between the cold-load
// (/api/logs/recent) and live-stream (/api/sse/logs) channels by seq.
export type LogLevel =
  | 'DEBUG'
  | 'INFO'
  | 'WARNING'
  | 'ERROR'
  | 'CRITICAL';

export interface LogEnvelope {
  seq: number;
  ts: string;
  level: LogLevel;
  logger: string;
  message: string;
}

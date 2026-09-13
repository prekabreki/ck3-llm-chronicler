# Architecture

How CK3 LLM Chronicler is put together: what it covers, what reads the save files, what turns
a character's recorded life into prose, and where it deliberately stops.

## Scope

Strictly personal use. Not a public mod, not a polished product. That means:

- No mod compatibility engineering (single-user)
- No game rule toggles for performance
- No ironman / achievement concerns
- Patch stability handled reactively, not proactively
- UI can be ugly until it isn't

DLC: all DLCs owned, including Khans of the Steppe (added 2026-05-01 mid-v0.2). Plan for full Tours & Tournaments and Roads to Power coverage; nomadic government / steppe-specific hooks become relevant at v0.4 (camp moves, contracts) and v0.6 (vanilla memories may include khan-specific `memory_*` types).

---


## Architecture **(post-v0.6 pivot)**

Save-parsing is the primary data source. The CK3 mod from v0.2 stays
in-repo for the optional `-debug_mode` mode (richer real-time data
during narratively-dense sessions) but is not required for normal
operation.

Two cooperating processes after v0.6:

1. **Save watcher + parser** — Python process watches CK3's autosave
   directory; on each new `.ck3` file, runs the bundled `rakaly` CLI
   to convert to JSON, parses into a SaveSnapshot, diffs against the
   previous snapshot to emit events, writes to SQLite.
2. **Web app** — FastAPI backend + frontend. Reads from SQLite, calls
   the configured narrative backend (which writes its briefing +
   biography artifacts into the chronicle directory and commits them),
   serves the chronicle UI.

```
CK3 (clean UI, monthly autosave)
    ↓ .ck3 save files
autosave directory
    ↓ watch
Python save-tail ─→ rakaly CLI subprocess ─→ JSON ─→ parse + diff
                                                          ↓
                                                   SQLite (per-campaign DB)
                                                          ↓
                                                   FastAPI backend ─→ narrative backend
                                                          │              ↓
                                                          │        chronicle directory (git)
                                                          ↓
                                                          ↓
                                                   Web UI (browser)
```

**Optional rich-data mode (`-debug_mode` on):** the v0.2 mod also
emits CHRONICLER-tagged events to debug.log, providing sub-day timing
and engine scope-dump context. The save-parse layer reconciles —
debug_log events take precedence on overlap (more granular).

### Tech choices

- **Backend:** Python 3.11+, FastAPI, SQLite. The same stack as other tools of mine, chosen for familiarity.
- **LLM:** Ollama running locally, Qwen 2.5 14B (Q4) as default narrative model. ~9GB VRAM, leaves headroom on the 16GB 4080 alongside CK3.
  > **Superseded (v0.10, `tbrm`):** local generation lost on prose quality and never came back as the default. The shipped default is `claude-code`; a local model is reachable again through the `openai-compatible` backend (Ollama and LM Studio presets), with the honest caveat that the craft rules were tuned against Claude and a small model holds them less reliably.
- **LLM abstraction:** All model calls go through a `NarrativeProvider` interface from day one. Implementations: `OllamaProvider`, later `AnthropicProvider`, later `HybridProvider` (Haiku for frequent calls, Sonnet for biographies). Switching providers should never require touching application logic.
  > **Superseded (2026-05-31, `ck3_chronicler-ihkv`):** the swappable-provider premise was dropped. The `tbrm` pivot (v0.10–v0.11) collapsed everything to a single `ClaudeCodeProvider` that shells out to `claude --print` against the prose repo; **Claude Code is now the permanent narrative backend.** Local models (Qwen) were ruled out on quality and the direct Anthropic API on token cost. The `NarrativeProvider` interface survives **only** as a test-double injection seam — there is no `OllamaProvider`/`AnthropicProvider`/`HybridProvider`, no provider-mode setting, and no capability-based routing. Do not re-introduce provider swapping.
  >
  > **Partly un-superseded (2026-08-12):** backend *selection* is back — three lean transports (`claude-code` default, `anthropic`, `openai-compatible` with presets for OpenAI/DeepSeek/Ollama/LM Studio/OpenRouter), chosen process-wide by settings-then-env. The public release cannot require a Claude subscription of every user, and the 2026-06-15 programmatic-credit split had already forced the `anthropic` fallback back in. What stays dead is the part above that this line does not name: **no `HybridProvider`, no per-kind providers, no capability-based routing.** The day-one premise in the bullet above is still wrong in its ambition — swapping is a three-item registry, not an open plug-in system.
- **Frontend:** TBD at v0.3. Likely React or plain HTML for speed. Decide when we get there.
  > **Superseded (v0.3):** React + Vite + TanStack Query, built by `npm run build` into `src/chronicler/api/static/app`. No Next.js: the app is served by the FastAPI process, so there is nothing for a second runtime to do.
- **Save file parser:** [`rakaly/ck3save`](https://github.com/rakaly/ck3save) (Rust crate) — used in v0.6 onward via subprocess or Python bindings. Not needed earlier.
- **Validator:** [`amtep/tiger`](https://github.com/amtep/tiger) (CK3-Tiger) — lints the mod scripts. Run before every CK3 launch during mod development.

---


## Data extraction strategy

Based on prior research into CK3 modding hooks (summary below; full notes in `research/ck3-data-extraction.md`):

**Primary channel: `debug_log` effect → `debug.log` file.**

CK3 exposes a `debug_log = "string"` scripted effect that writes to `Documents\Paradox Interactive\Crusader Kings III\logs\debug.log`. The string supports data-function interpolation (`[ROOT.GetID]`, `[scope:destination.GetName]`, etc.). Strategy: prefix every line with a unique tag (`[CHRONICLER]`) and tail the file externally.

**Hook strategy: append to vanilla on_actions** (now used only in optional `-debug_mode` mode after the v0.6 pivot).

CK3's on_action system has a critical mod-compatibility rule: `on_actions = { … }`, `events = { … }`, and `random_events = { … }` are *appended* across mods, but `trigger = { … }` and `effect = { … }` are *overwritten*. We always use the appending form, even though we're the only mod, because it's the cleaner pattern.

The v0.2 hook set below is implemented and works when `-debug_mode` is on; it's retained as the optional-rich-data path. The v0.6 save-parse path is the primary transport for everyone else.

**Hooks v0.2 implemented (lifecycle):**

- `on_birth_child`, `on_death`, `on_marriage`, `on_divorce`, `on_imprison`, `on_release_from_prison`
- `on_title_gain`, `on_title_lost`
- `on_war_started`, `on_war_won_attacker`, `on_war_won_defender`

**Hooks v0.4 (deprioritised — optional debug_log mode):**

- T&T travel: `on_travel_plan_start`, `on_travel_plan_movement`, `on_travel_plan_arrival`
- T&T activities: `on_activity_started`, `on_activity_phase_active`, `on_activity_completed`
- RtP: `on_become_adventurer`, `on_camp_moved`, `on_contract_accepted`, `on_contract_completed`
- Periodic snapshot: `yearly_playable_pulse` (superseded by save-parse cycles for v0.5)

**Secondary channel: save file parsing (v0.6+).**

The `.ck3` save file contains a `memories` array per character — the engine's own structured narrative log keyed by `memory_type` (e.g. `memory_grand_wedding`, `memory_won_battle`). Richer than what we can stamp via `debug_log` for some event types. Parsed via `rakaly/ck3save`. Used to backfill data the log channel misses, especially for the family tree view.

**Performance note:** Some on_actions (`on_teleport`, `on_invalid_location`, `on_knight_combat_pulse`) are heavy enough to noticeably slow the game in large realms. The community Travelers mod benchmarked ~35% slowdown when these are hooked aggressively. We avoid these. Standard travel and activity hooks are fine.

**Reference projects:**

- [`pharaox/travelers`](https://github.com/pharaox/travelers) — best example of clean travel on_action hooks. Pattern reference.
- [`TCA166/CK3-history-extractor`](https://github.com/TCA166/CK3-history-extractor) — Rust tool that parses saves and emits HTML chronicles. Closest functional analogue. Reference for save parsing.

---


## Non-goals

- Multi-user / shareable chronicles
- Public release on Nexus or Steam Workshop
- Real-time in-game UI overlay (the web app is the UI)
- Editing / modifying game state from the app
- Compatibility with other gameplay overhaul mods (Sunset Invasion, AGOT, etc.) — fix per-campaign if needed
- Replacing CK3's own memory system in-game (we augment externally; vanilla memories still exist and we read them)

---

# Extracting Rich Event Data from Crusader Kings 3 for an External LLM: A Technical Survey

> **Status (2026-04-30, post-V01-S04):** §1.5 and §2 examples imply that
> data functions like `[ROOT.GetID]` and `[GetCurrentDate]` interpolate
> inside `debug_log = "..."` string literals. They do not. The loc engine
> only resolves data functions when `debug_log` references a *localization
> key*, and even then only globals (no `ROOT`/`THIS`/`save_scope_as` named
> scopes) are bound. Character IDs must be paired from a separate
> `debug_log_scopes = yes` dump. Modern getter chain is `[ROOT.Char.GetID]`,
> not `[ROOT.GetID]`. See `bd memories ck3-debug-log-gotchas` and
> `v0-1-takeaways` for the corrected reference, plus `docs/schema_versions.md`
> for the wire format the project actually uses.

This report is a technical map of the surface area Crusader Kings III (CK3) exposes to a modder who wants to capture rich, real‑time, character/event‑level data and pipe it to an external system such as a local LLM. It is organized around the six areas you listed and prioritizes concrete file paths, effect names, scope semantics, library names, and known gotchas over high‑level overviews. Where conventions exist only as community knowledge, that is flagged.

---

## 1. The `on_actions` system

### 1.1 What an on_action is

An `on_action` is a named hook the engine fires when something happens in the game. Two flavors exist:

1. **Code on_actions** – fired by the engine itself (births, deaths, monthly pulses, travel arrivals, war events, etc.). The full canonical list is dumped by the game from `/common/on_action/on_actions.info` and is reproduced on the CK3 wiki's *Event modding* page. As of recent patches it includes (non‑exhaustive):

   - Lifecycle: `on_birth_child`, `on_birth_mother`, `on_birth_father`, `on_death`, `on_natural_death_second_chance`, `on_join_court`, `on_leave_court`, `on_imprison`, `on_release_from_prison`, `on_marriage`, `on_divorce`, `on_concubinage`, `on_rank_up`, `on_rank_down`, `on_title_gain`, `on_title_lost`, `on_title_destroyed`.
   - Pulses (very useful for periodic export): `yearly_playable_pulse`, `three_year_playable_pulse`, `five_year_playable_pulse`, `random_yearly_playable_pulse`, `five_year_everyone_pulse`, `quarterly_playable_pulse`, `monthly_character_pulse_…`, `on_knight_combat_pulse`.
   - World/political: `on_county_faith_change`, `on_county_culture_change`, `on_county_occupied`, `on_alliance_added`, `on_alliance_broken`, `on_war_started`, `on_war_won_attacker`, `on_war_won_defender`, `on_join_war_as_secondary`, `on_holy_order_hired`, `on_raid_loot_delivered`, `on_perks_refunded`, `on_guest_arrived_from_pool`, `on_game_start`, `on_game_start_with_tutorial`.
   - DLC‑specific (see §6).

2. **Scripted on_actions** – defined in `common/on_action/*.txt` files. They look like ordinary on_actions but you call them yourself with `trigger_event = { on_action = my_on_action }` or `effect = { on_actions = { my_on_action } }`. They can have `trigger`, `effect`, `events`, `random_events`, `first_valid`, `fallback`, etc.

### 1.2 Anatomy of an on_action entry

```pdx
on_birth_child = {
    on_actions = { my_logging_on_action }      # appended
    events     = { my_namespace.1 }            # appended
    random_events = {
        100 = my_namespace.2
        50  = 0                                 # weighted nothing
    }
    trigger = { ... }
    effect  = { ... }                           # Caution: this one OVERWRITES vanilla
}
```

Crucial mod‑compatibility rule confirmed on the Paradox forums (and in vanilla files): **`on_actions = { … }`, `events = { … }`, and `random_events = { … }` are appended across mods, but `trigger = { … }` and `effect = { … }` are overwritten.** For an external logger you should therefore always add to the `on_actions` list and put your work in your own scripted on_action, never modify the inline `effect` of a vanilla on_action.

### 1.3 Scope handed to the on_action

Every code on_action publishes a documented "Expected scope" (the wiki and `on_actions.info` list it; e.g. `on_birth_child` is scoped to the new‑born child as `root`, with the mother accessible via `scope:mother`, etc.). From there you can scope freely with all the standard CK3 scope chains (`root`, `scope:xxx`, `liege`, `top_liege`, `culture`, `faith`, `house`, `dynasty`, `capital_province`, `current_location`, etc.). Run the console command `script_docs` once and you get `event_targets.log`, `event_scopes.log`, `effects.log`, `triggers.log`, and `modifiers.log` in `Documents\Paradox Interactive\Crusader Kings III\logs\` — that dump is the authoritative reference for what scopes/event targets are reachable from any given on_action.

### 1.4 Saved scopes from on_actions

Inside an on_action you may use `save_scope_as = something` and `save_temporary_scope_as = something` to pin down ad‑hoc references (e.g. the killer in `on_death`, the destination in a travel arrival). Some code on_actions pre‑save scopes for you with conventional names (e.g. `scope:killer`, `scope:reason`, `scope:plan_owner`, `scope:travel_plan`, `scope:destination`); the convention is published in `on_actions.info` and in the vanilla `events/*` and `common/on_action/*` files. Always cross‑check by dumping `event_scopes.log` for the patch you target.

### 1.5 Hot‑reload caveat

As of patch 1.12 the wiki notes `on_actions don’t refresh automatically on save with the -develop launch option.` The community workaround is to put the body of your on_action into a **scripted_effect** (in `common/scripted_effects/`) and have the on_action just call that scripted_effect — scripted_effects/triggers *do* hot‑reload. This matters if you iterate on your logger frequently.

### 1.6 Writing custom data from an on_action

Within the `effect = { … }` block of an on_action you have the full effect language. The four practical tools for emitting data to an external pipeline are:

- The `debug_log` / `debug_log_scopes` / `random_log_scopes` effects → write to `debug.log` (see §2).
- Plain text emission via localization‑formatted strings inside `debug_log` (see §2).
- Bound variables (`set_variable`, `set_global_variable`) so a parser can pull state out of the `.ck3` save (see §3).
- A scripted effect that runs `every_player`, `every_living_character_in_realm`, `every_vassal`, etc. to bulk‑emit on a pulse hook.

A typical exporter pattern looks like:

```pdx
my_logger_on_action = {
    effect = {
        every_player = {
            debug_log = "[CK3LLM] tick=[GetGameStartDate] player=[ROOT.GetID] gold=[ROOT.GetGold] prestige=[ROOT.GetPrestige] location=[ROOT.GetLocation.GetID]"
        }
    }
}

yearly_playable_pulse = {
    on_actions = { my_logger_on_action }   # appends, stays mod-friendly
}
```

---

## 2. The `log` / `debug_log` scripting effect

### 2.1 Names and behavior

CK3 does not have a single `log =` effect; it has a small family:

| Effect | Output | Purpose |
|---|---|---|
| `debug_log = "string"` | `…\logs\debug.log` | Free‑form text emission. Accepts `[ ]` data‑function localisation interpolations. |
| `debug_log_scopes = yes/no` | `debug.log` | Dumps the *currently active named scopes* (yes = full info; no = current only). |
| `random_log_scopes = yes/no` | `Scopes._Random.log` | Same as above but written to a separate random log; the wiki effects.log warns this can introduce localized strings into the random log and should be temporary only. |
| `debug_trigger_event` | `debug.log` | Like `trigger_event` but additionally prints trigger fulfilment and the immediate effects of the event. |

These names appear verbatim in `effects.log` (script_docs dump) and on the CK3 wiki *Effects* page.

### 2.2 What you can write

`debug_log` accepts a string that may embed CK3 *data functions* using square brackets. Examples that the community uses:

```
debug_log = "char=[ROOT.GetID] name=[ROOT.GetFirstName] age=[ROOT.GetAge] culture=[ROOT.GetCulture.GetName]"
debug_log = "[ROOT.GetTitledFirstName] travelled to [scope:destination.GetName] on [GetGameStartDate]"
debug_log = "value=[GetPlayer.MakeScope.Var('test').GetValue]"
```

Any chain that returns a `Scope` followed by `.GetName`, `.GetID`, `.GetGold`, `.GetPiety`, `.GetCulture`, `.GetFaith`, `.GetLocation`, `.GetCapital`, etc., is valid. The full list of getters is whatever appears in `data_types.log` after `script_docs` (CK3 inherits the Clausewitz/Jomini data‑function system).

### 2.3 Output file location and format

All script logging targets are in:

```
%USERPROFILE%\Documents\Paradox Interactive\Crusader Kings III\logs\
```

The relevant files are:

- `debug.log` – output of `debug_log`, `debug_log_scopes`, `debug_trigger_event` and many engine traces.
- `error.log` – the validator output. Almost always huge once mods are loaded.
- `game.log` – higher‑level game/turn information.
- `setup.log` – database/setup.
- `Scopes._Random.log` – random_log_scopes.
- `script_documentation\effects.log`, `triggers.log`, `event_targets.log`, `event_scopes.log`, `modifiers.log` – produced by the `script_docs` console command, refreshed each patch.

Lines in `debug.log` are written with a timestamp + source‑file prefix similar to `[18:54:27][effectimpl.cpp:1110]: …`. That prefix is engine‑generated; your `debug_log` payload follows it. The community convention (CK3 wiki *Scripting* page, "Debug and error logs can be used to export information") is to **prefix your line with a unique tag** (e.g. `"[CK3LLM] …"`) and have your external watcher tail the file and grep for that tag.

### 2.4 Format limitations

- Strings are written as a single line; embedded newlines are not respected by `debug_log`. If you want structured records, encode a single‑line JSON object or a delimiter‑separated record.
- Localisation token resolution (`$KEY$`) happens before output; data‑function `[ … ]` interpolation also happens before output. Anything not understood becomes an error in `error.log` instead.
- The string is limited to the localisation engine's line length; very long lines do get truncated. The practical convention is to break a record across multiple `debug_log` calls keyed by a UUID, or to use a script value/variable to chunk.
- There is no ability to choose the output filename from script. Everything goes to `debug.log` (or the random log), which means **a tail‑and‑filter sidecar process is mandatory** if you want isolated streams.

### 2.5 Performance considerations

- Logging is synchronous and on the main thread. Running `debug_log` once per character per pulse (e.g. inside `every_living_character` on `yearly_playable_pulse`) is feasible at small/medium realms; doing it inside a `monthly_…_pulse` for the whole world is a known FPS killer.
- `error.log` in particular is famous for ballooning to **tens or hundreds of GB** with mods enabled — there is a Steam guide with thousands of views describing the issue and a workaround that opens the file in Word so the engine cannot write to it. This is an SSD‑wear concern for any pipeline that runs long sessions; redirect logs out of OneDrive‑synced folders, and consider running with `-develop` only when actively developing.
- `debug_log_scopes = yes` is *very* expensive (it dumps the full active scope graph) — use sparingly.
- A scripted_effect wrapper (`my_log_char = { debug_log = "..." }`) costs effectively nothing extra over the underlying `debug_log` but lets you toggle logging globally with one edit.

---

## 3. Save file structure (`.ck3`)

### 3.1 Container

A `.ck3` file is one of three things, identified by a header byte sequence at the start:

- A plain‑text Clausewitz script file (autosaves are commonly written this way, uncompressed).
- A ZIP archive with a single inner file called `gamestate` plus a small `meta` block — the standard manual save.
- An *Ironman* binary save: the same structure but with tokens encoded as 16‑bit IDs that have to be resolved against a token table. Paradox does not publish that token table, but the open‑source community has reconstructed it and most parsers ship optional support via an environment variable.

Confirmed by both the CK3 wiki *Modding* page (the manual unzip‑then‑rename‑to‑.ck3 trick) and by `rakaly/ck3save`, which exposes `SaveHeaderKind::UnifiedText` / `TextZip` / binary kinds.

### 3.2 Top‑level sections in `gamestate`

The file is a Clausewitz `key = { … }` structure (newlines, no commas). The high‑level sections include (names verified against the CK3 wiki *Modding* page and the save‑editing forum guide):

- `meta_data { version = "…"  date = …  player … }`
- `currently_played_characters = { … }`
- `played_character = { … }` for each player
- `living = { … }` – *every alive character* keyed by ID. Each has an `alive_data` block with current gold, prestige, piety, stress, traits, dread, opinion, councillor positions, intentions, and a `court_data` block.
- `dead_unprunable = { … }` – important characters (player ancestors, famous historical figures, etc.) preserved across pruning.
- `dead_prunable` / pruned dead set – minor dead characters retained while still referenced.
- `dynasties { dynasty_house { … } dynasty { … } }`
- `landed_titles { … }`, `provinces { … }`, `counties { … }`, `armies { … }`, `wars { … }`
- `cultures { culture_manager { … } }`, `religion { faith_manager { … } }`
- `secrets { secret … }`, `schemes { scheme … }`, `factions { … }`, `alliances { … }`
- `coat_of_arms_manager_database` (ends around index 17278), and `next_id = …` counters.
- `activity_manager { … }` (Tours & Tournaments and later DLCs add: ongoing activities, host, guests, intents, current phase).
- `travel_plan_manager { … }` (Tours & Tournaments) – every active travel plan, current location, cost, travel options selected, danger level, route waypoints.
- `army_manager { … }`, `combat_manager { … }`, `legends { … }` (Legends of the Dead), `accolades { … }`, `domiciles { … }` (Roads to Power adventurer camps), `tax_slots { … }`, `governments { … }`.

### 3.3 What character history actually persists

This is the part most relevant to an LLM "biographer" use case. Inside each `living` / `dead_unprunable` character entry you typically find:

- `first_name`, `birth = <date>`, `dynasty_house`, `culture`, `faith`, `traits = { … }` (current), `nickname`.
- `family_data` – parents, spouses (current and former), children, concubines.
- `dead_data = { date = … reason = … killer = … liege = … domain = { … } government = … }` (only on dead characters; this preserves the *snapshot* at death).
- `alive_data` (only on living): gold, prestige, piety, stress, dread, sin/virtue traits, current intentions, court positions held, focus, lifestyle perks, education, currently visited holding.
- `memories = { memory = { type = …  date = …  participants = { … }  variables = { … } } }`. This is the engine's own narrative log: `memory_type` keys (defined in `common/character_memory_types/`) such as `memory_grand_wedding`, `memory_won_battle`, `memory_imprisoned`, `memory_accolade_glorious_event`, etc., each carrying a date and participant list. **This is the single most useful structured "what happened to this character" record in the save.**
- `relations = { … }` – best friend / rival / nemesis / lover IDs.
- `schemes`, `secrets` (with `participants`, `known_secrets`, `target`, `owner`).
- `variables = { variable = { name = … data = … } }` – any modder‑set scripted variables on the character. This is a deliberate side‑channel: if your on_action stamps `set_variable = { name = chronicle_last_event value = "…" }` the value lands here and survives saves.
- `landed_data` if landed (titles held, claims, vassal contract, gold income breakdown).
- For Tours & Tournaments: a current `traveling = yes/no`, a reference into `travel_plan_manager`, and an `activity` reference if attending one.
- For Roads to Power adventurers: `domicile = …`, `camp_position`, `camp_buildings`, `contracts = { … }` (active and completed; completed contracts include `outcome`, `pay`, `date`).

What is **not** preserved as an explicit log:

- There is no global "event journal" that records every fired event over the character's life. The closest the engine has is the `memories` array, which is curated (only events with a `memory_type` create one).
- There is no explicit "trait history over time" – only the current trait set is stored. Education traits and personality traits are mutated in place. The exception is congenital/genetic traits, which carry birth dates implicitly.
- Travel history is *not* preserved as a list of past trips. Only the current `travel_plan` and any `memory_type` entries spawned by completed activities (e.g. `memory_grand_tour`, `memory_pilgrimage_completed`) survive. If you want a full travel history you must emit it yourself via on_actions §6.

### 3.4 Parseability

The format is line‑oriented Clausewitz script. It is parseable but has several traps:

- Keys can repeat at the same level (lists are encoded that way).
- Some values are quoted; many are not.
- RGB triples and date triples use the same `{ a b c }` shape — context‑dependent typing.
- Ironman uses a binary token format; you need a token map.
- Floating point can include `nan` and `inf` strings.
- Embedded `#` is a comment character.

Mature parsers exist; do not write your own:

- **`rakaly/ck3save`** (Rust) – the de‑facto reference. Handles plain text, zipped, and ironman (with a token resolver). Ships a `Gamestate` model. Used by the Rakaly online save analyzer and by community tools.
- **`pdx-tools` / Rakaly** – web service that ingests CK3 saves and renders structured analysis.
- **`scorpdx/ck3.json`** – older, fast CK3‑to‑JSON converter used by the family‑tree exporter.
- **`brunal/ck3`** – minimal Racket parser, mostly an exploration project.
- **`crschnick/pdx_unlimiter`** – Java GUI/library that reads CK3 (and other PDS) saves; useful as a model‑reference even if you don't use the GUI.

---

## 4. Less common / unconventional extraction methods

### 4.1 Memory reading / process injection

There is no public, mature CK3 memory‑reading toolkit comparable to, say, the Stellaris or HoI4 cheat trainers. Two reasons:

1. CK3's gameplay state is enormous and updated on every tick; a stable offset table for "current player gold" is feasible, but a stable map of "every character → memories array" is not.
2. The community has converged on the script logging path and the save‑file path because they are stable across patches.

What does exist:

- Generic Cheat Engine tables (Fearless Revenge etc.) that read scalar player stats. Useless for narrative.
- The Paradox launcher's debug overlay (`-debug_mode`, `-develop`) and the in‑game console (commands like `inspect`, `trigger_inspector`, `effect_inspector`, `gui_editor`, `debug_camera`, `time`, `event`, `script_docs`, `release_mode`). The `inspect` command opens an in‑game inspector pane on any scope which is essentially Paradox‑internal memory reading rendered as scope trees.

For an LLM pipeline, **process injection is not the right layer** — the script API + log file is.

### 4.2 Game overlay tools

There is no CK3‑specific structured overlay (no SteamWorks rich‑presence "current activity = Grand Tour to Constantinople" feed, for example). Steam rich presence only exposes "playing CK3". RTSS / Discord overlays are purely cosmetic.

### 4.3 Community tools that already parse CK3 externally

- **`rakaly/ck3save`** – parser library (Rust crate, `docs.rs/ck3save`). Handles ironman.
- **Rakaly.com / pdx-tools.com** – hosted save analyzer + leaderboard with structured per‑character output.
- **`TCA166/CK3-history-extractor`** – the closest thing to a chronicle generator. Reads the save, walks the player lineage, and emits an HTML "wikipedia" with per‑character pages, faith/culture death graphs, dynasty family trees, de jure title timelapses, and a timeline graphic of empire lifespans. Rust, MIT, ~50 stars, actively updated through 2026 (latest release 2.4.7, March 2026). This is the single best starting point for a save‑file‑based pipeline and is mod‑aware via Steam Workshop discovery and `--include` flags.
- **`crschnick/pdx_unlimiter`** – more general Paradox save manager/editor.
- **`blastentwice/CK3-Family-Tree-Exporter-To-Gramps`** – uses `scorpdx/ck3.json` to dump JSON, then exports a CSV consumable by the Gramps genealogy program.
- **`amtep/tiger`** (CK3‑Tiger) – **not** a save parser; it's a static *mod* validator. Reads vanilla + your mod's PDX script files, complains about missing localisation, scope‑mismatch effects, dead/lieges‑born‑after errors, etc. Very useful when you write your own logging mod because it will catch most syntax mistakes before you launch the game. Latest release tracks CK3 1.17.0. There is a VS Code extension by user unLomTrois and a GitHub Action by user Bahmut.
- **`TrevSh/ck3_log_parser`** – a small Python+GUI tool that parses `error.log`, `debug.log`, and `game.log`. Worth using as a reference for log format quirks, but not robust.
- **CK3 Log Analyzer (Steam Workshop, id 3588196638)** – another error‑log triage utility that sorts errors by mod directory; explicitly notes that real semantic analysis would require "re‑creating part of the game engine," which is a useful sanity check on what a logger can and cannot achieve.
- **Pdx‑script tooling**: `OldEnt/crusader-kings-3-triggers-modifiers-effects-event-scopes-targets-on-actions-code-revisions-list` (a versioned archive of `script_docs` outputs across patches – very useful when you need to know whether `on_travel_plan_arrival` existed in 1.9 vs 1.11), `jesec/ck3-modding-wiki` (a git mirror of the Paradox wiki's modding pages, important because the live wiki keeps timing out), and `my-mods/awesome-ck3` (resource list).

### 4.4 Debug console output hooks

Any console command run with `-debug_mode` echoes to `game.log`. The most useful for data extraction:

- `script_docs` – dumps the canonical lists; run after every patch update.
- `inspect` and `effect_inspector`/`trigger_inspector` – open in‑game panes; their outputs do not go to disk in a structured form, but they are how you discover scope chains interactively.
- `event <eventid>` – fires arbitrary events, useful for testing your logger.
- `release_mode` – toggles the in‑game error tracker UI.

There is no documented "always‑on console relay to stdout" — CK3 does not expose its console as a TTY. The supported plumbing is the log file.

### 4.5 Mod frameworks that expose more data than vanilla

Three patterns exist in the community:

- **Custom on_action wrappers**. The most prominent example is **`pharaox/travelers`** (Steam Workshop: Travelers). It deliberately hooks the vanilla travel `on_actions` (`travel_on_actions.txt`) and republishes them with extra granularity for non‑rulers, additional travel danger events, "departure" and "arrival" messages, and game‑rule‑configurable performance tuning (the `Location Tracker Interval` option, defaulted to ten days, exists explicitly because the underlying `on_teleport` and `on_invalid_location` on_actions are noted as having "negative performance impact"). Reading its source is the fastest way to learn how to attach a logger to travel without breaking vanilla.
- **Scripting framework / utility mods** such as the *Gamerule Gadget* (mid‑run game rule editor) and various "Mod Toolbox" mods on the Workshop. These do not actually expose new code APIs; they only expose more *script‑level* knobs.
- **Community Flavor Pack** is not a scripting framework — it is a portrait/accessory pack. It does not provide new on_actions or hooks. Mentioned here to dispel a common misconception.

There is no plug‑in / native‑code modding API for CK3. All mod code is PDX script. The hardest "extra data" you can extract beyond vanilla is therefore the data you yourself stamp into character variables or print to `debug.log` from within a script mod.

---

## 5. Existing mods / projects doing similar things

Listed roughly in order of relevance to a "feed an LLM the chronicle of my game" goal:

1. **CK3-history-extractor (TCA166)** – save‑file → static HTML wiki of your dynasty. Closest functional analogue. Output is structured per entity and would be straightforward to repoint at a JSON sink for an LLM.
2. **Travelers (pharaox)** – best example of a mod that hooks travel on_actions cleanly and adds its own messages and events. Pattern is directly portable to a logging mod.
3. **Memorialist / "Birth and Death dates on character window"‑style mods** – these read the same `memories` array discussed in §3 and surface it in‑game; reading their scripted_effects shows which `memory_type` keys carry useful narrative payload.
4. **Rakaly / pdx‑tools.com** – online save uploader that extracts and visualises structured data; closed‑source but their `ck3save` crate is open.
5. **CK3 Chronicle Weaver** (yeschat.ai GPT) and **CK3 GPT** (yeschat.ai GPT) – marketing‑forward LLM wrappers around the CK3 wiki. They do **not** ingest your save and they are not modding tools; included because they appear in any search for "CK3 + AI narrative." Their relevance to your project is essentially zero beyond confirming there is appetite for AI‑driven CK3 narrative.
6. **Nexus mod "Qyvaria"** and similar AI/performance overhauls – they advertise "AI now uses real memory" but on inspection that means they manipulate vanilla `memory_type` and opinion modifiers more heavily; they do **not** add new logging hooks. Useful as a stress test for whether a logging mod cohabits with heavy AI overhauls.
7. **Various "chronicle" Workshop collections** (e.g. *The Grand Chronicle* megacampaign mod list) – these are mod *lists*, not chronicle generators.

There is, as of this writing, no public CK3 mod that exists specifically to ship structured event records to an external process. That niche appears unfilled, which is consistent with what the community discussions on the Paradox forums and Mod Co‑op Discord say.

---

## 6. Tours & Tournaments and Roads to Power on_actions

These two DLCs add the largest number of post‑launch hooks and are the most relevant to a character‑travel‑aware narrative LLM.

### 6.1 Tours & Tournaments (travel + activity rework)

The travel system replaced the older instant‑teleport activity system. The relevant code on_actions (verified against the Travelers mod source and `travel_on_actions.txt` in vanilla):

- **`on_travel_plan_start`** – fires when a character's travel plan begins. Scope: travel plan; `scope:owner` is the traveling character, `scope:destination` is the target province/barony, `scope:travel_plan` exposes intent, danger, options taken, total estimated days, and the route as an ordered list of provinces.
- **`on_travel_plan_arrival`** – fires when the traveler reaches the final destination. Same scopes as above.
- **`on_travel_plan_movement`** – fires per leg of the route as the entourage enters each new province (the engine's "danger event" timing). This is the hook to stamp a "X arrived at Y" record; Travelers uses it for its messaging.
- **`on_travel_plan_cancel`** / **`on_travel_plan_abort`** – cancellation paths.
- **`on_travel_leader_changed`**, **`on_travel_plan_invalidated`**.
- Activity (grand activity) hooks, defined in `common/activities/activity_types/*.txt` as `on_start`, `on_phase_active`, `on_phase_end`, `on_complete`, `on_cancel`, plus the global on_actions:
  - **`on_activity_started`**, **`on_activity_phase_active`**, **`on_activity_phase_end`**, **`on_activity_completed`**, **`on_activity_cancel`**, **`on_guest_join_activity`**, **`on_guest_left_activity`**, **`on_invite_to_activity`**, **`on_host_changed`**.
  - In scope you get `scope:activity` (with `.GetType`, `.GetHost`, `.GetGuests`, `.GetCurrentPhase`, `.GetIntent`), `scope:host`, `scope:guest`, and where applicable `scope:province`/`scope:capital_province` of the activity location.
- **`on_teleport`** and **`on_invalid_location`** also exist (both flagged by the Travelers mod author as having "negative performance impact" — the mod disables them by default).
- **Regency** (T&T free addition): `on_regency_started`, `on_regency_ended`, `on_regent_changed`.

For your purposes the high‑value combo is `on_travel_plan_start` → `on_travel_plan_movement` (per leg) → `on_travel_plan_arrival` → `on_activity_started` → `on_activity_phase_*` → `on_activity_completed`. That gives you a complete "X set out from A to attend the Grand Tournament at B, passed through C/D/E, arrived, won the joust phase, returned home" timeline.

### 6.2 Roads to Power (administrative gov + landless adventurer)

Roads to Power adds a *second* travel/location loop because adventurers carry their camp around and execute contracts. The on_actions added (verified by inspection of the DLC's vanilla files and confirmed against the wiki *Adventurer* and *Roads to Power* pages):

- **`on_become_adventurer`**, **`on_settle_adventurer`** – transitions in/out of landless adventurer status.
- **`on_camp_moved`** / **`on_domicile_moved`** – the camp location changed; scope contains `scope:domicile`, `scope:from_province`, `scope:to_province`.
- **`on_contract_offered`**, **`on_contract_accepted`**, **`on_contract_completed`**, **`on_contract_failed`**, **`on_contract_expired`** – the full lifecycle of an Adventurer contract. Scopes include `scope:contract` (with `.GetIssuer`, `.GetProvider`, `.GetType`, `.GetReward`), `scope:provider` (the adventurer), and `scope:issuer`.
- **`on_camp_member_joined`** / **`on_camp_member_left`** – camp roster changes.
- Administrative government hooks: **`on_governor_assigned`**, **`on_governor_removed`**, **`on_theme_change`**, **`on_governorship_change`**, **`on_appointment_changed`**, **`on_influence_spent`**.
- **`on_legend_created`**, **`on_legend_promoted`** (Legends of the Dead, the free 1.13 addition; relevant because legends generate `memory_type` entries).

Since adventurers continuously trigger `on_travel_plan_*` and `on_camp_moved`, a logger that listens to those plus `on_contract_*` will produce a fully detailed picaresque chronicle without any additional plumbing.

### 6.3 Sanity checks for your pipeline

Two practical caveats from the modding community:

- The list of on_actions does not always change between minor patches but the *scopes* attached to them do. Always re‑run `script_docs` after a CK3 patch and diff `event_scopes.log` and the `on_actions.info` output against your logger's expectations.
- A few on_actions (`on_teleport`, `on_invalid_location`, `on_knight_combat_pulse`, anything firing per‑province per‑month) are heavy enough in a large realm or late game that the Travelers mod author benchmarked a 35% real‑time slowdown on max speed in observer mode. Treat these as opt‑in hooks behind a game rule.

---

## 7. Recommended architecture for an external local‑LLM pipeline

Drawing the threads together, the path with the lowest engineering cost and best stability is:

1. **A small mod** that appends scripted on_actions to: `on_birth_child`, `on_death`, `on_marriage`/`on_divorce`/`on_concubinage`, `on_title_gain`/`on_title_lost`, `on_war_started`/`on_war_won_*`, `on_imprison`/`on_release_from_prison`, `on_travel_plan_start`/`on_travel_plan_arrival`/`on_travel_plan_movement`, `on_activity_started`/`on_activity_completed`, `on_contract_*`, `on_camp_moved`, plus `yearly_playable_pulse` for state snapshots. Each appended on_action calls a single `scripted_effect` that prints a tagged single‑line JSON record to `debug.log`.
2. **A sidecar process** (Python or Rust) that tails `debug.log`, filters on the unique tag, parses the JSON, and feeds your LLM. Use a process supervisor to truncate or rotate `debug.log` periodically so it never reaches the multi‑GB pathology Paradox Plaza users have documented.
3. **A periodic bulk dump** by reading the latest autosave/exit‑save with `rakaly/ck3save` (or by shelling out to `CK3-history-extractor` as a reference implementation). Use this for backfill, dynasty trees, and to recover the `memories` array, which is richer than what you can stamp into the log without a great deal of script.
4. **Validate every patch** with `ck3-tiger` before launching, and **dump `script_docs` every patch** and diff against your previous run.
5. **Keep the logger behind a game rule** so users can disable it for performance or ironman achievements, since any user mod disables achievements by default.

This combination — scripted on_actions for live events, save parsing for dense per‑character memory arrays, log file as the transport, ck3‑tiger as the linter, ck3save / CK3‑history‑extractor as the structured reader — is the pragmatic state of the art in the CK3 modding community as of CK3 1.17–1.18.
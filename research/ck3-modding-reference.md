# CK3 modding: a complete technical reference

Crusader Kings 3 offers one of the most extensible modding frameworks in Paradox's catalog, built on the Clausewitz/Jomini engine's plain-text scripting system. **Every game mechanic from traits to GUIs is defined in editable text files**, and mods work by overriding or extending these files through a well-defined folder structure. The system has no MTTH (Mean Time To Happen), uses a scope-based scripting language for all logic, and since patch 1.5 supports custom UI windows through scripted widgets. However, core AI military behavior, army movement, and some UI data types remain hardcoded and inaccessible to modders.

This reference covers mod structure, scripting, events, GUI modding, council tasks, character data storage, AI behavior, and DLC compatibility — everything needed to build or understand a CK3 mod.

---

## How CK3 mods are organized on disk

### The descriptor system

CK3 uses **two descriptor files** per mod. The outer `.mod` file (e.g., `my_mod.mod`) sits in `Documents/Paradox Interactive/Crusader Kings III/mod/` and tells the launcher where to find the mod. The inner `descriptor.mod` lives inside the mod folder itself. Both use identical Clausewitz key-value syntax, except the outer file includes the `path` key while the inner one omits it:

```
# Outer file: mod/my_mod.mod
version="1.0.0"
tags={ "Gameplay" "Decisions" }
name="My Mod"
supported_version="1.12.*"
path="mod/my_mod"
```

Available fields include `name` (required, minimum 3 characters), `version` (mod version), `supported_version` (game version, wildcards allowed), `tags` (categorization), and `picture` (thumbnail filename). Values must be in double quotes. The launcher can auto-generate these files via **Mod Library → Upload Mod → Create a Mod**.

### Folder structure mirroring the base game

A mod folder mirrors the base game's `game/` directory. Only include folders and files you actually modify or add. The critical directories are:

| Folder | Purpose |
|--------|---------|
| `common/` | 80+ subfolders for all database objects — the heart of most mods |
| `events/` | Event scripts organized by namespace |
| `gui/` | UI definitions in `.gui` files, plus `gui/scripted_widgets/` |
| `localization/` | Text strings by language (e.g., `localization/english/`) |
| `gfx/` | Textures, icons, portraits, illustrations, 3D models |
| `history/` | Historical setup — characters, provinces, titles |
| `music/` | Custom music and sound files |
| `map_data/` | Map topology and province definitions |

The `common/` folder deserves special attention. Key subfolders include `common/traits/`, `common/decisions/`, `common/on_action/`, `common/character_interactions/`, `common/scripted_triggers/`, `common/scripted_effects/`, `common/script_values/`, `common/modifiers/`, `common/council_tasks/`, `common/schemes/`, `common/casus_belli_types/`, `common/scripted_guis/`, `common/buildings/`, `common/culture/`, `common/religion/`, and DLC-gated folders like `common/court_positions/` (Royal Court), `common/legends/` (Legends of the Dead), and `common/activities/` (Tours & Tournaments).

### File loading: replacement, not merging

CK3 does **not merge** mod files with base game files. If a mod provides a file at the same path and filename as a vanilla file, the mod's version **replaces the entire vanilla file**. However, the engine supports object-level overrides through two ordering systems:

**LIOS (Last In, Only Served)** governs most database objects. Files in the same folder load in ASCIIbetical order, and the **last** definition of a given top-level object key wins. To override a single scripted trigger without touching the vanilla file, create a new file that sorts later alphabetically (e.g., prefix with `zz_mymod_`). Only top-level declarations can be overridden this way.

**FIOS (First In, Only Served)** governs GUI types. The **first** definition loaded wins, so to override a vanilla UI type, your file must sort earlier (e.g., prefix with `00_`).

**On_actions have special behavior**: the `events`, `random_events`, and nested `on_actions` blocks **append** rather than overwrite, making them mod-safe. However, `trigger` and `effect` blocks directly inside an on_action will overwrite vanilla definitions — a critical distinction for compatibility.

---

## The scripting language: triggers, effects, and scopes

### Three building blocks

Every piece of CK3 game logic is built from three primitives. **Triggers** evaluate conditions and return true/false — `is_ruler = yes`, `gold > 1000`, `has_trait = brave`. **Effects** change game state — `add_gold = 100`, `add_trait = ambitious`, `imprison = { target = scope:prisoner type = dungeon }`. **Event targets** switch the current scope to another game object — `liege`, `primary_heir`, `capital_county`.

Triggers support logical operators: `AND = {}` (default in all trigger blocks), `OR = {}`, `NOT = {}`, `NOR = {}`, and `NAND = {}`. Control flow uses `trigger_if`/`trigger_else_if`/`trigger_else` for conditional evaluation, and `if`/`else_if`/`else` with `limit = {}` blocks for conditional effects.

### The scope system

Scopes are the addressing mechanism for all game objects. CK3 defines scope types for **characters, titles, provinces, houses, dynasties, faiths, cultures, wars, casus belli, armies**, and more. Each scope type exposes different triggers, effects, and event targets.

Key scope keywords include `root` (the top-level entity the script executes on), `this` (the current scope, usually implicit), and `prev` (the previous scope before the last switch). Named scopes use prefixes: `title:k_france`, `character:12345`, `culture:english`, `faith:catholic`. Chaining works with dots: `mother.primary_title.holder`.

**Saved scopes** are the primary mechanism for passing data between script blocks:

```
father = { save_scope_as = dad }
scope:dad = { add_gold = 100 }
```

The `save_scope_value_as` variant stores computed values. Saved scopes persist through called events and scripted effects within the same execution chain but are cleared when execution ends. Use `save_temporary_scope_as` inside trigger blocks. The safe comparison operator `?=` checks for scope existence before comparing: `capital_county ?= title:c_byzantion`.

### Scripted triggers, scripted effects, and inline scripts

Reusable logic blocks defined in `common/scripted_triggers/` and `common/scripted_effects/` can be invoked anywhere their type is valid. Both support parameter passing via literal text replacement with `$PARAM$` syntax:

```
# Definition in common/scripted_effects/:
reward_character = {
    add_gold = $GOLD$
    add_prestige = $PRESTIGE$
}

# Usage:
reward_character = { GOLD = 500 PRESTIGE = 200 }
```

**Inline scripts** (`common/inline_scripts/`) provide file-level includes: `inline_script = my_script_file` or `inline_script = { script = my_script_file PARAM = value }`. Recursion is not allowed in any of these systems.

### Character interactions

Defined in `common/character_interactions/`, these create right-click menu options on characters. The structure includes `is_shown` (visibility trigger), `auto_accept` (whether the target must accept), `on_accept`/`on_auto_accept`/`on_decline` (effect blocks), and `ai_will_do`/`ai_potential` (AI logic). Two key scopes — `scope:actor` and `scope:recipient` — are always available. Interactions can include `redirect` blocks to reassign targets and `populate_actor_list`/`populate_recipient_list` for selection interfaces.

### Decisions

Defined in `common/decisions/`, decisions include `is_shown` and `is_valid` trigger blocks, a `cost` block (gold, prestige, piety), `cooldown`, `effect`, and AI control via `ai_check_interval`, `ai_potential`, and `ai_will_do`. Setting `major = yes` highlights the decision in the UI.

---

## Events and the on_action system

### Event structure

Events live in `.txt` files under `events/`. Each file declares a namespace (e.g., `namespace = my_mod`) and contains one or more event blocks where the ID comes first:

```
my_mod.1001 = {
    type = character_event
    title = my_mod.1001.t
    desc = my_mod.1001.desc
    theme = diplomacy
    
    left_portrait = { character = root animation = personality_honorable }
    
    trigger = { is_adult = yes }
    
    immediate = {
        save_scope_as = event_ruler
    }
    
    option = {
        name = my_mod.1001.a
        add_prestige = 100
        trigger_event = { id = my_mod.1002 days = { 7 14 } }
        ai_chance = { base = 50 }
    }
    
    after = { remove_character_flag = my_flag }
}
```

**Event types** include `character_event` (standard popup), `letter_event` (message format), `court_event` (Royal Court view), and `fullscreen_event` (dramatic full-screen). Hidden events use `hidden = yes` and have no options or UI — they execute their `immediate` block silently.

Descriptions support dynamic text via `first_valid` and `triggered_desc` blocks that select localization keys based on conditions. Portraits use `left_portrait`, `right_portrait`, `lower_left_portrait`, `lower_center_portrait`, and `lower_right_portrait` positions with optional animation specifications. Themes (defined in `common/event_themes/`) bundle icons, backgrounds, lighting, and sound.

### CK3 eliminated MTTH entirely

This is a fundamental difference from CK2. **There is no Mean Time To Happen system in CK3.** All events fire through on_actions — hooks in the game code triggered by specific occurrences. The developers stated this was intentional: MTTH caused statistical anomalies and performance problems. Random/periodic events are instead handled through **pulse on_actions** (yearly, monthly, weekly) with weighted random selection pools, and `trigger_event` with random delay ranges like `days = { 7 14 }`.

### On_actions: code hooks and scripted chains

On_actions are defined in `common/on_action/` and come in two types. **Code on_actions** fire from hardcoded game events: `on_birth_child`, `on_death`, `on_war_started`, `on_title_gain`, `on_marriage`, `on_game_start_after_lobby`, and dozens more. **Scripted on_actions** are custom-defined and chained from other on_actions.

The critical compatibility rule: **nest your additions inside the `on_actions` block** (which appends safely), never add `trigger` or `effect` blocks directly to a vanilla on_action (which overwrites):

```
# SAFE — appends to vanilla:
on_birth_child = {
    on_actions = { my_custom_birth_handler }
}

my_custom_birth_handler = {
    trigger = { is_female = yes }
    effect = { trigger_event = my_mod.1001 }
}
```

Event chains work by having each event trigger the next via `trigger_event` in option blocks or `immediate`. Scope data passes between events using `save_scope_as`. Cooldowns are typically managed with timed character flags.

---

## GUI modding: the Jomini widget system

### Architecture overview

CK3's UI is built on the **Clausewitz/Jomini declarative widget framework** — a proprietary system using `.gui` text files with brace-delimited blocks. It is not XML or HTML. All UI files live in `gui/`, textures in `gfx/interface/` as `.dds` files, and hot-reloading works in debug mode (`-debug_mode -develop` launch options). A built-in GUI Editor is accessible in debug mode for live inspection.

### Widget types and layout

**Container widgets** handle layout: `window` (top-level), `widget` (generic container), `vbox`/`hbox` (vertical/horizontal box layouts with `layoutpolicy_horizontal = expanding` or `preferred`), `flowcontainer` (auto-flowing), `scrollarea` (scrollable), `dynamicgridbox` (data-driven grid using `datamodel`), `fixedgridbox`, and `overlappingitembox`.

**Content widgets** display data: `button` (clickable, uses `onclick`/`onrightclick`), `icon` (texture display via `texture = "path.dds"`), `text_single` (single-line text), `textbox` (multiline), `progressbar`, `editbox` (text input), and `portrait_button` (3D character portrait).

All widgets share properties like `name`, `size = { w h }`, `position = { x y }`, `parentanchor`, `visible`, `enabled`, `tooltip`, `alpha`, `margin`, and layout policies. Data binding uses square-bracket expressions: `text = "[Character.GetName]"`.

### Data binding and Scripted GUIs

The `datacontext` property sets the scope for data expressions within a widget and its children: `datacontext = "[CharacterWindow.GetCharacter]"`. The `datamodel` property provides lists for repeating containers. **Datamodels are hardcoded by Paradox** — you cannot create new data types, only use what the engine exposes. Run the `dump_data_types` console command to generate complete documentation of available data functions.

**Scripted GUIs** (defined in `common/scripted_guis/`) bridge UI and game logic, allowing buttons to execute script effects:

```
# common/scripted_guis/my_sgui.txt
my_button_action = {
    scope = character
    is_shown = { is_alive = yes }
    is_valid = { gold >= 50 }
    effect = { add_gold = -50 add_prestige = 100 }
}
```

In `.gui` files, bind to it: `onclick = "[ScriptedGui.Execute(GuiScope.SetRoot(GetPlayer.MakeScope).End)]"`.

### Adding new windows

Since patch 1.5, **scripted widgets** enable entirely new UI windows. Create the window in a `.gui` file, then register it in `gui/scripted_widgets/my_widgets.txt` with the format `gui/my_file.gui = my_window_name`. The limitation is that scripted widget windows lack special data contexts — they can only access globally available data and the local player character. For toggling visibility, use `GetVariableSystem.Toggle('key')` (non-persistent) or Scripted GUIs with actual script variables (persistent).

### Templates, types, and the override problem

**Templates** (reusable property blocks applied with `using = template_name`) and **types** (named widget definitions inside `types MyGroup {}` blocks, used like custom widgets with `block`/`blockoverride` for partial customization) provide composability. Types follow FIOS — the first loaded wins, so override files need earlier-sorting names (prefix with `00_`).

The biggest GUI modding challenge is that **major windows live in single files** (`window_character.gui`, `window_council.gui`, `hud.gui`, etc.), and only one mod can replace each file. The community **Customizable GUI** framework splits `window_character.gui` into modular pieces for multi-mod compatibility, and template hooks (`using = other_mod_hook`) allow cross-mod integration since unknown templates are silently ignored.

---

## Spymaster tasks and the council system

### How council tasks are defined

Council positions are defined in `common/council_positions/` and tasks in `common/council_tasks/`. The five positions — Chancellor (Diplomacy), Marshal (Martial), Steward (Stewardship), **Spymaster (Intrigue)**, and Court Chaplain (Learning) — plus the Spouse position each have dedicated task files (e.g., `00_spymaster_tasks.txt`).

Task definitions specify `position` (which councillor), `default_task`, `county_target` (whether it targets a map county), `potential` (visibility trigger), modifier blocks like `council_owner_modifier` (bonuses applied to the liege), `on_monthly` or `on_monthly_county` (periodic effects), and `ai_will_do` (AI preference weighting). Key scopes include `scope:councillor`, `scope:councillor_liege`, and `scope:county`.

### The three spymaster tasks

**Find Secrets** is a county-targeted task. The spymaster investigates a specific county and has a monthly chance (scaling with **Intrigue skill**) to discover secrets about residents. Discovery grants the liege knowledge of a secret usable for blackmail and weak hooks. The scripted trigger `spymaster_task_find_secrets_suitable_minor_secret_trigger` filters eligible secrets — excluding those already known, those involving the spymaster or liege, and non-interesting types.

**Support Schemes** is a realm-wide (non-targeted) task that applies continuous modifiers boosting the liege's **hostile scheme power** and **scheme success chance**. The bonuses scale with the spymaster's Intrigue skill. This directly enhances active murder, abduction, and similar schemes.

**Disrupt Schemes** is also realm-wide and applies **hostile scheme resistance** and **scheme discovery chance** bonuses to the liege. This is widely considered the most important task for keeping rulers alive, as it disrupts incoming murder and abduction schemes.

### Extending the council system

**Adding new tasks to existing councillors is fully supported.** Create a new file in `common/council_tasks/` referencing an existing position. Mods like "Immersive Realm Espionage" demonstrate adding a custom spymaster task. **Adding entirely new council positions** is partially possible via `common/council_positions/` but requires significant GUI work since the vanilla council UI is designed for exactly 5+1 positions. Total conversion mods like AGOT have added positions (Castellan, Admiral) with custom GUI layouts.

---

## Storing custom data on characters

### Variables, flags, and modifiers compared

CK3 provides three primary mechanisms for character data storage. **Variables** (`set_variable`, accessed as `var:name`) hold numbers, booleans, scope references, or string flags. They persist in save files and support arithmetic via `change_variable` with `add`, `subtract`, `multiply`, `divide`, and `modulo`. **Global variables** (`set_global_variable`, accessed as `global_var:name`) are accessible from any scope. **Local variables** (`set_local_variable`, accessed as `local_var:name`) exist only during the current script execution.

**Character flags** (`add_character_flag`, checked with `has_character_flag`) are lightweight boolean markers — simpler and more performant than variables when you only need presence/absence tracking. Both variables and flags support **timed expiration** via `days`, `months`, or `years` parameters.

**Character modifiers** (defined in `common/modifiers/`, applied with `add_character_modifier`) provide stat bonuses/penalties. They can be permanent or timed and display in the character's modifier list. Opinion modifiers are a separate system for relationship effects, applied with `add_opinion` targeting a specific character.

### Lists for multiple objects

Temporary lists use `add_to_list`/`every_in_list`/`any_in_list` within a single execution chain. **Variable lists** (`add_to_variable_list`) persist in save files and can be displayed in the GUI. Both support duration parameters for auto-removal. The UI can access variable lists and global variable lists through data binding.

### Accessing custom data from GUI

In `.gui` files, variable values are accessible via `[Character.MakeScope.Var('my_var').GetValue|0]` and existence checks via `[Character.MakeScope.Var('my_flag').IsSet]`. Since patch 1.5, `[GetScriptValueBreakdown('my_value', Character.MakeScope)]` provides value breakdowns in tooltips. Global variables can be read similarly through `GetPlayer.MakeScope.GetVariable(...)`.

---

## AI behavior: what's scriptable and what's not

### The hardcoded boundary

Paradox's own documentation states plainly: **"Most of AI or army behavior is done in game code, which is inaccessible to modders."** Core military AI (army movement, battle logic, siege priorities, retreat behavior), alliance evaluation, war target selection algorithms, and army composition decisions are all hardcoded. There are no `ai_defines` files like CK2 had for tweaking fundamental AI parameters.

### What modders can control

The **`ai_will_do` block** appears in decisions, character interactions, and council tasks. In decisions, it returns a percentage chance (0–100) that the AI takes the action. In events, `ai_chance` provides **relative weights** between options — values of 50 and 100 mean option B is chosen roughly two-thirds of the time. Both support `modifier` blocks that add or multiply based on triggers, with trait checks being the most common condition.

AI personality is driven by **ai_value_modifiers** derived from traits: `ai_boldness`, `ai_compassion`, `ai_greed`, `ai_honor`, `ai_rationality`, `ai_energy`, `ai_sociability`, `ai_vengefulness`, and `ai_zeal`. These influence weighted decisions throughout the game. Modders can reference these values in `ai_chance` and `ai_will_do` blocks.

Casus belli definitions include AI scoring weights that influence how much the AI values using each CB. Modders can adjust these, create new CBs with custom weights, or use `is_ai` triggers to make CBs player-only. However, the actual target selection algorithm is inaccessible.

### Pseudo-AI behavior through scripting

The workaround pattern uses **periodic on_actions with hidden events**: fire monthly/yearly pulses for AI characters, evaluate conditions, and apply effects. Combined with `ai_potential` on decisions and `ai_chance` on event options, this creates flexible custom behavior loops. Character flags and variables track AI state across multiple evaluation cycles.

---

## DLC compatibility and mod conflict avoidance

### DLC feature gating

Most scripting features ship in free patches, not DLCs. DLC primarily adds content (events, assets, mechanics). Use `has_dlc_feature` triggers to gate DLC-dependent content:

```
trigger = { has_dlc_feature = royal_court }
```

Valid feature flags include `royal_court`, `court_artifacts`, `the_northern_lords`, `hybridize_culture`, `the_fate_of_iberia`, `friends_and_foes`, and more for newer DLCs. This lets mods work with or without specific DLC. Never reference DLC-only assets (3D models, specific artwork) without checking ownership first.

### Avoiding mod conflicts

- **Use unique namespaces** for events, unique key prefixes for traits/decisions/modifiers (e.g., `mymod_brave_bonus`)
- **Never overwrite entire vanilla files** when object-level LIOS overrides suffice — create new files sorted later alphabetically
- **For on_actions, always use the `on_actions = {}` nesting** (which appends safely) rather than adding `trigger`/`effect` blocks directly
- **Character interaction category indices must be contiguous** — gaps cause crashes. If multiple mods add categories, coordinate index ranges
- **Localization files must use UTF-8 with BOM encoding** — wrong encoding silently fails
- Use **compatibility patches** for popular mod combinations that need to merge conflicting files
- Use `database_conflicts.log` and `error.log` (in `Documents/Paradox Interactive/Crusader Kings III/logs/`) to debug override conflicts and script errors

### Development workflow

Launch with `-debug_mode -develop` for hot-reload, console access, and the GUI Editor. The `script_docs` console command generates `triggers.log`, `effects.log`, and `event_targets.log` with all available functions for the current game version. Use WinMerge or Git to diff mod files against updated vanilla after patches. VS Code extensions (CK3 Tiger for validation, CWTools for autocomplete) significantly improve the development experience. The community `ck3-mod-base` project on GitHub provides Git-friendly base game files for automated merge workflows across patches.

## Conclusion

CK3's modding framework is remarkably open for a commercial game — virtually every gameplay system from traits to council tasks to the full GUI is defined in editable text files. The scope-based scripting system with triggers, effects, and saved scopes provides genuine programming power without requiring compiled code. The elimination of MTTH in favor of on_actions, the addition of scripted widgets for custom UI, and Scripted GUIs bridging interface to game logic represent meaningful improvements over CK2's architecture. The primary constraints are hardcoded AI military behavior, hardcoded GUI data types, and the file-level replacement system that makes multi-mod GUI compatibility an ongoing challenge. For any serious mod project, the most impactful practices are disciplined use of LIOS overrides over file replacement, on_action nesting for safe event injection, unique namespacing throughout, and `has_dlc_feature` gating for DLC-conditional content.
# Wire format — schema versions

Every event is **two lines** in `debug.log`: a tagged event line followed
immediately by an engine scope dump.

```
<engine prefix> CHRONICLER|v=1|t=death|d=15th of September, 1066 AD|cause=battle
<engine prefix> Harold of Godwin of k_england (Internal ID: 31175 - Historical ID 122) weak (Character - 31175)!
... (more scope-dump lines from the same dump) ...
```

The engine prefix (`[18:54:27][effectimpl.cpp:1110]: `) is added by CK3
itself; we own everything after `CHRONICLER|`. The second line — and any
that follow up to the next CHRONICLER tag — is the engine's
`debug_log_scopes = yes` output. The first `Internal ID: <num>` we encounter
is the root scope, which for `on_death` is the dying character.

## Why two lines

Three CK3 constraints, all established during V01-S04, force this design:

1. `debug_log = "literal"` does NOT route through the loc engine, so
   data-function references like `[ROOT.Char.GetID]` and `[GetCurrentDate]`
   are emitted verbatim. The engine prefixes the line with `ERROR:`.
2. Brackets `[...]` inside loc strings are parsed as data-function calls.
   An unrecognised name like `[CHRONICLER]` emits `ERROR:[CHRONICLER]`.
   The tag is therefore bare `CHRONICLER` (no brackets).
3. `debug_log = some_loc_key` DOES go through the loc engine, but the
   resolution context contains **only globals** — ROOT, THIS, and saved-
   via-`save_scope_as` named scopes are all unbound. So the dying
   character's ID cannot be interpolated into the line via any pattern.

The escape hatch is `debug_log_scopes = yes`, an effect that dumps the
current scope tree to debug.log immediately. We emit our tagged line, then
trigger the dump. The Python tailer pairs them — `IncrementalParser`
holds the envelope and completes it from the first `Internal ID:` it
finds in the next ~50 lines.

Pipes have no special meaning in jomini script strings or loc strings,
so they survive both layers unchanged.

## Fields

| Field | Type | Source | Meaning |
|---|---|---|---|
| `v` | int | tagged line | Schema version. Bumped only when an existing payload's shape breaks. |
| `t` | string | tagged line | Event type. Matches the `chronicler_emit_<type>` scripted_effect filename and the discriminator in `chronicler.schema.events`. |
| `d` | string | tagged line | In-game date string (CK3's `[GetCurrentDate.GetStringLong]` form, e.g. `15th of September, 1066 AD`). |
| `c` | int | scope dump | Primary character — first `Internal ID:` in the engine's scope dump. |
| `x` | string | tagged line | Optional comma-separated free-string tags. Omitted when empty. |

Top-level keys (`v`, `t`, `d`, `x`) go to the envelope from the tagged
line; every other `key=value` pair lands in the `p` payload; `c` comes
from the paired scope dump. Pydantic coerces numeric strings to ints
where the model demands one.

## Constraints

- One line per emission. `debug_log` does not respect embedded
  newlines; multi-segment payloads must be split across lines that
  share a UUID and re-assembled by the parser.
- Lines are subject to the localisation engine's max line length;
  research/ck3-data-extraction.md §2.4 warns that very long lines get
  truncated. Travel routes with many legs (v0.4) will exceed this and
  emit per-leg lines sharing a `travel_plan_id`.
- Values may not contain `|`. Free-text payload values (e.g. localised
  names in v0.4+) will need URL-encoding when they land.
- Values may contain `=` — only the first `=` separates key from value.
- The line is parsed by `chronicler.tailer.parser.parse_line`; the
  Pydantic `EventPayload` discriminated union in
  `chronicler.schema.events` rejects unknown `t` values and any extra
  payload fields (`extra="forbid"`).

## v=1

### `t = "death"`

Emitted from `on_death` via the `chronicler_emit_death` scripted_effect.

| Field | Required | Type | Notes |
|---|---|---|---|
| `p.killer` | optional | int | CK3 ID of the killer if `scope:killer` exists. |
| `p.cause` | optional | string | Free-form cause descriptor (e.g. `"battle"`, `"natural"`). |

Example:

```
CHRONICLER|v=1|t=death|d=16th of September, 1066 AD
[scope dump line with the dying character's Internal ID]
```

### v0.2 lifecycle types

The following types ship at v0.2 and share an empty payload `{}`. The
primary character (`c`) and any participants come from the engine scope
dump (see `Saved event targets:` and `Saved list targets:` blocks)
rather than inline interpolation, since the loc engine has no script
scope context when resolving `debug_log = loc_key`.

| Type (`t`) | on_action hook | ROOT scope is | Common dump scopes |
|---|---|---|---|
| `birth` | `on_birth_child` | newborn child | `mother`, `father` |
| `marriage` | `on_marriage` | one of the spouses | other spouse |
| `divorce` | `on_divorce` | one of the spouses | other spouse |
| `title_gain` | `on_title_gain` | gainer | title, previous holder |
| `title_lost` | `on_title_lost` | loser | title |
| `war_started` | `on_war_started` | attacker | defender, casus_belli |
| `war_won_attacker` | `on_war_won_attacker` | winning attacker | defender |
| `war_won_defender` | `on_war_won_defender` | winning defender | attacker |
| `imprison` | `on_imprison` | prisoner | imprisoner |
| `release` | `on_release_from_prison` | released prisoner | releaser |

Exact scope-dump field names are CK3-version-dependent and verified
empirically per event type during V02-S01 smoke. The parser extracts
them via :class:`chronicler.tailer.parser.IncrementalParser` into
`event_participants`.

The mod-side spec lives in
`mod/chronicler/common/scripted_effects/chronicler_emit_effects.txt`.
The Python-side schema lives in `src/chronicler/schema/events.py`.

## Adding a new event type

1. Add a `chronicler_emit_<t>_log` localization key in
   `mod/chronicler/localization/english/chronicler_l_english.yml` whose
   value is `CHRONICLER|v=1|t=<t>|d=[GetCurrentDate.GetStringLong]|<static-payload>`.
   Only globals reach the loc engine here — do not try to embed
   character-scope data; that comes from the scope dump.
2. Add a `chronicler_emit_<t>` scripted_effect that wraps:
   ```
   if = {
       limit = { chronicler_is_relevant = yes }
       debug_log = chronicler_emit_<t>_log
       debug_log_scopes = yes
   }
   ```
   The `debug_log_scopes = yes` is what makes the dying character's ID
   recoverable.
2. Append to the relevant vanilla `on_action` in the appropriate
   `mod/chronicler/common/on_action/chronicler_*_on_actions.txt` file
   using the `on_actions = { chronicler_on_<t> }` form. **Never** add
   `trigger` or `effect` directly to a vanilla on_action — those
   blocks overwrite, the others append.
3. Add a Pydantic model to `chronicler.schema.events` and extend
   `EventPayload` to include it in the discriminated union.
4. Add an entry to this document under the current `v=` section.
5. If — and only if — an existing payload's shape changes
   incompatibly, bump `SCHEMA_VERSION` and start a new section here.

## Verifying after a CK3 patch

`scripts/diff_script_docs.py` (FND-06) compares our hooked on_actions
and the data-function getters we use against the previous patch's
`script_documentation/` dump. Any rename or scope change shows up
there, before our mod silently breaks at runtime.

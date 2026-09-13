# CK3 patch playbook

When Paradox ships a CK3 patch (1.18.x → 1.19, etc.), several things may
break:

- **rakaly** version compatibility with the new save format.
- **Heraldry assets** may shift — new patterns, new emblems, renamed
  named-colours, occasionally a moved subdirectory.
- **Save-file top-level keys** may change. (`ck3_chronicler-ayu` was
  filed when 1.18.3 omitted the canonical `playthrough_id` from one
  build — future patches may break similar load-bearing assumptions.)
- **Prompt v3 / v5 fixtures** may need re-evaluation if the diff layer
  starts emitting new event types.

This runbook is the 30-minute fix path. Code surgery should not be
required — when something does need a code change, open an issue
referencing the patch version so the next patch's playbook can avoid
the same trap.

`ck3_chronicler-8jz` tracks this playbook. Last reviewed against the
1.19 → ? upgrade path.

## 0. Triage signal

**Run `chronicler doctor` first.** Two probes flag patch-related
breakage informationally:

- `Heraldry up to date` — flips to "CK3 source dir is newer than the
  last extract" when the install dir's mtime is newer than the
  recorded `extracted_at`. This is the cheapest patch detector.
- `CK3 install located` and `Narrative backend configured` should both
  pass before continuing — fix them first if they don't.

A failing `Heraldry up to date` probe is also surfaced in the Settings
UI as a "Stale (CK3 patched)" pip on the Heraldry pipeline card with a
"Re-extract" button — ship-side equivalent of step 2 below.

## 1. Verify rakaly still parses the new save format

```bash
.venv/Scripts/python -c "from chronicler.save.rakaly import convert_save_to_json; \
  from pathlib import Path; \
  d = convert_save_to_json(Path('<path-to-a-fresh-autosave.ck3>')); \
  print('OK', len(d), 'top-level keys')"
```

Expected: a number near 60 ("OK 60 top-level keys") and no exception.

If rakaly raises:

- Update the rakaly binary at `<repo>/rakaly-*/`. Releases:
  https://github.com/rakaly/cli/releases/latest
- After replacing, re-run the verification command.

If rakaly is current but parsing fails on a top-level-key shape change,
file a bd ticket and check `chronicler.save.parse._parse_*_lookup`
helpers for the changed key.

## 2. Re-extract heraldry assets

The Settings UI's Heraldry pipeline card has a "Re-extract" button when
`is_stale` is true. CLI equivalent:

```bash
chronicler heraldry extract
```

(Add `--force` to re-convert every DDS even when PNGs already exist —
needed only if a patch reused a filename for a different image.)

Re-run `chronicler doctor` afterwards. The `Heraldry up to date` probe
should now pass.

## 3. Yearly-diff smoke fixture

```bash
chronicler smoke-yearly \
  --baseline <data-dir>/baselines/<campaign-id>.pkl \
  --save    <path-to-a-newer-yearly-autosave.ck3> \
  --db      <data-dir>/campaigns/<campaign-id>.db \
  --campaign-id <campaign-id>
```

(The `<data-dir>` is `~/Documents/chronicler/` by default; override
with `CHRONICLER_DATA_DIR`.)

Expected: an "ingested in N s" line followed by a `by event_type:`
table. Watch for:

- **New event types** the diff layer didn't recognise pre-patch.
  Record them; if any matter for narrative, file a bd ticket
  ("teach the diff layer about `event_type=...`").
- **Zero events ingested** when the save clearly progressed (a year+).
  This is the warning sign that a top-level key changed shape — go
  back to step 1 with the failing save loaded into rakaly directly.
- **Massive event explosion** (>10x prior runs). Usually the
  forward-only stale-read guard catching a regression — see
  `chronicler.save.ingest._is_advance_candidate` for the rationale.

The smoke command rewrites the baseline on success. **Pass a copy** if
you want to preserve the original.

`scripts/smoke_yearly_diff.py` is kept as a back-compat entrypoint for
anything that still shells out by file path; it delegates to the same
function (`chronicler.smoke.smoke_yearly.run_smoke_yearly`).

## 4. Re-evaluate the biography prompt

If step 3 surfaced new event types, walk one tracked character through
a regenerate-biography:

```bash
chronicler regenerate-biography --campaign <name> --ck3-id <id>
```

Read the output. Two failure modes that warrant a prompt revision:

- New event types appear as bare engine slugs in the biography prose
  ("…and then `revoke_title_event`…").
- Pre-existing prose patterns degrade (the model now over-indexes on
  the new events because they're novel).

Either signal means the next prompt revision (`biography_v6+`) should
include explicit guidance for the new event taxonomy. File a bd ticket
under `me4` (or successor).

## 5. Update fixture saves (when relevant)

`tests/integration/fixtures/` may contain pinned saves. If they're tied
to a specific CK3 version, document the pin in the test docstring and
keep the playbook entries below in sync.

Currently no fixture pins are version-locked — every integration test
either constructs SaveSnapshot programmatically or mocks rakaly.

## 6. Close-out

When all four steps pass:

- Update this file's "Last reviewed against" line with the new patch
  version pair.
- If a code change was needed, link the bd ticket inline so the next
  reader sees the breadcrumb.

If a patch breaks something this runbook can't recover from in 30
minutes, file a bd ticket tagged `patch-blocker` and pause the upgrade
on this machine until the underlying fix lands. The local-only
chronicler doesn't have to follow the live game version on day one.

---

## Appendix: the optional debug-mode mod (merged from the old FND-06 playbook)

Everything below applies **only if you run CK3 with `-debug_mode` and
the chronicler mod enabled** — the optional rich-data path retained
from v0.2. The save-parse pipeline above is the primary transport and
needs none of this. (27ov.74 merged the formerly-separate
`patch_playbook.md` here; this section is its surviving content.)

CK3 patches change `on_action` hooks and data-function getters from
time to time. Two mod-side risk surfaces:

1. **Hooks we wired are renamed or removed** (`on_death` → `on_die`,
   `on_travel_plan_arrival` → something else). The mod silently stops
   emitting; the tailer just sees no new lines.
2. **Data-function getters change shape** (`[ROOT.GetID]` returns
   string vs int, `[scope:killer.GetID|0]` no longer accepts a default).
   Lines start landing in `quarantine` instead of `events`.

Both are caught by diffing the engine's authoritative reference dumps
between patches:

1. **Launch CK3 with debug flags** (`-debug_mode -develop` in the
   launch options).

2. **Open the in-game console** with backtick (`` ` ``) and run
   `script_docs`. This dumps reference files into
   `<Documents>/Paradox Interactive/Crusader Kings III/logs/script_documentation/`:
   `on_actions.info` (every on_action + expected scope),
   `event_targets.log`, `event_scopes.log`, `effects.log`,
   `triggers.log`, `modifiers.log`.

3. **Snapshot the new dump into the repo:**

   ```bash
   mkdir -p reference/script_docs/<new_version>
   cp <Documents>/Paradox\ Interactive/Crusader\ Kings\ III/logs/script_documentation/* \
      reference/script_docs/<new_version>/
   ```

4. **Diff against the baseline:**

   ```bash
   uv run python scripts/diff_script_docs.py \
     --base reference/script_docs/<old_version> \
     --new  reference/script_docs/<new_version>
   ```

   The script reads our mod files, finds every `on_<xxx>` we hook and
   every `[<scope>.<getter>]` we reference in scripted_effects, and
   reports which changed between the two dumps.

5. **Triage anything red:**

   - Renamed on_action we hook: rename our handler in the matching
     `mod/chronicler/common/on_action/chronicler_*_on_actions.txt`.
     Note it in `docs/schema_versions.md` if it changes how events
     get emitted.
   - Getter changed signature / removed: update the matching
     `chronicler_emit_*` scripted_effect. Bump `SCHEMA_VERSION` in
     `chronicler.schema.events` only if the **payload shape** must
     change — getter renames don't break the v=1 payload as long as
     the mod side adapts.

6. **Update the symlink:** `ln -sfn <new_version>
   reference/script_docs/current` (Windows: `cmd /c mklink /D
   reference\script_docs\current ..\<new_version>`, or delete and
   recreate).

7. **Bump `supported_version`** in `mod/chronicler/descriptor.mod`.

8. **Smoke it:** kill a character in-game and verify a row lands in
   the campaign DB within 5 seconds (the V01-S04 procedure). If it
   doesn't, the diff missed something.

### error.log hygiene (debug-mode sessions)

The Steam-Workshop community has documented `error.log` ballooning to
multi-GB sizes. After a debug-mode play session, check the size of
`<Documents>/Paradox Interactive/Crusader Kings III/logs/error.log`.
Growth beyond ~10 MB per session usually means our mod is emitting
localisation errors (unresolved `$KEY$` tokens) or spamming a failing
effect — search it for `[CHRONICLER]`-adjacent lines. Rotate the file
periodically; CK3 recreates it.

### Adding a new on_action hook

The diff script extracts hooked names by grepping for `^on_<word> =`
at the start of a line in `mod/chronicler/common/on_action/*.txt`.
Add the hook using the standard append pattern:

```pdx
on_some_new_event = {
    on_actions = {
        chronicler_on_some_new_event
    }
}

chronicler_on_some_new_event = {
    effect = {
        chronicler_emit_some_new_event = yes
    }
}
```

Then re-run the diff against your last baseline to confirm the new
hook exists in the engine's `on_actions.info`.

# Script docs baselines

Snapshots of CK3's `script_documentation/` dump, one folder per CK3
version. Used by `scripts/diff_script_docs.py` to detect renames or
removals of the on_actions and getters our mod depends on.

## Capturing a baseline

1. Launch CK3 with `-debug_mode -develop`.
2. Open the console (backtick) and run `script_docs`.
3. Copy the contents of

   ```
   <Documents>/Paradox Interactive/Crusader Kings III/logs/script_documentation/
   ```

   into a new folder here, named for the CK3 version:

   ```
   reference/script_docs/1.17.0/
   ```

4. Update the `current` symlink:

   - Linux/macOS: `ln -sfn 1.17.0 reference/script_docs/current`
   - Windows: `cmd /c "rmdir reference\script_docs\current 2>nul & mklink /D reference\script_docs\current ..\\1.17.0"`

The first baseline lands as part of `ck3_chronicler-v0y` once the user
runs `script_docs` against the installed CK3 version. See
`docs/patch-playbook.md` for the full post-patch checklist.

## What goes here

The relevant files for the diff script:

- `on_actions.info`
- `event_targets.log`
- `event_scopes.log`
- `effects.log`
- `triggers.log`
- `modifiers.log` (referenced by the diff but not currently checked)

Other files in the dump are fine to commit — disk is cheap and they
form a complete record of the engine's contract per version.

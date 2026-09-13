# Chronicler — CK3 mod

The mod source. Installed into CK3's user mod folder by
`scripts/install_mod.py` (V01-S01) — never copy this directory by hand;
the script symlinks it so edits in-repo are picked up by CK3 directly.

Layout mirrors the base game's `game/` directory:

```
mod/chronicler/
├── descriptor.mod                       # inner descriptor (no path key)
├── common/
│   ├── on_action/                       # per-category appends to vanilla on_actions
│   ├── scripted_effects/                # emit helpers (one per event type)
│   └── scripted_triggers/               # filters (e.g. chronicler_is_relevant)
└── localization/                        # only if we ever need a string
```

Why the script files live in `scripted_effects/` rather than inline
inside `on_action/` files: CK3's `on_actions` block does **not**
hot-reload with `-debug_mode -develop`, but `scripted_effects` and
`scripted_triggers` do. Keeping the on_action body to a single
`scripted_effect` call lets us iterate on emission logic without
restarting the game (research/ck3-data-extraction.md §1.5).

The outer `chronicler.mod` descriptor — required by the launcher — is
generated into the user mod folder by `scripts/install_mod.py`. It is
not committed because it points at the absolute path of this repo on
the operator's machine.

---
description: CK3 1.20 writes every dead_unprunable entry twice, so rakaly --duplicate-keys group hands back a list of two identical dicts per dead character
type: gotcha
---

CK3 1.20 saves repeat each `dead_unprunable` id twice back to back, and the two copies are byte-identical.
The 1066 bookmark save does it too, so this is not something that builds up during a campaign.
We melt with `--duplicate-keys group`, which coat-of-arms charges need, so every such entry comes back as `[{...}, {...}]` instead of `{...}`.
On a 1076 save that was 32,403 of 32,674 entries.
No other collection in the save is grouped this way.

Any reader that guards with `isinstance(raw, dict)` silently skips the whole dead collection.
That is how King Harold #32638 (campaign 00f1e372, died 1069.11.5) was never recorded as dead and never got a biography.
He died straight into `dead_unprunable` and never passed through `dead_prunable`.

Read character slots through `character_entry` / `lookup_character_record` in `src/chronicler/save/raw_record.py`, never with a bare dict check.

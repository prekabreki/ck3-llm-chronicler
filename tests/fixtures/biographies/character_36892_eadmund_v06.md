# Sample biography — Eadmund (36892), v0.6 architecture

Generated 2026-05-01 against `qwen3:14b` with `biography_v1.md` prompt
(post-iteration). Source data: 4 vanilla memory events imported from
the user's actual autosave via `chronicler import-save` — no debug.log
or `-debug_mode` involved.

This is the **v0.6 reference output** for "rich-data biography quality."
Compare with `character_36715_v2.md` (v0.2 sparse single-event case)
to see how the prompt holds up across data densities. Both work.

The character: Eadmund Godwineson, the player's character in the
test campaign. He inherited the kingdom of England twice in three
days during V01-S04 testing (Harold killed → Eadmund inherits → his
heir killed → Eadmund inherits again).

The event log fed to the LLM (4 vanilla memory events from the save):

- 1066-09-18 `relative_died` (Harold, character 31175)
- 1066-09-19 `ascended_throne_memory`
- 1066-09-21 `relative_died` (heir, character 36715)
- 1066-09-22 `ascended_throne_memory`

What the prompt successfully avoids vs early v1 attempts:
- ✅ Real first name "Eadmund", not "Character 36892"
- ✅ No hallucinated specifics about Hastings, Norman conquest, etc.
- ✅ No Qwen template phrases ("shrouded in mists", "tapestry of time")
- ✅ Length matched to evidence (4 events → one substantial paragraph,
  not 4 padded ones)
- ✅ Honest acknowledgment of unknowns without melodrama

What's still slightly thin (prompt-iteration follow-up):
- ⚠️ Says "ascended to a position of authority" instead of inferring
  "throne" / "kingship" from `ascended_throne_memory`'s memory_type
  — the prompt could teach the LLM to interpret memory_type names.
  Save for v0.5 prompt-refinement work; not blocking v0.6 demo.

---

Eadmund was born on the tenth day of December in the year of our Lord one thousand and forty-nine. The recorded events of his life are confined to the autumn of the year one thousand and sixty-six, when he witnessed the passing of a relative on the eighteenth day of September, followed by his own ascension to a position of authority on the nineteenth. Two days later, another relative died, and on the twenty-second, Eadmund assumed a further office, though the nature of these roles and the identities of those involved are not specified in the records. His life beyond these dates is unmarked by the chronicler's hand, and the year of his death remains unknown.

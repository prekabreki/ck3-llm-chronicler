# Your chronicle directory

This directory holds two things: the instructions that turn a language model into
a chronicler, and every biography it has ever written for your campaigns.

`chronicler init-prose` scaffolded it. It is yours to edit — the craft rules in
`CLAUDE.md` and `voice/` are the levers on how your chronicle reads, and changing
them is the intended way to change the prose.

## Layout

```
CLAUDE.md                                    Role reshape + craft rules. The heart of it.
voice/biography.md                           Per-kind voice rules, layered on top of
voice/biography-woven.md                       CLAUDE.md for whichever kind is running.
voice/chronicle-export.md
briefings/<campaign>/<ck3_id>-vN.md          Inputs, written by the chronicler.
biographies/<campaign>/<ck3_id>-vN.md        Outputs, written by the model.
```

`<campaign>` matches the chronicler's per-campaign database filename. `<ck3_id>`
is the CK3 character id. `vN` is the biography version, which auto-increments each
time you regenerate a character and mirrors the version column in the campaign DB.

`_smoke/synthetic-jarl-v1.md` is a worked example pair — a fully invented jarl, not
a character from anyone's campaign. Read the two files side by side to see what the
model is given and what it is expected to produce. Nothing depends on them; delete
them once you have real chronicles.

## How the two files relate

A biography file is **prose only** — no frontmatter, no headers, no commentary. Its
one metadata line is the attribution footer the chronicler appends after the model
finishes.

That means a biography read on its own gives you no way to check it. To see which
facts grounded a passage, open the matching briefing at the same path under
`briefings/`. Same campaign, same id, same version. The briefing is the entire
factual ground the model was working from — if something in the prose isn't traceable
to it, that is a bug worth reporting.

## How the instructions are assembled

Per generation the chronicler builds one system prompt: `CLAUDE.md` first, then the
`voice/` file for the kind being generated. Both are read fresh from disk every
time, so an edit takes effect on the next generation with no restart.

If a file is missing the chronicler refuses to generate rather than falling back to
a generic assistant voice — register-less prose looks like a real biography, which
makes it the worst kind of silent failure.

## Version control

This directory is a git repository (`init-prose` ran `git init` unless a repo was
already there). The chronicler commits each briefing and biography pair as it
writes them, so your chronicle accumulates as reviewable history and you can see
how a character's biography changed when you regenerated it.

There is no remote. Add one if you want the chronicle backed up or shared across
machines — nothing in the chronicler depends on it either way.

## Editing the craft rules

Worth knowing before you change `CLAUDE.md`:

- The anti-fabrication rule is what keeps biographies traceable to the briefing.
  Loosening it produces better-sounding prose about events that never happened.
- The opening rule and peer-mention rule exist because models reliably drift into
  worldbuilding preambles that name characters who never recur.
- The forbidden-registers list is about phrasing, not interpretation. It is the
  most rewarding section to extend once you have read a few dozen biographies and
  started noticing your model's particular tics.

Keep a note in `NOTES.md` when you find something that works. Future you will have
forgotten why the rule is there.

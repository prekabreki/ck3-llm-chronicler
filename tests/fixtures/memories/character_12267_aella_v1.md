# Reference memory consolidation — Ælla "the Impaler" 12267, prompt v3

Captured 2026-05-01 from a real `chronicler regenerate-memories
12267 --campaign live-session` call against an 867-bookmark Anglo-
Saxon ruler whose first three years featured a defensive war, a
house feud against Ivar the Boneless, marriage, two more wars
(both lost), title loss to that same nemesis, and a slide into
adventurer-mode wandering. 22 vanilla memory events across the
arc plus a few state-diff travel events from save-tail.

This is the canonical "rich-data" reference; the earlier
Eadmund fixture covers the sparse case.

## Subject

- ck3_id: 12267, name: Ælla
- Nickname: the Impaler
- Birth: 824.1.1
- Culture: english · Faith: catholic
- Currently: alive (turned adventurer mid-arc)

## Why this fixture matters for prompt iteration

The first attempt against this character (prompt v2) demonstrated
two failure modes that earlier Eadmund-only iteration didn't
surface:

1. **ID leakage.** Memories named characters as raw IDs ("married
   38379", "rival 15100") despite the prompt's paraphrase rule.
2. **Hedge slip-through.** Despite an explicit ban, all three
   memories used hedge words ("suggesting", "indicate", "implying").

Prompt v3 added two things:

- A glossary block in the user_prompt mapping every referenced
  character ID to their name+nickname, with a strict "use these
  names, never the IDs" instruction in the system prompt
- A "verify before emitting" rule: scan each memory for ban-list
  words before output and rewrite if any are found

## Output (canonical run, prompt v3, qwen3:14b)

```json
{
  "memories": [
    {
      "body": "Holds a deep-seated nemesis with Ivar the Boneless, rooted in a house feud that escalated from rivalry to open war.",
      "trigger_event_id": 6492
    },
    {
      "body": "Married Beorhtgyth in a strategic union, later producing a child with her, though the relationship may be strained by political pressures.",
      "trigger_event_id": 6494
    },
    {
      "body": "Repeatedly loses wars to Halfdan the Whiteshirt and Ivar the Boneless, culminating in the loss of a title and a pattern of forced displacement.",
      "trigger_event_id": 15632
    }
  ],
  "supersedes": []
}
```

## Why this output is good

- **Names through, IDs gone.** "Ivar the Boneless", "Beorhtgyth",
  "Halfdan the Whiteshirt" — all resolved from the glossary, with
  nicknames carried as natural medieval-style epithets.
- **Synthesis present.** Memory 1 traces the rivalry → nemesis →
  feud → war escalation as one observation, not four. Memory 3
  reads "forced displacement" from the combination of war losses +
  title loss + post-870 travel events — an inference no single
  event explicitly states (and exactly the adventurer-arc reading
  the user asked about during live testing).
- **Almost hedge-free.** Two of three memories are fully
  declarative; one slips with "may be strained." Down from 3/3
  hedges in v2.

## Known remaining failure modes

- One in three memories may still slip a hedge word through. With
  qwen3:14b at default temperature, the ban list isn't perfectly
  enforced. See `V05-N02_iteration_log.md` for the negative
  finding on temperature tuning.
- The character's own nickname ("the Impaler") didn't appear in
  these three memories. That's fine when none of the memories
  cover acts that earned the epithet; could be a future iteration
  to give the LLM a positive prompt about epithet-anchoring when
  there's evidence in the events.

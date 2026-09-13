# Reference memory consolidation — Eadmund 36892, prompt v1

> **Note (2026-05-01):** This Eadmund-based fixture remains as a
> regression check for the early-arc, sparse-events case. The richer
> reference is now `character_12267_aella_v1.md` against Ælla "the
> Impaler" with 22 vanilla memories spanning rivalries, war losses,
> title loss, and adventurer-mode travels.


Captured 2026-05-01 from a real `chronicler regenerate-memories
36892 --campaign v06-smoke2` call against the V01-S04 inheritance
arc save state. Used for prompt-iteration regression — when
`memory_consolidation_v1.md` changes, run this character through the
consolidator and compare voice + concision.

## Subject

- ck3_id: 36892, name: Eadmund Godwineson
- Birth: 1049.12.10 (alive at time of consolidation)
- Culture: english · Faith: catholic

## Input events (4 vanilla memories, the inheritance arc)

| event id | date       | type           | payload |
| -------- | ---------- | -------------- | ------- |
| 1347     | 1066-09-18 | vanilla_memory | relative_died (dead_relation: 31175) |
| 1348     | 1066-09-19 | vanilla_memory | ascended_throne_memory (flavor_character: 31175) |
| 1349     | 1066-09-21 | vanilla_memory | relative_died (dead_relation: 36715) |
| 1350     | 1066-09-22 | vanilla_memory | ascended_throne_memory (flavor_character: 36715) |

## Active memories at consolidation time

(none — first pass)

## Output (canonical run, prompt v1, qwen3:14b)

```json
{
  "memories": [
    {
      "body": "Feels duty-bound to claim the throne after a relative's death, as if the weight of lineage compels him.",
      "trigger_event_id": 1347
    },
    {
      "body": "Holds deep attachment to family, as two relatives dying in quick succession have left him visibly shaken.",
      "trigger_event_id": 1348
    },
    {
      "body": "Sees ascension as a burden rather than a choice, his posture stiffening whenever the throne is mentioned.",
      "trigger_event_id": 1349
    }
  ],
  "supersedes": []
}
```

## Why this output

Three angles on the same arc, each interpretive rather than recap:

1. **Duty.** Asserts a *feeling* (duty-bound), not an event ("ascended after").
2. **Grief.** "Visibly shaken" is the observer's read, not in the data.
3. **Posture.** "Posture stiffening" is the kind of physical tell a
   confidant would notice — the spec's "attentive observer" voice.

Each memory could stand alone; together they cover internal state
(duty), emotional state (grief), and outward bearing (posture). The
consolidator was free to emit fewer if angles felt forced — three
distinct angles emerged naturally from a four-event arc.

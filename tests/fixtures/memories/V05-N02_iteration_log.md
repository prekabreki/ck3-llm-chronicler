# V05-N02 prompt iteration log

Captured 2026-05-01 while iterating `memory_consolidation_v1.md`
against character 36892 (Eadmund Godwineson, V01-S04 inheritance
arc) on qwen3:14b. Each pass cleared the campaign DB's `memories`
table, regenerated, and inspected. The prompt is in
`src/chronicler/narrative/prompts/memory_consolidation_v1.md`; this
file records *why* it ended up shaped that way.

## Pass 1 — seed prompt

Output (3 memories, hedge-heavy):

> 1. Eadmund's ascension to the throne following the death of a
>    relative **suggests** a pattern of assuming leadership roles in
>    the wake of personal loss, **possibly indicating** a deep sense
>    of duty or obligation.
> 2. The repeated instances of ascension memories, paired with the
>    deaths of relatives, **imply** that Eadmund **may view**
>    leadership as both a burden and a necessity, **perhaps feeling
>    compelled** to take on responsibilities others cannot.
> 3. The proximity of multiple significant events — losses and
>    ascensions — within a short timeframe **hints at** a life marked
>    by upheaval and sudden transitions, which **may have shaped**
>    Eadmund's resilience or pragmatism.

Three problems: every sentence hedges (suggests / may / perhaps /
implies / hints / may have); each memory restates the events instead
of interpreting them; all three say the same thing ("leadership-as-
duty after loss") in different academic phrasings.

## Pass 2 — added "Commit. Do not hedge." rule + ban list

Added: explicit ban on `suggests / possibly / perhaps / may / might /
implies / could be / indicates a pattern of`. Demanded declarative
present tense. Demanded distinct angles per memory.

Output (2 memories, sharper):

> 1. Eadmund treats every throne as a debt owed to the dead.
> 2. He believes each ascension is a failure to prevent the next death.

Voice is right. Problem: memory #1 verbatim echoes the prompt's
"Right:" example. The model grabbed the example phrase rather than
generating its own.

## Pass 3 — genericized the example

Replaced the specific Eadmund example with a generic she-rules one
and added "do not echo its phrasing."

Output: model parroted the new example with pronoun swap. One-shot
examples are too quotable for a 14B model.

## Pass 4 — dropped the example entirely (current prompt)

Final prompt has the rule + ban list but no quotable example —
relies on the constraint enumeration alone.

Output (canonical run, captured as
`character_36892_eadmund_v1.md`):

> 1. Feels duty-bound to claim the throne after a relative's death,
>    as if the weight of lineage compels him.
> 2. Holds deep attachment to family, as two relatives dying in
>    quick succession have left him visibly shaken.
> 3. Sees ascension as a burden rather than a choice, his posture
>    stiffening whenever the throne is mentioned.

Three distinct angles (duty / grief / posture-tells), declarative
voice, no hedge words, original phrasing.

## Reproducibility note

qwen3:14b varies run-to-run with default temperature. Two follow-up
runs with the v4 prompt produced:

- 2 memories (duty + emotional hollowness) — different angles, same
  voice, both declarative.
- 3 memories where one borrowed "suggesting" — a hedge slip-through.

Quality is acceptable but not perfectly stable. Future iterations
(v2) might tighten by listing more banned hedge phrases or by
lowering temperature in the OllamaProvider. For v0.5 release the
v1 prompt is good enough — most runs produce observer-voiced output.

## Takeaways for v0.5+ prompt work

1. **Concrete examples in a prompt get parroted.** Better to enumerate
   constraints and trust the model to find phrasing than to hand it a
   quotable line.
2. **Hedge words are the failure mode for "interpretive" prompts.**
   Without an explicit ban, the model defaults to academic register.
3. **Letting the model emit fewer memories is load-bearing.** Without
   constraint #4 ("if you can't find distinct angles, emit fewer"), it
   pads to three near-duplicates.

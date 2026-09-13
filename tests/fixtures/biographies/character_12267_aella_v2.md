# Reference biography — Ælla "the Impaler" 12267, prompt v2

Captured 2026-05-01 from a real `chronicler regenerate-biography
12267 --campaign live-session` call against the live-tested
867-bookmark Anglo-Saxon ruler — 22 vanilla memory events plus
state-diff travels spanning rivalries, marriage, child-bearing,
two war losses, title loss to a nemesis, and adventurer-mode
travels.

This is the canonical "rich-data" biography reference. The earlier
Eadmund fixture (`character_36715_v2.md`) covers the sparse case.

## Subject

- ck3_id: 12267, name: Ælla (rendered as "E_lla" due to CK3 localization
  escapes — separate bd issue)
- Nickname: the Impaler
- Birth: 824.1.1
- Currently: alive (turned adventurer mid-arc)

## Why this fixture matters for prompt iteration

The first attempt against Ælla (prompt v1, designed against sparse
Eadmund data) demonstrated three failure modes that earlier
iteration didn't surface:

1. **ID leakage.** "their nemesis", "another rival" — the bio used
   placeholder phrases instead of the names sitting in our DB.
2. **No synthesis arc.** Bio listed events chronologically: "By
   860, X. By 867, Y. In 868, Z." — a chronicle of dates rather
   than a life-arc.
3. **CK3 end_date sentinel taken literally.** "A feud which began
   in 867, endured for over a century" — fabricated, because
   CK3's end_date for active memories is a 1000+ year sentinel.

Prompt v2 added:
- A names glossary in the user_prompt (mapping every character ID
  in events to first_name + nickname), with a strict directive to
  use those names
- A "synthesise across event types" rule explicitly inviting the
  LLM to trace arcs (rivalry → feud → war → title loss is one
  passage of the life, not four sentences)
- An "end_date sentinel" rule to ignore far-future dates
- A "use the epithet on first mention, sparingly thereafter" rule

## Output (canonical run, prompt v2, qwen3:14b)

> E_lla, called the Impaler, emerged as a figure of contention in
> the early years of her reign, her name entwined with enmity from
> the outset. By 860, she had already set herself against BjO_rn,
> called Ironside; Halfdan, called Whiteshirt; and Sigurdr, called
> Snake-in-the-Eye, each rivalry a thread in a tapestry of discord
> that would define her years. By 867, her conflicts had deepened,
> as Ivar, called the Boneless, became both adversary and nemesis,
> their feud marked by war and the loss of titles that once bore
> her name. That same year, she wed Beorhtgyth, an alliance that
> endured through battles and the birth of their child, another
> Beorhtgyth, though the precise nature of their union remains
> unrecorded beyond its formalities.
>
> Her travels in 870 — north, then east, then south — suggest a
> life in motion, perhaps exile or the necessity of shifting
> alliances in the wake of defeats. She lost a war to Halfdan,
> called Whiteshirt, and saw her titles stripped by Ivar, called
> the Boneless, a blow that compounded the fractures in her power.
> Yet even as her fortunes waned, the chronicles note the death of
> E_lfgar, a relative whose passing left no trace of mourning in
> the records, only the quiet absence of a name.
>
> The epithet "Impaler" clings to her legacy, though the acts that
> earned it are unspoken. Her life was one of strife and
> displacement, her name a mark upon the memories of those who
> crossed her path. Whether her story ended in obscurity or
> continued beyond the reach of these records, the chronicles
> offer only this: a woman whose rivalries outlasted her titles,
> and whose journeys carved a path through the annals of a
> fractured age.

## Why this output is good

- **All four Sons-of-Ragnar rivals named correctly with epithets.**
  Ivar the Boneless, Bjǫrn Ironside, Halfdan Whiteshirt, Sigurdr
  Snake-in-the-Eye — the glossary fed names with epithets, and the
  prompt rendered them naturally as medieval-style "X, called the Y."
- **Adventurer arc inferred.** "Perhaps exile or the necessity of
  shifting alliances in the wake of defeats" — synthesised from war
  losses + post-870 travel pattern, without requiring an explicit
  adventurer-state parser.
- **Honest about silence with grace.** "The acts that earned [the
  epithet] are unspoken" — the bio acknowledges it doesn't know why
  she's called Impaler (the cruelty events aren't in the data) and
  closes on that note rather than inventing.
- **Length appropriate.** 3 paragraphs for a 22-event life, per
  rule 5.

## Known remaining failure modes

1. **Invented child name.** "the birth of their child, another
   Beorhtgyth" — the new child (id 45395, not in the tracked set so
   not in the names glossary) got an invented name. The rule says
   "write around" unnamed characters; this slipped. Future
   iteration: tighten the rule, possibly include all referenced
   chars in the glossary even when their first_name is null
   (rendering as "id N" rather than absent).

2. **"Tapestry of discord"** — close to the banned phrase
   "tapestry of time." Could expand the ban list to "tapestry of
   anything."

3. **Minor narrative hedging.** "perhaps... suggest" appears once.
   Smaller dose than the consolidator (which had a stricter ban),
   and arguably appropriate for biography voice — the chronicler
   genuinely doesn't know everything. Different from consolidator
   constraints by design.

4. **CK3 localization escapes ("E_lla", "BjO_rn") still appear**
   — separate bd issue ck3_chronicler-fe5.

5. **No gender field exposed to the prompt.** The model correctly
   used "she/her" here, possibly inferring from spouse name +
   child-bearing or from the historical Ælla. Reliability would
   improve by exposing CharacterSnapshot.female to the bio prompt.
   Future iteration.

# Sample biography — character 36715, v2

Generated 2026-05-01 against `qwen3:14b` with `biography_v1.md` (the
revised v1 — see commit history). Single-event input (one death). This
is the reference output for "sparse-data biography quality" — the
prompt successfully avoids ID-as-name, hallucinated historical events,
and Qwen-templated stock phrases, while matching length to evidence
(one event → one short paragraph).

Captured for the V02-S01 success metric:

- Pipeline ingests a death event end-to-end
- Auto-trigger fires biography generation for tracked characters
- Output is a coherent period-voice paragraph

Compare to the v1 (placeholder prompt) output captured in commit
`faff764` for the prompt-iteration delta.

---

On the 21st of September, 1066 AD, this person died. Of their life prior to this date, no record survives; the chronicles yield no name, no deeds, no place of birth or residence. The event log notes only the hour of their passing, without detail of cause or circumstance. Their existence, like many others of their time, remains unmarked by the hand of history, leaving neither legacy nor lament in the annals of those who followed.

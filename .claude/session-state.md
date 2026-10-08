---
repo: ck3-llm-chronicler
updated: 2026-10-08
open_issues: [2, 3, 4, 5, 6, 7, 9, 10]
in_flight: []
blocked_on:
  - issue: 10
    reason: builds on the verb from 9; promote once 9 merges
  - issue: 2
    reason: needs the user to play a live campaign for the throughput measurement
---

Audited every open issue against the scoping contract and re-checked them against a 1.20 save at 1067.11.1.
9 is promoted to ready-for-agent (cheap tier) and is the next foreman dispatch.
5 was un-deferred: 1.20 saves now populate barter_missions, but what its `barterer` ids point at is unknown, so it is a frontier investigation, not executor work.
3, 4, 6 and 7 stay deferred; their gates were re-checked and are still closed.

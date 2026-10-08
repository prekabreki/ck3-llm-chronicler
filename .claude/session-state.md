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

Fixed the bug that kept the dead King Harold #32638 (campaign 00f1e372) showing as alive with no biography, in 204f503, no issue filed.
CK3 1.20 writes each dead_unprunable entry twice and rakaly groups them into lists, which the parser skipped.
Not yet seen live: on the next autosave after a restart, Harold's death event should land and his biography should be queued.
The earlier audit context still holds: 9 is the next foreman dispatch and 5 is a frontier investigation.

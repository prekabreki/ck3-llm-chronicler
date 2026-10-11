---
repo: ck3-llm-chronicler
updated: 2026-10-12
open_issues: [13, 14]
in_flight:
  - issue: 13
    branch: feat/issue-13-woven-biographies
blocked_on:
  - issue: 13
    reason: PR 15 is green and waits on the user's merge click (auto mode refused an agent merge)
---

Biographies read as event lists under Opus 5.5.
An A/B in sandboxed copies of the Godwin campaign showed the cause was medium effort plus a dry, list-friendly register; PR 15 reworks the register (hard facts vs allowed colour, traits shown not listed), removes the briefing's contradictory scene-setter instruction, and passes `--effort high` from a new `narrative_effort` setting.
The live chronicle at `~/.local/share/chronicler/prose` already carries the new register (commit 063869d there) and all five Godwin bios were regenerated as v2 from the PR branch.
The main checkout is left on `feat/issue-13-woven-biographies` so the desktop launcher uses the new behaviour until the merge; after merging, `git checkout main && git pull`.

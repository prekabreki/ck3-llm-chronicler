---
repo: ck3-llm-chronicler
updated: 2026-10-06
open_issues: [2, 3, 4, 5, 6, 7, 8]
in_flight: []
blocked_on:
  - issue: 8
    reason: needs CK3 1.20 autosaves, which only exist once the user plays a 1.20 campaign
---

Readied the tool for a new campaign on CK3 1.20.0.4.
rakaly is 0.8.21, ck3-tiger 1.19.0 (no 1.20 release yet), and both finders now pick the newest release.
The heraldry staleness check reads source-file mtimes, and the shutdown tests use a readiness handshake.
The user's local `narrative_model` setting is now `claude-opus-5-5`; it was `claude-opus-5`, which runs Opus 5.0.
Heraldry did not need re-extracting: the patch touched no CoA or named-colour files.
The chronicle repo at ~/.local/share/chronicler/prose has no git remote, so the chronicles exist only on this machine.

# Docs

Eight files, in the order you would want them.

**Getting it running**

- [setup-fresh-machine.md](setup-fresh-machine.md) — empty box to first generated biography:
  Python deps, the git hooks, a narrative backend, the `rakaly` binary, the chronicle
  directory. Start here if `chronicler doctor` is complaining.
- [running-headless.md](running-headless.md) — running with no visible console, like a
  desktop app, watching the in-app Logs tab instead of a terminal.

**How it works**

- [architecture.md](architecture.md) — the map. What it covers, what reads the saves, what
  turns a recorded life into prose, and where the project deliberately stops.
- [biography-pipeline.md](biography-pipeline.md) — one death in CK3 followed all the way to a
  markdown biography on disk. The best single read for understanding the narrative half.
- [schema_versions.md](schema_versions.md) — the `debug.log` wire format and its schema
  versions. Needed when a CK3 patch moves something and ingest stops recognising events.

**Operating a chronicle**

- [patch-playbook.md](patch-playbook.md) — the 30-minute fix path after Paradox ships a CK3
  patch: what breaks (rakaly compatibility, heraldry assets, save-file keys) and the order to
  check it in. `chronicler doctor` is the triage signal.

- [archived-campaigns.md](archived-campaigns.md) — what sealing a campaign writes into the
  archive dir, and how to read it back.
- [version-history.md](version-history.md) — a changelog of what shipped per milestone, with
  the demo commands of the era. History, not a quickstart.

For working on the code, see [../CONTRIBUTING.md](../CONTRIBUTING.md). For the conventions
agents should follow, [../CLAUDE.md](../CLAUDE.md) and [../AGENTS.md](../AGENTS.md).

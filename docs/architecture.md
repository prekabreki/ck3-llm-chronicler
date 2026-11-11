# Architecture

How CK3 LLM Chronicler is put together: the module map, what it covers, what reads the save files, what turns a recorded life into prose, and where it deliberately stops.

## Module map

The code splits into three chains that meet at the per-campaign SQLite database.

**Ingest** — watcher → cache → parse pool → diff → tick → DB.

- `save/watcher.py` watches CK3's save directory and emits each new `.ck3` file.
- `save/cache.py` copies every fresh autosave into a per-campaign cache so rotation cannot drop one before it is parsed.
- `save/parse_worker.py` runs `rakaly` (`save/rakaly.py`) and `save/parse.py` in a process pool and returns a `SaveSnapshot`.
- `save/diff/` compares adjacent snapshots and emits the event variants defined in `schema/events.py`.
- `save/tick.py` is the snapshot-pair kernel: diff, advance the baseline, and write events when a pair is ingested.
- `save/ingest.py` is the long-running loop that drives the chain and hands deaths to the narrative scheduler.
- `db/` owns the SQLAlchemy models, per-campaign engine and session factory, registry, repositories and migrations; `ingest_common.py` is the write path shared with the legacy debug-log transport.

**Narrative** — scheduler → pipeline → prompt_builder/prose_io → runner → transport.

- `narrative/scheduler.py` turns a tracked character's death into a queued background task.
- `narrative/pipeline.py` reads the character and events into snapshots, builds the briefing, and persists the result through the shared runner.
- `narrative/prompt_builder.py` renders the user-side briefing; `narrative/prose_io.py` assembles the system prompt and owns the prose-repo artifact primitives.
- `narrative/runner.py` sequences version allocation, briefing and biography writes, the git commit and the DB insert around one transport call.
- `narrative/provider.py` is the transport ABC; `narrative/factory.py` and `narrative/backend_config.py` choose one of `claude_code.py`, `anthropic.py` or `openai_compatible.py` from settings then environment.
- `narrative/model_resolution.py` resolves the model and long-context policy shared by every transport.

**Serving** — orchestrator, api, cli.

- `orchestrator.py` runs the save-ingest loop and the FastAPI app together for `chronicler dev`.
- `api/` is the FastAPI app, SSE event bus, routes and serializers; `frontend/` is the React SPA it serves.
- `cli/` is the Typer command tree.

Supporting packages: `heraldry/` extracts real coats of arms from the game install, `export/` writes markdown and PDF bundles plus event rolls, `sync/` carries sealed campaigns between machines, `migrate/` detects and runs schema migrations with backups, and `smoke/` holds the patch-playbook smoke run.

## Scope

The project is public, open-source software.
Each user runs their own copy against their own saves and their own narrative backend.
That means:

- Cross-platform support: Linux, Windows and macOS.
- Any CK3 patch is handled reactively, with `chronicler doctor` and the patch playbook as the triage path.
- No requirement for a Claude subscription: the `anthropic` and `openai-compatible` transports exist so a user can choose their own provider.
- DLC support tracks what the save files expose; the parser plans for full Tours & Tournaments and Roads to Power coverage.
- The UI can be plain where plain is enough.

## Data source

Saves are the primary source: monthly autosaves are melted with `rakaly` to JSON, parsed into a `SaveSnapshot`, and diffed against the previous snapshot.
The optional `-debug_mode` mod in `mod/` still emits `[CHRONICLER]` lines to `debug.log` for sub-day timing, and `tailer/` reads them and reconciles with the save events.

## Stack

Python 3.11+, FastAPI and SQLite on the backend; React 19 and TypeScript built with Vite and served by the FastAPI app on the frontend; `rakaly` melts saves and `msgspec` carries snapshots across the parse pool.

## History

The project began as a v0.2 `-debug_mode` mod that stamped tagged lines into `debug.log`; the v0.6 pivot made save parsing the primary source and left the mod and `tailer/` as the optional rich-data path.
Early versions generated prose with a local Qwen model through Ollama behind a swappable provider interface.
The v0.10 `tbrm` pivot moved the default to `claude --print`, and 2026-08 brought backend selection back as three lean transports.
What stays dead is the ambition of that early abstraction: there is no `HybridProvider`, no per-kind provider and no capability-based routing.
The frontend moved from a placeholder to React + Vite at v0.3 and is served by the FastAPI process.

## Non-goals

- A hosted or multi-user service; each install is one user's chronicle.
- Real-time in-game UI overlay (the web app is the UI).
- Editing or modifying game state from the app.
- Compatibility with gameplay overhaul mods (Sunset Invasion, AGOT, etc.) beyond fixing a specific campaign if needed.
- Replacing CK3's own memory system in-game (we augment externally; vanilla memories still exist and we read them).
- Bundling Paradox assets; heraldry is read from the user's own game install.

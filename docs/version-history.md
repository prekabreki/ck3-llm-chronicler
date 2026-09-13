# Milestone history

Historical record of what shipped in each Chronicler milestone. This is a
changelog, not a quickstart — the demo commands below reflect each
milestone's state at the time it shipped and may reference flows that have
since changed or been removed. For the current way to run Chronicler, see
the [README](../README.md). For the project pitch and roadmap, see
[architecture.md](architecture.md).

> **Removed since:** the v0.5 "living memories" feature (LLM-generated
> interpretive notes on living characters) was removed wholesale in v0.12
> (plan `cozy-coalescing-shannon`). LLM work is now biography-only, written
> at death. Any `memories` API/UI references in older demos below no longer
> exist.

---

## v0.1 — done

Death events fire from CK3 → Python tailer → per-campaign SQLite DB within
seconds.

```bash
# One-time per machine
uv sync
uv run python scripts/install_mod.py
uv run python scripts/run_tiger.py     # lints the mod (requires ck3-tiger)

# Per session
uv run chronicler campaign create my-campaign
uv run chronicler tail --campaign my-campaign --no-biography

# In CK3 launched with -debug_mode, console: kill <character_id>
# Within seconds, the tailer prints "ingested death for character …"
uv run chronicler dump-character <character_id> --campaign my-campaign
```

The v0.1 regression fixture lives at
`tests/fixtures/debug_log_samples/1.19.0.4/death_william_normandy.log` and is
exercised by `tests/unit/test_real_fixture.py`.

## v0.2 — done

End-to-end biography generation pipeline (CK3 mod → debug.log → tailer →
BiographyScheduler → LLM → DB). The provider layer was rewired in `tbrm`
(2026-05-08); Ollama and the Anthropic API are no longer the runtime path —
biography generation now shells out to Claude Code. See
[docs/biography-pipeline.md](biography-pipeline.md).

### debug_log mode (optional rich-data)

For sessions where you want sub-day chronology and engine scope-dump context,
the debug_log mode can run alongside or instead of save-tail. Requires
`-debug_mode` (which adds CK3 UI clutter).

```bash
uv run python scripts/install_mod.py
uv run python scripts/run_tiger.py     # lints the mod

uv run chronicler campaign create my-debug-campaign
uv run chronicler track <player_id>  --campaign my-debug-campaign --note "player"
uv run chronicler tail --campaign my-debug-campaign
# Kill a tracked character: console -> kill <id>
uv run chronicler dump-biography <id> --campaign my-debug-campaign
```

## v0.3 — done

Web app over the same per-campaign DB. FastAPI + HTMX-driven Jinja2 pages
(later replaced by the React portal in v0.7). JSON API at `/api/...`;
OpenAPI docs at `/docs`. `chronicler dev` runs the save-tail loop and the web
server in one terminal.

## v0.5 — done, then removed in v0.12

Living memories: LLM-generated interpretive notes about tracked characters
that accumulated as save-state events rolled in. Removed wholesale by plan
`cozy-coalescing-shannon`; LLM work is now biography-only (written at death,
never on living characters).

## v0.6 — done

Save-parse architectural pivot: `chronicler save-tail` watches autosaves via
the `rakaly` CLI, no `-debug_mode` required. `chronicler auto-track` reads the
player + family from the save's metadata. `chronicler import-save` backfills
full historical context from the save's vanilla `memories` array.

```bash
# In CK3 settings: set autosave frequency to MONTHLY (Settings → Game)
# This is what gives v0.6 real-time-enough latency.

uv run chronicler campaign create my-campaign

# Auto-detect player + immediate family from the latest save:
uv run chronicler auto-track --campaign my-campaign

# (Optional) backfill the entire historical record:
uv run chronicler import-save \
    "C:/Users/<you>/Documents/Paradox Interactive/Crusader Kings III/save games/autosave.ck3" \
    --campaign my-campaign

# Start watching for new autosaves:
uv run chronicler save-tail --campaign my-campaign
```

A reference biography from the v0.6 pipeline (the player Eadmund Godwineson
after the V01-S04 inheritance arc) is committed at
`tests/fixtures/biographies/character_36892_eadmund_v06.md`.

## v0.7 — done

React/TypeScript portal replaces the Jinja+HTMX UI. Eight screens — Library,
Codex, Chronicle (centerpiece, with Vita/Events tabs), Stemma (SVG family
tree, click to refocus), Tracked, Save-tail (live SSE ingest stream), Search
(cross-campaign FTS5), Closing (campaign sealing ceremony), Settings (provider
status + cost dashboard). Real CK3 heraldry (extracted DDS textures +
named-color palette + SVG composition renderer) replaces procedural shields
where the character has a persisted CoA. Save-import via path picker + 4-stage
SSE progress modal.

Build: `cd frontend && npm install && npm run build` produces the SPA at
`src/chronicler/api/static/app/` and the FastAPI app serves it from `/`. The
legacy Jinja UI was removed — there is no fallback path; the SPA is the only
frontend.

## v0.12 — done

Living-memories feature removed (see v0.5 above); LLM work is biography-only.
The swappable narrative-provider abstraction (Ollama/Anthropic/Hybrid) was
removed — Claude Code was, at v0.12, the permanent narrative backend.
*(No longer current: backend selection returned in 2026 as three lean
transports — `claude-code`, `anthropic`, `openai-compatible` — while per-kind
routing stayed prohibited.)* See
[architecture.md](architecture.md) for how the system is put together.

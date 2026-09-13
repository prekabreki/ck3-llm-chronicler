# CLAUDE.md

Guidance for Claude Code and other coding agents working in this repository.

## What this is

CK3 LLM Chronicler reads Crusader Kings III save files and turns tracked characters' recorded
lives into biographies, written as markdown into a chronicle git repository the user owns.

Two halves meet in the middle. The **ingest** side melts saves to JSON, diffs each against the
previous one, and lands the resulting events in a per-campaign SQLite database. The **narrative**
side takes a dead character's whole recorded life, assembles it into a briefing, and hands that
to an LLM through one of three interchangeable transports. `docs/architecture.md` is the map;
`docs/biography-pipeline.md` follows a single biography from save file to finished prose.

## The rule that matters most

**The craft rules forbid invention.** If a fact is not in the briefing, the sentence does not get
written. When you touch prompt assembly, briefing construction, or anything that feeds the model,
assume a reader will check the output against the save. Adding a plausible detail the data does
not support is the one unforgivable bug in this codebase.

## Layout

| Path | What lives there |
|---|---|
| `src/chronicler/save/` | save melting and parsing, `rakaly` wrapper |
| `src/chronicler/ingest*` | diffing saves into events, the tailer |
| `src/chronicler/narrative/` | the three backends, prompt assembly, biography generation |
| `src/chronicler/cli/` | Typer commands; `_app.py` holds the root app to break an import cycle |
| `src/chronicler/heraldry/` | coat-of-arms extraction from the game install |
| `frontend/` | React 19 + TypeScript SPA, built and served by the FastAPI app |
| `prose-template/` | the chronicle scaffold `chronicler init-prose` copies out |

## Backends

Three lean transports, chosen by settings then environment: `claude-code` (the default, no API
key, the one the craft rules were tuned against), `anthropic`, and `openai-compatible` (OpenAI,
DeepSeek, OpenRouter, Ollama, LM Studio). They are pure transports. System-prompt assembly is
shared and lives above them, so a backend must never grow its own prompt logic.

## Commands

```bash
uv sync                                  # install, including dev deps
uv run pytest -q                         # the suite, ~2000 tests
uv run ruff check .                      # lint
uv run ruff format --check .             # formatting, a SEPARATE CI gate from the line above
cd frontend && npm ci && npm run build   # the SPA, served from /
cd frontend && npm test                  # vitest
cd frontend && npm run e2e               # playwright
```

Run `chronicler doctor` when something environmental looks wrong; it checks the install before
you go hunting in the code.

## Conventions

- Lint, formatting and tests all gate CI. `ruff check` and `ruff format --check` are two
  separate steps: running only the first and pushing is how this repo goes red.
- Tests live beside what they cover in `tests/unit`; anything touching a real save goes in
  `tests/integration`.
- Durable findings belong in `.memories/` as one fact per file, not in commit messages.
- The import package and the CLI verb are both `chronicler`. The distribution is
  `ck3-llm-chronicler`. Do not rename the internals to match the brand.

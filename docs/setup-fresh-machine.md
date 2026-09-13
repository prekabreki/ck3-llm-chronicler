# Setup on a fresh machine

This is the shortest path to a working chronicler on a brand-new Windows or Linux box. Audience: future-me coming back to this after months away, or a collaborator pulling the repo for the first time.

The chronicler is a personal tool that turns a Crusader Kings III campaign into an illuminated chronicle. It runs:

- a Python backend (FastAPI + save-tail watcher + narrative glue)
- a React frontend (Vite + TanStack Query)
- one of three **narrative backends** (`claude-code` by default, or the Anthropic API, or any OpenAI-compatible endpoint) writing into a **chronicle directory** you scaffold locally

Hard requirements beyond this repo: **a backend you can bill** (the `claude` CLI on PATH, or an API key), the **chronicle directory** scaffolded, and a **`rakaly` binary** for save parsing. Each is covered below.

## 1. Clone and install Python deps

```powershell
git clone https://github.com/prekabreki/ck3-llm-chronicler.git
cd ck3-llm-chronicler
uv sync
```

`uv sync` creates `.venv` and installs the project (editable) plus the `dev` dependency group (pytest, ruff — declared as a PEP 735 `[dependency-groups]`, **not** a `[dev]` extra, so `pip install -e ".[dev]"` would silently skip them). If you don't have uv: `pipx install uv` or follow [astral.sh/uv](https://docs.astral.sh/uv/).

Run the tool via the venv (`.venv\Scripts\chronicler` / `.venv\Scripts\activate`) — the package is **not** installed in the global Python.

### Optional: PDF chronicle export

The PDF export endpoint (`POST /export/pdf`) is gated behind the optional `pdf`
extra, because its dependency chain (`xhtml2pdf → svglib → rlpycairo → pycairo`)
builds `pycairo` from source on platforms without a wheel — which fails on a stock
box that lacks system **cairo** and **cmake**. The rest of the app runs without it;
the endpoint returns a clear `503` when the extra is absent (bpb8/knne).

To enable it:

```bash
# Linux: install the system build deps first
sudo dnf install cairo-devel cmake        # Fedora/Nobara
sudo apt install libcairo2-dev cmake      # Debian/Ubuntu

uv sync --extra pdf
```

On Windows/macOS `uv sync --extra pdf` usually works without system packages (pycairo ships wheels there).

## 2. Wire up the git hooks (one-time per clone)

```powershell
git config core.hooksPath .githooks
```

`hooksPath` is per-clone local config, not versioned, so every machine needs this once. The `pre-commit` hook regenerates `.memories/README.md` from each memory's frontmatter, so the index can never be committed stale. A Claude Code session self-heals this setting on start (see `tools/session-start-context.py`); plain-git users should run the line above by hand.

Work is tracked in **GitHub Issues** — `gh auth login` once per machine, then `python tools/issue-ready.py` shows ready work. (An earlier tracker was retired on 2026-07-28; GitHub Issues is the only one now.)

## 3. A narrative backend

Pick one. It is a process-wide choice, settable in Settings → Provider & LLM or by env var,
and `chronicler doctor` reports which one is live.

**`claude-code` (default).** Install [Claude Code](https://claude.com/claude-code) and log in
(`claude /login`) so it runs on **subscription auth**. The chronicler invokes `claude --print`
as a subprocess and deliberately strips `ANTHROPIC_API_KEY` (and sibling vars) from that
subprocess's env, so a stray key in your shell can't silently flip the CLI into pay-per-use
API billing (the e95t incident). Binary not literally named `claude`, or not on PATH? Set
`CHRONICLER_CLAUDE_CODE_BIN`.

**`anthropic`.** `CHRONICLER_NARRATIVE_BACKEND=anthropic` plus an `ANTHROPIC_API_KEY`
(or the key saved from the Settings screen). Metered per token.

**`openai-compatible`.** `CHRONICLER_NARRATIVE_BACKEND=openai-compatible` plus a preset
(`openai`, `deepseek`, `openrouter`, `ollama`, `lmstudio`) or a bare base URL, and a model
name — the endpoint decides which models exist, so there is no default. Presets prefill the
endpoint; the two local ones need no key.

## 4. Scaffold the chronicle directory

Generated chronicles live in a git repo of your own whose `CLAUDE.md` reshapes the model from
a coding assistant into a chronicler. Without it, generation refuses to start rather than
producing generic prose.

```powershell
chronicler init-prose
```

That creates `<data dir>/prose`, copies the bundled template (craft rules, per-kind voice
files, a worked example), git-inits it, and records the path in settings. Pass a path to put
it elsewhere, or set `CHRONICLER_PROSE_REPO_PATH` if you already keep one. Re-running is safe:
an existing chronicle directory is left exactly as it is, so edits to your own craft rules
survive. The Settings screen has the same button. Details: `docs/biography-pipeline.md`.

## 5. rakaly (save-file converter)

The save tail shells out to [rakaly](https://github.com/rakaly/cli) to melt `.ck3` saves to JSON. Either:

- put `rakaly` / `rakaly.exe` on PATH, or
- extract a release into the repo root as `rakaly-<version>/` — `find_rakaly()` picks up `rakaly-*/rakaly.exe` (flat or per-platform layout) automatically.

## 6. Extract heraldry assets (one-time)

```powershell
chronicler heraldry extract
```

Probes Steam's default install path for CK3, walks `gfx/coat_of_arms/`, converts `.dds` patterns + emblems to PNG, and writes `palette.json` to `~/Documents/chronicler/heraldry/`. Re-run after any CK3 patch that changes assets (doctor nudges you when the extract is stale).

If Steam isn't in the default location: `chronicler heraldry extract --ck3-dir "<path-to-CK3>"`.

## 7. Frontend deps

```powershell
cd frontend
npm install
npm run build   # tsc -b && vite build — emits into src/chronicler/api/static/app
```

## 8. Smoke check

```powershell
chronicler doctor
```

Expected shape (paths will differ):

```
  [OK]  CK3 install located           C:\...\Crusader Kings III
  [OK]  Narrative backend configured  claude-code via C:\...\claude.exe -- bio=claude-opus-5 (...)
  [OK]  Other backends available      anthropic: ready (key present) | openai-compatible: not ready
  [OK]  Heraldry palette extracted    ...\heraldry\palette.json
  [OK]  Heraldry up to date           last extracted 2026-06-01T...
  [!!]  Campaigns registered          no campaigns yet -- run `chronicler campaign create <name>` or import a save
```

The campaigns row stays red until you create or import one. "Heraldry up to date" is informational — a stale extract still renders valid shields.

> **Post-2026-06-15 billing note:** on the `claude-code` backend, generations bill the Claude subscription's metered programmatic-credit pool. The monthly credit must be claimed **once per seat** (an admin can't do it for you); billing errors from `claude --print` after that date usually mean the credit is unclaimed or exhausted. Switching to `anthropic` or `openai-compatible` is the escape hatch.

## 9. Run the dev stack

```powershell
chronicler dev --campaign <name>
```

This starts both save-tail and the FastAPI server. Open `http://127.0.0.1:8000` to see the chronicle. If you launch CK3 and play, autosaves drop into the save dir and the chronicler ingests them.

## What's NOT covered here

- **CK3 install on Steam Deck / non-standard locations.** Pass `--ck3-dir` to commands that need the install path.
- **Multi-machine sync.** The chronicler writes to `~/Documents/chronicler/` (campaigns, heraldry, save cache, chronicle directory). Sealed campaigns are snapshotted into the archive dir, which you sync with your own vehicle (`docs/archived-campaigns.md`); the chronicle directory syncs itself via git, since `init-prose` makes it a repo. Don't put the live data dir on OneDrive — an in-flight save-tail will fight with cloud-sync's atomic-rename pattern.
- **Headless / scheduled operation.** See `docs/running-headless.md`.

## When something feels wrong

```powershell
chronicler doctor   # is the environment intact?
```

If a probe fails, the detail line tells you which command to run. `claude` not on PATH? Install Claude Code, set `CHRONICLER_CLAUDE_CODE_BIN`, or switch backends. Chronicle directory missing? Run `chronicler init-prose`. Heraldry palette missing? Run `chronicler heraldry extract`. Campaigns missing? Create one.

For deeper issues, check `~/Documents/chronicler/logs/chronicler.log`.

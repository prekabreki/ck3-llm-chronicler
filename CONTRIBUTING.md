# Contributing

Everything needed to work on this repository lives in it. Start here, then
[`docs/`](docs/README.md) for depth.

## Set up

```bash
git clone https://github.com/prekabreki/ck3-llm-chronicler.git
cd ck3-llm-chronicler
uv sync                                  # .venv + the project + the dev group
git config core.hooksPath .githooks      # per-clone, not versioned, needed once
cd frontend && npm ci
```

`uv sync` installs the `dev` dependency group, which is declared as a PEP 735
`[dependency-groups]` and **not** a `[dev]` extra, so `pip install -e ".[dev]"` silently
skips it. The tool runs from the venv (`.venv/bin/chronicler`); it is not installed
globally.

You also need a **narrative backend** and a **`rakaly` binary** before the app does
anything interesting. [`docs/setup-fresh-machine.md`](docs/setup-fresh-machine.md) is the
full path from empty box to first generated biography, and `chronicler doctor` tells you
which piece is missing.

## The gates

CI runs these on **both Ubuntu and Windows**, and they are what a pull request has to pass.
Run them before pushing, in this order:

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run pytest -q                 # ~2000 tests
cd frontend
npm run lint                     # eslint
npm run build                    # tsc -b && vite build
npm test                         # vitest
```

Two traps worth knowing, both of which have cost real time here:

- **`ruff check` and `ruff format --check` are separate gates.** Running only the first
  and pushing is how this repo goes red. `uv run ruff format .` fixes the second.
- **`npm run build` is stricter than your editor.** It runs `tsc -b` across the test files
  too, so an unchecked index access in a test fails the build while `vitest` is green.

`npm run e2e` (Playwright) is not in CI; it boots a Vite dev server and exercises the shell.

## How work is tracked

GitHub Issues, via the `gh` CLI.

```bash
gh issue list --state open
python tools/issue-ready.py       # open, unassigned, not blocked by anything open
gh issue edit <N> --add-assignee @me
```

- **Labels:** `P0`-`P4` for priority, `bug` / `task` / `chore` / `epic` / `feature` for type,
  and `deferred` to park real backlog that is not actionable yet, which keeps it out of the
  ready view instead of being re-triaged every session.
- **Dependencies:** `Blocked by #N` (or `Blocked by: #N`), and it must **open its line**, after
  at most an indent and a list bullet. Both of those rules are scars. Matching the phrase
  anywhere on a line let a body that merely *described* the syntax mark itself blocked by the
  issues it was quoting, and it sat out of the ready view for two days. And only the leading run
  of refs counts, separated by `,` `;` `/` `&` `+` or `and`, so in

      Blocked by #12 (needs the client). Part of #15.

  only #12 is a blocker; the trailing #15 is prose. There is no `Blocks #N`: dependencies are
  declared from the blocked side only.

## Memories

`.memories/` holds durable project knowledge as one fact per file, committed and grep-able.
Write one when a fact cost real effort to learn and is not obvious from the code or the git
history. Each file carries YAML frontmatter with a one-line `description:`; the index in
`.memories/README.md` is generated, so never hand-edit it. The pre-commit hook regenerates
and stages it, or run `python tools/memory-index.py` yourself.

## The rule that outranks the rest

**The craft rules forbid invention.** If a fact is not in the briefing, the sentence does not
get written. When you touch prompt assembly, briefing construction, or anything that feeds the
model, assume a reader will check the output against their save file. A plausible detail the
data does not support is the one unforgivable bug in this codebase, because it quietly turns a
chronicle of what happened into fiction.

## Style

- Tests sit beside what they cover in `tests/unit`; anything that needs a real save file goes
  in `tests/integration` and skips with a declared reason when the fixture is absent.
- The import package and the CLI verb are both `chronicler`; the distribution is
  `ck3-llm-chronicler`. Do not rename the internals to match the brand.
- Conventional-ish commit subjects (`fix(save): …`). Say why in the body, not what: the diff
  already says what.

# Real-save golden fixtures

Drop one or more CK3 `.ck3` autosaves into this directory. The integration test
`tests/integration/test_real_save_golden.py` discovers them at runtime, runs
rakaly to produce JSON, and exercises `parse_save` + diff against the parsed
result.

Why this directory is gitignored: a typical mid-game autosave is 20-200MB
binary; the rakaly JSON output is 5-7x larger again. We can't commit those at
GitHub-friendly sizes. Each contributor drops in their own playthrough save
and the test runs locally against it.

## What CI does and does not cover

CI never runs any of this. It fetches no `rakaly` binary and tracks no `.ck3`,
so four tests are **local-only** — they execute on a developer machine that has
a save here, or nowhere:

- `tests/unit/test_parse_worker.py` — three tests needing `autosave_exit.ck3`
  specifically (the worker round-trip, the requested-ids filter, and the
  process-pool boundary).
- `tests/integration/test_real_save_golden.py` — needs any `*.ck3` here plus
  `rakaly` on PATH.

That gap is declared rather than silent (issue #41). `tests/conftest.py` holds
a table of every skip reason this suite may emit, split into by-design and
local-only, and prints a **skip ledger** naming each skipped test at the end of
every run. A skip whose reason is not in that table is reported as UNDECLARED,
and with `CHRONICLER_STRICT_SKIPS=1` — which CI sets — it fails the run. So a
green CI run means "the four local-only tests did not run, and nothing else was
missing", not "everything passed".

Run `pytest -q -rs` to see the ledger locally; drop a save here and the three
`test_parse_worker` tests move out of it.

Everything else that depends on build output *is* covered: CI's Python job
builds the frontend before pytest, so the SPA-serving tests in
`tests/unit/test_api_io.py` run there against real Vite output.

## Suggested sources

- Recent autosave from the CK3 save-games directory (Windows:
  `%USERPROFILE%\Documents\Paradox Interactive\Crusader Kings III\save games`).
- An exit autosave (`autosave_exit.ck3`) is usually smaller and faster to
  process than the regular autosave, while covering the same playthrough state.

The test caches the rakaly JSON output beside the `.ck3` as `<name>.rakaly.json`
and reuses it on subsequent runs (the JSON is also gitignored).

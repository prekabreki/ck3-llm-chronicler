# Biography pipeline

How a death in CK3 becomes a markdown biography on disk. Current as of v1.0 (issues #19,
#21, #45, #46); the historical note at the end records what this used to be, because a lot
of the shape only makes sense as a reaction to what came before.

---

## The two directories

```
<your clone>/                       this repo: save parser, DB, scheduler, API, frontend
   └─ generates against ↓

<chronicler data dir>/prose/        your chronicle directory (a git repo you own)
   ├─ CLAUDE.md                     the register: reshapes the model from "helpful
   │                                assistant" into "chronicler". Yours to edit.
   ├─ voice/biography.md            per-kind craft + length rules
   ├─ voice/biography-woven.md
   ├─ voice/chronicle-export.md
   ├─ briefings/<campaign-uuid>/<ck3_id>-vN.md    input, written by chronicler
   └─ biographies/<campaign-uuid>/<ck3_id>-vN.md  output, written by chronicler
```

Create it with `chronicler init-prose`, which copies the bundled `prose-template/`, runs
`git init`, and records the path in settings. The path resolves settings-first, then
`CHRONICLER_PROSE_REPO_PATH`, then the default above; Settings → Prose repo edits the
setting, and its readiness pips tell you which of the three checks (directory exists, git
initialised, `CLAUDE.md` present) are satisfied.

Two properties matter. It is a git repo, so every biography ever generated is preserved,
diffable across regenerations, and portable between machines. And it is plain markdown you
own: changing how your chronicle reads means editing `CLAUDE.md`, not a Python template.

A missing directory, a missing `CLAUDE.md`, or a missing voice file for the kind being
generated is a hard error naming both recovery paths (`init-prose`, or the env var). It is
deliberately not a fallback: generating register-less prose and filing it as a biography is
worse than generating nothing, and it used to happen silently.

## Three backends, one per process

`NarrativeProvider` has three production transports:

| Backend | Transport | Billing |
|---|---|---|
| `claude-code` (default) | `claude --print` subprocess | the subscription's monthly programmatic credit pool |
| `anthropic` | Messages API over httpx, streaming | `ANTHROPIC_API_KEY`, metered |
| `openai-compatible` | `POST /chat/completions`, non-streaming | whatever the endpoint bills; presets for OpenAI, DeepSeek, OpenRouter, Ollama, LM Studio |

Selection is process-wide: settings, then `CHRONICLER_NARRATIVE_BACKEND`, then the default.
The factory builds exactly one. There is no per-kind provider and no capability routing,
because cost attribution and the DB's `provider` tag (`<prefix>:<model>`) both assume one
provider per process, and because "which account gets billed" should never be a per-request
surprise.

The transports are pure in the sense that matters here: model choice is shared
(`narrative/model_resolution.py`), system-prompt assembly is shared
(`prose_io.assemble_system_prompt`), and the briefing wrapper is one constant
(`prose_io.BRIEFING_WRAPPER_INSTRUCTION`). Switching backends changes who is billed, not
how the prose reads.

For `claude-code`, chronicler strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
`CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX` from the subprocess environment. The
CLI silently switches itself to pay-per-use API mode when it sees those, which inverts the
entire point of that backend. This is not theoretical: on 2026-05-09 a key left exported in
a dev shell drained the owner's API credits through inherited env.

## End-to-end flow, live death event

1. **Save tail observes the death.** `chronicler.save.ingest.run_save_ingest` watches the
   autosave directory with `watchfiles`, melts each new save with `rakaly`, diffs it against
   the previous snapshot, and inserts the death event into the per-campaign DB.
   `tailer/ingest.py` then calls `scheduler.schedule(character_id)`.

2. **Scheduler enqueues.** `narrative.scheduler.NarrativeScheduler` honours the `is_tracked`
   filter, the paused and bumped flags, the global LLM pause, and a per-character lock so
   duplicate death events cannot race. Its fan-out width comes from the provider
   (`max_concurrent`), which is how a local model behind Ollama gets serialised while a
   cloud endpoint gets four in flight.

3. **Pipeline assembles the request.** `narrative.pipeline.generate_biography` opens a
   short-lived session, snapshots the character and events into detached dataclasses,
   builds the user prompt, calls `assemble_system_prompt(prose_repo=…, kind=…)` for the
   register plus that kind's voice file, and hands a `NarrativeRequest` to the provider. The
   session is never held open across the LLM call.

4. **Provider materialises the briefing.** Every transport does the same three things
   through `prose_io`: compute the next free `vN` by globbing existing biography files for
   that character, render the briefing markdown, and write
   `briefings/<campaign-uuid>/<ck3_id>-vN.md`. Briefing and biography share a version so
   they pair up on disk and in git.

5. **Provider calls the model.** The briefing goes in the user turn behind the shared
   wrapper instruction; the assembled register plus voice rules go in the system position.
   For `claude-code` that means:

   ```
   claude --print
          --model <resolved model>
          --output-format json
          --disallowedTools Bash,Edit,Write,Read,Glob,Grep,Task,WebFetch,…
          --system-prompt-file <temp file holding req.system_prompt>
   # the prompt itself arrives on stdin
   ```

   Three details are load-bearing. Tools are all denied, so nothing autoloads or indexes a
   repo (that growth is what marched the old agentic prompt toward the 1M cap) and the model
   cannot go agentic on its cwd. The cwd is a throwaway temp directory, which also holds the
   system-prompt file so one scope cleans up both. And the prompt goes over stdin rather
   than `-p`, because an event-rich character's inlined briefing overruns the Windows ~32KB
   command-line limit and fails with `WinError 206`.

6. **Provider persists.** The prose comes back in the response (the JSON envelope's `result`
   for `claude-code`), and the provider writes
   `biographies/<campaign-uuid>/<ck3_id>-vN.md` itself. The model writes no files at all.
   An attribution footer is appended to the file (`*Set down by <agent> · <model> · <in> in
   / <out> out*`) so a reader of the markdown sees what produced it; the in-memory body
   stays bare prose, so the DB column does not double-store the footer.

7. **Chronicler commits.** `prose_io.git_commit_biography` stages the briefing and biography
   pair and commits them, under a per-loop lock so concurrent generations cannot race the
   git index, with each git call bounded at 30 seconds so a stuck index lock cannot hang the
   pipeline forever. Auto-commit is in the tool, and the register tells the model not to
   commit, precisely so there is one owner of that step. It is best-effort: a git failure
   logs a warning and the biography stays on disk.

8. **Pipeline persists the row.** A fresh session inserts a `Biography` with the body,
   `provider="<prefix>:<model>"`, token counts, `events_through_event_id`, and `version=N`.
   It is now visible in the web UI and at `GET /api/campaigns/{name}/characters/{ck3_id}/biography`.

Manual regeneration (`chronicler regenerate-biography <ck3_id> --campaign <name>`, or the
Regenerate button) bypasses the scheduler's tracked-set and pause gates but runs the same
provider path, so it produces a `vN+1` artifact exactly like a live death does.

## What is in the chronicle directory

`CLAUDE.md` is written in opposition to the assistant defaults it has to override: a role
override ("this is a chronicle project, not a software project"), hard bans (no code, no
"Here is the biography" framing, no process narration), an anti-fabrication rule (every fact
traces to the briefing; if you would have to invent to make a sentence work, omit the
sentence), an opening-sentence rule (name the subject and an event; peer realms appear only
next to events that touch them), naming discipline, and the commit rule (the tool commits,
you do not).

`voice/<kind>.md` carries what differs per call kind: length bands and register for a plain
biography, the weaving rules for `biography_woven`, the synthesis rules for the campaign's
closing `chronicle_export`. Splitting these out of the register is what lets the static
prefix stay byte-identical across calls so prompt caching can hit it.

The template ships a worked example at `briefings/_smoke/synthetic-jarl-v1.md` plus its
`biographies/_smoke/` counterpart. Read them side by side to see what the rules produce,
then delete them.

## Briefing format

One self-contained markdown file, rendered by `render_briefing_markdown` in
`narrative/prose_io.py`:

```markdown
# Briefing: character <ck3_id> — kind=<biography|biography_woven|chronicle_export> (prompt_version=..., campaign=<uuid>)

Source of truth for this call. Every fact emitted must trace to this briefing. The
chronicler register and this kind's voice + length rules arrive separately, in the
system prompt.

---

Known names: <First> <Dynasty>, called <nickname-if-any>
Gender: <man|woman>
Birth date (if known): <date>
Death date (if known): <date>
Culture: <culture>; Faith: <faith>

Other characters referenced in this person's events (use these names, never the IDs):
  <id> = <name>, called <epithet>
  ...

World context (<region>, at the time of his/her death):
  ...

Recorded events (chronological):
  [<iso-date>] <event_type> — <human-readable description>; payload=<json>
  ...
```

## Configuration

| Setting | Default | Where set |
|---|---|---|
| Backend | `claude-code` | Settings → Provider & LLM, `narrative_backend` setting, or `CHRONICLER_NARRATIVE_BACKEND`. |
| Chronicle directory | `<data dir>/prose` | Settings → Prose repo, `prose_repo_path` setting, or `CHRONICLER_PROSE_REPO_PATH`. Created by `chronicler init-prose`. |
| Model, all kinds | per-kind defaults | Settings → The engine, `narrative_model` setting, or `CHRONICLER_NARRATIVE_MODEL`. |
| Model, per kind | Opus-tier | `narrative_bio_model` / `narrative_closing_model`, or `CHRONICLER_NARRATIVE_{BIO,CLOSING}_MODEL`. |
| `claude` binary | `claude` from PATH | `CHRONICLER_CLAUDE_CODE_BIN`. |
| Anthropic key | none | Settings → Provider & LLM, `anthropic_api_key` setting, or `ANTHROPIC_API_KEY`. |
| OpenAI-compatible target | none | Settings → Provider & LLM, or `CHRONICLER_OPENAI_{PRESET,BASE_URL,MODEL,API_KEY}`. |
| Request timeout | 600 s | per-transport module constant. |

Settings beat environment variables, deliberately: a backend chosen in the UI must not
revert on the next start because of a stale `export` in a shell profile.

## Cost

`chronicler doctor` names which backend is live and what it bills. `claude-code` draws on
the subscription's metered programmatic-credit pool, which since 2026-06-15 must be claimed
once per seat (an admin cannot claim it for you); billing errors from `claude --print` after
that date usually mean the credit is unclaimed or exhausted. The `anthropic` and
`openai-compatible` backends are metered per token against whatever key you supplied.

Per-generation token counts are persisted on the `Biography` row and aggregated by
`/api/campaigns/{name}/cost-summary`, which the Settings cost dashboard reads. Dollar
attribution goes through the per-provider rate table in `chronicler/cost.py`, keyed on the
tag prefix. An unpresetted OpenAI-compatible endpoint has no rate-card entry on purpose:
an unknown paid model should read as uncosted, never as free.

## Failure modes

Everything below surfaces through `pipeline.generate_biography`'s exception net, which logs
a warning and returns `GenerationOutcome(error=…)`. Generation failing never blocks ingest.

- **Chronicle directory missing, or no `CLAUDE.md`, or no voice file for the kind.**
  `RuntimeError` naming `chronicler init-prose` and the env var.
- **`claude` not on PATH.** `FileNotFoundError` from the subprocess. Set
  `CHRONICLER_CLAUDE_CODE_BIN` if the binary is named something else.
- **Subprocess non-zero exit.** `RuntimeError("claude --print exited <code>: <stderr tail>")`.
- **Empty result.** `RuntimeError` when the envelope carries no prose, or the API transports
  return no text. Nothing is written and no row is inserted, so a retry produces the same
  `vN` rather than a gap in the version sequence.
- **Timeout.** 600 s wall-clock per generation including retries, then the process is killed.
- **Git failure on commit.** Warning only; the files are on disk and can be committed by hand.

## Historical note

Pre-`tbrm` (v0.2 through v0.9) this was an in-process affair: an `OllamaProvider` running
`qwen3:14b` locally for plain biographies, an `AnthropicProvider` for woven biographies and
exports, and a `HybridProvider` routing between them by request kind. There was also a
periodic "interpretive memories" pipeline. The local model lost on prose quality, the
routing made cost attribution ambiguous, and the memories pipeline was demolished in v0.12.

The `tbrm` pivot (2026-05-08) replaced all of it with Claude Code shelling out against the
prose repo, and for a while that shell-out was *agentic*: `--permission-mode acceptEdits`,
cwd set to the prose repo so it would autoload `CLAUDE.md`, an `@`-mention to read the
briefing, and an instruction to write the biography file itself. That worked and was
expensive. The full Code-agent context (its own system prompt, tool definitions, whatever
the repo autoloaded) grew as the prose repo accumulated. `l7rk` replaced it with the scoped
completion described above and measured 7.8x fewer input tokens on the same character
(227,835 to 29,145) with no prose-quality regression. `27ov.14` deleted the agentic path
entirely, and in doing so accidentally left the claude-code transport sending `CLAUDE.md`
alone, so every woven biography for a while ran without its voice file; issue #19 fixed that
by moving assembly out of the transports, which is why step 3 above builds the system prompt
and no transport reads an instruction file.

The provider abstraction itself was declared permanently closed in `ihkv` ("Claude Code is
the permanent narrative backend, do not re-introduce provider swapping"). Anthropic's
2026-06-15 programmatic-credit split forced the direct-API fallback back in (`cs1o`), and
the prohibition was retired outright on 2026-08-12 for the public release: a user with no
Claude subscription has to be able to run this at all. What stayed prohibited is the part
that caused the original trouble, per-kind providers and capability routing.

## Reference

- Transports: `src/chronicler/narrative/{claude_code,anthropic,openai_compatible}.py`
- Shared artifact I/O and prompt assembly: `src/chronicler/narrative/prose_io.py`
- Backend selection: `src/chronicler/narrative/{backend_config,factory}.py`
- Model resolution: `src/chronicler/narrative/model_resolution.py`
- Scaffold: `src/chronicler/narrative/prose_scaffold.py`, template at `prose-template/`
- Bundled chronicle template README: `prose-template/README.md`

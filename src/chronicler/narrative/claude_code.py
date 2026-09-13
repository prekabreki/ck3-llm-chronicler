"""ClaudeCodeProvider — biographies via the Claude Code CLI in headless mode.

ck3_chronicler-tbrm: pivot from in-process LLM providers (Ollama HTTP +
Anthropic Messages API) to ``claude --print`` shelled out with the
sibling content repo at ``CHRONICLER_PROSE_REPO_PATH`` (default
``C:\\git\\ck3_chronicler_prose``) as the system of record. The prose
repo holds a tight ``CLAUDE.md`` that reshapes Claude Code's defaults
from a software-engineering assistant into a chronicler. Every
biography ever generated is written to disk under
``biographies/<campaign>/<ck3_id>-vN.md`` and git-committed by the
prose-repo workflow — the repo itself becomes the versioned
cross-machine record.

The role-reshape lives in ``CLAUDE.md`` at the prose-dir root plus the
per-kind ``voice/<kind>.md`` rules. Issue #19: those are assembled
provider-neutrally into ``NarrativeRequest.system_prompt`` before the
request reaches any transport; this one writes that text to a per-call
temp file and passes it as ``--system-prompt-file``. It reads no
prose-repo instruction files itself, so it cannot drift from the API
transports the way it did after ck3_chronicler-27ov.14 (which left it
sending CLAUDE.md alone — every woven biography generated without
``voice/biography-woven.md``).

Flow (the ck3_chronicler-l7rk lean shape — the only shape since
ck3_chronicler-27ov.14 deleted the legacy agentic fallback):
1. Render the briefing markdown from ``NarrativeRequest`` into
   ``briefings/<campaign>/<char_id>-v<N>.md`` inside the prose repo.
   Version is computed by globbing existing biography files for that
   character — first call writes ``-v1``, next ``-v2``, etc.
2. ``asyncio.create_subprocess_exec("claude", "--print", ...)`` as a
   scoped *completion*: the briefing piped inline via stdin, the
   assembled register + voice rules via ``--system-prompt-file``, tools
   disallowed, cwd a neutral throwaway temp dir (which also holds that
   file, so one scope cleans up both).
3. Pull the prose from the JSON envelope's ``result`` on stdout, write
   ``biographies/<campaign>/<char_id>-v<N>.md`` ourselves, parse the
   envelope for token counts + ``total_cost_usd``, and git-commit the
   briefing + biography pair.

Failure modes that surface through ``NarrativeProvider.generate``:
- ``claude`` not on PATH → :class:`FileNotFoundError`.
- Subprocess non-zero exit → :class:`RuntimeError`.
- Envelope carries no prose in ``result`` → :class:`RuntimeError`.

The pipeline catches all of these via its existing exception net
(``pipeline.generate_biography``'s try/except) so a failure logs WARN
and returns ``GenerationOutcome(error=...)`` — biography generation
never blocks ingest, same contract as the old providers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

from chronicler.narrative import model_resolution
from chronicler.narrative.model_resolution import (
    LongContextPolicy,
    apply_long_context_bracket,
    estimate_prompt_tokens,
    resolved_models,
)
from chronicler.narrative.model_resolution import (
    model_for_kind as _model_for_kind,
)
from chronicler.narrative.prose_io import (
    BRIEFING_WRAPPER_INSTRUCTION,
    _missing_prose_message,
    render_briefing_markdown,
)
from chronicler.narrative.prose_io import (
    briefing_paths as _briefing_paths,
)
from chronicler.narrative.prose_io import (
    format_attribution_footer as _format_attribution_footer,
)
from chronicler.narrative.prose_io import (
    get_prose_repo_path as _get_prose_repo_path,
)
from chronicler.narrative.prose_io import (
    git_commit_biography as _git_commit_biography,
)
from chronicler.narrative.prose_io import (
    subprocess_creationflags as _subprocess_creationflags,
)
from chronicler.narrative.prose_io import (
    subprocess_startupinfo as _subprocess_startupinfo,
)
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
    PromptKind,
)

log = logging.getLogger(__name__)

CHRONICLER_PROSE_REPO_PATH_ENV = "CHRONICLER_PROSE_REPO_PATH"
# ck3_chronicler-cs1o: model resolution moved to the backend-neutral
# narrative.model_resolution module (shared with the anthropic
# transport). The legacy env-var name constants below are re-exported
# for back-compat with tests/docs that import them from here.
CHRONICLER_CLAUDE_CODE_MODEL_ENV = model_resolution.LEGACY_MODEL_ENV
CHRONICLER_CLAUDE_CODE_BIN_ENV = "CHRONICLER_CLAUDE_CODE_BIN"
CHRONICLER_CLAUDE_CODE_BIO_MODEL_ENV = model_resolution.LEGACY_BIO_MODEL_ENV
CHRONICLER_CLAUDE_CODE_CLOSING_MODEL_ENV = model_resolution.LEGACY_CLOSING_MODEL_ENV

# cs1o: base default is Opus with NO [1m] bracket — the 1M-context
# variant carries a 2x input surcharge and only ~4% of real calls need
# the window. apply_long_context_bracket() appends [1m] per call
# when the estimated prompt exceeds the 180k threshold.
DEFAULT_CLAUDE_CODE_MODEL = model_resolution.DEFAULT_MODEL
DEFAULT_BIO_MODEL = model_resolution.DEFAULT_BIO_MODEL
DEFAULT_CLOSING_MODEL = model_resolution.DEFAULT_CLOSING_MODEL
DEFAULT_CLAUDE_CODE_BIN = "claude"
# Issue #20: a third, unreferenced copy of the hard-coded
# C:/git/ck3_chronicler_prose default lived here. Nothing read it — but
# leaving it invites the next caller to, which is how config.py and
# prose_io.py came to disagree on Linux in the first place. The single
# resolver is config.resolve_prose_repo_path / default_prose_repo_path.

# 10-minute ceiling. Opus on a long campaign (200+ events) plus the
# read+write tool calls can take 4-6 minutes. 600s gives ~50% headroom
# without making a hung CLI invisible for the rest of the play session.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0


def _get_claude_code_bin() -> str:
    raw = os.environ.get(CHRONICLER_CLAUDE_CODE_BIN_ENV)
    if raw and raw.strip():
        return raw.strip()
    return DEFAULT_CLAUDE_CODE_BIN


# ck3_chronicler-l7rk: lean provider. generate() invokes the claude CLI as a
# scoped *completion* — same inputs as the original agentic path (briefing +
# the prose-repo CLAUDE.md register), but delivered inline + via
# --system-prompt-file, with tools disallowed and a neutral cwd, and the
# prose read back from the JSON ``result``. This drops the per-call prompt
# from the full Code-agent context (which grew toward the 1M cap as the
# prose repo accumulated) to a flat ~30k. Live-verified at 7.8x fewer input
# tokens with no prose-quality regression (Harold 31175: 227,835 -> 29,145).
# ck3_chronicler-27ov.14 removed the legacy agentic fallback
# (CHRONICLER_LEAN_PROVIDER=0) entirely — lean is the only path now.
#
# Tools to deny so their definitions don't inflate the prompt and the model
# can't go agentic on the (neutral, empty) cwd. The model only needs to emit
# prose; it reads nothing and writes nothing.
_LEAN_DISALLOWED_TOOLS = (
    "Bash,Edit,Write,Read,Glob,Grep,Task,WebFetch,WebSearch,"
    "NotebookEdit,TodoWrite,BashOutput,KillShell,SlashCommand"
)


# ck3_chronicler-tbrm pivot intent: chronicler runs claude --print so
# the user's Claude Code subscription handles the inference and we do
# not bill the Anthropic Messages API per generation. The Claude Code
# CLI honours ``ANTHROPIC_API_KEY`` (and a few sibling vars) when those
# are present in its env, switching itself silently into pay-per-use
# API mode — which inverts the entire pivot. We strip those vars from
# the subprocess env so the CLI uses whichever subscription auth the
# user has configured (``claude /login`` → Pro/Max). Live signal:
# 2026-05-09 e95t — user's API credits exhausted because their personal
# ``ANTHROPIC_API_KEY`` was set in the dev shell and the chronicler
# subprocess inherited it, billing the API on every memory + biography
# call despite tbrm's pivot.
_API_MODE_ENV_VARS_TO_STRIP: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)


def _build_subprocess_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` with API-mode trigger vars
    removed so the spawned ``claude --print`` uses subscription auth.

    Any of :data:`_API_MODE_ENV_VARS_TO_STRIP` present in the parent
    env is dropped from the child env. Everything else (PATH, locale,
    HOME, AppData, etc.) is preserved verbatim because the CLI needs
    them — particularly PATH, since claude's npm installer sometimes
    shells out to node and git on its own.

    ck3_chronicler-iaf0: force UTF-8 on the subprocess stdio /
    file-write paths so high-Unicode characters (em-dashes, accented
    glyphs) survive on Windows. Without this the CLI inherits the
    Windows default (cp1252) and biography files written by the
    subprocess get mojibake'd — chronicler reads them back as UTF-8 and
    persists the double-encoded bytes (live signal 2026-05-10 smoke).
    PYTHONUTF8=1 is belt-and-braces in case the CLI uses Python on the
    win32 platform default code page path.
    """
    env = {k: v for k, v in os.environ.items() if k not in _API_MODE_ENV_VARS_TO_STRIP}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _parse_json_envelope(stdout: bytes) -> dict | None:
    """Extract the trailing JSON envelope from ``claude --print
    --output-format json`` stdout.

    The CLI emits a single JSON object on stdout when ``--output-format
    json`` is set, but defends against a leading text payload by
    finding the LAST top-level ``{...}`` block. Returns None when the
    payload isn't parseable — the provider treats that as "no token
    counts available", same shape as the existing providers when a
    response omits the ``usage`` block.
    """
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    # Fast path: stdout IS the JSON envelope.
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass
    # Fallback: walk back to find the last balanced JSON object.
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                try:
                    loaded = json.loads(text[i : end + 1])
                    if isinstance(loaded, dict):
                        return loaded
                except json.JSONDecodeError:
                    return None
    return None


class _EnvelopeUsage(NamedTuple):
    """Token/cost fields extracted from the CLI's JSON envelope plus the
    model the CLI actually billed against (``model_actual``)."""

    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    model_actual: str


def _usage_from_envelope(
    envelope: dict | None,
    *,
    requested_model: str,
    kind: str,
) -> _EnvelopeUsage:
    """Extract usage / cost / model attribution from the JSON envelope.

    When the envelope is missing or unparseable every token field is
    None and ``model_actual`` falls back to ``requested_model`` — same
    shape as the old providers when a response omits the ``usage``
    block. When an envelope IS present this also emits the 4hfu
    per-model breakdown log line and the 591u ``cache_hit``
    observability line.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    model_actual = requested_model
    if envelope is not None:
        # Schema observed in 2026-05-08 smoke (claude-opus-4-7[1m]):
        # ``usage`` carries ``input_tokens`` (marginal, post-cache),
        # ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
        # and ``output_tokens``. ``total_cost_usd`` is at the envelope
        # TOP level, not under ``usage``. The chronicler's existing
        # NarrativeResponse contract has only input/output token
        # fields, so we sum the prompt-side cache figures into
        # ``input_tokens`` to keep the cost dashboard's per-token
        # math close to reality. (Cache reads are billed at a
        # discount; this slight over-count is a known approximation
        # — Claude Code subscription users don't pay per-call
        # anyway, the dashboard is informational.)
        usage = envelope.get("usage") or {}
        in_marginal = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        cache_create = usage.get("cache_creation_input_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        out_t = usage.get("output_tokens") or usage.get("completion_tokens")
        if any(isinstance(v, int) for v in (in_marginal, cache_create, cache_read)):
            total_in = (
                (in_marginal if isinstance(in_marginal, int) else 0)
                + (cache_create if isinstance(cache_create, int) else 0)
                + (cache_read if isinstance(cache_read, int) else 0)
            )
            input_tokens = total_in if total_in > 0 else None
        output_tokens = out_t if isinstance(out_t, int) else None
        # ck3_chronicler-cs1o: preserve the cache breakdown (subsets
        # of the summed input_tokens) + the CLI's own cost figure so
        # the cost layer can bill each bucket at its real rate. The
        # envelope's total_cost_usd is Anthropic's own number for
        # what this call cost the programmatic credit pool — the
        # closest ground truth for pool-burn metering.
        cache_write_tokens = (
            cache_create if isinstance(cache_create, int) and cache_create > 0 else None
        )
        cache_read_tokens = cache_read if isinstance(cache_read, int) and cache_read > 0 else None
        raw_cost = envelope.get("total_cost_usd")
        cost_usd = float(raw_cost) if isinstance(raw_cost, (int, float)) else None
        # The envelope's modelUsage dict's keys ARE the model IDs the
        # CLI actually billed against. Prefer that over ``model`` at
        # the top level (which doesn't exist in the observed schema).
        model_usage = envelope.get("modelUsage") or {}
        if isinstance(model_usage, dict) and model_usage:
            first_key = next(iter(model_usage), None)
            if isinstance(first_key, str) and first_key:
                model_actual = first_key

        # ck3_chronicler-4hfu (Phase 0 baseline): log the full per-model
        # breakdown so we can audit how much input attributed to each
        # model and validate that --model is being honoured. Without
        # this, the only visible attribution path is the persisted
        # provider tag, which Phase 1 will show is currently a lie.
        log.info(
            "claude_code: envelope kind=%s requested=%s actual=%s "
            "usage=%s modelUsage=%s total_cost_usd=%s",
            kind,
            requested_model,
            model_actual,
            envelope.get("usage"),
            model_usage,
            envelope.get("total_cost_usd"),
        )

        # ck3_chronicler-591u: cache-hit observability. Computes the
        # share of cacheable prompt tokens served from the prompt
        # cache — post-warmup (i.e. after the first call against a
        # given briefing's static prefix) this should approach 1.0;
        # if it stays near 0 the slice-3 cache restructure isn't
        # producing the prefix-stability the cache needs and we're
        # paying full input price every call. Distinct log line so
        # ``grep cache_read_ratio`` over a session's log filters to
        # exactly these numbers without grepping through the broader
        # envelope summary. NB: ratio uses the cacheable-only
        # denominator (read + create) — when cacheable=0 (no @-refs
        # in the prompt, e.g. an internal smoke call) ratio is 0
        # by convention; the surrounding raw counts make that case
        # unambiguous.
        cacheable = (cache_create or 0) + (cache_read or 0)
        cache_read_ratio = (cache_read / cacheable) if cacheable > 0 else 0.0
        log.info(
            "claude_code: cache_hit kind=%s actual=%s "
            "cache_read=%d cache_create=%d input_marginal=%d "
            "cache_read_ratio=%.3f",
            kind,
            model_actual,
            cache_read or 0,
            cache_create or 0,
            in_marginal or 0,
            cache_read_ratio,
        )

    return _EnvelopeUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
        model_actual=model_actual,
    )


class ClaudeCodeProvider(NarrativeProvider):
    """Shells out to ``claude --print`` as a scoped completion; the
    prose repo supplies the CLAUDE.md register and receives the on-disk
    briefing + biography artifacts.

    Constructed once per chronicler process. ``generate`` is safe to
    call concurrently — each call materialises its own briefing file
    with a unique version number, and ``asyncio.create_subprocess_exec``
    runs each subprocess independently. The pipeline serialises calls
    via the scheduler's per-character lock + single-lane semaphore in
    practice.
    """

    def __init__(
        self,
        *,
        prose_repo_path: Path | None = None,
        model: str | None = None,
        claude_bin: str | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._prose_repo = prose_repo_path or _get_prose_repo_path()
        # ck3_chronicler-5d9o: ``model`` kwarg is an explicit
        # "use this for ALL kinds" override, mirroring the precedence of
        # CHRONICLER_CLAUDE_CODE_MODEL. When None, each ``generate()`` call
        # resolves per-kind via ``_model_for_kind``.
        self._model_override = model
        self._bin = claude_bin or _get_claude_code_bin()
        self._timeout = request_timeout

    def _resolve_model(self, kind: str) -> str:
        if self._model_override:
            return self._model_override
        return _model_for_kind(kind)

    @property
    def name(self) -> str:
        # Biography-anchored tag. Kept for back-compat with display call
        # sites (CLI echo, settings route) and the cost dashboard's
        # provider-key bucketing. Persistence call sites should call
        # :meth:`name_for_kind` for accurate attribution.
        # See ck3_chronicler-ju7j.
        return f"claude-code:{self._resolve_model('biography')}"

    def name_for_kind(self, kind: PromptKind) -> str:
        # ck3_chronicler-ju7j (Phase 1): kind-aware provider tag so the
        # DB stores the model that actually ran. An explicit
        # ``_model_override`` shadows all kinds.
        return f"claude-code:{self._resolve_model(kind)}"

    def resolved_models(self) -> dict[str, str | None]:
        """Per-kind resolved-model snapshot for this provider instance.

        Honours the constructor's ``model`` kwarg (overrides every kind).
        When no kwarg is set, delegates to the module-level
        :func:`resolved_models` which reads env + per-kind defaults.
        """
        if self._model_override:
            return {
                "biography": self._model_override,
                "closing": self._model_override,
                "global_override": self._model_override,
            }
        return resolved_models()

    @property
    def prose_repo_path(self) -> Path:
        return self._prose_repo

    @property
    def long_context_policy(self) -> LongContextPolicy:
        """Issue #46: this transport asks for the 1M window by appending
        ``[1m]`` to the model tag, which is what the ``claude`` CLI
        understands. Never the Anthropic beta header — there is no header
        to set on a subprocess."""
        return LongContextPolicy.MODEL_BRACKET

    def _build_argv_and_stdin(
        self, *, model: str, briefing_text: str, system_prompt_file: Path
    ) -> tuple[list[str], bytes]:
        """Assemble the ``claude --print`` argv and the stdin prompt bytes.

        ck3_chronicler-l7rk: scoped completion — the briefing inline in
        the prompt, the chronicler register via --system-prompt-file, no
        tools, so nothing autoloads/indexes the repo (the growth that
        marched the prompt toward the 1M cap). Prose returns on stdout.

        Issue #19: ``system_prompt_file`` holds ``req.system_prompt`` —
        the shared assembly's register + voice output — written by
        :meth:`generate`. This used to point straight at the prose
        repo's ``CLAUDE.md``, which is why the voice file never reached
        the model on this transport.
        """
        # Issue #21: one shared constant across all three transports, so
        # the output contract can't drift per backend.
        prompt = BRIEFING_WRAPPER_INSTRUCTION + briefing_text
        argv = [
            self._bin,
            "--print",
            "--model",
            model,
            "--output-format",
            "json",
            "--disallowedTools",
            _LEAN_DISALLOWED_TOOLS,
            # The role-reshape register + voice rules as the system
            # prompt (replaces Claude Code's default ~45k system + tool
            # scaffolding).
            "--system-prompt-file",
            str(system_prompt_file),
        ]
        # ck3_chronicler-wpx4: pipe the (potentially huge) inlined-briefing
        # prompt via stdin rather than a -p CLI arg — the inlined briefing
        # for event-rich characters (and the closing chronicle) overruns
        # the Windows ~32KB command-line limit and fails with WinError 206.
        # `claude --print` reads the prompt from stdin when none is given
        # on the command line; stdin has no such ceiling.
        return argv, prompt.encode("utf-8")

    async def _run_subprocess(
        self,
        *,
        argv: list[str],
        stdin_input: bytes,
        cwd: str,
        character_id: str,
        campaign_uuid: str,
    ) -> bytes:
        """Spawn ``claude --print``, pipe the prompt via stdin, and
        return raw stdout once the CLI exits 0.

        Owns the process lifecycle: the request timeout (kill +
        RuntimeError), the cancel path (kill + re-raise,
        ck3_chronicler-c5wq), and non-zero exit surfacing with the e95t
        stdout-envelope error detail. ``cwd`` is the caller's throwaway
        temp dir (see :meth:`generate`), which also holds the
        system-prompt file — the caller owns its removal.
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_subprocess_env(),
            # ck3_chronicler-gcvm: keep grandchildren on a hidden console so
            # they can't leak status strings into ours. See
            # _subprocess_creationflags.
            creationflags=_subprocess_creationflags(),
            startupinfo=_subprocess_startupinfo(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                # ck3_chronicler-wpx4: feed the prompt via stdin.
                proc.communicate(input=stdin_input),
                timeout=self._timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"claude --print timed out after {self._timeout}s for "
                f"character_id={character_id} campaign={campaign_uuid}"
            ) from None
        except asyncio.CancelledError:
            # ck3_chronicler-c5wq: queue-page Cancel button asked the
            # scheduler to abort this task. Kill the subprocess so it
            # doesn't keep streaming tokens after we've stopped caring,
            # then re-raise — the scheduler's outer CancelledError
            # handler will mark the queue item failed("cancelled").
            # claude --print writes only via stdout capture, so there
            # is no partial-file cleanup needed on the prose-repo side.
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise

        if proc.returncode != 0:
            # ck3_chronicler-e95t: claude --print emits structured API
            # errors (credit exhaustion, rate limits, request validation
            # failures) on STDOUT as a JSON envelope with
            # ``is_error=true``, while stderr stays empty. Capturing
            # only stderr_tail produced a useless ``exited 1:`` message
            # in the queue UI when those errors fired. Surface the
            # envelope's ``result`` + ``api_error_status`` so the queue
            # UI tells the user what actually went wrong (e.g. "Credit
            # balance is too low (status 400)").
            stderr_tail = stderr.decode("utf-8", errors="replace")[-2000:]
            error_detail = stderr_tail
            envelope_err = _parse_json_envelope(stdout)
            if envelope_err is not None and envelope_err.get("is_error"):
                envelope_msg = envelope_err.get("result")
                envelope_status = envelope_err.get("api_error_status")
                if isinstance(envelope_msg, str) and envelope_msg:
                    status_part = (
                        f" (api_error_status={envelope_status})" if envelope_status else ""
                    )
                    # Prefer the envelope message; append stderr_tail
                    # only when it's non-empty so we don't add a
                    # trailing ': '.
                    error_detail = f"{envelope_msg}{status_part}"
                    if stderr_tail.strip():
                        error_detail = f"{error_detail}; stderr={stderr_tail}"
            raise RuntimeError(f"claude --print exited {proc.returncode}: {error_detail}")

        return stdout

    async def _persist_artifacts(
        self,
        *,
        bio_path: Path,
        body: str,
        rel_brief: str,
        rel_bio: str,
        character_id: str,
        campaign_uuid: str,
        version: int,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Write the biography file, append the attribution footer, and
        git-commit the briefing + biography pair."""
        # ck3_chronicler-l7rk: the model wrote nothing (no tools), so the
        # provider persists the on-disk artifact itself — parity with the
        # original agentic path's on-disk artifact + git commit.
        bio_path.write_text(body + "\n", encoding="utf-8")

        # ck3_chronicler-vcv2-followup (2026-05-08): append a short
        # attribution footer to the biography file so the model + token
        # counts are visible alongside the prose on disk + in git diffs
        # without needing the SPA. The DB row carries the same token
        # numbers in prompt_tokens / completion_tokens, but those aren't
        # surfaced anywhere a user reading the prose can see them. Body
        # in memory (returned in NarrativeResponse) stays bare prose so
        # the persisted Biography.body column doesn't double-store the
        # footer.
        footer = _format_attribution_footer(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        try:
            with bio_path.open("a", encoding="utf-8") as fh:
                fh.write(footer)
        except OSError:
            log.exception(
                "claude_code: failed to append attribution footer to %s; "
                "biography body is unaffected, just the on-disk metadata trail",
                bio_path,
            )

        # ck3_chronicler-cj2f: stage + commit the briefing + biography
        # pair so each generation lands as a discrete versioned artifact.
        # The prose-repo CLAUDE.md tells Claude Code NOT to commit
        # itself ("the chronicler tool commits"); honouring that is
        # this provider's responsibility. Best-effort — git failures
        # log a warning and are swallowed by _git_commit_biography.
        await _git_commit_biography(
            prose_repo=self._prose_repo,
            rel_brief=rel_brief,
            rel_bio=rel_bio,
            character_id=character_id,
            campaign_uuid=campaign_uuid,
            version=version,
            model=model,
        )

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        if not self._prose_repo.is_dir():
            raise RuntimeError(
                _missing_prose_message(f"prose repo not found at {self._prose_repo}")
            )
        # Issue #19: this transport ships req.system_prompt verbatim and
        # derives nothing itself. An empty one would run the CLI with no
        # chronicler register at all and persist generic-assistant prose
        # as a biography — the silent failure this issue removes.
        if not req.system_prompt.strip():
            raise RuntimeError(
                "refusing to invoke claude --print with an empty system prompt: "
                "the chronicler register must be assembled from the prose dir "
                "before the request reaches a transport (see "
                "chronicler.narrative.prose_io.assemble_system_prompt)"
            )

        metadata = req.metadata or {}
        campaign_uuid = metadata.get("campaign_uuid") or "_unscoped"
        character_id = metadata.get("character_id") or "_unknown"

        brief_path, bio_path, version = _briefing_paths(
            prose_repo=self._prose_repo,
            campaign_uuid=campaign_uuid,
            character_id=character_id,
        )

        brief_path.parent.mkdir(parents=True, exist_ok=True)
        bio_path.parent.mkdir(parents=True, exist_ok=True)

        briefing_text = render_briefing_markdown(req)
        brief_path.write_text(briefing_text, encoding="utf-8")

        # Repo-relative artifact paths, used by the cj2f auto-commit.
        rel_brief = brief_path.relative_to(self._prose_repo).as_posix()
        rel_bio = bio_path.relative_to(self._prose_repo).as_posix()

        # ck3_chronicler-5d9o: resolve model per request kind.
        # biography* + chronicle_export stay on Opus.
        base_model = self._resolve_model(req.kind)
        # ck3_chronicler-cs1o: engage the [1m] 1M-context variant (2x
        # input price) only when THIS call's prompt approaches the 200k
        # standard window. The briefing dominates prompt size — the
        # wrapper adds <500 chars.
        est_tokens = estimate_prompt_tokens(briefing_text)
        # Issue #46: the bracket is applied because THIS transport declares
        # MODEL_BRACKET, not because the helper used to be named after it.
        model_for_call = (
            apply_long_context_bracket(base_model, est_tokens)
            if self.long_context_policy is LongContextPolicy.MODEL_BRACKET
            else base_model
        )
        if model_for_call != base_model:
            log.info(
                "claude_code: long-context [1m] engaged (est=%d tokens) "
                "for kind=%s character_id=%s",
                est_tokens,
                req.kind,
                character_id,
            )

        log.info(
            "claude_code: invoking %s for character_id=%s campaign=%s version=%d kind=%s",
            model_for_call,
            character_id,
            campaign_uuid,
            version,
            req.kind,
        )

        start = time.monotonic()
        # ck3_chronicler-l7rk: the neutral cwd is a throwaway temp dir so
        # nothing autoloads/indexes a repo. Issue #19 puts the
        # system-prompt file in the same dir: one scope owns both, so
        # both are removed on every exit path (success, error, timeout,
        # cancel). Written and closed before the spawn — Windows would
        # not let ``claude`` read an open NamedTemporaryFile.
        with tempfile.TemporaryDirectory(prefix="chronicler-lean-") as workdir:
            system_prompt_file = Path(workdir) / "chronicler-system-prompt.md"
            system_prompt_file.write_text(req.system_prompt, encoding="utf-8")
            argv, stdin_input = self._build_argv_and_stdin(
                model=model_for_call,
                briefing_text=briefing_text,
                system_prompt_file=system_prompt_file,
            )
            stdout = await self._run_subprocess(
                argv=argv,
                stdin_input=stdin_input,
                cwd=workdir,
                character_id=character_id,
                campaign_uuid=campaign_uuid,
            )
        latency_ms = int((time.monotonic() - start) * 1000)

        envelope = _parse_json_envelope(stdout)
        # ck3_chronicler-l7rk: the model wrote nothing (no tools). Pull the
        # prose from the envelope's `result`; _persist_artifacts writes the
        # on-disk file.
        result_text = (envelope.get("result") if envelope else None) or ""
        body = result_text.strip()
        if not body:
            raise RuntimeError(
                "lean claude --print exited 0 but returned no prose in "
                f"`result` for character_id={character_id} "
                f"campaign={campaign_uuid}"
            )

        usage = _usage_from_envelope(envelope, requested_model=model_for_call, kind=req.kind)

        await self._persist_artifacts(
            bio_path=bio_path,
            body=body,
            rel_brief=rel_brief,
            rel_bio=rel_bio,
            character_id=character_id,
            campaign_uuid=campaign_uuid,
            version=version,
            model=usage.model_actual,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

        return NarrativeResponse(
            text=body,
            model=usage.model_actual,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=latency_ms,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cost_usd=usage.cost_usd,
        )


def make_claude_code_provider(
    *,
    prose_repo_path: Path | None = None,
    model: str | None = None,
) -> ClaudeCodeProvider:
    """Construct ClaudeCodeProvider with env-var fallbacks. Mirrors
    the existing ``make_*_provider`` factory pattern."""
    return ClaudeCodeProvider(prose_repo_path=prose_repo_path, model=model)

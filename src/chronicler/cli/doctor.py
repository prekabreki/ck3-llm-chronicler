"""ck3_chronicler-a38: ``chronicler doctor`` health check.

Probes the local environment for the dependencies the chronicler needs
on a fresh machine: CK3 install located, heraldry assets extracted,
registered campaigns. Each probe is independent and reports its own
pass/fail with a short detail line so the user can act on the failure
without re-running individual commands.

ck3_chronicler-tbrm.3: Ollama reachability + Anthropic key probes were
removed when the in-process providers were ripped out. The Claude Code
provider invokes ``claude --print`` and is verified separately by the
synthetic-jarl smoke memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from chronicler.db.registry import get_data_dir, list_campaigns
from chronicler.heraldry import find_ck3_install
from chronicler.heraldry.extractor import compute_heraldry_status

log = logging.getLogger(__name__)

# Probe names. Stable strings — the CLI's exit-code logic and the tests
# both reference them.
PROBE_CK3_INSTALL = "CK3 install located"
PROBE_HERALDRY = "Heraldry palette extracted"
PROBE_HERALDRY_FRESH = "Heraldry up to date"
PROBE_CAMPAIGNS = "Campaigns registered"
PROBE_NARRATIVE_BACKEND = "Narrative backend configured"
PROBE_OTHER_BACKENDS = "Other backends available"
PROBE_PROSE_REPO = "Prose register available"

# Probes whose failure is informational rather than fatal. The
# ck3_chronicler-8jz heraldry-staleness check fits this bucket — a
# stale extract still renders a valid shield; the user just isn't
# seeing post-patch additions. Surfaces to nudge action without
# breaking exit code.
INFORMATIONAL_PROBES: frozenset[str] = frozenset(
    {
        PROBE_HERALDRY_FRESH,
        # Issue #46: the alternatives' readiness is a "what could you
        # switch to" line, not a health requirement. The configured
        # backend has its own probe above and that one does gate.
        PROBE_OTHER_BACKENDS,
    }
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str


def probe_ck3_install() -> ProbeResult:
    install = find_ck3_install()
    if install is None:
        return ProbeResult(
            PROBE_CK3_INSTALL,
            False,
            "Steam default not found; pass --ck3-dir to `chronicler heraldry extract`",
        )
    return ProbeResult(PROBE_CK3_INSTALL, True, str(install))


def probe_heraldry_palette() -> ProbeResult:
    palette = get_data_dir() / "heraldry" / "palette.json"
    if palette.exists():
        return ProbeResult(PROBE_HERALDRY, True, str(palette))
    return ProbeResult(
        PROBE_HERALDRY,
        False,
        "run `chronicler heraldry extract` to populate",
    )


def probe_heraldry_freshness() -> ProbeResult:
    """ck3_chronicler-8jz: nudge a re-extract when CK3 has been patched.

    Compares the recorded ``extracted_at`` in the heraldry manifest to
    the source CoA dir mtime. When the source is newer, the user has
    likely patched CK3 since the last extract — the existing shields
    still render, but they may be missing patterns/emblems that the new
    patch added. Informational: failure here doesn't gate exit.
    Skipped silently when heraldry isn't extracted at all (the
    palette probe covers that case) or when the CK3 install dir
    can't be located.
    """
    heraldry_dir = get_data_dir() / "heraldry"
    install = find_ck3_install()
    status = compute_heraldry_status(heraldry_dir, install)
    if not status.extracted:
        # Defer to probe_heraldry_palette for the not-extracted case.
        return ProbeResult(
            PROBE_HERALDRY_FRESH,
            True,
            "skipped (heraldry not extracted yet)",
        )
    if install is None:
        return ProbeResult(
            PROBE_HERALDRY_FRESH,
            True,
            "skipped (CK3 install dir not configured)",
        )
    if status.is_stale:
        return ProbeResult(
            PROBE_HERALDRY_FRESH,
            False,
            "CK3 source dir is newer than the last extract — "
            "run `chronicler heraldry extract` (or use the Settings UI)",
        )
    return ProbeResult(
        PROBE_HERALDRY_FRESH,
        True,
        f"last extracted {status.last_extraction_at or 'unknown'}",
    )


def probe_narrative_backend(
    *, check_endpoints: bool = False, _client: object = None
) -> ProbeResult:
    """ck3_chronicler-cs1o: backend-aware transport check.

    claude-code: verifies the ``claude`` binary resolves on PATH (the
    pivot-era check this module deferred to the smoke memory) and notes
    the post-2026-06-15 programmatic-credit claim requirement — if
    generations fail with billing errors after that date, the per-seat
    credit likely hasn't been claimed (one-time, can't be done by an
    admin) or is exhausted; CHRONICLER_NARRATIVE_BACKEND=anthropic is
    the fallback. anthropic: verifies ANTHROPIC_API_KEY presence
    (presence only — no live call from a health probe).

    Issue #21: openai-compatible validates the config the factory would
    otherwise refuse to construct on — preset known, base URL resolvable,
    model named, key present for a paid vendor. Endpoint reachability is
    deliberately NOT probed by default, for the same reason the anthropic
    leg checks key presence only: `doctor` runs offline and in CI, and
    blocking on a connect timeout to a local server that isn't running
    turns a diagnostic into a hang.

    Issue #30: pass ``--check-endpoints`` to opt into a reachability
    probe (``GET /models`` with a ≤3 s timeout, send key as Bearer).
    """
    import shutil

    from chronicler.narrative.anthropic import get_anthropic_api_key
    from chronicler.narrative.claude_code import _get_claude_code_bin
    from chronicler.narrative.factory import resolve_backend
    from chronicler.narrative.model_resolution import resolved_models

    backend = resolve_backend()
    if backend == "openai-compatible":
        return _probe_openai_compatible(check_endpoints=check_endpoints, _client=_client)
    models = resolved_models()
    if backend == "anthropic":
        if get_anthropic_api_key() is None:
            return ProbeResult(
                PROBE_NARRATIVE_BACKEND,
                False,
                "backend=anthropic but ANTHROPIC_API_KEY is not set — "
                "generation will refuse to start",
            )
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"anthropic (direct API, key present) — bio={models['biography']}",
        )
    if backend == "claude-code":
        bin_name = _get_claude_code_bin()
        resolved = shutil.which(bin_name)
        if resolved is None:
            return ProbeResult(
                PROBE_NARRATIVE_BACKEND,
                False,
                f"backend=claude-code but {bin_name!r} not on PATH — "
                "install Claude Code or set CHRONICLER_CLAUDE_CODE_BIN",
            )
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"claude-code via {resolved} — bio={models['biography']} "
            "(subscription pool: claim your monthly programmatic credit "
            "once per seat post-2026-06-15)",
        )
    # Issue #45: valid set comes from the registry, not a hand-kept list
    # here — this line already said "claude-code, anthropic,
    # openai-compatible" while the factory it describes only knew two.
    from chronicler.narrative.backend_config import (
        CHRONICLER_NARRATIVE_BACKEND_ENV,
        NARRATIVE_BACKEND_SETTING,
    )
    from chronicler.narrative.factory import known_backends

    return ProbeResult(
        PROBE_NARRATIVE_BACKEND,
        False,
        f"unknown narrative backend {backend!r} (from the "
        f"{NARRATIVE_BACKEND_SETTING!r} setting or "
        f"{CHRONICLER_NARRATIVE_BACKEND_ENV}); "
        f"valid: {', '.join(known_backends())}",
    )


def _probe_openai_compatible(
    *, check_endpoints: bool = False, _client: object = None
) -> ProbeResult:
    """Config check for the openai-compatible transport (issue #21).

    Reports what the transport would be pointed at — preset, endpoint,
    model — and never the key itself: ``chronicler doctor`` output gets
    pasted into bug reports.

    When *check_endpoints* is True, probes reachability with a ``GET
    /models`` (≤3 s timeout). 200 → green and model-list comparison;
    401/403/404/405 → green (reachability proven, /models may not be
    implemented or may require auth); 5xx → reachable-with-note; connect
    failure / timeout → red. The probe sends the configured API key as a
    Bearer token so endpoints that gate ``/models`` behind auth
    (OpenAI, DeepSeek, OpenRouter) get a 200 rather than a false 401 red.
    """
    from chronicler.narrative.backend_config import resolve_openai_config

    # Issue #45: resolution + the four failure messages now live in
    # backend_config, shared with the factory, and honour settings.json
    # ahead of the env vars. This probe used to read the env directly and
    # word the same four failures itself — two vocabularies for one check.
    config, error = resolve_openai_config()
    if config is None:
        return ProbeResult(PROBE_NARRATIVE_BACKEND, False, error or "unknown configuration error")

    endpoint = config.base_url
    model = config.model
    preset = config.preset
    label = config.label
    key_state = config.key_state

    if not check_endpoints:
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"{label} at {endpoint} — model={model} ({key_state}); "
            "endpoint reachability is not probed",
        )

    return _probe_endpoint_reachability(
        endpoint=endpoint,
        model=model,
        label=label,
        key_state=key_state,
        api_key=config.api_key,
        preset=preset,
        _client=_client,
    )


def _probe_endpoint_reachability(
    *,
    endpoint: str,
    model: str,
    label: str,
    key_state: str,
    api_key: str | None,
    preset: object | None,
    _client: object = None,
) -> ProbeResult:
    import httpx

    _LOCAL_PRESETS = frozenset({"ollama", "lmstudio"})

    headers: dict[str, str] = {}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    client = _client or httpx.Client(timeout=3.0)
    try:
        with client:
            resp = client.get(f"{endpoint.rstrip('/')}/models", headers=headers)
    except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
        local_hint = ""
        if preset is not None and preset.id in _LOCAL_PRESETS:
            local_hint = " (the server is probably not running)"
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            False,
            f"{label} at {endpoint} — connection refused{local_hint}; model={model}",
        )
    except httpx.TimeoutException:
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            False,
            f"{label} at {endpoint} — connection timed out; model={model}",
        )
    except httpx.TransportError as exc:
        # Only the exception's *type* reaches output. Its message is
        # third-party text of unbounded shape, and this module's output is
        # made to be pasted into bug reports (see the module docstring) —
        # so nothing an httpx/proxy/TLS layer chose to put in a string gets
        # a free ride into it. The type name is what tells the two failure
        # families apart; the message never added a diagnosis.
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            False,
            f"{label} at {endpoint} — transport error ({type(exc).__name__}); model={model}",
        )

    # Any HTTP response means the endpoint is reachable.
    status = resp.status_code
    if status == 200:
        try:
            data = resp.json()
            model_ids = [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            model_ids = []
        model_matches = model in model_ids
        model_note = (
            f"configured model {model!r} is in the server list"
            if model_matches
            else f"configured model {model!r} not found in "
            "server model list (proxies rename models)"
        )
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"{label} at {endpoint} — model={model} ({key_state}); "
            f"endpoint is reachable ({status}), {model_note}",
        )
    if status in (401, 403):
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"{label} at {endpoint} — model={model} ({key_state}); "
            f"endpoint is reachable ({status})",
        )
    if status in (404, 405):
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"{label} at {endpoint} — model={model} ({key_state}); "
            f"endpoint is reachable ({status}); "
            "/models is not implemented by this server",
        )
    if 500 <= status < 600:
        return ProbeResult(
            PROBE_NARRATIVE_BACKEND,
            True,
            f"{label} at {endpoint} — model={model} ({key_state}); "
            f"endpoint responded ({status}) — server may be unhealthy",
        )
    return ProbeResult(
        PROBE_NARRATIVE_BACKEND,
        True,
        f"{label} at {endpoint} — model={model} ({key_state}); "
        f"endpoint is reachable (HTTP {status})",
    )


def probe_other_backends() -> ProbeResult:
    """Issue #46: one line on the backends you are NOT using.

    The point is the fallback question — "the credit pool ran dry
    mid-campaign, what can I switch to right now?" — which previously meant
    reading the docs and guessing. Informational: never gates the exit
    code, since a chronicler with one working backend is healthy.

    Reports readiness only, and never a key: presence, not value.
    """
    import shutil

    from chronicler.narrative.anthropic import get_anthropic_api_key
    from chronicler.narrative.backend_config import resolve_backend, resolve_openai_config
    from chronicler.narrative.claude_code import _get_claude_code_bin
    from chronicler.narrative.factory import known_backends

    configured = resolve_backend()
    parts: list[str] = []
    for backend in known_backends():
        if backend == configured:
            continue
        if backend == "claude-code":
            ready = shutil.which(_get_claude_code_bin()) is not None
            detail = "claude on PATH" if ready else "claude not on PATH"
        elif backend == "anthropic":
            ready = get_anthropic_api_key() is not None
            detail = "key present" if ready else "no ANTHROPIC_API_KEY"
        else:
            config, _error = resolve_openai_config()
            ready = config is not None
            detail = f"{config.label} at {config.base_url}" if config else "not configured"
        parts.append(f"{backend}: {'ready' if ready else 'not ready'} ({detail})")

    return ProbeResult(PROBE_OTHER_BACKENDS, True, " | ".join(parts) or "none")


def probe_prose_repo() -> ProbeResult:
    """Issue #43: is the prose register actually where settings say it is?

    ``assemble_system_prompt`` enforces three things at generation time and
    raises on each, so a stale ``prose_repo_path`` broke every biography
    while doctor reported all-green. Found live on 2026-08-12: a settings
    override pointing at a path that had moved, five green probes, and a
    ``RuntimeError`` waiting at the next death.

    Mirrors those same three checks (directory, register, per-kind voice
    file) with filesystem calls only, and always reports the provenance —
    a green row naming only the path would not have surfaced that bug any
    better than the silence did, because the path *looked* plausible. The
    kinds come from :data:`PromptKind` rather than a hardcoded list, and a
    kind with no registered voice file is not a failure.
    """
    from typing import get_args

    from chronicler.config import resolve_prose_repo_path
    from chronicler.narrative.prose_io import (
        CHRONICLER_PROSE_REPO_PATH_ENV,
        voice_file_for_kind,
    )
    from chronicler.narrative.provider import PromptKind

    resolved = resolve_prose_repo_path()
    path = resolved.value
    # Provenance in every message, pass or fail: "where did this path even
    # come from" is the question the live failure could not answer.
    origin = {
        "override": "from settings.json",
        "env": f"from {CHRONICLER_PROSE_REPO_PATH_ENV}",
    }.get(resolved.source, f"{resolved.source} location")

    if path is None or not path.is_dir():
        shown = str(path) if path is not None else "(unresolved)"
        fix = (
            "correct `prose_repo_path` in settings.json, or run `chronicler init-prose`"
            if resolved.source == "override"
            else "run `chronicler init-prose` to scaffold one"
        )
        return ProbeResult(
            PROBE_PROSE_REPO,
            False,
            f"no chronicle directory at {shown} ({origin}) -- {fix}",
        )

    if not (path / "CLAUDE.md").is_file():
        return ProbeResult(
            PROBE_PROSE_REPO,
            False,
            f"{path} ({origin}) has no CLAUDE.md register -- generation refuses to "
            "run without it; `chronicler init-prose` scaffolds the template",
        )

    missing = [
        voice_rel
        for voice_rel in (voice_file_for_kind(kind) for kind in get_args(PromptKind))
        if voice_rel and not (path / voice_rel).is_file()
    ]
    if missing:
        return ProbeResult(
            PROBE_PROSE_REPO,
            False,
            f"{path} ({origin}) is missing voice file(s): {', '.join(sorted(missing))} "
            "-- those kinds fail at generation; re-run `chronicler init-prose` or "
            "restore them from prose-template/",
        )

    return ProbeResult(
        PROBE_PROSE_REPO,
        True,
        f"{path} ({origin}) -- register + voice files present",
    )


def probe_known_campaigns() -> ProbeResult:
    campaigns = list_campaigns(include_archived=True)
    if not campaigns:
        return ProbeResult(
            PROBE_CAMPAIGNS,
            False,
            "no campaigns yet -- run `chronicler campaign create <name>` or import a save",
        )
    active = sum(1 for c in campaigns if not c.archived)
    return ProbeResult(
        PROBE_CAMPAIGNS,
        True,
        f"{len(campaigns)} total ({active} active)",
    )


# Order matters for the rendered table — most-load-bearing first so a
# user reading top-down sees the critical-path failure before the
# informational ones.
ALL_PROBES = (
    probe_ck3_install,
    probe_narrative_backend,
    probe_other_backends,
    probe_prose_repo,
    probe_heraldry_palette,
    probe_heraldry_freshness,
    probe_known_campaigns,
)

# ck3_chronicler-27ov.80 (audit L27): fallback display names used only
# when a probe raises (so we can't read the name off its ProbeResult).
# Keeping the real name preserves informational-vs-critical semantics for
# a crashed probe. test_doctor_probe_names_cover_all_probes guards drift.
_PROBE_NAMES = {
    probe_ck3_install: PROBE_CK3_INSTALL,
    probe_narrative_backend: PROBE_NARRATIVE_BACKEND,
    probe_other_backends: PROBE_OTHER_BACKENDS,
    probe_prose_repo: PROBE_PROSE_REPO,
    probe_heraldry_palette: PROBE_HERALDRY,
    probe_heraldry_freshness: PROBE_HERALDRY_FRESH,
    probe_known_campaigns: PROBE_CAMPAIGNS,
}


def run_all_probes(*, check_endpoints: bool = False) -> list[ProbeResult]:
    """Run every probe in declaration order, isolating failures.

    The docstring used to *claim* "a probe never raises" but nothing
    enforced it — one probe that threw (e.g. a malformed Steam library
    file, a permissions error reading the heraldry dir) aborted the
    whole diagnostic, on exactly the fresh/broken machines doctor exists
    to help. ck3_chronicler-27ov.80 (audit L27): wrap each probe so an
    unexpected exception becomes a failed :class:`ProbeResult` instead of
    propagating, letting the remaining probes still report.
    """
    results: list[ProbeResult] = []
    for probe in ALL_PROBES:
        try:
            if probe is probe_narrative_backend:
                results.append(probe(check_endpoints=check_endpoints))
            else:
                results.append(probe())
        except Exception as exc:  # noqa: BLE001 — a crashed probe must not abort the rest
            name = _PROBE_NAMES.get(probe, getattr(probe, "__name__", "unknown probe"))
            log.exception("doctor probe %s crashed", name)
            results.append(ProbeResult(name, False, f"probe crashed: {type(exc).__name__}: {exc}"))
    return results


def render_probes(results: list[ProbeResult]) -> str:
    """Two-column rendering: pass/fail marker + name + detail.

    ASCII markers (``[OK]`` / ``[!!]``) instead of unicode glyphs so the
    output round-trips cleanly through Windows cp1252 default consoles
    without an explicit encoding switch.
    """
    name_width = max(len(r.name) for r in results) + 2
    lines = []
    for r in results:
        mark = "[OK]" if r.ok else "[!!]"
        info = " (informational)" if r.name in INFORMATIONAL_PROBES and not r.ok else ""
        lines.append(f"  {mark}  {r.name.ljust(name_width)}{r.detail}{info}")
    return "\n".join(lines)


def has_critical_failure(results: list[ProbeResult]) -> bool:
    """A failure on a non-informational probe gates the doctor's exit
    code — informational probes can fail without making the doctor
    return non-zero."""
    return any(not r.ok and r.name not in INFORMATIONAL_PROBES for r in results)


def cmd_doctor_migrate_impl(
    *,
    restore: str | None = None,
    status_only: bool = False,
) -> None:
    """Implementation reused by the typer subcommand. Kept module-level
    so tests can call directly without typer machinery if desired."""
    from pathlib import Path

    import typer

    from chronicler.db.registry import registry_path
    from chronicler.migrate import (
        list_backups,
        restore_from_backup,
        run_migration,
        scan,
    )

    data_dir = get_data_dir()
    reg = registry_path()

    if restore is not None:
        # Resolve timestamp → directory if user passed just a timestamp.
        backup_dir = Path(restore)
        if not backup_dir.is_absolute():
            entries = list_backups(data_dir=data_dir)
            match = next((e for e in entries if e.timestamp == restore), None)
            if match is None:
                typer.echo(f"backup not found: {restore}", err=True)
                raise typer.Exit(code=1)
            backup_dir = match.path
        restored = restore_from_backup(
            backup_dir,
            registry_path=reg,
            data_dir=data_dir,
            scheduler_running=False,
        )
        typer.echo(f"restored {restored} file(s) from {backup_dir}")
        return

    plan = scan(registry_path=reg)
    if not plan.needs_migration and not plan.registry_needs_migration:
        typer.echo("All schemas current; no migration needed.")
        return
    typer.echo("Pending migrations:")
    for p in plan.needs_migration:
        typer.echo(f"  - {p.name} ({p.current_head or 'unstamped'} -> {p.target_head})")
    if plan.registry_needs_migration:
        typer.echo(f"  - registry (missing columns: {', '.join(plan.registry_missing_columns)})")

    if status_only:
        return

    result = run_migration(registry_path=reg, data_dir=data_dir, scheduler_running=False)
    typer.echo("")
    if result.backup_dir:
        typer.echo(f"Backup written to {result.backup_dir}")
    for r in result.results:
        marker = "[OK]" if r.ok else "[!!]"
        typer.echo(f"  {marker} {r.id}{(' -- ' + r.error) if r.error else ''}")
    if not result.success:
        raise typer.Exit(code=1)

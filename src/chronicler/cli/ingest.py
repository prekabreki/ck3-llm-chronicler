"""Ingest + serve CLI verbs: tail / serve / dev / save-tail / import-save /
auto-track / track / untrack.

ck3_chronicler-64vp (audit L23): extracted from ``main.py``. The
module-level ``make_narrative_provider`` / ``run_save_ingest`` / ``asyncio``
bindings are the seams the CLI entry-point tests patch — keep them importable
here. Importing this module attaches its ``@app.command`` verbs to the root
``app`` from ``_app``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from chronicler.cli._app import app
from chronicler.cli._shared import (
    _auto_resolve_campaign,
    _campaign_session,
    _resolve_campaign,
    _try_auto_resolve_campaign,
)
from chronicler.config import get_ck3_debug_log, get_ck3_save_dir
from chronicler.db.engine import session_scope
from chronicler.db.registry import (
    Campaign,
    add_tracked_character,
    get_tail_offset,
    remove_tracked_character,
)
from chronicler.db.repository import (
    PlaythroughMismatchError,
    assert_playthrough_or_pin,
)
from chronicler.logging_setup import setup_logging
from chronicler.narrative import make_narrative_provider
from chronicler.save import (
    DEFAULT_SAVE_PATTERN,
    auto_track_candidates,
    convert_save_to_json,
    parse_save,
    resolve_auto_track_rules,
    run_save_ingest,
)
from chronicler.tailer.ingest import run_ingest

log = logging.getLogger(__name__)


def _run_closing_provider(make_coro, provider) -> object:  # type: ignore[no-untyped-def]
    """Run ``make_coro()``, releasing the narrative transport on the way out.

    Issue #46: these long-running CLI entry points own the provider they
    construct, and the two HTTP transports hold an ``httpx.AsyncClient``.
    ``aclose()`` has to be awaited *inside* the loop, hence the wrapper
    rather than a call after ``asyncio.run`` returns. In a ``finally`` so
    Ctrl-C and a crashing ingest loop close the client too — the paths that
    actually happen in daily use.

    Takes a **factory**, not a coroutine, so nothing is constructed outside
    the loop: a coroutine built at the call site and then never awaited (as
    happens when a test stubs ``asyncio.run``) raises "coroutine was never
    awaited" and, worse, would skip the close entirely.
    """

    async def _main():  # type: ignore[no-untyped-def]
        try:
            return await make_coro()
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception:  # noqa: BLE001 — must not mask the real exit reason
                    # Swallowed so a failing close cannot replace the reason
                    # the loop actually ended (Ctrl-C, an ingest crash), but
                    # LOGGED: a silent except here would also hide a provider
                    # that has no aclose() at all, which is a wiring bug.
                    log.warning(
                        "failed to close narrative transport %r on shutdown",
                        getattr(provider, "name", type(provider).__name__),
                        exc_info=True,
                    )

    return asyncio.run(_main())


@app.command("tail")
def cmd_tail(
    campaign: str = typer.Option(..., "--campaign", "-c", help="Campaign name."),
    log_path: Path | None = typer.Option(
        None,
        "--log",
        help="Override CK3 debug.log path.",
    ),
    log_level: str = typer.Option("INFO", help="Logging level."),
    no_biography: bool = typer.Option(
        False,
        "--no-biography",
        help="Disable automatic biography generation on death events.",
    ),
) -> None:
    """Tail CK3's debug.log into the named campaign's DB.

    DEPRECATED (audit F-17 / ck3_chronicler-hz16): the save-tail
    pipeline (``chronicler dev``) is the production path post-v0.6 —
    it watches the autosave directory directly and produces richer
    diff events than debug.log can. ``chronicler tail`` is preserved
    as a back-compat alternate for users who want log-driven ingest,
    but new event types ship through save-tail only. Removal of this
    command is tracked in ck3_chronicler-hz16; until then the warning
    below makes the deprecation visible at startup.

    When biography generation is enabled (the default), each ingested
    death event schedules an asynchronous LLM call against the
    configured narrative provider (claude --print on the sibling prose
    repo, post-tbrm). The biography task runs in its own session and
    never blocks the ingest loop.
    """
    setup_logging(level=getattr(logging, log_level.upper()))
    typer.echo(
        "WARNING: `chronicler tail` is deprecated — save-tail "
        "(`chronicler dev`) is the production ingest path post-v0.6. "
        "See ck3_chronicler-hz16 (audit F-17).",
        err=True,
    )
    c = _resolve_campaign(campaign)
    resolved_log = log_path or get_ck3_debug_log()
    typer.echo(f"tailing {resolved_log} -> {c.name} ({c.db_path})")
    provider = None if no_biography else make_narrative_provider()
    if provider is None:
        typer.echo("biography generation: DISABLED (--no-biography)")
    else:
        typer.echo(f"biography generation: enabled ({provider.name})")
    _run_closing_provider(
        lambda: run_ingest(
            log_path=resolved_log,
            db_path=Path(c.db_path),
            campaign_id=c.id,
            start_offset=get_tail_offset(c.id),
            biography_provider=provider,
        ),
        provider,
    )


@app.command("serve")
def cmd_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8000, "--port", help="Port to listen on."),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Watch source files and restart on changes (development mode).",
    ),
    log_level: str = typer.Option("info", help="uvicorn log level (debug/info/warning/error)."),
) -> None:
    """Run the chronicler web app on uvicorn.

    Reads from whatever campaigns exist in the registry — start it
    independently of any ingest loop. For a combined process that runs
    both the server and a save-tail loop, use ``chronicler dev``.

    Defaults to local-only binding (127.0.0.1:8000). Pass --host 0.0.0.0
    explicitly if you want LAN access (chronicler stores no auth and
    has no concept of multi-user; only do this on a trusted network).
    """
    import uvicorn

    if reload:
        # ck3_chronicler-pr7m: use the provider-bound factory so the
        # reload subprocess gets a populated app.state.narrative_provider.
        # The bare 'chronicler.api:create_app' factory takes no kwargs
        # under uvicorn factory mode, leaving the provider unset and every
        # LLM endpoint silently 503-ing.
        typer.echo("narrative provider: lazy (constructed inside factory)")
        uvicorn.run(
            "chronicler.api:create_app_with_default_provider",
            host=host,
            port=port,
            reload=True,
            factory=True,
            log_level=log_level,
            # ck3_chronicler-dsjo: uvicorn defaults timeout_keep_alive=5s,
            # which matches the SSE heartbeat cadence exactly — every quiet
            # SSE stream is one race-loss away from a server-side disconnect.
            # 120s is comfortably larger than any reasonable heartbeat
            # interval and below most HTTP intermediaries' upper bounds.
            timeout_keep_alive=120,
        )
    else:
        from chronicler.api import create_app

        provider = make_narrative_provider()
        typer.echo(f"narrative provider: {provider.name}")
        uvicorn.run(
            create_app(narrative_provider=provider),
            host=host,
            port=port,
            log_level=log_level,
            # ck3_chronicler-dsjo: see reload-branch comment above.
            timeout_keep_alive=120,
        )


@app.command("dev")
def cmd_dev(
    campaign: str | None = typer.Option(
        None,
        "--campaign",
        "-c",
        help=(
            "Campaign name. Omit to auto-detect from the save's "
            "playthrough_id (creates a new campaign if no match)."
        ),
    ),
    save_dir: Path | None = typer.Option(
        None, "--save-dir", help="Override CK3 save games directory."
    ),
    pattern: str = typer.Option(
        ",".join(DEFAULT_SAVE_PATTERN),
        "--pattern",
        help=(
            "Glob filter within save_dir. Comma-separate to watch "
            "multiple patterns (e.g. 'autosave.ck3,autosave_exit.ck3' "
            "— ck3_chronicler-fi7)."
        ),
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    no_biography: bool = typer.Option(
        False, "--no-biography", help="Disable automatic biography generation."
    ),
    log_level: str = typer.Option("INFO", help="Logging level."),
) -> None:
    """Run the web app and save-tail loop together.

    The intended one-terminal workflow: start CK3 (in clean UI mode
    with monthly autosaves), open a browser to localhost:8000, and run
    this command. New autosaves get ingested as you play; the browser
    page reflects updates on refresh.

    ck3_chronicler-cqo: ``--campaign`` is now optional. When omitted,
    auto-detect from the latest save in ``--save-dir``.
    """
    setup_logging(level=getattr(logging, log_level.upper()))
    resolved_dir = save_dir or get_ck3_save_dir()
    # ck3_chronicler-nji: campaign resolution is now best-effort — if no
    # campaign / save is available, the orchestrator boots uvicorn alone
    # and the user adopts a save via the Library's '+ Adopt save' button.
    c: Campaign | None = None
    if campaign is not None:
        c = _resolve_campaign(campaign)
    else:
        resolved = _try_auto_resolve_campaign(resolved_dir, pattern)
        if resolved is not None:
            c, _save_path = resolved
            typer.echo(f"auto-detected campaign: {c.name} ({c.id})")
        else:
            typer.echo(
                f"no save in {resolved_dir} matching {pattern!r}; starting "
                "Library in adoption mode (use the '+ Adopt save' button)"
            )
    provider = None if no_biography else make_narrative_provider()
    if provider is None:
        typer.echo("biography generation: DISABLED (--no-biography)")
    else:
        typer.echo(f"biography generation: live ({provider.name})")
    typer.echo(f"web: http://{host}:{port}")
    if c is None:
        typer.echo("save-tail: deferred (no campaign yet)")
    else:
        typer.echo(f"save-tail: {c.name} ({c.db_path})")

    from chronicler.orchestrator import run_dev

    _run_closing_provider(
        lambda: run_dev(
            campaign_id=c.id if c is not None else None,
            db_path=Path(c.db_path) if c is not None else None,
            save_dir=save_dir,
            pattern=pattern,
            host=host,
            port=port,
            biography_provider=provider,
        ),
        provider,
    )


@app.command("save-tail")
def cmd_save_tail(
    campaign: str | None = typer.Option(
        None,
        "--campaign",
        "-c",
        help=(
            "Campaign name. Omit to auto-detect from the save's "
            "playthrough_id (creates a new campaign if no match)."
        ),
    ),
    save_dir: Path | None = typer.Option(
        None,
        "--save-dir",
        help="Override CK3 save games directory.",
    ),
    pattern: str = typer.Option(
        ",".join(DEFAULT_SAVE_PATTERN),
        "--pattern",
        help=(
            "Glob filter within save_dir. Comma-separate to watch "
            "multiple patterns (e.g. 'autosave.ck3,autosave_exit.ck3' "
            "— ck3_chronicler-fi7)."
        ),
    ),
    log_level: str = typer.Option("INFO", help="Logging level."),
    no_biography: bool = typer.Option(
        False,
        "--no-biography",
        help="Disable automatic biography generation on death events.",
    ),
) -> None:
    """Watch CK3's save directory and ingest events from each new save.

    The v0.6 architectural-pivot primary mode — no -debug_mode required,
    works with monthly autosaves to give real-time-enough biographies
    while CK3 runs in clean UI. See ``docs/architecture.md`` v0.6 section.

    ck3_chronicler-cqo: ``--campaign`` is now optional. When omitted,
    the latest save in ``--save-dir`` is parsed and resolved to an
    existing campaign by playthrough_id, or a new one is auto-created
    with name '<player_first_name> <bookmark_date_with_dashes>'.
    """
    setup_logging(level=getattr(logging, log_level.upper()))
    resolved_dir = save_dir or get_ck3_save_dir()
    if campaign is not None:
        c = _resolve_campaign(campaign)
    else:
        c, _save_path = _auto_resolve_campaign(resolved_dir, pattern)
        typer.echo(f"auto-detected campaign: {c.name} ({c.id})")
    typer.echo(f"watching {resolved_dir} (pattern={pattern!r}) -> {c.name} ({c.db_path})")
    provider = None if no_biography else make_narrative_provider()
    if provider is None:
        typer.echo("biography generation: DISABLED (--no-biography)")
    else:
        typer.echo(f"biography generation: live ({provider.name})")
    # ck3_chronicler-9qiw: this entry point is diagnostic — no FastAPI
    # app, no EventBus, no SSE. The next launcher that copy-pastes this
    # invocation will silently recreate baq5 (browser shows nothing
    # because no bus is wired). Warn loudly so it's not subtle.
    typer.echo(
        "WARNING: 'chronicler save-tail' is the diagnostic mode — no SSE "
        "/ browser updates are wired. Use 'chronicler dev' for the "
        "production workflow (see ck3_chronicler-baq5).",
        err=True,
    )
    _run_closing_provider(
        lambda: run_save_ingest(
            save_dir=resolved_dir,
            db_path=Path(c.db_path),
            campaign_id=c.id,
            pattern=pattern,
            biography_provider=provider,
        ),
        provider,
    )


@app.command("import-save")
def cmd_import_save(
    save_path: Path = typer.Argument(..., help="Path to a .ck3 save file."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
    force_reset_playthrough: bool = typer.Option(
        False,
        "--force-reset-playthrough",
        help=(
            "Overwrite the campaign's pinned playthrough_id with this save's. "
            "Use only when intentionally switching a campaign DB to a different "
            "CK3 playthrough; otherwise let the safety check refuse."
        ),
    ),
) -> None:
    """One-shot import: parse a .ck3 save and dump every character +
    vanilla memory into the campaign DB.

    Useful as a backfill step before starting `chronicler save-tail` —
    populates character names + dynasty + culture + faith fields that
    were null in v0.2's debug_log-only ingest, and adds the entire
    vanilla `memories` array as `event_type='vanilla_memory'` rows.
    Idempotent: re-running with the same save produces zero new rows.
    """
    if not save_path.is_file():
        typer.echo(f"save file not found: {save_path}", err=True)
        raise typer.Exit(code=2)

    setup_logging(level=logging.INFO)
    typer.echo(f"parsing {save_path.name}…")

    with _campaign_session(campaign) as (c, factory):
        # ck3_chronicler-u3m: real work delegated to chronicler.save.importer
        # so the same code path runs from the HTTP endpoint with progress
        # streamed over SSE. CLI just relays per-stage progress to typer.
        from chronicler.save.importer import ImportProgress, import_save

        def _print(p: ImportProgress) -> None:
            typer.echo(f"  [{int(p.fraction * 100):3d}%] {p.stage}: {p.message}")

        result = import_save(
            save_path,
            factory=factory,
            campaign_id=c.id,
            progress=_print,
            force_reset_playthrough=force_reset_playthrough,
        )

    if not result.success:
        typer.echo(f"refusing: {result.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"  characters upserted: {result.chars_upserted}; "
        f"vanilla memories: {result.memories_inserted} new, "
        f"{result.memories_duplicate} duplicate"
    )


@app.command("auto-track")
def cmd_auto_track(
    campaign: str = typer.Option(..., "--campaign", "-c"),
    save_path: Path | None = typer.Option(
        None,
        "--save",
        help="Specific save to read; defaults to the latest in CK3's save games directory.",
    ),
    force_reset_playthrough: bool = typer.Option(
        False,
        "--force-reset-playthrough",
        help=(
            "Overwrite the campaign's pinned playthrough_id with this save's. "
            "Use only when intentionally switching a campaign DB to a different "
            "CK3 playthrough; otherwise let the safety check refuse."
        ),
    ),
) -> None:
    """Parse the latest save and add player + immediate family to tracked.

    Replaces the v0.2 manual flow of running `charinfo` in the CK3
    console to find character IDs. The save's ``meta_main_portrait.id``
    is the player; the player's ``family_data`` block has
    spouse/children/parents IDs. All get added to ``tracked_characters``
    with appropriate ``note`` labels.
    """
    # How many auto-resolved saves to try before giving up. The newest
    # save is `autosave_exit.ck3` whenever CK3 has just been quit, and
    # that one carries no `played_character` root, so the obvious
    # first-run order (play, quit, set up tracking) lands on exactly the
    # save auto-track cannot use. Walk back rather than bail. Bounded,
    # because each attempt melts and parses a multi-megabyte save.
    _AUTO_RESOLVE_ATTEMPTS = 5

    resolved = save_path
    snap = None
    if resolved is not None:
        # An explicit --save is honoured as given: never substituted.
        if not resolved.is_file():
            typer.echo(f"save file not found: {resolved}", err=True)
            raise typer.Exit(code=2)
        snap = parse_save(convert_save_to_json(resolved))
        if snap.player_character_id is None:
            typer.echo(
                f"{resolved.name} has no player character. CK3 writes "
                "autosave_exit.ck3 on quit without one; pick an in-play save, "
                "or omit --save to let auto-track choose.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        from chronicler.save import saves_by_recency

        save_dir = get_ck3_save_dir()
        candidates = saves_by_recency(save_dir, DEFAULT_SAVE_PATTERN)
        if not candidates:
            typer.echo(f"no autosaves found in {save_dir}; pass --save <path>", err=True)
            raise typer.Exit(code=2)

        skipped: list[str] = []
        for candidate in candidates[:_AUTO_RESOLVE_ATTEMPTS]:
            attempt = parse_save(convert_save_to_json(candidate))
            if attempt.player_character_id is not None:
                resolved, snap = candidate, attempt
                break
            skipped.append(candidate.name)

        if snap is None or resolved is None:
            tried = ", ".join(skipped) or "none"
            typer.echo(
                f"no save in {save_dir} carries a player character. Tried: {tried}. "
                "autosave_exit.ck3 never has one; an observer-mode game has none either. "
                "Pass --save <path> to name an in-play save.",
                err=True,
            )
            raise typer.Exit(code=1)

        if skipped:
            typer.echo(f"using {resolved.name} (skipped {', '.join(skipped)}: no player character)")
        else:
            typer.echo(f"using latest autosave: {resolved.name}")

    # Surface save metadata so a user who picked the wrong save (e.g.
    # ran auto-track before launching CK3 and grabbed a previous
    # campaign's autosave) notices before any tracked-character rows
    # land. The playthrough_id is the canonical campaign UUID; date +
    # ck3_version + character count make the save's identity obvious.
    typer.echo(f"  save date:      {snap.current_date}")
    typer.echo(f"  bookmark date:  {snap.bookmark_date or '(unknown)'}")
    typer.echo(f"  CK3 version:    {snap.ck3_version}")
    typer.echo(f"  playthrough id: {snap.playthrough_id[:12]}…")
    typer.echo(f"  characters:     {len(snap.characters)}")
    typer.echo("")

    with _campaign_session(campaign) as (c, factory):
        with session_scope(factory) as session:
            try:
                assert_playthrough_or_pin(
                    session,
                    observed=snap.playthrough_id,
                    allow_reset=force_reset_playthrough,
                )
            except PlaythroughMismatchError as e:
                typer.echo(f"refusing: {e}", err=True)
                typer.echo(
                    "this save belongs to a different CK3 playthrough than the one "
                    "this campaign was pinned to. if you intend to switch this "
                    "campaign DB to track the new playthrough, re-run with "
                    "--force-reset-playthrough.",
                    err=True,
                )
                raise typer.Exit(code=1) from None

        # ck3_chronicler-vlw3: honour the campaign's per-campaign
        # auto-track rules (gw16) exactly as the GUI route does — same
        # resolver, so the CLI is a faithful proxy for the GUI's
        # behaviour rather than silently tracking characters the rules
        # exclude.
        rules = resolve_auto_track_rules(c.auto_track_rules)
        candidates = auto_track_candidates(snap, rules=rules)
        typer.echo(f"adding {len(candidates)} character(s) to tracked list:")
        for cid, note in candidates:
            char = snap.characters.get(cid)
            name = char.first_name if char else "(unknown)"
            add_tracked_character(c.id, cid, note=note)
            typer.echo(f"  {cid:>10}  {name}  ({note})")


@app.command("track")
def cmd_track(
    ck3_id: int = typer.Argument(..., help="CK3 character ID to mark for biography."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
    note: str | None = typer.Option(
        None, "--note", help="Optional note (e.g. 'player', 'spouse', 'rival')."
    ),
) -> None:
    """Add a character to the campaign's biography opt-in list.

    Only tracked characters get auto-biographies on death — keeps the
    LLM from being asked to write 1,000+ NPC biographies per game-year.
    Idempotent; re-adding updates the note.
    """
    c = _resolve_campaign(campaign)
    add_tracked_character(c.id, ck3_id, note=note)
    typer.echo(f"tracked {ck3_id} in {c.name}" + (f" — {note}" if note else ""))


@app.command("untrack")
def cmd_untrack(
    ck3_id: int = typer.Argument(..., help="CK3 character ID to remove from tracking."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
) -> None:
    """Remove a character from the campaign's biography opt-in list."""
    c = _resolve_campaign(campaign)
    removed = remove_tracked_character(c.id, ck3_id)
    if removed:
        typer.echo(f"untracked {ck3_id} from {c.name}")
    else:
        typer.echo(f"{ck3_id} was not tracked in {c.name}", err=True)
        raise typer.Exit(code=1)

"""Typer CLI assembly + entry point.

ck3_chronicler-64vp (audit L23): this was a 1061-line monolith. The verbs
now live in sibling modules that each attach to the shared root ``app``
(housed in ``_app`` to avoid a circular import):

    _shared.py   — campaign resolution, the latest-save resolve pipeline,
                   the per-campaign session contextmanager
    campaigns.py — the ``campaign`` sub-app (create / list / unarchive)
    ingest.py    — tail / serve / dev / save-tail / import-save /
                   auto-track / track / untrack
    inspect.py   — dump-character / dump-biography / regenerate-biography /
                   tracked / smoke-yearly

This module keeps the ``doctor`` and ``heraldry`` sub-apps (thin seams over
``cli/doctor.py`` and ``chronicler.heraldry``), wires every sub-app onto the
root ``app``, and exposes ``main()`` (the ``chronicler`` script entry point
in ``pyproject.toml``).

``_alembic_upgrade`` is re-exported here for back-compat — it lives in
``_shared`` now, but ``tests/integration/test_logging_isolation.py`` imports
it from this module.

The ``chronicler`` script is wired in ``pyproject.toml``.
"""

from __future__ import annotations

from pathlib import Path

import typer

# Importing the command modules runs their @app.command / @sub_app.command
# decorators, registering every verb onto the shared root ``app`` (from _app).
from chronicler.cli import campaigns, ingest, init_prose, inspect  # noqa: F401
from chronicler.cli._app import app
from chronicler.cli._shared import _alembic_upgrade  # noqa: F401 — back-compat re-export
from chronicler.db.registry import get_data_dir
from chronicler.heraldry import extract_assets, find_ck3_install

doctor_app = typer.Typer(invoke_without_command=True, no_args_is_help=False)
app.add_typer(doctor_app, name="doctor")


@doctor_app.callback(invoke_without_command=True)
def doctor_root(
    ctx: typer.Context,
    check_endpoints: bool = typer.Option(
        False,
        "--check-endpoints",
        help="Probe endpoint reachability for the narrative backend (network call; ≤3 s timeout).",
    ),
) -> None:
    """Probe the local environment for chronicler dependencies.

    Checks CK3 install discovery, heraldry assets extracted, and
    registered campaigns. Returns exit 0 when every required probe
    passes; exit 1 otherwise.

    First port of call when something feels wrong on a fresh machine
    or after a CK3 patch — see docs/setup-fresh-machine.md for the
    setup walkthrough.
    """
    if ctx.invoked_subcommand is not None:
        return
    from chronicler.cli.doctor import (
        has_critical_failure,
        render_probes,
        run_all_probes,
    )

    results = run_all_probes(check_endpoints=check_endpoints)
    typer.echo(render_probes(results))
    if has_critical_failure(results):
        raise typer.Exit(code=1)


@doctor_app.command("migrate")
def cmd_doctor_migrate(
    restore: str | None = typer.Option(
        None,
        "--restore",
        help="Restore from a backup directory (timestamp or absolute path).",
    ),
    status_only: bool = typer.Option(
        False,
        "--status",
        help="Detect-only — print pending migrations + exit 0 without acting.",
    ),
) -> None:
    """Detect schema mismatches, take a backup, run upgrades.

    With --restore <timestamp>: restore the registry + each per-campaign
    DB from that backup (look up timestamps via `chronicler doctor
    migrate --status` or the GUI's Settings → Migration panel)."""
    from chronicler.cli.doctor import cmd_doctor_migrate_impl

    cmd_doctor_migrate_impl(restore=restore, status_only=status_only)


heraldry_app = typer.Typer(help="Real CK3 coat-of-arms asset extraction.")
app.add_typer(heraldry_app, name="heraldry")


@heraldry_app.command("extract")
def cmd_heraldry_extract(
    ck3_dir: Path | None = typer.Option(
        None,
        "--ck3-dir",
        help="Override CK3 install path (default: probe Steam library locations).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Override output dir (default: <data-dir>/heraldry/).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-convert all DDS files even when PNGs already exist.",
    ),
) -> None:
    """Extract CK3 coat-of-arms textures + named-color palette to disk.

    One-time setup for the real-heraldry renderer (ck3_chronicler-7ao).
    Run after first install or after a CK3 patch that changes assets.
    Output goes to ``~/Documents/chronicler/heraldry/`` by default —
    purely local, never copied off this machine.
    """
    install = find_ck3_install(override=ck3_dir)
    if install is None:
        typer.echo(
            "CK3 install not found. Probed Steam library defaults; pass "
            "--ck3-dir <path> to override.",
            err=True,
        )
        raise typer.Exit(code=1)

    target = output or (get_data_dir() / "heraldry")
    typer.echo("extracting CK3 heraldry assets")
    typer.echo(f"  source: {install}")
    typer.echo(f"  target: {target}")
    if force:
        typer.echo("  --force: re-converting all files")

    last_logged: dict[str, int] = {}

    def _progress(group: str, current: int, total: int) -> None:
        # Log every 100 files (or on the last one) to keep stdout readable.
        if current == total or current - last_logged.get(group, 0) >= 100:
            typer.echo(f"  {group}: {current}/{total}")
            last_logged[group] = current

    summary = extract_assets(install, target, force=force, progress=_progress)

    # ASCII arrow only — Windows console default cp1252 can't encode unicode arrows.
    typer.echo(
        f"done: {summary.patterns_extracted} patterns + "
        f"{summary.emblems_extracted} emblems + "
        f"{summary.title_icons_extracted} title icons + "
        f"{summary.palette_colors} palette colors -> {target}"
    )
    if summary.reused_existing:
        typer.echo(f"  ({summary.reused_existing} files reused; pass --force to re-convert)")
    if summary.skipped_designer:
        typer.echo(f"  ({summary.skipped_designer} designer files skipped)")
    if summary.failed:
        typer.echo(
            f"  WARNING: {summary.failed} files failed to convert (see log); re-run to retry them"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()

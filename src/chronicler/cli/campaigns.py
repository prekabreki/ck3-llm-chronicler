"""``chronicler campaign`` sub-app — create / list / unarchive.

ck3_chronicler-64vp (audit L23): extracted from ``main.py``. Importing this
module attaches ``campaign_app`` to the root ``app`` from ``_app``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from chronicler.cli._app import app
from chronicler.cli._shared import _alembic_upgrade
from chronicler.db.registry import (
    create_campaign,
    get_campaign_by_name,
    list_campaigns,
    unarchive_campaign,
)

campaign_app = typer.Typer(help="Manage campaigns.")
app.add_typer(campaign_app, name="campaign")


@campaign_app.command("create")
def campaign_create(
    name: str = typer.Argument(..., help="Display name for the campaign."),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="Override per-campaign DB path. Defaults to ~/Documents/chronicler/campaigns/<id>.db.",
    ),
    ck3_version: str | None = typer.Option(None, help="CK3 version this campaign was started in."),
) -> None:
    """Create a new campaign and run migrations on its DB."""
    campaign = create_campaign(
        name, db_path=str(db_path) if db_path else None, ck3_version=ck3_version
    )
    _alembic_upgrade(Path(campaign.db_path))
    typer.echo(f"created campaign {campaign.id}: {campaign.name}")
    typer.echo(f"  db: {campaign.db_path}")


@campaign_app.command("list")
def campaign_list(
    include_archived: bool = typer.Option(False, "--all", help="Include archived campaigns."),
) -> None:
    """List campaigns in the registry."""
    if include_archived:
        # ck3_chronicler-27ov.15: list_campaigns is a pure read now —
        # pull newly-committed archived snapshots in explicitly so the
        # secondary machine's `campaign list --all` reflects a fresh
        # `git pull`. Best-effort; never block listing on sync.
        try:
            from chronicler.sync import bootstrap_archived_snapshots

            bootstrap_archived_snapshots()
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"(archived-snapshot bootstrap failed: {exc})", err=True)
    rows = list_campaigns(include_archived=include_archived)
    if not rows:
        typer.echo("(no campaigns)")
        return
    for c in rows:
        marker = " [archived]" if c.archived else ""
        typer.echo(f"{c.id}  {c.name}{marker}")
        typer.echo(f"    db: {c.db_path}")
        if c.last_event_at:
            typer.echo(f"    last_event_at: {c.last_event_at}")


@campaign_app.command("unarchive")
def campaign_unarchive(
    name: str = typer.Argument(..., help="Name of the archived campaign to re-activate."),
) -> None:
    """Re-activate a sealed campaign (ck3_chronicler-w2s).

    Looks up by name including archived rows; the default
    ``get_campaign_by_name`` filters them out. Idempotent on already-
    active campaigns. Surfaced primarily for dev / smoke recovery; the
    end-user-facing un-archive flow is the v0.9 frontend kebab menu."""
    campaign = get_campaign_by_name(name, include_archived=True)
    if campaign is None:
        typer.echo(f"campaign not found: {name}", err=True)
        raise typer.Exit(code=2)
    unarchive_campaign(campaign.id)
    typer.echo(f"un-archived campaign {campaign.id}: {campaign.name}")

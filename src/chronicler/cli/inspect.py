"""Inspection + one-shot CLI verbs: dump-character / dump-biography /
regenerate-biography / tracked / smoke-yearly.

ck3_chronicler-64vp (audit L23): extracted from ``main.py``. Importing this
module attaches its ``@app.command`` verbs to the root ``app`` from ``_app``.
(Module name shadows nothing — ``import inspect`` elsewhere resolves to the
stdlib via absolute import.)
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from chronicler.cli._app import app
from chronicler.cli._shared import _campaign_session, _resolve_campaign
from chronicler.db.registry import list_tracked_characters
from chronicler.db.repository import dump_character, get_latest_biography_for_character


@app.command("dump-character")
def cmd_dump_character(
    ck3_id: int = typer.Argument(..., help="CK3 character ID."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
    raw: bool = typer.Option(False, "--raw", help="Print compact JSON instead of pretty-printed."),
) -> None:
    """Dump a character + their events as JSON."""
    with _campaign_session(campaign) as (c, factory), factory() as session:
        dump = dump_character(session, ck3_id)

    if dump is None:
        typer.echo(f"character {ck3_id} not found in campaign {c.name}", err=True)
        raise typer.Exit(code=1)

    if raw:
        typer.echo(json.dumps(dump, separators=(",", ":")))
    else:
        typer.echo(json.dumps(dump, indent=2, sort_keys=False))


@app.command("regenerate-biography")
def cmd_regenerate_biography(
    ck3_id: int = typer.Argument(..., help="CK3 character ID to (re)biograph."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
    include_raw_record: bool = typer.Option(
        False,
        "--include-raw-record",
        help=(
            "Include the character's full rakaly record in the prompt "
            "(ck3_chronicler-6ui). Adds ~5-15 KB of JSON; useful for A/B "
            "testing whether richer raw data improves biography quality."
        ),
    ),
) -> None:
    """Force biography generation for a character now, bypassing the
    tracked-list filter and the scheduler.

    Useful when iterating on prompts (regenerate after editing the
    current biography prompt template), or when testing on characters
    who died before chronicler was running and so never triggered the
    auto-trigger.
    Each call inserts a new biography row with version+1.
    """
    import asyncio

    from chronicler.narrative import generate_biography, make_narrative_provider

    with _campaign_session(campaign) as (c, factory):
        # campaign_uuid is the campaign id (uuid string) so briefings
        # land in the right per-campaign bucket of the prose repo.
        provider = make_narrative_provider()
        typer.echo(
            f"generating biography for character {ck3_id} via {provider.name} "
            f"(include_raw_record={include_raw_record}; "
            "this calls the configured narrative backend and can take 1-5 minutes)..."
        )

        async def _run():
            try:
                return await generate_biography(
                    ck3_id,
                    factory=factory,
                    provider=provider,
                    include_raw_record=include_raw_record,
                    campaign_uuid=c.id,
                )
            finally:
                # Issue #46: aclose() is on the ABC now, so no hasattr probe.
                await provider.aclose()

        outcome = asyncio.run(_run())

    if outcome.error:
        typer.echo(f"failed: {outcome.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"generated biography (id={outcome.biography_id}). read with:\n"
        f"  uv run chronicler dump-biography {ck3_id} --campaign {c.name}"
    )


@app.command("dump-biography")
def cmd_dump_biography(
    ck3_id: int = typer.Argument(..., help="CK3 character ID."),
    campaign: str = typer.Option(..., "--campaign", "-c"),
) -> None:
    """Print the latest LLM-generated biography for a character."""
    with _campaign_session(campaign) as (c, factory), factory() as session:
        bio = get_latest_biography_for_character(session, ck3_id)

    if bio is None:
        typer.echo(f"no biography for character {ck3_id} in campaign {c.name}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"# Biography v{bio.version} — {bio.provider}")
    typer.echo(f"# generated {bio.generated_at}; prompt {bio.prompt_template_version}")
    typer.echo("")
    typer.echo(bio.body)


@app.command("tracked")
def cmd_tracked(
    campaign: str = typer.Option(..., "--campaign", "-c"),
) -> None:
    """List the characters currently opted in for biography generation."""
    c = _resolve_campaign(campaign)
    rows = list_tracked_characters(c.id)
    if not rows:
        typer.echo(f"(no tracked characters in {c.name})")
        typer.echo(
            "use 'chronicler track <id> --campaign "
            f"{c.name}' to add — only tracked characters get biographies on death."
        )
        return
    for row in rows:
        marker = f" — {row.note}" if row.note else ""
        typer.echo(f"{row.character_id}{marker}  (added {row.added_at})")


# ck3_chronicler-8jz: yearly-diff smoke surfaced as a first-class CLI
# verb so the patch-playbook runbook can recommend it without users
# digging through ``scripts/``. Delegates to the same code path as
# ``scripts/smoke_yearly_diff.py`` (kept for back-compat).
@app.command("smoke-yearly")
def cmd_smoke_yearly(
    baseline: Path = typer.Option(  # noqa: B008
        ...,
        "--baseline",
        help="Path to a persisted baseline snapshot (the .pkl under <data-dir>/baselines/).",
    ),
    save: Path = typer.Option(  # noqa: B008
        ...,
        "--save",
        help="Path to the .ck3 yearly autosave to diff against the baseline.",
    ),
    db: Path = typer.Option(  # noqa: B008
        ...,
        "--db",
        help="Path to the per-campaign DB (registry's db_path column).",
    ),
    campaign_id: str = typer.Option(
        ...,
        "--campaign-id",
        help="Campaign UUID. Currently informational; reserved for future cross-checks.",
    ),
) -> None:
    """Yearly-diff smoke against two saves — the patch-playbook fixture.

    Loads the persisted baseline, parses the target save via rakaly,
    runs the diff layer, and prints inserted-event tallies + a sample of
    the last events written. Intended as the first command after a CK3
    patch lands: confirms rakaly still parses the new format and that
    the diff layer's event_type taxonomy hasn't drifted. Rewrites the
    baseline on success — pass a copy if you want to preserve the
    original.

    See ``docs/patch-playbook.md`` for the full runbook.
    """
    from chronicler.smoke.smoke_yearly import run_smoke_yearly

    rc = run_smoke_yearly(baseline=baseline, save=save, db=db, campaign_id=campaign_id)
    if rc != 0:
        raise typer.Exit(code=rc)

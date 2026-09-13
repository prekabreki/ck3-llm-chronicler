"""Shared CLI helpers — campaign resolution, the latest-save resolve
pipeline, and the per-campaign session contextmanager.

ck3_chronicler-64vp (audit L23): extracted from the monolithic ``main.py``
so ``campaigns`` / ``ingest`` / ``inspect`` command modules can share them
without importing each other.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import typer
from sqlalchemy.orm import Session, sessionmaker

from chronicler.db import make_engine_for_path, make_session_factory
from chronicler.db.migrate_runner import upgrade_to_head
from chronicler.db.registry import Campaign, get_campaign_by_name
from chronicler.save.adoption import _build_resolve_snap


def _alembic_upgrade(db_path: Path) -> None:
    # ck3_chronicler-27ov.52: thin wrapper over the canonical runner
    # (kept as a module-level name so tests can patch it).
    upgrade_to_head(db_path)


def _resolve_campaign(name: str) -> Campaign:
    campaign = get_campaign_by_name(name)
    if campaign is None:
        typer.echo(f"campaign not found: {name}", err=True)
        raise typer.Exit(code=2)
    return campaign


def _parse_save_at_with_raw_for_resolve(path: Path):
    """Indirection seam so tests can patch the rakaly+parse pair when
    exercising the CLI auto-detect path. Production import is the
    save module's existing helper."""
    from chronicler.save.ingest import _parse_save_at_with_raw

    return _parse_save_at_with_raw(path)


# ck3_chronicler-27ov.53 (audit M-P4): the resolve pipeline (latest_save
# -> parse -> _build_resolve_snap -> resolve_campaign_for_save ->
# _alembic_upgrade) lives ONCE in _resolve_latest_save, returning a small
# outcome union. Fatal-vs-non-fatal is a presentation concern — the two
# thin callers below map each variant to typer.Exit or None.


@dataclass(frozen=True)
class _ResolvedSave:
    campaign: Campaign
    save_path: Path


@dataclass(frozen=True)
class _NoSave:
    pass


@dataclass(frozen=True)
class _UnparseableSave:
    save_path: Path


@dataclass(frozen=True)
class _ArchivedConflict:
    archived: Campaign
    exc: Exception


def _resolve_latest_save(
    save_dir: Path,
    pattern: str | tuple[str, ...] | list[str],
) -> _ResolvedSave | _NoSave | _UnparseableSave | _ArchivedConflict:
    """ck3_chronicler-cqo: locate the latest save in save_dir, parse it,
    resolve to an existing or new campaign, and migrate its DB.

    - no matching save (or missing dir) → :class:`_NoSave`
    - rakaly/parse failure → :class:`_UnparseableSave`
    - playthrough_id matches an archived campaign (ck3_chronicler-obds)
      → :class:`_ArchivedConflict` — callers decide whether that's
      fatal; resolving must never silently fork onto a duplicate.
    """
    from chronicler.db.registry import (
        ArchivedCampaignConflict,
        resolve_campaign_for_save,
    )
    from chronicler.save import latest_save

    save_path = latest_save(save_dir, pattern)
    if save_path is None:
        return _NoSave()
    parsed = _parse_save_at_with_raw_for_resolve(save_path)
    if parsed is None:
        return _UnparseableSave(save_path=save_path)
    _raw, parsed_snap = parsed
    snap_for_resolve = _build_resolve_snap(parsed_snap)
    try:
        campaign = resolve_campaign_for_save(snap_for_resolve, ck3_version=parsed_snap.ck3_version)
    except ArchivedCampaignConflict as e:
        return _ArchivedConflict(archived=e.archived_campaign, exc=e)
    # ck3_chronicler-ylt: resolve_campaign_for_save creates the registry row
    # but doesn't migrate the per-campaign DB file. Idempotent for already-
    # migrated campaigns; required for fresh ones.
    _alembic_upgrade(Path(campaign.db_path))
    return _ResolvedSave(campaign=campaign, save_path=save_path)


def _auto_resolve_campaign(
    save_dir: Path,
    pattern: str | tuple[str, ...] | list[str],
) -> tuple[Campaign, Path]:
    """Fatal presentation of :func:`_resolve_latest_save`: every failure
    variant becomes typer.echo + typer.Exit(1)."""
    outcome = _resolve_latest_save(save_dir, pattern)
    if isinstance(outcome, _ResolvedSave):
        return outcome.campaign, outcome.save_path
    if isinstance(outcome, _NoSave):
        typer.echo(
            f"no save found at {save_dir} matching {pattern!r}; "
            "play CK3 to produce an autosave first",
            err=True,
        )
        raise typer.Exit(code=1)
    if isinstance(outcome, _UnparseableSave):
        typer.echo(f"save at {outcome.save_path} could not be parsed by rakaly", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"save's playthrough_id matches archived campaign "
        f"{outcome.archived.name!r}; refusing to silently fork.\n"
        f"  to resume that campaign: un-archive it via the Library "
        f"page, or POST /api/campaigns/{outcome.archived.name}/unarchive\n"
        f"  to start a genuinely fresh playthrough on this save: "
        f"re-run with --campaign <new-name>",
        err=True,
    )
    raise typer.Exit(code=1) from outcome.exc


def _try_auto_resolve_campaign(
    save_dir: Path,
    pattern: str | tuple[str, ...] | list[str],
) -> tuple[Campaign, Path] | None:
    """ck3_chronicler-nji: non-fatal presentation of
    :func:`_resolve_latest_save` — every failure variant maps to None so
    chronicler dev boots in 'no campaign yet' mode. The archived-conflict
    case (ck3_chronicler-obds) loud-warns first so the user knows to
    un-archive via the Library page rather than silently forking."""
    outcome = _resolve_latest_save(save_dir, pattern)
    if isinstance(outcome, _ResolvedSave):
        return outcome.campaign, outcome.save_path
    if isinstance(outcome, _ArchivedConflict):
        typer.echo(
            f"chronicler dev: latest save's playthrough_id matches "
            f"archived campaign {outcome.archived.name!r}; booting "
            f"in 'no campaign yet' mode. Un-archive via the Library "
            f"page to resume that campaign.",
            err=True,
        )
    return None


@contextmanager
def _campaign_session(name: str) -> Iterator[tuple[Campaign, sessionmaker[Session]]]:
    """Resolve a campaign and yield its (Campaign, session-factory) pair.

    Replaces the engine = make_engine_for_path / factory = make_session_factory /
    try: ... finally: engine.dispose() boilerplate that was repeated across
    every CLI command hitting a per-campaign DB. (F006 / 2hv.)
    """
    c = _resolve_campaign(name)
    engine = make_engine_for_path(Path(c.db_path))
    try:
        yield c, make_session_factory(engine)
    finally:
        engine.dispose()

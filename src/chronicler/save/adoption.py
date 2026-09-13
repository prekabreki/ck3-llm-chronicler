"""ck3_chronicler-v2a: adopt-from-save helper for the HTTP / CLI surfaces.

The CLI and the SPA both need the same chain of operations to onboard a
save into a campaign:

1. rakaly parse the save (raw + structured snapshot).
2. resolve_campaign_for_save → existing campaign matched by
   playthrough_id or a freshly-created row.
3. ``alembic upgrade head`` against the resolved campaign's per-campaign
   DB so a fresh DB has all the migrations applied before
   :func:`chronicler.save.importer.import_save` writes the first rows.
4. import_save populates characters + vanilla memories. (No diff layer
   runs here — the resolved snap is the baseline; subsequent saves
   diff against it via the regular ingest loop.)

This module owns the shared business logic so the API endpoint
(``POST /api/campaigns/adopt-from-save``) can call it without touching
typer / sys.exit, and the CLI's ``_auto_resolve_campaign`` can also
delegate to it for parity. The CLI keeps its typer-style error
reporting at its own boundary; the API maps the same exceptions to
HTTPException 400 / 409 / 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chronicler.db.engine import make_engine_for_path, make_session_factory
from chronicler.db.migrate_runner import upgrade_to_head
from chronicler.db.registry import Campaign, resolve_campaign_for_save
from chronicler.save.ck3_names import resolve_dynasty_name
from chronicler.save.importer import ImportResult, import_save
from chronicler.save.parse import SaveSnapshot, parse_save
from chronicler.save.rakaly import RakalyError, convert_save_to_json


class AdoptionError(Exception):
    """Base for adopt-from-save failures the caller should distinguish.

    The API layer maps subclasses to HTTP status codes (404 for
    SaveNotFound, 400 for RakalyParseError + ParseError, 500 for
    AlembicUpgradeFailed). Library / scripting callers handle them
    directly."""


class SaveNotFound(AdoptionError):
    pass


class RakalyParseError(AdoptionError):
    pass


class ParseError(AdoptionError):
    pass


class AlembicUpgradeFailed(AdoptionError):
    pass


@dataclass(frozen=True, slots=True)
class AdoptionResult:
    campaign: Campaign
    parsed_snap: SaveSnapshot
    raw_save_data: dict[str, Any]
    import_result: ImportResult


def _alembic_upgrade(db_path: Path) -> None:
    """Run ``alembic upgrade head`` against the per-campaign DB.

    Idempotent for already-migrated campaigns (alembic no-ops when the
    head is reached). Raises :class:`AlembicUpgradeFailed` on any
    underlying failure so the API can surface a 500 with a clean
    message. The upgrade itself is the canonical shared runner
    (ck3_chronicler-27ov.52); only the error policy lives here."""
    try:
        upgrade_to_head(db_path)
    except Exception as e:
        raise AlembicUpgradeFailed(
            f"alembic upgrade failed for {db_path}: {type(e).__name__}: {e}"
        ) from e


def _build_resolve_snap(parsed_snap: SaveSnapshot) -> SimpleNamespace:
    """Build the duck-typed object resolve_campaign_for_save reads.

    Matches the CLI's existing helper exactly — extracts player first
    name + founding dynasty name from the parsed snapshot for the auto-
    name path. Both fields tolerate None (early playthroughs, fresh
    starts) and the auto-name pipeline handles those gracefully.

    ck3_chronicler-0r44: when the dynasty record only carries an integer
    ``key`` (a localisation index — most engine-defined dynasties like
    Munsö/Rurikid fall in that bucket), ``dynasties_lookup`` skips it
    and the auto-name falls through to the player's first name. Fall
    back to ``houses_lookup[dynasty_house_id]`` decoded via
    :func:`decode_house_name`, which resolves the same character via
    the house-level string slug (``"house_munso"`` → ``"Munso"``). The
    house name is the closest user-readable proxy for the dynasty name
    and matches the user's expectation that a fresh campaign defaults
    to the dynasty/house name, not the founding character's name."""
    pid = parsed_snap.player_character_id
    player_first_name: str | None = None
    founding_dynasty_name: str | None = None
    if pid is not None:
        char = parsed_snap.characters.get(pid)
        if char is not None:
            player_first_name = char.first_name
            # ck3_chronicler-3kyg: the dynasty → house fallback is shared
            # with character_columns.hydrate_character_columns. culture is
            # None here (the auto-name path has no culture handy); the
            # house-name fallback is what the user expects a fresh
            # campaign to default to.
            founding_dynasty_name, _ = resolve_dynasty_name(
                dynasty_house_id=char.dynasty_house_id,
                houses_lookup=parsed_snap.houses_lookup,
                house_to_dynasty=parsed_snap.house_to_dynasty,
                dynasties_lookup=parsed_snap.dynasties_lookup,
            )
    return SimpleNamespace(
        playthrough_id=parsed_snap.playthrough_id,
        bookmark_date=parsed_snap.bookmark_date,
        founding_player_first_name=player_first_name,
        founding_dynasty_name=founding_dynasty_name,
    )


def adopt_save(
    save_path: Path,
    *,
    registry_path: Path | None = None,
    force_reset_playthrough: bool = False,
) -> AdoptionResult:
    """ck3_chronicler-v2a: adopt a CK3 save into a campaign end-to-end.

    Parse → resolve campaign → alembic upgrade → import_save. Returns
    the full AdoptionResult so the API layer can serialise the campaign
    (and surface import progress / counts in a follow-up endpoint when
    the SSE channel is ready).

    Idempotent on a save that's already been adopted: resolve_campaign
    matches the existing playthrough_id, alembic head is a no-op,
    import_save's idempotent insert paths skip duplicates.

    Errors raise specific :class:`AdoptionError` subclasses so callers
    can map to HTTP / CLI surfaces uniformly."""
    if not save_path.is_file():
        raise SaveNotFound(f"save file not found on server: {save_path}")
    try:
        raw_save_data = convert_save_to_json(save_path)
    except (RakalyError, FileNotFoundError) as e:
        raise RakalyParseError(f"rakaly failed for {save_path}: {e}") from e
    try:
        parsed_snap = parse_save(raw_save_data)
    except Exception as e:
        raise ParseError(f"parse_save failed for {save_path}: {type(e).__name__}: {e}") from e

    resolve_snap = _build_resolve_snap(parsed_snap)
    campaign = resolve_campaign_for_save(
        resolve_snap,
        ck3_version=parsed_snap.ck3_version or None,
        registry=registry_path,
    )
    _alembic_upgrade(Path(campaign.db_path))

    engine = make_engine_for_path(Path(campaign.db_path))
    factory = make_session_factory(engine)
    try:
        # ck3_chronicler (2026-05-09): pass campaign_id so import_save
        # writes the registry's current_player_character_id + house +
        # bookmark identity at the end of the import. Without this, the
        # registry stays at NULL until save-tail's first tick — which
        # breaks every surface that resolves "the player" (Dynasty page
        # 404s, Lineage forces a manual character pick, Library card
        # bylines render as the auto-name fallback). Save-tail eventually
        # backfills, but adoption is the natural place for the user to
        # expect "everything is set up now."
        import_result = import_save(
            save_path,
            factory=factory,
            campaign_id=campaign.id,
            registry_path=registry_path,
            force_reset_playthrough=force_reset_playthrough,
        )
    finally:
        engine.dispose()
    return AdoptionResult(
        campaign=campaign,
        parsed_snap=parsed_snap,
        raw_save_data=raw_save_data,
        import_result=import_result,
    )

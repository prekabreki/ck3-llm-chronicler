"""Campaign-level routes — /api/campaigns and /api/campaigns/{name}."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from chronicler.api.dependencies import (
    EngineCache,
    get_campaign,
    get_campaign_including_archived,
    get_engine_cache,
)
from chronicler.api.serializers import blurb_from_chronicle, parse_coa_json
from chronicler.db.registry import (
    ArchivedCampaignConflict,
    Campaign,
    add_tracked_character,
    delete_campaign,
    get_campaign_by_id,
    get_campaign_by_name,
    get_tracked_character_ids,
    list_campaigns,
    rename_campaign,
    unarchive_campaign,
)
from chronicler.db.repository import (
    PlaythroughMismatchError,
    aggregate_campaign_counts,
    get_character_coa_json,
)
from chronicler.save import auto_track_candidates
from chronicler.save.adoption import (
    AlembicUpgradeFailed,
    ParseError,
    RakalyParseError,
    SaveNotFound,
    adopt_save,
)

log = logging.getLogger(__name__)

# ck3_chronicler-wvrm: a Library delete can race the fire-and-forget startup
# backfill thread (chronicler.db.backfills), whose read-only
# campaign_db_is_usable probe holds a momentary handle on the per-campaign
# DB. Windows then refuses to unlink a file with any open handle (WinError
# 32). The probe is brief, so a short bounded backoff clears it.
_DELETE_UNLINK_RETRIES = 12
_DELETE_UNLINK_BACKOFF_S = 0.05

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class CampaignCounts(BaseModel):
    """Aggregate counts for a campaign (ck3_chronicler-75c).

    Always populated as plain integers — zero when the table is empty,
    never null. Populating these requires opening the per-campaign DB,
    so callers opt in via ``?include_counts=true`` on the campaign
    endpoints to keep the default list cheap.

    Plan cozy-coalescing-shannon: the ``memories`` count was dropped
    with the rest of the LLM-memory pipeline. Library + campaign-
    overview cards now render two count cells (Souls + Vitæ) instead
    of three.
    """

    characters: int
    biographies: int


class CampaignResponse(BaseModel):
    """One campaign as exposed by the registry.

    ``closing_chronicle_blurb`` is the first paragraph of the LLM-generated
    closing chronicle when one exists (i.e. the user has invoked the
    closing ceremony via POST /complete). Used by the Library to render
    a meaningful card on the Completed shelf without paying the bytes
    cost of shipping the full chronicle in every list response — the
    full text stays at GET /closing-chronicle.
    """

    id: str
    name: str
    ck3_version: str | None
    created_at: str
    last_event_at: str | None
    archived: bool
    db_path: str
    counts: CampaignCounts | None = None
    closing_chronicle_blurb: str | None = None
    # ck3_chronicler-cqo: identity + state for the Library card.
    bookmark_date: str | None = None
    current_in_game_date: str | None = None
    current_player_character_id: int | None = None
    current_player_name: str | None = None
    current_player_nickname: str | None = None
    current_house_name: str | None = None
    founding_dynasty_name: str | None = None
    # ck3_chronicler-a3f: the player's resolved CoA (parsed JSON), surfaced
    # only when ``?include_counts=true`` opens the per-campaign DB anyway.
    # Null when the campaign has no current_player_character_id, when the
    # Character row hasn't been refreshed by save-tail since the 7ao
    # migration, or when the data is otherwise missing — the Library
    # falls back to the procedural Banner in those cases.
    current_player_coa_json: dict[str, Any] | None = None
    # ck3_chronicler-wdhe / 5r5t: campaign-overview welcome page stats.
    # Both prestige and piety are *lifetime* accrued counters (not current
    # balances). Gold is the current balance at last save tick (no clean
    # lifetime analogue in CK3 saves). ``current_dynasty_renown`` is null
    # in adventurer mode (no dynasty). Floats so we can carry CK3's
    # fractional values exactly; the FE rounds for display.
    current_player_gold: float | None = None
    current_player_prestige_lifetime: float | None = None
    current_player_piety_lifetime: float | None = None
    current_dynasty_renown: float | None = None
    # ck3_chronicler-bges: persisted last save-pair tick info — null
    # only when the campaign has never been ingested. Read by the
    # IngestActivityStrip + Library card per-row line for cold-load
    # display before any live save_pair_completed SSE frame arrives.
    last_save_filename: str | None = None
    last_save_ingested_at: str | None = None
    last_save_in_game_date: str | None = None
    last_tick_event_count: int | None = None
    # JSON object decoded from the registry's TEXT storage. Null when
    # never-ingested or when JSON parse fails (defensive).
    last_tick_event_type_tally: dict[str, int] | None = None
    # ck3_chronicler-9xa6: in-game date of the most recent ingested event
    # ("1126.5.18"). The Closing page reads this for "Closed" + Span; the
    # wall-clock ``last_event_at`` stays as the "last ingested" indicator.
    # NULL on pre-9xa6 campaigns until backfill_last_event_in_game_date
    # runs at startup or the next save-tail tick writes it.
    last_event_in_game_date: str | None = None


class CampaignRenameRequest(BaseModel):
    """ck3_chronicler-bly: body of POST /api/campaigns/{name}/rename."""

    name: str


class BaselineResetResponse(BaseModel):
    """ck3_chronicler-yv8q: response from DELETE /api/campaigns/{name}/baseline.

    ``deleted`` is True iff a baseline file existed and was removed.
    ``path`` is the resolved baseline path (always returned so the UI
    can surface what the chronicler would have touched even on the
    no-op branch). ``message`` is a short human-readable summary the
    button's toast renders verbatim.

    Idempotent: re-issuing the call against a campaign with no
    baseline returns ``deleted=False`` and a "no baseline to clear"
    message, status 200 — the user clicked the button and got a
    defined outcome, not an error.
    """

    deleted: bool
    path: str
    message: str


class CampaignRenameResponse(BaseModel):
    """ck3_chronicler-bly: response from POST /api/campaigns/{name}/rename.

    ``warning`` is non-null when the new name collides with another
    non-archived campaign — the rename still succeeds (registry doesn't
    enforce uniqueness; cqo's auto-detect keys on ``ck3_playthrough_id``
    so name collisions are cosmetic) but the UI can surface the
    collision so the user can pick a more distinguishing name."""

    campaign: CampaignResponse
    warning: str | None = None


@dataclass(frozen=True)
class _PerCampaignExtras:
    """ck3_chronicler-a3f: per-campaign data fetched together inside one
    session open. Counts are always populated; player_coa is None when
    the campaign has no current_player_character_id, when the row is
    absent, or when save-tail hasn't yet refreshed coa_json on the
    player's Character row."""

    counts: CampaignCounts
    player_coa: dict[str, Any] | None


def _tally_from_json(raw: str | None) -> dict[str, int] | None:
    """ck3_chronicler-bges: decode the JSON-encoded TEXT tally; defensive
    against corrupt blobs (return None rather than raising)."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(k): int(v) for k, v in parsed.items() if isinstance(v, (int, float))}


def _to_response(c: Campaign, extras: _PerCampaignExtras | None = None) -> CampaignResponse:
    return CampaignResponse(
        id=c.id,
        name=c.name,
        ck3_version=c.ck3_version,
        created_at=c.created_at,
        last_event_at=c.last_event_at,
        archived=c.archived,
        db_path=c.db_path,
        counts=extras.counts if extras is not None else None,
        closing_chronicle_blurb=blurb_from_chronicle(c.closing_chronicle),
        # ck3_chronicler-cqo: surface the registry's denormalised
        # identity / state so the Library card is one query per row.
        bookmark_date=c.bookmark_date,
        current_in_game_date=c.current_in_game_date,
        current_player_character_id=c.current_player_character_id,
        current_player_name=c.current_player_name,
        current_player_nickname=c.current_player_nickname,
        current_house_name=c.current_house_name,
        founding_dynasty_name=c.founding_dynasty_name,
        # ck3_chronicler-a3f: piggybacks on the per-campaign session
        # already open for counts; null when counts weren't requested.
        current_player_coa_json=extras.player_coa if extras is not None else None,
        # ck3_chronicler-wdhe: campaign-overview welcome-page stats.
        # Already on the registry row — no per-campaign session needed.
        current_player_gold=c.current_player_gold,
        current_player_prestige_lifetime=c.current_player_prestige_lifetime,
        current_player_piety_lifetime=c.current_player_piety,
        current_dynasty_renown=c.current_dynasty_renown,
        # ck3_chronicler-bges: last save-pair tick info from registry.
        last_save_filename=c.last_save_filename,
        last_save_ingested_at=c.last_save_ingested_at,
        last_save_in_game_date=c.last_save_in_game_date,
        last_tick_event_count=c.last_tick_event_count,
        last_tick_event_type_tally=_tally_from_json(c.last_tick_event_type_tally),
        # ck3_chronicler-9xa6: in-game date of the most recent event;
        # consumed by ClosingPage for "Closed" + Span.
        last_event_in_game_date=c.last_event_in_game_date,
    )


def _extras_for(campaign: Campaign, cache: EngineCache) -> _PerCampaignExtras:
    """ck3_chronicler-a3f: open the per-campaign session once and pull
    every datum the Library card needs from the per-campaign DB —
    counts (75c) plus the player's resolved CoA (a3f). Two queries
    against an already-open session, dominated by the counts work."""
    factory = cache.factory_for(campaign)
    coa: dict[str, Any] | None = None
    with factory() as session:
        agg = aggregate_campaign_counts(session)
        if campaign.current_player_character_id is not None:
            raw = get_character_coa_json(session, campaign.current_player_character_id)
            # Best-effort parse; a malformed row shouldn't poison the
            # response — fall through to None and let the frontend
            # render the procedural fallback. (Should never happen
            # since save-tail produces well-formed JSON, but defence
            # in depth is cheap here.)
            coa = parse_coa_json(raw)
    return _PerCampaignExtras(counts=CampaignCounts(**agg), player_coa=coa)


@router.get("", response_model=list[CampaignResponse])
def list_all_campaigns(
    request: Request,
    include_archived: bool = False,
    include_counts: bool = False,
) -> list[CampaignResponse]:
    """All campaigns in the registry. Pass ``?include_archived=true`` to
    include archived ones (default: omitted). Pass ``?include_counts=true``
    to attach per-campaign character/biography/memory counts (one extra
    DB open per campaign — the AppShell library view uses this; the bare
    list does not)."""
    cache = get_engine_cache(request)
    campaigns = list_campaigns(include_archived=include_archived, registry=cache.registry_path)
    if include_counts:
        return [_to_response(c, _extras_for(c, cache)) for c in campaigns]
    return [_to_response(c) for c in campaigns]


@router.get("/{name}", response_model=CampaignResponse)
def get_campaign_endpoint(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
    include_counts: bool = False,
) -> CampaignResponse:
    """Single campaign lookup by name. 404 if not found.

    Includes archived campaigns — the listing endpoint exposes them via
    ``?include_archived=true``, so the Library shelf can render sealed
    cards that this endpoint is now reachable for (audit F-03 /
    ck3_chronicler-1p4t).

    Pass ``?include_counts=true`` to attach character/biography/memory
    counts in the response (ck3_chronicler-75c)."""
    extras = _extras_for(campaign, get_engine_cache(request)) if include_counts else None
    return _to_response(campaign, extras)


@router.post("/{name}/rename", response_model=CampaignRenameResponse)
def rename_campaign_endpoint(
    request: Request,
    body: CampaignRenameRequest,
    campaign: Campaign = Depends(get_campaign),
) -> CampaignRenameResponse:
    """ck3_chronicler-bly: change a campaign's display name.

    400 when the new name is empty/whitespace; 404 when the source
    campaign isn't found (handled by the dependency). Otherwise always
    200 — the registry doesn't enforce name uniqueness, so a collision
    with another non-archived campaign produces a soft ``warning`` on
    the response rather than rejecting the rename."""
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="name must not be empty")

    cache = get_engine_cache(request)
    warning: str | None = None
    if new_name != campaign.name:
        # Look at active campaigns only — archived ones are hidden in the
        # default Library and the user is unlikely to notice the clash.
        for other in list_campaigns(registry=cache.registry_path):
            if other.id != campaign.id and other.name == new_name:
                warning = f"name collides with existing campaign {other.name!r}"
                break

    rename_campaign(campaign.id, new_name, registry=cache.registry_path)
    refreshed = get_campaign_by_id(campaign.id, registry=cache.registry_path)
    assert refreshed is not None  # we just renamed it
    return CampaignRenameResponse(campaign=_to_response(refreshed), warning=warning)


class _AdoptSaveRequest(BaseModel):
    save_path: str
    force_reset_playthrough: bool = False


class _AdoptSaveResponse(BaseModel):
    """Returned from POST /adopt-from-save.

    ``campaign`` is the resolved (existing or freshly-created) row,
    re-fetched after the alembic upgrade + import_save chain so the
    extras (counts, current_player_coa_json) are populated. The
    import counts surface what landed in the per-campaign DB on this
    call: 0/0/0 when adopting an already-imported save (idempotent),
    chars_upserted plus memories_inserted/duplicate on a fresh adopt.

    ck3_chronicler 2026-05-09: ``auto_tracked_count`` is the number of
    characters auto-added to the tracked-characters list at adoption
    time (player + immediate kin via auto_track_candidates). Without
    this, save-tail's per-tick filter sees an empty tracked set,
    silently advances the baseline, and the user gets no events in
    the watcher list — they had to remember to hit auto-track manually
    after adoption, with the in-game time elapsed lost from the
    chronicle. Already-tracked candidates are skipped so re-adopting
    the same save is idempotent on the tracked list."""

    campaign: CampaignResponse
    chars_upserted: int
    memories_inserted: int
    memories_duplicate: int
    auto_tracked_count: int = 0


@router.post("/adopt-from-save", response_model=_AdoptSaveResponse)
async def adopt_from_save_endpoint(body: _AdoptSaveRequest, request: Request) -> _AdoptSaveResponse:
    """ck3_chronicler-v2a: server-side path → resolved/created campaign.

    Pairs with the frontend '+ Adopt save' button on the Library page
    (and the wider nji watch-and-adopt flow). The server reads the file
    at ``save_path`` directly — no upload — so the path must be
    accessible to the chronicler process. Typical desktop usage points
    at ``%USERPROFILE%/Documents/Paradox Interactive/Crusader Kings III/
    save games/<file>.ck3``.

    Status codes:
    - 200: resolved (existing or new) campaign + import counts.
    - 400: rakaly / parse failure (corrupt save, wrong format, mod
      schema drift). The error message names the reason.
    - 404: ``save_path`` doesn't exist on the server filesystem.
    - 409: the save's ``playthrough_id`` matches an existing campaign
      that's pinned to a different playthrough. Re-send with
      ``force_reset_playthrough: true`` to overwrite.
    - 500: alembic upgrade or import_save raised something we don't
      know how to surface as a user-actionable error. Logged.

    Idempotent: a save already adopted into a campaign produces the
    same campaign row + 0/0/N counts (resolve_campaign_for_save
    matches by playthrough_id, alembic head is a no-op, import_save's
    insert_event_idempotent absorbs duplicate vanilla memories).

    audit F-04 / ck3_chronicler-shen: rakaly + alembic + import_save
    are now offloaded via asyncio.to_thread so the handler doesn't
    pin a threadpool worker for a 5-30s adoption.
    """
    import asyncio

    cache = get_engine_cache(request)
    save_path = Path(body.save_path)
    try:
        result = await asyncio.to_thread(
            adopt_save,
            save_path,
            registry_path=cache.registry_path,
            force_reset_playthrough=body.force_reset_playthrough,
        )
    except SaveNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (RakalyParseError, ParseError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ArchivedCampaignConflict as e:
        archived = e.archived_campaign
        raise HTTPException(
            status_code=409,
            detail={
                "code": "archived_campaign_conflict",
                "message": (
                    f"save's playthrough_id matches archived campaign "
                    f"{archived.name!r}; un-archive it to resume "
                    f"(POST /api/campaigns/{archived.name}/unarchive) "
                    f"or adopt under a new explicit campaign name"
                ),
                "archived_campaign_id": archived.id,
                "archived_campaign_name": archived.name,
            },
        ) from e
    except PlaythroughMismatchError as e:
        raise HTTPException(
            status_code=409,
            detail=(
                f"playthrough mismatch: {e}. Re-send with "
                "force_reset_playthrough=true to overwrite."
            ),
        ) from e
    except AlembicUpgradeFailed as e:
        log.exception("alembic upgrade failed during adopt-from-save")
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not result.import_result.success:
        # import_save returned a controlled failure (e.g. playthrough
        # mismatch surfaced inside its own session_scope). 500 because
        # the campaign row exists by now and the next call will see it
        # — surface the underlying error rather than swallowing it.
        log.warning(
            "adopt-from-save's import_save phase failed for %s: %s",
            save_path,
            result.import_result.error,
        )
        raise HTTPException(
            status_code=500,
            detail=f"import_save failed: {result.import_result.error}",
        )

    # Re-fetch the campaign row through the registry so the response's
    # extras (counts, coa) reflect everything import_save just wrote.
    refreshed = get_campaign_by_id(result.campaign.id, registry=cache.registry_path)
    assert refreshed is not None  # we just resolved/created it
    extras = _extras_for(refreshed, cache)

    # ck3_chronicler 2026-05-09: auto-track the player + immediate kin
    # at adoption time. Without this, save-tail's per-tick tracked-set
    # filter is empty, the diff layer prunes every event, and the
    # user's "What the watcher sees" list stays blank for as long as
    # they haven't manually run auto-track — observed live by the user
    # who lost a year of in-game time before noticing. auto_track_candidates
    # picks the player + spouse + children + parents (parse.py:1362),
    # which matches the user's ask ("the player and his immediate
    # kin") and stays inside the "core narrative orbit" the import
    # already built.
    auto_tracked_count = 0
    candidates = auto_track_candidates(result.parsed_snap)
    # ck3_chronicler-27ov.78 (audit L17): load the tracked set once instead
    # of an is_character_tracked() registry round-trip per candidate.
    tracked_ids = get_tracked_character_ids(refreshed.id, registry=cache.registry_path)
    for cid, note in candidates:
        if cid in tracked_ids:
            continue
        try:
            add_tracked_character(refreshed.id, cid, note=note, registry=cache.registry_path)
            tracked_ids.add(cid)
            auto_tracked_count += 1
        except Exception:  # noqa: BLE001 — observability; never fail adopt on this
            log.exception(
                "adopt-from-save: auto-track failed for char %d in %s",
                cid,
                refreshed.id,
            )

    # ck3_chronicler-nji: when chronicler dev is running in "no campaign
    # at startup" mode, the orchestrator registered a spawn helper on
    # app.state. Calling it here kicks off save_ingest for the freshly-
    # adopted campaign so subsequent autosaves stream into the new
    # campaign without a process restart. Idempotent if the campaign
    # was already being tailed (re-adopting an existing campaign is
    # a no-op spawn). Headless test contexts don't register the
    # spawner; getattr returns None and we skip silently.
    spawn = getattr(request.app.state, "spawn_save_ingest_for_campaign", None)
    if callable(spawn):
        try:
            spawn(refreshed.id, Path(refreshed.db_path))
        except Exception:
            log.exception(
                "spawn_save_ingest_for_campaign failed for %s after adopt-from-save",
                refreshed.id,
            )

    return _AdoptSaveResponse(
        campaign=_to_response(refreshed, extras),
        chars_upserted=result.import_result.chars_upserted,
        memories_inserted=result.import_result.memories_inserted,
        memories_duplicate=result.import_result.memories_duplicate,
        auto_tracked_count=auto_tracked_count,
    )


class _IngestStateResponse(BaseModel):
    """Issue #2: the resync snapshot behind the ingest strip.

    Mirrors the disk-derived half of the SSE ``cache_state`` frame. The
    strip was SSE-only, and ``cache_state`` is published on ingest
    *activity* — so a client that reconnected after a backend restart got
    no frame at all until the next autosave and kept rendering a count
    from a dead process. This is the backstop that lets it converge with
    no new save required; SSE stays the live source of truth.

    ``gc_drops_lifetime`` is absent by design — it lives in the running
    ingest loop's SaveCache instance and leaves nothing on disk, so a REST
    read cannot know it. A client merging this keeps whatever the stream
    last told it.
    """

    pending: int
    bytes: int


@router.get("/{name}/ingest-state", response_model=_IngestStateResponse)
def ingest_state_endpoint(
    name: str,  # noqa: ARG001 — path param; the campaign comes from the dependency
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> _IngestStateResponse:
    """Issue #2: current save-cache state for this campaign, read from disk.

    Side-effect free — it never constructs a SaveCache (that would mkdir
    and prune orphan .tmp files) and never parses a save. A campaign with
    no cache directory reads as ``pending=0``, which is the truth for one
    that has never ingested.

    Archived campaigns are included: their strip should read 0, and saying
    so beats a 404 the client would have to special-case.
    """
    from chronicler.db.registry import get_data_dir
    from chronicler.save.cache import cache_dir_for, snapshot_dir

    return _IngestStateResponse(**snapshot_dir(cache_dir_for(get_data_dir(), campaign.id)))


class _RefreshSnapshotsResponse(BaseModel):
    """ck3_chronicler-0l06: result of a manual recompute of per-character
    snapshot JSON columns from a cached save file."""

    refreshed: int
    save_seqno: int | None
    save_date: str | None
    detail: str


@router.post("/{name}/refresh-snapshots", response_model=_RefreshSnapshotsResponse)
def refresh_snapshots_endpoint(
    name: str,
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> _RefreshSnapshotsResponse:
    """ck3_chronicler-0l06: recompute per-character snapshot JSON
    columns (region_summary_json, save_snapshot_json, coa_json,
    great_cause_json) from the latest cached save for this campaign.

    The motivating case is the vcv2 sealed-campaign-resilience class:
    when backend code changes the snapshot logic (a new field in
    summarise_region, a smarter family-data resolver, a CoA-walk fix),
    save-tail naturally refreshes the persisted JSON on its next tick
    — but sealed campaigns don't tail, and active campaigns may be
    weeks between play sessions. Hitting this route walks
    ``~/Documents/chronicler/save-cache/<uuid>/``, picks the latest
    cached ``.ck3``, re-parses it, and replays the same per-character
    snapshot writes the save-tail loop performs.

    Idempotent. Cheap (one save parse + one DB transaction). Includes
    archived campaigns — that's the whole point.

    Returns:
    - ``refreshed=0, save_seqno=None`` when the cache is empty (the
      common sealed-and-aged case — the save was GC'd at seal time
      and we have nothing to re-parse). The campaign is then a
      museum piece; only briefing-time renderer fallbacks help.
    - ``refreshed=N`` (count of tracked characters whose rows we
      re-wrote) when a cached save was parsed.
    - ``refreshed=0, detail='no tracked characters'`` when the cache
      has saves but the campaign has no opt-in tracking (mirrors
      save-tail's silent-baseline behaviour).
    """
    cache = get_engine_cache(request)

    # Defer imports so non-refresh requests don't pay for them.
    # (get_tracked_character_ids is imported at module level — its
    # registry module is already loaded, so no deferral benefit.)
    from chronicler.db.registry import get_data_dir
    from chronicler.save.cache import cache_dir_for
    from chronicler.save.ingest import (
        _parse_save_at_with_raw,
        _refresh_tracked_characters,
    )

    cache_dir = cache_dir_for(get_data_dir(), campaign.id)
    if not cache_dir.is_dir():
        return _RefreshSnapshotsResponse(
            refreshed=0,
            save_seqno=None,
            save_date=None,
            detail=(
                "no save-cache directory for this campaign — sealed "
                "campaigns whose cache GC'd at close-out can't be "
                "refreshed; only briefing-time fallbacks help."
            ),
        )

    # Pick the highest-seqno .ck3 in the cache. Filenames are zero-
    # padded so lex sort = chronological. Skip .tmp files left by
    # interrupted prior runs.
    candidates = sorted(
        entry for entry in cache_dir.iterdir() if entry.suffix == ".ck3" and entry.is_file()
    )
    if not candidates:
        return _RefreshSnapshotsResponse(
            refreshed=0,
            save_seqno=None,
            save_date=None,
            detail=(
                "save-cache directory empty — nothing to recompute against. "
                "Adopt a current save into this campaign to repopulate."
            ),
        )
    latest = candidates[-1]
    try:
        seqno = int(latest.stem)
    except ValueError:
        seqno = None

    parsed = _parse_save_at_with_raw(latest)
    if parsed is None:
        raise HTTPException(
            status_code=500,
            detail=f"failed to parse cached save {latest.name}",
        )
    snap, raw_save_data = parsed

    tracked_set = get_tracked_character_ids(campaign.id, registry=cache.registry_path)
    if not tracked_set:
        return _RefreshSnapshotsResponse(
            refreshed=0,
            save_seqno=seqno,
            save_date=snap.current_date,
            detail=(
                "no tracked characters — `chronicler auto-track --campaign "
                f"{name}` first, then retry."
            ),
        )

    factory = cache.factory_for(campaign)
    _refresh_tracked_characters(
        snap=snap,
        tracked_set=tracked_set,
        factory=factory,
        raw_save_data=raw_save_data,
    )
    return _RefreshSnapshotsResponse(
        refreshed=len(tracked_set),
        save_seqno=seqno,
        save_date=snap.current_date,
        detail=(
            f"re-wrote per-character snapshots for {len(tracked_set)} "
            f"tracked character(s) from {latest.name} "
            f"(in-game date {snap.current_date})."
        ),
    )


@router.post("/{name}/unarchive", response_model=CampaignResponse)
def unarchive_campaign_endpoint(
    name: str,
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> CampaignResponse:
    """ck3_chronicler-w2s: re-activate a sealed campaign.

    Looks up by name *including* archived rows (the default ``get_campaign``
    dependency hides them). Returns the freshly-active CampaignResponse.
    404 if the name doesn't exist at all; idempotent for already-active
    campaigns (the UPDATE is a no-op on archived=0 rows).

    audit F-43 / ck3_chronicler-e2ye: this endpoint has no FE caller as
    of 2026-05-07 — the Library renders archived campaigns on the
    Completed shelf but provides no re-open affordance. Kept as a
    CLI-/curl-driven escape hatch (e.g. accidental sealing) until a
    Library-side "Reopen" affordance ships."""
    cache = get_engine_cache(request)
    unarchive_campaign(campaign.id, registry=cache.registry_path)
    refreshed = get_campaign_by_name(name, include_archived=True, registry=cache.registry_path)
    assert refreshed is not None  # row exists; we just updated it
    return _to_response(refreshed)


@router.delete("/{name}", status_code=204)
def delete_campaign_endpoint(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> None:
    """ck3_chronicler-ezpc: hard-delete a campaign.

    Removes the registry row + dependent tracked_characters and
    suppressed_event_kinds rows, plus the per-campaign SQLite file
    and the archive snapshot files in the archive dir when they exist.
    Irreversible — the FE wraps this in a confirm modal.

    Looks up by name *including* archived rows so a sealed campaign
    can also be deleted (the FE Library renders sealed campaigns on
    the Completed shelf; the user wants the same destructive
    affordance there).

    audit-style notes:
    - Closing the engine in the cache before unlinking the file
      releases the SQLAlchemy connection; on Windows an open file
      handle would otherwise wedge the unlink with a PermissionError.
    - Archive snapshot deletion is best-effort — a campaign that was
      never sealed has no snapshot pair, same defensive shape as the
      export side.
    """
    cache = get_engine_cache(request)

    # Step 1: drop the engine cache entry so SQLAlchemy releases its
    # file handle. Without this, the per-campaign DB file unlink fails
    # on Windows with PermissionError.
    cache.evict(campaign)

    # Step 2: remove the registry rows.
    delete_campaign(campaign.id, registry=cache.registry_path)

    # Step 3: unlink the per-campaign SQLite file. Tolerate missing —
    # an aborted import flow could leave an orphan registry row whose
    # db_path doesn't exist on disk; deleting that row should still
    # succeed. Retry on a transient Windows lock (see the
    # _DELETE_UNLINK_* note above): a concurrent startup backfill thread
    # may briefly hold a handle, and Windows blocks unlink with WinError 32.
    db_path = Path(campaign.db_path)
    for attempt in range(_DELETE_UNLINK_RETRIES):
        try:
            if db_path.exists():
                db_path.unlink()
            break
        except OSError as exc:
            if attempt + 1 >= _DELETE_UNLINK_RETRIES:
                log.warning(
                    "ezpc: could not unlink per-campaign DB %s after %d attempts: %s",
                    db_path,
                    _DELETE_UNLINK_RETRIES,
                    exc,
                )
            else:
                time.sleep(_DELETE_UNLINK_BACKOFF_S)

    # Step 4: best-effort delete of the archive snapshot pair, if any.
    try:
        from chronicler.sync.archive_export import (
            archive_sync_enabled,
            archived_campaigns_dir,
        )

        if archive_sync_enabled():
            archive_dir = archived_campaigns_dir()
            for suffix in (".db", ".json"):
                snap = archive_dir / f"{campaign.id}{suffix}"
                if snap.exists():
                    snap.unlink()
    except Exception as exc:  # noqa: BLE001 — best-effort cleanup
        log.warning(
            "ezpc: archive-snapshot cleanup for %s failed: %s",
            campaign.id,
            exc,
        )

    # ck3_chronicler-wvrm: Step 5 — record a deletion tombstone and push it
    # so the delete propagates to the user's other machines (otherwise they
    # re-export this campaign on their next startup and resurrect it).
    # Best-effort: a tombstone failure must not fail the local delete.
    try:
        from chronicler.sync import tombstone_campaign

        tombstone_campaign(campaign)
    except Exception as exc:  # noqa: BLE001 — best-effort propagation
        log.warning("wvrm: tombstone for %s failed: %s", campaign.id, exc)


@router.delete("/{name}/baseline", response_model=BaselineResetResponse)
def reset_baseline_endpoint(
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> BaselineResetResponse:
    """ck3_chronicler-yv8q: clear a wedged save-tail baseline.

    Removes ``<campaign_db>.baseline.json`` so save-tail re-baselines on
    the next matching save. Use this when 'playthrough mismatch' errors
    appear in the boot log and you can't restart the chronicler without
    losing state — the recovery dance was previously a manual file
    deletion + process restart.

    No event data is lost: events live in the per-campaign DB, not the
    baseline JSON. The baseline is a memo of the last SaveSnapshot
    save-tail diffed against; rebuilding it from the next save is the
    same path the chronicler walks on first adoption.

    Idempotent: returns ``deleted=False`` with a "no baseline to clear"
    message when the file is already absent. Includes archived
    campaigns via :func:`get_campaign_including_archived` — sealed
    campaigns can occasionally retain stale baselines that interfere
    with un-archive flows.
    """
    from chronicler.save.baseline import (
        baseline_path_for,
        delete_baseline_if_exists,
    )

    path = baseline_path_for(Path(campaign.db_path))
    deleted = delete_baseline_if_exists(path)
    log.info(
        "yv8q: reset baseline for campaign %s (%s): deleted=%s path=%s",
        campaign.name,
        campaign.id,
        deleted,
        path,
    )
    return BaselineResetResponse(
        deleted=deleted,
        path=str(path),
        message="cleared" if deleted else "no baseline to clear",
    )

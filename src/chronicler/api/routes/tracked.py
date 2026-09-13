"""Tracked-character routes — the v0.7 settings/library list view.

GET /api/campaigns/{name}/tracked returns a join of:
  - registry.tracked_characters (note, role, added_at)
  - per-campaign characters       (first_name, nickname)
  - per-campaign biography aggregations (count + monthly spend)

Tracked-list size in practice is small (V02 testing showed users add a
handful — player + spouse + heirs + close rivals — typically <30 per
campaign), so the per-character DB lookup is acceptable. Activity stats
come from a single grouped query (``aggregate_tracked_summary``) to
avoid N+1.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from chronicler.api.dependencies import (
    get_campaign,
    get_campaign_including_archived,
    get_engine_cache,
    get_session,
    get_session_including_archived,
)
from chronicler.api.serializers import (
    extract_family_ids,
    parse_coa_json,
    parse_family_data,
)
from chronicler.db.engine import session_scope
from chronicler.db.models import Character
from chronicler.db.registry import (
    Campaign,
    add_tracked_character,
    bump_tracked_character,
    get_campaign_by_id,
    get_tracked_character,
    get_tracked_character_ids,
    list_tracked_characters,
    pause_tracked_character,
    remove_tracked_character,
    resume_tracked_character,
    set_auto_track_rules,
)
from chronicler.db.repository import (
    PlaythroughMismatchError,
    aggregate_tracked_summary,
    assert_playthrough_or_pin,
    get_character,
)

router = APIRouter(prefix="/api/campaigns/{name}", tags=["tracked"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class TrackedResponse(BaseModel):
    """One tracked character with activity stats (ck3_chronicler-4cl).

    Joins registry tracked_characters + per-campaign characters +
    biography aggregations. ``role`` comes from the registry and is
    nullable; the activity counts are always non-negative integers
    (0 when the character has no rows yet).

    Plan cozy-coalescing-shannon: ``memory_count`` was dropped with
    the rest of the LLM-memory pipeline. ck3_chronicler-nx2x: the dead
    ``preferred_provider`` / ``preferred_model`` fields were retired with
    the multi-provider fiction (Claude Code is the single backend).
    Tracked rows now surface biography count + this-month token spend only.
    """

    character_id: int
    first_name: str | None
    nickname: str | None
    role: str | None
    added_at: str
    biography_count: int
    monthly_token_spend: int
    # vysp.10: pause / bump state. paused_at non-null = scheduler skips;
    # bumped_at non-null = manual priority lift (scheduler picks up first).
    paused_at: str | None = None
    bumped_at: str | None = None
    # ck3_chronicler-4y0v (slice 1, 2026-05-08): persisted CoA so the
    # TrackedPage shield matches the chronicle folio. Same payload-cost
    # tradeoff as on CharacterSummary; tracked sets are small (~30).
    coa_json: dict[str, Any] | None = None


class AddTrackedRequest(BaseModel):
    """ck3_chronicler-ogi: body of POST /api/campaigns/{name}/tracked.

    ``character_id`` is the only required field; ``note`` and ``role``
    mirror the registry helper's optional kwargs so the UI's add-character
    dialog can persist a label like "rival" or "heir-apparent" without a
    follow-up update call.
    """

    character_id: int
    note: str | None = None
    role: str | None = None


class AutoTrackRulesBody(BaseModel):
    """ck3_chronicler-gw16: per-campaign auto-track rule flags.

    All three fields are nullable in the request body so the UI can do
    a partial PUT (toggle one checkbox without re-sending the others).
    None on read means "field never set" — the GET endpoint resolves
    each None against the legacy default before responding so the FE
    always has concrete bools to render.

    ``include_county_vassals`` is reserved for gw16.2 — the snapshot
    doesn't yet expose a character→liege relation. Persisting True
    on this field today is harmless (auto_track_candidates ignores it)
    but will start producing rows once the underlying support lands.
    """

    include_heirs: bool | None = None
    include_spouses: bool | None = None
    include_grandchildren: bool | None = None
    include_county_vassals: bool | None = None


class SuggestedCandidateResponse(BaseModel):
    """ck3_chronicler-gw16: one row in the Tracked-page rail's
    suggested-souls list. Living blood relative of the player who isn't
    yet tracked. Mirrors CharacterSummary's display fields so the FE can
    reuse its row component, but adds a relation hint and persists the
    short note copy that the one-tap Track button hands to add_tracked.
    """

    ck3_id: int
    first_name: str | None
    nickname: str | None
    relation: str  # "child" | "grandchild" | "spouse" | …
    birth_date: str | None
    death_date: str | None
    coa_json: dict[str, Any] | None = None


class AutoTrackResponse(BaseModel):
    """ck3_chronicler-ogi: response from POST /api/campaigns/{name}/tracked/auto-track.

    ``added`` is the freshly-added rows in the same TrackedResponse shape
    the list endpoint returns, so the UI can splice them into its cache
    without a refetch. ``already_tracked`` lists candidate IDs whose
    tracked-row already existed (auto-track is idempotent — re-running
    after a save tick that grew the family is the expected case).
    ``save_path`` echoes the file the candidates were drawn from so the
    UI can show "tracked from autosave_exit.ck3" copy.
    """

    added: list[TrackedResponse]
    already_tracked: list[int]
    save_path: str


def _current_month_yyyy_mm() -> str:
    """``YYYY-MM`` for "this calendar month, UTC". Stable across the
    minute the request lands in — month rollover at midnight UTC is
    intentional (matches generated_at which is UTC ISO)."""
    return datetime.now(UTC).strftime("%Y-%m")


@router.get("/tracked", response_model=list[TrackedResponse])
def list_tracked(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> list[TrackedResponse]:
    """Tracked characters for a campaign + their activity stats.

    Ordered by ``added_at`` ascending (the order they were added — the
    UI shows oldest at the top so the player → heirs progression reads
    chronologically).

    A tracked entry whose character isn't yet in the per-campaign DB
    (added before save-tail saw them) is included with ``first_name``
    and ``nickname`` as ``None``. The frontend can render that as a
    "pending" state.
    """
    cache = get_engine_cache(request)
    rows = list_tracked_characters(campaign.id, registry=cache.registry_path)

    char_ids = [r.character_id for r in rows]
    summary = aggregate_tracked_summary(
        session,
        char_ids,
        current_month_yyyy_mm=_current_month_yyyy_mm(),
    )

    # audit F-21 / ck3_chronicler-m0wy: was firing one get_character()
    # per tracked row; now one IN-list query for the lot. Tracked sets
    # are typically small (~30) but this matters more on Library
    # navigation where the page mounts/unmounts often.
    chars: dict[int, Character] = {}
    if char_ids:
        loaded = (
            session.execute(select(Character).where(Character.ck3_id.in_(char_ids))).scalars().all()
        )
        chars = {c.ck3_id: c for c in loaded}

    out: list[TrackedResponse] = []
    for r in rows:
        char = chars.get(r.character_id)
        stats = summary.get(r.character_id, {})
        # ck3_chronicler-4y0v slice 1: best-effort CoA parse so the
        # TrackedPage row renders the persisted shield instead of a
        # procedural seed. Malformed JSON (defensive — save-tail
        # produces well-formed) → None → procedural fallback.
        coa = parse_coa_json(char.coa_json) if char is not None else None
        out.append(
            TrackedResponse(
                character_id=r.character_id,
                first_name=char.first_name if char else None,
                nickname=char.nickname if char else None,
                role=r.role,
                added_at=r.added_at,
                biography_count=stats.get("biography_count", 0),
                monthly_token_spend=stats.get("monthly_token_spend", 0),
                paused_at=r.paused_at,
                bumped_at=r.bumped_at,
                coa_json=coa,
            )
        )
    return out


# vysp.10 — pause / resume / bump endpoints. The Tracked page needs the
# four-state UI (live / paused / queued / biography) the brief calls out;
# these routes are how the buttons in that UI mutate state. Each returns
# the updated TrackedResponse for the affected character so the frontend
# can patch its TanStack Query cache without an extra GET round-trip.


def _build_one_response(
    *,
    request: Request,
    session: Session,
    campaign: Campaign,
    character_id: int,
) -> TrackedResponse:
    """Build a single TrackedResponse — used by the pause/resume/bump
    routes after they mutate state, so the client can patch its cache.

    audit F-21 / ck3_chronicler-m0wy: was scanning the full tracked
    list to find one row; now hits the row directly via
    get_tracked_character()."""
    cache = get_engine_cache(request)
    row = get_tracked_character(campaign.id, character_id, registry=cache.registry_path)
    if row is None:
        raise HTTPException(status_code=404, detail="character not tracked")

    summary = aggregate_tracked_summary(
        session,
        [character_id],
        current_month_yyyy_mm=_current_month_yyyy_mm(),
    )
    stats = summary.get(character_id, {})
    char = get_character(session, character_id)

    coa = parse_coa_json(char.coa_json) if char is not None else None

    return TrackedResponse(
        character_id=character_id,
        first_name=char.first_name if char else None,
        nickname=char.nickname if char else None,
        role=row.role,
        added_at=row.added_at,
        biography_count=stats.get("biography_count", 0),
        monthly_token_spend=stats.get("monthly_token_spend", 0),
        paused_at=row.paused_at,
        bumped_at=row.bumped_at,
        coa_json=coa,
    )


@router.post("/tracked/{character_id}/pause", response_model=TrackedResponse)
def pause_tracked(
    character_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
) -> TrackedResponse:
    """Pause biography auto-generation for a tracked character."""
    cache = get_engine_cache(request)
    if not pause_tracked_character(campaign.id, character_id, registry=cache.registry_path):
        raise HTTPException(status_code=404, detail="character not tracked")
    return _build_one_response(
        request=request, session=session, campaign=campaign, character_id=character_id
    )


@router.post("/tracked/{character_id}/resume", response_model=TrackedResponse)
def resume_tracked(
    character_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
) -> TrackedResponse:
    """Resume a paused tracked character — clears ``paused_at``."""
    cache = get_engine_cache(request)
    if not resume_tracked_character(campaign.id, character_id, registry=cache.registry_path):
        raise HTTPException(status_code=404, detail="character not tracked")
    return _build_one_response(
        request=request, session=session, campaign=campaign, character_id=character_id
    )


@router.post("/tracked/{character_id}/bump", response_model=TrackedResponse)
def bump_tracked(
    character_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
) -> TrackedResponse:
    """Bump a tracked character to the head of the biography queue."""
    cache = get_engine_cache(request)
    if not bump_tracked_character(campaign.id, character_id, registry=cache.registry_path):
        raise HTTPException(status_code=404, detail="character not tracked")
    return _build_one_response(
        request=request, session=session, campaign=campaign, character_id=character_id
    )


# --- ck3_chronicler-ogi: add / remove / auto-track from the GUI -----------


@router.post("/tracked", response_model=TrackedResponse, status_code=201)
def add_tracked(
    body: AddTrackedRequest,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
) -> TrackedResponse:
    """Add a single character to the tracked list (ck3_chronicler-ogi).

    Idempotent: re-adding an already-tracked character updates the
    optional fields (``note``, ``role``) without resetting ``added_at``
    — same semantics as the underlying registry helper. The character
    does not need to exist in the per-campaign DB; if it doesn't, the
    response's ``first_name`` / ``nickname`` are ``None`` until save-tail
    refreshes the row.
    """
    cache = get_engine_cache(request)
    add_tracked_character(
        campaign.id,
        body.character_id,
        note=body.note,
        role=body.role,
        registry=cache.registry_path,
    )
    return _build_one_response(
        request=request,
        session=session,
        campaign=campaign,
        character_id=body.character_id,
    )


@router.delete("/tracked/{character_id}", status_code=204)
def delete_tracked(
    character_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
) -> None:
    """Remove a character from the tracked list (ck3_chronicler-ogi).

    Persisted biographies are preserved — untracking only stops future
    auto-generation. 404 when the character wasn't tracked
    (mirrors pause/resume/bump's behaviour for unknown ids)."""
    cache = get_engine_cache(request)
    if not remove_tracked_character(campaign.id, character_id, registry=cache.registry_path):
        raise HTTPException(status_code=404, detail="character not tracked")


@router.post("/tracked/auto-track", response_model=AutoTrackResponse)
async def auto_track(
    request: Request,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
    force_reset_playthrough: bool = False,
) -> AutoTrackResponse:
    """Parse the latest CK3 save and add player + family (ck3_chronicler-ogi).

    Mirrors ``chronicler auto-track`` but driven from the GUI. Reads the
    latest autosave from the configured CK3 save directory, asserts the
    save's ``playthrough_id`` matches the campaign's pin (use
    ``?force_reset_playthrough=true`` to override — the explicit-consent
    flag for switching a campaign DB to a different CK3 playthrough),
    then adds player + spouse + children + parents.

    503 when no autosave is reachable. 409 when the playthrough_id
    differs and ``force_reset_playthrough`` is not set.

    audit F-04 / ck3_chronicler-shen: rakaly subprocess (0.4–10s on big
    saves) + parse_save are now offloaded via asyncio.to_thread so the
    handler doesn't pin a threadpool worker for the duration.
    """
    import asyncio

    from chronicler.config import get_ck3_save_dir
    from chronicler.save import (
        DEFAULT_SAVE_PATTERN,
        auto_track_candidates,
        convert_save_to_json,
        latest_save,
        parse_save,
        resolve_auto_track_rules,
    )

    save_dir = get_ck3_save_dir()
    save_path = latest_save(save_dir, DEFAULT_SAVE_PATTERN)
    if save_path is None or not save_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                f"no CK3 autosave found in {save_dir} — launch CK3 and let "
                "it autosave at least once, or pass --save in the CLI"
            ),
        )

    raw = await asyncio.to_thread(convert_save_to_json, save_path)
    snap = await asyncio.to_thread(parse_save, raw)
    if snap.player_character_id is None:
        raise HTTPException(
            status_code=400,
            detail="save has no player character (observer mode?)",
        )

    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)
    with session_scope(factory) as ps_session:
        try:
            assert_playthrough_or_pin(
                ps_session,
                observed=snap.playthrough_id,
                allow_reset=force_reset_playthrough,
            )
        except PlaythroughMismatchError as e:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{e}. Pass ?force_reset_playthrough=true to switch the "
                    "campaign's pinned playthrough."
                ),
            ) from None

    # ck3_chronicler-gw16: honour the campaign's per-campaign rules.
    # Resolved via the shared resolver (ck3_chronicler-vlw3) so the GUI
    # and the `chronicler auto-track` CLI apply identical rules.
    # Malformed JSON falls back to defaults — a corrupted blob never
    # blocks auto-track.
    rules = resolve_auto_track_rules(campaign.auto_track_rules)

    candidates = auto_track_candidates(snap, rules=rules)
    added_ids: list[int] = []
    already: list[int] = []
    # ck3_chronicler-27ov.78 (audit L17): one tracked-set read, not an
    # is_character_tracked() registry round-trip per candidate.
    tracked_ids = get_tracked_character_ids(campaign.id, registry=cache.registry_path)
    for cid, note in candidates:
        if cid in tracked_ids:
            already.append(cid)
            continue
        add_tracked_character(campaign.id, cid, note=note, registry=cache.registry_path)
        tracked_ids.add(cid)
        added_ids.append(cid)

    added_responses = [
        _build_one_response(
            request=request,
            session=session,
            campaign=campaign,
            character_id=cid,
        )
        for cid in added_ids
    ]
    return AutoTrackResponse(
        added=added_responses,
        already_tracked=already,
        save_path=str(save_path),
    )


# --- ck3_chronicler-gw16: auto-track rules + suggested candidates -----------


def _load_rules(campaign: Campaign) -> dict[str, bool]:
    """Resolve a campaign's auto-track rules to a complete dict.

    Thin wrapper over the shared resolver (ck3_chronicler-vlw3) so the
    GET/PUT rules endpoints, the auto-track route, and the CLI all share
    one rule-resolution truth. Returns concrete bools for every known
    flag; malformed JSON falls back to defaults.
    """
    from chronicler.save import resolve_auto_track_rules

    return resolve_auto_track_rules(campaign.auto_track_rules)


@router.get("/auto-track-rules", response_model=AutoTrackRulesBody)
def get_auto_track_rules(
    campaign: Campaign = Depends(get_campaign),
) -> AutoTrackRulesBody:
    """Return the campaign's auto-track rule flags (gw16).

    Always returns concrete bools — None on a flag means "field not set
    in the persisted JSON," resolved against the legacy defaults
    server-side so the FE just renders three checkboxes."""
    resolved = _load_rules(campaign)
    return AutoTrackRulesBody(
        include_heirs=resolved.get("include_heirs", True),
        include_spouses=resolved.get("include_spouses", True),
        include_grandchildren=resolved.get("include_grandchildren", True),
        include_county_vassals=resolved.get("include_county_vassals", False),
    )


@router.put("/auto-track-rules", response_model=AutoTrackRulesBody)
def update_auto_track_rules(
    body: AutoTrackRulesBody,
    request: Request,
    campaign: Campaign = Depends(get_campaign),
) -> AutoTrackRulesBody:
    """Patch the campaign's auto-track rule flags (gw16).

    Accepts a partial body: any field set to a bool is persisted,
    fields left as None retain their current value (or the default if
    none was set). The response is the post-write resolved state."""
    cache = get_engine_cache(request)
    current = _load_rules(campaign)
    if body.include_heirs is not None:
        current["include_heirs"] = body.include_heirs
    if body.include_spouses is not None:
        current["include_spouses"] = body.include_spouses
    if body.include_grandchildren is not None:
        current["include_grandchildren"] = body.include_grandchildren
    if body.include_county_vassals is not None:
        current["include_county_vassals"] = body.include_county_vassals
    set_auto_track_rules(campaign.id, json.dumps(current), registry=cache.registry_path)
    return AutoTrackRulesBody(
        include_heirs=current.get("include_heirs", True),
        include_spouses=current.get("include_spouses", True),
        include_grandchildren=current.get("include_grandchildren", True),
        include_county_vassals=current.get("include_county_vassals", False),
    )


@router.get(
    "/tracked/suggested-candidates",
    response_model=list[SuggestedCandidateResponse],
)
def suggested_candidates(
    request: Request,
    character_id: int | None = None,
    limit: int = 5,
    campaign: Campaign = Depends(get_campaign),
    session: Session = Depends(get_session),
) -> list[SuggestedCandidateResponse]:
    """Surface untracked-but-near-the-player candidates (gw16).

    Walks the player's persisted family_data (children + grandchildren
    + spouses), filters to living characters not yet tracked, and
    returns up to ``limit`` rows. Reuses the same family_data the
    LineagePage walker reads — no new traversal logic.

    ``character_id`` defaults to the campaign's
    ``current_player_character_id``; pass an override to suggest
    candidates for any other tracked character."""
    cache = get_engine_cache(request)
    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    # Re-read the campaign to get a fresh registry row — auto_track_rules
    # may have been updated this request, but that's not relevant; we
    # read it for current_player_character_id which is stable.
    fresh = get_campaign_by_id(campaign.id, registry=cache.registry_path)
    target_id = character_id
    if target_id is None and fresh is not None:
        target_id = fresh.current_player_character_id
    if target_id is None:
        return []

    root = get_character(session, target_id)
    if root is None:
        return []

    fam = parse_family_data(root.save_snapshot_json)
    # Collect (relation, ck3_id) tuples; preserve insertion order so the
    # tightest relations (children/spouses) sort to the top before slicing.
    pairs: list[tuple[str, int]] = []
    seen: set[int] = set()

    def _push(relation: str, raw: object) -> None:
        for cid in extract_family_ids(raw):
            if cid in seen or cid == target_id:
                continue
            seen.add(cid)
            pairs.append((relation, cid))

    _push("child", fam.get("child"))
    _push("spouse", fam.get("primary_spouse"))
    _push("spouse", fam.get("spouse"))
    _push("spouse", fam.get("concubine"))
    _push("spouse", fam.get("betrothed"))

    # Grandchildren via children's children — one bulk lookup so we
    # don't pay an N+1 round-trip even for very large broods.
    child_ids = [cid for rel, cid in pairs if rel == "child"]
    if child_ids:
        children = (
            session.execute(select(Character).where(Character.ck3_id.in_(child_ids)))
            .scalars()
            .all()
        )
        for child in children:
            child_fam = parse_family_data(child.save_snapshot_json)
            _push("grandchild", child_fam.get("child"))

    # Untracked + living filter. Bulk-load all candidate Character rows
    # to get death_date + coa_json without an N+1.
    cand_ids = [cid for _rel, cid in pairs]
    chars: dict[int, Character] = {}
    if cand_ids:
        loaded = (
            session.execute(select(Character).where(Character.ck3_id.in_(cand_ids))).scalars().all()
        )
        chars = {c.ck3_id: c for c in loaded}

    out: list[SuggestedCandidateResponse] = []
    # ck3_chronicler-27ov.78 (audit L17): one tracked-set read instead of an
    # is_character_tracked() registry round-trip per candidate pair.
    tracked_ids = get_tracked_character_ids(campaign.id, registry=cache.registry_path)
    for relation, cid in pairs:
        if cid in tracked_ids:
            continue
        char = chars.get(cid)
        if char is None or char.death_date is not None:
            # Skip dead or never-persisted candidates — the FE only
            # surfaces these to add to the active flock.
            continue
        out.append(
            SuggestedCandidateResponse(
                ck3_id=cid,
                first_name=char.first_name,
                nickname=char.nickname,
                relation=relation,
                birth_date=char.birth_date,
                death_date=char.death_date,
                coa_json=parse_coa_json(char.coa_json),
            )
        )
        if len(out) >= limit:
            break
    return out

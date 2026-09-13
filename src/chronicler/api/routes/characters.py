"""Character routes — list, detail, events, biography."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    # audit F-19: only needed for type hints on _summary; keep the
    # ORM model out of the runtime import surface.
    from chronicler.db.models import Character

from chronicler.api.dependencies import (
    get_campaign_including_archived,
    get_session_including_archived,
    resolve_or_build_scheduler,
)
from chronicler.api.models import DEFAULT_LIMIT, MAX_LIMIT, PrimaryTitleSummary
from chronicler.api.serializers import (
    parse_coa_json,
    parse_held_titles,
    parse_primary_title,
)
from chronicler.cost import compute_generation_cost
from chronicler.db.registry import Campaign
from chronicler.db.repository import (
    compute_played_character_ids,
    get_character,
    get_latest_biography_for_character,
    list_characters_by_ids,
    list_characters_unfiltered,
    list_events_for_character,
    search_characters_by_name,
)

router = APIRouter(prefix="/api/campaigns/{name}", tags=["characters"])


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class CharacterSummary(BaseModel):
    """Lightweight character row for list views."""

    ck3_id: int
    first_name: str | None
    dynasty_name: str | None
    birth_date: str | None
    death_date: str | None
    # ck3_chronicler-4y0v (slice 1, 2026-05-08): persisted CoA so the
    # codex / tracked / search browse surfaces can render the same
    # heraldry the chronicle folio shows. None for characters without
    # a resolved CoA yet (untracked at parse time, family link-broken
    # at house-resolve time, etc.); FE falls through to the procedural
    # seeded shield in those cases via HeraldryWithFallback. Payload
    # ~100-500 bytes per row, so 250-row codex page adds ~50-125 KB.
    coa_json: dict[str, Any] | None = None
    # ck3_chronicler-w1t3 (2026-05-08): true when this character has
    # been (or currently is) the campaign player, derived per-request
    # by walking title_acquired events backward from the registry's
    # current_player_character_id (see compute_played_character_ids).
    # Lets the Codex of Souls highlight Thrugot, Svend, Christoffer,
    # ... regardless of which one is the latest player.
    is_played: bool = False


class EventResponse(BaseModel):
    """One event row + JSON payload."""

    id: int
    type: str
    date: str
    date_iso: str | None
    wall_clock_at: str
    schema_version: int
    payload: dict[str, Any]


class CharacterDetail(BaseModel):
    """Full character record + chronological event list."""

    ck3_id: int
    first_name: str | None
    dynasty_name: str | None
    house_name: str | None
    nickname: str | None
    female: bool | None = None
    birth_date: str | None
    death_date: str | None
    culture: str | None
    faith: str | None
    events: list[EventResponse]
    # ck3_chronicler-4y0v slice 1 follow-up: persisted CoA so the Codex
    # right-pane shield matches the chronicle folio. The FE consumer
    # (CodexPage) was already reading c.coa_json — adding the field
    # here closes the latent type/runtime gap.
    coa_json: dict[str, Any] | None = None
    # ck3_chronicler-zx2l: highest-tier directly-held title. The folio
    # aside renders this under the dynasty line with the tier crown.
    # NULL for landless characters or those who never held a title
    # during any observed tick.
    primary_title: PrimaryTitleSummary | None = None
    # ck3_chronicler-9ngy: EVERY title held at death (grandest-first). The
    # sidebar lists them all so a multi-realm ruler isn't reduced to a
    # single "King of X". Empty list when no titles were ever held;
    # held_titles[0] == primary_title.
    held_titles: list[PrimaryTitleSummary] = []


class BiographyResponse(BaseModel):
    """One persisted biography (latest version unless otherwise stated)."""

    id: int
    character_id: int
    version: int
    body: str
    prompt_template_version: str
    provider: str
    generated_at: str
    events_through_event_id: int | None
    prompt_tokens: int | None
    completion_tokens: int | None


class RegenerateBiographyResponse(BaseModel):
    """ck3_chronicler-7gw: response from POST /characters/{ck3_id}/biography/regenerate.

    202 Accepted — generation runs in the background under the scheduler's
    single-lane semaphore. ``item_id`` correlates with the SSE narrative_*
    frames + the queue snapshot, so the UI can light up its activity
    surfaces as soon as the LLM kicks off. ``item_id`` is ``None`` only
    in the (test-only) edge case where the scheduler has no queue state
    attached — production callers always get an int.
    """

    character_id: int
    item_id: int | None


class BiographyCostEstimateResponse(BaseModel):
    """ck3_chronicler-7bi5 (revived by cs1o): token + USD estimate
    before regenerating.

    The numbers are an estimate, not a quote. ``method`` reports how
    they were derived: ``from_prior_generation`` reuses the prior
    biography's token columns (most accurate — only available when the
    character has at least one prior biography, and cache-aware when the
    prior row carries a breakdown); ``estimate`` falls back to
    event-count heuristics for first-time generations.

    ``would_route_to`` carries the destination transport's kind-resolved
    name string (e.g. ``claude-code:claude-opus-4-7`` or
    ``anthropic:claude-sonnet-4-6``) so the UI can label the cost cell
    with the right tier; ``model`` is just the model portion for compact
    display. ``pool_billed`` is True when the cost draws from the
    subscription's monthly programmatic credit pool (claude-code
    transport) rather than a payment card.
    """

    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    est_usd: float
    kind: str  # "biography" | "biography_woven"
    would_route_to: str | None
    model: str | None
    method: str  # "from_prior_generation" | "estimate"
    pool_billed: bool


class CoaDefinitionResponse(BaseModel):
    """audit F-39 / ck3_chronicler-tjql: response model for
    GET /api/campaigns/{name}/characters/{ck3_id}/coa. The persisted
    ``coa_json`` blob is recursively-shaped (sub-shields, emblem
    arrays); we keep the schema permissive (extra="allow", values
    typed as ``Any``) rather than mirror the full CK3 grammar in
    Pydantic — but the response_model still validates that the body
    is a dict and gives the FE a stable type name in OpenAPI.

    Pre-fix: the route returned dict[str, object] verbatim from
    json.loads, so a malformed coa_json row would have been served
    unchanged and crashed the renderer. With this envelope, malformed
    rows fail validation server-side and return 500 with a logged
    cause instead of poisoning the client."""

    model_config = {"extra": "allow"}


class CoaHistoryEntry(BaseModel):
    """One transition in the CoA timeline (ck3_chronicler-7b8d)."""

    observed_at: str
    coa: dict[str, Any]


class CoaHistoryResponse(BaseModel):
    """Append-only history of resolved-CoA changes for one character
    (audit F-39). The character's chronological CoA timeline."""

    entries: list[CoaHistoryEntry]


def _summary(c: Character, *, is_played: bool = False) -> CharacterSummary:
    # ck3_chronicler-4y0v (slice 1, 2026-05-08): include the persisted
    # coa_json so list-view surfaces (Codex, Tracked, Search) render
    # the same heraldry the chronicle folio shows. Best-effort parse
    # — a malformed row doesn't poison the response; FE falls through
    # to the procedural shield via HeraldryWithFallback.
    # ck3_chronicler-27ov.45: the local copy skipped the isinstance(dict)
    # guard the other route modules apply; the shared parser restores it.
    return CharacterSummary(
        ck3_id=c.ck3_id,
        first_name=c.first_name,
        dynasty_name=c.dynasty_name,
        birth_date=c.birth_date,
        death_date=c.death_date,
        coa_json=parse_coa_json(c.coa_json),
        is_played=is_played,
    )


def _event_response(e: Any) -> EventResponse:
    return EventResponse(
        id=e.id,
        type=e.event_type,
        date=e.event_date,
        date_iso=e.event_date_iso,
        wall_clock_at=e.wall_clock_at,
        schema_version=e.schema_version,
        payload=json.loads(e.payload_json),
    )


@router.get("/characters", response_model=list[CharacterSummary])
def list_characters(
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    q: str | None = None,
    ids: str | None = None,
) -> list[CharacterSummary]:
    """Paginated character list for a campaign.

    Default page size is 100. The campaign DB can have tens of thousands
    of characters (post-import-save), so callers should paginate.

    ``q`` (ck3_chronicler-4i33) filters by name — case-insensitive
    substring match against first_name / dynasty_name / nickname. When
    set, results are ranked: exact (case-insensitive) first_name match
    first, prefix match second, substring match last; ties break by
    ck3_id. Whitespace-only queries are treated as omitted.

    ``ids`` (ck3_chronicler-5oyz) is a comma-separated list of ck3_ids
    for batched lookup. Backs the Codex tracked rail so it renders
    independently of the relevance-ranked top-N window — large
    campaigns push tracked characters out of the top 250 entirely.
    When ``ids`` is set, ``q`` / ``limit`` / ``offset`` are ignored;
    every requested id is returned (unknown ids silently dropped) and
    the response order matches the requested order.
    """
    # ck3_chronicler-w1t3 (2026-05-08): resolve the played-character set
    # once per request — the registry tells us the latest player; the
    # backward-walk over title_acquired events fills in the lineage. The
    # resulting set is small (3-10 chars in typical campaigns), so a
    # plain Python ``in`` check per row is fine.
    played_ids = compute_played_character_ids(session, campaign.current_player_character_id)

    if ids is not None:
        try:
            ck3_ids = [int(part) for part in ids.split(",") if part.strip()]
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail="ids must be a comma-separated list of integers",
            ) from e
        if len(ck3_ids) > MAX_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"ids accepts at most {MAX_LIMIT} entries",
            )
        return [
            _summary(c, is_played=c.ck3_id in played_ids)
            for c in list_characters_by_ids(session, ck3_ids)
        ]

    if limit < 1 or limit > MAX_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be between 1 and {MAX_LIMIT}",
        )
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    # audit F-19: ranked-name search lives in db/repository so the
    # route stays a thin shell. Keeps ranking + filter logic out of the
    # API layer where it can drift from save-tail / dump-character
    # callers.
    query = q.strip() if q else ""
    rows = (
        search_characters_by_name(session, query, limit=limit, offset=offset)
        if query
        else list_characters_unfiltered(session, limit=limit, offset=offset)
    )
    return [_summary(c, is_played=c.ck3_id in played_ids) for c in rows]


@router.get("/characters/{ck3_id}", response_model=CharacterDetail)
def get_character_detail(
    ck3_id: int,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> CharacterDetail:
    """Full character record + chronological event list."""
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    events = list_events_for_character(session, ck3_id)
    return CharacterDetail(
        ck3_id=char.ck3_id,
        first_name=char.first_name,
        dynasty_name=char.dynasty_name,
        house_name=char.house_name,
        nickname=char.nickname,
        female=char.female,
        birth_date=char.birth_date,
        death_date=char.death_date,
        culture=char.culture,
        faith=char.faith,
        events=[_event_response(e) for e in events],
        coa_json=parse_coa_json(char.coa_json),
        primary_title=parse_primary_title(char.primary_title_json),
        held_titles=parse_held_titles(char.held_titles_json),
    )


@router.get("/characters/{ck3_id}/coa", response_model=CoaDefinitionResponse)
def get_character_coa(
    ck3_id: int,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> CoaDefinitionResponse:
    """The character's resolved coat-of-arms structure (ck3_chronicler-7ao).

    Returns the persisted CoA dict from ``Character.coa_json`` (populated
    by save-tail's per-tracked-character refresh), shaped as CK3 emits
    it in ``coat_of_arms_manager_database`` — pattern + colors + nested
    sub-shields + colored_emblem charges. Suitable for direct
    consumption by the frontend SVG composition renderer.

    Returns ``404`` if the character is unknown OR if no CoA has been
    persisted yet (e.g. the character isn't in the tracked set so
    save-tail hasn't refreshed them).
    """
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    if not char.coa_json:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no CoA persisted for character {ck3_id} — track them and let "
                "save-tail run, or import the save"
            ),
        )
    parsed = parse_coa_json(char.coa_json)
    if parsed is None:
        # Malformed coa_json row (invalid JSON or not a dict) — clean 500
        # instead of serving the corruption verbatim or letting a raw
        # JSONDecodeError escape (it did pre-27ov.45). Audit F-39 /
        # ck3_chronicler-tjql.
        raise HTTPException(
            status_code=500,
            detail=(f"persisted coa_json for character {ck3_id} is malformed (not a JSON dict)"),
        )
    return CoaDefinitionResponse.model_validate(parsed)


@router.get("/characters/{ck3_id}/coa-history", response_model=CoaHistoryResponse)
def get_character_coa_history(
    ck3_id: int,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> CoaHistoryResponse:
    """Append-only history of resolved-CoA changes (ck3_chronicler-7b8d).

    Returns ``{entries: [{observed_at, coa}, ...]}`` ordered oldest →
    newest. Empty list when save-tail has only ever seen a single CoA
    for this character (the common case). Cadet-branch formation +
    in-game facelifts surface as multiple entries.

    Returns ``404`` only when the character is unknown — an empty
    history is a valid 200 response.
    """
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    from chronicler.db.repository import list_coa_history

    rows = list_coa_history(session, ck3_id)
    # ck3_chronicler-27ov.45: parse each history row guarded — this used
    # to json.loads unguarded, so one corrupt row 500'd the whole
    # timeline. A malformed entry is now dropped instead.
    entries: list[CoaHistoryEntry] = []
    for r in rows:
        coa = parse_coa_json(r.coa_json)
        if coa is None:
            continue
        entries.append(CoaHistoryEntry(observed_at=r.observed_at, coa=coa))
    return CoaHistoryResponse(entries=entries)


@router.get(
    "/characters/{ck3_id}/biography",
    response_model=BiographyResponse,
)
def get_character_biography(
    ck3_id: int,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> BiographyResponse:
    """Latest persisted biography for a character. 404 if none."""
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    bio = get_latest_biography_for_character(session, ck3_id)
    if bio is None:
        raise HTTPException(
            status_code=404,
            detail=f"no biography for character {ck3_id}",
        )
    return BiographyResponse(
        id=bio.id,
        character_id=bio.character_id,
        version=bio.version,
        body=bio.body,
        prompt_template_version=bio.prompt_template_version,
        provider=bio.provider,
        generated_at=bio.generated_at,
        events_through_event_id=bio.events_through_event_id,
        prompt_tokens=bio.prompt_tokens,
        completion_tokens=bio.completion_tokens,
    )


@router.post(
    "/characters/{ck3_id}/biography/regenerate",
    response_model=RegenerateBiographyResponse,
    status_code=202,
)
async def regenerate_character_biography(
    ck3_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> RegenerateBiographyResponse:
    """Force-regenerate a character's biography (ck3_chronicler-7gw).

    Returns immediately (202) — generation runs under the scheduler's
    single-lane semaphore so it queues behind any in-flight save-tail
    work. The new row lands as biography version+1; the SSE channel
    fans out narrative_started / narrative_completed frames so the UI
    can refetch on completion.

    Reuses the active save-tail scheduler when one is registered on
    ``app.state.narrative_scheduler`` (the common path during live
    play). Falls back to constructing a one-shot scheduler against
    this campaign's factory when no save-tail loop is running —
    sealed campaigns don't tail, but their biographies are still
    regenerable. ck3_chronicler-u0eu (2026-05-08).

    503 only when no NarrativeProvider is configured — without a
    provider the generation has nowhere to land.

    ck3_chronicler-fk9r-followup (2026-05-08): includes archived
    campaigns. Sealing a campaign means 'no more save-tail rotates
    state'; it does NOT mean 'biographies are frozen forever'. When
    fabrication or craft bugs surface post-seal (e.g. the 081b /
    nwoc / 1m8q smoke-find class), the user must be able to
    regenerate the affected biographies retroactively. Regenerating
    after a seal does drift the closing chronicle (which was
    synthesized from the older biographies); regenerate the closing
    chronicle separately if you want them aligned.
    """
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    sched = _resolve_or_build_scheduler(request, campaign)
    item_id = sched.regenerate(ck3_id)
    return RegenerateBiographyResponse(character_id=ck3_id, item_id=item_id)


# ck3_chronicler-7bi5 (revived by cs1o): cost-aware regenerate modal.
# Heuristic baseline used when no prior biography exists. Tuned against
# v0.9 vysp-smoke generations: a 30-event Jarl with no world-context
# block runs roughly 4-5k input tokens; with world-context the prompt
# block adds another ~1.5k. (Real current-era woven bios run far larger
# — avg ~97k summed input — but those always have a prior generation to
# estimate from, which is the accurate path.)
_BIO_BASE_INPUT_TOKENS = 1500
_BIO_PER_EVENT_TOKENS = 80
_BIO_WORLD_CONTEXT_BONUS = 1500
_BIO_DEFAULT_OUTPUT_TOKENS = 1100


def _estimate_provider_destination(
    request_app_state: Any, kind: str
) -> tuple[str | None, str | None]:
    """Return (provider_name, model) the request would actually route to.

    cs1o: the configured transport's ``name`` advertises
    ``claude-code:<model>`` or ``anthropic:<model>`` directly — no
    internal routing to unwrap. ``kind`` resolves the per-kind model via
    ``name_for_kind`` when the provider exposes it. Returns (None, None)
    when no narrative provider is configured (the ``unconfigured`` mode
    reported by the settings panel).
    """
    provider = getattr(request_app_state, "narrative_provider", None)
    if provider is None:
        return None, None
    name_for_kind = getattr(provider, "name_for_kind", None)
    full_name = name_for_kind(kind) if callable(name_for_kind) else provider.name
    model = full_name.split(":", 1)[1] if ":" in full_name else None
    return full_name, model


@router.get(
    "/characters/{ck3_id}/biography/cost-estimate",
    response_model=BiographyCostEstimateResponse,
)
def get_biography_cost_estimate(
    ck3_id: int,
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> BiographyCostEstimateResponse:
    """Estimate the token + USD cost of regenerating this character's
    biography (ck3_chronicler-7bi5, revived by cs1o).

    The estimate is best-effort, not a quote. When the character already
    has at least one persisted biography, the prior generation's
    token columns are reused (most accurate — same prompt template, same
    character data shape), including the cache breakdown when present so
    the USD figure bills each bucket at its real rate. For first-time
    generations it falls back to a coarse heuristic on event count plus
    a constant for the world-context block when present.

    ``would_route_to`` reflects the configured transport's kind-resolved
    name; null when no provider is configured so the UI can render a
    "no model selected" note rather than a price. ``pool_billed`` is
    True for the claude-code transport — the cost draws from the
    subscription's monthly programmatic credit, not a payment card.
    """
    char = get_character(session, ck3_id)
    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )

    has_world_context = bool(char.region_summary_json and char.region_summary_json.strip())
    kind = "biography_woven" if has_world_context else "biography"
    would_route_to, model = _estimate_provider_destination(request.app.state, kind)

    prior = get_latest_biography_for_character(session, ck3_id)
    cache_read: int | None = None
    cache_write: int | None = None
    if prior is not None and (
        prior.prompt_tokens is not None or prior.completion_tokens is not None
    ):
        estimated_input = prior.prompt_tokens or 0
        estimated_output = prior.completion_tokens or _BIO_DEFAULT_OUTPUT_TOKENS
        # cs1o: a regeneration within the cache TTL replays the prior
        # call's cache profile; billing the breakdown at real rates
        # beats pricing the summed input at full freight.
        cache_read = prior.cache_read_tokens
        cache_write = prior.cache_write_tokens
        method = "from_prior_generation"
    else:
        event_count = len(list_events_for_character(session, ck3_id))
        estimated_input = _BIO_BASE_INPUT_TOKENS + event_count * _BIO_PER_EVENT_TOKENS
        if has_world_context:
            estimated_input += _BIO_WORLD_CONTEXT_BONUS
        estimated_output = _BIO_DEFAULT_OUTPUT_TOKENS
        method = "estimate"

    if would_route_to is None:
        est_usd = 0.0
    else:
        est_usd = compute_generation_cost(
            would_route_to,
            input_tokens=estimated_input,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            output_tokens=estimated_output,
        )

    return BiographyCostEstimateResponse(
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
        estimated_total_tokens=estimated_input + estimated_output,
        est_usd=round(est_usd, 4),
        kind=kind,
        would_route_to=would_route_to,
        model=model,
        method=method,
        pool_billed=bool(would_route_to and would_route_to.startswith("claude-code")),
    )


# ck3_chronicler-z5os: scheduler provisioning moved to the
# dependencies module so settings.py no longer reaches across into this
# route module to borrow it. Kept as a thin re-export for the call site
# below + any existing importers.
_resolve_or_build_scheduler = resolve_or_build_scheduler

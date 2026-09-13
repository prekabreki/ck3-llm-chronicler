"""FastAPI dependency injection for chronicler routes.

Per-request session factory keyed by campaign name. The factory cache
lives in app state so we don't open + close a SQLAlchemy engine per
request — engines are cached by campaign UUID for the lifetime of the
process.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from chronicler.db import make_engine_for_path, make_session_factory
from chronicler.db.registry import Campaign, get_campaign_by_name


class EngineCache:
    """Process-lifetime cache of (campaign_id → engine, factory).

    Held in ``app.state.engine_cache``. Engines are disposed on app
    shutdown via the FastAPI lifespan handler.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self.registry_path = registry_path
        self._engines: dict[str, tuple[Engine, sessionmaker[Session]]] = {}
        # ck3_chronicler-27ov.78 (audit L15): serialize first-open so a
        # raced check-then-set can't leak a duplicate engine.
        self._lock = threading.Lock()

    def factory_for(self, campaign: Campaign) -> sessionmaker[Session]:
        # Fast path: an already-cached engine needs no lock. dict.get is
        # atomic under the GIL and entries are only ever added (never
        # mutated in place), so a lock-free read here is safe.
        cached = self._engines.get(campaign.id)
        if cached is not None:
            return cached[1]
        # Slow path: double-checked locking. FastAPI runs sync deps
        # (get_session) in a threadpool, so two requests racing the first
        # access for one campaign both miss the cache. Without the lock
        # both open an engine; the loser's engine is overwritten in the
        # dict and never disposed, leaking a SQLite file handle that wedges
        # delete_campaign_endpoint's unlink retry-loop on Windows (the
        # PermissionError evict() can't clear, since the orphan was never
        # cached). Creating under the lock guarantees one engine per id.
        with self._lock:
            cached = self._engines.get(campaign.id)
            if cached is not None:
                return cached[1]
            engine = make_engine_for_path(Path(campaign.db_path))
            factory = make_session_factory(engine)
            self._engines[campaign.id] = (engine, factory)
            return factory

    def dispose_all(self) -> None:
        for engine, _factory in self._engines.values():
            engine.dispose()
        self._engines.clear()

    def evict(self, campaign: Campaign) -> None:
        """ck3_chronicler-ezpc: drop the cached engine for one campaign
        so a subsequent file unlink isn't blocked by an open SQLite
        handle (Windows surfaces this as PermissionError on unlink;
        Unix tolerates it but the inode-cache cost is the same).
        Idempotent — a no-op for campaigns whose engine was never
        opened.
        """
        cached = self._engines.pop(campaign.id, None)
        if cached is not None:
            cached[0].dispose()


def get_engine_cache(request: Request) -> EngineCache:
    """Pull the EngineCache out of app.state for use as a dep."""
    cache = getattr(request.app.state, "engine_cache", None)
    if cache is None:
        raise RuntimeError("app.state.engine_cache not initialised — did create_app() run?")
    return cache


def get_narrative_provider(request: Request):
    """Return the configured NarrativeProvider or raise 503.

    Used by endpoints that run LLM generations on demand (POST /complete,
    forthcoming /regenerate-biography). Endpoints that don't need
    generation skip this dependency entirely.
    """
    provider = getattr(request.app.state, "narrative_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no narrative provider configured — pass "
                "narrative_provider= to create_app() (the chronicler CLI "
                "wires this up automatically)"
            ),
        )
    return provider


def resolve_or_build_scheduler(request: Request, campaign: Campaign):
    """Return the scheduler that should handle narrative work against
    ``campaign``. Prefer the live save-tail scheduler when one exists on
    ``app.state.narrative_schedulers`` AND it's bound to this campaign —
    that scheduler already carries the per-character locks + retry-cooldown
    state we want to share. Otherwise lazily construct + cache a scheduler
    keyed by campaign.id so subsequent calls against the same campaign
    reuse the per-character lock + queue plumbing.

    503 only when neither a live scheduler matches AND no provider is
    configured for lazy construction.

    This is the single scheduler-provisioning seam (ck3_chronicler-z5os).
    It lives here, not in a route module, so route modules
    (characters.py for regenerate, settings.py for the llm-pause drain)
    share it without reaching across into each other.
    """
    from chronicler.narrative.scheduler import NarrativeScheduler

    # 1. Prefer live save-tail scheduler bound to this campaign — it
    #    carries the right factory + state. The live scheduler has
    #    its own provider; we don't check app.state.narrative_provider
    #    in this path.
    # ck3_chronicler-m4cn: app.state.narrative_schedulers is a
    # campaign_id → NarrativeScheduler dict (not a single slot) so a
    # second adopted campaign doesn't poison the first's regenerate.
    schedulers = getattr(request.app.state, "narrative_schedulers", None)
    if schedulers is not None:
        live = schedulers.get(campaign.id)
        if live is not None:
            return live

    # 2. Per-campaign lazy-construction cache. Multiple calls against the
    #    same campaign share the same single-lane semaphore + per-character
    #    lock + queue_state plumbing.
    cache = getattr(request.app.state, "_lazy_regen_schedulers", None)
    if cache is None:
        cache = {}
        request.app.state._lazy_regen_schedulers = cache
    scheduler = cache.get(campaign.id)
    if scheduler is not None:
        return scheduler

    # 3. Build one. This is where we need a configured provider —
    #    without one, generation has nowhere to land.
    provider = getattr(request.app.state, "narrative_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no narrative provider configured — pass narrative_provider="
                " to create_app() (the chronicler CLI wires this up "
                "automatically)"
            ),
        )

    factory = get_engine_cache(request).factory_for(campaign)
    queue_state = getattr(request.app.state, "narrative_queue", None)
    scheduler = NarrativeScheduler(
        factory,
        provider,
        campaign_uuid=campaign.id,
        queue_state=queue_state,
    )
    cache[campaign.id] = scheduler
    return scheduler


def get_campaign(name: str, request: Request) -> Campaign:
    """Resolve a campaign by name; 404 if not found.

    Used as a path parameter dependency: ``campaign: Campaign = Depends(get_campaign)``.

    Excludes archived campaigns — use this for write-style routes (track,
    pause/resume, auto-track) where mutating a sealed campaign would be
    incorrect. For read-style routes that should remain reachable after
    sealing (chronicle GET, export, etc.), use
    :func:`get_campaign_including_archived` instead. ck3_chronicler-fk9r.
    """
    cache = get_engine_cache(request)
    campaign = get_campaign_by_name(name, registry=cache.registry_path)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"campaign not found: {name}")
    return campaign


def get_campaign_including_archived(name: str, request: Request) -> Campaign:
    """Resolve a campaign by name, including archived ones; 404 if not found.

    ck3_chronicler-fk9r: read-style routes for sealed campaigns (closing
    chronicle, markdown export) need to keep working after archive_campaign
    flips the archived flag. Calls the registry with include_archived=True.
    """
    cache = get_engine_cache(request)
    campaign = get_campaign_by_name(name, registry=cache.registry_path, include_archived=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"campaign not found: {name}")
    return campaign


def get_session(
    request: Request,
    campaign: Campaign = Depends(get_campaign),
) -> Iterator[Session]:
    """Yield a Session bound to the given campaign's DB.

    For use with FastAPI's ``Depends`` machinery; the session is closed
    after the response is generated. The ``campaign`` dependency
    chains through ``get_campaign`` so the path's ``{name}`` parameter
    flows into both the campaign lookup and the session selection
    without the route handler having to wire it manually.
    """
    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_session_including_archived(
    request: Request,
    campaign: Campaign = Depends(get_campaign_including_archived),
) -> Iterator[Session]:
    """Same as :func:`get_session` but its campaign dep includes archived
    campaigns (ck3_chronicler-1p4t / audit F-03).

    For read-only endpoints that should remain reachable on sealed
    campaigns — character detail, biography, memories, etc. The Library
    UI exposes archived campaigns via include_archived=true and lets the
    user click into them; the read-side routes need to honour that.
    """
    cache = get_engine_cache(request)
    factory = cache.factory_for(campaign)
    session = factory()
    try:
        yield session
    finally:
        session.close()

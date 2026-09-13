"""Schema-migration routes (ck3_chronicler-72a).

Five endpoints fronting :mod:`chronicler.migrate`. The route layer
adds the FastAPI plumbing and the active-save-tail check; the actual
detect / backup / upgrade / restore logic lives in the migrate
package."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from chronicler.api.dependencies import get_engine_cache
from chronicler.db.registry import get_data_dir, registry_path
from chronicler.migrate import (
    SaveTailRunningError,
    list_backups,
    restore_from_backup,
    run_migration,
    scan,
)

router = APIRouter(prefix="/api/migrate", tags=["migrate"])


class _PendingCampaign(BaseModel):
    campaign_id: str
    name: str
    current_head: str | None
    target_head: str


class _StatusResponse(BaseModel):
    needs_migration: list[_PendingCampaign]
    registry_needs_migration: bool
    registry_missing_columns: list[str]


class _RunRowResult(BaseModel):
    id: str
    ok: bool
    error: str | None = None


class _RunResponse(BaseModel):
    success: bool
    backup_dir: str | None
    results: list[_RunRowResult]


class _RestoreRequest(BaseModel):
    backup_dir: str


class _RestoreResponse(BaseModel):
    restored: int


class _BackupEntry(BaseModel):
    timestamp: str
    path: str
    campaign_count: int


class _HaltResponse(BaseModel):
    halted: bool


def _resolve_registry_and_data(request: Request) -> tuple[Path, Path]:
    """Pull the registry path from the engine cache (so test overrides
    are honoured) and resolve data_dir from get_data_dir()."""
    cache = get_engine_cache(request)
    reg = cache.registry_path or registry_path()
    return reg, get_data_dir()


def _scheduler_running(request: Request) -> bool:
    # ck3_chronicler-m4cn: app.state.narrative_schedulers is a per-
    # campaign dict; any non-empty entry means a save-tail loop is
    # active and the migration runner should ask it to drain first.
    schedulers = getattr(request.app.state, "narrative_schedulers", None)
    return bool(schedulers)


@router.get("/status", response_model=_StatusResponse)
def migration_status(request: Request) -> _StatusResponse:
    reg, _ = _resolve_registry_and_data(request)
    plan = scan(registry_path=reg)
    # ck3_chronicler-4zdg: hide archived (= "Completed"/sealed) campaigns
    # from the migration UI. Both surfaces that nag the user — the Library
    # MigrationBanner and the Settings → Migration panel — read this one
    # endpoint, so filtering here suppresses them everywhere with no FE
    # change. run_migration still re-scans the full plan and sweeps
    # archived DBs (migrator.py), so they stay at head and their read-only
    # pages don't 500 (sj31) — we only stop *displaying* them here.
    return _StatusResponse(
        needs_migration=[
            _PendingCampaign(
                campaign_id=p.campaign_id,
                name=p.name,
                current_head=p.current_head,
                target_head=p.target_head,
            )
            for p in plan.needs_migration
            if not p.archived
        ],
        registry_needs_migration=plan.registry_needs_migration,
        registry_missing_columns=plan.registry_missing_columns,
    )


@router.post("/run", response_model=_RunResponse)
async def migration_run(request: Request) -> _RunResponse:
    """audit F-05 / ck3_chronicler-n2ds: alembic upgrades are slow
    (seconds-to-tens-of-seconds on real campaign sets) and used to
    run on the FastAPI threadpool, blocking the worker for the
    duration. Now offloaded via asyncio.to_thread so the loop stays
    responsive — other read endpoints can serve while the migration
    grinds. cache.dispose_all() runs first so SQLAlchemy file handles
    are released before alembic's per-DB connection."""
    reg, data_dir = _resolve_registry_and_data(request)
    cache = get_engine_cache(request)
    cache.dispose_all()  # release SQLAlchemy file handles before file ops
    scheduler_running = _scheduler_running(request)
    try:
        result = await asyncio.to_thread(
            run_migration,
            registry_path=reg,
            data_dir=data_dir,
            scheduler_running=scheduler_running,
        )
    except SaveTailRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _RunResponse(
        success=result.success,
        backup_dir=str(result.backup_dir) if result.backup_dir else None,
        results=[_RunRowResult(id=r.id, ok=r.ok, error=r.error) for r in result.results],
    )


@router.post("/restore", response_model=_RestoreResponse)
async def migration_restore(body: _RestoreRequest, request: Request) -> _RestoreResponse:
    """audit F-05 / ck3_chronicler-n2ds: same async-offload as
    migration_run — the file copy + per-DB checks block on disk I/O
    that easily runs into seconds on a multi-campaign restore."""
    reg, data_dir = _resolve_registry_and_data(request)
    cache = get_engine_cache(request)
    cache.dispose_all()
    backup_dir = Path(body.backup_dir)
    if not backup_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"backup not found: {backup_dir}")
    scheduler_running = _scheduler_running(request)
    try:
        restored = await asyncio.to_thread(
            restore_from_backup,
            backup_dir,
            registry_path=reg,
            data_dir=data_dir,
            scheduler_running=scheduler_running,
        )
    except SaveTailRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _RestoreResponse(restored=restored)


@router.get("/backups", response_model=list[_BackupEntry])
def migration_list_backups(request: Request) -> list[_BackupEntry]:
    _, data_dir = _resolve_registry_and_data(request)
    return [
        _BackupEntry(
            timestamp=b.timestamp,
            path=str(b.path),
            campaign_count=b.campaign_count,
        )
        for b in list_backups(data_dir=data_dir)
    ]


@router.post("/halt-save-tail", response_model=_HaltResponse)
def migration_halt_save_tail(request: Request) -> _HaltResponse:
    """Signal the orchestrator's save-tail loop to drain. Idempotent —
    returns halted=True even when no loop is currently waiting (sets the
    event regardless; future spawns will see it pre-set and exit early)."""
    stop = getattr(request.app.state, "save_tail_stop", None)
    if stop is None:
        # No orchestrator running (tests or chronicler serve without
        # save-tail). Treat as already-halted.
        return _HaltResponse(halted=True)
    stop.set()
    return _HaltResponse(halted=True)

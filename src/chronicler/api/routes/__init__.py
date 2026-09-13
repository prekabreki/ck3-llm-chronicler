"""Route registration for the chronicler API."""

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from chronicler.api.routes.campaigns import router as campaigns_router
from chronicler.api.routes.characters import router as characters_router
from chronicler.api.routes.closing import router as closing_router
from chronicler.api.routes.cost import router as cost_router
from chronicler.api.routes.dynasty import router as dynasty_router
from chronicler.api.routes.export import router as export_router
from chronicler.api.routes.hall import router as hall_router
from chronicler.api.routes.heraldry import heraldry_router
from chronicler.api.routes.heraldry import heraldry_sse_router as heraldry_sse_router
from chronicler.api.routes.importer import router as importer_router
from chronicler.api.routes.importer import sse_router as importer_sse_router
from chronicler.api.routes.ingest_stream import router as ingest_stream_router
from chronicler.api.routes.logs import router as logs_router
from chronicler.api.routes.migrate import router as migrate_router
from chronicler.api.routes.narrative_queue import narrative_router
from chronicler.api.routes.save import router as save_router
from chronicler.api.routes.search import router as search_router
from chronicler.api.routes.settings import settings_router as global_settings_router
from chronicler.api.routes.tracked import router as tracked_router
from chronicler.api.routes.tree import router as tree_router
from chronicler.db.registry import get_data_dir

log = logging.getLogger(__name__)


class _LateBoundStaticFiles(StaticFiles):
    """StaticFiles over a directory that may not exist yet (issue #3).

    ``check_dir=False`` only skips the constructor's check; Starlette still
    stats the directory in ``check_config`` on the first request and raises
    ``RuntimeError`` when it is missing, which would turn "heraldry not
    extracted" into a 500 (and, on an unwritable data dir, into a broken
    app). Skipping that check leaves the per-file lookup to answer, and a
    missing file under a missing directory is a 404 either way.

    Traversal safety is unaffected: this overrides only the startup probe,
    not ``lookup_path``, which is what confines the URL-supplied filename
    to the mounted directory.
    """

    async def check_config(self) -> None:  # pragma: no cover - trivial override
        return None


# Stub HTML returned when the React build hasn't been produced yet
# (e.g. fresh dev environment, hasn't run `npm run build` in frontend/).
# Lives in templates/ as the only surviving Jinja artifact post-Phase 6.
_SPA_NOT_BUILT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "spa_not_built.html"
)


class _NoCacheHtmlStatic(StaticFiles):
    """StaticFiles subclass that disables HTTP caching for .html responses.

    ck3_chronicler-ex31: the bundled SPA's ``index.html`` is non-hashed and
    references content-hashed assets in ``assets/`` (Vite output). Without
    explicit cache headers, a browser tab can hold a stale ``index.html``
    across ``npm run build`` cycles and keep loading the OLD asset
    filenames — surfacing as "my fix didn't ship" until a manual Ctrl-F5.

    Adding ``Cache-Control: no-cache, no-store, must-revalidate`` (plus
    legacy ``Pragma`` + ``Expires``) only on ``.html`` responses leaves the
    hashed-asset long-cache behaviour untouched (those filenames change
    per build, so aggressive caching is correct there).

    SPA fallback (``html=True``) and direct ``GET /`` both end up serving
    ``index.html`` through this method, so the single ``.html`` check
    covers both code paths.
    """

    def file_response(
        self,
        full_path: Any,  # PathLike[str] - varies by starlette version
        stat_result: os.stat_result,
        scope: Any,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).lower().endswith(".html"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def register_routes(app: FastAPI) -> None:
    app.include_router(campaigns_router)
    app.include_router(characters_router)
    app.include_router(closing_router)
    app.include_router(cost_router)
    app.include_router(dynasty_router)
    app.include_router(export_router)
    app.include_router(hall_router)
    app.include_router(importer_router)
    app.include_router(migrate_router)
    app.include_router(importer_sse_router)
    app.include_router(ingest_stream_router)
    app.include_router(logs_router)
    app.include_router(save_router)
    app.include_router(search_router)
    app.include_router(global_settings_router)
    app.include_router(narrative_router)
    app.include_router(heraldry_router)
    app.include_router(heraldry_sse_router)
    app.include_router(tracked_router)
    app.include_router(tree_router)

    # ck3_chronicler-7ao: serve the extracted CK3 heraldry assets
    # (PNGs + palette.json + manifest.json) from the chronicler data dir.
    # Populated by `chronicler heraldry extract`.
    #
    # Issue #3: the mount used to be conditional on the directory existing
    # at app-construction time, and skipped silently otherwise. On a fresh
    # install that meant no mount for the life of the process: the user ran
    # the in-app extract, `GET /api/settings/heraldry` started reporting
    # extracted=true, the CoA endpoint kept returning valid definitions —
    # and every texture 404'd until a restart, with nothing anywhere saying
    # so. Hit for real on the first Linux run (2026-07-28).
    #
    # Mount unconditionally with check_dir=False, so "is there anything to
    # serve" is answered per request instead of once at boot. mkdir is
    # best-effort: an unwritable data dir must yield a working app that 404s
    # these paths, never a boot failure.
    heraldry_dir = get_data_dir() / "heraldry"
    try:
        heraldry_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning(
            "could not create the heraldry asset dir %s (%s); "
            "asset requests will 404 until it exists",
            heraldry_dir,
            exc,
        )
    app.mount(
        "/api/heraldry/assets",
        _LateBoundStaticFiles(directory=str(heraldry_dir), check_dir=False),
        name="heraldry",
    )

    # SPA mount for the React app (v0.7 — see ck3_chronicler-gog + 789).
    # Phase 6 retired the Jinja UI; the SPA is now the chronicler's
    # only frontend and serves the root path. When the build directory
    # is missing we serve a friendly stub instead of crashing — keeps
    # `chronicler serve` working in environments that haven't run
    # `npm run build` yet.
    static_dir = Path(__file__).resolve().parent.parent / "static"
    spa_dir = static_dir / "app"
    spa_index = spa_dir / "index.html"
    if spa_dir.exists() and spa_index.exists():
        # ck3_chronicler-da8: warn when the bundled SPA is older than
        # frontend source files. The previous failure mode was silent —
        # a developer would edit a .tsx file, run `chronicler dev`,
        # see the OLD bundle in the browser, and waste investigation
        # time chasing imaginary backend bugs. The warning shows on
        # startup so it's visible in the very first log line, naming
        # the newest source file so the fix is unambiguous.
        _warn_if_spa_stale(spa_index)
        # html=True makes StaticFiles serve index.html for "/" and any
        # subpath that doesn't map to a file (SPA history-mode).
        # _NoCacheHtmlStatic adds no-cache headers to .html responses
        # only (ck3_chronicler-ex31) so a rebuilt bundle takes effect
        # on a plain reload instead of needing Ctrl-F5.
        app.mount("/", _NoCacheHtmlStatic(directory=str(spa_dir), html=True), name="spa")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def _spa_not_built() -> str:
            return _SPA_NOT_BUILT_TEMPLATE.read_text(encoding="utf-8")


def _warn_if_spa_stale(spa_index: Path, frontend_src: Path | None = None) -> None:
    """ck3_chronicler-da8: log a WARNING when any file under
    ``frontend/src`` is newer than the built ``static/app/index.html``.

    Cheap (one os.walk on developer machines) and only runs once at
    startup — the cost is paid up-front and the developer-experience
    win is large. On installations that don't ship the frontend source
    (e.g. a Tauri-bundled chronicler.exe shipped without ``frontend/``),
    the source dir simply won't exist and the check no-ops. Any IO
    error is swallowed: this is observability, never a hard failure
    that should keep the API from starting.

    ``frontend_src`` is overridable for tests; in production it
    auto-resolves to ``<repo>/frontend/src`` via ``parents[4]``."""
    if frontend_src is None:
        # frontend/ sits at the repo root. Five .parents hops:
        # __init__.py → routes → api → chronicler → src → repo.
        frontend_src = Path(__file__).resolve().parents[4] / "frontend" / "src"
    if not frontend_src.is_dir():
        return
    try:
        bundle_mtime = spa_index.stat().st_mtime
        newest_source: tuple[float, Path] | None = None
        for path in frontend_src.rglob("*"):
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
            if newest_source is None or mtime > newest_source[0]:
                newest_source = (mtime, path)
        if newest_source is None:
            return
        newest_mtime, newest_path = newest_source
        if newest_mtime > bundle_mtime:
            log.warning(
                "stale SPA bundle: %s was last built at %.0f but %s was "
                "modified at %.0f (run `npm run build` in frontend/ to refresh)",
                spa_index,
                bundle_mtime,
                newest_path,
                newest_mtime,
            )
    except OSError:
        log.debug("SPA freshness check failed; skipping", exc_info=True)

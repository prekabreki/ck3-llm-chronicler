"""FastAPI HTTP layer for chronicler.

The v0.3 architectural milestone — exposes the per-campaign DB content
over HTTP so a browser can browse a finished campaign. JSON endpoints
land first (V03-P01); the HTML/HTMX pages built on top come at V03-P02.

Top-level entry point: :func:`create_app`. Used by both the
``chronicler serve`` CLI command and tests via FastAPI's ``TestClient``.
"""

from chronicler.api.app import create_app


def create_app_with_default_provider():
    """Factory used by `chronicler serve --reload`.

    uvicorn's `--reload` runs the factory in a subprocess that imports by
    string, so the callable has to be module-level. Constructs the
    narrative provider lazily; otherwise `app.state.narrative_
    provider = None` and every LLM endpoint 503s (ck3_chronicler-pr7m).
    """
    from chronicler.narrative import make_narrative_provider

    return create_app(narrative_provider=make_narrative_provider())


__all__ = ["create_app", "create_app_with_default_provider"]

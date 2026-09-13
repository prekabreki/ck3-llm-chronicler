"""Contract guard: the hand-maintained frontend wire types
(``frontend/src/api/types.ts``) must not silently drift from the backend
Pydantic models they mirror.

ck3_chronicler-27ov.29 (audit M-F11): types.ts is a ~700-line by-hand
mirror of ~62 Pydantic models. "Keep field names + nullability in
lockstep" was enforced only by humans — and it had already drifted: the
backend ``CostBucket`` grew a ``usd`` field (ck3_chronicler-cs1o) that
the FE ``CostBucket`` lacked, while the FE actively consumed the model
(SettingsPage → useCostSummary → CostDashboard). The F-01 production
incident ("import stuck at 0/N forever") came from exactly this
divergence class.

This test introspects FastAPI's generated OpenAPI schema — the single
source of truth for what the API actually serves — and diffs the field
names of every schema against the same-named TypeScript interface in
types.ts. A field added to (or removed from) a backend model the FE
mirrors now fails here instead of shipping a silent mismatch.

Scope: field-NAME parity over the intersection of {OpenAPI schemas} and
{types.ts interfaces}. FE-only shapes and BE schemas with no FE mirror
are out of scope (the FE only mirrors what it consumes). Field TYPES /
nullability aren't compared — the proven drift class is missing/renamed
fields, and the file's convention (``x: T | null``, keys always present)
doesn't map cleanly onto OpenAPI required/nullable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chronicler.api import create_app

TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts"

_INTERFACE_RE = re.compile(r"^export interface (\w+)\s*\{")
# A field line: optional leading whitespace, an identifier, an optional
# ``?``, then ``:``. Skips comment lines and blank lines by construction.
_FIELD_RE = re.compile(r"^\s+(\w+)\??:\s")

# Interfaces whose field set intentionally diverges from the same-named
# backend schema. Each entry must carry a reason. The goal is empty.
_ALLOWED_DRIFT: dict[str, str] = {}


def _parse_types_ts() -> dict[str, set[str]]:
    """interface name -> set of field names declared in types.ts."""
    interfaces: dict[str, set[str]] = {}
    current: str | None = None
    for line in TYPES_TS.read_text(encoding="utf-8").splitlines():
        if current is None:
            m = _INTERFACE_RE.match(line)
            if m:
                current = m.group(1)
                interfaces[current] = set()
            continue
        if line.startswith("}"):
            current = None
            continue
        fm = _FIELD_RE.match(line)
        if fm:
            interfaces[current].add(fm.group(1))
    return interfaces


def _openapi_schemas(tmp_path: Path) -> dict[str, set[str]]:
    """OpenAPI schema name -> set of property names served by the API."""
    app = create_app(registry_path=tmp_path / "registry.db")
    schema = app.openapi()
    out: dict[str, set[str]] = {}
    for name, defn in schema.get("components", {}).get("schemas", {}).items():
        props = defn.get("properties")
        if isinstance(props, dict):
            out[name] = set(props.keys())
    return out


def test_types_ts_field_names_match_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path))
    ts = _parse_types_ts()
    api = _openapi_schemas(tmp_path)

    shared = sorted(set(ts) & set(api))
    assert shared, "no interfaces shared between types.ts and the OpenAPI schema"

    drift: dict[str, dict[str, list[str]]] = {}
    for name in shared:
        if name in _ALLOWED_DRIFT:
            continue
        backend_only = sorted(api[name] - ts[name])
        frontend_only = sorted(ts[name] - api[name])
        if backend_only or frontend_only:
            drift[name] = {
                "backend has, FE missing": backend_only,
                "FE has, backend missing": frontend_only,
            }

    if drift:
        lines = ["wire-type drift between frontend/src/api/types.ts and backend models:"]
        for name, sides in sorted(drift.items()):
            lines.append(f"  {name}:")
            for label, fields in sides.items():
                if fields:
                    lines.append(f"    {label}: {', '.join(fields)}")
        pytest.fail("\n".join(lines))

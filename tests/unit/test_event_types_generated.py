"""Guard the generated frontend event-type list against schema drift.

``frontend/src/api/eventTypes.generated.ts`` is emitted from
``chronicler.schema.events.EventPayload`` by
``scripts/codegen_event_types.py``. The frontend coverage test
(``EventsPanel.test.ts``) trusts that file, so a stale copy lets event
types fall through to the bare fallback glyph without anyone noticing.

This test renders the file in memory from the schema and compares it to
the committed copy, without running the script's ``main`` (which would
write the file and hide the drift it is meant to catch).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEGEN = REPO_ROOT / "scripts" / "codegen_event_types.py"
GENERATED = REPO_ROOT / "frontend" / "src" / "api" / "eventTypes.generated.ts"


def _load_codegen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codegen_event_types", CODEGEN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["codegen_event_types"] = module
    # exec_module runs the module body only; main() is guarded by
    # __name__ == "__main__" and must never fire here.
    spec.loader.exec_module(module)
    return module


def test_loading_codegen_does_not_write_the_generated_file() -> None:
    """Importing the script must not rewrite the file it checks."""
    before = GENERATED.read_bytes()
    _load_codegen()
    assert GENERATED.read_bytes() == before


def test_generated_event_types_match_schema() -> None:
    module = _load_codegen()
    expected = module.render_typescript(module.extract_event_type_literals())
    actual = GENERATED.read_text(encoding="utf-8")
    assert actual == expected, (
        "frontend/src/api/eventTypes.generated.ts is stale. Run "
        "`uv run python scripts/codegen_event_types.py` and commit the result."
    )

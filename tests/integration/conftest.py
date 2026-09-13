"""Shared fixtures for the integration save-ingest suites (ck3_chronicler-27ov.72).

``session_factory`` / ``session`` back the test_ingest_* files split out of
the test_save_ingest monolith; a conftest here makes them available to every
file in tests/integration without cross-module imports.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from chronicler.db import Base, make_engine_for_path, make_session_factory


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator:
    engine = make_engine_for_path(tmp_path / "campaign.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s

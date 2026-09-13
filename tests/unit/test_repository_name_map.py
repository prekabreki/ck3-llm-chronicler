"""ck3_chronicler-2cc.1: character_name_map repository helper."""

from __future__ import annotations

from pathlib import Path

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import character_name_map, upsert_character


def test_character_name_map_returns_id_to_name(tmp_path: Path) -> None:
    engine = make_engine_for_path(tmp_path / "c.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        upsert_character(s, ck3_id=1, first_name="Harold")
        upsert_character(s, ck3_id=2, first_name="Edith")
        upsert_character(s, ck3_id=3, first_name=None)  # NULL name skipped
        s.commit()
    with factory() as s:
        names = character_name_map(s)
    assert names == {1: "Harold", 2: "Edith"}

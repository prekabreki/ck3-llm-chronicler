"""Shared CharacterSnapshot factory for save-pipeline tests (ck3_chronicler-27ov.69 / M-T3).

The same ~15-field ``save.parse.CharacterSnapshot`` literal was copy-pasted
across test_save_diff / test_save_baseline / test_save_ingest /
test_crash_recovery / test_dev_pipeline — and rebuilt wholesale inside
test_save_diff's ``_make_char_with_*`` variants just to override one field.
:func:`make_char` holds the literal once; each file's local helper delegates
here, preserving its own defaults and signature.

NOTE: this is the ``save.parse.CharacterSnapshot``. test_prompt_builder uses the
distinct ``narrative.prompt_builder.CharacterSnapshot`` and is intentionally not
covered here.
"""

from __future__ import annotations

from typing import Any

from chronicler.save.parse import CharacterSnapshot, FamilySnapshot


def make_char(cid: int, **overrides: Any) -> CharacterSnapshot:
    """Build a ``save.parse.CharacterSnapshot`` with the common test defaults.

    Defaults: alive, male, born 1020.1.1, culture/faith 1, location 100, a fresh
    empty :class:`FamilySnapshot`, empty traits/memories. Any field can be
    overridden by keyword (passed straight to the dataclass) — e.g.
    ``make_char(7, is_dead=True, death_date="1067.1.15")`` or
    ``make_char(7, government="feudal_government")``. ``death_cause``,
    ``death_killer``, ``decisions_taken``, etc. fall through to the dataclass's
    own defaults unless overridden.
    """
    base: dict[str, Any] = dict(
        ck3_id=cid,
        first_name=f"Char{cid}",
        nickname=None,
        is_dead=False,
        female=False,
        birth_date="1020.1.1",
        death_date=None,
        culture_id=1,
        faith_id=1,
        dynasty_house_id=None,
        ethnicity=None,
        traits=(),
        family=FamilySnapshot(),
        location_id=100,
        memories=(),
    )
    base.update(overrides)
    return CharacterSnapshot(**base)

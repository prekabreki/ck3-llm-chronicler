"""The single ordered registry of snapshot-level differs."""

from __future__ import annotations

from chronicler.save.diff.character import (
    _diff_concubines,
    _diff_lifestyle_commits,
    _diff_miscarriage,
    _diff_modifiers,
    _diff_one_character,
    _diff_perks,
    _diff_state_changes,
    _diff_traits,
)
from chronicler.save.diff.context import StreamDiffer, make_set_differ
from chronicler.save.diff.dynasty import _diff_dynasty_legacies, _diff_dynasty_splendor
from chronicler.save.diff.holdings import (
    _diff_constructions,
    _diff_contracts,
    _diff_domicile,
)
from chronicler.save.diff.streams import (
    _ACTIVITIES_SPEC,
    _ALLIANCES_SPEC,
    _ARTIFACTS_SPEC,
    _COURT_POSITIONS_SPEC,
    _EPIDEMICS_SPEC,
    _INSPIRATIONS_SPEC,
)
from chronicler.save.diff.titles import _diff_titles
from chronicler.save.diff.wars import _diff_wars

# Every snapshot-level differ, in dispatch order. Order is preserved from
# the pre-package implementation for review clarity; output is sorted by
# (c, t) at the end of diff_snapshots, so order only breaks ties among
# events sharing both keys.
STREAM_DIFFERS: list[StreamDiffer] = [
    _diff_one_character,
    _diff_titles,
    make_set_differ(_ALLIANCES_SPEC),
    _diff_wars,
    make_set_differ(_ARTIFACTS_SPEC),
    _diff_dynasty_legacies,
    _diff_dynasty_splendor,
    make_set_differ(_EPIDEMICS_SPEC),
    make_set_differ(_ACTIVITIES_SPEC),
    make_set_differ(_INSPIRATIONS_SPEC),
    _diff_contracts,
    make_set_differ(_COURT_POSITIONS_SPEC),
    _diff_domicile,
    _diff_constructions,
    _diff_traits,
    _diff_concubines,
    _diff_modifiers,
    _diff_perks,
    _diff_lifestyle_commits,
    _diff_miscarriage,
    _diff_state_changes,
]

DIFFERS = STREAM_DIFFERS

"""Golden tests against a real CK3 save file.

Every other parse/diff test in this repo builds ``SaveSnapshot`` /
``CharacterSnapshot`` / rakaly-JSON dicts by hand. That's fine for
testing the logic we know about, but it cannot catch the bug class
"CK3 stores X, we never modelled it on the snapshot, so synthetic
snapshots cannot surface it." (Audit ck3_chronicler-5r7g; recent
sibling bugs n0s4, gu7j, ei8t, 49lf, c3eg.)

This test runs against any ``.ck3`` file dropped under
``tests/fixtures/saves/``. If none is present, it skips with a clear
reason — the directory is gitignored because a real autosave is too
large to commit to GitHub (see ``tests/fixtures/saves/README.md``).

The two guards it provides:

1. Parse a real rakaly-produced JSON and assert ``parse_save`` returns
   a populated ``SaveSnapshot`` (characters, titles, lookup tables).
   Catches "rakaly bumped its output shape" silently.

2. Walk every raw character dict in ``living`` + ``dead_unprunable``
   and confirm its top-level key set is a subset of an explicit
   allowlist (:data:`_KNOWN_CHARACTER_KEYS`). New keys we haven't
   classified fail the test by name — so the next n0s4-class bug is a
   visible CI red instead of silent drift.

The test caches the rakaly JSON output beside the ``.ck3`` source on
first run so re-runs skip the ~3 second binary→JSON conversion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronicler.save.diff import diff_snapshots
from chronicler.save.parse import parse_save
from chronicler.save.rakaly import (
    RakalyNotFoundError,
    convert_save_to_json,
    find_rakaly,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "saves"


# Top-level keys ``_parse_character`` actively reads. Adding a field
# here means we already consume it.
_CHARACTER_KEYS_MODELLED: frozenset[str] = frozenset(
    {
        "alive_data",
        "dead_data",
        "nickname_text",
        "landed_data",
        "culture",
        "first_name",
        "female",
        "birth",
        "faith",
        "dynasty_house",
        "ethnicity",
        "traits",
        "family_data",
    }
)

# Top-level keys CK3 emits on character records that we know about but
# consciously do not model on ``CharacterSnapshot``. Each entry is a
# deliberate decision — if you uncomment a TODO from here, you're
# adding a new tracked field.
#
# Candidates that surface here but might be promoted to modelled in
# future:
#   - ``regnal_name`` — distinct from ``first_name`` for rulers
#     (e.g. "Charles V"); useful for biography copy.
#   - ``inactive_traits`` — relates to trait tier progression
#     (see ck3_chronicler-056).
#   - ``secret_faith`` — hidden faith adoption is a narrative event.
_CHARACTER_KEYS_KNOWN_UNUSED: frozenset[str] = frozenset(
    {
        "skill",  # stat array; not surfaced in chronicle
        "weight",  # personality/event weight noise
        "mass",  # body mass; not surfaced
        "court_data",  # courtier ID list; redundant with location/family
        "trait_xp_amounts",  # tracked separately at tier transitions
        "prowess_age",  # AI use only
        "sexuality",  # not surfaced in chronicle
        "playable_data",  # AI personality scores
        "recessive_traits",  # hidden trait genome
        "was_playable",  # historical flag, not load-bearing
        "dna",  # appearance hash; rendering only
        "secret_faith",  # candidate for future tracking
        "inactive_traits",  # candidate for future tracking (056)
        "nickname",  # localisation key; we use nickname_text instead
        "ai",  # AI personality scores
        "regnal_name",  # candidate for future tracking
        "portrait_override",  # custom-portrait override; render-time only
    }
)

_KNOWN_CHARACTER_KEYS: frozenset[str] = _CHARACTER_KEYS_MODELLED | _CHARACTER_KEYS_KNOWN_UNUSED


def _discover_saves() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(FIXTURES_DIR.glob("*.ck3"))


def _rakaly_json_for(save_path: Path) -> dict:
    """Load (or produce + cache) the rakaly JSON output for ``save_path``.

    Cached output lives alongside the source as
    ``<name>.rakaly.json`` — gitignored, see ``.gitignore``. Re-runs
    of the test skip the binary→JSON conversion (~3s on a 26MB save).
    """
    cache = save_path.with_suffix(".rakaly.json")
    if cache.is_file() and cache.stat().st_mtime >= save_path.stat().st_mtime:
        with cache.open("r", encoding="utf-8") as f:
            return json.load(f)

    if find_rakaly() is None:
        pytest.skip(
            "rakaly binary not found on PATH or in repo-local rakaly-*/ — "
            "see scripts/run_rakaly.py for install instructions"
        )

    try:
        data = convert_save_to_json(save_path)
    except RakalyNotFoundError:  # pragma: no cover — guarded above
        pytest.skip("rakaly binary not found")

    with cache.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


_SAVES = _discover_saves()
if not _SAVES:
    pytest.skip(
        "no real-save fixture under tests/fixtures/saves/*.ck3 — "
        "see tests/fixtures/saves/README.md to add one locally",
        allow_module_level=True,
    )


@pytest.mark.parametrize("save_path", _SAVES, ids=lambda p: p.name)
def test_real_save_parses_to_populated_snapshot(save_path: Path) -> None:
    """parse_save against a real rakaly JSON returns a populated snapshot."""
    data = _rakaly_json_for(save_path)
    snap = parse_save(data)

    assert snap.current_date, f"current_date empty for {save_path.name}"
    assert snap.playthrough_id, f"playthrough_id empty for {save_path.name}"
    assert len(snap.characters) > 100, (
        f"only {len(snap.characters)} characters parsed from {save_path.name}; "
        "expected a mid-game save to carry thousands"
    )
    assert snap.titles, f"no landed_titles parsed from {save_path.name}"
    assert snap.cultures_lookup, f"no cultures_lookup populated from {save_path.name}"
    assert snap.faiths_lookup, f"no faiths_lookup populated from {save_path.name}"
    assert snap.dynasties_lookup, f"no dynasties_lookup populated from {save_path.name}"
    assert snap.traits_lookup, f"no traits_lookup parsed from {save_path.name}"


@pytest.mark.parametrize("save_path", _SAVES, ids=lambda p: p.name)
def test_real_save_character_keys_are_in_allowlist(save_path: Path) -> None:
    """No unmodelled top-level keys appear on character records.

    This is the n0s4-style bug-class detector. CK3 (or a rakaly version
    bump) introducing a new top-level key on character records will
    fail this test by name — surfacing the new field for triage rather
    than letting it silently bypass the parser.

    To resolve a failure:
    - If the new field is bug-class (something the chronicle should
      track), model it in ``_parse_character`` and add it to
      :data:`_CHARACTER_KEYS_MODELLED`.
    - If we consciously don't care, add it to
      :data:`_CHARACTER_KEYS_KNOWN_UNUSED` with a one-line reason.
    """
    data = _rakaly_json_for(save_path)
    observed: set[str] = set()
    for bucket_name in ("living", "dead_unprunable"):
        bucket = data.get(bucket_name) or {}
        if not isinstance(bucket, dict):
            continue
        for raw in bucket.values():
            if isinstance(raw, dict):
                observed.update(raw.keys())

    unknown = observed - _KNOWN_CHARACTER_KEYS
    assert not unknown, (
        f"new top-level character keys in {save_path.name}: "
        f"{sorted(unknown)} — classify as modelled (parse them) or "
        f"known-unused (document why) in tests/integration/"
        f"test_real_save_golden.py. This is a potential n0s4-class "
        f"bug surface."
    )


@pytest.mark.parametrize("save_path", _SAVES, ids=lambda p: p.name)
def test_real_save_identity_diff_is_empty(save_path: Path) -> None:
    """diff_snapshots(snap, snap) on a real save produces zero events.

    Catches diff-layer regressions where stable state would generate
    spurious events — by definition a snapshot diffed against itself
    must surface nothing.
    """
    data = _rakaly_json_for(save_path)
    snap = parse_save(data)
    events = diff_snapshots(snap, snap)
    assert events == [], (
        f"identity diff on {save_path.name} produced {len(events)} events; "
        f"first: {events[0] if events else None}"
    )

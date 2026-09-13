"""Shared helpers for the test_ingest_* save-pipeline suites (ck3_chronicler-27ov.72).

Snapshot builders and parse-patching helpers lifted out of the
test_save_ingest monolith when it was split into concern files, so the
split files (and test_crash_recovery) share one definition instead of
re-importing across test modules.
"""

from __future__ import annotations

from chronicler.save.parse import (
    CharacterSnapshot,
    FamilySnapshot,
    MemorySnapshot,
    SaveSnapshot,
)
from tests.helpers.providers import RecordingProvider
from tests.helpers.snapshots import make_char


def _FakeProvider() -> RecordingProvider:
    """Save-ingest scheduler double; see tests.helpers.providers (audit L37)."""
    return RecordingProvider(
        provider_name="fake-save-ingest:v1",
        response_text=lambda req: f"bio for {req.metadata.get('character_id')}",
        input_tokens=10,
        output_tokens=20,
    )


def _char(
    cid: int,
    *,
    is_dead: bool = False,
    death_date: str | None = None,
    family: FamilySnapshot | None = None,
    location_id: int | None = 100,
    memories: tuple[MemorySnapshot, ...] = (),
) -> CharacterSnapshot:
    return make_char(
        cid,
        is_dead=is_dead,
        death_date=death_date,
        family=family or FamilySnapshot(),
        location_id=location_id,
        memories=memories,
    )


def _snap(chars: dict[int, CharacterSnapshot], *, date: str = "1067.2.1") -> SaveSnapshot:
    return SaveSnapshot(
        playthrough_id="test-uuid",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=list(chars.keys())[0] if chars else None,
        characters=chars,
    )


def _patch_parse_save(monkeypatch, fn):
    """Patch the startup parse (sync) AND the save-tail consumer dispatch.

    ``fn`` returns the legacy ``(raw_save_data, SaveSnapshot)`` tuple — the
    shape the startup verify/drain path (``_parse_save_at_with_raw``) and the
    old async consumer (``_parse_save_at_with_raw_async``) expect.

    ck3_chronicler-j86v: the live consumer now dispatches melt+decode+parse to
    a ProcessPoolExecutor via ``_dispatch_parse``, which returns the
    SaveSnapshot ONLY (a real pool runs the worker in a separate process where
    these monkeypatches wouldn't apply — so tests must patch the dispatch seam
    itself). We patch ``_dispatch_parse`` to return ``fn(path)[1]`` so every
    existing test keeps injecting canned snapshots through its same ``fn``.
    """

    async def _async_fn(path):
        return fn(path)

    async def _dispatch_fn(pool, path, tracked_ids):
        return fn(path)[1]

    monkeypatch.setattr("chronicler.save.ingest._parse_save_at_with_raw", fn)
    monkeypatch.setattr("chronicler.save.ingest._parse_save_at_with_raw_async", _async_fn)
    monkeypatch.setattr("chronicler.save.ingest._dispatch_parse", _dispatch_fn)


def _snap_pt(
    pt_id: str, date: str, chars: dict[int, CharacterSnapshot] | None = None
) -> SaveSnapshot:
    """SaveSnapshot helper with explicit playthrough_id + date.

    The default ``_snap`` hardcodes a single test playthrough; these
    tests need to construct two-playthrough scenarios."""
    return SaveSnapshot(
        playthrough_id=pt_id,
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date=date,
        player_character_id=list((chars or {}).keys())[0] if chars else None,
        characters=chars or {},
    )


async def _no_op_watch(*_args, **_kwargs):
    """An async generator that yields nothing — substitutes for
    ``watch_saves`` so ``run_save_ingest`` exits the loop body
    immediately after startup, letting tests inspect the post-startup
    state."""
    if False:
        yield  # pragma: no cover -- shapes the function as an async generator

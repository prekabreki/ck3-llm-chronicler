"""Tests for chronicler.save.cache — per-campaign save-file cache.

The cache lives between watchfiles' "save changed" notification and
the rakaly+diff pipeline. It absorbs CK3's autosave rotation so even
at Speed 5 (when autosaves rotate faster than rakaly can parse) we
never lose the source file.

Each test exercises one of the acceptance-criteria flows from
ck3_chronicler-1js: copy-then-process, multi-pending replay,
restart-with-pending, GC bounded by max-bytes, idempotent mark-done.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from chronicler.save import cache as cache_module
from chronicler.save.cache import (
    DEFAULT_MAX_CACHE_BYTES,
    SaveCache,
    cache_dir_for,
)


def _write_save(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_cache_save_copies_file_and_returns_metadata(tmp_path: Path) -> None:
    src = _write_save(tmp_path / "autosave.ck3", b"SAV01\nfake save content")
    cache = SaveCache(tmp_path / "cache")

    cached = cache.cache_save(src)

    assert cached is not None
    assert cached.seqno == 1
    assert cached.size_bytes == len(b"SAV01\nfake save content")
    assert cached.path.name == "000000000001.ck3"
    assert cached.path.read_bytes() == src.read_bytes()


def test_cache_dir_is_created(tmp_path: Path) -> None:
    target = tmp_path / "data" / "save-cache" / "live-session"
    assert not target.exists()
    SaveCache(target)
    assert target.is_dir()


def test_seqno_monotonic_within_run(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    a = _write_save(tmp_path / "a.ck3", b"a")
    b = _write_save(tmp_path / "b.ck3", b"b")
    c = _write_save(tmp_path / "c.ck3", b"c")

    ca = cache.cache_save(a)
    cb = cache.cache_save(b)
    cc = cache.cache_save(c)

    assert (ca, cb, cc) != (None, None, None)
    seqnos = [ca.seqno, cb.seqno, cc.seqno]  # type: ignore[union-attr]
    assert seqnos == [1, 2, 3]


def test_pending_returns_caches_in_seqno_order(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    cache.cache_save(_write_save(tmp_path / "b.ck3", b"bb"))
    cache.cache_save(_write_save(tmp_path / "c.ck3", b"ccc"))

    pending = cache.pending()
    assert [p.seqno for p in pending] == [1, 2, 3]
    assert [p.size_bytes for p in pending] == [1, 2, 3]


def test_seqno_persists_across_restart(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_a = SaveCache(cache_dir)
    cache_a.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    cache_a.cache_save(_write_save(tmp_path / "b.ck3", b"bb"))

    # Simulate a restart: new SaveCache over the same dir.
    cache_b = SaveCache(cache_dir)
    new_cache = cache_b.cache_save(_write_save(tmp_path / "c.ck3", b"ccc"))

    assert new_cache is not None
    assert new_cache.seqno == 3
    assert [p.seqno for p in cache_b.pending()] == [1, 2, 3]


def test_mark_processed_deletes_file_and_drops_from_pending(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    c2 = cache.cache_save(_write_save(tmp_path / "b.ck3", b"bb"))
    assert c1 is not None and c2 is not None

    cache.mark_processed(c1)

    assert not c1.path.exists()
    pending = cache.pending()
    assert [p.seqno for p in pending] == [2]


def test_mark_processed_is_idempotent(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert c1 is not None

    cache.mark_processed(c1)
    # Calling again on a non-existent file must not raise.
    cache.mark_processed(c1)


def test_orphan_tmp_cleaned_on_init(tmp_path: Path) -> None:
    """A previous run that crashed mid-copy may leave .tmp files behind.
    They are pruned on init so they don't accumulate or clash with new
    seqnos."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "000000000005.tmp").write_bytes(b"partial")
    (cache_dir / "garbage.tmp").write_bytes(b"???")

    SaveCache(cache_dir)

    assert not (cache_dir / "000000000005.tmp").exists()
    assert not (cache_dir / "garbage.tmp").exists()


def test_unrelated_files_in_cache_dir_are_ignored(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    # A cached save from a previous run.
    (cache_dir / "000000000007.ck3").write_bytes(b"cached")
    # Unrelated files (e.g. a stray text note from the user) must not
    # appear in pending() and must not break seqno computation.
    (cache_dir / "README.txt").write_bytes(b"hi")
    (cache_dir / "manual_save.ck3").write_bytes(b"manual")

    cache = SaveCache(cache_dir)
    pending = cache.pending()
    assert [p.seqno for p in pending] == [7]

    # New caches resume from 8 (max seen + 1 across pattern-matching files).
    new_src = _write_save(tmp_path / "new.ck3", b"new")
    nc = cache.cache_save(new_src)
    assert nc is not None
    assert nc.seqno == 8


def test_cache_save_returns_none_when_source_missing(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    missing = tmp_path / "never_existed.ck3"

    result = cache.cache_save(missing)

    assert result is None
    # Failed copy must not advance the seqno.
    real = _write_save(tmp_path / "real.ck3", b"x")
    cached = cache.cache_save(real)
    assert cached is not None
    assert cached.seqno == 1


def test_total_bytes_sums_pending(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    cache.cache_save(_write_save(tmp_path / "b.ck3", b"x" * 250))
    assert cache.total_bytes() == 350


def test_gc_oldest_drops_oldest_until_under_cap(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    cache.cache_save(_write_save(tmp_path / "b.ck3", b"x" * 100))
    cache.cache_save(_write_save(tmp_path / "c.ck3", b"x" * 100))
    cache.cache_save(_write_save(tmp_path / "d.ck3", b"x" * 100))
    assert cache.total_bytes() == 400

    deleted = cache.gc_oldest(max_bytes=200)

    assert deleted == 2
    pending = cache.pending()
    # Oldest two (seqno 1 and 2) gone, newest two survive.
    assert [p.seqno for p in pending] == [3, 4]
    assert cache.total_bytes() == 200


def test_gc_oldest_is_noop_when_under_cap(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 50))
    cache.cache_save(_write_save(tmp_path / "b.ck3", b"x" * 50))

    deleted = cache.gc_oldest(max_bytes=1000)

    assert deleted == 0
    assert [p.seqno for p in cache.pending()] == [1, 2]


def test_gc_oldest_uses_default_cap_when_unspecified(tmp_path: Path) -> None:
    """Default cap is 2 GB — well above any test fixture, so a no-op."""
    cache = SaveCache(tmp_path / "cache")
    cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))

    assert cache.gc_oldest() == 0  # uses DEFAULT_MAX_CACHE_BYTES
    assert DEFAULT_MAX_CACHE_BYTES > 100


def test_cache_dir_for_layout(tmp_path: Path) -> None:
    """The per-campaign cache dir is data_dir/save-cache/<campaign_id>."""
    assert cache_dir_for(tmp_path, "live-session") == tmp_path / "save-cache" / "live-session"
    assert cache_dir_for(tmp_path, "fresh-test") == tmp_path / "save-cache" / "fresh-test"


def test_pending_handles_empty_cache(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    assert cache.pending() == []
    assert cache.total_bytes() == 0
    assert cache.gc_oldest(max_bytes=0) == 0


def test_replay_after_restart_preserves_arrival_order(tmp_path: Path) -> None:
    """The bootstrap-recovery path: cache up several saves, simulate a
    crash before any are processed, restart over the same cache dir,
    drain pending in the same arrival order they came in."""
    cache_dir = tmp_path / "cache"

    # First run: cache 3 saves, process 1, crash before the rest.
    run1 = SaveCache(cache_dir)
    cs = [
        run1.cache_save(_write_save(tmp_path / f"s{i}.ck3", f"save {i}".encode())) for i in range(3)
    ]
    assert all(c is not None for c in cs)
    run1.mark_processed(cs[0])  # type: ignore[arg-type]

    # Simulate crash: restart.
    run2 = SaveCache(cache_dir)
    pending = run2.pending()
    # Only seqnos 2 and 3 remain (1 was processed); they replay in arrival order.
    assert [p.seqno for p in pending] == [2, 3]
    assert pending[0].path.read_bytes() == b"save 1"
    assert pending[1].path.read_bytes() == b"save 2"


@pytest.mark.parametrize("n", [10, 100])
def test_seqno_lex_sort_matches_numeric_sort(tmp_path: Path, n: int) -> None:
    """Filename zero-padding width must be wide enough that lex sort
    matches numeric sort for any plausible cache lifetime. 12 digits ⇒
    1 trillion entries — comfortably more than a single campaign will
    ever cache."""
    cache = SaveCache(tmp_path / "cache")
    for i in range(n):
        cache.cache_save(_write_save(tmp_path / f"s{i}.ck3", b"x"))

    pending = cache.pending()
    seqnos = [p.seqno for p in pending]
    assert seqnos == sorted(seqnos)
    # And the underlying filenames are also lex-sorted ascending.
    names = [p.path.name for p in pending]
    assert names == sorted(names)


# --- ck3_chronicler-7w5: snapshot() observability + GC counter ---


def test_snapshot_reports_pending_and_bytes(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    src1 = _write_save(tmp_path / "s1.ck3", b"x" * 100)
    src2 = _write_save(tmp_path / "s2.ck3", b"y" * 200)
    cache.cache_save(src1)
    cache.cache_save(src2)
    snap = cache.snapshot()
    assert snap == {"pending": 2, "bytes": 300, "gc_drops_lifetime": 0}


def test_snapshot_after_mark_processed_drops_count(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "s.ck3", b"x" * 50))
    assert cached is not None
    cache.mark_processed(cached)
    snap = cache.snapshot()
    assert snap["pending"] == 0
    assert snap["bytes"] == 0


def test_snapshot_increments_gc_drops_after_eviction(tmp_path: Path) -> None:
    """Run the GC under a cap that leaves room for one save: drops 2
    of 3 cached saves (total 300 bytes; cap 150 ⇒ keep newest 100,
    drop 200 worth of older ones)."""
    cache = SaveCache(tmp_path / "cache")
    for i in range(3):
        cache.cache_save(_write_save(tmp_path / f"s{i}.ck3", b"x" * 100))
    deleted = cache.gc_oldest(max_bytes=150)
    assert deleted == 2
    snap = cache.snapshot()
    assert snap["pending"] == 1
    assert snap["gc_drops_lifetime"] == 2


def test_gc_drops_lifetime_accumulates_across_calls(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    for i in range(3):
        cache.cache_save(_write_save(tmp_path / f"s{i}.ck3", b"x" * 100))
    cache.gc_oldest(max_bytes=150)  # drops 2
    for i in range(3, 6):
        cache.cache_save(_write_save(tmp_path / f"s{i}.ck3", b"x" * 100))
    cache.gc_oldest(max_bytes=150)  # drops more
    assert cache.snapshot()["gc_drops_lifetime"] >= 4


def test_snapshot_after_restart_resets_lifetime_drops(tmp_path: Path) -> None:
    """gc_drops_lifetime is process-local — a fresh SaveCache instance
    starts at 0 even when the on-disk cache already has pending saves."""
    cache = SaveCache(tmp_path / "cache")
    for i in range(3):
        cache.cache_save(_write_save(tmp_path / f"s{i}.ck3", b"x" * 100))
    cache.gc_oldest(max_bytes=150)
    # Restart
    cache2 = SaveCache(tmp_path / "cache")
    snap = cache2.snapshot()
    assert snap["gc_drops_lifetime"] == 0
    assert snap["pending"] == 1


# --- ck3_chronicler-5x47: in-flight protect + cap bump ---


def test_default_cap_is_8_gib() -> None:
    # Adventurer-mode 2026-05-17 smoke produced ~160 saves at ~72 MB each
    # in 55 minutes — the old 2 GB cap held only ~28 and dropped saves the
    # ingest worker still needed. 8 GB holds ~110, well past the steady-state
    # speed-5 backlog.
    assert DEFAULT_MAX_CACHE_BYTES == 8 * 1024 * 1024 * 1024


def test_protected_save_survives_gc_when_it_would_be_oldest(tmp_path: Path) -> None:
    """When the consumer is mid-rakaly on a cached save, GC must not drop
    that file out from under it — even when it's the oldest pending."""
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    c2 = cache.cache_save(_write_save(tmp_path / "b.ck3", b"x" * 100))
    c3 = cache.cache_save(_write_save(tmp_path / "c.ck3", b"x" * 100))
    assert (c1, c2, c3) != (None, None, None)

    cache.protect(c1.seqno)  # type: ignore[union-attr]

    # Cap leaves room for one save. Without protection, gc would drop the
    # two oldest (c1 + c2). With c1 protected, gc must skip it and drop c2
    # + c3 instead (the next-oldest unprotected).
    deleted = cache.gc_oldest(max_bytes=100)

    assert deleted == 2
    pending_seqnos = [p.seqno for p in cache.pending()]
    assert pending_seqnos == [c1.seqno]  # type: ignore[union-attr]


def test_protect_is_idempotent(tmp_path: Path) -> None:
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    assert c1 is not None

    cache.protect(c1.seqno)
    cache.protect(c1.seqno)  # second call must not raise


def test_gc_keeps_protected_save_even_when_alone_over_cap(tmp_path: Path) -> None:
    """Pathological case: only one save exists, it's larger than cap, and
    it's protected. GC must drop nothing — the consumer is parsing it."""
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 200))
    assert c1 is not None
    cache.protect(c1.seqno)

    deleted = cache.gc_oldest(max_bytes=50)

    assert deleted == 0
    assert [p.seqno for p in cache.pending()] == [c1.seqno]


def test_mark_processed_clears_protection(tmp_path: Path) -> None:
    """Once the consumer marks the save processed, the protection entry
    should not leak (otherwise long-running sessions accumulate stale
    seqnos in the protect set)."""
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    assert c1 is not None

    cache.protect(c1.seqno)
    cache.mark_processed(c1)

    # Re-using the same seqno (won't happen in practice, but cheapest probe
    # for "is the seqno still in the protect set after mark_processed").
    assert c1.seqno not in cache._protected  # type: ignore[attr-defined]


# --- ck3_chronicler-dqtk: hardlink dedup across same volume ---


def test_cache_save_uses_hardlink_when_same_volume(tmp_path: Path) -> None:
    """The default path on a single-volume layout: cache_save hardlinks
    the source autosave into the cache dir instead of copying. Both names
    share an inode so the cache costs zero additional disk space."""
    src = _write_save(tmp_path / "autosave.ck3", b"hello world")
    cache = SaveCache(tmp_path / "cache")

    cached = cache.cache_save(src)

    assert cached is not None
    assert cached.path.read_bytes() == b"hello world"
    # Hardlink invariant: same inode = same file content, two names.
    assert os.stat(src).st_ino == os.stat(cached.path).st_ino
    # nlink jumped from 1 to 2 (source name + cache name).
    assert os.stat(cached.path).st_nlink >= 2


def test_cache_save_dedupes_across_two_campaigns(tmp_path: Path) -> None:
    """The real-world payoff: two campaigns watching the same save dir both
    cache every CK3 autosave. Pre-dqtk, this consumed 2× disk per save. Now
    both cache dirs hold hardlinks to a single inode."""
    src = _write_save(tmp_path / "autosave.ck3", b"x" * 4096)
    cache_a = SaveCache(tmp_path / "cache_a")
    cache_b = SaveCache(tmp_path / "cache_b")

    cached_a = cache_a.cache_save(src)
    cached_b = cache_b.cache_save(src)

    assert cached_a is not None
    assert cached_b is not None
    # All three names resolve to the same inode.
    src_ino = os.stat(src).st_ino
    assert os.stat(cached_a.path).st_ino == src_ino
    assert os.stat(cached_b.path).st_ino == src_ino
    # Source + 2 caches = 3 links to the inode.
    assert os.stat(src).st_nlink == 3


def test_cache_save_falls_back_to_copy_when_os_link_raises_exdev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When os.link raises EXDEV (cross-device) — the canonical case on
    POSIX when source and cache live on different mount points — cache_save
    falls back to shutil.copy2 and the cache file is a real copy (own inode)."""
    src = _write_save(tmp_path / "autosave.ck3", b"copy-me")
    cache = SaveCache(tmp_path / "cache")

    def fake_link(src_path: object, dst_path: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(cache_module.os, "link", fake_link)
    # Reset the one-shot warning set so this test's fallback emits a log
    # regardless of test ordering.
    monkeypatch.setattr(cache_module, "_HARDLINK_FALLBACK_WARNED", set())

    cached = cache.cache_save(src)

    assert cached is not None
    assert cached.path.read_bytes() == b"copy-me"
    # Fallback path used shutil.copy2 — distinct inode.
    assert os.stat(cached.path).st_ino != os.stat(src).st_ino


def test_cache_save_skips_link_when_source_on_different_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-flight cross-volume detection (Windows splitdrive) avoids even
    trying os.link when the drives don't match. The save still lands as a
    copy."""
    src = _write_save(tmp_path / "autosave.ck3", b"xyz")
    cache = SaveCache(tmp_path / "cache")

    link_calls: list[tuple[object, object]] = []

    def watch_link(src_path: object, dst_path: object) -> None:
        link_calls.append((src_path, dst_path))

    def fake_splitdrive(p: str) -> tuple[str, str]:
        if "autosave.ck3" in p:
            return ("C:", p)
        return ("D:", p)

    monkeypatch.setattr(cache_module.os, "link", watch_link)
    monkeypatch.setattr(cache_module.os.path, "splitdrive", fake_splitdrive)
    monkeypatch.setattr(cache_module, "_HARDLINK_FALLBACK_WARNED", set())

    cached = cache.cache_save(src)

    assert cached is not None
    assert cached.path.read_bytes() == b"xyz"
    # The pre-flight detected different drives so os.link was never called.
    assert link_calls == []


def test_cache_save_returns_none_when_link_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CK3-rotation race: os.link reports FileNotFoundError when the
    source vanishes between detection and link. Caller surfaces None and
    does NOT burn a seqno (same semantics as the pre-hardlink copy path)."""
    src = _write_save(tmp_path / "autosave.ck3", b"data")
    cache = SaveCache(tmp_path / "cache")

    def fake_link(src_path: object, dst_path: object) -> None:
        raise FileNotFoundError(2, "source vanished")

    monkeypatch.setattr(cache_module.os, "link", fake_link)

    result = cache.cache_save(src)

    assert result is None
    # Seqno didn't advance — the next successful cache uses seqno 1.
    monkeypatch.undo()
    real = _write_save(tmp_path / "real.ck3", b"x")
    cached = cache.cache_save(real)
    assert cached is not None
    assert cached.seqno == 1


# --- ck3_chronicler-2faj: playthrough_id sidecar metadata ---


def test_cached_save_playthrough_id_defaults_to_none(tmp_path: Path) -> None:
    """A freshly-cached save has no playthrough_id yet — the producer
    only knows it after rakaly parses the file."""
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert cached is not None
    assert cached.playthrough_id is None


def test_tag_playthrough_persists_in_sidecar(tmp_path: Path) -> None:
    """tag_playthrough writes a sidecar .meta.json next to the .ck3, so
    subsequent calls to pending() can read the playthrough_id without
    re-running rakaly."""
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert cached is not None

    cache.tag_playthrough(cached, "uuid-A")

    sidecar = cached.path.with_suffix(".meta.json")
    assert sidecar.exists()


def test_pending_populates_playthrough_id_from_sidecar(tmp_path: Path) -> None:
    """After tag_playthrough, a fresh pending() scan surfaces the
    playthrough_id on the CachedSave so the drain can short-circuit
    foreign saves before invoking rakaly."""
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert cached is not None
    cache.tag_playthrough(cached, "uuid-A")

    pending = cache.pending()

    assert len(pending) == 1
    assert pending[0].playthrough_id == "uuid-A"


def test_mark_processed_deletes_sidecar(tmp_path: Path) -> None:
    """mark_processed removes both the .ck3 and the .meta.json — the
    cache entry is fully gone after processing."""
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert cached is not None
    cache.tag_playthrough(cached, "uuid-A")
    sidecar = cached.path.with_suffix(".meta.json")
    assert sidecar.exists()

    cache.mark_processed(cached)

    assert not cached.path.exists()
    assert not sidecar.exists()


def test_pending_handles_sidecar_without_ck3(tmp_path: Path) -> None:
    """An orphan sidecar (e.g. left over from a crash that deleted the
    .ck3 but not the .meta.json) must not appear in pending() — only
    real saves with their .ck3 still present should drain."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "000000000001.meta.json").write_text(
        '{"playthrough_id": "uuid-A"}', encoding="utf-8"
    )

    cache = SaveCache(cache_dir)

    assert cache.pending() == []


def test_pending_handles_corrupt_sidecar(tmp_path: Path) -> None:
    """A malformed sidecar must not break pending(); the cached save
    surfaces with playthrough_id=None so the drain falls back to a
    full parse (fail-safe, not fail-stop)."""
    cache = SaveCache(tmp_path / "cache")
    cached = cache.cache_save(_write_save(tmp_path / "a.ck3", b"a"))
    assert cached is not None
    cached.path.with_suffix(".meta.json").write_text("{not valid json", encoding="utf-8")

    pending = cache.pending()

    assert len(pending) == 1
    assert pending[0].playthrough_id is None


def test_gc_oldest_deletes_sidecar_alongside_ck3(tmp_path: Path) -> None:
    """When gc_oldest evicts a cached save, its sidecar must go with
    it — otherwise the cache dir accumulates orphan sidecars across
    sustained Speed-5 sessions."""
    cache = SaveCache(tmp_path / "cache")
    c1 = cache.cache_save(_write_save(tmp_path / "a.ck3", b"x" * 100))
    c2 = cache.cache_save(_write_save(tmp_path / "b.ck3", b"y" * 100))
    assert c1 is not None and c2 is not None
    cache.tag_playthrough(c1, "uuid-A")
    cache.tag_playthrough(c2, "uuid-A")
    s1 = c1.path.with_suffix(".meta.json")
    s2 = c2.path.with_suffix(".meta.json")

    # Cap forces eviction of the oldest (c1).
    cache.gc_oldest(max_bytes=150)

    assert not c1.path.exists()
    assert not s1.exists()
    assert c2.path.exists()
    assert s2.exists()

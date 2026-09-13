"""Save-cache layer (ck3_chronicler-1js).

CK3's autosave rotation drops files we never had time to parse — at
Speed 5, in-game months tick in ~6 real seconds and rakaly takes 5-10s
on a ~50 MB save. The 3-deep autosave backup buys ~12 real seconds of
grace; beyond that, we silently lose any state-diff event that
completed-and-undid-itself within the gap (marriage→divorce, travel
A→B→C→A, trait gained-then-lost, alliance formed-then-broken,
nickname changes between gaps).

This module decouples "CK3 wrote a save" from "rakaly finished
parsing it". The save-tail watcher copies each fresh autosave into our
own cache the moment it appears (50 MB ≈ 100 ms on SSD), then a
catch-up worker drains the cache in chronological order and ingests
each one in turn. Even if CK3 rotates the original autosave away, our
cached copy survives.

Cache layout::

    <chronicler-data-dir>/save-cache/<campaign_id>/{seqno:012d}.ck3

Monotonically-increasing per-campaign seqnos so chronological order is
preserved by lexicographic sort of filenames. Atomic write: copies
first to ``{seqno:012d}.tmp``, then renames over the final ``.ck3``
filename. A crash mid-copy leaves a ``.tmp`` file that gets pruned on
next init.

GC: callers ``mark_processed`` each cached save after successful
ingest, which deletes the file. :meth:`SaveCache.gc_oldest` enforces
a total-bytes cap by dropping the oldest pending caches if the cap is
exceeded — sustained Speed-5 sessions that outpace ingestion still
get bounded disk usage.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# ck3_chronicler-dqtk: one-shot warning when hardlinking is unavailable
# for a particular (source_volume, cache_volume) pair. The cross-campaign
# watcher caches the same source autosave into every active campaign's
# cache dir; hardlinks share the inode so 2 campaigns cost 1 file's worth
# of disk instead of 2. When the user's CK3 save dir and chronicler data
# dir live on different volumes (or any OSError suggests hardlink isn't
# available), we fall back to shutil.copy2 and warn once so the user can
# co-locate them if disk pressure matters.
_HARDLINK_FALLBACK_WARNED: set[tuple[str, str]] = set()

# Default cache size cap. CK3 autosaves are 30-100 MB; 8 GB allows
# ~110+ pending saves before we start dropping the oldest. Configurable
# at gc_oldest call time.
#
# Cap was 2 GB up to ck3_chronicler-5x47 — adventurer-mode Speed-5 sessions
# observed 2026-05-17 generated ~160 saves in 55 minutes, blowing past the
# old cap mid-session and triggering GC to drop pending saves the ingest
# worker still needed. Bumping to 8 GB keeps the steady-state Speed-5
# backlog comfortably under cap; the in-flight protect set (see
# :meth:`SaveCache.protect`) plugs the residual race where the file
# currently being parsed by rakaly vanishes under the consumer.
DEFAULT_MAX_CACHE_BYTES = 8 * 1024 * 1024 * 1024

_FILENAME_PATTERN = re.compile(r"^(\d{12})\.ck3$")
_SEQNO_WIDTH = 12

# ck3_chronicler-2faj: sidecar carries the parsed playthrough_id so a
# later drain can drop foreign saves without re-running rakaly. Named
# ``.meta.json`` (sibling to the ``.ck3``) so atomic delete in
# mark_processed and gc_oldest is a two-unlink pair against deterministic
# paths.
_SIDECAR_SUFFIX = ".meta.json"


def _sidecar_path_for(ck3_path: Path) -> Path:
    return ck3_path.with_suffix(_SIDECAR_SUFFIX)


def _read_playthrough_sidecar(ck3_path: Path) -> str | None:
    """Read the playthrough_id from a sidecar next to ``ck3_path``.

    Returns None when the sidecar is missing, unreadable, malformed, or
    lacks a ``playthrough_id`` key. The drain falls back to a full rakaly
    parse on None — fail-safe, never fail-stop.
    """
    sidecar = _sidecar_path_for(ck3_path)
    if not sidecar.is_file():
        return None
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("ignoring corrupt cache sidecar %s: %s", sidecar.name, e)
        return None
    pid = data.get("playthrough_id")
    return pid if isinstance(pid, str) and pid else None


@dataclass(frozen=True, slots=True)
class CachedSave:
    """A save file copied into our cache and ready for ingestion.

    ``playthrough_id`` (ck3_chronicler-2faj): set after the first rakaly
    parse via :meth:`SaveCache.tag_playthrough`, persisted as a sidecar
    .meta.json next to the .ck3. On subsequent pending() scans, the
    sidecar is read back so the drain can short-circuit foreign-
    playthrough saves before invoking rakaly. None on first observation.
    """

    path: Path
    seqno: int
    size_bytes: int
    playthrough_id: str | None = None


def cache_dir_for(data_dir: Path, campaign_id: str) -> Path:
    """Compute the per-campaign cache directory under a chronicler data dir."""
    return data_dir / "save-cache" / campaign_id


def pending_in(cache_dir: Path) -> list[CachedSave]:
    """Scan ``cache_dir`` for pending cached saves, oldest seqno first.

    Free function rather than a :class:`SaveCache` method because it reads
    nothing but the directory — no instance state is involved — and issue #2
    needs the same count from a REST route that has no live SaveCache to ask.
    :meth:`SaveCache.pending` delegates here so the number the resync route
    returns and the number the SSE ``cache_state`` frame carries can never be
    two different definitions of "pending".

    Missing directory reads as empty rather than raising: a campaign that has
    never ingested has no cache dir, and "nothing pending" is the truthful
    answer for it.

    ck3_chronicler-2faj: each entry is enriched with its ``playthrough_id``
    from the sidecar .meta.json if one exists, so the drain can short-circuit
    foreign saves without re-parsing. Orphan sidecars (no matching .ck3) are
    skipped — only real saves appear.
    """
    out: list[CachedSave] = []
    try:
        entries = list(cache_dir.iterdir())
    except OSError:
        return out
    for entry in entries:
        m = _FILENAME_PATTERN.match(entry.name)
        if m is None:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        out.append(
            CachedSave(
                path=entry,
                seqno=int(m.group(1)),
                size_bytes=size,
                playthrough_id=_read_playthrough_sidecar(entry),
            )
        )
    out.sort(key=lambda c: c.seqno)
    return out


def snapshot_dir(cache_dir: Path) -> dict[str, int]:
    """Issue #2: the disk-derived half of :meth:`SaveCache.snapshot`, with no
    side effects — constructing a SaveCache would mkdir and prune orphan
    .tmp files, which a GET must not do.

    Returns ``pending`` and ``bytes`` only. ``gc_drops_lifetime`` is a
    counter held by the running ingest loop's SaveCache instance and leaves
    no trace on disk, so it is deliberately absent rather than reported as a
    0 that would look like fact.
    """
    pending = pending_in(cache_dir)
    return {"pending": len(pending), "bytes": sum(c.size_bytes for c in pending)}


def _materialize_cache(source: Path, tmp: Path) -> None:
    """Materialize ``tmp`` from ``source`` — hardlink when possible, copy otherwise.

    Hardlinking instead of copying saves disk space when multiple campaigns
    cache the same CK3 autosave: the cross-campaign watcher loops every
    autosave into each active campaign's cache dir, so 1 CK3 save would
    otherwise become N copies. NTFS keeps the inode alive while any name
    still references it, so the cache file survives CK3's autosave rotation
    even after the original source name is deleted.

    Same-volume detection (Windows): ``os.path.splitdrive`` compares the
    drive letter or UNC root. Different drives short-circuit straight to
    copy — Windows hardlinks can't span volumes. On POSIX ``splitdrive``
    always returns ``""`` so we always try ``os.link``; if mount points
    differ we get an ``EXDEV`` OSError and fall through to copy.

    Falls back to ``shutil.copy2`` on any OSError from ``os.link``. A
    one-shot WARNING per (source_volume, cache_volume) pair flags the
    degraded mode so the user can co-locate the dirs if disk pressure
    matters. The fallback preserves all existing error semantics — the
    caller's ``FileNotFoundError`` / ``OSError`` handlers in
    :meth:`SaveCache.cache_save` still fire.
    """
    source_drive = os.path.splitdrive(os.fspath(source))[0]
    tmp_drive = os.path.splitdrive(os.fspath(tmp))[0]
    if source_drive == tmp_drive:
        try:
            os.link(source, tmp)
            return
        except FileNotFoundError:
            raise
        except OSError as e:
            key = (source_drive, tmp_drive)
            if key not in _HARDLINK_FALLBACK_WARNED:
                _HARDLINK_FALLBACK_WARNED.add(key)
                log.warning(
                    "save-cache hardlink unavailable on %s -> %s (%s); "
                    "falling back to copy. Disk usage will be N× when "
                    "N campaigns cache the same autosave.",
                    source_drive or "(rootless)",
                    tmp_drive or "(rootless)",
                    e,
                )
    else:
        key = (source_drive, tmp_drive)
        if key not in _HARDLINK_FALLBACK_WARNED:
            _HARDLINK_FALLBACK_WARNED.add(key)
            log.warning(
                "save-cache spans volumes (%s -> %s); hardlink unavailable, "
                "falling back to copy. Disk usage will be N× when N "
                "campaigns cache the same autosave. Co-locate the CK3 save "
                "dir and the chronicler data dir on the same volume to fix.",
                source_drive or "(rootless)",
                tmp_drive or "(rootless)",
            )
    shutil.copy2(source, tmp)


class SaveCache:
    """Per-campaign save-file cache.

    Single-writer (the save-tail loop). Filenames carry a 12-digit
    monotonic sequence number so listing the directory in lex order
    yields chronological order. The cache survives process restarts —
    pending caches from a prior run are surfaced via :meth:`pending`
    so the bootstrap path can drain them before the watcher starts.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._next_seqno = self._compute_next_seqno()
        self._prune_orphan_tmps()
        # ck3_chronicler-7w5: lifetime counter of files gc_oldest has
        # dropped. Process-local; restart resets to 0 (which is fine
        # — the user's UI shows "drops since current process started"
        # and a restart implies a fresh slate).
        self._gc_drops_total = 0
        # ck3_chronicler-5x47: seqnos currently in-flight in the ingest
        # consumer (between pop and mark_processed). gc_oldest skips
        # these so the rakaly subprocess doesn't have its source file
        # deleted out from under it. Cleared on mark_processed.
        self._protected: set[int] = set()

    def _compute_next_seqno(self) -> int:
        max_seen = 0
        for entry in self.cache_dir.iterdir():
            m = _FILENAME_PATTERN.match(entry.name)
            if m is None:
                continue
            seqno = int(m.group(1))
            if seqno > max_seen:
                max_seen = seqno
        return max_seen + 1

    def _prune_orphan_tmps(self) -> None:
        """Remove ``.tmp`` files left by an interrupted previous run."""
        for entry in self.cache_dir.glob("*.tmp"):
            try:
                entry.unlink()
                log.info("pruned orphan cache tmpfile %s", entry.name)
            except OSError as e:
                log.warning("failed to prune %s: %s", entry, e)

    def cache_save(self, source: Path) -> CachedSave | None:
        """Materialize ``source`` into the cache atomically.

        Hardlinks the source autosave into the cache when same-volume
        (ck3_chronicler-dqtk — saves N× disk pressure when N campaigns
        all cache the same CK3 autosave). Falls back to ``shutil.copy2``
        on cross-volume layouts or any OSError from ``os.link``.

        Returns the :class:`CachedSave` on success, or ``None`` if the
        source disappeared mid-materialize (CK3 rotation race) or the
        copy otherwise failed. Failures are logged; callers proceed.
        """
        seqno = self._next_seqno
        final_name = f"{seqno:0{_SEQNO_WIDTH}d}.ck3"
        tmp_name = f"{seqno:0{_SEQNO_WIDTH}d}.tmp"
        final = self.cache_dir / final_name
        tmp = self.cache_dir / tmp_name
        try:
            _materialize_cache(source, tmp)
        except FileNotFoundError:
            log.warning(
                "save %s vanished before cache copy completed (CK3 rotation race?)",
                source.name,
            )
            return None
        except OSError as e:
            log.warning("failed to cache %s: %s", source.name, e)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            return None
        try:
            os.replace(tmp, final)
        except OSError as e:
            log.warning("failed to finalize cache %s: %s", final.name, e)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            return None
        # Only advance the seqno after a fully-successful write so a
        # failed copy doesn't burn a sequence number.
        self._next_seqno = seqno + 1
        try:
            size = final.stat().st_size
        except OSError:
            size = 0
        log.info("cached %s -> %s (%d bytes)", source.name, final.name, size)
        return CachedSave(path=final, seqno=seqno, size_bytes=size)

    def pending(self) -> list[CachedSave]:
        """List all pending cached saves, sorted by seqno (oldest first).

        ck3_chronicler-2faj: each entry is enriched with its
        ``playthrough_id`` from the sidecar .meta.json if one exists, so
        the drain can short-circuit foreign saves without re-parsing.
        Orphan sidecars (no matching .ck3) are skipped — only real saves
        appear in pending().

        Issue #2: delegates to :func:`pending_in` so this and the
        ingest-state REST route share one definition of "pending".
        """
        return pending_in(self.cache_dir)

    def tag_playthrough(self, cached: CachedSave, playthrough_id: str) -> None:
        """ck3_chronicler-2faj: persist ``playthrough_id`` for ``cached``
        in a sidecar .meta.json next to the .ck3. The drain reads it on
        subsequent pending() scans to skip the rakaly parse for foreign-
        playthrough saves.

        Failures are logged + swallowed — the optimization is a fast-path
        not a correctness guarantee. A failed write just means the next
        encounter takes the full-parse path.
        """
        sidecar = _sidecar_path_for(cached.path)
        try:
            tmp = sidecar.with_suffix(_SIDECAR_SUFFIX + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({"playthrough_id": playthrough_id}, f)
            os.replace(tmp, sidecar)
        except OSError as e:
            log.warning("failed to write cache sidecar %s: %s", sidecar.name, e)

    def mark_processed(self, cached: CachedSave) -> None:
        """Delete a cached save after successful ingestion. Idempotent.

        ck3_chronicler-2faj: also deletes the playthrough_id sidecar if
        one was written via :meth:`tag_playthrough` — the cache entry is
        fully gone after processing.
        """
        try:
            cached.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("failed to delete processed cache %s: %s", cached.path.name, e)
        sidecar = _sidecar_path_for(cached.path)
        with contextlib.suppress(FileNotFoundError):
            sidecar.unlink()
        # Clear any in-flight protection so the protect set doesn't grow
        # unbounded over the lifetime of a long ingest session.
        self._protected.discard(cached.seqno)

    def protect(self, seqno: int) -> None:
        """Mark ``seqno`` as in-flight — :meth:`gc_oldest` will skip it.

        Called by the ingest consumer immediately before handing the
        cached file to rakaly; the protection is cleared by
        :meth:`mark_processed` once ingest completes. Idempotent.
        """
        self._protected.add(seqno)

    def total_bytes(self) -> int:
        return sum(c.size_bytes for c in self.pending())

    def snapshot(self) -> dict[str, int]:
        """Return current observability state for the SSE event bus
        (ck3_chronicler-7w5). Three counters:

        - ``pending`` — number of cached saves still awaiting ingestion
          (the "lag" indicator on the v0.7 Save-tail page)
        - ``bytes`` — total disk usage of the cache (informs whether
          GC is about to fire under cache_max_bytes)
        - ``gc_drops_lifetime`` — running tally of files :meth:`gc_oldest`
          has dropped over the SaveCache instance's lifetime. Process-
          local; not persisted across restarts (a restart is its own
          observability event).
        """
        return {
            **snapshot_dir(self.cache_dir),
            "gc_drops_lifetime": self._gc_drops_total,
        }

    def gc_oldest(self, max_bytes: int = DEFAULT_MAX_CACHE_BYTES) -> int:
        """Drop oldest pending caches until total disk usage is within
        ``max_bytes``. Returns the number of files deleted.

        Seqnos in the in-flight :attr:`_protected` set are skipped — the
        consumer is mid-rakaly on those files and dropping them would
        crash the subprocess (ck3_chronicler-5x47). If the only saves
        keeping the cache over cap are protected, this method returns
        ``0`` without deleting anything; the next GC tick after
        :meth:`mark_processed` will pick them up.

        Use this as a safety valve only; the normal flow is
        :meth:`mark_processed` after each successful ingest. A sustained
        Speed-5 session that outpaces ingestion will trigger GC; the
        result is missed events for the dropped saves (logged at
        WARNING) but bounded disk usage.
        """
        pending = self.pending()
        total = sum(c.size_bytes for c in pending)
        deleted = 0
        for cached in pending:
            if total <= max_bytes:
                break
            if cached.seqno in self._protected:
                continue
            try:
                cached.path.unlink()
                # ck3_chronicler-2faj: drop the playthrough_id sidecar
                # alongside its .ck3 so sustained Speed-5 sessions don't
                # accumulate orphan sidecars across GC churn.
                with contextlib.suppress(FileNotFoundError):
                    _sidecar_path_for(cached.path).unlink()
                total -= cached.size_bytes
                deleted += 1
                log.warning(
                    "GC dropped cached save %s (%d bytes) — cache exceeded %d-byte cap",
                    cached.path.name,
                    cached.size_bytes,
                    max_bytes,
                )
            except OSError as e:
                log.warning("GC failed to delete %s: %s", cached.path, e)
        # ck3_chronicler-7w5: lifetime drop counter feeds the SSE
        # observability snapshot — a non-zero value on the v0.7
        # Save-tail page surfaces the lossy-ingest WARN banner.
        self._gc_drops_total += deleted
        return deleted

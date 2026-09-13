"""Unit tests for chronicler.api.dependencies.EngineCache.

ck3_chronicler-27ov.78 (audit L15): EngineCache.factory_for did an
unsynchronized check-then-set. Two threads racing the first access for the
same campaign both saw an empty cache, both opened an engine, and the loser's
engine was overwritten in the dict — never disposed. On Windows that leaked
SQLite file handle wedges the delete-retry loop in delete_campaign_endpoint.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from chronicler.api import dependencies as deps


def test_factory_for_opens_one_engine_under_concurrent_first_access(monkeypatch):
    """Concurrent first-access for one campaign must open exactly one engine.

    Otherwise the losing engine is orphaned (created, overwritten, never
    disposed) and its SQLite handle leaks for the process lifetime.
    """
    n_threads = 8
    create_count = 0
    count_lock = threading.Lock()
    start = threading.Barrier(n_threads)

    def slow_make_engine(_path):
        nonlocal create_count
        with count_lock:
            create_count += 1
        # Widen the check-then-set window so every racing thread is past
        # the cache miss before the first one records its engine.
        time.sleep(0.2)
        return object()  # stand-in Engine; identity is all we assert on

    monkeypatch.setattr(deps, "make_engine_for_path", slow_make_engine)
    monkeypatch.setattr(deps, "make_session_factory", lambda _engine: object())

    cache = deps.EngineCache()
    campaign = SimpleNamespace(id="camp-1", db_path="unused.db")

    results: dict[int, object] = {}

    def worker(i: int) -> None:
        start.wait()
        results[i] = cache.factory_for(campaign)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one engine opened — no orphaned handle.
    assert create_count == 1
    # Every caller got the same cached factory.
    assert len({id(f) for f in results.values()}) == 1

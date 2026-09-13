"""Tests for chronicler.settings_store — JSON-on-disk user overrides.

ck3_chronicler-f9w.1.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from chronicler.settings_store import load_settings, save_settings, update_settings


def _path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


def test_load_settings_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_settings(path=_path(tmp_path)) == {}


def test_save_then_load_roundtrips_payload(tmp_path: Path) -> None:
    payload = {"save_dir": "C:/games/saves", "ck3_install_dir": "D:/Steam/CK3"}
    target = _path(tmp_path)
    save_settings(payload, path=target)
    assert load_settings(path=target) == payload


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    """settings.json lives under ~/Documents/chronicler/ which may not
    exist on a fresh install — save must mkdir parents."""
    target = tmp_path / "nested" / "missing" / "settings.json"
    save_settings({"save_dir": "X"}, path=target)
    assert target.is_file()


def test_load_returns_empty_for_unparseable_json(tmp_path: Path) -> None:
    target = _path(tmp_path)
    target.write_text("{not valid json", encoding="utf-8")
    assert load_settings(path=target) == {}


def test_load_returns_empty_for_non_object_json(tmp_path: Path) -> None:
    """A JSON list at the top level is valid JSON but not the schema we
    expect — fall back to empty so the resolver chain stays sane."""
    target = _path(tmp_path)
    target.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_settings(path=target) == {}


def test_update_merges_new_keys_into_existing(tmp_path: Path) -> None:
    target = _path(tmp_path)
    save_settings({"save_dir": "old"}, path=target)
    update_settings({"ck3_install_dir": "new"}, path=target)
    assert load_settings(path=target) == {
        "save_dir": "old",
        "ck3_install_dir": "new",
    }


def test_update_overwrites_existing_keys(tmp_path: Path) -> None:
    target = _path(tmp_path)
    save_settings({"save_dir": "first"}, path=target)
    update_settings({"save_dir": "second"}, path=target)
    assert load_settings(path=target)["save_dir"] == "second"


def test_update_with_none_clears_key(tmp_path: Path) -> None:
    """Passing None for a key removes it so the resolver falls back to
    env/default rather than treating it as a literal None."""
    target = _path(tmp_path)
    save_settings({"save_dir": "X", "ck3_install_dir": "Y"}, path=target)
    update_settings({"save_dir": None}, path=target)
    assert load_settings(path=target) == {"ck3_install_dir": "Y"}


def test_update_preserves_unknown_keys(tmp_path: Path) -> None:
    """Forward-compat: a newer chronicler may have written keys we don't
    know about; updating one of our keys must not strip them."""
    target = _path(tmp_path)
    target.write_text(
        json.dumps({"save_dir": "X", "future_feature": {"flag": True}}),
        encoding="utf-8",
    )
    update_settings({"save_dir": "Y"}, path=target)
    after = load_settings(path=target)
    assert after["future_feature"] == {"flag": True}
    assert after["save_dir"] == "Y"


def test_concurrent_updates_do_not_drop_keys(tmp_path: Path) -> None:
    """ck3_chronicler-27ov.80 (audit L26): many threads each writing a
    distinct key must all survive. Without the lock the read-merge-write
    races and writers clobber each other's keys (lost update). A barrier
    maximises the overlap so an unsynchronised implementation fails
    reliably rather than flakily."""
    target = _path(tmp_path)
    save_settings({}, path=target)

    n = 16
    barrier = threading.Barrier(n)

    def writer(i: int) -> None:
        barrier.wait()  # release all threads at once for max contention
        update_settings({f"key_{i}": i}, path=target)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = load_settings(path=target)
    assert final == {f"key_{i}": i for i in range(n)}


def test_save_is_atomic_no_temp_file_left_behind(tmp_path: Path) -> None:
    """A successful write should leave only the target file, not the
    .tmp companion (the atomic-replace pattern unlinks it)."""
    target = _path(tmp_path)
    save_settings({"save_dir": "X"}, path=target)
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".settings.")]
    assert leftovers == []

"""Tests for compute_heraldry_status (a3jc / f9w.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chronicler.heraldry.extractor import compute_heraldry_status


def _seed_palette(out: Path, n: int = 3) -> None:
    palette = {f"color_{i}": [i, i, i] for i in range(n)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "palette.json").write_text(json.dumps(palette), encoding="utf-8")


def _seed_manifest(out: Path, *, extracted_at: str) -> None:
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ck3_dir": "x:/CK3",
                "extracted_at": extracted_at,
                "patterns": [],
                "colored_emblems": [],
                "palette_colors": [],
            }
        ),
        encoding="utf-8",
    )


def _seed_pattern_pngs(out: Path, n: int) -> None:
    patterns = out / "patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (patterns / f"pattern_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def _seed_emblem_pngs(out: Path, n: int) -> None:
    emblems = out / "colored_emblems"
    emblems.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (emblems / f"ce_{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_compute_heraldry_status_not_extracted_on_empty_dir(tmp_path: Path) -> None:
    status = compute_heraldry_status(tmp_path / "h", ck3_install_dir=None)
    assert status.extracted is False
    assert status.last_extraction_at is None
    assert status.palette_colors == 0
    assert status.patterns_count == 0
    assert status.emblems_count == 0
    assert status.is_stale is False


def test_compute_heraldry_status_reads_manifest_and_counts(tmp_path: Path) -> None:
    h = tmp_path / "h"
    _seed_palette(h, n=15)
    _seed_pattern_pngs(h, 41)
    _seed_emblem_pngs(h, 1585)
    _seed_manifest(h, extracted_at="2026-05-01T00:00:00+00:00")

    status = compute_heraldry_status(h, ck3_install_dir=None)
    assert status.extracted is True
    assert status.last_extraction_at == "2026-05-01T00:00:00+00:00"
    assert status.palette_colors == 15
    assert status.patterns_count == 41
    assert status.emblems_count == 1585
    # No install_dir → is_stale stays False
    assert status.is_stale is False


def test_compute_heraldry_status_flags_stale_when_source_newer(tmp_path: Path) -> None:
    h = tmp_path / "h"
    _seed_palette(h, n=2)
    _seed_pattern_pngs(h, 1)
    _seed_emblem_pngs(h, 1)

    # Recorded extraction was a year ago.
    old_ts = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    _seed_manifest(h, extracted_at=old_ts)

    # Build a fake CK3 install with the CoA dir present (mtime is now,
    # which is post-old_ts → stale).
    ck3 = tmp_path / "ck3"
    coa = ck3 / "game" / "gfx" / "coat_of_arms"
    coa.mkdir(parents=True)

    status = compute_heraldry_status(h, ck3_install_dir=ck3)
    assert status.is_stale is True


def test_compute_heraldry_status_not_stale_when_source_older(tmp_path: Path) -> None:
    h = tmp_path / "h"
    _seed_palette(h, n=2)
    _seed_pattern_pngs(h, 1)
    _seed_emblem_pngs(h, 1)

    # Recorded extraction is now; CoA dir below is created first so its
    # mtime predates the manifest stamp.
    ck3 = tmp_path / "ck3"
    coa = ck3 / "game" / "gfx" / "coat_of_arms"
    coa.mkdir(parents=True)

    fresh_ts = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    _seed_manifest(h, extracted_at=fresh_ts)

    status = compute_heraldry_status(h, ck3_install_dir=ck3)
    assert status.is_stale is False

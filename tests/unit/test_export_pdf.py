"""Tests for chronicler.export.pdf — PDF chronicle bundle (r8i)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import (
    insert_biography,
    insert_event_idempotent,
    upsert_character,
)
from chronicler.export.markdown import build_export_bundle
from chronicler.export.pdf import _zip_to_html, build_pdf_export


def _seed_basic_campaign(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "c.db"
    engine = make_engine_for_path(db)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        upsert_character(
            s,
            ck3_id=100,
            first_name="Alfred",
            dynasty_name="Wessex",
            birth_date="848.10.1",
            death_date="899.10.26",
        )
        eid = insert_event_idempotent(
            s,
            schema_version=1,
            event_type="death",
            event_date="899.10.26",
            event_date_iso="0899-10-26",
            wall_clock_at="2026-05-06T00:00:00+00:00",
            primary_character_id=100,
            payload_json="{}",
            raw_line="seed",
        )
        insert_biography(
            s,
            character_id=100,
            body="Alfred ruled long and well across Wessex.",
            prompt_template_version="biography_v1",
            provider="test",
            generated_at="2026-05-06T10:00:00+00:00",
            events_through_event_id=eid,
        )
        s.commit()
    engine.dispose()
    return db, tmp_path


def test_zip_to_html_weaves_overview_chronicle_and_character_pages(tmp_path: Path) -> None:
    db, data_dir = _seed_basic_campaign(tmp_path)
    factory = make_session_factory(make_engine_for_path(db))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        build_export_bundle(
            zf=zf,
            factory=factory,
            campaign_name="Wessex",
            campaign_slug="wessex",
            ck3_version="1.19.0",
            closing_chronicle_body="The kingdom endured by the king's resolve.",
            closing_chronicle_generated_at="2026-05-06T12:00:00+00:00",
            bookmark_date="848.10.1",
            current_in_game_date="899.12.31",
            tracked_character_ids=[100],
            tracked_metadata={100: {"role": "player", "added_at": "2026-04-15T08:00:00+00:00"}},
            heraldry_dir=data_dir / "missing-heraldry-dir",
            palette={},
        )

    html = _zip_to_html(buf, campaign_slug="wessex")
    assert "<html>" in html and "</html>" in html
    # Overview meta + tracked roster present.
    assert "Wessex" in html
    assert "Alfred" in html
    # Closing-chronicle prose verbatim.
    assert "The kingdom endured" in html
    # Per-character vita prose present.
    assert "Alfred ruled long" in html
    # Character page is preceded by a page-break section.
    assert "character-page" in html
    # No shield (heraldry dir was missing) — no data URI img.
    assert "data:image/svg+xml" not in html


def test_build_pdf_export_emits_pdf_bytes(tmp_path: Path) -> None:
    pytest.importorskip("xhtml2pdf")  # optional `pdf` extra (bpb8/knne)
    db, data_dir = _seed_basic_campaign(tmp_path)
    factory = make_session_factory(make_engine_for_path(db))
    pdf_bytes, result = build_pdf_export(
        factory=factory,
        campaign_name="Wessex",
        campaign_slug="wessex",
        ck3_version="1.19.0",
        closing_chronicle_body="The kingdom endured.",
        closing_chronicle_generated_at="2026-05-06T12:00:00+00:00",
        bookmark_date="848.10.1",
        current_in_game_date="899.12.31",
        tracked_character_ids=[100],
        tracked_metadata={100: {"role": "player", "added_at": "2026-04-15T08:00:00+00:00"}},
        heraldry_dir=data_dir / "no-heraldry",
        palette={},
    )
    assert pdf_bytes.startswith(b"%PDF-")
    # 1KB+ for a real chronicle. xhtml2pdf produces ~5-10 KB even for
    # the minimal seed above; if we drop below 1KB something has gone
    # wrong with the HTML pipeline.
    assert len(pdf_bytes) > 1000
    # The bundle result is the same shape as the markdown route.
    assert result.files_written >= 3

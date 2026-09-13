"""Tests for chronicler.export.markdown — bundle renderers."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import (
    insert_biography,
    insert_event_idempotent,
    upsert_character,
)
from chronicler.export.event_roll import EventRollRow
from chronicler.export.markdown import (
    BundleResult,
    build_export_bundle,
    render_character_md,
    render_chronicle_md,
    render_overview_md,
)

# ---------- pure renderers ----------


def test_render_chronicle_md_emits_body_verbatim() -> None:
    out = render_chronicle_md("The kingdom fell at Hastings.\n\nThe end.")
    assert "The kingdom fell at Hastings." in out
    assert "The end." in out


def test_render_chronicle_md_handles_empty() -> None:
    # Defensive: empty/None handled by the API gate (409); renderer
    # still copes if called with empty.
    assert render_chronicle_md("") == ""


def test_render_character_md_includes_vita_and_events() -> None:
    md = render_character_md(
        ck3_id=100,
        first_name="Alfred",
        nickname="the Great",
        dynasty_name="Wessex",
        house_name="House of Cerdic",
        birth_date="848.10.1",
        death_date="899.10.26",
        culture="anglo_saxon",
        faith="catholic",
        role="player",
        biography_body="Alfred ruled long.",
        events=[
            EventRollRow(date="866.5.5", description="Acquired kingdom title 'Kingdom of Wessex'"),
            EventRollRow(date="899.10.26", description="Died (cause: natural)"),
        ],
        had_events=True,
        coa_svg_relpath="../assets/heraldry/100.svg",
    )
    # Header includes name, house, dynasty.
    assert md.startswith("# Alfred · House of Cerdic · Wessex")
    # Image link uses the relative path passed in.
    assert "![Arms of Alfred](../assets/heraldry/100.svg)" in md
    # Vital lines.
    assert "**Born** 848.10.1" in md and "**Died** 899.10.26" in md
    assert "**Culture** anglo_saxon" in md and "**Role** player" in md
    # Sections present.
    assert "## Vita" in md and "Alfred ruled long." in md
    assert "## Memories" not in md
    # Event roll is a Date | Event table of pre-rendered rows.
    assert "## Event roll" in md
    assert "| Date | Event |" in md
    assert "| 866.5.5 | Acquired kingdom title 'Kingdom of Wessex' |" in md


def test_render_character_md_no_notable_events_note() -> None:
    md = render_character_md(
        ck3_id=300,
        first_name="Quiet",
        nickname=None,
        dynasty_name=None,
        house_name=None,
        birth_date=None,
        death_date=None,
        culture=None,
        faith=None,
        role=None,
        biography_body=None,
        events=[],
        had_events=True,
        coa_svg_relpath=None,
    )
    assert "## Event roll" in md
    assert "_No notable events recorded._" in md


def test_build_export_bundle_curates_and_renders_event_roll(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    engine = make_engine_for_path(db)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        upsert_character(s, ck3_id=100, first_name="Alfred", dynasty_name="Wessex")
        upsert_character(s, ck3_id=200, first_name="Aethelflaed")  # ally name source
        insert_event_idempotent(
            s,
            schema_version=1,
            event_type="travel",  # noise -> dropped
            event_date="866.1.1",
            event_date_iso="0866-01-01",
            wall_clock_at="2026-05-06T00:00:00+00:00",
            primary_character_id=100,
            payload_json='{"from_location": 1, "to_location": 2}',
            raw_line="seed",
        )
        insert_event_idempotent(
            s,
            schema_version=1,
            event_type="alliance_formed",  # kept -> rendered + name resolved
            event_date="867.2.2",
            event_date_iso="0867-02-02",
            wall_clock_at="2026-05-06T00:00:00+00:00",
            primary_character_id=100,
            payload_json='{"ally_character_id": 200}',
            raw_line="seed",
        )
        s.commit()
    engine.dispose()
    factory = make_session_factory(make_engine_for_path(db))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        build_export_bundle(
            zf=zf,
            factory=factory,
            campaign_name="Wessex",
            campaign_slug="wessex",
            ck3_version=None,
            closing_chronicle_body="x",
            closing_chronicle_generated_at="2026-05-06T12:00:00+00:00",
            bookmark_date=None,
            current_in_game_date=None,
            tracked_character_ids=[100],
            tracked_metadata={100: {"role": "player"}},
            heraldry_dir=tmp_path / "no-heraldry",
            palette={},
        )
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        page = zf.read("wessex-chronicle/characters/100-alfred.md").decode("utf-8")
    assert "Formed alliance with Aethelflaed" in page  # rendered + name resolved
    assert "travel" not in page  # noise curated out
    assert "| Date | Event |" in page


def test_render_character_md_omits_image_when_none() -> None:
    md = render_character_md(
        ck3_id=200,
        first_name="Pending",
        nickname=None,
        dynasty_name=None,
        house_name=None,
        birth_date="900.1.1",
        death_date=None,
        culture=None,
        faith=None,
        role=None,
        biography_body=None,
        events=[],
        coa_svg_relpath=None,
    )
    assert "![Arms of" not in md
    # Vita section is omitted when no body.
    assert "## Vita" not in md


def test_render_overview_md_lists_tracked_and_warnings() -> None:
    md = render_overview_md(
        campaign_name="Wessex",
        ck3_version="1.19.0",
        sealed_at="2026-05-06",
        bookmark_date="848.10.1",
        current_in_game_date="899.12.31",
        tracked=[
            {
                "character_id": 100,
                "first_name": "Alfred",
                "role": "player",
                "added_at": "2026-04-15",
            },
            {
                "character_id": 200,
                "first_name": None,
                "role": None,
                "added_at": "2026-04-16",
            },
        ],
        biography_count=1,
        event_count=12,
        warnings=["Character #200 — no biography"],
    )
    assert "# Wessex" in md
    assert "CK3 version 1.19.0" in md
    assert "**Tracked souls:** 2" in md
    assert "Alfred (#100) · Player · since 2026-04-15" in md
    assert "Character 200" in md  # falls back when first_name is None
    assert "## Warnings" in md
    assert "no biography" in md


def test_render_overview_md_no_warnings_section_when_clean() -> None:
    md = render_overview_md(
        campaign_name="Wessex",
        ck3_version=None,
        sealed_at="2026-05-06",
        bookmark_date=None,
        current_in_game_date=None,
        tracked=[],
        biography_count=0,
        event_count=0,
        warnings=[],
    )
    assert "## Warnings" not in md
    assert "No tracked souls" in md


# ---------- bundle assembly ----------


def _seed_basic_campaign(tmp_path: Path) -> tuple[Path, Path]:
    """Stand up a minimal per-campaign DB with one tracked char + bio."""
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
            body="Alfred ruled long.",
            prompt_template_version="biography_v1",
            provider="test",
            generated_at="2026-05-06T10:00:00+00:00",
            events_through_event_id=eid,
        )
        s.commit()
    engine.dispose()
    return db, tmp_path


def test_build_export_bundle_emits_expected_zip_layout(tmp_path: Path) -> None:
    db, data_dir = _seed_basic_campaign(tmp_path)
    factory = make_session_factory(make_engine_for_path(db))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        result: BundleResult = build_export_bundle(
            zf=zf,
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
            heraldry_dir=data_dir / "missing-heraldry-dir",  # forces warnings
            palette={},
        )
    assert result.files_written >= 3
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        names = set(zf.namelist())
        assert "wessex-chronicle/chronicle.md" in names
        assert "wessex-chronicle/overview.md" in names
        assert "wessex-chronicle/characters/100-alfred.md" in names
        # No SVG written (heraldry dir doesn't exist).
        assert not any(n.startswith("wessex-chronicle/assets/") for n in names)
        # Warning recorded.
        overview = zf.read("wessex-chronicle/overview.md").decode("utf-8")
        assert "## Warnings" in overview


def test_build_export_bundle_skips_untracked_characters(tmp_path: Path) -> None:
    """A character with a biography but not in the tracked roster does
    NOT get a per-character file."""
    db, data_dir = _seed_basic_campaign(tmp_path)
    factory = make_session_factory(make_engine_for_path(db))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        build_export_bundle(
            zf=zf,
            factory=factory,
            campaign_name="Wessex",
            campaign_slug="wessex",
            ck3_version=None,
            closing_chronicle_body="Body",
            closing_chronicle_generated_at="2026-05-06T12:00:00+00:00",
            bookmark_date=None,
            current_in_game_date=None,
            tracked_character_ids=[],  # nothing tracked
            tracked_metadata={},
            heraldry_dir=data_dir / "no-heraldry",
            palette={},
        )
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        char_files = [n for n in zf.namelist() if n.startswith("wessex-chronicle/characters/")]
        assert char_files == []

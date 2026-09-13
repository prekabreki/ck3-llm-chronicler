"""HTTP-level tests for the chronicler export / adopt / migrate / SPA API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-72a: schema migration tool ---


def test_migrate_status_returns_pending_for_unstamped_db(client: TestClient) -> None:
    """The test-campaign fixture builds its DB via SQLAlchemy without
    stamping alembic_version, so scan() flags it as needing migration
    (current_head=None)."""
    resp = client.get("/api/migrate/status")
    assert resp.status_code == 200
    body = resp.json()
    # The test-campaign should be pending; registry_needs_migration is False.
    assert any(
        p["name"] == "test-campaign" and p["current_head"] is None for p in body["needs_migration"]
    )
    assert body["registry_needs_migration"] is False


def test_migrate_status_excludes_archived_campaigns(api: CampaignHarness) -> None:
    """ck3_chronicler-4zdg: completed (archived) campaigns must not appear
    in the migration status the UI consumes — the Library banner and the
    Settings → Migration panel both read /api/migrate/status, so filtering
    here hides them from both. The migrator still sweeps archived DBs
    (scan() includes them); only this status route filters. Here an
    unstamped active campaign + an unstamped archived campaign both need
    migration, but only the active one is reported."""
    api.campaign("active")
    api.campaign("sealed", archived=True)

    c = api.client()
    resp = c.get("/api/migrate/status")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["needs_migration"]}
    assert "active" in names
    assert "sealed" not in names


def test_migrate_run_409_when_save_tail_running(client: TestClient) -> None:
    """If app.state.narrative_schedulers has any campaign + there's
    pending work, /run returns 409. The test-campaign fixture has
    pending work because its DB is unstamped (see
    test_migrate_status_returns_pending_for_unstamped_db).

    ck3_chronicler-m4cn: app.state.narrative_schedulers is the per-
    campaign dict; non-empty means save-tail is alive somewhere."""
    client.app.state.narrative_schedulers = {"sentinel-campaign": object()}
    try:
        resp = client.post("/api/migrate/run")
        assert resp.status_code == 409
        assert "save-tail" in resp.json()["detail"].lower()
    finally:
        client.app.state.narrative_schedulers = {}


def test_migrate_backups_list_empty_when_no_backups(client: TestClient) -> None:
    resp = client.get("/api/migrate/backups")
    assert resp.status_code == 200
    assert resp.json() == []


def test_migrate_halt_save_tail_sets_stop_event(client: TestClient) -> None:
    """The halt route sets app.state.save_tail_stop. Idempotent: returns
    halted=True even if no save-tail loop is currently waiting."""
    import asyncio

    client.app.state.save_tail_stop = asyncio.Event()
    resp = client.post("/api/migrate/halt-save-tail")
    assert resp.status_code == 200
    assert resp.json()["halted"] is True
    assert client.app.state.save_tail_stop.is_set()


def test_migrate_halt_save_tail_idempotent_without_orchestrator(client: TestClient) -> None:
    """When app.state.save_tail_stop is absent (no orchestrator running)
    the halt route still returns 200 + halted=True so the UI doesn't
    have to special-case the no-orchestrator path."""
    if hasattr(client.app.state, "save_tail_stop"):
        delattr(client.app.state, "save_tail_stop")
    resp = client.post("/api/migrate/halt-save-tail")
    assert resp.status_code == 200
    assert resp.json()["halted"] is True


# --- ck3_chronicler-441: POST /export/markdown ---


def _seal_test_campaign(client: TestClient, body: str = "The kingdom endured.") -> None:
    """Helper: stamp a closing chronicle on the test-campaign so the
    export endpoint sees a sealed campaign. set_campaign_closing_chronicle
    keys on campaign id (the UUID), not the name — look up the row first."""
    from chronicler.db.registry import (
        get_campaign_by_name,
        set_campaign_closing_chronicle,
    )

    registry_path = client.app.state.engine_cache.registry_path
    camp = get_campaign_by_name("test-campaign", registry=registry_path)
    assert camp is not None, "fixture campaign missing"
    set_campaign_closing_chronicle(
        camp.id,
        body,
        generated_at="2026-05-06T12:00:00+00:00",
        registry=registry_path,
    )


def test_export_markdown_409_when_unsealed(client: TestClient) -> None:
    """Campaign with no closing-chronicle row → 409, not 200."""
    resp = client.post("/api/campaigns/test-campaign/export/markdown")
    assert resp.status_code == 409
    assert "closing chronicle" in resp.json()["detail"].lower()


def test_export_markdown_404_unknown_campaign(client: TestClient) -> None:
    resp = client.post("/api/campaigns/nope/export/markdown")
    assert resp.status_code == 404


def test_export_markdown_returns_zip_with_expected_files(client: TestClient) -> None:
    """Sealed campaign → 200 + application/zip; bundle has the
    chronicle, overview, and one character file (the seeded Eadmund
    isn't tracked → no character file)."""
    import io
    import zipfile

    _seal_test_campaign(client)
    # Track Eadmund (the seeded character) so we get a per-character file.
    client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "role": "player"},
    )

    resp = client.post("/api/campaigns/test-campaign/export/markdown")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert "attachment" in resp.headers["content-disposition"]
    assert "test-campaign" in resp.headers["content-disposition"].lower()

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    # Expect a single top-level dir.
    roots = {n.split("/", 1)[0] for n in names}
    assert len(roots) == 1
    root = roots.pop()
    assert root.endswith("-chronicle")
    assert f"{root}/chronicle.md" in names
    assert f"{root}/overview.md" in names
    char_files = [n for n in names if n.startswith(f"{root}/characters/")]
    assert len(char_files) == 1
    chronicle = zf.read(f"{root}/chronicle.md").decode("utf-8")
    assert "kingdom endured" in chronicle


# --- ck3_chronicler-r8i: POST /export/pdf ---


def test_export_pdf_409_when_unsealed(client: TestClient) -> None:
    resp = client.post("/api/campaigns/test-campaign/export/pdf")
    assert resp.status_code == 409
    assert "closing chronicle" in resp.json()["detail"].lower()


def test_export_pdf_404_unknown_campaign(client: TestClient) -> None:
    resp = client.post("/api/campaigns/nope/export/pdf")
    assert resp.status_code == 404


def test_export_pdf_503_when_stack_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """knne: with the optional `pdf` extra not installed, the endpoint
    returns a clear 503 rather than crashing the app at import time.

    Issue #41: this used to skip whenever xhtml2pdf *was* importable, which
    meant it never ran on the owner's machine (the extra is installed
    there) and never ran in CI either once the extra was absent — it only
    ever fired on a machine that happened to lack it. The absence is now
    simulated instead: a None entry in sys.modules makes the lazy
    ``from xhtml2pdf import pisa`` in export/pdf.py raise ImportError, so
    the 503 branch is exercised everywhere regardless of what is
    installed.
    """
    monkeypatch.setitem(sys.modules, "xhtml2pdf", None)
    monkeypatch.setitem(sys.modules, "xhtml2pdf.pisa", None)
    _seal_test_campaign(client)
    client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "role": "player"},
    )
    resp = client.post("/api/campaigns/test-campaign/export/pdf")
    assert resp.status_code == 503
    assert "pdf export is not installed" in resp.json()["detail"].lower()


def test_export_pdf_returns_application_pdf(client: TestClient) -> None:
    pytest.importorskip("xhtml2pdf")  # optional `pdf` extra (bpb8/knne)
    _seal_test_campaign(client)
    client.post(
        "/api/campaigns/test-campaign/tracked",
        json={"character_id": 36892, "role": "player"},
    )

    resp = client.post("/api/campaigns/test-campaign/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")
    # Real chronicles produce 5-15KB; 1KB floor catches HTML pipeline regressions.
    assert len(resp.content) > 1000


# --- v0.7 Phase 1.7: SPA static mount ---


def _spa_index_or_skip() -> Path:
    """Resolve the bundled index.html path or skip the test if not built.

    Issue #41: this skip is deliberately **not** in conftest's declared
    table. CI's Python job builds the frontend before pytest, so if these
    four tests ever skip in an automated run it means the build stopped
    producing output — and the skip ledger flags it UNDECLARED, which under
    CHRONICLER_STRICT_SKIPS fails the run. Locally it stays a plain skip
    with the command that fixes it.

    These assert against real Vite output (doctype, the configured title,
    content-hashed asset names) rather than a synthesized fixture. A stub
    index.html would satisfy the assertions without proving anything about
    the bundle actually shipped, which is worse than the skip was.
    """
    spa_index = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "chronicler"
        / "api"
        / "static"
        / "app"
        / "index.html"
    )
    if not spa_index.exists():
        pytest.skip("frontend/ build output missing — run `cd frontend && npm run build` to enable")
    return spa_index


def test_spa_mount_serves_index_when_built(client: TestClient) -> None:
    """When frontend/ has been built (output at static/app/), GET /
    returns the React index.html. Verifies the StaticFiles(html=True)
    fallback fires for the directory request post-Phase 6 (when the SPA
    moved from /app to /)."""
    _spa_index_or_skip()
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "<!doctype html>" in body.lower()
    # The Vite-built index.html references the title we set.
    assert "Chronicler" in body


# --- ck3_chronicler-ex31: no-cache header on index.html ---


def test_spa_index_has_no_cache_headers(client: TestClient) -> None:
    """GET / serves index.html with Cache-Control: no-cache so a rebuilt
    bundle takes effect on a plain reload. Without this, the non-hashed
    index.html can be held in browser cache across rebuilds and keep
    loading stale (now-deleted) asset hashes."""
    _spa_index_or_skip()
    resp = client.get("/")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "no-cache" in cache_control
    assert "no-store" in cache_control
    assert "must-revalidate" in cache_control
    assert resp.headers.get("pragma") == "no-cache"
    assert resp.headers.get("expires") == "0"


def test_spa_index_explicit_path_has_no_cache_headers(client: TestClient) -> None:
    """An explicit request for /index.html (rare but legal) must also get
    the no-cache treatment — same payload, same staleness risk as /."""
    _spa_index_or_skip()
    resp = client.get("/index.html")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "no-cache" in cache_control
    assert "no-store" in cache_control


def test_spa_hashed_asset_keeps_default_caching(client: TestClient) -> None:
    """Hashed asset bundles under assets/ should NOT get no-cache headers —
    their filenames are content-hashed by Vite, so long-cache is correct
    and desirable. We just need the no-cache treatment scoped to .html."""
    spa_index = _spa_index_or_skip()
    assets_dir = spa_index.parent / "assets"
    # Issue #41: these were skips. Once index.html exists the build ran, and
    # a Vite build that emits no hashed assets/ is broken — skipping there
    # reported green on a bundle that could not have worked in a browser.
    assert assets_dir.is_dir(), (
        f"{spa_index} exists but {assets_dir} does not — the frontend build "
        "is incomplete; re-run `npm run build` in frontend/"
    )
    hashed = next(
        (
            entry
            for entry in assets_dir.iterdir()
            if entry.is_file()
            and entry.name.endswith((".js", ".css"))
            and "-" in entry.name  # Vite hash separator
        ),
        None,
    )
    assert hashed is not None, (
        f"no content-hashed .js/.css under {assets_dir} — Vite always emits "
        "them, so this bundle is not a real build"
    )
    resp = client.get(f"/assets/{hashed.name}")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    # The no-cache wrapper should NOT have stamped this — either no
    # Cache-Control at all (default StaticFiles behaviour) or whatever
    # Starlette provides, but specifically NOT no-store.
    assert "no-store" not in cache_control


# --- ck3_chronicler-da8: stale SPA bundle warning ---


def test_warn_if_spa_stale_fires_when_source_newer_than_bundle(tmp_path: Path, caplog) -> None:
    """When a frontend/src file is newer than the built index.html, a
    WARNING log naming the file is emitted at startup. Without this the
    failure mode (developer edits .tsx, runs `chronicler dev`, sees old
    bundle) was silent and cost ~30 minutes of triage during a smoke."""
    import logging
    import os

    from chronicler.api.routes import _warn_if_spa_stale

    spa_index = tmp_path / "index.html"
    spa_index.write_text("<!doctype html><html></html>")
    older = spa_index.stat().st_mtime - 10_000
    os.utime(spa_index, (older, older))

    frontend_src = tmp_path / "src"
    frontend_src.mkdir()
    newer_source = frontend_src / "App.tsx"
    newer_source.write_text("export default function App() {}")

    with caplog.at_level(logging.WARNING, logger="chronicler.api.routes"):
        _warn_if_spa_stale(spa_index, frontend_src=frontend_src)
    assert any(
        "stale SPA bundle" in rec.message and "App.tsx" in rec.message for rec in caplog.records
    ), f"expected stale-bundle warning naming App.tsx; got {[r.message for r in caplog.records]}"


def test_warn_if_spa_stale_silent_when_bundle_newer(tmp_path: Path, caplog) -> None:
    import logging
    import os

    from chronicler.api.routes import _warn_if_spa_stale

    spa_index = tmp_path / "index.html"
    spa_index.write_text("<!doctype html><html></html>")
    frontend_src = tmp_path / "src"
    frontend_src.mkdir()
    older_source = frontend_src / "App.tsx"
    older_source.write_text("old")
    older = spa_index.stat().st_mtime - 100
    os.utime(older_source, (older, older))

    with caplog.at_level(logging.WARNING, logger="chronicler.api.routes"):
        _warn_if_spa_stale(spa_index, frontend_src=frontend_src)
    assert not any("stale SPA bundle" in rec.message for rec in caplog.records)


def test_warn_if_spa_stale_no_op_when_frontend_dir_missing(tmp_path: Path, caplog) -> None:
    """Tauri-bundled installs ship without frontend/; the freshness check
    must silently no-op rather than crash."""
    import logging

    from chronicler.api.routes import _warn_if_spa_stale

    spa_index = tmp_path / "index.html"
    spa_index.write_text("<!doctype html><html></html>")

    with caplog.at_level(logging.WARNING, logger="chronicler.api.routes"):
        _warn_if_spa_stale(spa_index, frontend_src=tmp_path / "no-such-dir")
    assert not any("stale SPA bundle" in rec.message for rec in caplog.records)


# --- ck3_chronicler-v2a: POST /api/campaigns/adopt-from-save ---


_FAKE_ADOPT_SAVE = {
    "playthrough_id": "adopt-test-pt-9876",
    "version": "1.19.0",
    "bookmark_date": "1066.9.15",
    "currently_played_characters": [],
    "meta_data": {"version": "1.19.0", "meta_main_portrait": {"id": 100}},
    "currently_played_character_history": [],
    "date": "1066.9.15",
    "player": [],
    "living": {
        "100": {
            "first_name": "Edmund",
            "birth": "1020.1.1",
            "female": False,
            "dynasty_house": 5,
        },
    },
    "dead_unprunable": {},
    "dynasties": {
        "dynasty_house": {"5": {"name": "dynn_Wessex", "dynasty": 1}},
        "dynasties": {"1": {"name": "dynn_Wessex"}},
    },
}


def test_adopt_from_save_creates_new_campaign(client: TestClient, tmp_path: Path) -> None:
    """ck3_chronicler-v2a: pointing the endpoint at a fresh save creates
    a new campaign row in the registry, runs alembic + import_save, and
    returns the resolved CampaignResponse with import counts."""
    save_path = tmp_path / "fresh.ck3"
    save_path.write_bytes(b"fake")
    with (
        patch(
            "chronicler.save.adoption.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
        patch(
            "chronicler.save.importer.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
    ):
        resp = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["campaign"]["ck3_version"] == "1.19.0"
    # import_save populated character 100; chars_upserted should be ≥ 1.
    assert body["chars_upserted"] >= 1


def test_adopt_from_save_pins_current_player_on_registry(
    client: TestClient, tmp_path: Path
) -> None:
    """ck3_chronicler (2026-05-09): adoption must populate the registry's
    current_player_character_id immediately, not wait for the first
    save-tail tick. Otherwise Dynasty (404 via the za6f safety net),
    Lineage (forces the user to pick a seed), and Library card heraldry
    (auto-name byline) all stay in their pre-tail empty states.

    Regression test: before the fix, adoption called import_save without
    passing campaign_id, so the importer's update_campaign_overview at
    importer.py:332 was skipped.
    """
    save_path = tmp_path / "fresh.ck3"
    save_path.write_bytes(b"fake")
    with (
        patch(
            "chronicler.save.adoption.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
        patch(
            "chronicler.save.importer.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
    ):
        resp = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Adoption resolved Edmund (id 100) as the player; registry must
    # now reflect that without waiting for save-tail.
    assert body["campaign"]["current_player_character_id"] == 100
    assert body["campaign"]["current_player_name"] == "Edmund"


def test_adopt_from_save_idempotent_for_same_playthrough(
    client: TestClient, tmp_path: Path
) -> None:
    """Re-adopting the same save must produce the same campaign and
    duplicate-not-insert the memories. resolve_campaign_for_save matches
    on playthrough_id; insert_event_idempotent absorbs duplicates."""
    save_path = tmp_path / "rerun.ck3"
    save_path.write_bytes(b"fake")
    with (
        patch(
            "chronicler.save.adoption.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
        patch(
            "chronicler.save.importer.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
    ):
        first = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
        second = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    # Same campaign id both times.
    assert first.json()["campaign"]["id"] == second.json()["campaign"]["id"]


def test_adopt_from_save_returns_404_for_missing_path(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/api/campaigns/adopt-from-save",
        json={"save_path": str(tmp_path / "no-such.ck3")},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_adopt_from_save_returns_400_on_rakaly_failure(client: TestClient, tmp_path: Path) -> None:
    """rakaly chokes (corrupt save / mod schema drift) → 400 with the
    underlying message so the user can act on it."""
    from chronicler.save.rakaly import RakalyError

    save_path = tmp_path / "corrupt.ck3"
    save_path.write_bytes(b"corrupt")
    with patch(
        "chronicler.save.adoption.convert_save_to_json",
        side_effect=RakalyError("corrupt save header"),
    ):
        resp = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
    assert resp.status_code == 400
    assert "rakaly" in resp.json()["detail"].lower()


def test_adopt_from_save_returns_409_on_archived_match(
    client: TestClient,
    tmp_path: Path,
    registry_and_campaign: tuple[Path, str],
) -> None:
    """ck3_chronicler-obds: when the save's playthrough_id matches an
    archived campaign, adopt-from-save returns 409 with a structured
    detail block carrying the archived campaign's id and name. Lets the
    SPA render 'this is an archived campaign — un-archive and resume?'
    instead of silently forking onto a duplicate."""
    from chronicler.db.registry import archive_campaign, create_campaign

    registry, _name = registry_and_campaign

    # Pre-create + archive a campaign whose playthrough_id matches
    # _FAKE_ADOPT_SAVE's playthrough_id.
    sealed = create_campaign(
        "Sealed Edmund",
        db_path=str(tmp_path / "sealed.db"),
        ck3_playthrough_id=_FAKE_ADOPT_SAVE["playthrough_id"],
        registry=registry,
    )
    archive_campaign(sealed.id, registry=registry)

    save_path = tmp_path / "fresh.ck3"
    save_path.write_bytes(b"fake")
    with (
        patch(
            "chronicler.save.adoption.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
        patch(
            "chronicler.save.importer.convert_save_to_json",
            return_value=_FAKE_ADOPT_SAVE,
        ),
    ):
        resp = client.post(
            "/api/campaigns/adopt-from-save",
            json={"save_path": str(save_path)},
        )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "archived_campaign_conflict"
    assert detail["archived_campaign_id"] == sealed.id
    assert detail["archived_campaign_name"] == "Sealed Edmund"


# --- ck3_chronicler-mb6q: save-picker listing endpoint ---


def test_save_list_returns_recent_saves_mtime_desc(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Three .ck3 files at different mtimes; newest is listed first.

    Non-.ck3 files (a CK3 mod loose-file or a stray text file) are
    skipped. Subdirectories aren't walked.
    """
    saves = tmp_path / "saves"
    saves.mkdir()
    # Override save_dir via the settings file so resolve_save_dir picks
    # it up — same path as the FE's Settings UI does.
    client.put("/api/settings/paths", json={"save_dir": str(saves)})

    older = saves / "older.ck3"
    older.write_bytes(b"a" * 100)
    # Make older actually older — set st_mtime backwards by 1h.
    import os
    import time

    one_hour_ago = time.time() - 3600
    os.utime(older, (one_hour_ago, one_hour_ago))

    middle = saves / "middle.ck3"
    middle.write_bytes(b"b" * 200)
    half_hour_ago = time.time() - 1800
    os.utime(middle, (half_hour_ago, half_hour_ago))

    newest = saves / "newest.ck3"
    newest.write_bytes(b"c" * 300)
    # Decoy non-ck3 file + a subdir with a ck3 in it (must be ignored).
    (saves / "ignore.txt").write_text("nope")
    sub = saves / "subdir"
    sub.mkdir()
    (sub / "should-not-show.ck3").write_bytes(b"x")

    resp = client.get("/api/save/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_dir_exists"] is True
    assert body["save_dir_source"] == "override"
    names = [r["filename"] for r in body["saves"]]
    assert names == ["newest.ck3", "middle.ck3", "older.ck3"]
    # Per-row shape.
    first = body["saves"][0]
    assert first["abs_path"] == str(newest)
    assert first["size_bytes"] == 300
    assert first["mtime_iso"].endswith("+00:00")


def test_save_list_returns_empty_when_save_dir_empty(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Empty save_dir → 200 with saves=[]; FE renders an informative empty state."""
    saves = tmp_path / "saves"
    saves.mkdir()
    client.put("/api/settings/paths", json={"save_dir": str(saves)})

    resp = client.get("/api/save/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_dir_exists"] is True
    assert body["saves"] == []


def test_save_list_empty_when_save_dir_does_not_exist(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Override points at a path that doesn't exist (typo'd) → 200 with
    saves=[] and save_dir_exists=False so the FE can show "couldn't read
    save_dir, type a path below"."""
    bogus = tmp_path / "not-here"
    client.put("/api/settings/paths", json={"save_dir": str(bogus)})

    resp = client.get("/api/save/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_dir_exists"] is False
    assert body["saves"] == []


def test_save_list_caps_limit(client: TestClient, paths_isolation: Path, tmp_path: Path) -> None:
    """limit query param caps the response; limit=3 returns 3 newest."""
    saves = tmp_path / "saves"
    saves.mkdir()
    client.put("/api/settings/paths", json={"save_dir": str(saves)})
    import os
    import time

    now = time.time()
    for i in range(5):
        p = saves / f"save_{i}.ck3"
        p.write_bytes(b"x")
        # Make each progressively older so we can assert sort order.
        os.utime(p, (now - i * 60, now - i * 60))

    resp = client.get("/api/save/list?limit=3")
    assert resp.status_code == 200
    names = [r["filename"] for r in resp.json()["saves"]]
    assert names == ["save_0.ck3", "save_1.ck3", "save_2.ck3"]

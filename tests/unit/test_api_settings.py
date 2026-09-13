"""HTTP-level tests for the chronicler settings API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-z6jm slice 1: debug-log status + rotate ---


def test_debug_log_status_reports_missing_file_cleanly(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: a chronicler instance with no CK3 run yet still gets a 200
    on the status endpoint; exists=False, exceeded=False."""
    monkeypatch.setenv("CK3_DEBUG_LOG", str(tmp_path / "missing.log"))
    resp = client.get("/api/settings/debug-log-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False
    assert data["size_bytes"] == 0
    assert data["exceeded"] is False
    assert data["path"].endswith("missing.log")


def test_debug_log_status_reports_size_and_threshold(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: a real log under threshold is reported with size + the
    static threshold constant."""
    log = tmp_path / "debug.log"
    log.write_bytes(b"some debug content\n")
    monkeypatch.setenv("CK3_DEBUG_LOG", str(log))
    resp = client.get("/api/settings/debug-log-status")
    data = resp.json()
    assert data["exists"] is True
    assert data["size_bytes"] == 19
    assert data["exceeded"] is False
    # The threshold is the module-level 200 MB constant.
    assert data["threshold_bytes"] == 200 * 1024 * 1024


def test_debug_log_status_flags_exceeded(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: above threshold the UI binds to exceeded=True. Threshold
    is monkeypatched down so the test doesn't write 200 MB."""
    monkeypatch.setattr(
        "chronicler.tailer.log_rotate.DEBUG_LOG_ROTATE_THRESHOLD_BYTES",
        50,
    )
    log = tmp_path / "debug.log"
    log.write_bytes(b"x" * 200)
    monkeypatch.setenv("CK3_DEBUG_LOG", str(log))
    resp = client.get("/api/settings/debug-log-status")
    data = resp.json()
    assert data["size_bytes"] == 200
    assert data["threshold_bytes"] == 50
    assert data["exceeded"] is True


def test_debug_log_rotate_archives_and_truncates(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: clicking rotate archives the log to gzip + truncates the
    original. Endpoint returns the archive path + the count of
    campaign offsets reset."""
    log = tmp_path / "debug.log"
    log.write_bytes(b"a" * 1000)
    monkeypatch.setenv("CK3_DEBUG_LOG", str(log))
    resp = client.post("/api/settings/debug-log-rotate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rotated"] is True
    assert data["archive_path"] is not None
    assert data["archive_path"].endswith(".log.gz")
    assert Path(data["archive_path"]).exists()
    # Original truncated to 0 bytes.
    assert log.stat().st_size == 0


def test_debug_log_rotate_no_op_on_missing_log(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """z6jm: pre-CK3-run rotate request is a no-op, NOT a 500."""
    monkeypatch.setenv("CK3_DEBUG_LOG", str(tmp_path / "absent.log"))
    resp = client.post("/api/settings/debug-log-rotate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rotated"] is False
    assert data["archive_path"] is None
    assert "no log to rotate" in data["message"]


# --- ck3_chronicler-f9w.1: paths panel ---


# --- ck3_chronicler-kze6 (f9w.3): first-run wizard detection ---


def test_first_run_status_no_wizard_when_library_not_empty(
    client: TestClient, paths_isolation: Path
) -> None:
    """Once any campaign exists, the wizard does NOT auto-open — onboarding
    is considered complete regardless of the other suppression flags. (The
    base ``client`` fixture seeds a 'test-campaign', so library_empty is
    False here; this is the NOT-clean-install case.)"""
    resp = client.get("/api/settings/first-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["library_empty"] is False
    assert body["needs_wizard"] is False


def test_first_run_status_needs_wizard_on_clean_install(
    api: CampaignHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truly clean install — empty registry, no settings overrides, no
    heraldry, never dismissed — is the one state that makes needs_wizard
    True (audit L38: this trigger was previously never exercised)."""
    # No api.campaign() call → the registry stays empty (clean install).
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        api.data_dir / "chronicler_settings.json",
    )
    monkeypatch.delenv("CHRONICLER_SAVE_DIR", raising=False)
    monkeypatch.delenv("CHRONICLER_CK3_INSTALL_DIR", raising=False)
    monkeypatch.setattr(
        "chronicler.heraldry.extractor.find_ck3_install",
        lambda override=None: None,
    )
    c = api.client()
    resp = c.get("/api/settings/first-run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["library_empty"] is True
    assert body["needs_wizard"] is True


def test_first_run_status_dismiss_persists_timestamp(
    client: TestClient, paths_isolation: Path
) -> None:
    resp = client.post("/api/settings/first-run/dismiss")
    assert resp.status_code == 200
    ts = resp.json()["wizard_dismissed_at"]
    assert isinstance(ts, str) and len(ts) >= 19

    # Subsequent GET reflects the dismissal.
    follow = client.get("/api/settings/first-run")
    assert follow.json()["wizard_dismissed_at"] == ts
    assert follow.json()["needs_wizard"] is False


# --- ck3_chronicler-a3jc (f9w.2): heraldry Settings card ---


def test_heraldry_status_empty_when_nothing_extracted(
    client: TestClient, paths_isolation: Path
) -> None:
    """Fresh data dir, no heraldry/ tree → extracted=False, all counts 0."""
    resp = client.get("/api/settings/heraldry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["extracted"] is False
    assert body["palette_colors"] == 0
    assert body["patterns_count"] == 0
    assert body["emblems_count"] == 0
    assert body["last_extraction_at"] is None
    assert body["is_stale"] is False


def test_heraldry_status_reports_install_dir_unresolved_when_probe_misses(
    client: TestClient, paths_isolation: Path
) -> None:
    resp = client.get("/api/settings/heraldry")
    body = resp.json()
    assert body["ck3_install_dir_exists"] is False


def test_heraldry_extract_400_when_install_dir_missing(
    client: TestClient, paths_isolation: Path
) -> None:
    """No CK3 install → extract endpoint refuses with 400."""
    resp = client.post("/api/settings/heraldry/extract", json={"force": False})
    assert resp.status_code == 400
    assert "ck3 install dir" in resp.json()["detail"].lower()


def test_heraldry_extract_returns_202_with_sse_url(
    client: TestClient,
    paths_isolation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured install + a stubbed extract_assets → 202 + sse_url."""
    install = tmp_path / "ck3"
    install.mkdir()
    client.put("/api/settings/paths", json={"ck3_install_dir": str(install)})
    # Stub out the heavy extract_assets so the test doesn't need DDS files.
    from chronicler.heraldry.extractor import ExtractionSummary

    def _fake_extract(*args, **kwargs):
        return ExtractionSummary(
            output_dir=kwargs.get("output_dir") or args[1],
            palette_colors=15,
            patterns_extracted=41,
            emblems_extracted=1585,
            skipped_designer=0,
            reused_existing=0,
        )

    monkeypatch.setattr("chronicler.api.routes.heraldry.extract_assets", _fake_extract)

    resp = client.post("/api/settings/heraldry/extract", json={"force": False})
    assert resp.status_code == 202
    body = resp.json()
    assert "extract_id" in body
    assert body["sse_url"] == f"/api/sse/heraldry-extract/{body['extract_id']}"


def test_paths_settings_returns_default_save_dir(client: TestClient, paths_isolation: Path) -> None:
    """No settings, no env → save_dir resolves to the CK3 default and
    install_dir reports unresolved (probe miss in this fixture)."""
    resp = client.get("/api/settings/paths")
    assert resp.status_code == 200
    data = resp.json()
    assert data["save_dir"]["source"] == "default"
    assert data["save_dir"]["override"] is None
    # exists may be true or false depending on dev box; just check shape.
    assert isinstance(data["save_dir"]["exists"], bool)
    assert "Crusader Kings III" in data["save_dir"]["resolved"]
    assert data["ck3_install_dir"]["source"] == "probe"
    assert data["ck3_install_dir"]["resolved"] == ""
    assert data["ck3_install_dir"]["exists"] is False
    assert data["ck3_install_dir"]["override"] is None


def test_paths_settings_put_persists_override(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Write an override; GET reflects it; the on-disk JSON has it too."""
    target = tmp_path / "my_saves"
    target.mkdir()
    resp = client.put("/api/settings/paths", json={"save_dir": str(target)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_dir"]["source"] == "override"
    assert body["save_dir"]["resolved"] == str(target)
    assert body["save_dir"]["exists"] is True
    assert body["save_dir"]["override"] == str(target)
    # On-disk
    assert json.loads(paths_isolation.read_text())["save_dir"] == str(target)


def test_paths_settings_put_null_clears_override(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """null in the body removes the override so the resolver falls back."""
    target = tmp_path / "my_saves"
    target.mkdir()
    client.put("/api/settings/paths", json={"save_dir": str(target)})

    resp = client.put("/api/settings/paths", json={"save_dir": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_dir"]["source"] == "default"
    assert body["save_dir"]["override"] is None
    assert "save_dir" not in json.loads(paths_isolation.read_text())


def test_paths_settings_put_empty_string_clears_override(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Frontend may send an empty input as ``""`` rather than null;
    treat both the same so the UI doesn't have to distinguish."""
    target = tmp_path / "my_saves"
    target.mkdir()
    client.put("/api/settings/paths", json={"save_dir": str(target)})

    resp = client.put("/api/settings/paths", json={"save_dir": ""})
    assert resp.status_code == 200
    assert resp.json()["save_dir"]["override"] is None


def test_paths_settings_put_partial_update_leaves_other_field_alone(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """A PUT with only save_dir must not clobber a previously-set
    ck3_install_dir override."""
    install = tmp_path / "ck3"
    install.mkdir()
    saves = tmp_path / "saves"
    saves.mkdir()
    client.put(
        "/api/settings/paths",
        json={"save_dir": str(saves), "ck3_install_dir": str(install)},
    )
    saves2 = tmp_path / "saves2"
    saves2.mkdir()
    resp = client.put("/api/settings/paths", json={"save_dir": str(saves2)})
    body = resp.json()
    assert body["save_dir"]["override"] == str(saves2)
    assert body["ck3_install_dir"]["override"] == str(install)


def test_paths_settings_override_with_nonexistent_path_marks_exists_false(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """A typo'd override should still persist (so the user can fix it
    rather than losing their input) but report exists=False so the UI
    can flag it."""
    bogus = tmp_path / "definitely_not_here"
    resp = client.put("/api/settings/paths", json={"save_dir": str(bogus)})
    body = resp.json()
    assert body["save_dir"]["override"] == str(bogus)
    assert body["save_dir"]["exists"] is False


# --- issue #51: the archive dir joins the paths panel ---


def test_paths_settings_reports_archive_dir_with_provenance(
    client: TestClient, paths_isolation: Path
) -> None:
    """With no override the archive dir resolves to the data-dir default
    (#24), and reports no git root — the default location is not in a
    checkout, which is the whole point of moving it out of the tree."""
    resp = client.get("/api/settings/paths")
    assert resp.status_code == 200
    data = resp.json()
    assert data["archive_dir"]["source"] == "default"
    assert data["archive_dir"]["override"] is None
    assert data["archive_dir"]["resolved"].endswith("archived")
    assert data["archive_git_root"] is None


def test_paths_settings_put_persists_archive_dir_override(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """The archive override round-trips like every other path row."""
    target = tmp_path / "sealed"
    target.mkdir()
    resp = client.put("/api/settings/paths", json={"archive_dir": str(target)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["archive_dir"]["source"] == "override"
    assert body["archive_dir"]["resolved"] == str(target)
    assert body["archive_dir"]["exists"] is True
    assert json.loads(paths_isolation.read_text())["archive_dir"] == str(target)


def test_paths_settings_reports_archive_git_root_when_inside_a_repo(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """Pointing the archive dir inside a checkout is what turns on
    commit-and-push, so the panel must be able to say so. Detection is a
    filesystem walk (``resolve_archive_git_root``), hence a bare .git dir
    is enough — no git binary is involved."""
    repo = tmp_path / "my_backups"
    (repo / ".git").mkdir(parents=True)
    target = repo / "sealed"
    target.mkdir()

    body = client.put("/api/settings/paths", json={"archive_dir": str(target)}).json()
    assert body["archive_git_root"] == str(repo.resolve())


def test_paths_settings_archive_put_leaves_the_other_overrides_alone(
    client: TestClient, paths_isolation: Path, tmp_path: Path
) -> None:
    """The partial-update contract has to hold in both directions, or
    saving one row silently blanks another."""
    saves = tmp_path / "saves"
    saves.mkdir()
    archive = tmp_path / "sealed"
    archive.mkdir()
    client.put("/api/settings/paths", json={"save_dir": str(saves)})

    body = client.put("/api/settings/paths", json={"archive_dir": str(archive)}).json()
    assert body["save_dir"]["override"] == str(saves)
    assert body["archive_dir"]["override"] == str(archive)

    body = client.put("/api/settings/paths", json={"save_dir": str(saves)}).json()
    assert body["archive_dir"]["override"] == str(archive)


# --- ck3_chronicler-tbrm.4: prose repo settings ---


@pytest.fixture
def prose_repo_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings_store at a temp file and clear the prose-repo env
    var so the test box's real settings.json doesn't leak in."""
    target = tmp_path / "chronicler_settings.json"
    monkeypatch.setattr("chronicler.settings_store.DEFAULT_SETTINGS_PATH", target)
    monkeypatch.delenv("CHRONICLER_PROSE_REPO_PATH", raising=False)
    return target


def test_prose_repo_status_default_when_no_override(
    client: TestClient,
    prose_repo_isolation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No settings, no env → falls through to the terminal default,
    source=default, override=null. exists/git/CLAUDE.md may be true on
    the dev box but that's box-dependent — assert only on shape.

    Issue #20: the default is now the ``chronicler init-prose`` target
    under the platform data dir, not the hard-coded
    ``ck3_chronicler_prose`` path, and the sibling autodetect this test
    used to have to defeat (via a ``__file__`` monkeypatch) is gone — so
    the resolver is deterministic without the fixture gymnastics."""
    resp = client.get("/api/settings/prose-repo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "default"
    assert body["override"] is None
    assert isinstance(body["exists"], bool)
    assert isinstance(body["git_initialized"], bool)
    assert isinstance(body["claude_md_present"], bool)
    from chronicler.config import default_prose_repo_path

    assert body["path"] == str(default_prose_repo_path())
    assert body["path"].endswith("prose")


def test_prose_repo_status_all_three_pips_green_for_real_repo(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """A directory with .git/ + CLAUDE.md surfaces all three pips green."""
    repo = tmp_path / "fake_prose_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# Chronicler role\n", encoding="utf-8")
    client.put("/api/settings/prose-repo", json={"path": str(repo)})

    resp = client.get("/api/settings/prose-repo")
    body = resp.json()
    assert body["source"] == "override"
    assert body["override"] == str(repo)
    assert body["exists"] is True
    assert body["git_initialized"] is True
    assert body["claude_md_present"] is True


def test_prose_repo_status_missing_claude_md_flagged(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """A repo with .git but no CLAUDE.md shows the right pip red — the
    role-reshape file is the load-bearing piece for biography quality."""
    repo = tmp_path / "no_claude_md"
    repo.mkdir()
    (repo / ".git").mkdir()
    client.put("/api/settings/prose-repo", json={"path": str(repo)})

    body = client.get("/api/settings/prose-repo").json()
    assert body["exists"] is True
    assert body["git_initialized"] is True
    assert body["claude_md_present"] is False


def test_prose_repo_put_persists_override(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """PUT writes the override to the on-disk settings.json and the GET
    response reflects it."""
    repo = tmp_path / "prose"
    repo.mkdir()
    resp = client.put("/api/settings/prose-repo", json={"path": str(repo)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "override"
    assert body["override"] == str(repo)
    on_disk = json.loads(prose_repo_isolation.read_text())
    assert on_disk["prose_repo_path"] == str(repo)


def test_prose_repo_put_null_clears_override(
    client: TestClient,
    prose_repo_isolation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """null in the body removes the override so resolution falls back to
    env / default — same ergonomics as the paths PUT.

    jnze: bypass sibling-detect so the fall-through reaches the
    hardcoded default deterministically."""
    import chronicler.config as config_module

    monkeypatch.setattr(config_module, "__file__", str(tmp_path / "elsewhere" / "config.py"))
    repo = tmp_path / "prose"
    repo.mkdir()
    client.put("/api/settings/prose-repo", json={"path": str(repo)})

    resp = client.put("/api/settings/prose-repo", json={"path": None})
    body = resp.json()
    assert body["source"] == "default"
    assert body["override"] is None
    assert "prose_repo_path" not in json.loads(prose_repo_isolation.read_text())


def test_prose_repo_put_empty_string_clears_override(
    client: TestClient,
    prose_repo_isolation: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string from the FE clears the override the same way null
    does — the FE shouldn't have to distinguish.

    jnze: bypass sibling-detect so the fall-through reaches the
    hardcoded default deterministically."""
    import chronicler.config as config_module

    monkeypatch.setattr(config_module, "__file__", str(tmp_path / "elsewhere" / "config.py"))
    repo = tmp_path / "prose"
    repo.mkdir()
    client.put("/api/settings/prose-repo", json={"path": str(repo)})

    resp = client.put("/api/settings/prose-repo", json={"path": ""})
    body = resp.json()
    assert body["source"] == "default"
    assert body["override"] is None


def test_prose_repo_put_nonexistent_path_persists_with_exists_false(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """Typo'd override persists so the user can fix it; exists=False so
    the UI flags it. Same pattern as the paths card."""
    bogus = tmp_path / "definitely_not_a_prose_repo"
    resp = client.put("/api/settings/prose-repo", json={"path": str(bogus)})
    body = resp.json()
    assert body["override"] == str(bogus)
    assert body["exists"] is False
    assert body["git_initialized"] is False
    assert body["claude_md_present"] is False


# --- issue #45: narrative-backend GET/PUT + writable models ---


def test_narrative_backend_get_reports_default_and_valid_set(client: TestClient) -> None:
    resp = client.get("/api/settings/narrative-backend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "claude-code"
    assert data["source"] == "default"
    assert data["valid_backends"] == ["anthropic", "claude-code", "openai-compatible"]
    assert "ollama" in data["valid_presets"]
    assert data["usable"] is True
    assert data["error"] is None
    assert data["openai_key"] == {"present": False, "source": None}


def test_narrative_backend_put_persists_and_reports_settings_source(
    client: TestClient,
) -> None:
    resp = client.put(
        "/api/settings/narrative-backend",
        json={"backend": "openai-compatible", "openai_preset": "ollama", "openai_model": "llama3"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "openai-compatible"
    assert data["source"] == "settings"
    assert data["openai_preset"] == "ollama"
    assert data["openai_model"] == "llama3"
    assert data["usable"] is True

    # Survives the request: a re-GET reads the persisted file.
    assert client.get("/api/settings/narrative-backend").json()["backend"] == "openai-compatible"


def test_narrative_backend_put_reports_unusable_config_without_refusing_the_write(
    client: TestClient,
) -> None:
    """Selecting openai-compatible before naming a model is a legitimate
    intermediate UI state — persist it, but say it will not run."""
    resp = client.put("/api/settings/narrative-backend", json={"backend": "openai-compatible"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["usable"] is False
    assert "no endpoint is configured" in data["error"]


def test_narrative_backend_put_rejects_unknown_backend(client: TestClient) -> None:
    resp = client.put("/api/settings/narrative-backend", json={"backend": "gpt5"})
    assert resp.status_code == 400
    assert "valid values" in resp.json()["detail"]
    # And nothing was persisted.
    assert client.get("/api/settings/narrative-backend").json()["backend"] == "claude-code"


def test_narrative_backend_put_rejects_unknown_preset(client: TestClient) -> None:
    resp = client.put("/api/settings/narrative-backend", json={"openai_preset": "llamafile"})
    assert resp.status_code == 400
    assert "valid presets" in resp.json()["detail"]


def test_narrative_backend_never_returns_a_stored_key(client: TestClient) -> None:
    """The danger zone: grep the whole response body for the key literal,
    not just the field names."""
    secret = "sk-do-not-leak-me-0123456789"
    resp = client.put("/api/settings/narrative-backend", json={"anthropic_api_key": secret})
    assert resp.status_code == 200
    assert secret not in resp.text
    assert resp.json()["anthropic_key"] == {"present": True, "source": "settings"}

    body = client.get("/api/settings/narrative-backend").text
    assert secret not in body


def test_narrative_backend_partial_put_does_not_blank_a_stored_key(client: TestClient) -> None:
    """A card that saves the base URL must not wipe the key stored earlier."""
    secret = "sk-keep-me-9876543210"
    client.put("/api/settings/narrative-backend", json={"openai_api_key": secret})
    assert client.get("/api/settings/narrative-backend").json()["openai_key"]["present"] is True

    resp = client.put(
        "/api/settings/narrative-backend",
        json={"openai_base_url": "http://127.0.0.1:8080/v1"},
    )
    assert resp.status_code == 200
    assert resp.json()["openai_key"]["present"] is True
    assert resp.json()["openai_base_url"] == "http://127.0.0.1:8080/v1"


def test_narrative_backend_empty_string_clears_a_setting(client: TestClient) -> None:
    """Omit = leave alone, empty string = clear. Both directions tested,
    because conflating them is how a key gets silently deleted."""
    client.put("/api/settings/narrative-backend", json={"openai_api_key": "sk-transient"})
    resp = client.put("/api/settings/narrative-backend", json={"openai_api_key": ""})
    assert resp.status_code == 200
    assert resp.json()["openai_key"]["present"] is False


def test_put_models_persists_and_returns_resolved_snapshot(client: TestClient) -> None:
    resp = client.put("/api/settings/models", json={"biography": "claude-haiku-4-5"})
    assert resp.status_code == 200
    assert resp.json()["biography"] == "claude-haiku-4-5"
    assert client.get("/api/settings/models").json()["biography"] == "claude-haiku-4-5"


def test_put_models_global_override_shadows_per_kind_in_the_response(client: TestClient) -> None:
    """The response comes from the real resolver, so the UI sees what will
    actually run rather than what was just typed."""
    client.put("/api/settings/models", json={"biography": "claude-haiku-4-5"})
    resp = client.put("/api/settings/models", json={"global_override": "claude-opus-5"})
    data = resp.json()
    assert data["global_override"] == "claude-opus-5"
    assert data["biography"] == "claude-opus-5"


def test_put_models_partial_leaves_other_kinds_untouched(client: TestClient) -> None:
    client.put("/api/settings/models", json={"biography": "claude-haiku-4-5"})
    client.put("/api/settings/models", json={"closing": "claude-sonnet-5"})
    data = client.get("/api/settings/models").json()
    assert data["biography"] == "claude-haiku-4-5"
    assert data["closing"] == "claude-sonnet-5"


# --- issue #23: preset table for the picker + the prose-repo init button ---


def test_narrative_backend_serves_the_preset_table_for_the_picker(client: TestClient) -> None:
    """The picker prefills a base URL from the chosen preset, so the ids
    alone are not enough — each preset ships its endpoint and whether it
    needs a key."""
    data = client.get("/api/settings/narrative-backend").json()
    by_id = {p["id"]: p for p in data["presets"]}
    assert sorted(by_id) == data["valid_presets"]
    assert by_id["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    assert by_id["deepseek"]["requires_key"] is True
    # The two local presets need no key — the picker greys that field.
    assert by_id["ollama"]["requires_key"] is False
    assert by_id["lmstudio"]["base_url"] == "http://localhost:1234/v1"


def test_prose_repo_init_scaffolds_the_resolved_path(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """The Initialize button with no path scaffolds wherever the card says
    the prose repo is — the override the user just saved."""
    target = tmp_path / "fresh_chronicle"
    client.put("/api/settings/prose-repo", json={"path": str(target)})

    resp = client.post("/api/settings/prose-repo/init", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["already_initialised"] is False
    assert body["status"]["exists"] is True
    assert body["status"]["claude_md_present"] is True
    assert body["notes"], "the CLI's human-facing notes must reach the UI"
    assert (target / "CLAUDE.md").is_file()
    assert (target / "voice" / "biography.md").is_file()


def test_prose_repo_init_accepts_an_explicit_path_and_records_it(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """An explicit path is scaffolded AND persisted, so the card's pips
    repaint against the new location without a second PUT."""
    target = tmp_path / "named_chronicle"
    resp = client.post("/api/settings/prose-repo/init", json={"path": str(target)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["override"] == str(target)
    assert body["status"]["path"] == str(target)
    assert json.loads(prose_repo_isolation.read_text())["prose_repo_path"] == str(target)


def test_prose_repo_init_is_idempotent_on_an_existing_scaffold(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """A second click must not clobber the craft rules the user edited."""
    target = tmp_path / "twice"
    client.post("/api/settings/prose-repo/init", json={"path": str(target)})
    (target / "CLAUDE.md").write_text("# my own rules\n", encoding="utf-8")

    resp = client.post("/api/settings/prose-repo/init", json={"path": str(target)})
    assert resp.status_code == 200
    assert resp.json()["already_initialised"] is True
    assert resp.json()["created"] is False
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "# my own rules\n"


def test_prose_repo_init_400s_on_a_non_empty_foreign_directory(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """Pointing Initialize at a documents folder must refuse with a
    readable reason, not scatter template files through it."""
    target = tmp_path / "my_documents"
    target.mkdir()
    (target / "taxes.pdf").write_bytes(b"not a chronicle")

    resp = client.post("/api/settings/prose-repo/init", json={"path": str(target)})
    assert resp.status_code == 400
    assert "not empty" in resp.json()["detail"]
    assert not (target / "CLAUDE.md").exists()


def test_first_run_status_reports_prose_repo_readiness(
    client: TestClient, prose_repo_isolation: Path, tmp_path: Path
) -> None:
    """The wizard's chronicle step labels itself done off this signal.
    A directory without CLAUDE.md is NOT ready — that is the case where
    claude --print would run as a generic assistant."""
    bare = tmp_path / "bare"
    bare.mkdir()
    client.put("/api/settings/prose-repo", json={"path": str(bare)})
    assert client.get("/api/settings/first-run").json()["prose_repo_ready"] is False

    client.post("/api/settings/prose-repo/init", json={})
    assert client.get("/api/settings/first-run").json()["prose_repo_ready"] is True


# --- issue #3: the heraldry asset mount must not be startup-only ---


def test_heraldry_assets_serve_after_extraction_without_a_restart(
    api: CampaignHarness,
) -> None:
    """The reported harm: on a fresh install the mount was conditional on
    ``heraldry/`` existing at app-construction time, so extracting from the
    running app left every texture 404ing until a restart while
    ``/api/settings/heraldry`` cheerfully reported extracted=true.

    Writes the files the extractor would write rather than running it (the
    extraction needs a CK3 install); the bug was never in the extraction,
    it was in the route table being fixed at boot.
    """
    heraldry_dir = api.data_dir / "heraldry"
    assert not heraldry_dir.exists(), "the fresh-install ordering is the point"

    client = api.client()
    # Nothing extracted yet: a miss, but a 404 from a live mount.
    assert client.get("/api/heraldry/assets/palette.json").status_code == 404

    # ... now extraction lands, mid-process.
    (heraldry_dir / "colored_emblems").mkdir(parents=True, exist_ok=True)
    (heraldry_dir / "palette.json").write_text('{"red": [168, 30, 30]}', encoding="utf-8")
    (heraldry_dir / "colored_emblems" / "ce_lion.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = client.get("/api/heraldry/assets/palette.json")
    assert resp.status_code == 200, "assets must serve without a restart"
    assert resp.json() == {"red": [168, 30, 30]}
    assert client.get("/api/heraldry/assets/colored_emblems/ce_lion.png").status_code == 200


def test_heraldry_status_extracted_implies_the_assets_are_reachable(
    api: CampaignHarness,
) -> None:
    """The honesty criterion: extracted=true while assets 404 was the
    actual reported fault. Tie the two together in one assertion so a
    future change cannot re-open the gap in a different ordering."""
    heraldry_dir = api.data_dir / "heraldry"
    client = api.client()

    heraldry_dir.mkdir(parents=True, exist_ok=True)
    (heraldry_dir / "palette.json").write_text('{"or": [212, 175, 55]}', encoding="utf-8")
    (heraldry_dir / "patterns").mkdir(exist_ok=True)
    (heraldry_dir / "patterns" / "pattern_solid.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    status = client.get("/api/settings/heraldry").json()
    if status["extracted"]:
        assert client.get("/api/heraldry/assets/palette.json").status_code == 200


def test_heraldry_asset_mount_rejects_path_traversal(api: CampaignHarness) -> None:
    """The mount stays StaticFiles precisely so the filename coming off the
    URL cannot escape the heraldry dir."""
    heraldry_dir = api.data_dir / "heraldry"
    heraldry_dir.mkdir(parents=True, exist_ok=True)
    secret = api.data_dir / "registry.db"
    secret.write_bytes(b"not yours")

    client = api.client()
    for attempt in (
        "/api/heraldry/assets/../registry.db",
        "/api/heraldry/assets/..%2Fregistry.db",
        "/api/heraldry/assets/%2e%2e/registry.db",
    ):
        resp = client.get(attempt)
        assert resp.status_code in (307, 404), attempt
        assert b"not yours" not in resp.content, attempt


def test_app_boots_when_the_heraldry_dir_cannot_be_created(
    api: CampaignHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only data dir must yield a working app that 404s these paths,
    not a boot failure — the mkdir is a convenience, not a precondition."""
    real_mkdir = Path.mkdir

    def _refuse(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "heraldry":
            raise PermissionError("read-only data dir")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _refuse)
    client = api.client()
    assert client.get("/api/campaigns").status_code == 200
    assert client.get("/api/heraldry/assets/palette.json").status_code == 404

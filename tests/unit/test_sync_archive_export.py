"""Tests for chronicler.sync.archive_export (ck3_chronicler-txuo).

Covers the seal-time export path (snapshot + sidecar + git
add/commit/push) and the read-side bootstrap that surfaces in-repo
archived snapshots through the registry. Each test sets up a tmp git
repo so the git steps run for real against a local-only branch."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from chronicler.db.registry import (
    Campaign,
    archive_campaign,
    create_campaign,
    list_campaigns,
)
from chronicler.db.registry._core import _connect
from chronicler.sync.archive_export import (
    archive_sync_enabled,
    archived_campaigns_dir,
    export_sealed_campaign,
    resolve_archive_git_root,
)
from chronicler.sync.bootstrap import (
    backfill_archived_campaigns,
    bootstrap_archived_snapshots,
)


def _archive_in_repo(repo_root: Path) -> Path:
    """Issue #24: an archive dir that happens to live inside a git repo —
    the sync-via-git vehicle. Callers pass the archive dir now, not the
    repo; chronicler discovers the repo by walking up from it."""
    return repo_root / "archived"


def _init_git_repo(repo_root: Path) -> None:
    """Bare-bones git repo with an identity, no remote. The export's
    push step fails harmlessly (no remote configured) which is exactly
    the scenario tests exercise — commit lands locally, push doesn't."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo_root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
    )
    # Empty initial commit so subsequent commits have a parent.
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )


def _seed_campaign(
    tmp_path: Path,
    *,
    name: str = "Wessex",
    sealed: bool = True,
) -> tuple[Campaign, Path]:
    """Create a registry + per-campaign DB pair under tmp_path. Returns
    the freshly-fetched Campaign row plus the registry path so callers
    can pass it into the sync helpers."""
    os.environ["CHRONICLER_DATA_DIR"] = str(tmp_path / "data_dir")
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(
        name,
        ck3_version="1.19.0",
        founding_dynasty_name="Cerdic",
        ck3_playthrough_id="test-pt",
        registry=registry_path,
    )
    # Touch the per-campaign DB so the snapshot has something real to copy.
    # NB: sqlite3's context manager commits but does NOT close the
    # connection, which leaves the file locked on Windows (so a later
    # Path(db_path).unlink() raises WinError 32). Close explicitly.
    conn = sqlite3.connect(campaign.db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS marker (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO marker (note) VALUES ('seeded')")
        conn.commit()
    finally:
        conn.close()
    if sealed:
        archive_campaign(campaign.id, registry=registry_path)
    # Re-fetch to get the latest archived flag.
    from chronicler.db.registry import get_campaign_by_id

    fresh = get_campaign_by_id(campaign.id, registry=registry_path)
    assert fresh is not None
    return fresh, registry_path


def _seed_full_campaign(tmp_path: Path) -> tuple[Campaign, Path]:
    """Build a more realistic campaign DB with characters/biographies/
    events tables — used to verify the slim-export pruning behaviour.

    Schema is the production shape (mirrors db.models.Base) so the
    DELETE / UPDATE statements run against real tables. We don't need
    every column, just the ones _slim_for_archive references."""
    os.environ["CHRONICLER_DATA_DIR"] = str(tmp_path / "data_dir")
    registry_path = tmp_path / "data_dir" / "registry.db"
    (tmp_path / "data_dir" / "campaigns").mkdir(parents=True, exist_ok=True)
    campaign = create_campaign(
        "Full",
        ck3_version="1.19.0",
        founding_dynasty_name="Cerdic",
        ck3_playthrough_id="test-pt",
        registry=registry_path,
    )
    with sqlite3.connect(campaign.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE characters (
                ck3_id INTEGER PRIMARY KEY,
                first_name TEXT,
                save_snapshot_json TEXT
            );
            CREATE TABLE biographies (
                id INTEGER PRIMARY KEY,
                character_id INTEGER,
                body TEXT
            );
            CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT);
            CREATE TABLE event_participants (event_id INTEGER, character_id INTEGER);
            """
        )
        # Three "protagonists" with biographies + 1-hop family ids in
        # their save_snapshot_json. The non-protagonist (5) has just an
        # identity row + heavy snapshot blob — which slim should NULL.
        big_blob = '"x":"' + ("padding" * 200) + '"'
        family_for_1 = json.dumps({"family_data": {"father": 2, "mother": 3, "child": [4]}})
        conn.executemany(
            "INSERT INTO characters (ck3_id, first_name, save_snapshot_json) VALUES (?, ?, ?)",
            [
                (1, "Alfred", family_for_1),
                (2, "Æthelwulf", "{" + big_blob + "}"),
                (3, "Osburh", "{" + big_blob + "}"),
                (4, "Edward", "{" + big_blob + "}"),
                (5, "Stranger", "{" + big_blob + "}"),
            ],
        )
        conn.execute(
            "INSERT INTO biographies (character_id, body) VALUES (?, ?)",
            (1, "Alfred lived a long life..."),
        )
        conn.executemany(
            "INSERT INTO events (kind) VALUES (?)",
            [("birth",), ("death",), ("title_acquired",)],
        )
        conn.executemany(
            "INSERT INTO event_participants (event_id, character_id) VALUES (?, ?)",
            [(1, 1), (2, 1)],
        )
        conn.commit()
    archive_campaign(campaign.id, registry=registry_path)
    from chronicler.db.registry import get_campaign_by_id

    fresh = get_campaign_by_id(campaign.id, registry=registry_path)
    assert fresh is not None
    return fresh, registry_path


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch) -> None:
    """Each test gets its own CHRONICLER_DATA_DIR. Restored automatically."""
    monkeypatch.setenv("CHRONICLER_DATA_DIR", str(tmp_path / "data_dir"))


def test_export_sealed_campaign_writes_db_and_sidecar(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    campaign, _registry = _seed_campaign(tmp_path)
    result = export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    assert result.snapshot_written is True
    assert result.sidecar_written is True
    assert result.committed is True
    # No remote configured -> push fails harmlessly. The committed
    # snapshot is what other machines pick up via git pull.
    assert result.pushed is False

    snapshot = _archive_in_repo(repo_root) / f"{campaign.id}.db"
    sidecar = _archive_in_repo(repo_root) / f"{campaign.id}.json"
    assert snapshot.exists()
    assert sidecar.exists()

    # The snapshot is a real SQLite file with the source's data.
    with sqlite3.connect(str(snapshot)) as conn:
        rows = conn.execute("SELECT note FROM marker").fetchall()
        assert rows == [("seeded",)]

    # Sidecar is parseable JSON with the identity columns.
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["id"] == campaign.id
    assert payload["name"] == "Wessex"
    assert payload["founding_dynasty_name"] == "Cerdic"


def test_export_sealed_campaign_outside_git_writes_the_snapshot_anyway(
    tmp_path: Path,
) -> None:
    """Issue #24: an archive dir that is not in a git repo is the NORMAL
    case now (the default lives under the data dir). The snapshot pair
    must still be written — that is what the user's own sync vehicle
    carries — and reported as success with no commit."""
    archive_dir = tmp_path / "plain-archive"
    campaign, _ = _seed_campaign(tmp_path)

    result = export_sealed_campaign(campaign, archive_dir=archive_dir)

    assert result.snapshot_written is True
    assert result.sidecar_written is True
    assert result.committed is False
    assert result.pushed is False
    assert (archive_dir / f"{campaign.id}.db").exists()
    assert (archive_dir / f"{campaign.id}.json").exists()


def test_export_sealed_campaign_is_a_no_op_when_sync_is_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    """The test-suite guard still stops the default path from writing into
    a real archive dir. Only the no-argument form is gated: an explicit
    archive_dir is a deliberate injection."""
    monkeypatch.setenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", "1")
    campaign, _ = _seed_campaign(tmp_path)

    assert archive_sync_enabled() is False
    result = export_sealed_campaign(campaign)
    assert result.snapshot_written is False
    assert "disabled" in result.message


def test_export_sealed_campaign_commits_via_git(tmp_path: Path) -> None:
    """The commit lands in the local repo's history with our message."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    campaign, _ = _seed_campaign(tmp_path, name="Erik 1066-9-15")
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    log = (
        subprocess.run(
            ["git", "-C", str(repo_root), "log", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    assert "archive: seal Erik 1066-9-15" in log


def test_export_sealed_campaign_repeated_call_does_not_crash(
    tmp_path: Path,
) -> None:
    """A second export against the same source must not crash. SQLite
    snapshots aren't always byte-identical between two backup() calls
    against the same source (header sequence numbers vary), so whether
    the second call produces a new commit or hits the "nothing to
    commit" branch is implementation-detail. We assert the resilient
    surface: snapshot + sidecar overwrite cleanly and ExportResult
    reports a sensible state either way."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    campaign, _ = _seed_campaign(tmp_path)
    first = export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))
    assert first.snapshot_written is True
    assert first.committed is True

    second = export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))
    assert second.snapshot_written is True
    assert second.sidecar_written is True


def test_bootstrap_archived_snapshots_inserts_sidecar_into_registry(
    tmp_path: Path,
) -> None:
    """A fresh secondary machine: we drop a sidecar+db pair into the
    archive dir and bootstrap pulls it into the local
    registry."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    # Seed an "originating" registry + campaign, export, then wipe the
    # local registry to simulate the secondary-machine state.
    campaign, primary_registry = _seed_campaign(tmp_path, name="Munster")
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    secondary_registry = tmp_path / "secondary_registry.db"
    inserted = bootstrap_archived_snapshots(
        registry=secondary_registry,
        archive_dir=_archive_in_repo(repo_root),
    )
    assert inserted == 1

    # The bootstrapped row appears in list_campaigns(include_archived=True).
    campaigns = list_campaigns(include_archived=True, registry=secondary_registry)
    assert len(campaigns) == 1
    boot = campaigns[0]
    assert boot.id == campaign.id
    assert boot.name == "Munster"
    assert boot.archived is True
    # db_path points at the in-repo snapshot, not the (non-existent)
    # secondary's local data dir.
    expected_db = _archive_in_repo(repo_root) / f"{campaign.id}.db"
    assert Path(boot.db_path) == expected_db


def test_bootstrap_archived_snapshots_idempotent_against_existing_id(
    tmp_path: Path,
) -> None:
    """If the campaign id is already in the local registry (e.g. the
    primary machine that just sealed), bootstrap leaves it alone."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    campaign, registry = _seed_campaign(tmp_path)
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    # The primary machine's registry already has this id; second
    # bootstrap call should be a no-op.
    inserted = bootstrap_archived_snapshots(
        registry=registry,
        archive_dir=_archive_in_repo(repo_root),
    )
    assert inserted == 0


def test_bootstrap_archived_snapshots_absent_dir_returns_zero(tmp_path: Path, monkeypatch) -> None:
    """A missing archive dir bootstraps 0 silently (no crash, no spurious
    INSERTs), and the disable guard short-circuits the default path."""
    secondary_registry = tmp_path / "secondary_registry.db"
    assert (
        bootstrap_archived_snapshots(
            registry=secondary_registry,
            archive_dir=tmp_path / "no-archive-here",
        )
        == 0
    )
    monkeypatch.setenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", "1")
    assert bootstrap_archived_snapshots(registry=secondary_registry) == 0


def test_bootstrap_archived_snapshots_skips_malformed_sidecar(
    tmp_path: Path,
) -> None:
    """A corrupt sidecar JSON is skipped with a log warning, doesn't
    crash bootstrap, and doesn't poison subsequent valid sidecars."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    out_dir = _archive_in_repo(repo_root)
    out_dir.mkdir(parents=True)
    # Garbage sidecar with no companion .db.
    (out_dir / "broken.json").write_text("{not valid json", encoding="utf-8")

    # Plus one valid pair.
    campaign, _ = _seed_campaign(tmp_path)
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    secondary_registry = tmp_path / "secondary_registry.db"
    inserted = bootstrap_archived_snapshots(
        registry=secondary_registry,
        archive_dir=_archive_in_repo(repo_root),
    )
    assert inserted == 1


def test_bootstrap_archived_snapshots_skips_sidecar_without_db(
    tmp_path: Path,
) -> None:
    """A sidecar JSON whose companion .db is missing (mid-pull state,
    partial git fetch, etc.) is skipped — we'd rather show nothing
    than an unopenable Library card."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    out_dir = _archive_in_repo(repo_root)
    out_dir.mkdir(parents=True)
    (out_dir / "stranded.json").write_text(
        json.dumps(
            {
                "id": "stranded-id",
                "name": "Stranded",
                "ck3_version": "1.19.0",
                "created_at": "2026-04-15T08:00:00+00:00",
                "founding_dynasty_name": "Test",
            }
        ),
        encoding="utf-8",
    )

    secondary_registry = tmp_path / "secondary_registry.db"
    inserted = bootstrap_archived_snapshots(
        registry=secondary_registry,
        archive_dir=_archive_in_repo(repo_root),
    )
    assert inserted == 0


def test_resolve_archive_git_root_ignores_the_cwd(tmp_path: Path, monkeypatch) -> None:
    """Issue #24 / ck3_chronicler-27ov.5 (audit H11): the CWD must never
    decide which repo chronicler commits into. The old resolver anchored on
    __file__ for that reason; this one anchors on the archive dir, so a
    decoy checkout the process happens to be sitting in is invisible."""
    monkeypatch.delenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", raising=False)
    decoy = tmp_path / "unrelated_repo"
    decoy.mkdir()
    _init_git_repo(decoy)
    monkeypatch.chdir(decoy)

    plain = tmp_path / "plain" / "archived"
    plain.mkdir(parents=True)
    assert resolve_archive_git_root(plain) is None


def test_resolve_archive_git_root_finds_the_enclosing_repo(tmp_path: Path) -> None:
    """An archive dir inside a repo of the user's own resolves to that repo
    (git-as-vehicle), at any nesting depth."""
    repo_root = tmp_path / "my-chronicle-sync"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    assert resolve_archive_git_root(repo_root) == repo_root
    nested = repo_root / "campaigns" / "archived"
    nested.mkdir(parents=True)
    assert resolve_archive_git_root(nested) == repo_root


def test_archived_campaigns_dir_resolves_settings_then_env_then_default(
    tmp_path: Path, monkeypatch
) -> None:
    """Issue #24: same precedence as every other structural path."""
    monkeypatch.setattr(
        "chronicler.settings_store.DEFAULT_SETTINGS_PATH",
        tmp_path / "chronicler_settings.json",
    )
    monkeypatch.delenv("CHRONICLER_ARCHIVE_DIR", raising=False)
    from chronicler.config import chronicler_data_dir
    from chronicler.settings_store import update_settings

    assert archived_campaigns_dir() == chronicler_data_dir() / "archived"

    monkeypatch.setenv("CHRONICLER_ARCHIVE_DIR", str(tmp_path / "from-env"))
    assert archived_campaigns_dir() == tmp_path / "from-env"

    update_settings({"archive_dir": str(tmp_path / "from-settings")})
    assert archived_campaigns_dir() == tmp_path / "from-settings"


def test_export_sealed_campaign_slims_db(tmp_path: Path) -> None:
    """The export step prunes events + non-protagonist save_snapshot_json
    so the committed snapshot stays under GitHub's 100 MB hard limit
    on real campaigns. Verified inline by checking the DB contents."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    campaign, _ = _seed_full_campaign(tmp_path)
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    snapshot = _archive_in_repo(repo_root) / f"{campaign.id}.db"
    with sqlite3.connect(str(snapshot)) as conn:
        # Events tables: rows wiped.
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM event_participants").fetchone()[0] == 0
        # Protagonist (1) + 1-hop family (2, 3, 4) keep their
        # save_snapshot_json. The unrelated stranger (5) has it NULL'd.
        kept = {
            r[0]: r[1] for r in conn.execute("SELECT ck3_id, save_snapshot_json FROM characters")
        }
        assert kept[1] is not None
        assert kept[2] is not None
        assert kept[3] is not None
        assert kept[4] is not None
        assert kept[5] is None
        # Identity columns survive on every row regardless — Codex still
        # lists everyone.
        names = dict(conn.execute("SELECT ck3_id, first_name FROM characters"))
        assert names == {
            1: "Alfred",
            2: "Æthelwulf",
            3: "Osburh",
            4: "Edward",
            5: "Stranger",
        }
        # Biographies untouched — they're the read-only surface's
        # primary content.
        assert conn.execute("SELECT count(*) FROM biographies").fetchone()[0] == 1


def test_list_campaigns_is_pure_read_until_explicit_bootstrap(tmp_path: Path, monkeypatch) -> None:
    """ck3_chronicler-27ov.15 (audit H12): list_campaigns is a pure
    SELECT — it must NOT lazily bootstrap (and especially not prune /
    delete files). The secondary machine picks up in-repo archived
    campaigns via the explicit bootstrap call (API lifespan startup, or
    `campaign list --all`), after which the pure list sees the row."""
    monkeypatch.delenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", raising=False)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    # Primary side: seed + export.
    campaign, _ = _seed_campaign(tmp_path, name="ListTest")
    export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    # Secondary side: brand-new registry, repo designated explicitly
    # (27ov.5: the resolver no longer trusts the CWD).
    secondary_registry = tmp_path / "secondary_registry.db"
    monkeypatch.setenv("CHRONICLER_ARCHIVE_DIR", str(_archive_in_repo(repo_root)))

    # Pure read: the archived campaign is invisible pre-bootstrap.
    rows = list_campaigns(include_archived=True, registry=secondary_registry)
    assert not any(r.id == campaign.id for r in rows)

    # Explicit bootstrap (what API startup / the CLI now run) inserts it.
    bootstrap_archived_snapshots(registry=secondary_registry)
    rows = list_campaigns(include_archived=True, registry=secondary_registry)
    assert any(r.id == campaign.id and r.archived for r in rows)


def test_run_git_suppresses_console_window(monkeypatch, tmp_path: Path) -> None:
    """ck3_chronicler-w26q: _run_git is the prose-repo/archive git path that
    fires many times in the closing/archive flow. It must pass the shared
    Windows console-suppression flags so it doesn't flash a terminal per call.
    """
    import sys

    from chronicler.sync import git as sync_git
    from chronicler.util import win_subprocess

    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sync_git.subprocess, "run", fake_run)

    sync_git._run_git(tmp_path, "status")

    kwargs = captured["kwargs"]
    assert "creationflags" in kwargs
    assert "startupinfo" in kwargs
    assert kwargs["creationflags"] == win_subprocess.creationflags()
    if sys.platform == "win32":
        assert kwargs["creationflags"] != 0
        assert kwargs["startupinfo"] is not None
    else:
        assert kwargs["creationflags"] == 0
        assert kwargs["startupinfo"] is None


def test_export_missing_source_does_not_create_empty_db(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, _registry = _seed_campaign(tmp_path, name="Gone")
    # Simulate the source DB having been deleted (the half-synced state).
    Path(campaign.db_path).unlink()

    result = export_sealed_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    assert result.snapshot_written is False
    # Must NOT have materialised an empty snapshot at the target.
    target = _archive_in_repo(repo_root) / f"{campaign.id}.db"
    assert not target.exists()


def test_backfill_does_not_reexport_bootstrapped_row(tmp_path: Path) -> None:
    """A row whose db_path points INTO the archive dir is a bootstrapped
    projection of a snapshot; if the snapshot is gone it must NOT be
    re-exported (the munso/Saar resurrection path)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="Bootstrapped")

    # Rewrite the row's db_path to point at an in-repo snapshot that does
    # NOT exist (snapshot deleted on another machine, pulled here).
    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    phantom = archive_dir / f"{campaign.id}.db"
    with _connect(registry_path) as conn:
        conn.execute(
            "UPDATE campaigns SET db_path = ? WHERE id = ?",
            (str(phantom), campaign.id),
        )

    from chronicler.sync.bootstrap import backfill_archived_campaigns

    exported = backfill_archived_campaigns(
        registry=registry_path, archive_dir=_archive_in_repo(repo_root)
    )

    assert exported == 0
    assert not phantom.exists()  # no empty snapshot forged


def test_backfill_skips_tombstoned_campaign(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="Tombstoned")

    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{campaign.id}.deleted").write_text("{}", encoding="utf-8")

    from chronicler.sync.bootstrap import backfill_archived_campaigns

    exported = backfill_archived_campaigns(
        registry=registry_path, archive_dir=_archive_in_repo(repo_root)
    )

    assert exported == 0
    assert not (archive_dir / f"{campaign.id}.db").exists()


def test_tombstone_campaign_writes_and_commits(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, _registry = _seed_campaign(tmp_path, name="Doomed")

    # Pretend a snapshot pair was previously committed, so the tombstone
    # commit also stages their removal.
    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{campaign.id}.db").write_bytes(b"snap")
    (archive_dir / f"{campaign.id}.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "seed snap"],
        check=True,
        capture_output=True,
    )
    # Remove the snapshot pair on disk (the endpoint unlinks them first).
    (archive_dir / f"{campaign.id}.db").unlink()
    (archive_dir / f"{campaign.id}.json").unlink()

    from chronicler.sync.archive_export import tombstone_campaign

    result = tombstone_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    tomb = archive_dir / f"{campaign.id}.deleted"
    assert tomb.exists()
    payload = json.loads(tomb.read_text(encoding="utf-8"))
    assert payload["id"] == campaign.id
    assert payload["name"] == campaign.name
    assert isinstance(payload["deleted_at"], str) and payload["deleted_at"]
    assert result.committed is True
    # The commit message records the deletion.
    log_out = subprocess.run(
        ["git", "-C", str(repo_root), "log", "-1", "--pretty=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log_out == f"archive: delete {campaign.name}"

    # The tombstone commit must also record the snapshot-pair REMOVAL —
    # that is the whole reason tombstone_campaign uses `git add -A`.
    name_status = subprocess.run(
        ["git", "-C", str(repo_root), "show", "--name-status", "--pretty=format:", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"D\tarchived/{campaign.id}.db" in name_status
    assert f"D\tarchived/{campaign.id}.json" in name_status
    assert f"A\tarchived/{campaign.id}.deleted" in name_status


def test_tombstone_campaign_commits_without_a_committed_snapshot_pair(
    tmp_path: Path,
) -> None:
    """A campaign that was never sealed (no committed
    <archive dir>/<id>.db+.json) must STILL commit its tombstone, not
    leave it untracked. ck3_chronicler-0ewl: naming the absent .db/.json
    pathspecs made `git add` exit 128 ('pathspec did not match any
    files'), so the tombstone was written but never committed and the
    working tree was left dirty with stray .deleted litter."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    # sealed=False + no snapshot pair anywhere in the archive dir.
    campaign, _registry = _seed_campaign(tmp_path, name="NeverSealed", sealed=False)

    from chronicler.sync.archive_export import tombstone_campaign

    result = tombstone_campaign(campaign, archive_dir=_archive_in_repo(repo_root))

    tomb = _archive_in_repo(repo_root) / f"{campaign.id}.deleted"
    assert tomb.exists()
    assert result.committed is True

    # The tombstone is committed, not left as untracked litter — the repo
    # working tree is clean afterward.
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status.strip() == "", f"working tree not clean: {status!r}"

    log_out = subprocess.run(
        ["git", "-C", str(repo_root), "log", "-1", "--pretty=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert log_out == f"archive: delete {campaign.name}"
    name_status = subprocess.run(
        ["git", "-C", str(repo_root), "show", "--name-status", "--pretty=format:", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"A\tarchived/{campaign.id}.deleted" in name_status


def test_bootstrap_prunes_tombstoned_row_and_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="ToPrune")
    live_db = Path(campaign.db_path)
    assert live_db.exists()

    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{campaign.id}.deleted").write_text(
        f'{{"id": "{campaign.id}"}}', encoding="utf-8"
    )

    inserted = bootstrap_archived_snapshots(
        registry=registry_path, archive_dir=_archive_in_repo(repo_root)
    )

    from chronicler.db.registry import get_campaign_by_id

    assert get_campaign_by_id(campaign.id, registry=registry_path) is None
    assert not live_db.exists()  # local DB removed
    # Idempotent: a second pass is a no-op and still leaves the row gone.
    bootstrap_archived_snapshots(registry=registry_path, archive_dir=_archive_in_repo(repo_root))
    assert get_campaign_by_id(campaign.id, registry=registry_path) is None
    assert inserted == 0


def test_bootstrap_prunes_legacy_bootstrapped_row_without_tombstone(
    tmp_path: Path,
) -> None:
    """A row whose db_path points into the archive dir but whose snapshot +
    sidecar are both gone (deleted on another machine before tombstones
    existed) self-heals away. This is the Saar case."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="LegacySaar")

    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    phantom = archive_dir / f"{campaign.id}.db"  # does not exist
    Path(campaign.db_path).unlink()  # bootstrapped rows have no live DB
    with _connect(registry_path) as conn:
        conn.execute(
            "UPDATE campaigns SET db_path = ? WHERE id = ?",
            (str(phantom), campaign.id),
        )

    bootstrap_archived_snapshots(registry=registry_path, archive_dir=_archive_in_repo(repo_root))

    from chronicler.db.registry import get_campaign_by_id

    assert get_campaign_by_id(campaign.id, registry=registry_path) is None


def test_bootstrap_keeps_live_row_with_missing_db_and_no_tombstone(
    tmp_path: Path,
) -> None:
    """A live-DB row (db_path outside the archive dir) whose file is missing
    and which has no tombstone is TRANSIENT (disconnected drive) — keep
    it, do not prune."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="Transient")
    Path(campaign.db_path).unlink()  # file gone, but db_path is a live path

    bootstrap_archived_snapshots(registry=registry_path, archive_dir=_archive_in_repo(repo_root))

    from chronicler.db.registry import get_campaign_by_id

    assert get_campaign_by_id(campaign.id, registry=registry_path) is not None


def test_delete_endpoint_writes_tombstone(tmp_path, monkeypatch) -> None:
    """End-to-end at the route level: deleting a sealed campaign leaves a
    committed tombstone so other machines reconcile it."""
    from fastapi.testclient import TestClient

    # The archive-sync env guard (set in conftest) would make
    # resolve_repo_root() return None; clear it for this test.
    monkeypatch.delenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", raising=False)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    campaign, registry_path = _seed_campaign(tmp_path, name="EndToEnd")

    # The endpoint resolves repo_root via resolve_repo_root(); designate our
    # repo explicitly (27ov.5: the resolver no longer trusts the CWD).
    monkeypatch.setenv("CHRONICLER_ARCHIVE_DIR", str(_archive_in_repo(repo_root)))

    from chronicler.api import create_app

    app = create_app(registry_path=registry_path)
    with TestClient(app) as client:
        resp = client.delete(f"/api/campaigns/{campaign.name}")
        assert resp.status_code == 204

    tomb = _archive_in_repo(repo_root) / f"{campaign.id}.deleted"
    assert tomb.exists()
    payload = json.loads(tomb.read_text(encoding="utf-8"))
    assert payload["id"] == campaign.id


def test_incident_2026_06_01_munso_saar_reconcile(tmp_path, monkeypatch, caplog) -> None:
    import logging

    from chronicler.db.registry import get_campaign_by_id
    from chronicler.sync.bootstrap import backfill_archived_campaigns

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    archive_dir = _archive_in_repo(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)

    # ck3_chronicler-27ov.15: the prune now runs via the EXPLICIT
    # bootstrap_archived_snapshots call (API lifespan runs it right before
    # the backfill — mirrored below). Clear the conftest test guard and
    # designate the repo explicitly so the reconcile path actually runs
    # (27ov.5: resolver no longer trusts the CWD; mirrors the endpoint test).
    monkeypatch.delenv("CHRONICLER_ARCHIVE_SYNC_DISABLED", raising=False)
    monkeypatch.setenv("CHRONICLER_ARCHIVE_DIR", str(_archive_in_repo(repo_root)))

    # munso: a live-DB row WITH a tombstone (deleted elsewhere).
    munso, registry_path = _seed_campaign(tmp_path, name="munso")
    munso_live = Path(munso.db_path)
    assert munso_live.exists()
    (archive_dir / f"{munso.id}.deleted").write_text(f'{{"id": "{munso.id}"}}', encoding="utf-8")

    # Saar: a bootstrapped row whose snapshot is gone, NO tombstone (legacy).
    saar, _ = _seed_campaign(tmp_path, name="Saar")
    Path(saar.db_path).unlink()
    phantom = archive_dir / f"{saar.id}.db"
    with _connect(registry_path) as conn:
        conn.execute("UPDATE campaigns SET db_path = ? WHERE id = ?", (str(phantom), saar.id))

    with caplog.at_level(logging.WARNING):
        # Startup order per 27ov.15: bootstrap (reconcile/prune) first,
        # then the backfill — which is what previously resurrected munso.
        bootstrap_archived_snapshots(
            registry=registry_path, archive_dir=_archive_in_repo(repo_root)
        )
        exported = backfill_archived_campaigns(
            registry=registry_path, archive_dir=_archive_in_repo(repo_root)
        )

    assert exported == 0  # nothing resurrected
    assert get_campaign_by_id(munso.id, registry=registry_path) is None
    assert get_campaign_by_id(saar.id, registry=registry_path) is None
    assert not munso_live.exists()
    assert not phantom.exists()
    assert "no such table" not in caplog.text


# --- issue #24: the move out of the repo tree ---


def test_warn_if_legacy_archive_unmigrated_names_the_move_command(
    tmp_path: Path, monkeypatch
) -> None:
    """Warn, never move: the snapshots are the user's only copy of those
    campaigns, so startup names the exact command instead of relocating
    files into what might be a half-configured override."""
    from chronicler.sync.archive_export import warn_if_legacy_archive_unmigrated

    legacy = tmp_path / "checkout" / "data" / "archived"
    legacy.mkdir(parents=True)
    (legacy / "old.db").write_bytes(b"snap")
    monkeypatch.setattr("chronicler.config.find_repo_root", lambda: tmp_path / "checkout")
    new_dir = tmp_path / "new-archive"

    message = warn_if_legacy_archive_unmigrated(new_dir)
    assert message is not None
    assert str(legacy) in message
    assert str(new_dir) in message
    assert "mv" in message
    # Nothing was touched.
    assert (legacy / "old.db").exists()
    assert not new_dir.exists()


def test_warn_if_legacy_archive_unmigrated_silent_once_the_new_dir_is_in_use(
    tmp_path: Path, monkeypatch
) -> None:
    """Once anything has been sealed post-move, the old directory is
    history rather than a pending migration — nagging about it forever
    would train the user to ignore startup warnings."""
    from chronicler.sync.archive_export import warn_if_legacy_archive_unmigrated

    legacy = tmp_path / "checkout" / "data" / "archived"
    legacy.mkdir(parents=True)
    (legacy / "old.db").write_bytes(b"snap")
    monkeypatch.setattr("chronicler.config.find_repo_root", lambda: tmp_path / "checkout")
    new_dir = tmp_path / "new-archive"
    new_dir.mkdir()
    (new_dir / "fresh.db").write_bytes(b"snap")

    assert warn_if_legacy_archive_unmigrated(new_dir) is None


def test_warn_if_legacy_archive_unmigrated_ignores_an_empty_legacy_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """A leftover empty directory (or one holding only the old README) is
    not unmigrated state."""
    from chronicler.sync.archive_export import warn_if_legacy_archive_unmigrated

    legacy = tmp_path / "checkout" / "data" / "archived"
    legacy.mkdir(parents=True)
    (legacy / "README.md").write_text("history", encoding="utf-8")
    monkeypatch.setattr("chronicler.config.find_repo_root", lambda: tmp_path / "checkout")

    assert warn_if_legacy_archive_unmigrated(tmp_path / "new-archive") is None


def test_bootstrap_reheals_a_row_left_pointing_at_the_legacy_location(
    tmp_path: Path, monkeypatch
) -> None:
    """Issue #24 danger zone: after the user moves the snapshots out of the
    tree, a previously-bootstrapped row still points at the old path. It
    must be pruned and re-inserted from the moved sidecar, not left as a
    Library entry that 500s on open (nothing would re-insert it — its id is
    already present)."""
    from chronicler.db.registry import list_campaigns

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    legacy = repo_root / "data" / "archived"
    legacy.mkdir(parents=True)
    monkeypatch.setattr("chronicler.config.find_repo_root", lambda: repo_root)

    # Primary side: export into the legacy location, as a pre-move machine did.
    campaign, _ = _seed_campaign(tmp_path, name="Moved")
    export_sealed_campaign(campaign, archive_dir=legacy)

    # Secondary side: bootstrap from the legacy dir, so its row points there.
    secondary = tmp_path / "secondary_registry.db"
    assert bootstrap_archived_snapshots(registry=secondary, archive_dir=legacy) == 1
    row = next(r for r in list_campaigns(include_archived=True, registry=secondary))
    assert row.db_path == str(legacy / f"{campaign.id}.db")

    # The user runs the documented move.
    new_dir = tmp_path / "new-archive"
    new_dir.mkdir()
    for suffix in (".db", ".json"):
        (legacy / f"{campaign.id}{suffix}").rename(new_dir / f"{campaign.id}{suffix}")

    inserted = bootstrap_archived_snapshots(registry=secondary, archive_dir=new_dir)

    assert inserted == 1, "the stale-path row must be pruned and re-inserted"
    rows = list_campaigns(include_archived=True, registry=secondary)
    assert len(rows) == 1
    assert rows[0].id == campaign.id
    assert rows[0].archived is True
    assert rows[0].db_path == str(new_dir / f"{campaign.id}.db")


def test_backfill_never_re_exports_a_legacy_path_projection(tmp_path: Path, monkeypatch) -> None:
    """The resurrection guard has to count the legacy path too: re-exporting
    a row that is really another machine's snapshot would push their
    campaign back out under this machine's hand."""
    from chronicler.db.registry import archive_campaign, create_campaign

    repo_root = tmp_path / "repo"
    legacy = repo_root / "data" / "archived"
    legacy.mkdir(parents=True)
    monkeypatch.setattr("chronicler.config.find_repo_root", lambda: repo_root)

    stale_snapshot = legacy / "aaaaaaaa-0000-0000-0000-000000000000.db"
    stale_snapshot.write_bytes(b"snap")
    registry = tmp_path / "registry.db"
    created = create_campaign("Legacy", db_path=str(stale_snapshot), registry=registry)
    archive_campaign(created.id, registry=registry)

    exported = backfill_archived_campaigns(registry=registry, archive_dir=tmp_path / "new-archive")
    assert exported == 0


def test_tombstone_is_written_without_git(tmp_path: Path) -> None:
    """The tombstone must not become conditional on the sync vehicle being
    git — a machine that receives a snapshot whose tombstone never arrived
    re-exports it and resurrects the campaign (ck3_chronicler-wvrm)."""
    from chronicler.sync.archive_export import tombstone_campaign

    archive_dir = tmp_path / "plain-archive"
    campaign, _ = _seed_campaign(tmp_path, name="Doomed")

    result = tombstone_campaign(campaign, archive_dir=archive_dir)

    tomb = archive_dir / f"{campaign.id}.deleted"
    assert tomb.exists()
    assert result.snapshot_written is True
    assert result.committed is False
    payload = json.loads(tomb.read_text(encoding="utf-8"))
    assert payload["id"] == campaign.id


def test_tombstone_without_git_still_prunes_on_the_other_machine(
    tmp_path: Path,
) -> None:
    """End-to-end of the danger zone: deletion propagates through a
    non-git archive dir exactly as it did through the repo."""
    from chronicler.db.registry import list_campaigns
    from chronicler.sync.archive_export import tombstone_campaign

    archive_dir = tmp_path / "shared-archive"
    campaign, _ = _seed_campaign(tmp_path, name="Deleted")
    export_sealed_campaign(campaign, archive_dir=archive_dir)

    secondary = tmp_path / "secondary_registry.db"
    assert bootstrap_archived_snapshots(registry=secondary, archive_dir=archive_dir) == 1

    # Machine A deletes: unlink the pair, write the tombstone.
    for suffix in (".db", ".json"):
        (archive_dir / f"{campaign.id}{suffix}").unlink()
    tombstone_campaign(campaign, archive_dir=archive_dir)

    # Machine B reconciles it out rather than resurrecting it.
    bootstrap_archived_snapshots(registry=secondary, archive_dir=archive_dir)
    assert list_campaigns(include_archived=True, registry=secondary) == []


def test_bootstrap_rehomes_a_row_from_an_unrelated_dead_path(tmp_path: Path) -> None:
    """The archive dir can move anywhere, not just out of the tree (a second
    clone, a renamed synced folder). A row whose DB file is gone while the
    archive dir holds that campaign is re-homed to the live location."""
    from chronicler.db.registry import list_campaigns

    old_home = tmp_path / "some-other-clone" / "archived"
    old_home.mkdir(parents=True)
    campaign, _ = _seed_campaign(tmp_path, name="Wandering")
    export_sealed_campaign(campaign, archive_dir=old_home)

    secondary = tmp_path / "secondary_registry.db"
    assert bootstrap_archived_snapshots(registry=secondary, archive_dir=old_home) == 1

    new_home = tmp_path / "elsewhere"
    new_home.mkdir()
    for suffix in (".db", ".json"):
        (old_home / f"{campaign.id}{suffix}").rename(new_home / f"{campaign.id}{suffix}")

    assert bootstrap_archived_snapshots(registry=secondary, archive_dir=new_home) == 1
    rows = list_campaigns(include_archived=True, registry=secondary)
    assert [r.db_path for r in rows] == [str(new_home / f"{campaign.id}.db")]


def test_bootstrap_leaves_a_live_campaign_alone_when_its_id_is_in_the_archive(
    tmp_path: Path,
) -> None:
    """The re-home arm must never touch a campaign whose DB is present —
    that would drop a row this machine owns in favour of someone else's
    read-only snapshot."""
    from chronicler.db.registry import list_campaigns

    archive_dir = tmp_path / "archive"
    campaign, registry = _seed_campaign(tmp_path, name="Live")
    export_sealed_campaign(campaign, archive_dir=archive_dir)

    assert bootstrap_archived_snapshots(registry=registry, archive_dir=archive_dir) == 0
    rows = list_campaigns(include_archived=True, registry=registry)
    assert [r.db_path for r in rows] == [campaign.db_path]
    assert Path(campaign.db_path).exists()


# --- Issue #54: no lingering handles on the snapshot ---


def _open_fds_for(path: Path) -> list[str]:
    """File descriptors this process currently holds on ``path``.

    Linux-only (``/proc/self/fd``), which is the point: the defect it
    catches is *invisible* on POSIX at the behavioural level, because an
    open file renames and unlinks perfectly well there. Asserting on the
    handle itself is what lets the Linux CI leg fail on a bug whose only
    behavioural symptom is a Windows WinError 32.
    """
    found = []
    target = str(path.resolve())
    for fd in os.listdir("/proc/self/fd"):
        try:
            link = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue  # fd closed while we were looking
        if link == target:
            found.append(fd)
    return found


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_export_leaves_no_open_handle_on_the_snapshot(tmp_path: Path) -> None:
    """Issue #54: export_sealed_campaign must close the snapshot it wrote.

    ``sqlite3.connect(...)`` used directly as a context manager COMMITS on
    exit but does not CLOSE — the connection lives until garbage collection.
    Export opened both the live DB and the snapshot that way, so the
    snapshot stayed open for the life of the process.

    On Linux nothing observable happens, which is how this survived: the
    three archive-move tests were green here and had failed on every Windows
    CI run since #24, because Windows refuses to rename a file with an open
    handle. The user-facing consequence is the same refusal — someone
    following docs/archived-campaigns.md while chronicler is running gets
    WinError 32 moving their own snapshots.

    This test's own harness already knew the gotcha (see the note in
    ``_seed_campaign``); the production path had never had it applied.
    """
    campaign, _registry = _seed_campaign(tmp_path, name="Handles")
    archive_dir = tmp_path / "archive"
    export_sealed_campaign(campaign, archive_dir=archive_dir)

    snapshot = archive_dir / f"{campaign.id}.db"
    assert snapshot.is_file()
    assert _open_fds_for(snapshot) == [], (
        "export left an open handle on the snapshot — on Windows that makes "
        "the file unmovable for the life of the process (issue #54)"
    )
    assert _open_fds_for(Path(campaign.db_path)) == [], (
        "export left an open handle on the live campaign DB"
    )


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_exported_snapshot_can_be_moved_immediately(tmp_path: Path) -> None:
    """The user-visible form of the same assertion: the documented move
    works right after a seal, with no wait and no restart.

    Passes on POSIX either way — kept because it is the behaviour the three
    Windows-failing tests actually exercise, and it states the contract in
    the terms docs/archived-campaigns.md uses.
    """
    campaign, _registry = _seed_campaign(tmp_path, name="Movable")
    archive_dir = tmp_path / "archive"
    export_sealed_campaign(campaign, archive_dir=archive_dir)

    snapshot = archive_dir / f"{campaign.id}.db"
    destination = tmp_path / "elsewhere"
    destination.mkdir()
    snapshot.rename(destination / snapshot.name)
    assert (destination / snapshot.name).is_file()
    assert not snapshot.exists()

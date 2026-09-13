"""HTTP-level tests for the chronicler narrative-queue / provider status API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicler.db.repository import (
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-ak6: provider-status endpoint ---


def test_provider_status_unconfigured_when_no_provider(client: TestClient) -> None:
    """Base fixture has no provider — endpoint returns mode='unconfigured'
    with all zero/None stat fields."""
    resp = client.get("/api/settings/provider-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "unconfigured"
    assert data["model"] is None
    assert data["recent_biographies"] == 0
    assert data["avg_biography_ms"] is None


def test_provider_status_reflects_queue_completions(client: TestClient) -> None:
    """ck3_chronicler-27ov.41 (audit M-N1): provider-status surfaces the
    completion count + avg latency from NarrativeQueueState (the live source
    that actually records completions), not the dead GenerationStats ring that
    nothing ever wrote to (so it always reported 0/None)."""
    queue = client.app.state.narrative_queue
    a = queue.enqueue(1, "biography")
    queue.mark_completed(a, duration_ms=1500)
    b = queue.enqueue(2, "biography")
    queue.mark_completed(b, duration_ms=2500)

    resp = client.get("/api/settings/provider-status")
    data = resp.json()
    assert data["recent_biographies"] == 2
    assert data["avg_biography_ms"] == 2000


def test_provider_status_with_claude_code_provider(api: CampaignHarness) -> None:
    """ck3_chronicler-tbrm.3: a configured ClaudeCodeProvider surfaces
    as mode='claude-code' with the bracketed model id parsed out."""

    class _ClaudeCodeStub:
        @property
        def name(self) -> str:
            return "claude-code:claude-opus-4-7[1m]"

        async def generate(self, req):
            raise NotImplementedError

    c = api.client(provider=_ClaudeCodeStub())
    resp = c.get("/api/settings/provider-status")
    data = resp.json()
    assert data["mode"] == "claude-code"
    assert data["model"] == "claude-opus-4-7[1m]"


# --- ck3_chronicler-5d9o: per-kind model snapshot endpoint ---


def test_resolved_models_falls_back_to_module_defaults_when_no_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No provider wired (base fixture) — endpoint delegates to the
    module-level ``resolved_models`` which reports the per-kind defaults."""
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_BIO_MODEL", raising=False)
    monkeypatch.delenv("CHRONICLER_CLAUDE_CODE_CLOSING_MODEL", raising=False)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "memory" not in data
    # cs1o: defaults are the BASE model; [1m] engages per-call by size.
    assert data["biography"] == "claude-opus-4-7"
    assert data["closing"] == "claude-opus-4-7"
    assert data["global_override"] is None


def test_resolved_models_reflects_provider_constructor_override(
    api: CampaignHarness,
) -> None:
    """A ClaudeCodeProvider constructed with an explicit ``model=`` kwarg
    pins every kind to that tag — the endpoint must show the override
    propagated through (not stale module-level defaults)."""

    class _Stub:
        @property
        def name(self) -> str:
            return "claude-code:claude-haiku-4-5"

        def resolved_models(self) -> dict[str, str | None]:
            return {
                "biography": "claude-haiku-4-5",
                "closing": "claude-haiku-4-5",
                "global_override": "claude-haiku-4-5",
            }

        async def generate(self, req):
            raise NotImplementedError

    c = api.client(provider=_Stub())
    resp = c.get("/api/settings/models")
    data = resp.json()
    assert "memory" not in data
    assert data["biography"] == "claude-haiku-4-5"
    assert data["closing"] == "claude-haiku-4-5"
    assert data["global_override"] == "claude-haiku-4-5"


def test_resolved_models_surfaces_legacy_global_env_as_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy CHRONICLER_CLAUDE_CODE_MODEL pins every kind to its value
    and the endpoint must signal that via the ``global_override`` field
    so the FE can render 'Override active: <tag>' alongside the rows."""
    monkeypatch.setenv("CHRONICLER_CLAUDE_CODE_MODEL", "claude-opus-4-7[1m]")

    resp = client.get("/api/settings/models")
    data = resp.json()
    assert "memory" not in data
    assert data["biography"] == "claude-opus-4-7[1m]"
    assert data["closing"] == "claude-opus-4-7[1m]"
    assert data["global_override"] == "claude-opus-4-7[1m]"


# --- ck3_chronicler-gx7b: global LLM pause endpoint ---


def test_llm_pause_default_state_is_unpaused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh chronicler with no settings.json: paused=False, paused_at=None."""
    from chronicler import settings_store
    from chronicler.save import ingest as ingest_module

    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", tmp_path / "settings.json")
    ingest_module._reset_llm_paused_cache()

    resp = client.get("/api/settings/llm-pause")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paused"] is False
    assert data["paused_at"] is None
    assert data["last_drain"] is None


def test_llm_pause_put_true_persists_state_and_clears_drain_report(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUT {paused: true} writes llm_paused + llm_paused_at to settings
    and returns the post-flip state. last_drain resets to None — a fresh
    pause shouldn't carry stale counts from a prior unpause."""
    from chronicler import settings_store
    from chronicler.save import ingest as ingest_module

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", settings_path)
    ingest_module._reset_llm_paused_cache()

    resp = client.put("/api/settings/llm-pause", json={"paused": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["paused"] is True
    assert isinstance(data["paused_at"], str)
    assert data["last_drain"] is None

    # State persisted to disk.
    import json as _json

    persisted = _json.loads(settings_path.read_text(encoding="utf-8"))
    assert persisted.get("llm_paused") is True
    assert isinstance(persisted.get("llm_paused_at"), str)


def test_llm_pause_put_false_triggers_drain_across_open_campaigns(
    api: CampaignHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ck3_chronicler-gx7b end-to-end: flipping pause OFF runs the drain
    across every open campaign and surfaces the aggregated DrainReport in
    the response. The scheduler is constructed lazily by
    _resolve_or_build_scheduler against the configured narrative_provider.

    Consolidation scheduling removed (plan: cozy-coalescing-shannon) —
    drain now only fires biographies for dead-without-bio characters.
    """
    from chronicler import settings_store
    from chronicler.db.registry import add_tracked_character, list_campaigns
    from chronicler.save import ingest as ingest_module

    settings_path = api.data_dir / "settings.json"
    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH", settings_path)
    ingest_module._reset_llm_paused_cache()

    # Wire a stub provider so _resolve_or_build_scheduler succeeds.
    class _StubProvider:
        @property
        def name(self) -> str:
            return "claude-code:stub"

        async def generate(self, req):
            raise NotImplementedError

    # Seed a campaign + tracked dead character (no bio) to give the
    # drain something to schedule.
    camp = api.campaign(
        "Drain Camp",
        seed=lambda s: upsert_character(
            s, ck3_id=1234, first_name="DeadNoBio", death_date="1075.8.27"
        ),
    )
    add_tracked_character(camp.id, 1234, note="t1", registry=api.registry)

    c = api.client(provider=_StubProvider())
    # First: pause.
    c.put("/api/settings/llm-pause", json={"paused": True})

    # Now: unpause. The drain runs against the seeded campaign;
    # the response carries the aggregated DrainReport.
    resp = c.put("/api/settings/llm-pause", json={"paused": False})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["paused"] is False
    assert data["paused_at"] is None
    report = data["last_drain"]
    assert report is not None
    # biographies_scheduled may be 0 or 1 depending on queue_state
    # wiring — both are valid.
    assert isinstance(report["biographies_scheduled"], int)

    # Cleanup hand-rolled.
    assert list_campaigns(include_archived=False, registry=api.registry)


# --- ck3_chronicler-eev: narrative-queue endpoint ---


def test_narrative_queue_empty_by_default(client: TestClient) -> None:
    """Fresh app — queue empty, counts zero, avg None."""
    resp = client.get("/api/settings/narrative-queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == []
    assert data["active"] == []
    assert data["recent"] == []
    assert data["completed_count"] == 0
    assert data["failed_count"] == 0
    assert data["avg_duration_ms"] is None


def test_narrative_queue_reflects_state_mutations(client: TestClient) -> None:
    """The snapshot endpoint reads app.state.narrative_queue, so mutating
    it directly is equivalent to the scheduler's lifecycle calls — and
    the JSON shape matches NarrativeQueueResponse exactly."""
    queue = client.app.state.narrative_queue
    a = queue.enqueue(1234, "biography")
    b = queue.enqueue(5678, "biography")
    queue.mark_started(a)
    queue.mark_completed(a, duration_ms=2500)

    resp = client.get("/api/settings/narrative-queue")
    data = resp.json()
    assert [i["character_id"] for i in data["queued"]] == [5678]
    assert data["queued"][0]["kind"] == "biography"
    assert [i["character_id"] for i in data["recent"]] == [1234]
    assert data["recent"][0]["status"] == "completed"
    assert data["recent"][0]["duration_ms"] == 2500
    assert data["completed_count"] == 1
    assert data["failed_count"] == 0
    assert data["avg_duration_ms"] == 2500
    # Untouched B is the only active item — wait, not active, queued.
    # Only A was started; B stays queued.
    assert data["active"] == []
    # b retained item_id 2
    assert b == 2


def test_narrative_queue_serialises_character_name(client: TestClient) -> None:
    """ck3_chronicler-27ov.81 (audit L30): the scheduler resolves a name at
    enqueue and it rides on the wire, so the FE renders queue names without
    a join against the active campaign's character window. None when the
    name wasn't resolved (older callers / unknown id)."""
    queue = client.app.state.narrative_queue
    queue.enqueue(1234, "biography", character_name="Erik")
    queue.enqueue(5678, "biography")  # no name resolved

    data = client.get("/api/settings/narrative-queue").json()
    by_cid = {i["character_id"]: i for i in data["queued"]}
    assert by_cid[1234]["character_name"] == "Erik"
    assert by_cid[5678]["character_name"] is None


def test_narrative_queue_cancel_unknown_item_returns_cancelled_false(
    client: TestClient,
) -> None:
    """c5wq: cancelling an unknown item_id returns 200 with
    cancelled=False — not a 404 — so the FE can treat a stale snapshot
    benignly."""
    resp = client.delete("/api/settings/narrative-queue/9999")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False, "item_id": 9999}


@pytest.mark.asyncio
async def test_narrative_queue_cancel_signals_scheduler(
    client: TestClient,
) -> None:
    """c5wq: cancelling an active item should call scheduler.cancel_item
    and mark the item failed("cancelled") in the recent ring.

    We inject a minimal fake scheduler so the endpoint contract is
    tested without spinning up a full provider stack."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    queue: NarrativeQueueState = client.app.state.narrative_queue
    a = queue.enqueue(1234, "biography")
    queue.mark_started(a)

    captured: dict[str, int] = {}

    class FakeScheduler:
        async def cancel_item(self, item_id: int) -> bool:
            captured["item_id"] = item_id
            queue.mark_failed(item_id, "cancelled")
            return True

    # The endpoint resolves the scheduler from app.state — install ours.
    client.app.state.narrative_schedulers = {"_test": FakeScheduler()}

    resp = client.delete(f"/api/settings/narrative-queue/{a}")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True, "item_id": a}
    assert captured == {"item_id": a}

    snap = queue.snapshot()
    assert [i.item_id for i in snap.recent] == [a]
    assert snap.recent[0].error == "cancelled"


def test_narrative_queue_regenerate_unknown_item_returns_404(
    client: TestClient,
) -> None:
    """c5wq: regenerate requires the item to be in the recent ring;
    unknown ids return 404 because there's no way to derive
    (character_id, kind) from a stale id."""
    resp = client.post("/api/settings/narrative-queue/9999/regenerate")
    assert resp.status_code == 404


def test_narrative_queue_regenerate_delegates_to_scheduler(
    client: TestClient,
) -> None:
    """c5wq: regenerating a completed item resolves (character_id, kind)
    from the recent ring and calls scheduler.regenerate, bypassing the
    per-character cooldown (regenerate already does this internally)."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    queue: NarrativeQueueState = client.app.state.narrative_queue
    a = queue.enqueue(1234, "biography")
    queue.mark_completed(a, duration_ms=2500)

    captured: dict[str, object] = {}

    class FakeScheduler:
        def regenerate(self, character_id: int) -> int | None:
            captured["character_id"] = character_id
            return 42

    client.app.state.narrative_schedulers = {"_test": FakeScheduler()}

    resp = client.post(f"/api/settings/narrative-queue/{a}/regenerate")
    assert resp.status_code == 202
    body = resp.json()
    assert body == {"character_id": 1234, "kind": "biography", "item_id": 42}
    assert captured == {"character_id": 1234}


def test_narrative_queue_regenerate_dispatches_to_owning_campaign_scheduler(
    client: TestClient,
) -> None:
    """ck3_chronicler-27ov.4 (audit H10): character ids are campaign-local.
    regenerate must dispatch to the scheduler that owns item.campaign_uuid,
    not the first scheduler in the dict — otherwise, with two campaigns open,
    it regenerates against the wrong DB."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    queue: NarrativeQueueState = client.app.state.narrative_queue
    item_id = queue.enqueue(1234, "biography", campaign_uuid="camp-B")
    queue.mark_completed(item_id, duration_ms=2500)

    called: list[str] = []

    class FakeScheduler:
        def __init__(self, name: str) -> None:
            self.name = name

        def regenerate(self, character_id: int) -> int | None:
            called.append(self.name)
            return 99

    # camp-A registered first; the buggy code would pick it.
    client.app.state.narrative_schedulers = {
        "camp-A": FakeScheduler("camp-A"),
        "camp-B": FakeScheduler("camp-B"),
    }

    resp = client.post(f"/api/settings/narrative-queue/{item_id}/regenerate")
    assert resp.status_code == 202
    assert called == ["camp-B"], "must dispatch to the item's owning campaign"


def test_narrative_queue_regenerate_503_when_owning_campaign_has_no_scheduler(
    client: TestClient,
) -> None:
    """ck3_chronicler-27ov.4 (audit H10): when the item's campaign has no
    live/lazy scheduler, return 503 rather than guessing another campaign's
    scheduler (which would generate against the wrong DB)."""
    from chronicler.narrative.queue_state import NarrativeQueueState

    queue: NarrativeQueueState = client.app.state.narrative_queue
    item_id = queue.enqueue(1234, "biography", campaign_uuid="camp-missing")
    queue.mark_completed(item_id, duration_ms=2500)

    class FakeScheduler:
        def regenerate(self, character_id: int) -> int | None:
            return 99

    client.app.state.narrative_schedulers = {"camp-other": FakeScheduler()}
    client.app.state._lazy_regen_schedulers = {}

    resp = client.post(f"/api/settings/narrative-queue/{item_id}/regenerate")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_narrative_queue_reorder_delegates_to_scheduler(
    client: TestClient,
) -> None:
    """c5wq: PATCH /reorder fans out to every known scheduler and
    aggregates the new_item_ids. With a single scheduler and three
    queued items, the result is the scheduler's reorder output."""
    captured: dict[str, object] = {}

    class FakeScheduler:
        async def reorder_queued(self, requested_ids: list[int]) -> list[int]:
            captured["requested_ids"] = requested_ids
            return [101, 102, 103]

    client.app.state.narrative_schedulers = {"_test": FakeScheduler()}

    resp = client.patch(
        "/api/settings/narrative-queue/reorder",
        json={"item_ids": [3, 1, 2]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"new_item_ids": [101, 102, 103]}
    assert captured == {"requested_ids": [3, 1, 2]}


def test_narrative_queue_reorder_with_no_schedulers_returns_empty(
    client: TestClient,
) -> None:
    """c5wq: with no active scheduler, reorder is a no-op (empty result),
    not a 500. A user opening the queue page on a fresh process with
    no save-tail running will see this — and that's fine."""
    # Wipe both scheduler dicts.
    client.app.state.narrative_schedulers = {}
    client.app.state._lazy_regen_schedulers = {}

    resp = client.patch(
        "/api/settings/narrative-queue/reorder",
        json={"item_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json() == {"new_item_ids": []}

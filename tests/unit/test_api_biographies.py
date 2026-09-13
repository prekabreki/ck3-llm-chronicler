"""HTTP-level tests for the chronicler biographies API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.api import create_app
from chronicler.db import (
    Base,
    make_engine_for_path,
    make_session_factory,
)
from chronicler.db.repository import (
    insert_biography,
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-fiv6 (was kdf): defer-narratives + drain endpoints
# were removed in 2026-05-08 with the local-tier providers. The
# regenerate path against a live scheduler still has its own coverage
# below. ---


# --- ck3_chronicler-7bi5 (revived by cs1o): biography cost-estimate ---


def test_biography_cost_estimate_404_unknown_character(client: TestClient) -> None:
    resp = client.get("/api/campaigns/test-campaign/characters/9999999/biography/cost-estimate")
    assert resp.status_code == 404


def test_biography_cost_estimate_uses_prior_generation_when_present(
    api: CampaignHarness, registry_and_campaign: tuple[Path, str]
) -> None:
    """A prior biography with token columns wins over the heuristic —
    and its cache breakdown makes the USD figure cache-aware."""
    campaign_db = api.data_dir / "campaigns" / "test.db"
    engine = make_engine_for_path(campaign_db)
    factory = make_session_factory(engine)
    with factory() as s:
        insert_biography(
            s,
            character_id=36892,
            body="A vita with token cols set.",
            prompt_template_version="biography_v5",
            provider="claude-code:claude-opus-4-7",
            generated_at="2026-06-02T12:00:00+00:00",
            prompt_tokens=4321,
            completion_tokens=1234,
            cache_read_tokens=3000,
            cache_write_tokens=1000,
        )
        s.commit()
    engine.dispose()

    class _NamedProvider:
        name = "claude-code:claude-opus-4-7"

        def name_for_kind(self, kind: str) -> str:
            return self.name

    c = api.client(provider=_NamedProvider())
    resp = c.get("/api/campaigns/test-campaign/characters/36892/biography/cost-estimate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["estimated_input_tokens"] == 4321
    assert body["estimated_output_tokens"] == 1234
    assert body["estimated_total_tokens"] == 5555
    assert body["method"] == "from_prior_generation"
    assert body["would_route_to"] == "claude-code:claude-opus-4-7"
    assert body["pool_billed"] is True
    from chronicler.cost import compute_generation_cost

    expected_usd = round(
        compute_generation_cost(
            "claude-code:claude-opus-4-7",
            input_tokens=4321,
            cache_read_tokens=3000,
            cache_write_tokens=1000,
            output_tokens=1234,
        ),
        4,
    )
    assert body["est_usd"] == pytest.approx(expected_usd)


def test_biography_cost_estimate_estimates_from_event_count_when_no_prior(
    make_campaign: Callable[..., TestClient],
) -> None:
    """A character with no biographies falls back to the event-count
    heuristic: BASE + count*PER_EVENT, plus the world-context bonus
    when region_summary_json is set."""
    c = make_campaign(
        "fresh",
        seed=lambda s: upsert_character(s, ck3_id=42, first_name="Edmund"),
    )
    resp = c.get("/api/campaigns/fresh/characters/42/biography/cost-estimate")
    assert resp.status_code == 200
    body = resp.json()
    # No events ⇒ input == BASE (1500), output == DEFAULT (1100).
    assert body["estimated_input_tokens"] == 1500
    assert body["estimated_output_tokens"] == 1100
    assert body["method"] == "estimate"
    assert body["kind"] == "biography"  # no world context


def test_biography_cost_estimate_world_context_bumps_kind_and_input(
    make_campaign: Callable[..., TestClient],
) -> None:
    """A character with region_summary_json gets kind=biography_woven
    and the world-context bonus added to the input estimate."""

    def _seed(s: Session) -> None:
        upsert_character(s, ck3_id=42, first_name="X")
        from chronicler.db.models import Character

        char = s.query(Character).filter_by(ck3_id=42).one()
        char.region_summary_json = '{"region": "Wessex"}'

    c = make_campaign("wctx", seed=_seed)
    resp = c.get("/api/campaigns/wctx/characters/42/biography/cost-estimate")
    body = resp.json()
    # 1500 base + 1500 world-context bonus.
    assert body["estimated_input_tokens"] == 3000
    assert body["kind"] == "biography_woven"


def test_biography_cost_estimate_unconfigured_provider_returns_zero_usd(
    client: TestClient,
) -> None:
    """No narrative provider on app.state ⇒ would_route_to=null and
    est_usd=0. Token estimates still come back so the UI can render a
    'no model selected' note rather than a price."""
    resp = client.get("/api/campaigns/test-campaign/characters/36892/biography/cost-estimate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["would_route_to"] is None
    assert body["est_usd"] == 0.0
    assert body["pool_billed"] is False
    assert isinstance(body["estimated_input_tokens"], int)


# --- ck3_chronicler-7gw: POST /characters/{ck3_id}/biography/regenerate ---


def test_regenerate_biography_503_when_no_provider(client: TestClient) -> None:
    """ck3_chronicler-u0eu (2026-05-08): the route now lazily constructs
    a scheduler when none is on app.state, so a missing scheduler is
    no longer fatal. The remaining 503 case is no narrative_provider
    configured — without one, generation has nowhere to land.

    The default test app fixture passes no narrative_provider, so this
    is the natural baseline."""
    resp = client.post("/api/campaigns/test-campaign/characters/36892/biography/regenerate")
    assert resp.status_code == 503
    assert "no narrative provider" in resp.json()["detail"].lower()


def test_regenerate_biography_404_unknown_character(client: TestClient) -> None:
    """Character lookup runs before scheduler resolution so an unknown
    id returns 404 even when no scheduler is running."""
    resp = client.post("/api/campaigns/test-campaign/characters/9999999/biography/regenerate")
    assert resp.status_code == 404


def test_regenerate_biography_404_unknown_campaign(client: TestClient) -> None:
    resp = client.post("/api/campaigns/nope/characters/36892/biography/regenerate")
    assert resp.status_code == 404


def test_regenerate_biography_against_live_scheduler(client: TestClient) -> None:
    """End-to-end: register a real NarrativeScheduler on app.state, hit
    the route, and observe a queue item enqueued for the requested
    character. We don't wait on the spawned generation task here —
    TestClient's event loop tears down after the request, which makes
    awaiting the task brittle. The scheduler-level test in
    test_scheduler.py covers the actual generation path.
    """
    import tempfile

    from chronicler.db.registry import get_campaign_by_name
    from chronicler.db.repository import upsert_character
    from chronicler.narrative.queue_state import NarrativeQueueState
    from chronicler.narrative.scheduler import NarrativeScheduler
    from tests.helpers.providers import CountingProvider

    # u0eu (2026-05-08): the route reuses app.state.narrative_scheduler
    # only when its _campaign_uuid matches the resolved campaign — the
    # test scheduler has to carry the live campaign's id for this path
    # to fire. Otherwise the route lazy-constructs against the registry's
    # provider, which is None on the default fixture.
    cache = client.app.state.engine_cache
    campaign = get_campaign_by_name("test-campaign", registry=cache.registry_path)
    assert campaign is not None

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = make_engine_for_path(Path(tmpdir) / "regen.db")
        Base.metadata.create_all(engine)
        local_factory = make_session_factory(engine)
        with local_factory() as s:
            upsert_character(s, ck3_id=36892)
            s.commit()

        provider = CountingProvider()
        queue_state = NarrativeQueueState()
        scheduler = NarrativeScheduler(
            local_factory,
            provider,
            campaign_uuid=campaign.id,
            queue_state=queue_state,
            # Even with everything that would normally block save-tail spawns,
            # the regenerate path must fire.
            is_tracked=lambda _cid: False,
            is_paused=lambda _cid: True,
        )
        # ck3_chronicler-m4cn: register the scheduler in the per-campaign
        # dict on app.state. Was a single slot; now a campaign_id-keyed
        # dict so two adopted campaigns can each have a live scheduler.
        client.app.state.narrative_schedulers = {campaign.id: scheduler}
        try:
            resp = client.post("/api/campaigns/test-campaign/characters/36892/biography/regenerate")
            assert resp.status_code == 202
            body = resp.json()
            assert body["character_id"] == 36892
            assert body["item_id"] == 1
            # Queue state was updated synchronously inside regenerate()
            # before the task spawn — assertable without awaiting the task.
            snap = queue_state.snapshot()
            total = snap.completed_count + snap.failed_count + len(snap.queued) + len(snap.active)
            assert total >= 1
        finally:
            client.app.state.narrative_schedulers = {}
            engine.dispose()


def test_regenerate_biography_lazily_constructs_scheduler_when_none_active(
    registry_and_campaign: tuple[Path, str],
) -> None:
    """ck3_chronicler-u0eu (2026-05-08): when no save-tail loop is
    running (e.g. the only campaign is sealed), the route used to 503
    'no narrative scheduler running'. Now it lazily constructs a
    scheduler bound to this campaign + the configured provider so
    regenerate works without a live save-tail.

    Provider is wired explicitly via create_app(narrative_provider=...)
    here because the default client fixture passes None."""
    from tests.helpers.providers import CountingProvider

    registry, name = registry_and_campaign
    provider = CountingProvider()
    app = create_app(registry_path=registry, narrative_provider=provider)
    # Crucially: leave app.state.narrative_schedulers empty. The route
    # must work without a live save-tail scheduler.
    assert not getattr(app.state, "narrative_schedulers", {})

    with TestClient(app) as c:
        resp = c.post(f"/api/campaigns/{name}/characters/36892/biography/regenerate")
        assert resp.status_code == 202, (
            f"lazy-scheduler regenerate must succeed; got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["character_id"] == 36892
        # The lazy cache should now hold a scheduler keyed by the
        # campaign id — subsequent calls reuse the same one.
        from chronicler.db.registry import get_campaign_by_name

        campaign = get_campaign_by_name(name, registry=registry)
        assert campaign is not None
        cache = getattr(app.state, "_lazy_regen_schedulers", {})
        assert campaign.id in cache


def test_scheduler_provisioning_seam_lives_in_dependencies() -> None:
    """ck3_chronicler-z5os: scheduler provisioning is one seam in the
    dependencies module, not a function borrowed across route modules.

    The architectural guarantee: settings.py can provision a scheduler
    for its llm-pause drain without importing from the characters route
    module. We assert (a) the function lives in dependencies, (b) the
    characters route still exposes it (as a re-export) so existing
    importers keep working, and (c) the settings module no longer
    references the characters route module in its source.
    """
    import inspect

    from chronicler.api import dependencies
    from chronicler.api.routes import characters, settings

    # (a) The seam lives in dependencies and is the same object the
    #     characters route re-exports.
    assert hasattr(dependencies, "resolve_or_build_scheduler")
    assert characters._resolve_or_build_scheduler is dependencies.resolve_or_build_scheduler

    # (c) settings.py provisions via the dependencies seam, never by
    #     reaching across into the characters route module.
    settings_src = inspect.getsource(settings)
    assert "routes.characters" not in settings_src
    assert "resolve_or_build_scheduler" in settings_src


def test_regenerate_biography_works_against_archived_campaign(
    registry_and_campaign: tuple[Path, str],
) -> None:
    """fk9r-followup + u0eu (2026-05-08): sealing a campaign locks save-tail
    state-rotation, not biography editing. When 081b/nwoc/1m8q-class
    bugs surface post-seal the user must be able to regenerate bios
    retroactively — otherwise the closing chronicle stays synthesised
    off bad biographies forever.

    Pre-fix the route used get_campaign (which excludes archived),
    so a sealed campaign returned 404 even when everything else was
    healthy. fk9r-followup switched to get_campaign_including_archived;
    u0eu added the lazy-scheduler fallback so regenerate works without
    a live save-tail (sealed campaigns don't tail).
    """
    from chronicler.db.registry import (
        archive_campaign,
        get_campaign_by_name,
    )
    from tests.helpers.providers import CountingProvider

    registry, name = registry_and_campaign

    # Seal the test campaign so the read path must go through
    # include_archived.
    sealed = get_campaign_by_name(name, registry=registry)
    assert sealed is not None
    archive_campaign(sealed.id, registry=registry)

    # Wire a provider through create_app so the lazy scheduler has
    # somewhere to land its work. Do NOT pre-stash a save-tail scheduler.
    provider = CountingProvider()
    app = create_app(registry_path=registry, narrative_provider=provider)

    with TestClient(app) as c:
        resp = c.post(f"/api/campaigns/{name}/characters/36892/biography/regenerate")
        # Pre-fix: 404 'campaign not found' (sealed). Post-fk9r-followup +
        # u0eu: 202 — sealed campaigns are still regenerable, and the
        # absence of a save-tail scheduler is no longer fatal.
        assert resp.status_code == 202, (
            f"sealed-campaign regenerate must succeed; got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["character_id"] == 36892


# --- ck3_chronicler (2026-05-09): GET /api/campaigns/{name}/biographies ---


def test_biographies_endpoint_lists_chronicled_characters_in_chronological_order(
    make_campaign: Callable[..., TestClient],
) -> None:
    """The all-biographies surface must surface every character with at
    least one biography (dedupe to latest version) in death-date asc
    order; living characters sort to the end."""
    from chronicler.db.repository import insert_biography, upsert_character

    def _seed(s: Session) -> None:
        # Three characters, each with a biography. Two deceased + one
        # still living. Out-of-input-order so the asc-sort assertion is
        # meaningful.
        upsert_character(
            s,
            ck3_id=10,
            first_name="Christoffer",
            birth_date="1085.5.1",
            death_date=None,
            dynasty_name="Thrugot",
        )
        upsert_character(
            s,
            ck3_id=11,
            first_name="Thrugot",
            birth_date="1040.1.1",
            death_date="1075.4.1",
            dynasty_name="Thrugot",
        )
        upsert_character(
            s,
            ck3_id=12,
            first_name="Svend",
            nickname="the Bold",
            birth_date="1066.9.15",
            death_date="1102.6.6",
            dynasty_name="Thrugot",
        )
        # Character with no biography — must NOT appear.
        upsert_character(
            s,
            ck3_id=13,
            first_name="Margaret",
            birth_date="1090.1.1",
            death_date=None,
            dynasty_name="Thrugot",
        )
        for cid in (10, 11, 12):
            insert_biography(
                s,
                character_id=cid,
                body="A long-and-detailed body. " * 12,
                prompt_template_version="biography_v1",
                provider="ollama:qwen3:14b",
                generated_at=f"2026-05-0{cid - 9}T00:00:00+00:00",
            )
        # Christoffer gets a regenerate (version 2) — must appear once,
        # with the version-2 generated_at.
        insert_biography(
            s,
            character_id=10,
            body="Updated bio body.",
            prompt_template_version="biography_v2",
            provider="anthropic:claude-opus-4-7",
            generated_at="2026-05-09T00:00:00+00:00",
        )

    c = make_campaign("thrugot", seed=_seed)
    resp = c.get("/api/campaigns/thrugot/biographies")
    assert resp.status_code == 200
    rows = resp.json()
    # Margaret has no biography → not in the list.
    assert {r["ck3_id"] for r in rows} == {10, 11, 12}
    # Order: Thrugot (died 1075) → Svend (1102) → Christoffer (alive, last).
    assert [r["first_name"] for r in rows] == ["Thrugot", "Svend", "Christoffer"]
    # Latest version wins for Christoffer.
    christoffer = next(r for r in rows if r["ck3_id"] == 10)
    assert christoffer["version"] == 2
    assert christoffer["generated_at"] == "2026-05-09T00:00:00+00:00"
    # Excerpt cut + generated_at attached.
    assert all(r["biography_excerpt"] for r in rows)
    # Nickname surfaced when present.
    svend = next(r for r in rows if r["ck3_id"] == 12)
    assert svend["nickname"] == "the Bold"


def test_biographies_endpoint_empty_when_no_biographies(
    make_campaign: Callable[..., TestClient],
) -> None:
    """A campaign with characters but zero biographies returns []."""
    from chronicler.db.repository import upsert_character

    c = make_campaign(
        "fresh",
        seed=lambda s: upsert_character(s, ck3_id=1, first_name="Alfred", dynasty_name="Wessex"),
    )
    resp = c.get("/api/campaigns/fresh/biographies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_classify_bio_role_ruler_kin_consort_other():
    # ck3_chronicler-lmex: role grouping + spouse-based relation line.
    from chronicler.api.routes.dynasty import _classify_bio_role

    P = "Sleggja"
    # Ruler: dynasty member who held a title; relation = spouse
    assert _classify_bio_role(
        char_dynasty="Sleggja",
        player_dynasty=P,
        has_title=True,
        family={"primary_spouse": {"id": 1, "name": "Malmfridr"}},
        female=False,
    ) == ("ruler", "m. Malmfridr")
    # Ruler with no spouse recorded -> no relation line
    assert _classify_bio_role(
        char_dynasty="Sleggja",
        player_dynasty=P,
        has_title=True,
        family={},
        female=False,
    ) == ("ruler", None)
    # Kin: dynasty member, no title; spouse if married (may be a list)
    assert _classify_bio_role(
        char_dynasty="Sleggja",
        player_dynasty=P,
        has_title=False,
        family={"spouse": [{"id": 4, "name": "Sif"}]},
        female=False,
    ) == ("kin", "m. Sif")
    # Kin with no spouse -> None
    assert _classify_bio_role(
        char_dynasty="Sleggja",
        player_dynasty=P,
        has_title=False,
        family={"child": [{"id": 9, "name": "X"}]},
        female=True,
    ) == ("kin", None)
    # Consort: outside dynasty with a spouse; gendered label
    assert _classify_bio_role(
        char_dynasty="hvitserk",
        player_dynasty=P,
        has_title=False,
        family={"spouse": [{"id": 3, "name": "Bjorn"}]},
        female=True,
    ) == ("consort", "wife of Bjorn")
    assert _classify_bio_role(
        char_dynasty="hvitserk",
        player_dynasty=P,
        has_title=False,
        family={"primary_spouse": {"id": 3, "name": "Saga"}},
        female=None,
    ) == ("consort", "spouse of Saga")
    # Other: outsider, no spouse
    assert _classify_bio_role(
        char_dynasty="Herse",
        player_dynasty=P,
        has_title=False,
        family={},
        female=False,
    ) == ("other", None)


def test_biographies_endpoint_lists_all_held_titles(api: CampaignHarness) -> None:
    """ck3_chronicler-9ngy: the Biographies overview must surface ALL titles
    held at death (grandest-first), not just the single primary — a
    multi-kingdom ruler reads as more than 'King of <one kingdom>', and the
    custom kingdom is no longer dropped. primary_title stays the head of the
    list for back-compat."""
    import json as _json

    from chronicler.db.repository import insert_biography, upsert_character

    held = [
        {"key": "k_scotland", "name": "Scotland", "tier": "kingdom"},
        {"key": "k_ireland", "name": "Ireland", "tier": "kingdom"},
        {"key": "k_nordreyjar", "name": "Nordreyjar", "tier": "kingdom"},
        {"key": "d_kent", "name": "Kent", "tier": "duchy"},
    ]

    def _seed(s: Session) -> None:
        upsert_character(
            s,
            ck3_id=20,
            first_name="Arnljotur",
            female=False,
            dynasty_name="Sleggja",
            death_date="0947.6.9",
            primary_title_json=_json.dumps(held[0]),
            held_titles_json=_json.dumps(held),
        )
        insert_biography(
            s,
            character_id=20,
            body="Body sentence. " * 12,
            prompt_template_version="biography_v1",
            provider="test:fake",
            generated_at="2026-05-01T00:00:00+00:00",
        )

    api.campaign("held", seed=_seed, overview={"current_player_character_id": 20})
    c = api.client()
    resp = c.get("/api/campaigns/held/biographies")
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["ck3_id"] == 20)
    assert [t["key"] for t in row["held_titles"]] == [
        "k_scotland",
        "k_ireland",
        "k_nordreyjar",
        "d_kent",
    ]
    # primary_title remains the head of the held list (back-compat).
    assert row["primary_title"]["key"] == "k_scotland"


def test_biographies_endpoint_sets_role_and_relation(api: CampaignHarness) -> None:
    """ck3_chronicler-lmex: each entry carries a role + spouse-based
    relation, classified relative to the player's dynasty."""
    import json as _json

    from chronicler.db.repository import insert_biography, upsert_character

    def _seed(s: Session) -> None:
        # Player ruler: Sleggja, holds a title, married Malmfridr.
        upsert_character(
            s,
            ck3_id=20,
            first_name="Arnljotur",
            female=False,
            dynasty_name="Sleggja",
            death_date="0947.6.9",
            primary_title_json=_json.dumps({"key": "k_x", "name": "Realm", "tier": "kingdom"}),
            save_snapshot_json=_json.dumps(
                {"family_data": {"primary_spouse": {"id": 21, "name": "Malmfridr"}}}
            ),
        )
        # Consort: married in from another dynasty.
        upsert_character(
            s,
            ck3_id=21,
            first_name="Malmfridr",
            female=True,
            dynasty_name="Munso",
            death_date="0948.1.1",
            save_snapshot_json=_json.dumps(
                {"family_data": {"primary_spouse": {"id": 20, "name": "Arnljotur"}}}
            ),
        )
        # Kin: Sleggja, no title, married.
        upsert_character(
            s,
            ck3_id=22,
            first_name="Halla",
            female=True,
            dynasty_name="Sleggja",
            death_date="0946.1.1",
            save_snapshot_json=_json.dumps(
                {"family_data": {"spouse": [{"id": 99, "name": "Sigtrygg"}]}}
            ),
        )
        for cid in (20, 21, 22):
            insert_biography(
                s,
                character_id=cid,
                body="Body sentence. " * 12,
                prompt_template_version="biography_v1",
                provider="test:fake",
                generated_at="2026-05-01T00:00:00+00:00",
            )

    api.campaign("rolegroup", seed=_seed, overview={"current_player_character_id": 20})
    c = api.client()
    resp = c.get("/api/campaigns/rolegroup/biographies")
    assert resp.status_code == 200
    by_id = {r["ck3_id"]: r for r in resp.json()}
    assert by_id[20]["role"] == "ruler"
    assert by_id[20]["relation"] == "m. Malmfridr"
    assert by_id[21]["role"] == "consort"
    assert by_id[21]["relation"] == "wife of Arnljotur"
    assert by_id[22]["role"] == "kin"
    assert by_id[22]["relation"] == "m. Sigtrygg"

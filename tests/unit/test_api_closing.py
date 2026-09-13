"""HTTP-level tests for the chronicler closing-chronicle API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db.registry import Campaign
from chronicler.db.repository import (
    insert_biography,
    upsert_character,
)
from tests.helpers.api import CampaignHarness
from tests.helpers.providers import RecordingProvider


def test_closing_chronicle_blurb_is_first_paragraph(api: CampaignHarness) -> None:
    """6e1: when a campaign has a closing_chronicle, the blurb is just
    the first paragraph (split on double-newline)."""
    api.campaign("wess")

    # Persist a chronicle directly via the registry helper.
    import sqlite3

    body = "First came Eadric the Steadfast.\n\nThen came his line."
    with sqlite3.connect(api.registry) as conn:
        conn.execute(
            "UPDATE campaigns SET closing_chronicle = ?, "
            "closing_chronicle_generated_at = ?, archived = 1 "
            "WHERE name = ?",
            (body, "2026-05-03T10:00:00+00:00", "wess"),
        )
        conn.commit()

    c = api.client()
    resp = c.get("/api/campaigns?include_archived=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["closing_chronicle_blurb"] == "First came Eadric the Steadfast."


# --- ck3_chronicler-z7l: closing-chronicle endpoints ---


def _seed_closing_characters(s: Session) -> None:
    """Seed the 3 tracked characters (with biographies) of the ``closing``
    campaign, ready to be sealed."""
    for cid, name, nickname, bio_body in (
        (10, "Alfred", "the Founder", "Alfred united the dynasty in 867."),
        (11, "Eadgyth", None, "Eadgyth ruled in council, holding the realm."),
        (12, "Cuthbert", "the Just", "Cuthbert closed out a long peace."),
    ):
        upsert_character(
            s,
            ck3_id=cid,
            first_name=name,
            nickname=nickname,
            culture="anglo_saxon",
            faith="catholic",
            birth_date=f"{800 + cid}.1.1",
            death_date=f"{870 + cid}.6.1",
        )
        insert_biography(
            s,
            character_id=cid,
            body=bio_body,
            prompt_template_version="biography_v2",
            provider="ollama:test",
            generated_at="2026-05-01T00:00:00+00:00",
        )


def _register_closing_campaign(api: CampaignHarness) -> Campaign:
    """Register the standard ``closing`` campaign — 3 tracked lives with
    biographies, founding dynasty Wessex — into the harness registry."""
    return api.campaign(
        "closing",
        seed=_seed_closing_characters,
        create_kwargs={"founding_dynasty_name": "Wessex"},
        tracked=[(10, "player"), (11, "heir"), (12, "heir")],
    )


@dataclass
class _RecordingFakeProvider(RecordingProvider):
    """Closing-chronicle double — captures the last request (``last_request``)
    and persists ``name`` as the provider tag (cs1o). A preconfigured
    ``tests.helpers.providers.RecordingProvider`` (audit L37)."""

    provider_name: str = "fake-closing:v1"
    response_text: str = "Wessex endures."
    input_tokens: int | None = 100
    output_tokens: int | None = 200
    cache_read_tokens: int | None = 60
    cache_write_tokens: int | None = 30
    cost_usd: float | None = 0.42


@pytest.fixture
def closing_provider() -> _RecordingFakeProvider:
    return _RecordingFakeProvider()


@pytest.fixture
def closing_client(
    api: CampaignHarness,
    closing_provider: _RecordingFakeProvider,
) -> TestClient:
    _register_closing_campaign(api)
    return api.client(provider=closing_provider)


def test_closing_chronicle_returns_null_before_complete(closing_client: TestClient) -> None:
    """F-60: 200 + null instead of 404 for the not-yet-sealed state — see
    chronicler.api.routes.closing for rationale."""
    resp = closing_client.get("/api/campaigns/closing/closing-chronicle")
    assert resp.status_code == 200
    assert resp.json() is None


def test_complete_generates_persists_and_archives(
    closing_client: TestClient, closing_provider: _RecordingFakeProvider
) -> None:
    resp = closing_client.post("/api/campaigns/closing/complete")
    assert resp.status_code == 200
    data = resp.json()
    assert data["body"] == "Wessex endures."
    assert data["campaign_name"] == "closing"
    assert data["archived"] is True
    assert data["generated_at"]  # ISO timestamp non-empty
    # Provider was called exactly once with the campaign-chronicle prompt
    assert closing_provider.call_count == 1
    req = closing_provider.last_request
    assert req is not None
    assert req.kind == "chronicle_export"
    assert req.prompt_version == "campaign_chronicle_v1"
    # User prompt must include all three biographies' bodies
    assert "Alfred united the dynasty" in req.user_prompt
    assert "Eadgyth ruled in council" in req.user_prompt
    assert "Cuthbert closed out a long peace" in req.user_prompt
    # Header carries the founding dynasty
    assert "Wessex" in req.user_prompt
    # Tracked-count metadata
    assert req.metadata["tracked_count"] == "3"
    # ck3_chronicler-g1y5: closing-chronicle requests now thread
    # campaign_uuid + character_id so ClaudeCodeProvider files the
    # artifact under prose_repo/biographies/<uuid>/closing-chronicle-vN.md
    # instead of the _unscoped/_unknown bucket.
    assert req.metadata["campaign_uuid"]  # non-empty UUID string
    assert req.metadata["character_id"] == "closing-chronicle"


def test_complete_then_get_returns_persisted_chronicle(
    api: CampaignHarness,
    closing_provider: _RecordingFakeProvider,
) -> None:
    """After /complete archives the campaign, fetch it directly via the
    registry to verify the chronicle and timestamp survived persistence."""
    cid = _register_closing_campaign(api).id
    client = api.client(provider=closing_provider)
    resp = client.post("/api/campaigns/closing/complete")
    assert resp.status_code == 200

    from chronicler.db.registry import get_campaign_by_id

    camp = get_campaign_by_id(cid, registry=api.registry)
    assert camp is not None
    assert camp.archived is True
    assert camp.closing_chronicle == "Wessex endures."
    assert camp.closing_chronicle_generated_at  # ISO timestamp present
    # ck3_chronicler-cs1o: the chronicle records who generated it + the
    # cache breakdown + cost so per-provider USD attribution works.
    assert camp.closing_chronicle_provider == "fake-closing:v1"
    assert camp.closing_chronicle_cache_read_tokens == 60
    assert camp.closing_chronicle_cache_write_tokens == 30
    assert camp.closing_chronicle_cost_usd == 0.42


@dataclass
class _ProseAwareClosingProvider(_RecordingFakeProvider):
    """Closing double that declares a prose dir, like real transports."""

    prose_dir: Path | None = None

    @property
    def prose_repo_path(self) -> Path | None:
        return self.prose_dir


def test_complete_assembles_the_chronicle_export_voice_into_the_system_prompt(
    api: CampaignHarness, tmp_path: Path
) -> None:
    """Issue #19: the closing chronicle goes through the same
    provider-neutral assembly as biographies, so it carries the register
    plus voice/chronicle-export.md. It used to carry the in-repo
    campaign_chronicle_v1.md template, which every provider discarded."""
    prose = tmp_path / "prose"
    (prose / "voice").mkdir(parents=True)
    (prose / "CLAUDE.md").write_text("(the register)", encoding="utf-8")
    (prose / "voice" / "chronicle-export.md").write_text("(export rules)", encoding="utf-8")

    _register_closing_campaign(api)
    provider = _ProseAwareClosingProvider(prose_dir=prose)
    resp = api.client(provider=provider).post("/api/campaigns/closing/complete")

    assert resp.status_code == 200
    req = provider.last_request
    assert req is not None
    assert req.system_prompt == "(the register)\n\n(export rules)"


def test_complete_502_when_the_prose_dir_is_missing(api: CampaignHarness, tmp_path: Path) -> None:
    """A register-less closing chronicle must fail, not persist generic
    prose as a sealed campaign chronicle."""
    _register_closing_campaign(api)
    provider = _ProseAwareClosingProvider(prose_dir=tmp_path / "absent")

    resp = api.client(provider=provider).post("/api/campaigns/closing/complete")

    assert resp.status_code == 502
    assert "chronicler init-prose" in resp.json()["detail"]
    assert provider.call_count == 0


def test_complete_503_when_no_provider(api: CampaignHarness) -> None:
    _register_closing_campaign(api)
    client = api.client()  # no provider injected
    resp = client.post("/api/campaigns/closing/complete")
    assert resp.status_code == 503
    assert "no narrative provider" in resp.json()["detail"]


def test_complete_502_when_provider_raises(api: CampaignHarness) -> None:
    """Provider exception → 502, campaign NOT archived (safe to retry)."""
    cid = _register_closing_campaign(api).id

    class _BoomProvider(_RecordingFakeProvider):
        async def generate(self, req):
            raise RuntimeError("upstream gateway down")

    client = api.client(provider=_BoomProvider())
    resp = client.post("/api/campaigns/closing/complete")
    assert resp.status_code == 502
    assert "upstream gateway down" in resp.json()["detail"]
    # Campaign still active — closing didn't commit
    from chronicler.db.registry import get_campaign_by_id

    camp = get_campaign_by_id(cid, registry=api.registry)
    assert camp is not None
    assert camp.archived is False
    assert camp.closing_chronicle is None


def test_complete_handles_tracked_char_without_biography(
    api: CampaignHarness,
    closing_provider: _RecordingFakeProvider,
) -> None:
    """A tracked character with no biography is still surfaced in the
    prompt (with a placeholder body) so the LLM sees the slot."""
    from chronicler.db.registry import add_tracked_character

    camp = _register_closing_campaign(api)
    # Add a 4th tracked char with no row in characters table at all
    add_tracked_character(camp.id, 999, role="rival", registry=api.registry)
    client = api.client(provider=closing_provider)
    resp = client.post("/api/campaigns/closing/complete")
    assert resp.status_code == 200
    req = closing_provider.last_request
    assert req is not None
    assert "(unknown character 999)" in req.user_prompt
    assert "(no biography was generated for this life)" in req.user_prompt


def test_complete_surfaces_family_relations_and_alive_status(
    api: CampaignHarness,
    closing_provider: _RecordingFakeProvider,
) -> None:
    """ck3_chronicler-2wv: closing prompt must declare each life's
    alive/dead status AND the resolved family relationships between
    tracked lives. The v0.7 smoke fabricated 'Sigrid is Erik's
    granddaughter and successor' from spouse pairs because the prompt
    gave no relationship context. Surfacing 'Spouse: Sigrid (id 36344)
    [also a tracked life]' kills that failure mode at the source."""
    erik_snapshot = json.dumps(
        {
            "family_data": {
                "primary_spouse": {"id": 36344, "name": "Sigrid"},
                "spouse": [{"id": 36344, "name": "Sigrid"}],
                "child": [
                    {"id": 65624, "name": "Asbjorn"},
                    {"id": 33559598, "name": "Ragnhild"},
                ],
            },
        }
    )
    sigrid_snapshot = json.dumps(
        {
            "family_data": {
                "primary_spouse": {"id": 32943, "name": "Erik"},
                "spouse": [{"id": 32943, "name": "Erik"}],
                "child": [
                    {"id": 65624, "name": "Asbjorn"},
                    {"id": 33559598, "name": "Ragnhild"},
                ],
            },
        }
    )

    def _seed_fam(s: Session) -> None:
        upsert_character(
            s,
            ck3_id=32943,
            first_name="Erik",
            nickname="the Heathen",
            birth_date="1031.1.1",
            death_date=None,
            dynasty_name=None,
            house_name="house_munso",
            culture="norse",
            faith="norse_pagan",
            save_snapshot_json=erik_snapshot,
        )
        upsert_character(
            s,
            ck3_id=36344,
            first_name="Sigrid",
            birth_date="1035.1.1",
            death_date=None,
            house_name="house_munso",
            culture="norse",
            faith="norse_pagan",
            save_snapshot_json=sigrid_snapshot,
        )
        # Departed character so we exercise the 'alive vs departed' partition
        upsert_character(
            s,
            ck3_id=70000,
            first_name="Bjorn",
            birth_date="1010.1.1",
            death_date="1062.5.5",
            culture="norse",
            faith="norse_pagan",
        )
        for cid in (32943, 36344, 70000):
            insert_biography(
                s,
                character_id=cid,
                body=f"bio for {cid}",
                prompt_template_version="biography_v2",
                provider="ollama:test",
                generated_at="2026-05-01T00:00:00+00:00",
            )

    api.campaign(
        "fam",
        seed=_seed_fam,
        tracked=[(32943, "player"), (36344, "spouse"), (70000, "ancestor")],
    )
    client = api.client(provider=closing_provider)
    resp = client.post("/api/campaigns/fam/complete")
    assert resp.status_code == 200

    req = closing_provider.last_request
    assert req is not None
    prompt = req.user_prompt
    # Roster partitions alive vs departed
    assert "Alive at campaign end:" in prompt
    assert "Departed before campaign end:" in prompt
    assert "Bjorn" in prompt and "died 1062.5.5" in prompt
    # Per-character relationship block resolves the spouse with the
    # tracked-life flag so the LLM cannot mistake them for a descendant.
    assert "Spouse: Sigrid (id 36344) [also a tracked life]" in prompt
    assert "Spouse: Erik (id 32943) [also a tracked life]" in prompt
    # Children listed inline
    assert "Children:" in prompt
    assert "Asbjorn" in prompt and "Ragnhild" in prompt
    # Living life-span line is explicit (no "?" placeholder).
    assert "alive at campaign end" in prompt
    # House surfaces (45i fix means engine slug populated even when
    # dynasty_name remained NULL).
    assert "house house_munso" in prompt

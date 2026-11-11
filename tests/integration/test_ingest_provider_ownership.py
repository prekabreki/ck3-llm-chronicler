"""Issue #25: ingest loops must not close the narrative provider they are handed.

``chronicler dev`` builds one provider and shares it between the app lifespan
and every per-campaign ingest loop.  A loop's teardown used to call
``biography_provider.aclose()``, closing the shared ``httpx.AsyncClient``
out from under the other campaign.  Ownership now sits with the constructor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chronicler.db import Base, make_engine_for_path
from chronicler.narrative.provider import NarrativeProvider, NarrativeRequest, NarrativeResponse
from chronicler.save.ingest import run_save_ingest
from chronicler.save.parse import SaveSnapshot
from tests.helpers.ingest import _char, _no_op_watch, _patch_parse_save


class _CloseTrackingProvider(NarrativeProvider):
    """Records ``aclose`` calls and refuses to generate once closed."""

    def __init__(self) -> None:
        self.close_calls = 0
        self.closed = False

    @property
    def name(self) -> str:
        return "close-tracking:v1"

    async def aclose(self) -> None:
        self.close_calls += 1
        self.closed = True

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        if self.closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return NarrativeResponse(
            text="still open",
            model=self.name,
            input_tokens=None,
            output_tokens=None,
            latency_ms=0,
        )


@pytest.mark.asyncio
async def test_run_save_ingest_does_not_close_shared_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    (save_dir / "autosave.ck3").write_bytes(b"opaque save content")

    db_path = tmp_path / "campaign.db"
    registry_path = tmp_path / "registry.db"

    engine = make_engine_for_path(db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    snapshot = SaveSnapshot(
        playthrough_id="uuid-provider-ownership",
        ck3_version="1.19.0.4",
        bookmark_date="1066.9.15",
        current_date="1066.9.15",
        player_character_id=1234,
        characters={1234: _char(1234, is_dead=False)},
    )
    _patch_parse_save(monkeypatch, lambda path: ({}, snapshot))
    monkeypatch.setattr("chronicler.save.ingest.watch_saves", _no_op_watch)

    provider = _CloseTrackingProvider()
    stop_event = asyncio.Event()
    stop_event.set()

    await run_save_ingest(
        save_dir=save_dir,
        db_path=db_path,
        campaign_id="test-campaign-provider-ownership",
        registry_path=registry_path,
        biography_provider=provider,
        stop_event=stop_event,
    )

    assert provider.close_calls == 0
    response = await provider.generate(
        NarrativeRequest(
            kind="biography",
            prompt_version="v1",
            system_prompt="",
            user_prompt="",
        )
    )
    assert response.text == "still open"

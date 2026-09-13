"""Tests for the provider-neutral system-prompt assembly (issue #19).

Before this, each transport built its own system prompt: the anthropic
transport inlined ``CLAUDE.md`` + ``voice/<kind>.md`` as two system
blocks, while the claude-code transport passed only ``CLAUDE.md`` via
``--system-prompt-file`` — so the default route generated woven
biographies WITHOUT ``voice/biography-woven.md`` from 27ov.14 onward.
:func:`assemble_system_prompt` is the single step both request-building
call sites now use; providers transport its output verbatim.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import insert_event_idempotent, upsert_character
from chronicler.narrative.pipeline import generate_biography
from chronicler.narrative.prose_io import assemble_system_prompt
from tests.helpers.providers import FakeProvider


def _prose_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text("(the register)", encoding="utf-8")
    voice = tmp_path / "voice"
    voice.mkdir()
    (voice / "biography.md").write_text("(plain voice rules)", encoding="utf-8")
    (voice / "biography-woven.md").write_text("(woven voice rules)", encoding="utf-8")
    (voice / "chronicle-export.md").write_text("(export voice rules)", encoding="utf-8")
    return tmp_path


def test_assembles_register_then_kind_voice(tmp_path: Path) -> None:
    """The register comes first (it reshapes the role), the kind's voice
    file second (it constrains this specific call)."""
    prose = _prose_repo(tmp_path)

    assembled = assemble_system_prompt(prose_repo=prose, kind="biography_woven")

    assert assembled == "(the register)\n\n(woven voice rules)"


def test_missing_prose_dir_fails_loud_naming_the_recovery_paths(tmp_path: Path) -> None:
    """A missing prose dir must raise, not yield a register-less prompt.

    The AnthropicProvider used to fall back to an empty system block and
    persist generic-assistant prose as a valid biography; the error has
    to tell the user how to fix it.
    """
    with pytest.raises(RuntimeError) as exc:
        assemble_system_prompt(prose_repo=tmp_path / "nope", kind="biography")

    message = str(exc.value)
    assert "chronicler init-prose" in message
    assert "CHRONICLER_PROSE_REPO_PATH" in message


def test_missing_register_fails_loud(tmp_path: Path) -> None:
    """A prose dir that exists but carries no CLAUDE.md is just as
    register-less as a missing one."""
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "biography.md").write_text("(voice)", encoding="utf-8")

    with pytest.raises(RuntimeError, match="CLAUDE.md"):
        assemble_system_prompt(prose_repo=tmp_path, kind="biography")


def test_missing_voice_file_for_kind_fails_loud(tmp_path: Path) -> None:
    """The defect this issue fixes: woven biographies generated without
    voice/biography-woven.md. Silently omitting it is what produced
    scene-setter openers on the woven route for months — so a missing
    voice file is an error, never a skipped block."""
    prose = _prose_repo(tmp_path)
    (prose / "voice" / "biography-woven.md").unlink()

    with pytest.raises(RuntimeError, match="biography-woven.md"):
        assemble_system_prompt(prose_repo=prose, kind="biography_woven")


def test_kind_without_a_registered_voice_file_uses_the_register_alone(tmp_path: Path) -> None:
    """Unknown kinds (test fixtures, future kinds with no dedicated
    rules) have no mapped voice file — they get the register and no
    error, matching voice_file_for_kind's existing contract."""
    prose = _prose_repo(tmp_path)

    assert assemble_system_prompt(prose_repo=prose, kind="some_future_kind") == "(the register)"


def test_no_prose_dir_declared_yields_an_empty_prompt(tmp_path: Path) -> None:
    """Test doubles declare no prose dir (the ABC's default). They never
    reach a real transport, so there is nothing to fail loud about."""
    assert assemble_system_prompt(prose_repo=None, kind="biography") == ""


# --- The assembly reaches the request the transports actually receive ---


@dataclass
class ProseAwareProvider(FakeProvider):
    """A double that declares a prose dir, the way real transports do."""

    prose_dir: Path | None = None

    @property
    def prose_repo_path(self) -> Path | None:
        return self.prose_dir


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine_for_path(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _seed(factory: sessionmaker[Session], ck3_id: int = 4250) -> None:
    with factory() as session:
        upsert_character(session, ck3_id=ck3_id, first_name="Erik", culture="norse")
        insert_event_idempotent(
            session,
            schema_version=1,
            event_type="death",
            event_date="1st of January, 1066 AD",
            event_date_iso="1066-01-01",
            wall_clock_at="2026-05-01T12:00:00+00:00",
            primary_character_id=ck3_id,
            payload_json='{"v":1,"t":"death","d":"x","c":4250,"p":{}}',
            raw_line="line-0",
        )
        session.commit()


@pytest.mark.asyncio
async def test_pipeline_sets_the_assembled_prompt_on_the_request(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    """The transports ship ``req.system_prompt`` verbatim, so this is
    what reaches the model. It must carry the register AND the kind's
    voice file — the woven route lost the latter for months because
    each transport assembled its own."""
    prose = _prose_repo(tmp_path / "prose")
    _seed(factory)
    provider = ProseAwareProvider(prose_dir=prose)

    outcome = await generate_biography(4250, factory=factory, provider=provider)

    assert outcome.error is None
    assert provider.seen_requests[0].system_prompt == "(the register)\n\n(plain voice rules)"


@pytest.mark.asyncio
async def test_pipeline_reports_assembly_failure_as_a_failed_generation(
    tmp_path: Path, factory: sessionmaker[Session]
) -> None:
    """A missing prose dir must land in ``GenerationOutcome.error`` — the
    scheduler turns that into a failed queue item the user can see.
    Raising out of generate_biography instead would blow past the
    ingest layer's never-block contract."""
    _seed(factory)
    provider = ProseAwareProvider(prose_dir=tmp_path / "absent")

    outcome = await generate_biography(4250, factory=factory, provider=provider)

    assert outcome.biography_id is None
    assert outcome.error is not None
    assert "chronicler init-prose" in outcome.error
    # The provider was never called — no register, no generation.
    assert provider.seen_requests == []

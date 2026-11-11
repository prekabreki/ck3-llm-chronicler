"""Issue #30: the shared generation runner owns every prose-repo artifact.

These tests pin the sequence the transports used to duplicate: DB-derived
version allocation, briefing write, generation, biography + footer, git
commit, then the DB insert — all inside one non-raising error net.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chronicler.narrative.model_resolution import estimate_prompt_tokens
from chronicler.narrative.prose_io import BRIEFING_WRAPPER_INSTRUCTION
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
)
from chronicler.narrative.runner import ArtifactDescriptor, run_generation


class _FakeTransport(NarrativeProvider):
    def __init__(
        self,
        prose_repo: Path | None,
        *,
        text: str = "A recorded life.",
        fail_with: Exception | None = None,
        delay: float = 0.0,
        max_concurrent: int = 4,
    ) -> None:
        self._prose_repo = prose_repo
        self.text = text
        self.fail_with = fail_with
        self.delay = delay
        self._max_concurrent = max_concurrent
        self.requests: list[NarrativeRequest] = []
        self.inflight = 0
        self.peak = 0

    @property
    def name(self) -> str:
        return "fake:test"

    @property
    def prose_repo_path(self) -> Path | None:
        return self._prose_repo

    @property
    def attribution_label(self) -> str:
        return "Test Agent"

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        self.requests.append(req)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_with is not None:
                raise self.fail_with
            return NarrativeResponse(
                text=self.text,
                model="fake-model",
                input_tokens=123,
                output_tokens=45,
                latency_ms=1,
            )
        finally:
            self.inflight -= 1


@pytest.fixture
def prose(tmp_path: Path) -> Path:
    (tmp_path / "CLAUDE.md").write_text("(register)", encoding="utf-8")
    return tmp_path


def _artifact() -> ArtifactDescriptor:
    return ArtifactDescriptor(
        directory="camp-1",
        basename="42",
        subject="biography: 42",
    )


@pytest.mark.asyncio
async def test_runner_owns_the_full_artifact_sequence(prose: Path, monkeypatch) -> None:
    recorded: list[list[str]] = []

    async def commit(**kwargs):
        recorded.append(["commit", kwargs["rel_bio"]])

    monkeypatch.setattr("chronicler.narrative.runner.git_commit_biography", commit)
    provider = _FakeTransport(prose)
    events: list[str] = []

    def persist(response: NarrativeResponse, version: int) -> int:
        events.append("persist")
        return 77

    outcome = await run_generation(
        provider=provider,
        kind="biography_woven",
        prompt_version="biography_v5",
        system_prompt="(register)\n\n(voice)",
        user_prompt="Recorded events: (none)",
        metadata={"character_id": "42", "campaign_uuid": "camp-1"},
        artifact=_artifact(),
        allocate_version=lambda: 3,
        persist=persist,
    )

    assert outcome.error is None
    assert outcome.version == 3
    assert outcome.persisted == 77
    assert events == ["persist"]
    assert recorded == [["commit", "biographies/camp-1/42-v3.md"]]

    brief = prose / "briefings" / "camp-1" / "42-v3.md"
    bio = prose / "biographies" / "camp-1" / "42-v3.md"
    assert brief.is_file()
    assert bio.is_file()
    assert "# Briefing: character 42" in brief.read_text(encoding="utf-8")
    on_disk = bio.read_text(encoding="utf-8")
    assert on_disk.startswith("A recorded life.\n")
    assert "Set down by Test Agent" in on_disk
    assert "123 in / 45 out" in on_disk

    # The transport got the final user turn and the one estimate.
    (request,) = provider.requests
    expected_user = BRIEFING_WRAPPER_INSTRUCTION + brief.read_text(encoding="utf-8")
    assert request.user_prompt == expected_user
    assert request.system_prompt == "(register)\n\n(voice)"
    assert request.kind == "biography_woven"
    assert request.prompt_tokens_estimate == estimate_prompt_tokens(
        request.system_prompt + request.user_prompt
    )
    # The response body stays bare prose (no footer double-stored).
    assert outcome.response is not None
    assert outcome.response.text == "A recorded life."


@pytest.mark.asyncio
async def test_generation_failure_keeps_the_briefing_and_writes_nothing_else(
    prose: Path, monkeypatch
) -> None:
    async def commit(**kwargs):  # pragma: no cover - must not run
        raise AssertionError("no commit on a generation failure")

    monkeypatch.setattr("chronicler.narrative.runner.git_commit_biography", commit)
    provider = _FakeTransport(prose, fail_with=RuntimeError("connection refused"))

    def persist(response, version):  # pragma: no cover - must not run
        raise AssertionError("no DB insert on a generation failure")

    outcome = await run_generation(
        provider=provider,
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="(register)",
        user_prompt="body",
        metadata={"character_id": "42", "campaign_uuid": "camp-1"},
        artifact=_artifact(),
        allocate_version=lambda: 1,
        persist=persist,
    )

    assert outcome.error is not None and "RuntimeError" in outcome.error
    assert outcome.response is None
    assert (prose / "briefings" / "camp-1" / "42-v1.md").is_file()
    assert not (prose / "biographies" / "camp-1" / "42-v1.md").exists()


@pytest.mark.asyncio
async def test_failed_db_insert_rolls_the_committed_biography_back(
    prose: Path, monkeypatch
) -> None:
    """Danger zone #30: the insert runs after the commit. A failed insert
    must not leave a file on disk under the same version, or the next
    retry would allocate that version again and overwrite it."""
    recorded: list[dict] = []

    async def commit(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("chronicler.narrative.runner.git_commit_biography", commit)
    provider = _FakeTransport(prose)

    def persist(response, version):
        raise RuntimeError("insert failed")

    outcome = await run_generation(
        provider=provider,
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="(register)",
        user_prompt="body",
        metadata={"character_id": "42", "campaign_uuid": "camp-1"},
        artifact=_artifact(),
        allocate_version=lambda: 1,
        persist=persist,
    )

    assert outcome.error is not None and "insert failed" in outcome.error
    assert not (prose / "biographies" / "camp-1" / "42-v1.md").exists()
    # First commit added the pair; the rollback commit records the removal.
    assert len(recorded) == 2
    assert recorded[-1]["rel_bio"] == "biographies/camp-1/42-v1.md"


@pytest.mark.asyncio
async def test_empty_system_prompt_and_missing_prose_dir_refuse_before_generation(
    prose: Path, tmp_path: Path
) -> None:
    provider = _FakeTransport(prose)
    outcome = await run_generation(
        provider=provider,
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="   ",
        user_prompt="body",
        metadata={},
        artifact=_artifact(),
        allocate_version=lambda: 1,
    )
    assert outcome.error is not None and "system prompt" in outcome.error
    assert provider.requests == []

    absent_provider = _FakeTransport(tmp_path / "absent")
    outcome = await run_generation(
        provider=absent_provider,
        kind="biography",
        prompt_version="biography_v1",
        system_prompt="(register)",
        user_prompt="body",
        metadata={},
        artifact=_artifact(),
        allocate_version=lambda: 1,
    )
    assert outcome.error is not None and "chronicler init-prose" in outcome.error
    assert absent_provider.requests == []


@pytest.mark.asyncio
async def test_runner_acquires_the_provider_permit(prose: Path, monkeypatch) -> None:
    """Issue #30: concurrent direct callers are bounded by the provider's
    one shared limiter, not by whichever caller remembered to acquire it."""

    async def commit(**kwargs):
        return None

    monkeypatch.setattr("chronicler.narrative.runner.git_commit_biography", commit)
    provider = _FakeTransport(prose, delay=0.05, max_concurrent=1)

    async def one() -> object:
        return await run_generation(
            provider=provider,
            kind="biography",
            prompt_version="biography_v1",
            system_prompt="(register)",
            user_prompt="body",
            metadata={"character_id": "42", "campaign_uuid": "camp-1"},
            artifact=_artifact(),
            allocate_version=lambda: 1,
        )

    results = await asyncio.gather(one(), one(), one())

    assert all(result.error is None for result in results)
    assert provider.peak == 1


@pytest.mark.asyncio
async def test_closing_artifact_descriptor_drives_the_filename(prose: Path, monkeypatch) -> None:
    """The closing chronicle names its artifact instead of borrowing a
    character-id sentinel for path derivation."""
    recorded: list[dict] = []

    async def commit(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("chronicler.narrative.runner.git_commit_biography", commit)
    provider = _FakeTransport(prose)

    artifact = ArtifactDescriptor(
        directory="campaign-uuid",
        basename="closing-chronicle",
        subject="closing chronicle",
    )
    outcome = await run_generation(
        provider=provider,
        kind="chronicle_export",
        prompt_version="campaign_chronicle_v1",
        system_prompt="(register)",
        user_prompt="the dynasty",
        metadata={"character_id": "closing-chronicle"},
        artifact=artifact,
    )

    assert outcome.error is None
    assert (prose / "biographies" / "campaign-uuid" / "closing-chronicle-v1.md").is_file()
    assert recorded[0]["subject"] == "closing chronicle"
    assert recorded[0]["character_id"] == "closing-chronicle"

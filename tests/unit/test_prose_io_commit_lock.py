"""Concurrency contract for the prose-repo auto-commit (27ov.44 / M-N6).

The scheduler runs up to two generations concurrently; when both finish
near-simultaneously their git add/diff/commit sequences used to
interleave in the same prose repo, and the index.lock loser was
swallowed by the best-effort contract — silent commit loss in what is
"the versioned cross-machine record".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chronicler.narrative import prose_io


@pytest.mark.asyncio
async def test_concurrent_commits_serialize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three git_commit_biography calls in flight at once must run
    their git subprocess sequences strictly one-at-a-time."""
    active = 0
    max_active = 0

    async def _fake_run_git(*, prose_repo: Path, argv, label: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        # add/commit succeed (rc=0); diff reports staged changes (rc=1)
        # so the commit step actually runs.
        return (1 if label == "diff" else 0), b""

    monkeypatch.setattr(prose_io, "_run_git", _fake_run_git)

    async def _one(i: int) -> None:
        await prose_io.git_commit_biography(
            prose_repo=tmp_path,
            rel_brief=f"briefs/{i}.md",
            rel_bio=f"bios/{i}.md",
            character_id=str(i),
            campaign_uuid="test-uuid",
            version=1,
            model="test-model",
        )

    await asyncio.gather(_one(1), _one(2), _one(3))
    assert max_active == 1, "git bodies overlapped — index.lock race (M-N6) is back"

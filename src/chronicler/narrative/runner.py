"""Issue #30: one generation runner owning the prose-repo artifacts.

Before this module each transport repeated the same prose-repo side
effects around the model call: version allocation from the directory
scan, briefing write, empty-prose refusal, biography write, attribution
footer, git commit and finally the DB insert in the orchestrator. The
three copies drifted, and the file version (directory scan) and DB
version (``max(Biography.version)``) could disagree.

The runner is now the only place that sequence exists. Transports are
pure generation calls: system prompt in, user content in, kind in, text
plus usage out. The runner:

1. allocates the version from the DB (the caller supplies the closure;
   the closing chronicle uses a fixed single-artifact version),
2. writes the briefing (kept on disk when generation fails, #23),
3. calls the transport under the provider's shared concurrency permit,
4. writes the biography + attribution footer,
5. git-commits the pair (best-effort, serialised by prose_io's lock),
6. persists the row.

All of it sits inside one error net that returns :class:`RunnerOutcome`
instead of raising.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chronicler.narrative.model_resolution import estimate_prompt_tokens
from chronicler.narrative.prose_io import (
    BRIEFING_WRAPPER_INSTRUCTION,
    _missing_prose_message,
    format_attribution_footer,
    git_commit_biography,
    render_briefing_markdown,
)
from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
    PromptKind,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """Where a generation's prose-repo artifacts live and what git says.

    ``directory`` is the per-campaign bucket, ``basename`` the per-life
    artifact stem. ``subject`` is the commit-message subject (the runner
    appends ``v<version>`` plus the campaign/model details). Explicit so
    the closing chronicle no longer masquerades as a character id.
    """

    directory: str
    basename: str
    subject: str


@dataclass(frozen=True, slots=True)
class RunnerOutcome:
    """What the runner produced, or why it failed. Never raised."""

    response: NarrativeResponse | None
    version: int | None
    persisted: Any
    error: str | None


def _default_version() -> int:
    """Single-artifact domains (the closing chronicle) have one version."""
    return 1


async def run_generation(
    *,
    provider: NarrativeProvider,
    kind: PromptKind,
    prompt_version: str,
    system_prompt: str,
    user_prompt: str,
    metadata: dict[str, str],
    artifact: ArtifactDescriptor,
    allocate_version: Callable[[], int] | None = None,
    persist: Callable[[NarrativeResponse, int], Any] | None = None,
) -> RunnerOutcome:
    """Run one generation and all of its prose-repo side effects.

    Every failure — missing prose dir, empty system prompt, transport
    error, empty prose, write/commit trouble, DB insert — is caught and
    returned as ``RunnerOutcome.error``. The briefing is intentionally
    left on disk when generation fails, so the failed call still has a
    diagnostic artifact (#23).
    """
    prose_repo = provider.prose_repo_path
    version: int | None = None
    bio_path: Path | None = None
    brief_path: Path | None = None
    rel_brief: str | None = None
    rel_bio: str | None = None
    model: str | None = None

    try:
        # Issue #19/#30: both refusals live here now and run before any
        # model call. They used to be duplicated in every transport.
        if prose_repo is not None and not prose_repo.is_dir():
            raise RuntimeError(_missing_prose_message(f"prose repo not found at {prose_repo}"))
        # A provider that declares no prose dir is a test double or a
        # deliberately register-less one-off; real transports always set
        # one, and for those an empty prompt is the silent-failure case.
        if prose_repo is not None and not system_prompt.strip():
            raise RuntimeError(
                "refusing to generate with an empty system prompt: the "
                "chronicler register must be assembled from the prose dir "
                "before the request reaches a transport (see "
                "chronicler.narrative.prose_io.assemble_system_prompt)"
            )

        version = (allocate_version or _default_version)()
        if not isinstance(version, int) or version < 1:
            raise RuntimeError(f"invalid artifact version {version!r}")

        # The on-disk briefing is the *unwrapped* markdown; the wrapper
        # is part of the user turn only (byte-identical to the old
        # per-transport output).
        briefing_text = render_briefing_markdown(
            NarrativeRequest(
                kind=kind,
                prompt_version=prompt_version,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                metadata=metadata,
            )
        )

        if prose_repo is not None:
            brief_dir = prose_repo / "briefings" / artifact.directory
            bio_dir = prose_repo / "biographies" / artifact.directory
            brief_dir.mkdir(parents=True, exist_ok=True)
            bio_dir.mkdir(parents=True, exist_ok=True)
            brief_path = brief_dir / f"{artifact.basename}-v{version}.md"
            bio_path = bio_dir / f"{artifact.basename}-v{version}.md"
            brief_path.write_text(briefing_text, encoding="utf-8")
            rel_brief = brief_path.relative_to(prose_repo).as_posix()
            rel_bio = bio_path.relative_to(prose_repo).as_posix()

        user_content = BRIEFING_WRAPPER_INSTRUCTION + briefing_text
        # Issue #30: computed once, from the exact system + user text the
        # transport will send. The transports no longer each re-estimate.
        estimate = estimate_prompt_tokens(system_prompt + user_content)
        transport_request = NarrativeRequest(
            kind=kind,
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            user_prompt=user_content,
            metadata=metadata,
            prompt_tokens_estimate=estimate,
        )

        async with provider.generation_permit():
            response = await provider.generate(transport_request)

        if not response.text.strip():
            raise RuntimeError(
                "generation completed but returned no prose for "
                f"kind={kind} campaign={artifact.directory} "
                f"artifact={artifact.basename}"
            )

        model = response.model
        if bio_path is not None:
            bio_path.write_text(response.text + "\n", encoding="utf-8")
            footer = format_attribution_footer(
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                agent=provider.attribution_label,
            )
            try:
                with bio_path.open("a", encoding="utf-8") as fh:
                    fh.write(footer)
            except OSError:
                log.exception(
                    "runner: failed to append attribution footer to %s; "
                    "biography body is unaffected",
                    bio_path,
                )

            assert rel_brief is not None and rel_bio is not None
            await git_commit_biography(
                prose_repo=prose_repo,
                rel_brief=rel_brief,
                rel_bio=rel_bio,
                character_id=artifact.basename,
                campaign_uuid=artifact.directory,
                version=version,
                model=response.model,
                subject=artifact.subject,
            )

        persisted = persist(response, version) if persist is not None else None
        return RunnerOutcome(
            response=response,
            version=version,
            persisted=persisted,
            error=None,
        )
    except Exception as e:
        log.exception(
            "runner: generation failed for kind=%s campaign=%s artifact=%s",
            kind,
            artifact.directory,
            artifact.basename,
        )
        # Danger zone (#30): the DB insert runs after the commit. If it
        # fails, the file must not remain on disk under this version or
        # the next retry would allocate the same version again and
        # overwrite what git already recorded. Remove it and commit the
        # removal, best-effort.
        if bio_path is not None and bio_path.exists():
            try:
                bio_path.unlink()
            except OSError:
                log.exception("runner: failed to roll back %s after failure", bio_path)
            if (
                prose_repo is not None
                and rel_brief is not None
                and rel_bio is not None
                and model is not None
                and version is not None
            ):
                await git_commit_biography(
                    prose_repo=prose_repo,
                    rel_brief=rel_brief,
                    rel_bio=rel_bio,
                    character_id=artifact.basename,
                    campaign_uuid=artifact.directory,
                    version=version,
                    model=model,
                    subject=artifact.subject,
                )
        return RunnerOutcome(
            response=None,
            version=version,
            persisted=None,
            error=f"{type(e).__name__}: {e}",
        )

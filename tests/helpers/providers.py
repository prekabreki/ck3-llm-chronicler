"""Shared narrative-provider test doubles (ck3_chronicler-27ov.82 / audit L37).

Before this, each test module grew its own near-duplicate recording
provider — ``CountingProvider`` (test_scheduler), ``FakeProvider``
(test_pipeline), ``_RecordingFakeProvider`` (test_api) and ``_FakeProvider``
(test_save_ingest / test_tailer_to_db) — and sibling modules imported them
across file boundaries (``from tests.unit.test_scheduler import
CountingProvider``). That coupling would break when the test_api /
test_save_ingest monoliths are split (M-T1 / M-T7). This module is the
single configurable double they all collapse to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from chronicler.narrative.provider import (
    NarrativeProvider,
    NarrativeRequest,
    NarrativeResponse,
)


def _char_id(req: NarrativeRequest) -> str:
    return req.metadata.get("character_id", "-1")


def _biography_for(req: NarrativeRequest) -> str:
    return f"biography for {int(_char_id(req))}"


@dataclass
class RecordingProvider(NarrativeProvider):
    """Configurable NarrativeProvider double — records calls, returns canned responses.

    Covers every recording double in the suite: call-counting
    (``delay`` / ``fail_with`` / ``.calls``), prompt-capture
    (``seen_requests``) and per-kind attribution (``tag_kind``). The
    request is recorded at the *start* of :meth:`generate`, so a call that
    then sleeps or raises is still counted — the CountingProvider contract
    the scheduler tests rely on.
    """

    provider_name: str = "recording:v1"
    #: Literal text, or a callable taking the request (e.g. to template on
    #: ``character_id``).
    response_text: str | Callable[[NarrativeRequest], str] = "A recorded life."
    input_tokens: int | None = 100
    output_tokens: int | None = 50
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 5
    delay: float = 0.0
    fail_with: Exception | None = None
    #: When True, ``name_for_kind`` returns ``f"{name}:{kind}"`` (the cs1o
    #: per-kind DB attribution path) instead of the base default (``name``).
    tag_kind: bool = False

    seen_requests: list[NarrativeRequest] = field(default_factory=list)
    #: ``character_id`` of each recorded call (CountingProvider contract). A
    #: real mutable list, not a derived view — subclasses that override
    #: ``generate`` append to it directly (the reorder tests' BlockingProvider).
    calls: list[int] = field(default_factory=list)
    name_for_kind_calls: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.provider_name

    def name_for_kind(self, kind: str) -> str:
        self.name_for_kind_calls.append(kind)
        return f"{self.provider_name}:{kind}" if self.tag_kind else self.provider_name

    @property
    def call_count(self) -> int:
        return len(self.seen_requests)

    @property
    def last_request(self) -> NarrativeRequest | None:
        return self.seen_requests[-1] if self.seen_requests else None

    async def generate(self, req: NarrativeRequest) -> NarrativeResponse:
        self.seen_requests.append(req)
        # Non-character requests (e.g. the closing-chronicle sentinel) carry a
        # non-numeric character_id — recorded only in seen_requests, never in
        # the numeric calls list.
        with suppress(TypeError, ValueError):
            self.calls.append(int(_char_id(req)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        text = self.response_text(req) if callable(self.response_text) else self.response_text
        return NarrativeResponse(
            text=text,
            model=self.name,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.latency_ms,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cost_usd=self.cost_usd,
        )


@dataclass
class CountingProvider(RecordingProvider):
    """Scheduler double — counts calls (``.calls``), supports ``delay`` / ``fail_with``."""

    provider_name: str = "counting:v1"
    response_text: str | Callable[[NarrativeRequest], str] = _biography_for
    input_tokens: int | None = 10
    output_tokens: int | None = 20
    latency_ms: int = 1


@dataclass
class FakeProvider(RecordingProvider):
    """Pipeline double — captures prompts (``seen_requests``) + per-kind tags."""

    provider_name: str = "fake-provider:v1"
    response_text: str | Callable[[NarrativeRequest], str] = "A dutiful life cut short."
    input_tokens: int | None = 100
    output_tokens: int | None = 50
    latency_ms: int = 42
    tag_kind: bool = True

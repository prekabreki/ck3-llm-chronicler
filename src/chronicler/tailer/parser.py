"""Parse ``CHRONICLER|``-tagged lines + paired scope dumps from CK3's ``debug.log``.

CK3's ``debug_log = loc_key`` resolves with **no script scope context** —
ROOT, THIS, and saved-via-``save_scope_as`` named scopes are not visible to
the loc engine. Only globals (``GetCurrentDate``, ``GetPlayer``, etc.)
interpolate. We cannot inline the dying character's ID into the line.

The mod works around this by emitting two lines per event: a tagged
``CHRONICLER|...`` line followed immediately by ``debug_log_scopes = yes``
which dumps the engine's scope tree to ``debug.log``. The dump is a
multi-line block that begins with an engine-prefixed line containing the
root scope and continues unprefixed until the next engine-prefixed line::

    [20:54:27][D][effectimpl.cpp:642]: William ... (Internal ID: 32134 ...)!
    Root: William ... (Internal ID: 32134 ...)!

    Saved event targets:
    surviving_consort: Mathilde ... (Internal ID: 33007 ...)!
    dead_character: William ... (Internal ID: 32134 ...)!
    liege_of_dead_character_councillor: Philippe ... (Internal ID: 37409 ...)!

    Saved list targets:
    send_death_management_0002:
    Mathilde ... (Internal ID: 33007 ...)!
    Agathe ... (Internal ID: 39543 ...)!

    [20:54:27][D][army_manager.cpp:1139]: Destroying regiment ...

This module pairs the two streams and extracts both the primary character
(root scope) and named-scope participants (surviving_consort, dead_character,
list members, etc.) which feed into ``events.event_participants``.

:class:`IncrementalParser` is the streaming entry point. ``feed`` accepts
one debug.log line and returns any completed events. State machine:

- See ``CHRONICLER|...`` line: parse envelope (``v``, ``t``, ``d``, payload),
  hold as *pending*. If a pending event already exists, finalise it (its
  scope dump ended just before this new tag).
- First engine-prefixed line after the tag is the dump start: extract root
  Internal ID into ``envelope.c``.
- Subsequent unprefixed lines are dump body. Section markers
  (``Saved event targets:`` / ``Saved list targets:``) switch parser mode;
  named-scope lines (``role: ... (Internal ID: <id>) ... (Character - <id>)!``)
  add ``(role, id)`` to participants; bare list-item lines under an active
  list label add ``(list_name, id)``.
- Second engine-prefixed line ends the dump → finalise + emit.
- Timeout: if more than ``SCOPE_TIMEOUT_LINES`` lines have passed, emit a
  ``missing_scope`` failure so a malformed sequence can't stall the parser.

The contract: ``feed`` never raises. Failures come back as
:class:`ParseFailure` so the caller can quarantine and continue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from chronicler.schema import EventAdapter, EventPayload

# CHRONICLER tag — bare word (no brackets, those would be parsed as data
# functions by the loc engine). Word boundary prevents substring matches.
_CHRONICLER_RE = re.compile(r"(?:^|\W)CHRONICLER\|(.+?)\s*$")
_ENVELOPE_KEYS = frozenset({"v", "t", "d"})  # 'c' comes from scope dump

# Engine prefix: "[HH:MM:SS][D][file.cpp:NNN]:" with three bracketed groups.
# The first one after a CHRONICLER tag is the dump start (root scope line).
# Any subsequent prefix line ends the dump.
_ENGINE_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\[\w+\]\[[^\]]+\]:")

# Internal ID — two formats based on whether the character has a historical
# anchor (real historical figures vs procedurally-generated AI):
#   "(Internal ID: <id> - Historical ID <hist>)"  -- with colon, with hist
#   "(Internal ID <id>)"                          -- no colon, no hist
_INTERNAL_ID_RE = re.compile(r"Internal ID:?\s+(\d+)")

# Named saved-scope line in the "Saved event targets:" section. Filtered to
# Character type — skip Character_memory and other non-character scopes.
# Example: "surviving_consort: Mathilde ... (Internal ID: 33007 ...) weak (Character - 33007)!"
_SAVE_SCOPE_RE = re.compile(r"^([a-z_]\w*):\s.*?Internal ID:?\s+(\d+).*?\(Character\s*-\s*\d+\)")

# List label in the "Saved list targets:" section. Just "name:" possibly
# with trailing whitespace, no character info on the same line.
_LIST_LABEL_RE = re.compile(r"^([a-z_]\w*):\s*$")

# Bare character line within an active list. No leading "label:" — the role
# comes from the most recently seen list label.
_LIST_ITEM_RE = re.compile(r".*?\(Internal ID:?\s+(\d+).*?\(Character\s*-\s*\d+\)")

_EVENT_TARGETS_HEADER = "Saved event targets:"
_LIST_TARGETS_HEADER = "Saved list targets:"

FailureReason = Literal["invalid_format", "schema_violation", "missing_scope"]


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event: EventPayload
    raw_line: str  # CHRONICLER line + scope dump joined with \n
    wall_clock_at: str  # ISO-8601 UTC
    participants: tuple[tuple[str, int], ...] = ()  # (role, ck3_id) pairs


@dataclass(frozen=True, slots=True)
class ParseFailure:
    raw_line: str
    error: str
    reason: FailureReason
    wall_clock_at: str
    # ck3_chronicler-fkw: best-effort event type from the failing envelope's
    # ``t`` field. None for invalid_format failures where _build_envelope
    # never ran. Surfaced into Quarantine.event_kind and consulted by the
    # suppression hook before insert.
    event_kind: str | None = None


ParseResult = ParsedEvent | ParseFailure


@dataclass
class _Pending:
    envelope: dict[str, Any]
    wall_clock_at: str
    raw_lines: list[str] = field(default_factory=list)
    participants: list[tuple[str, int]] = field(default_factory=list)
    scope_lines_seen: int = 0
    saw_dump_start: bool = False
    section: str = ""  # "" | "event_targets" | "list_targets"
    current_list_name: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_envelope(body: str) -> dict[str, Any]:
    """Convert ``v=1|t=death|d=...|cause=natural`` into an envelope dict."""
    envelope: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    for segment in body.split("|"):
        if "=" not in segment:
            raise ValueError(f"segment is not key=value: {segment!r}")
        key, _, value = segment.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"empty key in segment: {segment!r}")
        if key in _ENVELOPE_KEYS:
            envelope[key] = value
        else:
            payload[key] = value
    envelope["p"] = payload
    return envelope


def _envelope_kind(envelope: dict[str, Any]) -> str | None:
    """Pull the event type tag from a partial envelope, if present.

    Returns None if the envelope doesn't carry a ``t`` field — callers
    treat None as "kind unknown" (skip the suppression check).
    """
    t = envelope.get("t")
    return str(t) if isinstance(t, str) and t else None


def _finalise(pending: _Pending) -> ParseResult:
    """Validate envelope + return ParsedEvent or ParseFailure."""
    raw = "\n".join(pending.raw_lines)
    kind = _envelope_kind(pending.envelope)
    if "c" not in pending.envelope:
        return ParseFailure(
            raw_line=raw,
            error="no Internal ID extracted from scope dump",
            reason="missing_scope",
            wall_clock_at=pending.wall_clock_at,
            event_kind=kind,
        )
    try:
        event = EventAdapter.validate_python(pending.envelope)
    except ValidationError as e:
        return ParseFailure(
            raw_line=raw,
            error=str(e),
            reason="schema_violation",
            wall_clock_at=pending.wall_clock_at,
            event_kind=kind,
        )
    # Dedupe participants while preserving order.
    seen: set[tuple[str, int]] = set()
    deduped: list[tuple[str, int]] = []
    for entry in pending.participants:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return ParsedEvent(
        event=event,
        raw_line=raw,
        wall_clock_at=pending.wall_clock_at,
        participants=tuple(deduped),
    )


class IncrementalParser:
    """Stateful parser pairing CHRONICLER lines with following scope dumps."""

    SCOPE_TIMEOUT_LINES = 80

    def __init__(self) -> None:
        self._pending: _Pending | None = None

    def feed(self, line: str) -> list[ParseResult]:
        """Feed one debug.log line. Returns events that completed (0..2)."""
        results: list[ParseResult] = []
        stripped = line.rstrip("\r\n")

        # New CHRONICLER tag — finalise current pending (its dump just ended)
        # and start a new one.
        chronicler_match = _CHRONICLER_RE.search(stripped)
        if chronicler_match is not None:
            if self._pending is not None:
                results.append(_finalise(self._pending))
                self._pending = None

            body = chronicler_match.group(1)
            wall = _now_iso()
            try:
                envelope = _build_envelope(body)
            except ValueError as e:
                results.append(
                    ParseFailure(
                        raw_line=stripped,
                        error=f"invalid format: {e}",
                        reason="invalid_format",
                        wall_clock_at=wall,
                    )
                )
                return results
            self._pending = _Pending(
                envelope=envelope,
                wall_clock_at=wall,
                raw_lines=[stripped],
            )
            return results

        # Non-tagged line: only interesting if we have a pending.
        if self._pending is None:
            return results

        self._pending.raw_lines.append(stripped)
        self._pending.scope_lines_seen += 1

        # Timeout guard: if a malformed sequence stalls progress, fail it
        # so we don't accumulate forever. Check before per-line processing
        # so lines that early-return don't bypass it.
        if self._pending.scope_lines_seen >= self.SCOPE_TIMEOUT_LINES:
            results.append(
                ParseFailure(
                    raw_line="\n".join(self._pending.raw_lines),
                    error=(
                        f"scope dump exceeded {self.SCOPE_TIMEOUT_LINES} lines without terminator"
                    ),
                    reason="missing_scope",
                    wall_clock_at=self._pending.wall_clock_at,
                    event_kind=_envelope_kind(self._pending.envelope),
                )
            )
            self._pending = None
            return results

        # Engine-prefix line. Two cases:
        # - Awaiting dump start: only an engine-prefix line that ALSO contains
        #   an Internal ID marks the dump start. Plain prefix lines without an
        #   ID are interleaved CK3 trace (music_manager warnings, console
        #   echoes, etc.) and we ignore them.
        # - In dump: any engine-prefix line ends the dump.
        if _ENGINE_PREFIX_RE.match(stripped):
            id_match = _INTERNAL_ID_RE.search(stripped)
            if not self._pending.saw_dump_start:
                if id_match is not None:
                    self._pending.saw_dump_start = True
                    self._pending.envelope["c"] = int(id_match.group(1))
                # else: interleaved noise — skip
                return results
            # In dump → terminator.
            results.append(_finalise(self._pending))
            self._pending = None
            return results

        # Section header detection.
        if stripped == _EVENT_TARGETS_HEADER:
            self._pending.section = "event_targets"
            self._pending.current_list_name = None
            return results
        if stripped == _LIST_TARGETS_HEADER:
            self._pending.section = "list_targets"
            self._pending.current_list_name = None
            return results

        # Blank line: ends the current list label scope (the next non-blank
        # line could be a new label or the next section header).
        if stripped == "":
            self._pending.current_list_name = None
            return results

        # Section-specific extraction.
        if self._pending.section == "event_targets":
            m = _SAVE_SCOPE_RE.match(stripped)
            if m is not None:
                role, char_id = m.group(1), int(m.group(2))
                self._pending.participants.append((role, char_id))

        elif self._pending.section == "list_targets":
            label_match = _LIST_LABEL_RE.match(stripped)
            if label_match is not None:
                self._pending.current_list_name = label_match.group(1)
            elif self._pending.current_list_name is not None:
                item_match = _LIST_ITEM_RE.match(stripped)
                if item_match is not None:
                    self._pending.participants.append(
                        (self._pending.current_list_name, int(item_match.group(1)))
                    )

        return results

    def flush(self) -> list[ParseResult]:
        """Emit any pending event at end-of-stream. Useful for batch tests."""
        if self._pending is None:
            return []
        # If we got far enough to extract `c`, treat end-of-stream as a
        # legitimate dump terminator (the file simply ended). Otherwise
        # report missing_scope.
        if "c" in self._pending.envelope:
            result: ParseResult = _finalise(self._pending)
        else:
            result = ParseFailure(
                raw_line="\n".join(self._pending.raw_lines),
                error="end of stream with no scope dump",
                reason="missing_scope",
                wall_clock_at=self._pending.wall_clock_at,
                event_kind=_envelope_kind(self._pending.envelope),
            )
        self._pending = None
        return [result]

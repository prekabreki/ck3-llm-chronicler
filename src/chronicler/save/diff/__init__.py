"""Emit events from snapshot deltas — the core of the v0.6 architecture.

The save-parse layer (V06-P01) produces :class:`SaveSnapshot` instances.
This package compares two adjacent snapshots and emits events the user
cares about: deaths, marriages, travel, vanilla memory accumulations.

For monthly autosaves, the snapshot pair represents one in-game month
of changes. Sub-month chronology is intentionally collapsed (per the
v0.6 architectural-pivot trade-off documented in ``v0-6-architectural-
pivot`` memory). Each emitted event is dated to the *current*
snapshot's date — the tightest bound we have on when the event
happened without finer-grained information.

Every event constructed here is a variant of the
:data:`chronicler.schema.events.EventPayload` discriminated union — the
same models the debug.log transport produces — so downstream consumers
(biographies, repository inserts) work uniformly across debug_log-sourced
and save-diff-sourced events. That union is the authoritative,
non-drifting catalogue of what can be emitted; this package constructs
most of its variants (deaths, marriages, divorces, travel, vanilla
memories, titles acquired/relinquished/created, alliances, traits,
house/culture/faith changes, wars declared/joined/left/concluded,
artifacts, dynasty legacy / splendor, concubines, modifiers, perks,
lifestyles, epidemics, miscarriages, decisions, buildings, and the RtP
camp/contract/domicile/activity/inspiration beats). Each ``_make_*`` /
``_diff_*`` helper owns one beat and is the source of truth for that
event's trigger condition.

What remains explicitly skipped:
- Birth events for proc-gen NPCs (the population is huge; rely on
  vanilla memories' relative_died / relative_born for player-relevant
  births).
- Imprison / release (no clean state field on the prisoner; vanilla
  memory_imprisoned covers important cases).
"""

from __future__ import annotations

from chronicler.save.diff.character import _NARRATIVELY_INTERESTING_DECISIONS
from chronicler.save.diff.context import (
    DiffContext,
    DiffEvent,
    SetDiffBuilder,
    SetDiffSpec,
    StreamDiffer,
    make_set_differ,
)
from chronicler.save.diff.registry import DIFFERS, STREAM_DIFFERS
from chronicler.save.diff.snapshot import diff_snapshots

__all__ = [
    "DIFFERS",
    "STREAM_DIFFERS",
    "DiffContext",
    "DiffEvent",
    "SetDiffBuilder",
    "SetDiffSpec",
    "StreamDiffer",
    "_NARRATIVELY_INTERESTING_DECISIONS",
    "diff_snapshots",
    "make_set_differ",
]

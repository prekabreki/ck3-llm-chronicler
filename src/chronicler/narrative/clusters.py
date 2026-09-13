"""ck3_chronicler-qdh8: temporal clustering of relative_died vanilla memories.

Pure-logic module. Called from `pipeline._build_user_prompt` to surface
death-cluster patterns to the LLM so biographies can frame multi-death
waves as a single event ("a fever passed through the household") rather
than as three unrelated misfortunes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from chronicler.util.dates import parse_ck3_date

log = logging.getLogger(__name__)

# Thresholds chosen to match the Thrugot smoke evidence (3 deaths in 15
# days felt like an outbreak). Constants — not config — because tuning
# would require regenerating affected biographies, so the change cost is
# already heavy enough that "edit and ship" is the right cadence.
DEATH_CLUSTER_MIN_COUNT = 3
DEATH_CLUSTER_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class ClusterDeath:
    """One death within a cluster. Fields mirror the relative_died
    VanillaMemoryPayload (7jwu/dn7a) so callers can render without
    re-touching the event row."""

    date: str  # CK3-format date string, e.g. "1102.6.2"
    first_name: str | None
    age: int | None
    cause: str | None


@dataclass(frozen=True, slots=True)
class DeathCluster:
    start_date: str
    end_date: str
    deaths: tuple[ClusterDeath, ...]

    @property
    def count(self) -> int:
        return len(self.deaths)


def detect_death_clusters(
    events: Sequence[object],
    *,
    min_count: int = DEATH_CLUSTER_MIN_COUNT,
    window_days: int = DEATH_CLUSTER_WINDOW_DAYS,
) -> list[DeathCluster]:
    """Find temporal clusters of relative_died memories in ``events``.

    ``events`` is a sequence of `_EventSnapshot`-shaped objects (anything
    with ``event_type: str``, ``event_date: str``, ``event_date_iso: str | None``,
    and ``payload_json: str``). Returns clusters sorted by ``start_date``.

    Merge rule: greedy left-to-right scan. Two relative_died memories
    join the same cluster when their ISO dates are within ``window_days``
    of each other (using the previous event in the cluster as the anchor,
    so a slow-trickle outbreak still merges).
    """
    # Filter to relative_died with a parseable date. Sort by ISO date.
    candidates: list[tuple[date, object, dict]] = []
    for e in events:
        if getattr(e, "event_type", None) != "vanilla_memory":
            continue
        iso = getattr(e, "event_date_iso", None)
        if iso is None:
            continue
        try:
            payload = json.loads(getattr(e, "payload_json", "{}"))
        except json.JSONDecodeError:
            log.debug("dropping cluster candidate with malformed payload")
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("memory_type") != "relative_died":
            continue
        try:
            parsed = date.fromisoformat(iso)
        except (TypeError, ValueError):
            log.debug("dropping cluster candidate with unparseable iso %r", iso)
            continue
        candidates.append((parsed, e, payload))

    candidates.sort(key=lambda t: t[0])

    clusters: list[DeathCluster] = []
    current: list[tuple[date, object, dict]] = []

    def _close(group: list[tuple[date, object, dict]]) -> None:
        if len(group) < min_count:
            return
        deaths = tuple(
            ClusterDeath(
                date=ev.event_date,
                first_name=p.get("deceased_first_name"),
                age=p.get("deceased_age"),
                cause=p.get("deceased_cause"),
            )
            for _, ev, p in group
        )
        clusters.append(
            DeathCluster(
                start_date=group[0][1].event_date,
                end_date=group[-1][1].event_date,
                deaths=deaths,
            )
        )

    for entry in candidates:
        if not current:
            current = [entry]
            continue
        last_date = current[-1][0]
        if (entry[0] - last_date).days <= window_days:
            current.append(entry)
        else:
            _close(current)
            current = [entry]
    _close(current)

    return clusters


_HEADER = "Recorded death clusters in this character's life:"
_MISSING = "—"  # em-dash placeholder for null fields


def _format_one_death(d: ClusterDeath) -> str:
    name = d.first_name or _MISSING
    age = str(d.age) if d.age is not None else _MISSING
    cause = d.cause or _MISSING
    return f"{name} ({age}, {cause})"


def _day_span(start_iso: str, end_iso: str) -> int:
    """Days between two CK3-format dates; 0 when either is unparseable."""
    s = parse_ck3_date(start_iso)
    e = parse_ck3_date(end_iso)
    if s is None or e is None:
        return 0
    return (date.fromisoformat(e) - date.fromisoformat(s)).days


def format_death_cluster_block(clusters: Sequence[DeathCluster]) -> str:
    """Render the cluster-handle block for the biography prompt.

    Returns empty string when no clusters — caller can use the result
    in a falsy check before composing it into the prompt.
    """
    if not clusters:
        return ""
    lines = [_HEADER]
    for c in clusters:
        days = _day_span(c.start_date, c.end_date)
        deaths_inline = "; ".join(_format_one_death(d) for d in c.deaths)
        lines.append(
            f"- {c.start_date} → {c.end_date} ({days} days, {c.count} relatives): {deaths_inline}."
        )
    return "\n".join(lines)

"""Generic set-diff streams over per-character reverse indexes.

Most snapshot-level streams share one skeleton (dead-guard, set-diff,
sorted added/removed loops, None-record skip). Each spec declares the
index accessor and per-transition event builders; :func:`make_set_differ`
runs the skeleton once.
"""

from __future__ import annotations

from chronicler.save.diff.context import DiffEvent, SetDiffSpec
from chronicler.save.parse import CharacterSnapshot
from chronicler.schema import (
    ActivityCompletedEvent,
    ActivityPayload,
    AllianceBrokenEvent,
    AllianceFormedEvent,
    AlliancePayload,
    ArtifactAcquiredEvent,
    ArtifactLostEvent,
    ArtifactPayload,
    CampCompanionJoinedEvent,
    CampCompanionLeftEvent,
    CampCompanionPayload,
    EpidemicOutbreakEvent,
    EpidemicPayload,
    InspirationSponsoredEvent,
    InspirationSponsoredPayload,
)


def _resolve_employee_name(
    employee_id: int | None,
    characters: dict[int, CharacterSnapshot],
) -> str | None:
    """Look up a character's first_name from the snapshot. Returns
    None when the id is missing or the character has no first_name
    (e.g. pruned record). Mirrors gu7j's diff-time name resolution
    convention so the renderer can read the name directly off the
    payload without a second lookup."""
    if employee_id is None:
        return None
    char = characters.get(employee_id)
    if char is None:
        return None
    return char.first_name


def _alliance_added(cid, ally_id, _prev, _curr, date):
    return DiffEvent(
        event=AllianceFormedEvent(d=date, c=cid, p=AlliancePayload(ally_character_id=ally_id)),
        participants=(("ally", ally_id),),
    )


def _alliance_removed(cid, ally_id, _prev, _curr, date):
    return DiffEvent(
        event=AllianceBrokenEvent(d=date, c=cid, p=AlliancePayload(ally_character_id=ally_id)),
        participants=(("former_ally", ally_id),),
    )


_ALLIANCES_SPEC = SetDiffSpec(
    name="alliances",
    index=lambda s: s.alliances,
    on_added=_alliance_added,
    on_removed=_alliance_removed,
)


def _artifact_added(cid, art_id, _prev, curr, date):
    art = curr.artifacts.get(art_id)
    if art is None:
        return None
    return DiffEvent(
        event=ArtifactAcquiredEvent(
            d=date,
            c=cid,
            p=ArtifactPayload(artifact_id=art_id, name=art.name, type=art.type, rarity=art.rarity),
        ),
        participants=(),
    )


def _artifact_removed(cid, art_id, prev, curr, date):
    art = prev.artifacts.get(art_id) or curr.artifacts.get(art_id)
    if art is None:
        return None
    return DiffEvent(
        event=ArtifactLostEvent(
            d=date,
            c=cid,
            p=ArtifactPayload(artifact_id=art_id, name=art.name, type=art.type, rarity=art.rarity),
        ),
        participants=(),
    )


_ARTIFACTS_SPEC = SetDiffSpec(
    name="artifacts",
    index=lambda s: s.character_to_artifacts,
    on_added=_artifact_added,
    on_removed=_artifact_removed,
)


def _epidemic_added(cid, ep_id, _prev, curr, date):
    ep = curr.epidemics.get(ep_id)
    if ep is None:
        return None
    return DiffEvent(
        event=EpidemicOutbreakEvent(
            d=date,
            c=cid,
            p=EpidemicPayload(
                epidemic_id=ep.epidemic_id,
                epidemic_type=ep.epidemic_type,
                name=ep.name,
                intensity=ep.intensity,
                start_date=ep.creation_date,
                num_infected_provinces=ep.num_infected_provinces,
                num_character_deaths=ep.num_character_deaths,
            ),
        ),
        participants=(),
    )


_EPIDEMICS_SPEC = SetDiffSpec(
    name="epidemics",
    index=lambda s: s.character_to_epidemics,
    on_added=_epidemic_added,
    on_removed=None,
)


def _activity_removed(cid, activity_id, prev, _curr, date):
    snap = prev.activities.get(activity_id)
    if snap is None:
        return None
    role: str = "host" if snap.host_id == cid else "attendee"
    return DiffEvent(
        event=ActivityCompletedEvent(
            d=date,
            c=cid,
            p=ActivityPayload(
                activity_id=snap.activity_id,
                activity_type=snap.activity_type,
                role=role,  # type: ignore[arg-type]
                host_id=snap.host_id,
                start_province_id=snap.start_province_id,
                start_date=snap.active_start_date,
            ),
        ),
        participants=(),
    )


_ACTIVITIES_SPEC = SetDiffSpec(
    name="activities",
    index=lambda s: s.character_to_activities,
    on_added=None,
    on_removed=_activity_removed,
)


def _inspiration_added(cid, inspiration_id, _prev, curr, date):
    snap = curr.inspirations.get(inspiration_id)
    if snap is None:
        return None
    return DiffEvent(
        event=InspirationSponsoredEvent(
            d=date,
            c=cid,
            p=InspirationSponsoredPayload(
                inspiration_id=snap.inspiration_id,
                inspiration_type=snap.inspiration_type,
                artisan_character_id=snap.artisan_character_id,
                total_cost=snap.total_cost,
            ),
        ),
        participants=(),
    )


_INSPIRATIONS_SPEC = SetDiffSpec(
    name="inspirations",
    index=lambda s: s.character_to_sponsored_inspirations,
    on_added=_inspiration_added,
    on_removed=None,
)


def _court_position_added(cid, pid, _prev, curr, date):
    snap = curr.court_positions.get(pid)
    if snap is None:
        return None
    employee_name = _resolve_employee_name(snap.employee_id, curr.characters)
    return DiffEvent(
        event=CampCompanionJoinedEvent(
            d=date,
            c=cid,
            p=CampCompanionPayload(
                position_id=snap.position_id,
                court_position=snap.court_position,
                employee_id=snap.employee_id,
                employee_name=employee_name,
                hire_date=snap.hire_date,
            ),
        ),
        participants=(),
    )


def _court_position_removed(cid, pid, prev, curr, date):
    snap = prev.court_positions.get(pid)
    if snap is None:
        return None
    # Departing employee may still be alive in curr.characters — same
    # lookup as the original (curr_characters for the left branch too).
    employee_name = _resolve_employee_name(snap.employee_id, curr.characters)
    return DiffEvent(
        event=CampCompanionLeftEvent(
            d=date,
            c=cid,
            p=CampCompanionPayload(
                position_id=snap.position_id,
                court_position=snap.court_position,
                employee_id=snap.employee_id,
                employee_name=employee_name,
                hire_date=snap.hire_date,
            ),
        ),
        participants=(),
    )


_COURT_POSITIONS_SPEC = SetDiffSpec(
    name="court_positions",
    index=lambda s: s.character_to_court_positions,
    on_added=_court_position_added,
    on_removed=_court_position_removed,
)

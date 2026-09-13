"""Tests for the wire-format Pydantic models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from chronicler.schema import (
    SCHEMA_VERSION,
    BirthEvent,
    DeathEvent,
    DivorceEvent,
    EventAdapter,
    ImprisonEvent,
    MarriageEvent,
    ReleaseEvent,
    TitleGainEvent,
    TitleLostEvent,
    WarStartedEvent,
    WarWonAttackerEvent,
    WarWonDefenderEvent,
)


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_death_event_round_trip() -> None:
    raw = {
        "v": 1,
        "t": "death",
        "d": "1066.10.14",
        "c": 1234,
        "p": {"killer": 5678, "cause": "battle"},
    }
    event = DeathEvent.model_validate(raw)
    assert event.t == "death"
    assert event.c == 1234
    assert event.p.killer == 5678
    assert event.p.cause == "battle"
    assert json.loads(event.model_dump_json(exclude_none=True)) == raw


def test_death_event_optional_fields() -> None:
    raw = {"v": 1, "t": "death", "d": "1066.10.14", "c": 1234, "p": {}}
    event = DeathEvent.model_validate(raw)
    assert event.p.killer is None
    assert event.p.cause is None


def test_death_event_rejects_unknown_field() -> None:
    raw = {
        "v": 1,
        "t": "death",
        "d": "1066.10.14",
        "c": 1234,
        "p": {},
        "unknown": "nope",
    }
    with pytest.raises(ValidationError):
        DeathEvent.model_validate(raw)


def test_death_event_rejects_wrong_type_discriminator() -> None:
    raw = {"v": 1, "t": "birth", "d": "1066.10.14", "c": 1234, "p": {}}
    with pytest.raises(ValidationError):
        DeathEvent.model_validate(raw)


def test_death_event_rejects_missing_required_field() -> None:
    raw = {"v": 1, "t": "death", "d": "1066.10.14", "p": {}}
    with pytest.raises(ValidationError):
        DeathEvent.model_validate(raw)


def test_death_event_is_frozen() -> None:
    raw = {"v": 1, "t": "death", "d": "1066.10.14", "c": 1234, "p": {}}
    event = DeathEvent.model_validate(raw)
    with pytest.raises(ValidationError):
        event.c = 9999  # type: ignore[misc]


# --- v0.2 lifecycle event types ---


@pytest.mark.parametrize(
    "type_name,model_cls",
    [
        ("birth", BirthEvent),
        ("marriage", MarriageEvent),
        ("divorce", DivorceEvent),
        ("title_gain", TitleGainEvent),
        ("title_lost", TitleLostEvent),
        ("war_started", WarStartedEvent),
        ("war_won_attacker", WarWonAttackerEvent),
        ("war_won_defender", WarWonDefenderEvent),
        ("imprison", ImprisonEvent),
        ("release", ReleaseEvent),
    ],
)
def test_v02_event_types_round_trip(type_name: str, model_cls) -> None:
    raw = {"v": 1, "t": type_name, "d": "16th of September, 1066 AD", "c": 1234, "p": {}}
    event = model_cls.model_validate(raw)
    assert event.t == type_name
    assert event.c == 1234
    # The discriminated union should accept and route to the right model
    via_adapter = EventAdapter.validate_python(raw)
    assert isinstance(via_adapter, model_cls)


@pytest.mark.parametrize(
    "type_name,model_cls",
    [
        ("birth", BirthEvent),
        ("marriage", MarriageEvent),
        ("imprison", ImprisonEvent),
    ],
)
def test_v02_event_payload_rejects_unknown_fields(type_name: str, model_cls) -> None:
    raw = {
        "v": 1,
        "t": type_name,
        "d": "16th of September, 1066 AD",
        "c": 1234,
        "p": {"unknown_field": "x"},
    }
    with pytest.raises(ValidationError):
        model_cls.model_validate(raw)


def test_unknown_event_type_rejected_by_adapter() -> None:
    raw = {"v": 1, "t": "smarch_horror", "d": "1066.10.14", "c": 1234, "p": {}}
    with pytest.raises(ValidationError):
        EventAdapter.validate_python(raw)

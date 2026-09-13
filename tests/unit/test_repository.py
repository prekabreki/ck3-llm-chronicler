"""Tests for the per-campaign DB repository helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from chronicler.db import Base, Character, make_engine_for_path, make_session_factory
from chronicler.db.repository import (
    PLAYTHROUGH_ID_META_KEY,
    PlaythroughMismatchError,
    append_coa_history_if_changed,
    assert_playthrough_or_pin,
    compute_played_character_ids,
    dump_character,
    get_character,
    get_latest_biography_for_character,
    get_meta,
    insert_biography,
    insert_event_idempotent,
    insert_quarantine,
    list_biographies_for_character,
    list_coa_history,
    list_events_for_character,
    search_characters_by_name,
    set_meta,
    upsert_character,
)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine_for_path(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _insert_args(
    *,
    primary_character_id: int = 1234,
    event_type: str = "death",
    event_date: str = "1066.10.14",
    payload_json: str = '{"v":1,"t":"death","d":"1066.10.14","c":1234,"p":{}}',
    raw_line: str = "raw",
) -> dict:
    return dict(
        schema_version=1,
        event_type=event_type,
        event_date=event_date,
        wall_clock_at="2026-04-29T18:54:27+00:00",
        primary_character_id=primary_character_id,
        payload_json=payload_json,
        raw_line=raw_line,
    )


def test_search_characters_by_name_escapes_like_wildcards(session: Session) -> None:
    """audit L20: LIKE wildcards in the query are escaped, so '%' and '_'
    match those characters literally instead of matching every row."""
    upsert_character(session, ck3_id=1, first_name="100% Real")
    upsert_character(session, ck3_id=2, first_name="Alfred")
    upsert_character(session, ck3_id=3, first_name="a_b")
    session.commit()

    # A bare "%" is the LIKE "match anything" wildcard; escaped, it only
    # matches names that literally contain "%". Pre-fix this returned all rows.
    pct = search_characters_by_name(session, "%", limit=50)
    assert {c.ck3_id for c in pct} == {1}

    # "_" is the single-char wildcard; escaped, only the literal "a_b" matches
    # (not "Alfred", which an unescaped '%_%' would catch).
    underscore = search_characters_by_name(session, "_", limit=50)
    assert {c.ck3_id for c in underscore} == {3}


def test_upsert_character_inserts_new(session: Session) -> None:
    upsert_character(session, ck3_id=1, first_name="Harold", dynasty_name="Godwin")
    character = get_character(session, 1)
    assert character is not None
    assert character.first_name == "Harold"
    assert character.dynasty_name == "Godwin"
    # ck3_chronicler-u6cr (2026-05-08): Character.relevance was dropped —
    # the column was always 'unknown' in production, see migration u6cr0d11e0d.


def test_upsert_character_updates_existing(session: Session) -> None:
    upsert_character(session, ck3_id=1, first_name="Harold")
    upsert_character(session, ck3_id=1, first_name="Harold", death_date="1066.10.14")
    character = get_character(session, 1)
    assert character is not None
    assert character.first_name == "Harold"
    assert character.death_date == "1066.10.14"


def test_upsert_character_drops_unknown_kwargs(session: Session) -> None:
    upsert_character(session, ck3_id=1, first_name="Harold", made_up_field="x")
    character = get_character(session, 1)
    assert character is not None
    assert character.first_name == "Harold"


def test_insert_event_returns_id(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    event_id = insert_event_idempotent(session, **_insert_args())
    assert event_id is not None
    assert event_id > 0


def test_insert_event_idempotent_returns_none_on_duplicate(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    first = insert_event_idempotent(session, **_insert_args())
    second = insert_event_idempotent(session, **_insert_args())
    assert first is not None
    assert second is None
    events = list_events_for_character(session, 1234)
    assert len(events) == 1


def test_insert_event_with_participants(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    upsert_character(session, ck3_id=5678)
    event_id = insert_event_idempotent(session, **_insert_args(), participants=[(5678, "killer")])
    assert event_id is not None
    events = list_events_for_character(session, 1234)
    assert len(events) == 1


def test_dump_character_unknown_returns_none(session: Session) -> None:
    assert dump_character(session, 9999) is None


def test_dump_character_returns_dict_with_events(session: Session) -> None:
    upsert_character(session, ck3_id=1234, first_name="Harold", dynasty_name="Godwin")
    insert_event_idempotent(session, **_insert_args())
    dump = dump_character(session, 1234)
    assert dump is not None
    assert dump["ck3_id"] == 1234
    assert dump["first_name"] == "Harold"
    assert len(dump["events"]) == 1
    assert dump["events"][0]["type"] == "death"
    assert dump["events"][0]["payload"]["t"] == "death"


def test_insert_quarantine(session: Session) -> None:
    qid = insert_quarantine(
        session, raw_line="bad line", error="some error", ts="2026-04-29T18:54:27+00:00"
    )
    assert qid > 0


def test_set_and_get_meta(session: Session) -> None:
    set_meta(session, "schema_version", "1")
    assert get_meta(session, "schema_version") == "1"
    set_meta(session, "schema_version", "2")  # overwrite
    assert get_meta(session, "schema_version") == "2"


def test_get_meta_missing_returns_none(session: Session) -> None:
    assert get_meta(session, "nope") is None


def test_foreign_key_enforced(session: Session) -> None:
    """foreign_keys=ON pragma should reject events for unknown characters."""
    from sqlalchemy.exc import IntegrityError

    args = _insert_args(primary_character_id=99999)  # no character row
    with pytest.raises(IntegrityError):
        insert_event_idempotent(session, **args)
        session.flush()


def test_character_can_have_zero_events(session: Session) -> None:
    upsert_character(session, ck3_id=1, first_name="Edward")
    assert list_events_for_character(session, 1) == []


def test_models_via_orm_query(session: Session) -> None:
    upsert_character(session, ck3_id=1, first_name="Edward")
    from sqlalchemy import select

    rows = session.execute(select(Character)).scalars().all()
    assert len(rows) == 1
    assert rows[0].first_name == "Edward"


# --- V02-N02 biographies ---


def test_insert_biography_assigns_version_one_for_first_row(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    bio = insert_biography(
        session,
        character_id=1234,
        body="A short life cut shorter still.",
        prompt_template_version="biography_v1",
        provider="ollama:qwen2.5:14b",
        generated_at="2026-05-01T12:00:00+00:00",
    )
    assert bio.version == 1
    assert bio.body.startswith("A short life")


def test_insert_biography_persists_cache_breakdown_and_cost(session: Session) -> None:
    """ck3_chronicler-cs1o: cache read/write subsets + the transport's
    cost figure land on the row so the cost endpoints can bill each
    bucket at its real rate instead of the summed-input approximation."""
    upsert_character(session, ck3_id=1234)
    bio = insert_biography(
        session,
        character_id=1234,
        body="A costed life.",
        prompt_template_version="biography_v5",
        provider="anthropic:claude-opus-4-7",
        generated_at="2026-06-05T12:00:00+00:00",
        prompt_tokens=1012,
        completion_tokens=9,
        cache_read_tokens=900,
        cache_write_tokens=100,
        cost_usd=0.336,
    )
    assert bio.cache_read_tokens == 900
    assert bio.cache_write_tokens == 100
    assert bio.cost_usd == 0.336


def test_insert_biography_cache_columns_default_null(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    bio = insert_biography(
        session,
        character_id=1234,
        body="A legacy row.",
        prompt_template_version="biography_v1",
        provider="ollama:qwen2.5:14b",
        generated_at="2026-05-01T12:00:00+00:00",
    )
    assert bio.cache_read_tokens is None
    assert bio.cache_write_tokens is None
    assert bio.cost_usd is None


def test_insert_biography_increments_version_per_character(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    upsert_character(session, ck3_id=5678)
    insert_biography(
        session,
        character_id=1234,
        body="v1 of 1234",
        prompt_template_version="biography_v1",
        provider="ollama",
        generated_at="2026-05-01T12:00:00+00:00",
    )
    insert_biography(
        session,
        character_id=1234,
        body="v2 of 1234",
        prompt_template_version="biography_v1",
        provider="ollama",
        generated_at="2026-05-01T13:00:00+00:00",
    )
    bio_other = insert_biography(
        session,
        character_id=5678,
        body="v1 of 5678",
        prompt_template_version="biography_v1",
        provider="ollama",
        generated_at="2026-05-01T13:30:00+00:00",
    )
    # Each character has its own version sequence
    versions_1234 = [b.version for b in list_biographies_for_character(session, 1234)]
    assert versions_1234 == [1, 2]
    assert bio_other.version == 1


def test_get_latest_biography_returns_highest_version(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    insert_biography(
        session,
        character_id=1234,
        body="first attempt",
        prompt_template_version="biography_v1",
        provider="ollama",
        generated_at="2026-05-01T12:00:00+00:00",
    )
    insert_biography(
        session,
        character_id=1234,
        body="second attempt — better prompt",
        prompt_template_version="biography_v1",
        provider="ollama",
        generated_at="2026-05-01T13:00:00+00:00",
    )
    latest = get_latest_biography_for_character(session, 1234)
    assert latest is not None
    assert latest.version == 2
    assert latest.body == "second attempt — better prompt"


def test_get_latest_biography_returns_none_for_unknown_character(session: Session) -> None:
    assert get_latest_biography_for_character(session, 999999) is None


def test_biography_token_metadata_persisted(session: Session) -> None:
    upsert_character(session, ck3_id=1234)
    event_id = insert_event_idempotent(
        session,
        schema_version=1,
        event_type="death",
        event_date="1066.10.14",
        wall_clock_at="2026-05-01T11:30:00+00:00",
        primary_character_id=1234,
        payload_json='{"v":1,"t":"death","d":"1066.10.14","c":1234,"p":{}}',
        raw_line="CHRONICLER|v=1|t=death|d=1066.10.14",
    )
    assert event_id is not None
    bio = insert_biography(
        session,
        character_id=1234,
        body="some text",
        prompt_template_version="biography_v1",
        provider="ollama:qwen2.5:14b",
        generated_at="2026-05-01T12:00:00+00:00",
        prompt_tokens=512,
        completion_tokens=320,
        events_through_event_id=event_id,
    )
    assert bio.prompt_tokens == 512
    assert bio.completion_tokens == 320
    assert bio.events_through_event_id == event_id


# --- playthrough_id pinning ---


def test_assert_playthrough_pins_on_first_observation(session: Session) -> None:
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) is None
    result = assert_playthrough_or_pin(session, observed="uuid-aaa")
    assert result == "uuid-aaa"
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) == "uuid-aaa"


def test_assert_playthrough_match_is_noop(session: Session) -> None:
    assert_playthrough_or_pin(session, observed="uuid-aaa")
    # second pass with the same id is fine, doesn't raise, doesn't change state
    result = assert_playthrough_or_pin(session, observed="uuid-aaa")
    assert result == "uuid-aaa"


def test_assert_playthrough_mismatch_raises(session: Session) -> None:
    assert_playthrough_or_pin(session, observed="uuid-aaa")
    with pytest.raises(PlaythroughMismatchError) as exc_info:
        assert_playthrough_or_pin(session, observed="uuid-bbb")
    assert exc_info.value.observed == "uuid-bbb"
    assert exc_info.value.pinned == "uuid-aaa"
    # state preserved on refusal
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) == "uuid-aaa"


def test_assert_playthrough_allow_reset_overwrites(session: Session) -> None:
    assert_playthrough_or_pin(session, observed="uuid-aaa")
    result = assert_playthrough_or_pin(session, observed="uuid-bbb", allow_reset=True)
    assert result == "uuid-bbb"
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) == "uuid-bbb"


def test_assert_playthrough_empty_observed_does_not_pin(session: Session) -> None:
    """A blank observed value (older save format, partial parse) must not lock the campaign."""
    result = assert_playthrough_or_pin(session, observed="")
    assert result is None
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) is None


def test_assert_playthrough_empty_observed_after_pin_returns_pinned(session: Session) -> None:
    assert_playthrough_or_pin(session, observed="uuid-aaa")
    result = assert_playthrough_or_pin(session, observed="")
    assert result == "uuid-aaa"
    # mismatch check is skipped, state preserved
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) == "uuid-aaa"


def test_assert_playthrough_pin_if_unset_false_does_not_pin(session: Session) -> None:
    """save-tail's startup baseline must verify only, never pin from a stale save."""
    result = assert_playthrough_or_pin(session, observed="uuid-aaa", pin_if_unset=False)
    assert result is None
    assert get_meta(session, PLAYTHROUGH_ID_META_KEY) is None


def test_assert_playthrough_pin_if_unset_false_still_checks_match(session: Session) -> None:
    """Once a pin exists, pin_if_unset=False still verifies on match."""
    assert_playthrough_or_pin(session, observed="uuid-aaa")  # explicit pin
    # save-tail-style verify against matching id is a no-op
    result = assert_playthrough_or_pin(session, observed="uuid-aaa", pin_if_unset=False)
    assert result == "uuid-aaa"


def test_assert_playthrough_pin_if_unset_false_still_raises_on_mismatch(session: Session) -> None:
    """Once a pin exists, pin_if_unset=False still raises on mismatch."""
    assert_playthrough_or_pin(session, observed="uuid-aaa")
    with pytest.raises(PlaythroughMismatchError):
        assert_playthrough_or_pin(session, observed="uuid-bbb", pin_if_unset=False)


# --- ck3_chronicler-7b8d: CoA history ---


def test_append_coa_history_inserts_first_row(session: Session) -> None:
    upsert_character(session, ck3_id=100, first_name="Alfred")
    inserted = append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"pattern":"a"}',
        observed_at="2026-05-06T10:00:00+00:00",
    )
    assert inserted is True
    rows = list_coa_history(session, 100)
    assert len(rows) == 1
    assert rows[0].coa_json == '{"pattern":"a"}'
    assert rows[0].observed_at == "2026-05-06T10:00:00+00:00"


def test_append_coa_history_no_op_when_unchanged(session: Session) -> None:
    upsert_character(session, ck3_id=100, first_name="Alfred")
    append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"pattern":"a"}',
        observed_at="2026-05-06T10:00:00+00:00",
    )
    inserted = append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"pattern":"a"}',
        observed_at="2026-05-06T11:00:00+00:00",
    )
    assert inserted is False
    rows = list_coa_history(session, 100)
    assert len(rows) == 1


def test_append_coa_history_inserts_on_change(session: Session) -> None:
    upsert_character(session, ck3_id=100, first_name="Alfred")
    append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"pattern":"a"}',
        observed_at="2026-05-06T10:00:00+00:00",
    )
    inserted = append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"pattern":"b"}',
        observed_at="2026-05-06T11:00:00+00:00",
    )
    assert inserted is True
    rows = list_coa_history(session, 100)
    assert [r.coa_json for r in rows] == ['{"pattern":"a"}', '{"pattern":"b"}']


def test_list_coa_history_per_character_isolated(session: Session) -> None:
    upsert_character(session, ck3_id=100)
    upsert_character(session, ck3_id=200)
    append_coa_history_if_changed(
        session,
        character_id=100,
        coa_json='{"x":1}',
        observed_at="t1",
    )
    append_coa_history_if_changed(
        session,
        character_id=200,
        coa_json='{"y":2}',
        observed_at="t2",
    )
    assert [r.coa_json for r in list_coa_history(session, 100)] == ['{"x":1}']
    assert [r.coa_json for r in list_coa_history(session, 200)] == ['{"y":2}']


# --- ck3_chronicler-w1t3: compute_played_character_ids ---


def _seed_title_acquired(
    session: Session,
    *,
    acquirer_id: int,
    from_holder_id: int | None,
    tier: str = "duchy",
    title_id: int = 9001,
    date: str = "1066.9.15",
) -> None:
    """Insert a synthetic title_acquired event whose payload mirrors what
    save/diff.py emits in production (TitleAcquiredEvent serialised via
    payload_canonical_json).
    """
    import json as _json

    payload = {
        "v": 1,
        "t": "title_acquired",
        "d": date,
        "c": acquirer_id,
        "p": {
            "title_id": title_id,
            "title_key": "d_jylland",
            "title_name": "Jylland",
            "tier": tier,
        },
    }
    if from_holder_id is not None:
        payload["p"]["from_holder_id"] = from_holder_id

    insert_event_idempotent(
        session,
        schema_version=1,
        event_type="title_acquired",
        event_date=date,
        event_date_iso="1066-09-15",
        wall_clock_at="2026-05-08T00:00:00+00:00",
        primary_character_id=acquirer_id,
        payload_json=_json.dumps(payload, separators=(",", ":"), sort_keys=True),
        raw_line="seed",
    )


def test_compute_played_returns_empty_when_no_player(session: Session) -> None:
    assert compute_played_character_ids(session, None) == set()


def test_compute_played_singleton_when_no_inheritance_chain(session: Session) -> None:
    # Latest player exists, but there are no title_acquired events linking
    # them to any predecessor — the set must still include the player.
    upsert_character(session, ck3_id=42)
    assert compute_played_character_ids(session, 42) == {42}


def test_compute_played_walks_chain_backward(session: Session) -> None:
    """Thrugot → Svend → Christoffer: Christoffer is the latest player.
    The walk must surface all three by chasing from_holder_id backward
    through duchy-tier title_acquired events.
    """
    for cid in (1001, 1002, 1003):
        upsert_character(session, ck3_id=cid)
    # Svend acquired Jylland from Thrugot.
    _seed_title_acquired(session, acquirer_id=1002, from_holder_id=1001)
    # Christoffer acquired Jylland from Svend.
    _seed_title_acquired(session, acquirer_id=1003, from_holder_id=1002, date="1095.6.6")
    session.commit()

    assert compute_played_character_ids(session, 1003) == {1001, 1002, 1003}


def test_compute_played_filters_below_duchy_tier(session: Session) -> None:
    """A title_acquired event for a county-tier title must NOT extend
    the played-character set — only landed players (duchy+) are
    primary-title holders in the player line."""
    upsert_character(session, ck3_id=1)
    upsert_character(session, ck3_id=2)
    _seed_title_acquired(session, acquirer_id=2, from_holder_id=1, tier="county")
    session.commit()

    # Only the latest player surfaces; the from_holder_id at county tier
    # is not promoted into the played set.
    assert compute_played_character_ids(session, 2) == {2}


def test_compute_played_includes_kingdom_and_empire_tiers(session: Session) -> None:
    upsert_character(session, ck3_id=10)
    upsert_character(session, ck3_id=11)
    upsert_character(session, ck3_id=12)
    _seed_title_acquired(session, acquirer_id=11, from_holder_id=10, tier="kingdom")
    _seed_title_acquired(session, acquirer_id=12, from_holder_id=11, tier="empire", date="1100.1.1")
    session.commit()

    assert compute_played_character_ids(session, 12) == {10, 11, 12}


def test_compute_played_handles_malformed_payload_gracefully(session: Session) -> None:
    """A bad payload row (e.g. legacy event with corrupt JSON) must not
    blow up the helper; it should skip the row and continue."""
    upsert_character(session, ck3_id=5)
    upsert_character(session, ck3_id=6)
    upsert_character(session, ck3_id=7)
    # Insert a well-formed row first.
    _seed_title_acquired(session, acquirer_id=6, from_holder_id=5)
    # Then sneak a malformed row in via raw SQL — bypasses Pydantic.
    from sqlalchemy import text as _text

    session.execute(
        _text(
            "INSERT INTO events (schema_version, event_type, event_date, "
            "event_date_iso, wall_clock_at, primary_character_id, "
            "payload_json, raw_line) VALUES "
            "(1, 'title_acquired', '1070.1.1', '1070-01-01', "
            "'2026-05-08T00:00:00+00:00', 7, '{not valid json', 'malformed')"
        )
    )
    session.commit()

    assert compute_played_character_ids(session, 6) == {5, 6}


def test_compute_played_terminates_on_cycle(session: Session) -> None:
    """A pathological from_holder_id loop (A→B→A) must not hang the
    walk — the safety counter caps recursion."""
    upsert_character(session, ck3_id=200)
    upsert_character(session, ck3_id=201)
    _seed_title_acquired(session, acquirer_id=201, from_holder_id=200)
    _seed_title_acquired(session, acquirer_id=200, from_holder_id=201, date="1080.1.1")
    session.commit()

    # Both ids surface; no infinite loop.
    assert compute_played_character_ids(session, 201) == {200, 201}

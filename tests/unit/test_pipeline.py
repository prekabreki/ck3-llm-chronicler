"""Tests for chronicler.narrative.pipeline.generate_biography.

A fake NarrativeProvider records what it was called with so we can
verify prompt assembly and the persistence path without spinning up
Ollama or mocking HTTP.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from chronicler.db import Base, make_engine_for_path, make_session_factory
from chronicler.db.repository import (
    get_latest_biography_for_character,
    insert_event_idempotent,
    upsert_character,
)
from chronicler.narrative import generate_biography
from chronicler.narrative.pipeline import MAX_EVENTS_PER_BIOGRAPHY
from chronicler.narrative.prompt_builder import PROMPT_TEMPLATE_VERSION
from tests.helpers.providers import FakeProvider


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """Yield a sessionmaker bound to a fresh in-tmp_path SQLite DB."""
    engine = make_engine_for_path(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Helper for tests that want a session for setup/assertion work.

    Auto-committing on context exit so test bodies don't have to."""
    with factory() as s:
        yield s


def _seed_character_with_events(session: Session, ck3_id: int, count: int = 1) -> int:
    """Helper: create a character + N death-shaped events. Returns last event id."""
    upsert_character(session, ck3_id=ck3_id, first_name="Harold", culture="english")
    last_id = 0
    for i in range(count):
        eid = insert_event_idempotent(
            session,
            schema_version=1,
            event_type="death",
            event_date=f"{i + 1}st of January, {1066 + i} AD",
            event_date_iso=f"{1066 + i:04d}-01-{i + 1:02d}",
            wall_clock_at=f"2026-05-01T12:00:{i:02d}+00:00",
            primary_character_id=ck3_id,
            payload_json=f'{{"v":1,"t":"death","d":"x","c":{ck3_id},"p":{{"event_no":{i}}}}}',
            raw_line=f"line-{i}",
        )
        assert eid is not None
        last_id = eid
    session.commit()
    return last_id


@pytest.mark.asyncio
async def test_generate_biography_persists_a_row(
    session: Session,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the scene_setter (v3) mode so this baseline persistence test
    # asserts against PROMPT_TEMPLATE_VERSION (which is the v3 constant
    # by design, see _PROMPT_BY_MODE in pipeline.py). The active default
    # flipped to woven/v5 on 2026-05-08; the woven path is covered by
    # test_biography_pipeline_selects_v5_prompt_when_mode_woven.
    monkeypatch.setattr("chronicler.config.BIOGRAPHY_WORLDBUILDING_MODE", "scene_setter")
    last_event_id = _seed_character_with_events(session, ck3_id=1234, count=3)
    provider = FakeProvider(response_text="Life arc body.")

    outcome = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()

    assert outcome.biography_id is not None
    assert outcome.error is None
    assert outcome.events_through_event_id == last_event_id

    bio = get_latest_biography_for_character(session, 1234)
    assert bio is not None
    assert bio.body == "Life arc body."
    # cs1o: provider tag is kind-resolved (scene_setter → plain biography).
    assert bio.provider == "fake-provider:v1:biography"
    assert bio.prompt_template_version == PROMPT_TEMPLATE_VERSION
    assert bio.events_through_event_id == last_event_id
    assert bio.prompt_tokens == 100
    assert bio.completion_tokens == 50


@pytest.mark.asyncio
async def test_generate_biography_attributes_woven_kind_and_breakdown(
    session: Session,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ck3_chronicler-cs1o: two attribution fixes pinned here.

    1. The persisted ``provider`` tag must reflect the kind that
       actually ran (pipeline.py used to hardcode "biography", so woven
       rows were mis-attributed — corrupting per-provider USD math).
    2. The cache breakdown + cost from NarrativeResponse must survive
       onto the row.
    """
    monkeypatch.setattr("chronicler.config.BIOGRAPHY_WORLDBUILDING_MODE", "woven")
    _seed_character_with_events(session, ck3_id=1234, count=2)
    # Give the character world context so build_briefing selects the
    # woven kind — the block renders only when region_empire_name (or
    # key) resolves (_format_world_context_block).
    upsert_character(
        session,
        ck3_id=1234,
        region_summary_json='{"region_empire_name": "Britannia"}',
    )
    session.commit()
    provider = FakeProvider(
        cache_read_tokens=900,
        cache_write_tokens=100,
        cost_usd=0.123,
    )

    outcome = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()
    assert outcome.error is None

    bio = get_latest_biography_for_character(session, 1234)
    assert bio is not None
    assert provider.seen_requests[-1].kind == "biography_woven"
    assert provider.name_for_kind_calls == ["biography_woven"]
    assert bio.provider == "fake-provider:v1:biography_woven"
    assert bio.cache_read_tokens == 900
    assert bio.cache_write_tokens == 100
    assert bio.cost_usd == 0.123


@pytest.mark.asyncio
async def test_generate_biography_unknown_character_returns_failure_outcome(
    session: Session,
    factory: sessionmaker[Session],
) -> None:
    provider = FakeProvider()
    outcome = await generate_biography(999_999, factory=factory, provider=provider)
    assert outcome.biography_id is None
    assert outcome.error is not None
    assert "unknown character" in outcome.error
    assert provider.seen_requests == []  # provider never called


@pytest.mark.asyncio
async def test_generate_biography_provider_failure_does_not_raise(
    session: Session, factory: sessionmaker[Session], caplog
) -> None:
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider(fail_with=RuntimeError("connection refused"))

    with caplog.at_level(logging.ERROR):
        outcome = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()

    assert outcome.biography_id is None
    assert outcome.error is not None
    assert "RuntimeError" in outcome.error
    # No biography row written
    assert get_latest_biography_for_character(session, 1234) is None


@pytest.mark.asyncio
async def test_user_prompt_includes_chronologically_ordered_events(
    session: Session,
    factory: sessionmaker[Session],
) -> None:
    _seed_character_with_events(session, ck3_id=1234, count=4)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider)

    assert len(provider.seen_requests) == 1
    user_prompt = provider.seen_requests[0].user_prompt
    # Events seeded with dates 1066, 1067, 1068, 1069 — should appear in order.
    pos = [user_prompt.index(f"[{1066 + i:04d}-01-") for i in range(4)]
    assert pos == sorted(pos)


@pytest.mark.asyncio
async def test_warning_logged_when_event_count_exceeds_threshold(
    session: Session, factory: sessionmaker[Session], monkeypatch
) -> None:
    """Above the threshold the pipeline logs a warning and proceeds anyway.

    Patches ``log.warning`` directly because pytest's caplog and global
    logging-handler-based capture are both flaky in the presence of other
    tests that touch logger configuration (CliRunner / Typer).
    """
    from unittest.mock import patch

    monkeypatch.setattr("chronicler.narrative.pipeline.MAX_EVENTS_PER_BIOGRAPHY", 5)
    _seed_character_with_events(session, ck3_id=1234, count=10)
    provider = FakeProvider()

    with patch("chronicler.narrative.pipeline.log.warning") as warn:
        await generate_biography(1234, factory=factory, provider=provider)

    # Look for the threshold-warning message among the captured calls.
    msgs = [args[0] if args else "" for args, _ in [c[1:] for c in warn.mock_calls]]
    flat = "".join(str(m) for m in msgs)
    assert "may exceed context window" in flat
    # Generation still succeeds — the warning is informational
    assert get_latest_biography_for_character(session, 1234) is not None


@pytest.mark.asyncio
async def test_regenerate_increments_version(
    session: Session, factory: sessionmaker[Session]
) -> None:
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider(response_text="v1")

    out1 = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()
    provider.response_text = "v2 — better prompt"
    out2 = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()

    assert out1.biography_id != out2.biography_id
    latest = get_latest_biography_for_character(session, 1234)
    assert latest is not None
    assert latest.version == 2
    assert latest.body == "v2 — better prompt"


@pytest.mark.asyncio
async def test_character_with_zero_events_still_generates(
    session: Session, factory: sessionmaker[Session]
) -> None:
    upsert_character(session, ck3_id=1234, first_name="Edward")
    session.commit()
    provider = FakeProvider(response_text="An obscure life.")

    outcome = await generate_biography(1234, factory=factory, provider=provider)
    session.commit()

    assert outcome.biography_id is not None
    assert outcome.events_through_event_id is None
    bio = get_latest_biography_for_character(session, 1234)
    assert bio is not None
    assert bio.body == "An obscure life."
    # The user prompt should indicate no events recorded
    user_prompt = provider.seen_requests[0].user_prompt
    assert "(none)" in user_prompt


def test_max_events_constant_is_documented() -> None:
    """A regression guard so v0.5 work doesn't silently change the threshold."""
    assert MAX_EVENTS_PER_BIOGRAPHY == 200


@pytest.mark.asyncio
async def test_biography_briefing_uses_titles_map_to_override_stale_other_tier(
    session: Session,
    factory: sessionmaker[Session],
) -> None:
    """ck3_chronicler-7t88: when a character holds a player-decision-formed
    title (x_script_*) whose ``title_created`` event was persisted with
    ``tier='other'`` (CK3 hadn't wired the de_jure_liege chain yet),
    the briefing must recover the correct tier from the character's
    ``primary_title_json`` and render the event line with the
    q1ai-inferred tier. Mirrors the live Örvar Sleggja case: kingdom
    of Norðreyjar formed in 907, payload frozen on 'other', latest
    snapshot has tier='kingdom' in primary_title_json.
    """
    ck3_id = 38137
    upsert_character(
        session,
        ck3_id=ck3_id,
        first_name="Örvar",
        primary_title_json='{"key":"x_script_2517","name":"Norðreyjar","tier":"kingdom"}',
    )
    insert_event_idempotent(
        session,
        schema_version=1,
        event_type="title_created",
        event_date="907.7.1",
        event_date_iso="0907-07-01",
        wall_clock_at="2026-05-13T17:55:50+00:00",
        primary_character_id=ck3_id,
        payload_json=(
            '{"v":1,"t":"title_created","d":"907.7.1","c":38137,'
            '"p":{"tier":"other","title_id":19138,'
            '"title_key":"x_script_2517","title_name":"Norðreyjar"}}'
        ),
        raw_line="title_created-2864",
    )
    session.commit()

    provider = FakeProvider()
    await generate_biography(ck3_id, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    # The briefing line for the kingdom-formation event must read
    # "founded kingdom title", not "founded other title" — the LLM
    # needs the kingdom-tier signal to write "founded the Kingdom of
    # Norðreyjar" in the biography prose.
    assert "founded kingdom title 'Norðreyjar'" in user_prompt
    assert "founded other title" not in user_prompt


# --- ck3_chronicler-lv7: culture-aware biography prompt voice ---


def test_biography_prompt_version_tags_survive_the_template_removal() -> None:
    """Issue #20 removed ``narrative/prompts/*.md``; the version TAGS stay.

    Persisted ``Biography.prompt_template_version`` rows reference these
    strings, and they are how you tell which era of rules produced which
    biography on a regeneration. Changing or dropping one silently
    reinterprets existing history, so they are pinned as literals.

    (ck3_chronicler-8ek slice 1 bumped v2 → v3 for the world-context
    scene-setter; v4 is the withdrawn j7a experiment, which is why the
    woven mode is v5. That trail now lives in git history rather than in
    a directory of files nothing reads.)
    """
    from chronicler.narrative import prompt_builder

    assert PROMPT_TEMPLATE_VERSION == "biography_v3"
    assert prompt_builder._resolve_prompt_version("scene_setter") == "biography_v3"
    assert prompt_builder._resolve_prompt_version("woven") == "biography_v5"
    # a typo'd config must not break generation
    assert prompt_builder._resolve_prompt_version("nonsense") == "biography_v3"


def test_the_in_repo_prompt_templates_are_gone() -> None:
    """The dead loading path is removed, not just unused (issue #20).

    Leaving the files behind is how the register drifted in the first
    place: two copies of the craft rules, one of them unread, and no
    signal about which was live. The rules ship in prose-template/ and
    are read from the user's own prose directory.
    """
    prompts_dir = (
        Path(__file__).parent.parent.parent / "src" / "chronicler" / "narrative" / "prompts"
    )
    assert not prompts_dir.exists(), f"{prompts_dir} should have been removed by #20"


# The shipped template is where the biography rules are load-bearing now,
# so the constraint tests below read it rather than the deleted in-repo
# copies. Content was verified byte-identical at removal time.
_TEMPLATE_VOICE_DIR = Path(__file__).parent.parent.parent / "prose-template" / "voice"


def _prompt_template_body(path: Path | None = None) -> str:
    """Read a shipped voice file — the live source of the craft rules.

    Issue #19 deleted ``prompt_builder._load_system_prompt`` (the in-repo
    templates reached no model); issue #20 deleted the templates. These
    rules are now genuinely load-bearing: the shared assembly reads this
    exact file per generation and every transport ships it verbatim.
    """
    return (path or _TEMPLATE_VOICE_DIR / "biography.md").read_text(encoding="utf-8").strip()


def test_biography_prompt_drops_medieval_europe_anchor() -> None:
    """The opening framing must NOT hard-code 'medieval Europe' as the
    setting. Non-European arcs (Norse, Abbasid, Japanese, etc.) need a
    voice cued from the subject's culture/faith fields, not the prompt."""
    body = _prompt_template_body()
    # Defensive: catch the exact phrasing from v1 *and* casing variants.
    assert "medieval Europe" not in body
    assert "medieval europe" not in body.lower()


# --- ck3_chronicler-6ui: opt-in raw-character-record in prompt ---


@pytest.mark.asyncio
async def test_user_prompt_omits_raw_record_by_default(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """Default behavior: no raw record in the prompt, even if the
    character has one persisted. Opt-in keeps the token cost bounded."""
    upsert_character(
        session,
        ck3_id=1234,
        first_name="Aella",
        save_snapshot_json='{"first_name":"Aella","secret_field":"value"}',
    )
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Full character record from save" not in user_prompt
    assert "secret_field" not in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_includes_raw_record_when_opted_in(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """include_raw_record=True surfaces persisted save_snapshot_json
    in the prompt with the source-of-truth framing so the LLM can
    extract narratively-relevant fields the parser doesn't know."""
    upsert_character(
        session,
        ck3_id=1234,
        first_name="Aella",
        save_snapshot_json='{"first_name":"Aella","culture_specific_field":"value"}',
    )
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider, include_raw_record=True)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Full character record from save" in user_prompt
    assert "culture_specific_field" in user_prompt
    # ck3_chronicler-60m: explicit precedence clause keeps the model from
    # trusting raw first_name / female / nickname over the structured
    # header. Verify the clause is present.
    assert "header above" in user_prompt
    assert "takes precedence" in user_prompt
    # And the family-id-resolution rule: write the name, never the id.
    assert "write the name, never the id" in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_no_raw_record_section_when_opted_in_but_unpersisted(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """If the user opts in but no raw record was persisted (e.g. character
    upserted before fe5/6ui shipped), the prompt should silently skip
    the section rather than emit a stub "Full character record: null"
    that the model would treat as data."""
    upsert_character(session, ck3_id=1234, first_name="Aella")  # no save_snapshot_json
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider, include_raw_record=True)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Full character record from save" not in user_prompt


def test_biography_prompt_instructs_culture_aware_voice() -> None:
    """The new framing must positively instruct the model to take cues
    from the subject's culture/faith — not just remove the old anchor.
    Without an explicit replacement the model defaults back to its
    training-data prior (Western/Latin chronicler)."""
    body = _prompt_template_body().lower()
    assert "culture" in body
    # The acceptance scenario in lv7 names non-European arcs explicitly;
    # the framing should mention or imply that range, not just say
    # "appropriate".
    assert "milieu" in body or "context" in body


# --- ck3_chronicler-30s: gender exposed to biography prompts ---


@pytest.mark.asyncio
async def test_user_prompt_includes_gender_man_when_female_false(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """When the character record stores female=False, the prompt must spell
    out 'Gender: man' so the model doesn't pattern-match pronouns from
    spouse names or 'had_sex' / 'child_born' events."""
    upsert_character(session, ck3_id=1234, first_name="Aella", female=False)
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Gender: man" in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_includes_gender_woman_when_female_true(
    session: Session, factory: sessionmaker[Session]
) -> None:
    upsert_character(session, ck3_id=1234, first_name="Beorhtgyth", female=True)
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Gender: woman" in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_omits_gender_when_unknown(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """No Gender line at all when we have no evidence — better than 'Gender:
    (unknown)' which would invite the model to flag it in prose."""
    upsert_character(session, ck3_id=1234, first_name="Anonymous")
    _seed_character_with_events(session, ck3_id=1234, count=1)
    provider = FakeProvider()
    await generate_biography(1234, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Gender:" not in user_prompt


# --- ck3_chronicler-8ek: world-context scene-setter ---


@pytest.mark.asyncio
async def test_biography_prompt_includes_world_context_block_when_persisted(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """ck3_chronicler-8ek slice 1: when Character.region_summary_json is
    populated, the biography prompt grows a 'World context' block at
    the top with the structured facts and the 2wv-class rails."""
    import json as _json

    summary = {
        "region_empire_key": "e_scandinavia",
        "region_empire_name": "Scandinavia",
        "self_realm": {
            "kingdom_key": "k_sweden",
            "kingdom_name": "Sweden",
            "culture": "norse",
            "faith": "germanic_pagan",
            "ruler_first_name": "Erik",
            "ruler_nickname": None,
        },
        "peers": [
            {
                "kingdom_key": "k_denmark",
                "kingdom_name": "Denmark",
                "culture": "norse",
                "faith": "germanic_pagan",
                "ruler_first_name": "Sven",
                "ruler_nickname": None,
            },
            {
                "kingdom_key": "k_norway",
                "kingdom_name": "Norway",
                "culture": "norse",
                "faith": "germanic_pagan",
                "ruler_first_name": "Magnus",
                "ruler_nickname": None,
            },
        ],
        "cross_currents": [
            {
                "held_title_key": "d_sjaelland",
                "held_title_name": "Sjælland",
                "held_tier": "duchy",
                "de_jure_kingdom_key": "k_denmark",
                "de_jure_kingdom_name": "Denmark",
                "holder_realm_key": "k_norway",
                "holder_realm_name": "Norway",
                "holder_first_name": "Magnus",
            }
        ],
    }
    upsert_character(
        session,
        ck3_id=4242,
        first_name="Erik",
        culture="norse",
        region_summary_json=_json.dumps(summary),
    )
    _seed_character_with_events(session, ck3_id=4242, count=1)
    provider = FakeProvider()
    await generate_biography(4242, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "World context (Scandinavia" in user_prompt
    assert "Self: Sweden — norse, germanic_pagan." in user_prompt
    assert "Denmark" in user_prompt
    assert "Norway" in user_prompt
    assert "ruled by Sven" in user_prompt
    assert "Sjælland" in user_prompt
    assert "de jure of Denmark" in user_prompt
    assert "whose realm is Norway" in user_prompt
    # 2wv-style guardrails on the new block
    assert "Use the kingdom and ruler names exactly as given" in user_prompt
    assert "Do not invent additional realms" in user_prompt
    # The world-context block precedes the character header.
    assert user_prompt.index("World context") < user_prompt.index("Known names:")


@pytest.mark.asyncio
async def test_biography_prompt_omits_world_context_when_summary_missing(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """ck3_chronicler-8ek failure mode: NULL region_summary_json falls
    through to today's prompt without the world-context block. v3
    biography rule 11 instructs the LLM to open as v2-style in this
    case (no scene-setter)."""
    _seed_character_with_events(session, ck3_id=4243, count=1)
    provider = FakeProvider()
    await generate_biography(4243, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "World context" not in user_prompt
    # Header still in place — v2 path is intact.
    assert "Known names: Harold" in user_prompt


@pytest.mark.asyncio
async def test_biography_prompt_omits_world_context_on_malformed_json(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """Defence in depth: a malformed region_summary_json (somehow) does
    NOT 500 the biography pipeline. We fall through to v2 cleanly."""
    upsert_character(
        session,
        ck3_id=4244,
        first_name="Brokensummary",
        culture="english",
        region_summary_json="{not json",
    )
    _seed_character_with_events(session, ck3_id=4244, count=1)
    provider = FakeProvider()
    outcome = await generate_biography(4244, factory=factory, provider=provider)
    assert outcome.error is None
    assert "World context" not in provider.seen_requests[0].user_prompt


# --- ck3_chronicler-7md7: great-cause briefing block ---


@pytest.mark.asyncio
async def test_biography_prompt_includes_great_cause_block_when_persisted(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """When Character.great_cause_json is populated, the biography
    briefing grows a 'Current great cause' block listing the cause,
    target, side, joined date, and any kingdom-tier allies. The
    formatter passes names verbatim and tells the model not to invent
    pope / holy site / treaty details beyond what's recorded."""
    import json as _json

    facts = {
        "war_id": 67108864,
        "casus_belli_type": "undirected_great_holy_war",
        "war_name": "Crusade for Kingdom of Jerusalem",
        "side": "attacker",
        "start_date": "1096.8.1",
        "target_kingdom_key": "k_jerusalem",
        "target_kingdom_name": "Kingdom of Jerusalem",
        "allies": [
            {
                "character_id": 350,
                "first_name": "Bjorn",
                "realm_key": "k_iceland",
                "realm_name": "Iceland",
            }
        ],
    }
    upsert_character(
        session,
        ck3_id=4250,
        first_name="Svend",
        culture="norse",
        great_cause_json=_json.dumps(facts),
    )
    _seed_character_with_events(session, ck3_id=4250, count=1)
    provider = FakeProvider()
    await generate_biography(4250, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "Current great cause (undirected_great_holy_war" in user_prompt
    assert "Crusade for Kingdom of Jerusalem" in user_prompt
    assert "Kingdom of Jerusalem" in user_prompt
    assert "Side: attacker" in user_prompt
    assert "Joined: 1096.8.1" in user_prompt
    assert "Bjorn of Iceland" in user_prompt
    assert "Do not name a pope, holy site, or treaty" in user_prompt
    # The block sits before the character header (after world-context if any).
    assert user_prompt.index("Current great cause") < user_prompt.index("Known names:")


@pytest.mark.asyncio
async def test_biography_prompt_great_cause_block_shifts_to_death_framing(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """ck3_chronicler-85mk: when the character has a death_date AND
    great_cause_json is still populated (cause was active at death,
    didn't conclude), the briefing's headline + closing instruction
    shift from 'Current great cause' to 'Great cause active at
    death'. That cues the LLM to frame the death scene with the
    cause context (fell while still bound for X) rather than
    treating it as ongoing."""
    import json as _json

    facts = {
        "casus_belli_type": "undirected_great_holy_war",
        "war_name": "Crusade for Kingdom of Jerusalem",
        "side": "attacker",
        "start_date": "1096.8.1",
        "target_kingdom_name": "Kingdom of Jerusalem",
    }
    upsert_character(
        session,
        ck3_id=4260,
        first_name="Svend",
        culture="norse",
        death_date="1099.3.4",
        great_cause_json=_json.dumps(facts),
    )
    _seed_character_with_events(session, ck3_id=4260, count=1)
    provider = FakeProvider()
    await generate_biography(4260, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    # Death framing — distinct from the alive-character "Current
    # great cause" headline.
    assert "Great cause active at death" in user_prompt
    assert "Current great cause" not in user_prompt
    # The "Died" line names the unresolved-at-death framing.
    assert "Died: 1099.3.4 (cause unresolved" in user_prompt
    # The closing instruction tells the LLM to frame the death
    # biography around the cause.
    assert "died while bound to the cause above" in user_prompt
    # The cause + target names still surface verbatim.
    assert "Crusade for Kingdom of Jerusalem" in user_prompt
    assert "Kingdom of Jerusalem" in user_prompt


@pytest.mark.asyncio
async def test_biography_prompt_omits_great_cause_when_no_active_cause(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """The common case: NULL great_cause_json (no active crusade). The
    block is absent so the LLM doesn't see crusade language for a
    character who never crusaded."""
    _seed_character_with_events(session, ck3_id=4251, count=1)
    provider = FakeProvider()
    await generate_biography(4251, factory=factory, provider=provider)
    assert "Current great cause" not in provider.seen_requests[0].user_prompt


@pytest.mark.asyncio
async def test_biography_prompt_omits_great_cause_on_malformed_json(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """Defence in depth: a malformed great_cause_json doesn't 500 the
    pipeline. We fall through cleanly."""
    upsert_character(
        session,
        ck3_id=4252,
        first_name="Brokencause",
        culture="english",
        great_cause_json="{not json",
    )
    _seed_character_with_events(session, ck3_id=4252, count=1)
    provider = FakeProvider()
    outcome = await generate_biography(4252, factory=factory, provider=provider)
    assert outcome.error is None
    assert "Current great cause" not in provider.seen_requests[0].user_prompt


def test_upsert_character_explicitly_clears_great_cause_json(
    session: Session,
) -> None:
    """ck3_chronicler-7md7 lifecycle: when a war concludes (CK3 deletes
    it from active_wars), the next save-tail tick passes
    great_cause_json=None to upsert_character. Without the explicit-
    None update path, the stale crusade payload would linger and the
    biography would think the character was still crusading years
    later. Verify the column is actually nulled."""
    from chronicler.db.models import Character

    upsert_character(
        session,
        ck3_id=4253,
        first_name="Svend",
        great_cause_json='{"war_id":1,"casus_belli_type":"papal_crusade"}',
    )
    session.commit()
    row = session.get(Character, 4253)
    assert row is not None and row.great_cause_json is not None

    # War concludes — upsert with explicit None.
    upsert_character(session, ck3_id=4253, great_cause_json=None)
    session.commit()
    session.expire_all()
    row = session.get(Character, 4253)
    assert row is not None
    assert row.great_cause_json is None
    # Sibling field that uses the protect-existing default (region_summary_json)
    # should NOT be clobbered by an unrelated upsert that omits it.
    assert row.first_name == "Svend"


def test_upsert_character_does_not_clobber_other_nullables(
    session: Session,
) -> None:
    """Guardrail for the _CHARACTER_NULLABLE_ON_UPDATE_FIELDS opt-in:
    only fields explicitly listed get the explicit-None semantic.
    region_summary_json should still skip-on-None to protect the
    established value when a caller passes None."""
    from chronicler.db.models import Character

    upsert_character(
        session,
        ck3_id=4254,
        first_name="Erik",
        region_summary_json='{"region_empire_name":"Scandinavia"}',
    )
    session.commit()
    upsert_character(session, ck3_id=4254, region_summary_json=None)
    session.commit()
    session.expire_all()
    row = session.get(Character, 4254)
    assert row is not None
    # region_summary_json was NOT cleared — established value protected.
    assert row.region_summary_json == '{"region_empire_name":"Scandinavia"}'


def test_upsert_character_persists_female(session: Session) -> None:
    from chronicler.db.models import Character

    upsert_character(session, ck3_id=42, first_name="Test", female=True)
    session.commit()
    row = session.get(Character, 42)
    assert row is not None
    assert row.female is True

    upsert_character(session, ck3_id=43, first_name="Test", female=False)
    session.commit()
    row = session.get(Character, 43)
    assert row is not None
    assert row.female is False


def test_upsert_character_female_defaults_null(session: Session) -> None:
    """Characters created without a known gender must keep female=None,
    not silently default to False (which would mis-render unknowns as men)."""
    from chronicler.db.models import Character

    upsert_character(session, ck3_id=99, first_name="Unknown")
    session.commit()
    row = session.get(Character, 99)
    assert row is not None
    assert row.female is None


# --- ck3_chronicler-me4: 8ek slice 2 — woven biography prompt ---


def test_config_biography_worldbuilding_mode_default_is_woven() -> None:
    """me4 → tbrm.5 close: chronicler.config exposes
    BIOGRAPHY_WORLDBUILDING_MODE. Default flipped from 'scene_setter'
    to 'woven' once the strict 1p50 acceptance gate cleared on
    biography_v5 (commit 607ee30, 2026-05-08). 'scene_setter' is
    still selectable for regression A/B."""
    import chronicler.config as cfg

    assert hasattr(cfg, "BIOGRAPHY_WORLDBUILDING_MODE"), (
        "config must expose BIOGRAPHY_WORLDBUILDING_MODE for me4 A/B"
    )
    assert cfg.BIOGRAPHY_WORLDBUILDING_MODE == "woven", (
        f"default mode must be woven (flipped 2026-05-08 after 1p50), got "
        f"{cfg.BIOGRAPHY_WORLDBUILDING_MODE!r}"
    )


@pytest.mark.asyncio
async def test_biography_pipeline_selects_v5_prompt_when_mode_woven(
    session: Session, factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """me4: when BIOGRAPHY_WORLDBUILDING_MODE = 'woven', the pipeline
    loads biography_v5.md as the system prompt and tags both the
    request and the persisted biography row with prompt_template_version
    = 'biography_v5'. Default-mode tests cover scene_setter unchanged."""
    import json as _json

    monkeypatch.setattr("chronicler.config.BIOGRAPHY_WORLDBUILDING_MODE", "woven")

    summary = {
        "region_empire_key": "e_scandinavia",
        "region_empire_name": "Scandinavia",
        "self_realm": {
            "kingdom_key": "k_sweden",
            "kingdom_name": "Sweden",
            "culture": "norse",
            "faith": "germanic_pagan",
        },
        "peers": [],
        "cross_currents": [],
    }
    upsert_character(
        session,
        ck3_id=4250,
        first_name="Erik",
        culture="norse",
        region_summary_json=_json.dumps(summary),
    )
    _seed_character_with_events(session, ck3_id=4250, count=1)
    provider = FakeProvider()
    outcome = await generate_biography(4250, factory=factory, provider=provider)

    assert outcome.error is None
    assert len(provider.seen_requests) == 1
    request = provider.seen_requests[0]
    assert request.prompt_version == "biography_v5", (
        f"expected biography_v5 in woven mode, got {request.prompt_version!r}"
    )
    # The version tag is now the whole signal: issue #19 stopped loading
    # biography_v5.md into system_prompt (every provider discarded it),
    # so there is no prompt-body sentinel to check here. The file's woven
    # constraints are pinned by
    # test_biography_v5_prompt_exists_and_contains_woven_constraints, and
    # what the model actually receives by
    # tests/unit/test_system_prompt_assembly.py.

    bio = get_latest_biography_for_character(session, 4250)
    assert bio is not None
    assert bio.prompt_template_version == "biography_v5"


def test_shipped_woven_voice_file_contains_the_woven_constraints() -> None:
    """me4's woven constraints, now guarded where they actually run.

    Issue #20 moved this from ``narrative/prompts/biography_v5.md`` (which
    no model ever saw after #19) to the shipped
    ``prose-template/voice/biography-woven.md``, which the shared assembly
    reads per generation. Same sentinels, verified byte-identical at the
    time of the move — but a failure here now means real biographies lose
    the rule, not that a dead file drifted.
    """
    path = _TEMPLATE_VOICE_DIR / "biography-woven.md"
    assert path.exists(), f"woven voice file missing at {path}"
    body = path.read_text(encoding="utf-8")
    # Sentinels lifted from the constraint-11 rewrite in the spec.
    assert "Do NOT open with a standalone scene-setter paragraph" in body
    assert "thread the relevant" in body
    assert "Do NOT introduce facts where no recorded event touches them" in body
    # Naming-rail inheritance from v3 (substring chosen to not span a
    # line wrap — the full phrase wraps mid-sentence in the prompt body).
    assert "names exactly as supplied" in body
    assert "do not invent additional realms" in body


# --- ck3_chronicler-nwoc: family-relation labels in briefing glossary ---


def test_build_relation_labels_resolves_basic_family_relations() -> None:
    """nwoc unit: subject's persisted family_data ({id, name}-resolved
    by ck3_chronicler-60m) maps to a flat {cid → relation_label} dict.
    Children render as 'son'/'daughter' when name_genders is known
    and as 'child' otherwise. primary_spouse + spouse[] dedupe to a
    single 'spouse' label per character_id."""
    import json as _json

    from chronicler.narrative.prompt_builder import _build_relation_labels

    snapshot = _json.dumps(
        {
            "family_data": {
                "father": {"id": 33186, "name": "Thrugot"},
                "mother": {"id": 32753, "name": "Thorgunna"},
                "primary_spouse": {"id": 16830070, "name": "Wila"},
                "spouse": [{"id": 16830070, "name": "Wila"}],
                "former_spouses": [{"id": 200111, "name": "Estrith"}],
                "child": [
                    {"id": 78001, "name": "Thrugot"},
                    {"id": 78002, "name": "Christoffer"},
                    {"id": 78003, "name": "Bodil"},
                ],
            }
        }
    )
    name_genders = {
        78001: False,  # son (False = male in the model)
        78002: False,  # son
        78003: True,  # daughter (True = female)
    }
    labels = _build_relation_labels(snapshot, subject_female=False, name_genders=name_genders)
    assert labels[33186] == "father"
    assert labels[32753] == "mother"
    assert labels[16830070] == "spouse"
    assert labels[200111] == "former spouse"
    assert labels[78001] == "son"
    assert labels[78002] == "son"
    assert labels[78003] == "daughter"


def test_build_relation_labels_falls_back_to_child_when_gender_unknown() -> None:
    """When the gender of a child can't be resolved (untracked /
    pruned row), the label degrades to 'child' rather than guessing
    a gender from name."""
    import json as _json

    from chronicler.narrative.prompt_builder import _build_relation_labels

    snapshot = _json.dumps({"family_data": {"child": [{"id": 99999, "name": "Asser"}]}})
    labels = _build_relation_labels(snapshot, subject_female=False, name_genders={})
    assert labels[99999] == "child"


def test_build_relation_labels_returns_empty_when_no_family_data() -> None:
    """Missing or malformed JSON, or absent family_data, falls
    through to an empty map — caller emits bare glossary lines."""
    from chronicler.narrative.prompt_builder import _build_relation_labels

    assert _build_relation_labels(None, subject_female=None) == {}
    assert _build_relation_labels("not json", subject_female=None) == {}
    assert _build_relation_labels("{}", subject_female=None) == {}
    assert _build_relation_labels('{"family_data":[]}', subject_female=None) == {}


@pytest.mark.asyncio
async def test_user_prompt_glossary_carries_father_and_spouse_labels(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """nwoc end-to-end: when the subject's persisted save_snapshot_json
    names a father and a spouse, the briefing glossary lines decorate
    those entries with '(father)' and '(spouse)'. This is the load-
    bearing fix surfaced by the tbrm.5 marquee smoke — without it,
    biographies fell back to 'kinsman' / 'ally'."""
    import json as _json

    upsert_character(
        session,
        ck3_id=36957,
        first_name="Svend",
        nickname="Longshanks",
        save_snapshot_json=_json.dumps(
            {
                "family_data": {
                    "father": {"id": 33186, "name": "Thrugot"},
                    "primary_spouse": {"id": 16830070, "name": "Wila"},
                }
            }
        ),
    )
    upsert_character(
        session,
        ck3_id=33186,
        first_name="Thrugot",
        nickname="the Timid",
    )
    upsert_character(session, ck3_id=16830070, first_name="Wila")
    eid = insert_event_idempotent(
        session,
        schema_version=1,
        event_type="alliance_formed",
        event_date="1072.7.1",
        event_date_iso="1072-07-01",
        wall_clock_at="2026-05-08T12:00:00+00:00",
        primary_character_id=36957,
        # Reference both via vanilla_memory.participants so they enter the
        # glossary through extract_referenced_ids — the names module only
        # picks up specific shapes (participants dict + a known-key set).
        payload_json=(
            '{"v":1,"t":"alliance_formed","c":36957,'
            '"p":{"participants":{"father":33186,"spouse":16830070}}}'
        ),
        raw_line="line-1",
    )
    assert eid is not None
    session.commit()

    provider = FakeProvider()
    await generate_biography(36957, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    assert "33186 = Thrugot, called the Timid (father)" in user_prompt
    assert "16830070 = Wila (spouse)" in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_glossary_unaffected_when_no_family_data(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """Belt-and-braces: a subject whose save_snapshot_json carries no
    family_data still gets bare glossary lines (no '(unknown
    relation)' suffix). Same shape as today's pre-nwoc output."""
    upsert_character(session, ck3_id=42001, first_name="Edward")
    upsert_character(session, ck3_id=42002, first_name="Stranger")
    eid = insert_event_idempotent(
        session,
        schema_version=1,
        event_type="death",
        event_date="1090.1.1",
        event_date_iso="1090-01-01",
        wall_clock_at="2026-05-08T12:00:00+00:00",
        primary_character_id=42001,
        payload_json='{"v":1,"t":"death","c":42001,"p":{"killer":42002}}',
        raw_line="line-d",
    )
    assert eid is not None
    session.commit()

    provider = FakeProvider()
    await generate_biography(42001, factory=factory, provider=provider)
    user_prompt = provider.seen_requests[0].user_prompt
    # Stranger appears in the glossary unadorned — no parenthetical.
    assert "42002 = Stranger" in user_prompt
    assert "42002 = Stranger (" not in user_prompt


def test_build_relation_labels_reverse_scans_for_missing_parent() -> None:
    """ck3_chronicler-u0eu reverse-scan (2026-05-08): live smoke evidence
    showed CK3 saving a character without ``father`` / ``mother`` keys
    on its own family_data — even when the parent IS present in the
    snapshot and lists the subject in their own ``child`` array.

    The renderer-only fix wasn't enough: the briefing glossary stayed
    bare on the parent ID, the model fell back to "ally" again. Pass 2
    of _build_relation_labels scans related characters' family_data
    for ``child`` arrays containing the subject_id, and back-derives
    the parent label using the parent's recorded gender."""
    import json as _json

    from chronicler.narrative.prompt_builder import _build_relation_labels

    # Subject (Svend, 36957) saved without father/mother — only spouse + children.
    subject_snapshot = _json.dumps(
        {
            "family_data": {
                "primary_spouse": {"id": 16830070, "name": "Wila"},
                "child": [{"id": 39853, "name": "Asser"}],
            }
        }
    )
    # Thrugot's snapshot DOES list Svend as his child.
    thrugot_snapshot = _json.dumps(
        {
            "family_data": {
                "child": [
                    {"id": 36957, "name": "Svend"},
                    {"id": 99999, "name": "Other"},
                ],
            }
        }
    )
    related_snapshots = {33186: thrugot_snapshot}
    # Thrugot is male (female=False), so the back-derived label is "father".
    name_genders = {33186: False, 16830070: True, 39853: False}

    labels = _build_relation_labels(
        subject_snapshot,
        subject_id=36957,
        subject_female=False,
        name_genders=name_genders,
        related_snapshots=related_snapshots,
    )
    assert labels[33186] == "father"
    assert labels[16830070] == "spouse"
    assert labels[39853] == "son"


def test_build_relation_labels_reverse_scan_uses_mother_for_female_parent() -> None:
    """Reverse-scan respects the parent's gender — a female parent
    listing the subject as her child gets labelled "mother", not
    "father" or generic "parent"."""
    import json as _json

    from chronicler.narrative.prompt_builder import _build_relation_labels

    subject_snapshot = "{}"
    mother_snapshot = _json.dumps({"family_data": {"child": [{"id": 36957, "name": "Svend"}]}})
    labels = _build_relation_labels(
        subject_snapshot,
        subject_id=36957,
        subject_female=False,
        name_genders={32753: True},  # female parent
        related_snapshots={32753: mother_snapshot},
    )
    assert labels[32753] == "mother"


def test_build_relation_labels_reverse_scan_falls_back_to_parent_when_gender_unknown() -> None:
    """When the back-derived parent's gender isn't in name_genders
    (e.g. the row is pruned), the label degrades to a generic
    "parent" rather than guessing father vs mother."""
    import json as _json

    from chronicler.narrative.prompt_builder import _build_relation_labels

    parent_snapshot = _json.dumps({"family_data": {"child": [{"id": 36957, "name": "Svend"}]}})
    labels = _build_relation_labels(
        "{}",
        subject_id=36957,
        subject_female=False,
        name_genders={},
        related_snapshots={33186: parent_snapshot},
    )
    assert labels[33186] == "parent"


@pytest.mark.asyncio
async def test_user_prompt_world_block_falls_back_to_subject_culture_faith(
    session: Session, factory: sessionmaker[Session]
) -> None:
    """ck3_chronicler-u0eu (2026-05-08, follow-on to 081b): when the
    persisted region_summary_json's self_realm has culture=null and
    faith=null (typical for campaigns sealed BEFORE the layer-1
    summarise_region fallback shipped — their JSON is frozen at the
    pre-fix Nones), the renderer now falls back to the SUBJECT's own
    culture/faith strings. Without this the briefing said just
    "Self: Denmark." (no qualifier), and the model interpreted the
    gap as license to invent a culture/faith change at inheritance.
    """
    import json as _json

    summary = {
        "region_empire_key": "e_scandinavia",
        "region_empire_name": "Scandinavia",
        "self_realm": {
            "kingdom_key": "k_denmark",
            "kingdom_name": "Denmark",
            "culture": None,  # frozen at pre-081b Nones
            "faith": None,
            "ruler_first_name": None,
            "ruler_nickname": None,
        },
        "peers": [],
        "cross_currents": [],
    }
    upsert_character(
        session,
        ck3_id=36957,
        first_name="Svend",
        culture="danish",
        faith="catholic",
        region_summary_json=_json.dumps(summary),
    )
    eid = insert_event_idempotent(
        session,
        schema_version=1,
        event_type="death",
        event_date="1106.6.6",
        event_date_iso="1106-06-06",
        wall_clock_at="2026-05-08T12:00:00+00:00",
        primary_character_id=36957,
        payload_json='{"v":1,"t":"death","c":36957,"p":{}}',
        raw_line="line-d",
    )
    assert eid is not None
    session.commit()
    provider = FakeProvider()
    await generate_biography(36957, factory=factory, provider=provider)

    user_prompt = provider.seen_requests[0].user_prompt
    # The Self line now positively asserts Denmark's culture/faith,
    # closing the contradiction loop the model had been resolving
    # by inventing a culture/faith change.
    assert "Self: Denmark — danish, catholic." in user_prompt


@pytest.mark.asyncio
async def test_user_prompt_includes_death_cluster_block(
    session: Session,
    factory: sessionmaker[Session],
) -> None:
    """ck3_chronicler-qdh8: when relative_died memories cluster in time,
    the biography prompt carries a cluster handle so the LLM can write
    about the deaths as a wave."""
    import json as _json

    ck3_id = 1234
    # count=0 is valid: the range is empty, only the character row is created.
    _seed_character_with_events(session, ck3_id=ck3_id, count=0)

    # Seed three relative_died vanilla_memory events within a 15-day window.
    # Dates match the Thrugot smoke evidence (June 1102 typhus cluster).
    rel_died_events = [
        ("1102.6.2", "Margit", 21, "death_disease_typhus"),
        ("1102.6.16", "Knud", 14, None),
        ("1102.6.17", "Thorgunna", 8, "death_disease_typhus"),
    ]
    for ck3_date, name, age, cause in rel_died_events:
        iso = _ck3_to_iso(ck3_date)
        payload: dict = {
            "memory_type": "relative_died",
            "participants": {"dead_relation": 99},
            "deceased_first_name": name,
            "deceased_age": age,
        }
        if cause is not None:
            payload["deceased_cause"] = cause
        insert_event_idempotent(
            session,
            schema_version=1,
            event_type="vanilla_memory",
            event_date=ck3_date,
            event_date_iso=iso,
            wall_clock_at="2026-05-10T00:00:00+00:00",
            primary_character_id=ck3_id,
            payload_json=_json.dumps(payload),
            raw_line=f"cluster-{ck3_date}",
        )
    session.commit()

    provider = FakeProvider()
    await generate_biography(ck3_id, factory=factory, provider=provider)

    assert len(provider.seen_requests) == 1
    prompt = provider.seen_requests[0].user_prompt
    assert "Recorded death clusters in this character's life:" in prompt
    assert "1102.6.2 → 1102.6.17" in prompt
    assert "3 relatives" in prompt
    assert "Margit (21, death_disease_typhus)" in prompt
    assert "Knud (14, —)" in prompt
    assert "Thorgunna (8, death_disease_typhus)" in prompt


def _ck3_to_iso(ck3_date: str) -> str:
    """Convert '1102.6.2' to '1102-06-02' for event_date_iso column."""
    y, m, d = ck3_date.split(".")
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

"""Tests for the streaming CHRONICLER + scope-dump parser."""

from __future__ import annotations

from chronicler.tailer.parser import (
    IncrementalParser,
    ParsedEvent,
    ParseFailure,
)

ENGINE_PREFIX = "[18:54:27][D][effectimpl.cpp:1110]: "


def _chronicler(body: str) -> str:
    return f"{ENGINE_PREFIX}CHRONICLER|{body}"


def _scope_dump(name: str, ck3_id: int) -> str:
    return (
        f"{ENGINE_PREFIX}{name} (Internal ID: {ck3_id} - Historical ID 5) "
        f"weak (Character - {ck3_id})!"
    )


def _scope_dump_no_hist(name: str, ck3_id: int) -> str:
    """Format used for procedurally-generated characters with no historical ID."""
    return f"{ENGINE_PREFIX}{name} (Internal ID {ck3_id}) weak (Character - {ck3_id})!"


def _feed(parser: IncrementalParser, *lines: str):
    """Feed lines and flush at end so single-event tests don't have to add a
    trailing engine-prefix terminator."""
    out = []
    for line in lines:
        out.extend(parser.feed(line))
    out.extend(parser.flush())
    return out


def test_chronicler_then_scope_dump_emits_event() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14|cause=battle|killer=5678"),
        _scope_dump("Harold of Godwin", 1234),
    )
    assert len(out) == 1
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.t == "death"
    assert out[0].event.c == 1234
    assert out[0].event.p.killer == 5678
    assert out[0].event.p.cause == "battle"


def test_minimal_payload() -> None:
    p = IncrementalParser()
    out = _feed(p, _chronicler("v=1|t=death|d=1066.10.14"), _scope_dump("Harold", 1234))
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.p.killer is None


def test_scope_dump_after_intermediate_lines() -> None:
    """Scope dump may not be the very next line — engine often prefixes a header."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        f"{ENGINE_PREFIX}engine bookkeeping noise",
        f"{ENGINE_PREFIX}more noise",
        _scope_dump("Harold", 1234),
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.c == 1234


def test_first_internal_id_wins() -> None:
    """In on_death the root scope (dying char) is dumped first."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold (root)", 1234),
        "surviving_consort: Edith (Internal ID: 5678 - Historical ID 9) weak (Character - 5678)!",
    )
    assert out[0].event.c == 1234


def test_non_chronicler_lines_alone_emit_nothing() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        f"{ENGINE_PREFIX}engine trace",
        f"{ENGINE_PREFIX}[OTHER_MOD] noise",
        "",
    )
    assert out == []


def test_chronicler_without_scope_dump_times_out() -> None:
    p = IncrementalParser()
    out: list = []
    out.extend(p.feed(_chronicler("v=1|t=death|d=1066.10.14")))
    for _ in range(IncrementalParser.SCOPE_TIMEOUT_LINES):
        out.extend(p.feed(f"{ENGINE_PREFIX}filler"))
    assert len(out) == 1
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "missing_scope"


def test_flush_emits_stranded_pending() -> None:
    p = IncrementalParser()
    p.feed(_chronicler("v=1|t=death|d=1066.10.14"))
    out = p.flush()
    assert len(out) == 1
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "missing_scope"


def test_back_to_back_chronicler_strands_first() -> None:
    """A new CHRONICLER line before scope-dump fails the prior pending."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _chronicler("v=1|t=death|d=1067.01.05"),
        _scope_dump("Harold", 1234),
    )
    assert len(out) == 2
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "missing_scope"
    assert isinstance(out[1], ParsedEvent)
    assert out[1].event.c == 1234
    assert out[1].event.d == "1067.01.05"


def test_invalid_line_format_quarantines() -> None:
    p = IncrementalParser()
    out = _feed(p, _chronicler("v=1|t=death|d=1066.10.14|garbage_segment"))
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "invalid_format"
    # Pending was not started — next line is just discarded
    assert _feed(p, _scope_dump("Harold", 1234)) == []


def test_unknown_event_type_is_schema_violation() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=smarch_horror|d=1066.10.14"),
        _scope_dump("nope", 1234),
    )
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "schema_violation"


def test_unknown_payload_field_is_schema_violation() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14|junk=x"),
        _scope_dump("Harold", 1234),
    )
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "schema_violation"


# --- ck3_chronicler-fkw: ParseFailure surfaces event_kind when known ---


def test_parse_failure_carries_event_kind_for_schema_violation() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14|junk=x"),
        _scope_dump("Harold", 1234),
    )
    assert isinstance(out[0], ParseFailure)
    assert out[0].event_kind == "death"


def test_parse_failure_carries_event_kind_for_missing_scope() -> None:
    p = IncrementalParser()
    p.feed(_chronicler("v=1|t=birth|d=1066.10.14"))
    out = p.flush()
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "missing_scope"
    assert out[0].event_kind == "birth"


def test_parse_failure_invalid_format_has_no_event_kind() -> None:
    """Body that fails _build_envelope never sets envelope['t'] — kind None."""
    p = IncrementalParser()
    out = _feed(p, _chronicler("v=1|t=death|d=1066.10.14|garbage_segment"))
    assert isinstance(out[0], ParseFailure)
    assert out[0].reason == "invalid_format"
    assert out[0].event_kind is None


def test_word_boundary_required() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        f"{ENGINE_PREFIX}XCHRONICLER|v=1|t=death|d=1066.10.14",
        _scope_dump("Harold", 1234),
    )
    # Substring shouldn't match — line is ignored, so no event.
    assert out == []


def test_legacy_bracketed_chronicler_returns_none() -> None:
    """Earlier wire-format attempts used [CHRONICLER]; the loc engine
    emitted ERROR:[CHRONICLER]. Those don't match the bare-tag regex so
    they're ignored rather than quarantined."""
    p = IncrementalParser()
    out = _feed(
        p,
        f"{ENGINE_PREFIX}ERROR:[CHRONICLER]|v=1|t=death|d=|c=ERROR",
        f"{ENGINE_PREFIX}ERROR:[CHRONICLER] {{",
    )
    assert out == []


def test_value_containing_equals_keeps_full_value() -> None:
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14|cause=heart=attack"),
        _scope_dump("Harold", 1234),
    )
    assert out[0].event.p.cause == "heart=attack"


def test_scope_dump_without_historical_id_works() -> None:
    """AI-generated characters get '(Internal ID NNN)' (no colon, no hist)."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump_no_hist("Adherbal Lowborn", 42143),
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.c == 42143


# --- V02-P01 participant extraction ---


def _saved_event_target(role: str, name: str, ck3_id: int) -> str:
    return f"{role}: {name} (Internal ID: {ck3_id} - Historical ID 9) weak (Character - {ck3_id})!"


def test_event_targets_section_extracts_named_participants() -> None:
    """The 'Saved event targets:' block produces (role, char_id) participants."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold (root)", 1234),
        "Root: Harold (root) (Internal ID: 1234) weak (Character - 1234)!",
        "",
        "Saved event targets:",
        _saved_event_target("surviving_consort", "Edith", 5678),
        _saved_event_target("dead_character", "Harold", 1234),
        _saved_event_target("liege_of_dead_character_councillor", "Philippe", 9999),
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.c == 1234
    assert ("surviving_consort", 5678) in out[0].participants
    assert ("dead_character", 1234) in out[0].participants
    assert ("liege_of_dead_character_councillor", 9999) in out[0].participants


def test_non_character_scope_lines_filtered() -> None:
    """Character_memory and scalar lines (e.g. 'stress: 40.00') must not yield participants."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold", 1234),
        "",
        "Saved event targets:",
        "new_memory: spouse died weak (Character_memory - 2253)!",  # not a Character
        "deceased_character_stress: 40.00",  # scalar, no Internal ID
        _saved_event_target("surviving_consort", "Edith", 5678),
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].participants == (("surviving_consort", 5678),)


def test_list_targets_section_extracts_each_item() -> None:
    """A 'Saved list targets:' label collects subsequent bare character lines."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold", 1234),
        "",
        "Saved list targets:",
        "send_death_management_0002:",
        "Edith (Internal ID: 5678 - Historical ID 9) weak (Character - 5678)!",
        "Agathe (Internal ID: 9012 - Historical ID 9) weak (Character - 9012)!",
        "Richard (Internal ID: 3456 - Historical ID 9) weak (Character - 3456)!",
    )
    assert isinstance(out[0], ParsedEvent)
    expected = {
        ("send_death_management_0002", 5678),
        ("send_death_management_0002", 9012),
        ("send_death_management_0002", 3456),
    }
    assert set(out[0].participants) >= expected


def test_blank_line_resets_list_label() -> None:
    """A blank line between two list labels prevents items from one being
    misattributed to the previous label."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold", 1234),
        "",
        "Saved list targets:",
        "first_list:",
        "Alice (Internal ID: 100) weak (Character - 100)!",
        "",
        "second_list:",
        "Bob (Internal ID: 200) weak (Character - 200)!",
    )
    assert isinstance(out[0], ParsedEvent)
    parts = dict(out[0].participants)
    assert parts.get("first_list") == 100 or ("first_list", 100) in out[0].participants
    assert ("second_list", 200) in out[0].participants
    assert ("first_list", 200) not in out[0].participants


def test_interleaved_engine_trace_before_dump_does_not_terminate() -> None:
    """Other CK3 trace lines (music_manager warnings etc.) may interleave
    between the CHRONICLER tag and the actual scope-dump start. They are
    engine-prefixed but contain no Internal ID, so the parser must skip
    them rather than treating them as the dump start."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        f"{ENGINE_PREFIX}QueueCue failed, current track is uninterruptable",
        f"{ENGINE_PREFIX}some other unrelated trace",
        _scope_dump("Harold", 1234),
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].event.c == 1234


def test_participants_deduped() -> None:
    """The same (role, id) pair shouldn't appear twice in the output."""
    p = IncrementalParser()
    out = _feed(
        p,
        _chronicler("v=1|t=death|d=1066.10.14"),
        _scope_dump("Harold", 1234),
        "",
        "Saved event targets:",
        _saved_event_target("dead_character", "Harold", 1234),
        _saved_event_target("dead_character", "Harold", 1234),  # duplicate
    )
    assert isinstance(out[0], ParsedEvent)
    assert out[0].participants.count(("dead_character", 1234)) == 1

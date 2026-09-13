"""Unit tests for the narrative name-resolution glossary extractor
(ck3_chronicler-27ov.22 / audit M-N2).

The biography + memory prompts resolve character ids in event payloads to
readable names via a glossary built by :func:`extract_referenced_ids`. If a
payload key holding a character id is not recognised by the extractor, the id
never enters the glossary and the renderer falls back to "id N" — in a prompt
whose instructions say "use these names, never the IDs". The extractor must
therefore recognise every character-id key the renderers actually read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from chronicler.narrative import event_rendering
from chronicler.narrative.names import extract_referenced_ids


@dataclass
class _FakeEvent:
    payload_json: str


def _payload(p: dict) -> _FakeEvent:
    return _FakeEvent(payload_json=json.dumps({"v": 1, "t": "x", "c": 1, "p": p}))


def test_extract_referenced_ids_picks_up_real_diff_payload_keys() -> None:
    """The real diff.py payload shapes (not the participants workaround) must
    enter the glossary. Pre-fix the predicate only matched endswith('_character')
    + a tiny set, so spouse_character_id / from_holder_id / host_id / ... were
    all dropped and rendered as bare ids."""
    cases = {
        "ally_character_id": 1001,
        "spouse_character_id": 1002,
        "former_spouse_character_id": 1003,
        "from_holder_id": 1004,
        "to_holder_id": 1005,
        "host_id": 1006,
        "employer_id": 1007,
        "employee_id": 1008,
        "concubine_id": 1009,
        "assumed_father_id": 1010,
        "artisan_character_id": 1011,
        "killer": 1012,
    }
    for key, cid in cases.items():
        assert cid in extract_referenced_ids([_payload({key: cid})]), (
            f"extractor dropped character-id key {key!r} (audit M-N2)"
        )


def test_extract_referenced_ids_still_reads_participants_dict() -> None:
    """The vanilla_memory participants {role: id} shape must keep working."""
    ev = _payload({"participants": {"father": 33186, "spouse": 16830070}})
    assert extract_referenced_ids([ev]) == {33186, 16830070}


def test_extract_referenced_ids_ignores_non_character_id_keys() -> None:
    """title_id / location_id / memory_id are NOT character ids — they must
    stay out of the glossary so the LLM isn't handed a title id as a person."""
    ev = _payload({"title_id": 500, "location_id": 600, "memory_id": 700})
    assert extract_referenced_ids([ev]) == set()


def _character_id_keys_read_by_renderers() -> set[str]:
    """Scan event_rendering.py for every payload key whose value is resolved to
    a name via _name(...): either _name(p.get("KEY")) directly, or
    VAR = p.get("KEY") followed by _name(VAR). This is the source of truth the
    extractor must cover."""
    src = Path(event_rendering.__file__).read_text(encoding="utf-8")
    direct = set(re.findall(r'_name\(\s*p\.get\(\s*["\'](\w+)["\']', src))
    var_to_key = dict(re.findall(r'(\w+)\s*=\s*p\.get\(\s*["\'](\w+)["\']', src))
    used_vars = set(re.findall(r"_name\(\s*([a-zA-Z_]\w*)", src))
    indirect = {var_to_key[v] for v in used_vars if v in var_to_key}
    return direct | indirect


def test_extractor_covers_every_character_key_event_rendering_reads() -> None:
    """Drift guard (27ov.22 / audit M-N2): cross-check the extractor against the
    keys the renderers actually resolve. A new renderer that reads a new
    character-id key fails this until extract_referenced_ids learns it."""
    keys = _character_id_keys_read_by_renderers()
    # Sanity: the scan found the known fan-out, not nothing.
    assert "ally_character_id" in keys and "host_id" in keys, keys
    missing = {key for key in keys if 4242 not in extract_referenced_ids([_payload({key: 4242})])}
    assert not missing, (
        f"extract_referenced_ids does not cover renderer keys {sorted(missing)} "
        "— they will render as 'id N' in prompts (audit M-N2)"
    )

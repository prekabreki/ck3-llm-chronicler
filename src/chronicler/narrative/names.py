"""Shared name-resolution helpers for the narrative layer.

The biography pipeline (:mod:`chronicler.narrative.pipeline`) maps
character IDs that appear in event payloads to readable names —
otherwise the LLM sees prompts full of bare integers like "15100".

Originally promoted from the memory consolidator's
``_extract_referenced_ids`` (F012 / 9un); the memories module itself
was demolished in v0.12, leaving the pipeline as the sole consumer
(27ov.43 fixed this stale reference).
"""

from __future__ import annotations

import json
from typing import Protocol

# ck3_chronicler-27ov.22 (audit M-N2): int-valued payload keys that hold a
# character id and so must enter the name-resolution glossary. Anything ending
# in '_character_id' is matched by suffix (ally_character_id,
# spouse_character_id, former_spouse_character_id, artisan_character_id, ...);
# these are the remaining id-shaped keys event_rendering's renderers resolve via
# _name(). Title/location/memory ids are deliberately excluded — they are NOT
# people. Kept in sync with the renderers by
# tests/unit/test_names.py::test_extractor_covers_every_character_key_event_rendering_reads.
_CHARACTER_ID_PAYLOAD_KEYS = frozenset(
    {
        "from_holder_id",
        "to_holder_id",
        "host_id",
        "employer_id",
        "employee_id",
        "concubine_id",
        "assumed_father_id",
        "killer",
    }
)


class _HasPayloadJson(Protocol):
    """Structural type: any snapshot record exposing a JSON payload string.

    Both ``pipeline._EventSnapshot`` and ``memories._EventSnapshot`` satisfy
    this — neither shares a base class but they shape-match here.
    """

    payload_json: str


def extract_referenced_ids(events: list[_HasPayloadJson]) -> set[int]:
    """Pull every character ID referenced from event payloads.

    Walks the parsed payload's ``p.participants`` dict (when present)
    and any other ``*_id``-shaped keys whose value is an int. Used to
    build a name-resolution glossary for the prompt so the LLM sees
    readable names instead of raw numbers like 15100.
    """
    ids: set[int] = set()
    for e in events:
        try:
            payload = json.loads(e.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        p = payload.get("p") if isinstance(payload, dict) else None
        if not isinstance(p, dict):
            continue
        # vanilla_memory.participants is {role: id}
        participants = p.get("participants")
        if isinstance(participants, dict):
            for v in participants.values():
                if isinstance(v, int):
                    ids.add(v)
        # ck3_chronicler-27ov.22 (audit M-N2): catch every int-valued character
        # id the renderers resolve — keys suffixed '_character_id' plus the
        # explicit non-suffixed set. The old predicate matched
        # endswith('_character') + {killer, child, spouse, ...}, which hit NONE
        # of diff.py's actual keys (spouse_character_id, from_holder_id,
        # host_id, ...) except 'killer' — so the glossary silently dropped them
        # and prompts rendered 'id 38379'.
        for k, v in p.items():
            if isinstance(v, int) and (
                k.endswith("_character_id") or k in _CHARACTER_ID_PAYLOAD_KEYS
            ):
                ids.add(v)
    return ids

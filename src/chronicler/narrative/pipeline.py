"""Biography generation orchestrator.

The single entry point :func:`generate_biography` reads a character's
event log from the per-campaign DB, builds a :class:`NarrativeRequest`
from a chronological event serialisation plus the system prompt
assembled from the prose dir (issue #19), calls the configured
:class:`NarrativeProvider`, and persists the result via the v0.2
``biographies`` repository helpers.

Prompt assembly itself lives in :mod:`chronicler.narrative.prompt_builder`
(ck3_chronicler-05rs): this module owns only the three-phase transaction
discipline and calls :func:`prompt_builder.build_briefing` between the
read and the provider call. A biography template change (v3 → v5 →
world-context) edits the prompt builder, not this orchestrator.

Three discrete phases each acquire a session for a brief moment and
release it before the next phase. The session is **never held during
the LLM call** — that's load-bearing for the auto-trigger use case
where dozens of biographies might queue: each holds a session for at
most milliseconds, not minutes, so the connection pool can't
exhaust. Verified against the v0.2 smoke (5,500-event session) where
the prior session-spanning design crashed with ``QueuePool limit
reached`` after the first 15 deaths.

Phases:
1. **Read** — open a session, snapshot the character + events into
   plain dataclasses (a :class:`prompt_builder.BiographyInputs`), close
   the session.
2. **Generate** — call ``provider.generate(request)`` with no DB
   connection held. This is the slow part (seconds to minutes per
   call on a 14B local model).
3. **Persist** — open a fresh session, insert the biography row,
   close.

Failures from the provider are caught + logged; no biography row is
written, and no exception propagates out of the ingest layer. That's
load-bearing for V02-N04 (auto-trigger on death) where biography
generation must never block ingest.

Token-budget guard: if a character's event log exceeds
``MAX_EVENTS_PER_BIOGRAPHY`` (default 200), a WARNING is logged. The
real summarisation pass (a "memory consolidation" lite) lands at v0.5
when the yearly_pulse pipeline arrives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from chronicler.db.engine import session_scope
from chronicler.db.repository import (
    get_character,
    insert_biography,
    list_events_for_character,
)

# Prompt assembly moved to prompt_builder (ck3_chronicler-05rs).
# ck3_chronicler-27ov.77 (L10): the historical re-export aliases
# (_PROMPT_PATH, PROMPT_TEMPLATE_VERSION, _build_relation_labels) are
# gone — import them from prompt_builder. Issue #19 deleted
# _load_system_prompt outright: the register comes from the prose dir.
from chronicler.narrative.prompt_builder import (
    BiographyInputs,
    CharacterSnapshot,
    EventSnapshot,
    build_briefing,
)
from chronicler.narrative.prose_io import assemble_system_prompt
from chronicler.narrative.provider import NarrativeProvider, NarrativeRequest

log = logging.getLogger(__name__)

MAX_EVENTS_PER_BIOGRAPHY = 200


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """Result of an attempted biography generation."""

    biography_id: int | None
    error: str | None
    events_through_event_id: int | None


def _snapshot_character_and_events(
    factory: sessionmaker[Session], character_id: int
) -> BiographyInputs | None:
    """Phase 1: read into detached snapshots, release the session.

    Returns a :class:`prompt_builder.BiographyInputs` bundling the
    character + event snapshots and the lookups prompt assembly needs:
    a ``names_map`` for the glossary block, a ``name_genders`` map, and
    each related character's ``save_snapshot_json`` for the relation
    reverse-scan. Returns ``None`` when the character is unknown.

    Relation-label *inference* is no longer done here — it is prompt
    shaping and lives in :func:`prompt_builder.build_briefing`
    (ck3_chronicler-05rs). This phase only gathers the raw materials.
    """
    with session_scope(factory) as session:
        character = get_character(session, character_id)
        if character is None:
            return None
        events = list_events_for_character(session, character_id)
        snap_char = CharacterSnapshot(
            ck3_id=character.ck3_id,
            first_name=character.first_name,
            dynasty_name=character.dynasty_name,
            nickname=character.nickname,
            female=character.female,
            birth_date=character.birth_date,
            death_date=character.death_date,
            culture=character.culture,
            faith=character.faith,
            raw_record_json=character.save_snapshot_json,
            region_summary_json=character.region_summary_json,
            great_cause_json=character.great_cause_json,
            primary_title_json=character.primary_title_json,
        )
        snap_events = [
            EventSnapshot(
                id=e.id,
                event_type=e.event_type,
                event_date=e.event_date,
                event_date_iso=e.event_date_iso,
                payload_json=e.payload_json,
            )
            for e in events
        ]

        # Names glossary: resolve every character referenced from event
        # payloads (death.killer, marriage.spouse, vanilla_memory's
        # participants dict, etc.). One batched query.
        from chronicler.narrative.names import extract_referenced_ids

        ref_ids = extract_referenced_ids(snap_events) | {character_id}
        names_map: dict[int, str] = {}
        # ck3_chronicler-nwoc: gender map keyed by the same ref_ids so the
        # relation builder can resolve "child" → "son" / "daughter" when
        # we know the gender, without a second pass over the DB.
        name_genders: dict[int, bool | None] = {}
        # ck3_chronicler-u0eu reverse-scan (2026-05-08): also collect each
        # related character's save_snapshot_json so build_briefing's
        # relation inference can back-derive parent links the subject's
        # own record omits (CK3's family_data is asymmetric: a character
        # may know its children without listing its parents). Same query,
        # one extra column read.
        related_snapshots: dict[int, str] = {}
        if ref_ids:
            from sqlalchemy import select as _select

            from chronicler.db.models import Character as _Character

            rows = (
                session.execute(_select(_Character).where(_Character.ck3_id.in_(ref_ids)))
                .scalars()
                .all()
            )
            for row in rows:
                if row.first_name:
                    label = row.first_name
                    if row.nickname:
                        label = f"{label}, called {row.nickname}"
                    names_map[row.ck3_id] = label
                name_genders[row.ck3_id] = row.female
                if row.save_snapshot_json:
                    related_snapshots[row.ck3_id] = row.save_snapshot_json

        return BiographyInputs(
            character=snap_char,
            events=snap_events,
            names_map=names_map,
            name_genders=name_genders,
            related_snapshots=related_snapshots,
        )


async def generate_biography(
    character_id: int,
    *,
    factory: sessionmaker[Session],
    provider: NarrativeProvider,
    include_raw_record: bool = False,
    campaign_uuid: str | None = None,
) -> GenerationOutcome:
    """Generate and persist a biography for the given character.

    Takes a session **factory**, not a session — the function opens its
    own short-lived sessions for read and write, never holding one
    during the LLM call.

    Returns :class:`GenerationOutcome`. On any failure (unknown
    character, provider exception) returns with ``biography_id = None``
    and the failure message in ``error`` — never raises.

    ``include_raw_record`` (ck3_chronicler-6ui): when True, append the
    character's full rakaly record (as persisted in
    ``Character.save_snapshot_json``) to the user prompt so the LLM can
    reach culture-specific or otherwise-uncurated fields. Default False
    because the blob is large (~5-15 KB per character) and prompt
    iteration / A/B evaluation is needed before flipping it on broadly.

    ``campaign_uuid`` (ck3_chronicler-tbrm.1): the per-campaign DB
    UUID, threaded through to ``NarrativeRequest.metadata`` so the
    ClaudeCodeProvider can compute the correct briefing/biography
    paths under ``briefings/<uuid>/<char_id>-vN.md``. None is accepted
    for callers without campaign context (tests, ad-hoc CLI runs); it
    lands as the literal ``"_unscoped"`` bucket in the prose repo.
    """
    # Phase 1: read.
    inputs = _snapshot_character_and_events(factory, character_id)
    if inputs is None:
        log.warning("biography requested for unknown character: %d", character_id)
        return GenerationOutcome(
            biography_id=None,
            error=f"unknown character: {character_id}",
            events_through_event_id=None,
        )

    if len(inputs.events) > MAX_EVENTS_PER_BIOGRAPHY:
        log.warning(
            "character %d has %d events (>%d) — biography may exceed context window",
            character_id,
            len(inputs.events),
            MAX_EVENTS_PER_BIOGRAPHY,
        )

    # Assemble the prompt (pure; no DB session held). build_briefing
    # picks the prompt path + version from the mode config and infers
    # the request kind (biography vs biography_woven).
    # ck3_chronicler-me4: read the mode at call time (not import time) so
    # monkeypatching the config in tests works.
    import chronicler.config as _cfg

    _mode = getattr(_cfg, "BIOGRAPHY_WORLDBUILDING_MODE", "scene_setter")
    briefing = build_briefing(
        inputs,
        mode=_mode,
        include_raw_record=include_raw_record,
    )

    last_event_id = inputs.events[-1].id if inputs.events else None

    metadata = {"character_id": str(character_id)}
    if campaign_uuid:
        metadata["campaign_uuid"] = campaign_uuid

    # Phase 2: generate. No DB session held during this call.
    # Issue #19: the system prompt is assembled here, provider-neutrally,
    # from the prose dir the transport declares — register + the kind's
    # voice file. Inside the error net on purpose: a missing prose dir
    # raises, and that has to become a GenerationOutcome error (a failed
    # queue item the user sees) rather than an exception escaping into
    # the ingest layer, which must never block on biography generation.
    try:
        request = NarrativeRequest(
            kind=briefing.kind,
            prompt_version=briefing.prompt_version,
            system_prompt=assemble_system_prompt(
                prose_repo=provider.prose_repo_path, kind=briefing.kind
            ),
            user_prompt=briefing.user_prompt,
            metadata=metadata,
        )
        response = await provider.generate(request)
    except Exception as e:
        log.exception("biography generation failed for character %d", character_id)
        return GenerationOutcome(
            biography_id=None,
            error=f"{type(e).__name__}: {e}",
            events_through_event_id=last_event_id,
        )

    # Phase 3: persist.
    with session_scope(factory) as session:
        biography = insert_biography(
            session,
            character_id=character_id,
            body=response.text,
            prompt_template_version=briefing.prompt_version,
            # ck3_chronicler-cs1o: attribute the kind that actually ran —
            # this was hardcoded "biography", mis-attributing woven rows
            # (and corrupting per-provider USD aggregation).
            provider=provider.name_for_kind(briefing.kind),
            model=response.model,
            events_through_event_id=last_event_id,
            generated_at=datetime.now(UTC).isoformat(),
            prompt_tokens=response.input_tokens,
            completion_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            cost_usd=response.cost_usd,
        )
        biography_id = biography.id
        biography_version = biography.version
    log.info(
        "biography generated for character %d: bio_id=%d version=%d events=%d",
        character_id,
        biography_id,
        biography_version,
        len(inputs.events),
    )
    return GenerationOutcome(
        biography_id=biography_id,
        error=None,
        events_through_event_id=last_event_id,
    )

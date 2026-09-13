"""Per-campaign DB repository helpers.

These functions operate on an open ``Session`` and never raise out of the
ingest loop on data-shape errors. The idempotency UNIQUE on ``events``
(see docs/architecture.md) means re-inserting the same event silently
succeeds; this is intentional so the tailer can replay a stale offset
without producing duplicate rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from chronicler.cost import TokenUsdBucket, new_token_usd_bucket
from chronicler.db.models import (
    Biography,
    Character,
    CharacterCoaHistory,
    Event,
    EventParticipant,
    Quarantine,
    SchemaMeta,
)

# Fields on Character that are safe to overwrite when we learn a fresher value.
_CHARACTER_UPDATABLE_FIELDS = (
    "first_name",
    "dynasty_name",
    "house_name",
    "nickname",
    "female",
    "birth_date",
    "death_date",
    "culture",
    "faith",
    "last_seen_event_id",
    "save_snapshot_json",
    "coa_json",
    "region_summary_json",
    "great_cause_json",
    "primary_title_json",
    "held_titles_json",
)

# ck3_chronicler-7md7: fields whose ``None`` is a meaningful value (the
# great cause cleared, the trait dropped, etc.) rather than "caller
# didn't have a fresh value to write". These are included in update_set
# even when None — distinguishes between "caller didn't pass this field"
# (key absent from kwargs → still skipped) and "caller passed None
# explicitly" (key present, value None → write the NULL).
_CHARACTER_NULLABLE_ON_UPDATE_FIELDS = frozenset({"great_cause_json"})


def upsert_character(session: Session, ck3_id: int, **fields: Any) -> Character:
    """Insert or update a character row by CK3 ID.

    Unknown kwargs are ignored (forgiving — the parser may pass a partial
    set depending on what the event populated). Returns the persisted
    ``Character`` row.
    """
    payload = {k: v for k, v in fields.items() if k in _CHARACTER_UPDATABLE_FIELDS}
    payload["ck3_id"] = ck3_id

    update_set = {
        k: payload[k]
        for k in payload
        if k != "ck3_id" and (payload[k] is not None or k in _CHARACTER_NULLABLE_ON_UPDATE_FIELDS)
    }

    stmt = sqlite_insert(Character).values(**payload)
    if update_set:
        stmt = stmt.on_conflict_do_update(index_elements=[Character.ck3_id], set_=update_set)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Character.ck3_id])
    session.execute(stmt)
    session.flush()

    character = session.get(Character, ck3_id)
    if character is None:
        raise RuntimeError(f"upsert_character invariant: row {ck3_id} missing after flush")
    return character


def insert_event_idempotent(
    session: Session,
    *,
    schema_version: int,
    event_type: str,
    event_date: str,
    wall_clock_at: str,
    primary_character_id: int,
    payload_json: str,
    raw_line: str,
    event_date_iso: str | None = None,
    participants: Iterable[tuple[int, str]] | None = None,
) -> int | None:
    """Insert an event, treating idempotency-collisions as no-ops.

    Returns the new event id, or ``None`` if the row was a duplicate (the
    UNIQUE on ``(event_type, event_date, primary_character_id, payload_json)``
    fired). ``participants`` is an iterable of ``(character_id, role)``.
    ``event_date_iso`` is the sortable normalisation of ``event_date`` (see
    :func:`chronicler.util.dates.parse_ck3_long_date`); ``None`` if the
    source string couldn't be parsed.
    """
    stmt = sqlite_insert(Event).values(
        schema_version=schema_version,
        event_type=event_type,
        event_date=event_date,
        event_date_iso=event_date_iso,
        wall_clock_at=wall_clock_at,
        primary_character_id=primary_character_id,
        payload_json=payload_json,
        raw_line=raw_line,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["event_type", "event_date", "primary_character_id", "payload_json"],
    ).returning(Event.id)
    result = session.execute(stmt).fetchone()
    if result is None:
        return None
    event_id = int(result[0])

    if participants:
        for character_id, role in participants:
            session.execute(
                sqlite_insert(EventParticipant)
                .values(event_id=event_id, character_id=character_id, role=role)
                .on_conflict_do_nothing()
            )

    session.flush()
    return event_id


def insert_quarantine(
    session: Session,
    *,
    raw_line: str,
    error: str,
    ts: str,
    event_kind: str | None = None,
) -> int:
    quarantine_row = Quarantine(raw_line=raw_line, error=error, ts=ts, event_kind=event_kind)
    session.add(quarantine_row)
    session.flush()
    if quarantine_row.id is None:
        raise RuntimeError("record_quarantine invariant: quarantine.id missing after flush")
    return quarantine_row.id


def get_character(session: Session, ck3_id: int) -> Character | None:
    return session.get(Character, ck3_id)


def character_name_map(session: Session) -> dict[int, str]:
    """{ck3_id: first_name} for every character with a non-NULL first_name.

    ck3_chronicler-2cc.1: feeds the chronicle export's event-roll name
    resolution so participants (allies, spouses, employers, hosts) render
    as names rather than raw ids. One indexed scan; called once per export.
    """
    rows = session.execute(
        select(Character.ck3_id, Character.first_name).where(Character.first_name.is_not(None))
    )
    return {ck3_id: name for ck3_id, name in rows}


def append_coa_history_if_changed(
    session: Session,
    *,
    character_id: int,
    coa_json: str,
    observed_at: str,
) -> bool:
    """Append a CoA history row when ``coa_json`` differs from the latest.

    Returns True iff a new row was inserted. Idempotent — calling on an
    unchanged CoA is a cheap no-op (one indexed lookup, no insert).
    Compares on the canonical JSON form the caller already provides;
    callers should pass coa_json built with the same separators across
    ticks (the diff layer already does — ``json.dumps(coa, separators=(",", ":"))``).

    ck3_chronicler-7b8d.
    """
    latest = session.execute(
        select(CharacterCoaHistory.coa_json)
        .where(CharacterCoaHistory.character_id == character_id)
        .order_by(CharacterCoaHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest == coa_json:
        return False
    session.add(
        CharacterCoaHistory(
            character_id=character_id,
            observed_at=observed_at,
            coa_json=coa_json,
        )
    )
    session.flush()
    return True


def list_coa_history(session: Session, character_id: int) -> list[CharacterCoaHistory]:
    """Return all CoA history rows for ``character_id``, oldest first."""
    return list(
        session.execute(
            select(CharacterCoaHistory)
            .where(CharacterCoaHistory.character_id == character_id)
            .order_by(CharacterCoaHistory.id.asc())
        ).scalars()
    )


def get_character_coa_json(session: Session, ck3_id: int) -> str | None:
    """ck3_chronicler-a3f: raw JSON string from ``Character.coa_json``.

    Returns the as-stored JSON string (callers parse if they need a
    dict). None when the character row is missing or its coa_json is
    NULL — both are normal early-campaign states (the row may not
    exist yet, or save-tail hasn't refreshed the tracked set since the
    7ao migration). Used by the Library card to render real heraldry
    when available, falling back to the procedural Banner otherwise."""
    row = session.execute(select(Character.coa_json).where(Character.ck3_id == ck3_id)).first()
    if row is None:
        return None
    coa_json: str | None = row[0]
    return coa_json


# audit L21: cache the played-character walk keyed on (db, player, max
# event id). Bounded; a fresh process starts cold. Cleared wholesale when
# it grows past the cap — recompute is cheap and the working set is one
# entry per open campaign.
_PLAYED_CHARACTER_CACHE: dict[tuple[str, int, int], frozenset[int]] = {}
_PLAYED_CHARACTER_CACHE_MAX = 64


def compute_played_character_ids(session: Session, current_player_id: int | None) -> set[int]:
    """ck3_chronicler-w1t3: derive the set of characters who have ever
    been the campaign player by walking the title-inheritance chain
    backward from the latest known player.

    Bootstraps from ``current_player_id`` (the registry's
    ``current_player_character_id`` — the LATEST player, refreshed every
    save-tail tick at ``save/overview.py:resolve_player_identity``). Iteratively
    chases ``from_holder_id`` through duchy+ ``title_acquired`` events
    until the chain runs dry. Predecessors that the diff layer wrote a
    ``from_holder_id`` for were holders of the same title before the
    current player's lineage acquired it — by definition, players at the
    moment that title was the player's primary.

    Returns at minimum ``{current_player_id}`` when one is set;
    transitively expanded by event walking. Empty set when the campaign
    has no current player (pre-launch state, archived-empty edge cases).

    This implements the ticket's recommended approach (2): works
    retroactively against any campaign that has a populated events table,
    no new schema concept needed. The walk is bounded (per-campaign
    events are ~thousands; the title-tier filter narrows further), and
    a depth cap prevents pathological loops on malformed payloads.
    """
    if current_player_id is None:
        return set()

    # audit L21: events are append-only, so for a fixed player the walk's
    # result can only change when a new event lands — memoize on the max
    # event id rather than re-scanning title_acquired on every request
    # (typeahead keystrokes included).
    db_key = str(session.get_bind().url)
    max_event_id = int(session.execute(select(func.max(Event.id))).scalar() or 0)
    cache_key = (db_key, current_player_id, max_event_id)
    cached = _PLAYED_CHARACTER_CACHE.get(cache_key)
    if cached is not None:
        return set(cached)

    # ck3_chronicler-wak4: ix_events_event_type backs the event_type
    # exact-match. The tier filter is pushed into json_extract so
    # SQLite drops irrelevant rows server-side instead of returning
    # tens of thousands of rows for Python to filter. The
    # json_extract path matches the canonical Pydantic dump shape
    # (payload.p.tier).
    rows = session.execute(
        text(
            "SELECT primary_character_id, payload_json FROM events "
            "WHERE event_type = 'title_acquired' "
            "AND json_valid(payload_json) "
            "AND json_extract(payload_json, '$.p.tier') "
            "IN ('duchy', 'kingdom', 'empire')"
        )
    ).all()

    backward: dict[int, set[int]] = {}
    for r in rows:
        try:
            payload = json.loads(r.payload_json).get("p", {})
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if not isinstance(payload, dict):
            continue
        # Tier guard retained for defence in depth; SQL already filtered.
        if payload.get("tier") not in ("duchy", "kingdom", "empire"):
            continue
        from_id = payload.get("from_holder_id")
        if from_id is None:
            continue
        try:
            backward.setdefault(int(r.primary_character_id), set()).add(int(from_id))
        except (TypeError, ValueError):
            continue

    played: set[int] = {current_player_id}
    frontier: set[int] = {current_player_id}
    safety = 200
    while frontier and safety > 0:
        safety -= 1
        next_frontier: set[int] = set()
        for cid in frontier:
            for predecessor in backward.get(cid, ()):
                if predecessor not in played:
                    played.add(predecessor)
                    next_frontier.add(predecessor)
        frontier = next_frontier

    if len(_PLAYED_CHARACTER_CACHE) >= _PLAYED_CHARACTER_CACHE_MAX:
        _PLAYED_CHARACTER_CACHE.clear()
    _PLAYED_CHARACTER_CACHE[cache_key] = frozenset(played)
    return played


def aggregate_campaign_counts(session: Session) -> dict[str, int]:
    """Return ``{characters, biographies}`` row counts.

    Two single-table COUNT(*) queries against the open per-campaign
    session. Used by the campaigns endpoint when callers opt in via
    ``?include_counts=true`` (ck3_chronicler-75c).

    Plan cozy-coalescing-shannon: the ``memories`` count formerly
    surfaced here was dropped along with the rest of the LLM-memory
    pipeline. Library + campaign-overview cards now render two count
    cells instead of three.
    """
    return {
        "characters": session.execute(select(func.count(Character.ck3_id))).scalar_one(),
        "biographies": session.execute(select(func.count(Biography.id))).scalar_one(),
    }


def aggregate_campaign_tokens(
    session: Session,
    *,
    month_prefix: str | None = None,
    since: str | None = None,
) -> dict[str, TokenUsdBucket]:
    """Sum token usage grouped by ``provider`` across biographies.

    Returns ``{provider_string: {input, output}}`` where ``provider_string``
    is whatever was stored on the row at generation time (e.g.
    ``"anthropic:claude-opus-4-7"`` or ``"ollama:qwen3:14b"``). Callers
    convert to dollars via :func:`chronicler.cost.lookup_token_price`.

    When ``month_prefix`` is provided (e.g. ``"2026-05"``), only rows
    whose ``generated_at`` starts with that string are summed. Used by
    the cost-summary endpoint's "this month" bucket. None means
    all-time (the "this campaign" bucket).

    ck3_chronicler-0224: ``since`` (ISO 8601 timestamp string) filters
    to rows whose ``generated_at >= since``. Powers the dual-meter
    session counter on the visibility surface (the user manually resets
    the session boundary via ``POST /cost/reset-session``). Composes
    with ``month_prefix``; if both are set, both predicates apply.

    NULL token columns count as 0 — older rows pre-dating the token
    accounting migration don't poison the aggregate.

    Plan cozy-coalescing-shannon: the memory-token branch was dropped
    with the rest of the LLM-memory pipeline. Biographies are now the
    only LLM-generated rows that consume tokens, so the result reflects
    the full per-campaign spend.

    ck3_chronicler-cs1o: buckets carry USD accounting alongside the
    token totals. ``usd_persisted`` sums rows that recorded their own
    cost figure (claude-code envelope total_cost_usd / anthropic local
    computation); the ``uncosted_*`` fields sum the token columns of
    rows whose ``cost_usd`` is NULL so callers can price them via
    :func:`chronicler.cost.compute_generation_cost` (legacy rows have
    no cache breakdown either, so they bill the documented summed-input
    approximation).
    """
    out: dict[str, TokenUsdBucket] = {}

    bio_input = func.coalesce(Biography.prompt_tokens, 0)
    bio_output = func.coalesce(Biography.completion_tokens, 0)
    uncosted = Biography.cost_usd.is_(None)
    bio_stmt = select(
        Biography.provider,
        func.sum(bio_input),
        func.sum(bio_output),
        func.sum(func.coalesce(Biography.cost_usd, 0.0)),
        func.sum(case((uncosted, bio_input), else_=0)),
        func.sum(case((uncosted, func.coalesce(Biography.cache_read_tokens, 0)), else_=0)),
        func.sum(case((uncosted, func.coalesce(Biography.cache_write_tokens, 0)), else_=0)),
        func.sum(case((uncosted, bio_output), else_=0)),
    ).group_by(Biography.provider)
    if month_prefix is not None:
        bio_stmt = bio_stmt.where(Biography.generated_at.like(f"{month_prefix}%"))
    if since is not None:
        bio_stmt = bio_stmt.where(Biography.generated_at >= since)
    for row in session.execute(bio_stmt).all():
        provider, in_t, out_t, usd, un_in, un_read, un_write, un_out = row
        bucket = out.setdefault(str(provider), new_token_usd_bucket())
        bucket["input"] += int(in_t or 0)
        bucket["output"] += int(out_t or 0)
        bucket["usd_persisted"] += float(usd or 0.0)
        bucket["uncosted_input"] += int(un_in or 0)
        bucket["uncosted_read"] += int(un_read or 0)
        bucket["uncosted_write"] += int(un_write or 0)
        bucket["uncosted_output"] += int(un_out or 0)

    return out


def aggregate_tracked_summary(
    session: Session, character_ids: Iterable[int], *, current_month_yyyy_mm: str
) -> dict[int, dict[str, int]]:
    """Per-character biography count + this-month token spend.

    Used by the /tracked endpoint (ck3_chronicler-4cl) to surface
    activity for each tracked character without N+1 queries. Returns
    ``{character_id: {biography_count, monthly_token_spend}}`` keyed
    by every input id (zeros for characters with no rows).

    "monthly_token_spend" sums ``prompt_tokens + completion_tokens``
    on biographies whose ``generated_at`` starts with
    ``current_month_yyyy_mm`` (e.g. ``"2026-05"``). NULL token columns
    count as 0 so older rows that pre-date the token-accounting
    migration don't poison the aggregate.

    Plan cozy-coalescing-shannon: the memory branch was dropped along
    with the rest of the LLM-memory pipeline. Biographies are the only
    LLM-generated per-character rows left, so the count + token sum
    cover the full picture.
    """
    char_ids = list({int(c) for c in character_ids})
    out: dict[int, dict[str, int]] = {
        cid: {"biography_count": 0, "monthly_token_spend": 0} for cid in char_ids
    }
    if not char_ids:
        return out

    month_prefix = f"{current_month_yyyy_mm}%"
    prompt = func.coalesce(Biography.prompt_tokens, 0)
    completion = func.coalesce(Biography.completion_tokens, 0)
    bio_rows = session.execute(
        select(
            Biography.character_id,
            func.count(Biography.id),
            func.sum(
                case(
                    (Biography.generated_at.like(month_prefix), prompt + completion),
                    else_=0,
                )
            ),
        )
        .where(Biography.character_id.in_(char_ids))
        .group_by(Biography.character_id)
    ).all()
    for cid, bio_count, monthly_spend in bio_rows:
        out[cid]["biography_count"] = int(bio_count)
        out[cid]["monthly_token_spend"] += int(monthly_spend or 0)

    return out


def list_events_for_character(
    session: Session, ck3_id: int, *, limit: int | None = None
) -> list[Event]:
    """Events for a character ordered chronologically.

    Uses ``event_date_iso`` for ordering — rows with NULL ISO (date string
    failed to parse) sort first via SQLite's NULL semantics, then events
    with parseable dates in proper chronological order. ``id`` breaks ties
    so emission order is preserved within the same in-game date.
    """
    stmt = (
        select(Event)
        .where(Event.primary_character_id == ck3_id)
        .order_by(Event.event_date_iso, Event.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def count_events(session: Session) -> int:
    """Total events in the per-campaign DB (audit F-47 / ck3_chronicler-hgnb).

    Used by save/ingest.py:_campaign_db_is_fresh as the empty-DB signal
    for the auto-import path. Lives here so the repository owns the
    Event-model query rather than the ingest module reaching across the
    package boundary."""
    return int(session.execute(select(func.count(Event.id))).scalar_one())


def get_character_by_ck3_id(session: Session, ck3_id: int) -> Character | None:
    """Alias for :func:`get_character` — kept distinct so dynasty/codex
    callers can express intent (a single record by ck3_id) without
    looking like the ingest's hot-path get_character that callers may
    grep for. Both wrap the same primary-key fetch."""
    return get_character(session, ck3_id)


def list_characters_by_dynasty(session: Session, dynasty_name: str) -> list[Character]:
    """All characters whose dynasty_name matches (audit F-19).

    Used by the Dynasty Wall route after the player's dynasty is
    resolved. Order is not enforced here — callers sort by birth date
    via _birth_sort_key for the lineage strip."""
    stmt = select(Character).where(Character.dynasty_name == dynasty_name)
    return list(session.execute(stmt).scalars())


def top_dynasty_by_member_count(
    session: Session,
) -> str | None:
    """Find the dynasty_name with the most living-or-dead members.

    The dynasty page falls back to this when current_player_character_id
    isn't pinned — most-populated dynasty is almost always the player's
    in practice. Returns None when the per-campaign DB has no characters
    with a dynasty_name set yet (audit F-19)."""
    stmt = (
        select(Character.dynasty_name, func.count(Character.ck3_id))
        .where(Character.dynasty_name.is_not(None))
        .group_by(Character.dynasty_name)
        .order_by(func.count(Character.ck3_id).desc(), Character.dynasty_name.asc())
        .limit(1)
    )
    row = session.execute(stmt).first()
    return row[0] if row is not None else None


def biographies_for_characters(
    session: Session, character_ids: Iterable[int]
) -> list[tuple[int, str, str, int]]:
    """Bulk-fetch (character_id, body, generated_at, version) tuples for
    the dynasty vita roll (audit F-19). Unsorted — callers pick the
    latest version per character themselves; the dynasty route already
    has that logic."""
    ids = list(character_ids)
    if not ids:
        return []
    stmt = select(
        Biography.character_id,
        Biography.body,
        Biography.generated_at,
        Biography.version,
    ).where(Biography.character_id.in_(ids))
    return [(cid, body, gen_at, ver) for cid, body, gen_at, ver in session.execute(stmt).all()]


def list_latest_biographies_with_character(
    session: Session,
) -> list[tuple[Character, str, str, int]]:
    """Return the latest biography per character joined to its Character
    row, for the campaign-overview Biographies tab.

    ck3_chronicler (2026-05-09): yields ``(Character, body, generated_at,
    version)`` tuples — one per character — so the Biographies surface
    can render birth/death dates, dynasty, names, and a body excerpt
    without N+1ing back to characters per row. "Latest" = highest
    ``version`` per ``character_id``; if a regenerate produced version 2,
    version 1 is hidden from the list (still queryable via the per-
    character endpoint).

    Unsorted on purpose — the route layer chooses the order so different
    surfaces can pick chronological vs. generation-order vs. dynasty
    grouping without paying for a second SQL pass.
    """
    # Subquery: max(version) per character_id.
    latest_versions = (
        select(Biography.character_id, func.max(Biography.version).label("v"))
        .group_by(Biography.character_id)
        .subquery()
    )
    stmt = (
        select(Character, Biography.body, Biography.generated_at, Biography.version)
        .join(Biography, Biography.character_id == Character.ck3_id)
        .join(
            latest_versions,
            (latest_versions.c.character_id == Biography.character_id)
            & (latest_versions.c.v == Biography.version),
        )
    )
    out: list[tuple[Character, str, str, int]] = []
    for char, body, gen_at, ver in session.execute(stmt).all():
        out.append((char, body, gen_at, ver))
    return out


def search_characters_by_name(
    session: Session,
    query: str,
    *,
    limit: int,
    offset: int = 0,
) -> list[Character]:
    """Substring + name-rank-ordered search across name fields.

    audit F-19 / ck3_chronicler-kj7u: pulled out of routes/characters.py
    so the route stays a thin handler. ``query`` should already be
    stripped; an empty query returns the unfiltered list (callers
    handle that path explicitly via list_characters_unfiltered so we
    don't pay a no-op WHERE here).

    Ranking matches the typeahead's expected ordering: exact (case-
    insensitive) first_name match first, prefix match second, substring
    match third by field, ties broken by ck3_id.

    ck3_chronicler-u6cr (2026-05-08): the ``relevance DESC`` tie-break
    that previously sat between ``rank`` and ``ck3_id`` was dropped
    along with the dead ``Character.relevance`` column. Every row in
    production carried the same ``'unknown'`` default, so the column
    never actually broke ties — name-rank already does the heavy
    lifting and ck3_id provides stable cross-run ordering. If we
    later want a real salience signal it should be a numeric column
    (event-count or biography-count), not a string label.
    """
    from sqlalchemy import or_

    from chronicler.db.search import _escape_like

    # audit L20: escape LIKE wildcards so "50%" / "a_b" match literally
    # instead of acting as wildcards (db/search.py already does this).
    escaped = _escape_like(query)
    like = f"%{escaped}%"
    prefix = f"{escaped}%"
    rank = case(
        (func.lower(Character.first_name) == query.lower(), 0),
        (Character.first_name.ilike(prefix, escape="\\"), 1),
        (Character.first_name.ilike(like, escape="\\"), 2),
        (Character.dynasty_name.ilike(like, escape="\\"), 3),
        (Character.nickname.ilike(like, escape="\\"), 4),
        else_=5,
    )
    stmt = (
        select(Character)
        .where(
            or_(
                Character.first_name.ilike(like, escape="\\"),
                Character.dynasty_name.ilike(like, escape="\\"),
                Character.nickname.ilike(like, escape="\\"),
            )
        )
        .order_by(rank, Character.ck3_id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def list_characters_unfiltered(session: Session, *, limit: int, offset: int = 0) -> list[Character]:
    """Page through every character ordered by ck3_id (audit F-19)."""
    stmt = select(Character).order_by(Character.ck3_id).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


def list_characters_by_ids(session: Session, ck3_ids: list[int]) -> list[Character]:
    """ck3_chronicler-5oyz: batched lookup by ck3_id.

    Backs the Codex tracked rail so it doesn't depend on the
    relevance-ranked top-N window — large campaigns push tracked
    characters out of the top 250 entirely. Returned in the order of
    ``ck3_ids`` so the caller can preserve user-defined ordering
    (e.g. tracked bumped_at). Unknown ids are silently dropped.
    Empty input returns an empty list (no DB query).
    """
    if not ck3_ids:
        return []
    stmt = select(Character).where(Character.ck3_id.in_(ck3_ids))
    rows = {c.ck3_id: c for c in session.execute(stmt).scalars()}
    return [rows[cid] for cid in ck3_ids if cid in rows]


def dump_character(session: Session, ck3_id: int) -> dict[str, Any] | None:
    """Return a JSON-friendly dict combining a character and their events.

    Used by ``chronicler dump-character <id>`` (V01-P07) for the v0.1 demo.
    Returns ``None`` if the character is unknown.
    """
    character = get_character(session, ck3_id)
    if character is None:
        return None

    events = list_events_for_character(session, ck3_id)
    return {
        "ck3_id": character.ck3_id,
        "first_name": character.first_name,
        "dynasty_name": character.dynasty_name,
        "house_name": character.house_name,
        "nickname": character.nickname,
        "birth_date": character.birth_date,
        "death_date": character.death_date,
        "culture": character.culture,
        "faith": character.faith,
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "date": e.event_date,
                "date_iso": e.event_date_iso,
                "wall_clock_at": e.wall_clock_at,
                "schema_version": e.schema_version,
                "payload": json.loads(e.payload_json),
            }
            for e in events
        ],
    }


def insert_biography(
    session: Session,
    *,
    character_id: int,
    body: str,
    prompt_template_version: str,
    provider: str,
    generated_at: str,
    model: str | None = None,
    events_through_event_id: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cost_usd: float | None = None,
) -> Biography:
    """Insert a new biography row, auto-incrementing version per character.

    Each character has an independent version sequence (1, 2, 3, ...) so
    regenerations don't collide across characters. Returns the persisted row.
    """
    next_version = _next_biography_version(session, character_id)
    row = Biography(
        character_id=character_id,
        version=next_version,
        body=body,
        prompt_template_version=prompt_template_version,
        provider=provider,
        model=model,
        events_through_event_id=events_through_event_id,
        generated_at=generated_at,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=cost_usd,
    )
    session.add(row)
    session.flush()
    if row.id is None:
        raise RuntimeError("insert_biography invariant: biography.id missing after flush")
    return row


def _next_biography_version(session: Session, character_id: int) -> int:
    stmt = select(func.max(Biography.version)).where(Biography.character_id == character_id)
    current = session.execute(stmt).scalar()
    return (current or 0) + 1


def get_latest_biography_for_character(session: Session, character_id: int) -> Biography | None:
    """Return the highest-version biography for the character, or None."""
    stmt = (
        select(Biography)
        .where(Biography.character_id == character_id)
        .order_by(Biography.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def list_biographies_for_character(session: Session, character_id: int) -> list[Biography]:
    """Return all biographies for a character ordered oldest → newest."""
    stmt = (
        select(Biography).where(Biography.character_id == character_id).order_by(Biography.version)
    )
    return list(session.execute(stmt).scalars())


PLAYTHROUGH_ID_META_KEY = "playthrough_id"


class PlaythroughMismatchError(Exception):
    """A save was read whose playthrough_id differs from what's pinned in schema_meta.

    Attributes:
        observed: the playthrough_id present in the save the caller passed.
        pinned: the playthrough_id previously recorded against this campaign.
    """

    def __init__(self, *, observed: str, pinned: str) -> None:
        super().__init__(
            f"playthrough_id mismatch: save has {observed[:12]}…, "
            f"campaign was pinned to {pinned[:12]}… on a previous read"
        )
        self.observed = observed
        self.pinned = pinned


def assert_playthrough_or_pin(
    session: Session,
    *,
    observed: str,
    allow_reset: bool = False,
    pin_if_unset: bool = True,
) -> str | None:
    """Verify ``observed`` matches the campaign's pinned playthrough_id, or pin it.

    Three cases:

    - First read for this campaign: schema_meta has no playthrough_id.
      If ``pin_if_unset`` is True (default), the observed value is
      recorded and returned. If False, the call is a no-op — the pin
      stays unset and ``None`` is returned.
    - Match: the observed value equals the pinned value; no-op, returns
      the pinned value.
    - Mismatch: raises :class:`PlaythroughMismatchError`. Pass
      ``allow_reset=True`` to overwrite the pinned value instead — that's
      the explicit-confirm escape hatch for users intentionally switching
      a campaign DB to a different CK3 playthrough.

    ``pin_if_unset=False`` is the right call for code that *reads* a save
    incidentally (save-tail's startup baseline) — those paths must never
    silently pin a campaign to whatever stale autosave happened to be in
    the directory. Pinning should be reserved for explicit user-initiated
    commands like ``auto-track`` and ``import-save``.

    Empty/blank ``observed`` is treated as "save lacks playthrough_id"
    and skipped silently — older save formats or partial parses can leave
    it blank. We don't want to lock the campaign to an empty string.
    """
    if not observed:
        return get_meta(session, PLAYTHROUGH_ID_META_KEY)

    pinned = get_meta(session, PLAYTHROUGH_ID_META_KEY)
    if pinned is None:
        if pin_if_unset:
            set_meta(session, PLAYTHROUGH_ID_META_KEY, observed)
            return observed
        return None
    if allow_reset:
        set_meta(session, PLAYTHROUGH_ID_META_KEY, observed)
        return observed
    if pinned != observed:
        raise PlaythroughMismatchError(observed=observed, pinned=pinned)
    return pinned


def set_meta(session: Session, key: str, value: str) -> None:
    stmt = (
        sqlite_insert(SchemaMeta)
        .values(key=key, value=value)
        .on_conflict_do_update(index_elements=[SchemaMeta.key], set_={"value": value})
    )
    session.execute(stmt)
    session.flush()


def get_meta(session: Session, key: str) -> str | None:
    row = session.get(SchemaMeta, key)
    return row.value if row is not None else None

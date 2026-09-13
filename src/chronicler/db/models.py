"""SQLAlchemy 2.0 declarative models for the per-campaign DB.

One SQLite database per campaign. The registry DB (campaigns table) is a
tiny separate file managed without Alembic — see docs/architecture.md.

Key invariants enforced at the schema level:

- ``events`` has a UNIQUE on ``(event_type, event_date, primary_character_id,
  payload_json)`` so the tailer can replay ``debug.log`` from a prior offset
  after a restart without producing duplicates.
- ``event_participants`` cascades on ``events`` delete so participant rows
  never outlive their event.
- ``schema_meta`` is the single-row-per-key table that records
  ``schema_version``, ``ck3_version``, ``mod_version``, ``created_at``.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Character(Base):
    __tablename__ = "characters"

    ck3_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(String)
    dynasty_name: Mapped[str | None] = mapped_column(String)
    house_name: Mapped[str | None] = mapped_column(String)
    # Localised epithet from the save's nickname_text (e.g. "the Impaler",
    # "the Wise"). Distinct from first_name so the consolidator can render
    # "Ælla, called the Impaler" in prompt headers.
    nickname: Mapped[str | None] = mapped_column(String)
    # CK3 gender flag, mirrored from CharacterSnapshot.female. Nullable
    # because we may upsert a character (FK target for an event participant)
    # before we've seen their full record. None means "unknown" — biography
    # and consolidator prompts omit the Gender line rather than guessing.
    female: Mapped[bool | None] = mapped_column(Boolean)
    birth_date: Mapped[str | None] = mapped_column(String)
    death_date: Mapped[str | None] = mapped_column(String)
    culture: Mapped[str | None] = mapped_column(String)
    faith: Mapped[str | None] = mapped_column(String)
    last_seen_event_id: Mapped[int | None] = mapped_column(Integer)
    save_snapshot_json: Mapped[str | None] = mapped_column(Text)
    # ck3_chronicler-7ao: full CoA structure resolved at save-tail time
    # via heraldry.resolve_character_coa (character → dynasty_house →
    # coat_of_arms_id → CoA dict). Surfaced to the SVG composition
    # renderer via the /coa API endpoint. NULL until the next save-tail
    # tick refreshes the character.
    coa_json: Mapped[str | None] = mapped_column(Text)
    # ck3_chronicler-8ek: structured world-context summary (RegionSummary
    # TypedDict, JSON-serialized) — character's de jure region peers and
    # cross-currents at the time of the last save-tail refresh. Read by
    # the death-biography pipeline as the world-context scene-setter
    # block. NULL when the character holds no kingdom-tier title or
    # their realm sits outside any de jure empire (early eras / mod
    # content) — biography pipeline falls through to today's prompt.
    region_summary_json: Mapped[str | None] = mapped_column(Text)
    # ck3_chronicler-7md7: structured "current great cause" payload
    # (GreatCauseFacts TypedDict, JSON-serialized) — populated when the
    # character is a participant in a war whose casus_belli_type is in
    # the great-cause allowlist (crusades, great holy wars, papal
    # crusades). Snapshot-derived: auto-clears on the next save-tail
    # tick after the war concludes (CK3 removes it from active_wars).
    # NULL when no great cause is active.
    great_cause_json: Mapped[str | None] = mapped_column(Text)
    # ck3_chronicler-zx2l: highest-tier directly-held title for the
    # character at the time of the last save-tail refresh, serialized
    # as ``{"key": "k_england", "name": "Kingdom of England", "tier":
    # "kingdom"}``. Surfaced on the Biographies list + Chronicle aside
    # so an unnamed row reads as "King of England Ælfric" rather than
    # just "Ælfric of Æscingas". Deliberately *not* on
    # ``_CHARACTER_NULLABLE_ON_UPDATE_FIELDS``: when the character dies
    # and their titles transfer to an heir, the next refresh computes
    # "no held titles" — leaving the column untouched preserves the
    # title-at-death (the last alive-tick value) for the biography
    # surface. NULL only for characters who have never been a
    # title-holder during any observed tick.
    primary_title_json: Mapped[str | None] = mapped_column(Text)
    # ck3_chronicler-9ngy: EVERY directly-held title at the last refresh,
    # grandest-first, serialized as a JSON list of
    # ``[{"key","name","tier"}, ...]`` (the head equals primary_title_json).
    # Surfaced on the Biographies sidebar + overview so a multi-realm ruler
    # lists all their titles at death (kingdoms, duchies, counties) instead
    # of just the single primary. Same preserve-at-death semantics as
    # primary_title_json: deliberately NOT on
    # ``_CHARACTER_NULLABLE_ON_UPDATE_FIELDS`` so the death tick (heir has
    # inherited → "no held titles") leaves the last alive-tick value intact.
    held_titles_json: Mapped[str | None] = mapped_column(Text)


class CharacterCoaHistory(Base):
    """Append-only history of resolved-CoA changes per character (7b8d).

    Each save-tail tick that resolves a fresh ``coa_json`` for a tracked
    character compares the new value against the most recent row here;
    on change, a new row is appended. The Chronicle Folio renders these
    rows as a medallion timeline ("arms changed on YYYY.MM.DD") so the
    user can see cadet-branch + facelift transitions in one glance.

    The store is canonical: the one-row-at-a-time live ``coa_json`` on
    Character is for fast lookup; this table is the audit trail.
    """

    __tablename__ = "character_coa_history"
    __table_args__ = (
        Index(
            "ix_character_coa_history_char_observed",
            "character_id",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.ck3_id"), nullable=False
    )
    # ISO 8601 UTC timestamp of when save-tail observed this CoA. Distinct
    # from in-game date — we may not always have an in-game date at hand
    # (the diff loop has it; a manual refresh wouldn't), and wall-clock
    # is sufficient ordering for the timeline.
    observed_at: Mapped[str] = mapped_column(String, nullable=False)
    coa_json: Mapped[str] = mapped_column(Text, nullable=False)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "event_date",
            "primary_character_id",
            "payload_json",
            name="events_idempotency",
        ),
        Index("ix_events_char_date", "primary_character_id", "event_date"),
        Index("ix_events_char_date_iso", "primary_character_id", "event_date_iso"),
        # ck3_chronicler-wak4: compute_played_character_ids filters by
        # event_type='title_acquired' on every character list/detail
        # request. Without this index the query was a full-table scan +
        # Python-side JSON tier filter — multi-hundred-ms latency on
        # long campaigns where title_acquired events run to the tens of
        # thousands.
        Index("ix_events_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    # Long-form date as CK3 emitted it (e.g. "16th of September, 1066 AD").
    # Kept verbatim for human readability and biography prompts.
    event_date: Mapped[str] = mapped_column(String, nullable=False)
    # ISO 8601 form (YYYY-MM-DD) for sortable ORDER BY / range queries.
    # NULL when the source string couldn't be parsed.
    event_date_iso: Mapped[str | None] = mapped_column(String, nullable=True)
    wall_clock_at: Mapped[str] = mapped_column(String, nullable=False)
    primary_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.ck3_id"), nullable=False
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)


class EventParticipant(Base):
    __tablename__ = "event_participants"

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.ck3_id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String, primary_key=True)


class Quarantine(Base):
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    # ck3_chronicler-fkw: best-effort event type extracted from the
    # failing envelope (e.g. "death", "birth"). NULL when the parser
    # never made it far enough to know — the suppression hook treats
    # NULL kinds as un-suppressable so they always reach the table.
    event_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Biography(Base):
    """LLM-generated narrative biography for a character.

    Multiple versions per character are kept (the ``UNIQUE(character_id,
    version)`` constraint enforces strict ordering) so we can regenerate
    with new prompts or providers without losing history. The latest
    version is the one surfaced by ``dump-biography``; older rows are
    audit trail.

    ``events_through_event_id`` records the last event that was visible to
    the LLM at generation time — useful for "regenerate if N new events
    have arrived since last biography" logic in v0.5+.
    """

    __tablename__ = "biographies"
    __table_args__ = (
        UniqueConstraint("character_id", "version", name="biographies_char_version"),
        Index("ix_biographies_character_id", "character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.ck3_id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template_version: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    # ck3_chronicler-ju7j (Phase 1): model the envelope actually billed
    # (from modelUsage), distinct from ``provider`` which records the
    # kind-resolved tag. Diverges when CLI routes the requested model
    # to a different one or when subagent calls show up in modelUsage.
    # NULL on pre-Phase-1 rows.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    events_through_event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id"), nullable=True
    )
    generated_at: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ck3_chronicler-cs1o: prompt-cache breakdown (subsets of
    # prompt_tokens, which stays the summed total) + the transport's own
    # cost figure (claude-code envelope total_cost_usd, or locally
    # computed for the anthropic transport). NULL on pre-cs1o rows —
    # cost endpoints fall back to the summed-input approximation there.
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

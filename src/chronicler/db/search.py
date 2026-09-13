"""Cross-campaign search helpers (ck3_chronicler-b2y).

Two surfaces:

- :func:`search_biographies` uses the FTS5 shadow table created by
  migration ebc825c7d3ed when available; if the host SQLite was built
  without FTS5 it falls back to a LIKE scan so the endpoint still works.

- :func:`search_characters` and :func:`search_events` are LIKE-only —
  characters has just first_name + dynasty_name (low-cardinality, FTS5
  isn't worth a separate shadow); events store JSON payloads where
  FTS5 tokenization is poor anyway.

All helpers return ``list[SearchHit]`` with a per-hit ``snippet``
suitable for direct frontend display.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

SearchKind = Literal["biography", "character", "event"]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One cross-campaign search match."""

    kind: SearchKind
    row_id: int  # biographies.id, characters.ck3_id, or events.id
    snippet: str  # short excerpt around the match (FTS5 snippet() or first chars)
    character_id: int | None  # joined where applicable
    rank: float | None  # FTS5 rank (lower = better) or None for LIKE


def _fts5_table_exists(session: Session, table: str) -> bool:
    """True iff the named virtual table exists in this DB."""
    row = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).first()
    return row is not None


def _escape_like(query: str) -> str:
    """Escape LIKE wildcards (% and _) so a query matches literally.

    Pair with ``ESCAPE '\\'`` on the SQL side (or ``escape="\\"`` on a
    SQLAlchemy ``ilike``). Shared with ``repository.search_characters_by_name``
    (audit L20).
    """
    return query.replace("%", r"\%").replace("_", r"\_")


def _like_pattern(query: str) -> str:
    """Wrap a query for a substring LIKE — escape % and _ to match literally."""
    return f"%{_escape_like(query)}%"


# FTS5 query operators that, if present in unsanitised user input, can
# either match nothing or trigger an "no such column" parse error
# (e.g. hyphens become NEAR-operator syntax). We don't expose query
# operators to end users in v0.7 — the search box is a flat input —
# so the safe default is to treat the entire query as a phrase by
# wrapping it in double quotes (after escaping any inner double
# quotes via the FTS5 escape, which is doubling). Tokenisation then
# splits on punctuation and matches on the resulting tokens.
_FTS5_OPERATOR_CHARS = frozenset('"-+*^():/{}.,!?')

# FTS5 reads AND/OR/NOT/NEAR as boolean operators ONLY when uppercase. A query
# whose whitespace-split tokens include a bare one of these is either a syntax
# error ("cat AND", "AND") or silently applies the operator ("cat NOT dog").
# Since we don't expose operators (see module docstring), such queries are
# treated as phrases too (ck3_chronicler-27ov.6 / audit M-B2).
_FTS5_BOOLEAN_KEYWORDS = frozenset({"AND", "OR", "NOT", "NEAR"})


def _fts5_phrase(query: str) -> str:
    """Force the whole query to a single FTS5 phrase (FTS5 escapes "" by doubling)."""
    inner = query.replace('"', '""')
    return f'"{inner}"'


def _fts5_safe_query(query: str) -> str:
    """Sanitise user input for an FTS5 MATCH expression.

    We don't expose query operators: any input that FTS5 could read as an
    operator — an operator character, or a bare uppercase boolean keyword —
    is wrapped as a phrase. Clean queries pass through to keep implicit-AND.
    """
    if any(c in _FTS5_OPERATOR_CHARS for c in query):
        return _fts5_phrase(query)
    if any(tok in _FTS5_BOOLEAN_KEYWORDS for tok in query.split()):
        return _fts5_phrase(query)
    return query


def _excerpt(body: str, query: str, *, length: int = 160) -> str:
    """Return a short excerpt of ``body`` around the first hit on ``query``.

    Used by the LIKE fallback path. FTS5 has its own snippet() function
    we use directly.
    """
    if not body:
        return ""
    lower_body = body.lower()
    lower_query = query.lower()
    idx = lower_body.find(lower_query)
    if idx < 0:
        # Query may have been multi-token; just return the head
        return body[:length]
    half = length // 2
    start = max(0, idx - half)
    end = min(len(body), start + length)
    excerpt = body[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(body):
        excerpt = excerpt + "…"
    return excerpt


def search_biographies(session: Session, query: str, *, limit: int = 50) -> list[SearchHit]:
    """Search biography bodies. FTS5 if available, else LIKE."""
    if not query.strip():
        return []
    if _fts5_table_exists(session, "biographies_fts"):
        stmt = text(
            """
            SELECT b.id, b.character_id,
                   snippet(biographies_fts, 0, '<mark>', '</mark>', '…', 12) AS snippet,
                   biographies_fts.rank AS rank
            FROM biographies_fts
            JOIN biographies b ON b.id = biographies_fts.rowid
            WHERE biographies_fts MATCH :q
            ORDER BY rank
            LIMIT :limit
            """
        )
        try:
            rows = session.execute(stmt, {"q": _fts5_safe_query(query), "limit": limit}).all()
        except OperationalError:
            # Belt-and-suspenders: any MATCH syntax the sanitizer missed must
            # not 500 the search page — retry as a literal phrase (27ov.6).
            session.rollback()
            rows = session.execute(stmt, {"q": _fts5_phrase(query), "limit": limit}).all()
        return [
            SearchHit(
                kind="biography",
                row_id=int(r.id),
                snippet=str(r.snippet),
                character_id=int(r.character_id),
                rank=float(r.rank) if r.rank is not None else None,
            )
            for r in rows
        ]
    # LIKE fallback
    rows = session.execute(
        text(
            """
            SELECT id, character_id, body
            FROM biographies
            WHERE body LIKE :pat ESCAPE '\\'
            ORDER BY id DESC
            LIMIT :limit
            """
        ),
        {"pat": _like_pattern(query), "limit": limit},
    ).all()
    return [
        SearchHit(
            kind="biography",
            row_id=int(r.id),
            snippet=_excerpt(str(r.body), query),
            character_id=int(r.character_id),
            rank=None,
        )
        for r in rows
    ]


def search_characters(session: Session, query: str, *, limit: int = 50) -> list[SearchHit]:
    """LIKE-only — name + dynasty + nickname."""
    if not query.strip():
        return []
    pat = _like_pattern(query)
    rows = session.execute(
        text(
            """
            SELECT ck3_id, first_name, dynasty_name, nickname
            FROM characters
            WHERE first_name LIKE :pat ESCAPE '\\'
               OR dynasty_name LIKE :pat ESCAPE '\\'
               OR nickname LIKE :pat ESCAPE '\\'
            ORDER BY ck3_id
            LIMIT :limit
            """
        ),
        {"pat": pat, "limit": limit},
    ).all()
    out: list[SearchHit] = []
    for r in rows:
        label_parts = [
            str(r.first_name) if r.first_name else "",
            str(r.dynasty_name) if r.dynasty_name else "",
        ]
        label = " ".join(p for p in label_parts if p)
        if r.nickname:
            label = f"{label}, called {r.nickname}"
        out.append(
            SearchHit(
                kind="character",
                row_id=int(r.ck3_id),
                snippet=label or "(unnamed)",
                character_id=int(r.ck3_id),
                rank=None,
            )
        )
    return out


def search_events(session: Session, query: str, *, limit: int = 50) -> list[SearchHit]:
    """LIKE on payload_json — events store structured JSON, FTS5 is overkill."""
    if not query.strip():
        return []
    rows = session.execute(
        text(
            """
            SELECT id, primary_character_id, event_type, event_date, payload_json
            FROM events
            WHERE payload_json LIKE :pat ESCAPE '\\'
               OR event_type LIKE :pat ESCAPE '\\'
            ORDER BY id DESC
            LIMIT :limit
            """
        ),
        {"pat": _like_pattern(query), "limit": limit},
    ).all()
    out: list[SearchHit] = []
    for r in rows:
        snippet = f"{r.event_date} · {r.event_type} · {r.payload_json[:120]}"
        out.append(
            SearchHit(
                kind="event",
                row_id=int(r.id),
                snippet=snippet,
                character_id=int(r.primary_character_id),
                rank=None,
            )
        )
    return out


def search_all_scopes(
    session: Session,
    query: str,
    *,
    scopes: Iterable[SearchKind] = ("biography", "character", "event"),
    limit_per_scope: int = 20,
) -> list[SearchHit]:
    out: list[SearchHit] = []
    scope_set = set(scopes)
    if "biography" in scope_set:
        out.extend(search_biographies(session, query, limit=limit_per_scope))
    if "character" in scope_set:
        out.extend(search_characters(session, query, limit=limit_per_scope))
    if "event" in scope_set:
        out.extend(search_events(session, query, limit=limit_per_scope))
    return out

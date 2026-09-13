"""Parse CK3's long-form English date string into sortable ISO 8601.

V0.1's wire format had to use ``[GetCurrentDate.GetStringLong]`` because
the loc engine in ``debug_log`` resolution had no script scope context to
compute a YYYY.M.D string. The result is human-readable but not lexically
sortable::

    "16th of September, 1066 AD" → "1066-09-16"
    "1st of January, 867 AD"     → "0867-01-01"

This module provides a single function, :func:`parse_ck3_long_date`,
which returns the ISO form or ``None`` if the input doesn't match. The
ingest layer uses it to populate ``events.event_date_iso`` alongside the
original ``event_date`` text; biography prompts read the long form (more
natural for an LLM), while ``ORDER BY`` queries use the ISO column.

BC dates are rejected (return ``None``). CK3's standard bookmarks start
867+, and our two-track storage means an unparsed date still has the
original text in ``event_date``.
"""

from __future__ import annotations

import re

_MONTHS: dict[str, int] = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

# Matches "<day><suffix> of <Month>, <year> AD"
# Suffix is st/nd/rd/th — present in GetStringLong output.
_LONG_DATE_RE = re.compile(
    r"^\s*(?P<day>\d{1,2})(?:st|nd|rd|th)\s+of\s+(?P<month>\w+),\s+(?P<year>\d+)\s+AD\s*$"
)

# Matches CK3's short save-file date form "YYYY.M.D" (no zero-padding)
_SHORT_DATE_RE = re.compile(r"^\s*(?P<year>\d+)\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})\s*$")


def parse_ck3_short_date(text: str) -> str | None:
    """Parse CK3's short save-file date form (``YYYY.M.D``) to ISO 8601.

    Used for save-diff events where dates come from save metadata
    rather than ``[GetCurrentDate.GetStringLong]`` interpolation.
    Returns ``None`` if the string doesn't match.
    """
    match = _SHORT_DATE_RE.match(text)
    if match is None:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    if year <= 0 or not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_ck3_date(text: str) -> str | None:
    """Try both CK3 date forms; return ISO 8601 or ``None``.

    Save-diff events carry short dates (``1066.10.14``); debug_log
    events carry long English dates (``14th of October, 1066 AD``).
    Both flow through the same Pydantic schema and event_date column,
    so the normaliser accepts either input form.
    """
    return parse_ck3_long_date(text) or parse_ck3_short_date(text)


def parse_ck3_long_date(text: str) -> str | None:
    """Parse CK3's GetStringLong output to ISO 8601 (``YYYY-MM-DD``).

    Returns ``None`` if the string doesn't match the expected pattern,
    if the month name is unknown, or if the day/year fall outside sane
    bounds. Year is zero-padded to four digits — CK3 campaigns can start
    as early as 867 AD.
    """
    match = _LONG_DATE_RE.match(text)
    if match is None:
        return None
    month = _MONTHS.get(match.group("month"))
    if month is None:
        return None
    day = int(match.group("day"))
    if not 1 <= day <= 31:
        return None
    year = int(match.group("year"))
    if year <= 0:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"

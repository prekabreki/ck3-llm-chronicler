"""Tests for chronicler.util.dates.parse_ck3_long_date."""

from __future__ import annotations

import pytest

from chronicler.util.dates import parse_ck3_long_date


@pytest.mark.parametrize(
    "text,expected",
    [
        ("16th of September, 1066 AD", "1066-09-16"),
        ("1st of January, 867 AD", "0867-01-01"),
        ("23rd of September, 1066 AD", "1066-09-23"),
        ("2nd of February, 1200 AD", "1200-02-02"),
        ("3rd of March, 1500 AD", "1500-03-03"),
        ("31st of December, 1453 AD", "1453-12-31"),
        ("  16th of September, 1066 AD  ", "1066-09-16"),  # surrounding whitespace
    ],
)
def test_parse_known_strings(text: str, expected: str) -> None:
    assert parse_ck3_long_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1066.10.14",  # YYYY.M.D form (not what we get)
        "1066-10-14",  # ISO form (not what we get)
        "October 14, 1066",  # American
        "16th September 1066 AD",  # missing 'of' / comma
        "16th of Smarch, 1066 AD",  # bogus month
        "0th of January, 1066 AD",  # invalid day
        "32nd of January, 1066 AD",  # invalid day
        "1st of January, -100 AD",  # negative year
        "BC dates not handled — '1st of January, 100 BC'",
        "garbage",
    ],
)
def test_parse_returns_none_for_unparseable(text: str) -> None:
    assert parse_ck3_long_date(text) is None


def test_parse_iso_form_is_lexically_sortable() -> None:
    """Two events on different in-game dates sort correctly via the ISO form."""
    early = parse_ck3_long_date("1st of January, 867 AD")
    late = parse_ck3_long_date("16th of September, 1066 AD")
    assert early is not None and late is not None
    assert early < late  # lexical ordering matches chronological

"""Tests for chronicler.util.slug — ASCII-fold + punctuation-strip
slugify used by the export bundle to build safe filenames."""

from chronicler.util.slug import slugify


def test_basic_lowercase() -> None:
    assert slugify("Alfred") == "alfred"


def test_spaces_become_hyphens() -> None:
    assert slugify("Eadgyth of Wessex") == "eadgyth-of-wessex"


def test_diacritics_folded() -> None:
    # OE-ligature, eth, accented vowels, the lot.
    assert slugify("Aélfgyfu") == "aelfgyfu"
    assert slugify("Bjørn Þórsson") == "bjorn-thorsson"


def test_punctuation_stripped() -> None:
    assert slugify("Aélfgyfu the *Bold*") == "aelfgyfu-the-bold"
    assert slugify("Edward (the Confessor)") == "edward-the-confessor"


def test_collapses_runs_of_separators() -> None:
    assert slugify("a   --   b") == "a-b"


def test_strips_leading_trailing_separators() -> None:
    assert slugify("  --hello-- ") == "hello"


def test_empty_input_returns_empty_string() -> None:
    assert slugify("") == ""
    assert slugify("   ") == ""
    assert slugify("***") == ""

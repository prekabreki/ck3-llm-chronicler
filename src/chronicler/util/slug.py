"""Slugify for export filenames (ck3_chronicler-441).

Pure function — no I/O, no deps. Lowercases, ASCII-folds (NFKD +
combining-mark strip + a small custom map for the few CK3 names that
NFKD doesn't cover, e.g. 'þ' → 'th'), strips non-alphanumerics, and
collapses runs of separators to single hyphens.
"""

from __future__ import annotations

import re
import unicodedata

# NFKD doesn't decompose these; CK3 character names hit them often
# enough (Norse + Old English) to warrant the explicit map.
_EXTRA: dict[str, str] = {
    "þ": "th",
    "Þ": "th",
    "ð": "d",
    "Ð": "d",
    "ß": "ss",
    "æ": "ae",
    "Æ": "ae",
    "œ": "oe",
    "Œ": "oe",
    "ø": "o",
    "Ø": "o",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Return an ASCII slug for ``text``. Empty string when nothing
    survives folding."""
    if not text:
        return ""
    folded_chars: list[str] = []
    for ch in text:
        replacement = _EXTRA.get(ch)
        folded_chars.append(replacement if replacement is not None else ch)
    folded = unicodedata.normalize("NFKD", "".join(folded_chars))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_ALNUM.sub("-", ascii_only).strip("-")
    return slug

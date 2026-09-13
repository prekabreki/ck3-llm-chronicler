"""Shared serialization helpers for the API route modules.

Small, pure response-shaping helpers that more than one route module needs.
Keeping them here avoids cross-importing private helpers between route modules
(ck3_chronicler-27ov.46 / audit M-A4). It is also the consolidation point for
the JSON-decode helpers that used to be duplicated verbatim across
characters/dynasty/tree/tracked/hall/campaigns/search — the copies existed
only to dodge routes→routes imports, and had already drifted (some skipped
the ``isinstance(dict)`` guard). ck3_chronicler-27ov.45 / audit M-A1: this
module is the parsers' one legitimate home; every helper keeps the safest
(fully guarded) variant of the old copies.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from chronicler.api.models import PrimaryTitleSummary

log = logging.getLogger(__name__)


def blurb_from_chronicle(body: str | None) -> str | None:
    """First paragraph of a closing chronicle, or None.

    Splits on the first blank line — the closing-ceremony prompt template emits
    paragraph-separated prose, so this is the natural boundary. We don't
    truncate to a character cap; the first paragraph is bounded by the LLM's own
    pacing. Surfaces as the Library card's ``closing_chronicle_blurb`` and the
    Dynasty page's one-paragraph ``founding_paragraph`` hero.
    """
    if not body:
        return None
    first = body.split("\n\n", 1)[0].strip()
    return first or None


def parse_coa_json(raw: str | None) -> dict[str, Any] | None:
    """Decode a persisted ``coa_json`` TEXT column to the dict the FE consumes.

    Best-effort: bad JSON or a non-dict payload return ``None`` rather than
    raising — a malformed row must not poison a list/detail response. The
    frontend falls through to the procedural shield via HeraldryWithFallback.
    (Save-tail produces well-formed JSON, so a warning here means a corrupt
    row; defence in depth is cheap.)
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("persisted coa_json is not valid JSON; rendering shield omitted")
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_primary_title(raw: str | None) -> PrimaryTitleSummary | None:
    """Decode ``Character.primary_title_json`` to the response model.

    Forgiving: bad JSON, missing required keys, or a non-dict payload
    return None rather than raising — the column is a write-through of
    a save-tail compute, so a corrupt row shouldn't 500 the detail
    endpoint. ck3_chronicler-zx2l.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    key = data.get("key")
    tier = data.get("tier")
    if not isinstance(key, str) or not isinstance(tier, str):
        return None
    name = data.get("name")
    return PrimaryTitleSummary(
        key=key,
        name=name if isinstance(name, str) else None,
        tier=tier,
    )


def parse_held_titles(raw: str | None) -> list[PrimaryTitleSummary]:
    """ck3_chronicler-9ngy: decode ``Character.held_titles_json`` (a JSON
    list of ``{"key","name","tier"}``, grandest-first) to response models.

    Forgiving like :func:`parse_primary_title`: bad JSON, a non-list
    payload, or malformed entries yield ``[]`` / skip the entry rather than
    raising — the column is a write-through of a save-tail compute, so a
    corrupt row shouldn't 500 the detail/list endpoints.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[PrimaryTitleSummary] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        tier = entry.get("tier")
        if not isinstance(key, str) or not isinstance(tier, str):
            continue
        name = entry.get("name")
        out.append(
            PrimaryTitleSummary(
                key=key,
                name=name if isinstance(name, str) else None,
                tier=tier,
            )
        )
    return out


def parse_family_data(save_snapshot_json: str | None) -> dict[str, Any]:
    """Best-effort decode of ``Character.save_snapshot_json.family_data``.

    Returns ``{}`` on missing/malformed JSON or when the snapshot has no
    family_data dict (e.g. the row predates the save-tail tick that persists
    it). family_data values are already name-expanded (``{"id","name"}``)
    by the save pipeline when a name_lookup was available — see
    ``chronicler.save.parse._resolve_family_names``. ck3_chronicler-lmex.
    """
    if not save_snapshot_json:
        return {}
    try:
        data = json.loads(save_snapshot_json)
    except (ValueError, TypeError):
        return {}
    fam = data.get("family_data") if isinstance(data, dict) else None
    return fam if isinstance(fam, dict) else {}


def extract_family_ids(value: Any) -> list[int]:
    """Pull character IDs out of a family_data value.

    Handles four shapes:
      - int (raw — no name_lookup was passed at extract time)
      - {"id": int, "name": ...} (name-resolved)
      - list[int]
      - list[{"id": int, "name": ...}]

    Anything else (None, strings, junk entries inside lists) contributes
    no IDs rather than raising.
    """
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        cid = value.get("id")
        return [cid] if isinstance(cid, int) else []
    if isinstance(value, list):
        out: list[int] = []
        for item in value:
            out.extend(extract_family_ids(item))
        return out
    return []

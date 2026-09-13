"""Shared API wire models for the chronicler portal.

ck3_chronicler-27ov.48 (M-A6): single-consumer response/request models now
live in their owning route module (co-location). What remains here is the
genuinely shared surface: PrimaryTitleSummary (built by serializers, nested
by character + dynasty models) and the pagination caps.
"""

from __future__ import annotations

from pydantic import BaseModel

# Pagination caps applied to character-list endpoints (HTML + JSON). Kept
# in one place so both routers (and any future paginating endpoint) agree.
DEFAULT_LIMIT = 100


MAX_LIMIT = 500


class PrimaryTitleSummary(BaseModel):
    """ck3_chronicler-zx2l: highest-tier directly-held title for a
    character.

    ``key`` is the CK3 engine identifier (``k_england``, ``d_kent``).
    ``name`` is the localised display string from the save's
    ``title_name_data.name`` (``"Kingdom of England"``), or ``None`` for
    titles CK3 didn't localise. ``tier`` is one of ``empire`` /
    ``kingdom`` / ``duchy`` / ``county`` / ``barony`` (the FE uses this
    to fetch the matching tier crown from
    ``/api/heraldry/assets/title_icons/<tier>.png``).

    Persisted as JSON on ``Character.primary_title_json``; preserved
    through the death tick so this is the "title at death" for the
    biography surface (and the "current title" for a still-tracked
    living character).
    """

    key: str
    name: str | None
    tier: str

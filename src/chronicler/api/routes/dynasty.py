"""Dynasty Wall endpoint (ck3_chronicler-thpz.1).

GET /api/campaigns/{name}/dynasty assembles the player's dynasty into
one response: hero shield (head's CoA), lineage strip (members ordered
by birth date with each character's own arms), and vita roll
(biographies in death-date desc order). Tracking status is intentionally
left to the frontend — TrackedCharacter rows live in the registry DB
and can be merged client-side via the existing useTracked() query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from chronicler.api.dependencies import (
    get_campaign_including_archived,
    get_session_including_archived,
)
from chronicler.api.models import PrimaryTitleSummary
from chronicler.api.serializers import (
    blurb_from_chronicle,
    parse_coa_json,
    parse_family_data,
    parse_held_titles,
    parse_primary_title,
)
from chronicler.db.registry import Campaign
from chronicler.db.repository import (
    biographies_for_characters,
    get_character_by_ck3_id,
    list_characters_by_dynasty,
    list_latest_biographies_with_character,
    top_dynasty_by_member_count,
)

if TYPE_CHECKING:
    # audit F-19: ORM model only needed for type hints below; keep
    # runtime imports inside the repository.
    from chronicler.db.models import Character

router = APIRouter(prefix="/api/campaigns/{name}", tags=["dynasty"])

_VITA_EXCERPT_LEN = 240


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class BiographyListEntry(BaseModel):
    """One row on the campaign-overview Biographies tab (ck3_chronicler 2026-05-09).

    Surfaces every character that has a biography, joined to their
    Character row so the FE can render in-game dates + dynasty + name
    without follow-up calls. ``biography_excerpt`` is a short slice of
    the body (sentence-aware cut at ~240 chars, same shape as the
    Dynasty page's vita roll). ``version`` is the latest persisted
    version — older revisions stay queryable via the per-character
    biography endpoint.
    """

    ck3_id: int
    first_name: str | None
    nickname: str | None
    dynasty_name: str | None
    female: bool | None = None
    birth_date: str | None
    death_date: str | None
    biography_excerpt: str
    generated_at: str
    version: int
    coa_json: dict[str, Any] | None = None
    # ck3_chronicler-zx2l: title-at-death (or current title for the
    # still-living tracked player) shown on the Biographies list row
    # below the name line with the tier crown.
    primary_title: PrimaryTitleSummary | None = None
    # ck3_chronicler-9ngy: every title held at death (grandest-first), so
    # the Biographies overview row lists all of a ruler's realms, not just
    # the primary. held_titles[0] == primary_title.
    held_titles: list[PrimaryTitleSummary] = []
    # ck3_chronicler-lmex: role grouping + relationship context on the
    # Biographies page. role ∈ {"ruler","consort","kin","other"}.
    role: str | None = None
    relation: str | None = None


class DynastyMember(BaseModel):
    """One person in the Dynasty Wall lineage strip (ck3_chronicler-thpz.1).

    ``coa_json`` is the per-character resolved CoA structure (or null
    when save-tail hasn't refreshed this character yet). Cadet-branch
    members will have a different coa_json than the dynasty head — the
    strip surfaces that divergence visually.
    """

    ck3_id: int
    first_name: str | None
    dynasty_name: str | None
    nickname: str | None
    birth_date: str | None
    death_date: str | None
    is_player: bool
    has_biography: bool
    coa_json: dict[str, object] | None


class DynastyVitaEntry(BaseModel):
    """One row in the Dynasty Wall vita roll. Body is truncated to a
    short excerpt for inline rendering — the full biography lives at
    GET /characters/{ck3_id}/biography."""

    ck3_id: int
    first_name: str | None
    nickname: str | None
    death_date: str | None
    biography_excerpt: str
    generated_at: str


class DynastyResponse(BaseModel):
    """Player's dynasty in one shot — ck3_chronicler-thpz.1.

    ``founder`` is the earliest-born member (chronological seed for the
    lineage chain). ``current_head`` is whichever member matches the
    campaign's ``current_player_character_id`` when known. ``members``
    is ordered by birth_date ascending so the strip naturally chains
    founder → present.

    ``founding_paragraph`` is filled in when the campaign has a closing
    chronicle blurb on file; otherwise null and the frontend falls
    back to a derived line.
    """

    dynasty_name: str
    member_count: int
    chronicled_count: int
    founding_date: str | None
    founding_paragraph: str | None
    founder: DynastyMember | None
    current_head: DynastyMember | None
    members: list[DynastyMember]
    vita_roll: list[DynastyVitaEntry]


def _resolve_player_dynasty(
    session: Session, campaign: Campaign
) -> tuple[Character | None, str | None]:
    """Pick the player character + their dynasty_name.

    Falls back to the most-populated dynasty *only* when no player is
    pinned — that's a stable, intent-preserving default for adopted-
    pre-resolution campaigns and cross-campaign smoke runs.

    ck3_chronicler-za6f: when a player IS pinned but
    ``player.dynasty_name`` is NULL (save-tail's resolver hasn't yet
    populated dynasty_name through the dynasty_house_id chain), we
    surface 404 rather than substituting the top-populated dynasty.
    On large campaigns the most-populated dynasty has nothing to do
    with the player (smoke session hit ``japanese_fujiwara`` for a
    House of Barcelona player) — a confidently-wrong answer is worse
    than a "no data yet" state.

    audit F-19: queries pulled into db/repository so the route layer
    stays thin and the ranking logic isn't duplicated.
    """
    if campaign.current_player_character_id is not None:
        player = get_character_by_ck3_id(session, campaign.current_player_character_id)
        if player is not None and player.dynasty_name:
            return player, player.dynasty_name
        # ck3_chronicler-za6f: pinned player but unresolved dynasty —
        # don't fall back to top-populated. Caller surfaces 404.
        return player, None

    dynasty_name = top_dynasty_by_member_count(session)
    return None, dynasty_name


def _fam_name(node: object) -> str | None:
    """First non-empty name from a family node: a {"id","name"} dict, a list
    of them, or None."""
    if isinstance(node, dict):
        name = node.get("name")
        return name if isinstance(name, str) and name else None
    if isinstance(node, list):
        for entry in node:
            if isinstance(entry, dict) and entry.get("name"):
                return entry["name"]
    return None


def _classify_bio_role(
    *,
    char_dynasty: str | None,
    player_dynasty: str | None,
    has_title: bool,
    family: dict,
    female: bool | None,
) -> tuple[str, str | None]:
    """(role, relation) for a biography entry, relative to the player's
    dynasty. Relation is spouse-based — parent links are pruned for the
    dead and unavailable. See ck3_chronicler-lmex.

    role ∈ {"ruler","consort","kin","other"}:
    - ruler:   dynasty member who held a title  -> "m. {spouse}"
    - kin:     dynasty member, no title         -> "m. {spouse}"
    - consort: outsider with a spouse           -> "wife/husband/spouse of {spouse}"
    - other:   none of the above                -> no relation
    """
    spouse = _fam_name(family.get("primary_spouse")) or _fam_name(family.get("spouse"))
    is_dynast = char_dynasty is not None and char_dynasty == player_dynasty
    if is_dynast and has_title:
        return "ruler", (f"m. {spouse}" if spouse else None)
    if is_dynast:
        return "kin", (f"m. {spouse}" if spouse else None)
    if spouse:
        word = "wife" if female else "husband" if female is False else "spouse"
        return "consort", f"{word} of {spouse}"
    return "other", None


def _to_member(
    char: Character,
    *,
    is_player: bool,
    has_biography: bool,
) -> DynastyMember:
    return DynastyMember(
        ck3_id=char.ck3_id,
        first_name=char.first_name,
        dynasty_name=char.dynasty_name,
        nickname=char.nickname,
        birth_date=char.birth_date,
        death_date=char.death_date,
        is_player=is_player,
        has_biography=has_biography,
        coa_json=parse_coa_json(char.coa_json),
    )


def _excerpt(body: str) -> str:
    """First sentence-ish slice of a biography body, capped at length.

    We try to break on the first ``. `` after the cap — keeps the cut
    from landing mid-word — but fall back to a hard slice if no break is
    near.
    """
    if len(body) <= _VITA_EXCERPT_LEN:
        return body.strip()
    head = body[:_VITA_EXCERPT_LEN]
    # Look for a sentence break in the last 60 chars of the cap window.
    for break_seq in (". ", "? ", "! "):
        idx = head.rfind(break_seq, _VITA_EXCERPT_LEN - 60)
        if idx >= 0:
            return head[: idx + 1].strip() + " …"
    return head.rstrip() + "…"


def _birth_sort_key(char: Character) -> tuple[int, str, int]:
    """Sort by birth_date as a CK3-style YYYY.M.D string, treating
    unparseable / null dates as last (so members with unknown birth
    don't capsize the chain). Ties break by ck3_id ascending."""
    raw = char.birth_date or ""
    if not raw:
        return (1, "", char.ck3_id)
    try:
        year_str, month_str, day_str = raw.split(".")
        year = int(year_str)
        month = int(month_str)
        day = int(day_str)
        return (0, f"{year:05d}.{month:02d}.{day:02d}", char.ck3_id)
    except (ValueError, AttributeError):
        return (1, raw, char.ck3_id)


def _death_sort_key_desc(date: str | None) -> tuple[int, str]:
    """Sort vita roll by death date descending: most recent first.

    Returns a key that puts known-recent dates first and unknown last.
    """
    if not date:
        return (1, "")
    try:
        year_str, month_str, day_str = date.split(".")
        year = int(year_str)
        month = int(month_str)
        day = int(day_str)
        # Negate by subtracting from a large constant for desc order.
        return (0, f"{99999 - year:05d}.{99 - month:02d}.{99 - day:02d}")
    except (ValueError, AttributeError):
        return (1, "")


def _latest_biography_by_character(
    bio_rows: list[tuple[int, str, str, int]],
) -> dict[int, tuple[str, str]]:
    """Pick the highest-version ``(body, generated_at)`` per character.

    ``biographies_for_characters`` is documented unsorted, so the latest
    biography must be chosen by comparing ``version`` — not by row order.
    ck3_chronicler-27ov.25 (audit M-A3): the old `version > 0` was always true
    (versions start at 1), degenerating to last-row-wins — correct only by
    accident of SQLite's rowid ordering.
    """
    latest_bio_by_char: dict[int, tuple[str, str]] = {}
    best_version: dict[int, int] = {}
    for char_id, body, generated_at, version in bio_rows:
        if char_id not in best_version or version > best_version[char_id]:
            best_version[char_id] = version
            latest_bio_by_char[char_id] = (body, generated_at)
    return latest_bio_by_char


@router.get("/dynasty", response_model=DynastyResponse)
def get_dynasty(
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> DynastyResponse:
    """Assembled view of the player's dynasty.

    404 when the campaign has no characters with a dynasty_name set —
    that's the empty-DB case (no save adopted yet, or save adopted
    but heraldry resolver hasn't run). UIs should render the page's
    empty state rather than treating it as a hard error.
    """
    player, dynasty_name = _resolve_player_dynasty(session, campaign)
    if not dynasty_name:
        raise HTTPException(
            status_code=404,
            detail=(
                "No dynasty resolved for this campaign yet — adopt a save "
                "first or wait for the next save-tail tick."
            ),
        )

    members_q = list_characters_by_dynasty(session, dynasty_name)
    members_sorted = sorted(members_q, key=_birth_sort_key)

    bio_rows = biographies_for_characters(session, [m.ck3_id for m in members_sorted])
    latest_bio_by_char = _latest_biography_by_character(bio_rows)

    player_id = campaign.current_player_character_id
    members_out: list[DynastyMember] = [
        _to_member(
            c,
            is_player=(player_id is not None and c.ck3_id == player_id),
            has_biography=c.ck3_id in latest_bio_by_char,
        )
        for c in members_sorted
    ]

    # Vita roll: chronicled members ordered by death date desc.
    vita: list[DynastyVitaEntry] = []
    for c in members_sorted:
        bio = latest_bio_by_char.get(c.ck3_id)
        if bio is None:
            continue
        body, generated_at = bio
        vita.append(
            DynastyVitaEntry(
                ck3_id=c.ck3_id,
                first_name=c.first_name,
                nickname=c.nickname,
                death_date=c.death_date,
                biography_excerpt=_excerpt(body),
                generated_at=generated_at,
            )
        )
    vita.sort(key=lambda v: _death_sort_key_desc(v.death_date))

    founder_member = members_out[0] if members_out else None
    current_head_member = next((m for m in members_out if m.is_player), None) if player else None

    return DynastyResponse(
        dynasty_name=dynasty_name,
        member_count=len(members_out),
        chronicled_count=len(latest_bio_by_char),
        founding_date=members_sorted[0].birth_date if members_sorted else None,
        founding_paragraph=blurb_from_chronicle(campaign.closing_chronicle),
        founder=founder_member,
        current_head=current_head_member,
        members=members_out,
        vita_roll=vita,
    )


# ck3_chronicler (2026-05-09): Biographies tab on the campaign-overview
# page. Lists every chronicled character in the campaign with the latest
# version of their biography, ordered chronologically by death date
# (so the page reads as a chronicle of departures). Living characters
# without a death date sort to the end so the active player surfaces
# beneath the deceased predecessors.
def _bio_sort_key(entry: BiographyListEntry) -> tuple[int, str]:
    """Sort by death date ascending; living characters (no death) last,
    secondary sort on birth date so two living siblings have a stable
    order. Unparseable dates fall back to lexicographic compare."""
    death = entry.death_date
    if death:
        try:
            year_str, month_str, day_str = death.split(".")
            return (
                0,
                f"{int(year_str):05d}.{int(month_str):02d}.{int(day_str):02d}",
            )
        except (ValueError, AttributeError):
            return (0, death)
    return (1, entry.birth_date or "")


@router.get("/biographies", response_model=list[BiographyListEntry])
def list_campaign_biographies(
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
) -> list[BiographyListEntry]:
    """All chronicled characters' latest biographies in chronological
    order.

    One row per character (the latest ``version`` wins; older revisions
    stay queryable via the per-character endpoint). Sorted by in-game
    death date ascending — living characters (no death recorded yet)
    sort to the end so the page reads as a chronicle of departures with
    the still-active player(s) at the bottom.
    """
    rows = list_latest_biographies_with_character(session)
    # ck3_chronicler-lmex: role grouping is classified relative to the
    # player's dynasty — resolve it once for the whole list.
    _player, player_dynasty = _resolve_player_dynasty(session, campaign)
    entries: list[BiographyListEntry] = []
    for char, body, generated_at, version in rows:
        role, relation = _classify_bio_role(
            char_dynasty=char.dynasty_name,
            player_dynasty=player_dynasty,
            has_title=char.primary_title_json is not None,
            family=parse_family_data(char.save_snapshot_json),
            female=char.female,
        )
        entries.append(
            BiographyListEntry(
                ck3_id=char.ck3_id,
                first_name=char.first_name,
                nickname=char.nickname,
                dynasty_name=char.dynasty_name,
                female=char.female,
                birth_date=char.birth_date,
                death_date=char.death_date,
                biography_excerpt=_excerpt(body),
                generated_at=generated_at,
                version=version,
                coa_json=parse_coa_json(char.coa_json),
                primary_title=parse_primary_title(char.primary_title_json),
                held_titles=parse_held_titles(char.held_titles_json),
                role=role,
                relation=relation,
            )
        )
    entries.sort(key=_bio_sort_key)
    return entries

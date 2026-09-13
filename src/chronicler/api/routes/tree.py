"""Family-tree route — ancestors, descendants, spouses, siblings.

Walks each character's persisted ``save_snapshot_json.family_data``
block, recursing through mother/father (ancestors) and child
(descendants) up to a caller-controlled depth. Spouses (current,
former, concubines, betrothed) and siblings (via shared parent) are
gathered shallowly — there is no concept of "depth" for those.

Family IDs in ``family_data`` may be either raw ints (no name_lookup
was passed at extract time) or ``{id, name}`` dicts (name_lookup was
applied — see ``chronicler.save.parse._resolve_family_names``). We
accept both shapes.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from chronicler.api.dependencies import (
    get_campaign_including_archived,
    get_session_including_archived,
)
from chronicler.api.serializers import (
    extract_family_ids,
    parse_coa_json,
    parse_family_data,
)
from chronicler.db.models import Character
from chronicler.db.registry import Campaign
from chronicler.db.repository import get_character


# --- response/request models (co-located, ck3_chronicler-27ov.48) ---
class FamilyTreeNode(BaseModel):
    """One related character in the family-tree response (ck3_chronicler-b04)."""

    ck3_id: int
    first_name: str | None
    nickname: str | None
    birth_date: str | None
    death_date: str | None
    relation: str
    depth: int
    # ck3_chronicler-h9u6 (slice 2.1 of 4y0v, 2026-05-09): persisted CoA
    # so the LineagePage tree renders the same real heraldry the
    # Codex/Tracked/Search cards render. Null when the character has no
    # resolved CoA yet (untracked at parse time, save-tail hasn't run
    # since the 7ao migration, or the row is otherwise missing one) —
    # FE falls through to the procedural seeded shield via
    # HeraldryWithFallback.
    coa_json: dict[str, Any] | None = None


class FamilyTreeResponse(BaseModel):
    """Pre-categorised flat lists of relatives for one character.

    Each list is sorted by ``depth`` then ``ck3_id`` for stable output.
    Unknown family IDs (referenced from family_data but not in the
    Character DB) are silently skipped — the chronicler only persists
    characters touched by events or tracked explicitly.
    """

    self_node: FamilyTreeNode
    ancestors: list[FamilyTreeNode]
    descendants: list[FamilyTreeNode]
    spouses: list[FamilyTreeNode]
    siblings: list[FamilyTreeNode]


def _batch_load(session: Session, ids: Iterable[int]) -> dict[int, Character]:
    """audit F-20 / ck3_chronicler-ofja: one bulk lookup instead of N
    per-row session.get() calls. Returns a dict keyed by ck3_id; missing
    rows are simply absent (the walkers skip them via .get(...))."""
    id_list = [i for i in ids]
    if not id_list:
        return {}
    rows = session.execute(select(Character).where(Character.ck3_id.in_(id_list))).scalars().all()
    return {row.ck3_id: row for row in rows}


# ck3_chronicler-vtmx (63yw slice 2): CK3 save format stores
# parent→child only on the parent's record (family_data.child list).
# Child records carry no mother/father IDs at all, so we build an
# upward index per request by scanning every parent's child list.
# Parent gender comes from Character.female; when None we fall back to
# "father" (CK3 default-male convention) — better than dropping the
# edge entirely.
def _build_parent_index(session: Session) -> dict[int, list[tuple[int, str]]]:
    """Scan all persisted family_data to build child_id → [(parent_id, parent_key)].

    Returns an empty dict when nothing is tracked yet. parent_key is
    ``'mother'`` or ``'father'`` inferred from the parent's ``female``
    column.
    """
    index: dict[int, list[tuple[int, str]]] = {}
    rows = session.execute(
        select(Character.ck3_id, Character.female, Character.save_snapshot_json).where(
            Character.save_snapshot_json.is_not(None)
        )
    ).all()
    for parent_id, female, snap_json in rows:
        fam = parse_family_data(snap_json)
        if not fam:
            continue
        parent_key = "mother" if female else "father"
        for cid in extract_family_ids(fam.get("child")):
            index.setdefault(cid, []).append((parent_id, parent_key))
    return index


# ck3_chronicler-vrqa: process-local cache for _build_parent_index keyed
# by (campaign_id, last_save_ingested_at). Family edges only change when
# save-tail commits a new tick — and every tick updates last_save_ingested_at
# (including the 87pi zero-event heartbeat). So the cache key cleanly
# tracks "the persisted family_data could have changed since we last
# built." Before this lands, each /characters/{id}/family-tree request
# re-scanned every Character row and json.loads'd a 5-50KB save_snapshot_json
# blob per row — N+1 JSON decode whenever ChroniclePage navigated between
# siblings.
_PARENT_INDEX_CACHE: dict[str, tuple[str | None, dict[int, list[tuple[int, str]]]]] = {}
_PARENT_INDEX_CACHE_LOCK = threading.Lock()


def _cached_parent_index(session: Session, campaign: Campaign) -> dict[int, list[tuple[int, str]]]:
    """Return the parent index for ``campaign``, rebuilding only when
    save-tail has advanced past the cached tick.

    Hot path: read campaign.last_save_ingested_at, hit a tiny in-memory
    dict, return the cached index. Cold path: rebuild via
    :func:`_build_parent_index` and store under the new key.
    """
    marker = campaign.last_save_ingested_at
    cached = _PARENT_INDEX_CACHE.get(campaign.id)
    if cached is not None and cached[0] == marker:
        return cached[1]
    index = _build_parent_index(session)
    with _PARENT_INDEX_CACHE_LOCK:
        _PARENT_INDEX_CACHE[campaign.id] = (marker, index)
    return index


def invalidate_parent_index_cache(campaign_id: str | None = None) -> None:
    """Drop cached parent indexes — test seam + safety valve.

    With no arg, clears the whole cache. With a campaign id, drops just
    that campaign's entry. The cache key already self-invalidates on
    tick advance; this exists for tests that mutate the DB without
    going through save-tail.
    """
    with _PARENT_INDEX_CACHE_LOCK:
        if campaign_id is None:
            _PARENT_INDEX_CACHE.clear()
        else:
            _PARENT_INDEX_CACHE.pop(campaign_id, None)


router = APIRouter(prefix="/api/campaigns/{name}", tags=["family-tree"])

DEFAULT_DEPTH = 3
MAX_DEPTH = 10

_CHILD_KEYS = ("child",)
_CURRENT_SPOUSE_SINGLE = ("primary_spouse", "concubinist")
_CURRENT_SPOUSE_LIST = ("spouse", "concubine", "betrothed")
_FORMER_SPOUSE_LIST = ("former_spouses",)


def _to_node(char: Character, *, relation: str, depth: int) -> FamilyTreeNode:
    return FamilyTreeNode(
        ck3_id=char.ck3_id,
        first_name=char.first_name,
        nickname=char.nickname,
        birth_date=char.birth_date,
        death_date=char.death_date,
        relation=relation,
        depth=depth,
        # ck3_chronicler-h9u6: parse the persisted Character.coa_json
        # column (TEXT) into the dict shape the FE consumes. The
        # _batch_load BFS already pulls the column as part of the
        # SELECT so this is a no-extra-query attach. Malformed JSON
        # falls through to None — FE renders the procedural fallback.
        coa_json=parse_coa_json(char.coa_json),
    )


def _ancestor_relation(depth: int, parent_key: str) -> str:
    """Return e.g. 'mother', 'grandfather', 'great_grandmother'."""
    base = parent_key  # "mother" or "father"
    if depth == 1:
        return base
    if depth == 2:
        return f"grand{base}"
    return ("great_" * (depth - 2)) + f"grand{base}"


def _descendant_relation(depth: int) -> str:
    if depth == 1:
        return "child"
    if depth == 2:
        return "grandchild"
    return ("great_" * (depth - 2)) + "grandchild"


# audit F-20: each level of BFS now batches one IN-list query for
# every character it needs to load. With ancestor_depth=10 a deep tree
# was issuing 100+ session.get() calls per request; now it issues at
# most max_depth queries.


def _walk_ancestors(
    session: Session,
    *,
    seed: Character,
    max_depth: int,
    parent_index: dict[int, list[tuple[int, str]]],
) -> list[FamilyTreeNode]:
    """BFS up the parent chain via the prebuilt reverse index.

    ck3_chronicler-vtmx: child records in CK3 saves carry no parent IDs,
    so the upward walk consults the parent_index built by
    ``_build_parent_index``. The DB hit per generation is unchanged
    (one IN-list ``_batch_load`` to materialise the parent rows for
    relation/COA fields and the next hop).
    """
    out: list[FamilyTreeNode] = []
    visited: set[int] = {seed.ck3_id}
    frontier: list[tuple[Character, int]] = [(seed, 0)]
    while frontier:
        requested: list[tuple[int, str, int]] = []  # (pid, parent_key, child_depth)
        for char, depth in frontier:
            if depth >= max_depth:
                continue
            child_depth = depth + 1
            for pid, parent_key in parent_index.get(char.ck3_id, []):
                if pid in visited:
                    continue
                visited.add(pid)
                requested.append((pid, parent_key, child_depth))
        if not requested:
            break
        loaded = _batch_load(session, (pid for pid, _, _ in requested))
        next_frontier: list[tuple[Character, int]] = []
        for pid, parent_key, child_depth in requested:
            parent = loaded.get(pid)
            if parent is None:
                continue
            out.append(
                _to_node(
                    parent,
                    relation=_ancestor_relation(child_depth, parent_key),
                    depth=child_depth,
                )
            )
            next_frontier.append((parent, child_depth))
        frontier = next_frontier
    return out


def _walk_descendants(
    session: Session,
    *,
    seed: Character,
    max_depth: int,
) -> list[FamilyTreeNode]:
    """BFS down the child chain, batched one query per generation."""
    out: list[FamilyTreeNode] = []
    visited: set[int] = {seed.ck3_id}
    frontier: list[tuple[Character, int]] = [(seed, 0)]
    while frontier:
        requested: list[tuple[int, int]] = []  # (cid, child_depth)
        for char, depth in frontier:
            if depth >= max_depth:
                continue
            fam = parse_family_data(char.save_snapshot_json)
            child_depth = depth + 1
            for child_key in _CHILD_KEYS:
                for cid in extract_family_ids(fam.get(child_key)):
                    if cid in visited:
                        continue
                    visited.add(cid)
                    requested.append((cid, child_depth))
        if not requested:
            break
        loaded = _batch_load(session, (cid for cid, _ in requested))
        next_frontier: list[tuple[Character, int]] = []
        for cid, child_depth in requested:
            child = loaded.get(cid)
            if child is None:
                continue
            out.append(
                _to_node(
                    child,
                    relation=_descendant_relation(child_depth),
                    depth=child_depth,
                )
            )
            next_frontier.append((child, child_depth))
        frontier = next_frontier
    return out


def _gather_spouses(session: Session, *, seed: Character) -> list[FamilyTreeNode]:
    """Single-query batch over every spouse key on the seed's family_data."""
    fam = parse_family_data(seed.save_snapshot_json)
    requested: list[tuple[int, str]] = []  # (sid, relation)
    seen: set[int] = set()
    for key in _CURRENT_SPOUSE_SINGLE + _CURRENT_SPOUSE_LIST:
        relation = "spouse" if key in ("spouse", "primary_spouse") else key
        for sid in extract_family_ids(fam.get(key)):
            if sid in seen or sid == seed.ck3_id:
                continue
            seen.add(sid)
            requested.append((sid, relation))
    for key in _FORMER_SPOUSE_LIST:
        for sid in extract_family_ids(fam.get(key)):
            if sid in seen or sid == seed.ck3_id:
                continue
            seen.add(sid)
            requested.append((sid, "former_spouse"))
    if not requested:
        return []
    loaded = _batch_load(session, (sid for sid, _ in requested))
    out: list[FamilyTreeNode] = []
    for sid, relation in requested:
        spouse = loaded.get(sid)
        if spouse is None:
            continue
        out.append(_to_node(spouse, relation=relation, depth=1))
    return out


def _gather_siblings(
    session: Session,
    *,
    seed: Character,
    parent_index: dict[int, list[tuple[int, str]]],
) -> list[FamilyTreeNode]:
    """Siblings are the children of either parent, minus the seed itself.

    Half-siblings (sharing only one parent) get ``relation='half_sibling'``;
    full siblings (sharing both) get ``relation='sibling'``. When only one
    parent is known we conservatively label everyone ``sibling`` — there's
    no way to tell halves from fulls without both parents.

    ck3_chronicler-vtmx: seed's parent IDs come from the prebuilt
    upward index (child records have no parent keys in CK3 saves).
    """
    parent_ids = {pid for pid, _ in parent_index.get(seed.ck3_id, [])}
    if not parent_ids:
        return []

    parents = _batch_load(session, parent_ids)
    # Map child_id -> set of parent_ids that claim them.
    sibling_parents: dict[int, set[int]] = {}
    for pid, parent in parents.items():
        pf = parse_family_data(parent.save_snapshot_json)
        for cid in extract_family_ids(pf.get("child")):
            if cid == seed.ck3_id:
                continue
            sibling_parents.setdefault(cid, set()).add(pid)

    if not sibling_parents:
        return []
    siblings = _batch_load(session, sibling_parents.keys())
    full_parent_count = len(parent_ids)
    out: list[FamilyTreeNode] = []
    for sid, shared in sorted(sibling_parents.items()):
        sib = siblings.get(sid)
        if sib is None:
            continue
        if full_parent_count >= 2 and len(shared) >= 2:
            relation = "sibling"
        elif full_parent_count >= 2:
            relation = "half_sibling"
        else:
            # Only one parent known on the seed — can't distinguish halves
            relation = "sibling"
        out.append(_to_node(sib, relation=relation, depth=1))
    return out


def _sort_nodes(nodes: list[FamilyTreeNode]) -> list[FamilyTreeNode]:
    return sorted(nodes, key=lambda n: (n.depth, n.ck3_id))


@router.get(
    "/characters/{ck3_id}/family-tree",
    response_model=FamilyTreeResponse,
)
def get_family_tree(
    ck3_id: int,
    campaign: Campaign = Depends(get_campaign_including_archived),
    session: Session = Depends(get_session_including_archived),
    ancestor_depth: int = Query(DEFAULT_DEPTH, ge=0, le=MAX_DEPTH),
    descendant_depth: int = Query(DEFAULT_DEPTH, ge=0, le=MAX_DEPTH),
) -> FamilyTreeResponse:
    """Walk the persisted family graph for one character.

    Recursion bounds: ``ancestor_depth`` and ``descendant_depth`` each
    default to 3 generations and cap at 10. Spouses (current + former)
    and siblings (via shared parents) are always one hop, no depth knob.

    Returns 404 if the character is unknown. If the character has no
    persisted ``save_snapshot_json`` (e.g. untracked, save-tail hasn't
    run yet), all four lists come back empty — that's the correct
    "we don't know their family yet" answer.
    """
    seed = get_character(session, ck3_id)
    if seed is None:
        raise HTTPException(
            status_code=404,
            detail=f"character {ck3_id} not in campaign {campaign.name}",
        )
    # ck3_chronicler-vtmx: one scan of all persisted family_data per
    # request feeds both the ancestor walk and the sibling lookup.
    # ck3_chronicler-vrqa: cached across requests by (campaign,
    # last_save_ingested_at) so navigating between siblings on
    # ChroniclePage / LineagePage no longer re-decodes every persisted
    # save_snapshot_json blob on each render.
    parent_index = _cached_parent_index(session, campaign)
    return FamilyTreeResponse(
        self_node=_to_node(seed, relation="self", depth=0),
        ancestors=_sort_nodes(
            _walk_ancestors(
                session,
                seed=seed,
                max_depth=ancestor_depth,
                parent_index=parent_index,
            )
        ),
        descendants=_sort_nodes(_walk_descendants(session, seed=seed, max_depth=descendant_depth)),
        spouses=_sort_nodes(_gather_spouses(session, seed=seed)),
        siblings=_sort_nodes(_gather_siblings(session, seed=seed, parent_index=parent_index)),
    )

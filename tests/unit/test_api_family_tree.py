"""HTTP-level tests for the chronicler family-tree API (split from the
test_api monolith — ck3_chronicler-27ov.67 / audit M-T1).

Uses FastAPI's TestClient against a fresh tmp_path registry + fixture
campaign DBs (the shared ``api`` / ``make_campaign`` / ``client`` fixtures
live in tests/conftest.py). No actual HTTP, just ASGI in-process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from chronicler.db import (
    make_engine_for_path,
)
from chronicler.db.repository import (
    upsert_character,
)
from tests.helpers.api import CampaignHarness

# --- ck3_chronicler-b04: family-tree endpoint ---


def _seed_three_gen_tree(s: Session) -> None:
    """Build a synthetic 3-generation tree:

    gen 0 (grandparents):  100 (Alfred), 101 (Eadgyth) | 102 (Beorn), 103 (Sigrith)
    gen 1 (parents):       200 (Cuthbert), 201 (Mildgyth)
                           C is child of 100/101, M is child of 102/103
    gen 1 (uncles/aunts):  202 (Wulfstan, child of 100/101 = full sibling of 200)
                           203 (Halfdan, child of 100/999-unknown = HALF sibling of 200)
    gen 2 (focal):         300 (Edward) child of 200/201, married to 400 (Eadgifu)
                           301 (Hild)   child of 200/201 = full sibling of 300
    gen 2 (spouse line):   400 (Eadgifu, spouse of 300, no parents in DB)
                           401 (Sigrun, former spouse of 300)
    gen 3 (children):      500 (Athelstan) child of 300/400
                           501 (Edmund)    child of 300/400
    gen 4 (grandchildren): 600 (Edgar)     child of 500
    """

    def _snap(family: dict[str, object]) -> str:
        return json.dumps({"family_data": family}, separators=(",", ":"))

    # ck3_chronicler-vtmx: parent labeling is driven by Character.female,
    # so the parent rows whose children appear in the index need their
    # gender set explicitly. The mother/father keys on child records are
    # vestigial — left in place so the test fixture also exercises the
    # "child carries hint" shape, but the labels now come from the
    # parent's female column.
    rows: list[dict[str, object]] = [
        # Grandparents (paternal — Alfred + Eadgyth share children 200, 202)
        dict(
            ck3_id=100,
            first_name="Alfred",
            female=False,
            death_date="0975.1.1",
            save_snapshot_json=_snap({"child": [200, 202, 203]}),
        ),
        dict(
            ck3_id=101,
            first_name="Eadgyth",
            female=True,
            death_date="0978.1.1",
            save_snapshot_json=_snap({"child": [200, 202]}),
        ),
        # Maternal grandparents
        dict(
            ck3_id=102,
            first_name="Beorn",
            female=False,
            death_date="0980.1.1",
            save_snapshot_json=_snap({"child": [201]}),
        ),
        dict(
            ck3_id=103,
            first_name="Sigrith",
            female=True,
            death_date="0982.1.1",
            save_snapshot_json=_snap({"child": [201]}),
        ),
        # Parents
        dict(
            ck3_id=200,
            first_name="Cuthbert",
            female=False,
            death_date="1010.6.1",
            save_snapshot_json=_snap(
                {
                    "father": 100,
                    "mother": 101,
                    "spouse": [201],
                    "child": [300, 301],
                }
            ),
        ),
        dict(
            ck3_id=201,
            first_name="Mildgyth",
            female=True,
            nickname="the Wise",
            save_snapshot_json=_snap(
                {
                    "father": 102,
                    "mother": 103,
                    "spouse": [200],
                    "child": [300, 301],
                }
            ),
        ),
        # Full sibling of parent (uncle of focal char) - shares both grandparents
        dict(
            ck3_id=202,
            first_name="Wulfstan",
            female=False,
            save_snapshot_json=_snap({"father": 100, "mother": 101}),
        ),
        # Half sibling of parent (different mother, unknown to DB)
        dict(
            ck3_id=203,
            first_name="Halfdan",
            female=False,
            save_snapshot_json=_snap({"father": 100, "mother": 999}),
        ),
        # Focal: Edward 300
        dict(
            ck3_id=300,
            first_name="Edward",
            female=False,
            nickname="the Confessor",
            birth_date="1000.5.1",
            save_snapshot_json=_snap(
                {
                    "father": 200,
                    "mother": 201,
                    "primary_spouse": 400,
                    "spouse": [400],
                    "former_spouses": [401],
                    "child": [500, 501],
                }
            ),
        ),
        # Full sibling of focal
        dict(
            ck3_id=301,
            first_name="Hild",
            female=True,
            save_snapshot_json=_snap({"father": 200, "mother": 201}),
        ),
        # Spouses
        dict(
            ck3_id=400,
            first_name="Eadgifu",
            female=True,
            save_snapshot_json=_snap({"spouse": [300], "child": [500, 501]}),
        ),
        dict(
            ck3_id=401,
            first_name="Sigrun",
            female=True,
            save_snapshot_json=_snap({"former_spouses": [300]}),
        ),
        # Children
        dict(
            ck3_id=500,
            first_name="Athelstan",
            female=False,
            save_snapshot_json=_snap({"father": 300, "mother": 400, "child": [600]}),
        ),
        dict(
            ck3_id=501,
            first_name="Edmund",
            female=False,
            save_snapshot_json=_snap({"father": 300, "mother": 400}),
        ),
        # Grandchild
        dict(
            ck3_id=600,
            first_name="Edgar",
            female=False,
            save_snapshot_json=_snap({"father": 500}),
        ),
    ]
    for r in rows:
        upsert_character(s, **r)


@pytest.fixture
def tree_client(make_campaign: Callable[..., TestClient]) -> TestClient:
    """A synthetic 3-generation ``tree`` campaign for the family-tree tests."""
    return make_campaign("tree", seed=_seed_three_gen_tree)


def test_family_tree_404_unknown_character(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/99999/family-tree")
    assert resp.status_code == 404


def test_family_tree_self_node_present(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree")
    assert resp.status_code == 200
    data = resp.json()
    me = data["self_node"]
    assert me["ck3_id"] == 300
    assert me["first_name"] == "Edward"
    assert me["nickname"] == "the Confessor"
    assert me["relation"] == "self"
    assert me["depth"] == 0


def test_family_tree_ancestors_walk_three_generations(tree_client: TestClient) -> None:
    """Default depth=3 walks parents (1) + grandparents (2). The synthetic
    tree has no great-grandparents, so depth-3 results match depth-2."""
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["ancestors"]}
    # Parents
    assert 200 in by_id
    assert by_id[200]["relation"] == "father"
    assert by_id[200]["depth"] == 1
    assert 201 in by_id
    assert by_id[201]["relation"] == "mother"
    # Grandparents (depth 2)
    assert by_id[100]["relation"] == "grandfather"
    assert by_id[100]["depth"] == 2
    assert by_id[101]["relation"] == "grandmother"
    assert by_id[102]["relation"] == "grandfather"
    assert by_id[103]["relation"] == "grandmother"
    # Sorted by (depth, ck3_id)
    depths = [n["depth"] for n in data["ancestors"]]
    assert depths == sorted(depths)


def test_family_tree_ancestor_depth_zero_returns_no_ancestors(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree?ancestor_depth=0")
    data = resp.json()
    assert data["ancestors"] == []
    # Other categories unaffected
    assert len(data["descendants"]) > 0


def test_family_tree_ancestor_depth_one_only_parents(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree?ancestor_depth=1")
    data = resp.json()
    ids = sorted(n["ck3_id"] for n in data["ancestors"])
    assert ids == [200, 201]


def test_family_tree_descendants_walk_two_generations(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree?descendant_depth=2")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["descendants"]}
    # Children
    assert by_id[500]["relation"] == "child"
    assert by_id[500]["depth"] == 1
    assert by_id[501]["relation"] == "child"
    # Grandchild
    assert by_id[600]["relation"] == "grandchild"
    assert by_id[600]["depth"] == 2


def test_family_tree_descendants_great_grandchild_label(tree_client: TestClient) -> None:
    """When the chain is deeper, labels should add great_ prefixes."""
    # Edgar (600) is the deepest; from Alfred's perspective, descendants
    # at depth 4 are great_great_grandchildren.
    resp = tree_client.get("/api/campaigns/tree/characters/100/family-tree?descendant_depth=5")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["descendants"]}
    assert by_id[600]["depth"] == 4
    assert by_id[600]["relation"] == "great_great_grandchild"


def test_family_tree_spouses_split_current_and_former(tree_client: TestClient) -> None:
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["spouses"]}
    assert by_id[400]["relation"] == "spouse"
    assert by_id[401]["relation"] == "former_spouse"
    assert by_id[400]["depth"] == 1


def test_family_tree_spouses_dedupe_primary_and_list(tree_client: TestClient) -> None:
    """Edward has both primary_spouse=400 and spouse=[400]. Should appear once."""
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree")
    data = resp.json()
    spouse_ids = [n["ck3_id"] for n in data["spouses"]]
    assert spouse_ids.count(400) == 1


def test_family_tree_siblings_full_vs_half(tree_client: TestClient) -> None:
    """Cuthbert (200) has full sibling Wulfstan (102, sharing both parents)
    and half sibling Halfdan (203, sharing only father 100)."""
    resp = tree_client.get("/api/campaigns/tree/characters/200/family-tree")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["siblings"]}
    assert by_id[202]["relation"] == "sibling"
    assert by_id[203]["relation"] == "half_sibling"


def test_family_tree_siblings_full_when_shared(tree_client: TestClient) -> None:
    """Edward (300) and Hild (301) share both parents → full siblings."""
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree")
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["siblings"]}
    assert by_id[301]["relation"] == "sibling"


def test_family_tree_skips_unknown_family_ids(tree_client: TestClient) -> None:
    """Halfdan's mother is 999 which isn't in the DB. Walking up his
    ancestors should silently skip the missing parent."""
    resp = tree_client.get("/api/campaigns/tree/characters/203/family-tree")
    data = resp.json()
    ancestor_ids = [n["ck3_id"] for n in data["ancestors"]]
    assert 100 in ancestor_ids  # known father
    assert 999 not in ancestor_ids


def test_family_tree_attaches_coa_json_per_node(api: CampaignHarness) -> None:
    """ck3_chronicler-h9u6 (4y0v slice 2.1): family-tree nodes carry the
    persisted Character.coa_json so the LineagePage can render real
    heraldry without a follow-up batch query. Null when the column is
    empty (untracked / unrefreshed character)."""

    def _snap(family: dict[str, object]) -> str:
        return json.dumps({"family_data": family}, separators=(",", ":"))

    coa_payload = json.dumps(
        {
            "pattern": "pattern_solid.dds",
            "color1": "black",
            "color2": "green",
            "color3": "yellow",
        }
    )

    def _seed(s: Session) -> None:
        upsert_character(
            s,
            ck3_id=1,
            first_name="Father",
            save_snapshot_json=_snap({"child": [2]}),
            coa_json=coa_payload,
        )
        upsert_character(
            s,
            ck3_id=2,
            first_name="Child",
            save_snapshot_json=_snap({"father": 1}),
            coa_json=None,
        )

    c = api.make_campaign("coatree", seed=_seed)
    resp = c.get("/api/campaigns/coatree/characters/2/family-tree")
    assert resp.status_code == 200
    data = resp.json()
    # Self node has no coa.
    assert data["self_node"]["coa_json"] is None
    # Father (ancestor) carries the parsed dict, not the raw string.
    ancestors = {n["ck3_id"]: n for n in data["ancestors"]}
    assert 1 in ancestors
    assert ancestors[1]["coa_json"] == {
        "pattern": "pattern_solid.dds",
        "color1": "black",
        "color2": "green",
        "color3": "yellow",
    }


def test_family_tree_no_save_snapshot_returns_empty_lists(
    tree_client: TestClient,
) -> None:
    """A character with no save_snapshot_json gets empty family lists,
    not 404 — caller can still use the self_node."""
    # Insert a fresh char with no snapshot
    from sqlalchemy.orm import sessionmaker

    db_path = tree_client.get("/api/campaigns/tree").json()["db_path"]
    engine = make_engine_for_path(Path(db_path))
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        upsert_character(s, ck3_id=12345, first_name="Ghost")
        s.commit()
    engine.dispose()

    resp = tree_client.get("/api/campaigns/tree/characters/12345/family-tree")
    assert resp.status_code == 200
    data = resp.json()
    assert data["self_node"]["first_name"] == "Ghost"
    assert data["ancestors"] == []
    assert data["descendants"] == []
    assert data["spouses"] == []
    assert data["siblings"] == []


def test_family_tree_handles_name_resolved_dict_shape(api: CampaignHarness) -> None:
    """When extract_character_record was passed a name_lookup, family_data
    values come back as {id, name} dicts rather than ints. The endpoint
    must tolerate both shapes — serializers.extract_family_ids unwraps
    {id} to ints both in the descendant walk and in the upward index
    built from each parent's child list (ck3_chronicler-vtmx)."""

    def _snap(family: dict[str, object]) -> str:
        return json.dumps({"family_data": family}, separators=(",", ":"))

    # Each parent carries Cuthbert in their child list (the canonical
    # CK3 shape) using the dict {id, name} variant.
    alfred_snap = _snap({"child": [{"id": 1, "name": "Cuthbert"}]})
    eadgyth_snap = _snap({"child": [{"id": 1, "name": "Cuthbert"}]})
    cuthbert_snap = _snap(
        {
            "child": [{"id": 20, "name": "Edward"}, {"id": 21, "name": "Hild"}],
            "spouse": [{"id": 30, "name": "Eadgifu"}],
        }
    )

    def _seed(s: Session) -> None:
        upsert_character(
            s, ck3_id=10, first_name="Alfred", female=False, save_snapshot_json=alfred_snap
        )
        upsert_character(
            s, ck3_id=11, first_name="Eadgyth", female=True, save_snapshot_json=eadgyth_snap
        )
        upsert_character(s, ck3_id=20, first_name="Edward")
        upsert_character(s, ck3_id=21, first_name="Hild")
        upsert_character(s, ck3_id=30, first_name="Eadgifu")
        upsert_character(s, ck3_id=1, first_name="Cuthbert", save_snapshot_json=cuthbert_snap)

    c = api.make_campaign("dictshape", seed=_seed)
    resp = c.get("/api/campaigns/dictshape/characters/1/family-tree")
    assert resp.status_code == 200
    data = resp.json()
    ancestors = {n["ck3_id"]: n for n in data["ancestors"]}
    descendant_ids = sorted(n["ck3_id"] for n in data["descendants"])
    spouse_ids = sorted(n["ck3_id"] for n in data["spouses"])
    assert sorted(ancestors) == [10, 11]
    assert ancestors[10]["relation"] == "father"
    assert ancestors[11]["relation"] == "mother"
    assert descendant_ids == [20, 21]
    assert spouse_ids == [30]


def test_family_tree_walks_ancestors_via_upward_index(api: CampaignHarness) -> None:
    """ck3_chronicler-vtmx regression: real CK3 saves store parent→child
    only on the parent's record. Child records have no mother/father IDs.
    Walking ancestors must succeed by scanning every parent's child list
    and labelling via the parent's ``female`` column."""

    def _snap(family: dict[str, object]) -> str:
        return json.dumps({"family_data": family}, separators=(",", ":"))

    # Three generations, canonical CK3 shape: only the parent side of
    # each edge is stored. Grandchild row 70 carries an empty family_data
    # block — matches the Aasa/Kiur child snapshots observed in Saar.
    def _seed(s: Session) -> None:
        # Great-grandparents
        upsert_character(
            s,
            ck3_id=10,
            first_name="Alfred",
            female=False,
            save_snapshot_json=_snap({"child": [30]}),
        )
        upsert_character(
            s,
            ck3_id=11,
            first_name="Eadgyth",
            female=True,
            save_snapshot_json=_snap({"child": [30]}),
        )
        upsert_character(
            s,
            ck3_id=20,
            first_name="Beorn",
            female=False,
            save_snapshot_json=_snap({"child": [31]}),
        )
        upsert_character(
            s,
            ck3_id=21,
            first_name="Sigrith",
            female=True,
            save_snapshot_json=_snap({"child": [31]}),
        )
        # Grandparents
        upsert_character(
            s,
            ck3_id=30,
            first_name="Cuthbert",
            female=False,
            save_snapshot_json=_snap({"child": [50]}),
        )
        upsert_character(
            s,
            ck3_id=31,
            first_name="Mildgyth",
            female=True,
            save_snapshot_json=_snap({"child": [50]}),
        )
        # Parent (Edward)
        upsert_character(
            s,
            ck3_id=50,
            first_name="Edward",
            female=False,
            save_snapshot_json=_snap({"child": [70, 71]}),
        )
        # Children — empty family_data, the real-save shape that triggered
        # vtmx in the first place.
        upsert_character(s, ck3_id=70, first_name="Aasa", female=True, save_snapshot_json=_snap({}))
        upsert_character(
            s, ck3_id=71, first_name="Kiur", female=False, save_snapshot_json=_snap({})
        )

    c = api.make_campaign("vtmx", seed=_seed)
    resp = c.get("/api/campaigns/vtmx/characters/70/family-tree?ancestor_depth=3")
    assert resp.status_code == 200
    data = resp.json()
    by_id = {n["ck3_id"]: n for n in data["ancestors"]}
    # Parent (depth 1): Edward (50) — only one known parent, labelled father.
    assert 50 in by_id
    assert by_id[50]["relation"] == "father"
    assert by_id[50]["depth"] == 1
    # Grandparents (depth 2): Cuthbert + Mildgyth, gendered.
    assert by_id[30]["relation"] == "grandfather"
    assert by_id[30]["depth"] == 2
    assert by_id[31]["relation"] == "grandmother"
    # Great-grandparents (depth 3): all four ancestors of Edward.
    assert by_id[10]["relation"] == "great_grandfather"
    assert by_id[10]["depth"] == 3
    assert by_id[11]["relation"] == "great_grandmother"
    assert by_id[20]["relation"] == "great_grandfather"
    assert by_id[21]["relation"] == "great_grandmother"
    # Sibling Kiur (71) is found via Edward's child list (shared parent),
    # even though Aasa's own record has no parent IDs.
    siblings = {n["ck3_id"]: n for n in data["siblings"]}
    assert 71 in siblings
    assert siblings[71]["relation"] == "sibling"


def test_family_tree_validates_depth_bounds(tree_client: TestClient) -> None:
    """ancestor_depth and descendant_depth must be in [0, 10]."""
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree?ancestor_depth=-1")
    assert resp.status_code == 422
    resp = tree_client.get("/api/campaigns/tree/characters/300/family-tree?descendant_depth=11")
    assert resp.status_code == 422


def test_family_tree_endpoint_in_openapi(tree_client: TestClient) -> None:
    spec = tree_client.get("/openapi.json").json()
    assert "/api/campaigns/{name}/characters/{ck3_id}/family-tree" in spec["paths"]

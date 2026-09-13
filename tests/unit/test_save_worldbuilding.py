"""ck3_chronicler-8ek: worldbuilding region-summariser tests.

Synthetic CK3-shaped JSON mirroring the de jure structure: an empire
of Scandinavia containing kingdoms of Sweden + Denmark + Norway, each
with duchies under them. Tests cover the happy path (Erik, king of
Sweden, has Scandinavia + peers + a cross-current), the no-empire
fall-through (orphan kingdom), the no-realm case (a sub-king vassal),
and the cross-current detector.
"""

from __future__ import annotations

from chronicler.save.parse import parse_save
from chronicler.save.worldbuilding import detect_great_cause, summarise_region


def _scandinavia_save(*, with_cross_current: bool = True) -> dict:
    """Synthetic save with Erik (100) king of Sweden, Sven (200) of
    Denmark, Magnus (300) of Norway. Sjælland duchy is de jure of
    Denmark; when ``with_cross_current=True``, Magnus (Norway's king)
    holds it instead of Sven."""
    sjaelland_holder = 300 if with_cross_current else 200
    return {
        "meta_data": {
            "version": "1.19.0.4",
            "meta_date": "1067.2.1",
            "meta_main_portrait": {"id": 1234},
        },
        "playthrough_id": "test-scandinavia",
        "bookmark_date": "1066.9.15",
        "living": {
            "100": {
                "first_name": "Erik",
                "culture": 10,
                "faith": 20,
                "alive_data": {"birth": "1040.1.1"},
            },
            "200": {
                "first_name": "Sven",
                "culture": 10,
                "faith": 20,
                "alive_data": {"birth": "1020.1.1"},
            },
            "300": {
                "first_name": "Magnus",
                "culture": 10,
                "faith": 20,
                "alive_data": {"birth": "1024.1.1"},
            },
        },
        "dead_unprunable": {},
        "character_memory_manager": {"database": {}},
        "culture_manager": {"cultures": {"10": {"name": "norse"}}},
        "religion": {"faiths": {"20": {"tag": "germanic_pagan"}}},
        "landed_titles": {
            "landed_titles": {
                "1": {
                    "key": "e_scandinavia",
                    "title_name_data": {"name": "Scandinavia"},
                },
                # Three peer kingdoms in Scandinavia
                "2": {
                    "key": "k_sweden",
                    "de_jure_liege": 1,
                    "holder": 100,
                    "title_name_data": {"name": "Sweden"},
                },
                "3": {
                    "key": "k_denmark",
                    "de_jure_liege": 1,
                    "holder": 200,
                    "title_name_data": {"name": "Denmark"},
                },
                "4": {
                    "key": "k_norway",
                    "de_jure_liege": 1,
                    "holder": 300,
                    "title_name_data": {"name": "Norway"},
                },
                # Sweden's own duchy — held by Erik himself, no cross-current
                "5": {"key": "d_uppland", "de_jure_liege": 2, "holder": 100},
                # Sjælland — duchy de jure of Denmark, optionally
                # held by Norway's king for the cross-current case
                "6": {
                    "key": "d_sjaelland",
                    "de_jure_liege": 3,
                    "holder": sjaelland_holder,
                    "title_name_data": {"name": "Sjælland"},
                },
            }
        },
    }


def test_summarise_region_scandinavia_happy_path() -> None:
    snap = parse_save(_scandinavia_save())
    summary = summarise_region(snap, character_id=100)

    assert summary is not None
    assert summary["region_empire_key"] == "e_scandinavia"
    assert summary["region_empire_name"] == "Scandinavia"

    self_realm = summary["self_realm"]
    assert self_realm["kingdom_key"] == "k_sweden"
    assert self_realm["kingdom_name"] == "Sweden"
    assert self_realm["culture"] == "norse"
    assert self_realm["faith"] == "germanic_pagan"
    assert self_realm["ruler_first_name"] == "Erik"

    peer_keys = sorted(p["kingdom_key"] for p in summary["peers"])
    assert peer_keys == ["k_denmark", "k_norway"]
    denmark = next(p for p in summary["peers"] if p["kingdom_key"] == "k_denmark")
    assert denmark["ruler_first_name"] == "Sven"


def test_summarise_region_detects_cross_current_sjaelland() -> None:
    """ck3_chronicler-8ek: the Norway-owns-Sjælland flavour. Sjælland
    is de jure of Denmark but held by the king of Norway."""
    snap = parse_save(_scandinavia_save(with_cross_current=True))
    summary = summarise_region(snap, character_id=100)
    assert summary is not None
    cc = summary["cross_currents"]
    assert len(cc) == 1
    cross = cc[0]
    assert cross["held_title_key"] == "d_sjaelland"
    assert cross["held_title_name"] == "Sjælland"
    assert cross["de_jure_kingdom_key"] == "k_denmark"
    assert cross["holder_realm_key"] == "k_norway"
    assert cross["holder_first_name"] == "Magnus"


def test_summarise_region_no_cross_currents_when_holdings_aligned() -> None:
    """When every duchy is held by its de jure kingdom's ruler, no
    cross-currents."""
    snap = parse_save(_scandinavia_save(with_cross_current=False))
    summary = summarise_region(snap, character_id=100)
    assert summary is not None
    assert summary["cross_currents"] == []


def test_summarise_region_returns_none_for_orphan_kingdom() -> None:
    """ck3_chronicler-8ek failure mode: a king whose kingdom isn't
    de jure of any empire (mod content, custom title, early eras)
    falls through to None — biography pipeline emits today's prompt."""
    save = {
        "meta_data": {
            "version": "1.19.0.4",
            "meta_date": "1067.2.1",
            "meta_main_portrait": {"id": 1},
        },
        "playthrough_id": "x",
        "living": {
            "100": {"first_name": "Orphan", "alive_data": {"birth": "1040.1.1"}},
        },
        "dead_unprunable": {},
        "character_memory_manager": {"database": {}},
        "landed_titles": {
            "landed_titles": {
                "1": {"key": "k_orphan", "holder": 100},  # no de_jure_liege
            }
        },
    }
    snap = parse_save(save)
    assert summarise_region(snap, character_id=100) is None


def test_summarise_region_returns_none_for_landless_character() -> None:
    """A character holding no kingdom-tier (or higher) title — likely
    a sub-realm vassal — has no primary realm we can derive from
    title-holdings alone. Return None; pipeline falls through."""
    snap = parse_save(_scandinavia_save())
    # 999 holds nothing
    assert summarise_region(snap, character_id=999) is None


def test_summarise_region_emperor_resolves_self_kingdom_from_subholdings() -> None:
    """When the character holds the empire directly plus a kingdom
    title within it, the self_realm is the kingdom (more specific
    than the empire — "Erik, King of Sweden, Emperor of Scandinavia"
    feels right; "Erik of Scandinavia" loses the texture)."""
    save = _scandinavia_save()
    # Make Erik hold Scandinavia too
    save["landed_titles"]["landed_titles"]["1"]["holder"] = 100
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=100)
    assert summary is not None
    assert summary["self_realm"]["kingdom_key"] == "k_sweden"
    # Peers exclude Sweden (which is self) — Denmark + Norway only
    peer_keys = sorted(p["kingdom_key"] for p in summary["peers"])
    assert peer_keys == ["k_denmark", "k_norway"]


def test_summarise_region_excludes_self_from_peers() -> None:
    """The self-realm doesn't double-appear in peers."""
    snap = parse_save(_scandinavia_save())
    summary = summarise_region(snap, character_id=100)
    assert summary is not None
    self_key = summary["self_realm"]["kingdom_key"]
    assert self_key not in {p["kingdom_key"] for p in summary["peers"]}


# --- ck3_chronicler-95n7: sub-kingdom holders resolve via de_jure walk ---


def _scandinavia_save_with_subking_duke() -> dict:
    """Same Scandinavia layout as the happy-path save plus a Danish
    duke (400, Thrugot) who holds *only* a duchy (d_jutland, de jure of
    Denmark). 99% of 1066 bookmarks start the player at this tier;
    pre-95n7 their region_summary stayed None."""
    save = _scandinavia_save()
    save["living"]["400"] = {
        "first_name": "Thrugot",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1030.1.1"},
    }
    save["landed_titles"]["landed_titles"]["7"] = {
        "key": "d_jutland",
        "de_jure_liege": 3,  # Denmark
        "holder": 400,
        "title_name_data": {"name": "Jutland"},
    }
    return save


def test_summarise_region_resolves_subking_duke_via_de_jure_walk() -> None:
    """ck3_chronicler-95n7: Thrugot holds only d_jutland (a duchy de
    jure of Denmark). Pre-fix _primary_realm_of_character returned
    None and the entire region_summary collapsed. Post-fix his realm
    is k_denmark and the empire is Scandinavia, with Sven of Denmark
    as the realm's ruler (not Thrugot)."""
    snap = parse_save(_scandinavia_save_with_subking_duke())
    summary = summarise_region(snap, character_id=400)
    assert summary is not None
    assert summary["region_empire_key"] == "e_scandinavia"
    self_realm = summary["self_realm"]
    assert self_realm["kingdom_key"] == "k_denmark"
    assert self_realm["kingdom_name"] == "Denmark"
    # The kingdom's own holder (Sven) is reported as ruler, not
    # Thrugot — _realm_facts always reads kingdom.holder_id.
    assert self_realm["ruler_first_name"] == "Sven"
    # Peers are the other Scandinavian kingdoms.
    peer_keys = sorted(p["kingdom_key"] for p in summary["peers"])
    assert peer_keys == ["k_norway", "k_sweden"]


def test_summarise_region_subking_duke_with_kingdom_holding_prefers_kingdom() -> None:
    """When a character holds both a kingdom AND a sub-kingdom title,
    the kingdom wins (the existing semantics). The de_jure walk is
    only consulted when no kingdom+ holding exists."""
    save = _scandinavia_save_with_subking_duke()
    # Promote Thrugot to also hold k_denmark — now he's both duke and king.
    # Old test path should still pick k_denmark via the direct-hold check.
    save["landed_titles"]["landed_titles"]["3"]["holder"] = 400
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=400)
    assert summary is not None
    assert summary["self_realm"]["kingdom_key"] == "k_denmark"
    # Held directly, so ruler_first_name is Thrugot now.
    assert summary["self_realm"]["ruler_first_name"] == "Thrugot"


def test_summarise_region_resolves_count_via_county_to_kingdom_walk() -> None:
    """ck3_chronicler-95n7: a count holding only county-tier walks up
    county → duchy → kingdom via _de_jure_kingdom_of's iterative walk."""
    save = _scandinavia_save()
    save["living"]["500"] = {
        "first_name": "Brand",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1035.1.1"},
    }
    # County of Roskilde, de jure under Sjælland duchy (id 6, de jure
    # under Denmark kingdom id 3). The walk has to traverse two hops.
    save["landed_titles"]["landed_titles"]["8"] = {
        "key": "c_roskilde",
        "tier": "county",
        "de_jure_liege": 6,
        "holder": 500,
    }
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=500)
    assert summary is not None
    assert summary["self_realm"]["kingdom_key"] == "k_denmark"


def test_summarise_region_returns_none_when_dejure_chain_breaks() -> None:
    """ck3_chronicler-95n7: a sub-kingdom holder whose de_jure_liege
    chain doesn't reach kingdom tier (orphan duchy in mod content)
    falls through to None. Biography pipeline emits today's prompt."""
    save = _scandinavia_save()
    save["living"]["600"] = {
        "first_name": "Hakon",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1030.1.1"},
    }
    # Duchy with no de_jure_liege — broken chain. _de_jure_kingdom_of
    # returns None, _primary_realm_of_character returns None,
    # summarise_region returns None.
    save["landed_titles"]["landed_titles"]["9"] = {
        "key": "d_orphan",
        "holder": 600,
    }
    snap = parse_save(save)
    assert summarise_region(snap, character_id=600) is None


# --- ck3_chronicler-ggqn: family-fallback for unlanded tracked characters ---


def test_summarise_region_spouse_inherits_realm_from_primary_spouse() -> None:
    """ck3_chronicler-ggqn live signal: Bodil sits in Sven's court as
    his primary_spouse. She holds no titles of her own. Pre-fix, her
    rsj came back NULL and her death biography had no region context
    to weave. Post-fix, she inherits Sven's realm (k_denmark) and her
    region summary mirrors his."""
    save = _scandinavia_save()
    # Sven gets a primary_spouse (Bodil, 250); Bodil holds no titles.
    save["living"]["200"]["family_data"] = {"primary_spouse": 250}
    save["living"]["250"] = {
        "first_name": "Bodil",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1025.1.1"},
        "family_data": {"primary_spouse": 200},
    }
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=250)
    assert summary is not None
    assert summary["region_empire_key"] == "e_scandinavia"
    assert summary["self_realm"]["kingdom_key"] == "k_denmark"
    # The realm's ruler is still Sven (the kingdom-holder), not Bodil.
    assert summary["self_realm"]["ruler_first_name"] == "Sven"


def test_summarise_region_unlanded_child_inherits_realm_from_father() -> None:
    """An unlanded heir-apparent (child of a king who hasn't yet
    inherited / been granted a title) should still surface their
    parent's realm. Without this, child-death biographies on the
    player's heir get no context."""
    save = _scandinavia_save()
    save["living"]["260"] = {
        "first_name": "Knut",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1058.1.1"},
        "family_data": {"father": 200},
    }
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=260)
    assert summary is not None
    assert summary["self_realm"]["kingdom_key"] == "k_denmark"


def test_summarise_region_truly_unrooted_courtier_still_returns_none() -> None:
    """An unlanded character with no spouse and no parents in the
    snapshot has nothing to fall back on. Return None — biography
    pipeline emits today's no-context prompt. Guards against the
    fallback over-reaching for tracked advisors with empty family
    data."""
    save = _scandinavia_save()
    save["living"]["270"] = {
        "first_name": "Nameless",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1030.1.1"},
        # No family_data at all.
    }
    snap = parse_save(save)
    assert summarise_region(snap, character_id=270) is None


def test_summarise_region_family_cycle_does_not_loop_forever() -> None:
    """Cycle-safety: two unlanded characters listed as each other's
    primary_spouse (rare but legal in CK3 — courtier marriage) must
    not infinite-recurse. The walk visits each once via the _seen
    set and falls through to None when no member of the cycle holds
    a title."""
    save = _scandinavia_save()
    save["living"]["280"] = {
        "first_name": "Aase",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1030.1.1"},
        "family_data": {"primary_spouse": 281},
    }
    save["living"]["281"] = {
        "first_name": "Bersi",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1028.1.1"},
        "family_data": {"primary_spouse": 280},
    }
    snap = parse_save(save)
    # Neither holds a title; the cycle has no exit. Falls through to None
    # without StackOverflow / infinite loop.
    assert summarise_region(snap, character_id=280) is None


# --- ck3_chronicler-081b: self-realm culture/faith fallback to subject ---


def test_summarise_region_falls_back_to_subject_when_self_kingdom_holder_pruned() -> None:
    """ck3_chronicler-081b: smoke-found cardinal bug. The self_kingdom's
    holder character row was pruned from snap.characters (relevance
    filtering on an upstream king), so _realm_facts came back with
    culture=None, faith=None for self_realm — rendered as "(unknown
    culture), (unknown faith)" in the briefing. The model reconciled
    that against the briefing header by inventing a culture/faith
    change at inheritance.

    Fix: fall back to the SUBJECT's own culture/faith for the self
    realm — they're always in the snapshot since the briefing is
    built FOR them. Peer realms still report the kingdom-holder's
    data (the original semantics)."""
    save = _scandinavia_save_with_subking_duke()
    # Pop the kingdom-holder character row so the lookup misses, the
    # same shape as the live smoke (Sven the upstream king pruned).
    del save["living"]["200"]
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=400)  # Thrugot, duke
    assert summary is not None
    self_realm = summary["self_realm"]
    assert self_realm["kingdom_key"] == "k_denmark"
    # Holder lookup misses — ruler fields stay None — but culture/faith
    # are filled from Thrugot's own row, breaking the contradiction
    # that motivated the fabricated culture-change in the smoke.
    assert self_realm["ruler_first_name"] is None
    assert self_realm["culture"] == "norse"
    assert self_realm["faith"] == "germanic_pagan"


def test_summarise_region_does_not_overwrite_existing_self_culture() -> None:
    """Belt-and-braces: the fallback only kicks in when the holder
    lookup returned None. When the kingdom holder IS in the snapshot,
    their culture/faith continue to win — even if the subject's
    differs (a foreign-cultured vassal in a native-cultured realm).
    Without this guard, an Asturian count in a Castilian realm would
    get the realm's culture rewritten to Asturian on his briefing."""
    save = _scandinavia_save_with_subking_duke()
    # Give Thrugot a different culture than Sven's — the realm should
    # still report Sven's culture (norse), not Thrugot's (saxon).
    save["culture_manager"]["cultures"]["11"] = {"name": "saxon"}
    save["living"]["400"]["culture"] = 11
    snap = parse_save(save)
    summary = summarise_region(snap, character_id=400)
    assert summary is not None
    # Sven's culture wins; Thrugot's is not promoted onto the realm.
    assert summary["self_realm"]["culture"] == "norse"


# --- ck3_chronicler-7md7: great-cause detection ---


def _scandinavia_save_with_crusade(
    *,
    cb_type: str = "undirected_great_holy_war",
    target_title_id: int = 8755,
    target_title_key: str = "k_jerusalem",
    target_title_name: str = "Kingdom of Jerusalem",
    war_id: int = 67108864,
    character_side: str = "attacker",
) -> dict:
    """Scandinavia save plus a Jerusalem-target crusade war joined by
    the player (Erik 100). Adds Bjorn (350) as another Catholic king on
    the same side to exercise the allies surface, and sets up the
    target kingdom (8755 = k_jerusalem) plus its de jure empire to
    keep the resolver happy."""
    save = _scandinavia_save()
    # Add a second Scandinavian/Catholic ally on the attacker side
    # (Bjorn, holding k_iceland) so allies surface in the test.
    save["living"]["350"] = {
        "first_name": "Bjorn",
        "culture": 10,
        "faith": 20,
        "alive_data": {"birth": "1030.1.1"},
    }
    save["landed_titles"]["landed_titles"]["10"] = {
        "key": "k_iceland",
        "de_jure_liege": 1,  # Scandinavia
        "holder": 350,
        "title_name_data": {"name": "Iceland"},
    }
    # Add the target kingdom (Jerusalem) with its own empire so the
    # resolver can name it. Empire id 11, kingdom id `target_title_id`.
    save["landed_titles"]["landed_titles"]["11"] = {
        "key": "e_outremer",
        "title_name_data": {"name": "Outremer"},
    }
    save["landed_titles"]["landed_titles"][str(target_title_id)] = {
        "key": target_title_key,
        "de_jure_liege": 11,
        "holder": 999,  # Saracen defender, no character record needed
        "title_name_data": {"name": target_title_name},
    }
    attacker_ids = [100, 350] if character_side == "attacker" else [999]
    defender_ids = [999] if character_side == "attacker" else [100, 350]
    save["wars"] = {
        "active_wars": {
            str(war_id): {
                "name": f"Crusade for {target_title_name}",
                "start_date": "1096.8.1",
                "attacker": {
                    "participants": [{"character": cid, "casualties": 0} for cid in attacker_ids]
                },
                "defender": {
                    "participants": [{"character": cid, "casualties": 0} for cid in defender_ids]
                },
                "casus_belli": {
                    "type": cb_type,
                    "targeted_titles": [target_title_id],
                    "attacker": attacker_ids[0],
                    "defender": defender_ids[0],
                },
            }
        }
    }
    return save


def test_detect_great_cause_first_crusade_happy_path() -> None:
    """Acceptance scenario from the 7md7 ticket — Svend (here played
    by Erik 100) joins the First Crusade. The undirected_great_holy_war
    CB type is in the great-cause allowlist, so a GreatCauseFacts
    payload comes back fully populated."""
    snap = parse_save(_scandinavia_save_with_crusade())
    facts = detect_great_cause(snap, character_id=100)
    assert facts is not None
    assert facts["casus_belli_type"] == "undirected_great_holy_war"
    assert facts["war_name"] == "Crusade for Kingdom of Jerusalem"
    assert facts["start_date"] == "1096.8.1"
    assert facts["side"] == "attacker"
    assert facts["target_kingdom_key"] == "k_jerusalem"
    assert facts["target_kingdom_name"] == "Kingdom of Jerusalem"
    # Bjorn (350) is on the same side and holds k_iceland — should
    # surface as an ally. Erik himself is excluded.
    ally_ids = [a["character_id"] for a in facts["allies"]]
    assert 100 not in ally_ids
    assert 350 in ally_ids
    bjorn_ally = next(a for a in facts["allies"] if a["character_id"] == 350)
    assert bjorn_ally["first_name"] == "Bjorn"
    assert bjorn_ally["realm_key"] == "k_iceland"


def test_detect_great_cause_returns_none_for_non_great_cb() -> None:
    """A regular claimant war is not a great cause — the allowlist
    gates on the engine string. Returns None; nothing surfaces in
    the briefing."""
    snap = parse_save(_scandinavia_save_with_crusade(cb_type="claimant_faction_war"))
    assert detect_great_cause(snap, character_id=100) is None


def test_detect_great_cause_returns_none_for_character_in_no_war() -> None:
    """The character is alive and a king but isn't a participant in
    any war. Returns None — the snapshot's character_to_wars map has
    no entry for them."""
    snap = parse_save(_scandinavia_save())  # no wars at all
    assert detect_great_cause(snap, character_id=100) is None


def test_detect_great_cause_clears_when_war_disappears() -> None:
    """Snapshot-derived lifecycle: when CK3 removes the war from
    active_wars on a future tick (war_concluded), the next save-tail
    pass yields no candidates and detect_great_cause returns None.
    The persisted column auto-clears, no diff bookkeeping needed."""
    save_with = _scandinavia_save_with_crusade()
    save_without = _scandinavia_save()
    # Same character setup; the only difference is the war record.
    snap_with = parse_save(save_with)
    snap_without = parse_save(save_without)
    assert detect_great_cause(snap_with, character_id=100) is not None
    assert detect_great_cause(snap_without, character_id=100) is None


def test_detect_great_cause_resolves_defender_side() -> None:
    """The character can be on either side of a great cause. The Saracen
    side of a crusade is also a great cause from their perspective; the
    detector reports the right side string."""
    snap = parse_save(_scandinavia_save_with_crusade(character_side="defender"))
    facts = detect_great_cause(snap, character_id=100)
    assert facts is not None
    assert facts["side"] == "defender"


def test_detect_great_cause_picks_smallest_war_id_when_multi_cause() -> None:
    """Edge case: two simultaneous great-cause wars on the same
    character. Returns the war with the smallest war_id for stable
    cross-run output. Other crusades silently lose; file a follow-up
    if a real save shows this matters."""
    save = _scandinavia_save_with_crusade(war_id=67108864)
    # Add a second great-cause war with a higher id.
    save["wars"]["active_wars"]["67108865"] = {
        "name": "Second Crusade for Egypt",
        "start_date": "1098.1.1",
        "attacker": {"participants": [{"character": 100, "casualties": 0}]},
        "defender": {"participants": [{"character": 999, "casualties": 0}]},
        "casus_belli": {
            "type": "papal_crusade",
            "targeted_titles": [],
            "attacker": 100,
            "defender": 999,
        },
    }
    snap = parse_save(save)
    facts = detect_great_cause(snap, character_id=100)
    assert facts is not None
    # Smallest war_id wins, so the Jerusalem crusade survives.
    assert facts["war_id"] == 67108864
    assert facts["target_kingdom_key"] == "k_jerusalem"

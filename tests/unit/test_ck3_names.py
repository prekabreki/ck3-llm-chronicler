"""ck3_chronicler-r3fs: CK3 character-name localization lookup.

CK3 encodes names as lossy letter+underscore escapes (``A_slaug``,
``T_O_runn``) — the same ``O_`` is Ö in ``O_rvar`` (Örvar) but ó in
``O_lafr`` (Ólafr). The game's own ``localization/<lang>/names/*.yml``
files map each escape token to the canonical Unicode string, which is the
only authoritative disambiguation. rakaly hands us that same token as
``first_name``, so it keys directly into the map.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chronicler.save import ck3_names

# The conftest autouse fixture pins find_enabled_mod_dirs to "no mods" so the
# suite never reads the dev box's real dlc_load.json. Capture the genuine
# function here, at import time, for the tests that mean to exercise it.
_REAL_FIND_MOD_DIRS = ck3_names.find_enabled_mod_dirs

# A tiny stand-in for character_names_l_english.yml: header line, a comment,
# and the exact tricky cases that prove ó-vs-ö disambiguation + Iberian ñ.
_FIXTURE = """﻿l_english:
 # Old Norse / Icelandic
 A_slaug:0 "Áslaug"
 A__sta:0 "Ásta"
 O_lafr:0 "Ólafr"
 O_rvar:0 "Örvar"
 T_O_runn:0 "Þórunn"
 BjO_rn:0 "Björn"
 # Iberian
 OrdoN_o:0 "Ordoño"
 InE_s:0 "Inês"
"""


@pytest.fixture
def fixture_yml(tmp_path: Path) -> Path:
    p = tmp_path / "character_names_l_english.yml"
    p.write_text(_FIXTURE, encoding="utf-8")
    return p


def test_build_name_map_parses_canonical_names(fixture_yml: Path) -> None:
    m = ck3_names.build_name_map([fixture_yml])
    # The crux: identical O_ escape, different canonical glyph.
    assert m["O_lafr"] == "Ólafr"
    assert m["O_rvar"] == "Örvar"
    assert m["T_O_runn"] == "Þórunn"
    assert m["BjO_rn"] == "Björn"
    assert m["A_slaug"] == "Áslaug"
    assert m["A__sta"] == "Ásta"
    assert m["OrdoN_o"] == "Ordoño"
    assert m["InE_s"] == "Inês"


def test_build_name_map_skips_header_and_comments(fixture_yml: Path) -> None:
    m = ck3_names.build_name_map([fixture_yml])
    assert "l_english" not in m
    # No comment text leaked in as a key/value.
    assert all(not k.startswith("#") for k in m)


def test_build_name_map_tolerates_missing_files(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yml"
    # Unreadable paths are skipped, not fatal.
    assert ck3_names.build_name_map([missing]) == {}


def test_resolve_name_hit_and_miss(fixture_yml: Path, monkeypatch) -> None:
    monkeypatch.setattr(ck3_names, "_NAME_MAP", ck3_names.build_name_map([fixture_yml]))
    assert ck3_names.resolve_name("A_slaug") == "Áslaug"
    assert ck3_names.resolve_name("NotAName_q") is None
    assert ck3_names.resolve_name(None) is None
    assert ck3_names.resolve_name("") is None


# --- ck3_chronicler-3kyg: consolidated resolution interface ---


def test_resolve_character_name_prefers_loca_then_falls_back(monkeypatch) -> None:
    """resolve_character_name uses the loca hit when present and the
    heuristic decoder on a miss — the resolve-or-decode sequence that
    used to be inlined in parse.py, now testable in isolation."""
    monkeypatch.setattr(
        ck3_names, "resolve_name", lambda token: "Áslaug" if token == "A_slaug" else None
    )
    # Loca hit wins.
    assert ck3_names.resolve_character_name("A_slaug") == "Áslaug"
    # Miss → heuristic decoder ('E_lla' → 'Ælla' via the escape map).
    assert ck3_names.resolve_character_name("E_lla") == "Ælla"
    # None passes through to None (the parser strips empty tokens to
    # None upstream; "" mirrors the decoder's own pass-through).
    assert ck3_names.resolve_character_name(None) is None
    assert ck3_names.resolve_character_name("") == ""


def test_resolve_dynasty_name_walks_chain() -> None:
    """House → dynasty walk, decoded, with house_name returned too."""
    dynasty_name, house_name = ck3_names.resolve_dynasty_name(
        dynasty_house_id=10,
        houses_lookup={10: "house_munso"},
        house_to_dynasty={10: 99},
        dynasties_lookup={99: "dynn_Sleggja"},
    )
    assert dynasty_name == "Sleggja"
    assert house_name == "munso"  # decode_house_name strips the house_ prefix


def test_resolve_dynasty_name_falls_back_to_house_when_chain_breaks() -> None:
    """ck3_chronicler-te8s: when the dynasty chain can't be walked
    (integer-key dynasty skipped by dynasties_lookup), the house name
    stands in as the dynasty name."""
    dynasty_name, house_name = ck3_names.resolve_dynasty_name(
        dynasty_house_id=10,
        houses_lookup={10: "house_munso"},
        house_to_dynasty={},  # no dynasty edge
        dynasties_lookup={},
    )
    assert dynasty_name == "munso"  # house stands in
    assert house_name == "munso"


def test_resolve_dynasty_name_none_house_id() -> None:
    """Landless / early-playthrough characters yield (None, None)."""
    assert ck3_names.resolve_dynasty_name(
        dynasty_house_id=None,
        houses_lookup={},
        house_to_dynasty={},
        dynasties_lookup={},
    ) == (None, None)


def test_resolve_name_graceful_when_no_game_dir(monkeypatch) -> None:
    """No game install reachable → empty map, resolve_name returns None,
    never raises. Callers then fall back to the heuristic decoder."""
    monkeypatch.delenv("CHRONICLER_CK3_DIR", raising=False)
    monkeypatch.setattr(ck3_names, "find_game_dir", lambda: None)
    monkeypatch.setattr(ck3_names, "_NAME_MAP", None)
    assert ck3_names.get_name_map() == {}
    assert ck3_names.resolve_name("A_slaug") is None


def test_get_name_map_loads_from_env_dir(tmp_path: Path, monkeypatch) -> None:
    """When CHRONICLER_CK3_DIR points at an install, the names loca under
    game/localization/english/names is parsed."""
    names_dir = tmp_path / "game" / "localization" / "english" / "names"
    names_dir.mkdir(parents=True)
    (names_dir / "character_names_l_english.yml").write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.setenv("CHRONICLER_CK3_DIR", str(tmp_path))
    monkeypatch.setattr(ck3_names, "_NAME_MAP", None)
    m = ck3_names.get_name_map()
    assert m.get("O_rvar") == "Örvar"
    assert m.get("O_lafr") == "Ólafr"


# --- ck3_chronicler-6rgx: dynasty / house-name localization ---

# Mirrors dynasty_names_l_english.yml. The dynn_* values the save stores in
# dynasty_house[].name key straight into this map (verified against a real
# CK3 install: `dynn_A_rpA_d:0 "Árpád"`). The last line is the
# no-version-digit `key: "value" # comment` form CK3's
# chinese_dynasty_names_l_english.yml uses.
_DYNASTY_FIXTURE = """﻿l_english:
 dynn_A_rpA_d:0 "Árpád"
 dynn_A_lvarez:0 "Álvarez"
 dynn_Orsini:0 "Orsini"
 dynn_AikO_: "Aikō" # 愛甲
"""


@pytest.fixture
def dynasty_yml(tmp_path: Path) -> Path:
    p = tmp_path / "dynasty_names_l_english.yml"
    p.write_text(_DYNASTY_FIXTURE, encoding="utf-8")
    return p


def test_build_name_map_parses_dynasty_loca(dynasty_yml: Path) -> None:
    """build_name_map handles the dynasty file too, including the
    no-version-digit ``key: "value" # comment`` line CK3 uses for Chinese
    dynasties."""
    m = ck3_names.build_name_map([dynasty_yml])
    assert m["dynn_A_rpA_d"] == "Árpád"
    assert m["dynn_A_lvarez"] == "Álvarez"
    assert m["dynn_Orsini"] == "Orsini"
    assert m["dynn_AikO_"] == "Aikō"


def test_resolve_house_name_prefers_loca(dynasty_yml: Path, monkeypatch) -> None:
    """The authoritative loca glyph beats the lossy heuristic strip:
    dynn_A_rpA_d -> 'Árpád', which decode_house_name can't recover from the
    escape (same ó-vs-ö ambiguity r3fs solved for first names)."""
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", ck3_names.build_name_map([dynasty_yml]))
    assert ck3_names.resolve_house_name("dynn_A_rpA_d") == "Árpád"
    assert ck3_names.resolve_house_name("dynn_A_lvarez") == "Álvarez"


def test_resolve_house_name_falls_back_to_heuristic_on_miss(monkeypatch) -> None:
    """Loca miss (custom/player/mod dynasty, or no install) → the
    decode_house_name heuristic, so nothing regresses."""
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", {})
    assert ck3_names.resolve_house_name("dynn_Sleggja") == "Sleggja"
    assert ck3_names.resolve_house_name("house_munso") == "munso"


def test_resolve_house_name_passthrough_none_empty(monkeypatch) -> None:
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", {})
    assert ck3_names.resolve_house_name(None) is None
    assert ck3_names.resolve_house_name("") == ""


def test_resolve_dynasty_name_uses_loca_for_diacritics(dynasty_yml: Path, monkeypatch) -> None:
    """resolve_dynasty_name routes both the house and dynasty names through
    the loca, so diacritic-bearing dynasties render correctly instead of
    the heuristic's lossy strip."""
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", ck3_names.build_name_map([dynasty_yml]))
    dynasty_name, house_name = ck3_names.resolve_dynasty_name(
        dynasty_house_id=1,
        houses_lookup={1: "dynn_A_rpA_d"},
        house_to_dynasty={1: 2},
        dynasties_lookup={2: "dynn_A_lvarez"},
    )
    assert house_name == "Árpád"
    assert dynasty_name == "Álvarez"


def test_get_dynasty_map_loads_from_env_dir(tmp_path: Path, monkeypatch) -> None:
    """CHRONICLER_CK3_DIR install → the dynasties loca under
    game/localization/english/dynasties/*.yml is parsed."""
    dyn_dir = tmp_path / "game" / "localization" / "english" / "dynasties"
    dyn_dir.mkdir(parents=True)
    (dyn_dir / "dynasty_names_l_english.yml").write_text(_DYNASTY_FIXTURE, encoding="utf-8")
    monkeypatch.setenv("CHRONICLER_CK3_DIR", str(tmp_path))
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", None)
    m = ck3_names.get_dynasty_map()
    assert m.get("dynn_A_rpA_d") == "Árpád"


def test_find_game_dir_falls_back_to_per_os_resolver(tmp_path: Path, monkeypatch) -> None:
    """With no settings override and no env var, autodetection must defer to
    the per-OS resolver rather than the Windows-only candidate list.

    The hardcoded ``_DEFAULT_INSTALL_CANDIDATES`` are all ``C:\\``/``E:\\``
    paths, so on Linux/macOS they never match and canonical name lookup
    silently degraded to the heuristic decoder even when the Steam probe
    could see the install.
    """
    import chronicler.config as config
    import chronicler.settings_store as settings_store

    monkeypatch.setattr(settings_store, "load_settings", lambda *a, **k: {})
    monkeypatch.delenv("CHRONICLER_CK3_DIR", raising=False)
    monkeypatch.setattr(
        config,
        "resolve_ck3_install_dir",
        lambda: config.ResolvedPath(value=tmp_path, source="probe", exists=True),
    )

    assert ck3_names.find_game_dir() == tmp_path


def test_find_game_dir_none_when_resolver_also_misses(monkeypatch) -> None:
    """Resolver miss (value=None) must not be returned as a game dir."""
    import chronicler.config as config
    import chronicler.settings_store as settings_store

    monkeypatch.setattr(settings_store, "load_settings", lambda *a, **k: {})
    monkeypatch.delenv("CHRONICLER_CK3_DIR", raising=False)
    monkeypatch.setattr(
        config,
        "resolve_ck3_install_dir",
        lambda: config.ResolvedPath(value=None, source="probe", exists=False),
    )
    monkeypatch.setattr(ck3_names, "_DEFAULT_INSTALL_CANDIDATES", ())

    assert ck3_names.find_game_dir() is None


# --- issue #14: loca from CK3's enabled mods ---

# A mod's own names file. Overrides one base key (O_lafr) and adds one the
# base set does not have — the two halves of the precedence contract.
_MOD_FIXTURE = """﻿l_english:
 O_lafr:0 "Óláfr-the-mod-spelling"
 Bai_767D:0 "Bái"
"""


def _fake_ck3_user_dir(tmp_path: Path, monkeypatch, enabled: list[str]) -> Path:
    """Build a CK3 user dir with a dlc_load.json listing ``enabled``."""
    import chronicler.config as config

    user_dir = tmp_path / "ck3user"
    (user_dir / "mod").mkdir(parents=True, exist_ok=True)
    (user_dir / "dlc_load.json").write_text(
        json.dumps({"enabled_mods": enabled, "disabled_dlcs": []}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "ck3_user_dir", lambda: user_dir)
    return user_dir


def _write_mod(
    user_dir: Path,
    tmp_path: Path,
    slug: str,
    *,
    names: str | None = None,
    dynasties: str | None = None,
) -> Path:
    """Write a mod content dir plus the .mod descriptor pointing at it."""
    root = tmp_path / "workshop" / slug
    if names is not None:
        d = root / "localization" / "english" / "names"
        d.mkdir(parents=True)
        (d / f"{slug}_names_l_english.yml").write_text(names, encoding="utf-8")
    if dynasties is not None:
        d = root / "localization" / "english" / "dynasties"
        d.mkdir(parents=True)
        (d / f"{slug}_dynasties_l_english.yml").write_text(dynasties, encoding="utf-8")
    (user_dir / "mod" / f"{slug}.mod").write_text(
        f'version="1"\nname="{slug}"\nsupported_version="1.19.0.6"\npath="{root}"\n',
        encoding="utf-8",
    )
    return root


def _fake_install(
    tmp_path: Path, monkeypatch, *, names: str | None = None, dynasties: str | None = None
) -> Path:
    """A game install whose english loca holds the base fixtures."""
    if names is not None:
        d = tmp_path / "game" / "localization" / "english" / "names"
        d.mkdir(parents=True)
        (d / "character_names_l_english.yml").write_text(names, encoding="utf-8")
    if dynasties is not None:
        d = tmp_path / "game" / "localization" / "english" / "dynasties"
        d.mkdir(parents=True)
        (d / "dynasty_names_l_english.yml").write_text(dynasties, encoding="utf-8")
    monkeypatch.setenv("CHRONICLER_CK3_DIR", str(tmp_path))
    return tmp_path


def test_find_enabled_mod_dirs_resolves_descriptors_in_load_order(
    tmp_path: Path, monkeypatch
) -> None:
    """dlc_load.json order is load order, so the returned dirs preserve it."""
    user_dir = _fake_ck3_user_dir(tmp_path, monkeypatch, ["mod/first.mod", "mod/second.mod"])
    first = _write_mod(user_dir, tmp_path, "first", names=_MOD_FIXTURE)
    second = _write_mod(user_dir, tmp_path, "second", names=_MOD_FIXTURE)

    assert _REAL_FIND_MOD_DIRS() == [first, second]


def test_find_enabled_mod_dirs_skips_unresolvable_entries(tmp_path: Path, monkeypatch) -> None:
    """An unsubscribed mod (path points nowhere), a zipped mod (archive= and
    no path=) and a descriptor that isn't there at all are all skipped
    rather than raising — name lookup must never break ingest."""
    user_dir = _fake_ck3_user_dir(
        tmp_path,
        monkeypatch,
        ["mod/real.mod", "mod/gone.mod", "mod/zipped.mod", "mod/absent.mod"],
    )
    real = _write_mod(user_dir, tmp_path, "real", names=_MOD_FIXTURE)
    (user_dir / "mod" / "gone.mod").write_text(
        f'name="gone"\npath="{tmp_path / "no-such-dir"}"\n', encoding="utf-8"
    )
    (user_dir / "mod" / "zipped.mod").write_text(
        'name="zipped"\narchive="/somewhere/mod.zip"\n', encoding="utf-8"
    )

    assert _REAL_FIND_MOD_DIRS() == [real]


@pytest.mark.parametrize("payload", ["", "{not json", '{"enabled_mods": "nope"}', "{}"])
def test_find_enabled_mod_dirs_tolerates_bad_dlc_load(
    tmp_path: Path, monkeypatch, payload: str
) -> None:
    """Missing/malformed dlc_load.json yields no mods, never an exception."""
    import chronicler.config as config

    user_dir = tmp_path / "ck3user"
    user_dir.mkdir()
    monkeypatch.setattr(config, "ck3_user_dir", lambda: user_dir)
    if payload:
        (user_dir / "dlc_load.json").write_text(payload, encoding="utf-8")

    assert _REAL_FIND_MOD_DIRS() == []


def test_mod_name_overrides_base_and_base_only_key_survives(tmp_path: Path, monkeypatch) -> None:
    """The precedence contract: a mod key wins over the base key of the same
    name, a key only the mod has resolves, and a key only the base has is
    untouched by the merge."""
    install = tmp_path / "install"
    install.mkdir()
    _fake_install(install, monkeypatch, names=_FIXTURE)
    user_dir = _fake_ck3_user_dir(tmp_path, monkeypatch, ["mod/namesmod.mod"])
    _write_mod(user_dir, tmp_path, "namesmod", names=_MOD_FIXTURE)
    monkeypatch.setattr(ck3_names, "find_enabled_mod_dirs", _REAL_FIND_MOD_DIRS)
    monkeypatch.setattr(ck3_names, "_NAME_MAP", None)

    m = ck3_names.get_name_map()
    assert m["O_lafr"] == "Óláfr-the-mod-spelling"  # mod overrides base
    assert m["Bai_767D"] == "Bái"  # mod-only key resolves
    assert m["O_rvar"] == "Örvar"  # base-only key untouched
    assert ck3_names.resolve_name("NotInAnySource") is None  # heuristic fallback


def test_later_enabled_mod_wins_over_earlier(tmp_path: Path, monkeypatch) -> None:
    """Two mods claiming the same key: the one later in load order wins."""
    install = tmp_path / "install"
    install.mkdir()
    _fake_install(install, monkeypatch, names=_FIXTURE)
    user_dir = _fake_ck3_user_dir(tmp_path, monkeypatch, ["mod/early.mod", "mod/late.mod"])
    _write_mod(user_dir, tmp_path, "early", names='﻿l_english:\n O_lafr:0 "from-early"\n')
    _write_mod(user_dir, tmp_path, "late", names='﻿l_english:\n O_lafr:0 "from-late"\n')
    monkeypatch.setattr(ck3_names, "find_enabled_mod_dirs", _REAL_FIND_MOD_DIRS)
    monkeypatch.setattr(ck3_names, "_NAME_MAP", None)

    assert ck3_names.get_name_map()["O_lafr"] == "from-late"


def test_mod_dynasty_loca_merges_too(tmp_path: Path, monkeypatch) -> None:
    """The dynasty map takes mod loca on the same terms — "More Character
    Names" ships dynasties/ as well as names/."""
    install = tmp_path / "install"
    install.mkdir()
    _fake_install(install, monkeypatch, dynasties='﻿l_english:\n dynn_Base:0 "Basehouse"\n')
    user_dir = _fake_ck3_user_dir(tmp_path, monkeypatch, ["mod/dynmod.mod"])
    _write_mod(
        user_dir,
        tmp_path,
        "dynmod",
        dynasties='﻿l_english:\n dynn_Base:0 "Modhouse"\n dynn_Bai_767D:0 "Bái"\n',
    )
    monkeypatch.setattr(ck3_names, "find_enabled_mod_dirs", _REAL_FIND_MOD_DIRS)
    monkeypatch.setattr(ck3_names, "_DYNASTY_MAP", None)

    m = ck3_names.get_dynasty_map()
    assert m["dynn_Base"] == "Modhouse"
    assert m["dynn_Bai_767D"] == "Bái"

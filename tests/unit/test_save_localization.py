"""Unit tests for chronicler.save.localization.decode_ck3_name.

CK3 stores Anglo-Saxon / Old Norse / Old English names with
diacritical marks as letter+underscore escapes ("E_lla" for Ælla,
"t_orfinn" for þorfinn). rakaly returns the raw save form; this
decoder is applied at parse time so SaveSnapshot and Character DB
rows store readable Unicode.
"""

from __future__ import annotations

from chronicler.save.localization import (
    decode_ck3_name,
    decode_house_name,
    strip_loca_markup,
)


def test_passthrough_ascii_name_unchanged() -> None:
    assert decode_ck3_name("Eadmund") == "Eadmund"


def test_passthrough_already_decoded_name_unchanged() -> None:
    """Idempotent — if a Unicode name somehow arrives already decoded,
    don't double-process it."""
    assert decode_ck3_name("Ælla") == "Ælla"


def test_none_passthrough() -> None:
    assert decode_ck3_name(None) is None


def test_empty_string_passthrough() -> None:
    assert decode_ck3_name("") == ""


def test_anglo_saxon_capital_ash_decoded() -> None:
    """The acceptance scenario from ck3_chronicler-fe5: Ælla 12267
    (Anglo-Saxon ruler) shows as 'E_lla' in rakaly output."""
    assert decode_ck3_name("E_lla") == "Ælla"


def test_anglo_saxon_lowercase_ash_decoded() -> None:
    """Old English compound names — Æthelred, Ælfric — use lowercase
    ash mid-name."""
    assert decode_ck3_name("E_thelred") == "Æthelred"


def test_old_norse_lowercase_thorn_decoded() -> None:
    """Norse/Icelandic names: Þorfinn, Þorsteinn use thorn (Þ/þ)."""
    assert decode_ck3_name("t_orfinn") == "þorfinn"


def test_old_norse_uppercase_thorn_decoded() -> None:
    assert decode_ck3_name("T_orsteinn") == "Þorsteinn"


def test_old_english_eth_decoded() -> None:
    """Eth (Ð/ð) appears in Old Norse and early Old English names."""
    assert decode_ck3_name("d_unstan") == "ðunstan"
    assert decode_ck3_name("D_unstan") == "Ðunstan"


def test_norse_o_with_stroke_decoded() -> None:
    """Culture-blind path: O_ → Ø (no culture supplied)."""
    assert decode_ck3_name("O_ystein") == "Øystein"
    assert decode_ck3_name("o_rnulf") == "ørnulf"


def test_norse_culture_o_decoded_as_umlaut() -> None:
    """Norse culture path: O_ → Ö (umlaut) to match CK3's in-game display.
    Observed live: 'O_rvar' shows as 'Örvar' in-game, not 'Ørvar'."""
    assert decode_ck3_name("O_rvar", culture="norse") == "Örvar"
    assert decode_ck3_name("o_rnulf", culture="norse") == "örnulf"


def test_scandinavian_a_with_ring_decoded() -> None:
    """Norse/Swedish 'Åke', 'Åse' — a-with-ring."""
    assert decode_ck3_name("A_ke") == "Åke"
    assert decode_ck3_name("a_se") == "åse"


def test_multiple_escapes_in_one_name() -> None:
    """Compound or rare names may carry more than one escape; decode all."""
    # Hypothetical "Ælfþryð" → "E_lft_ryd_"
    assert decode_ck3_name("E_lft_ryd_") == "Ælfþryð"


def test_unknown_letter_underscore_left_alone() -> None:
    """If a letter+underscore combination isn't a known escape AND
    the letter isn't in the empirically-observed CK3 escape-marker set,
    leave it untouched rather than guessing. Adding new mappings or
    extending the marker set is the only way to broaden coverage."""
    assert decode_ck3_name("X_zyzx") == "X_zyzx"


# ck3_chronicler-63y regressions: capital escapes mid-word are
# culture-overloaded and must NOT be substituted via the Norse map.


def test_breton_diaeresis_mid_word_drops_to_lossy_e() -> None:
    """Hoël / 'HoE_l' was the live trigger for 63y. Pre-fix it became
    'HoÆl' (HoE_l literal-mapped E_ → Æ, even mid-word). The right
    answer is Hoël but we don't have culture context here, so we drop
    to lossy 'Hoel' to avoid the worse mojibake outcome."""
    assert decode_ck3_name("HoE_l") == "Hoel"


def test_provencal_grave_mid_word_drops_to_lossy_e() -> None:
    """'GuilhE_m' is Provençal Guilhèm. Same E_ → Æ overload we'd hit
    pre-63y. Drops to readable 'Guilhem'."""
    assert decode_ck3_name("GuilhE_m") == "Guilhem"


def test_hungarian_acute_mid_word_drops_to_lossy_e() -> None:
    """'PE_ter' is Hungarian Péter. E_ overload again."""
    assert decode_ck3_name("PE_ter") == "Peter"


def test_spanish_acute_mid_word_drops_to_lossy_i() -> None:
    """'MarI_a' is Spanish María. The I_ marker doesn't exist in the
    Norse map, so pre-63y it would have passed through as 'MarI_a'.
    Now it lossily decodes to 'Maria'."""
    assert decode_ck3_name("MarI_a") == "Maria"


def test_czech_acute_mid_word_drops_to_lossy_i() -> None:
    """'JaromI_r' is Czech Jaromír."""
    assert decode_ck3_name("JaromI_r") == "Jaromir"


def test_polish_stroke_mid_word_drops_to_lossy_l() -> None:
    """'StanisL_aw' is Polish Stanisław. L_ marker."""
    assert decode_ck3_name("StanisL_aw") == "Stanislaw"


def test_slavic_carons_at_name_start_lossy_keep_case() -> None:
    """'C_ilbu' starts with a Slavic letter (Č). The Norse map doesn't
    have C_; pre-63y it passed through as 'C_ilbu'. Now it falls back to
    case-preserving 'Cilbu' so the name-start capital is intact."""
    assert decode_ck3_name("C_ilbu") == "Cilbu"


def test_anglo_saxon_at_name_start_still_works() -> None:
    """Position-0 escapes from the full _ESCAPE_MAP must continue to
    apply — that's the v0.5/fe5 contract for Ælla, Þorfinn, etc."""
    assert decode_ck3_name("A_lfrhildr") == "Ålfrhildr"  # Norse map wins at start
    assert decode_ck3_name("E_lla") == "Ælla"
    assert decode_ck3_name("T_orsteinn") == "Þorsteinn"


def test_norse_lowercase_escapes_apply_mid_word() -> None:
    """The hypothetical Ælfþryð compound 'E_lft_ryd_' must still decode
    correctly: position-0 E_ → Æ via full map; mid-word t_ and d_ →
    þ and ð via the lowercase-only map (not the fallback)."""
    assert decode_ck3_name("E_lft_ryd_") == "Ælfþryð"


# --- ck3_chronicler-57j: culture-aware decoding for non-Norse names -----


def test_breton_culture_decodes_diaeresis_correctly() -> None:
    """Hoël (Breton): with culture context, the mid-word E_ resolves to
    ë instead of falling through to the lossy strip."""
    assert decode_ck3_name("HoE_l", culture="breton") == "Hoël"


def test_castilian_culture_decodes_acute_correctly() -> None:
    """María (Castilian): mid-word I_ resolves to í instead of i."""
    assert decode_ck3_name("MarI_a", culture="castilian") == "María"


def test_polish_culture_decodes_stroke_l_correctly() -> None:
    """Stanisław (Polish): mid-word L_ resolves to ł."""
    assert decode_ck3_name("StanisL_aw", culture="polish") == "Stanisław"


def test_occitan_culture_decodes_grave_correctly() -> None:
    """Guilhèm (Occitan/Provençal): mid-word E_ resolves to è."""
    assert decode_ck3_name("GuilhE_m", culture="occitan") == "Guilhèm"


def test_magyar_culture_decodes_acute_e() -> None:
    """Péter (Magyar/Hungarian): mid-word E_ → é."""
    assert decode_ck3_name("PE_ter", culture="magyar") == "Péter"


def test_icelandic_culture_overrides_norse_o_to_acute() -> None:
    """Ólafr (Icelandic): position-0 O_ resolves to Ó (acute), NOT Ø
    (Norse stroke). The culture override is what makes this disambiguation
    possible — culture-blind decoding would map O_ → Ø via _ESCAPE_MAP."""
    assert decode_ck3_name("O_lafr", culture="icelandic") == "Ólafr"


def test_anglo_saxon_with_explicit_culture_still_decodes_position_zero() -> None:
    """Ælla with explicit culture must continue to decode — the existing
    contract holds, the culture map just makes mid-word capitals also
    work for the Anglo-Saxon escape set."""
    assert decode_ck3_name("E_lla", culture="anglo_saxon") == "Ælla"


def test_culture_aware_decoding_handles_culture_blind_lowercase_fallback() -> None:
    """Lowercase escapes the per-culture map doesn't override still go
    through the shared _ESCAPE_MAP / _LC_ESCAPE_MAP. Norse compound
    E_lft_ryd_ → Ælfþryð still works under culture='norse' because the
    Norse map carries both case variants."""
    assert decode_ck3_name("E_lft_ryd_", culture="norse") == "Ælfþryð"


def test_unknown_culture_falls_back_to_culture_blind_path() -> None:
    """An unrecognised culture string is silently ignored — caller can
    pass whatever ``cultures_lookup`` returns without filtering, and the
    decoder handles the gap by falling through to the existing
    culture-blind behaviour (lossy strip mid-word)."""
    assert decode_ck3_name("HoE_l", culture="some_modded_culture") == "Hoel"


def test_culture_none_keeps_v05_behaviour() -> None:
    """Default ``culture=None`` is the v0.5/63y behaviour — confirms the
    new parameter is purely additive and backward-compatible with every
    existing call site."""
    assert decode_ck3_name("HoE_l") == "Hoel"
    assert decode_ck3_name("HoE_l", culture=None) == "Hoel"


def test_breton_lowercase_e_escape_decoded() -> None:
    """If a Breton name has lowercase e_ mid-word (less common — most
    diacritics in CK3 names are encoded with capital escapes), the
    culture map's lowercase entry handles it."""
    assert decode_ck3_name("Joe_l", culture="breton") == "Joël"


def test_sami_culture_decodes_acute_a() -> None:
    """Máidna (Sámi): mid-word capital A_ resolves to lowercase á via
    the per-culture map (the same case-flip rule that produces Hoël
    from HoE_l for Breton). Without the sami entry the user saw
    ``Maidna`` because the lossy strip dropped the diacritic info.
    Live signal: live save 2026-05-09, ck3_id=52098."""
    assert decode_ck3_name("MA_idna", culture="sami") == "Máidna"


def test_sami_culture_decodes_position_zero_acute_a() -> None:
    """Áidná (Sámi): position-0 A_ → Á; trailing A_ → á (lowercase
    diacritic via the mid-word case-flip rule). Confirms the encoding
    works at both edges of the same name."""
    assert decode_ck3_name("A_idnA_", culture="sami") == "Áidná"


# --- ck3_chronicler-6d1c: decode_house_name ---


def test_decode_house_name_strips_dynn_prefix() -> None:
    assert decode_house_name("dynn_Barcelona") == "Barcelona"


def test_decode_house_name_strips_dyn_prefix() -> None:
    assert decode_house_name("dyn_Plantagenet") == "Plantagenet"


def test_decode_house_name_strips_dynasty_prefix() -> None:
    assert decode_house_name("dynasty_Frostbeard") == "Frostbeard"


def test_decode_house_name_strips_house_prefix() -> None:
    assert decode_house_name("house_munso") == "munso"


def test_decode_house_name_passes_through_no_prefix() -> None:
    """Custom-named houses (set via 'create dynasty' decision) carry the
    user-typed name verbatim; no prefix to strip."""
    assert decode_house_name("House of Erik") == "House of Erik"
    assert decode_house_name("Custom Dynasty") == "Custom Dynasty"


def test_decode_house_name_preserves_none_and_empty() -> None:
    assert decode_house_name(None) is None
    assert decode_house_name("") == ""


def test_decode_house_name_decodes_diacritic_escapes() -> None:
    """Bosnian dynasty 'Kotromanić' is stored 'dynn_KotromaniC_'; the
    house decoder reuses decode_ck3_name on the suffix."""
    assert decode_house_name("dynn_KotromaniC_") == "Kotromanic"


def test_decode_house_name_idempotent_on_decoded_input() -> None:
    """Feeding already-decoded output back returns it unchanged — keeps
    callers honest in pipelines where the same record may flow through
    twice."""
    once = decode_house_name("dynn_Barcelona")
    twice = decode_house_name(once)
    assert once == twice == "Barcelona"


# --- ck3_chronicler-90tv: strip_loca_markup ---


def test_strip_loca_markup_none_passthrough() -> None:
    assert strip_loca_markup(None) is None


def test_strip_loca_markup_empty_passthrough() -> None:
    assert strip_loca_markup("") == ""


def test_strip_loca_markup_clean_input_unchanged() -> None:
    """A war name that came through with no markup (already plain) is
    returned unchanged. Idempotent in the no-op case."""
    assert strip_loca_markup("Civil War") == "Civil War"


def test_strip_loca_markup_crusade_kingdom_of_jerusalem() -> None:
    """Acceptance scenario from the 90tv ticket — the live Thrugot
    smoke war_name for Svend joining the First Crusade."""
    raw = (
        "Crusade for \x15ONCLICK:TITLE,8755 \x15TOOLTIP:LANDED_TITLE,8755 "
        "\x15L; Kingdom of Jerusalem\x15!\x15!\x15!"
    )
    assert strip_loca_markup(raw) == "Crusade for Kingdom of Jerusalem"


def test_strip_loca_markup_dithmarschen_dual_title_war() -> None:
    """Acceptance scenario: a two-title war name with two ONCLICK +
    TOOLTIP wrappers and stranded "; " separators in both literal
    sections."""
    raw = (
        "2nd \x15ONCLICK:TITLE,111 \x15TOOLTIP:LANDED_TITLE,111 "
        "\x15L; Holy Roman\x15!\x15!\x15! De Jure War for the "
        "\x15ONCLICK:TITLE,633 \x15TOOLTIP:LANDED_TITLE,633 "
        "\x15L; County of Dithmarschen\x15!\x15!\x15!"
    )
    assert strip_loca_markup(raw) == "2nd Holy Roman De Jure War for the County of Dithmarschen"


def test_strip_loca_markup_character_with_rank_double_span() -> None:
    """Character-link form with two literal spans (rank + name). The
    sanitizer drops every marker; the inner text of both spans is
    preserved verbatim, which produces a slightly redundant 'high King
    high Svend III' rather than a deduplicated 'high King Svend III'.
    Acceptable: the prose is human-readable and the LLM can ignore the
    duplication. If a future smoke shows this pattern frequently, refine
    the regex to dedup the leading rank token."""
    raw = (
        "War against the Tyranny of \x15ONCLICK:CHARACTER,36956 "
        "\x15TOOLTIP:CHARACTER,36956 \x15L \x15high King\x15! "
        "\x15high Svend III\x15!\x15!\x15!\x15!"
    )
    assert strip_loca_markup(raw) == "War against the Tyranny of high King high Svend III"


def test_strip_loca_markup_idempotent_on_clean_output() -> None:
    """Feeding the cleaned output back in returns it unchanged — guards
    against double-application in pipelines (e.g. a backfill that
    re-runs sanitization on already-cleaned event payloads)."""
    raw = (
        "Crusade for \x15ONCLICK:TITLE,8755 \x15TOOLTIP:LANDED_TITLE,8755 "
        "\x15L; Kingdom of Jerusalem\x15!\x15!\x15!"
    )
    once = strip_loca_markup(raw)
    twice = strip_loca_markup(once)
    assert once == twice == "Crusade for Kingdom of Jerusalem"


# --- ck3_chronicler-4u14: strip_loca_markup composed with other decoders ---


def test_decode_ck3_name_strips_loca_markup_first() -> None:
    """4u14: a character first_name carrying \\x15 markup is stripped
    before the diacritic decoder runs. CK3 occasionally emits player-
    customised names with the same rich-text wrappers war_name carries
    (the 90tv signal). Composes: a name with BOTH a tooltip wrapper
    AND a diacritic escape inside should land on a clean Unicode form."""
    raw = "\x15TOOLTIP:CHARACTER,123 \x15L; E_lla\x15!\x15!"
    assert decode_ck3_name(raw) == "Ælla"


def test_decode_ck3_name_no_markup_path_unchanged() -> None:
    """The strip is a no-op on the typical plain-name input — guards
    against the 4u14 wrapping accidentally regressing the existing
    diacritic-decoder behaviour for names without markup."""
    assert decode_ck3_name("Eadmund") == "Eadmund"
    assert decode_ck3_name("E_lla") == "Ælla"


def test_decode_house_name_strips_loca_markup_around_prefix() -> None:
    """4u14: a player-customised dynasty name carrying \\x15 markup
    around the dynn_/dynasty_ prefix gets cleaned. Empirical signal:
    custom_name fields are free-text input that may pass through CK3's
    rich-text composer."""
    raw = "\x15TOOLTIP:DYNASTY,42 \x15L; dynn_Barcelona\x15!\x15!"
    assert decode_house_name(raw) == "Barcelona"


# --- ck3_chronicler-x2qj: post-decode Unicode fixup ---


def test_norse_engine_decoded_o_stroke_becomes_o_umlaut() -> None:
    """When CK3 emits the engine-decoded Unicode form "Ørvar" rather
    than the escape form "O_rvar", the culture-aware fixup pass still
    normalizes to the romanized "Örvar". Live signal: Örvar Sleggja
    (ck3_id 38137) whose first_name flipped across save phases."""
    assert decode_ck3_name("Ørvar", culture="norse") == "Örvar"
    assert decode_ck3_name("ørvar", culture="norse") == "örvar"


def test_norse_escape_form_continues_to_decode_to_o_umlaut() -> None:
    """The fixup must not break the existing escape-form path. Both
    forms should produce the same romanized output for norse."""
    assert decode_ck3_name("O_rvar", culture="norse") == "Örvar"


def test_norse_unicode_fixup_idempotent_on_already_correct_name() -> None:
    """Running the decoder on a name that's already in the destination
    form is a no-op — destination characters (Ö / ö) aren't in the
    fixup keys."""
    assert decode_ck3_name("Örvar", culture="norse") == "Örvar"


def test_unicode_fixup_does_not_fire_for_unknown_culture() -> None:
    """Cultures without a fixup map should pass the Unicode form
    through unchanged — we don't false-positive Ø→Ö for cultures that
    legitimately use Ø."""
    assert decode_ck3_name("Ørvar", culture="breton") == "Ørvar"
    assert decode_ck3_name("Ørvar", culture=None) == "Ørvar"


def test_unicode_fixup_only_touches_targeted_characters() -> None:
    """The fixup is a per-character map, not a wholesale Unicode
    transform — characters not in the fixup table pass through."""
    # Þ (thorn) is unrelated to Ø/Ö and must not be touched by the
    # norse fixup pass.
    assert decode_ck3_name("Þórr", culture="norse") == "Þórr"


# --- ck3_chronicler-kekj: name-suffix handling ------------------------------


def test_kekj_ordinal_disambiguation_suffix_stripped() -> None:
    """CK3 ruler-designer disambiguation: when multiple characters
    share a name in the same dynasty, the engine stamps a trailing
    ``_<digit>`` on the duplicates (``Masakane_2``, ``Shigemoto_3``).
    In-game UI strips this for display; the chronicler must too.

    Live anchor: 2026-05-17 Genji adventurer smoke, player's raw
    first_name = 'Masakane_2' rendered as 'Masakanæ2' because the
    mid-word ``e_`` got substituted to ``æ`` by ``_LC_ESCAPE_MAP``."""
    assert decode_ck3_name("Masakane_2") == "Masakane"
    assert decode_ck3_name("Shigemoto_3") == "Shigemoto"
    # Multi-digit ordinals (rare but possible — large dynasties)
    assert decode_ck3_name("John_42") == "John"


def test_kekj_no_diacritic_substitution_when_followed_by_digit() -> None:
    """The ``_LC_ESCAPE_MAP`` 'e_' -> 'æ' substitution must NOT fire
    on patterns like 'Masakane_2' where the underscore is followed
    by a digit — that's ordinal disambiguation, not an escape pair.

    Without this guard the name renders 'Masakanæ2' (mojibake)."""
    # Even if the suffix is preserved (no strip), the body shouldn't
    # be mojibaked. Combined with the strip above, the user sees
    # 'Masakane'.
    assert "æ" not in decode_ck3_name("Masakane_2")
    assert "æ" not in decode_ck3_name("Shigemoto_3")


def test_kekj_hex_codepoint_suffix_preserved() -> None:
    """East Asian names in CK3 use ``_HEX_HEX`` suffixes to encode
    Unicode codepoints (Shide_5E08_5FB7 = Shide 师 德). The decoder
    must NOT mangle these: the 'e_' before '5' is the same pattern
    that breaks ordinal handling, and the trailing digits aren't
    purely decimal so the ordinal-strip regex skips them too.

    Future codepoint-decode work would translate _5E08_5FB7 to actual
    Chinese characters — for now we just preserve the raw form
    rather than producing the 'Shidæ5E08_5FB7' mojibake the original
    bug would have caused."""
    assert decode_ck3_name("Shide_5E08_5FB7") == "Shide_5E08_5FB7"


def test_kekj_legitimate_underscore_letter_preserved() -> None:
    """Korean transliterations use underscore-separated syllables
    (Seung_gyeong, Jun_jeong). These should pass through unchanged
    — the letter after the underscore isn't a digit, so the
    ordinal guard doesn't fire, and neither letter sits in
    _LC_ESCAPE_MAP."""
    assert decode_ck3_name("Seung_gyeong") == "Seung_gyeong"
    assert decode_ck3_name("Jun_jeong") == "Jun_jeong"


def test_kekj_existing_diacritic_decode_unchanged() -> None:
    """Sanity: pre-existing diacritic decoding paths still work.
    The new ordinal-strip and digit-look-ahead are surgical — they
    only fire on the specific <letter>_<digit> pattern."""
    assert decode_ck3_name("E_lla") == "Ælla"
    assert decode_ck3_name("E_thelred") == "Æthelred"
    assert decode_ck3_name("O_rvar", culture="norse") == "Örvar"

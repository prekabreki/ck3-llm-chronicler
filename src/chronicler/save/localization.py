"""Decode CK3's name-field escape sequences to Unicode (ck3_chronicler-fe5).

CK3 stores Anglo-Saxon / Old Norse / Old English names with diacritical
marks as letter+underscore escapes inside save files: "E_lla" for Ælla,
"t_orfinn" for þorfinn, "d_unstan" for ðunstan, "O_ystein" for Øystein.
The in-game UI renders these correctly via the engine's localization
layer, but rakaly returns the raw save form, so without this transform
every Anglo-Saxon and Norse character shows up with mangled names in
biographies, memories, and the web UI.

Live signal driving this: Ælla 12267 (Anglo-Saxon ruler from the 867
bookmark) rendered as "E_lla" everywhere — biography opener, memory
text, character list. The nickname column captured "the Impaler"
correctly because rakaly resolves nickname_text through the engine's
localization, but first_name comes through raw.

Mapping derived from observed CK3 escapes for Anglo-Saxon, Old English,
Norse, and Scandinavian name lists. Coverage is intentionally narrow —
only escapes confirmed in real save data. Unknown letter+underscore
sequences pass through unchanged so we don't introduce false-positive
substitutions on dynasty names that happen to contain underscores. Add
new mappings as they're observed in live testing.
"""

from __future__ import annotations

import re

# Two-character escape → Unicode codepoint.
# Order matters only insofar as the longest escape wins on overlaps —
# all our escapes are exactly 2 chars so this is moot here.
_ESCAPE_MAP: dict[str, str] = {
    # Anglo-Saxon / Old English ash
    "E_": "Æ",
    "e_": "æ",
    # Old Norse / Old English thorn
    "T_": "Þ",
    "t_": "þ",
    # Old Norse / early Old English eth
    "D_": "Ð",
    "d_": "ð",
    # Norse / Old Norse o-with-stroke
    "O_": "Ø",
    "o_": "ø",
    # Scandinavian a-with-ring
    "A_": "Å",
    "a_": "å",
}

# ck3_chronicler-63y: lowercase entries of the escape map can be applied
# mid-word safely. Their case unambiguously signals a lowercase diacritic
# variant (e.g. ``t_`` → ``þ`` in the hypothetical ``E_lft_ryd_`` →
# ``Ælfþryð``). Capital entries CANNOT be applied mid-word without a
# culture (see _CULTURE_DIACRITIC_MAPS): CK3 overloads them across
# cultures — ``E_`` means Æ in Anglo-Saxon names but ë in Breton, è in
# Provençal, é in Hungarian; ``I_`` means í in Spanish/Czech; ``O_``
# means Ó in Icelandic; ``L_`` means ł in Polish. Without the character's
# culture context we can't pick the right diacritic, so the safest move
# is to drop the diacritic (lossy but readable) rather than false-positive
# substitute the wrong one.
_LC_ESCAPE_MAP: dict[str, str] = {k: v for k, v in _ESCAPE_MAP.items() if k[0].islower()}

# ck3_chronicler-57j: per-culture diacritic maps used when the caller
# threads a culture template name through ``decode_ck3_name``. Keys are
# CK3 culture template strings as returned by
# ``culture_manager.cultures[*].name`` (see
# :func:`chronicler.save.parse._parse_cultures_lookup`). Each value is a
# letter+underscore escape → Unicode replacement map covering both case
# variants (capital escape → uppercase diacritic, lowercase escape →
# lowercase diacritic).
#
# Coverage is intentionally narrow — only cultures with known overload
# pain. The 63y lossy fallback handles the rest. Add new maps as we
# observe more empirical data from real saves.
_CULTURE_DIACRITIC_MAPS: dict[str, dict[str, str]] = {
    # Anglo-Saxon / Old English: ash, thorn, eth.
    "anglo_saxon": {
        "E_": "Æ",
        "e_": "æ",
        "T_": "Þ",
        "t_": "þ",
        "D_": "Ð",
        "d_": "ð",
    },
    # English (post-1066 reform). Same canonical name set as anglo_saxon
    # because the underlying name corpus didn't change overnight.
    "english": {
        "E_": "Æ",
        "e_": "æ",
        "T_": "Þ",
        "t_": "þ",
        "D_": "Ð",
        "d_": "ð",
    },
    # Norse: ash, thorn, eth, plus Norse-specific umlaut-O + ring.
    # CK3's in-game UI renders O_ as Ö (umlaut) for Norse names — e.g.
    # "O_rvar" displays as "Örvar", not "Ørvar". The culture-blind
    # _ESCAPE_MAP keeps Ø for callers that don't pass a culture.
    "norse": {
        "E_": "Æ",
        "e_": "æ",
        "T_": "Þ",
        "t_": "þ",
        "D_": "Ð",
        "d_": "ð",
        "O_": "Ö",
        "o_": "ö",
        "A_": "Å",
        "a_": "å",
    },
    # Icelandic: O_ disambiguates to Ó (acute), not Ø — Ólafr/O_lafr was
    # the canonical example in the 57j filing. Thorn + eth still apply.
    "icelandic": {
        "A_": "Á",
        "a_": "á",
        "E_": "É",
        "e_": "é",
        "I_": "Í",
        "i_": "í",
        "O_": "Ó",
        "o_": "ó",
        "U_": "Ú",
        "u_": "ú",
        "T_": "Þ",
        "t_": "þ",
        "D_": "Ð",
        "d_": "ð",
    },
    # Castilian (early Spanish in CK3, e.g. 867 bookmark): acute + ñ.
    "castilian": {
        "A_": "Á",
        "a_": "á",
        "E_": "É",
        "e_": "é",
        "I_": "Í",
        "i_": "í",
        "O_": "Ó",
        "o_": "ó",
        "U_": "Ú",
        "u_": "ú",
        "N_": "Ñ",
        "n_": "ñ",
    },
    # Occitan (covers Provençal in CK3): grave-leaning acute set.
    "occitan": {
        "A_": "À",
        "a_": "à",
        "E_": "È",
        "e_": "è",
        "I_": "Ì",
        "i_": "ì",
        "O_": "Ò",
        "o_": "ò",
        "U_": "Ù",
        "u_": "ù",
    },
    # Magyar (CK3's name for Hungarian): acute set.
    "magyar": {
        "A_": "Á",
        "a_": "á",
        "E_": "É",
        "e_": "é",
        "I_": "Í",
        "i_": "í",
        "O_": "Ó",
        "o_": "ó",
        "U_": "Ú",
        "u_": "ú",
    },
    # Polish: stroke L (Stanisław). Other Polish diacritics (Ć, Ń, Ś,
    # Ż, etc.) aren't seen in observed save data yet — extend on signal.
    "polish": {
        "L_": "Ł",
        "l_": "ł",
    },
    # Breton: diaeresis (Hoël). One letter is the entirety of empirical
    # signal so far.
    "breton": {
        "E_": "Ë",
        "e_": "ë",
    },
    # Sámi (Lappish): acute á is the dominant diacritic in personal
    # names (Máidna, Áidná, Áilu). Other Sámi diacritics (č, đ, š, ž, ŋ)
    # exist in the language but haven't been observed yet in save data
    # — extend on signal, matching the per-culture conservative
    # convention.
    "sami": {
        "A_": "Á",
        "a_": "á",
    },
}


# ck3_chronicler-x2qj: post-decode Unicode fixup applied when CK3 has
# already engine-localised a name into Unicode form *before* writing
# the save (so the input to `decode_ck3_name` contains literal "Ø" /
# "ø" rather than the escape pair "O_" / "o_"). The escape-map path
# above is a no-op on these inputs — they have no `_` to match — so
# without a separate pass they would persist with whatever Unicode
# letter CK3 emitted, even when the culture's romanization convention
# wants something different.
#
# Live signal: Örvar Sleggja (ck3_id 38137, culture=norse) — across
# a single playthrough's saves his `first_name` flipped from the
# escape form "O_rvar" (decoded to "Örvar" via the culture map) to
# the engine-decoded form "Ørvar". The upsert overwrote the previously
# correct "Örvar" with "Ørvar" because the second decode was a pass-
# through. The norse mapping here normalizes both forms to the same
# romanized output.
#
# Coverage is intentionally narrow — only cultures with empirical
# evidence. Add cultures as live signal surfaces them; unknown culture
# strings silently skip the fixup pass.
_CULTURE_UNICODE_FIXUP: dict[str, dict[str, str]] = {
    # Norse: CK3's in-game UI uses Ö (umlaut), not Ø (stroke), for
    # Old Norse names. Both forms appear in saves depending on whether
    # the engine localised before serializing.
    "norse": {
        "Ø": "Ö",
        "ø": "ö",
    },
}


# Letters that CK3 uses as diacritic-escape markers, observed empirically
# across one full save's character roster (~60k chars: Anglo-Saxon,
# Norse, Icelandic, Spanish, Italian, Hungarian, Provençal, Polish,
# Slavic, Breton, Tibetan, Basque). Used by the mid-word fallback to
# distinguish CK3 diacritic-escape sequences from genuine underscores in
# names. Letters outside this set keep the v0.5/fe5 conservative
# behavior (pass through unchanged) so we don't regress
# ``test_unknown_letter_underscore_left_alone``.
_KNOWN_ESCAPE_LETTERS: frozenset[str] = frozenset("ACDEILOSTU" + "acdeilostu")


def decode_ck3_name(raw: str | None, culture: str | None = None) -> str | None:
    """Decode CK3 name-field escapes into readable Unicode.

    Walks the string left-to-right with three rules:

    1. **Position 0** matches against the full :data:`_ESCAPE_MAP` so
       Anglo-Saxon (``E_lla`` → ``Ælla``) and Norse (``T_orsteinn`` →
       ``Þorsteinn``) name-start escapes decode correctly.
    2. **Mid-word** matches against :data:`_LC_ESCAPE_MAP` only. This
       keeps Norse compound forms like ``E_lft_ryd_`` → ``Ælfþryð``
       working without false-positive-decoding capital escapes that
       mean different diacritics in different cultures (see ck3_chronicler-63y).
    3. **Fallback** for ``<Letter>_`` where ``<Letter>`` is in
       :data:`_KNOWN_ESCAPE_LETTERS` but the pair isn't in either map:
       drop the underscore and (at mid-word only) lowercase the letter.
       Lossy — diacritic information is dropped — but produces readable
       output (``HoE_l`` → ``Hoel``, ``MarI_a`` → ``Maria``) instead of
       false-positive substitutions (``HoE_l`` → ``HoÆl``, the original
       63y bug).

    Idempotent: feeding an already-decoded Unicode string back in
    returns it unchanged. ``None`` and empty strings pass through.

    ``culture`` (ck3_chronicler-57j): when supplied, the corresponding
    entry in :data:`_CULTURE_DIACRITIC_MAPS` overrides rules 1–2 for
    every escape pair it knows. So ``decode_ck3_name("HoE_l",
    culture="breton")`` resolves the mid-word ``E_`` to ``ë`` instead of
    falling through to the lossy strip. Unknown culture strings (or
    cultures without a map entry) silently fall through to the
    culture-blind path so the caller can pass cultures_lookup output
    verbatim without filtering.

    ck3_chronicler-4u14: strips CK3 ``\\x15`` tooltip/link markup before
    diacritic decoding. CK3 occasionally emits character names + custom
    nicknames with the same rich-text wrappers war_name carries (90tv
    surfaced the live signal). The strip is a no-op on strings without
    ``\\x15`` so the diacritic path stays unchanged for the typical
    plain-name input.
    """
    if not raw:
        return raw
    raw = strip_loca_markup(raw)
    if not raw:
        return raw
    culture_map = _CULTURE_DIACRITIC_MAPS.get(culture) if culture else None
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if i + 1 < n:
            pair = raw[i : i + 2]
            # ck3_chronicler-kekj: `<letter>_<digit>` is a CK3 internal
            # marker, not a diacritic escape. Two known cases: (a)
            # ruler-designer ordinal disambiguation like ``Masakane_2``
            # / ``Shigemoto_3``; (b) East Asian Unicode codepoint
            # encoding like ``Shide_5E08_5FB7``. Without this guard
            # the mid-word ``e_`` (or any other lowercase entry in
            # ``_LC_ESCAPE_MAP``) gets substituted to the diacritic
            # form, producing ``Masakanæ2`` mojibake. Skip the
            # substitution branches and let the letter pass through
            # so the underscore + tail stay intact for the post-loop
            # ordinal strip and any future codepoint decode.
            if pair[1] == "_" and i + 2 < n and raw[i + 2].isdigit():
                out.append(raw[i])
                i += 1
                continue
            # Culture-aware lookup wins when present — it disambiguates
            # the mid-word capital escapes that the culture-blind path
            # has to strip lossily. At position 0, a capital escape
            # produces the capital diacritic (E_lla → Ælla). Mid-word, a
            # capital escape produces the LOWERCASE diacritic (HoE_l →
            # Hoël) — the escape's case is the diacritic-marker, not the
            # diacritic char's case (mirrors the culture-blind 63y rule
            # that lowercases mid-word capital escape letters).
            if culture_map is not None:
                lookup = pair if i == 0 else pair.lower()
                cm_replacement = culture_map.get(lookup)
                if cm_replacement is not None:
                    out.append(cm_replacement)
                    i += 2
                    continue
            map_to_use = _ESCAPE_MAP if i == 0 else _LC_ESCAPE_MAP
            replacement = map_to_use.get(pair)
            if replacement is not None:
                out.append(replacement)
                i += 2
                continue
            if raw[i + 1] == "_" and raw[i] in _KNOWN_ESCAPE_LETTERS:
                # Drop diacritic info we can't disambiguate. Preserve
                # case at name-start (so ``C_ilbu`` becomes ``Cilbu``,
                # not ``cilbu``) but lowercase mid-word since the
                # original capital was just the escape marker.
                out.append(raw[i] if i == 0 else raw[i].lower())
                i += 2
                continue
        out.append(raw[i])
        i += 1
    result = "".join(out)

    # ck3_chronicler-x2qj: Unicode fixup pass for cultures whose
    # romanization convention differs from CK3's engine-emitted Unicode
    # form. Idempotent — running on an already-fixed string is a no-op
    # because the destination characters aren't in the fixup keys.
    fixup = _CULTURE_UNICODE_FIXUP.get(culture) if culture else None
    if fixup:
        result = "".join(fixup.get(ch, ch) for ch in result)

    # ck3_chronicler-kekj: strip CK3 ruler-designer ordinal-disambiguation
    # suffix at the end of the name (``Masakane_2`` -> ``Masakane``,
    # ``Shigemoto_3`` -> ``Shigemoto``). CK3 displays the bare name
    # in-UI; the trailing ``_<digits>`` is internal bookkeeping for
    # dynasty/culture disambiguation. Decimal-only so it doesn't
    # false-positive on hex-codepoint suffixes — ``Shide_5E08_5FB7``
    # stays intact pending a future codepoint-decode pass.
    result = re.sub(r"_\d+$", "", result)
    return result


# ck3_chronicler-6d1c: house keys CK3 emits in dynasties_lookup /
# houses_lookup are localization-token form (``dynn_Barcelona``,
# ``dyn_de_Barcelona``, ``dynasty_Frostbeard``). The actual
# human-readable form lives in CK3's localization YAMLs (e.g.
# ``localization/english/house_names_l_english.yml`` maps
# ``dynn_Barcelona`` → ``Barcelona`` for the English locale). Without
# YAML access at runtime the chronicler can't resolve the canonical
# display name, but it can do the next best thing: strip the prefix and
# decode any embedded culture-escape diacritics. The fallback is a
# stable, readable approximation that's almost always correct (the
# vast majority of dynasty keys are ``<prefix>_<FamilyName>`` with no
# rewriting in loca beyond casing/articles).
_HOUSE_KEY_PREFIXES: tuple[str, ...] = (
    "dynn_",
    "dyn_",
    "dynasty_",
    "house_",
)


def decode_house_name(raw: str | None, culture: str | None = None) -> str | None:
    """ck3_chronicler-6d1c: best-effort display name from a CK3 house key.

    Strips the leading namespace prefix (``dynn_`` etc.) then runs the
    remainder through :func:`decode_ck3_name` so embedded diacritic
    escapes (``KotromaniC_`` → ``Kotromanic``) decode the same way
    character names do. Returns the input unchanged when no prefix
    matches — that catches loca-already-resolved values flowing through
    the same pipeline so callers don't need a separate ``is_raw_key``
    branch. Idempotent.

    This is a fallback. When CK3 localization YAML access lands later
    (an ``.ymd``-style table mapping ``dynn_*`` → display name), prefer
    the YAML hit and fall back to this only on miss.

    ck3_chronicler-4u14: strips CK3 ``\\x15`` tooltip/link markup before
    prefix detection. Player-customised dynasty names (the
    ``localized_name`` / ``custom_name`` field in
    :func:`chronicler.save.parse._resolve_dynasty_or_house_name`) can
    in principle contain markup; the strip is idempotent on the typical
    plain-name input.
    """
    if not raw:
        return raw
    raw = strip_loca_markup(raw)
    if not raw:
        return raw
    for prefix in _HOUSE_KEY_PREFIXES:
        if raw.startswith(prefix):
            tail = raw[len(prefix) :]
            return decode_ck3_name(tail, culture=culture) or tail
    return raw


# ck3_chronicler-90tv: CK3 emits localized strings (war names, character
# titles, and similar rich-text fields) wrapped in tooltip/link markup
# using \x15 (NAK, ASCII 0x15) as a directive delimiter. Example war_name:
#   "Crusade for \x15ONCLICK:TITLE,8755 \x15TOOLTIP:LANDED_TITLE,8755 "
#   "\x15L; Kingdom of Jerusalem\x15!\x15!\x15!"
# rakaly returns the raw save form; without a strip pass the chronicler
# stores it verbatim and biographies see unusable garbage. Stripping the
# markup yields the visible text CK3 would render in-game.
#
# Directive grammar (empirically observed):
#   \x15<ALL_CAPS>:<args>...   payload directive (ONCLICK:TYPE,ID etc.)
#                              no visible text — drop the whole run up to
#                              the next \x15.
#   \x15L                      literal-text marker — drop the marker, keep
#                              what follows (often after a stray "; ").
#   \x15!                      close marker — drop.
#   \x15<other>                stray open-marker around a literal span
#                              (e.g. \x15high Svend III\x15!) — drop just
#                              the marker; the inner text is visible.
_CK3_DIRECTIVE_WITH_PAYLOAD = re.compile(r"\x15[A-Z]+:[^\x15]*")
_CK3_LITERAL_MARKER = re.compile(r"\x15L\b")
_CK3_CLOSE_MARKER = re.compile(r"\x15!")
_LEADING_PUNCT = re.compile(r"^[\s;,:.\-]+")
_STRANDED_SEMICOLON = re.compile(r"\s+[;,]\s+")
_WHITESPACE = re.compile(r"\s+")


def strip_loca_markup(raw: str | None) -> str | None:
    """Strip CK3 tooltip/link markup, leaving the visible text.

    Idempotent: a string with no \\x15 markers is returned unchanged
    (including ``None`` and ``""``). The L directive sometimes leaves a
    stranded "; " or ", " separator behind in the literal section; those
    are collapsed so the output reads as natural prose.

    Examples:
        >>> strip_loca_markup(
        ...     "Crusade for \\x15ONCLICK:TITLE,8755 "
        ...     "\\x15TOOLTIP:LANDED_TITLE,8755 \\x15L; "
        ...     "Kingdom of Jerusalem\\x15!\\x15!\\x15!"
        ... )
        'Crusade for Kingdom of Jerusalem'
    """
    if not raw or "\x15" not in raw:
        return raw
    s = _CK3_DIRECTIVE_WITH_PAYLOAD.sub("", raw)
    s = _CK3_LITERAL_MARKER.sub("", s)
    s = _CK3_CLOSE_MARKER.sub("", s)
    s = s.replace("\x15", "")
    s = _WHITESPACE.sub(" ", s)
    s = _LEADING_PUNCT.sub("", s)
    s = _STRANDED_SEMICOLON.sub(" ", s)
    return s.strip()

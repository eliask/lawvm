"""Tests for the Finnish defined-term / alias binding recognizer.

Gates the three CONSERVATIVE binding shapes, the complex-NP morphology refusal,
and the NEGATIVE no-fabrication discipline.
"""
from __future__ import annotations

from lawvm.finland.references.defined_terms import (
    BINDING_JALJEMPANA,
    BINDING_PARENTHETICAL_ALIAS,
    BINDING_TARKOITETAAN,
    SCOPE_VALUES,
    STATUS_OK,
    STATUS_UNSUPPORTED_MORPHOLOGY,
    DefinedTermBinding,
    recognize_defined_term_bindings,
)


def _by_kind(bindings: list[DefinedTermBinding], kind: str) -> list[DefinedTermBinding]:
    return [b for b in bindings if b.binding_kind == kind]


# ---------------------------------------------------------------------------
# Shape 1: parenthetical alias right after an act cite
# ---------------------------------------------------------------------------


def test_parenthetical_alias_binds_to_eu_act() -> None:
    text = (
        "Eläimistä saatavista sivutuotteista annetussa asetuksessa "
        "(EY) N:o 1069/2009 (sivutuoteasetus) säädetään tarkemmin."
    )
    bindings = recognize_defined_term_bindings(text, source_file="he.xml")
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    b = alias[0]
    assert b.term == "sivutuoteasetus"
    assert b.target_ref == "1069/2009"
    assert b.expansion is None
    assert b.scope == "statute"
    assert b.binding_status == STATUS_OK
    assert b.source_span.source_file == "he.xml"
    assert b.source_span.byte_len > 0


def test_parenthetical_alias_binds_to_finnish_act() -> None:
    text = "Ympäristönsuojelulaissa (527/2014) (ympäristönsuojelulaki) säädetään."
    bindings = recognize_defined_term_bindings(text)
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    assert alias[0].term == "ympäristönsuojelulaki"
    # FI ids canonicalize to YEAR/NUMBER (the visible cite is "(527/2014)").
    assert alias[0].target_ref == "2014/527"
    assert alias[0].binding_status == STATUS_OK


def test_parenthetical_not_after_cite_is_not_bound() -> None:
    # A parenthetical that does NOT follow an act cite is not an alias binding.
    text = "Tämä on tavallista tekstiä (huomautus) ilman lakiviittausta."
    bindings = recognize_defined_term_bindings(text)
    assert _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS) == []


# ---------------------------------------------------------------------------
# Shape 2: jäljempänä X
# ---------------------------------------------------------------------------


def test_jaljempana_unquoted_binds_to_finnish_act() -> None:
    text = "Ympäristönsuojelulaissa (527/2014, jäljempänä ympäristönsuojelulaki) säädetään."
    bindings = recognize_defined_term_bindings(text)
    jal = _by_kind(bindings, BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "ympäristönsuojelulaki"
    # FI ids canonicalize to YEAR/NUMBER (the visible cite is "(527/2014)").
    assert jal[0].target_ref == "2014/527"
    assert jal[0].binding_status == STATUS_OK


def test_jaljempana_quoted_binds_to_eu_act() -> None:
    text = 'asetuksessa (EY) N:o 1069/2009 (jäljempänä "sivutuoteasetus") tarkoitettu.'
    bindings = recognize_defined_term_bindings(text)
    jal = _by_kind(bindings, BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "sivutuoteasetus"
    assert jal[0].target_ref == "1069/2009"
    # The quoted alias parenthesis must NOT also be emitted as a bare
    # parenthetical alias.
    assert _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS) == []


def test_jaljempana_fi_target_is_canonical_year_number_orientation() -> None:
    # The Finnish VISIBLE cite is "(906/2019)" (NUMBER/YEAR); the binding's
    # target_ref is the CANONICAL "YEAR/NUMBER" id (same orientation as the
    # <ref> / cross-ref lane and the corpus store keys), so the same act never
    # splits into a second, inverted legal_work_entity.
    text = (
        "julkisen hallinnon tiedonhallinnasta annetun lain (906/2019), "
        "jäljempänä tiedonhallintalaki, mukaan."
    )
    jal = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "tiedonhallintalaki"
    assert jal[0].target_ref == "2019/906"  # canonical, NOT the visible "906/2019"
    # The canonical orientation matches how the corpus store splits a statute id
    # (``year, num = sid.split("/", 1)``): year first, then running number.
    year, num = jal[0].target_ref.split("/", 1)
    assert (year, num) == ("2019", "906")


def test_jaljempana_eu_target_keeps_source_orientation() -> None:
    # EU ids are NOT canonicalized: their source surface orientation is kept
    # (here NUMBER/YEAR from the "N:o 1069/2009" cite), unaffected by the FI fix.
    text = 'asetuksen (EY) N:o 1069/2009, jäljempänä sivutuoteasetus, mukaan.'
    jal = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "sivutuoteasetus"
    assert jal[0].target_ref == "1069/2009"


# ---------------------------------------------------------------------------
# Shape 3: X tarkoitetaan Y
# ---------------------------------------------------------------------------


def test_tarkoitetaan_binds_term_to_expansion() -> None:
    text = "Sivutuotteella tarkoitetaan kuollutta eläintä tai sen osaa."
    bindings = recognize_defined_term_bindings(text)
    tk = _by_kind(bindings, BINDING_TARKOITETAAN)
    assert len(tk) == 1
    # The defined-term SURFACE is preserved as written (the adessive form); the
    # adessive cannot be reverse-inflected (M1 is generation-only), so the term is
    # matched by its exact surface and flagged morphologically unsupported.
    assert tk[0].term == "Sivutuotteella"
    assert tk[0].binding_status == STATUS_UNSUPPORTED_MORPHOLOGY
    assert tk[0].target_ref is None
    assert tk[0].expansion is not None
    assert "kuollutta" in tk[0].expansion


def test_tarkoitetaan_binds_term_to_act_when_expansion_is_a_cite() -> None:
    text = (
        "Sivutuoteasetuksella tarkoitetaan asetusta (EY) N:o 1069/2009 "
        "eläimistä saatavista sivutuotteista."
    )
    bindings = recognize_defined_term_bindings(text)
    tk = _by_kind(bindings, BINDING_TARKOITETAAN)
    assert len(tk) == 1
    # Surface preserved as written; the act target is still resolved from the
    # expansion cite.
    assert tk[0].term == "Sivutuoteasetuksella"
    assert tk[0].binding_status == STATUS_UNSUPPORTED_MORPHOLOGY
    assert tk[0].target_ref == "1069/2009"
    assert tk[0].expansion is None


# ---------------------------------------------------------------------------
# Complex-NP morphology refusal
# ---------------------------------------------------------------------------


def test_complex_np_alias_is_unsupported_morphology() -> None:
    # An agreeing modifier + noun (case-marked modifier) is a complex NP:
    # emitted (binding not dropped) but flagged unsupported.
    text = "asetuksessa (EY) N:o 1069/2009 (yleisessä sivutuoteasetuksessa) tarkoitettu."
    bindings = recognize_defined_term_bindings(text)
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    assert alias[0].binding_status == STATUS_UNSUPPORTED_MORPHOLOGY
    # Target is still known — the binding is accounted for, not silently dropped.
    assert alias[0].target_ref == "1069/2009"


def test_final_head_compound_is_supported() -> None:
    # An invariant modifier + inflectable head (no case marker on modifier) is a
    # final-head compound → supported.
    text = "asetuksessa (EY) N:o 1069/2009 (uusi sivutuoteasetus) tarkoitettu."
    bindings = recognize_defined_term_bindings(text)
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    assert alias[0].binding_status == STATUS_OK
    assert alias[0].term == "uusi sivutuoteasetus"


# ---------------------------------------------------------------------------
# NEGATIVE: bare term, no binding construct → no binding
# ---------------------------------------------------------------------------


def test_bare_term_without_binding_yields_nothing() -> None:
    # "sivutuoteasetus" appears in inflected use but is NEVER bound here.
    text = (
        "Sivutuoteasetuksen mukaan toiminnanharjoittajan on noudatettava "
        "sivutuoteasetuksessa säädettyjä vaatimuksia."
    )
    bindings = recognize_defined_term_bindings(text)
    assert bindings == []


def test_empty_text_yields_nothing() -> None:
    assert recognize_defined_term_bindings("") == []


# ---------------------------------------------------------------------------
# Shape 3 scope cue: the definitional binding inherits the scope of the nearest
# preceding definitions-block cue (closed vocabulary; conservative default).
# ---------------------------------------------------------------------------


def test_scope_default_is_statute_when_no_cue() -> None:
    # No recognisable narrower cue → conservative statute default (prior behaviour).
    text = "Sivutuotteella tarkoitetaan kuollutta eläintä tai sen osaa."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "statute"


def test_scope_tassa_laissa_is_statute() -> None:
    text = "Tässä laissa sivutuotteella tarkoitetaan kuollutta eläintä."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "statute"


def test_scope_tata_lakia_sovellettaessa_is_statute() -> None:
    text = "Tätä lakia sovellettaessa sivutuotteella tarkoitetaan kuollutta eläintä."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "statute"


def test_scope_tassa_luvussa_is_chapter() -> None:
    # A "Tässä luvussa" header before per-item definienda → chapter scope inherited
    # by each definition in the block.
    text = (
        "Tässä luvussa: tietojärjestelmällä tarkoitetaan kokonaisuutta; "
        "rekisterinpitäjällä tarkoitetaan tahoa."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 2
    assert {b.scope for b in tk} == {"chapter"}
    assert {b.term for b in tk} == {"tietojärjestelmällä", "rekisterinpitäjällä"}


def test_scope_tassa_pykalassa_is_section() -> None:
    text = "Tässä pykälässä viranomaisella tarkoitetaan valtion virastoa."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "section"


def test_scope_tassa_momentissa_is_subsection() -> None:
    text = "Tässä momentissa kuljettajalla tarkoitetaan ajoneuvon ohjaajaa."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "subsection"


def test_scope_cue_beyond_window_falls_back_to_statute() -> None:
    # A cue further back than the bounded look-back window must NOT leak into a
    # later definition (fail-safe to the conservative statute default).
    text = (
        "Tässä luvussa annetaan yleisiä säännöksiä. "
        + ("täytesana " * 700)
        + "Sivutuotteella tarkoitetaan kuollutta eläintä."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "statute"


def test_nearest_cue_wins_chapter_overrides_statute_application() -> None:
    # A statute-wide application clause higher up, then a chapter header nearer the
    # definiendum → the nearer (chapter) cue wins.
    text = (
        "Tätä lakia sovellettaessa noudatetaan seuraavaa. "
        "Tässä luvussa rekisterinpitäjällä tarkoitetaan tahoa."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].scope == "chapter"


def test_aliases_stay_statute_scope() -> None:
    # Act-level aliases are document-wide naming conventions → always statute.
    text = (
        "Ympäristönsuojelulaissa (527/2014, jäljempänä ympäristönsuojelulaki) "
        "ja asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) säädetään."
    )
    bindings = recognize_defined_term_bindings(text)
    for b in bindings:
        if b.binding_kind in (BINDING_JALJEMPANA, BINDING_PARENTHETICAL_ALIAS):
            assert b.scope == "statute"


def test_enumerated_block_chapter_items_inherit_chapter_scope() -> None:
    # Canonical Finnish definitions block: a "Tässä luvussa tarkoitetaan:" header
    # followed by a ';'-separated list whose items open with an adessive
    # definiendum. Each item binds and inherits the header's chapter scope.
    text = (
        "Tässä luvussa tarkoitetaan: tietojärjestelmällä sähköistä "
        "kokonaisuutta; rekisterinpitäjällä toiminnasta vastaavaa tahoa;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert {b.scope for b in tk} == {"chapter"}
    assert {b.term for b in tk} == {"tietojärjestelmällä", "rekisterinpitäjällä"}


def test_enumerated_block_statute_header_items_are_statute() -> None:
    text = (
        "Tässä laissa tarkoitetaan: sivutuotteella kuollutta eläintä; "
        "jätteellä hylättävää ainetta;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert {b.scope for b in tk} == {"statute"}
    assert {b.term for b in tk} == {"sivutuotteella", "jätteellä"}


def test_enumerated_block_item_act_cite_expansion_resolves_target() -> None:
    text = (
        "Tässä laissa tarkoitetaan: sivutuoteasetuksella asetusta "
        "(EY) N:o 1069/2009;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].target_ref == "1069/2009"
    assert tk[0].scope == "statute"


def test_enumerated_block_non_adessive_item_does_not_bind() -> None:
    # An item that does not open with an adessive definiendum is not fabricated
    # into a binding (no fabrication discipline).
    text = "Tässä laissa tarkoitetaan: ja muuta sellaista;"
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert tk == []


def test_enumerated_block_decree_header_items_are_statute() -> None:
    # A decree ("asetus") opens its definitions block exactly like a law:
    # "Tässä asetuksessa tarkoitetaan:" → each item is statute(=instrument)-wide.
    text = (
        "Tässä asetuksessa tarkoitetaan: eläkelailla lyhytaikaisten "
        "työsuhteiden eläkelakia; vakuutetulla eläkelain mukaan vakuutettavaa "
        "työntekijää;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert {b.scope for b in tk} == {"statute"}
    assert {b.term for b in tk} == {"eläkelailla", "vakuutetulla"}


def test_enumerated_block_decision_header_items_are_statute() -> None:
    # A government decision ("päätös") definitions block: "Tässä päätöksessä
    # tarkoitetaan:" governs each adessive-definiendum item at instrument scope.
    text = (
        "Tässä päätöksessä tarkoitetaan: räjähdysaineella ainetta; "
        "välineellä laitetta;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert {b.scope for b in tk} == {"statute"}
    assert {b.term for b in tk} == {"räjähdysaineella", "välineellä"}


def test_enumerated_block_sovellettaessa_application_cue_header() -> None:
    # The APPLICATION-cue enumerated header "… sovellettaessa tarkoitetaan:" opens
    # a statute-wide definitions block exactly like "Tässä laissa tarkoitetaan:".
    # Previously dropped by both lanes (the "Tässä <unit>" arm did not match), so
    # the items were silently lost. Statute scope (application cue = whole
    # instrument, identical to "Tätä lakia sovellettaessa").
    text = (
        "Valvontalakia ja tätä asetusta sovellettaessa tarkoitetaan: "
        "työsuojeluviranomaisella valvovaa viranomaista; "
        "työpaikalla työn tekemisen paikkaa;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert {b.scope for b in tk} == {"statute"}
    assert {b.term for b in tk} == {"työsuojeluviranomaisella", "työpaikalla"}


def test_sovellettaessa_referential_noudatetaan_is_not_a_block_header() -> None:
    # The ambiguity guard: a "… sovellettaessa noudatetaan …" referential clause
    # ("when applying …, the following is observed") lacks the "tarkoitetaan:"
    # tail and must NOT open a definitions block (no fabrication).
    text = (
        "Tätä lakia sovellettaessa noudatetaan seuraavaa: "
        "viranomaisella on oikeus saada tietoja;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert tk == []


def test_decree_inline_definiendum_before_verb_is_statute() -> None:
    # Inline decree definition with the definiendum (adessive) BEFORE the verb,
    # mirroring the "Tässä laissa X:llä tarkoitetaan …" inline shape.
    text = "Tässä asetuksessa rekisterinpitäjällä tarkoitetaan seurakunnan kirkkoherraa."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].term == "rekisterinpitäjällä"
    assert tk[0].scope == "statute"


def test_decree_referential_saadetaan_is_not_a_definition_header() -> None:
    # The far more common REFERENTIAL idiom "Tässä asetuksessa säädetään …"
    # ("provided for in this decree") must NOT be admitted as a definitions
    # header — the ``tarkoitetaan`` ambiguity guard inherits to the decree word.
    text = "Jollei tässä asetuksessa säädetään, sovelletaan yleislakia."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert tk == []


def test_decision_referential_maaratan_is_not_a_definition_header() -> None:
    text = "Siten kuin tässä päätöksessä määrätään, on noudatettava asianmukaisesti."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert tk == []


def test_all_scopes_are_in_closed_vocabulary() -> None:
    text = (
        "Tässä luvussa tietojärjestelmällä tarkoitetaan kokonaisuutta. "
        "Tässä pykälässä viranomaisella tarkoitetaan virastoa. "
        "Tässä momentissa kuljettajalla tarkoitetaan ohjaajaa. "
        "Sivutuotteella tarkoitetaan eläintä."
    )
    bindings = recognize_defined_term_bindings(text)
    assert bindings
    for b in bindings:
        assert b.scope in SCOPE_VALUES


# ---------------------------------------------------------------------------
# Surface fidelity: multi-word definienda + no stem mangling
# ---------------------------------------------------------------------------


def test_enumerated_multiword_definiendum_surface_is_preserved() -> None:
    # The defined term is the FULL adessive-headed phrase, not just the head word
    # ("palkansaajaan rinnastettavalla yrittäjällä"), and never truncated to
    # "yrittäjä".  (Regression: 1984/602.)
    text = (
        "Tässä laissa tarkoitetaan: "
        "palkansaajalla työntekijää; "
        "palkansaajaan rinnastettavalla yrittäjällä luonnollista henkilöä;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert "palkansaajaan rinnastettavalla yrittäjällä" in terms
    assert "palkansaajalla" in terms
    # No truncated single-word head and no mangled stem.
    assert "yrittäjä" not in terms
    assert "yrittäjällä" not in terms


def test_inline_multiword_definiendum_surface_is_preserved() -> None:
    text = (
        "palkansaajaan rinnastettavalla yrittäjällä tarkoitetaan "
        "henkilöä, joka tekee työtä."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].term == "palkansaajaan rinnastettavalla yrittäjällä"
    assert tk[0].binding_status == STATUS_UNSUPPORTED_MORPHOLOGY


def test_function_word_after_colon_is_not_minted_as_term() -> None:
    # In the enumerated "Tässä laissa tarkoitetaan:" shape the defined terms are
    # the adessive heads AFTER the colon, never the locative ("laissa") before
    # "tarkoitetaan".  The header locative must not be minted as a defined term.
    text = "Tässä laissa tarkoitetaan: tuotteella esinettä;"
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert "tuotteella" in terms
    assert "laissa" not in terms
    assert "tuottee" not in terms  # no stem mangling


def test_adessive_definiendum_not_mangled_to_garbage_stem() -> None:
    # The broken stem-stripper turned "kustannuksilla" into "kustannuksi"; the
    # surface must now be preserved verbatim.
    text = "Tässä laissa tarkoitetaan: kustannuksilla välittömiä menoja;"
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert len(tk) == 1
    assert tk[0].term == "kustannuksilla"
    assert "kustannuksi" != tk[0].term


# ---------------------------------------------------------------------------
# Alias-paren misfires: CELEX bodies and inline markup
# ---------------------------------------------------------------------------


def test_celex_parenthetical_is_not_minted_as_alias() -> None:
    # "(EU) 2020/284 (32020L0284)" — the CELEX paren is the machine id of the same
    # act, not a Finnish alias surface; no binding may be minted for it.
    text = (
        "Neuvoston direktiivissä (EU) 2020/284 (32020L0284) säädetään asiasta."
    )
    bindings = recognize_defined_term_bindings(text)
    terms = {b.term for b in bindings}
    assert "32020L0284" not in terms
    assert not any(
        b.binding_kind == BINDING_PARENTHETICAL_ALIAS and b.term == "32020L0284"
        for b in bindings
    )


# ---------------------------------------------------------------------------
# Over-capture: cross-reference idiom + swept clause fragments are NOT definienda
# ---------------------------------------------------------------------------


def test_cross_reference_idiom_in_enum_block_is_not_a_definition() -> None:
    # "… N §:ssä tarkoitetulla tavalla" is the CROSS-REFERENCE idiom ("in the
    # manner referred to in § N"), not a definition. After tokenization the
    # "§:" is lost, leaving "ssä tarkoitetulla" — a bare suffix fragment + the
    # reference participle. It must NOT be minted as a defined term. (2023/371.)
    text = (
        "Tässä laissa tarkoitetaan: tuella avustusta; "
        "laitokselle on osoitettu määrärahaa 7 luvun 15 ssä tarkoitetulla tavalla;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert "ssä tarkoitetulla" not in terms
    assert not any("tarkoitetulla" in t for t in terms)
    # The genuine definiendum in the same block survives.
    assert "tuella" in terms


def test_swept_clause_fragment_with_verb_is_not_a_definition() -> None:
    # A stray sentence-internal colon ("kuntalain (410/2015) …") lets the enum
    # item regex sweep a clause containing the infinitive "katsota" and a bare
    # "n" fragment. It spans a clause boundary → declined. (2023/371.)
    text = (
        "Tässä laissa tarkoitetaan: hankkeella toimenpidettä; "
        "toimintaa, jota ei kuntalain (410/2015) 126 n mukaan katsota "
        "kilpailluilla markkinoilla tapahtuvaksi toiminnaksi;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert not any("katsota" in t for t in terms)
    assert not any(t.startswith("n ") for t in terms)
    assert "hankkeella" in terms


def test_swept_clause_fragment_with_postposition_is_not_a_definition() -> None:
    # "… tämän lain ja EU:n geenivara-asetuksen sekä niiden nojalla annettujen …"
    # — the "EU:" colon sweeps a clause fragment opening with a bare "n" fragment
    # and containing the cross-reference postposition "nojalla". Declined; no
    # garbled term. (2016/394.)
    text = (
        "Tässä laissa tarkoitetaan: viranomaisella valvovaa virastoa; "
        "noudatettava tämän lain ja EU:n geenivara-asetuksen sekä niiden "
        "nojalla annettujen säännösten vaatimuksia;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert "n geenivara-asetuksen sekä niiden nojalla" not in terms
    assert not any("nojalla" in t for t in terms)
    assert "viranomaisella" in terms


def test_coordinated_definienda_still_bind() -> None:
    # Finnish definitions routinely coordinate two definienda with "ja" / "tai":
    # "Pintaverkolla ja pintaverkkopyydyksellä tarkoitetaan …". A plain
    # coordinator is NOT a clause-spill signal — the coordinated phrase must
    # survive. (1982/1116 / 1982/311 legitimate definitions.)
    text = (
        "Pintaverkolla ja pintaverkkopyydyksellä tarkoitetaan ankkuroitua "
        "verkkoa."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(b.term == "Pintaverkolla ja pintaverkkopyydyksellä" for b in tk)


def test_adessive_noun_head_avulla_still_binds() -> None:
    # "avulla" / "perusteella" are adessive in form but here they are the noun
    # head of a genuine defined term ("Henkilökohtaisella avulla tarkoitetaan …"),
    # NOT a postposition. Such a head must NOT be rejected. (1987/380.)
    text = "Henkilökohtaisella avulla tarkoitetaan vaikeavammaisen henkilön avustamista."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(b.term == "Henkilökohtaisella avulla" for b in tk)


def test_inline_cross_reference_participle_is_not_a_definition() -> None:
    # Inline shape-3: "N §:ssä tarkoitetulla tavalla tarkoitetaan" must not bind
    # — the head before the verb is the reference participle, not a definiendum.
    text = "Edellä 5 ssä tarkoitetulla tavalla tarkoitetaan jotain muuta."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert not any("tarkoitetulla" in t for t in terms)


def test_clean_multiword_definiendum_still_binds() -> None:
    # The fix must keep clean multi-word definienda (content-word start, noun
    # head, no clause-boundary token). (2023/371 legitimate definition.)
    text = (
        "Tässä laissa tarkoitetaan: "
        "Palkkatuella katettavilla palkkakustannuksilla työntekijälle "
        "maksettavaa palkkaa;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(
        b.term == "Palkkatuella katettavilla palkkakustannuksilla" for b in tk
    )


def test_jaljempana_alias_markup_is_stripped() -> None:
    # Inline markup "<i>rakennetukilaki</i>" must be stripped to the bare word.
    text = (
        "Maaseudun rakennetukilaissa (1476/2007, jäljempänä "
        "<i>rakennetukilaki</i>) säädetään."
    )
    jal = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "rakennetukilaki"
    assert "<" not in jal[0].term
    # FI ids canonicalize to YEAR/NUMBER (the visible cite is "(1476/2007)").
    assert jal[0].target_ref == "2007/1476"


# ---------------------------------------------------------------------------
# Definiendum-boundary precision (sentence boundary / coordinated head /
# leading adverbial / prior-entry connector)
# ---------------------------------------------------------------------------


def test_jaljempana_alias_stops_at_sentence_boundary() -> None:
    # An unquoted alias is a single naming token; it must terminate at the
    # sentence-ending period and never swallow the following sentence's first
    # word. (Observed: "…laissa (1137/2016), jäljempänä markkinavalvontalaki.
    # Markkinavalvonnan …".)
    text = (
        "annetussa laissa (1137/2016), jäljempänä markkinavalvontalaki. "
        "Markkinavalvonnan, talouden toimijoiden tehtävät."
    )
    jal = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert len(jal) == 1
    assert jal[0].term == "markkinavalvontalaki"
    assert "." not in jal[0].term


def test_dangling_hyphen_compound_head_is_kept_in_coordination() -> None:
    # An elliptical coordination "Lammas- ja vuohirekisterillä" (= lammasrekisteri
    # + vuohirekisteri) must keep the clipped compound prefix "Lammas-"; the head
    # must not be clipped to "ja vuohirekisterillä". (2010/233.)
    text = "Lammas- ja vuohirekisterillä tarkoitetaan lammas- ja vuohieläinten rekisteriä."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(b.term == "Lammas- ja vuohirekisterillä" for b in tk)


def test_leading_adverbial_clause_is_trimmed_from_definiendum() -> None:
    # A leading adverbial clause ("… huomioon ottaen") modifies the definition
    # sentence, not the definiendum NP; the term is the adessive head alone.
    # (2010/233: "… rajausmahdollisuus huomioon ottaen kasvulohkolla tarkoitetaan
    # …" → "kasvulohkolla".)
    text = (
        "viljelylohkon rajausmahdollisuus huomioon ottaen kasvulohkolla "
        "tarkoitetaan yhtenäistä viljelyaluetta."
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(b.term == "kasvulohkolla" for b in tk)
    assert not any("ottaen" in b.term for b in tk)


def test_leading_prior_entry_connector_is_stripped() -> None:
    # In an enumerated block a prior item's trailing "sekä" must not be prepended
    # to the next item's definiendum ("…komissiota; sekä [\n] tavaralla …" →
    # "tavaralla", not "sekä tavaralla"). (1997/231 shape.)
    text = (
        "Tässä laissa tarkoitetaan: "
        "jollakin Euroopan yhteisön komissiota; sekä "
        "tavaralla sukusoluja, alkioita ja siitoseläimiä;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    assert any(b.term == "tavaralla" for b in tk)
    assert not any(b.term.startswith("sekä") for b in tk)


# ---------------------------------------------------------------------------
# Enumerated-block structural list-end terminator (F1 precision)
# ---------------------------------------------------------------------------
#
# A definitions block is a CONTIGUOUS run of ';'-items under its header. It has no
# explicit closing marker, so a fixed byte window swept in UNRELATED operative
# ';'-lists later in the same section (an operative "… on esitettävä:\n<item>;"
# list whose items open with an adessive-headed phrase). The block must END at the
# first operative-prose provision sentence: a later operative list is not minted.


def test_operative_list_after_block_is_not_minted_as_definition() -> None:
    # The genuine block has two items, then an operative provision sentence, then an
    # operative ':'-list whose items carry adessive heads. The operative-list items
    # must NOT be minted as definitions (the F1 over-capture). Mirrors 2014/527:
    # "Tässä luvussa tarkoitetaan: … . <operative sentence>. Perustilaselvityksessä
    # on esitettävä:\n tiedot … selvityksen laatimishetkellä …;".
    text = (
        "Tässä luvussa tarkoitetaan: "
        "vertailuasiakirjalla teollisuuden päästöistä annettua asiakirjaa; "
        "päästötasoilla päästöjen vaihteluväliä tietyissä vertailuolosuhteissa.\n"
        "Sen lisäksi, mitä 6 luvussa säädetään, direktiivilaitoksen "
        "ympäristölupa-asiaa käsiteltäessä sovelletaan tätä lukua.\n"
        "Perustilaselvityksessä on esitettävä:\n"
        "tiedot toiminnan sijaintipaikan käytöstä selvityksen laatimishetkellä "
        "ja sitä aikaisemmin;\n"
        "riittävät tiedot mittauksista, jotka kuvastavat maaperän tilaa;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    # Genuine definitions are captured.
    assert "vertailuasiakirjalla" in terms
    assert "päästötasoilla" in terms
    # The operative-list items are NOT minted as phantom definitions.
    assert not any("selvityksen laatimishetkellä" in t for t in terms)
    assert not any("toiminnan sijaintipaikan" in t for t in terms)
    assert not any("mittauksista" in t for t in terms)


def test_full_definition_list_still_captured_when_no_operative_prose() -> None:
    # A genuine long ';'-item definition list with NO intervening operative prose
    # must be captured in FULL — the terminator never truncates a real list.
    items = [
        "ilma-aluksella laitetta, jota voidaan kannatella ilmakehässä",
        "kelpuutuksella ilma-aluksen ohjaamiseen oikeuttavaa merkintää",
        "huollolla ilma-aluksen kunnossapitoa",
        "lentopaikalla aluetta lentoonlähtöä ja laskua varten",
        "lentoasemalla lentopaikkaa miehitettyine rakennuksineen",
        "asematasolla aluetta matkustajien lastaamista varten",
    ]
    text = "Tässä laissa tarkoitetaan: " + "; ".join(items) + "."
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    for expected in (
        "ilma-aluksella",
        "kelpuutuksella",
        "huollolla",
        "lentopaikalla",
        "lentoasemalla",
        "asematasolla",
    ):
        assert expected in terms, f"genuine definiendum {expected!r} dropped"


def test_definiens_with_embedded_sublist_does_not_end_the_block() -> None:
    # A genuine definition item whose definiens contains an embedded
    # colon-introduced sub-list (formula parts each ending in ';') stays within the
    # definitions block; the FOLLOWING genuine definiendum must still bind — the
    # embedded sub-list (short ';'-parts, never a free-standing operative provision
    # sentence) does not trip the operative-prose terminator. Mirrors 1988/161
    # "efektiivisellä kilogrammalla … kertomalla tämä:\n plutoniumin … luvulla 1;\n
    # …;\n alkuperämaarajoituksella …".
    text = (
        "Tässä laissa tarkoitetaan: "
        "köyhdytetyllä uraanilla uraania, jonka rikastusaste on pieni;\n"
        "efektiivisellä kilogrammalla mittayksikköä, joka saadaan kertomalla "
        "tämä:\n"
        "plutoniumin osalta luvulla 1;\n"
        "uraanin osalta rikastusasteen neliöllä;\n"
        "alkuperämaarajoituksella sellaista rajoitusta, joka koskee alkuperää;"
    )
    tk = _by_kind(recognize_defined_term_bindings(text), BINDING_TARKOITETAAN)
    terms = {b.term for b in tk}
    assert "köyhdytetyllä uraanilla" in terms
    assert "efektiivisellä kilogrammalla" in terms
    # The genuine definiendum AFTER the embedded sub-list is still captured (the
    # ';'-terminated sub-list parts do not end the block).
    assert "alkuperämaarajoituksella" in terms


# ---------------------------------------------------------------------------
# jäljempänä verb-idiom false-mint guard (F2 precision)
# ---------------------------------------------------------------------------


def test_jaljempana_verb_idiom_is_not_minted_as_alias() -> None:
    # "…, jollei jäljempänä toisin säädetä" is the ADVERBIAL idiom ("as hereinafter
    # otherwise provided"), NOT an alias. An act cite in the 90-char look-back
    # ("(688/2001) säädetään, jollei jäljempänä toisin säädetä") would otherwise
    # satisfy the act-cite guard and bind the verb phrase "toisin säädetä" to the
    # unrelated act (2002/1021 false mint). It must NOT bind.
    text = (
        "Asiasta säädetään valtioneuvoston asetuksella (688/2001) "
        "säädetään, jollei jäljempänä toisin säädetä."
    )
    ja = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert not any("säädetä" in b.term for b in ja)
    assert not any(b.term == "toisin säädetä" for b in ja)


def test_jaljempana_saadetaan_clause_is_not_minted_as_alias() -> None:
    # The bare adverbial "jäljempänä säädetään" / "jäljempänä tässä luvussa
    # säädetään" clause is referential, never an alias, even with a cite in the
    # look-back window.
    text = "Tästä asetuksesta (527/2014) poiketen jäljempänä tässä luvussa säädetään."
    ja = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert not any("säädetään" in b.term for b in ja)


def test_jaljempana_genuine_multiword_noun_alias_still_binds() -> None:
    # A genuine multi-word NOUN-PHRASE alias (head is a noun) must still bind — the
    # verb-idiom guard rejects only adverb/verb clause fragments, never an NP.
    text = (
        "Euroopan parlamentin ja neuvoston asetuksessa (EU) 2016/679 "
        "(jäljempänä yleinen tietosuoja-asetus) säädetään tarkemmin."
    )
    ja = _by_kind(recognize_defined_term_bindings(text), BINDING_JALJEMPANA)
    assert any(b.term == "yleinen tietosuoja-asetus" for b in ja)

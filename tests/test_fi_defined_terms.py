"""Tests for the Finnish defined-term / alias binding recognizer.

Gates the three CONSERVATIVE binding shapes, the complex-NP morphology refusal,
and the NEGATIVE no-fabrication discipline.
"""
from __future__ import annotations

from lawvm.finland.references.defined_terms import (
    BINDING_JALJEMPANA,
    BINDING_PARENTHETICAL_ALIAS,
    BINDING_TARKOITETAAN,
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
    assert b.status == STATUS_OK
    assert b.source_span.source_file == "he.xml"
    assert b.source_span.byte_len > 0


def test_parenthetical_alias_binds_to_finnish_act() -> None:
    text = "Ympäristönsuojelulaissa (527/2014) (ympäristönsuojelulaki) säädetään."
    bindings = recognize_defined_term_bindings(text)
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    assert alias[0].term == "ympäristönsuojelulaki"
    assert alias[0].target_ref == "527/2014"
    assert alias[0].status == STATUS_OK


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
    assert jal[0].target_ref == "527/2014"
    assert jal[0].status == STATUS_OK


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


# ---------------------------------------------------------------------------
# Shape 3: X tarkoitetaan Y
# ---------------------------------------------------------------------------


def test_tarkoitetaan_binds_term_to_expansion() -> None:
    text = "Sivutuotteella tarkoitetaan kuollutta eläintä tai sen osaa."
    bindings = recognize_defined_term_bindings(text)
    tk = _by_kind(bindings, BINDING_TARKOITETAAN)
    assert len(tk) == 1
    # Conservative adessive -lla stripping only (no consonant-gradation reversal):
    # "Sivutuotteella" -> "Sivutuottee" (citation stem, not nominative).
    assert tk[0].term == "Sivutuottee"
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
    assert tk[0].term == "Sivutuoteasetukse"  # adessive -lla stripped, no gradation
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
    assert alias[0].status == STATUS_UNSUPPORTED_MORPHOLOGY
    # Target is still known — the binding is accounted for, not silently dropped.
    assert alias[0].target_ref == "1069/2009"


def test_final_head_compound_is_supported() -> None:
    # An invariant modifier + inflectable head (no case marker on modifier) is a
    # final-head compound → supported.
    text = "asetuksessa (EY) N:o 1069/2009 (uusi sivutuoteasetus) tarkoitettu."
    bindings = recognize_defined_term_bindings(text)
    alias = _by_kind(bindings, BINDING_PARENTHETICAL_ALIAS)
    assert len(alias) == 1
    assert alias[0].status == STATUS_OK
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

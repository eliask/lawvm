"""Tests for the shared M1-derived morphology gate + negative collision paradigms.

The gate (:mod:`lawvm.finland.references.lemma_gate`) replaces the four
hand-written false-positive suffix regexes that ``by_name.py`` used to carry. It
inverts the M1 morphology engine over the closed statute heads PLUS the closed
non-statute collision paradigms (:mod:`lawvm.finland.morphology.negative`).

These tests pin BOTH directions:
  * every false-positive family the by-name recognizer must reject is rejected
    by the gate (REJECT_KNOWN_OTHER) — the contract by_name relies on;
  * every genuine statute reference is NOT rejected (UNKNOWN = honest-unknown,
    emit as before / ACCEPT_HEAD for a bare head);
  * the new negative paradigms ``analyze`` to the expected non-statute lemma.
"""

from __future__ import annotations

import pytest

from lawvm.finland.morphology.negative import negative_paradigms
from lawvm.finland.references.lemma_gate import GateVerdict, lemma_gate


# --------------------------------------------------------------------------- #
# REJECT side — the preserved false-positive families (the by_name contract).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "token",
    [
        # -lainen / -nainen adjective partitive (collides with laki elative).
        "sellaista",
        "veronalaista",
        "työnalaista",
        "valvonnanalaista",
        "samanlaista",
        "tällaista",
        "tuollaista",
        "erilaista",
        "monenlaista",
        "vastaavanlaista",
        "alaista",
        # productive -nlainen genitive-modifier family (open modifier).
        "muunlaista",
        "toisenlaista",
        "uudenlaista",
        "tietynlaista",
        "seuraavanlaista",
        "määrätynlaista",
        "tarkoitetunlaista",
        "minkäänlaista",
        "yhdenlaista",
        "minkälaista",
        # jokin pronoun joll-/joill- obliques.
        "jollain",
        "joillain",
        "jollaiksi",
        "jollailla",
        "jollaille",
        "jollailta",
        "jollaissa",
        "joillaiksi",
        "joillailla",
        "joillaille",
        "joillailta",
        "joillaissa",
        # -las / -läs agent-noun plural obliques.
        "oppilaille",
        "sotilailta",
        "kokelaiksi",
        "oppilailta",
        "oppilain",
        "sotilailla",
        "oppilaissa",
        "oppilaista",
        "rintamasotilaille",
    ],
)
def test_gate_rejects_non_statute_paradigm(token: str) -> None:
    """Every FP-family token is REJECTED by paradigm inversion (no regex)."""
    assert lemma_gate(token).verdict is GateVerdict.REJECT_KNOWN_OTHER


@pytest.mark.parametrize(
    ("token", "modifier"),
    [
        ("tämänlain", "tämän"),
        ("tässälaissa", "tässä"),
        ("tästälaista", "tästä"),
        ("mainitunlain", "mainitun"),
        ("sellaisenlain", "sellaisen"),
        ("kunkinlain", "kunkin"),
        ("samanlaissa", "saman"),
        ("kyseisenlain", "kyseisen"),
        ("eräänlain", "erään"),
    ],
)
def test_gate_rejects_determiner_laki_collapse(token: str, modifier: str) -> None:
    """A peeled modifier that is a complete determiner inflection -> REJECT."""
    d = lemma_gate(token, peeled_modifier=modifier)
    assert d.verdict is GateVerdict.REJECT_KNOWN_OTHER
    assert d.reason == "determiner_collapse"


# --------------------------------------------------------------------------- #
# UNKNOWN / ACCEPT side — genuine references must survive.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "token",
    [
        # genuine inflected compound titles
        "luonnonsuojelulaissa",
        "ympäristönsuojelulain",
        "kuntalain",
        "rakennuslain",
        # genuine -lai- laws (adessive / translative) that share the surface
        # fragment but carry a real statute modifier — must NOT be swept.
        "työeläkelailla",
        "elintarvelaiksi",
        "maakuntalailla",
        "eläkelailla",
        # the CRITICAL bare laki elative Xlaista (NOT a -lainen adjective):
        # only the bare 6-char `laista` is a suffix, never strictly longer.
        "verolaista",
        "rikoslaista",
        "kielilaista",
        "perustuslaista",
        "jätelaista",
    ],
)
def test_gate_does_not_reject_real_reference(token: str) -> None:
    """A genuine reference returns UNKNOWN (honest-unknown), never REJECT."""
    assert lemma_gate(token).verdict is GateVerdict.UNKNOWN


@pytest.mark.parametrize(
    ("token", "lemma"),
    [
        ("laissa", "laki"),
        ("lain", "laki"),
        ("laista", "laki"),
        ("laiksi", "laki"),
        ("asetuksen", "asetus"),
        ("asetuksessa", "asetus"),
    ],
)
def test_gate_accepts_bare_statute_head(token: str, lemma: str) -> None:
    """A bare statute-head inflection is ACCEPT_HEAD with the head lemma."""
    d = lemma_gate(token)
    assert d.verdict is GateVerdict.ACCEPT_HEAD
    assert d.lemma == lemma


# --------------------------------------------------------------------------- #
# Negative paradigm engine — analyze the new negative lemmas to expectation.
# --------------------------------------------------------------------------- #


def test_negative_paradigm_jokin_obliques_indexed() -> None:
    """The jokin obliques resolve to the jokin lemma via the negative index."""
    np = negative_paradigms()
    for tok in ("jollain", "joillain", "jollaiksi", "joillaille"):
        hit = np.longest_suffix_match(tok)
        assert hit is not None and hit.lemma == "jokin", tok


def test_negative_paradigm_agent_nouns_indexed() -> None:
    """oppilas / sotilas / kokelas plural obliques resolve to the agent noun."""
    np = negative_paradigms()
    assert np.longest_suffix_match("oppilaille").lemma == "oppilas"  # type: ignore[union-attr]
    assert np.longest_suffix_match("rintamasotilaille").lemma == "sotilas"  # type: ignore[union-attr]
    assert np.longest_suffix_match("kokelaiksi").lemma == "kokelas"  # type: ignore[union-attr]


def test_negative_paradigm_alainen_generated_via_m1() -> None:
    """The productive -alainen partitive is generated (alaista), not stored."""
    np = negative_paradigms()
    hit = np.longest_suffix_match("veronalaista")
    assert hit is not None and hit.lemma == "alainen"
    assert hit.surface == "alaista"


def test_negative_paradigm_bare_laki_elative_not_shadowed() -> None:
    """A bare laki elative (Xlaista) is NOT shadowed by the -lainen partitive.

    The negative `laista` surface is exactly as long as the bare laki oblique it
    shadows, so the strictly-longer rule leaves the real elative alone.
    """
    np = negative_paradigms()
    assert np.longest_suffix_match("verolaista") is None
    assert np.longest_suffix_match("rikoslaista") is None


def test_negative_paradigm_determiner_modifiers() -> None:
    np = negative_paradigms()
    assert np.is_determiner_modifier("tämän")
    assert np.is_determiner_modifier("Mainitun")  # case-folded
    assert not np.is_determiner_modifier("luonnonsuojelu")


def test_negative_paradigm_non_statute_sopimus_whole_compound() -> None:
    """A closed non-statute ``-sopimus`` lemma's whole-compound surface rejects.

    ``kapitalisaatiosopimus`` is a contract/product common noun riding the
    ``sopimus`` head, not a named act. Each of its oblique surfaces is a
    whole-compound negative entry (no shorter shadowed head), so the gate rejects
    it to the ``kapitalisaatiosopimus`` lemma.
    """
    np = negative_paradigms()
    for tok in (
        "kapitalisaatiosopimuksen",
        "kapitalisaatiosopimuksessa",
        "kapitalisaatiosopimuksesta",
        "kapitalisaatiosopimukselle",
    ):
        hit = np.longest_suffix_match(tok)
        assert hit is not None and hit.lemma == "kapitalisaatiosopimus", tok
        assert hit.shadows == ""
        assert lemma_gate(tok).verdict is GateVerdict.REJECT_KNOWN_OTHER


def test_negative_paradigm_genuine_sopimuslaki_not_shadowed() -> None:
    """A genuine ``...sopimuslaki`` act (``laki`` head) is never shadowed.

    The named act carries the ``laki`` head (``vakuutussopimuslain`` /
    ``työsopimuslaissa``); the rejected product noun carries the ``sopimus`` head
    (``kapitalisaatiosopimuksen``). The two never share a tail, so the
    non-statute ``-sopimus`` family cannot match a real act reference.
    """
    np = negative_paradigms()
    for tok in (
        "vakuutussopimuslain",
        "vakuutussopimuslaissa",
        "työsopimuslaissa",
        "maakaaressa",
    ):
        assert np.longest_suffix_match(tok) is None, tok
        assert lemma_gate(tok).verdict is GateVerdict.UNKNOWN


# --------------------------------------------------------------------------- #
# M1-backed recognizer alternations (chapter head + name-head exclusion).
# --------------------------------------------------------------------------- #


def test_chapter_head_alternation_is_m1_backed_table() -> None:
    """The shared ``luku`` chapter-head alternation equals the old hand table.

    Replaces the verbatim ``(?:luvun|luvussa|...|luku)`` duplicated across the
    internal-ref and body-tail lanes with one M1-generated set (paradigm
    inversion, gradation-correct: ``luku`` -> ``luvu-``). Strict-equal to the old
    table — no recall change, only the rule-of-three duplication retired.
    """
    from lawvm.finland.references.lemma_gate import chapter_head_alternation

    got = set(chapter_head_alternation().split("|"))
    hand = {"luvun", "luvussa", "luvusta", "lukuun", "luvut", "luvuissa", "luku"}
    assert got == hand


def test_definitions_header_unit_alternation_is_m1_backed_table() -> None:
    """The definitions-block header scope-unit set equals the old hand table.

    Replaces the verbatim
    ``laissa|luvussa|pykälässä|momentissa|asetuksessa|päätöksessä`` duplicated
    across ``defined_terms._SCOPE_CUE_TASSA`` and ``_ENUM_HEADER`` with one
    M1-generated INE-SG set (paradigm inversion, gradation-correct: ``päätös`` ->
    ``päätökse-`` -> ``päätöksessä``). Strict-equal to the old table — no recall
    change, only the rule-of-three duplication retired and the gradation substring
    bug class killed.
    """
    from lawvm.finland.references.lemma_gate import (
        definitions_header_unit_alternation,
        definitions_header_unit_scope_map,
    )

    got = set(definitions_header_unit_alternation().split("|"))
    hand = {"laissa", "luvussa", "pykälässä", "momentissa", "asetuksessa", "päätöksessä"}
    assert got == hand
    assert definitions_header_unit_scope_map() == {
        "laissa": "statute",
        "luvussa": "chapter",
        "pykälässä": "section",
        "momentissa": "subsection",
        "asetuksessa": "statute",
        "päätöksessä": "statute",
    }


def test_definitions_header_unit_alternation_longest_first() -> None:
    """Alternation is longest-first so a regex prefers the most-specific surface."""
    from lawvm.finland.references.lemma_gate import definitions_header_unit_alternation

    surfaces = definitions_header_unit_alternation().split("|")
    lengths = [len(s) for s in surfaces]
    assert lengths == sorted(lengths, reverse=True)


def test_chapter_head_shared_across_lanes() -> None:
    """internal_refs and sections build the chapter head from the SAME M1 source."""
    import lawvm.finland.references.internal_refs as ir
    import lawvm.finland.references.sections as sec

    assert ir._CHAPTER_HEAD == sec._CHAPTER_TAIL_HEAD


def test_name_suffix_exclusion_is_m1_superset_of_hand_table() -> None:
    """The M1-backed name-head exclusion alternation reproduces the old table.

    Every form the hand ``_NAME_SUFFIX`` recognized is generated (gradation-
    correct: ``muodon`` / ``järjestyksen`` / ``kaaren``); the only difference is
    the dropped wrong-partitive ``kaartta`` (the real ``kaarta`` is kept), which
    is not a genuine recognition. No new form is added (full-paradigm widening
    would EXCLUDE genuine internal refs — a recall regression — so it is avoided).
    """
    import lawvm.finland.references.internal_refs as ir

    got = set(ir._NAME_SUFFIX[3:-1].split("|"))  # strip the (?: ... )
    hand = set(
        "lain lakia laissa laista laiksi laille lailla lailta laki "
        "asetuksen asetusta asetuksessa asetuksesta asetukseksi asetuksella "
        "asetukselle asetukselta asetus "
        "järjestyksen järjestystä järjestyksessä järjestyksestä järjestys "
        "muodon muotoa muodossa muodosta muoto "
        "kaaren kaaressa kaaresta kaareen kaarella kaarelta kaarelle kaareksi "
        "kaarena kaarin kaarta".split()
    )
    assert got == hand, (got - hand, hand - got)
    # the bare nominative ``kaari`` is deliberately NOT an exclusion trigger
    assert "kaari" not in got

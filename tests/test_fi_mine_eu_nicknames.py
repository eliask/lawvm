"""Unit tests for the EU-nickname mining tool.

Tests the CELEX conversion, EU-binding filter, aggregation and seed cross-check
logic with SYNTHETIC bindings (no corpus access). A single real-corpus smoke is
guarded to skip when the farchive is absent.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.defined_terms import (
    BINDING_PARENTHETICAL_ALIAS,
    STATUS_OK,
    DefinedTermBinding,
)
from lawvm.tools import mine_eu_nicknames as m


def _binding(term: str, target_ref: str | None) -> DefinedTermBinding:
    return DefinedTermBinding(
        term=term,
        target_ref=target_ref,
        expansion=None,
        scope="statute",
        source_span=SourceSpan("x", 0, len(term)),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=STATUS_OK,
    )


# --- CELEX type from head -------------------------------------------------


def test_celex_type_regulation_head():
    assert m.celex_type_for_term("sivutuoteasetus") == "R"
    assert m.celex_type_for_term("Sivutuoteasetus") == "R"  # case-insensitive


def test_celex_type_directive_head():
    assert m.celex_type_for_term("teollisuuspäästödirektiivi") == "L"


def test_celex_type_no_head_is_none():
    # A Finnish-law alias (head "laki") carries no EU-instrument head.
    assert m.celex_type_for_term("ympäristönsuojelulaki") is None
    assert m.celex_type_for_term("") is None


# --- year/number parse (both orders) -------------------------------------


def test_parse_number_first():
    # old N:o form 1069/2009 -> (year, number)
    assert m._parse_year_number("1069/2009") == ("2009", "1069")


def test_parse_year_first():
    # GDPR-style 2016/679 -> (year, number)
    assert m._parse_year_number("2016/679") == ("2016", "679")


def test_parse_rejects_malformed():
    assert m._parse_year_number("foo") is None
    assert m._parse_year_number("12/34/56") is None
    assert m._parse_year_number("12/34") is None  # no 4-digit year component
    assert m._parse_year_number("abc/2009") is None


# --- target_ref -> CELEX (the seed examples) ------------------------------


def test_convert_sivutuoteasetus():
    conv = m.target_ref_to_celex("1069/2009", "sivutuoteasetus")
    assert conv.celex == "32009R1069"
    assert conv.reason is None


def test_convert_gdpr_year_first():
    conv = m.target_ref_to_celex("2016/679", "yleinen tietosuoja-asetus")
    # 679 zero-padded to 0679
    assert conv.celex == "32016R0679"


def test_convert_directive():
    conv = m.target_ref_to_celex("2010/75", "teollisuuspäästödirektiivi")
    assert conv.celex == "32010L0075"


def test_convert_reach_padding():
    conv = m.target_ref_to_celex("1907/2006", "reach-asetus")
    assert conv.celex == "32006R1907"


def test_convert_unconvertible_no_head_failloud():
    conv = m.target_ref_to_celex("527/2014", "ympäristönsuojelulaki")
    assert conv.celex is None
    assert conv.reason is not None
    # self-evidencing: the offending term is embedded in the reason
    assert "ympäristönsuojelulaki" in conv.reason


def test_convert_unconvertible_bad_id_failloud():
    conv = m.target_ref_to_celex("garbage", "sivutuoteasetus")
    assert conv.celex is None
    assert conv.reason is not None
    assert "garbage" in conv.reason


# --- EU-binding filter ----------------------------------------------------


def test_eu_bindings_filter():
    bindings = [
        _binding("sivutuoteasetus", "1069/2009"),  # EU regulation -> kept
        _binding("teollisuuspäästödirektiivi", "2010/75"),  # EU directive -> kept
        _binding("ympäristönsuojelulaki", "527/2014"),  # FI law -> dropped (no head)
        _binding("sivutuoteasetus", None),  # no target -> dropped
    ]
    eu = m._eu_bindings(bindings)
    assert {b.term for b in eu} == {"sivutuoteasetus", "teollisuuspäästödirektiivi"}


# --- aggregation ----------------------------------------------------------


def test_mine_aggregates_distinct_statute_support():
    per_statute = [
        ("100/2001", [_binding("sivutuoteasetus", "1069/2009")]),
        ("200/2002", [_binding("Sivutuoteasetus", "1069/2009")]),  # diff case, same lemma
        ("300/2003", [_binding("sivutuoteasetus", "1069/2009")]),
        ("400/2004", [_binding("biosidiasetus", "528/2012")]),
    ]
    result = m.mine_bindings(per_statute)
    assert result.statutes_with_eu_bindings == 4
    assert result.total_eu_bindings == 4
    stat = result.stats["sivutuoteasetus"]
    # one CELEX, supported by 3 distinct statutes
    assert stat.celex_support == {"32009R1069": {"100/2001", "200/2002", "300/2003"}}
    assert result.stats["biosidiasetus"].celex_support == {"32012R0528": {"400/2004"}}


def test_normalize_strips_eu_scope_qualifier():
    # A coined nickname bound WITH a leading EU-scope qualifier ("EU:n …") must be
    # keyed on the BARE term-of-art, so a later bare ``<nickname> N artikla`` use
    # hits the seeded key. The instrument identity (CELEX) is unchanged.
    assert (
        m.normalize_nickname_lemma("EU:n kryptovaramarkkina-asetus")
        == "kryptovaramarkkina-asetus"
    )
    assert (
        m.normalize_nickname_lemma("Euroopan unionin DORA-asetus") == "dora-asetus"
    )
    # No qualifier — lowercased/trimmed only.
    assert m.normalize_nickname_lemma("ESAP-asetus") == "esap-asetus"


def test_mine_keys_on_qualifier_stripped_lemma():
    # "EU:n kryptovaramarkkina-asetus" bound across statutes aggregates under the
    # bare lemma so the support accrues to the registry-usable key.
    per_statute = [
        ("100/2001", [_binding("EU:n kryptovaramarkkina-asetus", "2023/1114")]),
        ("200/2002", [_binding("kryptovaramarkkina-asetus", "2023/1114")]),
    ]
    result = m.mine_bindings(per_statute)
    stat = result.stats["kryptovaramarkkina-asetus"]
    assert stat.celex_support == {"32023R1114": {"100/2001", "200/2002"}}


def test_mine_same_statute_counted_once_per_celex():
    # Two bindings of the same nickname->CELEX in ONE statute = support 1.
    per_statute = [
        ("100/2001", [
            _binding("sivutuoteasetus", "1069/2009"),
            _binding("sivutuoteasetus", "1069/2009"),
        ]),
    ]
    result = m.mine_bindings(per_statute)
    assert len(result.stats["sivutuoteasetus"].celex_support["32009R1069"]) == 1


def test_mine_records_unconvertible_not_dropped():
    # An -asetus head with a malformed id is recorded as unconvertible, not lost.
    per_statute = [("100/2001", [_binding("kummasetus", "not-an-id")])]
    result = m.mine_bindings(per_statute)
    stat = result.stats["kummasetus"]
    assert stat.celex_support == {}
    assert len(stat.unconvertible) == 1
    sid, ref, reason = stat.unconvertible[0]
    assert sid == "100/2001" and ref == "not-an-id"


def test_mine_ambiguous_nickname_keeps_both_celex():
    # Same nickname bound to TWO distinct CELEX across statutes = ambiguous.
    per_statute = [
        ("100/2001", [_binding("jätedirektiivi", "2008/98")]),
        ("200/2002", [_binding("jätedirektiivi", "2006/12")]),
    ]
    result = m.mine_bindings(per_statute)
    assert set(result.stats["jätedirektiivi"].celex_support) == {
        "32008L0098",
        "32006L0012",
    }


# --- classification against the seed --------------------------------------


def test_classify_new_single_meets_support():
    per_statute = [
        (f"{i}/2001", [_binding("kuvitteellinenasetus", "1234/2020")])
        for i in range(1, 5)  # 4 distinct statutes
    ]
    result = m.mine_bindings(per_statute)
    cands = m.classify_candidates(result, min_support=3)
    c = next(c for c in cands if c.lemma == "kuvitteellinenasetus")
    assert c.classification == m.CLASS_NEW_SINGLE
    assert c.statute_support == 4
    assert c.mined_celex == (("32020R1234", 4),)
    assert c.seed_celex == ()


def test_classify_below_support():
    per_statute = [("1/2001", [_binding("kuvitteellinenasetus", "1234/2020")])]
    result = m.mine_bindings(per_statute)
    cands = m.classify_candidates(result, min_support=3)
    c = next(c for c in cands if c.lemma == "kuvitteellinenasetus")
    assert c.classification == m.CLASS_BELOW_SUPPORT


def test_classify_already_seeded():
    # sivutuoteasetus is in the production seed -> already_seeded regardless of support.
    per_statute = [
        (f"{i}/2001", [_binding("sivutuoteasetus", "1069/2009")]) for i in range(1, 6)
    ]
    result = m.mine_bindings(per_statute)
    cands = m.classify_candidates(result, min_support=3)
    c = next(c for c in cands if c.lemma == "sivutuoteasetus")
    assert c.classification == m.CLASS_ALREADY_SEEDED
    assert c.seed_celex == ("32009R1069",)


def test_classify_new_ambiguous_never_collapses():
    per_statute = [
        ("1/2001", [_binding("kuvitteellinendirektiivi", "2008/98")]),
        ("2/2002", [_binding("kuvitteellinendirektiivi", "2006/12")]),
    ]
    result = m.mine_bindings(per_statute)
    cands = m.classify_candidates(result, min_support=1)
    c = next(c for c in cands if c.lemma == "kuvitteellinendirektiivi")
    assert c.classification == m.CLASS_NEW_AMBIGUOUS
    assert len(c.mined_celex) == 2


def test_render_report_runs():
    per_statute = [
        (f"{i}/2001", [_binding("kuvitteellinenasetus", "1234/2020")]) for i in range(1, 5)
    ]
    result = m.mine_bindings(per_statute)
    result.statutes_scanned = 4
    cands = m.classify_candidates(result, min_support=3)
    text = m.render_report(result, cands)
    assert "EU-nickname mining candidate report" in text
    assert "kuvitteellinenasetus" in text
    # JSON shape is serializable
    payload = m._candidates_to_json(result, cands)
    assert payload["candidates"][0]["lemma"] == "kuvitteellinenasetus"


# --- real-corpus smoke (skipped when archive absent) ----------------------


def _archive_present() -> bool:
    return os.path.exists(m._archive_path()) and bool(
        os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    )


@pytest.mark.skipif(not _archive_present(), reason="farchive not available")
def test_scan_corpus_smoke():
    result = m.scan_corpus(sample=50)
    assert result.statutes_scanned <= 50
    cands = m.classify_candidates(result, min_support=2)
    # Should not raise; candidates are well-formed.
    for c in cands:
        assert c.classification in {
            m.CLASS_NEW_SINGLE,
            m.CLASS_NEW_AMBIGUOUS,
            m.CLASS_ALREADY_SEEDED,
            m.CLASS_BELOW_SUPPORT,
        }

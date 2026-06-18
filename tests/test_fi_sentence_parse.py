"""Unit tests for the citation-bearing-sentence construction parse (Pilot A).

Covers the SentenceParse-lite IR, total-token-ownership (no silent drop), the
projection to ReferenceMention, and the differential-census classification — on
hand-built witness sentences in the coordinate space the SegmentationGraph
produces (the decoded body text). Does NOT touch the corpus.
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.legal_surface.sentence_census import _classify, _miss_shape
from lawvm.finland.legal_surface.sentence_parse import (
    SENTENCE_LANE_CONSTRUCTION_OWNED,
    SENTENCE_LANE_DECLINED,
    assert_total_ownership,
    oracle_reference_keys_for_span,
    parse_citation_sentence,
    projection_reference_keys,
    sentence_parse_to_mentions,
)


# --------------------------------------------------------------------------
# Construction parse + total-token-ownership
# --------------------------------------------------------------------------


def test_simple_section_citation_is_owned_and_carries_reference() -> None:
    text = "Tähän sovelletaan, mitä ympäristönsuojelulaissa (527/2014) 5 §:ssä säädetään."
    sp = parse_citation_sentence(text)
    assert sp.kind == "citation_bearing"
    assert sp.parser_lane == SENTENCE_LANE_CONSTRUCTION_OWNED
    assert len(sp.citations) == 1
    c = sp.citations[0]
    assert c.statute_id == "527/2014"
    assert [t.section_label for t in c.targets] == ["5"]
    # declaration cue recognized (säädet... / sovellet... — first occurrence)
    assert sp.declaration_marker in ("sovellet", "säädet")
    # total-token-ownership: no silent drop
    assert_total_ownership(sp)


def test_statute_level_citation_no_section_tail() -> None:
    text = "Asiaan sovelletaan lakia eläinten hyvinvoinnista (693/2023)."
    sp = parse_citation_sentence(text)
    assert sp.kind == "citation_bearing"
    assert len(sp.citations) == 1
    assert sp.citations[0].statute_id == "693/2023"
    # no parsable § tail -> statute-level (empty target list)
    assert sp.citations[0].targets == ()
    assert_total_ownership(sp)


def test_coordinated_sections_expand() -> None:
    # body recognizer expands coordination "6 ja 8 §"
    text = "Kuten laissa (359/1968) 6 ja 8 §:ssä säädetään, asia ratkaistaan."
    sp = parse_citation_sentence(text)
    labels = sorted(t.section_label for t in sp.citations[0].targets)
    assert labels == ["6", "8"]
    assert_total_ownership(sp)


def test_momentti_precision_carried() -> None:
    text = "Edellytyksistä säädetään laissa (731/1999) 104 §:n 2 momentissa."
    sp = parse_citation_sentence(text)
    tgts = sp.citations[0].targets
    assert len(tgts) == 1
    assert tgts[0].section_label == "104"
    assert tgts[0].subsection_num == 2
    assert_total_ownership(sp)


def test_chapter_qualified_citation_carried() -> None:
    text = "Toimivallasta säädetään poliisilaissa (872/2011) 9 luvun 9 b §:ssä."
    sp = parse_citation_sentence(text)
    tgts = sp.citations[0].targets
    assert len(tgts) == 1
    assert tgts[0].chapter == "9"
    assert tgts[0].section_label == "9b"
    assert_total_ownership(sp)


def test_two_digit_year_expands() -> None:
    text = "Säännös vastaa sitä, mitä aiemmin laissa (307/86) 4 §:ssä säädettiin."
    sp = parse_citation_sentence(text)
    assert sp.citations[0].statute_id == "4/1986" or sp.citations[0].statute_id == "307/1986"
    # the id is num/year; num=307, year 86 -> 1986
    assert sp.citations[0].statute_id == "307/1986"
    assert_total_ownership(sp)


def test_multiple_citations_in_one_sentence() -> None:
    text = (
        "Tässä laissa tarkoitetaan, mitä laissa (359/1968) 6 §:ssä ja "
        "laissa (527/2014) 5 §:ssä säädetään."
    )
    sp = parse_citation_sentence(text)
    ids = sorted(c.statute_id for c in sp.citations)
    assert ids == ["359/1968", "527/2014"]
    assert_total_ownership(sp)


def test_non_citation_sentence_declines_as_residue() -> None:
    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    sp = parse_citation_sentence(text)
    assert sp.kind == "declined"
    assert sp.parser_lane == SENTENCE_LANE_DECLINED
    assert len(sp.citations) == 0
    # the whole segment is one explicit residual (no silent drop, no fabrication)
    assert len(sp.residuals) == 1
    assert sp.residuals[0].reason == "not_citation_bearing"
    assert_total_ownership(sp)


def test_total_ownership_holds_on_marker_outside_citation() -> None:
    # declaration cue appears AFTER the citation span; both owned, gap is residual
    text = "Lukuun ottamatta laissa (359/1968) 6 §:ssä mainittuja, sovelletaan tätä."
    sp = parse_citation_sentence(text)
    assert_total_ownership(sp)


# --------------------------------------------------------------------------
# Projection to ReferenceMention
# --------------------------------------------------------------------------


def test_projection_emits_cross_statute_exact_mention() -> None:
    text = "Sovelletaan, mitä laissa (527/2014) 5 §:ssä säädetään."
    sp = parse_citation_sentence(text)
    mentions = sentence_parse_to_mentions(sp, "999/2025")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.EXACT
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "527/2014"
    assert m.target_provision_ref.section_label == "5"
    assert m.source_provision_ref.statute_id == "999/2025"
    assert m.phrase_lemma == "citation_sentence_construction"


def test_projection_statute_only_for_section_less_citation() -> None:
    text = "Sovelletaan lakia (693/2023)."
    sp = parse_citation_sentence(text)
    mentions = sentence_parse_to_mentions(sp, "999/2025")
    assert len(mentions) == 1
    assert mentions[0].cite_confidence is CiteConfidence.STATUTE_ONLY


def test_projection_keys_match_serialized_form() -> None:
    text = "Sovelletaan, mitä laissa (527/2014) 5 §:n 2 momentissa säädetään."
    sp = parse_citation_sentence(text)
    keys = projection_reference_keys(sp, "999/2025")
    assert keys == {"527/2014/5/2"}


# --------------------------------------------------------------------------
# Oracle parity (the production plain-text recognizer over the same span)
# --------------------------------------------------------------------------


def test_oracle_and_projection_agree_on_simple_citation() -> None:
    text = "Sovelletaan, mitä laissa (527/2014) 5 §:ssä säädetään."
    sp = parse_citation_sentence(text)
    proj = projection_reference_keys(sp, "999/2025")
    oracle = oracle_reference_keys_for_span(text)
    assert proj == oracle
    assert _classify(proj, oracle, sp.parser_lane == SENTENCE_LANE_DECLINED) == "match"


def test_oracle_empty_on_non_citation() -> None:
    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    assert oracle_reference_keys_for_span(text) == set()


# --------------------------------------------------------------------------
# Census classification logic
# --------------------------------------------------------------------------


def test_classify_buckets() -> None:
    assert _classify({"a"}, {"a"}, False) == "match"
    assert _classify({"a", "b"}, {"a"}, False) == "superset"
    assert _classify({"a"}, {"a", "b"}, False) == "miss"
    # symmetric difference both ways -> conservative miss
    assert _classify({"a", "x"}, {"a", "b"}, False) == "miss"
    assert _classify(set(), set(), True) == "decline"


def test_miss_shape_classes() -> None:
    assert "kohta" in _miss_shape({"527/2014/5/k3"}, "")
    assert "momentti" in _miss_shape({"527/2014/5/2"}, "säädet")
    assert "chapter" in _miss_shape({"527/2014/ch9/9b"}, "")
    assert "statute_only" in _miss_shape({"527/2014"}, "")
    assert "with_cue" in _miss_shape({"527/2014/5"}, "säädet")
    assert "no_cue" in _miss_shape({"527/2014/5"}, "")

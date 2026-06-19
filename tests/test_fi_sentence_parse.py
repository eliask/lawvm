"""Unit tests for the citation-bearing-sentence construction parse (Pilot A).

Covers the SentenceParse-lite IR, total-token-ownership (no silent drop), the
projection to ReferenceMention, and the differential-census classification — on
hand-built witness sentences in the coordinate space the SegmentationGraph
produces (the decoded body text). Does NOT touch the corpus.
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.legal_surface.bundle import decode_body_text
from lawvm.finland.legal_surface.clause_segment import build_clause_index
from lawvm.finland.legal_surface.sentence_census import _classify, _miss_shape
from lawvm.finland.legal_surface.sentence_parse import (
    SENTENCE_LANE_CONSTRUCTION_OWNED,
    SENTENCE_LANE_DECLINED,
    _bucket_full_extractor_oracle,
    _canonicalize_statute_key,
    _canonicalize_statute_key_in_segment,
    _collapse_redundant_statute_only,
    assert_total_ownership,
    full_oracle_reference_keys,
    oracle_reference_keys_for_span,
    parse_citation_sentence,
    projection_reference_keys,
    sentence_parse_to_mentions,
)
from lawvm.finland.references.ref_mention_extractor import _PLAIN_TEXT_FI_STATUTE_RE


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


def test_two_digit_year_bounded_by_citing_statute() -> None:
    # A cite cannot post-date the statute that makes it: when the citing statute's
    # year is threaded, the 2-digit pivot is bounded by it. A 1950 statute citing
    # ``(1/19)`` means 1919, not 2019.
    text = "painovapauslaissa (1/19) säädetään, että"
    sp = parse_citation_sentence(text, source_statute_id="1950/558")
    assert [c.statute_id for c in sp.citations] == ["1/1919"]
    assert_total_ownership(sp)

    # ``(208/25)`` cited by a 1925 statute is 1925 (the latest non-post-dating
    # reading), not 2025.
    sp2 = parse_citation_sentence("annetun lain (208/25) nojalla", "1925/100")
    assert [c.statute_id for c in sp2.citations] == ["208/1925"]

    # Without a citing year the legacy fixed pivot is preserved (no regression):
    # ``(1/19)`` -> 2019 under the current-year heuristic.
    sp3 = parse_citation_sentence("painovapauslaissa (1/19) säädetään, että")
    assert [c.statute_id for c in sp3.citations] == ["1/2019"]

    # A genuinely 21st-century cite under a 21st-century citing statute is kept:
    # ``(1/04)`` cited by a 2010 statute is 2004.
    sp4 = parse_citation_sentence("laissa (1/04) säädetään, että", "2010/1")
    assert [c.statute_id for c in sp4.citations] == ["1/2004"]

    # A 4-digit year is never touched by the pivot, with or without a citing year.
    sp5 = parse_citation_sentence("laissa (359/1968) säädetään, että", "1950/558")
    assert [c.statute_id for c in sp5.citations] == ["359/1968"]


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
# Full-extractor oracle: whole-statute extraction, bucketed to segments by
# source-span overlap (the flip-gate oracle).
# --------------------------------------------------------------------------


def _akn(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<akomaNtoso><body>{body}</body></akomaNtoso>".encode("utf-8")


def test_full_oracle_buckets_mention_into_its_own_segment() -> None:
    # Two citation paragraphs; each cross-statute citation must bucket into the
    # SEGMENT that actually contains its surface, not the other one.
    xml = _akn(
        "Ensimmainen kappale ilman viittauksia.",
        "Sovelletaan, mita osakeyhtiolaissa (624/2006) 5 §:ssa saadetaan.",
        "Toinen on tassa: kuten valtionavustuslaissa (688/2001) 9 §:ssa.",
    )
    sid = "999/2025"
    body = decode_body_text(xml)
    ctx = _bucket_full_extractor_oracle(sid, xml, body)

    seg_a = next(s for s in ctx.by_segment if "624/2006" in "".join(ctx.by_segment[s]))
    seg_b = next(s for s in ctx.by_segment if "688/2001" in "".join(ctx.by_segment[s]))
    # the 624/2006 key lands ONLY in the osakeyhtiolaissa segment
    assert ctx.by_segment[seg_a] == {"624/2006"}
    assert "624/2006" in seg_a and "688/2001" not in seg_a
    assert ctx.by_segment[seg_b] == {"688/2001"}
    assert "688/2001" in seg_b and "624/2006" not in seg_b


def test_full_oracle_matches_projection_per_segment() -> None:
    # Each citation segment's full-oracle key set equals the construction
    # projection key set -> every in-scope segment is a MATCH.
    xml = _akn(
        "Sovelletaan, mita osakeyhtiolaissa (624/2006) 5 §:ssa saadetaan.",
        "Tehdaan kuten valtionavustuslaissa (688/2001) 9 §:ssa.",
    )
    sid = "999/2025"
    body = decode_body_text(xml)
    ctx = _bucket_full_extractor_oracle(sid, xml, body)
    index = build_clause_index(sid, body)
    matched = 0
    for sent in index.sentences:
        seg = body[sent.char_start : sent.char_end]
        proj = projection_reference_keys(parse_citation_sentence(seg), sid)
        oracle = full_oracle_reference_keys(seg, ctx)
        if proj or oracle:
            assert proj == oracle
            matched += 1
    assert matched == 2


def test_canonicalize_year_number_key_to_number_year() -> None:
    # <ref>-element eId convention YEAR/NUMBER -> canonical NUMBER/YEAR.
    assert _canonicalize_statute_key("2015/1385") == "1385/2015"
    assert _canonicalize_statute_key("1992/150") == "150/1992"
    # eId instance suffix on the number is stripped on swap
    assert _canonicalize_statute_key("1889/39-001") == "39/1889"
    # provision tail preserved
    assert _canonicalize_statute_key("2015/1385/5") == "1385/2015/5"
    # already-canonical NUMBER/YEAR is unchanged (year is the 2nd component)
    assert _canonicalize_statute_key("1385/2015") == "1385/2015"
    assert _canonicalize_statute_key("39/1889/ch38/1") == "39/1889/ch38/1"
    # by-name and non-statute keys untouched
    assert _canonicalize_statute_key("fi-name:valmiuslaki") == "fi-name:valmiuslaki"


def test_canonicalize_statute_key_in_segment_disambiguates_both_years() -> None:
    # The <ref>-lane key ``1995/1774`` (YEAR/NUMBER) cannot be oriented by number
    # alone — the statute NUMBER 1774 is itself in the 1700–2100 enactment-year
    # range, so the number-only rule leaves it un-swapped (ambiguous). The SEGMENT
    # carries the author-written inline ``(1774/1995)`` paren (NUMBER/YEAR), which
    # disambiguates: 1995 is the year, 1774 the number -> swap to ``1774/1995``.
    seg_parens = {"1774/1995"}
    assert _canonicalize_statute_key("1995/1774") == "1995/1774"  # number-only: stuck
    assert (
        _canonicalize_statute_key_in_segment("1995/1774", seg_parens) == "1774/1995"
    )
    # provision tail preserved across the segment-aware swap
    assert (
        _canonicalize_statute_key_in_segment("1995/1774/48c/2", seg_parens)
        == "1774/1995/48c/2"
    )
    # chapter-qualified tail preserved
    assert (
        _canonicalize_statute_key_in_segment("1995/1774/ch5/42/4", seg_parens)
        == "1774/1995/ch5/42/4"
    )
    # with NO matching inline paren, the ambiguous key is left as-is (honest
    # non-swap, never an orientation guess).
    assert _canonicalize_statute_key_in_segment("1995/1774", set()) == "1995/1774"
    # the unambiguous case is still handled by the number-only rule (year/non-year)
    assert (
        _canonicalize_statute_key_in_segment("2015/1385", {"9999/9999"}) == "1385/2015"
    )
    # by-name keys are never reoriented
    assert (
        _canonicalize_statute_key_in_segment("fi-name:x", {"1/2"}) == "fi-name:x"
    )


def test_full_oracle_orientation_two_digit_year_paren_matches_projection() -> None:
    # The author wrote a TWO-DIGIT-year paren ``(1767/95)``; the projection expands
    # it to ``1767/1995`` and the <ref> lane keys ``1995/1767`` (4-digit YEAR/NUMBER,
    # both year-plausible). The segment-paren orientation lookup must compare against
    # the EXPANDED 4-digit form, else the swap silently fails -> orientation miss.
    seg = "Verohallinto on arvonlisäverolain (1767/95) 128 §:n nojalla määrännyt."
    xml = _akn(seg)
    sid = "999/2025"
    body = decode_body_text(xml)
    ctx = _bucket_full_extractor_oracle(sid, xml, body)
    index = build_clause_index(sid, body)
    seen = 0
    for sent in index.sentences:
        s = body[sent.char_start : sent.char_end]
        proj = projection_reference_keys(parse_citation_sentence(s), sid)
        oracle = full_oracle_reference_keys(s, ctx)
        if proj or oracle:
            seen += 1
            assert proj == oracle, (proj, oracle)
            assert "1767/1995/128" in proj
    assert seen == 1


def test_full_oracle_orientation_matches_projection_for_year_plausible_number() -> None:
    # End-to-end: a citation whose statute NUMBER is year-plausible (1774). The
    # <ref> lane keys it YEAR/NUMBER (``1995/1774``); the construction projection
    # keys it from the inline surface (``1774/1995``). The segment-aware oracle
    # canonicalization must make these AGREE -> a MATCH, not an orientation miss.
    seg = "Eläkesäätiölain (1774/1995) 43 §:n 2 momentin 3 kohdan mukaista."
    xml = _akn(seg)
    sid = "999/2025"
    body = decode_body_text(xml)
    ctx = _bucket_full_extractor_oracle(sid, xml, body)
    index = build_clause_index(sid, body)
    seen = 0
    for sent in index.sentences:
        s = body[sent.char_start : sent.char_end]
        proj = projection_reference_keys(parse_citation_sentence(s), sid)
        oracle = full_oracle_reference_keys(s, ctx)
        if proj or oracle:
            seen += 1
            assert proj == oracle, (proj, oracle)
            # the canonical NUMBER/YEAR orientation won on both sides
            assert any(k.startswith("1774/1995") for k in proj)
    assert seen == 1


# --------------------------------------------------------------------------
# Finding B: the construction parse subsumes the regex-anchor class. The
# production plain-text regex (_PLAIN_TEXT_FI_STATUTE_RE) anchors on a known
# inflected statute-name head IMMEDIATELY before the (id) paren; an intervening
# genitive modifier or a descriptive ``-kaari`` title with no recognized suffix
# DEFEATS it. The construction parse keys on the (id) anchor itself, so it catches
# every inline-(id) citation regardless of the head morphology.
# --------------------------------------------------------------------------


def test_construction_catches_intervening_modifier_regex_misses() -> None:
    # ``annettu opetusministeriön asetus (253/2001)``: the genitive modifier
    # ``opetusministeriön`` sits BETWEEN the ``annettu`` participle and the
    # nominative head ``asetus``, breaking the regex's ``annettu (laki|asetus)``
    # adjacency. The regex finds nothing; the construction parse gets ``253/2001``.
    seg = "Tämä korvaa annettu opetusministeriön asetus (253/2001)."
    assert _PLAIN_TEXT_FI_STATUTE_RE.search(seg) is None  # regex DEFEATED
    sp = parse_citation_sentence(seg)
    assert projection_reference_keys(sp, "999/2025") == {"253/2001"}
    assert_total_ownership(sp)


def test_construction_catches_kaari_descriptive_title_regex_misses() -> None:
    # ``tietoyhteiskuntakaaressa (917/2014)``: a ``-kaari`` descriptive title with
    # no ``laki``/``asetus``/``päätös`` suffix the regex knows -> regex misses.
    seg = "Turvallisuusverkko on tietoyhteiskuntakaaressa (917/2014) tarkoitettu verkko."
    assert _PLAIN_TEXT_FI_STATUTE_RE.search(seg) is None  # regex DEFEATED
    sp = parse_citation_sentence(seg)
    assert projection_reference_keys(sp, "999/2025") == {"917/2014"}
    assert_total_ownership(sp)
    # also the genitive ``-kaaren`` form
    seg2 = "Maakaaren (540/1995) mukaista kiinteistöä koskee tämä säännös."
    assert _PLAIN_TEXT_FI_STATUTE_RE.search(seg2) is None
    sp2 = parse_citation_sentence(seg2)
    assert projection_reference_keys(sp2, "999/2025") == {"540/1995"}
    assert_total_ownership(sp2)


def test_collapse_redundant_statute_only_keeps_lone_statute_key() -> None:
    # a statute-only key with a same-statute precise sibling is dropped
    assert _collapse_redundant_statute_only({"527/2014", "527/2014/142"}) == {
        "527/2014/142"
    }
    # a lone statute-only key (no precise sibling) is kept
    assert _collapse_redundant_statute_only({"688/2001"}) == {"688/2001"}
    # by-name keys are never treated as statute-only collapse targets
    assert _collapse_redundant_statute_only(
        {"fi-name:x", "fi-name:x/5"}
    ) == {"fi-name:x", "fi-name:x/5"}


def test_full_oracle_surfaces_genuine_kaari_inline_id_now_bound_in_production() -> None:
    # The witness shape from the corpus: a ``-kaari`` INESSIVE citation with an
    # inline (id). Before the citation production-flip the regex plain-text lane
    # could not anchor this head, so the full production oracle MISSED it and the
    # construction frame scored a SUPERSET. AFTER the flip the construction parse is
    # the PRIMARY inline-(id) producer in production, so the full extractor — and
    # therefore the full oracle built from it — NOW binds ``917/2014`` directly.
    # The legacy plain-text-ONLY span oracle (just the regex recognizer) still
    # misses it, evidencing the strangle: the regex's Finding-B gap is now covered
    # by construction in the live pipeline. Projection and full oracle AGREE.
    seg = "Turvallisuusverkko on tietoyhteiskuntakaaressa (917/2014) tarkoitettu verkko."
    xml = _akn(seg)
    sid = "999/2025"
    body = decode_body_text(xml)
    assert oracle_reference_keys_for_span(seg) == set()  # regex-only lane still misses
    ctx = _bucket_full_extractor_oracle(sid, xml, body)
    proj = projection_reference_keys(parse_citation_sentence(seg), sid)
    oracle = full_oracle_reference_keys(seg, ctx)
    assert proj == {"917/2014"}
    assert oracle == {"917/2014"}  # post-flip: production now binds this -kaari site
    assert _classify(proj, oracle, False) == "match"


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

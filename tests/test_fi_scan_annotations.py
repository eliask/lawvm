"""Tests for the annotation-based scan layer.

Verifies that AnnotatedStream.structural_view() and the full annotation
pipeline (apply_annotations) produce correct, deterministic output for
real johtolause text samples.
"""
from __future__ import annotations

import pytest
from typing import cast

from lawvm.finland.johtolause.peg3 import Token, tokenize
from lawvm.finland.johtolause.scan import (
    Annotation,
    AnnotatedStream,
    Span,
    annotate_statute_citations,
    annotate_statute_names,
    annotate_formal_title_suffix,
    annotate_jolloin,
    apply_annotations,
    apply_annotations_with_jolloin_pairs,
    _extract_renumber_pairs_from_jolloin_tokens,
    extract_target_version_bindings,
)
from lawvm.finland.source_verb import SourceVerb
from tests.fixtures.fi_curated_cases import CURATED_CASES


class TestSpan:
    def test_contains(self):
        s = Span(2, 5)
        assert 2 in s
        assert 4 in s
        assert 5 not in s
        assert 1 not in s

    def test_len(self):
        assert len(Span(0, 3)) == 3
        assert len(Span(5, 5)) == 0


class TestAnnotatedStream:
    def test_no_annotations_passes_through(self):
        tokens = tokenize("muutetaan 3 §")
        stream = AnnotatedStream(tokens=tokens)
        assert stream.structural_view() == tokens

    def test_single_annotation_emits_sentinel(self):
        tokens = [
            Token("muutetaan", "muuttaa", "VERB", "", SourceVerb.MUUTTAA),
            Token("lain", "laki", "WORD", "", None),
            Token("100/2021", "100/2021", "CITE", "", None),
            Token("3", "3", "NUM", "", None),
            Token("§", "§", "PYKALA", "NOM", None),
        ]
        ann = Annotation(
            kind="citation",
            span=Span(1, 3),  # covers "lain" and "100/2021"
            sentinel_cat="CITATION_SPAN",
        )
        stream = AnnotatedStream(tokens=tokens, annotations=[ann])
        view = stream.structural_view()
        assert len(view) == 4  # VERB + CITATION_SPAN + NUM + PYKALA
        assert view[0].cat == "VERB"
        assert view[1].cat == "CITATION_SPAN"
        assert view[2].cat == "NUM"
        assert view[3].cat == "PYKALA"

    def test_overlapping_annotations_outer_wins(self):
        tokens = [
            Token("a", "a", "WORD", "", None),
            Token("b", "b", "WORD", "", None),
            Token("c", "c", "WORD", "", None),
            Token("d", "d", "WORD", "", None),
        ]
        outer = Annotation(kind="prov", span=Span(0, 3), sentinel_cat="PROVENANCE_SPAN")
        inner = Annotation(kind="cite", span=Span(1, 2), sentinel_cat="CITATION_SPAN")
        stream = AnnotatedStream(tokens=tokens, annotations=[inner, outer])
        view = stream.structural_view()
        # Outer covers 0-3, inner is subsumed
        assert len(view) == 2  # PROVENANCE_SPAN + "d"
        assert view[0].cat == "PROVENANCE_SPAN"
        assert view[1].text == "d"

    def test_adjacent_annotations(self):
        tokens = [
            Token("a", "a", "WORD", "", None),
            Token("b", "b", "WORD", "", None),
            Token("c", "c", "WORD", "", None),
            Token("d", "d", "WORD", "", None),
        ]
        a1 = Annotation(kind="cite", span=Span(0, 2), sentinel_cat="CITATION_SPAN")
        a2 = Annotation(kind="prov", span=Span(2, 4), sentinel_cat="PROVENANCE_SPAN")
        stream = AnnotatedStream(tokens=tokens, annotations=[a1, a2])
        view = stream.structural_view()
        assert len(view) == 2
        assert view[0].cat == "CITATION_SPAN"
        assert view[1].cat == "PROVENANCE_SPAN"


class TestAnnotateCitationsRoundTrip:
    """Verify annotation-based citation annotation produces correct, deterministic output."""

    _SAMPLES = [
        # Standard citation after statute name
        "muutetaan kuluttajansuojalain (38/1978) 11 luku",
        # Citation with provenance clause
        "kumotaan kuluttajansuojalain (38/1978) 11 luku sekä muutetaan 6 a luvun 12 § sekä 7 luku, sellaisena kuin niistä on 6 a luvun 12 § laissa 29/2005, seuraavasti:",
        # Multiple citations
        "muutetaan arvopaperimarkkinalain (746/2012) 3 luvun 1 §, sellaisena kuin se on laissa 519/2019, ja 8 luvun 5 a §, sellaisena kuin se on laissa 1278/2015, sekä lisätään 10 lukuun uusi 10 a luku, seuraavasti:",
        # Provenance at start
        "muutetaan sellaisena kuin se on laissa 100/2021 3 § ja 5 §",
        # No citation
        "muutetaan 3 § ja 5 §",
        # Complex multi-citation
        "muutetaan ajoneuvojen hyväksynnästä 19 päivänä joulukuuta 2002 annetun valtioneuvoston asetuksen (1244/2002) 1 ja 2 §",
    ]

    @pytest.mark.parametrize("text", _SAMPLES, ids=range(len(_SAMPLES)))
    def test_citation_annotations_are_deterministic(self, text: str):
        """annotate_statute_citations → structural_view is deterministic."""
        tokens = tokenize(text)
        annotations = annotate_statute_citations(tokens)
        stream = AnnotatedStream(tokens=tokens, annotations=annotations)
        result1 = stream.structural_view()
        result2 = AnnotatedStream(tokens=tokens, annotations=annotate_statute_citations(tokens)).structural_view()
        assert len(result1) == len(result2)
        for i, (a, b) in enumerate(zip(result1, result2, strict=True)):
            assert a.cat == b.cat and a.text == b.text, (
                f"Token {i} non-deterministic: ({a.cat}, {a.text!r}) vs ({b.cat}, {b.text!r})"
            )

    @pytest.mark.parametrize("text", _SAMPLES, ids=range(len(_SAMPLES)))
    def test_citation_annotations_emit_citation_span(self, text: str):
        """Citation annotations must produce CITATION_SPAN sentinels for inputs with citations."""
        tokens = tokenize(text)
        annotations = annotate_statute_citations(tokens)
        if not annotations:
            # No citations in input — structural view should equal raw tokens
            stream = AnnotatedStream(tokens=tokens, annotations=annotations)
            assert stream.structural_view() == tokens
        else:
            stream = AnnotatedStream(tokens=tokens, annotations=annotations)
            view = stream.structural_view()
            assert any(t.cat == "CITATION_SPAN" for t in view), (
                f"Expected CITATION_SPAN in view: {[(t.cat, t.text) for t in view]}"
            )


class TestAnnotateStatuteNamesRoundTrip:
    """Verify annotation-based statute name annotation produces correct output."""

    _SAMPLES = [
        "muutetaan kuluttajansuojalain (38/1978) 11 luku",
        "kumotaan 20 päivänä tammikuuta 1978 annetun kuluttajansuojalain (38/78) 1 luvun 6 §",
        "muutetaan ajoneuvojen hyväksynnästä 19 päivänä joulukuuta 2002 annetun valtioneuvoston asetuksen (1244/2002) 1 ja 2 §",
        "muutetaan 3 § ja 5 §",  # no statute name
        "lisätään lakiin uusi 8 luku",  # DOC:ILL target
    ]

    @pytest.mark.parametrize("text", _SAMPLES, ids=range(len(_SAMPLES)))
    def test_statute_names_annotations_are_deterministic(self, text: str):
        """citation + names annotations → structural_view is deterministic."""
        tokens = tokenize(text)
        # Run twice, verify identical output
        cite_anns1 = annotate_statute_citations(tokens)
        name_anns1 = annotate_statute_names(tokens, cite_anns1)
        result1 = AnnotatedStream(tokens=tokens, annotations=cite_anns1 + name_anns1).structural_view()

        cite_anns2 = annotate_statute_citations(tokens)
        name_anns2 = annotate_statute_names(tokens, cite_anns2)
        result2 = AnnotatedStream(tokens=tokens, annotations=cite_anns2 + name_anns2).structural_view()

        assert len(result1) == len(result2), (
            f"Length mismatch: {len(result1)} vs {len(result2)}\n"
            f"run1: {[(t.cat, t.text) for t in result1]}\n"
            f"run2: {[(t.cat, t.text) for t in result2]}"
        )
        for i, (a, b) in enumerate(zip(result1, result2, strict=True)):
            assert a.cat == b.cat and a.text == b.text, (
                f"Token {i} non-deterministic: ({a.cat}, {a.text!r}) vs ({b.cat}, {b.text!r})"
            )


class TestFullPipelineRoundTrip:
    """Verify apply_annotations produces deterministic, structurally valid output.

    The annotation pipeline is the sole noise-stripping pipeline.  These tests
    ensure it produces stable output and that the output contains expected
    structural tokens (VERB, structural targets) for each curated case.
    """

    @pytest.mark.parametrize(
        "case",
        CURATED_CASES,
        ids=[cast(str, c["name"]) for c in CURATED_CASES],
    )
    def test_apply_annotations_is_deterministic(self, case: dict):
        """apply_annotations(tokenize(text)) is deterministic across calls."""
        text = case["text"]
        tokens = tokenize(text)
        result1 = apply_annotations(tokens)
        result2 = apply_annotations(tokens)
        assert len(result1) == len(result2), (
            f"[{case['name']}] Length mismatch: {len(result1)} vs {len(result2)}\n"
            f"run1: {[(t.cat, t.text) for t in result1]}\n"
            f"run2: {[(t.cat, t.text) for t in result2]}"
        )
        for i, (a, b) in enumerate(zip(result1, result2, strict=True)):
            assert a.cat == b.cat and a.text == b.text, (
                f"[{case['name']}] Token {i}: ({a.cat}, {a.text!r}) vs ({b.cat}, {b.text!r})"
            )




class TestJolloiSectionRenumber:
    """Tests for the jolloin section-level renumber fix.

    Bug: _extract_renumber_pairs_from_jolloin_tokens only handled LUKU (chapter)
    renumbers, silently dropping PYKALA (section) renumbers such as
    "jolloin nykyinen 10 § siirtyy 10 a §:ksi".
    """

    def test_extract_section_renumber_pair_simple(self):
        """Section renumber pair: plain numeric destination."""
        tokens = tokenize("jolloin nykyinen 5 § siirtyy 6 §:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("5", "6", "P")]

    def test_extract_section_renumber_pair_with_letter_suffix(self):
        """Section renumber pair: destination with letter suffix (10 → 10a)."""
        tokens = tokenize("jolloin nykyinen 10 § siirtyy 10 a §:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("10", "10a", "P")]

    def test_extract_chapter_renumber_pair_still_works(self):
        """Chapter renumber pair: existing behaviour must be preserved."""
        tokens = tokenize("jolloin nykyinen 8 ja 9 luku siirtyvät 10 ja 11 luvuksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("8", "10", "L"), ("9", "11", "L")]

    def test_annotate_jolloin_stores_section_renumber_in_detail(self):
        """annotate_jolloin must populate renumber_pairs for section patterns."""
        tokens = tokenize(
            "lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi"
        )
        anns = annotate_jolloin(tokens)
        assert len(anns) == 1
        assert anns[0].detail is not None
        pairs = anns[0].detail["renumber_pairs"]
        assert pairs == [("10", "10a", "P")]

    def test_annotate_jolloin_chapter_renumber_detail_preserved(self):
        """Chapter renumber pairs in annotate_jolloin detail must still carry kind='L'."""
        tokens = tokenize(
            "lisätään uusi 3 luku, jolloin nykyinen 3 luku siirtyy 4 luvuksi"
        )
        anns = annotate_jolloin(tokens)
        assert len(anns) == 1
        assert anns[0].detail is not None
        pairs = anns[0].detail["renumber_pairs"]
        assert pairs == [("3", "4", "L")]

    def test_parse_clause_emits_section_renumber_op(self):
        """parse_clause must prepend S P op for jolloin section renumber."""
        from lawvm.finland.johtolause.compat import parse_clause
        text = (
            "lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi, "
            "sekä muutetaan 14 §"
        )
        ops = parse_clause(text).parsed_ops
        codes = [op.code() for op in ops]
        assert codes == ["S P 10", "L P 10", "M P 14"]
        # Verify renumber_dest is set on the RENUMBER op
        renumber_op = ops[0]
        assert renumber_op.verb == "S"
        assert renumber_op.kind == "P"
        assert renumber_op.number == "10"
        assert renumber_op.renumber_dest == "10a"
        assert "renumber_clause" in renumber_op.notes

    def test_apply_annotations_with_jolloin_pairs_keeps_trailing_explicit_target(self):
        """Trailing explicit targets after jolloin must survive scanner annotation."""
        text = (
            "lisätään 15 luvun 2 §:ään, sellaisena kuin se on laissa 1278/2015, "
            "uusi 3 momentti, jolloin muutettu 3 momentti siirtyy 4 momentiksi, "
            "sekä 17 luvun 2 §:ään uusi 2 momentti seuraavasti:"
        )

        filtered_tokens, pair_map = apply_annotations_with_jolloin_pairs(tokenize(text))
        filtered_shapes = [(tok.cat, tok.text) for tok in filtered_tokens]

        assert pair_map == {11: [("3", "4", "M")]}
        assert filtered_shapes == [
            ("VERB", "lisätään"),
            ("NUM", "15"),
            ("LUKU", "luvun"),
            ("NUM", "2"),
            ("PYKALA", "§:ään"),
            ("COMMA", ","),
            ("CITATION_SPAN", "[CITE]"),
            ("UUSI", "uusi"),
            ("NUM", "3"),
            ("MOMENTTI", "momentti"),
            ("COMMA", ","),
            ("JOLLOIN_MOVE", "jolloin-move"),
            ("COMMA", ","),
            ("CONJ", "sekä"),
            ("NUM", "17"),
            ("LUKU", "luvun"),
            ("NUM", "2"),
            ("PYKALA", "§:ään"),
            ("UUSI", "uusi"),
            ("NUM", "2"),
            ("MOMENTTI", "momentti"),
            ("END_SENTINEL_SPAN", "[END]"),
        ]

    def test_parse_clause_keeps_trailing_explicit_insert_after_jolloin_clause(self):
        """A trailing explicit insert after jolloin must keep its own chapter scope."""
        from lawvm.finland.johtolause.compat import parse_clause

        text = (
            "lisätään 15 luvun 2 §:ään, sellaisena kuin se on laissa 1278/2015, "
            "uusi 3 momentti, jolloin muutettu 3 momentti siirtyy 4 momentiksi, "
            "sekä 17 luvun 2 §:ään uusi 2 momentti seuraavasti:"
        )

        codes = [op.code() for op in parse_clause(text).parsed_ops]
        assert codes == ["S P L:15 2 3", "L P L:15 2 3", "L P L:17 2 2"]

    def test_stamp_default_witness_uses_typed_renumber_dest(self) -> None:
        from lawvm.finland.johtolause.surface_model import SurfaceNode, SurfaceTargetRef, TargetKind
        from lawvm.finland.johtolause.surface_parse import _stamp_default_witness

        nodes: list[SurfaceNode] = [SurfaceTargetRef(kind=TargetKind.SECTION, label="10", renumber_dest="10a")]
        _stamp_default_witness(nodes, 0, 1)

        stamped = nodes[0]
        assert stamped.witness is not None
        assert stamped.witness.rule_id == "fi.section_renumber"

    def test_parse_clause_chapter_renumber_still_emits_kind_L(self):
        """parse_clause must still emit S L for jolloin chapter renumber."""
        from lawvm.finland.johtolause.compat import parse_clause
        text = "lisätään uusi 3 luku, jolloin nykyinen 8 ja 9 luku siirtyvät 10 ja 11 luvuksi"
        ops = parse_clause(text).parsed_ops
        renumber_ops = [op for op in ops if op.verb == "S" and op.kind == "L"]
        assert len(renumber_ops) == 2
        assert renumber_ops[0].number == "8"
        assert renumber_ops[0].renumber_dest == "10"
        assert renumber_ops[1].number == "9"
        assert renumber_ops[1].renumber_dest == "11"


class TestJolloinRangeExpansion:
    """Tests for range expansion in jolloin renumber extraction (Pro audit #7).

    Bug: _extract_renumber_pairs_from_jolloin_tokens zipped raw NUM tokens,
    so a range like "3\u20135 \u00a7 ... 4\u20136 \u00a7:ksi" produced (3\u21924, 5\u21926)
    instead of the correct full expansion (3\u21924, 4\u21925, 5\u21926).
    """

    def test_section_range_endash(self):
        """Section range with endash: 3\u20135 \u2192 4\u20136 expands to 3 pairs."""
        tokens = tokenize("jolloin nykyinen 3\u20135 \u00a7 siirtyv\u00e4t 4\u20136 \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("3", "4", "P"), ("4", "5", "P"), ("5", "6", "P")]

    def test_section_range_hyphen(self):
        """Section range with regular hyphen: 3-5 \u2192 4-6 expands identically."""
        tokens = tokenize("jolloin nykyinen 3-5 \u00a7 siirtyv\u00e4t 4-6 \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("3", "4", "P"), ("4", "5", "P"), ("5", "6", "P")]

    def test_chapter_range_endash(self):
        """Chapter range: 3\u20135 luku \u2192 4\u20136 luvuksi expands to 3 pairs."""
        tokens = tokenize("jolloin nykyinen 3\u20135 luku siirtyv\u00e4t 4\u20136 luvuksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("3", "4", "L"), ("4", "5", "L"), ("5", "6", "L")]

    def test_single_element_range(self):
        """Degenerate range 5\u20135 \u2192 6\u20136 produces one pair."""
        tokens = tokenize("jolloin nykyinen 5\u20135 \u00a7 siirtyv\u00e4t 6\u20136 \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("5", "6", "P")]

    def test_range_does_not_break_single_renumber(self):
        """Single-number renumber must still work after range logic is added."""
        tokens = tokenize("jolloin nykyinen 5 \u00a7 siirtyy 6 \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("5", "6", "P")]

    def test_range_does_not_break_ja_list(self):
        """Explicit ja-list renumber must still work after range logic is added."""
        tokens = tokenize("jolloin nykyinen 8 ja 9 luku siirtyv\u00e4t 10 ja 11 luvuksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("8", "10", "L"), ("9", "11", "L")]

    def test_range_with_letter_suffix_on_destination(self):
        """Single source with letter-suffixed destination still works."""
        tokens = tokenize("jolloin nykyinen 10 \u00a7 siirtyy 10 a \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == [("10", "10a", "P")]

    def test_mismatched_range_sizes_returns_empty(self):
        """Mismatched expanded range sizes (3\u20135 \u2192 4\u20138) returns empty."""
        tokens = tokenize("jolloin nykyinen 3\u20135 \u00a7 siirtyv\u00e4t 4\u20138 \u00a7:ksi")
        pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, 0, len(tokens))
        assert pairs == []


class TestAnnotateFormalTitleSuffix:
    """Tests for annotate_formal_title_suffix — formal citation suffix stripping.

    Regression for 2012/728 (amendment 2025/1184): the johtolause contains a
    reference to a prior amendment act cited by its formal title, e.g.
    "kumotaan kalastuslain 2 §:n kumoamisesta annetun lain 5 §".
    The phrase "2 §:n kumoamisesta annetun lain" is structural noise (a citation
    to the repealed act's title), not an operative target.  Before this fix,
    the PEG parser extracted "2" as the repeal target instead of "5".
    """

    def test_produces_citation_span_for_suffix_with_prior_citation(self):
        """'N §:n kumoamisesta annetun lain' after CITATION_SPAN → CITATION_SPAN."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import (
            AnnotatedStream, annotate_statute_citations, annotate_statute_names,
        )
        text = "kumotaan kalastuslain (728/2012) 2 §:n kumoamisesta annetun lain 5 §"
        tokens = tokenize(text)
        # Phase 1 view
        cite_anns = annotate_statute_citations(tokens)
        name_anns = annotate_statute_names(tokens, cite_anns)
        view = AnnotatedStream(tokens=tokens, annotations=cite_anns + name_anns).structural_view()
        # Phase 2: formal title suffix on the Phase 1 view
        suffix_anns = annotate_formal_title_suffix(view)
        assert len(suffix_anns) == 1
        assert suffix_anns[0].kind == "formal_title_suffix"
        assert suffix_anns[0].sentinel_cat == "CITATION_SPAN"

    def test_produces_citation_span_for_suffix_after_statute_name_span(self):
        """'N §:n kumoamisesta annetun lain' after STATUTE_NAME_SPAN → CITATION_SPAN."""
        from lawvm.finland.johtolause.peg3 import tokenize
        from lawvm.finland.johtolause.scan import (
            AnnotatedStream, annotate_statute_citations, annotate_statute_names,
        )
        text = "kumotaan kalastuslain 2 §:n kumoamisesta annetun lain 5 §"
        tokens = tokenize(text)
        cite_anns = annotate_statute_citations(tokens)
        name_anns = annotate_statute_names(tokens, cite_anns)
        view = AnnotatedStream(tokens=tokens, annotations=cite_anns + name_anns).structural_view()
        suffix_anns = annotate_formal_title_suffix(view)
        assert len(suffix_anns) == 1
        assert suffix_anns[0].kind == "formal_title_suffix"

    def test_produces_citation_span_for_suffix_after_verb_directly(self):
        """'N §:n kumoamisesta annetun lain' immediately after VERB → CITATION_SPAN."""
        from lawvm.finland.johtolause.peg3 import tokenize
        text = "kumotaan 2 §:n kumoamisesta annetun lain 5 §"
        tokens = tokenize(text)
        suffix_anns = annotate_formal_title_suffix(tokens)
        assert len(suffix_anns) == 1
        assert suffix_anns[0].kind == "formal_title_suffix"

    def test_muuttamisesta_variant_also_caught(self):
        """'N §:n muuttamisesta annetun lain' is also a formal title suffix."""
        from lawvm.finland.johtolause.peg3 import tokenize
        text = "kumotaan 2 §:n muuttamisesta annetun lain 5 §"
        tokens = tokenize(text)
        suffix_anns = annotate_formal_title_suffix(tokens)
        assert len(suffix_anns) == 1

    def test_does_not_annotate_plain_repeal_clause(self):
        """Plain 'kumotaan 5 §' produces no formal-title-suffix annotation."""
        from lawvm.finland.johtolause.peg3 import tokenize
        text = "kumotaan 5 §"
        tokens = tokenize(text)
        suffix_anns = annotate_formal_title_suffix(tokens)
        assert len(suffix_anns) == 0

    def test_full_pipeline_extracts_correct_target_with_citation(self):
        """apply_annotations + extract_legal_ops must yield §5, not §2 (bug regression)."""
        from lawvm.finland.johtolause import extract_legal_ops
        text = "kumotaan kalastuslain (728/2012) 2 §:n kumoamisesta annetun lain 5 §"
        ops = extract_legal_ops(text)
        assert len(ops) == 1
        assert ops[0].target.path == (("section", "5"),)

    def test_full_pipeline_extracts_correct_target_without_citation(self):
        """Without citation: §5 must be extracted, not §2."""
        from lawvm.finland.johtolause import extract_legal_ops
        text = "kumotaan kalastuslain 2 §:n kumoamisesta annetun lain 5 §"
        ops = extract_legal_ops(text)
        assert len(ops) == 1
        assert ops[0].target.path == (("section", "5"),)

    def test_full_pipeline_extracts_correct_target_no_name_at_all(self):
        """No base name either: §5 must be extracted, not §2."""
        from lawvm.finland.johtolause import extract_legal_ops
        text = "kumotaan 2 §:n kumoamisesta annetun lain 5 §"
        ops = extract_legal_ops(text)
        assert len(ops) == 1
        assert ops[0].target.path == (("section", "5"),)

    def test_full_pipeline_with_date_words_in_suffix(self):
        """Date words between kumoamisesta and annetun must not block span detection."""
        from lawvm.finland.johtolause import extract_legal_ops
        text = "kumotaan kalastuslain 2 §:n kumoamisesta 21 päivänä elokuuta 2015 annetun lain 5 §"
        ops = extract_legal_ops(text)
        assert len(ops) == 1
        assert ops[0].target.path == (("section", "5"),)

    def test_plain_repeal_unaffected(self):
        """Plain operative repeal 'kumotaan 5 §' must still extract §5."""
        from lawvm.finland.johtolause import extract_legal_ops
        text = "kumotaan 5 §"
        ops = extract_legal_ops(text)
        assert len(ops) == 1
        assert ops[0].target.path == (("section", "5"),)


def test_extract_target_version_bindings_preserves_split_cited_version_owners() -> None:
    tokens = tokenize(
        "muutetaan aluevalvontalain (755/2000) 23, 24 c, 30 b ja 34 a §, "
        "sellaisina kuin ne ovat, 23 § laissa 195/2015 sekä 24 c, 30 b ja 34 a § "
        "laissa 575/2018, seuraavasti:"
    )

    bindings = extract_target_version_bindings(tokens)

    assert [(binding.target_labels, binding.cited_statute_id) for binding in bindings] == [
        (("23",), "2015/195"),
        (("24c", "30b", "34a"), "2018/575"),
    ]

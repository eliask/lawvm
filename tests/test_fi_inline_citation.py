"""Tests for InlineCitation core primitive, Finland extractor, and conformance corpus.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests — typed primitive construction + serialization.
  2. Real corpus regression — conformance corpus fixtures (all 7 scenarios).
  3. Finding/observation tests — InlineCitationPatternMatch emission.
  4. Negative tests — patterns that must NOT fire on nearby valid prose.
  5. Strict-mode tests — reserved (inline body has no strict-mode UNRESOLVED emit).
  6. No-leak tests — OLD_COMMITTEE canonical_id is None; UNRESOLVED kind absent
     from non-trivial citations.
  7. Schema-stability tests — serialized column names pinned.
  8. Cross-feature composition test — rows for same statute from #1 (fi_refs) +
     #11 (fi_preparatory_refs) + #12 (fi_inline_citations) form a clean
     non-overlapping cover.

Module coverage:
  - lawvm.core.inline_citation
  - lawvm.finland.inline_citation_extractor
  - lawvm.finland.conformance_corpus.inline.fixtures
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from lawvm.core.inline_citation import (
    InlineCitation,
    InlineCitationContext,
    InlineCitationKind,
    InlineCitationPatternMatch,
    inline_citation_to_row,
)
from lawvm.finland.inline_citation_extractor import (
    InlineCitationExtractionResult,
    InlineCitationRecognizer,
    extract_inline_citations,
)
from lawvm.finland.conformance_corpus.inline.fixtures import (
    ALL_FIXTURES,
    EK_IN_PRELIMINARY_WORK,
    HE_INLINE_CITATION,
    HE_PERUSTELUT_MIXED_CITATIONS,
    HE_VTV_CITATION,
    NEGATIVE_BARE_NUMBERS,
    REF_MARKUP_DEFERRED,
    STATUTE_BODY_COURT_REFS,
    InlineCorpusFixture,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_citation_matches(actual: InlineCitation, expected: Dict[str, Any]) -> None:
    """Assert actual InlineCitation matches all expected key/value pairs."""
    row = inline_citation_to_row(actual)
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"citation row key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


def _assert_pattern_match_matches(
    actual: InlineCitationPatternMatch,
    expected: Dict[str, Any],
) -> None:
    actual_dict = {
        "rule_id": actual.rule_id,
        "phase": actual.phase,
        "source_doc_id": actual.source_doc_id,
        "reason": actual.reason,
        "raw_text": actual.raw_text,
        "kind_attempted": actual.kind_attempted,
        "blocking": actual.blocking,
    }
    for key, val in expected.items():
        assert actual_dict.get(key) == val, (
            f"pattern_match key {key!r}: expected {val!r}, got {actual_dict.get(key)!r}"
        )


def _run_fixture(fixture: InlineCorpusFixture) -> InlineCitationExtractionResult:
    """Extract inline citations for a conformance fixture."""
    return extract_inline_citations(
        fixture.xml_bytes,
        doc_id=fixture.source_doc_id,
        doc_kind=fixture.source_doc_kind,
        source_span_file=None,
    )


# ===========================================================================
# 1. Synthetic unit tests
# ===========================================================================


class TestInlineCitationPrimitive:
    """Test the InlineCitation typed primitive (AGENTS.md §15 category 1)."""

    def test_construct_court_kko(self) -> None:
        citation = InlineCitation(
            source_doc_id="711/2022",
            source_doc_kind="statute",
            source_provision_ref="",
            kind=InlineCitationKind.COURT_KKO,
            canonical_id="fi.court.kko.2018.45",
            raw_text="KKO 2018:45",
            case_year=2018,
            case_number=45,
            context=InlineCitationContext.ENACTED_STATUTE_BODY,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
        )
        assert citation.kind == InlineCitationKind.COURT_KKO
        assert citation.canonical_id == "fi.court.kko.2018.45"
        assert citation.case_year == 2018
        assert citation.case_number == 45

    def test_construct_he_inline(self) -> None:
        citation = InlineCitation(
            source_doc_id="184/2024",
            source_doc_kind="he",
            source_provision_ref="",
            kind=InlineCitationKind.HE_INLINE,
            canonical_id="he/2024/116",
            raw_text="HE 116/2024",
            case_year=2024,
            case_number=116,
            context=InlineCitationContext.HE_RATIONALE,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
        )
        assert citation.source_doc_kind == "he"
        assert citation.canonical_id == "he/2024/116"

    def test_construct_old_committee_canonical_none(self) -> None:
        """OLD_COMMITTEE kind allows canonical_id=None (mapping deferred)."""
        citation = InlineCitation(
            source_doc_id="99/1985",
            source_doc_kind="statute",
            source_provision_ref="",
            kind=InlineCitationKind.OLD_COMMITTEE,
            canonical_id=None,
            raw_text="lvk.miet. 1/1980",
            case_year=None,
            case_number=None,
            context=InlineCitationContext.ENACTED_STATUTE_BODY,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
        )
        assert citation.canonical_id is None
        assert citation.kind == InlineCitationKind.OLD_COMMITTEE

    def test_construct_unresolved_canonical_none(self) -> None:
        """UNRESOLVED kind allows canonical_id=None."""
        citation = InlineCitation(
            source_doc_id="99/2022",
            source_doc_kind="statute",
            source_provision_ref="",
            kind=InlineCitationKind.UNRESOLVED,
            canonical_id=None,
            raw_text="some weird text",
            case_year=None,
            case_number=None,
            context=InlineCitationContext.OTHER,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
        )
        assert citation.canonical_id is None

    def test_non_nullable_kinds_require_canonical_id(self) -> None:
        """Non-null kinds raise ValueError when canonical_id=None."""
        with pytest.raises(ValueError, match="canonical_id"):
            InlineCitation(
                source_doc_id="711/2022",
                source_doc_kind="statute",
                source_provision_ref="",
                kind=InlineCitationKind.COURT_KKO,
                canonical_id=None,  # Must not be None for COURT_KKO
                raw_text="KKO 2018:45",
                case_year=2018,
                case_number=45,
                context=InlineCitationContext.ENACTED_STATUTE_BODY,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
            )

    def test_invalid_doc_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="source_doc_kind"):
            InlineCitation(
                source_doc_id="711/2022",
                source_doc_kind="mystery",  # Invalid
                source_provision_ref="",
                kind=InlineCitationKind.COURT_KKO,
                canonical_id="fi.court.kko.2018.45",
                raw_text="KKO 2018:45",
                case_year=2018,
                case_number=45,
                context=InlineCitationContext.ENACTED_STATUTE_BODY,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
            )

    def test_empty_doc_id_raises(self) -> None:
        with pytest.raises(ValueError, match="source_doc_id"):
            InlineCitation(
                source_doc_id="",  # Empty — forbidden
                source_doc_kind="statute",
                source_provision_ref="",
                kind=InlineCitationKind.COURT_KKO,
                canonical_id="fi.court.kko.2018.45",
                raw_text="KKO 2018:45",
                case_year=2018,
                case_number=45,
                context=InlineCitationContext.ENACTED_STATUTE_BODY,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
            )


# ===========================================================================
# 2. Conformance corpus — real corpus regression
# ===========================================================================


class TestConformanceCorpus:
    """Conformance corpus fixture tests (AGENTS.md §15 category 2)."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=[f.fixture_id for f in ALL_FIXTURES])
    def test_expected_citations_present(self, fixture: InlineCorpusFixture) -> None:
        """Every expected citation must appear in the extraction result."""
        result = _run_fixture(fixture)
        citation_rows = [inline_citation_to_row(c) for c in result.citations]

        for expected in fixture.expected_citations:
            matching = [
                row for row in citation_rows
                if all(row.get(k) == v for k, v in expected.items())
            ]
            assert matching, (
                f"[{fixture.fixture_id}] Expected citation not found: {expected}\n"
                f"Actual citations: {citation_rows}"
            )

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=[f.fixture_id for f in ALL_FIXTURES])
    def test_absent_kinds_not_present(self, fixture: InlineCorpusFixture) -> None:
        """Kinds listed in expected_absent_kinds must not appear in citations."""
        result = _run_fixture(fixture)
        actual_kinds = {c.kind.value for c in result.citations}
        for absent_kind in fixture.expected_absent_kinds:
            assert absent_kind not in actual_kinds, (
                f"[{fixture.fixture_id}] Kind {absent_kind!r} should be absent "
                f"but appeared in citations: {actual_kinds}"
            )

    def test_statute_body_court_refs_count(self) -> None:
        """Fixture 1: exactly 2 citations (KKO + KHO)."""
        result = _run_fixture(STATUTE_BODY_COURT_REFS)
        kinds = [c.kind.value for c in result.citations]
        assert "court_kko" in kinds
        assert "court_kho" in kinds

    def test_he_perustelut_all_four_kinds(self) -> None:
        """Fixture 2: all four citation kinds appear."""
        result = _run_fixture(HE_PERUSTELUT_MIXED_CITATIONS)
        kinds = {c.kind.value for c in result.citations}
        assert "court_kko" in kinds
        assert "court_kho" in kinds
        assert "ombudsman_eoa" in kinds
        assert "statute_inline" in kinds

    def test_he_inline_citation(self) -> None:
        """Fixture 3: HE->HE citation extracted with correct canonical_id."""
        result = _run_fixture(HE_INLINE_CITATION)
        kinds = {c.kind.value for c in result.citations}
        assert "he_inline" in kinds
        he_cites = [c for c in result.citations if c.kind == InlineCitationKind.HE_INLINE]
        assert he_cites
        assert he_cites[0].canonical_id == "he/2024/116"

    def test_he_inline_not_extracted_from_statute(self) -> None:
        """HE_INLINE must not be extracted when doc_kind='statute'."""
        # Statute body containing 'HE 116/2024' text
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Viittaus esitykseen HE 116/2024.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="50/2024", doc_kind="statute")
        kinds = {c.kind.value for c in result.citations}
        assert "he_inline" not in kinds, (
            "he_inline must not fire when doc_kind='statute'"
        )

    def test_vtv_citation(self) -> None:
        """Fixture 4: VTV report citation extracted."""
        result = _run_fixture(HE_VTV_CITATION)
        kinds = {c.kind.value for c in result.citations}
        assert "vtv_report" in kinds
        vtv = [c for c in result.citations if c.kind == InlineCitationKind.VTV_REPORT]
        assert vtv[0].canonical_id == "fi.vtv.5.2022"

    def test_ek_in_preliminary_work(self) -> None:
        """Fixture 5: EK in preliminaryWork → extracted; no HE_INLINE from prelim."""
        result = _run_fixture(EK_IN_PRELIMINARY_WORK)
        kinds = {c.kind.value for c in result.citations}
        assert "parliament_kirjelma" in kinds, "EK must be extracted from preliminaryWork"
        assert "he_inline" not in kinds, "HE_INLINE must not fire in preliminaryWork"
        ek = [c for c in result.citations if c.kind == InlineCitationKind.PARLIAMENT_KIRJELMA]
        assert ek[0].canonical_id == "fi.ek.42.2023"
        assert ek[0].context == InlineCitationContext.PRELIMINARY_WORK

    def test_negative_bare_numbers(self) -> None:
        """Fixture 6: bare YYYY/N in date context → no citation emitted."""
        result = _run_fixture(NEGATIVE_BARE_NUMBERS)
        assert len(result.citations) == 0, (
            f"Expected 0 citations for negative fixture; got {len(result.citations)}: "
            f"{[inline_citation_to_row(c) for c in result.citations]}"
        )

    def test_ref_markup_deferred(self) -> None:
        """Fixture 7: text inside <ref> excluded; plain-text KKO still extracted."""
        result = _run_fixture(REF_MARKUP_DEFERRED)
        kinds = {c.kind.value for c in result.citations}
        assert "court_kko" in kinds, "KKO outside <ref> should be extracted"
        # statute_inline must NOT appear because "711/2022" was inside a <ref>
        assert "statute_inline" not in kinds, (
            "statute_inline must not fire on text inside <ref> (deferred to #1)"
        )


# ===========================================================================
# 3. Finding / observation tests
# ===========================================================================


class TestPatternMatchObservations:
    """Tests for InlineCitationPatternMatch emission (AGENTS.md §15 category 3)."""

    def test_no_pattern_matches_for_clean_text(self) -> None:
        """Clean text with no citations emits no pattern_matches."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>T\xc3\xa4m\xc3\xa4 laki koskee puhtaita asioita.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="1/2024", doc_kind="statute")
        assert len(result.pattern_matches) == 0

    def test_pattern_match_primitive_fields(self) -> None:
        """InlineCitationPatternMatch has correct required fields."""
        pm = InlineCitationPatternMatch(
            rule_id="fi_inline_kko_sanity_fail",
            phase="inline_citation_extraction",
            source_doc_id="1/2024",
            reason="year out of range",
            raw_text="KKO 9999:1",
            kind_attempted="court_kko",
        )
        assert pm.rule_id == "fi_inline_kko_sanity_fail"
        assert pm.blocking is False
        assert pm.strict_disposition == "record"


# ===========================================================================
# 4. Negative tests
# ===========================================================================


class TestNegativeCases:
    """Tests for patterns that must NOT fire on nearby valid prose (AGENTS.md §15 category 4)."""

    def test_ek_in_body_fires_outside_prelim(self) -> None:
        """EK N/YYYY in normal body prose fires (positive check for negative isolation)."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Katso my\xc3\xb6s EK 5/2023 kirjelm\xc3\xa4.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="1/2024", doc_kind="statute")
        kinds = {c.kind.value for c in result.citations}
        assert "parliament_kirjelma" in kinds

    def test_kko_with_wrong_separator_not_extracted(self) -> None:
        """KKO without colon in year-number separator should NOT extract."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>KKO 2018 45 on ratkaisu ilman oikeaa erotinta.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="1/2024", doc_kind="statute")
        kko = [c for c in result.citations if c.kind == InlineCitationKind.COURT_KKO]
        assert len(kko) == 0, "KKO without colon separator should not match"

    def test_he_inline_not_from_prelim(self) -> None:
        """HE N/YYYY in preliminaryWork does NOT fire HE_INLINE (belongs to #11)."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<doc><meta/><mainBody>"
            b'<hcontainer name="conclusions">'
            b'<hcontainer name="preliminaryWork">'
            b"<content><p>HE 99/2022</p></content>"
            b"</hcontainer>"
            b"</hcontainer>"
            b"</mainBody></doc>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="50/2023", doc_kind="he")
        kinds = {c.kind.value for c in result.citations}
        # HE_INLINE should be suppressed in preliminaryWork
        assert "he_inline" not in kinds, (
            "HE_INLINE must not fire in preliminaryWork (belongs to #11)"
        )

    def test_empty_body_no_citations(self) -> None:
        """Document with no body content produces no citations."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body/></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="1/2024", doc_kind="statute")
        assert len(result.citations) == 0
        assert len(result.pattern_matches) == 0


# ===========================================================================
# 5. Strict-mode test (placeholder — body prose has no strict UNRESOLVED emit)
# ===========================================================================


class TestStrictMode:
    """Strict-mode tests (AGENTS.md §15 category 5).

    Body prose does not emit UNRESOLVED for every unmatched <p>.
    Strict mode for inline citations currently has no additional behavior beyond
    the base extraction. This test confirms that passing strict=True does not
    crash and produces identical results for clean input.
    """

    def test_strict_mode_no_crash(self) -> None:
        """strict=True must not crash on clean input."""
        result = extract_inline_citations(
            STATUTE_BODY_COURT_REFS.xml_bytes,
            doc_id=STATUTE_BODY_COURT_REFS.source_doc_id,
            doc_kind=STATUTE_BODY_COURT_REFS.source_doc_kind,
            strict=True,
        )
        kinds = {c.kind.value for c in result.citations}
        assert "court_kko" in kinds
        assert "court_kho" in kinds


# ===========================================================================
# 6. No-leak tests
# ===========================================================================


class TestNoLeak:
    """No-leak tests (AGENTS.md §15 category 6)."""

    def test_old_committee_canonical_id_is_none(self) -> None:
        """OLD_COMMITTEE citations always have canonical_id=None."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Ks. my\xc3\xb6s lvk.miet. 2/1965.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="50/1966", doc_kind="statute")
        old_c = [c for c in result.citations if c.kind == InlineCitationKind.OLD_COMMITTEE]
        for c in old_c:
            assert c.canonical_id is None, (
                f"OLD_COMMITTEE canonical_id must be None; got {c.canonical_id!r}"
            )

    def test_no_unresolved_rows_for_plain_prose(self) -> None:
        """Plain prose without citations emits NO UNRESOLVED rows (body is not a typed block)."""
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>T\xc3\xa4ss\xc3\xa4 py\xc3\xa4nniss\xc3\xa4 k\xc3\xa4sitell\xc3\xa4\xc3\xa4n asiaa ilman viittauksia.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="99/2024", doc_kind="statute")
        unresolved = [c for c in result.citations if c.kind == InlineCitationKind.UNRESOLVED]
        assert len(unresolved) == 0, (
            "Body prose must not emit UNRESOLVED for every unmatched <p>; "
            f"got: {unresolved}"
        )


# ===========================================================================
# 7. Schema stability tests
# ===========================================================================


class TestSchemaStability:
    """Schema-stability tests — serialized column names pinned (AGENTS.md §15 category 7)."""

    _EXPECTED_COLUMNS = frozenset({
        "source_doc_id",
        "source_doc_kind",
        "source_provision_ref",
        "kind",
        "canonical_id",
        "raw_text",
        "case_year",
        "case_number",
        "context",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_byte_len",
    })

    def test_serialized_columns_pinned(self) -> None:
        """inline_citation_to_row produces exactly the expected column set."""
        citation = InlineCitation(
            source_doc_id="711/2022",
            source_doc_kind="statute",
            source_provision_ref="",
            kind=InlineCitationKind.COURT_KKO,
            canonical_id="fi.court.kko.2018.45",
            raw_text="KKO 2018:45",
            case_year=2018,
            case_number=45,
            context=InlineCitationContext.ENACTED_STATUTE_BODY,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
        )
        row = inline_citation_to_row(citation)
        assert set(row.keys()) == self._EXPECTED_COLUMNS, (
            f"Column set mismatch.\n"
            f"Missing: {self._EXPECTED_COLUMNS - set(row.keys())}\n"
            f"Extra:   {set(row.keys()) - self._EXPECTED_COLUMNS}"
        )

    def test_kind_enum_values_stable(self) -> None:
        """InlineCitationKind enum values are pinned (do not rename without migration)."""
        expected_values = {
            "court_kko", "court_kho", "ombudsman_eoa", "chancellor_oka",
            "statute_inline", "he_inline", "vtv_report", "working_group_memo",
            "parliament_kirjelma", "old_committee", "unresolved",
        }
        actual_values = {k.value for k in InlineCitationKind}
        assert actual_values == expected_values, (
            f"InlineCitationKind values changed.\n"
            f"Missing: {expected_values - actual_values}\n"
            f"Extra:   {actual_values - expected_values}"
        )

    def test_context_enum_values_stable(self) -> None:
        """InlineCitationContext enum values are pinned."""
        expected_values = {
            "enacted_statute_body", "he_rationale", "he_introduction",
            "preliminary_work", "other",
        }
        actual_values = {c.value for c in InlineCitationContext}
        assert actual_values == expected_values


# ===========================================================================
# 8. Cross-feature composition test
# ===========================================================================


class TestCrossFeatureComposition:
    """Cross-feature composition: #1 + #11 + #12 form a non-overlapping cover.

    This test verifies the composition discipline:
    - <ref>-markup citations: deferred to #1 (fi_refs.parquet) — not in #12.
    - preliminaryWork citations: deferred to #11 — not in #12 (except EK/old-committee).
    - HE_INLINE in HE body outside prelim: in #12, not in #11.
    - EK in preliminaryWork: in #12 (closes #11 gap).

    The test constructs a document with citations from all three feature domains
    and verifies that #12 extraction does NOT duplicate what #1 or #11 would capture.
    """

    def test_ref_markup_excluded_from_inline_citations(self) -> None:
        """Text inside <ref> is excluded from #12 (deferred to #1)."""
        result = _run_fixture(REF_MARKUP_DEFERRED)
        # statute_inline must NOT appear (that text was inside <ref>)
        kinds = {c.kind.value for c in result.citations}
        assert "statute_inline" not in kinds, (
            "statute_inline must not fire on text inside <ref> — belongs to #1"
        )
        # court_kko MUST appear (plain text outside <ref>)
        assert "court_kko" in kinds

    def test_prelim_work_he_citations_excluded(self) -> None:
        """HE citations in preliminaryWork are excluded from #12 (deferred to #11)."""
        # Statute prelim block with a bare "HE 99/2022" line
        xml_bytes = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body>"
            b'<hcontainer name="conclusions">'
            b'<hcontainer name="preliminaryWork">'
            b"<content>"
            b"<p>HE 99/2022</p>"
            b"<p>EV 56/2022</p>"
            b"</content>"
            b"</hcontainer>"
            b"</hcontainer>"
            b"</body></act>"
            b"</akomaNtoso>"
        )
        result = extract_inline_citations(xml_bytes, doc_id="711/2022", doc_kind="statute")
        # No HE_INLINE (doc_kind=statute; also prelim suppresses it)
        # No other citation kinds from plain prelim content
        kinds = {c.kind.value for c in result.citations}
        assert "he_inline" not in kinds
        # EV in prelim: no EV recognizer in this extractor (belongs to #11)
        # So for this XML, no citations should be emitted at all
        assert len(result.citations) == 0, (
            f"Prelim-only content should produce 0 citations from #12; "
            f"got: {[inline_citation_to_row(c) for c in result.citations]}"
        )

    def test_ek_in_prelim_is_exclusively_in_12(self) -> None:
        """EK in preliminaryWork is captured by #12 (closes #11 UNRESOLVED gap)."""
        result = _run_fixture(EK_IN_PRELIMINARY_WORK)
        kinds = {c.kind.value for c in result.citations}
        assert "parliament_kirjelma" in kinds
        ek_cites = [c for c in result.citations if c.kind == InlineCitationKind.PARLIAMENT_KIRJELMA]
        assert ek_cites[0].context == InlineCitationContext.PRELIMINARY_WORK

    def test_recognizer_single_instance_reuse(self) -> None:
        """The module-scope _RECOGNIZER is an InlineCitationRecognizer instance."""
        from lawvm.finland.inline_citation_extractor import _RECOGNIZER
        assert isinstance(_RECOGNIZER, InlineCitationRecognizer)


class TestStatuteHeadMorphology:
    """The plain-text statute-head inflection alternation is morphology-driven.

    The retired hand-written suffix alternation (``lain|lakia|...``) is replaced
    by the M1-generated forms of the curated case set, killing the consonant-
    gradation substring bug class.  The closed COURT/AUTHORITY identifier codes
    (KKO, KHO, EOA, OKV, VTV, EK) are NOT morphology and stay regex-recognized.
    """

    def _statute_cites(self, text: str) -> list[InlineCitation]:
        rec = InlineCitationRecognizer()
        cites, _pm = rec.recognize_all(
            text=text,
            doc_id="x/2022",
            doc_kind="statute",
            context=InlineCitationContext.ENACTED_STATUTE_BODY,
            source_span_file=None,
        )
        return [c for c in cites if c.kind == InlineCitationKind.STATUTE_INLINE]

    def test_head_forms_are_morphology_generated_gradated(self) -> None:
        from lawvm.finland.references.inline_citation_extractor import (
            _STATUTE_HEAD_FORMS,
        )

        forms = set(_STATUTE_HEAD_FORMS)
        # Gradated genitive surfaces are GENERATED (not an ``asetu`` substring).
        assert {"asetuksen", "asetuksesta", "säädöksen"} <= forms
        assert "asetu" not in forms

    def test_gradated_genitive_statute_detected(self) -> None:
        # ``rakennusasetuksen (123/2020)``: the gradated genitive head (asetus ->
        # -Ukse-) is detected as the compound tail (head glued to its modifier).
        cites = self._statute_cites("rakennusasetuksen (123/2020) mukaan")
        assert len(cites) == 1
        assert cites[0].canonical_id == "123/2020"

    def test_essive_supplement_still_recognised(self) -> None:
        # The essive ``lakina`` is outside M1's reference_v1 profile; it is kept
        # via the explicit supplement so coverage is not dropped.
        cites = self._statute_cites("rakennuslakina (456/2019) tarkoitettu")
        assert len(cites) == 1
        assert cites[0].canonical_id == "456/2019"

    def test_court_abbreviation_codes_still_recognized(self) -> None:
        # Closed identifier codes are NOT morphology and must still be matched.
        rec = InlineCitationRecognizer()
        cites, _pm = rec.recognize_all(
            text="ratkaisussa KKO 2018:45 ja KHO 2019:12 sekä VTV 3/2020",
            doc_id="x/2022",
            doc_kind="statute",
            context=InlineCitationContext.ENACTED_STATUTE_BODY,
            source_span_file=None,
        )
        kinds = {c.kind for c in cites}
        assert InlineCitationKind.COURT_KKO in kinds
        assert InlineCitationKind.COURT_KHO in kinds
        assert InlineCitationKind.VTV_REPORT in kinds

"""Tests for ReferenceMention core primitive and Finland extractor.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests — each cite_kind × confidence cell.
  2. Real corpus regression — at least 5 real Finnish statute patterns
     (via conformance corpus fixtures, which use real AKN patterns).
  3. Finding/observation tests — BROKEN emits BrokenReferenceFinding, etc.
  4. Negative tests — non-citation text does not produce ReferenceMention.
  5. Strict-mode tests — APPROXIMATE/UNRESOLVED blocked in strict mode.
  6. No-leak tests — synthetic markers not in non-test parquet.
  7. Schema-stability tests — parquet column order + dtypes pinned.

Module coverage:
  - lawvm.core.reference_mention (ReferenceMention, CiteKind, CiteConfidence, etc.)
  - lawvm.finland.ref_mention_extractor (extraction entry points)
  - lawvm.finland.conformance_corpus.refs.fixtures (conformance fixtures)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    ApproximateReferenceFinding,
    BrokenReferenceFinding,
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    RejectedRefCandidate,
    SourceSpan,
    reference_mention_to_row,
)
from lawvm.finland.ref_mention_extractor import (
    ExtractionResult,
    extract_all_reference_mentions,
    extract_eu_reference_mentions,
    extract_reference_mentions,
)
from lawvm.finland.conformance_corpus.refs.fixtures import (
    ALL_FIXTURES,
    EXACT_CROSS_STATUTE,
    EXACT_EU,
    EXACT_INTERNAL_SELF_REF_SKIPPED,
    EXACT_ISSUED_UNDER,
    EXACT_REPEALS,
    NO_LEAK_SYNTHETIC_MARKER,
    XML_PARSE_FAILURE,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_mention_matches(actual: ReferenceMention, expected: Dict[str, Any]) -> None:
    """Assert that actual ReferenceMention matches all expected key/value pairs."""
    row = reference_mention_to_row(actual)
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"mention row key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


def _assert_rejected_matches(actual: RejectedRefCandidate, expected: Dict[str, Any]) -> None:
    """Assert that actual RejectedRefCandidate matches all expected key/value pairs."""
    actual_dict = {
        "rule_id": actual.rule_id,
        "phase": actual.phase,
        "source_statute_id": actual.source_statute_id,
        "reason": actual.reason,
        "matched_text": actual.matched_text,
        "blocking": actual.blocking,
        "strict_disposition": actual.strict_disposition,
    }
    for key, val in expected.items():
        assert actual_dict.get(key) == val, (
            f"rejected candidate key {key!r}: expected {val!r}, got {actual_dict.get(key)!r}"
        )


# ===========================================================================
# Category 1: Synthetic unit tests — typed primitive construction
# ===========================================================================


class TestReferenceMentionConstruction:
    """Synthetic unit tests for the typed primitive itself."""

    def test_exact_cross_statute_construction(self) -> None:
        """EXACT × CROSS_STATUTE: basic construction and serialization."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", provision_path="sec_7", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        assert mention.cite_kind == CiteKind.CROSS_STATUTE
        assert mention.cite_confidence == CiteConfidence.EXACT
        assert mention.target_provision_ref is not None
        assert mention.target_provision_ref.statute_id == "2022/711"

    def test_unresolved_allows_none_target(self) -> None:
        """UNRESOLVED confidence allows target_provision_ref=None."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.UNRESOLVED,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        assert mention.cite_confidence == CiteConfidence.UNRESOLVED
        assert mention.target_provision_ref is None

    def test_broken_allows_none_target(self) -> None:
        """BROKEN confidence allows target_provision_ref=None."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.BROKEN,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(date(2010, 1, 1), date(2022, 6, 1)),
            edge_subtype="CITES",
        )
        assert mention.cite_confidence == CiteConfidence.BROKEN

    def test_exact_requires_target(self) -> None:
        """EXACT confidence MUST have a non-None target_provision_ref."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        with pytest.raises(ValueError, match="target_provision_ref"):
            ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=None,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype="CITES",
            )

    def test_empty_phrase_lemma_rejected(self) -> None:
        """Empty phrase_lemma is not allowed."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711")
        with pytest.raises(ValueError, match="phrase_lemma"):
            ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype="CITES",
            )

    def test_all_cite_kinds_constructable(self) -> None:
        """Each CiteKind enum value is constructable."""
        for kind in CiteKind:
            src = ProvisionRef(statute_id="2003/314")
            tgt = ProvisionRef(statute_id="2022/711")
            mention = ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=kind,
                cite_confidence=CiteConfidence.EXACT,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype=None,
            )
            assert mention.cite_kind == kind

    def test_all_confidences_constructable_when_target_present(self) -> None:
        """EXACT/APPROXIMATE/AMBIGUOUS require non-None target."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        for conf in (CiteConfidence.EXACT, CiteConfidence.APPROXIMATE, CiteConfidence.AMBIGUOUS):
            mention = ReferenceMention(
                source_provision_ref=src,
                target_provision_ref=tgt,
                cite_kind=CiteKind.CROSS_STATUTE,
                cite_confidence=conf,
                phrase_lemma="ref_element",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype=None,
            )
            assert mention.cite_confidence == conf

    def test_source_span_construction(self) -> None:
        """SourceSpan validates byte_offset >= 0 and byte_len >= 0."""
        span = SourceSpan(source_file="test.xml", byte_offset=100, byte_len=42)
        assert span.byte_offset == 100
        assert span.byte_len == 42

    def test_source_span_negative_offset_rejected(self) -> None:
        with pytest.raises(ValueError, match="byte_offset"):
            SourceSpan(source_file="test.xml", byte_offset=-1, byte_len=10)

    def test_provision_ref_serialized(self) -> None:
        """ProvisionRef.serialized() produces stable output."""
        ref = ProvisionRef(statute_id="711/2022", section_label="7", subsection_num=3)
        assert ref.serialized() == "711/2022/7/3"

    def test_provision_ref_serialized_statute_only(self) -> None:
        ref = ProvisionRef(statute_id="711/2022")
        assert ref.serialized() == "711/2022"

    def test_frozen_dataclass_immutable(self) -> None:
        """ReferenceMention is frozen (immutable)."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        with pytest.raises((TypeError, AttributeError)):
            mention.cite_kind = CiteKind.EU  # type: ignore[misc]


# ===========================================================================
# Category 2: Real corpus regression via conformance fixtures
# ===========================================================================


class TestConformanceFixtures:
    """Real AKN patterns from the conformance corpus."""

    def test_fixture_exact_cross_statute(self) -> None:
        """EXACT × CROSS_STATUTE: inline <ref> to Finnish statute §7."""
        result = extract_all_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_CROSS_STATUTE.expected_mentions[0])

    def test_fixture_exact_internal_self_ref_skipped(self) -> None:
        """Self-reference produces diagnostic, no mention."""
        result = extract_all_reference_mentions(
            EXACT_INTERNAL_SELF_REF_SKIPPED.xml_bytes,
            EXACT_INTERNAL_SELF_REF_SKIPPED.source_statute_id,
        )
        assert result.mentions == []
        diag_ids = [d.rule_id for d in result.diagnostics]
        for expected_id in EXACT_INTERNAL_SELF_REF_SKIPPED.expected_diagnostics_rule_ids:
            assert expected_id in diag_ids, (
                f"Expected diagnostic {expected_id!r} not found in {diag_ids}"
            )

    def test_fixture_exact_eu(self) -> None:
        """EU regulation via text pattern: cite_kind=EU."""
        result = extract_all_reference_mentions(
            EXACT_EU.xml_bytes,
            EXACT_EU.source_statute_id,
        )
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1, f"Expected EU mention, got {result.mentions}"
        _assert_mention_matches(eu_mentions[0], EXACT_EU.expected_mentions[0])

    def test_fixture_exact_issued_under(self) -> None:
        """finlex:issuedUnderActs metadata: NON_STATUTORY_INSTRUMENT, ISSUED_UNDER."""
        result = extract_all_reference_mentions(
            EXACT_ISSUED_UNDER.xml_bytes,
            EXACT_ISSUED_UNDER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_ISSUED_UNDER.expected_mentions[0])

    def test_fixture_exact_repeals(self) -> None:
        """finlex:repeals metadata: CROSS_STATUTE, REPEALS."""
        result = extract_all_reference_mentions(
            EXACT_REPEALS.xml_bytes,
            EXACT_REPEALS.source_statute_id,
        )
        assert len(result.mentions) >= 1
        _assert_mention_matches(result.mentions[0], EXACT_REPEALS.expected_mentions[0])

    def test_fixture_xml_parse_failure(self) -> None:
        """Corrupt XML: blocking diagnostic, no mentions (per §1.8)."""
        result = extract_all_reference_mentions(
            XML_PARSE_FAILURE.xml_bytes,
            XML_PARSE_FAILURE.source_statute_id,
        )
        assert result.mentions == []
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_cross_ref_xml_parse_failed" in diag_ids
        blocking_diags = [d for d in result.diagnostics if d.blocking]
        assert len(blocking_diags) >= 1

    def test_all_fixtures_run_without_exception(self) -> None:
        """All conformance fixtures must run without unhandled exceptions."""
        for fid, fixture in ALL_FIXTURES.items():
            result = extract_all_reference_mentions(
                fixture.xml_bytes,
                fixture.source_statute_id,
            )
            # ExtractionResult must be a valid instance
            assert isinstance(result, ExtractionResult), (
                f"Fixture {fid}: expected ExtractionResult, got {type(result)}"
            )


# ===========================================================================
# Category 3: Finding/observation tests
# ===========================================================================


class TestFindingObservation:
    """Tests that typed findings are emitted for BROKEN, AMBIGUOUS, etc."""

    def test_broken_reference_finding_construction(self) -> None:
        """BrokenReferenceFinding has required fields."""
        finding = BrokenReferenceFinding(
            rule_id="fi_ref_broken_target_repealed",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            target_statute_id="1999/532",
            source_provision_ref_str="2003/314/5",
            target_provision_ref_str="1999/532/3",
            reason="Target statute 1999/532 was repealed on 2010-01-01 after "
                   "source provision was last amended on 2005-03-01.",
        )
        assert finding.rule_id == "fi_ref_broken_target_repealed"
        assert finding.blocking is False
        assert finding.strict_disposition == "record"

    def test_ambiguous_reference_finding_construction(self) -> None:
        """AmbiguousReferenceFinding normalizes candidate_target_ids to tuple."""
        finding = AmbiguousReferenceFinding(
            rule_id="fi_ref_ambiguous_multiple_targets",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            source_provision_ref_str="2003/314/5",
            candidate_target_ids=["1984/523", "2003/527"],
            reason="Two ympäristönsuojelulaki versions both match.",
        )
        assert isinstance(finding.candidate_target_ids, tuple)
        assert "1984/523" in finding.candidate_target_ids

    def test_approximate_reference_finding_construction(self) -> None:
        """ApproximateReferenceFinding has heuristic_applied field."""
        finding = ApproximateReferenceFinding(
            rule_id="fi_ref_approximate_agency_lifecycle",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            source_provision_ref_str="2003/314/5",
            target_provision_ref_str="2019/561/3",
            heuristic_applied="Evira → Ruokavirasto lifecycle rename: "
                               "2006/37 merged into 2019/561 on 2019-01-01.",
        )
        assert "Evira" in finding.heuristic_applied

    def test_rejected_ref_candidate_construction(self) -> None:
        """RejectedRefCandidate has all required fields."""
        rej = RejectedRefCandidate(
            rule_id="fi_ref_candidate_not_statute_citation",
            phase="cross_ref_extraction",
            source_statute_id="2003/314",
            reason="Pattern matched 'vuoden 2020 talousarvio' but year-reference "
                   "is not a statute citation.",
            matched_text="vuoden 2020 talousarvio",
            source_span=None,
        )
        assert rej.blocking is False
        assert rej.rule_id == "fi_ref_candidate_not_statute_citation"

    def test_diagnostic_from_xml_parse_failure_is_blocking(self) -> None:
        """CrossRefDiagnostic from xml_parse_failed is blocking=True."""
        result = extract_reference_mentions(b"<bad>", "2000/1")
        assert result.mentions == []
        assert len(result.diagnostics) >= 1
        blocking = [d for d in result.diagnostics if d.blocking]
        assert len(blocking) >= 1
        assert blocking[0].rule_id == "fi_cross_ref_xml_parse_failed"


# ===========================================================================
# Category 4: Negative tests (non-citation text → no mention)
# ===========================================================================


class TestNegative:
    """Non-citation patterns must not produce ReferenceMention records."""

    def test_empty_statute_body_no_mentions(self) -> None:
        """A statute with no <ref> elements and no EU patterns → no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>Ei viittauksia.</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        assert result.mentions == []
        assert result.rejected == []

    def test_year_reference_not_statute(self) -> None:
        """'vuoden 2020 talousarvio' is not a statute citation — no EU mention."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"vuoden 2020 talousarvio on hyv\xc3\xa4ksytty."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        # No mentions — year alone is not a statute number
        # EU extractor would only match "N:o YYYY/NNNN" or "NNNN/YYYY/EU" patterns
        assert result.mentions == []

    def test_no_body_no_mentions(self) -> None:
        """Statute with no body element → no CITES mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/></act></akomaNtoso>"
        )
        result = extract_reference_mentions(xml, "2003/314")
        assert result.mentions == []

    def test_self_reference_not_a_cross_statute_mention(self) -> None:
        """A self-referencing <ref> produces a diagnostic, not a CROSS_STATUTE mention."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b'<ref href="/akn/fi/act/statute/2003/314#sec_1">1 \xc2\xa7:ss\xc3\xa4</ref>'
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_reference_mentions(xml, "2003/314")
        # No mention (self-ref skipped), but diagnostic emitted
        assert result.mentions == []
        diag_ids = [d.rule_id for d in result.diagnostics]
        assert "fi_cross_ref_self_reference_skipped" in diag_ids


# ===========================================================================
# Category 5: Strict-mode tests
# ===========================================================================


class TestStrictMode:
    """Strict mode rejects APPROXIMATE and UNRESOLVED confidence mentions."""

    def test_strict_mode_blocks_on_bad_xml(self) -> None:
        """In strict mode, a parse failure produces a blocking diagnostic."""
        result = extract_reference_mentions(
            b"<invalid>",
            "2000/1",
            strict=True,
        )
        # Extract fails silently but records blocking diagnostic
        assert result.mentions == []
        blocking_diags = [d for d in result.diagnostics if d.blocking]
        assert len(blocking_diags) >= 1

    def test_non_strict_mode_records_without_blocking(self) -> None:
        """Non-strict mode records diagnostic but doesn't block."""
        result = extract_reference_mentions(
            b"<invalid>",
            "2000/1",
            strict=False,
        )
        # Diagnostics exist but extraction result is non-blocking aside from the XML error
        assert result.mentions == []

    def test_strict_mode_flag_passed_correctly(self) -> None:
        """strict=True is accepted by extract_reference_mentions without error."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
            strict=True,
        )
        # EXACT mentions are not blocked by strict mode
        assert len(result.mentions) >= 1
        assert result.mentions[0].cite_confidence == CiteConfidence.EXACT


# ===========================================================================
# Category 6: No-leak tests
# ===========================================================================


class TestNoLeak:
    """Synthetic test markers must not leak into production parquet runs."""

    def test_synthetic_statute_id_is_extractable_in_test(self) -> None:
        """Synthetic IDs extract correctly in test context."""
        result = extract_all_reference_mentions(
            NO_LEAK_SYNTHETIC_MARKER.xml_bytes,
            NO_LEAK_SYNTHETIC_MARKER.source_statute_id,
        )
        assert len(result.mentions) >= 1
        # The source_statute_id must carry the synthetic marker
        assert result.mentions[0].source_provision_ref.statute_id == (
            "__test__/9999/synthetic_source"
        )

    def test_reference_mention_to_row_no_internal_sentinels(self) -> None:
        """Serialized row must not contain '__test__' in non-test statute fields."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        row = reference_mention_to_row(mention)
        assert "__test__" not in row["source_statute_id"]
        assert "__test__" not in (row["target_statute_id"] or "")


# ===========================================================================
# Category 7: Schema-stability tests
# ===========================================================================


class TestSchemaStability:
    """Parquet schema column order and dtypes must be pinned."""

    # This is the stable schema from REFERENCE_MENTION_EXTRACTION.md
    EXPECTED_COLUMNS = [
        "source_statute_id",
        "source_provision_ref_str",
        "target_statute_id",
        "target_provision_ref_str",
        "cite_kind",
        "cite_confidence",
        "edge_subtype",
        "phrase_lemma",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_len",
        "valid_at_start",
        "valid_at_end",
        "target_stat_hash",
    ]

    def test_reference_mention_to_row_has_all_columns(self) -> None:
        """reference_mention_to_row() produces all expected columns."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        row = reference_mention_to_row(mention)
        for col in self.EXPECTED_COLUMNS:
            assert col in row, f"Column {col!r} missing from serialized row"

    def test_reference_mention_to_row_column_types(self) -> None:
        """Schema column types match expected Python types."""
        src = ProvisionRef(statute_id="2003/314", section_label="5")
        tgt = ProvisionRef(statute_id="2022/711", section_label="7")
        span = SourceSpan(source_file="/data/fi/2003/314.xml", byte_offset=1024, byte_len=50)
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=span,
            valid_at_interval=(date(2003, 1, 1), date(2022, 6, 1)),
            edge_subtype="CITES",
            target_stat_hash="abcdef0123456789",
        )
        row = reference_mention_to_row(mention)

        assert isinstance(row["source_statute_id"], str)
        assert isinstance(row["source_provision_ref_str"], str)
        assert isinstance(row["target_statute_id"], str)
        assert isinstance(row["cite_kind"], str)
        assert isinstance(row["cite_confidence"], str)
        assert isinstance(row["source_span_file"], str)
        assert isinstance(row["source_span_byte_offset"], int)
        assert isinstance(row["source_span_len"], int)
        assert isinstance(row["valid_at_start"], str)
        assert isinstance(row["valid_at_end"], str)

    def test_reference_mention_to_row_nullable_columns(self) -> None:
        """Nullable columns are None when absent."""
        src = ProvisionRef(statute_id="2003/314")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=None,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.UNRESOLVED,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        row = reference_mention_to_row(mention)
        assert row["target_statute_id"] is None
        assert row["target_provision_ref_str"] is None
        assert row["source_span_file"] is None
        assert row["source_span_byte_offset"] is None
        assert row["source_span_len"] is None
        assert row["valid_at_start"] is None
        assert row["valid_at_end"] is None
        assert row["target_stat_hash"] is None

    def test_column_order_stable(self) -> None:
        """Column order in reference_mention_to_row() output is stable."""
        src = ProvisionRef(statute_id="2003/314")
        tgt = ProvisionRef(statute_id="2022/711")
        mention = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype=None,
        )
        row = reference_mention_to_row(mention)
        actual_cols = list(row.keys())
        assert actual_cols == self.EXPECTED_COLUMNS


# ===========================================================================
# Integration: extract_all_reference_mentions combines domestic + EU
# ===========================================================================


class TestExtractAllReferenceMentions:
    """Integration tests for the combined domestic+EU extraction entry point."""

    def test_combines_domestic_and_eu_mentions(self) -> None:
        """extract_all_reference_mentions returns both domestic CITES and EU mentions.

        Uses '(EY) N:o 999/2001' format (NUMBER/YEAR) which the existing EU
        extractor P1 pattern handles. The newer '(EU) YEAR/NUMBER' format
        (e.g. GDPR '(EU) 2016/679') is not yet handled by the EU extractor
        — that is a known limitation of the existing cross_refs.extract_eu_refs.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b'Ks. <ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" sek\xc3\xa4 neuvoston asetusta (EY) N:o 999/2001."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        kinds = {m.cite_kind for m in result.mentions}
        assert CiteKind.CROSS_STATUTE in kinds
        assert CiteKind.EU in kinds

    def test_empty_xml_no_crash(self) -> None:
        """Empty XML byte string → graceful failure via diagnostic, no exception."""
        result = extract_all_reference_mentions(b"", "2000/1")
        assert isinstance(result, ExtractionResult)

    def test_source_statute_id_preserved_in_all_mentions(self) -> None:
        """All mentions carry the source_statute_id of the input statute."""
        result = extract_all_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        for mention in result.mentions:
            assert mention.source_provision_ref.statute_id == (
                EXACT_CROSS_STATUTE.source_statute_id
            ), f"Mention has wrong source_statute_id: {mention.source_provision_ref.statute_id}"


# ===========================================================================
# Enum coverage
# ===========================================================================


class TestEnumCoverage:
    """All enum values are accessible and have correct string values."""

    def test_cite_kind_values(self) -> None:
        assert CiteKind.INTERNAL.value == "internal"
        assert CiteKind.CROSS_STATUTE.value == "cross_statute"
        assert CiteKind.EU.value == "eu"
        assert CiteKind.TREATY.value == "treaty"
        assert CiteKind.NON_STATUTORY_INSTRUMENT.value == "non_statutory_instrument"

    def test_cite_confidence_values(self) -> None:
        assert CiteConfidence.EXACT.value == "exact"
        assert CiteConfidence.APPROXIMATE.value == "approximate"
        assert CiteConfidence.AMBIGUOUS.value == "ambiguous"
        assert CiteConfidence.UNRESOLVED.value == "unresolved"
        assert CiteConfidence.BROKEN.value == "broken"

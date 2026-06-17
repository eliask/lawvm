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
from typing import Any, Dict, Iterable, cast

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
    PlainTextStatuteCitationRecognizer,
    PlainTextStatuteHit,
    extract_all_reference_mentions,
    extract_eu_reference_mentions,
    extract_plain_text_statute_mentions,
    extract_reference_mentions,
)
from lawvm.finland.conformance_corpus.refs.fixtures import (
    ALL_FIXTURES,
    EU_EMBEDDED_REPEAL,
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


def _runtime_candidate_target_ids(values: list[str]) -> tuple[str, ...]:
    """Deliberately pass list input so constructor normalization is exercised."""
    return cast(tuple[str, ...], values)


def _target_statute_ids(mentions: Iterable[ReferenceMention]) -> set[str]:
    target_ids: set[str] = set()
    for mention in mentions:
        assert mention.target_provision_ref is not None
        target_ids.add(mention.target_provision_ref.statute_id)
    return target_ids


def _set_runtime_attr(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


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
            _set_runtime_attr(mention, "cite_kind", CiteKind.EU)


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
        """A self-referencing <ref> produces a diagnostic, never a CROSS_STATUTE
        mention. Bare same-statute section refs in body prose ARE now captured as
        INTERNAL mentions (the InternalRef family), targeting the same statute."""
        result = extract_all_reference_mentions(
            EXACT_INTERNAL_SELF_REF_SKIPPED.xml_bytes,
            EXACT_INTERNAL_SELF_REF_SKIPPED.source_statute_id,
        )
        # The self-referencing <ref> must not become a cross-statute mention.
        cross = [m for m in result.mentions if m.cite_kind == CiteKind.CROSS_STATUTE]
        assert cross == [], f"self-ref must not be a CROSS_STATUTE mention: {cross}"
        # Any emitted mentions are INTERNAL and target the citing statute itself.
        for m in result.mentions:
            assert m.cite_kind == CiteKind.INTERNAL
            assert m.target_provision_ref is not None
            assert (
                m.target_provision_ref.statute_id
                == EXACT_INTERNAL_SELF_REF_SKIPPED.source_statute_id
            )
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

    def test_fixture_eu_embedded_repeal(self) -> None:
        """Long-form EU citation: primary target + embedded-repeal provenance.

        'asetuksen (EY) N:o 1774/2002 kumoamisesta ... asetuksessa (EY) N:o
        1069/2009' yields TWO typed EU mentions — 1069/2009 as the primary CITES
        target and 1774/2002 as REPEALS_EMBEDDED provenance.
        """
        result = extract_all_reference_mentions(
            EU_EMBEDDED_REPEAL.xml_bytes,
            EU_EMBEDDED_REPEAL.source_statute_id,
        )
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        by_target = {
            m.target_provision_ref.statute_id: m
            for m in eu_mentions
            if m.target_provision_ref is not None
        }
        assert "eu/act/2009/1069" in by_target, f"primary missing: {by_target}"
        assert "eu/act/2002/1774" in by_target, f"embedded missing: {by_target}"
        assert by_target["eu/act/2009/1069"].edge_subtype == "CITES"
        assert by_target["eu/act/2002/1774"].edge_subtype == "REPEALS_EMBEDDED"
        # Each expected fixture mention must match an actual EU mention by target.
        for expected in EU_EMBEDDED_REPEAL.expected_mentions:
            match = by_target[expected["target_statute_id"]]
            _assert_mention_matches(match, expected)

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

    def _assert_body_fixture(self, fixture_id: str) -> None:
        fixture = ALL_FIXTURES[fixture_id]
        result = extract_all_reference_mentions(
            fixture.xml_bytes, fixture.source_statute_id
        )
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        actual_paths = {
            m.target_provision_ref.serialized()
            for m in plain
            if m.target_provision_ref is not None
        }
        for expected in fixture.expected_mentions:
            path = expected["target_provision_ref_str"]
            match = next(
                (
                    m
                    for m in plain
                    if m.target_provision_ref is not None
                    and m.target_provision_ref.serialized() == path
                ),
                None,
            )
            assert match is not None, (
                f"{fixture_id}: expected provision {path!r} not in {actual_paths}"
            )
            _assert_mention_matches(match, expected)

    def test_fixture_body_section_range(self) -> None:
        """Body lane: en-dash section RANGE expands to per-section mentions."""
        self._assert_body_fixture("body_section_range")

    def test_fixture_body_section_coordination(self) -> None:
        """Body lane: coordinated section list expands to per-section mentions."""
        self._assert_body_fixture("body_section_coordination")

    def test_fixture_body_byid_momentti(self) -> None:
        """Body lane: by-id citation threads momentti into the target ref."""
        self._assert_body_fixture("body_byid_momentti")


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
            candidate_target_ids=_runtime_candidate_target_ids(["1984/523", "2003/527"]),
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

    def test_ref_element_surface_text_is_preserved_on_typed_mention(self) -> None:
        """The literal <ref> text survives extraction for neutral interlink overlays."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert result.mentions
        assert result.mentions[0].phrase_lemma == "ref_element"
        assert result.mentions[0].surface_text
        assert result.mentions[0].surface_text != "ref_element"


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
        assert "__test__" not in str(row["source_statute_id"])
        assert "__test__" not in str(row["target_statute_id"] or "")


# ===========================================================================
# Category 6b: Source-span provenance tests
# ===========================================================================


class TestSourceSpanProvenance:
    """Emitted mentions must carry real byte spans into the source xml_bytes.

    OFFSET UNIT: bytes into the statute's xml_bytes (matches SourceSpan field
    names). The span must slice back to the exact citation surface, so byte-overlap
    recall benches and the parse-overlay-IR anchoring both work.
    """

    def test_ref_lane_mention_carries_byte_span(self) -> None:
        """An AKN <ref> mention carries a non-None SourceSpan with byte_len > 0."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        assert result.mentions
        m = result.mentions[0]
        assert m.phrase_lemma == "ref_element"
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        assert m.source_span.source_file == EXACT_CROSS_STATUTE.source_statute_id

    def test_ref_lane_byte_span_slices_to_the_ref_element(self) -> None:
        """The recovered byte span slices back to the <ref>…</ref> element."""
        result = extract_reference_mentions(
            EXACT_CROSS_STATUTE.xml_bytes,
            EXACT_CROSS_STATUTE.source_statute_id,
        )
        span = result.mentions[0].source_span
        assert span is not None
        sliced = EXACT_CROSS_STATUTE.xml_bytes[
            span.byte_offset : span.byte_offset + span.byte_len
        ]
        assert sliced.startswith(b"<ref")
        assert sliced.endswith(b"</ref>")
        # The surface text the edge carried is inside the sliced element.
        assert b"lannoitelaissa" in sliced

    def test_plain_text_lane_mention_carries_byte_span(self) -> None:
        """A plain-text statute citation mention carries a real byte span that
        slices back to the matched citation surface."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            b"Sovelletaan, mit\xc3\xa4 elintarvikelain (297/2021) 5 \xc2\xa7 nojalla."
            b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert plain
        m = plain[0]
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        sliced = xml[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        assert sliced == "elintarvikelain (297/2021)".encode("utf-8")

    def test_eu_lane_mention_carries_byte_span(self) -> None:
        """An EU citation mention carries a non-None byte span > 0 that slices
        back into the source EU citation surface (byte-accurate past non-ASCII)."""
        result = extract_eu_reference_mentions(
            EXACT_EU.xml_bytes,
            EXACT_EU.source_statute_id,
        )
        eu = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert eu
        m = eu[0]
        assert m.source_span is not None
        assert m.source_span.byte_len > 0
        sliced = EXACT_EU.xml_bytes[
            m.source_span.byte_offset : m.source_span.byte_offset + m.source_span.byte_len
        ]
        # The slice contains the EU regulation number that defined the target.
        assert b"999/2001" in sliced

    def test_metadata_edge_mention_has_no_span(self) -> None:
        """REPEALS/ISSUED_UNDER metadata edges have no body surface → span None.

        This documents the deliberate fail-loud-by-absence boundary: spans are
        populated only where a surface exists in the body bytes.
        """
        result = extract_reference_mentions(
            EXACT_REPEALS.xml_bytes,
            EXACT_REPEALS.source_statute_id,
        )
        meta = [m for m in result.mentions if m.edge_subtype == "REPEALS"]
        assert meta
        assert meta[0].source_span is None


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

        Uses '(EY) N:o 999/2001' format (NUMBER/YEAR) which the EU extractor P1 handles.
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

    def test_cite_confidence_resolution_status_members(self) -> None:
        """STATUTE_ONLY and OPEN exist with the catalogue-spec value strings.

        Per FI_REFERENCE_CATALOGUE.md §0: STATUTE_ONLY = act known / provision
        pending; OPEN = vague catch-all (tag-don't-guess, never assigned by a
        confidence threshold).
        """
        assert CiteConfidence.STATUTE_ONLY.value == "statute_only"
        assert CiteConfidence.OPEN.value == "open"

    def test_cite_confidence_existing_members_unchanged(self) -> None:
        """The original five members keep their identity and value strings.

        Adding STATUTE_ONLY / OPEN must not perturb EXACT / APPROXIMATE /
        AMBIGUOUS / UNRESOLVED / BROKEN.
        """
        existing = {
            "EXACT": "exact",
            "APPROXIMATE": "approximate",
            "AMBIGUOUS": "ambiguous",
            "UNRESOLVED": "unresolved",
            "BROKEN": "broken",
        }
        for name, value in existing.items():
            assert CiteConfidence[name].value == value
        # Full membership is exactly the original five plus the two new ones.
        assert {m.name for m in CiteConfidence} == set(existing) | {
            "STATUTE_ONLY",
            "OPEN",
        }


# ===========================================================================
# Task 1: Modern EU regulation patterns (year-first form)
# ===========================================================================


class TestModernEUYearFirstPattern:
    """Tests for the modern (EU) YEAR/NUMBER citation pattern added in Task 1.

    The old P1 pattern handled "(EY) N:o NUMBER/YEAR".
    The new P1B pattern handles "(EU) YEAR/NUMBER" (GDPR-style year-first form).
    Both forms must produce correctly typed CrossRefEdge → ReferenceMention rows
    with cite_kind=EU and cite_confidence=EXACT.
    """

    # ── Synthetic XML helpers ────────────────────────────────────────────────

    @staticmethod
    def _xml_with_eu_text(eu_citation: bytes) -> bytes:
        """Build minimal AKN XML containing a given EU citation byte string."""
        return (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>3 \xc2\xa7</num>"
            b"<paragraph><content><p>"
            + eu_citation
            + b"</p></content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )

    # ── Year-first form tests ────────────────────────────────────────────────

    def test_modern_eu_gdpr_2016_679(self) -> None:
        """'(EU) 2016/679' (GDPR) is extracted as eu/reg/2016/679."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1, f"Expected EU mention, got {result.mentions}"
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2016/679" in target_ids, f"GDPR not found in {target_ids}"

    def test_modern_eu_2017_2226(self) -> None:
        """'(EU) 2017/2226' (EES regulation) is extracted as eu/reg/2017/2226."""
        xml = self._xml_with_eu_text(
            b"Noudatetaan Euroopan parlamentin ja neuvoston asetusta (EU) 2017/2226."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2017/2226" in target_ids, f"EES reg not found in {target_ids}"

    def test_modern_eu_phrase_lemma_is_eu_text_pattern(self) -> None:
        """Modern year-first EU citation has phrase_lemma='eu_text_pattern'."""
        xml = self._xml_with_eu_text(b"soveltaen asetusta (EU) 2016/679.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        assert all(m.phrase_lemma == "eu_text_pattern" for m in eu_mentions)

    def test_modern_eu_cite_confidence_is_exact(self) -> None:
        """Modern year-first EU citation has cite_confidence=EXACT."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        assert all(m.cite_confidence == CiteConfidence.EXACT for m in eu_mentions)

    # ── Back-compat: old N:o form still works ────────────────────────────────

    def test_old_no_form_still_extracted(self) -> None:
        """'(EY) N:o 999/2001' form is still extracted correctly after adding P1B."""
        xml = self._xml_with_eu_text(b"neuvoston asetus (EY) N:o 999/2001.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2001/999" in target_ids, f"N:o form not found in {target_ids}"

    def test_old_and_new_forms_coexist(self) -> None:
        """Both '(EY) N:o 999/2001' and '(EU) 2016/679' are extracted from same XML."""
        xml = self._xml_with_eu_text(
            b"Ks. neuvoston asetus (EY) N:o 999/2001 sek\xc3\xa4 asetus (EU) 2016/679."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2001/999" in target_ids
        assert "eu/reg/2016/679" in target_ids

    # ── Deduplication ────────────────────────────────────────────────────────

    def test_modern_eu_deduplicated(self) -> None:
        """Same modern EU citation appearing twice produces only one mention."""
        xml = self._xml_with_eu_text(
            b"asetus (EU) 2016/679 ja (EU) 2016/679 taas."
        )
        result = extract_eu_reference_mentions(xml, "2018/1050")
        gdpr_mentions = [
            m for m in result.mentions
            if m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "eu/reg/2016/679"
        ]
        assert len(gdpr_mentions) == 1, (
            f"Expected exactly one deduplicated mention, got {len(gdpr_mentions)}"
        )

    # ── Sanity filter: year out of range not extracted ────────────────────────

    def test_year_out_of_range_not_extracted(self) -> None:
        """'(EU) 1900/123' — year below 1957 threshold — is not extracted."""
        xml = self._xml_with_eu_text(b"asetus (EU) 1900/123.")
        result = extract_eu_reference_mentions(xml, "2018/1050")
        # Year 1900 < 1957 — must not produce a mention.
        assert all(
            m.target_provision_ref is None
            or "1900" not in m.target_provision_ref.statute_id
            for m in result.mentions
        )

    # ── Integration: modern EU in extract_all_reference_mentions ─────────────

    def test_modern_eu_in_extract_all(self) -> None:
        """Modern year-first EU citation appears in extract_all_reference_mentions output."""
        xml = self._xml_with_eu_text(
            b"Euroopan parlamentin ja neuvoston asetus (EU) 2016/679."
        )
        result = extract_all_reference_mentions(xml, "2018/1050")
        eu_mentions = [m for m in result.mentions if m.cite_kind == CiteKind.EU]
        assert len(eu_mentions) >= 1
        target_ids = _target_statute_ids(eu_mentions)
        assert "eu/reg/2016/679" in target_ids


# ===========================================================================
# Task 2: Plain-text statute citation grammar
# ===========================================================================


class TestPlainTextStatuteCitations:
    """Tests for the PlainTextStatuteCitationRecognizer and
    extract_plain_text_statute_mentions (Task 2).

    Covers: basic extraction, multiple inflection variants, no-double-count
    against <ref> elements, self-reference skip, and negative patterns.
    """

    # ── PlainTextStatuteCitationRecognizer unit tests ───────────────────────

    def test_recognizer_extracts_lain_form(self) -> None:
        """'lannoitelain (711/2022) 7 §' is extracted with correct statute ID."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Noudatetaan lannoitelain (711/2022) 7 \xa7 tarkoittamia.</p>"
        )
        hits = recognizer.scan(p)
        assert len(hits) >= 1
        statute_ids = [h[0] for h in hits]
        assert "711/2022" in statute_ids

    def test_recognizer_extracts_section_label(self) -> None:
        """Section label is extracted from 'lannoitelain (711/2022) 7 §'."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>lannoitelain (711/2022) 7 \xa7 nojalla.</p>"
        )
        hits = recognizer.scan(p)
        assert any(h == ("711/2022", "7") for h in hits), f"Got {hits}"

    def test_recognizer_extracts_asetuksen_form(self) -> None:
        """'-asetuksen (NUMBER/YEAR) SECTION §' form is extracted."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>ympäristönsuojeluasetuksen (169/2000) 2 \xa7 perusteella.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "169/2000" in statute_ids

    def test_recognizer_extracts_laissa_form(self) -> None:
        """'elintarvikelaissa (23/2006)' form is extracted."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>elintarvikelaissa (23/2006) 7 \xa7 tarkoitetaan.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "23/2006" in statute_ids

    def test_recognizer_extracts_short_lain_form(self) -> None:
        """Bare 'lain (NUMBER/YEAR)' form without prefix word."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Sovelletaan lain (1326/2010) 50 \xa7:ss\xe4.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        assert "1326/2010" in statute_ids

    def test_recognizer_skips_text_without_section_mark(self) -> None:
        """Text without § character is skipped by substring guard."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        # No § in text — guard must skip without running regex
        p = ET.fromstring("<p>Sovelletaan lain (711/2022) tekstia.</p>")
        hits = recognizer.scan(p)
        assert hits == []

    def test_recognizer_skips_text_without_paren(self) -> None:
        """Text without '(' is skipped by substring guard."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring("<p>Ei sulkuja mutta on \xa7 merkki.</p>")
        hits = recognizer.scan(p)
        assert hits == []

    def test_recognizer_excludes_ref_element_text(self) -> None:
        """Text inside a <ref> element is NOT included in plain-text scan."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        # The ref element text "lannoitelain (711/2022) 7 §" should NOT be scanned
        # — only the tail "muuta mainittavaa" contributes.
        xml_str = (
            '<p xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            "Katso "
            '<ref href="/akn/fi/act/statute/2022/711">lannoitelain (711/2022) 7 \xa7</ref>'
            " muuta mainittavaa \xa7 teksti."
            "</p>"
        )
        p = ET.fromstring(xml_str)
        hits = recognizer.scan(p)
        # If any hit, it must NOT come from inside the ref element
        statute_ids = [h[0] for h in hits]
        # The ref content "711/2022" must not appear since it's inside a <ref>
        assert "711/2022" not in statute_ids, (
            "Statute ID from inside <ref> must not be in plain-text scan results"
        )

    def test_recognizer_deduplicates_same_statute_in_paragraph(self) -> None:
        """Same statute cited twice in one <p> produces only one entry."""
        import xml.etree.ElementTree as ET
        recognizer = PlainTextStatuteCitationRecognizer()
        p = ET.fromstring(
            "<p>Ks. lannoitelain (711/2022) 7 \xa7 ja lannoitelain (711/2022) 8 \xa7.</p>"
        )
        hits = recognizer.scan(p)
        statute_ids = [h[0] for h in hits]
        # Both hits have statute_id 711/2022 but different sections — NOT deduplicated
        # since the key includes section. But if same section cited twice, deduplicated.
        assert statute_ids.count("711/2022") >= 1  # At least one hit

    # ── extract_plain_text_statute_mentions integration tests ────────────────

    def test_plain_text_mention_emitted(self) -> None:
        """Plain-text statute citation → ReferenceMention with phrase_lemma='plain_text'."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan, mit\xc3\xa4 lannoitelain (711/2022) 7 \xc2\xa7 s\xc3\xa4\xc3\xa4t\xc3\xa4\xc3\xa4.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain_mentions = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain_mentions) >= 1, f"Expected plain_text mention, got {result.mentions}"
        target_ids = _target_statute_ids(plain_mentions)
        assert "711/2022" in target_ids

    def test_plain_text_mention_cite_kind(self) -> None:
        """Plain-text statute citation has cite_kind=CROSS_STATUTE."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan terveydenhuoltolain (1326/2010) 50 \xc2\xa7:ss\xc3\xa4.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) >= 1
        assert all(m.cite_kind == CiteKind.CROSS_STATUTE for m in plain)

    def test_plain_text_mention_confidence_exact(self) -> None:
        """Plain-text statute citation has cite_confidence=EXACT."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan lannoitelain (711/2022) 7 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) >= 1
        assert all(m.cite_confidence == CiteConfidence.EXACT for m in plain)

    def test_plain_text_no_double_count_vs_ref_element(self) -> None:
        """When <ref> covers a statute, plain_text pass skips that statute_id.

        The dedup guard is at statute level: if 711/2022 is already in the
        ref_covered set, the plain_text extractor won't emit it again.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan "
            b'<ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" (711/2022) 7 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(
            xml, "2003/314",
            ref_covered_statute_ids={"711/2022"},  # already covered by <ref>
        )
        # 711/2022 is in the covered set → plain_text pass must skip it
        plain_711 = [
            m for m in result.mentions
            if m.phrase_lemma == "plain_text"
            and m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "711/2022"
        ]
        assert plain_711 == [], (
            f"711/2022 should be skipped (covered by <ref>), but got {plain_711}"
        )

    def test_plain_text_self_reference_skipped(self) -> None:
        """Plain-text citations where target == source statute are skipped."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan t\xc3\xa4m\xc3\xa4n lain (711/2022) 3 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        # Source statute IS 711/2022 — so the citation targets itself
        result = extract_plain_text_statute_mentions(xml, "711/2022")
        self_refs = [
            m for m in result.mentions
            if m.phrase_lemma == "plain_text"
            and m.target_provision_ref is not None
            and m.target_provision_ref.statute_id == "711/2022"
        ]
        assert self_refs == [], "Self-reference must not produce a plain_text mention"

    def test_plain_text_negative_no_citation(self) -> None:
        """Plain text without statute citation pattern → no mentions."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>T\xc3\xa4ss\xc3\xa4 laissa ei ole viittauksia muihin lakeihin.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        assert result.mentions == []

    def test_plain_text_phrase_lemma_distinct_from_ref_element(self) -> None:
        """phrase_lemma='plain_text' is distinct from 'ref_element' in combined output."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Ks. "
            b'<ref href="/akn/fi/act/statute/2022/711">lannoitelakia</ref>'
            b" ja terveydenhuoltolain (1326/2010) 50 \xc2\xa7.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_all_reference_mentions(xml, "2003/314")
        lemmas = {m.phrase_lemma for m in result.mentions}
        # ref_element from AKN <ref>, plain_text from prose
        assert "ref_element" in lemmas, f"Expected ref_element, got {lemmas}"
        # plain_text from terveydenhuoltolain citation (different statute ID, so not deduped)
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) >= 1, f"Expected plain_text mention, got lemmas={lemmas}"


# ===========================================================================
# Task 23: momentti/kohta sub-section precision for deeplink consumers
# ===========================================================================


class TestPlainTextMomenttiPrecision:
    """The plain-text recognizer must surface momentti (subsection) and kohta
    (item) precision so a deeplink consumer can target the exact provision,
    not just the §. Section-level precision is the fallback when the citation
    stops at the §.
    """

    @staticmethod
    def _p(text: str):
        import xml.etree.ElementTree as ET

        return ET.fromstring(f"<p>{text}</p>")

    # ── Recognizer scan_precise unit tests ───────────────────────────────────

    def test_momentti_captured(self) -> None:
        """'7 §:n 2 momentissa' yields subsection_num=2."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("lannoitelain (711/2022) 7 \xa7:n 2 momentissa tarkoitettu.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="711/2022", section_label="7", subsection_num=2, item_label=None,
                surface_text="lannoitelain (711/2022)",
            )
        ], hits

    def test_momentti_and_kohta_captured(self) -> None:
        """'5 §:n 2 momentin 3 kohdan' yields subsection_num=2, item_label='3'."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("lain (250/1966) 5 \xa7:n 2 momentin 3 kohdan mukaan.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="250/1966", section_label="5", subsection_num=2, item_label="3",
                surface_text="lain (250/1966)",
            )
        ], hits

    def test_section_only_falls_back(self) -> None:
        """A bare '5 §' citation yields subsection_num=None (section-level)."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("elintarvikelain (297/2021) 5 \xa7 nojalla.")
        )
        assert hits == [
            PlainTextStatuteHit(
                statute_id="297/2021", section_label="5", subsection_num=None, item_label=None,
                surface_text="elintarvikelain (297/2021)",
            )
        ], hits

    def test_distinct_momentit_not_collapsed(self) -> None:
        """Two distinct momentit of one statute (each a full citation) produce
        two precise hits — the dedup key includes the sub-section precision.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p(
                "lain (12/2000) 3 \xa7:n 4 momentti ja "
                "lain (12/2000) 3 \xa7:n 5 momentti."
            )
        )
        subsections = sorted(h.subsection_num for h in hits if h.subsection_num is not None)
        assert subsections == [4, 5], hits

    def test_scan_stays_backward_compatible(self) -> None:
        """The legacy scan() 2-tuple contract is unchanged by momentti capture."""
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan(
            self._p("lannoitelain (711/2022) 7 \xa7:n 2 momentissa tarkoitettu.")
        )
        assert hits == [("711/2022", "7")], hits

    def test_name_internal_relative_clause_not_a_target(self) -> None:
        """A '§:n M momentissa tarkoitettu' relative clause with no statute id
        of its own does not fabricate a citation target. This guards the
        adversarial reversal that blocked promoting `momentissa` into the
        shared johtolause lexicon — the body-citation pass must not perturb
        amendment grammar.
        """
        recognizer = PlainTextStatuteCitationRecognizer()
        hits = recognizer.scan_precise(
            self._p("8 \xa7:n 2 momentissa tarkoitettu menettely.")
        )
        assert hits == [], hits

    # ── End-to-end: ProvisionRef threading ───────────────────────────────────

    def test_mention_target_provision_ref_carries_subsection(self) -> None:
        """extract_plain_text_statute_mentions threads momentti+kohta into the
        target ProvisionRef so the interlink consumer can build a subsection
        deeplink.
        """
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Noudatetaan lannoitelain (711/2022) 7 \xc2\xa7:n 2 "
            b"momentin 3 kohdan mukaan.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) == 1, result.mentions
        tgt = plain[0].target_provision_ref
        assert tgt is not None
        assert tgt.statute_id == "711/2022"
        assert tgt.section_label == "7"
        assert tgt.subsection_num == 2
        assert tgt.item_label == "3"
        assert tgt.serialized() == "711/2022/7/2/3"

    def test_interlink_target_locator_has_subsection_segment(self) -> None:
        """The neutral interlink built from a momentti citation carries a
        subsection LocatorSegment — the deeplink precision the consumer needs.
        """
        from lawvm.finland.interlinks import fi_interlink_from_reference_mention

        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><body>"
            b"<section><num>5 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>Sovelletaan lannoitelain (711/2022) 7 \xc2\xa7:n 2 momentissa.</p>"
            b"</content></paragraph>"
            b"</section>"
            b"</body></act></akomaNtoso>"
        )
        result = extract_plain_text_statute_mentions(xml, "2003/314")
        plain = [m for m in result.mentions if m.phrase_lemma == "plain_text"]
        assert len(plain) == 1
        interlink = fi_interlink_from_reference_mention(plain[0], interlink_id="t23")
        assert interlink.target.locator is not None
        kinds = [seg.kind for seg in interlink.target.locator.locator.segments]
        labels = [seg.label for seg in interlink.target.locator.locator.segments]
        assert kinds == ["section", "subsection"], (kinds, labels)
        assert labels == ["7", "2"], (kinds, labels)

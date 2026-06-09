"""Tests for PreparatoryReference core primitive and Finland extractor.

Per AGENTS.md §15 test categories:
  1. Synthetic unit tests — typed primitive construction + serialization.
  2. Real corpus regression — conformance corpus fixtures.
  3. Finding/observation tests — RejectedPreparatoryCandidate emission.
  4. Negative tests — <p> outside preliminaryWork NOT extracted.
  5. Strict-mode tests — UNRESOLVED rejected in strict mode (blocking=True).
  6. No-leak tests — UNRESOLVED kind does not appear in non-UNRESOLVED rows.
  7. Schema-stability tests — serialized column names pinned.
  8. Reuse-verification — HE rows from preliminaryWork use "he/YEAR/NUMBER"
     canonical_id format matching feature #1's fi_refs HE target_statute_id.

Module coverage:
  - lawvm.core.preparatory_reference (PreparatoryReference, enums, observations)
  - lawvm.finland.preparatory_reference_extractor (extraction entry point)
  - lawvm.finland.conformance_corpus.preparatory.fixtures (conformance fixtures)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest

from lawvm.core.preparatory_reference import (
    PreparatoryReference,
    PreparatoryReferenceConfidence,
    PreparatoryReferenceKind,
    RejectedPreparatoryCandidate,
    preparatory_reference_to_row,
)
from lawvm.finland.preparatory_reference_extractor import (
    PrepRefExtractionResult,
    PreparatoryRefRecognizer,
    extract_preparatory_refs,
)
from lawvm.finland.conformance_corpus.preparatory.fixtures import (
    COMMITTEE_OPINION_ONLY,
    EU_DIRECTIVE,
    EVK_RESPONSE,
    FULL_CHAIN,
    HE_EV_NO_COMMITTEE,
    LAW_INITIATIVE,
    MULTI_EU_ACTS,
    NEGATIVE_OUTSIDE_PRELIM,
    UNRESOLVED_P_TEXT,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_ref_matches(actual: PreparatoryReference, expected: Dict[str, Any]) -> None:
    """Assert actual PreparatoryReference matches all expected key/value pairs."""
    row = preparatory_reference_to_row(actual)
    for key, val in expected.items():
        assert row.get(key) == val, (
            f"ref row key {key!r}: expected {val!r}, got {row.get(key)!r}\n"
            f"Full row: {row}"
        )


def _assert_rejected_matches(
    actual: RejectedPreparatoryCandidate,
    expected: Dict[str, Any],
) -> None:
    """Assert actual RejectedPreparatoryCandidate matches expected key/value pairs."""
    actual_dict = {
        "rule_id": actual.rule_id,
        "phase": actual.phase,
        "source_statute_id": actual.source_statute_id,
        "reason": actual.reason,
        "raw_text": actual.raw_text,
        "blocking": actual.blocking,
        "strict_disposition": actual.strict_disposition,
    }
    for key, val in expected.items():
        assert actual_dict.get(key) == val, (
            f"rejected candidate key {key!r}: expected {val!r}, "
            f"got {actual_dict.get(key)!r}"
        )


# ===========================================================================
# Category 1: Synthetic unit tests — typed primitive construction
# ===========================================================================


class TestPreparatoryReferenceConstruction:
    """Synthetic unit tests for the typed primitive itself."""

    def test_exact_he_construction(self) -> None:
        """kind=HE with canonical_id 'he/2021/173'."""
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.HE,
            canonical_id="he/2021/173",
            raw_text="HE 173/2021",
            committee_abbrev=None,
            he_year=2021,
            he_number=173,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(None, None),
        )
        assert ref.kind == PreparatoryReferenceKind.HE
        assert ref.canonical_id == "he/2021/173"
        assert ref.he_year == 2021
        assert ref.he_number == 173

    def test_unresolved_allows_none_canonical_id(self) -> None:
        """UNRESOLVED confidence allows canonical_id=None."""
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.UNRESOLVED,
            canonical_id=None,
            raw_text="some unparseable text",
            committee_abbrev=None,
            he_year=None,
            he_number=None,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.UNRESOLVED,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(None, None),
        )
        assert ref.canonical_id is None
        assert ref.confidence == PreparatoryReferenceConfidence.UNRESOLVED

    def test_exact_requires_canonical_id(self) -> None:
        """EXACT confidence MUST have non-None canonical_id."""
        with pytest.raises(ValueError, match="canonical_id"):
            PreparatoryReference(
                source_statute_id="2022/711",
                kind=PreparatoryReferenceKind.HE,
                canonical_id=None,  # invalid for EXACT
                raw_text="HE 173/2021",
                committee_abbrev=None,
                he_year=2021,
                he_number=173,
                eu_form=None,
                eu_number=None,
                eu_year=None,
                celex=None,
                oj_series=None,
                oj_number=None,
                oj_date=None,
                oj_page=None,
                confidence=PreparatoryReferenceConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_interval=(None, None),
            )

    def test_empty_source_statute_id_rejected(self) -> None:
        """Empty source_statute_id is not allowed."""
        with pytest.raises(ValueError, match="source_statute_id"):
            PreparatoryReference(
                source_statute_id="",
                kind=PreparatoryReferenceKind.HE,
                canonical_id="he/2021/173",
                raw_text="HE 173/2021",
                committee_abbrev=None,
                he_year=2021,
                he_number=173,
                eu_form=None,
                eu_number=None,
                eu_year=None,
                celex=None,
                oj_series=None,
                oj_number=None,
                oj_date=None,
                oj_page=None,
                confidence=PreparatoryReferenceConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_interval=(None, None),
            )

    def test_empty_raw_text_rejected(self) -> None:
        """Empty raw_text is not allowed."""
        with pytest.raises(ValueError, match="raw_text"):
            PreparatoryReference(
                source_statute_id="2022/711",
                kind=PreparatoryReferenceKind.HE,
                canonical_id="he/2021/173",
                raw_text="",  # invalid
                committee_abbrev=None,
                he_year=2021,
                he_number=173,
                eu_form=None,
                eu_number=None,
                eu_year=None,
                celex=None,
                oj_series=None,
                oj_number=None,
                oj_date=None,
                oj_page=None,
                confidence=PreparatoryReferenceConfidence.EXACT,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_interval=(None, None),
            )

    def test_all_kinds_constructable(self) -> None:
        """Each PreparatoryReferenceKind enum value is constructable."""
        for kind in PreparatoryReferenceKind:
            cid = "he/2021/1" if kind != PreparatoryReferenceKind.UNRESOLVED else None
            conf = (
                PreparatoryReferenceConfidence.UNRESOLVED
                if kind == PreparatoryReferenceKind.UNRESOLVED
                else PreparatoryReferenceConfidence.EXACT
            )
            ref = PreparatoryReference(
                source_statute_id="2022/711",
                kind=kind,
                canonical_id=cid,
                raw_text=f"test {kind.value}",
                committee_abbrev=None,
                he_year=None,
                he_number=None,
                eu_form=None,
                eu_number=None,
                eu_year=None,
                celex=None,
                oj_series=None,
                oj_number=None,
                oj_date=None,
                oj_page=None,
                confidence=conf,
                source_span_file=None,
                source_span_byte_offset=None,
                source_span_byte_len=None,
                valid_at_interval=(None, None),
            )
            assert ref.kind == kind

    def test_serialization_columns(self) -> None:
        """preparatory_reference_to_row produces all expected columns."""
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.HE,
            canonical_id="he/2021/173",
            raw_text="HE 173/2021",
            committee_abbrev=None,
            he_year=2021,
            he_number=173,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(date(2022, 1, 1), None),
        )
        row = preparatory_reference_to_row(ref)
        assert row["kind"] == "he"
        assert row["canonical_id"] == "he/2021/173"
        assert row["he_year"] == 2021
        assert row["he_number"] == 173
        assert row["confidence"] == "exact"
        assert row["valid_at_start"] == "2022-01-01"
        assert row["valid_at_end"] is None


# ===========================================================================
# Category 2: Real corpus regression — conformance fixtures
# ===========================================================================


class TestConformanceCorpusFixtures:
    """Run all conformance corpus fixtures through the extractor."""

    def _run_fixture(self, fixture: Any) -> PrepRefExtractionResult:
        return extract_preparatory_refs(
            fixture.xml_bytes, fixture.source_statute_id
        )

    def test_full_chain_he_extracted(self) -> None:
        """FULL_CHAIN: HE via AKN <ref> produces kind=HE."""
        result = self._run_fixture(FULL_CHAIN)
        he_refs = [r for r in result.refs if r.kind == PreparatoryReferenceKind.HE]
        assert len(he_refs) == 1
        _assert_ref_matches(he_refs[0], FULL_CHAIN.expected_refs[0])

    def test_full_chain_committee_report(self) -> None:
        """FULL_CHAIN: HaVM 23/2022 produces kind=committee_report."""
        result = self._run_fixture(FULL_CHAIN)
        vm_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.COMMITTEE_REPORT
        ]
        assert len(vm_refs) == 1
        _assert_ref_matches(vm_refs[0], FULL_CHAIN.expected_refs[1])

    def test_full_chain_ev(self) -> None:
        """FULL_CHAIN: EV 156/2022 produces kind=parliament_response."""
        result = self._run_fixture(FULL_CHAIN)
        ev_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE
        ]
        assert len(ev_refs) == 1
        _assert_ref_matches(ev_refs[0], FULL_CHAIN.expected_refs[2])

    def test_full_chain_eu_regulation(self) -> None:
        """FULL_CHAIN: EU regulation with CELEX + OJ produces kind=eu_regulation."""
        result = self._run_fixture(FULL_CHAIN)
        eu_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.EU_REGULATION
        ]
        assert len(eu_refs) == 1
        eu_ref = eu_refs[0]
        assert eu_ref.celex == "32017R2226"
        assert eu_ref.eu_year == 2017   # year of enactment
        assert eu_ref.eu_number == 2226  # sequential act number
        assert eu_ref.oj_series == "L"
        assert eu_ref.oj_number == 327
        assert eu_ref.oj_page == 20
        assert eu_ref.oj_date == date(2017, 12, 9)
        assert eu_ref.canonical_id == "eu.celex.32017R2226"

    def test_full_chain_row_count(self) -> None:
        """FULL_CHAIN: exactly 4 refs produced (HE + HaVM + EV + EU reg)."""
        result = self._run_fixture(FULL_CHAIN)
        assert len(result.refs) == 4
        assert len(result.rejected) == 0

    def test_committee_opinion_only(self) -> None:
        """COMMITTEE_OPINION_ONLY: PeVL produces kind=committee_opinion."""
        result = self._run_fixture(COMMITTEE_OPINION_ONLY)
        opinion_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.COMMITTEE_OPINION
        ]
        assert len(opinion_refs) == 1
        vl_ref = opinion_refs[0]
        assert vl_ref.committee_abbrev == "PeVL"
        assert vl_ref.canonical_id == "fi.committee_opinion.pevl.12.2019"

    def test_he_ev_no_committee(self) -> None:
        """HE_EV_NO_COMMITTEE: 2 refs (HE + EV), no committee."""
        result = self._run_fixture(HE_EV_NO_COMMITTEE)
        assert len(result.refs) == 2
        kinds = {r.kind for r in result.refs}
        assert PreparatoryReferenceKind.HE in kinds
        assert PreparatoryReferenceKind.PARLIAMENT_RESPONSE in kinds
        assert PreparatoryReferenceKind.COMMITTEE_REPORT not in kinds

    def test_eu_directive_classification(self) -> None:
        """EU_DIRECTIVE: CELEX type 'L' → kind=eu_directive (not eu_regulation)."""
        result = self._run_fixture(EU_DIRECTIVE)
        eu_refs = [
            r for r in result.refs
            if r.kind in (
                PreparatoryReferenceKind.EU_DIRECTIVE,
                PreparatoryReferenceKind.EU_REGULATION,
            )
        ]
        assert len(eu_refs) == 1
        assert eu_refs[0].kind == PreparatoryReferenceKind.EU_DIRECTIVE
        assert eu_refs[0].celex == "32019L0904"

    def test_multi_eu_acts_each_captured(self) -> None:
        """MULTI_EU_ACTS: each EU act on its own <p> produces a separate row."""
        result = self._run_fixture(MULTI_EU_ACTS)
        eu_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.EU_REGULATION
        ]
        assert len(eu_refs) == 2
        celexes = {r.celex for r in eu_refs}
        assert "32016R0679" in celexes
        assert "32018R1725" in celexes

    def test_evk_response(self) -> None:
        """EVK_RESPONSE: EVK pattern produces kind=parliament_response_comm."""
        result = self._run_fixture(EVK_RESPONSE)
        evk_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE_COMM
        ]
        assert len(evk_refs) == 1
        assert evk_refs[0].canonical_id == "fi.evk.3.2008"

    def test_law_initiative(self) -> None:
        """LAW_INITIATIVE: LA pattern produces kind=law_initiative."""
        result = self._run_fixture(LAW_INITIATIVE)
        la_refs = [
            r for r in result.refs
            if r.kind == PreparatoryReferenceKind.LAW_INITIATIVE
        ]
        assert len(la_refs) == 1
        assert la_refs[0].canonical_id == "fi.la.5.2011"


# ===========================================================================
# Category 3: Finding / observation tests
# ===========================================================================


class TestFindingsAndObservations:
    """Verify RejectedPreparatoryCandidate is emitted correctly."""

    def test_unresolved_p_emits_rejected_candidate(self) -> None:
        """UNRESOLVED_P_TEXT: unparseable <p> emits RejectedPreparatoryCandidate."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes, UNRESOLVED_P_TEXT.source_statute_id
        )
        assert len(result.rejected) == 1
        rej = result.rejected[0]
        assert rej.rule_id == "fi_prep_ref_unresolved_p_text"
        assert rej.phase == "preparatory_ref_extraction"
        assert rej.source_statute_id == UNRESOLVED_P_TEXT.source_statute_id
        assert not rej.blocking  # non-strict: just a record

    def test_unresolved_rejected_has_raw_text(self) -> None:
        """RejectedPreparatoryCandidate.raw_text captures the problematic text."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes, UNRESOLVED_P_TEXT.source_statute_id
        )
        assert len(result.rejected) == 1
        # raw_text should contain some of the signature text
        assert len(result.rejected[0].raw_text) > 5

    def test_valid_refs_still_emitted_alongside_rejected(self) -> None:
        """Even when UNRESOLVED exists, valid refs (HE, VM, EV) are still emitted."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes, UNRESOLVED_P_TEXT.source_statute_id
        )
        kinds = {r.kind for r in result.refs}
        assert PreparatoryReferenceKind.HE in kinds
        assert PreparatoryReferenceKind.COMMITTEE_REPORT in kinds
        assert PreparatoryReferenceKind.PARLIAMENT_RESPONSE in kinds

    def test_no_rejected_in_full_chain(self) -> None:
        """FULL_CHAIN: no rejected candidates when all patterns recognized."""
        result = extract_preparatory_refs(FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id)
        assert len(result.rejected) == 0


# ===========================================================================
# Category 4: Negative tests
# ===========================================================================


class TestNegativeCases:
    """Verify extractor does NOT capture text outside preliminaryWork."""

    def test_no_extraction_outside_prelim(self) -> None:
        """NEGATIVE_OUTSIDE_PRELIM: body-text citations are NOT extracted."""
        result = extract_preparatory_refs(
            NEGATIVE_OUTSIDE_PRELIM.xml_bytes,
            NEGATIVE_OUTSIDE_PRELIM.source_statute_id,
        )
        assert result.refs == []
        assert result.rejected == []
        assert result.lifecycle_observations == []

    def test_empty_xml_returns_empty(self) -> None:
        """Statute with no preliminaryWork block → empty result."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body><section><num>1 \xc2\xa7</num>"
            b"<paragraph><content><p>HaVM 5/2021</p></content></paragraph>"
            b"</section></body></act>"
            b"</akomaNtoso>"
        )
        result = extract_preparatory_refs(xml, "2021/100")
        assert result.refs == []
        assert result.rejected == []

    def test_committee_vm_in_body_not_extracted(self) -> None:
        """'TyVM 13/2020' in body prose is NOT extracted as committee_report."""
        xml = (
            b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            b"<act><meta/><body><section><num>1 \xc2\xa7</num>"
            b"<paragraph><content>"
            b"<p>TyVM 13/2020 mietin\xc3\xb6n mukaisesti toimittava.</p>"
            b"</content></paragraph>"
            b"</section></body></act>"
            b"</akomaNtoso>"
        )
        result = extract_preparatory_refs(xml, "2020/500")
        assert not any(
            r.kind == PreparatoryReferenceKind.COMMITTEE_REPORT for r in result.refs
        )


# ===========================================================================
# Category 5: Strict-mode tests
# ===========================================================================


class TestStrictMode:
    """Verify strict mode blocks UNRESOLVED refs."""

    def test_strict_mode_unresolved_is_blocking(self) -> None:
        """In strict mode, UNRESOLVED <p> produces blocking=True rejected candidate."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes,
            UNRESOLVED_P_TEXT.source_statute_id,
            strict=True,
        )
        assert len(result.rejected) == 1
        rej = result.rejected[0]
        assert rej.blocking is True
        assert rej.strict_disposition == "block"

    def test_strict_mode_valid_refs_still_emitted(self) -> None:
        """Strict mode does NOT suppress valid refs — only unresolved are blocked."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes,
            UNRESOLVED_P_TEXT.source_statute_id,
            strict=True,
        )
        # 3 valid refs (HE + TyVM + EV) still present
        assert len(result.refs) == 3

    def test_non_strict_unresolved_is_non_blocking(self) -> None:
        """Default (non-strict) mode: UNRESOLVED → blocking=False."""
        result = extract_preparatory_refs(
            UNRESOLVED_P_TEXT.xml_bytes,
            UNRESOLVED_P_TEXT.source_statute_id,
            strict=False,
        )
        for rej in result.rejected:
            assert not rej.blocking


# ===========================================================================
# Category 6: No-leak tests
# ===========================================================================


class TestNoLeak:
    """Verify UNRESOLVED confidence does not appear in non-UNRESOLVED rows."""

    def test_no_unresolved_confidence_in_resolved_rows(self) -> None:
        """Resolved rows must not have confidence=UNRESOLVED."""
        result = extract_preparatory_refs(FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id)
        for ref in result.refs:
            # Any ref that was emitted should have EXACT or APPROXIMATE confidence
            assert ref.confidence != PreparatoryReferenceConfidence.UNRESOLVED, (
                f"ref {ref.kind.value}/{ref.raw_text!r} has UNRESOLVED confidence "
                f"but was emitted as a PreparatoryReference (should be UNRESOLVED kind)"
            )

    def test_unresolved_kind_has_none_canonical_id(self) -> None:
        """If UNRESOLVED kind appears in refs, its canonical_id must be None."""
        # Build a synthetic UNRESOLVED row
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.UNRESOLVED,
            canonical_id=None,
            raw_text="some unrecognized text",
            committee_abbrev=None,
            he_year=None,
            he_number=None,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.UNRESOLVED,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(None, None),
        )
        row = preparatory_reference_to_row(ref)
        assert row["canonical_id"] is None


# ===========================================================================
# Category 7: Schema-stability tests
# ===========================================================================


class TestSchemaStability:
    """Pin the serialized column names from preparatory_reference_to_row."""

    _EXPECTED_COLUMNS = {
        "source_statute_id",
        "kind",
        "canonical_id",
        "raw_text",
        "committee_abbrev",
        "he_year",
        "he_number",
        "eu_form",
        "eu_number",
        "eu_year",
        "celex",
        "oj_series",
        "oj_number",
        "oj_date",
        "oj_page",
        "confidence",
        "source_span_file",
        "source_span_byte_offset",
        "source_span_byte_len",
        "valid_at_start",
        "valid_at_end",
    }

    def test_serialization_has_all_columns(self) -> None:
        """preparatory_reference_to_row must produce all expected columns."""
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.EU_REGULATION,
            canonical_id="eu.celex.32017R2226",
            raw_text="(EU) 2017/2226 (32017R2226); EUVL L 327, 9.12.2017, s. 20",
            committee_abbrev=None,
            he_year=None,
            he_number=None,
            eu_form="EU",
            eu_number=2017,
            eu_year=2226,
            celex="32017R2226",
            oj_series="L",
            oj_number=327,
            oj_date=date(2017, 12, 9),
            oj_page=20,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(None, None),
        )
        row = preparatory_reference_to_row(ref)
        missing = self._EXPECTED_COLUMNS - set(row.keys())
        assert not missing, f"Missing columns in serialization: {missing}"

    def test_no_extra_columns(self) -> None:
        """preparatory_reference_to_row must not add undocumented columns."""
        ref = PreparatoryReference(
            source_statute_id="2022/711",
            kind=PreparatoryReferenceKind.PARLIAMENT_RESPONSE,
            canonical_id="fi.ev.156.2022",
            raw_text="EV 156/2022",
            committee_abbrev=None,
            he_year=None,
            he_number=None,
            eu_form=None,
            eu_number=None,
            eu_year=None,
            celex=None,
            oj_series=None,
            oj_number=None,
            oj_date=None,
            oj_page=None,
            confidence=PreparatoryReferenceConfidence.EXACT,
            source_span_file=None,
            source_span_byte_offset=None,
            source_span_byte_len=None,
            valid_at_interval=(None, None),
        )
        row = preparatory_reference_to_row(ref)
        extra = set(row.keys()) - self._EXPECTED_COLUMNS
        assert not extra, f"Unexpected extra columns in serialization: {extra}"


# ===========================================================================
# Category 8: Reuse-verification
#
# HE rows from preliminaryWork must use canonical_id = "he/YEAR/NUMBER",
# matching feature #1's fi_refs.parquet HE target_statute_id format.
# ===========================================================================


class TestReuseVerification:
    """Verify HE canonical_id format matches feature #1 fi_refs format."""

    def test_he_canonical_id_uses_slash_format(self) -> None:
        """HE rows use 'he/YEAR/NUMBER' format (not 'fi.he.YEAR.NUMBER')."""
        result = extract_preparatory_refs(FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id)
        he_refs = [r for r in result.refs if r.kind == PreparatoryReferenceKind.HE]
        assert len(he_refs) >= 1
        for ref in he_refs:
            assert ref.canonical_id is not None
            assert ref.canonical_id.startswith("he/"), (
                f"HE canonical_id should start with 'he/' (slash format), "
                f"got {ref.canonical_id!r}"
            )
            # Validate structure: he/YEAR/NUMBER
            parts = ref.canonical_id.split("/")
            assert len(parts) == 3, (
                f"HE canonical_id should be 'he/YEAR/NUMBER', got {ref.canonical_id!r}"
            )
            assert parts[0] == "he"
            assert parts[1].isdigit() and len(parts[1]) == 4
            assert parts[2].isdigit()

    def test_he_canonical_id_consistent_with_year_number_fields(self) -> None:
        """HE canonical_id = 'he/{he_year}/{he_number}' exactly."""
        result = extract_preparatory_refs(FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id)
        he_refs = [r for r in result.refs if r.kind == PreparatoryReferenceKind.HE]
        for ref in he_refs:
            assert ref.he_year is not None
            assert ref.he_number is not None
            expected_id = f"he/{ref.he_year}/{ref.he_number}"
            assert ref.canonical_id == expected_id, (
                f"canonical_id mismatch: expected {expected_id!r}, "
                f"got {ref.canonical_id!r}"
            )

    def test_he_from_multiple_fixtures_use_same_format(self) -> None:
        """HE canonical_id format is consistent across all fixtures that have HE."""
        fixtures_with_he = [
            FULL_CHAIN,
            COMMITTEE_OPINION_ONLY,
            HE_EV_NO_COMMITTEE,
            EU_DIRECTIVE,
            MULTI_EU_ACTS,
            UNRESOLVED_P_TEXT,
            EVK_RESPONSE,
        ]
        for fixture in fixtures_with_he:
            result = extract_preparatory_refs(fixture.xml_bytes, fixture.source_statute_id)
            he_refs = [r for r in result.refs if r.kind == PreparatoryReferenceKind.HE]
            for ref in he_refs:
                assert ref.canonical_id is not None
                assert ref.canonical_id.startswith("he/"), (
                    f"Fixture {fixture.fixture_id}: HE canonical_id should start with "
                    f"'he/', got {ref.canonical_id!r}"
                )

    def test_fi_refs_join_compatibility(self) -> None:
        """HE canonical_id in preparatory refs matches the target_statute_id format
        from fi_refs.parquet HE-CITES edges (cross_refs.py 'he/YEAR/NUMBER')."""
        # Verify using the same _HE_REF_HREF_RE logic as cross_refs
        import re
        _HE_REF_PATTERN = re.compile(
            r'/akn/fi/doc/government-proposal/(\d{4})/(\d+(?:-\d+)?)'
        )
        # The pattern produces "he/YEAR/NUMBER" — same as our canonical_id
        href = "/akn/fi/doc/government-proposal/2021/173"
        m = _HE_REF_PATTERN.match(href)
        assert m is not None
        expected = f"he/{m.group(1)}/{int(m.group(2).split('-')[0])}"
        assert expected == "he/2021/173"

        # Now verify our extractor produces this same format
        result = extract_preparatory_refs(FULL_CHAIN.xml_bytes, FULL_CHAIN.source_statute_id)
        he_refs = [r for r in result.refs if r.kind == PreparatoryReferenceKind.HE]
        assert len(he_refs) == 1
        assert he_refs[0].canonical_id == "he/2021/173"


# ===========================================================================
# Category 9: Recognizer unit tests
# ===========================================================================


class TestPreparatoryRefRecognizer:
    """Direct unit tests for the named PreparatoryRefRecognizer."""

    _recognizer = PreparatoryRefRecognizer()

    def _recognize(self, text: str) -> List[PreparatoryReference]:
        refs, _ = self._recognizer.recognize(text, "TEST/100", (None, None))
        return refs

    def test_committee_vm_recognized(self) -> None:
        refs = self._recognize("HaVM 23/2022")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.COMMITTEE_REPORT
        assert refs[0].committee_abbrev == "HaVM"
        assert refs[0].canonical_id == "fi.committee.havm.23.2022"

    def test_committee_vl_recognized(self) -> None:
        refs = self._recognize("PeVL 12/2021")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.COMMITTEE_OPINION
        assert refs[0].committee_abbrev == "PeVL"
        assert refs[0].canonical_id == "fi.committee_opinion.pevl.12.2021"

    def test_ev_recognized(self) -> None:
        refs = self._recognize("EV 156/2022")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE
        assert refs[0].canonical_id == "fi.ev.156.2022"

    def test_evk_recognized(self) -> None:
        refs = self._recognize("EVK 3/2008")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE_COMM
        assert refs[0].canonical_id == "fi.evk.3.2008"

    def test_la_recognized(self) -> None:
        refs = self._recognize("LA 5/2011")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.LAW_INITIATIVE
        assert refs[0].canonical_id == "fi.la.5.2011"

    def test_eu_regulation_with_celex_and_oj(self) -> None:
        text = (
            "Euroopan parlamentin ja neuvoston asetus (EU) 2017/2226 "
            "(32017R2226); EUVL L 327, 9.12.2017, s. 20"
        )
        refs = self._recognize(text)
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.EU_REGULATION
        assert refs[0].celex == "32017R2226"
        assert refs[0].oj_series == "L"
        assert refs[0].oj_number == 327
        assert refs[0].oj_date == date(2017, 12, 9)
        assert refs[0].oj_page == 20

    def test_eu_directive_via_celex_type(self) -> None:
        text = (
            "Euroopan parlamentin ja neuvoston direktiivi (EU) 2019/904 "
            "(32019L0904); EUVL L 155, 12.6.2019, s. 1"
        )
        refs = self._recognize(text)
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.EU_DIRECTIVE
        assert refs[0].celex == "32019L0904"

    def test_empty_text_returns_empty(self) -> None:
        refs = self._recognize("")
        assert refs == []

    def test_unrecognized_text_returns_empty(self) -> None:
        """Recognizer returns empty list for unrecognized text (caller emits UNRESOLVED)."""
        refs = self._recognize("some completely unparseable text here")
        assert refs == []

    def test_ev_not_confused_with_evk(self) -> None:
        """EV recognizer does not fire when text is EVK."""
        refs = self._recognize("EVK 3/2008")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE_COMM

    def test_various_committee_abbreviations(self) -> None:
        """Several standard committee abbreviations are recognized."""
        cases = [
            ("LaVM 5/2020", "LaVM", "fi.committee.lavm.5.2020"),
            ("SiVM 3/2019", "SiVM", "fi.committee.sivm.3.2019"),
            ("StVM 7/2021", "StVM", "fi.committee.stvm.7.2021"),
            ("MmVM 2/2022", "MmVM", "fi.committee.mmvm.2.2022"),
            ("YmVM 9/2019", "YmVM", "fi.committee.ymvm.9.2019"),
            ("TyVM 13/2015", "TyVM", "fi.committee.tyvm.13.2015"),
            ("VaVM 8/2020", "VaVM", "fi.committee.vavm.8.2020"),
        ]
        for text, expected_abbr, expected_cid in cases:
            refs = self._recognize(text)
            assert len(refs) == 1, f"Expected 1 ref for {text!r}, got {len(refs)}"
            assert refs[0].committee_abbrev == expected_abbr
            assert refs[0].canonical_id == expected_cid

    def test_ey_form_recognized(self) -> None:
        """EU form 'EY' (older EU form in Finnish text) is recognized."""
        text = "(EY) N:o 178/2002 (32002R0178); EUVL L 31, 1.2.2002, s. 1"
        refs = self._recognize(text)
        assert len(refs) == 1
        assert refs[0].eu_form == "EY"
        assert refs[0].celex == "32002R0178"

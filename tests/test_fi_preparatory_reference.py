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
from lawvm.finland.references.preparatory_reference_extractor import (
    _normalize_domestic_year,
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


class TestPreparatoryLaneDefectRegressions:
    """Regression tests for the five preparatory-lane recall/precision defects.

    F1 — amendment-footer blocks are now scanned (not just preliminaryWork).
    F2 — packed multi-ref paragraphs enumerate ALL tokens (not just the first),
         while preserving the start-anchor FP-resistance.
    F3 — EU directives/decisions in the "/EU" suffix form with a CELEX classify
         as CELEX-typed EU acts (not degraded to a bare OJ row).
    F4 — emitted surface = the citation TOKEN, not the whole paragraph.
    F5 — committee abbreviation set is closed (EVL is NOT a committee opinion).
    Plus the preserved invariant: HE is owned by the <ref> lane only.
    """

    _recognizer = PreparatoryRefRecognizer()

    def _recognize(self, text: str) -> List[PreparatoryReference]:
        refs, _ = self._recognizer.recognize(text, "TEST/100", (None, None))
        return refs

    # --- F1: amendment-footer block scanning -------------------------------

    def test_f1_footer_block_committee_and_ev_captured(self) -> None:
        """F1: committee + EV in an amendmentEntryIntoForce* footer are scanned."""
        from lawvm.finland.conformance_corpus.preparatory.fixtures import (
            AMENDMENT_FOOTER_PACKED,
        )
        result = extract_preparatory_refs(
            AMENDMENT_FOOTER_PACKED.xml_bytes,
            AMENDMENT_FOOTER_PACKED.source_statute_id,
        )
        kinds = {r.kind for r in result.refs}
        assert PreparatoryReferenceKind.COMMITTEE_REPORT in kinds
        assert PreparatoryReferenceKind.PARLIAMENT_RESPONSE in kinds
        assert PreparatoryReferenceKind.HE in kinds

    def test_f1_footer_block_no_double_visit(self) -> None:
        """F1: nested entryIntoForce inside the wrapper is visited ONCE, not twice."""
        from lawvm.finland.conformance_corpus.preparatory.fixtures import (
            AMENDMENT_FOOTER_PACKED,
        )
        result = extract_preparatory_refs(
            AMENDMENT_FOOTER_PACKED.xml_bytes,
            AMENDMENT_FOOTER_PACKED.source_statute_id,
        )
        # Exactly one committee_report and one parliament_response — a double
        # visit of the nested block would duplicate both.
        cr = [r for r in result.refs
              if r.kind == PreparatoryReferenceKind.COMMITTEE_REPORT]
        ev = [r for r in result.refs
              if r.kind == PreparatoryReferenceKind.PARLIAMENT_RESPONSE]
        assert len(cr) == 1
        assert len(ev) == 1

    # --- F2: packed-paragraph enumeration ----------------------------------

    def test_f2_packed_paragraph_all_tokens(self) -> None:
        """F2: a packed paragraph yields every citation, not just the first."""
        refs = self._recognize("LaVM 6/2025, EV 52/2025")
        cids = {r.canonical_id for r in refs}
        assert cids == {"fi.committee.lavm.6.2025", "fi.ev.52.2025"}

    def test_f2_packed_three_tokens(self) -> None:
        """F2: HE-text + committee + EV in one segment-delimited paragraph."""
        refs = self._recognize("HaVM 3/2019, EV 20/2019, LaVL 1/2019")
        cids = {r.canonical_id for r in refs}
        assert cids == {
            "fi.committee.havm.3.2019",
            "fi.ev.20.2019",
            "fi.committee_opinion.lavl.1.2019",
        }

    def test_f2_fp_resistance_midprose_ev(self) -> None:
        """F2 FP-resistance: a mid-sentence EV token still does NOT match."""
        assert self._recognize("jotain EV 5/2020 keskellä") == []

    def test_f2_fp_resistance_midprose_committee(self) -> None:
        """F2 FP-resistance: a mid-sentence committee token still does NOT match."""
        assert self._recognize("ks. HaVM 1/2020 tarkemmin liitteessä") == []

    def test_f2_fp_resistance_statute_cite_not_committee(self) -> None:
        """F2 FP-resistance: an ordinary statute citation is not a committee ref."""
        assert self._recognize("annettu lailla 358/2021 muutoksin") == []

    # --- F3: EU suffix form → CELEX classification -------------------------

    def test_f3_directive_suffix_form_celex_typed(self) -> None:
        """F3: 'direktiivi 2014/40/EU (32014L0040)' → eu_directive on CELEX."""
        refs = self._recognize(
            "Euroopan parlamentin ja neuvoston direktiivi 2014/40/EU (32014L0040)"
        )
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.EU_DIRECTIVE
        assert refs[0].canonical_id == "eu.celex.32014L0040"
        assert refs[0].celex == "32014L0040"
        assert refs[0].eu_form == "EU"
        assert refs[0].eu_year == 2014

    def test_f3_directive_suffix_form_not_oj(self) -> None:
        """F3: the suffix form with an OJ tail is NOT degraded to a bare OJ row."""
        refs = self._recognize(
            "direktiivi 2001/9/EY (32001L0009); EYVL N:o L 48, 17.2.2001, s. 18"
        )
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.EU_DIRECTIVE
        assert refs[0].celex == "32001L0009"
        # OJ tail is carried on the SAME EU row, not a separate OJ_REFERENCE row.
        assert refs[0].oj_series == "L"

    def test_f3_decision_celex_typed(self) -> None:
        """F3: a CELEX type 'D' classifies as eu_decision."""
        refs = self._recognize("päätös 2010/12/EU (32010D0012)")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.EU_DECISION
        assert refs[0].canonical_id == "eu.celex.32010D0012"

    # --- F4: tight surface (token, not whole paragraph) --------------------

    def test_f4_domestic_surface_is_token(self) -> None:
        """F4: a domestic ref's raw_text is the citation token, not the paragraph."""
        refs = self._recognize("LaVM 6/2025, EV 52/2025")
        by_cid = {r.canonical_id: r for r in refs}
        assert by_cid["fi.committee.lavm.6.2025"].raw_text == "LaVM 6/2025"
        assert by_cid["fi.ev.52.2025"].raw_text == "EV 52/2025"

    def test_f4_eu_surface_is_token(self) -> None:
        """F4: an EU ref's raw_text is the act token, not the whole paragraph."""
        refs = self._recognize(
            "asetus (EU) 2017/2226 (32017R2226); EUVL L 327, 9.12.2017, s. 20"
        )
        assert len(refs) == 1
        assert refs[0].raw_text == "(EU) 2017/2226"

    # --- F5: closed committee abbreviation set -----------------------------

    def test_f5_evl_not_committee_opinion(self) -> None:
        """F5: 'EVL 5/2020' must NOT parse as a committee opinion."""
        refs = self._recognize("EVL 5/2020")
        assert all(
            r.kind != PreparatoryReferenceKind.COMMITTEE_OPINION for r in refs
        )

    def test_f5_bogus_vm_abbrev_rejected(self) -> None:
        """F5: an out-of-set '*VM' abbreviation is not accepted as a committee."""
        assert self._recognize("XyzVM 5/2020") == []

    def test_f5_real_committee_stems_still_recognized(self) -> None:
        """F5: the closed set still recognizes every real committee stem."""
        for text, cid in (
            ("SuVM 1/2020", "fi.committee.suvm.1.2020"),
            ("TarVM 2/2021", "fi.committee.tarvm.2.2021"),
            ("PeVL 3/2022", "fi.committee_opinion.pevl.3.2022"),
            ("TiVL 4/2023", "fi.committee_opinion.tivl.4.2023"),
        ):
            refs = self._recognize(text)
            assert len(refs) == 1, f"{text!r} → {refs}"
            assert refs[0].canonical_id == cid

    # --- preserved invariant: HE owned by the <ref> lane only --------------

    def test_he_not_double_counted_in_mention_lane(self) -> None:
        """HE is emitted by the <ref> lane only; the prep mention lane excludes it."""
        from lawvm.finland.references.ref_mention_extractor import (
            extract_preparatory_reference_mentions,
        )
        from lawvm.finland.conformance_corpus.preparatory.fixtures import (
            AMENDMENT_FOOTER_PACKED,
        )
        mentions = extract_preparatory_reference_mentions(
            AMENDMENT_FOOTER_PACKED.xml_bytes,
            AMENDMENT_FOOTER_PACKED.source_statute_id,
        ).mentions
        subtypes = {m.edge_subtype for m in mentions}
        assert "he" not in subtypes
        assert "committee_report" in subtypes
        assert "parliament_response" in subtypes


class TestPreparatoryTwoDigitYearRecall:
    """G1 — pre-2000 preparatory footers cite domestic refs with TWO-digit years.

    Older statutes' entry-into-force footers carry committee / EV / EVK / LA
    citations with a 2-digit year (e.g. ``"LaVM 10/92"``, ``"EV 45/93 vp"``).
    The recognizer previously required a 4-digit year and silently dropped them.
    Corpus witness: statute 1966/232's amendment footer reads
    ``"... HE 131/92 , LaVM 10/92"`` (Swedish-language statute).

    The 2-digit arm is admitted ONLY behind the closed committee/EV/EVK/LA
    prefixes, so an ordinary statute citation ("lailla 358/95") still never
    matches, and a 4-digit year is never mis-split into 2+2.
    """

    _recognizer = PreparatoryRefRecognizer()

    def _recognize(self, text: str) -> List[PreparatoryReference]:
        refs, _ = self._recognizer.recognize(text, "TEST/100", (None, None))
        return refs

    def test_committee_report_two_digit_year(self) -> None:
        """'LaVM 10/92' → committee report, year normalized to 1992."""
        refs = self._recognize("LaVM 10/92")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.COMMITTEE_REPORT
        assert refs[0].canonical_id == "fi.committee.lavm.10.1992"

    def test_corpus_witness_packed_two_digit(self) -> None:
        """The 1966/232 footer shape: HE-text + 2-digit committee in one segment."""
        # HE text without <ref> is not recognized (HE owned by the <ref> lane);
        # the co-located 2-digit committee token must still be captured.
        refs = self._recognize("HE 131/92, LaVM 10/92")
        cids = {r.canonical_id for r in refs}
        assert "fi.committee.lavm.10.1992" in cids

    def test_committee_opinion_two_digit_year(self) -> None:
        """'PeVL 7/99 vp' → committee opinion, year normalized to 1999."""
        refs = self._recognize("PeVL 7/99 vp")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.COMMITTEE_OPINION
        assert refs[0].canonical_id == "fi.committee_opinion.pevl.7.1999"

    def test_ev_evk_la_two_digit_year(self) -> None:
        """EV / EVK / LA all admit a 2-digit year."""
        cases = [
            ("EV 45/93", "fi.ev.45.1993"),
            ("EVK 3/95", "fi.evk.3.1995"),
            ("LA 12/96", "fi.la.12.1996"),
        ]
        for text, cid in cases:
            refs = self._recognize(text)
            assert len(refs) == 1, f"{text!r} → {refs}"
            assert refs[0].canonical_id == cid

    def test_century_pivot(self) -> None:
        """2-digit year century pivot: <=29 → 2000s, >=30 → 1900s."""
        assert _normalize_domestic_year("92") == 1992
        assert _normalize_domestic_year("05") == 2005
        assert _normalize_domestic_year("29") == 2029
        assert _normalize_domestic_year("30") == 1930
        assert _normalize_domestic_year("2022") == 2022

    def test_four_digit_year_not_mis_split(self) -> None:
        """A 4-digit year is never read as a 2-digit year + stray digits."""
        refs = self._recognize("LaVM 5/2020")
        assert len(refs) == 1
        assert refs[0].canonical_id == "fi.committee.lavm.5.2020"

    def test_fp_statute_cite_two_digit_not_a_committee(self) -> None:
        """FP guard: a statute citation with a 2-digit tail is NOT a ref."""
        assert self._recognize("annettu lailla 358/95 muutoksin") == []
        assert self._recognize("asetus 1431/93") == []
        assert self._recognize("muutos 12/95") == []

    def test_fp_midprose_two_digit_ev_not_a_ref(self) -> None:
        """FP guard: a mid-sentence 2-digit EV token still does NOT match."""
        assert self._recognize("jotain EV 5/20 keskellä") == []


class TestPreparatoryEuYearFirstSuffixRecall:
    """G2 — un-parenthesized year-first form-suffix EU acts were dropped.

    A footer may cite an EU act in the year-first form-suffix shape
    ``"direktiivi 2011/24/EU"`` / ``"direktiivin 2009/13/EY"`` — where the
    trailing ``/EU``/``/EY`` form letters ARE the EU marker — with no
    parenthesized ``(EU)`` token and no CELEX.  The EU-paragraph gate previously
    fired only on a parenthesized marker or a CELEX, so it skipped these
    entirely: the act was dropped (no ref) or, when an ``EUVL``/``EYVL`` tail was
    present, degraded to a bare OJ row that lost the act identity.

    Corpus witnesses: statute 2010/1326 footer ``"... direktiv 2011/24/EU ...,
    EUT L 88, 4.4.2011, s. 45"`` and statute 2025/103 footer ``"neuvoston
    direktiivi 2009/13/EY, EUVL L 124, 20.5.2009, s. 30"``.

    The shared recognize_eu_acts(DIALECT_PREPARATORY) is the authoritative form
    gate, so it does not fire on a plain statute cite, a committee token, or an
    OJ-only paragraph — no false-positive widening.
    """

    _recognizer = PreparatoryRefRecognizer()

    def _recognize(self, text: str) -> List[PreparatoryReference]:
        refs, _ = self._recognizer.recognize(text, "TEST/100", (None, None))
        return refs

    def test_year_first_suffix_bare_act_captured(self) -> None:
        """'direktiivin 2004/36/EY' (no paren, no CELEX, no OJ) is captured."""
        refs = self._recognize(
            "Euroopan parlamentin ja neuvoston direktiivin 2004/36/EY"
        )
        eu = [r for r in refs if r.kind.name.startswith("EU")]
        assert len(eu) == 1
        assert eu[0].eu_form == "EY"
        assert eu[0].eu_year == 2004
        assert eu[0].eu_number == 36

    def test_corpus_witness_with_oj_tail_keeps_act_and_oj(self) -> None:
        """2025/103 shape: act identity preserved AND OJ data populated."""
        refs = self._recognize(
            "neuvoston direktiivi 2009/13/EY, EUVL L 124, 20.5.2009, s. 30"
        )
        eu = [r for r in refs if r.kind.name.startswith("EU")]
        assert len(eu) == 1, f"expected one EU act, got {refs}"
        assert eu[0].eu_form == "EY"
        assert eu[0].eu_year == 2009
        assert eu[0].eu_number == 13
        # OJ data is preserved on the same row, not split into a bare OJ ref.
        assert eu[0].oj_series == "L"
        assert eu[0].oj_number == 124

    def test_corpus_witness_swedish_bare_act_captured(self) -> None:
        """2010/1326 shape: a Swedish-language directive cite is captured."""
        refs = self._recognize(
            "Europaparlamentets och rådets direktiv 2011/24/EU om "
            "tillämpningen, EUT L 88, 4.4.2011, s. 45"
        )
        eu = [r for r in refs if r.kind.name.startswith("EU")]
        assert len(eu) == 1
        assert eu[0].eu_form == "EU"
        assert eu[0].eu_year == 2011
        assert eu[0].eu_number == 24

    def test_fp_oj_only_paragraph_still_bare_oj(self) -> None:
        """FP guard: an OJ-only paragraph (no EU-act form) is still a bare OJ."""
        refs = self._recognize("EUVL L 124, 20.5.2009, s. 30")
        assert len(refs) == 1
        assert refs[0].kind == PreparatoryReferenceKind.OJ_REFERENCE

    def test_fp_statute_cite_not_eu_act(self) -> None:
        """FP guard: a plain domestic statute cite is not an EU act."""
        assert self._recognize("annettu lailla 358/2021 muutoksin") == []

from __future__ import annotations

from lawvm.core.payload_realization import (
    PayloadRealizationUnit,
    audit_payload_realization,
    payload_realization_gap_findings,
)


def test_audit_payload_realization_reports_missing_substantive_chunk() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text that should land.",),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="The folded state still contains unrelated old text.",
    )

    assert len(gaps) == 1
    assert gaps[0].unit_id == "section_1"


def test_audit_payload_realization_accepts_normalized_text_match() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text, that should land.",),
        ),
    )

    gaps = audit_payload_realization(
        units=units,
        after_text="Substantive payload text that should land",
    )

    assert gaps == ()


def test_payload_realization_gap_findings_use_shared_coverage_code() -> None:
    units = (
        PayloadRealizationUnit(
            unit_id="section_1",
            unit_kind="section",
            observed_label="1",
            text_chunks=("Substantive payload text that should land.",),
        ),
    )
    gaps = audit_payload_realization(units=units, after_text="unrelated old text")

    findings = payload_realization_gap_findings(gaps, source_ref="2000/1")

    assert [finding.kind for finding in findings] == ["COVERAGE.PAYLOAD_REALIZATION_GAP"]
    assert findings[0].source_statute == "2000/1"

from __future__ import annotations

from lawvm.core.compile_views import (
    projection_rows_from_findings,
    quirks_used_from_findings,
    source_completeness_issues_from_findings,
    source_pathology_rows_from_findings,
)
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE, OBLIGATION_ROLE


def test_projection_rows_from_findings_sorts_by_role_then_kind() -> None:
    findings = (
        Finding(
            kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            role=OBLIGATION_ROLE,
            stage="apply",
            detail={},
            blocking=True,
        ),
        Finding(
            kind="ELAB.MISSING_PAYLOAD_SURFACE",
            role=OBSERVATION_ROLE,
            stage="apply",
            detail={},
            blocking=False,
        ),
    )

    rows = projection_rows_from_findings(findings)

    assert [row["kind"] for row in rows] == [
        "ELAB.MISSING_PAYLOAD_SURFACE",
        "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
    ]


def test_source_pathology_rows_from_findings_deduplicates_equivalent_rows() -> None:
    findings = (
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role=OBSERVATION_ROLE,
            stage="elab",
            source_statute="2024/1",
            detail={
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "partial whole-section payload",
                "target_unit_kind": "section",
                "target_label": "3",
                "detail": {"diagnostic_reason": "shape"},
            },
        ),
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role=OBSERVATION_ROLE,
            stage="elab",
            source_statute="2024/1",
            detail={
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "partial whole-section payload",
                "target_unit_kind": "section",
                "target_label": "3",
                "detail": {"diagnostic_reason": "shape"},
            },
        ),
    )

    rows = source_pathology_rows_from_findings(findings)

    assert len(rows) == 1
    assert rows[0]["code"] == "PARTIAL_WHOLE_SECTION_PAYLOAD"


def test_source_pathology_rows_from_findings_prefers_native_apply_row_over_elab_aliases() -> None:
    findings = (
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role=OBSERVATION_ROLE,
            stage="elab",
            source_statute="2024/1",
            detail={
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "legacy elab message",
                "target_unit_kind": "section",
                "target_label": "3",
                "detail": {"diagnostic_reason": "shape"},
            },
        ),
        Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            source_statute="2024/1",
            detail={
                "barrier_code": "APPLY.SOURCE_PATHOLOGY_DETECTED",
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "message": "native apply message",
                "target_unit_kind": "section",
                "target_label": "3",
                "detail": {"diagnostic_reason": "shape"},
            },
            blocking=True,
        ),
    )

    rows = source_pathology_rows_from_findings(findings)

    assert len(rows) == 1
    assert rows[0]["code"] == "PARTIAL_WHOLE_SECTION_PAYLOAD"
    assert rows[0]["message"] == "native apply message"


def test_quirks_used_from_findings_filters_to_governed_quirks_only() -> None:
    findings = (
        Finding(
            kind="ELAB.UNASSIGNED_SPARSE_SLOTS",
            role=OBSERVATION_ROLE,
            stage="elab",
            detail={},
        ),
        Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            detail={},
            blocking=True,
        ),
    )

    result = quirks_used_from_findings(findings)

    assert tuple(f.kind for f in result) == ("ELAB.UNASSIGNED_SPARSE_SLOTS",)


def test_source_completeness_issues_from_findings_collects_obs_and_obligations() -> None:
    findings = (
        Finding(
            kind="ELAB.MISSING_PAYLOAD_SURFACE",
            role=OBSERVATION_ROLE,
            stage="elab",
            detail={},
        ),
        Finding(
            kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
            role=OBLIGATION_ROLE,
            stage="replay",
            detail={},
            blocking=True,
        ),
        Finding(
            kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            role=OBLIGATION_ROLE,
            stage="elab",
            detail={},
            blocking=True,
        ),
        Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="replay",
            detail={"barrier_code": "APPLY.SOURCE_PATHOLOGY_DETECTED"},
            blocking=True,
        ),
    )

    issues = source_completeness_issues_from_findings(findings)

    assert tuple(f.kind for f in issues) == (
        "ELAB.MISSING_PAYLOAD_SURFACE",
        "APPLY.SOURCE_CORRECTED_BY_PATCH",
        "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        "RUNTIME.VIOLATION",
    )

"""Finding adapters for Finland body-coverage analysis."""

from __future__ import annotations

from lawvm.core.phase_result import Finding


def high_uncovered_body_degraded_finding(
    *,
    source_statute: str,
    uncovered_count: int,
    total_units: int,
    uncov_ratio: float,
    confidence: float,
    signals: list[str],
) -> Finding:
    """Build the typed finding for a degraded uncovered-body chapter insert."""
    return Finding(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        role="obligation",
        stage="coverage_analysis",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": (
                "chapter-level INSERT plan has high uncovered body ratio; "
                "fallback proceeded with explicit degraded confidence"
            ),
            "uncovered_count": uncovered_count,
            "total_units": total_units,
            "uncov_ratio": round(uncov_ratio, 4),
            "confidence": confidence,
            "signals": signals,
        },
    )


def coverage_ignored_unit_finding(
    *,
    source_statute: str,
    unit_kind: str,
    reason: str,
    observed_label: str | None,
    parent_label: str | None,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.BODY_UNIT_IGNORED",
        role="observation",
        stage="coverage_analysis",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Body coverage ignored a malformed or unlabeled source unit",
            "unit_kind": unit_kind,
            "reason": reason,
            "observed_label": observed_label or "",
            "parent_label": parent_label or "",
            "evidence": list(evidence),
        },
    )


def coverage_rejected_claim_finding(
    *,
    source_statute: str,
    reason: str,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.CLAIM_REJECTED",
        role="observation",
        stage="coverage_analysis",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Body coverage rejected a targetless or unsupported coverage claim",
            "reason": reason,
            "evidence": list(evidence),
        },
    )


def coverage_unresolved_gap_finding(
    *,
    source_statute: str,
    disposition: str,
    unit_kind: str,
    observed_label: str | None,
    parent_label: str | None,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.UNRESOLVED_BODY_GAP",
        role="obligation",
        stage="coverage_analysis",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": "Body coverage found an unresolved uncovered unit",
            "disposition": disposition,
            "unit_kind": unit_kind,
            "observed_label": observed_label or "",
            "parent_label": parent_label or "",
            "evidence": list(evidence),
        },
    )

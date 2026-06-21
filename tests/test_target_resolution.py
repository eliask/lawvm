from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.diagnostic_records import validate_diagnostic_detail
from lawvm.core.frozen_values import FrozenDict
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_FALLBACK,
    SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE,
    TARGET_AMBIGUOUS,
    TARGET_FALLBACK_RESOLVED,
    TARGET_REJECTED,
    TARGET_RESOLVED,
    TargetResolutionStatus,
    TargetResolutionCandidate,
    TargetResolutionCoverage,
    target_resolution_candidate_from_mapping,
)


def test_target_resolution_coverage_projects_selected_target() -> None:
    detail = TargetResolutionCoverage(
        rule_id="test_target_exact",
        phase="elaboration",
        reason="explicit source target matched exactly one live node",
        resolution_status=TARGET_RESOLVED,
        source_target="section:5",
        selected_target="section:5",
        candidate_count=1,
        candidates=(
            TargetResolutionCandidate(
                target="section:5",
                reason="explicit_label_match",
                detail={"node_id": "s5"},
            ),
        ),
        scope_confidence=SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE,
        detail={"op_id": "op-1"},
    ).to_diagnostic_detail()

    assert detail == {
        "rule_id": "test_target_exact",
        "phase": "elaboration",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "family": "target_resolution",
        "reason": "explicit source target matched exactly one live node",
        "target_resolution_status": "resolved",
        "source_target": "section:5",
        "candidate_count": 1,
        "target_candidates": (
            {
                "target": "section:5",
                "reason": "explicit_label_match",
                "node_id": "s5",
            },
        ),
        "selected_target": "section:5",
        "selected_target_differs_from_source": False,
        "scope_confidence": "inferred_from_live_unique",
        "op_id": "op-1",
    }
    assert validate_diagnostic_detail(detail) == ()


def test_target_resolution_coverage_records_fallback_difference() -> None:
    detail = TargetResolutionCoverage(
        rule_id="test_target_fallback",
        phase="elaboration",
        reason="source target required named recovery",
        resolution_status=TARGET_FALLBACK_RESOLVED,
        source_target="chapter:2/section:5",
        selected_target="chapter:2/section:5/subsection:1",
        candidate_count=1,
        scope_confidence=SCOPE_CONFIDENCE_FALLBACK,
        blocking=True,
    ).to_diagnostic_detail()

    assert detail["blocking"] is True
    assert detail["strict_disposition"] == "block"
    assert detail["target_resolution_status"] == "fallback_resolved"
    assert detail["selected_target_differs_from_source"] is True
    assert validate_diagnostic_detail(detail) == ()


def test_target_resolution_coverage_can_record_ambiguity_without_selection() -> None:
    detail = TargetResolutionCoverage(
        rule_id="test_target_ambiguous",
        phase="elaboration",
        reason="two same-label targets remained plausible",
        resolution_status=TARGET_AMBIGUOUS,
        source_target="section:5",
        candidate_count=2,
        candidates=(
            TargetResolutionCandidate(target="chapter:1/section:5"),
            TargetResolutionCandidate(target="chapter:2/section:5"),
        ),
        blocking=True,
    ).to_diagnostic_detail()

    assert detail["target_resolution_status"] == "ambiguous"
    assert detail["candidate_count"] == 2
    assert "selected_target" not in detail
    assert validate_diagnostic_detail(detail) == ()


def test_target_resolution_coverage_rejects_reserved_detail_keys() -> None:
    with pytest.raises(ValueError, match="selected_target"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="bad detail",
            resolution_status=TARGET_REJECTED,
            source_target="section:5",
            detail={"selected_target": "section:6"},
        )


def test_target_resolution_candidate_rejects_reserved_detail_keys() -> None:
    with pytest.raises(ValueError, match="target"):
        TargetResolutionCandidate(
            target="section:5",
            reason="exact",
            detail={"target": "section:6"},
        )


def test_target_resolution_coverage_requires_selected_target_for_resolved_status() -> None:
    with pytest.raises(ValueError, match="requires selected_target"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="missing selected target",
            resolution_status=TARGET_RESOLVED,
            source_target="section:5",
            candidate_count=1,
        )


def test_target_resolution_coverage_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="bad status",
            resolution_status=cast(TargetResolutionStatus, "exact_source_path"),
            source_target="section:5",
        )


def test_target_resolution_coverage_requires_candidate_count_for_selected_status() -> None:
    with pytest.raises(ValueError, match="candidate_count >= 1"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="selected target without counted candidate",
            resolution_status=TARGET_RESOLVED,
            source_target="section:5",
            selected_target="section:5",
        )


def test_target_resolution_coverage_rejects_unknown_scope_confidence() -> None:
    with pytest.raises(ValueError, match="scope_confidence must be one of"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="bad confidence",
            resolution_status=TARGET_REJECTED,
            source_target="section:5",
            scope_confidence="probably",
        )


def test_target_resolution_coverage_candidate_count_covers_listed_candidates() -> None:
    with pytest.raises(ValueError, match="candidate_count"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="bad count",
            resolution_status=TARGET_AMBIGUOUS,
            source_target="section:5",
            candidate_count=1,
            candidates=(
                TargetResolutionCandidate(target="chapter:1/section:5"),
                TargetResolutionCandidate(target="chapter:2/section:5"),
            ),
        )


def test_target_resolution_coverage_resolved_selection_must_be_listed_candidate() -> None:
    with pytest.raises(ValueError, match="selected_target must be one of the listed candidates"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="resolved target selected outside candidates",
            resolution_status=TARGET_RESOLVED,
            source_target="section:5",
            selected_target="chapter:2/section:5",
            candidate_count=1,
            candidates=(
                TargetResolutionCandidate(target="chapter:1/section:5"),
            ),
        )


def test_target_resolution_coverage_recovery_selection_may_differ_from_listed_candidates() -> None:
    detail = TargetResolutionCoverage(
        rule_id="test_target_recovery",
        phase="elaboration",
        reason="frontend recorded named fallback after listed candidates failed",
        resolution_status=TARGET_FALLBACK_RESOLVED,
        source_target="section:5",
        selected_target="chapter:2/section:5",
        candidate_count=1,
        candidates=(
            TargetResolutionCandidate(target="chapter:1/section:5"),
        ),
        scope_confidence=SCOPE_CONFIDENCE_FALLBACK,
    ).to_diagnostic_detail()

    assert detail["target_resolution_status"] == "fallback_resolved"
    assert detail["selected_target"] == "chapter:2/section:5"


def test_target_resolution_candidate_from_mapping_preserves_local_payload() -> None:
    candidate = target_resolution_candidate_from_mapping(
        {"target": "section:5", "reason": "exact", "kind": "section"}
    )

    assert candidate.to_dict() == {
        "target": "section:5",
        "reason": "exact",
        "kind": "section",
    }


def test_target_resolution_coverage_normalizes_candidates_and_detail() -> None:
    candidate_detail = {"nested": {"labels": ["5"]}}
    candidate = TargetResolutionCandidate(
        target="section:5",
        reason="exact",
        detail=candidate_detail,
    )
    candidates = [candidate]

    certificate = TargetResolutionCoverage(
        rule_id="test_target_exact",
        phase="elaboration",
        reason="explicit source target matched exactly one live node",
        resolution_status=TARGET_RESOLVED,
        source_target="section:5",
        selected_target="section:5",
        candidate_count=1,
        candidates=cast(Any, candidates),
        detail={"op_id": "op-1"},
    )

    candidates.clear()
    candidate_detail["nested"]["labels"].append("mutated")

    assert certificate.candidates == (candidate,)
    assert isinstance(candidate.detail, FrozenDict)
    assert candidate.detail["nested"]["labels"] == ("5",)
    assert certificate.to_diagnostic_detail()["target_candidates"][0]["nested"]["labels"] == (
        "5",
    )


def test_target_resolution_coverage_rejects_malformed_detail_lanes() -> None:
    with pytest.raises(ValueError, match="candidates must contain TargetResolutionCandidate"):
        TargetResolutionCoverage(
            rule_id="test_target_bad",
            phase="elaboration",
            reason="bad candidate",
            resolution_status=TARGET_AMBIGUOUS,
            source_target="section:5",
            candidate_count=1,
            candidates=cast(Any, ("not-a-candidate",)),
        )

    with pytest.raises(ValueError, match="detail must be a mapping"):
        TargetResolutionCandidate(target="section:5", detail=cast(Any, []))

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.observation_registry import validate_finding_projection
from lawvm.core.phase_result import (
    Finding,
    Observation,
    Obligation,
    PhaseBuilder,
    PhaseResult,
    Violation,
)


def test_phase_result_findings_collect_runtime_roles_and_blocking_state() -> None:
    """PhaseResult should carry only runtime-facing finding rows: observation/obligation/violation."""
    pr = PhaseResult(
        output=[1, 2, 3],
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="elab",
                detail={"a": 1},
                blocking=False,
            ),
            Finding(
                kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
                role="obligation",
                stage="strict",
                detail={"b": 2},
                blocking=True,
            ),
            Finding(
                kind="RUNTIME.VIOLATION",
                role="violation",
                stage="apply",
                detail={"c": 3},
                blocking=True,
            ),
        ),
    )

    findings = pr.findings()

    assert [f.role for f in findings] == ["observation", "obligation", "violation"]
    assert findings[0].blocking is False
    assert findings[0].detail == {"a": 1}
    assert findings[1].blocking is True
    assert findings[2].role == "violation"
    assert findings[2].blocking is True


def test_finding_rejects_invalid_role() -> None:
    with pytest.raises(ValueError, match="invalid role"):
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role=cast(Any, "something-else"),
            stage="elab",
            detail={},
        )


def test_finding_rejects_registry_role_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 'observation'"):
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="violation",
            stage="elab",
            detail={},
            blocking=True,
        )


def test_finding_rejects_observation_blocking_true() -> None:
    with pytest.raises(ValueError, match="observation.*blocking=True"):
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="elab",
            detail={},
            blocking=True,
        )


def test_finding_rejects_violation_blocking_false() -> None:
    with pytest.raises(ValueError, match="violation.*blocking=True"):
        Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="apply",
            detail={},
            blocking=False,
        )


def test_tree_invariant_kind_requires_violation_role_in_runtime_finding_rows() -> None:
    """Tree invariant findings are direct runtime violations, not obligations."""
    kind = "APPLY.TREE_INVARIANT_VIOLATION"

    builder = PhaseBuilder()
    with pytest.raises(ValueError, match="expected 'violation'"):
        builder.oblige(kind, "apply", {"section": "1"})

    validate_finding_projection(kind, "violation", True)

    with pytest.raises(ValueError, match="expected 'violation'"):
        Finding(
            kind=kind,
            role="obligation",
            stage="apply",
            detail={"section": "1"},
            blocking=True,
        )


def test_phase_builder_rejects_forged_finding_projection() -> None:
    """Forged Finding payloads must still pass the same registry/runtime projection checks."""
    forged = object.__new__(Finding)
    object.__setattr__(forged, "kind", "ELAB.SOURCE_PATHOLOGY")
    object.__setattr__(forged, "role", "violation")
    object.__setattr__(forged, "stage", "elab")
    object.__setattr__(forged, "detail", {})
    object.__setattr__(forged, "source_statute", "")
    object.__setattr__(forged, "blocking", True)

    with pytest.raises(ValueError, match="expected 'observation'"):
        PhaseBuilder().add_findings((forged,))


def test_phase_result_rejects_non_finding_ledger_items() -> None:
    with pytest.raises(TypeError, match="Finding instances"):
        PhaseResult(output=None, findings=(cast(Any, object()),))


def test_phase_result_finding_detail_is_frozen_recursively() -> None:
    source_detail: dict[str, Any] = {
        "target": {"path": ["section", "1"]},
        "candidates": [{"label": "1"}],
    }

    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="elab",
        detail=source_detail,
    )
    source_detail["target"]["path"].append("mutated")
    source_detail["candidates"].append({"label": "2"})

    assert finding.detail == {
        "target": {"path": ("section", "1")},
        "candidates": ({"label": "1"},),
    }
    frozen_detail = cast(Any, finding.detail)
    with pytest.raises(TypeError, match="immutable"):
        frozen_detail["extra"] = "blocked"


def test_phase_signal_details_are_frozen_recursively() -> None:
    observation = Observation(
        kind="ELAB.SOURCE_PATHOLOGY",
        stage="elab",
        detail={"items": ["a"]},
    )
    obligation = Obligation(
        kind="ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
        stage="strict",
        detail={"items": ["b"]},
    )
    violation = Violation(
        kind="RUNTIME.VIOLATION",
        stage="apply",
        detail={"items": ["c"]},
    )

    assert observation.detail["items"] == ("a",)
    assert obligation.detail["items"] == ("b",)
    assert violation.detail["items"] == ("c",)


def test_phase_signal_details_reject_non_mappings() -> None:
    with pytest.raises(TypeError, match="Observation.detail must be a mapping"):
        Observation(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="elab",
            detail=cast(Any, ["not", "a", "mapping"]),
        )
    with pytest.raises(TypeError, match="Finding.detail must be a mapping"):
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="elab",
            detail=cast(Any, ["not", "a", "mapping"]),
        )


def test_phase_builder_violate_accepts_tree_invariant_violation_kind() -> None:
    builder = PhaseBuilder()
    builder.violate(
        "APPLY.TREE_INVARIANT_VIOLATION",
        "apply",
        {"section": "1"},
    )
    result = builder.finish(None)
    findings = result.findings()
    assert len(findings) == 1
    assert findings[0].kind == "APPLY.TREE_INVARIANT_VIOLATION"
    assert findings[0].role == "violation"

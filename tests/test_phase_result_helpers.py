"""Tests for PhaseResult finding projection views.

Run:
    uv run pytest tests/test_phase_result_helpers.py -v
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress, OperationSource
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.temporal import ActivationRule
from lawvm.core.provenance import MigrationEvent


def _obs(kind: str, stage: str = "test_stage") -> Finding:
    return Finding(
        kind=kind,
        role="observation",
        stage=stage,
        detail={},
        source_statute="",
        blocking=False,
    )


def test_observations_projection_returns_tuple() -> None:
    obs = _obs("ELAB.SOURCE_PATHOLOGY")
    pr = PhaseResult(output=None, findings=(obs,))
    assert tuple(f for f in pr.findings() if f.role == "observation") == (obs,)
    assert isinstance(pr.findings(), tuple)


def test_observations_projection_preserves_order_after_merge() -> None:
    obs_a = _obs("ELAB.SOURCE_PATHOLOGY", "s1")
    obs_b = _obs("ELAB.SOURCE_PATHOLOGY", "s2")
    obs_other = _obs("PARSE.DUPLICATE_TARGET_OP", "s2")
    pr_a = PhaseResult(output="a", findings=(obs_a,))
    pr_b = PhaseResult(
        output="b",
        findings=(obs_b, obs_other),
    )
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "observation") == (obs_a, obs_b, obs_other)


def test_violations_projection_returns_tuple() -> None:
    vio = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply",
        detail={"message": "boom"},
        source_statute="2024/1",
        blocking=True,
    )
    pr = PhaseResult(output=None, findings=(vio,))
    assert tuple(f for f in pr.findings() if f.role == "violation") == (vio,)
    assert isinstance(pr.findings(), tuple)


def test_violations_projection_preserves_order_after_merge() -> None:
    vio_a = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply_a",
        detail={"message": "a"},
        source_statute="2024/1",
        blocking=True,
    )
    vio_b = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply_b",
        detail={"message": "b"},
        source_statute="2024/2",
        blocking=True,
    )
    pr_a = PhaseResult(output="a", findings=(vio_a,))
    pr_b = PhaseResult(output="b", findings=(vio_b,))
    merged = pr_a.merge(pr_b)
    assert tuple(f for f in merged.findings() if f.role == "violation") == (vio_a, vio_b)


def test_migration_events_preserve_order_after_merge() -> None:
    migration_a = MigrationEvent(
        event_id="mig:a",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "1a"),)),
    )
    migration_b = MigrationEvent(
        event_id="mig:b",
        kind="move",
        from_address=LegalAddress(path=(("section", "2"),)),
        to_address=LegalAddress(path=(("section", "2a"),)),
    )
    pr_a = PhaseResult(output="a", migration_events=(migration_a,))
    pr_b = PhaseResult(output="b", migration_events=(migration_b,))
    merged = pr_a.merge(pr_b)
    assert merged.migration_events == (migration_a, migration_b)


def test_phase_result_summary_accessors_project_derived_kinds() -> None:
    migration = MigrationEvent(
        event_id="mig:a",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "1a"),)),
    )
    temporal = TemporalEvent(
        event_id="temporal:a",
        kind="commence",
        scope=TemporalScope(target_statute="1991/1"),
        activation_rule=ActivationRule(kind="fixed_date", effective_date="2024-01-01"),
        source=OperationSource(statute_id="2024/1", enacted="2024-01-01"),
    )
    pr = PhaseResult(output=None, migration_events=(migration,), temporal_events=(temporal,))

    assert pr.migration_event_kinds == ("renumber",)
    assert pr.temporal_event_kinds == ("commence",)
    assert pr.temporal_events_with_activation_rules == 1
    assert pr.temporal_events_with_source == 1
    assert pr.temporal_event_activation_rule_kinds == ("fixed_date",)

"""Runtime dispatch for ElaborationRuleRegistry-covered pipelines."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from lawvm.core.phase_result import Finding, OBSERVATION_ROLE
from lawvm.finland.elaboration_rule_registry import ElaborationRuleSpec, rule_by_id

T = TypeVar("T")

UNCOVERED_BODY_RECOVERY_PIPELINE: tuple[str, ...] = (
    "fi.uncovered.recovery_prepare",
    "fi.uncovered.recovery_iteration",
    "fi.uncovered.recovery_runner",
    "fi.uncovered.body_recovery",
    "fi.uncovered.recovery_findings",
)

PROCESS_AMENDMENT_PIPELINE: tuple[str, ...] = (
    "fi.process.runtime",
    "fi.process.acquisition",
    "fi.process.precompile_selection",
    "fi.process.frontend_normalization",
    "fi.process.structural_prepare",
    "fi.process.temporal_authority",
    "fi.process.compile_signals",
    "fi.process.apply_projection",
    "fi.process.temporal_postprocessing",
    "fi.process.failed_op_governance",
    "fi.process.apply_fold",
    "fi.process.result_builder",
)


def validate_elaboration_pipeline(rule_ids: tuple[str, ...]) -> tuple[ElaborationRuleSpec, ...]:
    """Resolve *rule_ids* through the registry or fail fast."""
    specs: list[ElaborationRuleSpec] = []
    for rule_id in rule_ids:
        spec = rule_by_id(rule_id)
        if spec is None:
            raise ValueError(f"unregistered elaboration rule: {rule_id}")
        specs.append(spec)
    return tuple(specs)


def emit_elaboration_pipeline_observation(
    findings_out: list[Finding] | None,
    *,
    rule_ids: tuple[str, ...],
    source_statute: str,
    amendment_id: str,
    pipeline_family: str = "uncovered_body_recovery",
    stage: str = "elaboration",
) -> None:
    """Record that a named elaboration pipeline ran under registry rule ids."""
    if findings_out is None:
        return
    findings_out.append(
        Finding(
            kind="ELAB.REGISTRY_PIPELINE",
            role=OBSERVATION_ROLE,
            stage=stage,
            blocking=False,
            source_statute=source_statute,
            detail={
                "pipeline_family": pipeline_family,
                "amendment_id": amendment_id,
                "rule_ids": list(rule_ids),
                "rule_count": len(rule_ids),
            },
        )
    )


def emit_elaboration_stage_observation(
    findings_out: list[Finding] | None,
    *,
    rule_id: str,
    source_statute: str,
    amendment_id: str,
    stage_status: str = "completed",
) -> None:
    """Record one registry-owned elaboration stage execution."""
    if findings_out is None:
        return
    spec = rule_by_id(rule_id)
    if spec is None:
        raise ValueError(f"unregistered elaboration rule: {rule_id}")
    findings_out.append(
        Finding(
            kind="ELAB.REGISTRY_STAGE",
            role=OBSERVATION_ROLE,
            stage=spec.owner_phase,
            blocking=False,
            source_statute=source_statute,
            detail={
                "rule_id": rule_id,
                "module": spec.module,
                "amendment_id": amendment_id,
                "stage_status": stage_status,
            },
        )
    )


def run_registered_elaboration_stage(
    rule_id: str,
    stage_fn: Callable[[], T],
    *,
    findings_out: list[Finding] | None,
    source_statute: str,
    amendment_id: str,
) -> T:
    """Execute *stage_fn* after registry validation and stage observation."""
    validate_elaboration_pipeline((rule_id,))
    emit_elaboration_stage_observation(
        findings_out,
        rule_id=rule_id,
        source_statute=source_statute,
        amendment_id=amendment_id,
    )
    return stage_fn()


__all__ = [
    "PROCESS_AMENDMENT_PIPELINE",
    "UNCOVERED_BODY_RECOVERY_PIPELINE",
    "emit_elaboration_pipeline_observation",
    "emit_elaboration_stage_observation",
    "run_registered_elaboration_stage",
    "validate_elaboration_pipeline",
]

"""Registry of named Finland elaboration / uncovered recovery rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OwnerPhase = Literal["elaboration", "uncovered_recovery", "payload_normalize", "apply", "process"]


@dataclass(frozen=True, slots=True)
class ElaborationRuleSpec:
    """One named elaboration or uncovered-recovery rule family."""

    rule_id: str
    module: str
    owner_phase: OwnerPhase
    description: str


ELABORATION_RULE_REGISTRY: tuple[ElaborationRuleSpec, ...] = (
    ElaborationRuleSpec(
        rule_id="fi.uncovered.body_recovery",
        module="uncovered_body_recovery",
        owner_phase="uncovered_recovery",
        description="Recover operative body text omitted from sparse amendment payloads.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.kumotaan_recovery",
        module="uncovered_kumotaan_recovery",
        owner_phase="uncovered_recovery",
        description="Recover kumotaan/repeal clauses not covered by standard lowering.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.chapter_scaffold",
        module="uncovered_chapter_scaffold",
        owner_phase="uncovered_recovery",
        description="Scaffold missing chapter containers for sparse chapter payloads.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.target_resolve",
        module="uncovered_target_resolve",
        owner_phase="elaboration",
        description="Resolve under-specified uncovered-body targets against live state.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.omission_merge",
        module="uncovered_dispose",
        owner_phase="uncovered_recovery",
        description="Evaluate omission-merge candidates for uncovered sections.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.recovery_runner",
        module="uncovered_recovery_runner",
        owner_phase="uncovered_recovery",
        description="Orchestrate uncovered-body recovery passes and skip accounting.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.recovery_prepare",
        module="uncovered_recovery_prepare",
        owner_phase="uncovered_recovery",
        description="Prepare uncovered recovery context and candidate sections.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.recovery_iteration",
        module="uncovered_recovery_iteration",
        owner_phase="uncovered_recovery",
        description="Iterate uncovered recovery until stable or bounded.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.recovery_findings",
        module="uncovered_recovery_findings",
        owner_phase="uncovered_recovery",
        description="Emit findings for uncovered recovery skips and successes.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.uncovered.body_findings",
        module="uncovered_body_findings",
        owner_phase="uncovered_recovery",
        description="Emit findings for uncovered-body candidate audits.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.pipeline",
        module="process_pipeline",
        owner_phase="process",
        description="Coordinate one amendment act through acquisition, compile, apply, and governance.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.request",
        module="process_request",
        owner_phase="process",
        description="Typed request boundary for one process_muutoslaki invocation.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.call",
        module="process_call",
        owner_phase="process",
        description="Resolve process_muutoslaki call inputs into a typed amendment call.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.runtime",
        module="process_runtime",
        owner_phase="process",
        description="Build per-amendment runtime buffers, recorders, and compat sinks.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.acquisition",
        module="process_acquisition",
        owner_phase="process",
        description="Acquire amendment XML and route/operative-structure facts.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.route_rejection",
        module="process_route_rejection",
        owner_phase="process",
        description="Handle rejected or side-effect-only amendment routes.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.precompile_selection",
        module="process_precompile_selection",
        owner_phase="process",
        description="Select precompile/VTS enrichment path before frontend normalization.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.frontend_normalization",
        module="process_frontend_normalization",
        owner_phase="process",
        description="Normalize and compile sparse amendment frontend payloads.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.structural_prepare",
        module="process_structural_prepare",
        owner_phase="process",
        description="Prepare structural chapter-seed and restructure context before compile.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.temporal_authority",
        module="process_temporal_authority",
        owner_phase="process",
        description="Derive amendment effective, expiry, and issue dates.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.compile_signals",
        module="process_compile_signals",
        owner_phase="process",
        description="Project compile outputs into process findings and observations.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.apply_projection",
        module="process_apply_projection",
        owner_phase="process",
        description="Project apply-side migration and touch accounting into process signals.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.temporal_postprocessing",
        module="process_temporal_postprocessing",
        owner_phase="process",
        description="Post-apply temporal authority and commencement/expiry reconciliation.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.failed_op_governance",
        module="process_failed_op_governance",
        owner_phase="process",
        description="Govern failed operations and emit process-owned obligations.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.apply_fold",
        module="process_apply_fold",
        owner_phase="process",
        description="Normalize post-apply replay fold and label-dedup backstop.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.result_builder",
        module="process_result_builder",
        owner_phase="process",
        description="Assemble the typed PhaseResult for one processed amendment.",
    ),
    ElaborationRuleSpec(
        rule_id="fi.process.findings",
        module="process_findings",
        owner_phase="process",
        description="Record governed findings for process_muutoslaki phases.",
    ),
)


def rule_by_id(rule_id: str) -> ElaborationRuleSpec | None:
    for spec in ELABORATION_RULE_REGISTRY:
        if spec.rule_id == rule_id:
            return spec
    return None


def rules_for_module(module: str) -> tuple[ElaborationRuleSpec, ...]:
    return tuple(spec for spec in ELABORATION_RULE_REGISTRY if spec.module == module)


__all__ = [
    "ELABORATION_RULE_REGISTRY",
    "ElaborationRuleSpec",
    "OwnerPhase",
    "rule_by_id",
    "rules_for_module",
]

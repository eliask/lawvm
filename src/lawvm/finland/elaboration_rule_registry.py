"""Registry of named Finland elaboration / uncovered recovery rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OwnerPhase = Literal["elaboration", "uncovered_recovery", "payload_normalize", "apply"]


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

"""Static catalog of Finland recovery rule ids used by proof surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoveryRuleSpec:
    """One recovery rule id with its proof-surface authorization prefix."""

    rule_id: str
    kind: str
    owner_surface: str


RECOVERY_RULE_REGISTRY: tuple[RecoveryRuleSpec, ...] = (
    RecoveryRuleSpec(
        rule_id="fi_recovery_sparse_merge",
        kind="sparse_merge",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_recovery_omission_merge",
        kind="omission_merge",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_recovery_uncovered_body",
        kind="uncovered_body",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_recovery_kumotaan",
        kind="kumotaan",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_recovery_chapter_scaffold",
        kind="chapter_scaffold",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_finlex_inline_repeal_stub_confirmed",
        kind="inline_repeal_stub",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_finlex_inline_repeal_stub_disagrees",
        kind="inline_repeal_stub",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_finlex_inline_repeal_stub_unresolved",
        kind="inline_repeal_stub",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_sparse_slot_binding_candidate_set",
        kind="sparse_slot_binding",
        owner_surface="proof_surfaces",
    ),
    RecoveryRuleSpec(
        rule_id="fi_sparse_slot_ambiguous_binding_candidate_set",
        kind="sparse_slot_binding",
        owner_surface="proof_surfaces",
    ),
)


def recovery_rule_ids() -> tuple[str, ...]:
    return tuple(spec.rule_id for spec in RECOVERY_RULE_REGISTRY)


__all__ = [
    "RECOVERY_RULE_REGISTRY",
    "RecoveryRuleSpec",
    "recovery_rule_ids",
]

"""Static catalog of Finland recovery/strictness finding authorization metadata."""

from __future__ import annotations

from dataclasses import dataclass
from lawvm.core.quirks_disposition import QuirksDisposition


_RECOVERY_AUTHORIZATION_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "recovery_rule_ownership_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_RECOVERY_AUTHORIZATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "recovery_projection_as_replay_authorization",
    "strict_recovery_row_as_quirks_permission",
    "recovery_finding_as_mutation_boundary_proof",
    "recovery_projection_as_target_widening",
)


@dataclass(frozen=True, slots=True)
class FinlandRecoveryAuthorizationRule:
    """Declarative projection metadata for Finland recovery/strictness findings."""

    kind: str
    owner_phase: str
    family: str
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD
    validator_status: str = "not_validated_for_replay_promotion"
    required_proofs: tuple[str, ...] = _RECOVERY_AUTHORIZATION_REQUIRED_PROOFS
    forbidden_shortcuts: tuple[str, ...] = _RECOVERY_AUTHORIZATION_FORBIDDEN_SHORTCUTS
    safe_default: str = "treat_recovery_projection_as_diagnostic_not_replay_authorization"

    def blocks_in_strict(self) -> bool:
        """Whether strict acceptance blocks this recovery finding.

        Typed replacement for the stringly ``rule.strict_disposition == "block"``
        comparison at the projector call sites. The serialized
        ``strict_disposition`` string is unchanged; this method is the canonical
        way to ask the policy question, so consumers stop matching the literal.
        """
        return self.strict_disposition == "block"


RECOVERY_AUTHORIZATION_RULES: dict[str, FinlandRecoveryAuthorizationRule] = {
    "PARSE.EXTRACTION_FALLBACK": FinlandRecoveryAuthorizationRule(
        kind="PARSE.EXTRACTION_FALLBACK",
        owner_phase="surface_parse",
        family="formula_extraction_recovery",
    ),
    "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER": FinlandRecoveryAuthorizationRule(
        kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
        owner_phase="surface_parse",
        family="semantic_collapse_recovery",
    ),
    "PARSE.TARGET_GUESSING": FinlandRecoveryAuthorizationRule(
        kind="PARSE.TARGET_GUESSING",
        owner_phase="surface_parse",
        family="target_resolution_recovery",
    ),
    "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": FinlandRecoveryAuthorizationRule(
        kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
        owner_phase="canonical_op_compilation",
        family="context_dependent_anchor_resolution",
    ),
    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE": FinlandRecoveryAuthorizationRule(
        kind="ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPLIT_SINGLE_TARGET_SUBSECTION_CARRIED_LIVE_TAIL",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPLIT_FINAL_LIST_ITEM_TRAILING_SUBSECTION",
        owner_phase="typed_elaboration",
        family="payload_source_shape_recovery",
    ),
    "ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL": FinlandRecoveryAuthorizationRule(
        kind="ELAB.FOLD_SINGLE_INSERT_SUBSECTION_LIST_TAIL",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.CONTAINER_PRUNED_SHADOWED": FinlandRecoveryAuthorizationRule(
        kind="ELAB.CONTAINER_PRUNED_SHADOWED",
        owner_phase="typed_elaboration",
        family="payload_ownership_recovery",
    ),
    "ELAB.SPARSE_DESCENDANT_LABEL_OMISSION_MERGE": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPARSE_DESCENDANT_LABEL_OMISSION_MERGE",
        owner_phase="typed_elaboration",
        family="sparse_payload_elaboration_recovery",
    ),
    "ELAB.PAYLOAD_COMPLETENESS": FinlandRecoveryAuthorizationRule(
        kind="ELAB.PAYLOAD_COMPLETENESS",
        owner_phase="payload_elaboration",
        family="payload_completeness_recovery",
    ),
    "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET": FinlandRecoveryAuthorizationRule(
        kind="ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET",
        owner_phase="typed_elaboration",
        family="payload_ownership_recovery",
    ),
    # NOTE: report-only metadata. This authorization registry is not wired into a
    # runtime blocking decision; ELAB.SPARSE_PAYLOAD_LEFTOVER is registered
    # warn/obligation (non-blocking) in observation_registry, which governs actual
    # enforcement. The "block" disposition here is an inconsistent metadata note,
    # not a live gate — left as-is (behaviour unchanged) pending a metadata audit.
    "ELAB.SPARSE_PAYLOAD_LEFTOVER": FinlandRecoveryAuthorizationRule(
        kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
        owner_phase="typed_elaboration",
        family="sparse_payload_frontier",
        strict_disposition="block",
    ),
    "ELAB.STRICT_REJECTED_OPERATION": FinlandRecoveryAuthorizationRule(
        kind="ELAB.STRICT_REJECTED_OPERATION",
        owner_phase="typed_elaboration",
        family="strict_operation_barrier",
        strict_disposition="block",
    ),
    "APPLY.UNCOVERED_BODY_RECOVERY": FinlandRecoveryAuthorizationRule(
        kind="APPLY.UNCOVERED_BODY_RECOVERY",
        owner_phase="replay_apply",
        family="uncovered_body_recovery",
    ),
    "APPLY.STRICT_REJECTED_UNCOVERED_BODY": FinlandRecoveryAuthorizationRule(
        kind="APPLY.STRICT_REJECTED_UNCOVERED_BODY",
        owner_phase="replay_apply",
        family="uncovered_body_recovery",
        strict_disposition="block",
    ),
    "APPLY.FALLBACK_WHOLE_SECTION_REPLACE": FinlandRecoveryAuthorizationRule(
        kind="APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
        owner_phase="replay_apply",
        family="whole_section_replace_fallback",
    ),
    "APPLY.WORD_SUBSTITUTION": FinlandRecoveryAuthorizationRule(
        kind="APPLY.WORD_SUBSTITUTION",
        owner_phase="replay_apply",
        family="text_substitution_recovery",
    ),
    "APPLY.SOURCE_CORRECTED_BY_PATCH": FinlandRecoveryAuthorizationRule(
        kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
        owner_phase="replay_apply",
        family="source_corrected_by_patch",
    ),
    "APPLY.LEGACY_DISPATCH_FALLBACK": FinlandRecoveryAuthorizationRule(
        kind="APPLY.LEGACY_DISPATCH_FALLBACK",
        owner_phase="replay_apply",
        family="legacy_dispatch_fallback",
    ),
    "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED": FinlandRecoveryAuthorizationRule(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        owner_phase="payload_elaboration",
        family="uncovered_body_coverage_barrier",
        strict_disposition="block",
    ),
}


def recovery_authorization_rule(kind: str) -> FinlandRecoveryAuthorizationRule | None:
    """Return authorization metadata for a recovery finding kind, if registered."""
    return RECOVERY_AUTHORIZATION_RULES.get(str(kind or ""))


def recovery_authorization_kinds() -> tuple[str, ...]:
    return tuple(sorted(RECOVERY_AUTHORIZATION_RULES))


__all__ = [
    "FinlandRecoveryAuthorizationRule",
    "RECOVERY_AUTHORIZATION_RULES",
    "recovery_authorization_kinds",
    "recovery_authorization_rule",
]

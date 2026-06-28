"""Static catalog of Finland source-pathology proof projection metadata."""

from __future__ import annotations

from dataclasses import dataclass
from lawvm.core.quirks_disposition import QuirksDisposition

_DEFAULT_REQUIRED_PROOFS: tuple[str, ...] = (
    "source_identity_proof",
    "target_identity_proof",
    "payload_identity_or_manual_resolution_proof",
    "mutation_boundary_proof_before_replay_promotion",
)
_DEFAULT_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "treat_source_pathology_as_replay_authorization",
    "silently_widen_or_retarget_source_scope",
    "delete_or_overwrite_live_structure_to_match_oracle",
)
_DEFAULT_SAFE_DEFAULT = "preserve_uncertainty_and_do_not_promote_pathology_to_replay_authority"


@dataclass(frozen=True, slots=True)
class FinlandSourcePathologyProofRule:
    """Declarative projection metadata for a Finland source-pathology code."""

    code: str
    lane: str
    owner_phase: str
    strict_disposition: str
    quirks_disposition: QuirksDisposition
    frontier_family: str
    frontier_status: str
    required_claim_kind: str
    required_proofs: tuple[str, ...] = _DEFAULT_REQUIRED_PROOFS
    forbidden_shortcuts: tuple[str, ...] = _DEFAULT_FORBIDDEN_SHORTCUTS
    safe_default: str = _DEFAULT_SAFE_DEFAULT
    candidate_operation_family: str = "source_pathology_resolution"
    validator_status: str = "not_validated_for_replay_promotion"
    required_validator_checks: tuple[str, ...] = (
        "validate_source_pathology_resolution_claim",
        "validate_mutation_boundary_before_replay_promotion",
    )


SOURCE_PATHOLOGY_PROOF_RULES: dict[str, FinlandSourcePathologyProofRule] = {
    "EMPTY_OPERATIVE_BODY": FinlandSourcePathologyProofRule(
        code="EMPTY_OPERATIVE_BODY",
        lane="source_acquisition",
        owner_phase="source_acquisition",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_empty_operative_body",
        frontier_status="source_acquisition_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_artifact_identity_proof",
            "operative_body_inventory",
            "alternative_source_witness_or_source_correction_proof",
            "operation_cue_exhaustiveness_certificate",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default=(
            "treat_bodyless_source_as_non_executable_until_an_operative_source_witness_exists"
        ),
        forbidden_shortcuts=(
            "invent_operative_body_from_title_or_metadata",
            "treat_empty_source_as_no_effects_proof",
            "promote_alternative_source_without_digest_witness",
        ),
    ),
    "PARTIAL_WHOLE_SECTION_PAYLOAD": FinlandSourcePathologyProofRule(
        code="PARTIAL_WHOLE_SECTION_PAYLOAD",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_partial_whole_section_payload",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    ),
    "BODY_SECTION_LABEL_MISMATCH_PAYLOAD": FinlandSourcePathologyProofRule(
        code="BODY_SECTION_LABEL_MISMATCH_PAYLOAD",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_body_section_label_mismatch_payload",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_formula_target_identity_proof",
            "payload_section_label_identity_proof",
            "payload_binding_resolution_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="bind_payload_to_explicit_formula_target_only_with_recorded_source_pathology",
    ),
    "MALFORMED_BROAD_REPLACE_BODY": FinlandSourcePathologyProofRule(
        code="MALFORMED_BROAD_REPLACE_BODY",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_malformed_broad_replace_body",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    ),
    "CONTAINER_MEMBERSHIP_MISMATCH": FinlandSourcePathologyProofRule(
        code="CONTAINER_MEMBERSHIP_MISMATCH",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_container_membership_mismatch",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION",
    ),
    "UNSCOPED_ROOT_DUPLICATE_CONSUMED": FinlandSourcePathologyProofRule(
        code="UNSCOPED_ROOT_DUPLICATE_CONSUMED",
        lane="source_pathology",
        owner_phase="payload_normalization",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_unscoped_root_duplicate_consumed",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "scoped_target_identity_proof",
            "duplicate_wrapper_section_identity_proof",
            "source_pathology_resolution_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_unscoped_duplicate_as_source_pathology_without_extra_replay_authority",
        forbidden_shortcuts=(
            "consume_unscoped_duplicate_without_scoped_target_proof",
            "treat_wrapper_duplicate_as_permission_to_widen_target_scope",
            "promote_duplicate_consumption_as_replay_authorization",
        ),
    ),
    "SPARSE_ITEM_BODY_MISSING": FinlandSourcePathologyProofRule(
        code="SPARSE_ITEM_BODY_MISSING",
        lane="source_pathology",
        owner_phase="typed_elaboration",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_sparse_item_body_missing",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    ),
    "ITEM_TARGET_STRUCTURE_ABSENT": FinlandSourcePathologyProofRule(
        code="ITEM_TARGET_STRUCTURE_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_item_target_structure_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_item_target_identity_proof",
            "live_state_candidate_set_coverage",
            "payload_slot_identity_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "ITEM_TARGET_SLOT_OCCUPIED": FinlandSourcePathologyProofRule(
        code="ITEM_TARGET_SLOT_OCCUPIED",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_item_target_slot_occupied",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_item_target_identity_proof",
            "occupied_slot_lineage_proof",
            "payload_slot_identity_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "ITEM_TARGET_ANCHOR_ABSENT": FinlandSourcePathologyProofRule(
        code="ITEM_TARGET_ANCHOR_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_item_target_anchor_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_item_target_identity_proof",
            "live_state_candidate_set_coverage",
            "anchor_absence_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "ITEM_TARGET_POSITIONAL_REBIND": FinlandSourcePathologyProofRule(
        code="ITEM_TARGET_POSITIONAL_REBIND",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_item_target_positional_rebind",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_item_target_identity_proof",
            "live_state_candidate_set_coverage",
            "intrinsic_label_identity_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_positional_rebind_as_non_executable_target_resolution_frontier",
        forbidden_shortcuts=(
            "treat_ordinal_position_as_intrinsic_item_identity",
            "rebind_item_target_without_intrinsic_label_proof",
            "promote_positional_rebind_recovery_as_replay_authorization",
        ),
    ),
    "SUBSECTION_TARGET_ABSENT": FinlandSourcePathologyProofRule(
        code="SUBSECTION_TARGET_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_subsection_target_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_subsection_target_identity_proof",
            "live_state_candidate_set_coverage",
            "payload_slot_identity_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING": FinlandSourcePathologyProofRule(
        code="SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_section_replace_bootstrap_parent_missing",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_section_target_identity_proof",
            "parent_container_identity_proof",
            "bootstrap_rule_ownership_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_missing_parent_bootstrap_as_non_executable_frontier",
        forbidden_shortcuts=(
            "insert_section_under_unproven_parent_container",
            "treat_missing_parent_as_permission_for_unscoped_insert",
            "promote_bootstrap_failure_as_replay_authorization",
        ),
    ),
    "SAME_EFFECTIVE_CONTAINER_REPEAL_SHADOWED": FinlandSourcePathologyProofRule(
        code="SAME_EFFECTIVE_CONTAINER_REPEAL_SHADOWED",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_same_effective_container_repeal_shadowed",
        frontier_status="source_chain_frontier",
        required_claim_kind="fi.v1.SOURCE_CHAIN_RESOLUTION",
    ),
    "RECODIFICATION_SOURCE_CHAIN_GAP": FinlandSourcePathologyProofRule(
        code="RECODIFICATION_SOURCE_CHAIN_GAP",
        lane="source_pathology",
        owner_phase="source_chain_elaboration",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_recodification_source_chain_gap",
        frontier_status="source_chain_frontier",
        required_claim_kind="fi.v1.SOURCE_CHAIN_RESOLUTION",
    ),
    "RECODIFICATION_OMISSION_ONLY_SECTION_SHELL": FinlandSourcePathologyProofRule(
        code="RECODIFICATION_OMISSION_ONLY_SECTION_SHELL",
        lane="source_pathology",
        owner_phase="payload_surface_extraction",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_recodification_omission_only_section_shell",
        frontier_status="manual_frontier",
        required_claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    ),
    "TEMPORARY_SECTION_REBASE": FinlandSourcePathologyProofRule(
        code="TEMPORARY_SECTION_REBASE",
        lane="temporal_recovery",
        owner_phase="temporal_elaboration",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_temporary_section_rebase",
        frontier_status="temporal_frontier",
        required_claim_kind="fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "target_identity_proof",
            "temporal_applicability_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
    ),
    "DESTRUCTIVE_SHAPE_LOSS_RISK": FinlandSourcePathologyProofRule(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        lane="replay_recovery_risk",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_destructive_shape_loss_risk",
        frontier_status="mutation_boundary_frontier",
        required_claim_kind="fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    ),
    "SUBSECTION_SHELL_REPLACE_KEPT": FinlandSourcePathologyProofRule(
        code="SUBSECTION_SHELL_REPLACE_KEPT",
        lane="replay_recovery_risk",
        owner_phase="replay_apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_subsection_shell_replace_kept",
        frontier_status="mutation_boundary_frontier",
        required_claim_kind="fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    ),
    "UNRESOLVED_DESCENDANT_SCOPE_CUE": FinlandSourcePathologyProofRule(
        code="UNRESOLVED_DESCENDANT_SCOPE_CUE",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_unresolved_descendant_scope_cue",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    ),
    "SUBSECTION_TARGET_REBOUND": FinlandSourcePathologyProofRule(
        code="SUBSECTION_TARGET_REBOUND",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_subsection_target_rebound",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_target_identity_proof",
            "live_state_candidate_set_coverage",
            "rebound_rule_ownership_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default=(
            "preserve_rebound_as_non_executable_target_resolution_frontier"
        ),
        forbidden_shortcuts=(
            "treat_live_unique_subsection_as_source_target_proof",
            "rebind_subsection_target_without_target_identity_proof",
            "promote_rebound_recovery_as_replay_authorization",
        ),
    ),
    "CONTAINER_REPLACE_TARGET_ABSENT": FinlandSourcePathologyProofRule(
        code="CONTAINER_REPLACE_TARGET_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_container_replace_target_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_container_target_identity_proof",
            "live_state_candidate_set_coverage",
            "container_absence_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_absent_container_replace_as_non_executable_frontier",
        forbidden_shortcuts=(
            "replace_nearest_live_container_when_explicit_target_is_absent",
            "insert_container_from_replace_without_target_identity_proof",
            "promote_absent_container_recovery_as_replay_authorization",
        ),
    ),
    "CONTAINER_OP_TARGET_ABSENT": FinlandSourcePathologyProofRule(
        code="CONTAINER_OP_TARGET_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_container_op_target_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_container_target_identity_proof",
            "live_state_candidate_set_coverage",
            "container_absence_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_absent_container_op_as_non_executable_frontier",
        forbidden_shortcuts=(
            "repeal_or_renumber_nearest_live_container_when_explicit_target_is_absent",
            "synthesize_container_from_repeal_without_target_identity_proof",
            "promote_absent_container_op_recovery_as_replay_authorization",
        ),
    ),
    "CONTAINER_OTSIKKO_PAYLOAD_ABSENT": FinlandSourcePathologyProofRule(
        code="CONTAINER_OTSIKKO_PAYLOAD_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_container_otsikko_payload_absent",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_container_target_identity_proof",
            "payload_heading_inventory",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_heading_payload_absence_as_non_executable_frontier",
        forbidden_shortcuts=(
            "synthesize_container_heading_from_metadata_or_num",
            "promote_payload_absent_heading_as_replay_authorization",
        ),
    ),
    "SECTION_INSERT_SCOPED_PARENT_ABSENT": FinlandSourcePathologyProofRule(
        code="SECTION_INSERT_SCOPED_PARENT_ABSENT",
        lane="target_resolution_recovery",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_section_insert_scoped_parent_absent",
        frontier_status="target_resolution_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_scoped_parent_target_identity_proof",
            "live_state_candidate_set_coverage",
            "container_absence_proof",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_absent_scoped_parent_insert_as_non_executable_frontier",
        forbidden_shortcuts=(
            "insert_section_at_nearest_live_parent_when_scoped_parent_is_absent",
            "seed_scoped_parent_scaffold_without_target_identity_proof",
            "promote_absent_parent_insert_as_replay_authorization",
        ),
    ),
    "UNHANDLED_STRUCTURE_OP": FinlandSourcePathologyProofRule(
        code="UNHANDLED_STRUCTURE_OP",
        lane="replay_recovery_risk",
        owner_phase="replay_apply",
        strict_disposition="block",
        quirks_disposition=QuirksDisposition.RECORD,
        frontier_family="fi_unhandled_structure_op",
        frontier_status="source_pathology_frontier",
        required_claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        required_proofs=(
            "source_identity_proof",
            "explicit_target_identity_proof",
            "operation_cue_exhaustiveness_certificate",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="preserve_unhandled_structure_op_as_non_executable_frontier",
        forbidden_shortcuts=(
            "route_unhandled_op_to_nearest_apply_arm_without_target_identity_proof",
            "promote_unhandled_op_fallthrough_as_replay_authorization",
        ),
    ),
}

def source_pathology_proof_rule(code: str) -> FinlandSourcePathologyProofRule:
    """Return declarative projection metadata for a pathology code."""

    code_text = str(code or "")
    return SOURCE_PATHOLOGY_PROOF_RULES.get(
        code_text,
        FinlandSourcePathologyProofRule(
            code=code_text or "UNKNOWN_SOURCE_PATHOLOGY",
            lane="source_pathology",
            owner_phase="typed_elaboration",
            strict_disposition="block",
            quirks_disposition=QuirksDisposition.RECORD,
            frontier_family="fi_unclassified_source_pathology",
            frontier_status="source_pathology_frontier",
            required_claim_kind="source_pathology_resolution",
        ),
    )


def registered_source_pathology_proof_rule_codes() -> tuple[str, ...]:
    """Return statically registered Finland source-pathology codes."""

    return tuple(sorted(SOURCE_PATHOLOGY_PROOF_RULES))

__all__ = [
    "FinlandSourcePathologyProofRule",
    "SOURCE_PATHOLOGY_PROOF_RULES",
    "registered_source_pathology_proof_rule_codes",
    "source_pathology_proof_rule",
]

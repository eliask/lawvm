"""UK projections into the shared execution authorization contract."""

from __future__ import annotations

from lawvm.core.execution_authorization import ExecutionAuthorization


def uk_execution_authorization_from_manual_frontier(
    *,
    manual_compile_status: str,
    manual_compile_rule_id: str,
    owner_phase: str,
    strict_disposition: str = "record",
    quirks_disposition: str = "record",
    validator_status: str = "",
) -> ExecutionAuthorization:
    """Build authorization facts for UK manual-frontier diagnostic rows."""
    status = str(manual_compile_status or "")
    rule_id = str(manual_compile_rule_id or "")
    if status == "deterministic_frontend_supported":
        return ExecutionAuthorization(
            executable=True,
            replay_authorized=True,
            authorization_status="replay_authorized",
            authorization_rule_id="uk_execution_authorization_deterministic_supported",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=(),
            safe_default="execute_lowered_operations",
            forbidden_shortcuts=(),
            detail={"manual_compile_status": status, "manual_compile_rule_id": rule_id},
        )
    if status == "deterministic_frontend_candidate":
        return _non_authorized_frontier(
            status="deterministic_frontend_work_required",
            rule_id="uk_execution_authorization_deterministic_candidate",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=("canonical_operation_compilation", "mutation_boundary_proof"),
            safe_default="block_until_compiler_rule_is_owned",
            manual_compile_status=status,
            manual_compile_rule_id=rule_id,
        )
    if status == "manual_compile_candidate":
        return _non_authorized_frontier(
            status="manual_claim_required",
            rule_id="uk_execution_authorization_manual_claim_required",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=(
                "source_identity",
                "target_identity",
                "action_family",
                "payload_or_boundary_identity",
                "temporal_extent_applicability",
                "mutation_boundary_proof",
            ),
            safe_default="block_until_validated_claim_authorizes_replay",
            manual_compile_status=status,
            manual_compile_rule_id=rule_id,
        )
    if status == "source_insufficient":
        return _non_authorized_frontier(
            status="source_insufficient",
            rule_id="uk_execution_authorization_source_insufficient",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=("official_source_witness", "payload_or_instruction_witness"),
            safe_default="block_and_over_retain_until_source_is_available",
            manual_compile_status=status,
            manual_compile_rule_id=rule_id,
        )
    if status == "non_textual_or_out_of_scope":
        return _non_authorized_frontier(
            status="out_of_scope",
            rule_id="uk_execution_authorization_non_textual_or_out_of_scope",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=("applicability_or_non_textual_semantics",),
            safe_default="do_not_replay_as_text_or_tree_mutation",
            manual_compile_status=status,
            manual_compile_rule_id=rule_id,
        )
    if status == "source_or_feed_target_conflict":
        return _non_authorized_frontier(
            status="source_target_conflict",
            rule_id="uk_execution_authorization_source_target_conflict",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=validator_status,
            required_proofs=("source_target_reconciliation", "authority_surface_selection"),
            safe_default="block_until_source_and_feed_targets_are_reconciled",
            manual_compile_status=status,
            manual_compile_rule_id=rule_id,
        )
    return _non_authorized_frontier(
        status="unclassified_frontier",
        rule_id="uk_execution_authorization_unclassified_frontier",
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=("phase_owner_classification", "frontier_family_classification"),
        safe_default="block_and_classify_before_replay",
        manual_compile_status=status,
        manual_compile_rule_id=rule_id,
    )


def _non_authorized_frontier(
    *,
    status: str,
    rule_id: str,
    owner_phase: str,
    strict_disposition: str,
    quirks_disposition: str,
    validator_status: str,
    required_proofs: tuple[str, ...],
    safe_default: str,
    manual_compile_status: str,
    manual_compile_rule_id: str,
) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=status,
        authorization_rule_id=rule_id,
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=required_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=(
            "oracle_backed_mutation",
            "target_guessing",
            "parent_widening",
            "unvalidated_manual_claim_execution",
        ),
        detail={
            "manual_compile_status": manual_compile_status,
            "manual_compile_rule_id": manual_compile_rule_id,
        },
    )

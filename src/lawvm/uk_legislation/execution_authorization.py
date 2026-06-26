"""UK projections into the shared execution authorization contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lawvm.core.compile_records import CompileRecord, is_blocking_compile_record
from lawvm.core.execution_authorization import ExecutionAuthorization


_COMPILE_LANE_PROOFS: dict[str, tuple[str, ...]] = {
    "source_parse": ("source_artifact_parse", "source_identity"),
    "effect_feed_parse": ("effect_metadata_parse", "effect_feed_witness"),
    "effect_source_pathology": ("source_pathology_resolution", "source_payload_witness"),
    "source_acquisition": ("official_source_witness", "source_acquisition_success"),
    "lowering": ("canonical_operation_compilation",),
    "authority": ("authority_surface_selection", "replay_authority_contract"),
}

_REPLAY_ADJUDICATION_BUCKET_PROOFS: dict[str, tuple[str, ...]] = {
    "replay_bug": ("replay_executor_fix", "mutation_boundary_proof"),
    "source_shape": ("source_shape_elaboration", "mutation_boundary_proof"),
    "text_surface": ("text_surface_preimage_or_payload_boundary",),
    "nonblocking_observation": ("replay_authority_not_claimed",),
    "unknown": ("replay_adjudication_classification",),
}


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
            authorization_status="deterministic_frontend_work_required",
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
            authorization_status="manual_claim_required",
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
            authorization_status="source_insufficient",
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
            authorization_status="out_of_scope",
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
            authorization_status="source_target_conflict",
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
        authorization_status="unclassified_frontier",
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


def uk_execution_authorization_from_compile_record(
    *,
    record: Mapping[str, Any],
    lane: str,
    owner_phase: str,
) -> ExecutionAuthorization:
    """Build authorization facts for UK compile diagnostic/rejection rows."""
    lane_id = str(lane or "unknown")
    if lane_id == "manual_compile_frontier":
        return uk_execution_authorization_from_manual_frontier(
            manual_compile_status=str(record.get("manual_compile_status") or ""),
            manual_compile_rule_id=str(record.get("manual_compile_rule_id") or ""),
            owner_phase=owner_phase,
            strict_disposition=str(record.get("strict_disposition") or "record"),
            quirks_disposition=str(record.get("quirks_disposition") or "record"),
            validator_status=str(record.get("validator_status") or ""),
        )
    compile_record = CompileRecord.from_mapping(record)
    blocking = is_blocking_compile_record(compile_record)
    strict_disposition = str(
        compile_record.strict_disposition or ("block" if blocking else "record")
    )
    quirks_disposition = str(record.get("quirks_disposition") or "record")
    if blocking:
        return _non_authorized_compile_record(
            authorization_status=f"{lane_id}_compile_blocked",
            rule_id=f"uk_execution_authorization_{lane_id}_compile_blocked",
            lane=lane_id,
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status="blocking_compile_record",
            required_proofs=_COMPILE_LANE_PROOFS.get(
                lane_id,
                ("compile_record_classification",),
            ),
            safe_default="block_until_missing_compile_proofs_are_available",
            record=record,
        )
    return _non_authorized_compile_record(
        authorization_status=f"{lane_id}_diagnostic_evidence_only",
        rule_id=f"uk_execution_authorization_{lane_id}_diagnostic_evidence_only",
        lane=lane_id,
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status="nonblocking_compile_observation",
        required_proofs=("canonical_operation_or_replay_authorization",),
        safe_default="record_diagnostic_without_promoting_to_replay_authority",
        record=record,
    )


def uk_execution_authorization_from_replay_adjudication(
    *,
    adjudication: Any,
    owner_phase: str,
    bucket: str,
) -> ExecutionAuthorization:
    """Build authorization facts for UK replay adjudication residual rows."""
    if isinstance(adjudication, Mapping):
        kind = str(adjudication.get("kind") or "")
        detail_obj = adjudication.get("detail")
    else:
        kind = str(getattr(adjudication, "kind", "") or "")
        detail_obj = getattr(adjudication, "detail", {})
    detail = dict(detail_obj) if isinstance(detail_obj, Mapping) else {}
    blocking = bool(detail.get("blocking", bucket not in {"nonblocking_observation"}))
    strict_disposition = str(
        detail.get("strict_disposition") or ("block" if blocking else "record")
    )
    quirks_disposition = str(detail.get("quirks_disposition") or "record")
    bucket_id = str(bucket or "unknown")
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=f"replay_adjudication_{bucket_id}",
        authorization_rule_id=f"uk_execution_authorization_replay_adjudication_{bucket_id}",
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status="replay_adjudication_residual",
        required_proofs=_REPLAY_ADJUDICATION_BUCKET_PROOFS.get(
            bucket_id,
            _REPLAY_ADJUDICATION_BUCKET_PROOFS["unknown"],
        ),
        safe_default="treat_adjudication_as_residual_not_replay_authority",
        forbidden_shortcuts=(
            "adjudication_as_replay_authority",
            "oracle_backed_mutation",
            "target_guessing",
            "residual_over_promotion",
        ),
        detail={
            "adjudication_kind": kind,
            "bucket": bucket_id,
            "record_rule_id": str(detail.get("rule_id") or kind),
        },
    )


def uk_execution_authorization_from_semantic_claim_validation(
    *,
    validator_status: str,
    owner_phase: str,
    strict_disposition: str = "record",
    quirks_disposition: str = "record",
) -> ExecutionAuthorization:
    """Build authorization facts for UK semantic-claim validation rows.

    The semantic-claim validator is intentionally non-executable. Passing rows
    validate provenance or preconditions only; they do not authorize replay.
    """
    status = str(validator_status or "")
    if status.startswith("validated_"):
        return _non_authorized_claim(
            authorization_status="validated_non_executable_claim",
            rule_id="uk_execution_authorization_semantic_claim_validated_non_executable",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=(
                "canonical_operation_compilation",
                "source_identity",
                "target_identity",
                "payload_or_boundary_identity",
                "temporal_extent_applicability",
                "mutation_boundary_proof",
                "replay_authorization_validator",
            ),
            safe_default="keep_claim_non_executable_until_replay_validator_exists",
        )
    if status == "rejected_schema":
        return _non_authorized_claim(
            authorization_status="claim_rejected_schema",
            rule_id="uk_execution_authorization_semantic_claim_rejected_schema",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=("valid_claim_schema", "non_executable_claim_shape"),
            safe_default="reject_claim_without_replay",
        )
    if status == "rejected_workqueue_missing":
        return _non_authorized_claim(
            authorization_status="claim_rejected_workqueue_missing",
            rule_id="uk_execution_authorization_semantic_claim_workqueue_missing",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=("matched_frontier_work_item",),
            safe_default="reject_claim_without_replay",
        )
    if status == "rejected_workqueue_mismatch":
        return _non_authorized_claim(
            authorization_status="claim_rejected_workqueue_mismatch",
            rule_id="uk_execution_authorization_semantic_claim_workqueue_mismatch",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=("workqueue_provenance_match",),
            safe_default="reject_claim_without_replay",
        )
    if status == "rejected_source_text_mismatch":
        return _non_authorized_claim(
            authorization_status="claim_rejected_source_text_mismatch",
            rule_id="uk_execution_authorization_semantic_claim_source_text_mismatch",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=("source_text_precondition_match",),
            safe_default="reject_claim_without_replay",
        )
    if status in {"rejected_live_state_missing", "rejected_live_state_mismatch"}:
        return _non_authorized_claim(
            authorization_status="claim_rejected_live_state",
            rule_id="uk_execution_authorization_semantic_claim_live_state_rejected",
            owner_phase=owner_phase,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
            validator_status=status,
            required_proofs=("live_target_precondition_match",),
            safe_default="reject_claim_without_replay",
        )
    return _non_authorized_claim(
        authorization_status="claim_validation_frontier",
        rule_id="uk_execution_authorization_semantic_claim_validation_frontier",
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=status,
        required_proofs=("semantic_claim_validator_classification",),
        safe_default="block_and_classify_before_replay",
    )


def uk_execution_authorization_from_residual_claim(
    *,
    claim: Mapping[str, Any],
    owner_phase: str,
    validator_status: str = "not_validated",
    strict_disposition: str = "record",
    quirks_disposition: str = "record",
) -> ExecutionAuthorization:
    """Build authorization facts for UK residual-claim workqueue rows."""
    tier = str(claim.get("selected_tier") or "UNRESOLVED")
    kind = str(claim.get("selected_kind") or "unknown")
    status = f"residual_claim_{_status_token(tier)}"
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=status,
        authorization_rule_id="uk_execution_authorization_residual_claim_workqueue",
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=(
            "source_identity",
            "oracle_commensurability",
            "candidate_set_completeness",
            "residual_adjudication_review",
            "mutation_boundary_proof",
        ),
        safe_default="treat_residual_claim_as_workqueue_evidence_not_replay_authority",
        forbidden_shortcuts=(
            "residual_claim_as_replay_authority",
            "oracle_score_as_source_truth",
            "candidate_overlap_as_execution_authorization",
            "target_guessing",
        ),
        detail={
            "selected_tier": tier,
            "selected_kind": kind,
            "comparison_class": str(claim.get("comparison_class") or ""),
        },
    )


def _non_authorized_compile_record(
    *,
    authorization_status: str,
    rule_id: str,
    lane: str,
    owner_phase: str,
    strict_disposition: str,
    quirks_disposition: str,
    validator_status: str,
    required_proofs: tuple[str, ...],
    safe_default: str,
    record: Mapping[str, Any],
) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=authorization_status,
        authorization_rule_id=rule_id,
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=required_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=(
            "diagnostic_as_replay_authority",
            "effect_metadata_over_promotion",
            "source_witness_over_promotion",
            "target_guessing",
            "oracle_backed_mutation",
        ),
        detail={
            "lane": lane,
            "record_rule_id": str(record.get("rule_id") or ""),
            "record_phase": str(record.get("phase") or ""),
        },
    )


def _status_token(value: str) -> str:
    token = str(value or "").strip().lower()
    normalized = "".join(ch if ch.isalnum() else "_" for ch in token)
    return "_".join(part for part in normalized.split("_") if part) or "unknown"


def _non_authorized_frontier(
    *,
    authorization_status: str,
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
        authorization_status=authorization_status,
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


def _non_authorized_claim(
    *,
    authorization_status: str,
    rule_id: str,
    owner_phase: str,
    strict_disposition: str,
    quirks_disposition: str,
    validator_status: str,
    required_proofs: tuple[str, ...],
    safe_default: str,
) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=authorization_status,
        authorization_rule_id=rule_id,
        owner_phase=owner_phase,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        required_proofs=required_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=(
            "claim_as_replay_authority",
            "oracle_backed_mutation",
            "unvalidated_manual_claim_execution",
        ),
        detail={"validator_status": validator_status},
    )

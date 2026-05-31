"""Validate proposed UK semantic-compile claims as non-executable evidence."""

from __future__ import annotations

import json
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.semantic_types import StructuralAction

if TYPE_CHECKING:
    import argparse


_CLAIM_SCHEMA = "lawvm.uk_semantic_compile_claim.v1"
_VALIDATION_SCHEMA = "lawvm.uk_semantic_compile_claim_validation.v1"
_WORKQUEUE_SCHEMA = "lawvm.uk_manual_compile_frontier.v1"
_LIVE_TARGET_INDEX_SCHEMA = "lawvm.uk_live_target_index.v1"
_ALLOWED_OUTCOME_KINDS = frozenset(
    {
        "canonical_operations",
        "non_replayable_finding",
        "source_pathology",
        "oracle_adjudication",
        "request_more_source_evidence",
    }
)
_REJECTED_STATUSES = frozenset(
    {
        "input_error",
        "rejected_schema",
        "rejected_workqueue_missing",
        "rejected_workqueue_mismatch",
        "rejected_source_text_mismatch",
        "rejected_live_state_missing",
        "rejected_live_state_mismatch",
    }
)
_ACCEPTED_STATUSES = frozenset(
    {
        "validated_provenance_only",
        "validated_provenance_and_source_text_only",
        "validated_provenance_and_live_targets_only",
        "validated_provenance_source_text_and_live_targets_only",
        "validated_provenance_live_targets_and_preconditions_only",
        "validated_provenance_source_text_live_targets_and_preconditions_only",
    }
)
_ALLOWED_OPERATION_ACTIONS = frozenset(
    {action.name for action in StructuralAction}
    | {action.value for action in StructuralAction}
)
_FORBIDDEN_WEAK_VALIDATOR_CHECK_STATUSES = frozenset(
    {
        "passed",
        "proved",
        "validated",
        "verified",
    }
)
_AUTHORIZATION_ASSERTION_VALUES = frozenset({"1", "true", "yes", "authorized"})
UK_OPERATION_FAMILY_PROOF_SEMANTICS = frozenset(
    {
        "amendment_program_target_source_payload_and_boundary",
        "appropriate_place_anchor_or_ordering_claim",
        "cross_container_renumber_source_destination_and_lineage",
        "definition_child_structural_insert_boundary_claim",
        "definition_child_structural_payload_boundary_claim",
        "definition_child_text_tail_boundary_claim",
        "definition_entry_insert_term_boundary_claim",
        "mixed_body_heading_split_boundary_claim",
        "range_to_container_source_range_payload_and_lineage",
        "referent_qualified_occurrence_scope_claim",
        "savings_qualified_omission_applicability_scope",
        "schedule_list_entry_anchor_boundary_claim",
        "source_carried_child_tail_boundary_claim",
        "source_carried_multi_subunit_boundary_claim",
        "source_carried_structured_payload_boundary_claim",
        "source_carried_structured_tail_boundary_claim",
        "structural_child_range_source_payload_boundary_claim",
        "structural_insert_source_payload_and_live_parent",
        "table_repeal_or_omission_boundary_preservation",
        "table_surface_insert_anchor_and_live_carrier",
        "text_rewrite_source_preimage_and_live_target",
        "whole_act_listed_enactments_scope_and_exclusions",
    }
)


class _WorkqueueIndex(NamedTuple):
    by_work_item_id: dict[str, Mapping[str, Any]]
    by_identity: dict[tuple[str, str, str], tuple[Mapping[str, Any], ...]]


class _WorkqueueMatch(NamedTuple):
    row: Mapping[str, Any] | None
    issues: tuple[str, ...]
    status: str


class _LiveTargetIndex(NamedTuple):
    by_statute_id: dict[str, frozenset[str]]
    fingerprints_by_statute_id: dict[str, Mapping[str, Mapping[str, Any]]]


class _ClaimIdDeclaration(NamedTuple):
    container: str
    index: int
    id_field: str
    value: str


def _read_jsonl_rows(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "line_number": line_number,
                        "validator_status": "input_error",
                        "validator_rule_id": "uk_semantic_claim_jsonl_decode_error",
                        "reason": str(exc),
                    }
                )
                continue
            if not isinstance(parsed, dict):
                rows.append(
                    {
                        "line_number": line_number,
                        "validator_status": "input_error",
                        "validator_rule_id": "uk_semantic_claim_jsonl_row_not_object",
                        "reason": "JSONL row must be an object.",
                    }
                )
                continue
            parsed.setdefault("line_number", line_number)
            rows.append(parsed)
    return tuple(rows)


def _write_jsonl_rows(path: Path, rows: tuple[Mapping[str, Any], ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


def _non_empty_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if isinstance(value, str) and value:
        return value
    return ""


def _optional_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _mapping_value(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = row.get(key)
    return value if isinstance(value, Mapping) else {}


def _sequence_value(row: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    value = row.get(key)
    if isinstance(value, list | tuple):
        return tuple(value)
    return ()


def _string_set(values: tuple[Any, ...]) -> set[str]:
    return {value for value in values if isinstance(value, str) and value}


def _string_tuple_from_value(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _claim_source_preview_sha256(row: Mapping[str, Any]) -> str:
    direct = _optional_string(row, "source_preview_sha256")
    if direct:
        return direct
    source_witness = _mapping_value(row, "source_witness")
    witness_hash = _optional_string(source_witness, "source_preview_sha256")
    if witness_hash:
        return witness_hash
    source = _mapping_value(row, "source")
    return _optional_string(source, "text_preview_sha256")


def _asserts_authorization(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _AUTHORIZATION_ASSERTION_VALUES
    return False


def _workqueue_source_preview_sha256(row: Mapping[str, Any]) -> str:
    source = _mapping_value(row, "source")
    return _optional_string(source, "text_preview_sha256")


def _claim_identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        _optional_string(row, "statute_id"),
        _optional_string(row, "effect_id"),
        _optional_string(row, "manual_compile_rule_id"),
    )


def _build_workqueue_index(rows: tuple[Mapping[str, Any], ...]) -> _WorkqueueIndex:
    by_work_item_id: dict[str, Mapping[str, Any]] = {}
    identity_lists: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if str(row.get("validator_status") or "") == "input_error":
            continue
        if _optional_string(row, "schema") != _WORKQUEUE_SCHEMA:
            continue
        work_item_id = _optional_string(row, "work_item_id")
        if work_item_id and work_item_id not in by_work_item_id:
            by_work_item_id[work_item_id] = row
        identity = _claim_identity(row)
        if all(identity):
            identity_lists.setdefault(identity, []).append(row)
    return _WorkqueueIndex(
        by_work_item_id=by_work_item_id,
        by_identity={
            identity: tuple(identity_rows)
            for identity, identity_rows in identity_lists.items()
        },
    )


def _build_live_target_index(rows: tuple[Mapping[str, Any], ...]) -> _LiveTargetIndex:
    paths_by_statute: dict[str, set[str]] = {}
    fingerprints_by_statute: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        if str(row.get("validator_status") or "") == "input_error":
            continue
        schema = _optional_string(row, "schema")
        if schema and schema != _LIVE_TARGET_INDEX_SCHEMA:
            continue
        statute_id = _optional_string(row, "statute_id")
        if not statute_id:
            continue
        target_paths = _path_strings_from_value(row.get("target_paths"))
        target_paths += _path_strings_from_value(row.get("paths"))
        paths_by_statute.setdefault(statute_id, set()).update(target_paths)
        fingerprints = row.get("target_fingerprints")
        if isinstance(fingerprints, Mapping):
            statute_fingerprints = fingerprints_by_statute.setdefault(statute_id, {})
            for path, fingerprint in fingerprints.items():
                if isinstance(path, str) and path and isinstance(fingerprint, Mapping):
                    statute_fingerprints[path] = fingerprint
    return _LiveTargetIndex(
        by_statute_id={
            statute_id: frozenset(paths)
            for statute_id, paths in paths_by_statute.items()
        },
        fingerprints_by_statute_id=fingerprints_by_statute,
    )


def _validate_claim_schema(row: Mapping[str, Any]) -> tuple[str, ...]:
    issues: list[str] = []
    if _optional_string(row, "schema") != _CLAIM_SCHEMA:
        issues.append(f"schema must be {_CLAIM_SCHEMA}")
    if _optional_string(row, "claim_kind") != "semantic_compile":
        issues.append("claim_kind must be semantic_compile")
    if _optional_string(row, "jurisdiction") != "uk":
        issues.append("jurisdiction must be uk")
    if _asserts_authorization(row.get("executable")):
        issues.append("claim.executable cannot be true in the non-executable validator")
    if _asserts_authorization(row.get("replay_authorized")):
        issues.append(
            "claim.replay_authorized cannot be true in the non-executable validator"
        )
    for key in (
        "claim_id",
        "claim_status",
        "statute_id",
        "effect_id",
        "manual_compile_rule_id",
        "action_family",
        "claimant",
    ):
        if not _non_empty_string(row, key):
            issues.append(f"{key} is required")
    source_witness = _mapping_value(row, "source_witness")
    if not source_witness:
        issues.append("source_witness is required")
    if not _claim_source_preview_sha256(row):
        issues.append("source_preview_sha256 is required")
    issues.extend(_claim_source_preview_hash_issues(row))
    proposed_outcome = _mapping_value(row, "proposed_outcome")
    if not proposed_outcome:
        issues.append("proposed_outcome is required")
        return tuple(issues)
    if _asserts_authorization(proposed_outcome.get("executable")):
        issues.append(
            "proposed_outcome.executable cannot be true in the non-executable validator"
        )
    if _asserts_authorization(proposed_outcome.get("replay_authorized")):
        issues.append(
            "proposed_outcome.replay_authorized cannot be true in the non-executable validator"
        )
    issues.extend(_validator_check_identity_issues(row))
    issues.extend(_ownership_claim_identity_issues(row))
    issues.extend(_live_target_precondition_identity_issues(row))
    outcome_kind = _optional_string(proposed_outcome, "outcome_kind")
    if outcome_kind not in _ALLOWED_OUTCOME_KINDS:
        issues.append(
            "proposed_outcome.outcome_kind must be one of "
            + ", ".join(sorted(_ALLOWED_OUTCOME_KINDS))
        )
    if outcome_kind == "canonical_operations" and not _sequence_value(
        proposed_outcome,
        "operations",
    ):
        issues.append("canonical_operations outcome requires operations")
    if outcome_kind == "canonical_operations":
        issues.extend(_validate_canonical_operation_shapes(proposed_outcome))
        issues.extend(_validate_operation_family_proof_refs(row))
    elif outcome_kind == "non_replayable_finding":
        issues.extend(_validate_non_replayable_finding_shape(proposed_outcome))
    elif outcome_kind == "source_pathology":
        issues.extend(_validate_source_pathology_shape(proposed_outcome))
    elif outcome_kind == "oracle_adjudication":
        issues.extend(_validate_oracle_adjudication_shape(proposed_outcome))
    elif outcome_kind == "request_more_source_evidence":
        issues.extend(_validate_source_evidence_request_shape(proposed_outcome))
    return tuple(issues)


def _has_target_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, Mapping):
        path = value.get("path")
        return bool(path)
    return False


def _validate_canonical_operation_shapes(
    proposed_outcome: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    operations = _sequence_value(proposed_outcome, "operations")
    seen_op_ids: set[str] = set()
    for index, operation in enumerate(operations, start=1):
        prefix = f"canonical_operations[{index}]"
        if not isinstance(operation, Mapping):
            issues.append(f"{prefix} must be an object")
            continue
        op_id = _non_empty_string(operation, "op_id")
        if not op_id:
            issues.append(f"{prefix}.op_id is required")
        elif op_id in seen_op_ids:
            issues.append(f"{prefix}.op_id duplicates earlier operation id {op_id!r}")
        else:
            seen_op_ids.add(op_id)
        action = _non_empty_string(operation, "action")
        if not action:
            issues.append(f"{prefix}.action is required")
        elif action not in _ALLOWED_OPERATION_ACTIONS:
            issues.append(f"{prefix}.action is not a canonical StructuralAction")
        if not _has_target_value(operation.get("target")):
            issues.append(f"{prefix}.target is required")
        mutation_boundary = _mapping_value(operation, "mutation_boundary")
        if not mutation_boundary:
            issues.append(f"{prefix}.mutation_boundary is required")
            continue
        if not _sequence_value(mutation_boundary, "changed_paths"):
            issues.append(f"{prefix}.mutation_boundary.changed_paths is required")
        target_region = mutation_boundary.get("target_region")
        if not _has_target_value(target_region) and not _sequence_value(
            mutation_boundary,
            "target_region",
        ):
            issues.append(f"{prefix}.mutation_boundary.target_region is required")
        issues.extend(
            _validate_mutation_boundary_containment(
                prefix=prefix,
                mutation_boundary=mutation_boundary,
            )
        )
        issues.extend(
            _validate_mutation_boundary_exception_ownership(
                prefix=prefix,
                operation=operation,
                mutation_boundary=mutation_boundary,
            )
        )
    return tuple(issues)


def _claim_canonical_operation_ids(claim: Mapping[str, Any]) -> set[str]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    operation_ids: set[str] = set()
    for operation in _sequence_value(proposed_outcome, "operations"):
        if not isinstance(operation, Mapping):
            continue
        op_id = _optional_string(operation, "op_id")
        if op_id:
            operation_ids.add(op_id)
    return operation_ids


def _claim_operation_family_proof_rows(
    claim: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    rows: list[Mapping[str, Any]] = []
    for value in (
        claim.get("operation_family_proofs"),
        proposed_outcome.get("operation_family_proofs"),
    ):
        if isinstance(value, Mapping):
            rows.append(value)
        elif isinstance(value, list | tuple):
            rows.extend(item for item in value if isinstance(item, Mapping))
    return tuple(rows)


def _claim_operation_family_proof_semantics(claim: Mapping[str, Any]) -> tuple[str, ...]:
    semantics: list[str] = []
    for proof in _claim_operation_family_proof_rows(claim):
        proof_semantic = (
            _optional_string(proof, "proof_semantic")
            or _optional_string(proof, "proof_rule_id")
        )
        if proof_semantic:
            semantics.append(proof_semantic)
    return tuple(semantics)


def _claim_operation_family_proof_families(claim: Mapping[str, Any]) -> tuple[str, ...]:
    families: list[str] = []
    for proof in _claim_operation_family_proof_rows(claim):
        proof_family = (
            _optional_string(proof, "operation_family")
            or _optional_string(proof, "action_family")
        )
        if proof_family:
            families.append(proof_family)
    return tuple(families)


def _claim_source_text_precondition_ids(claim: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in _claim_source_text_precondition_rows(claim):
        precondition_id = _optional_string(row, "precondition_id")
        if precondition_id:
            ids.add(precondition_id)
    return ids


def _claim_live_target_precondition_ids_and_paths(
    claim: Mapping[str, Any],
) -> tuple[set[str], set[str], dict[str, set[str]]]:
    ids: set[str] = set()
    paths: set[str] = set()
    paths_by_id: dict[str, set[str]] = {}
    for row in _claim_live_target_precondition_rows(claim):
        precondition_id = _optional_string(row, "precondition_id")
        if precondition_id:
            ids.add(precondition_id)
        path = _optional_string(row, "path")
        if path:
            paths.add(path)
        if precondition_id and path:
            paths_by_id.setdefault(precondition_id, set()).add(path)
    return ids, paths, paths_by_id


def _validate_operation_family_proof_refs(
    claim: Mapping[str, Any],
) -> tuple[str, ...]:
    proofs = _claim_operation_family_proof_rows(claim)
    if not proofs:
        return ()
    claim_action_family = _optional_string(claim, "action_family")
    operation_ids = _claim_canonical_operation_ids(claim)
    validator_check_ids = _claim_validator_check_ids(claim)
    source_precondition_ids = _claim_source_text_precondition_ids(claim)
    live_precondition_ids, live_precondition_paths, live_precondition_paths_by_id = (
        _claim_live_target_precondition_ids_and_paths(claim)
    )
    issues: list[str] = []
    seen_proof_ids: set[str] = set()
    for index, proof in enumerate(proofs, start=1):
        prefix = f"operation_family_proofs[{index}]"
        proof_id = _non_empty_string(proof, "proof_id")
        if not proof_id:
            issues.append(f"{prefix}.proof_id is required")
        elif proof_id in seen_proof_ids:
            issues.append(f"{prefix}.proof_id duplicates earlier proof id {proof_id!r}")
        else:
            seen_proof_ids.add(proof_id)
        proof_family = (
            _optional_string(proof, "operation_family")
            or _optional_string(proof, "action_family")
        )
        if not proof_family:
            issues.append(f"{prefix}.operation_family is required")
        elif claim_action_family and proof_family != claim_action_family:
            issues.append(
                f"{prefix}.operation_family mismatch: "
                f"proof={proof_family!r} claim={claim_action_family!r}"
            )
        status = _non_empty_string(proof, "status")
        if not status:
            issues.append(f"{prefix}.status is required")
        elif status in _FORBIDDEN_WEAK_VALIDATOR_CHECK_STATUSES:
            issues.append(
                f"{prefix}.status {status!r} cannot be claimed by this "
                "non-executable validator"
            )
        proof_operation_ids = set(_string_tuple_from_value(proof.get("operation_ids")))
        if not proof_operation_ids:
            issues.append(f"{prefix}.operation_ids is required")
        for op_id in sorted(proof_operation_ids - operation_ids):
            issues.append(f"{prefix}.operation_ids references unknown operation {op_id!r}")
        proof_check_ids = set(_string_tuple_from_value(proof.get("validator_check_ids")))
        if not proof_check_ids:
            issues.append(f"{prefix}.validator_check_ids is required")
        for check_id in sorted(proof_check_ids - validator_check_ids):
            issues.append(
                f"{prefix}.validator_check_ids references undeclared check {check_id!r}"
            )
        proof_source_ids = set(
            _string_tuple_from_value(proof.get("source_text_precondition_ids"))
        )
        for precondition_id in sorted(proof_source_ids - source_precondition_ids):
            issues.append(
                f"{prefix}.source_text_precondition_ids references unknown "
                f"precondition {precondition_id!r}"
            )
        proof_live_ids = set(
            _string_tuple_from_value(proof.get("live_target_precondition_ids"))
        )
        for precondition_id in sorted(proof_live_ids - live_precondition_ids):
            issues.append(
                f"{prefix}.live_target_precondition_ids references unknown "
                f"precondition {precondition_id!r}"
            )
        proof_live_paths = set(
            _string_tuple_from_value(proof.get("live_target_precondition_paths"))
        )
        for path in sorted(proof_live_paths - live_precondition_paths):
            issues.append(
                f"{prefix}.live_target_precondition_paths references unknown "
                f"path {path!r}"
            )
        if not proof_source_ids and not proof_live_ids and not proof_live_paths:
            issues.append(
                f"{prefix} must reference source_text_precondition_ids, "
                "live_target_precondition_ids, or live_target_precondition_paths"
            )
        issues.extend(
            _validate_operation_family_proof_semantic(
                claim=claim,
                proof=proof,
                prefix=prefix,
                proof_family=proof_family,
                proof_operation_ids=proof_operation_ids,
                proof_source_ids=proof_source_ids,
                proof_live_ids=proof_live_ids,
                proof_live_paths=proof_live_paths,
                live_precondition_paths=live_precondition_paths,
                live_precondition_paths_by_id=live_precondition_paths_by_id,
            )
        )
    return tuple(issues)


def _operations_by_id(claim: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    operations: dict[str, Mapping[str, Any]] = {}
    for operation in _sequence_value(proposed_outcome, "operations"):
        if not isinstance(operation, Mapping):
            continue
        op_id = _optional_string(operation, "op_id")
        if op_id and op_id not in operations:
            operations[op_id] = operation
    return operations


def _validate_operation_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    proof_semantic = (
        _optional_string(proof, "proof_semantic")
        or _optional_string(proof, "proof_rule_id")
    )
    if not proof_semantic:
        return ()
    if proof_semantic not in UK_OPERATION_FAMILY_PROOF_SEMANTICS:
        return (f"{prefix}.proof_semantic {proof_semantic!r} is not supported",)
    if proof_semantic == "table_surface_insert_anchor_and_live_carrier":
        return _validate_table_insert_family_proof_semantic(
            claim=claim,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "text_rewrite_source_preimage_and_live_target":
        return _validate_text_rewrite_family_proof_semantic(
            claim=claim,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "structural_insert_source_payload_and_live_parent":
        return _validate_structural_insert_family_proof_semantic(
            claim=claim,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "schedule_list_entry_anchor_boundary_claim":
        return _validate_schedule_list_entry_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "definition_entry_insert_term_boundary_claim":
        return _validate_definition_entry_insert_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "savings_qualified_omission_applicability_scope":
        return _validate_savings_qualified_omission_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "whole_act_listed_enactments_scope_and_exclusions":
        return _validate_whole_act_listed_enactments_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "appropriate_place_anchor_or_ordering_claim":
        return _validate_appropriate_place_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "range_to_container_source_range_payload_and_lineage":
        return _validate_range_to_container_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "table_repeal_or_omission_boundary_preservation":
        return _validate_table_repeal_or_omission_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "cross_container_renumber_source_destination_and_lineage":
        return _validate_cross_container_renumber_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            live_precondition_paths=live_precondition_paths,
        )
    if proof_semantic == "amendment_program_target_source_payload_and_boundary":
        return _validate_amendment_program_target_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "definition_child_text_tail_boundary_claim":
        return _validate_definition_child_text_tail_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "definition_child_structural_payload_boundary_claim":
        return _validate_definition_child_structural_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "definition_child_structural_insert_boundary_claim":
        return _validate_definition_child_structural_insert_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "referent_qualified_occurrence_scope_claim":
        return _validate_referent_qualified_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "mixed_body_heading_split_boundary_claim":
        return _validate_mixed_body_heading_split_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "structural_child_range_source_payload_boundary_claim":
        return _validate_structural_child_range_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "source_carried_multi_subunit_boundary_claim":
        return _validate_source_carried_multi_subunit_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "source_carried_child_tail_boundary_claim":
        return _validate_source_carried_child_tail_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "source_carried_structured_payload_boundary_claim":
        return _validate_source_carried_structured_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    if proof_semantic == "source_carried_structured_tail_boundary_claim":
        return _validate_source_carried_structured_tail_family_proof_semantic(
            claim=claim,
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_family=proof_family,
            proof_operation_ids=proof_operation_ids,
            proof_source_ids=proof_source_ids,
            proof_live_ids=proof_live_ids,
            proof_live_paths=proof_live_paths,
            live_precondition_paths=live_precondition_paths,
            live_precondition_paths_by_id=live_precondition_paths_by_id,
        )
    return (f"{prefix}.proof_semantic {proof_semantic!r} is not supported",)


def _live_carrier_paths_for_proof(
    *,
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> set[str]:
    live_carrier_paths = set(proof_live_paths)
    for precondition_id in sorted(proof_live_ids):
        live_carrier_paths.update(
            live_precondition_paths_by_id.get(precondition_id, set())
        )
    return live_carrier_paths


def _validate_table_insert_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "table_surface_mutation":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'table_surface_mutation'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    live_carrier_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_carrier_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an INSERT"
            )
        targets = _path_strings_from_value(operation.get("target"))
        if not targets:
            continue
        for target in targets:
            parent = _parent_path_string(target)
            if live_carrier_paths and not _path_within_any_region(
                parent,
                tuple(live_carrier_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target parent "
                    f"{parent!r} is outside declared live carrier preconditions"
                )
    return tuple(issues)


def _validate_text_rewrite_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family not in {
        "facet_text_rewrite",
        "crossheading_text_rewrite",
        "table_crossheading_text_rewrite",
        "schedule_note_text_rewrite",
        "referent_qualified_text_substitution",
        "source_carried_child_tail_text_rewrite",
        "source_carried_multi_subunit_text_rewrite",
        "source_carried_structured_text_patch",
        "whole_act_listed_enactments_text_patch",
        "savings_qualified_text_omission",
    }:
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} does not support "
            f"operation_family {proof_family!r}"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    live_carrier_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_carrier_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    allowed_actions = {
        "TEXT_REPLACE",
        "text_replace",
        "TEXT_REPEAL",
        "text_repeal",
        "HEADING_REPLACE",
        "heading_replace",
    }
    required_surface_roles_by_family = {
        "facet_text_rewrite": {
            "heading_facet",
            "title_facet",
            "sidenote_facet",
        },
        "crossheading_text_rewrite": {"crossheading"},
        "table_crossheading_text_rewrite": {"table_crossheading"},
        "schedule_note_text_rewrite": {"schedule_note"},
    }
    required_surface_roles = required_surface_roles_by_family.get(proof_family)
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in allowed_actions:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a text "
                "or heading rewrite action"
            )
        if required_surface_roles is not None:
            surface_role = _optional_string(operation, "surface_role")
            if surface_role not in required_surface_roles:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                    "surface_role "
                    + " or ".join(repr(role) for role in sorted(required_surface_roles))
                )
        for target in _path_strings_from_value(operation.get("target")):
            if live_carrier_paths and target not in live_carrier_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live target preconditions"
                )
    return tuple(issues)


def _validate_structural_insert_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family not in {
        "structural_sibling_insert",
        "definition_entry_insert",
        "index_entry_insert",
        "schedule_part_wrapper_insertion",
    }:
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} does not support "
            f"operation_family {proof_family!r}"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    live_parent_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_parent_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an INSERT"
            )
        for target in _path_strings_from_value(operation.get("target")):
            parent = _parent_path_string(target)
            if live_parent_paths and not _path_within_any_region(
                parent,
                tuple(live_parent_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target parent "
                    f"{parent!r} is outside declared live parent preconditions"
                )
    return tuple(issues)


def _validate_schedule_list_entry_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "schedule_list_entry_mutation":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'schedule_list_entry_mutation'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "entry_anchor_precondition_ids",
                "entry_payload_precondition_ids",
            ),
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("entry_ownership_ids")))
    for required_id in (
        "source_named_entry_anchor",
        "entry_carrier",
        "sibling_insertion_or_replacement_boundary",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires entry_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.entry_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_entry_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_entry_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert", "REPLACE", "replace"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an "
                "entry insert or replacement action"
            )
        if not _has_any_non_empty_string(
            operation,
            ("entry_anchor", "entry_anchor_id", "insertion_position"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "an entry anchor or insertion position"
            )
        if not _has_any_non_empty_string(
            operation,
            ("schedule_entry_label", "entry_text", "inserted_entry_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a schedule entry label or text"
            )
        _append_target_scope_issues(
            issues=issues,
            operation=operation,
            proof_semantic=proof_semantic,
            prefix=prefix,
            op_id=op_id,
            live_paths=live_entry_paths,
            region_name="live schedule-entry preconditions",
            insert_uses_parent=True,
        )
    return tuple(issues)


def _validate_definition_entry_insert_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "definition_entry_insert":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'definition_entry_insert'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "definition_term_precondition_ids",
                "definition_entry_payload_precondition_ids",
            ),
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("definition_ownership_ids")))
    for required_id in (
        "inserted_definition_term_identity",
        "complete_definition_entry_payload",
        "definition_list_target_boundary",
        "insertion_position_or_list_end_boundary",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires definition_ownership_ids "
                f"to include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.definition_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_definition_list_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_definition_list_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an INSERT"
            )
        if not _has_any_non_empty_string(
            operation,
            ("inserted_definition_term", "definition_term"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "an inserted definition term"
            )
        if not _has_any_non_empty_string(
            operation,
            ("definition_entry_payload_id", "definition_entry_text"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a definition entry payload"
            )
        if not _has_any_non_empty_string(
            operation,
            ("definition_entry_anchor", "insertion_position", "list_end_boundary"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "an insertion anchor, position, or list-end boundary"
            )
        _append_target_scope_issues(
            issues=issues,
            operation=operation,
            proof_semantic=proof_semantic,
            prefix=prefix,
            op_id=op_id,
            live_paths=live_definition_list_paths,
            region_name="live definition-list preconditions",
            insert_uses_parent=True,
        )
    return tuple(issues)


def _validate_savings_qualified_omission_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "savings_qualified_text_omission":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'savings_qualified_text_omission'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    omitted_reference_ids = set(
        _string_tuple_from_value(proof.get("omitted_reference_precondition_ids"))
    )
    if not omitted_reference_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires omitted_reference_precondition_ids"
        )
    for precondition_id in sorted(omitted_reference_ids - proof_source_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.omitted_reference_precondition_ids "
            f"references source precondition {precondition_id!r} not listed in "
            "source_text_precondition_ids"
        )
    savings_condition_ids = set(
        _string_tuple_from_value(proof.get("savings_condition_precondition_ids"))
    ) | set(
        _string_tuple_from_value(proof.get("applicability_scope_precondition_ids"))
    )
    if not savings_condition_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "savings_condition_precondition_ids or "
            "applicability_scope_precondition_ids"
        )
    for precondition_id in sorted(savings_condition_ids - proof_source_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.savings_condition_precondition_ids "
            f"references source precondition {precondition_id!r} not listed in "
            "source_text_precondition_ids"
        )
    live_carrier_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_carrier_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    allowed_actions = {
        "TEXT_REPEAL",
        "text_repeal",
        "TEXT_REPLACE",
        "text_replace",
    }
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in allowed_actions:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "text omission action"
            )
        if not _has_savings_scope_qualification(operation):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "applicability_scope or savings_condition"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_carrier_paths and target not in live_carrier_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live target preconditions"
                )
    return tuple(issues)


def _has_savings_scope_qualification(operation: Mapping[str, Any]) -> bool:
    for key in (
        "applicability_scope",
        "savings_condition",
        "scope_qualifier",
        "temporal_or_applicability_scope",
    ):
        value = operation.get(key)
        if isinstance(value, Mapping) and value:
            return True
        if isinstance(value, str) and value:
            return True
    return False


def _validate_whole_act_listed_enactments_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "whole_act_listed_enactments_text_patch":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'whole_act_listed_enactments_text_patch'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    list_membership_ids = set(
        _string_tuple_from_value(proof.get("list_membership_precondition_ids"))
    )
    if not list_membership_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires list_membership_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.list_membership_precondition_ids",
            ids=list_membership_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    quoted_preimage_ids = set(
        _string_tuple_from_value(proof.get("quoted_preimage_precondition_ids"))
    )
    if not quoted_preimage_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires quoted_preimage_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.quoted_preimage_precondition_ids",
            ids=quoted_preimage_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    exclusion_ownership_ids = set(
        _string_tuple_from_value(proof.get("exclusion_ownership_ids"))
    )
    if "same_schedule_and_same_act_exclusions" not in exclusion_ownership_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires exclusion_ownership_ids to "
            "include 'same_schedule_and_same_act_exclusions'"
        )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(exclusion_ownership_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.exclusion_ownership_ids references "
            f"undeclared ownership {ownership_id!r}"
        )
    excluded_surface_families = set(
        _string_tuple_from_value(proof.get("excluded_surface_families"))
    )
    if "title_or_short_title" not in excluded_surface_families:
        issues.append(
            f"{prefix}.{proof_semantic} requires excluded_surface_families to "
            "include 'title_or_short_title'"
        )
    live_carrier_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_carrier_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    allowed_actions = {
        "TEXT_REPLACE",
        "text_replace",
        "TEXT_REPEAL",
        "text_repeal",
    }
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in allowed_actions:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "whole-Act text patch action"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if _is_title_or_short_title_path(target):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is an excluded title or short-title surface"
                )
            if live_carrier_paths and target not in live_carrier_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live text carriers"
                )
    return tuple(issues)


def _source_precondition_subset_issues(
    *,
    prefix: str,
    ids: set[str],
    proof_source_ids: set[str],
) -> tuple[str, ...]:
    return tuple(
        f"{prefix} references source precondition {precondition_id!r} not "
        "listed in source_text_precondition_ids"
        for precondition_id in sorted(ids - proof_source_ids)
    )


def _is_title_or_short_title_path(path: str) -> bool:
    for component in path.lower().split("/"):
        kind = component.split(":", 1)[0]
        if kind in {"title", "long_title", "short_title"}:
            return True
        if component in {"facet:title", "facet:long_title", "facet:short_title"}:
            return True
    return False


def _validate_appropriate_place_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family not in {
        "appropriate_place_mutation",
        "definition_entry_insert",
        "index_entry_insert",
    }:
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} does not support "
            f"operation_family {proof_family!r}"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    payload_ids = set(_string_tuple_from_value(proof.get("payload_precondition_ids")))
    if not payload_ids:
        issues.append(f"{prefix}.{proof_semantic} requires payload_precondition_ids")
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.payload_precondition_ids",
            ids=payload_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    anchor_ownership_ids = set(
        _string_tuple_from_value(proof.get("anchor_or_ordering_ownership_ids"))
    )
    if "validated_predecessor_or_successor_anchor" not in anchor_ownership_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "anchor_or_ordering_ownership_ids to include "
            "'validated_predecessor_or_successor_anchor'"
        )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(anchor_ownership_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.anchor_or_ordering_ownership_ids "
            f"references undeclared ownership {ownership_id!r}"
        )
    anchor_live_ids = set(
        _string_tuple_from_value(proof.get("anchor_live_target_precondition_ids"))
    )
    for precondition_id in sorted(anchor_live_ids - proof_live_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.anchor_live_target_precondition_ids "
            f"references live precondition {precondition_id!r} not listed in "
            "live_target_precondition_ids"
        )
    anchor_live_paths = set(
        _string_tuple_from_value(proof.get("anchor_live_target_precondition_paths"))
    )
    for path in sorted(anchor_live_paths - live_precondition_paths):
        issues.append(
            f"{prefix}.{proof_semantic}.anchor_live_target_precondition_paths "
            f"references undeclared live path {path!r}"
        )
    ordering_rule = _optional_string(proof, "ordering_rule_id")
    proof_check_ids = set(_string_tuple_from_value(proof.get("validator_check_ids")))
    if ordering_rule and ordering_rule not in proof_check_ids:
        issues.append(
            f"{prefix}.{proof_semantic}.ordering_rule_id {ordering_rule!r} "
            "must be listed in validator_check_ids"
        )
    if not anchor_live_ids and not anchor_live_paths and not ordering_rule:
        issues.append(
            f"{prefix}.{proof_semantic} requires anchor_live_target_precondition_ids, "
            "anchor_live_target_precondition_paths, or ordering_rule_id"
        )
    live_parent_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_parent_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an INSERT"
            )
        for target in _path_strings_from_value(operation.get("target")):
            parent = _parent_path_string(target)
            if live_parent_paths and not _path_within_any_region(
                parent,
                tuple(live_parent_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target parent "
                    f"{parent!r} is outside declared live parent preconditions"
                )
    return tuple(issues)


def _validate_range_to_container_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "range_to_container_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'range_to_container_substitution'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    source_range_ids = set(
        _string_tuple_from_value(proof.get("source_range_precondition_ids"))
    )
    if not source_range_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires source_range_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.source_range_precondition_ids",
            ids=source_range_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    payload_ids = set(
        _string_tuple_from_value(proof.get("container_payload_precondition_ids"))
    ) | set(_string_tuple_from_value(proof.get("payload_precondition_ids")))
    if not payload_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "container_payload_precondition_ids or payload_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.container_payload_precondition_ids",
            ids=payload_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    migration_ownership_ids = set(
        _string_tuple_from_value(proof.get("migration_ownership_ids"))
    )
    if "lineage_or_migration_events" not in migration_ownership_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires migration_ownership_ids to "
            "include 'lineage_or_migration_events'"
        )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(migration_ownership_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.migration_ownership_ids references "
            f"undeclared ownership {ownership_id!r}"
        )
    live_container_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_container_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"REPLACE", "replace"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a REPLACE"
            )
        mutation_boundary = _mapping_value(operation, "mutation_boundary")
        if not _path_strings_from_value(
            mutation_boundary.get("declared_migration_paths")
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "migration paths"
            )
        if not _has_lineage_or_migration_witness(operation, mutation_boundary):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
                "lineage or migration event id"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_container_paths and not _path_within_any_region(
                target,
                tuple(live_container_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live container preconditions"
                )
    return tuple(issues)


def _has_lineage_or_migration_witness(
    operation: Mapping[str, Any],
    mutation_boundary: Mapping[str, Any],
) -> bool:
    return _has_any_non_empty_string(
        operation,
        ("migration_event_id", "lineage_event_id"),
    ) or _has_any_non_empty_string(
        mutation_boundary,
        ("migration_event_id", "lineage_event_id"),
    )


def _validate_table_repeal_or_omission_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "table_repeal_or_omission":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'table_repeal_or_omission'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    table_surface_ids = set(
        _string_tuple_from_value(proof.get("table_surface_precondition_ids"))
    )
    if not table_surface_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires table_surface_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.table_surface_precondition_ids",
            ids=table_surface_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    repealed_boundary_ids = set(
        _string_tuple_from_value(proof.get("repealed_boundary_ownership_ids"))
    )
    for required_id in (
        "repealed_row_column_or_cell_boundary",
        "unclaimed_table_surface_preservation",
    ):
        if required_id not in repealed_boundary_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires "
                f"repealed_boundary_ownership_ids to include {required_id!r}"
            )
    if proof.get("requires_structural_text_split") is True and (
        "structural_and_text_repeal_split_boundary" not in repealed_boundary_ids
    ):
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "repealed_boundary_ownership_ids to include "
            "'structural_and_text_repeal_split_boundary'"
        )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(repealed_boundary_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.repealed_boundary_ownership_ids "
            f"references undeclared ownership {ownership_id!r}"
        )
    live_table_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_table_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    allowed_actions = {
        "REPEAL",
        "repeal",
        "TEXT_REPEAL",
        "text_repeal",
    }
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in allowed_actions:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "table repeal or text omission action"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_table_paths and not _path_within_any_region(
                target,
                tuple(live_table_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live table preconditions"
                )
    return tuple(issues)


def _validate_cross_container_renumber_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    live_precondition_paths: set[str],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "cross_container_renumber_migration":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'cross_container_renumber_migration'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    source_target_ids = set(
        _string_tuple_from_value(proof.get("source_target_precondition_ids"))
    )
    if not source_target_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires source_target_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.source_target_precondition_ids",
            ids=source_target_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    destination_target_ids = set(
        _string_tuple_from_value(proof.get("destination_target_precondition_ids"))
    )
    if not destination_target_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "destination_target_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.destination_target_precondition_ids",
            ids=destination_target_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    migration_ownership_ids = set(
        _string_tuple_from_value(proof.get("migration_ownership_ids"))
    )
    for required_id in (
        "lineage_or_migration_events",
        "cross_container_destination_boundary",
    ):
        if required_id not in migration_ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires migration_ownership_ids to "
                f"include {required_id!r}"
            )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(migration_ownership_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.migration_ownership_ids references "
            f"undeclared ownership {ownership_id!r}"
        )
    source_live_paths = set(
        _string_tuple_from_value(proof.get("source_live_target_precondition_paths"))
    )
    destination_live_paths = set(
        _string_tuple_from_value(
            proof.get("destination_live_target_precondition_paths")
        )
    )
    issues.extend(
        _live_path_subset_issues(
            prefix=(
                f"{prefix}.{proof_semantic}."
                "source_live_target_precondition_paths"
            ),
            paths=source_live_paths,
            live_precondition_paths=live_precondition_paths,
        )
    )
    issues.extend(
        _live_path_subset_issues(
            prefix=(
                f"{prefix}.{proof_semantic}."
                "destination_live_target_precondition_paths"
            ),
            paths=destination_live_paths,
            live_precondition_paths=live_precondition_paths,
        )
    )
    if not source_live_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "source_live_target_precondition_paths"
        )
    if not destination_live_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "destination_live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"RENUMBER", "renumber"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a RENUMBER"
            )
        mutation_boundary = _mapping_value(operation, "mutation_boundary")
        if not _path_strings_from_value(
            mutation_boundary.get("declared_migration_paths")
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "migration paths"
            )
        if not _has_lineage_or_migration_witness(operation, mutation_boundary):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
                "lineage or migration event id"
            )
        destination_paths = _path_strings_from_value(operation.get("destination"))
        if not destination_paths:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
                "destination"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if source_live_paths and not _path_within_any_region(
                target,
                tuple(source_live_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared source live preconditions"
                )
        for destination in destination_paths:
            destination_parent = _parent_path_string(destination)
            if destination_live_paths and not _path_within_any_region(
                destination_parent or destination,
                tuple(destination_live_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} destination "
                    f"{destination!r} is outside declared destination live "
                    "preconditions"
                )
    return tuple(issues)


def _live_path_subset_issues(
    *,
    prefix: str,
    paths: set[str],
    live_precondition_paths: set[str],
) -> tuple[str, ...]:
    return tuple(
        f"{prefix} references unknown live precondition path {path!r}"
        for path in sorted(paths - live_precondition_paths)
    )


def _validate_amendment_program_target_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "amendment_program_target_mutation":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'amendment_program_target_mutation'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    source_target_ids = set(
        _string_tuple_from_value(proof.get("source_target_precondition_ids"))
    )
    if not source_target_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires source_target_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.source_target_precondition_ids",
            ids=source_target_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    payload_ids = set(
        _string_tuple_from_value(proof.get("inserted_payload_precondition_ids"))
    ) | set(_string_tuple_from_value(proof.get("payload_precondition_ids")))
    if not payload_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires "
            "inserted_payload_precondition_ids or payload_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.inserted_payload_precondition_ids",
            ids=payload_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    boundary_ownership_ids = set(
        _string_tuple_from_value(proof.get("boundary_ownership_ids"))
    )
    for required_id in (
        "amendment_program_target_boundary",
        "payload_ownership",
    ):
        if required_id not in boundary_ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    declared_ownership_ids = _claim_ownership_ids(claim)
    for ownership_id in sorted(boundary_ownership_ids - declared_ownership_ids):
        issues.append(
            f"{prefix}.{proof_semantic}.boundary_ownership_ids references "
            f"undeclared ownership {ownership_id!r}"
        )
    live_program_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_program_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    allowed_actions = {
        "INSERT",
        "insert",
        "REPLACE",
        "replace",
        "TEXT_REPLACE",
        "text_replace",
    }
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in allowed_actions:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an "
                "amendment-program insert or replacement action"
            )
        mutation_boundary = _mapping_value(operation, "mutation_boundary")
        if not _has_any_non_empty_string(
            operation,
            ("amendment_program_target_id", "amendment_program_source_target"),
        ) and not _has_any_non_empty_string(
            mutation_boundary,
            ("amendment_program_target_id", "amendment_program_source_target"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare an "
                "amendment program target id or source target"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_program_paths and not _path_within_any_region(
                _parent_path_string(target) if action in {"INSERT", "insert"} else target,
                tuple(live_program_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live amendment-program "
                    "preconditions"
                )
    return tuple(issues)


def _validate_definition_child_text_tail_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "definition_child_and_tail_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'definition_child_and_tail_substitution'"
        )
    issues.extend(
        _definition_child_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            payload_key="replacement_payload_precondition_ids",
        )
    )
    tail_connector_ids = set(
        _string_tuple_from_value(proof.get("tail_connector_precondition_ids"))
    )
    if not tail_connector_ids:
        issues.append(
            f"{prefix}.{proof_semantic} requires tail_connector_precondition_ids"
        )
    issues.extend(
        _source_precondition_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.tail_connector_precondition_ids",
            ids=tail_connector_ids,
            proof_source_ids=proof_source_ids,
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "definition_child_text_boundary",
        "post_child_tail_connector_boundary",
        "replacement_payload",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_definition_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_definition_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"TEXT_REPLACE", "text_replace"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded definition-child text replacement action"
            )
        issues.extend(
            _definition_child_operation_scope_issues(
                operation=operation,
                proof_semantic=proof_semantic,
                prefix=prefix,
                op_id=op_id,
                live_definition_paths=live_definition_paths,
            )
        )
    return tuple(issues)


def _validate_definition_child_structural_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "definition_child_structural_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'definition_child_structural_substitution'"
        )
    issues.extend(
        _definition_child_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            payload_key="replacement_child_payload_precondition_ids",
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "definition_term_scope",
        "definition_child_identity",
        "replacement_child_payload_shape",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    if proof.get("includes_tail_connector") is True and (
        "post_child_tail_connector_boundary" not in ownership_ids
    ):
        issues.append(
            f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
            "include 'post_child_tail_connector_boundary'"
        )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_definition_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_definition_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"REPLACE", "replace"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded definition-child structural replacement action"
            )
        issues.extend(
            _definition_child_operation_scope_issues(
                operation=operation,
                proof_semantic=proof_semantic,
                prefix=prefix,
                op_id=op_id,
                live_definition_paths=live_definition_paths,
            )
        )
    return tuple(issues)


def _validate_definition_child_structural_insert_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "definition_child_structural_insert":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'definition_child_structural_insert'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "definition_term_precondition_ids",
                "anchor_child_precondition_ids",
                "inserted_payload_precondition_ids",
                "tail_connector_precondition_ids",
            ),
        )
    )
    boundary_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "definition_term_scope",
        "anchor_definition_child_identity",
        "inserted_child_payload_shape",
        "existing_tail_connector_boundary",
        "connector_migration_or_preservation_rule",
    ):
        if required_id not in boundary_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=boundary_ids,
            claim=claim,
        )
    )
    live_definition_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_definition_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"INSERT", "insert"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be an INSERT"
            )
        mutation_boundary = _mapping_value(operation, "mutation_boundary")
        if not _has_any_non_empty_string(
            operation,
            ("definition_term", "definition_term_id"),
        ) and not _has_any_non_empty_string(
            mutation_boundary,
            ("definition_term", "definition_term_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
                "definition term"
            )
        if not _has_any_non_empty_string(
            operation,
            ("anchor_definition_child_label", "anchor_child_label"),
        ) and not _has_any_non_empty_string(
            mutation_boundary,
            ("anchor_definition_child_label", "anchor_child_label"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare an "
                "anchor definition child label"
            )
        if not _has_any_non_empty_string(
            operation,
            ("inserted_definition_child_label", "inserted_child_label"),
        ) and not _has_any_non_empty_string(
            mutation_boundary,
            ("inserted_definition_child_label", "inserted_child_label"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare an "
                "inserted definition child label"
            )
        if not _has_any_non_empty_string(
            operation,
            ("tail_connector_handling", "connector_rule_id"),
        ) and not _has_any_non_empty_string(
            mutation_boundary,
            ("tail_connector_handling", "connector_rule_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "tail connector handling"
            )
        for target in _path_strings_from_value(operation.get("target")):
            parent = _parent_path_string(target)
            if live_definition_paths and not _path_within_any_region(
                parent,
                tuple(live_definition_paths),
            ):
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target parent "
                    f"{parent!r} is outside declared live definition preconditions"
                )
    return tuple(issues)


def _definition_child_source_precondition_issues(
    *,
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_source_ids: set[str],
    payload_key: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    for key in (
        "definition_term_precondition_ids",
        "definition_child_precondition_ids",
        payload_key,
    ):
        ids = set(_string_tuple_from_value(proof.get(key)))
        if not ids:
            issues.append(f"{prefix}.{proof_semantic} requires {key}")
        issues.extend(
            _source_precondition_subset_issues(
                prefix=f"{prefix}.{proof_semantic}.{key}",
                ids=ids,
                proof_source_ids=proof_source_ids,
            )
        )
    return tuple(issues)


def _declared_ownership_subset_issues(
    *,
    prefix: str,
    ids: set[str],
    claim: Mapping[str, Any],
) -> tuple[str, ...]:
    declared_ownership_ids = _claim_ownership_ids(claim)
    return tuple(
        f"{prefix} references undeclared ownership {ownership_id!r}"
        for ownership_id in sorted(ids - declared_ownership_ids)
    )


def _validate_mixed_body_heading_split_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "mixed_body_heading_text_substitution_split":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'mixed_body_heading_text_substitution_split'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "body_target_precondition_ids",
                "heading_facet_precondition_ids",
                "per_surface_preimage_precondition_ids",
                "replacement_precondition_ids",
            ),
        )
    )
    split_ids = set(_string_tuple_from_value(proof.get("split_ownership_ids")))
    for required_id in (
        "body_text_target_boundary",
        "heading_facet_boundary",
        "split_operation_boundary",
        "unclaimed_surface_preservation",
    ):
        if required_id not in split_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires split_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.split_ownership_ids",
            ids=split_ids,
            claim=claim,
        )
    )
    live_surface_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_surface_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    if len(proof_operation_ids) < 2:
        issues.append(
            f"{prefix}.{proof_semantic} requires at least two split operations"
        )
    seen_roles: set[str] = set()
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "TEXT_REPLACE",
            "text_replace",
            "TEXT_REPEAL",
            "text_repeal",
            "HEADING_REPLACE",
            "heading_replace",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "body text or heading rewrite action"
            )
        surface_role = _optional_string(operation, "surface_role")
        if surface_role not in {"body_text", "heading_facet"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "surface_role 'body_text' or 'heading_facet'"
            )
        else:
            seen_roles.add(surface_role)
        for target in _path_strings_from_value(operation.get("target")):
            if live_surface_paths and target not in live_surface_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live split-surface "
                    "preconditions"
                )
    for required_role in ("body_text", "heading_facet"):
        if required_role not in seen_roles:
            issues.append(
                f"{prefix}.{proof_semantic} requires a {required_role!r} operation"
            )
    return tuple(issues)


def _validate_structural_child_range_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "structural_child_range_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'structural_child_range_substitution'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "child_range_precondition_ids",
                "removed_child_precondition_ids",
                "replacement_payload_precondition_ids",
            ),
        )
    )
    boundary_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "source_named_child_range",
        "replacement_payload_shape",
        "removed_child_identities",
        "parent_text_or_tail_boundary",
    ):
        if required_id not in boundary_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=boundary_ids,
            claim=claim,
        )
    )
    live_range_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_range_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "REPLACE",
            "replace",
            "INSERT",
            "insert",
            "REPEAL",
            "repeal",
            "TEXT_REPLACE",
            "text_replace",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded child-range substitution action"
            )
        if not _has_any_non_empty_string(
            operation,
            ("child_range_id", "source_child_range"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a source child range"
            )
        if not _string_tuple_from_value(operation.get("removed_child_ids")):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "removed_child_ids"
            )
        if action in {
            "REPLACE",
            "replace",
            "INSERT",
            "insert",
            "TEXT_REPLACE",
            "text_replace",
        } and not _has_any_non_empty_string(
            operation,
            ("replacement_payload_shape", "replacement_payload_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "replacement payload shape"
            )
        _append_target_scope_issues(
            issues=issues,
            operation=operation,
            proof_semantic=proof_semantic,
            prefix=prefix,
            op_id=op_id,
            live_paths=live_range_paths,
            region_name="live child-range preconditions",
            insert_uses_parent=True,
        )
    return tuple(issues)


def _definition_child_operation_scope_issues(
    *,
    operation: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    op_id: str,
    live_definition_paths: set[str],
) -> tuple[str, ...]:
    issues: list[str] = []
    mutation_boundary = _mapping_value(operation, "mutation_boundary")
    if not _has_any_non_empty_string(
        operation,
        ("definition_term", "definition_term_id"),
    ) and not _has_any_non_empty_string(
        mutation_boundary,
        ("definition_term", "definition_term_id"),
    ):
        issues.append(
            f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
            "definition term"
        )
    if not _has_any_non_empty_string(
        operation,
        ("definition_child_label", "definition_child_id"),
    ) and not _has_any_non_empty_string(
        mutation_boundary,
        ("definition_child_label", "definition_child_id"),
    ):
        issues.append(
            f"{prefix}.{proof_semantic} operation {op_id!r} must declare a "
            "definition child label"
        )
    for target in _path_strings_from_value(operation.get("target")):
        if live_definition_paths and not _path_within_any_region(
            target,
            tuple(live_definition_paths),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} target "
                f"{target!r} is outside declared live definition preconditions"
            )
    return tuple(issues)


def _validate_referent_qualified_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "referent_qualified_text_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'referent_qualified_text_substitution'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    for key in (
        "referent_entity_precondition_ids",
        "quoted_preimage_precondition_ids",
        "replacement_precondition_ids",
    ):
        ids = set(_string_tuple_from_value(proof.get(key)))
        if not ids:
            issues.append(f"{prefix}.{proof_semantic} requires {key}")
        issues.extend(
            _source_precondition_subset_issues(
                prefix=f"{prefix}.{proof_semantic}.{key}",
                ids=ids,
                proof_source_ids=proof_source_ids,
            )
        )
    ownership_ids = set(_string_tuple_from_value(proof.get("referent_ownership_ids")))
    for required_id in (
        "source_qualified_referent_entity",
        "per_occurrence_coreference_decision",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires referent_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.referent_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_text_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_text_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {"TEXT_REPLACE", "text_replace"}:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "referent-qualified text replacement action"
            )
        if not _has_any_non_empty_string(
            operation,
            ("referent_entity", "referent_scope", "coreference_rule_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a referent entity, scope, or coreference rule"
            )
        if not _string_tuple_from_value(operation.get("occurrence_ids")):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "occurrence_ids"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_text_paths and target not in live_text_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live text preconditions"
                )
    return tuple(issues)


def _validate_source_carried_multi_subunit_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "source_carried_multi_subunit_text_rewrite":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'source_carried_multi_subunit_text_rewrite'"
        )
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    for key in (
        "child_unit_precondition_ids",
        "per_child_preimage_precondition_ids",
        "replacement_or_repeal_payload_precondition_ids",
    ):
        ids = set(_string_tuple_from_value(proof.get(key)))
        if not ids:
            issues.append(f"{prefix}.{proof_semantic} requires {key}")
        issues.extend(
            _source_precondition_subset_issues(
                prefix=f"{prefix}.{proof_semantic}.{key}",
                ids=ids,
                proof_source_ids=proof_source_ids,
            )
        )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "source_named_child_unit_set",
        "per_child_text_preimage",
        "per_child_replacement_or_repeal_payload",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_child_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_child_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "TEXT_REPLACE",
            "text_replace",
            "TEXT_REPEAL",
            "text_repeal",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded child-unit text rewrite action"
            )
        if not _has_any_non_empty_string(
            operation,
            ("child_unit_id", "child_unit_label", "source_child_unit"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a child unit id or label"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_child_paths and target not in live_child_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live child-unit preconditions"
                )
    return tuple(issues)


def _validate_source_carried_child_tail_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "source_carried_child_tail_text_rewrite":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'source_carried_child_tail_text_rewrite'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "child_anchor_precondition_ids",
                "tail_scope_precondition_ids",
                "replacement_or_repeal_payload_precondition_ids",
            ),
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "source_named_child_anchor",
        "tail_text_preimage_or_repeal_scope",
        "replacement_or_repeal_payload",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_tail_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_tail_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "TEXT_REPLACE",
            "text_replace",
            "TEXT_REPEAL",
            "text_repeal",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded child-tail text rewrite action"
            )
        if not _has_any_non_empty_string(
            operation,
            ("child_anchor", "child_unit_id", "child_unit_label"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a child anchor or child unit id"
            )
        if not _has_any_non_empty_string(
            operation,
            ("tail_scope_id", "tail_boundary", "tail_preimage_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a tail scope or boundary"
            )
        for target in _path_strings_from_value(operation.get("target")):
            if live_tail_paths and target not in live_tail_paths:
                issues.append(
                    f"{prefix}.{proof_semantic} operation {op_id!r} target "
                    f"{target!r} is outside declared live child-tail preconditions"
                )
    return tuple(issues)


def _validate_source_carried_structured_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "source_carried_structured_text_patch":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'source_carried_structured_text_patch'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "parent_formula_anchor_precondition_ids",
                "payload_unit_precondition_ids",
            ),
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "source_parent_formula_anchor",
        "source_carried_payload_units",
        "child_target_boundaries",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_child_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_child_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "REPLACE",
            "replace",
            "INSERT",
            "insert",
            "TEXT_REPLACE",
            "text_replace",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded structured payload operation"
            )
        if not _has_any_non_empty_string(
            operation,
            ("payload_unit_id", "source_payload_unit", "child_target_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a payload unit or child target id"
            )
        _append_target_scope_issues(
            issues=issues,
            operation=operation,
            proof_semantic=proof_semantic,
            prefix=prefix,
            op_id=op_id,
            live_paths=live_child_paths,
            region_name="live structured child-target preconditions",
            insert_uses_parent=True,
        )
    return tuple(issues)


def _validate_source_carried_structured_tail_family_proof_semantic(
    *,
    claim: Mapping[str, Any],
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_family: str,
    proof_operation_ids: set[str],
    proof_source_ids: set[str],
    proof_live_ids: set[str],
    proof_live_paths: set[str],
    live_precondition_paths: set[str],
    live_precondition_paths_by_id: Mapping[str, set[str]],
) -> tuple[str, ...]:
    issues: list[str] = []
    if proof_family != "source_carried_structured_tail_substitution":
        issues.append(
            f"{prefix}.proof_semantic {proof_semantic!r} requires "
            "operation_family 'source_carried_structured_tail_substitution'"
        )
    issues.extend(
        _source_carried_required_source_precondition_issues(
            proof=proof,
            proof_semantic=proof_semantic,
            prefix=prefix,
            proof_source_ids=proof_source_ids,
            required_keys=(
                "tail_range_precondition_ids",
                "structured_payload_unit_precondition_ids",
            ),
        )
    )
    ownership_ids = set(_string_tuple_from_value(proof.get("boundary_ownership_ids")))
    for required_id in (
        "source_tail_range_preimage",
        "source_carried_structured_payload_units",
        "child_target_boundaries",
        "flattened_patch_replacement_boundary",
    ):
        if required_id not in ownership_ids:
            issues.append(
                f"{prefix}.{proof_semantic} requires boundary_ownership_ids to "
                f"include {required_id!r}"
            )
    issues.extend(
        _declared_ownership_subset_issues(
            prefix=f"{prefix}.{proof_semantic}.boundary_ownership_ids",
            ids=ownership_ids,
            claim=claim,
        )
    )
    live_tail_paths = _live_carrier_paths_for_proof(
        proof_live_ids=proof_live_ids,
        proof_live_paths=proof_live_paths,
        live_precondition_paths_by_id=live_precondition_paths_by_id,
    )
    if not live_tail_paths:
        issues.append(
            f"{prefix}.{proof_semantic} requires live_target_precondition_ids "
            "or live_target_precondition_paths"
        )
    operations = _operations_by_id(claim)
    for op_id in sorted(proof_operation_ids):
        operation = operations.get(op_id)
        if operation is None:
            continue
        action = _non_empty_string(operation, "action")
        if action not in {
            "REPLACE",
            "replace",
            "INSERT",
            "insert",
            "TEXT_REPLACE",
            "text_replace",
        }:
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must be a "
                "bounded structured tail substitution operation"
            )
        if not _has_any_non_empty_string(
            operation,
            ("tail_range_id", "tail_preimage_id", "tail_boundary"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a tail range or boundary"
            )
        if not _has_any_non_empty_string(
            operation,
            ("payload_unit_id", "source_payload_unit", "child_target_id"),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} must declare "
                "a structured payload unit or child target id"
            )
        _append_target_scope_issues(
            issues=issues,
            operation=operation,
            proof_semantic=proof_semantic,
            prefix=prefix,
            op_id=op_id,
            live_paths=live_tail_paths,
            region_name="live structured tail preconditions",
            insert_uses_parent=True,
        )
    return tuple(issues)


def _source_carried_required_source_precondition_issues(
    *,
    proof: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    proof_source_ids: set[str],
    required_keys: tuple[str, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    if not proof_source_ids:
        issues.append(f"{prefix}.{proof_semantic} requires source_text_precondition_ids")
    for key in required_keys:
        ids = set(_string_tuple_from_value(proof.get(key)))
        if not ids:
            issues.append(f"{prefix}.{proof_semantic} requires {key}")
        issues.extend(
            _source_precondition_subset_issues(
                prefix=f"{prefix}.{proof_semantic}.{key}",
                ids=ids,
                proof_source_ids=proof_source_ids,
            )
        )
    return tuple(issues)


def _append_target_scope_issues(
    *,
    issues: list[str],
    operation: Mapping[str, Any],
    proof_semantic: str,
    prefix: str,
    op_id: str,
    live_paths: set[str],
    region_name: str,
    insert_uses_parent: bool,
) -> None:
    action = _non_empty_string(operation, "action")
    for target in _path_strings_from_value(operation.get("target")):
        checked_path = (
            _parent_path_string(target)
            if insert_uses_parent and action in {"INSERT", "insert"}
            else target
        )
        if live_paths and not _path_within_any_region(
            checked_path,
            tuple(live_paths),
        ):
            issues.append(
                f"{prefix}.{proof_semantic} operation {op_id!r} target "
                f"{target!r} is outside declared {region_name}"
            )


def _validate_mutation_boundary_containment(
    *,
    prefix: str,
    mutation_boundary: Mapping[str, Any],
) -> tuple[str, ...]:
    changed_paths = _path_strings_from_value(mutation_boundary.get("changed_paths"))
    target_region = _path_strings_from_value(mutation_boundary.get("target_region"))
    if not changed_paths or not target_region:
        return ()
    exception_paths = (
        _path_strings_from_value(mutation_boundary.get("declared_migration_paths"))
        + _path_strings_from_value(mutation_boundary.get("declared_recovery_paths"))
        + _path_strings_from_value(
            mutation_boundary.get("declared_editorial_projection_paths")
        )
    )
    authorized_regions = target_region + exception_paths
    issues: list[str] = []
    for changed_path in changed_paths:
        if not _path_within_any_region(changed_path, authorized_regions):
            issues.append(
                f"{prefix}.mutation_boundary.changed_paths contains "
                f"{changed_path!r} outside target_region or declared exception paths"
            )
    return tuple(issues)


def _validate_mutation_boundary_exception_ownership(
    *,
    prefix: str,
    operation: Mapping[str, Any],
    mutation_boundary: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    required_fields_by_exception = {
        "declared_migration_paths": (
            "migration_rule_id",
            "migration_reason",
            "migration_event_id",
        ),
        "declared_recovery_paths": (
            "recovery_rule_id",
            "recovery_reason",
            "recovery_observation_id",
        ),
        "declared_editorial_projection_paths": (
            "editorial_projection_rule_id",
            "editorial_projection_reason",
            "editorial_projection_id",
        ),
    }
    for exception_key, witness_fields in required_fields_by_exception.items():
        if not _path_strings_from_value(mutation_boundary.get(exception_key)):
            continue
        if _has_any_non_empty_string(operation, witness_fields) or _has_any_non_empty_string(
            mutation_boundary,
            witness_fields,
        ):
            continue
        issues.append(
            f"{prefix}.mutation_boundary.{exception_key} requires "
            + ", ".join(witness_fields[:-1])
            + f", or {witness_fields[-1]}"
        )
    return tuple(issues)


def _has_any_non_empty_string(
    row: Mapping[str, Any],
    keys: tuple[str, ...],
) -> bool:
    return any(_optional_string(row, key) for key in keys)


def _path_strings_from_value(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, Mapping):
        path = value.get("path")
        return (path,) if isinstance(path, str) and path else ()
    if not isinstance(value, list | tuple):
        return ()
    paths: list[str] = []
    for item in value:
        paths.extend(_path_strings_from_value(item))
    return tuple(paths)


def _path_within_any_region(path: str, regions: tuple[str, ...]) -> bool:
    return any(
        path == region
        or path.startswith(f"{region}/")
        or path.startswith(f"{region}#")
        or path.startswith(f"{region}::")
        for region in regions
    )


def _parent_path_string(path: str) -> str:
    head, separator, _tail = path.rpartition("/")
    return head if separator else ""


def _validate_live_target_paths(
    claim: Mapping[str, Any],
    live_paths: frozenset[str],
) -> tuple[str, ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    if _optional_string(proposed_outcome, "outcome_kind") != "canonical_operations":
        return ()
    issues: list[str] = []
    for index, operation in enumerate(
        _sequence_value(proposed_outcome, "operations"),
        start=1,
    ):
        if not isinstance(operation, Mapping):
            continue
        prefix = f"canonical_operations[{index}]"
        action = _non_empty_string(operation, "action")
        for target in _path_strings_from_value(operation.get("target")):
            if action in {"INSERT", "insert"}:
                parent = _parent_path_string(target)
                if not parent:
                    if target not in live_paths:
                        issues.append(
                            f"{prefix}.target {target!r} has no parent in supplied "
                            "live target index"
                        )
                    continue
                if parent not in live_paths:
                    issues.append(
                        f"{prefix}.target parent {parent!r} is absent from supplied "
                        f"live target index for insert target {target!r}"
                    )
                continue
            if target not in live_paths:
                issues.append(
                    f"{prefix}.target {target!r} is absent from supplied live target index"
                )
        for destination in _path_strings_from_value(operation.get("destination")):
            parent = _parent_path_string(destination)
            if parent and parent not in live_paths:
                issues.append(
                    f"{prefix}.destination parent {parent!r} is absent from supplied "
                    f"live target index for destination {destination!r}"
                )
    return tuple(issues)


def _live_target_precondition_rows_from_value(
    value: Any,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        path = _optional_string(value, "path")
        if path:
            return (value,)
        rows: list[Mapping[str, Any]] = []
        for path_key, path_value in value.items():
            if not isinstance(path_key, str) or not path_key:
                continue
            if isinstance(path_value, str):
                rows.append({"path": path_key, "subtree_sha256": path_value})
            elif isinstance(path_value, Mapping):
                rows.append({"path": path_key, **dict(path_value)})
        return tuple(rows)
    if not isinstance(value, list | tuple):
        return ()
    rows = []
    for item in value:
        rows += list(_live_target_precondition_rows_from_value(item))
    return tuple(rows)


def _claim_live_target_precondition_rows(
    claim: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    rows: list[Mapping[str, Any]] = []
    for value in (
        claim.get("live_target_preconditions"),
        proposed_outcome.get("live_target_preconditions"),
    ):
        rows += list(_live_target_precondition_rows_from_value(value))
    for operation in _sequence_value(proposed_outcome, "operations"):
        if not isinstance(operation, Mapping):
            continue
        rows += list(
            _live_target_precondition_rows_from_value(
                operation.get("live_target_preconditions")
            )
        )
        rows += list(
            _live_target_precondition_rows_from_value(
                _mapping_value(operation, "mutation_boundary").get(
                    "live_target_preconditions"
                )
            )
        )
    return tuple(rows)


def _live_target_precondition_identity_issues(
    claim: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    seen: dict[str, int] = {}
    for index, precondition in enumerate(
        _claim_live_target_precondition_rows(claim),
        start=1,
    ):
        precondition_id = _optional_string(precondition, "precondition_id")
        if not precondition_id:
            continue
        previous_index = seen.get(precondition_id)
        if previous_index is not None:
            issues.append(
                f"live_target_preconditions[{index}].precondition_id duplicates "
                f"live_target_preconditions[{previous_index}].precondition_id "
                f"{precondition_id!r}"
            )
            continue
        seen[precondition_id] = index
    return tuple(issues)


def _validate_live_target_preconditions(
    claim: Mapping[str, Any],
    live_fingerprints: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, ...], bool]:
    preconditions = _claim_live_target_precondition_rows(claim)
    if not preconditions:
        return (), False
    issues: list[str] = []
    checked = False
    for index, precondition in enumerate(preconditions, start=1):
        prefix = f"live_target_preconditions[{index}]"
        path = _non_empty_string(precondition, "path")
        if not path:
            issues.append(f"{prefix}.path is required")
            continue
        live_fingerprint = live_fingerprints.get(path)
        if live_fingerprint is None:
            issues.append(f"{prefix}.path {path!r} has no supplied live fingerprint")
            continue
        declared_hashes = {
            key: _optional_string(precondition, key)
            for key in ("subtree_sha256", "text_sha256")
            if _optional_string(precondition, key)
        }
        if not declared_hashes:
            issues.append(
                f"{prefix} for {path!r} must declare subtree_sha256 or text_sha256"
            )
            continue
        for key, expected in declared_hashes.items():
            actual = _optional_string(live_fingerprint, key)
            if not actual:
                issues.append(f"{prefix}.{key} for {path!r} is absent from live index")
                continue
            if actual != expected:
                issues.append(
                    f"{prefix}.{key} mismatch for {path!r}: "
                    f"claim={expected!r} live={actual!r}"
                )
                continue
            checked = True
    return tuple(issues), checked


def _validate_non_replayable_finding_shape(
    proposed_outcome: Mapping[str, Any],
) -> tuple[str, ...]:
    finding = _mapping_value(proposed_outcome, "finding")
    if not finding:
        return ("non_replayable_finding outcome requires finding",)
    issues: list[str] = []
    for key in ("rule_id", "reason_code", "reason"):
        if not _non_empty_string(finding, key):
            issues.append(f"non_replayable_finding.finding.{key} is required")
    return tuple(issues)


def _validate_source_pathology_shape(
    proposed_outcome: Mapping[str, Any],
) -> tuple[str, ...]:
    pathology = _mapping_value(proposed_outcome, "pathology")
    if not pathology:
        return ("source_pathology outcome requires pathology",)
    issues: list[str] = []
    for key in ("rule_id", "source_pathology", "reason"):
        if not _non_empty_string(pathology, key):
            issues.append(f"source_pathology.pathology.{key} is required")
    return tuple(issues)


def _validate_oracle_adjudication_shape(
    proposed_outcome: Mapping[str, Any],
) -> tuple[str, ...]:
    adjudication = _mapping_value(proposed_outcome, "adjudication")
    if not adjudication:
        return ("oracle_adjudication outcome requires adjudication",)
    issues: list[str] = []
    for key in ("rule_id", "adjudication_kind", "reason"):
        if not _non_empty_string(adjudication, key):
            issues.append(f"oracle_adjudication.adjudication.{key} is required")
    return tuple(issues)


def _validate_source_evidence_request_shape(
    proposed_outcome: Mapping[str, Any],
) -> tuple[str, ...]:
    requested_evidence = _sequence_value(proposed_outcome, "requested_evidence")
    if not requested_evidence:
        return ("request_more_source_evidence outcome requires requested_evidence",)
    issues: list[str] = []
    for index, item in enumerate(requested_evidence, start=1):
        prefix = f"request_more_source_evidence.requested_evidence[{index}]"
        if not isinstance(item, Mapping):
            issues.append(f"{prefix} must be an object")
            continue
        for key in ("evidence_kind", "reason"):
            if not _non_empty_string(item, key):
                issues.append(f"{prefix}.{key} is required")
    return tuple(issues)


def _match_workqueue(
    row: Mapping[str, Any],
    index: _WorkqueueIndex,
) -> _WorkqueueMatch:
    work_item_id = _optional_string(row, "work_item_id")
    if work_item_id:
        match = index.by_work_item_id.get(work_item_id)
        if match is None:
            return _WorkqueueMatch(
                row=None,
                issues=(f"work_item_id {work_item_id} was not found",),
                status="rejected_workqueue_missing",
            )
        return _WorkqueueMatch(
            row=match,
            issues=_workqueue_mismatch_issues(row, match),
            status="rejected_workqueue_mismatch",
        )
    identity = _claim_identity(row)
    if not all(identity):
        return _WorkqueueMatch(
            row=None,
            issues=(
                "claim must include work_item_id or statute_id/effect_id/manual_compile_rule_id",
            ),
            status="rejected_workqueue_missing",
        )
    matches = index.by_identity.get(identity, ())
    if not matches:
        return _WorkqueueMatch(
            row=None,
            issues=(
                "no workqueue row matched statute_id/effect_id/manual_compile_rule_id",
            ),
            status="rejected_workqueue_missing",
        )
    if len(matches) > 1:
        return _WorkqueueMatch(
            row=None,
            issues=(
                "workqueue identity match is ambiguous; claim must include work_item_id",
            ),
            status="rejected_workqueue_missing",
        )
    match = matches[0]
    return _WorkqueueMatch(
        row=match,
        issues=_workqueue_mismatch_issues(row, match),
        status="rejected_workqueue_mismatch",
    )


def _workqueue_mismatch_issues(
    claim: Mapping[str, Any],
    workqueue: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    for key in (
        "statute_id",
        "effect_id",
        "manual_compile_rule_id",
        "affecting_act_id",
        "affected_provisions",
        "affecting_provisions",
    ):
        claim_value = _optional_string(claim, key)
        workqueue_value = _optional_string(workqueue, key)
        if workqueue_value and not claim_value:
            issues.append(f"{key} is required by matched workqueue")
            continue
        if claim_value and workqueue_value and claim_value != workqueue_value:
            issues.append(
                f"{key} mismatch: claim={claim_value!r} workqueue={workqueue_value!r}"
            )
    template = _mapping_value(workqueue, "suggested_claim_template")
    template_action_family = _optional_string(template, "action_family")
    claim_action_family = _optional_string(claim, "action_family")
    if (
        template_action_family
        and claim_action_family
        and template_action_family != claim_action_family
    ):
        issues.append(
            "action_family mismatch: "
            f"claim={claim_action_family!r} template={template_action_family!r}"
        )
    claim_hash = _claim_source_preview_sha256(claim)
    workqueue_hash = _workqueue_source_preview_sha256(workqueue)
    if claim_hash and workqueue_hash and claim_hash != workqueue_hash:
        issues.append(
            "source_preview_sha256 mismatch: "
            f"claim={claim_hash!r} workqueue={workqueue_hash!r}"
        )
    issues.extend(_workqueue_source_preview_hash_issues(workqueue))
    issues.extend(_template_target_context_issues(claim, template))
    issues.extend(_template_operation_target_issues(claim, template))
    required_validator_checks = _string_set(
        _sequence_value(template, "required_validator_checks")
    )
    if required_validator_checks:
        declared_validator_checks = _claim_validator_check_ids(claim)
        missing_checks = sorted(required_validator_checks - declared_validator_checks)
        if missing_checks:
            issues.append(
                "required_validator_checks missing: "
                + ", ".join(missing_checks)
            )
        issues.extend(
            _validator_check_status_issues(
                claim,
                required_validator_checks,
            )
        )
    required_ownership = _string_set(_sequence_value(template, "required_ownership"))
    if required_ownership:
        declared_ownership = _claim_ownership_ids(claim)
        missing_ownership = sorted(required_ownership - declared_ownership)
        if missing_ownership:
            issues.append("required_ownership missing: " + ", ".join(missing_ownership))
        issues.extend(_ownership_status_issues(claim, required_ownership))
    required_proof_semantics = _string_set(
        _sequence_value(template, "required_operation_family_proof_semantics")
    )
    if required_proof_semantics:
        declared_proof_semantics = set(_claim_operation_family_proof_semantics(claim))
        missing_semantics = sorted(required_proof_semantics - declared_proof_semantics)
        if missing_semantics:
            issues.append(
                "required_operation_family_proof_semantics missing: "
                + ", ".join(missing_semantics)
            )
    return tuple(issues)


def _claim_source_preview_hash_issues(claim: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _preview_hash_issues(
            _mapping_value(claim, "source_witness"),
            prefix="source_witness",
            text_key="text_preview",
            hash_key="source_preview_sha256",
        )
        + _preview_hash_issues(
            _mapping_value(claim, "source"),
            prefix="source",
            text_key="text_preview",
            hash_key="text_preview_sha256",
        )
        + _preview_hash_issues(
            claim,
            prefix="claim",
            text_key="source_preview",
            hash_key="source_preview_sha256",
        )
    )


def _workqueue_source_preview_hash_issues(
    workqueue: Mapping[str, Any],
) -> tuple[str, ...]:
    return _preview_hash_issues(
        _mapping_value(workqueue, "source"),
        prefix="workqueue.source",
        text_key="text_preview",
        hash_key="text_preview_sha256",
    )


def _preview_hash_issues(
    row: Mapping[str, Any],
    *,
    prefix: str,
    text_key: str,
    hash_key: str,
) -> tuple[str, ...]:
    text = _optional_string(row, text_key)
    digest = _optional_string(row, hash_key)
    if not text or not digest:
        return ()
    actual = sha256(text.encode("utf-8")).hexdigest()
    if actual == digest:
        return ()
    return (
        f"{prefix}.{hash_key} does not match {prefix}.{text_key}: "
        f"expected {actual!r} got {digest!r}",
    )


def _source_text_precondition_rows_from_value(
    value: Any,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, str) and value:
        return ({"contains": value},)
    if isinstance(value, Mapping):
        contains = (
            _optional_string(value, "contains")
            or _optional_string(value, "text_contains")
            or _optional_string(value, "snippet")
        )
        if contains:
            return (value,)
        rows: list[Mapping[str, Any]] = []
        for key, item in value.items():
            if isinstance(item, str) and item:
                rows.append({"precondition_id": str(key), "contains": item})
            elif isinstance(item, Mapping):
                rows.append({"precondition_id": str(key), **dict(item)})
        return tuple(rows)
    if not isinstance(value, list | tuple):
        return ()
    rows: list[Mapping[str, Any]] = []
    for item in value:
        rows += list(_source_text_precondition_rows_from_value(item))
    return tuple(rows)


def _claim_source_text_precondition_rows(
    claim: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    rows: list[Mapping[str, Any]] = []
    for value in (
        claim.get("source_text_preconditions"),
        proposed_outcome.get("source_text_preconditions"),
    ):
        rows += list(_source_text_precondition_rows_from_value(value))
    for operation in _sequence_value(proposed_outcome, "operations"):
        if not isinstance(operation, Mapping):
            continue
        rows += list(
            _source_text_precondition_rows_from_value(
                operation.get("source_text_preconditions")
            )
        )
        rows += list(
            _source_text_precondition_rows_from_value(
                _mapping_value(operation, "mutation_boundary").get(
                    "source_text_preconditions"
                )
            )
        )
    return tuple(rows)


def _available_source_texts(
    claim: Mapping[str, Any],
    workqueue_row: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    texts: list[str] = []
    for value in (
        _optional_string(_mapping_value(claim, "source_witness"), "text_preview"),
        _optional_string(_mapping_value(claim, "source"), "text_preview"),
        _optional_string(claim, "source_preview"),
    ):
        if value and value not in texts:
            texts.append(value)
    if workqueue_row is not None:
        workqueue_preview = _optional_string(
            _mapping_value(workqueue_row, "source"),
            "text_preview",
        )
        if workqueue_preview and workqueue_preview not in texts:
            texts.append(workqueue_preview)
    return tuple(texts)


def _source_text_precondition_snippet(precondition: Mapping[str, Any]) -> str:
    return (
        _optional_string(precondition, "contains")
        or _optional_string(precondition, "text_contains")
        or _optional_string(precondition, "snippet")
    )


def _validate_source_text_preconditions(
    claim: Mapping[str, Any],
    workqueue_row: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], bool]:
    preconditions = _claim_source_text_precondition_rows(claim)
    if not preconditions:
        return (), False
    source_texts = _available_source_texts(claim, workqueue_row)
    issues: list[str] = []
    checked = False
    issues.extend(_source_text_precondition_identity_issues(preconditions))
    for index, precondition in enumerate(preconditions, start=1):
        prefix = f"source_text_preconditions[{index}]"
        snippet = _source_text_precondition_snippet(precondition)
        if not snippet:
            issues.append(f"{prefix}.contains is required")
            continue
        declared_hash = (
            _optional_string(precondition, "sha256")
            or _optional_string(precondition, "snippet_sha256")
        )
        if declared_hash:
            actual = sha256(snippet.encode("utf-8")).hexdigest()
            if actual != declared_hash:
                issues.append(
                    f"{prefix}.sha256 does not match declared source text snippet: "
                    f"expected {actual!r} got {declared_hash!r}"
                )
                continue
        if not source_texts:
            issues.append(f"{prefix} cannot be checked because no source text preview is supplied")
            continue
        matching_texts = tuple(
            source_text for source_text in source_texts if snippet in source_text
        )
        if not matching_texts:
            issues.append(f"{prefix}.contains {snippet!r} is absent from supplied source text")
            continue
        count_issues = _source_text_precondition_count_issues(
            prefix=prefix,
            precondition=precondition,
            snippet=snippet,
            matching_texts=matching_texts,
        )
        if count_issues:
            issues.extend(count_issues)
            continue
        checked = True
    order_issues = _source_text_precondition_order_issues(
        preconditions=preconditions,
        source_texts=source_texts,
    )
    if order_issues:
        issues.extend(order_issues)
        checked = False
    return tuple(issues), checked


def _source_text_precondition_identity_issues(
    preconditions: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    seen: dict[str, int] = {}
    for index, precondition in enumerate(preconditions, start=1):
        precondition_id = _optional_string(precondition, "precondition_id")
        if not precondition_id:
            continue
        previous_index = seen.get(precondition_id)
        if previous_index is not None:
            issues.append(
                f"source_text_preconditions[{index}].precondition_id duplicates "
                f"source_text_preconditions[{previous_index}].precondition_id "
                f"{precondition_id!r}"
            )
            continue
        seen[precondition_id] = index
    return tuple(issues)


def _source_text_precondition_count_issues(
    *,
    prefix: str,
    precondition: Mapping[str, Any],
    snippet: str,
    matching_texts: tuple[str, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    counts = tuple(source_text.count(snippet) for source_text in matching_texts)
    exact_count = _optional_nonnegative_int(
        precondition,
        ("occurrence_count", "count"),
    )
    if exact_count is None and _has_any_key(precondition, ("occurrence_count", "count")):
        issues.append(f"{prefix}.occurrence_count must be a non-negative integer")
    elif exact_count is not None and any(count != exact_count for count in counts):
        issues.append(
            f"{prefix}.occurrence_count {exact_count} does not match supplied "
            f"source text counts {list(counts)} for {snippet!r}"
        )
    min_count = _optional_nonnegative_int(
        precondition,
        ("min_occurrences", "minimum_occurrence_count"),
    )
    if min_count is None and _has_any_key(
        precondition,
        ("min_occurrences", "minimum_occurrence_count"),
    ):
        issues.append(f"{prefix}.min_occurrences must be a non-negative integer")
    elif min_count is not None and any(count < min_count for count in counts):
        issues.append(
            f"{prefix}.min_occurrences {min_count} exceeds supplied source text "
            f"counts {list(counts)} for {snippet!r}"
        )
    max_count = _optional_nonnegative_int(
        precondition,
        ("max_occurrences", "maximum_occurrence_count"),
    )
    if max_count is None and _has_any_key(
        precondition,
        ("max_occurrences", "maximum_occurrence_count"),
    ):
        issues.append(f"{prefix}.max_occurrences must be a non-negative integer")
    elif max_count is not None and any(count > max_count for count in counts):
        issues.append(
            f"{prefix}.max_occurrences {max_count} is below supplied source text "
            f"counts {list(counts)} for {snippet!r}"
        )
    return tuple(issues)


def _source_text_precondition_order_issues(
    *,
    preconditions: tuple[Mapping[str, Any], ...],
    source_texts: tuple[str, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    by_id: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, precondition in enumerate(preconditions, start=1):
        precondition_id = _optional_string(precondition, "precondition_id")
        if precondition_id and precondition_id not in by_id:
            by_id[precondition_id] = (index, precondition)
    for index, precondition in enumerate(preconditions, start=1):
        prefix = f"source_text_preconditions[{index}]"
        snippet = _source_text_precondition_snippet(precondition)
        if not snippet:
            continue
        issues.extend(
            _source_text_precondition_relative_order_issues(
                prefix=prefix,
                relation="after",
                relation_keys=("after_precondition_ids", "must_follow_precondition_ids"),
                current_snippet=snippet,
                reference_should_precede=True,
                precondition=precondition,
                by_id=by_id,
                source_texts=source_texts,
            )
        )
        issues.extend(
            _source_text_precondition_relative_order_issues(
                prefix=prefix,
                relation="before",
                relation_keys=("before_precondition_ids", "must_precede_precondition_ids"),
                current_snippet=snippet,
                reference_should_precede=False,
                precondition=precondition,
                by_id=by_id,
                source_texts=source_texts,
            )
        )
    return tuple(issues)


def _source_text_precondition_relative_order_issues(
    *,
    prefix: str,
    relation: str,
    relation_keys: tuple[str, ...],
    current_snippet: str,
    reference_should_precede: bool,
    precondition: Mapping[str, Any],
    by_id: Mapping[str, tuple[int, Mapping[str, Any]]],
    source_texts: tuple[str, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    reference_ids = _string_tuple_from_first_value(precondition, relation_keys)
    if not reference_ids:
        return ()
    if not _optional_string(precondition, "precondition_id"):
        issues.append(f"{prefix}.{relation_keys[0]} requires precondition_id")
    for reference_id in reference_ids:
        reference = by_id.get(reference_id)
        if reference is None:
            issues.append(
                f"{prefix}.{relation_keys[0]} references unknown source text "
                f"precondition {reference_id!r}"
            )
            continue
        reference_index, reference_precondition = reference
        reference_snippet = _source_text_precondition_snippet(reference_precondition)
        if not reference_snippet:
            issues.append(
                f"{prefix}.{relation_keys[0]} references "
                f"source_text_preconditions[{reference_index}] without contains"
            )
            continue
        common_texts = tuple(
            text
            for text in source_texts
            if current_snippet in text and reference_snippet in text
        )
        if not common_texts:
            issues.append(
                f"{prefix}.{relation_keys[0]} cannot be checked because "
                f"{current_snippet!r} and {reference_snippet!r} do not occur in "
                "the same supplied source text"
            )
            continue
        reversed_texts = [
            text_index
            for text_index, text in enumerate(common_texts, start=1)
            if _source_text_precondition_order_is_reversed(
                current_snippet=current_snippet,
                reference_snippet=reference_snippet,
                reference_should_precede=reference_should_precede,
                source_text=text,
            )
        ]
        if reversed_texts:
            issues.append(
                f"{prefix}.{relation_keys[0]} {relation} "
                f"{reference_id!r} is not satisfied by supplied source text "
                f"indexes {reversed_texts}"
            )
    return tuple(issues)


def _source_text_precondition_order_is_reversed(
    *,
    current_snippet: str,
    reference_snippet: str,
    reference_should_precede: bool,
    source_text: str,
) -> bool:
    current_index = source_text.find(current_snippet)
    reference_index = source_text.find(reference_snippet)
    if reference_should_precede:
        return reference_index >= current_index
    return current_index >= reference_index


def _string_tuple_from_first_value(
    row: Mapping[str, Any],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    for key in keys:
        if key in row:
            return _string_tuple_from_value(row.get(key))
    return ()


def _optional_nonnegative_int(
    row: Mapping[str, Any],
    keys: tuple[str, ...],
) -> int | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdecimal():
            return int(value)
        return None
    return None


def _has_any_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(key in row for key in keys)


def _template_target_context_issues(
    claim: Mapping[str, Any],
    template: Mapping[str, Any],
) -> tuple[str, ...]:
    issues: list[str] = []
    for key in ("source_target_address", "destination_address"):
        template_value = _optional_string(template, key)
        if not template_value:
            continue
        claim_value = _claim_declared_target_context_string(claim, key)
        if not claim_value:
            issues.append(f"{key} is required by matched template")
            continue
        if claim_value != template_value:
            issues.append(
                f"{key} mismatch: claim={claim_value!r} template={template_value!r}"
            )
    return tuple(issues)


def _template_operation_target_issues(
    claim: Mapping[str, Any],
    template: Mapping[str, Any],
) -> tuple[str, ...]:
    authorized_carriers = tuple(
        carrier
        for carrier in (
            _optional_string(template, "source_target_address"),
            _optional_string(template, "destination_address"),
        )
        if carrier
    )
    if not authorized_carriers:
        return ()
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    if _optional_string(proposed_outcome, "outcome_kind") != "canonical_operations":
        return ()
    issues: list[str] = []
    for index, operation in enumerate(
        _sequence_value(proposed_outcome, "operations"),
        start=1,
    ):
        if not isinstance(operation, Mapping):
            continue
        for target in _path_strings_from_value(operation.get("target")):
            if _path_within_any_region(target, authorized_carriers):
                continue
            issues.append(
                f"canonical_operations[{index}].target {target!r} is outside "
                "matched template source_target_address/destination_address"
            )
    return tuple(issues)


def _claim_declared_target_context_string(
    claim: Mapping[str, Any],
    key: str,
) -> str:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    for container in (
        claim,
        _mapping_value(claim, "target_context"),
        proposed_outcome,
        _mapping_value(proposed_outcome, "target_context"),
    ):
        value = _optional_string(container, key)
        if value:
            return value
    return ""


def _claim_validator_check_ids(claim: Mapping[str, Any]) -> set[str]:
    return _validator_check_ids_from_value(
        claim.get("validator_checks"),
    ) | _validator_check_ids_from_value(
        _mapping_value(claim, "proposed_outcome").get("validator_checks"),
    )


def _claim_ownership_ids(claim: Mapping[str, Any]) -> set[str]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    return (
        _ownership_ids_from_value(claim.get("ownership_claims"))
        | _ownership_ids_from_value(proposed_outcome.get("ownership_claims"))
        | _ownership_ids_from_value(claim.get("required_ownership"))
        | _ownership_ids_from_value(proposed_outcome.get("required_ownership"))
    )


def _claim_id_declarations_from_value(
    *,
    container: str,
    id_field: str,
    rows: tuple[tuple[str, Mapping[str, Any]], ...],
) -> tuple[_ClaimIdDeclaration, ...]:
    return tuple(
        _ClaimIdDeclaration(
            container=container,
            index=index,
            id_field=id_field,
            value=declaration_id,
        )
        for index, (declaration_id, _row) in enumerate(rows, start=1)
    )


def _claim_id_location(declaration: _ClaimIdDeclaration) -> str:
    return (
        f"{declaration.container}[{declaration.index}]."
        f"{declaration.id_field}"
    )


def _duplicate_claim_id_issues(
    declarations: tuple[_ClaimIdDeclaration, ...],
) -> tuple[str, ...]:
    issues: list[str] = []
    seen: dict[str, _ClaimIdDeclaration] = {}
    for declaration in declarations:
        previous = seen.get(declaration.value)
        if previous is not None:
            issues.append(
                f"{_claim_id_location(declaration)} duplicates "
                f"{_claim_id_location(previous)} {declaration.value!r}"
            )
            continue
        seen[declaration.value] = declaration
    return tuple(issues)


def _claim_validator_check_declarations(
    claim: Mapping[str, Any],
) -> tuple[_ClaimIdDeclaration, ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    declarations: list[_ClaimIdDeclaration] = []
    for container, value in (
        ("validator_checks", claim.get("validator_checks")),
        (
            "proposed_outcome.validator_checks",
            proposed_outcome.get("validator_checks"),
        ),
    ):
        declarations += list(
            _claim_id_declarations_from_value(
                container=container,
                id_field="check_id",
                rows=_validator_check_rows_from_value(value),
            )
        )
    return tuple(declarations)


def _validator_check_identity_issues(claim: Mapping[str, Any]) -> tuple[str, ...]:
    return _duplicate_claim_id_issues(_claim_validator_check_declarations(claim))


def _claim_ownership_declarations(
    claim: Mapping[str, Any],
) -> tuple[_ClaimIdDeclaration, ...]:
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    declarations: list[_ClaimIdDeclaration] = []
    for container, value in (
        ("ownership_claims", claim.get("ownership_claims")),
        (
            "proposed_outcome.ownership_claims",
            proposed_outcome.get("ownership_claims"),
        ),
        ("required_ownership", claim.get("required_ownership")),
        (
            "proposed_outcome.required_ownership",
            proposed_outcome.get("required_ownership"),
        ),
    ):
        declarations += list(
            _claim_id_declarations_from_value(
                container=container,
                id_field="ownership_id",
                rows=_ownership_rows_from_value(value),
            )
        )
    return tuple(declarations)


def _ownership_claim_identity_issues(claim: Mapping[str, Any]) -> tuple[str, ...]:
    return _duplicate_claim_id_issues(_claim_ownership_declarations(claim))


def _ownership_ids_from_value(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {key for key in value if isinstance(key, str) and key}
    if not isinstance(value, list | tuple):
        return set()
    ownership_ids: set[str] = set()
    for item in value:
        if isinstance(item, str) and item:
            ownership_ids.add(item)
            continue
        if isinstance(item, Mapping):
            ownership_id = _optional_string(item, "ownership_id")
            if ownership_id:
                ownership_ids.add(ownership_id)
    return ownership_ids


def _ownership_status_issues(
    claim: Mapping[str, Any],
    required_ownership: set[str],
) -> tuple[str, ...]:
    issues: list[str] = []
    ownership_rows = _claim_ownership_rows(claim)
    for ownership_id in sorted(required_ownership):
        rows = ownership_rows.get(ownership_id, ())
        for row in rows:
            status = _optional_string(row, "status")
            if not status:
                issues.append(f"ownership_claim {ownership_id} status is required")
                continue
            if status in _FORBIDDEN_WEAK_VALIDATOR_CHECK_STATUSES:
                issues.append(
                    f"ownership_claim {ownership_id} status {status!r} cannot be "
                    "claimed by this non-executable validator"
                )
    return tuple(issues)


def _claim_ownership_rows(
    claim: Mapping[str, Any],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    rows_by_id: dict[str, list[Mapping[str, Any]]] = {}
    proposed_outcome = _mapping_value(claim, "proposed_outcome")
    for value in (
        claim.get("ownership_claims"),
        proposed_outcome.get("ownership_claims"),
        claim.get("required_ownership"),
        proposed_outcome.get("required_ownership"),
    ):
        for ownership_id, row in _ownership_rows_from_value(value):
            rows_by_id.setdefault(ownership_id, []).append(row)
    return {
        ownership_id: tuple(rows)
        for ownership_id, rows in rows_by_id.items()
    }


def _ownership_rows_from_value(
    value: Any,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        for ownership_id, status in value.items():
            if isinstance(ownership_id, str) and ownership_id:
                rows.append((ownership_id, {"status": status}))
        return tuple(rows)
    if not isinstance(value, list | tuple):
        return ()
    for item in value:
        if isinstance(item, str) and item:
            rows.append((item, {}))
            continue
        if isinstance(item, Mapping):
            ownership_id = _optional_string(item, "ownership_id")
            if ownership_id:
                rows.append((ownership_id, item))
    return tuple(rows)


def _validator_check_ids_from_value(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {key for key in value if isinstance(key, str) and key}
    if not isinstance(value, list | tuple):
        return set()
    check_ids: set[str] = set()
    for item in value:
        if isinstance(item, str) and item:
            check_ids.add(item)
            continue
        if isinstance(item, Mapping):
            check_id = _optional_string(item, "check_id")
            if check_id:
                check_ids.add(check_id)
    return check_ids


def _validator_check_status_issues(
    claim: Mapping[str, Any],
    required_validator_checks: set[str],
) -> tuple[str, ...]:
    issues: list[str] = []
    check_rows = _claim_validator_check_rows(claim)
    for check_id in sorted(required_validator_checks):
        rows = check_rows.get(check_id, ())
        for row in rows:
            status = _optional_string(row, "status")
            if not status:
                issues.append(f"validator_check {check_id} status is required")
                continue
            if status in _FORBIDDEN_WEAK_VALIDATOR_CHECK_STATUSES:
                issues.append(
                    f"validator_check {check_id} status {status!r} cannot be "
                    "claimed by this non-executable validator"
                )
    return tuple(issues)


def _claim_validator_check_rows(
    claim: Mapping[str, Any],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    rows_by_id: dict[str, list[Mapping[str, Any]]] = {}
    for value in (
        claim.get("validator_checks"),
        _mapping_value(claim, "proposed_outcome").get("validator_checks"),
    ):
        for check_id, row in _validator_check_rows_from_value(value):
            rows_by_id.setdefault(check_id, []).append(row)
    return {
        check_id: tuple(rows)
        for check_id, rows in rows_by_id.items()
    }


def _validator_check_rows_from_value(
    value: Any,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        for check_id, status in value.items():
            if isinstance(check_id, str) and check_id:
                rows.append((check_id, {"status": status}))
        return tuple(rows)
    if not isinstance(value, list | tuple):
        return ()
    for item in value:
        if isinstance(item, str) and item:
            rows.append((item, {}))
            continue
        if isinstance(item, Mapping):
            check_id = _optional_string(item, "check_id")
            if check_id:
                rows.append((check_id, item))
    return tuple(rows)


def _validation_row(
    row: Mapping[str, Any],
    *,
    validator_status: str,
    rule_id: str,
    issues: tuple[str, ...] = (),
    workqueue_row: Mapping[str, Any] | None = None,
    reason: str = "",
    source_text_preconditions_checked: bool = False,
    live_state_checked: bool = False,
    live_state_preconditions_checked: bool = False,
    operation_family_proofs_checked: bool = False,
) -> dict[str, Any]:
    proposed_outcome = _mapping_value(row, "proposed_outcome")
    proof_semantics = _claim_operation_family_proof_semantics(row)
    proof_families = _claim_operation_family_proof_families(row)
    matched_work_item_id = ""
    if workqueue_row is not None:
        matched_work_item_id = _optional_string(workqueue_row, "work_item_id")
    return {
        "schema": _VALIDATION_SCHEMA,
        **diagnostic_detail(
            rule_id=rule_id,
            family="manual_compilation",
            phase="claim_validation",
            reason=reason,
            blocking=validator_status in _REJECTED_STATUSES,
            strict_disposition=(
                "block" if validator_status in _REJECTED_STATUSES else "record"
            ),
            quirks_disposition=(
                "block" if validator_status in _REJECTED_STATUSES else "record"
            ),
        ),
        "jurisdiction": "uk",
        "validator_status": validator_status,
        "validator_scope": (
            _validator_scope(
                source_text_preconditions_checked=source_text_preconditions_checked,
                live_state_checked=live_state_checked,
                live_state_preconditions_checked=live_state_preconditions_checked,
                operation_family_proofs_checked=operation_family_proofs_checked,
            )
        ),
        "source_text_preconditions_checked": source_text_preconditions_checked,
        "live_state_checked": live_state_checked,
        "live_state_preconditions_checked": live_state_preconditions_checked,
        "operation_family_proofs_checked": operation_family_proofs_checked,
        "operation_family_proof_count": len(_claim_operation_family_proof_rows(row)),
        "operation_family_proof_semantics": list(proof_semantics),
        "operation_family_proof_families": list(proof_families),
        "line_number": int(row.get("line_number") or 0),
        "claim_id": _optional_string(row, "claim_id"),
        "claim_status": _optional_string(row, "claim_status"),
        "claim_kind": _optional_string(row, "claim_kind"),
        "statute_id": _optional_string(row, "statute_id"),
        "effect_id": _optional_string(row, "effect_id"),
        "manual_compile_rule_id": _optional_string(row, "manual_compile_rule_id"),
        "action_family": _optional_string(row, "action_family"),
        "work_item_id": _optional_string(row, "work_item_id"),
        "matched_work_item_id": matched_work_item_id,
        "source_preview_sha256": _claim_source_preview_sha256(row),
        "proposed_outcome_kind": _optional_string(proposed_outcome, "outcome_kind"),
        "validation_issues": list(issues),
        "executable": False,
        "replay_authorized": False,
    }


def _validator_scope(
    *,
    source_text_preconditions_checked: bool,
    live_state_checked: bool,
    live_state_preconditions_checked: bool,
    operation_family_proofs_checked: bool,
) -> str:
    if (
        not source_text_preconditions_checked
        and not live_state_checked
        and not operation_family_proofs_checked
    ):
        return "schema_workqueue_shape_and_declared_obligations_non_executable"
    if (
        not source_text_preconditions_checked
        and live_state_preconditions_checked
        and not operation_family_proofs_checked
    ):
        return "schema_workqueue_shape_live_target_preconditions_and_declared_obligations_non_executable"
    if (
        not source_text_preconditions_checked
        and live_state_checked
        and not operation_family_proofs_checked
    ):
        return "schema_workqueue_shape_live_targets_and_declared_obligations_non_executable"
    parts = ["schema", "workqueue", "shape"]
    if source_text_preconditions_checked:
        parts.append("source_text_preconditions")
    if live_state_preconditions_checked:
        parts.append("live_target_preconditions")
    elif live_state_checked:
        parts.append("live_targets")
    if operation_family_proofs_checked:
        parts.append("operation_family_proofs")
    parts.append("declared_obligations")
    parts.append("non_executable")
    return "_".join(parts)


def _accepted_status_and_rule(
    *,
    source_text_preconditions_checked: bool,
    live_state_checked: bool,
    live_state_preconditions_checked: bool,
) -> tuple[str, str]:
    if source_text_preconditions_checked and live_state_preconditions_checked:
        status = "validated_provenance_source_text_live_targets_and_preconditions_only"
    elif source_text_preconditions_checked and live_state_checked:
        status = "validated_provenance_source_text_and_live_targets_only"
    elif source_text_preconditions_checked:
        status = "validated_provenance_and_source_text_only"
    elif live_state_preconditions_checked:
        status = "validated_provenance_live_targets_and_preconditions_only"
    elif live_state_checked:
        status = "validated_provenance_and_live_targets_only"
    else:
        status = "validated_provenance_only"
    return status, f"uk_semantic_claim_{status}"


def validate_semantic_claim_rows(
    rows: tuple[Mapping[str, Any], ...],
    *,
    workqueue_rows: tuple[Mapping[str, Any], ...] = (),
    live_target_rows: tuple[Mapping[str, Any], ...] = (),
) -> tuple[dict[str, Any], ...]:
    index = _build_workqueue_index(workqueue_rows) if workqueue_rows else None
    live_target_index = (
        _build_live_target_index(live_target_rows) if live_target_rows else None
    )
    output: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("validator_status") or "") == "input_error":
            output.append(
                _validation_row(
                    row,
                    validator_status="input_error",
                    rule_id=str(
                        row.get("validator_rule_id")
                        or "uk_semantic_claim_validator_input_error"
                    ),
                    issues=(str(row.get("reason") or "input error"),),
                    reason=str(row.get("reason") or "input error"),
                )
            )
            continue
        schema_issues = _validate_claim_schema(row)
        if schema_issues:
            output.append(
                _validation_row(
                    row,
                    validator_status="rejected_schema",
                    rule_id="uk_semantic_claim_schema_rejected",
                    issues=schema_issues,
                    reason="Semantic claim row failed required schema validation.",
                )
            )
            continue
        operation_family_proofs_checked = bool(_claim_operation_family_proof_rows(row))
        workqueue_match = None
        if index is not None:
            workqueue_match = _match_workqueue(row, index)
            if workqueue_match.row is None:
                output.append(
                    _validation_row(
                        row,
                        validator_status=workqueue_match.status,
                        rule_id="uk_semantic_claim_workqueue_missing",
                        issues=workqueue_match.issues,
                        reason="Semantic claim does not match the supplied workqueue.",
                    )
                )
                continue
            if workqueue_match.issues:
                output.append(
                    _validation_row(
                        row,
                        validator_status=workqueue_match.status,
                        rule_id="uk_semantic_claim_workqueue_mismatch",
                        issues=workqueue_match.issues,
                        workqueue_row=workqueue_match.row,
                        reason="Semantic claim conflicts with the supplied workqueue provenance.",
                    )
                )
                continue
        source_text_issues, source_text_preconditions_checked = (
            _validate_source_text_preconditions(
                row,
                workqueue_match.row if workqueue_match else None,
            )
        )
        if source_text_issues:
            output.append(
                _validation_row(
                    row,
                    validator_status="rejected_source_text_mismatch",
                    rule_id="uk_semantic_claim_source_text_precondition_mismatch",
                    issues=source_text_issues,
                    workqueue_row=workqueue_match.row if workqueue_match else None,
                    reason=(
                        "Semantic claim conflicts with supplied source-text "
                        "preconditions."
                    ),
                )
            )
            continue
        live_state_checked = live_target_index is not None
        if live_target_index is not None:
            statute_id = _optional_string(row, "statute_id")
            live_paths = live_target_index.by_statute_id.get(statute_id)
            if live_paths is None:
                output.append(
                    _validation_row(
                        row,
                        validator_status="rejected_live_state_missing",
                        rule_id="uk_semantic_claim_live_state_missing",
                        issues=(
                            f"no live target index row matched statute_id {statute_id!r}",
                        ),
                        workqueue_row=workqueue_match.row if workqueue_match else None,
                        reason=(
                            "Semantic claim cannot be checked against the supplied "
                            "live target index."
                        ),
                        live_state_checked=True,
                    )
                )
                continue
            live_target_issues = _validate_live_target_paths(row, live_paths)
            if live_target_issues:
                output.append(
                    _validation_row(
                        row,
                        validator_status="rejected_live_state_mismatch",
                        rule_id="uk_semantic_claim_live_target_mismatch",
                        issues=live_target_issues,
                        workqueue_row=workqueue_match.row if workqueue_match else None,
                        reason=(
                            "Semantic claim conflicts with the supplied live target "
                            "index."
                        ),
                        live_state_checked=True,
                    )
                )
                continue
            precondition_issues, live_state_preconditions_checked = (
                _validate_live_target_preconditions(
                    row,
                    live_target_index.fingerprints_by_statute_id.get(statute_id, {}),
                )
            )
            if precondition_issues:
                output.append(
                    _validation_row(
                        row,
                        validator_status="rejected_live_state_mismatch",
                        rule_id="uk_semantic_claim_live_target_precondition_mismatch",
                        issues=precondition_issues,
                        workqueue_row=workqueue_match.row if workqueue_match else None,
                        reason=(
                            "Semantic claim conflicts with supplied live target "
                            "fingerprints."
                        ),
                        live_state_checked=True,
                    )
                )
                continue
        else:
            live_state_preconditions_checked = False
        accepted_status, accepted_rule_id = _accepted_status_and_rule(
            source_text_preconditions_checked=source_text_preconditions_checked,
            live_state_checked=live_state_checked,
            live_state_preconditions_checked=live_state_preconditions_checked,
        )
        output.append(
            _validation_row(
                row,
                validator_status=accepted_status,
                rule_id=accepted_rule_id,
                workqueue_row=workqueue_match.row if workqueue_match else None,
                reason=(
                    "Claim schema, supplied workqueue provenance, and live target "
                    "index validate, but the claim remains non-executable."
                    if live_state_checked
                    else "Claim schema and supplied workqueue provenance validate, "
                    "but the claim remains non-executable."
                ),
                source_text_preconditions_checked=source_text_preconditions_checked,
                live_state_checked=live_state_checked,
                live_state_preconditions_checked=live_state_preconditions_checked,
                operation_family_proofs_checked=operation_family_proofs_checked,
            )
        )
    return tuple(output)


def _validation_report_jsonable(
    *,
    input_path: Path,
    rows: tuple[Mapping[str, Any], ...],
    workqueue_path: Path | None = None,
    live_target_path: Path | None = None,
    validation_jsonl: Mapping[str, Any] | None = None,
    summary_only: bool = False,
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("validator_status") or "unknown") for row in rows)
    rule_counts = Counter(str(row.get("rule_id") or "unknown") for row in rows)
    manual_rule_counts = Counter(
        str(row.get("manual_compile_rule_id") or "unknown")
        for row in rows
        if str(row.get("manual_compile_rule_id") or "")
    )
    outcome_kind_counts = Counter(
        str(row.get("proposed_outcome_kind") or "unknown")
        for row in rows
        if str(row.get("proposed_outcome_kind") or "")
    )
    proof_semantic_counts = Counter(
        proof_semantic
        for row in rows
        for proof_semantic in _string_tuple_from_value(
            row.get("operation_family_proof_semantics")
        )
    )
    proof_family_counts = Counter(
        proof_family
        for row in rows
        for proof_family in _string_tuple_from_value(
            row.get("operation_family_proof_families")
        )
    )
    accepted_count = sum(int(status_counts.get(status, 0)) for status in _ACCEPTED_STATUSES)
    input_error_count = int(status_counts.get("input_error", 0))
    rejected_count = sum(
        int(count)
        for status, count in status_counts.items()
        if status in _REJECTED_STATUSES and status != "input_error"
    )
    report: dict[str, Any] = {
        "report_kind": "uk_semantic_claim_validation_report",
        "input_path": str(input_path),
        "workqueue_path": str(workqueue_path) if workqueue_path is not None else "",
        "live_target_index_path": str(live_target_path) if live_target_path is not None else "",
        "summary": {
            "row_count": len(rows),
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "input_error_count": input_error_count,
            "replay_authorized_count": 0,
            "validator_status_counts": dict(sorted(status_counts.items())),
            "validator_rule_counts": dict(sorted(rule_counts.items())),
            "manual_compile_rule_counts": dict(sorted(manual_rule_counts.items())),
            "proposed_outcome_kind_counts": dict(sorted(outcome_kind_counts.items())),
            "operation_family_proof_semantic_counts": dict(
                sorted(proof_semantic_counts.items())
            ),
            "operation_family_proof_family_counts": dict(
                sorted(proof_family_counts.items())
            ),
        },
    }
    if not summary_only:
        report["rows"] = [dict(row) for row in rows]
    if validation_jsonl is not None:
        report["validation_jsonl"] = dict(validation_jsonl)
    return report


def _format_count_map(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "{}"
    return ", ".join(f"{key}={count}" for key, count in sorted(value.items()))


def _print_text_report(report: Mapping[str, Any], *, summary_only: bool = False) -> None:
    summary = _mapping_value(report, "summary")
    print("UK semantic-claim validation")
    print(f"Rows: {summary.get('row_count', 0)}")
    print(
        "Triage: "
        f"accepted={summary.get('accepted_count', 0)} "
        f"rejected={summary.get('rejected_count', 0)} "
        f"input_errors={summary.get('input_error_count', 0)} "
        f"replay_authorized={summary.get('replay_authorized_count', 0)}"
    )
    print("Statuses: " + _format_count_map(summary.get("validator_status_counts")))
    print("Rules: " + _format_count_map(summary.get("validator_rule_counts")))
    print(
        "Manual rules: "
        + _format_count_map(summary.get("manual_compile_rule_counts"))
    )
    print(
        "Outcome kinds: "
        + _format_count_map(summary.get("proposed_outcome_kind_counts"))
    )
    print(
        "Proof semantics: "
        + _format_count_map(summary.get("operation_family_proof_semantic_counts"))
    )
    print(
        "Proof families: "
        + _format_count_map(summary.get("operation_family_proof_family_counts"))
    )
    validation_jsonl = report.get("validation_jsonl")
    if isinstance(validation_jsonl, Mapping):
        print(
            "Validation JSONL: "
            f"{validation_jsonl.get('path')} rows={validation_jsonl.get('rows')}"
        )
    if summary_only:
        return
    for row in report.get("rows", ()):
        if not isinstance(row, Mapping):
            continue
        print(
            f"{row.get('validator_status')} {row.get('claim_id') or '-'} "
            f"{row.get('statute_id') or '-'} {row.get('effect_id') or '-'} "
            f"rule={row.get('manual_compile_rule_id') or '-'} "
            f"outcome={row.get('proposed_outcome_kind') or '-'} "
            f"replay_authorized={row.get('replay_authorized')}"
        )


def main(args: "argparse.Namespace") -> None:
    input_arg = str(getattr(args, "input", "") or "")
    if not input_arg:
        print("error: uk-semantic-claims-validate requires INPUT", file=sys.stderr)
        sys.exit(2)
    input_path = Path(input_arg)
    if not input_path.exists():
        print(f"error: input JSONL not found at {input_path}", file=sys.stderr)
        sys.exit(1)
    workqueue_arg = str(getattr(args, "workqueue_jsonl", "") or "")
    workqueue_path = Path(workqueue_arg) if workqueue_arg else None
    if workqueue_path is not None and not workqueue_path.exists():
        print(f"error: workqueue JSONL not found at {workqueue_path}", file=sys.stderr)
        sys.exit(1)
    live_target_arg = str(getattr(args, "live_targets_jsonl", "") or "")
    live_target_path = Path(live_target_arg) if live_target_arg else None
    if live_target_path is not None and not live_target_path.exists():
        print(f"error: live target index JSONL not found at {live_target_path}", file=sys.stderr)
        sys.exit(1)
    validation_jsonl_arg = str(getattr(args, "validation_jsonl", "") or "")
    validation_jsonl_path = Path(validation_jsonl_arg) if validation_jsonl_arg else None
    summary_only = bool(getattr(args, "summary_only", False))
    claim_rows = _read_jsonl_rows(input_path)
    workqueue_rows = _read_jsonl_rows(workqueue_path) if workqueue_path is not None else ()
    live_target_rows = (
        _read_jsonl_rows(live_target_path) if live_target_path is not None else ()
    )
    rows = validate_semantic_claim_rows(
        claim_rows,
        workqueue_rows=workqueue_rows,
        live_target_rows=live_target_rows,
    )
    workqueue_input_errors = tuple(
        row for row in workqueue_rows if str(row.get("validator_status") or "") == "input_error"
    )
    if workqueue_input_errors:
        rows = rows + tuple(
            _validation_row(
                row,
                validator_status="input_error",
                rule_id=str(
                    row.get("validator_rule_id")
                    or "uk_semantic_claim_workqueue_input_error"
                ),
                issues=(str(row.get("reason") or "workqueue input error"),),
                reason="Workqueue JSONL input error.",
            )
            for row in workqueue_input_errors
        )
    live_target_input_errors = tuple(
        row for row in live_target_rows if str(row.get("validator_status") or "") == "input_error"
    )
    if live_target_input_errors:
        rows = rows + tuple(
            _validation_row(
                row,
                validator_status="input_error",
                rule_id=str(
                    row.get("validator_rule_id")
                    or "uk_semantic_claim_live_target_input_error"
                ),
                issues=(str(row.get("reason") or "live target index input error"),),
                reason="Live target index JSONL input error.",
                live_state_checked=True,
            )
            for row in live_target_input_errors
        )
    validation_jsonl_report = None
    if validation_jsonl_path is not None:
        validation_jsonl_report = {
            "path": str(validation_jsonl_path),
            "rows": _write_jsonl_rows(validation_jsonl_path, rows),
        }
    report = _validation_report_jsonable(
        input_path=input_path,
        rows=rows,
        workqueue_path=workqueue_path,
        live_target_path=live_target_path,
        validation_jsonl=validation_jsonl_report,
        summary_only=summary_only,
    )
    if bool(getattr(args, "json", False)):
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_text_report(report, summary_only=summary_only)
    summary = report["summary"]
    if bool(getattr(args, "fail_on_input_error", False)) and int(
        summary.get("input_error_count") or 0
    ):
        sys.exit(1)
    if bool(getattr(args, "fail_on_rejected", False)) and int(
        summary.get("rejected_count") or 0
    ):
        sys.exit(1)

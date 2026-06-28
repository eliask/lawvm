"""Project Finland strict-report payloads into candidate-set and closure evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lawvm.core.candidate_set_coverage import (
    CANDIDATE_SET_COMPLETE,
    CANDIDATE_SET_PARTIAL,
    CANDIDATE_SET_UNAVAILABLE,
    CandidateSetCoverage,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.ownership_closure import (
    OwnershipClosureCoverage,
    ownership_closure_evidence_report,
)
from lawvm.core.potential_operation import (
    POTENTIAL_OPERATION_COMPILED,
    POTENTIAL_OPERATION_FAILED,
    PotentialOperation,
)
from lawvm.core.source_unit_coverage import (
    SOURCE_UNIT_FRONTIER_WITNESSED,
    SOURCE_UNIT_LINEAGE_WITNESSED,
    SourceUnitCoverage,
    SourceUnitCoverageStatus,
)
from lawvm.finland.pathology_failed_op_projector import (
    FAILED_OPERATION_REQUIRED_PROOFS,
    FAILED_OPERATION_SAFE_DEFAULT,
    failed_operation_row,
)
from lawvm.finland.proof_surface_row_helpers import (
    kind_slug,
    mapping_or_empty,
    mapping_sequence,
    mapping_str_int,
    mapping_str_str,
    string_sequence,
    with_finland_claim_template,
)
from lawvm.finland.recovery_temporal_proof_projector import (
    recovery_execution_authorization_rows_from_projection_rows,
    temporal_resolution_evidence_rows_from_projection_rows,
)
from lawvm.core.quirks_disposition import QuirksDisposition

def finland_strict_report_candidate_set_coverages(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Candidate-set certificates for visible strict-report accounting slices."""

    statute_id = str(payload.get("statute_id") or "unknown")
    canonical_count = _ops_count(payload, "canonical")
    canonical_op_ids = string_sequence(payload.get("canonical_op_ids"))
    failed_ops = mapping_sequence(payload.get("failed_ops"))
    potential_operations = mapping_sequence(payload.get("potential_operations"))
    source_unit_coverages = mapping_sequence(payload.get("source_unit_coverages"))
    source_lineage_witnesses = mapping_sequence(payload.get("source_lineage_source_witnesses"))
    visible_operation_ids = (
        _potential_operation_candidate_ids(potential_operations)
        or _visible_operation_candidate_ids(
            canonical_op_ids=canonical_op_ids,
            canonical_count=canonical_count,
            failed_ops=failed_ops,
        )
    )
    source_unit_ids = (
        _source_unit_coverage_candidate_ids(source_unit_coverages)
        or _source_lineage_candidate_ids(source_lineage_witnesses)
    )
    return [
        CandidateSetCoverage(
            scope_id=f"fi:{statute_id}:strict-report-visible-operation-rows",
            candidate_set_kind="fi_strict_report_visible_operation_rows",
            phase="strict_report_projection",
            rule_id="fi_strict_report_visible_operation_candidate_projection",
            reason=(
                "Visible canonical and failed operation rows are accounted for, "
                "but this does not prove all source-text operation cues were enumerated."
            ),
            completeness_status=CANDIDATE_SET_PARTIAL,
            candidate_count=len(visible_operation_ids),
            candidate_ids=visible_operation_ids,
            missing_candidate_count=1,
            blocker_counts={"source_text_cue_exhaustiveness_unproved": 1},
            blocker_families=("source_text_cue_exhaustiveness_unproved",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_unit_enumeration_certificate",
                "operation_cue_exhaustiveness_certificate",
                "execution_authorization",
            ),
            detail={
                "visible_scope": "strict_report_canonical_andfailed_operation_rows",
                "potential_operation_row_count": len(potential_operations),
                "canonical_count": canonical_count,
                "failed_count": len(failed_ops),
                "safe_default": "do_not_treat_visible_operation_rows_as_complete_source_cue_coverage",
            },
        ).to_dict(),
        CandidateSetCoverage(
            scope_id=f"fi:{statute_id}:strict-report-source-lineage-units",
            candidate_set_kind="fi_strict_report_source_lineage_units",
            phase="source_chain_elaboration",
            rule_id="fi_strict_report_source_lineage_candidate_projection",
            reason=(
                "Source-lineage witnesses are accounted for when available, but "
                "they are amendment-chain witnesses rather than a full source-unit enumeration."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if source_unit_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(source_unit_ids),
            candidate_ids=source_unit_ids,
            missing_candidate_count=1,
            blocker_counts={"source_unit_enumeration_exhaustiveness_unproved": 1},
            blocker_families=("source_unit_enumeration_exhaustiveness_unproved",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_unit_enumeration_certificate",
                "source_unit_digest_coverage",
            ),
            detail={
                "visible_scope": "strict_report_source_lineage_witnesses",
                "safe_default": "do_not_treat_lineage_witnesses_as_full_source_unit_enumeration",
            },
        ).to_dict(),
        CandidateSetCoverage(
            scope_id=f"fi:{statute_id}:strict-report-source-unit-enumeration",
            candidate_set_kind="fi_strict_report_source_unit_enumeration",
            phase="source_unit_enumeration",
            rule_id="fi_strict_report_source_unit_enumeration_gap_certificate",
            reason=(
                "Strict-report evidence does not yet include an independent "
                "enumeration of all amendment-bearing source units."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if source_unit_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(source_unit_ids),
            candidate_ids=source_unit_ids,
            missing_candidate_count=1,
            blocker_counts={"source_unit_enumeration_not_complete": 1},
            blocker_families=("source_unit_enumeration_gap",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "source_artifact_unit_inventory",
                "source_unit_digest_coverage",
                "unclassified_source_unit_count_zero",
            ),
            detail={
                "visible_scope": "strict_report_negative_space_source_units",
                "source_unit_coverage_count": len(source_unit_coverages),
                "source_lineage_witness_count": len(source_lineage_witnesses),
                "safe_default": "do_not_treat_this_as_source_unit_closure_until_complete",
            },
        ).to_dict(),
        CandidateSetCoverage(
            scope_id=f"fi:{statute_id}:strict-report-operation-cue-coverage",
            candidate_set_kind="fi_strict_report_operation_cue_coverage",
            phase="operation_cue_detection",
            rule_id="fi_strict_report_operation_cue_coverage_gap_certificate",
            reason=(
                "Strict-report visible operation rows do not prove that all "
                "source-text operation cues were detected or classified."
            ),
            completeness_status=(
                CANDIDATE_SET_PARTIAL if visible_operation_ids else CANDIDATE_SET_UNAVAILABLE
            ),
            candidate_count=len(visible_operation_ids),
            candidate_ids=visible_operation_ids,
            missing_candidate_count=1,
            blocker_counts={"operation_cue_exhaustiveness_unproved": 1},
            blocker_families=("operation_cue_coverage_gap",),
            next_promotion_allowed=False,
            next_promotion_requires=(
                "independent_source_text_cue_detector",
                "operation_cue_classification_report",
                "parser_gap_frontier_items_for_unclassified_cues",
            ),
            detail={
                "visible_scope": "strict_report_potential_operation_rows",
                "visible_operation_row_count": len(visible_operation_ids),
                "potential_operation_row_count": len(potential_operations),
                "safe_default": "do_not_treat_visible_operation_rows_as_operation_cue_closure",
            },
        ).to_dict(),
    ]


def finland_strict_report_potential_operation_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Potential-operation coverage rows for visible strict-report operations.

    These rows are a typed census of visible canonical/failed operation rows.
    They intentionally do not prove that all source-text operation cues were
    independently detected.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    canonical_count = _ops_count(payload, "canonical")
    canonical_op_ids = string_sequence(payload.get("canonical_op_ids"))
    failed_ops = mapping_sequence(payload.get("failed_ops"))
    failed_operation_frontiers = _failed_operation_frontier_by_candidate_id(
        mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    )
    rows: list[dict[str, Any]] = []
    for index, op_id in enumerate(canonical_op_ids[:canonical_count], start=1):
        visible_id = op_id or f"canonical-op:{index}"
        rows.append(_strict_report_canonical_potential_operation(
            statute_id=statute_id,
            visible_id=visible_id,
            visible_index=index,
            synthesized=False,
        ))
    for index in range(len(rows), canonical_count):
        visible_id = f"canonical-op:{index + 1}"
        rows.append(_strict_report_canonical_potential_operation(
            statute_id=statute_id,
            visible_id=visible_id,
            visible_index=index + 1,
            synthesized=True,
        ))
    for index, row in enumerate(failed_ops):
        candidate_id = _failed_operation_candidate_id(index=index, row=row)
        failed_row = failed_operation_row(row)
        source_anchor = _failed_operation_potential_source_anchor(
            failed_operation_frontiers.get(candidate_id)
        )
        rows.append(
            PotentialOperation(
                potential_operation_id=candidate_id,
                jurisdiction="fi",
                source_artifact_id=str(
                    failed_row.get("source_statute")
                    or failed_row.get("amendment_id")
                    or statute_id
                ),
                source_unit_id=str(
                    failed_row.get("target_label")
                    or failed_row.get("description")
                    or f"failed-operation:{index + 1}"
                ),
                owner_phase="replay_apply",
                classification=POTENTIAL_OPERATION_FAILED,
                operation_family="fi_failed_operation",
                action=str(row.get("action") or ""),
                target=str(failed_row.get("target_label") or ""),
                source_anchor=source_anchor,
                refs=(),
                required_proofs=FAILED_OPERATION_REQUIRED_PROOFS,
                safe_default=FAILED_OPERATION_SAFE_DEFAULT,
                detail={
                    "statute_id": statute_id,
                    "visible_index": index + 1,
                    "failed_operation": failed_row,
                    "projection_only": True,
                },
            ).to_dict()
        )
    return rows


def _failed_operation_frontier_by_candidate_id(
    frontier_items: tuple[Mapping[str, Any], ...],
) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(frontier_items):
        detail = mapping_or_empty(item.get("detail"))
        failed_operation = mapping_or_empty(detail.get("failed_operation"))
        candidate_id = _failed_operation_candidate_id(index=index, row=failed_operation)
        rows[candidate_id] = item
    return rows


def _failed_operation_potential_source_anchor(
    frontier: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if frontier is None:
        return {}
    source_witness = mapping_or_empty(frontier.get("source_witness"))
    return {
        "basis": "failed_operation_frontier_source_witness",
        "frontier_work_item_id": str(frontier.get("work_item_id") or ""),
        "frontier_status": str(frontier.get("frontier_status") or ""),
        "authorization_status": str(frontier.get("authorization_status") or ""),
        "required_claim_kind": str(frontier.get("required_claim_kind") or ""),
        "source_artifact_id": str(frontier.get("source_artifact_id") or ""),
        "source_unit_id": str(frontier.get("source_unit_id") or ""),
        "source_role": str(source_witness.get("source_role") or ""),
        "source_lane": str(source_witness.get("source_lane") or ""),
        "preview_digest_algorithm": str(source_witness.get("preview_digest_algorithm") or ""),
        "preview_digest": str(source_witness.get("preview_digest") or ""),
        "projection_only": True,
        "does_not_claim": [
            "operation_cue_exhaustiveness",
            "source_unit_enumeration_closure",
            "replay_authorization",
        ],
    }


def finland_strict_report_source_unit_coverage_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Source-unit coverage rows visible to the Finland strict report.

    This projects source-lineage and frontier source witnesses. It is useful
    accounting, but it is not a full inventory of amendment-bearing XML units.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    source_lineage_witnesses = mapping_sequence(payload.get("source_lineage_source_witnesses"))
    source_pathology_frontiers = mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_frontiers = mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, witness in enumerate(source_lineage_witnesses, start=1):
        artifact_id = str(witness.get("artifact_id") or witness.get("amendment_id") or statute_id)
        source_unit_id = str(witness.get("source_unit_id") or artifact_id or f"lineage:{index}")
        _append_source_unit_coverage_row(
            rows,
            seen=seen,
            statute_id=statute_id,
            artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase="source_chain_elaboration",
            coverage_status=SOURCE_UNIT_LINEAGE_WITNESSED,
            unit_family="finland_source_lineage_amendment",
            source_role=str(witness.get("source_role") or "finland_source_lineage_amendment"),
            source_lane=str(witness.get("source_lane") or "finland_source_adjudication_lineage"),
            witness=witness,
            index=index,
            safe_default="treat_lineage_source_unit_coverage_as_witnessed_only_not_full_enumeration",
        )
    frontier_index = len(rows)
    for frontier in (*source_pathology_frontiers, *failed_operation_frontiers):
        witness = frontier.get("source_witness")
        if not isinstance(witness, Mapping):
            continue
        frontier_index += 1
        artifact_id = str(
            witness.get("artifact_id")
            or witness.get("source_statute")
            or frontier.get("source_artifact_id")
            or statute_id
        )
        source_unit_id = str(
            witness.get("source_unit_id")
            or frontier.get("source_unit_id")
            or frontier.get("work_item_id")
            or f"frontier:{frontier_index}"
        )
        _append_source_unit_coverage_row(
            rows,
            seen=seen,
            statute_id=statute_id,
            artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase=str(frontier.get("owner_phase") or "frontier_projection"),
            coverage_status=SOURCE_UNIT_FRONTIER_WITNESSED,
            unit_family=str(frontier.get("frontier_family") or "finland_frontier_source_unit"),
            source_role=str(witness.get("source_role") or "finland_frontier_source_unit"),
            source_lane=str(witness.get("source_lane") or frontier.get("frontier_status") or "frontier"),
            witness=witness,
            index=frontier_index,
            safe_default="treat_frontier_source_unit_coverage_as_witnessed_only_not_full_enumeration",
            frontier=frontier,
        )
    return rows


def finland_strict_report_candidate_set_execution_authorizations(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replay-authorization boundary rows for strict-report candidate sets."""

    rows = mapping_sequence(payload.get("strict_report_candidate_set_coverages"))
    authorizations: list[dict[str, Any]] = []
    for row in rows:
        candidate_set_kind = str(row.get("candidate_set_kind") or "unknown")
        completeness_status = str(row.get("completeness_status") or "unknown")
        complete = completeness_status == CANDIDATE_SET_COMPLETE
        required_proofs = string_sequence(row.get("next_promotion_requires")) or (
            "candidate_set_completion_proof",
            "execution_authorization",
        )
        authorization = ExecutionAuthorization(
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "candidate_set_complete_not_replay_authority"
                if complete
                else "candidate_set_incomplete_not_replay_authority"
            ),
            authorization_rule_id=f"fi_strict_report_candidate_set_{kind_slug(candidate_set_kind)}",
            owner_phase=str(row.get("phase") or "strict_report_projection"),
            strict_disposition="record" if complete else "block",
            quirks_disposition=QuirksDisposition.RECORD,
            validator_status=(
                "candidate_set_complete_requires_separate_execution_authorization"
                if complete
                else "candidate_set_incomplete_requires_missing_coverage_proofs"
            ),
            required_proofs=required_proofs,
            safe_default="do_not_treat_candidate_set_coverage_as_replay_authorization",
            forbidden_shortcuts=(
                "candidate_set_coverage_as_replay_authorization",
                "visible_candidate_set_as_source_cue_exhaustiveness_proof",
                "partial_candidate_set_as_target_uniqueness_proof",
            ),
            detail={
                "jurisdiction": "fi",
                "candidate_set_kind": candidate_set_kind,
                "completeness_status": completeness_status,
                "scope_id": str(row.get("scope_id") or ""),
                "candidate_count": int(row.get("candidate_count") or 0),
                "missing_candidate_count": int(row.get("missing_candidate_count") or 0),
                "blocker_counts": dict(row.get("blocker_counts") or {}),
                "projection_only": True,
            },
        ).to_dict()
        authorization["row_id"] = _strict_report_candidate_set_authorization_row_id(
            candidate_set_kind=candidate_set_kind,
            scope_id=str(row.get("scope_id") or ""),
            completeness_status=completeness_status,
        )
        authorization["candidate_set_kind"] = candidate_set_kind
        authorization["completeness_status"] = completeness_status
        authorization["scope_id"] = str(row.get("scope_id") or "")
        authorizations.append(authorization)
    return authorizations


def _append_source_unit_coverage_row(
    rows: list[dict[str, Any]],
    *,
    seen: set[tuple[str, str, str, str]],
    statute_id: str,
    artifact_id: str,
    source_unit_id: str,
    owner_phase: str,
    coverage_status: SourceUnitCoverageStatus,
    unit_family: str,
    source_role: str,
    source_lane: str,
    witness: Mapping[str, Any],
    index: int,
    safe_default: str,
    frontier: Mapping[str, Any] | None = None,
) -> None:
    key = (artifact_id, source_unit_id, coverage_status, source_lane)
    if key in seen:
        return
    seen.add(key)
    detail: dict[str, Any] = {
        "statute_id": statute_id,
        "visible_index": index,
        "source_witness": dict(witness),
        "projection_only": True,
        "does_not_claim": [
            "complete_source_unit_enumeration",
            "operation_cue_exhaustiveness",
            "replay_authorization",
        ],
    }
    if frontier is not None:
        detail["frontier_work_item_id"] = str(frontier.get("work_item_id") or "")
        detail["frontier_status"] = str(frontier.get("frontier_status") or "")
    rows.append(
        SourceUnitCoverage(
            coverage_id=_strict_report_source_unit_coverage_id(
                statute_id=statute_id,
                artifact_id=artifact_id,
                source_unit_id=source_unit_id,
                index=index,
            ),
            jurisdiction="fi",
            source_artifact_id=artifact_id,
            source_unit_id=source_unit_id,
            owner_phase=owner_phase,
            coverage_status=coverage_status,
            unit_family=unit_family,
            source_role=source_role,
            source_lane=source_lane,
            refs=(artifact_id,),
            required_proofs=(
                "source_artifact_unit_inventory",
                "source_unit_digest_coverage",
                "unclassified_source_unit_count_zero",
            ),
            safe_default=safe_default,
            detail=detail,
        ).to_dict()
    )


def finland_strict_report_candidate_set_frontier_work_items(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project incomplete strict-report candidate sets as actionable work.

    These rows are not manual-claim templates and not replay authority. They
    simply make the missing coverage-certificate work queue machine-visible.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    rows = mapping_sequence(payload.get("strict_report_candidate_set_coverages"))
    frontier_items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        completeness_status = str(row.get("completeness_status") or "")
        if completeness_status == CANDIDATE_SET_COMPLETE:
            continue
        candidate_set_kind = str(row.get("candidate_set_kind") or "unknown")
        scope_id = str(row.get("scope_id") or candidate_set_kind)
        profile = _candidate_set_frontier_profile(candidate_set_kind)
        required_proofs = string_sequence(row.get("next_promotion_requires")) or (
            "candidate_set_completion_certificate",
        )
        item = FrontierWorkItem(
            work_item_id=_strict_report_candidate_set_frontier_work_item_id(
                statute_id=statute_id,
                candidate_set_kind=candidate_set_kind,
                scope_id=scope_id,
                completeness_status=completeness_status,
                index=index,
            ),
            jurisdiction="fi",
            source_artifact_id=f"fi:{statute_id}:strict-report-candidate-sets",
            source_unit_id=scope_id,
            source_witness={
                "source_role": "finland_strict_report_candidate_set",
                "artifact_id": f"fi:{statute_id}:strict-report-candidate-sets",
                "source_unit_id": scope_id,
                "source_lane": "strict_report_candidate_set",
            },
            target_witness={
                "candidate_set_kind": candidate_set_kind,
                "scope_id": scope_id,
                "completeness_status": completeness_status,
                "candidate_count": int(row.get("candidate_count") or 0),
                "missing_candidate_count": int(row.get("missing_candidate_count") or 0),
                "blocker_counts": dict(row.get("blocker_counts") or {}),
            },
            owner_phase=str(row.get("phase") or "strict_report_projection"),
            frontier_family=profile["frontier_family"],
            frontier_status=f"{completeness_status or 'unknown'}_candidate_set_frontier",
            candidate_operation_family=profile["candidate_operation_family"],
            candidate_targets=(scope_id,),
            guidance_refs=profile["guidance_refs"],
            required_claim_kind=profile["required_claim_kind"],
            required_validator_checks=profile["required_validator_checks"],
            required_proofs=required_proofs,
            safe_default=profile["safe_default"],
            forbidden_shortcuts=(
                "candidate_set_frontier_as_replay_authorization",
                "candidate_set_frontier_as_source_cue_exhaustiveness_proof",
                "partial_candidate_set_as_complete_candidate_set",
                *profile["forbidden_shortcuts"],
            ),
            executable=False,
            replay_authorized=False,
            authorization_status=(
                "candidate_set_incomplete_not_replay_authority"
                if completeness_status != CANDIDATE_SET_COMPLETE
                else "candidate_set_complete_not_replay_authority"
            ),
            detail={
                "candidate_set": dict(row),
                "projection_only": True,
                "does_not_claim": [
                    "candidate_set_completion",
                    "operation_cue_exhaustiveness",
                    "source_unit_enumeration_closure",
                    "replay_authorization",
                    *profile["does_not_claim"],
                ],
            },
        )
        frontier_items.append(with_finland_claim_template(item).to_dict())
    return frontier_items


def finland_strict_report_ownership_closure_coverage(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a passive ownership-closure certificate for strict-report facts.

    This certificate is deliberately scoped to the strict-report proof surfaces.
    It stays open until source-unit enumeration and operation-candidate coverage
    are backed by real certificates; those missing proof boundaries are not
    papered over by a green strict-report row.
    """

    statute_id = str(payload.get("statute_id") or "unknown")
    profile_id = str(payload.get("profile") or "unknown_profile")
    projection_rows = mapping_sequence(payload.get("projection_rows"))
    failed_ops = mapping_sequence(payload.get("failed_ops"))
    strict_fail_reasons = string_sequence(payload.get("strict_fail_reasons"))
    source_pathologies = mapping_sequence(payload.get("source_pathologies"))
    source_pathology_frontier_items = mapping_sequence(payload.get("source_pathology_frontier_work_items"))
    failed_operation_frontier_items = mapping_sequence(payload.get("failed_operation_frontier_work_items"))
    potential_operations = mapping_sequence(payload.get("potential_operations"))
    source_unit_coverages = mapping_sequence(payload.get("source_unit_coverages"))
    sparse_certificates = mapping_sequence(payload.get("sparse_slot_candidate_set_coverages"))
    source_lineage_witnesses = mapping_sequence(payload.get("source_lineage_source_witnesses"))
    agreement_residuals = mapping_sequence(payload.get("agreement_residuals"))
    mutation_boundary_proofs = mapping_sequence(payload.get("mutation_boundary_proofs"))
    candidate_set_coverages = mapping_sequence(payload.get("strict_report_candidate_set_coverages"))
    candidate_set_authorizations = mapping_sequence(payload.get("strict_report_candidate_set_execution_authorizations"))
    candidate_set_frontier_items = mapping_sequence(payload.get("strict_report_candidate_set_frontier_work_items"))
    source_completeness = mapping_or_empty(payload.get("source_completeness"))
    temporal_rows = temporal_resolution_evidence_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
    )
    recovery_authorizations = recovery_execution_authorization_rows_from_projection_rows(
        projection_rows,
        strict_fail_reasons=strict_fail_reasons,
        statute_id=statute_id,
    )
    unproved_mutation_boundaries = tuple(
        row for row in mutation_boundary_proofs if str(row.get("boundary_proof_status") or "") != "proved"
    )
    incomplete_candidate_sets = tuple(
        row
        for row in candidate_set_coverages
        if str(row.get("completeness_status") or "") != "complete"
    )
    candidate_set_authorization_keys = {
        _strict_report_candidate_set_authorization_key(row)
        for row in candidate_set_authorizations
        if _strict_report_candidate_set_authorization_key(row) is not None
    }
    candidate_sets_without_authorization = tuple(
        row
        for row in candidate_set_coverages
        if _strict_report_candidate_set_authorization_key(row)
        not in candidate_set_authorization_keys
    )
    candidate_set_frontier_keys = {
        _strict_report_candidate_set_frontier_key(row)
        for row in candidate_set_frontier_items
        if _strict_report_candidate_set_frontier_key(row) is not None
    }
    incomplete_candidate_sets_without_frontier = tuple(
        row
        for row in incomplete_candidate_sets
        if _strict_report_candidate_set_frontier_key(row) not in candidate_set_frontier_keys
    )
    failed_gates = (
        *(
            (
                "source_unit_enumeration_closure_unverified",
                "operation_candidate_coverage_unverified",
            )
            if not candidate_set_coverages
            else ()
        ),
        *(
            f"candidate_set_{str(row.get('candidate_set_kind') or 'unknown')}_{str(row.get('completeness_status') or 'unknown')}"
            for row in incomplete_candidate_sets
        ),
        *(
            "candidate_set_"
            f"{str(row.get('candidate_set_kind') or 'unknown')}_"
            f"{str(row.get('scope_id') or 'unknown')}_"
            "execution_authorization_missing"
            for row in candidate_sets_without_authorization
        ),
        *(() if not failed_ops else ("failed_ops_present",)),
        *(() if not strict_fail_reasons else ("strict_fail_reasons_present",)),
        *(() if not unproved_mutation_boundaries else ("unproved_mutation_boundary_present",)),
    )
    unowned_counts = {
        "source_units_without_enumeration_certificate": 1
        if not _has_candidate_set(candidate_set_coverages, "fi_strict_report_source_unit_enumeration")
        else 0,
        "operation_cues_without_candidate_coverage_certificate": 1
        if not _has_candidate_set(candidate_set_coverages, "fi_strict_report_operation_cue_coverage")
        else 0,
        "incomplete_candidate_set_coverages": len(incomplete_candidate_sets),
        "candidate_set_coverages_without_execution_authorization": len(
            candidate_sets_without_authorization
        ),
        "incomplete_candidate_set_coverages_without_frontier_work_item": len(
            incomplete_candidate_sets_without_frontier
        ),
        "failed_ops_without_frontier_work_item": max(
            len(failed_ops) - len(failed_operation_frontier_items),
            0,
        ),
        "strict_fail_reasons_without_closure": len(strict_fail_reasons),
        "unproved_mutation_boundary_proofs": len(unproved_mutation_boundaries),
    }
    closed = not failed_gates and all(count == 0 for count in unowned_counts.values())
    missing_required_certificates = (
        *_closure_certificate_requirements(
            candidate_set_coverages,
            candidate_set_kind="fi_strict_report_source_unit_enumeration",
            missing_certificate="source_unit_enumeration_certificate",
            incomplete_certificate="complete_source_unit_enumeration_certificate",
        ),
        *_closure_certificate_requirements(
            candidate_set_coverages,
            candidate_set_kind="fi_strict_report_operation_cue_coverage",
            missing_certificate="operation_candidate_coverage_certificate",
            incomplete_certificate="complete_operation_cue_exhaustiveness_certificate",
        ),
        *(
            "candidate_set_execution_authorization:"
            f"{str(row.get('candidate_set_kind') or 'unknown')}:"
            f"{str(row.get('scope_id') or 'unknown')}"
            for row in candidate_sets_without_authorization
        ),
        *(
            "candidate_set_frontier_work_item:"
            f"{str(row.get('candidate_set_kind') or 'unknown')}:"
            f"{str(row.get('scope_id') or 'unknown')}"
            for row in incomplete_candidate_sets_without_frontier
        ),
    )
    closure_dimensions = (
        "visible_operation_rows",
        "source_lineage_units",
        "source_unit_enumeration",
        "operation_cue_coverage",
        "failed_operations",
        "strict_fail_reasons",
        "mutation_boundary_proofs",
    )
    does_not_claim = (
        "full_finland_corpus_closure",
        *(
            ()
            if _candidate_set_complete(
                candidate_set_coverages,
                "fi_strict_report_source_unit_enumeration",
            )
            else ("source_unit_enumeration_closure",)
        ),
        *(
            ()
            if _candidate_set_complete(
                candidate_set_coverages,
                "fi_strict_report_operation_cue_coverage",
            )
            else ("operation_candidate_coverage_closure",)
        ),
        "replay_authorization",
    )
    owned_counts = {
        "canonical_ops": _ops_count(payload, "canonical"),
        "failed_ops_visible": len(failed_ops),
        "failed_operation_frontier_items": len(failed_operation_frontier_items),
        "potential_operations": len(potential_operations),
        "projection_rows_visible": len(projection_rows),
        "source_pathology_frontier_items": len(source_pathology_frontier_items),
        "source_pathologies_visible": len(source_pathologies),
        "sparse_slot_candidate_certificates": len(sparse_certificates),
        "source_lineage_witnesses": len(source_lineage_witnesses),
        "source_unit_coverages": len(source_unit_coverages),
        "agreement_residuals": len(agreement_residuals),
        "mutation_boundary_proofs": len(mutation_boundary_proofs),
        "temporal_resolution_evidence_rows": len(temporal_rows),
        "recovery_authorization_rows": len(recovery_authorizations),
        "strict_report_candidate_set_coverages": len(candidate_set_coverages),
        "strict_report_candidate_set_authorizations": len(candidate_set_authorizations),
        "strict_report_candidate_set_frontier_items": len(candidate_set_frontier_items),
    }
    certificate = OwnershipClosureCoverage(
        certificate_id=_strict_report_id("ownership-closure", statute_id, payload),
        corpus_slice_id=f"fi:{statute_id}:strict-report-visible-surfaces",
        source_bundle_hash=_strict_report_digest(
            "source-bundle",
            {
                "source_completeness": source_completeness,
                "source_lineage_source_witnesses": source_lineage_witnesses,
                "source_unit_coverages": source_unit_coverages,
            },
        ),
        profile_id=profile_id,
        interpretation_policy_id="fi.strict_report.visible_surfaces.v1",
        graph_snapshot_hash=_strict_report_digest(
            "strict-report-graph",
            _strict_report_closure_graph_payload(payload),
        ),
        phase_report_ids={
            "strict_report": _strict_report_id("strict-report", statute_id, payload),
            "source_lineage_witnesses": _strict_report_id(
                "source-lineage",
                statute_id,
                source_lineage_witnesses,
            ),
            "source_pathology_frontiers": _strict_report_id(
                "source-pathology-frontiers",
                statute_id,
                source_pathology_frontier_items,
            ),
            "failed_operation_frontiers": _strict_report_id(
                "failed-operation-frontiers",
                statute_id,
                failed_operation_frontier_items,
            ),
            "potential_operations": _strict_report_id(
                "potential-operations",
                statute_id,
                potential_operations,
            ),
            "source_unit_coverages": _strict_report_id(
                "source-unit-coverages",
                statute_id,
                source_unit_coverages,
            ),
            "mutation_boundary_proofs": _strict_report_id(
                "mutation-boundary",
                statute_id,
                mutation_boundary_proofs,
            ),
            "strict_report_candidate_sets": _strict_report_id(
                "strict-report-candidate-sets",
                statute_id,
                candidate_set_coverages,
            ),
            "strict_report_candidate_set_authorizations": _strict_report_id(
                "strict-report-candidate-set-authorizations",
                statute_id,
                candidate_set_authorizations,
            ),
            "strict_report_candidate_set_frontiers": _strict_report_id(
                "strict-report-candidate-set-frontiers",
                statute_id,
                candidate_set_frontier_items,
            ),
        },
        closed=closed,
        failed_gates=tuple(failed_gates),
        unowned_counts=unowned_counts,
        owned_counts=owned_counts,
        detail={
            "scope": "strict_report_visible_surfaces_only",
            "closure_dimensions": closure_dimensions,
            "does_not_claim": does_not_claim,
            "safe_default": "treat_open_certificate_as_accounting_gap_not_replay_failure",
            "missing_required_certificates": missing_required_certificates,
        },
    )
    return certificate.to_dict()


def finland_strict_report_ownership_closure_report(
    certificate: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap one strict-report closure certificate in a passive evidence report."""

    cert = OwnershipClosureCoverage(
        certificate_id=str(certificate.get("certificate_id") or ""),
        corpus_slice_id=str(certificate.get("corpus_slice_id") or ""),
        source_bundle_hash=str(certificate.get("source_bundle_hash") or ""),
        profile_id=str(certificate.get("profile_id") or ""),
        interpretation_policy_id=str(certificate.get("interpretation_policy_id") or ""),
        graph_snapshot_hash=str(certificate.get("graph_snapshot_hash") or ""),
        phase_report_ids=mapping_str_str(certificate.get("phase_report_ids")),
        closed=bool(certificate.get("closed")),
        failed_gates=tuple(str(item) for item in certificate.get("failed_gates", ()) or ()),
        unowned_counts=mapping_str_int(certificate.get("unowned_counts")),
        owned_counts=mapping_str_int(certificate.get("owned_counts")),
        detail=mapping_or_empty(certificate.get("detail")),
    )
    return ownership_closure_evidence_report(
        cert,
        jurisdiction="fi",
        report_kind="finland_strict_report_ownership_closure",
    ).to_dict()

def _has_candidate_set(
    rows: tuple[Mapping[str, Any], ...],
    candidate_set_kind: str,
) -> bool:
    return any(str(row.get("candidate_set_kind") or "") == candidate_set_kind for row in rows)


def _candidate_set_complete(
    rows: tuple[Mapping[str, Any], ...],
    candidate_set_kind: str,
) -> bool:
    return any(
        str(row.get("candidate_set_kind") or "") == candidate_set_kind
        and str(row.get("completeness_status") or "") == CANDIDATE_SET_COMPLETE
        for row in rows
    )


def _closure_certificate_requirements(
    rows: tuple[Mapping[str, Any], ...],
    *,
    candidate_set_kind: str,
    missing_certificate: str,
    incomplete_certificate: str,
) -> tuple[str, ...]:
    if not _has_candidate_set(rows, candidate_set_kind):
        return (missing_certificate,)
    if not _candidate_set_complete(rows, candidate_set_kind):
        return (incomplete_certificate,)
    return ()


def _strict_report_candidate_set_authorization_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    candidate_set_kind = str(row.get("candidate_set_kind") or "")
    scope_id = str(row.get("scope_id") or "")
    completeness_status = str(row.get("completeness_status") or "")
    if not candidate_set_kind or not scope_id or not completeness_status:
        return None
    return (candidate_set_kind, scope_id, completeness_status)


def _strict_report_candidate_set_authorization_row_id(
    *,
    candidate_set_kind: str,
    scope_id: str,
    completeness_status: str,
) -> str:
    digest = _strict_report_digest(
        "candidate-set-authorization",
        {
            "candidate_set_kind": candidate_set_kind,
            "scope_id": scope_id,
            "completeness_status": completeness_status,
        },
    ).split(":", 1)[1][:16]
    return f"fi:strict-report-candidate-set-authorization:{digest}"


def _strict_report_candidate_set_frontier_key(
    row: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    direct = _strict_report_candidate_set_authorization_key(row)
    if direct is not None:
        return direct
    target_witness = row.get("target_witness")
    if not isinstance(target_witness, Mapping):
        return None
    return _strict_report_candidate_set_authorization_key(target_witness)


def _strict_report_candidate_set_frontier_work_item_id(
    *,
    statute_id: str,
    candidate_set_kind: str,
    scope_id: str,
    completeness_status: str,
    index: int,
) -> str:
    digest = _strict_report_digest(
        "candidate-set-frontier",
        {
            "statute_id": statute_id,
            "candidate_set_kind": candidate_set_kind,
            "scope_id": scope_id,
            "completeness_status": completeness_status,
            "index": index,
        },
    ).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:candidate-set-frontier:{digest}"


def _candidate_set_frontier_required_claim_kind(candidate_set_kind: str) -> str:
    return _candidate_set_frontier_profile(candidate_set_kind)["required_claim_kind"]


def _candidate_set_frontier_profile(candidate_set_kind: str) -> dict[str, Any]:
    if candidate_set_kind == "fi_sparse_payload_slot_assignment":
        return {
            "frontier_family": "fi_sparse_payload_slot_assignment_manual_compilation_frontier",
            "candidate_operation_family": "fi_sparse_slot_payload_resolution",
            "guidance_refs": (
                "lawvm_fi_sparse_slot_phase_gate_review",
                "notes_internal/FINLAND_XML_MANUAL_COMPILATION_FRONTIER_AUDIT_2026-06-07.md",
            ),
            "required_claim_kind": "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
            "required_validator_checks": (
                "validate_sparse_slot_resolution_claim",
                "validate_target_uniqueness_before_replay_promotion",
                "validate_payload_identity_before_replay_promotion",
                "validate_rejected_candidate_accounting",
                "validate_mutation_boundary_before_replay_promotion",
                "validate_no_replay_promotion_from_partial_candidate_set",
            ),
            "safe_default": "keep_sparse_slot_candidate_set_non_executable_until_phase_replay_gate_exists",
            "forbidden_shortcuts": (
                "sparse_slot_candidate_set_as_target_uniqueness_proof",
                "sparse_slot_binding_as_payload_identity_proof",
                "manual_claim_as_phase_replay_gate",
            ),
            "does_not_claim": (
                "target_uniqueness",
                "payload_identity",
                "rejected_candidate_exhaustiveness",
                "mutation_boundary_proof",
            ),
        }
    if candidate_set_kind in (
        "fi_strict_report_source_lineage_units",
        "fi_strict_report_source_unit_enumeration",
    ):
        claim_kind = "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE"
    elif candidate_set_kind in (
        "fi_strict_report_visible_operation_rows",
        "fi_strict_report_operation_cue_coverage",
    ):
        claim_kind = "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE"
    else:
        claim_kind = "candidate_set_completion_certificate"
    return {
        "frontier_family": f"fi_{kind_slug(candidate_set_kind)}_coverage_gap",
        "candidate_operation_family": "candidate_set_coverage_completion",
        "guidance_refs": ("lawvm_candidate_set_completion_review",),
        "required_claim_kind": claim_kind,
        "required_validator_checks": (
            "validate_candidate_set_completion_certificate",
            "validate_no_replay_promotion_from_partial_candidate_set",
        ),
        "safe_default": "keep_candidate_set_gap_open_until_completion_certificate_exists",
        "forbidden_shortcuts": (),
        "does_not_claim": (),
    }


def _strict_report_canonical_potential_operation(
    *,
    statute_id: str,
    visible_id: str,
    visible_index: int,
    synthesized: bool,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "statute_id": statute_id,
        "visible_index": visible_index,
        "projection_only": True,
    }
    if synthesized:
        detail["op_id_synthesized"] = True
    potential_operation_id = (
        visible_id if visible_id.startswith("canonical-op:") else f"canonical-op:{visible_id}"
    )
    return PotentialOperation(
        potential_operation_id=potential_operation_id,
        jurisdiction="fi",
        source_artifact_id=f"fi:{statute_id}:strict-report-canonical-ops",
        source_unit_id=visible_id,
        owner_phase="canonical_operation_lowering",
        classification=POTENTIAL_OPERATION_COMPILED,
        operation_family="fi_canonical_operation",
        refs=(visible_id,),
        required_proofs=(
            "source_text_operation_cue_detector",
            "source_unit_enumeration_certificate",
            "operation_cue_exhaustiveness_certificate",
        ),
        safe_default="do_not_treat_compiled_visible_ops_as_source_cue_exhaustiveness",
        detail=detail,
    ).to_dict()


def _potential_operation_candidate_ids(
    potential_operations: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    return tuple(
        str(row.get("potential_operation_id") or "")
        for row in potential_operations
        if str(row.get("potential_operation_id") or "")
    )


def _visible_operation_candidate_ids(
    *,
    canonical_op_ids: tuple[str, ...],
    canonical_count: int,
    failed_ops: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    ids = [
        op_id or f"canonical-op:{index + 1}"
        for index, op_id in enumerate(canonical_op_ids[:canonical_count])
    ]
    if len(ids) < canonical_count:
        ids.extend(f"canonical-op:{index + 1}" for index in range(len(ids), canonical_count))
    ids.extend(_failed_operation_candidate_id(index=index, row=row) for index, row in enumerate(failed_ops))
    return tuple(ids)


def _failed_operation_candidate_id(*, index: int, row: Mapping[str, Any]) -> str:
    op_id = str(row.get("op_id") or "")
    if op_id:
        return f"failed-op:{op_id}"
    digest_payload = {
        "index": index,
        "amendment_id": row.get("amendment_id") or row.get("source") or "",
        "description": row.get("description") or "",
        "reason": row.get("reason") or "",
        "reason_code": row.get("reason_code") or "",
        "target_unit_kind": row.get("target_unit_kind") or row.get("target_kind") or "",
        "target_section": row.get("target_section") or "",
        "target_chapter": row.get("target_chapter") or "",
        "target_part": row.get("target_part") or "",
    }
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"failed-op:{digest}"


def _source_lineage_candidate_ids(
    source_lineage_witnesses: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    ids: list[str] = []
    for index, witness in enumerate(source_lineage_witnesses):
        artifact_id = str(witness.get("artifact_id") or "")
        source_unit_id = str(witness.get("source_unit_id") or "")
        if artifact_id or source_unit_id:
            ids.append(f"source-lineage:{artifact_id or 'unknown'}:{source_unit_id or index + 1}")
            continue
        digest = hashlib.sha256(
            json.dumps(witness, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        ids.append(f"source-lineage:{digest}")
    return tuple(ids)


def _source_unit_coverage_candidate_ids(
    source_unit_coverages: tuple[Mapping[str, Any], ...],
) -> tuple[str, ...]:
    return tuple(
        str(row.get("coverage_id") or "")
        for row in source_unit_coverages
        if str(row.get("coverage_id") or "")
    )


def _strict_report_source_unit_coverage_id(
    *,
    statute_id: str,
    artifact_id: str,
    source_unit_id: str,
    index: int,
) -> str:
    digest = _strict_report_digest(
        "source-unit-coverage",
        {
            "statute_id": statute_id,
            "artifact_id": artifact_id,
            "source_unit_id": source_unit_id,
            "index": index,
        },
    ).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:source-unit-coverage:{digest}"


def _ops_count(payload: Mapping[str, Any], name: str) -> int:
    ops = payload.get("ops")
    if not isinstance(ops, Mapping):
        return 0
    value = ops.get(name)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _strict_report_digest(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(prefix.encode('utf-8') + b':' + encoded).hexdigest()}"


def _strict_report_id(prefix: str, statute_id: str, payload: Any) -> str:
    digest = _strict_report_digest(prefix, payload).split(":", 1)[1][:16]
    return f"fi:{statute_id or 'unknown'}:{prefix}:{digest}"


def _strict_report_closure_graph_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    keys = (
        "statute_id",
        "profile",
        "ops",
        "canonical_op_ids",
        "source_completeness",
        "source_pathologies",
        "source_pathology_execution_authorizations",
        "source_pathology_frontier_work_items",
        "failed_operation_execution_authorizations",
        "failed_operation_frontier_work_items",
        "potential_operations",
        "source_unit_coverages",
        "sparse_slot_candidate_set_coverages",
        "source_lineage_source_witnesses",
        "agreement_residuals",
        "mutation_boundary_proofs",
        "strict_report_candidate_set_coverages",
        "strict_report_candidate_set_execution_authorizations",
        "strict_report_candidate_set_frontier_work_items",
        "strict_fail_reasons",
        "projection_rows",
        "failed_ops",
    )
    return {key: payload.get(key) for key in keys if key in payload}

__all__ = [
    "finland_strict_report_candidate_set_coverages",
    "finland_strict_report_candidate_set_execution_authorizations",
    "finland_strict_report_candidate_set_frontier_work_items",
    "finland_strict_report_ownership_closure_coverage",
    "finland_strict_report_ownership_closure_report",
    "finland_strict_report_potential_operation_rows",
    "finland_strict_report_source_unit_coverage_rows",
]

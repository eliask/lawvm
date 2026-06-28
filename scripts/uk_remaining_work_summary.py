#!/usr/bin/env python3
"""Summarize remaining UK work from a broad-baseline evidence report.

This is a work-selection surface, not replay authority. It consumes the
EvidenceSurfaceReport emitted by ``scripts/uk_broad_baseline.py --out-report``
and groups already-classified residuals by the proof boundary they still lack.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lawvm.core.candidate_set_coverage import CandidateSetCoverage
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.quirks_disposition import QuirksDisposition


_DEFAULT_REFERENCE_BUCKET = "high_fidelity_after_grounding"
_DEFAULT_CORE_POLISH_THRESHOLD = 99.5
_EXPECTED_JURISDICTION = "uk"
_EXPECTED_INPUT_REPORT_KIND = "uk_broad_baseline_agreement_report"
_EXPECTED_INPUT_SCHEMA = "lawvm.uk_broad_baseline_agreement_report.v1"
_EXPECTED_EFFECTIVE_ORACLE_REPORT_KIND = "uk_effective_oracle_review"
_EXPECTED_EFFECTIVE_ORACLE_SCHEMA = "lawvm.uk_effective_oracle_review.v1"
_PLAUSIBLE_EFFECTIVE_ORACLE_STATUSES = frozenset(
    {"plausible_true_divergence", "partially_plausible_true_divergence"}
)


@dataclass(frozen=True)
class UKRemainingWorkLane:
    lane_id: str
    priority_rank: int
    owner_phase: str
    work_kind: str
    row_count: int
    scored_count: int
    source_frontier_count: int
    mean_aligned: float | None
    loss_points_vs_reference: float | None
    triage_bucket_counts: dict[str, int]
    source_status_pair_counts: dict[str, int]
    missing_proof_counts: dict[str, int]
    sample_statutes: tuple[str, ...]
    next_action: str
    safe_default: str
    forbidden_shortcuts: tuple[str, ...]


@dataclass(frozen=True)
class UKRemainingWorkItem:
    work_item_id: str
    statute_id: str
    lane_id: str
    priority_rank: int
    owner_phase: str
    work_kind: str
    status: str
    executable: bool
    replay_authorized: bool
    triage_bucket: str
    score_status: str
    aligned: float | None
    n_replay: int
    n_oracle: int
    n_only_in_replayed: int
    n_only_in_oracle: int
    source_chain_frontier_reasons: tuple[str, ...]
    missing_proofs: tuple[str, ...]
    manual_frontier_status_counts: Mapping[str, int]
    manual_frontier_rule_counts: Mapping[str, int]
    manual_frontier_work_item_family_counts: Mapping[str, int]
    compile_rejection_rule_counts: Mapping[str, int]
    blocking_compile_rejection_rule_counts: Mapping[str, int]
    mutation_boundary_proof_status_counts: Mapping[str, int]
    mutation_boundary_proof_rule_counts: Mapping[str, int]
    mutation_boundary_result_code_counts: Mapping[str, int]
    mutation_boundary_unexplained_report_count: int
    mutation_boundary_unexplained_path_count: int
    base_source_status: str
    base_source_locator: str
    oracle_source_status: str
    oracle_source_locator: str
    replay_only_eid_samples: tuple[str, ...]
    oracle_only_eid_samples: tuple[str, ...]
    next_action: str
    safe_default: str
    forbidden_shortcuts: tuple[str, ...]
    execution_authorization: Mapping[str, Any]
    frontier_work_item: Mapping[str, Any]
    candidate_set_coverage: Mapping[str, Any]


@dataclass(frozen=True)
class _LaneSpec:
    lane_id: str
    priority_rank: int
    owner_phase: str
    work_kind: str
    next_action: str
    forbidden_shortcuts: tuple[str, ...]


_LANE_SPECS = {
    "unclassified_or_gate_failure": _LaneSpec(
        lane_id="unclassified_or_gate_failure",
        priority_rank=100,
        owner_phase="phase_owner_required",
        work_kind="classification_or_gate_repair",
        next_action=(
            "Inspect completion-gate failures or unknown triage buckets before "
            "doing any score-oriented work."
        ),
        forbidden_shortcuts=(
            "unknown_bucket_as_manual_frontier",
            "completion_gate_failure_as_score_noise",
        ),
    ),
    "manual_compilation_frontier": _LaneSpec(
        lane_id="manual_compilation_frontier",
        priority_rank=90,
        owner_phase="typed_elaboration",
        work_kind="manual_claim_or_deterministic_proof_family",
        next_action=(
            "Either add an owned manual claim or prove target, payload, and "
            "mutation-boundary identity for a repeatable deterministic family."
        ),
        forbidden_shortcuts=(
            "manual_frontier_as_replay_authorization",
            "oracle_shape_as_payload_identity",
            "source_or_target_over_promotion",
        ),
    ),
    "effect_source_footing_gap": _LaneSpec(
        lane_id="effect_source_footing_gap",
        priority_rank=80,
        owner_phase="effect_metadata_frontend",
        work_kind="effect_or_source_identity_recovery",
        next_action=(
            "Recover or classify missing effect/source identity; do not compile "
            "oracle additions without a source-chain proof."
        ),
        forbidden_shortcuts=(
            "oracle_addition_as_effect_identity",
            "effect_absence_as_replay_permission",
            "source_or_target_over_promotion",
        ),
    ),
    "source_footing_gap": _LaneSpec(
        lane_id="source_footing_gap",
        priority_rank=70,
        owner_phase="affecting_source_extraction",
        work_kind="acquisition_or_metadata_only_source_frontier",
        next_action=(
            "Acquire or classify the source surface; metadata-only or too-small "
            "XML remains non-executable."
        ),
        forbidden_shortcuts=(
            "metadata_only_xml_as_executable_text",
            "source_frontier_as_replay_authorization",
        ),
    ),
    "metadata_only_source_pathology_frontier": _LaneSpec(
        lane_id="metadata_only_source_pathology_frontier",
        priority_rank=35,
        owner_phase="affecting_source_extraction",
        work_kind="official_metadata_only_source_pathology",
        next_action=(
            "Record as a digest-backed official source pathology unless a "
            "body-bearing authoritative source or an explicit PDF/manual-import "
            "policy is introduced."
        ),
        forbidden_shortcuts=(
            "metadata_only_xml_as_executable_text",
            "metadata_only_source_as_no_law",
            "pdf_or_oracle_text_as_source_without_claim",
        ),
    ),
    "oracle_suspect_review": _LaneSpec(
        lane_id="oracle_suspect_review",
        priority_rank=60,
        owner_phase="compare_oracle_classification",
        work_kind="high_confidence_consolidation_error_packet",
        next_action=(
            "Package source-backed replay and retained-oracle witnesses for "
            "external consolidation-error review."
        ),
        forbidden_shortcuts=(
            "oracle_suspect_as_source_truth",
            "oracle_error_candidate_as_replay_authorization",
        ),
    ),
    "effective_oracle_review_frontier": _LaneSpec(
        lane_id="effective_oracle_review_frontier",
        priority_rank=55,
        owner_phase="compare_oracle_classification",
        work_kind="effective_oracle_surface_review",
        next_action=(
            "Keep page-declared-current refutations out of external "
            "consolidation-error packets; fetch missing dated current XML for "
            "insufficient witnesses."
        ),
        forbidden_shortcuts=(
            "effective_oracle_witness_as_replay_authority",
            "current_page_xml_as_source_truth",
            "refuted_oracle_suspect_as_external_error_packet",
        ),
    ),
    "canonical_or_temporal_frontier": _LaneSpec(
        lane_id="canonical_or_temporal_frontier",
        priority_rank=50,
        owner_phase="canonical_op_compilation",
        work_kind="canonical_operation_or_temporal_proof_gap",
        next_action=(
            "Add a narrow compiler or temporal applicability proof, or keep the "
            "row non-executable with its missing proof explicit."
        ),
        forbidden_shortcuts=(
            "unsupported_effect_as_noop",
            "temporal_gap_as_commenced_state",
            "candidate_as_replay_authorization",
        ),
    ),
    "temporal_commencement_frontier": _LaneSpec(
        lane_id="temporal_commencement_frontier",
        priority_rank=48,
        owner_phase="effect_metadata_frontend",
        work_kind="temporal_commencement_materialization_proof_gap",
        next_action=(
            "Prove commencement date, extent, and applicability before any "
            "commenced-state materialization; otherwise keep the row as a "
            "temporal frontier."
        ),
        forbidden_shortcuts=(
            "undated_commencement_as_commenced_state",
            "deterministic_effect_support_as_temporal_materialization",
            "current_oracle_shape_as_commencement_proof",
        ),
    ),
    "non_textual_or_out_of_scope_effect_frontier": _LaneSpec(
        lane_id="non_textual_or_out_of_scope_effect_frontier",
        priority_rank=45,
        owner_phase="effect_metadata_frontend",
        work_kind="non_textual_or_out_of_scope_effect_visibility",
        next_action=(
            "Keep commencement, non-textual, and out-of-scope effect rows out of "
            "text/tree replay unless a separate temporal/applicability "
            "materialization proof is introduced."
        ),
        forbidden_shortcuts=(
            "commencement_effect_as_text_mutation",
            "out_of_scope_effect_as_noop_success",
            "oracle_shape_as_temporal_materialization",
        ),
    ),
    "oracle_topology_granularity_residual": _LaneSpec(
        lane_id="oracle_topology_granularity_residual",
        priority_rank=40,
        owner_phase="compare_oracle_classification",
        work_kind="oracle_topology_or_granularity_classification",
        next_action=(
            "Improve comparison/oracle topology classification; do not change "
            "replay structure merely to match an editorial projection."
        ),
        forbidden_shortcuts=(
            "oracle_topology_as_source_structure",
            "granularity_mismatch_as_replay_mutation",
        ),
    ),
    "non_commensurable_oracle_surface": _LaneSpec(
        lane_id="non_commensurable_oracle_surface",
        priority_rank=30,
        owner_phase="compare_oracle_classification",
        work_kind="oracle_surface_commensurability_gap",
        next_action=(
            "Separate non-commensurable zero-oracle surfaces from scored replay "
            "quality; never delete replay state to improve this score."
        ),
        forbidden_shortcuts=(
            "zero_oracle_as_over_retention_bug",
            "oracle_absence_as_repeal_authority",
        ),
    ),
    "comparison_core_polish": _LaneSpec(
        lane_id="comparison_core_polish",
        priority_rank=20,
        owner_phase="compare_oracle_classification",
        work_kind="high_fidelity_core_residual_polish",
        next_action=(
            "Inspect only after frontier lanes are stable; this is low-volume "
            "core polish, not evidence of broad deterministic incompleteness."
        ),
        forbidden_shortcuts=(
            "small_score_delta_as_semantic_bug",
            "oracle_overlap_as_source_truth",
        ),
    ),
}

_TRIAGE_LANES = {
    "manual_compile_frontier_residual": "manual_compilation_frontier",
    "effect_feed_absent_frontier": "effect_source_footing_gap",
    "no_effect_rows_frontier": "effect_source_footing_gap",
    "oracle_expansion_without_effects": "effect_source_footing_gap",
    "oracle_addition_source_chain_frontier": "effect_source_footing_gap",
    "retained_repeal_oracle_branch": "oracle_suspect_review",
    "source_backed_temporal_recovery_oracle_residual": (
        "effective_oracle_review_frontier"
    ),
    "nonreplay_effect_frontier": "canonical_or_temporal_frontier",
    "no_compiled_ops_frontier": "canonical_or_temporal_frontier",
    "temporal_commencement_frontier": "temporal_commencement_frontier",
    "bounded_low_volume_residual": "oracle_topology_granularity_residual",
    "body_oracle_first_paragraph_sectionization_residual": (
        "oracle_topology_granularity_residual"
    ),
    "body_oracle_collapsed_range_granularity_residual": (
        "oracle_topology_granularity_residual"
    ),
    "body_nested_list_oracle_granularity_residual": (
        "oracle_topology_granularity_residual"
    ),
    "retained_eu_schedule_oracle_granularity_residual": (
        "oracle_topology_granularity_residual"
    ),
    "zero_oracle_retention": "non_commensurable_oracle_surface",
}


def load_remaining_work(
    report_path: Path,
    *,
    reference_bucket: str = _DEFAULT_REFERENCE_BUCKET,
    core_polish_threshold: float = _DEFAULT_CORE_POLISH_THRESHOLD,
    effective_oracle_review_path: Path | None = None,
    include_items: bool = False,
    item_lane_ids: frozenset[str] = frozenset(),
    item_limit: int = 0,
    item_limit_per_lane: int = 0,
) -> dict[str, Any]:
    """Load a UK broad-baseline report and return remaining-work lanes."""

    unknown_lane_ids = item_lane_ids - frozenset(_LANE_SPECS)
    if unknown_lane_ids:
        raise ValueError(f"unknown lane id(s): {', '.join(sorted(unknown_lane_ids))}")

    report = json.loads(report_path.read_text())
    _validate_input_report(report, report_path)
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{report_path} does not contain a broad-baseline row list")

    effective_reviews = _load_effective_oracle_reviews(effective_oracle_review_path)
    typed_rows = [
        _with_effective_oracle_review(row, effective_reviews)
        for row in rows
        if isinstance(row, Mapping)
    ]
    scored_rows = [
        row
        for row in typed_rows
        if str(row.get("score_status") or "") == "scored"
        and _float_or_none(row.get("aligned")) is not None
    ]
    source_frontier_rows = [
        row
        for row in typed_rows
        if str(row.get("score_status") or "") == "source_frontier"
    ]
    reference_score = _reference_score(scored_rows, reference_bucket)
    lane_rows: dict[str, list[Mapping[str, Any]]] = {}
    unknown_buckets: set[str] = set()

    for row in typed_rows:
        lane_id = _lane_for_row(row, core_polish_threshold=core_polish_threshold)
        if lane_id is None:
            continue
        if lane_id == "unclassified_or_gate_failure":
            bucket = str(row.get("triage_bucket") or "")
            if bucket:
                unknown_buckets.add(bucket)
        lane_rows.setdefault(lane_id, []).append(row)

    lanes = [
        _summarize_lane(
            _LANE_SPECS[lane_id],
            lane_group,
            reference_score=reference_score,
            scored_count=len(scored_rows),
        )
        for lane_id, lane_group in lane_rows.items()
    ]
    lanes.sort(key=lambda lane: (-lane.priority_rank, -lane.row_count, lane.lane_id))

    summary = _summary(
        report,
        typed_rows,
        scored_rows,
        source_frontier_rows,
        lanes,
        reference_bucket=reference_bucket,
        reference_score=reference_score,
        unknown_buckets=unknown_buckets,
    )
    payload: dict[str, Any] = {
        "report_kind": "uk_remaining_work_summary.v1",
        "truth_claim": "uk_remaining_work_summary_report_not_source_truth",
        "safe_default": "use_for_work_selection_not_replay_authorization",
        "forbidden_shortcuts": [
            "work_lane_as_replay_authorization",
            "oracle_score_as_source_truth",
            "frontier_class_as_manual_claim",
            "source_or_target_over_promotion",
        ],
        "summary": summary,
        "lanes": [asdict(lane) for lane in lanes],
    }
    if include_items:
        items = [
            asdict(item)
            for item in _remaining_work_items(
                lane_rows,
                lane_ids=item_lane_ids,
                limit=item_limit,
                limit_per_lane=item_limit_per_lane,
            )
        ]
        payload["items"] = items
        payload["summary"].update(
            _item_export_summary(items, lane_rows, lane_ids=item_lane_ids)
        )
        payload["summary"]["item_lane_filter"] = sorted(item_lane_ids)
        payload["summary"]["item_count"] = len(items)
        payload["summary"]["item_limit_per_lane"] = item_limit_per_lane
    return payload


def _validate_input_report(report: Mapping[str, Any], report_path: Path) -> None:
    jurisdiction = report.get("jurisdiction")
    report_kind = report.get("report_kind")
    schema = report.get("schema")
    if jurisdiction != _EXPECTED_JURISDICTION:
        raise ValueError(
            f"{report_path} is not a UK broad-baseline report: "
            f"jurisdiction={jurisdiction!r}"
        )
    if report_kind != _EXPECTED_INPUT_REPORT_KIND:
        raise ValueError(
            f"{report_path} is not a UK broad-baseline report: "
            f"report_kind={report_kind!r}"
        )
    if schema != _EXPECTED_INPUT_SCHEMA:
        raise ValueError(
            f"{report_path} has unsupported schema: {schema!r}"
        )


def _lane_for_row(
    row: Mapping[str, Any],
    *,
    core_polish_threshold: float,
) -> str | None:
    triage_bucket = str(row.get("triage_bucket") or "")
    score_status = str(row.get("score_status") or "")
    if score_status == "source_frontier" or triage_bucket.startswith(
        "source_frontier:"
    ):
        if _is_metadata_only_source_frontier(row):
            return "metadata_only_source_pathology_frontier"
        return "source_footing_gap"
    if (
        triage_bucket in {"nonreplay_effect_frontier", "no_compiled_ops_frontier"}
        and _only_non_textual_or_out_of_scope_effects(row)
    ):
        return "non_textual_or_out_of_scope_effect_frontier"
    if triage_bucket in {"nonreplay_effect_frontier", "no_compiled_ops_frontier"}:
        if _has_manual_compile_frontier(row):
            return "manual_compilation_frontier"
        if _has_effect_source_footing_gap(row):
            return "effect_source_footing_gap"
    if (
        triage_bucket == "retained_repeal_oracle_branch"
        and _effective_oracle_status(row)
        and _effective_oracle_status(row) not in _PLAUSIBLE_EFFECTIVE_ORACLE_STATUSES
    ):
        return "effective_oracle_review_frontier"
    if triage_bucket in _TRIAGE_LANES:
        return _TRIAGE_LANES[triage_bucket]
    if triage_bucket == _DEFAULT_REFERENCE_BUCKET:
        aligned = _float_or_none(row.get("aligned"))
        if aligned is not None and aligned < core_polish_threshold:
            return "comparison_core_polish"
        return None
    if score_status == "scored":
        return "unclassified_or_gate_failure"
    return None


def _summarize_lane(
    spec: _LaneSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    reference_score: float,
    scored_count: int,
) -> UKRemainingWorkLane:
    scored_rows = [
        row
        for row in rows
        if str(row.get("score_status") or "") == "scored"
        and _float_or_none(row.get("aligned")) is not None
    ]
    source_frontier_count = sum(
        1
        for row in rows
        if str(row.get("score_status") or "") == "source_frontier"
    )
    mean_aligned = _mean_aligned(scored_rows)
    loss_points = None
    if mean_aligned is not None and scored_count > 0:
        loss_points = len(scored_rows) * (reference_score - mean_aligned) / scored_count
    return UKRemainingWorkLane(
        lane_id=spec.lane_id,
        priority_rank=spec.priority_rank,
        owner_phase=_dominant_owner_phase(rows) or spec.owner_phase,
        work_kind=spec.work_kind,
        row_count=len(rows),
        scored_count=len(scored_rows),
        source_frontier_count=source_frontier_count,
        mean_aligned=mean_aligned,
        loss_points_vs_reference=loss_points,
        triage_bucket_counts=_counter_dict(
            str(row.get("triage_bucket") or "unknown") for row in rows
        ),
        source_status_pair_counts=_counter_dict(
            pair for row in rows for pair in [_source_status_pair(row)] if pair
        ),
        missing_proof_counts=_missing_proof_counts(rows),
        sample_statutes=_sample_statutes(rows),
        next_action=spec.next_action,
        safe_default="classify_or_queue_without_replay_promotion",
        forbidden_shortcuts=spec.forbidden_shortcuts,
    )


def _remaining_work_items(
    lane_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    lane_ids: frozenset[str],
    limit: int,
    limit_per_lane: int,
) -> list[UKRemainingWorkItem]:
    selected_lane_ids = lane_ids or frozenset(lane_rows)
    items: list[UKRemainingWorkItem] = []
    for lane_id in sorted(
        selected_lane_ids & frozenset(lane_rows),
        key=lambda item: (-_LANE_SPECS[item].priority_rank, item),
    ):
        spec = _LANE_SPECS[lane_id]
        lane_count = 0
        for row in sorted(lane_rows[lane_id], key=_sample_sort_key):
            if limit_per_lane > 0 and lane_count >= limit_per_lane:
                break
            items.append(_remaining_work_item(spec, row))
            lane_count += 1
            if limit > 0 and len(items) >= limit:
                return items
    return items


def _item_export_summary(
    items: Sequence[Mapping[str, Any]],
    lane_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    lane_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    exported_lane_counts = _counter_dict(str(item.get("lane_id") or "") for item in items)
    selected_lane_ids = lane_ids or frozenset(lane_rows)
    selected_lane_ids &= frozenset(lane_rows)
    exported_lane_ids = set(exported_lane_counts)
    expected_row_count = sum(len(lane_rows[lane_id]) for lane_id in selected_lane_ids)
    exported_row_count = sum(
        count
        for lane_id, count in exported_lane_counts.items()
        if lane_id in selected_lane_ids
    )
    unexported_row_count = max(0, expected_row_count - exported_row_count)
    safety_gap_counts = {
        key: count
        for key, count in {
            "executable_items": sum(1 for item in items if item.get("executable") is True),
            "replay_authorized_items": sum(
                1 for item in items if item.get("replay_authorized") is True
            ),
            "missing_execution_authorization": sum(
                1 for item in items if not item.get("execution_authorization")
            ),
            "missing_frontier_work_item": sum(
                1 for item in items if not item.get("frontier_work_item")
            ),
            "missing_candidate_set_coverage": sum(
                1 for item in items if not item.get("candidate_set_coverage")
            ),
        }.items()
        if count
    }
    return {
        "item_exported_lane_counts": exported_lane_counts,
        "item_exported_lane_count": len(exported_lane_ids),
        "item_expected_row_count": expected_row_count,
        "item_exported_row_count": exported_row_count,
        "item_unexported_row_count": unexported_row_count,
        "item_fully_exported": unexported_row_count == 0,
        "item_unexported_lane_ids": sorted(selected_lane_ids - exported_lane_ids),
        "item_authorization_status_counts": _counter_dict(
            str(item.get("execution_authorization", {}).get("authorization_status") or "")
            for item in items
        ),
        "item_safety_gap_counts": safety_gap_counts,
    }


def _remaining_work_item(
    spec: _LaneSpec,
    row: Mapping[str, Any],
) -> UKRemainingWorkItem:
    statute_id = str(row.get("statute_id") or "")
    residual = _primary_agreement_residual(row)
    owner_phase = str(residual.get("owner_phase") or spec.owner_phase)
    missing_proofs = _missing_proofs_for_row(row)
    work_item_id = f"uk-remaining:{spec.lane_id}:{statute_id}"
    source_chain_frontier_reasons = _string_tuple(
        row.get("source_chain_frontier_reasons")
    )
    evidence_counters = _remaining_work_item_evidence_counters(row)
    replay_only_eid_samples = _string_tuple(row.get("replay_only_eid_samples"))
    oracle_only_eid_samples = _string_tuple(row.get("oracle_only_eid_samples"))
    return UKRemainingWorkItem(
        work_item_id=work_item_id,
        statute_id=statute_id,
        lane_id=spec.lane_id,
        priority_rank=spec.priority_rank,
        owner_phase=owner_phase,
        work_kind=spec.work_kind,
        status="non_executable_work_item",
        executable=False,
        replay_authorized=False,
        triage_bucket=str(row.get("triage_bucket") or ""),
        score_status=str(row.get("score_status") or ""),
        aligned=_float_or_none(row.get("aligned")),
        n_replay=_int(row.get("n_replay")),
        n_oracle=_int(row.get("n_oracle")),
        n_only_in_replayed=_int(row.get("n_only_in_replayed")),
        n_only_in_oracle=_int(row.get("n_only_in_oracle")),
        source_chain_frontier_reasons=source_chain_frontier_reasons,
        missing_proofs=missing_proofs,
        manual_frontier_status_counts=evidence_counters[
            "manual_frontier_status_counts"
        ],
        manual_frontier_rule_counts=evidence_counters["manual_frontier_rule_counts"],
        manual_frontier_work_item_family_counts=evidence_counters[
            "manual_frontier_work_item_family_counts"
        ],
        compile_rejection_rule_counts=evidence_counters[
            "compile_rejection_rule_counts"
        ],
        blocking_compile_rejection_rule_counts=evidence_counters[
            "blocking_compile_rejection_rule_counts"
        ],
        mutation_boundary_proof_status_counts=evidence_counters[
            "mutation_boundary_proof_status_counts"
        ],
        mutation_boundary_proof_rule_counts=evidence_counters[
            "mutation_boundary_proof_rule_counts"
        ],
        mutation_boundary_result_code_counts=evidence_counters[
            "mutation_boundary_result_code_counts"
        ],
        mutation_boundary_unexplained_report_count=_int(
            row.get("n_mutation_boundary_unexplained_reports")
        ),
        mutation_boundary_unexplained_path_count=_int(
            row.get("n_mutation_boundary_unexplained_paths")
        ),
        base_source_status=str(row.get("base_source_status") or ""),
        base_source_locator=str(row.get("base_source_locator") or ""),
        oracle_source_status=str(row.get("oracle_source_status") or ""),
        oracle_source_locator=str(row.get("oracle_source_locator") or ""),
        replay_only_eid_samples=replay_only_eid_samples,
        oracle_only_eid_samples=oracle_only_eid_samples,
        next_action=spec.next_action,
        safe_default="classify_or_queue_without_replay_promotion",
        forbidden_shortcuts=spec.forbidden_shortcuts,
        execution_authorization=_execution_authorization(
            spec=spec,
            owner_phase=owner_phase,
            missing_proofs=missing_proofs,
            triage_bucket=str(row.get("triage_bucket") or ""),
        ).to_dict(),
        frontier_work_item=_frontier_work_item(
            spec=spec,
            row=row,
            statute_id=statute_id,
            work_item_id=work_item_id,
            owner_phase=owner_phase,
            missing_proofs=missing_proofs,
            evidence_counters=evidence_counters,
            source_chain_frontier_reasons=source_chain_frontier_reasons,
            replay_only_eid_samples=replay_only_eid_samples,
            oracle_only_eid_samples=oracle_only_eid_samples,
        ).to_dict(),
        candidate_set_coverage=_candidate_set_coverage(
            spec=spec,
            row=row,
            statute_id=statute_id,
            missing_proofs=missing_proofs,
            replay_only_eid_samples=replay_only_eid_samples,
            oracle_only_eid_samples=oracle_only_eid_samples,
        ).to_dict(),
    )


def _execution_authorization(
    *,
    spec: _LaneSpec,
    owner_phase: str,
    missing_proofs: tuple[str, ...],
    triage_bucket: str,
) -> ExecutionAuthorization:
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="non_executable_work_item",
        authorization_rule_id=f"uk_remaining_work_{spec.lane_id}_non_executable",
        owner_phase=owner_phase,
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        validator_status="remaining_work_summary_projection",
        required_proofs=missing_proofs or ("frontier_review",),
        safe_default="classify_or_queue_without_replay_promotion",
        forbidden_shortcuts=spec.forbidden_shortcuts,
        detail={
            "lane_id": spec.lane_id,
            "triage_bucket": triage_bucket,
            "work_kind": spec.work_kind,
        },
    )


def _frontier_work_item(
    *,
    spec: _LaneSpec,
    row: Mapping[str, Any],
    statute_id: str,
    work_item_id: str,
    owner_phase: str,
    missing_proofs: tuple[str, ...],
    evidence_counters: Mapping[str, Mapping[str, int]],
    source_chain_frontier_reasons: tuple[str, ...],
    replay_only_eid_samples: tuple[str, ...],
    oracle_only_eid_samples: tuple[str, ...],
) -> FrontierWorkItem:
    triage_bucket = str(row.get("triage_bucket") or "")
    return FrontierWorkItem(
        work_item_id=work_item_id,
        jurisdiction="uk",
        source_artifact_id=statute_id,
        source_unit_id=triage_bucket or spec.lane_id,
        source_witness={
            "base": _source_witness_for_row(row, role="base"),
            "oracle": _source_witness_for_row(row, role="oracle"),
            "source_chain_frontier_reasons": list(source_chain_frontier_reasons),
        },
        target_witness={
            "replay_only_eid_samples": list(replay_only_eid_samples),
            "oracle_only_eid_samples": list(oracle_only_eid_samples),
        },
        compare_witness={
            "score_status": str(row.get("score_status") or ""),
            "aligned": _float_or_none(row.get("aligned")),
            "n_replay": _int(row.get("n_replay")),
            "n_oracle": _int(row.get("n_oracle")),
            "n_only_in_replayed": _int(row.get("n_only_in_replayed")),
            "n_only_in_oracle": _int(row.get("n_only_in_oracle")),
        },
        owner_phase=owner_phase,
        frontier_family=spec.lane_id,
        frontier_status=triage_bucket or "remaining_work_frontier",
        candidate_operation_family=triage_bucket,
        candidate_targets=tuple(
            dict.fromkeys((*oracle_only_eid_samples[:10], *replay_only_eid_samples[:10]))
        ),
        guidance_refs=("notes_internal/UK_LAWVM_ROADMAP.md",),
        required_claim_kind=spec.work_kind,
        required_validator_checks=missing_proofs or ("frontier_review",),
        required_proofs=missing_proofs or ("frontier_review",),
        safe_default="classify_or_queue_without_replay_promotion",
        forbidden_shortcuts=spec.forbidden_shortcuts,
        executable=False,
        replay_authorized=False,
        authorization_status="non_executable_work_item",
        detail={
            "lane_id": spec.lane_id,
            "priority_rank": spec.priority_rank,
            "next_action": spec.next_action,
            "source_frontier_reason": str(row.get("source_chain_frontier_reason") or ""),
            "effective_oracle_review_status": _effective_oracle_status(row),
            "effective_oracle_refutation_reason": str(
                row.get("effective_oracle_refutation_reason") or ""
            ),
            "effective_oracle_remaining_question": str(
                row.get("effective_oracle_remaining_question") or ""
            ),
            "evidence_counters": {
                key: dict(value) for key, value in evidence_counters.items()
            },
            "mutation_boundary_unexplained_report_count": _int(
                row.get("n_mutation_boundary_unexplained_reports")
            ),
            "mutation_boundary_unexplained_path_count": _int(
                row.get("n_mutation_boundary_unexplained_paths")
            ),
        },
    )


def _remaining_work_item_evidence_counters(
    row: Mapping[str, Any],
) -> Mapping[str, Mapping[str, int]]:
    return {
        "manual_frontier_status_counts": _int_mapping(
            row.get("manual_frontier_status_counts")
        ),
        "manual_frontier_rule_counts": _int_mapping(
            row.get("manual_frontier_rule_counts")
        ),
        "manual_frontier_work_item_family_counts": _int_mapping(
            row.get("manual_frontier_work_item_family_counts")
        ),
        "compile_rejection_rule_counts": _int_mapping(
            row.get("compile_rejection_rule_counts")
        ),
        "blocking_compile_rejection_rule_counts": _int_mapping(
            row.get("blocking_compile_rejection_rule_counts")
        ),
        "mutation_boundary_proof_status_counts": _int_mapping(
            row.get("mutation_boundary_proof_status_counts")
        ),
        "mutation_boundary_proof_rule_counts": _int_mapping(
            row.get("mutation_boundary_proof_rule_counts")
        ),
        "mutation_boundary_result_code_counts": _int_mapping(
            row.get("mutation_boundary_result_code_counts")
        ),
    }


def _candidate_set_coverage(
    *,
    spec: _LaneSpec,
    row: Mapping[str, Any],
    statute_id: str,
    missing_proofs: tuple[str, ...],
    replay_only_eid_samples: tuple[str, ...],
    oracle_only_eid_samples: tuple[str, ...],
) -> CandidateSetCoverage:
    candidate_ids = tuple(
        dict.fromkeys((*oracle_only_eid_samples, *replay_only_eid_samples))
    )
    declared_count = max(
        len(candidate_ids),
        _int(row.get("n_only_in_oracle")) + _int(row.get("n_only_in_replayed")),
    )
    missing_count = max(0, declared_count - len(candidate_ids))
    if declared_count == 0:
        completeness_status = "unavailable"
    elif missing_count:
        completeness_status = "partial"
    else:
        completeness_status = "complete"
    return CandidateSetCoverage(
        scope_id=f"uk-remaining:{spec.lane_id}:{statute_id}",
        candidate_set_kind="remaining_work_residual_eid_samples",
        phase=spec.owner_phase,
        rule_id=f"uk_remaining_work_{spec.lane_id}_residual_sample_certificate",
        reason=(
            "Residual EID samples bound review scope only; they do not prove "
            "candidate completeness or authorize replay."
        ),
        completeness_status=completeness_status,
        candidate_count=declared_count,
        candidate_ids=candidate_ids,
        missing_candidate_count=missing_count,
        selected_candidate_ids=(),
        blocker_counts={proof: 1 for proof in missing_proofs},
        blocker_families=missing_proofs,
        next_promotion_allowed=False,
        next_promotion_requires=missing_proofs or ("frontier_review",),
        detail={
            "lane_id": spec.lane_id,
            "triage_bucket": str(row.get("triage_bucket") or ""),
            "oracle_only_sample_count": len(oracle_only_eid_samples),
            "replay_only_sample_count": len(replay_only_eid_samples),
        },
    )


def _source_witness_for_row(row: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    witness = _mapping(row.get(f"{role}_source_witness"))
    if witness:
        return dict(witness)
    return {
        "source_status": str(row.get(f"{role}_source_status") or ""),
        "locator": str(row.get(f"{role}_source_locator") or ""),
        "source_role": f"uk_remaining_work_{role}_source",
    }


def _summary(
    report: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    scored_rows: Sequence[Mapping[str, Any]],
    source_frontier_rows: Sequence[Mapping[str, Any]],
    lanes: Sequence[UKRemainingWorkLane],
    *,
    reference_bucket: str,
    reference_score: float,
    unknown_buckets: set[str],
) -> dict[str, Any]:
    report_summary = _mapping(report.get("summary"))
    completion_gate_failure_counts = _mapping(
        report_summary.get("completion_gate_failure_counts")
    )
    return {
        "row_count": len(rows),
        "scored_count": len(scored_rows),
        "source_frontier_count": len(source_frontier_rows),
        "lane_count": len(lanes),
        "reference_bucket": reference_bucket,
        "reference_score": reference_score,
        "completion_gate_clean": bool(report_summary.get("completion_gate_clean")),
        "completion_gate_failure_counts": dict(completion_gate_failure_counts),
        "active_unclassified_residual_count": _int(
            report_summary.get("active_unclassified_residual_count")
        ),
        "deterministic_frontend_candidate_count": _int(
            report_summary.get("deterministic_frontend_candidate_count")
        ),
        "non_manual_source_chain_frontier_count": _int(
            report_summary.get("non_manual_source_chain_frontier_count")
        ),
        "mutation_boundary_unexplained_report_count": _int(
            report_summary.get("mutation_boundary_unexplained_report_count")
        ),
        "mutation_boundary_unexplained_path_count": _int(
            report_summary.get("mutation_boundary_unexplained_path_count")
        ),
        "unknown_scored_triage_buckets": sorted(unknown_buckets),
        "lane_counts": {lane.lane_id: lane.row_count for lane in lanes},
        "effective_oracle_review_status_counts": _counter_dict(
            status for row in rows for status in [_effective_oracle_status(row)] if status
        ),
        "forbidden_shortcuts": [
            "work_lane_as_replay_authorization",
            "oracle_score_as_source_truth",
            "frontier_class_as_manual_claim",
            "source_or_target_over_promotion",
        ],
    }


def _reference_score(
    scored_rows: Sequence[Mapping[str, Any]],
    reference_bucket: str,
) -> float:
    reference_rows = [
        row
        for row in scored_rows
        if str(row.get("triage_bucket") or "") == reference_bucket
    ]
    return _mean_aligned(reference_rows) or _mean_aligned(scored_rows) or 0.0


def _dominant_owner_phase(rows: Sequence[Mapping[str, Any]]) -> str | None:
    counts: Counter[str] = Counter()
    for row in rows:
        residual = _primary_agreement_residual(row)
        owner_phase = str(residual.get("owner_phase") or "")
        if owner_phase:
            counts[owner_phase] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _missing_proof_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        source_frontier_proofs = _source_frontier_required_proofs(row)
        if source_frontier_proofs:
            for proof in source_frontier_proofs:
                counts[proof] += 1
            continue
        residual = _primary_agreement_residual(row)
        for proof in _string_tuple(residual.get("missing_proofs")):
            counts[proof] += 1
        if _has_effective_oracle_residual(row):
            continue
        for field in (
            "manual_frontier_missing_proof_counts",
            "compile_rejection_missing_proof_counts",
            "blocking_compile_rejection_missing_proof_counts",
        ):
            for proof, count in _mapping(row.get(field)).items():
                counts[str(proof)] += _int(count)
    return dict(sorted(counts.items()))


def _missing_proofs_for_row(row: Mapping[str, Any]) -> tuple[str, ...]:
    source_frontier_proofs = _source_frontier_required_proofs(row)
    if source_frontier_proofs:
        return tuple(dict.fromkeys(source_frontier_proofs))
    proofs: list[str] = []
    residual = _primary_agreement_residual(row)
    proofs.extend(_string_tuple(residual.get("missing_proofs")))
    if _has_effective_oracle_residual(row):
        return tuple(dict.fromkeys(proofs))
    for field in (
        "manual_frontier_missing_proof_counts",
        "compile_rejection_missing_proof_counts",
        "blocking_compile_rejection_missing_proof_counts",
    ):
        for proof, count in _mapping(row.get(field)).items():
            if _int(count) > 0:
                proofs.append(str(proof))
    return tuple(dict.fromkeys(proofs))


def _only_non_textual_or_out_of_scope_effects(row: Mapping[str, Any]) -> bool:
    counts = _mapping(row.get("manual_frontier_status_counts"))
    total = sum(_int(value) for value in counts.values())
    return total > 0 and set(counts) == {"non_textual_or_out_of_scope"}


def _has_manual_compile_frontier(row: Mapping[str, Any]) -> bool:
    reasons = set(_string_tuple(row.get("source_chain_frontier_reasons")))
    counts = _mapping(row.get("manual_frontier_status_counts"))
    return (
        "manual_frontier_manual_compile_candidate" in reasons
        or _int(counts.get("manual_compile_candidate")) > 0
    )


def _has_effect_source_footing_gap(row: Mapping[str, Any]) -> bool:
    reasons = set(_string_tuple(row.get("source_chain_frontier_reasons")))
    if reasons & {
        "effect_rows_not_admitted_by_replay_lens",
        "manual_frontier_source_insufficient",
    }:
        return True
    counts = _mapping(row.get("manual_frontier_status_counts"))
    if _int(counts.get("source_insufficient")) > 0:
        return True
    return set(_missing_proofs_for_row(row)) <= {"source_identity"}


def _is_metadata_only_source_frontier(row: Mapping[str, Any]) -> bool:
    if str(row.get("score_status") or "") != "source_frontier":
        triage_bucket = str(row.get("triage_bucket") or "")
        if not triage_bucket.startswith("source_frontier:"):
            return False
    return "metadata_only" in {
        str(row.get("base_source_status") or ""),
        str(row.get("oracle_source_status") or ""),
    }


def _source_status_pair(row: Mapping[str, Any]) -> str:
    if "base_source_status" not in row and "oracle_source_status" not in row:
        return ""
    base = str(row.get("base_source_status") or "unknown")
    oracle = str(row.get("oracle_source_status") or "unknown")
    return f"base:{base}|oracle:{oracle}"


def _source_frontier_required_proofs(row: Mapping[str, Any]) -> tuple[str, ...]:
    work_item = _mapping(row.get("source_frontier_work_item"))
    return _string_tuple(work_item.get("required_proofs"))


def _load_effective_oracle_reviews(
    review_path: Path | None,
) -> Mapping[str, Mapping[str, Any]]:
    if review_path is None:
        return {}
    report = json.loads(review_path.read_text())
    if report.get("jurisdiction") != _EXPECTED_JURISDICTION:
        raise ValueError(
            f"{review_path} is not a UK effective-oracle review report: "
            f"jurisdiction={report.get('jurisdiction')!r}"
        )
    if report.get("report_kind") != _EXPECTED_EFFECTIVE_ORACLE_REPORT_KIND:
        raise ValueError(
            f"{review_path} is not an effective-oracle review report: "
            f"report_kind={report.get('report_kind')!r}"
        )
    if report.get("schema") != _EXPECTED_EFFECTIVE_ORACLE_SCHEMA:
        raise ValueError(
            f"{review_path} has unsupported effective-oracle schema: "
            f"{report.get('schema')!r}"
        )
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{review_path} does not contain effective-oracle rows")
    reviews: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        statute_id = str(row.get("statute_id") or "")
        if not statute_id:
            continue
        reviews[statute_id] = row
    return reviews


def _with_effective_oracle_review(
    row: Mapping[str, Any],
    reviews: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    if not reviews:
        return row
    statute_id = str(row.get("statute_id") or "")
    review = reviews.get(statute_id)
    if not review:
        return row
    updated = dict(row)
    updated["effective_oracle_review_status"] = str(review.get("review_status") or "")
    updated["effective_oracle_refutation_reason"] = str(
        review.get("refutation_reason") or ""
    )
    updated["effective_oracle_remaining_question"] = str(
        review.get("remaining_question") or ""
    )
    review_residual = _mapping(review.get("agreement_residual"))
    if review_residual:
        updated["effective_oracle_agreement_residual"] = dict(review_residual)
    return updated


def _effective_oracle_status(row: Mapping[str, Any]) -> str:
    return str(row.get("effective_oracle_review_status") or "")


def _primary_agreement_residual(row: Mapping[str, Any]) -> Mapping[str, Any]:
    effective_residual = _mapping(row.get("effective_oracle_agreement_residual"))
    if effective_residual:
        return effective_residual
    return _mapping(row.get("agreement_residual"))


def _has_effective_oracle_residual(row: Mapping[str, Any]) -> bool:
    return bool(_mapping(row.get("effective_oracle_agreement_residual")))


def _sample_statutes(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int = 10,
) -> tuple[str, ...]:
    ranked = sorted(rows, key=_sample_sort_key)
    samples: list[str] = []
    seen: set[str] = set()
    for row in ranked:
        statute_id = str(row.get("statute_id") or "")
        if not statute_id or statute_id in seen:
            continue
        samples.append(statute_id)
        seen.add(statute_id)
        if len(samples) >= limit:
            break
    return tuple(samples)


def _sample_sort_key(row: Mapping[str, Any]) -> tuple[int, float, str]:
    disagreement = _int(row.get("n_only_in_replayed")) + _int(
        row.get("n_only_in_oracle")
    )
    aligned = _float_or_none(row.get("aligned"))
    aligned_value = aligned if aligned is not None else 101.0
    return (-disagreement, aligned_value, str(row.get("statute_id") or ""))


def _counter_dict(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _mean_aligned(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = [
        value
        for row in rows
        for value in [_float_or_none(row.get("aligned"))]
        if value is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _int_mapping(value: Any) -> Mapping[str, int]:
    return {
        str(key): _int(count)
        for key, count in _mapping(value).items()
        if _int(count) > 0
    }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if str(item))


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(float(value))
    return 0


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return None


def _emit_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _emit_tsv(payload: Mapping[str, Any]) -> str:
    header = (
        "lane_id",
        "priority_rank",
        "owner_phase",
        "work_kind",
        "row_count",
        "scored_count",
        "source_frontier_count",
        "mean_aligned",
        "loss_points_vs_reference",
        "top_triage_buckets",
        "source_status_pairs",
        "top_missing_proofs",
        "sample_statutes",
    )
    lines: list[str] = ["\t".join(header)]
    for lane in payload["lanes"]:
        lines.append(
            "\t".join(
                (
                    str(lane["lane_id"]),
                    str(lane["priority_rank"]),
                    str(lane["owner_phase"]),
                    str(lane["work_kind"]),
                    str(lane["row_count"]),
                    str(lane["scored_count"]),
                    str(lane["source_frontier_count"]),
                    _format_optional_float(lane["mean_aligned"], digits=4),
                    _format_optional_float(
                        lane["loss_points_vs_reference"],
                        digits=4,
                    ),
                    _format_counter(lane["triage_bucket_counts"]),
                    _format_counter(lane["source_status_pair_counts"]),
                    _format_counter(lane["missing_proof_counts"]),
                    ",".join(lane["sample_statutes"]),
                )
            )
        )
    return "\n".join(lines)


def _emit_text(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "UK remaining-work summary",
        (
            f"  rows={summary['row_count']} scored={summary['scored_count']} "
            f"source_frontier={summary['source_frontier_count']}"
        ),
        (
            f"  gates clean={summary['completion_gate_clean']} "
            f"active_unclassified={summary['active_unclassified_residual_count']} "
            f"deterministic_candidates={summary['deterministic_frontend_candidate_count']} "
            "mutation_boundary_unexplained="
            f"{summary['mutation_boundary_unexplained_report_count']}/"
            f"{summary['mutation_boundary_unexplained_path_count']}"
        ),
        (
            "  reference="
            f"{summary['reference_bucket']}:{summary['reference_score']:.4f}"
        ),
        "  lanes:",
    ]
    if summary["effective_oracle_review_status_counts"]:
        lines.insert(
            -1,
            "  effective_oracle_review="
            f"{_format_counter(summary['effective_oracle_review_status_counts'], limit=6)}",
        )
    for lane in payload["lanes"]:
        mean = _format_optional_float(lane["mean_aligned"], digits=2, empty="n/a")
        loss = _format_optional_float(
            lane["loss_points_vs_reference"],
            digits=2,
            empty="n/a",
        )
        lines.append(
            "    "
            f"{lane['lane_id']}: n={lane['row_count']} scored={lane['scored_count']} "
            f"source_frontier={lane['source_frontier_count']} "
            f"phase={lane['owner_phase']} mean={mean} loss={loss}"
        )
        if lane["sample_statutes"]:
            lines.append(f"      samples={','.join(lane['sample_statutes'][:5])}")
        if lane["source_status_pair_counts"]:
            lines.append(
                "      source_statuses="
                f"{_format_counter(lane['source_status_pair_counts'], limit=5)}"
            )
        if lane["missing_proof_counts"]:
            lines.append(
                f"      proofs={_format_counter(lane['missing_proof_counts'], limit=5)}"
            )
    return "\n".join(lines)


def _format_optional_float(
    value: Any,
    *,
    digits: int,
    empty: str = "",
) -> str:
    if value is None:
        return empty
    return f"{float(value):.{digits}f}"


def _format_counter(counts: Mapping[str, Any], *, limit: int = 4) -> str:
    ordered = sorted(counts.items(), key=lambda item: (-_int(item[1]), str(item[0])))
    return ",".join(f"{key}={_int(value)}" for key, value in ordered[:limit])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize remaining UK work from a broad-baseline report."
    )
    parser.add_argument("report", type=Path, help="Broad-baseline .report.json path")
    parser.add_argument(
        "--format",
        choices=("json", "tsv", "text"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--core-polish-threshold",
        type=float,
        default=_DEFAULT_CORE_POLISH_THRESHOLD,
        help=(
            "Aligned-score threshold below which high-fidelity rows are shown as "
            "low-priority core polish (default: 99.5)"
        ),
    )
    parser.add_argument(
        "--effective-oracle-review",
        type=Path,
        help=(
            "Optional uk_effective_oracle_review JSON overlay used to move "
            "reviewed retained-repeal rows out of live oracle-suspect work."
        ),
    )
    parser.add_argument(
        "--include-items",
        action="store_true",
        help="include row-level non-executable work items in JSON output",
    )
    parser.add_argument(
        "--lane",
        action="append",
        default=[],
        help="with --include-items, restrict item export to this lane id; repeatable",
    )
    parser.add_argument(
        "--item-limit",
        type=int,
        default=0,
        help="with --include-items, maximum row-level items to emit",
    )
    parser.add_argument(
        "--item-limit-per-lane",
        type=int,
        default=0,
        help=(
            "with --include-items, maximum row-level items to emit from each "
            "remaining-work lane"
        ),
    )
    parser.add_argument(
        "--fail-on-item-safety-gaps",
        action="store_true",
        help=(
            "with --include-items, exit nonzero if any exported item is "
            "executable, replay-authorized, or missing shared packet fields"
        ),
    )
    parser.add_argument(
        "--fail-on-item-coverage-gaps",
        action="store_true",
        help=(
            "with --include-items, exit nonzero if selected remaining-work rows "
            "were not exported as items"
        ),
    )
    args = parser.parse_args(argv)

    payload = load_remaining_work(
        args.report,
        core_polish_threshold=args.core_polish_threshold,
        effective_oracle_review_path=args.effective_oracle_review,
        include_items=args.include_items,
        item_lane_ids=frozenset(args.lane),
        item_limit=args.item_limit,
        item_limit_per_lane=args.item_limit_per_lane,
    )
    if args.format == "json":
        print(_emit_json(payload))
    elif args.format == "tsv":
        print(_emit_tsv(payload))
    else:
        print(_emit_text(payload))
    if args.fail_on_item_safety_gaps and payload["summary"].get(
        "item_safety_gap_counts"
    ):
        return 1
    if args.fail_on_item_coverage_gaps and not payload["summary"].get(
        "item_fully_exported",
        False,
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

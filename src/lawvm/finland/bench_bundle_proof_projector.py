"""Finland bench, evidence-bundle, and frontier proof-surface projections.

Report/read-model adapters only; no replay authorization semantics.
"""

from __future__ import annotations

from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.source_pathology import source_pathology_evidence_report
from lawvm.core.source_witness import source_witness_digest_coverage_counts
from lawvm.finland.pathology_failed_op_projector import (
    source_pathology_projections as _source_pathology_projections,
)
from lawvm.finland.proof_surface_row_helpers import (
    mapping_sequence as _mapping_sequence,
    string_sequence as _string_sequence,
)


def finland_bench_run_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap a saved Finland benchmark run in a passive evidence envelope."""

    stats_raw = payload.get("stats")
    stats = dict(stats_raw) if isinstance(stats_raw, Mapping) else {}
    diagnostic_counts = dict(payload.get("diagnostic_summary_counts") or {})
    status_counts = dict(payload.get("status_counts") or {})
    summary = {
        "statute_count": int(stats.get("n") or 0),
        "error_count": int(stats.get("errors") or 0),
        "mean_score": stats.get("mean"),
        "perfect_count": int(stats.get("perfect") or 0),
        "above_99_count": int(stats.get("above_99") or 0),
        "above_95_count": int(stats.get("above_95") or 0),
        "below_90_count": int(stats.get("below_90") or 0),
        "status_counts": status_counts,
        "diagnostic_summary_counts": diagnostic_counts,
        "diagnostic_summary_row_count": int(payload.get("diagnostic_summary_row_count") or 0),
        "section_score": bool(payload.get("section_score") or False),
        "levenshtein_score": bool(payload.get("levenshtein_score") or False),
    }
    oracle_adjusted = payload.get("oracle_stale_adjusted")
    if isinstance(oracle_adjusted, Mapping):
        summary["oracle_stale_adjusted_mean"] = oracle_adjusted.get("mean")
        summary["oracle_stale_adjusted_excluded_count"] = len(oracle_adjusted.get("excluded") or ())
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_bench_run",
        schema="lawvm.finland_bench_run.v1",
        truth_claim="finland_benchmark_agreement_regression_evidence_not_source_truth",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "label": str(payload.get("label") or ""),
            "mode": str(payload.get("mode") or ""),
            "corpus_path": str(payload.get("corpus_path") or ""),
        },
        filtered_summary=summary,
        rows=(),
        rows_truncated=False,
        written_paths=tuple(str(path) for path in (payload.get("run_path"), payload.get("history_path")) if path),
        detail={
            "safe_default": "treat_benchmark_scores_as_regression_evidence_not_replay_authorization",
            "forbidden_shortcuts": (
                "bench_score_as_source_truth",
                "bench_score_as_replay_authorization",
                "diagnostics_summary_as_mutation_instruction",
                "oracle_adjusted_headline_as_legal_state",
                "run_csv_as_manual_claim_authority",
            ),
            "included_surfaces": (
                "saved_run_csv",
                "benchmark_history_csv",
                "status_counts",
                "diagnostic_summary_counts",
            ),
            "timestamp": str(payload.get("timestamp") or ""),
            "worker_count": int(payload.get("worker_count") or 0),
            "fast_mode": bool(payload.get("fast_mode") or False),
            "diagnostic_replay": bool(payload.get("diagnostic_replay") or False),
            "selection_as_of": str(payload.get("selection_as_of") or ""),
        },
    ).to_dict()


def finland_evidence_bundle_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland evidence-bundle JSON in the shared evidence envelope."""

    html_topology = payload.get("html_topology")
    html_witnesses: tuple[Mapping[str, Any], ...] = ()
    if isinstance(html_topology, Mapping) and isinstance(html_topology.get("source_witness"), Mapping):
        html_witnesses = (html_topology["source_witness"],)
    supporting_amendments = _mapping_sequence(payload.get("supporting_amendments"))
    corrigendum_witnesses = tuple(
        witness
        for amendment in supporting_amendments
        for witness in _mapping_sequence(amendment.get("source_witnesses"))
    )
    source_witnesses = (*html_witnesses, *corrigendum_witnesses)
    proof_claims = _mapping_sequence(payload.get("proof_claims"))
    section_claims = _mapping_sequence(payload.get("section_claims"))
    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_report = source_pathology_evidence_report(
        _source_pathology_projections(source_pathologies),
        jurisdiction="fi",
        report_kind="finland_evidence_bundle_source_pathology",
    ).to_dict()
    source_pathology_rows = _mapping_sequence(source_pathology_report.get("rows"))
    context_diagnostics = _mapping_sequence(payload.get("evidence_context_diagnostics"))
    section_bisect = _mapping_sequence(payload.get("section_bisect"))
    compiler_observations_raw = payload.get("compiler_observations")
    compiler_observations = dict(compiler_observations_raw) if isinstance(compiler_observations_raw, Mapping) else {}
    proof_tiers = _string_sequence(payload.get("proof_tiers"))
    rows = tuple(
        (
            *({**dict(row), "surface": "source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "proof_claim"} for row in proof_claims),
            *({"surface": "source_pathology", **dict(row)} for row in source_pathology_rows),
            *({**dict(row), "surface": "evidence_context_diagnostic"} for row in context_diagnostics),
        )
    )
    summary = {
        "proof_claim_count": len(proof_claims),
        "section_claim_count": len(section_claims),
        "source_pathology_count": len(source_pathology_rows),
        "source_pathology_kind_counts": dict(
            source_pathology_report.get("summary", {}).get("pathology_kind_counts", {})
        ),
        "source_pathology_affected_phase_counts": dict(
            source_pathology_report.get("summary", {}).get("affected_phase_counts", {})
        ),
        "source_pathology_suggested_lane_counts": dict(
            source_pathology_report.get("summary", {}).get("suggested_lane_counts", {})
        ),
        "supporting_amendment_count": len(supporting_amendments),
        "source_witness_count": len(source_witnesses),
        "html_topology_source_witness_count": len(html_witnesses),
        "corrigendum_source_witness_count": len(corrigendum_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "evidence_context_diagnostic_count": len(context_diagnostics),
        "section_bisect_row_count": len(section_bisect),
        "proof_tiers": proof_tiers,
        "primary_proof_tier": str(payload.get("primary_proof_tier") or ""),
        "overall_score": payload.get("overall_score"),
        "section_score": payload.get("section_score"),
        "compiler_normalized_observation_count": int(
            compiler_observations.get("normalized_section_observation_count") or 0
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_evidence_bundle",
        schema="lawvm.finland_evidence_bundle.v1",
        truth_claim="finland_oracle_review_and_proof_claim_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filters={
            "statute_id": str(payload.get("statute_id") or ""),
            "mode": str(payload.get("mode") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_evidence_bundle_as_passive_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "evidence_bundle_as_replay_authorization",
                "oracle_score_as_source_truth",
                "proof_claim_as_mutation_instruction",
                "html_topology_witness_as_raw_html_digest",
                "corrigendum_witness_as_manual_patch_authority",
            ),
            "included_surfaces": (
                "source_witness",
                "proof_claim",
                "source_pathology",
                "evidence_context_diagnostic",
            ),
        },
    ).to_dict()


def finland_frontier_proof_evidence_surface(
    *,
    rows: tuple[Mapping[str, Any], ...],
    summary: Mapping[str, Any],
    label: str,
    mode: str,
    top: int | None = None,
    bucket_filter: str = "",
) -> dict[str, Any]:
    """Wrap Finland frontier proof rows in the shared evidence envelope."""

    normalized_rows = tuple(dict(row) for row in rows)
    normalized_summary = dict(summary)
    row_surfaces = tuple({**row, "surface": "frontier_proof_row"} for row in normalized_rows)
    report_summary = {
        "frontier_proof_row_count": len(normalized_rows),
        "primary_tiers": dict(normalized_summary.get("primary_tiers") or {}),
        "proof_kinds": dict(normalized_summary.get("proof_kinds") or {}),
        "section_claim_kinds": dict(normalized_summary.get("section_claim_kinds") or {}),
        "statute_only_proof_kinds": dict(normalized_summary.get("statute_only_proof_kinds") or {}),
        "section_claim_rules": dict(normalized_summary.get("section_claim_rules") or {}),
        "defeated_section_claim_kinds": dict(normalized_summary.get("defeated_section_claim_kinds") or {}),
        "defeated_section_claim_rules": dict(normalized_summary.get("defeated_section_claim_rules") or {}),
        "alternative_replay_sections": dict(normalized_summary.get("alternative_replay_sections") or {}),
        "bucket_primary_tiers": dict(normalized_summary.get("bucket_primary_tiers") or {}),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_frontier_proof_report",
        schema="lawvm.finland_frontier_proof_report.v1",
        truth_claim="finland_frontier_proof_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=report_summary,
        filters={
            "label": label,
            "mode": mode,
            "top": top if top is not None else "",
            "bucket_filter": bucket_filter,
        },
        filtered_summary=report_summary,
        rows=row_surfaces,
        rows_truncated=False,
        detail={
            "safe_default": "treat_frontier_proof_report_as_diagnostic_ranking_not_replay_authorization",
            "forbidden_shortcuts": (
                "frontier_rank_as_replay_authorization",
                "proof_tier_as_canonical_operation",
                "proof_row_as_mutation_instruction",
                "oracle_score_as_source_truth",
                "bucket_as_source_pathology_proof",
            ),
            "included_surfaces": ("frontier_proof_row",),
        },
    ).to_dict()


__all__ = [
    "finland_bench_run_evidence_surface",
    "finland_evidence_bundle_evidence_surface",
    "finland_frontier_proof_evidence_surface",
]

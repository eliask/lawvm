"""Finland corrigendum proof-surface projections.

Report/read-model adapters only; no replay authorization semantics.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_acquisition import (
    SourceAcquisitionAssertion,
    SourceBundlePolicy,
    source_bundle_evidence_report,
)
from lawvm.core.source_completeness import SourceCompletenessStatus
from lawvm.core.source_pathology import source_pathology_evidence_report
from lawvm.core.source_witness import source_witness_digest_coverage_counts
from lawvm.finland.pathology_failed_op_projector import (
    source_pathology_projections as _source_pathology_projections,
)
from lawvm.finland.proof_surface_row_helpers import (
    count_by_field as _count_by_field,
    field as _field,
    frontier_claim_template_kind_counts as _frontier_claim_template_kind_counts,
    frontier_claim_template_status_counts as _frontier_claim_template_status_counts,
    mapping_sequence as _mapping_sequence,
    object_sequence as _object_sequence,
    positive_int as _positive_int,
    string_sequence as _string_sequence,
    with_finland_claim_template as _with_finland_claim_template,
)
from lawvm.finland.source_witness_proof_projector import corrigendum_source_witness

def finland_corrigendum_review_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum review JSON in the shared evidence envelope."""

    amendments = _mapping_sequence(payload.get("amendments"))
    source_pathologies = _mapping_sequence(payload.get("source_pathologies"))
    source_pathology_report = source_pathology_evidence_report(
        _source_pathology_projections(source_pathologies),
        jurisdiction="fi",
        report_kind="finland_corrigendum_review_source_pathology",
    ).to_dict()
    source_pathology_rows = _mapping_sequence(source_pathology_report.get("rows"))
    unblamed_sections = _mapping_sequence(payload.get("unblamed_sections"))
    witnesses = tuple(
        witness
        for amendment in amendments
        for witness in _mapping_sequence(amendment.get("source_witnesses"))
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in witnesses),
            *({"surface": "source_pathology", **dict(row)} for row in source_pathology_rows),
            *({**dict(row), "surface": "corrigendum_review_amendment"} for row in amendments),
            *({**dict(row), "surface": "unblamed_section"} for row in unblamed_sections),
        )
    )
    summary = {
        "amendment_count": len(amendments),
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
        "unblamed_section_count": len(unblamed_sections),
        "contingent_effective_source_count": len(_string_sequence(payload.get("contingent_effective_sources"))),
        "corrigendum_source_witness_count": len(witnesses),
        "corrigendum_source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(witnesses),
        "corrigendum_db_row_count": sum(int(amendment.get("corrigendum_db_rows") or 0) for amendment in amendments),
        "corrigendum_no_match_row_count": sum(
            int(amendment.get("corrigendum_no_match_rows") or 0) for amendment in amendments
        ),
        "corrigendum_verified_row_count": sum(
            int(amendment.get("corrigendum_verified_rows") or 0) for amendment in amendments
        ),
        "manual_override_count": sum(int(amendment.get("manual_override_count") or 0) for amendment in amendments),
        "manual_template_entry_count": sum(
            int(amendment.get("manual_template_entry_count") or 0) for amendment in amendments
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_review",
        schema="lawvm.finland_corrigendum_review.v1",
        truth_claim="finland_corrigendum_review_diagnostics",
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
            "safe_default": "treat_corrigendum_review_as_source_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_review_as_replay_authorization",
                "corrigendum_source_witness_as_patch_application",
                "source_pathology_as_corrigendum_proof",
                "manual_template_count_as_manual_claim",
                "oracle_score_as_source_truth",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "source_pathology",
                "corrigendum_review_amendment",
                "unblamed_section",
            ),
        },
    ).to_dict()


def finland_corrigendum_provenance_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap amendment-scoped Finland corrigendum provenance in the shared envelope."""

    provenance_rows = _mapping_sequence(payload.get("rows"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    row_witnesses = tuple(
        witness
        for row in provenance_rows
        for witness in (row.get("source_witness"),)
        if isinstance(witness, Mapping)
    )
    witnesses = source_witnesses or row_witnesses
    status_counts: dict[str, int] = {}
    for row in provenance_rows:
        status = str(row.get("provenance_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in witnesses),
            *({**dict(row), "surface": "corrigendum_provenance_row"} for row in provenance_rows),
        )
    )
    summary = {
        "provenance_row_count": len(provenance_rows),
        "source_witness_count": len(witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(witnesses),
        "status_counts": dict(sorted(status_counts.items())),
        "verified_count": int(payload.get("verified_count") or 0),
        "attachment_only_count": int(payload.get("attachment_only_count") or 0),
        "manual_exact_count": int(payload.get("manual_exact_count") or 0),
        "open_manual_candidate_count": int(payload.get("open_manual_candidate_count") or 0),
        "manual_entry_count": int(payload.get("manual_entry_count") or 0),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_provenance",
        schema="lawvm.finland_corrigendum_provenance.v1",
        truth_claim="finland_corrigendum_provenance_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_corrigendum_provenance_as_source_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_provenance_as_replay_authorization",
                "corrigendum_source_witness_as_patch_application",
                "manual_override_status_as_manual_claim",
                "source_verified_status_as_mutation_boundary_proof",
                "attachment_only_status_as_source_text_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_provenance_row",
            ),
        },
    ).to_dict()


def finland_corrigendum_overview_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap corpus-level Finland corrigendum overview in the shared envelope."""

    top_unresolved = _mapping_sequence(payload.get("top_unresolved_amendments"))
    top_open_manual = _mapping_sequence(payload.get("top_open_manual_amendments"))
    top_attachment_only = _mapping_sequence(payload.get("top_attachment_only_amendments"))
    source_pdf_count = int(payload.get("source_pdf_count") or 0)
    missing_date_published_count = int(payload.get("missing_date_published_count") or 0)
    source_date_status_counts = dict(payload.get("source_date_status_counts") or {})
    source_completeness_status = _corrigendum_sources_completeness_status(
        pdf_count=source_pdf_count,
        missing_date_count=missing_date_published_count,
        date_status_counts=source_date_status_counts,
        mode=str(payload.get("mode") or ""),
        manifest_kind="finland_corrigendum_overview_sources",
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_overview_unresolved_amendment"} for row in top_unresolved),
            *({**dict(row), "surface": "corrigendum_overview_open_manual_amendment"} for row in top_open_manual),
            *(
                {**dict(row), "surface": "corrigendum_overview_attachment_only_amendment"}
                for row in top_attachment_only
            ),
            *(
                (
                    {
                        "surface": "source_completeness_status",
                        **source_completeness_status.to_dict(),
                    },
                )
                if source_completeness_status is not None
                else ()
            ),
        )
    )
    status_counts = dict(payload.get("status_counts") or {})
    summary = {
        "official_item_count": int(payload.get("official_item_count") or 0),
        "amendment_count": int(payload.get("amendment_count") or 0),
        "source_pdf_count": source_pdf_count,
        "missing_amendment_id_count": int(payload.get("missing_amendment_id_count") or 0),
        "missing_date_published_count": missing_date_published_count,
        "source_date_status_counts": source_date_status_counts,
        "type_counts": dict(payload.get("type_counts") or {}),
        "status_counts": status_counts,
        "top_unresolved_amendment_count": len(top_unresolved),
        "top_open_manual_amendment_count": len(top_open_manual),
        "top_attachment_only_amendment_count": len(top_attachment_only),
        "open_manual_candidate_count": int(status_counts.get("open_manual_candidate") or 0),
        "unresolved_unverified_count": int(status_counts.get("unresolved_unverified") or 0),
        "unresolved_unreviewed_count": int(status_counts.get("unresolved_unreviewed") or 0),
        "source_completeness_status_count": 1 if source_completeness_status is not None else 0,
        "source_completeness": (
            source_completeness_status.counts if source_completeness_status is not None else {}
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_overview",
        schema="lawvm.finland_corrigendum_overview.v1",
        truth_claim="finland_corrigendum_corpus_overview_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "mode": str(payload.get("mode") or ""),
            "limit": int(payload.get("limit") or 0),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_corrigendum_overview_as_corpus_diagnostics_not_replay_authorization",
            "forbidden_shortcuts": (
                "corrigendum_overview_as_replay_authorization",
                "status_count_as_manual_claim",
                "status_count_as_mutation_boundary_proof",
                "source_date_status_as_source_text_repair",
                "top_list_rank_as_execution_priority",
            ),
            "included_surfaces": (
                "corrigendum_overview_unresolved_amendment",
                "corrigendum_overview_open_manual_amendment",
                "corrigendum_overview_attachment_only_amendment",
                "source_completeness_status",
            ),
        },
    ).to_dict()


def finland_corrigendum_open_manual_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap the open manual-corrigendum candidate listing in the shared envelope."""

    candidates = _mapping_sequence(payload.get("rows"))
    frontier_items = tuple(
        _corrigendum_open_manual_frontier_work_item(index=index, candidate=candidate).to_dict()
        for index, candidate in enumerate(candidates)
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_open_manual_candidate"} for row in candidates),
            *(
                {**dict(row), "surface": "corrigendum_open_manual_frontier_work_item"}
                for row in frontier_items
            ),
        )
    )
    summary = {
        "candidate_count": len(candidates),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "open_manual_row_count": sum(int(row.get("open_manual_rows") or 0) for row in candidates),
        "attachment_only_row_count": sum(int(row.get("attachment_only_rows") or 0) for row in candidates),
        "unverified_row_count": sum(int(row.get("db_no_match_rows") or 0) for row in candidates),
        "manual_entry_count": sum(int(row.get("manual_entry_count") or 0) for row in candidates),
        "db_row_count": sum(int(row.get("db_row_count") or 0) for row in candidates),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_open_manual",
        schema="lawvm.finland_corrigendum_open_manual.v1",
        truth_claim="finland_corrigendum_open_manual_frontier_listing",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "limit": int(payload.get("limit") or 0),
            "include_all": bool(payload.get("include_all")),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_open_manual_listing_as_frontier_triage_not_manual_claim_or_replay_authority",
            "forbidden_shortcuts": (
                "open_manual_candidate_as_replay_authorization",
                "open_manual_candidate_as_manual_claim",
                "candidate_rank_as_execution_priority",
                "unverified_count_as_source_text_repair",
                "manual_entry_count_as_claim_validation",
            ),
            "included_surfaces": (
                "corrigendum_open_manual_candidate",
                "corrigendum_open_manual_frontier_work_item",
            ),
        },
    ).to_dict()


def _corrigendum_open_manual_frontier_work_item(
    *,
    index: int,
    candidate: Mapping[str, Any],
) -> FrontierWorkItem:
    amendment_id = str(candidate.get("amendment_id") or f"unknown-{index}")
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "db_row_count": int(candidate.get("db_row_count") or 0),
                "db_no_match_rows": int(candidate.get("db_no_match_rows") or 0),
                "open_manual_rows": int(candidate.get("open_manual_rows") or 0),
                "attachment_only_rows": int(candidate.get("attachment_only_rows") or 0),
                "manual_entry_count": int(candidate.get("manual_entry_count") or 0),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-open-manual:{digest}",
        jurisdiction="fi",
        source_artifact_id=amendment_id,
        source_unit_id=f"{amendment_id}#open-manual",
        owner_phase="manual_claim_frontier",
        frontier_family="fi_corrigendum_open_manual_candidate",
        frontier_status="manual_claim_needed",
        candidate_operation_family="corrigendum_source_repair",
        candidate_targets=(amendment_id,),
        guidance_refs=("lawvm_corrigendum_open_manual_review",),
        required_claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_open_manual_candidate_without_manual_claim",
        forbidden_shortcuts=(
            "open_manual_candidate_as_replay_authorization",
            "open_manual_candidate_as_manual_claim",
            "candidate_rank_as_execution_priority",
            "unverified_count_as_source_text_repair",
            "manual_entry_count_as_claim_validation",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_manual_claim_required",
        detail={
            "amendment_id": amendment_id,
            "candidate_index": index,
            "db_row_count": int(candidate.get("db_row_count") or 0),
            "db_no_match_rows": int(candidate.get("db_no_match_rows") or 0),
            "open_manual_rows": int(candidate.get("open_manual_rows") or 0),
            "attachment_only_rows": int(candidate.get("attachment_only_rows") or 0),
            "manual_entry_count": int(candidate.get("manual_entry_count") or 0),
        },
    ))


def finland_corrigendum_unsupported_patch_frontier_item(
    *,
    patch: Mapping[str, Any] | object,
    source_witness: Mapping[str, Any] | None = None,
) -> FrontierWorkItem:
    """Project an unsupported corrigendum patch as non-executable frontier work."""

    row = _unsupported_corrigendum_patch_row(patch)
    amendment_id = str(row.get("amendment_id") or "unknown")
    sequence = _positive_int(row.get("sequence"))
    correction_kind = str(row.get("correction_kind") or "unsupported")
    reason = str(row.get("reason") or "FINLAND.CORRIGENDUM_UNSUPPORTED_PATCH")
    location = str(row.get("location") or "")
    target = str(row.get("target") or "")
    source_statute = str(row.get("source_statute") or f"corr/{amendment_id}")
    witness = dict(source_witness or {})
    source_artifact_id = str(witness.get("artifact_id") or witness.get("locator") or source_statute)
    source_unit_id = str(
        witness.get("source_unit_id")
        or f"{amendment_id}#unsupported-corrigendum-{sequence or 'unknown'}"
    )
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "sequence": sequence,
                "correction_kind": correction_kind,
                "reason": reason,
                "location": location,
                "target": target,
                "wrong_text": str(row.get("wrong_text") or ""),
                "correct_text": str(row.get("correct_text") or ""),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-unsupported-patch:{digest}",
        jurisdiction="fi",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=witness,
        target_witness={
            "target": target,
            "location": location,
            "correction_kind": correction_kind,
        },
        owner_phase="corrigendum_payload_extraction",
        frontier_family=f"fi_corrigendum_{correction_kind.lower()}_unsupported",
        frontier_status="unsupported_corrigendum_patch_frontier",
        candidate_operation_family="corrigendum_patch_support",
        candidate_targets=(target or amendment_id,),
        guidance_refs=("lawvm_corrigendum_unsupported_patch_review",),
        required_claim_kind="fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "unsupported_patch_parser_support_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "unsupported_patch_shape_resolution",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_unsupported_corrigendum_patch_without_manual_claim_or_parser_support",
        forbidden_shortcuts=(
            "unsupported_corrigendum_patch_as_replay_authorization",
            "unsupported_corrigendum_patch_as_manual_claim",
            "correct_text_as_insert_payload_without_boundary_proof",
            "source_witness_as_patch_application",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_unsupported_corrigendum_patch",
        detail={
            "amendment_id": amendment_id,
            "sequence": sequence,
            "correction_kind": correction_kind,
            "reason": reason,
            "location": location,
            "source_statute": source_statute,
            "wrong_text_preview": str(row.get("wrong_text") or "")[:160],
            "correct_text_preview": str(row.get("correct_text") or "")[:160],
        },
    ))


def finland_corrigendum_unsupported_patch_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap unsupported corrigendum patches in a passive evidence envelope."""

    patches = tuple(
        _unsupported_corrigendum_patch_row(patch)
        for patch in _object_sequence(payload.get("patches"))
    )
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    default_source_witness = source_witnesses[0] if source_witnesses else None
    frontier_items = tuple(
        finland_corrigendum_unsupported_patch_frontier_item(
            patch=patch,
            source_witness=default_source_witness,
        ).to_dict()
        for patch in patches
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *(
                {**dict(row), "surface": "corrigendum_unsupported_patch_frontier_work_item"}
                for row in frontier_items
            ),
            *({**dict(row), "surface": "corrigendum_unsupported_patch"} for row in patches),
        )
    )
    summary = {
        "unsupported_patch_count": len(patches),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "reason_counts": _count_by_field(patches, "reason"),
        "correction_kind_counts": _count_by_field(patches, "correction_kind"),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_unsupported_patch",
        schema="lawvm.finland_corrigendum_unsupported_patch.v1",
        truth_claim="finland_corrigendum_unsupported_patch_frontier_listing",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_unsupported_corrigendum_patches_as_frontier_work_not_replay_authority",
            "forbidden_shortcuts": (
                "unsupported_corrigendum_patch_as_replay_authorization",
                "unsupported_corrigendum_patch_as_manual_claim",
                "corrigendum_source_witness_as_patch_application",
                "unsupported_patch_count_as_source_text_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_unsupported_patch_frontier_work_item",
                "corrigendum_unsupported_patch",
            ),
        },
    ).to_dict()


def _unsupported_corrigendum_patch_row(patch: Mapping[str, Any] | object) -> Mapping[str, Any]:
    target = _field(patch, "target", None)
    return {
        "amendment_id": str(_field(patch, "amendment_id", "") or ""),
        "sequence": _positive_int(_field(patch, "sequence", 0)),
        "correction_kind": str(
            _field(patch, "correction_kind", "")
            or _field(patch, "correction_type", "")
            or "unsupported"
        ),
        "location": str(_field(patch, "location", "") or _field(patch, "location_desc", "")),
        "target": str(target) if target is not None else "",
        "correct_text": str(_field(patch, "correct_text", "") or ""),
        "wrong_text": str(_field(patch, "wrong_text", "") or ""),
        "reason": str(_field(patch, "reason", "") or "FINLAND.CORRIGENDUM_UNSUPPORTED_PATCH"),
        "source_statute": str(_field(patch, "source_statute", "") or ""),
    }




def finland_corrigendum_manual_template_frontier_item(
    *,
    amendment_id: str,
    entry_index: int,
    entry: Mapping[str, Any],
    source_witness: Mapping[str, Any] | None = None,
) -> FrontierWorkItem:
    """Project a generated corrigendum manual-template entry as non-executable work."""

    witness = dict(source_witness or {})
    source_artifact_id = str(witness.get("artifact_id") or witness.get("locator") or amendment_id)
    source_unit_id = str(witness.get("source_unit_id") or f"{amendment_id}#{entry_index}")
    wrong_text = str(entry.get("wrong_text") or "")
    correct_text = str(entry.get("correct_text") or "")
    correction_type = str(entry.get("correction_type") or "")
    digest = hashlib.sha256(
        json.dumps(
            {
                "amendment_id": amendment_id,
                "entry_index": entry_index,
                "wrong_text": wrong_text,
                "correct_text": correct_text,
                "correction_type": correction_type,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return _with_finland_claim_template(FrontierWorkItem(
        work_item_id=f"fi:{amendment_id}:corrigendum-manual-template:{digest}",
        jurisdiction="fi",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=witness,
        owner_phase="manual_claim_frontier",
        frontier_family="fi_corrigendum_manual_override",
        frontier_status="manual_claim_needed",
        candidate_operation_family="corrigendum_source_repair",
        candidate_targets=(amendment_id,),
        guidance_refs=("lawvm_corrigendum_manual_template",),
        required_claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
        required_validator_checks=(
            "manual_corrigendum_claim_review",
            "source_xml_non_verification_review",
            "mutation_boundary_check_before_replay",
        ),
        required_proofs=(
            "corrigendum_source_correction_claim",
            "source_corrigendum_witness_review",
            "targeted_source_xml_non_verification_review",
            "mutation_boundary_proof_before_replay_promotion",
        ),
        safe_default="do_not_apply_corrigendum_template_without_manual_claim",
        forbidden_shortcuts=(
            "manual_template_entry_as_replay_authorization",
            "wrong_correct_text_pair_as_source_repair",
            "source_witness_as_patch_application",
            "manual_template_entry_as_manual_claim",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="blocked_manual_claim_required",
        detail={
            "amendment_id": amendment_id,
            "entry_index": entry_index,
            "correction_type": correction_type,
            "wrong_text_preview": wrong_text[:160],
            "correct_text_preview": correct_text[:160],
            "notes": str(entry.get("notes") or ""),
        },
    ))


def finland_corrigendum_manual_template_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum manual-template output in the shared envelope."""

    entries = _mapping_sequence(payload.get("entries"))
    frontier_items = _mapping_sequence(payload.get("frontier_work_items"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "corrigendum_manual_template_frontier_work_item"} for row in frontier_items),
            *({**dict(row), "surface": "corrigendum_manual_template_entry"} for row in entries),
        )
    )
    summary = {
        "entry_count": len(entries),
        "frontier_work_item_count": len(frontier_items),
        "frontier_claim_template_status_counts": _frontier_claim_template_status_counts(frontier_items),
        "frontier_claim_template_kind_counts": _frontier_claim_template_kind_counts(frontier_items),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "manual_entry_count": int(payload.get("manual_entry_count") or 0),
        "already_covered": bool(payload.get("already_covered")),
        "attachment_only_entry_count": int(payload.get("attachment_only_entry_count") or 0),
        "include_all": bool(payload.get("include_all")),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_manual_template",
        schema="lawvm.finland_corrigendum_manual_template.v1",
        truth_claim="finland_corrigendum_manual_template_frontier_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "amendment_id": str(payload.get("amendment_id") or ""),
            "include_all": bool(payload.get("include_all")),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail={
            "safe_default": "treat_manual_template_as_frontier_scaffold_not_manual_claim_or_replay_authority",
            "forbidden_shortcuts": (
                "manual_template_as_replay_authorization",
                "manual_template_entry_as_manual_claim",
                "frontier_work_item_as_canonical_operation",
                "source_witness_as_patch_application",
                "wrong_correct_text_pair_as_source_repair",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_manual_template_frontier_work_item",
                "corrigendum_manual_template_entry",
            ),
        },
    ).to_dict()


def finland_corrigendum_sources_evidence_surface(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap Finland corrigendum PDF source manifest output in the shared envelope."""

    records = _mapping_sequence(payload.get("records"))
    source_witnesses = _mapping_sequence(payload.get("source_witnesses"))
    date_status_counts = dict(payload.get("date_status_counts") or {})
    pdf_count = int(payload.get("pdf_count") or 0)
    missing_date_count = sum(
        int(count or 0)
        for status, count in date_status_counts.items()
        if str(status) != "present"
    )
    source_completeness_status = _corrigendum_sources_completeness_status(
        pdf_count=pdf_count,
        missing_date_count=missing_date_count,
        date_status_counts=date_status_counts,
        mode=str(payload.get("mode") or ""),
        manifest_kind="finland_corrigendum_pdf_sources",
    )
    source_bundle_report = _corrigendum_source_bundle_report(
        records,
        mode=str(payload.get("mode") or ""),
        rows_truncated=bool(payload.get("records_truncated")),
    )
    source_bundle_summary = (
        dict(source_bundle_report.summary) if source_bundle_report is not None else {}
    )
    rows = tuple(
        (
            *({**dict(row), "surface": "corrigendum_source_witness"} for row in source_witnesses),
            *({**dict(row), "surface": "corrigendum_source_manifest_record"} for row in records),
            *(source_bundle_report.rows if source_bundle_report is not None else ()),
            *(
                (
                    {
                        "surface": "source_completeness_status",
                        **source_completeness_status.to_dict(),
                    },
                )
                if source_completeness_status is not None
                else ()
            ),
        )
    )
    summary = {
        "pdf_count": pdf_count,
        "amendment_count": int(payload.get("amendment_count") or 0),
        "total_item_count": int(payload.get("total_item_count") or 0),
        "shown_record_count": len(records),
        "source_witness_count": len(source_witnesses),
        "source_witness_digest_coverage_counts": source_witness_digest_coverage_counts(source_witnesses),
        "date_status_counts": date_status_counts,
        "missing_date_count": missing_date_count,
        "source_completeness_status_count": 1 if source_completeness_status is not None else 0,
        "source_completeness": (
            source_completeness_status.counts if source_completeness_status is not None else {}
        ),
        "source_bundle_assertion_count": int(source_bundle_summary.get("assertion_count") or 0),
        "source_bundle_admission_count": int(source_bundle_summary.get("admission_count") or 0),
        "source_bundle_admitted_count": int(source_bundle_summary.get("admitted_count") or 0),
        "source_bundle_status_counts": dict(source_bundle_summary.get("status_counts") or {}),
    }
    return EvidenceSurfaceReport(
        jurisdiction="fi",
        report_kind="finland_corrigendum_sources",
        schema="lawvm.finland_corrigendum_sources.v1",
        truth_claim="finland_corrigendum_pdf_source_manifest_diagnostics",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "mode": str(payload.get("mode") or ""),
            "limit": int(payload.get("limit") or 0),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=bool(payload.get("records_truncated")),
        detail={
            "safe_default": "treat_corrigendum_source_manifest_as_source_footing_not_replay_authorization",
            "forbidden_shortcuts": (
                "source_manifest_as_replay_authorization",
                "source_witness_as_patch_application",
                "pdf_digest_as_manual_claim",
                "date_status_as_commencement_proof",
                "manifest_record_as_source_text_repair",
                "source_bundle_admission_as_replay_authorization",
            ),
            "included_surfaces": (
                "corrigendum_source_witness",
                "corrigendum_source_manifest_record",
                "source_acquisition_assertion",
                "source_bundle_admission",
                "source_completeness_status",
            ),
        },
    ).to_dict()


def _corrigendum_source_bundle_report(
    records: tuple[Mapping[str, Any], ...],
    *,
    mode: str,
    rows_truncated: bool,
) -> EvidenceSurfaceReport | None:
    if not records:
        return None
    assertions = tuple(_corrigendum_source_acquisition_assertion(record) for record in records)
    policy = SourceBundlePolicy(
        policy_id="fi.corrigendum_source_bundle.v1",
        jurisdiction="fi",
        admitted_source_lanes=("corrigendum_pdf",),
        safe_default="exclude_corrigendum_pdf_source_until_manifest_policy_is_satisfied",
        detail={
            "source_family": "finland_corrigendum_pdf_sources",
            "source_role": "finland_corrigendum_pdf",
        },
    )
    admissions = tuple(policy.evaluate(assertion) for assertion in assertions)
    return source_bundle_evidence_report(
        admissions,
        jurisdiction="fi",
        assertions=assertions,
        report_kind="finland_corrigendum_source_bundle",
        filters={"mode": mode},
        rows_truncated=rows_truncated,
    )


def _corrigendum_source_acquisition_assertion(
    record: Mapping[str, Any],
) -> SourceAcquisitionAssertion:
    witness = corrigendum_source_witness(record)
    digest = str(record.get("sha256") or "")
    artifact_id = (
        witness.artifact_id
        or witness.source_unit_id
        or str(record.get("pdf_name") or "")
        or (f"sha256:{digest}" if digest else "unknown_corrigendum_pdf_source")
    )
    assertion_key = json.dumps(
        {
            "amendment_id": str(record.get("amendment_id") or ""),
            "artifact_id": artifact_id,
            "digest": digest,
            "source_pdf": str(record.get("source_pdf") or ""),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    assertion_digest = hashlib.sha256(assertion_key.encode("utf-8")).hexdigest()[:16]
    return SourceAcquisitionAssertion(
        assertion_id=f"fi.corrigendum-source:{assertion_digest}",
        jurisdiction="fi",
        artifact_id=artifact_id,
        source_lane=witness.source_lane,
        assertion_kind="finland_corrigendum_pdf_manifest_record",
        acquisition_status="source_manifest_recorded",
        witness=witness,
        detail={
            "statute_id": str(record.get("statute_id") or ""),
            "amendment_id": str(record.get("amendment_id") or ""),
            "pdf_name": str(record.get("pdf_name") or ""),
            "source_pdf": str(record.get("source_pdf") or ""),
            "date_status": str(record.get("date_status") or ""),
            "digest_available": bool(digest),
            "manifest_source": "finland_corrigendum_pdf_sources",
        },
    )


def _corrigendum_sources_completeness_status(
    *,
    pdf_count: int,
    missing_date_count: int,
    date_status_counts: Mapping[str, Any],
    mode: str,
    manifest_kind: str,
) -> SourceCompletenessStatus | None:
    if pdf_count <= 0:
        return None
    dates_available = max(pdf_count - missing_date_count, 0)
    return SourceCompletenessStatus(
        jurisdiction="fi",
        statute_id="corrigendum_source_manifest",
        chain_length=pdf_count,
        source_available=pdf_count,
        dates_available=dates_available,
        owner_phase="source_acquisition",
        detail={
            "manifest_kind": manifest_kind,
            "mode": mode,
            "date_status_counts": dict(date_status_counts),
        },
    )

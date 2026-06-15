"""New Zealand projections into the shared frontier work-item contract.

This adapter converts the diagnostic instruction-workqueue rows (see
``instruction_workqueue.py``) whose ``queue_status`` is ``blocked`` or
``review`` into explicit, reviewable :class:`FrontierWorkItem` packets.

The workqueue already carries the raw witnesses (stable ``row_id``, blocking
rule id, amending-provision hrefs, payload snippets, old/new text, and the
latest-oracle text/target witnesses). This module does not re-extract any of
that: it routes those witnesses into the shared frontier shape and adds the
manual-frontier enrichment the roadmap Phase 7 requires --- probable
adjudication options, whether the source enumerates a finite (exhaustive)
choice set, locally-available official guidance references, an explicit
adjudication prompt, and a concrete next action.

Frontier rows are deliberately non-executable. They describe useful manual
work; they never authorize replay or lower a candidate operation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_witness import source_witness_from_mapping
from lawvm.new_zealand.instruction_workqueue import (
    NZInstructionWorkQueueReport,
    NZInstructionWorkQueueRow,
    build_archived_work_instruction_workqueue,
)


_FRONTIER_QUEUE_STATUSES: frozenset[str] = frozenset({"blocked", "review"})

_NZ_REQUIRED_CLAIM_KIND = "nz_instruction_semantic_compile"

_NZ_FRONTIER_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "nz_frontier_work_item_as_replay_authorization",
    "nz_frontier_work_item_as_canonical_operation",
    "nz_latest_oracle_text_as_payload_authority",
    "nz_obvious_before_after_diff_as_mutation_boundary_proof",
)

_NZ_FRONTIER_SAFE_DEFAULT = (
    "treat_nz_frontier_work_item_as_blocked_non_executable_review_work"
)

# Locally-available official-source references for NZ manual compilation. These
# are stable pointers a reviewer can consult without leaving the corpus; they do
# not assert any particular adjudication.
_NZ_BASELINE_GUIDANCE_REFS: tuple[str, ...] = (
    "nz:guidance:legislation_api_v0_history_note_schema",
    "nz:guidance:nz_legislation_xml_amend_in_structure_notes",
    "nz:guidance:pco_drafting_amendment_instruction_conventions",
)

# Per-frontier-family adjudication packets. Each entry supplies:
#   - candidate_operation_family: the operation a resolved claim would lower to;
#   - probable_options: the likely manual adjudication choices a reviewer picks
#       between;
#   - exhaustive_options_available: True only when the source text enumerates a
#       finite, closed set of choices (so the reviewer is selecting, not
#       inventing);
#   - required_validator_checks: the proofs a future claim must satisfy;
#   - adjudication_prompt: the human-facing question the row poses;
#   - next_action: the concrete next reviewer step.
#
# The keyed family is the workqueue ``payload_structural_subfamily`` (most
# specific) or the ``instruction_semantic_candidate_family`` fallback.
_NZ_FRONTIER_FAMILY_PACKETS: Mapping[str, Mapping[str, Any]] = {
    "direct_text_insert_payload": {
        "candidate_operation_family": "inline_text_insert",
        "probable_options": (
            "inline_text_insert_after_anchor",
            "structural_child_insert_after_anchor",
        ),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_exact_after_anchor_and_inserted_text",
            "claim_identifies_exact_text_anchor_in_live_target",
            "claim_preserves_unclaimed_surrounding_text",
            "changed_paths_are_within_declared_insertion_boundary",
        ),
        "adjudication_prompt": (
            "Is this an inline text insertion after a named anchor, or a "
            "structural child insertion? Identify the exact anchor and payload."
        ),
        "next_action": (
            "Confirm the after-anchor and inserted text from the source "
            "snippet, then record a reviewed inline-insert claim for dry-run."
        ),
    },
    "section_after_insert_payload": {
        "candidate_operation_family": "structural_sibling_insert",
        "probable_options": ("structural_section_insert_after_anchor",),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_anchor_section_and_inserted_sections",
            "claim_identifies_exact_anchor_section_and_parent_container",
            "claim_materializes_each_inserted_section_as_structural_unit",
            "claim_preserves_anchor_and_unclaimed_siblings",
        ),
        "adjudication_prompt": (
            "Which existing section is the after-anchor, and what is the exact "
            "structural payload of each inserted section?"
        ),
        "next_action": (
            "Resolve the anchor section and inserted section boundaries, then "
            "record a reviewed structural-insert claim for dry-run."
        ),
    },
    "subsection_after_insert_payload": {
        "candidate_operation_family": "structural_sibling_insert",
        "probable_options": ("structural_subsection_insert_after_anchor",),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_anchor_subsection_and_inserted_subsections",
            "claim_identifies_exact_anchor_subsection_and_parent_section",
            "claim_materializes_each_inserted_subsection_as_structural_unit",
            "claim_preserves_anchor_and_unclaimed_siblings",
        ),
        "adjudication_prompt": (
            "Which existing subsection is the after-anchor, and what is the "
            "exact structural payload of each inserted subsection?"
        ),
        "next_action": (
            "Resolve the anchor subsection and inserted subsection boundaries, "
            "then record a reviewed structural-insert claim for dry-run."
        ),
    },
    "paragraph_after_insert_payload": {
        "candidate_operation_family": "structural_sibling_insert",
        "probable_options": ("structural_paragraph_insert_after_anchor",),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_anchor_paragraph_and_inserted_paragraphs",
            "claim_identifies_exact_anchor_paragraph_and_parent",
            "claim_materializes_each_inserted_paragraph_as_structural_unit",
            "claim_preserves_anchor_and_unclaimed_siblings",
        ),
        "adjudication_prompt": (
            "Which existing paragraph is the after-anchor, and what is the "
            "exact structural payload of each inserted paragraph?"
        ),
        "next_action": (
            "Resolve the anchor paragraph and inserted paragraph boundaries, "
            "then record a reviewed structural-insert claim for dry-run."
        ),
    },
    "cross_heading_insert_payload": {
        "candidate_operation_family": "cross_heading_insert",
        "probable_options": ("cross_heading_and_following_provisions_insert",),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_names_cross_heading_and_following_payload",
            "claim_identifies_exact_heading_anchor_and_parent_container",
            "claim_materializes_heading_and_provisions_as_structural_units",
            "claim_preserves_anchor_and_unclaimed_siblings",
        ),
        "adjudication_prompt": (
            "What is the inserted cross-heading text, and which provisions "
            "belong under it relative to the named anchor?"
        ),
        "next_action": (
            "Resolve the heading anchor and the full following payload, then "
            "record a reviewed cross-heading-insert claim for dry-run."
        ),
    },
    "definition_alphabetical_insert_payload": {
        "candidate_operation_family": "definition_entry_insert",
        "probable_options": ("definition_entry_insert_alphabetical_order",),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_names_inserted_definition_entry",
            "claim_identifies_target_definition_list_surface",
            "claim_supplies_alphabetical_insertion_index_or_anchor",
            "inserted_term_is_not_already_present_at_effective_preimage",
            "claim_preserves_unclaimed_definition_entries",
        ),
        "adjudication_prompt": (
            "What is the complete inserted definition entry, and where does it "
            "fall in the target's alphabetical definition order?"
        ),
        "next_action": (
            "Resolve the definition list surface and alphabetical insertion "
            "index, then record a reviewed definition-insert claim for dry-run."
        ),
    },
    "direct_replace_payload": {
        "candidate_operation_family": "structural_child_range_substitution",
        "probable_options": (
            "structural_child_substitution",
            "whole_provision_substitution",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_names_replaced_unit_and_replacement_payload",
            "claim_identifies_each_removed_child_unit",
            "claim_materializes_replacement_payload_as_structural_units",
            "claim_preserves_unclaimed_units_and_parent_text",
        ),
        "adjudication_prompt": (
            "Is this a child-range structural substitution or a whole-provision "
            "substitution? Identify the removed units and replacement payload."
        ),
        "next_action": (
            "Resolve the replaced-unit boundary and replacement payload shape, "
            "then record a reviewed substitution claim for dry-run."
        ),
    },
    "multi_section_replace_payload": {
        "candidate_operation_family": "structural_child_range_substitution",
        "probable_options": ("multi_section_structural_substitution",),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_each_replaced_section_and_replacement",
            "claim_identifies_each_removed_section_unit",
            "claim_materializes_each_replacement_section_as_structural_unit",
            "claim_preserves_unclaimed_sibling_sections",
        ),
        "adjudication_prompt": (
            "Which sections are replaced, and what is the exact structural "
            "payload of each replacement section?"
        ),
        "next_action": (
            "Resolve each replaced section and replacement boundary, then "
            "record a reviewed multi-section substitution claim for dry-run."
        ),
    },
    "whole_provision_substitution_payload": {
        "candidate_operation_family": "whole_provision_substitution",
        "probable_options": ("whole_provision_repeal_and_substitute",),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "source_witness_names_repealed_provision_and_substitute_payload",
            "claim_identifies_exact_repealed_provision_boundary",
            "claim_materializes_substitute_payload_as_structural_units",
            "claim_preserves_unclaimed_sibling_provisions",
        ),
        "adjudication_prompt": (
            "Which provision is repealed, and what is the exact structural "
            "payload of the substituted provision?"
        ),
        "next_action": (
            "Resolve the repealed-provision boundary and substitute payload, "
            "then record a reviewed whole-provision substitution claim for "
            "dry-run."
        ),
    },
    "mixed_text_and_structural_insert_payload": {
        "candidate_operation_family": "mixed_text_and_structural_split",
        "probable_options": (
            "split_into_inline_text_op_and_structural_insert_op",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_carries_both_text_and_structural_instruction",
            "claim_splits_inline_text_operation_from_structural_insert",
            "claim_identifies_each_mutated_text_and_structural_surface",
            "claim_preserves_unclaimed_parent_and_sibling_text",
        ),
        "adjudication_prompt": (
            "How does this payload split into a separate inline-text operation "
            "and a structural-insert operation?"
        ),
        "next_action": (
            "Split the payload into the inline-text and structural-insert "
            "operations, then record reviewed claims for each for dry-run."
        ),
    },
    "mixed_repeal_substitute_payload": {
        "candidate_operation_family": "mixed_repeal_substitute_split",
        "probable_options": (
            "split_into_repeal_op_and_structural_substitute_op",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_carries_both_repeal_and_substitute_instruction",
            "claim_splits_repeal_operation_from_structural_substitution",
            "claim_identifies_each_repealed_and_substituted_surface",
            "claim_preserves_unclaimed_parent_and_sibling_units",
        ),
        "adjudication_prompt": (
            "How does this payload split into a repeal operation and a "
            "structural-substitution operation?"
        ),
        "next_action": (
            "Split the payload into the repeal and substitution operations, "
            "then record reviewed claims for each for dry-run."
        ),
    },
    "schedule_indirection_payload": {
        "candidate_operation_family": "schedule_indirection_resolution",
        "probable_options": (
            "resolve_schedule_indirection_to_direct_instructions",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "source_witness_names_schedule_carrying_the_amendments",
            "claim_resolves_each_schedule_row_to_a_direct_instruction",
            "claim_identifies_each_resolved_target_and_payload",
            "claim_blocks_replay_until_schedule_rows_are_resolved",
        ),
        "adjudication_prompt": (
            "Which schedule carries the amendment instructions, and what are "
            "the resolved direct instructions it indirects to?"
        ),
        "next_action": (
            "Resolve the schedule rows into direct instructions, then record "
            "reviewed claims for each resolved instruction for dry-run."
        ),
    },
    "incorporated_amendment_stub_payload": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "probable_options": (
            "acquire_full_amendment_instruction_from_official_source",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "official_source_witness_contains_full_amendment_instruction",
            "payload_or_instruction_witness_is_not_a_stub",
            "claim_blocks_replay_until_full_instruction_is_available",
        ),
        "adjudication_prompt": (
            "The payload is an incorporated-amendment stub. What is the full "
            "amendment instruction from the official source?"
        ),
        "next_action": (
            "Acquire the full amendment instruction from the official source, "
            "then re-classify the row before recording any claim."
        ),
    },
    "historical_inserted_note_payload": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "probable_options": (
            "classify_as_historical_note_no_direct_mutation",
        ),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "claim_identifies_historical_inserted_note_semantics",
            "claim_confirms_no_direct_current_text_mutation",
            "claim_preserves_affected_provision_text_state",
        ),
        "adjudication_prompt": (
            "Is this a historical inserted-note residue with no direct current "
            "mutation, or does it carry a live instruction?"
        ),
        "next_action": (
            "Confirm the note is historical with no live mutation, then record "
            "a reviewed non-textual finding (no replay)."
        ),
    },
    "ambiguous_amend_replace_payload": {
        "candidate_operation_family": "source_target_reconciliation",
        "probable_options": (
            "inline_text_substitution",
            "structural_child_substitution",
        ),
        "exhaustive_options_available": False,
        "required_validator_checks": (
            "claim_disambiguates_amend_versus_replace_semantics",
            "claim_identifies_exact_text_or_structural_preimage",
            "claim_identifies_exact_replacement_payload",
            "claim_blocks_replay_until_amend_replace_ambiguity_is_resolved",
        ),
        "adjudication_prompt": (
            "Is the amend/replace payload an inline text substitution or a "
            "structural substitution? Identify the exact preimage."
        ),
        "next_action": (
            "Disambiguate the amend/replace semantics and identify the exact "
            "preimage, then record a reviewed claim for dry-run."
        ),
    },
    "retrospective_incorporated_note": {
        "candidate_operation_family": "retrospective_incorporated_note_review",
        "probable_options": (
            "confirm_retrospective_incorporation_already_in_current_text",
            "flag_retrospective_incorporation_as_pending",
        ),
        "exhaustive_options_available": True,
        "required_validator_checks": (
            "claim_identifies_retrospective_incorporation_semantics",
            "claim_confirms_whether_current_text_already_incorporates_change",
            "claim_routes_effect_to_temporal_applicability_model_if_pending",
        ),
        "adjudication_prompt": (
            "Has this retrospectively-incorporated amendment already been "
            "folded into the current text, or is it still pending?"
        ),
        "next_action": (
            "Compare the current oracle text against the noted change and "
            "record a reviewed incorporation-status finding (no replay)."
        ),
    },
}

_NZ_FRONTIER_DEFAULT_PACKET: Mapping[str, Any] = {
    "candidate_operation_family": "unclassified_nz_manual_frontier",
    "probable_options": ("classify_frontier_family_before_adjudication",),
    "exhaustive_options_available": False,
    "required_validator_checks": (
        "claim_classifies_frontier_family_before_replay",
        "claim_identifies_source_target_and_payload_dimensions",
        "claim_blocks_replay_until_authorization_family_is_named",
    ),
    "adjudication_prompt": (
        "This blocked/review row is not yet routed to a known NZ frontier "
        "family. What is its source/target/payload classification?"
    ),
    "next_action": (
        "Classify the frontier family from the source and payload witnesses, "
        "then route the row to a specific adjudication packet."
    ),
}

_NZ_REQUIRED_PROOFS: tuple[str, ...] = (
    "frontier_family_classification",
    "source_instruction_semantics_proof",
    "target_resolution_proof",
    "canonical_operation_compilation",
    "mutation_boundary_proof",
)


def nz_frontier_work_item_from_workqueue_row(
    row: NZInstructionWorkQueueRow,
    *,
    work_id: str,
) -> FrontierWorkItem:
    """Project one blocked/review NZ workqueue row as a frontier work item.

    The caller is responsible for filtering to ``blocked``/``review`` rows; this
    function will project any row but is only meaningful for those statuses.
    """

    frontier_family = _frontier_family(row)
    packet = _NZ_FRONTIER_FAMILY_PACKETS.get(
        frontier_family, _NZ_FRONTIER_DEFAULT_PACKET
    )
    source_artifact_id = work_id or "new_zealand_instruction_workqueue"
    source_unit_id = row.operation_row_id or row.row_id
    work_item_id = f"nz-frontier-{source_artifact_id}-{row.row_id}"

    source_witness = _source_witness(
        row,
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
    )
    target_witness = _target_witness(row)
    compare_witness = _compare_witness(row)
    candidate_targets = (row.target_address,) if row.target_address else ()

    probable_options = tuple(packet.get("probable_options", ()))
    exhaustive = bool(packet.get("exhaustive_options_available", False))
    candidate_operation_family = str(packet.get("candidate_operation_family") or "")
    required_validator_checks = tuple(packet.get("required_validator_checks", ()))

    detail: dict[str, Any] = {
        "queue_status": row.queue_status,
        "operation_family": row.operation_family,
        "effect_readiness_status": row.effect_readiness_status,
        "effect_readiness_row_id": row.effect_readiness_row_id,
        "instruction_semantic_candidate_status": (
            row.instruction_semantic_candidate_status
        ),
        "instruction_semantic_candidate_family": (
            row.instruction_semantic_candidate_family
        ),
        "instruction_semantic_rule_id": row.instruction_semantic_rule_id,
        "payload_instruction_shape": row.payload_instruction_shape,
        "payload_instruction_safety": row.payload_instruction_safety,
        "payload_structural_subfamily_status": (
            row.payload_structural_subfamily_status
        ),
        "payload_structural_subfamily": row.payload_structural_subfamily,
        "payload_structural_subfamily_rule_id": (
            row.payload_structural_subfamily_rule_id
        ),
        "probable_options": list(probable_options),
        "exhaustive_options_available": exhaustive,
        "adjudication_prompt": str(packet.get("adjudication_prompt") or ""),
        "next_action": str(packet.get("next_action") or ""),
        "adjudication_options_not_replay_authorization": True,
    }
    if row.explicit_target_citation:
        detail["explicit_target_citation"] = row.explicit_target_citation
    if row.target_citation_status:
        detail["target_citation_status"] = row.target_citation_status

    return FrontierWorkItem(
        work_item_id=work_item_id,
        jurisdiction="nz",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=source_witness,
        target_witness=target_witness,
        compare_witness=compare_witness,
        owner_phase="nz_instruction_semantics",
        frontier_family=frontier_family,
        frontier_status=row.queue_status,
        candidate_operation_family=candidate_operation_family,
        candidate_targets=candidate_targets,
        guidance_refs=_NZ_BASELINE_GUIDANCE_REFS,
        required_claim_kind=_NZ_REQUIRED_CLAIM_KIND,
        required_validator_checks=required_validator_checks,
        required_proofs=_NZ_REQUIRED_PROOFS,
        safe_default=_NZ_FRONTIER_SAFE_DEFAULT,
        forbidden_shortcuts=_NZ_FRONTIER_FORBIDDEN_SHORTCUTS,
        executable=False,
        replay_authorized=False,
        authorization_status="nz_frontier_work_item_non_executable",
        detail=detail,
    )


def frontier_work_items(
    report: NZInstructionWorkQueueReport,
) -> tuple[FrontierWorkItem, ...]:
    """Project all blocked/review workqueue rows into frontier work items."""

    return tuple(
        nz_frontier_work_item_from_workqueue_row(row, work_id=report.work_id)
        for row in report.rows
        if row.queue_status in _FRONTIER_QUEUE_STATUSES
    )


def frontier_work_items_summary(
    work_items: tuple[FrontierWorkItem, ...],
) -> dict[str, Any]:
    """Summarize a frontier work-item set for compact (non-JSON) reporting."""

    frontier_family_counts: dict[str, int] = {}
    candidate_operation_family_counts: dict[str, int] = {}
    frontier_status_counts: dict[str, int] = {}
    exhaustive_options_count = 0
    for item in work_items:
        frontier_family_counts[item.frontier_family] = (
            frontier_family_counts.get(item.frontier_family, 0) + 1
        )
        candidate_operation_family_counts[item.candidate_operation_family] = (
            candidate_operation_family_counts.get(
                item.candidate_operation_family, 0
            )
            + 1
        )
        frontier_status_counts[item.frontier_status] = (
            frontier_status_counts.get(item.frontier_status, 0) + 1
        )
        if bool(item.detail.get("exhaustive_options_available")):
            exhaustive_options_count += 1
    return {
        "frontier_work_item_count": len(work_items),
        "frontier_family_counts": dict(sorted(frontier_family_counts.items())),
        "candidate_operation_family_counts": dict(
            sorted(candidate_operation_family_counts.items())
        ),
        "frontier_status_counts": dict(sorted(frontier_status_counts.items())),
        "exhaustive_options_available_count": exhaustive_options_count,
        "replay_claims": False,
        "canonical_effect_claims": False,
    }


def _frontier_family(row: NZInstructionWorkQueueRow) -> str:
    """Pick the most specific frontier family for an NZ workqueue row."""

    if row.payload_structural_subfamily:
        return row.payload_structural_subfamily
    if row.instruction_semantic_candidate_family:
        return row.instruction_semantic_candidate_family
    return "nz_unclassified_manual_frontier"


def _source_witness(
    row: NZInstructionWorkQueueRow,
    *,
    source_artifact_id: str,
    source_unit_id: str,
) -> Mapping[str, Any]:
    preview = " | ".join(snippet for snippet in row.payload_text_snippets if snippet)
    witness_seed: dict[str, Any] = {
        "source_role": "nz_amending_instruction_source",
        "artifact_id": row.amending_work_id or source_artifact_id,
        "source_unit_id": source_unit_id,
        "amending_work_id": row.amending_work_id,
        "amending_provision_hrefs": list(row.amending_provision_hrefs),
        "payload_match_headings": list(row.payload_match_headings),
        "payload_text_snippets": list(row.payload_text_snippets),
        "instruction_clause_count": row.instruction_clause_count,
    }
    if preview:
        witness_seed["text_preview"] = preview
    if row.old_text:
        witness_seed["old_text"] = row.old_text
    if row.new_text:
        witness_seed["new_text"] = row.new_text
    if row.text_substitution_scope:
        witness_seed["text_substitution_scope"] = row.text_substitution_scope
    return source_witness_from_mapping(
        witness_seed,
        default_role="nz_amending_instruction_source",
        default_artifact_id=source_artifact_id,
        default_source_unit_id=source_unit_id,
    ).to_dict()


def _target_witness(row: NZInstructionWorkQueueRow) -> Mapping[str, Any]:
    witness: dict[str, Any] = {
        "surface": "nz_effect_feed_target_address",
        "target_address": row.target_address,
        "operation_family": row.operation_family,
    }
    if row.latest_oracle_target_resolution_status:
        witness["latest_oracle_target_resolution_status"] = (
            row.latest_oracle_target_resolution_status
        )
    if row.latest_oracle_target_resolution_rule_id:
        witness["latest_oracle_target_resolution_rule_id"] = (
            row.latest_oracle_target_resolution_rule_id
        )
    if row.latest_oracle_target_source_path:
        witness["latest_oracle_target_source_path"] = list(
            row.latest_oracle_target_source_path
        )
        witness["latest_oracle_target_not_replay_authorization"] = True
    return witness


def _compare_witness(row: NZInstructionWorkQueueRow) -> Mapping[str, Any]:
    if not row.latest_oracle_text_status:
        return {}
    witness: dict[str, Any] = {
        "surface": "nz_latest_oracle_text_presence",
        "latest_oracle_text_status": row.latest_oracle_text_status,
        "latest_oracle_text_rule_id": row.latest_oracle_text_rule_id,
        "latest_oracle_old_text_occurrences": (
            row.latest_oracle_old_text_occurrences
        ),
        "latest_oracle_new_text_occurrences": (
            row.latest_oracle_new_text_occurrences
        ),
        "latest_oracle_text_not_payload_authority": True,
    }
    return witness


def _filtered_work_items(
    work_items: tuple[FrontierWorkItem, ...],
    *,
    frontier_status: str = "",
    frontier_family: str = "",
    candidate_operation_family: str = "",
) -> tuple[FrontierWorkItem, ...]:
    filtered = work_items
    if frontier_status:
        filtered = tuple(
            item for item in filtered if item.frontier_status == frontier_status
        )
    if frontier_family:
        filtered = tuple(
            item for item in filtered if item.frontier_family == frontier_family
        )
    if candidate_operation_family:
        filtered = tuple(
            item
            for item in filtered
            if item.candidate_operation_family == candidate_operation_family
        )
    return filtered


def _frontier_filters(
    *,
    frontier_status: str,
    frontier_family: str,
    candidate_operation_family: str,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "frontier_status": frontier_status,
            "frontier_family": frontier_family,
            "candidate_operation_family": candidate_operation_family,
        }.items()
        if value
    }


def main(args: Any) -> None:
    """CLI entry point for ``lawvm nz-corpus frontier``.

    Emits non-executable frontier work items for one archived work. Frontier
    rows describe reviewable manual work; they never authorize replay.
    """

    report = build_archived_work_instruction_workqueue(Path(args.db), args.work_id)
    work_items = frontier_work_items(report)
    frontier_status = getattr(args, "frontier_status", "") or ""
    frontier_family = getattr(args, "frontier_family", "") or ""
    candidate_operation_family = getattr(args, "candidate_operation_family", "") or ""
    filtered = _filtered_work_items(
        work_items,
        frontier_status=frontier_status,
        frontier_family=frontier_family,
        candidate_operation_family=candidate_operation_family,
    )
    filters = _frontier_filters(
        frontier_status=frontier_status,
        frontier_family=frontier_family,
        candidate_operation_family=candidate_operation_family,
    )
    limit = getattr(args, "limit", None)
    summary_only = bool(getattr(args, "summary_only", False))

    if getattr(args, "json", False):
        selected = filtered if limit is None else filtered[:limit]
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "frontier_work_items",
            "truth_claim": "non_executable_frontier_work_item_projections",
            "replay_claims": False,
            "canonical_effect_claims": False,
            "work_id": report.work_id,
            "summary": frontier_work_items_summary(work_items),
            "filters": filters,
            "filtered_summary": frontier_work_items_summary(filtered),
        }
        if not summary_only:
            payload["rows"] = [item.to_dict() for item in selected]
            if limit is not None and len(filtered) > limit:
                payload["rows_truncated"] = True
                payload["rows_omitted"] = len(filtered) - limit
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    summary = frontier_work_items_summary(work_items)
    print(
        f"work_id={report.work_id} "
        f"frontier_work_items={summary['frontier_work_item_count']} "
        f"filtered={len(filtered)} filters={filters} "
        f"frontier_status_counts={summary['frontier_status_counts']} "
        f"frontier_family_counts={summary['frontier_family_counts']} "
        f"exhaustive_options_available={summary['exhaustive_options_available_count']}"
    )
    if summary_only:
        return
    selected = filtered if limit is None else filtered[:limit]
    for item in selected:
        target = (item.candidate_targets[0] if item.candidate_targets else "-")
        print(
            f"{item.work_item_id}\t{item.frontier_status}\t{item.frontier_family}\t"
            f"{item.candidate_operation_family}\t{target}\t"
            f"next_action={item.detail.get('next_action', '')}"
        )
    if limit is not None and len(filtered) > limit:
        print(f"... {len(filtered) - limit} more")

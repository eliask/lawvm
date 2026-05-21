"""Non-executable UK semantic-claim templates for manual frontier review."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from lawvm.uk_legislation.manual_claim_templates import (
    UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS,
)

UK_CLAIM_TEMPLATE_RULE_IDS = UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS


def _quoted_for_substitute_pair(source_preview: str) -> tuple[str, str]:
    """Return the quoted preimage/replacement pair from a simple formula."""
    replacement_match = re.search(
        r"\bfor\b.{0,240}?[\"“](?P<old>[^\"”]{1,240})[\"”]\s+substitute\s+[\"“](?P<new>[^\"”]{1,240})[\"”]",
        " ".join(source_preview.split()),
        flags=re.I,
    )
    if replacement_match is None:
        return "", ""
    return (
        " ".join(replacement_match.group("old").split()),
        " ".join(replacement_match.group("new").split()),
    )


def _range_to_container_replacement_sections(
    payload_roots: Any,
) -> tuple[dict[str, str], ...]:
    """Return bounded replacement section labels from range-to-container payload evidence."""
    sections: list[dict[str, str]] = []
    for root in payload_roots or ():
        if not isinstance(root, dict):
            continue
        root_sections = root.get("descendant_sections") or ()
        for section in root_sections:
            if not isinstance(section, dict):
                continue
            sections.append(
                {
                    "label": str(section.get("label") or ""),
                    "eid": str(section.get("eid") or ""),
                }
            )
    return tuple(sections)


def _surface_text_rewrite_claim_template(
    *,
    statute_id: str,
    row: Any,
    action_family: str,
    facet_family: str,
    placement_family: str,
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    text_match, replacement = _quoted_for_substitute_pair(source_preview)
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "facet_family": facet_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "text_match": text_match,
        "replacement": replacement,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def _bounded_mutation_claim_template(
    *,
    statute_id: str,
    row: Any,
    action_family: str,
    placement_family: str,
    required_ownership: list[str],
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "required_ownership": required_ownership,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def manual_compile_suggested_claim_template(
    *,
    statute_id: str,
    row: Any,
) -> dict[str, Any]:
    """Return a non-executable semantic-claim template for known manual families."""
    summary = row.summary
    effect = row.effect
    if summary.manual_compile_rule_id == "uk_manual_frontier_heading_facet_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="facet_text_rewrite",
            facet_family="heading_or_title",
            placement_family="explicit_facet_target_required",
            required_validator_checks=[
                "source_witness_targets_heading_title_or_sidenote_facet",
                "claim_identifies_exact_target_facet_not_host_body",
                "claim_preserves_host_body_text_and_children",
                "claim_text_preimage_matches_target_facet_surface",
                "changed_paths_are_within_declared_facet_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_crossheading_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="crossheading_text_rewrite",
            facet_family="crossheading",
            placement_family="explicit_crossheading_carrier_required",
            required_validator_checks=[
                "source_witness_targets_crossheading_surface",
                "claim_identifies_exact_crossheading_carrier",
                "claim_preserves_neighbouring_sections_and_body_text",
                "claim_text_preimage_matches_crossheading_surface",
                "changed_paths_are_within_declared_crossheading_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_note_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_note_text_rewrite",
            facet_family="schedule_note",
            placement_family="explicit_schedule_note_carrier_required",
            required_validator_checks=[
                "source_witness_targets_schedule_note_surface",
                "claim_identifies_exact_schedule_note_carrier",
                "claim_preserves_schedule_paragraph_body_structure",
                "claim_text_preimage_matches_schedule_note_surface",
                "changed_paths_are_within_declared_schedule_note_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_list_entry_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_list_entry_mutation",
            placement_family="entry_anchor_requires_carrier_claim",
            required_ownership=[
                "source_named_entry_anchor",
                "entry_carrier",
                "sibling_insertion_or_replacement_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_schedule_or_list_entry_anchor",
                "claim_identifies_exact_entry_carrier",
                "claim_identifies_predecessor_or_replaced_entry",
                "claim_preserves_unclaimed_sibling_entries",
                "changed_paths_are_within_claimed_entry_boundary",
            ],
        )
    if summary.manual_compile_rule_id in {
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_column_insert_candidate",
        "uk_manual_frontier_table_appropriate_place_candidate",
    }:
        placement_family_by_rule = {
            "uk_manual_frontier_table_entry_candidate": "table_entry_anchor_required",
            "uk_manual_frontier_table_entry_deictic_candidate": "deictic_table_entry_anchor_required",
            "uk_manual_frontier_table_column_insert_candidate": "table_column_boundary_required",
            "uk_manual_frontier_table_appropriate_place_candidate": "appropriate_place_table_entry_requires_ordering_claim",
        }
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_surface_mutation",
            placement_family=placement_family_by_rule[summary.manual_compile_rule_id],
            required_ownership=[
                "source_named_table_surface",
                "row_or_column_carrier",
                "cell_alignment_or_column_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_table_entry_or_column_surface",
                "claim_identifies_exact_table_carrier",
                "claim_identifies_row_or_column_boundary",
                "claim_preserves_unclaimed_rows_columns_and_cells",
                "changed_paths_are_within_claimed_table_surface",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_appropriate_place_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="appropriate_place_mutation",
            placement_family="appropriate_place_requires_anchor_claim",
            required_ownership=[
                "source_named_insertion_payload",
                "validated_predecessor_or_successor_anchor",
                "target_container_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_uses_appropriate_place_formula",
                "claim_supplies_exact_anchor_or_ordering_rule",
                "claim_identifies_target_container_surface",
                "claim_identifies_payload_units_owned_by_source",
                "claim_preserves_unclaimed_sibling_units",
                "changed_paths_are_within_claimed_insertion_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_structural_sibling_insert_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="structural_sibling_insert",
            placement_family="source_named_sibling_anchor_required",
            required_ownership=[
                "source_named_sibling_anchor",
                "inserted_sibling_payload",
                "sibling_order_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_before_or_after_sibling_anchor",
                "claim_identifies_exact_parent_and_anchor_sibling",
                "claim_identifies_each_inserted_sibling_payload",
                "claim_preserves_anchor_and_unclaimed_siblings",
                "changed_paths_are_within_declared_sibling_insertion_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_amendment_program_target_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="amendment_program_target_mutation",
            placement_family="inserted_parent_instruction_context_required",
            required_ownership=[
                "source_amendment_program_context",
                "inserted_parent_instruction",
                "derived_child_target_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_text_inserted_by_same_amending_program",
                "claim_identifies_the_parent_instruction_that_created_the_target",
                "claim_identifies_exact_inserted_parent_or_child_boundary",
                "claim_preserves_unclaimed_inserted_payload_and_live_target_text",
                "changed_paths_are_within_declared_amendment_program_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_repeal_table_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_repeal_or_omission",
            placement_family="source_named_table_or_row_boundary_required",
            required_ownership=[
                "source_named_table_or_row_surface",
                "repealed_row_column_or_cell_boundary",
                "unclaimed_table_surface_preservation",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_table_repeal_or_omission",
                "claim_identifies_exact_table_carrier",
                "claim_identifies_every_repealed_row_column_or_cell",
                "claim_preserves_unclaimed_table_rows_columns_and_cells",
                "changed_paths_are_within_declared_table_repeal_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_multi_subunit_text_rewrite",
            placement_family="source_named_child_units_required",
            required_ownership=[
                "source_named_child_unit_set",
                "per_child_text_preimage",
                "per_child_replacement_or_repeal_payload",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_each_child_unit_to_mutate",
                "claim_splits_the_parent_formula_into_bounded_child_operations",
                "claim_text_preimage_matches_each_declared_child_surface",
                "claim_preserves_unclaimed_child_units_and_parent_text",
                "changed_paths_are_within_declared_child_unit_boundaries",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_child_tail_text_rewrite",
            placement_family="source_named_child_tail_required",
            required_ownership=[
                "source_named_child_anchor",
                "tail_text_preimage_or_repeal_scope",
                "replacement_or_repeal_payload",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_the_child_anchor_and_tail_scope",
                "claim_targets_only_the_tail_text_following_that_child",
                "claim_text_preimage_matches_the_declared_tail_surface",
                "claim_preserves_child_body_and_unclaimed_parent_text",
                "changed_paths_are_within_declared_child_tail_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_structured_text_patch_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_structured_text_patch",
            placement_family="parent_formula_anchor_with_structured_payload_required",
            required_ownership=[
                "source_parent_formula_anchor",
                "source_carried_payload_units",
                "child_target_boundaries",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_contains_parent_formula_and_structured_payload",
                "claim_binds_payload_units_to_named_child_targets",
                "claim_preserves_unclaimed_parent_and_sibling_text",
                "claim_rejects_flattening_structured_payload_into_host_text",
                "changed_paths_are_within_claimed_child_target_boundaries",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_range_to_container_candidate":
        blocking_rows = tuple(
            row
            for row in summary.lowering_rejections
            if str(row.get("rule_id") or "") == "uk_effect_range_to_container_substitution_rejected"
        )
        detail = dict(blocking_rows[0]) if blocking_rows else {}
        payload_roots = tuple(detail.get("payload_roots") or ())
        replacement_sections = _range_to_container_replacement_sections(payload_roots)
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "range_to_container_substitution",
            "placement_family": "requires_lineage_or_migration_claim",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "source_range_kind": detail.get("source_range_kind", ""),
            "source_range_start": detail.get("source_range_start", ""),
            "source_range_end": detail.get("source_range_end", ""),
            "target_container_surface": detail.get(
                "target_container_ref",
                effect.affected_provisions,
            ),
            "compiled_targets": list(detail.get("compiled_targets") or ()),
            "payload_kinds": list(detail.get("payload_kinds") or ()),
            "payload_roots": list(payload_roots),
            "replacement_section_count": len(replacement_sections),
            "replacement_sections": list(replacement_sections),
            "required_ownership": list(detail.get("required_ownership") or ()),
            "required_validator_checks": [
                "source_witness_contains_range_to_container_substitution",
                "claim_identifies_every_replaced_source_unit_in_range",
                "claim_identifies_container_payload_root_and_all_owned_children",
                "claim_emits_lineage_or_migration_events_for_displaced_units",
                "claim_preserves_crossheading_or_heading_facet_scope",
                "changed_paths_are_within_source_range_or_declared_migration_paths",
            ],
            "executable": False,
        }
    if (
        summary.manual_compile_rule_id
        != "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    ):
        return {}
    source_preview = summary.source_extracted_text_preview or ""
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bat\s+(?:an?|the)\s+appropriate\s+place,?\s+"
        r"(?:in\s+alphabetical\s+order,?\s+)?insert\s*[—–-]\s*(?P<payload>.+)$",
        source_norm,
        flags=re.I | re.S,
    )
    payload = (
        " ".join(match.group("payload").split()).strip()
        if match is not None
        else source_norm
    )
    term_match = re.search(r"[\"“]\s*(?P<term>[^\"”]{1,160}?)\s*[\"”]", payload)
    term = " ".join(str(term_match.group("term") if term_match else "").split()).strip()
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": "definition_entry_insert",
        "placement_family": "appropriate_place_requires_anchor_claim",
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "source_preview_sha256": (
            hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
            if source_preview
            else ""
        ),
        "inserted_definition_term": term,
        "inserted_definition_entry_preview": payload[:500],
        "candidate_target_surface": effect.affected_provisions,
        "required_validator_checks": [
            "source_witness_contains_exact_appropriate_place_instruction",
            "payload_is_complete_definition_entry",
            "claim_supplies_exact_definition_entry_anchor_or_insertion_index",
            "target_subtree_contains_definition_list_surface",
            "inserted_term_is_not_already_present_in_target_at_effective_preimage",
            "changed_paths_remain_inside_claimed_interpretation_target",
        ],
        "executable": False,
    }

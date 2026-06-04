"""UK projections into the shared frontier work-item contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_witness import source_witness_from_mapping


_FRONTIER_FAMILY_DEFAULTS: Mapping[str, Mapping[str, tuple[str, ...] | str]] = {
    "uk_manual_frontier_appropriate_place_definition_entry_candidate": {
        "candidate_operation_family": "definition_entry_insert",
        "required_validator_checks": (
            "source_witness_contains_exact_appropriate_place_instruction",
            "payload_is_complete_definition_entry",
            "claim_supplies_exact_definition_entry_anchor_or_insertion_index",
            "target_subtree_contains_definition_list_surface",
            "inserted_term_is_not_already_present_in_target_at_effective_preimage",
            "changed_paths_remain_inside_claimed_interpretation_target",
        ),
    },
    "uk_manual_frontier_appropriate_place_index_entry_candidate": {
        "candidate_operation_family": "index_entry_insert",
        "required_validator_checks": (
            "source_witness_uses_appropriate_place_formula",
            "payload_is_complete_index_entry",
            "claim_supplies_exact_index_entry_anchor_or_ordering_rule",
            "claim_identifies_target_index_or_list_surface",
            "claim_preserves_unclaimed_index_entries",
            "changed_paths_are_within_claimed_insertion_boundary",
        ),
    },
    "uk_manual_frontier_application_by_reference_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_application_by_reference_semantics",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_application_modification_payload_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_application_modification_semantics",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_routes_effect_to_temporal_applicability_model",
        ),
    },
    "uk_manual_frontier_as_if_application_modification_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_as_if_application_modification_semantics",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_amendment_program_target_candidate": {
        "candidate_operation_family": "amendment_program_target_mutation",
        "required_validator_checks": (
            "source_witness_targets_text_inserted_by_same_amending_program",
            "claim_identifies_the_parent_instruction_that_created_the_target",
            "claim_identifies_exact_inserted_parent_or_child_boundary",
            "claim_preserves_unclaimed_inserted_payload_and_live_target_text",
            "changed_paths_are_within_declared_amendment_program_target",
        ),
    },
    "uk_manual_frontier_deictic_amendment_program_target_candidate": {
        "candidate_operation_family": "amendment_program_target_mutation",
        "required_validator_checks": (
            "source_witness_targets_text_inserted_by_same_amending_program",
            "claim_identifies_the_parent_instruction_that_created_the_target",
            "claim_proves_as_inserted_anchor_from_source_context",
            "claim_identifies_exact_inserted_parent_or_child_boundary",
            "claim_preserves_unclaimed_inserted_payload_and_live_target_text",
            "changed_paths_are_within_declared_amendment_program_target",
        ),
    },
    "uk_effect_temporal_ceases_to_have_effect_replay_excluded": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_temporal_ceases_to_have_effect_semantics",
            "claim_confirms_no_direct_current_text_replay_mutation",
            "claim_routes_effect_to_temporal_applicability_model",
        ),
    },
    "uk_manual_frontier_amendment_table_payload_without_row_context": {
        "candidate_operation_family": "table_surface_mutation",
        "required_validator_checks": (
            "source_witness_contains_amendment_table_payload",
            "claim_identifies_source_table_row_context",
            "claim_identifies_exact_table_carrier",
            "claim_blocks_replay_until_row_context_is_proved",
            "changed_paths_are_within_claimed_table_surface",
        ),
    },
    "uk_manual_frontier_appropriate_place_candidate": {
        "candidate_operation_family": "appropriate_place_mutation",
        "required_validator_checks": (
            "source_witness_uses_appropriate_place_formula",
            "claim_supplies_exact_anchor_or_ordering_rule",
            "claim_identifies_target_container_surface",
            "claim_identifies_payload_units_owned_by_source",
            "changed_paths_are_within_claimed_insertion_boundary",
        ),
    },
    "uk_manual_frontier_commencement_effect_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_commencement_or_temporal_semantics",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_routes_effect_to_temporal_applicability_model",
        ),
    },
    "uk_manual_frontier_conditional_temporal_repeal_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_conditional_temporal_repeal_semantics",
            "claim_confirms_no_unconditional_current_text_repeal",
            "claim_routes_effect_to_temporal_applicability_model",
        ),
    },
    "uk_manual_frontier_external_act_target_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_external_act_target_named_by_source",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_child_qualified_word_omission_target_mismatch": {
        "candidate_operation_family": "source_target_reconciliation",
        "required_validator_checks": (
            "source_witness_names_child_qualified_omission_target",
            "claim_reconciles_source_child_target_and_effect_feed_target",
            "claim_blocks_replay_until_target_identity_is_proved",
        ),
    },
    "uk_manual_frontier_crossheading_candidate": {
        "candidate_operation_family": "crossheading_text_rewrite",
        "required_validator_checks": (
            "source_witness_targets_crossheading_surface",
            "claim_identifies_exact_crossheading_carrier",
            "claim_preserves_neighbouring_sections_and_body_text",
            "claim_text_preimage_matches_crossheading_surface",
            "changed_paths_are_within_declared_crossheading_target",
        ),
    },
    "uk_manual_frontier_heading_facet_candidate": {
        "candidate_operation_family": "facet_text_rewrite",
        "required_validator_checks": (
            "source_witness_targets_heading_title_or_sidenote_facet",
            "claim_identifies_exact_target_facet_not_host_body",
            "claim_preserves_host_body_text_and_children",
            "claim_text_preimage_matches_target_facet_surface",
            "changed_paths_are_within_declared_facet_target",
        ),
    },
    "uk_manual_frontier_instruction_header_source_insufficient": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "source_witness_contains_header_only_instruction_context",
            "complete_child_instruction_or_payload_witness_is_available",
            "claim_blocks_replay_until_complete_instruction_is_proved",
        ),
    },
    "uk_manual_frontier_misselected_target_context_source_insufficient": {
        "candidate_operation_family": "source_target_reconciliation",
        "required_validator_checks": (
            "source_witness_matches_effect_feed_target_context",
            "claim_reconciles_source_target_and_feed_target",
            "claim_blocks_replay_until_target_identity_is_proved",
        ),
    },
    "uk_manual_frontier_missing_payload_source_insufficient": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "official_source_witness_contains_payload_or_instruction",
            "payload_or_instruction_witness_is_not_empty",
            "claim_blocks_replay_until_source_payload_is_available",
        ),
    },
    "uk_manual_frontier_non_substantive_payload_source_insufficient": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "source_witness_contains_only_non_substantive_payload",
            "complete_operative_instruction_or_payload_witness_is_available",
            "claim_blocks_replay_until_substantive_payload_is_proved",
        ),
    },
    "uk_manual_frontier_nested_definition_child_structural_substitution_candidate": {
        "candidate_operation_family": "nested_definition_child_structural_substitution",
        "required_validator_checks": (
            "source_witness_names_outer_definition_child_and_nested_child",
            "claim_identifies_exact_nested_definition_child_node",
            "claim_preserves_unclaimed_definition_children",
            "claim_materializes_replacement_payload_as_structural_child_units",
            "changed_paths_are_within_claimed_nested_definition_boundary",
        ),
    },
    "uk_manual_frontier_non_textual_or_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_non_textual_or_unadmitted_replay_semantics",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_repeal_table_candidate": {
        "candidate_operation_family": "table_repeal_or_omission",
        "required_validator_checks": (
            "source_witness_targets_table_repeal_or_omission",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_every_repealed_row_column_or_cell",
            "claim_preserves_unclaimed_table_rows_columns_and_cells",
            "changed_paths_are_within_declared_table_repeal_boundary",
        ),
    },
    "uk_manual_frontier_savings_qualified_text_omission_candidate": {
        "candidate_operation_family": "savings_qualified_text_omission",
        "required_validator_checks": (
            "source_witness_contains_savings_qualified_omission",
            "claim_identifies_exact_reference_text_preimage",
            "claim_represents_savings_condition_as_applicability_not_unconditional_deletion",
            "claim_preserves_occurrences_outside_the_savings_qualified_scope",
            "changed_paths_are_within_declared_text_carriers_and_applicability_scope",
        ),
    },
    "uk_manual_frontier_schedule_note_candidate": {
        "candidate_operation_family": "schedule_note_text_rewrite",
        "required_validator_checks": (
            "source_witness_targets_schedule_note_surface",
            "claim_identifies_exact_schedule_note_carrier",
            "claim_preserves_schedule_paragraph_body_structure",
            "claim_text_preimage_matches_schedule_note_surface",
            "changed_paths_are_within_declared_schedule_note_target",
        ),
    },
    "uk_manual_frontier_schedule_list_entry_candidate": {
        "candidate_operation_family": "schedule_list_entry_mutation",
        "required_validator_checks": (
            "source_witness_targets_schedule_or_list_entry_surface",
            "claim_identifies_exact_schedule_or_list_entry_carrier",
            "claim_supplies_exact_entry_anchor_or_ordering_rule",
            "claim_preserves_unclaimed_schedule_or_list_entries",
            "changed_paths_are_within_claimed_schedule_list_entry_boundary",
        ),
    },
    "uk_manual_frontier_sentence_scoped_repeated_insert_candidate": {
        "candidate_operation_family": "sentence_scoped_repeated_insert",
        "required_validator_checks": (
            "source_witness_names_sentence_scope_and_inserted_text",
            "claim_identifies_each_sentence_boundary_in_effective_preimage",
            "claim_preserves_unselected_sentences_and_surrounding_text",
            "claim_inserts_only_at_declared_sentence_end_boundaries",
            "changed_paths_are_within_declared_sentence_text_carriers",
        ),
    },
    "uk_manual_frontier_source_carried_structured_text_patch_candidate": {
        "candidate_operation_family": "source_carried_structured_text_patch",
        "required_validator_checks": (
            "source_witness_contains_parent_formula_and_structured_payload",
            "claim_binds_payload_units_to_named_child_targets",
            "claim_preserves_unclaimed_parent_and_sibling_text",
            "claim_rejects_flattening_structured_payload_into_host_text",
            "changed_paths_are_within_claimed_child_target_boundaries",
        ),
    },
    "uk_manual_frontier_source_carried_structured_tail_substitution_candidate": {
        "candidate_operation_family": "source_carried_structured_tail_substitution",
        "required_validator_checks": (
            "source_witness_contains_tail_range_and_structured_replacement",
            "claim_identifies_exact_tail_preimage_boundary",
            "claim_materializes_replacement_payload_as_child_units_not_flat_text",
            "claim_preserves_unclaimed_existing_child_units_and_parent_text",
            "changed_paths_are_within_claimed_tail_and_child_payload_boundaries",
        ),
    },
    "uk_manual_frontier_source_pathology_insufficient": {
        "candidate_operation_family": "source_pathology_resolution",
        "required_validator_checks": (
            "official_source_witness_resolves_source_pathology",
            "payload_or_instruction_witness_is_complete",
            "claim_blocks_replay_until_source_pathology_is_resolved",
        ),
    },
    "uk_manual_frontier_source_payload_without_instruction_context": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "source_witness_contains_payload_fragment_without_instruction_context",
            "complete_parent_instruction_context_witness_is_available",
            "claim_blocks_replay_until_instruction_context_is_proved",
        ),
    },
    "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate": {
        "candidate_operation_family": "definition_entry_insert",
        "required_validator_checks": (
            "effect_metadata_names_pseudo_definition_target",
            "payload_is_complete_definition_entry",
            "claim_supplies_exact_definition_entry_anchor_or_insertion_index",
            "target_subtree_contains_definition_list_surface",
            "inserted_term_is_not_already_present_in_target_at_effective_preimage",
            "changed_paths_remain_inside_claimed_interpretation_target",
        ),
    },
    "uk_manual_frontier_table_entry_candidate": {
        "candidate_operation_family": "table_surface_mutation",
        "required_validator_checks": (
            "source_witness_targets_table_entry_or_column_surface",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_row_or_column_boundary",
            "claim_preserves_unclaimed_rows_columns_and_cells",
            "changed_paths_are_within_claimed_table_surface",
        ),
    },
    "uk_manual_frontier_table_appropriate_place_candidate": {
        "candidate_operation_family": "table_surface_mutation",
        "required_validator_checks": (
            "source_witness_targets_table_entry_or_column_surface",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_table_ordering_rule_or_anchor",
            "claim_preserves_unclaimed_rows_columns_and_cells",
            "changed_paths_are_within_claimed_table_surface",
        ),
    },
    "uk_manual_frontier_table_entry_placement_insert": {
        "candidate_operation_family": "table_surface_mutation",
        "required_validator_checks": (
            "source_witness_targets_table_entry_or_column_surface",
            "claim_identifies_exact_table_carrier",
            "claim_identifies_exact_insert_position_within_table_or_list",
            "claim_preserves_unclaimed_rows_columns_and_cells",
            "changed_paths_are_within_claimed_table_surface",
        ),
    },
    "uk_manual_frontier_unsupported_effect_family": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_unsupported_effect_family",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_unclassified": {
        "candidate_operation_family": "unclassified_manual_frontier",
        "required_validator_checks": (
            "claim_classifies_frontier_family_before_replay",
            "claim_identifies_source_target_payload_and_temporal_dimensions",
            "claim_blocks_replay_until_authorization_family_is_named",
        ),
    },
    "uk_manual_frontier_text_patch_preimage_chain_gap": {
        "candidate_operation_family": "source_chain_text_patch",
        "required_validator_checks": (
            "source_chain_contains_missing_preimage_state",
            "claim_identifies_intermediate_amendment_effect",
            "claim_proves_text_patch_preimage_boundary",
            "claim_preserves_effect_feed_target_identity",
            "changed_paths_are_within_text_patch_target",
        ),
    },
    "uk_manual_frontier_text_patch_target_source_chain_gap": {
        "candidate_operation_family": "source_chain_text_patch",
        "required_validator_checks": (
            "source_chain_contains_amendment_created_target",
            "claim_identifies_target_creation_effect",
            "claim_proves_text_patch_preimage_boundary",
            "claim_preserves_created_target_identity",
            "changed_paths_are_within_text_patch_target",
        ),
    },
    "uk_manual_frontier_text_patch_postimage_chain_gap": {
        "candidate_operation_family": "source_chain_text_patch",
        "required_validator_checks": (
            "source_chain_contains_preimage_to_postimage_transition",
            "claim_identifies_intermediate_amendment_effect",
            "claim_links_current_postimage_to_source_instruction",
            "claim_preserves_effect_feed_target_identity",
            "changed_paths_are_within_text_patch_target",
        ),
    },
    "uk_manual_frontier_whole_act_word_level_text_patch_candidate": {
        "candidate_operation_family": "whole_act_listed_enactments_text_patch",
        "required_validator_checks": (
            "source_witness_lists_the_affected_act_or_short_citation",
            "claim_uses_longest_preimage_first_for_overlapping_phrases",
            "claim_excludes_title_and_short_title_surfaces",
            "claim_excludes_words_amended_by_named_same_schedule_paragraphs",
            "claim_excludes_words_inserted_by_same_act_unless_otherwise_provided",
            "changed_paths_are_within_declared_whole_act_text_carriers",
        ),
    },
}


def uk_frontier_work_item_from_manual_frontier_row(
    row: Mapping[str, Any],
) -> FrontierWorkItem:
    """Project a UK manual-frontier row as a non-executable work item."""
    template = _mapping(row.get("suggested_claim_template"))
    manual_frontier = _mapping(row.get("manual_compile_frontier"))
    target_context = _mapping(row.get("target_context"))
    source_witness = _first_mapping(
        row.get("affecting_source_witness"),
        row.get("source"),
        row.get("source_witness"),
    )
    statute_id = str(row.get("statute_id") or "")
    effect_id = str(row.get("effect_id") or "")
    frontier_family = str(
        row.get("current_manual_compile_rule_id")
        or row.get("manual_compile_rule_id")
        or row.get("validator_current_manual_compile_rule_id")
        or row.get("rule_id")
        or ""
    )
    frontier_status = str(
        row.get("current_manual_compile_status")
        or row.get("manual_compile_status")
        or row.get("validator_status")
        or ""
    )
    source_artifact_id = str(
        row.get("source_artifact_id")
        or row.get("affecting_act_id")
        or row.get("affecting_uri")
        or row.get("affected_uri")
        or statute_id
    )
    source_unit_id = str(
        row.get("source_unit_id")
        or effect_id
        or row.get("rule_id")
        or frontier_family
    )
    family_defaults = _mapping(_FRONTIER_FAMILY_DEFAULTS.get(frontier_family))
    detail = {
        "statute_id": statute_id,
        "effect_id": effect_id,
        "source_pathology": str(
            row.get("current_source_pathology") or row.get("source_pathology") or ""
        ),
        "manual_compile_reason": str(
            row.get("current_manual_compile_reason")
            or row.get("manual_compile_reason")
            or manual_frontier.get("reason")
            or ""
        ),
        "suggested_claim_template_status": str(
            row.get("suggested_claim_template_status") or ""
        ),
        "claim_status": str(row.get("claim_status") or ""),
        "validator_status": str(row.get("validator_status") or ""),
        "lowering_rule_ids": _first_string_tuple(
            row.get("current_lowering_rule_ids"),
            row.get("manual_compile_lowering_rule_ids"),
            row.get("validator_current_lowering_rule_ids"),
            manual_frontier.get("lowering_rule_ids"),
        ),
        "blocking_lowering_rule_ids": _first_string_tuple(
            row.get("current_blocking_lowering_rule_ids"),
            row.get("manual_compile_blocking_lowering_rule_ids"),
            row.get("validator_current_blocking_lowering_rule_ids"),
            manual_frontier.get("blocking_lowering_rule_ids"),
            _mapping(row.get("blocking_lowering_rejection_rule_counts")).keys(),
        ),
        "compiled_op_count": _nonnegative_int(row.get("compiled_op_count")),
        "compare_shape": str(row.get("compare_shape") or target_context.get("compare_shape") or ""),
    }
    normalized_source_witness = source_witness_from_mapping(
        source_witness,
        default_role=_source_witness_role(source_witness),
        default_artifact_id=source_artifact_id,
        default_source_unit_id=source_unit_id,
    ).to_dict()
    target_witness = _target_witness(row)
    compare_witness = _compare_witness(row, target_witness=target_witness)
    return FrontierWorkItem(
        work_item_id=str(
            row.get("work_item_id") or f"uk-frontier-{source_artifact_id}-{source_unit_id}"
        ),
        jurisdiction="uk",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=normalized_source_witness,
        target_witness=target_witness,
        compare_witness=compare_witness,
        owner_phase=str(
            row.get("current_owner_phase")
            or row.get("owner_phase")
            or row.get("manual_compile_owner_phase")
            or ""
        ),
        frontier_family=frontier_family,
        frontier_status=frontier_status,
        candidate_operation_family=str(
            template.get("action_family")
            or row.get("work_item_kind")
            or family_defaults.get("candidate_operation_family")
            or ""
        ),
        candidate_targets=_candidate_targets(row),
        guidance_refs=_string_tuple(template.get("guidance_refs")),
        required_claim_kind=str(row.get("claim_kind") or "semantic_compile"),
        required_validator_checks=_first_string_tuple(
            template.get("required_validator_checks"),
            row.get("required_validator_checks"),
            family_defaults.get("required_validator_checks"),
        ),
        required_proofs=_string_tuple(row.get("required_proofs")),
        safe_default=str(row.get("safe_default") or ""),
        forbidden_shortcuts=_string_tuple(row.get("forbidden_shortcuts")),
        executable=_bool_flag(row.get("executable")),
        replay_authorized=_bool_flag(row.get("replay_authorized")),
        authorization_status=str(row.get("authorization_status") or ""),
        detail=detail,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping) and value:
            return value
    return {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    if not isinstance(value, Mapping) and hasattr(value, "__iter__"):
        return tuple(str(item) for item in value if str(item))
    return ()


def _first_string_tuple(*values: Any) -> tuple[str, ...]:
    for value in values:
        items = _string_tuple(value)
        if items:
            return items
    return ()


def _bool_flag(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    return 0


def _source_witness_role(source_witness: Mapping[str, Any]) -> str:
    if source_witness.get("source_sha256") or source_witness.get("affecting_act_id"):
        return "affecting_source"
    if source_witness.get("text_preview"):
        return "source_preview"
    if source_witness:
        return "source_context"
    return "unspecified_source"


def _candidate_targets(row: Mapping[str, Any]) -> tuple[str, ...]:
    target_context = _mapping(row.get("target_context"))
    targets = (
        *_string_tuple(row.get("affected_provisions")),
        *_string_tuple(target_context.get("affected_provisions")),
        *_string_tuple(target_context.get("resolver_eids")),
    )
    return tuple(dict.fromkeys(targets))


def _target_witness(row: Mapping[str, Any]) -> Mapping[str, Any]:
    target_context = _mapping(row.get("target_context"))
    witness = {
        "surface": str(
            target_context.get("surface")
            or row.get("target_surface")
            or "effect_feed_affected_provisions"
        ),
        "affected_provisions": str(
            target_context.get("affected_provisions")
            or row.get("affected_provisions")
            or ""
        ),
        "candidate_targets": _candidate_targets(row),
        "resolver_eids": _first_string_tuple(
            target_context.get("resolver_eids"),
            row.get("resolver_eids"),
        ),
    }
    return _compact_witness(witness)


def _compare_witness(
    row: Mapping[str, Any],
    *,
    target_witness: Mapping[str, Any],
) -> Mapping[str, Any]:
    target_context = _mapping(row.get("target_context"))
    compare = _mapping(row.get("compare"))
    witness = {
        "surface": "replay_vs_current_oracle_target_presence",
        "compare_shape": str(
            row.get("compare_shape")
            or target_context.get("compare_shape")
            or compare.get("shape")
            or ""
        ),
        "resolver_eids": _first_string_tuple(
            compare.get("resolver_eids"),
            target_witness.get("resolver_eids"),
        ),
        "base_target_hits": _bool_tuple(compare.get("base_target_hits")),
        "oracle_target_hits": _bool_tuple(compare.get("oracle_target_hits")),
        "base_descendant_hits": _bool_tuple(compare.get("base_descendant_hits")),
        "oracle_descendant_hits": _bool_tuple(compare.get("oracle_descendant_hits")),
        "base_parent_hits": _bool_tuple(compare.get("base_parent_hits")),
        "oracle_parent_hits": _bool_tuple(compare.get("oracle_parent_hits")),
    }
    return _compact_witness(witness)


def _bool_tuple(value: Any) -> tuple[bool, ...]:
    if isinstance(value, list | tuple):
        return tuple(bool(item) for item in value)
    return ()


def _compact_witness(witness: Mapping[str, Any]) -> Mapping[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in witness.items():
        if value in ("", (), [], {}, None):
            continue
        compact[str(key)] = value
    return compact

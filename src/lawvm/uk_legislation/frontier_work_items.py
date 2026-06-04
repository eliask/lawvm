"""UK projections into the shared frontier work-item contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lawvm.core.candidate_set_certificate import (
    CANDIDATE_SET_COMPLETE,
    CANDIDATE_SET_UNAVAILABLE,
    CandidateSetCertificate,
)
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_witness import source_witness_from_mapping
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
    TARGET_AMBIGUOUS,
    TARGET_RESOLVED,
    TARGET_UNRESOLVED,
    TargetResolutionCandidate,
    TargetResolutionCertificate,
)


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
    "uk_manual_frontier_deictic_structural_sibling_insert_candidate": {
        "candidate_operation_family": "structural_sibling_insert",
        "required_validator_checks": (
            "source_witness_uses_deictic_sibling_anchor",
            "claim_identifies_exact_parent_and_anchor_sibling",
            "claim_proves_anchor_resolution_from_source_or_live_preimage",
            "claim_identifies_each_inserted_sibling_payload",
            "claim_preserves_anchor_and_unclaimed_siblings",
            "changed_paths_are_within_declared_sibling_insertion_boundary",
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
    "uk_manual_frontier_amount_specified_source_target_mismatch": {
        "candidate_operation_family": "source_target_reconciliation",
        "required_validator_checks": (
            "source_witness_names_amount_specified_target",
            "claim_reconciles_source_amount_target_and_effect_feed_target",
            "claim_preserves_unclaimed_parent_amounts",
            "changed_paths_are_within_source_feed_reconciled_target",
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
    "uk_manual_frontier_empty_type_whole_act_action_out_of_scope": {
        "candidate_operation_family": "non_textual_or_out_of_scope",
        "required_validator_checks": (
            "claim_identifies_empty_effect_type_or_whole_act_action_gap",
            "claim_confirms_no_direct_text_or_tree_mutation",
            "claim_preserves_affected_statute_text_state",
        ),
    },
    "uk_manual_frontier_partial_whole_act_repeal_candidate": {
        "candidate_operation_family": "whole_act_repeal_with_exceptions",
        "required_validator_checks": (
            "source_witness_names_whole_act_repeal_and_exception_set",
            "claim_enumerates_repealed_targets_excluding_named_exceptions",
            "claim_preserves_named_exception_provisions",
            "claim_proves_temporal_extent_applicability_for_broad_repeal",
            "changed_paths_are_within_whole_act_minus_exception_boundary",
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
    "uk_manual_frontier_crossheading_source_target_mismatch": {
        "candidate_operation_family": "source_target_reconciliation",
        "required_validator_checks": (
            "source_witness_names_crossheading_facet_target",
            "claim_reconciles_source_crossheading_target_and_effect_feed_target",
            "claim_identifies_whether_body_text_heading_facet_or_both_are_affected",
            "claim_blocks_host_body_rewrite_until_facet_scope_is_proved",
            "changed_paths_are_within_source_feed_reconciled_target",
        ),
    },
    "uk_manual_frontier_cross_container_renumber_candidate": {
        "candidate_operation_family": "cross_container_renumber",
        "required_validator_checks": (
            "source_witness_names_original_and_new_container_context",
            "claim_identifies_each_renumbered_or_migrated_unit",
            "claim_preserves_unclaimed_container_children",
            "claim_emits_lineage_for_changed_identities",
            "changed_paths_are_within_declared_cross_container_boundary",
        ),
    },
    "uk_manual_frontier_definition_child_structural_substitution_candidate": {
        "candidate_operation_family": "definition_child_structural_substitution",
        "required_validator_checks": (
            "source_witness_names_definition_child_and_replacement_payload",
            "claim_identifies_exact_definition_child_node",
            "claim_materializes_replacement_payload_as_child_units_not_flat_text",
            "claim_preserves_unclaimed_definition_children_and_tail_text",
            "changed_paths_are_within_claimed_definition_child_boundary",
        ),
    },
    "uk_manual_frontier_definition_child_structural_insert_candidate": {
        "candidate_operation_family": "definition_child_structural_insert",
        "required_validator_checks": (
            "source_witness_names_definition_child_anchor_and_insert_payload",
            "claim_identifies_definition_term_scope",
            "claim_identifies_anchor_definition_child_identity",
            "claim_materializes_inserted_payload_as_structural_child_units",
            "claim_identifies_existing_tail_connector_surface",
            "claim_owns_connector_migration_or_preservation_rule",
            "changed_paths_are_within_claimed_definition_child_insert_boundary",
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
    "uk_manual_frontier_labeled_child_end_range_candidate": {
        "candidate_operation_family": "labeled_child_end_range_text_patch",
        "required_validator_checks": (
            "source_witness_names_quoted_preimage_and_child_endpoint",
            "claim_identifies_exact_child_carrier_and_endpoint",
            "claim_text_preimage_matches_effective_child_surface",
            "claim_materializes_replacement_payload_without_parent_widening",
            "changed_paths_are_within_declared_child_end_range_boundary",
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
    "uk_manual_frontier_unquoted_preimage_substitution_source_insufficient": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "source_witness_contains_unquoted_substitution_instruction",
            "explicit_text_preimage_source_or_claim_is_available",
            "claim_blocks_replay_until_text_preimage_is_proved",
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
    "uk_manual_frontier_parser_or_extraction_candidate": {
        "candidate_operation_family": "parser_or_extraction_gap",
        "required_validator_checks": (
            "source_witness_contains_complete_operative_instruction",
            "compiler_or_claim_identifies_exact_text_or_structural_preimage",
            "compiler_or_claim_identifies_exact_replacement_or_inserted_payload",
            "target_scope_is_the_effect_target_or_source_named_descendant_only",
            "changed_paths_are_within_declared_target_and_payload_boundaries",
        ),
    },
    "uk_manual_frontier_relative_other_place_occurrence_candidate": {
        "candidate_operation_family": "relative_occurrence_text_patch",
        "required_validator_checks": (
            "source_witness_contains_relative_other_place_formula",
            "claim_identifies_preceding_first_occurrence_source_sibling_or_equivalent_context",
            "claim_identifies_exact_original_and_replacement_or_inserted_text",
            "claim_preserves_the_first_occurrence_and_unselected_occurrences",
            "changed_paths_are_within_declared_relative_occurrence_text_carrier",
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
    "uk_manual_frontier_mixed_body_heading_text_substitution_split": {
        "candidate_operation_family": "mixed_body_heading_text_substitution_split",
        "required_validator_checks": (
            "source_witness_names_body_target_and_heading_facet",
            "claim_splits_body_text_operation_from_heading_facet_operation",
            "claim_identifies_exact_heading_or_italic_heading_carrier",
            "claim_text_preimage_matches_each_claimed_surface",
            "claim_preserves_unclaimed_body_text_heading_text_and_children",
            "changed_paths_are_within_declared_body_and_facet_targets",
        ),
    },
    "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate": {
        "candidate_operation_family": "source_carried_multi_subunit_text_rewrite",
        "required_validator_checks": (
            "source_witness_names_each_child_unit_to_mutate",
            "claim_splits_the_parent_formula_into_bounded_child_operations",
            "claim_text_preimage_matches_each_declared_child_surface",
            "claim_preserves_unclaimed_child_units_and_parent_text",
            "changed_paths_are_within_declared_child_unit_boundaries",
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
    "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate": {
        "candidate_operation_family": "source_carried_child_tail_text_rewrite",
        "required_validator_checks": (
            "source_witness_names_the_child_anchor_and_tail_scope",
            "claim_targets_only_the_tail_text_following_that_child",
            "claim_text_preimage_matches_the_declared_tail_surface",
            "claim_preserves_child_body_and_unclaimed_parent_text",
            "changed_paths_are_within_declared_child_tail_boundary",
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
    "uk_manual_frontier_structural_child_range_substitution_candidate": {
        "candidate_operation_family": "structural_child_range_substitution",
        "required_validator_checks": (
            "source_witness_names_child_range_and_replacement_payload",
            "claim_identifies_each_removed_child_unit",
            "claim_materializes_replacement_payload_as_child_units_not_flat_text",
            "claim_preserves_unclaimed_child_units_and_parent_text",
            "changed_paths_are_within_claimed_child_range_boundary",
        ),
    },
    "uk_manual_frontier_structural_sibling_insert_candidate": {
        "candidate_operation_family": "structural_sibling_insert",
        "required_validator_checks": (
            "source_witness_names_before_or_after_sibling_anchor",
            "claim_identifies_exact_parent_and_anchor_sibling",
            "claim_identifies_each_inserted_sibling_payload",
            "claim_preserves_anchor_and_unclaimed_siblings",
            "changed_paths_are_within_declared_sibling_insertion_boundary",
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
    "uk_manual_frontier_structural_pseudo_definition_source_insufficient": {
        "candidate_operation_family": "source_acquisition_or_payload_extraction",
        "required_validator_checks": (
            "effect_metadata_names_pseudo_definition_target",
            "official_source_witness_contains_definition_payload_or_instruction",
            "claim_blocks_replay_until_source_payload_is_available",
            "claim_blocks_replay_until_target_identity_is_proved",
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
    "uk_manual_frontier_table_crossheading_candidate": {
        "candidate_operation_family": "table_crossheading_text_rewrite",
        "required_validator_checks": (
            "source_witness_targets_table_crossheading_surface",
            "claim_identifies_exact_table_and_crossheading_carrier",
            "claim_preserves_unclaimed_table_rows_columns_and_cells",
            "claim_text_preimage_matches_table_crossheading_surface",
            "changed_paths_are_within_declared_table_crossheading_boundary",
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
    work_item_id = str(
        row.get("work_item_id") or f"uk-frontier-{source_artifact_id}-{source_unit_id}"
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
    source_fragment = _mapping(row.get("source"))
    if source_fragment:
        detail["source_fragment_witness"] = source_witness_from_mapping(
            source_fragment,
            default_role=_source_witness_role(source_fragment),
            default_artifact_id=source_artifact_id,
            default_source_unit_id=source_unit_id,
        ).to_dict()
    target_witness = _target_witness(row)
    compare_witness = _compare_witness(row, target_witness=target_witness)
    execution_authorization = _execution_authorization_packet(row)
    candidate_targets = _candidate_targets(row)
    owner_phase = str(
        row.get("current_owner_phase")
        or row.get("owner_phase")
        or row.get("manual_compile_owner_phase")
        or execution_authorization.get("owner_phase")
        or ""
    )
    required_validator_checks = _first_string_tuple(
        template.get("required_validator_checks"),
        row.get("required_validator_checks"),
        family_defaults.get("required_validator_checks"),
    )
    required_proofs = _first_string_tuple(
        row.get("required_proofs"),
        execution_authorization.get("required_proofs"),
    )
    safe_default = str(
        row.get("safe_default") or execution_authorization.get("safe_default") or ""
    )
    forbidden_shortcuts = _first_string_tuple(
        row.get("forbidden_shortcuts"),
        execution_authorization.get("forbidden_shortcuts"),
    )
    executable = _bool_flag(
        row.get("executable", execution_authorization.get("executable"))
    )
    replay_authorized = _bool_flag(
        row.get(
            "replay_authorized",
            execution_authorization.get("replay_authorized"),
        )
    )
    authorization_status = str(
        row.get("authorization_status")
        or execution_authorization.get("authorization_status")
        or ""
    )
    detail["execution_authorization"] = execution_authorization
    detail["candidate_set_certificate"] = _candidate_target_set_certificate(
        work_item_id=work_item_id,
        owner_phase=owner_phase,
        candidate_targets=candidate_targets,
        frontier_family=frontier_family,
        target_witness=target_witness,
    )
    detail["target_resolution_certificate"] = _target_resolution_certificate(
        owner_phase=owner_phase,
        target_witness=target_witness,
        candidate_targets=candidate_targets,
    )
    detail["packet_completeness"] = _packet_completeness(
        execution_authorization=execution_authorization,
        source_witness=normalized_source_witness,
        target_witness=target_witness,
        compare_witness=compare_witness,
        candidate_set_certificate=detail["candidate_set_certificate"],
        target_resolution_certificate=detail["target_resolution_certificate"],
        owner_phase=owner_phase,
        frontier_family=frontier_family,
        frontier_status=frontier_status,
        required_validator_checks=required_validator_checks,
        required_proofs=required_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=forbidden_shortcuts,
        executable=executable,
        replay_authorized=replay_authorized,
        authorization_status=authorization_status,
    )
    return FrontierWorkItem(
        work_item_id=work_item_id,
        jurisdiction="uk",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=normalized_source_witness,
        target_witness=target_witness,
        compare_witness=compare_witness,
        owner_phase=owner_phase,
        frontier_family=frontier_family,
        frontier_status=frontier_status,
        candidate_operation_family=str(
            template.get("action_family")
            or row.get("work_item_kind")
            or family_defaults.get("candidate_operation_family")
            or ""
        ),
        candidate_targets=candidate_targets,
        guidance_refs=_string_tuple(template.get("guidance_refs")),
        required_claim_kind=str(row.get("claim_kind") or "semantic_compile"),
        required_validator_checks=required_validator_checks,
        required_proofs=required_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=forbidden_shortcuts,
        executable=executable,
        replay_authorized=replay_authorized,
        authorization_status=authorization_status,
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


def _execution_authorization_packet(row: Mapping[str, Any]) -> Mapping[str, Any]:
    packet = _mapping(row.get("execution_authorization"))
    if packet:
        return _compact_witness(
            {
                "executable": _bool_flag(packet.get("executable")),
                "replay_authorized": _bool_flag(packet.get("replay_authorized")),
                "authorization_status": str(packet.get("authorization_status") or ""),
                "authorization_rule_id": str(packet.get("authorization_rule_id") or ""),
                "owner_phase": str(packet.get("owner_phase") or ""),
                "strict_disposition": str(packet.get("strict_disposition") or ""),
                "quirks_disposition": str(packet.get("quirks_disposition") or ""),
                "validator_status": str(packet.get("validator_status") or ""),
                "required_proofs": _string_tuple(packet.get("required_proofs")),
                "safe_default": str(packet.get("safe_default") or ""),
                "forbidden_shortcuts": _string_tuple(packet.get("forbidden_shortcuts")),
                "detail": _mapping(packet.get("detail")),
            }
        )
    return _compact_witness(
        {
            "executable": _bool_flag(row.get("executable")),
            "replay_authorized": _bool_flag(row.get("replay_authorized")),
            "authorization_status": str(row.get("authorization_status") or ""),
            "authorization_rule_id": str(row.get("authorization_rule_id") or ""),
            "owner_phase": str(
                row.get("current_owner_phase")
                or row.get("owner_phase")
                or row.get("manual_compile_owner_phase")
                or ""
            ),
            "strict_disposition": str(row.get("strict_disposition") or ""),
            "quirks_disposition": str(row.get("quirks_disposition") or ""),
            "validator_status": str(row.get("validator_status") or ""),
            "required_proofs": _string_tuple(row.get("required_proofs")),
            "safe_default": str(row.get("safe_default") or ""),
            "forbidden_shortcuts": _string_tuple(row.get("forbidden_shortcuts")),
        }
    )


def _candidate_target_set_certificate(
    *,
    work_item_id: str,
    owner_phase: str,
    candidate_targets: tuple[str, ...],
    frontier_family: str,
    target_witness: Mapping[str, Any],
) -> Mapping[str, Any]:
    has_candidates = bool(candidate_targets)
    blockers = {} if has_candidates else {"candidate_targets_unavailable": 1}
    certificate = CandidateSetCertificate(
        scope_id=f"uk-frontier-work-item:{work_item_id}",
        candidate_set_kind="uk_frontier_work_item_candidate_targets",
        phase=owner_phase or "unknown",
        rule_id="uk_frontier_work_item_candidate_target_set_projection",
        reason=(
            "candidate target surfaces are bounded by the manual-frontier work "
            "item target witness and do not authorize replay"
        ),
        completeness_status=(
            CANDIDATE_SET_COMPLETE if has_candidates else CANDIDATE_SET_UNAVAILABLE
        ),
        candidate_count=len(candidate_targets),
        candidate_ids=candidate_targets,
        missing_candidate_count=0 if has_candidates else 1,
        blocker_counts=blockers,
        blocker_families=tuple(blockers),
        next_promotion_allowed=False,
        next_promotion_requires=(
            "target_candidate_set_completeness",
            "execution_authorization",
            "mutation_boundary_proof",
        ),
        detail={
            "frontier_family_for_projection": frontier_family,
            "target_witness_surface": str(target_witness.get("surface") or ""),
            "target_witness_has_resolver_eids": bool(target_witness.get("resolver_eids")),
        },
    )
    return certificate.to_dict()


def _target_resolution_certificate(
    *,
    owner_phase: str,
    target_witness: Mapping[str, Any],
    candidate_targets: tuple[str, ...],
) -> Mapping[str, Any]:
    source_target = str(
        target_witness.get("affected_provisions")
        or (candidate_targets[0] if candidate_targets else "")
        or "unknown"
    )
    candidates = tuple(
        TargetResolutionCandidate(
            target=target,
            reason="manual_frontier_candidate_target",
            detail={
                "target_witness_surface": str(target_witness.get("surface") or ""),
                "target_resolution_not_replay_authorization": True,
            },
        )
        for target in candidate_targets
    )
    resolver_eids = _string_tuple(target_witness.get("resolver_eids"))
    candidate_count = len(candidate_targets)
    selected_target = candidate_targets[0] if candidate_count == 1 else ""
    if candidate_count == 0:
        status = TARGET_UNRESOLVED
    elif candidate_count == 1:
        status = TARGET_RESOLVED
    else:
        status = TARGET_AMBIGUOUS
    return TargetResolutionCertificate(
        rule_id="uk_frontier_work_item_target_resolution_projection",
        phase=owner_phase or "unknown",
        reason=(
            "manual-frontier target witness is projected for validation and "
            "does not authorize replay"
        ),
        status=status,
        source_target=source_target,
        candidate_count=candidate_count,
        candidates=candidates,
        selected_target=selected_target,
        scope_confidence=(
            SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT
            if resolver_eids
            else SCOPE_CONFIDENCE_EXPLICIT_SOURCE
        ),
        blocking=False,
        strict_disposition="record",
        quirks_disposition="record",
        detail={
            "target_witness_surface": str(target_witness.get("surface") or ""),
            "affected_provisions": str(target_witness.get("affected_provisions") or ""),
            "resolver_eids": resolver_eids,
            "target_resolution_not_replay_authorization": True,
        },
    ).to_diagnostic_detail()


def _packet_completeness(
    *,
    execution_authorization: Mapping[str, Any],
    source_witness: Mapping[str, Any],
    target_witness: Mapping[str, Any],
    compare_witness: Mapping[str, Any],
    candidate_set_certificate: Mapping[str, Any],
    target_resolution_certificate: Mapping[str, Any],
    owner_phase: str,
    frontier_family: str,
    frontier_status: str,
    required_validator_checks: tuple[str, ...],
    required_proofs: tuple[str, ...],
    safe_default: str,
    forbidden_shortcuts: tuple[str, ...],
    executable: bool,
    replay_authorized: bool,
    authorization_status: str,
) -> Mapping[str, Any]:
    checks = {
        "has_execution_authorization": bool(execution_authorization),
        "has_authorization_status": bool(authorization_status),
        "has_authorization_rule_id": bool(
            execution_authorization.get("authorization_rule_id")
        ),
        "has_owner_phase": bool(owner_phase),
        "has_frontier_family": bool(frontier_family),
        "has_frontier_status": bool(frontier_status),
        "has_source_witness": bool(source_witness),
        "has_source_digest_or_preview_digest": bool(
            source_witness.get("digest") or source_witness.get("preview_digest")
        ),
        "has_target_witness": bool(target_witness),
        "has_target_resolution_certificate": bool(target_resolution_certificate),
        "has_compare_witness": bool(compare_witness),
        "has_candidate_set_certificate": bool(candidate_set_certificate),
        "has_required_validator_checks": bool(required_validator_checks),
        "has_required_proofs": bool(required_proofs),
        "has_safe_default": bool(safe_default),
        "has_forbidden_shortcuts": bool(forbidden_shortcuts),
        "non_executable_frontier_invariant": (
            executable is False and replay_authorized is False
        ),
    }
    missing = tuple(
        name.removeprefix("has_") for name, present in checks.items() if not present
    )
    ready = (
        checks["has_execution_authorization"]
        and checks["has_authorization_status"]
        and checks["has_owner_phase"]
        and checks["has_frontier_family"]
        and checks["has_frontier_status"]
        and checks["has_source_witness"]
        and checks["has_target_witness"]
        and checks["has_target_resolution_certificate"]
        and checks["has_candidate_set_certificate"]
        and checks["has_required_validator_checks"]
        and checks["has_required_proofs"]
        and checks["has_safe_default"]
        and checks["has_forbidden_shortcuts"]
        and checks["non_executable_frontier_invariant"]
    )
    return {
        **checks,
        "missing_fields": list(missing),
        "ready_for_manual_claim_validation": ready,
        "proof_boundary": (
            "frontier_work_item_is_non_executable_until_execution_authorization_"
            "is_replay_authorized"
        ),
    }


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

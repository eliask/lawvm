"""UK manual-frontier claim-template availability metadata."""
from __future__ import annotations

UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS = frozenset(
    {
        "uk_manual_frontier_application_by_reference_deixis_resolution_candidate",
        "uk_manual_frontier_appropriate_place_candidate",
        "uk_manual_frontier_appropriate_place_definition_entry_candidate",
        "uk_manual_frontier_appropriate_place_index_entry_candidate",
        "uk_manual_frontier_body_section_schedule_payload_candidate",
        "uk_manual_frontier_savings_references_qualified_repeal_candidate",
        "uk_manual_frontier_amendment_program_target_candidate",
        "uk_manual_frontier_amount_specified_source_target_mismatch",
        "uk_manual_frontier_child_qualified_word_omission_target_mismatch",
        "uk_manual_frontier_conditional_temporal_repeal_resolution_candidate",
        "uk_manual_frontier_cross_container_renumber_candidate",
        "uk_manual_frontier_crossheading_candidate",
        "uk_manual_frontier_crossheading_source_target_mismatch",
        "uk_manual_frontier_deictic_amendment_program_target_candidate",
        "uk_manual_frontier_deictic_structural_sibling_insert_candidate",
        "uk_manual_frontier_definition_child_and_tail_substitution_candidate",
        "uk_manual_frontier_definition_anchor_tail_insert_candidate",
        "uk_manual_frontier_definition_child_structural_insert_candidate",
        "uk_manual_frontier_definition_child_structural_substitution_candidate",
        "uk_manual_frontier_definition_entry_substitution_candidate",
        "uk_manual_frontier_nested_definition_child_structural_substitution_candidate",
        "uk_manual_frontier_definition_list_end_insert_candidate",
        "uk_manual_frontier_effect_metadata_schedule_paragraph_range_to_part_renumber_candidate",
        "uk_manual_frontier_effect_metadata_unsupported_renumber_candidate",
        "uk_manual_frontier_effect_metadata_carried_text_patch_candidate",
        "uk_manual_frontier_heading_facet_candidate",
        "uk_manual_frontier_labeled_child_end_range_candidate",
        "uk_manual_frontier_mixed_structural_definition_repeal_split",
        "uk_manual_frontier_mixed_structural_text_rewrite_split",
        "uk_manual_frontier_mixed_body_heading_text_substitution_split",
        "uk_manual_frontier_multi_enactment_specified_provisions_text_patch",
        "uk_manual_frontier_parser_or_extraction_candidate",
        "uk_manual_frontier_partial_whole_act_repeal_candidate",
        "uk_manual_frontier_range_to_container_candidate",
        "uk_manual_frontier_relative_other_place_occurrence_candidate",
        "uk_manual_frontier_referent_qualified_text_substitution_candidate",
        "uk_manual_frontier_repeal_table_candidate",
        "uk_manual_frontier_schedule_list_entry_candidate",
        "uk_manual_frontier_schedule_note_candidate",
        "uk_manual_frontier_sentence_scoped_repeated_insert_candidate",
        "uk_manual_frontier_same_moment_cross_act_precedence_resolution_candidate",
        "uk_manual_frontier_savings_qualified_text_omission_candidate",
        "uk_manual_frontier_scoped_occurrence_substitution_with_exclusions",
        "uk_manual_frontier_scoped_occurrence_text_patch_with_exclusions_candidate",
        "uk_manual_frontier_scoped_occurrence_program_exclusion_candidate",
        "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate",
        "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate",
        "uk_manual_frontier_source_carried_structured_text_patch_candidate",
        "uk_manual_frontier_source_carried_structured_tail_substitution_candidate",
        "uk_manual_frontier_structural_child_range_substitution_candidate",
        "uk_manual_frontier_structural_sibling_insert_candidate",
        "uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate",
        "uk_manual_frontier_table_appropriate_place_candidate",
        "uk_manual_frontier_table_column_insert_candidate",
        "uk_manual_frontier_table_crossheading_candidate",
        "uk_manual_frontier_table_deictic_this_subsection_insert",
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_entry_placement_insert",
        "uk_manual_frontier_whole_act_word_level_text_patch_candidate",
        "uk_manual_frontier_range_to_container_resolution_candidate",
        "uk_manual_frontier_non_textual_modification_overlay_candidate",
    }
)

_ACTIONABLE_MANUAL_COMPILE_STATUSES = frozenset(
    {
        "manual_compile_candidate",
        "deterministic_frontend_candidate",
        "source_or_feed_target_conflict",
    }
)


def uk_manual_claim_template_status(
    *,
    manual_compile_status: str,
    manual_compile_rule_id: str,
) -> str:
    """Return claim-template availability for actionable UK manual-frontier rows."""
    if manual_compile_status not in _ACTIONABLE_MANUAL_COMPILE_STATUSES:
        return ""
    if manual_compile_rule_id in UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS:
        return "available"
    return "not_available"

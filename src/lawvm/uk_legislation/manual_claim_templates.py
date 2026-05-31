"""UK manual-frontier claim-template availability metadata."""
from __future__ import annotations

UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS = frozenset(
    {
        "uk_manual_frontier_appropriate_place_candidate",
        "uk_manual_frontier_appropriate_place_definition_entry_candidate",
        "uk_manual_frontier_appropriate_place_index_entry_candidate",
        "uk_manual_frontier_amendment_program_target_candidate",
        "uk_manual_frontier_cross_container_renumber_candidate",
        "uk_manual_frontier_crossheading_candidate",
        "uk_manual_frontier_definition_child_and_tail_substitution_candidate",
        "uk_manual_frontier_definition_child_structural_insert_candidate",
        "uk_manual_frontier_definition_child_structural_substitution_candidate",
        "uk_manual_frontier_definition_list_end_insert_candidate",
        "uk_manual_frontier_heading_facet_candidate",
        "uk_manual_frontier_mixed_body_heading_text_substitution_split",
        "uk_manual_frontier_parser_or_extraction_candidate",
        "uk_manual_frontier_range_to_container_candidate",
        "uk_manual_frontier_referent_qualified_text_substitution_candidate",
        "uk_manual_frontier_repeal_table_candidate",
        "uk_manual_frontier_schedule_list_entry_candidate",
        "uk_manual_frontier_schedule_note_candidate",
        "uk_manual_frontier_savings_qualified_text_omission_candidate",
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
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_entry_placement_insert",
        "uk_manual_frontier_whole_act_word_level_text_patch_candidate",
    }
)

_ACTIONABLE_MANUAL_COMPILE_STATUSES = frozenset(
    {
        "manual_compile_candidate",
        "deterministic_frontend_candidate",
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

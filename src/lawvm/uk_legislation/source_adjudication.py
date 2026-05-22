"""Typed UK oracle/source adjudication helpers.

Architectural observations
--------------------------
- UK already has a reasonably explicit adjudication vocabulary, which is good.
- Cross-jurisdiction comparison still shows UK findings as wrapper-surface
  kinds rather than shared direct/core kinds. So the vocabulary is typed but
  not yet harmonized into the common kernel.
- This module is therefore a good staging area for normalization, but not yet
  the final shared adjudication surface.

TODO
----
- Promote reusable UK finding families into governed cross-jurisdiction/core
  finding kinds where they are not genuinely UK-specific.
- Keep compare-shape noise separated from source-pathology claims.

Actionables
-----------
- New UK adjudication work should prefer shared finding families first, then
  UK-local wrappers only when the phenomenon is truly jurisdiction-specific.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.replay_adjudication import SourceAdjudication


UK_CORE_COMPARISON_CLASSES = frozenset({"commensurable", "unapplied_oracle_expansion"})
UK_EFFECT_SOURCE_PATHOLOGY_CLASSES = frozenset(
    {
        "missing_extracted_source",
        "unhandled_instruction_text",
        "reference_only_source_fragment",
        "fragment_context_missing",
        "payload_fragment_without_action_formula",
        "source_carried_multi_subunit_text_rewrite_unsupported",
        "source_carried_child_tail_text_rewrite_unsupported",
        "instruction_text_reused_as_payload",
        "broad_source_reused_as_payload",
        "appropriate_place_definition_entry_insert_unsupported",
        "appropriate_place_insert_unsupported",
        "repeal_schedule_table_source_unsupported",
        "as_if_application_modification_unsupported",
        "commencement_effect_out_of_scope",
        "application_modification_payload_out_of_scope",
        "broad_schedule_flat_payload_unsupported",
        "amendment_text_target_unsupported",
        "conditional_temporal_repeal_unsupported",
        "definition_child_and_tail_substitution_unsupported",
        "table_entry_target_unsupported",
        "schedule_list_entry_target_unsupported",
        "structural_sibling_insert_unsupported",
        "heading_facet_target_unsupported",
        "crossheading_target_unsupported",
        "schedule_note_target_unsupported",
        "misselected_target_context",
        "nonstructural_root_gap",
        "non_substantive_shell_payload",
        "range_to_container_target_unsupported",
        "temporary_as_if_word_omission_unsupported",
    }
)
UK_EFFECT_COMPARE_SHAPE_CLASSES = frozenset(
    {
        "collapsed_subtree_oracle_shape",
        "descendant_only_oracle_wrapper",
        "legacy_labeled_oracle_shape",
        "oracle_missing_live_branch",
        "range_to_container_target_absent",
        "retained_repeal_oracle_branch",
        "table_cell_text_patch_requires_table_surface",
        "text_patch_preimage_absent_from_target_surfaces",
        "territorial_extension_oracle_gap",
    }
)
UK_COMPARE_TABLE_CELL_TEXT_PATCH_RULE_IDS = frozenset(
    {
        "uk_effect_table_column_text_patch",
        "uk_effect_table_entry_inline_text_insertion",
    }
)
UK_COMPARE_CHAINED_TEXT_REWRITE_RULE_IDS = frozenset(
    {
        "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        "uk_effect_all_occurrences_substitution_text_patch",
        "uk_effect_respectively_all_occurrences_substitution_text_patch",
        "uk_effect_wherever_occurring_substitution_text_patch",
    }
)
_ManualFrontierClassification = tuple[str, str, str]
_UK_MANUAL_FRONTIER_RANGE_SOURCE_PATHOLOGY_RESULTS: dict[str, _ManualFrontierClassification] = {
    "range_to_container_target_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_range_to_container_candidate",
        "The source substitutes a section range into a higher-level container; a manual or deterministic range-to-container migration claim must own the replaced range, new container, and lineage.",
    ),
}
_UK_MANUAL_FRONTIER_SOURCE_INSUFFICIENT_PATHOLOGY_RESULTS: dict[
    str, _ManualFrontierClassification
] = {
    "missing_extracted_source": (
        "source_insufficient",
        "uk_manual_frontier_missing_payload_source_insufficient",
        "No extracted source witness is available; a manual claim cannot replace missing public source evidence.",
    ),
    "non_substantive_shell_payload": (
        "source_insufficient",
        "uk_manual_frontier_non_substantive_payload_source_insufficient",
        "The available payload is non-substantive shell or dot-leader text and should not become legal content.",
    ),
}
_UK_MANUAL_FRONTIER_MAIN_SOURCE_PATHOLOGY_RESULTS: dict[str, _ManualFrontierClassification] = {
    "amendment_text_target_unsupported": (
        "deterministic_frontend_candidate",
        "uk_manual_frontier_amendment_program_target_candidate",
        "The source targets text inserted by another amendment instruction; this needs an explicit amendment-program compilation lane, not a base-text guess.",
    ),
    "schedule_list_entry_target_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_schedule_list_entry_candidate",
        "The source targets a schedule/list entry by anchor entry text; a claim or future list-entry compiler must identify the entry carrier and sibling insertion point rather than mutating collapsed schedule text.",
    ),
    "appropriate_place_definition_entry_insert_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_appropriate_place_definition_entry_candidate",
        "The source inserts a definition entry at an appropriate place without naming an anchor; a claim or future placement compiler must supply and validate the exact definition-entry insertion point instead of inferring it from live text.",
    ),
    "appropriate_place_insert_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_appropriate_place_candidate",
        "The source asks for appropriate-place placement; a claim or future placement compiler must identify the insertion anchor without guessing from live text.",
    ),
    "repeal_schedule_table_source_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_repeal_table_candidate",
        "The row appears to depend on a repeal schedule/table or grouped repeal source that may need row/column compilation.",
    ),
    "as_if_application_modification_unsupported": (
        "non_textual_or_out_of_scope",
        "uk_manual_frontier_as_if_application_modification_out_of_scope",
        "The source is an applied/as-if modification clause rather than a direct mutation of the affected statute text/tree under the current UK replay model.",
    ),
    "commencement_effect_out_of_scope": (
        "non_textual_or_out_of_scope",
        "uk_manual_frontier_commencement_effect_out_of_scope",
        "The source is a commencement/applicability instrument; it may matter to temporal selection, but it is not a direct text/tree mutation under the current UK replay model.",
    ),
    "conditional_temporal_repeal_unsupported": (
        "non_textual_or_out_of_scope",
        "uk_manual_frontier_conditional_temporal_repeal_out_of_scope",
        "The source conditionally repeals material based on future commencement state; replay must not treat it as an unconditional current-text repeal without explicit temporal applicability evidence.",
    ),
    "definition_child_and_tail_substitution_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_definition_child_and_tail_substitution_candidate",
        "The source substitutes a definition child together with that child's trailing connective; a claim or future multi-patch compiler must own both the child text and the post-child tail boundary.",
    ),
    "application_modification_payload_out_of_scope": (
        "non_textual_or_out_of_scope",
        "uk_manual_frontier_application_modification_payload_out_of_scope",
        "The extracted payload belongs to an application-modification formula; replay must not treat it as a direct amendment to current target text without a scoped temporal/application model.",
    ),
    "source_carried_multi_subunit_text_rewrite_unsupported": (
        "deterministic_frontend_candidate",
        "uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate",
        "The feed target is broader than the source-carried child targets; compile must split the text rewrite by the named child units rather than mutate the whole parent.",
    ),
    "source_carried_child_tail_text_rewrite_unsupported": (
        "deterministic_frontend_candidate",
        "uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate",
        "The source targets the text tail following a named child; compile must own a bounded child-tail selector rather than delete from the whole parent text.",
    ),
    "structural_sibling_insert_unsupported": (
        "deterministic_frontend_candidate",
        "uk_manual_frontier_structural_sibling_insert_candidate",
        "The source inserts new structural siblings after a named child; a future compiler must emit sibling insert operations instead of appending payload text to the anchor child.",
    ),
    "heading_facet_target_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_heading_facet_candidate",
        "The source targets a heading/title/sidenote facet; a manual claim or future facet compiler must target that facet without mutating the host body.",
    ),
    "crossheading_target_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_crossheading_candidate",
        "The source targets a cross-heading surface that needs an explicit crossheading/facet claim.",
    ),
    "schedule_note_target_unsupported": (
        "manual_compile_candidate",
        "uk_manual_frontier_schedule_note_candidate",
        "The source targets a schedule note surface; a claim or future note compiler must target that note without inventing paragraph structure.",
    ),
}
_UK_SOURCE_CONTAINER_EID_CHILD_STARTS = {
    "part": frozenset({"chapter", "crossheading", "section"}),
    "schedule": frozenset({"crossheading", "paragraph", "part"}),
}
UK_REPLAY_BUG_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_tree_invariant_violation",
        "uk_replay_target_not_found",
        "uk_replay_payload_mismatch",
        "uk_replay_payload_missing",
        "uk_replay_text_patch_missing_structured_payload",
        "uk_replay_unsupported_action",
    }
)

UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_absent_sibling_range_gap",
        "uk_replay_absent_child_repeal_target_gap",
        "uk_replay_broad_schedule_part_table_shape_gap",
        "uk_replay_broad_schedule_table_shape_gap",
        "uk_replay_annex_schedule_reference_gap",
        "uk_replay_definition_child_shape_gap",
        "uk_replay_definition_anchor_lexical_variant_recovered",
        "uk_replay_definition_entry_shape_gap",
        "uk_replay_direct_section_paragraph_carrier_gap",
        "uk_replay_empty_descendant_shape_gap",
        "uk_replay_empty_schedule_shape_gap",
        "uk_replay_existing_target_conflict_gap",
        "uk_replay_existing_target_gap",
        "uk_replay_heading_facet_target_gap",
        "uk_replay_malformed_target_gap",
        "uk_replay_malformed_target_granularity_collapse_gap",
        "uk_replay_malformed_target_note_or_crossheading_gap",
        "uk_replay_malformed_target_placeholder_label_gap",
        "uk_replay_malformed_target_schedule_root_label_gap",
        "uk_replay_malformed_target_sectionlike_label_gap",
        "uk_replay_missing_source_target_gap",
        "uk_replay_missing_parent_grandparent_present_gap",
        "uk_replay_missing_parent_shape_gap",
        "uk_replay_missing_root_parent_shape_gap",
        "uk_replay_missing_schedule_branch_gap",
        "uk_replay_missing_schedule_range_gap",
        "uk_replay_missing_sectionlike_range_gap",
        "uk_replay_part_order_shape_gap",
        "uk_replay_chapter_order_shape_gap",
        "uk_replay_crossheading_and_structural_repeal_unresolved",
        "uk_replay_crossheading_target_gap",
        "uk_replay_paragraph_order_shape_gap",
        "uk_replay_subparagraph_order_shape_gap",
        "uk_replay_item_order_shape_gap",
        "uk_replay_section_order_shape_gap",
        "uk_replay_payload_shape_gap",
        "uk_replay_repeated_form_label_payload_shape_gap",
        "uk_replay_repealed_target_gap",
        "uk_replay_replace_payload_target_leaf_mismatch_gap",
        "uk_replay_schedule_entry_repeal_granularity_blocked",
        "uk_replay_schedule_list_entry_replace_unresolved",
        "uk_replay_schedule_list_entry_repeal_unresolved",
        "uk_replay_schedule_paragraph_carrier_gap",
        "uk_replay_schedule_list_entry_anchor_unresolved",
        "uk_replay_schedule_p1group_wrapper_carrier_gap",
        "uk_replay_schedule_container_text_target_gap",
        "uk_replay_schedule_partition_target_gap",
        "uk_replay_schedule_partition_part_target_gap",
        "uk_replay_schedule_unlabeled_paragraph_target_gap",
        "uk_replay_subsection_descendant_target_collapse_gap",
        "uk_replay_table_shape_gap",
        "uk_replay_table_column_insert_unresolved",
        "uk_replay_table_entry_row_insert_unresolved",
        "uk_replay_table_entry_inline_text_insertion_unresolved",
        "uk_replay_table_entry_inline_text_preimage_gap",
        "uk_replay_schedule_list_entry_table_rows_insert_unresolved",
        "uk_replay_schedule_table_end_rows_insert_unresolved",
        "uk_replay_text_target_empty_surface_gap",
        "uk_replay_text_match_citation_tail_surface_gap",
        "uk_replay_text_match_normalized_preimage_present_gap",
        "uk_replay_text_match_non_substantive_selector_gap",
        "uk_replay_text_match_multi_fragment_selector_gap",
        "uk_replay_text_match_synthetic_selector_gap",
        "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
        "uk_replay_same_source_text_patch_overlap_blocked",
    }
)

UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_heading_text_preimage_gap",
        "uk_replay_text_insert_anchor_preimage_gap",
        "uk_replay_text_match_already_rewritten",
        "uk_replay_text_match_article_phrase_surface_gap",
        "uk_replay_text_match_citation_connector_surface_gap",
        "uk_replay_text_match_missing",
        "uk_replay_text_monetary_amount_preimage_gap",
        "uk_replay_text_parenthetical_omission_preimage_gap",
        "uk_replay_text_patch_preimage_drift",
    }
)

UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS = frozenset(
    {
        "text_duplication_warning",
        "uk_replay_after_definition_child_flat_ordinal_insert_applied",
        "uk_replay_after_definition_child_structured_insert_applied",
        "uk_replay_after_definition_text_insert_applied",
        "uk_replay_after_anchor_to_end_text_rewrite_applied",
        "uk_replay_contextual_word_anchor_kind_normalized",
        "uk_replay_contextual_word_text_rewrite_applied",
        "uk_replay_amendment_insert_tail_text_rewrite_applied",
        "uk_replay_before_definition_text_rewrite_applied",
        "uk_replay_definition_anchor_conjoined_term_normalized",
        "uk_replay_definition_anchor_parenthetical_translation_normalized",
        "uk_replay_definition_anchor_qualifier_phrase_normalized",
        "uk_replay_definition_entry_already_absent_observed",
        "uk_replay_definition_child_flat_ordinal_text_rewrite_applied",
        "uk_replay_definition_child_structured_text_rewrite_applied",
        "uk_replay_definition_entry_orphan_separator_normalized",
        "uk_replay_definition_entry_qualifier_phrase_normalized",
        "uk_replay_definition_entry_text_rewrite_applied",
        "uk_replay_definition_predicate_shall_construed_normalized",
        "uk_replay_direct_section_paragraph_child_text_recovered",
        "uk_replay_empty_descendant_parent_text_recovered",
        "uk_replay_existing_target_already_materialized",
        "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied",
        "uk_replay_in_definition_child_structured_text_rewrite_applied",
        "uk_replay_in_definition_after_anchor_text_rewrite_applied",
        "uk_replay_in_definition_after_each_text_rewrite_applied",
        "uk_replay_in_definition_at_end_text_rewrite_applied",
        "uk_replay_in_definition_range_text_rewrite_applied",
        "uk_replay_in_definition_range_to_end_text_rewrite_applied",
        "uk_replay_implicit_first_subparagraph_parent_text_recovered",
        "uk_replay_schedule_list_entry_alphabetical_position_resolved",
        "uk_replay_schedule_list_entry_anchor_article_normalized",
        "uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized",
        "uk_replay_schedule_list_entry_anchor_prefix_normalized",
        "uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized",
        "uk_replay_schedule_list_entry_group_anchor_resolved",
        "uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized",
        "uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized",
        "uk_replay_schedule_list_entry_replace_resolved",
        "uk_replay_schedule_list_entry_repeal_resolved",
        "uk_replay_repeal_target_already_absent_observed",
        "uk_replay_schedule_list_entry_table_rows_insert_resolved",
        "uk_replay_schedule_table_end_rows_insert_resolved",
        "uk_replay_schedule_item_target_from_parent_substitution_resolved",
        "uk_replay_schedule_p1group_paragraph_wrapper_resolved",
        "uk_replay_source_anchored_order_observed",
        "uk_replay_source_carried_table_entry_paragraph_substitution_resolved",
        "uk_replay_table_entry_multi_cell_text_patch_resolved",
        "uk_replay_source_label_changing_substitution_resolved",
        "uk_replay_source_carried_after_child_text_rewrite_applied",
        "uk_replay_source_carried_before_child_text_rewrite_applied",
        "uk_replay_source_carried_labeled_child_text_substitution_recovered",
        "uk_replay_source_carried_child_tail_text_rewrite_applied",
        "uk_replay_source_carried_multi_child_text_rewrite_applied",
        "uk_replay_source_carried_structured_tail_substitution_recovered",
        "uk_replay_same_source_text_patch_overlap_disjoint",
        "uk_replay_crossheading_and_structural_repeal_resolved",
        "uk_replay_body_root_fallback_insert_resolved",
        "uk_effect_table_column_insert",
        "uk_effect_table_entry_row_insert",
        "uk_replay_text_match_replacement_normalized_present",
        "uk_replay_text_fragment_substitution_fallback_applied",
        "uk_replay_fragment_substitution_child_range_deleted",
        "uk_replay_text_match_punctuation_space_normalized",
        "uk_replay_text_match_rotated_trailing_comma_omission",
        "uk_replay_text_match_word_punctuation_elided",
        "uk_replay_numeric_list_trailing_comma_anchor_normalized",
        "uk_replay_text_range_anchor_word_boundary_normalized",
        "uk_replay_heading_respectively_all_occurrences_absent_observed",
        "uk_replay_labeled_child_end_range_applied",
        "uk_replay_node_local_range_text_rewrite_applied",
        "uk_replay_node_local_range_to_end_text_rewrite_applied",
        "uk_replay_subtree_range_text_rewrite_flattened",
        "uk_replay_subtree_range_to_end_text_rewrite_flattened",
    }
)

UK_REPLAY_BUG_PROOF_KIND_BY_ADJUDICATION_KIND = {
    "uk_replay_tree_invariant_violation": "uk_replay_tree_invariant_violation",
    "uk_replay_target_not_found": "uk_replay_target_not_found",
    "uk_replay_payload_mismatch": "uk_replay_payload_mismatch",
    "uk_replay_payload_missing": "uk_replay_payload_missing",
    "uk_replay_unsupported_action": "uk_replay_unsupported_action",
}

UK_REPLAY_BUG_PROOF_KIND_PRIORITY = (
    "uk_replay_tree_invariant_violation",
    "uk_replay_target_not_found",
    "uk_replay_payload_mismatch",
    "uk_replay_payload_missing",
    "uk_replay_unsupported_action",
)

_UK_REPLAY_SOURCE_SHAPE_RESIDUAL_KIND_PRIORITY: tuple[tuple[str, str], ...] = (
    ("uk_replay_absent_sibling_range_gap", "uk_absent_sibling_range_gap"),
    ("uk_replay_absent_child_repeal_target_gap", "uk_absent_child_repeal_target_gap"),
    ("uk_replay_empty_descendant_shape_gap", "uk_empty_descendant_shape_gap"),
    ("uk_replay_annex_schedule_reference_gap", "uk_annex_schedule_reference_gap"),
    ("uk_replay_existing_target_conflict_gap", "uk_existing_target_conflict_gap"),
    ("uk_replay_existing_target_gap", "uk_existing_target_gap"),
    ("uk_replay_heading_facet_target_gap", "uk_heading_facet_target_gap"),
    ("uk_replay_missing_source_target_gap", "uk_missing_source_target_gap"),
    (
        "uk_replay_missing_parent_grandparent_present_gap",
        "uk_missing_parent_grandparent_present_gap",
    ),
    ("uk_replay_missing_root_parent_shape_gap", "uk_missing_root_parent_shape_gap"),
    ("uk_replay_missing_parent_shape_gap", "uk_missing_parent_shape_gap"),
    ("uk_replay_missing_schedule_branch_gap", "uk_missing_schedule_branch_gap"),
    ("uk_replay_missing_schedule_range_gap", "uk_missing_schedule_range_gap"),
    ("uk_replay_missing_sectionlike_range_gap", "uk_missing_sectionlike_range_gap"),
    ("uk_replay_part_order_shape_gap", "uk_part_order_shape_gap"),
    ("uk_replay_chapter_order_shape_gap", "uk_chapter_order_shape_gap"),
    ("uk_replay_crossheading_target_gap", "uk_crossheading_target_gap"),
    (
        "uk_replay_broad_schedule_part_table_shape_gap",
        "uk_broad_schedule_part_table_shape_gap",
    ),
    ("uk_replay_broad_schedule_table_shape_gap", "uk_broad_schedule_table_shape_gap"),
    ("uk_replay_definition_entry_shape_gap", "uk_definition_entry_shape_gap"),
    ("uk_replay_definition_child_shape_gap", "uk_definition_child_shape_gap"),
    (
        "uk_replay_direct_section_paragraph_carrier_gap",
        "uk_direct_section_paragraph_carrier_gap",
    ),
    ("uk_replay_paragraph_order_shape_gap", "uk_paragraph_order_shape_gap"),
    ("uk_replay_subparagraph_order_shape_gap", "uk_subparagraph_order_shape_gap"),
    ("uk_replay_item_order_shape_gap", "uk_item_order_shape_gap"),
    ("uk_replay_section_order_shape_gap", "uk_section_order_shape_gap"),
    (
        "uk_replay_schedule_partition_part_target_gap",
        "uk_schedule_partition_part_target_gap",
    ),
    ("uk_replay_schedule_partition_target_gap", "uk_schedule_partition_target_gap"),
    (
        "uk_replay_schedule_p1group_wrapper_carrier_gap",
        "uk_schedule_p1group_wrapper_carrier_gap",
    ),
    ("uk_replay_schedule_paragraph_carrier_gap", "uk_schedule_paragraph_carrier_gap"),
    (
        "uk_replay_schedule_entry_repeal_granularity_blocked",
        "uk_schedule_entry_repeal_granularity_blocked",
    ),
    (
        "uk_replay_schedule_list_entry_repeal_unresolved",
        "uk_schedule_list_entry_repeal_unresolved",
    ),
    (
        "uk_replay_schedule_list_entry_anchor_unresolved",
        "uk_schedule_list_entry_anchor_unresolved",
    ),
    (
        "uk_replay_schedule_container_text_target_gap",
        "uk_schedule_container_text_target_gap",
    ),
    (
        "uk_replay_schedule_unlabeled_paragraph_target_gap",
        "uk_schedule_unlabeled_paragraph_target_gap",
    ),
    (
        "uk_replay_subsection_descendant_target_collapse_gap",
        "uk_subsection_descendant_target_collapse_gap",
    ),
    (
        "uk_replay_repeated_form_label_payload_shape_gap",
        "uk_repeated_form_label_payload_shape_gap",
    ),
    ("uk_replay_payload_shape_gap", "uk_payload_shape_gap"),
    (
        "uk_replay_malformed_target_placeholder_label_gap",
        "uk_malformed_target_placeholder_label_gap",
    ),
    (
        "uk_replay_malformed_target_note_or_crossheading_gap",
        "uk_malformed_target_note_or_crossheading_gap",
    ),
    (
        "uk_replay_malformed_target_sectionlike_label_gap",
        "uk_malformed_target_sectionlike_label_gap",
    ),
    (
        "uk_replay_malformed_target_schedule_root_label_gap",
        "uk_malformed_target_schedule_root_label_gap",
    ),
    (
        "uk_replay_malformed_target_granularity_collapse_gap",
        "uk_malformed_target_granularity_collapse_gap",
    ),
    ("uk_replay_malformed_target_gap", "uk_malformed_target_gap"),
    (
        "uk_replay_replace_payload_target_leaf_mismatch_gap",
        "uk_replace_payload_target_leaf_mismatch_gap",
    ),
    ("uk_replay_table_shape_gap", "uk_table_shape_gap"),
    ("uk_replay_text_target_empty_surface_gap", "uk_text_target_empty_surface_gap"),
    (
        "uk_replay_text_match_citation_tail_surface_gap",
        "uk_text_match_citation_tail_surface_gap",
    ),
    (
        "uk_replay_text_match_normalized_preimage_present_gap",
        "uk_text_match_normalized_preimage_present_gap",
    ),
    (
        "uk_replay_text_match_non_substantive_selector_gap",
        "uk_text_match_non_substantive_selector_gap",
    ),
    (
        "uk_replay_text_match_multi_fragment_selector_gap",
        "uk_text_match_multi_fragment_selector_gap",
    ),
    (
        "uk_replay_text_match_synthetic_selector_gap",
        "uk_text_match_synthetic_selector_gap",
    ),
    (
        "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
        "uk_text_patch_preimage_drift_multi_prior_same_target",
    ),
    ("uk_replay_repealed_target_gap", "uk_repealed_target_gap"),
)

_UK_REPLAY_SOURCE_SHAPE_RESIDUAL_DEFAULT_KIND = "uk_empty_schedule_shape_gap"
_UK_REPLAY_SOURCE_SHAPE_RESIDUAL_DEFAULT_ADJUDICATION_KINDS = frozenset(
    {
        "uk_replay_crossheading_and_structural_repeal_unresolved",
        "uk_replay_definition_anchor_lexical_variant_recovered",
        "uk_replay_empty_schedule_shape_gap",
        "uk_replay_same_source_text_patch_overlap_blocked",
        "uk_replay_schedule_list_entry_replace_unresolved",
        "uk_replay_schedule_list_entry_table_rows_insert_unresolved",
        "uk_replay_schedule_table_end_rows_insert_unresolved",
        "uk_replay_table_column_insert_unresolved",
        "uk_replay_table_entry_inline_text_insertion_unresolved",
        "uk_replay_table_entry_inline_text_preimage_gap",
        "uk_replay_table_entry_row_insert_unresolved",
    }
)

_UK_REPLAY_TEXT_SURFACE_RESIDUAL_KIND_PRIORITY: tuple[tuple[str, str], ...] = (
    (
        "uk_replay_text_match_already_rewritten",
        "uk_text_match_already_rewritten",
    ),
    (
        "uk_replay_text_patch_preimage_drift",
        "uk_text_patch_preimage_drift",
    ),
    (
        "uk_replay_heading_text_preimage_gap",
        "uk_heading_text_preimage_gap",
    ),
    (
        "uk_replay_text_insert_anchor_preimage_gap",
        "uk_text_insert_anchor_preimage_gap",
    ),
    (
        "uk_replay_text_monetary_amount_preimage_gap",
        "uk_text_monetary_amount_preimage_gap",
    ),
    (
        "uk_replay_text_parenthetical_omission_preimage_gap",
        "uk_text_parenthetical_omission_preimage_gap",
    ),
    (
        "uk_replay_text_match_citation_connector_surface_gap",
        "uk_text_match_citation_connector_surface_gap",
    ),
    (
        "uk_replay_text_match_article_phrase_surface_gap",
        "uk_text_match_article_phrase_surface_gap",
    ),
    (
        "uk_replay_text_match_missing",
        "uk_text_match_missing",
    ),
)


def classify_uk_replay_adjudication_bucket(kind: str) -> str:
    """Classify an emitted UK replay adjudication into an evidence bucket."""
    normalized = str(kind or "").strip()
    if normalized in UK_REPLAY_BUG_ADJUDICATION_KINDS:
        return "replay_bug"
    if normalized in UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS:
        return "source_shape"
    if normalized in UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS:
        return "text_surface"
    if normalized in UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS:
        return "nonblocking_observation"
    return "unknown"


def classify_uk_bench_comparison(
    *,
    n_enacted_eids: int,
    n_oracle_eids: int,
    n_effects: int,
    raw_score: float,
    effect_source_pathology_counts: dict[str, int] | None = None,
) -> str:
    """Classify whether a UK bench row is commensurable for replay work.

    This deliberately operates on post-parse facts rather than raw XML folklore.
    It is not a replay diagnosis; it is a benchmark-triage classification.
    """
    if n_oracle_eids <= 0:
        return "no_oracle_eids"
    if n_enacted_eids <= 0:
        return "no_enacted_eids"
    if raw_score >= 1.0:
        return "commensurable"
    if (
        n_effects > 0
        and effect_source_pathology_counts
        and sum(effect_source_pathology_counts.values()) >= n_effects
        and set(effect_source_pathology_counts) <= {"nonstructural_root_gap"}
    ):
        return "nonstructural_current_projection"
    if n_enacted_eids >= max(3 * n_oracle_eids, 30) and raw_score < 0.5:
        return "oracle_collapsed_structure"
    if n_oracle_eids >= max(2 * n_enacted_eids, 30):
        if n_effects > 0:
            return "unapplied_oracle_expansion"
        return "oracle_expansion_without_effects"
    return "commensurable"


def normalize_uk_replay_compare_eids(
    replayed_eids: Iterable[str],
    oracle_eids: Iterable[str],
    oracle_physical_eid_aliases: dict[str, str] | None = None,
    oracle_visible_number_eid_aliases: dict[str, str] | None = None,
) -> tuple[set[str], set[str]]:
    """Normalize UK replay-vs-oracle EID sets for known compare-shape noise.

    This is intentionally narrow and only applies to replay comparison, not
    mutation semantics. It currently handles:

    - official oracle EID parent-path drift where XML physical ancestry proves
      a different intermediate parent while preserving the same root and leaf
    - official oracle schedule EID display-number drift where the XML `Pnumber`
      visibly names a leaf label hidden by an `n` placeholder in the EID
    - non-legal UK text-fragment IDs such as `p00090`
    - case-only EID drift (`2a` vs `2A`)
    - source URI ordinal drift for generic UK containers (`part-n2` vs
      `part-2`, `schedule-paragraph-1` vs `schedule-1-paragraph-1`)
    - replay-only descendant nodes under a `section` / `article` / `schedule`
      root when oracle exposes only the collapsed root text and no child EIDs
    - replay-only wrapper paragraph nodes under `part` / `crossheading` parents
      where oracle collapses the paragraph into the parent node
    - replay-only wrapper nodes whose descendants exist in oracle but the
      wrapper itself does not (`paragraph 2A` vs oracle-only `2A-1..4`)
    - replay-only table fallback nodes when the oracle EID surface has no table
      EIDs; table wording remains compared through ancestor text, but row/cell
      fallback identity is not yet a common benchmark surface
    """
    def _proper_prefixes(eid: str) -> tuple[str, ...]:
        parts = [part for part in str(eid or "").split("-") if part]
        return tuple("-".join(parts[:idx]) for idx in range(1, len(parts)))

    alias_norm: dict[str, str] = {}
    for aliases in (oracle_physical_eid_aliases or {}, oracle_visible_number_eid_aliases or {}):
        for original, replacement in aliases.items():
            normalized_original = _normalize_uk_source_container_eid(original)
            normalized_replacement = _normalize_uk_source_container_eid(replacement)
            if (
                normalized_original
                and normalized_replacement
                and normalized_original != normalized_replacement
            ):
                alias_norm[normalized_original] = normalized_replacement

    replay_norm = {
        alias_norm.get(normalized, normalized)
        for eid in replayed_eids
        if (normalized := _normalize_uk_source_container_eid(eid))
        and not _is_uk_nonlegal_text_fragment_eid(normalized)
    }
    oracle_norm = {
        alias_norm.get(normalized, normalized)
        for eid in oracle_eids
        if (normalized := _normalize_uk_source_container_eid(eid))
        and not _is_uk_nonlegal_text_fragment_eid(normalized)
    }
    dropped_prefixes: set[str] = set()
    kept: set[str] = set()
    collapsed_roots: set[str] = set()
    oracle_has_table_eids = any(_uk_compare_eid_has_table_segment(eid) for eid in oracle_norm)
    oracle_proper_prefixes: set[str] = set()
    replay_proper_prefix_counts: Counter[str] = Counter()
    replay_proper_prefixes_by_eid: dict[str, tuple[str, ...]] = {}
    for eid in oracle_norm:
        oracle_proper_prefixes.update(_proper_prefixes(eid))
    for eid in replay_norm:
        prefixes = _proper_prefixes(eid)
        replay_proper_prefixes_by_eid[eid] = prefixes
        replay_proper_prefix_counts.update(prefixes)

    for root in oracle_norm:
        if not root.startswith(("section-", "article-", "schedule-", "crossheading-")):
            continue
        if root in oracle_proper_prefixes:
            continue
        if replay_proper_prefix_counts.get(root, 0) >= 2:
            collapsed_roots.add(root)

    for eid in sorted(replay_norm, key=lambda s: len(s)):
        if any(eid == prefix or eid.startswith(prefix + "-") for prefix in dropped_prefixes):
            continue
        if collapsed_roots and any(
            prefix in collapsed_roots for prefix in replay_proper_prefixes_by_eid.get(eid, ())
        ):
            continue
        if not oracle_has_table_eids and _uk_compare_eid_has_table_segment(eid):
            continue
        if eid in oracle_norm:
            kept.add(eid)
            continue
        if eid in oracle_proper_prefixes:
            continue
        match = re.match(r"(.+?)(?:_|-)paragraph-[0-9a-z]+$", eid)
        if match:
            parent = match.group(1)
            if parent in oracle_norm and ("-part-" in parent or "-crossheading-" in parent):
                dropped_prefixes.add(eid)
                continue
        kept.add(eid)

    return kept, oracle_norm


def classify_uk_current_projection_eid_shape(
    *,
    enacted_eids: Iterable[str],
    oracle_eids: Iterable[str],
) -> str:
    """Classify current-oracle projections that are not replay-frontier claims."""
    enacted_norm = {_normalize_uk_source_container_eid(eid) for eid in enacted_eids if eid}
    oracle_norm = {_normalize_uk_source_container_eid(eid) for eid in oracle_eids if eid}
    if not enacted_norm or not oracle_norm:
        return ""
    if not oracle_norm < enacted_norm:
        return ""
    if len(oracle_norm) > 5:
        return ""
    if len(enacted_norm) < 3 * len(oracle_norm):
        return ""
    roots: set[str] = set()
    for eid in oracle_norm:
        match = re.match(r"^(section|article|rule|regulation)-([^-]+)", eid)
        if match is None:
            return ""
        roots.add(f"{match.group(1)}-{match.group(2)}")
    if len(roots) != 1:
        return ""
    return "spent_amending_act_current_projection"


def classify_uk_commencement_current_projection(
    *,
    replay_compare_eids: Iterable[str],
    oracle_compare_eids: Iterable[str],
    commenced_replay_eids: Iterable[str],
    commenced_oracle_eids: Iterable[str],
) -> str:
    """Classify current-oracle surfaces that project a commenced subset.

    This is a benchmark/adjudication classifier, not a replay normalizer. It
    applies only when the full oracle EID surface is contained in replay, while
    the independently computed commencement lens exactly agrees. In that shape
    the remaining full-score deficit is replay-extra future/uncommenced
    structure, not an unsupported mutation.
    """
    replay_norm = {_normalize_uk_source_container_eid(eid) for eid in replay_compare_eids if eid}
    oracle_norm = {_normalize_uk_source_container_eid(eid) for eid in oracle_compare_eids if eid}
    commenced_replay_norm = {
        _normalize_uk_source_container_eid(eid) for eid in commenced_replay_eids if eid
    }
    commenced_oracle_norm = {
        _normalize_uk_source_container_eid(eid) for eid in commenced_oracle_eids if eid
    }
    if not replay_norm or not oracle_norm or not commenced_oracle_norm:
        return ""
    if not oracle_norm < replay_norm:
        return ""
    if commenced_replay_norm != commenced_oracle_norm:
        return ""
    if not commenced_oracle_norm <= oracle_norm:
        return ""
    replay_extra_count = len(replay_norm - oracle_norm)
    if replay_extra_count < max(10, len(oracle_norm) // 10):
        return ""
    return "commencement_current_projection"


def _normalize_uk_source_container_eid(eid: str) -> str:
    parts: list[str] = []
    for part in str(eid or "").lower().split("-"):
        if not part:
            continue
        match = re.fullmatch(r"(chapter|part|schedule|paragraph)([0-9]+[a-z]?)", part)
        if match is not None:
            parts.extend([match.group(1), match.group(2)])
            continue
        parts.append(part)
    if not parts:
        return ""
    normalized: list[str] = []
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        child_starts = _UK_SOURCE_CONTAINER_EID_CHILD_STARTS.get(part)
        if child_starts is None:
            normalized.append(part)
            idx += 1
            continue
        normalized.append(part)
        next_part = parts[idx + 1] if idx + 1 < len(parts) else ""
        if not next_part:
            normalized.append("1")
            idx += 1
            continue
        match = re.fullmatch(r"n(?P<label>[0-9]+[a-z]?)", next_part)
        if match is not None:
            normalized.append(match.group("label"))
            idx += 2
            continue
        if re.fullmatch(r"[0-9]+[a-z]?", next_part):
            idx += 1
            continue
        if next_part in child_starts:
            normalized.append("1")
        idx += 1
    return "-".join(normalized)


def _is_uk_nonlegal_text_fragment_eid(eid: str) -> bool:
    return re.fullmatch(r"p[0-9]{4,}[a-z]?", str(eid or "").lower()) is not None


def _uk_compare_eid_has_table_segment(eid: str) -> bool:
    parts = [part for part in re.split(r"[-_]+", str(eid or "").lower()) if part]
    return any(part in {"table", "row", "cell", "header", "headercell"} for part in parts)


def is_core_uk_comparison(comparison_class: str) -> bool:
    """Return True when the UK comparison belongs in the core replay frontier."""
    return comparison_class in UK_CORE_COMPARISON_CLASSES


def _uk_replay_residual_kind_for_side(
    base_kind: str,
    *,
    replay_only: list[str],
    oracle_only: list[str],
) -> str:
    if replay_only and oracle_only:
        return f"{base_kind}_mixed_residual_eids"
    if replay_only:
        return f"{base_kind}_replay_only_residual_eids"
    if oracle_only:
        return f"{base_kind}_oracle_only_residual_eids"
    return base_kind


def classify_uk_replay_residual(
    *,
    only_in_replayed: Iterable[str] = (),
    only_in_oracle: Iterable[str] = (),
    adjudication_kinds: Iterable[str] = (),
) -> tuple[str, str]:
    """Classify UK replay residuals into proved-vs-unresolved buckets.

    Residual EID mismatch alone is not sufficient to prove a replay bug.
    We only promote to PROVED_REPLAY_BUG when the replay engine emitted a
    direct replay-owned adjudication. Otherwise the residual remains
    unresolved and is partitioned by which side still carries residue.
    """
    replay_only = [str(eid or "") for eid in only_in_replayed if str(eid or "")]
    oracle_only = [str(eid or "") for eid in only_in_oracle if str(eid or "")]
    adjudications = {
        str(kind or "")
        for kind in adjudication_kinds
        if str(kind or "")
    }
    if adjudications & UK_REPLAY_BUG_ADJUDICATION_KINDS:
        for kind in UK_REPLAY_BUG_PROOF_KIND_PRIORITY:
            if kind in adjudications:
                return ("PROVED_REPLAY_BUG", UK_REPLAY_BUG_PROOF_KIND_BY_ADJUDICATION_KIND[kind])
    if adjudications & UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS:
        for adjudication_kind, residual_kind in _UK_REPLAY_SOURCE_SHAPE_RESIDUAL_KIND_PRIORITY:
            if adjudication_kind in adjudications:
                return ("UNRESOLVED", residual_kind)
        return ("UNRESOLVED", _UK_REPLAY_SOURCE_SHAPE_RESIDUAL_DEFAULT_KIND)
    if adjudications & UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS:
        for adjudication_kind, residual_kind in _UK_REPLAY_TEXT_SURFACE_RESIDUAL_KIND_PRIORITY:
            if adjudication_kind in adjudications:
                return (
                    "UNRESOLVED",
                    _uk_replay_residual_kind_for_side(
                        residual_kind,
                        replay_only=replay_only,
                        oracle_only=oracle_only,
                    ),
                )
    if replay_only and oracle_only:
        return ("UNRESOLVED", "uk_mixed_residual_eids")
    if replay_only:
        return ("UNRESOLVED", "uk_replay_only_residual_eids")
    if oracle_only:
        return ("UNRESOLVED", "uk_oracle_only_residual_eids")
    return ("UNRESOLVED", "no_strong_claim")


def _normalize_effect_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _is_synthetic_text_patch_selector(text: str) -> bool:
    return bool(re.match(r"^TEXT(?:_|$)", (text or "").strip()))


def _literal_text_patch_match_present(match: str, surfaces: Iterable[str]) -> bool:
    match_text = " ".join((match or "").split()).strip()
    if not match_text:
        return False
    pattern = re.escape(match_text).replace(r"\ ", r"\s+")
    if match_text[0].isalnum():
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if match_text[-1].isalnum():
        pattern += r"(?![A-Za-z0-9])"
    if any(re.search(pattern, surface or "", re.I) for surface in surfaces):
        return True
    norm_match = _normalize_compare_text(match_text)
    if len(norm_match) <= 1:
        return False
    return any(norm_match in _normalize_compare_text(surface) for surface in surfaces)


def _looks_like_instruction_text(text: str) -> bool:
    norm = _normalize_effect_text(text)
    return bool(
        re.search(
            r"\b("
            r"insert|substitute|omit|repeal|renumber|after|before|at the end|"
            r"become|becomes|"
            r"in subsection|in paragraph|in sub-paragraph|in section|for paragraphs|for sub-paragraphs"
            r")\b",
            norm,
            re.I,
        )
    )


def _looks_like_schedule_list_entry_instruction(text: str) -> bool:
    norm = _normalize_effect_text(text)
    if not (
        re.search(
            r"\b(?:before|after|for)\s+(?:the\s+)?entry\s+(?:(?:relating|relation)\s+to|for)\b",
            norm,
        )
        or re.search(r"\bomit\s+(?:the\s+)?entry\s+for\b", norm)
    ):
        return False
    if re.search(r"\b(?:table|column|row)\b", norm):
        return False
    return bool(re.search(r"\b(?:insert|insertion|substitute|omit|repeal)\b", norm))


def _looks_like_table_entry_instruction(text: str, *, target_paths: Iterable[str] = ()) -> bool:
    norm = _normalize_effect_text(text)
    targets_norm = " ".join(str(path or "").lower() for path in target_paths)
    target_names_table = re.search(r"(?:^|[:/ -])table\b", targets_norm) is not None
    if "corresponding entry" in norm:
        return False
    has_entry_text = (
        re.search(r"\b(?:entry|entries)\b", norm) is not None
        or (target_names_table and re.search(r"\bafter\s+(?:that\s+)?entry\s+[0-9A-Za-z]+\b", norm) is not None)
        or (target_names_table and re.search(r"\bafter\s+that\s+entry\b", norm) is not None)
    )
    has_column_instruction = (
        re.search(r"\b(?:table|column|columns)\b", norm) is not None
        or target_names_table
    )
    if not has_entry_text and not has_column_instruction:
        return False
    if not re.search(
        r"\b(?:insert|inserted|substitute|substituted|omit|omitted|repeal|repealed|amend|amended|add|added)\b",
        norm,
    ):
        return False
    return bool(
        re.search(r"\b(?:table|column|columns)\b", norm)
        or (target_names_table and re.search(r"\bafter\s+(?:that\s+)?entry\b", norm))
        or (target_names_table and re.search(r"\bbetween\s+the\s+\w+\s+and\s+\w+\s+columns?\b", norm))
    )


def _looks_like_appropriate_place_insert_instruction(text: str) -> bool:
    norm = _normalize_effect_text(text)
    return bool(
        re.search(r"\bat\s+(?:an?|the)\s+appropriate\s+places?\b", norm)
        and re.search(r"\b(?:insert|insertion|substitute)\b", norm)
    )


def _looks_like_appropriate_place_definition_entry_insert_instruction(text: str) -> bool:
    norm = _normalize_effect_text(text)
    if not _looks_like_appropriate_place_insert_instruction(norm):
        return False
    if not re.search(r"\binsert(?:ed|ion)?\b", norm):
        return False
    return bool(
        re.search(
            r"[\"“][^\"”]{1,160}[\"”]\s*(?:,\s*[^;]{1,180})?\s+"
            r"(?:means|has\s+the\s+same\s+meaning|has\s+the\s+meaning|"
            r"is\s+to\s+be\s+construed|shall\s+be\s+construed|includes)\b",
            norm,
        )
    )


def _looks_like_repeal_schedule_table_source(
    *,
    extracted_tag: str | None,
    effect_type: str,
    text: str,
) -> bool:
    tag = extracted_tag or ""
    if tag not in {"Schedule", "Part", "Table", "Tgroup", "Pblock"}:
        return False
    norm_effect_type = _normalize_effect_text(effect_type)
    if not any(term in norm_effect_type for term in ("repeal", "omit")):
        return False
    norm = _normalize_effect_text(text)
    if re.search(r"\b(?:enactment|extent)\s+of\s+repeal\b", norm):
        return True
    return bool(re.search(r"\bthe\s+whole\s+act\s+except\b", norm))


def _looks_like_structural_sibling_insert_instruction(text: str) -> bool:
    norm = _normalize_effect_text(text)
    return bool(
        re.search(
            r"\bafter\s+(?:paragraph|sub-?paragraph|subsection)\s*\([0-9A-Za-z]+\)\s+insert(?:\b|\s*[—-])",
            norm,
        )
        or re.search(
            r"\bafter\s+that\s+(?:paragraph|sub-?paragraph|subsection)\s*,?\s+insert(?:\b|\s*[—-])",
            norm,
        )
        or re.search(
            r"\bat\s+the\s+end\s+of\s+(?:paragraph|sub-?paragraph|subsection)\s*\([0-9A-Za-z]+\)\s*,?\s+insert\s*[—-]",
            norm,
        )
        or re.search(
            r"\bbefore\s+(?:paragraph|sub-?paragraph|subsection)\s*\([0-9A-Za-z]+\)\s+insert(?:\b|\s*[—-])",
            norm,
        )
    )


def _looks_like_amendment_program_inserted_parent_instruction(text: str) -> bool:
    norm = _normalize_effect_text(text)
    return bool(
        re.search(r"\bin\s+the\s+inserted\s+(?:paragraph|sub-?paragraph|subsection)\b", norm)
        and re.search(
            r"\b(?:after|before)\s+(?:paragraph|sub-?paragraph|subsection)\s*\([0-9A-Za-z]+\)\s+insert(?:\b|\s*[—-])",
            norm,
        )
    )


def _looks_like_non_substantive_shell(text: str) -> bool:
    norm = " ".join((text or "").split()).strip()
    if not norm:
        return False
    # UK extracted source often preserves a list label like "b" or "i" ahead
    # of dotted shell placeholders.
    norm = re.sub(r"^[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?\s+", "", norm)
    if re.search(r"[A-Za-z]", norm):
        return False
    return norm.count(".") >= 4


def _looks_like_reference_only_source(text: str) -> bool:
    norm = " ".join((text or "").split()).strip()
    if not norm:
        return False
    norm = re.sub(r"^[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?\s+", "", norm)
    if re.search(r"[\"“”'‘’]", norm):
        return False
    if _looks_like_instruction_text(norm):
        return False
    if re.match(
        r"^(?:section|sections|subsection|subsections|paragraph|paragraphs|sub-?paragraph|sub-?paragraphs|schedule|part|chapter|article)\b",
        norm,
        re.I,
    ):
        return True
    if re.match(r"^[A-Z][A-Za-z'(),.& -]+ Act \d{4}[,.;]?$", norm):
        return True
    return False


def _looks_like_commencement_effect_source(text: str) -> bool:
    norm = " ".join((text or "").split()).strip().lower()
    if not norm:
        return False
    # Commencement instruments alter temporal applicability, not the target
    # statute text/tree, under the current UK structural replay lens.
    return bool(re.search(r"\bshall\s+come\s+into\s+force\b|\bcomes?\s+into\s+force\b", norm))


def _looks_like_conditional_temporal_repeal_source(text: str) -> bool:
    norm = " ".join((text or "").split()).strip().lower()
    if not norm:
        return False
    return bool(
        re.search(r"\b(?:is|are|be)\s+repealed\b", norm)
        and re.search(r"\b(?:at|before|after)\s+the\s+end\s+of\s+\d{4}\b", norm)
        and re.search(r"\bif\b.+\bnot\s+been\s+brought\s+into\s+force\b", norm)
    )


def _looks_like_definition_child_and_tail_substitution(text: str) -> bool:
    norm = " ".join((text or "").split()).strip().lower()
    if not norm:
        return False
    return bool(
        re.search(
            r"\bfor\s+paragraph\s+\([0-9a-z]+\)\s+of\s+the\s+definition\s+of\b",
            norm,
        )
        and re.search(r"\band\s+the\s+[“\"'‘]?(?:or|and)[”\"'’]?\s+at\s+the\s+end\s+of\s+that\s+paragraph\b", norm)
        and re.search(r"\bsubstitute\b", norm)
    )


def _target_depth(target_path: str) -> int:
    return sum(1 for part in target_path.split("/") if ":" in part)


def _target_kinds(target_path: str) -> tuple[str, ...]:
    return tuple(part.split(":", 1)[0].lower() for part in target_path.split("/") if ":" in part)


def _required_instruction_depth(text: str) -> int:
    norm = _normalize_effect_text(text)
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+sub-?paragraphs?\b", norm):
        return 3
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+paragraphs?\b", norm):
        return 3
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+subsections?\b", norm):
        return 2
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+sections?\b", norm):
        return 1
    return 0


def _target_satisfies_instruction_depth(target_path: str, *, required_depth: int, text: str) -> bool:
    if _target_depth(target_path) >= required_depth:
        return True
    norm = _normalize_effect_text(text)
    kinds = _target_kinds(target_path)
    if re.match(r"^(?:[0-9a-z]+\s+)?(?:in|for)\s+paragraphs?\b", norm):
        return "schedule" in kinds and "paragraph" in kinds and "section" not in kinds
    return False


def classify_uk_effect_source_pathology(
    *,
    extracted_tag: str | None,
    extracted_text: str,
    op_actions: Iterable[str] = (),
    payload_kinds: Iterable[str] = (),
    payload_texts: Iterable[str] = (),
    target_paths: Iterable[str] = (),
    lowering_rule_ids: Iterable[str] = (),
    effect_type: str = "",
    is_structural: bool = True,
) -> str:
    """Classify deterministic UK source-pathology facts for one inspected effect row."""
    norm_text = _normalize_effect_text(extracted_text)
    norm_effect_type = _normalize_effect_text(effect_type)
    actions = list(op_actions)
    kinds = [kind for kind in payload_kinds if kind]
    payload_norms = [_normalize_effect_text(text) for text in payload_texts if _normalize_effect_text(text)]
    targets = [path for path in target_paths if path]
    lowering_rules = {str(rule_id or "") for rule_id in lowering_rule_ids}

    if {
        "uk_effect_source_parent_substitution_range_payload_lowered",
        "uk_effect_source_parent_at_end_added_payload_lowered",
        "uk_effect_after_paragraph_insert_labelled_series_lowered",
    } & lowering_rules:
        return ""
    if not norm_text and not actions and not is_structural:
        return "nonstructural_root_gap"
    if not norm_text and not actions:
        return "missing_extracted_source"
    if norm_text and _looks_like_non_substantive_shell(extracted_text):
        return "non_substantive_shell_payload"
    if norm_text and not actions and not is_structural:
        return "nonstructural_root_gap"
    if norm_text and not actions:
        if "uk_effect_crossheading_replace_rejected" in lowering_rules:
            return "crossheading_target_unsupported"
        if "uk_effect_heading_only_ref_rejected" in lowering_rules:
            return "heading_facet_target_unsupported"
        if "uk_effect_table_entry_instruction_rejected" in lowering_rules:
            return "table_entry_target_unsupported"
        if "uk_effect_table_entry_row_insert" in lowering_rules:
            return "table_entry_target_unsupported"
        if "uk_effect_amendment_program_inserted_parent_structural_insert_rejected" in lowering_rules:
            return "amendment_text_target_unsupported"
        if "uk_effect_broad_schedule_flat_payload_rejected" in lowering_rules:
            return "broad_schedule_flat_payload_unsupported"
        if "uk_effect_empty_type_as_if_words_omitted_rejected" in lowering_rules:
            return "temporary_as_if_word_omission_unsupported"
        if "uk_effect_commencement_source_rejected" in lowering_rules:
            return "commencement_effect_out_of_scope"
        if _looks_like_conditional_temporal_repeal_source(norm_text):
            return "conditional_temporal_repeal_unsupported"
        if _looks_like_definition_child_and_tail_substitution(norm_text):
            return "definition_child_and_tail_substitution_unsupported"
        if "uk_effect_application_modification_payload_rejected" in lowering_rules:
            return "application_modification_payload_out_of_scope"
        if "uk_effect_schedule_note_target_rejected" in lowering_rules:
            return "schedule_note_target_unsupported"
        targets_norm = " ".join(targets).lower()
        if re.search(r"(?:^|[:/ -])cross[-_ ]?heading\b", targets_norm):
            return "crossheading_target_unsupported"
        if re.search(r"(?:^|[:/ -])(?:heading|title|sidenote)\b", targets_norm):
            return "heading_facet_target_unsupported"
        if re.search(r"\bfor (?:the )?inserted text\b", norm_text):
            return "amendment_text_target_unsupported"
        if _looks_like_table_entry_instruction(norm_text, target_paths=targets):
            return "table_entry_target_unsupported"
        if _looks_like_schedule_list_entry_instruction(norm_text):
            return "schedule_list_entry_target_unsupported"
        if _looks_like_amendment_program_inserted_parent_instruction(norm_text):
            return "amendment_text_target_unsupported"
        if _looks_like_structural_sibling_insert_instruction(norm_text):
            return "structural_sibling_insert_unsupported"
        if _looks_like_appropriate_place_definition_entry_insert_instruction(norm_text):
            return "appropriate_place_definition_entry_insert_unsupported"
        if _looks_like_appropriate_place_insert_instruction(norm_text):
            return "appropriate_place_insert_unsupported"
        if _looks_like_repeal_schedule_table_source(
            extracted_tag=extracted_tag,
            effect_type=effect_type,
            text=extracted_text,
        ):
            return "repeal_schedule_table_source_unsupported"
        if re.search(
            r"\b(?:shall\s+have\s+effect\s+(?:as\s+if|subject\s+to)|shall\s+be\s+read\s+as\s+if)\b",
            norm_text,
        ):
            return "as_if_application_modification_unsupported"
        if _looks_like_commencement_effect_source(norm_text):
            return "commencement_effect_out_of_scope"
        if (
            (extracted_tag or "") == "BlockAmendment"
            and (
                not norm_effect_type
                or norm_effect_type.startswith(("word ", "words "))
            )
            and (
                "uk_effect_lowering_no_supported_action_rejected" in lowering_rules
                or
                norm_text.startswith(";")
                or re.match(r"^(?:[a-z]+|\([a-z0-9]+\))\s+(?:or|and|in|to|then)\b", norm_text)
                or (re.search(r"[—-]", norm_text) and re.search(r"\b[a-z]\s+(?:after|with|and|or|if)\b", norm_text))
            )
        ):
            return "payload_fragment_without_action_formula"
        if re.search(
            r"\bwhere\s+(?:it|they|those words?)\s+"
            r"(?:occurs?|appear)s?\s+in\s+"
            r"(?:subsections?|paragraphs?|sub-paragraphs?)\b",
            norm_text,
        ):
            return "source_carried_multi_subunit_text_rewrite_unsupported"
        if re.search(
            r"\b(?:word|words)\s+following\s+(?:paragraph|sub-paragraph|subsection)\s+\([^)]+\)\s+"
            r"(?:is|are)\s+(?:omitted|repealed)",
            norm_text,
        ) or re.search(
            r"\bfor\s+the\s+words\s+after\s+(?:paragraph|sub-paragraph|subsection)\s+\([^)]+\)\s+substitute\b",
            norm_text,
        ):
            return "source_carried_child_tail_text_rewrite_unsupported"
        if _looks_like_instruction_text(norm_text):
            return "unhandled_instruction_text"
        if _looks_like_reference_only_source(extracted_text):
            return "reference_only_source_fragment"
        if norm_effect_type.startswith("word ") or norm_effect_type.startswith("words "):
            return "fragment_context_missing"
        return ""

    if norm_text and payload_norms and any(payload == norm_text for payload in payload_norms):
        if (extracted_tag or "") in {"Schedule", "BlockAmendment"} and len(norm_text) >= 200:
            return "broad_source_reused_as_payload"
        if _looks_like_instruction_text(norm_text):
            return "instruction_text_reused_as_payload"

    if (
        norm_text
        and (extracted_tag or "") == "Schedule"
        and len(norm_text) >= 200
        and "repeal" in actions
        and "schedule" in kinds
    ):
        return "broad_source_reused_as_payload"

    if norm_text and targets:
        if "uk_effect_source_carried_definition_child_text_omission_text_patch" in lowering_rules:
            return ""
        if (
            norm_effect_type.startswith("substituted for ")
            and "-" in norm_effect_type
            and re.search(r"\bfor sections?\b", norm_text)
            and re.search(r"\bsubstitute\b", norm_text)
            and any("part:" in path.lower() and "chapter:" in path.lower() for path in targets)
        ):
            return "range_to_container_target_unsupported"

        required_depth = _required_instruction_depth(norm_text)
        if required_depth > 0:
            if not any(
                _target_satisfies_instruction_depth(
                    path,
                    required_depth=required_depth,
                    text=norm_text,
                )
                for path in targets
            ):
                return "misselected_target_context"

    return ""


def is_core_uk_effect_source_candidate(pathology_class: str) -> bool:
    """Return True when an inspected UK effect row is not already a typed source pathology."""
    return pathology_class not in UK_EFFECT_SOURCE_PATHOLOGY_CLASSES


def _looks_like_source_carried_structured_text_patch_payload(text: str) -> bool:
    """Return True for payload-only fragments that visibly carry child structure.

    The parent source instruction may supply the quoted anchor, but the payload
    itself already proves that lowering as a flat text patch would lose
    structure.  This is only a manual-frontier classifier, not authorization to
    replay the fragment.
    """
    norm = " ".join(str(text or "").lower().split())
    if not norm or re.search(r"\bthen\b", norm):
        return False
    return bool(
        re.search(r"[—-]\s*(?:\(?[a-z0-9]+\)?|[ivxlcdm]+)\s+\w", norm)
        or re.match(r"^(?:[ivxlcdm]+|[a-z])\s+where\b", norm)
        or re.match(r"^;\s*(?:or|and)\s+(?:\(?[a-z0-9]+\)?\s+)", norm)
    )


def _looks_like_effect_metadata_carried_text_patch_fragment(
    *,
    effect_type: str,
    text: str,
) -> bool:
    """Return True when feed metadata carries action but source text is an object fragment.

    This is a frontier classifier only. It identifies rows where deterministic
    UK lowering could combine official effect metadata with a source-carried
    quoted/object fragment, without authorizing replay from the fragment alone.
    """
    norm_effect_type = _normalize_effect_text(effect_type)
    if not norm_effect_type.startswith(("word ", "words ")):
        return False
    if not re.search(
        r"\b(?:insert|insertion|omit|omitted|repeal|repealed|substitut)",
        norm_effect_type,
    ):
        return False

    norm = _normalize_effect_text(text)
    if not norm:
        return False
    if re.search(
        r"\b(?:insert|inserted|substitute|substituted|omit|omitted|repeal|repealed)\b",
        norm,
    ):
        return False
    return bool(
        re.search(r"[\"“][^\"”]{1,240}[\"”]", norm)
        or re.search(r"\bthe\s+words?\b", norm)
        or re.search(r"\bthe\s+definition\s+of\b", norm)
    )


def _looks_like_definition_target_without_inserted_payload(
    *,
    effect_type: str,
    text: str,
) -> bool:
    norm_effect_type = _normalize_effect_text(effect_type)
    if "insert" not in norm_effect_type:
        return False
    norm = _normalize_effect_text(text)
    if not re.search(r"\bthe\s+definition\s+of\s+[\"“][^\"”]{1,160}[\"”]", norm):
        return False
    if re.search(r"\b(?:means|has\s+the\s+meaning|includes)\b", norm):
        return False
    return bool(re.search(r"\bin\s+section\s*\(?[0-9A-Za-z]+", norm))


def _uk_manual_frontier_classification(
    table: dict[str, _ManualFrontierClassification],
    source_pathology: str,
) -> dict[str, str] | None:
    classification = table.get(source_pathology)
    if classification is None:
        return None
    status, rule_id, reason = classification
    return {"status": status, "rule_id": rule_id, "reason": reason}


def classify_uk_manual_compile_frontier(  # noqa: PLR0913
    *,
    effect_type: str,
    source_pathology: str,
    extracted_tag: str,
    extracted_text: str,
    lowering_rejections: Iterable[dict[str, Any]],
    compiled_op_count: int,
    replay_applicable: bool,
    structural_for_replay: bool,
    compare_shape: str = "",
) -> dict[str, str]:
    """Classify whether a UK blocked row belongs in deterministic or manual work.

    This is an evidence/triage surface only. It must not alter lowering,
    replay, candidate gating, or benchmark scoring.
    """
    lowering_rows = tuple(lowering_rejections)
    blocking_rules = {
        str(rejection.get("rule_id") or "")
        for rejection in lowering_rows
        if is_blocking_compile_record(rejection)
    }
    all_rules = {str(rejection.get("rule_id") or "") for rejection in lowering_rows}
    effect_type_norm = " ".join(str(effect_type or "").lower().split())
    source_pathology_norm = str(source_pathology or "")
    compare_shape_norm = str(compare_shape or "")
    extracted_tag_norm = str(extracted_tag or "")
    extracted_text_norm = " ".join(str(extracted_text or "").lower().split())

    if compare_shape_norm == "text_patch_preimage_absent_from_target_surfaces":
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_text_patch_preimage_chain_gap",
            "reason": "The source instruction lowers to a text patch, but the quoted preimage is absent from available enacted/oracle target surfaces; acquire or prove the missing intermediate source chain before replaying or claiming it.",
        }

    if compare_shape_norm == "range_to_container_target_absent":
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_range_to_container_candidate",
            "reason": "The source substitutes a section range into a higher-level container whose feed target is absent from the available source surfaces; a manual or deterministic range-to-container migration claim must own the replaced range, new container, and lineage.",
        }

    range_source_pathology_result = _uk_manual_frontier_classification(
        _UK_MANUAL_FRONTIER_RANGE_SOURCE_PATHOLOGY_RESULTS,
        source_pathology_norm,
    )
    if range_source_pathology_result is not None:
        return range_source_pathology_result

    if (
        compiled_op_count > 0
        and not blocking_rules
        and "uk_effect_added_type_source_structuralized" in all_rules
    ):
        return {
            "status": "deterministic_frontend_supported",
            "rule_id": "uk_manual_frontier_deterministic_supported",
            "reason": "The row already lowers to replay operations through a source-verified nonstructural replay family.",
        }

    if not replay_applicable or not structural_for_replay:
        return {
            "status": "non_textual_or_out_of_scope",
            "rule_id": "uk_manual_frontier_non_textual_or_out_of_scope",
            "reason": "The selected replay lens does not admit this row as a structural text/tree replay effect.",
        }

    insufficient_source_pathology_result = _uk_manual_frontier_classification(
        _UK_MANUAL_FRONTIER_SOURCE_INSUFFICIENT_PATHOLOGY_RESULTS,
        source_pathology_norm,
    )
    if insufficient_source_pathology_result is not None:
        return insufficient_source_pathology_result

    if (
        source_pathology_norm in {"fragment_context_missing", "payload_fragment_without_action_formula"}
        and extracted_tag_norm == "BlockAmendment"
        and effect_type_norm.startswith(("word ", "words "))
        and blocking_rules
        and _looks_like_source_carried_structured_text_patch_payload(extracted_text_norm)
    ):
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_source_carried_structured_text_patch_candidate",
            "reason": "The extracted payload is a source-carried structured replacement/insert fragment; a future compiler or manual claim must combine the parent formula anchor with the payload structure instead of flattening it into host text.",
        }

    if (
        source_pathology_norm
        in {
            "broad_source_reused_as_payload",
            "fragment_context_missing",
            "instruction_text_reused_as_payload",
            "payload_fragment_without_action_formula",
            "reference_only_source_fragment",
            "broad_schedule_flat_payload_unsupported",
            "temporary_as_if_word_omission_unsupported",
        }
        and blocking_rules
    ):
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_source_pathology_insufficient",
            "reason": "The blocking row is dominated by source-shape pathology rather than an unambiguous manual compilation opportunity.",
        }

    if compiled_op_count > 0 and not blocking_rules:
        return {
            "status": "deterministic_frontend_supported",
            "rule_id": "uk_manual_frontier_deterministic_supported",
            "reason": "The row already lowers to replay operations without blocking lowering rejections.",
        }

    main_source_pathology_result = _uk_manual_frontier_classification(
        _UK_MANUAL_FRONTIER_MAIN_SOURCE_PATHOLOGY_RESULTS,
        source_pathology_norm,
    )
    if main_source_pathology_result is not None:
        return main_source_pathology_result

    if source_pathology_norm == "table_entry_target_unsupported":
        entry_shapes = {
            str(rejection.get("entry_shape") or "")
            for rejection in lowering_rows
            if str(rejection.get("entry_shape") or "")
        }
        if "deictic_table_entry" in entry_shapes:
            return {
                "status": "manual_compile_candidate",
                "rule_id": "uk_manual_frontier_table_entry_deictic_candidate",
                "reason": "The source uses deictic table-entry placement such as 'after that entry'; a future compiler must prove the antecedent from source context, resolve exactly one table row, and preserve the source-owned row payload.",
            }
        if "between_columns" in entry_shapes:
            return {
                "status": "manual_compile_candidate",
                "rule_id": "uk_manual_frontier_table_column_insert_candidate",
                "reason": "The source inserts material between table columns; this needs a column-insertion compiler that proves the column boundary, row alignment, and rowspan handling instead of replaying a row insertion.",
            }
        if "appropriate_place_table_entry" in entry_shapes:
            return {
                "status": "manual_compile_candidate",
                "rule_id": "uk_manual_frontier_table_appropriate_place_candidate",
                "reason": "The source inserts table rows at an appropriate place without an explicit row anchor; a placement claim or compiler must prove the predecessor/successor or table ordering rule before replay.",
            }
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_table_entry_candidate",
            "reason": "The source targets a table entry/column surface; a claim or future table compiler must identify the row and cell rather than mutating host body text.",
        }

    if "uk_effect_heading_only_ref_rejected" in blocking_rules:
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_heading_facet_candidate",
            "reason": "The source targets a heading/title/sidenote facet; a manual claim could target an explicit facet without mutating the host body.",
        }

    if "uk_effect_crossheading_replace_rejected" in blocking_rules:
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_crossheading_candidate",
            "reason": "The source targets a cross-heading surface that needs an explicit crossheading/facet claim.",
        }

    if "uk_effect_appropriate_place_definition_entry_insert_rejected" in blocking_rules:
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_appropriate_place_definition_entry_candidate",
            "reason": "The source inserts a definition entry at an appropriate place without naming an anchor; a claim or future placement compiler must supply and validate the exact definition-entry insertion point instead of inferring it from live text.",
        }

    if "uk_effect_external_act_target_rejected" in blocking_rules:
        return {
            "status": "non_textual_or_out_of_scope",
            "rule_id": "uk_manual_frontier_external_act_target_out_of_scope",
            "reason": "The affecting source names a different Act as the mutation target; the row is not a manual compilation opportunity for the current statute.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and any(
            "table" in str(rejection.get("original_affected_provisions") or "").lower()
            or "table" in str(rejection.get("affected_provisions") or "").lower()
            for rejection in lowering_rows
        )
        and _looks_like_table_entry_instruction(
            extracted_text_norm,
            target_paths=(
                str(rejection.get("original_affected_provisions") or rejection.get("affected_provisions") or "")
                for rejection in lowering_rows
            ),
        )
    ):
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_table_entry_candidate",
            "reason": "The source targets a table entry/column surface; a claim or future table compiler must identify the row and cell rather than mutating host body text.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and re.search(r"\bat the appropriate place(?:s)?\b", extracted_text_norm)
        and re.search(r"\b(?:insert|substitute)\b", extracted_text_norm)
    ):
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_appropriate_place_candidate",
            "reason": "The source asks for appropriate-place placement; a claim or future placement compiler must identify the insertion anchor without guessing from live text.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and _looks_like_schedule_list_entry_instruction(extracted_text_norm)
    ):
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_schedule_list_entry_candidate",
            "reason": "The source targets a schedule/list entry by anchor entry text; a claim or future list-entry compiler must identify the entry carrier and sibling insertion point rather than mutating collapsed schedule text.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and _looks_like_structural_sibling_insert_instruction(extracted_text_norm)
    ):
        return {
            "status": "deterministic_frontend_candidate",
            "rule_id": "uk_manual_frontier_structural_sibling_insert_candidate",
            "reason": "The source inserts new structural siblings after a named child; a future compiler must emit sibling insert operations instead of appending payload text to the anchor child.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and re.search(
            r"\bfor the period specified in\b.+\bthere\s+(?:is|are|shall\s+be)\s+substituted\b",
            extracted_text_norm,
        )
        and not re.search(r"[“\"'‘]", str(extracted_text or ""))
    ):
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_unquoted_preimage_substitution_source_insufficient",
            "reason": "The source names the new period but does not quote or otherwise provide the old text preimage; replay requires an explicit source/preimage claim rather than parser inference from the live target.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and extracted_tag_norm in {"Schedule", "Part", "Table", "Tgroup", "Pblock"}
        and any(term in effect_type_norm for term in ("repeal", "omit"))
    ):
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_repeal_table_candidate",
            "reason": "The row appears to depend on a repeal schedule/table or grouped repeal source that may need row/column compilation.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and extracted_tag_norm not in {"", "BlockAmendment"}
        and re.search(
            r"\b(?:substitute|substituted|insert|inserted|omit|omitted|repeal|repealed)\b",
            extracted_text_norm,
        )
    ):
        return {
            "status": "deterministic_frontend_candidate",
            "rule_id": "uk_manual_frontier_parser_or_extraction_candidate",
            "reason": "The source still contains explicit instruction text; prefer deterministic parser or extraction work before manual claims.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and source_pathology_norm == "unhandled_instruction_text"
        and _looks_like_definition_target_without_inserted_payload(
            effect_type=effect_type_norm,
            text=extracted_text_norm,
        )
    ):
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_definition_target_fragment_source_insufficient",
            "reason": "The source fragment identifies a definition target but does not carry the inserted words or a placement instruction; replay requires the missing parent instruction or payload evidence.",
        }

    if (
        "uk_effect_overlap_substitution_unlowered" in blocking_rules
        and source_pathology_norm == "unhandled_instruction_text"
        and _looks_like_effect_metadata_carried_text_patch_fragment(
            effect_type=effect_type_norm,
            text=extracted_text_norm,
        )
    ):
        return {
            "status": "deterministic_frontend_candidate",
            "rule_id": "uk_manual_frontier_effect_metadata_carried_text_patch_candidate",
            "reason": (
                "The official effect feed supplies a word-level action and target, "
                "while the extracted source carries only the quoted/object fragment; "
                "deterministic lowering must combine those source surfaces explicitly "
                "instead of treating the fragment as a standalone instruction."
            ),
        }

    if "uk_effect_missing_structural_payload_rejected" in blocking_rules:
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_missing_payload_source_insufficient",
            "reason": "No extracted payload is available; a closed operation cannot be claimed without better source evidence.",
        }

    if "uk_effect_non_substantive_payload_rejected" in blocking_rules:
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_non_substantive_payload_source_insufficient",
            "reason": "The available payload is non-substantive shell or dot-leader text and should not become legal content.",
        }

    if (
        "uk_effect_lowering_no_supported_action_rejected" in blocking_rules
        and not effect_type_norm
        and extracted_tag_norm == "BlockAmendment"
    ):
        return {
            "status": "source_insufficient",
            "rule_id": "uk_manual_frontier_payload_without_action_source_insufficient",
            "reason": "The source lane exposes a naked payload fragment without a supported action verb or effect type.",
        }

    if "uk_effect_metadata_cross_container_renumber_rejected" in all_rules:
        return {
            "status": "manual_compile_candidate",
            "rule_id": "uk_manual_frontier_cross_container_renumber_candidate",
            "reason": "The effect metadata proves a cross-container renumber/migration; a claim or future migration compiler must own source identity, destination identity, and descendant wrapping before replay can apply it.",
        }

    if "uk_effect_empty_type_whole_act_action_rejected" in blocking_rules:
        return {
            "status": "non_textual_or_out_of_scope",
            "rule_id": "uk_manual_frontier_empty_type_whole_act_action_out_of_scope",
            "reason": (
                "The effect row names the whole Act but has no effect type; replay "
                "must not infer a destructive whole-Act text/tree action from "
                "incidental source wording."
            ),
        }

    if "uk_effect_lowering_no_supported_action_rejected" in all_rules:
        return {
            "status": "non_textual_or_out_of_scope",
            "rule_id": "uk_manual_frontier_unsupported_effect_family",
            "reason": "The effect family is currently outside UK text/tree replay, though the diagnostic remains visible.",
        }

    return {
        "status": "unclassified_frontier",
        "rule_id": "uk_manual_frontier_unclassified",
        "reason": "No manual-frontier rule classified this row; inspect the source and lowering evidence directly.",
    }


def classify_uk_effect_compare_shape(
    *,
    affecting_title: str = "",
    effect_type: str = "",
    op_actions: Iterable[str] = (),
    payload_texts: Iterable[str] = (),
    resolver_eids: Iterable[str] = (),
    base_target_hits: Iterable[bool] = (),
    oracle_target_hits: Iterable[bool] = (),
    base_descendant_hits: Iterable[bool] = (),
    oracle_descendant_hits: Iterable[bool] = (),
    base_parent_hits: Iterable[bool] = (),
    oracle_parent_hits: Iterable[bool] = (),
    base_target_texts: Iterable[str] = (),
    oracle_target_texts: Iterable[str] = (),
    base_parent_texts: Iterable[str] = (),
    oracle_parent_texts: Iterable[str] = (),
    text_patch_matches: Iterable[str] = (),
    text_patch_replacements: Iterable[str] = (),
    lowering_rule_ids: Iterable[str] = (),
    base_has_text: bool = False,
    base_has_children: bool = False,
    oracle_has_text: bool = False,
    oracle_has_children: bool = False,
) -> str:
    """Classify deterministic compare-shape-only lanes for one inspected effect row."""
    norm_affecting_title = _normalize_effect_text(affecting_title)
    actions = {action for action in op_actions if action}
    payload_norms = [_normalize_compare_text(text) for text in payload_texts if _normalize_compare_text(text)]
    text_patch_match_texts = [
        text
        for text in text_patch_matches
        if not _is_synthetic_text_patch_selector(text) and _normalize_compare_text(text)
    ]
    text_patch_replacement_texts = [
        text
        for text in text_patch_replacements
        if not _is_synthetic_text_patch_selector(text) and _normalize_compare_text(text)
    ]
    lowering_rules = {rule_id for rule_id in lowering_rule_ids if rule_id}
    resolved = [eid for eid in resolver_eids if eid]
    base_hits = list(base_target_hits)
    oracle_hits = list(oracle_target_hits)
    base_descendants = list(base_descendant_hits)
    oracle_descendants = list(oracle_descendant_hits)
    base_parents = list(base_parent_hits)
    oracle_parents = list(oracle_parent_hits)
    base_target_text_surfaces = tuple(base_target_texts)
    oracle_target_text_surfaces = tuple(oracle_target_texts)
    base_target_norms = [
        _normalize_compare_text(text) for text in base_target_text_surfaces if _normalize_compare_text(text)
    ]
    oracle_target_norms = [
        _normalize_compare_text(text) for text in oracle_target_text_surfaces if _normalize_compare_text(text)
    ]
    base_parent_norms = [
        _normalize_compare_text(text) for text in base_parent_texts if _normalize_compare_text(text)
    ]
    oracle_parent_norms = [
        _normalize_compare_text(text) for text in oracle_parent_texts if _normalize_compare_text(text)
    ]

    if not resolved:
        if (
            actions == {"replace"}
            and (effect_type or "").strip().lower().startswith("substituted for ")
            and "-" in (effect_type or "")
        ):
            return "range_to_container_target_absent"
        return ""
    text_patch_preimage_absent = (
        actions & {"text_replace", "text_repeal"}
        and text_patch_match_texts
        and (base_hits or oracle_hits)
        and (any(base_hits) or any(oracle_hits))
        and (base_target_norms or oracle_target_norms)
        and not any(
            _literal_text_patch_match_present(
                match,
                (*base_target_text_surfaces, *oracle_target_text_surfaces),
            )
            for match in text_patch_match_texts
        )
    )
    if (
        text_patch_preimage_absent
        and "text_replace" in actions
        and lowering_rules & UK_COMPARE_CHAINED_TEXT_REWRITE_RULE_IDS
        and base_hits
        and oracle_hits
        and not any(base_hits)
        and any(oracle_hits)
        and any(
            _literal_text_patch_match_present(replacement, oracle_target_text_surfaces)
            for replacement in text_patch_replacement_texts
        )
    ):
        return "uk_compare_text_patch_preimage_consumed_by_replay_chain"
    if text_patch_preimage_absent and lowering_rules & UK_COMPARE_TABLE_CELL_TEXT_PATCH_RULE_IDS:
        return "table_cell_text_patch_requires_table_surface"
    if text_patch_preimage_absent:
        return "text_patch_preimage_absent_from_target_surfaces"
    if (
        "gibraltar" in norm_affecting_title
        and "insert" in actions
        and (oracle_hits or oracle_descendants)
        and not any(oracle_hits)
        and not any(oracle_descendants)
        and (
            (base_parents and any(base_parents))
            or (oracle_parents and any(oracle_parents))
            or (base_hits and any(base_hits))
        )
    ):
        return "territorial_extension_oracle_gap"
    if (
        "gibraltar" in norm_affecting_title
        and actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and base_target_norms
        and oracle_target_norms
        and any(base_text == oracle_text for base_text in base_target_norms for oracle_text in oracle_target_norms)
    ):
        return "territorial_extension_oracle_gap"
    if (
        "gibraltar" in norm_affecting_title
        and actions & {"text_replace", "text_repeal"}
        and (oracle_hits or oracle_parents)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
    ):
        return "territorial_extension_oracle_gap"
    if (
        actions == {"repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and any(base_descendants)
        and any(oracle_descendants)
    ):
        return "retained_repeal_oracle_branch"
    if (
        actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and not any(oracle_hits)
        and (base_parents or oracle_parents)
        and any(base_parents)
        and not any(oracle_parents)
    ):
        return "oracle_missing_live_branch"
    if base_has_children and not base_has_text and oracle_has_text and not oracle_has_children:
        return "collapsed_subtree_oracle_shape"
    if (
        actions & {"text_replace", "text_repeal"}
        and (base_hits or oracle_hits)
        and any(base_hits)
        and any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and not any(base_descendants)
        and any(oracle_descendants)
        and base_has_text
        and oracle_has_text
    ):
        return "collapsed_subtree_oracle_shape"
    if (
        "insert" in actions
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
        and payload_norms
        and oracle_parent_norms
    ):
        for payload in payload_norms:
            if len(payload) < 16:
                continue
            if any(payload in parent for parent in oracle_parent_norms) and not any(
                payload in parent for parent in base_parent_norms
            ):
                return "collapsed_subtree_oracle_shape"
        for eid in resolved:
            leaf = eid.rsplit("-", 1)[-1].lower()
            if len(leaf) < 2:
                continue
            if any(leaf in parent for parent in oracle_parent_norms) and not any(
                leaf in parent for parent in base_parent_norms
            ):
                return "collapsed_subtree_oracle_shape"
    if (
        "insert" in actions
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and (base_descendants or oracle_descendants)
        and not any(base_descendants)
        and any(oracle_descendants)
    ):
        return "descendant_only_oracle_wrapper"
    if not actions & {"text_replace", "text_repeal", "replace"}:
        return ""
    if (
        (effect_type or "").strip().lower().startswith("substituted for ")
        and "-" in (effect_type or "")
        and actions == {"replace"}
        and (base_hits or oracle_hits)
        and not any(base_hits)
        and not any(oracle_hits)
        and ((base_parents and any(base_parents)) or (oracle_parents and any(oracle_parents)))
    ):
        return "legacy_labeled_oracle_shape"
    return ""


def is_core_uk_effect_compare_candidate(compare_shape_class: str) -> bool:
    """Return True when an inspected UK effect row is not already compare-shape only."""
    return compare_shape_class not in UK_EFFECT_COMPARE_SHAPE_CLASSES


def build_uk_source_adjudication(
    *,
    statute_id: str,
    cutoff_date: str,
    comparison_class: str,
    lineage: Iterable[dict[str, Any]] = (),
) -> SourceAdjudication:
    """Build typed UK source adjudication from a benchmark comparison class."""
    return SourceAdjudication(
        statute_id=statute_id,
        replay_mode="uk_bench",
        cutoff_date=cutoff_date,
        oracle_version_amendment_id="current" if not cutoff_date else cutoff_date,
        oracle_suspect="" if is_core_uk_comparison(comparison_class) else comparison_class,
        lineage=tuple(lineage),
    )


__all__ = [
    "UK_CORE_COMPARISON_CLASSES",
    "UK_EFFECT_COMPARE_SHAPE_CLASSES",
    "UK_EFFECT_SOURCE_PATHOLOGY_CLASSES",
    "UK_REPLAY_BUG_ADJUDICATION_KINDS",
    "UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS",
    "UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS",
    "UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS",
    "build_uk_source_adjudication",
    "classify_uk_effect_compare_shape",
    "classify_uk_effect_source_pathology",
    "classify_uk_current_projection_eid_shape",
    "classify_uk_manual_compile_frontier",
    "classify_uk_replay_adjudication_bucket",
    "classify_uk_replay_residual",
    "classify_uk_bench_comparison",
    "is_core_uk_effect_compare_candidate",
    "is_core_uk_effect_source_candidate",
    "is_core_uk_comparison",
    "normalize_uk_replay_compare_eids",
]

"""UK Amendment Replay Pipeline.

This module implements the acquisition and op-extraction layer for building
a PIT (Point-in-Time) legal graph from first principles for UK legislation —
analogous to lawvm.finland.grafter but without LLM dependency for the
amendment schedule, since UK effects feeds provide structured metadata.

Architecture:
  1. Effects feed  → ordered list of StructuredAmendmentOps
  2. For each op: fetch the affecting act's XML from legislation.gov.uk
  3. Extract the provision text referenced by the op
  4. Compile to IR ops against the base statute IR
  5. Replay enacted base + IR ops → PIT states
  6. Compare against official consolidated versions (oracle score)

Current status:
  - effects.py owns effect-feed records, parsers, and acquisition manifests
  - AffectingActFetcher: downloads affecting act XML via legislation.gov.uk API
  - ProvisionExtractor: finds referenced provision text in affecting act XML
  - OpCompiler: converts effect/source payloads → typed IR operations
  - Replayer: applies IR ops to base enacted IR
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import Levenshtein
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, List, Optional, Sequence, cast

from lawvm.core import tree_ops
from lawvm.core.ir import (
    IRStatute,
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.canonicalize import (
    canonicalize_uk_address,
    uk_compound_subsection_candidate,
    uk_find_body_predecessor_parent,
    uk_kind_matches,
    uk_is_transparent_wrapper_kind,
    uk_recursive_kind_match,
    uk_schedule_ordinal_paragraph_matches,
    uk_schedule_root_candidates,
    uk_semantic_path_key,
    uk_should_bubble_structural_commencement,
    uk_should_descend_transparently,
)
from lawvm.uk_legislation.commencement import (
    commencement_eid_set,
)
from lawvm.uk_legislation.definition_anchors import _uk_definition_term_lexical_variants
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.uk_grafter import (
    _parse_part,
    _parse_chapter,
    _parse_section,
    _parse_p1group,
    _parse_p2,
    _parse_p3,
    _parse_p4,
    _clean_num,
    _semantic_hash,
    _LEG_NS,
    _extract_num,
    _parse_pblock,
    _parse_schedule_single,
)
from lawvm.uk_legislation.nlp_parser import US, is_whole_node_replacement, parse_fragment_substitution
from lawvm.uk_legislation.witnesses import UKLoweredOperationWitness
from lawvm.uk_legislation.effects import (
    STRUCTURAL_EFFECT_TYPES,
    UKEffectRecord,
    _COMMENCEMENT_EFFECT_TYPES,
    _is_uk_renumber_effect_type,
    build_acquisition_manifest,
    fetch_effects_for_statute,
    fetch_metadata_for_statute,
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    load_effects_for_statute,
    load_effects_for_statute_from_archive,
    load_effects_for_statute_from_raw,
    parse_effects_from_bytes,
    parse_effects_from_feeds,
    parse_effects_from_metadata,
    uk_effect_requires_affecting_source_for_replay,
)
from lawvm.uk_legislation.addressing import (
    _action_name,
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
    _canonicalize_eid_tail_label,
    _canonicalize_schedule_paragraph_eid_label,
    _order_schedule_materialization_ops,
    _schedule_target_levels,
    _uk_eid_value,
    _uk_kind_value,
)
from lawvm.uk_legislation.authority_filter import (
    _following_eid,
    _partition_uk_ops_by_authority_mode,
    _preceding_eid,
    _uk_authority_filter_diagnostic,
    _uk_op_allowed_by_authority_mode,
)
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
    _CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE,
    _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_RESOLVED_RULE_ID,
    _UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
    _crossheading_and_structural_repeal_selector,
    _crossheading_before_anchor_replacement_text,
    _crossheading_before_anchor_text_patch_fragment,
    _expand_heading_facet_section_range_ref,
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_full_replacement_fragment,
    _is_crossheading_ref,
    _is_direct_section_paragraph_ref,
    _is_heading_facet_word_patch_supported,
    _is_heading_only_ref,
    _is_schedule_note_ref,
    _is_schedule_part_abbreviation_ref,
    _mixed_heading_structural_insert_ref,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
    append_manual_compile_frontier_diagnostic,
    append_metadata_only_selection_rejection,
    append_no_ops_lowering_rejections,
    append_pit_date_filter_rejection,
    append_replay_applicability_filter_diagnostic,
    append_source_pathology_classified_diagnostic,
    append_source_pathology_filter_lowering_rejections,
    append_structural_no_ops_lowering_rejection,
    mark_nonreplay_lowering_rejections_nonblocking,
)
from lawvm.uk_legislation.lowering_actions import (
    _is_uk_word_level_effect_type,
    _to_structural_action,
    _uk_effect_type_action,
)
from lawvm.uk_legislation.metadata_rewrites import (
    UKMetadataRenumberTargets,
    _renumbered_descendant_text,
    _select_whole_schedule_element,
    _uk_metadata_renumber_targets,
    _uk_source_text_corrected_renumber_targets,
)
from lawvm.uk_legislation.mutable_ir import UKMutableNode, UKMutableStatute
from lawvm.uk_legislation.provision_extractor import (
    _extract_provision_element_from_root,
    _find_provision_greedy,
    _get_id_sequence,
    _get_ref_sequence,
    _instruction_text_before_amendment_container,
    _norm_prov_ref,
    _parse_ref,
    _select_extracted_match,
    extract_provision_element,
    extract_provision_element_from_bytes,
)
from lawvm.uk_legislation.provenance_notes import (
    NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR as _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    NOTE_EFFECT_TYPE as _NOTE_EFFECT_TYPE,
    NOTE_FRAGMENT_SUB as _NOTE_FRAGMENT_SUB,
    NOTE_METADATA_SOURCE_FALLBACK as _NOTE_METADATA_SOURCE_FALLBACK,
    NOTE_ORIGINAL_REF as _NOTE_ORIGINAL_REF,
    NOTE_PRECEDING_EID as _NOTE_PRECEDING_EID,
    NOTE_RAW_TEXT as _NOTE_RAW_TEXT,
    NOTE_REWRITE_WITNESS as _NOTE_REWRITE_WITNESS,
    NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_SELECTOR,
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
    NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION as _NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    NOTE_TABLE_ROW_INSERT_SELECTOR as _NOTE_TABLE_ROW_INSERT_SELECTOR,
    NOTE_TEXT_REWRITE_RULE as _NOTE_TEXT_REWRITE_RULE,
    _crossheading_group_repeal_selector,
    _schedule_list_entry_repeal_selector,
    _schedule_list_entry_replace_selector,
    _schedule_list_entry_selector,
    _schedule_list_entry_table_rows_selector,
    _schedule_table_end_rows_selector,
    _table_cell_selector,
    _table_column_insert_selector,
    _table_row_insert_selector,
)
from lawvm.uk_legislation.replay_text import (
    _append_definition_child_suffix_text,
    _article_phrase_content_word_present,
    _citation_connector_elided_text_match_present,
    _citation_stripped_text_match_present,
    _compact_normalized_text,
    _compact_numbered_schedule_entry_text,
    _compact_numbered_schedule_entry_text_without_article,
    _compact_schedule_entry_anchor_with_citation_short_title,
    _compact_schedule_entry_anchor_without_article,
    _definition_entry_term_absent,
    _monetary_amount_text_selector,
    _multi_fragment_text_selector,
    _non_substantive_text_selector,
    _normalize_text_for_grounding,
    _normalized_replay_subtree_text,
    _normalized_replacement_text_present,
    _normalized_text_match_present,
    _node_text_contains_text,
    _parenthetical_omission_text_selector,
    _range_anchor_matches,
    _replay_subtree_text_preview,
    _schedule_entry_parenthetical_paragraph_anchor,
    _subtree_contains_text,
    _subtree_text_match_count,
    _synthetic_text_selector,
    _text_patch_replacement_preserves_anchor,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
    _append_affecting_source_context_diagnostic,
    _build_affecting_source_context,
    _extract_from_affecting_source_context,
    _extract_from_affecting_source_context_with_observations,
    _preview_source_text,
    _select_enacted_source_for_current_shell,
    _source_parent_range_label,
)
from lawvm.uk_legislation.source_text_reclassifications import (
    SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE as _SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE,
    _empty_effect_type_as_if_words_omitted,
    _empty_effect_type_commencement_source,
    _external_act_target_from_source_text,
    _partial_whole_act_repeal_exceptions,
    _quote_only_definition_list_omission_payload_match,
    _quote_only_omission_payload_match,
    _source_parent_application_modification_context,
    _word_level_structural_subsection_omission,
)
from lawvm.uk_legislation.table_selectors import (
    UK_SCHEDULE_TABLE_END_ROWS_RULE_ID as _UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
    UK_TABLE_COLUMN_HEADING_TEXT_RULE_ID as _UK_TABLE_COLUMN_HEADING_TEXT_RULE_ID,
    UK_TABLE_COLUMN_INSERT_RULE_ID as _UK_TABLE_COLUMN_INSERT_RULE_ID,
    UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID as _UK_TABLE_COLUMN_TEXT_PATCH_RULE_ID,
    UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID as _UK_TABLE_ENTRY_DEICTIC_LABEL_COLUMN_TEXT_RULE_ID,
    UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID as _UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID,
    UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID as _UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID,
    UK_TABLE_ENTRY_LABELS_COLUMN_TEXT_RULE_ID as _UK_TABLE_ENTRY_LABELS_COLUMN_TEXT_RULE_ID,
    UK_TABLE_ENTRY_LABEL_COLUMN_TEXT_RULE_ID as _UK_TABLE_ENTRY_LABEL_COLUMN_TEXT_RULE_ID,
    UK_TABLE_ENTRY_LABEL_TEXT_RULE_ID as _UK_TABLE_ENTRY_LABEL_TEXT_RULE_ID,
    UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID as _UK_TABLE_ENTRY_RELATING_COLUMN_TEXT_RULE_ID,
    UK_TABLE_ENTRY_RELATING_TEXT_RULE_ID as _UK_TABLE_ENTRY_RELATING_TEXT_RULE_ID,
    UK_TABLE_ENTRY_ROW_INSERT_RULE_ID as _UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
    _uk_schedule_list_entry_table_payload,
    _uk_schedule_table_end_rows_selector,
    _uk_single_logical_table_entry_group_payload,
    _uk_single_table_column_payload,
    _uk_single_table_row_payload,
    _uk_broad_table_entry_instruction,
    _uk_parent_target_before_table_marker,
    _uk_table_column_text_patch_selector,
    _uk_table_column_insert_selector,
    _uk_table_entry_inline_text_selector,
    _uk_table_entry_row_insert_selector,
)
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _expand_sibling_targets_from_text,
    _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor,
    _source_label_changing_substitution_series,
    _source_replaced_sibling_count_from_substitution_text,
    _source_text_schedule_paragraph_target_override,
)
from lawvm.uk_legislation.witness_sidecars import (
    _lowered_witness_from_payload_data,
    _lowered_witness_to_payload_data,
    _payload_with_rewrite_witness,
    _uk_lowered_op_provenance_tags,
    _witness_for_op,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_applicability_witness,
    _uk_effect_witness,
    _uk_extraction_witness,
    _uk_insertion_anchor_witness,
    _uk_target_expansion_witness,
    _uk_temporal_events_from_ops,
    _uk_temporal_group_id,
    _uk_text_rewrite_spec,
)
from lawvm.uk_legislation.ordering import (
    _label_sort_key,
    _order_uk_effects_for_replay,
    _order_uk_text_patch_preimage_chains,
    _text_replace_preimage_chain_key,
    _uk_source_provision_label_sort_key,
    _uk_source_provision_order_key,
)
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.payload_identity import (
    _synthesize_payload_descendant_eids,
    _synthesize_whole_schedule_payload_descendant_eids,
)
from lawvm.uk_legislation.payload_conversion import _to_irnode, _to_mutable_node
from lawvm.uk_legislation.uk_prefetch import (
    fetch_affecting_act,
    fetch_affecting_acts_from_manifest,
)
from lawvm.uk_legislation.replay_applicability import (
    should_replay_nonstructural_ops,
)
from lawvm.uk_legislation.replay_prepare import prepare_replay_uk_ops
from lawvm.uk_legislation.replay_records import (
    UKReplayPrepareResult,
    append_replay_fold_text_duplication_adjudications,
    _append_uk_replay_adjudication,
    _build_uk_replay_adjudication,
)
from lawvm.uk_legislation.replay_table_geometry import (
    expanded_uk_table_rows,
    expanded_uk_table_rows_with_physical_index,
    resolve_uk_table_entry_row_insert_index,
    resolve_unique_uk_table_column_text_cell,
    resolve_unique_uk_table_entry_cell,
    resolve_unique_uk_table_entry_cells,
    resolve_unique_uk_table_entry_text_cell,
    resolve_unique_uk_table_relating_cell,
    strip_uk_identity_attrs_recursive,
    uk_table_column_insert_plans,
    uk_table_column_payload_cells,
    uk_table_cell_span,
    uk_table_selector_tables,
)
from lawvm.uk_legislation.schedule_list_selectors import (
    UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
    UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID as _UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
    _strip_schedule_entry_payload,
    _strip_schedule_entry_phrase,
    _uk_numbered_schedule_entry_repeal_target,
    _uk_schedule_list_entry_insert_selector,
    _uk_schedule_list_entry_repeal_selector,
    _uk_schedule_list_entry_replace_selector,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS as _UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS,
    UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID as _UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID,
    _fragment_rule_ids,
    _fragment_substitution,
    _fragment_target_suffix,
    _labeled_child_end_range_selector,
    _multi_quoted_word_repeal_fragments,
    _separate_all_occurrences_text_replace_fragments,
    _separate_definition_repeal_fragments,
    _separate_multi_quoted_word_repeal_fragments,
    _separate_occurrence_text_replace_fragments,
    _text_rewrite_rule_ids_for_op,
)
from lawvm.uk_legislation.source_context import (
    _first_amendment_container,
)
from lawvm.uk_legislation.source_child_tail_rewrites import (
    _fragment_substitution_source_carried_child_tail_repeal,
    _fragment_substitution_source_carried_child_tail_substitution,
)
from lawvm.uk_legislation.source_amendment_program_fragments import (
    _amendment_program_inserted_parent_structural_insert,
    _fragment_substitution_amendment_inserted_text_substitution,
    _fragment_substitution_source_carried_multi_subunit_repeal,
)
from lawvm.uk_legislation.source_definition_context import (
    _scope_fragment_substitutions_to_source_definition_parent,
    _source_definition_child_refined_target,
)
from lawvm.uk_legislation.source_definition_fragments import (
    _fragment_substitution_source_carried_after_quoted_anchor_insert,
    _fragment_substitution_source_carried_definition_child_at_end_insert,
    _fragment_substitution_source_carried_definition_child_insert,
    _fragment_substitution_source_carried_definition_child_text_omission,
    _fragment_substitution_source_carried_definition_entry_insert,
    _fragment_substitution_source_carried_definition_entry_substitution,
    _fragment_substitution_source_carried_following_words_repeal,
    _fragment_substitution_source_carried_quoted_text_substitution,
    _looks_like_appropriate_place_definition_entry_insert_text,
)
from lawvm.uk_legislation.source_fragment_context import (
    _fragment_substitution_after_words_inserted_by_sibling,
    _fragment_substitution_grouped_anchor_occurrence,
)
from lawvm.uk_legislation.source_labeled_child_parts import (
    _source_carried_labeled_child_replacement_parts,
)
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID as _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
    UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID as _UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID,
    _direct_payload_text,
    _flat_p1para_schedule_paragraph_insert_payload,
    _inserted_section_p1group_heading_text,
    _prepend_inserted_section_heading_carrier,
)
from lawvm.uk_legislation.source_payload_elaboration import (
    _crossheading_and_structural_replacement_heading_text,
    _expand_sibling_targets_from_extracted,
    _extract_crossheading_payload_from_extracted,
    _is_broad_schedule_flat_replace_payload,
    _is_non_substantive_structural_payload,
    _retarget_instruction_element_to_target,
    _source_payload_matches_target_leaf,
    _substituted_series_new_sibling_insert_detail,
    _with_trailing_subordinate_siblings,
)
from lawvm.uk_legislation.source_parent_payloads import (
    SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE as _SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE,
    UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID as _UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
    UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
    _source_after_paragraph_insert_labelled_series,
    _source_parent_at_end_added_payload,
    _source_parent_instruction_with_payload,
    _source_parent_substitution_range_payload,
)
from lawvm.uk_legislation.source_structural_sibling import _structural_sibling_insert_from_source
from lawvm.uk_legislation.source_table_entry_paragraph import (
    SOURCE_TABLE_CELL_PARAGRAPH_SENTINEL_RE as _TABLE_CELL_PARAGRAPH_SENTINEL_RE,
    UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID as _UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
    _source_carried_table_entry_paragraph_substitution,
)
from lawvm.uk_legislation.target_anchors import (
    _body_target_eid_suffixes,
    _fallback_target_eid,
    _source_after_insertion_anchor,
    _source_before_insertion_anchor,
    _target_anchor_eid,
)
from lawvm.uk_legislation.target_parser import (
    _parse_affected_target,
    _schedule_part_context_removed_target,
    _split_metadata_provisions,
)
from lawvm.uk_legislation.table_sources import (
    _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID,
    _UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
    _uk_table_driven_corresponding_entry_word_substitution,
    _uk_table_driven_repeal_table_quoted_words_text_repeal,
    _uk_table_driven_repeal_table_structural_repeal,
)
from lawvm.uk_legislation.text_matching import (
    _normalize_text,
    _numeric_list_trailing_comma_replacement_text,
    _numeric_list_trailing_comma_subtree_replacement,
    _rotated_trailing_comma_omission_match,
    _text_match_has_word_punctuation_elision_candidate,
    _text_patch_pattern,
)
from lawvm.uk_legislation.xml_helpers import (
    _direct_structural_num,
    _tag,
    _text_content,
    get_all_eids,
)

# ---------------------------------------------------------------------------
# UK replay helpers
# ---------------------------------------------------------------------------


_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID = "uk_replay_table_entry_inline_text_insertion_unresolved"
_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID = "uk_replay_table_entry_inline_text_preimage_gap"
_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID = "uk_replay_table_entry_row_insert_unresolved"
_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID = "uk_replay_table_column_insert_unresolved"
_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID = "uk_effect_schedule_list_entry_table_rows_lowered"
_UK_ENACTED_SCHEDULE_TABLE_ROW_PART_TARGET_RULE_ID = (
    "uk_effect_enacted_schedule_table_row_part_target_refined"
)
_UK_NUMBERED_SCHEDULE_ENTRY_REPEAL_TARGET_REFINED_RULE_ID = (
    "uk_effect_numbered_schedule_entry_repeal_target_refined"
)
_UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID = (
    "uk_effect_substituted_for_label_changing_target_rebound"
)
_UK_SOURCE_TEXT_SCHEDULE_PARAGRAPH_TARGET_OVERRIDE_RULE_ID = (
    "uk_effect_source_text_schedule_paragraph_target_overrides_metadata"
)
_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID = (
    "uk_replay_source_label_changing_substitution_resolved"
)
_UK_REPLAY_SCHEDULE_ITEM_TARGET_FROM_PARENT_SUBSTITUTION_RULE_ID = (
    "uk_replay_schedule_item_target_from_parent_substitution_resolved"
)
_UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID = (
    "uk_replay_source_carried_labeled_child_text_substitution_recovered"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_replace_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_replace_resolved"
)
_UK_REPLAY_SCHEDULE_P1GROUP_PARAGRAPH_WRAPPER_RESOLVED_RULE_ID = (
    "uk_replay_schedule_p1group_paragraph_wrapper_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_prefix_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_article_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PARENTHETICAL_PARAGRAPH_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_GROUP_ANCHOR_RULE_ID = (
    "uk_replay_schedule_list_entry_group_anchor_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ALPHABETICAL_POSITION_RULE_ID = (
    "uk_replay_schedule_list_entry_alphabetical_position_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_table_rows_insert_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_table_rows_insert_unresolved"
)
_UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_RESOLVED_RULE_ID = (
    "uk_replay_schedule_table_end_rows_insert_resolved"
)
_UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_table_end_rows_insert_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ANCHOR_CITATION_SHORT_TITLE_RULE_ID = (
    "uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_PARENTHETICAL_PARAGRAPH_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_NUMBERED_ANCHOR_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized"
)
_UK_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID = (
    "uk_effect_respectively_all_occurrences_substitution_text_patch"
)
_UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID = "uk_effect_range_to_end_there_is_substituted_text_patch"
def compile_effect_to_ir_ops(
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    sequence: int = 0,
    fallback_for_missing_extracted_source: bool = False,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
    allow_payload_identity_synthesis: bool = True,
    source_root: Optional[ET.Element] = None,
    source_authority_layer: str = "",
) -> list[LegalOperation]:
    """Compile a UKEffectRecord + XML element into LawVM LegalOperations.

    Word-level effects ("words substituted", "words repealed", "words omitted",
    "words inserted") compile to text_replace / text_repeal actions with a
    typed ``text_patch`` as the authoritative text-level payload. Legacy
    ``text_match`` / ``text_replacement`` are compatibility only when they
    still appear at older boundaries. Structural effects ("substituted",
    "repealed", "inserted") compile to replace / repeal / insert as before.

    Effects with an empty effect_type (typically from XML metadata) are inferred
    from the provision text when possible; if no verb can be found they are skipped
    rather than guessing a structural action.
    """
    # Determine whether this is a word-level (intra-node text) effect.
    effect_type = (effect.effect_type or "").strip().lower()
    metadata_renumber_targets = _uk_metadata_renumber_targets(effect)

    # Commencement rows affect in-force status, not structural text/state.
    if effect_type in _COMMENCEMENT_EFFECT_TYPES:
        return []

    is_word_level = _is_uk_word_level_effect_type(effect_type)

    # Word-level effects start as "replace" but may be promoted to
    # text_replace / text_repeal after fragment extraction.
    action = _uk_effect_type_action(
        effect_type,
        has_metadata_renumber_targets=metadata_renumber_targets is not None,
    )
    extracted_text = _text_content(extracted_el) if extracted_el is not None else None
    metadata_renumber_targets = _uk_source_text_corrected_renumber_targets(
        metadata_renumber_targets,
        extracted_text,
    )
    source_parent_substitution_range_payload: Optional[dict[str, Any]] = None
    source_parent_at_end_added_payload: Optional[dict[str, Any]] = None

    # Infer missing action from text heuristics if metadata is empty.
    # For empty effect_type we require a clear structural verb — if none is found
    # we skip (return []) rather than guessing "modified" or a structural replace.
    if not action and extracted_el is not None:
        text_lower = (extracted_text or "").lower()
        if _empty_effect_type_commencement_source(extracted_text or ""):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_commencement_source_rejected",
                family="applicability_scope",
                reason_code="commencement_source_out_of_scope",
                reason=(
                    "UK effect has no explicit text/tree action and the source "
                    "is a commencement instrument; structural replay must not "
                    "synthesize a mutation from in-force language."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"effect_type_normalized": effect_type},
            )
            return []
        application_modification_context = _source_parent_application_modification_context(
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if application_modification_context:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_application_modification_payload_rejected",
                family="applicability_scope",
                reason_code="application_modification_payload_out_of_scope",
                reason=(
                    "UK effect has no explicit effect type and the extracted "
                    "BlockAmendment payload is governed by a parent "
                    "application-modification formula; structural replay must "
                    "not treat it as an unconditional current-text amendment."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "parent_context_preview": _preview_source_text(
                        application_modification_context,
                        limit=240,
                    ),
                },
            )
            return []
        if _empty_effect_type_as_if_words_omitted(extracted_text or ""):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_empty_type_as_if_words_omitted_rejected",
                family="temporal_recovery",
                reason_code="empty_effect_type_temporary_as_if_word_omission",
                reason=(
                    "UK effect has no explicit effect type and the source uses "
                    "temporary 'shall have effect as if words were omitted' "
                    "language; lowering must not infer a structural repeal of "
                    "the broad affected provision."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"affected_provisions": effect.affected_provisions},
            )
            return []
        if "repeal" in text_lower or "omit" in text_lower:
            action = "repeal"
        elif "substitute" in text_lower or "replace" in text_lower:
            action = "replace"
        elif "insert" in text_lower:
            action = "insert"
        elif re.search(r"\bfrom\b.*\bto\b", text_lower, re.I | re.S):
            action = "replace"
        else:
            source_parent_substitution_range_payload = _source_parent_substitution_range_payload(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
                target_refs=_split_metadata_provisions(effect.affected_provisions),
            )
            if source_parent_substitution_range_payload is not None:
                action = "replace"
            else:
                source_parent_at_end_added_payload = _source_parent_at_end_added_payload(
                    extracted_el=extracted_el,
                    source_root=source_root,
                    extracted_text=extracted_text,
                    target_refs=_split_metadata_provisions(effect.affected_provisions),
                )
                if source_parent_at_end_added_payload is not None:
                    action = "insert"
        # No else — leave action=None so we fall through to the early return below.

    if not action:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_lowering_no_supported_action_rejected",
            family="unsupported_or_unresolved_action",
            reason_code="no_supported_action",
            reason=(
                "UK effect lowered to no replay operations because no supported "
                "action could be inferred"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"effect_type_normalized": effect_type},
        )
        return []

    use_metadata_fallback = (
        fallback_for_missing_extracted_source
        and extracted_el is None
        and action == "insert"
        and effect_type not in {"added", "entry inserted"}
    )
    extraction_witness = _uk_extraction_witness(
        effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        metadata_fallback_used=use_metadata_fallback,
        source_authority_layer=source_authority_layer,
    )
    effect_witness = _uk_effect_witness(
        effect,
        authority_layer=extraction_witness.authority_layer,
    )

    if action == "renumber" and metadata_renumber_targets is not None:
        source_target = metadata_renumber_targets.source_target
        destination = metadata_renumber_targets.destination
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=metadata_renumber_targets.rule_id,
            family="lineage_normalization",
            reason_code=metadata_renumber_targets.reason_code,
            reason=metadata_renumber_targets.reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "source_target": str(source_target),
                "destination": str(destination),
                "metadata_destination": (
                    str(metadata_renumber_targets.metadata_destination)
                    if metadata_renumber_targets.metadata_destination is not None
                    else ""
                ),
                "affected_provisions": effect.affected_provisions,
            },
        )
        src = OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        )
        target_expansion_witness = _uk_target_expansion_witness(
            effect.affected_provisions,
            [effect.affected_provisions],
            original_targets_str=[effect.affected_provisions],
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=effect.effect_id,
            sequence=sequence,
            action=StructuralAction.RENUMBER,
            target=source_target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=None,
            insertion_anchor_witness=None,
        )
        return [
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=StructuralAction.RENUMBER,
                target=source_target,
                destination=destination,
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                witness_rule_id=metadata_renumber_targets.rule_id,
            )
        ]

    after_paragraph_series = _source_after_paragraph_insert_labelled_series(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_series is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
            family="source_context_elaboration",
            reason_code="after_paragraph_insert_semicolon_and_labelled_series",
            reason=(
                "UK source row inserts a semicolon after an existing paragraph "
                "and then a contiguous labelled paragraph series; lowering "
                "separates the punctuation patch from the inserted legal "
                "siblings instead of treating the instruction text as one "
                "payload."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                key: value
                for key, value in after_paragraph_series.items()
                if key != "rule_id"
            },
        )
        src = OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        )
        semicolon_target = LegalAddress(
            path=(
                ("section", str(after_paragraph_series["section"])),
                ("subsection", str(after_paragraph_series["subsection"])),
                ("paragraph", str(after_paragraph_series["anchor_label"])),
            )
        )
        semicolon_patch = TextPatchSpec(
            kind=TextPatchKindEnum.APPEND,
            selector=TextSelector(match_text="TEXT_END", occurrence=0),
            replacement=";",
        )
        semicolon_rewrite = _uk_text_rewrite_spec(
            fragment_subs=[
                {
                    "original": "TEXT_END",
                    "replacement": ";",
                    "rule_id": _UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
                }
            ],
            text_patch=semicolon_patch,
            op_text_match="TEXT_END",
            op_text_replacement=";",
            op_text_occurrence=0,
        )
        semicolon_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_semicolon",
            sequence=sequence,
            action=StructuralAction.TEXT_REPLACE,
            target=semicolon_target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=_uk_target_expansion_witness(
                effect.affected_provisions,
                [str(after_paragraph_series["semicolon_target"])],
                original_targets_str=[effect.affected_provisions],
            ),
            text_rewrite_witness=semicolon_rewrite,
            insertion_anchor_witness=None,
        )
        custom_ops = [
            LegalOperation(
                op_id=semicolon_witness.op_id,
                sequence=semicolon_witness.sequence,
                action=semicolon_witness.action,
                target=semicolon_target,
                payload=None,
                source=src,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(semicolon_witness),
                text_patch=semicolon_patch,
                witness_rule_id=_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
            )
        ]
        preceding_target = semicolon_target
        for payload_index, payload in enumerate(after_paragraph_series["payloads"]):
            payload_target = _parse_affected_target(str(payload["target_ref"]))
            payload_node = IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=str(payload["label"]),
                text=str(payload["text"]),
            )
            insert_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_insert_{payload_index}",
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=payload_target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=_uk_target_expansion_witness(
                    effect.affected_provisions,
                    [str(payload["target_ref"])],
                    original_targets_str=[effect.affected_provisions],
                ),
                text_rewrite_witness=None,
                insertion_anchor_witness=_uk_insertion_anchor_witness(
                    _target_anchor_eid(preceding_target),
                    anchor_source=_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
                ),
            )
            custom_ops.append(
                LegalOperation(
                    op_id=insert_witness.op_id,
                    sequence=insert_witness.sequence,
                    action=insert_witness.action,
                    target=payload_target,
                    payload=_payload_with_rewrite_witness(payload_node, insert_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(insert_witness),
                    witness_rule_id=_UK_AFTER_PARAGRAPH_INSERT_LABELLED_SERIES_RULE_ID,
                )
            )
            preceding_target = payload_target
        return custom_ops

    # ALWAYS split metadata provisions to handle ranges and lists
    raw_affected_provisions = effect.affected_provisions
    targets_str = _split_metadata_provisions(effect.affected_provisions)
    original_targets_str = list(targets_str)
    heading_facet_range_targets = _expand_heading_facet_section_range_ref(raw_affected_provisions)
    if heading_facet_range_targets and targets_str == heading_facet_range_targets:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_heading_facet_range_expanded",
            family="target_shape_normalization",
            reason_code="explicit_section_heading_facet_range_expanded",
            reason=(
                "UK effect metadata names an explicit range of section titles/headings; "
                "lowering expands that range into one typed heading facet target per section."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "original_target_ref": raw_affected_provisions,
                "expanded_targets": list(heading_facet_range_targets),
            },
        )
    mixed_heading_source_ref_by_target: dict[str, str] = {}
    trailing_repeal_refs: list[str] = []
    replacement_leaf_override: Optional[str] = None
    replacement_leaf_kind: Optional[str] = None
    label_changing_substitutions: tuple[UKSourceLabelChangingSubstitution, ...] = ()
    if action == "replace":
        # Keep the replacement target labels authoritative. The older anchor-
        # retarget heuristic rewrites live replacement labels back to the
        # legacy anchor series, which is exactly the kind of compatibility
        # slop we do not want to keep around.
        label_changing_substitutions = _source_label_changing_substitution_series(
            effect.effect_type,
            original_targets_str,
        )
        if label_changing_substitutions:
            targets_str = [substitution.source_ref for substitution in label_changing_substitutions]
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
                family="lineage_normalization",
                reason_code="substituted_for_old_sibling_with_new_payload_label",
                reason=(
                    "UK source says a labelled sibling is substituted for an "
                    "existing sibling, while effect metadata names the new "
                    "payload label; lowering keeps the executable replace "
                    "target on the source-named old sibling and preserves the "
                    "new payload label as the replacement identity."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "substitutions": [
                        {
                            "source_ref": substitution.source_ref,
                            "source_target": str(substitution.source_target),
                            "replacement_ref": substitution.replacement_ref,
                            "replacement_target": str(substitution.replacement_target),
                        }
                        for substitution in label_changing_substitutions
                    ],
                },
            )
        if source_parent_substitution_range_payload is not None:
            trailing_repeal_refs = list(source_parent_substitution_range_payload["trailing_refs"])
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
                family="source_context_elaboration",
                reason_code="payload_fragment_combined_with_parent_substitution_range",
                reason=(
                    "UK effect feed row has no effect type and the extracted "
                    "BlockAmendment contains only the replacement payload, but "
                    "the source-local parent instruction explicitly substitutes "
                    "a bounded sibling range; lowering combines those facts into "
                    "one source-owned replacement plus explicit trailing repeals."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    key: value
                    for key, value in source_parent_substitution_range_payload.items()
                    if key != "rule_id"
                },
            )
        else:
            trailing_repeal_refs = _repeal_tail_for_substituted_series_replacement(
                effect.effect_type,
                original_targets_str,
            )
        if (
            trailing_repeal_refs
            and original_targets_str
            and not label_changing_substitutions
            and source_parent_substitution_range_payload is None
        ):
            try:
                replacement_target = _parse_affected_target(original_targets_str[0])
            except Exception:
                replacement_target = None
            if replacement_target is not None:
                replacement_leaf_override = _addr_leaf_label(replacement_target)
                replacement_leaf_kind = _addr_leaf_kind(replacement_target)
    if source_parent_at_end_added_payload is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
            family="source_context_elaboration",
            reason_code="payload_fragment_combined_with_parent_at_end_added",
            reason=(
                "UK effect feed row has no effect type and the extracted "
                "BlockAmendment contains only an inserted structural payload, "
                "but the source-local parent instruction explicitly adds it at "
                "the end of the affected provision; lowering keeps the metadata "
                "target and payload identity as one source-owned insert."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                key: value
                for key, value in source_parent_at_end_added_payload.items()
                if key != "rule_id"
            },
        )
    if len(targets_str) == 1:
        mixed_heading_structural_ref = _mixed_heading_structural_insert_ref(
            targets_str[0],
            action=action,
        )
        expansion_source_el = extracted_el
        expansion_ref = targets_str[0]
        if mixed_heading_structural_ref:
            expansion_ref = mixed_heading_structural_ref
            amendment_container = _first_amendment_container(extracted_el)
            expansion_source_el = amendment_container if amendment_container is not None else extracted_el
        else:
            amendment_container = _first_amendment_container(extracted_el)
            if amendment_container is not None:
                expansion_source_el = amendment_container
        expanded_targets = _expand_sibling_targets_from_extracted(expansion_ref, expansion_source_el)
        if not expanded_targets:
            expanded_targets = _expand_sibling_targets_from_text(expansion_ref, extracted_text)
        if expanded_targets:
            targets_str = expanded_targets
            if mixed_heading_structural_ref:
                mixed_heading_source_ref_by_target = {
                    target_ref: original_targets_str[0] for target_ref in expanded_targets
                }
            else:
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id="uk_effect_source_payload_sibling_range_expanded",
                    family="target_shape_normalization",
                    reason_code="source_payload_children_expand_compressed_sibling_range",
                    reason=(
                        "UK effect metadata compressed a sibling target range, "
                        "while the extracted BlockAmendment contains one direct "
                        "payload child for each sibling; lowering expands the "
                        "targets to those source-owned children."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "original_target_ref": original_targets_str[0],
                        "expanded_targets": list(expanded_targets),
                        "source_container": _tag(expansion_source_el) if expansion_source_el is not None else "",
                    },
                )
        elif mixed_heading_structural_ref and len(re.findall(r"\([0-9A-Z]+\)", mixed_heading_structural_ref, re.I)) == 1:
            targets_str = [mixed_heading_structural_ref]
            mixed_heading_source_ref_by_target = {
                mixed_heading_structural_ref: original_targets_str[0],
            }
        if mixed_heading_structural_ref and mixed_heading_source_ref_by_target:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_mixed_heading_structural_insert_target_normalized",
                family="target_shape_normalization",
                reason_code="mixed_heading_structural_insert_target_split",
                reason=(
                    "UK effect target combines inserted structural provisions "
                    "with a heading facet; lowering removes the heading suffix "
                    "only for source-owned structural insert targets and keeps "
                    "the heading facet unresolved."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_target_ref": original_targets_str[0],
                    "structural_targets": list(targets_str),
                    "heading_facet_status": "unresolved",
                },
            )
    if effect_type == "added" and action == "insert" and extracted_el is not None:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_added_type_source_structuralized",
            family="effect_feed_normalization",
            reason_code="nonstructural_added_type_has_source_structural_insert",
            reason=(
                "UK effect feed classified the row as 'added', but the exact "
                "affecting source provision resolves and contains a source-owned "
                "insert payload for the affected target; lowering admits the row "
                "as a structural insert without treating all 'added' rows as "
                "structural by metadata alone."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_refs": list(targets_str),
                "source_container": _tag(_first_amendment_container(extracted_el))
                if _first_amendment_container(extracted_el) is not None
                else _tag(extracted_el),
            },
        )
    if not targets_str:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_lowering_no_targets_rejected",
            family="target_resolution_recovery",
            reason_code="no_affected_targets",
            reason=(
                "UK effect lowered to no replay operations because affected "
                "provisions produced no target candidates"
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={"original_affected_provisions": effect.affected_provisions},
        )
        return []

    ops = []
    unlowered_overlap_substitution_targets: list[str] = []
    unlowered_overlap_substitution_reason = ""
    chained_insert_preceding_eid: Optional[str] = None
    chained_insert_preceding_eid_source = "effect_comments_after_clause"
    if action == "insert":
        crossheading_payload = _extract_crossheading_payload_from_extracted(
            effect.affected_provisions,
            extracted_el,
        )
        if crossheading_payload is not None:
            crossheading_target = canonicalize_uk_address(LegalAddress(path=(("crossheading", ""),)))
            crossheading_target_witness = _uk_target_expansion_witness(
                "cross-heading",
                ["cross-heading"],
            )
            crossheading_lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_crossheading",
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=crossheading_target,
                payload=crossheading_payload,
                source=OperationSource(
                    statute_id=effect.affecting_act_id,
                    title=effect.affecting_title,
                    effective=effect_witness.applicability.effective_date or "",
                    raw_text=extraction_witness.extracted_text,
                ),
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=crossheading_target_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=crossheading_lowered_witness.op_id,
                    sequence=sequence,
                    action=StructuralAction.INSERT,
                    target=crossheading_target,
                    payload=_payload_with_rewrite_witness(crossheading_payload, crossheading_lowered_witness),
                    source=crossheading_lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(crossheading_lowered_witness),
                )
            )
    source_replaced_sibling_count = (
        _source_replaced_sibling_count_from_substitution_text(
            extracted_text=extracted_text,
            target_refs=targets_str,
        )
        if action == "replace"
        else None
    )
    for target_index, t_str in enumerate(targets_str):
        heading_facet_target = _is_heading_only_ref(t_str)
        if _is_schedule_note_ref(t_str):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_schedule_note_target_rejected",
                family="unsupported_target_facet",
                reason_code="schedule_note_target_unsupported",
                reason=(
                    "UK effect target names a schedule note; lowering must "
                    "not coerce that note into paragraph/subparagraph structure."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target_candidate_count": len(targets_str)},
            )
            continue
        if heading_facet_target and not _is_heading_facet_word_patch_supported(effect.effect_type, extracted_text):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_heading_only_ref_rejected",
                family="unsupported_target_facet",
                reason_code="heading_only_ref_unsupported",
                reason=(
                    "UK effect target names only a heading or sidenote facet; "
                    "lowering cannot safely mutate the host provision body"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target_candidate_count": len(targets_str)},
            )
            continue
        parsed_target = _parse_affected_target(t_str)
        target = parsed_target if _is_direct_section_paragraph_ref(t_str) else canonicalize_uk_address(parsed_target)
        source_schedule_table_row_part_label = (
            str(extracted_el.get("source_part_label") or "")
            if extracted_el is not None
            and str(extracted_el.get("source_rule_id") or "")
            == "uk_affecting_act_enacted_schedule_table_row_source_extracted"
            else ""
        )
        if (
            action == "insert"
            and source_schedule_table_row_part_label
            and _addr_container(target) == "schedule"
            and _addr_field(target, "part") is None
            and _addr_leaf_kind(target) == "paragraph"
        ):
            original_target = target
            schedule_label = _addr_field(target, "schedule") or ""
            paragraph_label = _addr_leaf_label(target) or ""
            target = canonicalize_uk_address(
                LegalAddress(
                    path=(
                        ("schedule", schedule_label),
                        ("part", source_schedule_table_row_part_label),
                        ("paragraph", paragraph_label),
                    )
                )
            )
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_ENACTED_SCHEDULE_TABLE_ROW_PART_TARGET_RULE_ID,
                family="target_resolution_recovery",
                reason_code="source_enacted_schedule_table_row_part_context",
                reason=(
                    "UK enacted affecting source exposed the added schedule "
                    "paragraph as a unique row under a schedule Part; lowering "
                    "refines the metadata paragraph target to that source-owned "
                    "Part instead of inserting under the schedule root."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "metadata_target": str(original_target),
                    "refined_target": str(target),
                    "source_part_label": source_schedule_table_row_part_label,
                    "source_rule_id": str(extracted_el.get("source_rule_id") or ""),
                    "source_row_text": str(extracted_el.get("source_row_text") or ""),
                },
            )
        label_changing_substitution = next(
            (
                substitution
                for substitution in label_changing_substitutions
                if tuple(target.path) == tuple(substitution.source_target.path)
            ),
            None,
        )
        target_replacement_leaf_override = replacement_leaf_override
        target_replacement_leaf_kind = replacement_leaf_kind
        if label_changing_substitution is not None:
            target_replacement_leaf_override = _addr_leaf_label(label_changing_substitution.replacement_target)
            target_replacement_leaf_kind = _addr_leaf_kind(label_changing_substitution.replacement_target)
        source_text_target_override = (
            _source_text_schedule_paragraph_target_override(
                extracted_text=extracted_text,
                target=target,
            )
            if is_word_level and action == "replace"
            else None
        )
        if source_text_target_override is not None:
            original_target = target
            target = canonicalize_uk_address(source_text_target_override)
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SOURCE_TEXT_SCHEDULE_PARAGRAPH_TARGET_OVERRIDE_RULE_ID,
                family="target_resolution_recovery",
                reason_code="explicit_source_schedule_paragraph_overrides_metadata",
                reason=(
                    "UK source text explicitly names a different paragraph in "
                    "the same schedule than the effect metadata; lowering uses "
                    "the source-named target and records the metadata target as "
                    "overridden evidence."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "metadata_target": str(original_target),
                    "source_target": str(target),
                },
        )
        flat_p1para_schedule_insert_lowered = False
        flat_p1para_payload_detail: dict[str, Any] = {}
        if action == "insert":
            flat_p1para_probe = _flat_p1para_schedule_paragraph_insert_payload(
                extracted_el,
                target,
                fallback_target_eid=_fallback_target_eid,
            )
            if flat_p1para_probe is not None:
                if _addr_field(target, "part") is not None:
                    stripped_target = _schedule_part_context_removed_target(target)
                    if stripped_target is not None:
                        original_target = target
                        target = canonicalize_uk_address(stripped_target)
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=_UK_NONADDRESSABLE_SCHEDULE_PART_INSERT_TARGET_RULE_ID,
                            family="target_resolution_recovery",
                            reason_code="flat_insert_payload_uses_nonaddressable_schedule_part_context",
                            reason=(
                                "UK source names a schedule Part as insertion context, "
                                "but the source-owned BlockAmendment payload is a direct "
                                "labelled schedule paragraph with no Part wrapper; lowering "
                                "records the Part as context and targets the replay-addressable "
                                "schedule paragraph."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "metadata_target": str(original_target),
                                "normalized_target": str(target),
                                "removed_part_label": _addr_field(original_target, "part") or "",
                            },
                        )
        payload_match_target = target
        if label_changing_substitution is not None:
            payload_match_target = label_changing_substitution.replacement_target
        elif source_parent_substitution_range_payload is not None and target_index == 0:
            payload_match_target = LegalAddress(
                path=(
                    *target.path[:-1],
                    ("item", str(source_parent_substitution_range_payload["payload_label"])),
                )
            )
        crossheading_replacement_text = (
            _crossheading_before_anchor_replacement_text(extracted_text)
            if action == "replace" and _is_crossheading_ref(t_str)
            else None
        )
        crossheading_text_patch_fragment = (
            _crossheading_before_anchor_text_patch_fragment(extracted_text)
            if action == "replace" and _is_crossheading_ref(t_str)
            else None
        )
        crossheading_compound_heading_text = (
            _crossheading_and_structural_replacement_heading_text(
                affected_ref=t_str,
                extracted_el=extracted_el,
                target=target,
            )
            if action == "replace" and _is_crossheading_ref(t_str)
            else None
        )
        crossheading_group_repeal_selector = (
            _crossheading_and_structural_repeal_selector(
                affected_ref=t_str,
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
                target=target,
            )
            if action in {"replace", "repeal"} and _is_crossheading_ref(t_str)
            else None
        )
        if (
            action == "replace"
            and _is_crossheading_ref(t_str)
            and crossheading_replacement_text is None
            and crossheading_text_patch_fragment is None
            and crossheading_compound_heading_text is None
            and crossheading_group_repeal_selector is None
        ):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_crossheading_replace_rejected",
                family="unsupported_target_facet",
                reason_code="crossheading_replace_unsupported",
                reason=(
                    "UK cross-heading replacement target lacks an explicit "
                    "heading-before-anchor replacement shape"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str},
            )
            continue
        if extracted_el is None and effect_type in {"entry inserted", "entry repealed", "entry omitted"}:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_schedule_entry_missing_source_rejected",
                family="source_schedule_list_entry_elaboration",
                reason_code="entry_effect_requires_source_text",
                reason=(
                    "UK schedule-entry effect row requires affecting source text; "
                    "metadata alone does not identify the entry payload or entry "
                    "anchor safely enough for replay."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target": str(target), "action": action},
            )
            continue
        if _is_direct_section_paragraph_ref(t_str):
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_direct_section_paragraph_target_normalized",
                family="target_shape_normalization",
                reason_code="explicit_section_paragraph_ref",
                reason=(
                    "UK affected-provision reference uses section-number plus "
                    "an alphabetic bracket, which denotes a direct section "
                    "paragraph rather than an alphabetic subsection."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target": str(target)},
            )
        if _is_schedule_part_abbreviation_ref(t_str) and any(kind == "part" for kind, _label in target.path):
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_schedule_part_abbreviation_target_normalized",
                family="target_shape_normalization",
                reason_code="explicit_schedule_part_abbreviation_ref",
                reason=(
                    "UK affected-provision reference uses a schedule Part abbreviation; "
                    "lowering preserves it as an explicit schedule part target rather "
                    "than treating the abbreviation as a paragraph label."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target": str(target)},
            )
        if action == "repeal":
            refined_numbered_entry_target = _uk_numbered_schedule_entry_repeal_target(
                target=target,
                extracted_text=extracted_text,
            )
            if refined_numbered_entry_target is not None:
                original_target = target
                target = refined_numbered_entry_target
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id=_UK_NUMBERED_SCHEDULE_ENTRY_REPEAL_TARGET_REFINED_RULE_ID,
                    family="source_schedule_list_entry_elaboration",
                    reason_code="explicit_numbered_entry_child",
                    reason=(
                        "UK source text claims omission/repeal of a numbered "
                        "entry under a schedule partition; lowering refines "
                        "the partition carrier target to the explicit numbered "
                        "paragraph instead of deleting the carrier."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "original_target": str(original_target),
                        "refined_target": str(target),
                    },
                )
        if crossheading_compound_heading_text is not None:
            heading_target = LegalAddress(path=target.path, special=FacetKind.HEADING)
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
                family="target_facet_lowering",
                reason_code="explicit_crossheading_and_structural_replacement_split",
                reason=(
                    "UK source replaces a provision and its cross-heading from a "
                    "single titled payload; lowering emits a separate heading "
                    "facet patch and leaves the structural payload on the named "
                    "provision target."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "structural_target": str(target),
                    "heading_target": str(heading_target),
                    "replacement_text_preview": crossheading_compound_heading_text[:200],
                },
            )
            fragment_subs_for_heading = [
                {
                    "original": "TEXT_ALL",
                    "replacement": crossheading_compound_heading_text,
                    "rule_id": _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
                }
            ]
            heading_text_patch = TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_ALL", occurrence=0),
                replacement=crossheading_compound_heading_text,
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            text_rewrite_witness = _uk_text_rewrite_spec(
                fragment_subs=fragment_subs_for_heading,
                text_patch=heading_text_patch,
                op_text_match="TEXT_ALL",
                op_text_replacement=crossheading_compound_heading_text,
                op_text_occurrence=0,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_crossheading",
                sequence=sequence,
                action=StructuralAction.TEXT_REPLACE,
                target=heading_target,
                payload=None,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=text_rewrite_witness,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=lowered_witness.action,
                    target=lowered_witness.target,
                    payload=None,
                    source=lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                    text_patch=heading_text_patch,
                    witness_rule_id=_CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
                )
            )
        schedule_table_end_rows_selector = (
            _uk_schedule_table_end_rows_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
            )
            if action == "insert" and not heading_facet_target
            else None
        )
        if schedule_table_end_rows_selector is not None:
            table_payload_node = _uk_schedule_list_entry_table_payload(extracted_el)
            if table_payload_node is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
                    family="source_table_elaboration",
                    reason_code="explicit_schedule_end_insert_without_table_payload",
                    reason=(
                        "UK source text explicitly inserts at the end of a "
                        "schedule, but no single BlockAmendment table payload "
                        "was available; lowering blocks instead of inventing "
                        "flattened text or schedule entries."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail=dict(schedule_table_end_rows_selector),
                )
                continue
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
                family="source_table_elaboration",
                reason_code="explicit_schedule_end_insert_table_payload",
                reason=(
                    "UK source text explicitly inserts source-owned tabular "
                    "rows at the end of a schedule table; lowering preserves "
                    "the BlockAmendment table rows and replay must resolve a "
                    "unique table-backed schedule carrier."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=dict(schedule_table_end_rows_selector),
            )
            payload_node = dc_replace(
                table_payload_node,
                attrs={
                    **dict(table_payload_node.attrs or {}),
                    "source_rule_id": "uk_schedule_table_end_rows_payload",
                    "anchor_direction": "end",
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.INSERT,
                    target=target,
                    payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        (
                            f"{_NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR}"
                            f"{json.dumps(schedule_table_end_rows_selector, ensure_ascii=False)}"
                        ),
                    ),
                    witness_rule_id=_UK_SCHEDULE_TABLE_END_ROWS_RULE_ID,
                )
            )
            continue
        schedule_list_entry_selector = (
            _uk_schedule_list_entry_insert_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
            )
            if action == "insert" and not heading_facet_target
            else None
        )
        source_parent_schedule_entry_insert = (
            _source_parent_instruction_with_payload(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
                instruction_pattern=_SOURCE_PARENT_SCHEDULE_ENTRY_INSERT_RE,
            )
            if schedule_list_entry_selector is None and action == "insert" and not heading_facet_target
            else None
        )
        if source_parent_schedule_entry_insert is not None:
            schedule_list_entry_selector = _uk_schedule_list_entry_insert_selector(
                target_ref=t_str,
                target=target,
                extracted_text=source_parent_schedule_entry_insert["combined_text"],
            )
            if schedule_list_entry_selector is not None:
                schedule_list_entry_selector = {
                    **schedule_list_entry_selector,
                    "source_parent_id": source_parent_schedule_entry_insert["source_parent_id"],
                    "source_parent_instruction": source_parent_schedule_entry_insert[
                        "source_parent_instruction"
                    ],
                }
        if schedule_list_entry_selector is not None:
            table_payload_node = _uk_schedule_list_entry_table_payload(extracted_el)
            if table_payload_node is not None:
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id=_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID,
                    family="source_table_elaboration",
                    reason_code="explicit_schedule_entry_insert_table_payload",
                    reason=(
                        "UK schedule-list-entry insertion carried a tabular "
                        "source payload; lowering preserves source rows and "
                        "replay must resolve the entry anchor in the target "
                        "schedule table before inserting rows."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "selector_rule_id": str(schedule_list_entry_selector.get("rule_id") or ""),
                        **{
                            key: value
                            for key, value in schedule_list_entry_selector.items()
                            if key != "rule_id"
                        },
                    },
                )
                payload_node = dc_replace(
                    table_payload_node,
                    attrs={
                        **dict(table_payload_node.attrs or {}),
                        "source_rule_id": "uk_schedule_list_entry_table_rows_payload",
                        "anchor_text": str(schedule_list_entry_selector["anchor_text"]),
                        "anchor_direction": str(schedule_list_entry_selector["direction"]),
                    },
                )
                src = OperationSource(
                    statute_id=effect.affecting_act_id,
                    title=effect.affecting_title,
                    effective=effect_witness.applicability.effective_date or "",
                    raw_text=extraction_witness.extracted_text,
                )
                target_expansion_witness = _uk_target_expansion_witness(
                    t_str,
                    [t_str],
                    original_targets_str=original_targets_str,
                )
                lowered_witness = UKLoweredOperationWitness(
                    op_id=effect.effect_id,
                    sequence=sequence,
                    action=StructuralAction.INSERT,
                    target=target,
                    payload=payload_node,
                    source=src,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    target_expansion_witness=target_expansion_witness,
                    text_rewrite_witness=None,
                    insertion_anchor_witness=None,
                )
                ops.append(
                    LegalOperation(
                        op_id=lowered_witness.op_id,
                        sequence=lowered_witness.sequence,
                        action=StructuralAction.INSERT,
                        target=target,
                        payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                        source=src,
                        group_id=_uk_temporal_group_id(effect),
                        provenance_tags=(
                            *_uk_lowered_op_provenance_tags(lowered_witness),
                            (
                                f"{_NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR}"
                                f"{json.dumps(schedule_list_entry_selector, ensure_ascii=False)}"
                            ),
                        ),
                        witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_TABLE_ROWS_RULE_ID,
                    )
                )
                continue
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
                family="source_schedule_list_entry_elaboration",
                reason_code="explicit_schedule_list_entry_anchor",
                reason=(
                    "UK schedule-list-entry insertion lowered as a typed "
                    "schedule-entry sibling insert; replay must resolve exactly "
                    "one anchor entry before mutating schedule children."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=dict(schedule_list_entry_selector),
            )
            payload_node = IRNode(
                kind=IRNodeKind.SCHEDULE_ENTRY,
                label=None,
                text=str(schedule_list_entry_selector["inserted_text"]),
                attrs={
                    "source_rule_id": "uk_schedule_list_entry_insert_payload",
                    "anchor_text": str(schedule_list_entry_selector["anchor_text"]),
                    "anchor_direction": str(schedule_list_entry_selector["direction"]),
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.INSERT,
                    target=target,
                    payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        f"{_NOTE_SCHEDULE_LIST_ENTRY_SELECTOR}{json.dumps(schedule_list_entry_selector, ensure_ascii=False)}",
                    ),
                    witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
                )
            )
            continue
        schedule_list_entry_repeal_selector = (
            _uk_schedule_list_entry_repeal_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
            )
            if action == "repeal"
            or effect_type in {"words omitted", "word omitted", "words repealed", "word repealed"}
            else None
        )
        if schedule_list_entry_repeal_selector is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
                family="source_schedule_list_entry_elaboration",
                reason_code="explicit_schedule_list_entry_repeal_anchor",
                reason=(
                    "UK schedule-list-entry repeal lowered as a typed "
                    "entry-level schedule mutation; replay must resolve every "
                    "claimed entry anchor before deleting any schedule child."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=dict(schedule_list_entry_repeal_selector),
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.REPEAL,
                target=target,
                payload=None,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.REPEAL,
                    target=target,
                    payload=None,
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        (
                            f"{_NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR}"
                            f"{json.dumps(schedule_list_entry_repeal_selector, ensure_ascii=False)}"
                        ),
                    ),
                    witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
                )
            )
            continue
        schedule_list_entry_replace_selector = (
            _uk_schedule_list_entry_replace_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
            )
            if action == "replace" or effect_type in {"words substituted", "word substituted"}
            else None
        )
        if schedule_list_entry_replace_selector is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
                family="source_schedule_list_entry_elaboration",
                reason_code="explicit_schedule_list_entry_replace_anchor",
                reason=(
                    "UK schedule-list-entry replacement lowered as a typed "
                    "entry-level schedule mutation; replay must resolve the "
                    "claimed entry anchor before replacing a schedule child."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=dict(schedule_list_entry_replace_selector),
            )
            payload_node = IRNode(
                kind=IRNodeKind.SCHEDULE_ENTRY,
                label=None,
                text=str(schedule_list_entry_replace_selector["replacement_text"]),
                attrs={
                    "source_rule_id": "uk_schedule_list_entry_replace_payload",
                    "anchor_text": str(schedule_list_entry_replace_selector["anchor"]),
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.REPLACE,
                target=target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.REPLACE,
                    target=target,
                    payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        (
                            f"{_NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR}"
                            f"{json.dumps(schedule_list_entry_replace_selector, ensure_ascii=False)}"
                        ),
                    ),
                    witness_rule_id=_UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
                )
            )
            continue
        table_column_insert_selector = (
            _uk_table_column_insert_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
                extracted_el=extracted_el,
            )
            if action == "insert"
            else None
        )
        if table_column_insert_selector is not None:
            table_marker_parent = _uk_parent_target_before_table_marker(target)
            parent_target = table_marker_parent
            if (
                parent_target is not None
                and len(parent_target.path) >= 2
                and parent_target.path[-1] == ("subsection", "1")
                and parent_target.path[-2][0] == "section"
            ):
                table_column_insert_selector = {
                    **table_column_insert_selector,
                    "allow_implicit_subsection_one_table": True,
                    "table_marker_parent_target": str(parent_target),
                }
                parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)
            source_column_payload = _uk_single_table_column_payload(extracted_el)
            if parent_target is None or source_column_payload is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
                    family="source_table_elaboration",
                    reason_code=(
                        "table_marker_parent_missing"
                        if parent_target is None
                        else "between_columns_without_single_column_payload"
                    ),
                    reason=(
                        "UK table-column insertion needs both a containing "
                        "table target and an exactly one-column BlockAmendment "
                        "table payload; lowering blocks instead of inventing "
                        "column cells from flattened text."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={"target_ref": t_str, "target": str(target), **table_column_insert_selector},
                )
                continue
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
                family="source_table_elaboration",
                reason_code="explicit_between_columns_table_column_insert_selector",
                reason=(
                    "UK table-column insertion lowered as a typed column "
                    "insert; replay must prove the visual column boundary, "
                    "row alignment, and span adjustments before mutating the table."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "original_target": str(target),
                    "containing_target": str(parent_target),
                    **table_column_insert_selector,
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=parent_target,
                payload=source_column_payload,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.INSERT,
                    target=parent_target,
                    payload=_payload_with_rewrite_witness(source_column_payload, lowered_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        (
                            f"{_NOTE_TABLE_COLUMN_INSERT_SELECTOR}"
                            f"{json.dumps(table_column_insert_selector, ensure_ascii=False)}"
                        ),
                    ),
                    witness_rule_id=_UK_TABLE_COLUMN_INSERT_RULE_ID,
                )
            )
            continue
        table_row_insert_selector = (
            _uk_table_entry_row_insert_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
                extracted_el=extracted_el,
                source_root=source_root,
            )
            if action == "insert"
            else None
        )
        if table_row_insert_selector is not None:
            table_marker_parent = _uk_parent_target_before_table_marker(target)
            parent_target = table_marker_parent
            if (
                parent_target is None
                and table_row_insert_selector.get("source_names_table")
                and _addr_leaf_kind(target)
                in {"section", "subsection", "paragraph", "schedule", "part", "chapter"}
            ):
                parent_target = target
            if (
                parent_target is None
                and str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
                and _addr_leaf_kind(target) == "subsection"
            ):
                parent_target = target
            if (
                parent_target is not None
                and len(parent_target.path) >= 2
                and parent_target.path[-1] == ("subsection", "1")
                and parent_target.path[-2][0] == "section"
            ):
                table_row_insert_selector = {
                    **table_row_insert_selector,
                    "allow_implicit_subsection_one_table": True,
                    "table_marker_parent_target": str(parent_target),
                }
                parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)
            if parent_target is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id="uk_effect_table_entry_row_insert_target_unresolved",
                    family="source_table_elaboration",
                    reason_code="table_marker_parent_missing",
                    reason=(
                        "UK table-row insertion source names a table entry, "
                        "but the affected target could not be reduced to a "
                        "containing provision for table-row replay."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={"target_ref": t_str, "target": str(target), **table_row_insert_selector},
                )
                continue
            entry_label_table_rows = (
                str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                and str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
            )
            logical_entry_group_payload = (
                str(table_row_insert_selector.get("source_payload_mode") or "")
                == "logical_table_entry_group"
            )
            needs_single_source_row_payload = (
                str(table_row_insert_selector.get("source_payload_mode") or "") == "single_table_row"
                or (
                    str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                    and not entry_label_table_rows
                )
            )
            source_row_payload = (
                _uk_single_table_row_payload(extracted_el)
                if needs_single_source_row_payload
                else None
            )
            source_table_payload = (
                _uk_schedule_list_entry_table_payload(extracted_el)
                if str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
                else None
            )
            source_logical_entry_group_payload = (
                _uk_single_logical_table_entry_group_payload(extracted_el)
                if logical_entry_group_payload
                else None
            )
            if needs_single_source_row_payload and source_row_payload is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                    family="source_table_elaboration",
                    reason_code=(
                        "explicit_table_entry_label_insert_without_single_row_payload"
                        if str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                        else "deictic_table_entry_insert_without_single_row_payload"
                    ),
                    reason=(
                        "UK table-row insertion resolves a table-entry anchor, but "
                        "the source does not carry exactly one BlockAmendment "
                        "table row payload; lowering blocks instead of "
                        "inventing a row from flattened text."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "original_target": str(target),
                        "containing_target": str(parent_target),
                        "entry_shape": (
                            "deictic_table_entry"
                            if str(table_row_insert_selector.get("source_payload_mode") or "")
                            == "single_table_row"
                            else "numbered_entry"
                        ),
                        **table_row_insert_selector,
                    },
                )
                continue
            if logical_entry_group_payload and source_logical_entry_group_payload is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                    family="source_table_elaboration",
                    reason_code="deictic_table_entry_insert_without_single_logical_entry_payload",
                    reason=(
                        "UK table-row insertion resolves a deictic table-entry "
                        "anchor, but the source table payload is not exactly one "
                        "logical entry group owned by a rowspanning first cell."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "original_target": str(target),
                        "containing_target": str(parent_target),
                        "entry_shape": "deictic_logical_table_entry_group",
                        **table_row_insert_selector,
                    },
                )
                continue
            if (
                str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows"
                and source_table_payload is None
            ):
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                    family="source_table_elaboration",
                    reason_code="explicit_table_entry_group_insert_without_table_payload",
                    reason=(
                        "UK table-entry group insertion names an entry anchor, "
                        "but the source does not carry a BlockAmendment table "
                        "payload; lowering blocks instead of inventing table "
                        "rows from flattened text."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "original_target": str(target),
                        "containing_target": str(parent_target),
                        **table_row_insert_selector,
                    },
                )
                continue
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                family="source_table_elaboration",
                reason_code="explicit_table_entry_row_insert_selector",
                reason=(
                    "UK table-row insertion lowered as a typed row insert; "
                    "replay must resolve the source-owned table row before "
                    "mutating table structure."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "original_target": str(target),
                    "containing_target": str(parent_target),
                    **table_row_insert_selector,
                },
            )
            if str(table_row_insert_selector.get("source_payload_mode") or "") == "table_rows":
                assert source_table_payload is not None
                payload_node = dc_replace(
                    source_table_payload,
                    attrs={
                        **dict(source_table_payload.attrs or {}),
                        "source_rule_id": "uk_table_entry_group_insert_payload"
                        if str(table_row_insert_selector.get("selector_mode") or "") == "entry_group_heading"
                        else "uk_table_entry_label_insert_payload",
                        "anchor_direction": str(table_row_insert_selector["direction"]),
                        **(
                            {
                                "relating_text": str(table_row_insert_selector["relating_text"]),
                            }
                            if str(table_row_insert_selector.get("selector_mode") or "")
                            == "entry_group_heading"
                            else {
                                "anchor_entry_label": str(
                                    table_row_insert_selector["anchor_entry_label"]
                                ),
                            }
                        ),
                    },
                )
            elif logical_entry_group_payload:
                assert source_logical_entry_group_payload is not None
                payload_node = dc_replace(
                    source_logical_entry_group_payload,
                    attrs={
                        **dict(source_logical_entry_group_payload.attrs or {}),
                        "source_rule_id": "uk_table_entry_logical_group_insert_payload",
                        "relating_text": str(table_row_insert_selector["relating_text"]),
                        "source_context": str(table_row_insert_selector.get("source_context") or ""),
                    },
                )
            elif (
                str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                or str(table_row_insert_selector.get("source_payload_mode") or "") == "single_table_row"
            ):
                assert source_row_payload is not None
                payload_node = dc_replace(
                    source_row_payload,
                    attrs={
                        **dict(source_row_payload.attrs or {}),
                        "source_rule_id": "uk_table_entry_row_insert_payload",
                        **(
                            {
                                "anchor_entry_label": str(
                                    table_row_insert_selector["anchor_entry_label"]
                                ),
                            }
                            if str(table_row_insert_selector.get("selector_mode") or "") == "entry_label"
                            else {
                                "relating_text": str(table_row_insert_selector["relating_text"]),
                                "source_context": str(
                                    table_row_insert_selector.get("source_context") or ""
                                ),
                            }
                        ),
                    },
                )
            else:
                column_index = int(table_row_insert_selector["column_index"])
                payload_node = IRNode(
                    kind=IRNodeKind.ROW,
                    label=None,
                    attrs={
                        "source_rule_id": "uk_table_entry_row_insert_payload",
                        "target_column_index": str(column_index),
                        "relating_text": str(table_row_insert_selector["relating_text"]),
                    },
                    children=tuple(
                        IRNode(
                            kind=IRNodeKind.CELL,
                            label=None,
                            text=(
                                str(table_row_insert_selector["inserted_text"])
                                if cell_index == column_index
                                else ""
                            ),
                            attrs={
                                "source_rule_id": "uk_table_entry_row_insert_cell",
                                "column_index": str(cell_index),
                            },
                        )
                        for cell_index in range(1, column_index + 1)
                    ),
                )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.INSERT,
                target=parent_target,
                payload=payload_node,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.INSERT,
                    target=parent_target,
                    payload=_payload_with_rewrite_witness(payload_node, lowered_witness),
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=(
                        *_uk_lowered_op_provenance_tags(lowered_witness),
                        (
                            f"{_NOTE_TABLE_ROW_INSERT_SELECTOR}"
                            f"{json.dumps(table_row_insert_selector, ensure_ascii=False)}"
                        ),
                    ),
                    witness_rule_id=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
                )
            )
            continue
        repeal_table_structural_repeal = _uk_table_driven_repeal_table_structural_repeal(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            target=target,
        )
        if repeal_table_structural_repeal.recognized and repeal_table_structural_repeal.match_count == 1:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
                family="source_repeal_table_elaboration",
                reason_code="unique_repeal_table_extent_row_structural_repeal",
                reason=(
                    "UK repeal-table source row matched the affected Act and "
                    "provision exactly, and its extent cell names a whole "
                    "provision repeal; lowering emits a typed exact-target "
                    "repeal instead of replaying the broad repeal schedule."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "table_index": repeal_table_structural_repeal.table_index,
                    "row_text": repeal_table_structural_repeal.row_text,
                    "enactment_cell": repeal_table_structural_repeal.enactment_cell,
                    "extent_cell": repeal_table_structural_repeal.extent_cell,
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=effect.effect_id,
                sequence=sequence,
                action=StructuralAction.REPEAL,
                target=target,
                payload=None,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=StructuralAction.REPEAL,
                    target=target,
                    payload=None,
                    source=src,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                    witness_rule_id=_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID,
                )
            )
            continue
        if repeal_table_structural_repeal.recognized:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=f"{_UK_REPEAL_TABLE_STRUCTURAL_REPEAL_RULE_ID}_unresolved",
                family="source_repeal_table_elaboration",
                reason_code=repeal_table_structural_repeal.reason_code,
                reason=(
                    "UK repeal-table source could not be resolved to one "
                    "exact structural extent row for the affected target."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "match_count": repeal_table_structural_repeal.match_count,
                },
            )
            continue
        repeal_table_text_repeal = _uk_table_driven_repeal_table_quoted_words_text_repeal(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            target=target,
        )
        if repeal_table_text_repeal.recognized and repeal_table_text_repeal.original:
            repeal_table_rule_id = repeal_table_text_repeal.rule_id
            if repeal_table_rule_id == _UK_REPEAL_TABLE_DEFINITION_ENTRY_TEXT_REPEAL_RULE_ID:
                reason_code = "unique_repeal_table_extent_row_definition_entry"
                reason = (
                    "UK repeal-table source row matched the affected Act and "
                    "provision exactly, and its extent cell names a definition "
                    "entry repeal; lowering emits definition-entry text deletes "
                    "instead of replaying the broad repeal schedule."
                )
            else:
                reason_code = "unique_repeal_table_extent_row_quoted_words"
                reason = (
                    "UK repeal-table source row matched the affected Act and "
                    "provision exactly, and its extent cell names a quoted "
                    "word-level repeal; lowering emits a text delete instead "
                    "of replaying the broad repeal schedule."
                )
            repeal_table_originals = (
                repeal_table_text_repeal.original,
                *repeal_table_text_repeal.additional_originals,
            )
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=repeal_table_rule_id,
                family="source_repeal_table_elaboration",
                reason_code=reason_code,
                reason=reason,
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "table_index": repeal_table_text_repeal.table_index,
                    "row_text": repeal_table_text_repeal.row_text,
                    "enactment_cell": repeal_table_text_repeal.enactment_cell,
                    "extent_cell": repeal_table_text_repeal.extent_cell,
                    "original": repeal_table_text_repeal.original,
                    "originals": repeal_table_originals,
                    "occurrence": repeal_table_text_repeal.occurrence,
                    "end_occurrence": repeal_table_text_repeal.end_occurrence,
                },
            )
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )
            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            for original_index, original in enumerate(repeal_table_originals):
                fragment_subs = [
                    {
                        "original": original,
                        "replacement": "",
                        "rule_id": repeal_table_rule_id,
                        "occurrence": str(repeal_table_text_repeal.occurrence),
                        "end_occurrence": str(repeal_table_text_repeal.end_occurrence),
                    }
                ]
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=original,
                        occurrence=repeal_table_text_repeal.occurrence,
                        end_occurrence=repeal_table_text_repeal.end_occurrence,
                    ),
                )
                text_rewrite_witness = _uk_text_rewrite_spec(
                    fragment_subs=fragment_subs,
                    text_patch=text_patch,
                    op_text_match=original,
                    op_text_replacement="",
                    op_text_occurrence=repeal_table_text_repeal.occurrence,
                    op_text_end_occurrence=repeal_table_text_repeal.end_occurrence,
                )
                lowered_witness = UKLoweredOperationWitness(
                    op_id=(
                        effect.effect_id
                        if len(repeal_table_originals) == 1
                        else f"{effect.effect_id}_{original_index}"
                    ),
                    sequence=sequence,
                    action=StructuralAction.TEXT_REPEAL,
                    target=target,
                    payload=None,
                    source=src,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    target_expansion_witness=target_expansion_witness,
                    text_rewrite_witness=text_rewrite_witness,
                    insertion_anchor_witness=None,
                )
                ops.append(
                    LegalOperation(
                        op_id=lowered_witness.op_id,
                        sequence=lowered_witness.sequence,
                        action=StructuralAction.TEXT_REPEAL,
                        target=target,
                        payload=None,
                        source=src,
                        group_id=_uk_temporal_group_id(effect),
                        provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                        text_patch=text_patch,
                        witness_rule_id=repeal_table_rule_id,
                    )
                )
            continue
        if repeal_table_text_repeal.recognized:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=f"{_UK_REPEAL_TABLE_QUOTED_WORDS_TEXT_REPEAL_RULE_ID}_unresolved",
                family="source_repeal_table_elaboration",
                reason_code=repeal_table_text_repeal.reason_code,
                reason=(
                    "UK repeal-table source could not be resolved to one "
                    "bounded quoted-words extent row for the affected target."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "match_count": repeal_table_text_repeal.match_count,
                },
            )
            continue
        table_cell_selector = _uk_table_entry_inline_text_selector(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if table_cell_selector is None:
            table_cell_selector = _uk_table_column_text_patch_selector(
                target_ref=t_str,
                target=target,
                extracted_text=extracted_text,
            )
        source_carried_table_entry_paragraph_substitution = (
            _source_carried_table_entry_paragraph_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
                target_ref=t_str,
                target=target,
            )
            if table_cell_selector is None
            else None
        )
        if source_carried_table_entry_paragraph_substitution is not None:
            table_cell_selector = cast(
                dict[str, Any],
                source_carried_table_entry_paragraph_substitution["table_cell_selector"],
            )
        if table_cell_selector is not None:
            selector_rule_id = str(table_cell_selector.get("rule_id") or _UK_TABLE_ENTRY_INLINE_TEXT_RULE_ID)
            selector_mode = str(table_cell_selector.get("selector_mode") or "")
            table_marker_parent = _uk_parent_target_before_table_marker(target)
            parent_target = (
                target
                if selector_mode in {"unique_column_text", "unique_entry_cell"}
                and table_marker_parent is None
                else table_marker_parent
            )
            if (
                parent_target is not None
                and len(parent_target.path) >= 2
                and parent_target.path[-1] == ("subsection", "1")
                and parent_target.path[-2][0] == "section"
            ):
                table_cell_selector = {
                    **table_cell_selector,
                    "allow_implicit_subsection_one_table": True,
                    "table_marker_parent_target": str(parent_target),
                }
                parent_target = LegalAddress(path=parent_target.path[:-1], special=parent_target.special)
            if parent_target is None:
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id="uk_effect_table_entry_inline_text_target_unresolved",
                    family="source_table_elaboration",
                    reason_code="table_marker_parent_missing",
                    reason=(
                        "UK table-entry word effect named a table cell, but "
                        "the affected target could not be reduced to a containing "
                        "provision for table-cell replay."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={"target_ref": t_str, "target": str(target), **table_cell_selector},
                )
                continue
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=selector_rule_id,
                family="source_table_elaboration",
                reason_code=(
                    "explicit_table_column_preimage_selector"
                    if selector_mode == "unique_column_text"
                    else "source_parent_table_entry_paragraph_selector"
                    if selector_mode == "unique_entry_cell"
                    else "explicit_table_entry_column_selector"
                ),
                reason=(
                    "UK table word effect lowered as a typed table-cell text "
                    "patch; replay must resolve the source-owned table cell "
                    "before mutating text."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "original_target": str(target),
                    "containing_target": str(parent_target),
                    **table_cell_selector,
                },
            )
            target = parent_target
        elif table_entry_instruction := _uk_broad_table_entry_instruction(
            target_ref=t_str,
            target=target,
            extracted_text=extracted_text,
        ):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=_UK_TABLE_ENTRY_INSTRUCTION_REJECTED_RULE_ID,
                family="source_table_elaboration",
                reason_code="table_entry_instruction_without_cell_target",
                reason=(
                    "UK source instruction targets a table entry or column, "
                    "but effect metadata names only a broader provision; "
                    "lowering must not replay it as a host repeal/replace."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=table_entry_instruction,
            )
            continue
        if crossheading_replacement_text is not None:
            target = LegalAddress(path=target.path, special=FacetKind.HEADING)
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_crossheading_before_anchor_replacement_lowered",
                family="target_facet_lowering",
                reason_code="explicit_crossheading_before_anchor_replacement",
                reason=(
                    "UK cross-heading replacement lowered as a typed heading "
                    "facet text patch anchored by the named following provision"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "replacement_text_preview": crossheading_replacement_text[:200],
                },
            )
        if crossheading_text_patch_fragment is not None:
            target = LegalAddress(path=target.path, special=FacetKind.HEADING)
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_crossheading_before_anchor_text_patch_lowered",
                family="target_facet_lowering",
                reason_code="explicit_crossheading_before_anchor_text_patch",
                reason=(
                    "UK cross-heading replacement lowered as a typed heading "
                    "facet text patch anchored by the named following provision"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "match_text": str(crossheading_text_patch_fragment["original"]),
                    "replacement_text_preview": str(crossheading_text_patch_fragment["replacement"])[:200],
                },
            )
        if heading_facet_target:
            target = LegalAddress(path=target.path, special=FacetKind.HEADING)
            heading_append_fragment = _heading_facet_append_fragment(extracted_text)
            heading_after_anchor_insert_fragment = _heading_facet_after_anchor_insert_fragment(extracted_text)
            heading_full_replacement_fragment = _heading_facet_full_replacement_fragment(extracted_text)
            if heading_append_fragment is not None:
                heading_observation_rule = "uk_effect_heading_facet_append_lowered"
                heading_reason_code = "explicit_heading_facet_append"
                heading_reason = (
                    "UK heading/title/sidenote target lowered as a typed facet "
                    "append; replay must mutate only the heading carrier."
                )
            elif heading_after_anchor_insert_fragment is not None:
                heading_observation_rule = "uk_effect_heading_facet_after_anchor_insert_lowered"
                heading_reason_code = "explicit_heading_facet_after_anchor_insert"
                heading_reason = (
                    "UK heading/title/sidenote target lowered as a facet text "
                    "insertion after an explicit heading anchor; replay must "
                    "mutate only the heading carrier."
                )
            elif heading_full_replacement_fragment is not None:
                heading_observation_rule = "uk_effect_heading_facet_full_replacement_lowered"
                heading_reason_code = "explicit_heading_facet_full_replacement"
                heading_reason = (
                    "UK heading/title/sidenote target lowered as a full facet "
                    "replacement; replay must mutate only the heading carrier."
                )
            else:
                heading_observation_rule = "uk_effect_heading_facet_word_patch_lowered"
                heading_reason_code = "explicit_heading_facet_word_patch"
                heading_reason = (
                    "UK heading/title/sidenote target lowered as a facet "
                    "text patch; replay must mutate only the heading carrier."
                )
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=heading_observation_rule,
                family="target_facet_lowering",
                reason_code=heading_reason_code,
                reason=heading_reason,
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "target": str(target)},
            )
        external_act_target = (
            _external_act_target_from_source_text(extracted_text)
            if str(target.special or "") == "whole_act"
            else ""
        )
        if external_act_target:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_external_act_target_rejected",
                family="target_resolution_recovery",
                reason_code="external_act_target_in_source_text",
                reason=(
                    "UK effect metadata points at the current Act, but the "
                    "affecting source text names a different Act as the target"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "source_named_target": external_act_target,
                },
            )
            continue
        whole_act_partial_repeal_exceptions = (
            _partial_whole_act_repeal_exceptions(extracted_text)
            if str(target.special or "") == "whole_act" and effect_type == "repealed in part"
            else ""
        )
        if whole_act_partial_repeal_exceptions:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_partial_whole_act_repeal_rejected",
                family="unsupported_target_scope",
                reason_code="partial_whole_act_repeal_unsupported",
                reason=(
                    "UK effect repeals the whole Act except named provisions; "
                    "lowering cannot safely expand that broad negative scope"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "exception_provisions": whole_act_partial_repeal_exceptions,
                },
            )
            continue
        parse_context = "schedule" if _addr_container(target) == "schedule" else ""
        content_ir = None
        actual_el: Optional[ET.Element] = None
        source_structural_payload_matches_target = False
        if extracted_el is not None:
            flat_p1para_payload = None
            if action == "insert":
                flat_p1para_payload = _flat_p1para_schedule_paragraph_insert_payload(
                    extracted_el,
                    payload_match_target,
                    fallback_target_eid=_fallback_target_eid,
                )
            if flat_p1para_payload is not None:
                flat_p1para_payload_detail = dict(flat_p1para_payload.pop("_lawvm_detail", {}) or {})
                content_ir = flat_p1para_payload
                flat_p1para_schedule_insert_lowered = True
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id=_UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
                    family="payload_normalization",
                    reason_code="flat_blockamendment_p1para_labelled_schedule_paragraph",
                    reason=(
                        "UK inserted schedule paragraph source payload is a flat "
                        "BlockAmendment/P1para with a direct text run beginning with "
                        "the target paragraph label; lowering uses that labelled text "
                        "as the paragraph payload and records sibling heading text as "
                        "unresolved rather than replaying the whole instruction."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "target": str(payload_match_target),
                        **flat_p1para_payload_detail,
                    },
                )
            actual_el = _select_whole_schedule_element(extracted_el, target)
            # Find any BlockAmendment or InlineAmendment in the subtree
            if content_ir is None and actual_el is None:
                for am in extracted_el.iter():
                    if _tag(am) in ("BlockAmendment", "InlineAmendment"):
                        # Find the first structural node whose numbering matches the
                        # target provision. Whole-schedule targets are handled above
                        # so a paragraph "2" does not hijack "Sch. 2".
                        for child in am.iter():
                            ct = _tag(child)
                            if ct in (
                                "Part",
                                "Chapter",
                                "EUChapter",
                                "Pblock",
                                "P1group",
                                "Section",
                                "P1",
                                "Article",
                                "Rule",
                                "Subsection",
                                "P2",
                                "P3",
                                "P4",
                                "Schedule",
                            ):
                                c_num = _direct_structural_num(child)
                                target_num = _addr_leaf_label(payload_match_target)
                                if not target_num or _clean_num(c_num) == _clean_num(target_num):
                                    actual_el = child
                                    break
                        if actual_el is not None:
                            actual_el = _with_trailing_subordinate_siblings(actual_el, am)
                            break

            if content_ir is None and actual_el is None:
                # Fallback: maybe the extracted element ITSELF is the node
                if _tag(extracted_el) in (
                    "Part",
                    "Chapter",
                    "EUChapter",
                    "Pblock",
                    "P1group",
                    "Section",
                    "P1",
                    "Article",
                    "Rule",
                    "Subsection",
                    "P2",
                    "P3",
                    "P4",
                    "Schedule",
                ):
                    target_num = _addr_leaf_label(payload_match_target)
                    extracted_num = _direct_structural_num(extracted_el)
                    if not target_num or _clean_num(extracted_num) == _clean_num(target_num):
                        actual_el = extracted_el
                    else:
                        actual_el = _retarget_instruction_element_to_target(
                            extracted_el,
                            payload_match_target,
                            extracted_text,
                        )
            elif content_ir is None and actual_el is not extracted_el:
                actual_el = _with_trailing_subordinate_siblings(actual_el, extracted_el)

            if content_ir is None and actual_el is not None:
                tag = _tag(actual_el)
                if tag == "Part":
                    content_ir = _parse_part(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Chapter", "EUChapter"):
                    content_ir = _parse_chapter(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "Pblock":
                    content_ir = _parse_pblock(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P1group":
                    content_ir = _parse_p1group(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Section", "P1", "Article", "Rule", "ConventionRights", "EUSection"):
                    content_ir = _parse_section(
                        actual_el, parse_context, force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag in ("Subsection", "P2"):
                    content_ir = _parse_p2(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P3":
                    content_ir = _parse_p3(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "P4":
                    content_ir = _parse_p4(
                        actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                elif tag == "Schedule":
                    content_ir = _parse_schedule_single(
                        actual_el, "schedule", force_active=True, pit_date=None, is_eur=False
                    ).to_dict()
                if content_ir is not None:
                    direct_text = _direct_payload_text(actual_el)
                    if direct_text:
                        content_ir["text"] = direct_text
                    inserted_heading_text = _inserted_section_p1group_heading_text(actual_el, extracted_el, target)
                    target_leaf_kind = _addr_leaf_kind(target) or ""
                    heading_source_rule_id = (
                        "uk_inserted_section_p1group_heading_carrier"
                        if target_leaf_kind == "section"
                        else "uk_inserted_p1group_heading_carrier"
                    )
                    heading_observation_rule_id = (
                        "uk_effect_inserted_section_p1group_heading_carrier_lowered"
                        if target_leaf_kind == "section"
                        else "uk_effect_inserted_p1group_heading_carrier_lowered"
                    )
                    if inserted_heading_text and _prepend_inserted_section_heading_carrier(
                        content_ir,
                        heading_text=inserted_heading_text,
                        source_rule_id=heading_source_rule_id,
                    ):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=heading_observation_rule_id,
                            family="payload_normalization",
                            reason_code=f"inserted_{target_leaf_kind}_wrapped_by_p1group_title",
                            reason=(
                                "UK inserted provision payload is wrapped by a P1group "
                                "Title; lowering preserves that title as a target-owned "
                                "heading carrier instead of relying on a shared live parent group"
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_tag": "P1group",
                                "heading_text_preview": inserted_heading_text[:200],
                            },
                        )
                    source_structural_payload_matches_target = _source_payload_matches_target_leaf(
                        content_ir,
                        payload_match_target,
                    )

        if content_ir is None and t_str in mixed_heading_source_ref_by_target:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_mixed_heading_structural_insert_payload_unresolved",
                family="source_shape_filter",
                reason_code="mixed_heading_structural_insert_payload_missing",
                reason=(
                    "UK mixed structural-plus-heading insert target was "
                    "normalized to its structural component, but no matching "
                    "source-owned structural payload was found; lowering must "
                    "not synthesize inserted body text from the heading-qualified "
                    "metadata string."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_target_ref": mixed_heading_source_ref_by_target[t_str],
                    "structural_target_ref": t_str,
                },
            )
            continue

        if content_ir is None:
            # Infer kind and label from target if metadata points to a specific provision
            inferred_kind = "content"
            inferred_label = None
            _container = _addr_container(target)
            _t_section = _addr_field(target, "section") or _addr_field(target, "schedule")
            _t_part = _addr_field(target, "part")
            _t_chapter = _addr_field(target, "chapter")
            _schedule_paragraph = None
            _schedule_subparagraph = None
            _schedule_items: list[str] = []
            if _container == "schedule":
                _schedule_paragraph, _schedule_subparagraph, _schedule_items = _schedule_target_levels(target)
                _t_subsection = _schedule_subparagraph
                _t_item = _schedule_items[-1] if _schedule_items else None
            else:
                _paras2 = [lbl for k, lbl in target.path if k == "paragraph"]
                _subsec_field2 = _addr_field(target, "subsection")
                if _subsec_field2:
                    _t_subsection = _subsec_field2
                    _t_item = _paras2[0] if _paras2 else None
                else:
                    _t_subsection = _paras2[0] if _paras2 else None
                    _t_item = _paras2[1] if len(_paras2) >= 2 else None
            if _container == "schedule" and not _t_subsection and not _t_item:
                if _schedule_paragraph:
                    inferred_kind = "paragraph"
                    inferred_label = _schedule_paragraph
                else:
                    inferred_kind = "schedule"
                    inferred_label = _t_section
            elif _container == "schedule" and _t_item:
                inferred_kind = "item"
                inferred_label = _t_item
            elif _container == "schedule" and _t_subsection:
                inferred_kind = "subparagraph"
                inferred_label = _t_subsection
            elif _t_item:
                inferred_kind = "paragraph"
                inferred_label = _t_item
            elif _t_subsection:
                inferred_kind = "subsection"
                inferred_label = _t_subsection
            elif _t_section:
                inferred_kind = "section"
                inferred_label = _t_section
            elif _t_chapter:
                inferred_kind = "chapter"
                inferred_label = _t_chapter
            elif _t_part:
                inferred_kind = "part"
                inferred_label = _t_part

            inferred_text = extracted_text or ""
            if use_metadata_fallback and not inferred_text and not _is_heading_only_ref(t_str):
                inferred_text = f"[inserted by metadata source only: {effect.effect_id}]"
            content_ir = {
                "kind": inferred_kind,
                "label": inferred_label,
                "text": inferred_text,
                "children": [],
            }

        # Safety guard: if extraction failed (extracted_el is None) and the action is a
        # structural replace or insert, we have no payload text.  Applying a replace with an
        # empty-text node would silently erase real content, which is worse than a no-op.
        # Repeal is fine (no payload needed).  Word-level effects (text_replace/text_repeal)
        # are handled via fragment_subs and don't reach here with a structural payload.
        if (
            extracted_el is None
            and action in ("replace", "insert")
            and not extracted_text
            and not use_metadata_fallback
        ):
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_missing_structural_payload_rejected",
                family="source_pathology_filter",
                reason_code="missing_extracted_payload",
                reason=(
                    "UK structural effect has no extracted source payload; "
                    "lowering cannot emit an empty replace or insert without "
                    "risking destructive replay"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={"target_ref": t_str, "action": action},
            )
            continue

        curr_action = action
        fragment_subs: Optional[list] = None
        # Text-level fields (populated for text_replace / text_repeal ops)
        op_text_match: Optional[str] = None
        op_text_replacement: Optional[str] = None
        op_text_occurrence: int = 0
        op_text_end_occurrence: int = 0
        if crossheading_group_repeal_selector is not None:
            curr_action = "repeal"
            content_ir = None
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
                family="target_facet_lowering",
                reason_code="explicit_crossheading_and_structural_repeal",
                reason=(
                    "UK source explicitly repeals the named provision and the "
                    "heading above it; lowering keeps the provision target and "
                    "carries a replay selector that may remove the heading "
                    "wrapper only if that wrapper owns exactly the target."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=dict(crossheading_group_repeal_selector),
            )
        elif crossheading_replacement_text is not None:
            curr_action = "text_replace"
            content_ir = None
            op_text_match = "TEXT_ALL"
            op_text_replacement = crossheading_replacement_text
            fragment_subs = [
                {
                    "original": "TEXT_ALL",
                    "replacement": crossheading_replacement_text,
                    "rule_id": _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
                }
            ]
        elif crossheading_text_patch_fragment is not None:
            curr_action = "text_replace"
            content_ir = None
            fragment_subs = [crossheading_text_patch_fragment]
            op_text_match = crossheading_text_patch_fragment["original"]
            op_text_replacement = crossheading_text_patch_fragment["replacement"]
        substituted_series_insert_detail = _substituted_series_new_sibling_insert_detail(
            effect_type=effect.effect_type,
            original_target_refs=original_targets_str,
            target_index=target_index,
            target_ref=t_str,
            target=target,
            content_ir=content_ir,
        )
        if substituted_series_insert_detail is not None:
            curr_action = "insert"
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_substituted_series_new_sibling_insert_lowered",
                family="lowering_normalization",
                reason_code="substituted_for_single_old_target_with_new_sibling_payload",
                reason=(
                    "UK substituted-for row names one replaced target but the "
                    "source-backed replacement series contains an additional "
                    "sibling payload; lowering preserves the first target as "
                    "replace and lowers later source-owned siblings as inserts"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail=substituted_series_insert_detail,
            )
        elif (
            source_replaced_sibling_count is not None
            and target_index >= source_replaced_sibling_count
            and _source_payload_matches_target_leaf(content_ir, target)
        ):
            curr_action = "insert"
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_substituted_range_extra_payload_sibling_insert_lowered",
                family="lowering_normalization",
                reason_code="source_substitution_payload_contains_extra_sibling",
                reason=(
                    "UK source substitutes a bounded sibling range but the "
                    "BlockAmendment contains additional source-owned sibling "
                    "payloads beyond the replaced range; lowering keeps the "
                    "range members as replacements and lowers the extra "
                    "siblings as inserts."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "replaced_sibling_count": source_replaced_sibling_count,
                    "source_payload_kind": str(content_ir.get("kind") or "") if content_ir else "",
                    "source_payload_label": str(content_ir.get("label") or "") if content_ir else "",
                },
            )

        structural_sibling_insert_detail = (
            _structural_sibling_insert_from_source(
                extracted_text=extracted_text,
                target=target,
            )
            if curr_action == "insert"
            and effect_type in {"words inserted", "word inserted"}
            and extracted_text
            else None
        )
        if structural_sibling_insert_detail is not None:
            target = canonicalize_uk_address(
                LegalAddress(
                    path=(
                        *target.path,
                        (
                            structural_sibling_insert_detail["child_kind"],
                            structural_sibling_insert_detail["inserted_label"],
                        ),
                    )
                )
            )
            content_ir = {
                "kind": structural_sibling_insert_detail["child_kind"],
                "label": structural_sibling_insert_detail["inserted_label"],
                "text": structural_sibling_insert_detail["inserted_text"],
                "attrs": {
                    "source_rule_id": "uk_effect_structural_sibling_insert_lowered",
                    "source_anchor_child_label": structural_sibling_insert_detail["anchor_label"],
                    "source_child_kind": structural_sibling_insert_detail["source_kind"],
                },
                "children": [],
            }
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_structural_sibling_insert_lowered",
                family="source_context_elaboration",
                reason_code="source_owned_structural_sibling_insert",
                reason=(
                    "UK source text explicitly inserts a new labelled structural "
                    "sibling after a named child of the affected parent; lowering "
                    "emits a child insert at the source-owned sibling target "
                    "instead of appending payload text to the anchor."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_target_ref": t_str,
                    "source_anchor_child_label": structural_sibling_insert_detail["anchor_label"],
                    "source_child_kind": structural_sibling_insert_detail["source_kind"],
                    "inserted_child_kind": structural_sibling_insert_detail["child_kind"],
                    "inserted_child_label": structural_sibling_insert_detail["inserted_label"],
                    "target": str(target),
                },
            )

        amendment_program_inserted_parent_structural_insert = (
            _amendment_program_inserted_parent_structural_insert(
                extracted_text=extracted_text,
                target=target,
            )
            if extracted_text and curr_action == "insert"
            else None
        )
        if amendment_program_inserted_parent_structural_insert is not None:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id="uk_effect_amendment_program_inserted_parent_structural_insert_rejected",
                family="amendment_program_lowering",
                reason_code="insert_targets_prior_amendment_inserted_parent",
                reason=(
                    "UK source text inserts a child into a paragraph inserted by "
                    "a prior amendment instruction; this needs an amendment-"
                    "program compiler and must not be replayed against an "
                    "unrelated live base-law parent."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    **amendment_program_inserted_parent_structural_insert,
                },
            )
            continue

        # Grounding 2.0: Fragment substitutions
        structural_omission_reclassification = _word_level_structural_subsection_omission(
            effect_type=effect.effect_type,
            extracted_text=extracted_text,
            target=target,
        )
        if structural_omission_reclassification is not None:
            curr_action = "repeal"
            content_ir = None
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_word_omission_structural_subsection_repeal_reclassified",
                family="lowering_normalization",
                reason_code="word_level_feed_row_explicitly_omits_target_subsection",
                reason=(
                    "UK effect feed labels the row as word-level omission, but "
                    "the affecting source explicitly omits the exact affected subsection"
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    **structural_omission_reclassification,
                },
            )

        source_carried_definition_child_text_omission = (
            _fragment_substitution_source_carried_definition_child_text_omission(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
            if extracted_text
            else None
        )
        if source_carried_definition_child_text_omission is not None:
            fragment_subs = [source_carried_definition_child_text_omission]
            content_ir = None
            op_text_match = source_carried_definition_child_text_omission["original"]
            op_text_replacement = source_carried_definition_child_text_omission["replacement"]
            curr_action = "text_repeal" if op_text_replacement == "" else "text_replace"
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_source_carried_definition_child_text_omission_text_patch",
                family="source_context_elaboration",
                reason_code="definition_child_text_omission_resolved_from_parent_source",
                reason=(
                    "UK child-row source names only a definition paragraph and quoted "
                    "omitted text, while the parent source instruction names the "
                    "definition term; lowering combines those source-local facts into "
                    "a bounded definition-child text omission instead of deleting the "
                    "quoted word from the whole target subsection."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "source_parent_id": str(
                        source_carried_definition_child_text_omission.get("source_parent_id") or ""
                    ),
                    "source_definition_term": str(
                        source_carried_definition_child_text_omission.get("source_definition_term") or ""
                    ),
                    "source_child_label": str(
                        source_carried_definition_child_text_omission.get("source_child_label") or ""
                    ),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                },
            )

        source_carried_definition_child_at_end_insert = (
            _fragment_substitution_source_carried_definition_child_at_end_insert(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
            if extracted_text and curr_action == "insert"
            else None
        )
        if source_carried_definition_child_at_end_insert is not None:
            fragment_subs = [source_carried_definition_child_at_end_insert]
            content_ir = None
            op_text_match = source_carried_definition_child_at_end_insert["original"]
            op_text_replacement = source_carried_definition_child_at_end_insert["replacement"]
            curr_action = "text_replace"
            source_definition_child_refined_target = _source_definition_child_refined_target(
                target=target,
                fragment=source_carried_definition_child_at_end_insert,
            )
            if source_definition_child_refined_target is not None:
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id="uk_effect_source_parent_definition_child_target_refined",
                    family="source_context_elaboration",
                    reason_code="source_parent_definition_child_refines_direct_section_paragraph",
                    reason=(
                        "UK affected-provision metadata names a direct section paragraph, "
                        "while the source parent explicitly says that paragraph is inside "
                        "a named definition entry; lowering targets the containing section "
                        "and preserves the child paragraph as a scoped text selector."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "original_target": str(target),
                        "refined_target": str(source_definition_child_refined_target),
                        "source_definition_term": str(
                            source_carried_definition_child_at_end_insert.get("source_definition_term") or ""
                        ),
                        "source_child_label": str(
                            source_carried_definition_child_at_end_insert.get("source_child_label") or ""
                        ),
                    },
                )
                target = source_definition_child_refined_target
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_source_carried_definition_child_at_end_insert_text_patch",
                family="source_context_elaboration",
                reason_code="definition_child_at_end_insert_resolved_from_parent_source",
                reason=(
                    "UK source payload contains only the inserted definition-child tail, "
                    "while the parent source instruction names the definition term and "
                    "paragraph; lowering combines those source-local facts into a bounded "
                    "definition-child text append instead of inserting an unreachable "
                    "address-only subparagraph."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": t_str,
                    "target": str(target),
                    "source_parent_id": str(
                        source_carried_definition_child_at_end_insert.get("source_parent_id") or ""
                    ),
                    "source_definition_term": str(
                        source_carried_definition_child_at_end_insert.get("source_definition_term") or ""
                    ),
                    "source_child_label": str(
                        source_carried_definition_child_at_end_insert.get("source_child_label") or ""
                    ),
                    "source_child_sublabel": str(
                        source_carried_definition_child_at_end_insert.get("source_child_sublabel") or ""
                    ),
                    "text_match": op_text_match,
                    "replacement": op_text_replacement,
                },
            )

        word_level_text_patch_required = (
            is_word_level
            and curr_action != "repeal"
            and structural_sibling_insert_detail is None
        )
        if fragment_subs is None and (curr_action == "replace" or word_level_text_patch_required) and extracted_text:
            treat_as_source_structural_replace = (
                curr_action == "replace"
                and not is_word_level
                and source_structural_payload_matches_target
            )
            heading_full_replacement_precheck = (
                _heading_facet_full_replacement_fragment(extracted_text) if heading_facet_target else None
            )
            source_carried_definition_child_text_omission_precheck = (
                _fragment_substitution_source_carried_definition_child_text_omission(
                    extracted_el=extracted_el,
                    source_root=source_root,
                    extracted_text=extracted_text,
                )
            )
            if not treat_as_source_structural_replace and (
                source_carried_definition_child_text_omission_precheck is not None
                or
                heading_full_replacement_precheck is not None
                or not is_whole_node_replacement(extracted_text, effect.effect_type)
            ):
                table_substitution = _uk_table_driven_corresponding_entry_word_substitution(
                    effect=effect,
                    extracted_text=extracted_text,
                    source_root=source_root,
                    target=target,
                )
                if table_substitution.recognized and table_substitution.original and table_substitution.replacement is not None:
                    fragment_subs = [
                        {
                            "original": table_substitution.original,
                            "replacement": table_substitution.replacement,
                            "rule_id": "uk_effect_corresponding_table_entry_word_substitution",
                        }
                    ]
                    content_ir = None
                    op_text_match = table_substitution.original
                    op_text_replacement = table_substitution.replacement
                    curr_action = "text_replace"
                    _append_uk_effect_lowering_observation(
                        lowering_rejections_out,
                        rule_id="uk_effect_corresponding_table_entry_word_substitution",
                        family="source_table_elaboration",
                        reason_code="unique_column_1_target_column_2_words_match",
                        reason=(
                            "UK table-driven word substitution resolved by matching "
                            "the affected provision to a unique source table row"
                        ),
                        effect=effect,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        detail={
                            "target_ref": t_str,
                            "target": str(target),
                            "table_index": table_substitution.table_index,
                            "row_text": table_substitution.row_text,
                            "original": table_substitution.original,
                            "replacement": table_substitution.replacement,
                        },
                    )
                elif table_substitution.recognized:
                    _append_uk_effect_lowering_rejection(
                        lowering_rejections_out,
                        rule_id="uk_effect_corresponding_table_entry_word_substitution_unresolved",
                        family="source_table_elaboration",
                        reason_code=table_substitution.reason_code,
                        reason=(
                            "UK table-driven word substitution could not be "
                            "resolved to a unique source table row"
                        ),
                        effect=effect,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        detail={
                            "target_ref": t_str,
                            "target": str(target),
                            "match_count": table_substitution.match_count,
                            "replacement": table_substitution.replacement or "",
                        },
                    )
                    curr_action = None
                    continue
                heading_after_anchor_insert = (
                    _heading_facet_after_anchor_insert_fragment(extracted_text) if heading_facet_target else None
                )
                heading_full_replacement = (
                    _heading_facet_full_replacement_fragment(extracted_text) if heading_facet_target else None
                )
                subs = (
                    fragment_subs
                    if table_substitution.recognized
                    else [source_carried_definition_child_text_omission_precheck]
                    if source_carried_definition_child_text_omission_precheck is not None
                    else [heading_after_anchor_insert]
                    if heading_after_anchor_insert is not None
                    else [heading_full_replacement]
                    if heading_full_replacement is not None
                    else parse_fragment_substitution(extracted_text)
                )
                multi_quoted_word_repeals = _multi_quoted_word_repeal_fragments(
                    extracted_text=extracted_text,
                    effect_type=effect.effect_type,
                )
                if (
                    multi_quoted_word_repeals
                    and len(subs) == 1
                    and _multi_fragment_text_selector(str(subs[0].get("original") or ""))
                ):
                    subs = list(multi_quoted_word_repeals)
                if not subs:
                    after_inserted_by_sibling = _fragment_substitution_after_words_inserted_by_sibling(
                        extracted_el=extracted_el,
                        source_root=source_root,
                        extracted_text=extracted_text,
                    )
                    if after_inserted_by_sibling is not None:
                        subs = [after_inserted_by_sibling]
                if not subs:
                    grouped_anchor_occurrence = _fragment_substitution_grouped_anchor_occurrence(
                        extracted_el=extracted_el,
                        source_root=source_root,
                        extracted_text=extracted_text,
                    )
                    if grouped_anchor_occurrence is not None:
                        subs = [grouped_anchor_occurrence]
                if not subs and source_carried_table_entry_paragraph_substitution is not None:
                    subs = [
                        {
                            key: str(value)
                            for key, value in source_carried_table_entry_paragraph_substitution.items()
                            if key != "table_cell_selector"
                        }
                    ]
                if not subs:
                    source_carried_definition_child_insert = (
                        _fragment_substitution_source_carried_definition_child_insert(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_definition_child_insert is not None:
                        subs = [source_carried_definition_child_insert]
                if not subs:
                    source_carried_definition_entry_insert = (
                        _fragment_substitution_source_carried_definition_entry_insert(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_definition_entry_insert is not None:
                        subs = [source_carried_definition_entry_insert]
                if not subs:
                    source_carried_definition_entry_substitution = (
                        _fragment_substitution_source_carried_definition_entry_substitution(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_definition_entry_substitution is not None:
                        subs = [source_carried_definition_entry_substitution]
                if not subs:
                    source_carried_following_words_repeal = (
                        _fragment_substitution_source_carried_following_words_repeal(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_following_words_repeal is not None:
                        subs = [source_carried_following_words_repeal]
                if not subs:
                    source_carried_after_anchor_insert = (
                        _fragment_substitution_source_carried_after_quoted_anchor_insert(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_after_anchor_insert is not None:
                        subs = [source_carried_after_anchor_insert]
                if not subs:
                    source_carried_quoted_text_substitution = (
                        _fragment_substitution_source_carried_quoted_text_substitution(
                            extracted_el=extracted_el,
                            source_root=source_root,
                            extracted_text=extracted_text,
                        )
                    )
                    if source_carried_quoted_text_substitution is not None:
                        subs = [source_carried_quoted_text_substitution]
                if not subs:
                    source_carried_child_tail_repeal = (
                        _fragment_substitution_source_carried_child_tail_repeal(
                            extracted_text=extracted_text,
                            target=target,
                        )
                    )
                    if source_carried_child_tail_repeal is not None:
                        subs = [source_carried_child_tail_repeal]
                if not subs:
                    source_carried_child_tail_substitution = (
                        _fragment_substitution_source_carried_child_tail_substitution(
                            extracted_text=extracted_text,
                            target=target,
                        )
                    )
                    if source_carried_child_tail_substitution is not None:
                        subs = [source_carried_child_tail_substitution]
                if not subs:
                    source_carried_multi_subunit_repeal = (
                        _fragment_substitution_source_carried_multi_subunit_repeal(
                            extracted_text=extracted_text,
                            target=target,
                        )
                    )
                    if source_carried_multi_subunit_repeal is not None:
                        subs = [source_carried_multi_subunit_repeal]
                if not subs:
                    amendment_inserted_text_substitution = (
                        _fragment_substitution_amendment_inserted_text_substitution(
                            extracted_text=extracted_text,
                            target=target,
                        )
                    )
                    if amendment_inserted_text_substitution is not None:
                        subs = [amendment_inserted_text_substitution]
                if subs:
                    subs = _scope_fragment_substitutions_to_source_definition_parent(
                        fragments=subs,
                        extracted_el=extracted_el,
                        source_root=source_root,
                        extracted_text=extracted_text,
                        target=target,
                    )
                    if table_cell_selector is not None:
                        subs = [
                            {
                                **dict(item),
                                "rule_id": str(item.get("rule_id") or selector_rule_id),
                            }
                            for item in subs
                        ]
                    fragment_subs = subs
                    content_ir = None
                    # Promote to text_replace / text_repeal with fields populated.
                    # Use the first pair as the primary; additional pairs stay in notes.
                    primary = subs[0]
                    source_definition_child_refined_target = _source_definition_child_refined_target(
                        target=target,
                        fragment=primary,
                    )
                    if source_definition_child_refined_target is not None:
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_parent_definition_child_target_refined",
                            family="source_context_elaboration",
                            reason_code="source_parent_definition_child_refines_direct_section_paragraph",
                            reason=(
                                "UK affected-provision metadata names a direct section paragraph, "
                                "while the source parent explicitly says that paragraph is inside "
                                "a named definition entry; lowering targets the containing section "
                                "and preserves the child paragraph as a scoped text selector."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "original_target": str(target),
                                "refined_target": str(source_definition_child_refined_target),
                                "source_definition_term": str(primary.get("source_definition_term") or ""),
                                "source_child_label": str(primary.get("source_child_label") or ""),
                            },
                        )
                        target = source_definition_child_refined_target
                    primary_target_suffix = _fragment_target_suffix(primary)
                    if primary_target_suffix is not None:
                        labeled_child_end_selector = _labeled_child_end_range_selector(
                            target,
                            primary,
                            primary_target_suffix,
                        )
                        if not labeled_child_end_selector:
                            _append_uk_effect_lowering_rejection(
                                lowering_rejections_out,
                                rule_id="uk_effect_labeled_child_end_range_target_rejected",
                                family="target_resolution_recovery",
                                reason_code="unsupported_labeled_end_range_target_suffix",
                                reason=(
                                    "UK source text bounds a text range to a labelled child target, "
                                    "but the affected provision target could not safely carry the "
                                    "parent-scoped child-end selector without widening or changing "
                                    "the source scope."
                                ),
                                effect=effect,
                                extracted_el=extracted_el,
                                extracted_text=extracted_text,
                                detail={
                                    "target_ref": t_str,
                                    "target": str(target),
                                    "target_suffix_kind": primary_target_suffix[0],
                                    "target_suffix_label": primary_target_suffix[1],
                                },
                            )
                            curr_action = None
                            continue
                        primary = {**primary, "original": labeled_child_end_selector}
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_labeled_child_end_range_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_bounded_text_range_names_child_endpoint",
                            reason=(
                                "UK source text bounds a range from a parent text anchor to "
                                "the end of a labelled child provision; lowering preserves the "
                                "parent target and encodes the explicit child endpoint in the "
                                "text selector instead of retargeting to the child."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": labeled_child_end_selector,
                                "source_text_match": str(subs[0].get("original") or ""),
                                "target_suffix_kind": primary_target_suffix[0],
                                "target_suffix_label": primary_target_suffix[1],
                            },
                        )
                    op_text_match = primary["original"]
                    op_text_replacement = primary["replacement"]
                    op_text_occurrence = int(primary.get("occurrence", "0") or "0")
                    op_text_end_occurrence = int(primary.get("end_occurrence", "0") or "0")
                    # Word-level fragment edits are replayed as text_replace/text_repeal
                    # regardless of whether the metadata verb was "replace" or "insert".
                    if is_word_level and op_text_replacement == "":
                        curr_action = "text_repeal"
                    else:
                        curr_action = "text_replace"
                    for rewrite_rule_id in _fragment_rule_ids(fragment_subs):
                        if rewrite_rule_id not in _UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS:
                            continue
                        rewrite_fragments = [
                            item
                            for item in fragment_subs or []
                            if str(item.get("rule_id") or "") == rewrite_rule_id
                        ]
                        if not rewrite_fragments:
                            rewrite_fragments = [
                                {
                                    "original": op_text_match,
                                    "replacement": op_text_replacement,
                                    "occurrence": str(op_text_occurrence),
                                }
                            ]
                        for rewrite_fragment in rewrite_fragments:
                            _append_uk_effect_lowering_observation(
                                lowering_rejections_out,
                                rule_id=rewrite_rule_id,
                                family="text_rewrite_lowering",
                                reason_code="explicit_all_occurrences_text_patch",
                                reason=(
                                    "UK effect source explicitly applies a word-level "
                                    "text rewrite wherever/in each place it occurs; "
                                    "lowering preserves that as an all-occurrences "
                                    "text patch scoped to the affected target."
                                ),
                                effect=effect,
                                extracted_el=extracted_el,
                                extracted_text=extracted_text,
                                detail={
                                    "target_ref": t_str,
                                    "target": str(target),
                                    "text_match": str(rewrite_fragment.get("original") or ""),
                                    "replacement": str(rewrite_fragment.get("replacement") or ""),
                                    "occurrence": int(str(rewrite_fragment.get("occurrence") or "0") or "0"),
                                },
                            )
                    if "uk_effect_contextual_adjacent_word_omit_text_patch" in _fragment_rule_ids(
                        fragment_subs
                    ):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_contextual_adjacent_word_omit_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_contextual_adjacent_word_omission_lowered",
                            reason=(
                                "UK source text explicitly omits a quoted word following "
                                "a named local child; lowering preserves that child anchor "
                                "instead of deleting the quoted word from the whole parent."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "occurrence": op_text_occurrence,
                            },
                        )
                    if _UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=_UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID,
                            family="text_rewrite_lowering",
                            reason_code="explicit_range_to_end_there_is_substituted_text_patch",
                            reason=(
                                "UK source text uses the drafting form 'there is substituted' "
                                "for a word-level range ending at the end of the target; lowering "
                                "preserves that as a bounded TEXT_FROM_*_TO_END text patch."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "occurrence": op_text_occurrence,
                            },
                        )
                    for source_definition_fragment in fragment_subs:
                        source_definition_rule_id = str(source_definition_fragment.get("rule_id") or "")
                        if source_definition_rule_id not in {
                            "uk_effect_source_parent_definition_range_text_patch",
                            "uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch",
                            "uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch",
                            "uk_effect_source_parent_definition_child_substitution_text_patch",
                        }:
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=source_definition_rule_id,
                            family="source_context_elaboration",
                            reason_code="text_patch_scoped_to_source_parent_definition",
                            reason=(
                                "UK child-row source gives a generic text patch while the parent "
                                "instruction explicitly names a definition entry; lowering scopes "
                                "the text patch to that definition instead of searching the whole "
                                "target subsection."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(source_definition_fragment.get("source_parent_id") or ""),
                                "source_definition_term": str(
                                    source_definition_fragment.get("source_definition_term") or ""
                                ),
                                "source_unscoped_match_text": str(
                                    source_definition_fragment.get("source_unscoped_match_text") or ""
                                ),
                                "source_child_label": str(source_definition_fragment.get("source_child_label") or ""),
                                "source_child_sublabel": str(
                                    source_definition_fragment.get("source_child_sublabel") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "occurrence": op_text_occurrence,
                                "end_occurrence": op_text_end_occurrence,
                            },
                        )
                    if "uk_effect_source_carried_child_tail_repeal_text_patch" in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_child_tail_repeal_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_child_tail_repeal_lowered",
                            reason=(
                                "UK source text explicitly repeals the words following "
                                "a named paragraph inside the affected subsection; lowering "
                                "preserves that as a bounded child-tail text selector instead "
                                "of deleting from the whole parent."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "source_anchor_child_label": str(primary.get("source_anchor_child_label") or ""),
                                "source_subsection_label": str(primary.get("source_subsection_label") or ""),
                            },
                        )
                    if (
                        "uk_effect_source_carried_following_words_repeal_text_patch"
                        in _fragment_rule_ids(fragment_subs)
                    ):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_following_words_repeal_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_following_words_repeal_lowered",
                            reason=(
                                "UK source parent says the following words are repealed "
                                "and the BlockAmendment carries only those words; lowering "
                                "preserves the block payload as the exact deletion preimage."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "source_parent_id": str(primary.get("source_parent_id") or ""),
                            },
                        )
                    if "uk_effect_source_carried_subparagraph_tail_repeal_text_patch" in _fragment_rule_ids(
                        fragment_subs
                    ):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_subparagraph_tail_repeal_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_subparagraph_tail_repeal_lowered",
                            reason=(
                                "UK source text explicitly repeals the words following "
                                "a named subparagraph inside the affected paragraph; lowering "
                                "preserves that as a bounded child-tail text selector instead "
                                "of deleting from the whole paragraph."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "source_anchor_child_kind": str(
                                    primary.get("source_anchor_child_kind") or ""
                                ),
                                "source_anchor_child_label": str(
                                    primary.get("source_anchor_child_label") or ""
                                ),
                                "source_parent_kind": str(primary.get("source_parent_kind") or ""),
                                "source_parent_label": str(primary.get("source_parent_label") or ""),
                            },
                        )
                    if _UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=_UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
                            family="source_table_elaboration",
                            reason_code="source_carried_table_entry_paragraph_substitution_lowered",
                            reason=(
                                "UK child-row source names a paragraph or subparagraph "
                                "inside a table entry, while the parent source names the "
                                "entry; lowering combines those source-local facts into "
                                "a bounded table-cell text patch instead of inventing "
                                "schedule paragraph structure."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "source_parent_id": str(primary.get("source_parent_id") or ""),
                                "source_entry_label": str(primary.get("source_entry_label") or ""),
                                "source_paragraph_label": str(primary.get("source_paragraph_label") or ""),
                                "source_subparagraph_label": str(
                                    primary.get("source_subparagraph_label") or ""
                                ),
                            },
                        )
                    if "uk_effect_source_carried_child_tail_substitution_text_patch" in _fragment_rule_ids(
                        fragment_subs
                    ):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_child_tail_substitution_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_child_tail_substitution_lowered",
                            reason=(
                                "UK source text explicitly substitutes the words after "
                                "a named paragraph inside the affected subsection; lowering "
                                "preserves that as a bounded child-tail text selector instead "
                                "of replacing the whole parent."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "source_anchor_child_label": str(primary.get("source_anchor_child_label") or ""),
                                "source_subsection_label": str(primary.get("source_subsection_label") or ""),
                            },
                        )
                    if "uk_effect_source_carried_multi_subunit_repeal_text_patch" in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_multi_subunit_repeal_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="source_carried_multi_subunit_repeal_lowered",
                            reason=(
                                "UK source text explicitly repeals quoted words where "
                                "they occur in named child subsections; lowering preserves "
                                "those child labels in a synthetic selector rather than "
                                "deleting from the whole parent section."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "source_child_labels": str(primary.get("source_child_labels") or ""),
                                "source_section_label": str(primary.get("source_section_label") or ""),
                            },
                        )
                    if _UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=_UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID,
                            family="text_rewrite_lowering",
                            reason_code="multi_quoted_word_repeal_split",
                            reason=(
                                "UK source text repeals multiple separately quoted word "
                                "fragments; lowering emits one bounded text delete per "
                                "quoted fragment instead of replaying a collapsed selector."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "fragments": tuple(str(item.get("original") or "") for item in fragment_subs),
                            },
                        )
                    if "uk_effect_amendment_inserted_text_substitution_text_patch" in _fragment_rule_ids(fragment_subs):
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_amendment_inserted_text_substitution_text_patch",
                            family="amendment_program_lowering",
                            reason_code="source_targets_inserted_text_in_amendment_instruction",
                            reason=(
                                "UK source text substitutes text inserted by a named amendment "
                                "instruction; lowering preserves that as a bounded rewrite of "
                                "the target amendment instruction's inserted payload, not as a "
                                "base-law text guess."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "source_paragraph_label": str(primary.get("source_paragraph_label") or ""),
                                "source_item_label": str(primary.get("source_item_label") or ""),
                            },
                        )
                    if op_text_end_occurrence:
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_range_independent_end_occurrence_text_patch",
                            family="text_rewrite_lowering",
                            reason_code="explicit_independent_end_occurrence_text_range",
                            reason=(
                                "UK source text gives separate ordinal occurrences for "
                                "the start and end anchors of a word-level range; lowering "
                                "preserves both ordinals in a typed text selector rather than "
                                "guessing the first end anchor after the start."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "occurrence": op_text_occurrence,
                                "end_occurrence": op_text_end_occurrence,
                            },
                        )
                    for sibling_context_fragment in fragment_subs:
                        if (
                            str(sibling_context_fragment.get("rule_id") or "")
                            != "uk_effect_after_words_inserted_by_sibling_text_patch"
                        ):
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_after_words_inserted_by_sibling_text_patch",
                            family="source_context_elaboration",
                            reason_code="text_insert_anchor_resolved_from_named_source_sibling",
                            reason=(
                                "UK source inserts words after the words inserted by a named "
                                "sibling sub-paragraph; lowering resolves that anchor from the "
                                "cited sibling source instruction instead of guessing from live text."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_sibling_label": str(
                                    sibling_context_fragment.get("source_sibling_label") or ""
                                ),
                                "source_sibling_rule_id": str(
                                    sibling_context_fragment.get("source_sibling_rule_id") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                            },
                        )
                    for grouped_context_fragment in fragment_subs:
                        if (
                            str(grouped_context_fragment.get("rule_id") or "")
                            != "uk_effect_grouped_anchor_occurrence_substitution_text_patch"
                        ):
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_grouped_anchor_occurrence_substitution_text_patch",
                            family="source_context_elaboration",
                            reason_code="text_substitution_anchor_resolved_from_group_parent",
                            reason=(
                                "UK source child gives only the ordinal occurrence to replace, "
                                "while its parent instruction explicitly carries the quoted "
                                "anchor. Lowering combines those source-local facts instead of "
                                "guessing the anchor from live text."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(grouped_context_fragment.get("source_parent_id") or ""),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "occurrence": op_text_occurrence,
                            },
                        )
                    for definition_entry_context_fragment in fragment_subs:
                        if (
                            str(definition_entry_context_fragment.get("rule_id") or "")
                            != "uk_effect_source_carried_definition_entry_insert_text_patch"
                        ):
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_definition_entry_insert_text_patch",
                            family="source_context_elaboration",
                            reason_code="definition_insert_anchor_resolved_from_parent_source",
                            reason=(
                                "UK source payload contains only the inserted definition entry, "
                                "while the parent source instruction names the definition anchor; "
                                "lowering combines those source-local facts instead of guessing "
                                "definition placement from live text."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(
                                    definition_entry_context_fragment.get("source_parent_id") or ""
                                ),
                                "source_anchor_definition_term": str(
                                    definition_entry_context_fragment.get("source_anchor_definition_term") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                                "payload_normalization_rule_ids": tuple(
                                    rule_id
                                    for rule_id in str(
                                        definition_entry_context_fragment.get(
                                            "payload_normalization_rule_ids"
                                        )
                                        or ""
                                    ).split(US)
                                    if rule_id
                                ),
                            },
                        )
                    for definition_entry_context_fragment in fragment_subs:
                        if (
                            str(definition_entry_context_fragment.get("rule_id") or "")
                            != "uk_effect_source_carried_definition_entry_substitution_text_patch"
                        ):
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_definition_entry_substitution_text_patch",
                            family="source_context_elaboration",
                            reason_code="definition_substitution_anchor_resolved_from_parent_source",
                            reason=(
                                "UK source payload contains only the replacement definition entry, "
                                "while the parent source instruction names the definition being "
                                "substituted; lowering combines those source-local facts instead "
                                "of guessing the old definition term from live text."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(
                                    definition_entry_context_fragment.get("source_parent_id") or ""
                                ),
                                "source_original_definition_term": str(
                                    definition_entry_context_fragment.get("source_original_definition_term") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                            },
                        )
                    for definition_child_context_fragment in fragment_subs:
                        if (
                            str(definition_child_context_fragment.get("rule_id") or "")
                            != "uk_effect_source_carried_definition_child_text_omission_text_patch"
                        ):
                            continue
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id="uk_effect_source_carried_definition_child_text_omission_text_patch",
                            family="source_context_elaboration",
                            reason_code="definition_child_text_omission_resolved_from_parent_source",
                            reason=(
                                "UK child-row source names only a definition paragraph and quoted "
                                "omitted text, while the parent source instruction names the "
                                "definition term; lowering combines those source-local facts into "
                                "a bounded definition-child text omission instead of deleting the "
                                "quoted word from the whole target subsection."
                            ),
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(
                                    definition_child_context_fragment.get("source_parent_id") or ""
                                ),
                                "source_definition_term": str(
                                    definition_child_context_fragment.get("source_definition_term") or ""
                                ),
                                "source_child_label": str(
                                    definition_child_context_fragment.get("source_child_label") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                            },
                        )
                    for source_carried_context_fragment in fragment_subs:
                        source_carried_rule_id = str(source_carried_context_fragment.get("rule_id") or "")
                        if source_carried_rule_id not in {
                            "uk_effect_source_carried_after_quoted_anchor_insert_text_patch",
                            "uk_effect_source_carried_quoted_text_substitution_text_patch",
                        }:
                            continue
                        reason_code = (
                            "quoted_insert_anchor_resolved_from_parent_source"
                            if source_carried_rule_id
                            == "uk_effect_source_carried_after_quoted_anchor_insert_text_patch"
                            else "quoted_substitution_preimage_resolved_from_parent_source"
                        )
                        reason = (
                            "UK source payload contains only the inserted text, while "
                            "the parent source instruction names the quoted after-anchor; "
                            "lowering combines those source-local facts instead of guessing "
                            "the anchor from live text."
                            if source_carried_rule_id
                            == "uk_effect_source_carried_after_quoted_anchor_insert_text_patch"
                            else "UK source payload contains only the replacement text, while "
                            "the parent source instruction names the quoted preimage; lowering "
                            "combines those source-local facts instead of guessing the old text "
                            "from live state."
                        )
                        _append_uk_effect_lowering_observation(
                            lowering_rejections_out,
                            rule_id=source_carried_rule_id,
                            family="source_context_elaboration",
                            reason_code=reason_code,
                            reason=reason,
                            effect=effect,
                            extracted_el=extracted_el,
                            extracted_text=extracted_text,
                            detail={
                                "target_ref": t_str,
                                "target": str(target),
                                "source_parent_id": str(source_carried_context_fragment.get("source_parent_id") or ""),
                                "source_definition_term": str(
                                    source_carried_context_fragment.get("source_definition_term") or ""
                                ),
                                "source_inserted_text": str(
                                    source_carried_context_fragment.get("source_inserted_text") or ""
                                ),
                                "text_match": op_text_match,
                                "replacement": op_text_replacement,
                            },
                        )
                else:
                    # Fallback regex for simple omissions not caught by NLP
                    _OPEN_Q = "\"\u201c\u2018'"
                    _CLOSE_Q = "\"\u201d\u2019'"
                    m_omit = re.search("(?:omit|repeal) [" + _OPEN_Q + "](.*?)[" + _CLOSE_Q + "]", extracted_text, re.I)
                    if not m_omit:
                        m_omit = re.search(
                            "[" + _OPEN_Q + "](.*?)[" + _CLOSE_Q + "] is (?:omitted|repealed)", extracted_text, re.I
                        )
                    if m_omit:
                        fragment_subs = [{"original": m_omit.group(1), "replacement": ""}]
                        content_ir = None
                        op_text_match = m_omit.group(1)
                        op_text_replacement = ""
                        curr_action = "text_repeal" if is_word_level else "text_replace"
                    elif (
                        is_word_level
                        and effect.effect_type == "substituted for words"
                        and content_ir is not None
                        and content_ir.get("kind") == _addr_leaf_kind(target)
                        and _clean_num(str(content_ir.get("label") or "")) == _clean_num(_addr_leaf_label(target) or "")
                    ):
                        # Some archive-backed UK effects are labeled as word-level
                        # substitutions even though the affecting source provides
                        # the fully substituted structural node text. When we
                        # already extracted a typed payload and no quoted fragment
                        # can be recovered, treat this as a structural replace
                        # rather than silently dropping the effect.
                        curr_action = "replace"
                    elif is_word_level:
                        quote_only_definition_omission: Optional[tuple[str, str]] = None
                        quote_only_omission = None
                        if (
                            effect_type in {"words omitted", "word omitted", "words repealed", "word repealed"}
                            and len(targets_str) == 1
                        ):
                            quote_only_definition_omission = _quote_only_definition_list_omission_payload_match(
                                extracted_el=extracted_el,
                                source_root=source_root,
                                extracted_text=extracted_text,
                            )
                            quote_only_omission = _quote_only_omission_payload_match(extracted_text)
                        if quote_only_definition_omission is not None:
                            definition_term, source_parent_id = quote_only_definition_omission
                            fragment_subs = [
                                {
                                    "original": f"TEXT_DEFINITION_ENTRY_{definition_term}",
                                    "replacement": "",
                                    "rule_id": "uk_effect_quote_only_definition_list_omission_text_patch",
                                }
                            ]
                            content_ir = None
                            op_text_match = f"TEXT_DEFINITION_ENTRY_{definition_term}"
                            op_text_replacement = ""
                            curr_action = "text_repeal"
                            _append_uk_effect_lowering_observation(
                                lowering_rejections_out,
                                rule_id="uk_effect_quote_only_definition_list_omission_text_patch",
                                family="text_rewrite_lowering",
                                reason_code="quote_only_payload_in_parent_definition_omission_list",
                                reason=(
                                    "UK word-level omission source row contains only a quoted "
                                    "definition term, and its parent source instruction explicitly "
                                    "omits definitions; lowering preserves a bounded definition-entry "
                                    "selector instead of deleting every phrase occurrence."
                                ),
                                effect=effect,
                                extracted_el=extracted_el,
                                extracted_text=extracted_text,
                                detail={
                                    "target_ref": t_str,
                                    "target": str(target),
                                    "definition_term": definition_term,
                                    "source_parent_id": source_parent_id,
                                },
                            )
                        elif quote_only_omission:
                            fragment_subs = [
                                {
                                    "original": quote_only_omission,
                                    "replacement": "",
                                    "rule_id": "uk_effect_quote_only_omission_payload_text_patch",
                                }
                            ]
                            content_ir = None
                            op_text_match = quote_only_omission
                            op_text_replacement = ""
                            curr_action = "text_repeal"
                        else:
                            # We couldn't extract the fragment for a word-level effect.
                            # Do NOT replace the whole node text with the amendment instruction!
                            unlowered_overlap_substitution_targets.append(t_str)
                            unlowered_overlap_substitution_reason = (
                                "overlap_substitution_arity_unsupported"
                                if len(targets_str) > 1
                                else "overlap_substitution_parse_failed"
                            )
                            curr_action = None

        if curr_action:
            preceding_eid = None
            preceding_eid_source = "effect_comments_after_clause"
            used_chained_insert_anchor = False
            if chained_insert_preceding_eid:
                preceding_eid = chained_insert_preceding_eid
                preceding_eid_source = chained_insert_preceding_eid_source
                used_chained_insert_anchor = True
            source_anchor_text = ""
            if extracted_el is not None:
                source_anchor_text = _instruction_text_before_amendment_container(extracted_el) or (extracted_text or "")
            source_preceding_eid, source_preceding_eid_source = _source_after_insertion_anchor(
                source_anchor_text,
                target,
            )
            if source_preceding_eid and not preceding_eid:
                preceding_eid = source_preceding_eid
                preceding_eid_source = source_preceding_eid_source or preceding_eid_source
            following_eid = None
            following_eid_source = None
            if curr_action == "insert":
                following_eid, following_eid_source = _source_before_insertion_anchor(
                    source_anchor_text,
                    target,
                )
            if "after " in effect.comments.lower():
                rel_m = re.search(r"after (?:paragraph|section|ss\.|s\.)\s?\(?([0-9a-zA-Z]+)\)?", effect.comments, re.I)
                if rel_m and not preceding_eid:
                    num = rel_m.group(1)
                    preceding_eid = f"p1-{num}" if "paragraph" in effect.comments.lower() else f"section-{num}"

            # Build payload IRNode (None when fragment substitution handles content)
            payload_node_mut: Optional[UKMutableNode] = _to_mutable_node(content_ir) if content_ir else None
            if (
                payload_node_mut is not None
                and target_replacement_leaf_override
                and target_replacement_leaf_kind
                and str(payload_node_mut.kind).lower() == target_replacement_leaf_kind
            ):
                payload_node_mut.label = target_replacement_leaf_override
            if payload_node_mut is not None and curr_action == "insert":
                leaf_kind = _addr_leaf_kind(target) or ""
                leaf_label = _addr_leaf_label(target) or ""
                if (
                    leaf_kind
                    and leaf_label
                    and payload_node_mut.kind == leaf_kind
                    and not _clean_num(payload_node_mut.label or "")
                ):
                    payload_node_mut.label = leaf_label
                leafish_kinds = {"subsection", "paragraph", "subparagraph", "item", "point"}
                if (
                    leaf_kind in leafish_kinds
                    and payload_node_mut.kind in leafish_kinds
                    and payload_node_mut.kind != leaf_kind
                    and _clean_num(payload_node_mut.label or "") == _clean_num(leaf_label)
                ):
                    payload_node_mut.kind = cast(IRNodeKind, leaf_kind)
            if payload_node_mut is not None and curr_action in ("insert", "replace"):
                payload_identity_target = payload_match_target if curr_action == "replace" else target
                payload_node_mut = _synthesize_whole_schedule_payload_descendant_eids(
                    payload_node_mut,
                    target=payload_identity_target,
                    effect=effect,
                    lowering_records_out=lowering_rejections_out,
                    allow_payload_identity_synthesis=allow_payload_identity_synthesis,
                )
                payload_node_mut = _synthesize_payload_descendant_eids(
                    payload_node_mut,
                    target=payload_identity_target,
                    effect=effect,
                    lowering_records_out=lowering_rejections_out,
                    allow_payload_identity_synthesis=allow_payload_identity_synthesis,
                )

            if curr_action in ("insert", "replace") and _is_non_substantive_structural_payload(payload_node_mut):
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id="uk_effect_non_substantive_payload_rejected",
                    family="source_pathology_filter",
                    reason_code="non_substantive_structural_payload",
                    reason=(
                        "UK structural effect payload contains only numbering "
                        "or dot leaders, so replaying it would create a bogus "
                        "legal unit"
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "action": curr_action,
                        "payload_kind": str(payload_node_mut.kind) if payload_node_mut is not None else "",
                    },
                )
                continue
            if (
                curr_action == "replace"
                and _is_broad_schedule_flat_replace_payload(
                    target=target,
                    payload_node=payload_node_mut,
                    actual_source_el=actual_el,
                )
            ):
                _append_uk_effect_lowering_rejection(
                    lowering_rejections_out,
                    rule_id="uk_effect_broad_schedule_flat_payload_rejected",
                    family="payload_coverage_filter",
                    reason_code="broad_schedule_or_part_replace_payload_undercovered",
                    reason=(
                        "UK structural replace targets a whole schedule or schedule part, "
                        "but the extracted source payload is only flat text and does not "
                        "claim the target's descendant structure."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "target": str(target),
                        "payload_kind": str(payload_node_mut.kind),
                        "payload_label": str(payload_node_mut.label or ""),
                        "payload_text_preview": " ".join((payload_node_mut.text or "").split())[:240],
                    },
                )
                continue
            payload_node = payload_node_mut.to_irnode() if payload_node_mut is not None else None
            text_patch_items: list[tuple[Optional[TextPatchSpec], Optional[list]]] = []
            separate_definition_repeals = _separate_definition_repeal_fragments(fragment_subs)
            separate_occurrence_replacements = _separate_occurrence_text_replace_fragments(fragment_subs)
            separate_all_occurrences_replacements = _separate_all_occurrences_text_replace_fragments(fragment_subs)
            separate_multi_quoted_word_repeals = _separate_multi_quoted_word_repeal_fragments(fragment_subs)
            if curr_action == "text_repeal" and separate_definition_repeals:
                for fragment in separate_definition_repeals:
                    text_patch_items.append(
                        (
                            TextPatchSpec(
                                kind=TextPatchKindEnum.DELETE,
                                selector=TextSelector(
                                    match_text=fragment["original"],
                                    occurrence=0,
                                ),
                            ),
                            [fragment],
                        )
                    )
            elif curr_action == "text_repeal" and separate_multi_quoted_word_repeals:
                for fragment in separate_multi_quoted_word_repeals:
                    text_patch_items.append(
                        (
                            TextPatchSpec(
                                kind=TextPatchKindEnum.DELETE,
                                selector=TextSelector(
                                    match_text=fragment["original"],
                                    occurrence=0,
                                ),
                            ),
                            [fragment],
                        )
                    )
            elif curr_action == "text_replace" and separate_occurrence_replacements:
                for fragment in separate_occurrence_replacements:
                    text_patch_items.append(
                        (
                            TextPatchSpec(
                                kind=TextPatchKindEnum.REPLACE,
                                selector=TextSelector(
                                    match_text=fragment["original"],
                                    occurrence=int(fragment["occurrence"]),
                                ),
                                replacement=fragment["replacement"],
                            ),
                            [fragment],
                        )
                    )
            elif curr_action == "text_replace" and separate_all_occurrences_replacements:
                for fragment in separate_all_occurrences_replacements:
                    text_patch_items.append(
                        (
                            TextPatchSpec(
                                kind=TextPatchKindEnum.REPLACE,
                                selector=TextSelector(
                                    match_text=fragment["original"],
                                    occurrence=0,
                                ),
                                replacement=fragment["replacement"],
                            ),
                            [fragment],
                        )
                    )
            elif curr_action == "text_repeal" and op_text_match:
                text_patch_items.append(
                    (
                        TextPatchSpec(
                            kind=TextPatchKindEnum.DELETE,
                            selector=TextSelector(
                                match_text=op_text_match,
                                occurrence=op_text_occurrence,
                                end_occurrence=op_text_end_occurrence,
                            ),
                        ),
                        fragment_subs,
                    )
                )
            elif (
                curr_action == "text_replace"
                and op_text_match == "TEXT_FROM__TO_END"
                and op_text_replacement is not None
            ):
                text_patch_items.append(
                    (
                        TextPatchSpec(
                            kind=TextPatchKindEnum.APPEND,
                            selector=TextSelector(
                                match_text="TEXT_END",
                                occurrence=0,
                            ),
                            replacement=op_text_replacement,
                        ),
                        fragment_subs,
                    )
                )
            elif curr_action == "text_replace" and op_text_match and op_text_replacement is not None:
                text_patch_items.append(
                    (
                        TextPatchSpec(
                            kind=TextPatchKindEnum.REPLACE,
                            selector=TextSelector(
                                match_text=op_text_match,
                                occurrence=op_text_occurrence,
                                end_occurrence=op_text_end_occurrence,
                            ),
                            replacement=op_text_replacement,
                        ),
                        fragment_subs,
                    )
                )
            else:
                text_patch_items.append((None, fragment_subs))

            # Build source
            src = OperationSource(
                statute_id=effect.affecting_act_id,
                title=effect.affecting_title,
                effective=effect_witness.applicability.effective_date or "",
                raw_text=extraction_witness.extracted_text,
            )

            target_expansion_witness = _uk_target_expansion_witness(
                t_str,
                [t_str],
                original_targets_str=original_targets_str,
            )
            insertion_anchor_witness = _uk_insertion_anchor_witness(
                preceding_eid,
                following_eid=following_eid,
                anchor_source=following_eid_source or preceding_eid_source,
            )
            if used_chained_insert_anchor:
                _append_uk_effect_lowering_observation(
                    lowering_rejections_out,
                    rule_id="uk_effect_chained_insertion_anchor_lowered",
                    family="target_resolution_recovery",
                    reason_code="same_effect_insert_targets_ordered_by_prior_generated_target",
                    reason=(
                        "UK effect expands one insertion instruction into multiple sibling "
                        "insert operations; later operations are anchored after the prior "
                        "generated target rather than the original source anchor."
                    ),
                    effect=effect,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    detail={
                        "target_ref": t_str,
                        "target": str(target),
                        "preceding_eid": preceding_eid,
                        "preceding_eid_source": preceding_eid_source,
                    },
                )
            for text_patch_item, fragment_subs_for_witness in text_patch_items:
                text_rewrite_witness = _uk_text_rewrite_spec(
                    fragment_subs=fragment_subs_for_witness,
                    text_patch=text_patch_item,
                    op_text_match=op_text_match,
                    op_text_replacement=op_text_replacement,
                    op_text_occurrence=op_text_occurrence,
                    op_text_end_occurrence=op_text_end_occurrence,
                )
                lowered_witness = UKLoweredOperationWitness(
                    op_id=(
                        f"{effect.effect_id}_{len(ops)}"
                        if len(targets_str) > 1 or len(text_patch_items) > 1
                        else effect.effect_id
                    ),
                    sequence=sequence,
                    action=_to_structural_action(curr_action),
                    target=target,
                    payload=payload_node,
                    source=src,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    target_expansion_witness=target_expansion_witness,
                    text_rewrite_witness=text_rewrite_witness,
                    insertion_anchor_witness=insertion_anchor_witness,
                )
                provenance_tags = _uk_lowered_op_provenance_tags(lowered_witness)
                if table_cell_selector is not None:
                    provenance_tags = (
                        *provenance_tags,
                        f"{_NOTE_TABLE_CELL_SELECTOR}{json.dumps(table_cell_selector, ensure_ascii=False)}",
                    )
                op_witness_rule_id = None
                if crossheading_group_repeal_selector is not None and curr_action == "repeal":
                    op_witness_rule_id = _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE
                    provenance_tags = (
                        *provenance_tags,
                        (
                            f"{_NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR}"
                            f"{json.dumps(crossheading_group_repeal_selector, ensure_ascii=False)}"
                        ),
                    )
                if (
                    label_changing_substitution is not None
                    and curr_action == "replace"
                    and tuple(target.path) == tuple(label_changing_substitution.source_target.path)
                ):
                    op_witness_rule_id = _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID
                    label_change_note = {
                        "rule_id": _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
                        "source_target": str(label_changing_substitution.source_target),
                        "replacement_target": str(label_changing_substitution.replacement_target),
                        "source_ref": label_changing_substitution.source_ref,
                        "replacement_ref": label_changing_substitution.replacement_ref,
                    }
                    provenance_tags = (
                        *provenance_tags,
                        (
                            f"{_NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION}"
                            f"{json.dumps(label_change_note, ensure_ascii=False)}"
                        ),
                    )
                if flat_p1para_schedule_insert_lowered and curr_action == "insert":
                    op_witness_rule_id = _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID
                if (
                    source_parent_substitution_range_payload is not None
                    and curr_action == "replace"
                    and target_index < len(source_parent_substitution_range_payload["payload_labels"])
                ):
                    op_witness_rule_id = _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
                if source_parent_at_end_added_payload is not None and curr_action == "insert":
                    op_witness_rule_id = _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID
                op = LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=lowered_witness.action,
                    target=lowered_witness.target,
                    payload=_payload_with_rewrite_witness(lowered_witness.payload, lowered_witness),
                    source=lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=provenance_tags,
                    text_patch=text_patch_item,
                    witness_rule_id=op_witness_rule_id,
                )
                ops.append(op)
            if curr_action == "insert" and preceding_eid:
                target_anchor_eid = _target_anchor_eid(target)
                if target_anchor_eid:
                    chained_insert_preceding_eid = target_anchor_eid
                    chained_insert_preceding_eid_source = "prior_insert_in_same_effect"
            else:
                chained_insert_preceding_eid = None
                chained_insert_preceding_eid_source = "effect_comments_after_clause"
    if not ops and unlowered_overlap_substitution_targets:
        appropriate_place_definition_entry = _looks_like_appropriate_place_definition_entry_insert_text(
            extracted_text or ""
        )
        lowering_rule_id = (
            "uk_effect_appropriate_place_definition_entry_insert_rejected"
            if appropriate_place_definition_entry
            else "uk_effect_overlap_substitution_unlowered"
        )
        reason_code = (
            "appropriate_place_definition_entry_requires_anchor_claim"
            if appropriate_place_definition_entry
            else unlowered_overlap_substitution_reason
        )
        reason = (
            "UK source inserts a definition entry at an appropriate place without "
            "naming an anchor; lowering requires a validated placement claim and "
            "must not infer an insertion point from live text or oracle order."
            if appropriate_place_definition_entry
            else (
                "UK word-level overlap substitution lowered to no replay operations "
                "because the source instruction could not be parsed into a safe text patch"
            )
        )
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id=lowering_rule_id,
            family="lowering_filter",
            reason_code=reason_code,
            reason=reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "effect_type_normalized": effect_type,
                "original_affected_provisions": effect.affected_provisions,
                "original_target_candidates": original_targets_str,
                "unlowered_target_candidates": unlowered_overlap_substitution_targets,
                "target_candidate_count": len(targets_str),
                "parser": "parse_fragment_substitution",
                "placement_family": (
                    "appropriate_place_definition_entry_requires_anchor_claim"
                    if appropriate_place_definition_entry
                    else ""
                ),
            },
        )
    if action == "replace" and trailing_repeal_refs:
        src = OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        )
        for repeal_idx, repeal_ref in enumerate(trailing_repeal_refs):
            repeal_target = _parse_affected_target(repeal_ref)
            target_expansion_witness = _uk_target_expansion_witness(
                repeal_ref,
                [repeal_ref],
                original_targets_str=original_targets_str,
            )
            lowered_witness = UKLoweredOperationWitness(
                op_id=f"{effect.effect_id}_repeal_{repeal_idx}",
                sequence=sequence,
                action=StructuralAction.REPEAL,
                target=repeal_target,
                payload=None,
                source=src,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                target_expansion_witness=target_expansion_witness,
                text_rewrite_witness=None,
                insertion_anchor_witness=None,
            )
            ops.append(
                LegalOperation(
                    op_id=lowered_witness.op_id,
                    sequence=lowered_witness.sequence,
                    action=lowered_witness.action,
                    target=lowered_witness.target,
                    payload=None,
                    source=lowered_witness.source,
                    group_id=_uk_temporal_group_id(effect),
                    provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                    witness_rule_id=(
                        _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
                        if source_parent_substitution_range_payload is not None
                        else None
                    ),
                )
            )
    return ops


# ---------------------------------------------------------------------------
# Replay Pipeline
# ---------------------------------------------------------------------------


class UKReplayPipeline:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def compile_ops_for_statute(
        self,
        affected_act_id: str,
        pit_date: Optional[str] = None,
        archive: Optional[Any] = None,
        allow_metadata_backfill: bool = True,
        applicability_mode: str = "effective_date_plus_feed_applied",
        authority_mode: str = "current_mixed",
        allow_metadata_only_effects: bool = True,
        authority_rejections_out: Optional[list[dict[str, Any]]] = None,
        lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
        effect_feed_parse_rejections_out: Optional[list[dict[str, Any]]] = None,
        effect_diagnostics_out: Optional[list[dict[str, Any]]] = None,
    ) -> list[LegalOperation]:
        """Compile IR ops for *affected_act_id*.

        UK replay is archive-backed. Effects feeds and affecting act XMLs are
        loaded from the Farchive DB; deprecated on-disk XML fallbacks are
        intentionally not used.
        """
        if archive is None:
            raise ValueError(
                "UKReplayPipeline.compile_ops_for_statute requires archive-backed "
                "effects/XML; deprecated on-disk XML inputs have been removed"
            )

        # ── Load effects ────────────────────────────────────────────────────
        if effect_feed_parse_rejections_out is None:
            effects = load_effects_for_statute_from_archive(affected_act_id, archive)
        else:
            effects = load_effects_for_statute_from_archive(
                affected_act_id,
                archive,
                parse_rejections_out=effect_feed_parse_rejections_out,
            )

        replayable = list(effects)
        if pit_date:
            pit_replayable: list[UKEffectRecord] = []
            for e in replayable:
                effective_date = e.effective_date or "9999-99-99"
                if effective_date <= pit_date:
                    pit_replayable.append(e)
                    continue
                append_pit_date_filter_rejection(
                    effect_diagnostics_out,
                    effect=e,
                    effective_date=effective_date,
                    pit_date=pit_date,
                )
            replayable = pit_replayable

        replayable = _order_uk_effects_for_replay(
            replayable,
            diagnostics_out=effect_diagnostics_out,
            lowering_observations_out=lowering_rejections_out,
        )

        from lawvm.uk_legislation.source_adjudication import classify_uk_effect_source_pathology

        ops = []
        extraction_cache: dict[str, UKAffectingSourceContext] = {}
        enacted_extraction_cache: dict[str, UKAffectingSourceContext] = {}
        for i, e in enumerate(replayable):
            if bool(e.metadata_only) and not allow_metadata_only_effects:
                append_metadata_only_selection_rejection(
                    lowering_rejections_out,
                    effect=e,
                )
                continue
            source_required_for_replay = uk_effect_requires_affecting_source_for_replay(
                e,
                applicability_mode=applicability_mode,
            )

            if not source_required_for_replay:
                source_context, _parse_error = _build_affecting_source_context(
                    xml_bytes=None,
                    locator="",
                    authority_layer="EFFECT_FEED_INDEX",
                    provision_extractor=extract_provision_element_from_bytes,
                )
            elif e.affecting_act_id in extraction_cache:
                source_context = extraction_cache[e.affecting_act_id]
            else:
                current_locator = f"https://www.legislation.gov.uk/{e.affecting_act_id}/data.xml"
                source_context, parse_error = _build_affecting_source_context(
                    xml_bytes=get_affecting_act_xml_from_archive(e.affecting_act_id, archive),
                    locator=current_locator,
                    authority_layer="AFFECTING_ACT_TEXT",
                    provision_extractor=extract_provision_element_from_bytes,
                )
                _append_affecting_source_context_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    source_context=source_context,
                    parse_error=parse_error,
                )
                extraction_cache[e.affecting_act_id] = source_context
            el, source_extraction_observations = _extract_from_affecting_source_context_with_observations(
                source_context,
                e,
            )
            source_context, el, source_lane_observations = _select_enacted_source_for_current_shell(
                effect=e,
                archive=archive,
                current_context=source_context,
                current_el=el,
                enacted_context_cache=enacted_extraction_cache,
                enacted_xml_loader=get_affecting_act_enacted_xml_from_archive,
            )
            if effect_diagnostics_out is not None:
                effect_diagnostics_out.extend(source_extraction_observations)
                effect_diagnostics_out.extend(source_lane_observations)
            xml_bytes = source_context.xml_bytes
            root = source_context.root

            structural_for_replay = e.is_structural_for_replay(applicability_mode=applicability_mode)
            lowering_rejection_count_before = (
                len(lowering_rejections_out) if lowering_rejections_out is not None else 0
            )
            compiled = compile_effect_to_ir_ops(
                e,
                el,
                sequence=i,
                fallback_for_missing_extracted_source=(
                    source_required_for_replay
                    and xml_bytes is None
                    and allow_metadata_backfill
                ),
                lowering_rejections_out=lowering_rejections_out,
                source_root=root,
                source_authority_layer=source_context.authority_layer,
            )
            compile_recorded_lowering_rejection = (
                lowering_rejections_out is not None
                and len(lowering_rejections_out) > lowering_rejection_count_before
            )
            if lowering_rejections_out is not None:
                mark_nonreplay_lowering_rejections_nonblocking(
                    e,
                    structural_for_replay=structural_for_replay,
                    applicability_mode=applicability_mode,
                    lowering_rejections=lowering_rejections_out,
                    start_index=lowering_rejection_count_before,
                )
            extracted_tag = el.tag.rsplit("}", 1)[-1] if el is not None else None
            extracted_text = " ".join(t.strip() for t in el.itertext() if t and t.strip()) if el is not None else ""
            source_pathology = classify_uk_effect_source_pathology(
                extracted_tag=extracted_tag,
                extracted_text=extracted_text,
                op_actions=[_action_name(op.action) for op in compiled],
                payload_kinds=[str(op.payload.kind) for op in compiled if op.payload is not None],
                payload_texts=[op.payload.text or "" for op in compiled if op.payload is not None],
                target_paths=["/".join(f"{kind}:{label}" for kind, label in op.target.path) for op in compiled],
                lowering_rule_ids=[] if lowering_rejections_out is None else [
                    str(row.get("rule_id") or "")
                    for row in lowering_rejections_out[lowering_rejection_count_before:]
                ],
                effect_type=e.effect_type,
                is_structural=structural_for_replay,
            )
            append_source_pathology_classified_diagnostic(
                effect_diagnostics_out,
                effect=e,
                source_pathology=source_pathology,
                structural_for_replay=structural_for_replay,
                replay_applicable=e.is_applicable_for_replay(applicability_mode=applicability_mode),
                compiled_op_count=len(compiled),
            )

            if not compiled:
                append_no_ops_lowering_rejections(
                    e,
                    structural_for_replay=structural_for_replay,
                    lowering_rejections_out=lowering_rejections_out,
                    compile_recorded_lowering_rejection=compile_recorded_lowering_rejection,
                    applicability_mode=applicability_mode,
                )
                append_manual_compile_frontier_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    source_pathology=source_pathology,
                    extracted_tag=extracted_tag or "",
                    extracted_text=extracted_text,
                    lowering_rejections_out=lowering_rejections_out,
                    lowering_rejection_start_index=lowering_rejection_count_before,
                    compiled_op_count=0,
                    replay_applicable=e.is_applicable_for_replay(
                        applicability_mode=applicability_mode
                    ),
                    structural_for_replay=structural_for_replay,
                )
                continue
            source_pathology_filter_rejected = append_source_pathology_filter_lowering_rejections(
                e,
                source_pathology=source_pathology,
                structural_for_replay=structural_for_replay,
                compiled_ops=compiled,
                lowering_rejections_out=lowering_rejections_out,
            )
            append_manual_compile_frontier_diagnostic(
                effect_diagnostics_out,
                effect=e,
                source_pathology=source_pathology,
                extracted_tag=extracted_tag or "",
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
                lowering_rejection_start_index=lowering_rejection_count_before,
                compiled_op_count=len(compiled),
                replay_applicable=e.is_applicable_for_replay(
                    applicability_mode=applicability_mode
                ),
                structural_for_replay=structural_for_replay,
            )
            if source_pathology_filter_rejected:
                continue
            replay_applicable = e.is_applicable_for_replay(applicability_mode=applicability_mode)
            should_replay_compiled = structural_for_replay or should_replay_nonstructural_ops(
                e,
                compiled,
                applicability_mode=applicability_mode,
            )
            if not should_replay_compiled:
                append_replay_applicability_filter_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    compiled_ops=compiled,
                    structural_for_replay=structural_for_replay,
                    replay_applicable=replay_applicable,
                    applicability_mode=applicability_mode,
                )
                if authority_mode == "source_text_only" and authority_rejections_out is not None:
                    _, rejected_ops, rejected_reason_counts = _partition_uk_ops_by_authority_mode(
                        compiled,
                        authority_mode,
                    )
                    if rejected_ops:
                        authority_rejections_out.append(
                            _uk_authority_filter_diagnostic(
                                effect=e,
                                authority_mode=authority_mode,
                                compiled_op_count=len(compiled),
                                rejected_ops=rejected_ops,
                                rejected_reason_counts=rejected_reason_counts,
                                replay_applicable=replay_applicable,
                                structural_for_replay=structural_for_replay,
                                rule_id="uk_effect_authority_filter_non_applicable_observed",
                                blocking=False,
                                reason=(
                                    "UK source-text-only authority mode observed "
                                    "non-source-text operations on a non-replay-applicable effect"
                                ),
                            )
                        )
                continue
            if authority_mode == "source_text_only":
                kept_ops, rejected_ops, rejected_reason_counts = _partition_uk_ops_by_authority_mode(
                    compiled,
                    authority_mode,
                )
                if rejected_ops and authority_rejections_out is not None:
                    authority_rejections_out.append(
                        _uk_authority_filter_diagnostic(
                            effect=e,
                            authority_mode=authority_mode,
                            compiled_op_count=len(compiled),
                            rejected_ops=rejected_ops,
                            rejected_reason_counts=rejected_reason_counts,
                            replay_applicable=replay_applicable,
                            structural_for_replay=structural_for_replay,
                        )
                    )
                compiled = kept_ops
                if not compiled:
                    continue
            if should_replay_compiled:
                ops.extend(compiled)

        ops = _order_schedule_materialization_ops(ops)
        return _order_uk_text_patch_preimage_chains(
            ops,
            lowering_observations_out=lowering_rejections_out,
        )

    def apply_ops(
        self,
        base_ir: IRStatute,
        ops: list[LegalOperation],
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        allow_oracle_alignment: bool = True,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
        oracle_alignment_events_out: Optional[list[dict[str, Any]]] = None,
    ) -> IRStatute:
        executor = UKReplayExecutor(
            base_ir,
            eid_map=eid_map if allow_oracle_alignment else None,
            text_map=text_map if allow_oracle_alignment else None,
            verbose=verbose,
            lo_ops_out=lo_ops_out,
            adjudications_out=adjudications_out,
        )
        prepared_ops = _prepare_replay_uk_ops(
            ops,
            base_ir=base_ir,
            verbose=verbose,
            adjudications_out=adjudications_out,
        )
        for op in prepared_ops.accepted_ops:
            executor.apply_op(op)
        if allow_oracle_alignment and eid_map:
            executor.ground_ids()
        if oracle_alignment_events_out is not None:
            oracle_alignment_events_out.extend(dict(event) for event in executor.oracle_alignment_events)
        return executor.statute.to_irstatute()


# ---------------------------------------------------------------------------
# Replay Executor
# ---------------------------------------------------------------------------


class UKReplayExecutor:
    def __init__(
        self,
        statute: IRStatute,
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
    ):
        self.statute = UKMutableStatute.from_irstatute(statute)
        self.eid_map = eid_map or {}
        self.text_map = text_map or {}
        self.verbose = bool(verbose)
        self.lo_ops_out = lo_ops_out  # None = don't collect snapshots
        self.adjudications_out = adjudications_out
        self._seen_invariant_violations = self._collect_invariant_violations()
        self._repealed_target_prefixes: set[str] = set()
        self._applied_text_patch_targets: dict[str, list[str]] = {}
        self.oracle_alignment_events: list[dict[str, Any]] = []

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _replace_statute(
        self,
        *,
        body: Optional[UKMutableNode] = None,
        supplements: Optional[list[UKMutableNode]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Replace the UK-local mutable runtime state."""
        if body is not None:
            self.statute.body = body
        if supplements is not None:
            self.statute.supplements = list(supplements)
        if metadata is not None:
            self.statute.metadata = dict(metadata)

    def _find_path_to_node(
        self,
        root: UKMutableNode,
        target_node: UKMutableNode,
        path: tuple[int, ...] = (),
    ) -> Optional[tuple[int, ...]]:
        if root is target_node:
            return path
        for i, child in enumerate(root.children):
            found = self._find_path_to_node(child, target_node, path + (i,))
            if found is not None:
                return found
        return None

    def _replace_descendant_at_path(
        self,
        root: UKMutableNode,
        path: tuple[int, ...],
        new_node: UKMutableNode,
    ) -> UKMutableNode:
        if not path:
            return new_node
        idx = path[0]
        root.children[idx] = self._replace_descendant_at_path(root.children[idx], path[1:], new_node)
        return root

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool:
        if self.statute.body is old_node:
            self.statute.body = new_node
            return True
        body_path = self._find_path_to_node(self.statute.body, old_node)
        if body_path is not None:
            self._replace_descendant_at_path(self.statute.body, body_path, new_node)
            return True
        for idx, root in enumerate(self.statute.supplements):
            if root is old_node:
                self.statute.supplements[idx] = new_node
                return True
            sub_path = self._find_path_to_node(root, old_node)
            if sub_path is not None:
                self._replace_descendant_at_path(root, sub_path, new_node)
                return True
        return False

    def _replace_children(self, node: UKMutableNode, new_children: list[UKMutableNode]) -> bool:
        node.children = list(new_children)
        return True

    def _replace_text(self, node: UKMutableNode, new_text: str) -> bool:
        node.text = new_text
        return True

    def _replace_text_and_children(
        self,
        node: UKMutableNode,
        *,
        text: str,
        children: list[UKMutableNode],
    ) -> bool:
        node.text = text
        node.children = list(children)
        return True

    def _replace_attrs(self, node: UKMutableNode, attrs: dict[str, Any]) -> bool:
        node.attrs = dict(attrs)
        return True

    def _insert_table_column(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        node, _, _ = self._find_node_by_target(target)
        if node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-column insertion containing target.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "target_not_found",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        try:
            after_column_index = int(selector.get("after_column_index") or 0)
            before_column_index = int(selector.get("before_column_index") or 0)
        except (TypeError, ValueError):
            after_column_index = 0
            before_column_index = 0
        if after_column_index < 1 or before_column_index != after_column_index + 1:
            reason = "invalid_selector"
            detail: dict[str, Any] = {}
            table = None
        else:
            tables, carrier_detail = uk_table_selector_tables(node, selector)
            table = tables[0] if len(tables) == 1 else None
            reason = "" if table is not None else "table_not_unique"
            detail = {"table_count": len(tables), **carrier_detail} if table is None else carrier_detail
        payload_cells, payload_reason, payload_detail = uk_table_column_payload_cells(new_node)
        if table is None or payload_reason:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a source-owned table column insertion.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": payload_reason or reason,
                    **detail,
                    **payload_detail,
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False

        payload_index = 0
        adjusted_spans = 0
        inserted_cells = 0
        matched_rows: list[str] = []
        plans = uk_table_column_insert_plans(table)
        for plan in plans:
            row = cast(UKMutableNode, plan["row"])
            row_cells = cast(dict[int, UKMutableNode], plan["row_cells"])
            owned_ranges = cast(list[tuple[int, int, UKMutableNode, int]], plan["owned_ranges"])
            before_cell = row_cells.get(before_column_index)
            after_cell = row_cells.get(after_column_index)
            owned_spanners = [
                (start, end, cell, physical_index)
                for start, end, cell, physical_index in owned_ranges
                if start <= after_column_index and end >= before_column_index
            ]
            if owned_spanners:
                if len(owned_spanners) != 1:
                    reason = "column_boundary_span_ambiguous"
                    break
                _start, _end, spanner, _physical_index = owned_spanners[0]
                old_colspan_raw = str(spanner.attrs.get("colspan") or "1")
                if not re.fullmatch(r"[0-9]+", old_colspan_raw):
                    reason = "unsupported_colspan_value"
                    break
                old_colspan = int(old_colspan_raw)
                spanner.attrs = {**spanner.attrs, "colspan": str(old_colspan + 1)}
                adjusted_spans += 1
                matched_rows.append(str(spanner.text or "")[:160])
                continue
            if before_cell is not None and before_cell is after_cell:
                reason = "column_boundary_carried_span_unsupported"
                break
            if payload_index >= len(payload_cells):
                reason = "payload_row_count_too_small"
                break
            insertion_candidates = [
                physical_index
                for start, _end, _cell, physical_index in owned_ranges
                if start >= before_column_index
            ]
            if insertion_candidates:
                insert_index = min(insertion_candidates)
            elif row_cells and max(row_cells) == after_column_index:
                insert_index = len(row.children)
            else:
                reason = "column_boundary_not_found"
                break
            row.children[insert_index:insert_index] = [payload_cells[payload_index]]
            inserted_cells += 1
            payload_index += 1
            matched_rows.append(
                " | ".join(
                    str(row_cells[col].text or "")
                    for col in sorted(row_cells)
                    if str(row_cells[col].text or "")
                )[:160]
            )
        else:
            reason = ""
        if reason or payload_index != len(payload_cells):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not prove the table-column insertion boundary.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": reason or "payload_row_count_too_large",
                    "payload_row_count": len(payload_cells),
                    "payload_rows_consumed": payload_index,
                    "adjusted_spans": adjusted_spans,
                    "inserted_cells": inserted_cells,
                    "matched_rows": tuple(matched_rows[:5]),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_COLUMN_INSERT_RULE_ID,
            message=(
                "UK replay inserted a table column after resolving a source-owned "
                "between-columns selector."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "payload_row_count": len(payload_cells),
                "adjusted_spans": adjusted_spans,
                "inserted_cells": inserted_cells,
                "matched_rows": tuple(matched_rows[:5]),
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _insert_table_entry_row(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        node, _, _ = self._find_node_by_target(target)
        if node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-row insertion containing target.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "target_not_found",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        table, insert_index, reason, detail = resolve_uk_table_entry_row_insert_index(node, selector)
        if table is None or insert_index is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay could not resolve a unique source-owned table row "
                    "for table-row insertion."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": reason,
                    **detail,
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        payload_kind = _uk_kind_value(new_node.kind).lower()
        if payload_kind not in {"row", "table"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay table-row insertion payload was not a table row.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_not_row",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        inserted_rows = [new_node] if payload_kind == "row" else [
            child for child in new_node.children if _uk_kind_value(child.kind).lower() == "row"
        ]
        if not inserted_rows:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay table-row insertion payload had no table rows.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_has_no_rows",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        for row in inserted_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(table.children)
        children[insert_index:insert_index] = inserted_rows
        self._replace_children(table, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            message=(
                "UK replay inserted a table row after resolving an explicit "
                "table-entry selector."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "insert_index": insert_index,
                "inserted_row_count": len(inserted_rows),
                **detail,
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _apply_source_carried_table_cell_paragraph_substitution(
        self,
        cell: UKMutableNode,
        match_text: str,
        replacement: str,
    ) -> tuple[UKMutableNode, bool, str, dict[str, Any]]:
        match = _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(match_text)
        if match is None:
            return cell, False, "not_source_carried_table_cell_selector", {}
        text = cell.text or ""
        paragraph_label = _clean_num(match.group("paragraph"))
        subparagraph_label = _clean_num(match.group("subparagraph") or "")
        try:
            paragraph_index = int(paragraph_label) - 1
        except ValueError:
            return cell, False, "invalid_paragraph_label", {"source_paragraph_label": paragraph_label}
        parts = re.split(r"(\n{6,})", text)
        paragraph_slots = [index for index in range(0, len(parts), 2)]
        if paragraph_index < 0 or paragraph_index >= len(paragraph_slots):
            return cell, False, "paragraph_not_found", {
                "source_paragraph_label": paragraph_label,
                "paragraph_count": len(paragraph_slots),
            }
        slot = paragraph_slots[paragraph_index]
        old_paragraph = parts[slot]
        if not subparagraph_label:
            parts[slot] = replacement
            old_fragment = old_paragraph
        else:
            if len(subparagraph_label) != 1 or not subparagraph_label.isalpha():
                return cell, False, "unsupported_subparagraph_label", {
                    "source_paragraph_label": paragraph_label,
                    "source_subparagraph_label": subparagraph_label,
                }
            sub_index = ord(subparagraph_label.lower()) - ord("a")
            if sub_index < 0:
                return cell, False, "invalid_subparagraph_label", {
                    "source_paragraph_label": paragraph_label,
                    "source_subparagraph_label": subparagraph_label,
                }
            if sub_index == 0:
                sub_match = re.search(r"(?P<prefix>[—-]\s*\n+)(?P<old>.*?)(?=\n{4,}|$)", old_paragraph, re.S)
                if sub_match is None:
                    return cell, False, "subparagraph_not_found", {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                    }
                old_fragment = sub_match.group("old")
                parts[slot] = (
                    old_paragraph[: sub_match.start("old")]
                    + replacement
                    + old_paragraph[sub_match.end("old") :]
                )
            else:
                subparts = re.split(r"(\n{4,})", old_paragraph)
                sub_slots = [index for index in range(2, len(subparts), 2)]
                if sub_index - 1 < 0 or sub_index - 1 >= len(sub_slots):
                    return cell, False, "subparagraph_not_found", {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                        "subparagraph_count": len(sub_slots) + 1,
                    }
                sub_slot = sub_slots[sub_index - 1]
                old_fragment = subparts[sub_slot]
                subparts[sub_slot] = replacement
                parts[slot] = "".join(subparts)
        new_text = "".join(parts)
        if new_text == text:
            return cell, False, "replacement_noop", {
                "source_paragraph_label": paragraph_label,
                "source_subparagraph_label": subparagraph_label,
            }
        old_cell = cell
        cell = dc_replace(cell, text=new_text)
        self._replace_node_in_statute(old_cell, cell)
        return cell, True, "", {
            "source_paragraph_label": paragraph_label,
            "source_subparagraph_label": subparagraph_label,
            "old_fragment": " ".join(old_fragment.split())[:240],
            "replacement_fragment": " ".join(replacement.split())[:240],
        }

    def _resolve_table_entry_inline_cell(
        self,
        node: UKMutableNode,
        selector: dict[str, Any],
    ) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
        """Resolve a source-owned "nth entry in column N relating to X" table cell."""
        if str(selector.get("selector_mode") or "") == "unique_column_text":
            return resolve_unique_uk_table_column_text_cell(node, selector)
        if str(selector.get("selector_mode") or "") == "unique_relating_cell":
            return resolve_unique_uk_table_relating_cell(node, selector)
        if str(selector.get("selector_mode") or "") in {"unique_relating_text", "unique_entry_text"}:
            return resolve_unique_uk_table_entry_text_cell(node, selector)
        if str(selector.get("selector_mode") or "") == "unique_entry_cell":
            return resolve_unique_uk_table_entry_cell(node, selector)

        try:
            column_index = int(selector.get("column_index") or 0)
            entry_index = int(selector.get("entry_index") or 0)
        except (TypeError, ValueError):
            return None, "invalid_selector", {}
        relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
        if column_index < 1 or entry_index < 1 or not relating_norm:
            return None, "invalid_selector", {}

        tables, carrier_detail = uk_table_selector_tables(node, selector)
        if len(tables) != 1:
            return None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

        matching_cells: list[UKMutableNode] = []
        matching_rows: list[str] = []
        for row_cells in expanded_uk_table_rows(tables[0]):
            target_cell = row_cells.get(column_index)
            if target_cell is None:
                continue
            relation_cells = [
                cell
                for col, cell in sorted(row_cells.items())
                if col < column_index and _compact_normalized_text(cell.text or "").find(relating_norm) >= 0
            ]
            if not relation_cells:
                continue
            if not matching_cells or matching_cells[-1] is not target_cell:
                matching_cells.append(target_cell)
                matching_rows.append(
                    " | ".join(
                        str(row_cells[col].text or "")
                        for col in sorted(row_cells)
                        if str(row_cells[col].text or "")
                    )[:240]
                )
        if len(matching_cells) < entry_index:
            return None, "entry_not_found", {
                "matching_entry_count": len(matching_cells),
                "matching_rows": tuple(matching_rows[:5]),
                **carrier_detail,
            }
        return matching_cells[entry_index - 1], "", {
            "matching_entry_count": len(matching_cells),
            "matched_row": matching_rows[entry_index - 1] if entry_index - 1 < len(matching_rows) else "",
            **carrier_detail,
        }

    def _heading_facet_carrier_for_target(
        self,
        target: LegalAddress,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        *,
        allow_crossheading_parent: bool = False,
    ) -> Optional[UKMutableNode]:
        """Return the replay node whose text owns a UK heading facet target."""
        if target.special is not FacetKind.HEADING:
            return None
        node_kind = _uk_kind_value(node.kind).lower()
        if node_kind in {"part", "chapter", "schedule", "p1group", "pblock", "crossheading"} and node.text:
            return node
        direct_heading_children = [
            child for child in node.children if _uk_kind_value(child.kind).lower() == "heading" and child.text
        ]
        if len(direct_heading_children) == 1:
            return direct_heading_children[0]
        if parent is None or not parent.text:
            return None
        parent_kind = _uk_kind_value(parent.kind).lower()
        if parent_kind not in {"p1group", "pgroup", "crossheading"}:
            return None
        structural_children = [
            child
            for child in parent.children
            if _uk_kind_value(child.kind).lower()
            in {"section", "article", "rule", "regulation", "subsection", "paragraph", "subparagraph", "item"}
        ]
        if parent_kind in {"p1group", "pgroup"} and len(structural_children) == 1 and structural_children[0] is node:
            return parent
        if (
            parent_kind == "pgroup"
            and structural_children
            and structural_children[0] is node
            and str(parent.attrs.get("source_rule_id") or "")
            == "uk_parse_subordinate_pgroup_heading_carrier"
        ):
            return parent
        if allow_crossheading_parent and parent_kind == "crossheading":
            # Cross-heading refs say "heading before paragraph X". The carrier
            # is the crossheading parent only when X is the first structural
            # child under that heading; otherwise placement would be ambiguous.
            if structural_children and structural_children[0] is node:
                return parent
        return None

    def _apply_numeric_list_trailing_comma_anchor_on_node_text_only(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int,
    ) -> tuple[UKMutableNode, bool, str | None]:
        """Recover a unique numeric list item whose source selector adds a comma."""

        text = node.text or ""
        new_text, anchor = _numeric_list_trailing_comma_replacement_text(
            text,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if new_text is None or anchor is None:
            return node, False, None
        rebuilt = dc_replace(node, text=new_text)
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True, anchor

    def _apply_numeric_list_trailing_comma_anchor_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
    ) -> tuple[UKMutableNode, bool, str | None]:
        """Recover one unique numeric list item across a resolved target subtree."""

        subtree_replacement = _numeric_list_trailing_comma_subtree_replacement(
            node,
            match,
            replacement,
            occurrence,
            end_occurrence,
        )
        if subtree_replacement is None:
            return node, False, None
        path, new_text, anchor = subtree_replacement
        text_node = node
        for index in path:
            text_node = text_node.children[index]
        rebuilt = self._replace_descendant_at_path(
            node,
            path,
            dc_replace(
                text_node,
                text=new_text,
            ),
        )
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True, anchor

    def _apply_text_replace_on_node_text_only(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
        *,
        allow_punctuation_spacing: bool = False,
        allow_word_punctuation_elision: bool = False,
        recovery_rule_ids_out: Optional[list[str]] = None,
    ) -> tuple[UKMutableNode, bool]:
        """Apply a text patch only to one node's text, never to descendants."""
        text = node.text or ""
        if not text:
            return node, False
        if match == "TEXT_ALL":
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if match.startswith("TEXT_AFTER_") and match.endswith("_TO_END"):
            anchor = match[len("TEXT_AFTER_") : -len("_TO_END")]
            if not anchor:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(anchor), text))
            if len(literal_matches) >= ordinal:
                anchor_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                anchor_match = matches[ordinal - 1]
            joiner = (
                ""
                if text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
                or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                else " "
            )
            rebuilt = dc_replace(node, text=f"{text[: anchor_match.end()]}{joiner}{replacement}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if match.startswith("TEXT_FROM_") and match.endswith("_TO_END"):
            start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
            if not start_text:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(start_text), text))
            if len(literal_matches) >= ordinal:
                start_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                start_match = matches[ordinal - 1]
            rebuilt = dc_replace(node, text=f"{text[: start_match.start()]}{replacement}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if match.startswith("TEXT_FROM_") and "_TO_" in match:
            start_text, end_text = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
            if not start_text or not end_text:
                return node, False
            start_ordinal = occurrence if occurrence > 0 else 1
            end_ordinal = end_occurrence if end_occurrence > 0 else 0
            if occurrence > 0:
                start_matches, used_word_start = _range_anchor_matches(text, start_text)
            else:
                start_matches = list(re.finditer(re.escape(start_text), text))
                used_word_start = False
            if len(start_matches) >= start_ordinal:
                start_match = start_matches[start_ordinal - 1]
                if used_word_start and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append("uk_replay_text_range_anchor_word_boundary_normalized")
            else:
                start_pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                start_matches = list(re.finditer(start_pattern, text, flags=re.I | re.S))
                if len(start_matches) < start_ordinal:
                    return node, False
                start_match = start_matches[start_ordinal - 1]
            if end_ordinal:
                end_matches, used_word_end = _range_anchor_matches(text, end_text)
                if len(end_matches) >= end_ordinal:
                    end_match = end_matches[end_ordinal - 1]
                    if end_match.start() < start_match.end():
                        return node, False
                    end_end = end_match.end()
                    if used_word_end and recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append("uk_replay_text_range_anchor_word_boundary_normalized")
                else:
                    end_pattern = _text_patch_pattern(
                        end_text,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    end_matches = list(re.finditer(end_pattern, text, flags=re.I | re.S))
                    if len(end_matches) < end_ordinal:
                        return node, False
                    end_match = end_matches[end_ordinal - 1]
                    if end_match.start() < start_match.end():
                        return node, False
                    end_end = end_match.end()
            else:
                end_idx = text.find(end_text, start_match.end())
                if end_idx == -1:
                    return node, False
                end_end = end_idx + len(end_text)
            rebuilt = dc_replace(node, text=f"{text[: start_match.start()]}{replacement}{text[end_end:]}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if occurrence == -1:
            pos = text.rfind(match)
            if pos != -1:
                rebuilt = dc_replace(node, text=text[:pos] + replacement + text[pos + len(match) :])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            matches = list(re.finditer(pattern, text, flags=re.I))
            if not matches:
                return node, False
            last = matches[-1]
            rebuilt = dc_replace(node, text=text[: last.start()] + replacement + text[last.end() :])
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        if occurrence == 0:
            if match in text:
                rebuilt = dc_replace(node, text=text.replace(match, replacement))
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            new_text, count = re.subn(pattern, replacement, text, flags=re.I)
            if count == 0:
                return node, False
            rebuilt = dc_replace(node, text=new_text)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True
        start = 0
        seen = 0
        while True:
            pos = text.find(match, start)
            if pos == -1:
                break
            seen += 1
            if seen == occurrence:
                rebuilt = dc_replace(node, text=text[:pos] + replacement + text[pos + len(match) :])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            start = pos + len(match)
        pattern = _text_patch_pattern(
            match,
            allow_punctuation_spacing=allow_punctuation_spacing,
            allow_word_punctuation_elision=allow_word_punctuation_elision,
        )
        for idx, normalized_match in enumerate(re.finditer(pattern, text, flags=re.I), start=1):
            if idx == occurrence:
                rebuilt = dc_replace(
                    node,
                    text=text[: normalized_match.start()] + replacement + text[normalized_match.end() :],
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
        return node, False

    def _apply_text_append_on_node_text_only(
        self,
        node: UKMutableNode,
        insertion: str,
    ) -> tuple[UKMutableNode, bool]:
        """Append text only to one node's text, never to descendants."""
        text = node.text or ""
        if not insertion:
            return node, False
        joiner = (
            ""
            if not text
            or text.endswith((" ", "\t", "\n", "\r"))
            or insertion.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        rebuilt = dc_replace(node, text=f"{text}{joiner}{insertion}")
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True

    def _node_text_patch_preimage_present(
        self,
        node: UKMutableNode,
        match: str,
        occurrence: int,
        end_occurrence: int = 0,
    ) -> bool:
        """Preflight the simple node-local text patches used for multi-cell table edits."""
        if occurrence != 0 or end_occurrence != 0:
            return False
        text = node.text or ""
        if not text or not match:
            return False
        if match in text:
            return True
        pattern = _text_patch_pattern(match)
        return re.search(pattern, text, flags=re.I) is not None

    def _apply_text_append_on_subtree_text_end(
        self,
        node: UKMutableNode,
        insertion: str,
    ) -> tuple[UKMutableNode, bool]:
        """Append text at the target subtree end without flattening children."""
        if not insertion:
            return node, False
        if node.text or not node.children:
            return self._apply_text_append_on_node_text_only(node, insertion)

        text_nodes: list[tuple[tuple[int, ...], UKMutableNode]] = []

        def _collect(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
            if n.text:
                text_nodes.append((path, n))
            for i, child in enumerate(n.children):
                _collect(child, path + (i,))

        _collect(node)
        if not text_nodes:
            return node, False
        text_path, text_node = text_nodes[-1]
        text = text_node.text or ""
        joiner = (
            ""
            if not text
            or text.endswith((" ", "\t", "\n", "\r"))
            or insertion.startswith((" ", ",", ".", ";", ":", ")"))
            else " "
        )
        replacement_node = dc_replace(text_node, text=f"{text}{joiner}{insertion}")
        if not text_path:
            self._replace_node_in_statute(text_node, replacement_node)
            return replacement_node, True
        rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt, True

    def _remove_node(self, node: UKMutableNode, parent: Optional[UKMutableNode], idx: Optional[int]) -> bool:
        if parent is not None and idx is not None:
            parent.children.pop(idx)
            return True
        for s_idx, root in enumerate(self.statute.supplements):
            if root is node:
                self.statute.supplements.pop(s_idx)
                return True
        return False

    def _find_parent_tuple_for_node(
        self,
        target_node: UKMutableNode,
    ) -> tuple[Optional[UKMutableNode], Optional[int]]:
        def _walk(parent: UKMutableNode) -> tuple[Optional[UKMutableNode], Optional[int]]:
            for child_idx, child in enumerate(parent.children):
                if child is target_node:
                    return parent, child_idx
                found_parent, found_idx = _walk(child)
                if found_parent is not None:
                    return found_parent, found_idx
            return None, None

        if self.statute.body is target_node:
            return None, None
        found_parent, found_idx = _walk(self.statute.body)
        if found_parent is not None:
            return found_parent, found_idx
        for supplement in self.statute.supplements:
            if supplement is target_node:
                return None, None
            found_parent, found_idx = _walk(supplement)
            if found_parent is not None:
                return found_parent, found_idx
        return None, None

    def _repeal_crossheading_group(
        self,
        target: LegalAddress,
        node: UKMutableNode,
        parent: Optional[UKMutableNode],
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        """Delete a heading wrapper only when source and live shape prove sole ownership."""
        if str(selector.get("selector_mode") or "") != "structural_with_heading_above_repeal":
            reason_code = "invalid_selector"
            detail: dict[str, Any] = {"selector": dict(selector)}
        elif parent is None:
            reason_code = "target_has_no_heading_parent"
            detail = {"selector": dict(selector)}
        else:
            parent_kind = _uk_kind_value(parent.kind).lower()
            structural_children = [
                child
                for child in parent.children
                if _uk_kind_value(child.kind).lower()
                in {"section", "article", "rule", "regulation", "paragraph", "subparagraph", "item"}
            ]
            if parent_kind not in {"crossheading", "p1group", "pgroup", "pblock"}:
                reason_code = "parent_is_not_heading_wrapper"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
            elif not (parent.text or "").strip():
                reason_code = "heading_wrapper_has_no_heading_text"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
            elif len(structural_children) != 1 or structural_children[0] is not node:
                reason_code = "heading_wrapper_does_not_solely_own_target"
                detail = {
                    "parent_kind": parent_kind,
                    "structural_child_count": len(structural_children),
                    "selector": dict(selector),
                }
            else:
                grandparent, parent_idx = self._find_parent_tuple_for_node(parent)
                if self._remove_node(parent, grandparent, parent_idx):
                    self._record_repealed_target(target)
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_RESOLVED_RULE_ID,
                        message=(
                            "UK replay removed a cross-heading wrapper because "
                            "the source explicitly repealed the heading above "
                            "the target and the wrapper owned only that target."
                        ),
                        op=op,
                        detail={
                            "target": str(target),
                            "removed_parent_kind": parent_kind,
                            "removed_heading_preview": " ".join((parent.text or "").split())[:200],
                            "selector": dict(selector),
                        },
                    )
                    return True
                reason_code = "heading_wrapper_remove_failed"
                detail = {"parent_kind": parent_kind, "selector": dict(selector)}
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
            message=(
                "UK replay skipped cross-heading group repeal: source selector "
                "did not prove a unique heading wrapper solely owned by the target."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "reason_code": reason_code,
                **detail,
            },
        )
        return False

    def _insert_child_sorted(self, parent: UKMutableNode, new_node: UKMutableNode) -> bool:
        from lawvm.uk_legislation.canonicalize import uk_insert_into_children

        uk_insert_into_children(
            cast(list[IRNode], parent.children),
            cast(IRNode, new_node),
            label_sort_key=_label_sort_key,
        )
        return True

    def _insert_supplement_sorted(self, new_node: UKMutableNode) -> bool:
        from lawvm.uk_legislation.canonicalize import uk_insert_into_children

        uk_insert_into_children(
            cast(list[IRNode], self.statute.supplements),
            cast(IRNode, new_node),
            label_sort_key=_label_sort_key,
        )
        return True

    def _collect_invariant_violations(self) -> set[str]:
        violations: set[str] = set()
        targets: list[tuple[str, UKMutableNode]] = [("body", self.statute.body)]
        targets.extend((f"schedule:{schedule.label or '?'}", schedule) for schedule in self.statute.supplements)
        for root_name, node in targets:
            for violation in tree_ops.check_invariants(cast(IRNode, node)):
                if "duplicate " not in violation and " out of order:" not in violation:
                    continue
                violations.add(f"{root_name}:{violation}")
        return violations

    def _payload_shape_invariant_violations(self, op: LegalOperation) -> list[str]:
        payload = getattr(op, "payload", None)
        if payload is None or _action_name(op.action) not in {"insert", "replace"}:
            return []
        violations: list[str] = []
        for violation in tree_ops.check_invariants(payload):
            if "duplicate " not in violation and " out of order:" not in violation:
                continue
            violations.append(violation)
        return violations

    def _payload_container_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "duplicate part:" not in scoped_violation.lower():
            return False
        payload = getattr(op, "payload", None)
        if payload is None or _action_name(op.action) != "replace":
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "part":
            return False
        payload_kind = str(getattr(payload, "kind", "") or "").lower()
        payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
        return payload_kind == "part" and payload_label in {"", "part"}

    def _repeated_form_label_payload_shape_gap(self, op: LegalOperation, payload_violations: list[str]) -> bool:
        payload = getattr(op, "payload", None)
        if payload is None or _action_name(op.action) != "insert":
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if len(target_path) != 1 or str(target_path[0][0] or "").lower() != "schedule":
            return False
        if str(getattr(payload, "kind", "") or "").lower() != "schedule":
            return False
        if not payload_violations:
            return False
        allowed = (
            "duplicate item:",
            "item out of order:",
        )
        return all(any(token in violation.lower() for token in allowed) for violation in payload_violations)

    def _part_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "part out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        part_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "part"]
        if not part_labels:
            return False
        leaf_kind = "part"
        leaf_text = _clean_num(part_labels[-1])
        violation = str(scoped_violation or "")
        match = re.search(r"part out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)

        def normalize(text: str) -> str:
            return re.sub(r"^(?:part)\s*", "", text.strip(), flags=re.I)

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", normalize(text)))

        def roman(text: str) -> bool:
            return bool(re.fullmatch(r"(?:part)?[ivxlcdm]+", text, re.I))

        schedule_labels = [
            _clean_num(str(label or "")) for kind, label in target_path if str(kind or "").lower() == "schedule"
        ]
        if (
            str(leaf_kind or "").lower() == "part"
            and schedule_labels
            and any(re.fullmatch(r"\d+[a-z]+", label, re.I) for label in schedule_labels if label)
        ):
            return True
        if re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", leaf_text):
            return True
        if match is None:
            return False
        left = _clean_num(normalize(match.group(1)))
        right = _clean_num(normalize(match.group(2)))
        return (numeric(left) and roman(right)) or (roman(left) and numeric(right))

    def _chapter_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "chapter out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "chapter":
            return False
        violation = str(scoped_violation or "")
        match = re.search(r"chapter out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False

        def normalize(text: str) -> str:
            return re.sub(r"^(?:chapter)\s*", "", text.strip(), flags=re.I)

        left = _clean_num(normalize(match.group(1)))
        right = _clean_num(normalize(match.group(2)))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", text, re.I))

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        return (
            (numeric(left) and mixed(right))
            or (mixed(left) and numeric(right))
            or (mixed(left) and mixed(right))
            or left == right
        )

    def _section_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "section out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "section":
            return False
        leaf_text = _clean_num(str(target_path[-1][1] or ""))
        violation = str(scoped_violation or "")

        def mixed(text: str) -> bool:
            return bool(
                re.fullmatch(
                    r"(?:\d+[a-z]+\d+[a-z0-9]*|\d+[a-z]{2,}|\d+[a-z]\d[a-z0-9]*|[a-z]+\d+[a-z0-9]*)", text, re.I
                )
            )

        if mixed(leaf_text):
            return True
        if leaf_text and not re.fullmatch(r"\d+[a-z]*", leaf_text, re.I):
            return True
        match = re.search(r"section out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))

        def numeric(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        return (numeric(left) and mixed(right)) or (mixed(left) and numeric(right)) or (mixed(left) and mixed(right))

    def _source_anchored_order_observation(self, op: LegalOperation, scoped_violation: str) -> bool:
        if _action_name(op.action) != "insert":
            return False
        if " out of order:" not in str(scoped_violation or "").lower():
            return False
        witness = _witness_for_op(op)
        insertion_anchor_witness = getattr(witness, "insertion_anchor_witness", None)
        if insertion_anchor_witness is None:
            return False
        if not (
            getattr(insertion_anchor_witness, "preceding_eid", None)
            or getattr(insertion_anchor_witness, "following_eid", None)
        ):
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        target_kind = str(target_path[-1][0] or "").lower()
        target_label = _clean_num(str(target_path[-1][1] or ""))
        if not target_kind or not target_label:
            return False
        if f"{target_kind} out of order:" not in str(scoped_violation or "").lower():
            return False
        return target_label in _clean_num(scoped_violation)

    def _paragraph_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "paragraph out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        paragraph_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "paragraph"]
        if not paragraph_labels:
            return False
        leaf_text = _clean_num(paragraph_labels[-1])

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*)", text, re.I))

        def pure_alpha(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]+", text, re.I))

        def pure_num(text: str) -> bool:
            return bool(re.fullmatch(r"\d+", text))

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"paragraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return (
            (mixed(left) and pure_alpha(right))
            or (pure_alpha(left) and mixed(right))
            or (mixed(left) and pure_num(right))
            or (pure_num(left) and mixed(right))
            or (mixed(left) and mixed(right))
            or (pure_num(left) and pure_alpha(right))
            or (pure_alpha(left) and pure_num(right))
            or (alpha_suffix(left) and pure_alpha(right))
            or (pure_alpha(left) and alpha_suffix(right))
            or (pure_roman(left) and pure_alpha(right))
            or (pure_alpha(left) and pure_roman(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
        )

    def _subparagraph_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "subparagraph out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() != "subparagraph":
            return False
        leaf_text = _clean_num(str(target_path[-1][1] or ""))

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*|[ivxlcdm]+[a-z]+)", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"subparagraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return bool(
            (mixed(left) and pure_roman(right))
            or (pure_roman(left) and mixed(right))
            or (re.fullmatch(r"\d+", left) and mixed(right))
            or (mixed(left) and re.fullmatch(r"\d+", right))
            or (mixed(left) and mixed(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
            or (alpha_suffix(left) and alpha_suffix(right))
            or (re.fullmatch(r"\d+", left) and alpha_suffix(right))
            or (alpha_suffix(left) and re.fullmatch(r"\d+", right))
        )

    def _item_order_shape_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if "item out of order:" not in scoped_violation.lower():
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path or str(target_path[-1][0] or "").lower() not in {"subparagraph", "item", "point"}:
            return False
        in_schedule = any(str(kind or "").lower() == "schedule" for kind, _ in target_path)
        raw_leaf_text = str(target_path[-1][1] or "").strip().lower()
        leaf_text = _clean_num(raw_leaf_text)

        def pure_roman(text: str) -> bool:
            return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))

        def pure_alpha(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]+", text, re.I))

        def pure_alpha_single(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]", text, re.I))

        def alpha_suffix(text: str) -> bool:
            return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))

        def mixed(text: str) -> bool:
            return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*|[ivxlcdm]+[a-z]+)", text, re.I))

        if mixed(leaf_text) or alpha_suffix(leaf_text):
            return True
        violation = str(scoped_violation or "")
        match = re.search(r"item out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
        if match is None:
            return False
        raw_left = str(match.group(1) or "").strip().lower()
        raw_right = str(match.group(2) or "").strip().lower()
        left = _clean_num(match.group(1))
        right = _clean_num(match.group(2))
        return bool(
            (mixed(left) and pure_roman(right))
            or (pure_roman(left) and mixed(right))
            or (re.fullmatch(r"\d+", left) and mixed(right))
            or (mixed(left) and re.fullmatch(r"\d+", right))
            or (mixed(left) and mixed(right))
            or (alpha_suffix(left) and pure_alpha(right))
            or (pure_alpha(left) and alpha_suffix(right))
            or (alpha_suffix(left) and pure_roman(right))
            or (pure_roman(left) and alpha_suffix(right))
            or (alpha_suffix(left) and alpha_suffix(right))
            or (
                in_schedule
                and pure_alpha_single(raw_leaf_text)
                and pure_alpha_single(raw_left)
                and pure_alpha_single(raw_right)
            )
        )

    def _replace_payload_kind_mismatch_gap(self, op: LegalOperation, scoped_violation: str) -> bool:
        if _action_name(op.action) != "replace" or op.payload is None:
            return False
        target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
        if not target_path:
            return False
        target_kind = str(target_path[-1][0] or "").lower()
        payload_kind = str(getattr(op.payload, "kind", "") or "").lower()
        if payload_kind == target_kind:
            return False
        return (
            (
                target_kind == "subsection"
                and payload_kind == "paragraph"
                and "paragraph out of order:" in scoped_violation.lower()
            )
            or (
                target_kind == "paragraph"
                and payload_kind == "subparagraph"
                and "subparagraph out of order:" in scoped_violation.lower()
            )
            or (
                target_kind in {"subparagraph", "item", "point"}
                and payload_kind in {"item", "point"}
                and "duplicate " in scoped_violation.lower()
            )
        )

    def _record_invariant_violations(self, op: LegalOperation) -> None:
        current_violations = self._collect_invariant_violations()
        payload_shape_violations = self._payload_shape_invariant_violations(op)
        for scoped_violation in sorted(current_violations - self._seen_invariant_violations):
            if payload_shape_violations and self._repeated_form_label_payload_shape_gap(
                op, payload_shape_violations
            ):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repeated_form_label_payload_shape_gap",
                    message=(
                        "UK replay applied an inserted schedule payload whose form-like source "
                        "structure repeats local item labels under the same paragraph."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "payload_violations": "; ".join(payload_shape_violations),
                    },
                )
            elif payload_shape_violations or self._payload_container_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_shape_gap",
                    message="UK replay applied a payload that already violated order/duplication tree invariants.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "payload_violations": "; ".join(payload_shape_violations),
                    },
                )
            elif self._replace_payload_kind_mismatch_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_replace_payload_target_leaf_mismatch_gap",
                    message="UK replay hit an invariant because the replace payload kind does not match the lowered target leaf.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "payload_kind": str(getattr(op.payload, "kind", "")) if op.payload is not None else "",
                    },
                )
            elif self._part_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_part_order_shape_gap",
                    message="UK replay hit a mixed-label part ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._chapter_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_chapter_order_shape_gap",
                    message="UK replay hit a mixed-label chapter ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._source_anchored_order_observation(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_source_anchored_order_observed",
                    message=(
                        "UK replay retained explicit source insertion order even though the "
                        "generic label-order invariant would sort the inserted label elsewhere."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    },
                )
            elif self._section_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_section_order_shape_gap",
                    message="UK replay hit an alphanumeric section ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._paragraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_paragraph_order_shape_gap",
                    message="UK replay hit a mixed-label paragraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._subparagraph_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subparagraph_order_shape_gap",
                    message="UK replay hit a mixed-label subparagraph ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            elif self._item_order_shape_gap(op, scoped_violation):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_item_order_shape_gap",
                    message="UK replay hit a mixed-label item ordering seam that is not yet canonically ordered.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_tree_invariant_violation",
                    message="UK replay violated order/duplication tree invariant after applying an op.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(op.target),
                        "violation": scoped_violation,
                    },
                )
        self._seen_invariant_violations = current_violations

    def _record_repealed_target(self, target: LegalAddress) -> None:
        target_text = str(target or "").strip()
        if target_text:
            self._repealed_target_prefixes.add(target_text)

    def _target_under_repealed_prefix(self, target: LegalAddress) -> bool:
        target_text = str(target or "").strip()
        if not target_text:
            return False
        for prefix in self._repealed_target_prefixes:
            if target_text == prefix or target_text.startswith(prefix + "/"):
                return True
        return False

    def _table_target_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        return any(_clean_num(label or "") == "table" for _, label in path)

    def _broad_schedule_table_shape_gap(self, target: LegalAddress, node: UKMutableNode) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or not path:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"schedule", "part"}:
            return False
        node_kind = str(getattr(node, "kind", "") or "").lower()
        if node_kind not in {"schedule", "part"}:
            return False
        descendant_kinds: set[str] = set()
        stack = list(getattr(node, "children", []) or [])
        while stack:
            curr = stack.pop()
            curr_kind = str(getattr(curr, "kind", "") or "").lower()
            descendant_kinds.add(curr_kind)
            stack.extend(list(getattr(curr, "children", []) or []))
        if descendant_kinds & {"table", "row", "cell", "header_cell"}:
            return False
        provision_kinds = {"paragraph", "subparagraph", "item", "point", "p1group", "section"}
        return not bool(descendant_kinds & provision_kinds)

    def _schedule_unlabeled_paragraph_target_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        root_kind, root_label = path[0]
        if str(root_kind or "").lower() != "schedule":
            return False
        paragraph_segments = [
            re.sub(r"[^0-9a-z]+", "", str(label or "").lower())
            for kind, label in path
            if str(kind or "").lower() == "paragraph"
        ]
        if not paragraph_segments or not any(label.isdigit() for label in paragraph_segments if label):
            return False
        want = _clean_num(root_label or "")
        root_node = None
        for schedule in getattr(self.statute, "supplements", []) or []:
            if str(getattr(schedule, "kind", "") or "").lower() != "schedule":
                continue
            have = _clean_num(getattr(schedule, "label", "") or "")
            if have == want or have.endswith(want):
                root_node = schedule
                break
        if root_node is None:
            return False
        paragraph_labels: list[str] = []
        subparagraph_labels: list[str] = []
        stack = list(getattr(root_node, "children", []) or [])
        while stack:
            curr = stack.pop()
            curr_kind = str(getattr(curr, "kind", "") or "").lower()
            if curr_kind == "paragraph":
                paragraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            elif curr_kind == "subparagraph":
                subparagraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            stack.extend(list(getattr(curr, "children", []) or []))
        leaf_kind = str(path[-1][0] or "").lower()
        return (
            bool(paragraph_labels)
            and not any(paragraph_labels)
            and bool(subparagraph_labels)
            and leaf_kind
            in {
                "subparagraph",
                "item",
                "point",
            }
        )

    def _malformed_target_gap(self, target: LegalAddress) -> bool:
        def _descendant_labels(node: UKMutableNode, *, kinds: set[str]) -> list[str]:
            out: list[str] = []
            stack = list(getattr(node, "children", []) or [])
            while stack:
                curr = stack.pop()
                curr_kind = str(getattr(curr, "kind", "") or "").lower()
                if curr_kind in kinds:
                    out.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
                stack.extend(list(getattr(curr, "children", []) or []))
            return out

        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        if any(
            str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
            and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
            for kind, label in path
        ):
            return True
        if any(_clean_num(label or "").lower() == "note" for _, label in path):
            return True
        if any(
            re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
            for _, label in path
        ):
            return True
        if self._malformed_target_sectionlike_label_gap(target):
            return True
        if _addr_container(target) == "schedule":
            first_kind, first_label = path[0]
            if first_kind == "schedule" and not _clean_num(first_label or ""):
                return True
        if len(path) >= 2:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            leaf_kind, leaf_label = path[-1]
            textual_leaf = re.sub(r"[^0-9a-z]+", "", str(leaf_label or "").lower())
            is_roman = bool(re.fullmatch(r"[ivxlcdm]+", textual_leaf))
            is_alpha = bool(re.fullmatch(r"[a-z]+", textual_leaf))
            if (
                len(path) >= 2
                and str(path[-2][0] or "").lower() == "subsection"
                and re.fullmatch(r"[a-z]+", str(path[-2][1] or "").strip().lower())
                and str(path[-1][0] or "").lower() == "paragraph"
                and is_roman
            ):
                return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_roman
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    for grandchild in getattr(child, "children", []) or []:
                        if str(getattr(grandchild, "kind", "") or "").lower() not in {"subparagraph", "item", "point"}:
                            continue
                        grandchild_label = re.sub(
                            r"[^0-9a-z]+",
                            "",
                            str(getattr(grandchild, "label", "") or "").lower(),
                        )
                        if grandchild_label == textual_leaf:
                            return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
                if child_labels and all(re.fullmatch(r"\d+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"item", "point"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and textual_leaf.isdigit()
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and is_alpha
                and len(textual_leaf) > 1
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                if textual_leaf[:1] in child_labels:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
                and is_alpha
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subparagraph"
                ]
                if (
                    child_kinds
                    and child_kinds <= {"subparagraph"}
                    and child_labels
                    and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label)
                ):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_alpha
                and len(textual_leaf) > 1
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                first = textual_leaf[:1]
                rest = textual_leaf[1:]
                if rest and first in child_labels:
                    return True
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != first:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if rest and rest in descendant_labels:
                        return True
                last = textual_leaf[-1:]
                prefix = textual_leaf[:-1]
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != last:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if prefix and prefix in descendant_labels:
                        return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and any(label == "" for label in child_labels):
                    return True
                if any(re.fullmatch(rf"{re.escape(textual_leaf)}[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if "part" in child_kinds:
                    return True
                if re.fullmatch(r"[a-z]+\d+", textual_leaf):
                    paragraph_labels = [
                        label for label in _descendant_labels(parent_node, kinds={"paragraph"}) if label
                    ]
                    if paragraph_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in paragraph_labels):
                        return True
            if self._schedule_unlabeled_paragraph_target_gap(target):
                return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() in {"part", "chapter", "division"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() in {"part", "chapter", "division"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and re.fullmatch(r"\d+[a-z]{2,}", textual_leaf)
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and len(path) == 2
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"part", "chapter", "division", "crossheading", "pblock"}:
                    return True
        return any(_clean_num(label or "") == "and" for _, label in path)

    def _malformed_target_placeholder_label_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        return any(
            str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
            and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
            for kind, label in path
        )

    def _malformed_target_note_or_crossheading_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if any(_clean_num(label or "").lower() == "note" for _, label in path):
            return True
        return any(
            re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
            for _, label in path
        )

    def _malformed_target_sectionlike_label_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        root_kind, root_label = path[0]
        if str(root_kind or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        normalized = re.sub(r"[^0-9a-z]+", "", str(root_label or "").strip().lower())
        if not normalized:
            return True
        if any(ch.isdigit() for ch in normalized):
            return False
        return True

    def _malformed_target_schedule_root_label_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or not path:
            return False
        first_kind, first_label = path[0]
        return str(first_kind or "").lower() == "schedule" and not _clean_num(first_label or "")

    def _schedule_partition_target_gap(self, target: LegalAddress) -> bool:
        return bool(self._schedule_partition_target_gap_kind(target))

    def _schedule_partition_target_gap_kind(self, target: LegalAddress) -> str | None:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) != 2:
            return None
        leaf_kind, _ = path[-1]
        if str(leaf_kind or "").lower() != "paragraph":
            return None
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() != "schedule":
            return None
        child_kinds = {
            str(getattr(child, "kind", "") or "").lower()
            for child in getattr(parent_node, "children", []) or []
        }
        if "part" in child_kinds:
            return "uk_replay_schedule_partition_part_target_gap"
        if child_kinds & {"chapter", "division"}:
            return "uk_replay_schedule_partition_target_gap"
        return None

    def _malformed_target_gap_kind(self, target: LegalAddress) -> str:
        if self._malformed_target_placeholder_label_gap(target):
            return "uk_replay_malformed_target_placeholder_label_gap"
        if self._malformed_target_note_or_crossheading_gap(target):
            return "uk_replay_malformed_target_note_or_crossheading_gap"
        if self._schedule_unlabeled_paragraph_target_gap(target):
            return "uk_replay_schedule_unlabeled_paragraph_target_gap"
        partition_kind = self._schedule_partition_target_gap_kind(target)
        if partition_kind is not None:
            return partition_kind
        if self._malformed_target_sectionlike_label_gap(target):
            return "uk_replay_malformed_target_sectionlike_label_gap"
        if self._malformed_target_schedule_root_label_gap(target):
            return "uk_replay_malformed_target_schedule_root_label_gap"
        if self._malformed_target_gap(target):
            return "uk_replay_malformed_target_granularity_collapse_gap"
        return "uk_replay_malformed_target_gap"

    def _missing_source_target_gap(self, op: LegalOperation) -> bool:
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        authority_layer = str(getattr(extraction, "authority_layer", "") or "")
        extraction_failure_kind = str(getattr(extraction, "extraction_failure_kind", "") or "")
        extracted_source_present = bool(getattr(extraction, "extracted_source_present", False))
        return (
            authority_layer == "EFFECT_FEED_INDEX"
            and not extracted_source_present
            and extraction_failure_kind == "missing_extracted_source"
        )

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        return not bool(getattr(parent_node, "children", []) or [])

    def _recover_text_patch_on_empty_descendant_parent(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._empty_descendant_shape_gap(target):
            return False
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"paragraph", "subparagraph", "item", "point"}:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or getattr(parent_node, "children", None):
            return False
        match_text = text_patch.selector.match_text
        if not _node_text_contains_text(parent_node, match_text):
            return False
        rebuilt, applied = self._apply_text_replace_on_node_text_only(
            parent_node,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace empty-descendant parent recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_empty_descendant_parent_text_recovered",
            message=(
                "UK replay applied a text patch to an empty parent because the "
                "source-targeted descendant is not represented as a structural carrier."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "recovery_target": str(parent_target),
                "text_match": match_text,
                "replacement_text": replacement,
                "family": "target_resolution_recovery",
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return True

    def _implicit_first_subparagraph_parent_text_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        leaf_label = _clean_num(str(path[-1][1] or ""))
        if leaf_kind != "subparagraph" or leaf_label != "1":
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or _uk_kind_value(parent_node.kind).lower() != "paragraph":
            return False
        for child in getattr(parent_node, "children", []) or []:
            child_kind = _uk_kind_value(child.kind).lower()
            child_label = _clean_num(str(child.label or ""))
            if child_kind == "subparagraph" and child_label == "1":
                return False
        return bool(parent_node.text or "")

    def _recover_text_patch_on_implicit_first_subparagraph_parent_text(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._implicit_first_subparagraph_parent_text_gap(target):
            return False
        parent_target = LegalAddress(path=tuple(target.path[:-1]), special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        match_text = text_patch.selector.match_text
        if not _node_text_contains_text(parent_node, match_text):
            return False
        rebuilt, applied = self._apply_text_replace_on_node_text_only(
            parent_node,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace implicit first-subparagraph parent-text recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_implicit_first_subparagraph_parent_text_recovered",
            message=(
                "UK replay applied a text patch to the paragraph intro text because "
                "the source-targeted first subparagraph is represented as parent text "
                "rather than a structural child."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "recovery_target": str(parent_target),
                "text_match": match_text,
                "replacement_text": replacement,
                "family": "target_resolution_recovery",
                "source_shape": "implicit_first_subparagraph_parent_text",
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return True

    def _source_following_anchor_structured_substitution_anchor(self, op: LegalOperation) -> str:
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        source_text = str(getattr(extraction, "extracted_text", "") or getattr(op.source, "raw_text", "") or "")
        if not source_text:
            return ""
        match = _SOURCE_FOLLOWING_ANCHOR_STRUCTURED_SUBSTITUTION_RE.search(source_text)
        if match is None:
            return ""
        return " ".join(match.group("anchor").split()).strip()

    def _recover_source_carried_structured_tail_substitution(
        self,
        op: LegalOperation,
        target: LegalAddress,
        new_node: UKMutableNode,
    ) -> bool:
        anchor = self._source_following_anchor_structured_substitution_anchor(op)
        if not anchor:
            return False
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        leaf_label = _clean_num(str(path[-1][1] or ""))
        if leaf_kind not in {"paragraph", "subparagraph", "item", "point"} or not leaf_label:
            return False
        if not uk_kind_matches(
            node_kind=str(new_node.kind),
            target_kind=leaf_kind,
            node_label=_clean_num(new_node.label or ""),
            target_label=leaf_label,
        ):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        for child in getattr(parent_node, "children", []) or []:
            if str(child.kind).lower() == leaf_kind and _clean_num(str(child.label or "")) == leaf_label:
                return False

        parent_had_children = bool(getattr(parent_node, "children", []) or [])
        parent_tail_trimmed = False
        if not parent_had_children:
            parent_node, parent_tail_trimmed = self._apply_text_replace_on_node_text_only(
                parent_node,
                f"TEXT_AFTER_{anchor}_TO_END",
                "",
                occurrence=0,
            )
            if not parent_tail_trimmed:
                return False
            trimmed_parent_text = (parent_node.text or "").rstrip()
            if trimmed_parent_text != (parent_node.text or ""):
                old_parent_node = parent_node
                parent_node = dc_replace(parent_node, text=trimmed_parent_text)
                self._replace_node_in_statute(old_parent_node, parent_node)

        if not str(new_node.attrs.get("eId") or new_node.attrs.get("id") or ""):
            new_node.attrs["eId"] = self._derive_target_eid(target)
        self._insert_child_sorted(parent_node, new_node)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_source_carried_structured_tail_substitution_recovered",
            message=(
                "UK replay materialized a source-carried structured substitution: "
                "the affecting text replaces the words after a quoted parent anchor "
                "with explicit child provisions."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "recovery_target": str(parent_target),
                "source_anchor": anchor,
                "payload_kind": str(new_node.kind),
                "payload_label": str(new_node.label or ""),
                "parent_had_children_before": parent_had_children,
                "parent_tail_trimmed": parent_tail_trimmed,
                "family": "source_carried_structured_tail_substitution",
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return True

    def _recover_source_carried_labeled_child_text_substitution(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if text_patch.kind is not TextPatchKindEnum.REPLACE:
            return False
        if text_patch.selector.end_occurrence:
            return False
        if (
            "uk_effect_source_carried_quoted_text_substitution_text_patch"
            not in _text_rewrite_rule_ids_for_op(op)
        ):
            return False
        if target.special is not None:
            return False
        parent_kind = _addr_leaf_kind(target) or ""
        child_kind, parts = _source_carried_labeled_child_replacement_parts(
            replacement,
            parent_kind=parent_kind,
        )
        if not child_kind or not parts:
            return False
        if getattr(node, "children", None):
            return False
        text = node.text or ""
        if not text:
            return False
        match_text = text_patch.selector.match_text
        if not match_text or match_text.startswith("TEXT_"):
            return False

        ordinal = text_patch.selector.occurrence if text_patch.selector.occurrence > 0 else 1

        def _find_span(pattern: str, *, flags: int = 0) -> tuple[int, int] | None:
            matches = list(re.finditer(pattern, text, flags=flags))
            if text_patch.selector.occurrence == 0 and len(matches) != 1:
                return None
            if len(matches) < ordinal:
                return None
            selected = matches[ordinal - 1]
            return selected.start(), selected.end()

        literal_span = _find_span(re.escape(match_text))
        span = literal_span
        if span is None:
            span = _find_span(
                _text_patch_pattern(match_text, allow_punctuation_spacing=True),
                flags=re.I | re.S,
            )
        if span is None and _text_match_has_word_punctuation_elision_candidate(match_text):
            span = _find_span(
                _text_patch_pattern(match_text, allow_word_punctuation_elision=True),
                flags=re.I | re.S,
            )
        if span is None:
            return False

        before = text[: span[0]].rstrip()
        after = text[span[1] :].strip()
        # Do not smuggle unrelated parent-tail text into a child-materialization recovery.
        if after and not re.fullmatch(r"[\.,;:]+", after):
            return False
        rebuilt_text = before.rstrip(" ,;:")
        parent_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
        children: list[UKMutableNode] = []
        for label, child_text in parts:
            child_target = LegalAddress(path=(*tuple(target.path), (child_kind, label)), special=None)
            child_eid = self._derive_target_eid(child_target)
            attrs = {"source_rule_id": _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID}
            if child_eid:
                attrs["eId"] = child_eid
            elif parent_eid:
                attrs["eId"] = f"{parent_eid}-{label}"
            children.append(
                UKMutableNode(
                    kind=IRNodeKind(child_kind),
                    label=label,
                    text=child_text,
                    attrs=attrs,
                )
            )
        if not children:
            return False

        self._replace_text_and_children(node, text=rebuilt_text, children=children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID,
            message=(
                "UK replay materialized visible labelled child provisions from a "
                "source-carried quoted substitution payload."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "text_match": match_text,
                "replacement_text": replacement,
                "child_kind": child_kind,
                "child_labels": tuple(label for label, _ in parts),
                "family": "source_carried_labeled_child_text_substitution",
                "source_shape": "flat_replacement_payload_with_visible_child_labels",
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return True

    def _annex_schedule_mismatch_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if "annex" not in original_ref.lower():
            for note in getattr(op, "provenance_tags", []) or []:
                if str(note or "").startswith("original_ref:") and "annex" in str(note or "").lower():
                    original_ref = str(note or "")
                    break
        if "annex" not in original_ref.lower():
            return False
        if target is None:
            return False
        node, _, _ = self._find_node_by_target(cast(LegalAddress, target))
        return node is None

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        if self._schedule_paragraph_carrier_gap(target):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        return parent_node is None

    def _missing_parent_grandparent_present_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 3:
            return False
        if not self._missing_parent_shape_gap(target):
            return False
        grandparent_target = LegalAddress(path=path[:-2], special=None)
        grandparent_node, _, _ = self._find_node_by_target(grandparent_target)
        return grandparent_node is not None

    def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str:
        if self._missing_parent_grandparent_present_gap(target):
            return "uk_replay_missing_parent_grandparent_present_gap"
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) == 2:
            return "uk_replay_missing_root_parent_shape_gap"
        return "uk_replay_missing_parent_shape_gap"

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        if not any(str(kind or "").lower() == "paragraph" for kind, _ in path):
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"subparagraph", "item", "point"}:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is not None and str(getattr(parent_node, "kind", "") or "").lower() == "p1group":
            return True
        grandparent_target = LegalAddress(path=path[:-2], special=None)
        grandparent_node, _, _ = self._find_node_by_target(grandparent_target)
        return grandparent_node is not None and parent_node is None

    def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) >= 2:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            if parent_node is not None and str(getattr(parent_node, "kind", "") or "").lower() == "p1group":
                return "uk_replay_schedule_p1group_wrapper_carrier_gap"
        return "uk_replay_schedule_paragraph_carrier_gap"

    def _direct_section_paragraph_carrier_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if str(path[0][0] or "").lower() != "section" or str(path[1][0] or "").lower() != "paragraph":
            return False
        label = re.sub(r"[^0-9a-z]+", "", str(path[1][1] or "").lower())
        if not re.fullmatch(r"[a-z]", label):
            return False
        parent_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() not in {
            "section",
            "article",
            "rule",
            "regulation",
        }:
            return False
        child_kinds = {
            str(getattr(child, "kind", "") or "").lower()
            for child in getattr(parent_node, "children", []) or []
        }
        return bool(child_kinds and "paragraph" not in child_kinds)

    def _recover_text_patch_on_direct_section_paragraph_child_text(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._direct_section_paragraph_carrier_gap(target):
            return False
        path = tuple(getattr(target, "path", ()) or ())
        parent_target = LegalAddress(path=path[:1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        match_text = text_patch.selector.match_text
        required_occurrence = text_patch.selector.occurrence if text_patch.selector.occurrence > 0 else 1
        candidates = [
            child
            for child in parent_node.children
            if _subtree_text_match_count(child, match_text) >= required_occurrence
        ]
        if len(candidates) != 1:
            return False
        recovered_target = candidates[0]
        rebuilt, applied = self._apply_text_replace_on_subtree(
            recovered_target,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace direct section-paragraph child-text recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_direct_section_paragraph_child_text_recovered",
            message=(
                "UK replay applied a direct section-paragraph text patch to a unique "
                "direct child because the source-targeted paragraph is not represented "
                "as an addressable carrier."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "recovery_target": str(
                    LegalAddress(
                        path=(
                            *parent_target.path,
                            (_uk_kind_value(recovered_target.kind), recovered_target.label or ""),
                        ),
                        special=None,
                    )
                ),
                "text_match": match_text,
                "replacement_text": replacement,
                "family": "target_resolution_recovery",
                "source_shape": "direct_section_paragraph_text_carried_by_unique_child",
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return True

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool:
        def _local_alnum_suffix_key(text: str) -> tuple[int, int] | None:
            m = re.fullmatch(r"(\d+)([a-z])", text.strip().lower())
            if not m:
                return None
            return (int(m.group(1)), ord(m.group(2)) - ord("a") + 1)

        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        leaf_kind, leaf_label = path[-1]
        if str(leaf_kind or "").lower() != "subparagraph":
            return False
        text = str(leaf_label or "").strip().lower()
        want_pair = None
        if text.isdigit():
            want_num = int(text)
        elif re.fullmatch(r"\d+[a-z]", text):
            want_pair = _local_alnum_suffix_key(text)
            if want_pair is None:
                return False
            want_num = want_pair[0]
        else:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() != "paragraph":
            return False
        blank_present = False
        numeric_labels: list[int] = []
        numeric_pairs: list[tuple[int, int]] = []
        for child in getattr(parent_node, "children", []) or []:
            if str(getattr(child, "kind", "") or "").lower() != "subparagraph":
                continue
            raw = str(getattr(child, "label", "") or "").strip().lower()
            if not raw:
                blank_present = True
                continue
            if raw.isdigit():
                numeric_labels.append(int(raw))
                continue
            pair = _local_alnum_suffix_key(raw)
            if pair is not None:
                numeric_pairs.append(pair)
        if not blank_present:
            return False
        if want_pair is not None:
            if any(pair[0] == want_pair[0] and pair[1] > want_pair[1] for pair in numeric_pairs):
                return True
        if numeric_labels and want_num < min(numeric_labels):
            return True
        if numeric_pairs and want_num < min(pair[0] for pair in numeric_pairs):
            return True
        return False

    def _missing_schedule_branch_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2 or str(path[0][0] or "").lower() != "schedule":
            return False
        schedule_target = LegalAddress(path=path[:1], special=None)
        schedule_node, _, _ = self._find_node_by_target(schedule_target)
        return schedule_node is None

    def _prior_same_target_gap_kind(self, target: LegalAddress) -> str | None:
        want = str(target)
        prior = getattr(self, "adjudications_out", None) or []
        preferred = {
            "uk_replay_annex_schedule_reference_gap",
            "uk_replay_empty_descendant_shape_gap",
            "uk_replay_missing_parent_shape_gap",
            "uk_replay_missing_parent_grandparent_present_gap",
            "uk_replay_missing_root_parent_shape_gap",
            "uk_replay_missing_schedule_branch_gap",
            "uk_replay_missing_schedule_range_gap",
            "uk_replay_missing_sectionlike_range_gap",
            "uk_replay_malformed_target_granularity_collapse_gap",
            "uk_replay_malformed_target_gap",
            "uk_replay_malformed_target_note_or_crossheading_gap",
            "uk_replay_malformed_target_placeholder_label_gap",
            "uk_replay_malformed_target_schedule_root_label_gap",
            "uk_replay_malformed_target_sectionlike_label_gap",
            "uk_replay_replace_payload_target_leaf_mismatch_gap",
            "uk_replay_repealed_target_gap",
            "uk_replay_absent_sibling_range_gap",
            "uk_replay_schedule_container_text_target_gap",
            "uk_replay_schedule_paragraph_carrier_gap",
            "uk_replay_schedule_p1group_wrapper_carrier_gap",
            "uk_replay_schedule_partition_target_gap",
            "uk_replay_schedule_partition_part_target_gap",
            "uk_replay_schedule_unlabeled_paragraph_target_gap",
            "uk_replay_subsection_descendant_target_collapse_gap",
            "uk_replay_table_shape_gap",
            "uk_replay_missing_source_target_gap",
        }
        for adjudication in reversed(prior):
            kind = str(getattr(adjudication, "kind", "") or "")
            if kind not in preferred:
                continue
            detail = getattr(adjudication, "detail", {}) or {}
            if str(detail.get("target", "") or "") == want:
                return kind
        return None

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool:
        # Roman numeral parser: shared implementation in lawvm.roman
        # rejects non-canonical spellings like "IIII" via round-trip
        # canonicalization.  The previous nested implementation had a
        # latent bug where ``prev`` only updated in the additive branch.
        _roman_to_int = _shared_roman_to_arabic

        def _alnum_suffix_key(text: str) -> tuple[int, int] | None:
            m = re.fullmatch(r"(\d+)([a-z])", text.lower())
            if not m:
                return None
            return (int(m.group(1)), ord(m.group(2)) - ord("a") + 1)

        def _alnum_multi_suffix_key(text: str) -> tuple[int, str] | None:
            m = re.fullmatch(r"(\d+)([a-z]{2,})", text.lower())
            if not m:
                return None
            return (int(m.group(1)), m.group(2))

        def _alpha_num_suffix_key(text: str) -> tuple[str, int] | None:
            m = re.fullmatch(r"([a-z]+)(\d+)", text.lower())
            if not m:
                return None
            return (m.group(1), int(m.group(2)))

        def _part_numeric_value(raw: str) -> int | None:
            text = str(raw or "").strip()
            if not text:
                return None
            text = re.sub(r"^(?:part)\s+", "", text, flags=re.I).strip()
            if text.isdigit():
                return int(text)
            roman = _roman_to_int(text)
            if roman is not None:
                return roman
            return None

        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        mode: str | None = None
        want: int
        want_pair: tuple[int, int] | None = None
        want_multi_pair: tuple[int, str] | None = None
        want_alpha_num_pair: tuple[str, int] | None = None
        if text.isdigit():
            mode = "numeric"
            want = int(text)
        elif re.fullmatch(r"[a-z]", text):
            mode = "alpha"
            want = ord(text) - ord("a") + 1
        elif re.fullmatch(r"[a-z]{2,}", text):
            mode = "alpha_suffix"
            want = ord(text[0]) - ord("a") + 1
        elif re.fullmatch(r"[ivxlcdm]+", text):
            roman = _roman_to_int(text)
            if roman is None:
                return False
            mode = "roman"
            want = roman
        elif re.fullmatch(r"\d+[a-z]", text):
            pair = _alnum_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_suffix"
            want = pair[0]
            want_pair = pair
        elif re.fullmatch(r"\d+[a-z]{2,}", text):
            pair = _alnum_multi_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_multi_suffix"
            want = pair[0]
            want_multi_pair = pair
        elif re.fullmatch(r"[a-z]+\d+", text):
            pair = _alpha_num_suffix_key(text)
            if pair is None:
                return False
            mode = "alpha_num_suffix"
            want = pair[1]
            want_alpha_num_pair = pair
        else:
            return False
        if len(path) == 1:
            parent_node = self.statute.body
        else:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            if parent_node is None:
                return False
        if str(leaf_kind or "").lower() == "part" and text.isdigit():
            part_nums: list[int] = []
            for child in getattr(parent_node, "children", []) or []:
                if str(getattr(child, "kind", "") or "").lower() != "part":
                    continue
                num = _part_numeric_value(str(getattr(child, "label", "") or ""))
                if num is not None:
                    part_nums.append(num)
            if part_nums:
                part_nums = sorted(set(part_nums))
                want_num = int(text)
                lower = max((n for n in part_nums if n < want_num), default=None)
                upper = min((n for n in part_nums if n > want_num), default=None)
                if lower is not None and upper is not None and lower < want_num < upper:
                    return True
                if lower is None and part_nums and want_num < part_nums[0]:
                    return True
                if upper is None and part_nums and want_num > part_nums[-1]:
                    return True
        if str(leaf_kind or "").lower() == "part" and re.fullmatch(r"\d+[a-z]+", text):
            base_match = re.fullmatch(r"(\d+)[a-z]+", text)
            if base_match is not None:
                want_num = int(base_match.group(1))
                part_nums: list[int] = []
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "part":
                        continue
                    raw = str(getattr(child, "label", "") or "").strip()
                    base_num = _part_numeric_value(raw)
                    if base_num is not None:
                        part_nums.append(base_num)
                        continue
                    m = re.fullmatch(r"part\s+(\d+)[a-z]+", raw, re.I)
                    if m is not None:
                        part_nums.append(int(m.group(1)))
                if part_nums:
                    part_nums = sorted(set(part_nums))
                    lower = max((n for n in part_nums if n < want_num), default=None)
                    upper = min((n for n in part_nums if n > want_num), default=None)
                    if lower is not None and upper is not None and lower < want_num < upper:
                        return True
                    if any(n == want_num for n in part_nums):
                        return True
        sibling_labels: list[int] = []
        sibling_pairs: list[tuple[int, int]] = []
        sibling_multi_pairs: list[tuple[int, str]] = []
        sibling_alpha_num_pairs: list[tuple[str, int]] = []
        alpha_raw_labels: list[str] = []
        numeric_suffix_labels: list[int] = []
        alpha_suffix_labels: list[str] = []
        blank_same_kind_present = False
        for child in getattr(parent_node, "children", []) or []:
            child_kind = str(getattr(child, "kind", "") or "").lower()
            if child_kind == str(leaf_kind or "").lower():
                label_text = str(getattr(child, "label", "") or "").strip()
                if not label_text:
                    blank_same_kind_present = True
                if mode == "numeric" and label_text.isdigit():
                    sibling_labels.append(int(label_text))
                elif mode == "numeric" and (pair := _alnum_suffix_key(label_text)) is not None:
                    numeric_suffix_labels.append(int(pair[0]))
                elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                    sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                elif mode == "alpha":
                    alpha_raw_labels.append(label_text.lower())
                elif mode == "alpha_suffix":
                    lowered = label_text.lower()
                    if re.fullmatch(r"[a-z]", lowered):
                        sibling_labels.append(ord(lowered) - ord("a") + 1)
                    else:
                        alpha_suffix_labels.append(lowered)
                elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                    roman = _roman_to_int(label_text)
                    if roman is not None:
                        sibling_labels.append(roman)
                elif mode == "alnum_suffix":
                    pair = _alnum_suffix_key(label_text)
                    if pair is not None:
                        sibling_pairs.append(pair)
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alnum_multi_suffix":
                    pair = _alnum_multi_suffix_key(label_text)
                    if pair is not None:
                        sibling_multi_pairs.append(pair)
                    elif (pair1 := _alnum_suffix_key(label_text)) is not None:
                        sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alpha_num_suffix":
                    pair = _alpha_num_suffix_key(label_text)
                    if pair is not None:
                        sibling_alpha_num_pairs.append(pair)
                    elif re.fullmatch(r"[a-z]+", label_text.lower()):
                        alpha_raw_labels.append(label_text.lower())
                continue
            if uk_is_transparent_wrapper_kind(child_kind):
                for grandchild in getattr(child, "children", []) or []:
                    if str(getattr(grandchild, "kind", "") or "").lower() != str(leaf_kind or "").lower():
                        continue
                    label_text = str(getattr(grandchild, "label", "") or "").strip()
                    if not label_text:
                        blank_same_kind_present = True
                    if mode == "numeric" and label_text.isdigit():
                        sibling_labels.append(int(label_text))
                    elif mode == "numeric" and (pair := _alnum_suffix_key(label_text)) is not None:
                        numeric_suffix_labels.append(int(pair[0]))
                    elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                        sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                    elif mode == "alpha":
                        alpha_raw_labels.append(label_text.lower())
                    elif mode == "alpha_suffix":
                        lowered = label_text.lower()
                        if re.fullmatch(r"[a-z]", lowered):
                            sibling_labels.append(ord(lowered) - ord("a") + 1)
                        else:
                            alpha_suffix_labels.append(lowered)
                    elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                        roman = _roman_to_int(label_text)
                        if roman is not None:
                            sibling_labels.append(roman)
                    elif mode == "alnum_suffix":
                        pair = _alnum_suffix_key(label_text)
                        if pair is not None:
                            sibling_pairs.append(pair)
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alnum_multi_suffix":
                        pair = _alnum_multi_suffix_key(label_text)
                        if pair is not None:
                            sibling_multi_pairs.append(pair)
                        elif (pair1 := _alnum_suffix_key(label_text)) is not None:
                            sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alpha_num_suffix":
                        pair = _alpha_num_suffix_key(label_text)
                        if pair is not None:
                            sibling_alpha_num_pairs.append(pair)
                        elif re.fullmatch(r"[a-z]+", label_text.lower()):
                            alpha_raw_labels.append(label_text.lower())
        if mode == "alnum_multi_suffix":
            if want_multi_pair is None:
                return False
            if sibling_multi_pairs:
                sibling_multi_pairs = sorted(set(sibling_multi_pairs))
                lower = max((pair for pair in sibling_multi_pairs if pair < want_multi_pair), default=None)
                upper = min((pair for pair in sibling_multi_pairs if pair > want_multi_pair), default=None)
                if lower is not None or upper is not None:
                    return True
                if any(pair[0] == want_multi_pair[0] for pair in sibling_multi_pairs):
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_multi_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels and want_multi_pair[0] in set(numeric_suffix_labels):
                return True
            return False
        if mode == "alpha_num_suffix":
            if want_alpha_num_pair is None:
                return False
            if sibling_alpha_num_pairs:
                sibling_alpha_num_pairs = sorted(set(sibling_alpha_num_pairs))
                same_prefix = [pair for pair in sibling_alpha_num_pairs if pair[0] == want_alpha_num_pair[0]]
                if same_prefix:
                    lower = max((pair for pair in same_prefix if pair[1] < want_alpha_num_pair[1]), default=None)
                    upper = min((pair for pair in same_prefix if pair[1] > want_alpha_num_pair[1]), default=None)
                    if lower is not None or upper is not None:
                        return True
            if any(label == want_alpha_num_pair[0] for label in alpha_raw_labels):
                return True
            return False
        if mode == "alnum_suffix":
            if not sibling_pairs or want_pair is None:
                # If the section still has the numeric base subsection (e.g. "6")
                # but the alpha extension (e.g. "6A") is absent, treat this as the
                # same stale/shape family as other missing sibling gaps.
                want_pair_base = want_pair[0] if want_pair is not None else None
                want_num = str(want_pair_base) if want_pair_base is not None else ""
                numeric_base_present = any(
                    str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                    and str(getattr(child, "label", "") or "").strip().lower() == want_num
                    for child in getattr(parent_node, "children", []) or []
                )
                if numeric_suffix_labels and want_pair_base is not None:
                    nums = sorted(set(numeric_suffix_labels))
                    lower_num = max((n for n in nums if n < want_pair_base), default=None)
                    upper_num = min((n for n in nums if n > want_pair_base), default=None)
                    if lower_num is not None and upper_num is not None and lower_num < want_pair_base < upper_num:
                        return True
                    if lower_num is None and nums and want_pair_base < nums[0]:
                        return True
                    if upper_num is None and nums and want_pair_base > nums[-1]:
                        return True
                return numeric_base_present
            sibling_pairs = sorted(set(sibling_pairs))
            lower = max((pair for pair in sibling_pairs if pair < want_pair), default=None)
            upper = min((pair for pair in sibling_pairs if pair > want_pair), default=None)
            if lower is not None and upper is not None and lower < want_pair < upper:
                return True
            if lower is None and sibling_pairs and want_pair < sibling_pairs[0]:
                return True
            if upper is None and sibling_pairs and want_pair > sibling_pairs[-1]:
                return True
            same_num = [pair for pair in sibling_pairs if pair[0] == want_pair[0]]
            if same_num:
                lower_same = max((pair for pair in same_num if pair[1] < want_pair[1]), default=None)
                upper_same = min((pair for pair in same_num if pair[1] > want_pair[1]), default=None)
                if lower_same is not None or upper_same is not None:
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want_pair[0]), default=None)
                upper_num = min((n for n in nums if n > want_pair[0]), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want_pair[0] < upper_num:
                    return True
                if lower_num is None and nums and want_pair[0] < nums[0]:
                    return True
                if upper_num is None and nums and want_pair[0] > nums[-1]:
                    return True
            return False
        if mode == "alpha_suffix":
            if any(label.startswith(text) and len(label) > len(text) for label in alpha_suffix_labels):
                return True
            first = text[:1]
            if any(label == first for label in alpha_raw_labels):
                return True
            lower = max((n for n in sibling_labels if n < want), default=None)
            upper = min((n for n in sibling_labels if n > want), default=None)
            if lower is not None and upper is not None and lower < want < upper:
                return True
            if any(label.startswith(first) and len(label) > 1 for label in alpha_suffix_labels):
                return True
            return False
        if not sibling_labels:
            if mode == "numeric" and numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want), default=None)
                upper_num = min((n for n in nums if n > want), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want < upper_num:
                    return True
                if lower_num is None and nums and want < nums[0]:
                    return True
                if upper_num is None and nums and want > nums[-1]:
                    return True
            if mode == "alpha" and any(label.startswith(text) and len(label) > 1 for label in alpha_raw_labels):
                return True
            if mode == "alpha":
                repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
                if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                    return True
            return False
        if mode == "alpha":
            repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
            if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                return True
        sibling_labels = sorted(set(sibling_labels))
        if mode == "numeric" and blank_same_kind_present and sibling_labels and want < sibling_labels[0]:
            return True
        lower = max((label for label in sibling_labels if label < want), default=None)
        upper = min((label for label in sibling_labels if label > want), default=None)
        if lower is not None and upper is not None and lower < want < upper:
            return True
        if lower is None and sibling_labels and want < sibling_labels[0]:
            return True
        if upper is None and sibling_labels and want > sibling_labels[-1]:
            return True
        return False

    def _container_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if _addr_container(cast(LegalAddress, target)) != "schedule":
            return False
        leaf_kind, _ = path[-1]
        if str(leaf_kind or "").lower() not in {"part", "chapter"}:
            return False
        schedule_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if schedule_node is None:
            return False
        if any(
            str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
            for child in getattr(schedule_node, "children", []) or []
        ):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return any(token in combined for token in ("paragraph", "sub-paragraph", "subparagraph", "item"))

    def _subsection_alpha_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if str(path[0][0] or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        if str(path[1][0] or "").lower() != "subsection":
            return False
        leaf_label = str(path[1][1] or "").strip().lower()
        if not re.fullmatch(r"[a-z]+", leaf_label):
            return False
        parent_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if parent_node is None:
            return False
        subsection_labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == "subsection"
        ]
        if not subsection_labels or not all(re.fullmatch(r"\d+[a-z]?", label) for label in subsection_labels if label):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return bool(re.search(r"subsection\s*\(\d+[a-z]?\)\s*\([a-z]+\)", combined))

    def _missing_sectionlike_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1:
            return False
        leaf_kind, leaf_label = path[0]
        if str(leaf_kind or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        want_label = str(leaf_label or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels: list[str] = []

        def _walk(node: UKMutableNode) -> None:
            for child in getattr(node, "children", []) or []:
                if str(getattr(child, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}:
                    label = str(getattr(child, "label", "") or "").strip()
                    if label:
                        labels.append(label)
                _walk(child)

        _walk(self.statute.body)
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        return lower is not None and upper is not None

    def _doubled_alpha_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        if not re.fullmatch(r"([a-z])\1+", text):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
        ]
        repeated = sorted(label for label in labels if re.fullmatch(r"([a-z])\1+", label))
        if not repeated:
            return False
        return any(rep < text for rep in repeated) and any(rep > text for rep in repeated)

    def _missing_schedule_root_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        want_label = str(path[0][1] or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels = [str(getattr(sched, "label", "") or "").strip() for sched in self.statute.supplements]
        labels = [label for label in labels if label]
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        if lower is not None and upper is not None:
            return True
        if lower is None and existing and want_key < existing[0]:
            return True
        if upper is None and existing and want_key > existing[-1]:
            return True
        return False

    def _existing_target_insert_gap(
        self,
        target: LegalAddress,
        node: Optional[UKMutableNode],
        op: LegalOperation,
    ) -> bool:
        if _action_name(op.action) != "insert" or node is None:
            return False
        payload = getattr(op, "payload", None)
        if payload is None:
            return True
        payload_kind = str(getattr(payload, "kind", "") or "")
        payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
        target_kind = _addr_leaf_kind(target) or ""
        target_label = _addr_leaf_label(target) or ""
        if not (
            uk_kind_matches(
                node_kind=payload_kind,
                target_kind=target_kind,
                node_label=payload_label,
                target_label=_clean_num(target_label),
            )
            and payload_label == _clean_num(target_label)
        ):
            return False
        return uk_kind_matches(
            node_kind=str(getattr(node, "kind", "") or ""),
            target_kind=target_kind,
            node_label=_clean_num(str(getattr(node, "label", "") or "")),
            target_label=_clean_num(target_label),
        ) and _clean_num(str(getattr(node, "label", "") or "")) == _clean_num(target_label)

    def _existing_target_insert_already_materialized(
        self,
        node: Optional[UKMutableNode],
        op: LegalOperation,
    ) -> bool:
        payload = getattr(op, "payload", None)
        if node is None or payload is None:
            return False
        existing_text = _normalized_replay_subtree_text(node)
        payload_text = _normalized_replay_subtree_text(payload)
        return bool(existing_text and payload_text and existing_text == payload_text)

    def _existing_target_insert_conflict_detail(
        self,
        node: Optional[UKMutableNode],
        op: LegalOperation,
    ) -> Optional[dict[str, str]]:
        payload = getattr(op, "payload", None)
        if node is None or payload is None:
            return None
        existing_text = _normalized_replay_subtree_text(node)
        payload_text = _normalized_replay_subtree_text(payload)
        if not existing_text or not payload_text or existing_text == payload_text:
            return None
        return {
            "existing_text_preview": existing_text[:240],
            "payload_text_preview": payload_text[:240],
        }

    def _find_existing_insert_target_by_explicit_parent_leaf(
        self,
        target: LegalAddress,
        op: LegalOperation,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int], str]:
        if _action_name(op.action) != "insert" or op.payload is None:
            return None, None, None, ""
        parent_addr = target.parent() if len(target.path) > 1 else None
        leaf_kind = _addr_leaf_kind(target)
        leaf_label = _addr_leaf_label(target)
        if parent_addr is None or not leaf_kind or not leaf_label:
            return None, None, None, ""
        parent_candidate: Optional[UKMutableNode] = None
        parent_eid = self._derive_target_eid(parent_addr)
        if parent_eid:
            parent_candidate, _, _ = self._find_node_and_parent_statute(
                parent_eid,
                allow_sequence_match=False,
            )
            if parent_candidate is not None and not self._eid_candidate_matches_target_leaf(
                parent_candidate,
                parent_addr,
            ):
                parent_candidate = None
        if parent_candidate is None:
            parent_candidate, _, _ = self._find_node_by_target(
                parent_addr,
                allow_recursive_match=False,
            )
        if parent_candidate is None:
            return None, None, None, ""
        for child_idx, child in enumerate(parent_candidate.children):
            if self._match_kind_label(child, leaf_kind, leaf_label) and self._existing_target_insert_gap(
                target,
                child,
                op,
            ):
                return child, parent_candidate, child_idx, "explicit_parent_leaf_same_kind_label"
        return None, None, None, ""

    def _crossheading_insert_target_gap(
        self,
        target: LegalAddress,
        op: LegalOperation,
    ) -> bool:
        payload = getattr(op, "payload", None)
        return (
            _action_name(op.action) == "insert"
            and _addr_leaf_kind(target) == "crossheading"
            and not _clean_num(_addr_leaf_label(target) or "")
            and payload is not None
            and str(getattr(payload, "kind", "") or "").lower() == "crossheading"
        )

    def _match_kind_label(self, node: Any, kind: str, label: Optional[str]) -> bool:
        """Shared matching logic for UK IR nodes."""
        nk = str(node.kind)
        tk = kind.lower()
        node_label = _clean_num(node.label or "")
        want_label = _clean_num(label or "") if label else ""

        if not uk_kind_matches(
            node_kind=nk,
            target_kind=tk,
            node_label=node_label,
            target_label=want_label,
        ):
            return False

        if not label:
            return True
        if tk == "schedule" and want_label:
            schedule_labels = {want_label}
            if want_label.startswith("schedule "):
                schedule_labels.add(want_label.removeprefix("schedule ").strip())
            else:
                schedule_labels.add(f"schedule {want_label}")
            return node_label in schedule_labels
        return node_label == want_label

    def _find_compound_subsection_candidate(
        self,
        curr_node: UKMutableNode,
        label: str,
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        """Match malformed UK shapes like legal subsection 8A stored as 8 -> a."""
        return uk_compound_subsection_candidate(
            cast(IRNode, curr_node),
            label,
            match_kind_label=self._match_kind_label,
        )

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        """Find a node and its parent by LegalAddress path."""
        def _find(address: LegalAddress) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
            path = list(address.path)
            container = _addr_container(address)

            # 1. Resolve top-level container
            roots: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
            if container == "schedule":
                # First path segment is ("schedule", label)
                sched_label = path[0][1] if path else None
                remaining = path[1:]
                roots = uk_schedule_root_candidates(
                    cast(list[IRNode], self.statute.supplements),
                    sched_label=sched_label,
                    remaining_path=tuple(remaining),
                    match_kind_label=self._match_kind_label,
                )
                if sched_label and roots and not remaining:
                    sch, _, idx = roots[0]
                    return cast(UKMutableNode, sch), None, idx
                if not sched_label and len(roots) == 1 and not remaining:
                    sch, _, idx = roots[0]
                    return cast(UKMutableNode, sch), None, idx
                path = remaining
            else:
                roots = [(cast(IRNode, self.statute.body), None, None)]
            if not roots:
                return None, None, None

            curr_cands = roots
            for p_kind, p_label in path:
                next_cands: list[tuple[IRNode, Optional[IRNode], Optional[int]]] = []
                for curr_node, _, _ in curr_cands:
                    for i, child in enumerate(curr_node.children):
                        if self._match_kind_label(child, p_kind, p_label):
                            next_cands.append((child, curr_node, i))
                    if not next_cands and allow_compound_subsection_alias and p_kind.lower() == "subsection" and p_label:
                        compound = self._find_compound_subsection_candidate(cast(UKMutableNode, curr_node), p_label)
                        if compound[0] is not None:
                            next_cands.append(cast(tuple[IRNode, Optional[IRNode], Optional[int]], compound))
                if not next_cands:
                    if container == "schedule":
                        ordinal_matches = uk_schedule_ordinal_paragraph_matches(
                            curr_cands,
                            p_kind=p_kind,
                            p_label=p_label,
                        )
                        if ordinal_matches:
                            if target_resolution_op is not None:
                                for resolved_node, resolved_parent, _resolved_idx in ordinal_matches:
                                    if (
                                        _uk_kind_value(resolved_node.kind) == "paragraph"
                                        and resolved_parent is not None
                                        and _uk_kind_value(resolved_parent.kind) == "p1group"
                                        and not _clean_num(str(resolved_parent.label or ""))
                                    ):
                                        _append_uk_replay_adjudication(
                                            self.adjudications_out,
                                            kind=_UK_REPLAY_SCHEDULE_P1GROUP_PARAGRAPH_WRAPPER_RESOLVED_RULE_ID,
                                            message=(
                                                "UK replay resolved an explicit schedule paragraph "
                                                "target through an unlabeled p1group wrapper with a "
                                                "single exactly labelled paragraph child."
                                            ),
                                            op=target_resolution_op,
                                            detail={
                                                "action": _action_name(target_resolution_op.action),
                                                "target": str(target),
                                                "paragraph_label": str(p_label),
                                                "wrapper_kind": "p1group",
                                                "family": "target_resolution_recovery",
                                                "blocking": False,
                                                "strict_disposition": "record",
                                                "quirks_disposition": "apply",
                                            },
                                        )
                                        break
                            next_cands = ordinal_matches
                    if not next_cands:
                        for curr_node, _, _ in curr_cands:
                            if allow_recursive_match:
                                for child in curr_node.children:
                                    res_node, res_p, res_i = self._find_recursive_match(
                                        cast(UKMutableNode, child), p_kind, p_label
                                    )
                                    if res_node:
                                        next_cands.append((res_node, res_p, res_i))
                if not next_cands:
                    return None, None, None
                curr_cands = next_cands
            return (
                cast(tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]], curr_cands[0])
                if curr_cands
                else (None, None, None)
            )

        if self._is_explicit_direct_section_paragraph_target(target):
            raw_node = _find(target)
            if raw_node[0] is not None:
                return raw_node
        return _find(canonicalize_uk_address(target))

    def _find_unique_schedule_item_for_source_parent_substitution_range_target(
        self,
        target: LegalAddress,
        op: LegalOperation,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        """Resolve feed `Sch. N para. (d)` shape to a unique schedule item.

        This recovery is available only for ops whose lowering witness proved a
        source-parent sibling-range substitution. It does not authorize general
        schedule paragraph-to-item fallback.
        """
        if op.witness_rule_id != _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID:
            return None, None, None
        if _addr_container(target) != "schedule" or len(tuple(target.path)) != 2:
            return None, None, None
        schedule_label = target.path[0][1]
        target_kind, target_label_raw = target.path[1]
        target_label = _source_parent_range_label(target_label_raw)
        if target_kind != "paragraph" or not re.fullmatch(r"[a-z]", target_label, re.I):
            return None, None, None
        if op.payload is not None:
            payload_kind = _uk_kind_value(op.payload.kind).lower()
            payload_label = _source_parent_range_label(op.payload.label or "")
            if payload_kind != "item" or payload_label != target_label:
                return None, None, None

        roots = uk_schedule_root_candidates(
            cast(list[IRNode], self.statute.supplements),
            sched_label=schedule_label,
            remaining_path=(),
            match_kind_label=self._match_kind_label,
        )
        candidates: list[tuple[UKMutableNode, UKMutableNode, int]] = []

        def _walk(parent: UKMutableNode) -> None:
            for child_idx, child in enumerate(parent.children):
                if (
                    _uk_kind_value(child.kind).lower() == "item"
                    and _source_parent_range_label(child.label or "") == target_label
                ):
                    candidates.append((child, parent, child_idx))
                _walk(child)

        for root, _root_parent, _root_idx in roots:
            _walk(cast(UKMutableNode, root))
        if len(candidates) != 1:
            return None, None, None
        recovered_node, recovered_parent, recovered_idx = candidates[0]
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_ITEM_TARGET_FROM_PARENT_SUBSTITUTION_RULE_ID,
            message=(
                "UK replay resolved a source-parent substitution-range target "
                "whose effect feed names a schedule item as a schedule paragraph."
            ),
            op=op,
            detail={
                "action": _action_name(op.action),
                "target": str(target),
                "recovered_kind": _uk_kind_value(recovered_node.kind),
                "recovered_label": recovered_node.label or "",
                "family": "target_resolution_recovery",
                "source_rule_id": _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
                "blocking": False,
                "strict_disposition": "block",
                "quirks_disposition": "apply",
            },
        )
        return recovered_node, recovered_parent, recovered_idx

    @staticmethod
    def _is_explicit_direct_section_paragraph_target(target: LegalAddress) -> bool:
        path = tuple(target.path or ())
        return len(path) >= 2 and path[0][0] == "section" and path[1][0] == "paragraph"

    def _find_recursive_match(
        self, node: UKMutableNode, kind: str, label: str
    ) -> tuple[Optional[IRNode], Optional[IRNode], Optional[int]]:
        return uk_recursive_kind_match(
            cast(IRNode, node),
            kind=str(kind),
            label=label,
            match_kind_label=self._match_kind_label,
        )

    def _empty_schedule_root_shape_gap(self, target: LegalAddress) -> bool:
        """Return True when a descendant target lands under an empty schedule root."""
        if _addr_container(target) != "schedule" or len(target.path) <= 1:
            return False
        sched_label = target.path[0][1] if target.path else None
        if not sched_label:
            return False
        for sch in self.statute.supplements:
            if self._match_kind_label(sch, "schedule", sched_label):
                return len(sch.children) == 0
        return False

    def _emit_top_section_snapshot(self, op: LegalOperation) -> None:
        """Emit a top-level section/schedule snapshot to lo_ops_out after an op is applied.

        Finds the top-level node (first path segment) affected by *op* in the
        current statute state and appends a LegalOperation snapshot to lo_ops_out.
        This gives compile_timelines() section-level content for overlay
        materialization, mirroring the Finland lo_ops_out pattern.

        For repeal ops the tombstone is recorded (payload=None, action="repeal").
        For all other structural ops the current node content is snapshotted
        (action="replace" / "insert" depending on whether the node was already in
        the base, but "replace" is used as the conservative choice since
        compile_timelines handles both identically for existing addresses).
        """
        if self.lo_ops_out is None:
            return
        target = op.target
        if not target.path:
            return
        # Derive the canonical address for the top-level container.
        # For body ops this is the first path segment (e.g. section:1 or part:I).
        # For schedule ops it is the schedule element itself.
        top_kind, top_label = target.path[0]
        top_addr = LegalAddress(path=((top_kind, top_label),))

        # Find the top-level node in the current (post-op) statute state.
        # We look in body children and schedules.
        top_node: Optional[UKMutableNode] = None
        for child in self.statute.body.children:
            if str(child.kind) == top_kind and (child.label is not None and child.label == top_label):
                top_node = child
                break
        if top_node is None:
            for sch in self.statute.supplements:
                if str(sch.kind) == top_kind and sch.label == top_label:
                    top_node = sch
                    break

        if _action_name(op.action) == "repeal" and top_node is None:
            # Node was removed — emit tombstone
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_repeal_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPEAL,
                    target=top_addr,
                    payload=None,
                    source=op.source,
                    group_id=op.group_id,
                )
            )
        elif top_node is not None:
            # Snapshot the current state of the top-level node after op applied.
            self.lo_ops_out.append(
                LegalOperation(
                    op_id=f"uk_snapshot_{top_kind}_{top_label}_{op.op_id}",
                    sequence=op.sequence,
                    action=StructuralAction.REPLACE,
                    target=top_addr,
                    payload=top_node.to_irnode(),
                    source=op.source,
                    group_id=op.group_id,
                )
            )

    def _apply_same_provision_descendant_renumber(self, op: LegalOperation) -> bool:
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if len(destination.path) != len(source_target.path) + 1 or destination.path[:-1] != source_target.path:
            return False

        source_node, _source_parent, _source_idx = self._find_node_by_target(source_target)
        if source_node is None:
            return False
        destination_kind = _addr_leaf_kind(destination) or ""
        destination_label = _addr_leaf_label(destination)
        # Descendant renumbering creates the destination as an immediate child of
        # the source provision.  Do not use broad recursive target lookup here:
        # schedule item "i" may normalize like subparagraph "1", but it is not a
        # destination collision for "paragraph 12 becomes sub-paragraph (1)".
        for child in source_node.children:
            child_kind = str(child.kind or "").lower()
            child_label = _clean_num(str(child.label or ""))
            if child_kind == destination_kind and child_label == _clean_num(destination_label or ""):
                return False

        if not destination_kind:
            return False

        child = UKMutableNode(
            kind=IRNodeKind(destination_kind),
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={"eId": self._derive_target_eid(destination)},
            children=list(source_node.children),
        )
        replacement = UKMutableNode(
            kind=source_node.kind,
            label=source_node.label,
            text="",
            attrs=dict(source_node.attrs),
            children=[child],
        )
        return self._replace_node_in_statute(source_node, replacement)

    def _apply_same_parent_sibling_renumber(self, op: LegalOperation) -> bool:
        source_target = canonicalize_uk_address(op.target)
        destination = canonicalize_uk_address(op.destination) if op.destination is not None else None
        if destination is None:
            return False
        if (
            len(destination.path) != len(source_target.path)
            or destination.path[:-1] != source_target.path[:-1]
            or _addr_leaf_kind(destination) != _addr_leaf_kind(source_target)
        ):
            return False

        source_node, source_parent, source_idx = self._find_node_by_target(source_target)
        if source_node is None or source_parent is None or source_idx is None:
            return False
        destination_node, _destination_parent, _destination_idx = self._find_node_by_target(destination)
        if destination_node is not None:
            return False

        destination_label = _addr_leaf_label(destination)
        moved = dc_replace(
            source_node,
            label=destination_label,
            text=_renumbered_descendant_text(
                source_node.text or "",
                source_label=source_node.label,
                destination_label=destination_label,
            ),
            attrs={**dict(source_node.attrs), "eId": self._derive_target_eid(destination)},
        )
        source_parent.children.pop(source_idx)
        self._insert_child_sorted(source_parent, moved)
        return True

    def apply_op(self, op: LegalOperation):
        target = op.target
        # Keep legacy warnings visible during replay runs while also recording
        # structured adjudications for downstream analyses.

        if str(target.special or "") == "whole_act":
            if _action_name(op.action) == "repeal":
                self._log("  EXECUTOR: repealing WHOLE ACT")
                self.statute.body.children = []
                self.statute.supplements = []
                self._record_invariant_violations(op)
            else:
                self._log(
                    f"  EXECUTOR: WARN whole_act target with unhandled action {op.action!r} — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_unsupported_action",
                    message="UK replay skipped unsupported whole-act action.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            return

        target_eid = self._derive_target_eid(target)
        node: Optional[UKMutableNode]
        parent: Optional[UKMutableNode]
        idx: Optional[int]
        node, parent, idx = None, None, None
        if target_eid:
            node, parent, idx = self._find_node_and_parent_statute(
                target_eid,
                allow_sequence_match=False,
            )
            if node is not None and not self._eid_candidate_matches_target_leaf(node, target):
                node, parent, idx = None, None, None

        if not node:
            allow_compound_subsection_alias = _action_name(op.action) in ("text_replace", "text_repeal")
            node, parent, idx = self._find_node_by_target(
                target,
                allow_compound_subsection_alias=allow_compound_subsection_alias,
                allow_recursive_match=_action_name(op.action) != "insert",
                target_resolution_op=op,
            )
        insert_existing_target_resolution = ""
        if not node:
            node, parent, idx, insert_existing_target_resolution = (
                self._find_existing_insert_target_by_explicit_parent_leaf(target, op)
            )
        if not node and _action_name(op.action) in {"replace", "repeal"}:
            node, parent, idx = self._find_unique_schedule_item_for_source_parent_substitution_range_target(
                target,
                op,
            )
        target_found = node is not None
        if not target_found and self._empty_schedule_root_shape_gap(target):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_empty_schedule_shape_gap",
                message="UK replay skipped text-based op: empty schedule root has no descendant target shape.",
                op=op,
                detail={
                    "action": _action_name(op.action),
                    "target": str(target),
                    "source_shape": "empty_schedule_root",
                },
            )
            return

        if _action_name(op.action) == "repeal":
            schedule_list_entry_repeal_selector = _schedule_list_entry_repeal_selector(op)
            if schedule_list_entry_repeal_selector is not None:
                if self._repeal_schedule_list_entries(target, op, schedule_list_entry_repeal_selector):
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                return
            crossheading_group_repeal_selector = _crossheading_group_repeal_selector(op)
            if crossheading_group_repeal_selector is not None and node is not None:
                if self._repeal_crossheading_group(target, node, parent, op, crossheading_group_repeal_selector):
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                return
            if crossheading_group_repeal_selector is not None:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped cross-heading group repeal: "
                        "structural target was not found."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "reason_code": "target_not_found",
                        "selector": dict(crossheading_group_repeal_selector),
                    },
                )
                return
            if node is None:
                if self._target_under_repealed_prefix(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repeal_target_already_absent_observed",
                        message=(
                            "UK replay observed a structural repeal whose target path "
                            "was already repealed earlier in the chain."
                        ),
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "reason_code": "target_previously_repealed",
                            "family": "structural_repeal_idempotence",
                            "blocking": False,
                            "strict_disposition": "record",
                            "quirks_disposition": "record",
                        },
                    )
                elif self._doubled_alpha_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped repeal: target falls inside an absent doubled-alpha sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._malformed_target_gap_kind(target),
                        message="UK replay skipped repeal: lowered target path is malformed.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_source_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_source_target_gap",
                        message="UK replay skipped repeal: target comes from index-only effect row without extracted source text.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped repeal: target falls inside an absent sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_empty_descendant_shape_gap",
                        message="UK replay skipped repeal: parent target exists but has no descendant structural shape.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sectionlike_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_sectionlike_range_gap",
                        message="UK replay skipped repeal: target falls inside an absent sectionlike range gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_schedule_branch_gap",
                        message="UK replay skipped repeal: schedule root branch is absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_root_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_schedule_range_gap",
                        message="UK replay skipped repeal: target falls inside an absent alphanumeric schedule range gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_schedule_branch_gap",
                        message="UK replay skipped repeal: schedule root branch is absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._missing_parent_shape_gap_kind(target),
                        message="UK replay skipped repeal: immediate parent target path is structurally absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._schedule_paragraph_carrier_gap_kind(target),
                        message="UK replay skipped repeal: schedule paragraph carrier is structurally absent or wrapped.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped repeal: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                else:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_target_not_found",
                        message="UK replay skipped repeal: target not found.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                return
            if parent and idx is not None:
                self._log(f"  EXECUTOR: repealing {node.kind} {node.label} from parent {parent.kind} {parent.label}")
                self._remove_node(node, parent, idx)
                self._record_repealed_target(target)
            elif node in self.statute.supplements:
                self._log(f"  EXECUTOR: repealing schedule {node.label}")
                self._remove_node(node, None, None)
                self._record_repealed_target(target)
            self._record_invariant_violations(op)
            self._emit_top_section_snapshot(op)
        elif _action_name(op.action) == "replace":
            schedule_list_entry_replace_selector = _schedule_list_entry_replace_selector(op)
            if schedule_list_entry_replace_selector is not None:
                if op.payload is None:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                        message=(
                            "UK replay skipped schedule-list-entry replacement: "
                            "replacement payload was missing."
                        ),
                        op=op,
                        detail={
                            "target": str(target),
                            "selector": dict(schedule_list_entry_replace_selector),
                            "reason_code": "payload_missing",
                            "family": "source_schedule_list_entry_elaboration",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
                    )
                    return
                new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
                if self._replace_schedule_list_entry(
                    target,
                    new_node,
                    op,
                    schedule_list_entry_replace_selector,
                ):
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                return
            frag_subs = _fragment_substitution(op)
            if frag_subs is not None:
                if node:
                    self._log(f"  EXECUTOR: substituting text in {node.kind} {node.label}")
                    self._apply_text_substitution_on_node(node, frag_subs)
                    self._record_invariant_violations(op)
                else:
                    if self._malformed_target_gap(target):
                        kind = self._malformed_target_gap_kind(target)
                        message = "UK replay skipped replace: lowered target path is malformed."
                    elif self._missing_parent_shape_gap(target):
                        kind = self._missing_parent_shape_gap_kind(target)
                        message = "UK replay skipped replace: immediate parent target path is structurally absent."
                    elif self._missing_sectionlike_gap(target):
                        kind = "uk_replay_missing_sectionlike_range_gap"
                        message = "UK replay skipped replace: target falls inside an absent sectionlike range gap."
                    else:
                        kind = "uk_replay_target_not_found"
                        message = "UK replay skipped replace: target not found."
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=str(kind),
                        message=message,
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
            elif op.payload is not None:
                # Clone payload so repeated ops don't share state
                new_node = UKMutableNode.from_dict(op.payload.to_jsonable_dict())
                if node:
                    node_kind = str(node.kind).lower()
                    new_kind = str(new_node.kind).lower()
                    if node_kind != "content" and new_kind != "content":
                        label_changing_substitution = (
                            op.witness_rule_id == _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID
                        )
                        existing_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
                        if existing_eid and not label_changing_substitution:
                            new_node.attrs["eId"] = existing_eid
                        if parent and idx is not None:
                            self._replace_node_in_statute(node, new_node)
                            if label_changing_substitution:
                                _append_uk_replay_adjudication(
                                    self.adjudications_out,
                                    kind=_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID,
                                    message=(
                                        "UK replay applied a source-owned label-changing "
                                        "substitution by replacing the old sibling with "
                                        "the new labelled payload."
                                    ),
                                    op=op,
                                    detail={
                                        "target": str(target),
                                        "source_label": str(node.label or ""),
                                        "replacement_label": str(new_node.label or ""),
                                        "family": "lineage_normalization",
                                        "blocking": False,
                                        "strict_disposition": "record",
                                        "quirks_disposition": "record",
                                    },
                                )
                            self._record_invariant_violations(op)
                        elif idx is not None and node in self.statute.supplements:
                            self._replace_node_in_statute(node, new_node)
                            if label_changing_substitution:
                                _append_uk_replay_adjudication(
                                    self.adjudications_out,
                                    kind=_UK_REPLAY_SOURCE_LABEL_CHANGING_SUBSTITUTION_RESOLVED_RULE_ID,
                                    message=(
                                        "UK replay applied a source-owned label-changing "
                                        "substitution by replacing the old sibling with "
                                        "the new labelled payload."
                                    ),
                                    op=op,
                                    detail={
                                        "target": str(target),
                                        "source_label": str(node.label or ""),
                                        "replacement_label": str(new_node.label or ""),
                                        "family": "lineage_normalization",
                                        "blocking": False,
                                        "strict_disposition": "record",
                                        "quirks_disposition": "record",
                                    },
                                )
                            self._record_invariant_violations(op)
                    elif node_kind != "content" and new_kind == "content":
                        self._replace_text(node, new_node.text)
                    else:
                        existing_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
                        if existing_eid:
                            new_node.attrs["eId"] = existing_eid
                        if parent and idx is not None:
                            self._replace_node_in_statute(node, new_node)
                            self._record_invariant_violations(op)
                elif self._recover_source_carried_structured_tail_substitution(op, target, new_node):
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                elif uk_kind_matches(
                    node_kind=str(new_node.kind),
                    target_kind=_addr_leaf_kind(op.target) or "",
                    node_label=_clean_num(new_node.label or ""),
                    target_label=_clean_num(_addr_leaf_label(op.target) or ""),
                ) and _clean_num(new_node.label or "") == _clean_num(_addr_leaf_label(op.target) or ""):
                    # Some UK replace ops target a node that is missing from the
                    # base shape but present in the commensurable oracle shape
                    # (for example a collapsed section lead becoming an explicit
                    # subsection 1). If the replacement payload already matches
                    # the missing target leaf exactly, materialize it under the
                    # parent instead of silently dropping the replace.
                    leaf_kind = str(_addr_leaf_kind(op.target) or "").lower()
                    parent_target = LegalAddress(path=target.path[:-1], special=None)
                    parent_node, _, _ = self._find_node_by_target(parent_target)
                    inserted = False
                    if parent_node is not None and leaf_kind not in {"subparagraph", "item", "point"}:
                        inserted = self._insert_node_v2(op.target, new_node, op)
                    if inserted:
                        self._record_invariant_violations(op)
                        self._emit_top_section_snapshot(op)
                    else:
                        if self._malformed_target_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=self._malformed_target_gap_kind(target),
                                message="UK replay skipped replace: lowered target path is malformed.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._missing_parent_shape_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=self._missing_parent_shape_gap_kind(target),
                                message="UK replay skipped replace: immediate parent target path is structurally absent.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._schedule_paragraph_carrier_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=self._schedule_paragraph_carrier_gap_kind(target),
                                message="UK replay skipped replace: schedule paragraph carrier is structurally absent or wrapped.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._leading_blank_subparagraph_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_absent_sibling_range_gap",
                                message="UK replay skipped replace: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._missing_sibling_range_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_absent_sibling_range_gap",
                                message="UK replay skipped replace: target falls inside an absent sibling range under the parent path.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        if self._empty_descendant_shape_gap(target):
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_empty_descendant_shape_gap",
                                message="UK replay skipped replace: parent target exists but has no descendant structural shape.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "payload_kind": str(new_node.kind),
                                    "payload_label": new_node.label or "",
                                },
                            )
                            return
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_payload_mismatch",
                            message="UK replay skipped replace: payload could not be inserted by target path.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(new_node.kind),
                                "payload_label": new_node.label or "",
                            },
                        )
                else:
                    if _addr_leaf_kind(op.target) and (
                        str(new_node.kind or "").lower() != str(_addr_leaf_kind(op.target) or "").lower()
                        or _clean_num(new_node.label or "") != _clean_num(_addr_leaf_label(op.target) or "")
                    ):
                        kind = "uk_replay_replace_payload_target_leaf_mismatch_gap"
                        message = "UK replay skipped replace: payload does not match lowered target leaf."
                    elif self._malformed_target_gap(target):
                        kind = self._malformed_target_gap_kind(target)
                        message = "UK replay skipped replace: lowered target path is malformed."
                    elif self._missing_parent_shape_gap(target):
                        kind = self._missing_parent_shape_gap_kind(target)
                        message = "UK replay skipped replace: immediate parent target path is structurally absent."
                    elif self._missing_sectionlike_gap(target):
                        kind = "uk_replay_missing_sectionlike_range_gap"
                        message = "UK replay skipped replace: target falls inside an absent sectionlike range gap."
                    else:
                        kind = "uk_replay_target_not_found"
                        message = "UK replay skipped replace: target not found."
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=str(kind),
                        message=message,
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_missing",
                    message="UK replay skipped replace: payload missing.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            if target_found or node is not None:
                self._emit_top_section_snapshot(op)
        elif _action_name(op.action) in ("text_replace", "text_repeal"):
            text_patch = op.text_patch
            if text_patch is None:
                self._log(
                    f"  EXECUTOR: WARN text_replace/text_repeal op has no structured text patch — skipping {op.op_id}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_text_match_missing",
                    message="UK replay skipped text-based op: text_match missing.",
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                    },
                )
                return
            replacement = (
                text_patch.replacement
                if text_patch.kind in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.APPEND}
                and text_patch.replacement is not None
                else ""
            )
            if node:
                recovery_rule_ids: list[str] = []
                allow_crossheading_parent = any(
                    str(note)
                    in {
                        f"{_NOTE_TEXT_REWRITE_RULE}{_CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE}",
                        f"{_NOTE_TEXT_REWRITE_RULE}{_CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE}",
                    }
                    for note in (op.provenance_tags or ())
                )
                heading_carrier = self._heading_facet_carrier_for_target(
                    target,
                    node,
                    parent,
                    allow_crossheading_parent=allow_crossheading_parent,
                )
                if target.special is FacetKind.HEADING and heading_carrier is None:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_heading_facet_target_gap",
                        message=(
                            "UK replay skipped heading-facet text op: target "
                            "has no unique replay heading carrier."
                        ),
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
                    )
                    return
                if (
                    target.special is None
                    and self._recover_text_patch_on_implicit_first_subparagraph_parent_text(
                        op,
                        target,
                        text_patch,
                        replacement,
                    )
                ):
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                    return
                table_cell_selector = _table_cell_selector(op)
                if table_cell_selector is not None:
                    if str(table_cell_selector.get("selector_mode") or "") == "unique_entry_cells":
                        table_cells, table_cell_reason, table_cell_detail = resolve_unique_uk_table_entry_cells(
                            node,
                            table_cell_selector,
                        )
                        if not table_cells:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                                message=(
                                    "UK replay skipped multi-entry table text op: the "
                                    "source-owned table cell selector did not resolve."
                                ),
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "replacement_text": replacement,
                                    "selector": dict(table_cell_selector),
                                    "reason_code": table_cell_reason,
                                    **table_cell_detail,
                                    "family": "source_table_elaboration",
                                    "blocking": True,
                                    "strict_disposition": "block",
                                    "quirks_disposition": "record",
                                },
                            )
                            return
                        if text_patch.kind not in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.DELETE}:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                                message="UK replay skipped multi-entry table text op: unsupported text-patch kind.",
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "replacement_text": replacement,
                                    "selector": dict(table_cell_selector),
                                    "reason_code": "unsupported_multi_cell_text_patch_kind",
                                    **table_cell_detail,
                                    "family": "source_table_elaboration",
                                    "blocking": True,
                                    "strict_disposition": "block",
                                    "quirks_disposition": "record",
                                },
                            )
                            return
                        preimage_gaps = [
                            str(cell.text or "")[:240]
                            for cell in table_cells
                            if not self._node_text_patch_preimage_present(
                                cell,
                                text_patch.selector.match_text,
                                text_patch.selector.occurrence,
                                text_patch.selector.end_occurrence,
                            )
                        ]
                        if preimage_gaps:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                                message=(
                                    "UK replay skipped multi-entry table text op: at least one "
                                    "selected table cell lacked the source text preimage."
                                ),
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "replacement_text": replacement,
                                    "selector": dict(table_cell_selector),
                                    "reason_code": "multi_cell_text_preimage_gap",
                                    "preimage_gap_cells": tuple(preimage_gaps),
                                    **table_cell_detail,
                                    "family": "source_table_elaboration",
                                    "blocking": True,
                                    "strict_disposition": "block",
                                    "quirks_disposition": "record",
                                },
                            )
                            return
                        for table_cell in table_cells:
                            _new_cell, applied = self._apply_text_replace_on_node_text_only(
                                table_cell,
                                text_patch.selector.match_text,
                                replacement,
                                text_patch.selector.occurrence,
                                text_patch.selector.end_occurrence,
                            )
                            if not applied:
                                _append_uk_replay_adjudication(
                                    self.adjudications_out,
                                    kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                                    message=(
                                        "UK replay skipped multi-entry table text op: "
                                        "preflight passed but apply failed."
                                    ),
                                    op=op,
                                    detail={
                                        "action": _action_name(op.action),
                                        "target": str(target),
                                        "text_match": text_patch.selector.match_text,
                                        "replacement_text": replacement,
                                        "selector": dict(table_cell_selector),
                                        "reason_code": "multi_cell_text_apply_gap",
                                        **table_cell_detail,
                                        "family": "source_table_elaboration",
                                        "blocking": True,
                                        "strict_disposition": "block",
                                        "quirks_disposition": "record",
                                    },
                                )
                                return
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_table_entry_multi_cell_text_patch_resolved",
                            message="UK replay applied a source-owned text patch to multiple table cells.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                **table_cell_detail,
                                "family": "source_table_elaboration",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "apply",
                            },
                        )
                        target_key = str(target)
                        if target_key:
                            self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                        self._record_invariant_violations(op)
                        self._emit_top_section_snapshot(op)
                        return
                    table_cell, table_cell_reason, table_cell_detail = self._resolve_table_entry_inline_cell(
                        node,
                        table_cell_selector,
                    )
                    if table_cell is None:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                            message=(
                                "UK replay skipped table-entry text op: the source-owned "
                                "table cell selector did not resolve to a replay cell."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                "reason_code": table_cell_reason,
                                **table_cell_detail,
                                "family": "source_table_elaboration",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                    symbolic_detail: dict[str, Any] = {}
                    symbolic_reason = ""
                    if _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(text_patch.selector.match_text):
                        table_cell, applied, symbolic_reason, symbolic_detail = (
                            self._apply_source_carried_table_cell_paragraph_substitution(
                                table_cell,
                                text_patch.selector.match_text,
                                replacement,
                            )
                        )
                    elif (
                        text_patch.kind is TextPatchKindEnum.APPEND
                        and text_patch.selector.match_text == "TEXT_END"
                    ):
                        table_cell, applied = self._apply_text_append_on_node_text_only(
                            table_cell,
                            replacement,
                        )
                    else:
                        table_cell, applied = self._apply_text_replace_on_node_text_only(
                            table_cell,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    if applied:
                        if symbolic_detail:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind="uk_replay_source_carried_table_entry_paragraph_substitution_resolved",
                                message=(
                                    "UK replay applied a source-carried table-entry "
                                    "paragraph substitution to one resolved table cell."
                                ),
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "replacement_text": replacement,
                                    "selector": dict(table_cell_selector),
                                    **table_cell_detail,
                                    **symbolic_detail,
                                    "family": "source_table_elaboration",
                                    "blocking": False,
                                    "strict_disposition": "record",
                                    "quirks_disposition": "apply",
                                },
                            )
                    else:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                            message=(
                                "UK replay skipped table-entry text op: the selected "
                                "table cell lacked the source text preimage."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                "reason_code": symbolic_reason or "cell_text_preimage_gap",
                                **table_cell_detail,
                                **symbolic_detail,
                                "family": "source_table_elaboration",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                elif (
                    heading_carrier is None
                    and text_patch.kind is TextPatchKindEnum.REPLACE
                    and self._recover_source_carried_labeled_child_text_substitution(
                        op,
                        target,
                        node,
                        text_patch,
                        replacement,
                    )
                ):
                    applied = True
                    applied_rule_id = _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID
                elif heading_carrier is not None and text_patch.kind is TextPatchKindEnum.APPEND:
                    node, applied = self._apply_text_append_on_node_text_only(
                        heading_carrier,
                        replacement,
                    )
                elif (
                    text_patch.kind is TextPatchKindEnum.APPEND
                    and text_patch.selector.match_text == "TEXT_END"
                ):
                    node, applied = self._apply_text_append_on_subtree_text_end(
                        node,
                        replacement,
                    )
                elif heading_carrier is not None:
                    node, applied = self._apply_text_replace_on_node_text_only(
                        heading_carrier,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                else:
                    node, applied = self._apply_text_replace_on_subtree(
                        node,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                        recovery_rule_ids_out=recovery_rule_ids,
                    )
                applied_match = text_patch.selector.match_text
                applied_replacement = replacement
                applied_rule_id = ""
                for recovery_rule_id in recovery_rule_ids:
                    if recovery_rule_id == "uk_replay_definition_predicate_shall_construed_normalized":
                        message = (
                            "UK replay applied definition-entry text op after recognizing "
                            "the definition predicate variant 'shall be construed'."
                        )
                        family = "definition_entry_predicate_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_definition_entry_qualifier_phrase_normalized":
                        message = (
                            "UK replay applied definition-entry text op after recognizing "
                            "a qualifier phrase between the defined term and predicate."
                        )
                        family = "definition_entry_predicate_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_definition_entry_orphan_separator_normalized":
                        message = (
                            "UK replay applied definition-entry text op after normalizing "
                            "an orphan comma after a definition-entry separator."
                        )
                        family = "definition_entry_separator_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_definition_anchor_lexical_variant_recovered":
                        message = (
                            "UK replay applied definition-anchor text op after resolving "
                            "a narrow education/educational lexical variant in the source anchor."
                        )
                        family = "target_resolution_recovery"
                        strict_disposition = "block"
                    elif recovery_rule_id == "uk_replay_definition_anchor_parenthetical_translation_normalized":
                        message = (
                            "UK replay applied definition-anchor text op after recognizing "
                            "a parenthetical translation between the defined term and predicate."
                        )
                        family = "target_resolution_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_definition_anchor_qualifier_phrase_normalized":
                        message = (
                            "UK replay applied definition-anchor text op after recognizing "
                            "a qualifier phrase between the anchor term and predicate."
                        )
                        family = "target_resolution_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_definition_anchor_conjoined_term_normalized":
                        message = (
                            "UK replay applied definition-anchor text op after recognizing "
                            "the anchor as the final term in a conjoined definition entry."
                        )
                        family = "target_resolution_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_text_range_anchor_word_boundary_normalized":
                        message = (
                            "UK replay applied range text op after matching a quoted "
                            "single-word range anchor as a word token."
                        )
                        family = "text_match_recovery"
                        strict_disposition = "record"
                    elif recovery_rule_id == "uk_replay_labeled_child_end_range_applied":
                        message = (
                            "UK replay applied a text range from a parent text anchor "
                            "through the end of an explicitly labelled child provision."
                        )
                        family = "text_rewrite_recovery"
                        strict_disposition = "record"
                    else:
                        message = (
                            "UK replay applied text-based op after normalizing "
                            "a contextual selector anchor kind."
                        )
                        family = "text_match_recovery"
                        strict_disposition = "record"
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=recovery_rule_id,
                        message=message,
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "family": family,
                            "blocking": False,
                            "strict_disposition": strict_disposition,
                            "quirks_disposition": "record",
                        },
                    )
                if not applied:
                    if heading_carrier is not None:
                        heading_carrier, punctuation_applied = self._apply_text_replace_on_node_text_only(
                            heading_carrier,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                            allow_punctuation_spacing=True,
                        )
                    else:
                        node, punctuation_applied = self._apply_text_replace_on_subtree(
                            node,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                            allow_punctuation_spacing=True,
                        )
                    if punctuation_applied:
                        applied = True
                        applied_rule_id = "uk_replay_text_match_punctuation_space_normalized"
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=applied_rule_id,
                            message=(
                                "UK replay applied text-based op after normalizing "
                                "citation punctuation spacing in text_match."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "family": "text_match_recovery",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                            },
                        )
                if (
                    not applied
                    and _text_match_has_word_punctuation_elision_candidate(text_patch.selector.match_text)
                ):
                    if heading_carrier is not None:
                        heading_carrier, word_punctuation_applied = self._apply_text_replace_on_node_text_only(
                            heading_carrier,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                            allow_word_punctuation_elision=True,
                        )
                    else:
                        node, word_punctuation_applied = self._apply_text_replace_on_subtree(
                            node,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                            allow_word_punctuation_elision=True,
                        )
                    if word_punctuation_applied:
                        applied = True
                        applied_rule_id = "uk_replay_text_match_word_punctuation_elided"
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=applied_rule_id,
                            message=(
                                "UK replay applied text-based op after normalizing "
                                "word-internal apostrophe/hyphen elision in text_match."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "family": "text_match_recovery",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                            },
                        )
                if (
                    not applied
                    and text_patch.kind is TextPatchKindEnum.REPLACE
                    and bool(replacement)
                ):
                    if heading_carrier is not None:
                        (
                            heading_carrier,
                            numeric_comma_applied,
                            numeric_comma_anchor,
                        ) = self._apply_numeric_list_trailing_comma_anchor_on_node_text_only(
                            heading_carrier,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    else:
                        (
                            node,
                            numeric_comma_applied,
                            numeric_comma_anchor,
                        ) = self._apply_numeric_list_trailing_comma_anchor_on_subtree(
                            node,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    if numeric_comma_applied:
                        applied = True
                        applied_match = numeric_comma_anchor or text_patch.selector.match_text
                        applied_rule_id = "uk_replay_numeric_list_trailing_comma_anchor_normalized"
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=applied_rule_id,
                            message=(
                                "UK replay applied insertion-style text op after proving "
                                "a unique numeric list anchor whose source selector carried "
                                "a trailing comma absent before a conjunction in the target."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "applied_match": applied_match,
                                "replacement_text": replacement,
                                "family": "text_match_recovery",
                                "source_shape": "numeric_list_trailing_comma_before_conjunction",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                                "prior_same_target_text_patch_op_ids": tuple(
                                    self._applied_text_patch_targets.get(str(target), ())
                                ),
                                "prior_same_target_text_patch_count": len(
                                    self._applied_text_patch_targets.get(str(target), ())
                                ),
                            },
                        )
                if (
                    not applied
                    and text_patch.kind is TextPatchKindEnum.DELETE
                    and not replacement
                    and text_patch.selector.occurrence == 0
                    and text_patch.selector.end_occurrence == 0
                ):
                    rotated_match = _rotated_trailing_comma_omission_match(
                        text_patch.selector.match_text,
                        heading_carrier if heading_carrier is not None else node,
                    )
                    if rotated_match:
                        if heading_carrier is not None:
                            heading_carrier, rotated_comma_applied = self._apply_text_replace_on_node_text_only(
                                heading_carrier,
                                rotated_match,
                                replacement,
                                0,
                                0,
                            )
                        else:
                            node, rotated_comma_applied = self._apply_text_replace_on_subtree(
                                node,
                                rotated_match,
                                replacement,
                                0,
                                0,
                            )
                        if rotated_comma_applied:
                            applied = True
                            applied_match = rotated_match
                            applied_rule_id = "uk_replay_text_match_rotated_trailing_comma_omission"
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=applied_rule_id,
                                message=(
                                    "UK replay applied omission after proving a unique "
                                    "rotated trailing-comma selector preimage."
                                ),
                                op=op,
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "applied_match": rotated_match,
                                    "replacement_text": replacement,
                                    "family": "text_match_recovery",
                                    "source_shape": "trailing_comma_rotated_before_phrase",
                                    "blocking": False,
                                    "strict_disposition": "record",
                                    "quirks_disposition": "record",
                                },
                            )
                if not applied:
                    for frag_sub in _fragment_substitution(op) or []:
                        alt_match = str(frag_sub.get("original") or "").strip()
                        alt_replacement = str(frag_sub.get("replacement") or "")
                        if not alt_match or (
                            alt_match == text_patch.selector.match_text and alt_replacement == replacement
                        ):
                            continue
                        if heading_carrier is not None:
                            node, alt_applied = self._apply_text_replace_on_node_text_only(
                                node,
                                alt_match,
                                alt_replacement,
                                text_patch.selector.occurrence,
                                text_patch.selector.end_occurrence,
                            )
                        else:
                            node, alt_applied = self._apply_text_replace_on_subtree(
                                node,
                                alt_match,
                                alt_replacement,
                                text_patch.selector.occurrence,
                                text_patch.selector.end_occurrence,
                            )
                        if alt_applied:
                            applied = True
                            applied_match = alt_match
                            applied_replacement = alt_replacement
                            self._log(
                                f"  EXECUTOR: text_replace fallback in {node.kind} {node.label}: {alt_match!r} -> {alt_replacement!r}"
                            )
                            break
                if applied:
                    self._log(
                        f"  EXECUTOR: text_replace in {node.kind} {node.label}: {applied_match!r} -> {applied_replacement!r}"
                    )
                    if applied_rule_id:
                        self._log(f"  EXECUTOR: text_replace recovery rule: {applied_rule_id}")
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    already_rewritten = (
                        text_patch.kind is TextPatchKindEnum.REPLACE
                        and bool(replacement)
                        and (
                            _node_text_contains_text(node, replacement)
                            if heading_carrier is not None
                            else _subtree_contains_text(node, replacement)
                        )
                    )
                    if already_rewritten:
                        kind = "uk_replay_text_match_already_rewritten"
                        message = (
                            "UK replay skipped text-based op: text_match missing but "
                            "replacement text is already present in target subtree."
                        )
                    elif (
                        text_patch.kind is TextPatchKindEnum.REPLACE
                        and bool(replacement)
                        and _normalized_replacement_text_present(
                            replacement,
                            heading_carrier if heading_carrier is not None else node,
                        )
                    ):
                        kind = "uk_replay_text_match_replacement_normalized_present"
                        message = (
                            "UK replay skipped text-based op: text_match missing but "
                            "the normalized replacement text is already present in target subtree."
                        )
                    elif (
                        text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_")
                        and text_patch.kind is TextPatchKindEnum.DELETE
                        and _definition_entry_term_absent(text_patch.selector.match_text, node)
                    ):
                        kind = "uk_replay_definition_entry_already_absent_observed"
                        message = (
                            "UK replay observed a definition-entry repeal whose named "
                            "definition term is already absent from the target subtree."
                        )
                    elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_"):
                        kind = "uk_replay_definition_entry_shape_gap"
                        message = (
                            "UK replay skipped definition-entry text op: definition entry "
                            "could not be uniquely bounded in the target subtree."
                        )
                    elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_CHILD_"):
                        kind = "uk_replay_definition_child_shape_gap"
                        message = (
                            "UK replay skipped definition-child text op: definition child "
                            "could not be uniquely bounded in the target subtree."
                        )
                    elif (
                        target.special is FacetKind.HEADING
                        and heading_carrier is not None
                        and _UK_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID
                        in _text_rewrite_rule_ids_for_op(op)
                    ):
                        kind = "uk_replay_heading_respectively_all_occurrences_absent_observed"
                        message = (
                            "UK replay observed a respectively paired heading-facet rewrite "
                            "whose quoted preimage is absent from this heading carrier; the "
                            "source instruction applies wherever that expression occurs."
                        )
                    elif target.special is FacetKind.HEADING and heading_carrier is not None:
                        kind = "uk_replay_heading_text_preimage_gap"
                        message = (
                            "UK replay skipped heading-facet text op: heading carrier exists "
                            "but lacks the source text preimage."
                        )
                    elif str(target) in self._applied_text_patch_targets:
                        prior_count = len(self._applied_text_patch_targets.get(str(target), ()))
                        if prior_count > 1:
                            kind = "uk_replay_text_patch_preimage_drift_multi_prior_same_target"
                        else:
                            kind = "uk_replay_text_patch_preimage_drift"
                        message = (
                            "UK replay skipped text-based op: text_match missing after "
                            "an earlier same-target text patch changed the replay preimage."
                        )
                    elif self._broad_schedule_table_shape_gap(target, node):
                        if str(_addr_leaf_kind(target) or "").lower() == "part":
                            kind = "uk_replay_broad_schedule_part_table_shape_gap"
                        else:
                            kind = "uk_replay_broad_schedule_table_shape_gap"
                        message = (
                            "UK replay skipped text-based op: broad schedule target has no "
                            "table or provision structure carrying the text patch preimage."
                        )
                    elif not _normalized_replay_subtree_text(node):
                        kind = "uk_replay_text_target_empty_surface_gap"
                        message = (
                            "UK replay skipped text-based op: target subtree has no "
                            "replay-visible text carrying the text patch preimage."
                        )
                    elif _synthetic_text_selector(text_patch.selector.match_text):
                        kind = "uk_replay_text_match_synthetic_selector_gap"
                        message = (
                            "UK replay skipped text-based op: synthetic text selector "
                            "could not be resolved in the target subtree."
                        )
                    elif _non_substantive_text_selector(text_patch.selector.match_text):
                        kind = "uk_replay_text_match_non_substantive_selector_gap"
                        message = (
                            "UK replay skipped text-based op: non-substantive selector "
                            "could not be resolved in the target subtree."
                        )
                    elif _multi_fragment_text_selector(text_patch.selector.match_text):
                        kind = "uk_replay_text_match_multi_fragment_selector_gap"
                        message = (
                            "UK replay skipped text-based op: text_match appears to "
                            "combine multiple separated source fragments into one selector."
                        )
                    elif _normalized_text_match_present(text_patch.selector.match_text, node):
                        kind = "uk_replay_text_match_normalized_preimage_present_gap"
                        message = (
                            "UK replay skipped text-based op: exact text_match was missing "
                            "but an alphanumeric-normalized preimage is present in the target subtree."
                        )
                    elif _citation_stripped_text_match_present(text_patch.selector.match_text, node):
                        kind = "uk_replay_text_match_citation_tail_surface_gap"
                        message = (
                            "UK replay skipped text-based op: exact text_match was missing "
                            "but the target subtree appears to omit citation year/chapter tail text."
                        )
                    elif _citation_connector_elided_text_match_present(text_patch.selector.match_text, node):
                        kind = "uk_replay_text_match_citation_connector_surface_gap"
                        message = (
                            "UK replay skipped citation-list text op: exact text_match was missing "
                            "but the target subtree appears to elide connector words between citations."
                        )
                    elif _article_phrase_content_word_present(text_patch.selector.match_text, node):
                        kind = "uk_replay_text_match_article_phrase_surface_gap"
                        message = (
                            "UK replay skipped article-prefixed text op: exact text_match was missing "
                            "but the target subtree contains the selector's content word in a different phrase shape."
                        )
                    elif _monetary_amount_text_selector(text_patch.selector.match_text):
                        kind = "uk_replay_text_monetary_amount_preimage_gap"
                        message = (
                            "UK replay skipped monetary-amount text op: quoted amount preimage "
                            "is absent from the target subtree."
                        )
                    elif (
                        text_patch.kind is TextPatchKindEnum.DELETE
                        and _parenthetical_omission_text_selector(text_patch.selector.match_text)
                    ):
                        kind = "uk_replay_text_parenthetical_omission_preimage_gap"
                        message = (
                            "UK replay skipped parenthetical omission text op: quoted parenthetical "
                            "preimage is absent from the target subtree."
                        )
                    elif (
                        text_patch.kind is TextPatchKindEnum.REPLACE
                        and _text_patch_replacement_preserves_anchor(text_patch.selector.match_text, replacement)
                    ):
                        kind = "uk_replay_text_insert_anchor_preimage_gap"
                        message = (
                            "UK replay skipped insertion-style text op: the replacement preserves "
                            "the source anchor, but that anchor is absent from the target subtree."
                        )
                    else:
                        kind = "uk_replay_text_match_missing"
                        message = (
                            "UK replay skipped text-based op: text_match not found in target subtree."
                        )
                    self._log(
                        f"  EXECUTOR: WARN text_replace target found but text_match not in subtree: {text_patch.selector.match_text!r} in {node.kind} {node.label}"
                    )
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=kind,
                        message=message,
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "blocking": kind
                            in {
                                "uk_replay_broad_schedule_table_shape_gap",
                                "uk_replay_broad_schedule_part_table_shape_gap",
                                "uk_replay_table_shape_gap",
                                "uk_replay_definition_entry_shape_gap",
                                "uk_replay_heading_text_preimage_gap",
                                "uk_replay_text_target_empty_surface_gap",
                                "uk_replay_text_match_missing",
                                "uk_replay_text_insert_anchor_preimage_gap",
                                "uk_replay_text_monetary_amount_preimage_gap",
                                "uk_replay_text_parenthetical_omission_preimage_gap",
                                "uk_replay_text_match_article_phrase_surface_gap",
                                "uk_replay_text_patch_preimage_drift",
                                "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
                                "uk_replay_text_match_synthetic_selector_gap",
                                "uk_replay_text_match_normalized_preimage_present_gap",
                                "uk_replay_text_match_non_substantive_selector_gap",
                                "uk_replay_text_match_multi_fragment_selector_gap",
                                "uk_replay_text_match_citation_tail_surface_gap",
                                "uk_replay_text_match_citation_connector_surface_gap",
                            },
                            "strict_disposition": (
                                "block"
                                if kind
                                in {
                                    "uk_replay_broad_schedule_table_shape_gap",
                                    "uk_replay_broad_schedule_part_table_shape_gap",
                                    "uk_replay_table_shape_gap",
                                    "uk_replay_definition_entry_shape_gap",
                                    "uk_replay_heading_text_preimage_gap",
                                    "uk_replay_text_target_empty_surface_gap",
                                    "uk_replay_text_match_missing",
                                    "uk_replay_text_insert_anchor_preimage_gap",
                                    "uk_replay_text_monetary_amount_preimage_gap",
                                    "uk_replay_text_parenthetical_omission_preimage_gap",
                                    "uk_replay_text_match_article_phrase_surface_gap",
                                    "uk_replay_text_patch_preimage_drift",
                                    "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
                                    "uk_replay_text_match_synthetic_selector_gap",
                                    "uk_replay_text_match_normalized_preimage_present_gap",
                                    "uk_replay_text_match_non_substantive_selector_gap",
                                    "uk_replay_text_match_multi_fragment_selector_gap",
                                    "uk_replay_text_match_citation_tail_surface_gap",
                                    "uk_replay_text_match_citation_connector_surface_gap",
                                }
                                else "record"
                            ),
                            "quirks_disposition": "record",
                            "prior_same_target_text_patch_op_ids": tuple(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                            "prior_same_target_text_patch_count": len(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                            "target_container": _addr_container(target),
                            "target_granularity": _addr_leaf_kind(target) or "",
                            "source_shape": (
                                "broad_schedule_without_table_or_provision_structure"
                                if kind
                                in {
                                    "uk_replay_broad_schedule_table_shape_gap",
                                    "uk_replay_broad_schedule_part_table_shape_gap",
                                }
                                else "target_subtree_without_text_surface"
                                if kind == "uk_replay_text_target_empty_surface_gap"
                                else "heading_preimage_absent"
                                if kind == "uk_replay_heading_text_preimage_gap"
                                else "respectively_all_occurrences_heading_preimage_absent"
                                if kind == "uk_replay_heading_respectively_all_occurrences_absent_observed"
                                else "definition_entry_already_absent"
                                if kind == "uk_replay_definition_entry_already_absent_observed"
                                else "insert_anchor_preimage_absent"
                                if kind == "uk_replay_text_insert_anchor_preimage_gap"
                                else "monetary_amount_preimage_absent"
                                if kind == "uk_replay_text_monetary_amount_preimage_gap"
                                else "parenthetical_omission_preimage_absent"
                                if kind == "uk_replay_text_parenthetical_omission_preimage_gap"
                                else "article_phrase_content_word_surface_gap"
                                if kind == "uk_replay_text_match_article_phrase_surface_gap"
                                else "normalized_preimage_present"
                                if kind == "uk_replay_text_match_normalized_preimage_present_gap"
                                else "replacement_normalized_present"
                                if kind == "uk_replay_text_match_replacement_normalized_present"
                                else "multi_fragment_text_selector"
                                if kind == "uk_replay_text_match_multi_fragment_selector_gap"
                                else "citation_tail_surface_gap"
                                if kind == "uk_replay_text_match_citation_tail_surface_gap"
                                else "citation_connector_surface_gap"
                                if kind == "uk_replay_text_match_citation_connector_surface_gap"
                                else ""
                            ),
                            "target_text_preview": _replay_subtree_text_preview(node),
                            "target_text_normalized_preview": _normalized_replay_subtree_text(node)[:240],
                        },
                    )
            else:
                self._log(f"  EXECUTOR: WARN text_replace target not found: {op.target}")
                if self._recover_text_patch_on_empty_descendant_parent(op, target, text_patch, replacement):
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                elif self._recover_text_patch_on_implicit_first_subparagraph_parent_text(
                    op,
                    target,
                    text_patch,
                    replacement,
                ):
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                elif self._table_target_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_table_shape_gap",
                        message="UK replay skipped text-based op: table target has no structural table node.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._empty_descendant_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_empty_descendant_shape_gap",
                        message="UK replay skipped text-based op: parent target exists but has no descendant structural shape.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._target_under_repealed_prefix(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_repealed_target_gap",
                        message="UK replay skipped text-based op: target path was already repealed earlier in the chain.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._doubled_alpha_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped text-based op: target falls inside an absent doubled-alpha sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sibling_range_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped text-based op: target falls inside an absent sibling range under the parent path.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._annex_schedule_mismatch_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_annex_schedule_reference_gap",
                        message="UK replay skipped text-based op: Annex reference was lowered to a missing schedule root target.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._container_text_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_schedule_container_text_target_gap",
                        message="UK replay skipped text-based op: lowered target points at a missing schedule container instead of the textual descendant.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._subsection_alpha_text_target_gap(op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_subsection_descendant_target_collapse_gap",
                        message="UK replay skipped text-based op: lowered target collapsed a numeric subsection and alphabetic descendant into one subsection label.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._malformed_target_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._malformed_target_gap_kind(target),
                        message="UK replay skipped text-based op: lowered target path is malformed.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_schedule_branch_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_schedule_branch_gap",
                        message="UK replay skipped text-based op: schedule root branch is absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_parent_shape_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._missing_parent_shape_gap_kind(target),
                        message="UK replay skipped text-based op: immediate parent target path is structurally absent.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._schedule_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=self._schedule_paragraph_carrier_gap_kind(target),
                        message="UK replay skipped text-based op: schedule paragraph carrier is structurally absent or wrapped.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._recover_text_patch_on_direct_section_paragraph_child_text(
                    op,
                    target,
                    text_patch,
                    replacement,
                ):
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                elif self._direct_section_paragraph_carrier_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_direct_section_paragraph_carrier_gap",
                        message=(
                            "UK replay skipped text-based op: direct section paragraph "
                            "target is not represented as an addressable carrier in source XML."
                        ),
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
                    )
                elif self._leading_blank_subparagraph_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_absent_sibling_range_gap",
                        message="UK replay skipped text-based op: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif self._missing_sectionlike_gap(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_missing_sectionlike_range_gap",
                        message="UK replay skipped text-based op: target falls inside an absent sectionlike range gap.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                elif prior_kind := self._prior_same_target_gap_kind(target):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=prior_kind,
                        message="UK replay skipped text-based op: target already exhibited the same structural gap earlier in the chain.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
                else:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_target_not_found",
                        message="UK replay skipped text-based op: target not found.",
                        op=op,
                        detail={"action": _action_name(op.action), "target": str(target)},
                    )
        elif _action_name(op.action) == "insert":
            if op.payload is not None:
                if self._crossheading_insert_target_gap(target, op):
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_crossheading_target_gap",
                        message=(
                            "UK replay skipped crossheading insert: target has no explicit "
                            "crossheading identity or placement anchor."
                        ),
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(op.payload.kind),
                            "payload_text": (op.payload.text or "")[:200],
                        },
                    )
                    return
                if self._existing_target_insert_gap(target, node, op):
                    if self._existing_target_insert_already_materialized(node, op):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_existing_target_already_materialized",
                            message=(
                                "UK replay skipped insert: target already exists with the same "
                                "normalized payload text."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                                "target_resolution_recovery": insert_existing_target_resolution,
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                    if conflict_detail := self._existing_target_insert_conflict_detail(node, op):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_existing_target_conflict_gap",
                            message=(
                                "UK replay skipped insert: target path already exists with "
                                "different normalized payload text."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                                "target_resolution_recovery": insert_existing_target_resolution,
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                                **conflict_detail,
                            },
                        )
                        return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_existing_target_gap",
                        message="UK replay skipped insert: target path already exists before applying the op.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(op.payload.kind),
                            "payload_label": op.payload.label or "",
                            "target_resolution_recovery": insert_existing_target_resolution,
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
                    )
                    return
                # Clone payload so repeated ops (same source for multiple targets) don't share nodes
                inserted = self._insert_node_v2(
                    target,
                    UKMutableNode.from_dict(op.payload.to_jsonable_dict()),
                    op,
                )
                if inserted:
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                else:
                    if _schedule_list_entry_table_rows_selector(op) is not None:
                        return
                    if _schedule_list_entry_selector(op) is not None:
                        return
                    if self._malformed_target_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._malformed_target_gap_kind(target),
                            message="UK replay skipped insert: lowered target path is malformed.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._missing_parent_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._missing_parent_shape_gap_kind(target),
                            message="UK replay skipped insert: immediate parent target path is structurally absent.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._schedule_paragraph_carrier_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=self._schedule_paragraph_carrier_gap_kind(target),
                            message="UK replay skipped insert: schedule target expects a paragraph carrier that is absent or wrapped by legacy p1group structure.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._leading_blank_subparagraph_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped insert: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._missing_sibling_range_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_absent_sibling_range_gap",
                            message="UK replay skipped insert: target falls inside an absent sibling range under the parent path.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    if self._empty_descendant_shape_gap(target):
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_empty_descendant_shape_gap",
                            message="UK replay skipped insert: parent target exists but has no descendant structural shape.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "payload_kind": str(op.payload.kind),
                                "payload_label": op.payload.label or "",
                            },
                        )
                        return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_payload_mismatch",
                        message="UK replay skipped insert: payload could not be inserted by target path.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "payload_kind": str(op.payload.kind),
                            "payload_label": op.payload.label or "",
                        },
                    )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_payload_missing",
                    message="UK replay skipped insert: payload missing.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
        elif _action_name(op.action) == "renumber":
            if self._apply_same_provision_descendant_renumber(op):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
                return
            if self._apply_same_parent_sibling_renumber(op):
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
                return
            self._log(f"  EXECUTOR: unsupported renumber shape — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail={
                    "action": _action_name(op.action),
                    "target": str(target),
                    "destination": str(op.destination) if op.destination is not None else "",
                },
            )
        elif _action_name(op.action) == "unknown":
            self._log(f"  EXECUTOR: unknown action — skipping {op.op_id}")
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_unsupported_action",
                message="UK replay skipped unsupported action.",
                op=op,
                detail={"action": _action_name(op.action), "target": str(target)},
            )
        else:
            raise ValueError(
                f"UKReplayExecutor.apply_op: unhandled action {op.action!r} "
                f"on op {op.op_id}. This is a programming error — every action "
                f"type must be explicitly handled (even if only to skip+warn)."
            )

    def _apply_text_replace_on_subtree(
        self,
        node: UKMutableNode,
        match: str,
        replacement: str,
        occurrence: int,
        end_occurrence: int = 0,
        *,
        allow_punctuation_spacing: bool = False,
        allow_word_punctuation_elision: bool = False,
        recovery_rule_ids_out: Optional[list[str]] = None,
    ) -> tuple[UKMutableNode, bool]:
        """Walk the subtree rooted at *node*, find *match* in text fields, and substitute.

        Args:
            node:        Root of the IR subtree to search.
            match:       Exact string to find (case-sensitive first, then whitespace-
                         normalized fallback, consistent with _apply_text_substitution_on_node).
            replacement: String to substitute in place of *match*.
            occurrence:  0 = replace all occurrences across the subtree.
                         N > 0 = replace only the Nth occurrence (1-based, document order).
                         -1 = replace only the last occurrence in document order.

        Returns:
            True if at least one substitution was made; False otherwise.
        """
        # Collect all nodes with text in document order (pre-order traversal)
        text_nodes: list[tuple[tuple[int, ...], UKMutableNode]] = []

        def _collect(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
            if n.text:
                text_nodes.append((path, n))
            for i, child in enumerate(n.children):
                _collect(child, path + (i,))

        _collect(node)

        if match == "TEXT_OPENING_WORDS":
            if not node.text:
                return node, False
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match == "TEXT_BEGINNING":
            if not node.text:
                return node, False
            joiner = "" if replacement.endswith((" ", "(", "/", "-")) else " "
            rebuilt = dc_replace(node, text=f"{replacement}{joiner}{node.text}")
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith(f"TEXT_FROM_CHILD_END{US}"):
            parts = match.split(US, 3)
            if len(parts) != 4:
                return node, False
            child_kind = parts[1]
            child_label = parts[2]
            start_text = parts[3].strip()
            if not child_kind or not child_label or not start_text:
                return node, False
            direct_child_matches = [
                (index, child)
                for index, child in enumerate(node.children)
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            child_index, _child = direct_child_matches[0]
            text = node.text or ""
            if not text:
                return node, False
            ordinal = occurrence if occurrence > 0 else 1
            literal_matches = list(re.finditer(re.escape(start_text), text))
            if len(literal_matches) >= ordinal:
                start_match = literal_matches[ordinal - 1]
            else:
                pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return node, False
                start_match = matches[ordinal - 1]
            separators = list(re.finditer(r"[—–-]", text[start_match.end() :]))
            if not separators:
                return node, False
            separator = separators[-1]
            tail_start = start_match.end() + separator.end()
            prefix = text[: start_match.start()].rstrip()
            tail = text[tail_start:].strip()
            replacement_text = replacement.strip()
            if not replacement_text:
                new_text = f"{prefix} {tail}".strip()
            else:
                joiner_before = "" if not prefix or replacement_text.startswith((" ", ",", ".", ";", ":", ")")) else " "
                joiner_after = "" if not tail or replacement_text.endswith((" ", "(", "/", "-")) else " "
                new_text = f"{prefix}{joiner_before}{replacement_text}{joiner_after}{tail}".strip()
            rebuilt = dc_replace(
                node,
                text=" ".join(new_text.split()).strip(),
                children=tuple(node.children[child_index + 1 :]),
            )
            self._replace_node_in_statute(node, rebuilt)
            if recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_labeled_child_end_range_applied")
            return rebuilt, True

        if match.startswith("TEXT_BEFORE_CHILD_"):
            child_match = re.fullmatch(
                r"TEXT_BEFORE_CHILD_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            if not node.text:
                return node, False

            direct_child_matches = [
                child
                for child in node.children
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            rebuilt = dc_replace(node, text=replacement)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match == "TEXT_AFTER_AMENDMENT_INSERT_TO_END":
            text = node.text or ""
            insert_matches = list(re.finditer(r"\binsert\s*[—–-]", text, flags=re.I))
            if not insert_matches or not replacement:
                return node, False
            insert_match = insert_matches[-1]
            rebuilt = dc_replace(
                node,
                text=f"{text[: insert_match.end()].rstrip()} {replacement.strip()}",
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_IN_CHILDREN_"):
            child_match = re.fullmatch(
                rf"TEXT_IN_CHILDREN_([A-Za-z]+)_([0-9A-Za-z_]+){re.escape(US)}(.+)",
                match,
                flags=re.S,
            )
            if child_match is None or replacement:
                return node, False
            child_kind = child_match.group(1)
            child_labels = tuple(label for label in child_match.group(2).split("_") if label)
            original = child_match.group(3).strip()
            if not child_labels or not original:
                return node, False
            direct_matches: dict[str, tuple[int, UKMutableNode]] = {}
            for index, child in enumerate(node.children):
                kind_value = child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind)
                label_key = _clean_num(child.label or "")
                if kind_value == child_kind and label_key in child_labels and label_key not in direct_matches:
                    direct_matches[label_key] = (index, child)
                elif kind_value == child_kind and label_key in child_labels:
                    return node, False
            if set(direct_matches) != set(child_labels):
                return node, False

            def _delete_from_child_text(text: str) -> tuple[str, bool]:
                if original in text:
                    return text.replace(original, ""), True
                pattern = _text_patch_pattern(
                    original,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                new_text, count = re.subn(pattern, "", text, flags=re.I | re.S)
                return new_text, count > 0

            new_children = list(node.children)
            for label in child_labels:
                index, child = direct_matches[label]
                new_text, changed = _delete_from_child_text(child.text or "")
                if not changed:
                    return node, False
                new_children[index] = dc_replace(child, text=new_text)
            rebuilt = dc_replace(node, children=new_children)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_AFTER_CHILD_TAIL_"):
            child_match = re.fullmatch(
                r"TEXT_AFTER_CHILD_TAIL_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            direct_child_matches = [
                (index, child)
                for index, child in enumerate(node.children)
                if (child.kind.value if isinstance(child.kind, IRNodeKind) else str(child.kind))
                == child_kind
                and _clean_num(child.label or "") == _clean_num(child_label)
            ]
            if len(direct_child_matches) != 1:
                return node, False
            child_index, _ = direct_child_matches[0]
            if child_index != len(node.children) - 1:
                return node, False
            text = node.text or ""
            if not text:
                return node, False
            separator_matches = list(re.finditer(r"[—–-]", text))
            if not separator_matches:
                return node, False
            separator = separator_matches[-1]
            tail = text[separator.end() :].strip()
            replacement_text = str(replacement or "").strip()
            if not tail:
                return node, False
            if not replacement_text and not re.match(r"(?:,?\s*)?(?:and|or)\b|[“\"'‘]", tail, flags=re.I):
                return node, False
            if replacement_text:
                joiner = "" if replacement_text.startswith((" ", ",", ".", ";", ":", ")")) else " "
                rebuilt_text = f"{text[: separator.end()].rstrip()}{joiner}{replacement_text}".rstrip()
            else:
                rebuilt_text = text[: separator.end()].rstrip()
            rebuilt = dc_replace(node, text=rebuilt_text)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_AFTER_CHILD_"):
            child_match = re.fullmatch(
                r"TEXT_AFTER_CHILD_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if child_match is None:
                return node, False
            child_kind = child_match.group(1)
            child_label = child_match.group(2)
            anchor_path: Optional[tuple[int, ...]] = None
            recovered_anchor_kind = False

            def _find_child_anchor(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
                nonlocal anchor_path
                if anchor_path is not None:
                    return
                kind_value = n.kind.value if isinstance(n.kind, IRNodeKind) else str(n.kind)
                if kind_value == child_kind and _clean_num(n.label or "") == _clean_num(child_label):
                    anchor_path = path
                    return
                for i, child in enumerate(n.children):
                    _find_child_anchor(child, path + (i,))

            def _node_at_child_path(n: UKMutableNode, path: tuple[int, ...]) -> UKMutableNode:
                current = n
                for idx in path:
                    current = current.children[idx]
                return current

            _find_child_anchor(node)
            if anchor_path is None:
                return node, False
            target_node = _node_at_child_path(node, anchor_path)
            joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
            new_text = f"{(target_node.text or '').rstrip()}{joiner}{replacement}".rstrip()
            rebuilt = self._replace_descendant_at_path(
                node,
                anchor_path,
                dc_replace(target_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_BEFORE_DEFINITION_"):
            term = match[len("TEXT_BEFORE_DEFINITION_") :].strip()
            if not term:
                return node, False
            full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
            if not full_text:
                return node, False
            term_pattern = _text_patch_pattern(
                term,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            definition_pattern = re.compile(
                rf"(?P<prefix>^|[;\.—–-]\s*)"
                rf"(?P<body>[“\"'‘]?\s*{term_pattern}\s*[”\"'’]?(?:\s*[,;:])?\s+)",
                re.I | re.S,
            )
            definition_match = definition_pattern.search(full_text)
            if definition_match is None:
                return node, False
            insert_at = definition_match.start("body")
            joiner = "" if replacement.endswith(" ") else " "
            new_text = f"{full_text[:insert_at]}{replacement}{joiner}{full_text[insert_at:]}"
            rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_IN_DEFINITION_") and not match.startswith("TEXT_IN_DEFINITION_CHILD_"):
            parts = match[len("TEXT_IN_DEFINITION_") :].split(US)
            if len(parts) == 2 and parts[1] == "AT_END":
                term = parts[0].strip()
                if not term:
                    return node, False

                def _insert_at_end_of_definition(text: str) -> tuple[str, bool]:
                    term_pattern = _text_patch_pattern(
                        term,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    definition_start_pattern = re.compile(
                        rf"""
                        (?P<prefix>(?:^|[;\.,\u2014\u2013-]\s*))
                        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                        (?:\s*\([^;]*?\))*
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |shall\s+be\s+construed
                            |includes
                        )\b
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    starts = list(definition_start_pattern.finditer(text))
                    if len(starts) != 1:
                        return text, False
                    definition_start = starts[0]
                    next_definition_pattern = re.compile(
                        r"""
                        [;\.,]\s*
                        [“"'\u2018][^”"'\u2019;]{1,160}[”"'\u2019]
                        (?:\s*\([^;]*?\))*
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |shall\s+be\s+construed
                            |includes
                        )\b
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    next_definition = next_definition_pattern.search(text, definition_start.end())
                    if next_definition is not None:
                        insert_at = next_definition.start()
                    else:
                        terminal = re.search(r"\s*[;,.]\s*$", text)
                        insert_at = terminal.start() if terminal is not None else len(text)
                    joiner = (
                        ""
                        if insert_at == 0
                        or text[:insert_at].endswith((" ", "\t", "\n", "\r"))
                        or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                        else " "
                    )
                    new_text = f"{text[:insert_at]}{joiner}{replacement}{text[insert_at:]}"
                    return " ".join(new_text.split()).strip(), True

                if node.text:
                    new_text, changed = _insert_at_end_of_definition(node.text)
                    if changed:
                        rebuilt = dc_replace(node, text=new_text)
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True

                candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
                for path, text_node in text_nodes:
                    if not text_node.text:
                        continue
                    new_text, changed = _insert_at_end_of_definition(text_node.text)
                    if changed:
                        candidate_paths.append((path, text_node, new_text))
                if len(candidate_paths) != 1:
                    return node, False
                path, text_node, new_text = candidate_paths[0]
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(text_node, text=new_text),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if len(parts) == 4 and parts[1] == "FROM" and parts[3] == "TO_END":
                term = parts[0].strip()
                start_anchor = parts[2].strip()
                if not term or not start_anchor:
                    return node, False

                def _rewrite_range_to_end_in_definition(text: str) -> tuple[str, bool]:
                    term_pattern = _text_patch_pattern(
                        term,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    definition_pattern = re.compile(
                        rf"""
                        (?P<prefix>(?:^|[;\.]\s*))
                        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |shall\s+be\s+construed
                            |includes
                        )\b
                        .*?
                        (?P<terminator>;|$)
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    definition_matches = list(definition_pattern.finditer(text))
                    if len(definition_matches) != 1:
                        return text, False
                    definition_match = definition_matches[0]
                    entry_text = definition_match.group(0)
                    start_pattern = _text_patch_pattern(
                        start_anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    start_matches = list(re.finditer(start_pattern, entry_text, flags=re.I | re.S))
                    if len(start_matches) != 1:
                        return text, False
                    start_match = start_matches[0]
                    terminator = str(definition_match.group("terminator") or "")
                    replacement_text = replacement.strip()
                    if terminator and not replacement_text.endswith(terminator):
                        replacement_text = f"{replacement_text}{terminator}"
                    rewritten_entry = entry_text[: start_match.start()].rstrip()
                    joiner = (
                        ""
                        if not rewritten_entry
                        or rewritten_entry.endswith((" ", "\t", "\n", "\r"))
                        or replacement_text.startswith((" ", ",", ".", ";", ":", ")"))
                        else " "
                    )
                    rewritten_entry = f"{rewritten_entry}{joiner}{replacement_text}"
                    new_text = (
                        text[: definition_match.start()]
                        + rewritten_entry
                        + text[definition_match.end() :]
                    )
                    return " ".join(new_text.split()).strip(), True

                if node.text:
                    new_text, changed = _rewrite_range_to_end_in_definition(node.text)
                    if changed:
                        rebuilt = dc_replace(node, text=new_text)
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True

                candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
                for path, text_node in text_nodes:
                    if not text_node.text:
                        continue
                    new_text, changed = _rewrite_range_to_end_in_definition(text_node.text)
                    if changed:
                        candidate_paths.append((path, text_node, new_text))
                if len(candidate_paths) != 1:
                    return node, False
                path, text_node, new_text = candidate_paths[0]
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(text_node, text=new_text),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if len(parts) == 5 and parts[1] == "FROM" and parts[3] == "TO":
                term = parts[0].strip()
                start_anchor = parts[2].strip()
                end_anchor = parts[4].strip()
                if not term or not start_anchor or not end_anchor:
                    return node, False

                def _rewrite_range_in_definition(text: str) -> tuple[str, bool]:
                    term_pattern = _text_patch_pattern(
                        term,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    definition_pattern = re.compile(
                        rf"""
                        (?P<prefix>(?:^|[;\.]\s*))
                        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |shall\s+be\s+construed
                            |includes
                        )\b
                        .*?
                        (?P<terminator>;|$)
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    definition_matches = list(definition_pattern.finditer(text))
                    if len(definition_matches) != 1:
                        return text, False
                    definition_match = definition_matches[0]
                    entry_text = definition_match.group(0)
                    start_pattern = _text_patch_pattern(
                        start_anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    start_ordinal = occurrence if occurrence > 0 else 1
                    start_matches = list(re.finditer(start_pattern, entry_text, flags=re.I | re.S))
                    if len(start_matches) < start_ordinal:
                        return text, False
                    start_match = start_matches[start_ordinal - 1]
                    end_pattern = _text_patch_pattern(
                        end_anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    end_matches = [
                        candidate
                        for candidate in re.finditer(end_pattern, entry_text, flags=re.I | re.S)
                        if candidate.start() >= start_match.end()
                    ]
                    end_ordinal = end_occurrence if end_occurrence > 0 else 1
                    if len(end_matches) < end_ordinal:
                        return text, False
                    end_match = end_matches[end_ordinal - 1]
                    rewritten_entry = (
                        entry_text[: start_match.start()]
                        + replacement
                        + entry_text[end_match.end() :]
                    )
                    new_text = (
                        text[: definition_match.start()]
                        + rewritten_entry
                        + text[definition_match.end() :]
                    )
                    return " ".join(new_text.split()).strip(), True

                if node.text:
                    new_text, changed = _rewrite_range_in_definition(node.text)
                    if changed:
                        rebuilt = dc_replace(node, text=new_text)
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True

                candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
                for path, text_node in text_nodes:
                    if not text_node.text:
                        continue
                    new_text, changed = _rewrite_range_in_definition(text_node.text)
                    if changed:
                        candidate_paths.append((path, text_node, new_text))
                if len(candidate_paths) != 1:
                    return node, False
                path, text_node, new_text = candidate_paths[0]
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(text_node, text=new_text),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if len(parts) == 3 and parts[1] == "AFTER_EACH":
                term = parts[0].strip()
                anchor = parts[2].strip()
                if not term or not anchor:
                    return node, False

                def _rewrite_each_anchor_in_definition_entry(text: str) -> tuple[str, bool]:
                    term_pattern = _text_patch_pattern(
                        term,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    definition_pattern = re.compile(
                        rf"""
                        (?P<prefix>(?:^|[;\.]\s*))
                        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |includes
                        )\b
                        .*?
                        (?P<terminator>;|$)
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    definition_matches = list(definition_pattern.finditer(text))
                    if len(definition_matches) != 1:
                        return text, False
                    definition_match = definition_matches[0]
                    entry_text = definition_match.group(0)
                    anchor_pattern = _text_patch_pattern(
                        anchor,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    anchor_matches = list(re.finditer(anchor_pattern, entry_text, flags=re.I | re.S))
                    if not anchor_matches:
                        return text, False
                    rewritten_entry = re.sub(anchor_pattern, replacement, entry_text, flags=re.I | re.S)
                    new_text = f"{text[:definition_match.start()]}{rewritten_entry}{text[definition_match.end():]}"
                    return " ".join(new_text.split()).strip(), True

                if node.text:
                    new_text, changed = _rewrite_each_anchor_in_definition_entry(node.text)
                    if changed:
                        rebuilt = dc_replace(node, text=new_text)
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True

                candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
                for path, text_node in text_nodes:
                    if not text_node.text:
                        continue
                    new_text, changed = _rewrite_each_anchor_in_definition_entry(text_node.text)
                    if changed:
                        candidate_paths.append((path, text_node, new_text))
                if len(candidate_paths) != 1:
                    return node, False
                path, text_node, new_text = candidate_paths[0]
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(text_node, text=new_text),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if len(parts) != 3 or parts[1] != "AFTER":
                return node, False
            term = parts[0].strip()
            anchor = parts[2].strip()
            if not term or not anchor:
                return node, False

            def _rewrite_anchor_in_definition_entry(text: str) -> tuple[str, bool]:
                term_pattern = _text_patch_pattern(
                    term,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                definition_pattern = re.compile(
                    rf"""
                    (?P<prefix>(?:^|[;\.]\s*))
                    [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |includes
                    )\b
                    .*?
                    (?P<terminator>;|$)
                    """,
                    flags=re.I | re.S | re.X,
                )
                definition_matches = list(definition_pattern.finditer(text))
                if len(definition_matches) != 1:
                    return text, False
                definition_match = definition_matches[0]
                entry_text = definition_match.group(0)
                anchor_pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                anchor_matches = list(re.finditer(anchor_pattern, entry_text, flags=re.I | re.S))
                if len(anchor_matches) != 1:
                    return text, False
                anchor_match = anchor_matches[0]
                rewritten_entry = (
                    entry_text[: anchor_match.start()]
                    + replacement
                    + entry_text[anchor_match.end() :]
                )
                new_text = f"{text[:definition_match.start()]}{rewritten_entry}{text[definition_match.end():]}"
                return " ".join(new_text.split()).strip(), True

            if node.text:
                new_text, changed = _rewrite_anchor_in_definition_entry(node.text)
                if changed:
                    rebuilt = dc_replace(node, text=new_text)
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

            candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
            for path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed = _rewrite_anchor_in_definition_entry(text_node.text)
                if changed:
                    candidate_paths.append((path, text_node, new_text))
            if len(candidate_paths) != 1:
                return node, False
            path, text_node, new_text = candidate_paths[0]
            rebuilt = self._replace_descendant_at_path(
                node,
                path,
                dc_replace(text_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_DEFINITION_CHILD_"):
            child_selector = match[len("TEXT_DEFINITION_CHILD_") :]
            child_parts = child_selector.split(US)
            if len(child_parts) != 2:
                return node, False
            kind_and_term, child_label = child_parts
            child_match = re.fullmatch(r"([A-Z]+)_(.+)", kind_and_term)
            if child_match is None:
                return node, False
            child_kind = child_match.group(1).lower()
            term = child_match.group(2).strip()
            child_label = child_label.strip()
            if child_kind != "paragraph" or not term or not child_label:
                return node, False

            def _definition_child_nodes(
                n: UKMutableNode,
                path: tuple[int, ...] = (),
            ) -> list[tuple[tuple[int, ...], UKMutableNode]]:
                matches: list[tuple[tuple[int, ...], UKMutableNode]] = []
                for index, child in enumerate(n.children):
                    child_path = path + (index,)
                    if (
                        child.kind is IRNodeKind.ITEM
                        and str(child.attrs.get("definition_child_label") or "").lower() == child_label.lower()
                        and child.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved"
                        and _normalize_text(str(child.attrs.get("definition_term") or "")) == _normalize_text(term)
                    ):
                        matches.append((child_path, child))
                    matches.extend(_definition_child_nodes(child, child_path))
                return matches

            structured_child_matches = _definition_child_nodes(node)
            if len(structured_child_matches) == 1:
                child_path, child_node = structured_child_matches[0]

                def _node_at_path(n: UKMutableNode, path: tuple[int, ...]) -> UKMutableNode:
                    current = n
                    for index in path:
                        current = current.children[index]
                    return current

                if replacement:
                    rebuilt_child = dc_replace(child_node, text=replacement.strip())
                    rebuilt = self._replace_descendant_at_path(node, child_path, rebuilt_child)
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True
                parent_path = child_path[:-1]
                child_index = child_path[-1]
                parent_node = _node_at_path(node, parent_path)
                new_children = list(parent_node.children)
                new_children.pop(child_index)
                rebuilt_parent = dc_replace(parent_node, children=new_children)
                rebuilt = (
                    rebuilt_parent
                    if not parent_path
                    else self._replace_descendant_at_path(node, parent_path, rebuilt_parent)
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if len(text_nodes) != 1:
                return node, False

            def _child_ordinal(label: str) -> Optional[int]:
                if len(label) == 1 and label.isalpha():
                    return ord(label.lower()) - ord("a") + 1
                if label.isdigit():
                    return int(label)
                return None

            def _rewrite_definition_child(text: str) -> tuple[str, bool]:
                ordinal = _child_ordinal(child_label)
                if ordinal is None or ordinal < 1:
                    return text, False
                term_pattern = _text_patch_pattern(
                    term,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                definition_start_pattern = re.compile(
                    rf"""
                    (?P<prefix>(?:^|[;\.]\s*))
                    [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |includes
                    )\b
                    """,
                    flags=re.I | re.S | re.X,
                )
                definition_starts = list(definition_start_pattern.finditer(text))
                if len(definition_starts) != 1:
                    return text, False
                definition_start = definition_starts[0]
                next_definition_pattern = re.compile(
                    r"""
                    [;\.]\s*
                    [“"'\u2018][^”"'\u2019;]{1,160}[”"'\u2019]
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |includes
                    )\b
                    """,
                    flags=re.I | re.S | re.X,
                )
                next_definition = next_definition_pattern.search(text, definition_start.end())
                entry_end = next_definition.start() + 1 if next_definition is not None else len(text)
                body_start = definition_start.end()
                entry_body = text[body_start:entry_end]
                semicolons = list(re.finditer(r";", entry_body))
                if len(semicolons) < ordinal:
                    return text, False
                segment_start = body_start
                if ordinal > 1:
                    segment_start = body_start + semicolons[ordinal - 2].end()
                segment_end = body_start + semicolons[ordinal - 1].end()
                before = text[:segment_start].rstrip()
                after = text[segment_end:].lstrip()
                if replacement:
                    old_segment = text[segment_start:segment_end]
                    terminator = (
                        ";"
                        if old_segment.rstrip().endswith(";")
                        and not replacement.rstrip().endswith(";")
                        else ""
                    )
                    new_segment = f"{replacement.strip()}{terminator}"
                    new_text = f"{before} {new_segment} {after}".strip()
                else:
                    new_text = f"{before} {after}".strip()
                return " ".join(new_text.split()).strip(), True

            text_path, text_node = text_nodes[0]
            new_text, changed = _rewrite_definition_child(text_node.text or "")
            if not changed:
                return node, False
            replacement_node = dc_replace(text_node, text=new_text)
            if not text_path:
                self._replace_node_in_statute(text_node, replacement_node)
                return replacement_node, True
            rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_IN_DEFINITION_CHILD_"):
            child_selector = match[len("TEXT_IN_DEFINITION_CHILD_") :]
            child_parts = child_selector.split(US)
            child_after_anchor = ""
            child_at_end = False
            if len(child_parts) == 4 and child_parts[2] == "AFTER":
                kind_and_term, child_label, _, child_after_anchor = child_parts
                original = ""
            elif len(child_parts) == 3:
                kind_and_term, child_label, original = child_parts
                child_at_end = original == "AT_END"
            else:
                return node, False
            child_match = re.fullmatch(r"([A-Z]+)_(.+)", kind_and_term)
            if child_match is None:
                return node, False
            child_kind = child_match.group(1).lower()
            term = child_match.group(2).strip()
            child_label = child_label.strip()
            original = original.strip()
            child_after_anchor = child_after_anchor.strip()
            if child_kind != "paragraph" or not term or not child_label:
                return node, False
            if (child_after_anchor and original) or (not child_after_anchor and not original and not child_at_end):
                return node, False

            def _definition_child_nodes(
                n: UKMutableNode,
                path: tuple[int, ...] = (),
            ) -> list[tuple[tuple[int, ...], UKMutableNode]]:
                matches: list[tuple[tuple[int, ...], UKMutableNode]] = []
                for index, child in enumerate(n.children):
                    child_path = path + (index,)
                    if (
                        child.kind is IRNodeKind.ITEM
                        and str(child.attrs.get("definition_child_label") or "").lower() == child_label.lower()
                        and child.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved"
                        and _normalize_text(str(child.attrs.get("definition_term") or "")) == _normalize_text(term)
                    ):
                        matches.append((child_path, child))
                    matches.extend(_definition_child_nodes(child, child_path))
                return matches

            structured_child_matches = _definition_child_nodes(node)
            if child_after_anchor:
                pattern = _text_patch_pattern(
                    child_after_anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
            elif re.fullmatch(r"[A-Za-z0-9]+", original):
                pattern = rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])"
            else:
                pattern = _text_patch_pattern(
                    original,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
            replacement_text = replacement or ""
            if len(structured_child_matches) == 1:
                child_path, child_node = structured_child_matches[0]
                child_text = child_node.text or ""
                if not child_text:
                    return node, False
                if child_at_end:
                    new_text = _append_definition_child_suffix_text(child_text, replacement_text)
                elif child_after_anchor:
                    required_occurrence = occurrence if occurrence > 0 else 1
                    matches = list(re.finditer(pattern, child_text, flags=re.I | re.S))
                    if len(matches) < required_occurrence:
                        return node, False
                    match_obj = matches[required_occurrence - 1]
                    new_text = (
                        child_text[: match_obj.start()]
                        + replacement_text
                        + child_text[match_obj.end() :]
                    )
                else:
                    new_text, count = re.subn(pattern, replacement_text, child_text, count=1, flags=re.I | re.S)
                    if count != 1:
                        return node, False
                rebuilt_child = dc_replace(child_node, text=" ".join(new_text.split()).strip())
                rebuilt = self._replace_descendant_at_path(node, child_path, rebuilt_child)
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            def _child_ordinal(label: str) -> Optional[int]:
                if len(label) == 1 and label.isalpha():
                    return ord(label.lower()) - ord("a") + 1
                if label.isdigit():
                    return int(label)
                return None

            def _rewrite_flat_definition_child(text: str) -> tuple[str, bool]:
                ordinal = _child_ordinal(child_label)
                if ordinal is None or ordinal < 1:
                    return text, False
                term_pattern = _text_patch_pattern(
                    term,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                definition_start_pattern = re.compile(
                    rf"""
                    (?P<prefix>(?:^|[;\.]\s*))
                    [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |includes
                    )\b
                    """,
                    flags=re.I | re.S | re.X,
                )
                definition_starts = list(definition_start_pattern.finditer(text))
                if len(definition_starts) != 1:
                    return text, False
                definition_start = definition_starts[0]
                next_definition_pattern = re.compile(
                    r"""
                    [;\.]\s*
                    [“"'\u2018][^”"'\u2019;]{1,160}[”"'\u2019]
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |includes
                    )\b
                    """,
                    flags=re.I | re.S | re.X,
                )
                next_definition = next_definition_pattern.search(text, definition_start.end())
                entry_end = next_definition.start() + 1 if next_definition is not None else len(text)
                body_start = definition_start.end()
                entry_body = text[body_start:entry_end]
                semicolons = list(re.finditer(r";", entry_body))
                if len(semicolons) < ordinal:
                    return text, False
                segment_start = body_start
                if ordinal > 1:
                    segment_start = body_start + semicolons[ordinal - 2].end()
                search_end = (
                    body_start + semicolons[ordinal - 1].end()
                    if child_after_anchor
                    else body_start + semicolons[ordinal].end()
                    if len(semicolons) > ordinal
                    else entry_end
                )
                segment = text[segment_start:search_end]
                if child_at_end:
                    new_segment = _append_definition_child_suffix_text(segment, replacement_text)
                    new_text = f"{text[:segment_start]}{new_segment}{text[search_end:]}"
                    return " ".join(new_text.split()).strip(), True
                matches = list(re.finditer(pattern, segment, flags=re.I | re.S))
                if child_after_anchor:
                    required_occurrence = occurrence if occurrence > 0 else 1
                    if len(matches) < required_occurrence:
                        return text, False
                    match_obj = matches[required_occurrence - 1]
                else:
                    if len(matches) != 1:
                        return text, False
                    match_obj = matches[0]
                absolute_start = segment_start + match_obj.start()
                absolute_end = segment_start + match_obj.end()
                new_text = f"{text[:absolute_start]}{replacement_text}{text[absolute_end:]}"
                return " ".join(new_text.split()).strip(), True

            candidate_rewrites: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
            for text_path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed = _rewrite_flat_definition_child(text_node.text)
                if changed:
                    candidate_rewrites.append((text_path, text_node, new_text))
            if len(candidate_rewrites) != 1:
                return node, False
            text_path, text_node, new_text = candidate_rewrites[0]
            replacement_node = dc_replace(text_node, text=new_text)
            if not text_path:
                self._replace_node_in_statute(text_node, replacement_node)
                return replacement_node, True
            rebuilt = self._replace_descendant_at_path(node, text_path, replacement_node)
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_AFTER_DEFINITION_"):
            definition_child_match = re.fullmatch(
                r"TEXT_AFTER_DEFINITION_([A-Z]+)_(.*)_AFTER_([0-9A-Za-z]+)",
                match,
            )
            if definition_child_match is not None:
                child_kind = definition_child_match.group(1).lower()
                term = definition_child_match.group(2).strip()
                child_label = definition_child_match.group(3).strip()
                if child_kind != "paragraph" or not term or not child_label:
                    return node, False

                def _definition_child_nodes(
                    n: UKMutableNode,
                    path: tuple[int, ...] = (),
                ) -> list[tuple[tuple[int, ...], UKMutableNode]]:
                    matches: list[tuple[tuple[int, ...], UKMutableNode]] = []
                    for index, child in enumerate(n.children):
                        child_path = path + (index,)
                        if (
                            child.kind is IRNodeKind.ITEM
                            and str(child.attrs.get("definition_child_label") or "").lower() == child_label.lower()
                            and child.attrs.get("source_rule_id") == "uk_definition_ordered_list_child_preserved"
                            and _normalize_text(str(child.attrs.get("definition_term") or "")) == _normalize_text(term)
                        ):
                            matches.append((child_path, child))
                        matches.extend(_definition_child_nodes(child, child_path))
                    return matches

                def _definition_insert_payload(
                    raw_replacement: str,
                ) -> tuple[str, list[UKMutableNode]]:
                    text = " ".join(raw_replacement.split()).strip(" ,.")
                    if not text:
                        return "", []
                    anchor_suffix = ""
                    suffix_match = re.match(r"^(?P<suffix>;\s+(?:or|and))\s+(?P<body>.+)$", text, flags=re.I | re.S)
                    if suffix_match is not None:
                        anchor_suffix = " ".join(suffix_match.group("suffix").split())
                        text = suffix_match.group("body").strip()
                    item_matches = list(
                        re.finditer(
                            r"(?:(?<=^)|(?<=;\s))(?P<label>[0-9A-Za-z]+)\s+(?P<body>[^;]+;)",
                            text,
                            flags=re.S,
                        )
                    )
                    items: list[UKMutableNode] = []
                    if item_matches and item_matches[0].start() == 0:
                        for item_match in item_matches:
                            label = item_match.group("label").strip()
                            item_text = " ".join(item_match.group("body").split()).strip()
                            if not label or not item_text:
                                return "", []
                            items.append(
                                UKMutableNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text=item_text,
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": term,
                                        "definition_child_label": label,
                                        "source_rule_detail": (
                                            "uk_effect_source_carried_definition_child_insert_text_patch"
                                        ),
                                    },
                                )
                            )
                    return anchor_suffix, items

                structured_child_matches = _definition_child_nodes(node)
                if len(structured_child_matches) == 1:
                    child_path, child_node = structured_child_matches[0]
                    parent_path = child_path[:-1]
                    child_index = child_path[-1]

                    def _node_at_path(n: UKMutableNode, path: tuple[int, ...]) -> UKMutableNode:
                        current = n
                        for index in path:
                            current = current.children[index]
                        return current

                    anchor_suffix, inserted_children = _definition_insert_payload(replacement)
                    if inserted_children:
                        parent_node = _node_at_path(node, parent_path)
                        new_children = list(parent_node.children)
                        if anchor_suffix:
                            anchor_text = " ".join(f"{child_node.text.rstrip()} {anchor_suffix}".split()).strip()
                            new_children[child_index] = dc_replace(child_node, text=anchor_text)
                        new_children[child_index + 1 : child_index + 1] = inserted_children
                        rebuilt_parent = dc_replace(parent_node, children=new_children)
                        rebuilt = (
                            rebuilt_parent
                            if not parent_path
                            else self._replace_descendant_at_path(node, parent_path, rebuilt_parent)
                        )
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True

                full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
                if not full_text:
                    return node, False
                term_pattern = re.escape(term).replace(r"\ ", r"\s+")
                definition_match = re.search(
                    rf"[“\"'‘]?\s*{term_pattern}\s*[”\"'’]?.*?\bmeans\b",
                    full_text,
                    flags=re.I | re.S,
                )
                if definition_match is None:
                    return node, False
                if len(child_label) == 1 and child_label.isalpha():
                    semicolon_ordinal = ord(child_label.lower()) - ord("a") + 1
                elif child_label.isdigit():
                    semicolon_ordinal = int(child_label)
                else:
                    return node, False
                tail = full_text[definition_match.end() :]
                semicolons = list(re.finditer(r";", tail))
                if len(semicolons) < semicolon_ordinal:
                    return node, False
                insert_at = definition_match.end() + semicolons[semicolon_ordinal - 1].end()
                joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
                new_text = f"{full_text[:insert_at]}{joiner}{replacement}{full_text[insert_at:]}"
                rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            term = match[len("TEXT_AFTER_DEFINITION_") :].strip()
            if not term:
                return node, False

            def _insert_after_definition_in_text(text: str) -> tuple[str, bool]:
                definition_start: Optional[re.Match[str]] = None
                recovered_anchor = False
                recovered_parenthetical_translation = False
                recovered_qualifier_phrase = False
                recovered_conjoined_term = False
                for candidate_term in (term, *_uk_definition_term_lexical_variants(term)):
                    term_pattern = _text_patch_pattern(
                        candidate_term,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    definition_start_pattern = re.compile(
                        rf"""
                        (?P<prefix>(?:^|[;\.,\u2014\u2013-]\s*|(?:\band\b|\bor\b)\s+))
                        [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                        (?P<parenthetical_translation>(?:\s*\([^;]*?\))*)
                        (?P<qualifier>\s*,\s*[^;]{{1,240}}?\s*,)?
                        \s+
                        (?:
                            means
                            |has\s+the\s+same\s+meaning\s+as
                            |has\s+the\s+meaning
                            |is\s+to\s+be\s+construed
                            |shall\s+be\s+construed
                            |includes
                        )\b
                        """,
                        flags=re.I | re.S | re.X,
                    )
                    definition_starts = list(definition_start_pattern.finditer(text))
                    if len(definition_starts) != 1:
                        continue
                    definition_start = definition_starts[0]
                    recovered_anchor = candidate_term != term
                    recovered_parenthetical_translation = bool(
                        str(definition_start.group("parenthetical_translation") or "").strip()
                    )
                    recovered_qualifier_phrase = bool(
                        str(definition_start.group("qualifier") or "").strip()
                    )
                    recovered_conjoined_term = bool(
                        re.fullmatch(
                            r"\s*(?:and|or)\s+",
                            str(definition_start.group("prefix") or ""),
                            flags=re.I,
                        )
                    )
                    break
                if definition_start is None:
                    return text, False
                next_definition_pattern = re.compile(
                    r"""
                    [;\.,]\s*
                    [“"'\u2018][^”"'\u2019;]{1,160}[”"'\u2019]
                    (?:\s*\([^;]*?\))*
                    \s+
                    (?:
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |shall\s+be\s+construed
                        |includes
                    )\b
                    """,
                    flags=re.I | re.S | re.X,
                )
                next_definition = next_definition_pattern.search(text, definition_start.end())
                if next_definition is not None:
                    insert_at = next_definition.start() + 1
                else:
                    insert_at = len(text)
                joiner = "" if replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
                new_text = f"{text[:insert_at]}{joiner}{replacement}{text[insert_at:]}"
                if recovered_anchor and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_anchor_lexical_variant_recovered"
                    )
                if recovered_parenthetical_translation and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_anchor_parenthetical_translation_normalized"
                    )
                if recovered_qualifier_phrase and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_anchor_qualifier_phrase_normalized"
                    )
                if recovered_conjoined_term and recovery_rule_ids_out is not None:
                    recovery_rule_ids_out.append(
                        "uk_replay_definition_anchor_conjoined_term_normalized"
                    )
                return " ".join(new_text.split()).strip(), True

            if node.text:
                new_text, changed = _insert_after_definition_in_text(node.text)
                if changed:
                    rebuilt = dc_replace(node, text=new_text)
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

            candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
            for path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed = _insert_after_definition_in_text(text_node.text)
                if changed:
                    candidate_paths.append((path, text_node, new_text))
            if len(candidate_paths) != 1:
                return node, False
            path, text_node, new_text = candidate_paths[0]
            rebuilt = self._replace_descendant_at_path(
                node,
                path,
                dc_replace(text_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_DEFINITION_ENTRY_"):
            term = match[len("TEXT_DEFINITION_ENTRY_") :].strip()
            if not term:
                return node, False

            def _rewrite_definition_entry(text: str) -> tuple[str, bool, tuple[str, ...]]:
                term_pattern = _text_patch_pattern(
                    term,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                definition_pattern = re.compile(
                    rf"""
                    (?P<prefix>(?:^|[;\.:\u2014]\s*,?\s*))
                    \s*
                    [“"'\u2018]?\s*{term_pattern}\s*[”"'\u2019]?
                    (?:\s*\([^;]*?\))*
                    (?P<qualifier>\s*,\s*[^;]{{1,240}}?\s*,)?
                    \s+
                    (?P<predicate>
                        means
                        |has\s+the\s+same\s+meaning\s+as
                        |has\s+the\s+meaning
                        |is\s+to\s+be\s+construed
                        |shall\s+be\s+construed
                        |includes
                    )\b
                    .*?
                    (?P<terminator>;|$)
                    """,
                    flags=re.I | re.S | re.X,
                )
                matches = list(definition_pattern.finditer(text))
                if len(matches) != 1:
                    return text, False, ()
                m = matches[0]
                predicate = " ".join(str(m.group("predicate") or "").lower().split())
                used_shall_construed = predicate == "shall be construed"
                used_qualifier = bool(str(m.group("qualifier") or "").strip())
                raw_prefix = m.group("prefix")
                used_orphan_separator = bool(re.search(r"[;\.:\u2014]\s*,\s*$", raw_prefix))
                prefix = re.sub(r"\s*,\s*$", " ", raw_prefix) if used_orphan_separator else raw_prefix
                if replacement:
                    replacement_prefix = "" if m.start() == 0 else prefix
                    joiner = "" if not replacement_prefix or replacement.startswith((" ", ",", ".", ";", ":", ")")) else " "
                    new_text = f"{text[:m.start()]}{replacement_prefix}{joiner}{replacement}{text[m.end():]}"
                else:
                    replacement_prefix = "" if m.start() == 0 or prefix.strip() == "." else prefix
                    new_text = f"{text[:m.start()]}{replacement_prefix}{text[m.end():]}"
                recovery_rule_ids = []
                if used_shall_construed:
                    recovery_rule_ids.append("uk_replay_definition_predicate_shall_construed_normalized")
                if used_qualifier:
                    recovery_rule_ids.append("uk_replay_definition_entry_qualifier_phrase_normalized")
                if used_orphan_separator:
                    recovery_rule_ids.append("uk_replay_definition_entry_orphan_separator_normalized")
                return " ".join(new_text.split()).strip(), True, tuple(recovery_rule_ids)

            if node.text:
                new_text, changed, definition_recovery_rule_ids = _rewrite_definition_entry(node.text)
                if changed:
                    if definition_recovery_rule_ids and recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.extend(definition_recovery_rule_ids)
                    rebuilt = dc_replace(node, text=new_text)
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

            candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str, tuple[str, ...]]] = []
            for path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed, definition_recovery_rule_ids = _rewrite_definition_entry(text_node.text)
                if changed:
                    candidate_paths.append((path, text_node, new_text, definition_recovery_rule_ids))
            if len(candidate_paths) != 1:
                return node, False
            path, text_node, new_text, definition_recovery_rule_ids = candidate_paths[0]
            if definition_recovery_rule_ids and recovery_rule_ids_out is not None:
                recovery_rule_ids_out.extend(definition_recovery_rule_ids)
            rebuilt = self._replace_descendant_at_path(
                node,
                path,
                dc_replace(text_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_WORD_"):
            target_contextual_match = re.fullmatch(
                r"TEXT_WORD_(.*?)_IMMEDIATELY_FOLLOWING_TARGET",
                match,
            )
            if target_contextual_match is not None:
                word = target_contextual_match.group(1)

                def _remove_trailing_target_word(text: str, needle: str) -> tuple[str, bool]:
                    pattern = re.compile(
                        rf"(?P<prefix>.*?)(?P<sep>\s*,?\s*){re.escape(needle)}(?P<suffix>\s*[,;:]?\s*)$",
                        re.I | re.S,
                    )
                    m = pattern.fullmatch(text)
                    if not m:
                        return text, False
                    return (m.group("prefix").rstrip() + m.group("suffix").rstrip()).rstrip(), True

                new_text, changed = _remove_trailing_target_word(node.text or "", word)
                if not changed:
                    return node, False
                rebuilt = dc_replace(node, text=new_text)
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            contextual_match = re.fullmatch(
                r"TEXT_WORD_(.*?)_IMMEDIATELY_(PRECEDING|FOLLOWING)_([A-Za-z]+)_([0-9A-Za-z]+)",
                match,
            )
            if contextual_match is None:
                return node, False
            word = contextual_match.group(1)
            relation = contextual_match.group(2)
            anchor_kind = contextual_match.group(3)
            anchor_label = contextual_match.group(4)
            anchor_path: Optional[tuple[int, ...]] = None
            recovered_anchor_kind = False

            def _find_anchor(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
                nonlocal anchor_path
                if anchor_path is not None:
                    return
                kind_value = n.kind.value if isinstance(n.kind, IRNodeKind) else str(n.kind)
                if kind_value == anchor_kind and _clean_num(n.label or "") == _clean_num(anchor_label):
                    anchor_path = path
                    return
                for i, child in enumerate(n.children):
                    _find_anchor(child, path + (i,))

            def _node_at_path(n: UKMutableNode, path: tuple[int, ...]) -> UKMutableNode:
                current = n
                for idx in path:
                    current = current.children[idx]
                return current

            def _remove_trailing_word(text: str, needle: str) -> tuple[str, bool]:
                pattern = re.compile(
                    rf"(?P<prefix>.*?)(?P<sep>\s*,?\s*){re.escape(needle)}(?P<suffix>\s*[,;:]?\s*)$",
                    re.I | re.S,
                )
                m = pattern.fullmatch(text)
                if not m:
                    return text, False
                return (m.group("prefix").rstrip() + m.group("suffix").rstrip()).rstrip(), True

            _find_anchor(node)
            if anchor_path is None:
                allowed_anchor_kinds = {
                    "paragraph",
                    "subparagraph",
                    "item",
                    "point",
                }
                if anchor_kind.lower() not in allowed_anchor_kinds:
                    return node, False

                candidate_paths: list[tuple[int, ...]] = []

                def _collect_label_anchors(n: UKMutableNode, path: tuple[int, ...] = ()) -> None:
                    kind_value = n.kind.value if isinstance(n.kind, IRNodeKind) else str(n.kind)
                    if kind_value in allowed_anchor_kinds and _clean_num(n.label or "") == _clean_num(anchor_label):
                        candidate_paths.append(path)
                    for i, child in enumerate(n.children):
                        _collect_label_anchors(child, path + (i,))

                _collect_label_anchors(node)
                if len(candidate_paths) != 1:
                    return node, False
                anchor_path = candidate_paths[0]
                recovered_anchor_kind = True
            target_path = anchor_path
            if relation == "PRECEDING":
                if not anchor_path:
                    return node, False
                sibling_idx = anchor_path[-1] - 1
                if sibling_idx < 0:
                    return node, False
                target_path = anchor_path[:-1] + (sibling_idx,)
            target_node = _node_at_path(node, target_path)
            new_text, changed = _remove_trailing_word(target_node.text or "", word)
            if not changed:
                return node, False
            if recovered_anchor_kind and recovery_rule_ids_out is not None:
                recovery_rule_ids_out.append("uk_replay_contextual_word_anchor_kind_normalized")
            rebuilt = self._replace_descendant_at_path(
                node,
                target_path,
                dc_replace(target_node, text=new_text),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_AFTER_") and match.endswith("_TO_END"):
            anchor = match[len("TEXT_AFTER_") : -len("_TO_END")]
            if not anchor:
                return node, False

            def _rewrite_after_anchor(text: str) -> tuple[str, bool]:
                ordinal = occurrence if occurrence > 0 else 1
                start = 0
                for _ in range(ordinal):
                    idx = text.find(anchor, start)
                    if idx == -1:
                        break
                    start = idx + len(anchor)
                else:
                    anchor_end = idx + len(anchor)
                    joiner = (
                        ""
                        if text[:anchor_end].endswith((" ", "\t", "\n", "\r"))
                        or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                        else " "
                    )
                    return f"{text[:anchor_end]}{joiner}{replacement}", True

                pattern = _text_patch_pattern(
                    anchor,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return text, False
                anchor_match = matches[ordinal - 1]
                joiner = (
                    ""
                    if text[: anchor_match.end()].endswith((" ", "\t", "\n", "\r"))
                    or replacement.startswith((" ", ",", ".", ";", ":", ")"))
                    else " "
                )
                return f"{text[: anchor_match.end()]}{joiner}{replacement}", True

            if node.text:
                new_text, changed = _rewrite_after_anchor(node.text)
                if changed:
                    rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip())
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

            candidate_paths: list[tuple[tuple[int, ...], UKMutableNode, str]] = []
            for path, text_node in text_nodes:
                if not text_node.text:
                    continue
                new_text, changed = _rewrite_after_anchor(text_node.text)
                if changed:
                    candidate_paths.append((path, text_node, new_text))
            if len(candidate_paths) != 1:
                return node, False
            path, text_node, new_text = candidate_paths[0]
            rebuilt = self._replace_descendant_at_path(
                node,
                path,
                dc_replace(text_node, text=" ".join(new_text.split()).strip()),
            )
            self._replace_node_in_statute(node, rebuilt)
            return rebuilt, True

        if match.startswith("TEXT_FROM_"):
            if "_TO_" in match and not match.endswith("_TO_END") and node.text:
                rebuilt, applied = self._apply_text_replace_on_node_text_only(
                    node,
                    match,
                    replacement,
                    occurrence,
                    end_occurrence,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                    recovery_rule_ids_out=recovery_rule_ids_out,
                )
                if applied:
                    return rebuilt, True

            full_text = " ".join(tn.text.strip() for _, tn in text_nodes if tn.text).strip()
            if not full_text:
                return node, False

            def _find_start_index(start_text: str) -> int:
                ordinal = occurrence if occurrence > 0 else 1
                if occurrence > 0:
                    range_matches, used_word_anchor = _range_anchor_matches(full_text, start_text)
                else:
                    range_matches = list(re.finditer(re.escape(start_text), full_text))
                    used_word_anchor = False
                if len(range_matches) >= ordinal:
                    if used_word_anchor and recovery_rule_ids_out is not None:
                        recovery_rule_ids_out.append("uk_replay_text_range_anchor_word_boundary_normalized")
                    return range_matches[ordinal - 1].start()
                pattern = _text_patch_pattern(
                    start_text,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                matches = list(re.finditer(pattern, full_text, flags=re.I | re.S))
                if len(matches) < ordinal:
                    return -1
                return matches[ordinal - 1].start()

            if match.endswith("_TO_END"):
                start_text = match[len("TEXT_FROM_") : -len("_TO_END")]
                start_idx = _find_start_index(start_text)
                if start_idx == -1:
                    return node, False
                new_text = full_text[:start_idx] + replacement
                rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            if "_TO_" in match:
                parts = match.replace("TEXT_FROM_", "", 1).split("_TO_", 1)
                if len(parts) == 2:
                    start_text, end_text = parts[0], parts[1]
                    start_idx = _find_start_index(start_text)
                    end_idx = -1
                    if start_idx != -1:
                        if end_occurrence > 0:
                            end_matches, used_word_end = _range_anchor_matches(full_text, end_text)
                            if len(end_matches) >= end_occurrence:
                                end_match = end_matches[end_occurrence - 1]
                                if end_match.start() >= start_idx + len(start_text):
                                    end_idx = end_match.start()
                                    end_end = end_match.end()
                                    if used_word_end and recovery_rule_ids_out is not None:
                                        recovery_rule_ids_out.append(
                                            "uk_replay_text_range_anchor_word_boundary_normalized"
                                        )
                        else:
                            end_idx = full_text.find(end_text, start_idx + len(start_text))
                            end_end = end_idx + len(end_text)
                    if start_idx == -1 or end_idx == -1:
                        start_pattern = _text_patch_pattern(
                            start_text,
                            allow_punctuation_spacing=allow_punctuation_spacing,
                            allow_word_punctuation_elision=allow_word_punctuation_elision,
                        )
                        start_matches = list(re.finditer(start_pattern, full_text, flags=re.I | re.S))
                        ordinal = occurrence if occurrence > 0 else 1
                        if len(start_matches) < ordinal:
                            return node, False
                        start_match = start_matches[ordinal - 1]
                        if end_occurrence > 0:
                            end_pattern = _text_patch_pattern(
                                end_text,
                                allow_punctuation_spacing=allow_punctuation_spacing,
                                allow_word_punctuation_elision=allow_word_punctuation_elision,
                            )
                            end_matches = list(re.finditer(end_pattern, full_text, flags=re.I | re.S))
                            if len(end_matches) < end_occurrence:
                                return node, False
                            end_match = end_matches[end_occurrence - 1]
                            if end_match.start() < start_match.end():
                                return node, False
                            new_text = full_text[: start_match.start()] + replacement + full_text[end_match.end() :]
                        else:
                            pattern = (
                                start_pattern
                                + r".*?"
                                + _text_patch_pattern(
                                    end_text,
                                    allow_punctuation_spacing=allow_punctuation_spacing,
                                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                                )
                            )
                            m = re.search(pattern, full_text, flags=re.I | re.S)
                            if not m:
                                return node, False
                            new_text = full_text[: m.start()] + replacement + full_text[m.end() :]
                    else:
                        new_text = full_text[:start_idx] + replacement + full_text[end_end:]
                    rebuilt = dc_replace(node, text=" ".join(new_text.split()).strip(), children=[])
                    self._replace_node_in_statute(node, rebuilt)
                    return rebuilt, True

        if occurrence == -1:
            last_exact_match: Optional[tuple[tuple[int, ...], UKMutableNode, int]] = None
            for path, tn in text_nodes:
                start = 0
                while True:
                    pos = tn.text.find(match, start)
                    if pos == -1:
                        break
                    last_exact_match = (path, tn, pos)
                    start = pos + len(match)
            if last_exact_match is not None:
                path, tn, pos = last_exact_match
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(tn, text=tn.text[:pos] + replacement + tn.text[pos + len(match) :]),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True

            pattern = _text_patch_pattern(
                match,
                allow_punctuation_spacing=allow_punctuation_spacing,
                allow_word_punctuation_elision=allow_word_punctuation_elision,
            )
            last_normalized_match: Optional[tuple[tuple[int, ...], UKMutableNode, re.Match[str]]] = None
            for path, tn in text_nodes:
                for m in re.finditer(pattern, tn.text, flags=re.I):
                    last_normalized_match = (path, tn, m)
            if last_normalized_match is not None:
                path, tn, m = last_normalized_match
                rebuilt = self._replace_descendant_at_path(
                    node,
                    path,
                    dc_replace(tn, text=tn.text[: m.start()] + replacement + tn.text[m.end() :]),
                )
                self._replace_node_in_statute(node, rebuilt)
                return rebuilt, True
            return node, False

        if occurrence == 0:
            # Replace all occurrences across all text nodes
            made_any = False
            rebuilt = node
            for path, tn in text_nodes:
                text = tn.text
                if match in text:
                    rebuilt = self._replace_descendant_at_path(
                        rebuilt,
                        path,
                        dc_replace(tn, text=text.replace(match, replacement)),
                    )
                    made_any = True
                else:
                    # Whitespace-normalized fallback (same as _apply_text_substitution_on_node)
                    pattern = _text_patch_pattern(
                        match,
                        allow_punctuation_spacing=allow_punctuation_spacing,
                        allow_word_punctuation_elision=allow_word_punctuation_elision,
                    )
                    new_text, count = re.subn(pattern, replacement, text, flags=re.I)
                    if count > 0:
                        rebuilt = self._replace_descendant_at_path(
                            rebuilt,
                            path,
                            dc_replace(tn, text=new_text),
                        )
                        made_any = True
            if made_any:
                self._replace_node_in_statute(node, rebuilt)
            return rebuilt, made_any
        else:
            # Replace only the Nth occurrence (1-based) — count across all text nodes in order
            global_count = 0
            for path, tn in text_nodes:
                text = tn.text
                # Count occurrences in this node's text
                start = 0
                while True:
                    pos = text.find(match, start)
                    if pos == -1:
                        break
                    global_count += 1
                    if global_count == occurrence:
                        rebuilt = self._replace_descendant_at_path(
                            node,
                            path,
                            dc_replace(tn, text=text[:pos] + replacement + text[pos + len(match) :]),
                        )
                        self._replace_node_in_statute(node, rebuilt)
                        return rebuilt, True
                    start = pos + len(match)
            # Whitespace-normalized fallback if exact search found nothing
            if global_count == 0:
                pattern = _text_patch_pattern(
                    match,
                    allow_punctuation_spacing=allow_punctuation_spacing,
                    allow_word_punctuation_elision=allow_word_punctuation_elision,
                )
                nth_seen = 0
                for path, tn in text_nodes:
                    for m in re.finditer(pattern, tn.text, flags=re.I):
                        nth_seen += 1
                        if nth_seen == occurrence:
                            rebuilt = self._replace_descendant_at_path(
                                node,
                                path,
                                dc_replace(tn, text=tn.text[: m.start()] + replacement + tn.text[m.end() :]),
                            )
                            self._replace_node_in_statute(node, rebuilt)
                            return rebuilt, True
            return node, False

    def _apply_text_substitution_on_node(self, node: UKMutableNode, subs: list[dict]) -> UKMutableNode:
        text = node.text or ""
        children = list(node.children)
        for s in subs:
            old, new = s["original"], s["replacement"]
            if old.startswith("FROM_") and "_TO_" in old:
                parts = old.replace("FROM_", "").split("_TO_")
                if len(parts) == 2:
                    start_label, end_label = parts[0].strip("()"), parts[1].strip("()")
                    start_idx = end_idx = -1
                    for i, child in enumerate(children):
                        if _clean_num(child.label or "") == _clean_num(start_label):
                            start_idx = i
                        if _clean_num(child.label or "") == _clean_num(end_label):
                            end_idx = i
                    if start_idx != -1 and end_idx != -1 and start_idx <= end_idx:
                        self._log(
                            f"  EXECUTOR: deleting children from '{start_label}' to '{end_label}' in {node.kind} {node.label}"
                        )
                        for i in range(end_idx, start_idx - 1, -1):
                            children.pop(i)
                continue
            if old in text:
                text = text.replace(old, new)
            else:
                pattern = re.escape(old).replace(r"\ ", r"\s+")
                new_text, count = re.subn(pattern, new, text, flags=re.I)
                if count > 0:
                    text = new_text
        rebuilt = dc_replace(node, text=text, children=list(children))
        self._replace_node_in_statute(node, rebuilt)
        return rebuilt

    def _insert_schedule_list_entry_table_rows(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        direction = str(selector.get("direction") or "")
        is_end_insert = direction == "end"
        unresolved_rule_id = (
            _UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_UNRESOLVED_RULE_ID
            if is_end_insert
            else _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_UNRESOLVED_RULE_ID
        )
        resolved_rule_id = (
            _UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_RESOLVED_RULE_ID
            if is_end_insert
            else _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_RESOLVED_RULE_ID
        )
        if _uk_kind_value(new_node.kind) != "table":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message="UK replay skipped schedule-list table-row insert: payload was not a table.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_not_table",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        schedule_node, _, _ = self._find_node_by_target(target)
        if schedule_node is None or _uk_kind_value(schedule_node.kind) != "schedule":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: target "
                    "did not resolve to a schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        direct_tables = [
            child
            for child in schedule_node.children
            if _uk_kind_value(child.kind) == "table"
        ]
        direct_entries = [
            child
            for child in schedule_node.children
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        if len(direct_tables) != 1 or direct_entries:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: schedule "
                    "was not represented by exactly one direct table and no "
                    "direct schedule-entry children."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_not_single_table_backed",
                    "table_count": len(direct_tables),
                    "direct_entry_count": len(direct_entries),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor_text = str(selector.get("anchor_text") or "")
        anchor_norm = _compact_normalized_text(anchor_text)
        article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor_text)
        citation_short_anchor_norm = _compact_schedule_entry_anchor_with_citation_short_title(anchor_text)
        if direction not in {"before", "after", "end"} or (not is_end_insert and not anchor_norm):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message="UK replay skipped schedule-list table-row insert: selector was invalid.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        table = direct_tables[0]
        payload_rows = [
            child
            for child in new_node.children
            if _uk_kind_value(child.kind) == "row"
        ]
        if is_end_insert:
            if not payload_rows:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=unresolved_rule_id,
                    message=(
                        "UK replay skipped schedule table end-row insert: "
                        "payload rows were absent."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "payload_empty",
                        "payload_row_count": len(payload_rows),
                        "family": "source_table_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            insert_index = len(table.children)
            for row in payload_rows:
                strip_uk_identity_attrs_recursive(row)
            children = list(table.children)
            children[insert_index:insert_index] = payload_rows
            self._replace_children(table, children)
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=resolved_rule_id,
                message=(
                    "UK replay inserted source-owned schedule table rows at "
                    "the end of the unique table-backed schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "explicit_schedule_end_unique_table",
                    "insert_index": insert_index,
                    "payload_row_count": len(payload_rows),
                    "family": "source_table_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
            return True
        matched_rows: list[tuple[int, str, str]] = []
        last_anchor_cell: UKMutableNode | None = None
        for row_index, row_cells in expanded_uk_table_rows_with_physical_index(table):
            anchor_cell = row_cells.get(1)
            if anchor_cell is None or anchor_cell is last_anchor_cell:
                continue
            last_anchor_cell = anchor_cell
            cell_text = str(anchor_cell.text or "")
            cell_norm = _compact_normalized_text(cell_text)
            cell_article_norm = _compact_schedule_entry_anchor_without_article(cell_text)
            cell_citation_short_norm = _compact_schedule_entry_anchor_with_citation_short_title(cell_text)
            match_mode = ""
            if cell_norm == anchor_norm:
                match_mode = "exact"
            elif cell_norm.startswith(anchor_norm):
                match_mode = "prefix"
            elif article_anchor_norm and cell_article_norm == article_anchor_norm:
                match_mode = "article"
            elif article_anchor_norm and cell_article_norm.startswith(article_anchor_norm):
                match_mode = "article_prefix"
            elif citation_short_anchor_norm and cell_norm == citation_short_anchor_norm:
                match_mode = "citation_short_title"
            elif citation_short_anchor_norm and cell_norm.startswith(citation_short_anchor_norm):
                match_mode = "citation_short_title_prefix"
            elif cell_citation_short_norm and cell_citation_short_norm == anchor_norm:
                match_mode = "cell_citation_short_title"
            elif cell_citation_short_norm and cell_citation_short_norm.startswith(anchor_norm):
                match_mode = "cell_citation_short_title_prefix"
            if match_mode:
                matched_rows.append((row_index, match_mode, cell_text[:240]))
        if len(matched_rows) != 1 or not payload_rows:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: anchor "
                    "row did not resolve uniquely or payload rows were absent."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_not_unique_or_payload_empty",
                    "anchor_match_count": len(matched_rows),
                    "payload_row_count": len(payload_rows),
                    "matching_rows": tuple(row[2] for row in matched_rows[:5]),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        row_index, match_mode, row_preview = matched_rows[0]
        insert_index = row_index if direction == "before" else row_index + 1
        for row in payload_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(table.children)
        children[insert_index:insert_index] = payload_rows
        self._replace_children(table, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=resolved_rule_id,
            message=(
                "UK replay inserted source-owned schedule table rows after "
                "resolving an explicit schedule-list entry anchor in the table."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchor_unique_in_schedule_table",
                "match_mode": match_mode,
                "matched_row": row_preview,
                "anchor_normalization_rule_id": (
                    _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ANCHOR_CITATION_SHORT_TITLE_RULE_ID
                    if "citation_short_title" in match_mode
                    else ""
                ),
                "insert_index": insert_index,
                "payload_row_count": len(payload_rows),
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _insert_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        if _uk_kind_value(new_node.kind) != "schedule_entry":
            return False
        carrier_node, _, _ = self._find_node_by_target(target)
        carrier_kind = _uk_kind_value(carrier_node.kind) if carrier_node is not None else ""
        if carrier_node is None or carrier_kind not in {"schedule", "part", "chapter", "division"}:
            if self._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repealed_target_gap",
                    message=(
                        "UK replay skipped schedule-list-entry insert: carrier "
                        "target was already repealed earlier in the chain."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "schedule_target_previously_repealed",
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: target did "
                    "not resolve to a schedule or schedule-partition carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor_norm = _compact_normalized_text(str(selector.get("anchor_text") or ""))
        direction = str(selector.get("direction") or "")
        if (not anchor_norm and direction != "alphabetical") or direction not in {"before", "after", "alphabetical"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: selector was "
                    "missing a valid anchor or direction."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False

        entry_rows: list[tuple[int, UKMutableNode]] = [
            (idx, child)
            for idx, child in enumerate(carrier_node.children)
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        if direction == "alphabetical":
            inserted_sort_key = _compact_schedule_entry_anchor_without_article(new_node.text)
            duplicate_matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_schedule_entry_anchor_without_article(child.text) == inserted_sort_key
            ]
            if not inserted_sort_key or duplicate_matches:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped alphabetical schedule-list-entry insert: "
                        "inserted entry text was missing or already present."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "alphabetical_position_duplicate_or_empty",
                        "entry_count": len(entry_rows),
                        "duplicate_match_count": len(duplicate_matches),
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            insert_index = len(carrier_node.children)
            for idx, child in entry_rows:
                child_sort_key = _compact_schedule_entry_anchor_without_article(child.text)
                if child_sort_key > inserted_sort_key:
                    insert_index = idx
                    break
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ALPHABETICAL_POSITION_RULE_ID,
                message=(
                    "UK replay placed a schedule-list-entry insert using the "
                    "source's explicit alphabetical-order instruction."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "explicit_alphabetical_order",
                    "entry_count": len(entry_rows),
                    "insert_index": insert_index,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
            for key in ("eId", "id"):
                new_node.attrs.pop(key, None)
            children = list(carrier_node.children)
            children.insert(insert_index, new_node)
            self._replace_children(carrier_node, children)
            return True

        matches = [
            (idx, child)
            for idx, child in entry_rows
            if _compact_normalized_text(child.text) == anchor_norm
        ]
        prefix_normalized = False
        article_normalized = False
        parenthetical_paragraph_normalized = False
        if not matches:
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_normalized_text(child.text).startswith(anchor_norm)
            ]
            prefix_normalized = len(matches) == 1
        if not matches:
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(
                str(selector.get("anchor_text") or "")
            )
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if article_anchor_norm
                and (
                    _compact_schedule_entry_anchor_without_article(child.text) == article_anchor_norm
                    or _compact_schedule_entry_anchor_without_article(child.text).startswith(article_anchor_norm)
                )
            ]
            article_normalized = len(matches) == 1
        if not matches and carrier_kind != "schedule":
            parenthetical_anchor = _schedule_entry_parenthetical_paragraph_anchor(
                str(selector.get("anchor_text") or "")
            )
            if parenthetical_anchor is not None:
                entry_text, paragraph_label = parenthetical_anchor
                entry_article_norm = _compact_schedule_entry_anchor_without_article(entry_text)
                matches = [
                    (idx, child)
                    for idx, child in entry_rows
                    if _clean_num(child.label or "") == paragraph_label
                    and entry_article_norm
                    and (
                        _compact_schedule_entry_anchor_without_article(child.text) == entry_article_norm
                        or _compact_schedule_entry_anchor_without_article(child.text).startswith(entry_article_norm)
                    )
                ]
                parenthetical_paragraph_normalized = len(matches) == 1
        if not matches:
            grouped_entry_rows: list[tuple[int, UKMutableNode, int, UKMutableNode]] = [
                (group_idx, group, child_idx, child)
                for group_idx, group in enumerate(carrier_node.children)
                if _uk_kind_value(group.kind) == "p1group"
                for child_idx, child in enumerate(group.children)
                if _uk_kind_value(child.kind) == "schedule_entry"
            ]

            def _matches_in_group(mode: str) -> list[tuple[int, UKMutableNode, int, UKMutableNode]]:
                if mode == "exact":
                    return [
                        row for row in grouped_entry_rows if _compact_normalized_text(row[3].text) == anchor_norm
                    ]
                if mode == "prefix":
                    return [
                        row
                        for row in grouped_entry_rows
                        if _compact_normalized_text(row[3].text).startswith(anchor_norm)
                    ]
                article_anchor_norm = _compact_schedule_entry_anchor_without_article(
                    str(selector.get("anchor_text") or "")
                )
                return [
                    row
                    for row in grouped_entry_rows
                    if article_anchor_norm
                    and (
                        _compact_schedule_entry_anchor_without_article(row[3].text) == article_anchor_norm
                        or _compact_schedule_entry_anchor_without_article(row[3].text).startswith(article_anchor_norm)
                    )
                ]

            grouped_matches: list[tuple[int, UKMutableNode, int, UKMutableNode]] = []
            grouped_match_mode = ""
            for mode in ("exact", "prefix", "article"):
                grouped_matches = _matches_in_group(mode)
                if grouped_matches:
                    grouped_match_mode = mode
                    break
            if len(grouped_matches) == 1:
                group_idx, group_node, child_idx, _child = grouped_matches[0]
                insert_index = child_idx if direction == "before" else child_idx + 1
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_GROUP_ANCHOR_RULE_ID,
                    message=(
                        "UK replay resolved a schedule-list-entry anchor inside "
                        "a schedule child group and inserted into that same group."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "anchor_unique_in_schedule_child_group",
                        "group_index": group_idx,
                        "group_kind": _uk_kind_value(group_node.kind),
                        "group_label": group_node.label or "",
                        "group_text": (group_node.text or "")[:200],
                        "group_entry_index": child_idx,
                        "group_insert_index": insert_index,
                        "grouped_entry_count": len(grouped_entry_rows),
                        "match_mode": grouped_match_mode,
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    },
                )
                for key in ("eId", "id"):
                    new_node.attrs.pop(key, None)
                group_children = list(group_node.children)
                group_children.insert(insert_index, new_node)
                group_node.children = group_children
                return True
            matches = [(idx, child) for idx, child in enumerate(grouped_matches)]
        if len(matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: anchor entry "
                    "did not resolve uniquely."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_not_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "grouped_entry_count": len(grouped_entry_rows) if "grouped_entry_rows" in locals() else 0,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        if article_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor after "
                    "normalizing a leading article."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_leading_article_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        if prefix_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor as the "
                    "unique prefix of a longer source entry."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_prefix_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        if parenthetical_paragraph_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PARENTHETICAL_PARAGRAPH_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry insert anchor "
                    "after validating its parenthetical paragraph label."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_parenthetical_paragraph_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )

        anchor_index = matches[0][0]
        insert_index = anchor_index if direction == "before" else anchor_index + 1
        for key in ("eId", "id"):
            new_node.attrs.pop(key, None)
        children = list(carrier_node.children)
        children.insert(insert_index, new_node)
        self._replace_children(carrier_node, children)
        return True

    def _repeal_schedule_list_entries(
        self,
        target: LegalAddress,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        carrier_node, _, _ = self._find_node_by_target(target)
        carrier_kind = _uk_kind_value(carrier_node.kind) if carrier_node is not None else ""
        if carrier_node is None or carrier_kind not in {"schedule", "part", "chapter", "division"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry repeal: target did "
                    "not resolve to a schedule or schedule-partition carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        raw_anchors = selector.get("anchors")
        anchors = tuple(str(anchor or "") for anchor in raw_anchors) if isinstance(raw_anchors, list) else ()
        anchors = tuple(anchor for anchor in anchors if _compact_normalized_text(anchor))
        if not anchors:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message="UK replay skipped schedule-list-entry repeal: selector had no entry anchors.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        entry_rows: list[tuple[UKMutableNode, int, UKMutableNode]] = []
        if carrier_kind == "schedule":
            entry_rows = [
                (carrier_node, idx, child)
                for idx, child in enumerate(carrier_node.children)
                if _uk_kind_value(child.kind) == "schedule_entry"
            ]
        else:
            def _collect_partition_entry_rows(parent: UKMutableNode) -> None:
                for idx, child in enumerate(parent.children):
                    child_kind = _uk_kind_value(child.kind)
                    if child_kind == "paragraph":
                        entry_rows.append((parent, idx, child))
                    elif child_kind in {"p1group", "pblock", "part", "chapter", "division"}:
                        _collect_partition_entry_rows(child)

            _collect_partition_entry_rows(carrier_node)

        def _entry_text_norm(child: UKMutableNode) -> str:
            if carrier_kind == "schedule":
                return _compact_normalized_text(child.text)
            return _compact_numbered_schedule_entry_text(child.text)

        def _entry_text_article_norm(child: UKMutableNode) -> str:
            if carrier_kind == "schedule":
                return _compact_schedule_entry_anchor_without_article(child.text)
            return _compact_numbered_schedule_entry_text_without_article(child.text)

        def _matches_for_anchor(anchor: str) -> tuple[list[tuple[UKMutableNode, int, UKMutableNode]], str]:
            anchor_norm = _compact_normalized_text(anchor)
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if _entry_text_norm(child) == anchor_norm
            ]
            if matches:
                return matches, "exact"
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if _entry_text_norm(child).startswith(anchor_norm)
            ]
            if matches:
                return matches, "prefix"
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor)
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if article_anchor_norm
                and (
                    _entry_text_article_norm(child) == article_anchor_norm
                    or _entry_text_article_norm(child).startswith(article_anchor_norm)
                )
            ]
            if matches:
                return matches, "article"
            if carrier_kind != "schedule":
                parenthetical_anchor = _schedule_entry_parenthetical_paragraph_anchor(anchor)
                if parenthetical_anchor is not None:
                    entry_text, paragraph_label = parenthetical_anchor
                    entry_article_norm = _compact_schedule_entry_anchor_without_article(entry_text)
                    matches = [
                        (parent, idx, child)
                        for parent, idx, child in entry_rows
                        if _clean_num(child.label or "") == paragraph_label
                        and entry_article_norm
                        and (
                            _entry_text_article_norm(child) == entry_article_norm
                            or _entry_text_article_norm(child).startswith(entry_article_norm)
                        )
                    ]
                    if matches:
                        return matches, "parenthetical_paragraph"
                numbered_anchor_norm = _compact_numbered_schedule_entry_text(anchor)
                matches = [
                    (parent, idx, child)
                    for parent, idx, child in entry_rows
                    if numbered_anchor_norm
                    and (
                        _entry_text_norm(child) == numbered_anchor_norm
                        or _entry_text_norm(child).startswith(numbered_anchor_norm)
                    )
                ]
                if matches:
                    return matches, "numbered"
                numbered_anchor_article_norm = _compact_numbered_schedule_entry_text_without_article(anchor)
                matches = [
                    (parent, idx, child)
                    for parent, idx, child in entry_rows
                    if numbered_anchor_article_norm
                    and (
                        _entry_text_article_norm(child) == numbered_anchor_article_norm
                        or _entry_text_article_norm(child).startswith(numbered_anchor_article_norm)
                    )
                ]
                if matches:
                    return matches, "numbered_article"
            return [], "none"

        matched_rows: list[tuple[UKMutableNode, int, UKMutableNode]] = []
        match_modes: dict[str, str] = {}
        for anchor in anchors:
            matches, mode = _matches_for_anchor(anchor)
            if len(matches) != 1:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped schedule-list-entry repeal: entry "
                        "anchor did not resolve uniquely."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "anchor": anchor,
                        "reason_code": "anchor_not_unique",
                        "anchor_match_count": len(matches),
                        "entry_count": len(entry_rows),
                        "carrier_kind": carrier_kind,
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            matched_rows.append(matches[0])
            match_modes[anchor] = mode
        matched_keys = tuple((id(parent), idx) for parent, idx, _child in matched_rows)
        if len(set(matched_keys)) != len(matched_keys):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry repeal: multiple "
                    "anchors resolved to the same entry child."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_collision",
                    "matched_indices": tuple(idx for _parent, idx, _child in matched_rows),
                    "carrier_kind": carrier_kind,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        rows_by_parent: dict[int, tuple[UKMutableNode, list[int]]] = {}
        for parent, idx, _child in matched_rows:
            key = id(parent)
            if key not in rows_by_parent:
                rows_by_parent[key] = (parent, [])
            rows_by_parent[key][1].append(idx)
        for parent, indices in rows_by_parent.values():
            children = list(parent.children)
            for idx in sorted(indices, reverse=True):
                children.pop(idx)
            self._replace_children(parent, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_RESOLVED_RULE_ID,
            message=(
                "UK replay applied a schedule-list-entry repeal after every "
                "source entry anchor resolved to exactly one direct schedule child."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchors_unique",
                "matched_indices": tuple(idx for _parent, idx, _child in matched_rows),
                "match_modes": match_modes,
                "normalization_rule_ids": tuple(
                    (
                        _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_PARENTHETICAL_PARAGRAPH_RULE_ID
                        if mode == "parenthetical_paragraph"
                        else _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_NUMBERED_ANCHOR_RULE_ID
                    )
                    for mode in match_modes.values()
                    if mode in {"parenthetical_paragraph", "numbered", "numbered_article"}
                ),
                "entry_count": len(entry_rows),
                "deleted_count": len(matched_rows),
                "carrier_kind": carrier_kind,
                "family": "source_schedule_list_entry_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _replace_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        schedule_node, _, _ = self._find_node_by_target(target)
        if schedule_node is None or _uk_kind_value(schedule_node.kind) != "schedule":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry replacement: target "
                    "did not resolve to a schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor = str(selector.get("anchor") or "")
        if not _compact_normalized_text(anchor):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message="UK replay skipped schedule-list-entry replacement: selector had no entry anchor.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        entry_rows: list[tuple[int, UKMutableNode]] = [
            (idx, child)
            for idx, child in enumerate(schedule_node.children)
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        anchor_norm = _compact_normalized_text(anchor)
        matches = [
            (idx, child)
            for idx, child in entry_rows
            if _compact_normalized_text(child.text) == anchor_norm
        ]
        match_mode = "exact"
        if not matches:
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_normalized_text(child.text).startswith(anchor_norm)
            ]
            match_mode = "prefix"
        if not matches:
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor)
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if article_anchor_norm
                and (
                    _compact_schedule_entry_anchor_without_article(child.text) == article_anchor_norm
                    or _compact_schedule_entry_anchor_without_article(child.text).startswith(article_anchor_norm)
                )
            ]
            match_mode = "article"
        if len(matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry replacement: entry "
                    "anchor did not resolve uniquely."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "anchor": anchor,
                    "reason_code": "anchor_not_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        replace_idx = matches[0][0]
        if match_mode == "prefix":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor as the "
                    "unique prefix of a longer source entry."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_prefix_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        elif match_mode == "article":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor after "
                    "normalizing a leading article."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_leading_article_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        for key in ("eId", "id"):
            new_node.attrs.pop(key, None)
        children = list(schedule_node.children)
        children[replace_idx] = new_node
        self._replace_children(schedule_node, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_RESOLVED_RULE_ID,
            message=(
                "UK replay applied a schedule-list-entry replacement after the "
                "source entry anchor resolved to exactly one direct schedule child."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchor_unique",
                "matched_index": replace_idx,
                "match_mode": match_mode,
                "entry_count": len(entry_rows),
                "family": "source_schedule_list_entry_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _insert_node_v2(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
    ) -> bool:
        from lawvm.uk_legislation.canonicalize import (
            uk_insert_into_children,
            uk_resolve_insertion_parent,
        )

        schedule_list_entry_table_rows_selector = _schedule_list_entry_table_rows_selector(op)
        if schedule_list_entry_table_rows_selector is not None:
            return self._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_list_entry_table_rows_selector,
            )
        schedule_table_end_rows_selector = _schedule_table_end_rows_selector(op)
        if schedule_table_end_rows_selector is not None:
            return self._insert_schedule_list_entry_table_rows(
                target,
                new_node,
                op,
                schedule_table_end_rows_selector,
            )
        schedule_list_entry_selector = _schedule_list_entry_selector(op)
        if schedule_list_entry_selector is not None:
            return self._insert_schedule_list_entry(target, new_node, op, schedule_list_entry_selector)
        table_column_insert_selector = _table_column_insert_selector(op)
        if table_column_insert_selector is not None:
            return self._insert_table_column(target, new_node, op, table_column_insert_selector)
        table_row_insert_selector = _table_row_insert_selector(op)
        if table_row_insert_selector is not None:
            return self._insert_table_entry_row(target, new_node, op, table_row_insert_selector)

        prec_eid = _preceding_eid(op)
        following_eid = _following_eid(op)
        parent_node, insert_idx = uk_resolve_insertion_parent(
            target=target,
            body_root=cast(IRNode, self.statute.body),
            node_kind=str(new_node.kind),
            node_label=new_node.label,
            preceding_eid=prec_eid,
            following_eid=following_eid,
            find_node_by_target=self._find_node_by_target,
            find_node_and_parent_statute=self._find_node_and_parent_statute,
            label_sort_key=_label_sort_key,
        )
        parent_node = cast(Optional[UKMutableNode], parent_node)
        target_eid = self._derive_target_eid(target)
        if target_eid and "eId" not in new_node.attrs and "id" not in new_node.attrs:
            new_node.attrs["eId"] = target_eid

        def _inherit_parent_local_eid(parent_node: UKMutableNode, candidate: UKMutableNode) -> UKMutableNode:
            parent_eid = str(parent_node.attrs.get("eId") or parent_node.attrs.get("id") or "")
            current_eid = str(candidate.attrs.get("eId") or candidate.attrs.get("id") or "")
            label = str(candidate.label or _addr_leaf_label(target) or "").strip()
            if not parent_eid or not label:
                return candidate
            if current_eid and (
                (current_eid == target_eid and _addr_container(target) == "schedule")
                or current_eid in self.eid_map.values()
            ):
                return candidate
            if target_eid and _addr_container(target) == "schedule":
                candidate.attrs["eId"] = target_eid
                return candidate
            candidate.attrs["eId"] = f"{parent_eid}-{label}"
            return candidate

        if parent_node and insert_idx is not None:
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} at routed index {insert_idx}")
            children = list(parent_node.children)
            children.insert(insert_idx, new_node)
            self._replace_children(parent_node, children)
            return True
        if parent_node:
            new_node = _inherit_parent_local_eid(parent_node, new_node)
            self._log(
                f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {parent_node.kind} {parent_node.label}"
            )
            return self._insert_child_sorted(parent_node, new_node)

        # Build parent address by dropping the last path segment.
        # Single-segment paths (e.g. section:2a) get parent = body/schedules directly,
        # matching the old IRTargetRef behaviour where parent_target.section=None caused
        # _find_node_by_target to return the body node for non-schedule containers.
        container = _addr_container(target)
        parent_addr = target.parent() if len(target.path) > 1 else None

        if parent_addr is not None:
            p_node, _, _ = self._find_node_by_target(parent_addr)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into {p_node.kind} {p_node.label}")
                return self._insert_child_sorted(p_node, new_node)
        elif container == "schedule":
            # Single-segment schedule target: the target IS the schedule — insert payload into it,
            # but only when the payload is a part, chapter, or section (structural containers
            # that appear as direct children of schedules).  Paragraph/subsection payloads
            # targeted at a whole schedule are likely table-row inserts (e.g. concordat
            # schedules) whose EIDs don't match oracle EIDs — fall through to the EID-derived
            # logic in those cases.
            #
            # A schedule payload targeted at a whole schedule path (for example
            # ``schedule:7a`` with payload kind ``schedule``) is a top-level
            # schedule insertion and must be added to ``statute.supplements``.
            # Falling through to the EID-derived parent lookup turns
            # ``schedule-7a`` into parent ``schedule`` and can incorrectly nest
            # the new schedule under an existing schedule branch like
            # ``schedule-7``.
            _sch_structural = {"part", "chapter", "section", "article", "p1group", "crossheading"}
            new_kind = str(new_node.kind).lower()
            if new_kind == "schedule":
                self._log(f"  EXECUTOR: inserting schedule {new_node.label} at top-level")
                return self._insert_supplement_sorted(new_node)
            if new_kind in _sch_structural:
                sch_node, _, _ = self._find_node_by_target(target)
                if sch_node:
                    sch_node = cast(UKMutableNode, sch_node)
                    new_node = _inherit_parent_local_eid(sch_node, new_node)
                    self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into schedule {sch_node.label}")
                    return self._insert_child_sorted(sch_node, new_node)
                return False
        else:
            # Single-segment non-schedule target: prefer inserting after the
            # nearest existing same-kind predecessor in its actual parent,
            # because UK body sections/articles often live under wrappers like
            # crossheading -> p1group rather than directly under body.
            pred_parent, pred_idx, pred_label = uk_find_body_predecessor_parent(
                cast(IRNode, self.statute.body),
                str(new_node.kind),
                new_node.label,
                label_sort_key=_label_sort_key,
            )
            if pred_parent is not None and pred_idx is not None:
                pred_parent = cast(UKMutableNode, pred_parent)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} after body predecessor {pred_label}")
                children: list[UKMutableNode] = list(pred_parent.children)
                children.insert(pred_idx + 1, new_node)
                self._replace_children(pred_parent, children)
                return True

            # No suitable predecessor exists in the body tree: fall back to a
            # true body-root insertion.
            self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into body (top-level)")
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
            return True

        if "-" in target_eid:
            parent_eid = "-".join(target_eid.split("-")[:-1])
            p_node, _, _ = self._find_node_and_parent_statute(parent_eid)
            if p_node:
                new_node = _inherit_parent_local_eid(p_node, new_node)
                self._log(f"  EXECUTOR: inserting {new_node.kind} {new_node.label} into parent {parent_eid}")
                return self._insert_child_sorted(cast(UKMutableNode, p_node), new_node)

        body_root_kinds = {
            "part",
            "chapter",
            "crossheading",
            "pblock",
            "division",
            "section",
            "article",
            "rule",
            "regulation",
            "p1group",
            "schedule",
        }
        new_kind = str(new_node.kind).lower()
        if new_kind not in body_root_kinds:
            self._log(
                "  EXECUTOR: WARN refusing impossible body-root fallback for "
                f"{new_node.kind} {new_node.label} target {target}"
            )
            return False
        self._log(f"  EXECUTOR: fallback inserting {new_node.kind} {new_node.label} into body")
        if new_kind == "schedule":
            supplements = list(self.statute.supplements)
            supplements.append(new_node)
            self._replace_statute(supplements=supplements)
            return True
        else:
            body_children: list[UKMutableNode] = list(self.statute.body.children)
            uk_insert_into_children(
                cast(list[IRNode], body_children),
                cast(IRNode, new_node),
                label_sort_key=_label_sort_key,
            )
            self.statute.body.children = body_children
            return True

    def _eid_candidate_matches_target_leaf(self, node: UKMutableNode, target: LegalAddress) -> bool:
        leaf_kind = _addr_leaf_kind(target)
        if not leaf_kind:
            return True
        return self._match_kind_label(node, str(leaf_kind), _addr_leaf_label(target))

    def _derive_target_eid(self, addr: LegalAddress) -> str:
        is_eur = self.statute.metadata.get("is_eur", False)
        container = _addr_container(addr)
        section = _addr_field(addr, "schedule") or _addr_field(addr, "section")
        part = _addr_field(addr, "part")
        chapter = _addr_field(addr, "chapter")
        if container == "schedule":
            paragraph, subsection, item_labels = _schedule_target_levels(addr)
        else:
            paragraph = None
            subsection = None
            item_labels = []

        def _get_candidates():
            parts: list[str] = []
            if container == "schedule":
                sch_prefix = "annex" if is_eur else "schedule"
                if section:
                    parts.append(f"{sch_prefix}-{_clean_num(section)}")
                else:
                    parts.append(sch_prefix)

                # EU specific: very flat scheme for Annexes
                if is_eur:
                    eu_parts = list(parts)
                    if paragraph:
                        eu_parts.append(f"paragraph-{_clean_num(paragraph)}")
                    if subsection:
                        eu_parts.append(_clean_num(subsection))
                    for item_label in item_labels:
                        eu_parts.append(_canonicalize_eid_tail_label(item_label))
                    yield "-".join(eu_parts)
                    # Reset parts for hierarchical try
                    parts = [f"{sch_prefix}-{_clean_num(section)}"] if section else [sch_prefix]

                if part:
                    parts.append(f"part-{_clean_num(part)}")
                if chapter:
                    parts.append(f"chapter-{_clean_num(chapter)}")
                if paragraph:
                    if is_eur:
                        parts.append(f"paragraph-{_clean_num(paragraph)}")
                    else:
                        parts.append(f"paragraph-{_canonicalize_schedule_paragraph_eid_label(paragraph)}")
                if subsection:
                    parts.append(_clean_num(subsection))
                for item_label in item_labels:
                    parts.append(_canonicalize_eid_tail_label(item_label))
                yield "-".join(parts)
            else:
                # Try section and article prefixes
                for prefix in ["article", "section"] if is_eur else ["section", "article"]:
                    parts = []
                    if section:
                        parts.append(f"{prefix}-{_clean_num(section)}")
                        for suffix_label in _body_target_eid_suffixes(addr):
                            parts.append(_canonicalize_eid_tail_label(suffix_label))
                    yield "-".join(parts)

        for full_key in _get_candidates():
            if not full_key:
                continue
            if full_key.lower() in self.eid_map:
                return self.eid_map[full_key.lower()]

        # Fallback to the first best guess
        return next(_get_candidates(), "")

    def _find_node_and_parent_statute(
        self,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        node, parent, idx = self._find_node_and_parent(
            self.statute.body,
            eid,
            allow_sequence_match=allow_sequence_match,
        )
        if node:
            return node, parent, idx
        for sched_idx, sched in enumerate(self.statute.supplements):
            if sched.attrs.get("eId") == eid:
                return sched, None, sched_idx
            node, parent, idx = self._find_node_and_parent(
                sched,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if node:
                return node, parent, idx
        return None, None, None

    def _find_node_and_parent(
        self,
        node: UKMutableNode,
        eid: str,
        *,
        allow_sequence_match: bool = True,
    ) -> tuple[Optional[UKMutableNode], Optional[UKMutableNode], Optional[int]]:
        target_seq = _get_id_sequence(eid)
        for i, child in enumerate(node.children):
            c_eid = child.attrs.get("eId") or child.attrs.get("id")
            if c_eid:
                if c_eid == eid:
                    return child, node, i
                if c_eid.endswith("-" + eid) or c_eid.endswith("_" + eid):
                    return child, node, i
                if allow_sequence_match and _get_id_sequence(c_eid) == target_seq:
                    return child, node, i
            res_node, res_parent, res_idx = self._find_node_and_parent(
                child,
                eid,
                allow_sequence_match=allow_sequence_match,
            )
            if res_node:
                return res_node, res_parent, res_idx
        return None, None, None

    def ground_ids(self):
        """Walks the entire statute and updates EIDs to match the Oracle map."""
        if not self.eid_map:
            return

        # Collect the full set of oracle EID values (the canonical IDs we want to
        # assign).  Used both for pre-seeding and in the main matching loop.
        oracle_id_values: set = set(self.eid_map.values())

        # Pre-seed seen_oracle_ids with EIDs that are already correct.
        # These nodes already carry an oracle-canonical EID and must NOT be
        # cleared — they would otherwise be reset to generic local IDs and
        # potentially mis-re-grounded to a different oracle EID.
        seen_oracle_ids: set = set()

        def _get_eid(node: UKMutableNode) -> Optional[str]:
            """Return the EID/id from a node's attrs (handles both 'eId' and 'id' keys)."""
            return _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))

        def _set_eid(node: UKMutableNode, eid: str) -> None:
            """Set an EID on a node, using whichever key the node already uses."""
            if "eId" in node.attrs:
                node.attrs["eId"] = eid
            else:
                # Node uses 'id' key (UK legislation XML) or has no EID attr yet.
                # Use 'eId' as the canonical key going forward.
                node.attrs["eId"] = eid

        def _grounding_clean_label(kind_name: str, label: Optional[str]) -> str:
            clean_label = _clean_num(label) if label else ""
            if not clean_label:
                return ""
            kind_prefix = str(kind_name or "").lower()
            if kind_prefix in {"part", "chapter"}:
                stripped = re.sub(rf"^{re.escape(kind_prefix)}\s+", "", clean_label).strip()
                if stripped:
                    return stripped
            return clean_label

        def _preseed_correct_eids(node: UKMutableNode) -> None:
            eid = _get_eid(node)
            if eid and eid in oracle_id_values:
                seen_oracle_ids.add(eid)
            for c in node.children:
                _preseed_correct_eids(c)

        if getattr(self.statute, "body", None):
            _preseed_correct_eids(self.statute.body)
        for sch in self.statute.supplements:
            _preseed_correct_eids(sch)

        def _clear_eids(node: UKMutableNode) -> None:
            """Clear EIDs that are NOT already in oracle (those stay for matching)."""
            eid = _get_eid(node)
            if eid and eid not in oracle_id_values:
                # Non-canonical EID — clear it so the grounding pass can assign
                # the correct oracle ID.
                for key in ("eId", "id"):
                    if key in node.attrs:
                        del node.attrs[key]
            # Children may need grounding even if the parent is already correct.
            for c in node.children:
                _clear_eids(c)

        if getattr(self.statute, "body", None):
            _clear_eids(self.statute.body)
        for sch in self.statute.supplements:
            _clear_eids(sch)

        # Pre-pass: ensure every node has a reasonable local eId.
        # Skip nodes that already have an oracle-canonical EID (under either
        # 'eId' or 'id' key) — those were preserved by _clear_eids and must
        # not be overwritten with a generic local label.
        def _ensure_local_eid(node: UKMutableNode) -> None:
            kind_value = _uk_kind_value(node.kind)
            if kind_value == "schedule_entry":
                for key in ("eId", "id"):
                    node.attrs.pop(key, None)
            elif "eId" not in node.attrs and "id" not in node.attrs and kind_value != "body":
                clean_label = _grounding_clean_label(kind_value, node.label)
                if clean_label:
                    node.attrs["eId"] = f"{kind_value}-{clean_label}"
                else:
                    node.attrs["eId"] = kind_value
            for c in node.children:
                _ensure_local_eid(c)

        if getattr(self.statute, "body", None):
            _ensure_local_eid(self.statute.body)
        for sch in self.statute.supplements:
            _ensure_local_eid(sch)

        def _slugify(text: str) -> str:
            if not text:
                return ""
            return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")

        def _node_full_text(node: UKMutableNode) -> str:
            """Collect normalized full-subtree text for a node (matches oracle text_map)."""
            parts = []
            if node.text:
                parts.append(node.text.strip())
            for child in node.children:
                t = _node_full_text(child)
                if t:
                    parts.append(t)
            raw = " ".join(parts)
            return _normalize_text_for_grounding(raw)

        def _ground_node(node: UKMutableNode, parent_path_key, parent_eid=None, ordinal=1, context="body"):
            nonlocal seen_oracle_ids
            parent_eid = _uk_eid_value(parent_eid)
            if _uk_kind_value(node.kind) == "schedule_entry":
                for key in ("eId", "id"):
                    node.attrs.pop(key, None)
                return
            # Fast path: if this node already has a correct oracle EID (preserved
            # from the pre-seed pass), skip the multi-pass matching for this node
            # and recurse into children with updated context.  The EID is already
            # registered in seen_oracle_ids from the pre-seed pass.
            existing_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
            if existing_eid and existing_eid in oracle_id_values and existing_eid in seen_oracle_ids:
                kind = node.kind
                kind_name = _uk_kind_value(kind).lower()
                clean_label = _grounding_clean_label(kind_name, node.label)
                next_path_key = uk_semantic_path_key(
                    parent_path_key,
                    kind=kind_name,
                    clean_label=clean_label,
                )
                new_context = context
                if kind_name == "schedule" and clean_label:
                    new_context = f"schedule-{clean_label}"
                elif kind_name == "body":
                    new_context = "body"
                kind_counts: dict = {}
                for child in node.children:
                    child_kind = _uk_kind_value(child.kind)
                    kind_counts[child_kind] = kind_counts.get(child_kind, 0) + 1
                    _ground_node(
                        child, next_path_key, existing_eid, ordinal=kind_counts[child_kind], context=new_context
                    )
                return

            kind = node.kind
            kind_name = _uk_kind_value(kind).lower()
            clean_label = _grounding_clean_label(kind_name, node.label)
            raw_label = str(node.label or "").strip()
            heading = node.attrs.get("heading") or ""
            if (
                not heading
                and kind_name in ("p1group", "pblock", "crossheading", "chapter", "part")
                and node.text
                and len(node.text) < 200
            ):
                heading = node.text
            slug = _slugify(heading)

            node_key_part = f"{kind_name}-{clean_label}" if clean_label else (f"{kind_name}-{slug}" if slug else kind_name)

            # Use : as separator for semantic path matching against eid_map
            if not parent_path_key:
                hierarchical_path_key = str(node_key_part)
            else:
                hierarchical_path_key = f"{parent_path_key}:{node_key_part}"

            next_path_key = uk_semantic_path_key(
                parent_path_key,
                kind=kind_name,
                clean_label=clean_label or slug,
            )

            oracle_id = None
            matched_cand = None

            # Pass 0: Exact Hash Matching (NEW - Grounding 2.0)
            # ONLY match meaningful text to avoid dot-shell collisions.
            # Skip for: (a) structural containers (part/chapter/schedule) — heading text
            # can collide with inline term definitions, (b) nodes whose exact hierarchical
            # path exists in oracle eid_map — flat matching will succeed and is more precise
            # (prevents section-1 enacted text matching oracle's subsection-1-1 with same text).
            _structural_kinds = {"part", "chapter", "schedule", "annex"}
            # Kinds that may legitimately match oracle term-* EIDs (definition nodes).
            # All other structural kinds (section, paragraph, subsection …) must NOT be
            # grounded to a term-* oracle EID via hash — the hash collision is accidental
            # (e.g. paragraph-a whose text begins with a term name).
            _term_eid_kinds = {"p1group", "crossheading", "section", "article"}
            is_dots = bool(node.text and re.match(r"^[.\s]+$", node.text))
            _has_structural_path = str(hierarchical_path_key).lower() in self.eid_map
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
            ):
                h = _semantic_hash(node.text)
                hash_key = f"hash:{h}"
                if hash_key in self.eid_map:
                    candidate_id = self.eid_map[hash_key]
                    if candidate_id not in seen_oracle_ids:
                        # Guard: reject a term-* oracle EID for non-term node kinds.
                        # Prevents paragraph-a (e.g. "(a) chief constable means…") from
                        # hash-colliding with the oracle's term-chief-constable definition.
                        _is_term_eid = candidate_id.startswith("term-")
                        if not _is_term_eid or kind_name in _term_eid_kinds:
                            oracle_id = candidate_id
                            matched_cand = f"hash:{h}"

            # Pass 0.5: Fuzzy Text Matching (NEW - Grounding 2.1)
            # Use node.text (direct text only) for the length/Levenshtein comparison.
            # Transparent wrapper nodes (p1group, crossheading) are excluded from fuzzy
            # matching because:
            #   (a) p1group direct text is typically empty — fuzzy wouldn't fire anyway
            #       but using full-subtree text would steal oracle EIDs from child sections.
            #   (b) crossheading direct text is the heading — it can fuzzy-match oracle
            #       term-* EIDs whose text equals the heading name.  Instead, a separate
            #       guard (below) blocks crossheading → term-* matches explicitly.
            # Non-transparent nodes (section, paragraph, subsection…) use direct text and
            # additionally must not fuzzy-match term-* oracle EIDs (same guard as hash pass).
            _fuzzy_skip_kinds = {"p1group", "pblock"}  # transparent wrappers whose children own the EIDs
            if (
                not oracle_id
                and node.text
                and not is_dots
                and not _has_structural_path
                and kind_name not in _structural_kinds
                and kind_name not in _fuzzy_skip_kinds
            ):
                node_norm = _normalize_text_for_grounding(node.text)
                if len(node_norm) > 30:
                    best_score = 0
                    best_id = None
                    for oid, otext in self.text_map.items():
                        if oid in seen_oracle_ids:
                            continue
                        if abs(len(otext) - len(node_norm)) > 0.1 * len(node_norm):
                            continue
                        score = Levenshtein.ratio(node_norm, otext)
                        if score > 0.92 and score > best_score:
                            best_score = score
                            best_id = oid
                    if best_id:
                        # Guard: crossheadings must not fuzzy-match term-* oracle EIDs.
                        # A crossheading "domestic abuse protection notices" should match
                        # oracle's crossheading EID (not term-domestic-abuse-protection-notice)
                        # even if the heading text and term text are nearly identical.
                        # When a crossheading matches a term-* EID the bench penalises the
                        # match because the crossheading's full subtree (all its sections) is
                        # compared to the oracle term's short text → very low text similarity.
                        _is_term_eid = best_id.startswith("term-")
                        if not _is_term_eid or kind_name not in ("crossheading", "pblock", "chapter"):
                            oracle_id = best_id
                            matched_cand = f"fuzzy:{best_score:.3f}"

            kind_syns: list[str] = [kind_name]
            if kind_name == "pblock":
                kind_syns.extend(["chapter", "crossheading", "eusection", "division"])
            elif kind_name == "chapter":
                kind_syns.extend(["pblock", "crossheading", "euchapter", "division"])
            elif kind_name == "crossheading":
                kind_syns.extend(["pblock", "chapter", "eusection", "division"])
            elif kind_name == "p1group":
                kind_syns.extend(["section", "crossheading", "paragraph", "article"])
            elif kind_name == "schedule":
                kind_syns.extend(["annex"])
            elif kind_name in ("section", "p1", "article"):
                kind_syns = ["section", "p1", "article"]
            elif kind_name in ("paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"):
                kind_syns = ["paragraph", "subsection", "p2", "p3", "subparagraph", "item", "point"]

            # Pass 1: Local & Flat Matching (High Priority for top-level nodes)
            if not oracle_id:
                flat_cands = []
                # Check hierarchical keys with synonyms
                for k in kind_syns:
                    parts = str(hierarchical_path_key).split(":")
                    last = parts[-1]
                    if "-" in last:
                        parts[-1] = f"{k}-{last.split('-', 1)[1]}"
                    else:
                        parts[-1] = k
                    flat_cands.append(":".join(parts).lower())

                # Check flat/suffix keys
                # crossheading/pblock are included so that ECHR-article Pblocks in
                # Schedule 1 can match oracle chapter-N EIDs via the suffix slug key.
                #
                # IMPORTANT: Suppress the short context:kind-label flat candidates for
                # sub-section-level nodes (paragraph, subsection, subparagraph, item)
                # that are deeply nested *inside a section* (parent_path_key contains
                # a "section-N" or "article-N" segment).  Without this guard a paragraph
                # node inside section-1-7 matches oracle's section-25-1-b via the shared
                # key "body:paragraph-b", stealing the oracle EID from section-25.
                # Structural containers (section, chapter, part, schedule) are NOT
                # restricted — their flat keys are the primary lookup path and they do
                # not collide across sections.
                _sub_kinds = {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3"}
                _is_inside_section = bool(
                    kind_name in _sub_kinds and re.search(r":(section|article|rule|regulation)-", parent_path_key or "")
                )
                # Suppress flat matching for paragraph/subparagraph/item nodes inside
                # schedule chapters/parts. Without this guard, "paragraph 2" under
                # chapter-1 matches oracle's chapter-10-paragraph-2 via the shared
                # key "schedule-1:paragraph-2". Schedule descendant nodes must match
                # via hierarchical paths or hash/fuzzy, not flat context:kind-label keys.
                _is_inside_schedule_chapter = bool(
                    kind_name in _sub_kinds
                    and context.startswith("schedule")
                    and re.search(r":(chapter|part)-", parent_path_key or "")
                )
                _schedule_structural_flat = bool(
                    context.startswith("schedule") and kind_name in {"part", "chapter", "crossheading", "pblock", "division"}
                )
                if kind_name in (
                    "section",
                    "article",
                    "schedule",
                    "annex",
                    "part",
                    "chapter",
                    "paragraph",
                    "crossheading",
                    "pblock",
                    "division",
                ):
                    for k in kind_syns:
                        if clean_label:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:{k}-{clean_label}")
                                flat_cands.append(f"{context}:suffix:{k}-{clean_label}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{clean_label}")
                        elif slug:
                            if not _is_inside_section and not _is_inside_schedule_chapter:
                                flat_cands.append(f"{context}:suffix:{k}-{slug}")
                            if not _schedule_structural_flat:
                                flat_cands.append(f"{k}-{slug}")

                if kind_name == "subsection" and clean_label and parent_eid:
                    parent_match = re.match(
                        r"^(section|article|rule|regulation)-(.+)$",
                        parent_eid,
                        re.I,
                    )
                    if parent_match:
                        parent_suffix = _clean_num(parent_match.group(2))
                        if parent_suffix:
                            flat_cands.append(f"{context}:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{context}:suffix:subsection-{parent_suffix}-{clean_label}")
                            flat_cands.append(f"{parent_path_key}:subsection-{parent_suffix}-{clean_label}")

                for cand in flat_cands:
                    if cand.lower() in self.eid_map:
                        candidate_id = self.eid_map[cand.lower()]
                        if candidate_id not in seen_oracle_ids:
                            oracle_id = candidate_id
                            matched_cand = f"flat:{cand.lower()}"
                            break

            # Pass 3: Ordinal Matching (Fallback for non-semantic IDs)
            # Guard: before accepting an ordinal match, verify text similarity when the
            # oracle text_map has content for the candidate.  This prevents a case where
            # enacted section[1] inside part-1 matches oracle section[1]-inside-part-1
            # (which is section-21, a definitions section) purely by position even though
            # the content is completely different — e.g. enacted Part 1 had sections 1-20
            # but after amendments only section-21 (definitions) remains in oracle Part 1.
            #
            # Two-factor rejection:
            #   (a) length ratio: if max/min > 3.0, texts are too different in size.
            #   (b) Levenshtein ratio < 0.50: text content does not match well enough.
            # Either condition alone rejects the candidate.  Both must pass to accept.
            # Threshold 0.50 is intentionally strict because legitimate ordinal matches
            # (same provision at same structural position) will score 0.80+ while wrong
            # ordinal matches (different section at same ordinal slot after amendments)
            # typically score 0.30-0.55 even for similar legal vocabulary.
            _ORDINAL_LEN_RATIO_MAX = 3.0
            _ORDINAL_TEXT_THRESHOLD = 0.50
            if not oracle_id:
                ord_key = f"{parent_path_key}:{kind_name}[{ordinal}]".lower()
                if ord_key in self.eid_map:
                    candidate_id = self.eid_map[ord_key]
                    if candidate_id not in seen_oracle_ids:
                        # Text guard: if oracle has text for the candidate, require
                        # the node full text to be sufficiently similar to oracle text.
                        oracle_text = self.text_map.get(candidate_id, "")
                        accept = True
                        if oracle_text:
                            node_full = _node_full_text(node)
                            if node_full and len(node_full) > 20 and len(oracle_text) > 20:
                                max_len = max(len(node_full), len(oracle_text))
                                min_len = min(len(node_full), len(oracle_text))
                                if max_len / min_len > _ORDINAL_LEN_RATIO_MAX:
                                    accept = False
                                else:
                                    ratio = Levenshtein.ratio(node_full, oracle_text)
                                    if ratio < _ORDINAL_TEXT_THRESHOLD:
                                        accept = False
                        if accept:
                            oracle_id = candidate_id
                            matched_cand = f"ordinal:{ord_key}"

            if oracle_id:
                before_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
                node.attrs["eId"] = oracle_id
                seen_oracle_ids.add(oracle_id)
                self.oracle_alignment_events.append(
                    {
                        "rule_id": "uk_oracle_eid_alignment_adapter",
                        "phase": "oracle_alignment",
                        "family": "oracle_alignment_adapter",
                        "kind": str(node.kind),
                        "label": node.label,
                        "before_eid": before_eid,
                        "after_eid": oracle_id,
                        "match_method": str(matched_cand).split(":", 1)[0] if matched_cand else "oracle_preserved",
                        "match_key": matched_cand,
                    }
                )
                if matched_cand:
                    self._log(f"  Matched {node.kind} {node.label or ''} to {oracle_id} via {matched_cand}")
            else:
                if uk_is_transparent_wrapper_kind(kind_name):
                    if "eId" in node.attrs:
                        before_eid = _uk_eid_value(node.attrs.get("eId"))
                        del node.attrs["eId"]
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": None,
                                "match_method": "transparent_wrapper_cleared",
                                "match_key": None,
                            }
                        )
                elif parent_eid:
                    before_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id"))
                    local_label = clean_label
                    if (
                        raw_label
                        and kind_name in {"subparagraph", "item", "point"}
                        and re.fullmatch(
                            r"[ivxlcdm]+",
                            raw_label,
                            re.IGNORECASE,
                        )
                    ):
                        local_label = raw_label.lower().strip(".")
                    part = local_label if local_label else kind_name
                    if context.startswith("schedule") and clean_label:
                        if kind_name in {"paragraph", "subparagraph", "subsection", "item", "point", "p2", "p3"}:
                            # UK schedule descendant IDs flatten nested paragraph/item levels
                            # to bare suffixes once the first schedule paragraph is established.
                            if re.search(r"(?:^|-)paragraph-[^-]+(?:-|$)", parent_eid):
                                part = local_label
                            else:
                                part = f"paragraph-{local_label}"
                        else:
                            part = f"{kind_name}-{clean_label}"
                    fallback_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{part}"
                    if not clean_label and kind_name not in {"schedule", "part", "chapter"}:
                        for key in ("eId", "id"):
                            node.attrs.pop(key, None)
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": None,
                                "match_method": "local_fallback_unlabeled_blocked",
                                "match_key": None,
                            }
                        )
                    else:
                        node.attrs["eId"] = fallback_eid
                        self.oracle_alignment_events.append(
                            {
                                "rule_id": "uk_oracle_eid_alignment_adapter",
                                "phase": "oracle_alignment",
                                "family": "oracle_alignment_adapter",
                                "kind": str(node.kind),
                                "label": node.label,
                                "before_eid": before_eid,
                                "after_eid": fallback_eid,
                                "match_method": "local_fallback",
                                "match_key": None,
                            }
                        )

            kind_counts = {}
            new_context = context
            if kind_name == "schedule" and clean_label:
                new_context = f"schedule-{clean_label}"
            elif kind_name == "body":
                new_context = "body"

            actual_eid = _uk_eid_value(node.attrs.get("eId") or node.attrs.get("id") or parent_eid)
            for child in node.children:
                child_kind = _uk_kind_value(child.kind)
                kind_counts[child_kind] = kind_counts.get(child_kind, 0) + 1
                _ground_node(child, next_path_key, actual_eid, ordinal=kind_counts[child_kind], context=new_context)

        grounded_count = 0

        def _visit_count(n):
            nonlocal grounded_count
            eid = n.attrs.get("eId")
            if eid and eid in self.eid_map.values():
                grounded_count += 1
            for c in n.children:
                _visit_count(c)

        body_node = getattr(self.statute, "body", None)
        if body_node:
            kind_counts = {}
            for node in body_node.children:
                node_kind = _uk_kind_value(node.kind)
                kind_counts[node_kind] = kind_counts.get(node_kind, 0) + 1
                _ground_node(node, "body", None, ordinal=kind_counts[node_kind], context="body")
            _visit_count(body_node)

        for i, sch in enumerate(self.statute.supplements):
            _ground_node(sch, "", None, ordinal=i + 1, context="schedule")
            _visit_count(sch)

        self._log(f"  EXECUTOR: grounded {grounded_count} nodes against Oracle map")


# ---------------------------------------------------------------------------
# Commencement-aware EID filtering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Public replay API
# ---------------------------------------------------------------------------


def _prepare_replay_uk_ops(
    ops: list[LegalOperation],
    *,
    base_ir: Optional[IRStatute] = None,
    verbose: bool = False,
    adjudications_out: Optional[list[CompileAdjudication]] = None,
) -> UKReplayPrepareResult:
    """Normalize replay ops so every entry point applies the same semantics."""
    base_executor: Optional[UKReplayExecutor] = UKReplayExecutor(base_ir) if base_ir is not None else None
    return prepare_replay_uk_ops(
        ops,
        base_executor=base_executor,
        verbose=verbose,
        adjudications_out=adjudications_out,
    )


def replay_uk_ops(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    allow_oracle_alignment: bool = True,
    verbose: bool = False,
    lo_ops_out: Optional[List[LegalOperation]] = None,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
) -> IRStatute:
    """Apply compiled UK legal operations to enacted base, return amended statute.

    This is the primary public entry point for the UK replay engine.  It wraps
    UKReplayExecutor with a clean function signature so callers do not need to
    instantiate the executor directly.

    Args:
        base:       Enacted (base) IRStatute produced by parse_uk_statute_ir().
        ops:        Compiled LegalOperation list from compile_effect_to_ir_ops()
                    or UKReplayPipeline.compile_ops_for_statute().
        eid_map:    Optional oracle EID map for grounding (key → oracle EID).
        text_map:   Optional oracle text map for fuzzy-text grounding.
        allow_oracle_alignment:
                    When True, replay-time oracle adapter behavior is enabled:
                    oracle-zombie collapse preparation plus post-apply EID grounding.
                    When False, replay runs without ORACLE_ALIGNMENT_ONLY mutation help.
        verbose:    If True, executor prints each applied op to stdout.
        lo_ops_out: Optional list to collect top-section snapshots after each
                    structural op.  Pass an empty list; it will be populated with
                    legal operations suitable for replay timelines.
        adjudications_out: Optional list to collect replay skip/no-op adjudications.
                    Entries are `CompileAdjudication` with one of the `uk_replay_*`
                    kinds defined by this executor.

    Returns:
        A new IRStatute with all ops applied (deep copy — base is not mutated).

    Op ordering:
        Ops are applied in the order supplied.  Callers should pre-sort by
        (effective_date, sequence) before passing.  UKReplayPipeline already
        does this in compile_ops_for_statute().
    """
    if verbose:
        print(f"  replay_uk_ops: applying {len(ops)} ops to {base.statute_id}")
    prepared_ops = _prepare_replay_uk_ops(
        ops,
        base_ir=base,
        verbose=verbose,
        adjudications_out=adjudications_out,
    )

    executor = UKReplayExecutor(
        base,
        eid_map=(eid_map or {}) if allow_oracle_alignment else {},
        text_map=(text_map or {}) if allow_oracle_alignment else {},
        verbose=verbose,
        lo_ops_out=lo_ops_out,
        adjudications_out=adjudications_out,
    )
    for op in prepared_ops.accepted_ops:
        executor.apply_op(op)

    if adjudications_out is not None:
        append_replay_fold_text_duplication_adjudications(
            adjudications_out,
            frozen_statute=executor.statute.to_irstatute(),
            source_statute=base.statute_id,
        )

    return executor.statute.to_irstatute()

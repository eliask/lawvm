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
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, List, Optional, Sequence, cast

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
    uk_kind_matches,
    uk_should_bubble_structural_commencement,
    uk_should_descend_transparently,
)
from lawvm.uk_legislation.commencement import (
    commencement_eid_set,
)
from lawvm.uk_legislation.uk_grafter import (
    _parse_part,
    _parse_chapter,
    _parse_section,
    _parse_p1group,
    _parse_p2,
    _parse_p3,
    _parse_p4,
    _clean_num,
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
from lawvm.uk_legislation.effect_special_lowering import (
    lower_uk_after_paragraph_insert_labelled_series,
    lower_uk_metadata_renumber_effect,
)
from lawvm.uk_legislation.addressing import (
    _action_name,
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
    _order_schedule_materialization_ops,
    _schedule_target_levels,
    _uk_eid_value,
    _uk_kind_value,
)
from lawvm.uk_legislation.authority_filter import (
    _partition_uk_ops_by_authority_mode,
    _uk_authority_filter_diagnostic,
    _uk_op_allowed_by_authority_mode,
)
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
    _CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE,
    _crossheading_and_structural_repeal_selector,
    _crossheading_before_anchor_replacement_text,
    _crossheading_before_anchor_text_patch_fragment,
    _expand_heading_facet_section_range_ref,
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_carrier_for_target,
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
    _select_whole_schedule_element,
    _uk_metadata_renumber_targets,
    _uk_source_text_corrected_renumber_targets,
)
from lawvm.uk_legislation.mutable_ir import (
    UKMutableNode,
    UKMutableStatute,
    uk_replace_children,
    uk_replace_text,
)
from lawvm.uk_legislation.provision_extractor import (
    _extract_provision_element_from_root,
    _find_provision_greedy,
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
    _schedule_list_entry_replace_selector,
    _schedule_list_entry_selector,
    _schedule_list_entry_table_rows_selector,
    _table_cell_selector,
)
from lawvm.uk_legislation.replay_text import (
    _article_phrase_content_word_present,
    _citation_connector_elided_text_match_present,
    _citation_stripped_text_match_present,
    _compact_normalized_text,
    _definition_entry_term_absent,
    _monetary_amount_text_selector,
    _multi_fragment_text_selector,
    _non_substantive_text_selector,
    _normalized_replay_subtree_text,
    _normalized_replacement_text_present,
    _normalized_text_match_present,
    _node_text_contains_text,
    _parenthetical_omission_text_selector,
    _replay_subtree_text_preview,
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
    _select_enacted_source_for_current_shell,
)
from lawvm.uk_legislation.source_action_inference import infer_uk_effect_action_from_source
from lawvm.uk_legislation.source_text_reclassifications import (
    _external_act_target_from_source_text,
    _partial_whole_act_repeal_exceptions,
    _quote_only_definition_list_omission_payload_match,
    _quote_only_omission_payload_match,
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
    resolve_uk_table_entry_inline_cell,
    resolve_unique_uk_table_entry_cells,
    uk_table_cell_span,
)
from lawvm.uk_legislation.replay_table_apply import UKReplayTableApplyMixin
from lawvm.uk_legislation.replay_text_apply import UKReplayTextApplyMixin
from lawvm.uk_legislation.replay_invariant_diagnostics import UKReplayInvariantDiagnosticsMixin
from lawvm.uk_legislation.replay_grounding import UKReplayGroundingMixin
from lawvm.uk_legislation.replay_heading_apply import UKReplayHeadingApplyMixin
from lawvm.uk_legislation.replay_insert_apply import UKReplayInsertApplyMixin
from lawvm.uk_legislation.replay_target_diagnostics import (
    UKReplayTargetDiagnosticsMixin,
    _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.replay_target_lookup import UKReplayTargetLookupMixin
from lawvm.uk_legislation.replay_schedule_list_apply import (
    UKReplayScheduleListApplyMixin,
    _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
)
from lawvm.uk_legislation.replay_renumber_apply import UKReplayRenumberApplyMixin
from lawvm.uk_legislation.replay_repeal_apply import UKReplayRepealApplyMixin
from lawvm.uk_legislation.replay_state import UKReplayStateMixin
from lawvm.uk_legislation.replay_target_gaps import (
    uk_broad_schedule_table_shape_gap,
    uk_table_target_shape_gap,
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
    UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
    _source_after_paragraph_insert_labelled_series,
    _source_parent_instruction_with_payload,
)
from lawvm.uk_legislation.source_structural_sibling import _structural_sibling_insert_from_source
from lawvm.uk_legislation.source_table_entry_paragraph import (
    SOURCE_TABLE_CELL_PARAGRAPH_SENTINEL_RE as _TABLE_CELL_PARAGRAPH_SENTINEL_RE,
    UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID as _UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
    _source_carried_table_entry_paragraph_substitution,
)
from lawvm.uk_legislation.target_anchors import (
    _fallback_target_eid,
    _source_after_insertion_anchor,
    _source_before_insertion_anchor,
    _target_anchor_eid,
    uk_match_kind_label,
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
    _node_text_patch_preimage_present,
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

    action_inference = infer_uk_effect_action_from_source(
        effect=effect,
        effect_type=effect_type,
        initial_action=action,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_root=source_root,
        lowering_rejections_out=lowering_rejections_out,
    )
    if action_inference.blocked:
        return []
    action = action_inference.action
    source_parent_substitution_range_payload = (
        action_inference.source_parent_substitution_range_payload
    )
    source_parent_at_end_added_payload = action_inference.source_parent_at_end_added_payload

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
        return lower_uk_metadata_renumber_effect(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            metadata_renumber_targets=metadata_renumber_targets,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )

    after_paragraph_series = _source_after_paragraph_insert_labelled_series(
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        affected_provisions=effect.affected_provisions,
    )
    if action == "insert" and after_paragraph_series is not None:
        return lower_uk_after_paragraph_insert_labelled_series(
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            after_paragraph_series=after_paragraph_series,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            lowering_rejections_out=lowering_rejections_out,
        )

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


class UKReplayExecutor(
    UKReplayTableApplyMixin,
    UKReplayTextApplyMixin,
    UKReplayInvariantDiagnosticsMixin,
    UKReplayScheduleListApplyMixin,
    UKReplayGroundingMixin,
    UKReplayTargetDiagnosticsMixin,
    UKReplayTargetLookupMixin,
    UKReplayInsertApplyMixin,
    UKReplayStateMixin,
    UKReplayRenumberApplyMixin,
    UKReplayHeadingApplyMixin,
    UKReplayRepealApplyMixin,
):
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
            self._apply_repeal_op(op, target, node, parent, idx)
            return
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
                        uk_replace_text(node, new_node.text)
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
                heading_carrier = _heading_facet_carrier_for_target(
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
                            if not _node_text_patch_preimage_present(
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
                    table_cell, table_cell_reason, table_cell_detail = resolve_uk_table_entry_inline_cell(
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
                    elif uk_broad_schedule_table_shape_gap(target, node):
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
                elif uk_table_target_shape_gap(target):
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
            self._apply_insert_op(op, target, node, insert_existing_target_resolution)
            return
        elif _action_name(op.action) == "renumber":
            self._apply_renumber_op(op, target)
            return
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

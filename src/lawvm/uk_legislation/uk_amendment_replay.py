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
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.canonicalize import (
    canonicalize_uk_address,
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
from lawvm.uk_legislation.effect_lowering_tail import (
    append_no_targets_rejection,
    append_source_parent_at_end_added_observation,
    append_unlowered_overlap_substitution_rejection,
    build_crossheading_insert_ops,
    build_trailing_repeal_ops,
)
from lawvm.uk_legislation.effect_operation_builder import build_lowered_operation_provenance
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_missing_structural_payload,
    reject_mixed_heading_structural_insert_missing_payload,
)
from lawvm.uk_legislation.effect_crossheading_prelude import (
    build_crossheading_context,
    build_crossheading_compound_heading_op,
    reject_unsupported_crossheading_replace,
)
from lawvm.uk_legislation.effect_replace_prelude import plan_replace_effect_prelude
from lawvm.uk_legislation.effect_schedule_lowering import (
    try_lower_schedule_list_entry_mutation,
    try_lower_schedule_table_end_rows_insert,
)
from lawvm.uk_legislation.effect_table_lowering import (
    prepare_table_cell_text_patch_context,
    try_lower_repeal_table_effect,
    try_lower_table_column_insert,
    try_lower_table_row_insert,
)
from lawvm.uk_legislation.effect_target_prelude import (
    append_target_shape_observations,
    expand_single_target_prelude,
    refine_numbered_schedule_entry_repeal_target,
    reject_external_or_partial_whole_act_scope,
    reject_unsupported_target_facet,
    reject_schedule_entry_missing_source,
    resolve_effect_target_context,
)
from lawvm.uk_legislation.addressing import (
    _action_name,
    _addr_container,
    _addr_leaf_kind,
    _addr_leaf_label,
    _order_schedule_materialization_ops,
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
    _expand_heading_facet_section_range_ref,
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_full_replacement_fragment,
    _is_heading_only_ref,
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
    uk_replace_children,
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
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    NOTE_TABLE_ROW_INSERT_SELECTOR as _NOTE_TABLE_ROW_INSERT_SELECTOR,
    NOTE_TEXT_REWRITE_RULE as _NOTE_TEXT_REWRITE_RULE,
    _schedule_list_entry_selector,
    _schedule_list_entry_table_rows_selector,
)
from lawvm.uk_legislation.replay_text import (
    _multi_fragment_text_selector,
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
    _quote_only_definition_list_omission_payload_match,
    _quote_only_omission_payload_match,
    reclassify_word_level_structural_subsection_omission,
)
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor,
    _source_replaced_sibling_count_from_substitution_text,
)
from lawvm.uk_legislation.witness_sidecars import (
    _lowered_witness_from_payload_data,
    _lowered_witness_to_payload_data,
    _payload_with_rewrite_witness,
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
from lawvm.uk_legislation.replay_table_geometry import (
    expanded_uk_table_rows,
    uk_table_cell_span,
)
from lawvm.uk_legislation.replay_executor import (
    UKReplayExecutor,
    _prepare_replay_uk_ops,
    replay_uk_ops,
)
from lawvm.uk_legislation.schedule_list_selectors import (
    _strip_schedule_entry_payload,
    _strip_schedule_entry_phrase,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    _fragment_rule_ids,
    _fragment_substitution,
    append_all_occurrences_text_rewrite_observations,
    append_basic_text_rewrite_observations,
    append_source_carried_substitution_rewrite_observations,
    append_source_carried_tail_rewrite_observations,
    lower_labeled_child_end_range_selector,
    _multi_quoted_word_repeal_fragments,
)
from lawvm.uk_legislation.source_context import (
    _first_amendment_container,
)
from lawvm.uk_legislation.source_child_tail_rewrites import (
    _fragment_substitution_source_carried_child_tail_repeal,
    _fragment_substitution_source_carried_child_tail_substitution,
)
from lawvm.uk_legislation.source_amendment_program_fragments import (
    _fragment_substitution_amendment_inserted_text_substitution,
    _fragment_substitution_source_carried_multi_subunit_repeal,
    reject_amendment_program_inserted_parent_structural_insert,
)
from lawvm.uk_legislation.source_definition_context import (
    _scope_fragment_substitutions_to_source_definition_parent,
)
from lawvm.uk_legislation.source_definition_fragments import (
    _fragment_substitution_source_carried_after_quoted_anchor_insert,
    _fragment_substitution_source_carried_definition_child_insert,
    _fragment_substitution_source_carried_definition_child_text_omission,
    _fragment_substitution_source_carried_definition_entry_insert,
    _fragment_substitution_source_carried_definition_entry_substitution,
    _fragment_substitution_source_carried_following_words_repeal,
    _fragment_substitution_source_carried_quoted_text_substitution,
    lower_source_carried_definition_child_at_end_insert,
    lower_source_carried_definition_child_text_omission,
    refine_source_definition_child_target,
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
    _direct_payload_text,
    _flat_p1para_schedule_paragraph_insert_payload,
    infer_source_payload_from_target,
    _inserted_section_p1group_heading_text,
    _prepend_inserted_section_heading_carrier,
)
from lawvm.uk_legislation.source_payload_elaboration import (
    _is_broad_schedule_flat_replace_payload,
    _is_non_substantive_structural_payload,
    _retarget_instruction_element_to_target,
    _source_payload_matches_target_leaf,
    _substituted_series_new_sibling_insert_detail,
    _with_trailing_subordinate_siblings,
)
from lawvm.uk_legislation.source_parent_payloads import (
    _source_after_paragraph_insert_labelled_series,
)
from lawvm.uk_legislation.source_structural_sibling import lower_source_structural_sibling_insert
from lawvm.uk_legislation.source_table_entry_paragraph import (
    UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID as _UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
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
    _split_metadata_provisions,
)
from lawvm.uk_legislation.table_sources import (
    lower_uk_table_driven_corresponding_entry_word_substitution,
)
from lawvm.uk_legislation.text_patch_lowering import build_uk_text_patch_items
from lawvm.uk_legislation.text_matching import (
    _normalize_text,
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
        replace_prelude = plan_replace_effect_prelude(
            effect=effect,
            original_targets_str=original_targets_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
            lowering_rejections_out=lowering_rejections_out,
        )
        targets_str = replace_prelude.targets_str
        trailing_repeal_refs = replace_prelude.trailing_repeal_refs
        replacement_leaf_override = replace_prelude.replacement_leaf_override
        replacement_leaf_kind = replace_prelude.replacement_leaf_kind
        label_changing_substitutions = replace_prelude.label_changing_substitutions
    append_source_parent_at_end_added_observation(
        lowering_rejections_out,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        source_parent_at_end_added_payload=source_parent_at_end_added_payload,
    )
    target_prelude = expand_single_target_prelude(
        effect=effect,
        action=action,
        targets_str=targets_str,
        original_targets_str=original_targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    targets_str = target_prelude.targets_str
    mixed_heading_source_ref_by_target = target_prelude.mixed_heading_source_ref_by_target
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
        append_no_targets_rejection(
            lowering_rejections_out,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
        )
        return []

    ops = []
    unlowered_overlap_substitution_targets: list[str] = []
    unlowered_overlap_substitution_reason = ""
    chained_insert_preceding_eid: Optional[str] = None
    chained_insert_preceding_eid_source = "effect_comments_after_clause"
    if action == "insert":
        ops.extend(
            build_crossheading_insert_ops(
                effect=effect,
                extracted_el=extracted_el,
                sequence=sequence,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
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
        if reject_unsupported_target_facet(
            effect=effect,
            t_str=t_str,
            target_candidate_count=len(targets_str),
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
            continue
        target_context = resolve_effect_target_context(
            effect=effect,
            action=action,
            is_word_level=is_word_level,
            t_str=t_str,
            target_index=target_index,
            label_changing_substitutions=label_changing_substitutions,
            replacement_leaf_override=replacement_leaf_override,
            replacement_leaf_kind=replacement_leaf_kind,
            source_parent_substitution_range_payload=source_parent_substitution_range_payload,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        heading_facet_target = target_context.heading_facet_target
        target = target_context.target
        payload_match_target = target_context.payload_match_target
        label_changing_substitution = target_context.label_changing_substitution
        target_replacement_leaf_override = target_context.target_replacement_leaf_override
        target_replacement_leaf_kind = target_context.target_replacement_leaf_kind
        flat_p1para_schedule_insert_lowered = False
        flat_p1para_payload_detail: dict[str, Any] = {}
        append_target_shape_observations(
            effect=effect,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        target = refine_numbered_schedule_entry_repeal_target(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        crossheading_context = build_crossheading_context(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
        )
        crossheading_replacement_text = crossheading_context.replacement_text
        crossheading_text_patch_fragment = crossheading_context.text_patch_fragment
        crossheading_compound_heading_text = crossheading_context.compound_heading_text
        crossheading_group_repeal_selector = crossheading_context.group_repeal_selector
        if reject_unsupported_crossheading_replace(
            effect=effect,
            action=action,
            t_str=t_str,
            context=crossheading_context,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
            continue
        if reject_schedule_entry_missing_source(
            effect=effect,
            effect_type=effect_type,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
            continue
        append_target_shape_observations(
            effect=effect,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        target = refine_numbered_schedule_entry_repeal_target(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        if crossheading_compound_heading_text is not None:
            ops.append(
                build_crossheading_compound_heading_op(
                    effect=effect,
                    t_str=t_str,
                    target=target,
                    replacement_text=crossheading_compound_heading_text,
                    sequence=sequence,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    original_targets_str=original_targets_str,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                    lowering_rejections_out=lowering_rejections_out,
                )
            )
        schedule_table_end_rows = try_lower_schedule_table_end_rows_insert(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            heading_facet_target=heading_facet_target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            lowering_rejections_out=lowering_rejections_out,
        )
        if schedule_table_end_rows.handled:
            if schedule_table_end_rows.op is not None:
                ops.append(schedule_table_end_rows.op)
            continue
        schedule_list_entry = try_lower_schedule_list_entry_mutation(
            effect=effect,
            action=action,
            effect_type=effect_type,
            t_str=t_str,
            target=target,
            heading_facet_target=heading_facet_target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            lowering_rejections_out=lowering_rejections_out,
        )
        if schedule_list_entry.handled:
            if schedule_list_entry.op is not None:
                ops.append(schedule_list_entry.op)
            continue
        table_column_insert = try_lower_table_column_insert(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            lowering_rejections_out=lowering_rejections_out,
        )
        if table_column_insert.handled:
            if table_column_insert.op is not None:
                ops.append(table_column_insert.op)
            continue
        table_row_insert = try_lower_table_row_insert(
            effect=effect,
            action=action,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            lowering_rejections_out=lowering_rejections_out,
        )
        if table_row_insert.handled:
            if table_row_insert.op is not None:
                ops.append(table_row_insert.op)
            continue
        repeal_table_effect = try_lower_repeal_table_effect(
            effect=effect,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            sequence=sequence,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            original_targets_str=original_targets_str,
            lowering_rejections_out=lowering_rejections_out,
        )
        if repeal_table_effect.handled:
            ops.extend(repeal_table_effect.ops)
            continue
        table_cell_context = prepare_table_cell_text_patch_context(
            effect=effect,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            source_root=source_root,
            lowering_rejections_out=lowering_rejections_out,
        )
        table_cell_selector = table_cell_context.table_cell_selector
        selector_rule_id = table_cell_context.selector_rule_id
        source_carried_table_entry_paragraph_substitution = (
            table_cell_context.source_carried_table_entry_paragraph_substitution
        )
        target = table_cell_context.target
        if table_cell_context.handled:
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
        if reject_external_or_partial_whole_act_scope(
            effect=effect,
            effect_type=effect_type,
            t_str=t_str,
            target=target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
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

        if reject_mixed_heading_structural_insert_missing_payload(
            effect=effect,
            t_str=t_str,
            mixed_heading_source_ref_by_target=mixed_heading_source_ref_by_target,
            content_ir=content_ir,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
            continue

        if content_ir is None:
            content_ir = infer_source_payload_from_target(
                target=target,
                extracted_text=extracted_text,
                effect_id=effect.effect_id,
                use_metadata_fallback=(
                    use_metadata_fallback and not _is_heading_only_ref(t_str)
                ),
            )

        if reject_missing_structural_payload(
            effect=effect,
            action=action,
            t_str=t_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            use_metadata_fallback=use_metadata_fallback,
            lowering_rejections_out=lowering_rejections_out,
        ):
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

        structural_sibling_insert = lower_source_structural_sibling_insert(
            effect=effect,
            effect_type=effect_type,
            curr_action=curr_action,
            target=target,
            content_ir=content_ir,
            target_ref=t_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        target = structural_sibling_insert.target
        content_ir = structural_sibling_insert.content_ir
        structural_sibling_insert_detail = structural_sibling_insert.detail

        if reject_amendment_program_inserted_parent_structural_insert(
            effect=effect,
            curr_action=curr_action,
            target=target,
            target_ref=t_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        ):
            continue

        # Grounding 2.0: Fragment substitutions
        structural_omission_reclassification = reclassify_word_level_structural_subsection_omission(
            effect=effect,
            curr_action=curr_action,
            content_ir=content_ir,
            target=target,
            target_ref=t_str,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        curr_action = structural_omission_reclassification.curr_action
        content_ir = structural_omission_reclassification.content_ir

        definition_child_text_omission_lowering = lower_source_carried_definition_child_text_omission(
            effect=effect,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            target=target,
            target_ref=t_str,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        target = definition_child_text_omission_lowering.target
        curr_action = definition_child_text_omission_lowering.curr_action
        content_ir = definition_child_text_omission_lowering.content_ir
        fragment_subs = definition_child_text_omission_lowering.fragment_subs
        op_text_match = definition_child_text_omission_lowering.op_text_match
        op_text_replacement = definition_child_text_omission_lowering.op_text_replacement

        definition_child_at_end_insert_lowering = lower_source_carried_definition_child_at_end_insert(
            effect=effect,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            target=target,
            target_ref=t_str,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        target = definition_child_at_end_insert_lowering.target
        curr_action = definition_child_at_end_insert_lowering.curr_action
        content_ir = definition_child_at_end_insert_lowering.content_ir
        fragment_subs = definition_child_at_end_insert_lowering.fragment_subs
        op_text_match = definition_child_at_end_insert_lowering.op_text_match
        op_text_replacement = definition_child_at_end_insert_lowering.op_text_replacement

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
                table_substitution = lower_uk_table_driven_corresponding_entry_word_substitution(
                    effect=effect,
                    curr_action=curr_action,
                    content_ir=content_ir,
                    fragment_subs=fragment_subs,
                    op_text_match=op_text_match,
                    op_text_replacement=op_text_replacement,
                    target=target,
                    target_ref=t_str,
                    extracted_el=extracted_el,
                    source_root=source_root,
                    extracted_text=extracted_text,
                    lowering_rejections_out=lowering_rejections_out,
                )
                curr_action = table_substitution.curr_action
                content_ir = table_substitution.content_ir
                fragment_subs = table_substitution.fragment_subs
                op_text_match = table_substitution.op_text_match
                op_text_replacement = table_substitution.op_text_replacement
                if table_substitution.skip_effect:
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
                    target = refine_source_definition_child_target(
                        effect=effect,
                        target=target,
                        fragment=primary,
                        target_ref=t_str,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
                    )
                    labeled_child_end_range_lowering = lower_labeled_child_end_range_selector(
                        effect=effect,
                        target=target,
                        target_ref=t_str,
                        primary=primary,
                        curr_action=curr_action,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
                    )
                    primary = labeled_child_end_range_lowering.primary
                    curr_action = labeled_child_end_range_lowering.curr_action
                    if labeled_child_end_range_lowering.skip_effect:
                        continue
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
                    append_all_occurrences_text_rewrite_observations(
                        effect=effect,
                        target=target,
                        target_ref=t_str,
                        fragment_subs=fragment_subs,
                        op_text_match=op_text_match,
                        op_text_replacement=op_text_replacement,
                        op_text_occurrence=op_text_occurrence,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
                    )
                    append_basic_text_rewrite_observations(
                        effect=effect,
                        target=target,
                        target_ref=t_str,
                        fragment_subs=fragment_subs,
                        op_text_match=op_text_match,
                        op_text_replacement=op_text_replacement,
                        op_text_occurrence=op_text_occurrence,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
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
                    append_source_carried_tail_rewrite_observations(
                        effect=effect,
                        target=target,
                        target_ref=t_str,
                        fragment_subs=fragment_subs,
                        primary=primary,
                        op_text_match=op_text_match,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
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
                    append_source_carried_substitution_rewrite_observations(
                        effect=effect,
                        target=target,
                        target_ref=t_str,
                        fragment_subs=fragment_subs,
                        primary=primary,
                        op_text_match=op_text_match,
                        op_text_replacement=op_text_replacement,
                        op_text_occurrence=op_text_occurrence,
                        op_text_end_occurrence=op_text_end_occurrence,
                        extracted_el=extracted_el,
                        extracted_text=extracted_text,
                        lowering_rejections_out=lowering_rejections_out,
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
            text_patch_items = build_uk_text_patch_items(
                curr_action=curr_action,
                fragment_subs=fragment_subs,
                op_text_match=op_text_match,
                op_text_replacement=op_text_replacement,
                op_text_occurrence=op_text_occurrence,
                op_text_end_occurrence=op_text_end_occurrence,
            )

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
                provenance_tags, op_witness_rule_id = build_lowered_operation_provenance(
                    lowered_witness=lowered_witness,
                    table_cell_selector=table_cell_selector,
                    crossheading_group_repeal_selector=crossheading_group_repeal_selector,
                    curr_action=curr_action,
                    target=target,
                    label_changing_substitution=label_changing_substitution,
                    flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
                    source_parent_substitution_range_payload=(
                        source_parent_substitution_range_payload
                    ),
                    source_parent_at_end_added_payload=source_parent_at_end_added_payload,
                    target_index=target_index,
                )
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
        append_unlowered_overlap_substitution_rejection(
            lowering_rejections_out,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            effect_type=effect_type,
            original_targets_str=original_targets_str,
            target_candidate_count=len(targets_str),
            unlowered_overlap_substitution_targets=unlowered_overlap_substitution_targets,
            unlowered_overlap_substitution_reason=unlowered_overlap_substitution_reason,
        )
    if action == "replace" and trailing_repeal_refs:
        ops.extend(
            build_trailing_repeal_ops(
                effect=effect,
                sequence=sequence,
                trailing_repeal_refs=trailing_repeal_refs,
                effect_witness=effect_witness,
                extraction_witness=extraction_witness,
                original_targets_str=original_targets_str,
                source_parent_substitution_range_payload=source_parent_substitution_range_payload,
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

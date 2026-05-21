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

import json as json  # noqa: F401
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, List, Optional

from lawvm.core.ir import (
    IRStatute,
    LegalOperation,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_grafter import _LEG_NS as _LEG_NS  # noqa: F401
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    _COMMENCEMENT_EFFECT_TYPES,
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    load_effects_for_statute_from_archive,
    uk_effect_requires_affecting_source_for_replay,
)
from lawvm.uk_legislation.effect_special_lowering import (
    lower_uk_after_paragraph_insert_labelled_series,
    lower_uk_metadata_renumber_effect,
)
from lawvm.uk_legislation.effect_substitution_normalization import (
    lower_substituted_payload_insert_normalization,
)
from lawvm.uk_legislation.effect_lowering_tail import (
    append_chained_insertion_anchor_observation,
    append_no_targets_rejection,
    append_source_parent_at_end_added_observation,
    append_unlowered_overlap_substitution_rejection,
    build_crossheading_insert_ops,
    build_trailing_repeal_ops,
    resolve_uk_insertion_anchor_context,
)
from lawvm.uk_legislation.effect_operation_builder import (
    build_lowered_operations_for_text_patches,
)
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_missing_structural_payload,
    reject_mixed_heading_structural_insert_missing_payload,
)
from lawvm.uk_legislation.effect_payload_normalization import (
    extract_uk_structural_payload_ir,
    prepare_uk_operation_payload_node,
)
from lawvm.uk_legislation.effect_crossheading_prelude import (
    append_crossheading_group_repeal_observation,
    build_crossheading_context,
    build_crossheading_compound_heading_op,
    reject_unsupported_crossheading_replace,
    refine_crossheading_or_heading_facet_target,
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
from lawvm.uk_legislation.effect_text_fragment_lowering import lower_uk_text_fragment_rewrite
from lawvm.uk_legislation.effect_target_prelude import (
    append_added_type_source_structuralized_observation,
    append_heading_facet_range_expansion_observation,
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
    _order_schedule_materialization_ops,
)
from lawvm.uk_legislation.authority_filter import (
    _partition_uk_ops_by_authority_mode,
    _uk_authority_filter_diagnostic,
)
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _is_heading_only_ref,
)
from lawvm.uk_legislation.lowering_records import (
    append_manual_compile_frontier_diagnostic,
    append_metadata_only_selection_rejection,
    append_no_ops_lowering_rejections,
    append_pit_date_filter_rejection,
    append_replay_applicability_filter_diagnostic,
    append_source_pathology_classified_diagnostic,
    append_source_pathology_filter_lowering_rejections,
    mark_nonreplay_lowering_rejections_nonblocking,
)
from lawvm.uk_legislation.lowering_actions import (
    _is_uk_word_level_effect_type,
    _uk_effect_type_action,
)
from lawvm.uk_legislation.metadata_rewrites import (
    _uk_metadata_renumber_targets,
    _uk_source_text_corrected_renumber_targets,
)
from lawvm.uk_legislation.provision_extractor import (
    extract_provision_element_from_bytes,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
    _append_affecting_source_context_diagnostic,
    _build_affecting_source_context,
    _extract_from_affecting_source_context_with_observations,
    _select_enacted_source_for_current_shell,
)
from lawvm.uk_legislation.source_action_inference import (
    append_no_supported_action_rejection,
    infer_uk_effect_action_from_source,
)
from lawvm.uk_legislation.source_text_reclassifications import (
    reclassify_word_level_structural_subsection_omission,
)
from lawvm.uk_legislation.substitution_metadata import (
    UKSourceLabelChangingSubstitution,
    _source_replaced_sibling_count_from_substitution_text,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_effect_witness,
    _uk_extraction_witness,
)
from lawvm.uk_legislation.ordering import (
    _order_uk_effects_for_replay,
    _order_uk_text_patch_preimage_chains,
)
from lawvm.uk_legislation.replay_applicability import (
    should_replay_nonstructural_ops,
)
from lawvm.uk_legislation.replay_executor import (
    UKReplayExecutor,
    _prepare_replay_uk_ops,
)
from lawvm.uk_legislation.source_amendment_program_fragments import (
    reject_amendment_program_inserted_parent_structural_insert,
)
from lawvm.uk_legislation.source_definition_fragments import (
    lower_source_carried_definition_child_at_end_insert,
    lower_source_carried_definition_child_text_omission,
)
from lawvm.uk_legislation.source_payload_helpers import (
    infer_source_payload_from_target,
)
from lawvm.uk_legislation.source_parent_payloads import (
    _source_after_paragraph_insert_labelled_series,
)
from lawvm.uk_legislation.source_structural_sibling import lower_source_structural_sibling_insert
from lawvm.uk_legislation.target_anchors import (
    _fallback_target_eid,
    _target_anchor_eid,
)
from lawvm.uk_legislation.target_parser import (
    _split_metadata_provisions,
)
from lawvm.uk_legislation.text_patch_lowering import build_uk_text_patch_items
from lawvm.uk_legislation.xml_helpers import (
    _text_content,
)

# Backward-compatible re-exports for older tools/tests that imported UK helper
# internals from this historical facade while the implementation moved out.
from lawvm.uk_legislation.authority_filter import (  # noqa: F401
    _uk_op_allowed_by_authority_mode as _uk_op_allowed_by_authority_mode,
)
from lawvm.uk_legislation.commencement import commencement_eid_set as commencement_eid_set  # noqa: F401
from lawvm.uk_legislation.effects import (  # noqa: F401
    load_effects_for_statute as load_effects_for_statute,
    parse_effects_from_bytes as parse_effects_from_bytes,
    parse_effects_from_feeds as parse_effects_from_feeds,
    parse_effects_from_metadata as parse_effects_from_metadata,
)
from lawvm.uk_legislation.ordering import (  # noqa: F401
    _uk_source_provision_order_key as _uk_source_provision_order_key,
)
from lawvm.uk_legislation.provenance_notes import (  # noqa: F401
    NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR as _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    NOTE_FRAGMENT_SUB as _NOTE_FRAGMENT_SUB,
    NOTE_METADATA_SOURCE_FALLBACK as _NOTE_METADATA_SOURCE_FALLBACK,
    NOTE_PRECEDING_EID as _NOTE_PRECEDING_EID,
    NOTE_REWRITE_WITNESS as _NOTE_REWRITE_WITNESS,
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    NOTE_TABLE_ROW_INSERT_SELECTOR as _NOTE_TABLE_ROW_INSERT_SELECTOR,
    NOTE_TEXT_REWRITE_RULE as _NOTE_TEXT_REWRITE_RULE,
)
from lawvm.uk_legislation.provision_extractor import (  # noqa: F401
    _parse_ref as _parse_ref,
)
from lawvm.uk_legislation.replay_executor import replay_uk_ops as replay_uk_ops  # noqa: F401
from lawvm.uk_legislation.source_context import (  # noqa: F401
    _extract_from_affecting_source_context as _extract_from_affecting_source_context,
)
from lawvm.uk_legislation.substitution_metadata import (  # noqa: F401
    _repeal_tail_for_substituted_series_replacement as _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor as _retarget_substituted_series_to_replaced_anchor,
)
from lawvm.uk_legislation.target_parser import (  # noqa: F401
    _parse_affected_target as _parse_affected_target,
)
from lawvm.uk_legislation.text_rewrite_fragments import (  # noqa: F401
    _fragment_substitution as _fragment_substitution,
)
from lawvm.uk_legislation.xml_helpers import _tag as _tag  # noqa: F401

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
        append_no_supported_action_rejection(
            effect=effect,
            effect_type=effect_type,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
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
    append_heading_facet_range_expansion_observation(
        effect=effect,
        raw_affected_provisions=raw_affected_provisions,
        targets_str=targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
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
    append_added_type_source_structuralized_observation(
        effect=effect,
        effect_type=effect_type,
        action=action,
        targets_str=targets_str,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
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
        target = refine_crossheading_or_heading_facet_target(
            effect=effect,
            t_str=t_str,
            target=target,
            heading_facet_target=heading_facet_target,
            crossheading_replacement_text=crossheading_replacement_text,
            crossheading_text_patch_fragment=crossheading_text_patch_fragment,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
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
        structural_payload = extract_uk_structural_payload_ir(
            effect=effect,
            action=action,
            target_ref=t_str,
            target=target,
            payload_match_target=payload_match_target,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            fallback_target_eid=_fallback_target_eid,
            lowering_rejections_out=lowering_rejections_out,
        )
        content_ir = structural_payload.content_ir
        actual_el = structural_payload.actual_el
        flat_p1para_schedule_insert_lowered = (
            structural_payload.flat_p1para_schedule_insert_lowered
        )
        source_structural_payload_matches_target = (
            structural_payload.source_structural_payload_matches_target
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
            append_crossheading_group_repeal_observation(
                effect=effect,
                crossheading_group_repeal_selector=crossheading_group_repeal_selector,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
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
        substitution_insert_normalization = lower_substituted_payload_insert_normalization(
            effect=effect,
            curr_action=curr_action,
            original_target_refs=original_targets_str,
            target_index=target_index,
            target_ref=t_str,
            target=target,
            content_ir=content_ir,
            source_replaced_sibling_count=source_replaced_sibling_count,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        curr_action = substitution_insert_normalization.curr_action

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

        text_fragment_lowering = lower_uk_text_fragment_rewrite(
            effect=effect,
            effect_type=effect_type,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
            target=target,
            target_ref=t_str,
            targets_str=targets_str,
            is_word_level=is_word_level,
            heading_facet_target=heading_facet_target,
            source_structural_payload_matches_target=source_structural_payload_matches_target,
            source_carried_table_entry_paragraph_substitution=(
                source_carried_table_entry_paragraph_substitution
            ),
            table_cell_selector=table_cell_selector,
            selector_rule_id=selector_rule_id,
            structural_sibling_insert_detail=structural_sibling_insert_detail,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        if text_fragment_lowering.skip_effect:
            continue
        target = text_fragment_lowering.target
        curr_action = text_fragment_lowering.curr_action
        content_ir = text_fragment_lowering.content_ir
        fragment_subs = text_fragment_lowering.fragment_subs
        op_text_match = text_fragment_lowering.op_text_match
        op_text_replacement = text_fragment_lowering.op_text_replacement
        op_text_occurrence = text_fragment_lowering.op_text_occurrence
        op_text_end_occurrence = text_fragment_lowering.op_text_end_occurrence
        if text_fragment_lowering.unlowered_overlap_reason:
            unlowered_overlap_substitution_targets.append(t_str)
            unlowered_overlap_substitution_reason = (
                text_fragment_lowering.unlowered_overlap_reason
            )

        if curr_action:
            anchor_context = resolve_uk_insertion_anchor_context(
                effect=effect,
                curr_action=curr_action,
                target=target,
                chained_insert_preceding_eid=chained_insert_preceding_eid,
                chained_insert_preceding_eid_source=chained_insert_preceding_eid_source,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
            )
            preceding_eid = anchor_context.preceding_eid
            preceding_eid_source = anchor_context.preceding_eid_source
            following_eid = anchor_context.following_eid
            following_eid_source = anchor_context.following_eid_source

            payload_preparation = prepare_uk_operation_payload_node(
                effect=effect,
                curr_action=curr_action,
                content_ir=content_ir,
                target_ref=t_str,
                target=target,
                payload_match_target=payload_match_target,
                target_replacement_leaf_override=target_replacement_leaf_override,
                target_replacement_leaf_kind=target_replacement_leaf_kind,
                actual_el=actual_el,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                allow_payload_identity_synthesis=allow_payload_identity_synthesis,
                lowering_rejections_out=lowering_rejections_out,
            )
            if payload_preparation.skip_effect:
                continue
            payload_node = payload_preparation.payload_node
            text_patch_items = build_uk_text_patch_items(
                curr_action=curr_action,
                fragment_subs=fragment_subs,
                op_text_match=op_text_match,
                op_text_replacement=op_text_replacement,
                op_text_occurrence=op_text_occurrence,
                op_text_end_occurrence=op_text_end_occurrence,
            )

            append_chained_insertion_anchor_observation(
                lowering_rejections_out,
                effect=effect,
                target_ref=t_str,
                target=target,
                preceding_eid=preceding_eid,
                preceding_eid_source=preceding_eid_source,
                used_chained_insert_anchor=anchor_context.used_chained_insert_anchor,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
            )
            ops.extend(
                build_lowered_operations_for_text_patches(
                    effect=effect,
                    existing_ops_count=len(ops),
                    sequence=sequence,
                    curr_action=curr_action,
                    target=target,
                    payload_node=payload_node,
                    target_ref=t_str,
                    original_targets_str=original_targets_str,
                    targets_str=targets_str,
                    text_patch_items=text_patch_items,
                    op_text_match=op_text_match,
                    op_text_replacement=op_text_replacement,
                    op_text_occurrence=op_text_occurrence,
                    op_text_end_occurrence=op_text_end_occurrence,
                    preceding_eid=preceding_eid,
                    preceding_eid_source=preceding_eid_source,
                    following_eid=following_eid,
                    following_eid_source=following_eid_source,
                    effect_witness=effect_witness,
                    extraction_witness=extraction_witness,
                    table_cell_selector=table_cell_selector,
                    crossheading_group_repeal_selector=crossheading_group_repeal_selector,
                    label_changing_substitution=label_changing_substitution,
                    flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
                    source_parent_substitution_range_payload=source_parent_substitution_range_payload,
                    source_parent_at_end_added_payload=source_parent_at_end_added_payload,
                    target_index=target_index,
                )
            )
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


def _source_context_for_effect(
    *,
    effect: UKEffectRecord,
    source_required_for_replay: bool,
    archive: Any,
    extraction_cache: dict[str, UKAffectingSourceContext],
    effect_diagnostics_out: Optional[list[dict[str, Any]]],
) -> UKAffectingSourceContext:
    """Return the current affecting-source context for one UK effect row."""
    if not source_required_for_replay:
        source_context, _parse_error = _build_affecting_source_context(
            xml_bytes=None,
            locator="",
            authority_layer="EFFECT_FEED_INDEX",
            provision_extractor=extract_provision_element_from_bytes,
        )
        return source_context
    if effect.affecting_act_id in extraction_cache:
        return extraction_cache[effect.affecting_act_id]

    current_locator = f"https://www.legislation.gov.uk/{effect.affecting_act_id}/data.xml"
    source_context, parse_error = _build_affecting_source_context(
        xml_bytes=get_affecting_act_xml_from_archive(effect.affecting_act_id, archive),
        locator=current_locator,
        authority_layer="AFFECTING_ACT_TEXT",
        provision_extractor=extract_provision_element_from_bytes,
    )
    _append_affecting_source_context_diagnostic(
        effect_diagnostics_out,
        effect=effect,
        source_context=source_context,
        parse_error=parse_error,
    )
    extraction_cache[effect.affecting_act_id] = source_context
    return source_context


def _classify_compiled_effect_source_pathology(
    *,
    effect: UKEffectRecord,
    extracted_tag: Optional[str],
    extracted_text: str,
    compiled_ops: list[LegalOperation],
    lowering_rejections: Optional[list[dict[str, Any]]],
    lowering_rejection_start_index: int,
    structural_for_replay: bool,
) -> str:
    from lawvm.uk_legislation.source_adjudication import classify_uk_effect_source_pathology

    return classify_uk_effect_source_pathology(
        extracted_tag=extracted_tag,
        extracted_text=extracted_text,
        op_actions=[_action_name(op.action) for op in compiled_ops],
        payload_kinds=[
            str(op.payload.kind) for op in compiled_ops if op.payload is not None
        ],
        payload_texts=[
            op.payload.text or "" for op in compiled_ops if op.payload is not None
        ],
        target_paths=[
            "/".join(f"{kind}:{label}" for kind, label in op.target.path)
            for op in compiled_ops
        ],
        lowering_rule_ids=[]
        if lowering_rejections is None
        else [
            str(row.get("rule_id") or "")
            for row in lowering_rejections[lowering_rejection_start_index:]
        ],
        effect_type=effect.effect_type,
        is_structural=structural_for_replay,
    )


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
            source_context = _source_context_for_effect(
                effect=e,
                source_required_for_replay=source_required_for_replay,
                archive=archive,
                extraction_cache=extraction_cache,
                effect_diagnostics_out=effect_diagnostics_out,
            )
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
            source_pathology = _classify_compiled_effect_source_pathology(
                effect=e,
                extracted_tag=extracted_tag,
                extracted_text=extracted_text,
                compiled_ops=compiled,
                lowering_rejections=lowering_rejections_out,
                lowering_rejection_start_index=lowering_rejection_count_before,
                structural_for_replay=structural_for_replay,
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

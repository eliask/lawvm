"""UK effect text-fragment lowering."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.heading_facets import (
    _heading_facet_after_anchor_insert_fragment,
    _heading_facet_append_fragment,
    _heading_facet_full_replacement_fragment,
    _heading_facet_source_parent_full_replacement_fragment,
)
from lawvm.uk_legislation.nlp_parser import (
    _ORDINAL_OCCURRENCES,
    _ORDINAL_OCCURRENCE_WORDS,
    US,
    is_whole_node_replacement,
    parse_fragment_substitution,
)
from lawvm.uk_legislation.replay_text import _multi_fragment_text_selector
from lawvm.uk_legislation.source_amendment_program_fragments import (
    _fragment_substitution_amendment_program_inserted_parent_child_insert,
    _fragment_substitution_amendment_inserted_text_substitution,
    _fragment_substitution_source_carried_multi_subunit_repeal,
)
from lawvm.uk_legislation.source_child_tail_rewrites import (
    _fragment_substitution_source_carried_child_list_tail_repeal,
    _fragment_substitution_source_carried_child_tail_repeal,
    _fragment_substitution_source_carried_child_tail_substitution,
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
    append_source_definition_fragment_observations,
    refine_source_definition_child_target,
)
from lawvm.uk_legislation.source_fragment_context import (
    _fragment_substitution_after_words_inserted_by_sibling,
    _fragment_substitution_each_other_place_from_sibling,
    _fragment_substitution_grouped_after_insert_from_parent,
    _fragment_substitution_grouped_anchor_occurrence,
    _fragment_substitution_source_parent_at_end_text_insert,
    _fragment_substitution_source_parent_following_provisions_substitution,
    _fragment_substitution_source_parent_prefix_substitute,
    _fragment_substitution_source_parent_tail_substitution,
    _fragment_substitution_source_parent_word_range_substitution,
    _fragment_substitutions_source_parent_each_provision_substitution,
    append_source_fragment_context_observations,
)
from lawvm.uk_legislation.source_table_entry_paragraph import (
    append_source_carried_table_entry_paragraph_observation,
)
from lawvm.uk_legislation.source_text_reclassifications import (
    lower_quote_only_word_omission,
    source_claims_child_qualified_word_omission,
)
from lawvm.uk_legislation.table_sources import (
    lower_uk_table_driven_corresponding_entry_word_substitution,
)
from lawvm.uk_legislation.table_selectors import (
    _uk_table_column_entry_text_patch_claim,
    _uk_table_entry_text_patch_claim,
    _uk_table_target_column_text_patch_claim,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    _fragment_rule_ids,
    _multi_quoted_word_repeal_fragments,
    append_all_occurrences_text_rewrite_observations,
    append_basic_text_rewrite_observations,
    append_source_carried_substitution_rewrite_observations,
    append_source_carried_tail_rewrite_observations,
    lower_labeled_child_end_range_selector,
    UK_CHILD_QUALIFIED_RANGE_SUBSTITUTION_RULE_ID,
    UK_INTERPRETATION_ENTRIES_RELATING_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_DEFINITION_ENTRY_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_DEFINITION_QUOTED_WORD_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID,
    UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


@dataclass(frozen=True)
class UKTextFragmentLowering:
    target: LegalAddress
    curr_action: Optional[str]
    content_ir: Optional[dict[str, Any]]
    fragment_subs: Optional[list[dict[str, Any]]]
    op_text_match: Optional[str]
    op_text_replacement: Optional[str]
    op_text_occurrence: int
    op_text_end_occurrence: int
    skip_effect: bool = False
    unlowered_overlap_reason: str = ""


def lower_uk_text_fragment_rewrite(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    curr_action: Optional[str],
    content_ir: Optional[dict[str, Any]],
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
    target: LegalAddress,
    target_ref: str,
    targets_str: list[str],
    is_word_level: bool,
    heading_facet_target: bool,
    source_structural_payload_matches_target: bool,
    source_carried_table_entry_paragraph_substitution: Optional[dict[str, Any]],
    table_cell_selector: Optional[str],
    selector_rule_id: str,
    structural_sibling_insert_detail: Optional[dict[str, Any]],
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTextFragmentLowering:
    """Lower source-carried word fragments into typed text patch fields."""
    if not extracted_text:
        return UKTextFragmentLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )

    word_level_text_patch_required = (
        is_word_level
        and curr_action != "repeal"
        and structural_sibling_insert_detail is None
    )
    heading_full_replacement_precheck = (
        _heading_facet_full_replacement_fragment(extracted_text) if heading_facet_target else None
    )
    heading_source_parent_full_replacement_precheck = (
        _heading_facet_source_parent_full_replacement_fragment(
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if heading_facet_target and not is_word_level
        else None
    )
    heading_facet_text_patch_required = (
        heading_facet_target
        and not is_word_level
        and (
            heading_full_replacement_precheck is not None
            or heading_source_parent_full_replacement_precheck is not None
        )
    )
    source_parent_at_end_text_insert_precheck = (
        _fragment_substitution_source_parent_at_end_text_insert(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if word_level_text_patch_required and curr_action == "insert"
        else None
    )
    if fragment_subs is not None or not (
        curr_action == "replace"
        or word_level_text_patch_required
        or heading_facet_text_patch_required
        or source_parent_at_end_text_insert_precheck is not None
    ):
        return UKTextFragmentLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )

    treat_as_source_structural_replace = (
        curr_action == "replace"
        and not is_word_level
        and source_structural_payload_matches_target
    )
    source_carried_definition_child_text_omission_precheck = (
        _fragment_substitution_source_carried_definition_child_text_omission(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
    )
    if treat_as_source_structural_replace or (
            source_carried_definition_child_text_omission_precheck is None
            and heading_full_replacement_precheck is None
            and heading_source_parent_full_replacement_precheck is None
            and is_whole_node_replacement(extracted_text, effect.effect_type)
    ):
        return UKTextFragmentLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )

    table_substitution = lower_uk_table_driven_corresponding_entry_word_substitution(
        effect=effect,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        target=target,
        target_ref=target_ref,
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
        return UKTextFragmentLowering(
            target=target,
            curr_action=curr_action,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
            skip_effect=True,
        )

    if is_word_level:
        quote_only_omission_lowering = lower_quote_only_word_omission(
            effect=effect,
            effect_type=effect_type,
            curr_action=curr_action,
            content_ir=content_ir,
            is_word_level=is_word_level,
            targets_str=targets_str,
            target=target,
            target_ref=target_ref,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        if quote_only_omission_lowering.applied:
            return UKTextFragmentLowering(
                target=target,
                curr_action=quote_only_omission_lowering.curr_action,
                content_ir=quote_only_omission_lowering.content_ir,
                fragment_subs=quote_only_omission_lowering.fragment_subs,
                op_text_match=quote_only_omission_lowering.op_text_match,
                op_text_replacement=quote_only_omission_lowering.op_text_replacement,
                op_text_occurrence=(
                    quote_only_omission_lowering.op_text_occurrence
                    if quote_only_omission_lowering.op_text_occurrence is not None
                    else op_text_occurrence
                ),
                op_text_end_occurrence=op_text_end_occurrence,
            )
        if source_claims_child_qualified_word_omission(
            effect_type=effect_type,
            extracted_text=extracted_text,
        ):
            return UKTextFragmentLowering(
                target=target,
                curr_action=None,
                content_ir=content_ir,
                fragment_subs=fragment_subs,
                op_text_match=op_text_match,
                op_text_replacement=op_text_replacement,
                op_text_occurrence=op_text_occurrence,
                op_text_end_occurrence=op_text_end_occurrence,
                unlowered_overlap_reason="child_qualified_word_omission_target_mismatch",
            )

    subs = _extract_text_fragment_substitutions(
        effect=effect,
        table_substitution_recognized=table_substitution.recognized,
        fragment_subs=fragment_subs,
        heading_facet_target=heading_facet_target,
        source_carried_definition_child_text_omission_precheck=(
            source_carried_definition_child_text_omission_precheck
        ),
        source_carried_table_entry_paragraph_substitution=(
            source_carried_table_entry_paragraph_substitution
        ),
        target=target,
        target_ref=target_ref,
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
        allow_heading_source_parent_full_replacement=not is_word_level,
        allow_source_parent_at_end_text_insert=(
            word_level_text_patch_required and curr_action == "insert"
        ),
        allow_source_parent_word_range_substitution=(
            word_level_text_patch_required and curr_action == "replace"
        ),
    )
    if subs:
        return _promote_text_fragment_substitutions(
            effect=effect,
            curr_action=curr_action,
            subs=subs,
            is_word_level=is_word_level,
            target=target,
            target_ref=target_ref,
            table_cell_selector=table_cell_selector,
            selector_rule_id=selector_rule_id,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )

    simple_omission = _simple_quoted_omission_fragment(extracted_text)
    if simple_omission is not None:
        return UKTextFragmentLowering(
            target=target,
            curr_action="text_repeal" if is_word_level else "text_replace",
            content_ir=None,
            fragment_subs=[simple_omission],
            op_text_match=simple_omission["original"],
            op_text_replacement="",
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )

    if (
        is_word_level
        and effect.effect_type == "substituted for words"
        and content_ir is not None
        and content_ir.get("kind") == _addr_leaf_kind(target)
        and _clean_num(str(content_ir.get("label") or "")) == _clean_num(_addr_leaf_label(target) or "")
    ):
        # Some archive-backed UK effects are labeled as word-level substitutions even
        # though the source carries the fully substituted structural node.
        return UKTextFragmentLowering(
            target=target,
            curr_action="replace",
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
        )

    if is_word_level:
        quote_only_omission_lowering = lower_quote_only_word_omission(
            effect=effect,
            effect_type=effect_type,
            curr_action=curr_action,
            content_ir=content_ir,
            is_word_level=is_word_level,
            targets_str=targets_str,
            target=target,
            target_ref=target_ref,
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        if quote_only_omission_lowering.applied:
            return UKTextFragmentLowering(
                target=target,
                curr_action=quote_only_omission_lowering.curr_action,
                content_ir=quote_only_omission_lowering.content_ir,
                fragment_subs=quote_only_omission_lowering.fragment_subs,
                op_text_match=quote_only_omission_lowering.op_text_match,
                op_text_replacement=quote_only_omission_lowering.op_text_replacement,
                op_text_occurrence=(
                    quote_only_omission_lowering.op_text_occurrence
                    if quote_only_omission_lowering.op_text_occurrence is not None
                    else op_text_occurrence
                ),
                op_text_end_occurrence=op_text_end_occurrence,
            )
        return UKTextFragmentLowering(
            target=target,
            curr_action=None,
            content_ir=content_ir,
            fragment_subs=fragment_subs,
            op_text_match=op_text_match,
            op_text_replacement=op_text_replacement,
            op_text_occurrence=op_text_occurrence,
            op_text_end_occurrence=op_text_end_occurrence,
            unlowered_overlap_reason=(
                "overlap_substitution_arity_unsupported"
                if len(targets_str) > 1
                else "overlap_substitution_parse_failed"
            ),
        )

    return UKTextFragmentLowering(
        target=target,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=fragment_subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
    )


def _extract_text_fragment_substitutions(
    *,
    effect: UKEffectRecord,
    table_substitution_recognized: bool,
    fragment_subs: Optional[list[dict[str, Any]]],
    heading_facet_target: bool,
    source_carried_definition_child_text_omission_precheck: Optional[dict[str, Any]],
    source_carried_table_entry_paragraph_substitution: Optional[dict[str, Any]],
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: str,
    allow_heading_source_parent_full_replacement: bool = True,
    allow_source_parent_at_end_text_insert: bool = False,
    allow_source_parent_word_range_substitution: bool = False,
) -> list[dict[str, Any]]:
    heading_after_anchor_insert = (
        _heading_facet_after_anchor_insert_fragment(extracted_text) if heading_facet_target else None
    )
    heading_append = (
        _heading_facet_append_fragment(extracted_text) if heading_facet_target else None
    )
    heading_full_replacement = (
        _heading_facet_full_replacement_fragment(extracted_text) if heading_facet_target else None
    )
    heading_source_parent_full_replacement = (
        _heading_facet_source_parent_full_replacement_fragment(
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if heading_facet_target and allow_heading_source_parent_full_replacement
        else None
    )
    subs = (
        fragment_subs
        if table_substitution_recognized
        else [source_carried_definition_child_text_omission_precheck]
        if source_carried_definition_child_text_omission_precheck is not None
        else [heading_append]
        if heading_append is not None
        else [heading_after_anchor_insert]
        if heading_after_anchor_insert is not None
        else [heading_full_replacement]
        if heading_full_replacement is not None
        else [heading_source_parent_full_replacement]
        if heading_source_parent_full_replacement is not None
        else parse_fragment_substitution(extracted_text)
    )
    if not subs:
        beginning_each_child_insert = _effect_beginning_each_child_text_insert_fragment(
            target=target,
            extracted_text=extracted_text,
        )
        if beginning_each_child_insert is not None:
            subs = [beginning_each_child_insert]
    if not subs:
        at_end_each_child_insert = _effect_at_end_each_child_text_insert_fragment(
            target=target,
            extracted_text=extracted_text,
        )
        if at_end_each_child_insert is not None:
            subs = [at_end_each_child_insert]
    multi_quoted_word_repeals = _multi_quoted_word_repeal_fragments(
        extracted_text=extracted_text,
        effect_type=effect.effect_type,
    )
    if (
        multi_quoted_word_repeals
        and len(subs) == 1
        and (
            _multi_fragment_text_selector(str(subs[0].get("original") or ""))
            or str(subs[0].get("replacement") or "") == ""
        )
    ):
        subs = list(multi_quoted_word_repeals)
    if not subs:
        table_column_entry_text_patch = _uk_table_column_entry_text_patch_claim(
            target_ref=target_ref,
            target=target,
            extracted_text=extracted_text,
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if table_column_entry_text_patch is not None:
            subs = [
                {
                    "original": str(table_column_entry_text_patch["text_patch_original"]),
                    "replacement": str(table_column_entry_text_patch["text_patch_replacement"]),
                    "rule_id": str(table_column_entry_text_patch["rule_id"]),
                    "column_index": str(table_column_entry_text_patch["column_index"]),
                    "match_text": str(table_column_entry_text_patch["match_text"]),
                    "table_column_entry_action": str(
                        table_column_entry_text_patch["table_column_entry_action"]
                    ),
                }
            ]
    if not subs:
        table_entry_text_patch = _uk_table_entry_text_patch_claim(
            target_ref=target_ref,
            target=target,
            extracted_text=extracted_text,
        )
        if table_entry_text_patch is not None:
            subs = [
                {
                    "original": str(table_entry_text_patch["text_patch_original"]),
                    "replacement": str(table_entry_text_patch["text_patch_replacement"]),
                    "rule_id": str(table_entry_text_patch["rule_id"]),
                    "match_text": str(table_entry_text_patch["match_text"]),
                    "table_entry_action": str(table_entry_text_patch["table_entry_action"]),
                }
            ]
    if not subs:
        table_target_column_text_patch = _uk_table_target_column_text_patch_claim(
            target_ref=target_ref,
            target=target,
            extracted_text=extracted_text,
            extracted_el=extracted_el,
            source_root=source_root,
        )
        if table_target_column_text_patch is not None:
            subs = [
                {
                    "original": str(table_target_column_text_patch["text_patch_original"]),
                    "replacement": str(table_target_column_text_patch["text_patch_replacement"]),
                    "rule_id": str(table_target_column_text_patch["rule_id"]),
                    "column_index": str(table_target_column_text_patch["column_index"]),
                    "match_text": str(table_target_column_text_patch["match_text"]),
                    "table_column_text_action": str(
                        table_target_column_text_patch["table_column_text_action"]
                    ),
                }
            ]
    if not subs:
        metadata_carried_word_repeal = _effect_metadata_carried_quoted_words_repeal_fragment(
            effect_type=effect.effect_type,
            extracted_text=extracted_text,
        )
        if metadata_carried_word_repeal is not None:
            subs = [metadata_carried_word_repeal]
    if not subs:
        ordinal_sentence_beginning_repeal = _effect_ordinal_sentence_beginning_repeal_fragment(
            effect_type=effect.effect_type,
            extracted_text=extracted_text,
        )
        if ordinal_sentence_beginning_repeal is not None:
            subs = [ordinal_sentence_beginning_repeal]
    if not subs:
        scoped_metadata_carried_word_repeals = (
            _effect_metadata_carried_scoped_quoted_words_repeal_fragments(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
                target=target,
            )
        )
        if scoped_metadata_carried_word_repeals:
            subs = list(scoped_metadata_carried_word_repeals)
    if not subs:
        metadata_carried_after_ordinal_insert = (
            _effect_metadata_carried_after_ordinal_insert_fragment(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_after_ordinal_insert is not None:
            subs = [metadata_carried_after_ordinal_insert]
    if not subs:
        metadata_carried_definition_entry_repeals = (
            _effect_metadata_carried_definition_entry_repeal_fragments(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_definition_entry_repeals:
            subs = list(metadata_carried_definition_entry_repeals)
    if not subs:
        metadata_carried_definition_quoted_word_repeal = (
            _effect_metadata_carried_definition_quoted_word_repeal_fragment(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_definition_quoted_word_repeal is not None:
            subs = [metadata_carried_definition_quoted_word_repeal]
    if not subs:
        interpretation_entry_repeals = _effect_interpretation_entries_relating_repeal_fragments(
            effect=effect,
            target=target,
            extracted_text=extracted_text,
        )
        if interpretation_entry_repeals:
            subs = list(interpretation_entry_repeals)
    if not subs:
        child_qualified_range_substitution = _effect_child_qualified_range_substitution_fragment(
            effect=effect,
            target=target,
            extracted_text=extracted_text,
        )
        if child_qualified_range_substitution is not None:
            subs = [child_qualified_range_substitution]
    if not subs:
        after_inserted_by_sibling = _fragment_substitution_after_words_inserted_by_sibling(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if after_inserted_by_sibling is not None:
            subs = [after_inserted_by_sibling]
    if not subs:
        each_other_place_from_sibling = _fragment_substitution_each_other_place_from_sibling(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if each_other_place_from_sibling is not None:
            subs = [each_other_place_from_sibling]
    if not subs:
        grouped_anchor_occurrence = _fragment_substitution_grouped_anchor_occurrence(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if grouped_anchor_occurrence is not None:
            subs = [grouped_anchor_occurrence]
    if not subs:
        grouped_after_insert = _fragment_substitution_grouped_after_insert_from_parent(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if grouped_after_insert is not None:
            subs = [grouped_after_insert]
    if not subs:
        source_parent_tail_substitution = (
            _fragment_substitution_source_parent_tail_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
        )
        if source_parent_tail_substitution is not None:
            subs = [source_parent_tail_substitution]
    if not subs:
        source_parent_following_provisions_substitution = (
            _fragment_substitution_source_parent_following_provisions_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
        )
        if source_parent_following_provisions_substitution is not None:
            subs = [source_parent_following_provisions_substitution]
    if not subs:
        source_parent_prefix_substitute = (
            _fragment_substitution_source_parent_prefix_substitute(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
        )
        if source_parent_prefix_substitute is not None:
            subs = [source_parent_prefix_substitute]
    if not subs:
        source_parent_each_provision_substitution = (
            _fragment_substitutions_source_parent_each_provision_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
        )
        if source_parent_each_provision_substitution:
            subs = list(source_parent_each_provision_substitution)
    if not subs and allow_source_parent_at_end_text_insert:
        source_parent_at_end_insert = _fragment_substitution_source_parent_at_end_text_insert(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if source_parent_at_end_insert is not None:
            subs = [source_parent_at_end_insert]
    if not subs and allow_source_parent_word_range_substitution:
        source_parent_word_range_substitution = (
            _fragment_substitution_source_parent_word_range_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
            )
        )
        if source_parent_word_range_substitution is not None:
            subs = [source_parent_word_range_substitution]
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
        source_carried_child_list_tail_repeal = (
            _fragment_substitution_source_carried_child_list_tail_repeal(
                extracted_text=extracted_text,
                target=target,
            )
        )
        if source_carried_child_list_tail_repeal is not None:
            subs = [source_carried_child_list_tail_repeal]
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
        amendment_program_child_insert = (
            _fragment_substitution_amendment_program_inserted_parent_child_insert(
                extracted_text=extracted_text,
                target=target,
            )
        )
        if amendment_program_child_insert is not None:
            subs = [amendment_program_child_insert]
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
        filtered_subs = []
        for sub in subs:
            orig = str(sub.get("original") or "")
            if orig.startswith("TEXT_REPLACE_CHILDREN_"):
                parts = orig[len("TEXT_REPLACE_CHILDREN_") :].split("_")
                if parts:
                    child_kind = parts[0].lower()
                    if target.path and target.path[-1][0].lower() == child_kind:
                        continue
            filtered_subs.append(sub)
        subs = filtered_subs
    return list(subs or [])


def _promote_text_fragment_substitutions(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    subs: list[dict[str, Any]],
    is_word_level: bool,
    target: LegalAddress,
    target_ref: str,
    table_cell_selector: Optional[str],
    selector_rule_id: str,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: str,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTextFragmentLowering:
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

    primary = subs[0]
    target = refine_source_definition_child_target(
        effect=effect,
        target=target,
        fragment=primary,
        target_ref=target_ref,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    labeled_child_end_range_lowering = lower_labeled_child_end_range_selector(
        effect=effect,
        target=target,
        target_ref=target_ref,
        primary=primary,
        curr_action=curr_action,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    primary = labeled_child_end_range_lowering.primary
    curr_action = labeled_child_end_range_lowering.curr_action
    if labeled_child_end_range_lowering.skip_effect:
        return UKTextFragmentLowering(
            target=target,
            curr_action=curr_action,
            content_ir=None,
            fragment_subs=subs,
            op_text_match=None,
            op_text_replacement=None,
            op_text_occurrence=0,
            op_text_end_occurrence=0,
            skip_effect=True,
        )

    op_text_match = primary["original"]
    op_text_replacement = primary["replacement"]
    op_text_occurrence = int(primary.get("occurrence", "0") or "0")
    op_text_end_occurrence = int(primary.get("end_occurrence", "0") or "0")
    if is_word_level and op_text_replacement == "":
        curr_action = "text_repeal"
    else:
        curr_action = "text_replace"

    append_all_occurrences_text_rewrite_observations(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_subs=subs,
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
        target_ref=target_ref,
        fragment_subs=subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    append_source_definition_fragment_observations(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_subs=subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    append_source_carried_tail_rewrite_observations(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_subs=subs,
        primary=primary,
        op_text_match=op_text_match,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    append_source_carried_table_entry_paragraph_observation(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_rule_ids=_fragment_rule_ids(subs),
        primary=primary,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    append_source_carried_substitution_rewrite_observations(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_subs=subs,
        primary=primary,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    append_source_fragment_context_observations(
        effect=effect,
        target=target,
        target_ref=target_ref,
        fragment_subs=subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
    return UKTextFragmentLowering(
        target=target,
        curr_action=curr_action,
        content_ir=None,
        fragment_subs=subs,
        op_text_match=op_text_match,
        op_text_replacement=op_text_replacement,
        op_text_occurrence=op_text_occurrence,
        op_text_end_occurrence=op_text_end_occurrence,
    )


def _simple_quoted_omission_fragment(extracted_text: str) -> Optional[dict[str, str]]:
    open_quotes = "\"\u201c\u2018'"
    close_quotes = "\"\u201d\u2019'"
    m_omit = re.search(
        "(?:omit|repeal) [" + open_quotes + "](.*?)[" + close_quotes + "]",
        extracted_text,
        re.I,
    )
    if not m_omit:
        m_omit = re.search(
            "[" + open_quotes + "](.*?)[" + close_quotes + "] is (?:omitted|repealed)",
            extracted_text,
            re.I,
        )
    if not m_omit:
        return None
    return {"original": m_omit.group(1), "replacement": ""}


def _effect_beginning_each_child_text_insert_fragment(
    *,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    text = " ".join(str(extracted_text or "").split())
    match = re.search(
        r"\bat\s+the\s+beginning\s+of\s+(?:each\s+of\s+)?"
        r"(?P<kind>paragraphs?|sub-?paragraphs?|subsections?)\s+"
        r"(?P<labels>[^.;]+?)\s+"
        r"(?:insert|there\s+(?:is|are|shall\s+be)\s+inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    source_kind = re.sub(r"[^a-z]+", "", match.group("kind").lower())
    if source_kind.endswith("s"):
        source_kind = source_kind[:-1]
    if _addr_leaf_kind(target) != source_kind:
        return None
    target_label = _clean_num(_addr_leaf_label(target) or "")
    labels = [_clean_num(label) for label in re.findall(r"\(([0-9A-Za-z]+)\)", match.group("labels"))]
    if target_label not in labels or len(labels) < 2:
        return None
    return {
        "original": "TEXT_BEGINNING",
        "replacement": match.group("inserted").strip(),
        "rule_id": "uk_effect_beginning_each_child_text_insertion_patch",
    }


def _effect_at_end_each_child_text_insert_fragment(
    *,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    text = " ".join(str(extracted_text or "").split())
    match = re.search(
        r"\bat\s+the\s+end\s+of\s+(?:each\s+of\s+)?"
        r"(?P<kind>paragraphs?|sub-?paragraphs?|subsections?)\s+"
        r"(?P<labels>[^.;]+?)\s+"
        r"(?:insert|there\s+(?:is|are|shall\s+be)\s+inserted)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    source_kind = re.sub(r"[^a-z]+", "", match.group("kind").lower())
    if source_kind.endswith("s"):
        source_kind = source_kind[:-1]
    if _addr_leaf_kind(target) != source_kind:
        return None
    target_label = _clean_num(_addr_leaf_label(target) or "")
    labels = [_clean_num(label) for label in re.findall(r"\(([0-9A-Za-z]+)\)", match.group("labels"))]
    if target_label not in labels or len(labels) < 2:
        return None
    return {
        "original": "TEXT_FROM__TO_END",
        "replacement": match.group("inserted").strip(),
        "rule_id": "uk_effect_at_end_each_child_text_insertion_patch",
    }


def _effect_ordinal_sentence_beginning_repeal_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word omitted", "words omitted", "word repealed", "words repealed"}:
        return None
    text = " ".join(str(extracted_text or "").split())
    match = re.search(
        r"\b(?:omit|repeal)\s+(?:the\s+)?"
        rf"(?P<ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+sentence\s+"
        r"beginning\s+[“\"'‘](?P<anchor>.*?)[”\"'’]",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    ordinal = _ORDINAL_OCCURRENCES.get(match.group("ordinal").lower())
    anchor = " ".join(match.group("anchor").split()).strip()
    if not ordinal or not anchor:
        return None
    return {
        "original": f"TEXT_SENTENCE_{ordinal}{US}BEGINNING{US}{anchor}",
        "replacement": "",
        "rule_id": "uk_effect_ordinal_sentence_beginning_repeal_text_patch",
    }


def _effect_metadata_carried_quoted_words_repeal_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    table_entry_scoped_quote = (
        re.search(r"\btable\b", text, flags=re.I) is not None
        and re.search(r"\bentry\s+relating\s+to\b", text, flags=re.I) is not None
    )
    if (
        not text
        or (
            not table_entry_scoped_quote
            and not re.search(r"\bthe\s+words?\b", text, flags=re.I)
        )
    ):
        return None
    if re.search(r"\bwhere\s+they\s+occur\b", text, flags=re.I):
        return None
    if re.search(
        r"\bin\s+(?:subsection|paragraph|sub-?paragraph)\s*\([^)]+\)(?:\([^)]+\))?",
        text,
        flags=re.I,
    ):
        return None
    quote_matches = tuple(re.finditer(r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\")", text))
    quoted = tuple(
        match.group("curly") if match.group("curly") is not None else match.group("double")
        for match in quote_matches
    )
    quoted = tuple(" ".join(fragment.split()).strip() for fragment in quoted if " ".join(fragment.split()).strip())
    if len(quoted) != 1 or len(quote_matches) != 1:
        return None
    tail = text[quote_matches[0].end() :]
    if re.search(r"\bin\s+(?:paragraph|sub-?paragraph|subsection)\b", tail, flags=re.I):
        return None
    return {
        "original": quoted[0],
        "replacement": "",
        "rule_id": UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID,
    }


def _target_section_ref_pattern(target: LegalAddress) -> Optional[re.Pattern[str]]:
    if len(target.path) < 3 or target.path[0][0] != "section":
        return None
    section_label = _clean_num(target.path[0][1])
    if not section_label:
        return None
    pieces = [rf"section\s+{re.escape(section_label)}"]
    for kind, label in target.path[1:]:
        if kind not in {"subsection", "paragraph", "subparagraph", "item"}:
            continue
        clean_label = _clean_num(label)
        if not clean_label:
            continue
        pieces.append(rf"\s*\(\s*{re.escape(clean_label)}\s*\)")
    if len(pieces) == 1:
        return None
    return re.compile("".join(pieces), flags=re.I)


def _metadata_carried_quote_scope_matches_target(text: str, target: LegalAddress) -> bool:
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if target_kind in {"paragraph", "subparagraph", "subsection"} and target_label:
        source_kind_pattern = {
            "paragraph": r"paragraph",
            "subparagraph": r"sub-?paragraph",
            "subsection": r"subsection",
        }[target_kind]
        if re.search(
            rf"\bin\s+(?:the\s+)?{source_kind_pattern}\s*\(\s*{re.escape(target_label)}\s*\)",
            text,
            flags=re.I,
        ):
            return True
    section_ref_pattern = _target_section_ref_pattern(target)
    if section_ref_pattern is not None and section_ref_pattern.search(text):
        return True
    return False


def _effect_metadata_carried_scoped_quoted_words_repeal_fragments(
    *,
    effect_type: str,
    extracted_text: str,
    target: LegalAddress,
) -> tuple[dict[str, str], ...]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return ()
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return ()
    if re.search(r"\b(?:table|column|entry|definitions?)\b", text, flags=re.I):
        return ()
    if re.search(r"\b(?:omit|omitted|repeal|repealed|insert|inserted|substitute|substituted)\b", text, flags=re.I):
        return ()
    if not _metadata_carried_quote_scope_matches_target(text, target):
        return ()
    quoted = tuple(
        " ".join((match.group("curly") or match.group("double") or "").split()).strip()
        for match in re.finditer(r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\")", text)
    )
    quoted = tuple(fragment for fragment in quoted if fragment)
    if not quoted:
        return ()
    return tuple(
        {
            "original": fragment,
            "replacement": "",
            "rule_id": UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID,
        }
        for fragment in quoted
    )


def _effect_metadata_carried_after_ordinal_insert_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text or re.search(r"\b(?:insert|substitute|omit|repeal)\b", text, flags=re.I):
        return None
    match = re.search(
        rf"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){{0,2}}"
        rf"after\s+(?:the\s+words?\s+)?[“\"](?P<anchor>.*?)[”\"],?\s+"
        rf"where\s+(?P<ordinal>{_ORDINAL_OCCURRENCE_WORDS})\s+"
        rf"(?:occurs?|occurring|appears?|appear),?\s+"
        rf"[“\"](?P<inserted>.*?)[”\"]\s*(?:[,;]\s*(?:and)?\s*)?$",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    anchor = match.group("anchor")
    inserted = match.group("inserted")
    joiner = (
        ""
        if anchor.endswith((" ", "\t", "\n", "\r"))
        or inserted.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    return {
        "original": anchor.strip(),
        "replacement": f"{anchor}{joiner}{inserted}".strip(),
        "occurrence": _ORDINAL_OCCURRENCES[match.group("ordinal").lower()],
        "rule_id": UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID,
    }


def _effect_interpretation_entries_relating_repeal_fragments(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> tuple[dict[str, str], ...]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return ()
    if not target.path or target.path[-1][0] != "subsection":
        return ()
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return ()
    if not re.search(
        r"\bsection\s+[0-9]+[A-Za-z]?\s*\([^)]+\)\s*\(\s*interpretation\s*\)",
        text,
        flags=re.I,
    ):
        return ()
    if re.search(r"\b(?:table|column|schedule)\b", text, flags=re.I):
        return ()
    match = re.search(
        r"\b(?:the\s+)?entries\s+relating\s+to\s+(?P<terms>.+?)\s+"
        r"(?:are|is|shall\s+be)\s+(?:repealed|omitted)\b",
        text,
        flags=re.I,
    )
    if match is None:
        return ()
    term_parts = [part.strip(" \t\r\n,.;:") for part in re.split(r"\s+and\s+", match.group("terms"))]
    terms = tuple(part for part in term_parts if part)
    if not terms:
        return ()
    if len(terms) > 1 and any(not re.match(r"(?i)^the\s+[A-Z]", term) for term in terms):
        return ()
    fragments = []
    for term in terms:
        if not re.match(r"(?i)^(?:the\s+)?[A-Z][A-Za-z0-9&'(). /-]{1,140}$", term):
            return ()
        fragments.append(
            {
                "original": f"TEXT_DEFINITION_ENTRY_{term}",
                "replacement": "",
                "rule_id": UK_INTERPRETATION_ENTRIES_RELATING_REPEAL_RULE_ID,
            }
        )
    return tuple(fragments)


def _effect_child_qualified_range_substitution_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words substituted", "substituted for words"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    match = re.search(
        r"\bfor\s+the\s+words\s+in\s+"
        r"(?P<kind>subsection|paragraph|sub-?paragraph)\s*"
        r"\(\s*(?P<label>[0-9A-Za-z]+)\s*\)\s+"
        r"from\s+[“\"](?P<start>.*?)[”\"]\s+to\s+[“\"](?P<end>.*?)[”\"]\s+"
        r"(?:there\s+shall\s+be\s+substituted|substitute)\s+[“\"](?P<replacement>.*?)[”\"]",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return None
    source_kind = match.group("kind").replace("-", "").lower()
    source_kind = "subparagraph" if source_kind == "subparagraph" else source_kind
    source_label = _clean_num(str(match.group("label") or ""))
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if source_kind != target_kind or not source_label or source_label != target_label:
        return None
    start = " ".join(match.group("start").split()).strip()
    end = " ".join(match.group("end").split()).strip()
    replacement = " ".join(match.group("replacement").split()).strip()
    if not start or not end or not replacement:
        return None
    return {
        "original": f"TEXT_FROM_{start}_TO_{end}",
        "replacement": replacement,
        "rule_id": UK_CHILD_QUALIFIED_RANGE_SUBSTITUTION_RULE_ID,
    }


def _effect_metadata_carried_definition_entry_repeal_fragments(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> tuple[dict[str, str], ...]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return ()
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return ()
    match = re.search(
        r"\bin\s+(?P<kind>subsection|paragraph|sub-?paragraph)\s*"
        r"\(\s*(?P<label>[0-9A-Za-z]+)\s*\)\s*,?\s+"
        r"(?:the\s+)?definitions?\s+of\s+(?P<terms>.+?)\s*[.;]?\s*$",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return ()
    source_kind = match.group("kind").replace("-", "").lower()
    source_kind = "subparagraph" if source_kind == "subparagraph" else source_kind
    source_label = _clean_num(str(match.group("label") or ""))
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if source_kind != target_kind or not source_label or source_label != target_label:
        return ()
    terms = tuple(
        " ".join((quoted.group("curly") or quoted.group("double") or "").split()).strip()
        for quoted in re.finditer(
            r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\")",
            match.group("terms"),
        )
    )
    terms = tuple(term for term in terms if term)
    if not terms:
        return ()
    return tuple(
        {
            "original": f"TEXT_DEFINITION_ENTRY_{term}",
            "replacement": "",
            "rule_id": UK_METADATA_CARRIED_DEFINITION_ENTRY_REPEAL_RULE_ID,
        }
        for term in terms
    )


def _effect_metadata_carried_definition_quoted_word_repeal_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    if re.search(r"\b(?:table|column|entry)\b", text, flags=re.I):
        return None
    match = re.search(
        r"\bin\s+the\s+definition\s+of\s+[“\"](?P<term>.*?)[”\"]\s+"
        r"in\s+section\s+(?P<section>[0-9]+[A-Za-z]?)\s+"
        r"(?:the\s+)?words?\s+[“\"](?P<fragment>.*?)[”\"]",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return None
    if not target.path or target.path[0][0] != "section":
        return None
    source_section = _clean_num(match.group("section"))
    target_section = _clean_num(target.path[0][1])
    if not source_section or source_section != target_section:
        return None
    term = " ".join(match.group("term").split()).strip()
    fragment = " ".join(match.group("fragment").split()).strip()
    if not term or not fragment:
        return None
    return {
        "original": f"TEXT_IN_DEFINITION_{term}\x1fDELETE\x1f{fragment}",
        "replacement": "",
        "rule_id": UK_METADATA_CARRIED_DEFINITION_QUOTED_WORD_REPEAL_RULE_ID,
    }

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
    _heading_facet_full_replacement_fragment,
)
from lawvm.uk_legislation.nlp_parser import is_whole_node_replacement, parse_fragment_substitution
from lawvm.uk_legislation.replay_text import _multi_fragment_text_selector
from lawvm.uk_legislation.source_amendment_program_fragments import (
    _fragment_substitution_amendment_program_inserted_parent_child_insert,
    _fragment_substitution_amendment_inserted_text_substitution,
    _fragment_substitution_source_carried_multi_subunit_repeal,
)
from lawvm.uk_legislation.source_child_tail_rewrites import (
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
    _fragment_substitution_grouped_after_insert_from_parent,
    _fragment_substitution_grouped_anchor_occurrence,
    append_source_fragment_context_observations,
)
from lawvm.uk_legislation.source_table_entry_paragraph import (
    append_source_carried_table_entry_paragraph_observation,
)
from lawvm.uk_legislation.source_text_reclassifications import lower_quote_only_word_omission
from lawvm.uk_legislation.table_sources import (
    lower_uk_table_driven_corresponding_entry_word_substitution,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    _fragment_rule_ids,
    _multi_quoted_word_repeal_fragments,
    append_all_occurrences_text_rewrite_observations,
    append_basic_text_rewrite_observations,
    append_source_carried_substitution_rewrite_observations,
    append_source_carried_tail_rewrite_observations,
    lower_labeled_child_end_range_selector,
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
    if fragment_subs is not None or not (curr_action == "replace" or word_level_text_patch_required):
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
    if treat_as_source_structural_replace or (
        source_carried_definition_child_text_omission_precheck is None
        and heading_full_replacement_precheck is None
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
        extracted_el=extracted_el,
        source_root=source_root,
        extracted_text=extracted_text,
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
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: str,
) -> list[dict[str, Any]]:
    heading_after_anchor_insert = (
        _heading_facet_after_anchor_insert_fragment(extracted_text) if heading_facet_target else None
    )
    heading_full_replacement = (
        _heading_facet_full_replacement_fragment(extracted_text) if heading_facet_target else None
    )
    subs = (
        fragment_subs
        if table_substitution_recognized
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
    if not subs:
        grouped_after_insert = _fragment_substitution_grouped_after_insert_from_parent(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if grouped_after_insert is not None:
            subs = [grouped_after_insert]
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

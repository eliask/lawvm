"""UK effect text-fragment lowering."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD,
    TARGET_RECOVERED,
    TargetResolutionCandidate,
    TargetResolutionCoverage,
)
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
    UK_AFTER_QUOTED_ANCHOR_EXCEPT_CHILD_INSERT_RULE_ID,
    UK_IN_DEFINITION_AT_END_TARGET_CONTEXT_INSERT_RULE_ID,
    US,
    is_whole_node_replacement,
    parse_fragment_substitution,
)
from lawvm.uk_legislation.replay_text import _multi_fragment_text_selector
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.source_amendment_program_fragments import (
    UK_AMENDMENT_PROGRAM_INSERTED_ANCHOR_STRUCTURAL_INSERT_RULE_ID,
    _fragment_substitution_amendment_program_inserted_parent_child_insert,
    _fragment_substitution_amendment_inserted_text_substitution,
    _fragment_substitution_source_carried_multi_subunit_repeal,
    _fragment_substitution_source_carried_multi_subunit_substitution,
)
from lawvm.uk_legislation.source_child_tail_rewrites import (
    _fragment_substitution_source_carried_between_paragraphs_substitution,
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
    _fragment_substitution_source_parent_after_anchor_to_end_substitution,
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
    UK_EFFECT_WORD_SUBSTITUTION_PARENT_CHILD_REPLACEMENT_RULE_ID,
    UK_EFFECT_WORD_SUBSTITUTION_STRUCTURAL_CHILD_REPLACEMENT_RULE_ID,
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
    UK_DEFINITION_ANCHOR_FINAL_PUNCTUATION_SUBSTITUTION_RULE_ID,
    UK_DEFINITION_ANCHOR_TAIL_INSERT_RULE_ID,
    UK_INTERPRETATION_ENTRIES_RELATING_REPEAL_RULE_ID,
    UK_AMOUNT_SPECIFIED_SUBSTITUTION_RULE_ID,
    UK_METADATA_CARRIED_DEFINITION_ENTRY_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_DEFINITION_QUOTED_WORD_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_OMITTING_WORDS_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_SUBSTITUTING_WORDS_RULE_ID,
    UK_MIXED_STRUCTURAL_TEXT_REWRITE_TEXT_HALF_REPEAL_RULE_ID,
    UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID,
    UK_METADATA_CARRIED_AFTER_SUBSTITUTE_INSERT_RULE_ID,
    UK_AFTER_ANCHOR_SUBSTITUTE_TAIL_SUBSTITUTION_RULE_ID,
    UK_NEGATIVE_LEFT_CONTEXT_EXCLUDED_CHILDREN_SUBSTITUTION_RULE_ID,
    UK_METADATA_CARRIED_AT_END_ADD_INSERT_RULE_ID,
    UK_METADATA_CARRIED_AT_END_INSERT_QUOTED_RULE_ID,
    UK_METADATA_CARRIED_AT_END_SUBSTITUTE_INSERT_RULE_ID,
    UK_METADATA_CARRIED_RANGE_INSERT_SUBSTITUTION_RULE_ID,
    UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID,
    UK_SOURCE_PARENT_CARRIED_AFTER_WORD_ORDINAL_INSERT_RULE_ID,
    UK_TARGET_SCOPED_EACH_CHILD_AFTER_WORD_INSERT_RULE_ID,
)
from lawvm.uk_legislation.text_selectors import (
    ExceptChildSelector,
    ExceptSourceSiblingOccurrenceSelector,
    NegativeLeftContextExceptChildrenSelector,
    UKTextRewriteFragment,
    fragment_to_legacy_dict,
)
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _text_content
from lawvm.core.quirks_disposition import QuirksDisposition


_UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID = (
    "uk_effect_word_substitution_escalated_to_structural_replace"
)
_UK_EFFECT_FLAT_TARGET_PARAGRAPH_SUBSTITUTION_RULE_ID = (
    "uk_effect_flat_target_paragraph_substitution_text_payload"
)
_UK_TABLE_COLUMN_PARENT_AT_END_INSERT_BLOCKED_RULE_ID = (
    "uk_effect_table_column_parent_at_end_insert_blocks_generic_text_append"
)
_UK_BARE_AT_END_INSERT_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+\s+)?at\s+the\s+end\s+insert\s*[—-]",
    re.I,
)
_UK_TABLE_COLUMN_PARENT_CONTEXT_RE = re.compile(
    r"\bin\s+(?:the\s+)?"
    r"(?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+column|"
    r"column\s+\d+)\s+of\s+(?:the\s+)?Table\b",
    re.I,
)
_SOURCE_CHILD_WHERE_ORDINAL_INSERT_RE = re.compile(
    rf"\bwhere it (?P<ordinal>{'|'.join(re.escape(key) for key in _ORDINAL_OCCURRENCES)}) "
    r"occurs?,? insert [“\"'‘](?P<inserted>[^”\"'’]{1,1000})[”\"'’]",
    flags=re.I,
)
UK_SOURCE_SIBLING_EXCEPT_OCCURRENCE_SUBSTITUTION_RULE_ID = (
    "uk_effect_source_sibling_except_occurrence_substitution_text_patch"
)
_SOURCE_SIBLING_EXCEPT_AS_MENTIONED_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>[^”\"'’]{1,500})[”\"'’],?\s+"
    r"in\s+each\s+place\s+except\s+as\s+mentioned\s+in\s+"
    r"(?P<source_sibling_kind>sub-?paragraph|paragraph|subsection)\s+"
    r"\((?P<source_sibling_label>[0-9A-Za-z]+)\),?\s+"
    r"substitute\s+[“\"'‘](?P<replacement>[^”\"'’]{1,500})[”\"'’]",
    flags=re.I,
)
_SOURCE_SIBLING_CHILD_TARGET_RE = re.compile(
    r"\bin\s+(?P<child_kind>subsection|paragraph|sub-?paragraph|subparagraph)\s+"
    r"\((?P<child_label>[0-9A-Za-z]+)\)",
    flags=re.I,
)
_NEGATIVE_LEFT_CONTEXT_EXCLUDED_CHILDREN_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>[^”\"'’]{1,500})[”\"'’],?\s+"
    r"in\s+each\s+case\s+where\s+it\s+occurs\s+without\s+"
    r"[“\"'‘](?P<left_context>[^”\"'’]{1,80})[”\"'’]\s+before\s+it,?\s+"
    r"substitute\s+[“\"'‘](?P<replacement>[^”\"'’]{1,500})[”\"'’]\s*"
    r"\(\s*but\s+this\s+does\s+not\s+apply\s+to\s+paragraph\s+"
    r"(?P<first_paragraph>[0-9A-Za-z]+)\s*\(\s*(?P<first_child>[0-9A-Za-z]+)\s*\)\s+"
    r"or\s+(?:paragraph\s+)?"
    r"(?P<second_paragraph>[0-9A-Za-z]+)\s*\(\s*(?P<second_child>[0-9A-Za-z]+)\s*\)\s+"
    r"of\s+Schedule\s+(?P<schedule>[0-9A-Za-z]+)\b[^)]{0,500}\)",
    flags=re.I,
)
_AFTER_ANCHOR_EACH_PLACE_EXCEPT_CHILD_INSERT_RE = re.compile(
    r"\bafter\s+[“\"'‘](?P<anchor>[^”\"'’]{1,500})[”\"'’]\s*,?\s+"
    r"in\s+each\s+place\s+except\s+"
    r"(?P<child_kind>paragraph|sub-?paragraph|subparagraph)\s*"
    r"\(\s*(?P<child_label>[0-9A-Za-z]+)\s*\)"
    r"(?:\s*\(\s*(?P<grandchild_label>[0-9A-Za-z]+)\s*\))?\s+"
    r"to\s+the\s+proviso\s+to\s+subsection\s*"
    r"\(\s*(?P<excluded_subsection>[0-9A-Za-z]+)\s*\)\s+"
    r"insert\s+[“\"'‘](?P<inserted>[^”\"'’]{1,500})[”\"'’]",
    flags=re.I,
)
_SOURCE_SUBSECTIONS_LIST_RE = re.compile(
    r"\bsubsections\s+(?P<body>[^.;]{1,240})",
    flags=re.I,
)
_SOURCE_SUBSECTION_LABEL_RE = re.compile(r"\(\s*([0-9A-Za-z]+)\s*\)")
_AMOUNT_SPECIFIED_SECTION_TARGET_RE = re.compile(
    r"\bamount\s+specified\s+in\s+section\s+"
    r"(?P<section>[0-9]+[A-Za-z]?)"
    r"(?P<suffix>(?:\s*\([0-9A-Za-z]+\)){0,4})",
    flags=re.I,
)
_AMOUNT_SPECIFIED_SUBSECTION_TARGET_RE = re.compile(
    r"\bamount\s+specified\s+in\s+subsection\s+"
    r"(?P<suffix>(?:\s*\([0-9A-Za-z]+\)){1,4})",
    flags=re.I,
)
_AMOUNT_SPECIFIED_SUFFIX_LABEL_RE = re.compile(r"\(([0-9A-Za-z]+)\)")


@lru_cache(maxsize=256)
def _source_subsection_target_re(clean_target: str) -> re.Pattern[str]:
    return re.compile(
        rf"\bsubsection\s*\(\s*{re.escape(clean_target)}\s*\)",
        flags=re.I,
    )


def _amount_specified_source_target_path(text: str) -> tuple[tuple[str, str], ...]:
    section_match = _AMOUNT_SPECIFIED_SECTION_TARGET_RE.search(text)
    if section_match is not None:
        path: list[tuple[str, str]] = [
            ("section", _clean_num(section_match.group("section")))
        ]
        suffix_labels = _AMOUNT_SPECIFIED_SUFFIX_LABEL_RE.findall(
            section_match.group("suffix") or ""
        )
        suffix_kinds = ("subsection", "paragraph", "subparagraph", "item")
        for kind, label in zip(suffix_kinds, suffix_labels, strict=False):
            path.append((kind, _clean_num(label)))
        return tuple(path)
    subsection_match = _AMOUNT_SPECIFIED_SUBSECTION_TARGET_RE.search(text)
    if subsection_match is None:
        return ()
    path = []
    suffix_labels = _AMOUNT_SPECIFIED_SUFFIX_LABEL_RE.findall(
        subsection_match.group("suffix") or ""
    )
    suffix_kinds = ("subsection", "paragraph", "subparagraph", "item")
    for kind, label in zip(suffix_kinds, suffix_labels, strict=False):
        path.append((kind, _clean_num(label)))
    return tuple(path)


def _amount_specified_source_target_matches(
    *,
    fragment: dict[str, Any],
    extracted_text: str,
    target: LegalAddress,
) -> bool:
    if str(fragment.get("rule_id") or "") != UK_AMOUNT_SPECIFIED_SUBSTITUTION_RULE_ID:
        return True
    source_path = _amount_specified_source_target_path(extracted_text)
    if not source_path:
        return True
    if source_path[0][0] == "subsection":
        target_suffix = tuple(part for part in target.path if part[0] != "section")
        return target_suffix == source_path
    return tuple(target.path) == source_path


def _normalized_source_child_kind(kind: str) -> str:
    normalized = kind.strip().lower().replace("-", "")
    if normalized == "subparagraph":
        return "subparagraph"
    return normalized


def _fragment_substitution_source_sibling_except_occurrence(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    text = " ".join((extracted_text or "").split())
    match = _SOURCE_SIBLING_EXCEPT_AS_MENTIONED_SUBSTITUTION_RE.search(text)
    if match is None or extracted_el is None:
        return None
    source_sibling_kind = _normalized_source_child_kind(match.group("source_sibling_kind"))
    source_sibling_label = _clean_num(match.group("source_sibling_label"))
    if source_sibling_kind != "subparagraph" or not source_sibling_label:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return None
    parent = ancestors[0]
    for child in parent:
        if child is extracted_el or child.get("id") == extracted_el.get("id"):
            continue
        if _clean_num(_direct_structural_num(child)) != source_sibling_label:
            continue
        sibling_text = " ".join(_text_content(child).split())
        target_match = _SOURCE_SIBLING_CHILD_TARGET_RE.search(sibling_text)
        if target_match is None:
            return None
        sibling_fragments = parse_fragment_substitution(sibling_text)
        if len(sibling_fragments) != 1:
            return None
        sibling_fragment = sibling_fragments[0]
        excluded_original = " ".join(
            str(sibling_fragment.get("original") or "").split()
        ).strip()
        excluded_occurrence = str(sibling_fragment.get("occurrence") or "").strip()
        if not excluded_original or not excluded_occurrence.isdigit():
            return None
        child_kind = _normalized_source_child_kind(target_match.group("child_kind"))
        child_label = _clean_num(target_match.group("child_label"))
        if not child_kind or not child_label:
            return None
        return fragment_to_legacy_dict(
            UKTextRewriteFragment(
                selector=ExceptSourceSiblingOccurrenceSelector(
                    original=match.group("original").strip(),
                    child_kind=child_kind,
                    child_label=child_label,
                    excluded_original=excluded_original,
                    excluded_occurrence=excluded_occurrence,
                    source_sibling_kind=source_sibling_kind,
                    source_sibling_label=source_sibling_label,
                ),
                replacement=match.group("replacement").strip(),
                rule_id=UK_SOURCE_SIBLING_EXCEPT_OCCURRENCE_SUBSTITUTION_RULE_ID,
                occurrence="0",
            )
        )
    return None


def _effect_after_anchor_except_child_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    match = _AFTER_ANCHOR_EACH_PLACE_EXCEPT_CHILD_INSERT_RE.search(text)
    if match is None:
        return None
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if target_kind != "subsection" or not target_label:
        return None
    if not _source_mentions_subsection_target(text, target_label):
        return None
    anchor = " ".join(match.group("anchor").split()).strip()
    inserted = " ".join(match.group("inserted").split()).strip()
    if not anchor or not inserted:
        return None
    joiner = (
        ""
        if anchor.endswith((" ", "\t", "\n", "\r"))
        or inserted.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    replacement = f"{anchor}{joiner}{inserted}"
    excluded_subsection = _clean_num(match.group("excluded_subsection"))
    if target_label != excluded_subsection:
        return {
            "original": anchor,
            "replacement": replacement,
            "rule_id": UK_AFTER_QUOTED_ANCHOR_EXCEPT_CHILD_INSERT_RULE_ID,
        }
    child_kind = match.group("child_kind").replace("-", "").lower()
    child_label = _clean_num(match.group("child_label"))
    grandchild_label = _clean_num(match.group("grandchild_label") or "")
    if grandchild_label:
        child_kind = "subparagraph"
        child_label = grandchild_label
    if not child_label:
        return None
    return fragment_to_legacy_dict(
        UKTextRewriteFragment(
            selector=ExceptChildSelector(anchor, child_kind, child_label),
            replacement=replacement,
            rule_id=UK_AFTER_QUOTED_ANCHOR_EXCEPT_CHILD_INSERT_RULE_ID,
            occurrence="0",
        )
    )


def _effect_negative_left_context_excluded_children_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words substituted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    match = _NEGATIVE_LEFT_CONTEXT_EXCLUDED_CHILDREN_SUBSTITUTION_RE.search(text)
    if match is None:
        return None
    if _addr_leaf_kind(target) != "schedule":
        return None
    source_schedule = _clean_num(match.group("schedule"))
    target_schedule = _clean_num(_addr_leaf_label(target) or "")
    if not source_schedule or source_schedule != target_schedule:
        return None
    original = " ".join(match.group("original").split()).strip()
    left_context = " ".join(match.group("left_context").split()).strip()
    replacement = " ".join(match.group("replacement").split()).strip()
    if not original or not left_context or not replacement:
        return None
    excluded_paths = (
        (
            ("paragraph", _clean_num(match.group("first_paragraph"))),
            ("subparagraph", _clean_num(match.group("first_child"))),
        ),
        (
            ("paragraph", _clean_num(match.group("second_paragraph"))),
            ("subparagraph", _clean_num(match.group("second_child"))),
        ),
    )
    if any(not label for path in excluded_paths for _kind, label in path):
        return None
    return fragment_to_legacy_dict(
        UKTextRewriteFragment(
            selector=NegativeLeftContextExceptChildrenSelector(
                original=original,
                negative_left_context=left_context,
                excluded_child_paths=excluded_paths,
            ),
            replacement=replacement,
            rule_id=UK_NEGATIVE_LEFT_CONTEXT_EXCLUDED_CHILDREN_SUBSTITUTION_RULE_ID,
            occurrence="0",
        )
    )


def _source_mentions_subsection_target(text: str, target_label: str) -> bool:
    clean_target = _clean_num(target_label)
    if not clean_target:
        return False
    if _source_subsection_target_re(clean_target).search(text):
        return True
    for match in _SOURCE_SUBSECTIONS_LIST_RE.finditer(text):
        labels = {
            _clean_num(label)
            for label in _SOURCE_SUBSECTION_LABEL_RE.findall(match.group("body"))
        }
        if clean_target in labels:
            return True
    return False


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
    table_cell_selector: Optional[dict[str, Any]],
    selector_rule_id: str,
    structural_sibling_insert_detail: Optional[dict[str, Any]],
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKTextFragmentLowering:
    """Lower source-carried word fragments into typed text patch fields."""
    fragment_parse_text = lowering_extracted_text or extracted_text
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

    if is_word_level and curr_action is not None:
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
        lowering_extracted_text=fragment_parse_text,
        lowering_rejections_out=lowering_rejections_out,
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
        and curr_action == "replace"
        and content_ir is not None
        and dict(content_ir.get("attrs") or {}).get("source_rule_id")
        in {
            UK_EFFECT_WORD_SUBSTITUTION_PARENT_CHILD_REPLACEMENT_RULE_ID,
            UK_EFFECT_WORD_SUBSTITUTION_STRUCTURAL_CHILD_REPLACEMENT_RULE_ID,
        }
        and content_ir.get("kind") == _addr_leaf_kind(target)
        and _clean_num(str(content_ir.get("label") or "")) == _clean_num(_addr_leaf_label(target) or "")
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

    if (
        is_word_level
        and effect.effect_type == "substituted for words"
        and content_ir is not None
        and content_ir.get("kind") == _addr_leaf_kind(target)
        and _clean_num(str(content_ir.get("label") or "")) == _clean_num(_addr_leaf_label(target) or "")
    ):
        # Some archive-backed UK effects are labeled as word-level substitutions even
        # though the source carries the fully substituted structural node.
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID,
            family="action_family_recovery",
            reason_code="word_level_effect_escalated_to_structural_replace",
            reason=(
                "UK effect feed row is labeled as a word-level substitution but the "
                "source carries the fully substituted structural node matching the "
                "target leaf kind and label; lowering escalates to a structural replace."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "source_payload_kind": str(content_ir.get("kind") or ""),
                "source_payload_label": str(content_ir.get("label") or ""),
                "target_leaf_kind": str(_addr_leaf_kind(target) or ""),
                "target_leaf_label": str(_addr_leaf_label(target) or ""),
                "strict_disposition": "block",
                "quirks_disposition": QuirksDisposition.APPLY,
            },
        )
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

    if (
        is_word_level
        and curr_action == "insert"
        and content_ir is not None
        and dict(content_ir.get("attrs") or {}).get("source_rule_id")
        == UK_AMENDMENT_PROGRAM_INSERTED_ANCHOR_STRUCTURAL_INSERT_RULE_ID
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

    if is_word_level and curr_action is not None:
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
        flat_target_paragraph_substitution = _flat_target_paragraph_substitution_payload(
            effect=effect,
            target=target,
            target_ref=target_ref,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
        if flat_target_paragraph_substitution is not None:
            return UKTextFragmentLowering(
                target=target,
                curr_action="replace",
                content_ir=flat_target_paragraph_substitution,
                fragment_subs=fragment_subs,
                op_text_match=op_text_match,
                op_text_replacement=op_text_replacement,
                op_text_occurrence=op_text_occurrence,
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


def _direct_parent_text_before_child(parent_el: ET._Element, child_el: ET._Element) -> str:
    parts: list[str] = []
    for child in parent_el:
        if child is child_el:
            break
        if child.tag.rsplit("}", 1)[-1] == "Text":
            parts.append(" ".join(" ".join(str(part) for part in child.itertext()).split()).strip())
    return " ".join(part for part in parts if part).strip()


def _table_column_parent_blocks_generic_at_end_insert(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET._Element],
    extracted_text: str,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if extracted_el is None or "table" not in str(target_ref or "").lower():
        return False
    text = " ".join(str(extracted_text or "").split()).strip()
    if _UK_BARE_AT_END_INSERT_RE.search(text) is None:
        return False
    child_el = extracted_el
    parent_el = extracted_el.getparent()
    for _ in range(4):
        if parent_el is None:
            return False
        parent_instruction = _direct_parent_text_before_child(parent_el, child_el)
        if _UK_TABLE_COLUMN_PARENT_CONTEXT_RE.search(parent_instruction) is not None:
            _append_uk_effect_lowering_rejection(
                lowering_rejections_out,
                rule_id=_UK_TABLE_COLUMN_PARENT_AT_END_INSERT_BLOCKED_RULE_ID,
                family="table_surface_boundary",
                reason_code="table_column_parent_requires_table_surface_claim",
                reason=(
                    "UK source row says `at the end insert` under a parent "
                    "instruction that scopes the amendment to a table column. "
                    "Generic target text append is blocked until a table "
                    "surface claim proves the exact row, column, and payload "
                    "boundary."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "source_parent_instruction": parent_instruction[:500],
                    "strict_disposition": "block",
                    "quirks_disposition": QuirksDisposition.BLOCK,
                    "required_proofs": (
                        "table_surface_identity",
                        "row_or_column_boundary",
                        "payload_boundary_identity",
                    ),
                },
            )
            return True
        child_el = parent_el
        parent_el = parent_el.getparent()
    return False


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
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    extracted_text: str,
    lowering_extracted_text: Optional[str] = None,
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
    allow_heading_source_parent_full_replacement: bool = True,
    allow_source_parent_at_end_text_insert: bool = False,
    allow_source_parent_word_range_substitution: bool = False,
) -> list[dict[str, Any]]:
    fragment_parse_text = lowering_extracted_text or extracted_text
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
    table_column_parent_blocks_generic_at_end_insert = (
        _table_column_parent_blocks_generic_at_end_insert(
            effect=effect,
            target=target,
            target_ref=target_ref,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            lowering_rejections_out=lowering_rejections_out,
        )
    )
    source_sibling_except_occurrence = (
        _fragment_substitution_source_sibling_except_occurrence(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
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
        else [source_sibling_except_occurrence]
        if source_sibling_except_occurrence is not None
        else []
        if table_column_parent_blocks_generic_at_end_insert
        else parse_fragment_substitution(extracted_text)
    )
    target_scoped_except_child_insert = _effect_after_anchor_except_child_insert_fragment(
        effect=effect,
        target=target,
        extracted_text=extracted_text,
    )
    if target_scoped_except_child_insert is not None:
        subs = [target_scoped_except_child_insert]
    target_scoped_negative_context_exclusions = (
        _effect_negative_left_context_excluded_children_fragment(
            effect=effect,
            target=target,
            extracted_text=extracted_text,
        )
    )
    if target_scoped_negative_context_exclusions is not None:
        subs = [target_scoped_negative_context_exclusions]
    mixed_structural_text_rewrite_text_half = (
        _effect_mixed_structural_text_rewrite_text_half_repeal_fragment(
            effect_type=effect.effect_type,
            extracted_text=extracted_text,
            target=target,
        )
    )
    if mixed_structural_text_rewrite_text_half is not None:
        subs = [mixed_structural_text_rewrite_text_half]
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
        and subs is not None
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
        metadata_carried_omitting_words_repeals = (
            _effect_metadata_carried_omitting_words_repeal_fragments(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
                target=target,
            )
        )
        if metadata_carried_omitting_words_repeals:
            subs = list(metadata_carried_omitting_words_repeals)
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
        metadata_carried_after_substitute_insert = (
            _effect_metadata_carried_after_substitute_insert_fragment(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_after_substitute_insert is not None:
            subs = [metadata_carried_after_substitute_insert]
    if not subs:
        metadata_carried_substituting_words = (
            _effect_metadata_carried_substituting_words_fragment(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
                target=target,
            )
        )
        if metadata_carried_substituting_words is not None:
            subs = [metadata_carried_substituting_words]
    if not subs:
        after_anchor_substitute_tail_substitution = (
            _effect_after_anchor_substitute_tail_substitution_fragment(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
            )
        )
        if after_anchor_substitute_tail_substitution is not None:
            subs = [after_anchor_substitute_tail_substitution]
    if not subs:
        metadata_carried_at_end_substitute_insert = (
            _effect_metadata_carried_at_end_substitute_insert_fragment(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_at_end_substitute_insert is not None:
            subs = [metadata_carried_at_end_substitute_insert]
    if not subs:
        metadata_carried_definition_at_end_add_insert = (
            _effect_metadata_carried_definition_at_end_add_insert_fragment(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_definition_at_end_add_insert is not None:
            subs = [metadata_carried_definition_at_end_add_insert]
    if not subs:
        metadata_carried_at_end_add_insert = _effect_metadata_carried_at_end_add_insert_fragment(
            effect=effect,
            target=target,
            extracted_text=extracted_text,
        )
        if metadata_carried_at_end_add_insert is not None:
            subs = [metadata_carried_at_end_add_insert]
    if not subs:
        metadata_carried_at_end_insert_quoted = (
            _effect_metadata_carried_at_end_insert_quoted_fragment(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_at_end_insert_quoted is not None:
            subs = [metadata_carried_at_end_insert_quoted]
    if not subs:
        metadata_carried_range_insert_substitution = (
            _effect_metadata_carried_range_insert_substitution_fragment(
                effect_type=effect.effect_type,
                extracted_text=extracted_text,
            )
        )
        if metadata_carried_range_insert_substitution is not None:
            subs = [metadata_carried_range_insert_substitution]
    if not subs:
        target_scoped_each_child_after_word_insert = (
            _effect_target_scoped_each_child_after_word_insert_fragment(
                effect=effect,
                target=target,
                extracted_text=extracted_text,
            )
        )
        if target_scoped_each_child_after_word_insert is not None:
            subs = [target_scoped_each_child_after_word_insert]
    if not subs:
        if extracted_el is not None:
            source_parent_carried_after_word_ordinal_insert = (
                _effect_source_parent_carried_after_word_ordinal_insert_fragment(
                    effect=effect,
                    target=target,
                    extracted_el=extracted_el,
                    extracted_text=extracted_text,
                )
            )
            if source_parent_carried_after_word_ordinal_insert is not None:
                subs = [source_parent_carried_after_word_ordinal_insert]
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
        definition_anchor_tail = _effect_definition_anchor_tail_fragment(
            effect=effect,
            target=target,
            extracted_text=extracted_text,
        )
        if definition_anchor_tail is not None:
            subs = [definition_anchor_tail]
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
    if not subs:
        source_parent_after_anchor_to_end_substitution = (
            _fragment_substitution_source_parent_after_anchor_to_end_substitution(
                extracted_el=extracted_el,
                source_root=source_root,
                extracted_text=extracted_text,
                target=target,
            )
        )
        if source_parent_after_anchor_to_end_substitution is not None:
            subs = [source_parent_after_anchor_to_end_substitution]
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
                extracted_el=extracted_el,
                source_root=source_root,
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
        source_carried_between_paragraphs_substitution = (
            _fragment_substitution_source_carried_between_paragraphs_substitution(
                extracted_text=extracted_text,
                target=target,
                extracted_el=extracted_el,
                source_root=source_root,
            )
        )
        if source_carried_between_paragraphs_substitution is not None:
            subs = [source_carried_between_paragraphs_substitution]
    if not subs:
        source_carried_multi_subunit_substitution = (
            _fragment_substitution_source_carried_multi_subunit_substitution(
                extracted_text=extracted_text,
                target=target,
            )
        )
        if source_carried_multi_subunit_substitution is not None:
            subs = [source_carried_multi_subunit_substitution]
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
    if not subs and fragment_parse_text != extracted_text:
        subs = parse_fragment_substitution(fragment_parse_text)
        if subs and lowering_rejections_out is not None:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id="uk_effect_source_payload_instruction_context_augmented",
                family="source_extraction_context",
                reason_code="payload_fragment_augmented_with_parent_instruction",
                reason=(
                    "UK extracted source was a bare payload fragment with no amendment "
                    "verb; lowering prepended the parent amendment-container instruction "
                    "so the source text could be parsed into a typed text patch."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "augmented_text_preview": fragment_parse_text[:300],
                },
            )
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


def _flat_target_paragraph_substitution_payload(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_flat_target_paragraph_substitution(text)
    if parsed is None:
        return None
    source_label, payload_label, replacement = parsed
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if _addr_leaf_kind(target) != "paragraph" or not target_label:
        return None
    if source_label != target_label or payload_label != target_label:
        return None
    if not replacement:
        return None
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_EFFECT_FLAT_TARGET_PARAGRAPH_SUBSTITUTION_RULE_ID,
        family="action_family_recovery",
        reason_code="word_level_feed_flat_paragraph_substitution_source",
        reason=(
            "UK effect feed marks a word-level substitution, but the source "
            "instruction explicitly substitutes the same affected paragraph and "
            "carries the replacement paragraph text in a flat source row; "
            "lowering emits a structural replace scoped to that exact paragraph."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "source_paragraph_label": source_label,
            "payload_paragraph_label": payload_label,
            "replacement_preview": replacement[:500],
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.APPLY,
        },
    )
    return {
        "kind": "paragraph",
        "label": target_label,
        "text": replacement,
        "attrs": {
            "source_rule_id": _UK_EFFECT_FLAT_TARGET_PARAGRAPH_SUBSTITUTION_RULE_ID,
        },
        "children": [],
    }


def _parse_flat_target_paragraph_substitution(text: str) -> Optional[tuple[str, str, str]]:
    lower = text.lower()
    marker = "for paragraph ("
    marker_index = lower.find(marker)
    if marker_index < 0:
        return None
    label_start = marker_index + len(marker)
    label_end = text.find(")", label_start)
    if label_end < 0:
        return None
    source_label = _clean_num(text[label_start:label_end])
    if not source_label:
        return None
    substitute_phrase = "substitute the following paragraph"
    substitute_index = lower.find(substitute_phrase, label_end + 1)
    if substitute_index < 0:
        return None
    connector_context = lower[label_end + 1 : substitute_index]
    if "and the following" not in connector_context:
        return None
    dash_index_candidates = [
        index
        for index in (
            text.find("—", substitute_index + len(substitute_phrase)),
            text.find("-", substitute_index + len(substitute_phrase)),
        )
        if index >= 0
    ]
    if not dash_index_candidates:
        return None
    dash_index = min(dash_index_candidates)
    payload = text[dash_index + 1 :].strip()
    payload = payload.rstrip(";").strip()
    payload = re.sub(r"\s+\.$", "", payload).strip()
    payload_parts = payload.split(maxsplit=1)
    if len(payload_parts) != 2:
        return None
    payload_label = _clean_num(payload_parts[0])
    replacement = payload_parts[1].strip()
    if not payload_label or not replacement:
        return None
    return source_label, payload_label, replacement


def _promote_text_fragment_substitutions(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    subs: list[dict[str, Any]],
    is_word_level: bool,
    target: LegalAddress,
    target_ref: str,
    table_cell_selector: Optional[dict[str, Any]],
    selector_rule_id: str,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
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
    if not _amount_specified_source_target_matches(
        fragment=primary,
        extracted_text=extracted_text,
        target=target,
    ):
        return UKTextFragmentLowering(
            target=target,
            curr_action=None,
            content_ir=None,
            fragment_subs=subs,
            op_text_match=None,
            op_text_replacement=None,
            op_text_occurrence=0,
            op_text_end_occurrence=0,
            unlowered_overlap_reason="amount_specified_source_target_mismatch",
        )
    target = _refine_source_carried_child_text_target(
        effect=effect,
        target=target,
        fragment=primary,
        target_ref=target_ref,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )
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

    if table_cell_selector is not None:
        selector_match = " ".join(
            str(table_cell_selector.get("match_text") or "").split()
        ).strip()
        if selector_match:
            primary = {
                **primary,
                "original": selector_match,
                "rule_id": str(table_cell_selector.get("rule_id") or selector_rule_id),
            }
            subs = [primary]

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
    if (
        primary.get("rule_id")
        == UK_SOURCE_SIBLING_EXCEPT_OCCURRENCE_SUBSTITUTION_RULE_ID
    ):
        selector_parts = str(op_text_match or "").split(US, 7)
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_SIBLING_EXCEPT_OCCURRENCE_SUBSTITUTION_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_sibling_excluded_occurrence_text_patch",
            reason=(
                "UK source substitutes a quoted expression in each place except "
                "an occurrence owned by a source sibling; lowering preserves the "
                "source-sibling exclusion, target child, excluded preimage, and "
                "excluded occurrence in the text selector."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match or "",
                "replacement": op_text_replacement or "",
                "excluded_child_kind": selector_parts[2] if len(selector_parts) == 8 else "",
                "excluded_child_label": selector_parts[3] if len(selector_parts) == 8 else "",
                "excluded_original": selector_parts[4] if len(selector_parts) == 8 else "",
                "excluded_occurrence": selector_parts[5] if len(selector_parts) == 8 else "",
                "source_sibling_kind": selector_parts[6] if len(selector_parts) == 8 else "",
                "source_sibling_label": selector_parts[7] if len(selector_parts) == 8 else "",
            },
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


def _refine_source_carried_child_text_target(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    fragment: dict[str, Any],
    target_ref: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> LegalAddress:
    if str(fragment.get("target_refinement") or "") != "source_carried_child_text":
        return target
    if target.special is not None:
        return target
    child_kind = str(fragment.get("target_refinement_kind") or "").strip().lower().replace("-", "")
    child_label = _clean_num(str(fragment.get("target_refinement_label") or ""))
    if not child_kind or not child_label:
        return target
    leaf_kind = target.leaf_kind().strip().lower().replace("-", "")
    if leaf_kind == child_kind and target.leaf_label().strip().lower() == child_label:
        return target
    if (leaf_kind, child_kind) not in {
        ("subsection", "paragraph"),
        ("paragraph", "subparagraph"),
    }:
        return target
    refined = LegalAddress(path=target.path + ((child_kind, child_label),), special=None)
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_source_carried_child_text_target_refined",
        family="target_resolution_recovery",
        reason_code="source_carried_child_text_target_refined",
        reason=(
            "UK source text identifies a child-local text rewrite inside the "
            "effect-feed parent target; lowering refines to the source-named "
            "child instead of applying a broad parent text patch."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "original_target": str(target),
            "refined_target": str(refined),
            "source_child_kind": child_kind,
            "source_child_label": child_label,
            "source_rule_id": str(fragment.get("rule_id") or ""),
            "target_resolution": TargetResolutionCoverage(
                rule_id="uk_effect_source_carried_child_text_target_refined",
                phase="lowering",
                reason=(
                    "UK source text identifies a child-local text rewrite inside "
                    "the effect-feed parent target."
                ),
                resolution_status=TARGET_RECOVERED,
                source_target=str(target),
                selected_target=str(refined),
                candidate_count=1,
                candidates=(
                    TargetResolutionCandidate(
                        target=str(refined),
                        reason="source_carried_child_text_refinement",
                        detail={
                            "target_ref": target_ref,
                            "source_child_kind": child_kind,
                            "source_child_label": child_label,
                        },
                    ),
                ),
                scope_confidence=SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD,
                detail={
                    "source_rule_id": str(fragment.get("rule_id") or ""),
                    "original_leaf_kind": leaf_kind,
                    "refined_child_kind": child_kind,
                },
            ).to_diagnostic_detail(),
        },
    )
    return refined


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


def _effect_metadata_carried_omitting_words_repeal_fragments(
    *,
    effect_type: str,
    extracted_text: str,
    target: LegalAddress,
) -> tuple[dict[str, str], ...]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return ()
    text = " ".join(str(extracted_text or "").split()).strip()
    lowered = text.lower()
    if not text or not re.search(r"\bomitt?ing\b|\bomit\b|\bomitted\b", lowered):
        return ()
    if re.search(r"\bwhere\s+they\s+occur\b|\bsubject\s+to\b|\bexcept\b", lowered):
        return ()
    if not _metadata_carried_quote_scope_matches_target(text, target):
        return ()
    quote_matches = tuple(re.finditer(r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\")", text))
    if not quote_matches:
        return ()
    fragments = tuple(
        " ".join((match.group("curly") or match.group("double") or "").split()).strip()
        for match in quote_matches
    )
    fragments = tuple(fragment for fragment in fragments if fragment)
    if not fragments:
        return ()
    return tuple(
        {
            "original": fragment,
            "replacement": "",
            "rule_id": UK_METADATA_CARRIED_OMITTING_WORDS_REPEAL_RULE_ID,
        }
        for fragment in fragments
    )


def _effect_mixed_structural_text_rewrite_text_half_repeal_fragment(
    *,
    effect_type: str,
    extracted_text: str,
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word repealed", "words repealed", "word omitted", "words omitted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    lowered = text.lower()
    if (
        not text
        or "omit subsection" not in lowered
        or " words from " not in lowered
        or " to the end" not in lowered
    ):
        return None
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if target_kind != "subsection" or not target_label:
        return None
    match = re.search(
        rf"\bin\s+subsection\s*\(\s*{re.escape(target_label)}\s*\)"
        r".{0,160}?\bwords\s+from\s+[“\"](?P<anchor>[^”\"]{1,500})[”\"]"
        r".{0,80}?\bto\s+the\s+end\b",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    anchor = " ".join(match.group("anchor").split()).strip()
    if not anchor:
        return None
    return {
        "original": f"TEXT_FROM_{anchor}_TO_END",
        "replacement": "",
        "rule_id": UK_MIXED_STRUCTURAL_TEXT_REWRITE_TEXT_HALF_REPEAL_RULE_ID,
    }


def _target_section_ref_pattern(target: LegalAddress) -> Optional[re.Pattern[str]]:
    if len(target.path) < 2 or target.path[0][0] != "section":
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


def _effect_metadata_carried_substituting_words_fragment(
    *,
    effect_type: str,
    extracted_text: str,
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words substituted", "substituted for words"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    lowered = text.lower()
    if not text or "substitut" not in lowered:
        return None
    if re.search(r"\bwhere\s+they?\s+refer\b|\bwhere\s+it\s+refers\b|\bexcept\b", lowered):
        return None
    if not _metadata_carried_quote_scope_matches_target(text, target):
        return None
    substituting_idx = lowered.find("substituting ")
    if substituting_idx < 0:
        return None
    first_quote_idx = min(
        (idx for idx in (text.find(q, substituting_idx) for q in _QUOTE_PAIRS) if idx >= 0),
        default=-1,
    )
    if first_quote_idx < 0:
        return None
    replacement = _read_quoted(text, first_quote_idx)
    if replacement is None:
        return None
    for_idx = lowered.find(" for ", replacement[1])
    if for_idx < 0:
        return None
    original_quote_idx = min(
        (idx for idx in (text.find(q, for_idx) for q in _QUOTE_PAIRS) if idx >= 0),
        default=-1,
    )
    if original_quote_idx < 0:
        return None
    original = _read_quoted(text, original_quote_idx)
    if original is None:
        return None
    original_text = " ".join(original[0].split()).strip()
    replacement_text = " ".join(replacement[0].split()).strip()
    if not original_text or not replacement_text:
        return None
    return {
        "original": original_text,
        "replacement": replacement_text,
        "rule_id": UK_METADATA_CARRIED_SUBSTITUTING_WORDS_RULE_ID,
    }


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


def _effect_metadata_carried_after_substitute_insert_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_after_anchor_substitute_insert(text)
    if parsed is None:
        return None
    anchor, scope, inserted = parsed
    if not anchor or not inserted:
        return None
    joiner = (
        ""
        if anchor.endswith((" ", "\t", "\n", "\r"))
        or inserted.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    fragment = {
        "original": anchor,
        "replacement": f"{anchor}{joiner}{inserted}".strip(),
        "rule_id": UK_METADATA_CARRIED_AFTER_SUBSTITUTE_INSERT_RULE_ID,
    }
    occurrence = _occurrence_from_after_substitute_scope(scope)
    if occurrence:
        fragment["occurrence"] = occurrence
    return fragment


def _effect_after_anchor_substitute_tail_substitution_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words substituted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_after_anchor_substitute_insert(text)
    if parsed is None:
        return None
    anchor, scope, replacement = parsed
    if not anchor or not replacement:
        return None
    fragment = {
        "original": f"TEXT_AFTER_{anchor}_TO_END",
        "replacement": replacement,
        "rule_id": UK_AFTER_ANCHOR_SUBSTITUTE_TAIL_SUBSTITUTION_RULE_ID,
    }
    occurrence = _occurrence_from_after_substitute_scope(scope)
    if occurrence:
        fragment["occurrence"] = occurrence
    return fragment


def _effect_metadata_carried_range_insert_substitution_fragment(
    *,
    effect_type: str,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words substituted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    range_insert = _parse_metadata_carried_range_insert_substitution(text)
    if range_insert is None:
        return None
    start, end, replacement = range_insert
    if not start or not end or not replacement:
        return None
    return {
        "original": f"TEXT_FROM_{start}_TO_{end}",
        "replacement": replacement,
        "rule_id": UK_METADATA_CARRIED_RANGE_INSERT_SUBSTITUTION_RULE_ID,
    }


def _parse_metadata_carried_range_insert_substitution(text: str) -> Optional[tuple[str, str, str]]:
    lower = text.lower()
    search_at = 0
    while search_at < len(text):
        for_pos = lower.find("for ", search_at)
        if for_pos < 0:
            return None
        pos = _consume_metadata_carried_range_insert_prefix(lower, for_pos + len("for "))
        if pos is None:
            search_at = for_pos + len("for ")
            continue
        start, pos = _read_simple_quoted_segment(text, pos)
        if not start:
            search_at = for_pos + len("for ")
            continue
        pos = _skip_spaces(text, pos)
        if not lower.startswith("to ", pos):
            search_at = for_pos + len("for ")
            continue
        pos = _skip_spaces(text, pos + len("to "))
        end, pos = _read_simple_quoted_segment(text, pos)
        if not end:
            search_at = for_pos + len("for ")
            continue
        pos = _skip_spaces(text, pos)
        if not lower.startswith("insert ", pos):
            search_at = for_pos + len("for ")
            continue
        pos = _skip_spaces(text, pos + len("insert "))
        if pos >= len(text) or text[pos] not in "“\"'‘":
            search_at = for_pos + len("for ")
            continue
        replacement_surface = text[pos + 1 :].strip()
        if replacement_surface.endswith((".", ";")):
            replacement_surface = replacement_surface[:-1].strip()
        if not replacement_surface.endswith(("”", '"', "'", "’")):
            return None
        replacement = " ".join(replacement_surface[:-1].split()).strip()
        return (start, end, replacement)
    return None


def _consume_metadata_carried_range_insert_prefix(lower: str, pos: int) -> Optional[int]:
    if lower.startswith("the ", pos):
        pos += len("the ")
    if lower.startswith("word ", pos):
        pos += len("word ")
    elif lower.startswith("words ", pos):
        pos += len("words ")
    else:
        return None
    if not lower.startswith("from ", pos):
        return None
    return pos + len("from ")


def _read_simple_quoted_segment(text: str, pos: int) -> tuple[str, int]:
    if pos >= len(text):
        return ("", pos)
    close_quote_by_open = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
    }
    close_quote = close_quote_by_open.get(text[pos])
    if close_quote is None:
        return ("", pos)
    end = text.find(close_quote, pos + 1)
    if end < 0:
        return ("", pos)
    return (" ".join(text[pos + 1 : end].split()).strip(), end + 1)


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _occurrence_from_after_substitute_scope(scope: str) -> str:
    normalized = " ".join(str(scope or "").lower().split())
    if not normalized:
        return ""
    for ordinal, occurrence in sorted(
        _ORDINAL_OCCURRENCES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if re.search(rf"\b{re.escape(ordinal)}\b", normalized):
            return occurrence
    if re.search(r"\blast(?:ly)?\s+(?:occurring|occurs?|appears?)\b", normalized):
        return "-1"
    return ""


_QUOTE_PAIRS = {
    "“": "”",
    '"': '"',
    "'": "'",
    "‘": "’",
}


def _read_quoted(text: str, start: int) -> Optional[tuple[str, int]]:
    if start < 0 or start >= len(text):
        return None
    close_quote = _QUOTE_PAIRS.get(text[start])
    if close_quote is None:
        return None
    end = text.find(close_quote, start + 1)
    if end < 0:
        return None
    return text[start + 1 : end], end + 1


def _skip_any_prefix(text: str, start: int, prefixes: tuple[str, ...]) -> int:
    lower = text.lower()
    for prefix in prefixes:
        if lower.startswith(prefix, start):
            return start + len(prefix)
    return start


def _parse_after_anchor_substitute_insert(text: str) -> Optional[tuple[str, str, str]]:
    lower = text.lower()
    after_idx = lower.find("after ")
    if after_idx < 0:
        return None
    anchor_start = _skip_any_prefix(
        text,
        after_idx + len("after "),
        ("the words ", "the word ", "words ", "word "),
    )
    anchor = _read_quoted(text, anchor_start)
    if anchor is None:
        return None
    substitute_idx = lower.find("substitute ", anchor[1])
    if substitute_idx < 0:
        return None
    inserted_start = substitute_idx + len("substitute ")
    inserted = _read_quoted(text, inserted_start)
    if inserted is None:
        return None
    return anchor[0].strip(), text[anchor[1] : substitute_idx], inserted[0].strip()


def _effect_metadata_carried_at_end_substitute_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_at_end_substitute_insert(text)
    if parsed is None:
        return None
    parent_label, source_kind, source_label, inserted = parsed
    source_kind = source_kind.replace("-", "").lower()
    source_kind = "subparagraph" if source_kind == "subparagraph" else source_kind
    source_label = _clean_num(source_label)
    if _addr_leaf_kind(target) != source_kind or _clean_num(_addr_leaf_label(target) or "") != source_label:
        return None
    parent_label = _clean_num(parent_label)
    if parent_label:
        target_parent_labels = {
            _clean_num(label)
            for kind, label in target.path
            if kind in {"paragraph", "subparagraph", "subsection"}
        }
        if parent_label not in target_parent_labels:
            return None
    if not inserted:
        return None
    return {
        "original": "TEXT_END",
        "replacement": inserted,
        "rule_id": UK_METADATA_CARRIED_AT_END_SUBSTITUTE_INSERT_RULE_ID,
    }


def _parse_at_end_substitute_insert(text: str) -> Optional[tuple[str, str, str, str]]:
    lower = text.lower()
    at_end_idx = lower.find("at the end of ")
    if at_end_idx < 0:
        return None
    parent_label = ""
    prefix = lower[:at_end_idx].strip(" ,")
    if prefix.startswith("in paragraph (") and prefix.endswith(")"):
        parent_label = prefix.removeprefix("in paragraph (").removesuffix(")")
    cursor = at_end_idx + len("at the end of ")
    kind = ""
    for candidate in ("sub-paragraph", "paragraph", "subsection"):
        label_prefix = f"{candidate} ("
        if lower.startswith(label_prefix, cursor):
            kind = candidate
            cursor += len(label_prefix)
            break
    if not kind:
        return None
    label_end = text.find(")", cursor)
    if label_end < 0:
        return None
    label = text[cursor:label_end]
    after_label = label_end + 1
    if not lower.startswith(" substitute ", after_label):
        return None
    inserted = _read_quoted(text, after_label + len(" substitute "))
    if inserted is None:
        return None
    return parent_label, kind, label, inserted[0].strip()


def _effect_metadata_carried_definition_at_end_add_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_definition_at_end_add_insert(text)
    if parsed is None:
        return None
    target_context_label, term, inserted = parsed
    if target_context_label:
        target_labels = {_clean_num(label) for _, label in target.path}
        if _clean_num(target_context_label) not in target_labels:
            return None
    if not term or not inserted:
        return None
    return {
        "original": f"TEXT_IN_DEFINITION_{term}{US}AT_END",
        "replacement": inserted,
        "rule_id": UK_IN_DEFINITION_AT_END_TARGET_CONTEXT_INSERT_RULE_ID,
    }


def _parse_definition_at_end_add_insert(text: str) -> Optional[tuple[str, str, str]]:
    lower = text.lower()
    definition_phrase = "in the definition of "
    definition_idx = lower.find(definition_phrase)
    if definition_idx < 0:
        return None
    target_context_label = _source_in_unit_label_before(lower[:definition_idx])
    term_start = definition_idx + len(definition_phrase)
    term = _read_quoted(text, term_start)
    if term is None:
        return None
    cursor = _skip_spaces(text, term[1])
    if not lower.startswith("at the end", cursor):
        return None
    cursor = _skip_at_end_add_separator(text, cursor + len("at the end"))
    if not lower.startswith("add ", cursor):
        return None
    inserted = _read_quoted(text, cursor + len("add "))
    if inserted is None:
        return None
    return target_context_label, term[0].strip(), inserted[0].strip()


def _source_in_unit_label_before(prefix: str) -> str:
    for unit in ("subsection", "paragraph", "sub-paragraph", "section"):
        needle = f"in {unit} ("
        idx = prefix.rfind(needle)
        if idx < 0:
            continue
        start = idx + len(needle)
        end = prefix.find(")", start)
        if end >= 0:
            return prefix[start:end]
    return ""


def _effect_metadata_carried_at_end_add_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_at_end_add_insert(text)
    if parsed is None:
        return None
    parent_label, source_kind, source_label, inserted = parsed
    if "definition of" in text.lower():
        return None
    if source_kind:
        normalized_source_kind = source_kind.replace("-", "").lower()
        normalized_source_kind = (
            "subparagraph" if normalized_source_kind == "subparagraph" else normalized_source_kind
        )
        if _addr_leaf_kind(target) != normalized_source_kind:
            return None
        if _clean_num(_addr_leaf_label(target) or "") != _clean_num(source_label):
            return None
    parent_label = _clean_num(parent_label)
    if parent_label:
        target_labels = {_clean_num(label) for _, label in target.path}
        required_parent_labels = _source_label_parts(parent_label)
        if not required_parent_labels.issubset(target_labels):
            return None
    inserted = inserted.strip()
    if not inserted:
        return None
    return {
        "original": "TEXT_END",
        "replacement": inserted,
        "rule_id": UK_METADATA_CARRIED_AT_END_ADD_INSERT_RULE_ID,
    }


# Source-shape patterns the quoted at-end insert lowering must refuse rather than
# silently append to the host node's text: placement-ambiguous list/index inserts
# ("at the appropriate place"), and table/step contexts where "at the end" does not
# denote the end of the feed target's own text run.
_AT_END_INSERT_QUOTED_REFUSED_CONTEXTS = (
    "appropriate place",
    "appropriate places",
    "in step ",
    "of the table",
    "in the table",
)


def _effect_metadata_carried_at_end_insert_quoted_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    """Lower ``In <unit>(<label>), [at [the] end insert | insert at [the] end] "<q>"``.

    This is the ``insert`` verb counterpart to the ``add`` verb handled by
    ``_effect_metadata_carried_at_end_add_insert_fragment``.  Soundness mirrors that
    path: the payload is a *single quoted string*, the preimage anchor is the end of
    the feed target's own text run (``TEXT_END``), and any source-named unit/label is
    validated against the resolved target leaf and path.  Shapes where "at the end"
    does not denote the end of the target text run (definition entries, appropriate-
    place index inserts, table/step contexts) are refused so the broad overlap
    residue keeps owning them rather than appending to the wrong node.
    """
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    lowered_text = text.lower()
    if "definition of" in lowered_text:
        return None
    if any(marker in lowered_text for marker in _AT_END_INSERT_QUOTED_REFUSED_CONTEXTS):
        return None
    parsed = _parse_at_end_insert_quoted(text)
    if parsed is None:
        return None
    parent_label, source_kind, source_label, inserted = parsed
    if source_kind:
        normalized_source_kind = source_kind.replace("-", "").lower()
        if _addr_leaf_kind(target) != normalized_source_kind:
            return None
        if _clean_num(_addr_leaf_label(target) or "") != _clean_num(source_label):
            return None
    parent_label = _clean_num(parent_label)
    if parent_label:
        target_labels = {_clean_num(label) for _, label in target.path}
        required_parent_labels = _source_label_parts(parent_label)
        if not required_parent_labels.issubset(target_labels):
            return None
    inserted = inserted.strip()
    if not inserted:
        return None
    return {
        # TEXT_FROM__TO_END is the append sentinel the text-patch builder maps to a
        # true APPEND op (match_text="TEXT_END", kind=APPEND).  A bare "TEXT_END"
        # original would lower to a REPLACE whose synthetic selector cannot resolve.
        "original": "TEXT_FROM__TO_END",
        "replacement": inserted,
        "rule_id": UK_METADATA_CARRIED_AT_END_INSERT_QUOTED_RULE_ID,
    }


def _parse_at_end_insert_quoted(text: str) -> Optional[tuple[str, str, str, str]]:
    """Parse the two ``insert``-verb at-end word orders into a quoted tail payload.

    Order A: ``... at [the] end insert <quoted>``      (anchor then verb)
    Order B: ``... insert at [the] end <quoted>``       (verb then anchor)

    Returns ``(parent_label, source_kind, source_label, inserted)`` where the payload
    is required to be a single quoted string.  ``source_kind``/``source_label`` are
    populated for the ``at [the] end of <unit>(<label>)`` form so the caller can bind
    them to the target leaf.  Returns ``None`` when the shape is not a single-quoted
    at-end insert.
    """
    lower = text.lower()
    insert_idx = lower.find("insert")
    at_end_match = re.search(r"\bat\s+(?:the\s+)?end\b", lower)
    if insert_idx < 0 or at_end_match is None:
        return None
    at_end_start = at_end_match.start()
    at_end_after = at_end_match.end()

    if insert_idx > at_end_start:
        # Order A: "at [the] end ... insert <quoted>".  Only an optional
        # "of <unit>(<label>)" may sit between the anchor and the verb.
        source_kind, source_label, between_end = _read_at_end_of_unit(text, at_end_after)
        gap = lower[between_end:insert_idx].strip(" ,;—-")
        if gap:
            return None
        cursor = _skip_spaces(text, insert_idx + len("insert"))
        parent_label = _source_parent_label_at_end_insert(lower[:at_end_start])
    else:
        # Order B: "insert at [the] end <quoted>".  Nothing but separators may sit
        # between the verb and the anchor.
        gap = lower[insert_idx + len("insert") : at_end_start].strip(" ,;—-")
        if gap:
            return None
        source_kind, source_label, cursor = _read_at_end_of_unit(text, at_end_after)
        parent_label = _source_parent_label_at_end_insert(lower[:insert_idx])

    cursor = _skip_spaces(text, cursor)
    if cursor < len(text) and text[cursor] in ",;:—-":
        cursor = _skip_spaces(text, cursor + 1)
    inserted = _read_quoted(text, cursor)
    if inserted is None:
        return None
    return parent_label, source_kind, source_label, inserted[0].strip()


def _read_at_end_of_unit(text: str, cursor: int) -> tuple[str, str, int]:
    """Read an optional ``of <unit>(<label>)`` immediately after an at-end anchor."""
    lower = text.lower()
    cursor = _skip_spaces(text, cursor)
    if not lower.startswith("of ", cursor):
        return "", "", cursor
    after_of = _skip_spaces(text, cursor + len("of "))
    for candidate in ("sub-paragraph", "paragraph", "subsection", "section"):
        label_prefix = f"{candidate} ("
        if lower.startswith(label_prefix, after_of):
            label_start = after_of + len(label_prefix)
            label_end = text.find(")", label_start)
            if label_end < 0:
                return "", "", cursor
            return candidate, text[label_start:label_end], label_end + 1
    return "", "", cursor


def _source_parent_label_at_end_insert(prefix: str) -> str:
    """Extract a parent unit label from ``In <unit> (<label>)`` left context.

    Covers the common units that scope an at-end insert (subsection, paragraph,
    sub-paragraph, section).  Returns "" when no explicit unit/label is named (e.g.
    deictic "in that subsection"), in which case the caller falls back to the
    resolved feed target as the placement anchor.
    """
    prefix = prefix.strip(" ,;")
    best_idx = -1
    best_needle = ""
    for unit in ("sub-paragraph", "subsection", "paragraph", "section"):
        needle = f"in {unit} ("
        idx = prefix.rfind(needle)
        if idx > best_idx:
            best_idx = idx
            best_needle = needle
    if best_idx < 0:
        return ""
    start = best_idx + len(best_needle)
    end = prefix.find(")", start)
    if end < 0:
        return ""
    return prefix[start:end]


def _parse_at_end_add_insert(text: str) -> Optional[tuple[str, str, str, str]]:
    lower = text.lower()
    at_end_idx = lower.find("at the end")
    if at_end_idx < 0:
        return None
    source_kind = ""
    source_label = ""
    cursor = at_end_idx + len("at the end")
    if lower.startswith(" of ", cursor):
        cursor += len(" of ")
        for candidate in ("sub-paragraph", "paragraph", "subsection", "section"):
            label_prefix = f"{candidate} ("
            if lower.startswith(label_prefix, cursor):
                source_kind = candidate
                cursor += len(label_prefix)
                label_end = text.find(")", cursor)
                if label_end < 0:
                    return None
                source_label = text[cursor:label_end]
                cursor = label_end + 1
                break
        if not source_kind:
            return None
    cursor = _skip_at_end_add_separator(text, cursor)
    if not lower.startswith("add ", cursor):
        return None
    inserted = _read_quoted(text, cursor + len("add "))
    if inserted is None:
        return None
    parent_label = _source_parent_label_before_at_end(lower[:at_end_idx])
    return parent_label, source_kind, source_label, inserted[0].strip()


def _skip_at_end_add_separator(text: str, cursor: int) -> int:
    cursor = _skip_spaces(text, cursor)
    if cursor < len(text) and text[cursor] in ",;":
        cursor = _skip_spaces(text, cursor + 1)
    return cursor


def _source_parent_label_before_at_end(prefix: str) -> str:
    prefix = prefix.strip(" ,;")
    in_paragraph = prefix.rfind("in paragraph ")
    if in_paragraph < 0:
        return ""
    cursor = in_paragraph + len("in paragraph ")
    if cursor >= len(prefix):
        return ""
    if prefix[cursor] == "(":
        end = prefix.find(")", cursor + 1)
        if end < 0:
            return ""
        return prefix[cursor + 1 : end]
    end = cursor
    while end < len(prefix) and (prefix[end].isalnum() or prefix[end] in "()"):
        end += 1
    return prefix[cursor:end].strip("()")


def _source_label_parts(source_label: str) -> set[str]:
    cleaned = _clean_num(source_label)
    if not cleaned:
        return set()
    if "(" not in cleaned:
        return {cleaned}
    parts: set[str] = set()
    current = ""
    for char in cleaned:
        if char in "()":
            if current:
                parts.add(_clean_num(current))
                current = ""
            continue
        current += char
    if current:
        parts.add(_clean_num(current))
    return {part for part in parts if part}


def _text_patch_join(anchor: str, inserted: str) -> str:
    joiner = (
        ""
        if anchor.endswith((" ", "\t", "\n", "\r"))
        or inserted.startswith((" ", ",", ".", ";", ":", ")"))
        else " "
    )
    return f"{anchor}{joiner}{inserted}".strip()


def _effect_target_scoped_each_child_after_word_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text:
        return None
    parsed = _parse_each_child_after_word_insert(text)
    if parsed is None:
        return None
    parent_kind, parent_label, anchor, child_kind, child_labels, inserted = parsed
    parent_kind = parent_kind.replace("-", "").lower()
    parent_label = _clean_num(parent_label)
    if parent_kind and parent_label:
        if (parent_kind, parent_label) not in {
            (kind, _clean_num(label)) for kind, label in target.path
        }:
            return None
    child_kind = child_kind.replace("-", "").lower()
    child_kind = child_kind.removesuffix("s")
    if child_kind != _addr_leaf_kind(target):
        return None
    child_labels = {_clean_num(label) for label in child_labels}
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if not target_label or target_label not in child_labels:
        return None
    if not anchor or not inserted:
        return None
    return {
        "original": anchor,
        "replacement": _text_patch_join(anchor, inserted),
        "rule_id": UK_TARGET_SCOPED_EACH_CHILD_AFTER_WORD_INSERT_RULE_ID,
    }


def _parse_each_child_after_word_insert(
    text: str,
) -> Optional[tuple[str, str, str, str, tuple[str, ...], str]]:
    lower = text.lower()
    after_idx = lower.find("after ")
    if after_idx < 0:
        return None
    parent_kind = ""
    parent_label = ""
    prefix = lower[:after_idx].strip(" ,")
    if prefix.startswith("in "):
        for candidate in ("subsection", "paragraph", "sub-paragraph"):
            prefix_start = f"in {candidate} ("
            if prefix.startswith(prefix_start) and prefix.endswith(")"):
                parent_kind = candidate
                parent_label = prefix.removeprefix(prefix_start).removesuffix(")")
                break
    anchor_start = _skip_any_prefix(
        text,
        after_idx + len("after "),
        ("the word ", "word "),
    )
    anchor = _read_quoted(text, anchor_start)
    if anchor is None:
        return None
    marker = "where it occurs in each of "
    marker_idx = lower.find(marker, anchor[1])
    if marker_idx < 0:
        return None
    child_start = marker_idx + len(marker)
    child_kind = ""
    for candidate in ("subsections", "paragraphs", "sub-paragraphs"):
        if lower.startswith(f"{candidate} ", child_start):
            child_kind = candidate
            child_start += len(candidate) + 1
            break
    if not child_kind:
        return None
    insert_idx = lower.find(" insert ", child_start)
    if insert_idx < 0:
        return None
    labels_text = text[child_start:insert_idx].strip(" ,")
    child_labels = tuple(re.findall(r"\(([0-9A-Za-z]+)\)", labels_text))
    inserted = _read_quoted(text, insert_idx + len(" insert "))
    if inserted is None:
        return None
    return parent_kind, parent_label, anchor[0].strip(), child_kind, child_labels, inserted[0].strip()


def _effect_source_parent_carried_after_word_ordinal_insert_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_el: ET._Element,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word inserted", "words inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    child_match = _SOURCE_CHILD_WHERE_ORDINAL_INSERT_RE.search(text)
    if child_match is None:
        return None
    parent_el = extracted_el.getparent()
    if parent_el is None:
        return None
    parent_text = " ".join(" ".join(str(part) for part in parent_el.itertext()).split()).strip()
    anchor = _parse_parent_after_word_anchor(parent_text)
    if anchor is None:
        return None
    inserted = child_match.group("inserted").strip()
    occurrence = _ORDINAL_OCCURRENCES.get(child_match.group("ordinal").lower(), "")
    if not anchor or not inserted or not occurrence:
        return None
    return {
        "original": anchor,
        "replacement": _text_patch_join(anchor, inserted),
        "occurrence": occurrence,
        "rule_id": UK_SOURCE_PARENT_CARRIED_AFTER_WORD_ORDINAL_INSERT_RULE_ID,
    }


def _parse_parent_after_word_anchor(text: str) -> Optional[str]:
    lower = text.lower()
    after_idx = lower.find("after ")
    if after_idx < 0:
        return None
    anchor_start = _skip_any_prefix(text, after_idx + len("after "), ("the word ", "word "))
    anchor = _read_quoted(text, anchor_start)
    if anchor is None:
        return None
    if anchor[1] >= len(text) or text[anchor[1]] not in {"—", "-"}:
        return None
    return anchor[0].strip()


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


def _effect_definition_anchor_tail_fragment(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    extracted_text: str,
) -> Optional[dict[str, str]]:
    norm_effect_type = " ".join(str(effect.effect_type or "").lower().split())
    if norm_effect_type not in {"word substituted", "words inserted", "word inserted"}:
        return None
    text = " ".join(str(extracted_text or "").split()).strip()
    if not text or "after that definition" not in text.lower():
        return None
    if not _metadata_carried_quote_scope_matches_target(text, target):
        return None
    parsed = _parse_definition_anchor_tail_instruction(text)
    if parsed is None:
        return None
    term, tail = parsed
    if norm_effect_type == "word substituted":
        return {
            "original": f"TEXT_IN_DEFINITION_{term}{US}FINAL_PUNCT{US}.",
            "replacement": ";",
            "rule_id": UK_DEFINITION_ANCHOR_FINAL_PUNCTUATION_SUBSTITUTION_RULE_ID,
        }
    if tail:
        return {
            "original": f"TEXT_AFTER_DEFINITION_{term}",
            "replacement": tail,
            "rule_id": UK_DEFINITION_ANCHOR_TAIL_INSERT_RULE_ID,
        }
    return None


def _parse_definition_anchor_tail_instruction(text: str) -> Optional[tuple[str, str]]:
    lower = text.lower()
    marker = "for the full stop at the end of the definition of "
    marker_index = lower.find(marker)
    if marker_index < 0:
        return None
    quoted_term = _read_quoted(text, marker_index + len(marker))
    if quoted_term is None:
        return None
    term, after_term = quoted_term
    after_term_text = lower[after_term:]
    substitute_phrase = " substitute a semicolon and after that definition insert"
    substitute_index = after_term_text.find(substitute_phrase)
    if substitute_index < 0:
        return None
    tail_start = after_term + substitute_index + len(substitute_phrase)
    tail = text[tail_start:].strip(" \u2014-")
    while tail.endswith(" ."):
        tail = tail[:-2].rstrip()
    if not term.strip() or not tail:
        return None
    return term.strip(), tail

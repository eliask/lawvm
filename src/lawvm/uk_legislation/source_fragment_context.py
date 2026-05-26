from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from weakref import WeakKeyDictionary

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
from lawvm.uk_legislation.nlp_parser import (
    UK_AFTER_QUOTED_ANCHOR_EACH_OTHER_PLACE_INSERT_RULE_ID,
    UK_SIBLING_FIRST_THEN_EACH_OTHER_PLACE_SUBSTITUTION_RULE_ID,
    US,
)
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag, _text_content


_AFTER_WORDS_INSERTED_BY_SIBLING_RE = re.compile(
    r"\bafter\s+the\s+words\s+inserted\s+by\s+(?:sub-?paragraph|paragraph)\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
    r"insert(?:\s+[“\"'‘](?P<quoted>.*?)[”\"'’]|\s*[—-]\s*(?P<block>.+?)(?:\s+[.,;])?$)",
    flags=re.I,
)

_GROUPED_ANCHOR_OCCURRENCE_CHILD_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+the\s+"
    r"(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+"
    r"time\s+it\s+(?:appears|occurs),?\s+substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*;?\s*$",
    flags=re.I,
)

_GROUPED_ANCHOR_OCCURRENCE_PARENT_RE = re.compile(
    r"(?:^|\b)for\s+(?:the\s+words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]\s*[—-]\s*$",
    flags=re.I,
)

_GROUPED_AFTER_INSERT_CHILD_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+"
    r"[“\"'‘](?P<anchor>.*?)[”\"'’]"
    r"(?P<all_occurrences>,?\s+in\s+(?:both|each)\s+places?)?"
    r"\s*,?\s*(?:and)?\s*$",
    flags=re.I,
)

_GROUPED_AFTER_INSERT_PARENT_TAIL_RE = re.compile(
    r"\binsert(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]\s*\.?\s*$",
    flags=re.I,
)

_SOURCE_PARENT_EACH_PROVISION_SUBSTITUTION_RE = re.compile(
    r"\bIn\s+each\s+provision\s+specified\b.+?\bfor\s+"
    r"[“\"'‘](?P<original_a>.*?)[”\"'’]\s+or,\s+as\s+the\s+case\s+may\s+be,\s+"
    r"[“\"'‘](?P<original_b>.*?)[”\"'’]\s+there\s+is\s+substituted\s+"
    r"[“\"'‘](?P<replacement>.*?)[”\"'’]",
    flags=re.I,
)
_SOURCE_PARENT_FOLLOWING_PROVISIONS_SUBSTITUTION_RE = re.compile(
    r"\bIn\s+the\s+following\s+(?:provisions|enactments)\b.+?\bfor\s+"
    r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
    r"(?:there\s+(?:is|are|shall\s+be)\s+substituted|substitute)\s+"
    r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]",
    flags=re.I,
)
_SOURCE_PARENT_FOLLOWING_PROVISIONS_SUBSTITUTION_REVERSED_RE = re.compile(
    r"\bIn\s+the\s+following\s+(?:provisions|enactments)\b.+?\bsubstitute\s+"
    r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]\s+"
    r"for\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]",
    flags=re.I,
)
_SOURCE_PARENT_TAIL_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]\s+"
    r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\s+"
    r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]",
    flags=re.I,
)
_SOURCE_PARENT_PREFIX_SUBSTITUTE_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+|[ivxlcdm]+)?\s*"
    r"(?:Substitute|For)\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*$",
    flags=re.I,
)
_SOURCE_PARENT_AT_END_TEXT_INSERT_RE = re.compile(
    r"(?:\bat\s+the\s+end(?:\s+of\s+(?:(?:that|the)\s+)?"
    r"(?:paragraph|sub-?paragraph|subsection|section)(?:\s+\([^)]+\))?"
    r"(?:\s+\([^)]*\))?(?:\s+of\s+(?:that|the)\s+section)?)?,?\s+"
    r"(?:(?:there\s+(?:is|are|shall\s+be)\s+)?insert(?:ed)?"
    r"(?:\s+the\s+following\s+definition)?|"
    r"(?:there\s+(?:is|are)\s+)?added)|\binsert(?:ed)?\s+at\s+the\s+end)\s*[—–-]?\s*$",
    flags=re.I,
)
_SOURCE_CHILD_TARGET_ONLY_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+"
    r"(?:(?:[A-Z][A-Za-z0-9.]*,\s*)?(?:sections?|subsections?|paragraphs?|sub-paragraphs?|Schedules?|Parts?)\b|[A-Z][^.;]*?\bAct\s+\d{4}\b)",
    flags=re.I,
)
_SOURCE_CHILD_FOR_QUOTED_IN_TARGET_RE = re.compile(
    r"^\s*(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+for\s+"
    r"(?:(?:the\s+)?words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’]\s+in\s+",
    flags=re.I,
)


def _source_parent_opens_target_list(lead_text: str) -> bool:
    normalized = " ".join((lead_text or "").split()).strip()
    if not normalized.endswith(("—", "-")):
        return False
    lowered = normalized.lower()
    return lowered.startswith(("in ", "for ")) or " in " in lowered or " for " in lowered

_EACH_OTHER_PLACE_AFTER_INSERT_RE = re.compile(
    r"\bafter\s+(?:the\s+words?\s+)?[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
    r"in\s+each\s+other\s+place(?:\s+(?:where\s+)?(?:it|they|those\s+words?)?\s*"
    r"(?:occurs?|occurring|appears?|appear))?,?\s+"
    r"(?:there\s+(?:is|are|shall\s+be)\s+inserted|insert)\s+"
    r"(?:the\s+words?\s+)?[“\"'‘](?P<inserted>.*?)[”\"'’]",
    flags=re.I,
)

_EACH_OTHER_PLACE_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+(?:the\s+words?\s+)?[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
    r"in\s+each\s+other\s+place(?:\s+(?:where\s+)?(?:it|they|those\s+words?)?\s*"
    r"(?:occurs?|occurring|appears?|appear))?,?\s+"
    r"(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\s+"
    r"(?:the\s+words?\s+)?[“\"'‘](?P<replacement>.*?)[”\"'’]",
    flags=re.I,
)

_SOURCE_SUBORDINATE_ROW_TAGS = frozenset({"P1", "P2", "P3", "P4", "P5", "P6"})
_SOURCE_PARENT_AT_END_TEXT_INSERT_RULE_ID = "uk_effect_source_parent_at_end_text_insertion_patch"
_SOURCE_PARENT_AT_END_QUOTED_LIST_TEXT_INSERT_RULE_ID = (
    "uk_effect_source_parent_at_end_quoted_list_text_insertion_patch"
)
_SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RULE_ID = (
    "uk_effect_source_parent_word_range_substitution_text_patch"
)
_SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RE = re.compile(
    r"\bfor\s+(?:the\s+)?words?\s+from\s+[“\"'‘](?P<start>.*?)[”\"'’]\s+"
    r"to\s+[“\"'‘](?P<end>.*?)[”\"'’]\s+"
    r"(?:there\s+(?:is|are|shall\s+be)\s+)?substitut(?:ed|e)\s+"
    r"(?:(?:the\s+)?words?)?\s*[—–-]?\s*$",
    flags=re.I,
)
_SOURCE_LEAD_TEXT_CACHE: WeakKeyDictionary[ET.Element, str] = WeakKeyDictionary()
_SOURCE_TAIL_TEXT_CACHE: WeakKeyDictionary[ET.Element, str] = WeakKeyDictionary()


def append_source_fragment_context_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    for sibling_context_fragment in fragment_subs or []:
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
                "target_ref": target_ref,
                "target": str(target),
                "source_sibling_label": str(sibling_context_fragment.get("source_sibling_label") or ""),
                "source_sibling_rule_id": str(sibling_context_fragment.get("source_sibling_rule_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for grouped_context_fragment in fragment_subs or []:
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
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(grouped_context_fragment.get("source_parent_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
            },
        )
    grouped_after_insert_rule_ids = {
        "uk_effect_source_parent_grouped_after_anchor_insert_text_patch",
        "uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch",
    }
    for grouped_after_insert_fragment in fragment_subs or []:
        grouped_after_insert_rule_id = str(grouped_after_insert_fragment.get("rule_id") or "")
        if grouped_after_insert_rule_id not in grouped_after_insert_rule_ids:
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=grouped_after_insert_rule_id,
            family="source_context_elaboration",
            reason_code="text_insert_payload_resolved_from_group_parent",
            reason=(
                "UK source child row gives a quoted anchor while its grouped "
                "parent instruction carries the insertion payload. Lowering "
                "combines those source-local facts instead of guessing from "
                "live text."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(grouped_after_insert_fragment.get("source_parent_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "all_occurrences": bool(grouped_after_insert_fragment.get("all_occurrences")),
            },
        )
    for parent_substitution_fragment in fragment_subs or []:
        if (
            str(parent_substitution_fragment.get("rule_id") or "")
            != "uk_effect_source_parent_each_provision_substitution_text_patch"
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_source_parent_each_provision_substitution_text_patch",
            family="source_context_elaboration",
            reason_code="text_substitution_resolved_from_each_provision_parent",
            reason=(
                "UK source child row identifies a target provision while its "
                "parent list instruction carries the quoted substitution; "
                "lowering combines those source-local facts instead of treating "
                "the child row as an unsupported fragment."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(parent_substitution_fragment.get("source_parent_id") or ""),
                "text_match": str(parent_substitution_fragment.get("original") or ""),
                "replacement": op_text_replacement,
            },
        )
    for parent_prefix_substitution_fragment in fragment_subs or []:
        if (
            str(parent_prefix_substitution_fragment.get("rule_id") or "")
            != "uk_effect_source_parent_prefix_substitute_text_patch"
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_source_parent_prefix_substitute_text_patch",
            family="source_context_elaboration",
            reason_code="text_substitution_replacement_resolved_from_source_parent_prefix",
            reason=(
                "UK source child row carries the quoted preimage and target "
                "context while its parent prefix carries the replacement. "
                "Lowering combines those source-local facts instead of "
                "treating the child as a standalone incomplete instruction."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(parent_prefix_substitution_fragment.get("source_parent_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for following_provisions_fragment in fragment_subs or []:
        if (
            str(following_provisions_fragment.get("rule_id") or "")
            != "uk_effect_source_parent_following_provisions_substitution_text_patch"
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_source_parent_following_provisions_substitution_text_patch",
            family="source_context_elaboration",
            reason_code="text_substitution_resolved_from_following_provisions_parent",
            reason=(
                "UK source child row enumerates a target provision while its "
                "parent instruction carries the quoted substitution for the "
                "following provisions. Lowering combines those source-local "
                "facts and leaves target selection to effects metadata."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(following_provisions_fragment.get("source_parent_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for tail_substitution_fragment in fragment_subs or []:
        if (
            str(tail_substitution_fragment.get("rule_id") or "")
            != "uk_effect_source_parent_tail_substitution_text_patch"
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_source_parent_tail_substitution_text_patch",
            family="source_context_elaboration",
            reason_code="text_substitution_resolved_from_source_parent_tail",
            reason=(
                "UK source child row enumerates a target provision while its "
                "parent opens a target list and carries the quoted substitution "
                "after the child rows. Lowering combines those source-local "
                "facts and leaves target selection to effects metadata."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(tail_substitution_fragment.get("source_parent_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for heading_source_parent_fragment in fragment_subs or []:
        if (
            str(heading_source_parent_fragment.get("rule_id") or "")
            != "uk_effect_heading_facet_source_parent_full_replacement_text_patch"
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id="uk_effect_heading_facet_source_parent_full_replacement_text_patch",
            family="source_context_elaboration",
            reason_code="heading_replacement_resolved_from_source_parent",
            reason=(
                "UK source payload carries only the inserted body provisions, "
                "while its parent instruction carries the heading/title "
                "replacement. Lowering combines those source-local facts for "
                "the heading facet target instead of mutating the host body."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(
                    heading_source_parent_fragment.get("source_parent_id") or ""
                ),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for source_parent_word_range_fragment in fragment_subs or []:
        if (
            str(source_parent_word_range_fragment.get("rule_id") or "")
            != _SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RULE_ID
        ):
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=_SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RULE_ID,
            family="source_context_elaboration",
            reason_code="word_range_substitution_resolved_from_source_parent",
            reason=(
                "UK source payload carries only the replacement words while "
                "its local parent instruction explicitly names the word range "
                "to be substituted. Lowering combines those source-local facts "
                "into a typed range text patch instead of treating the payload "
                "as a standalone broad text rewrite."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(
                    source_parent_word_range_fragment.get("source_parent_id") or ""
                ),
                "source_parent_instruction": str(
                    source_parent_word_range_fragment.get("source_parent_instruction") or ""
                ),
                "payload_shape": str(source_parent_word_range_fragment.get("payload_shape") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    for each_other_fragment in fragment_subs or []:
        each_other_rule_id = str(each_other_fragment.get("rule_id") or "")
        if each_other_rule_id not in {
            UK_AFTER_QUOTED_ANCHOR_EACH_OTHER_PLACE_INSERT_RULE_ID,
            UK_SIBLING_FIRST_THEN_EACH_OTHER_PLACE_SUBSTITUTION_RULE_ID,
        }:
            continue
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=each_other_rule_id,
            family="source_context_elaboration",
            reason_code="relative_each_other_place_resolved_from_first_occurrence_sibling",
            reason=(
                "UK source uses a relative 'each other place' occurrence selector; "
                "lowering proceeds only because a preceding source sibling explicitly "
                "claims the first occurrence of the same quoted anchor."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_sibling_label": str(each_other_fragment.get("source_sibling_label") or ""),
                "source_sibling_rule_id": str(each_other_fragment.get("source_sibling_rule_id") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "selector_mode": str(each_other_fragment.get("selector_mode") or ""),
            },
        )
    source_parent_at_end_reasons = {
        _SOURCE_PARENT_AT_END_TEXT_INSERT_RULE_ID: (
            "text_insert_end_resolved_from_source_parent",
            (
                "UK source payload carries only inserted text while its local "
                "parent instruction explicitly says the text is inserted at "
                "the end. Lowering combines those source-local facts into a "
                "typed end-append text patch instead of treating the payload "
                "as a standalone broad text rewrite."
            ),
        ),
        _SOURCE_PARENT_AT_END_QUOTED_LIST_TEXT_INSERT_RULE_ID: (
            "quoted_list_text_insert_end_resolved_from_source_parent",
            (
                "UK source payload carries an inserted quoted list item as XML "
                "row markup, while the effect feed classifies the change as a "
                "word-level insertion and the local parent instruction says it "
                "is inserted at the end. Lowering flattens only this quoted "
                "payload shape into a typed end-append text patch."
            ),
        ),
    }
    for source_parent_at_end_fragment in fragment_subs or []:
        source_parent_rule_id = str(source_parent_at_end_fragment.get("rule_id") or "")
        source_parent_reason = source_parent_at_end_reasons.get(source_parent_rule_id)
        if source_parent_reason is None:
            continue
        reason_code, reason = source_parent_reason
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=source_parent_rule_id,
            family="source_context_elaboration",
            reason_code=reason_code,
            reason=reason,
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "source_parent_id": str(source_parent_at_end_fragment.get("source_parent_id") or ""),
                "source_parent_instruction": str(
                    source_parent_at_end_fragment.get("source_parent_instruction") or ""
                ),
                "payload_shape": str(source_parent_at_end_fragment.get("payload_shape") or ""),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )


def _fragment_substitution_after_words_inserted_by_sibling(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve "after the words inserted by sub-paragraph (a)" from a source sibling."""
    text = " ".join((extracted_text or "").split())
    match = _AFTER_WORDS_INSERTED_BY_SIBLING_RE.search(text)
    if not match:
        return None
    sibling_label = _clean_num(match.group("label"))
    inserted_raw = match.group("quoted") if match.group("quoted") is not None else match.group("block")
    inserted = " ".join((inserted_raw or "").split()).strip()
    if not sibling_label or not inserted:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return None
    parent = ancestors[0]
    for child in parent:
        if child is extracted_el or (extracted_el is not None and child.get("id") == extracted_el.get("id")):
            continue
        if _clean_num(_direct_structural_num(child)) != sibling_label:
            continue
        sibling_fragments = parse_fragment_substitution(_text_content(child))
        if len(sibling_fragments) != 1:
            return None
        sibling_fragment = sibling_fragments[0]
        anchor = " ".join(str(sibling_fragment.get("replacement") or "").split()).strip()
        if not anchor:
            return None
        joiner = "" if anchor.endswith((" ", "\t", "\n", "\r")) or inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        return {
            "original": anchor,
            "replacement": f"{anchor}{joiner}{inserted}",
            "source_sibling_label": sibling_label,
            "source_sibling_rule_id": str(sibling_fragment.get("rule_id") or "fragment_substitution"),
            "rule_id": "uk_effect_after_words_inserted_by_sibling_text_patch",
        }
    return None


def _source_lead_text_before_subordinate_rows(el: ET.Element) -> str:
    cached = _SOURCE_LEAD_TEXT_CACHE.get(el)
    if cached is not None:
        return cached
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if _tag(child) in _SOURCE_SUBORDINATE_ROW_TAGS:
            break
        parts.append(_text_content(child))
        if child.tail:
            parts.append(child.tail)
    text = " ".join(" ".join(parts).split())
    _SOURCE_LEAD_TEXT_CACHE[el] = text
    return text


def _source_tail_text_after_subordinate_rows(el: ET.Element) -> str:
    cached = _SOURCE_TAIL_TEXT_CACHE.get(el)
    if cached is not None:
        return cached
    parts: list[str] = []
    seen_subordinate = False
    for child in el:
        if _tag(child) in _SOURCE_SUBORDINATE_ROW_TAGS:
            seen_subordinate = True
            if child.tail:
                parts.append(child.tail)
            continue
        if seen_subordinate:
            parts.append(_text_content(child))
            if child.tail:
                parts.append(child.tail)
    text = " ".join(" ".join(parts).split())
    _SOURCE_TAIL_TEXT_CACHE[el] = text
    return text


def _source_has_subordinate_row_scope(el: ET.Element) -> bool:
    """Return true when an ancestor can contain unrelated sibling amendment rows."""
    if _tag(el) in {"Legislation", "Body", "Pblock"}:
        return True
    for child in el:
        child_tag = _tag(child)
        if child_tag in _SOURCE_SUBORDINATE_ROW_TAGS:
            return True
        if child_tag.endswith("para"):
            if any(_tag(grandchild) in _SOURCE_SUBORDINATE_ROW_TAGS for grandchild in child):
                return True
    return False


def _source_local_instruction_text_for_carried_payload(ancestor: ET.Element) -> str:
    """Collect only source-local instruction text for a carried BlockAmendment.

    Broad containers such as Pblock/P1/P1para may contain earlier sibling rows
    with unrelated definition instructions. Those rows cannot supply the anchor
    for the current payload.
    """
    lead_text = _source_lead_text_before_subordinate_rows(ancestor)
    if lead_text:
        return lead_text
    if _source_has_subordinate_row_scope(ancestor):
        return ""
    return _instruction_text_before_amendment_container(ancestor)


def _is_flat_source_list_item_quote(row: ET.Element) -> bool:
    if _tag(row) not in _SOURCE_SUBORDINATE_ROW_TAGS:
        return False
    if not any(_tag(child) == "Pnumber" and _text_content(child).strip() for child in row):
        return False
    para_children = [child for child in row if _tag(child).endswith("para")]
    if len(para_children) != 1:
        return False
    for descendant in para_children[0].iter():
        if descendant is para_children[0]:
            continue
        descendant_tag = _tag(descendant)
        if descendant_tag in _SOURCE_SUBORDINATE_ROW_TAGS or descendant_tag in {
            "BlockAmendment",
            "InlineAmendment",
            "Table",
            "Tabular",
        }:
            return False
    return bool(_text_content(para_children[0]).strip())


def _source_payload_has_disallowed_text_flattening_descendant(el: ET.Element) -> bool:
    for descendant in el.iter():
        if descendant is el:
            continue
        descendant_tag = _tag(descendant)
        if descendant_tag in {
            "BlockAmendment",
            "InlineAmendment",
            "Table",
            "Tabular",
            "Section",
            "Subsection",
            "Paragraph",
            "Part",
            "Chapter",
            "Schedule",
        }:
            return True
    return False


def _source_parent_at_end_text_insert_payload_shape(extracted_el: ET.Element) -> str:
    """Classify source payload shapes that can safely become word-level appends."""
    direct_children = list(extracted_el)
    subordinate_rows = [
        child for child in direct_children if _tag(child) in _SOURCE_SUBORDINATE_ROW_TAGS
    ]
    if not subordinate_rows:
        if _source_payload_has_disallowed_text_flattening_descendant(extracted_el):
            return ""
        return "plain_text"
    if len(subordinate_rows) != 1:
        return ""
    if any(_tag(child) not in {"Text", _tag(subordinate_rows[0])} for child in direct_children):
        return ""
    direct_text = " ".join(
        _text_content(child).strip() for child in direct_children if _tag(child) == "Text"
    ).strip()
    if not re.match(r"^(?:[,;:]|\band\b|\bor\b)", direct_text, flags=re.I):
        return ""
    if not _is_flat_source_list_item_quote(subordinate_rows[0]):
        return ""
    return "quoted_list_item_flattened_text"


def _source_parent_word_range_payload_shape(extracted_el: ET.Element) -> str:
    """Classify replacement payloads safe to flatten for word-range substitution."""
    direct_children = list(extracted_el)
    subordinate_rows = [
        child for child in direct_children if _tag(child) in _SOURCE_SUBORDINATE_ROW_TAGS
    ]
    if not subordinate_rows:
        if _source_payload_has_disallowed_text_flattening_descendant(extracted_el):
            return ""
        return "plain_text"
    if any(_tag(child) not in {"Text"} | _SOURCE_SUBORDINATE_ROW_TAGS for child in direct_children):
        return ""
    if any(not _is_flat_source_list_item_quote(row) for row in subordinate_rows):
        return ""
    return "flat_numbered_rows_text"


def _fragment_substitution_grouped_anchor_occurrence(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve child rows like "the first time it appears" from a carried parent anchor."""
    child_match = _GROUPED_ANCHOR_OCCURRENCE_CHILD_RE.match(" ".join((extracted_text or "").split()))
    if not child_match:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            candidate_text = _instruction_text_before_amendment_container(ancestor)
        parent_match = _GROUPED_ANCHOR_OCCURRENCE_PARENT_RE.search(candidate_text.strip())
        if not parent_match:
            continue
        original = parent_match.group("original").strip()
        replacement = child_match.group("replacement").strip()
        if not original or not replacement:
            return None
        occurrence = _uk_ordinal_to_int(child_match.group("ordinal"))
        if occurrence is None:
            return None
        return {
            "original": original,
            "replacement": replacement,
            "occurrence": str(occurrence),
            "source_parent_id": str(
                ancestor.get("id")
                or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
            ),
            "rule_id": "uk_effect_grouped_anchor_occurrence_substitution_text_patch",
        }
    return None


def _previous_source_sibling_first_occurrence_rule(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    anchor: str,
) -> Optional[dict[str, str]]:
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    if not ancestors or extracted_el is None:
        return None
    parent = ancestors[0]
    normalized_anchor = " ".join(anchor.split()).strip()
    for child in parent:
        if child is extracted_el or child.get("id") == extracted_el.get("id"):
            break
        sibling_label = _clean_num(_direct_structural_num(child))
        for sibling_fragment in parse_fragment_substitution(_text_content(child)):
            if " ".join(str(sibling_fragment.get("original") or "").split()).strip() != normalized_anchor:
                continue
            if str(sibling_fragment.get("occurrence") or "") != "1":
                continue
            return {
                "source_sibling_label": sibling_label,
                "source_sibling_rule_id": str(sibling_fragment.get("rule_id") or "fragment_substitution"),
                "source_sibling_replacement": " ".join(
                    str(sibling_fragment.get("replacement") or "").split()
                ).strip(),
            }
    return None


def _fragment_substitution_each_other_place_from_sibling(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve relative `each other place` only when a sibling owns the first occurrence."""
    text = " ".join((extracted_text or "").split())
    insert_match = _EACH_OTHER_PLACE_AFTER_INSERT_RE.search(text)
    if insert_match is not None:
        anchor = " ".join(insert_match.group("anchor").split()).strip()
        inserted = " ".join(insert_match.group("inserted").split()).strip()
        sibling = _previous_source_sibling_first_occurrence_rule(
            extracted_el=extracted_el,
            source_root=source_root,
            anchor=anchor,
        )
        if not anchor or not inserted or sibling is None:
            return None
        return {
            "original": f"TEXT_AFTER_EACH_OTHER_OCCURRENCE{US}{anchor}",
            "replacement": inserted,
            "selector_mode": "after_each_other_occurrence_except_first",
            **sibling,
            "rule_id": UK_AFTER_QUOTED_ANCHOR_EACH_OTHER_PLACE_INSERT_RULE_ID,
        }

    substitution_match = _EACH_OTHER_PLACE_SUBSTITUTION_RE.search(text)
    if substitution_match is not None:
        original = " ".join(substitution_match.group("original").split()).strip()
        replacement = " ".join(substitution_match.group("replacement").split()).strip()
        sibling = _previous_source_sibling_first_occurrence_rule(
            extracted_el=extracted_el,
            source_root=source_root,
            anchor=original,
        )
        if not original or not replacement or sibling is None:
            return None
        return {
            "original": (
                f"TEXT_EACH_OTHER_OCCURRENCE_AFTER_FIRST_SIBLING"
                f"{US}{str(sibling.get('source_sibling_replacement') or '')}{US}{original}"
            ),
            "replacement": replacement,
            "selector_mode": "all_remaining_after_first_occurrence_sibling",
            **sibling,
            "rule_id": UK_SIBLING_FIRST_THEN_EACH_OTHER_PLACE_SUBSTITUTION_RULE_ID,
        }

    return None


def _fragment_substitution_grouped_after_insert_from_parent(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve grouped `after-- child rows insert "X"` source fragments."""
    child_match = _GROUPED_AFTER_INSERT_CHILD_RE.match(" ".join((extracted_text or "").split()))
    if not child_match:
        return None
    anchor = child_match.group("anchor").strip()
    if not anchor:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor).strip()
        if not re.search(r"\bafter\s*[—-]\s*$", candidate_text, flags=re.I):
            continue
        tail_text = _source_tail_text_after_subordinate_rows(ancestor)
        tail_match = _GROUPED_AFTER_INSERT_PARENT_TAIL_RE.search(tail_text)
        if not tail_match:
            continue
        inserted = tail_match.group("inserted").strip()
        if not inserted:
            return None
        joiner = "" if anchor.endswith((" ", "\t", "\n", "\r")) or inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
        all_occurrences = bool(child_match.group("all_occurrences"))
        return {
            "original": anchor,
            "replacement": f"{anchor}{joiner}{inserted}",
            "all_occurrences": "true" if all_occurrences else "",
            "source_parent_id": str(
                ancestor.get("id")
                or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
            ),
            "rule_id": (
                "uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch"
                if all_occurrences
                else "uk_effect_source_parent_grouped_after_anchor_insert_text_patch"
            ),
        }
    return None


def _fragment_substitutions_source_parent_each_provision_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> tuple[dict[str, str], ...]:
    """Resolve child target rows governed by a parent `In each provision ...` substitution."""
    child_text = " ".join((extracted_text or "").split())
    if not child_text or re.search(r"\bsubstitut(?:e|ed)\b", child_text, flags=re.I):
        return ()
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        match = _SOURCE_PARENT_EACH_PROVISION_SUBSTITUTION_RE.search(candidate_text)
        if match is None:
            continue
        originals = tuple(
            original
            for original in (
                " ".join(match.group("original_a").split()).strip(),
                " ".join(match.group("original_b").split()).strip(),
            )
            if original
        )
        replacement = " ".join(match.group("replacement").split()).strip()
        if len(originals) < 2 or not replacement:
            return ()
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        return tuple(
            {
                "original": original,
                "replacement": replacement,
                "source_parent_id": source_parent_id,
                "rule_id": "uk_effect_source_parent_each_provision_substitution_text_patch",
            }
            for original in originals
        )
    return ()


def _fragment_substitution_source_parent_following_provisions_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve child target rows governed by a parent `In the following provisions` substitution."""
    child_text = " ".join((extracted_text or "").split())
    if not child_text or re.search(
        r"\b(?:for|substitut(?:e|ed)|insert(?:ed)?|omit(?:ted)?|repeal(?:ed)?)\b",
        child_text,
        flags=re.I,
    ):
        return None
    if _SOURCE_CHILD_TARGET_ONLY_RE.match(child_text) is None:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        match = _SOURCE_PARENT_FOLLOWING_PROVISIONS_SUBSTITUTION_RE.search(candidate_text)
        if match is None:
            match = _SOURCE_PARENT_FOLLOWING_PROVISIONS_SUBSTITUTION_REVERSED_RE.search(
                candidate_text
            )
        if match is None:
            continue
        original = " ".join(match.group("original").split()).strip()
        replacement = " ".join(match.group("replacement").split()).strip()
        if not original or not replacement:
            return None
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": source_parent_id,
            "rule_id": "uk_effect_source_parent_following_provisions_substitution_text_patch",
        }
    return None


def _fragment_substitution_source_parent_tail_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve target-list child rows governed by a substitution in the parent tail."""
    child_text = " ".join((extracted_text or "").split())
    if not child_text or re.search(
        r"\b(?:for|substitut(?:e|ed)|insert(?:ed)?|omit(?:ted)?|repeal(?:ed)?)\b",
        child_text,
        flags=re.I,
    ):
        return None
    if _SOURCE_CHILD_TARGET_ONLY_RE.match(child_text) is None:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        lead_text = _source_lead_text_before_subordinate_rows(ancestor).strip()
        if not _source_parent_opens_target_list(lead_text):
            continue
        tail_text = _source_tail_text_after_subordinate_rows(ancestor)
        match = _SOURCE_PARENT_TAIL_SUBSTITUTION_RE.search(tail_text)
        if match is None:
            continue
        original = " ".join(match.group("original").split()).strip()
        replacement = " ".join(match.group("replacement").split()).strip()
        if not original or not replacement:
            return None
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": source_parent_id,
            "rule_id": "uk_effect_source_parent_tail_substitution_text_patch",
        }
    return None


def _fragment_substitution_source_parent_prefix_substitute(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve child rows governed by a parent `Substitute "X"` prefix."""
    child_text = " ".join((extracted_text or "").split())
    child_match = _SOURCE_CHILD_FOR_QUOTED_IN_TARGET_RE.match(child_text)
    if child_match is None or re.search(r"\bsubstitut(?:e|ed)\b", child_text, flags=re.I):
        return None
    original = " ".join(child_match.group("original").split()).strip()
    if not original:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor).strip()
        parent_match = _SOURCE_PARENT_PREFIX_SUBSTITUTE_RE.match(candidate_text)
        if parent_match is None:
            continue
        replacement = " ".join(parent_match.group("replacement").split()).strip()
        if not replacement:
            return None
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        return {
            "original": original,
            "replacement": replacement,
            "source_parent_id": source_parent_id,
            "rule_id": "uk_effect_source_parent_prefix_substitute_text_patch",
        }
    return None


def _fragment_substitution_source_parent_word_range_substitution(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve payload-only replacements governed by a local word-range parent."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or extracted_el is None or _tag(extracted_el) not in {
        "BlockAmendment",
        "InlineAmendment",
    }:
        return None
    payload_shape = _source_parent_word_range_payload_shape(extracted_el)
    if not payload_shape:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        if not instruction_text:
            instruction_text = _source_local_instruction_text_for_carried_payload(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        match = _SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RE.search(instruction_text)
        if match is None:
            continue
        start = " ".join(match.group("start").split()).strip()
        end = " ".join(match.group("end").split()).strip()
        if not start or not end:
            return None
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        return {
            "original": f"TEXT_FROM_{start}_TO_{end}",
            "replacement": payload_text,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
            "payload_shape": payload_shape,
            "rule_id": _SOURCE_PARENT_WORD_RANGE_SUBSTITUTION_RULE_ID,
        }
    return None


def _fragment_substitution_source_parent_at_end_text_insert(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve payload-only text governed by a local `at the end insert` parent."""
    payload_text = " ".join((extracted_text or "").split()).strip()
    if not payload_text or extracted_el is None or _tag(extracted_el) not in {
        "BlockAmendment",
        "InlineAmendment",
    }:
        return None
    payload_shape = _source_parent_at_end_text_insert_payload_shape(extracted_el)
    if not payload_shape:
        return None
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = _instruction_text_before_amendment_container(ancestor)
        if not instruction_text:
            instruction_text = _source_local_instruction_text_for_carried_payload(ancestor)
        instruction_text = " ".join(instruction_text.split()).strip()
        if re.search(r"\b(?:table|column|columns?|entry|entries)\b", instruction_text, flags=re.I):
            continue
        if not instruction_text or not _SOURCE_PARENT_AT_END_TEXT_INSERT_RE.search(instruction_text):
            continue
        source_parent_id = str(
            ancestor.get("id")
            or next((candidate.get("id") for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")), "")
        )
        rule_id = (
            _SOURCE_PARENT_AT_END_QUOTED_LIST_TEXT_INSERT_RULE_ID
            if payload_shape == "quoted_list_item_flattened_text"
            else _SOURCE_PARENT_AT_END_TEXT_INSERT_RULE_ID
        )
        return {
            "original": "TEXT_FROM__TO_END",
            "replacement": payload_text,
            "source_parent_id": source_parent_id,
            "source_parent_instruction": instruction_text,
            "payload_shape": payload_shape,
            "rule_id": rule_id,
        }
    return None

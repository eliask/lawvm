from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from weakref import WeakKeyDictionary

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
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

_SOURCE_SUBORDINATE_ROW_TAGS = frozenset({"P1", "P2", "P3", "P4", "P5", "P6"})
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

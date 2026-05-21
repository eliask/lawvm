from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_field, _schedule_target_levels
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.uk_grafter import _clean_num


_SOURCE_CARRIED_MULTI_SUBUNIT_REPEAL_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+section\s+(?P<section>[0-9A-Za-z]+)\b.*?,\s+"
    r"the\s+words\s+[“\"'‘](?P<original>.*?)[”\"'’],?\s+"
    r"where\s+they\s+occur\s+in\s+subsections?\s+"
    r"(?P<labels>\([0-9A-Za-z]+\)(?:\s*(?:,|and)\s*\([0-9A-Za-z]+\))*)"
    r",?\s+are\s+repealed\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_AMENDMENT_INSERTED_TEXT_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+paragraph\s+(?P<paragraph>[0-9A-Za-z]+)\b.*?,\s+"
    r"in\s+sub-?paragraph\s+\((?P<item>[0-9A-Za-z]+)\),?\s+"
    r"for\s+the\s+inserted\s+text\s+substitute\s*[—-]\s*(?P<replacement>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_AMENDMENT_INSERTED_PARENT_STRUCTURAL_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+sub-?paragraph\s+\((?P<subparagraph>[0-9A-Za-z]+)\)\s*"
    r"\((?P<item>[0-9A-Za-z]+)\),?\s+"
    r"in\s+the\s+inserted\s+paragraph\s+\((?P<inserted_parent>[0-9A-Za-z]+)\),?\s+"
    r"(?P<direction>before|after)\s+sub-?paragraph\s+\((?P<anchor>[0-9A-Za-z]+)\)\s+"
    r"insert\s*[—–-]\s*(?P<inserted_label>[0-9A-Za-z]+)\s+"
    r"(?P<inserted_text>.+?)\s*$",
    flags=re.I | re.S,
)


def _fragment_substitution_source_carried_multi_subunit_repeal(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Resolve explicit section-level rows that name child subsection text targets."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = _SOURCE_CARRIED_MULTI_SUBUNIT_REPEAL_RE.match(text)
    if match is None:
        return None
    source_section = _clean_num(match.group("section"))
    target_section = _clean_num(_addr_field(target, "section") or "")
    if not source_section or source_section != target_section:
        return None
    labels = tuple(_clean_num(label) for label in re.findall(r"\(([0-9A-Za-z]+)\)", match.group("labels")))
    labels = tuple(label for label in labels if label)
    if len(labels) < 2:
        return None
    original = " ".join(match.group("original").split()).strip()
    if not original:
        return None
    label_part = "_".join(labels)
    return {
        "original": f"TEXT_IN_CHILDREN_subsection_{label_part}{US}{original}",
        "replacement": "",
        "source_section_label": source_section,
        "source_child_labels": ",".join(labels),
        "rule_id": "uk_effect_source_carried_multi_subunit_repeal_text_patch",
    }


def _fragment_substitution_amendment_inserted_text_substitution(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Resolve source rows that amend text inserted by a targeted amendment instruction."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = _SOURCE_AMENDMENT_INSERTED_TEXT_SUBSTITUTION_RE.match(text)
    if match is None:
        return None
    if _addr_container(target) != "schedule":
        return None
    target_paragraph, _, target_items = _schedule_target_levels(target)
    source_paragraph = _clean_num(match.group("paragraph"))
    source_item = _clean_num(match.group("item"))
    if not source_paragraph or _clean_num(target_paragraph or "") != source_paragraph:
        return None
    if not source_item or not target_items or _clean_num(target_items[-1]) != source_item:
        return None
    replacement = " ".join(match.group("replacement").split()).strip()
    if not replacement:
        return None
    return {
        "original": "TEXT_AFTER_AMENDMENT_INSERT_TO_END",
        "replacement": replacement,
        "source_paragraph_label": source_paragraph,
        "source_item_label": source_item,
        "rule_id": "uk_effect_amendment_inserted_text_substitution_text_patch",
    }


def _amendment_program_inserted_parent_structural_insert(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Identify structural inserts into a prior amendment instruction's inserted payload."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text or _addr_container(target) != "schedule":
        return None
    match = _SOURCE_AMENDMENT_INSERTED_PARENT_STRUCTURAL_INSERT_RE.match(text)
    if match is None:
        return None
    _target_paragraph, target_subparagraph, target_items = _schedule_target_levels(target)
    source_subparagraph = _clean_num(match.group("subparagraph"))
    source_item = _clean_num(match.group("item"))
    if not source_subparagraph or _clean_num(target_subparagraph or "") != source_subparagraph:
        return None
    if not source_item or not target_items or _clean_num(target_items[-1]) != source_item:
        return None
    source_label = lambda value: str(value or "").strip().strip("()").lower().strip(".")
    return {
        "source_subparagraph_label": source_subparagraph,
        "source_item_label": source_item,
        "inserted_parent_label": source_label(match.group("inserted_parent")),
        "direction": str(match.group("direction") or "").lower(),
        "anchor_label": source_label(match.group("anchor")),
        "inserted_label": source_label(match.group("inserted_label")),
        "inserted_text_preview": " ".join(str(match.group("inserted_text") or "").split())[:240],
    }


def reject_amendment_program_inserted_parent_structural_insert(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    detail = (
        _amendment_program_inserted_parent_structural_insert(
            extracted_text=extracted_text,
            target=target,
        )
        if extracted_text and curr_action == "insert"
        else None
    )
    if detail is None:
        return False

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
            "target_ref": target_ref,
            "target": str(target),
            **detail,
        },
    )
    return True

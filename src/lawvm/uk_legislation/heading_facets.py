"""UK heading-facet and crossheading source-text helpers.

These helpers classify affected-provision strings and extract explicit text
patch fragments. They do not resolve targets against live state or mutate IR.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.uk_legislation.addressing import _addr_leaf_kind, _addr_leaf_label, _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _source_text_before_extracted_child,
    _unique_source_ancestor_chain_by_tag_text,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


def _is_heading_only_ref(ref: str) -> bool:
    ref_clean = ref.strip().lower()
    if "cross-heading" in ref_clean or "cross heading" in ref_clean or "crossheading" in ref_clean:
        return False
    return ref_clean.endswith(" heading") or ref_clean.endswith(" title") or ref_clean.endswith(" sidenote")


def _heading_facet_carrier_for_target(
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
        and str(parent.attrs.get("source_rule_id") or "") == "uk_parse_subordinate_pgroup_heading_carrier"
    ):
        return parent
    if allow_crossheading_parent and parent_kind == "crossheading":
        return parent if structural_children and structural_children[0] is node else None
    return None


def _expand_heading_facet_section_range_ref(ref: str) -> list[str]:
    """Expand explicit section-title/heading ranges into heading facet refs."""
    ref_clean = " ".join(str(ref or "").split()).strip()
    if not ref_clean:
        return []
    if "cross-heading" in ref_clean.lower() or "cross heading" in ref_clean.lower():
        return []
    match = re.fullmatch(
        r"(?P<prefix>s\.|ss\.|section|sections)\s+"
        r"(?P<start>\d+[A-Z]?)\s*(?:-|to)\s*(?P<end>\d+[A-Z]?)\s+"
        r"(?P<facet>heading|headings|title|titles|sidenote|sidenotes)",
        ref_clean,
        flags=re.I,
    )
    if match is None:
        return []
    start_label = match.group("start")
    end_label = match.group("end")
    start_num_match = re.fullmatch(r"(\d+)([A-Z]?)", start_label, flags=re.I)
    end_num_match = re.fullmatch(r"(\d+)([A-Z]?)", end_label, flags=re.I)
    if start_num_match is None or end_num_match is None:
        return []
    start_num = int(start_num_match.group(1))
    end_num = int(end_num_match.group(1))
    if end_num < start_num or end_num - start_num >= 100:
        return []

    start_suffix = start_num_match.group(2).upper()
    end_suffix = end_num_match.group(2).upper()
    labels = [str(value) for value in range(start_num, end_num + 1)]
    if end_suffix:
        if end_num == start_num:
            suffix_start = start_suffix or "A"
            if ord(end_suffix) < ord(suffix_start):
                return []
            labels = [f"{start_num}{chr(code)}" for code in range(ord(suffix_start), ord(end_suffix) + 1)]
        else:
            labels.extend(f"{end_num}{chr(code)}" for code in range(ord("A"), ord(end_suffix) + 1))
    facet = match.group("facet").lower()
    singular_facet = "sidenote" if facet.startswith("sidenote") else "heading" if facet.startswith("heading") else "title"
    return [f"s. {label} {singular_facet}" for label in labels]


def _mixed_heading_structural_insert_ref(ref: str, *, action: str) -> str:
    """Return the structural component of ``X and heading`` insert targets.

    UK effects sometimes report an inserted structural payload plus its heading
    carrier as one affected-provision string, e.g. ``s. 61(2A)(2B) and
    heading``. The heading suffix is not a body target, but it also must not
    block the source-owned inserted children.
    """
    if action != "insert":
        return ""
    ref_clean = " ".join(str(ref or "").split()).strip()
    if not re.search(r"\s+and\s+heading\s*$", ref_clean, flags=re.I):
        return ""
    if "cross-heading" in ref_clean.lower() or "cross heading" in ref_clean.lower():
        return ""
    structural_ref = re.sub(r"\s+and\s+heading\s*$", "", ref_clean, flags=re.I).strip()
    has_child_label = "(" in structural_ref and ")" in structural_ref
    has_explicit_leaf_label = bool(
        re.search(
            r"\b(?:s\.?|section|para\.?|paragraph)\s+\d+[A-Z]?\b",
            structural_ref,
            flags=re.I,
        )
    )
    has_range = bool(re.search(r"\b(?:to|-)\b", structural_ref, flags=re.I))
    if not (has_child_label or (has_explicit_leaf_label and not has_range)):
        return ""
    return structural_ref


def _heading_facet_append_fragment(extracted_text: Optional[str]) -> Optional[dict[str, Any]]:
    for fragment in parse_fragment_substitution(extracted_text or ""):
        if str(fragment.get("original") or "") == "TEXT_FROM__TO_END":
            replacement = str(fragment.get("replacement") or "")
            if replacement:
                return fragment
    text = " ".join((extracted_text or "").split()).strip()
    match = re.search(
        r"\bat\s+end,?\s+insert\s+[“\"'‘](?P<replacement>.*?)[”\"'’]",
        text,
        flags=re.I | re.S,
    )
    if match is not None:
        replacement = match.group("replacement").strip()
        if replacement:
            return {
                "original": "TEXT_FROM__TO_END",
                "replacement": replacement,
                "rule_id": "uk_effect_heading_facet_at_end_insert_text_patch",
            }
    return None


def _heading_facet_after_anchor_insert_fragment(extracted_text: Optional[str]) -> Optional[dict[str, Any]]:
    """Return a bounded heading/title insertion after an explicit quoted anchor."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = re.search(
        r"\bafter\s+[“\"'‘](?P<anchor>.*?)[”\"'’],?\s+"
        r"(?:there\s+(?:is|are|shall\s+be)\s+inserted|insert)"
        r"(?:\s+(?:the\s+)?words?)?\s+[“\"'‘](?P<inserted>.*?)[”\"'’]",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return None
    anchor = match.group("anchor").strip()
    inserted = match.group("inserted").strip()
    if not anchor or not inserted:
        return None
    joiner = "" if anchor.endswith((" ", "\t", "\n", "\r")) or inserted.startswith((" ", ",", ".", ";", ":", ")")) else " "
    return {
        "original": anchor,
        "replacement": f"{anchor}{joiner}{inserted}",
        "rule_id": "uk_effect_heading_facet_after_anchor_insert_text_patch",
    }


def _heading_facet_insert_fragment(extracted_text: Optional[str]) -> Optional[dict[str, Any]]:
    """Return supported heading/title/sidenote insertion fragments."""
    append_fragment = _heading_facet_append_fragment(extracted_text)
    if append_fragment is not None:
        return append_fragment
    after_anchor_fragment = _heading_facet_after_anchor_insert_fragment(extracted_text)
    if after_anchor_fragment is not None:
        return after_anchor_fragment
    supported_parser_rules = {
        "uk_effect_beginning_text_insertion_patch",
        "uk_effect_preposed_beginning_text_insertion_patch",
        "uk_effect_for_insert_text_insertion_patch",
    }
    for fragment in parse_fragment_substitution(extracted_text or ""):
        if str(fragment.get("rule_id") or "") in supported_parser_rules:
            original = str(fragment.get("original") or "")
            replacement = str(fragment.get("replacement") or "")
            if original and replacement:
                return fragment
    return None


def _heading_facet_full_replacement_fragment(extracted_text: Optional[str]) -> Optional[dict[str, Any]]:
    """Return an explicit full heading/title/sidenote replacement fragment."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    parenthetical_match = re.search(
        r"\b(?:heading|title|sidenote)\s+to\s+which\s+becomes\s+"
        r"(?:[“\"'‘](?P<quoted>.*?)[”\"'’]|(?P<bare>[^).;]+))",
        text,
        flags=re.I | re.S,
    )
    if parenthetical_match is not None:
        replacement = (
            parenthetical_match.group("quoted")
            if parenthetical_match.group("quoted") is not None
            else parenthetical_match.group("bare")
        )
        replacement = " ".join(str(replacement or "").split()).strip(" “”\"'‘’")
        if replacement:
            return {
                "original": "TEXT_ALL",
                "replacement": replacement,
                "rule_id": "uk_effect_heading_facet_full_replacement_text_patch",
            }
    match = re.search(
        r"\b(?:the\s+)?(?:italic\s+)?(?:section\s+)?(?:heading|title|sidenote)"
        r"(?:\s+before\s+(?:(?:paragraph|section|article)\s+[0-9A-Za-z().]+|that\s+paragraph))?"
        r"(?:\s+(?:to\s+the\s+section|of\s+(?:the\s+)?(?:section|part|chapter|schedule|article|rule|regulation)\s+[0-9A-Za-z]+"
        r"(?:\s+of\s+(?:the\s+)?(?:[0-9]{4}\s+)?Act)?))?"
        r"(?:\s+to\s+which)?"
        r"(?:\s+accordingly)?\s+becomes\s+(?P<replacement>.+)$",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        match = re.search(
            r"\bfor\s+the\s+(?:(?:section|part|chapter|schedule|article|rule|regulation)\s+)?"
            r"(?:heading|title|sidenote)"
            r"(?:\s+of\s+(?:the\s+)?(?:section|part|chapter|schedule|article|rule|regulation)"
            r"\s+[0-9A-Za-z]+)?"
            r"(?:\s+to\s+(?:the|that)\s+section)?"
            r"\s+substitute\s*[—–-]?\s*(?P<replacement>.+)$",
            text,
            flags=re.I | re.S,
        )
    if match is None:
        match = re.search(
            r"\b(?:before|after)\s+"
            r"(?:(?:section|paragraph|article|rule|regulation)\s+[0-9A-Za-z().]+|that\s+paragraph)\s+"
            r"insert\s+(?:the\s+)?(?:italic\s+)?(?:heading|title|sidenote)\s+"
            r"(?P<replacement>[“\"'‘].*?[”\"'’]|[^.;]+)",
            text,
            flags=re.I | re.S,
        )
    if match is None:
        return None
    replacement = match.group("replacement").strip()
    replacement = replacement.strip(" “”\"'‘’")
    replacement = re.sub(r"^(?:\.\s*)+", "", replacement).strip(" “”\"'‘’")
    replacement = re.sub(r"(?:\s*\.)+$", "", replacement).strip(" “”\"'‘’")
    if not replacement:
        return None
    return {
        "original": "TEXT_ALL",
        "replacement": replacement,
        "rule_id": "uk_effect_heading_facet_full_replacement_text_patch",
    }


def _heading_facet_source_parent_full_replacement_fragment(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
) -> Optional[dict[str, Any]]:
    """Return a heading replacement carried by the parent source instruction."""
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        ancestors = _unique_source_ancestor_chain_by_tag_text(source_root, extracted_el)
    for ancestor in ancestors:
        lead_text = _source_text_before_extracted_child(ancestor, extracted_el)
        fragment = _heading_facet_full_replacement_fragment(lead_text)
        if fragment is None:
            continue
        return {
            **fragment,
            "rule_id": "uk_effect_heading_facet_source_parent_full_replacement_text_patch",
            "source_parent_id": str(ancestor.get("id") or ancestor.get("eId") or ""),
        }
    return None


def _is_heading_facet_word_patch_supported(
    effect_type: str,
    extracted_text: Optional[str] = None,
    *,
    extracted_el: Optional[ET.Element] = None,
    source_root: Optional[ET.Element] = None,
) -> bool:
    """Return whether a UK heading-facet effect can carry an explicit text patch."""
    normalized = " ".join((effect_type or "").lower().split())
    if normalized in {"substituted", "replaced", "inserted"}:
        return (
            _heading_facet_full_replacement_fragment(extracted_text) is not None
            or _heading_facet_source_parent_full_replacement_fragment(
                extracted_el=extracted_el,
                source_root=source_root,
            )
            is not None
        )
    if normalized in {
        "words substituted",
        "word substituted",
        "words omitted",
        "word omitted",
        "words repealed",
        "word repealed",
    }:
        return True
    if normalized in {"words inserted", "word inserted"}:
        return _heading_facet_insert_fragment(extracted_text) is not None
    return False


def _is_direct_section_paragraph_ref(ref: str) -> bool:
    ref_clean = " ".join(str(ref or "").strip().lower().split())
    return bool(re.search(r"\b(?:s|section)\.?\s+\d+[a-z]?\s*\(\s*[a-z]\s*\)", ref_clean))


def _is_schedule_part_abbreviation_ref(ref: str) -> bool:
    ref_clean = " ".join(str(ref or "").strip().lower().split())
    return bool(re.search(r"\bsch(?:edule)?\.?\s+[0-9a-z]+\s+pt\s+[0-9ivxlcdm]+[a-z]?\b", ref_clean))


def _is_crossheading_ref(ref: str) -> bool:
    ref_clean = str(ref or "").strip().lower()
    return "cross-heading" in ref_clean or "cross heading" in ref_clean or "crossheading" in ref_clean


def _is_schedule_note_ref(ref: str) -> bool:
    ref_clean = " ".join(str(ref or "").strip().lower().split())
    return bool(re.search(r"\bsch(?:edule)?\.?\s+[0-9a-z]+(?:\s+|\s+pt\.\s+[0-9a-z]+\s+)note(?:\s+\d+)?\b", ref_clean))


_CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE = "uk_effect_crossheading_before_anchor_replacement_text_patch"
_CROSSHEADING_TARGET_REPLACEMENT_RULE = "uk_effect_crossheading_target_replacement_text_patch"
_CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE = "uk_effect_crossheading_before_anchor_text_patch"
_CROSSHEADING_SOURCE_PARENT_REFERENCE_SUBSTITUTION_RULE = (
    "uk_effect_crossheading_source_parent_reference_substitution_text_patch"
)
_CROSSHEADING_AND_STRUCTURAL_REPLACEMENT_SPLIT_RULE = (
    "uk_effect_crossheading_and_structural_replacement_split_lowered"
)
_CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE = "uk_effect_crossheading_and_structural_repeal_lowered"
_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_RESOLVED_RULE_ID = (
    "uk_replay_crossheading_and_structural_repeal_resolved"
)
_UK_REPLAY_CROSSHEADING_AND_STRUCTURAL_REPEAL_UNRESOLVED_RULE_ID = (
    "uk_replay_crossheading_and_structural_repeal_unresolved"
)


def _crossheading_before_anchor_replacement_text(extracted_text: Optional[str]) -> Optional[str]:
    """Return explicit replacement text for ``heading before paragraph X`` cross-heading claims."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bfor\s+(?:the\s+)?(?:italic\s+)?(?:heading|cross-heading|cross heading)\s+"
        r"(?:before|preceding)\s+(?:paragraphs?|sections?|articles?)\s+[0-9A-Za-z().]+"
        r"(?:\s*(?:to|-|–|—)\s*[0-9A-Za-z().]+)?\s+"
        r"substitute\s*[—-]?\s+(.+?)\s*$",
        text,
        re.I,
    )
    if match is None:
        return None
    replacement = match.group(1).strip()
    replacement = re.sub(r"^[\s“\"'‘.]+", "", replacement)
    replacement = re.sub(r"[\s”\"'’.]+$", "", replacement)
    return replacement or None


def _crossheading_target_replacement_text(extracted_text: Optional[str]) -> Optional[str]:
    """Return explicit replacement text for a source-owned cross-heading target."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bfor\s+(?:the\s+)?(?:italic\s+)?(?:heading|cross-heading|cross heading)"
        r"\s+substitute\s*[—–-]?\s*(?P<replacement>.+?)\s*$",
        text,
        flags=re.I | re.S,
    )
    if match is None:
        return None
    replacement = match.group("replacement").strip()
    replacement = replacement.strip(" “”\"'‘’")
    replacement = re.sub(r"^(?:\.\s*)+", "", replacement).strip(" “”\"'‘’")
    replacement = re.sub(r"(?:\s*\.)+$", "", replacement).strip(" “”\"'‘’")
    return replacement or None


def _crossheading_before_anchor_text_patch_fragment(extracted_text: Optional[str]) -> Optional[dict[str, str]]:
    """Return a quoted text patch for ``cross-heading before section X`` claims."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    if not re.search(
        r"\b(?:heading|cross-heading|cross heading)\s+before\s+(?:paragraph|section|article)\s+[0-9A-Za-z().]+",
        text,
        flags=re.I,
    ):
        return None
    fragments = parse_fragment_substitution(text)
    if len(fragments) != 1:
        return None
    fragment = dict(fragments[0])
    original = str(fragment.get("original") or "").strip()
    replacement = str(fragment.get("replacement") or "").strip()
    if not original or replacement == "":
        return None
    return {
        "original": original,
        "replacement": replacement,
        "rule_id": _CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE,
    }


def _crossheading_metadata_target_deictic_text_patch_fragment(
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Return a crossheading text patch for ``before that paragraph`` shapes.

    The deictic anchor is admissible only because the effect metadata already
    targets the corresponding cross-heading facet.
    """
    if _addr_leaf_kind(target) not in {"paragraph", "section", "article"}:
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\b(?:heading|cross-heading|cross heading)\s+before\s+that\s+"
        r"(?:paragraph|section|article)\b(?P<tail>.*)$",
        text,
        flags=re.I,
    )
    if match is None:
        return None
    fragments = parse_fragment_substitution(match.group("tail"))
    if len(fragments) != 1:
        return None
    fragment = dict(fragments[0])
    original = str(fragment.get("original") or "").strip()
    replacement = str(fragment.get("replacement") or "").strip()
    if not original or replacement == "":
        return None
    return {
        "original": original,
        "replacement": replacement,
        "rule_id": _CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE,
        "source_context": "metadata_target_deictic_anchor",
    }


def _crossheading_and_structural_repeal_selector(
    *,
    affected_ref: str,
    effect_type: str,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, Any]]:
    """Return an explicit selector for ``paragraph X and the heading above it`` repeals."""
    if not _is_crossheading_ref(affected_ref):
        return None
    effect_type_norm = " ".join((effect_type or "").lower().split())
    if effect_type_norm not in {"repealed", "omitted", "revoked", "repealed in part"}:
        return None
    target_kind = _addr_leaf_kind(target)
    target_label = _clean_num(_addr_leaf_label(target) or "")
    if target_kind not in {"section", "article", "paragraph"} or not target_label:
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    noun_by_kind = {
        "section": "section",
        "article": "article",
        "paragraph": "paragraph",
    }
    noun = noun_by_kind[target_kind]
    label_rx = re.escape(target_label)
    if not re.search(
        rf"\b{noun}\s+{label_rx}\b"
        rf"(?:(?!\b(?:section|article|paragraph)\s+[0-9A-Za-z]+).){{0,320}}?"
        rf"\band\s+the\s+(?:italic\s+)?(?:heading|cross-heading|cross heading)\s+above\s+it\b"
        rf"(?:(?!\.).){{0,240}}?\b(?:is|are)\s+(?:repealed|omitted|revoked)\b",
        text,
        flags=re.I,
    ):
        return None
    return {
        "rule_id": _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE,
        "selector_mode": "structural_with_heading_above_repeal",
        "heading_anchor_direction": "above",
        "target_ref": affected_ref,
        "structural_target": str(target),
        "source_target_kind": target_kind,
        "source_target_label": target_label,
    }

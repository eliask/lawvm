from __future__ import annotations

import re
from typing import Optional

from lxml import etree as ET

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_field, _addr_leaf_kind
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag, _text_content


UK_SOURCE_CARRIED_DEICTIC_CHILD_TAIL_REPEAL_RULE_ID = (
    "uk_effect_source_carried_deictic_child_tail_repeal_text_patch"
)


_SOURCE_CARRIED_CHILD_TAIL_REPEAL_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+subsection\s+\((?P<subsection>[0-9A-Za-z]+)\),?\s+"
    r"the\s+words\s+following\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
    r"are\s+repealed\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_CHILD_TAIL_OMIT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+subsection\s+\((?P<subsection>[0-9A-Za-z]+)\),?\s+"
    r"(?:omit|repeal)\s+the\s+words\s+(?:following|after)\s+"
    r"paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_TARGET_CHILD_TAIL_OMIT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"(?:omit|repeal)\s+the\s+words\s+(?:following|after)\s+"
    r"paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_DEICTIC_CHILD_TAIL_OMIT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"(?:omit|repeal)\s+the\s+words\s+(?:following|after)\s+"
    r"that\s+paragraph\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_PREVIOUS_SOURCE_SIBLING_PARAGRAPH_TARGET_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"(?:in\s+)?paragraph\s+\((?P<label>[0-9A-Za-z]+)\)(?=\W|$)",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_CHILD_LIST_TAIL_OMIT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"(?:in\s+section\s+(?P<section>[0-9A-Za-z]+)\s*"
    r"\(\s*(?P<subsection>[0-9A-Za-z]+)\s*\)[^,]*,?\s+)?"
    r"(?:omit|repeal)\s+the\s+words\s+(?:following|after)\s+the\s+paragraphs\s*"
    r";?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_SUBPARAGRAPH_TAIL_REPEAL_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+paragraph\s+\((?P<paragraph>[0-9A-Za-z]+)\),?\s+"
    r"the\s+words\s+following\s+sub-?paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
    r"are\s+repealed\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_CHILD_TAIL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+subsection\s+\((?P<subsection>[0-9A-Za-z]+)\),?\s+"
    r"for\s+the\s+words\s+(?:following|after)\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)"
    r"(?:\s+to\s+the\s+end(?:\s+of\s+the\s+subsection)?)?\s+"
    r"substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_TARGET_CHILD_TAIL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"for\s+the\s+words\s+(?:following|after)\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)"
    r"(?:\s+to\s+the\s+end(?:\s+of\s+the\s+subsection)?)?\s+"
    r"substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_CARRIED_BETWEEN_PARAGRAPHS_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"for\s+the\s+[“\"'‘](?P<original>.*?)[”\"'’]\s+"
    r"between\s+those\s+paragraphs\s+"
    r"substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_PREVIOUS_SIBLING_PARAGRAPHS_RE = re.compile(
    r"\bin\s+each\s+of\s+paragraphs\s+\((?P<first>[0-9A-Za-z]+)\)\s+"
    r"and\s+\((?P<second>[0-9A-Za-z]+)\)",
    flags=re.I | re.S,
)


def _previous_structural_source_sibling(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
) -> Optional[ET._Element]:
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors or extracted_el is None:
        return None
    extracted_id = extracted_el.get("id")
    previous: Optional[ET._Element] = None
    for child in ancestors[0]:
        if child is extracted_el or (extracted_id and child.get("id") == extracted_id):
            return previous
        if _tag(child) in {"P1", "P2", "P3", "P4", "P5", "P6", "P7", "Para"}:
            previous = child
    return None


def _previous_source_sibling_paragraph_target(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
) -> tuple[str, str]:
    previous = _previous_structural_source_sibling(
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if previous is None:
        return ("", "")
    text = " ".join(_text_content(previous).split()).strip()
    if not text:
        return ("", "")
    match = _PREVIOUS_SOURCE_SIBLING_PARAGRAPH_TARGET_RE.match(text)
    if match is None:
        return ("", "")
    return (_clean_num(match.group("label")), text)


def _previous_source_sibling_paragraph_pair(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
) -> tuple[str, str, str]:
    previous = _previous_structural_source_sibling(
        extracted_el=extracted_el,
        source_root=source_root,
    )
    if previous is None:
        return ("", "", "")
    text = " ".join(_text_content(previous).split()).strip()
    if not text:
        return ("", "", "")
    match = _SOURCE_PREVIOUS_SIBLING_PARAGRAPHS_RE.search(text)
    if match is None:
        return ("", "", "")
    return (_clean_num(match.group("first")), _clean_num(match.group("second")), text)


def _fragment_substitution_source_carried_child_tail_repeal(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
    extracted_el: Optional[ET._Element] = None,
    source_root: Optional[ET._Element] = None,
) -> Optional[dict[str, str]]:
    """Resolve explicit "words following paragraph (x)" tail repeals.

    The source carries both the subsection and child anchor.  Lowering only
    succeeds when the feed target already names that exact subsection; replay
    then owns the source XML collapse separately by trimming only target text.
    """
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = _SOURCE_CARRIED_CHILD_TAIL_REPEAL_RE.match(text)
    if match is None:
        match = _SOURCE_CARRIED_CHILD_TAIL_OMIT_RE.match(text)
    if match is None:
        deictic_match = _SOURCE_CARRIED_DEICTIC_CHILD_TAIL_OMIT_RE.match(text)
        if deictic_match is not None:
            if _addr_leaf_kind(target) != "subsection":
                return None
            target_subsection = _clean_num(_addr_field(target, "subsection") or "")
            if not target_subsection:
                return None
            anchor_label, antecedent_text = _previous_source_sibling_paragraph_target(
                extracted_el=extracted_el,
                source_root=source_root,
            )
            if not anchor_label:
                return None
            return {
                "original": f"TEXT_AFTER_CHILD_TAIL_paragraph_{anchor_label}",
                "replacement": "",
                "source_subsection_label": "",
                "target_supplied_subsection_context": "true",
                "source_anchor_child_label": anchor_label,
                "source_deictic_antecedent": "previous_source_sibling",
                "source_deictic_antecedent_text": antecedent_text,
                "rule_id": UK_SOURCE_CARRIED_DEICTIC_CHILD_TAIL_REPEAL_RULE_ID,
            }
        target_match = _SOURCE_CARRIED_TARGET_CHILD_TAIL_OMIT_RE.match(text)
        if target_match is not None:
            if _addr_leaf_kind(target) not in {"subsection", "paragraph", "subparagraph"}:
                return None
            target_subsection = _clean_num(_addr_field(target, "subsection") or "")
            anchor_label = _clean_num(target_match.group("label"))
            if _addr_leaf_kind(target) == "subsection" and not target_subsection:
                return None
            if not anchor_label:
                return None
            anchor_kind = "paragraph"
            if _addr_container(target) == "schedule" and _addr_leaf_kind(target) == "subparagraph":
                anchor_kind = "item"
            return {
                "original": f"TEXT_AFTER_CHILD_TAIL_{anchor_kind}_{anchor_label}",
                "replacement": "",
                "source_subsection_label": "",
                "target_supplied_subsection_context": "true",
                "source_anchor_child_kind": anchor_kind,
                "source_anchor_child_label": anchor_label,
                "rule_id": "uk_effect_source_carried_child_tail_repeal_text_patch",
            }
        subparagraph_match = _SOURCE_CARRIED_SUBPARAGRAPH_TAIL_REPEAL_RE.match(text)
        if subparagraph_match is None:
            return None
        source_paragraph = _clean_num(subparagraph_match.group("paragraph"))
        target_paragraph = _clean_num(_addr_field(target, "paragraph") or "")
        if (
            _addr_leaf_kind(target) != "paragraph"
            or not source_paragraph
            or source_paragraph != target_paragraph
        ):
            return None
        anchor_label = _clean_num(subparagraph_match.group("label"))
        if not anchor_label:
            return None
        return {
            "original": f"TEXT_AFTER_CHILD_TAIL_subparagraph_{anchor_label}",
            "replacement": "",
            "source_parent_kind": "paragraph",
            "source_parent_label": source_paragraph,
            "source_anchor_child_kind": "subparagraph",
            "source_anchor_child_label": anchor_label,
            "rule_id": "uk_effect_source_carried_subparagraph_tail_repeal_text_patch",
        }
    source_subsection = _clean_num(match.group("subsection"))
    target_subsection = _clean_num(_addr_field(target, "subsection") or "")
    if not source_subsection or source_subsection != target_subsection:
        return None
    anchor_label = _clean_num(match.group("label"))
    if not anchor_label:
        return None
    return {
        "original": f"TEXT_AFTER_CHILD_TAIL_paragraph_{anchor_label}",
        "replacement": "",
        "source_subsection_label": source_subsection,
        "source_anchor_child_label": anchor_label,
        "rule_id": "uk_effect_source_carried_child_tail_repeal_text_patch",
    }


def _fragment_substitution_source_carried_child_list_tail_repeal(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Resolve explicit "words following the paragraphs" tail repeals."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = _SOURCE_CARRIED_CHILD_LIST_TAIL_OMIT_RE.match(text)
    if match is None:
        return None
    if _addr_leaf_kind(target) != "subsection":
        return None
    source_section = _clean_num(match.group("section") or "")
    source_subsection = _clean_num(match.group("subsection") or "")
    target_section = _clean_num(_addr_field(target, "section") or "")
    target_subsection = _clean_num(_addr_field(target, "subsection") or "")
    if source_section and source_section != target_section:
        return None
    if source_subsection and source_subsection != target_subsection:
        return None
    return {
        "original": "TEXT_AFTER_CHILD_LIST_TAIL_paragraph",
        "replacement": "",
        "source_anchor_child_kind": "paragraph",
        "rule_id": "uk_effect_source_carried_child_list_tail_repeal_text_patch",
    }


def _fragment_substitution_source_carried_child_tail_substitution(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Resolve explicit "words after paragraph (x) substitute" tail rewrites."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    match = _SOURCE_CARRIED_CHILD_TAIL_SUBSTITUTION_RE.match(text)
    source_subsection = ""
    if match is None:
        match = _SOURCE_CARRIED_TARGET_CHILD_TAIL_SUBSTITUTION_RE.match(text)
        if match is None:
            return None
        if _addr_leaf_kind(target) != "subsection":
            return None
    else:
        source_subsection = _clean_num(match.group("subsection"))
    target_subsection = _clean_num(_addr_field(target, "subsection") or "")
    if not target_subsection:
        return None
    if source_subsection and source_subsection != target_subsection:
        return None
    anchor_label = _clean_num(match.group("label"))
    replacement = " ".join(match.group("replacement").split()).strip()
    if not anchor_label or not replacement:
        return None
    return {
        "original": f"TEXT_AFTER_CHILD_TAIL_paragraph_{anchor_label}",
        "replacement": replacement,
        "source_subsection_label": source_subsection or target_subsection,
        "source_anchor_child_label": anchor_label,
        "rule_id": "uk_effect_source_carried_child_tail_substitution_text_patch",
    }


def _fragment_substitution_source_carried_between_paragraphs_substitution(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
    extracted_el: Optional[ET._Element] = None,
    source_root: Optional[ET._Element] = None,
) -> Optional[dict[str, str]]:
    """Resolve "the word between those paragraphs" from source sibling context.

    The deictic phrase is only accepted when the immediately previous source
    sibling names exactly the paragraph pair.  The connector belongs to the end
    of the first paragraph, so lowering refines the effect-feed subsection
    target to that source-named child instead of replacing the parent text.
    """
    text = " ".join((extracted_text or "").split()).strip()
    if not text or _addr_leaf_kind(target) != "subsection":
        return None
    match = _SOURCE_CARRIED_BETWEEN_PARAGRAPHS_SUBSTITUTION_RE.match(text)
    if match is None:
        return None
    first_label, second_label, antecedent_text = _previous_source_sibling_paragraph_pair(
        extracted_el=extracted_el,
        source_root=source_root,
    )
    original = " ".join(match.group("original").split()).strip()
    replacement = " ".join(match.group("replacement").split()).strip()
    if not first_label or not second_label or not original or not replacement:
        return None
    return {
        "original": original,
        "replacement": replacement,
        "target_refinement": "source_carried_child_text",
        "target_refinement_kind": "paragraph",
        "target_refinement_label": first_label,
        "source_between_child_kind": "paragraph",
        "source_between_child_labels": f"{first_label},{second_label}",
        "source_deictic_antecedent": "previous_source_sibling",
        "source_deictic_antecedent_text": antecedent_text,
        "rule_id": "uk_effect_source_carried_between_paragraphs_substitution_text_patch",
    }

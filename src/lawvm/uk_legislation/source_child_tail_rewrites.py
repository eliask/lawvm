from __future__ import annotations

import re
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_field, _addr_leaf_kind
from lawvm.uk_legislation.uk_grafter import _clean_num


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
    r"for\s+the\s+words\s+after\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)\s+"
    r"substitute\s+[“\"'‘](?P<replacement>.*?)[”\"'’]\s*;?\s*(?:and)?\s*\.?\s*$",
    flags=re.I | re.S,
)


def _fragment_substitution_source_carried_child_tail_repeal(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
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
    if match is None:
        return None
    source_subsection = _clean_num(match.group("subsection"))
    target_subsection = _clean_num(_addr_field(target, "subsection") or "")
    if not source_subsection or source_subsection != target_subsection:
        return None
    anchor_label = _clean_num(match.group("label"))
    replacement = " ".join(match.group("replacement").split()).strip()
    if not anchor_label or not replacement:
        return None
    return {
        "original": f"TEXT_AFTER_CHILD_TAIL_paragraph_{anchor_label}",
        "replacement": replacement,
        "source_subsection_label": source_subsection,
        "source_anchor_child_label": anchor_label,
        "rule_id": "uk_effect_source_carried_child_tail_substitution_text_patch",
    }

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import (
    _source_ancestor_chain,
    _source_text_before_extracted_child,
)
from lawvm.uk_legislation.source_fragment_context import _source_lead_text_before_subordinate_rows
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag


_SOURCE_DEFINITION_TERM_RE = re.compile(
    r"\bin\s+the\s+definition\s+of\s+[“\"'‘](?P<term>.*?)[”\"'’]",
    flags=re.I | re.S,
)
_SOURCE_SECTION_DEFINITION_CHILD_CONTEXT_RE = re.compile(
    r"\bin\s+section\s+(?P<section>[0-9A-Za-z]+)\b"
    r"(?:(?!\bin\s+section\b).){0,500}?"
    r"\bin\s+the\s+definition\s+of\s+[“\"'‘](?P<term>.*?)[”\"'’]"
    r"(?:(?!\bin\s+the\s+definition\b).){0,260}?"
    r"\bin\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)",
    flags=re.I | re.S,
)
_SOURCE_DEFINITION_CHILD_CONTEXT_RE = re.compile(
    r"\bin\s+the\s+definition\s+of\s+[“\"'‘](?P<term>.*?)[”\"'’]"
    r"(?:(?!\bin\s+the\s+definition\b).){0,260}?"
    r"\bin\s+paragraph\s+\((?P<label>[0-9A-Za-z]+)\)"
    r"(?:\((?P<sublabel>[0-9A-Za-z]+)\))?",
    flags=re.I | re.S,
)


def _source_definition_term_from_ancestors(ancestors: tuple[ET.Element, ...]) -> str:
    for ancestor in ancestors:
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            candidate_text = _instruction_text_before_amendment_container(ancestor)
        match = _SOURCE_DEFINITION_TERM_RE.search(candidate_text)
        if match is not None:
            return " ".join(match.group("term").split()).strip()
    return ""


def _source_definition_term_from_local_ancestor_context(
    ancestors: tuple[ET.Element, ...],
    *,
    start_index: int,
    extracted_el: Optional[ET.Element],
) -> str:
    """Find a definition term only in local instruction context for a child row.

    This deliberately avoids broad containers such as ``Pblock`` because they
    can contain unrelated sibling amendment paragraphs whose definition context
    must not be smuggled into the current row.
    """

    for ancestor in ancestors[start_index:]:
        if _tag(ancestor) not in {"P1para", "P2para", "P3para", "P4para", "P5para"}:
            continue
        candidate_text = _source_text_before_extracted_child(ancestor, extracted_el)
        if not candidate_text:
            candidate_text = _instruction_text_before_amendment_container(ancestor)
        match = _SOURCE_DEFINITION_TERM_RE.search(candidate_text)
        if match is not None:
            return " ".join(match.group("term").split()).strip()
    return ""


def _source_definition_child_context_for_direct_section_paragraph(
    *,
    target: LegalAddress,
    parent_context_text: str,
) -> tuple[str, str]:
    path = tuple(getattr(target, "path", ()) or ())
    if len(path) != 2:
        return "", ""
    section_kind, section_label = path[0]
    child_kind, child_label = path[1]
    if str(section_kind or "").lower() != "section" or str(child_kind or "").lower() != "paragraph":
        return "", ""
    match = _SOURCE_SECTION_DEFINITION_CHILD_CONTEXT_RE.search(parent_context_text)
    if match is None:
        return "", ""
    if _clean_num(match.group("section")) != _clean_num(str(section_label or "")):
        return "", ""
    label = _clean_num(match.group("label"))
    if label != _clean_num(str(child_label or "")):
        return "", ""
    term = " ".join(match.group("term").split()).strip()
    return term, label


def _source_definition_child_context_from_parent(parent_context_text: str) -> tuple[str, str, str]:
    match = _SOURCE_DEFINITION_CHILD_CONTEXT_RE.search(parent_context_text)
    if match is None:
        return "", "", ""
    term = " ".join(match.group("term").split()).strip()
    label = _clean_num(match.group("label"))
    sublabel = " ".join((match.group("sublabel") or "").split()).strip()
    return term, label, sublabel


def _source_row_names_explicit_target_context(row_text: str) -> bool:
    # Quoted payloads often contain legal references ("in section 2A") that are
    # inserted text, not a competing source target. Strip quoted segments before
    # deciding that the child row overrides parent definition context.
    text_without_quotes = re.sub(r"[“\"'‘][^”\"'’]{0,800}[”\"'’]", "", row_text)
    return bool(
        re.search(
            r"\bin\s+(?:section|schedule|subsection|paragraph|article|regulation)\b",
            text_without_quotes,
            flags=re.I,
        )
    )


def _scope_fragment_substitutions_to_source_definition_parent(
    *,
    fragments: list[dict[str, str]],
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
    target: LegalAddress,
) -> list[dict[str, str]]:
    """Scope generic child-row text patches when the source parent names a definition."""
    if not fragments:
        return fragments
    row_text = " ".join((extracted_text or "").split())
    if _source_row_names_explicit_target_context(row_text):
        return fragments
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    if not ancestors:
        return fragments
    parent = ancestors[0]
    parent_context_text = _source_text_before_extracted_child(parent, extracted_el)
    child_definition_term, child_definition_label = _source_definition_child_context_for_direct_section_paragraph(
        target=target,
        parent_context_text=parent_context_text,
    )
    child_definition_sublabel = ""
    if not child_definition_term:
        (
            child_definition_term,
            child_definition_label,
            child_definition_sublabel,
        ) = _source_definition_child_context_from_parent(parent_context_text)
    definition_match = _SOURCE_DEFINITION_TERM_RE.search(parent_context_text)
    definition_term = (
        " ".join(definition_match.group("term").split()).strip()
        if definition_match is not None
        else ""
    )
    if not definition_term:
        return fragments
    scoped: list[dict[str, str]] = []
    changed = False
    source_parent_id = str(parent.get("id") or "")
    if not source_parent_id:
        source_parent_id = next(
            (str(candidate.get("id")) for candidate in ancestors[1:] if candidate.get("id")),
            "",
        )
    for fragment in fragments:
        original = str(fragment.get("original") or "")
        replacement = str(fragment.get("replacement") or "")
        scoped_fragment = dict(fragment)
        omit_paragraph_match = re.fullmatch(r"TEXT_OMIT_PARAGRAPH_(?P<lbl>.+)", original)
        range_match = re.fullmatch(r"TEXT_FROM_(?P<start>.+)_TO_(?P<end>.+)", original)
        if omit_paragraph_match is not None:
            lbl = omit_paragraph_match.group("lbl").strip()
            if definition_term:
                scoped_fragment["original"] = (
                    f"TEXT_DEFINITION_CHILD_PARAGRAPH_{definition_term}{US}{lbl}"
                )
                scoped_fragment["source_parent_id"] = source_parent_id
                scoped_fragment["source_definition_term"] = definition_term
                scoped_fragment["source_child_label"] = lbl
                scoped_fragment["rule_id"] = "uk_effect_definition_child_repeal_text_patch"
                changed = True
        elif range_match is not None:
            start = range_match.group("start").strip()
            end = range_match.group("end").strip()
            if start and end and end != "END":
                scoped_fragment["original"] = (
                    f"TEXT_IN_DEFINITION_{definition_term}{US}FROM{US}{start}{US}TO{US}{end}"
                )
                scoped_fragment["source_parent_id"] = source_parent_id
                scoped_fragment["source_definition_term"] = definition_term
                scoped_fragment["source_unscoped_match_text"] = original
                scoped_fragment["rule_id"] = "uk_effect_source_parent_definition_range_text_patch"
                changed = True
        elif original and replacement.startswith(original):
            if child_definition_term and child_definition_label:
                scoped_fragment["original"] = (
                    f"TEXT_IN_DEFINITION_CHILD_PARAGRAPH_{child_definition_term}"
                    f"{US}{child_definition_label}{US}AFTER{US}{original}"
                )
                scoped_fragment["source_child_label"] = child_definition_label
                scoped_fragment["rule_id"] = (
                    "uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch"
                )
            else:
                scoped_fragment["original"] = f"TEXT_IN_DEFINITION_{definition_term}{US}AFTER{US}{original}"
                scoped_fragment["rule_id"] = "uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch"
            scoped_fragment["source_parent_id"] = source_parent_id
            scoped_fragment["source_definition_term"] = child_definition_term or definition_term
            scoped_fragment["source_unscoped_match_text"] = original
            changed = True
        elif (
            original
            and replacement
            and child_definition_term
            and child_definition_label
            and not original.startswith("TEXT_")
        ):
            scoped_fragment["original"] = (
                f"TEXT_IN_DEFINITION_CHILD_PARAGRAPH_{child_definition_term}"
                f"{US}{child_definition_label}{US}{original}"
            )
            scoped_fragment["source_parent_id"] = source_parent_id
            scoped_fragment["source_definition_term"] = child_definition_term
            scoped_fragment["source_child_label"] = child_definition_label
            if child_definition_sublabel:
                scoped_fragment["source_child_sublabel"] = child_definition_sublabel
            scoped_fragment["source_unscoped_match_text"] = original
            scoped_fragment["rule_id"] = (
                "uk_effect_source_parent_definition_child_substitution_text_patch"
            )
            changed = True
        scoped.append(scoped_fragment)
    return scoped if changed else fragments


def _source_definition_child_refined_target(
    *,
    target: LegalAddress,
    fragment: dict[str, str],
) -> Optional[LegalAddress]:
    if str(fragment.get("rule_id") or "") not in {
        "uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch",
        "uk_effect_source_carried_definition_child_at_end_insert_text_patch",
    }:
        return None
    path = tuple(getattr(target, "path", ()) or ())
    if len(path) < 2:
        return None
    if str(path[0][0] or "").lower() != "section" or str(path[1][0] or "").lower() != "paragraph":
        return None
    if _clean_num(str(path[1][1] or "")) != _clean_num(str(fragment.get("source_child_label") or "")):
        return None
    return LegalAddress(path=(path[0],), special=target.special)


def _source_definition_child_context_from_ancestors(
    ancestors: tuple[ET.Element, ...],
) -> tuple[str, str, str, str]:
    for ancestor in ancestors:
        candidate_text = _source_lead_text_before_subordinate_rows(ancestor)
        if not candidate_text:
            candidate_text = _instruction_text_before_amendment_container(ancestor)
        term, label, sublabel = _source_definition_child_context_from_parent(candidate_text)
        if term and label:
            return term, label, sublabel, str(ancestor.get("id") or "")
    return "", "", "", ""

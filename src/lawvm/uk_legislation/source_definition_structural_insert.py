from __future__ import annotations

import re
from lxml import etree as ET
from typing import Any, Optional

from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.source_definition_context import (
    _source_definition_term_from_local_ancestor_context,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag


UK_DEFINITION_CHILD_STRUCTURAL_SIBLING_INSERT_RULE_ID = (
    "uk_effect_definition_child_structural_sibling_insert_lowered"
)
UK_DEFINITION_CHILD_STRUCTURAL_INSERT_BEFORE_TAIL_CONNECTOR_RULE_ID = (
    "uk_effect_definition_child_structural_insert_before_tail_connector_lowered"
)
UK_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RULE_ID = (
    "uk_effect_definition_child_structural_substitution_lowered"
)

_AFTER_PARAGRAPH_DEFINITION_CHILD_INSERT_RE = re.compile(
    r"^\s*after\s+paragraph\s+\((?P<anchor>[a-z])\),?\s+"
    r"insert\s*[—–-]\s*(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_IN_DEFINITION_AFTER_PARAGRAPH_BEFORE_CONNECTOR_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+)?"
    r"(?:in\s+section\s+(?P<section>[0-9A-Za-z]+)\b.*?)?"
    r"\bin\s+the\s+definition\s+of\s+[“\"'‘](?P<term>[^”\"'’]+)[”\"'’],?\s+"
    r"after\s+paragraph\s+\((?P<anchor>[a-z])\)\s+"
    r"\(\s*but\s+before\s+the\s+[“\"'‘](?P<connector>and|or)[”\"'’]\s+"
    r"at\s+the\s+end\s+of\s+that\s+paragraph\s*\)\s+"
    r"insert\s*[—–-]\s*(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_IN_DEFINITION_AFTER_PARAGRAPH_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+)?"
    r"(?:in\s+section\s+(?P<section>[0-9A-Za-z]+)\b.*?)?"
    r"\bin\s+the\s+definition\s+of\s+[“\"'‘](?P<term>[^”\"'’]+)[”\"'’],?\s+"
    r"after\s+paragraph\s+\((?P<anchor>[a-z])\)\s+"
    r"insert\s*[—–-]\s*(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_IN_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+)?"
    r"(?:in\s+section\s+(?P<section>[0-9A-Za-z]+)\b.*?)?"
    r"\bin\s+subsection\s+\((?P<subsection>[0-9A-Za-z]+)\)\s*,?\s+"
    r"in\s+the\s+definition\s+of\s+[“\"'‘](?P<term>[^”\"'’]+)[”\"'’],?\s+"
    r"for\s+paragraph\s+\((?P<label>[a-z])\)\s+"
    r"\(\s*including\s+the\s+[“\"'‘](?P<connector>and|or)[”\"'’]\s+"
    r"at\s+the\s+end\s*\)\s+"
    r"substitute\s*[—–-]\s*(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_SECTION_TARGET_RE = re.compile(r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*$", flags=re.I)
_SECTION_SUBSECTION_TARGET_RE = re.compile(
    r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*\(\s*(?P<subsection>[0-9A-Za-z]+)\s*\)\s*$",
    flags=re.I,
)
_DEFINITION_CHILD_INSERT_PAYLOAD_RE = re.compile(
    r"(?:^|;\s*)(?P<label>[a-z][a-z0-9]*)\s+"
    r"(?P<text>.*?)(?=(?:;\s*[a-z][a-z0-9]*\s+)|;\s*\.?\s*$|\.?\s*$)",
    flags=re.I | re.S,
)
_ROMAN_CHILD_LABEL_RE = re.compile(
    r"(?:^|[—–;,]\s*|\band\s+)(?P<label>i|ii|iii|iv|v|vi|vii|viii|ix|x)\s+",
    flags=re.I,
)


def _next_single_letter_label(label: str) -> str:
    clean = _clean_num(label)
    if len(clean) != 1 or not clean.isalpha() or clean == "z":
        return ""
    return chr(ord(clean) + 1)


def _next_definition_child_label(label: str) -> str:
    clean = _clean_num(label)
    if len(clean) == 2 and clean[0].isalpha() and clean[1].isalpha() and clean[1] != "z":
        return f"{clean[0]}{chr(ord(clean[1]) + 1)}"
    return _next_single_letter_label(clean)


def _definition_child_insert_payloads(
    payload_text: str,
    *,
    anchor_label: str,
    allow_intercalated_after_anchor: bool = False,
) -> tuple[dict[str, str], ...]:
    payload = " ".join((payload_text or "").split()).strip()
    payload = re.sub(r"\s+\.\s*$", "", payload).strip()
    if not payload:
        return ()

    rows: list[dict[str, str]] = []
    expected_label = _next_single_letter_label(anchor_label)
    intercalated_label = f"{_clean_num(anchor_label)}a" if allow_intercalated_after_anchor else ""
    if not expected_label:
        return ()
    for match in _DEFINITION_CHILD_INSERT_PAYLOAD_RE.finditer(payload):
        label = _clean_num(match.group("label"))
        if label != expected_label and label != intercalated_label:
            return ()
        text = " ".join(match.group("text").split()).strip()
        if not text:
            return ()
        text = re.sub(r"\s+\.\s*$", "", text).strip()
        if not text.endswith(";"):
            text = f"{text};"
        rows.append({"label": label, "text": text})
        expected_label = _next_definition_child_label(label)
        intercalated_label = ""
        if not expected_label:
            expected_label = ""
    return tuple(rows)


def _definition_child_structural_substitution_payload(
    payload_text: str,
    *,
    child_label: str,
) -> Optional[dict[str, Any]]:
    payload = " ".join((payload_text or "").split()).strip()
    payload = re.sub(r"\s+\.\s*$", "", payload).strip()
    child_label = _clean_num(child_label)
    if not payload or not child_label:
        return None
    label_match = re.match(rf"^\(?{re.escape(child_label)}\)?\s+(?P<body>.+)$", payload, flags=re.I | re.S)
    if label_match is None:
        return None
    body = label_match.group("body").strip()
    child_matches = list(_ROMAN_CHILD_LABEL_RE.finditer(body))
    if not child_matches:
        return {"text": body.rstrip(" ."), "children": ()}

    intro = body[: child_matches[0].start()].strip(" —–,;")
    if not intro:
        return None
    children: list[dict[str, str]] = []
    expected = 1
    roman_ordinals = {
        "i": 1,
        "ii": 2,
        "iii": 3,
        "iv": 4,
        "v": 5,
        "vi": 6,
        "vii": 7,
        "viii": 8,
        "ix": 9,
        "x": 10,
    }
    for index, match in enumerate(child_matches):
        label = match.group("label").lower()
        if roman_ordinals.get(label) != expected:
            return None
        next_start = child_matches[index + 1].start() if index + 1 < len(child_matches) else len(body)
        text = body[match.end() : next_start].strip()
        text = text.strip(" ,;")
        if not text:
            return None
        children.append({"label": label, "text": text})
        expected += 1
    return {"text": intro, "children": tuple(children)}


def source_definition_child_structural_substitution(
    *,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Parse source-owned definition child substitutions with structural payloads."""
    if extracted_el is None or _tag(extracted_el) not in {"P1", "P2", "P3", "P4"}:
        return None
    target_match = _SECTION_SUBSECTION_TARGET_RE.match(affected_provisions or "")
    if target_match is None:
        return None
    text = " ".join((extracted_text or "").split()).strip()
    row_label = _clean_num(_direct_structural_num(extracted_el))
    if row_label:
        text = re.sub(
            rf"^\s*(?:{re.escape(row_label)}\s+){{1,2}}(?=in\s+section\b)",
            "",
            text,
            count=1,
            flags=re.I,
        ).strip()
    match = _IN_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RE.match(text)
    if match is None:
        return None
    section = _clean_num(match.group("section") or target_match.group("section"))
    subsection = _clean_num(match.group("subsection"))
    target_section = _clean_num(target_match.group("section"))
    target_subsection = _clean_num(target_match.group("subsection"))
    if not section or section != target_section or subsection != target_subsection:
        return None
    child_label = _clean_num(match.group("label"))
    payload = _definition_child_structural_substitution_payload(
        match.group("payload"),
        child_label=child_label,
    )
    if payload is None:
        return None
    return {
        "rule_id": UK_DEFINITION_CHILD_STRUCTURAL_SUBSTITUTION_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": text[: match.start("payload")].strip(),
        "target_ref": affected_provisions,
        "section": section,
        "subsection": subsection,
        "definition_term": " ".join(match.group("term").split()).strip(),
        "child_label": child_label,
        "tail_connector": match.group("connector").strip().lower(),
        "payload": payload,
    }


def source_definition_child_structural_sibling_insert(
    *,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    source_root: Optional[ET._Element],
    affected_provisions: str,
) -> Optional[dict[str, Any]]:
    """Parse a source-local definition child sibling insertion.

    This handles rows where the immediate source parent supplies the definition
    term, while the child row supplies only an anchor paragraph and inserted
    paragraph payloads.  Without the source-local parent term this must remain
    a manual frontier row, because a broad section target cannot identify the
    definition child list safely.
    """
    if extracted_el is None or source_root is None or _tag(extracted_el) not in {"P3", "P4"}:
        if extracted_el is None:
            return None
        row_text = " ".join((extracted_text or "").split()).strip()
        row_label = _clean_num(_direct_structural_num(extracted_el))
        if row_label:
            row_text = re.sub(
                rf"^\s*(?:{re.escape(row_label)}\s+){{1,2}}(?=in\s+section\b)",
                "",
                row_text,
                count=1,
                flags=re.I,
            ).strip()
        row_match = _IN_DEFINITION_AFTER_PARAGRAPH_BEFORE_CONNECTOR_INSERT_RE.match(row_text)
        if row_match is None:
            unsupported_match = _IN_DEFINITION_AFTER_PARAGRAPH_INSERT_RE.match(row_text)
            if unsupported_match is None:
                return None
            section_match = _SECTION_TARGET_RE.match(affected_provisions or "")
            if section_match is None:
                return None
            section_label = _clean_num(section_match.group("section"))
            source_section = _clean_num(unsupported_match.group("section") or section_label)
            if not section_label or source_section != section_label:
                return None
            return {
                "rule_id": "uk_effect_definition_child_structural_insert_rejected",
                "blocking": True,
                "family": "source_payload_elaboration",
                "reason_code": "definition_child_structural_insert_requires_child_and_tail_claim",
                "reason": (
                    "UK source inserts a structural definition child under a broad section "
                    "target, but does not explicitly claim the existing child-tail connector; "
                    "lowering must not append the payload to the broad section text."
                ),
                "source_id": str(extracted_el.get("id") or ""),
                "source_instruction": row_text[: unsupported_match.start("payload")].strip(),
                "target_ref": affected_provisions,
                "section": section_label,
                "definition_term": " ".join(unsupported_match.group("term").split()).strip(),
                "anchor_label": _clean_num(unsupported_match.group("anchor")),
            }
        section_match = _SECTION_TARGET_RE.match(affected_provisions or "")
        if section_match is None:
            return None
        section_label = _clean_num(section_match.group("section"))
        source_section = _clean_num(row_match.group("section") or section_label)
        if not section_label or source_section != section_label:
            return None
        anchor_label = _clean_num(row_match.group("anchor"))
        payloads = _definition_child_insert_payloads(
            row_match.group("payload"),
            anchor_label=anchor_label,
            allow_intercalated_after_anchor=True,
        )
        if not payloads:
            return None
        return {
            "rule_id": UK_DEFINITION_CHILD_STRUCTURAL_INSERT_BEFORE_TAIL_CONNECTOR_RULE_ID,
            "source_id": str(extracted_el.get("id") or ""),
            "source_instruction": row_text[: row_match.start("payload")].strip(),
            "target_ref": affected_provisions,
            "section": section_label,
            "definition_term": " ".join(row_match.group("term").split()).strip(),
            "anchor_label": anchor_label,
            "tail_connector": row_match.group("connector").strip().lower(),
            "anchor_target": f"section:{section_label}/item:{anchor_label}",
            "payloads": tuple(
                {
                    "label": payload["label"],
                    "text": payload["text"],
                    "target_ref": f"s. {section_label}({payload['label']})",
                    "target": f"section:{section_label}/item:{payload['label']}",
                }
                for payload in payloads
            ),
        }
    section_match = _SECTION_TARGET_RE.match(affected_provisions or "")
    if section_match is None:
        return None
    section_label = _clean_num(section_match.group("section"))
    if not section_label:
        return None

    ancestors = _source_ancestor_chain(source_root, extracted_el)
    definition_term = _source_definition_term_from_local_ancestor_context(
        ancestors,
        start_index=0,
        extracted_el=extracted_el,
    )
    if not definition_term:
        return None

    row_text = " ".join((extracted_text or "").split()).strip()
    row_label = _clean_num(_direct_structural_num(extracted_el))
    if row_label:
        row_text = re.sub(
            rf"^\s*(?:{re.escape(row_label)}\s+){{1,2}}(?=after\s+paragraph\b)",
            "",
            row_text,
            count=1,
            flags=re.I,
        ).strip()
    match = _AFTER_PARAGRAPH_DEFINITION_CHILD_INSERT_RE.match(row_text)
    if match is None:
        return None
    anchor_label = _clean_num(match.group("anchor"))
    payloads = _definition_child_insert_payloads(
        match.group("payload"),
        anchor_label=anchor_label,
    )
    if not payloads:
        return None
    return {
        "rule_id": UK_DEFINITION_CHILD_STRUCTURAL_SIBLING_INSERT_RULE_ID,
        "source_id": str(extracted_el.get("id") or ""),
        "source_instruction": row_text[: match.start("payload")].strip(),
        "target_ref": affected_provisions,
        "section": section_label,
        "definition_term": definition_term,
        "anchor_label": anchor_label,
        "anchor_target": f"section:{section_label}/item:{anchor_label}",
        "payloads": tuple(
            {
                "label": payload["label"],
                "text": payload["text"],
                "target_ref": f"s. {section_label}({payload['label']})",
                "target": f"section:{section_label}/item:{payload['label']}",
            }
            for payload in payloads
        ),
    }

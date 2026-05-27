from __future__ import annotations

import re
import xml.etree.ElementTree as ET
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

_AFTER_PARAGRAPH_DEFINITION_CHILD_INSERT_RE = re.compile(
    r"^\s*after\s+paragraph\s+\((?P<anchor>[a-z])\),?\s+"
    r"insert\s*[—–-]\s*(?P<payload>.+?)\s*$",
    flags=re.I | re.S,
)
_SECTION_TARGET_RE = re.compile(r"^\s*s\.\s*(?P<section>[0-9A-Za-z]+)\s*$", flags=re.I)
_DEFINITION_CHILD_INSERT_PAYLOAD_RE = re.compile(
    r"(?:^|;\s*)(?P<label>[a-z])\s+(?P<text>.*?)(?=(?:;\s*[a-z]\s+)|;\s*\.?\s*$|\.?\s*$)",
    flags=re.I | re.S,
)


def _next_single_letter_label(label: str) -> str:
    clean = _clean_num(label)
    if len(clean) != 1 or not clean.isalpha() or clean == "z":
        return ""
    return chr(ord(clean) + 1)


def _definition_child_insert_payloads(
    payload_text: str,
    *,
    anchor_label: str,
) -> tuple[dict[str, str], ...]:
    payload = " ".join((payload_text or "").split()).strip()
    payload = re.sub(r"\s+\.\s*$", "", payload).strip()
    if not payload:
        return ()

    rows: list[dict[str, str]] = []
    expected_label = _next_single_letter_label(anchor_label)
    if not expected_label:
        return ()
    for match in _DEFINITION_CHILD_INSERT_PAYLOAD_RE.finditer(payload):
        label = _clean_num(match.group("label"))
        if label != expected_label:
            return ()
        text = " ".join(match.group("text").split()).strip()
        if not text:
            return ()
        text = re.sub(r"\s+\.\s*$", "", text).strip()
        if not text.endswith(";"):
            text = f"{text};"
        rows.append({"label": label, "text": text})
        expected_label = _next_single_letter_label(label)
        if not expected_label:
            expected_label = ""
    return tuple(rows)


def source_definition_child_structural_sibling_insert(
    *,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_root: Optional[ET.Element],
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
        return None
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

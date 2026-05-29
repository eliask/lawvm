from __future__ import annotations

import re
from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_leaf_kind
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.provision_extractor import _instruction_text_before_amendment_container
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.source_fragment_context import _source_lead_text_before_subordinate_rows
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID = (
    "uk_effect_source_carried_table_entry_paragraph_substitution_text_patch"
)


def append_source_carried_table_entry_paragraph_observation(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_rule_ids: tuple[str, ...],
    primary: dict[str, Any],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    if UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID not in fragment_rule_ids:
        return
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
        family="source_table_elaboration",
        reason_code="source_carried_table_entry_paragraph_substitution_lowered",
        reason=(
            "UK child-row source names a paragraph or subparagraph "
            "inside a table entry, while the parent source names the "
            "entry; lowering combines those source-local facts into "
            "a bounded table-cell text patch instead of inventing "
            "schedule paragraph structure."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "text_match": op_text_match,
            "replacement": op_text_replacement,
            "source_parent_id": str(primary.get("source_parent_id") or ""),
            "source_entry_label": str(primary.get("source_entry_label") or ""),
            "source_paragraph_label": str(primary.get("source_paragraph_label") or ""),
            "source_subparagraph_label": str(primary.get("source_subparagraph_label") or ""),
        },
    )

SOURCE_TABLE_CELL_PARAGRAPH_SENTINEL_RE = re.compile(
    r"^TEXT_TABLE_CELL_PARAGRAPH_(?P<paragraph>[0-9]+)"
    r"(?:_SUBPARAGRAPH_(?P<subparagraph>[0-9A-Za-z]+))?$"
)

_SOURCE_TABLE_ENTRY_PARENT_RE = re.compile(
    r"\bentry\s+for\s+(?P<entry>.+?)\s+is\s+amended\s+as\s+follows\b",
    flags=re.I | re.S,
)
_SOURCE_TABLE_ENTRY_PARAGRAPH_SUBPARA_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"in\s+paragraph\s+(?P<paragraph>[0-9]+),?\s+"
    r"for\s+sub-?paragraph\s+\((?P<subparagraph>[0-9A-Za-z]+)\)\s+"
    r"substitute\s*[—-]\s*(?P<replacement>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)
_SOURCE_TABLE_ENTRY_PARAGRAPH_SUBSTITUTION_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"for\s+paragraph\s+(?P<paragraph>[0-9]+)\s+"
    r"substitute\s*[—-]\s*(?P<replacement>.+?)\s*\.?\s*$",
    flags=re.I | re.S,
)


def _strip_instruction_terminal_dot(text: str) -> str:
    stripped = " ".join((text or "").split()).strip()
    if stripped.endswith(" ."):
        stripped = stripped[:-2].rstrip()
    if len(stripped) > 1 and stripped.endswith(".") and stripped[-2] in {",", ";"}:
        stripped = stripped[:-1].rstrip()
    return stripped


def _strip_source_payload_label(text: str, label: str) -> str:
    label_norm = _clean_num(label)
    if not label_norm:
        return _strip_instruction_terminal_dot(text)
    stripped = _strip_instruction_terminal_dot(text)
    return re.sub(rf"^\s*{re.escape(label_norm)}\s+", "", stripped, flags=re.I).strip()


def _parenthesize_flat_source_subparagraph_labels(text: str) -> str:
    """Render flattened source child labels as visible subparagraph markers."""
    rendered = re.sub(r"([—-])\s+([a-z])\s+", r"\1\n\n(\2) ", text, count=1, flags=re.I)
    rendered = re.sub(r"(\b(?:or|and)\b)\s+([a-z])\s+", r"\1\n\n(\2) ", rendered, flags=re.I)
    return rendered


def _source_carried_table_entry_paragraph_substitution(
    *,
    extracted_el: Optional[ET._Element],
    source_root: Optional[ET._Element],
    extracted_text: Optional[str],
    target_ref: str,
    target: LegalAddress,
) -> Optional[dict[str, Any]]:
    """Resolve child paragraph substitutions under a source-named table entry.

    The effect feed can name only a broad schedule table, while the affecting
    source parent says that a specific table entry is amended as follows and
    child rows address paragraph/subparagraph slots inside that entry's matter
    cell.  Lowering keeps that as a table-cell selector plus a symbolic
    paragraph selector; replay resolves the live flat cell shape.
    """
    if _addr_leaf_kind(target) != "schedule":
        return None
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return None
    sub_match = _SOURCE_TABLE_ENTRY_PARAGRAPH_SUBPARA_SUBSTITUTION_RE.match(text)
    para_match = _SOURCE_TABLE_ENTRY_PARAGRAPH_SUBSTITUTION_RE.match(text)
    if sub_match is None and para_match is None:
        return None

    ancestors = _source_ancestor_chain(source_root, extracted_el)
    entry_label = ""
    source_parent_id = ""
    for ancestor_index, ancestor in enumerate(ancestors):
        parent_text = _instruction_text_before_amendment_container(ancestor)
        if not parent_text:
            parent_text = _source_lead_text_before_subordinate_rows(ancestor)
        parent_match = _SOURCE_TABLE_ENTRY_PARENT_RE.search(" ".join(parent_text.split()))
        if parent_match is None:
            continue
        entry_label = " ".join(parent_match.group("entry").split()).strip()
        source_parent_id = str(ancestor.get("id") or "")
        if not source_parent_id:
            source_parent_id = next(
                (str(candidate.get("id")) for candidate in ancestors[ancestor_index + 1 :] if candidate.get("id")),
                "",
            )
        break
    if not entry_label:
        return None

    if sub_match is not None:
        paragraph_label = _clean_num(sub_match.group("paragraph"))
        subparagraph_label = _clean_num(sub_match.group("subparagraph"))
        replacement_body = _strip_source_payload_label(sub_match.group("replacement"), subparagraph_label)
        if not paragraph_label or not subparagraph_label or not replacement_body:
            return None
        replacement = f"({subparagraph_label}) {replacement_body}"
        original = f"TEXT_TABLE_CELL_PARAGRAPH_{paragraph_label}_SUBPARAGRAPH_{subparagraph_label}"
    else:
        assert para_match is not None
        paragraph_label = _clean_num(para_match.group("paragraph"))
        replacement_body = _strip_source_payload_label(para_match.group("replacement"), paragraph_label)
        if not paragraph_label or not replacement_body:
            return None
        replacement = f"{paragraph_label}. {_parenthesize_flat_source_subparagraph_labels(replacement_body)}"
        subparagraph_label = ""
        original = f"TEXT_TABLE_CELL_PARAGRAPH_{paragraph_label}"

    selector = {
        "rule_id": UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
        "selector_mode": "unique_entry_cell",
        "entry_label": entry_label,
        "column_index": 2,
        "target_ref": target_ref,
        "original_target": str(target),
        "source_parent_id": source_parent_id,
        "source_paragraph_label": paragraph_label,
    }
    if subparagraph_label:
        selector["source_subparagraph_label"] = subparagraph_label
    return {
        "original": original,
        "replacement": replacement,
        "rule_id": UK_SOURCE_CARRIED_TABLE_ENTRY_PARAGRAPH_RULE_ID,
        "source_parent_id": source_parent_id,
        "source_entry_label": entry_label,
        "source_paragraph_label": paragraph_label,
        "source_subparagraph_label": subparagraph_label,
        "table_cell_selector": selector,
    }

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_leaf_kind,
    _addr_leaf_label,
    _looks_like_lettered_item_label,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.uk_grafter import _clean_num


_SOURCE_CARRIED_STRUCTURAL_SIBLING_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"after\s+(?P<source_kind>sub-?paragraph|paragraph|subsection|item)\s+"
    r"\((?P<anchor_label>[0-9A-Za-z]+)\),?\s+"
    r"insert\s*[—-]\s*(?P<inserted_label>[0-9A-Za-z]+)\s+"
    r"(?P<inserted_text>.+?)\s*$",
    flags=re.I | re.S,
)


@dataclass(frozen=True)
class UKStructuralSiblingInsertLowering:
    target: LegalAddress
    content_ir: Optional[dict[str, Any]]
    detail: Optional[dict[str, str]]

    @property
    def applied(self) -> bool:
        return self.detail is not None


def _normalize_structural_sibling_source_kind(text: str) -> str:
    normalized = re.sub(r"[^a-z]+", "", str(text or "").lower())
    if normalized.endswith("s"):
        normalized = normalized[:-1]
    if normalized == "subparagraph":
        return "subparagraph"
    return normalized


def _child_kind_for_structural_sibling_insert(
    *,
    target: LegalAddress,
    source_kind: str,
    inserted_label: str,
) -> str:
    """Return the LawVM child kind for a source-owned structural sibling insert."""
    target_leaf_kind = str(_addr_leaf_kind(target) or "").lower()
    if not target_leaf_kind:
        return ""
    normalized_source_kind = _normalize_structural_sibling_source_kind(source_kind)
    if _addr_container(target) == "schedule":
        if target_leaf_kind == "paragraph" and normalized_source_kind == "paragraph":
            return "item" if _looks_like_lettered_item_label(inserted_label) else "subparagraph"
        if target_leaf_kind == "subparagraph" and normalized_source_kind in {"subparagraph", "paragraph"}:
            return "item"
        if target_leaf_kind in {"item", "point"} and normalized_source_kind in {"subparagraph", "paragraph", "item"}:
            return "item"
        return ""
    if target_leaf_kind == "section" and normalized_source_kind == "subsection":
        return "subsection"
    if target_leaf_kind == "subsection" and normalized_source_kind == "paragraph":
        return "paragraph"
    if target_leaf_kind == "paragraph" and normalized_source_kind == "subparagraph":
        return "subparagraph"
    if target_leaf_kind in {"subparagraph", "item", "point"} and normalized_source_kind in {"item", "paragraph"}:
        return "item"
    return ""


def _structural_sibling_insert_from_source(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Lower explicit source-owned sibling insertions to a child insert payload."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text or re.search(r"\bin\s+the\s+inserted\s+", text, flags=re.I):
        return None
    match = _SOURCE_CARRIED_STRUCTURAL_SIBLING_INSERT_RE.match(text)
    if match is None:
        return None
    inserted_label = _clean_num(match.group("inserted_label"))
    anchor_label = _clean_num(match.group("anchor_label"))
    if not inserted_label or not anchor_label or inserted_label == anchor_label:
        return None
    if inserted_label == _clean_num(_addr_leaf_label(target) or ""):
        return None
    child_kind = _child_kind_for_structural_sibling_insert(
        target=target,
        source_kind=match.group("source_kind"),
        inserted_label=inserted_label,
    )
    if not child_kind:
        return None
    inserted_text = " ".join(match.group("inserted_text").split()).strip()
    inserted_text = re.sub(r"\s*;\s*;\s*$", ";", inserted_text).strip()
    if not inserted_text:
        return None
    new_target = canonicalize_uk_address(LegalAddress(path=(*target.path, (child_kind, inserted_label))))
    return {
        "anchor_label": anchor_label,
        "child_kind": child_kind,
        "inserted_label": inserted_label,
        "inserted_text": inserted_text,
        "new_target": str(new_target),
        "source_kind": _normalize_structural_sibling_source_kind(match.group("source_kind")),
    }


def lower_source_structural_sibling_insert(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    curr_action: str,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
    target_ref: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKStructuralSiblingInsertLowering:
    detail = (
        _structural_sibling_insert_from_source(
            extracted_text=extracted_text,
            target=target,
        )
        if curr_action == "insert"
        and effect_type in {"words inserted", "word inserted"}
        and extracted_text
        else None
    )
    if detail is None:
        return UKStructuralSiblingInsertLowering(target=target, content_ir=content_ir, detail=None)

    lowered_target = canonicalize_uk_address(
        LegalAddress(
            path=(
                *target.path,
                (
                    detail["child_kind"],
                    detail["inserted_label"],
                ),
            )
        )
    )
    lowered_content_ir = {
        "kind": detail["child_kind"],
        "label": detail["inserted_label"],
        "text": detail["inserted_text"],
        "attrs": {
            "source_rule_id": "uk_effect_structural_sibling_insert_lowered",
            "source_anchor_child_label": detail["anchor_label"],
            "source_child_kind": detail["source_kind"],
        },
        "children": [],
    }
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_structural_sibling_insert_lowered",
        family="source_context_elaboration",
        reason_code="source_owned_structural_sibling_insert",
        reason=(
            "UK source text explicitly inserts a new labelled structural "
            "sibling after a named child of the affected parent; lowering "
            "emits a child insert at the source-owned sibling target "
            "instead of appending payload text to the anchor."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "original_target_ref": target_ref,
            "source_anchor_child_label": detail["anchor_label"],
            "source_child_kind": detail["source_kind"],
            "inserted_child_kind": detail["child_kind"],
            "inserted_child_label": detail["inserted_label"],
            "target": str(lowered_target),
        },
    )
    return UKStructuralSiblingInsertLowering(
        target=lowered_target,
        content_ir=lowered_content_ir,
        detail=detail,
    )

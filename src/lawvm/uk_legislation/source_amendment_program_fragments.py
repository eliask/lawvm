from __future__ import annotations

from dataclasses import dataclass
import re
from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
    _schedule_target_levels,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
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
_SOURCE_AMENDMENT_INSERTED_ANCHOR_STRUCTURAL_INSERT_RE = re.compile(
    r"^\s*(?:(?:[0-9A-Za-z]+|[ivxlcdm]+)\s+){0,2}"
    r"(?P<direction>before|after)\s+"
    r"(?P<anchor_kind>Ground|sub-?paragraph|paragraph|subsection)\s+"
    r"(?P<anchor_label>[0-9A-Za-z]+(?:\([0-9A-Za-z]+\))?)\s+"
    r"(?:(?:\(\s*inserted\s+by\s+(?P<inserted_by>[^)]{1,240})\s*\))|as\s+inserted)"
    r",?\s+insert\s*[—–-]\s*"
    r"(?P<inserted_label>(?:Ground\s+)?[0-9A-Za-z]+)\s+"
    r"(?P<inserted_text>[\s\S]{1,8000})\s*$",
    flags=re.I,
)
_SOURCE_INSERTED_BY_PARAGRAPH_RE = re.compile(
    r"\bparagraph\s+(?P<label>[0-9A-Za-z]+)\b(?P<this_schedule>\s+of\s+this\s+Schedule)?",
    flags=re.I,
)
_GROUND_PAYLOAD_LABEL_RE = re.compile(r"\bGround\s+(?P<label>[0-9][0-9A-Za-z]*)\b", flags=re.I)
UK_AMENDMENT_PROGRAM_INSERTED_PARENT_CHILD_INSERT_RULE_ID = (
    "uk_effect_amendment_program_inserted_parent_child_insert_text_patch"
)
UK_AMENDMENT_PROGRAM_INSERTED_ANCHOR_STRUCTURAL_INSERT_RULE_ID = (
    "uk_effect_amendment_program_inserted_anchor_structural_insert_lowered"
)


@dataclass(frozen=True)
class UKAmendmentProgramInsertedAnchorLowering:
    target: LegalAddress
    content_ir: Optional[dict[str, Any]]
    detail: Optional[dict[str, Any]]

    @property
    def applied(self) -> bool:
        return self.detail is not None


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
    target_levels = _schedule_target_levels(target)
    source_paragraph = _clean_num(match.group("paragraph"))
    source_item = _clean_num(match.group("item"))
    if not source_paragraph or _clean_num(target_levels.paragraph or "") != source_paragraph:
        return None
    if (
        not source_item
        or not target_levels.item_labels
        or _clean_num(target_levels.item_labels[-1]) != source_item
    ):
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
    target_levels = _schedule_target_levels(target)
    source_subparagraph = _clean_num(match.group("subparagraph"))
    source_item = _clean_num(match.group("item"))
    if not source_subparagraph or _clean_num(target_levels.subparagraph or "") != source_subparagraph:
        return None
    if (
        not source_item
        or not target_levels.item_labels
        or _clean_num(target_levels.item_labels[-1]) != source_item
    ):
        return None
    def source_label(value: object) -> str:
        return str(value or "").strip().strip("()").lower().strip(".")

    return {
        "source_subparagraph_label": source_subparagraph,
        "source_item_label": source_item,
        "inserted_parent_label": source_label(match.group("inserted_parent")),
        "direction": str(match.group("direction") or "").lower(),
        "anchor_label": source_label(match.group("anchor")),
        "inserted_label": source_label(match.group("inserted_label")),
        "inserted_text_preview": " ".join(str(match.group("inserted_text") or "").split())[:240],
    }


def _amendment_program_inserted_anchor_structural_insert(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Identify inserts after an anchor created by a prior amendment instruction."""
    text = " ".join((extracted_text or "").split()).strip()
    if not text or "inserted" not in text.lower():
        return None
    match = _SOURCE_AMENDMENT_INSERTED_ANCHOR_STRUCTURAL_INSERT_RE.match(text)
    if match is None:
        return None

    def source_label(value: object) -> str:
        raw = str(value or "").strip().lower().strip(".")
        if raw.startswith("(") and raw.endswith(")"):
            raw = raw[1:-1].strip()
        raw = re.sub(r"^ground\s+", "", raw, flags=re.I)
        return raw

    anchor_label = source_label(match.group("anchor_label"))
    inserted_label = source_label(match.group("inserted_label"))
    if not anchor_label or not inserted_label or anchor_label == inserted_label:
        return None
    source_inserted_by = " ".join(str(match.group("inserted_by") or "as inserted").split())
    inserted_text = " ".join(str(match.group("inserted_text") or "").split()).strip()
    inserted_payload_labels = _inserted_anchor_payload_labels(
        anchor_kind=str(match.group("anchor_kind") or ""),
        inserted_label=inserted_label,
        inserted_text=inserted_text,
    )
    return {
        "target": str(target),
        "inserted_anchor_kind": str(match.group("anchor_kind") or "").lower().replace("-", ""),
        "inserted_anchor_label": anchor_label,
        "source_inserted_by": source_inserted_by,
        **_source_inserted_by_detail(source_inserted_by),
        "direction": str(match.group("direction") or "").lower(),
        "anchor_label": anchor_label,
        "inserted_label": inserted_label,
        "inserted_payload_labels": inserted_payload_labels,
        "inserted_payload_label_count": len(inserted_payload_labels),
        "inserted_text": inserted_text,
        "inserted_text_preview": inserted_text[:240],
    }


def _source_inserted_by_detail(source_inserted_by: str) -> dict[str, str]:
    """Return normalized source-chain fields for inserted-anchor diagnostics."""
    if source_inserted_by == "as inserted":
        return {
            "source_inserted_by_label": "",
            "source_inserted_by_scope": "deictic",
        }
    match = _SOURCE_INSERTED_BY_PARAGRAPH_RE.search(source_inserted_by)
    if match is None:
        return {
            "source_inserted_by_label": "",
            "source_inserted_by_scope": "unparsed",
        }
    return {
        "source_inserted_by_label": _clean_num(match.group("label")),
        "source_inserted_by_scope": (
            "this_schedule" if match.group("this_schedule") else "unspecified"
        ),
    }


def _affecting_schedule_paragraph_label(effect: UKEffectRecord) -> str:
    match = re.search(
        r"\bSch\.\s*[0-9A-Za-z]+\s+para\.\s*(?P<label>[0-9A-Za-z]+)",
        effect.affecting_provisions or "",
        flags=re.I,
    )
    return _clean_num(match.group("label")) if match is not None else ""


def _is_prior_same_schedule_paragraph_chain(
    *,
    detail: dict[str, Any],
    effect: UKEffectRecord,
) -> bool:
    if detail.get("source_inserted_by_scope") not in {"this_schedule", "unspecified"}:
        return False
    source_label = _clean_num(detail.get("source_inserted_by_label") or "")
    affecting_label = _affecting_schedule_paragraph_label(effect)
    if not source_label or not affecting_label:
        return False
    if not source_label.isdigit() or not affecting_label.isdigit():
        return False
    return int(source_label) < int(affecting_label)


def _source_chain_scope_is_inferred_from_affecting_provision(
    detail: dict[str, Any],
) -> bool:
    return detail.get("source_inserted_by_scope") == "unspecified"


def _inserted_anchor_payload_labels(
    *,
    anchor_kind: str,
    inserted_label: str,
    inserted_text: str,
) -> list[str]:
    """Return payload labels visible in an inserted-anchor instruction."""
    labels = [inserted_label]
    if str(anchor_kind or "").lower().replace("-", "") == "ground":
        inserted_stem = _ground_label_series_stem(inserted_label)
        labels.extend(
            candidate
            for match in _GROUND_PAYLOAD_LABEL_RE.finditer(inserted_text or "")
            if (candidate := _clean_num(match.group("label")))
            and _ground_label_series_stem(candidate) == inserted_stem
        )
    return list(dict.fromkeys(label for label in labels if label))


def _ground_label_series_stem(label: str) -> str:
    """Return the numeric series that groups related UK ground labels."""
    match = re.match(r"([0-9]+)", str(label or ""))
    return match.group(1) if match is not None else ""


def _ground_payload_text_for_label(
    *,
    inserted_label: str,
    inserted_text: str,
    target_label: str,
) -> str:
    labels: list[tuple[str, int, int]] = [(inserted_label, 0, 0)]
    labels.extend(
        (_clean_num(match.group("label")), match.start(), match.end())
        for match in _GROUND_PAYLOAD_LABEL_RE.finditer(inserted_text or "")
    )
    for index, (label, start, payload_start) in enumerate(labels):
        if _clean_num(label) != _clean_num(target_label):
            continue
        end = labels[index + 1][1] if index + 1 < len(labels) else len(inserted_text)
        return " ".join(inserted_text[payload_start:end].split()).strip()
    return ""


def lower_amendment_program_inserted_anchor_structural_insert(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    target: LegalAddress,
    content_ir: Optional[dict[str, Any]],
    target_ref: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKAmendmentProgramInsertedAnchorLowering:
    detail = (
        _amendment_program_inserted_anchor_structural_insert(
            extracted_text=extracted_text,
            target=target,
        )
        if extracted_text and curr_action == "insert"
        else None
    )
    if detail is None:
        return UKAmendmentProgramInsertedAnchorLowering(
            target=target,
            content_ir=content_ir,
            detail=None,
        )
    target_leaf_kind = str(_addr_leaf_kind(target) or "").lower()
    target_leaf_label = _clean_num(_addr_leaf_label(target) or "")
    inserted_label = _clean_num(detail.get("inserted_label") or "")
    inserted_payload_labels = tuple(str(label) for label in detail.get("inserted_payload_labels") or ())
    if (
        detail.get("inserted_anchor_kind") != "ground"
        or detail.get("direction") != "after"
        or not _is_prior_same_schedule_paragraph_chain(detail=detail, effect=effect)
        or _addr_container(target) != "schedule"
        or target_leaf_kind != "subparagraph"
        or not inserted_label
        or target_leaf_label not in inserted_payload_labels
    ):
        return UKAmendmentProgramInsertedAnchorLowering(
            target=target,
            content_ir=content_ir,
            detail=None,
        )

    inserted_text = _ground_payload_text_for_label(
        inserted_label=inserted_label,
        inserted_text=str(detail.get("inserted_text") or ""),
        target_label=target_leaf_label,
    )
    if not inserted_text:
        return UKAmendmentProgramInsertedAnchorLowering(
            target=target,
            content_ir=content_ir,
            detail=None,
        )

    lowered_target = canonicalize_uk_address(target)
    lowered_content_ir = {
        "kind": target_leaf_kind,
        "label": inserted_label,
        "text": inserted_text,
        "attrs": {
            "source_rule_id": UK_AMENDMENT_PROGRAM_INSERTED_ANCHOR_STRUCTURAL_INSERT_RULE_ID,
            "source_anchor_child_label": str(detail.get("anchor_label") or ""),
            "source_inserted_by_label": str(detail.get("source_inserted_by_label") or ""),
            "source_inserted_by_scope": str(detail.get("source_inserted_by_scope") or ""),
        },
        "children": [],
    }
    inferred_scope = _source_chain_scope_is_inferred_from_affecting_provision(detail)
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_AMENDMENT_PROGRAM_INSERTED_ANCHOR_STRUCTURAL_INSERT_RULE_ID,
        family="amendment_program_lowering",
        reason_code=(
            "same_schedule_contextual_ground_source_chain_insert"
            if inferred_scope
            else "same_schedule_ground_source_chain_insert"
        ),
        reason=(
            "UK source text inserts Ground payload material after an anchor "
            "inserted by an earlier paragraph of the same amendment schedule; "
            "lowering emits only the feed-target child and does not search "
            "base law for the inserted anchor."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(lowered_target),
            **detail,
            "compiled_payload_label": target_leaf_label,
            "source_inserted_by_scope_inferred_from_affecting_provision": inferred_scope,
        },
    )
    return UKAmendmentProgramInsertedAnchorLowering(
        target=lowered_target,
        content_ir=lowered_content_ir,
        detail=detail,
    )


def _fragment_substitution_amendment_program_inserted_parent_child_insert(
    *,
    extracted_text: Optional[str],
    target: LegalAddress,
) -> Optional[dict[str, str]]:
    """Lower a child insert into text created by a prior amendment instruction."""
    detail = _amendment_program_inserted_parent_structural_insert(
        extracted_text=extracted_text,
        target=target,
    )
    if detail is None:
        return None
    inserted_label = detail["inserted_label"]
    inserted_text = detail["inserted_text_preview"]
    if not inserted_label or not inserted_text:
        return None
    return {
        "original": (
            "TEXT_AMENDMENT_PROGRAM_INSERTED_PARENT_"
            f"{detail['inserted_parent_label']}_"
            f"{detail['direction'].upper()}_{detail['anchor_label']}"
        ),
        "replacement": f"{inserted_label} {inserted_text}",
        "rule_id": UK_AMENDMENT_PROGRAM_INSERTED_PARENT_CHILD_INSERT_RULE_ID,
        **detail,
    }


def reject_amendment_program_inserted_parent_structural_insert(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    target: LegalAddress,
    target_ref: str,
    extracted_el: Optional[ET._Element],
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
        inserted_anchor_detail = (
            _amendment_program_inserted_anchor_structural_insert(
                extracted_text=extracted_text,
                target=target,
            )
            if extracted_text and curr_action == "insert"
            else None
        )
        if inserted_anchor_detail is None:
            return False
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_amendment_program_inserted_anchor_structural_insert_rejected",
            family="amendment_program_lowering",
            reason_code="insert_targets_prior_amendment_inserted_anchor",
            reason=(
                "UK source text inserts material after an anchor that the "
                "same amendment program says was inserted by another "
                "instruction; this needs explicit source-chain compilation "
                "and must not search the base law for a convenient anchor."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                **inserted_anchor_detail,
            },
        )
        return True

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

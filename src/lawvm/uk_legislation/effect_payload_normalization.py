"""Payload normalization observations for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
from lxml import etree as ET
from typing import Any, Callable, Optional

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _addr_leaf_label,
)
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_payload_rejections import (
    reject_body_section_replace_with_unmatched_schedule_payload,
    reject_broad_schedule_flat_replace_payload,
    reject_non_substantive_structural_payload,
)
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_observation
from lawvm.uk_legislation.metadata_rewrites import _select_whole_schedule_element
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_ir_node_kind
from lawvm.uk_legislation.payload_conversion import _to_mutable_node
from lawvm.uk_legislation.payload_identity import (
    _synthesize_payload_descendant_eids,
    _synthesize_whole_schedule_payload_descendant_eids,
)
from lawvm.uk_legislation.source_payload_elaboration import (
    _retarget_instruction_element_to_target,
    _source_payload_matches_target_leaf,
    _with_trailing_subordinate_siblings,
)
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
    _direct_payload_text,
    _flat_p1para_schedule_paragraph_insert_payload,
    _inserted_section_p1group_heading_text,
    _prepend_inserted_section_heading_carrier,
)
from lawvm.uk_legislation.uk_grafter import (
    _LEG_NS,
    _clean_num,
    _parse_chapter,
    _parse_p1group,
    _parse_p2,
    _parse_p3,
    _parse_p4,
    _parse_part,
    _parse_pblock,
    _parse_schedule_single,
    _parse_section,
    _slugify,
    _text_content,
)
from lawvm.uk_legislation.xml_helpers import _direct_structural_num, _tag


_UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID = (
    "uk_effect_payload_label_realigned_to_target_leaf"
)
_UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID = (
    "uk_effect_payload_kind_realigned_to_target_leaf"
)


@dataclass(frozen=True)
class UKFlatP1paraScheduleParagraphInsertLowering:
    content_ir: Optional[dict[str, Any]]
    lowered: bool = False


@dataclass(frozen=True)
class UKStructuralPayloadExtraction:
    content_ir: Optional[dict[str, Any]]
    actual_el: Optional[ET._Element]
    flat_p1para_schedule_insert_lowered: bool
    source_structural_payload_matches_target: bool


@dataclass(frozen=True)
class UKPayloadNodePreparation:
    payload_node: Optional[IRNode]
    skip_effect: bool = False


def _uk_core_kind_alias_value(kind: str) -> str:
    """Return the core IR kind value for UK-local aliases used in addresses."""
    kind_value = str(kind or "").lower()
    if kind_value == "point":
        return "item"
    return kind_value


_UK_EFFECT_INSERTED_SCHEDULE_P1GROUP_CROSSHEADING_WRAPPER_RULE_ID = (
    "uk_effect_inserted_schedule_p1group_crossheading_wrapper_lowered"
)


def _find_schedule_p1group_wrapper_payload_element(
    extracted_el: Optional[ET._Element],
    payload_match_target: LegalAddress,
) -> Optional[ET._Element]:
    """Return a P1group wrapper whose inner P1 matches a schedule paragraph target.

    UK affecting XML sometimes wraps inserted schedule paragraphs in a
    ``P1group/Title`` that supplies the intended crossheading (e.g. ``The
    Harbours Act 1964 (c. 40)``).  The matching inner ``P1`` supplies the
    paragraph number.  When present, the whole ``P1group`` should be lowered as
    the payload so the crossheading wrapper is preserved.
    """
    if extracted_el is None:
        return None
    if (
        _addr_container(payload_match_target) != "schedule"
        or _addr_leaf_kind(payload_match_target) != "paragraph"
    ):
        return None
    target_label = _addr_leaf_label(payload_match_target) or ""
    if not target_label:
        return None
    target_clean = _clean_num(target_label)
    for am in extracted_el.iter():
        if _tag(am) not in ("BlockAmendment", "InlineAmendment"):
            continue
        for child in list(am):
            if _tag(child) != "P1group":
                continue
            inner_p1s = [c for c in child if _tag(c) == "P1"]
            if not inner_p1s:
                continue
            if any(
                _clean_num(_direct_structural_num(p1)) == target_clean
                for p1 in inner_p1s
            ):
                return _with_trailing_subordinate_siblings(child, am)
    return None


def _is_foreign_physical_source_id(identity: str) -> bool:
    """Return True for legislation.gov.uk physical ids like ``p02828``."""
    if not identity or identity[0] != "p" or len(identity) < 4:
        return False
    return identity[1:4].isdigit()


def _maybe_lower_inserted_schedule_p1group_crossheading_wrapper(
    *,
    content_ir: Optional[dict[str, Any]],
    actual_el: Optional[ET._Element],
    target: LegalAddress,
    effect: UKEffectRecord,
    target_ref: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    """Convert an inserted schedule P1group wrapper into a crossheading Pblock.

    UK affecting XML sometimes wraps inserted schedule paragraphs in a
    ``P1group`` whose ``Title`` supplies the intended crossheading.  The oracle
    materialises this as a ``<Pblock id=\"...crossheading-...\">`` containing
    the inserted paragraphs.  Lowering converts the ``P1group`` to a
    ``CROSSHEADING`` node, assigns a schedule-scoped crossheading EID, and gives
    each child paragraph a flat schedule EID.
    """
    if content_ir is None or actual_el is None:
        return content_ir
    if _tag(actual_el) != "P1group":
        return content_ir
    if _addr_container(target) != "schedule" or _addr_leaf_kind(target) != "paragraph":
        return content_ir
    schedule_label = _addr_field(target, "schedule")
    if not schedule_label:
        return content_ir
    schedule_root = f"schedule-{_clean_num(schedule_label)}"
    title_el = actual_el.find(f"./{{{_LEG_NS}}}Title")
    heading_text = _text_content(title_el) if title_el is not None else ""
    if not heading_text:
        return content_ir
    # The current-oracle crossheading slug appears to strip hyphens before
    # slugifying compound words (e.g. "Levelling-Up" -> "levellingup"), so
    # we remove ASCII hyphens here to stay commensurable.
    heading_slug = _slugify(heading_text.replace("-", ""))
    if not heading_slug:
        return content_ir
    wrapper_eid = f"{schedule_root}-crossheading-{heading_slug}"

    result = dict(content_ir)
    result["kind"] = IRNodeKind.CROSSHEADING.value
    result["attrs"] = dict(result.get("attrs") or {})
    result["attrs"]["eId"] = wrapper_eid
    result["attrs"].pop("id", None)
    result["attrs"]["source_tag"] = "P1group"
    result["attrs"]["source_rule_id"] = (
        _UK_EFFECT_INSERTED_SCHEDULE_P1GROUP_CROSSHEADING_WRAPPER_RULE_ID
    )

    child_eids: list[str] = []
    children = list(result.get("children") or [])
    for child in children:
        child_kind = str(child.get("kind") or "").lower()
        child_label = str(child.get("label") or "").strip()
        clean_label = _clean_num(child_label) if child_label else ""
        if not clean_label:
            continue
        if child_kind == "paragraph":
            child_eid = f"{schedule_root}-paragraph-{clean_label}"
        elif child_kind in ("subparagraph", "item", "point", "p2", "p3", "p4"):
            child_eid = f"{schedule_root}-{child_kind}-{clean_label}"
        else:
            continue
        child["attrs"] = dict(child.get("attrs") or {})
        child["attrs"]["eId"] = child_eid
        child["attrs"].pop("id", None)
        child_eids.append(child_eid)

    def _scrub_foreign_ids(node: dict[str, Any]) -> None:
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            identity = str(attrs.get("eId") or attrs.get("id") or "")
            if _is_foreign_physical_source_id(identity):
                attrs.pop("eId", None)
                attrs.pop("id", None)
        for c in node.get("children") or []:
            _scrub_foreign_ids(c)

    _scrub_foreign_ids(result)

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_EFFECT_INSERTED_SCHEDULE_P1GROUP_CROSSHEADING_WRAPPER_RULE_ID,
        family="payload_normalization",
        reason_code="inserted_schedule_paragraph_p1group_wrapper_lowered_to_crossheading",
        reason=(
            "UK inserted schedule paragraph payload is wrapped in a P1group whose "
            "Title supplies the intended crossheading; lowering converts the "
            "P1group to a CROSSHEADING Pblock and assigns flat schedule EIDs to "
            "the wrapper and its paragraph children."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "schedule_root": schedule_root,
            "wrapper_eid": wrapper_eid,
            "heading_text_preview": heading_text[:200],
            "child_eids": child_eids,
        },
    )
    return result


def lower_flat_p1para_schedule_paragraph_insert_payload(
    *,
    effect: UKEffectRecord,
    action: str,
    target_ref: str,
    payload_match_target: LegalAddress,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    fallback_target_eid: Callable[[LegalAddress], str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKFlatP1paraScheduleParagraphInsertLowering:
    if action != "insert" or extracted_el is None:
        return UKFlatP1paraScheduleParagraphInsertLowering(content_ir=None)
    flat_p1para_payload = _flat_p1para_schedule_paragraph_insert_payload(
        extracted_el,
        payload_match_target,
        fallback_target_eid=fallback_target_eid,
    )
    if flat_p1para_payload is None:
        return UKFlatP1paraScheduleParagraphInsertLowering(content_ir=None)
    flat_p1para_payload_detail = dict(
        flat_p1para_payload.pop("_lawvm_detail", {}) or {}
    )
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
        family="payload_normalization",
        reason_code="flat_blockamendment_p1para_labelled_schedule_paragraph",
        reason=(
            "UK inserted schedule paragraph source payload is a flat "
            "BlockAmendment/P1para with a direct text run beginning with "
            "the target paragraph label; lowering uses that labelled text "
            "as the paragraph payload and records sibling heading text as "
            "unresolved rather than replaying the whole instruction."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(payload_match_target),
            **flat_p1para_payload_detail,
        },
    )
    return UKFlatP1paraScheduleParagraphInsertLowering(
        content_ir=flat_p1para_payload,
        lowered=True,
    )


def prepend_inserted_p1group_heading_carrier(
    *,
    effect: UKEffectRecord,
    target_ref: str,
    target: LegalAddress,
    content_ir: dict[str, Any],
    actual_el: ET._Element,
    extracted_el: ET._Element,
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    inserted_heading_text = _inserted_section_p1group_heading_text(
        actual_el,
        extracted_el,
        target,
    )
    target_leaf_kind = _addr_leaf_kind(target) or ""
    heading_source_rule_id = (
        "uk_inserted_section_p1group_heading_carrier"
        if target_leaf_kind == "section"
        else "uk_inserted_p1group_heading_carrier"
    )
    heading_observation_rule_id = (
        "uk_effect_inserted_section_p1group_heading_carrier_lowered"
        if target_leaf_kind == "section"
        else "uk_effect_inserted_p1group_heading_carrier_lowered"
    )
    if not inserted_heading_text or not _prepend_inserted_section_heading_carrier(
        content_ir,
        heading_text=inserted_heading_text,
        source_rule_id=heading_source_rule_id,
    ):
        return False
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=heading_observation_rule_id,
        family="payload_normalization",
        reason_code=f"inserted_{target_leaf_kind}_wrapped_by_p1group_title",
        reason=(
            "UK inserted provision payload is wrapped by a P1group "
            "Title; lowering preserves that title as a target-owned "
            "heading carrier instead of relying on a shared live parent group"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "source_tag": "P1group",
            "heading_text_preview": inserted_heading_text[:200],
        },
    )
    return True


def extract_uk_structural_payload_ir(
    *,
    effect: UKEffectRecord,
    action: str,
    target_ref: str,
    target: LegalAddress,
    payload_match_target: LegalAddress,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    fallback_target_eid: Callable[[LegalAddress], str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKStructuralPayloadExtraction:
    content_ir: Optional[dict[str, Any]] = None
    actual_el: Optional[ET._Element] = None
    flat_p1para_schedule_insert_lowered = False
    source_structural_payload_matches_target = False
    if extracted_el is None:
        return UKStructuralPayloadExtraction(
            content_ir=content_ir,
            actual_el=actual_el,
            flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
            source_structural_payload_matches_target=source_structural_payload_matches_target,
        )

    flat_p1para_lowering = lower_flat_p1para_schedule_paragraph_insert_payload(
        effect=effect,
        action=action,
        target_ref=target_ref,
        payload_match_target=payload_match_target,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        fallback_target_eid=fallback_target_eid,
        lowering_rejections_out=lowering_rejections_out,
    )
    if flat_p1para_lowering.lowered:
        content_ir = flat_p1para_lowering.content_ir
        flat_p1para_schedule_insert_lowered = True

    actual_el = _select_whole_schedule_element(extracted_el, target)
    if actual_el is None and action == "insert" and _addr_container(target) == "schedule" and len(target.path) > 1:
        schedule_root_target = LegalAddress(path=target.path[:1], special=None)
        actual_el = _select_whole_schedule_element(extracted_el, schedule_root_target)
    if content_ir is None and actual_el is None and action == "insert":
        actual_el = _find_schedule_p1group_wrapper_payload_element(
            extracted_el=extracted_el,
            payload_match_target=payload_match_target,
        )
    if content_ir is None and actual_el is None:
        actual_el = _find_matching_structural_payload_element(
            extracted_el=extracted_el,
            payload_match_target=payload_match_target,
        )

    if content_ir is None and actual_el is None:
        actual_el = _extracted_element_as_payload(
            extracted_el=extracted_el,
            payload_match_target=payload_match_target,
            extracted_text=extracted_text,
        )
    elif content_ir is None and actual_el is not None and actual_el is not extracted_el:
        actual_el = _with_trailing_subordinate_siblings(actual_el, extracted_el)

    if content_ir is None and actual_el is not None:
        parse_context = "schedule" if _addr_container(target) == "schedule" else ""
        is_eur = effect.affected_class == "EuropeanUnionRegulation" or "/eur/" in getattr(effect, "affected_uri", "")
        content_ir = _parse_structural_payload_element(actual_el, parse_context=parse_context, is_eur=is_eur)
        if content_ir is not None:
            direct_text = _direct_payload_text(actual_el)
            if direct_text:
                content_ir["text"] = direct_text
            prepend_inserted_p1group_heading_carrier(
                effect=effect,
                target_ref=target_ref,
                target=target,
                content_ir=content_ir,
                actual_el=actual_el,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
            )
            content_ir = _maybe_lower_inserted_schedule_p1group_crossheading_wrapper(
                content_ir=content_ir,
                actual_el=actual_el,
                target=target,
                effect=effect,
                target_ref=target_ref,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
            )
            source_structural_payload_matches_target = _source_payload_matches_target_leaf(
                content_ir,
                payload_match_target,
            )

    return UKStructuralPayloadExtraction(
        content_ir=content_ir,
        actual_el=actual_el,
        flat_p1para_schedule_insert_lowered=flat_p1para_schedule_insert_lowered,
        source_structural_payload_matches_target=source_structural_payload_matches_target,
    )


def prepare_uk_operation_payload_node(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    content_ir: Optional[dict[str, Any]],
    target_ref: str,
    target: LegalAddress,
    payload_match_target: LegalAddress,
    target_replacement_leaf_override: Optional[str],
    target_replacement_leaf_kind: Optional[str],
    actual_el: Optional[ET._Element],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    allow_payload_identity_synthesis: bool,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKPayloadNodePreparation:
    """Prepare the structural payload node and enforce payload safety gates."""
    payload_node_mut: Optional[UKMutableNode] = _to_mutable_node(content_ir) if content_ir else None
    if (
        payload_node_mut is not None
        and target_replacement_leaf_override
        and target_replacement_leaf_kind
        and payload_node_mut.kind.value == _uk_core_kind_alias_value(target_replacement_leaf_kind)
    ):
        payload_node_mut.label = target_replacement_leaf_override

    if payload_node_mut is not None and curr_action == "insert":
        leaf_kind = _addr_leaf_kind(target) or ""
        leaf_label = _addr_leaf_label(target) or ""
        payload_kind = payload_node_mut.kind.value
        leafish_kinds = {"subsection", "paragraph", "subparagraph", "item", "point"}
        canonical_leaf_kind = _uk_core_kind_alias_value(leaf_kind)
        if (
            leaf_kind
            and leaf_label
            and payload_kind == canonical_leaf_kind
            and not _clean_num(payload_node_mut.label or "")
        ):
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID,
                family="payload_realignment",
                reason_code="insert_payload_blank_label_realigned_to_target_leaf",
                reason=(
                    "UK insert payload has a blank label but its kind matches the "
                    "target leaf kind; the payload label is realigned to the target "
                    "leaf label so the inserted node carries the expected address."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_payload_label": "",
                    "new_payload_label": leaf_label,
                    "payload_kind": payload_kind,
                    "target_leaf_kind": leaf_kind,
                    "target_leaf_label": leaf_label,
                    "strict_disposition": "block",
                    "quirks_disposition": "apply",
                },
            )
            payload_node_mut.label = leaf_label
        if (
            leaf_kind in leafish_kinds
            and payload_kind in leafish_kinds
            and payload_kind != canonical_leaf_kind
            and _clean_num(payload_node_mut.label or "") == _clean_num(leaf_label)
        ):
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=_UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID,
                family="payload_realignment",
                reason_code="insert_payload_kind_realigned_to_canonical_target_leaf_kind",
                reason=(
                    "UK insert payload has a leafish kind that differs from the "
                    "canonical target leaf kind but whose label number matches the "
                    "target leaf label; the payload kind is realigned to the canonical "
                    "target leaf kind so the inserted node has the expected structure."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "original_payload_kind": payload_kind,
                    "new_payload_kind": canonical_leaf_kind,
                    "payload_label": payload_node_mut.label or "",
                    "target_leaf_kind": leaf_kind,
                    "target_leaf_label": leaf_label,
                    "strict_disposition": "block",
                    "quirks_disposition": "apply",
                },
            )
            payload_node_mut.kind = uk_ir_node_kind(leaf_kind)

    if payload_node_mut is not None and curr_action in ("insert", "replace"):
        payload_identity_target = payload_match_target if curr_action == "replace" else target
        payload_node_mut = _synthesize_whole_schedule_payload_descendant_eids(
            payload_node_mut,
            target=payload_identity_target,
            effect=effect,
            lowering_records_out=lowering_rejections_out,
            allow_payload_identity_synthesis=allow_payload_identity_synthesis,
        )
        payload_node_mut = _synthesize_payload_descendant_eids(
            payload_node_mut,
            target=payload_identity_target,
            effect=effect,
            lowering_records_out=lowering_rejections_out,
            allow_payload_identity_synthesis=allow_payload_identity_synthesis,
        )

    if reject_non_substantive_structural_payload(
        effect=effect,
        curr_action=curr_action,
        t_str=target_ref,
        payload_node_mut=payload_node_mut,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return UKPayloadNodePreparation(payload_node=None, skip_effect=True)
    if reject_broad_schedule_flat_replace_payload(
        effect=effect,
        curr_action=curr_action,
        t_str=target_ref,
        target=target,
        payload_node_mut=payload_node_mut,
        actual_el=actual_el,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return UKPayloadNodePreparation(payload_node=None, skip_effect=True)
    if reject_body_section_replace_with_unmatched_schedule_payload(
        effect=effect,
        curr_action=curr_action,
        t_str=target_ref,
        target=target,
        payload_node_mut=payload_node_mut,
        actual_el=actual_el,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    ):
        return UKPayloadNodePreparation(payload_node=None, skip_effect=True)

    return UKPayloadNodePreparation(
        payload_node=payload_node_mut.to_irnode() if payload_node_mut is not None else None,
    )


def _find_matching_structural_payload_element(
    *,
    extracted_el: ET._Element,
    payload_match_target: LegalAddress,
) -> Optional[ET._Element]:
    for am in extracted_el.iter():
        if _tag(am) not in ("BlockAmendment", "InlineAmendment"):
            continue
        for child in am.iter():
            ct = _tag(child)
            if ct not in _STRUCTURAL_PAYLOAD_TAGS:
                continue
            c_num = _direct_structural_num(child)
            target_num = _addr_leaf_label(payload_match_target)
            if not target_num or _clean_num(c_num) == _clean_num(target_num):
                return _with_trailing_subordinate_siblings(child, am)
    return None


def _extracted_element_as_payload(
    *,
    extracted_el: ET._Element,
    payload_match_target: LegalAddress,
    extracted_text: Optional[str],
) -> Optional[ET._Element]:
    if _tag(extracted_el) not in _STRUCTURAL_PAYLOAD_TAGS:
        return None
    target_num = _addr_leaf_label(payload_match_target)
    extracted_num = _direct_structural_num(extracted_el)
    if not target_num or _clean_num(extracted_num) == _clean_num(target_num):
        return extracted_el
    return _retarget_instruction_element_to_target(
        extracted_el,
        payload_match_target,
        extracted_text,
    )


def _parse_structural_payload_element(
    actual_el: ET._Element,
    *,
    parse_context: str,
    is_eur: bool = False,
) -> Optional[dict[str, Any]]:
    tag = _tag(actual_el)
    if tag == "Part":
        return _parse_part(
            actual_el, parse_context, force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag in ("Chapter", "EUChapter"):
        return _parse_chapter(
            actual_el, parse_context, force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag == "Pblock":
        return _parse_pblock(
            actual_el, parse_context, force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag == "P1group":
        return _parse_p1group(
            actual_el, parse_context, force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag in ("Section", "P1", "Article", "Rule", "ConventionRights", "EUSection"):
        return _parse_section(
            actual_el, parse_context, force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag in ("Subsection", "P2"):
        return _parse_p2(
            actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag == "P3":
        return _parse_p3(
            actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag == "P4":
        return _parse_p4(
            actual_el, parse_context or "body", force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    if tag == "Schedule":
        return _parse_schedule_single(
            actual_el, "schedule", force_active=True, pit_date=None, is_eur=is_eur
        ).to_dict()
    return None


_STRUCTURAL_PAYLOAD_TAGS = {
    "Part",
    "Chapter",
    "EUChapter",
    "Pblock",
    "P1group",
    "Section",
    "P1",
    "Article",
    "Rule",
    "Subsection",
    "P2",
    "P3",
    "P4",
    "Schedule",
}

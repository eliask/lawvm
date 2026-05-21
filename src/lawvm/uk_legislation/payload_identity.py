from __future__ import annotations

import re
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.addressing import _addr_container, _addr_field, _canonicalize_eid_tail_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.target_anchors import _fallback_target_eid
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID = (
    "uk_whole_schedule_payload_descendant_eid_synthesis"
)
UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID = "uk_payload_descendant_eid_synthesis"


def _whole_schedule_target_root_eid(target: LegalAddress) -> str:
    if _addr_container(target) != "schedule" or len(target.path) != 1:
        return ""
    schedule_label = _addr_field(target, "schedule")
    if not schedule_label:
        return ""
    return f"schedule-{_clean_num(schedule_label)}"


def _whole_schedule_payload_local_suffix(parent_eid: str, child: UKMutableNode) -> str:
    kind_name = str(child.kind or "").lower()
    raw_label = str(child.label or "").strip()
    clean_label = _clean_num(raw_label).strip("().")
    if kind_name == "crossheading":
        heading_text = " ".join(str(child.text or raw_label).split()).strip()
        heading_slug = re.sub(r"[^a-zA-Z0-9]+", "-", heading_text.lower()).strip("-")
        if heading_slug:
            return f"crossheading-{heading_slug}"
        return ""
    if (
        raw_label
        and kind_name in {"subparagraph", "item", "point"}
        and re.fullmatch(r"[ivxlcdm]+", raw_label, re.IGNORECASE)
    ):
        clean_label = raw_label.lower().strip(".")
    if not clean_label:
        return ""
    if kind_name in {"paragraph", "subparagraph", "subsection", "item", "point", "p2", "p3"}:
        if re.search(r"(?:^|-)paragraph-[^-]+(?:-|$)", parent_eid):
            return clean_label
        return f"paragraph-{clean_label}"
    return f"{kind_name}-{clean_label}"


def _payload_local_suffix(child: UKMutableNode) -> str:
    raw_label = str(child.label or "").strip()
    return _canonicalize_eid_tail_label(raw_label)


def _synthesize_whole_schedule_payload_descendant_eids(
    payload_node: UKMutableNode,
    *,
    target: LegalAddress,
    effect: UKEffectRecord,
    lowering_records_out: Optional[list[dict[str, Any]]],
    allow_payload_identity_synthesis: bool,
) -> UKMutableNode:
    """Own local descendant IDs for whole-schedule payloads before replay.

    This is source-local identity normalization, not oracle alignment: it only
    runs for an explicit single-schedule target and derives descendants from
    the target-owned root EID plus parsed labels.
    """
    if payload_node.kind != IRNodeKind.SCHEDULE and str(payload_node.kind) != "schedule":
        return payload_node
    root_eid = _whole_schedule_target_root_eid(target)
    if not root_eid:
        return payload_node
    existing_root_eid = str(payload_node.attrs.get("eId") or payload_node.attrs.get("id") or "")
    if not existing_root_eid:
        payload_node.attrs["eId"] = root_eid
    else:
        root_eid = existing_root_eid

    if not allow_payload_identity_synthesis:
        if lowering_records_out is not None:
            lowering_records_out.append(
                {
                    "rule_id": UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                    "family": "payload_identity_normalization",
                    "phase": "payload_normalization",
                    "effect_id": effect.effect_id,
                    "affecting_act_id": effect.affecting_act_id,
                    "affected_provisions": effect.affected_provisions,
                    "affecting_provisions": effect.affecting_provisions,
                    "effect_type": effect.effect_type,
                    "target": str(target),
                    "reason": (
                        "Whole-schedule payload has descendants without source EIDs; "
                        "strict lowering did not synthesize local descendant identity"
                    ),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
        return payload_node

    synthesized: list[dict[str, Any]] = []
    used_eids: set[str] = {root_eid}
    skipped_ambiguous = 0
    skipped_duplicate = 0

    def _walk(parent_eid: str, current: UKMutableNode) -> None:
        nonlocal skipped_ambiguous, skipped_duplicate
        for child in current.children:
            child_kind_name = str(child.kind or "").lower()
            existing_eid = str(child.attrs.get("eId") or child.attrs.get("id") or "")
            child_parent_eid = existing_eid or parent_eid
            if existing_eid:
                used_eids.add(existing_eid)
            else:
                suffix = _whole_schedule_payload_local_suffix(parent_eid, child)
                if suffix:
                    child_parent_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{suffix}"
                    if child_parent_eid in used_eids:
                        skipped_duplicate += 1
                        child_parent_eid = parent_eid
                        if child.children:
                            skipped_ambiguous += 1
                        _walk(child_parent_eid, child)
                        continue
                    used_eids.add(child_parent_eid)
                    child.attrs["eId"] = child_parent_eid
                    if child_kind_name == "crossheading":
                        child_parent_eid = parent_eid
                    synthesized.append(
                        {
                            "kind": str(child.kind),
                            "label": child.label,
                            "parent_eid": parent_eid,
                            "after_eid": child_parent_eid,
                        }
                    )
                elif child.children:
                    skipped_ambiguous += 1
            _walk(child_parent_eid, child)

    _walk(root_eid, payload_node)
    if synthesized and lowering_records_out is not None:
        lowering_records_out.append(
            {
                "rule_id": UK_WHOLE_SCHEDULE_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                "family": "payload_identity_normalization",
                "phase": "payload_normalization",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "target": str(target),
                "root_eid": root_eid,
                "synthesized_count": len(synthesized),
                "skipped_ambiguous_count": skipped_ambiguous,
                "skipped_duplicate_count": skipped_duplicate,
                "sample": synthesized[:8],
                "reason": (
                    "Whole-schedule payload descendants lacked source EIDs; "
                    "lowering synthesized deterministic local IDs from the explicit schedule target"
                ),
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            }
        )
    return payload_node


def _synthesize_payload_descendant_eids(
    payload_node: UKMutableNode,
    *,
    target: LegalAddress,
    effect: UKEffectRecord,
    lowering_records_out: Optional[list[dict[str, Any]]],
    allow_payload_identity_synthesis: bool,
) -> UKMutableNode:
    """Own local descendant IDs for non-schedule source-backed payload trees."""
    if str(payload_node.kind).lower() == "schedule":
        return payload_node
    root_eid = str(payload_node.attrs.get("eId") or payload_node.attrs.get("id") or "")
    if not root_eid:
        root_eid = _fallback_target_eid(target)
        if root_eid:
            payload_node.attrs["eId"] = root_eid
    if not root_eid or not payload_node.children:
        return payload_node

    if not allow_payload_identity_synthesis:
        if lowering_records_out is not None:
            lowering_records_out.append(
                {
                    "rule_id": UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                    "family": "payload_identity_normalization",
                    "phase": "payload_normalization",
                    "effect_id": effect.effect_id,
                    "affecting_act_id": effect.affecting_act_id,
                    "affected_provisions": effect.affected_provisions,
                    "affecting_provisions": effect.affecting_provisions,
                    "effect_type": effect.effect_type,
                    "target": str(target),
                    "reason": (
                        "Source-backed payload has descendants without source EIDs; "
                        "strict lowering did not synthesize local descendant identity"
                    ),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
        return payload_node

    synthesized: list[dict[str, Any]] = []
    used_eids: set[str] = {root_eid}
    skipped_duplicate = 0

    def _walk(parent_eid: str, current: UKMutableNode) -> None:
        nonlocal skipped_duplicate
        for child in current.children:
            child_eid = str(child.attrs.get("eId") or child.attrs.get("id") or "")
            child_parent_eid = child_eid or parent_eid
            if child_eid:
                used_eids.add(child_eid)
            else:
                suffix = _payload_local_suffix(child)
                if suffix:
                    child_parent_eid = f"{parent_eid}{'' if parent_eid.endswith('-') else '-'}{suffix}"
                    if child_parent_eid in used_eids:
                        skipped_duplicate += 1
                        child_parent_eid = parent_eid
                    else:
                        used_eids.add(child_parent_eid)
                        child.attrs["eId"] = child_parent_eid
                        synthesized.append(
                            {
                                "kind": str(child.kind),
                                "label": child.label,
                                "parent_eid": parent_eid,
                                "after_eid": child_parent_eid,
                            }
                        )
            _walk(child_parent_eid, child)

    _walk(root_eid, payload_node)
    if synthesized and lowering_records_out is not None:
        lowering_records_out.append(
            {
                "rule_id": UK_PAYLOAD_DESCENDANT_EID_SYNTHESIS_RULE_ID,
                "family": "payload_identity_normalization",
                "phase": "payload_normalization",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "target": str(target),
                "root_eid": root_eid,
                "synthesized_count": len(synthesized),
                "skipped_duplicate_count": skipped_duplicate,
                "sample": synthesized[:8],
                "reason": (
                    "Source-backed payload descendants lacked source EIDs; "
                    "lowering synthesized deterministic local IDs from the explicit target"
                ),
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            }
        )
    return payload_node

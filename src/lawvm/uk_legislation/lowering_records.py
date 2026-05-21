"""UK lowering-phase diagnostic record builders."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from typing import Any, Optional, Sequence

from lawvm.core.ir import IRNode, LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import UKEffectRecord


def _append_uk_effect_lowering_rejection(
    rejections_out: Optional[list[dict[str, Any]]],
    *,
    rule_id: str,
    family: str,
    reason_code: str,
    reason: str,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append a phase-local UK effect lowering rejection when requested."""
    if rejections_out is None:
        return
    extracted_tag = ""
    if extracted_el is not None:
        extracted_tag = extracted_el.tag.rsplit("}", 1)[-1]
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        "family": family,
        "phase": "lowering",
        "effect_id": effect.effect_id,
        "affecting_act_id": effect.affecting_act_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_provisions": effect.affecting_provisions,
        "effect_type": effect.effect_type,
        "reason": reason,
        "reason_code": reason_code,
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "extracted_tag": extracted_tag,
        "has_extracted_source": extracted_el is not None,
    }
    if extracted_text:
        payload["extracted_text_preview"] = " ".join(extracted_text.split())[:500]
    payload.update(detail or {})
    rejections_out.append(payload)


def _append_uk_effect_lowering_observation(
    observations_out: Optional[list[dict[str, Any]]],
    *,
    rule_id: str,
    family: str,
    reason_code: str,
    reason: str,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append a non-blocking phase-local UK effect lowering observation."""
    if observations_out is None:
        return
    extracted_tag = ""
    if extracted_el is not None:
        extracted_tag = extracted_el.tag.rsplit("}", 1)[-1]
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        "family": family,
        "phase": "lowering",
        "effect_id": effect.effect_id,
        "affecting_act_id": effect.affecting_act_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_provisions": effect.affecting_provisions,
        "effect_type": effect.effect_type,
        "reason": reason,
        "reason_code": reason_code,
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "extracted_tag": extracted_tag,
        "has_extracted_source": extracted_el is not None,
    }
    if extracted_text:
        payload["extracted_text_preview"] = " ".join(extracted_text.split())[:500]
    payload.update(detail or {})
    observations_out.append(payload)


def _range_to_container_substitution_detail(
    effect: UKEffectRecord,
    compiled_ops: Sequence[LegalOperation],
) -> dict[str, Any]:
    """Return typed evidence for a blocked UK range-to-container substitution."""
    range_match = re.search(
        r"\bss?\.?\s*(?P<start>[0-9]+[A-Za-z]?)\s*[-\u2013\u2014]\s*(?P<end>[0-9]+[A-Za-z]?)\b",
        effect.effect_type,
        flags=re.I,
    )
    compiled_targets = tuple(str(op.target) for op in compiled_ops)
    compiled_actions = tuple(_action_name(op.action) for op in compiled_ops)
    payloads = tuple(op.payload for op in compiled_ops if op.payload is not None)
    payload_kinds = tuple(payload.kind.value for payload in payloads)
    payload_roots = tuple(_range_to_container_payload_root_summary(payload) for payload in payloads)
    detail: dict[str, Any] = {
        "compiled_actions": compiled_actions,
        "compiled_targets": compiled_targets,
        "payload_kinds": payload_kinds,
        "payload_roots": payload_roots,
        "required_ownership": (
            "source_range",
            "container_payload",
            "lineage_or_migration_events",
            "mutation_boundary",
        ),
        "target_container_ref": effect.affected_provisions,
    }
    if range_match is not None:
        detail.update(
            {
                "source_range_kind": "section",
                "source_range_start": range_match.group("start"),
                "source_range_end": range_match.group("end"),
            }
        )
    return detail


def _range_to_container_payload_root_summary(payload: IRNode) -> dict[str, Any]:
    child_summaries = tuple(
        {
            "kind": child.kind.value,
            "label": child.label or "",
            "eid": str(child.attrs.get("eId") or child.attrs.get("id") or ""),
        }
        for child in payload.children[:12]
    )
    return {
        "kind": payload.kind.value,
        "label": payload.label or "",
        "eid": str(payload.attrs.get("eId") or payload.attrs.get("id") or ""),
        "direct_child_count": len(payload.children),
        "direct_children": child_summaries,
        "truncated_direct_children": len(payload.children) > len(child_summaries),
    }

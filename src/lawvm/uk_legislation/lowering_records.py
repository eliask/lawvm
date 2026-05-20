"""UK lowering-phase diagnostic record builders."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Optional

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

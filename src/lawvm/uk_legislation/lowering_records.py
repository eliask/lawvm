"""UK lowering-phase diagnostic record builders."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from typing import Any, Optional, Sequence

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.ir import IRNode, LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import (
    _COMMENCEMENT_EFFECT_TYPES,
    UKEffectRecord,
    uk_nonstructural_replay_candidate_family,
)
from lawvm.uk_legislation.manual_claim_templates import uk_manual_claim_template_status
from lawvm.uk_legislation.source_adjudication import classify_uk_manual_compile_frontier
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
)


def _extracted_tag(extracted_el: Optional[ET.Element]) -> str:
    if extracted_el is None:
        return ""
    return extracted_el.tag.rsplit("}", 1)[-1]


def _effect_lowering_record_base(
    *,
    rule_id: str,
    family: str,
    reason_code: str,
    reason: str,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    blocking: bool,
) -> dict[str, Any]:
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
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": "record",
        "extracted_tag": _extracted_tag(extracted_el),
        "has_extracted_source": extracted_el is not None,
    }
    if extracted_text:
        payload["extracted_text_preview"] = " ".join(extracted_text.split())[:500]
    return payload


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
    payload = _effect_lowering_record_base(
        rule_id=rule_id,
        family=family,
        reason_code=reason_code,
        reason=reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        blocking=True,
    )
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
    payload = _effect_lowering_record_base(
        rule_id=rule_id,
        family=family,
        reason_code=reason_code,
        reason=reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        blocking=False,
    )
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


def append_structural_no_ops_lowering_rejection(
    effect: UKEffectRecord,
    *,
    structural_for_replay: bool,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    compile_recorded_lowering_rejection: bool,
) -> bool:
    if not structural_for_replay or lowering_rejections_out is None:
        return False
    if compile_recorded_lowering_rejection:
        return False
    if any(
        rejection.get("rule_id") == "uk_effect_lowering_no_ops_rejected"
        and str(rejection.get("effect_id") or "") == str(effect.effect_id or "")
        for rejection in lowering_rejections_out
    ):
        return False
    lowering_rejections_out.append(
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "family": "lowering_filter",
            "phase": "lowering",
            "effect_id": effect.effect_id,
            "affecting_act_id": effect.affecting_act_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_provisions": effect.affecting_provisions,
            "effect_type": effect.effect_type,
            "reason": "UK structural effect lowered to no replay operations",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    )
    return True


def append_source_pathology_filter_lowering_rejections(
    effect: UKEffectRecord,
    *,
    source_pathology: str,
    structural_for_replay: bool,
    compiled_ops: Sequence[LegalOperation],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    """Append blocking lowering records for source pathology filters.

    These filters are shared by replay and effect-inspection tooling. They do
    not repair the row; they make the rejected semantic lane visible.
    """
    if lowering_rejections_out is None:
        return False
    appended = False
    if (
        structural_for_replay
        and source_pathology == "instruction_text_reused_as_payload"
        and any(_action_name(op.action) in {"insert", "replace"} for op in compiled_ops)
        and not any(
            op.witness_rule_id == UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID
            for op in compiled_ops
        )
    ):
        lowering_rejections_out.append(
            {
                "rule_id": "uk_effect_instruction_text_payload_rejected",
                "family": "source_pathology_filter",
                "phase": "lowering",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "reason": "UK effect payload reused instruction text rather than source legal payload",
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "record",
                "source_pathology": source_pathology,
            }
        )
        appended = True
    if source_pathology == "range_to_container_target_unsupported":
        lowering_rejections_out.append(
            {
                "rule_id": "uk_effect_range_to_container_substitution_rejected",
                "family": "source_pathology_filter",
                "phase": "lowering",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "reason": (
                    "UK source substitutes a section range into a container payload; "
                    "lowering must own range replacement and lineage before replay"
                ),
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "record",
                "source_pathology": source_pathology,
                **_range_to_container_substitution_detail(effect, compiled_ops),
            }
        )
        appended = True
    return appended


def append_no_ops_lowering_rejections(
    effect: UKEffectRecord,
    *,
    structural_for_replay: bool,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    compile_recorded_lowering_rejection: bool,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> bool:
    """Append owned lowering rejections for replay-relevant effect rows with no ops."""
    appended = append_structural_no_ops_lowering_rejection(
        effect,
        structural_for_replay=structural_for_replay,
        lowering_rejections_out=lowering_rejections_out,
        compile_recorded_lowering_rejection=compile_recorded_lowering_rejection,
    )
    if structural_for_replay or lowering_rejections_out is None or compile_recorded_lowering_rejection:
        return appended
    nonstructural_candidate_family = uk_nonstructural_replay_candidate_family(
        effect,
        applicability_mode=applicability_mode,
    )
    if nonstructural_candidate_family:
        lowering_rejections_out.append(
            {
                "rule_id": "uk_effect_nonstructural_lowering_no_ops_rejected",
                "family": "lowering_filter",
                "phase": "lowering",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "reason": "UK nonstructural effect row may be replayable but lowered to no replay operations",
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "record",
                "nonstructural_replay_candidate_family": nonstructural_candidate_family,
            }
        )
        return True
    if (
        (effect.effect_type or "").strip().lower() not in _COMMENCEMENT_EFFECT_TYPES
        and effect.is_applicable_for_replay(applicability_mode=applicability_mode)
    ):
        lowering_rejections_out.append(
            {
                "rule_id": "uk_effect_nonstructural_unsupported_no_ops_observed",
                "family": "nonstructural_replay_observation",
                "phase": "lowering",
                "effect_id": effect.effect_id,
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "effect_type": effect.effect_type,
                "reason": (
                    "UK applicable nonstructural effect row is not replay-supported "
                    "under the selected replay lens and lowered to no replay operations"
                ),
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            }
        )
        return True
    return appended


def mark_nonreplay_lowering_rejections_nonblocking(
    effect: UKEffectRecord,
    *,
    structural_for_replay: bool,
    applicability_mode: str,
    lowering_rejections: list[dict[str, Any]],
    start_index: int,
) -> bool:
    """Mark compile-time lowering diagnostics nonblocking when replay cannot use the row.

    `compile_effect_to_ir_ops` is intentionally source-local and may emit a
    blocking rejection before the caller has applied the replay lens. The caller
    owns this phase-boundary reclassification so nonstructural, unsupported rows
    remain visible without masquerading as replay blockers.
    """
    if structural_for_replay:
        return False
    if uk_nonstructural_replay_candidate_family(effect, applicability_mode=applicability_mode):
        return False
    if start_index >= len(lowering_rejections):
        return False
    changed = False
    for rejection in lowering_rejections[start_index:]:
        if not is_blocking_compile_record(rejection):
            continue
        rejection["blocking"] = False
        rejection["strict_disposition"] = "record"
        rejection["nonblocking_reclassification_rule_id"] = (
            "uk_effect_nonreplay_lowering_observed"
        )
        rejection["replay_relevance"] = "nonstructural_unsupported"
        rejection["reclassification_reason"] = (
            "The selected replay lens does not support or admit this "
            "nonstructural effect row; the lowering diagnostic is evidence, "
            "not a replay blocker."
        )
        changed = True
    return changed


def _lowering_record_rule_ids(rows: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(str(row.get("rule_id") or "") for row in rows if row.get("rule_id"))


def append_manual_compile_frontier_diagnostic(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    source_pathology: str,
    extracted_tag: str,
    extracted_text: str,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    lowering_rejection_start_index: int,
    compiled_op_count: int,
    replay_applicable: bool,
    structural_for_replay: bool,
) -> None:
    """Append the manual compile frontier classification for one UK effect row."""
    if diagnostics_out is None:
        return
    current_lowering_rejections = (
        tuple(lowering_rejections_out[lowering_rejection_start_index:])
        if lowering_rejections_out is not None
        else ()
    )
    manual_frontier = classify_uk_manual_compile_frontier(
        effect_type=effect.effect_type or "",
        source_pathology=source_pathology,
        extracted_tag=extracted_tag or "",
        extracted_text=extracted_text,
        lowering_rejections=current_lowering_rejections,
        compiled_op_count=compiled_op_count,
        replay_applicable=replay_applicable,
        structural_for_replay=structural_for_replay,
    )
    record = {
        "rule_id": "uk_manual_compile_frontier_classified",
        "family": "manual_compile_frontier",
        "phase": "lowering",
        "effect_id": str(effect.effect_id or ""),
        "affecting_act_id": str(effect.affecting_act_id or ""),
        "affected_provisions": str(effect.affected_provisions or ""),
        "affecting_provisions": str(effect.affecting_provisions or ""),
        "effect_type": str(effect.effect_type or ""),
        "manual_compile_status": manual_frontier["status"],
        "manual_compile_rule_id": manual_frontier["rule_id"],
        "manual_compile_reason": manual_frontier["reason"],
        "lowering_rule_ids": _lowering_record_rule_ids(current_lowering_rejections),
        "blocking_lowering_rule_ids": _lowering_record_rule_ids(
            tuple(
                row
                for row in current_lowering_rejections
                if is_blocking_compile_record(row)
            )
        ),
        "source_pathology": source_pathology or "",
        "structural_for_replay": structural_for_replay,
        "replay_applicable": replay_applicable,
        "compiled_op_count": compiled_op_count,
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }
    template_status = uk_manual_claim_template_status(
        manual_compile_status=record["manual_compile_status"],
        manual_compile_rule_id=record["manual_compile_rule_id"],
    )
    if template_status:
        record["suggested_claim_template_status"] = template_status
    diagnostics_out.append(record)


def append_pit_date_filter_rejection(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    effective_date: str,
    pit_date: str,
) -> None:
    """Record that a UK effect is later than the requested point-in-time."""
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": "uk_effect_pit_date_filter_rejected",
            "family": "temporal_filter",
            "phase": "lowering",
            "effect_id": effect.effect_id,
            "affecting_act_id": str(effect.affecting_act_id or ""),
            "affected_provisions": effect.affected_provisions,
            "affecting_provisions": effect.affecting_provisions,
            "effect_type": effect.effect_type,
            "effective_date": effective_date,
            "pit_date": pit_date,
            "reason": "UK effect effective date is later than requested point-in-time date",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    )


def append_metadata_only_selection_rejection(
    rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
) -> None:
    """Record that the selected UK replay regime excludes metadata-only effects."""
    if rejections_out is None:
        return
    rejections_out.append(
        {
            "rule_id": "uk_effect_metadata_only_selection_rejected",
            "family": "applicability_filter",
            "phase": "lowering",
            "effect_id": effect.effect_id,
            "affecting_act_id": effect.affecting_act_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_provisions": effect.affecting_provisions,
            "effect_type": effect.effect_type,
            "metadata_only": True,
            "reason": "UK replay regime excludes metadata-only effect rows",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    )


def append_source_pathology_classified_diagnostic(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    source_pathology: str,
    structural_for_replay: bool,
    replay_applicable: bool,
    compiled_op_count: int,
) -> None:
    """Record the source-pathology classification for a lowered UK effect."""
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": "uk_effect_source_pathology_classified",
            "family": "source_pathology",
            "phase": "lowering",
            "effect_id": str(effect.effect_id or ""),
            "affecting_act_id": str(effect.affecting_act_id or ""),
            "affected_provisions": str(effect.affected_provisions or ""),
            "affecting_provisions": str(effect.affecting_provisions or ""),
            "effect_type": str(effect.effect_type or ""),
            "source_pathology": source_pathology or "",
            "structural_for_replay": structural_for_replay,
            "replay_applicable": replay_applicable,
            "compiled_op_count": compiled_op_count,
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    )


def append_replay_applicability_filter_diagnostic(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    compiled_ops: Sequence[LegalOperation],
    structural_for_replay: bool,
    replay_applicable: bool,
    applicability_mode: str,
) -> None:
    """Record that a compiled UK effect is excluded from replay by applicability."""
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        {
            "rule_id": "uk_effect_replay_applicability_filter_rejected",
            "family": "applicability_filter",
            "phase": "lowering",
            "effect_id": str(effect.effect_id or ""),
            "affecting_act_id": str(effect.affecting_act_id or ""),
            "affected_provisions": str(effect.affected_provisions or ""),
            "affecting_provisions": str(effect.affecting_provisions or ""),
            "effect_type": str(effect.effect_type or ""),
            "compiled_op_count": len(compiled_ops),
            "compiled_op_ids": [str(op.op_id or "") for op in compiled_ops],
            "compiled_op_actions": [_action_name(op.action) for op in compiled_ops],
            "structural_for_replay": structural_for_replay,
            "replay_applicable": replay_applicable,
            "nonstructural_replay_family": uk_nonstructural_replay_candidate_family(
                effect,
                applicability_mode=applicability_mode,
            ),
            "reason": "UK effect compiled to operations but replay applicability excludes the effect",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
    )


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

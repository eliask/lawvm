"""UK projections into the shared frontier work-item contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.source_witness import source_witness_from_mapping


def uk_frontier_work_item_from_manual_frontier_row(
    row: Mapping[str, Any],
) -> FrontierWorkItem:
    """Project a UK manual-frontier row as a non-executable work item."""
    template = _mapping(row.get("suggested_claim_template"))
    manual_frontier = _mapping(row.get("manual_compile_frontier"))
    target_context = _mapping(row.get("target_context"))
    source_witness = _first_mapping(
        row.get("affecting_source_witness"),
        row.get("source"),
        row.get("source_witness"),
    )
    statute_id = str(row.get("statute_id") or "")
    effect_id = str(row.get("effect_id") or "")
    frontier_family = str(
        row.get("current_manual_compile_rule_id")
        or row.get("manual_compile_rule_id")
        or row.get("validator_current_manual_compile_rule_id")
        or row.get("rule_id")
        or ""
    )
    frontier_status = str(
        row.get("current_manual_compile_status")
        or row.get("manual_compile_status")
        or row.get("validator_status")
        or ""
    )
    source_artifact_id = str(
        row.get("source_artifact_id")
        or row.get("affecting_act_id")
        or row.get("affecting_uri")
        or row.get("affected_uri")
        or statute_id
    )
    source_unit_id = str(
        row.get("source_unit_id")
        or effect_id
        or row.get("rule_id")
        or frontier_family
    )
    detail = {
        "statute_id": statute_id,
        "effect_id": effect_id,
        "source_pathology": str(
            row.get("current_source_pathology") or row.get("source_pathology") or ""
        ),
        "manual_compile_reason": str(
            row.get("current_manual_compile_reason")
            or row.get("manual_compile_reason")
            or manual_frontier.get("reason")
            or ""
        ),
        "suggested_claim_template_status": str(
            row.get("suggested_claim_template_status") or ""
        ),
        "claim_status": str(row.get("claim_status") or ""),
        "validator_status": str(row.get("validator_status") or ""),
        "lowering_rule_ids": _first_string_tuple(
            row.get("current_lowering_rule_ids"),
            row.get("manual_compile_lowering_rule_ids"),
            row.get("validator_current_lowering_rule_ids"),
            manual_frontier.get("lowering_rule_ids"),
        ),
        "blocking_lowering_rule_ids": _first_string_tuple(
            row.get("current_blocking_lowering_rule_ids"),
            row.get("manual_compile_blocking_lowering_rule_ids"),
            row.get("validator_current_blocking_lowering_rule_ids"),
            manual_frontier.get("blocking_lowering_rule_ids"),
            _mapping(row.get("blocking_lowering_rejection_rule_counts")).keys(),
        ),
        "compiled_op_count": _nonnegative_int(row.get("compiled_op_count")),
        "compare_shape": str(row.get("compare_shape") or target_context.get("compare_shape") or ""),
    }
    normalized_source_witness = source_witness_from_mapping(
        source_witness,
        default_role=_source_witness_role(source_witness),
        default_artifact_id=source_artifact_id,
        default_source_unit_id=source_unit_id,
    ).to_dict()
    return FrontierWorkItem(
        work_item_id=str(
            row.get("work_item_id") or f"uk-frontier-{source_artifact_id}-{source_unit_id}"
        ),
        jurisdiction="uk",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=normalized_source_witness,
        owner_phase=str(
            row.get("current_owner_phase")
            or row.get("owner_phase")
            or row.get("manual_compile_owner_phase")
            or ""
        ),
        frontier_family=frontier_family,
        frontier_status=frontier_status,
        candidate_operation_family=str(
            template.get("action_family") or row.get("work_item_kind") or ""
        ),
        candidate_targets=_candidate_targets(row),
        guidance_refs=_string_tuple(template.get("guidance_refs")),
        required_claim_kind=str(row.get("claim_kind") or "semantic_compile"),
        required_validator_checks=_string_tuple(template.get("required_validator_checks")),
        required_proofs=_string_tuple(row.get("required_proofs")),
        safe_default=str(row.get("safe_default") or ""),
        forbidden_shortcuts=_string_tuple(row.get("forbidden_shortcuts")),
        executable=_bool_flag(row.get("executable")),
        replay_authorized=_bool_flag(row.get("replay_authorized")),
        authorization_status=str(row.get("authorization_status") or ""),
        detail=detail,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping) and value:
            return value
    return {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    if not isinstance(value, Mapping) and hasattr(value, "__iter__"):
        return tuple(str(item) for item in value if str(item))
    return ()


def _first_string_tuple(*values: Any) -> tuple[str, ...]:
    for value in values:
        items = _string_tuple(value)
        if items:
            return items
    return ()


def _bool_flag(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    return 0


def _source_witness_role(source_witness: Mapping[str, Any]) -> str:
    if source_witness.get("source_sha256") or source_witness.get("affecting_act_id"):
        return "affecting_source"
    if source_witness.get("text_preview"):
        return "source_preview"
    if source_witness:
        return "source_context"
    return "unspecified_source"


def _candidate_targets(row: Mapping[str, Any]) -> tuple[str, ...]:
    target_context = _mapping(row.get("target_context"))
    targets = (
        *_string_tuple(row.get("affected_provisions")),
        *_string_tuple(target_context.get("affected_provisions")),
        *_string_tuple(target_context.get("resolver_eids")),
    )
    return tuple(dict.fromkeys(targets))

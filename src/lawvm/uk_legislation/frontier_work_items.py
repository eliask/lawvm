"""UK projections into the shared frontier work-item contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lawvm.core.frontier_work_item import FrontierWorkItem


def uk_frontier_work_item_from_manual_frontier_row(
    row: Mapping[str, Any],
) -> FrontierWorkItem:
    """Project a UK manual-frontier row as a non-executable work item."""
    template = _mapping(row.get("suggested_claim_template"))
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
    return FrontierWorkItem(
        work_item_id=str(
            row.get("work_item_id") or f"uk-frontier-{source_artifact_id}-{source_unit_id}"
        ),
        jurisdiction="uk",
        source_artifact_id=source_artifact_id,
        source_unit_id=source_unit_id,
        source_witness=source_witness,
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
        executable=bool(row.get("executable")),
        replay_authorized=bool(row.get("replay_authorized")),
        authorization_status=str(row.get("authorization_status") or ""),
        detail={
            "statute_id": statute_id,
            "effect_id": effect_id,
            "source_pathology": str(
                row.get("current_source_pathology") or row.get("source_pathology") or ""
            ),
            "suggested_claim_template_status": str(
                row.get("suggested_claim_template_status") or ""
            ),
        },
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
    return ()


def _candidate_targets(row: Mapping[str, Any]) -> tuple[str, ...]:
    target_context = _mapping(row.get("target_context"))
    targets = (
        *_string_tuple(row.get("affected_provisions")),
        *_string_tuple(target_context.get("affected_provisions")),
        *_string_tuple(target_context.get("resolver_eids")),
    )
    return tuple(dict.fromkeys(targets))

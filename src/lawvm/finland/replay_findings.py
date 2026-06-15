"""Finding and metadata projection helpers for Finland replay.

This module is intentionally narrow: it owns conversion of replay/runtime
diagnostics into LawVM ``Finding`` rows and JSON-compatible report payloads. It
does not decide replay semantics.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Dict, Optional, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import check_invariants as _check_tree_invariants
from lawvm.finland.replay_notices import replay_print as _replay_print


def _replay_product_invariant_finding(
    *,
    violation: str,
    source_statute: str,
    message: str = "Replay/materialization product invariant violated.",
) -> Finding:
    """Build Finland replay-product invariant findings before compatibility projection."""
    return Finding(
        kind="APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": message,
            "violation": violation,
            "barrier_code": "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        },
    )


def _apply_mutation_boundary_violation_finding(
    *,
    violation: str,
    source_statute: str,
) -> Finding:
    """Build the replay finding emitted for apply mutation accounting violations."""
    barrier_code = violation.split(" ", 1)[0]
    return Finding(
        kind=barrier_code,
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": "Apply mutation boundary accounting violated.",
            "violation": violation,
            "barrier_code": barrier_code,
        },
    )


def _apply_mutation_invariant_report_finding(
    *,
    report: Any,
    result: Any,
    source_statute: str,
) -> Finding | None:
    """Project registered mutation-accounting results as native replay findings."""
    spec = get_finding_spec(result.code)
    if spec is None:
        return None

    finding = _apply_mutation_boundary_violation_finding(
        violation=result.as_violation_string(),
        source_statute=source_statute,
    )
    detail = {
        **dict(finding.detail),
        "op_id": report.op_id,
        "helper": report.helper,
        "outcome": report.outcome,
        "touched_paths": [list(path) for path in report.touched_paths],
        "changed_paths": [list(path) for path in report.changed_paths],
        "allowed_effect_region_paths": [list(path) for path in report.allowed_effect_region_paths],
        "declared_recovery_paths": [list(path) for path in report.declared_recovery_paths],
        "declared_recovery_rule_ids": list(report.declared_recovery_rule_ids),
        "declared_migration_paths": [list(path) for path in report.declared_migration_paths],
        "declared_migration_rule_ids": list(report.declared_migration_rule_ids),
        "permitted_paths": [list(path) for path in report.permitted_paths],
        "covered_changed_paths": [list(path) for path in report.covered_changed_paths],
        "unexplained_changed_paths": [list(path) for path in report.unexplained_changed_paths],
        "allowed_non_target_paths": [list(path) for path in report.allowed_non_target_paths],
        "out_of_scope_paths": [list(path) for path in result.out_of_scope_paths],
        "matched_allowance_rule_ids": list(result.matched_allowance_rule_ids),
        "path_set_invariant_holds": report.path_set_invariant_holds,
    }
    return Finding(
        kind=finding.kind,
        role=finding.role,
        stage=finding.stage,
        blocking=finding.blocking,
        source_statute=finding.source_statute,
        detail=detail,
    )


def _apply_mutation_fallback_event_finding(
    *,
    event: Any,
    fallback_kind: str,
) -> Finding | None:
    """Project governed apply fallback tags as native replay findings."""
    spec = get_finding_spec(fallback_kind)
    if spec is None:
        return None

    fallback_tags = tuple(str(tag).strip() for tag in event.used_fallback_tags if str(tag).strip())
    if fallback_kind not in fallback_tags:
        return None
    reason_tag = next((tag for tag in fallback_tags if tag != fallback_kind), "")
    reason_code = str(event.reason_code or "").strip() or reason_tag
    resolved_target_path = [list(path) for path in event.resolved_target_path] if event.resolved_target_path else []
    message = "Apply used a governed fallback path."
    if fallback_kind == "APPLY.LEGACY_DISPATCH_FALLBACK":
        message = "Apply fell back to legacy field-based dispatch."
    elif fallback_kind == "APPLY.RELABEL_SKIPPED":
        message = "Typed relabel intent was skipped for a governed reason."
    elif fallback_kind == "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK":
        message = "Section path resolution fell back to a live unique match after scoped lookup failed."
    elif fallback_kind == "APPLY.SAME_WAVE_MIGRATION_REBASE":
        message = "Section path resolution followed same-wave migration lineage to the current address."
    detail = {
        "message": message,
        "helper": event.helper,
        "reason_tag": reason_tag,
        "reason_code": reason_code,
        "used_fallback_tags": list(fallback_tags),
        "failure_reason": str(event.failure_reason or ""),
        "resolved_target_path": resolved_target_path,
        "op_id": event.op_id,
        "source_statute": event.source_statute,
    }
    if spec.role == "observation":
        return Finding(
            kind=fallback_kind,
            role="observation",
            stage=spec.phase,
            detail=detail,
            source_statute=event.source_statute,
            blocking=False,
        )
    if spec.role == "barrier":
        return Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage=spec.phase,
            detail={**detail, "barrier_code": fallback_kind},
            source_statute=event.source_statute,
            blocking=True,
        )
    return Finding(
        kind=fallback_kind,
        role="obligation",
        stage=spec.phase,
        detail=detail,
        source_statute=event.source_statute,
        blocking=spec.default_enforcement in ("strict_fail", "hard_fail"),
    )


def _serialize_apply_mutation_event(event: Any) -> dict[str, object]:
    payload = asdict(event)
    if not payload.get("declared_allowances"):
        payload.pop("declared_allowances", None)
    return payload


def _serialize_apply_mutation_invariant_report(report: Any) -> dict[str, object]:
    return asdict(report)


def _serialize_observed_write_audit(audit: ObservedWriteAudit) -> dict[str, object]:
    return asdict(audit)


def _structural_dedup_applied_finding(
    *,
    phase: str,
    source_statute: str,
    duplicates: Optional[list[dict[str, str]]] = None,
) -> Finding:
    """Build the observation emitted when the global dedup backstop modifies a tree."""
    return Finding(
        kind="APPLY.GLOBAL_LABEL_DEDUP_APPLIED",
        role="observation",
        stage="apply",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Global same-kind+label dedup backstop modified the replay tree.",
            "phase": phase,
            "duplicates": list(duplicates or ()),
        },
    )


def _pre_dedup_duplicate_details(tree: IRNode) -> list[dict[str, str]]:
    """Extract duplicate-label details from a tree before the dedup backstop runs."""
    details: list[dict[str, str]] = []
    duplicate_re = re.compile(r"duplicate\s+(\w+):(\S+)", re.IGNORECASE)
    for violation in _check_tree_invariants(tree):
        last_slash = violation.rfind("/")
        search_from = last_slash + 1 if last_slash != -1 else 0
        sep = violation.find(": ", search_from)
        if sep == -1:
            continue
        path = violation[:sep].strip()
        message = violation[sep + 2 :].strip()
        match = duplicate_re.search(message)
        if match is None:
            continue
        details.append(
            {
                "path": path,
                "kind": match.group(1),
                "label": match.group(2),
            }
        )
    return details


def _strict_rejected_source_pathology_finding(
    pathology: SourcePathology,
    *,
    stage: str,
    fallback_source_statute: str = "",
) -> Finding:
    """Build the blocking finding for strict-profile source pathology rejection."""
    return Finding(
        kind="APPLY.SOURCE_PATHOLOGY_DETECTED",
        role="obligation",
        stage=stage,
        blocking=True,
        source_statute=pathology.source_statute or fallback_source_statute,
        detail={
            **pathology.scope_detail(),
            "code": pathology.code,
            "detail": dict(pathology.detail),
            "message": f"Strict profile rejected a suspicious non-literal source path: {pathology.code}",
        },
    )


def _base_observation_to_finding(obs_dict: Dict[str, object]) -> Optional[Finding]:
    """Convert a base observation dict to a Finding object.

    Base observations from T1b (BASE_UNNUMBERED_PARAGRAPH_PEER, LABEL_EID_DIVERGENCE)
    are collected during statute parsing and added to elaboration_observations.
    This converts them to Finding objects for the findings ledger.
    """
    obs_kind = str(obs_dict.get("kind", "")).strip()
    source_statute = str(obs_dict.get("source_statute", "")).strip()
    raw_detail = obs_dict.get("detail")
    detail_dict: dict[str, Any] = {}
    if isinstance(raw_detail, dict):
        for k, v in raw_detail.items():
            detail_dict[str(k)] = v
    stage = str(obs_dict.get("stage", "base_source_analysis")).strip()

    if not obs_kind:
        return None

    spec = get_finding_spec(obs_kind)
    if spec is None:
        return None

    return Finding(
        kind=obs_kind,
        role="observation",
        stage=stage,
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": f"Base statute observation: {obs_kind}",
            **detail_dict,
        },
    )


def _emit_structural_dedup_warning(
    *,
    phase: str,
    before_ir: IRNode,
    after_ir: IRNode,
    source_statute: str,
    replay_findings: list[Finding],
    replay_meta_out: Optional[Dict[str, object]],
) -> IRNode:
    """Surface a warning whenever the global dedup backstop modifies the tree."""
    if after_ir is before_ir:
        return after_ir

    duplicate_details = _pre_dedup_duplicate_details(before_ir)
    _replay_print(
        f"WARNING structural dedup: {phase} same-kind+label duplicates were removed"
    )
    if replay_meta_out is not None:
        dedup_warnings = replay_meta_out.setdefault("structural_dedup_warnings", [])
        warning_payload: dict[str, object] = {
            "phase": phase,
            "message": "Global same-kind+label dedup backstop modified the replay tree.",
        }
        if duplicate_details:
            warning_payload["duplicates"] = duplicate_details
        cast(list[dict[str, object]], dedup_warnings).append(warning_payload)
    replay_findings.append(
        _structural_dedup_applied_finding(
            phase=phase,
            source_statute=source_statute,
            duplicates=duplicate_details,
        )
    )
    return after_ir

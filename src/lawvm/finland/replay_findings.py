"""Finding and metadata projection helpers for Finland replay.

This module is intentionally narrow: it owns conversion of replay/runtime
diagnostics into LawVM ``Finding`` rows and JSON-compatible report payloads. It
does not decide replay semantics.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.mutation_accounting import MutationAccountingResult, MutationInvariantReport
from lawvm.core.mutation_events import DeclaredMutationAllowance, MutationEvent
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.tree_ops import iter_tree_invariant_violations
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
    elif fallback_kind == "APPLY.RESOLVER_BINDING_CONTRACT_ERROR":
        message = "Apply target resolver binding instrumentation violated its contract."
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


def _serialize_declared_mutation_allowance(allowance: DeclaredMutationAllowance) -> dict[str, object]:
    return {
        "kind": allowance.kind,
        "paths": allowance.paths,
        "rule_id": allowance.rule_id,
        "note": allowance.note,
    }


def _serialize_apply_mutation_event(event: MutationEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "op_id": event.op_id,
        "source_statute": event.source_statute,
        "action": event.action,
        "helper": event.helper,
        "outcome": event.outcome,
        "resolved_target_path": event.resolved_target_path,
        "parent_path": event.parent_path,
        "declared_allowances": tuple(
            _serialize_declared_mutation_allowance(allowance)
            for allowance in event.declared_allowances
        ),
        "consumed_paths": event.consumed_paths,
        "created_paths": event.created_paths,
        "removed_paths": event.removed_paths,
        "replaced_paths": event.replaced_paths,
        "renumbered_paths": event.renumbered_paths,
        "placeholder_created_paths": event.placeholder_created_paths,
        "placeholder_consumed_paths": event.placeholder_consumed_paths,
        "used_fallback_tags": event.used_fallback_tags,
        "failure_reason": event.failure_reason,
        "reason_code": event.reason_code,
    }
    if not payload.get("declared_allowances"):
        payload.pop("declared_allowances", None)
    return payload


def _serialize_mutation_accounting_result(result: MutationAccountingResult) -> dict[str, object]:
    return {
        "code": result.code,
        "op_id": result.op_id,
        "helper": result.helper,
        "touched_count": result.touched_count,
        "allowed_roots": result.allowed_roots,
        "out_of_scope_paths": result.out_of_scope_paths,
        "allowed_paths": result.allowed_paths,
        "matched_allowance_rule_ids": result.matched_allowance_rule_ids,
    }


def _serialize_apply_mutation_invariant_report(report: MutationInvariantReport) -> dict[str, object]:
    return {
        "op_id": report.op_id,
        "helper": report.helper,
        "outcome": report.outcome,
        "touched_paths": report.touched_paths,
        "changed_paths": report.changed_paths,
        "allowed_roots": report.allowed_roots,
        "allowed_effect_region_paths": report.allowed_effect_region_paths,
        "declared_allowance_paths": report.declared_allowance_paths,
        "declared_recovery_paths": report.declared_recovery_paths,
        "declared_recovery_rule_ids": report.declared_recovery_rule_ids,
        "declared_migration_paths": report.declared_migration_paths,
        "declared_migration_rule_ids": report.declared_migration_rule_ids,
        "permitted_paths": report.permitted_paths,
        "covered_changed_paths": report.covered_changed_paths,
        "unexplained_changed_paths": report.unexplained_changed_paths,
        "allowed_non_target_paths": report.allowed_non_target_paths,
        "out_of_scope_paths": report.out_of_scope_paths,
        "matched_allowance_rule_ids": report.matched_allowance_rule_ids,
        "path_set_invariant_holds": report.path_set_invariant_holds,
        "results": tuple(_serialize_mutation_accounting_result(result) for result in report.results),
    }


def _serialize_observed_write_audit(audit: ObservedWriteAudit) -> dict[str, object]:
    return asdict(audit)


def timeline_version_dedupe_finding(
    *,
    source_statute: str,
    address: str,
    effective: str,
    enacted: str,
    variant_kind: str,
    witness_rule_id: str,
    removed_count: int,
) -> Finding:
    """Build the observation emitted when owned timeline dedupe collapses ledger rows."""
    return Finding(
        kind="REPLAY.TIMELINE_VERSION_DEDUPE",
        role="observation",
        stage="replay",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Same-source timeline bucket carried competing version rows; "
                "an owned dedupe rule collapsed redundant ledger entries "
                "before PIT materialization."
            ),
            "address": address,
            "effective": effective,
            "enacted": enacted,
            "variant_kind": variant_kind,
            "witness_rule_id": witness_rule_id,
            "removed_count": removed_count,
        },
    )


def fold_timeline_backfill_finding(
    *,
    source_statute: str,
    address: str,
    effective: str,
    witness_rule_id: str,
) -> Finding:
    """Build the observation emitted when fold-owned content is grafted into timelines."""
    return Finding(
        kind="REPLAY.FOLD_TIMELINE_BACKFILL",
        role="observation",
        stage="replay",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Replay fold carried a section without timeline authority; "
                "a fold-owned snapshot was grafted before PIT materialization."
            ),
            "address": address,
            "effective": effective,
            "witness_rule_id": witness_rule_id,
        },
    )


def editorial_repeal_notice_substring_finding(
    *,
    source_statute: str,
    kind: str,
    label: str,
    clause_text: str,
    witness_rule_id: str,
) -> Finding:
    """Build the observation emitted when a repeal notice is recognised by substring.

    The placeholder-restoration guard preferred the typed
    ``lawvm_repeal_placeholder`` attr but fell back to a case-insensitive
    ``kumottu`` scan of materialized text because no typed marker yet owns
    "the consolidation text itself declares this provision repealed"
    (leak-ledger rank 15; AGENTS §1.11–§1.12). The clause snippet is embedded so
    triaging the residual never requires re-running materialization.
    """
    return Finding(
        kind="REPLAY.EDITORIAL_REPEAL_NOTICE_SUBSTRING",
        role="observation",
        stage="replay",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Replay-fold repeal-placeholder restoration skipped a node whose "
                "materialized text already shows an editorial repeal notice. No "
                "typed marker owns this case yet, so a residual 'kumottu' "
                "substring scan decided it; recorded as a witness instead of a "
                "silent surface-predicate decision."
            ),
            "kind": kind,
            "label": label,
            "clause_text": clause_text,
            "witness_rule_id": witness_rule_id,
        },
    )


def cited_version_snapshot_drop_finding(
    *,
    source_statute: str,
    op_id: str,
    drop_source_statute: str,
    effective: str,
    target_path: tuple[str, ...],
    witness_rule_id: str,
) -> Finding:
    """Build the observation emitted when a covered cited-version snapshot op is dropped.

    A later amending act emitted a stale ancestor snapshot for an item-scoped
    cited-version clause; the cited act's same-effective snapshot structurally
    covers it, so the stale ``REPLACE``/``INSERT`` op is removed from the
    materialized-state op stream. The drop alters materialized text-state, so it
    is surfaced as a typed observation rather than left silent (leak-ledger
    rank 2; AGENTS §0/§4). The dropped op's identity is embedded so triaging the
    drop never requires re-running materialization.
    """
    return Finding(
        kind="REPLAY.CITED_VERSION_SNAPSHOT_DROP",
        role="observation",
        stage="replay",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "A later amending act's item-scoped cited-version clause emitted a "
                "stale ancestor snapshot op; the cited act's same-effective snapshot "
                "structurally covers it, so the stale op was dropped from the "
                "materialized-state op stream. Recorded as a witness so the "
                "legal-state op drop is never a silent omission."
            ),
            "op_id": op_id,
            "drop_source_statute": drop_source_statute,
            "effective": effective,
            "target_path": list(target_path),
            "witness_rule_id": witness_rule_id,
        },
    )


def xml_ingest_observation_finding(
    *,
    source_statute: str,
    obs_dict: Dict[str, object],
) -> Optional[Finding]:
    """Build a governed Finding for one witnessed XML->IR ingest event.

    The base statute's XML->IR ingest can silently drop an unknown childless
    source element, assign a positional (non-intrinsic) label, hit an unmapped
    tag, or re-parent/merge tree shape on a structural-repair heuristic. The
    production parse (``StatuteContext.from_xml``) witnesses these through an
    ``_IngestSink`` and folds them into ``ingest_metadata`` -- but that channel
    is only read by tests, so a dropped/guessed/repaired source child reached no
    certificate or findings ledger. Each witnessed ``IngestObservation`` dict
    (envelope ``kind``/``family``/``phase`` + witness detail) is projected here
    into the governed SCAN.XML_INGEST_* observation finding so the ingest-boundary
    event reaches the same production findings ledger every other base-statute
    witness flows through. This is witness threading only: the set of
    dropped/guessed/repaired children is unchanged.
    """
    obs_kind = str(obs_dict.get("kind", "")).strip()
    if not obs_kind:
        return None
    spec = get_finding_spec(obs_kind)
    if spec is None or spec.role != "observation":
        return None
    detail: dict[str, Any] = {
        "message": f"XML->IR ingest observation: {obs_kind}",
    }
    for key, value in obs_dict.items():
        if str(key) == "kind":
            continue
        detail[str(key)] = value
    return Finding(
        kind=obs_kind,
        role="observation",
        stage="xml_ingest",
        blocking=False,
        source_statute=source_statute,
        detail=detail,
    )


def materialized_provisions_wrapper_projection_finding(
    *,
    source_statute: str,
    witness_rule_id: str,
) -> Finding:
    """Build the observation emitted when a materialized provisions wrapper is projected."""
    return Finding(
        kind="REPLAY.MATERIALIZED_PROVISIONS_WRAPPER_PROJECTED",
        role="observation",
        stage="replay_products",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Materialized PIT product projected fold-owned "
                "statuteProvisionsWrapper children into legal topology."
            ),
            "witness_rule_id": witness_rule_id,
        },
    )


def materialized_attachments_wrapper_split_finding(
    *,
    source_statute: str,
    moved_section_labels: tuple[str, ...],
    witness_rule_id: str,
) -> Finding:
    """Build the observation emitted when operative sections are split out of appendices."""
    return Finding(
        kind="REPLAY.MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT",
        role="observation",
        stage="replay_products",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": (
                "Materialized PIT product carried fold-owned operative sections under "
                "an attachments wrapper; sections were split into a provisions wrapper."
            ),
            "witness_rule_id": witness_rule_id,
            "source_shape": "attachments_hcontainer_with_fold_owned_direct_sections",
            "target_wrapper": "statuteProvisionsWrapper",
            "moved_section_labels": moved_section_labels,
        },
    )


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
    for violation in iter_tree_invariant_violations(
        tree,
        families={"duplicate_label"},
    ):
        if violation.child_kind is None or violation.label is None:
            continue
        details.append(
            {
                "path": violation.path_text,
                "kind": violation.child_kind,
                "label": violation.label,
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

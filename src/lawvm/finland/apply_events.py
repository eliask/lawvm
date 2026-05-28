"""Mutation-event helpers for Finland apply.

This module isolates the apply-time observability surface from the execution
helpers so `apply.py` can shrink without changing the public compatibility
surface.  The helpers here are pure formatting/recording utilities: they do
not read live replay state or mutate IR.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from lawvm.core.mutation_boundary import partition_changed_paths, path_has_prefix
from lawvm.core.tree_ops import Path
from lawvm.finland.ops import AmendmentOp, ResolvedOp


@dataclass(frozen=True)
class ApplyMutationEvent:
    op_id: str
    source_statute: str
    action: str
    helper: str
    outcome: str
    resolved_target_path: tuple[tuple[str, str], ...] | None = None
    parent_path: tuple[tuple[str, str], ...] | None = None
    declared_allowances: tuple["DeclaredMutationAllowance", ...] = ()
    consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    removed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    renumbered_paths: tuple[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]], ...] = ()
    placeholder_created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    placeholder_consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    used_fallback_tags: tuple[str, ...] = ()
    failure_reason: str = ""
    reason_code: str = ""


@dataclass(frozen=True)
class ApplyMutationAccountingResult:
    code: str
    op_id: str
    helper: str
    touched_count: int = 0
    allowed_roots: tuple[tuple[tuple[str, str], ...], ...] = ()
    out_of_scope_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    allowed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()

    def as_violation_string(self) -> str:
        base = f"{self.code} op_id={self.op_id or '<missing>'} helper={self.helper}"
        if self.code in {
            "REPLAY_SKIPPED_OP_MUTATED_TREE",
            "REPLAY_FAILED_OP_MUTATED_TREE",
            "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        }:
            return f"{base} touched={self.touched_count}"
        return base


@dataclass(frozen=True)
class ApplyMutationInvariantReport:
    op_id: str
    helper: str
    outcome: str
    touched_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    changed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    allowed_roots: tuple[tuple[tuple[str, str], ...], ...] = ()
    allowed_effect_region_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    declared_allowance_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    declared_recovery_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    declared_recovery_rule_ids: tuple[str, ...] = ()
    declared_migration_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    declared_migration_rule_ids: tuple[str, ...] = ()
    permitted_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    covered_changed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    unexplained_changed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    allowed_non_target_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    out_of_scope_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()
    path_set_invariant_holds: bool = True
    results: tuple[ApplyMutationAccountingResult, ...] = ()


@dataclass(frozen=True)
class DeclaredMutationAllowance:
    kind: str
    paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    rule_id: str = ""
    note: str = ""


def _path_to_tuple(path: Path | None) -> tuple[tuple[str, str], ...] | None:
    if path is None:
        return None
    return tuple((str(kind), str(label)) for kind, label in path)


def _resolved_target_path_for_event(
    op: AmendmentOp,
    sec_path: Path | None,
) -> tuple[tuple[str, str], ...] | None:
    if sec_path is None:
        return None
    resolved: tuple[tuple[str, str], ...] = tuple(sec_path)
    if op.target_paragraph is not None:
        resolved = resolved + (("subsection", str(op.target_paragraph)),)
    if op.target_item is not None:
        resolved = resolved + (("paragraph", str(op.target_item)),)
    if op.target_special is not None:
        resolved = resolved + (("special", str(op.target_special)),)
    return _path_to_tuple(resolved)


def _resolved_target_path_for_rop_event(
    rop: ResolvedOp,
    sec_path: Path | None,
) -> tuple[tuple[str, str], ...] | None:
    """Resolve mutation-event target identity from late-waist fields."""
    resolved_address_path = _target_address_path_for_rop_event(rop)
    if resolved_address_path is not None:
        return resolved_address_path
    return _path_to_tuple(sec_path)


def _target_address_path_for_rop_event(
    rop: ResolvedOp,
    path_hint: Path | None = None,
) -> tuple[tuple[str, str], ...] | None:
    """Resolve mutation-event identity from the effective ResolvedOp target address."""
    address = rop.resolved_target_address
    if address is not None and address.path:
        return _path_to_tuple(address.path)
    return _path_to_tuple(path_hint)


def _emit_apply_mutation_event(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    op: AmendmentOp,
    helper: str,
    outcome: str,
    resolved_target_path: tuple[tuple[str, str], ...] | None = None,
    parent_path: tuple[tuple[str, str], ...] | None = None,
    declared_allowances: tuple[DeclaredMutationAllowance, ...] = (),
    consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    created_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    removed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    renumbered_paths: tuple[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]], ...] = (),
    placeholder_created_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    placeholder_consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    used_fallback_tags: tuple[str, ...] = (),
    failure_reason: str = "",
    reason_code: str = "",
) -> None:
    if mutation_events_out is None:
        return
    mutation_events_out.append(
        ApplyMutationEvent(
            op_id=op.op_id,
            source_statute=op.source_statute,
            action=op.op_type.lower(),
            helper=helper,
            outcome=outcome,
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=declared_allowances,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            removed_paths=removed_paths,
            replaced_paths=replaced_paths,
            renumbered_paths=renumbered_paths,
            placeholder_created_paths=placeholder_created_paths,
            placeholder_consumed_paths=placeholder_consumed_paths,
            used_fallback_tags=used_fallback_tags,
            failure_reason=failure_reason,
            reason_code=reason_code,
        )
    )


def _emit_apply_mutation_event_for_rop(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    rop: ResolvedOp,
    helper: str,
    outcome: str,
    resolved_target_path: tuple[tuple[str, str], ...] | None = None,
    parent_path: tuple[tuple[str, str], ...] | None = None,
    declared_allowances: tuple[DeclaredMutationAllowance, ...] = (),
    consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    created_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    removed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    renumbered_paths: tuple[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]], ...] = (),
    placeholder_created_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    placeholder_consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = (),
    used_fallback_tags: tuple[str, ...] = (),
    failure_reason: str = "",
    reason_code: str = "",
) -> None:
    """Emit a mutation event from late-waist fields without consulting AmendmentOp."""
    if mutation_events_out is None:
        return
    effective_declared_allowances = declared_allowances
    if not effective_declared_allowances and rop.uses_uncovered_body_recovery:
        allowed_paths = tuple(
            path
            for path in (resolved_target_path, parent_path)
            if path
        )
        effective_declared_allowances = (
            DeclaredMutationAllowance(
                kind="recovery",
                paths=allowed_paths,
                rule_id="uncovered_body_recovery",
            ),
        )
    mutation_events_out.append(
        ApplyMutationEvent(
            op_id=rop.op_id or "",
            source_statute=rop.resolved_source_statute,
            action=rop.resolved_action_type.lower(),
            helper=helper,
            outcome=outcome,
            resolved_target_path=resolved_target_path,
            parent_path=parent_path,
            declared_allowances=effective_declared_allowances,
            consumed_paths=consumed_paths,
            created_paths=created_paths,
            removed_paths=removed_paths,
            replaced_paths=replaced_paths,
            renumbered_paths=renumbered_paths,
            placeholder_created_paths=placeholder_created_paths,
            placeholder_consumed_paths=placeholder_consumed_paths,
            used_fallback_tags=used_fallback_tags,
            failure_reason=failure_reason,
            reason_code=reason_code,
        )
    )


def _emit_legacy_dispatch_fallback_event(
    mutation_events_out: Optional[List[ApplyMutationEvent]],
    *,
    rop: ResolvedOp,
    helper: str,
    reason_tag: str,
    failure_reason: str,
    reason_code: str = "",
    path_hint: Path | None = None,
) -> None:
    """Record that typed apply fell back to legacy field-based dispatch."""
    _emit_apply_mutation_event_for_rop(
        mutation_events_out,
        rop=rop,
        helper=helper,
        outcome="skipped",
        resolved_target_path=_target_address_path_for_rop_event(rop, path_hint),
        used_fallback_tags=("APPLY.LEGACY_DISPATCH_FALLBACK", reason_tag),
        failure_reason=failure_reason,
        reason_code=reason_code,
    )


def _path_is_descendant_or_same(
    ancestor: tuple[tuple[str, str], ...] | None,
    candidate: tuple[tuple[str, str], ...],
) -> bool:
    if ancestor is None:
        return True
    return path_has_prefix(candidate, (ancestor,))


def _event_touched_paths(event: ApplyMutationEvent) -> tuple[tuple[tuple[str, str], ...], ...]:
    touched: list[tuple[tuple[str, str], ...]] = []
    touched.extend(event.consumed_paths)
    touched.extend(event.created_paths)
    touched.extend(event.removed_paths)
    touched.extend(event.replaced_paths)
    touched.extend(event.placeholder_created_paths)
    touched.extend(event.placeholder_consumed_paths)
    for old_path, new_path in event.renumbered_paths:
        touched.append(old_path)
        touched.append(new_path)
    deduped: list[tuple[tuple[str, str], ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in touched:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _event_declared_allowance_paths(event: ApplyMutationEvent) -> tuple[tuple[tuple[str, str], ...], ...]:
    allowed: list[tuple[tuple[str, str], ...]] = []
    for allowance in event.declared_allowances:
        allowed.extend(path for path in allowance.paths if path)
    deduped: list[tuple[tuple[str, str], ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in allowed:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _allowance_paths_by_kind(
    event: ApplyMutationEvent,
    *kinds: str,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    wanted = set(kinds)
    paths: list[tuple[tuple[str, str], ...]] = []
    for allowance in event.declared_allowances:
        if allowance.kind not in wanted:
            continue
        paths.extend(path for path in allowance.paths if path)
    deduped: list[tuple[tuple[str, str], ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def _allowance_rule_ids_by_kind(
    event: ApplyMutationEvent,
    *kinds: str,
) -> tuple[str, ...]:
    wanted = set(kinds)
    rule_ids: list[str] = []
    seen: set[str] = set()
    for allowance in event.declared_allowances:
        if allowance.kind not in wanted:
            continue
        rule_id = str(allowance.rule_id or "").strip()
        if not rule_id or rule_id in seen:
            continue
        seen.add(rule_id)
        rule_ids.append(rule_id)
    return tuple(rule_ids)


def _matching_declared_allowance_rule_ids(
    event: ApplyMutationEvent,
    path: tuple[tuple[str, str], ...],
) -> tuple[str, ...]:
    matched: list[str] = []
    for allowance in event.declared_allowances:
        if not allowance.rule_id:
            continue
        if any(_path_is_descendant_or_same(allowed_path, path) for allowed_path in allowance.paths if allowed_path):
            matched.append(allowance.rule_id)
    deduped: list[str] = []
    seen: set[str] = set()
    for rule_id in matched:
        if rule_id in seen:
            continue
        seen.add(rule_id)
        deduped.append(rule_id)
    return tuple(deduped)


def _dedupe_paths(
    paths: Iterable[tuple[tuple[str, str], ...]],
) -> tuple[tuple[tuple[str, str], ...], ...]:
    deduped: list[tuple[tuple[str, str], ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return tuple(deduped)


def build_apply_mutation_invariant_reports(
    events: Iterable[ApplyMutationEvent],
) -> tuple[ApplyMutationInvariantReport, ...]:
    """Return typed mutation-boundary reports for replay apply events."""
    reports: list[ApplyMutationInvariantReport] = []
    for event in events:
        touched_paths = _event_touched_paths(event)
        results: list[ApplyMutationAccountingResult] = []
        allowed_roots: tuple[tuple[tuple[str, str], ...], ...] = ()
        declared_allowance_paths = _event_declared_allowance_paths(event)
        declared_recovery_paths = _allowance_paths_by_kind(event, "recovery", "recovery_path")
        declared_recovery_rule_ids = _allowance_rule_ids_by_kind(event, "recovery", "recovery_path")
        declared_migration_paths = _allowance_paths_by_kind(event, "migration", "migration_path")
        declared_migration_rule_ids = _allowance_rule_ids_by_kind(event, "migration", "migration_path")
        allowed_effect_region_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        permitted_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        covered_changed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        unexplained_changed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        allowed_non_target_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        out_of_scope_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
        matched_allowance_rule_ids: tuple[str, ...] = ()
        path_set_invariant_holds = True
        if event.outcome == "skipped":
            if touched_paths:
                results.append(
                    ApplyMutationAccountingResult(
                        code="REPLAY_SKIPPED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif event.outcome == "failed":
            if touched_paths:
                results.append(
                    ApplyMutationAccountingResult(
                        code="REPLAY_FAILED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif event.outcome == "applied":
            if not touched_paths:
                results.append(
                    ApplyMutationAccountingResult(
                        code="REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
                        op_id=event.op_id,
                        helper=event.helper,
                    )
                )
            else:
                if event.helper in {"apply_op", "_apply_legacy_dispatch"}:
                    allowed_roots = tuple(
                        path
                        for path in (
                            event.resolved_target_path,
                            event.parent_path if event.action in {"insert", "move"} else None,
                        )
                        if path is not None
                    )
                else:
                    allowed_roots = tuple(
                        path for path in (event.resolved_target_path, event.parent_path) if path is not None
                    )
                allowed_effect_region_paths = allowed_roots
                if not allowed_roots:
                    results.append(
                        ApplyMutationAccountingResult(
                            code="REPLAY_APPLY_BOUNDARY_UNRESOLVED",
                            op_id=event.op_id,
                            helper=event.helper,
                        )
                    )
                else:
                    permitted_paths = _dedupe_paths(
                        (
                            *allowed_effect_region_paths,
                            *declared_recovery_paths,
                            *declared_migration_paths,
                        )
                    )
                    partition = partition_changed_paths(touched_paths, permitted_paths)
                    allowed_non_target_list = [
                        path
                        for path in partition.covered_changed_paths
                        if not path_has_prefix(path, allowed_roots)
                    ]
                    covered_changed_paths = partition.covered_changed_paths
                    unexplained_changed_paths = partition.unexplained_changed_paths
                    allowed_non_target_paths = tuple(allowed_non_target_list)
                    out_of_scope_paths = unexplained_changed_paths
                    path_set_invariant_holds = not out_of_scope_paths
                    if allowed_non_target_paths:
                        matched_rule_ids: list[str] = []
                        for path in allowed_non_target_paths:
                            matched_rule_ids.extend(_matching_declared_allowance_rule_ids(event, path))
                        deduped_rule_ids: list[str] = []
                        seen_rule_ids: set[str] = set()
                        for rule_id in matched_rule_ids:
                            if rule_id in seen_rule_ids:
                                continue
                            seen_rule_ids.add(rule_id)
                            deduped_rule_ids.append(rule_id)
                        matched_allowance_rule_ids = tuple(deduped_rule_ids)
                        results.append(
                            ApplyMutationAccountingResult(
                                code="REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED",
                                op_id=event.op_id,
                                helper=event.helper,
                                touched_count=len(allowed_non_target_paths),
                                allowed_roots=allowed_roots,
                                allowed_paths=allowed_non_target_paths,
                                matched_allowance_rule_ids=matched_allowance_rule_ids,
                            )
                        )
                    if out_of_scope_paths:
                        results.append(
                            ApplyMutationAccountingResult(
                                code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
                                op_id=event.op_id,
                                helper=event.helper,
                                touched_count=len(out_of_scope_paths),
                                allowed_roots=allowed_roots,
                                out_of_scope_paths=out_of_scope_paths,
                            )
                        )
        reports.append(
            ApplyMutationInvariantReport(
                op_id=event.op_id,
                helper=event.helper,
                outcome=event.outcome,
                touched_paths=touched_paths,
                changed_paths=touched_paths,
                allowed_roots=allowed_roots,
                allowed_effect_region_paths=allowed_effect_region_paths,
                declared_allowance_paths=declared_allowance_paths,
                declared_recovery_paths=declared_recovery_paths,
                declared_recovery_rule_ids=declared_recovery_rule_ids,
                declared_migration_paths=declared_migration_paths,
                declared_migration_rule_ids=declared_migration_rule_ids,
                permitted_paths=permitted_paths,
                covered_changed_paths=covered_changed_paths,
                unexplained_changed_paths=unexplained_changed_paths,
                allowed_non_target_paths=allowed_non_target_paths,
                out_of_scope_paths=out_of_scope_paths,
                matched_allowance_rule_ids=matched_allowance_rule_ids,
                path_set_invariant_holds=path_set_invariant_holds,
                results=tuple(results),
            )
        )
    return tuple(reports)


def analyze_apply_mutation_accounting(
    events: Iterable[ApplyMutationEvent],
) -> list[ApplyMutationAccountingResult]:
    """Return typed passive replay-lint results for apply mutation accounting."""
    return analyze_apply_mutation_invariant_reports(
        build_apply_mutation_invariant_reports(events)
    )


def analyze_apply_mutation_invariant_reports(
    reports: Iterable[ApplyMutationInvariantReport],
) -> list[ApplyMutationAccountingResult]:
    """Return typed passive replay-lint results from typed invariant reports."""
    violations: list[ApplyMutationAccountingResult] = []
    for report in reports:
        violations.extend(report.results)
    return violations


def check_apply_mutation_accounting(events: Iterable[ApplyMutationEvent]) -> list[str]:
    """Return passive replay-lint violations for apply mutation accounting."""
    return check_apply_mutation_invariant_reports(
        build_apply_mutation_invariant_reports(events)
    )


def check_apply_mutation_invariant_reports(
    reports: Iterable[ApplyMutationInvariantReport],
) -> list[str]:
    """Return passive replay-lint violations from typed invariant reports."""
    return [
        result.as_violation_string()
        for result in analyze_apply_mutation_invariant_reports(reports)
        if result.code
        in {
            "REPLAY_SKIPPED_OP_MUTATED_TREE",
            "REPLAY_FAILED_OP_MUTATED_TREE",
            "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
            "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
            "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        }
    ]


__all__ = [
    "ApplyMutationEvent",
    "ApplyMutationAccountingResult",
    "ApplyMutationInvariantReport",
    "DeclaredMutationAllowance",
    "build_apply_mutation_invariant_reports",
    "analyze_apply_mutation_invariant_reports",
    "analyze_apply_mutation_accounting",
    "check_apply_mutation_invariant_reports",
    "check_apply_mutation_accounting",
    "_path_to_tuple",
    "_resolved_target_path_for_event",
    "_resolved_target_path_for_rop_event",
    "_target_address_path_for_rop_event",
    "_emit_apply_mutation_event",
    "_emit_apply_mutation_event_for_rop",
    "_emit_legacy_dispatch_fallback_event",
]

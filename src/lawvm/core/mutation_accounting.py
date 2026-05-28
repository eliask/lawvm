"""Shared mutation-event accounting.

This module turns mutation-site observations into passive invariant reports.
It does not decide whether a frontend may recover; it only classifies whether
recorded tree touches are missing, unresolved, target-covered, allowance-
covered, or outside declared mutation regions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from lawvm.core.mutation_boundary import TreePaths
from lawvm.core.mutation_events import (
    MutationEvent,
    build_mutation_event_path_set_report,
    mutation_event_touched_paths,
)

MUTATION_ACCOUNTING_HARD_CODES = frozenset(
    {
        "REPLAY_SKIPPED_OP_MUTATED_TREE",
        "REPLAY_FAILED_OP_MUTATED_TREE",
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
    }
)


@dataclass(frozen=True)
class MutationAccountingResult:
    code: str
    op_id: str
    helper: str
    touched_count: int = 0
    allowed_roots: TreePaths = ()
    out_of_scope_paths: TreePaths = ()
    allowed_paths: TreePaths = ()
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
class MutationInvariantReport:
    op_id: str
    helper: str
    outcome: str
    touched_paths: TreePaths = ()
    changed_paths: TreePaths = ()
    allowed_roots: TreePaths = ()
    allowed_effect_region_paths: TreePaths = ()
    declared_allowance_paths: TreePaths = ()
    declared_recovery_paths: TreePaths = ()
    declared_recovery_rule_ids: tuple[str, ...] = ()
    declared_migration_paths: TreePaths = ()
    declared_migration_rule_ids: tuple[str, ...] = ()
    permitted_paths: TreePaths = ()
    covered_changed_paths: TreePaths = ()
    unexplained_changed_paths: TreePaths = ()
    allowed_non_target_paths: TreePaths = ()
    out_of_scope_paths: TreePaths = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()
    path_set_invariant_holds: bool = True
    results: tuple[MutationAccountingResult, ...] = ()


def build_mutation_invariant_reports(
    events: Iterable[MutationEvent],
    *,
    parent_boundary_helpers: frozenset[str] = frozenset({"apply_op", "_apply_legacy_dispatch"}),
    parent_boundary_actions: frozenset[str] = frozenset({"insert", "move"}),
) -> tuple[MutationInvariantReport, ...]:
    """Return typed mutation-boundary reports for replay/apply events.

    ``parent_boundary_helpers`` lets frontends preserve legacy accounting for
    broad dispatch helpers that should only admit parent paths for child-list
    actions. Direct helper events normally use both resolved target and parent.
    """

    reports: list[MutationInvariantReport] = []
    for event in events:
        touched_paths = mutation_event_touched_paths(event)
        path_report = build_mutation_event_path_set_report(event, ())
        results: list[MutationAccountingResult] = []
        allowed_roots: TreePaths = ()
        declared_allowance_paths = path_report.declared_allowance_paths
        declared_recovery_paths = path_report.declared_recovery_paths
        declared_recovery_rule_ids = path_report.declared_recovery_rule_ids
        declared_migration_paths = path_report.declared_migration_paths
        declared_migration_rule_ids = path_report.declared_migration_rule_ids
        allowed_effect_region_paths: TreePaths = ()
        permitted_paths: TreePaths = ()
        covered_changed_paths: TreePaths = ()
        unexplained_changed_paths: TreePaths = ()
        allowed_non_target_paths: TreePaths = ()
        out_of_scope_paths: TreePaths = ()
        matched_allowance_rule_ids: tuple[str, ...] = ()
        path_set_invariant_holds = True
        if event.outcome == "skipped":
            if touched_paths:
                results.append(
                    MutationAccountingResult(
                        code="REPLAY_SKIPPED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif event.outcome == "failed":
            if touched_paths:
                results.append(
                    MutationAccountingResult(
                        code="REPLAY_FAILED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif event.outcome == "applied":
            if not touched_paths:
                results.append(
                    MutationAccountingResult(
                        code="REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
                        op_id=event.op_id,
                        helper=event.helper,
                    )
                )
            else:
                allowed_roots = _event_allowed_roots(
                    event,
                    parent_boundary_helpers=parent_boundary_helpers,
                    parent_boundary_actions=parent_boundary_actions,
                )
                allowed_effect_region_paths = allowed_roots
                path_report = build_mutation_event_path_set_report(event, allowed_effect_region_paths)
                declared_allowance_paths = path_report.declared_allowance_paths
                declared_recovery_paths = path_report.declared_recovery_paths
                declared_recovery_rule_ids = path_report.declared_recovery_rule_ids
                declared_migration_paths = path_report.declared_migration_paths
                declared_migration_rule_ids = path_report.declared_migration_rule_ids
                if not allowed_roots:
                    results.append(
                        MutationAccountingResult(
                            code="REPLAY_APPLY_BOUNDARY_UNRESOLVED",
                            op_id=event.op_id,
                            helper=event.helper,
                        )
                    )
                else:
                    permitted_paths = path_report.permitted_paths
                    covered_changed_paths = path_report.covered_changed_paths
                    unexplained_changed_paths = path_report.unexplained_changed_paths
                    allowed_non_target_paths = path_report.allowed_non_target_paths
                    out_of_scope_paths = unexplained_changed_paths
                    path_set_invariant_holds = path_report.path_set_invariant_holds
                    if allowed_non_target_paths:
                        matched_allowance_rule_ids = path_report.matched_allowance_rule_ids
                        results.append(
                            MutationAccountingResult(
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
                            MutationAccountingResult(
                                code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
                                op_id=event.op_id,
                                helper=event.helper,
                                touched_count=len(out_of_scope_paths),
                                allowed_roots=allowed_roots,
                                out_of_scope_paths=out_of_scope_paths,
                            )
                        )
        reports.append(
            MutationInvariantReport(
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


def analyze_mutation_accounting(
    events: Iterable[MutationEvent],
) -> list[MutationAccountingResult]:
    """Return typed passive replay-lint results for mutation accounting."""

    return analyze_mutation_invariant_reports(build_mutation_invariant_reports(events))


def analyze_mutation_invariant_reports(
    reports: Iterable[MutationInvariantReport],
) -> list[MutationAccountingResult]:
    """Return typed passive replay-lint results from typed invariant reports."""

    violations: list[MutationAccountingResult] = []
    for report in reports:
        violations.extend(report.results)
    return violations


def check_mutation_accounting(events: Iterable[MutationEvent]) -> list[str]:
    """Return passive replay-lint violations for mutation accounting."""

    return check_mutation_invariant_reports(build_mutation_invariant_reports(events))


def check_mutation_invariant_reports(
    reports: Iterable[MutationInvariantReport],
) -> list[str]:
    """Return passive replay-lint violations from typed invariant reports."""

    return [
        result.as_violation_string()
        for result in analyze_mutation_invariant_reports(reports)
        if result.code in MUTATION_ACCOUNTING_HARD_CODES
    ]


def _event_allowed_roots(
    event: MutationEvent,
    *,
    parent_boundary_helpers: frozenset[str],
    parent_boundary_actions: frozenset[str],
) -> TreePaths:
    if event.helper in parent_boundary_helpers:
        return tuple(
            path
            for path in (
                event.resolved_target_path,
                event.parent_path if event.action in parent_boundary_actions else None,
            )
            if path is not None
        )
    return tuple(path for path in (event.resolved_target_path, event.parent_path) if path is not None)


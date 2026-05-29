"""Shared mutation-event accounting.

This module turns mutation-site observations into passive invariant reports.
It does not decide whether a frontend may recover; it only classifies whether
recorded tree touches are missing, unresolved, target-covered, allowance-
covered, or outside declared mutation regions.
"""

from __future__ import annotations

from collections.abc import Iterable as IterableABC
from dataclasses import dataclass
from typing import Iterable, cast

from lawvm.core.mutation_boundary import TreePath, TreePaths, validate_tree_path
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
MUTATION_ACCOUNTING_RESULT_CODES = MUTATION_ACCOUNTING_HARD_CODES | frozenset(
    {
        "REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED",
    }
)

APPLIED_MUTATION_OUTCOMES = frozenset(
    {
        "applied",
        "inserted_node",
        "removed_node",
        "renumbered_node",
        "replaced_node",
        "schedule_list_entries_repealed",
        "schedule_list_entry_inserted",
        "schedule_list_entry_replaced",
        "schedule_table_rows_inserted",
        "table_column_inserted",
        "table_rows_inserted",
        "table_rows_replaced",
        "whole_act_repealed",
    }
)
FAILED_MUTATION_OUTCOMES = frozenset({"failed"})
SKIPPED_MUTATION_OUTCOMES = frozenset({"skipped"})


def mutation_event_outcome_family(outcome: str) -> str:
    """Classify frontend-specific mutation outcomes for shared accounting.

    The original ``MutationEvent.outcome`` remains the evidence surface. This
    adapter only lets shared invariant checks reason over specific applied
    outcomes without forcing frontends to erase their local outcome labels.
    """

    normalized = str(outcome or "")
    if normalized in APPLIED_MUTATION_OUTCOMES:
        return "applied"
    if normalized in FAILED_MUTATION_OUTCOMES:
        return "failed"
    if normalized in SKIPPED_MUTATION_OUTCOMES:
        return "skipped"
    return "unknown"


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

    def __post_init__(self) -> None:
        if self.code not in MUTATION_ACCOUNTING_RESULT_CODES:
            raise ValueError(f"MutationAccountingResult.code is not a known mutation-accounting code: {self.code!r}")
        if not isinstance(self.op_id, str):
            raise ValueError("MutationAccountingResult.op_id must be a string")
        if not isinstance(self.helper, str):
            raise ValueError("MutationAccountingResult.helper must be a string")
        if not isinstance(self.touched_count, int) or isinstance(self.touched_count, bool) or self.touched_count < 0:
            raise ValueError("MutationAccountingResult.touched_count must be a non-negative int")
        object.__setattr__(
            self,
            "allowed_roots",
            _normalize_tree_paths("MutationAccountingResult.allowed_roots", self.allowed_roots),
        )
        object.__setattr__(
            self,
            "out_of_scope_paths",
            _normalize_tree_paths("MutationAccountingResult.out_of_scope_paths", self.out_of_scope_paths),
        )
        object.__setattr__(
            self,
            "allowed_paths",
            _normalize_tree_paths("MutationAccountingResult.allowed_paths", self.allowed_paths),
        )
        object.__setattr__(
            self,
            "matched_allowance_rule_ids",
            _normalize_rule_ids(
                "MutationAccountingResult.matched_allowance_rule_ids",
                self.matched_allowance_rule_ids,
            ),
        )

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

    def __post_init__(self) -> None:
        if not isinstance(self.op_id, str):
            raise ValueError("MutationInvariantReport.op_id must be a string")
        if not isinstance(self.helper, str):
            raise ValueError("MutationInvariantReport.helper must be a string")
        if not isinstance(self.outcome, str):
            raise ValueError("MutationInvariantReport.outcome must be a string")
        for field_name, paths in (
            ("touched_paths", self.touched_paths),
            ("changed_paths", self.changed_paths),
            ("allowed_roots", self.allowed_roots),
            ("allowed_effect_region_paths", self.allowed_effect_region_paths),
            ("declared_allowance_paths", self.declared_allowance_paths),
            ("declared_recovery_paths", self.declared_recovery_paths),
            ("declared_migration_paths", self.declared_migration_paths),
            ("permitted_paths", self.permitted_paths),
            ("covered_changed_paths", self.covered_changed_paths),
            ("unexplained_changed_paths", self.unexplained_changed_paths),
            ("allowed_non_target_paths", self.allowed_non_target_paths),
            ("out_of_scope_paths", self.out_of_scope_paths),
        ):
            object.__setattr__(
                self,
                field_name,
                _normalize_tree_paths(f"MutationInvariantReport.{field_name}", paths),
            )
        object.__setattr__(
            self,
            "declared_recovery_rule_ids",
            _normalize_rule_ids("MutationInvariantReport.declared_recovery_rule_ids", self.declared_recovery_rule_ids),
        )
        object.__setattr__(
            self,
            "declared_migration_rule_ids",
            _normalize_rule_ids("MutationInvariantReport.declared_migration_rule_ids", self.declared_migration_rule_ids),
        )
        object.__setattr__(
            self,
            "matched_allowance_rule_ids",
            _normalize_rule_ids("MutationInvariantReport.matched_allowance_rule_ids", self.matched_allowance_rule_ids),
        )
        if not isinstance(self.path_set_invariant_holds, bool):
            raise ValueError("MutationInvariantReport.path_set_invariant_holds must be a bool")
        results = tuple(self.results)
        if not all(isinstance(result, MutationAccountingResult) for result in results):
            raise ValueError("MutationInvariantReport.results must contain MutationAccountingResult records")
        object.__setattr__(self, "results", results)


def _normalize_tree_paths(field_name: str, paths: Iterable[Iterable[object]]) -> TreePaths:
    normalized: list[TreePath] = []
    for index, path in enumerate(paths):
        if isinstance(path, str):
            raise ValueError(f"{field_name}[{index}] must be a tree path, not a string")
        if not isinstance(path, IterableABC):
            raise ValueError(f"{field_name}[{index}] must be a tree path")
        tree_steps: list[tuple[object, ...]] = []
        for step_index, step in enumerate(path):
            if isinstance(step, str):
                raise ValueError(f"{field_name}[{index}] step {step_index} must be a path step, not a string")
            if not isinstance(step, IterableABC):
                raise ValueError(f"{field_name}[{index}] step {step_index} must be a path step")
            tree_steps.append(tuple(cast(Iterable[object], step)))
        tree_path = cast(TreePath, tuple(tree_steps))
        issues = validate_tree_path(tree_path, field_name=f"{field_name}[{index}]")
        if issues:
            raise ValueError("; ".join(issues))
        normalized.append(tree_path)
    return tuple(normalized)


def _normalize_rule_ids(field_name: str, rule_ids: Iterable[object]) -> tuple[str, ...]:
    normalized = tuple(rule_ids)
    if not all(isinstance(rule_id, str) for rule_id in normalized):
        raise ValueError(f"{field_name} must contain strings")
    return cast(tuple[str, ...], normalized)


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
        outcome_family = mutation_event_outcome_family(event.outcome)
        if outcome_family == "skipped":
            if touched_paths:
                results.append(
                    MutationAccountingResult(
                        code="REPLAY_SKIPPED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif outcome_family == "failed":
            if touched_paths:
                results.append(
                    MutationAccountingResult(
                        code="REPLAY_FAILED_OP_MUTATED_TREE",
                        op_id=event.op_id,
                        helper=event.helper,
                        touched_count=len(touched_paths),
                    )
                )
        elif outcome_family == "applied":
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

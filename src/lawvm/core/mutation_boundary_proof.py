"""Shared mutation-boundary proof projection.

This module projects passive mutation-boundary accounting into a typed proof
object. It does not authorize replay; callers decide what proof status is
required before promoting evidence to execution.
"""

from __future__ import annotations

from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, cast

from lawvm.core.frozen_values import freeze_mapping
from lawvm.core.mutation_accounting import (
    MUTATION_ACCOUNTING_HARD_CODES,
    MutationInvariantReport,
)
from lawvm.core.mutation_boundary import (
    TreePaths,
    tree_path_to_diagnostic_string,
    validate_tree_path,
)

MutationBoundaryProofStatus = Literal[
    "proved",
    "proved_with_allowance",
    "unresolved",
    "violated",
]

_VALID_STATUSES = frozenset(MutationBoundaryProofStatus.__args__)
_UNRESOLVED_RESULT_CODES = frozenset(
    {
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    }
)


@dataclass(frozen=True, slots=True)
class MutationBoundaryProof:
    """Typed proof surface for changed-path containment.

    A proved boundary means the observed changed paths are covered by the
    declared target/effect region plus declared allowance, recovery, or
    migration paths. It is a proof/reporting object, not replay authority.
    """

    proof_id: str
    jurisdiction: str
    materialization_surface: str
    operation_id: str
    owner_phase: str
    rule_id: str
    status: MutationBoundaryProofStatus
    helper: str = ""
    outcome: str = ""
    selected_target_paths: TreePaths = ()
    allowed_mutation_regions: TreePaths = ()
    changed_paths: TreePaths = ()
    covered_changed_paths: TreePaths = ()
    unexplained_changed_paths: TreePaths = ()
    declared_allowance_paths: TreePaths = ()
    declared_recovery_paths: TreePaths = ()
    declared_recovery_rule_ids: tuple[str, ...] = ()
    declared_migration_paths: TreePaths = ()
    declared_migration_rule_ids: tuple[str, ...] = ()
    matched_allowance_rule_ids: tuple[str, ...] = ()
    result_codes: tuple[str, ...] = ()
    path_set_invariant_holds: bool = True
    safe_default: str = ""
    forbidden_shortcuts: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proof_id", _required_string("proof_id", self.proof_id))
        object.__setattr__(self, "jurisdiction", _required_string("jurisdiction", self.jurisdiction))
        object.__setattr__(
            self,
            "materialization_surface",
            _required_string("materialization_surface", self.materialization_surface),
        )
        object.__setattr__(self, "operation_id", _required_string("operation_id", self.operation_id))
        object.__setattr__(self, "owner_phase", _required_string("owner_phase", self.owner_phase))
        object.__setattr__(self, "rule_id", _required_string("rule_id", self.rule_id))
        status = _required_string("status", self.status)
        if status not in _VALID_STATUSES:
            raise ValueError(
                "MutationBoundaryProof.status must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )
        object.__setattr__(self, "status", status)
        for field_name in (
            "selected_target_paths",
            "allowed_mutation_regions",
            "changed_paths",
            "covered_changed_paths",
            "unexplained_changed_paths",
            "declared_allowance_paths",
            "declared_recovery_paths",
            "declared_migration_paths",
        ):
            object.__setattr__(
                self,
                field_name,
                _validated_tree_paths(
                    f"MutationBoundaryProof.{field_name}",
                    getattr(self, field_name),
                ),
            )
        for field_name in (
            "declared_recovery_rule_ids",
            "declared_migration_rule_ids",
            "matched_allowance_rule_ids",
            "result_codes",
            "forbidden_shortcuts",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_tuple(f"MutationBoundaryProof.{field_name}", getattr(self, field_name)),
            )
        if not isinstance(self.path_set_invariant_holds, bool):
            raise ValueError("MutationBoundaryProof.path_set_invariant_holds must be a bool")
        object.__setattr__(self, "safe_default", _required_string("safe_default", self.safe_default))
        if not self.forbidden_shortcuts:
            raise ValueError("MutationBoundaryProof.forbidden_shortcuts is required")
        if not isinstance(self.detail, Mapping):
            raise ValueError("MutationBoundaryProof.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    @classmethod
    def from_mutation_invariant_report(
        cls,
        report: MutationInvariantReport,
        *,
        proof_id: str,
        jurisdiction: str,
        materialization_surface: str,
        owner_phase: str,
        safe_default: str,
        forbidden_shortcuts: tuple[str, ...],
    ) -> MutationBoundaryProof:
        result_codes = tuple(result.code for result in report.results)
        return cls(
            proof_id=proof_id,
            jurisdiction=jurisdiction,
            materialization_surface=materialization_surface,
            operation_id=report.op_id,
            owner_phase=owner_phase,
            rule_id=_rule_id_for_report(report),
            status=_status_for_report(report),
            helper=report.helper,
            outcome=report.outcome,
            selected_target_paths=report.allowed_roots,
            allowed_mutation_regions=report.permitted_paths,
            changed_paths=report.changed_paths,
            covered_changed_paths=report.covered_changed_paths,
            unexplained_changed_paths=report.unexplained_changed_paths,
            declared_allowance_paths=report.declared_allowance_paths,
            declared_recovery_paths=report.declared_recovery_paths,
            declared_recovery_rule_ids=report.declared_recovery_rule_ids,
            declared_migration_paths=report.declared_migration_paths,
            declared_migration_rule_ids=report.declared_migration_rule_ids,
            matched_allowance_rule_ids=report.matched_allowance_rule_ids,
            result_codes=result_codes,
            path_set_invariant_holds=report.path_set_invariant_holds,
            safe_default=safe_default,
            forbidden_shortcuts=forbidden_shortcuts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_id": self.proof_id,
            "jurisdiction": self.jurisdiction,
            "materialization_surface": self.materialization_surface,
            "operation_id": self.operation_id,
            "owner_phase": self.owner_phase,
            "rule_id": self.rule_id,
            "status": self.status,
            "helper": self.helper,
            "outcome": self.outcome,
            "selected_target_paths": _path_strings(self.selected_target_paths),
            "allowed_mutation_regions": _path_strings(self.allowed_mutation_regions),
            "changed_paths": _path_strings(self.changed_paths),
            "covered_changed_paths": _path_strings(self.covered_changed_paths),
            "unexplained_changed_paths": _path_strings(self.unexplained_changed_paths),
            "declared_allowance_paths": _path_strings(self.declared_allowance_paths),
            "declared_recovery_paths": _path_strings(self.declared_recovery_paths),
            "declared_recovery_rule_ids": list(self.declared_recovery_rule_ids),
            "declared_migration_paths": _path_strings(self.declared_migration_paths),
            "declared_migration_rule_ids": list(self.declared_migration_rule_ids),
            "matched_allowance_rule_ids": list(self.matched_allowance_rule_ids),
            "result_codes": list(self.result_codes),
            "path_set_invariant_holds": self.path_set_invariant_holds,
            "safe_default": self.safe_default,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "detail": _plain_jsonable(self.detail),
        }


def _status_for_report(report: MutationInvariantReport) -> MutationBoundaryProofStatus:
    result_codes = {result.code for result in report.results}
    if result_codes & MUTATION_ACCOUNTING_HARD_CODES:
        if result_codes <= _UNRESOLVED_RESULT_CODES:
            return "unresolved"
        return "violated"
    if not report.path_set_invariant_holds:
        return "violated"
    if result_codes:
        return "proved_with_allowance"
    return "proved"


def _rule_id_for_report(report: MutationInvariantReport) -> str:
    status = _status_for_report(report)
    if status == "proved":
        return "mutation_boundary_path_set_proved"
    if status == "proved_with_allowance":
        return "mutation_boundary_path_set_proved_with_allowance"
    if status == "unresolved":
        return "mutation_boundary_path_set_unresolved"
    return "mutation_boundary_path_set_violated"


def _required_string(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"MutationBoundaryProof.{field_name} is required")
    return text


def _validated_tree_paths(field_name: str, paths: Any) -> TreePaths:
    if isinstance(paths, str) or not isinstance(paths, IterableABC):
        raise ValueError(f"{field_name} must be a sequence of tree paths")
    normalized: list[tuple[tuple[str, str], ...]] = []
    for index, path in enumerate(paths):
        if isinstance(path, str) or not isinstance(path, IterableABC):
            raise ValueError(f"{field_name}[{index}] must be a tree path")
        tree_steps: list[tuple[str, str]] = []
        for step_index, step in enumerate(path):
            if isinstance(step, str) or not isinstance(step, IterableABC):
                raise ValueError(
                    f"{field_name}[{index}] step {step_index} must be a path step"
                )
            step_tuple = tuple(cast(IterableABC[object], step))
            if len(step_tuple) != 2:
                raise ValueError(
                    f"{field_name}[{index}] step {step_index} must have kind and label"
                )
            tree_steps.append((str(step_tuple[0]), str(step_tuple[1])))
        tree_path = tuple(tree_steps)
        issues = validate_tree_path(tree_path, field_name=f"{field_name}[{index}]")
        if issues:
            raise ValueError("; ".join(issues))
        normalized.append(tree_path)
    return tuple(normalized)


def _string_tuple(field_name: str, values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(str(value) for value in values if str(value))


def _path_strings(paths: TreePaths) -> list[str]:
    return [tree_path_to_diagnostic_string(path) for path in paths]


def _plain_jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_jsonable(inner) for inner in value]
    if isinstance(value, set | frozenset):
        return sorted((_plain_jsonable(inner) for inner in value), key=repr)
    return value

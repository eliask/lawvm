"""Finland mutation-boundary proof-surface projections.

Report/read-model adapters only; no replay authorization semantics.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping, cast

from lawvm.core.mutation_accounting import (
    MUTATION_ACCOUNTING_HARD_CODES,
    MutationAccountingResult,
    MutationInvariantReport,
)
from lawvm.core.mutation_boundary import tree_path_to_diagnostic_string
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.finland.proof_surface_row_helpers import (
    mapping_sequence as _mapping_sequence,
    string_sequence as _string_sequence,
)

_MUTATION_BOUNDARY_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "mutation_boundary_report_as_replay_authorization",
    "ignore_unexplained_changed_paths",
    "treat_allowed_recovery_as_universal_target_widening",
)


def mutation_boundary_proof_rows(
    reports: tuple[MutationInvariantReport | Mapping[str, Any], ...],
    *,
    statute_id: str,
    materialization_surface: str = "finland_strict_report",
) -> list[dict[str, Any]]:
    """Project Finland apply mutation-invariant reports into shared proof rows."""

    rows: list[dict[str, Any]] = []
    for index, report_like in enumerate(reports, start=1):
        if isinstance(report_like, MutationInvariantReport):
            row = _mutation_boundary_proof_row_from_report(
                report_like,
                proof_id=_mutation_boundary_proof_id(
                    statute_id=statute_id,
                    index=index,
                    op_id=report_like.op_id,
                ),
                materialization_surface=materialization_surface,
            )
        else:
            report = _mutation_invariant_report(report_like)
            proof = MutationBoundaryProof.from_mutation_invariant_report(
                report,
                proof_id=_mutation_boundary_proof_id(
                    statute_id=statute_id,
                    index=index,
                    op_id=report.op_id,
                ),
                jurisdiction="fi",
                materialization_surface=materialization_surface,
                owner_phase="replay_apply",
                safe_default="preserve_report_as_passive_boundary_evidence_not_replay_authorization",
                forbidden_shortcuts=_MUTATION_BOUNDARY_FORBIDDEN_SHORTCUTS,
            )
            row = proof.to_dict()
        source_statute = ""
        if isinstance(report_like, Mapping):
            report_mapping = cast("Mapping[str, Any]", report_like)
            source_statute = str(report_mapping.get("source_statute") or "")
        if source_statute:
            row["source_artifact_id"] = source_statute
        rows.append(row)
    return rows


def _mutation_invariant_report(
    report: MutationInvariantReport | Mapping[str, Any],
) -> MutationInvariantReport:
    if isinstance(report, MutationInvariantReport):
        return report
    return MutationInvariantReport(
        op_id=str(report.get("op_id") or ""),
        helper=str(report.get("helper") or ""),
        outcome=str(report.get("outcome") or ""),
        touched_paths=_path_tuple(report.get("touched_paths")),
        changed_paths=_path_tuple(report.get("changed_paths")),
        allowed_roots=_path_tuple(report.get("allowed_roots")),
        allowed_effect_region_paths=_path_tuple(report.get("allowed_effect_region_paths")),
        declared_allowance_paths=_path_tuple(report.get("declared_allowance_paths")),
        declared_recovery_paths=_path_tuple(report.get("declared_recovery_paths")),
        declared_recovery_rule_ids=_string_sequence(report.get("declared_recovery_rule_ids")),
        declared_migration_paths=_path_tuple(report.get("declared_migration_paths")),
        declared_migration_rule_ids=_string_sequence(report.get("declared_migration_rule_ids")),
        permitted_paths=_path_tuple(report.get("permitted_paths")),
        covered_changed_paths=_path_tuple(report.get("covered_changed_paths")),
        unexplained_changed_paths=_path_tuple(report.get("unexplained_changed_paths")),
        allowed_non_target_paths=_path_tuple(report.get("allowed_non_target_paths")),
        out_of_scope_paths=_path_tuple(report.get("out_of_scope_paths")),
        matched_allowance_rule_ids=_string_sequence(report.get("matched_allowance_rule_ids")),
        path_set_invariant_holds=_bool_field(
            report,
            "path_set_invariant_holds",
            default=True,
        ),
        results=tuple(_mutation_accounting_result(result) for result in _mapping_sequence(report.get("results"))),
    )


_UNRESOLVED_RESULT_CODES = frozenset(
    {
        "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
        "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    }
)


def _status_for_report(report: MutationInvariantReport) -> str:
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


def _rule_id_for_status(proof_status: str) -> str:
    if proof_status == "proved":
        return "mutation_boundary_path_set_proved"
    if proof_status == "proved_with_allowance":
        return "mutation_boundary_path_set_proved_with_allowance"
    if proof_status == "unresolved":
        return "mutation_boundary_path_set_unresolved"
    return "mutation_boundary_path_set_violated"


def _path_strings(paths: tuple[tuple[tuple[str, str], ...], ...]) -> list[str]:
    return [_cached_tree_path_to_diagnostic_string(path) for path in paths]


@lru_cache(maxsize=16384)
def _cached_tree_path_to_diagnostic_string(path: tuple[tuple[str, str], ...]) -> str:
    return tree_path_to_diagnostic_string(path)


def _mutation_boundary_proof_row_from_report(
    report: MutationInvariantReport,
    *,
    proof_id: str,
    materialization_surface: str,
) -> dict[str, Any]:
    status = _status_for_report(report)
    result_codes = tuple(result.code for result in report.results)
    return {
        "proof_id": proof_id,
        "jurisdiction": "fi",
        "materialization_surface": materialization_surface,
        "operation_id": report.op_id,
        "owner_phase": "replay_apply",
        "rule_id": _rule_id_for_status(status),
        "status": status,
        "helper": report.helper,
        "outcome": report.outcome,
        "selected_target_paths": _path_strings(report.allowed_roots),
        "allowed_mutation_regions": _path_strings(report.permitted_paths),
        "changed_paths": _path_strings(report.changed_paths),
        "covered_changed_paths": _path_strings(report.covered_changed_paths),
        "unexplained_changed_paths": _path_strings(report.unexplained_changed_paths),
        "declared_allowance_paths": _path_strings(report.declared_allowance_paths),
        "declared_recovery_paths": _path_strings(report.declared_recovery_paths),
        "declared_recovery_rule_ids": list(report.declared_recovery_rule_ids),
        "declared_migration_paths": _path_strings(report.declared_migration_paths),
        "declared_migration_rule_ids": list(report.declared_migration_rule_ids),
        "matched_allowance_rule_ids": list(report.matched_allowance_rule_ids),
        "result_codes": list(result_codes),
        "path_set_invariant_holds": report.path_set_invariant_holds,
        "safe_default": "preserve_report_as_passive_boundary_evidence_not_replay_authorization",
        "forbidden_shortcuts": list(_MUTATION_BOUNDARY_FORBIDDEN_SHORTCUTS),
        "detail": {},
    }


def _mutation_accounting_result(row: Mapping[str, Any]) -> MutationAccountingResult:
    return MutationAccountingResult(
        code=str(row.get("code") or ""),
        op_id=str(row.get("op_id") or ""),
        helper=str(row.get("helper") or ""),
        touched_count=int(row.get("touched_count") or 0),
        allowed_roots=_path_tuple(row.get("allowed_roots")),
        out_of_scope_paths=_path_tuple(row.get("out_of_scope_paths")),
        allowed_paths=_path_tuple(row.get("allowed_paths")),
        matched_allowance_rule_ids=_string_sequence(row.get("matched_allowance_rule_ids")),
    )


def _mutation_boundary_proof_id(
    *,
    statute_id: str,
    index: int,
    op_id: str,
) -> str:
    return f"fi:{statute_id}:mutation-boundary:{index}:{op_id or 'unknown-op'}"


def _path_tuple(value: Any) -> tuple[tuple[tuple[str, str], ...], ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("mutation invariant path field must be a sequence")
    paths: list[tuple[tuple[str, str], ...]] = []
    for path_index, path in enumerate(value):
        if not isinstance(path, (list, tuple)):
            raise ValueError(f"mutation invariant path {path_index} must be a sequence")
        steps: list[tuple[str, str]] = []
        for step_index, step in enumerate(path):
            if not isinstance(step, (list, tuple)) or len(step) != 2:
                raise ValueError(f"mutation invariant path {path_index} step {step_index} must have kind and label")
            steps.append((str(step[0]), str(step[1])))
        paths.append(tuple(steps))
    return tuple(paths)


def _bool_field(row: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = row.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"mutation invariant {key} must be a boolean")


__all__ = ["mutation_boundary_proof_rows"]

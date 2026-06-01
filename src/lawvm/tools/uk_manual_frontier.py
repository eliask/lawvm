"""Validate exported UK manual-frontier rows against the current compiler."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.tools.uk_replay_regime import UK_APPLICABILITY_MODE_CHOICES
from lawvm.uk_legislation.execution_authorization import (
    uk_execution_authorization_from_manual_frontier,
)
from lawvm.uk_legislation.phase_discipline import uk_phase_owner_for_manual_frontier

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_DEFAULT_APPLICABILITY_MODE = "effective_date_plus_feed_applied"
_WORKQUEUE_SCHEMA = "lawvm.uk_manual_compile_frontier.v1"
_STALE_VALIDATOR_STATUSES = frozenset(
    {
        "changed_without_manual_frontier_or_ops",
        "resolved_compiles_without_blocking_lowering",
        "resolved_deterministic_supported",
    }
)
_VALIDATION_ERROR_STATUSES = frozenset({"effect_not_found", "input_error"})


class _ValidationStatus(NamedTuple):
    status: str
    rule_id: str


def _manual_frontier_validation_row(
    *,
    rule_id: str,
    validator_status: str,
    line_number: int,
    statute_id: str,
    effect_id: str,
    reason: str = "",
    blocking: bool = False,
    strict_disposition: str = "record",
    quirks_disposition: str = "record",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "lawvm.uk_manual_frontier_validation.v1",
        **diagnostic_detail(
            rule_id=rule_id,
            family="manual_frontier_validation",
            phase="tooling_diagnostic",
            reason=reason,
            blocking=blocking,
            strict_disposition=strict_disposition,
            quirks_disposition=quirks_disposition,
        ),
        "jurisdiction": "uk",
        "validator_status": validator_status,
        "line_number": line_number,
        "statute_id": statute_id,
        "effect_id": effect_id,
        **dict(extra or {}),
    }


def _read_jsonl_rows(path: Path) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "line_number": line_number,
                        "validator_status": "input_error",
                        "validator_rule_id": "uk_manual_frontier_jsonl_decode_error",
                        "reason": str(exc),
                    }
                )
                continue
            if not isinstance(parsed, dict):
                rows.append(
                    {
                        "line_number": line_number,
                        "validator_status": "input_error",
                        "validator_rule_id": "uk_manual_frontier_jsonl_row_not_object",
                    }
                )
                continue
            parsed["line_number"] = line_number
            rows.append(parsed)
    return tuple(rows)


def _write_jsonl_rows(path: Path, rows: tuple[Mapping[str, Any], ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


def _row_line_number(row: Mapping[str, Any]) -> int:
    value = row.get("line_number")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal():
            return int(stripped)
    return 0


def _row_line_witness(row: Mapping[str, Any]) -> str:
    line_number = _row_line_number(row)
    return f"line {line_number}" if line_number else "unknown line"


def _same_jsonl_payload_ignoring_line_number(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_payload = dict(left)
    right_payload = dict(right)
    left_payload.pop("line_number", None)
    right_payload.pop("line_number", None)
    return left_payload == right_payload


def _conflicting_work_item_id_issues_by_index(
    rows: tuple[Mapping[str, Any], ...],
) -> dict[int, tuple[str, ...]]:
    first_row_by_work_item_id: dict[str, tuple[int, Mapping[str, Any]]] = {}
    issue_lists_by_index: dict[int, list[str]] = {}
    for index, row in enumerate(rows):
        work_item_id = str(row.get("work_item_id") or "")
        if not work_item_id:
            continue
        existing = first_row_by_work_item_id.get(work_item_id)
        if existing is None:
            first_row_by_work_item_id[work_item_id] = (index, row)
            continue
        existing_index, existing_row = existing
        if _same_jsonl_payload_ignoring_line_number(existing_row, row):
            continue
        issue = (
            f"work_item_id {work_item_id!r} has conflicting rows at "
            f"{_row_line_witness(existing_row)} and {_row_line_witness(row)}"
        )
        issue_lists_by_index.setdefault(existing_index, []).append(issue)
        issue_lists_by_index.setdefault(index, []).append(issue)
    return {
        index: tuple(issues)
        for index, issues in issue_lists_by_index.items()
    }


def _row_replay_regime(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("replay_regime")
    if not isinstance(value, Mapping):
        value = {}
    return {
        "applicability_mode": str(
            value.get("applicability_mode") or _DEFAULT_APPLICABILITY_MODE
        ),
    }


def _find_effect_by_id(effects: tuple[Any, ...], effect_id: str) -> Any | None:
    for effect in effects:
        if str(getattr(effect, "effect_id", "") or "") == effect_id:
            return effect
    return None


def _validation_status(
    *,
    current_manual_status: str,
    current_blocking_rules: tuple[str, ...],
    current_compiled_op_count: int,
) -> _ValidationStatus:
    if current_manual_status == "deterministic_frontend_supported":
        return _ValidationStatus(
            status="resolved_deterministic_supported",
            rule_id="uk_manual_frontier_validator_currently_deterministic_supported",
        )
    if current_manual_status:
        return _ValidationStatus(
            status="still_manual_frontier",
            rule_id="uk_manual_frontier_validator_still_manual_frontier",
        )
    if current_compiled_op_count > 0 and not current_blocking_rules:
        return _ValidationStatus(
            status="resolved_compiles_without_blocking_lowering",
            rule_id="uk_manual_frontier_validator_currently_compiles",
        )
    if current_blocking_rules:
        return _ValidationStatus(
            status="still_blocked_without_manual_frontier_classification",
            rule_id="uk_manual_frontier_validator_still_blocked_unclassified",
        )
    return _ValidationStatus(
        status="changed_without_manual_frontier_or_ops",
        rule_id="uk_manual_frontier_validator_current_shape_changed",
    )


def _wrong_schema_validation_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    schema = str(row.get("schema") or "")
    if not schema or schema == _WORKQUEUE_SCHEMA:
        return None
    return _manual_frontier_validation_row(
        rule_id="uk_manual_frontier_validator_schema_rejected",
        validator_status="input_error",
        line_number=_row_line_number(row),
        statute_id=str(row.get("statute_id") or ""),
        effect_id=str(row.get("effect_id") or ""),
        reason=f"Manual-frontier JSONL row schema must be {_WORKQUEUE_SCHEMA}.",
        blocking=True,
        strict_disposition="block",
        quirks_disposition="block",
        extra={
            "input_schema": schema,
            "expected_schema": _WORKQUEUE_SCHEMA,
        },
    )


def _invalid_replay_regime_validation_row(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    value = row.get("replay_regime")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return _manual_frontier_validation_row(
            rule_id="uk_manual_frontier_validator_replay_regime_rejected",
            validator_status="input_error",
            line_number=_row_line_number(row),
            statute_id=str(row.get("statute_id") or ""),
            effect_id=str(row.get("effect_id") or ""),
            reason="Manual-frontier replay_regime must be an object when supplied.",
            blocking=True,
            strict_disposition="block",
            quirks_disposition="block",
            extra={
                "input_replay_regime": value,
                "expected_applicability_modes": list(UK_APPLICABILITY_MODE_CHOICES),
            },
        )
    applicability_mode = str(value.get("applicability_mode") or "")
    if not applicability_mode or applicability_mode in UK_APPLICABILITY_MODE_CHOICES:
        return None
    return _manual_frontier_validation_row(
        rule_id="uk_manual_frontier_validator_replay_regime_rejected",
        validator_status="input_error",
        line_number=_row_line_number(row),
        statute_id=str(row.get("statute_id") or ""),
        effect_id=str(row.get("effect_id") or ""),
        reason="Manual-frontier replay_regime.applicability_mode is not supported.",
        blocking=True,
        strict_disposition="block",
        quirks_disposition="block",
        extra={
            "input_applicability_mode": applicability_mode,
            "expected_applicability_modes": list(UK_APPLICABILITY_MODE_CHOICES),
        },
    )


def _validation_row_jsonable(
    row: Mapping[str, Any],
    *,
    current_summary: Any | None,
    current_suggested_claim_template: Mapping[str, Any] | None = None,
    effect_found: bool,
) -> dict[str, Any]:
    statute_id = str(row.get("statute_id") or "")
    effect_id = str(row.get("effect_id") or "")
    original_manual_status = str(row.get("manual_compile_status") or "")
    original_manual_rule_id = str(row.get("manual_compile_rule_id") or "")
    original_owner_phase = str(
        row.get("owner_phase")
        or row.get("manual_compile_owner_phase")
        or uk_phase_owner_for_manual_frontier(
            manual_compile_status=original_manual_status,
            manual_compile_rule_id=original_manual_rule_id,
            source_pathology=str(row.get("source_pathology") or ""),
        )
    )
    wrong_schema_row = _wrong_schema_validation_row(row)
    if wrong_schema_row is not None:
        return wrong_schema_row
    replay_regime_error_row = _invalid_replay_regime_validation_row(row)
    if replay_regime_error_row is not None:
        return replay_regime_error_row
    if not statute_id or not effect_id:
        return _manual_frontier_validation_row(
            rule_id="uk_manual_frontier_validator_input_missing_key",
            validator_status="input_error",
            line_number=_row_line_number(row),
            statute_id=statute_id,
            effect_id=effect_id,
            reason="Manual-frontier JSONL row must include statute_id and effect_id.",
            blocking=True,
            strict_disposition="block",
            quirks_disposition="block",
        )
    if not effect_found or current_summary is None:
        return _manual_frontier_validation_row(
            rule_id="uk_manual_frontier_validator_effect_not_found",
            validator_status="effect_not_found",
            line_number=_row_line_number(row),
            statute_id=statute_id,
            effect_id=effect_id,
            reason="The exported workqueue effect_id is no longer present in the current effect feed for this statute.",
            blocking=True,
            strict_disposition="block",
            quirks_disposition="record",
            extra={
                "original_manual_compile_status": original_manual_status,
                "original_manual_compile_rule_id": original_manual_rule_id,
                "original_owner_phase": original_owner_phase,
            },
        )
    current_manual_status = str(current_summary.manual_compile_status or "")
    current_manual_rule_id = str(current_summary.manual_compile_rule_id or "")
    current_owner_phase = str(
        current_summary.manual_compile_owner_phase
        or uk_phase_owner_for_manual_frontier(
            manual_compile_status=current_manual_status,
            manual_compile_rule_id=current_manual_rule_id,
            source_pathology=str(current_summary.source_pathology or ""),
        )
    )
    current_blocking_rules = tuple(current_summary.manual_compile_blocking_lowering_rule_ids)
    current_template = dict(current_suggested_claim_template or {})
    validation_status = _validation_status(
        current_manual_status=current_manual_status,
        current_blocking_rules=current_blocking_rules,
        current_compiled_op_count=int(current_summary.n_ops),
    )
    current_authorization = uk_execution_authorization_from_manual_frontier(
        manual_compile_status=current_manual_status,
        manual_compile_rule_id=current_manual_rule_id,
        owner_phase=current_owner_phase,
        validator_status=validation_status.status,
    ).to_dict()
    return _manual_frontier_validation_row(
        rule_id=validation_status.rule_id,
        validator_status=validation_status.status,
        line_number=_row_line_number(row),
        statute_id=statute_id,
        effect_id=effect_id,
        extra={
            "original_manual_compile_status": original_manual_status,
            "original_manual_compile_rule_id": original_manual_rule_id,
            "original_owner_phase": original_owner_phase,
            "current_manual_compile_status": current_manual_status,
            "current_manual_compile_rule_id": current_manual_rule_id,
            "current_owner_phase": current_owner_phase,
            "current_manual_compile_reason": str(current_summary.manual_compile_reason or ""),
            "current_compiled_op_count": int(current_summary.n_ops),
            "current_lowering_observation_rule_ids": [
                str(record.get("rule_id") or "unknown")
                for record in current_summary.lowering_rejections
            ],
            "current_blocking_lowering_rule_ids": list(current_blocking_rules),
            "current_source_pathology": str(current_summary.source_pathology or ""),
            "current_compare_shape": str(current_summary.compare_shape or ""),
            "current_replay_applicable": bool(current_summary.replay_applicable),
            "current_structural_for_replay": bool(current_summary.structural_for_replay),
            "current_suggested_claim_template_status": (
                "available" if current_template else "not_available"
            ),
            "current_suggested_claim_template": current_template,
            "current_execution_authorization": current_authorization,
            "current_executable": current_authorization["executable"],
            "current_replay_authorized": current_authorization["replay_authorized"],
            "current_authorization_status": current_authorization[
                "authorization_status"
            ],
            "current_authorization_rule_id": current_authorization[
                "authorization_rule_id"
            ],
            "current_required_proofs": current_authorization["required_proofs"],
            "current_safe_default": current_authorization["safe_default"],
            "current_forbidden_shortcuts": current_authorization[
                "forbidden_shortcuts"
            ],
        },
    )


def validate_manual_frontier_rows(
    rows: tuple[Mapping[str, Any], ...],
    *,
    db_path: Path,
) -> tuple[dict[str, Any], ...]:
    import farchive
    from lawvm.tools import uk_effects
    from lawvm.uk_legislation import effects as uk_legislation_effects

    output: list[dict[str, Any]] = []
    effects_cache: dict[str, tuple[Any, ...]] = {}
    context_cache: dict[str, Any] = {}
    work_item_id_conflicts = _conflicting_work_item_id_issues_by_index(rows)
    with farchive.Farchive(db_path) as archive:
        for index, row in enumerate(rows):
            if str(row.get("validator_status") or "") == "input_error":
                output.append(
                    _manual_frontier_validation_row(
                        rule_id=str(
                            row.get("validator_rule_id")
                            or "uk_manual_frontier_validator_input_error"
                        ),
                        validator_status="input_error",
                        line_number=_row_line_number(row),
                        statute_id="",
                        effect_id="",
                        reason=str(row.get("reason") or ""),
                        blocking=True,
                        strict_disposition="block",
                        quirks_disposition="block",
                    )
                )
                continue
            conflict_issues = work_item_id_conflicts.get(index, ())
            if conflict_issues:
                output.append(
                    _manual_frontier_validation_row(
                        rule_id="uk_manual_frontier_validator_work_item_id_conflict",
                        validator_status="input_error",
                        line_number=_row_line_number(row),
                        statute_id=str(row.get("statute_id") or ""),
                        effect_id=str(row.get("effect_id") or ""),
                        reason="; ".join(conflict_issues),
                        blocking=True,
                        strict_disposition="block",
                        quirks_disposition="block",
                    )
                )
                continue
            wrong_schema_row = _wrong_schema_validation_row(row)
            if wrong_schema_row is not None:
                output.append(wrong_schema_row)
                continue
            replay_regime_error_row = _invalid_replay_regime_validation_row(row)
            if replay_regime_error_row is not None:
                output.append(replay_regime_error_row)
                continue
            statute_id = str(row.get("statute_id") or "")
            effect_id = str(row.get("effect_id") or "")
            if not statute_id or not effect_id:
                output.append(
                    _validation_row_jsonable(
                        row,
                        current_summary=None,
                        effect_found=False,
                    )
                )
                continue
            if statute_id not in effects_cache:
                effects_cache[statute_id] = tuple(
                    uk_legislation_effects.load_effects_for_statute_from_archive(
                        statute_id,
                        archive,
                    )
                )
            effect = _find_effect_by_id(effects_cache[statute_id], effect_id)
            if effect is None:
                output.append(
                    _validation_row_jsonable(
                        row,
                        current_summary=None,
                        effect_found=False,
                    )
                )
                continue
            if statute_id not in context_cache:
                context_cache[statute_id] = uk_effects.build_uk_effect_summary_context(
                    statute_id,
                    archive=archive,
                )
            replay_regime = _row_replay_regime(row)
            current_summary = uk_effects.summarize_uk_effect(
                effect,
                archive=archive,
                context=context_cache[statute_id],
                applicability_mode=str(replay_regime["applicability_mode"]),
            )
            current_report_row = uk_effects._EffectReportRow(
                effect=effect,
                summary=current_summary,
            )
            current_suggested_claim_template = (
                uk_effects._manual_compile_suggested_claim_template(
                    statute_id=statute_id,
                    row=current_report_row,
                )
            )
            output.append(
                _validation_row_jsonable(
                    row,
                    current_summary=current_summary,
                    current_suggested_claim_template=current_suggested_claim_template,
                    effect_found=True,
                )
            )
    return tuple(output)


def _validation_report_jsonable(
    *,
    input_path: Path,
    db_path: Path,
    rows: tuple[Mapping[str, Any], ...],
    validation_jsonl: Mapping[str, Any] | None = None,
    remaining_jsonl: Mapping[str, Any] | None = None,
    summary_only: bool = False,
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("validator_status") or "unknown") for row in rows)
    rule_counts = Counter(str(row.get("rule_id") or "unknown") for row in rows)
    original_manual_rule_counts = Counter(
        str(row.get("original_manual_compile_rule_id") or "unknown")
        for row in rows
        if str(row.get("original_manual_compile_rule_id") or "")
    )
    current_manual_rule_counts = Counter(
        str(row.get("current_manual_compile_rule_id") or "unknown")
        for row in rows
        if str(row.get("current_manual_compile_rule_id") or "")
    )
    current_manual_status_counts = Counter(
        str(row.get("current_manual_compile_status") or "unknown")
        for row in rows
        if str(row.get("current_manual_compile_status") or "")
    )
    current_suggested_claim_template_status_counts = Counter(
        str(row.get("current_suggested_claim_template_status") or "unknown")
        for row in rows
        if str(row.get("current_suggested_claim_template_status") or "")
    )
    current_source_pathology_counts = Counter(
        str(row.get("current_source_pathology") or "unknown")
        for row in rows
        if str(row.get("current_source_pathology") or "")
    )
    original_owner_phase_counts = Counter(
        str(row.get("original_owner_phase") or "unknown")
        for row in rows
        if str(row.get("original_owner_phase") or "")
    )
    current_owner_phase_counts = Counter(
        str(row.get("current_owner_phase") or "unknown")
        for row in rows
        if str(row.get("current_owner_phase") or "")
    )
    current_authorization_status_counts = Counter(
        str(row.get("current_authorization_status") or "unknown")
        for row in rows
        if str(row.get("current_authorization_status") or "")
    )
    current_missing_proof_counts = _required_proof_counter(rows)
    remaining_manual_rule_counts: Counter[str] = Counter()
    remaining_manual_status_counts: Counter[str] = Counter()
    remaining_suggested_claim_template_status_counts: Counter[str] = Counter()
    remaining_source_pathology_counts: Counter[str] = Counter()
    remaining_owner_phase_counts: Counter[str] = Counter()
    remaining_authorization_status_counts: Counter[str] = Counter()
    remaining_missing_proof_counts: Counter[str] = Counter()
    stale_original_manual_rule_counts: Counter[str] = Counter()
    current_blocking_lowering_rule_counts: Counter[str] = Counter()
    remaining_blocking_lowering_rule_counts: Counter[str] = Counter()
    current_template_action_family_counts = _template_field_counter(
        rows,
        "action_family",
    )
    remaining_template_action_family_counts = _template_field_counter(
        rows,
        "action_family",
        remaining_only=True,
    )
    current_template_validator_check_counts = _template_field_counter(
        rows,
        "required_validator_checks",
    )
    remaining_template_validator_check_counts = _template_field_counter(
        rows,
        "required_validator_checks",
        remaining_only=True,
    )
    current_template_ownership_counts = _template_field_counter(
        rows,
        "required_ownership",
    )
    remaining_template_ownership_counts = _template_field_counter(
        rows,
        "required_ownership",
        remaining_only=True,
    )
    current_template_proof_semantic_counts = _template_field_counter(
        rows,
        "required_operation_family_proof_semantics",
    )
    remaining_template_proof_semantic_counts = _template_field_counter(
        rows,
        "required_operation_family_proof_semantics",
        remaining_only=True,
    )
    for row in rows:
        blocking_rules = tuple(
            str(rule_id)
            for rule_id in row.get("current_blocking_lowering_rule_ids") or ()
            if str(rule_id)
        )
        current_blocking_lowering_rule_counts.update(blocking_rules)
        if _is_remaining_manual_frontier_validation(row):
            current_status = str(row.get("current_manual_compile_status") or "")
            if current_status:
                remaining_manual_status_counts[current_status] += 1
            current_rule_id = str(row.get("current_manual_compile_rule_id") or "")
            if current_rule_id:
                remaining_manual_rule_counts[current_rule_id] += 1
            current_template_status = str(
                row.get("current_suggested_claim_template_status") or ""
            )
            if current_template_status:
                remaining_suggested_claim_template_status_counts[
                    current_template_status
                ] += 1
            source_pathology = str(row.get("current_source_pathology") or "")
            if source_pathology:
                remaining_source_pathology_counts[source_pathology] += 1
            owner_phase = str(row.get("current_owner_phase") or "")
            if owner_phase:
                remaining_owner_phase_counts[owner_phase] += 1
            authorization_status = str(row.get("current_authorization_status") or "")
            if authorization_status:
                remaining_authorization_status_counts[authorization_status] += 1
            remaining_missing_proof_counts.update(
                _required_proof_counter((row,))
            )
            remaining_blocking_lowering_rule_counts.update(blocking_rules)
        if _is_stale_manual_frontier_validation(row):
            original_rule_id = str(row.get("original_manual_compile_rule_id") or "")
            if original_rule_id:
                stale_original_manual_rule_counts[original_rule_id] += 1
    remaining_count = sum(1 for row in rows if _is_remaining_manual_frontier_validation(row))
    stale_count = sum(1 for row in rows if _is_stale_manual_frontier_validation(row))
    validation_error_count = sum(
        1 for row in rows if _is_validation_error_manual_frontier_validation(row)
    )
    report: dict[str, Any] = {
        "report_kind": "uk_manual_frontier_validation_report",
        "input_path": str(input_path),
        "db_path": str(db_path),
        "summary": {
            "row_count": len(rows),
            "remaining_row_count": remaining_count,
            "stale_row_count": stale_count,
            "validation_error_count": validation_error_count,
            "validator_status_counts": dict(sorted(status_counts.items())),
            "validator_rule_counts": dict(sorted(rule_counts.items())),
            "original_manual_rule_counts": dict(sorted(original_manual_rule_counts.items())),
            "current_manual_status_counts": dict(
                sorted(current_manual_status_counts.items())
            ),
            "current_manual_rule_counts": dict(sorted(current_manual_rule_counts.items())),
            "current_suggested_claim_template_status_counts": dict(
                sorted(current_suggested_claim_template_status_counts.items())
            ),
            "current_source_pathology_counts": dict(
                sorted(current_source_pathology_counts.items())
            ),
            "original_owner_phase_counts": dict(
                sorted(original_owner_phase_counts.items())
            ),
            "current_owner_phase_counts": dict(sorted(current_owner_phase_counts.items())),
            "current_authorization_status_counts": dict(
                sorted(current_authorization_status_counts.items())
            ),
            "current_missing_proof_counts": dict(
                sorted(current_missing_proof_counts.items())
            ),
            "remaining_manual_status_counts": dict(
                sorted(remaining_manual_status_counts.items())
            ),
            "remaining_manual_rule_counts": dict(sorted(remaining_manual_rule_counts.items())),
            "remaining_suggested_claim_template_status_counts": dict(
                sorted(remaining_suggested_claim_template_status_counts.items())
            ),
            "remaining_source_pathology_counts": dict(
                sorted(remaining_source_pathology_counts.items())
            ),
            "remaining_owner_phase_counts": dict(
                sorted(remaining_owner_phase_counts.items())
            ),
            "remaining_authorization_status_counts": dict(
                sorted(remaining_authorization_status_counts.items())
            ),
            "remaining_missing_proof_counts": dict(
                sorted(remaining_missing_proof_counts.items())
            ),
            "stale_original_manual_rule_counts": dict(
                sorted(stale_original_manual_rule_counts.items())
            ),
            "current_blocking_lowering_rule_counts": dict(
                sorted(current_blocking_lowering_rule_counts.items())
            ),
            "remaining_blocking_lowering_rule_counts": dict(
                sorted(remaining_blocking_lowering_rule_counts.items())
            ),
            "current_template_action_family_counts": dict(
                sorted(current_template_action_family_counts.items())
            ),
            "remaining_template_action_family_counts": dict(
                sorted(remaining_template_action_family_counts.items())
            ),
            "current_template_required_validator_check_counts": dict(
                sorted(current_template_validator_check_counts.items())
            ),
            "remaining_template_required_validator_check_counts": dict(
                sorted(remaining_template_validator_check_counts.items())
            ),
            "current_template_required_ownership_counts": dict(
                sorted(current_template_ownership_counts.items())
            ),
            "remaining_template_required_ownership_counts": dict(
                sorted(remaining_template_ownership_counts.items())
            ),
            "current_template_required_operation_family_proof_semantic_counts": dict(
                sorted(current_template_proof_semantic_counts.items())
            ),
            "remaining_template_required_operation_family_proof_semantic_counts": dict(
                sorted(remaining_template_proof_semantic_counts.items())
            ),
        },
    }
    if not summary_only:
        report["rows"] = [dict(row) for row in rows]
    if validation_jsonl is not None:
        report["validation_jsonl"] = dict(validation_jsonl)
    if remaining_jsonl is not None:
        report["remaining_jsonl"] = dict(remaining_jsonl)
    return report


def _string_tuple_from_value(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    return ()


def _required_proof_counter(rows: tuple[Mapping[str, Any], ...]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("current_replay_authorized") is True:
            continue
        proofs = row.get("current_required_proofs") or ()
        counts.update(_string_tuple_from_value(proofs))
    return counts


def _template_mapping(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("current_suggested_claim_template")
    return value if isinstance(value, Mapping) else {}


def _template_field_counter(
    rows: tuple[Mapping[str, Any], ...],
    field: str,
    *,
    remaining_only: bool = False,
) -> Counter[str]:
    return Counter(
        item
        for row in rows
        if not remaining_only or _is_remaining_manual_frontier_validation(row)
        for item in _string_tuple_from_value(_template_mapping(row).get(field))
    )


def _is_remaining_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in {
        "still_manual_frontier",
        "still_blocked_without_manual_frontier_classification",
    }


def _is_stale_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in _STALE_VALIDATOR_STATUSES


def _is_validation_error_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in _VALIDATION_ERROR_STATUSES


def _value_allowed(value: str, allowed_values: frozenset[str]) -> bool:
    return not allowed_values or value in allowed_values


def _remaining_workqueue_rows(
    original_rows: tuple[Mapping[str, Any], ...],
    validation_rows: tuple[Mapping[str, Any], ...],
    *,
    manual_rule_ids: frozenset[str] = frozenset(),
    source_pathologies: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    remaining: list[dict[str, Any]] = []
    for original, validation in zip(original_rows, validation_rows, strict=False):
        if not _is_remaining_manual_frontier_validation(validation):
            continue
        current_rule_id = str(validation.get("current_manual_compile_rule_id") or "")
        if not _value_allowed(current_rule_id, manual_rule_ids):
            continue
        source_pathology = str(validation.get("current_source_pathology") or "")
        if not _value_allowed(source_pathology, source_pathologies):
            continue
        row = dict(original)
        row["validator_status"] = str(validation.get("validator_status") or "")
        row["validator_rule_id"] = str(validation.get("rule_id") or "")
        row["validator_current_manual_compile_status"] = str(
            validation.get("current_manual_compile_status") or ""
        )
        row["validator_current_manual_compile_rule_id"] = str(
            validation.get("current_manual_compile_rule_id") or ""
        )
        row["validator_current_compiled_op_count"] = int(
            validation.get("current_compiled_op_count") or 0
        )
        row["validator_current_blocking_lowering_rule_ids"] = list(
            validation.get("current_blocking_lowering_rule_ids") or ()
        )
        row["validator_current_source_pathology"] = str(
            validation.get("current_source_pathology") or ""
        )
        row["validator_current_owner_phase"] = str(
            validation.get("current_owner_phase") or ""
        )
        current_authorization = validation.get("current_execution_authorization")
        if isinstance(current_authorization, Mapping):
            row["execution_authorization"] = dict(current_authorization)
        else:
            row["execution_authorization"] = {}
        # Preserve the original workqueue evidence fields, but expose the
        # current classification at top level so downstream queue tooling does
        # not accidentally group by stale exported families.
        row["current_manual_compile_status"] = row[
            "validator_current_manual_compile_status"
        ]
        row["current_manual_compile_rule_id"] = row[
            "validator_current_manual_compile_rule_id"
        ]
        row["current_compiled_op_count"] = row[
            "validator_current_compiled_op_count"
        ]
        row["current_blocking_lowering_rule_ids"] = list(
            row["validator_current_blocking_lowering_rule_ids"]
        )
        row["current_source_pathology"] = row["validator_current_source_pathology"]
        row["current_owner_phase"] = row["validator_current_owner_phase"]
        row["executable"] = bool(validation.get("current_executable") or False)
        row["replay_authorized"] = bool(
            validation.get("current_replay_authorized") or False
        )
        row["authorization_status"] = str(
            validation.get("current_authorization_status") or ""
        )
        row["authorization_rule_id"] = str(
            validation.get("current_authorization_rule_id") or ""
        )
        row["required_proofs"] = list(validation.get("current_required_proofs") or ())
        row["safe_default"] = str(validation.get("current_safe_default") or "")
        row["forbidden_shortcuts"] = list(
            validation.get("current_forbidden_shortcuts") or ()
        )
        current_template = validation.get("current_suggested_claim_template")
        if isinstance(current_template, Mapping) and current_template:
            row["suggested_claim_template"] = dict(current_template)
            row["suggested_claim_template_status"] = str(
                validation.get("current_suggested_claim_template_status")
                or "available"
            )
        else:
            row["suggested_claim_template"] = {}
            row["suggested_claim_template_status"] = "not_available"
        row["validation"] = dict(validation)
        remaining.append(row)
    return tuple(remaining)


def _print_text_report(report: Mapping[str, Any], *, summary_only: bool = False) -> None:
    summary = report["summary"]

    def _format_count_map(value: object) -> str:
        if not isinstance(value, Mapping) or not value:
            return "{}"
        return ", ".join(
            f"{key}={count}"
            for key, count in sorted(value.items())
        )

    print("UK manual-frontier validation")
    print(f"Rows: {summary['row_count']}")
    print(
        "Triage: "
        f"remaining={summary.get('remaining_row_count', 0)} "
        f"stale={summary.get('stale_row_count', 0)} "
        f"validation_errors={summary.get('validation_error_count', 0)}"
    )
    print(
        "Statuses: "
        + ", ".join(
            f"{key}={value}"
            for key, value in sorted(summary["validator_status_counts"].items())
        )
    )
    print(
        "Rules: "
        + ", ".join(
            f"{key}={value}"
            for key, value in sorted(summary["validator_rule_counts"].items())
        )
    )
    print(
        "Remaining manual rules: "
        + _format_count_map(summary.get("remaining_manual_rule_counts"))
    )
    print(
        "Current manual statuses: "
        + _format_count_map(summary.get("current_manual_status_counts"))
    )
    print(
        "Remaining manual statuses: "
        + _format_count_map(summary.get("remaining_manual_status_counts"))
    )
    print(
        "Current claim templates: "
        + _format_count_map(
            summary.get("current_suggested_claim_template_status_counts")
        )
    )
    print(
        "Remaining claim templates: "
        + _format_count_map(
            summary.get("remaining_suggested_claim_template_status_counts")
        )
    )
    print(
        "Current source pathologies: "
        + _format_count_map(summary.get("current_source_pathology_counts"))
    )
    print(
        "Current owner phases: "
        + _format_count_map(summary.get("current_owner_phase_counts"))
    )
    print(
        "Current authorization statuses: "
        + _format_count_map(summary.get("current_authorization_status_counts"))
    )
    print(
        "Current missing proofs: "
        + _format_count_map(summary.get("current_missing_proof_counts"))
    )
    print(
        "Remaining source pathologies: "
        + _format_count_map(summary.get("remaining_source_pathology_counts"))
    )
    print(
        "Remaining owner phases: "
        + _format_count_map(summary.get("remaining_owner_phase_counts"))
    )
    print(
        "Remaining authorization statuses: "
        + _format_count_map(summary.get("remaining_authorization_status_counts"))
    )
    print(
        "Remaining missing proofs: "
        + _format_count_map(summary.get("remaining_missing_proof_counts"))
    )
    print(
        "Remaining blocking lowering: "
        + _format_count_map(summary.get("remaining_blocking_lowering_rule_counts"))
    )
    print(
        "Current template action families: "
        + _format_count_map(summary.get("current_template_action_family_counts"))
    )
    print(
        "Remaining template action families: "
        + _format_count_map(summary.get("remaining_template_action_family_counts"))
    )
    print(
        "Current template validator checks: "
        + _format_count_map(
            summary.get("current_template_required_validator_check_counts")
        )
    )
    print(
        "Remaining template validator checks: "
        + _format_count_map(
            summary.get("remaining_template_required_validator_check_counts")
        )
    )
    print(
        "Current template ownership: "
        + _format_count_map(summary.get("current_template_required_ownership_counts"))
    )
    print(
        "Remaining template ownership: "
        + _format_count_map(
            summary.get("remaining_template_required_ownership_counts")
        )
    )
    print(
        "Current template proof semantics: "
        + _format_count_map(
            summary.get(
                "current_template_required_operation_family_proof_semantic_counts"
            )
        )
    )
    print(
        "Remaining template proof semantics: "
        + _format_count_map(
            summary.get(
                "remaining_template_required_operation_family_proof_semantic_counts"
            )
        )
    )
    if summary.get("stale_original_manual_rule_counts"):
        print(
            "Stale original manual rules: "
            + _format_count_map(summary.get("stale_original_manual_rule_counts"))
        )
    validation_jsonl = report.get("validation_jsonl")
    if isinstance(validation_jsonl, Mapping):
        print(
            "Validation JSONL: "
            f"{validation_jsonl.get('path')} rows={validation_jsonl.get('rows')}"
        )
    remaining_jsonl = report.get("remaining_jsonl")
    if isinstance(remaining_jsonl, Mapping):
        filter_parts: list[str] = []
        manual_rule_filters = tuple(remaining_jsonl.get("manual_rule_filters") or ())
        if manual_rule_filters:
            filter_parts.append(
                "manual_rules=" + ",".join(str(item) for item in manual_rule_filters)
            )
        source_pathology_filters = tuple(
            remaining_jsonl.get("source_pathology_filters") or ()
        )
        if source_pathology_filters:
            filter_parts.append(
                "source_pathologies="
                + ",".join(str(item) for item in source_pathology_filters)
            )
        filter_text = ""
        if filter_parts:
            filter_text = " filters=" + ";".join(filter_parts)
        print(
            "Remaining JSONL: "
            f"{remaining_jsonl.get('path')} rows={remaining_jsonl.get('rows')} "
            f"statuses={','.join(str(item) for item in remaining_jsonl.get('statuses') or ())}"
            f"{filter_text}"
        )
    if summary_only:
        return
    for row in report.get("rows", ()):
        if not isinstance(row, Mapping):
            continue
        print(
            f"{row.get('validator_status')} {row.get('statute_id')} {row.get('effect_id')} "
            f"old={row.get('original_manual_compile_rule_id') or '-'} "
            f"current={row.get('current_manual_compile_rule_id') or '-'} "
            f"ops={row.get('current_compiled_op_count', '-')}"
        )


def main(args: "argparse.Namespace") -> None:
    input_arg = str(getattr(args, "input", "") or "")
    if not input_arg:
        print("error: uk-manual-frontier-validate requires INPUT", file=sys.stderr)
        sys.exit(2)
    input_path = Path(input_arg)
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    validation_jsonl_arg = str(getattr(args, "validation_jsonl", "") or "")
    validation_jsonl_path = Path(validation_jsonl_arg) if validation_jsonl_arg else None
    remaining_jsonl_arg = str(getattr(args, "remaining_jsonl", "") or "")
    remaining_jsonl_path = Path(remaining_jsonl_arg) if remaining_jsonl_arg else None
    remaining_manual_rule_filters = frozenset(
        str(item)
        for item in (getattr(args, "remaining_manual_rule", None) or ())
        if str(item)
    )
    remaining_source_pathology_filters = frozenset(
        str(item)
        for item in (getattr(args, "remaining_source_pathology", None) or ())
        if str(item)
    )
    summary_only = bool(getattr(args, "summary_only", False))
    if not input_path.exists():
        print(f"error: input JSONL not found at {input_path}", file=sys.stderr)
        sys.exit(1)
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    original_rows = _read_jsonl_rows(input_path)
    rows = validate_manual_frontier_rows(original_rows, db_path=db_path)
    validation_jsonl_report = None
    if validation_jsonl_path is not None:
        validation_jsonl_report = {
            "path": str(validation_jsonl_path),
            "rows": _write_jsonl_rows(validation_jsonl_path, rows),
        }
    remaining_rows = _remaining_workqueue_rows(
        original_rows,
        rows,
        manual_rule_ids=remaining_manual_rule_filters,
        source_pathologies=remaining_source_pathology_filters,
    )
    remaining_jsonl_report = None
    if remaining_jsonl_path is not None:
        remaining_jsonl_report = {
            "path": str(remaining_jsonl_path),
            "rows": _write_jsonl_rows(remaining_jsonl_path, remaining_rows),
            "statuses": sorted(
                {
                    str(row.get("validator_status") or "")
                    for row in remaining_rows
                }
            ),
            "manual_rule_filters": sorted(remaining_manual_rule_filters),
            "source_pathology_filters": sorted(remaining_source_pathology_filters),
        }
    report = _validation_report_jsonable(
        input_path=input_path,
        db_path=db_path,
        rows=rows,
        validation_jsonl=validation_jsonl_report,
        remaining_jsonl=remaining_jsonl_report,
        summary_only=summary_only,
    )
    if bool(getattr(args, "json", False)):
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_text_report(report, summary_only=summary_only)
    summary = report["summary"]
    if bool(getattr(args, "fail_on_validation_error", False)) and int(
        summary.get("validation_error_count") or 0
    ):
        sys.exit(1)
    if bool(getattr(args, "fail_on_stale", False)) and int(
        summary.get("stale_row_count") or 0
    ):
        sys.exit(1)
    if bool(getattr(args, "fail_on_remaining", False)) and int(
        summary.get("remaining_row_count") or 0
    ):
        sys.exit(1)

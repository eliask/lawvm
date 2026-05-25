"""Validate exported UK manual-frontier rows against the current compiler."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_DEFAULT_APPLICABILITY_MODE = "effective_date_plus_feed_applied"
_STALE_VALIDATOR_STATUSES = frozenset(
    {
        "changed_without_manual_frontier_or_ops",
        "resolved_compiles_without_blocking_lowering",
        "resolved_deterministic_supported",
    }
)
_VALIDATION_ERROR_STATUSES = frozenset({"effect_not_found", "input_error"})


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
            parsed.setdefault("line_number", line_number)
            rows.append(parsed)
    return tuple(rows)


def _write_jsonl_rows(path: Path, rows: tuple[Mapping[str, Any], ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


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
) -> tuple[str, str]:
    if current_manual_status == "deterministic_frontend_supported":
        return (
            "resolved_deterministic_supported",
            "uk_manual_frontier_validator_currently_deterministic_supported",
        )
    if current_compiled_op_count > 0 and not current_blocking_rules:
        return (
            "resolved_compiles_without_blocking_lowering",
            "uk_manual_frontier_validator_currently_compiles",
        )
    if current_manual_status:
        return (
            "still_manual_frontier",
            "uk_manual_frontier_validator_still_manual_frontier",
        )
    if current_blocking_rules:
        return (
            "still_blocked_without_manual_frontier_classification",
            "uk_manual_frontier_validator_still_blocked_unclassified",
        )
    return (
        "changed_without_manual_frontier_or_ops",
        "uk_manual_frontier_validator_current_shape_changed",
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
    if not statute_id or not effect_id:
        return {
            "schema": "lawvm.uk_manual_frontier_validation.v1",
            "rule_id": "uk_manual_frontier_validator_input_missing_key",
            "validator_status": "input_error",
            "line_number": int(row.get("line_number") or 0),
            "statute_id": statute_id,
            "effect_id": effect_id,
            "reason": "Manual-frontier JSONL row must include statute_id and effect_id.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "block",
        }
    if not effect_found or current_summary is None:
        return {
            "schema": "lawvm.uk_manual_frontier_validation.v1",
            "rule_id": "uk_manual_frontier_validator_effect_not_found",
            "validator_status": "effect_not_found",
            "line_number": int(row.get("line_number") or 0),
            "statute_id": statute_id,
            "effect_id": effect_id,
            "original_manual_compile_status": original_manual_status,
            "original_manual_compile_rule_id": original_manual_rule_id,
            "reason": "The exported workqueue effect_id is no longer present in the current effect feed for this statute.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    current_manual_status = str(current_summary.manual_compile_status or "")
    current_manual_rule_id = str(current_summary.manual_compile_rule_id or "")
    current_blocking_rules = tuple(current_summary.manual_compile_blocking_lowering_rule_ids)
    current_template = dict(current_suggested_claim_template or {})
    status, rule_id = _validation_status(
        current_manual_status=current_manual_status,
        current_blocking_rules=current_blocking_rules,
        current_compiled_op_count=int(current_summary.n_ops),
    )
    return {
        "schema": "lawvm.uk_manual_frontier_validation.v1",
        "rule_id": rule_id,
        "family": "manual_frontier_validation",
        "phase": "tooling_diagnostic",
        "jurisdiction": "uk",
        "validator_status": status,
        "line_number": int(row.get("line_number") or 0),
        "statute_id": statute_id,
        "effect_id": effect_id,
        "original_manual_compile_status": original_manual_status,
        "original_manual_compile_rule_id": original_manual_rule_id,
        "current_manual_compile_status": current_manual_status,
        "current_manual_compile_rule_id": current_manual_rule_id,
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
        "blocking": status in {"input_error", "effect_not_found"},
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }


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
    with farchive.Farchive(db_path) as archive:
        for row in rows:
            if str(row.get("validator_status") or "") == "input_error":
                output.append(
                    {
                        "schema": "lawvm.uk_manual_frontier_validation.v1",
                        "rule_id": str(
                            row.get("validator_rule_id")
                            or "uk_manual_frontier_validator_input_error"
                        ),
                        "validator_status": "input_error",
                        "line_number": int(row.get("line_number") or 0),
                        "statute_id": "",
                        "effect_id": "",
                        "reason": str(row.get("reason") or ""),
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "block",
                    }
                )
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
    current_suggested_claim_template_status_counts = Counter(
        str(row.get("current_suggested_claim_template_status") or "unknown")
        for row in rows
        if str(row.get("current_suggested_claim_template_status") or "")
    )
    remaining_manual_rule_counts: Counter[str] = Counter()
    remaining_suggested_claim_template_status_counts: Counter[str] = Counter()
    stale_original_manual_rule_counts: Counter[str] = Counter()
    current_blocking_lowering_rule_counts: Counter[str] = Counter()
    remaining_blocking_lowering_rule_counts: Counter[str] = Counter()
    for row in rows:
        blocking_rules = tuple(
            str(rule_id)
            for rule_id in row.get("current_blocking_lowering_rule_ids") or ()
            if str(rule_id)
        )
        current_blocking_lowering_rule_counts.update(blocking_rules)
        if _is_remaining_manual_frontier_validation(row):
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
            "current_manual_rule_counts": dict(sorted(current_manual_rule_counts.items())),
            "current_suggested_claim_template_status_counts": dict(
                sorted(current_suggested_claim_template_status_counts.items())
            ),
            "remaining_manual_rule_counts": dict(sorted(remaining_manual_rule_counts.items())),
            "remaining_suggested_claim_template_status_counts": dict(
                sorted(remaining_suggested_claim_template_status_counts.items())
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
        },
    }
    if not summary_only:
        report["rows"] = [dict(row) for row in rows]
    if validation_jsonl is not None:
        report["validation_jsonl"] = dict(validation_jsonl)
    if remaining_jsonl is not None:
        report["remaining_jsonl"] = dict(remaining_jsonl)
    return report


def _is_remaining_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in {
        "still_manual_frontier",
        "still_blocked_without_manual_frontier_classification",
    }


def _is_stale_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in _STALE_VALIDATOR_STATUSES


def _is_validation_error_manual_frontier_validation(row: Mapping[str, Any]) -> bool:
    return str(row.get("validator_status") or "") in _VALIDATION_ERROR_STATUSES


def _remaining_workqueue_rows(
    original_rows: tuple[Mapping[str, Any], ...],
    validation_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    remaining: list[dict[str, Any]] = []
    for original, validation in zip(original_rows, validation_rows, strict=False):
        if not _is_remaining_manual_frontier_validation(validation):
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
        "Remaining blocking lowering: "
        + _format_count_map(summary.get("remaining_blocking_lowering_rule_counts"))
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
        print(
            "Remaining JSONL: "
            f"{remaining_jsonl.get('path')} rows={remaining_jsonl.get('rows')} "
            f"statuses={','.join(str(item) for item in remaining_jsonl.get('statuses') or ())}"
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
    remaining_rows = _remaining_workqueue_rows(original_rows, rows)
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

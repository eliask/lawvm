"""Read-only query helper for shared LawVM evidence-row JSONL reports."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from lawvm.core.evidence_contracts import (
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)


@dataclass(frozen=True)
class ReportQueryRecord:
    """One JSONL record plus its shared evidence row projection."""

    source_path: str
    line_no: int
    original: Mapping[str, Any]
    evidence_row: Mapping[str, Any]
    row_kind: str
    validation_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportQueryFilters:
    """Shared evidence-row filters."""

    row_id: str = ""
    status: str = ""
    rule_id: str = ""
    phase: str = ""
    source_artifact: str = ""
    source_unit: str = ""
    locator: str = ""
    blocking: bool = False


def load_report_query_records(paths: Iterable[str | Path], *, validate: bool = False) -> tuple[ReportQueryRecord, ...]:
    """Load JSONL records that either are evidence rows or contain ``evidence_row``."""

    records: list[ReportQueryRecord] = []
    for path_like in paths:
        path = Path(path_like)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            original = json.loads(line)
            if not isinstance(original, Mapping):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            evidence_row = original.get("evidence_row", original)
            if not isinstance(evidence_row, Mapping):
                raise ValueError(f"{path}:{line_no}: evidence_row must be an object")
            row_kind = _classify_evidence_row(evidence_row)
            validation_issues = _validate_evidence_row(evidence_row, row_kind) if validate else ()
            records.append(
                ReportQueryRecord(
                    source_path=str(path),
                    line_no=line_no,
                    original=original,
                    evidence_row=evidence_row,
                    row_kind=row_kind,
                    validation_issues=validation_issues,
                )
            )
    return tuple(records)


def filter_report_query_records(
    records: Iterable[ReportQueryRecord],
    filters: ReportQueryFilters,
) -> tuple[ReportQueryRecord, ...]:
    """Filter records using only shared evidence-row fields."""

    return tuple(record for record in records if _matches(record.evidence_row, filters))


def report_query_rows_to_jsonable(records: Iterable[ReportQueryRecord]) -> list[dict[str, Any]]:
    """Return a stable JSON-friendly projection for query output."""

    return [
        {
            "source_path": record.source_path,
            "line_no": record.line_no,
            "row_kind": record.row_kind,
            "validation_issues": list(record.validation_issues),
            "evidence_row": dict(record.evidence_row),
            "record": dict(record.original),
        }
        for record in records
    ]


def format_report_query_rows(records: Iterable[ReportQueryRecord]) -> str:
    """Render compact text lines for shared evidence rows."""

    lines: list[str] = []
    for record in records:
        row = record.evidence_row
        row_id = _scalar(row.get("row_id") or row.get("finding_id"))
        status = _scalar(row.get("status") or record.row_kind)
        source = _scalar(row.get("source_artifact_id"))
        locator = _scalar(row.get("source_locator") or _evidence_value(row, "codify_path"))
        rule_id = _scalar(row.get("rule_id"))
        phase = _scalar(row.get("phase"))
        finding_ids = row.get("finding_ids", ())
        finding_text = ""
        if isinstance(finding_ids, (list, tuple)) and finding_ids:
            finding_text = " findings=" + ",".join(str(item) for item in finding_ids)
        if rule_id:
            finding_text = f" rule={rule_id}"
        phase_text = f" phase={phase}" if phase else ""
        invalid_text = f" invalid={len(record.validation_issues)}" if record.validation_issues else ""
        lines.append(
            f"{row_id} {status} {source} {locator}{phase_text}{finding_text}{invalid_text}".rstrip()
        )
        for issue in record.validation_issues:
            lines.append(f"  issue: {issue}")
    return "\n".join(lines)


def main(args: Any) -> None:
    command = str(getattr(args, "report_command", "") or "")
    if command != "query":
        raise SystemExit(f"unknown report command: {command}")
    paths = tuple(str(path) for path in getattr(args, "paths", ()) or ())
    if not paths:
        raise SystemExit("report query requires at least one JSONL path")
    records = load_report_query_records(paths, validate=bool(getattr(args, "validate", False)))
    filters = ReportQueryFilters(
        row_id=str(getattr(args, "row_id", "") or ""),
        status=str(getattr(args, "status", "") or ""),
        rule_id=str(getattr(args, "rule_id", "") or ""),
        phase=str(getattr(args, "phase", "") or ""),
        source_artifact=str(getattr(args, "source_artifact", "") or ""),
        source_unit=str(getattr(args, "source_unit", "") or ""),
        locator=str(getattr(args, "locator", "") or ""),
        blocking=bool(getattr(args, "blocking", False)),
    )
    selected = filter_report_query_records(records, filters)
    limit = int(getattr(args, "limit", 0) or 0)
    if limit > 0:
        selected = selected[:limit]
    if getattr(args, "json", False):
        print(json.dumps(report_query_rows_to_jsonable(selected), ensure_ascii=False, indent=2))
    else:
        print(format_report_query_rows(selected))
    if getattr(args, "validate", False) and any(record.validation_issues for record in selected):
        raise SystemExit(1)


def _matches(row: Mapping[str, Any], filters: ReportQueryFilters) -> bool:
    if filters.row_id and filters.row_id not in {_scalar(row.get("row_id")), _scalar(row.get("finding_id"))}:
        return False
    if filters.status and _scalar(row.get("status")) != filters.status:
        return False
    if filters.rule_id and filters.rule_id not in _rule_ids(row):
        return False
    if filters.phase and _scalar(row.get("phase")) != filters.phase:
        return False
    if filters.source_artifact and _scalar(row.get("source_artifact_id")) != filters.source_artifact:
        return False
    if filters.source_unit and _scalar(row.get("source_unit_id")) != filters.source_unit:
        return False
    if filters.locator and filters.locator not in {_scalar(row.get("source_locator")), _evidence_value(row, "codify_path")}:
        return False
    if filters.blocking and row.get("blocking") is not True:
        return False
    return True


def _classify_evidence_row(row: Mapping[str, Any]) -> str:
    if "finding_id" in row or "rule_id" in row:
        return "finding"
    return "operation"


def _validate_evidence_row(row: Mapping[str, Any], row_kind: str) -> tuple[str, ...]:
    if row_kind == "finding":
        return validate_corpus_finding_evidence_row(row)
    return validate_corpus_operation_evidence_row(row)


def _rule_ids(row: Mapping[str, Any]) -> set[str]:
    values = {_scalar(row.get("rule_id"))}
    finding_ids = row.get("finding_ids", ())
    if isinstance(finding_ids, (list, tuple)):
        values.update(str(value) for value in finding_ids)
    return {value for value in values if value}


def _evidence_value(row: Mapping[str, Any], key: str) -> str:
    evidence = row.get("evidence") or row.get("detail") or {}
    if not isinstance(evidence, Mapping):
        return ""
    value = evidence.get(key)
    if isinstance(value, (list, tuple)):
        return "|".join(str(part) for part in value)
    return _scalar(value)


def _scalar(value: Any) -> str:
    return str(value or "")

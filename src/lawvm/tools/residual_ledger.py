"""Residual ledger helper for Tranche 0 Finland review flow."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


RESIDUAL_LEDGER_COLUMNS: tuple[str, ...] = (
    "statute_id",
    "path",
    "observed_symptom",
    "suspected_first_bad_phase",
    "confirmed_first_bad_phase",
    "interaction_family",
    "secondary_phase",
    "source_pathology_present",
    "oracle_or_editorial_witness_drift",
    "source_lane_used",
    "fix_owner",
    "regression_ids",
    "status",
    "notes",
)

_PHASE_VALUES = frozenset(
    {
        "",
        "acquire",
        "parse",
        "clause_surface",
        "payload_surface",
        "lowering",
        "replay_fold",
        "materialization",
        "verification",
    }
)
_TRISTATE_VALUES = frozenset({"", "yes", "no", "unknown"})


def _normalize_scalar(value: Any) -> str:
    return str(value or "").strip()


def validate_residual_ledger(path: str | Path) -> dict[str, Any]:
    ledger_path = Path(path)
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        errors: list[str] = []
        warnings: list[str] = []
        rows_checked = 0

        if fieldnames != RESIDUAL_LEDGER_COLUMNS:
            errors.append(
                "header mismatch: expected "
                + ",".join(RESIDUAL_LEDGER_COLUMNS)
                + " got "
                + ",".join(fieldnames)
            )

        for row_index, row in enumerate(reader, start=2):
            normalized = {key: _normalize_scalar(value) for key, value in row.items()}
            if not any(normalized.values()):
                continue
            rows_checked += 1
            statute_id = normalized["statute_id"]
            observed_symptom = normalized["observed_symptom"]
            if not statute_id:
                errors.append(f"row {row_index}: statute_id is required")
            if not observed_symptom:
                errors.append(f"row {row_index}: observed_symptom is required")

            for phase_field in (
                "suspected_first_bad_phase",
                "confirmed_first_bad_phase",
                "secondary_phase",
            ):
                phase_value = normalized[phase_field]
                if phase_value not in _PHASE_VALUES:
                    errors.append(
                        f"row {row_index}: {phase_field} must be one of "
                        + ", ".join(sorted(_PHASE_VALUES - {""}))
                    )

            for tristate_field in (
                "source_pathology_present",
                "oracle_or_editorial_witness_drift",
            ):
                tristate_value = normalized[tristate_field].lower()
                if tristate_value not in _TRISTATE_VALUES:
                    errors.append(
                        f"row {row_index}: {tristate_field} must be one of yes/no/unknown"
                    )

            if normalized["confirmed_first_bad_phase"] and not normalized["suspected_first_bad_phase"]:
                warnings.append(
                    f"row {row_index}: confirmed_first_bad_phase set without suspected_first_bad_phase"
                )

    return {
        "schema": "lawvm.residual_ledger_validation.v1",
        "path": str(ledger_path),
        "ok": not errors,
        "rows_checked": rows_checked,
        "errors": errors,
        "warnings": warnings,
    }


def build_row_from_phase_witness(
    witness: dict[str, Any],
    *,
    observed_symptom: str,
    path: str = "",
    interaction_family: str = "",
    suspected_first_bad_phase: str = "",
    confirmed_first_bad_phase: str = "",
    secondary_phase: str = "",
    source_pathology_present: str = "",
    oracle_or_editorial_witness_drift: str = "",
    fix_owner: str = "",
    regression_ids: str = "",
    status: str = "open",
    notes: str = "",
) -> dict[str, str]:
    acquisition = witness.get("acquisition") or {}
    target_path = _normalize_scalar(path) or _normalize_scalar(witness.get("target_path"))
    if target_path in {"", "(all)"}:
        target_path = ""
    row = {column: "" for column in RESIDUAL_LEDGER_COLUMNS}
    row["statute_id"] = _normalize_scalar(witness.get("statute_id"))
    row["path"] = target_path
    row["observed_symptom"] = _normalize_scalar(observed_symptom)
    row["suspected_first_bad_phase"] = _normalize_scalar(suspected_first_bad_phase)
    row["confirmed_first_bad_phase"] = _normalize_scalar(confirmed_first_bad_phase)
    row["interaction_family"] = _normalize_scalar(interaction_family)
    row["secondary_phase"] = _normalize_scalar(secondary_phase)
    row["source_pathology_present"] = _normalize_scalar(source_pathology_present)
    row["oracle_or_editorial_witness_drift"] = _normalize_scalar(oracle_or_editorial_witness_drift)
    row["source_lane_used"] = _normalize_scalar(acquisition.get("source_lane_used"))
    row["fix_owner"] = _normalize_scalar(fix_owner)
    row["regression_ids"] = _normalize_scalar(regression_ids) or _normalize_scalar(witness.get("source_id"))
    row["status"] = _normalize_scalar(status)
    row["notes"] = _normalize_scalar(notes)
    return row


def _format_validation_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Ledger   : {payload['path']}",
        f"Rows     : {payload['rows_checked']}",
        f"Status   : {'ok' if payload['ok'] else 'invalid'}",
    ]
    for warning in payload.get("warnings") or []:
        lines.append(f"Warning  : {warning}")
    for error in payload.get("errors") or []:
        lines.append(f"Error    : {error}")
    return "\n".join(lines)


def main(args: Any) -> None:
    command = str(getattr(args, "residual_ledger_command", "") or "")
    if command == "validate":
        payload = validate_residual_ledger(getattr(args, "path", "notes/RESIDUAL_BUG_LEDGER_TEMPLATE.csv"))
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        print(_format_validation_text(payload))
        if not payload["ok"]:
            raise SystemExit(1)
        return

    if command == "row":
        witness_path = Path(str(getattr(args, "witness", "") or ""))
        witness = json.loads(witness_path.read_text(encoding="utf-8"))
        row = build_row_from_phase_witness(
            witness,
            observed_symptom=str(getattr(args, "observed_symptom", "") or ""),
            path=str(getattr(args, "path", "") or ""),
            interaction_family=str(getattr(args, "interaction_family", "") or ""),
            suspected_first_bad_phase=str(getattr(args, "suspected_first_bad_phase", "") or ""),
            confirmed_first_bad_phase=str(getattr(args, "confirmed_first_bad_phase", "") or ""),
            secondary_phase=str(getattr(args, "secondary_phase", "") or ""),
            source_pathology_present=str(getattr(args, "source_pathology_present", "") or ""),
            oracle_or_editorial_witness_drift=str(getattr(args, "oracle_or_editorial_witness_drift", "") or ""),
            fix_owner=str(getattr(args, "fix_owner", "") or ""),
            regression_ids=str(getattr(args, "regression_ids", "") or ""),
            status=str(getattr(args, "status", "open") or "open"),
            notes=str(getattr(args, "notes", "") or ""),
        )
        if getattr(args, "json", False):
            print(json.dumps(row, ensure_ascii=False, indent=2))
            return
        writer = csv.DictWriter(
            __import__("sys").stdout,
            fieldnames=list(RESIDUAL_LEDGER_COLUMNS),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(row)
        return

    raise SystemExit(f"unknown residual-ledger command: {command}")

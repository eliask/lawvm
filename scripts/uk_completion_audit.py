#!/usr/bin/env python3
"""Audit whether current UK reports support a bounded completion declaration.

This report is not replay authority. It binds the broad-baseline evidence report
and the remaining-work item export into one machine-readable declaration gate.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


_EXPECTED_JURISDICTION = "uk"
_EXPECTED_BROAD_REPORT_KIND = "uk_broad_baseline_agreement_report"
_EXPECTED_BROAD_SCHEMA = "lawvm.uk_broad_baseline_agreement_report.v1"
_EXPECTED_REMAINING_REPORT_KIND = "uk_remaining_work_summary.v1"
_DEFAULT_DECLARATION_ID = "uk_source_only_current_state_public_xml_effects_full_farchive"
_SUPPORTED_STATEMENT = (
    "UK source-only current-state replay is complete for the declared public "
    "XML/effects corpus slice, modulo typed non-executable frontiers and "
    "source/oracle pathologies."
)
_FORBIDDEN_SHORTCUTS = (
    "completion_audit_as_replay_authorization",
    "manual_frontier_as_executable",
    "remaining_work_item_as_legal_state",
    "oracle_score_as_source_truth",
)


@dataclass(frozen=True)
class AuditGate:
    gate_id: str
    status: str
    observed: Any
    expected: Any
    source: str
    reason: str


def load_completion_audit(
    broad_report_path: Path,
    remaining_work_path: Path,
    *,
    declaration_id: str = _DEFAULT_DECLARATION_ID,
) -> dict[str, Any]:
    broad_report = _load_json(broad_report_path)
    remaining_report = _load_json(remaining_work_path)
    broad_summary = _mapping(broad_report.get("summary"))
    remaining_summary = _mapping(remaining_report.get("summary"))

    broad_validation_gates = _validate_broad_report(broad_report)
    remaining_validation_gates = _validate_remaining_report(remaining_report)
    gates = [
        *broad_validation_gates,
        *remaining_validation_gates,
        *_broad_completion_gates(broad_summary),
        *_remaining_completion_gates(remaining_summary),
    ]
    failed_gates = [gate for gate in gates if gate.status == "fail"]
    supported = not failed_gates
    declaration_status = "supported" if supported else "not_supported"
    lane_counts = _mapping(remaining_summary.get("lane_counts"))

    return {
        "report_kind": "uk_completion_audit.v1",
        "truth_claim": "completion_declaration_audit_not_replay_authority",
        "safe_default": "keep_frontiers_non_executable_until_required_proofs_exist",
        "forbidden_shortcuts": _FORBIDDEN_SHORTCUTS,
        "declaration": {
            "declaration_id": declaration_id,
            "jurisdiction": "uk",
            "regime": "source_only_current_state_replay",
            "corpus_slice": "declared public XML/effects corpus slice",
            "status": declaration_status,
            "statement": _SUPPORTED_STATEMENT,
            "modulo": (
                "typed_non_executable_remaining_work_frontiers",
                "official_source_pathologies",
                "oracle_surface_or_editorial_pathologies",
                "manual_claim_frontiers",
            ),
        },
        "summary": {
            "supported": supported,
            "failed_gate_count": len(failed_gates),
            "gate_count": len(gates),
            "item_count": _int(remaining_summary.get("item_count")),
            "lane_count": _int(remaining_summary.get("lane_count")),
            "lane_counts": dict(sorted(lane_counts.items())),
            "completion_gate_clean": bool(
                broad_summary.get("completion_gate_clean")
            )
            and bool(remaining_summary.get("completion_gate_clean")),
            "active_unclassified_residual_count": _int(
                broad_summary.get("active_unclassified_residual_count")
            ),
            "deterministic_frontend_candidate_count": _int(
                broad_summary.get("deterministic_frontend_candidate_count")
            ),
            "non_manual_source_chain_frontier_count": _int(
                broad_summary.get("non_manual_source_chain_frontier_count")
            ),
            "mutation_boundary_unexplained_report_count": _int(
                broad_summary.get("mutation_boundary_unexplained_report_count")
            ),
            "mutation_boundary_unexplained_path_count": _int(
                broad_summary.get("mutation_boundary_unexplained_path_count")
            ),
            "item_authorization_status_counts": dict(
                sorted(
                    _mapping(
                        remaining_summary.get("item_authorization_status_counts")
                    ).items()
                )
            ),
            "item_safety_gap_counts": dict(
                sorted(_mapping(remaining_summary.get("item_safety_gap_counts")).items())
            ),
        },
        "gates": [asdict(gate) for gate in gates],
        "inputs": {
            "broad_report": str(broad_report_path),
            "remaining_work_report": str(remaining_work_path),
        },
    }


def _validate_broad_report(report: Mapping[str, Any]) -> tuple[AuditGate, ...]:
    return (
        _gate(
            "broad_report_jurisdiction",
            report.get("jurisdiction"),
            _EXPECTED_JURISDICTION,
            source="broad_report",
            reason="input must be a UK evidence report",
        ),
        _gate(
            "broad_report_kind",
            report.get("report_kind"),
            _EXPECTED_BROAD_REPORT_KIND,
            source="broad_report",
            reason="input must be a broad-baseline agreement report",
        ),
        _gate(
            "broad_report_schema",
            report.get("schema"),
            _EXPECTED_BROAD_SCHEMA,
            source="broad_report",
            reason="input must use the expected broad-baseline schema",
        ),
    )


def _validate_remaining_report(report: Mapping[str, Any]) -> tuple[AuditGate, ...]:
    return (
        _gate(
            "remaining_report_kind",
            report.get("report_kind"),
            _EXPECTED_REMAINING_REPORT_KIND,
            source="remaining_work_report",
            reason="input must be a remaining-work summary report",
        ),
    )


def _broad_completion_gates(summary: Mapping[str, Any]) -> tuple[AuditGate, ...]:
    return (
        _gate(
            "broad_completion_gate_clean",
            bool(summary.get("completion_gate_clean")),
            True,
            source="broad_report.summary",
            reason="the broad completion gate must already be clean",
        ),
        _zero_gate(
            "broad_active_unclassified_residuals",
            summary.get("active_unclassified_residual_count"),
            source="broad_report.summary",
            reason="unclassified residuals need phase ownership before completion",
        ),
        _zero_gate(
            "broad_deterministic_frontend_candidates",
            summary.get("deterministic_frontend_candidate_count"),
            source="broad_report.summary",
            reason="deterministic frontend candidates must be resolved or reclassified",
        ),
        _zero_gate(
            "broad_non_manual_source_chain_frontiers",
            summary.get("non_manual_source_chain_frontier_count"),
            source="broad_report.summary",
            reason="source-chain frontiers must be typed as manual/pathology work",
        ),
        _zero_gate(
            "broad_manual_frontier_template_gaps",
            summary.get("manual_frontier_template_gap_count"),
            source="broad_report.summary",
            reason="manual frontiers must have packet templates before declaration",
        ),
        _zero_gate(
            "broad_mutation_boundary_unexplained_reports",
            summary.get("mutation_boundary_unexplained_report_count"),
            source="broad_report.summary",
            reason="mutation-boundary reports may not contain unexplained changes",
        ),
        _zero_gate(
            "broad_mutation_boundary_unexplained_paths",
            summary.get("mutation_boundary_unexplained_path_count"),
            source="broad_report.summary",
            reason="mutation-boundary paths may not contain unexplained changes",
        ),
    )


def _remaining_completion_gates(summary: Mapping[str, Any]) -> tuple[AuditGate, ...]:
    lane_counts = _mapping(summary.get("lane_counts"))
    item_exported_lane_counts = _mapping(summary.get("item_exported_lane_counts"))
    item_authorization_status_counts = _mapping(
        summary.get("item_authorization_status_counts")
    )
    executable_item_status_counts = {
        key: value
        for key, value in item_authorization_status_counts.items()
        if key != "non_executable_work_item" and _int(value) > 0
    }
    expected_item_count = sum(_int(value) for value in lane_counts.values())
    exported_item_count = sum(
        _int(value) for value in item_exported_lane_counts.values()
    )
    return (
        _gate(
            "remaining_completion_gate_clean",
            bool(summary.get("completion_gate_clean")),
            True,
            source="remaining_work_report.summary",
            reason="remaining-work summary must preserve the clean completion gate",
        ),
        _zero_gate(
            "remaining_active_unclassified_residuals",
            summary.get("active_unclassified_residual_count"),
            source="remaining_work_report.summary",
            reason="remaining-work export must not hide unclassified residuals",
        ),
        _zero_gate(
            "remaining_deterministic_frontend_candidates",
            summary.get("deterministic_frontend_candidate_count"),
            source="remaining_work_report.summary",
            reason="remaining-work export must not hide deterministic candidates",
        ),
        _zero_gate(
            "remaining_non_manual_source_chain_frontiers",
            summary.get("non_manual_source_chain_frontier_count"),
            source="remaining_work_report.summary",
            reason="remaining-work export must not hide source-chain frontiers",
        ),
        _zero_gate(
            "remaining_mutation_boundary_unexplained_reports",
            summary.get("mutation_boundary_unexplained_report_count"),
            source="remaining_work_report.summary",
            reason="remaining-work export must preserve mutation-boundary safety",
        ),
        _zero_gate(
            "remaining_mutation_boundary_unexplained_paths",
            summary.get("mutation_boundary_unexplained_path_count"),
            source="remaining_work_report.summary",
            reason="remaining-work export must preserve mutation-boundary safety",
        ),
        _gate(
            "remaining_items_fully_exported",
            bool(summary.get("item_fully_exported")),
            True,
            source="remaining_work_report.summary",
            reason="every selected remaining-work row must be exported as an item",
        ),
        _zero_gate(
            "remaining_item_unexported_rows",
            summary.get("item_unexported_row_count"),
            source="remaining_work_report.summary",
            reason="no selected remaining-work rows may be missing item packets",
        ),
        _gate(
            "remaining_item_unexported_lanes",
            _tuple_sorted(summary.get("item_unexported_lane_ids")),
            (),
            source="remaining_work_report.summary",
            reason="all remaining-work lanes must be represented in item export",
        ),
        _gate(
            "remaining_item_safety_gaps",
            _mapping(summary.get("item_safety_gap_counts")),
            {},
            source="remaining_work_report.summary",
            reason="remaining-work items may not be executable or packet-incomplete",
        ),
        _gate(
            "remaining_items_non_executable",
            executable_item_status_counts,
            {},
            source="remaining_work_report.summary",
            reason="remaining-work items must not authorize replay",
        ),
        _gate(
            "remaining_item_expected_count_matches_lanes",
            _int(summary.get("item_expected_row_count")),
            expected_item_count,
            source="remaining_work_report.summary",
            reason="expected item rows must match remaining-work lane counts",
        ),
        _gate(
            "remaining_item_exported_count_matches_lanes",
            exported_item_count,
            _int(summary.get("item_exported_row_count")),
            source="remaining_work_report.summary",
            reason="exported item lane counts must match exported row count",
        ),
        _gate(
            "remaining_item_count_matches_export",
            _int(summary.get("item_count")),
            _int(summary.get("item_exported_row_count")),
            source="remaining_work_report.summary",
            reason="reported item count must equal exported row count",
        ),
        _gate(
            "remaining_item_lane_count_matches_export",
            _int(summary.get("item_exported_lane_count")),
            _int(summary.get("lane_count")),
            source="remaining_work_report.summary",
            reason="all remaining-work lanes must be exported",
        ),
    )


def _gate(
    gate_id: str,
    observed: Any,
    expected: Any,
    *,
    source: str,
    reason: str,
) -> AuditGate:
    status = "pass" if observed == expected else "fail"
    return AuditGate(
        gate_id=gate_id,
        status=status,
        observed=observed,
        expected=expected,
        source=source,
        reason=reason,
    )


def _zero_gate(
    gate_id: str,
    observed: Any,
    *,
    source: str,
    reason: str,
) -> AuditGate:
    return _gate(gate_id, _int(observed), 0, source=source, reason=reason)


def _load_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tuple_sorted(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted(str(item) for item in value))


def _emit_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _emit_text(payload: Mapping[str, Any]) -> str:
    summary = _mapping(payload.get("summary"))
    declaration = _mapping(payload.get("declaration"))
    lines = [
        "UK completion audit",
        f"  status={declaration.get('status', 'not_supported')}",
        f"  declaration_id={declaration.get('declaration_id', '')}",
        f"  failed_gates={summary.get('failed_gate_count', 0)}/"
        f"{summary.get('gate_count', 0)}",
        f"  item_count={summary.get('item_count', 0)} "
        f"lane_count={summary.get('lane_count', 0)}",
    ]
    if declaration.get("status") == "supported":
        lines.append(f"  statement={declaration.get('statement', '')}")
    failed_gates = [
        gate for gate in payload.get("gates", []) if gate.get("status") == "fail"
    ]
    if failed_gates:
        lines.append("  failed:")
        for gate in failed_gates:
            lines.append(
                "    "
                f"{gate.get('gate_id')}: observed={gate.get('observed')!r} "
                f"expected={gate.get('expected')!r}"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit whether UK evidence reports support a completion declaration."
    )
    parser.add_argument("broad_report", type=Path, help="UK broad-baseline report JSON")
    parser.add_argument(
        "remaining_work_report",
        type=Path,
        help="UK remaining-work summary JSON exported with --include-items",
    )
    parser.add_argument(
        "--declaration-id",
        default=_DEFAULT_DECLARATION_ID,
        help="stable declaration identifier to include in the audit report",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="exit nonzero when any completion gate fails",
    )
    args = parser.parse_args(argv)

    payload = load_completion_audit(
        args.broad_report,
        args.remaining_work_report,
        declaration_id=args.declaration_id,
    )
    if args.format == "json":
        print(_emit_json(payload))
    else:
        print(_emit_text(payload))
    if args.fail_on_incomplete and not payload["summary"]["supported"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared evidence-pack export for New Zealand corpus surfaces.

This module does not infer new legal effects. It only bundles existing NZ
operation witness, effect candidate, preflight, and instruction-workqueue
evidence rows into one report-query-compatible JSONL surface.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.evidence_contracts import (
    CorpusFindingEvidenceRow,
    CorpusOperationEvidenceRow,
    evidence_row_kind,
    evidence_rule_ids,
)
from lawvm.new_zealand.effect_candidates import (
    NZCanonicalEffectCandidateReport,
    NZEffectCandidatePreflightReport,
    build_archived_work_effect_candidate_preflight,
    build_archived_work_effect_candidate_surface,
)
from lawvm.new_zealand.instruction_workqueue import (
    NZInstructionWorkQueueReport,
    build_archived_work_instruction_workqueue,
)
from lawvm.new_zealand.operation_surface import NZOperationSurfaceReport, build_archived_work_operation_surface


@dataclass(frozen=True)
class NZEvidencePackReport:
    work_id: str
    operation_surface: NZOperationSurfaceReport
    effect_candidates: NZCanonicalEffectCandidateReport
    candidate_preflight: NZEffectCandidatePreflightReport
    instruction_workqueue: NZInstructionWorkQueueReport

    def summary(self) -> dict[str, Any]:
        operation_rows = self.operation_surface.operation_evidence_rows()
        operation_findings = self.operation_surface.finding_evidence_rows()
        candidate_rows = self.effect_candidates.operation_evidence_rows()
        candidate_summary = self.effect_candidates.summary()
        preflight_rows = self.candidate_preflight.operation_evidence_rows()
        preflight_findings = self.candidate_preflight.finding_evidence_rows()
        preflight_summary = self.candidate_preflight.summary()
        instruction_rows = self.instruction_workqueue.operation_evidence_rows()
        instruction_summary = self.instruction_workqueue.summary()
        evidence_rows = tuple(
            row.to_dict()
            for row in (
                *operation_rows,
                *operation_findings,
                *candidate_rows,
                *preflight_rows,
                *preflight_findings,
                *instruction_rows,
            )
        )
        total_rows = (
            len(operation_rows)
            + len(operation_findings)
            + len(candidate_rows)
            + len(preflight_rows)
            + len(preflight_findings)
            + len(instruction_rows)
        )
        return {
            "work_id": self.work_id,
            "operation_evidence_rows": len(operation_rows),
            "operation_finding_rows": len(operation_findings),
            "effect_candidate_evidence_rows": len(candidate_rows),
            "effect_candidate_emitted_rows": candidate_summary["candidate_emitted_rows"],
            "effect_candidate_operation_missing_rows": candidate_summary["candidate_operation_missing_rows"],
            "effect_candidate_status_counts": candidate_summary["candidate_status_counts"],
            "effect_candidate_action_counts": candidate_summary["candidate_action_counts"],
            "effect_candidate_operation_family_counts": candidate_summary["operation_family_counts"],
            "effect_candidate_blocked_operation_family_counts": candidate_summary["blocked_operation_family_counts"],
            "effect_candidate_blocking_rule_counts": candidate_summary["candidate_blocking_rule_counts"],
            "effect_candidate_blocked_operation_family_rule_counts": candidate_summary[
                "blocked_operation_family_rule_counts"
            ],
            "effect_candidate_blocked_operation_family_payload_shape_counts": candidate_summary[
                "blocked_operation_family_payload_shape_counts"
            ],
            "effect_candidate_blocked_operation_family_payload_safety_counts": candidate_summary[
                "blocked_operation_family_payload_safety_counts"
            ],
            "effect_candidate_blocked_operation_family_target_status_counts": candidate_summary[
                "blocked_operation_family_target_status_counts"
            ],
            "effect_candidate_blocked_operation_family_instruction_status_counts": candidate_summary[
                "blocked_operation_family_instruction_status_counts"
            ],
            "effect_candidate_payload_structural_subfamily_status_counts": candidate_summary[
                "payload_structural_subfamily_status_counts"
            ],
            "effect_candidate_payload_structural_subfamily_counts": candidate_summary[
                "payload_structural_subfamily_counts"
            ],
            "effect_candidate_witness_rule_counts": candidate_summary["candidate_witness_rule_counts"],
            "effect_candidate_action_witness_rule_counts": candidate_summary["candidate_action_witness_rule_counts"],
            "effect_candidate_action_source_change_text_witness_status_counts": candidate_summary[
                "candidate_action_source_change_text_witness_status_counts"
            ],
            "effect_candidate_text_replace_witness_support_status_counts": candidate_summary[
                "text_replace_witness_support_status_counts"
            ],
            "effect_candidate_action_text_replace_witness_support_status_counts": candidate_summary[
                "candidate_action_text_replace_witness_support_status_counts"
            ],
            "effect_candidate_blocked_operation_family_source_change_text_witness_status_counts": candidate_summary[
                "blocked_operation_family_source_change_text_witness_status_counts"
            ],
            "effect_candidate_latest_oracle_text_status_counts": candidate_summary["latest_oracle_text_status_counts"],
            "effect_candidate_source_version_date_window_status_counts": candidate_summary[
                "source_version_date_window_status_counts"
            ],
            "effect_candidate_source_change_text_witness_status_counts": candidate_summary[
                "source_change_text_witness_status_counts"
            ],
            "effect_candidate_repeal_payload_corroboration_status_counts": candidate_summary[
                "repeal_payload_corroboration_status_counts"
            ],
            "effect_candidate_operations": candidate_summary["candidate_operations"],
            "candidate_preflight_replayable_candidate_operations": preflight_summary[
                "replayable_candidate_operations"
            ],
            "candidate_preflight_source_change_only_candidate_rows": preflight_summary[
                "source_change_only_candidate_rows"
            ],
            "candidate_preflight_target_recovery_candidate_rows": preflight_summary[
                "target_recovery_candidate_rows"
            ],
            "candidate_preflight_operations_to_replay": preflight_summary["operations_to_replay"],
            "candidate_preflight_blocking_rule_counts": preflight_summary["blocking_rule_counts"],
            "candidate_preflight_evidence_rows": len(preflight_rows),
            "candidate_preflight_finding_rows": len(preflight_findings),
            "instruction_workqueue_evidence_rows": len(instruction_rows),
            "instruction_workqueue_queue_status_counts": instruction_summary["queue_status_counts"],
            "instruction_workqueue_candidate_rows": instruction_summary["candidate_instruction_rows"],
            "instruction_workqueue_review_rows": instruction_summary["review_instruction_rows"],
            "instruction_workqueue_blocked_rows": instruction_summary["blocked_instruction_rows"],
            "instruction_workqueue_not_required_rows": instruction_summary["not_required_rows"],
            "instruction_workqueue_latest_oracle_text_status_counts": instruction_summary[
                "latest_oracle_text_status_counts"
            ],
            "instruction_workqueue_latest_oracle_target_resolution_status_counts": instruction_summary[
                "latest_oracle_target_resolution_status_counts"
            ],
            "instruction_workqueue_structural_subfamily_status_counts": instruction_summary[
                "payload_structural_subfamily_status_counts"
            ],
            "instruction_workqueue_structural_subfamily_counts": instruction_summary[
                "payload_structural_subfamily_counts"
            ],
            "row_kind_counts": _row_kind_counts(evidence_rows),
            "surface_status_counts": _surface_status_counts(evidence_rows),
            "surface_rule_id_counts": _surface_rule_id_counts(evidence_rows),
            "blocking_rule_id_counts": _blocking_rule_id_counts(evidence_rows),
            "total_evidence_rows": total_rows,
            "replay_claims": False,
            "canonical_effect_claims": False,
        }

    def evidence_rows(self) -> tuple[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow, ...]:
        rows: list[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow] = []
        rows.extend(self.operation_surface.operation_evidence_rows())
        rows.extend(self.operation_surface.finding_evidence_rows())
        rows.extend(self.effect_candidates.operation_evidence_rows())
        rows.extend(self.candidate_preflight.operation_evidence_rows())
        rows.extend(self.candidate_preflight.finding_evidence_rows())
        rows.extend(self.instruction_workqueue.operation_evidence_rows())
        return tuple(rows)

    def filtered_evidence_rows(
        self,
        *,
        surface: str = "",
        row_kind: str = "",
        status: str = "",
        rule_id: str = "",
        blocking: bool = False,
    ) -> tuple[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow, ...]:
        rows = self.evidence_rows()
        return tuple(
            row
            for row in rows
            if _evidence_row_matches(
                row,
                surface=surface,
                row_kind=row_kind,
                status=status,
                rule_id=rule_id,
                blocking=blocking,
            )
        )

    def to_jsonable(
        self,
        *,
        row_limit: int | None = None,
        surface: str = "",
        row_kind: str = "",
        status: str = "",
        rule_id: str = "",
        blocking: bool = False,
    ) -> dict[str, Any]:
        rows = self.filtered_evidence_rows(
            surface=surface,
            row_kind=row_kind,
            status=status,
            rule_id=rule_id,
            blocking=blocking,
        )
        selected_rows = rows if row_limit is None else rows[:row_limit]
        row_dicts = tuple(row.to_dict() for row in rows)
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "shared_evidence_pack",
            "truth_claim": "evidence_rows_only",
            "replay_claims": False,
            "canonical_effect_claims": False,
            "summary": self.summary(),
            "filters": _jsonable_filters(
                surface=surface,
                row_kind=row_kind,
                status=status,
                rule_id=rule_id,
                blocking=blocking,
            ),
            "filtered_summary": _evidence_rows_summary(row_dicts),
            "filtered_evidence_rows": len(rows),
            "evidence_rows": [row.to_dict() for row in selected_rows],
        }
        if row_limit is not None and len(rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(rows) - row_limit
        return payload


def build_evidence_pack_report(
    *,
    work_id: str,
    operation_surface: NZOperationSurfaceReport,
    effect_candidates: NZCanonicalEffectCandidateReport,
    candidate_preflight: NZEffectCandidatePreflightReport,
    instruction_workqueue: NZInstructionWorkQueueReport,
) -> NZEvidencePackReport:
    return NZEvidencePackReport(
        work_id=work_id,
        operation_surface=operation_surface,
        effect_candidates=effect_candidates,
        candidate_preflight=candidate_preflight,
        instruction_workqueue=instruction_workqueue,
    )


def build_archived_work_evidence_pack_report(db_path: Path, work_id: str) -> NZEvidencePackReport:
    return build_evidence_pack_report(
        work_id=work_id,
        operation_surface=build_archived_work_operation_surface(db_path, work_id),
        effect_candidates=build_archived_work_effect_candidate_surface(db_path, work_id),
        candidate_preflight=build_archived_work_effect_candidate_preflight(db_path, work_id),
        instruction_workqueue=build_archived_work_instruction_workqueue(db_path, work_id),
    )


def write_evidence_pack_jsonl(
    report: NZEvidencePackReport,
    path: Path,
    *,
    surface: str = "",
    row_kind: str = "",
    status: str = "",
    rule_id: str = "",
    blocking: bool = False,
) -> int:
    rows = [
        row.to_dict()
        for row in report.filtered_evidence_rows(
            row_kind=row_kind,
            surface=surface,
            status=status,
            rule_id=rule_id,
            blocking=blocking,
        )
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return len(rows)


def main(args: Any) -> None:
    report = build_archived_work_evidence_pack_report(Path(args.db), args.work_id)
    output_row_count: int | None = None
    if args.output_jsonl:
        output_row_count = write_evidence_pack_jsonl(
            report,
            Path(args.output_jsonl),
            row_kind=args.row_kind,
            surface=args.surface,
            status=args.status,
            rule_id=args.rule_id,
            blocking=args.blocking,
        )
    if args.json:
        payload = report.to_jsonable(
            row_limit=args.limit,
            row_kind=args.row_kind,
            surface=args.surface,
            status=args.status,
            rule_id=args.rule_id,
            blocking=args.blocking,
        )
        if output_row_count is not None:
            payload["output_jsonl"] = {
                "path": args.output_jsonl,
                "rows": output_row_count,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if output_row_count is not None:
        print(f"wrote_evidence_rows={output_row_count} path={args.output_jsonl}")
    summary = report.summary()
    filters = _jsonable_filters(
        surface=args.surface,
        row_kind=args.row_kind,
        status=args.status,
        rule_id=args.rule_id,
        blocking=args.blocking,
    )
    print(
        f"work_id={summary['work_id']} total_evidence_rows={summary['total_evidence_rows']} "
        f"filtered_rows={len(report.filtered_evidence_rows(surface=args.surface, row_kind=args.row_kind, status=args.status, rule_id=args.rule_id, blocking=args.blocking))} "
        f"filters={filters} "
        f"operation_rows={summary['operation_evidence_rows']} "
        f"candidate_rows={summary['effect_candidate_evidence_rows']} "
        f"preflight_rows={summary['candidate_preflight_evidence_rows']} "
        f"instruction_rows={summary['instruction_workqueue_evidence_rows']}"
    )


def _evidence_row_matches(
    row: CorpusOperationEvidenceRow | CorpusFindingEvidenceRow,
    *,
    surface: str,
    row_kind: str,
    status: str,
    rule_id: str,
    blocking: bool,
) -> bool:
    row_dict = row.to_dict()
    if surface and _surface(row_dict) != surface:
        return False
    if row_kind and _row_kind(row_dict) != row_kind:
        return False
    if status and str(row_dict.get("status", "")) != status:
        return False
    if rule_id and rule_id not in _rule_ids(row_dict):
        return False
    if blocking and row_dict.get("blocking") is not True:
        return False
    return True


def _jsonable_filters(
    *,
    surface: str,
    row_kind: str,
    status: str,
    rule_id: str,
    blocking: bool,
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        key: value
        for key, value in {
            "surface": surface,
            "row_kind": row_kind,
            "status": status,
            "rule_id": rule_id,
        }.items()
        if value
    }
    if blocking:
        filters["blocking"] = True
    return filters


def _evidence_rows_summary(rows: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    return {
        "row_kind_counts": _row_kind_counts(rows),
        "surface_status_counts": _surface_status_counts(rows),
        "surface_rule_id_counts": _surface_rule_id_counts(rows),
        "blocking_rule_id_counts": _blocking_rule_id_counts(rows),
        "total_evidence_rows": len(rows),
        "replay_claims": False,
        "canonical_effect_claims": False,
    }


def _row_kind(row: Mapping[str, Any]) -> str:
    return evidence_row_kind(row)


def _surface(row: Mapping[str, Any]) -> str:
    row_id = str(row.get("row_id") or row.get("finding_id") or "")
    if row_id.startswith("preflight:"):
        return "candidate-preflight"
    if row_id.startswith("nz-effect-candidate-"):
        return "effect-candidates"
    if row_id.startswith("nz-instruction-workqueue-"):
        return "instruction-workqueue"
    if row_id.startswith("nz-opw-"):
        return "operation-surface"
    if ":nz_effect_preflight_" in row_id:
        return "candidate-preflight"
    return "unknown"


def _rule_ids(row: Mapping[str, Any]) -> set[str]:
    return evidence_rule_ids(row)


def _row_kind_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts = Counter(_row_kind(row) for row in rows)
    return dict(sorted(counts.items()))


def _surface_status_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts = Counter(f"{_surface(row)}|{str(row.get('status') or _row_kind(row))}" for row in rows)
    return dict(sorted(counts.items()))


def _surface_rule_id_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        surface = _surface(row)
        for rule_id in sorted(evidence_rule_ids(row)):
            counts[f"{surface}|{rule_id}"] += 1
    return dict(sorted(counts.items()))


def _blocking_rule_id_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("blocking") is not True:
            continue
        for rule_id in sorted(evidence_rule_ids(row)):
            counts[rule_id] += 1
    return dict(sorted(counts.items()))

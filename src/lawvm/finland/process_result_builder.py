"""PhaseResult construction for ``process_muutoslaki``.

The process function still owns compilation/replay sequencing. This module owns
the boundary projection: local process signals become PhaseResult findings,
legacy out-parameter sinks are populated, and mutation-boundary reports are
projected as registered findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

from lawvm.core.compile_result import SourcePathology, TemporalEvent
from lawvm.core.mutation_accounting import MutationInvariantReport as ApplyMutationInvariantReport
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    build_apply_mutation_invariant_reports,
    check_apply_mutation_accounting,
    check_apply_mutation_invariant_reports,
)
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp
from lawvm.finland.replay_findings import (
    _apply_mutation_boundary_violation_finding,
    _apply_mutation_fallback_event_finding,
    _apply_mutation_invariant_report_finding,
)
from lawvm.finland.vts import VtsSkippedTarget


@dataclass(slots=True)
class ProcessSignalBuffers:
    """Mutable per-amendment signals accumulated before PhaseResult projection."""

    process_findings: list[Finding]
    amendment_temporal_events: list[TemporalEvent]
    failed_ops: list[FailedOp]
    source_pathologies: list[SourcePathology]
    elaboration_observations: list[dict[str, object]]
    sparse_slot_bindings: list[dict[str, object]]
    sparse_leftovers: list[dict[str, object]]
    commencement_expiry_override_notes: list[dict[str, object]]
    vts_skipped_targets: list[VtsSkippedTarget]

    @classmethod
    def empty(cls) -> "ProcessSignalBuffers":
        return cls(
            process_findings=[],
            amendment_temporal_events=[],
            failed_ops=[],
            source_pathologies=[],
            elaboration_observations=[],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            commencement_expiry_override_notes=[],
            vts_skipped_targets=[],
        )


@dataclass(slots=True)
class ProcessResultBuilder:
    amendment_id: str
    buffers: ProcessSignalBuffers
    migration_ledger: MigrationLedger
    migration_ledger_initial_len: int
    failed_ops_out: Optional[List[FailedOp]]
    source_pathologies_out: Optional[List[SourcePathology]]
    elaboration_observations_out: Optional[List[dict[str, object]]]
    sparse_slot_bindings_out: Optional[List[dict[str, object]]]
    sparse_leftovers_out: Optional[List[dict[str, object]]]
    commencement_expiry_overrides_out: Optional[List[dict[str, object]]]
    mutation_events_out: Optional[List[ApplyMutationEvent]]
    mutation_invariant_reports_out: Optional[List[ApplyMutationInvariantReport]]
    mutation_cursor: int = 0

    def build(self, output_state: Any) -> PhaseResult[Any]:
        """Build PhaseResult from local phase-owned signals and project compat sinks."""
        amendment_temporal_events = list(self.buffers.amendment_temporal_events)
        merged_findings: list[Finding] = list(self.buffers.process_findings)
        if self.buffers.source_pathologies:
            merged_findings.extend(
                Finding(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="process_muutoslaki",
                    detail=p.as_detail(),
                    source_statute=p.source_statute or self.amendment_id,
                    blocking=False,
                )
                for p in self.buffers.source_pathologies
            )
        if self.buffers.elaboration_observations:
            merged_findings.extend(
                Finding(
                    kind=str(o.get("kind", "")),
                    role=(
                        spec.role
                        if (spec := get_finding_spec(str(o.get("kind", "")).strip())) is not None
                        and spec.role != "barrier"
                        else "observation"
                    ),
                    stage="process_muutoslaki",
                    detail=dict(o),
                    source_statute=str(o.get("source_statute", self.amendment_id)),
                    blocking=(
                        spec.role != "observation"
                        and spec.default_enforcement in ("strict_fail", "hard_fail")
                        if (spec := get_finding_spec(str(o.get("kind", "")).strip())) is not None
                        else False
                    ),
                )
                for o in self.buffers.elaboration_observations
                if str(o.get("kind", "")).strip()
            )
        if self.buffers.vts_skipped_targets:
            merged_findings.extend(
                Finding(
                    kind=record.rule_id,
                    role="observation",
                    stage=record.phase,
                    detail=record.as_detail(),
                    source_statute=record.source_statute or self.amendment_id,
                    blocking=record.blocking,
                )
                for record in self.buffers.vts_skipped_targets
            )
        if self.buffers.failed_ops:
            merged_findings.extend(
                Finding(
                    kind="APPLY.FAILED_OPERATION",
                    role="obligation",
                    stage="process_muutoslaki",
                    detail={**f.as_detail(), "barrier_code": "APPLY.FAILED_OPERATION"},
                    blocking=True,
                    source_statute="",
                )
                for f in self.buffers.failed_ops
            )

        current_events = (self.mutation_events_out or [])[self.mutation_cursor:]
        self.mutation_cursor = len(self.mutation_events_out or [])
        mutation_invariant_reports = build_apply_mutation_invariant_reports(current_events)
        if self.mutation_invariant_reports_out is not None:
            self.mutation_invariant_reports_out.extend(mutation_invariant_reports)
        self._append_apply_mutation_findings(merged_findings, mutation_invariant_reports)
        self._append_apply_fallback_findings(merged_findings)
        boundary_violations = (
            check_apply_mutation_invariant_reports(mutation_invariant_reports)
            if mutation_invariant_reports
            else check_apply_mutation_accounting(self.mutation_events_out or [])
        )
        if boundary_violations and not mutation_invariant_reports:
            merged_findings.extend(
                _apply_mutation_boundary_violation_finding(
                    violation=violation,
                    source_statute=self.amendment_id,
                )
                for violation in boundary_violations
            )

        self.project_compat_sinks()
        return PhaseResult(
            output=output_state,
            findings=tuple(self._dedupe_findings(merged_findings)),
            temporal_events=tuple(amendment_temporal_events),
            migration_events=tuple(self.migration_ledger.events[self.migration_ledger_initial_len:]),
        )

    def project_compat_sinks(self) -> None:
        if self.failed_ops_out is not None:
            self.failed_ops_out.extend(self.buffers.failed_ops)
        if self.source_pathologies_out is not None:
            self.source_pathologies_out.extend(self.buffers.source_pathologies)
        if self.elaboration_observations_out is not None:
            self.elaboration_observations_out.extend(self.buffers.elaboration_observations)
        if self.sparse_slot_bindings_out is not None:
            self.sparse_slot_bindings_out.extend(self.buffers.sparse_slot_bindings)
        if self.sparse_leftovers_out is not None:
            self.sparse_leftovers_out.extend(self.buffers.sparse_leftovers)
        if self.commencement_expiry_overrides_out is not None:
            self.commencement_expiry_overrides_out.extend(
                self.buffers.commencement_expiry_override_notes
            )

    def _append_apply_mutation_findings(
        self,
        merged_findings: list[Finding],
        mutation_invariant_reports: Sequence[ApplyMutationInvariantReport],
    ) -> None:
        seen: set[tuple[str, str, str]] = set()
        for report in mutation_invariant_reports:
            for accounting_result in report.results:
                finding = _apply_mutation_invariant_report_finding(
                    report=report,
                    result=accounting_result,
                    source_statute=self.amendment_id,
                )
                if finding is None:
                    continue
                dedupe_key = (finding.kind, report.op_id, report.helper)
                if dedupe_key in seen:
                    continue
                merged_findings.append(finding)
                seen.add(dedupe_key)

    def _append_apply_fallback_findings(self, merged_findings: list[Finding]) -> None:
        seen: set[tuple[str, str, str, str]] = set()
        for event in self.mutation_events_out or []:
            for fallback_kind in (
                "APPLY.LEGACY_DISPATCH_FALLBACK",
                "APPLY.RELABEL_SKIPPED",
                "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
            ):
                finding = _apply_mutation_fallback_event_finding(
                    event=event,
                    fallback_kind=fallback_kind,
                )
                if finding is None:
                    continue
                reason_code = str(finding.detail.get("reason_code") or finding.detail.get("reason_tag") or "")
                dedupe_key = (finding.kind, event.source_statute, event.op_id, reason_code)
                if dedupe_key in seen:
                    continue
                merged_findings.append(finding)
                seen.add(dedupe_key)

    @staticmethod
    def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
        deduped: list[Finding] = []
        seen: set[tuple[str, str, str, str, bool]] = set()
        for finding in findings:
            key = (
                str(finding.kind or ""),
                str(finding.role or ""),
                str(finding.source_statute or ""),
                repr(finding.detail),
                bool(finding.blocking),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
        return deduped

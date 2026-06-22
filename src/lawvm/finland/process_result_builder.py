"""PhaseResult construction for ``process_muutoslaki``.

The process function still owns compilation/replay sequencing. This module owns
the boundary projection: local process signals become PhaseResult findings,
legacy out-parameter sinks are populated, and mutation-boundary reports are
projected as registered findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, Sequence

if TYPE_CHECKING:
    from lawvm.core.stage_result import StageResult

from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    append_unique_effect_lifecycle_events,
    append_unique_effect_relations,
)
from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import MutationInvariantReport as ApplyMutationInvariantReport
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.provenance import MigrationEvent
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.temporal import TemporalEvent
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_events import (
    ApplyMutationEvent,
    build_apply_mutation_invariant_reports,
)
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride, EffectRelationSignal
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp
from lawvm.finland.replay_findings import (
    _apply_mutation_fallback_event_finding,
    _apply_mutation_invariant_report_finding,
)
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.vts import VtsSkippedTarget

_APPLY_FALLBACK_FINDING_KINDS: tuple[str, ...] = (
    "APPLY.LEGACY_DISPATCH_FALLBACK",
    "APPLY.RELABEL_SKIPPED",
    "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
    "APPLY.SAME_WAVE_MIGRATION_REBASE",
    "APPLY.RESOLVER_BINDING_CONTRACT_ERROR",
)


@dataclass(slots=True)
class ProcessSignalBuffers:
    """Mutable per-amendment signals accumulated before PhaseResult projection."""

    process_findings: list[Finding]
    amendment_temporal_events: list[TemporalEvent]
    source_effects: list[EffectRef]
    effect_relations: list[EffectRelation]
    effect_lifecycle_events: list[EffectLifecycleEvent]
    failed_ops: list[FailedOp]
    source_pathologies: list[SourcePathology]
    elaboration_observations: list[dict[str, object]]
    sparse_slot_bindings: list[dict[str, object]]
    sparse_leftovers: list[dict[str, object]]
    commencement_expiry_override_notes: list[EffectLifecycleOverride]
    effect_relation_signals: list[EffectRelationSignal]
    vts_skipped_targets: list[VtsSkippedTarget]

    @classmethod
    def empty(cls) -> "ProcessSignalBuffers":
        return cls(
            process_findings=[],
            amendment_temporal_events=[],
            source_effects=[],
            effect_relations=[],
            effect_lifecycle_events=[],
            failed_ops=[],
            source_pathologies=[],
            elaboration_observations=[],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            commencement_expiry_override_notes=[],
            effect_relation_signals=[],
            vts_skipped_targets=[],
        )


@dataclass(frozen=True, slots=True)
class ProcessCompatSinks:
    """Legacy process out-parameter sinks retained for CLI/test compatibility."""

    failed_ops_out: Optional[List[FailedOp]]
    source_pathologies_out: Optional[List[SourcePathology]]
    elaboration_observations_out: Optional[List[dict[str, object]]]
    sparse_slot_bindings_out: Optional[List[dict[str, object]]]
    sparse_leftovers_out: Optional[List[dict[str, object]]]
    commencement_expiry_overrides_out: Optional[List[EffectLifecycleOverride]]
    mutation_events_out: Optional[List[ApplyMutationEvent]]
    mutation_invariant_reports_out: Optional[List[ApplyMutationInvariantReport]]


@dataclass(frozen=True, slots=True)
class ProcessAmendmentSinks:
    """Typed external sinks for one ``process_muutoslaki`` invocation.

    This is the call-boundary carrier. ``ProcessCompatSinks`` remains the
    smaller PhaseResult-projection subset used by ``ProcessResultBuilder``.
    """

    compiled_ops_out: Optional[List[dict[str, object]]] = None
    lo_ops_out: Optional[List[LegalOperation]] = None
    failed_ops_out: Optional[List[FailedOp]] = None
    source_pathologies_out: Optional[List[SourcePathology]] = None
    elaboration_observations_out: Optional[List[dict[str, object]]] = None
    sparse_slot_bindings_out: Optional[List[dict[str, object]]] = None
    sparse_leftovers_out: Optional[List[dict[str, object]]] = None
    regex_recognition_coverage_out: Optional[List[RegexRecognitionCoverage]] = None
    commencement_expiry_overrides_out: Optional[List[EffectLifecycleOverride]] = None
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None
    mutation_invariant_reports_out: Optional[List[ApplyMutationInvariantReport]] = None
    write_audits_out: Optional[List[ObservedWriteAudit]] = None
    write_receipts_out: Optional[List[WriteReceipt]] = None
    migration_events_out: Optional[List[MigrationEvent]] = None
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None
    # StageResult-endgame WAIST #6: the per-amendment canonical-op StageResult
    # account sink. ``compile_amendment_ops`` APPENDS the stage it already builds
    # (the same account that backs the typed-residual decline single-channel) so
    # the replay can aggregate the per-amendment accounts onto
    # ``ReplayProducts.canonical_op_stage`` faithfully — NOT re-derived from the
    # stage-tagless union findings. The sink only OBSERVES; the decline channel is
    # unchanged.
    canonical_op_stages_out: Optional[List["StageResult[Any]"]] = None


@dataclass(slots=True)
class ProcessResultBuilder:
    amendment_id: str
    buffers: ProcessSignalBuffers
    migration_ledger: MigrationLedger
    migration_ledger_initial_len: int
    sinks: ProcessCompatSinks
    mutation_cursor: int = 0
    target_statute: str = ""

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
            for observation in self.buffers.elaboration_observations:
                kind = str(observation.get("kind", "")).strip()
                if not kind:
                    continue
                spec = get_finding_spec(kind)
                role = (
                    spec.role
                    if spec is not None and spec.role != "barrier"
                    else "observation"
                )
                blocking = (
                    spec.role != "observation"
                    and spec.default_enforcement in ("strict_fail", "hard_fail")
                    if spec is not None
                    else False
                )
                merged_findings.append(
                    Finding(
                        kind=str(observation.get("kind", "")),
                        role=role,
                        stage="process_muutoslaki",
                        detail=dict(observation),
                        source_statute=str(observation.get("source_statute", self.amendment_id)),
                        blocking=blocking,
                    )
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

        current_events = (self.sinks.mutation_events_out or [])[self.mutation_cursor:]
        self.mutation_cursor = len(self.sinks.mutation_events_out or [])
        mutation_invariant_reports = build_apply_mutation_invariant_reports(current_events)
        if self.sinks.mutation_invariant_reports_out is not None:
            self.sinks.mutation_invariant_reports_out.extend(mutation_invariant_reports)
        self._append_apply_mutation_findings(merged_findings, mutation_invariant_reports)
        self._append_apply_fallback_findings(merged_findings)
        self._append_effect_lifecycle_projection()

        self.project_compat_sinks()
        return PhaseResult(
            output=output_state,
            findings=tuple(self._dedupe_findings(merged_findings)),
            temporal_events=tuple(amendment_temporal_events),
            migration_events=tuple(self.migration_ledger.events[self.migration_ledger_initial_len:]),
            source_effects=tuple(self.buffers.source_effects),
            effect_relations=tuple(self.buffers.effect_relations),
            effect_lifecycle_events=tuple(self.buffers.effect_lifecycle_events),
        )

    def _append_effect_lifecycle_projection(self) -> None:
        """Project process-level relation notes that do not require canonical ops.

        Effective-date commencement overrides are projected in
        ``process_pipeline`` after canonical operations for the amendment exist.
        Projecting those rows here would bind them to generic pre-canonical
        source effects and can turn repeal effects into executable commencement
        events. Expiry/repeal override rows remain safe here and preserve the
        replay-time side channel even before canonical operation projection.
        """
        non_commencement_overrides = tuple(
            row
            for row in self.buffers.commencement_expiry_override_notes
            if not row.effective
        )
        _source_effects, relations, lifecycle_events = build_finland_effect_lifecycle(
            target_statute=self.target_statute,
            canonical_ops=(),
            temporal_events=(),
            lifecycle_overrides=non_commencement_overrides,
            relation_signals=tuple(self.buffers.effect_relation_signals),
            known_source_effects=tuple(self.buffers.source_effects),
        )
        append_unique_effect_relations(
            self.buffers.effect_relations,
            relations,
            subject="process effect lifecycle projection",
        )
        append_unique_effect_lifecycle_events(
            self.buffers.effect_lifecycle_events,
            lifecycle_events,
            subject="process effect lifecycle projection",
        )

    def project_compat_sinks(self) -> None:
        if self.sinks.failed_ops_out is not None:
            self.sinks.failed_ops_out.extend(self.buffers.failed_ops)
        if self.sinks.source_pathologies_out is not None:
            self.sinks.source_pathologies_out.extend(self.buffers.source_pathologies)
        if self.sinks.elaboration_observations_out is not None:
            self.sinks.elaboration_observations_out.extend(self.buffers.elaboration_observations)
        if self.sinks.sparse_slot_bindings_out is not None:
            self.sinks.sparse_slot_bindings_out.extend(self.buffers.sparse_slot_bindings)
        if self.sinks.sparse_leftovers_out is not None:
            self.sinks.sparse_leftovers_out.extend(self.buffers.sparse_leftovers)
        if self.sinks.commencement_expiry_overrides_out is not None:
            self.sinks.commencement_expiry_overrides_out.extend(
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
        for event in self.sinks.mutation_events_out or []:
            if not event.used_fallback_tags:
                continue
            fallback_tags = frozenset(
                str(tag).strip()
                for tag in event.used_fallback_tags
                if str(tag).strip()
            )
            if not fallback_tags:
                continue
            for fallback_kind in _APPLY_FALLBACK_FINDING_KINDS:
                if fallback_kind not in fallback_tags:
                    continue
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

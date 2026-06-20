"""Runtime setup for one ``process_muutoslaki`` invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from lawvm.corpus_store import CorpusStore
from lawvm.core.effect_lifecycle import EffectLifecycleEvent, EffectRef, EffectRelation
from lawvm.core.compile_result import SourcePathology
from lawvm.core.phase_result import Finding
from lawvm.core.temporal import TemporalEvent
from lawvm.finland.corpus import _get_corpus_store
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride, EffectRelationSignal
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import FailedOp
from lawvm.finland.process_call import ResolvedProcessAmendmentCall
from lawvm.finland.process_findings import ProcessFindingRecorder
from lawvm.finland.process_result_builder import (
    ProcessCompatSinks,
    ProcessResultBuilder,
    ProcessSignalBuffers,
)
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.vts import VtsSkippedTarget


@dataclass(slots=True)
class ProcessRuntimeContext:
    """Mutable per-amendment runtime objects shared across process phases."""

    signals: ProcessSignalBuffers
    amendment_temporal_events: list[TemporalEvent]
    source_effects: list[EffectRef]
    effect_relations: list[EffectRelation]
    effect_lifecycle_events: list[EffectLifecycleEvent]
    process_findings: list[Finding]
    compat_failed_ops: list[FailedOp]
    compat_source_pathologies: list[SourcePathology]
    compat_elaboration_observations: list[dict[str, object]]
    compat_sparse_slot_bindings: list[dict[str, object]]
    compat_sparse_leftovers: list[dict[str, object]]
    commencement_expiry_override_notes: list[EffectLifecycleOverride]
    effect_relation_signals: list[EffectRelationSignal]
    vts_skipped_targets: list[VtsSkippedTarget]
    finding_recorder: ProcessFindingRecorder
    record_process_finding: Callable[..., Finding]
    corpus: CorpusStore
    migration_ledger: MigrationLedger
    migration_ledger_initial_len: int
    result_builder: ProcessResultBuilder
    effective_restructure_plans_out: list[StructuralTransformPlan]
    processed_amendment_titles: dict[str, str]


def build_process_runtime(process_call: ResolvedProcessAmendmentCall) -> ProcessRuntimeContext:
    """Build process-local buffers and compatibility projections.

    This setup phase is intentionally replay-neutral: it allocates shared mutable
    buffers, resolves the corpus default, and wires the PhaseResult builder.
    """

    signals = ProcessSignalBuffers.empty()
    finding_recorder = ProcessFindingRecorder(signals.process_findings)
    corpus = process_call.corpus if process_call.corpus is not None else _get_corpus_store()
    migration_ledger = MigrationLedger(process_call.prior_migration_events or ())
    migration_ledger_initial_len = len(migration_ledger)
    result_builder = ProcessResultBuilder(
        amendment_id=process_call.amendment_id,
        buffers=signals,
        migration_ledger=migration_ledger,
        migration_ledger_initial_len=migration_ledger_initial_len,
        sinks=ProcessCompatSinks(
            failed_ops_out=process_call.failed_ops_out,
            source_pathologies_out=process_call.source_pathologies_out,
            elaboration_observations_out=process_call.elaboration_observations_out,
            sparse_slot_bindings_out=process_call.sparse_slot_bindings_out,
            sparse_leftovers_out=process_call.sparse_leftovers_out,
            commencement_expiry_overrides_out=process_call.commencement_expiry_overrides_out,
            mutation_events_out=process_call.mutation_events_out,
            mutation_invariant_reports_out=process_call.mutation_invariant_reports_out,
        ),
        mutation_cursor=len(process_call.mutation_events_out or ()),
        target_statute=process_call.parent_id,
    )
    return ProcessRuntimeContext(
        signals=signals,
        amendment_temporal_events=signals.amendment_temporal_events,
        source_effects=signals.source_effects,
        effect_relations=signals.effect_relations,
        effect_lifecycle_events=signals.effect_lifecycle_events,
        process_findings=signals.process_findings,
        compat_failed_ops=signals.failed_ops,
        compat_source_pathologies=signals.source_pathologies,
        compat_elaboration_observations=signals.elaboration_observations,
        compat_sparse_slot_bindings=signals.sparse_slot_bindings,
        compat_sparse_leftovers=signals.sparse_leftovers,
        commencement_expiry_override_notes=signals.commencement_expiry_override_notes,
        effect_relation_signals=signals.effect_relation_signals,
        vts_skipped_targets=signals.vts_skipped_targets,
        finding_recorder=finding_recorder,
        record_process_finding=finding_recorder.record,
        corpus=corpus,
        migration_ledger=migration_ledger,
        migration_ledger_initial_len=migration_ledger_initial_len,
        result_builder=result_builder,
        effective_restructure_plans_out=(
            process_call.restructure_plans_out
            if process_call.restructure_plans_out is not None
            else []
        ),
        processed_amendment_titles=process_call.processed_amendment_titles or {},
    )

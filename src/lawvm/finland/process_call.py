"""Typed resolution for one Finland amendment-processing call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, Literal, Optional, Set

if TYPE_CHECKING:
    from lawvm.core.stage_result import StageResult

from lawvm.corpus_store import CorpusStore
from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import MutationInvariantReport as ApplyMutationInvariantReport
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.provenance import MigrationEvent
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.chapter_seed_targets import ChapterSeedSkipInput
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.ops import FailedOp
from lawvm.finland.process_request import ProcessAmendmentRequest
from lawvm.finland.process_result_builder import ProcessAmendmentSinks
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.statute import ReplayState, StatuteContext


@dataclass(frozen=True, slots=True)
class ResolvedProcessAmendmentCall:
    """Fully resolved ``process_muutoslaki`` call boundary."""

    amendment_id: str
    state: ReplayState
    ctx: StatuteContext
    replay_mode: Literal["official_consolidation", "legal_pit"]
    compiled_ops_out: Optional[list[dict[str, object]]]
    lo_ops_out: Optional[list[LegalOperation]]
    parent_id: str
    failed_ops_out: Optional[list[FailedOp]]
    strict_profile: Optional[StrictProfile]
    chapter_seed_skip: Optional[Set[ChapterSeedSkipInput]]
    corpus: Optional[CorpusStore]
    future_repeals: Optional[Set[RepealTargetRef]]
    source_pathologies_out: Optional[list[SourcePathology]]
    elaboration_observations_out: Optional[list[Dict[str, object]]]
    sparse_slot_bindings_out: Optional[list[Dict[str, object]]]
    sparse_leftovers_out: Optional[list[Dict[str, object]]]
    regex_recognition_coverage_out: Optional[list[RegexRecognitionCoverage]]
    commencement_expiry_overrides_out: Optional[list[EffectLifecycleOverride]]
    mutation_events_out: Optional[list[ApplyMutationEvent]]
    mutation_invariant_reports_out: Optional[list[ApplyMutationInvariantReport]]
    write_audits_out: Optional[list[ObservedWriteAudit]]
    write_receipts_out: Optional[list[WriteReceipt]]
    migration_events_out: Optional[list[MigrationEvent]]
    prior_migration_events: Optional[Iterable[MigrationEvent]]
    restructure_plans_out: Optional[list[StructuralTransformPlan]]
    processed_amendment_titles: Optional[Dict[str, str]]
    amendment_edge_kind: str
    # WAIST #6 carrier: the per-amendment canonical-op StageResult sink (see
    # ``ProcessAmendmentSinks.canonical_op_stages_out``).
    canonical_op_stages_out: Optional[list["StageResult[Any]"]]


def resolve_process_amendment_call(
    request: ProcessAmendmentRequest,
    sinks: Optional[ProcessAmendmentSinks] = None,
) -> ResolvedProcessAmendmentCall:
    """Project the public typed boundary into the internal runtime carrier."""
    sinks = sinks or ProcessAmendmentSinks()

    return ResolvedProcessAmendmentCall(
        amendment_id=request.amendment_id,
        state=request.state,
        ctx=request.ctx,
        replay_mode=request.replay_mode,
        compiled_ops_out=sinks.compiled_ops_out,
        lo_ops_out=sinks.lo_ops_out,
        parent_id=request.parent_id,
        failed_ops_out=sinks.failed_ops_out,
        strict_profile=request.strict_profile,
        chapter_seed_skip=request.chapter_seed_skip,
        corpus=request.corpus,
        future_repeals=request.future_repeals,
        source_pathologies_out=sinks.source_pathologies_out,
        elaboration_observations_out=sinks.elaboration_observations_out,
        sparse_slot_bindings_out=sinks.sparse_slot_bindings_out,
        sparse_leftovers_out=sinks.sparse_leftovers_out,
        regex_recognition_coverage_out=sinks.regex_recognition_coverage_out,
        commencement_expiry_overrides_out=sinks.commencement_expiry_overrides_out,
        mutation_events_out=sinks.mutation_events_out,
        mutation_invariant_reports_out=sinks.mutation_invariant_reports_out,
        write_audits_out=sinks.write_audits_out,
        write_receipts_out=sinks.write_receipts_out,
        migration_events_out=sinks.migration_events_out,
        prior_migration_events=request.prior_migration_events,
        restructure_plans_out=sinks.restructure_plans_out,
        processed_amendment_titles=request.processed_amendment_titles,
        amendment_edge_kind=request.amendment_edge_kind,
        canonical_op_stages_out=sinks.canonical_op_stages_out,
    )

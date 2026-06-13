"""Compatibility resolution for one Finland amendment-processing call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Optional, Set

from lawvm.corpus_store import CorpusStore
from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import MutationInvariantReport as ApplyMutationInvariantReport
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.provenance import MigrationEvent
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.chapter_seed_targets import ChapterSeedSkipInput
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
    commencement_expiry_overrides_out: Optional[list[Dict[str, object]]]
    mutation_events_out: Optional[list[ApplyMutationEvent]]
    mutation_invariant_reports_out: Optional[list[ApplyMutationInvariantReport]]
    write_audits_out: Optional[list[ObservedWriteAudit]]
    migration_events_out: Optional[list[MigrationEvent]]
    prior_migration_events: Optional[Iterable[MigrationEvent]]
    restructure_plans_out: Optional[list[StructuralTransformPlan]]
    processed_amendment_titles: Optional[Dict[str, str]]


def resolve_process_amendment_call(
    *,
    amendment_id: Optional[str],
    state: Optional[ReplayState],
    ctx: Optional[StatuteContext],
    replay_mode: Literal["official_consolidation", "legal_pit"],
    compiled_ops_out: Optional[list[dict[str, object]]],
    lo_ops_out: Optional[list[LegalOperation]],
    parent_id: str,
    failed_ops_out: Optional[list[FailedOp]],
    strict_profile: Optional[StrictProfile],
    chapter_seed_skip: Optional[Set[ChapterSeedSkipInput]],
    corpus: Optional[CorpusStore],
    future_repeals: Optional[Set[RepealTargetRef]],
    source_pathologies_out: Optional[list[SourcePathology]],
    elaboration_observations_out: Optional[list[Dict[str, object]]],
    sparse_slot_bindings_out: Optional[list[Dict[str, object]]],
    sparse_leftovers_out: Optional[list[Dict[str, object]]],
    regex_recognition_coverage_out: Optional[list[RegexRecognitionCoverage]],
    commencement_expiry_overrides_out: Optional[list[Dict[str, object]]],
    mutation_events_out: Optional[list[ApplyMutationEvent]],
    mutation_invariant_reports_out: Optional[list[ApplyMutationInvariantReport]],
    write_audits_out: Optional[list[ObservedWriteAudit]],
    migration_events_out: Optional[list[MigrationEvent]],
    prior_migration_events: Optional[Iterable[MigrationEvent]],
    restructure_plans_out: Optional[list[StructuralTransformPlan]],
    processed_amendment_titles: Optional[Dict[str, str]],
    request: Optional[ProcessAmendmentRequest],
    sinks: Optional[ProcessAmendmentSinks],
) -> ResolvedProcessAmendmentCall:
    """Merge typed and legacy ``process_muutoslaki`` inputs."""

    if request is not None:
        amendment_id = request.amendment_id
        state = request.state
        ctx = request.ctx
        replay_mode = request.replay_mode
        parent_id = request.parent_id
        strict_profile = request.strict_profile
        chapter_seed_skip = request.chapter_seed_skip
        corpus = request.corpus
        future_repeals = request.future_repeals
        prior_migration_events = request.prior_migration_events
        processed_amendment_titles = request.processed_amendment_titles
    if amendment_id is None or state is None or ctx is None:
        raise TypeError("process_muutoslaki requires either amendment_id/state/ctx or request=")

    if sinks is not None:
        compiled_ops_out = compiled_ops_out if compiled_ops_out is not None else sinks.compiled_ops_out
        lo_ops_out = lo_ops_out if lo_ops_out is not None else sinks.lo_ops_out
        failed_ops_out = failed_ops_out if failed_ops_out is not None else sinks.failed_ops_out
        source_pathologies_out = (
            source_pathologies_out
            if source_pathologies_out is not None
            else sinks.source_pathologies_out
        )
        elaboration_observations_out = (
            elaboration_observations_out
            if elaboration_observations_out is not None
            else sinks.elaboration_observations_out
        )
        sparse_slot_bindings_out = (
            sparse_slot_bindings_out
            if sparse_slot_bindings_out is not None
            else sinks.sparse_slot_bindings_out
        )
        sparse_leftovers_out = (
            sparse_leftovers_out if sparse_leftovers_out is not None else sinks.sparse_leftovers_out
        )
        regex_recognition_coverage_out = (
            regex_recognition_coverage_out
            if regex_recognition_coverage_out is not None
            else sinks.regex_recognition_coverage_out
        )
        commencement_expiry_overrides_out = (
            commencement_expiry_overrides_out
            if commencement_expiry_overrides_out is not None
            else sinks.commencement_expiry_overrides_out
        )
        mutation_events_out = (
            mutation_events_out if mutation_events_out is not None else sinks.mutation_events_out
        )
        mutation_invariant_reports_out = (
            mutation_invariant_reports_out
            if mutation_invariant_reports_out is not None
            else sinks.mutation_invariant_reports_out
        )
        write_audits_out = write_audits_out if write_audits_out is not None else sinks.write_audits_out
        migration_events_out = (
            migration_events_out if migration_events_out is not None else sinks.migration_events_out
        )
        restructure_plans_out = (
            restructure_plans_out if restructure_plans_out is not None else sinks.restructure_plans_out
        )

    return ResolvedProcessAmendmentCall(
        amendment_id=amendment_id,
        state=state,
        ctx=ctx,
        replay_mode=replay_mode,
        compiled_ops_out=compiled_ops_out,
        lo_ops_out=lo_ops_out,
        parent_id=parent_id,
        failed_ops_out=failed_ops_out,
        strict_profile=strict_profile,
        chapter_seed_skip=chapter_seed_skip,
        corpus=corpus,
        future_repeals=future_repeals,
        source_pathologies_out=source_pathologies_out,
        elaboration_observations_out=elaboration_observations_out,
        sparse_slot_bindings_out=sparse_slot_bindings_out,
        sparse_leftovers_out=sparse_leftovers_out,
        regex_recognition_coverage_out=regex_recognition_coverage_out,
        commencement_expiry_overrides_out=commencement_expiry_overrides_out,
        mutation_events_out=mutation_events_out,
        mutation_invariant_reports_out=mutation_invariant_reports_out,
        write_audits_out=write_audits_out,
        migration_events_out=migration_events_out,
        prior_migration_events=prior_migration_events,
        restructure_plans_out=restructure_plans_out,
        processed_amendment_titles=processed_amendment_titles,
    )

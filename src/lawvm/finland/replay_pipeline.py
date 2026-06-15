"""Explicit replay-plan stages for the Finnish frontend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional

from lawvm.corpus_store import CorpusStore
from lawvm.core.compile_result import SourcePathology
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.provenance import MigrationEvent
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE, OBSERVATION_ROLE, PhaseResult
from lawvm.core.replay_contracts import ReplayCheckpoint, ReplayCheckpointCallback
from lawvm.core.tree_ops import resort_children as _resort_children
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.chapter_seed import ChapterSeedDiagnostic
from lawvm.finland.grafter_uncovered import (
    PreScanRepealDiagnostic,
    PreScanRepealTargetsRequest,
    PreScanRepealTargetsSinks,
)
from lawvm.finland.process_request import ProcessAmendmentRequest
from lawvm.finland.process_result_builder import ProcessAmendmentSinks
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.vts import VtsSkippedTarget, VtsSourceDiagnostic

from lawvm.finland.statute import ReplayState, StatuteContext, _serialize_text_node as _serialize_text
from lawvm.finland.statute_id import engine_statute_id, looks_like_statute_id


@dataclass(frozen=True)
class ReplayPlan:
    """Typed plan for replaying one Finnish parent statute."""

    parent_id: str
    replay_mode: Literal["official_consolidation", "legal_pit"]
    replay_profile: Any
    ctx: StatuteContext
    initial_state: ReplayState
    amendment_records: list[dict[str, Any]]
    amendment_ids: list[str]
    cutoff_date: Any
    oracle_version_amendment_id: str
    oracle_suspect: str


@dataclass(slots=True)
class ReplaySignalBuffers:
    """Mutable replay-run signals accumulated before final evidence projection.

    These are not semantic inputs. They are the named evidence/artifact streams
    produced while folding amendment acts over the statute state. Keeping them
    behind a typed carrier prevents replay orchestration from growing another
    anonymous list farm and makes new instrumentation channels visible at the
    replay boundary.
    """

    findings: list[Finding]
    source_pathologies: list[SourcePathology]
    elaboration_observations: list[dict[str, object]]
    sparse_slot_bindings: list[dict[str, object]]
    sparse_leftovers: list[dict[str, object]]
    regex_recognition_coverages: list[RegexRecognitionCoverage]
    commencement_expiry_overrides: list[dict[str, object]]
    mutation_events: list[ApplyMutationEvent]
    write_audits: list[ObservedWriteAudit]
    migration_events: list[MigrationEvent]
    temporal_events: list[Any]
    restructure_plans: list[StructuralTransformPlan]

    @classmethod
    def empty(cls) -> "ReplaySignalBuffers":
        return cls(
            findings=[],
            source_pathologies=[],
            elaboration_observations=[],
            sparse_slot_bindings=[],
            sparse_leftovers=[],
            regex_recognition_coverages=[],
            commencement_expiry_overrides=[],
            mutation_events=[],
            write_audits=[],
            migration_events=[],
            temporal_events=[],
            restructure_plans=[],
        )

    @classmethod
    def from_legacy_sinks(
        cls,
        *,
        findings_out: Optional[List[Finding]] = None,
        source_pathologies_out: Optional[List[Any]] = None,
        elaboration_observations_out: Optional[List[Any]] = None,
        sparse_slot_bindings_out: Optional[List[Any]] = None,
        sparse_leftovers_out: Optional[List[Any]] = None,
        regex_recognition_coverage_out: Optional[List[Any]] = None,
        commencement_expiry_overrides_out: Optional[List[Any]] = None,
        mutation_events_out: Optional[List[Any]] = None,
        write_audits_out: Optional[List[Any]] = None,
        migration_events_out: Optional[List[MigrationEvent]] = None,
        temporal_events_out: Optional[List[Any]] = None,
        restructure_plans_out: Optional[List[Any]] = None,
    ) -> "ReplaySignalBuffers":
        """Build buffers backed by legacy out-parameter lists where provided."""

        return cls(
            findings=findings_out if findings_out is not None else [],
            source_pathologies=source_pathologies_out if source_pathologies_out is not None else [],
            elaboration_observations=(
                elaboration_observations_out
                if elaboration_observations_out is not None
                else []
            ),
            sparse_slot_bindings=(
                sparse_slot_bindings_out if sparse_slot_bindings_out is not None else []
            ),
            sparse_leftovers=sparse_leftovers_out if sparse_leftovers_out is not None else [],
            regex_recognition_coverages=(
                regex_recognition_coverage_out
                if regex_recognition_coverage_out is not None
                else []
            ),
            commencement_expiry_overrides=(
                commencement_expiry_overrides_out
                if commencement_expiry_overrides_out is not None
                else []
            ),
            mutation_events=mutation_events_out if mutation_events_out is not None else [],
            write_audits=write_audits_out if write_audits_out is not None else [],
            migration_events=(
                migration_events_out if migration_events_out is not None else []
            ),
            temporal_events=temporal_events_out if temporal_events_out is not None else [],
            restructure_plans=(
                restructure_plans_out if restructure_plans_out is not None else []
            ),
        )

    def process_sinks(
        self,
        *,
        compiled_ops_out: Optional[List[dict[str, object]]],
        lo_ops_out: Optional[List[Any]],
        failed_ops_out: Optional[List[Any]],
        migration_events_out: Optional[List[MigrationEvent]],
    ) -> ProcessAmendmentSinks:
        return ProcessAmendmentSinks(
            compiled_ops_out=compiled_ops_out,
            lo_ops_out=lo_ops_out,
            failed_ops_out=failed_ops_out,
            source_pathologies_out=self.source_pathologies,
            elaboration_observations_out=self.elaboration_observations,
            sparse_slot_bindings_out=self.sparse_slot_bindings,
            sparse_leftovers_out=self.sparse_leftovers,
            regex_recognition_coverage_out=self.regex_recognition_coverages,
            commencement_expiry_overrides_out=self.commencement_expiry_overrides,
            mutation_events_out=self.mutation_events,
            write_audits_out=self.write_audits,
            migration_events_out=migration_events_out,
            restructure_plans_out=self.restructure_plans,
        )


def _normalize_stop_before(stop_before: str) -> str:
    if not stop_before:
        return ""
    token = stop_before.replace("-", "/")
    if "/" not in token:
        return token
    parts = token.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts[0]) == 4 else f"{parts[1]}/{parts[0]}"


def _dedupe_consecutive_amendment_records(
    amendment_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse exact consecutive duplicate amendment records.

    Resolver output can occasionally contain the same amendment statute twice in
    a row with only the synthetic ``sequence`` field differing. Replaying both
    copies is structurally dishonest: the second pass can manufacture fake
    failed ops and mutation-boundary violations even though the real source law
    should only execute once.

    We keep this dedupe narrow on purpose:
    - only consecutive duplicates are collapsed
    - only records equal on all substantive fields (everything except
      ``sequence``) are considered duplicates
    """

    def _dedupe_key(record: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
        return tuple(sorted((key, value) for key, value in record.items() if key != "sequence"))

    deduped: list[dict[str, Any]] = []
    previous_key: tuple[tuple[str, Any], ...] | None = None
    for record in amendment_records:
        current_key = _dedupe_key(record)
        if previous_key == current_key:
            continue
        deduped.append(record)
        previous_key = current_key
    return deduped


def prepare_replay_plan(
    parent_id: str,
    *,
    mode: Literal["official_consolidation", "legal_pit"],
    strict_profile: Any,
    corpus: CorpusStore,
    stop_before: str,
    label_postprocessor: Callable[[str, str], str],
    get_replay_profile: Callable[..., Any],
    resolve_applicable_amendment_records: Callable[..., tuple[list[dict[str, Any]], Any, Any]],
    get_consolidated_oracle_suspect: Callable[..., Optional[str]],
    extract_inline_corrections: Callable[[bytes, str], tuple[list[Any], bytes]],
) -> ReplayPlan:
    """Build the typed replay plan and initial state for one statute.

    The incoming ``parent_id`` is normalized to the engine ``year/num`` form at
    this single boundary so that the corpus (keyed ``finlex://sd/{year}/{num}``)
    and the amendment index (keyed ``year/num``) agree. Without this, a
    canonical ``num/year`` säädös id (e.g. ``"301/2004"``) would read no base —
    or, on any path that supplied base IR independently, resolve to an *empty*
    amendment set and silently degrade to a base-only materialization.
    """
    requested_id = parent_id
    normalized_id = engine_statute_id(parent_id)
    orig_bytes = corpus.read_source(normalized_id)
    if orig_bytes is None and normalized_id != requested_id:
        # The normalizer reordered the id but it still does not resolve — fall
        # back to the literal id only to produce a precise diagnostic below.
        orig_bytes = corpus.read_source(requested_id)
        if orig_bytes is not None:
            normalized_id = requested_id
    if orig_bytes is None:
        hint = ""
        if looks_like_statute_id(requested_id):
            hint = (
                f" Normalized engine id {normalized_id!r} (year/num) was tried; "
                "neither ordering resolves a base statute."
            )
        raise RuntimeError(
            "FI_STATUTE_ID_UNRESOLVED: säädös id "
            f"{requested_id!r} did not resolve to a base statute in the corpus."
            f"{hint} An id that does not resolve must NOT silently degrade to a "
            "base-only materialization; check the id ordering (canonical "
            "'num/year' vs engine 'year/num'), for typos, and that the corpus "
            "(LAWVM_FARCHIVE_DB) actually contains this statute."
        )
    parent_id = normalized_id

    corr_gate = strict_profile is None or strict_profile.allows_source_correction_rules
    if corr_gate:
        _, orig_bytes = extract_inline_corrections(orig_bytes, parent_id)
        # Apply Population-B body patches (prose/footnote corrigenda keyed by the
        # statute's own ID) to the base-statute XML.  These are the same patches that
        # patch_source_body_xml applies to amendment bodies, but the base statute is
        # never processed by process_muutoslaki, so we must apply them here.
        from lawvm.finland.corrigendum import get_patch_table as _get_corr_patch_table
        orig_bytes, _ = _get_corr_patch_table().patch_source_body_xml(orig_bytes, parent_id)

    ctx = StatuteContext.from_xml(orig_bytes, label_postprocessor)
    initial_state = ReplayState(ir=ctx.base_ir)
    replay_profile = get_replay_profile(mode)
    amendment_records, cutoff_date, oracle_version_amendment_id = resolve_applicable_amendment_records(
        parent_id,
        mode,
        corpus=corpus,
    )
    amendment_records = _dedupe_consecutive_amendment_records(amendment_records)
    amendment_ids = [str(rec["statute_id"]) for rec in amendment_records]

    stop_before_norm = _normalize_stop_before(stop_before)
    if stop_before_norm:
        try:
            cut = amendment_ids.index(stop_before_norm)
            amendment_ids = amendment_ids[:cut]
        except ValueError:
            pass

    oracle_suspect = get_consolidated_oracle_suspect(parent_id)
    return ReplayPlan(
        parent_id=parent_id,
        replay_mode=mode,
        replay_profile=replay_profile,
        ctx=ctx,
        initial_state=initial_state,
        amendment_records=amendment_records,
        amendment_ids=amendment_ids,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id or "",
        oracle_suspect=oracle_suspect or "",
    )


def populate_replay_meta(plan: ReplayPlan, replay_meta_out: Optional[Dict[str, object]]) -> None:
    """Emit backward-compatible replay metadata from a typed replay plan."""
    if replay_meta_out is None:
        return
    replay_meta_out.clear()
    replay_meta_out.update(
        {
            "cutoff_date": plan.cutoff_date.isoformat() if plan.cutoff_date else "",
            "oracle_version_amendment_id": plan.oracle_version_amendment_id or "",
            "lineage": plan.amendment_records,
            "oracle_suspect": plan.oracle_suspect or "",
        }
    )


def build_tree_invariant_finding(
    *,
    violation: str,
    source_statute: str,
    phase: str,
    message: str,
) -> Finding:
    """Build the replay-time tree invariant finding carried by Finland execution."""
    return Finding(
        kind="APPLY.TREE_INVARIANT_VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": message,
            "phase": phase,
            "violation": violation,
            "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
        },
    )


def build_chapter_seed_finding(diagnostic: ChapterSeedDiagnostic) -> Finding:
    """Project chapter-seed diagnostics onto the governed finding ledger."""
    detail = diagnostic.as_detail()
    if diagnostic.rule_id == "fi_chapter_seed_abridged_base_chapter_unreconstructable":
        # An expected source-completeness limitation, not an acquisition fault:
        # the abridged base omits a whole chapter span and no amendment body can
        # restate it, so the oracle's provisions there diverge by construction.
        # Record it as a non-blocking observation so the divergence is attributed
        # to the source witness rather than masquerading as a replay fault.
        return Finding(
            kind="SOURCE.ABRIDGED_BASE_CHAPTER_UNRECONSTRUCTABLE",
            role=OBSERVATION_ROLE,
            stage="execute_replay_plan",
            blocking=False,
            source_statute=diagnostic.source_statute,
            detail=detail,
        )
    if diagnostic.family == "source_pathology":
        return Finding(
            kind="ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY",
            role=OBLIGATION_ROLE,
            stage="execute_replay_plan",
            blocking=True,
            source_statute=diagnostic.source_statute,
            detail=detail,
        )
    return Finding(
        kind="ELAB.CHAPTER_SEED_REPAIR",
        role=OBSERVATION_ROLE,
        stage="execute_replay_plan",
        blocking=False,
        source_statute=diagnostic.source_statute,
        detail=detail,
    )


def append_chapter_seed_compiled_ops(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    diagnostics: list[ChapterSeedDiagnostic],
) -> None:
    """Project chapter-seed repairs onto the compiled-op witness surface."""
    if compiled_ops_out is None:
        return
    for diagnostic in diagnostics:
        if diagnostic.family == "source_pathology":
            continue
        chapter_label = str(diagnostic.chapter_label or "").strip()
        if not chapter_label:
            continue
        sequence = len(compiled_ops_out) + 1
        compiled_ops_out.append(
            {
                "sequence": sequence,
                "op_id": (
                    "chapter_seed:"
                    f"{diagnostic.source_statute or ''}:"
                    f"{chapter_label}"
                ),
                "action": "seed",
                "source_statute": diagnostic.source_statute,
                "source_title": None,
                "extraction_provenance_tags": [],
                "target_guessing_provenance_tags": [],
                "scope_provenance_tags": ["chapter_seed"],
                "witness_rule_id": diagnostic.rule_id,
                "target_unit_kind": "chapter",
                "target_norm": chapter_label,
                "target_chapter": "",
                "target_part": "",
                "target_paragraph": "",
                "target_item": "",
                "target_special": "",
                "scope_source": "chapter_seed_diagnostic",
                "scope_confidence": "fallback",
            }
        )


def build_prescan_finding(
    record: VtsSkippedTarget | VtsSourceDiagnostic | PreScanRepealDiagnostic,
) -> Finding:
    """Project future-repeal pre-scan visibility records onto the finding ledger."""
    return Finding(
        kind=record.rule_id,
        role=OBSERVATION_ROLE,
        stage=record.phase,
        blocking=record.blocking,
        source_statute=record.source_statute,
        detail={**record.as_detail(), "prescan_phase": "future_repeal_scan"},
    )


def execute_replay_plan(
    plan: ReplayPlan,
    *,
    corpus: CorpusStore,
    process_muutoslaki: Callable[
        [ProcessAmendmentRequest, ProcessAmendmentSinks],
        PhaseResult[ReplayState],
    ],
    seed_missing_chapters: Callable[..., tuple[Any, Any]],
    pre_scan_repeal_targets: Callable[..., Any],
    future_repeals_for_index: Callable[..., Any],
    post_process_tree: Callable[[Any, bool], Any],
    check_tree_invariants: Callable[[Any], list[str]],
    compiled_ops_out: Optional[List[Dict[str, object]]] = None,
    lo_ops_out: Optional[List[Any]] = None,
    failed_ops_out: Optional[List[Any]] = None,
    findings_out: Optional[List[Finding]] = None,
    source_pathologies_out: Optional[List[Any]] = None,
    elaboration_observations_out: Optional[List[Any]] = None,
    sparse_slot_bindings_out: Optional[List[Any]] = None,
    sparse_leftovers_out: Optional[List[Any]] = None,
    regex_recognition_coverage_out: Optional[List[Any]] = None,
    commencement_expiry_overrides_out: Optional[List[Any]] = None,
    mutation_events_out: Optional[List[Any]] = None,
    write_audits_out: Optional[List[Any]] = None,
    migration_events_out: Optional[List[MigrationEvent]] = None,
    temporal_events_out: Optional[List[Any]] = None,
    strict_profile: Any = None,
    logger: Any = None,
    checkpoint_callback: Optional[ReplayCheckpointCallback] = None,
    restructure_plans_out: Optional[List[Any]] = None,
    signal_buffers: Optional[ReplaySignalBuffers] = None,
) -> ReplayState:
    """Execute the replay fold for a prepared plan."""
    state = plan.initial_state
    signals = signal_buffers or ReplaySignalBuffers.from_legacy_sinks(
        findings_out=findings_out,
        source_pathologies_out=source_pathologies_out,
        elaboration_observations_out=elaboration_observations_out,
        sparse_slot_bindings_out=sparse_slot_bindings_out,
        sparse_leftovers_out=sparse_leftovers_out,
        regex_recognition_coverage_out=regex_recognition_coverage_out,
        commencement_expiry_overrides_out=commencement_expiry_overrides_out,
        mutation_events_out=mutation_events_out,
        write_audits_out=write_audits_out,
        migration_events_out=migration_events_out,
        temporal_events_out=temporal_events_out,
        restructure_plans_out=restructure_plans_out,
    )

    chapter_seed_diagnostics: list[ChapterSeedDiagnostic] = []
    seeded_ir, chapter_seed_skip = seed_missing_chapters(
        state.ir,
        plan.amendment_ids,
        corpus,
        diagnostics_out=chapter_seed_diagnostics,
    )
    if seeded_ir is not state.ir:
        state = state.with_ir(seeded_ir)
    signals.findings.extend(
        build_chapter_seed_finding(diagnostic)
        for diagnostic in chapter_seed_diagnostics
    )
    append_chapter_seed_compiled_ops(compiled_ops_out, chapter_seed_diagnostics)

    vts_skipped_targets: list[VtsSkippedTarget] = []
    vts_source_diagnostics: list[VtsSourceDiagnostic] = []
    prescan_diagnostics: list[PreScanRepealDiagnostic] = []
    repeal_schedule = pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=plan.amendment_ids,
            corpus_store=corpus,
            parent_id=plan.parent_id,
            parent_title=plan.ctx.title,
            cutoff_date=plan.cutoff_date,
        ),
        PreScanRepealTargetsSinks(
            vts_skipped_targets_out=vts_skipped_targets,
            vts_source_diagnostics_out=vts_source_diagnostics,
            prescan_diagnostics_out=prescan_diagnostics,
        ),
    )
    signals.findings.extend(
        build_prescan_finding(record)
        for record in (*vts_skipped_targets, *vts_source_diagnostics, *prescan_diagnostics)
    )
    repeal_suffix = future_repeals_for_index(repeal_schedule)
    processed_amendment_titles: dict[str, str] = {}
    effective_migration_events_out = signals.migration_events
    record_titles = {
        str(record.get("statute_id") or ""): str(record.get("title") or "")
        for record in plan.amendment_records
    }
    for idx, mid in enumerate(plan.amendment_ids):
        future_repeals = repeal_suffix[idx] if idx < len(repeal_suffix) else set()
        _pm_result = process_muutoslaki(
            ProcessAmendmentRequest(
                amendment_id=mid,
                state=state,
                ctx=plan.ctx,
                replay_mode=plan.replay_mode,
                parent_id=plan.parent_id,
                strict_profile=strict_profile,
                chapter_seed_skip=chapter_seed_skip,
                corpus=corpus,
                future_repeals=future_repeals if future_repeals else None,
                prior_migration_events=tuple(effective_migration_events_out),
                processed_amendment_titles=processed_amendment_titles,
            ),
            signals.process_sinks(
                compiled_ops_out=compiled_ops_out,
                lo_ops_out=lo_ops_out,
                failed_ops_out=failed_ops_out,
                migration_events_out=effective_migration_events_out,
            ),
        )
        state = _pm_result.output
        processed_amendment_titles[str(mid)] = record_titles.get(str(mid), "")
        phase_findings = _pm_result.findings()
        if checkpoint_callback is not None:
            _cp_state = state  # capture for lazy closure
            checkpoint_callback(ReplayCheckpoint(
                parent_id=plan.parent_id,
                amendment_id=mid,
                step_index=idx,
                total_steps=len(plan.amendment_ids),
                serialize_text=lambda _s=_cp_state: _serialize_text(_s.ir),
                ir_snapshot=lambda _s=_cp_state: _s.ir,
            ))
        signals.temporal_events.extend(_pm_result.temporal_events)
        signals.findings.extend(phase_findings)
        # Per-amendment invariant checks are expensive for heavily-amended
        # statutes (O(amendments * nodes)).  Skip them when the final
        # post-process check (below) is sufficient — i.e. when the caller
        # did not request a checkpoint callback (diagnostic/explain mode).
        if checkpoint_callback is not None:
            sorted_ir = _resort_children(state.ir)
            for violation in check_tree_invariants(sorted_ir):
                signals.findings.append(
                    build_tree_invariant_finding(
                        violation=violation,
                        source_statute=mid,
                        phase="post_amendment",
                        message="Replay tree invariant violated after amendment application.",
                    )
                )

    state = state.with_ir(post_process_tree(state.ir, plan.replay_profile.normalize_replay_text))
    # Sort before checking so transient sort_order violations from post-processing
    # are not emitted as replay findings.
    sorted_ir = _resort_children(state.ir)
    for violation in check_tree_invariants(sorted_ir):
        signals.findings.append(
            build_tree_invariant_finding(
                violation=violation,
                source_statute=plan.parent_id,
                phase="post_process",
                message="Replay tree invariant violated after replay post-processing.",
            )
        )

    if logger is not None and logger.isEnabledFor(10):  # logging.DEBUG
        for violation in check_tree_invariants(state.ir):
            logger.debug("  INVARIANT: %s", violation)

    return state


__all__ = [
    "ReplayPlan",
    "ReplaySignalBuffers",
    "append_chapter_seed_compiled_ops",
    "build_tree_invariant_finding",
    "prepare_replay_plan",
    "populate_replay_meta",
    "execute_replay_plan",
]

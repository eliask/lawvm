"""Explicit replay-plan stages for the Finnish frontend."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace as dataclasses_replace
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

if TYPE_CHECKING:
    from lawvm.core.stage_result import StageResult

from lawvm.corpus_store import CorpusStore
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    append_unique_effect_lifecycle_events,
    append_unique_effect_refs,
    append_unique_effect_relations,
)
from lawvm.core.compile_result import SourcePathology
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.provenance import MigrationEvent
from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
from lawvm.core.write_receipt import WriteReceipt
from lawvm.core.phase_result import Finding, OBLIGATION_ROLE, OBSERVATION_ROLE, PhaseResult
from lawvm.core.replay_contracts import ReplayCheckpoint, ReplayCheckpointCallback
from lawvm.core.tree_ops import resort_children as _resort_children
from lawvm.finland.amendment_selection import AmendmentSourcePathology
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.chapter_seed import ChapterSeedDiagnostic
from lawvm.finland.op_provenance import (
    ConfidenceTier,
    Recovered,
    RecognizerId,
    RecoverySurface,
    serialize_provenance,
)
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride
from lawvm.finland.future_repeal_prescan import (
    PreScanRepealDiagnostic,
    PreScanRepealTargetsRequest,
    PreScanRepealTargetsSinks,
)
from lawvm.finland.helpers import _parse_iso_date
from lawvm.finland.process_request import ProcessAmendmentRequest
from lawvm.finland.process_result_builder import ProcessAmendmentSinks
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.temporal_rewrites import reconcile_temporal_event_expiry_with_op_sources
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
    base_source_correction_op_ids: tuple[str, ...] = ()
    amendment_selection_residuals: tuple[AmendmentSourcePathology, ...] = ()


@dataclass(frozen=True, slots=True)
class StopBeforeReplayNotice:
    """User-facing diagnostic for a replay ``--before`` cutoff request."""

    raw_stop_before: str
    normalized_stop_before: str
    found_in_lineage: bool

    @property
    def message(self) -> str:
        if not self.found_in_lineage:
            return (
                f"WARNING: --before {self.raw_stop_before}: "
                "amendment not found in chain, ignoring"
            )
        return f"--before {self.raw_stop_before}: replay truncated before {self.normalized_stop_before}"


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
    commencement_expiry_overrides: list[EffectLifecycleOverride]
    mutation_events: list[ApplyMutationEvent]
    mutation_invariant_reports: list[Any]
    write_audits: list[ObservedWriteAudit]
    write_receipts: list[WriteReceipt]
    migration_events: list[MigrationEvent]
    temporal_events: list[Any]
    source_effects: list[EffectRef]
    effect_relations: list[EffectRelation]
    effect_lifecycle_events: list[EffectLifecycleEvent]
    restructure_plans: list[StructuralTransformPlan]
    # WAIST #6 carrier buffer: per-amendment canonical-op StageResult accounts
    # appended by ``compile_amendment_ops`` (via the process sinks). Aggregated at
    # replay assembly into ``ReplayProducts.canonical_op_stage``.
    canonical_op_stages: list["StageResult[Any]"]

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
            mutation_invariant_reports=[],
            write_audits=[],
            write_receipts=[],
            migration_events=[],
            temporal_events=[],
            source_effects=[],
            effect_relations=[],
            effect_lifecycle_events=[],
            restructure_plans=[],
            canonical_op_stages=[],
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
        commencement_expiry_overrides_out: Optional[List[EffectLifecycleOverride]] = None,
        mutation_events_out: Optional[List[Any]] = None,
        write_audits_out: Optional[List[Any]] = None,
        write_receipts_out: Optional[List[Any]] = None,
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
            mutation_invariant_reports=[],
            write_audits=write_audits_out if write_audits_out is not None else [],
            write_receipts=write_receipts_out if write_receipts_out is not None else [],
            migration_events=(
                migration_events_out if migration_events_out is not None else []
            ),
            temporal_events=temporal_events_out if temporal_events_out is not None else [],
            source_effects=[],
            effect_relations=[],
            effect_lifecycle_events=[],
            restructure_plans=(
                restructure_plans_out if restructure_plans_out is not None else []
            ),
            canonical_op_stages=[],
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
            mutation_invariant_reports_out=self.mutation_invariant_reports,
            write_audits_out=self.write_audits,
            write_receipts_out=self.write_receipts,
            migration_events_out=migration_events_out,
            restructure_plans_out=self.restructure_plans,
            canonical_op_stages_out=self.canonical_op_stages,
        )


def _normalize_stop_before(stop_before: str) -> str:
    if not stop_before:
        return ""
    token = stop_before.replace("-", "/")
    if "/" not in token:
        return token
    parts = token.split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts[0]) == 4 else f"{parts[1]}/{parts[0]}"


def build_stop_before_replay_notice(
    stop_before: str,
    amendment_records: list[dict[str, Any]],
) -> StopBeforeReplayNotice | None:
    normalized_stop_before = _normalize_stop_before(stop_before)
    if not normalized_stop_before:
        return None
    plan_lineage_ids = [str(record["statute_id"]) for record in amendment_records]
    return StopBeforeReplayNotice(
        raw_stop_before=stop_before,
        normalized_stop_before=normalized_stop_before,
        found_in_lineage=normalized_stop_before in plan_lineage_ids,
    )


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
        orig_bytes, base_source_correction_op_ids = _get_corr_patch_table().patch_source_body_xml(
            orig_bytes,
            parent_id,
        )
    else:
        base_source_correction_op_ids = []

    ctx = StatuteContext.from_xml(orig_bytes, label_postprocessor)
    # Build the attachment-supplement tuple for this statute — extract
    # ``<a href="media/N.pdf">`` links from the (patched) consolidated XML
    # and fetch + parse each PDF via the corpus store. The supplements
    # ride on StatuteContext as a projection-sidecar tuple; the replay
    # fold does not consume them (they're attachment content, not body
    # effect), but ``lawvm show`` / ``lawvm dump`` projection paths
    # surface them per SDOC-13 (a projection must include attachments
    # unless explicitly scoped out). Built here so the supplement tuple
    # traces through master.ctx as part of the standard replay plan —
    # existing tests that build StatuteContext indirectly continue to
    # receive an empty tuple.
    from lawvm.finland.attachment_ir import (
        build_attachment_ir_supplements,
        extract_attachment_pdf_links,
    )
    att_links = extract_attachment_pdf_links(orig_bytes)
    if att_links:
        att_supplements = build_attachment_ir_supplements(
            cs=corpus,
            sid=parent_id,
            links=att_links,
            source_ref_prefix=f"finlex://sd-cons/{parent_id}/media",
        )
        if att_supplements:
            ctx = dataclasses_replace(
                ctx, attachment_supplements=tuple(att_supplements)
            )
    initial_state = ReplayState(ir=ctx.base_ir)
    replay_profile = get_replay_profile(mode)
    amendment_selection_residuals: list[AmendmentSourcePathology] = []
    amendment_records, cutoff_date, oracle_version_amendment_id = resolve_applicable_amendment_records(
        parent_id,
        mode,
        corpus=corpus,
        residuals_out=amendment_selection_residuals,
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
        base_source_correction_op_ids=tuple(base_source_correction_op_ids),
        amendment_selection_residuals=tuple(amendment_selection_residuals),
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


def build_amendment_selection_source_pathologies(
    residuals: tuple[AmendmentSourcePathology, ...],
    *,
    parent_id: str,
) -> list[SourcePathology]:
    """Project amendment-selection source residuals onto the replay sink.

    The selection layer records a candidate amendment dropped from the replay
    plan for a source reason (missing source XML bytes) as an
    ``AmendmentSourcePathology``. The backward tuple adapter used by the live
    replay path discards the structured selection object, so without this
    projection a missing amendment source would silently shorten the replay
    plan with no production-visible witness. Here the residuals are converted to
    the same ``SourcePathology`` carrier the rest of the replay pipeline already
    surfaces (warnings + ``source_pathologies`` meta + strict findings).
    """

    pathologies: list[SourcePathology] = []
    for residual in residuals:
        pathologies.append(
            SourcePathology(
                code=residual.rule_id,
                message=residual.reason,
                source_statute=parent_id,
                detail={
                    "family": residual.family,
                    "phase": residual.phase,
                    "amendment_id": residual.amendment_id,
                    "blocking": residual.blocking,
                    "strict_disposition": residual.strict_disposition,
                },
            )
        )
    return pathologies


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
                # Typed provenance (canonical schema): a chapter-seed scaffold op
                # carries the SCOPE_CHAPTER_SEED scope recognizer. The serialized
                # form supersedes the three raw bag columns; serialized_bag_tags
                # reconstructs scope_provenance_tags == ("chapter_seed",) from it.
                "provenance": serialize_provenance(
                    Recovered(
                        surface=RecoverySurface.SCOPE,
                        recognizer_ids=frozenset({RecognizerId.SCOPE_CHAPTER_SEED}),
                        tier=ConfidenceTier.ANCHORED,
                    )
                ),
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


def _effective_dates_by_amendment_record(
    amendment_records: list[dict[str, Any]],
) -> dict[str, dt.date]:
    effective_dates: dict[str, dt.date] = {}
    for record in amendment_records:
        amendment_id = str(record.get("statute_id") or "")
        effective_date = _parse_iso_date(str(record.get("effective_date") or ""))
        if amendment_id and effective_date is not None:
            effective_dates[amendment_id] = effective_date
    return effective_dates


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
    commencement_expiry_overrides_out: Optional[List[EffectLifecycleOverride]] = None,
    mutation_events_out: Optional[List[Any]] = None,
    write_audits_out: Optional[List[Any]] = None,
    write_receipts_out: Optional[List[Any]] = None,
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
        write_receipts_out=write_receipts_out,
        migration_events_out=migration_events_out,
        temporal_events_out=temporal_events_out,
        restructure_plans_out=restructure_plans_out,
    )
    signals.findings.extend(
        Finding(
            kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
            role=OBLIGATION_ROLE,
            stage="base_source_acquisition",
            blocking=False,
            source_statute=plan.parent_id,
            detail={
                "op_id": op_id,
                "corrected_by": "corrigendum_patch_table",
                "source_role": "base_statute_xml",
            },
        )
        for op_id in plan.base_source_correction_op_ids
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
            effective_dates_by_amendment=_effective_dates_by_amendment_record(
                plan.amendment_records,
            ),
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
    record_edge_kinds = {
        str(record.get("statute_id") or ""): str(record.get("edge_kind") or "oracle_amendedBy")
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
                amendment_edge_kind=record_edge_kinds.get(str(mid), "oracle_amendedBy"),
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
        reconcile_temporal_event_expiry_with_op_sources(
            signals.temporal_events,
            lo_ops_out,
            target_statute=plan.parent_id,
        )
        append_unique_effect_refs(
            signals.source_effects,
            _pm_result.source_effects,
            subject="replay phase result",
        )
        append_unique_effect_relations(
            signals.effect_relations,
            _pm_result.effect_relations,
            subject="replay phase result",
        )
        append_unique_effect_lifecycle_events(
            signals.effect_lifecycle_events,
            _pm_result.effect_lifecycle_events,
            subject="replay phase result",
        )
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

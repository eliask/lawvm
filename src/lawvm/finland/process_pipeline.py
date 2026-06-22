"""Typed per-amendment process pipeline for Finland.

This module owns the end-to-end processing of one amendment act over a replay
state.  The individual phases remain in the focused ``process_*`` modules; this
file is only the coordinator that wires acquisition, frontend normalization,
compile projection, apply, temporal postprocessing, and failed-op governance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TypeVar

from lawvm.core.compile_result import StrictProfile
from lawvm.core.invariant_surface_matrix import (
    FI_REPLAY_FOLD_SURFACE,
    project_transition_detector_findings,
)
from lawvm.core.mutation_accounting import MutationAccountingResult
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.source_acquisition import SourceBundleAdmission
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.effect_lifecycle import (
    append_unique_effect_lifecycle_events,
    append_unique_effect_refs,
    append_unique_effect_relations,
)
from lawvm.finland.acquisition import amendment_lacks_operative_structure as _amendment_lacks_operative_structure
from lawvm.finland.apply_ops_boundary import ApplyOpsRequest, ApplyOpsSinks
from lawvm.finland.apply_ops_executor import _apply_ops_to_tree_typed
from lawvm.finland.citation_routing import (
    _single_target_title_names_other_statute,
    johtolause_cited_target_ids,
)
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.constraints import muutos_node_lookup_cache_scope
from lawvm.finland.elaboration_rule_dispatch import (
    PROCESS_AMENDMENT_PIPELINE,
    emit_elaboration_pipeline_observation,
    run_registered_elaboration_stage,
    validate_elaboration_pipeline,
)
from lawvm.finland.effect_lifecycle_projection import build_finland_effect_lifecycle
from lawvm.finland.frontend_compile import (
    _enrich_ops_from_amendment_tree,
    _tree_title,
    normalize_and_compile_ops,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.process_acquisition import ProcessAcquisitionContext
from lawvm.finland.process_apply_fold import normalize_process_apply_fold
from lawvm.finland.process_apply_projection import ProcessApplyProjectionContext
from lawvm.finland.process_call import ResolvedProcessAmendmentCall, resolve_process_amendment_call
from lawvm.finland.process_compile_signals import ProcessCompileSignalsContext
from lawvm.finland.process_failed_op_governance import ProcessFailedOpGovernance
from lawvm.finland.process_frontend_normalization import ProcessFrontendNormalizationContext
from lawvm.finland.process_precompile_selection import ProcessPrecompileSelectionContext
from lawvm.finland.process_request import ProcessAmendmentRequest
from lawvm.finland.process_result_builder import ProcessAmendmentSinks, ProcessResultBuilder
from lawvm.finland.process_route_rejection import ProcessRouteRejectionContext
from lawvm.finland.process_runtime import build_process_runtime
from lawvm.finland.process_structural_prepare import ProcessStructuralPrepareContext
from lawvm.finland.process_temporal_authority import ProcessTemporalAuthorityContext
from lawvm.finland.process_temporal_postprocessing import ProcessTemporalPostprocessContext
from lawvm.finland.payload_realization_audit import payload_realization_findings
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState
from lawvm.finland.vts import extract_vts_repeals_fallback

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _run_process_stage(
    rule_id: str,
    stage_fn: Callable[[], T],
    *,
    process_findings: list[Finding],
    parent_id: str,
    amendment_id: str,
) -> T:
    return run_registered_elaboration_stage(
        rule_id,
        stage_fn,
        findings_out=process_findings,
        source_statute=parent_id,
        amendment_id=amendment_id,
    )


def _verify_staged_source_identity(
    *,
    amendment_id: str,
    source_model: AmendmentSourceModel,
    read_witness: SourceWitness | DigestWitness | None,
    admission: SourceBundleAdmission | None,
) -> None:
    """Consume the staged source read's evidence + authority (WAIST #1).

    The content witness carried out of ``read_source_staged`` must agree with the
    source model's own intrinsic content digest over the bytes the read returned
    (the pre-correction bytes acquisition received), and the source lane must be
    admitted to the bundle. This is the production read that proves the witness is
    no longer severed: a content/lane divergence at the source identity boundary
    becomes a fail-loud stop instead of a silent acceptance. Source-bundle
    admission is footing, never replay authority.
    """
    if read_witness is None:
        raise ValueError(
            f"staged source read for {amendment_id} returned no content witness; "
            "the source identity account must be present"
        )
    # Admission is the FI store's authority half. When a backing store evaluates a
    # source-bundle policy (the production TransparentCorpusStore always does), an
    # UN-admitted lane is a typed boundary fact, not a silent acceptance. A store
    # with no policy (the ABC default / a duck-typed stub) carries no admission;
    # the witness-digest consistency below still holds.
    if admission is not None and not admission.admitted:
        raise ValueError(
            f"source lane for {amendment_id} not admitted to the bundle "
            f"(status={admission.admission_status!r}, lane={admission.source_lane!r})"
        )
    witness_digest = (
        read_witness.digest
        if isinstance(read_witness, SourceWitness)
        else read_witness
    )
    if witness_digest is None:
        raise ValueError(
            f"staged source witness for {amendment_id} carries no content digest"
        )
    # The read returns the pre-correction bytes acquisition consumes; the model's
    # pre_correction_digest is set iff a correction changed bytes, else the bytes
    # are unchanged and source_digest is over those same bytes.
    expected = source_model.pre_correction_digest or source_model.source_digest
    if expected is not None and expected.digest != witness_digest.digest:
        raise ValueError(
            f"staged source content digest for {amendment_id} "
            f"({witness_digest.digest}) diverges from the source model digest "
            f"({expected.digest}) over the same read bytes"
        )


def _apply_dispositions_by_op_id(
    process_findings: list[Finding],
    *,
    amendment_id: str,
) -> dict[str, str]:
    dispositions: dict[str, str] = {}
    for finding in process_findings:
        if finding.kind != "APPLY.RESOLVED_OP_AUDIT":
            continue
        if finding.source_statute and finding.source_statute != amendment_id:
            continue
        detail = finding.detail.get("detail", finding.detail)
        if not isinstance(detail, Mapping):
            continue
        op_id = str(detail.get("op_id") or "")
        disposition = str(detail.get("disposition") or "")
        if op_id and disposition:
            dispositions[op_id] = disposition
    return dispositions


def _finish_process_amendment(
    state: ReplayState,
    *,
    result_builder: ProcessResultBuilder,
    process_findings: list[Finding],
    parent_id: str,
    amendment_id: str,
) -> PhaseResult[ReplayState]:
    _run_process_stage(
        "fi.process.pipeline",
        lambda: None,
        process_findings=process_findings,
        parent_id=parent_id,
        amendment_id=amendment_id,
    )
    return _run_process_stage(
        "fi.process.result_builder",
        lambda: result_builder.build(state),
        process_findings=process_findings,
        parent_id=parent_id,
        amendment_id=amendment_id,
    )


def _accepted_route_should_use_vts_side_repeal_only(
    *,
    amendment_id: str,
    parent_id: str,
    parent_title: str,
    source_title: str,
    johto: str,
    amendment_edge_kind: str,
    source_model: AmendmentSourceModel,
    strict_profile: StrictProfile | None,
) -> bool:
    """Detect accepted routes whose only parent effect is a VTS side repeal."""
    try:
        source_year = int(str(amendment_id).split("/", 1)[0])
    except (TypeError, ValueError, IndexError):
        return False
    cited_ids = tuple(johtolause_cited_target_ids(johto, source_year))
    if amendment_edge_kind == "source_vts_explicit":
        if parent_id in cited_ids:
            return False
    elif not _single_target_title_names_other_statute(source_title, parent_title):
        return False
    elif cited_ids:
        return False
    return bool(
        source_model.extract_vts_cross_statute_repeals(
            parent_id=parent_id,
            parent_title=parent_title,
            strict_profile=strict_profile,
            skipped_targets_out=None,
        )
    )


def process_muutoslaki(
    request: ProcessAmendmentRequest,
    sinks: ProcessAmendmentSinks | None = None,
) -> PhaseResult[ReplayState]:
    """Process one amendment through the typed amendment-processing boundary."""

    return process_muutoslaki_resolved(
        resolve_process_amendment_call(request, sinks)
    )


def process_muutoslaki_resolved(
    process_call: ResolvedProcessAmendmentCall,
) -> PhaseResult[ReplayState]:
    """Process one amendment statute end-to-end.

    Returns a ``PhaseResult`` where:
    - ``output`` is the updated replay state after applying this amendment;
    - ``findings`` carry source pathologies, elaboration observations, replay
      warnings/rejections, and failed-op obligations;
    - ``temporal_events`` carry executable amendment temporal authority.
    """

    amendment_id = process_call.amendment_id
    amendment_edge_kind = process_call.amendment_edge_kind
    state = process_call.state
    ctx = process_call.ctx
    replay_mode = process_call.replay_mode
    compiled_ops_out = process_call.compiled_ops_out
    lo_ops_out = process_call.lo_ops_out
    parent_id = process_call.parent_id
    strict_profile = process_call.strict_profile
    chapter_seed_skip = process_call.chapter_seed_skip
    corpus = process_call.corpus
    future_repeals = process_call.future_repeals
    regex_recognition_coverage_out = process_call.regex_recognition_coverage_out
    mutation_events_out = process_call.mutation_events_out
    write_audits_out = process_call.write_audits_out
    write_receipts_out = process_call.write_receipts_out
    migration_events_out = process_call.migration_events_out
    canonical_op_stages_out = process_call.canonical_op_stages_out
    runtime = build_process_runtime(process_call)
    amendment_temporal_events = runtime.amendment_temporal_events
    process_findings = runtime.process_findings
    _run_process_stage(
        "fi.process.runtime",
        lambda: runtime,
        process_findings=process_findings,
        parent_id=parent_id,
        amendment_id=amendment_id,
    )
    compat_failed_ops = runtime.compat_failed_ops
    compat_source_pathologies = runtime.compat_source_pathologies
    compat_elaboration_observations = runtime.compat_elaboration_observations
    compat_sparse_slot_bindings = runtime.compat_sparse_slot_bindings
    compat_sparse_leftovers = runtime.compat_sparse_leftovers
    commencement_expiry_override_notes = runtime.commencement_expiry_override_notes
    vts_skipped_targets = runtime.vts_skipped_targets
    effective_restructure_plans_out = runtime.effective_restructure_plans_out
    processed_amendment_titles = runtime.processed_amendment_titles

    finding_recorder = runtime.finding_recorder
    record_process_finding = runtime.record_process_finding
    corpus = runtime.corpus
    migration_ledger = runtime.migration_ledger
    migration_ledger_initial_len = runtime.migration_ledger_initial_len
    result_builder = runtime.result_builder

    pipeline_specs = validate_elaboration_pipeline(PROCESS_AMENDMENT_PIPELINE)
    emit_elaboration_pipeline_observation(
        process_findings,
        rule_ids=tuple(spec.rule_id for spec in pipeline_specs),
        source_statute=parent_id,
        amendment_id=amendment_id,
        pipeline_family="process_amendment",
        stage="process",
    )

    try:
        # Staged source read (WAIST #1): the dominant amendment-source consumer.
        # Reads .value as the bytes AND the additive accounts — the content
        # SourceWitness/DigestWitness (.evidence) and the SourceBundleAdmission
        # (.authority.source_admission) — so the source identity is type-carried,
        # not convention-bridged. read_source_staged is the production consumer
        # that un-severs the previously-dead read_source_witness asset.
        source_staged = corpus.read_source_staged(amendment_id)
        if source_staged is None:
            _replay_print(f"  [{amendment_id}] not found in corpus — skipping")
            return _finish_process_amendment(
                state,
                result_builder=result_builder,
                process_findings=process_findings,
                parent_id=parent_id,
                amendment_id=amendment_id,
            )
        xml_bytes = source_staged.value
        source_admission = source_staged.authority.source_admission
        source_read_witness = (
            source_staged.evidence.witnesses[0]
            if source_staged.evidence.witnesses
            else None
        )
        acquired = _run_process_stage(
            "fi.process.acquisition",
            lambda: ProcessAcquisitionContext(
                amendment_id=amendment_id,
                parent_id=parent_id,
                parent_title=ctx.title,
                parent_issue_date=ctx.issue_date,
                xml_bytes=xml_bytes,
                strict_profile=strict_profile,
                processed_amendment_titles=processed_amendment_titles,
                effect_relation_signals=runtime.effect_relation_signals,
                finding_recorder=finding_recorder,
                record_finding=record_process_finding,
                replay_print=_replay_print,
                tree_title=_tree_title,
                amendment_lacks_operative_structure=_amendment_lacks_operative_structure,
            ).acquire(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        source_model = acquired.source_model
        lacks_operative_structure = acquired.lacks_operative_structure
        operative_tags = list(acquired.operative_tags)
        johto = acquired.johto
        source_title = acquired.source_title
        acquisition = acquired.acquisition
        used_preamble_body_fallback = acquired.used_preamble_body_fallback

        # Consume the staged-read evidence/authority (WAIST #1): the content
        # witness from the READ must agree with the source model's own intrinsic
        # content digest (over the SAME pre-correction bytes the read returned),
        # and the source lane must be admitted to the bundle. Both are typed,
        # returned facts now — a content/lane divergence at the source boundary is
        # caught here, not silently accepted. 0-delta on the green corpus (same
        # bytes -> same sha256; the Farchive lane is admitted).
        _verify_staged_source_identity(
            amendment_id=amendment_id,
            source_model=source_model,
            read_witness=source_read_witness,
            admission=source_admission,
        )

        should_apply = acquisition.decision.should_apply
        route_reason = acquisition.decision.route_reason
        ops: list[AmendmentOp] = []
        vts_ops_enrich_done = False
        if not should_apply:
            route_rejection = _run_process_stage(
                "fi.process.route_rejection",
                lambda: ProcessRouteRejectionContext(
                    amendment_id=amendment_id,
                    parent_id=parent_id,
                    parent_title=ctx.title,
                    source_title=source_title,
                    johto=johto,
                    source_model=source_model,
                    route_reason=route_reason,
                    route_target_amendment_id=acquisition.decision.route_target_amendment_id,
                    strict_profile=strict_profile,
                    replay_mode=replay_mode,
                    lo_ops_out=lo_ops_out,
                    vts_skipped_targets=vts_skipped_targets,
                    commencement_expiry_override_notes=commencement_expiry_override_notes,
                    effect_relation_signals=runtime.effect_relation_signals,
                    record_finding=record_process_finding,
                    replay_print=_replay_print,
                ).handle(),
                process_findings=process_findings,
                parent_id=parent_id,
                amendment_id=amendment_id,
            )
            if route_rejection.should_return_state:
                return _finish_process_amendment(
                    state,
                    result_builder=result_builder,
                    process_findings=process_findings,
                    parent_id=parent_id,
                    amendment_id=amendment_id,
                )
            ops = list(route_rejection.ops)
            vts_ops_enrich_done = route_rejection.vts_ops_enrich_done
            skip_to_compile = route_rejection.skip_to_compile
        elif _accepted_route_should_use_vts_side_repeal_only(
            amendment_id=amendment_id,
            parent_id=parent_id,
            parent_title=ctx.title,
            source_title=source_title,
            johto=johto,
            amendment_edge_kind=amendment_edge_kind,
            source_model=source_model,
            strict_profile=strict_profile,
        ):
            route_rejection = _run_process_stage(
                "fi.process.route_rejection",
                lambda: ProcessRouteRejectionContext(
                    amendment_id=amendment_id,
                    parent_id=parent_id,
                    parent_title=ctx.title,
                    source_title=source_title,
                    johto=johto,
                    source_model=source_model,
                    route_reason="citation_mismatch_skip",
                    route_target_amendment_id="",
                    strict_profile=strict_profile,
                    replay_mode=replay_mode,
                    lo_ops_out=lo_ops_out,
                    vts_skipped_targets=vts_skipped_targets,
                    commencement_expiry_override_notes=commencement_expiry_override_notes,
                    effect_relation_signals=runtime.effect_relation_signals,
                    record_finding=record_process_finding,
                    replay_print=_replay_print,
                ).handle(),
                process_findings=process_findings,
                parent_id=parent_id,
                amendment_id=amendment_id,
            )
            if route_rejection.should_return_state:
                return _finish_process_amendment(
                    state,
                    result_builder=result_builder,
                    process_findings=process_findings,
                    parent_id=parent_id,
                    amendment_id=amendment_id,
                )
            ops = list(route_rejection.ops)
            vts_ops_enrich_done = route_rejection.vts_ops_enrich_done
            skip_to_compile = route_rejection.skip_to_compile
        else:
            skip_to_compile = False

        amendment_tree_metadata = source_model.amendment_tree_metadata(amendment_id)
        # Stamp the byte-level source anchor captured at acquisition (which owns
        # the raw bytes + chosen operative clause) onto the frontend metadata so
        # it reaches OperationSource -> WriteReceipt. None stays fail-loud.
        if acquired.source_anchor is not None:
            amendment_tree_metadata = replace(
                amendment_tree_metadata, source_anchor=acquired.source_anchor
            )

        precompile_selection = _run_process_stage(
            "fi.process.precompile_selection",
            lambda: ProcessPrecompileSelectionContext(
                amendment_id=amendment_id,
                parent_id=parent_id,
                parent_title=ctx.title,
                source_title=source_title,
                johto=johto,
                source_model=source_model,
                strict_profile=strict_profile,
                acquisition=acquisition,
                skip_to_compile=skip_to_compile,
                ops=ops,
                vts_ops_enrich_done=vts_ops_enrich_done,
                lacks_operative_structure=lacks_operative_structure,
                operative_tags=operative_tags,
                source_pathologies=compat_source_pathologies,
                vts_skipped_targets=vts_skipped_targets,
                finding_recorder=finding_recorder,
                replay_print=_replay_print,
                extract_vts_repeals=extract_vts_repeals_fallback,
                enrich_ops_from_amendment_tree=_enrich_ops_from_amendment_tree,
                amendment_metadata=amendment_tree_metadata,
            ).select(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        if precompile_selection.should_return_state:
            return _finish_process_amendment(
                state,
                result_builder=result_builder,
                process_findings=process_findings,
                parent_id=parent_id,
                amendment_id=amendment_id,
            )
        ops = list(precompile_selection.ops)
        vts_ops_enrich_done = precompile_selection.vts_ops_enrich_done

        if not vts_ops_enrich_done:
            phase2_result = _run_process_stage(
                "fi.process.frontend_normalization",
                lambda: ProcessFrontendNormalizationContext(
                    johto=johto,
                    source_model=source_model,
                    state=state,
                    base_ir=ctx.base_ir,
                    amendment_id=amendment_id,
                    source_title=source_title,
                    used_preamble_body_fallback=used_preamble_body_fallback,
                    parent_id=parent_id,
                    strict_profile=strict_profile,
                    regex_recognition_coverage_out=regex_recognition_coverage_out,
                    normalize_and_compile_ops=normalize_and_compile_ops,
                    amendment_metadata=amendment_tree_metadata,
                ).run(),
                process_findings=process_findings,
                parent_id=parent_id,
                amendment_id=amendment_id,
            )
            ops = list(phase2_result.ops)
            amendment_temporal_events.extend(phase2_result.temporal_events)
            append_unique_effect_refs(
                runtime.source_effects,
                phase2_result.source_effects,
                subject="process frontend normalization",
            )
            append_unique_effect_relations(
                runtime.effect_relations,
                phase2_result.effect_relations,
                subject="process frontend normalization",
            )
            append_unique_effect_lifecycle_events(
                runtime.effect_lifecycle_events,
                phase2_result.effect_lifecycle_events,
                subject="process frontend normalization",
            )
            compat_elaboration_observations.extend(phase2_result.elaboration_observations)
            process_findings.extend(phase2_result.process_findings)

        ops = _run_process_stage(
            "fi.process.structural_prepare",
            lambda: ProcessStructuralPrepareContext(
                amendment_id=amendment_id,
                target_statute=ctx.id,
                ops=ops,
                chapter_seed_skip=chapter_seed_skip,
                restructure_plans=effective_restructure_plans_out,
                elaboration_observations=compat_elaboration_observations,
                replay_print=_replay_print,
            ).prepare(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )

        temporal_authority = _run_process_stage(
            "fi.process.temporal_authority",
            lambda: ProcessTemporalAuthorityContext(
                amendment_id=amendment_id,
                johto=johto,
                source_model=source_model,
                record_finding=record_process_finding,
            ).derive(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        amendment_effective_date = temporal_authority.effective_date
        amendment_expiry_date = temporal_authority.expiry_date
        amendment_issue_date = temporal_authority.issue_date

        with muutos_node_lookup_cache_scope():
            compile_result = compile_amendment_ops(
                state,
                ops,
                source_model,
                johto,
                replay_mode,
                compiled_ops_out=compiled_ops_out,
                strict_profile=strict_profile,
                source_ref=amendment_id,
                source_title=source_title,
                target_statute=ctx.id,
                canonical_op_stage_out=canonical_op_stages_out,
            )
        resolved = compile_result.output

        _run_process_stage(
            "fi.process.compile_signals",
            lambda: ProcessCompileSignalsContext(
                amendment_id=amendment_id,
                parent_id=parent_id,
                resolved=resolved,
                compile_result=compile_result,
                amendment_temporal_events=amendment_temporal_events,
                source_effects=runtime.source_effects,
                effect_relations=runtime.effect_relations,
                effect_lifecycle_events=runtime.effect_lifecycle_events,
                source_pathologies=compat_source_pathologies,
                elaboration_observations=compat_elaboration_observations,
                sparse_slot_bindings=compat_sparse_slot_bindings,
                sparse_leftovers=compat_sparse_leftovers,
                process_findings=process_findings,
                record_finding=record_process_finding,
            ).project(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )

        observed_touch_results: list[MutationAccountingResult] = []
        lo_ops_start = len(lo_ops_out or ())
        before_apply_ir = state.ir
        final_state = _apply_ops_to_tree_typed(
            ApplyOpsRequest(
                state=state,
                ctx=ctx,
                resolved=resolved,
                ops=ops,
                source_model=source_model,
                johto=johto,
                amendment_id=amendment_id,
                source_title=source_title,
                amendment_issue_date=amendment_issue_date,
                amendment_effective_date=amendment_effective_date,
                amendment_expiry_date=amendment_expiry_date,
                replay_mode=replay_mode,
                strict_profile=strict_profile,
                vts_ops_enrich_done=vts_ops_enrich_done,
                future_repeals=future_repeals,
            ),
            ApplyOpsSinks(
                compiled_ops_out=compiled_ops_out,
                lo_ops_out=lo_ops_out,
                failed_ops_out=compat_failed_ops,
                source_pathologies_out=compat_source_pathologies,
                mutation_events_out=mutation_events_out,
                migration_ledger=migration_ledger,
                restructure_plans_out=effective_restructure_plans_out,
                observations_out=compat_elaboration_observations,
                findings_out=process_findings,
                observed_touch_results_out=observed_touch_results,
                # write_audits_out / write_receipts_out are NON-Optional on
                # ApplyOpsSinks; pass the caller's concrete list when present,
                # else a fresh one (the fold always accounts every landed
                # write). Threading write_receipts_out up carries the landed
                # WriteReceipts into ReplayResult so the certificate stage can
                # cross-check covering-state transitions against them.
                write_audits_out=write_audits_out if write_audits_out is not None else [],
                write_receipts_out=write_receipts_out if write_receipts_out is not None else [],
            ),
        )
        amendment_lo_ops = tuple((lo_ops_out or [])[lo_ops_start:])
        source_effects, effect_relations, lifecycle_events = build_finland_effect_lifecycle(
            target_statute=parent_id,
            canonical_ops=amendment_lo_ops,
            temporal_events=(),
            lifecycle_overrides=tuple(commencement_expiry_override_notes),
            relation_signals=tuple(runtime.effect_relation_signals),
            known_source_effects=tuple(runtime.source_effects),
        )
        append_unique_effect_refs(
            runtime.source_effects,
            source_effects,
            subject="process canonical operation projection",
        )
        append_unique_effect_relations(
            runtime.effect_relations,
            effect_relations,
            subject="process canonical operation projection",
        )
        append_unique_effect_lifecycle_events(
            runtime.effect_lifecycle_events,
            lifecycle_events,
            subject="process canonical operation projection",
        )
        project_transition_detector_findings(
            before_ir=before_apply_ir,
            operations=amendment_lo_ops,
            profile=FI_REPLAY_FOLD_SURFACE.replay_profile,
            surface=FI_REPLAY_FOLD_SURFACE,
            replay_findings=process_findings,
            replay_meta_out=None,
            replay_print=_replay_print,
            source_statute=parent_id,
            phase="replay_apply",
        )
        _run_process_stage(
            "fi.process.apply_projection",
            lambda: ProcessApplyProjectionContext(
                amendment_id=amendment_id,
                observed_touch_results=observed_touch_results,
                elaboration_observations=compat_elaboration_observations,
                migration_ledger=migration_ledger,
                migration_ledger_initial_len=migration_ledger_initial_len,
                migration_events_out=migration_events_out,
                logger=logger,
            ).project(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        _run_process_stage(
            "fi.process.temporal_postprocessing",
            lambda: ProcessTemporalPostprocessContext(
                amendment_id=amendment_id,
                parent_id=parent_id,
                ctx_id=ctx.id,
                source_title=source_title,
                johto=johto,
                source_model=source_model,
                base_ir=ctx.base_ir,
                state=state,
                replay_mode=replay_mode,
                amendment_issue_date=amendment_issue_date,
                amendment_effective_date=amendment_effective_date,
                lo_ops_out=lo_ops_out,
                compiled_ops_out=compiled_ops_out,
                amendment_temporal_events=amendment_temporal_events,
                commencement_expiry_override_notes=commencement_expiry_override_notes,
                record_finding=record_process_finding,
                replay_print=_replay_print,
                section_expiry_overrides=amendment_tree_metadata.section_expiry_overrides,
            ).run(),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        _run_process_stage(
            "fi.process.failed_op_governance",
            lambda: ProcessFailedOpGovernance(
                amendment_id=amendment_id,
                johto=johto,
                failed_ops=compat_failed_ops,
                process_findings=process_findings,
                source_pathologies=compat_source_pathologies,
                lo_ops=tuple(lo_ops_out or ()),
                resolved_ops=tuple(resolved),
                migration_ledger=migration_ledger,
                migration_ledger_initial_len=migration_ledger_initial_len,
                record_finding=record_process_finding,
            ).govern_all(final_state),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        final_state = _run_process_stage(
            "fi.process.apply_fold",
            lambda: normalize_process_apply_fold(
                final_state,
                amendment_id=amendment_id,
                process_findings=process_findings,
            ),
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )
        process_findings.extend(
            payload_realization_findings(
                resolved_ops=tuple(resolved),
                after_ir=final_state.ir,
                amendment_id=amendment_id,
                apply_dispositions_by_op_id=_apply_dispositions_by_op_id(
                    process_findings,
                    amendment_id=amendment_id,
                ),
            )
        )
        return _finish_process_amendment(
            final_state,
            result_builder=result_builder,
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )

    except KeyError:
        _replay_print(f"  [{amendment_id}] SKIPPED — not found in zip")
        return _finish_process_amendment(
            state,
            result_builder=result_builder,
            process_findings=process_findings,
            parent_id=parent_id,
            amendment_id=amendment_id,
        )

"""Typed Finland replay entrypoint.

This module owns the public replay boundary for one Finnish parent statute.
The lower-level stages live in ``replay_pipeline``, ``replay_capture``,
``replay_base_evidence``, ``replay_evidence_projection``,
``replay_fold_projection``, and ``replay_product_assembly``.
"""

from __future__ import annotations

import logging

from typing import Optional, cast

from lawvm.core.tree_ops import check_invariants as _check_tree_invariants
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_realization import drop_materialized_payload_realization_false_positives
from lawvm.finland.amendment_selection import resolve_applicable_amendment_records
from lawvm.finland.apply_replay_authorization import aggregate_replay_authority
from lawvm.finland.chapter_seed import seed_missing_chapters as _seed_missing_chapters
from lawvm.finland.consolidated_store import ConsolidatedArtifactSelector
from lawvm.finland.corpus import (
    CorpusStore,
    _get_corpus_store,
    _selected_consolidated_locator_and_provenance_for_statute,
    get_consolidated_oracle_suspect,
)
from lawvm.finland.future_repeal import build_future_repeal_suffix
from lawvm.finland.future_repeal_prescan import _pre_scan_repeal_targets
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.ops import get_replay_profile
from lawvm.finland.payload_realization_audit import attach_payload_gap_apply_dispositions
from lawvm.finland.post_process import post_process_tree
from lawvm.finland.replay_base_evidence import (
    ReplayBaseEvidenceSeedRequest,
    seed_replay_base_evidence_signals,
)
from lawvm.finland.replay_capture import ReplayCaptureRequest, resolve_replay_capture_sinks
from lawvm.finland.replay_evidence_projection import (
    ReplayEvidenceProjectionRequest,
    project_replay_evidence,
)
from lawvm.finland.replay_fold_projection import ReplayFoldProjectionRequest, project_replay_fold
from lawvm.finland.replay_notices import (
    replay_print as _replay_print,
    reset_replay_verbose as _reset_replay_verbose,
    set_replay_verbose as _set_replay_verbose,
)
from lawvm.finland.replay_pipeline import (
    ReplaySignalBuffers,
    build_amendment_selection_source_pathologies,
    build_stop_before_replay_notice,
    execute_replay_plan,
    populate_replay_meta,
    prepare_replay_plan,
)
from lawvm.finland.replay_product_assembly import (
    ReplayProductAssemblyRequest,
    assemble_replay_products,
)
from lawvm.finland.replay_products import (
    aggregate_canonical_op_stage,
    aggregate_structural_stage,
)
from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, resolve_replay_xml_request
from lawvm.finland.statute import OracleSelectorInfo, ReplayResult

logger = logging.getLogger(__name__)


def replay_xml(
    *,
    request: ReplayXmlRequest,
    sinks: ReplayXmlSinks | None = None,
) -> ReplayResult:
    """Replay all applicable amendments for one parent statute.

    ``mode`` controls the meaning of "applicable":

    - ``official_consolidation`` tries to reproduce the selected Finlex
      consolidated XML artifact for benchmarking.
    - ``legal_pit`` applies a point-in-time rule based on legal effective dates.

    The return value is a ``ReplayResult`` whose products include the replay
    fold state and the materialized point-in-time state.
    """

    replay_call = resolve_replay_xml_request(request=request, sinks=sinks)
    parent_id = replay_call.parent_id
    mode = replay_call.mode
    compiled_ops_out = replay_call.compiled_ops_out
    replay_meta_out = replay_call.replay_meta_out
    lo_ops_out = replay_call.lo_ops_out
    stop_before = replay_call.stop_before
    failed_ops_out = replay_call.failed_ops_out
    strict_profile = replay_call.strict_profile
    corpus = replay_call.corpus or _get_corpus_store()
    quiet = replay_call.quiet
    build_full_products = replay_call.build_full_products
    temporal_events_out = replay_call.temporal_events_out
    checkpoint_callback = replay_call.checkpoint_callback
    as_of = replay_call.as_of
    strict_johto_temporal = replay_call.strict_johto_temporal
    oracle_selector = replay_call.oracle_selector
    source_pathologies_out = replay_call.source_pathologies_out

    verbose_token = _set_replay_verbose(not quiet)
    try:
        from lawvm.finland.corrigendum import extract_inline_corrections as _extract_inline_corr
        from lawvm.finland.process_pipeline import process_muutoslaki

        profile = get_replay_profile(mode)
        plan = prepare_replay_plan(
            parent_id,
            mode=mode,
            strict_profile=strict_profile,
            corpus=corpus,
            stop_before=stop_before,
            label_postprocessor=_fi_label_postprocessor,
            get_replay_profile=get_replay_profile,
            resolve_applicable_amendment_records=(
                lambda resolved_parent_id, resolved_mode, corpus=None, residuals_out=None: resolve_applicable_amendment_records(
                    resolved_parent_id,
                    resolved_mode,
                    corpus=corpus,
                    selector=oracle_selector,
                    residuals_out=residuals_out,
                )
            ),
            get_consolidated_oracle_suspect=(
                lambda resolved_parent_id, corpus=None: get_consolidated_oracle_suspect(
                    resolved_parent_id,
                    corpus=corpus,
                    selector=oracle_selector,
                )
            ),
            extract_inline_corrections=_extract_inline_corr,
        )
        capture_sinks = resolve_replay_capture_sinks(
            ReplayCaptureRequest(
                compiled_ops_out=compiled_ops_out,
                lo_ops_out=lo_ops_out,
                failed_ops_out=failed_ops_out,
                build_full_products=build_full_products,
            )
        )
        signals = ReplaySignalBuffers.empty()
        seed_replay_base_evidence_signals(
            ReplayBaseEvidenceSeedRequest(parent_id=parent_id, ctx=plan.ctx),
            signals=signals,
        )
        signals.source_pathologies.extend(
            build_amendment_selection_source_pathologies(
                plan.amendment_selection_residuals,
                parent_id=parent_id,
            )
        )
        _replay_print(f"Master {parent_id} rehydrated. Title: {plan.ctx.title}")
        stop_before_notice = build_stop_before_replay_notice(stop_before, plan.amendment_records)
        if stop_before_notice is not None:
            _replay_print(stop_before_notice.message)
        populate_replay_meta(plan, replay_meta_out)
        if mode == "legal_pit" and plan.oracle_suspect:
            _replay_print(f"WARNING oracle suspect: {plan.oracle_suspect}")
        logger.debug(
            "Replay mode=%s cutoff=%s version=%s",
            mode,
            plan.cutoff_date.isoformat() if plan.cutoff_date else "-",
            plan.oracle_version_amendment_id or "-",
        )
        _replay_print(f"Applying {len(plan.amendment_ids)} muutoslait...")

        replay_fold_state = execute_replay_plan(
            plan,
            corpus=corpus,
            process_muutoslaki=process_muutoslaki,
            seed_missing_chapters=_seed_missing_chapters,
            pre_scan_repeal_targets=_pre_scan_repeal_targets,
            future_repeals_for_index=build_future_repeal_suffix,
            post_process_tree=post_process_tree,
            check_tree_invariants=_check_tree_invariants,
            compiled_ops_out=capture_sinks.compiled_ops,
            lo_ops_out=capture_sinks.legal_operations,
            failed_ops_out=capture_sinks.failed_ops,
            strict_profile=strict_profile,
            logger=logger,
            checkpoint_callback=checkpoint_callback,
            signal_buffers=signals,
        )
        if temporal_events_out is not None:
            temporal_events_out.extend(signals.temporal_events)
        replay_fold_state = project_replay_fold(
            ReplayFoldProjectionRequest(
                state=replay_fold_state,
                parent_id=parent_id,
                replay_findings=signals.findings,
                replay_meta_out=replay_meta_out,
                replay_print=_replay_print,
            )
        )
        project_replay_evidence(
            ReplayEvidenceProjectionRequest(
                parent_id=parent_id,
                replay_findings=signals.findings,
                source_pathologies=signals.source_pathologies,
                elaboration_observations=signals.elaboration_observations,
                sparse_slot_bindings=signals.sparse_slot_bindings,
                sparse_leftovers=signals.sparse_leftovers,
                regex_recognition_coverages=signals.regex_recognition_coverages,
                commencement_expiry_overrides=signals.commencement_expiry_overrides,
                write_audits=signals.write_audits,
                mutation_events=signals.mutation_events,
                restructure_plans=signals.restructure_plans,
                source_pathologies_out=source_pathologies_out,
                replay_meta_out=replay_meta_out,
                strict_profile=strict_profile,
                replay_print=_replay_print,
                mutation_invariant_reports=signals.mutation_invariant_reports,
            )
        )
        products = assemble_replay_products(
            ReplayProductAssemblyRequest(
                parent_id=parent_id,
                mode=mode,
                as_of=as_of,
                profile=profile,
                plan=plan,
                corpus=corpus,
                oracle_selector=oracle_selector,
                replay_fold_state=replay_fold_state,
                capture_sinks=capture_sinks,
                signals=signals,
                build_full_products=build_full_products,
                strict_johto_temporal=strict_johto_temporal,
                replay_meta_out=replay_meta_out,
                replay_print=_replay_print,
                debug_enabled=logger.isEnabledFor(logging.DEBUG),
                debug_log=logger.debug,
                quiet=quiet,
            )
        )
        findings = drop_materialized_payload_realization_false_positives(
            tuple(signals.findings),
            materialized_text=irnode_to_text(products.materialized_state.ir),
        )
        findings = attach_payload_gap_apply_dispositions(
            findings,
            materialized_as_of=(
                products.materialization_spec.as_of
                if products.materialization_spec is not None
                else ""
            ),
        )

        # StageResult endgame WAIST #7: aggregate the per-replay apply/replay
        # execution authority over every landed write (replay_authorized = AND
        # over all landed writes). Carried on ReplayProducts so the per-replay
        # clean-claim predicate the certificate firewall branches on is
        # type-carried, not convention-bridged. Descriptive: it mints
        # replay_authorized=True iff every landed receipt's boundary is explained
        # AND no apply-boundary touch-outside-target violation finding fired (the
        # exact conjunction that lets the writes stand today).
        products.apply_authority = aggregate_replay_authority(
            write_receipts=signals.write_receipts,
            findings=signals.findings,
        )

        # StageResult endgame WAIST #3: aggregate the per-op structural
        # write-footprint accounts over every landed WriteReceipt of this replay
        # (the same receipts that feed ``apply_authority``). Carried on
        # ReplayProducts so the certificate dossier routes the structural stage
        # into a per-stage account subroot instead of re-deriving it. The
        # aggregate is a pure fold over the landed receipts: union footprint
        # coverage (all owned) + the union of any blocking unexplained-divergence
        # residuals (EMPTY on the green corpus, where every container write
        # explains its boundary). The structural value is the replay's
        # materialized IR tree (always present, even when the materialization
        # stage account itself is not built on the plain path).
        products.structural_stage = aggregate_structural_stage(
            materialized_ir=products.materialized_state.ir,
            write_receipts=tuple(signals.write_receipts),
        )

        # StageResult endgame WAIST #6: aggregate the per-amendment canonical-op
        # compile accounts over every amendment of this replay. Each
        # ``compile_amendment_ops`` already builds one canonical-op ``StageResult``
        # and APPENDS it to ``signals.canonical_op_stages`` via the
        # ``canonical_op_stages_out`` sink (threaded through the process sinks).
        # The aggregate is a pure fold over those per-amendment accounts (union
        # candidate-op coverage partition + the union of the per-amendment compile
        # declines). Carried on ReplayProducts so the certificate dossier routes
        # the canonical-op stage into a per-stage account subroot instead of
        # re-deriving it from the stage-tagless union findings. FAITHFUL: the
        # carried accounts ARE the producer's own stages, not a reconstruction.
        # The decline VERDICT stays on the existing #6 single-channel.
        products.canonical_op_stage = aggregate_canonical_op_stage(
            tuple(signals.canonical_op_stages),
        )

        return ReplayResult(
            ctx=plan.ctx,
            products=products,
            findings=findings,
            oracle_selector_info=_oracle_selector_info(
                corpus=corpus,
                parent_id=parent_id,
                oracle_selector=oracle_selector,
            ),
            write_receipts=tuple(signals.write_receipts),
        )
    finally:
        _reset_replay_verbose(verbose_token)


def _oracle_selector_info(
    *,
    corpus: object,
    parent_id: str,
    oracle_selector: object | None,
) -> OracleSelectorInfo | None:
    if oracle_selector is None:
        return None
    archive = getattr(corpus, "_archive", None)
    if archive is None or not hasattr(archive, "locators"):
        return None
    _locator, provenance = _selected_consolidated_locator_and_provenance_for_statute(
        parent_id,
        cast(Optional[CorpusStore], corpus),
        selector=cast(Optional[ConsolidatedArtifactSelector], oracle_selector),
    )
    if provenance is None:
        return None
    return OracleSelectorInfo(
        selector_mode=provenance.selector_mode,
        chosen_artifact_version=provenance.chosen_version_tag,
        tolerance_applied=provenance.tolerance_applied,
        rejected_candidates=provenance.rejected_version_tags,
    )

"""UK Amendment Replay Pipeline.

This module implements the acquisition and op-extraction layer for building
a PIT (Point-in-Time) legal graph from first principles for UK legislation —
analogous to lawvm.finland.grafter but without LLM dependency for the
amendment schedule, since UK effects feeds provide structured metadata.

Architecture:
  1. Effects feed  → ordered list of StructuredAmendmentOps
  2. For each op: fetch the affecting act's XML from legislation.gov.uk
  3. Extract the provision text referenced by the op
  4. Compile to IR ops against the base statute IR
  5. Replay enacted base + IR ops → PIT states
  6. Compare against official consolidated versions (oracle score)

Current status:
  - effects.py owns effect-feed records, parsers, and acquisition manifests
  - AffectingActFetcher: downloads affecting act XML via legislation.gov.uk API
  - ProvisionExtractor: finds referenced provision text in affecting act XML
  - OpCompiler: converts effect/source payloads → typed IR operations
  - Replayer: applies IR ops to base enacted IR
"""

from __future__ import annotations

import json as json  # noqa: F401
import time
import xml.etree.ElementTree as ET  # noqa: F401
from pathlib import Path
from typing import Any, List, Optional

from lawvm.core.ir import (
    IRStatute,
    LegalOperation,
)
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_grafter import _LEG_NS as _LEG_NS  # noqa: F401
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    load_effects_for_statute_from_archive,
    uk_effect_requires_affecting_source_for_replay,  # noqa: F401
)
from lawvm.uk_legislation.addressing import (
    _order_schedule_materialization_ops,
)
from lawvm.uk_legislation.authority_filter import (
    _apply_uk_authority_mode,
)
from lawvm.uk_legislation.compiled_effect_facts import uk_compiled_effect_facts
from lawvm.uk_legislation.effect_compiler import compile_effect_to_ir_ops
from lawvm.uk_legislation.effect_source_selection import (
    EffectSourceSelection as _EffectSourceSelection,  # noqa: F401
    extracted_tag_and_text as _extracted_tag_and_text,
    select_source_for_effect as _select_source_for_effect,
    source_context_for_effect as _source_context_for_effect,  # noqa: F401
)
from lawvm.uk_legislation.lowering_records import (
    append_manual_compile_frontier_diagnostic,
    append_metadata_only_selection_rejection,
    append_no_ops_lowering_rejections,
    append_pit_date_filter_rejection,
    append_replay_applicability_filter_diagnostic,
    append_source_pathology_classified_diagnostic,
    append_source_pathology_filter_lowering_rejections,
    mark_nonreplay_lowering_rejections_nonblocking,
    mark_source_pathology_nonreplay_lowering_rejections_nonblocking,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
)
from lawvm.uk_legislation.ordering import (
    _order_uk_effects_for_replay,
    _order_uk_text_patch_preimage_chains,
)
from lawvm.uk_legislation.replay_applicability import (
    should_replay_nonstructural_ops,
)
from lawvm.uk_legislation.replay_executor import (
    UKReplayExecutor,
    _prepare_replay_uk_ops,
)

# Backward-compatible re-exports for older tools/tests that imported UK helper
# internals from this historical facade while the implementation moved out.
from lawvm.uk_legislation.authority_filter import (  # noqa: F401
    _uk_op_allowed_by_authority_mode as _uk_op_allowed_by_authority_mode,
)
from lawvm.uk_legislation.commencement import commencement_eid_set as commencement_eid_set  # noqa: F401
from lawvm.uk_legislation.effects import (  # noqa: F401
    load_effects_for_statute as load_effects_for_statute,
    parse_effects_from_bytes as parse_effects_from_bytes,
    parse_effects_from_feeds as parse_effects_from_feeds,
    parse_effects_from_metadata as parse_effects_from_metadata,
)
from lawvm.uk_legislation.ordering import (  # noqa: F401
    _uk_source_provision_order_key as _uk_source_provision_order_key,
)
from lawvm.uk_legislation.provenance_notes import (  # noqa: F401
    NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR as _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    NOTE_FRAGMENT_SUB as _NOTE_FRAGMENT_SUB,
    NOTE_METADATA_SOURCE_FALLBACK as _NOTE_METADATA_SOURCE_FALLBACK,
    NOTE_PRECEDING_EID as _NOTE_PRECEDING_EID,
    NOTE_REWRITE_WITNESS as _NOTE_REWRITE_WITNESS,
    NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR as _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR as _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
    NOTE_TABLE_COLUMN_INSERT_SELECTOR as _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    NOTE_TABLE_ROW_INSERT_SELECTOR as _NOTE_TABLE_ROW_INSERT_SELECTOR,
    NOTE_TABLE_ROW_REPLACE_SELECTOR as _NOTE_TABLE_ROW_REPLACE_SELECTOR,
    NOTE_TEXT_REWRITE_RULE as _NOTE_TEXT_REWRITE_RULE,
)
from lawvm.uk_legislation.provision_extractor import (  # noqa: F401
    extract_provision_element_from_bytes as extract_provision_element_from_bytes,
    _parse_ref as _parse_ref,
)
from lawvm.uk_legislation.replay_executor import replay_uk_ops as replay_uk_ops  # noqa: F401
from lawvm.uk_legislation.source_context import (  # noqa: F401
    _build_affecting_source_context as _build_affecting_source_context,
    _extract_from_affecting_source_context as _extract_from_affecting_source_context,
    _extract_from_affecting_source_context_with_observations as _extract_from_affecting_source_context_with_observations,
    _select_enacted_source_for_current_shell as _select_enacted_source_for_current_shell,
)
from lawvm.uk_legislation.substitution_metadata import (  # noqa: F401
    _repeal_tail_for_substituted_series_replacement as _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor as _retarget_substituted_series_to_replaced_anchor,
)
from lawvm.uk_legislation.target_parser import (  # noqa: F401
    _split_metadata_provisions as _split_metadata_provisions,
    _parse_affected_target as _parse_affected_target,
)
from lawvm.uk_legislation.text_rewrite_fragments import (  # noqa: F401
    _fragment_substitution as _fragment_substitution,
)
from lawvm.uk_legislation.xml_helpers import (  # noqa: F401
    _tag as _tag,
    _text_content as _text_content,
)

# ---------------------------------------------------------------------------
# Replay Pipeline
# ---------------------------------------------------------------------------


def _classify_compiled_effect_source_pathology(
    *,
    effect: UKEffectRecord,
    extracted_tag: Optional[str],
    extracted_text: str,
    compiled_ops: list[LegalOperation],
    lowering_rejections: Optional[list[dict[str, Any]]],
    lowering_rejection_start_index: int,
    structural_for_replay: bool,
) -> str:
    from lawvm.uk_legislation.source_adjudication import classify_uk_effect_source_pathology

    facts = uk_compiled_effect_facts(
        ops=compiled_ops,
        lowering_rejections=lowering_rejections or (),
        lowering_rejection_start_index=lowering_rejection_start_index,
    )
    return classify_uk_effect_source_pathology(
        extracted_tag=extracted_tag,
        extracted_text=extracted_text,
        op_actions=facts.op_actions,
        payload_kinds=facts.payload_kinds,
        payload_texts=facts.payload_texts,
        target_paths=facts.target_paths,
        lowering_rule_ids=facts.lowering_rule_ids,
        effect_type=effect.effect_type,
        is_structural=structural_for_replay,
    )


class UKReplayPipeline:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def compile_ops_for_statute(
        self,
        affected_act_id: str,
        pit_date: Optional[str] = None,
        archive: Optional[Any] = None,
        allow_metadata_backfill: bool = True,
        applicability_mode: str = "effective_date_plus_feed_applied",
        authority_mode: str = "current_mixed",
        allow_metadata_only_effects: bool = True,
        authority_rejections_out: Optional[list[dict[str, Any]]] = None,
        lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
        effect_feed_parse_rejections_out: Optional[list[dict[str, Any]]] = None,
        effect_diagnostics_out: Optional[list[dict[str, Any]]] = None,
        compile_phase_timings_out: Optional[dict[str, float]] = None,
    ) -> list[LegalOperation]:
        """Compile IR ops for *affected_act_id*.

        UK replay is archive-backed. Effects feeds and affecting act XMLs are
        loaded from the Farchive DB; deprecated on-disk XML fallbacks are
        intentionally not used.
        """
        if archive is None:
            raise ValueError(
                "UKReplayPipeline.compile_ops_for_statute requires archive-backed "
                "effects/XML; deprecated on-disk XML inputs have been removed"
            )

        phase_t0 = time.perf_counter()

        def _mark_compile_phase(name: str) -> None:
            nonlocal phase_t0
            now = time.perf_counter()
            if compile_phase_timings_out is not None:
                compile_phase_timings_out[name] = (
                    compile_phase_timings_out.get(name, 0.0) + (now - phase_t0)
                )
            phase_t0 = now

        # ── Load effects ────────────────────────────────────────────────────
        if effect_feed_parse_rejections_out is None:
            effects = load_effects_for_statute_from_archive(affected_act_id, archive)
        else:
            effects = load_effects_for_statute_from_archive(
                affected_act_id,
                archive,
                parse_rejections_out=effect_feed_parse_rejections_out,
            )
        _mark_compile_phase("compile_load_effects")

        replayable = list(effects)
        if pit_date:
            pit_replayable: list[UKEffectRecord] = []
            for e in replayable:
                effective_date = e.effective_date or "9999-99-99"
                if effective_date <= pit_date:
                    pit_replayable.append(e)
                    continue
                append_pit_date_filter_rejection(
                    effect_diagnostics_out,
                    effect=e,
                    effective_date=effective_date,
                    pit_date=pit_date,
                )
            replayable = pit_replayable

        replayable = _order_uk_effects_for_replay(
            replayable,
            diagnostics_out=effect_diagnostics_out,
            lowering_observations_out=lowering_rejections_out,
        )
        _mark_compile_phase("compile_filter_order_effects")

        ops = []
        extraction_cache: dict[str, UKAffectingSourceContext] = {}
        enacted_extraction_cache: dict[str, UKAffectingSourceContext] = {}
        for i, e in enumerate(replayable):
            if bool(e.metadata_only) and not allow_metadata_only_effects:
                append_metadata_only_selection_rejection(
                    lowering_rejections_out,
                    effect=e,
                )
                continue
            source_selection = _select_source_for_effect(
                effect=e,
                archive=archive,
                applicability_mode=applicability_mode,
                extraction_cache=extraction_cache,
                enacted_extraction_cache=enacted_extraction_cache,
                effect_diagnostics_out=effect_diagnostics_out,
                current_xml_loader=get_affecting_act_xml_from_archive,
                enacted_xml_loader=get_affecting_act_enacted_xml_from_archive,
                provision_extractor=extract_provision_element_from_bytes,
                source_phase_timings_out=compile_phase_timings_out,
            )
            if compile_phase_timings_out is None:
                _mark_compile_phase("compile_source_select")
            else:
                phase_t0 = time.perf_counter()
            source_required_for_replay = source_selection.source_required_for_replay
            source_context = source_selection.source_context
            el = source_selection.extracted_el
            xml_bytes = source_context.xml_bytes
            root = source_context.root

            structural_for_replay = e.is_structural_for_replay(
                applicability_mode=applicability_mode
            )
            replay_applicable = e.is_applicable_for_replay(
                applicability_mode=applicability_mode
            )
            lowering_rejection_count_before = (
                len(lowering_rejections_out) if lowering_rejections_out is not None else 0
            )
            compiled = compile_effect_to_ir_ops(
                e,
                el,
                sequence=i,
                fallback_for_missing_extracted_source=(
                    source_required_for_replay
                    and xml_bytes is None
                    and allow_metadata_backfill
                ),
                lowering_rejections_out=lowering_rejections_out,
                source_root=root,
                source_authority_layer=source_context.authority_layer,
                lower_phase_timings_out=compile_phase_timings_out,
            )
            if compile_phase_timings_out is None:
                _mark_compile_phase("compile_lower_effect")
            else:
                phase_t0 = time.perf_counter()
            compile_recorded_lowering_rejection = (
                lowering_rejections_out is not None
                and len(lowering_rejections_out) > lowering_rejection_count_before
            )
            if lowering_rejections_out is not None:
                mark_nonreplay_lowering_rejections_nonblocking(
                    e,
                    structural_for_replay=structural_for_replay,
                    applicability_mode=applicability_mode,
                    lowering_rejections=lowering_rejections_out,
                    start_index=lowering_rejection_count_before,
                )
            extracted_tag, extracted_text = _extracted_tag_and_text(el)
            source_pathology = _classify_compiled_effect_source_pathology(
                effect=e,
                extracted_tag=extracted_tag,
                extracted_text=extracted_text,
                compiled_ops=compiled,
                lowering_rejections=lowering_rejections_out,
                lowering_rejection_start_index=lowering_rejection_count_before,
                structural_for_replay=structural_for_replay,
            )
            _mark_compile_phase("compile_source_pathology")
            if lowering_rejections_out is not None:
                mark_source_pathology_nonreplay_lowering_rejections_nonblocking(
                    source_pathology=source_pathology,
                    lowering_rejections=lowering_rejections_out,
                    start_index=lowering_rejection_count_before,
                )
            append_source_pathology_classified_diagnostic(
                effect_diagnostics_out,
                effect=e,
                source_pathology=source_pathology,
                structural_for_replay=structural_for_replay,
                replay_applicable=replay_applicable,
                compiled_op_count=len(compiled),
            )

            if not compiled:
                append_no_ops_lowering_rejections(
                    e,
                    structural_for_replay=structural_for_replay,
                    lowering_rejections_out=lowering_rejections_out,
                    compile_recorded_lowering_rejection=compile_recorded_lowering_rejection,
                    applicability_mode=applicability_mode,
                )
                append_manual_compile_frontier_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    source_pathology=source_pathology,
                    extracted_tag=extracted_tag or "",
                    extracted_text=extracted_text,
                    lowering_rejections_out=lowering_rejections_out,
                    lowering_rejection_start_index=lowering_rejection_count_before,
                    compiled_op_count=0,
                    replay_applicable=replay_applicable,
                    structural_for_replay=structural_for_replay,
                )
                _mark_compile_phase("compile_filter_effect")
                continue
            source_pathology_filter_rejected = append_source_pathology_filter_lowering_rejections(
                e,
                source_pathology=source_pathology,
                structural_for_replay=structural_for_replay,
                compiled_ops=compiled,
                lowering_rejections_out=lowering_rejections_out,
            )
            append_manual_compile_frontier_diagnostic(
                effect_diagnostics_out,
                effect=e,
                source_pathology=source_pathology,
                extracted_tag=extracted_tag or "",
                extracted_text=extracted_text,
                lowering_rejections_out=lowering_rejections_out,
                lowering_rejection_start_index=lowering_rejection_count_before,
                compiled_op_count=len(compiled),
                replay_applicable=replay_applicable,
                structural_for_replay=structural_for_replay,
            )
            if source_pathology_filter_rejected:
                _mark_compile_phase("compile_filter_effect")
                continue
            should_replay_compiled = structural_for_replay or should_replay_nonstructural_ops(
                e,
                compiled,
                applicability_mode=applicability_mode,
            )
            if not should_replay_compiled:
                append_replay_applicability_filter_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    compiled_ops=compiled,
                    structural_for_replay=structural_for_replay,
                    replay_applicable=replay_applicable,
                    applicability_mode=applicability_mode,
                )
                if authority_mode == "source_text_only":
                    _apply_uk_authority_mode(
                        ops=compiled,
                        effect=e,
                        authority_mode=authority_mode,
                        replay_applicable=replay_applicable,
                        structural_for_replay=structural_for_replay,
                        diagnostics_out=authority_rejections_out,
                        rule_id="uk_effect_authority_filter_non_applicable_observed",
                        blocking=False,
                        reason=(
                            "UK source-text-only authority mode observed "
                            "non-source-text operations on a non-replay-applicable effect"
                        ),
                    )
                _mark_compile_phase("compile_filter_effect")
                continue
            if authority_mode == "source_text_only":
                compiled = _apply_uk_authority_mode(
                    ops=compiled,
                    effect=e,
                    authority_mode=authority_mode,
                    replay_applicable=replay_applicable,
                    structural_for_replay=structural_for_replay,
                    diagnostics_out=authority_rejections_out,
                )
                if not compiled:
                    _mark_compile_phase("compile_filter_effect")
                    continue
            if should_replay_compiled:
                ops.extend(compiled)
            _mark_compile_phase("compile_filter_effect")

        ops = _order_schedule_materialization_ops(ops)
        ordered_ops = _order_uk_text_patch_preimage_chains(
            ops,
            lowering_observations_out=lowering_rejections_out,
        )
        _mark_compile_phase("compile_final_order")
        return ordered_ops

    def apply_ops(
        self,
        base_ir: IRStatute,
        ops: list[LegalOperation],
        eid_map: Optional[dict[str, str]] = None,
        text_map: Optional[dict[str, str]] = None,
        allow_oracle_alignment: bool = True,
        verbose: bool = False,
        lo_ops_out: Optional[List[LegalOperation]] = None,
        adjudications_out: Optional[List[CompileAdjudication]] = None,
        oracle_alignment_events_out: Optional[list[dict[str, Any]]] = None,
    ) -> IRStatute:
        executor = UKReplayExecutor(
            base_ir,
            eid_map=eid_map if allow_oracle_alignment else None,
            text_map=text_map if allow_oracle_alignment else None,
            verbose=verbose,
            lo_ops_out=lo_ops_out,
            adjudications_out=adjudications_out,
        )
        prepared_ops = _prepare_replay_uk_ops(
            ops,
            base_ir=base_ir,
            verbose=verbose,
            adjudications_out=adjudications_out,
        )
        for op in prepared_ops.accepted_ops:
            executor.apply_op(op)
        if allow_oracle_alignment and eid_map:
            executor.ground_ids()
        if oracle_alignment_events_out is not None:
            oracle_alignment_events_out.extend(dict(event) for event in executor.oracle_alignment_events)
        return executor.statute.to_irstatute()

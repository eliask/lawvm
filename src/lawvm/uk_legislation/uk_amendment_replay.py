"""UK Amendment Replay Pipeline.

This module implements the acquisition and op-extraction layer for building
a PIT (Point-in-Time) legal graph from first principles for UK legislation —
analogous to Finland's replay frontend but without LLM dependency for the
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

from enum import Enum
import json as json
import time
from lxml import etree as ET
from pathlib import Path
from collections.abc import Sequence
from typing import Any, List, Optional

from lawvm.core.ir import (
    IRStatute,
    LegalOperation,
    OperationSource,
)
from lawvm.core.mutation_events import MutationEvent
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_grafter import _LEG_NS as _LEG_NS
from lawvm.uk_legislation.effects import (
    UKEffectRecord,
    get_affecting_act_enacted_xml_from_archive,
    get_affecting_act_xml_from_archive,
    load_effects_for_statute_from_archive,
    uk_effect_requires_affecting_source_for_replay,
)
from lawvm.uk_legislation.effect_temporal import (
    resolve_uk_effective_date_overrides_for_replay,
)
from lawvm.uk_legislation.affecting_act_commencement import (
    affecting_provision_in_force,
    affecting_provision_start_dates,
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
    EffectSourceSelection as _EffectSourceSelection,
    extracted_tag_and_text as _extracted_tag_and_text,
    select_source_for_effect as _select_source_for_effect,
    source_context_for_effect as _source_context_for_effect,
)
from lawvm.uk_legislation.lowering_records import (
    append_manual_compile_frontier_diagnostic,
    append_metadata_only_selection_rejection,
    append_no_ops_lowering_rejections,
    append_pit_date_filter_rejection,
    append_prospective_pit_commencement_observation,
    append_replay_applicability_filter_diagnostic,
    append_source_pathology_classified_diagnostic,
    append_source_pathology_filter_lowering_rejections,
    mark_nonreplay_lowering_rejections_nonblocking,
    mark_source_pathology_nonreplay_lowering_rejections_nonblocking,
)
from lawvm.uk_legislation.source_context import (
    UKAffectingSourceContext,
    evict_source_root_caches,
)
from lawvm.uk_legislation.prospective_effect_warrant import (
    prospective_effect_applied_observation,
)
from lawvm.uk_legislation.contingent_commencement_claim import (
    ContingentCommencementClaim,
    gate_contingent_repeal_at_pit,
    validate_contingent_commencement_claim,
)
from lawvm.uk_legislation.same_moment_precedence_claim import (
    SameMomentPrecedenceClaim,
)
from lawvm.uk_legislation.deixis_application_claim import (
    DeixisInApplicationClaim,
    gate_deixis_in_application_claim,
    validate_deixis_in_application_claim,
)
from lawvm.uk_legislation.appropriate_place_claim import (
    AppropriatePlaceInsertClaim,
    gate_appropriate_place_insert,
    validate_appropriate_place_claim,
)
from lawvm.uk_legislation.savings_omission_claim import (
    SavingsScopedOmissionClaim,
    gate_savings_scoped_omission_claim,
    validate_savings_scoped_omission_claim,
)
from lawvm.uk_legislation.range_to_container_claim import (
    RangeToContainerClaim,
    gate_range_to_container_claim,
    validate_range_to_container_claim,
)
from lawvm.uk_legislation.application_overlay_claim import (
    ApplicationOverlayClaim,
    gate_application_overlay_claim,
    validate_application_overlay_claim,
)
from lawvm.uk_legislation.source_feed_reconciliation_claim import (
    SourceFeedReconciliationClaim,
    gate_source_feed_reconciliation_claim,
    validate_source_feed_reconciliation_claim,
)
from lawvm.uk_legislation.ordering import (
    _order_uk_effects_for_replay,
    _order_uk_text_patch_preimage_chains,
)
from lawvm.uk_legislation.repeal_no_double_entry import (
    collect_repeal_no_double_entry_groups,
    filter_repeal_no_double_entry_ops,
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
from lawvm.uk_legislation.authority_filter import (
    _uk_op_allowed_by_authority_mode as _uk_op_allowed_by_authority_mode,
)
from lawvm.uk_legislation.commencement import commencement_eid_set as commencement_eid_set
from lawvm.uk_legislation.effects import (
    load_effects_for_statute as load_effects_for_statute,
    parse_effects_from_bytes as parse_effects_from_bytes,
    parse_effects_from_feeds as parse_effects_from_feeds,
    parse_effects_from_metadata as parse_effects_from_metadata,
)
from lawvm.uk_legislation.ordering import (
    _uk_source_provision_order_key as _uk_source_provision_order_key,
)
from lawvm.uk_legislation.provenance_notes import (
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
from lawvm.uk_legislation.provision_extractor import (
    extract_provision_element_from_bytes as extract_provision_element_from_bytes,
    _parse_ref as _parse_ref,
)
from lawvm.uk_legislation.replay_executor import replay_uk_ops as replay_uk_ops
from lawvm.uk_legislation.source_context import (
    _build_affecting_source_context as _build_affecting_source_context,
    _extract_from_affecting_source_context as _extract_from_affecting_source_context,
    _extract_from_affecting_source_context_with_observations as _extract_from_affecting_source_context_with_observations,
    _select_enacted_source_for_current_shell as _select_enacted_source_for_current_shell,
)
from lawvm.uk_legislation.substitution_metadata import (
    _repeal_tail_for_substituted_series_replacement as _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor as _retarget_substituted_series_to_replaced_anchor,
)
from lawvm.uk_legislation.target_parser import (
    _split_metadata_provisions as _split_metadata_provisions,
    _parse_affected_target as _parse_affected_target,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    _fragment_substitution as _fragment_substitution,
)
from lawvm.uk_legislation.xml_helpers import (
    _tag as _tag,
    _text_content as _text_content,
)

_UK_AMENDMENT_REPLAY_COMPAT_EXPORTS = (
    json,
    ET,
    _LEG_NS,
    uk_effect_requires_affecting_source_for_replay,
    _EffectSourceSelection,
    _source_context_for_effect,
    _uk_op_allowed_by_authority_mode,
    commencement_eid_set,
    load_effects_for_statute,
    parse_effects_from_bytes,
    parse_effects_from_feeds,
    parse_effects_from_metadata,
    _uk_source_provision_order_key,
    _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    _NOTE_FRAGMENT_SUB,
    _NOTE_METADATA_SOURCE_FALLBACK,
    _NOTE_PRECEDING_EID,
    _NOTE_REWRITE_WITNESS,
    _NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR,
    _NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR,
    _NOTE_TABLE_CELL_SELECTOR,
    _NOTE_TABLE_COLUMN_INSERT_SELECTOR,
    _NOTE_TABLE_ROW_INSERT_SELECTOR,
    _NOTE_TABLE_ROW_REPLACE_SELECTOR,
    _NOTE_TEXT_REWRITE_RULE,
    extract_provision_element_from_bytes,
    _parse_ref,
    replay_uk_ops,
    _build_affecting_source_context,
    _extract_from_affecting_source_context,
    _extract_from_affecting_source_context_with_observations,
    _select_enacted_source_for_current_shell,
    _repeal_tail_for_substituted_series_replacement,
    _retarget_substituted_series_to_replaced_anchor,
    _split_metadata_provisions,
    _parse_affected_target,
    _fragment_substitution,
    _tag,
    _text_content,
)

# ---------------------------------------------------------------------------
# Replay Pipeline
# ---------------------------------------------------------------------------


class UKDiagnosticReplayFilterMode(Enum):
    """How compile diagnostics interact with replay op filtering."""

    ENFORCE = "enforce"
    OBSERVE_ONLY = "observe_only"


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
        diagnostic_replay_filter_mode: UKDiagnosticReplayFilterMode = (
            UKDiagnosticReplayFilterMode.ENFORCE
        ),
        contingent_commencement_claims: Optional[
            "Sequence[ContingentCommencementClaim]"
        ] = None,
        same_moment_precedence_claims: Optional[
            "Sequence[SameMomentPrecedenceClaim]"
        ] = None,
        appropriate_place_claims: Optional[
            "Sequence[AppropriatePlaceInsertClaim]"
        ] = None,
        deixis_application_claims: Optional[
            "Sequence[DeixisInApplicationClaim]"
        ] = None,
        savings_omission_claims: Optional[
            "Sequence[SavingsScopedOmissionClaim]"
        ] = None,
        range_to_container_claims: Optional[
            "Sequence[RangeToContainerClaim]"
        ] = None,
        application_overlay_claims: Optional[
            "Sequence[ApplicationOverlayClaim]"
        ] = None,
        source_feed_reconciliation_claims: Optional[
            "Sequence[SourceFeedReconciliationClaim]"
        ] = None,
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

        temporal_observations: list[dict[str, Any]] = []
        effective_date_overrides = resolve_uk_effective_date_overrides_for_replay(
            effects,
            archive,
            diagnostics_out=temporal_observations,
        )
        if effect_diagnostics_out is not None:
            effect_diagnostics_out.extend(temporal_observations)
        if lowering_rejections_out is not None:
            lowering_rejections_out.extend(dict(row) for row in temporal_observations)

        # §claim_source_binding: the affecting-source extraction caches are shared
        # between the up-front claim-validation index build below and the main
        # compile loop, so the EXTRACTED affecting text a claim's source-binding
        # stage sees is the SAME surface the manual-frontier classifier and the
        # lowering loop see (the prose lives in the affecting XML — real feed
        # effects carry empty source_text/raw_text/comments). The extraction is
        # cached per affecting act, so resolving it once during the index build and
        # again in the main loop loads the XML only once.
        extraction_cache: dict[str, UKAffectingSourceContext] = {}
        enacted_extraction_cache: dict[str, UKAffectingSourceContext] = {}

        def _extracted_source_text_for_claim_effect(
            bound_effect: Optional[UKEffectRecord],
        ) -> Optional[str]:
            """Extract the affecting-source text for a claim-bound effect, or None.

            Returns the same ``extracted_tag_and_text`` text the main compile loop
            derives for the effect (the manual-frontier classifier's source
            surface), so a claim's source-binding stage binds to what the classifier
            binds. Diagnostics are suppressed here (``effect_diagnostics_out=None``):
            the main loop re-runs selection for the effect and emits the canonical
            diagnostics, keeping the default/no-claim diagnostics stream unchanged.
            Returns None when no effect is bound or no source is extractable, so the
            validator falls back to the effect attributes (synthetic fixtures).
            """
            if bound_effect is None:
                return None
            selection = _select_source_for_effect(
                effect=bound_effect,
                archive=archive,
                applicability_mode=applicability_mode,
                extraction_cache=extraction_cache,
                enacted_extraction_cache=enacted_extraction_cache,
                effect_diagnostics_out=None,
                current_xml_loader=get_affecting_act_xml_from_archive,
                enacted_xml_loader=get_affecting_act_enacted_xml_from_archive,
                provision_extractor=extract_provision_element_from_bytes,
            )
            text = _extracted_tag_and_text(selection.extracted_el).text
            return text or None

        # §contingent_commencement: build an index of VALIDATED claims by bound
        # effect_id. This is opt-in: with no claims authored the index is empty
        # and the PIT filter below is byte-unchanged. Each claim is validated
        # against the bound effect (source-snippet binding + witness) before it
        # can gate replay; an unvalidated/mismatched claim never reaches the gate.
        validated_contingent_claims: dict[str, ContingentCommencementClaim] = {}
        if contingent_commencement_claims:
            effect_by_id = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for claim in contingent_commencement_claims:
                if claim.statute_id and claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id.get(str(claim.effect_id))
                validation = validate_contingent_commencement_claim(
                    claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(validation.to_dict())
                if validation.validated:
                    validated_contingent_claims[str(claim.effect_id)] = claim

        # §appropriate_place: build an index of VALIDATED appropriate-place insert
        # claims by bound effect_id. Opt-in and symmetric with the contingent
        # index above: with no claims authored the index is empty and the lowering
        # loop below is byte-unchanged — the appropriate-place insert stays on the
        # manual frontier exactly as today. Each claim is validated against the
        # bound effect (source-snippet binding + anchor-free check) before it can
        # emit; an unvalidated/mismatched claim never reaches the gate. The target
        # list (and thus position-consistency against the live siblings) is checked
        # at emission time inside the loop, where the source element is available.
        validated_appropriate_place_claims: dict[str, AppropriatePlaceInsertClaim] = {}
        if appropriate_place_claims:
            effect_by_id_ap = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for ap_claim in appropriate_place_claims:
                if ap_claim.statute_id and ap_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_ap.get(str(ap_claim.effect_id))
                ap_validation = validate_appropriate_place_claim(
                    ap_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(ap_validation.to_dict())
                if ap_validation.validated:
                    validated_appropriate_place_claims[str(ap_claim.effect_id)] = ap_claim

        # §deixis_application (M6): build an index of VALIDATED deixis-in-
        # application claims by bound effect_id. Opt-in and symmetric with the
        # indices above: with no claims authored the index is empty and the loop
        # below emits no finding — the N4 application-by-reference-with-deixis
        # effect stays on the manual frontier byte-unchanged. Each claim is
        # validated against the bound effect (N4 deixis source-binding + the
        # cat-4 inserted-anchor resolution check applied to the applying
        # instrument) before it can emit. The gate emits ONLY a non-replayable
        # typed finding (never a text op): M6 owns the deixis-resolution half; the
        # application overlay is the deferred M5.
        validated_deixis_application_claims: dict[str, DeixisInApplicationClaim] = {}
        if deixis_application_claims:
            effect_by_id_dx = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for dx_claim in deixis_application_claims:
                if dx_claim.statute_id and dx_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_dx.get(str(dx_claim.effect_id))
                dx_validation = validate_deixis_in_application_claim(
                    dx_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(dx_validation.to_dict())
                if dx_validation.validated:
                    validated_deixis_application_claims[str(dx_claim.effect_id)] = dx_claim

        # §savings_omission: build an index of VALIDATED savings-scoped omission
        # claims by bound effect_id. Opt-in and symmetric with the indices above:
        # with no claims authored the index is empty and the loop below emits no
        # finding — the savings-qualified omission stays on the manual frontier
        # byte-unchanged. Each claim is validated against the bound effect
        # (savings-omission source-binding via the source_adjudication classifier
        # + scope-consistency) before it can emit. The gate emits ONLY a non-
        # replayable typed finding (never a text op): the safe default is under-
        # application, NEVER a silent over-omission.
        validated_savings_omission_claims: dict[str, SavingsScopedOmissionClaim] = {}
        if savings_omission_claims:
            effect_by_id_sv = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for sv_claim in savings_omission_claims:
                if sv_claim.statute_id and sv_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_sv.get(str(sv_claim.effect_id))
                sv_validation = validate_savings_scoped_omission_claim(
                    sv_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(sv_validation.to_dict())
                if sv_validation.validated:
                    validated_savings_omission_claims[str(sv_claim.effect_id)] = sv_claim

        # §range_to_container: VALIDATED range-to-container resolution claims by
        # bound effect_id, symmetric with the indices above. Opt-in; with no
        # claims authored the index is empty and the gate emits nothing, so the
        # range-to-container frontier item stays byte-unchanged. The gate emits a
        # non-replayable typed finding only (never a text op): the resolved member
        # span is recorded, base text untouched (under-application default).
        validated_range_to_container_claims: dict[str, RangeToContainerClaim] = {}
        if range_to_container_claims:
            effect_by_id_rng = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for rng_claim in range_to_container_claims:
                if rng_claim.statute_id and rng_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_rng.get(str(rng_claim.effect_id))
                rng_validation = validate_range_to_container_claim(
                    rng_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(rng_validation.to_dict())
                if rng_validation.validated:
                    validated_range_to_container_claims[str(rng_claim.effect_id)] = rng_claim

        # §application_overlay (M5): VALIDATED non-textual application/modification
        # overlay claims by bound effect_id, symmetric with the indices above.
        # Opt-in; with no claims authored the index is empty and the gate emits
        # nothing, so the application/modification frontier item stays byte-
        # unchanged. The gate emits a non-replayable typed overlay finding only
        # (never a text op): the scoped reading is recorded, base text left intact
        # (the application dimension is an overlay relation, not a coordinate —
        # under-application default).
        validated_application_overlay_claims: dict[str, ApplicationOverlayClaim] = {}
        if application_overlay_claims:
            effect_by_id_ao = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for ao_claim in application_overlay_claims:
                if ao_claim.statute_id and ao_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_ao.get(str(ao_claim.effect_id))
                ao_validation = validate_application_overlay_claim(
                    ao_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(ao_validation.to_dict())
                if ao_validation.validated:
                    validated_application_overlay_claims[str(ao_claim.effect_id)] = ao_claim

        # §source_feed_reconciliation: VALIDATED N5 source/feed target-reconciliation
        # claims by bound effect_id, symmetric with the indices above. Opt-in; with
        # no claims authored the index is empty and the gate emits nothing, so the
        # source/feed target-conflict frontier item stays byte-unchanged. The gate
        # emits a typed finding only (never a text op into ``compiled``): the
        # parent-authoritative / genuinely-ambiguous bases are non-replayable
        # findings (under-application default), and the child-locatable basis emits
        # a replayable child-target resolution for a downstream compiler — neither
        # mutates the base text here.
        validated_source_feed_reconciliation_claims: dict[
            str, SourceFeedReconciliationClaim
        ] = {}
        if source_feed_reconciliation_claims:
            effect_by_id_sfr = {
                str(e.effect_id): e for e in effects if str(e.effect_id or "")
            }
            for sfr_claim in source_feed_reconciliation_claims:
                if sfr_claim.statute_id and sfr_claim.statute_id != affected_act_id:
                    continue
                bound_effect = effect_by_id_sfr.get(str(sfr_claim.effect_id))
                sfr_validation = validate_source_feed_reconciliation_claim(
                    sfr_claim,
                    effect=bound_effect,
                    extracted_source_text=_extracted_source_text_for_claim_effect(
                        bound_effect
                    ),
                )
                if effect_diagnostics_out is not None:
                    effect_diagnostics_out.append(sfr_validation.to_dict())
                if sfr_validation.validated:
                    validated_source_feed_reconciliation_claims[
                        str(sfr_claim.effect_id)
                    ] = sfr_claim

        replayable = list(effects)
        if pit_date:
            pit_replayable: list[UKEffectRecord] = []
            affecting_xml_by_act: dict[str, Optional[bytes]] = {}
            for e in replayable:
                contingent_claim = validated_contingent_claims.get(str(e.effect_id))
                if contingent_claim is not None:
                    gate = gate_contingent_repeal_at_pit(contingent_claim, pit_date)
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(gate.to_dict())
                    if gate.applies:
                        pit_replayable.append(e)
                    continue
                if e.is_prospective_only and e.is_structural_for_replay(
                    applicability_mode=applicability_mode,
                ):
                    affecting_act_id = e.affecting_act_id
                    if affecting_act_id not in affecting_xml_by_act:
                        affecting_xml_by_act[affecting_act_id] = (
                            get_affecting_act_xml_from_archive(affecting_act_id, archive)
                        )
                    affecting_xml = affecting_xml_by_act[affecting_act_id]
                    start_dates = affecting_provision_start_dates(
                        e.affecting_provisions,
                        affecting_xml,
                    )
                    in_force = affecting_provision_in_force(
                        e.affecting_provisions,
                        affecting_xml,
                        as_of=pit_date,
                    )
                    if in_force is True:
                        append_prospective_pit_commencement_observation(
                            effect_diagnostics_out,
                            effect=e,
                            status="resolved_in_force",
                            start_dates=start_dates,
                            pit_date=pit_date,
                        )
                        pit_replayable.append(e)
                        continue
                    if in_force is False:
                        append_prospective_pit_commencement_observation(
                            effect_diagnostics_out,
                            effect=e,
                            status="resolved_future",
                            start_dates=start_dates,
                            pit_date=pit_date,
                        )
                        continue
                    append_prospective_pit_commencement_observation(
                        effect_diagnostics_out,
                        effect=e,
                        status="unresolved",
                        start_dates=start_dates,
                        pit_date=pit_date,
                    )
                effective_date = (
                    effective_date_overrides.get(e.effect_id)
                    or e.effective_date
                    or "9999-99-99"
                )
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
            effective_date_overrides=effective_date_overrides,
            diagnostics_out=effect_diagnostics_out,
            lowering_observations_out=lowering_rejections_out,
            same_moment_precedence_claims=same_moment_precedence_claims,
        )
        repeal_no_double_entry_groups = collect_repeal_no_double_entry_groups(replayable)
        _mark_compile_phase("compile_filter_order_effects")

        # §source_root_lifecycle: Build a last-occurrence index so the compile
        # loop can evict source-root contexts as soon as their affecting act's
        # final effect has been processed.  Without eviction, all 229 unique
        # affecting-act ET._Element trees for ukpga/1970/9 accumulate in memory
        # simultaneously (~2.5 GB peak RSS).  Evicting after last use reduces
        # peak to the watermark of the maximum concurrently-live roots, which
        # drops to single-digit counts in typical ordered traversal.
        # See profiling diagnosis (.tmp/uk_sensor_profile_1970_9_v2.md §memory).
        _last_effect_idx: dict[str, int] = {}
        for _j, _e_j in enumerate(replayable):
            _last_effect_idx[_e_j.affecting_act_id] = _j

        ops = []
        # NB: ``extraction_cache`` / ``enacted_extraction_cache`` are initialized
        # once above (before the claim-validation index build) and reused here, so
        # the affecting XML is loaded only once per affecting act across both passes.
        for i, e in enumerate(replayable):
            try:
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
                # §appropriate_place: a validated, bound appropriate-place claim
                # supplies the POSITION the source left to editorial judgement, so
                # the insert lowering rejected can now be emitted at the claimed
                # slot. Only fires for an effect that carries a validated claim;
                # absent a claim the index is empty and ``compiled`` is unchanged.
                ap_claim = validated_appropriate_place_claims.get(str(e.effect_id))
                if ap_claim is not None:
                    ap_gate = gate_appropriate_place_insert(
                        ap_claim,
                        sequence=i,
                        validated=True,
                        source=OperationSource(
                            statute_id=e.affecting_act_id,
                            title=e.affecting_title,
                            effective=e.effective_date or "",
                        ),
                    )
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(ap_gate.to_dict())
                    if ap_gate.emitted and ap_gate.operation is not None:
                        compiled = [*compiled, ap_gate.operation]
                # §deixis_application (M6): a validated, bound deixis-in-
                # application claim resolves the "(as inserted)" reference of an
                # N4 application-by-reference effect and emits a NON-replayable
                # typed finding to the diagnostics stream. It NEVER mutates
                # ``compiled`` (no text op): the base text is left intact, the safe
                # N4 under-application default. Absent a claim the index is empty
                # and nothing is emitted, so replay is byte-unchanged.
                dx_claim = validated_deixis_application_claims.get(str(e.effect_id))
                if dx_claim is not None:
                    dx_gate = gate_deixis_in_application_claim(dx_claim, validated=True)
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(dx_gate.to_dict())
                        if dx_gate.emitted and dx_gate.finding is not None:
                            effect_diagnostics_out.append(dx_gate.finding.to_dict())
                # §savings_omission: a validated, bound savings-scoped omission
                # claim records the saving's preserved scope and emits a NON-
                # replayable typed finding to the diagnostics stream. It NEVER
                # mutates ``compiled`` (no text op): the base text is left intact,
                # the safe under-application default — never a silent over-omission.
                # Absent a claim the index is empty and nothing is emitted, so
                # replay is byte-unchanged.
                sv_claim = validated_savings_omission_claims.get(str(e.effect_id))
                if sv_claim is not None:
                    sv_gate = gate_savings_scoped_omission_claim(sv_claim, validated=True)
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(sv_gate.to_dict())
                        if sv_gate.emitted and sv_gate.finding is not None:
                            effect_diagnostics_out.append(sv_gate.finding.to_dict())
                # §range_to_container: a validated, bound range-to-container claim
                # resolves which concrete ordered container members the source
                # range denotes and emits a NON-replayable typed finding to the
                # diagnostics stream. It NEVER mutates ``compiled`` (no text op):
                # the base text is left intact, the safe under-application default
                # for an uncertain container boundary. Absent a claim the index is
                # empty and nothing is emitted, so replay is byte-unchanged.
                rng_claim = validated_range_to_container_claims.get(str(e.effect_id))
                if rng_claim is not None:
                    rng_gate = gate_range_to_container_claim(rng_claim, validated=True)
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(rng_gate.to_dict())
                        if rng_gate.emitted and rng_gate.finding is not None:
                            effect_diagnostics_out.append(rng_gate.finding.to_dict())
                # §application_overlay (M5): a validated, bound application/
                # modification overlay claim records the scoped reading (target,
                # scope, window, kind, applying instrument) and emits a NON-
                # replayable typed overlay finding to the diagnostics stream. It
                # NEVER mutates ``compiled`` (no text op): the base text is left
                # intact, the safe under-application default — the application
                # dimension is an overlay relation, not a coordinate. Where the
                # applying provision is deictic the claim reuses M6's resolution
                # (it does not re-resolve the deixis). Absent a claim the index is
                # empty and nothing is emitted, so replay is byte-unchanged.
                ao_claim = validated_application_overlay_claims.get(str(e.effect_id))
                if ao_claim is not None:
                    ao_gate = gate_application_overlay_claim(ao_claim, validated=True)
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(ao_gate.to_dict())
                        if ao_gate.emitted and ao_gate.finding is not None:
                            effect_diagnostics_out.append(ao_gate.finding.to_dict())
                # §source_feed_reconciliation: a validated, bound N5 claim records
                # which surface (source-named child vs feed-named parent) is
                # authoritative for the conflict and emits a typed finding to the
                # diagnostics stream only. It NEVER mutates ``compiled`` here: the
                # ambiguous / parent-authoritative bases are non-replayable findings
                # (base text intact, the safe §2.1 default); the child-locatable
                # basis emits a replayable child-target resolution a downstream
                # compiler consumes — still no base-text mutation in this pass.
                # Absent a claim the index is empty and nothing is emitted, so replay
                # is byte-unchanged.
                sfr_claim = validated_source_feed_reconciliation_claims.get(
                    str(e.effect_id)
                )
                if sfr_claim is not None:
                    sfr_gate = gate_source_feed_reconciliation_claim(
                        sfr_claim, validated=True
                    )
                    if effect_diagnostics_out is not None:
                        effect_diagnostics_out.append(sfr_gate.to_dict())
                        if sfr_gate.emitted and sfr_gate.finding is not None:
                            effect_diagnostics_out.append(sfr_gate.finding.to_dict())
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
                extracted_tag_and_text = _extracted_tag_and_text(el)
                source_pathology = _classify_compiled_effect_source_pathology(
                    effect=e,
                    extracted_tag=extracted_tag_and_text.tag,
                    extracted_text=extracted_tag_and_text.text,
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

                if effect_diagnostics_out is not None and replay_applicable:
                    prospective_observation = prospective_effect_applied_observation(e)
                    if prospective_observation is not None:
                        effect_diagnostics_out.append(prospective_observation)

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
                        extracted_tag=extracted_tag_and_text.tag or "",
                        extracted_text=extracted_tag_and_text.text,
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
                if (
                    diagnostic_replay_filter_mode
                    is UKDiagnosticReplayFilterMode.OBSERVE_ONLY
                ):
                    source_pathology_filter_rejected = False
                append_manual_compile_frontier_diagnostic(
                    effect_diagnostics_out,
                    effect=e,
                    source_pathology=source_pathology,
                    extracted_tag=extracted_tag_and_text.tag or "",
                    extracted_text=extracted_tag_and_text.text,
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
                    lowering_observations_out=lowering_rejections_out,
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
            finally:
                # §source_root_lifecycle: evict affecting-act source context once
                # its last effect in the ordered sequence has been processed.
                # The finally block runs on both continue and fall-through paths,
                # so eviction fires exactly once per act at its last occurrence.
                # Re-accessed acts (non-contiguous in ordered sequence) will be
                # re-parsed from archive bytes on demand — transparent because the
                # cache-miss path in source_context_for_effect already loads XML.
                if _last_effect_idx.get(e.affecting_act_id) == i:
                    evicted_ctx = extraction_cache.pop(e.affecting_act_id, None)
                    evicted_enacted_ctx = enacted_extraction_cache.pop(
                        e.affecting_act_id, None
                    )
                    # Explicitly release module-level source-root caches so the
                    # reference cycle (parent_map → root as value, ancestor
                    # tuples → root as terminal) is broken immediately, making
                    # root eligible for reference-count GC instead of waiting for
                    # a cyclic-GC sweep.  See evict_source_root_caches docstring.
                    if evicted_ctx is not None:
                        evict_source_root_caches(evicted_ctx.root)
                    if evicted_enacted_ctx is not None:
                        evict_source_root_caches(evicted_enacted_ctx.root)

        ops = _order_schedule_materialization_ops(ops)
        ops = filter_repeal_no_double_entry_ops(
            ops,
            repeal_no_double_entry_groups,
            diagnostics_out=lowering_rejections_out,
        )
        ordered_ops = _order_uk_text_patch_preimage_chains(
            ops,
            lowering_observations_out=lowering_rejections_out,
        )
        _mark_compile_phase("compile_final_order")
        if effect_diagnostics_out is not None:
            from lawvm.uk_legislation.repeal_source_warrant import (
                collect_repeal_source_warrant_observations,
            )

            effect_diagnostics_out.extend(
                collect_repeal_source_warrant_observations(ordered_ops)
            )
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
        mutation_events_out: Optional[list[MutationEvent]] = None,
    ) -> IRStatute:
        executor = UKReplayExecutor(
            base_ir,
            eid_map=eid_map if allow_oracle_alignment else None,
            text_map=text_map if allow_oracle_alignment else None,
            verbose=verbose,
            lo_ops_out=lo_ops_out,
            adjudications_out=adjudications_out,
            mutation_events_out=mutation_events_out,
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

import datetime as dt
import logging
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from lawvm.core.stage_result import StageResult
from lxml import etree

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.compile_result import SourcePathology
from lawvm.core.compile_result import StrictProfile
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.core.coverage import CoverageClaim, CoverageGap, CoverageReport, CoverageUnit
from lawvm.core.canonical_intent import ExecutionContract, IntentKind, Move, NodeTarget, OccupancyPolicy, Relabel
from lawvm.core.elaboration_context import (
    TargetUnitKind,
    build_payload_elaboration_context,
    snapshot_replay_lookups,
    snapshot_target_context,
)
from lawvm.core.payload_elaboration import PayloadCompletenessWitness
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.op_provenance import (
    ProvenanceBag,
    RecognizerId,
    has_recognizer,
    serialized_provenance_bag,
    serialized_provenance_from_bags,
)
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.corpus_store import CorpusStore
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.johtolause.api import parse_clause, derive_features
from lawvm.finland.kumotaan import (
    _extract_kumotaan_container_refs,
    _extract_kumotaan_chapter_section_map,
    _extract_muutetaan_section_refs,
    _extract_muutetaan_chapter_section_map,
    kumotaan_recycle_guard_result,
)
from lawvm.finland.amendment_chapter_precreate import _pre_create_amendment_chapters
from lawvm.finland.apply_ops_executor import _apply_ops_to_tree_typed
from lawvm.finland.apply_payload_ops import (
    _find_amend_paragraph,
    _has_single_intro_numbered_item_list_ir,
)
from lawvm.finland.apply_runtime_support import _snapshot_op_source
from lawvm.finland.corpus import get_corpus
from lawvm.finland.frontend_observations import (
    _duplicate_frontend_target_observations,
    _scope_anchor_dependence_observations,
    _semantic_collapse_move_or_renumber_observations,
)
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.effect_lifecycle_signals import EffectLifecycleOverride
from lawvm.finland.future_repeal_prescan import (
    PreScanRepealTargetsRequest,
    PreScanRepealTargetsSinks,
    _pre_scan_repeal_targets,
)
from lawvm.finland.group_ops import stabilize_insert_order as _stabilize_insert_order
from lawvm.finland.group_plan import (
    coalesce_same_target_mixed_scope_section_groups as _coalesce_same_target_mixed_scope_section_groups_impl,
)
from lawvm.finland.johto_scope_mentions import (
    collect_johto_chapter_scope_mentions as _collect_johto_chapter_scope_mentions,
    collect_johto_insert_subsection_section_targets,
    collect_johto_mentioned_section_labels as _collect_johto_mentioned_section_labels,
)
from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops
from lawvm.finland.johtolause_supplements import (
    _supplement_item_and_moment_clause_ops,
    _supplement_jolloin_moment_renumber_ops,
    _supplement_mixed_explicit_clause_ops,
    _supplement_missing_repeals_after_item_shift_clause,
    _supplement_named_table_row_mixed_clause_ops,
    _supplement_sparse_osalta_row_omission_repeals,
    _tag_explicit_item_shift_after_repeal_hints,
    _tag_named_table_row_single_clause_ops,
)
from lawvm.finland.kumotaan import _extract_kumotaan_section_refs
from lawvm.finland.kumotaan_replay import _rewrite_kumotaan_snapshot_replaces_to_repeal
from lawvm.finland.lowering_scope_recovery import (
    allow_unscoped_live_section_retarget as _allow_unscoped_live_section_retarget,
)
from lawvm.finland.merge import (
    _is_suspicious_partial_section_replace_ir,
    _merge_letter_item_from_content_subsection_ir,
    _merge_letter_item_into_content_only_subsection_ir,
    _merge_section_with_omission_ir,
    _merge_sparse_alakohta_insert_ir,
    _merge_sparse_alakohta_replace_ir,
)
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.normalize import (
    _dedupe_fallback_ops_ir,
    _extract_insert_subsection_ops_fallback,
    _extract_replace_ops_from_muutetaan_tail,
    _extract_root_replace_ops_from_body_fallback,
    parse_ops_fallback_heuristic,
    parse_ops_fallback_heuristic_with_coverage,
)
from lawvm.finland.ops import OpType, AmendmentOp, FailedOp, ResolvedOp, _build_canonical_intent
from lawvm.finland.ops import _lo_with_path_update
from lawvm.finland.ops import get_replay_profile
from lawvm.finland.ops import ScopeConfidence, ScopeResolutionConfidence, ScopeResolutionSource
from lawvm.finland.replay_findings import (
    _apply_mutation_boundary_violation_finding,
    _serialize_apply_mutation_event,
)
from lawvm.finland.replay_horizon import (
    ReplayHorizonRequest,
    choose_replay_horizon,
    oracle_version_future_repeal_only_uses_cutoff_date as _oracle_version_future_repeal_only_uses_cutoff_date,
)
from lawvm.finland.replay_notices import reset_replay_verbose, set_replay_verbose
from lawvm.finland.replay_request import ReplayXmlRequest
from lawvm.finland.restructure_plan import (
    resolved_op_is_owned_by_restructure_plan as _resolved_op_is_owned_by_restructure_plan,
)
from lawvm.finland.scope import (
    assign_chapter_scope_from_johtolause as _assign_chapter_scope_from_johtolause,
    chapter_chunks_from_johtolause as _chapter_chunks_from_johtolause,
    find_body_section_chapter as _find_body_section_chapter,
    restrict_sec1_fallback_to_parent as _restrict_sec1_fallback_to_parent,
    retarget_duplicate_body_section_scope_from_close_live_siblings as _retarget_duplicate_body_section_scope_from_close_live_siblings,
    strip_unjustified_chapter_scope_from_unique_sections as _strip_unjustified_chapter_scope_from_unique_sections,
)
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.standalone_targets import (
    build_standalone_section_targets as _build_standalone_section_targets,
    group_shadow_pruning_foreign_scoped_descendant_section_targets as _group_shadow_pruning_foreign_scoped_descendant_section_targets,
    group_shadow_pruning_foreign_scoped_replace_section_targets as _group_shadow_pruning_foreign_scoped_replace_section_targets,
    group_shadow_pruning_foreign_scoped_replace_section_target_scopes as _group_shadow_pruning_foreign_scoped_replace_section_target_scopes,
    group_shadow_pruning_foreign_scoped_section_targets as _group_shadow_pruning_foreign_scoped_section_targets,
    group_shadow_pruning_section_targets as _group_shadow_pruning_section_targets,
)
from lawvm.finland.temporal_rewrites import (
    _rewrite_compiled_op_activation_rule_effective_for_addresses,
    _rewrite_later_effective_lo_groups,
    _rewrite_lo_op_source_effective,
)
from lawvm.finland.payload_normalize import (
    _container_pruning_is_expected_heading_only,
    _unsupported_payload_rejected_ops,
    _prune_container_payload_sections_shadowed_by_standalone_targets as _prune_container_payload_sections_shadowed_by_standalone_targets_impl,
)
from lawvm.finland.process_pipeline import process_muutoslaki as _process_muutoslaki_typed
from lawvm.finland.amendment_payload_lookup import _find_muutos_ir
from lawvm.finland.compile_group_surface import (
    BuildGroupSurfaceRequest as _BuildGroupSurfaceRequest,
    build_group_surface as _build_group_surface,
)
from lawvm.finland.compile_group_elaboration import (
    ElaborateGroupRequest as _ElaborateGroupRequest,
    _drop_payloadless_source_replace_shadowed_by_same_group_relabel,
    elaborate_group as _elaborate_group,
)
from lawvm.finland.compile_amendment import (
    _split_numbered_table_child_group_ops,
    compile_amendment_ops,
)
from lawvm.finland.compile_group import compile_group_typed as _compile_group_typed
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from tests.corpus_pin_helpers import replay_xml_for_test as replay_xml
from lawvm.finland.apply_ops_boundary import ApplyOpsRequest, ApplyOpsSinks
from lawvm.finland.compile_group_boundary import CompileGroupRequest, CompileGroupSinks
from lawvm.finland.process_request import ProcessAmendmentRequest
from lawvm.finland.process_result_builder import ProcessAmendmentSinks
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.tools.section_keys import extract_ir_sections
from lawvm.finland.frontend_compile import (
    _attach_target_version_selectors,
    _ambiguous_unscoped_additive_fallback_insert_observation,
    _reject_overbroad_section_repeals_for_deep_targets,
    _restore_heading_facet_for_mixed_scope_section_replaces,
    _enrich_ops_from_amendment_tree,
    _retarget_stale_body_scope_for_section_op,
    _extract_enacting_formula_body_replace_ops_fallback,
    normalize_and_compile_ops,
)
from lawvm.finland.fallback_op_ids import stamp_fallback_op_ids
from lawvm.finland.apply_resolved_op import FI_APPLY_RESOLVED_OP_RULE_ID
from lawvm.finland.future_repeal_prescan import (
    PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID,
    PreScanRepealDiagnostic,
)
from lawvm.finland.uncovered_body_recovery import (
    UncoveredBodyRecoveryRequest,
    UncoveredBodyRecoveryResult,
    UncoveredBodyRecoverySinks,
    recover_uncovered_body_ops,
)
from lawvm.finland.uncovered_kumotaan_recovery import (
    FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID,
    KumotaanRecoveryRequest,
    KumotaanRecoverySinks,
    _apply_uncovered_kumotaan_typed,
)
from lawvm.finland.uncovered_recovery_findings import (
    _uncovered_body_recovery_finding,
    _uncovered_body_recovery_skipped_finding,
)
from lawvm.finland.uncovered_recovery_findings import UncoveredBodyRecoveryFindingRequest
from lawvm.finland.uncovered_recovery_state import (
    FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
    UncoveredCandidateAudit,
)
from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.apply import apply_op
from lawvm.finland.constraints import _FilterCtx, _filter_ops_by_constraints, _find_muutos_node
from lawvm.finland.group_ops import append_compiled_group_ops, normalize_group_ops_for_repeal_reenact
from lawvm.finland.group_plan import GroupTargetKey
from lawvm.finland.scope import assign_scope_from_renumber_destinations
from lawvm.finland.source_pathology import build_container_replace_target_absent_pathology
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.finland.restructure_plan import StructuralTransformPlan
import lawvm.tools.inspect_amendment as inspect_amendment
from lawvm.tools.inspect_amendment import build_amendment_bundle
from lawvm.tools.trace_section import build_trace_bundle


def _prune_container_payload_sections_shadowed_by_standalone_targets(
    master: "ReplayState",
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    muutos_ir: IRNode | None,
    standalone_section_targets: set[str],
):
    lookups = snapshot_replay_lookups(master)
    return _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        build_payload_elaboration_context(
            snapshot_target_context(
                master,
                target_unit_kind,
                target_norm,
                None,
                lookups,
            ),
            lookups,
        ),
        target_unit_kind,
        target_norm,
        muutos_ir,
        standalone_section_targets,
    )


def _coalesce_same_target_mixed_scope_section_groups(
    section_groups,
    *,
    master: "ReplayState",
    muutos_tree: etree._Element,
):
    return _coalesce_same_target_mixed_scope_section_groups_impl(
        section_groups,
        master=master,
        find_body_section_chapter=lambda target_norm: _find_body_section_chapter(
            muutos_tree,
            target_norm,
        ),
    )


def _recover_uncovered_body_ops(
    state: ReplayState,
    ctx: StatuteContext,
    ops: list[AmendmentOp],
    muutos_tree: etree._Element,
    amendment_id: str,
    *,
    future_repeals: set[RepealTargetRef] | None = None,
    op_source: OperationSource | None = None,
    new_chapter_labels: set[str] | None = None,
    failed_ops_out: list[FailedOp] | None = None,
    restructure_plans_out: list[StructuralTransformPlan] | None = None,
    observations_out: list[dict[str, object]] | None = None,
    findings_out: list[Finding] | None = None,
) -> list[ResolvedOp]:
    result = recover_uncovered_body_ops(
        UncoveredBodyRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=ops,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            amendment_id=amendment_id,
            future_repeals=future_repeals,
            op_source=op_source,
            new_chapter_labels=new_chapter_labels,
        ),
        UncoveredBodyRecoverySinks(
            failed_ops_out=failed_ops_out,
            restructure_plans_out=restructure_plans_out,
            observations_out=observations_out,
            findings_out=findings_out,
        ),
    )
    return list(result.recovered_ops)


LEGACY_MOVE_CLAUSE_RESIDUE = pytest.mark.skip(
    reason="Legacy move-clause bridge residue; core keeps move-tail state out of shared carriers.",
)


@pytest.fixture(scope="module")
def amendment_bundle_2010_182_2020_766() -> dict[str, Any]:
    try:
        return build_amendment_bundle("2010/182", "2020/766", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")


@pytest.fixture(scope="module")
def amendment_bundle_2010_1396_2018_441() -> dict[str, Any]:
    return build_amendment_bundle("2010/1396", "2018/441", "legal_pit")


@pytest.fixture(scope="module")
def amendment_bundle_2013_588_2025_201() -> dict[str, Any]:
    return build_amendment_bundle("2013/588", "2025/201", mode="official_consolidation")


@pytest.fixture(scope="module")
def replay_2013_588_finlex_oracle() -> Any:
    return pinned_replay("2013/588", mode="official_consolidation", quiet=True)


@pytest.fixture(scope="module")
def replay_2004_699_finlex_oracle() -> Any:
    return pinned_replay("2004/699", mode="official_consolidation", quiet=True)


@pytest.fixture(scope="module")
def replay_2016_1227_finlex_oracle() -> Any:
    return pinned_replay("2016/1227", mode="official_consolidation", quiet=True)


@pytest.fixture(scope="module")
def replay_2005_579_finlex_oracle() -> Any:
    return pinned_replay("2005/579", mode="official_consolidation", quiet=True)


@pytest.fixture(scope="module")
def replay_2003_549_finlex_oracle() -> Any:
    return pinned_replay("2003/549", mode="official_consolidation", quiet=True, build_full_products=False)


@pytest.fixture(scope="module")
def replay_2007_1024_finlex_oracle() -> Any:
    return pinned_replay("2007/1024", mode="official_consolidation", quiet=True)


class _MapCorpus:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def read_source(self, statute_id: str) -> bytes | None:
        return self._mapping.get(statute_id)

    def read_source_staged(self, statute_id: str) -> "StageResult[bytes] | None":
        from lawvm.corpus_store import _read_with_content_witness
        from lawvm.core.stage_result import EvidenceBundle, StageResult

        witnessed = _read_with_content_witness(
            self._mapping.get(statute_id), statute_id, "amendment_source_xml"
        )
        if witnessed is None:
            return None
        data, witness = witnessed
        return StageResult(value=data, evidence=EvidenceBundle((witness,)))

    def read_locator(self, locator: str) -> bytes | None:
        return self._mapping.get(locator)


def _replay_state(ir: IRNode) -> ReplayState:
    return ReplayState(ir=ir)


def _statute_context(base_ir: IRNode) -> StatuteContext:
    return StatuteContext(
        id="0/0",
        title="",
        base_ir=base_ir,
        base_xml_bytes=b"",
    )


def _compile_group(
    master: ReplayState,
    target_unit_kind: Any,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    group_ops: list[AmendmentOp],
    standalone_section_targets: set[str],
    inserted_chapter_labels: set[str],
    muutos_tree: etree._Element,
    johto: str,
    profile: Any,
    compiled_ops_out: list[dict[str, object]] | None,
    strict_profile: StrictProfile | None,
    foreign_scoped_standalone_section_targets: set[str] | None = None,
    foreign_scoped_replace_section_targets: set[str] | None = None,
    precomputed_lookups: Any = None,
) -> PhaseResult[list[ResolvedOp]]:
    return _compile_group_typed(
        CompileGroupRequest(
            master=master,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
            standalone_section_targets=standalone_section_targets,
            inserted_chapter_labels=inserted_chapter_labels,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto=johto,
            profile=profile,
            strict_profile=strict_profile,
            foreign_scoped_standalone_section_targets=set(
                foreign_scoped_standalone_section_targets or ()
            ),
            foreign_scoped_replace_section_targets=set(
                foreign_scoped_replace_section_targets or ()
            ),
            lookups=precomputed_lookups,
        ),
        CompileGroupSinks(compiled_ops_out=compiled_ops_out),
    )


def apply_ops_to_tree(
    state: ReplayState,
    ctx: StatuteContext,
    resolved: list[ResolvedOp],
    ops: list[AmendmentOp],
    muutos_tree: etree._Element,
    johto: str,
    amendment_id: str,
    source_title: str,
    amendment_issue_date: dt.date | None,
    amendment_effective_date: dt.date | None,
    amendment_expiry_date: dt.date | None,
    replay_mode: str,
    lo_ops_out: list[LegalOperation] | None,
    failed_ops_out: list[FailedOp] | None,
    source_pathologies_out: list[SourcePathology] | None,
    strict_profile: StrictProfile | None,
    _vts_ops_enrich_done: bool,
    *,
    mutation_events_out: list[ApplyMutationEvent] | None = None,
    observed_touch_results_out: list[Any] | None = None,
) -> ReplayState:
    return _apply_ops_to_tree_typed(
        ApplyOpsRequest(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto=johto,
            amendment_id=amendment_id,
            source_title=source_title,
            amendment_issue_date=amendment_issue_date,
            amendment_effective_date=amendment_effective_date,
            amendment_expiry_date=amendment_expiry_date,
            replay_mode=cast(Any, replay_mode),
            strict_profile=strict_profile,
            vts_ops_enrich_done=_vts_ops_enrich_done,
        ),
        ApplyOpsSinks(
            lo_ops_out=lo_ops_out,
            failed_ops_out=failed_ops_out,
            source_pathologies_out=source_pathologies_out,
            mutation_events_out=mutation_events_out,
            observed_touch_results_out=observed_touch_results_out,
        ),
    )


def process_muutoslaki(
    amendment_id: str,
    state: ReplayState,
    ctx: StatuteContext,
    replay_mode: str = "official_consolidation",
    *,
    compiled_ops_out: list[dict[str, object]] | None = None,
    lo_ops_out: list[LegalOperation] | None = None,
    parent_id: str = "",
    failed_ops_out: list[FailedOp] | None = None,
    strict_profile: StrictProfile | None = None,
    chapter_seed_skip: set[Any] | None = None,
    corpus: Any = None,
    future_repeals: set[Any] | None = None,
    source_pathologies_out: list[SourcePathology] | None = None,
    elaboration_observations_out: list[dict[str, object]] | None = None,
    sparse_slot_bindings_out: list[dict[str, object]] | None = None,
    sparse_leftovers_out: list[dict[str, object]] | None = None,
    regex_recognition_coverage_out: list[Any] | None = None,
    commencement_expiry_overrides_out: list[EffectLifecycleOverride] | None = None,
    mutation_events_out: list[ApplyMutationEvent] | None = None,
    mutation_invariant_reports_out: list[Any] | None = None,
    write_audits_out: list[Any] | None = None,
    migration_events_out: list[Any] | None = None,
    prior_migration_events: Any = (),
    restructure_plans_out: list[Any] | None = None,
    processed_amendment_titles: dict[str, str] | None = None,
) -> PhaseResult[ReplayState]:
    return _process_muutoslaki_typed(
        ProcessAmendmentRequest(
            amendment_id=amendment_id,
            state=state,
            ctx=ctx,
            replay_mode=cast(Any, replay_mode),
            parent_id=parent_id,
            strict_profile=strict_profile,
            chapter_seed_skip=cast(Any, chapter_seed_skip),
            corpus=corpus,
            future_repeals=cast(Any, future_repeals),
            prior_migration_events=tuple(prior_migration_events or ()),
            processed_amendment_titles=processed_amendment_titles,
        ),
        ProcessAmendmentSinks(
            compiled_ops_out=compiled_ops_out,
            lo_ops_out=lo_ops_out,
            failed_ops_out=failed_ops_out,
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
            restructure_plans_out=restructure_plans_out,
        ),
    )


def test_uncovered_body_recovery_finding_is_native_obligation() -> None:
    finding = _uncovered_body_recovery_finding(
        UncoveredBodyRecoveryFindingRequest(
            op_id="uncovered_insert_14",
            source_statute="2001/1529",
            target_unit_kind="section",
            target_norm="14",
            target_chapter="5",
        )
    )

    assert finding is not None
    assert finding.kind == "APPLY.UNCOVERED_BODY_RECOVERY"
    assert finding.role == "obligation"
    assert finding.blocking is True
    assert finding.detail["barrier_code"] == "APPLY.UNCOVERED_BODY_RECOVERY"


def _corpus_store(mapping: dict[str, bytes]) -> CorpusStore:
    return cast(CorpusStore, _MapCorpus(mapping))


def _without_target_kind(findings: list["Finding"]) -> list["Finding"]:
    return [
        Finding(
            kind=obs.kind,
            role=obs.role,
            stage=obs.stage,
            detail={k: v for k, v in obs.detail.items() if k != "target_kind"},
            source_statute=obs.source_statute,
            blocking=obs.blocking,
        )
        for obs in findings
    ]


def test_rewrite_kumotaan_snapshot_replaces_to_repeal_ignores_child_snapshots() -> None:
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_10d_from_chapter_2a",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "2a"), ("section", "10d"))),
            payload=None,
            source=OperationSource(
                statute_id="2021/984",
                effective="2022-01-31",
                expires="2022-01-31",
            ),
        ),
        LegalOperation(
            op_id="snapshot_subsection_1_from_section_10d",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(
                path=(("chapter", "2a"), ("section", "10d"), ("subsection", "1"))
            ),
            payload=None,
            source=OperationSource(
                statute_id="2021/984",
                effective="2022-01-31",
                expires="2022-01-31",
            ),
        ),
    ]

    changed = _rewrite_kumotaan_snapshot_replaces_to_repeal(
        lo_ops,
        target_source_statute="2021/984",
        section_labels={"10d"},
        chapter_section_map={"2a": {"10d"}},
    )

    assert changed is True
    assert lo_ops[0].action is StructuralAction.REPEAL
    assert lo_ops[0].source is not None
    assert lo_ops[0].source.expires == ""
    assert lo_ops[1].action is StructuralAction.REPLACE


def test_rewrite_kumotaan_snapshot_replaces_to_repeal_clears_matching_repeal_expiry_without_allowlist() -> None:
    lo_ops = [
        LegalOperation(
            op_id="repeal_section_10d",
            sequence=0,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("chapter", "2a"), ("section", "10d"))),
            payload=None,
            source=OperationSource(
                statute_id="2099/1",
                effective="2022-01-31",
                expires="2022-01-31",
            ),
        ),
    ]

    changed = _rewrite_kumotaan_snapshot_replaces_to_repeal(
        lo_ops,
        target_source_statute="2099/1",
        section_labels={"10d"},
        chapter_section_map={"2a": {"10d"}},
    )

    assert changed is True
    assert lo_ops[0].action is StructuralAction.REPEAL
    assert lo_ops[0].source is not None
    assert lo_ops[0].source.expires == ""


def test_rewrite_kumotaan_snapshot_replaces_to_repeal_retains_unique_chapter_scope_without_zero_day_expiry() -> None:
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_10d",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "10d"),)),
            payload=None,
            source=OperationSource(
                statute_id="2021/984",
                effective="2022-01-31",
                expires="",
            ),
        ),
    ]

    changed = _rewrite_kumotaan_snapshot_replaces_to_repeal(
        lo_ops,
        target_source_statute="2021/984",
        section_labels={"10d"},
        chapter_section_map={"2a": {"10d"}},
    )

    assert changed is True
    assert lo_ops[0].action is StructuralAction.REPEAL
    assert lo_ops[0].target == LegalAddress(path=(("chapter", "2a"), ("section", "10d")))
    assert lo_ops[0].source is not None
    assert lo_ops[0].source.expires == ""


def test_bracketed_single_subsection_replace_generalizes_without_statute_allowlist() -> None:
    from lawvm.finland.apply_ir_ops import _rewrite_bracketed_single_subsection_replace_ir

    def _sub(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=label,
            children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
        )

    sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        children=(
            _sub("1", "first live subsection"),
            _sub("2", "shared prefix replacement old wording"),
            _sub("3", "third live subsection"),
            _sub("4", "fourth live subsection"),
        ),
    )
    replacement_sub = _sub("3", "shared prefix replacement new wording")
    muutos_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        children=(
            IRNode(kind=IRNodeKind.OMISSION),
            _sub("3", "payload subsection"),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    rewritten = _rewrite_bracketed_single_subsection_replace_ir(
        sec,
        replacement_sub,
        3,
        muutos_ir,
        "2099/1",
    )

    assert rewritten is not None
    rewritten_subs = [child for child in rewritten.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in rewritten_subs] == ["1", "2", "3", "4"]
    assert irnode_to_text(rewritten_subs[1]) == "third live subsection"
    assert irnode_to_text(rewritten_subs[2]) == "shared prefix replacement new wording"


def test_find_muutos_ir_relabels_sparse_omission_subsection_from_intro_number() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section>
            <num>26 §</num>
            <heading>Kytkentäkatsastus</heading>
            <hcontainer name="omission"/>
            <subsection>
              <intro><p>3. Kytkentäkatsastuksessa on esitettävä:</p></intro>
              <paragraph><num>a)</num><content><p>foo</p></content></paragraph>
            </subsection>
          </section>
        </body>
        """
    )

    got, _ = _find_muutos_ir(root, "section", "26")

    assert got is not None
    subs = [c for c in got.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["3"]


def test_find_muutos_ir_merges_real_unlabeled_adjacent_section_continuation() -> None:
    corpus = get_corpus()
    xml_bytes = corpus.read_source("1993/1472")
    assert xml_bytes is not None
    root = etree.fromstring(xml_bytes)

    got, _ = _find_muutos_ir(root, "section", "5a")

    assert got is not None
    assert got.attrs["lawvm_payload_normalization_rule"] == (
        "ELAB.UNLABELED_ADJACENT_SECTION_CONTINUATION",
    )
    subsections = [child for child in got.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    text = irnode_to_text(got)
    assert "Laskelma tulee laatia niin" in text
    assert "jatkajan puolison tulot" in text
    assert "Asiakirjat, joista laskelman keskeiset lähtötiedot ilmenevät" in text
    assert "Laskelma tulee laatia niin" in irnode_to_text(subsections[1])
    assert "jatkajan puolison tulot" in irnode_to_text(subsections[1])
    assert "Asiakirjat, joista laskelman keskeiset lähtötiedot ilmenevät" in irnode_to_text(
        subsections[2]
    )


def test_find_muutos_ir_relabels_nested_sparse_omission_subsection_from_intro_number() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section>
              <num>26 §</num>
              <heading>Kytkentäkatsastus</heading>
              <hcontainer name="omission"/>
              <subsection>
                <intro><p>3. Kytkentäkatsastuksessa on esitettävä:</p></intro>
                <paragraph><num>a)</num><content><p>foo</p></content></paragraph>
              </subsection>
            </section>
          </chapter>
        </body>
        """
    )

    got, _ = _find_muutos_ir(root, "chapter", "3")

    assert got is not None
    sec = next(c for c in got.children if c.kind is IRNodeKind.SECTION and c.label == "26")
    subs = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["3"]


def test_process_muutoslaki_ignores_preseeded_compat_sinks_when_building_findings() -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    preseeded_pathologies = [
        SourcePathology.from_scope(
            code="SCHEMA_INVALID",
            message="preseeded compatibility carrier",
            source_statute="1999/1",
            target_unit_kind="section",
            target_label="1",
        )
    ]
    preseeded_failed_ops = cast(
        list[Any],
        [
            SimpleNamespace(
                as_detail=lambda: {
                    "source_statute": "1999/1",
                    "description": "preseeded failed op",
                    "reason": "compat carrier",
                    "target_unit_kind": "section",
                    "target_section": "1",
                    "target_chapter": "",
                }
            )
        ],
    )

    result = process_muutoslaki(
        "1999/2",
        state,
        ctx,
        corpus=_corpus_store({}),
        source_pathologies_out=preseeded_pathologies,
        failed_ops_out=preseeded_failed_ops,
    )

    assert result.output is state
    assert all(finding.source_statute != "1999/1" for finding in result.findings())
    assert len(preseeded_pathologies) == 1
    assert len(preseeded_failed_ops) == 1


def test_apply_ops_to_tree_preserves_uncovered_candidate_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        b'<akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><body /></akn>'
    )
    ops = [
        AmendmentOp(
            op_id="phase2_replace_7",
            op_type=OpType.REPLACE,
            target_section="7",
            target_unit_kind="section",
            source_statute="1996/1261",
        )
    ]

    def fake_recover_uncovered_body_ops(*_args, **_kwargs):
        return UncoveredBodyRecoveryResult(
            recovered_ops=(),
            candidate_audits=(
                UncoveredCandidateAudit(
                    section="7",
                    chapter="3",
                    part="",
                    disposition="SKIP",
                    reason="johto_guard",
                ),
            ),
        )

    def fake_apply_uncovered_kumotaan_typed(request, _sinks):
        return SimpleNamespace(state=request.state)

    monkeypatch.setattr(
        "lawvm.finland.apply_supplemental_recovery.recover_uncovered_body_ops",
        fake_recover_uncovered_body_ops,
    )
    monkeypatch.setattr(
        "lawvm.finland.apply_supplemental_recovery._apply_uncovered_kumotaan_typed",
        fake_apply_uncovered_kumotaan_typed,
    )
    observations: list[dict[str, object]] = []

    _apply_ops_to_tree_typed(
        ApplyOpsRequest(
            state=state,
            ctx=ctx,
            resolved=[],
            ops=ops,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto="",
            amendment_id="1996/1261",
            source_title="",
            amendment_issue_date=None,
            amendment_effective_date=None,
            amendment_expiry_date=None,
            replay_mode=cast(Any, "legal_pit"),
            strict_profile=None,
            vts_ops_enrich_done=False,
        ),
        ApplyOpsSinks(observations_out=observations),
    )

    assert observations == [
        {
            "kind": "APPLY.UNCOVERED_BODY_CANDIDATE_AUDIT",
            "source_statute": "1996/1261",
            "detail": {
                "rule_id": FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
                "target_section": "7",
                "target_chapter": "3",
                "target_part": "",
                "disposition": "SKIP",
                "reason": "johto_guard",
            },
        }
    ]


def test_apply_ops_to_tree_records_resolved_op_apply_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        b'<akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><body /></akn>'
    )
    op = AmendmentOp(
        op_id="replace_7",
        op_type=OpType.REPLACE,
        target_section="7",
        target_unit_kind="section",
        source_statute="1996/1261",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="7", text="new text"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="7",
        target_chapter=None,
    )

    def fake_apply_op(*args, **_kwargs):
        return args[0]

    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)
    observations: list[dict[str, object]] = []

    _apply_ops_to_tree_typed(
        ApplyOpsRequest(
            state=state,
            ctx=ctx,
            resolved=[rop],
            ops=[op],
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto="muutetaan 7 §",
            amendment_id="1996/1261",
            source_title="",
            amendment_issue_date=None,
            amendment_effective_date=None,
            amendment_expiry_date=None,
            replay_mode=cast(Any, "legal_pit"),
            strict_profile=None,
            vts_ops_enrich_done=False,
        ),
        ApplyOpsSinks(observations_out=observations),
    )

    apply_audits = [
        observation
        for observation in observations
        if observation.get("kind") == "APPLY.RESOLVED_OP_AUDIT"
    ]
    assert apply_audits == [
        {
            "kind": "APPLY.RESOLVED_OP_AUDIT",
            "source_statute": "1996/1261",
            "detail": {
                "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
                "source_effective": "",
                "source_expires": "",
                "op_id": "replace_7",
                "action_type": "REPLACE",
                "description": "REPLACE 7 §",
                "target_unit_kind": "section",
                "target_norm": "7",
                "target_chapter": "",
                "target_part": "",
                "target_paragraph": "",
                "target_item": "",
                "target_special": "",
                "disposition": "APPLIED",
            },
        }
    ]


def _base_process_muutoslaki_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <lifecycle>
          <eventRef date="2026-01-01" />
        </lifecycle>
      </meta>
      <dateEntryIntoForce date="2026-01-01" />
      <formula name="enactingClause">Muutetaan 1 §.</formula>
    </akn>
    """.encode("utf-8")


def _sec1_fallback_process_muutoslaki_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <lifecycle>
          <eventRef date="2026-01-01" />
        </lifecycle>
      </meta>
      <dateEntryIntoForce date="2026-01-01" />
      <formula name="enactingClause">Ympäristöministerin esittelystä säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <content>muutetaan rakennuslain (370/1958) 3 § seuraavasti:</content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")


def _vts_skipped_process_muutoslaki_xml() -> bytes:
    return """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <meta>
        <lifecycle>
          <eventRef date="2026-01-01" />
        </lifecycle>
      </meta>
      <dateEntryIntoForce date="2026-01-01" />
      <formula name="enactingClause">Eduskunnan päätöksen mukaisesti säädetään:</formula>
    </akn>
    """.encode("utf-8")


def test_process_muutoslaki_flags_missing_temporal_coverage(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(
        *_args,
        **_kwargs,
    ) -> PhaseResult[Any]:
        return PhaseResult(
            output=(SimpleNamespace(resolved_source_statute="1996/1260"),),
            temporal_events=(),
        )

    def fake_apply_ops_to_tree_typed(request, _sinks):
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)
    mutation_events: list[ApplyMutationEvent] = []

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    findings = result.findings()
    assert any(
        finding.kind == "TIME.TRIGGER_COVERAGE_INCOMPLETE"
        for finding in findings
    )


def test_process_muutoslaki_carries_cao_violation_into_findings(monkeypatch) -> None:
    """A violation-role finding from compile_amendment_ops must survive into the
    returned PhaseResult ledger. The compile barrier projection emits these;
    dropping them at the consumer loops hides a blocking barrier from
    has_blocking — the same conservation failure class as the parse-violation
    drop at the frontend boundary.
    """
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    violation = Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="compile",
        detail={"barrier_code": "ELAB.FORCED_TEST_BARRIER", "message": "forced barrier"},
        source_statute="1996/1261",
        blocking=True,
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(
            output=(SimpleNamespace(resolved_source_statute="1996/1260"),),
            temporal_events=(),
            findings=(violation,),
        )

    def fake_apply_ops_to_tree_typed(request, _sinks):
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
    )

    carried = [
        f
        for f in result.findings()
        if f.blocking and f.detail.get("barrier_code") == "ELAB.FORCED_TEST_BARRIER"
    ]
    assert carried, "compile-rail violation was dropped before the result ledger"
    assert result.has_blocking


def test_process_muutoslaki_does_not_flag_when_temporal_coverage_matches(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(
        *_args,
        **_kwargs,
    ) -> PhaseResult[Any]:
        return PhaseResult(
            output=(SimpleNamespace(resolved_source_statute="1996/1260"),),
            temporal_events=(
                TemporalEvent(
                    event_id="1996-1260-temporal",
                    kind="commence",
                    scope=TemporalScope(target_statute="1996/1260"),
                    group_id="finland-johto:1996/1260",
                ),
            ),
        )

    def fake_apply_ops_to_tree_typed(request, _sinks):
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)
    mutation_events: list[ApplyMutationEvent] = []

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    findings = result.findings()
    assert not any(
        finding.kind == "TIME.TRIGGER_COVERAGE_INCOMPLETE"
        for finding in findings
    )


def test_process_muutoslaki_observes_chapter_seed_skip(monkeypatch) -> None:
    from lawvm.finland.process_structural_prepare import FI_CHAPTER_SEED_SKIP_RULE_ID

    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    skipped_op = AmendmentOp(
        op_id="replace_ch_7",
        op_type=OpType.REPLACE,
        target_section="7",
        target_unit_kind="chapter",
        source_statute="1996/1261",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[skipped_op])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, _sinks):
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        chapter_seed_skip={("7", "1996/1261")},
    )

    findings = result.findings()
    seed_skip = [finding for finding in findings if finding.kind == "ELAB.CHAPTER_SEED_SKIP"]
    assert len(seed_skip) == 1
    assert seed_skip[0].detail.get("rule_id") == FI_CHAPTER_SEED_SKIP_RULE_ID
    assert seed_skip[0].detail.get("family") == "ontology_normalization"
    assert seed_skip[0].detail.get("phase") == "process_muutoslaki.structural_prepare"
    assert seed_skip[0].detail.get("strict_disposition") == "inherit_chapter_seed_repair"
    assert seed_skip[0].detail.get("quirks_disposition") == "suppress_duplicate_apply"
    assert seed_skip[0].detail.get("dropped_count") == 1
    assert seed_skip[0].detail.get("seeded_chapters") == ("7",)
    assert seed_skip[0].detail.get("dropped_ops") == (skipped_op.description(),)
    assert seed_skip[0].detail.get("dropped_op_records") == (
        {
            "op_id": "replace_ch_7",
            "op_type": "REPLACE",
            "target_unit_kind": "chapter",
            "target_section": "7",
            "target_chapter": None,
            "target_part": None,
            "description": skipped_op.description(),
            "source_statute": "1996/1261",
            "witness_rule_id": None,
        },
    )


def test_process_muutoslaki_observes_sec1_pre_routing_fallback(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = StatuteContext(
        id="1958/370",
        title="Rakennuslaki",
        base_ir=state.ir,
        base_xml_bytes=b"",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, _sinks):
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1993/949",
        state,
        ctx,
        corpus=_corpus_store({"1993/949": _sec1_fallback_process_muutoslaki_xml()}),
        parent_id="1958/370",
    )

    findings = result.findings()
    sec1 = [finding for finding in findings if finding.kind == "FI.PREAMBLE_BODY_PRE_ROUTING_FALLBACK"]
    assert len(sec1) == 1
    assert sec1[0].role == "obligation"
    assert sec1[0].blocking is True
    assert sec1[0].detail.get("fallback_stage") == "pre_routing"
    assert sec1[0].detail.get("fallback_applied") is True
    assert sec1[0].detail.get("original_johtolause") == "Ympäristöministerin esittelystä säädetään:"
    assert "rakennuslain (370/1958) 3 §" in str(sec1[0].detail.get("sec1_fallback_text"))


def test_process_muutoslaki_preserves_source_pathologies_from_uncovered_apply(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    recovered_rop = ResolvedOp.from_amendment_op(
        AmendmentOp(
            op_id="uncovered_replace_7",
            op_type=OpType.REPLACE,
            target_section="7",
            target_unit_kind="section",
            source_statute="1996/1261",
            _stamped_recognizers=frozenset({RecognizerId.UNCOVERED_BODY}),
        ),
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="7"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="7",
        target_chapter=None,
    )
    phase2_op = AmendmentOp(
        op_id="phase2_replace_7",
        op_type=OpType.REPLACE,
        target_section="7",
        target_unit_kind="section",
        source_statute="1996/1261",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[phase2_op])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_recover_uncovered_body_ops(*_args, **_kwargs):
        return UncoveredBodyRecoveryResult(
            recovered_ops=(recovered_rop,),
            candidate_audits=(),
        )

    def fake_apply_op(state_arg, *_args, source_pathologies_out=None, **_kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

        assert source_pathologies_out is not None
        source_pathologies_out.append(
            build_container_replace_target_absent_pathology(
                source_statute="1996/1261",
                target_unit_kind="section",
                target_section="7",
                has_payload=False,
            )
        )
        mutation_events_out = _kwargs.get("mutation_events_out")
        assert mutation_events_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="skipped_tree_touch",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="skipped",
                consumed_paths=((("section", "7"),),),
            )
        )
        return state_arg

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr(
        "lawvm.finland.apply_supplemental_recovery.recover_uncovered_body_ops",
        fake_recover_uncovered_body_ops,
    )
    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
    )

    findings = result.findings()
    source_pathologies = [finding for finding in findings if finding.kind == "ELAB.SOURCE_PATHOLOGY"]
    assert len(source_pathologies) == 1
    assert source_pathologies[0].detail.get("code") == "CONTAINER_REPLACE_TARGET_ABSENT"


def test_process_muutoslaki_projects_apply_mutation_findings_from_typed_invariant_reports(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, sinks):
        from lawvm.finland.apply_events import ApplyMutationEvent

        mutation_events_out = sinks.mutation_events_out
        assert mutation_events_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="skipped_tree_touch",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="skipped",
                consumed_paths=((("section", "7"),),),
            )
        )
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    replay_boundary_findings = [
        finding for finding in result.findings() if finding.kind == "REPLAY_SKIPPED_OP_MUTATED_TREE"
    ]
    assert len(replay_boundary_findings) == 1
    assert replay_boundary_findings[0].detail.get("op_id") == "skipped_tree_touch"
    assert replay_boundary_findings[0].detail.get("path_set_invariant_holds") is True


def test_process_muutoslaki_projects_governed_apply_fallback_findings(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, sinks):
        mutation_events_out = sinks.mutation_events_out
        assert mutation_events_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="op_1",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="skipped",
                resolved_target_path=(("section", "35"),),
                used_fallback_tags=("APPLY.LEGACY_DISPATCH_FALLBACK", "missing_canonical_intent"),
                failure_reason="ResolvedOp reached apply without CanonicalIntent",
                reason_code="missing_canonical_intent",
            )
        )
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    fallback_findings = [
        finding for finding in result.findings() if finding.kind == "APPLY.LEGACY_DISPATCH_FALLBACK"
    ]
    assert len(fallback_findings) == 1
    assert fallback_findings[0].detail.get("op_id") == "op_1"
    assert fallback_findings[0].detail.get("reason_code") == "missing_canonical_intent"


def test_process_muutoslaki_projects_scope_confidence_global_fallback_as_apply_fallback_not_source_pathology(
    monkeypatch,
) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, sinks):
        mutation_events_out = sinks.mutation_events_out
        source_pathologies_out = sinks.source_pathologies_out
        assert mutation_events_out is not None
        assert source_pathologies_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="op_scope",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="applied",
                resolved_target_path=(("chapter", "6"), ("section", "23")),
                used_fallback_tags=("APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK", "live_unique_global_fallback"),
                reason_code="live_unique_global_fallback",
            )
        )
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    fallback_findings = [
        finding
        for finding in result.findings()
        if finding.kind == "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK"
    ]
    assert len(fallback_findings) == 1
    assert fallback_findings[0].detail.get("reason_code") == "live_unique_global_fallback"
    assert not any(
        finding.kind == "APPLY.SOURCE_PATHOLOGY_DETECTED"
        and finding.detail.get("code") == "SCOPE_CONFIDENCE_GLOBAL_FALLBACK"
        for finding in result.findings()
    )


def test_process_muutoslaki_projects_same_wave_migration_rebase_apply_fallback(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, sinks):
        mutation_events_out = sinks.mutation_events_out
        assert mutation_events_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="op_migrated",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="applied",
                resolved_target_path=(("chapter", "7"), ("section", "61")),
                used_fallback_tags=(
                    "APPLY.SAME_WAVE_MIGRATION_REBASE",
                    "follow_same_wave_migration",
                ),
                reason_code="follow_same_wave_migration",
            )
        )
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    migration_findings = [
        finding
        for finding in result.findings()
        if finding.kind == "APPLY.SAME_WAVE_MIGRATION_REBASE"
    ]
    assert len(migration_findings) == 1
    assert migration_findings[0].role == "observation"
    assert migration_findings[0].blocking is False
    assert migration_findings[0].detail.get("reason_code") == "follow_same_wave_migration"
    assert migration_findings[0].detail.get("resolved_target_path") == (
        ("chapter", "7"),
        ("section", "61"),
    )


def test_process_muutoslaki_projects_resolver_binding_contract_error(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    mutation_events: list[ApplyMutationEvent] = []

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult[Any]:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree_typed(request, sinks):
        mutation_events_out = sinks.mutation_events_out
        assert mutation_events_out is not None
        mutation_events_out.append(
            ApplyMutationEvent(
                op_id="op_binding",
                source_statute="1996/1261",
                action="replace",
                helper="section_resolver_binding",
                outcome="skipped",
                resolved_target_path=(("section", "35"),),
                used_fallback_tags=(
                    "APPLY.RESOLVER_BINDING_CONTRACT_ERROR",
                    "resolver_binding_contract_error",
                ),
                failure_reason="synthetic binding contract break",
                reason_code="resolver_binding_contract_error",
            )
        )
        return request.state

    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", fake_apply_ops_to_tree_typed)

    result = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        mutation_events_out=mutation_events,
    )

    binding_findings = [
        finding
        for finding in result.findings()
        if finding.kind == "APPLY.RESOLVER_BINDING_CONTRACT_ERROR"
    ]
    assert len(binding_findings) == 1
    assert binding_findings[0].role == "observation"
    assert binding_findings[0].blocking is False
    assert binding_findings[0].detail.get("helper") == "section_resolver_binding"
    assert binding_findings[0].detail.get("reason_code") == "resolver_binding_contract_error"
    assert "synthetic binding contract break" in str(binding_findings[0].detail.get("failure_reason"))
    assert binding_findings[0].detail.get("resolved_target_path") == (("section", "35"),)


def test_replay_xml_projects_apply_mutation_boundary_violations(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    replay_meta: dict[str, object] = {}
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, signal_buffers=None, **_kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

        assert signal_buffers is not None
        signal_buffers.mutation_events.append(
            ApplyMutationEvent(
                op_id="skipped_tree_touch",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="skipped",
                consumed_paths=((("section", "7"),),),
            )
        )
        return state

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
    monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)

    result = replay_xml(
        "1996/1261",
        mode="legal_pit",
        replay_meta_out=replay_meta,
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        quiet=True,
        build_full_products=False,
    )

    assert replay_meta["apply_mutation_boundary_violations"] == [
        "REPLAY_SKIPPED_OP_MUTATED_TREE op_id=skipped_tree_touch helper=apply_op touched=1",
    ]
    assert replay_meta["apply_mutation_invariant_reports"] == [
        {
            "op_id": "skipped_tree_touch",
            "helper": "apply_op",
            "outcome": "skipped",
            "touched_paths": ((("section", "7"),),),
            "changed_paths": ((("section", "7"),),),
            "allowed_roots": (),
            "allowed_effect_region_paths": (),
            "declared_allowance_paths": (),
            "declared_recovery_paths": (),
            "declared_recovery_rule_ids": (),
            "declared_migration_paths": (),
            "declared_migration_rule_ids": (),
            "permitted_paths": (),
            "covered_changed_paths": (),
            "unexplained_changed_paths": (),
            "allowed_non_target_paths": (),
            "out_of_scope_paths": (),
            "matched_allowance_rule_ids": (),
            "path_set_invariant_holds": True,
            "results": (
                {
                    "code": "REPLAY_SKIPPED_OP_MUTATED_TREE",
                    "op_id": "skipped_tree_touch",
                    "helper": "apply_op",
                    "touched_count": 1,
                    "allowed_roots": (),
                    "out_of_scope_paths": (),
                    "allowed_paths": (),
                    "matched_allowance_rule_ids": (),
                },
            ),
        }
    ]
    replay_boundary_findings = [finding for finding in result.findings if finding.kind == "REPLAY_SKIPPED_OP_MUTATED_TREE"]
    assert len(replay_boundary_findings) == 1
    assert replay_boundary_findings[0].detail.get("op_id") == "skipped_tree_touch"
    assert replay_boundary_findings[0].detail.get("path_set_invariant_holds") is True
    assert replay_boundary_findings[0].detail.get("declared_recovery_rule_ids") == ()


def test_replay_xml_projects_legacy_apply_mutation_boundary_findings_without_meta(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, signal_buffers=None, **_kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

        assert signal_buffers is not None
        signal_buffers.mutation_events.append(
            ApplyMutationEvent(
                op_id="skipped_tree_touch",
                source_statute="1996/1261",
                action="replace",
                helper="apply_op",
                outcome="skipped",
                consumed_paths=((("section", "7"),),),
            )
        )
        return state

    monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
    monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)

    result = replay_xml(
        "1996/1261",
        mode="legal_pit",
        corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
        quiet=True,
        build_full_products=False,
    )

    replay_boundary_findings = [finding for finding in result.findings if finding.kind == "REPLAY_SKIPPED_OP_MUTATED_TREE"]
    assert len(replay_boundary_findings) == 1
    assert replay_boundary_findings[0].role == "violation"
    assert replay_boundary_findings[0].blocking is True
    assert replay_boundary_findings[0].detail.items() >= {
        "message": "Apply mutation boundary accounting violated.",
        "violation": "REPLAY_SKIPPED_OP_MUTATED_TREE op_id=skipped_tree_touch helper=apply_op touched=1",
        "barrier_code": "REPLAY_SKIPPED_OP_MUTATED_TREE",
    }.items()


def test_apply_mutation_boundary_violation_helper_emits_native_kind() -> None:
    finding = _apply_mutation_boundary_violation_finding(
        violation="REPLAY_SKIPPED_OP_MUTATED_TREE op_id=skipped_tree_touch helper=apply_op touched=1",
        source_statute="1996/1261",
    )

    assert finding.kind == "REPLAY_SKIPPED_OP_MUTATED_TREE"
    assert finding.role == "violation"
    assert finding.blocking is True
    assert finding.source_statute == "1996/1261"
    assert finding.detail.get("barrier_code") == "REPLAY_SKIPPED_OP_MUTATED_TREE"


def test_serialize_apply_mutation_event_omits_empty_declared_allowances() -> None:
    from lawvm.finland.apply_events import ApplyMutationEvent

    payload = _serialize_apply_mutation_event(
        ApplyMutationEvent(
            op_id="op-1",
            source_statute="2024/1",
            action="replace",
            helper="apply_op",
            outcome="applied",
        )
    )

    assert "declared_allowances" not in payload


def test_replay_xml_projects_base_tail_prose_absorb_fact() -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            source_normalization_facts=(
                SimpleNamespace(
                    kind_value="base_tail_prose_absorb",
                    path=("body:?", "section:17", "subsection:1", "paragraph:2"),
                    before="2) on laiminlyönyt tehtävänsä toistuvasti.",
                    after=(
                        "2) on laiminlyönyt tehtävänsä toistuvasti. "
                        "Eroamispäätös on tehtävä kirjallisesti."
                    ),
                    basis_value="tail_prose_peer",
                    confidence=1.0,
                    explanation="Absorb tail prose peer as wrap-up on preceding item.",
                ),
            ),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)
        result = replay_xml(
            "1996/1261",
            mode="legal_pit",
            corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
            quiet=True,
            build_full_products=False,
        )
    finally:
        monkeypatch.undo()

    findings = [finding for finding in result.findings if finding.kind == "BASE_TAIL_PROSE_ABSORB"]
    assert len(findings) == 1
    assert findings[0].role == "observation"
    assert findings[0].source_statute == "1996/1261"
    assert findings[0].detail.get("basis") == "tail_prose_peer"
    assert findings[0].detail.get("path") == ("body:?", "section:17", "subsection:1", "paragraph:2")
    assert "wrap-up" in str(findings[0].detail.get("explanation", "")).lower()


def test_replay_xml_projects_base_num_in_intro_normalization_facts() -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            source_normalization_facts=(
                SimpleNamespace(
                    kind_value="base_num_in_intro_recovered",
                    path=("body:?", "section:5", "subsection:1"),
                    before="unnumbered paragraph with leading token '2'",
                    after="recovered as numbered kohta label='2'",
                    basis_value="profile_invalid",
                    confidence=0.94,
                    explanation="Lift the leading token into a synthetic NUM child.",
                ),
                SimpleNamespace(
                    kind_value="base_num_in_intro_mismatch",
                    path=("body:?", "section:6", "subsection:1"),
                    before="unnumbered paragraph with leading token '5'",
                    after="(skipped: candidate does not fit surrounding numbered sequence)",
                    basis_value="profile_invalid",
                    confidence=0.85,
                    explanation="Recovery would require inventing a label, so the peer was left unchanged.",
                ),
            ),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)
        result = replay_xml(
            "1996/1261",
            mode="legal_pit",
            corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
            quiet=True,
            build_full_products=False,
        )
    finally:
        monkeypatch.undo()

    recovered = [finding for finding in result.findings if finding.kind == "BASE_NUM_IN_INTRO_RECOVERED"]
    mismatch = [finding for finding in result.findings if finding.kind == "BASE_NUM_IN_INTRO_MISMATCH"]
    assert len(recovered) == 1
    assert len(mismatch) == 1
    assert recovered[0].detail.get("basis") == "profile_invalid"
    assert recovered[0].detail.get("path") == ("body:?", "section:5", "subsection:1")
    assert recovered[0].role == "observation"
    assert mismatch[0].detail.get("basis") == "profile_invalid"
    assert mismatch[0].detail.get("path") == ("body:?", "section:6", "subsection:1")
    assert mismatch[0].role == "observation"
    assert "inventing a label" in str(mismatch[0].detail.get("explanation", "")).lower()


def test_replay_xml_projects_shape_rewrite_normalization_facts() -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            source_normalization_facts=(
                SimpleNamespace(
                    kind_value="suspicious_shape",
                    path=("body:?", "section:3", "subsection:9"),
                    before="section-scoped subsection with item-style num '9)'",
                    after="kept as subsection to avoid illegal section -> paragraph edge",
                    basis_value="profile_invalid",
                    confidence=0.93,
                    explanation="Preserve the suspicious shape and emit a typed witness instead.",
                ),
                SimpleNamespace(
                    kind_value="tag_reclassify",
                    path=("body:?", "section:5", "subsection:9"),
                    before="subsection with item-style num '9)'",
                    after="paragraph (kohta) with subparagraph (alakohta) children",
                    basis_value="impossible_numbering",
                    confidence=0.97,
                    explanation="Mislabelled kohta reclassified into the legal Finland IR shape.",
                ),
                SimpleNamespace(
                    kind_value="cross_heading_hoist",
                    path=("body:?", "chapter:2"),
                    before="crossHeading sibling 'Yleiset säännökset' before chapter:2",
                    after="heading attached to chapter:2",
                    basis_value="monotonic_local_repair",
                    confidence=0.98,
                    explanation="Hoist the standalone crossHeading into the following structural node.",
                ),
                    SimpleNamespace(
                        kind_value="base_duplicate_sibling_drop",
                        path=("body:?", "section:?"),
                        before="duplicate label 4 at index 7",
                        after="(dropped, first occurrence at index 5)",
                    basis_value="monotonic_local_repair",
                    confidence=0.95,
                    explanation="Drop the later duplicate-labelled sibling and keep the first occurrence.",
                ),
            ),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)
        result = replay_xml(
            "1996/1261",
            mode="legal_pit",
            corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
            quiet=True,
            build_full_products=False,
        )
    finally:
        monkeypatch.undo()

    by_kind = {finding.kind: finding for finding in result.findings}
    assert by_kind["BASE_SUSPICIOUS_SHAPE"].detail.get("basis") == "profile_invalid"
    assert by_kind["BASE_TAG_RECLASSIFY"].detail.get("basis") == "impossible_numbering"
    assert by_kind["BASE_CROSS_HEADING_HOIST"].detail.get("path") == ("body:?", "chapter:2")
    assert by_kind["BASE_DUPLICATE_SIBLING_DROP"].detail.get("path") == ("body:?", "section:?")
    assert by_kind["BASE_DUPLICATE_SIBLING_DROP"].role == "observation"


def test_replay_xml_projects_editorial_and_numbering_family_facts() -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    plan = SimpleNamespace(
        ctx=SimpleNamespace(
            id="1996/1261",
            title="Test title",
            base_observations=(),
            source_normalization_facts=(
                SimpleNamespace(
                    kind_value="editorial_strip",
                    path=("body:?", "section:4", "content:?"),
                    before="image block child",
                    after="(removed)",
                    basis_value="editorial_only",
                    confidence=1.0,
                    explanation="Strip editorial image block from legal source tree.",
                ),
                SimpleNamespace(
                    kind_value="numbering_repair",
                    path=("body:?", "section:8"),
                    before="1, 2, 4, 5",
                    after="gap witness preserved between 2 and 4",
                    basis_value="monotonic_local_repair",
                    confidence=0.96,
                    explanation="Numbering anomaly preserved with explicit repair witness.",
                ),
                SimpleNamespace(
                    kind_value="base_digit_reset_split",
                    path=("body:?", "section:9", "subsection:1", "paragraph:4"),
                    before="digit-labelled subparagraph 5 after lettered subparagraphs",
                    after="split into sibling paragraph 5 with trailing lettered subparagraphs",
                    basis_value="monotonic_local_repair",
                    confidence=0.96,
                    explanation="Digit reset split into a new sibling paragraph.",
                ),
                SimpleNamespace(
                    kind_value="base_duplicate_tail_split",
                    path=("body:?", "section:11", "subsection:3"),
                    before="subsection 3 ends with duplicated paragraph label 2 carrying trailing prose",
                    after="trailing prose lifted into new subsection 4",
                    basis_value="monotonic_local_repair",
                    confidence=0.98,
                    explanation="Trailing duplicate list prose lifted into a new subsection.",
                ),
            ),
            base_xml_bytes=_base_process_muutoslaki_xml(),
            base_ir=state.ir,
        ),
        amendment_ids=["1996/1261"],
        amendment_records=[],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
        amendment_selection_residuals=(),
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.replay_entrypoint.execute_replay_plan", fake_execute_replay_plan)
        result = replay_xml(
            "1996/1261",
            mode="legal_pit",
            corpus=_corpus_store({"1996/1261": _base_process_muutoslaki_xml()}),
            quiet=True,
            build_full_products=False,
        )
    finally:
        monkeypatch.undo()

    editorial = [finding for finding in result.findings if finding.kind == "BASE_EDITORIAL_STRIP"]
    numbering = [finding for finding in result.findings if finding.kind == "BASE_NUMBERING_REPAIR"]
    digit_reset = [finding for finding in result.findings if finding.kind == "BASE_DIGIT_RESET_SPLIT"]
    duplicate_tail = [finding for finding in result.findings if finding.kind == "BASE_DUPLICATE_TAIL_SPLIT"]
    assert len(editorial) == 1
    assert len(numbering) == 1
    assert len(digit_reset) == 1
    assert len(duplicate_tail) == 1
    assert editorial[0].detail.get("basis") == "editorial_only"
    assert editorial[0].detail.get("path") == ("body:?", "section:4", "content:?")
    assert editorial[0].role == "observation"
    assert numbering[0].detail.get("basis") == "monotonic_local_repair"
    assert numbering[0].detail.get("path") == ("body:?", "section:8")
    assert numbering[0].role == "observation"
    assert digit_reset[0].detail.get("basis") == "monotonic_local_repair"
    assert digit_reset[0].detail.get("path") == ("body:?", "section:9", "subsection:1", "paragraph:4")
    assert digit_reset[0].role == "observation"
    assert duplicate_tail[0].detail.get("basis") == "monotonic_local_repair"
    assert duplicate_tail[0].detail.get("path") == ("body:?", "section:11", "subsection:3")
    assert duplicate_tail[0].role == "observation"


def test_find_muutos_node_does_not_singleton_fallback_for_wrong_chapter() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>14 §</num></section>
          </chapter>
        </body>
        """
    )

    assert _find_muutos_node(root, "chapter", "4") is None


def test_find_muutos_node_keeps_explicit_part_scope_for_chapter_targets() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>I osa</num>
            <chapter>
              <num>2 luku</num>
              <heading>Wrong chapter</heading>
              <section><num>1 §</num></section>
            </chapter>
          </part>
          <part>
            <num>V osa</num>
            <chapter>
              <num>2 luku</num>
              <heading>Right chapter</heading>
              <section><num>19 §</num></section>
            </chapter>
          </part>
        </body>
        """
    )

    chapter = _find_muutos_node(root, "chapter", "2", target_part="V")

    assert chapter is not None
    assert chapter.findtext("{*}heading") == "Right chapter"
    assert [child.findtext("{*}num") for child in chapter.findall("./{*}section")] == ["19 §"]


def test_find_muutos_node_keeps_crossheading_part_scope_for_chapter_targets() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <hcontainer name="statuteProvisionsWrapper">
            <section><num>6 §</num></section>
            <crossHeading>V OSA</crossHeading>
            <crossHeading>Kansainvälisen yksityisoikeuden alaan kuuluvat säännökset</crossHeading>
            <chapter>
              <num>2 luku</num>
              <heading>Right chapter</heading>
              <section><num>115 §</num></section>
            </chapter>
          </hcontainer>
        </body>
        """
    )

    chapter = _find_muutos_node(root, "chapter", "2", target_part="V")

    assert chapter is not None
    assert chapter.findtext("{*}heading") == "Right chapter"
    assert [child.findtext("{*}num") for child in chapter.findall("./{*}section")] == ["115 §"]


def test_find_muutos_node_synthesizes_part_from_crossheading_wrapper() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <hcontainer name="statuteProvisionsWrapper">
            <section><num>6 §</num></section>
            <crossHeading>V OSA</crossHeading>
            <crossHeading>Kansainvälisen yksityisoikeuden alaan kuuluvat säännökset</crossHeading>
            <chapter>
              <num>1 luku</num>
              <section><num>108 §</num></section>
            </chapter>
          </hcontainer>
        </body>
        """
    )

    part = _find_muutos_node(root, "part", "V")

    assert part is not None
    assert part.findtext("{*}num") == "V OSA"
    assert part.findtext("{*}heading") == "Kansainvälisen yksityisoikeuden alaan kuuluvat säännökset"
    assert [child.findtext("{*}num") for child in part.findall("./{*}chapter")] == ["1 luku"]


def test_find_muutos_node_does_not_singleton_fallback_for_wrong_explicit_section() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section>
            <num>9 a §</num>
            <content><p>foreign payload</p></content>
          </section>
        </body>
        """
    )

    assert _find_muutos_node(root, "section", "4") is None


def test_find_muutos_node_does_not_global_fallback_when_scoped_chapter_lacks_section() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>2 luku</num>
            <section><num>5 §</num></section>
          </chapter>
          <chapter>
            <num>18 luku</num>
            <section><num>3 §</num></section>
          </chapter>
        </body>
        """
    )

    assert _find_muutos_node(root, "section", "5", target_chapter="18") is None


def test_build_group_surface_does_not_use_unscoped_unique_section_for_carry_forward_scope() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>18 luku</num>
            <section>
              <num>159 §</num>
              <subsection><num>4 mom.</num><content><p>payload</p></content></subsection>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_159_4",
        op_type=OpType.INSERT,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_paragraph=4,
        scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
        source_statute="2019/371",
    )

    result = _build_group_surface(
        _BuildGroupSurfaceRequest(
            group_ops=[op],
            target_unit_kind="section",
            target_norm="159",
            target_chapter="2",
            target_part="III",
            source_model=AmendmentSourceModel.from_tree(root),
        )
    )

    assert result.output.body_ir is None
    missing = [f for f in result.findings() if f.kind == "ELAB.MISSING_PAYLOAD_SURFACE"]
    assert len(missing) == 1
    assert missing[0].detail["target_norm"] == "159"


def test_build_group_surface_does_not_drop_part_for_grouped_part_scope() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>I osa</num>
            <chapter>
              <num>2 luku</num>
              <section>
                <num>159 §</num>
                <subsection><num>4 mom.</num><content><p>payload</p></content></subsection>
              </section>
            </chapter>
          </part>
          <part>
            <num>III osa</num>
            <chapter>
              <num>18 luku</num>
              <section>
                <num>159 §</num>
                <subsection><num>4 mom.</num><content><p>payload</p></content></subsection>
              </section>
            </chapter>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_159_4",
        op_type=OpType.INSERT,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_paragraph=4,
        scope_provenance_tags=("grouped_part_scope",),
        source_statute="2019/371",
    )

    result = _build_group_surface(
        _BuildGroupSurfaceRequest(
            group_ops=[op],
            target_unit_kind="section",
            target_norm="159",
            target_chapter="2",
            target_part="III",
            source_model=AmendmentSourceModel.from_tree(root),
        )
    )

    assert result.output.body_ir is None


def test_allow_unscoped_live_section_retarget_requires_carry_forward_scope() -> None:
    explicit_scoped = AmendmentOp(
        op_id="replace_159",
        op_type=OpType.REPLACE,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_from_preamble",),
        source_statute="2019/371",
    )
    carry_forward_scoped = AmendmentOp(
        op_id="replace_159_cf",
        op_type=OpType.REPLACE,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_carry_forward",),
        source_statute="2019/371",
    )
    carry_forward_tag_with_explicit_carrier = AmendmentOp(
        op_id="replace_159_cf_explicit",
        op_type=OpType.REPLACE,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_carry_forward",),
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="2",
        ),
        source_statute="2019/371",
    )

    assert not _allow_unscoped_live_section_retarget([explicit_scoped])
    assert _allow_unscoped_live_section_retarget([carry_forward_scoped]) == "carry_forward"
    # An op whose scope_confidence resolves to explicit_chunk also allows retarget
    # (the explicit_chunk confidence overrides the carry_forward tag).
    assert _allow_unscoped_live_section_retarget([carry_forward_tag_with_explicit_carrier]) == "explicit_chunk"


def test_compile_group_emits_carry_forward_live_section_retarget_witness() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *chapters: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("III", _chapter("18", _section("159", "live 159"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>III osa</num>
            <chapter>
              <num>18 luku</num>
              <section>
                <num>159 §</num>
                <content><p>payload</p></content>
              </section>
            </chapter>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_159_cf",
        op_type=OpType.REPLACE,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
        source_statute="2019/371",
        lo=LegalOperation(
            op_id="replace_159_cf",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "159"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "159",
        "2",
        "III",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "18"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "18"
    assert rop.op.lo is not None
    assert "body_chapter_retargeted_from:2" in rop.op.lo.provenance_tags
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert len(retarget) == 1
    assert retarget[0].detail["target_chapter"] == "2"
    assert retarget[0].detail["resolved_live_chapter"] == "18"


def test_retarget_duplicate_body_section_scope_from_close_live_siblings_uses_neighbor_consensus() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("2a", _section("18", "old duplicate")),
                _chapter(
                    "4",
                    _section("16", "live 16"),
                    _section("17", "live 17"),
                    _section("18", "live 18"),
                    _section("19", "live 19"),
                    _section("20", "live 20"),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
            <section><num>17 §</num><content><p>payload 17</p></content></section>
            <section><num>18 §</num><content><p>payload 18</p></content></section>
            <section><num>19 §</num><content><p>payload 19</p></content></section>
            <section><num>20 §</num><content><p>payload 20</p></content></section>
          </chapter>
        </body>
        """
    )

    retargeted = _retarget_duplicate_body_section_scope_from_close_live_siblings(
        muutos_tree=muutos_tree,
        section_norm="18",
        body_chapter="3",
        body_part=None,
        master=cast(Any, master),
    )

    assert retargeted == (None, "4")


def test_retarget_duplicate_body_section_scope_from_close_live_siblings_requires_consensus() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("2a", _section("17", "other chapter"), _section("18", "old duplicate")),
                _chapter("4", _section("16", "live 16"), _section("18", "live 18"), _section("19", "live 19")),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
            <section><num>17 §</num><content><p>payload 17</p></content></section>
            <section><num>18 §</num><content><p>payload 18</p></content></section>
            <section><num>19 §</num><content><p>payload 19</p></content></section>
          </chapter>
        </body>
        """
    )

    retargeted = _retarget_duplicate_body_section_scope_from_close_live_siblings(
        muutos_tree=muutos_tree,
        section_norm="18",
        body_chapter="3",
        body_part=None,
        master=cast(Any, master),
    )

    assert retargeted is None


def test_compile_group_retargets_duplicate_section_label_from_close_live_siblings() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("2a", _section("18", "old duplicate")),
                _chapter(
                    "4",
                    _section("16", "live 16"),
                    _section("17", "live 17"),
                    _section("18", "live 18"),
                    _section("19", "live 19"),
                    _section("20", "live 20"),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
            <section><num>17 §</num><content><p>payload 17</p></content></section>
            <section><num>18 §</num><content><p>payload 18</p></content></section>
            <section><num>19 §</num><content><p>payload 19</p></content></section>
            <section><num>20 §</num><content><p>payload 20</p></content></section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_18_explicit",
        op_type=OpType.REPLACE,
        target_section="18",
        target_unit_kind="section",
        target_chapter="3",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="3",
        ),
        source_statute="2021/984",
        lo=LegalOperation(
            op_id="replace_18_explicit",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "3"), ("section", "18"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "18",
        "3",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "4"
    assert rop.op.lo is not None
    assert "body_chapter_retargeted_from:3" in rop.op.lo.provenance_tags
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert len(retarget) == 1
    assert retarget[0].detail["scope_source"] == "close_live_sibling_consensus"
    assert retarget[0].detail["resolved_live_chapter"] == "4"


def test_retarget_duplicate_body_section_scope_from_close_live_siblings_handles_alpha_suffix_insert_family() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("2a", _section("18", "old duplicate")),
                _chapter(
                    "4",
                    _section("16", "live 16"),
                    _section("17", "live 17"),
                    _section("18", "live 18"),
                    _section("19", "live 19"),
                    _section("20", "live 20"),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
            <section><num>17 §</num><content><p>payload 17</p></content></section>
            <section><num>18 a §</num><content><p>payload 18a</p></content></section>
            <section><num>18 b §</num><content><p>payload 18b</p></content></section>
            <section><num>18 c §</num><content><p>payload 18c</p></content></section>
            <section><num>19 §</num><content><p>payload 19</p></content></section>
            <section><num>20 §</num><content><p>payload 20</p></content></section>
          </chapter>
        </body>
        """
    )
    retargeted = _retarget_duplicate_body_section_scope_from_close_live_siblings(
        muutos_tree=muutos_tree,
        section_norm="18a",
        body_chapter="3",
        body_part=None,
        master=master,
    )

    assert retargeted == (None, "4")


def test_retarget_keeps_section_living_in_corroborated_body_chapter() -> None:
    """A section with a live home in body_chapter is not dragged out of it.

    Finlex amendment bodies routinely lump sections from several target chapters
    under one ``<chapter>`` element (the section's real chapter comes from its own
    live home, not the XML nesting). Section 14 genuinely lives in chapter 4 (the
    body chapter), while a same-element sibling §16 is being edited in its own
    chapter 5. The divergent sibling must not retarget §14 to chapter 5.
    """

    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("4", _section("14", "live 14"), _section("15", "live 15")),
                _chapter("5", _section("16", "live 16"), _section("17", "live 17")),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>4 luku</num>
            <section><num>14 §</num><content><p>payload 14</p></content></section>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
          </chapter>
        </body>
        """
    )

    retargeted = _retarget_duplicate_body_section_scope_from_close_live_siblings(
        muutos_tree=muutos_tree,
        section_norm="14",
        body_chapter="4",
        body_part=None,
        master=cast(Any, master),
    )

    assert retargeted is None


def test_retarget_keeps_new_letter_suffix_section_when_numeric_neighbor_corroborates() -> None:
    """A new letter-suffix section stays in body_chapter when a numeric neighbor lives there.

    §§14 a/14 b are inserted under the ``4 luku`` element alongside an unrelated
    §16 edited in chapter 5. The numeric base §14 lives in chapter 4, corroborating
    the body chapter, so the new sub-sections must not be pulled into chapter 5.
    """

    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("4", _section("14", "live 14"), _section("15", "live 15")),
                _chapter("5", _section("16", "live 16"), _section("17", "live 17")),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>4 luku</num>
            <section><num>14 §</num><content><p>payload 14</p></content></section>
            <section><num>14 a §</num><content><p>payload 14a</p></content></section>
            <section><num>14 b §</num><content><p>payload 14b</p></content></section>
            <section><num>16 §</num><content><p>payload 16</p></content></section>
          </chapter>
        </body>
        """
    )

    retargeted = _retarget_duplicate_body_section_scope_from_close_live_siblings(
        muutos_tree=muutos_tree,
        section_norm="14a",
        body_chapter="4",
        body_part=None,
        master=cast(Any, master),
    )

    assert retargeted is None


def test_compile_group_uses_unscoped_body_surface_for_carry_forward_section_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="87",
                                    children=(
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="old one"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="old two"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="old three"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="old four"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="old five"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section>
            <num>87 §</num>
            <subsection><num>1 mom.</num><content><p>payload one</p></content></subsection>
            <subsection><num>6 mom.</num><content><p>payload six</p></content></subsection>
          </section>
        </body>
        """
    )
    group_ops = [
        AmendmentOp(
            op_id="replace_87_1",
            op_type=OpType.REPLACE,
            target_section="87",
            target_unit_kind="section",
            target_part="5",
            target_chapter="13",
            target_paragraph=1,
            scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
            source_statute="2025/201",
        ),
        AmendmentOp(
            op_id="insert_87_6",
            op_type=OpType.INSERT,
            target_section="87",
            target_unit_kind="section",
            target_part="5",
            target_chapter="13",
            target_paragraph=6,
            scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
            source_statute="2025/201",
        ),
    ]

    result = _compile_group(
        master,
        "section",
        "87",
        "13",
        "5",
        group_ops,
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("official_consolidation"),
        None,
        None,
    )

    assert [rop.description() for rop in result.output] == ["REPLACE 13 luku 87 § 1 mom", "INSERT 13 luku 87 § 6 mom"]


def test_compile_group_uses_stale_body_chapter_surface_for_carry_forward_section_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="87",
                                    children=(
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="old one"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="old two"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="old three"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="old four"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="old five"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>7 luku</num>
            <section>
              <num>87 §</num>
              <subsection><num>1 mom.</num><content><p>payload one</p></content></subsection>
              <subsection><num>6 mom.</num><content><p>payload six</p></content></subsection>
            </section>
          </chapter>
        </body>
        """
    )
    group_ops = [
        AmendmentOp(
            op_id="replace_87_1",
            op_type=OpType.REPLACE,
            target_section="87",
            target_unit_kind="section",
            target_part="5",
            target_chapter="13",
            target_paragraph=1,
            scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
            source_statute="2025/201",
        ),
        AmendmentOp(
            op_id="insert_87_6",
            op_type=OpType.INSERT,
            target_section="87",
            target_unit_kind="section",
            target_part="5",
            target_chapter="13",
            target_paragraph=6,
            scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
            source_statute="2025/201",
        ),
    ]

    result = _compile_group(
        master,
        "section",
        "87",
        "13",
        "5",
        group_ops,
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("official_consolidation"),
        None,
        None,
    )

    assert [rop.description() for rop in result.output] == [
        "REPLACE 13 luku 87 § 1 mom",
        "INSERT 13 luku 87 § 6 mom",
    ]


def test_compile_group_pure_insert_keeps_explicit_chapter_over_sibling_consensus() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="7"),
                        IRNode(kind=IRNodeKind.SECTION, label="8"),
                        IRNode(kind=IRNodeKind.SECTION, label="8a"),
                        IRNode(kind=IRNodeKind.SECTION, label="9"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="7"),
                        IRNode(kind=IRNodeKind.SECTION, label="8"),
                        IRNode(kind=IRNodeKind.SECTION, label="9"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>5 luku</num>
            <section>
              <num>8 a §</num>
              <content><p>new chapter 5 section 8a</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_8a_explicit",
        op_type=OpType.INSERT,
        target_section="8a",
        target_unit_kind="section",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="5",
        ),
        source_statute="2022/33",
        lo=LegalOperation(
            op_id="insert_8a_explicit",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "8a"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "8a",
        "5",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "5"
    assert not any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in result.findings()
    )


def test_compile_group_reports_pure_insert_body_chapter_scope_correction() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="7", children=()),
                IRNode(kind=IRNodeKind.CHAPTER, label="7a", children=()),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7a luku</num>
              <section>
                <num>53 a §</num>
                <content><p>new 53a</p></content>
              </section>
            </chapter>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="insert_53a_carry_forward",
        op_type=OpType.INSERT,
        target_section="53a",
        target_unit_kind="section",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="7",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
        source_statute="2024/1",
        lo=LegalOperation(
            op_id="insert_53a_carry_forward",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "7"), ("section", "53a"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "53a",
        "7",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "7a"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "7a"), ("section", "53a"))
    correction = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
    ]
    assert len(correction) == 1
    assert correction[0].detail["strict_disposition"] == "record"
    assert correction[0].detail["quirks_disposition"] == "apply"
    assert correction[0].detail["resolved_body_chapter"] == "7a"


def test_compile_group_strict_profile_blocks_carry_forward_live_section_retarget() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *chapters: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("III", _chapter("18", _section("159", "live 159"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>III osa</num>
            <chapter>
              <num>18 luku</num>
              <section>
                <num>159 §</num>
                <content><p>payload</p></content>
              </section>
            </chapter>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_159_cf",
        op_type=OpType.REPLACE,
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
        source_statute="2019/371",
    )
    strict_profile = StrictProfile(
        name="strict",
        allows_context_dependent_anchor_resolution=False,
    )

    result = _compile_group(
        master,
        "section",
        "159",
        "2",
        "III",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit", strict_profile),
        None,
        strict_profile,
    )

    assert result.output == []
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert retarget
    assert retarget[0].detail["strict_disposition"] == "block"
    assert retarget[0].detail["quirks_disposition"] == "record"
    rejected = [
        finding
        for finding in result.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
    ]
    assert any(
        finding.detail["reason_code"] == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in rejected
    )


def test_compile_group_reports_body_chapter_replace_to_insert_move_recovery() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(_chapter("7", _section("55", "live")),)))
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <hcontainer>
              <section><num>7 c luku</num><heading>Ydinjätteiden tuonti ja vienti</heading></section>
              <section><num>55 §</num><content><p>payload</p></content></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace55",
        op_type=OpType.REPLACE,
        target_section="55",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1996/473",
        lo=LegalOperation(
            op_id="replace55",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "7"), ("section", "55"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "55",
        "7",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.op.op_type == "INSERT"
    assert rop.op.target_cols.target_chapter == "7c"
    assert rop.op.body_chapter_move_from == "7"
    assert rop.op.lo is not None
    assert rop.op.lo.action is StructuralAction.INSERT
    finding = next(
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
    )
    assert finding.detail["family"] == "action_family_recovery"
    assert finding.detail["original_action"] == "REPLACE"
    assert finding.detail["lowered_action"] == "INSERT"
    assert finding.detail["body_chapter"] == "7c"
    assert finding.detail["trigger_evidence"] == ("pseudo_chapter_marker",)
    assert finding.detail["strict_disposition"] == "block"


def test_compile_group_preserves_declared_body_chapter_move_as_replace() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(_chapter("6", _section("25", "live")),)))
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <hcontainer>
              <section><num>6 a luku</num><heading>Sopimukset</heading></section>
              <section><num>25 §</num><content><p>payload</p></content></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace25",
        op_type=OpType.REPLACE,
        target_section="25",
        target_unit_kind="section",
        target_chapter="6",
        source_statute="1999/466",
        lo=LegalOperation(
            op_id="replace25",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "6"), ("section", "25"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "25",
        "6",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "lisätään lakiin uusi 6 a luku, johon samalla siirretään muutettu 25 §",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.op.op_type == "REPLACE"
    assert rop.op.target_cols.target_chapter == "6a"
    assert rop.op.move_clause_target_unit_kind == "chapter"
    assert rop.op.body_chapter_move_from == "6"
    assert rop.op.lo is not None
    assert rop.op.lo.action is StructuralAction.REPLACE
    finding = next(
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.BODY_CHAPTER_DECLARED_MOVE_REPLACE"
    )
    assert finding.detail["family"] == "action_family_recovery"
    assert finding.detail["original_action"] == "REPLACE"
    assert finding.detail["lowered_action"] == "REPLACE"
    assert finding.detail["body_chapter"] == "6a"
    assert finding.detail["strict_disposition"] == "allow"


def test_compile_group_strict_rejects_body_chapter_replace_to_insert_move_recovery() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(_chapter("7", _section("55", "live")),)))
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <hcontainer>
              <section><num>7 c luku</num><heading>Ydinjätteiden tuonti ja vienti</heading></section>
              <section><num>55 §</num><content><p>payload</p></content></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace55",
        op_type=OpType.REPLACE,
        target_section="55",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1996/473",
    )
    strict_profile = StrictProfile(
        name="strict",
        allows_context_dependent_anchor_resolution=False,
    )

    result = _compile_group(
        master,
        "section",
        "55",
        "7",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit", strict_profile),
        None,
        strict_profile,
    )

    assert result.output == []
    assert any(finding.kind == "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE" for finding in result.findings())
    rejected = [
        finding
        for finding in result.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
    ]
    assert any(
        finding.detail["reason_code"] == "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
        for finding in rejected
    )


def test_compile_group_does_not_report_body_chapter_move_for_same_chapter_replace() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=(_chapter("7", _section("55", "live")),)))
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 luku</num>
              <section><num>55 §</num><content><p>payload</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    op = AmendmentOp(
        op_id="replace55",
        op_type=OpType.REPLACE,
        target_section="55",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1996/473",
    )

    result = _compile_group(
        master,
        "section",
        "55",
        "7",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    assert result.output[0].op.op_type == "REPLACE"
    assert not any(
        finding.kind == "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
        for finding in result.findings()
    )


def test_compile_group_retargets_explicit_scope_rewrite_live_section_to_unique_current_chapter() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _chapter("3", _section("15", "live 15")),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section>
              <num>15 §</num>
              <content><p>payload</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_15_rewrite",
        op_type=OpType.REPLACE,
        target_section="15",
        target_unit_kind="section",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_stripped_unique_section",
            source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
            confidence=ScopeResolutionConfidence.REWRITTEN,
            resolved_chapter="2",
        ),
        source_statute="2016/533",
        lo=LegalOperation(
            op_id="replace_15_rewrite",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "2"), ("section", "15"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "15",
        "2",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "3"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "3"
    assert rop.op.lo is not None
    assert "body_chapter_retargeted_from:2" in rop.op.lo.provenance_tags
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert len(retarget) == 1
    assert retarget[0].detail["target_chapter"] == "2"
    assert retarget[0].detail["resolved_live_chapter"] == "3"
    assert retarget[0].detail["scope_source"] == "explicit_scope_rewrite"


def test_compile_group_retargets_explicit_chunk_section_to_body_backed_live_part_and_chapter() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("5", _chapter("13", _section("84", "live 84"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>V OSA</num>
            <section>
              <num>84 §</num>
              <content><p>payload</p></content>
            </section>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_84_explicit_chunk",
        op_type=OpType.REPLACE,
        target_section="84",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="7",
        ),
        source_statute="2023/497",
        lo=LegalOperation(
            op_id="replace_84_explicit_chunk",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "3"), ("chapter", "7"), ("section", "84"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "84",
        "7",
        "3",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_part == "5"
    assert rop.resolved_target_scope_view.target_chapter == "13"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.source == "explicit_scope_rewrite"
    assert rop.scope_confidence.tag == "body_container_membership_rewrite"
    assert rop.op.lo is not None
    assert "body_part_retargeted_from:3" in rop.op.lo.provenance_tags
    assert "body_chapter_retargeted_from:7" in rop.op.lo.provenance_tags
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert len(retarget) == 1
    assert retarget[0].detail["body_part"] == "5"
    assert retarget[0].detail["target_part"] == "3"
    assert retarget[0].detail["resolved_live_part"] == "5"
    assert retarget[0].detail["resolved_live_chapter"] == "13"
    assert retarget[0].detail["scope_source"] == "explicit_chunk"


def test_compile_group_retargets_explicit_chunk_section_from_stale_part_only_scope_to_live_part_and_chapter() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("5", _chapter("13", _section("93", "live 93"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>V OSA</num>
            <chapter>
              <num>13 luku</num>
              <section>
                <num>93 §</num>
                <subsection>
                  <num>1 mom.</num>
                  <content><p>payload</p></content>
                </subsection>
              </section>
            </chapter>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_93_part_only_scope",
        op_type=OpType.REPLACE,
        target_section="93",
        target_unit_kind="section",
        target_part="3",
        target_paragraph=4,
        scope_confidence=ScopeConfidence(
            tag="part_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
        ),
        source_statute="2023/497",
        lo=LegalOperation(
            op_id="replace_93_part_only_scope",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "3"), ("section", "93"), ("subsection", "4"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "93",
        None,
        "3",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_part == "5"
    assert rop.resolved_target_scope_view.target_chapter == "13"
    assert rop.effective_target_paragraph == 4
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.source == "explicit_scope_rewrite"
    assert rop.scope_confidence.tag == "body_container_membership_rewrite"
    assert rop.op.lo is not None
    assert "body_part_retargeted_from:3" in rop.op.lo.provenance_tags
    retarget = [
        finding
        for finding in result.findings()
        if finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
    ]
    assert len(retarget) == 1
    assert retarget[0].detail["target_part"] == "3"
    assert retarget[0].detail["target_chapter"] == ""
    assert retarget[0].detail["body_part"] == "5"
    assert retarget[0].detail["body_chapter"] == "13"
    assert retarget[0].detail["resolved_live_part"] == "5"
    assert retarget[0].detail["resolved_live_chapter"] == "13"
    assert retarget[0].detail["scope_source"] == "explicit_chunk"


def test_compile_group_strict_profile_blocks_explicit_chunk_body_backed_live_section_retarget() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("5", _chapter("13", _section("84", "live 84"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>V OSA</num>
            <section>
              <num>84 §</num>
              <content><p>payload</p></content>
            </section>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_84_explicit_chunk",
        op_type=OpType.REPLACE,
        target_section="84",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="7",
        ),
        source_statute="2023/497",
    )
    strict_profile = StrictProfile(
        name="strict",
        allows_context_dependent_anchor_resolution=False,
    )

    result = _compile_group(
        master,
        "section",
        "84",
        "7",
        "3",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit", strict_profile),
        None,
        strict_profile,
    )

    assert result.output == []
    assert any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in result.findings()
    )
    rejected = [
        finding
        for finding in result.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
    ]
    assert any(
        finding.detail["reason_code"] == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in rejected
    )


def test_compile_group_retargets_explicit_chunk_section_to_unique_live_path_when_body_scope_is_stale() -> None:
    def _section(label: str, text: str = "") -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)

    def _chapter(label: str, *sections: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))

    def _part(label: str, *children: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(children))

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _part("4", _chapter("11a", _section("75e", "live 75e"))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <part>
            <num>III OSA</num>
            <chapter>
              <num>7 luku</num>
              <section>
                <num>75 e §</num>
                <content><p>payload</p></content>
              </section>
            </chapter>
          </part>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="replace_75e_explicit_chunk",
        op_type=OpType.REPLACE,
        target_section="75e",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="7",
        ),
        source_statute="2023/497",
        lo=LegalOperation(
            op_id="replace_75e_explicit_chunk",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "3"), ("chapter", "7"), ("section", "75e"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "75e",
        "7",
        "3",
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    assert result.output[0].op.target_cols.target_section == "75e"


def test_build_group_surface_uses_renumber_destination_payload_when_source_label_missing() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>1 luku</num>
            <section>
              <num>159 §</num>
              <heading>Palveluiden yhteentoimivuus</heading>
            </section>
          </chapter>
        </body>
        """
    )
    renumber = AmendmentOp(
        op_id="renumber_5_159",
        op_type=OpType.RENUMBER,
        target_section="5",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        source_statute="2019/371",
        lo=LegalOperation(
            op_id="renumber_5_159",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "5"))),
            destination=LegalAddress(path=(("section", "159"),)),
        ),
    )
    heading_replace = AmendmentOp(
        op_id="replace_159_heading",
        op_type=OpType.REPLACE,
        target_section="5",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_special="otsikko",
        source_statute="2019/371",
    )

    result = _build_group_surface(
        _BuildGroupSurfaceRequest(
            group_ops=[renumber, heading_replace],
            target_unit_kind="section",
            target_norm="5",
            target_chapter="2",
            target_part="III",
            source_model=AmendmentSourceModel.from_tree(root),
        )
    )

    assert result.output.body_ir is not None
    assert result.output.body_ir.kind is IRNodeKind.SECTION
    assert result.output.body_ir.label == "159"


def test_elaborate_group_phase1_constraint_filter_records_rejected_op_obligation() -> None:
    muutos_tree = etree.fromstring('<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" />')
    op = AmendmentOp(
        op_id="replace_5",
        op_type=OpType.REPLACE,
        target_section="5",
        target_unit_kind="section",
        source_statute="2099/1",
    )
    group_surface_result = _build_group_surface(
        _BuildGroupSurfaceRequest(
            group_ops=[op],
            target_unit_kind="section",
            target_norm="5",
            target_chapter=None,
            target_part=None,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
        )
    )
    group_surface = group_surface_result.output
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    lookups = snapshot_replay_lookups(state)
    result = _elaborate_group(
        _ElaborateGroupRequest(
            target_ctx=snapshot_target_context(state, "section", "5", None, lookups),
            lookups=lookups,
            group_surface=group_surface,
            group_ops=[op],
            standalone_section_targets=set(),
            foreign_scoped_standalone_section_targets=set(),
            foreign_scoped_replace_section_targets=set(),
            effective_target_part=None,
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            johto="ruotsinkielinen sanamuoto",
            profile=get_replay_profile("legal_pit"),
            strict_profile=None,
        )
    )

    assert result.output.was_filtered is True
    failures = [
        finding
        for finding in result.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
    ]
    assert len(failures) == 1
    assert failures[0].role == "obligation"
    assert failures[0].blocking is True
    assert failures[0].detail.get("description") == op.description()
    assert "_c_language_variant" in str(failures[0].detail.get("reason", ""))
    assert failures[0].detail.get("reason_code") == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"


def test_find_muutos_node_truncates_real_chapter_before_pseudochapter_marker() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>16 a luku</num>
            <heading>Sulautuminen</heading>
            <section><num>1 §</num></section>
            <section><num>15 §</num></section>
            <section>
              <num>16 b luku</num>
              <heading>Jakautuminen</heading>
            </section>
            <section><num>1 §</num></section>
            <section><num>8 §</num></section>
          </chapter>
        </body>
        """
    )

    chapter = _find_muutos_node(root, "chapter", "16a")

    assert chapter is not None
    assert [child.findtext("{*}num") for child in chapter.findall("./{*}section")] == ["1 §", "15 §"]


def test_find_muutos_node_synthesizes_pseudochapter_from_marker_section() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>16 a luku</num>
            <heading>Sulautuminen</heading>
            <section><num>1 §</num></section>
            <section><num>15 §</num></section>
            <section>
              <num>16 b luku</num>
              <heading>Jakautuminen</heading>
            </section>
            <section><num>1 §</num></section>
            <section><num>8 §</num></section>
          </chapter>
        </body>
        """
    )

    chapter = _find_muutos_node(root, "chapter", "16b")

    assert chapter is not None
    assert chapter.findtext("{*}num") == "16 b luku"
    assert chapter.findtext("{*}heading") == "Jakautuminen"
    assert [child.findtext("{*}num") for child in chapter.findall("./{*}section")] == ["1 §", "8 §"]


def test_find_muutos_node_finds_scoped_section_under_synthetic_pseudochapter() -> None:
    root = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>16 a luku</num>
            <heading>Sulautuminen</heading>
            <section><num>1 §</num></section>
            <section><num>15 §</num></section>
            <section>
              <num>16 b luku</num>
              <heading>Jakautuminen</heading>
            </section>
            <section><num>1 §</num></section>
            <section><num>2 §</num></section>
            <section><num>8 §</num></section>
          </chapter>
        </body>
        """
    )

    section = _find_muutos_node(root, "section", "2", target_chapter="16b")

    assert section is not None
    assert section.findtext("{*}num") == "2 §"


def test_prune_container_payload_sections_keeps_new_sections_with_standalone_targets() -> None:
    """Legacy wrapper: new sections with standalone targets must be kept (Bug C fix)."""
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="14"),
                        IRNode(kind=IRNodeKind.SECTION, label="15"),
                    ),
                ),
            ),
        )
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="26"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        master, "chapter", "3", muutos_ir, {"26"}
    )

    # Section "26" is NEW (not in live members {14,15}) — must be kept.
    assert changed is False
    assert isinstance(got, IRNode)
    assert pruned == []
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["14", "15", "26"]


def test_prune_container_payload_sections_prunes_foreign_scoped_shadow_from_heading_only_live_container() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9a",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="9 a luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="59a"),
                        IRNode(kind=IRNodeKind.SECTION, label="59b"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="10",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="10 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="60"),
                        IRNode(kind=IRNodeKind.SECTION, label="60a"),
                    ),
                ),
            ),
        )
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="9a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="9 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="59a"),
            IRNode(kind=IRNodeKind.SECTION, label="59b"),
            IRNode(kind=IRNodeKind.SECTION, label="60b"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        build_payload_elaboration_context(
            snapshot_target_context(master, "chapter", "9a", None, snapshot_replay_lookups(master)),
            snapshot_replay_lookups(master),
        ),
        "chapter",
        "9a",
        muutos_ir,
        {"60b"},
        foreign_scoped_standalone_section_targets={"60b"},
        expected_heading_only=True,
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["60b"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["59a", "59b"]


def test_container_pruning_heading_only_accepts_plain_container_replace_group() -> None:
    assert _container_pruning_is_expected_heading_only(
        [
            AmendmentOp(
                op_type=OpType.REPLACE,
                target_kind=TargetKind.CHAPTER,
                target_section="9a",
            )
        ]
    )


def test_prune_container_payload_sections_keeps_nonshadowed_section() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="14"),
                        IRNode(kind=IRNodeKind.SECTION, label="15"),
                    ),
                ),
            ),
        )
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="3 luku"),
            IRNode(kind=IRNodeKind.SECTION, label="14"),
            IRNode(kind=IRNodeKind.SECTION, label="15"),
            IRNode(kind=IRNodeKind.SECTION, label="26"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets(
        master, "chapter", "3", muutos_ir, {"43"}
    )

    assert changed is False
    assert pruned == []
    assert got is muutos_ir


def test_prune_container_payload_sections_shadowed_by_standalone_targets_in_new_chapter() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5b",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="5 b luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="19i"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="20"),
                    ),
                ),
            ),
        )
    )
    lookups = snapshot_replay_lookups(state)
    ctx = build_payload_elaboration_context(
        snapshot_target_context(state, "chapter", "5c", None, lookups),
        lookups,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5c",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 c luku"),
            IRNode(kind=IRNodeKind.SECTION, label="19j"),
            IRNode(kind=IRNodeKind.SECTION, label="20a"),
            IRNode(kind=IRNodeKind.SECTION, label="20h"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        ctx, "chapter", "5c", muutos_ir, {"20a", "20h"}
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["20a", "20h"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["19j"]


def test_prune_container_payload_sections_drops_foreign_scoped_shadow_in_new_chapter() -> None:
    """Foreign-scoped standalone INSERT targets must be pruned from a new chapter.

    Sibling 3e08b575 ("Prune foreign-scoped FI container payload sections")
    corrected the contract: when a new container's source payload carries
    sections that are separately INSERTed into a FOREIGN chapter (their real
    home), those payload entries are shadows and must be pruned — otherwise the
    section is duplicated in both chapters. Oracle-grounded on the real
    2016/591 / 2022/296 amendment (§§22a/22b insert into chapter 4, pruned from
    the new chapter 3b payload; see
    test_inspect_amendment_2016_591_2022_296_prunes_foreign_scoped_sections_from_new_chapter).

    This synthetic case previously asserted the pre-correction "keep" behaviour,
    which left the foreign-scoped sections doubly placed.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5b",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="5 b luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="19i"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="6 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="20"),
                    ),
                ),
            ),
        )
    )
    lookups = snapshot_replay_lookups(state)
    ctx = build_payload_elaboration_context(
        snapshot_target_context(state, "chapter", "5c", None, lookups),
        lookups,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="5c",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="5 c luku"),
            IRNode(kind=IRNodeKind.SECTION, label="19j"),
            IRNode(kind=IRNodeKind.SECTION, label="20a"),
            IRNode(kind=IRNodeKind.SECTION, label="20h"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        ctx,
        "chapter",
        "5c",
        muutos_ir,
        {"20a", "20h"},
        foreign_scoped_standalone_section_targets={"20a", "20h"},
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["20a", "20h"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["19j"]


def test_prune_container_payload_sections_prunes_foreign_insert_when_payload_is_overwrapped_context() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="8"),
                        IRNode(kind=IRNodeKind.SECTION, label="20a"),
                    ),
                ),
            ),
        )
    )
    lookups = snapshot_replay_lookups(state)
    ctx = build_payload_elaboration_context(
        snapshot_target_context(state, "chapter", "2a", None, lookups),
        lookups,
    )
    muutos_ir = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2a",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="2 a luku"),
            IRNode(kind=IRNodeKind.SECTION, label="7a"),
            IRNode(kind=IRNodeKind.SECTION, label="8"),
            IRNode(kind=IRNodeKind.SECTION, label="20a"),
        ),
    )

    got, changed, pruned = _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        ctx,
        "chapter",
        "2a",
        muutos_ir,
        {"8", "20a"},
        foreign_scoped_standalone_section_targets={"20a"},
        foreign_scoped_replace_section_targets={"8"},
    )

    assert changed is True
    assert isinstance(got, IRNode)
    assert pruned == ["8", "20a"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["7a"]


def test_group_shadow_pruning_foreign_scoped_section_targets_ignores_foreign_replaces() -> None:
    chapter_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="chapter",
        target_section="3a",
    )
    foreign_replace_20 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="20",
        target_chapter="4",
    )
    foreign_replace_21 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="21",
        target_chapter="4",
    )

    got = _group_shadow_pruning_foreign_scoped_section_targets(
        [chapter_insert, foreign_replace_20, foreign_replace_21],
        target_unit_kind="chapter",
        target_norm="3a",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == set()


def test_group_shadow_pruning_foreign_scoped_replace_section_targets_keeps_foreign_replaces() -> None:
    chapter_replace = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="chapter",
        target_section="7",
    )
    foreign_replace_51 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="51",
        target_chapter="8",
        target_paragraph=1,
    )
    foreign_replace_61 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="61",
        target_chapter="8",
        target_paragraph=1,
    )

    got = _group_shadow_pruning_foreign_scoped_replace_section_targets(
        [chapter_replace, foreign_replace_51, foreign_replace_61],
        target_unit_kind="chapter",
        target_norm="7",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == {"51", "61"}


def test_group_shadow_pruning_foreign_scoped_replace_section_target_scopes_preserves_scope() -> None:
    chapter_replace = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="chapter",
        target_section="7",
    )
    foreign_replace_51 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="51",
        target_chapter="8",
        target_paragraph=1,
    )
    local_replace_52 = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="52",
        target_chapter="7",
    )

    got = _group_shadow_pruning_foreign_scoped_replace_section_target_scopes(
        [chapter_replace, foreign_replace_51, local_replace_52],
        target_unit_kind="chapter",
        target_norm="7",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == frozenset(
        {StandaloneSectionTarget(part=None, chapter="8", label="51")}
    )


def test_group_shadow_pruning_foreign_scoped_section_targets_keeps_foreign_inserts() -> None:
    chapter_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="chapter",
        target_section="5c",
    )
    foreign_insert_20a = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="20a",
        target_chapter="6",
    )
    foreign_insert_20h = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="20h",
        target_chapter="6",
    )

    got = _group_shadow_pruning_foreign_scoped_section_targets(
        [chapter_insert, foreign_insert_20a, foreign_insert_20h],
        target_unit_kind="chapter",
        target_norm="5c",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == {"20a", "20h"}


def test_group_shadow_pruning_foreign_scoped_descendant_section_targets_keeps_only_child_inserts() -> None:
    chapter_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="chapter",
        target_section="6a",
    )
    foreign_section_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="18a",
        target_chapter="7",
    )
    foreign_subsection_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="26",
        target_chapter="9",
        target_paragraph=1,
    )

    got = _group_shadow_pruning_foreign_scoped_descendant_section_targets(
        [chapter_insert, foreign_section_insert, foreign_subsection_insert],
        target_unit_kind="chapter",
        target_norm="6a",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == {"26"}


def test_group_shadow_pruning_foreign_scoped_section_targets_ignores_carry_forward_inserts() -> None:
    chapter_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="chapter",
        target_section="3a",
    )
    foreign_insert_16a = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="16a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )
    foreign_insert_16b = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="16b",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _group_shadow_pruning_foreign_scoped_section_targets(
        [chapter_insert, foreign_insert_16a, foreign_insert_16b],
        target_unit_kind="chapter",
        target_norm="3a",
        target_part=None,
        duplicate_section_labels=frozenset(),
    )

    assert got == set()


def test_build_standalone_section_targets_ignores_descendant_only_section_ops() -> None:
    section_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="1",
        target_chapter="11a",
    )
    subsection_insert = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="1",
        target_chapter=None,
        target_paragraph=5,
    )

    got = _build_standalone_section_targets([section_insert, subsection_insert])

    assert got == frozenset({StandaloneSectionTarget(part=None, chapter="11a", label="1")})


def test_retarget_stale_body_scope_skips_whole_section_insert_when_body_matches_explicit_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1a"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="25",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter eId="ch25">
            <num>25 luku</num>
            <section eId="sec_25_1a">
              <num>1 a §</num>
              <content><p>Uusi 25 luvun 1 a §.</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="1a",
        target_chapter="25",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="25",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got is None


def test_compile_group_keeps_explicit_chunk_insert_under_matching_body_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="1"),
                        IRNode(kind=IRNodeKind.SECTION, label="2a"),
                        IRNode(kind=IRNodeKind.SECTION, label="3"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>2 luku</num>
            <section><num>1 §</num><content><p>payload 1</p></content></section>
            <section><num>2 a §</num><content><p>payload 2a</p></content></section>
            <section><num>3 §</num><content><p>payload 3</p></content></section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_2a_explicit_chunk",
        op_type=OpType.INSERT,
        target_section="2a",
        target_unit_kind="section",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="2",
        ),
        source_statute="2023/1250",
        lo=LegalOperation(
            op_id="insert_2a_explicit_chunk",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("section", "2a"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "2a",
        "2",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "2"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "2"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "2"), ("section", "2a"))
    assert not any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in result.findings()
    )


def test_compile_group_retargets_inferred_body_wrapper_scope_from_live_stem() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="14"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="8",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="43"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="62"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act>
            <body>
              <chapter>
                <num>1 luku</num>
                <section><num>14 a §</num><content><p>payload 14a</p></content></section>
                <section><num>43 §</num><content><p>payload 43</p></content></section>
                <section><num>62 a §</num><content><p>payload 62a</p></content></section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    op = AmendmentOp(
        op_id="insert_62a_inferred_wrapper",
        op_type=OpType.INSERT,
        target_section="62a",
        target_unit_kind="section",
        target_chapter="1",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="1",
        ),
        source_statute="2010/661",
        lo=LegalOperation(
            op_id="insert_62a_inferred_wrapper",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "1"), ("section", "62a"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "62a",
        "1",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "9"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "9"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "9"), ("section", "62a"))
    assert any(
        finding.kind == "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
        and finding.detail["body_chapter"] == "1"
        and finding.detail["resolved_body_chapter"] == "9"
        for finding in result.findings()
    )


def test_compile_group_does_not_undo_live_stem_host_scope_with_body_wrapper_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="14"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="8",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="43"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="62"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act>
            <body>
              <chapter>
                <num>1 luku</num>
                <section><num>14 a §</num><content><p>payload 14a</p></content></section>
                <section><num>43 §</num><content><p>payload 43</p></content></section>
                <section><num>62 a §</num><content><p>payload 62a</p></content></section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    op = AmendmentOp(
        op_id="insert_62a_inferred_home_chapter",
        op_type=OpType.INSERT,
        target_section="62a",
        target_unit_kind="section",
        target_chapter="9",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="9",
        ),
        source_statute="2010/661",
        lo=LegalOperation(
            op_id="insert_62a_inferred_home_chapter",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "9"), ("section", "62a"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="62a",
            target_chapter="9",
            target_part=None,
            group_ops=[op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    recovered = result.output
    assert recovered.effective_target_chapter == "9"
    assert len(recovered.group_ops) == 1
    recovered_op = recovered.group_ops[0]
    assert recovered_op.scope_confidence is not None
    assert recovered_op.scope_confidence.source == "live_stem_host"
    assert recovered_op.scope_confidence.resolved_chapter == "9"
    assert recovered_op.lo is not None
    assert recovered_op.lo.target.path == (("chapter", "9"), ("section", "62a"))
    assert not any(
        finding.kind == "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
        for finding in result.findings()
    )


def test_compile_group_does_not_undo_live_scope_with_mixed_real_body_wrapper() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="4"),
                        IRNode(kind=IRNodeKind.SECTION, label="5"),
                        IRNode(kind=IRNodeKind.SECTION, label="6"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="7"),
                        IRNode(kind=IRNodeKind.SECTION, label="12"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="16"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act>
            <body>
              <chapter>
                <num>2 luku</num>
                <section><num>4 §</num><content><p>payload 4</p></content></section>
                <section><num>5 §</num><content><p>payload 5</p></content></section>
                <section><num>6 §</num><content><p>payload 6</p></content></section>
                <section><num>7 §</num><content><p>payload 7</p></content></section>
                <section>
                  <num>12 §</num>
                  <heading>new heading</heading>
                  <subsection><num>2 mom.</num><content><p>payload 12.2</p></content></subsection>
                </section>
                <section><num>16 §</num><content><p>payload 16</p></content></section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    heading_op = AmendmentOp(
        op_id="replace_12_heading",
        op_type=OpType.REPLACE,
        target_section="12",
        target_unit_kind="section",
        target_chapter="3",
        target_special="otsikko",
        source_statute="2007/930",
        lo=LegalOperation(
            op_id="replace_12_heading",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "3"), ("section", "12")), special=FacetKind.HEADING),
            payload=None,
        ),
    )
    subsection_op = AmendmentOp(
        op_id="insert_12_2",
        op_type=OpType.INSERT,
        target_section="12",
        target_unit_kind="section",
        target_chapter="3",
        target_paragraph=2,
        source_statute="2007/930",
        lo=LegalOperation(
            op_id="insert_12_2",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "3"), ("section", "12"), ("subsection", "2"))),
            payload=None,
        ),
    )

    result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind="section",
            target_norm="12",
            target_chapter="3",
            target_part=None,
            group_ops=[heading_op, subsection_op],
            inserted_chapter_labels=set(),
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            strict_profile=None,
        )
    )

    recovered = result.output
    assert recovered.effective_target_chapter == "3"
    assert [op.target_cols.target_chapter for op in recovered.group_ops] == ["3", "3"]
    first_lo = recovered.group_ops[0].lo
    second_lo = recovered.group_ops[1].lo
    assert first_lo is not None
    assert second_lo is not None
    assert first_lo.target.path == (("chapter", "3"), ("section", "12"))
    assert second_lo.target.path == (
        ("chapter", "3"),
        ("section", "12"),
        ("subsection", "2"),
    )
    assert not any(
        finding.kind == "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
        for finding in result.findings()
    )


def test_compile_group_prefers_live_body_chapter_over_live_stem_host_scope() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="6", children=()),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act>
            <body>
              <chapter>
                <num>6 luku</num>
                <section><num>37 a §</num><content><p>payload 37a</p></content></section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    op = AmendmentOp(
        op_id="insert_37a_inferred_home_chapter",
        op_type=OpType.INSERT,
        target_section="37a",
        target_unit_kind="section",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        source_statute="2025/500",
        lo=LegalOperation(
            op_id="insert_37a_inferred_home_chapter",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "37a"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "37a",
        "5",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "6"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "6"), ("section", "37a"))
    assert any(
        finding.kind == "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
        and finding.detail["body_chapter"] == "6"
        and finding.detail["target_chapter"] == "5"
        for finding in result.findings()
    )


def test_compile_group_retargets_descendant_insert_from_body_wrapper_to_live_section() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="8",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="43"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act>
            <body>
              <chapter>
                <num>1 luku</num>
                <section>
                  <num>43 §</num>
                  <subsection><num>3 mom.</num><content><p>payload 43.3</p></content></subsection>
                </section>
              </chapter>
            </body>
          </act>
        </akomaNtoso>
        """
    )
    op = AmendmentOp(
        op_id="insert_43_3_inferred_wrapper",
        op_type=OpType.INSERT,
        target_section="43",
        target_unit_kind="section",
        target_chapter="1",
        target_paragraph=3,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
            confidence=ScopeResolutionConfidence.REWRITTEN,
            resolved_chapter="1",
        ),
        source_statute="2010/661",
        lo=LegalOperation(
            op_id="insert_43_3_inferred_wrapper",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "1"), ("section", "43"), ("subsection", "3"))),
            payload=None,
        ),
    )

    result = _compile_group(
        master,
        "section",
        "43",
        "1",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "8"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "8"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (
        ("chapter", "8"),
        ("section", "43"),
        ("subsection", "3"),
    )
    assert any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        and finding.detail["scope_source"] == "explicit_scope_rewrite"
        and finding.detail["resolved_live_chapter"] == "8"
        for finding in result.findings()
    )


def test_compile_group_keeps_scoped_descendant_insert_under_matching_body_chapter(
    monkeypatch,
) -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="14"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>4 luku</num>
            <section>
              <num>14 §</num>
              <subsection>
                <num>2 mom.</num>
                <content><p>payload 2 mom</p></content>
              </subsection>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_14_2mom_scoped",
        op_type=OpType.INSERT,
        target_section="14",
        target_unit_kind="section",
        target_chapter="4",
        target_paragraph=2,
        source_statute="2005/215",
        lo=LegalOperation(
            op_id="insert_14_2mom_scoped",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "4"), ("section", "14"), ("subsection", "2"))),
            payload=None,
        ),
    )

    monkeypatch.setattr(
        "lawvm.finland.source_model.AmendmentSourceModel.retarget_duplicate_body_section_scope_from_close_live_siblings",
        lambda **_kwargs: (None, "5"),
    )

    result = _compile_group(
        master,
        "section",
        "14",
        "4",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.op.description() == "INSERT 4 luku 14 § 2 mom"
    assert rop.resolved_target_scope_view.target_chapter == "4"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "4"), ("section", "14"), ("subsection", "2"))


def test_compile_group_prefers_scoped_body_chapter_for_repeated_explicit_chunk_insert_labels(
    monkeypatch,
) -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="3a"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="2"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>6 luku</num>
            <section><num>2 §</num><content><p>payload 2</p></content></section>
            <section><num>3 a §</num><content><p>payload 3a</p></content></section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_3a_explicit_chunk",
        op_type=OpType.INSERT,
        target_section="3a",
        target_unit_kind="section",
        target_chapter="6",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="6",
        ),
        source_statute="2023/1250",
        lo=LegalOperation(
            op_id="insert_3a_explicit_chunk",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "6"), ("section", "3a"))),
            payload=None,
        ),
    )

    monkeypatch.setattr(
        "lawvm.finland.source_model.AmendmentSourceModel.source_body_chapter_for_scoped_section_target",
        lambda **_kwargs: "6",
    )
    monkeypatch.setattr(
        "lawvm.finland.source_model.AmendmentSourceModel.retarget_duplicate_body_section_scope_from_close_live_siblings",
        lambda **_kwargs: (None, "1"),
    )

    result = _compile_group(
        master,
        "section",
        "3a",
        "6",
        None,
        [op],
        set(),
        set(),
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "6"
    assert rop.scope_confidence is not None
    assert rop.scope_confidence.resolved_chapter == "6"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "6"), ("section", "3a"))
    assert not any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in result.findings()
    )


def test_compile_group_keeps_carry_forward_insert_scope_when_body_chapter_is_new_container(
    monkeypatch,
) -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="16"),
                        IRNode(kind=IRNodeKind.SECTION, label="17"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="16"),
                        IRNode(kind=IRNodeKind.SECTION, label="17"),
                        IRNode(kind=IRNodeKind.SECTION, label="18"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 a luku</num>
            <section><num>16 a §</num><content><p>payload 16a</p></content></section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_16a_carry_forward",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="16a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
        lo=LegalOperation(
            op_id="insert_16a_carry_forward",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "16a"))),
            payload=None,
        ),
    )

    monkeypatch.setattr(
        "lawvm.finland.source_model.AmendmentSourceModel.retarget_duplicate_body_section_scope_from_close_live_siblings",
        lambda **_kwargs: (None, "4"),
    )

    result = _compile_group(
        master,
        "section",
        "16a",
        "5",
        None,
        [op],
        set(),
        {"3a"},
        muutos_tree,
        "",
        get_replay_profile("legal_pit"),
        None,
        None,
    )

    assert len(result.output) == 1
    rop = result.output[0]
    assert rop.resolved_target_scope_view.target_chapter == "5"
    assert rop.op.target_cols.target_chapter == "5"
    assert rop.op.lo is not None
    assert rop.op.lo.target.path == (("chapter", "5"), ("section", "16a"))
    assert not any(
        finding.kind == "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
        for finding in result.findings()
    )


def test_find_amend_paragraph_matches_intro_keyed_dot_numbering() -> None:
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="10",
                children=(IRNode(kind=IRNodeKind.INTRO, text="15. Lieksan kaupunki"),),
            ),
        ),
    )

    got = _find_amend_paragraph("15", amend_sub, None)

    assert got is not None
    assert got.label == "15"


def test_retarget_stale_body_scope_does_not_hijack_explicit_same_label_move_destination() -> None:
    master = SimpleNamespace(
        duplicate_section_labels=set(),
        find_section_path=lambda section, chapter=None, part=None: (
            (("chapter", "5a"), ("section", "29e"))
            if section == "29e" and ((chapter == "5b") or (chapter is None))
            else None
        ),
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter eId="chp_5b">
            <num>5 b luku</num>
            <section eId="chp_5b__sec_29e">
              <num>29 e §</num>
              <heading>Datakeskuksen hukkalämmön hyödyntäminen</heading>
              <content><p>Uusi 5 b luvun 29 e §.</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="29e",
        target_chapter="5b",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="5b",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(
        op=op,
        muutos_tree=muutos_tree,
        master=cast(Any, master),
        johto="muutetaan 29 e §, joka samalla siirretään 5 b lukuun",
    )

    assert got is None


def test_find_amend_paragraph_prefers_explicit_intro_item_over_positional_label() -> None:
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="1",
                children=(IRNode(kind=IRNodeKind.INTRO, text="2. Virolahden kunta"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="2",
                children=(IRNode(kind=IRNodeKind.INTRO, text="3. Miehikkälän kunta"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="3",
                children=(IRNode(kind=IRNodeKind.INTRO, text="5. Lappeenrannan kaupunki"),),
            ),
        ),
    )

    got = _find_amend_paragraph("5", amend_sub, None)

    assert got is not None
    assert got.label == "5"
    assert got.children[0].text == "5. Lappeenrannan kaupunki"


def test_find_amend_paragraph_promotes_numbered_subsection_item_payload() -> None:
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="23",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="23)"),
            IRNode(kind=IRNodeKind.INTRO, text="oppilaitoksella ammatillisesta koulutuksesta annettua lakia;"),
        ),
    )

    got = _find_amend_paragraph("23", amend_sub, None)

    assert got is not None
    assert got.kind is IRNodeKind.PARAGRAPH
    assert got.label == "23"
    assert [(child.kind, child.text) for child in got.children] == [
        (IRNodeKind.NUM, "23)"),
        (IRNodeKind.INTRO, "oppilaitoksella ammatillisesta koulutuksesta annettua lakia;"),
    ]


def test_merge_sparse_alakohta_insert_ir_splices_letter_subitem_under_existing_item() -> None:
    master_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1)"),
            IRNode(kind=IRNodeKind.CONTENT, text="sähkö, polttoaineet ja öljytuotteet:"),
            IRNode(
                kind=IRNodeKind.SUBPARAGRAPH,
                label="a",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="a)"),
                    IRNode(kind=IRNodeKind.CONTENT, text="sähkö (09310000-5);"),
                ),
            ),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="sähkö, polttoaineet ja öljytuotteet:"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="b",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="dieselpolttoaine (09134200);"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    got = _merge_sparse_alakohta_insert_ir(master_para, amend_sub, "1")

    assert got is not None
    subps = [c for c in got.children if c.kind is IRNodeKind.SUBPARAGRAPH]
    assert [c.label for c in subps] == ["a", "b"]
    assert any("dieselpolttoaine" in (c.text or "") for sp in subps for c in sp.children if c.kind is IRNodeKind.CONTENT)


def test_merge_sparse_alakohta_replace_ir_preserves_untouched_subitems() -> None:
    master_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1)"),
            IRNode(kind=IRNodeKind.INTRO, text="nestemäisillä polttoaineilla liitteen tuotteita:"),
            IRNode(
                kind=IRNodeKind.SUBPARAGRAPH,
                label="a",
                children=(IRNode(kind=IRNodeKind.NUM, text="a)"), IRNode(kind=IRNodeKind.CONTENT, text="vanha a;")),
            ),
            IRNode(
                kind=IRNodeKind.SUBPARAGRAPH,
                label="h",
                children=(IRNode(kind=IRNodeKind.NUM, text="h)"), IRNode(kind=IRNodeKind.CONTENT, text="vanha h;")),
            ),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="nestemäisillä polttoaineilla:"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="h",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi h;"),),
            ),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="10",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="erillinen kohta 10;"),),
            ),
        ),
    )

    got = _merge_sparse_alakohta_replace_ir(master_para, amend_sub, "1")

    assert got is not None
    assert [c.kind for c in got.children][:2] == [IRNodeKind.NUM, IRNodeKind.INTRO]
    assert got.children[1].text == "nestemäisillä polttoaineilla:"
    subps = [c for c in got.children if c.kind is IRNodeKind.SUBPARAGRAPH]
    assert [c.label for c in subps] == ["a", "h"]
    assert any(
        "vanha a" in (c.text or "") for sp in subps if sp.label == "a" for c in sp.children if c.kind is IRNodeKind.CONTENT
    )
    assert any(
        "uusi h" in (c.text or "") for sp in subps if sp.label == "h" for c in sp.children if c.kind is IRNodeKind.CONTENT
    )


def test_merge_letter_item_into_content_only_subsection_ir_preserves_other_rows() -> None:
    sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=(
                    "Toimituksista maksetaan palkkiota seuraavasti: "
                    "A. Eläimen ruumiinavaus 29,00 "
                    "G. Laitoksen tarkastus 22,00 "
                    "H. Poronlihan tarkastus / tarkastettu ruho 1,35"
                ),
            ),
        ),
    )
    amend_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="h",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=("H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan valvonta / tunti 32,3"),
            ),
        ),
    )

    got = _merge_letter_item_into_content_only_subsection_ir(sub, amend_para, "h")

    assert got is not None
    text = " ".join((got.children[0].text or "").split())
    assert "A. Eläimen ruumiinavaus 29,00" in text
    assert "G. Laitoksen tarkastus 22,00" in text
    assert "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan valvonta / tunti 32,3" in text
    assert "H. Poronlihan tarkastus / tarkastettu ruho 1,35" not in text


def test_merge_letter_item_from_content_subsection_ir_preserves_other_rows() -> None:
    sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=(
                    "Toimituksista maksetaan palkkiota seuraavasti: "
                    "A. Eläimen ruumiinavaus 29,00 "
                    "G. Laitoksen tarkastus 22,00 "
                    "H. Poronlihan tarkastus / tarkastettu ruho 1,35"
                ),
            ),
        ),
    )
    amend_sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=(
                    "Toimituksista maksetaan palkkiota seuraavasti: "
                    "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan "
                    "valvonta / tunti 32,3"
                ),
            ),
        ),
    )

    got = _merge_letter_item_from_content_subsection_ir(sub, amend_sub, "h")

    assert got is not None
    text = " ".join((got.children[0].text or "").split())
    assert "A. Eläimen ruumiinavaus 29,00" in text
    assert "G. Laitoksen tarkastus 22,00" in text
    assert "H. Poronlihan tarkastus sekä poroteurastamon ja teurastuspaikan valvonta / tunti 32,3" in text
    assert "H. Poronlihan tarkastus / tarkastettu ruho 1,35" not in text
    assert text.count("Toimituksista maksetaan palkkiota seuraavasti:") == 1


def test_mixed_sparse_intro_replace_preserves_first_subsection_items() -> None:
    from lawvm.finland.merge import _mixed_sparse_intro_replace_preserve_first_subsection_items_ir

    master_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="31",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Finanssivalvonnan tehtävät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.INTRO, text="Finanssivalvonnan tehtävänä on:"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="valvoa;"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="laatia arvio;"),)),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="koordinoida;"),)),
                ),
            ),
        ),
    )
    amend_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="31",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Finanssivalvonnan tehtävät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.INTRO, text="Finanssivalvonnan tehtävänä on:"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Finanssivalvonnan on yhteensovitettava arvio."),),
            ),
        ),
    )

    got = _mixed_sparse_intro_replace_preserve_first_subsection_items_ir(master_sec, amend_sec)

    assert got is not None
    got_subs = [c for c in got.children if c.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in got_subs] == ["1", "2"]
    assert [c.label for c in got_subs[0].children if c.kind is IRNodeKind.PARAGRAPH] == ["1", "2", "3"]
    assert any(c.kind is IRNodeKind.INTRO for c in got_subs[0].children)
    assert irnode_to_text(got_subs[1]) == "Finanssivalvonnan on yhteensovitettava arvio."


def test_replay_1994_1472_preserves_subparagraph_tree_across_2018_1225() -> None:
    master = pinned_replay("1994/1472", mode="official_consolidation", stop_before="2019/1554")

    sec = master.find_section("2")
    assert sec is not None
    sub1 = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION][0]
    paras = [c for c in sub1.children if c.kind is IRNodeKind.PARAGRAPH]
    p1 = next(p for p in paras if p.label == "1")
    subps = [c.label for c in p1.children if c.kind is IRNodeKind.SUBPARAGRAPH]

    assert subps == ["a", "b", "c", "d", "e", "f", "g", "h"]
    p10 = next(p for p in paras if p.label == "10")
    # The bounded fix here is structural: preserve the live a..h tree for 1 kohta
    # and keep 10 kohta addressable as its own paragraph. The remaining 2 § tail is
    # a broader malformed whole-section source-pathology lane around 2010/1399.
    assert p10.label == "10"


def test_has_single_intro_numbered_item_list_ir_detects_plain_numbered_lists() -> None:
    sub = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="Rajavyöhykkeen takaraja kulkee seuraavasti:"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2", text="2. Virolahden kunta"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="3", text="3. Miehikkälän kunta"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="5", text="5. Lappeenrannan kaupunki"),
        ),
    )

    assert _has_single_intro_numbered_item_list_ir(sub) is True


def test_peg_keeps_trailing_section_refs_after_johdantolause() -> None:
    text = "muutetaan 48 §:n 1 momentin johdantolause ja 5 momentti, 49 ja 50 §, 51 §:n 3 momentti sekä 53 §"

    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]

    assert got == ["M P 48 1 j", "M P 48 5", "M P 49", "M P 50", "M P 51 3", "M P 53"]


def test_peg_keeps_comma_continued_intro_items_and_later_sections() -> None:
    text = (
        "muutetaan 48 §:n 1 momentin johdantokappale, 2 ja 4 kohta sekä 5 momentti, "
        "49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §"
    )

    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]

    assert got == [
        "M P 48 1 j",
        "M P 48 1 2",
        "M P 48 1 4",
        "M P 48 5",
        "M P 49a 2",
        "M P 50",
        "M P 51 3",
        "M P 53",
    ]


def test_peg_keeps_bare_johdanto_targets_and_later_sections() -> None:
    text = (
        "muutetaan 1 §, 2 §:n otsikko ja 1 momentin johdanto, "
        "5 §:n 1 momentin 3 kohta, 9 §:n otsikko ja 3 momentti, "
        "10 § sekä 11 §:n johdanto ja 2 kohta seuraavasti:"
    )

    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]

    assert got == [
        "M P 1",
        "M P 2 o",
        "M P 2 1 j",
        "M P 5 1 3",
        "M P 9 o",
        "M P 9 3",
        "M P 10",
        "M P 11 j",
        "M P 11 1 2",
    ]


def test_peg_keeps_item_heading_target_and_later_same_section_items() -> None:
    text = "muutetaan 1 §:n 4 kohdan otsikko sekä 1 §:n 5, 6 ja 12 kohta"

    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]

    assert got == ["M P 1 1 4", "M P 1 1 5", "M P 1 1 6", "M P 1 1 12"]


def test_peg_preserves_johd_special_for_kohdan_johtolause() -> None:
    """Provenance: 2017/252 §2 — amendment 2021/556 targets 'kohdan johtolause'.
    The parser must preserve special='johd' so the grafter does an intro-only
    replace instead of a destructive whole-item replace."""
    # With explicit momentti: "1 momentin 1 kohdan johtolause"
    text = "muutetaan 2 §:n 1 momentin 1 kohdan johtolause seuraavasti:"
    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]
    assert got == ["M P 2 1 1 j"]

    # Without explicit momentti: "10 kohdan johtolause"
    text2 = "muutetaan 2 §:n 10 kohdan johtolause seuraavasti:"
    ops2 = parse_clause(text2).parsed_ops
    got2 = [op2.code() for op2 in ops2]
    assert got2 == ["M P 2 1 10 j"]


def test_peg_keeps_trailing_section_refs_after_bare_letter_item_ref() -> None:
    text = (
        "muutetaan eläinlääkäreiden toimituspalkkioista annetun asetuksen 1 §:n 2 momentti, "
        "2 §:n H kohta, 4 §:n 2 momentti ja 9 §:n 1 momentti seuraavasti:"
    )

    ops = parse_clause(text).parsed_ops
    got = [op.code() for op in ops]

    assert got == ["M P 1 2", "M P 2 1 h", "M P 4 2", "M P 9 1"]


def test_old_clause_bundle_keeps_roman_part_refs_and_later_repeals_alive() -> None:
    try:
        bundle = build_amendment_bundle("1901/15-001", "1987/411", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    # Core repeal + replace ops (both PEG backends produce these).
    # Note: "siirretään" (moved) verbs are now emitted as REPLACE with
    # renumber_clause:true metadata instead of RENUMBER ops.
    # Note: Roman numeral part labels are normalized to Arabic by _norm_num_token
    # so "III osa" → "3 osa", "V osa" → "5 osa", "I osa" → "1 osa".
    core_ops = [
        "REPEAL 55 §",
        "REPEAL 3 osa",
        "REPEAL 5 osa",
        "REPEAL 86 § 4 mom",
        "REPEAL 97 §",
        "REPEAL 99 § 4 mom",
        "REPEAL 103a § 2 mom",
        "REPLACE 1 osa",
    ]
    compiled = bundle["compiled_ops"]
    for op in core_ops:
        assert op in compiled, f"missing core op: {op}"
    projection_kinds = [row["kind"] for row in bundle.get("compile_projection_rows", [])]
    assert "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER" not in projection_kinds
    assert "RENUMBER 1 osa" not in compiled


def test_replay_xml_1901_15_001_section_12_preserves_old_second_moment_after_1975_351() -> None:
    try:
        bundle = build_trace_bundle("1901/15-001", "1975/351", "12 §", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    after_text = bundle["after_text"]
    oracle_text = bundle["oracle_text"]

    assert "Kadonneen tai onnettomuudessa tuhoutuneen henkilön kuolleeksi julistamisesta on tuomioistuimen kuulutettava" in after_text
    assert "Edellä 1 momentissa mainittua kuuluttamista ei kuitenkaan tarvitse toimittaa" in after_text
    assert after_text.replace("§.", "§") == oracle_text.replace("§.", "§")


def test_replay_xml_1901_15_001_section_4a_collapses_absorbed_second_moment_and_rebases_tail() -> None:
    try:
        bundle = build_trace_bundle("1901/15-001", "1975/351", "4 a §", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    after_text = bundle["after_text"]
    oracle_text = bundle["oracle_text"]

    assert after_text.count("Heillä on myös oikeus jatkaa toisen henkilön hakemusta.") == 1
    assert "Hakemuksen kuolleeksi julistamisesta virallinen syyttäjä voi tehdä muulloinkin, jos lääninhallitus niin määrää." in after_text
    assert "Milloin oikeus katsoo sopivaksi" not in after_text
    assert after_text.replace("§.", "§") == oracle_text.replace("§.", "§")

    lo_ops: list[LegalOperation] = []
    pinned_replay("1901/15-001", mode="legal_pit", stop_before="1984/139", quiet=True, lo_ops_out=lo_ops)
    snapshot = next(
        op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "1975/351"
        and op.op_id == "snapshot_section_4a"
    )
    assert snapshot.payload is not None
    assert snapshot.payload.attrs["lawvm_tail_policy"] == "replace_if_target_scope_requires"


def test_chapter_chunks_accept_grouped_luku_form() -> None:
    text = "kumotaan 3 ja 4 luku, 47 §:n 1-4 ja 7 momentti sekä 48 §"

    assert _chapter_chunks_from_johtolause(text) == [("4", ", 47 §:n 1-4 ja 7 momentti sekä 48 §")]


def test_chapter_chunks_truncate_repealed_chapter_at_later_scope_verb() -> None:
    # A repealed chapter (``kumotaan ... 14 luku``) must not absorb sections
    # introduced by a later scope verb. The new 176 § (inserted "kumotun 176 §:n
    # tilalle") belongs to its own home chapter, not the repealed chapter 14, so
    # the "14 luku" chunk ends at "muutetaan" and excludes 176 §.
    text = (
        "kumotaan arvonlisäverolain ( 1501/1993 ) 14 luku, muutetaan 72 j §:n 2 momentti, "
        "lisätään lakiin siitä lailla 773/2016 kumotun 176 §:n tilalle uusi 176 § seuraavasti:"
    )
    chunks = _chapter_chunks_from_johtolause(text)

    assert chunks == [("14", ", ")]
    assert all("176" not in chunk for _label, chunk in chunks)


def test_assign_chapter_scope_handles_grouped_luku_form() -> None:
    text = "kumotaan 3 ja 4 luku, 47 §:n 1-4 ja 7 momentti"
    legal_ops = extract_johtolause_legal_ops(text)
    master = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="3", children=(IRNode(kind=IRNodeKind.SECTION, label="47"),)),
                IRNode(kind=IRNodeKind.CHAPTER, label="4", children=(IRNode(kind=IRNodeKind.SECTION, label="47"),)),
            ),
        )
    )

    scoped = _assign_chapter_scope_from_johtolause(legal_ops, text, master)
    target_paths = [dict(lo.target.path) for lo in scoped if dict(lo.target.path).get("section") == "47"]

    assert target_paths
    assert all(path.get("chapter") == "4" for path in target_paths)


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_natively_scopes_inline_same_label_move_clause() -> None:
    text = "muutetaan 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun"
    legal_ops = extract_johtolause_legal_ops(text)
    target_paths = [dict(lo.target.path) for lo in legal_ops if dict(lo.target.path).get("section") in {"33", "34"}]

    assert {"chapter": "5", "section": "33"} in target_paths
    assert {"chapter": "5", "section": "34"} in target_paths
    moved_notes = [
        lo.provenance_tags
        for lo in legal_ops
        if dict(lo.target.path).get("section") in {"33", "34"} and dict(lo.target.path).get("chapter") == "5"
    ]
    moved_ops = [
        op
        for lo in legal_ops
        if dict(lo.target.path).get("section") in {"33", "34"} and dict(lo.target.path).get("chapter") == "5"
        for op in AmendmentOp.from_lo(lo, 0)
    ]
    assert moved_notes
    assert all(getattr(lo, "move_clause_target_unit_kind", None) == "chapter" for lo in legal_ops if dict(lo.target.path).get("section") in {"33", "34"} and dict(lo.target.path).get("chapter") == "5")
    assert moved_ops
    assert all(op.move_clause_target_unit_kind == "chapter" for op in moved_ops)


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_natively_retargets_direct_same_label_move_clause() -> None:
    text = (
        "muutetaan maksupalvelulain (290/2010) 85 b ja 85 c §, sellaisina kuin ne ovat laissa 898/2017, "
        "siirretään muutettu 85 b § 9 lukuun ja lisätään lakiin uusi 85 d § seuraavasti:"
    )
    legal_ops = extract_johtolause_legal_ops(text)
    moved_replace = [
        lo
        for lo in legal_ops
        if lo.action is StructuralAction.REPLACE
        and dict(lo.target.path).get("section") == "85b"
        and dict(lo.target.path).get("chapter") == "9"
    ]
    orphan_renumber = [
        lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER and dict(lo.target.path).get("section") == "85b"
    ]

    assert moved_replace
    assert all(getattr(lo, "move_clause_target_unit_kind", None) == "chapter" for lo in moved_replace)
    assert orphan_renumber == []


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_direct_same_label_move_accepts_optional_comma_before_chapter() -> None:
    text = "muutetaan 85 b §, siirretään 85 b §, 9 lukuun,"
    legal_ops = extract_johtolause_legal_ops(text)
    moved_replace = [
        lo
        for lo in legal_ops
        if lo.action is StructuralAction.REPLACE
        and dict(lo.target.path).get("section") == "85b"
        and dict(lo.target.path).get("chapter") == "9"
    ]
    orphan_renumber = [
        lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER and dict(lo.target.path).get("section") == "85b"
    ]

    assert moved_replace
    assert all(getattr(lo, "move_clause_target_unit_kind", None) == "chapter" for lo in moved_replace)
    assert orphan_renumber == []


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_natively_scopes_inline_move_clause_without_samalla() -> None:
    text = "muutetaan 31–34 §, joista 33 ja 34 § siirretään 5 lukuun"
    legal_ops = extract_johtolause_legal_ops(text)
    target_paths = [dict(lo.target.path) for lo in legal_ops if dict(lo.target.path).get("section") in {"33", "34"}]
    assert {"chapter": "5", "section": "33"} in target_paths
    assert {"chapter": "5", "section": "34"} in target_paths
    moved_notes = [
        lo.provenance_tags
        for lo in legal_ops
        if dict(lo.target.path).get("section") in {"33", "34"} and dict(lo.target.path).get("chapter") == "5"
    ]
    assert moved_notes
    assert all("chapter" == getattr(lo, "move_clause_target_unit_kind", None) for lo in legal_ops if dict(lo.target.path).get("section") in {"33", "34"} and dict(lo.target.path).get("chapter") == "5")


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_natively_recovers_direct_section_relabel() -> None:
    text = (
        "kumotaan 12 päivänä heinäkuuta 1940 annetun perintö- ja lahjaverolain (378/40) 19 §:n 1 kohta, "
        "muutetaan 16 ja 21 a § sekä 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää, "
        "joka siirretään 7 luvun 61 §:ksi,"
    )
    legal_ops = extract_johtolause_legal_ops(text)
    relabel = next(lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER)

    assert dict(relabel.target.path) == {"chapter": "7", "section": "73"}
    assert relabel.destination is not None
    assert dict(relabel.destination.path) == {"chapter": "7", "section": "61"}


def test_extract_johtolause_legal_ops_direct_relabel_defaults_implied_destination_chapter() -> None:
    text = "kumotaan 1 §, muutetaan 7 luvun 73 §:ää, joka siirretään 61 §:ksi,"
    legal_ops = extract_johtolause_legal_ops(text)
    relabel = next(lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER)

    assert dict(relabel.target.path) == {"chapter": "7", "section": "73"}
    assert relabel.destination is not None
    assert dict(relabel.destination.path) == {"chapter": "7", "section": "61"}


def test_extract_johtolause_legal_ops_direct_relabel_accepts_plain_section_without_comma() -> None:
    text = "kumotaan 1 §, muutetaan 7 luvun 73 § joka siirretään 61 §:ksi,"
    legal_ops = extract_johtolause_legal_ops(text)
    relabel = next(lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER)

    assert dict(relabel.target.path) == {"chapter": "7", "section": "73"}
    assert relabel.destination is not None
    assert dict(relabel.destination.path) == {"chapter": "7", "section": "61"}


def test_extract_johtolause_legal_ops_direct_relabel_accepts_plain_source_section_token() -> None:
    text = "kumotaan 1 §, muutetaan 7 luvun 73 §, joka siirretään 7 luvun 61 §:ksi,"
    legal_ops = extract_johtolause_legal_ops(text)
    relabel = next(lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER)

    assert dict(relabel.target.path) == {"chapter": "7", "section": "73"}
    assert relabel.destination is not None
    assert dict(relabel.destination.path) == {"chapter": "7", "section": "61"}


def test_drop_payloadless_source_replace_shadowed_by_same_group_relabel() -> None:
    replace_op = AmendmentOp(
        op_id="replace_73",
        op_type=OpType.REPLACE,
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
    )
    renumber_op = AmendmentOp(
        op_id="renumber_73_61",
        op_type=OpType.RENUMBER,
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
        lo=LegalOperation(
            op_id="renumber_73_61",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
            destination=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
            source=OperationSource(statute_id="1994/318"),
        ),
    )

    kept, rejected = _drop_payloadless_source_replace_shadowed_by_same_group_relabel(
        [replace_op, renumber_op],
        muutos_ir=None,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
        target_part=None,
    )

    assert [op.op_type for op in kept] == ["RENUMBER"]
    assert len(rejected) == 1
    assert rejected[0].reason_code == "ELAB.PAYLOADLESS_REPLACE_SHADOWED_BY_RELABEL"


def test_drop_payloadless_source_replace_shadowed_by_same_group_relabel_keeps_replace_when_payload_exists() -> None:
    replace_op = AmendmentOp(
        op_id="replace_73",
        op_type=OpType.REPLACE,
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
    )
    renumber_op = AmendmentOp(
        op_id="renumber_73_61",
        op_type=OpType.RENUMBER,
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
    )
    payload = IRNode(kind=IRNodeKind.SECTION, label="73")

    kept, rejected = _drop_payloadless_source_replace_shadowed_by_same_group_relabel(
        [replace_op, renumber_op],
        muutos_ir=payload,
        target_unit_kind="section",
        target_norm="73",
        target_chapter="7",
        target_part=None,
    )

    assert [op.op_type for op in kept] == ["REPLACE", "RENUMBER"]
    assert rejected == []


def test_false_positive_reference_constraint_keeps_payloadless_relabel() -> None:
    relabel_op = AmendmentOp(
        op_id="renumber_27h_27i",
        op_type=OpType.RENUMBER,
        target_section="27h",
        target_unit_kind="section",
        target_chapter="6a",
        source_statute="2003/444",
        lo=LegalOperation(
            op_id="renumber_27h_27i",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("chapter", "6a"), ("section", "27h"))),
            destination=LegalAddress(path=(("chapter", "6a"), ("section", "27i"))),
            source=OperationSource(statute_id="2003/444"),
        ),
    )
    rejected: list[Any] = []

    kept = _filter_ops_by_constraints(
        [relabel_op],
        _FilterCtx(
            muutos_ir=None,
            johto="nykyinen 27 g-27 i § siirtyy 27 h-27 j §:ksi",
        ),
        rejected_ops_out=rejected,
    )

    assert kept == [relabel_op]
    assert rejected == []


def test_unsupported_payload_rejection_does_not_reject_relabel() -> None:
    relabel_op = AmendmentOp(
        op_id="renumber_27i_27j",
        op_type=OpType.RENUMBER,
        target_section="27i",
        target_unit_kind="section",
        target_chapter="6a",
        source_statute="2003/444",
    )
    replace_op = AmendmentOp(
        op_id="replace_27i",
        op_type=OpType.REPLACE,
        target_section="27i",
        target_unit_kind="section",
        target_chapter="6a",
        source_statute="2003/444",
    )

    rejected = _unsupported_payload_rejected_ops(
        group_ops=[relabel_op, replace_op],
        rejected_ops=[],
        payload_completeness=PayloadCompletenessWitness(
            kind="unsupported",
            reasons=("missing_payload_ir",),
            tail_policy="classify_only",
            detail={},
        ),
    )

    assert [item.description for item in rejected] == [replace_op.description()]
    assert rejected[0].reason_code == "UNSUPPORTED_PAYLOAD_MISSING_PAYLOAD_IR"


def test_build_amendment_bundle_keeps_scoped_move_targets_as_section_groups(
    amendment_bundle_2010_182_2020_766: dict[str, Any],
) -> None:
    """Scoped move-tail section targets must stay chapter-scoped after PEG migration.

    The old xfail expected a specific container-pruning count and observation.
    That shape is no longer stable or necessary. The real invariant is that the
    moved section targets continue to materialize as separate chapter-scoped
    section groups instead of being lost inside the chapter container payload.
    """
    bundle = amendment_bundle_2010_182_2020_766
    chapter5 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "chapter" and g["target_norm"] == "5")
    sec33 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "section" and g["target_norm"] == "33")
    sec34 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "section" and g["target_norm"] == "34")

    assert str(chapter5["normalized_payload"]["kind"].value) == "chapter"
    assert sec33["target_chapter"] == "5"
    assert sec34["target_chapter"] == "5"


def test_build_amendment_bundle_keeps_post_move_clause_trailing_replace_targets(
    amendment_bundle_2010_182_2020_766: dict[str, Any],
) -> None:
    bundle = amendment_bundle_2010_182_2020_766
    compiled = set(bundle["compiled_ops"])

    assert "REPLACE 7 luku otsikko" in compiled
    assert any(op.endswith("47 §") for op in compiled)
    assert any(op.endswith("48 §") for op in compiled)
    assert any(op.endswith("49 §") for op in compiled)
    assert any(op.endswith("54 §") for op in compiled)
    assert any(op.endswith("56 §") for op in compiled)
    assert any(op.endswith("71 §") for op in compiled)
    assert any(op.endswith("72 §") for op in compiled)
    assert any(op.endswith("74 §") for op in compiled)
    assert any(op.endswith("78 §") for op in compiled)
    assert any(op.endswith("80 §") for op in compiled)
    assert any(op.endswith("81 §") for op in compiled)
    assert any(op.endswith("82 §") for op in compiled)


def test_build_amendment_bundle_requests_replay_fold_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_call_replay_xml(_fn: object, *, request: ReplayXmlRequest) -> object:
        captured["build_full_products"] = request.build_full_products
        raise RuntimeError("stop after replay request")

    monkeypatch.setattr(inspect_amendment, "call_replay_xml", fake_call_replay_xml)

    with pytest.raises(RuntimeError, match="stop after replay request"):
        build_amendment_bundle("2014/917", "2020/1207", "legal_pit")

    assert captured == {"build_full_products": False}


def test_build_amendment_bundle_salvages_malformed_chapter_insert_surface() -> None:
    try:
        bundle = build_amendment_bundle("2014/917", "2020/1207", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    final_ops = {
        op
        for group in bundle["groups"]
        for op in group.get("ops_final", [])
    }

    assert "INSERT 7a luku" in final_ops
    assert "INSERT 9 luku 60 § 3 mom" in final_ops
    assert "INSERT 10 luku 81a §" in final_ops
    assert "INSERT 10 luku 81b §" in final_ops
    assert "INSERT 10 luku 81c §" in final_ops
    assert "INSERT 12 luku 91a §" in final_ops
    assert "INSERT 16 luku 113 §" in final_ops
    assert "INSERT 26a luku" in final_ops
    assert "INSERT 29 luku 244a §" in final_ops
    assert "INSERT 29 luku 244b §" in final_ops
    assert "INSERT 37 luku 301a §" in final_ops
    assert "INSERT 38 luku 304 § 1 mom 14 kohta" in final_ops
    assert "INSERT 38 luku 304 § 1 mom 17 kohta" in final_ops


def test_compile_2020_1207_keeps_explicit_insert_scope_over_body_wrappers() -> None:
    try:
        before_master = inspect_amendment.call_replay_xml(
            inspect_amendment.replay_xml,
            request=ReplayXmlRequest(
                parent_id="2014/917",
                mode="legal_pit",
                stop_before="2020/1207",
                build_full_products=False,
                quiet=True,
            ),
        )
        xml_bytes = get_corpus().read_source("2020/1207")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")
    if xml_bytes is None:
        pytest.skip("Finlex archive missing amendment 2020/1207")

    _, xml_bytes = inspect_amendment.extract_inline_corrections(xml_bytes, "2020/1207")
    xml_bytes, _ = inspect_amendment.get_patch_table().patch_source_xml(xml_bytes, "2020/1207")
    xml_bytes, _ = inspect_amendment.get_patch_table().patch_source_body_xml(xml_bytes, "2020/1207")

    muutos_tree = etree.fromstring(xml_bytes)
    source_title = inspect_amendment._tree_title(muutos_tree)
    muutos_tree, johto, used_preamble_body_fallback, should_apply, _route_reason = (
        inspect_amendment._working_johtolause(
            "2014/917",
            before_master.title,
            "2020/1207",
            xml_bytes,
            source_title,
        )
    )
    assert should_apply

    source_model = AmendmentSourceModel.from_tree(muutos_tree, source_ref="2020/1207")
    normalized = normalize_and_compile_ops(
        johto,
        muutos_tree,
        before_master.replay_fold_state,
        "2020/1207",
        source_title=source_title,
        used_preamble_body_fallback=used_preamble_body_fallback,
        parent_id="2014/917",
        strict_profile=None,
        source_model=source_model,
    )
    compiled = compile_amendment_ops(
        before_master.replay_fold_state,
        normalized.output,
        source_model,
        johto,
        "legal_pit",
        strict_profile=None,
        source_ref="2020/1207",
        source_title=source_title,
        target_statute="2014/917",
    )
    descendant_inserts = {
        (op.target_cols.target_section, str(op.target_cols.target_paragraph or "")): (
            rop.resolved_target_scope_part_label,
            rop.resolved_target_scope_chapter_label,
        )
        for rop in compiled.output
        if (op := rop.op).op_type == "INSERT"
        and op.target_cols.target_unit_kind == "section"
        and op.target_cols.target_section in {"60", "99", "100", "102"}
        and op.target_cols.target_paragraph is not None
    }

    assert descendant_inserts[("60", "3")] == ("3", "9")
    assert descendant_inserts[("99", "4")] == ("4", "14")
    assert descendant_inserts[("100", "6")] == ("4", "14")
    assert descendant_inserts[("102", "2")] == ("4", "14")


def test_build_amendment_bundle_expands_letter_suffix_range_with_hyphen_dash() -> None:
    try:
        bundle = build_amendment_bundle("2010/1396", "2014/434", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    compiled = set(bundle["compiled_ops"])

    # The chapter prefix is included in descriptions for scoped ops
    assert "INSERT 2 luku 17a §" in compiled
    assert "INSERT 2 luku 17b §" in compiled
    assert "INSERT 2 luku 17c §" in compiled
    assert "INSERT 2 luku 17d §" in compiled


def test_build_amendment_bundle_folds_terminal_continuation_subsection_for_2018_441(
    amendment_bundle_2010_1396_2018_441: dict[str, Any],
) -> None:
    bundle = amendment_bundle_2010_1396_2018_441

    group48 = next(group for group in bundle["groups"] if group["target_norm"] == "48")

    assert group48["subsection_map"][0]["op"] == "REPLACE 6 luku 48 § otsikko"
    assert group48["subsection_map"][0]["mapped_payload"] is None
    assert group48["subsection_map"][1]["op"] == "REPLACE 6 luku 48 § 1 mom"
    assert group48["subsection_map"][1]["mapped_payload"]["label"] == "1"
    assert group48["sparse_slot_bindings"] == [
        {
            "op": "REPLACE 6 luku 48 § 1 mom",
            "slot_index": 1,
            "slot_label": "1",
            "target_paragraph": 1,
            "target_item": "",
            "target_special": "",
        }
    ]


def test_build_amendment_bundle_splits_fused_restarted_subsection_for_2018_441(
    amendment_bundle_2010_1396_2018_441: dict[str, Any],
) -> None:
    bundle = amendment_bundle_2010_1396_2018_441

    group51 = next(group for group in bundle["groups"] if group["target_norm"] == "51")

    assert set(group51["ops_final"]) == {"REPLACE 7 luku 51 § 1 mom", "REPLACE 7 luku 51 § 2 mom"}
    assert [entry["op"] for entry in group51["subsection_map"]] == [
        "REPLACE 7 luku 51 § 1 mom",
        "REPLACE 7 luku 51 § 2 mom",
    ]
    assert group51["subsection_map"][0]["mapped_payload"]["label"] == "1"
    assert group51["subsection_map"][1]["mapped_payload"]["label"] == "2"


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_replay_xml_materialized_state_retires_old_section_address_after_move_clause() -> None:
    result = pinned_replay("2010/182", mode="legal_pit", stop_before="2021/1219", quiet=True)

    assert result.replay_fold_state.find_section("33", "5") is not None
    assert result.replay_fold_state.find_section("34", "5") is not None
    assert result.replay_fold_state.find_section("33", "6") is None
    assert result.replay_fold_state.find_section("34", "6") is None

    assert result.materialized_state.find_section("33", "5") is not None
    assert result.materialized_state.find_section("34", "5") is not None
    assert result.materialized_state.find_section("33", "6") is None
    assert result.materialized_state.find_section("34", "6") is None


def test_strip_unjustified_chapter_scope_from_unique_sections() -> None:
    text = "muutetaan 3 §:n 3 momentti, 4, 7-9 ja 13 §, 3 luku, 23-25 §, 26 §:n 3 momentti sekä 43 §"
    legal_ops = extract_johtolause_legal_ops(text)
    master = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="3"), IRNode(kind=IRNodeKind.SECTION, label="4")),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="7"),
                        IRNode(kind=IRNodeKind.SECTION, label="8"),
                        IRNode(kind=IRNodeKind.SECTION, label="9"),
                        IRNode(kind=IRNodeKind.SECTION, label="13"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="14"), IRNode(kind=IRNodeKind.SECTION, label="15")),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="23"),
                        IRNode(kind=IRNodeKind.SECTION, label="24"),
                        IRNode(kind=IRNodeKind.SECTION, label="25"),
                    ),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="6", children=(IRNode(kind=IRNodeKind.SECTION, label="26"),)),
                IRNode(kind=IRNodeKind.CHAPTER, label="9", children=(IRNode(kind=IRNodeKind.SECTION, label="43"),)),
            ),
        )
    )

    stripped = _strip_unjustified_chapter_scope_from_unique_sections(legal_ops, text, master)
    target_paths = [dict(lo.target.path) for lo in stripped]

    assert {"chapter": "3"} in target_paths
    for path in target_paths:
        if path.get("section") in {"23", "24", "25", "26", "43"}:
            assert "chapter" not in path


def test_strip_unjustified_chapter_scope_keeps_real_chapter_member() -> None:
    text = "muutetaan 7 b luku, 14 b § ja 15 §"
    legal_ops = extract_johtolause_legal_ops(text)
    master = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7b",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="14b"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="9", children=(IRNode(kind=IRNodeKind.SECTION, label="15"),)),
            ),
        )
    )

    stripped = _strip_unjustified_chapter_scope_from_unique_sections(legal_ops, text, master)
    target_paths = [dict(lo.target.path) for lo in stripped]

    assert {"chapter": "7b"} in target_paths
    assert {"chapter": "7b", "section": "14b"} in target_paths
    assert {"section": "15"} in target_paths


def test_strip_unjustified_chapter_scope_keeps_explicit_genitive_chapter_list() -> None:
    text = "kumotaan 7 luvun 14 a ja 14 b §"
    legal_ops = extract_johtolause_legal_ops(text)
    master = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="14a"),
                        IRNode(kind=IRNodeKind.SECTION, label="14b"),
                    ),
                ),
            ),
        )
    )

    stripped = _strip_unjustified_chapter_scope_from_unique_sections(legal_ops, text, master)
    target_paths = [dict(lo.target.path) for lo in stripped]

    assert {"chapter": "7", "section": "14a"} in target_paths
    assert {"chapter": "7", "section": "14b"} in target_paths


def test_lo_with_path_update_keeps_targets_in_sync() -> None:
    target = LegalAddress(path=(("chapter", "3"), ("section", "23")))
    lo = LegalOperation(
        op_id="op_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        provenance_tags=(),
    )

    got = _lo_with_path_update(lo, chapter=None)

    assert dict(got.target.path) == {"section": "23"}
    assert got.target.path == (("section", "23"),)


def test_dedupe_fallback_ops_considers_exact_duplicate_targets() -> None:
    ops = [
        AmendmentOp(op_id="", op_type=OpType.REPEAL, target_kind=TargetKind.CHAPTER, target_section="3"),
        AmendmentOp(op_id="", op_type=OpType.REPEAL, target_kind=TargetKind.CHAPTER, target_section="3"),
        AmendmentOp(op_id="", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="47", target_paragraph=7),
        AmendmentOp(op_id="", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="47", target_paragraph=7),
    ]

    deduped = _dedupe_fallback_ops_ir(ops)

    assert [op.description() for op in deduped] == ["REPEAL 3 luku", "REPEAL 47 § 7 mom"]


def test_dedupe_fallback_ops_preserves_same_section_in_distinct_parts() -> None:
    lo_part_ii = LegalOperation(
        op_id="renum_ii",
        sequence=0,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "II"), ("chapter", "2"), ("section", "5"))),
        destination=LegalAddress(path=(("section", "23"),)),
    )
    lo_part_iii = LegalOperation(
        op_id="renum_iii",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "5"))),
        destination=LegalAddress(path=(("section", "159"),)),
    )
    ops = [
        AmendmentOp(op_id="renum_ii", op_type=OpType.RENUMBER, lo=lo_part_ii),
        AmendmentOp(op_id="renum_iii", op_type=OpType.RENUMBER, lo=lo_part_iii),
    ]

    deduped = _dedupe_fallback_ops_ir(ops)

    assert len(deduped) == 2
    assert [op.lo.destination for op in deduped if op.lo is not None] == [
        LegalAddress(path=(("section", "23"),)),
        LegalAddress(path=(("section", "159"),)),
    ]


def test_apply_ops_to_tree_does_not_use_unique_global_snapshot_hint_for_scoped_section_miss(monkeypatch) -> None:
    state = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="I",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="5",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="23", text="live"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    op = AmendmentOp(
        op_id="replace_wrong_part_23",
        op_type=OpType.REPLACE,
        target_section="23",
        target_unit_kind="section",
        target_chapter="5",
        target_part="II",
        source_statute="2099/1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="23", text="payload"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="23",
        target_chapter="5",
        target_address=LegalAddress(path=(("part", "II"), ("chapter", "5"), ("section", "23"))),
    )
    seen: dict[str, object] = {}

    def fake_apply_op(*args, **kwargs):
        return args[0]

    def fake_emit_section_snapshot(
        _state,
        _target_unit_kind,
        _target_norm,
        _target_chapter,
        _target_part,
        _group_rops,
        _lo_ops_out,
        _amendment_id,
        _source_title,
        _amendment_issue_date,
        _amendment_effective_date,
        **kwargs,
    ):
        seen["path_hint"] = kwargs.get("path_hint")

    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)
    monkeypatch.setattr("lawvm.finland.apply_group_replay._emit_section_snapshot", fake_emit_section_snapshot)

    apply_ops_to_tree(
        state,
        ctx,
        [rop],
        [op],
        etree.fromstring('<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" />'),
        "",
        "2099/1",
        "",
        None,
        None,
        None,
        "legal_pit",
        [],
        [],
        [],
        None,
        False,
    )

    assert seen["path_hint"] is None


def test_apply_ops_to_tree_uses_cross_chapter_global_fallback_for_root_level_section(monkeypatch) -> None:
    """REPLACE op with chapter scope should still find a uniquely-named section at root level.

    Regression for 1991/800 / 2008/700: sections §45b–§45f live under an hcontainer
    at root level (no chapter node in their path) but the amendment groups them under
    "5 luku" heading.  _refresh_group_path_hint must fall back to the unique global
    path so that _emit_section_snapshot can emit correct lo_ops.
    """
    state = _replay_state(
        IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.HCONTAINER,
                    label="",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="45b", text="old text"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    op = AmendmentOp(
        op_id="replace_ch5_45b",
        op_type=OpType.REPLACE,
        target_section="45b",
        target_unit_kind="section",
        target_chapter="5",
        target_part=None,
        source_statute="2099/1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="45b", text="new text"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="45b",
        target_chapter="5",
        target_address=LegalAddress(path=(("chapter", "5"), ("section", "45b"))),
    )
    seen: dict[str, object] = {}

    def fake_apply_op(*args, **kwargs):
        return args[0]

    def fake_emit_section_snapshot(
        _state,
        _target_unit_kind,
        _target_norm,
        _target_chapter,
        _target_part,
        _group_rops,
        _lo_ops_out,
        _amendment_id,
        _source_title,
        _amendment_issue_date,
        _amendment_effective_date,
        **kwargs,
    ):
        seen["path_hint"] = kwargs.get("path_hint")

    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)
    monkeypatch.setattr("lawvm.finland.apply_group_replay._emit_section_snapshot", fake_emit_section_snapshot)

    apply_ops_to_tree(
        state,
        ctx,
        [rop],
        [op],
        etree.fromstring('<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" />'),
        "",
        "2099/1",
        "",
        None,
        None,
        None,
        "legal_pit",
        [],
        [],
        [],
        None,
        False,
    )

    # The section lives at root level — hint should point to its actual path, not None
    path_hint = cast(tuple[tuple[str, str], ...], seen.get("path_hint"))
    assert path_hint is not None
    assert path_hint[-1] == ("section", "45b")


def test_find_muutos_ir_relabels_requested_letter_suffix_insert_section() -> None:
    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section>
              <num>39§</num>
              <subsection>
                <content>Inserted payload</content>
              </subsection>
            </section>
          </body>
        </act>
        """
    )

    got, _ = _find_muutos_ir(tree, "section", "39a")

    assert got is not None
    assert got.label == "39a"
    nums = [child.text for child in got.children if child.kind is IRNodeKind.NUM]
    assert nums == ["39 a §"]


def test_extract_root_replace_ops_from_body_fallback_for_generic_whole_act_replace() -> None:
    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section><num>1 §</num></section>
            <section><num>2 §</num></section>
            <section><num>3 a §</num></section>
          </body>
        </act>
        """
    )

    got = _extract_root_replace_ops_from_body_fallback(
        "muutetaan päätös (123/2000), sellaisena kuin se on muutettuna, seuraavasti:",
        tree,
    )

    assert [op.description() for op in got] == ["REPLACE 1 §", "REPLACE 2 §", "REPLACE 3a §"]


def test_extract_enacting_formula_body_insert_ops_fallback_inserts_new_letter_sections() -> None:
    """Enacting-formula-only amendment body: letter-suffix sections absent from master → INSERT.

    Regression test for 1997/147 pattern: amendment has only 'Eduskunnan päätöksen mukaisesti'
    as preamble, body sections lack eId attributes, and section 26a is new (not in master).
    """
    from lawvm.finland.frontend_compile import _extract_enacting_formula_body_insert_ops_fallback

    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section><num>1 §</num><subsection><content><p>existing text</p></content></subsection></section>
              <section><num>26 §</num><subsection><content><p>existing text</p></content></subsection></section>
              <section><num>26 a §</num><subsection><content><p>new section text</p></content></subsection></section>
              <section><num>27 §</num><hcontainer name="omission"/><subsection><content><p>partial text</p></content></subsection></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    # master has sections 1, 26, 27 but NOT 26a
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1"),
                IRNode(kind=IRNodeKind.SECTION, label="26"),
                IRNode(kind=IRNodeKind.SECTION, label="27"),
            ),
        )
    )
    johto = "Eduskunnan päätöksen mukaisesti"
    ops = _extract_enacting_formula_body_insert_ops_fallback(johto, tree, master)
    assert len(ops) == 1
    assert ops[0].op_type == "INSERT"
    assert ops[0].target_cols.target_section == "26a"
    assert ops[0].target_cols.target_unit_kind == "section"


def test_extract_enacting_formula_body_insert_ops_fallback_skips_existing_letter_sections() -> None:
    """Letter-suffix section that already exists in master must NOT produce INSERT."""
    from lawvm.finland.frontend_compile import _extract_enacting_formula_body_insert_ops_fallback

    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section><num>3 a §</num><subsection><content><p>text</p></content></subsection></section>
          </body>
        </act>
        """
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="3a"),),
        )
    )
    ops = _extract_enacting_formula_body_insert_ops_fallback(
        "Eduskunnan päätöksen mukaisesti", tree, master
    )
    assert ops == []


def test_extract_enacting_formula_body_insert_ops_fallback_rejects_op_keyword_johto() -> None:
    """If johto contains amendment keywords, this fallback must not trigger."""
    from lawvm.finland.frontend_compile import _extract_enacting_formula_body_insert_ops_fallback

    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <section><num>5 a §</num></section>
          </body>
        </act>
        """
    )
    master = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ops = _extract_enacting_formula_body_insert_ops_fallback(
        "muutetaan laki, seuraavasti:", tree, master
    )
    assert ops == []


def test_extract_enacting_formula_body_replace_ops_fallback_recovers_single_existing_section() -> None:
    tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section><num>30 §</num><subsection><content><p>updated text</p></content></subsection></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="30"),),
        )
    )

    ops = _extract_enacting_formula_body_replace_ops_fallback(
        "Eduskunnan päätöksen mukaisesti", tree, master
    )

    assert [op.description() for op in ops] == ["REPLACE 30 §"]


def test_extract_enacting_formula_body_replace_ops_fallback_skips_multiple_or_missing_sections() -> None:
    multi_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section><num>30 §</num></section>
              <section><num>31 §</num></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    missing_tree = etree.fromstring(
        """
        <act xmlns="urn:test">
          <body>
            <hcontainer name="statuteProvisionsWrapper">
              <section><num>30 §</num></section>
            </hcontainer>
          </body>
        </act>
        """
    )
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="31"),),
        )
    )

    assert _extract_enacting_formula_body_replace_ops_fallback(
        "Eduskunnan päätöksen mukaisesti", multi_tree, master
    ) == []
    assert _extract_enacting_formula_body_replace_ops_fallback(
        "Eduskunnan päätöksen mukaisesti", missing_tree, master
    ) == []


def test_fallback_recovers_complex_lakiin_uusi_section_inserts() -> None:
    johto = (
        "kumotaan 8§:n 6 momentti ja 8 a§, muutetaan 1§:n 1 momentti, "
        "2§:n 1 momentin 1-4 kohta, 4§, 5§:n 2 ja 3 momentti, 8§:n 2-4 momentti, "
        "9 b§, 10§:n 2 momentin a ja b kohta ja 3 momentin johdantokappale ja b kohta, "
        "lisätään 3§:ään uusi 3 momentti, 9§:ään uusi 2 momentti, jolloin nykyinen 2 ja 3 momentti "
        "siirtyvät 3 ja 4 momentiksi, lakiin uusi 9 c ja 9 d§, 10§:ään uusi 4 momentti, "
        "jolloin nykyinen 4 ja 5 momentti siirtyvät 5 ja 6 momentiksi, lakiin uusi 10 b§ seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    # Root (whole-§) section inserts the PEG now owns natively (verb 'L', kind 'P').
    got = {op.number for op in ops if op.verb == "L" and op.kind == "P" and op.momentti == 0}

    assert "9c" in got
    assert "9d" in got
    assert "10b" in got


def test_parse_ops_fallback_heuristic_keeps_explicit_targets_in_mixed_container_clause() -> None:
    # The container-insert regex fallback was retired (the PEG owns ``lakiin uusi
    # N luku`` inserts when they follow a verb); this fallback path now only
    # recovers the explicit REPLACE targets from the ``muutetaan`` tail.
    johto = "lakiin uusi 25 a luku, muutetaan 3 §:n 1 momentti ja 4 §:n 2 momentti, sekä 5 § seuraavasti:"

    ops = parse_ops_fallback_heuristic(johto)

    assert any(op.op_type == "REPLACE" and op.target_cols.target_section == "3" and op.target_cols.target_paragraph == 1 for op in ops)
    assert any(op.op_type == "REPLACE" and op.target_cols.target_section == "4" and op.target_cols.target_paragraph == 2 for op in ops)


def test_extract_kumotaan_section_refs_keeps_trailing_history_citation() -> None:
    johto = (
        "Tällä asetuksella kumotaan 30 päivänä marraskuuta 1990 annetun "
        "eläinlääkintähuoltoasetuksen (1039/1990) 2, 2a ja 3 § sellaisina kuin "
        "ne ovat asetuksessa 1240/1995."
    )

    got = _extract_kumotaan_section_refs(johto)

    assert got == ["2", "2a", "3"]


def test_extract_kumotaan_section_refs_expands_same_base_letter_range() -> None:
    johto = (
        "kumotaan lääketieteellisestä tutkimuksesta annetun lain (488/1999) "
        "6 a §, 2 a luvun otsikko sekä 10 d–10 i ja 14 §"
    )

    got = _extract_kumotaan_section_refs(johto)

    assert set(got) == {"6a", "10d", "10e", "10f", "10g", "10h", "10i", "14"}


def test_extract_kumotaan_section_refs_expands_numeric_to_lettered_range_to_valid_labels() -> None:
    """A ``N―M x`` range yields VALID base labels, not a bogus unexpanded literal.

    The grammar-backed enumerator expands ``5―6 b`` to the numeric base range
    ``5, 6`` (valid section labels). The retired parallel regex emitted the
    raw string ``5-6b`` — a label that matches no real section node, so the
    repeal target was effectively a silent no-op. Regression pin for that fix
    (grammar enumerates structure; ``§(?!:)`` is only the site anchor).
    """
    johto = (
        "kumotaan valtion vakuusrahastosta 30 päivänä huhtikuuta 1992 annetun "
        "lain (379/92), 5―6 b, 8―10 ja 16―17 §"
    )

    got = _extract_kumotaan_section_refs(johto)

    assert "5-6b" not in got  # no bogus unexpanded range literal
    assert set(got) == {"5", "6", "8", "9", "10", "16", "17"}


def test_extract_kumotaan_section_refs_excludes_bare_section_descendant_repeal() -> None:
    """``9 § 4 kohta`` is an item repeal, while the coordinated ``13 §`` is whole-section."""
    johto = (
        "kumotaan laivanrakennuksen innovaatioihin myönnettävästä "
        "valtionavustuksesta annetun valtioneuvoston asetuksen (364/2015) "
        "9 § 4 kohta sekä 13 §, ja muutetaan 10 § seuraavasti:"
    )

    got = _extract_kumotaan_section_refs(johto)

    assert got == ["13"]


def test_extract_kumotaan_section_refs_ignores_attachment_number_ranges_without_section_marker() -> None:
    johto = (
        "Tällä lailla kumotaan 29 päivänä joulukuuta 1994 annetun sairausvakuutuslain "
        "(1224/2004) liitteen rn 2203―2205, 211 220―211 222 sekä 2215―2217 j kohta."
    )

    got = _extract_kumotaan_section_refs(johto)

    assert got == []


def test_extract_kumotaan_chapter_section_map_chapterless_falls_back_to_global() -> None:
    """No chapter markers → None key with flat section list (global scope)."""
    johto = "kumotaan lain (123/2000) 5 §, 7–9 § ja 12 a §"
    got = _extract_kumotaan_chapter_section_map(johto)
    assert got == {None: ["5", "7", "8", "9", "12a"]}


def test_extract_kumotaan_chapter_section_map_chapter_scoped() -> None:
    """Chapter-scoped kumotaan (1997/1339 / 2015/1752 pattern).

    Sections '5', '7' belong to chapter '1'; sections '2', '3', '4' to chapter '5'.
    The map must NOT assign them globally — each section is tied to its chapter.
    """
    johto = (
        "kumotaan kirjanpitoasetuksen (1339/1997) 1 luvun 1 §:n 3 ja 4 momentti, "
        "2 §:n 3 ja 4 momentti, 5 §, 6 §:n 4 momentti, 7 §, "
        "2 luvun 2 §:n 1 momentin 1 ja 7 kohta ja 2—4 momentti, 11 §, "
        "5 luvun 2—4 §"
    )
    got = _extract_kumotaan_chapter_section_map(johto)
    # Chapter 1: fully-repealed sections 5 and 7
    assert "5" in got.get("1", [])
    assert "7" in got.get("1", [])
    # Chapter 2: fully-repealed section 11
    assert "11" in got.get("2", [])
    # Chapter 5: fully-repealed sections 2, 3, 4
    assert "2" in got.get("5", [])
    assert "3" in got.get("5", [])
    assert "4" in got.get("5", [])
    # Section '2' should NOT appear under chapter '1' (it's only momentti-level in ch1)
    assert "2" not in got.get("1", [])


def test_extract_kumotaan_chapter_section_map_multi_chapter_same_section() -> None:
    """Same section number repealed in multiple chapters (1990/811 pattern for 1978/38).

    '11' in chapters 2 and 6, '25' only in chapter 7.
    Both should be extractable with their chapter context.
    """
    johto = (
        "kumotaan kuluttajansuojalain (38/78) 2 luvun 11§, 6 luvun 11§ ja 7 luvun 25§"
    )
    got = _extract_kumotaan_chapter_section_map(johto)
    assert "11" in got.get("2", [])
    assert "11" in got.get("6", [])
    assert "25" in got.get("7", [])


def test_extract_muutetaan_section_refs_stops_at_lisataan() -> None:
    """lisätään clause section numbers must NOT leak into muutetaan targets.

    Regression: 2024/917 johtolause has lisätään 1 luvun 4 §:ään.  Before the
    fix, that §4 was captured as a muutetaan target, which triggered the
    recycle guard and prevented kumotaan expiry for ch9 §§4–9.
    """
    johto = (
        "Eduskunnan päätöksen mukaisesti "
        "kumotaan lain (1308/2023) 9 luvun 4–9 §, "
        "muutetaan 6 luvun 4 § ja 5 luvun 7 §, sekä "
        "lisätään 1 luvun 4 §:ään uusi 2 momentti"
    )
    got = _extract_muutetaan_section_refs(johto)
    # §4 and §7 from muutetaan clause are legitimate targets
    assert "4" in got
    assert "7" in got
    # §4 in lisätään clause must NOT inflate the muutetaan set
    # (both contain §4 but one is from muutetaan, one from lisätään — the set
    # cannot distinguish them; the key test is that the SIZE stays bounded and
    # no §§ from BEYOND the lisätään keyword appear)
    # Concretely: §§ from "1 luvun 4 §:ään uusi 2 momentti" (momentti-level)
    # should not add new items beyond what the muutetaan clause contributed.
    # The whole-section refs from the muutetaan clause are §4 (ch6) and §7 (ch5).
    assert got <= {"4", "7"}


def test_extract_muutetaan_chapter_section_map_expands_numeric_to_lettered_range() -> None:
    """A ``N―M x`` range in a muutetaan clause yields VALID base labels.

    Grammar-backed enumeration expands ``22―23 a`` to ``22, 23`` rather than the
    retired regex's bogus literal ``22-23a`` (which matched no real section, so
    the recycle guard could never recognise the section as recycled). NEW-better
    delta from the kumotaan regex→grammar demotion.
    """
    johto = (
        "kumotaan sairausvakuutuslain (364/63) 28 §:n 4 momentti, "
        "muutetaan 4 §:n 1 momentti, 22―23 a §"
    )
    got = _extract_muutetaan_chapter_section_map(johto)
    flat = {label for labels in got.values() for label in labels}
    assert "22-23a" not in flat  # no bogus unexpanded range literal
    assert {"22", "23"} <= flat


def test_extract_muutetaan_chapter_section_map_chapter_scoped() -> None:
    """Muutetaan with chapter markers: sections are bucketed per chapter."""
    johto = (
        "muutetaan 6 luvun 4 § ja 15 §:n 6 momentti, "
        "5 luvun 7 § ja 8 §:n 3 ja 4 momentti"
    )
    got = _extract_muutetaan_chapter_section_map(johto)
    # §4 belongs to chapter 6 (whole-section)
    assert "4" in got.get("6", [])
    # §7 belongs to chapter 5 (whole-section)
    assert "7" in got.get("5", [])
    # §15 (momentti-level) and §8 (momentti-level) should not appear as whole-sections
    assert "15" not in got.get("6", [])
    assert "8" not in got.get("5", [])


def test_extract_muutetaan_chapter_section_map_stops_at_lisataan() -> None:
    """lisätään clause must not contribute sections to the muutetaan map."""
    johto = (
        "muutetaan 6 luvun 4 §, sekä "
        "lisätään 1 luvun 4 §:ään uusi 2 momentti"
    )
    got = _extract_muutetaan_chapter_section_map(johto)
    # §4 in chapter 6 is a legitimate muutetaan target
    assert "4" in got.get("6", [])
    # Chapter 1 must not appear — lisätään text was cut off
    assert "1" not in got


def test_muutetaan_chap_map_does_not_cross_chapter_on_recycle_guard() -> None:
    """Chapter-aware recycle guard: same section number in DIFFERENT chapters must
    NOT trigger the recycle guard (2024/917 regression pattern for 2023/1308).

    kumotaan: ch9 §§4–9
    muutetaan: ch6 §4, ch5 §7  (different chapters — not a recycle!)

    The guard must leave _kumotaan_labels intact (all of 4–9 should be eligible
    for expiry override).
    """
    johto = (
        "Eduskunnan päätöksen mukaisesti "
        "kumotaan lain (1308/2023) 9 luvun 4–9 §, "
        "muutetaan 1 luvun 10 § sekä 6 luvun 4 § ja 5 luvun 7 §, sekä "
        "lisätään 1 luvun 4 §:ään uusi 2 momentti seuraavasti:"
    )
    kum_map = _extract_kumotaan_chapter_section_map(johto)
    mut_map = _extract_muutetaan_chapter_section_map(johto)

    # Kumotaan ch9 has §§4–9
    assert set(kum_map.get("9", [])) == {"4", "5", "6", "7", "8", "9"}

    # Muutetaan has §4 in ch6, §7 in ch5 — NOT in ch9
    assert "4" in mut_map.get("6", [])
    assert "7" in mut_map.get("5", [])
    assert "9" not in mut_map.get("9", [])

    # Chapter-aware intersection for ch9: kum_ch9 ∩ mut_ch9 = empty
    kum_ch9 = set(kum_map.get("9", []))
    mut_ch9 = {s.lower() for s in mut_map.get("9", [])}
    recycled = {s for s in kum_ch9 if s.lower() in mut_ch9}
    assert recycled == set(), (
        f"False-positive recycle guard triggered for ch9 sections {recycled}; "
        "ch9 §§4–9 should all be eligible for expiry override"
    )
    result = kumotaan_recycle_guard_result(johto)
    assert result.chapter_aware is True
    assert result.recycled_labels == ()
    assert result.filtered_labels == ("4", "5", "6", "7", "8", "9")


def test_kumotaan_recycle_guard_result_surfaces_same_chapter_exclusion() -> None:
    johto = (
        "Eduskunnan päätöksen mukaisesti "
        "kumotaan lain (100/2000) 9 luvun 4–6 §, "
        "muutetaan 9 luvun 4 § ja 10 luvun 6 § seuraavasti:"
    )

    result = kumotaan_recycle_guard_result(johto)

    assert result.fired is True
    assert result.chapter_aware is True
    assert result.original_labels == ("4", "5", "6")
    assert result.recycled_labels == ("4",)
    assert result.filtered_labels == ("5", "6")
    detail = result.finding_detail()
    assert detail["rule_id"] == "fi_kumotaan_muutetaan_recycle_guard"
    assert detail["recycled_labels"] == ("4",)
    assert ("9", ("4", "5", "6")) in result.kumotaan_chapter_map
    assert ("9", ("4",)) in result.muutetaan_chapter_map


def test_extract_kumotaan_container_refs_keeps_trailing_history_citation() -> None:
    johto = (
        "Tällä asetuksella kumotaan mielenterveysasetuksen (1247/1990) 1 § ja 2 a luku, "
        "sellaisina kuin ne ovat, 1 § asetuksessa 1646/2009 sekä 2 a luku asetuksessa 1282/2000."
    )

    got = _extract_kumotaan_container_refs(johto)

    assert got["chapter"] == ["2a"]
    assert got["part"] == []


def test_apply_uncovered_kumotaan_retries_covered_container_when_still_present() -> None:
    ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2a",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="6a"),
                    IRNode(kind=IRNodeKind.SECTION, label="6b"),
                ),
            ),
        ),
    )
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    ops = [AmendmentOp(op_id="", op_type=OpType.REPEAL, target_kind=TargetKind.CHAPTER, target_section="2a")]
    johto = "Tällä asetuksella kumotaan mielenterveysasetuksen 2 a luku."

    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=ops,
            johto=johto,
            amendment_id="2022/1386",
        )
    ).state

    assert result.find("chapter", "2a") is None


def test_apply_uncovered_kumotaan_applies_vts_repeal_without_kumotaan_johtolause() -> None:
    ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="24", children=()),
            IRNode(kind=IRNodeKind.SECTION, label="24a", children=()),
        ),
    )
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    ops = [
        AmendmentOp(
            op_id="vts_24a",
            op_type=OpType.REPEAL,
            target_kind=TargetKind.SECTION,
            target_section="24a",
            voimaantulo_repeal=True,
        )
    ]
    johto = "Eduskunnan päätöksen mukaisesti säädetään:"

    lo_ops: list[Any] = []
    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=ops,
            johto=johto,
            amendment_id="2023/739",
        ),
        KumotaanRecoverySinks(lo_ops_out=lo_ops),
    ).state

    sec24a = result.find_section("24a")
    assert sec24a is not None
    assert sec24a.attrs.get("lawvm_repeal_placeholder") == "1"
    assert [op.witness_rule_id for op in lo_ops] == [FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID]


def test_apply_uncovered_kumotaan_typed_records_missing_section_skip_finding() -> None:
    ir = IRNode(kind=IRNodeKind.BODY, children=())
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    findings: list[Finding] = []

    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=[],
            johto="Tällä lailla kumotaan 9 §.",
            amendment_id="2017/276",
        ),
        KumotaanRecoverySinks(findings_out=findings),
    )

    assert result.state.ir == state.ir
    assert any(
        f.kind == "APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED"
        and f.detail.get("reason") == "kumotaan_missing_section_target"
        and f.detail.get("target_section") == "9"
        for f in findings
    )


def test_process_muutoslaki_applies_cross_statute_vts_repeal_without_payload_ir() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("1986/506")
    assert orig is not None

    ctx = StatuteContext.from_xml(orig, lambda text, kind: text)
    state = ReplayState(ir=ctx.base_ir)

    phase = process_muutoslaki(
        "2024/1049",
        state,
        ctx,
        replay_mode="legal_pit",
        parent_id="1986/506",
        corpus=corpus,
    )

    assert phase.output.find_section("2") is None
    rejected = [
        finding
        for finding in phase.findings()
        if finding.kind == "ELAB.STRICT_REJECTED_OPERATION"
    ]
    assert not any(
        finding.detail.get("reason_code") == "UNSUPPORTED_PAYLOAD_MISSING_PAYLOAD_IR"
        for finding in rejected
    )


def test_process_muutoslaki_projects_vts_skipped_targets_as_findings(monkeypatch) -> None:
    from lawvm.finland.vts import VTS_SKIPPED_TARGET_RULE_ID, VtsSkippedTarget

    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    def fake_extract_vts_repeals_fallback(*_args, skipped_targets_out=None, **_kwargs):
        assert skipped_targets_out is not None
        skipped_targets_out.append(
            VtsSkippedTarget(
                rule_id=VTS_SKIPPED_TARGET_RULE_ID,
                reason_code="unsafe_kohta_only_bare_section_parse",
                source_reason="whole-section repeal suppressed",
                source_statute="1996/1261",
                source_excerpt="6 §:n 3 kohta.",
                target_section="6",
            )
        )
        return []

    monkeypatch.setattr("lawvm.finland.process_pipeline.extract_vts_repeals_fallback", fake_extract_vts_repeals_fallback)
    monkeypatch.setattr("lawvm.finland.process_pipeline.normalize_and_compile_ops", lambda *_args, **_kwargs: PhaseResult(output=[]))
    monkeypatch.setattr("lawvm.finland.process_pipeline.compile_amendment_ops", lambda *_args, **_kwargs: PhaseResult(output=[]))
    monkeypatch.setattr("lawvm.finland.process_pipeline._apply_ops_to_tree_typed", lambda request, _sinks: request.state)

    phase = process_muutoslaki(
        "1996/1261",
        state,
        ctx,
        replay_mode="legal_pit",
        parent_id="1996/1261",
        corpus=_corpus_store({"1996/1261": _vts_skipped_process_muutoslaki_xml()}),
    )

    findings = [
        finding
        for finding in phase.findings()
        if finding.kind == VTS_SKIPPED_TARGET_RULE_ID
    ]
    assert len(findings) == 1
    assert findings[0].role == "observation"
    assert findings[0].blocking is False
    assert findings[0].stage == "frontend_extraction"
    assert findings[0].detail["reason_code"] == "unsafe_kohta_only_bare_section_parse"
    assert findings[0].detail["target_section"] == "6"


def test_resolve_applicable_amendment_records_re_admits_oracle_reflected_source_vts_child() -> None:
    corpus = _corpus_store(
        {
            "1991/806": b"""
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <dateEntryIntoForce date="1991-06-01"/>
              <meta><identification><FRBRManifestation><FRBRdate date="1991-05-10" name="dateIssued"/></FRBRManifestation></identification></meta>
              <docTitle>806/1991</docTitle>
            </akn>
            """,
            "1993/872": b"""
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <dateEntryIntoForce date="1993-12-01"/>
              <meta><identification><FRBRManifestation><FRBRdate date="1993-10-15" name="dateIssued"/></FRBRManifestation></identification></meta>
              <docTitle>872/1993</docTitle>
            </akn>
            """,
            "1994/1264": b"""
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <dateEntryIntoForce date="1995-01-01"/>
              <meta><identification><FRBRManifestation><FRBRdate date="1994-12-16" name="dateIssued"/></FRBRManifestation></identification></meta>
              <docTitle>1264/1994</docTitle>
            </akn>
            """,
            "2024/1049": b"""
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <dateEntryIntoForce date="2025-01-01"/>
              <meta><identification><FRBRManifestation><FRBRdate date="2024-12-30" name="dateIssued"/></FRBRManifestation></identification></meta>
              <docTitle>1049/2024</docTitle>
            </akn>
            """,
        }
    )

    class _Selector:
        mode = SimpleNamespace(value="latest_cached_editorial")

    import lawvm.finland.amendment_selection as selection_mod

    orig_children = selection_mod.amendment_children_by_parent
    orig_edges = selection_mod.amendment_child_edges_by_parent
    orig_reflected = selection_mod.get_consolidated_oracle_reflected_source_vts_children
    try:
        selection_patch = cast(Any, selection_mod)
        selection_patch.amendment_children_by_parent = lambda: {"1986/506": ["1991/806", "1993/872", "1994/1264", "2024/1049"]}
        selection_patch.amendment_child_edges_by_parent = lambda: {
            "1986/506": [
                ("1991/806", "oracle_amendedBy"),
                ("1993/872", "oracle_amendedBy"),
                ("1994/1264", "oracle_amendedBy"),
                ("2024/1049", "source_vts_explicit"),
            ]
        }
        selection_patch.get_consolidated_oracle_reflected_source_vts_children = lambda _parent_id, corpus=None, selector=None: {"2024/1049"}
        records, cutoff_date, oracle_version = selection_mod.resolve_applicable_amendment_records(
            "1986/506",
            "legal_pit",
            corpus=corpus,
            selector=cast(Any, _Selector()),
        )
    finally:
        selection_mod.amendment_children_by_parent = orig_children
        selection_mod.amendment_child_edges_by_parent = orig_edges
        selection_mod.get_consolidated_oracle_reflected_source_vts_children = orig_reflected

    assert oracle_version == "1994/1264"
    assert cutoff_date == dt.date(2025, 1, 1)
    assert [record["statute_id"] for record in records] == ["1991/806", "1993/872", "1994/1264", "2024/1049"]
    assert records[-1]["selection_basis"] == "oracle_editorial_repeal_stub_override"
    assert records[-1]["edge_kind"] == "source_vts_explicit"


def test_oracle_ref_body_surface_excludes_amendment_history_metadata() -> None:
    from lawvm.finland.corpus import _oracle_ref_is_body_surface

    root = etree.fromstring(
        """
        <akomaNtoso>
          <act>
            <meta>
              <proprietary>
                <amendedBy>
                  <statuteReference><ref href="/akn/fi/act/statute/2023/741">741/2023</ref></statuteReference>
                </amendedBy>
              </proprietary>
            </meta>
            <preface>
              <block eId="note_1">
                Ks. L <ref href="/akn/fi/act/statute/2024/1049">1049/2024</ref> voimaantulosäännös.
              </block>
            </preface>
          </act>
        </akomaNtoso>
        """.encode()
    )

    refs = root.findall(".//ref")

    assert not _oracle_ref_is_body_surface(refs[0])
    assert _oracle_ref_is_body_surface(refs[1])


def test_replay_xml_1986_506_applies_oracle_reflected_cross_statute_vts_repeal() -> None:
    ir = pinned_replay("1986/506", oracle_version="19941264")

    section_2 = ir.find_section("2")
    assert section_2 is not None
    assert section_2.attrs.get("lawvm_repeal_placeholder") == "1"
    assert [child.kind for child in section_2.children] == [IRNodeKind.NUM]


def test_apply_uncovered_kumotaan_does_not_promote_granular_vts_repeal_to_section() -> None:
    ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="8 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Muut haitallisten aineiden kuljetukset"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(IRNode(kind=IRNodeKind.CONTENT, text="Voimassa oleva momentti."),),
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                children=(IRNode(kind=IRNodeKind.CONTENT, text="Kumottava momentti."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    ops = [
        AmendmentOp(
            op_id="vts_8_m3",
            op_type=OpType.REPEAL,
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_chapter="2",
            target_paragraph=3,
            voimaantulo_repeal=True,
        )
    ]
    johto = "Tällä lailla kumotaan 2 luvun 8 §:n 3 momentti."

    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=ops,
            johto=johto,
            amendment_id="2017/275",
        )
    ).state

    sec8 = result.find_section("8", "2")
    assert sec8 is not None
    assert sec8.attrs.get("lawvm_repeal_placeholder") != "1"
    assert any(child.kind is IRNodeKind.HEADING for child in sec8.children)
    assert [child.label for child in sec8.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "3"]


def test_apply_uncovered_kumotaan_skips_section_already_recovered_in_same_amendment() -> None:
    ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="5 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Valmiussuunnitelma öljyvahingon varalle"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(IRNode(kind=IRNodeKind.CONTENT, text="Korvattu sisältö."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    findings: list[Finding] = []
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_5",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "1"), ("section", "5"))),
            payload=next(child for child in ir.children[0].children if child.kind is IRNodeKind.SECTION),
            source=OperationSource(
                statute_id="2017/275",
                title="Test",
                enacted="2017-05-05",
                effective="2017-05-05",
            ),
            group_id="finland-johto:2017/275",
        )
    ]

    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=[],
            johto="Tällä lailla kumotaan 1 luvun 5 §.",
            amendment_id="2017/275",
            op_source=OperationSource(
                statute_id="2017/275",
                title="Test",
                enacted="2017-05-05",
                effective="2017-05-05",
            ),
        ),
        KumotaanRecoverySinks(
            lo_ops_out=lo_ops,
            findings_out=findings,
        ),
    ).state

    sec5 = result.find_section("5", "1")
    assert sec5 is not None
    assert sec5.attrs.get("lawvm_repeal_placeholder") != "1"
    assert "Valmiussuunnitelma öljyvahingon varalle" in irnode_to_text(sec5)
    assert all(op.op_id != "uncovered_repeal_5" for op in lo_ops)
    assert any(
        f.kind == "APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED"
        and f.detail.get("reason") == "kumotaan_section_already_covered"
        and f.detail.get("target_section") == "5"
        for f in findings
    )


def test_apply_uncovered_kumotaan_records_missing_section_skip_finding() -> None:
    ir = IRNode(kind=IRNodeKind.BODY, children=())
    state = _replay_state(ir)
    ctx = _statute_context(ir)
    findings: list[Finding] = []

    result = _apply_uncovered_kumotaan_typed(
        KumotaanRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=[],
            johto="Tällä lailla kumotaan 9 §.",
            amendment_id="2017/276",
        ),
        KumotaanRecoverySinks(findings_out=findings),
    ).state

    assert result.ir == state.ir
    assert any(
        f.kind == "APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED"
        and f.detail.get("reason") == "kumotaan_missing_section_target"
        and f.detail.get("target_section") == "9"
        for f in findings
    )


def test_fallback_recovers_shifted_subsection_insert_and_retargeted_replace() -> None:
    johto = (
        "muutetaan 31 päivänä heinäkuuta 1947 annetun lahjanlupauslain (625/47) "
        "3§:n 2 momentti ja 4§ sekä lisätään 3§:ään uusi 2 momentti, jolloin "
        "muutettu 2 momentti siirtyy 3 momentiksi, seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "3", 2) in got
    assert ("REPLACE", "3", 3) in got
    assert ("REPLACE", "4", None) in got


def test_stabilize_insert_order_prefers_insert_first_when_replace_target_only_exists_after_shift() -> None:
    ops = [
        AmendmentOp(
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=2,
        ),
    ]
    target_ctx = SimpleNamespace(
        subsection_slots=(
            SimpleNamespace(label="1"),
            SimpleNamespace(label="2"),
        )
    )

    got = _stabilize_insert_order(ops, cast(Any, target_ctx))

    assert [(op.op_type, op.target_cols.target_paragraph) for op in got] == [
        ("INSERT", 2),
        ("REPLACE", 3),
    ]


def test_stabilize_insert_order_keeps_replace_first_when_live_target_exists() -> None:
    ops = [
        AmendmentOp(
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=5,
        ),
    ]
    target_ctx = SimpleNamespace(
        subsection_slots=tuple(SimpleNamespace(label=str(i)) for i in range(1, 5))
    )

    got = _stabilize_insert_order(ops, cast(Any, target_ctx))

    assert [(op.op_type, op.target_cols.target_paragraph) for op in got] == [
        ("REPLACE", 3),
        ("INSERT", 3),
        ("INSERT", 5),
    ]


def test_stabilize_insert_order_moves_same_wave_subsection_renumber_after_rebased_replace_family() -> None:
    ops = [
        AmendmentOp(
            op_type=OpType.RENUMBER,
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=2,
        ),
        AmendmentOp(
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=3,
            target_guessing_provenance_tags=("rebase_duplicate_target_shifted_replace",),
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=2,
        ),
    ]
    target_ctx = SimpleNamespace(
        subsection_slots=tuple(SimpleNamespace(label=str(i)) for i in range(1, 4))
    )

    got = _stabilize_insert_order(ops, cast(Any, target_ctx))

    assert [(op.op_type, op.target_cols.target_paragraph) for op in got] == [
        ("INSERT", 2),
        ("REPLACE", 3),
        ("RENUMBER", 2),
    ]


def test_subsection_insert_fallback_recovers_large_johtolause_moment_inserts() -> None:
    johto = (
        "kumotaan kilpailunrajoituksista 27 päivänä toukokuuta 1992 annetun lain "
        "(480/1992) 11 b §:n 5 momentti, 12 §:n 3 ja 4 momentti, 16, 19, 19 a ja "
        "19 b §, muutetaan 3 §:n 2 momentti, 4―9 §, 11 a §:n 1 momentti, 11 g §, "
        "12 §:n 1 momentti, 14 §:n 2 momentti, 15 §:n 1 momentti, 17 ja 18 §, "
        "18 a §:n 1 momentti, 20 §, 21 §:n 1 ja 2 momentti ja 22 §, lisätään "
        "lakiin uusi 1 a ja 10 b §, laista mainitulla lailla 303/1998 kumotun "
        "13 §:n tilalle uusi 13 §, 15 §:ään, sellaisena kuin se on mainitussa "
        "laissa 1529/2001, uusi 4 momentti, lakiin uusi 20 a ja 20 b § sekä "
        "29 §:ään uusi 2 momentti seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "15", 4) in got
    assert ("INSERT", "29", 2) in got


def test_subsection_insert_fallback_keeps_same_section_scope_for_trailing_insert_continuation() -> None:
    johto = (
        "muutetaan työntekijän eläkelain voimaanpanolain (396/2006) 26 §:n 3 momentti, "
        "sellaisena kuin se on laissa 1428/2011, sekä lisätään 26 §:ään, sellaisena "
        "kuin se on osaksi laissa 1428/2011, uusi 3 momentti, jolloin muutettu 3 "
        "momentti siirtyy 4 momentiksi, ja uusi 5 momentti seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "26", 3) in got
    assert ("INSERT", "26", 5) in got


def test_subsection_insert_fallback_expands_plural_momentti_insert_after_provenance() -> None:
    johto = (
        "muutetaan 22 §:n 1 momentti sekä lisätään 22 §:ään, sellaisena kuin se on "
        "osaksi mainitussa asetuksessa 693/2003, uusi 5 ja 6 momentti seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "22", 5) in got
    assert ("INSERT", "22", 6) in got


def test_subsection_insert_fallback_expands_mom_abbreviation_before_section_insert() -> None:
    johto = (
        "muutetaan 2 §:n 1 mom. sekä lisätään 2 §:ään uusi 9 ja 10 mom. "
        "ja asetukseen uusi 27 § seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "2", 9) in got
    assert ("INSERT", "2", 10) in got
    assert not any(op.target_cols.target_section == "27" for op in ops)


def test_subsection_insert_fallback_stops_at_next_chapter_scoped_section_ref() -> None:
    johto = (
        "lisätään 6 luvun 1 §:ään, sellaisena kuin se on osaksi laeissa 821/2017, "
        "868/2018 ja 406/2019, uusi 11 momentti, 6 luvun 3 §:n 1 momenttiin uusi "
        "4 kohta ja pykälään, sellaisena kuin se on osaksi laissa 821/2017, uusi "
        "3 momentti sekä lukuun uusi 7 § seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("INSERT", "1", 11) in got
    assert ("INSERT", "1", 3) not in got


def test_subsection_insert_fallback_coverage_surfaces_unclassified_bounded_gap() -> None:
    johto = "lisätään 5 §:ään kuitenkin uusi 2 momentti"

    result = parse_ops_fallback_heuristic_with_coverage(
        johto,
        source_artifact_id="2020/1",
    )

    assert [(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in result.ops] == [
        ("INSERT", "5", 2),
    ]
    assert len(result.regex_recognition_coverage) == 1
    coverage = result.regex_recognition_coverage[0].to_dict()
    assert coverage["recognizer_id"] == "fi_insert_subsection_fallback"
    assert coverage["coverage_status"] == "unclassified_gap"
    assert coverage["semantic_slots"] == {
        "action": "INSERT",
        "target_unit_kind": "subsection",
        "target_section": "5",
        "target_subsections": [2],
    }
    assert coverage["ignored_spans"] == [
        {
            "span": [17, 27],
            "classification": "unclassified",
            "text_preview": "kuitenkin ",
            "could_alter_meaning": True,
        }
    ]
    assert coverage["required_proofs"] == ["regex_skipped_span_classification"]
    assert "bounded_wildcard_as_semantic_proof" in coverage["forbidden_shortcuts"]


def test_subsection_insert_fallback_coverage_marks_plain_connector_classified() -> None:
    johto = "lisätään 5 §:ään uusi 2 momentti"

    result = parse_ops_fallback_heuristic_with_coverage(johto)

    assert [(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in result.ops] == [
        ("INSERT", "5", 2),
    ]
    assert len(result.regex_recognition_coverage) == 1
    coverage = result.regex_recognition_coverage[0].to_dict()
    assert coverage["coverage_status"] == "fully_classified"
    assert coverage["ignored_spans"] == []
    assert coverage["required_proofs"] == []


def test_item_insert_fallback_coverage_surfaces_unclassified_bounded_gap() -> None:
    johto = "lisätään 5 §:n 2 momenttiin kuitenkin uusi 4 kohta"

    result = parse_ops_fallback_heuristic_with_coverage(
        johto,
        source_artifact_id="2020/2",
    )

    got = {
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in result.ops
    }
    assert ("INSERT", "5", 2, "4") in got
    assert len(result.regex_recognition_coverage) == 1
    coverage = result.regex_recognition_coverage[0].to_dict()
    assert coverage["recognizer_id"] == "fi_insert_item_fallback"
    assert coverage["coverage_status"] == "unclassified_gap"
    assert coverage["semantic_slots"] == {
        "action": "INSERT",
        "target_unit_kind": "item",
        "target_section": "5",
        "target_subsection": 2,
        "target_items": ["4"],
    }
    assert coverage["ignored_spans"] == [
        {
            "span": [28, 38],
            "classification": "unclassified",
            "text_preview": "kuitenkin ",
            "could_alter_meaning": True,
        }
    ]
    assert coverage["required_proofs"] == ["regex_skipped_span_classification"]
    assert "bounded_wildcard_as_semantic_proof" in coverage["forbidden_shortcuts"]


def test_item_insert_fallback_coverage_marks_plain_connector_classified() -> None:
    johto = "lisätään 5 §:n 2 momenttiin uusi 4 kohta"

    result = parse_ops_fallback_heuristic_with_coverage(johto)

    got = {
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in result.ops
    }
    assert ("INSERT", "5", 2, "4") in got
    assert len(result.regex_recognition_coverage) == 1
    coverage = result.regex_recognition_coverage[0].to_dict()
    assert coverage["recognizer_id"] == "fi_insert_item_fallback"
    assert coverage["coverage_status"] == "fully_classified"
    assert coverage["ignored_spans"] == []
    assert coverage["required_proofs"] == []


def test_item_insert_fallback_recovers_historical_item_before_kohta_wording() -> None:
    johto = (
        "lisätään 30 päivänä maaliskuuta 1973 annetun vuosilomalain (272/73) "
        "3 §:n 5 momenttiin, sellaisena kuin se on osittain muutettuna "
        "24 päivänä helmikuuta ja 30 päivänä maaliskuuta 1978 annetuilla laeilla "
        "(153 ja 233/78), uusi näin kuuluva 11 a kohta:"
    )

    result = parse_ops_fallback_heuristic_with_coverage(johto)

    got = {
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in result.ops
    }
    assert got == {("INSERT", "3", 5, "11a")}


def test_normalize_compile_ops_converts_historical_item_insert_wording() -> None:
    johto = (
        "lisätään 30 päivänä maaliskuuta 1973 annetun vuosilomalain (272/73) "
        "3 §:n 5 momenttiin, sellaisena kuin se on osittain muutettuna "
        "24 päivänä helmikuuta ja 30 päivänä maaliskuuta 1978 annetuilla laeilla "
        "(153 ja 233/78), uusi näin kuuluva 11 a kohta:"
    )

    phase = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=etree.fromstring("<root/>"),
        master=ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=())),
        amendment_id="1979/276",
        source_title="Laki vuosilomalain 3 §:n muuttamisesta.",
        used_preamble_body_fallback=False,
        parent_id="1973/272",
        strict_profile=None,
    )

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in phase.output
    ] == [("INSERT", "3", 5, "11a")]


def test_combined_chapter_and_section_insert_owned_by_parser() -> None:
    # The container-insert regex fallback was retired; the PEG owns the combined
    # ``lakiin uusi N luku ja M, … §`` chapter+section insert natively.
    johto = "lisätään lakiin uusi 2 luku ja 15, 16 ja 17 § seuraavasti:"

    ops = parse_clause(johto).parsed_ops

    got = {(op.verb, op.kind, op.number) for op in ops}
    assert {
        ("L", "L", "2"),
        ("L", "P", "15"),
        ("L", "P", "16"),
        ("L", "P", "17"),
    } <= got


def test_chapter_insert_owned_by_parser() -> None:
    # The container-insert regex fallback was retired; the PEG owns the bare
    # ``lakiin uusi N luku`` chapter insert natively.
    johto = "lisätään lakiin uusi 3 luku"

    ops = parse_clause(johto).parsed_ops

    assert [(op.verb, op.kind, op.number) for op in ops] == [("L", "L", "3")]


def test_insert_section_fallback_expands_letter_suffix_range_inside_lakiin_uusi_clause() -> None:
    johto = "lisätään lakiin uusi 149 a–149 c ja 211 b § seuraavasti:"

    ops = parse_clause(johto).parsed_ops

    # verb 'L' = lisätään (INSERT), kind 'P' = pykälä (§/section).
    assert [(op.verb, op.kind, op.number) for op in ops] == [
        ("L", "P", "149a"),
        ("L", "P", "149b"),
        ("L", "P", "149c"),
        ("L", "P", "211b"),
    ]


def test_insert_section_fallback_keeps_law_level_reinstatement_before_range_clause() -> None:
    johto = (
        "lisätään lakiin siitä lailla 1068/2016 kumotun 149 §:n tilalle uusi 149 § "
        "sekä lakiin uusi 149 a–149 c ja 211 b § seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops

    # The reinstated 149 § (kumotun ... tilalle uusi) must precede the new range.
    assert [(op.verb, op.kind, op.number) for op in ops] == [
        ("L", "P", "149"),
        ("L", "P", "149a"),
        ("L", "P", "149b"),
        ("L", "P", "149c"),
        ("L", "P", "211b"),
    ]


def test_get_johtolause_keeps_insertions_originals_blocks() -> None:
    xml = b"""
    <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <preamble>
        <formula name="enactingClause">
          <blockContainer>
            <block name="insertions"><i>lisataan</i> 11 f pykalaan,</block>
            <block name="insertions-originals">sellaisena kuin se on laissa 303/1998, uusi 4 momentti, seuraavasti:</block>
          </blockContainer>
        </formula>
      </preamble>
    </act>
    """

    johto = " ".join(get_johtolause(xml).split())

    assert "11 f pykalaan" in johto
    assert "uusi 4 momentti" in johto


def test_fallback_expands_repealed_subsection_range() -> None:
    johto = (
        "Tällä asetuksella kumotaan 17 päivänä heinäkuuta 1959 annetun "
        "liikennevakuutusasetuksen (324/1959) 9 §:n 2―5 momentti."
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("REPEAL", "9", 2) in got
    assert ("REPEAL", "9", 3) in got
    assert ("REPEAL", "9", 4) in got
    assert ("REPEAL", "9", 5) in got


def test_fallback_splits_mixed_repeal_and_replace_clause() -> None:
    johto = (
        "kumotaan täydentävien ehtojen hyvän maatalouden ja ympäristön vaatimusten "
        "sekä ympäristöön liittyvien lakisääteisten hoitovaatimusten valvonnasta "
        "31 päivänä toukokuuta 2007 annetun valtioneuvoston asetuksen (636/2007) "
        "14 §:n 1 momentti, sellaisena kuin se on asetuksessa 359/2009, sekä "
        "muutetaan 1 §:n 2 momentti sekä 5―7 ja 13 §,"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert ("REPEAL", "14", 1) in got
    assert ("REPLACE", "1", 2) in got
    assert ("REPLACE", "5", None) in got
    assert ("REPLACE", "6", None) in got
    assert ("REPLACE", "7", None) in got
    assert ("REPLACE", "13", None) in got


def test_extract_replace_ops_from_muutetaan_tail_recovers_mixed_section_and_moment_targets() -> None:
    johto = "kumotaan vapaakuntakokeilusta annetun lain 5 §, muutetaan 2 §:n 2 momentti ja 15 § seuraavasti:"

    ops = _extract_replace_ops_from_muutetaan_tail(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph) for op in ops}

    assert got == {
        ("REPLACE", "2", 2),
        ("REPLACE", "15", None),
    }


def test_parser_recovers_chapter_and_chapter_scoped_inserts() -> None:
    # The container-insert regex fallback was retired; the PEG owns the chapter
    # inserts (``lakiin uusi 25 a luku`` / ``uusi 30 a luku``) and the
    # chapter-scoped section insert (``26 lukuun uusi 14 a §``) natively.
    johto = (
        "kumotaan oikeudenkäymiskaaren 26 luvun 1 a §, 1 b § ja 2 a §, muutetaan "
        "2 luvun 8 §:n 2 momentin 1 kohta, 25 luvun 14 b §, 26 luvun otsikko sekä 2, 3 ja 13-16 §, "
        "lisätään 25 luvun 15 §:n 1 momenttiin uusi 4 a kohta, lakiin uusi 25 a luku, "
        "26 lukuun uusi 14 a § sekä lakiin uusi 30 a luku seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    got = {(op.verb, op.kind, op.chapter, op.number) for op in ops}

    assert ("L", "L", "", "25a") in got
    assert ("L", "L", "", "30a") in got
    assert ("L", "P", "26", "14a") in got


def test_fallback_recovers_explicit_item_insert_and_prunes_shadowed_parent_subsection() -> None:
    johto = (
        "muutetaan 49 a §:n 1 momentin 9 kohta, lisätään 49 a §:n 1 momenttiin, "
        "sellaisena kuin se on laissa 108/2019, uusi 10 kohta, seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item) for op in ops}

    assert ("INSERT", "49a", 1, "10") in got
    assert ("INSERT", "49a", 1, None) not in got


def test_fallback_preserves_explicit_subsection_and_section_inserts_in_mixed_clause() -> None:
    johto = (
        "muutetaan ajoneuvolain (82/2021) 127 §, sellaisena kuin se on laissa (132/2024), "
        "ja lisätään 1 a §:ään, sellaisena kuin se on laissa 493/2023, uusi 5 momentti "
        "sekä lakiin uusi 83 a § seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    # verb 'M' = muutetaan (REPLACE), verb 'L' = lisätään (INSERT).
    got = {(op.verb, op.number, op.momentti) for op in ops}

    assert ("M", "127", 0) in got
    assert ("L", "1a", 5) in got
    assert ("L", "83a", 0) in got
    # 83 a § must be a fresh INSERT, never a REPLACE of an existing §.
    assert ("M", "83a", 0) not in got


def test_repeal_reenact_normalization_uses_typed_provenance_without_hint() -> None:
    got = normalize_group_ops_for_repeal_reenact(
        [
            AmendmentOp(op_id="rep", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="4"),
            AmendmentOp(op_id="ins", op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="4"),
        ]
    )

    assert len(got) == 1
    assert got[0].op_type == "REPLACE"
    assert got[0].extraction_provenance_tags == ("repeal_reenact_normalized",)


def test_repeal_reenact_normalization_leaves_multiple_repeals_unchanged() -> None:
    # Bug regression: amendment with kumotaan ... 2a, 4-7 § sekä muutetaan 2, 3 §
    # must NOT convert any repeal to replace — all repeals are pure repeals.
    ops = [
        AmendmentOp(op_id="rep_2a", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="2a"),
        AmendmentOp(op_id="rep_4", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="4"),
        AmendmentOp(op_id="rep_5", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="5"),
        AmendmentOp(op_id="repl_2", op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="2"),
        AmendmentOp(op_id="repl_3", op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3"),
    ]
    got = normalize_group_ops_for_repeal_reenact(ops)

    assert got is ops  # unchanged — same list object returned
    assert len(got) == 5
    assert got[0].op_type == "REPEAL"
    assert got[1].op_type == "REPEAL"
    assert got[2].op_type == "REPEAL"


def test_repeal_reenact_normalization_leaves_single_repeal_with_different_section_unchanged() -> None:
    # Single repeal of section "7" + replace of section "2" — different sections,
    # no re-enactment content for 7, so no conversion should happen.
    ops = [
        AmendmentOp(op_id="rep_7", op_type=OpType.REPEAL, target_kind=TargetKind.SECTION, target_section="7"),
        AmendmentOp(op_id="repl_2", op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="2"),
    ]
    got = normalize_group_ops_for_repeal_reenact(ops)

    assert got is ops  # unchanged
    assert got[0].op_type == "REPEAL"
    assert got[1].op_type == "REPLACE"


def test_append_compiled_group_ops_omits_resolution_hint_field() -> None:
    compiled_ops = []

    op = AmendmentOp(op_id="op0", op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="4")
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="4",
        target_chapter=None,
    )
    append_compiled_group_ops(
        compiled_ops,
        [rop],
    )

    assert len(compiled_ops) == 1
    assert "resolution_hint" not in compiled_ops[0]


def test_append_compiled_group_ops_serializes_resolved_scope_confidence() -> None:
    compiled_ops = []

    op = AmendmentOp(
        op_id="op0",
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="5",
        scope_provenance_tags=("chapter_scope_from_preamble",),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="4",
        target_chapter="5",
    )

    append_compiled_group_ops(compiled_ops, [rop])

    assert compiled_ops == [
        {
            "sequence": 1,
            "op_id": "op0",
            "action": "replace",
            "source_statute": "",
            "source_title": None,
            "provenance": serialized_provenance_from_bags(scope_tags=("chapter_scope_from_preamble",)),
            "witness_rule_id": None,
            "target_unit_kind": "section",
            "target_norm": "4",
            "target_chapter": "5",
            "target_part": "",
            "target_paragraph": "",
            "target_item": "",
            "target_special": "",
            "scope_source": "preamble",
            "scope_confidence": "inferred",
        }
    ]


def test_append_compiled_group_ops_prefers_stored_scope_confidence_over_sidecar_tags() -> None:
    compiled_ops = []

    op = AmendmentOp(
        op_id="op0",
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="5",
        scope_provenance_tags=("grouped_chapter_scope",),
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="5",
        ),
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="4",
        target_chapter="5",
    )

    append_compiled_group_ops(compiled_ops, [rop])

    assert serialized_provenance_bag(compiled_ops[0]["provenance"], ProvenanceBag.SCOPE) == ("grouped_chapter_scope",)
    assert compiled_ops[0]["scope_source"] == "explicit_chunk"
    assert compiled_ops[0]["scope_confidence"] == "explicit"


def test_duplicate_frontend_target_observations_flags_exact_duplicate_targets() -> None:
    ops = [
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(
            op_id="",
            op_type=OpType.INSERT,
            target_section="98",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.INSERT,
            target_section="98",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            target_paragraph=3,
        ),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="33", target_kind=TargetKind.SECTION, target_paragraph=1),
    ]

    got = _duplicate_frontend_target_observations(ops, "2020/766")

    assert _without_target_kind(got) == [
        Finding(
            kind="PARSE.DUPLICATE_TARGET_OP",
            role="observation",
            stage="frontend_ops",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "33",
                "target_chapter": "",
                "op_type": "REPLACE",
                "target_paragraph": None,
                "target_item": "",
                "target_special": "",
                "duplicate_count": 2,
            },
            blocking=False,
        ),
        Finding(
            kind="PARSE.DUPLICATE_TARGET_OP",
            role="observation",
            stage="frontend_ops",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "98",
                "target_chapter": "12",
                "op_type": "INSERT",
                "target_paragraph": 3,
                "target_item": "",
                "target_special": "",
                "duplicate_count": 2,
            },
            blocking=False,
        ),
    ]


def test_semantic_collapse_move_or_renumber_observations_flag_duplicate_move_clause_targets() -> None:
    ops = [
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="31", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="32", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="34", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="34", target_kind=TargetKind.SECTION),
    ]
    johto = "muutetaan 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun"

    got = sorted(
        _semantic_collapse_move_or_renumber_observations(ops, johto, "2020/766"),
        key=lambda item: str(item.detail.get("target_norm") or ""),
    )

    assert _without_target_kind(got) == [
        Finding(
            kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            role="observation",
            stage="frontend_extraction",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "33",
                "target_chapter": "",
                "collapse_kind": "move_to_chapter_clause",
                "destination_chapter": "5",
                "duplicate_replace_count": 2,
            },
            blocking=False,
        ),
        Finding(
            kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            role="observation",
            stage="frontend_extraction",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "34",
                "target_chapter": "",
                "collapse_kind": "move_to_chapter_clause",
                "destination_chapter": "5",
                "duplicate_replace_count": 2,
            },
            blocking=False,
        ),
    ]


def test_derive_features_reports_renumber_backref_features() -> None:
    johto = (
        "muutetaan II osan 1 luvun 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti, "
        "3 §:n numero 5:ksi ja mainittu pykälä"
    )

    result = parse_clause(johto)
    features = derive_features(johto, result.parsed_ops)

    assert "renumber" in features
    assert "backref_singular" in features
    assert "sub_ref" in features
    assert "part_ctx" in features


def test_fallback_move_clause_ops_preserves_move_target() -> None:
    johto = "muutetaan 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun"

    ops = parse_ops_fallback_heuristic(johto)

    assert ops
    assert len(ops) > 0


def test_enrich_ops_mints_deterministic_ids_for_blank_fallback_ops() -> None:
    muutos_tree = etree.fromstring("<akn><docTitle>Fallback test</docTitle></akn>")
    ops = [
        AmendmentOp(
            op_id="",
            op_type=OpType.INSERT,
            target_section="2",
            target_unit_kind="section",
        )
    ]

    got = _enrich_ops_from_amendment_tree(ops, "2024/1", muutos_tree, master=None)

    assert got[0].op_id
    assert got[0].op_id.startswith("fi:2024/1:")


def test_enrich_ops_preserves_johtolause_source_witness_on_legal_operation() -> None:
    muutos_tree = etree.fromstring("<akn><docTitle>Witness test</docTitle></akn>")
    johto = "kumotaan lain 2 § seuraavasti:"
    op = AmendmentOp(
        op_id="repeal-2",
        op_type=OpType.REPEAL,
        target_section="2",
        target_unit_kind="section",
        lo=LegalOperation(
            op_id="repeal-2",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "2"),)),
        ),
    )

    got = _enrich_ops_from_amendment_tree(
        [op],
        "2024/1",
        muutos_tree,
        master=None,
        johto=johto,
    )

    assert got[0].lo is not None
    assert got[0].lo.source is not None
    assert got[0].lo.source.raw_text == johto


def test_stamp_fallback_op_ids_mints_deterministic_ids_for_blank_ops() -> None:
    op = AmendmentOp(op_id="", op_type=OpType.INSERT, target_section="2", target_unit_kind="section")

    got = stamp_fallback_op_ids([op], "verify/1")

    assert got[0].op_id
    assert got[0].op_id.startswith("fi:verify/1:")


def test_extract_johtolause_legal_ops_preserves_renumber_clause_notes() -> None:
    johto = "muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti"

    got = extract_johtolause_legal_ops(johto)

    assert [op.action for op in got] == [StructuralAction.RENUMBER, StructuralAction.REPLACE]
    assert got[0].provenance_tags == ("renumber_clause",)
    assert got[1].provenance_tags == ("renumber_clause", "renumber_backref_clause")


def test_extract_johtolause_legal_ops_keeps_post_range_renumber_continuation() -> None:
    johto = "muutetaan 4 luvun 3–10 §:n numero 29–36:ksi sekä 11 §:n numero 52:ksi ja mainittu pykälä"

    got = extract_johtolause_legal_ops(johto)

    assert [op.target.path for op in got] == [
        (("chapter", "4"), ("section", "3")),
        (("chapter", "4"), ("section", "4")),
        (("chapter", "4"), ("section", "5")),
        (("chapter", "4"), ("section", "6")),
        (("chapter", "4"), ("section", "7")),
        (("chapter", "4"), ("section", "8")),
        (("chapter", "4"), ("section", "9")),
        (("chapter", "4"), ("section", "10")),
        (("chapter", "4"), ("section", "11")),
        (("chapter", "4"), ("section", "11")),
    ]
    assert got[0].provenance_tags == ("renumber_clause",)
    assert got[7].provenance_tags == ("renumber_clause",)
    assert got[8].provenance_tags == ("renumber_clause",)
    assert got[9].provenance_tags == ("renumber_clause", "renumber_backref_clause")
    # Direct renumber targets get action=StructuralAction.RENUMBER (jolloin annotations).
    # Backref "mainittu pykälä" resolves through MUUTTAA verb → "replace".
    assert all(op.action is StructuralAction.RENUMBER for op in got[:-1])
    assert got[-1].action is StructuralAction.REPLACE


def test_fallback_renumber_clause_returns_replace_ops() -> None:
    johto = "siirretään 2 § ja 3 § seuraavasti:"

    got = parse_ops_fallback_heuristic(johto)

    assert got
    assert all(op.op_type == "REPLACE" for op in got)


@LEGACY_MOVE_CLAUSE_RESIDUE
def test_extract_johtolause_legal_ops_continues_after_inline_move_clause_tail() -> None:
    johto = (
        "muutetaan 5 luvun otsikko, 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun, "
        "7 luvun otsikko, 47–49, 54, 56, 71, 72, 74, 78 ja 80–82 §"
    )

    got = extract_johtolause_legal_ops(johto)

    section_labels = [dict(op.target.path).get("section") for op in got if dict(op.target.path).get("section")]
    chapter_headings = [
        dict(op.target.path).get("chapter")
        for op in got
        if op.target.special is not None and op.target.special.value == "heading"
    ]
    moved = [
        op
        for op in got
        if dict(op.target.path).get("section") in {"33", "34"} and dict(op.target.path).get("chapter") == "5"
    ]

    assert chapter_headings == ["5", "7"]
    assert moved
    assert all(getattr(op, "move_clause_target_unit_kind", None) == "chapter" for op in moved)
    assert section_labels == [
        "31",
        "32",
        "33",
        "34",
        "33",
        "34",
        "47",
        "48",
        "49",
        "54",
        "56",
        "71",
        "72",
        "74",
        "78",
        "80",
        "81",
        "82",
    ]


def test_extract_johtolause_legal_ops_salvages_malformed_chapter_insert_surface() -> None:
    johto = "lisätään lakiin uusi 7 a § luku, 60 §:ään uusi 3 momentti, lakiin uusi 81 a–81 c ja 91 a §"

    got = extract_johtolause_legal_ops(johto)

    assert [op.action for op in got] == [
        StructuralAction.INSERT,
        StructuralAction.INSERT,
        StructuralAction.INSERT,
        StructuralAction.INSERT,
        StructuralAction.INSERT,
        StructuralAction.INSERT,
    ]
    assert got[0].target.path == (("chapter", "7a"),)
    assert got[1].target.path == (("section", "60"), ("subsection", "3"))
    assert got[2].target.path == (("section", "81a"),)
    assert got[3].target.path == (("section", "81b"),)
    assert got[4].target.path == (("section", "81c"),)
    assert got[5].target.path == (("section", "91a"),)


def test_extract_johtolause_legal_ops_expands_letter_suffix_range_with_hyphen_dash() -> None:
    johto = "lisätään lakiin uusi 17 a‐17 d § seuraavasti:"

    got = extract_johtolause_legal_ops(johto)

    assert [op.target.path for op in got] == [
        (("section", "17a"),),
        (("section", "17b"),),
        (("section", "17c"),),
        (("section", "17d"),),
    ]


def test_extract_johtolause_legal_ops_expands_alpha_start_to_plain_numeric_end_range() -> None:
    johto = "muutetaan 52 a-55 §"

    got = extract_johtolause_legal_ops(johto)

    assert [op.target.path for op in got] == [
        (("section", "52a"),),
        (("section", "53"),),
        (("section", "54"),),
        (("section", "55"),),
    ]


def test_extract_johtolause_legal_ops_keeps_alpha_start_numeric_end_range_inside_mixed_section_list() -> None:
    johto = "muutetaan 51 a §:n 2 momentin, 52 a-55 §:n, 56 §:n 1 momentin"

    got = extract_johtolause_legal_ops(johto)

    assert [op.target.path for op in got] == [
        (("section", "51a"), ("subsection", "2")),
        (("section", "52a"),),
        (("section", "53"),),
        (("section", "54"),),
        (("section", "55"),),
        (("section", "56"), ("subsection", "1")),
    ]


def test_semantic_collapse_move_or_renumber_observations_flag_renumber_backref_clauses() -> None:
    ops = [
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
            target_paragraph=1,
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="3",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="3",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
            target_paragraph=3,
        ),
    ]
    johto = (
        "muutetaan II osan 1 luvun 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti, "
        "3 §:n numero 5:ksi ja mainitun pykälän 3 momentti"
    )

    got = sorted(
        _semantic_collapse_move_or_renumber_observations(ops, johto, "2019/371"),
        key=lambda item: str(item.detail.get("target_norm") or ""),
    )

    assert _without_target_kind(got) == [
        Finding(
            kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            role="observation",
            stage="frontend_extraction",
            source_statute="2019/371",
            detail={
                "target_unit_kind": "section",
                "target_norm": "2",
                "target_chapter": "1",
                "collapse_kind": "renumber_backref_clause",
                "whole_section_replace_count": 1,
                "scoped_replace_count": 1,
            },
            blocking=False,
        ),
        Finding(
            kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            role="observation",
            stage="frontend_extraction",
            source_statute="2019/371",
            detail={
                "target_unit_kind": "section",
                "target_norm": "3",
                "target_chapter": "1",
                "collapse_kind": "renumber_backref_clause",
                "whole_section_replace_count": 1,
                "scoped_replace_count": 1,
            },
            blocking=False,
        ),
    ]


def test_scope_anchor_dependence_observations_flag_heuristic_scope_tags() -> None:
    ops = [
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="33",
            target_kind=TargetKind.SECTION,
            target_chapter="5",
            scope_provenance_tags=("chapter_scope_carry_forward",),
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="34",
            target_kind=TargetKind.SECTION,
            target_chapter="5",
            scope_provenance_tags=("grouped_chapter_scope", "chapter_scope_from_preamble"),
        ),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="34",
            target_kind=TargetKind.SECTION,
            target_chapter="5",
            scope_provenance_tags=("grouped_chapter_scope",),
        ),
    ]

    got = _scope_anchor_dependence_observations(ops, "2020/766")

    assert _without_target_kind(got) == [
        Finding(
            kind="LOWER.SCOPE_CARRY_FORWARD",
            role="observation",
            stage="frontend_scope",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "33",
                "target_chapter": "5",
                "tag": "chapter_scope_carry_forward",
                "scope_source": "carry_forward",
                "scope_confidence": "inferred",
                "op_type": "REPLACE",
                "target_paragraph": None,
                "target_item": "",
                "target_special": "",
            },
            blocking=False,
        ),
        Finding(
            kind="LOWER.CONTEXT_DEPENDENT_ANCHOR",
            role="observation",
            stage="frontend_scope",
            source_statute="2020/766",
            detail={
                "target_unit_kind": "section",
                "target_norm": "34",
                "target_chapter": "5",
                "tag": "chapter_scope_from_preamble",
                "scope_source": "preamble",
                "scope_confidence": "inferred",
                "op_type": "REPLACE",
                "target_paragraph": None,
                "target_item": "",
                "target_special": "",
            },
            blocking=False,
        ),
    ]


def test_assign_scope_from_renumber_destinations_carries_part_scope_forward() -> None:
    ops = [
        LegalOperation(
            op_id="renumber-1",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("chapter", "3"), ("part", "II"), ("section", "15"))),
            destination=LegalAddress(path=(("chapter", "3"), ("part", "II"), ("section", "16"))),
        ),
        LegalOperation(
            op_id="replace-2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "16"),)),
        ),
    ]

    got = assign_scope_from_renumber_destinations(ops)

    assert dict(got[1].target.path) == {"chapter": "3", "part": "II", "section": "16"}
    assert "chapter_scope_carry_forward" in got[1].provenance_tags
    assert "grouped_part_scope" in got[1].provenance_tags


def test_root_insert_fallback_does_not_consume_conjunction_as_suffix() -> None:
    johto = (
        "kumotaan 6 luvun 5 §:n 5 kohta ja 8 §, muutetaan 6 luvun 5 §:n 4 kohta "
        "sekä lisätään 1 lukuun uusi 3 ja 4 § sekä 4 lukuun uusi 1 a ja 1 b § "
        "seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    got = [(op.chapter, op.number) for op in ops if op.verb == "L"]

    assert ("1", "3") in got
    assert ("1", "4") in got
    assert ("1", "3j") not in got
    assert ("4", "1a") in got
    assert ("4", "1b") in got


def test_root_insert_fallback_recovers_decree_scoped_new_section() -> None:
    johto = (
        "muutetaan ajoneuvojen katsastuksesta annetun asetuksen 23 §, 31 §:n 3 momentti ja 45 §, "
        "lisätään 32 §:ään uusi 4 momentti ja asetuksen uusi 46 c § seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    # Root (whole-§) section inserts only: kind 'P', no momentti scope.
    got = {op.number for op in ops if op.verb == "L" and op.kind == "P" and op.momentti == 0}

    assert "46c" in got
    # 32 § takes a new momentti insert (32 §:ään uusi 4 momentti), not a root §.
    assert "32" not in got


def test_root_insert_fallback_recovers_combined_root_chapter_and_section_ranges() -> None:
    johto = (
        "lisätään 3 §:ään uusi 6 kohta, 16 §:ään uusi 5 momentti "
        "sekä lakiin uusi 5 a—5 c luku ja 20 a—20 h § seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    # kind 'L' = luku (chapter), kind 'P' = pykälä (§). Root inserts: no momentti scope.
    got = {(op.kind, op.number) for op in ops if op.verb == "L" and op.momentti == 0}

    assert ("L", "5a") in got
    assert ("L", "5b") in got
    assert ("L", "5c") in got
    assert ("P", "20a") in got
    assert ("P", "20h") in got
    # 16 § takes a new momentti insert (16 §:ään uusi 5 momentti), not a root §.
    assert ("P", "16") not in got


def test_root_insert_fallback_recovers_decision_scoped_secondary_section_range() -> None:
    johto = (
        "muutetaan yritystuesta 30 päivänä joulukuuta 1993 annetun valtioneuvoston päätöksen "
        "(1689/93) 1 §:n sekä lisätään päätökseen uuden 14a §:n ja sen edelle uuden väliotsikon "
        "sekä uuden 14b―14d §:n seuraavasti:"
    )

    ops = parse_clause(johto).parsed_ops
    got = {(op.kind, op.number) for op in ops if op.verb == "L"}

    assert ("P", "14a") in got
    assert ("P", "14b") in got
    assert ("P", "14c") in got
    assert ("P", "14d") in got


def test_combined_root_insert_ranges_keep_trailing_sections_under_source_chapter() -> None:
    master = pinned_replay("2007/159", mode="official_consolidation")
    sections = extract_ir_sections(master.ir)

    assert "chapter:5c/section:20a" in sections
    assert "chapter:5c/section:20h" in sections
    assert "chapter:6/section:20a" not in sections
    assert "chapter:6/section:20h" not in sections


def test_replay_xml_2002_1330_prefers_live_substantive_section_8_over_repeal_placeholder_slot() -> None:
    replay = pinned_replay("2002/1330", as_of="2019-04-02", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    section8_paths = sorted(path for path in sections if path.endswith("section:8"))
    assert section8_paths == ["chapter:2a/section:8"]

    sec8 = sections["chapter:2a/section:8"]
    text = irnode_to_text(sec8)
    assert "rekrytointia tukevaan toimintaan" in text
    assert "itsenäistä valmentautumista tarjolle annetun materiaalin perusteella" in text
    assert "julkisesta työvoima- ja yrityspalvelusta annetun lain 4 luvun 12 §:ssä" not in text


@pytest.mark.slow
def test_replay_xml_2013_588_retargets_explicit_chunk_sections_from_2023_497_to_live_part_chapter(
    replay_2013_588_finlex_oracle: Any,
) -> None:
    sections = extract_ir_sections(replay_2013_588_finlex_oracle.products.materialized_state.ir)

    for wrong_path in (
        "part:3/section:84",
        "part:3/section:86",
        "part:3/section:102",
        "part:3/chapter:7/section:75e",
        "part:3/chapter:7/section:114",
        "part:3/chapter:7/section:115",
    ):
        assert wrong_path not in sections

    sec84 = sections["part:5/chapter:13/section:84"]
    text84 = irnode_to_text(sec84)
    text86 = irnode_to_text(sections["part:5/chapter:13/section:86"])
    text102 = irnode_to_text(sections["part:5/chapter:13/section:102"])
    text75e = irnode_to_text(sections["part:4/chapter:11a/section:75e"])
    text114 = irnode_to_text(sections["part:7/chapter:16/section:114"])
    text115 = irnode_to_text(sections["part:7/chapter:16/section:115"])

    assert "Luvun soveltamisala ja määritelmät" in text84
    assert "Ennen sopimuksen tekemistä annettavat tiedot" in text86
    assert "Sähkönjakelun keskeyttäminen vähittäismyyjästä johtuvasta syystä" in text102
    assert "Loppukäyttäjän ja sähköntuottajan oikeus itseään koskevan tiedon hyödyntämiseen" in text75e
    assert "Muutoksenhausta Energiaviraston päätökseen" in text114
    assert "Energiaviraston päätöksen täytäntöönpanokelpoisuus" in text115


def test_replay_xml_1982_716_applies_glued_18ja_20_clause_for_section_18() -> None:
    replay = pinned_replay("1982/716", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec18 = sections["chapter:3/section:18"]
    text = irnode_to_text(sec18)

    assert "9, 9 a ja 10 §:n nojalla" in text
    assert "alueellisille ympäristökeskuksille" in text
    assert "Suomen Kuntaliitolle" in text
    assert "9 ja 9 a §:n nojalla" not in text


def test_replay_xml_2007_370_drops_stale_section_15_item_7_subitems_after_2015_742() -> None:
    replay = pinned_replay("2007/370", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec15 = sections["chapter:4/section:15"]
    sub1 = next(c for c in sec15.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    para7 = next(c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "7")

    assert not any(c.kind == IRNodeKind.SUBPARAGRAPH for c in para7.children)
    text = irnode_to_text(para7)
    assert "todistamiskiellosta huolimatta henkilö velvoitetaan todistamaan" in text
    assert "velvoitetaan ilmaisemaan seikka" not in text


def test_replay_xml_1997_133_applies_section_31_intro_replace_from_2026_130() -> None:
    replay = pinned_replay("1997/133", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec31 = sections["chapter:5/section:31"]
    text = " ".join(irnode_to_text(sec31).split())

    assert "Sen lisäksi, mitä kolttalain 37 §:n 1 momentissa säädetään, elinvoimakeskus voi määrätä" in text
    assert "elinkeino-, liikenne- ja ympäristökeskus voi määrätä" not in text


def test_replay_xml_1995_1760_restores_inserted_section_8b_from_2004_1250() -> None:
    replay = pinned_replay("1995/1760", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec8b = sections["section:8b"]
    text = irnode_to_text(sec8b)

    assert "Tonnistoverovelvollisen ilmoittamisvelvollisuus" in text
    assert "Tonnistoverovelvollisen yhtiön on annettava Konserniverokeskukselle seuraavat tiedot" in text


@pytest.mark.slow
def test_replay_xml_1940_378_1994_318_does_not_duplicate_section_61_timeline_versions() -> None:
    replay = pinned_replay("1940/378", as_of="1994-07-02", mode="official_consolidation", quiet=True)
    addr = LegalAddress(path=(("chapter", "7"), ("section", "61")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    versions = replay.timelines[addr].versions

    assert [version.source.statute_id if version.source else None for version in versions] == [
        None,
        "1994/318",
    ]


def test_whole_section_replace_collapses_intro_list_subsections_into_paragraphs() -> None:
    master = pinned_replay("1993/58", mode="official_consolidation")
    sec = master.find_section("3")
    subs = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION]

    assert [c.label for c in subs] == ["1", "2", "3", "4"]
    assert [c.label for c in subs[1].children if c.kind is IRNodeKind.PARAGRAPH] == ["1", "2", "3"]
    assert not any(c.kind is IRNodeKind.SUBSECTION and c.label in {"5", "6", "7"} for c in sec.children)


def test_uncovered_body_insert_accepts_spaced_lettered_sibling_section_refs() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 §"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 4 a ja 4 b § seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <crossHeading>First recovered source cross-heading</crossHeading>
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
            <crossHeading>Second recovered source cross-heading</crossHeading>
            <section>
              <num>4 b §</num>
              <subsection><content><p>bar</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    # Uncovered-body recovery expects amendment chapters to be seeded first.
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "2021/1215").state
    recovery = recover_uncovered_body_ops(
        UncoveredBodyRecoveryRequest(
            state=state,
            ctx=ctx,
            ops=[],
            source_model=AmendmentSourceModel.from_tree(muutos_tree),
            amendment_id="2021/1215",
        ),
        UncoveredBodyRecoverySinks(failed_ops_out=[]),
    )
    rops = list(recovery.recovered_ops)
    assert [audit.disposition for audit in recovery.candidate_audits] == ["INSERT", "INSERT"]
    assert [audit.op_id for audit in recovery.candidate_audits] == [
        "uncovered_insert_4a",
        "uncovered_insert_4b",
    ]
    got = state
    for rop in rops:
        assert rop.intent is not None
        assert has_recognizer(rop.op.provenance, RecognizerId.UNCOVERED_BODY)
        got = apply_op(got, None, ctx, None, replay_mode="official_consolidation", rop=rop)

    assert got.find_section("4a") is not None
    assert got.find_section("4b") is not None
    recovered_cross_headings = [
        child.text for child in got.ir.children if child.kind is IRNodeKind.CROSS_HEADING
    ]
    assert recovered_cross_headings == [
        "First recovered source cross-heading",
        "Second recovered source cross-heading",
    ]
    # Recovered op ids are mirrored onto ResolvedOp for audit joins.
    assert [rop.op_id for rop in rops] == ["uncovered_insert_4a", "uncovered_insert_4b"]
    assert [rop.cross_ir.text if rop.cross_ir is not None else "" for rop in rops] == [
        "First recovered source cross-heading",
        "Second recovered source cross-heading",
    ]
    assert [rop.op_id for rop in rops] == [rop.op.op_id for rop in rops]
    assert {rop.witness_rule_id for rop in rops} == {FI_RECOVERY_UNCOVERED_BODY_RULE_ID}
    assert {rop.op.witness_rule_id for rop in rops} == {FI_RECOVERY_UNCOVERED_BODY_RULE_ID}
    compiled_ops: list[dict[str, object]] = []
    append_compiled_group_ops(compiled_ops, rops)
    assert {row.get("witness_rule_id") for row in compiled_ops} == {
        FI_RECOVERY_UNCOVERED_BODY_RULE_ID
    }


def test_uncovered_body_skips_sections_owned_by_whole_chapter_insert() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7a",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 a luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="55a",
                            children=(IRNode(kind=IRNodeKind.NUM, text="55 a §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    ops = [AmendmentOp(op_id="", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="7a")]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 7 a luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>7 a luku</num>
              <section>
                <num>55 a §</num>
                <content><p>foo</p></content>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    # Uncovered-body recovery expects amendment chapters to be seeded first.
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "2020/1207").state
    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2020/1207",
        failed_ops_out=[],
        findings_out=findings_out,
    )
    # Section 55a is owned by the whole-chapter INSERT op — no uncovered ops expected.
    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "chapter_payload_owned"
    # State is unchanged (no ResolvedOps to apply).
    assert state.find_section("55a", "7a") is not None


def test_uncovered_body_records_future_repeal_skip_finding() -> None:
    from lawvm.finland.future_repeal import RepealTargetRef

    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 §"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 4 a § seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2021/1215",
        future_repeals={RepealTargetRef.section("4a")},
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_FUTURE_REPEAL_SKIP"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "future_repeal"


def test_uncovered_body_records_future_repeal_skip_finding_when_chapter_adopt_is_suppressed() -> None:
    from lawvm.finland.future_repeal import RepealTargetRef

    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 7 a luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>7 a luku</num>
              <section>
                <num>55 a §</num>
                <content><p>foo</p></content>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "2020/1207").state
    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2020/1207",
        future_repeals={RepealTargetRef.section("55a", "7a")},
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_FUTURE_REPEAL_SKIP"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "future_repeal"
    assert skipped[0].detail.get("target_section") == "55a"
    assert skipped[0].detail.get("target_chapter") == "7a"


def test_uncovered_body_surfaces_coverage_ignored_and_rejected_witnesses() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <heading>Missing num section</heading>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="missing_target",
            op_type=OpType.REPLACE,
            target_section="",
            target_unit_kind="section",
            source_statute="2021/1215",
        ),
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2021/1215",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    ignored = [f for f in findings_out if f.kind == "COVERAGE.BODY_UNIT_IGNORED"]
    rejected = [f for f in findings_out if f.kind == "COVERAGE.CLAIM_REJECTED"]
    assert len(ignored) == 1
    assert ignored[0].detail.get("unit_kind") == "section"
    assert ignored[0].detail.get("reason") == "missing_num"
    assert len(rejected) == 1
    assert rejected[0].detail.get("reason") == "missing_target_section"


def test_uncovered_body_surfaces_unresolved_coverage_gap_obligations(monkeypatch: pytest.MonkeyPatch) -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    def _fake_analyze_coverage(_units: list[CoverageUnit], _claims: list[CoverageClaim], **_kwargs: object) -> CoverageReport:
        unit = CoverageUnit(
            unit_id="section_4a",
            kind="section",
            observed_label="4a",
            parent_label=None,
            payload_ref=None,
            tags=frozenset(),
        )
        return CoverageReport(
            units=(unit,),
            claims=(),
            gaps=(
                CoverageGap(
                    unit=unit,
                    disposition="ambiguous_uncovered",
                    suggested_target=None,
                    evidence=("ambiguous_uncovered",),
                ),
            ),
        )

    monkeypatch.setattr("lawvm.finland.uncovered_body_recovery.analyze_coverage", _fake_analyze_coverage)

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2021/1215",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    # The gap is ambiguous and carries no payload_ref, so the typed candidate
    # sweep recovers nothing — but the obligation is still surfaced. (The legacy
    # raw-body dual-run formerly resurrected this section from the body XML; that
    # scan was removed as score-neutral, so the ambiguous gap now produces only
    # the unresolved-gap obligation, not a forced recovery op.)
    assert rops == []
    obligations = [f for f in findings_out if f.kind == "COVERAGE.UNRESOLVED_BODY_GAP"]
    assert len(obligations) == 1
    assert obligations[0].detail.get("disposition") == "ambiguous_uncovered"
    assert obligations[0].detail.get("unit_kind") == "section"
    assert obligations[0].detail.get("observed_label") == "4a"


def test_uncovered_body_records_peg_owned_label_collision_skip_finding() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 a luku</num>
              <section>
                <num>55 a §</num>
                <subsection><content><p>foo</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="replace_55a_1mom",
            op_type=OpType.REPLACE,
            target_section="55a",
            target_chapter="6",
            target_paragraph=1,
            target_unit_kind="section",
            source_statute="2020/1207",
        )
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2020/1207",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_LABEL_COLLISION"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "peg_owned_descendant_label_collision"
    assert skipped[0].detail.get("target_section") == "55a"
    assert skipped[0].detail.get("target_chapter") == "7a"


def test_uncovered_body_skips_section_candidate_owned_by_descendant_op() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="13",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="13 §"),
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="1",
                                    children=(IRNode(kind=IRNodeKind.CONTENT, text="live text"),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            lisätään 13 §:ään uusi merkkiä 141 a koskeva kohta seuraavasti:
          </preamble>
          <body>
            <section>
              <num>13 §</num>
              <hcontainer name="omission"/>
              <subsection><content><p>Merkki 141 a</p></content></subsection>
              <subsection><content><p>Merkillä voidaan varoittaa töyssystä.</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )
    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="insert_13_4_141a",
            op_type=OpType.INSERT,
            target_section="13",
            target_chapter="3",
            target_paragraph=4,
            target_item="141a",
            target_unit_kind="section",
            source_statute="2010/625",
        )
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2010/625",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_LABEL_COLLISION"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "peg_owned_descendant_label_collision"
    assert skipped[0].detail.get("target_section") == "13"
    assert skipped[0].detail.get("target_chapter") == ""


def test_uncovered_body_omission_merge_requires_scoped_target_witness() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="13",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="13 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="live one"),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>lisätään 13 §:ään uusi merkkiä 141 a koskeva kohta seuraavasti:</preamble>
          <body>
            <section>
              <num>13 §</num>
              <hcontainer name="omission"/>
              <subsection><content><p>sparse addition</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="unrelated_replace_99",
            op_type=OpType.REPLACE,
            target_section="99",
            target_unit_kind="section",
            source_statute="2010/625",
        )
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2010/625",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_OMISSION_MERGE_MISSING_SCOPE"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "omission_merge_missing_scope"
    assert skipped[0].detail.get("target_section") == "13"


def test_uncovered_body_omission_merge_rejects_named_subprovision_scope() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="16",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="16 §"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="live section"),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>muutetaan asetuksen 16 §:n merkkiä 317 koskeva kohta seuraavasti:</preamble>
          <body>
            <chapter>
              <num>3 luku</num>
              <section>
                <num>16 §</num>
                <hcontainer name="omission"/>
                <subsection><content><p>Merkki 317</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )
    findings_out: list[Finding] = []

    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2018/1311",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [
        f
        for f in findings_out
        if f.kind == "APPLY.UNCOVERED_BODY_SPECIAL_SUBPROVISION_SCOPE"
    ]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "omission_merge_special_subprovision_scope"
    assert skipped[0].detail.get("target_section") == "16"


def test_uncovered_body_omission_merge_allows_explicit_section_johto_witness() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="13",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="13 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="live one"),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>muutetaan asetuksen 13 § seuraavasti:</preamble>
          <body>
            <section>
              <num>13 §</num>
              <hcontainer name="omission"/>
              <subsection><content><p>sparse addition</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="unrelated_replace_99",
            op_type=OpType.REPLACE,
            target_section="99",
            target_unit_kind="section",
            source_statute="2010/625",
        )
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2010/625",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert len(rops) == 1
    assert rops[0].op_id == "uncovered_merge_13"
    assert rops[0].op.op_type == "REPLACE"
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_OMISSION_MERGE_MISSING_SCOPE"]
    assert skipped == []


def test_johto_insert_subsection_targets_exclude_item_insertions() -> None:
    assert collect_johto_insert_subsection_section_targets(
        "lisätään 15 §:ään uusi 2 ja 3 momentti seuraavasti:"
    ) == frozenset({"15"})
    assert collect_johto_insert_subsection_section_targets(
        "lisätään lain 20 §:ään ja 37 §:ään uusi 2 momentti seuraavasti:"
    ) == frozenset({"20", "37"})
    assert collect_johto_insert_subsection_section_targets(
        "lisätä päätökseen uuden 4 a §:n sekä 2 ja 8 §:ään uuden 2 momentin"
    ) == frozenset({"2", "8"})
    assert collect_johto_insert_subsection_section_targets(
        "lisätään 13 §:ään uusi merkkiä 141 a koskeva kohta seuraavasti:"
    ) == frozenset()


def test_uncovered_body_ignores_malformed_chapter_marker_section() -> None:
    # A malformed source encodes a chapter heading as a section ("16 b luku").
    # The typed coverage sweep classifies it as a chapter unit, not a section, so
    # it never enters the section-candidate path and produces no bogus INSERT.
    # (The legacy raw-body dual-run instead emitted a malformed_chapter_marker
    # skip finding; that scan was removed as score-neutral, and the chapter
    # classification subsumes the guard.)
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>16 b luku</num>
              <heading>Erinäiset säännökset</heading>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2020/1207",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    # No section was recovered or skipped: the unit is a chapter marker.
    assert not any(
        f.detail.get("target_section") == "16bluku" for f in findings_out
    )


def test_uncovered_body_skip_helper_maps_peg_owned_same_chapter_reason() -> None:
    finding = _uncovered_body_recovery_skipped_finding(
        source_statute="2020/1207",
        target_section="55a",
        target_chapter="7a",
        reason="peg_owned_same_chapter",
    )

    assert finding.kind == "APPLY.UNCOVERED_BODY_PEG_SAME_CHAPTER_OWNED"
    assert finding.detail.get("reason") == "peg_owned_same_chapter"
    assert finding.detail.get("target_section") == "55a"
    assert finding.detail.get("target_chapter") == "7a"


def test_uncovered_body_skip_helper_maps_peg_owned_descendant_reasons() -> None:
    same_chapter = _uncovered_body_recovery_skipped_finding(
        source_statute="2020/1207",
        target_section="55a",
        target_chapter="7a",
        reason="peg_owned_descendant_same_chapter",
    )
    label_collision = _uncovered_body_recovery_skipped_finding(
        source_statute="2020/1207",
        target_section="55a",
        target_chapter="8a",
        reason="peg_owned_descendant_label_collision",
    )

    assert same_chapter.kind == "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_SAME_CHAPTER_OWNED"
    assert label_collision.kind == "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_LABEL_COLLISION"


@pytest.mark.parametrize(
    ("reason", "expected_kind"),
    [
        ("moved_destination_mismatch", "APPLY.UNCOVERED_BODY_MOVED_DESTINATION_MISMATCH"),
        ("body_pairing_guard", "APPLY.UNCOVERED_BODY_BODY_PAIRING_GUARD"),
        ("no_content_ops", "APPLY.UNCOVERED_BODY_NO_CONTENT_OPS"),
        ("would_lose_subsections", "APPLY.UNCOVERED_BODY_WOULD_LOSE_SUBSECTIONS"),
        ("johto_guard", "APPLY.UNCOVERED_BODY_PREAMBLE_GUARD"),
        ("omission_merge_failed", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_FAILED"),
        ("omission_merge_low_text_ratio", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_LOW_TEXT_RATIO"),
        ("omission_merge_duplicate_subsection_labels", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_DUPLICATE_LABELS"),
        ("omission_merge_would_lose_subsections", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_WOULD_LOSE_SUBSECTIONS"),
        ("omission_merge_missing_scope", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_MISSING_SCOPE"),
    ],
)
def test_uncovered_body_skip_helper_maps_additional_typed_reasons(
    reason: str,
    expected_kind: str,
) -> None:
    finding = _uncovered_body_recovery_skipped_finding(
        source_statute="2020/1207",
        target_section="55a",
        target_chapter="7a",
        reason=reason,
    )

    assert finding.kind == expected_kind
    assert finding.detail.get("reason") == reason
    assert finding.detail.get("target_section") == "55a"
    assert finding.detail.get("target_chapter") == "7a"


def test_pre_scan_repeal_targets_uses_shared_sec1_acquisition_lane() -> None:
    xml = """
    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <formula name="enactingClause">Ympäristöministerin esittelystä säädetään:</formula>
      <body>
        <section eId="sec_1">
          <num>1 §</num>
          <content><p>kumotaan lain 5 §.</p></content>
        </section>
      </body>
    </akn>
    """.encode("utf-8")

    per_amendment = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["1993/949"],
            corpus_store=_corpus_store({"1993/949": xml}),
            parent_id="1958/370",
            parent_title="Rakennuslaki",
        )
    )

    assert len(per_amendment) == 1
    assert RepealTargetRef.section("5") in per_amendment[0]


def test_uncovered_body_chapter_payload_ownership_requires_subtree_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    from lawvm.finland.body_pairing import ClauseClaim, PayloadAssignment

    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="20",
                            children=(IRNode(kind=IRNodeKind.NUM, text="20 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    ops = [AmendmentOp(op_id="ch5_insert", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="5luku")]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 5 luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>5 luku</num>
              <heading>Uusi luku</heading>
              <section>
                <num>20 §</num>
                <subsection><content><p>Section 20 text.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    def _fake_assignments(*_args, **_kwargs):
        return [
            PayloadAssignment(
                body_unit_id="section:5/20",
                pairing_status="claimed_current",
                claim=ClauseClaim(
                    target_statute=ctx.id,
                    target_address="20",
                    claim_kind="REPLACE",
                    chapter="5",
                ),
            )
        ]

    monkeypatch.setattr("lawvm.finland.uncovered_body_recovery.assign_body_units_subtree_aware", _fake_assignments)

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2015/303",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert [rop.target_norm for rop in rops] == ["20"]
    assert not any(f.kind == "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED" for f in findings_out)


def test_uncovered_body_records_same_wave_relabel_destination_owned_skip() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(IRNode(kind=IRNodeKind.NUM, text="7 luku"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    renumber_lo = LegalOperation(
        op_id="renumber_73_61",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
        destination=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
        source=OperationSource(statute_id="1994/318"),
    )
    ops = [
        AmendmentOp(
            op_id="renumber_73_61",
            op_type=OpType.RENUMBER,
            target_section="73",
            target_kind=TargetKind.SECTION,
            target_chapter="7",
            lo=renumber_lo,
        ),
        AmendmentOp(
            op_id="replace_ch7_heading",
            op_type=OpType.REPLACE,
            target_section="7",
            target_kind=TargetKind.CHAPTER,
        ),
    ]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="substitutions">
                  muutetaan 7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää,
                  joka siirretään 7 luvun 61 §:ksi, seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>7 luku</num>
              <heading>Voimaantulo</heading>
              <section>
                <num>61 §</num>
                <hcontainer name="omission"/>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "1994/318",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED"]
    assert skipped
    assert all(f.detail.get("target_section") == "61" for f in skipped)
    assert all(f.detail.get("target_chapter") == "7" for f in skipped)
    assert all(f.detail.get("reason") == "same_wave_relabel_destination_owned" for f in skipped)


def test_uncovered_body_records_same_wave_relabel_destination_owned_skip_for_leaf_only_destination() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="12",
                    children=(IRNode(kind=IRNodeKind.NUM, text="12 luku"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    renumber_lo = LegalOperation(
        op_id="renumber_4_123",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "12"), ("section", "4"))),
        destination=LegalAddress(path=(("section", "123"),)),
        source=OperationSource(statute_id="2019/371"),
    )
    ops = [
        AmendmentOp(
            op_id="renumber_4_123",
            op_type=OpType.RENUMBER,
            target_section="4",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            lo=renumber_lo,
        ),
        AmendmentOp(
            op_id="replace_ch12_heading",
            op_type=OpType.REPLACE,
            target_section="12",
            target_kind=TargetKind.CHAPTER,
        ),
    ]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="substitutions">
                  muutetaan 12 luku, lukuun ottamatta kuitenkaan 12 luvun 4 §:ää,
                  joka siirretään 12 luvun 123 §:ksi, seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>12 luku</num>
              <heading>Rakenne</heading>
              <section>
                <num>123 §</num>
                <hcontainer name="omission"/>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2019/371",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED"]
    assert skipped
    assert all(f.detail.get("target_section") == "123" for f in skipped)
    assert all(f.detail.get("target_chapter") == "12" for f in skipped)
    assert all(f.detail.get("reason") == "same_wave_relabel_destination_owned" for f in skipped)


@pytest.mark.slow
def test_process_muutoslaki_2017_320_2019_371_recodification_regressions() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay(
        "2017/320",
        mode="legal_pit",
        stop_before="2019/371",
        quiet=True,
        build_full_products=False,
    )
    failed: list[FailedOp] = []

    with redirect_stdout(StringIO()):
        phase = process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
            failed_ops_out=failed,
        )

    skipped = [
        f for f in phase.findings()
        if f.kind == "APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED"
    ]

    assert any(
        f.detail.get("target_chapter") == "12" and f.detail.get("target_section") == "123"
        for f in skipped
    )
    assert any(
        f.detail.get("target_chapter") == "4" and f.detail.get("target_section") == "42"
        for f in skipped
    )

    assert not any(
        f.target_chapter == "12"
        and f.target_section == "6"
        and f.reason_code == "section_not_found"
        for f in failed
    )

    blocked = {
        (f.target_chapter, f.target_section, f.description)
        for f in failed
        if f.reason_code == "section_not_found"
    }
    assert ("1", "8", "REPLACE 1 luku 8 §") not in blocked
    assert ("1", "9", "REPLACE 1 luku 9 §") not in blocked
    assert ("1", "11", "REPLACE 1 luku 11 §") not in blocked

    assert not any(
        f.description == "REPLACE 2 luku 4 § otsikko"
        and f.target_part == "iia"
        and f.target_chapter == "2"
        and f.target_section == "4"
        for f in failed
    )
    assert not any(
        f.description == "REPLACE 1 luku 4 § otsikko"
        and f.target_part == "iia"
        and f.target_chapter == "1"
        and f.target_section == "4"
        for f in failed
    )

    assert not any(
        f.description == "REPLACE 1 luku 10 §"
        and f.target_part == "4"
        and f.target_chapter == "1"
        and f.target_section == "10"
        and f.reason_code == "section_not_found"
        for f in failed
    )
    assert not [f for f in failed if f.reason_code == "section_not_found"]

    observations = [
        f
        for f in phase.findings()
        if f.kind == "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE"
    ]

    assert any(
        f.detail.get("source_target_norm") == "2"
        and f.detail.get("destination_target_norm") == "115"
        and f.detail.get("target_part") == "1"
        and f.detail.get("target_chapter") == "1"
        for f in observations
    )
    assert any(
        f.detail.get("source_target_norm") == "3"
        and f.detail.get("destination_target_norm") == "221"
        and f.detail.get("target_part") == "2"
        and f.detail.get("target_chapter") == "1"
            for f in observations
    )

    assert not [
        f
        for f in failed
        if f.target_part == "6"
        and f.target_chapter == "2"
        and f.target_section == "7"
        and f.reason_code == "section_not_found"
    ]
    assert any(
        f.kind == "ELAB.SOURCE_PATHOLOGY"
        and f.detail.get("code") == "RECODIFICATION_SOURCE_CHAIN_GAP"
        and f.detail.get("target_label") == "2 luku 7 §"
        for f in phase.findings()
    )
    assert any(
        f.kind == "APPLY.FAILED_OPERATION_GOVERNED_BY_SOURCE_CHAIN_GAP"
        and f.detail.get("target_part") == "6"
        and f.detail.get("target_chapter") == "2"
        and f.detail.get("target_section") == "7"
        for f in phase.findings()
    )


@pytest.mark.slow
def test_process_muutoslaki_2017_320_2019_371_post_apply_dedup_clears_transient_duplicate_labels() -> None:
    """Same-wave restructure apply may leave transient duplicate labels before fold."""
    from lawvm.core.invariant_profiles import (
        collect_tree_invariant_violations,
        structural_tree_all_profile,
    )

    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay(
        "2017/320",
        mode="legal_pit",
        stop_before="2019/371",
        quiet=True,
        build_full_products=False,
    )

    with redirect_stdout(StringIO()):
        phase = process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
        )

    profile = structural_tree_all_profile("process_muutoslaki.post_apply")
    violations = collect_tree_invariant_violations(phase.output.ir, profile)
    duplicate_violations = [
        violation for violation in violations if "duplicate" in violation.message.lower()
    ]
    assert duplicate_violations == []

    dedup_findings = [
        finding
        for finding in phase.findings()
        if finding.kind == "APPLY.GLOBAL_LABEL_DEDUP_APPLIED"
    ]
    assert len(dedup_findings) == 1
    assert dedup_findings[0].detail.get("phase") == "process_muutoslaki.post_apply"
    assert dedup_findings[0].source_statute == "2019/371"


@pytest.mark.slow
def test_replay_xml_2017_320_2018_301_keeps_part_scoped_chapter_4_section_11() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay(
        "2017/320",
        mode="legal_pit",
        stop_before="2018/301",
        quiet=True,
        build_full_products=False,
    )

    with redirect_stdout(StringIO()):
        phase = process_muutoslaki(
            "2018/301",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
        )

    section = phase.output.find_section("11", "4", "2")
    assert section is not None
    assert "Yrittäjäkuljettajan työaikakirjanpito" in irnode_to_text(section)
    assert not any(
        f.kind == "ELAB.SOURCE_PATHOLOGY"
        and f.detail.get("code") == "CONTAINER_MEMBERSHIP_MISMATCH"
        and f.detail.get("target_label") == "4 luku"
        and "11" in f.detail.get("detail", {}).get("pruned_sections", [])
        for f in phase.findings()
    )


def test_uncovered_body_records_past_repeal_placeholder_guard_skip_finding() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4a",
                    attrs={"lawvm_repeal_placeholder": "1"},
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 a §"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2021/1215",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_PAST_REPEAL_GUARD"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "past_repeal_placeholder_guard"
    assert skipped[0].detail.get("target_section") == "4a"
    assert skipped[0].detail.get("target_chapter") == ""


def test_uncovered_body_whole_chapter_replace_stamps_exact_tail_policy() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="16",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="16 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="6",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="6 §"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=()),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=()),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=()),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", children=()),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="modifications">
                  muutetaan 16 luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>16 luku</num>
              <section>
                <num>6 §</num>
                <subsection>
                  <content><p>new first moment</p></content>
                </subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2006/1363",
        failed_ops_out=[],
    )

    replace_6 = next(rop for rop in rops if rop.op_id == "uncovered_replace_6")
    assert replace_6.payload_completeness is not None
    assert replace_6.payload_completeness.kind == "complete"
    assert replace_6.payload_completeness.tail_policy == "replace_if_target_scope_requires"

    result = apply_op(state, None, ctx, None, replay_mode="official_consolidation", rop=replace_6)
    live = result.find_section("6", "16")
    assert live is not None
    assert live.attrs["lawvm_tail_policy"] == "replace_if_target_scope_requires"
    subsections = [child for child in live.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]
    assert "new first moment" in irnode_to_text(subsections[0])


def test_uncovered_body_records_cross_chapter_existing_target_skip_finding() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="55a",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="55 a §"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=()),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 a luku</num>
              <section>
                <num>55 a §</num>
                <subsection><content><p>foo</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    ops = [
        AmendmentOp(
            op_id="replace_99",
            op_type=OpType.REPLACE,
            target_section="99",
            target_unit_kind="section",
            source_statute="2020/1207",
        )
    ]
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2020/1207",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert rops == []
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_CROSS_CHAPTER_COLLISION"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "cross_chapter_existing_target"
    assert skipped[0].detail.get("target_section") == "55a"
    assert skipped[0].detail.get("target_chapter") == "7a"


def test_uncovered_body_records_duplicate_recovered_candidate_skip_finding() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
            <section>
              <num>4 a §</num>
              <subsection><content><p>bar</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2021/1215",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert len(rops) == 1
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_DUPLICATE_CANDIDATE"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "duplicate_recovered_candidate"
    assert skipped[0].detail.get("target_section") == "4a"
    assert skipped[0].detail.get("target_chapter") == ""


def test_uncovered_body_adopts_sections_into_new_chapter_when_chapter_insert_left_them_out() -> None:
    """Chapter INSERT op may filter sections via standalone_section_targets.

    When a restructure amendment inserts a new chapter AND the chapter INSERT
    op filters out sections (because they had standalone PEG ops without chapter
    context), those sections end up absent from the new chapter in master.
    _recover_uncovered_body_ops should adopt them into the chapter even though
    covered_chapter_payloads blocks the normal uncovered recovery path.

    Scenario: amendment inserts chapter 5 with sections 20, 21, 22.
    After PEG ops run, chapter 5 exists but is empty (sections were filtered
    from the chapter INSERT due to standalone section targets).
    """
    # Simulate post-PEG state: chapter 5 exists but is empty (sections not yet added)
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="15",
                            children=(IRNode(kind=IRNodeKind.NUM, text="15 §"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    # Chapter 5 was pre-created empty (simulating _pre_create_amendment_chapters)
                    children=(IRNode(kind=IRNodeKind.NUM, text="5 luku"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)

    # PEG produced a chapter INSERT op for chapter 5 (whole-chapter claim)
    ops = [AmendmentOp(op_id="ch5_insert", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="5luku")]

    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 5 luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>5 luku</num>
              <heading>Uusi luku</heading>
              <section>
                <num>20 §</num>
                <subsection><content><p>Section 20 text.</p></content></subsection>
              </section>
              <section>
                <num>21 §</num>
                <subsection><content><p>Section 21 text.</p></content></subsection>
              </section>
              <section>
                <num>22 §</num>
                <subsection><content><p>Section 22 text.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    # Note: we do NOT call _pre_create_amendment_chapters here because the test
    # simulates the state AFTER that step (chapter 5 already in state.ir above).
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2015/303",
        failed_ops_out=[],
    )

    # All three sections should be adopted into chapter 5
    adopted_labels = {rop.target_norm for rop in rops if rop.resolved_target_scope_chapter_label == "5"}
    assert "20" in adopted_labels, (
        f"Section 20 not adopted; rops={[(r.op_id, r.target_norm, r.resolved_target_scope_chapter_label) for r in rops]}"
    )
    assert "21" in adopted_labels, (
        f"Section 21 not adopted; rops={[(r.op_id, r.target_norm, r.resolved_target_scope_chapter_label) for r in rops]}"
    )
    assert "22" in adopted_labels, (
        f"Section 22 not adopted; rops={[(r.op_id, r.target_norm, r.resolved_target_scope_chapter_label) for r in rops]}"
    )

    # Verify op_ids follow the adopt naming convention
    adopt_ids = {rop.op_id for rop in rops}
    assert "uncov_chapter_adopt_20" in adopt_ids
    assert "uncov_chapter_adopt_21" in adopt_ids
    assert "uncov_chapter_adopt_22" in adopt_ids

    # After applying the rops, sections should appear under chapter 5
    final_state = state
    for rop in rops:
        final_state = apply_op(final_state, None, ctx, None, replay_mode="official_consolidation", rop=rop)
    assert final_state.find_section("20", "5") is not None
    assert final_state.find_section("21", "5") is not None
    assert final_state.find_section("22", "5") is not None


def test_uncovered_body_adopts_sections_into_part_scoped_new_chapter_with_same_label_elsewhere() -> None:
    """Chapter-payload adoption must honor explicit part scope.

    If another part already contains a chapter with the same label, uncovered
    chapter-payload adoption must not treat that other chapter as proof that the
    target section is already present.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="IV OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="1",
                                    children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                                ),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="V OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    ops = [
        AmendmentOp(
            op_id="part5_ch2_insert",
            op_type=OpType.INSERT,
            target_kind=TargetKind.CHAPTER,
            target_part="V",
            target_section="2luku",
        )
    ]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin V osaan uusi 2 luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <part>
              <num>V OSA</num>
              <chapter>
                <num>2 luku</num>
                <heading>Uusi luku</heading>
                <section>
                  <num>1 §</num>
                  <subsection><content><p>Part V chapter 2 section 1 text.</p></content></subsection>
                </section>
              </chapter>
            </part>
          </body>
        </akn>
        """
    )

    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2018/301",
        failed_ops_out=[],
    )

    adopted = [
        rop
        for rop in rops
        if rop.op_id == "uncov_chapter_adopt_1"
    ]
    assert len(adopted) == 1
    assert adopted[0].resolved_target_scope_part_label == "5"
    assert adopted[0].resolved_target_scope_chapter_label == "2"

    final_state = state
    for rop in rops:
        final_state = apply_op(final_state, None, ctx, None, replay_mode="official_consolidation", rop=rop)

    assert final_state.find_section("1", "2", "5") is not None


def test_uncovered_body_reports_mixed_chapter_payload_ownership() -> None:
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="20",
                            children=(IRNode(kind=IRNodeKind.NUM, text="20 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    ops = [AmendmentOp(op_id="ch5_insert", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="5luku")]
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 5 luku seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>5 luku</num>
              <heading>Uusi luku</heading>
              <section>
                <num>20 §</num>
                <subsection><content><p>Section 20 text.</p></content></subsection>
              </section>
              <section>
                <num>21 §</num>
                <subsection><content><p>Section 21 text.</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    findings_out: list[Finding] = []
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2015/303",
        failed_ops_out=[],
        findings_out=findings_out,
    )

    assert [rop.target_norm for rop in rops] == ["21"]
    owned = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED"]
    assert len(owned) == 1
    assert owned[0].detail.get("target_section") == "20"
    mixed = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_MIXED"]
    assert len(mixed) == 1
    assert mixed[0].detail.get("target_chapter") == "5"
    assert mixed[0].detail.get("adopted_count") == 1
    assert mixed[0].detail.get("owned_count") == 1
def test_uncovered_body_insert_overrides_chapter_when_family_base_in_different_chapter() -> None:
    """When amendment places §32a in new chapter 4d but §32 lives in chapter 7,
    the uncovered INSERT should use chapter 7 (family-chapter override)."""
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4d",
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 d luku"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="32",
                            children=(IRNode(kind=IRNodeKind.NUM, text="32 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 32 a § seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>4 d luku</num>
              <section>
                <num>32 a §</num>
                <subsection><content><p>new section</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "2020/100").state
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2020/100",
        failed_ops_out=[],
    )
    assert len(rops) >= 1
    # The op should target chapter 7 (where §32 lives), not 4d
    insert_rop = [r for r in rops if r.op.op_id == "uncovered_insert_32a"]
    assert len(insert_rop) == 1
    assert insert_rop[0].op.target_cols.target_chapter == "7"
    assert has_recognizer(insert_rop[0].op.provenance, RecognizerId.UNCOVERED_BODY)


def test_uncovered_body_insert_keeps_explicit_existing_chapter_ownership() -> None:
    """An explicit body chapter that already exists in master should not be rehomed.

    This protects the 2013/393 case where §37a is under chapter 6 in the body
    but a numeric family sibling still lives in chapter 5.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="5 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="37",
                            children=(IRNode(kind=IRNodeKind.NUM, text="37 §"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.NUM, text="6 luku"),),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="insertions">
                  lisätään lakiin uusi 37 a § seuraavasti:
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>6 luku</num>
              <section>
                <num>37 a §</num>
                <subsection><content><p>new section</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2013/393",
        new_chapter_labels=set(),
        failed_ops_out=[],
    )
    assert len(rops) == 1
    assert rops[0].op.op_id == "uncovered_insert_37a"
    assert rops[0].op.target_cols.target_chapter == "6"
    assert has_recognizer(rops[0].op.provenance, RecognizerId.UNCOVERED_BODY)


def test_retarget_stale_body_chapter_scope_ignores_typed_scope_confidence_tags() -> None:
    from lawvm.finland.frontend_compile import _retarget_stale_body_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="32",
                            children=(IRNode(kind=IRNodeKind.NUM, text="32 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        '<akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><body/></akn>'
    )
    op = AmendmentOp(
        op_id="insert_32a",
        op_type=OpType.INSERT,
        target_section="32",
        target_kind=TargetKind.SECTION,
        target_chapter="4d",
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    # carry_forward source is not in {explicit_scope_rewrite, explicit_chunk} — early None
    got = _retarget_stale_body_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got is None


def test_retarget_stale_body_chapter_scope_respects_stored_scope_confidence_carrier() -> None:
    from lawvm.finland.frontend_compile import _retarget_stale_body_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="32",
                            children=(IRNode(kind=IRNodeKind.NUM, text="32 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    # Amendment body agrees with the op's explicit_chunk scope (section 32 in
    # chapter "4d") → INSERT guard fires → no retarget needed.
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>4d luku</num>
              <section><num>32 §</num></section>
            </chapter>
          </body>
        </akn>
        """
    )
    op = AmendmentOp(
        op_id="insert_32a",
        op_type=OpType.INSERT,
        target_section="32",
        target_kind=TargetKind.SECTION,
        target_chapter="4d",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="4d",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got is None


def test_retarget_stale_body_chapter_scope_keeps_explicit_chunk_whole_section_insert() -> None:
    from lawvm.finland.frontend_compile import _retarget_stale_body_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="5",
                            children=(IRNode(kind=IRNodeKind.NUM, text="5 §"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>1 luku</num>
              <section><num>5 §</num></section>
            </chapter>
          </body>
        </akn>
        """
    )
    op = AmendmentOp(
        op_id="insert_5_explicit_chunk",
        op_type=OpType.INSERT,
        target_section="5",
        target_kind=TargetKind.SECTION,
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source=ScopeResolutionSource.EXPLICIT_CHUNK,
            confidence=ScopeResolutionConfidence.EXPLICIT,
            resolved_chapter="2",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got is None


def test_retarget_stale_body_chapter_scope_allows_explicit_scope_rewrite_carrier() -> None:
    from lawvm.finland.frontend_compile import _retarget_stale_body_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="32",
                            children=(IRNode(kind=IRNodeKind.NUM, text="32 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    # Amendment body places section 32 in chapter "7", but op has stale scope "4d"
    # (explicit_scope_rewrite source) → retarget to the live location (None, "7").
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>7 luku</num>
              <section><num>32 §</num></section>
            </chapter>
          </body>
        </akn>
        """
    )
    op = AmendmentOp(
        op_id="insert_32a",
        op_type=OpType.INSERT,
        target_section="32",
        target_kind=TargetKind.SECTION,
        target_chapter="4d",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_stripped_unique_section",
            source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
            confidence=ScopeResolutionConfidence.REWRITTEN,
            resolved_chapter="4d",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    # Returns (live_part, live_chapter) tuple — the section lives in chapter "7"
    assert got == (None, "7")


def test_body_chapter_scope_for_section_op_respects_part_scope() -> None:
    from lawvm.finland.frontend_compile import _body_chapter_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.PART, label="1", children=(IRNode(kind=IRNodeKind.NUM, text="I osa"), IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),)))),
                IRNode(kind=IRNodeKind.PART, label="2", children=(IRNode(kind=IRNodeKind.NUM, text="II osa"), IRNode(kind=IRNodeKind.CHAPTER, label="5", children=(IRNode(kind=IRNodeKind.NUM, text="5 luku"),)))),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>I osa</num>
              <chapter><num>1 luku</num><section><num>1 §</num></section></chapter>
            </part>
            <part>
              <num>II osa</num>
              <chapter><num>5 luku</num><section><num>1 §</num></section></chapter>
            </part>
          </body>
        </akn>
        """
    )
    op = AmendmentOp(
        op_id="insert_subsection",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_paragraph=3,
        target_part="1",
    )

    got = _body_chapter_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got == "1"


def test_body_chapter_scope_for_section_op_keeps_ambiguous_same_part_unscoped() -> None:
    from lawvm.finland.frontend_compile import _body_chapter_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="I osa"),
                        IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),)),
                        IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),)),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>I osa</num>
              <chapter><num>1 luku</num><section><num>1 §</num></section></chapter>
              <chapter><num>2 luku</num><section><num>1 §</num></section></chapter>
            </part>
          </body>
        </akn>
        """
    )
    op = AmendmentOp(
        op_id="insert_subsection",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_paragraph=3,
        target_part="1",
    )

    got = _body_chapter_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got is None


def test_body_chapter_scope_for_section_op_overrides_carry_forward_with_unique_existing_body_chapter() -> None:
    from lawvm.finland.frontend_compile import _body_chapter_scope_for_section_op

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>6 luku</num>
            <section>
              <num>37 a §</num>
              <content><p>new section</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _body_chapter_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got == "6"


def test_enrich_ops_prefers_live_body_chapter_before_letter_suffix_stem_host_for_unscoped_insert() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="6", children=()),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>6 luku</num>
            <section>
              <num>37 a §</num>
              <content><p>new section</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_37a",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="37a",
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_cols.target_chapter == "6"
    assert got[0].witness_rule_id == "fi_body_chapter_scope_from_source_body"
    assert got[0].scope_confidence is not None
    assert got[0].scope_confidence.source == "carry_forward"
    assert got[0].scope_confidence.resolved_chapter == "6"


def test_enrich_ops_prefers_letter_suffix_stem_host_before_unborn_body_wrapper_for_unscoped_insert() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>6 luku</num>
            <section>
              <num>37 a §</num>
              <content><p>new section</p></content>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_37a",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="37a",
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_cols.target_chapter == "5"
    assert got[0].witness_rule_id == "fi_letter_suffix_insert_scope_from_stem_host"
    assert got[0].scope_confidence is not None
    assert got[0].scope_confidence.source == "carry_forward"
    assert got[0].scope_confidence.resolved_chapter == "5"


def test_enrich_ops_overrides_live_stem_host_with_corroborated_source_body_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="16",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="130"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="17",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="131"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>17 luku</num>
              <section><num>130 a §</num><content><p>new source section</p></content></section>
              <section><num>131 §</num><content><p>existing section replacement</p></content></section>
              <section><num>136 §</num><content><p>Edellä 130 a ja 135 c §:ssä tarkoitetut ilmoitukset toimitetaan vuosittain.</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_130a",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="130a",
        target_chapter="16",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="16",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        lo=LegalOperation(
            op_id="insert_130a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "16"), ("section", "130a"))),
        ),
    )
    replace_op = AmendmentOp(
        op_id="replace_131",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="131",
        target_chapter="17",
        lo=LegalOperation(
            op_id="replace_131",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "17"), ("section", "131"))),
        ),
    )
    insert_subsection_op = AmendmentOp(
        op_id="insert_136_5",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="136",
        target_chapter="17",
        target_paragraph=5,
        lo=LegalOperation(
            op_id="insert_136_5",
            sequence=3,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "17"), ("section", "136"), ("subsection", "5"))),
            payload=IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        text="Edellä 130 a ja 135 c §:ssä tarkoitetut ilmoitukset toimitetaan vuosittain.",
                    ),
                ),
            ),
        ),
    )

    got = _enrich_ops_from_amendment_tree(
        [insert_op, replace_op, insert_subsection_op],
        "2024/1",
        muutos_tree,
        master=master,
        source_model=AmendmentSourceModel.from_tree(muutos_tree),
    )

    assert got[0].target_cols.target_chapter == "17"
    assert got[0].witness_rule_id == "fi_live_stem_scope_overridden_by_corroborated_source_body"
    assert got[0].lo is not None
    assert got[0].lo.target.path == (("chapter", "17"), ("section", "130a"))


def test_enrich_ops_keeps_live_stem_host_without_existing_section_corroboration() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="16",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="130"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="17", children=()),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>17 luku</num>
              <section><num>130 a §</num><content><p>new source section</p></content></section>
              <section><num>135 b §</num><content><p>another new source section</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_130a",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="130a",
        target_chapter="16",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="16",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        lo=LegalOperation(
            op_id="insert_130a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "16"), ("section", "130a"))),
        ),
    )
    sibling_insert_op = AmendmentOp(
        op_id="insert_135b",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="135b",
        target_chapter="17",
    )

    got = _enrich_ops_from_amendment_tree(
        [insert_op, sibling_insert_op],
        "2024/1",
        muutos_tree,
        master=master,
        source_model=AmendmentSourceModel.from_tree(muutos_tree),
    )

    assert got[0].target_cols.target_chapter == "16"
    assert got[0].witness_rule_id != "fi_live_stem_scope_overridden_by_corroborated_source_body"


def test_enrich_ops_keeps_live_stem_host_without_internal_reference_witness() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="6"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="7"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>3 luku</num>
              <section><num>6 a §</num><content><p>new source section</p></content></section>
              <section><num>7 §</num><content><p>existing adjacent section replacement</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_6a",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="6a",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="2",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        lo=LegalOperation(
            op_id="insert_6a",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("section", "6a"))),
        ),
    )
    replace_op = AmendmentOp(
        op_id="replace_7",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="7",
        target_chapter="3",
        lo=LegalOperation(
            op_id="replace_7",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "3"), ("section", "7"))),
            payload=IRNode(kind=IRNodeKind.SECTION, label="7", text="No reference to the new suffix section."),
        ),
    )

    got = _enrich_ops_from_amendment_tree(
        [insert_op, replace_op],
        "2024/1",
        muutos_tree,
        master=master,
        source_model=AmendmentSourceModel.from_tree(muutos_tree),
    )

    assert got[0].target_cols.target_chapter == "2"
    assert got[0].witness_rule_id != "fi_live_stem_scope_overridden_by_corroborated_source_body"


def test_enrich_ops_keeps_live_stem_host_when_source_body_corroborator_is_distant() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="10"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="18"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>3 luku</num>
              <section><num>10 c §</num><content><p>new source section</p></content></section>
              <section><num>18 §</num><content><p>distant existing section replacement</p></content></section>
            </chapter>
          </body>
        </act>
        """
    )
    insert_op = AmendmentOp(
        op_id="insert_10c",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="10c",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_letter_suffix_stem_host",
            source=ScopeResolutionSource.LIVE_STEM_HOST,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="2",
        ),
        scope_provenance_tags=("chapter_scope_from_letter_suffix_stem_host",),
        lo=LegalOperation(
            op_id="insert_10c",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("section", "10c"))),
        ),
    )
    replace_op = AmendmentOp(
        op_id="replace_18",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="18",
        target_chapter="3",
    )

    got = _enrich_ops_from_amendment_tree(
        [insert_op, replace_op],
        "2024/1",
        muutos_tree,
        master=master,
        source_model=AmendmentSourceModel.from_tree(muutos_tree),
    )

    assert got[0].target_cols.target_chapter == "2"
    assert got[0].witness_rule_id != "fi_live_stem_scope_overridden_by_corroborated_source_body"


def test_enrich_ops_uses_vacated_live_scope_for_recodification_insert_under_mixed_wrapper() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="14a"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6a",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="25"),
                        IRNode(kind=IRNodeKind.SECTION, label="27f"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="9",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="42"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>3 luku</num>
            <section><num>14 a §</num><content><p>new 14a</p></content></section>
            <section><num>25 §</num><content><p>old 25 body</p></content></section>
            <section><num>27 f §</num><content><p>new 27f body</p></content></section>
            <section><num>42 §</num><content><p>old 42 body</p></content></section>
          </chapter>
        </body>
        """
    )
    renumber = AmendmentOp(
        op_id="renumber_27f",
        op_type=OpType.RENUMBER,
        target_unit_kind="section",
        target_section="27f",
        lo=LegalOperation(
            op_id="renumber_27f",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "27f"),)),
            destination=LegalAddress(path=(("section", "27g"),)),
        ),
    )
    insert = AmendmentOp(
        op_id="insert_27f",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="27f",
    )
    source_model = AmendmentSourceModel.from_tree(muutos_tree, source_ref="2003/444")

    got = _enrich_ops_from_amendment_tree(
        [renumber, insert],
        "2003/444",
        muutos_tree,
        master=master,
        source_model=source_model,
    )

    got_insert = next(op for op in got if op.op_id == "insert_27f")
    assert got_insert.target_cols.target_chapter == "6a"
    assert got_insert.witness_rule_id == "fi_recodification_vacated_insert_scope"
    assert got_insert.scope_confidence is not None
    assert got_insert.scope_confidence.source == "carry_forward"
    assert got_insert.scope_confidence.resolved_chapter == "6a"


def test_flat_body_insert_chapter_scope_uses_bracketing_live_siblings() -> None:
    from lawvm.finland.frontend_compile import (
        _infer_flat_body_insert_chapter_from_bracketing_live_siblings,
    )

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="15",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="148"),
                        IRNode(kind=IRNodeKind.SECTION, label="150"),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="20",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="211"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section><num>149 §</num><content><p>new section</p></content></section>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="149",
    )

    got = _infer_flat_body_insert_chapter_from_bracketing_live_siblings(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got == "15"


def test_flat_body_insert_chapter_scope_rejects_one_sided_live_sibling() -> None:
    from lawvm.finland.frontend_compile import (
        _infer_flat_body_insert_chapter_from_bracketing_live_siblings,
    )

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="15",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="148"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section><num>149 §</num><content><p>new section</p></content></section>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="149",
    )

    got = _infer_flat_body_insert_chapter_from_bracketing_live_siblings(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got is None


def test_flat_body_replace_scope_uses_letter_suffix_bracketing_live_siblings() -> None:
    from lawvm.finland.frontend_compile import (
        _infer_flat_body_replace_scope_from_bracketing_live_siblings,
    )

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2a",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="20a"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="20"),
                        IRNode(kind=IRNodeKind.SECTION, label="20a"),
                        IRNode(kind=IRNodeKind.SECTION, label="21"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section><num>20 a §</num><content><p>replacement</p></content></section>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="20a",
    )

    got = _infer_flat_body_replace_scope_from_bracketing_live_siblings(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got == (None, "4")


def test_flat_body_replace_scope_rejects_letter_suffix_disagreeing_brackets() -> None:
    from lawvm.finland.frontend_compile import (
        _infer_flat_body_replace_scope_from_bracketing_live_siblings,
    )

    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="20"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="20a"),
                        IRNode(kind=IRNodeKind.SECTION, label="21"),
                    ),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <section><num>20 a §</num><content><p>replacement</p></content></section>
        </body>
        """
    )
    op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="20a",
    )

    got = _infer_flat_body_replace_scope_from_bracketing_live_siblings(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got is None


def test_enrich_ops_keeps_live_carry_forward_subsection_scope_over_stale_body_chapter() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="2", children=()),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="8a"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>2 luku</num>
            <section>
              <num>8 a §</num>
              <subsection>
                <num>2 mom.</num>
                <content><p>body payload</p></content>
              </subsection>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_8a_2",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="8a",
        target_chapter="3",
        target_paragraph=2,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="3",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_cols.target_chapter == "3"
    assert got[0].scope_confidence is not None
    assert got[0].scope_confidence.source == "carry_forward"
    assert got[0].scope_confidence.resolved_chapter == "3"


def test_enrich_ops_still_rewrites_deep_carry_forward_when_live_host_is_absent() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="37"),),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="6", children=()),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <chapter>
            <num>6 luku</num>
            <section>
              <num>37 a §</num>
              <subsection>
                <num>2 mom.</num>
                <content><p>body payload</p></content>
              </subsection>
            </section>
          </chapter>
        </body>
        """
    )
    op = AmendmentOp(
        op_id="insert_37a_2",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        target_paragraph=2,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source=ScopeResolutionSource.CARRY_FORWARD,
            confidence=ScopeResolutionConfidence.INFERRED,
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_cols.target_chapter == "6"


def test_coalesce_same_target_mixed_scope_section_groups_tags_bare_ops_on_merge() -> None:
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="8",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
                        ),
                    ),
                ),
            ),
        )
    )
    bare = AmendmentOp(
        op_id="bare",
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_paragraph=2,
    )
    scoped = AmendmentOp(
        op_id="scoped",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_chapter="2",
        target_paragraph=7,
    )
    section_groups: dict[tuple[IRNodeKind, str, str | None, str | None], list[AmendmentOp]] = {
        (IRNodeKind.SECTION, "8", None, None): [bare],
        (IRNodeKind.SECTION, "8", "2", None): [scoped],
    }
    muutos_tree = etree.fromstring(
        "<muutos><section num='8'><subsection num='2'/><subsection num='7'/></section></muutos>"
    )

    got = _coalesce_same_target_mixed_scope_section_groups(
        section_groups,
        master=master,
        muutos_tree=muutos_tree,
    )

    assert set(got) == {(IRNodeKind.SECTION, "8", "2", None)}
    merged_ops = got[GroupTargetKey(IRNodeKind.SECTION, "8", "2", None)]
    assert [op.op_id for op in merged_ops] == ["bare", "scoped"]
    assert merged_ops[0].scope_provenance_tags[-1] == "mixed_scope_group_merge"
    assert merged_ops[0].target_cols.target_chapter == "2"
    assert merged_ops[0].scope_confidence is not None
    assert merged_ops[0].scope_confidence.tag == "mixed_scope_group_merge"
    assert merged_ops[0].scope_confidence.resolved_chapter == "2"


def test_coalesce_same_target_mixed_scope_section_groups_drops_covered_bare_duplicate_tail() -> None:
    """Duplicate-label mixed-scope tails must not survive as a second group.

    1997/1339 <- 2015/1752 carries both a chapter-scoped section 4 group and a
    bare section 4 group. The bare group only repeats the `7 kohta` tail that
    already exists in the scoped group. Coalescing should keep the scoped
    ownership and drop the covered bare group instead of replaying the same tail
    twice.
    """
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="4",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="4",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
                        ),
                    ),
                ),
            ),
        )
    )
    scoped_replace = AmendmentOp(
        op_id="scoped_replace",
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="1",
        target_paragraph=1,
    )
    scoped_insert = AmendmentOp(
        op_id="scoped_insert",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="1",
        target_paragraph=7,
    )
    bare_insert = AmendmentOp(
        op_id="bare_insert",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=7,
    )
    section_groups: dict[tuple[IRNodeKind, str, str | None, str | None], list[AmendmentOp]] = {
        (IRNodeKind.SECTION, "4", "1", None): [scoped_replace, scoped_insert],
        (IRNodeKind.SECTION, "4", None, None): [bare_insert],
    }
    muutos_tree = etree.fromstring(
        "<akn><body><chapter><num>1 luku</num><section num='4'><subsection num='1'/></section></chapter></body></akn>"
    )

    got = _coalesce_same_target_mixed_scope_section_groups(
        section_groups,
        master=master,
        muutos_tree=muutos_tree,
    )

    assert set(got) == {(IRNodeKind.SECTION, "4", "1", None)}
    assert [op.op_id for op in got[GroupTargetKey(IRNodeKind.SECTION, "4", "1", None)]] == [
        "scoped_replace",
        "scoped_insert",
    ]


def test_coalesce_same_target_mixed_scope_section_groups_does_not_alias_roman_item_tail() -> None:
    """A roman-looking item label is not the same duplicate tail as arabic item 4."""
    master = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
            ),
        )
    )
    scoped_insert = AmendmentOp(
        op_id="scoped_insert_4",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="1",
        target_paragraph=1,
        target_item="4",
    )
    bare_insert = AmendmentOp(
        op_id="bare_insert_iv",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="iv",
    )
    section_groups: dict[tuple[IRNodeKind, str, str | None, str | None], list[AmendmentOp]] = {
        (IRNodeKind.SECTION, "4", "1", None): [scoped_insert],
        (IRNodeKind.SECTION, "4", None, None): [bare_insert],
    }
    muutos_tree = etree.fromstring(
        "<akn><body><chapter><num>1 luku</num><section num='4'><subsection num='1'/></section></chapter></body></akn>"
    )

    got = _coalesce_same_target_mixed_scope_section_groups(
        section_groups,
        master=master,
        muutos_tree=muutos_tree,
    )

    assert set(got) == {(IRNodeKind.SECTION, "4", "1", None)}
    merged_ops = got[GroupTargetKey(IRNodeKind.SECTION, "4", "1", None)]
    assert [op.op_id for op in merged_ops] == ["scoped_insert_4", "bare_insert_iv"]
    assert merged_ops[1].target_cols.target_chapter == "1"
    assert merged_ops[1].scope_provenance_tags[-1] == "mixed_scope_group_merge"


def test_pre_scan_repeal_targets_skips_future_effective_repeals_past_cutoff() -> None:
    corpus = _corpus_store(
        {
            "2025/1352": """
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <meta>
                <lifecycle>
                  <eventRef date="2026-07-01" />
                </lifecycle>
              </meta>
              <dateEntryIntoForce date="2026-07-01" />
              <formula name="enactingClause">
                Talla lailla kumotaan sahkon ja eraiden polttoaineiden valmisteverosta annetun lain (1260/1996) 4 a §.
              </formula>
            </akn>
            """.encode("utf-8"),
        }
    )

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=corpus,
            parent_id="1996/1260",
            cutoff_date=dt.date(2025, 12, 22),
        )
    )

    assert got == [set()]


def test_pre_scan_repeal_targets_accepts_parent_title_for_vts_scan(monkeypatch) -> None:
    seen: list[str] = []

    def _fake_extract(xml_bytes, parent_id, parent_title="", **_kwargs):
        seen.append(parent_title)
        return []

    def _fail_acquisition_for_non_repeal_source(**_kwargs):
        raise AssertionError("johtolause acquisition should be skipped when source bytes contain no repeal keyword")

    # _pre_scan_repeal_targets lives in future_repeal_prescan, which has its own
    # `from lawvm.finland.vts import extract_voimaantulo_repeals` binding.
    # Patching the grafter re-export does not affect that module's lookup.
    monkeypatch.setattr(
        "lawvm.finland.future_repeal_prescan.extract_voimaantulo_repeals",
        _fake_extract,
    )
    monkeypatch.setattr(
        "lawvm.finland.future_repeal_prescan.build_amendment_acquisition_result",
        _fail_acquisition_for_non_repeal_source,
    )
    corpus = _corpus_store(
        {
            "2025/1352": """
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <meta>
                <lifecycle>
                  <eventRef date="2025-01-01" />
                </lifecycle>
              </meta>
              <dateEntryIntoForce date="2025-01-01" />
              <formula name="enactingClause">
                Talla lailla muutetaan sahkon valmisteverosta annetun lain (1260/1996) 4 a §.
              </formula>
            </akn>
            """.encode("utf-8"),
        }
    )

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=corpus,
            parent_id="1996/1260",
            parent_title="Sahkoverolaki",
            cutoff_date=dt.date(2025, 12, 22),
        )
    )

    assert got == [set()]
    assert seen == ["Sahkoverolaki"]


def test_pre_scan_repeal_targets_preserves_vts_skipped_targets(monkeypatch) -> None:
    from lawvm.finland.vts import VTS_SKIPPED_TARGET_RULE_ID, VtsSkippedTarget

    def _fake_extract(
        xml_bytes,
        parent_id,
        parent_title="",
        skipped_targets_out=None,
        source_diagnostics_out=None,
    ):
        assert skipped_targets_out is not None
        assert source_diagnostics_out is not None
        skipped_targets_out.append(
            VtsSkippedTarget(
                rule_id=VTS_SKIPPED_TARGET_RULE_ID,
                reason_code="unsupported_subitem_target",
                source_reason="subitem VTS target is not lowerable",
                source_statute="2025/1352",
                source_excerpt="4 a §:n 1 momentin 1 kohdan a alakohta.",
                target_section="4a",
                target_paragraph=1,
                target_item="1",
                target_subitem="a",
            )
        )
        return []

    monkeypatch.setattr(
        "lawvm.finland.future_repeal_prescan.extract_voimaantulo_repeals",
        _fake_extract,
    )
    corpus = _corpus_store(
        {
            "2025/1352": """
            <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <meta>
                <lifecycle>
                  <eventRef date="2025-01-01" />
                </lifecycle>
              </meta>
              <dateEntryIntoForce date="2025-01-01" />
              <formula name="enactingClause">
                Talla lailla muutetaan sahkon valmisteverosta annetun lain (1260/1996) 4 a §.
              </formula>
            </akn>
            """.encode("utf-8"),
        }
    )
    skipped: list[VtsSkippedTarget] = []
    source_diagnostics = []

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=corpus,
            parent_id="1996/1260",
            parent_title="Sahkoverolaki",
            cutoff_date=dt.date(2025, 12, 22),
        ),
        PreScanRepealTargetsSinks(
            vts_skipped_targets_out=skipped,
            vts_source_diagnostics_out=source_diagnostics,
        ),
    )

    assert got == [set()]
    assert [record.rule_id for record in skipped] == [VTS_SKIPPED_TARGET_RULE_ID]
    assert skipped[0].reason_code == "unsupported_subitem_target"
    assert skipped[0].target_subitem == "a"
    assert source_diagnostics == []


def test_pre_scan_repeal_targets_records_missing_source_diagnostic() -> None:
    diagnostics: list[PreScanRepealDiagnostic] = []

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=_corpus_store({}),
            parent_id="1996/1260",
            parent_title="Sahkoverolaki",
        ),
        PreScanRepealTargetsSinks(prescan_diagnostics_out=diagnostics),
    )

    assert got == [set()]
    assert len(diagnostics) == 1
    assert diagnostics[0].rule_id == PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID
    assert diagnostics[0].reason_code == "missing_source"
    assert diagnostics[0].source_statute == "2025/1352"
    assert diagnostics[0].blocking is False


def test_pre_scan_repeal_targets_records_xml_parse_diagnostic() -> None:
    diagnostics: list[PreScanRepealDiagnostic] = []

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=_corpus_store({"2025/1352": b"<akn><broken"}),
            parent_id="1996/1260",
            parent_title="Sahkoverolaki",
        ),
        PreScanRepealTargetsSinks(prescan_diagnostics_out=diagnostics),
    )

    assert got == [set()]
    assert len(diagnostics) == 1
    assert diagnostics[0].reason_code == "prescan_parse_error"
    assert diagnostics[0].exception_type == "XMLSyntaxError"
    assert diagnostics[0].source_excerpt.startswith("<akn>")


def test_pre_scan_repeal_targets_records_vts_extraction_exception(monkeypatch) -> None:
    def _raise_vts_error(*_args, **_kwargs):
        raise ValueError("synthetic vts failure")

    monkeypatch.setattr(
        "lawvm.finland.future_repeal_prescan.extract_voimaantulo_repeals",
        _raise_vts_error,
    )
    diagnostics: list[PreScanRepealDiagnostic] = []

    got = _pre_scan_repeal_targets(
        PreScanRepealTargetsRequest(
            muutoslait=["2025/1352"],
            corpus_store=_corpus_store(
                {
                    "2025/1352": """
                    <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
                      <dateEntryIntoForce date="2025-01-01" />
                      <formula name="enactingClause">Talla lailla muutetaan lakia.</formula>
                    </akn>
                    """.encode("utf-8"),
                }
            ),
            parent_id="1996/1260",
            parent_title="Sahkoverolaki",
        ),
        PreScanRepealTargetsSinks(prescan_diagnostics_out=diagnostics),
    )

    assert got == [set()]
    assert len(diagnostics) == 1
    assert diagnostics[0].reason_code == "vts_extraction_error"
    assert diagnostics[0].exception_type == "ValueError"
    assert diagnostics[0].exception_message == "synthetic vts failure"


def test_fallback_does_not_repeal_parent_for_amendment_act_titles() -> None:
    johto = (
        "Tällä lailla kumotaan Harmaan talouden selvitysyksiköstä annetun lain "
        "6 §:n muuttamisesta annettu laki (923/2017)."
    )

    ops = parse_ops_fallback_heuristic(johto)

    assert ops == []


def test_restrict_sec1_fallback_strips_duplicate_lead_in() -> None:
    sec1 = (
        "Täten kumotaan 29 päivänä kesäkuuta 1983 annetun sosiaalihuoltoasetuksen "
        "(607/83) 9 §:n 1 momentin 3 kohta ja 2 momentti."
    )

    restricted = _restrict_sec1_fallback_to_parent(sec1, "1983/607")

    assert restricted.count("Täten kumotaan") == 1
    assert "(607/83)" in restricted


def test_restrict_sec1_fallback_narrows_multi_parent_clause() -> None:
    sec1 = (
        "Tällä lailla kumotaan 17 päivänä syyskuuta 1982 annetun sosiaalihuoltolain "
        "(710/1982) 30―38 § ja 30 §:n edellä oleva väliotsikko sekä 29 päivänä "
        "kesäkuuta 1983 annetun sosiaalihuoltoasetuksen (607/1983) 14 §, "
        "sellaisina kuin niistä ovat lain 34 § osaksi laissa 736/1992 ja 38 § "
        "mainitussa laissa."
    )

    restricted = _restrict_sec1_fallback_to_parent(sec1, "1983/607")

    assert restricted.startswith("Tällä lailla kumotaan")
    assert "(607/1983)" in restricted
    assert "(710/1982)" not in restricted
    assert "14 §" in restricted


def test_restrict_sec1_fallback_drops_foreign_sentence_before_parent_repeal() -> None:
    sec1 = (
        "Ulosottokaaren (705/2007) 1 luvun 11 §:n 1 ja 2 momentti, 12 § sekä "
        "muut hallintovirastoa koskevat säännökset tulevat voimaan 1 päivänä "
        "tammikuuta 2010. Tällä lailla kumotaan ulosottotoimen hallinnosta "
        "20 päivänä joulukuuta 2007 annetun valtioneuvoston asetuksen "
        "(1321/2007) 11 §."
    )

    restricted = _restrict_sec1_fallback_to_parent(sec1, "2007/1321")

    assert restricted.startswith("Tällä lailla kumotaan")
    assert "(1321/2007)" in restricted
    assert "11 §" in restricted
    assert "(705/2007)" not in restricted
    assert "12 §" not in restricted


def test_snapshot_source_falls_back_to_amendment_dates_for_supplement_ops() -> None:
    aop = AmendmentOp(
        op_id="",
        op_type=OpType.INSERT,
        target_section="1a",
        target_unit_kind="section",
        source_statute="2020/1133",
    )
    rop = ResolvedOp.from_amendment_op(
        op=aop,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1a",
        target_chapter=None,
        op_source=None,  # no lo.source — should fall back to amendment dates
    )
    src = _snapshot_op_source(
        [rop],
        amendment_id="2020/1133",
        source_title="Laki oikeudenkäymiskaaren muuttamisesta",
        source_issue_date=dt.date(2020, 12, 30),
        source_effective_date=dt.date(2021, 1, 1),
    )

    assert src.statute_id == "2020/1133"
    assert src.enacted == "2020-12-30"
    assert src.effective == "2021-01-01"


def test_skip_suspicious_partial_fallback_whole_section_replace() -> None:
    def para(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=label,
            children=(IRNode(kind=IRNodeKind.CONTENT, text=text),),
        )

    master = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    para("1", "Header A"),
                    para("2", "Header B"),
                    para("3", "Alpha"),
                    para("4", "Beta"),
                    para("5", "Gamma"),
                    para("6", "Delta"),
                    para("7", "Epsilon"),
                    para("8", "Zeta"),
                ),
            ),
        ),
    )
    amend = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="1 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    para("1", "Header A"),
                    para("2", "Header B"),
                    para("3", "Beta"),
                ),
            ),
        ),
    )

    op = AmendmentOp(op_id="", op_type=OpType.REPLACE, target_section="1", target_kind=TargetKind.SECTION)

    assert _is_suspicious_partial_section_replace_ir(op, master, amend) is True


def test_strict_replay_emits_explicit_source_pathology_rejection_for_1994_1472() -> None:
    from lawvm.finland.strict_profile import FINLAND_INGESTION_V1

    replay = pinned_replay(
        "1994/1472",
        mode="legal_pit",
        strict_profile=FINLAND_INGESTION_V1,
    )

    rejected = [
        row for row in replay.projection_rows()
        if row.get("kind") == "APPLY.SOURCE_PATHOLOGY_DETECTED"
    ]
    # SUBSECTION_TARGET_REBOUND is no longer emitted here: the prefix-migration
    # follow is now bounded by each op's own establishment date (``not_before``),
    # so a subsection op no longer chases a renumber wave that predates its
    # content lineage. This removes a spurious rebound and improves the 1994/1472
    # replay score; the remaining genuine pathologies are unchanged.
    assert {
        cast(dict[str, object], row.get("detail") or {}).get("code")
        for row in rejected
        } == {
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "MALFORMED_BROAD_REPLACE_BODY",
            "PARTIAL_WHOLE_SECTION_PAYLOAD",
            # A section-genitive descendant-scope cue that does not resolve to the
            # target is now witnessed (was a silent ``return False``); additive
            # observability only — the replay/drop decisions are unchanged.
            "UNRESOLVED_DESCENDANT_SCOPE_CUE",
        }


def test_strict_replay_emits_explicit_source_pathology_rejection_for_2001_1234() -> None:
    from lawvm.finland.strict_profile import FINLAND_INGESTION_V1

    replay = pinned_replay(
        "2001/1234",
        mode="legal_pit",
        strict_profile=FINLAND_INGESTION_V1,
    )

    rejected = [
        row for row in replay.projection_rows()
        if row.get("kind") == "APPLY.SOURCE_PATHOLOGY_DETECTED"
    ]
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in {
        cast(dict[str, object], row.get("detail") or {}).get("code")
        for row in rejected
    }


def test_replay_xml_1986_609_applies_2021_657_subsection_replace_inside_section_omission_shell() -> None:
    replay = pinned_replay(
        "1986/609",
        mode="official_consolidation",
    )

    section = next(
        node
        for node in replay.replay_fold_state.ir.children
        if node.kind is IRNodeKind.HCONTAINER
    )
    sec3 = next(child for child in section.children if child.kind is IRNodeKind.SECTION and child.label == "3")
    sec3_text = irnode_to_text(sec3)

    assert "valtioon, hyvinvointialueeseen, kuntaan" in sec3_text
    assert "valtioon, kuntaan tai muuhun julkisyhteisöön" not in sec3_text
    heading = next(child for child in sec3.children if child.kind is IRNodeKind.HEADING)
    assert heading.text == "Määritelmiä"


def test_replay_xml_1920_26_applies_conclusions_repeal_clause_for_section_6() -> None:
    replay = pinned_replay("1920/26", mode="official_consolidation", quiet=True)
    sec6 = replay.find_section("6")
    assert sec6 is not None
    assert sec6.attrs.get("lawvm_repeal_placeholder") == "1"
    assert all(child.kind is IRNodeKind.NUM for child in sec6.children)
    assert replay.find_section("26") is not None
    repeal_lifecycle_targets = {
        event.relation.target_effect.effect_id
        for event in replay.products.effect_lifecycle_events
        if event.kind == "repeal_effect"
        and event.relation is not None
        and event.relation.target_effect is not None
    }
    assert {
        "fi-effect:1958/371:snapshot_section_1",
        "fi-effect:2000/90:snapshot_section_21",
        "fi-effect:2000/90:snapshot_section_22",
        "fi-effect:2000/90:snapshot_section_23",
        "fi-effect:2000/90:snapshot_section_23a",
        "fi-effect:2000/90:snapshot_section_24",
        "fi-effect:2000/90:snapshot_section_25",
    }.issubset(repeal_lifecycle_targets)
    assert all(
        event.executable is True
        for event in replay.products.effect_lifecycle_events
        if event.kind == "repeal_effect"
    )


def test_replay_xml_2004_699_preserves_section_31_items_when_2013_984_inserts_subsection_2(
    replay_2004_699_finlex_oracle: Any,
) -> None:
    sec31 = replay_2004_699_finlex_oracle.find_section("31")

    assert sec31 is not None
    sub1 = next(child for child in sec31.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    sub2 = next(child for child in sec31.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")
    paragraphs = [child for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH]
    assert [child.label for child in paragraphs] == ["1", "2", "3", "4", "5", "6"]
    para5 = next(child for child in paragraphs if child.label == "5")
    assert para5.attrs.get("lawvm_repeal_placeholder") == "1"
    assert "Euroopan keskuspankkiin" in irnode_to_text(sub2)


def test_replay_xml_2004_699_exact_section_replaces_do_not_keep_stale_subsection_tails(
    replay_2004_699_finlex_oracle: Any,
) -> None:
    sec7 = replay_2004_699_finlex_oracle.find_section("7")
    sec12 = replay_2004_699_finlex_oracle.find_section("12")
    sec21 = replay_2004_699_finlex_oracle.find_section("21")
    sec23 = replay_2004_699_finlex_oracle.find_section("23")
    sec32 = replay_2004_699_finlex_oracle.find_section("32")

    assert sec7 is not None
    assert sec12 is not None
    assert sec21 is not None
    assert sec23 is not None
    assert sec32 is not None

    assert [child.label for child in sec7.children if child.kind is IRNodeKind.SUBSECTION] == ["1"]
    assert [child.label for child in sec12.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2"]
    assert [child.label for child in sec21.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2", "3"]
    assert [child.label for child in sec23.children if child.kind is IRNodeKind.SUBSECTION] == ["1"]
    assert [child.label for child in sec32.children if child.kind is IRNodeKind.SUBSECTION] == ["1"]


def test_group_shadow_pruning_section_targets_ignores_duplicate_same_scope_labels() -> None:
    ops = [
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_chapter="5c",
            target_part="",
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="20a",
            target_chapter="6",
            target_part="",
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="20h",
            target_chapter=None,
            target_part="",
        ),
        AmendmentOp(
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="2",
            target_chapter="7",
            target_part="II",
        ),
    ]

    got = _group_shadow_pruning_section_targets(
        ops,
        target_unit_kind="chapter",
        target_norm="5c",
        target_part="",
        duplicate_section_labels=frozenset({"1"}),
    )

    assert got == {"20a", "20h", "2"}


@pytest.mark.slow
def test_replay_xml_2010_1048_repeals_6a_lane_and_keeps_live_18b_26() -> None:
    replay = pinned_replay("2010/1048", mode="official_consolidation")
    state = replay.materialized_state

    assert state.find("chapter", "6a") is None
    assert state.find_section("15a", "6a") is None
    assert state.find_section("15b", "6a") is None
    assert state.find_section("15c", "6a") is None
    assert state.find_section("15a", "6") is None
    assert state.find_section("15b", "6") is None
    assert state.find_section("15c", "6") is None
    assert state.find_section("18b", "6a") is None
    assert state.find_section("26", "6a") is None
    assert state.find_section("18b", "7") is not None
    assert state.find_section("26", "9") is not None


def test_replay_xml_1991_1144_does_not_duplicate_section_60b_under_chapter_9a() -> None:
    replay = pinned_replay("1991/1144", mode="official_consolidation")
    state = replay.materialized_state

    assert state.find_section("60b", "9a") is None
    assert state.find_section("60b", "10") is not None


def test_replay_xml_emits_empty_operative_body_pathology_for_1998_102() -> None:
    replay_meta = {}

    pinned_replay(
        "1992/1702",
        mode="legal_pit",
        replay_meta_out=replay_meta,
    )

    assert ("1998/102", "EMPTY_OPERATIVE_BODY") in {
        (row.get("source_statute"), row.get("code")) for row in replay_meta.get("source_pathologies", [])
    }


def test_replay_xml_retargets_1962_420_section_22_heading_insert_to_chapter_four() -> None:
    compiled_ops: list[dict[str, object]] = []
    pinned_replay("1962/420", mode="legal_pit", quiet=True, compiled_ops_out=compiled_ops)

    row = next(
        row
        for row in compiled_ops
        if row.get("source_statute") == "2024/247"
        and row.get("target_norm") == "22"
        and row.get("target_special") == "otsikko"
    )

    assert row["target_chapter"] == "4"


def test_replay_xml_1989_1045_recovers_damaged_1994_section_list() -> None:
    compiled_ops: list[dict[str, object]] = []
    replay_xml("1989/1045", mode="legal_pit", quiet=True, compiled_ops_out=compiled_ops)

    got = {
        (row.get("action"), row.get("target_norm"))
        for row in compiled_ops
        if row.get("source_statute") == "1994/1265"
    }

    assert {
        ("replace", "2"),
        ("replace", "3"),
        ("replace", "5"),
        ("replace", "7"),
        ("replace", "9"),
        ("insert", "9a"),
    } <= got


def test_replay_xml_2014_122_keeps_2018_1134_new_sections_in_source_owned_chapter_two() -> None:
    compiled_ops: list[dict[str, object]] = []
    replay = replay_xml("2014/122", mode="legal_pit", quiet=True, compiled_ops_out=compiled_ops)

    rows = {
        row.get("target_norm"): row
        for row in compiled_ops
        if row.get("source_statute") == "2018/1134"
        and row.get("action") == "insert"
        and row.get("target_norm") in {"5", "6"}
    }

    assert set(rows) == {"5", "6"}
    assert {row.get("target_chapter") for row in rows.values()} == {"2"}
    assert replay.state.find_section("5", "2") is not None
    assert replay.state.find_section("6", "2") is not None


@pytest.mark.slow
def test_replay_xml_dedupes_duplicate_amendment_records_for_1978_38() -> None:
    replay_meta: dict[str, object] = {}
    failed_ops: list[FailedOp] = []

    pinned_replay(
        "1978/38",
        mode="legal_pit",
        quiet=True,
        build_full_products=False,
        replay_meta_out=replay_meta,
        failed_ops_out=failed_ops,
    )

    lineage = cast(list[dict[str, object]], replay_meta.get("lineage") or [])
    lineage_ids = [str(row.get("statute_id") or "") for row in lineage]

    assert lineage_ids.count("1997/1241") == 1
    assert lineage_ids.count("2003/741") == 1
    assert not any(getattr(failed, "amendment_id", "") == "2003/741" for failed in failed_ops)


def test_replay_xml_materializes_1962_420_section_22_once_as_commencement_section() -> None:
    result = pinned_replay("1962/420", mode="legal_pit", quiet=True)

    def _walk_sections(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> list[tuple[tuple[str, str], ...]]:
        found: list[tuple[tuple[str, str], ...]] = []
        if node.kind == IRNodeKind.SECTION and node.label == "22":
            found.append(path)
        for child in node.children:
            found.extend(_walk_sections(child, path + ((child.kind.value, child.label or ""),)))
        return found

    section_paths = _walk_sections(result.state.ir)
    assert section_paths == [(("hcontainer", ""), ("section", "22"))]

    section_22 = next(
        child
        for container in result.state.ir.children
        if container.kind is IRNodeKind.HCONTAINER
        for child in container.children
        if child.kind is IRNodeKind.SECTION and child.label == "22"
    )
    text = irnode_to_text(section_22)
    assert "Tämä laki tulee voimaan" in text


def test_tag_explicit_item_shift_after_repeal_hints_marks_matching_repeal_op() -> None:
    ops = [
        AmendmentOp(
            op_id="repeal_d",
            op_type=OpType.REPEAL,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="d",
        ),
        AmendmentOp(
            op_id="replace_c",
            op_type=OpType.REPLACE,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="c",
        ),
    ]

    got = _tag_explicit_item_shift_after_repeal_hints(
        ops,
        "kumotaan 2 §:n 1 momentin d kohdan, jolloin kohdat e-h muuttuvat kohdiksi d-g ja muutetaan 2 §:n 1 momentin c kohdan",
    )

    assert got[0].post_repeal_item_shift_label == "d"
    assert got[1].post_repeal_item_shift_label is None


def test_supplement_missing_repeals_after_item_shift_clause_adds_lost_moment_repeal() -> None:
    ops = [
        AmendmentOp(
            op_id="repeal_d",
            op_type=OpType.REPEAL,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="d",
        ),
        AmendmentOp(
            op_id="replace_c",
            op_type=OpType.REPLACE,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="c",
        ),
    ]

    got = _supplement_missing_repeals_after_item_shift_clause(
        ops,
        "kumotaan 2 §:n 1 momentin d kohdan, jolloin kohdat e-h muuttuvat kohdiksi d-g ja 2 momentin, muutetaan 2 §:n 1 momentin c kohdan",
    )

    assert ("REPEAL", "2", 2, None, "d") in {
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item, op.post_repeal_item_shift_label)
        for op in got
    }


def test_johtolause_supplements_item_shift_delegates_to_canonical_clause_surface() -> None:
    """Q4 demotion invariant: the supplement lane owns NO rival item-shift regex.

    The ``jolloin … muuttuvat kohdiksi`` family has a single canonical parser in
    ``johtolause.clause_surface``; ``johtolause_supplements`` must call it rather
    than carry a duplicate regex (two rival parsers = audit state). Pin both the
    delegation (identical results) and the absence of a private copy.
    """
    from lawvm.finland.johtolause import clause_surface as _clause_surface
    from lawvm.finland import johtolause_supplements as _supplements

    # No private item-shift parser survives in the supplement module.
    assert not hasattr(_supplements, "_parse_item_shift_clauses")
    assert not hasattr(_supplements, "_parse_item_shift_after_repeal_clauses")

    johto = (
        "kumotaan 2 §:n 1 momentin d kohdan, jolloin kohdat e-h muuttuvat "
        "kohdiksi d-g ja muutetaan 2 §:n 1 momentin c kohdan"
    )
    ops = [
        AmendmentOp(
            op_id="repeal_d",
            op_type=OpType.REPEAL,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="d",
        ),
    ]
    # The supplement tagger threads its parse through the canonical recognizer.
    canonical = _clause_surface.parse_item_shift_clauses(johto)
    assert canonical, "canonical parser must recognize the sample item-shift clause"
    tagged = _tag_explicit_item_shift_after_repeal_hints(ops, johto)
    assert tagged[0].post_repeal_item_shift_label == "d"


def test_supplement_named_table_row_mixed_clause_ops_adds_missing_replace_and_tags_rows() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.REPEAL,
            target_section="1",
            target_kind=TargetKind.SECTION,
        )
    ]

    got = _supplement_named_table_row_mixed_clause_ops(
        ops,
        (
            "kumotaan käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista annetun "
            "päätöksen 1 §:n Iitin ja Juvan käräjäoikeuksia koskevat kohdat sekä muutetaan "
            "Kouvolan ja Mikkelin käräjäoikeuksia koskevat kohdat seuraavasti:"
        ),
    )

    assert [(op.op_type, op.target_cols.target_section) for op in got] == [("REPEAL", "1"), ("REPLACE", "1")]
    assert got[0].named_row_targets == ("iitin", "juvan")
    assert got[1].named_row_targets == ("kouvolan", "mikkelin")


def test_supplement_named_table_row_mixed_clause_ops_handles_osalta_wording() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.REPEAL,
            target_section="1",
            target_kind=TargetKind.SECTION,
        )
    ]

    got = _supplement_named_table_row_mixed_clause_ops(
        ops,
        (
            "kumota käräjäoikeuksien kanslioiden ja istuntopaikkojen sijainnista annetun päätöksen "
            "1 §:n Pirkanmaan käräjäoikeuden osalta ja muuttaa 1 §:n Tampereen käräjäoikeuden osalta seuraavasti:"
        ),
    )

    assert [(op.op_type, op.target_cols.target_section) for op in got] == [("REPEAL", "1"), ("REPLACE", "1")]
    assert got[0].named_row_targets == ("pirkanmaan",)
    assert got[1].named_row_targets == ("tampereen",)


def test_tag_named_table_row_single_clause_ops_tags_single_replace_clause() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.REPLACE,
            target_section="1",
            target_kind=TargetKind.SECTION,
        )
    ]

    got = _tag_named_table_row_single_clause_ops(
        ops,
        "muutetaan päätöksen 1 §:n Iisalmen käräjäoikeutta koskevan kohdan seuraavasti:",
    )

    assert [(op.op_type, op.target_cols.target_section) for op in got] == [("REPLACE", "1")]
    assert got[0].named_row_targets == ("iisalmen",)


def test_supplement_item_and_moment_clause_ops_recovers_item_targets() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.INSERT,
            target_section="",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
        )
    ]
    johto = (
        "muutetaan 24 §:n 1 momentin kohdat 1 ja 6 sekä 5 momentti, "
        "sekä lisätään 24 §:n 1 momenttiin uusi kohta 8, seuraavasti:"
    )

    got = _supplement_item_and_moment_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in got
    ] == [
        ("INSERT", "24", 1, "8"),
        ("REPLACE", "24", 1, "1"),
        ("REPLACE", "24", 1, "6"),
        ("REPLACE", "24", 5, None),
    ]
    assert got[0].witness_rule_id == "fi.item_and_moment_target_supplement.v1"
    assert got[0].extraction_provenance_tags == ("item_and_moment_target_supplement",)


def test_supplement_jolloin_moment_renumber_ops_recovers_insert_continuation_shift() -> None:
    ops = [
        AmendmentOp(
            op_id="insert_15_2",
            op_type=OpType.INSERT,
            target_section="15",
            target_kind=TargetKind.SECTION,
            target_paragraph=2,
        )
    ]
    johto = (
        "lisätään 15 §:ään uusi 2 ja 3 momentti, jolloin nykyinen 2 ja 3 "
        "momentti siirtyvät 4 ja 5 momentiksi, lakiin uusi 24 a-24 d §"
    )

    got = _supplement_jolloin_moment_renumber_ops(ops, johto)
    recovered_inserts = [
        op
        for op in got
        if op.op_type == "INSERT"
        and op.extraction_provenance_tags == ("jolloin_moment_renumber_supplement",)
    ]
    renumbers = [op for op in got if op.op_type == "RENUMBER"]

    assert [
        (op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in got
        if op.op_type == "INSERT"
    ] == [
        ("15", 2),
        ("15", 3),
    ]
    assert [(op.target_cols.target_section, op.target_cols.target_paragraph) for op in recovered_inserts] == [
        ("15", 3)
    ]
    assert [
        (op.target_cols.target_section, op.target_cols.target_paragraph, op.lo.destination.path[-1][1])
        for op in renumbers
        if op.lo is not None and op.lo.destination is not None
    ] == [("15", 2, "4"), ("15", 3, "5")]
    assert all(op.witness_rule_id == "fi.jolloin_renumber" for op in renumbers)
    assert all(
        op.extraction_provenance_tags == ("jolloin_moment_renumber_supplement",)
        for op in renumbers
    )


def test_supplement_mixed_explicit_clause_ops_recovers_skipped_targets() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.REPLACE,
            target_section="13",
            target_kind=TargetKind.SECTION,
            numbered_table_targets=("4",),
        ),
        AmendmentOp(
            op_id="op1",
            op_type=OpType.REPLACE,
            target_section="26",
            target_kind=TargetKind.SECTION,
            numbered_table_targets=("8",),
        ),
        AmendmentOp(
            op_id="op2",
            op_type=OpType.REPLACE,
            target_section="33",
            target_kind=TargetKind.SECTION,
            numbered_table_targets=("11",),
        ),
    ]
    johto = (
        "muutetaan 13 §:n taulukko 4, 25 §, 26 §:n taulukko 8, "
        "33 §:n taulukko 11 ja 2 momentti, 41 §:n 1 momentin kohta 1 "
        "ja 43 § sekä lisätään 24 §:n 1 momenttiin uusi kohta 8 ja "
        "26 §:ään uusi 3 momentti, seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item, op.numbered_table_targets)
        for op in got
    ] == [
        ("REPLACE", "13", None, None, ("4",)),
        ("REPLACE", "26", None, None, ("8",)),
        ("REPLACE", "33", None, None, ("11",)),
        ("REPLACE", "41", 1, "1", ()),
        ("REPLACE", "33", 2, None, ("11",)),
        ("INSERT", "26", 3, None, ("8",)),
        ("REPLACE", "25", None, None, ()),
        ("REPLACE", "43", None, None, ()),
    ]
    recovered = got[3:]
    assert {op.witness_rule_id for op in recovered} == {"fi.mixed_explicit_target_supplement.v1"}
    assert {op.extraction_provenance_tags for op in recovered} == {("mixed_explicit_target_supplement",)}


def test_supplement_mixed_explicit_clause_ops_recovers_terminal_section_list_and_moments() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_18",
            op_type=OpType.REPLACE,
            target_section="18",
            target_kind=TargetKind.SECTION,
        ),
        AmendmentOp(
            op_id="replace_23",
            op_type=OpType.REPLACE,
            target_section="23",
            target_kind=TargetKind.SECTION,
        ),
    ]
    johto = (
        "kumotaan työterveyslaitoksen toiminnasta ja rahoituksesta annetun "
        "asetuksen 3-5, 13 ja 15 §, muutetaan 3 §:n edellä oleva väliotsikko, "
        "6-9, 11, 12, 16 aja 18 §, 19 §:n 1 momentti, "
        "20 §:n 1 momentti, 21 §:n 1 momentti ja 23 §,"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in got
        if op.op_type == "REPLACE" and op.target_cols.target_unit_kind == "section"
    ] == [
        ("REPLACE", "18", None),
        ("REPLACE", "23", None),
        ("REPLACE", "19", 1),
        ("REPLACE", "20", 1),
        ("REPLACE", "21", 1),
        ("REPLACE", "6", None),
        ("REPLACE", "7", None),
        ("REPLACE", "8", None),
        ("REPLACE", "9", None),
        ("REPLACE", "11", None),
        ("REPLACE", "12", None),
        ("REPLACE", "16a", None),
    ]
    recovered = got[2:]
    assert {op.witness_rule_id for op in recovered} == {"fi.mixed_explicit_target_supplement.v1"}
    assert {op.extraction_provenance_tags for op in recovered} == {("mixed_explicit_target_supplement",)}


def test_supplement_mixed_explicit_clause_ops_does_not_add_moment_for_section_with_subtarget() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_7_1",
            op_type=OpType.REPLACE,
            target_section="7",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
        )
    ]
    johto = (
        "muutetaan asetuksen 7 §:n 1 momentin 2, 7 §:n 3 momentti "
        "ja 8 §:n 1 momentti, sellaisina kuin niistä on 7 §:n 1 "
        "momentin 2 kohta asetuksessa 869/2023, seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in got
    ] == [
        ("REPLACE", "7", 1),
        ("REPLACE", "8", 1),
    ]


def test_supplement_mixed_explicit_clause_ops_does_not_add_bare_section_for_moment_item_target() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_8_2_13",
            op_type=OpType.REPLACE,
            target_section="8",
            target_kind=TargetKind.SECTION,
            target_paragraph=2,
            target_item="13",
        )
    ]
    johto = (
        "muutetaan työterveyslaitoksen toiminnasta ja rahoituksesta "
        "29 päivänä kesäkuuta 1978 annetun asetuksen ( 501/1978 ) "
        "8 § 2 momentin 13 kohta, sellaisena kuin se on asetuksessa "
        "1307/1993, seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph, op.target_cols.target_item)
        for op in got
    ] == [("REPLACE", "8", 2, "13")]


def test_supplement_mixed_explicit_clause_ops_preserves_explicit_chapter_for_moment_insert() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_chapter_2_section_3_heading",
            op_type=OpType.REPLACE,
            target_section="3",
            target_kind=TargetKind.SECTION,
            target_chapter="2",
            target_special="otsikko",
        )
    ]
    johto = (
        "muutetaan rikostorjunnasta Tullissa annetun lain (623/2015) 1 luvun "
        "3 §:n 12 kohta ja 2 luvun 3 §:n otsikko, sekä lisätään 1 luvun "
        "3 §:ään uusi 13 kohta sekä 2 lukuun uusi 2 a – ja 2 b §, "
        "2 luvun 3 §:ään uusi 2 momentti sekä 2 lukuun uusi 5 – ja 6 § seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    recovered = [
        op
        for op in got
        if op.op_type == "INSERT"
        and op.target_cols.target_section == "3"
        and op.target_cols.target_paragraph == 2
        and op.target_cols.target_unit_kind == "section"
    ]
    assert len(recovered) == 1
    assert recovered[0].target_cols.target_chapter == "2"
    assert recovered[0].scope_provenance_tags == ("chapter_scope_from_explicit_chunk",)
    assert recovered[0].witness_rule_id == "fi.mixed_explicit_target_supplement.v1"
    assert recovered[0].extraction_provenance_tags == ("mixed_explicit_target_supplement",)


def test_supplement_mixed_explicit_clause_ops_does_not_treat_repeal_body_sections_as_targets() -> None:
    ops = [
        AmendmentOp(
            op_id="repeal_9",
            op_type=OpType.REPEAL,
            target_section="9",
            target_kind=TargetKind.SECTION,
        )
    ]
    johto = (
        "Tällä asetuksella kumotaan poliisin henkilörekistereistä 15 päivänä "
        "syyskuuta 1995 annetun asetuksen (1116/1995) 9 §, sellaisena kuin se "
        "on asetuksessa 1144/1998. Seuraavasti: 1 § Tällä asetuksella kumotaan "
        "9 §. 2 § Tämä asetus tulee voimaan 1 päivänä syyskuuta 2002."
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert got == ops


def test_supplement_mixed_explicit_clause_ops_keeps_possessive_moment_refs_child_scoped() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_2_2",
            op_type=OpType.REPLACE,
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=2,
        )
    ]
    johto = (
        "muutetaan 2 § n 2 momentti, 4 § n 2 ja 3 momentti, "
        "5 § n 1 momentti sekä 6 § seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in got
    ] == [
        ("REPLACE", "2", 2),
        ("REPLACE", "6", None),
    ]


def test_supplement_mixed_explicit_clause_ops_does_not_convert_chapter_heading_pair_to_section_replace() -> None:
    ops = [
        AmendmentOp(
            op_id="replace_chapter_12",
            op_type=OpType.REPLACE,
            target_kind=TargetKind.CHAPTER,
            target_section="12",
        ),
        AmendmentOp(
            op_id="insert_9_12",
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_chapter="9",
            target_section="12",
        ),
        AmendmentOp(
            op_id="insert_9_13",
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_chapter="9",
            target_section="13",
        ),
    ]
    johto = (
        "muutetaan kauppakaaren 12 luvun nimike ja 12 § ja lisätään "
        "9 lukuun kumotun 12 §:n sijaan uusi 12 § sekä uusi 13 § seuraavasti:"
    )

    got = _supplement_mixed_explicit_clause_ops(ops, johto)

    assert got == ops


def test_parse_ops_fallback_recovers_colonless_moment_target_list() -> None:
    johto = (
        "muutetaan vähemmistövaltuutetusta 26 päivänä heinäkuuta 2001 annetun "
        "valtioneuvoston asetuksen (687/2001) 2 § 2 momentti, "
        "4 § 2 ja 3 momentti, 5 § 1 momentti ja 6 § seuraavasti:"
    )

    got = parse_ops_fallback_heuristic(johto)

    assert [
        (op.op_type, op.target_cols.target_section, op.target_cols.target_paragraph)
        for op in got
    ] == [
        ("REPLACE", "2", 2),
        ("REPLACE", "4", 2),
        ("REPLACE", "4", 3),
        ("REPLACE", "5", 1),
        ("REPLACE", "6", None),
    ]


def test_numbered_table_proxy_splits_from_child_targets_before_group_compile() -> None:
    table_proxy = AmendmentOp(
        op_id="table_proxy",
        op_type=OpType.REPLACE,
        target_section="33",
        target_kind=TargetKind.SECTION,
        numbered_table_targets=("11",),
    )
    moment_replace = AmendmentOp(
        op_id="moment_replace",
        op_type=OpType.REPLACE,
        target_section="33",
        target_kind=TargetKind.SECTION,
        target_paragraph=2,
    )

    got = _split_numbered_table_child_group_ops([table_proxy, moment_replace])

    assert got == ([table_proxy], [moment_replace])


def test_parse_ops_fallback_recovers_citation_prose_root_part_insert() -> None:
    text = (
        "lisätään liikenteen palvelusta annetun lain (320/2017) I osan 1 luvun "
        "2 §:ään, sellaisena kuin se on laissa 301/2018, uusi 10 kohta sekä "
        "lakiin uusi II A osa seuraavasti:"
    )

    got = parse_ops_fallback_heuristic(text)

    assert any(
        op.op_type == "INSERT"
        and op.target_cols.target_unit_kind == "part"
        and op.target_cols.target_section == "2a"
        for op in got
    )


def test_supplement_sparse_osalta_row_omission_repeals_owns_action_recovery() -> None:
    got, findings = _supplement_sparse_osalta_row_omission_repeals(
        [],
        (
            "muutetaan valtion oikeusaputoimistojen ja niiden sivutoimistojen sijainnista "
            "annetun oikeusministeriön päätöksen 1 §:ää Oulunseudun oikeusaputoimiston "
            "Pudasjärven sivutoimiston osalta seuraavasti:"
        ),
        amendment_id="1999/77",
    )

    assert [(op.op_type, op.target_cols.target_section, op.named_row_targets) for op in got] == [
        ("REPEAL", "1", ("Pudasjärven",))
    ]
    assert got[0].witness_rule_id == "fi.sparse_osalta_row_omission_repeal.v1"
    assert got[0].extraction_provenance_tags == ("sparse_osalta_row_omission_repeal",)
    assert [finding.kind for finding in findings] == ["ELAB.SPARSE_PARTIAL_SCOPE_ROW_OMISSION_REPEAL"]
    assert findings[0].detail["source_verb"] == "muutetaan"
    assert findings[0].detail["lowered_action"] == "REPEAL"


def test_tag_named_table_row_single_clause_ops_recovers_regional_table_sections() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type=OpType.REPLACE,
            target_section="13",
            target_kind=TargetKind.SECTION,
        )
    ]

    got = _tag_named_table_row_single_clause_ops(
        ops,
        (
            "muutetaan 20 päivänä syyskuuta 1991 annetun metsäveroasetuksen "
            "(1208/91) 13 §:n Uudenmaan, Turun ja Porin, Hämeen, Kymen, "
            "Mikkelin, Kuopion, Pohjois-Karjalan, Vaasan, Keski-Suomen ja "
            "Oulun lääniä koskevat kohdat, 14 §:n Uudenmaan, Turun ja Porin, "
            "Hämeen ja Keski-Suomen lääniä koskevat kohdat ja 15 §,"
        ),
    )

    assert [(op.op_type, op.target_cols.target_section) for op in got] == [("REPLACE", "13"), ("REPLACE", "14")]
    assert got[0].named_row_targets == (
        "uudenmaan",
        "turun ja porin",
        "hämeen",
        "kymen",
        "mikkelin",
        "kuopion",
        "pohjois-karjalan",
        "vaasan",
        "keski-suomen",
        "oulun",
    )
    assert got[1].named_row_targets == (
        "uudenmaan",
        "turun ja porin",
        "hämeen",
        "keski-suomen",
    )


def test_replay_xml_1997_660_renumbers_lettered_items_after_explicit_repeal_shift() -> None:
    compiled_ops = []
    master = pinned_replay("1997/660", mode="legal_pit", compiled_ops_out=compiled_ops)
    sec = master.find_section("2")
    text = irnode_to_text(sec)

    assert "d) säiliöllä" in text
    assert "e) säiliöllä" not in text
    assert "g) lyhenteellä rn" in text
    assert "h) lyhenteellä rn" not in text
    assert (
        "repeal",
        "2",
        "2",
        None,
    ) in {
        (
            row.get("action"),
            row.get("target_norm"),
            row.get("target_paragraph") or None,
            row.get("target_item") or None,
        )
        for row in compiled_ops
        if row.get("source_statute") == "1998/846"
    }
    assert all("resolution_hint" not in row for row in compiled_ops)


def test_replay_xml_2002_504_does_not_duplicate_shared_tail_after_2009_1525() -> None:
    master = pinned_replay("2002/504", mode="legal_pit")
    sec = master.find_section("10")
    text = irnode_to_text(sec)

    assert text.count("rikostaustan selvittämisrikkomuksesta") == 1
    # The exact wording depends on which amendment is active for the
    # "ilmoittaa X" fragment; both are valid replay outputs.
    assert "2) rikkoo 4 §:n 3 momentissa säädetyn velvollisuuden ilmoittaa" in text


def test_replay_xml_1993_616_keeps_tail_inserted_moments_in_ascending_order() -> None:
    master = pinned_replay("1993/616", mode="legal_pit")
    sec = master.find_section("3")
    subsections = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION]

    assert [c.label for c in subsections] == ["1", "2", "3", "4", "5", "6"]
    assert "Maa- ja metsätalousministeriön asetuksella voidaan antaa tarkempia säännöksiä" in irnode_to_text(
        subsections[4]
    )
    assert "Riistanhoitoyhdistykselle myönnetty valtionavustus on käytettävä" in irnode_to_text(subsections[5])


def test_replay_xml_2015_1525_no_botanical_list_duplication() -> None:
    """Regression: amendment 2018/802 uses leading+trailing section-level omissions
    bracketing a single subsection replace.  The trailing omission must NOT be
    re-attached to the replacement subsection as a tail — doing so re-splices the
    old plant list after the new one, producing two copies of the species list in §1.

    Bug: _attach_terminal_section_omission_to_tail_subsection fired because
    target_paragraph==2==len(live_subsecs), but the section-level trailing omission
    is structural, not a subsection-level tail marker.
    """
    master = pinned_replay("2015/1525", mode="official_consolidation")
    sec = master.find_section("1")
    assert sec is not None
    text = irnode_to_text(sec)

    # The new list (with Linnaean "(L.)") must appear exactly once.
    assert text.count("Secale cereale L.") == 1, (
        f"Expected 'Secale cereale L.' exactly once; got {text.count('Secale cereale L.')} times"
    )
    # The old list (without "(L.)") must NOT appear — the replacement is complete.
    # Presence of bare "Secale cereale)" without trailing " L." indicates duplication.
    # The 2018/802 amendment also adds englanninraiheinä which the base text lacks.
    assert "englanninraiheinän" in text, "Expected englanninraiheinä from 2018/802 to be present"
    # Total subsections in §1 must remain 2 (not grow to 3 from spliced-in old content).


    subsecs = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION]
    assert len(subsecs) == 2, f"Expected 2 subsections in §1, got {len(subsecs)}"


def test_uncovered_body_allows_sections_from_muutetaan_whole_chapter() -> None:
    """Bug A: When the johtolause says 'muutetaan 45 luku' (whole-chapter replace)
    AND mentions specific section refs elsewhere (making johto_mentioned_labels
    non-empty), sections within chapter 45 must NOT be filtered by the johto guard.

    Previously, _label_allowed_by_johto only recognised 'lisätään uusi X luku'
    (new chapter insertions) and missed 'muutetaan X luku' (whole-chapter
    replacements).  Sections of the replaced chapter were silently dropped.
    """
    # Master: chapter 45 with one existing section
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="45",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="45 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    # Preamble: "muutetaan 45 luku, lisätään 2 luvun 14 a §:ään uusi 4 momentti"
    # — the "14 a §" reference makes johto_mentioned_labels non-empty,
    #   which previously caused the guard to block sections in chapter 45.
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="modifications">
                  muutetaan rikoslain 45 luku, lisätään 2 luvun 14 a §:ään uusi 4 momentti
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>45 luku</num>
              <section>
                <num>2 §</num>
                <subsection><content><p>new sec 2 text</p></content></subsection>
              </section>
              <section>
                <num>3 §</num>
                <subsection><content><p>new sec 3 text</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "2000/559").state
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2000/559",
        failed_ops_out=[],
    )
    # Sections 2 and 3 from chapter 45 body must NOT be filtered out
    recovered_labels = {rop.op.target_cols.target_section for rop in rops}
    assert "2" in recovered_labels, (
        f"Section 2 from chapter 45 was filtered by johto guard; recovered: {recovered_labels}"
    )
    assert "3" in recovered_labels, (
        f"Section 3 from chapter 45 was filtered by johto guard; recovered: {recovered_labels}"
    )


def test_uncovered_body_allows_sections_from_uusi_chapter_range() -> None:
    """Bug A sub-bug: 'uusi 47―49 luku' (chapter range with en-dash) must expand
    to chapters 47, 48, 49 and allow all their sections through the johto guard.

    Previously, the regex only matched single chapter numbers after 'uusi',
    not range forms.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="47",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="47 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                        ),
                    ),
                ),
            ),
        )
    )
    ctx = _statute_context(state.ir)
    # Preamble: section mentions make johto_mentioned_labels non-empty,
    # plus "uusi 47\u201349 luku" (en-dash range)
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <preamble>
            <formula>
              <blockContainer>
                <block name="modifications">
                  muutetaan rikoslain 3 § sekä lisätään lakiin uusi 47\u201349 luku
                </block>
              </blockContainer>
            </formula>
          </preamble>
          <body>
            <chapter>
              <num>47 luku</num>
              <section>
                <num>2 §</num>
                <subsection><content><p>ch47 sec2</p></content></subsection>
              </section>
            </chapter>
            <chapter>
              <num>48 luku</num>
              <section>
                <num>7 §</num>
                <subsection><content><p>ch48 sec7</p></content></subsection>
              </section>
            </chapter>
          </body>
        </akn>
        """
    )

    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state = _pre_create_amendment_chapters(state, muutos_body_el, "1995/578").state
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "1995/578",
        failed_ops_out=[],
    )
    recovered = {(rop.op.target_cols.target_chapter, rop.op.target_cols.target_section) for rop in rops}
    assert ("47", "2") in recovered, f"Section 47/2 was filtered; recovered: {recovered}"
    assert ("48", "7") in recovered, f"Section 48/7 was filtered; recovered: {recovered}"


# ---------------------------------------------------------------------------
# grafter_simple: label-based subsection resolution (Pattern C regression)
# ---------------------------------------------------------------------------


def test_subsection_replace_uses_label_not_position_current_apply_path() -> None:
    """Current subsection replace helper must still resolve by label, not index."""
    # Coverage lives primarily in tests/test_fi_apply.py; keep one fallback-era
    # assertion here so the older grafter regression family still points at the
    # current executor path instead of the deleted grafter_simple module.
    from lawvm.core.tree_ops import resolve as tree_resolve
    from tests.test_fi_apply import _FINLEX_ORACLE, _body, _content, _make_state, _modified, _op, _sec, _sub
    from lawvm.finland.apply_subsection_ops import _apply_subsection_replace

    sec = _sec(
        "5",
        _sub("1", _content("First moment")),
        _sub("1a", _content("Inserted 1a")),
        _sub("2", _content("Second moment original")),
    )
    body = _body(sec)
    sec_path = [("section", "5")]
    state = _make_state(body)
    subsecs = [c for c in sec.children if c.kind is IRNodeKind.SUBSECTION]
    replace_sub = _sub("2", _content("Second moment REPLACED"))
    op = _op(op_type=OpType.REPLACE, target_section="5", target_paragraph=2)

    result = _apply_subsection_replace(
        state, op, sec_path, sec, subsecs, replace_sub, None, _FINLEX_ORACLE, "[test] REPLACE 5 § 2 mom"
    )
    result = _modified(state, result)
    replace_sec = tree_resolve(result.ir, sec_path)
    assert replace_sec is not None
    replace_subsecs = [c for c in replace_sec.children if c.kind is IRNodeKind.SUBSECTION]

    sub_1a = next((s for s in replace_subsecs if s.label == "1a"), None)
    assert sub_1a is not None
    assert any(c.text == "Inserted 1a" for c in sub_1a.children)

    sub_2 = next((s for s in replace_subsecs if s.label == "2"), None)
    assert sub_2 is not None
    assert any(c.text == "Second moment REPLACED" for c in sub_2.children)


def test_dedup_children_by_label_removes_duplicate_sections() -> None:
    """dedup_children_by_label removes earlier duplicate sections at body/chapter scope."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import dedup_children_by_label

    def _sec(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            text="",
            attrs={},
            children=(
                IRNode(
                    kind=IRNodeKind.CONTENT,
                    label=None,
                    text=text,
                    attrs={},
                    children=(),
                ),
            ),
        )

    # Body with '14a' appearing 3 times (stale, stale, authoritative).
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(
            _sec("14", "original 14"),
            _sec("14a", "stale first"),
            _sec("14a", "stale second"),
            _sec("14a", "authoritative last"),
            _sec("15", "original 15"),
        ),
    )

    result = dedup_children_by_label(body)

    section_labels = [c.label for c in result.children if c.kind is IRNodeKind.SECTION]
    assert section_labels == ["14", "14a", "15"], (
        f"Expected deduplicated labels ['14', '14a', '15'], got {section_labels}"
    )
    # The surviving '14a' must be the last (authoritative) occurrence.
    surviving_14a = next(c for c in result.children if c.kind is IRNodeKind.SECTION and c.label == "14a")
    assert surviving_14a.children[0].text == "authoritative last", (
        f"Expected authoritative last but got {surviving_14a.children[0].text!r}"
    )


def test_dedup_children_by_label_removes_duplicate_sections_in_chapter() -> None:
    """dedup_children_by_label deduplicates inside a chapter container."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import dedup_children_by_label

    def _sec(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            text="",
            attrs={},
            children=(
                IRNode(
                    kind=IRNodeKind.CONTENT,
                    label=None,
                    text=text,
                    attrs={},
                    children=(),
                ),
            ),
        )

    chapter = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        text="",
        attrs={},
        children=(
            IRNode(
                kind=IRNodeKind.HEADING,
                label=None,
                text="Chapter 3",
                attrs={},
                children=(),
            ),
            _sec("20", "original 20"),
            _sec("20a", "stale 20a"),
            _sec("20a", "replaced 20a"),
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(chapter,),
    )

    result = dedup_children_by_label(body)
    result_chapter = next(c for c in result.children if c.kind is IRNodeKind.CHAPTER)
    sec_labels = [c.label for c in result_chapter.children if c.kind is IRNodeKind.SECTION]
    assert sec_labels == ["20", "20a"], (
        f"Expected ['20', '20a'], got {sec_labels}"
    )
    surviving = next(c for c in result_chapter.children if c.kind is IRNodeKind.SECTION and c.label == "20a")
    assert surviving.children[0].text == "replaced 20a"


def test_dedup_children_by_label_noop_when_no_duplicates() -> None:
    """dedup_children_by_label returns the same object when there are no duplicates."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import dedup_children_by_label, has_dedup_label_duplicates

    def _sec(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text="", attrs={}, children=())

    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(_sec("1"), _sec("2"), _sec("3")),
    )

    result = dedup_children_by_label(body)
    assert result is body, "Should return identical object when no deduplication needed"
    assert not has_dedup_label_duplicates(body)


def test_has_dedup_label_duplicates_matches_owned_dedup_scope() -> None:
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import has_dedup_label_duplicates

    clean = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="2"),
        ),
    )
    duplicate_section = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),
        ),
    )
    duplicate_heading_label = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.HEADING, label="1"),
            IRNode(kind=IRNodeKind.HEADING, label="1"),
        ),
    )

    assert not has_dedup_label_duplicates(clean)
    assert has_dedup_label_duplicates(duplicate_section)
    assert not has_dedup_label_duplicates(duplicate_heading_label)


def test_has_dedup_label_duplicates_no_drift_from_owned_dedup() -> None:
    """The cheap predicate never disagrees with dedup_children_by_label.

    Regression guard for the prior hand-rolled enum if/elif dispatch, which
    only recognised SECTION/CHAPTER/PART/SUBSECTION and silently dropped any
    other dedup-target kind via ``else: continue``.  subsection is in
    _DEDUP_TARGET_KINDS and has an IRNodeKind enum form, so a drift here would
    surface as the predicate reporting "no duplicates" while the owned dedup
    still removes them.
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import (
        dedup_children_by_label,
        has_dedup_label_duplicates,
    )

    # subsection (enum form) duplicate inside a section container.
    dup_subsection = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
        ),
    )
    assert has_dedup_label_duplicates(dup_subsection)
    # The owned dedup actually removes one -> predicate must agree it had work.
    deduped = dedup_children_by_label(dup_subsection)
    assert deduped is not dup_subsection
    assert not has_dedup_label_duplicates(deduped)


def test_dedup_children_by_label_removes_duplicate_subsections_in_section() -> None:
    """dedup_children_by_label deduplicates subsection siblings inside a section."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import dedup_children_by_label

    def _sub(label: str, text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=label,
            text="",
            attrs={},
            children=(
                IRNode(
                    kind=IRNodeKind.CONTENT,
                    label=None,
                    text=text,
                    attrs={},
                    children=(),
                ),
            ),
        )

    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        text="",
        attrs={},
        children=(
            _sub("1", "stale first"),
            _sub("2", "keep two"),
            _sub("1", "authoritative last"),
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(section,),
    )

    result = dedup_children_by_label(body)
    result_section = next(c for c in result.children if c.kind is IRNodeKind.SECTION)
    sub_labels = [c.label for c in result_section.children if c.kind is IRNodeKind.SUBSECTION]
    assert sub_labels == ["2", "1"], f"Expected ['2', '1'], got {sub_labels}"
    surviving = next(c for c in result_section.children if c.kind is IRNodeKind.SUBSECTION and c.label == "1")
    assert surviving.children[0].text == "authoritative last"


def test_emit_structural_dedup_warning_records_warning_and_finding() -> None:
    from lawvm.core.ir import IRNode
    from lawvm.finland.replay_findings import _emit_structural_dedup_warning

    before_ir = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="", attrs={}, children=()),
            IRNode(kind=IRNodeKind.SECTION, label="1", text="", attrs={}, children=()),
        ),
    )
    after_ir = IRNode(kind=IRNodeKind.BODY, label=None, text="", attrs={}, children=())
    replay_findings = []
    replay_meta: dict[str, object] = {}

    result = _emit_structural_dedup_warning(
        phase="replay_fold",
        before_ir=before_ir,
        after_ir=after_ir,
        source_statute="1976/673",
        replay_findings=replay_findings,
        replay_meta_out=replay_meta,
    )

    assert result is after_ir
    assert replay_meta["structural_dedup_warnings"] == [
        {
            "phase": "replay_fold",
            "message": "Global same-kind+label dedup backstop modified the replay tree.",
            "duplicates": [
                {
                    "path": "body",
                    "kind": "section",
                    "label": "1",
                }
            ],
        }
    ]
    assert len(replay_findings) == 1
    finding = replay_findings[0]
    assert finding.kind == "APPLY.GLOBAL_LABEL_DEDUP_APPLIED"
    assert finding.detail["phase"] == "replay_fold"
    assert finding.detail["duplicates"] == (
        {
            "path": "body",
            "kind": "section",
            "label": "1",
        },
    )
    assert finding.source_statute == "1976/673"


def test_emit_structural_dedup_warning_noop_when_tree_unchanged() -> None:
    from lawvm.core.ir import IRNode
    from lawvm.finland.replay_findings import _emit_structural_dedup_warning

    tree = IRNode(kind=IRNodeKind.BODY, label=None, text="", attrs={}, children=())
    replay_findings = []
    replay_meta: dict[str, object] = {}

    result = _emit_structural_dedup_warning(
        phase="materialized",
        before_ir=tree,
        after_ir=tree,
        source_statute="1976/673",
        replay_findings=replay_findings,
        replay_meta_out=replay_meta,
    )

    assert result is tree
    assert replay_findings == []
    assert replay_meta == {}


def test_resort_children_sorts_out_of_order_sections() -> None:
    """resort_children sorts labeled siblings of the same kind into canonical order."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import resort_children, check_invariants

    def _sec(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text="", attrs={}, children=())

    # Sections deliberately out of order: 5, 3, 7
    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(_sec("5"), _sec("3"), _sec("7")),
    )
    assert check_invariants(body) != [], "pre-condition: should have sort violations"

    result = resort_children(body)
    labels = [c.label for c in result.children if c.kind is IRNodeKind.SECTION]
    assert labels == ["3", "5", "7"], f"Expected ['3', '5', '7'], got {labels}"
    assert check_invariants(result) == [], "post-condition: no invariant violations"


def test_resort_children_noop_when_already_sorted() -> None:
    """resort_children returns the same object when children are already in order."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import resort_children

    def _sec(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text="", attrs={}, children=())

    body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        attrs={},
        children=(_sec("1"), _sec("2"), _sec("3")),
    )
    result = resort_children(body)
    assert result is body, "Should return identical object when already sorted"


def test_resort_children_preserves_non_labeled_children_positions() -> None:
    """resort_children does not move heading/num/content children."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import resort_children

    heading = IRNode(
        kind=IRNodeKind.HEADING,
        label=None,
        text="Chapter title",
        attrs={},
        children=(),
    )
    num = IRNode(kind=IRNodeKind.NUM, label=None, text="1.", attrs={}, children=())

    def _sec(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.SECTION, label=label, text="", attrs={}, children=())

    # heading and num first, then out-of-order sections
    chapter = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        text="",
        attrs={},
        children=(num, heading, _sec("5"), _sec("3")),
    )
    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", attrs={}, children=(chapter,))
    result = resort_children(body)

    result_chapter = next(c for c in result.children if c.kind is IRNodeKind.CHAPTER)
    kinds_order = [str(c.kind) for c in result_chapter.children]
    # num and heading must remain at indices 0 and 1
    assert kinds_order[:2] == ["num", "heading"], f"Non-labeled children moved: {kinds_order}"
    sec_labels = [c.label for c in result_chapter.children if c.kind is IRNodeKind.SECTION]
    assert sec_labels == ["3", "5"], f"Sections not sorted: {sec_labels}"


def test_resort_children_sorts_paragraphs_within_subsection() -> None:
    """resort_children fixes paragraph-level sort violations (the 92% case)."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import resort_children, check_invariants

    def _para(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text="", attrs={}, children=())

    # Paragraphs live inside subsections per _NESTING_ORDER
    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        text="",
        attrs={},
        children=(_para("3"), _para("1"), _para("2")),
    )
    section = IRNode(kind=IRNodeKind.SECTION, label="1", text="", attrs={}, children=(subsection,))
    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", attrs={}, children=(section,))
    assert check_invariants(body) != [], "pre-condition: should have paragraph sort violations"

    result = resort_children(body)
    result_sec = next(c for c in result.children if c.kind is IRNodeKind.SECTION)
    result_sub = next(c for c in result_sec.children if c.kind is IRNodeKind.SUBSECTION)
    para_labels = [c.label for c in result_sub.children if c.kind is IRNodeKind.PARAGRAPH]
    assert para_labels == ["1", "2", "3"], f"Expected ['1', '2', '3'], got {para_labels}"
    assert check_invariants(result) == [], "post-condition: no invariant violations"


def test_resort_children_preserves_mixed_numbered_lettered_paragraph_order() -> None:
    """Mixed digit/letter paragraph lists are source-ordered, not structural-sortable."""
    from lawvm.core.ir import IRNode
    from lawvm.core.tree_ops import resort_children, check_invariants

    def _para(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text="", attrs={}, children=())

    subsection = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        text="",
        attrs={},
        children=(_para("1"), _para("2"), _para("a"), _para("b"), _para("3")),
    )
    section = IRNode(kind=IRNodeKind.SECTION, label="1", text="", attrs={}, children=(subsection,))
    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", attrs={}, children=(section,))

    result = resort_children(body)
    result_sec = next(c for c in result.children if c.kind is IRNodeKind.SECTION)
    result_sub = next(c for c in result_sec.children if c.kind is IRNodeKind.SUBSECTION)
    para_labels = [c.label for c in result_sub.children if c.kind is IRNodeKind.PARAGRAPH]

    assert result is body
    assert para_labels == ["1", "2", "a", "b", "3"]
    assert check_invariants(result) == []


def test_replay_xml_2014_834_voimaantulo_only_amendment_keeps_section_7a() -> None:
    """Regression: 2014/834 §7a was MISSING from official_consolidation replay.

    §7a was inserted by 2019/154 with expires='2021-04-30'.  Subsequent amendments
    2021/179 and 2023/197 each extended 8a§ explicitly, and 2025/41 amended only the
    voimaantulosäännös (entry-into-force provision) to extend the whole regulation to
    2029-04-30.

    Bug: the _commencement_expiry_override was only called for SKIPPED amendments, not
    for accepted ones.  Additionally, the fallback in _rewrite_lo_op_source_expiry
    didn't handle the case where the target statute was the parent statute itself (all
    lo_ops carry amendment IDs, not the parent statute ID, as source).

    Fix: call _commencement_expiry_override for accepted amendments after
    apply_ops_to_tree, and clear the expires field in official_consolidation mode so
    materialization at 9999-12-31 includes the section.
    """
    master = pinned_replay("2014/834", mode="official_consolidation")
    sections = master.find_section("7a")
    assert sections is not None, "Section 7a must be present in official_consolidation replay"


def test_rewrite_lo_op_source_effective_uses_insert_for_scoped_replay_owned_snapshot() -> None:
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="base"),),
                    ),
                ),
            ),
        ),
    )
    prior_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="4a",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old text"),),
            ),
        ),
    )
    replacement_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="4a",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="new text"),),
            ),
        ),
    )
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_4a",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "4a"),)),
            payload=prior_section,
            source=OperationSource(statute_id="1986/241", enacted="1986-08-08", effective="1986-09-01"),
            group_id="finland-johto:1986/241",
        ),
        LegalOperation(
            op_id="snapshot_subsection_1_from_section_4a",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "4a"), ("subsection", "1"))),
            payload=prior_section.children[0],
            source=OperationSource(statute_id="1986/241", enacted="1986-08-08", effective="1986-09-01"),
            group_id="finland-johto:1986/241",
        ),
        LegalOperation(
            op_id="snapshot_section_4a",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4a"),)),
            payload=replacement_section,
            source=OperationSource(statute_id="1995/454", enacted="1995-03-24", effective="1995-04-01"),
            group_id="finland-johto:1995/454",
        ),
        LegalOperation(
            op_id="snapshot_subsection_1_from_section_4a",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4a"), ("subsection", "1"))),
            payload=replacement_section.children[0],
            source=OperationSource(statute_id="1995/454", enacted="1995-03-24", effective="1995-04-01"),
            group_id="finland-johto:1995/454",
        ),
    ]

    changed = _rewrite_lo_op_source_effective(
        lo_ops,
        "1995/454",
        dt.date(1995, 5, 1),
        chapter_section_map={None: {"4a"}},
        base_ir=base_ir,
    )

    assert changed is True
    section_snapshot = lo_ops[2]
    subsection_snapshot = lo_ops[3]
    assert section_snapshot.source is not None
    assert subsection_snapshot.source is not None
    assert section_snapshot.source.effective == "1995-05-01"
    assert subsection_snapshot.source.effective == "1995-05-01"
    assert section_snapshot.action is StructuralAction.INSERT
    assert subsection_snapshot.action is StructuralAction.INSERT


def test_rewrite_lo_op_source_effective_keeps_replace_for_base_owned_snapshot() -> None:
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="4a",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="base text"),),
                    ),
                ),
            ),
        ),
    )
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_4a",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4a"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="4a"),
            source=OperationSource(statute_id="1995/454", enacted="1995-03-24", effective="1995-04-01"),
            group_id="finland-johto:1995/454",
        ),
    ]

    changed = _rewrite_lo_op_source_effective(
        lo_ops,
        "1995/454",
        dt.date(1995, 5, 1),
        chapter_section_map={None: {"4a"}},
        base_ir=base_ir,
    )

    assert changed is True
    assert lo_ops[0].source is not None
    assert lo_ops[0].source.effective == "1995-05-01"
    assert lo_ops[0].action is StructuralAction.REPLACE


def test_replay_xml_1959_324_section_4a_uses_1995_454_commencement_text() -> None:
    """Scoped section commencement must update replay-introduced section snapshots.

    `4 a §` was first introduced by `1986/241`, so it is absent from the base
    statute. `1995/454` then rewrites the section, but its voimaantulo clause
    delays `4 ja 4 a §` to `1995-05-01`. The replay fold emits the correct
    scoped snapshot; the regression was that timeline products kept the older
    replay-introduced version instead of the commenced replacement.
    """
    master = pinned_replay("1959/324", mode="official_consolidation")
    sec = master.find_section("4a")
    assert sec is not None
    text = irnode_to_text(sec)

    assert "korkolain 4 §:n 3 momentissa tarkoitetun korkokannan mukainen" in text
    assert "16 prosenttia" not in text


def test_replay_xml_2016_549_section_32_keeps_subsection_1_under_2022_283_root() -> None:
    """Materialized PIT must reattach surviving subsection 1 under the 2022/283 section root."""
    master = pinned_replay("2016/549", mode="official_consolidation", quiet=True)
    sec = master.find_section("32", chapter_num="5")
    assert sec is not None
    subsection_labels = [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION]

    assert subsection_labels == ["1", "2", "3", "4", "5"]
    text = irnode_to_text(sec)
    assert "Tupakkatuotteiden vähittäismyyntipakkauksessa on oltava" in text
    assert "Sen lisäksi, mitä 1 momentissa säädetään" in text
    assert "Jollei muualla laissa toisin säädetä" in text
    assert "Sosiaali- ja terveysministeriön asetuksella voidaan antaa tarkempia säännöksiä" in text


# ---------------------------------------------------------------------------
# Chapter-in-part materialization: new chapters inside part-structured statutes
# ---------------------------------------------------------------------------


def test_pre_create_amendment_chapters_returns_created_refs() -> None:
    """_pre_create_amendment_chapters must return exact created chapter refs.

    When a new chapter is created, the returned list must carry enough scope for
    the caller to emit chapter-level LegalOperations for timeline materialization.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="8",
                    children=(IRNode(kind=IRNodeKind.NUM, text="8 luku"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>8 a luku</num>
              <heading>Uusi luku</heading>
              <section><num>1 §</num></section>
            </chapter>
          </body>
        </akn>
        """
    )
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    assert muutos_body_el is not None

    result = _pre_create_amendment_chapters(state, muutos_body_el, "2015/303")
    new_state = result.state
    created = result.created_refs

    assert any(ref.part_label == "" and ref.chapter_label == "8a" for ref in created), (
        f"Expected root chapter ref ('', '8a'); got {created}"
    )
    ch8a = new_state.find_chapter("8a")
    assert ch8a is not None, "Chapter 8a must be present in state after pre-creation"


def test_pre_create_amendment_chapters_keeps_part_scope_for_same_label_chapters() -> None:
    """Per-part chapter-restart statutes keep distinct same-label chapters.

    When the same chapter label already exists under more than one part (a
    per-part chapter-restart statute, e.g. 2017/320 where ``2 luku`` recurs in
    several osat), an amendment that scopes a new ``2 luku`` to a further part
    must still seed a distinct chapter there: the tree-wide label is ambiguous,
    so it cannot be treated as a single relocated unit.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="IV OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="VI OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.NUM, text="V OSA"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>V OSA</num>
              <chapter>
                <num>2 luku</num>
                <heading>Uusi luku</heading>
                <section><num>1 §</num></section>
              </chapter>
            </part>
          </body>
        </akn>
        """
    )
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    assert muutos_body_el is not None

    result = _pre_create_amendment_chapters(
        state,
        muutos_body_el,
        "2018/301",
        required_labels={("5", "2")},
    )
    new_state = result.state
    created = result.created_refs

    assert tuple((ref.part_label, ref.chapter_label) for ref in created) == (("5", "2"),)
    part_5_path = new_state.find("part", "5")
    assert part_5_path is not None
    part_5 = new_state.resolve(part_5_path)
    assert part_5 is not None
    part_5_chapters = [child.label for child in part_5.children if child.kind is IRNodeKind.CHAPTER]
    assert "2" in part_5_chapters


def test_pre_create_amendment_chapters_skips_duplicate_of_relocated_unique_chapter() -> None:
    """A globally-unique chapter scoped under a relabelled part is not duplicated.

    Continuous-numbering statutes (e.g. Kirkkolaki 1993/1054) keep each chapter
    label tree-wide unique. When an amendment re-presents such a chapter under a
    relabelled part scope — a container for sparse section amendments, not an
    explicit ``uusi N luku`` insert — pre-creation must recognise the existing
    chapter and skip rather than seed a second instance under the new part.
    """
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="V OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="22",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="22 luku"),
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="4",
                                    children=(IRNode(kind=IRNodeKind.NUM, text="4 §"),),
                                ),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.NUM, text="IV OSA"),),
                ),
            ),
        )
    )
    muutos_tree = etree.fromstring(
        """
        <akn xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <part>
              <num>IV OSA</num>
              <chapter>
                <num>22 luku</num>
                <section><num>4 §</num></section>
              </chapter>
            </part>
          </body>
        </akn>
        """
    )
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    assert muutos_body_el is not None

    from lawvm.core.tree_ops import find_all as tree_find_all

    result = _pre_create_amendment_chapters(state, muutos_body_el, "2003/1274")
    new_state = result.state

    assert result.created_refs == (), (
        "Globally-unique chapter:22 must not be duplicated under the relabelled part"
    )
    ch22_paths = tree_find_all(new_state.ir, "chapter", "22")
    assert len(ch22_paths) == 1, f"Expected exactly one chapter:22, got {ch22_paths}"
    assert ch22_paths[0][0] == ("part", "5"), (
        f"The single chapter:22 must stay under part:5; got {ch22_paths[0]}"
    )


def test_new_chapter_in_part_materializes_with_sections_via_lo_ops() -> None:
    """New chapters created by _pre_create_amendment_chapters must appear in
    the timeline-materialized PIT output even when the statute has part-scoped
    chapters (part/chapter nesting depth = 3 for sections).

    Bug: _overlay_on_container only iterated depth-1 top_keys when inserting new
    entries, so new chapters inside existing parts (depth-2) were silently dropped
    from materialize_pit output even though compile_timelines had entries for them.

    Fix: the insertion loop now iterates all active keys instead of top_keys,
    filtering by depth and parent prefix at iteration time.
    """
    from lawvm.core.ir import IRStatute, OperationSource, LegalOperation, LegalAddress
    from lawvm.core.semantic_types import StructuralAction
    from lawvm.core.timeline import compile_timelines, materialize_pit
    from lawvm.finland.replay_products import fi_label_norm

    # Base statute: part:2 containing chapter:8 with one section
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="II osa"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="8",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="8 luku"),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="1",
                                children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    base_statute = IRStatute(statute_id="2000/0", title="Test", body=base_ir)

    op_source = OperationSource(
        statute_id="2015/303",
        title="Test amendment",
        enacted="2015-04-01",
        effective="2016-01-01",
    )

    # Chapter 8a node (minimal, as pre_create would produce)
    ch8a_node = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="8a",
        children=(IRNode(kind=IRNodeKind.NUM, text="8 a luku"),),
    )
    # Section 1 in chapter 8a
    sec1_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
    )

    # LegalOperations: chapter INSERT at (part:2, chapter:8a)
    # and section INSERT at (part:2, chapter:8a, section:1)
    ch8a_op = LegalOperation(
        op_id="test_ch8a_insert",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("part", "2"), ("chapter", "8a"))),
        payload=ch8a_node,
        group_id="g:test_ch8a",
        source=op_source,
    )
    sec1_op = LegalOperation(
        op_id="test_ch8a_sec1_insert",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("part", "2"), ("chapter", "8a"), ("section", "1"))),
        payload=sec1_node,
        group_id="g:test_ch8a",
        source=op_source,
    )

    timelines = compile_timelines(
        base_statute,
        [ch8a_op, sec1_op],
        label_norm=fi_label_norm,
        temporal_events=(
            TemporalEvent(
                event_id="ev:test_ch8a",
                group_id="g:test_ch8a",
                kind="commence",
                effective="2016-01-01",
                source=op_source,
                scope=TemporalScope(target_statute=base_statute.statute_id),
            ),
        ),
    )

    # Timeline must have chapter 8a entry
    ch8a_addr = LegalAddress(path=(("part", "2"), ("chapter", "8a")))
    assert ch8a_addr in timelines, "Timeline must have chapter 8a entry"

    pit = materialize_pit(timelines, as_of="9999-12-31", base=base_statute, label_norm=fi_label_norm)

    # Chapter 8a must appear in the materialized body
    def find_ch(ir: IRNode, label: str) -> IRNode | None:
        for c in ir.children:
            if c.kind is IRNodeKind.CHAPTER and c.label == label:
                return c
            for gc in c.children:
                if gc.kind is IRNodeKind.CHAPTER and gc.label == label:
                    return gc
        return None

    ch8a_pit = find_ch(pit.body, "8a")
    assert ch8a_pit is not None, (
        "Chapter 8a must appear in materialize_pit output; "
        f"body children: {[(c.kind, c.label) for c in pit.body.children]}"
    )


# ---------------------------------------------------------------------------
# COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED observation guardrail
# ---------------------------------------------------------------------------


def _make_many_section_muutos_xml(n_sections: int, chapter_label: str = "3") -> bytes:
    """Build a minimal amendment XML with a chapter and n_sections sections.

    Used to trigger the HIGH_UNCOVERED_BODY coverage guardrail: with >10 sections
    and a chapter INSERT op that doesn't explicitly cover any of them, the
    uncovered ratio will be high enough to emit COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED.
    """
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    section_elems = "".join(
        f'<section xmlns="{ns}"><num>{i} §</num>'
        f'<subsection><content><p>text {i}</p></content></subsection></section>'
        for i in range(1, n_sections + 1)
    )
    return (
        f'<akn xmlns="{ns}">'
        f'<preamble><formula><blockContainer><block name="insertions">'
        f'lisätään {n_sections} uutta pykälää</block></blockContainer></formula></preamble>'
        f'<body><chapter><num>{chapter_label} luku</num>{section_elems}</chapter></body>'
        f'</akn>'
    ).encode()


def test_recover_uncovered_body_ops_emits_high_uncovered_observation() -> None:
    """COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED observation is emitted when a
    chapter-level INSERT plan has a high uncovered body ratio (>10 units, >50%).

    This tests the guardrail added in Pro Q4: instead of silently proceeding,
    the pipeline now emits an explicit typed observation so that callers can
    surface the degraded confidence.
    """
    # Build an IR state with no existing sections so nothing is "covered"
    state = ReplayState(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(),
        )
    )
    ctx = _statute_context(state.ir)

    # 12 sections with no PEG ops covering them → uncov_ratio = 1.0 >> 0.5
    # The chapter INSERT op triggers CHAPTER_INSERT signal
    n_sections = 12
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(n_sections))

    # One chapter INSERT op (covers the chapter structurally, but no per-section ops)
    ops = [AmendmentOp(op_id="", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="3")]

    observations_out: list[dict[str, Any]] = []
    restructure_plans_out: list[StructuralTransformPlan] = []
    findings_out: list[Finding] = []

    _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2002/1244",
        failed_ops_out=[],
        restructure_plans_out=restructure_plans_out,
        observations_out=observations_out,
        findings_out=findings_out,
    )

    # A StructuralTransformPlan should have been built
    assert len(restructure_plans_out) == 1, "Expected a StructuralTransformPlan to be built"

    # The degradation observation must be present
    degraded_obs = [
        o for o in observations_out
        if o.get("kind") == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
    ]
    assert len(degraded_obs) == 1, (
        f"Expected exactly one COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED observation; "
        f"got {observations_out}"
    )
    obs = degraded_obs[0]
    assert obs["amendment_id"] == "2002/1244"
    assert obs["total_units"] > 10
    assert obs["uncov_ratio"] > 0.5
    assert "confidence" in obs
    assert "signals" in obs

    degraded_findings = [
        f for f in findings_out
        if f.kind == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
    ]
    assert len(degraded_findings) == 1
    assert degraded_findings[0].blocking is True
    assert degraded_findings[0].source_statute == "2002/1244"


def test_recover_uncovered_body_ops_quiet_replay_suppresses_high_uncovered_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(12))
    ops = [
        AmendmentOp(
            op_id="",
            op_type=OpType.INSERT,
            target_kind=TargetKind.CHAPTER,
            target_section="3",
        )
    ]
    observations_out: list[dict[str, Any]] = []
    findings_out: list[Finding] = []

    token = set_replay_verbose(False)
    try:
        with caplog.at_level(logging.WARNING, logger="lawvm.finland.uncovered_body_recovery"):
            _recover_uncovered_body_ops(
                state,
                ctx,
                ops,
                muutos_tree,
                "2002/1244",
                failed_ops_out=[],
                observations_out=observations_out,
                findings_out=findings_out,
            )
    finally:
        reset_replay_verbose(token)

    assert any(
        row.get("kind") == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
        for row in observations_out
    )
    assert any(
        finding.kind == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
        for finding in findings_out
    )
    assert not any(
        "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" in record.message
        for record in caplog.records
    )


def test_recover_uncovered_body_ops_deduplicates_identical_restructure_plan_output() -> None:
    """Repeated recovery for the same amendment must not append the same plan twice."""
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(12))
    ops = [AmendmentOp(op_id="", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="3")]

    restructure_plans_out: list[StructuralTransformPlan] = []

    _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2002/1244",
        failed_ops_out=[],
        restructure_plans_out=restructure_plans_out,
        observations_out=[],
    )
    _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2002/1244",
        failed_ops_out=[],
        restructure_plans_out=restructure_plans_out,
        observations_out=[],
    )

    assert len(restructure_plans_out) == 1


def test_resolved_op_restructure_plan_helper_uses_typed_target_fields() -> None:
    """The restructure-plan ownership check must read late-waist typed fields."""
    source = LegalAddress(path=(("chapter", "5"), ("section", "33")))
    destination = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    op = AmendmentOp(
        op_id="relabel-1",
        op_type=OpType.RENUMBER,
        target_section="33",
        target_unit_kind="section",
        target_chapter="9",
        target_part="11",
        target_paragraph=9,
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="33",
        target_unit_kind="section",
        op_id="relabel-1",
        _op_type_seed=OpType.RENUMBER,
        _target_address_override=source,
        _destination_address_override=destination,
        intent=Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(source),
            destination=NodeTarget(destination),
            contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
        ),
    )

    assert rop.op.target_cols.target_paragraph == 9
    assert rop.op.target_cols.target_chapter == "9"
    assert rop.op.target_cols.target_part == "11"
    assert rop.resolved_target_scope_chapter_label == "5"
    assert rop.resolved_target_scope_part_label is None
    assert rop.effective_target_paragraph is None
    assert _resolved_op_is_owned_by_restructure_plan(rop, set()) is False


def test_resolved_op_restructure_plan_helper_accepts_exact_owned_signature() -> None:
    source = LegalAddress(path=(("chapter", "5"), ("section", "33")))
    destination = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    op = AmendmentOp(
        op_id="relabel-1",
        op_type=OpType.RENUMBER,
        target_section="33",
        target_unit_kind="section",
        target_chapter="9",
        target_part="11",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="33",
        target_unit_kind="section",
        op_id="relabel-1",
        _op_type_seed=OpType.RENUMBER,
        _target_address_override=source,
        _destination_address_override=destination,
        intent=Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(source),
            destination=NodeTarget(destination),
            contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
        ),
    )

    owned_relabels = {(source.path, destination.path)}
    assert _resolved_op_is_owned_by_restructure_plan(rop, owned_relabels) is True


def test_resolved_op_restructure_plan_helper_rejects_same_leaf_labels_in_different_scope() -> None:
    source = LegalAddress(path=(("chapter", "5"), ("section", "33")))
    destination = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    op = AmendmentOp(
        op_id="relabel-1",
        op_type=OpType.RENUMBER,
        target_section="33",
        target_unit_kind="section",
        target_chapter="9",
        target_part="11",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="33",
        target_unit_kind="section",
        op_id="relabel-1",
        _op_type_seed=OpType.RENUMBER,
        _target_address_override=source,
        _destination_address_override=destination,
        intent=Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(source),
            destination=NodeTarget(destination),
            contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
        ),
    )

    owned_relabels: set[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = {
        (
            (("chapter", "7"), ("section", "33")),
            (("chapter", "7"), ("section", "34")),
        )
    }
    assert _resolved_op_is_owned_by_restructure_plan(rop, owned_relabels) is False


def test_resolved_op_canonical_intent_uses_typed_move_clause_destination_fields() -> None:
    """Canonical intent move destinations must follow late-waist chapter/part fields.

    The Move lane is payload-less by contract: ``_apply_intent_move`` rehomes
    the existing node unchanged, so only payload-free ops (pure moves) may
    lower to a Move intent. A payload-bearing move rider stays a
    destination-scoped Replace (see the companion test below).
    """
    op = AmendmentOp(
        op_id="move-typed-1",
        op_type=OpType.RENUMBER,
        target_section="33",
        target_unit_kind="section",
        target_chapter="9",
        move_clause_target_unit_kind="chapter",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="33",
        target_chapter="6",
    )

    assert rop.op.target_cols.target_chapter == "9"
    rop.move_clause_target_chapter = "5"
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert isinstance(intent, Move)
    assert intent.destination_parent.path == (("chapter", "5"),)


def test_resolved_op_canonical_intent_payload_bearing_move_rider_stays_replace() -> None:
    """A payload-bearing move rider must NOT lower to a payload-less Move.

    "muutetaan 33 §, joka samalla siirretään 5 lukuun" both rehomes the
    section AND replaces its text. ``_apply_intent_move`` cannot land a
    payload, so lowering this to Move would silently drop the replacement
    text. It stays a Replace; the apply layer's section move+replace
    recovery performs the rehoming.
    """
    op = AmendmentOp(
        op_id="move-typed-2",
        op_type=OpType.REPLACE,
        target_section="33",
        target_unit_kind="section",
        target_chapter="9",
        move_clause_target_unit_kind="chapter",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="33", children=()),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="33",
        target_chapter="6",
    )

    rop.move_clause_target_chapter = "5"
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert not isinstance(intent, Move)


def test_recover_uncovered_body_ops_no_observation_when_observations_out_is_none() -> None:
    """When observations_out is None the guardrail is silently skipped (backward compat)."""
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(12))
    ops = [AmendmentOp(op_id="", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="3")]

    # Should not raise even when observations_out is None
    _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2002/1244",
        failed_ops_out=[],
        observations_out=None,
    )


def test_recover_uncovered_body_ops_no_observation_when_ratio_low() -> None:
    """No COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED observation when uncov ratio is low.

    With only 3 sections (below the 10-unit threshold) the HIGH_UNCOVERED_BODY
    signal is not triggered, so no degradation observation should be emitted.
    """
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    # Only 3 sections — below _CHAPTER_INSERT_TOTAL_UNITS_THRESHOLD (10)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(3))
    ops = [AmendmentOp(op_id="", op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER, target_section="3")]

    observations_out: list[dict[str, Any]] = []
    _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2002/0001",
        failed_ops_out=[],
        observations_out=observations_out,
    )

    degraded_obs = [
        o for o in observations_out
        if o.get("kind") == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
    ]
    assert degraded_obs == [], (
        f"Expected no degradation observation for low section count; got {observations_out}"
    )


def test_merge_section_with_omission_ir_accepts_new_subsection_addition() -> None:
    """Omission + new subsection must produce merged_count > master_count (addition case).

    Regression test for the guard that previously used == (rejecting additions).
    Pattern: 1990/650 §13, §46, §47, §49 — 2003/127 inserts a new momentti via
    omission-section amendment.  The merged section has more subsections than the
    master; the guard must allow this (>= not ==).
    """
    # Master section: one existing subsection
    master_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="13 §"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Alkuperäinen momentti 1."),
                ),
            ),
        ),
    )
    # Amendment section: omission covering existing subsection 1, plus new subsection 2
    amend_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="13",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="13 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="Uusi momentti 2."),
                ),
            ),
        ),
    )

    merged = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert merged is not None, "merge should succeed when amendment adds a subsection"
    merged_subsecs = [c for c in merged.children if c.kind is IRNodeKind.SUBSECTION]
    assert len(merged_subsecs) == 2, (
        f"merged section must have 2 subsections (1 carried + 1 new), got {len(merged_subsecs)}"
    )
    labels = [s.label for s in merged_subsecs]
    assert "1" in labels, "original subsection 1 must be preserved"
    assert "2" in labels, "new subsection 2 must be present"


def test_merge_section_with_omission_ir_preserves_trailing_subsection_for_sparse_middle_replace() -> None:
    """A sparse middle-slot replace must keep trailing live subsections.

    Pattern from 2016/1227 <- 2022/1149 §12:
    the amendment body is `omission + one subsection + omission` while the
    johtolause compiles to `REPLACE 12 § 4 mom`. The targeted omission merge
    must preserve live subsection 5 instead of truncating the section at 4.
    """
    master_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 1"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 2"),)),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", children=(IRNode(kind=IRNodeKind.CONTENT, text="mom 3"),)),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="4",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="old mom 4"),),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="preserved mom 5"),),
            ),
        ),
    )
    amend_sec = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="new mom 4"),),
            ),
            IRNode(kind=IRNodeKind.OMISSION),
        ),
    )

    merged = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[
            AmendmentOp(
                op_type=OpType.REPLACE,
                target_kind=TargetKind.SECTION,
                target_section="12",
                target_paragraph=4,
            )
        ],
    )

    assert merged is not None
    merged_subsecs = [c for c in merged.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in merged_subsecs] == ["1", "2", "3", "4", "5"]
    assert irnode_to_text(merged_subsecs[3]) == "new mom 4"
    assert irnode_to_text(merged_subsecs[4]) == "preserved mom 5"


def test_replay_xml_1990_1341_removes_repealed_8a_subsection_2_from_2010_512() -> None:
    """Explicit child repeal must survive same-group omission merge in 1990/1341 §8 a.

    Amendment 2010/512 repeals 8 a § 2 mom while also replacing the section
    sparsely via `1 mom + omission + 5 mom`. Replay keeps the repealed slot as
    an explicit tombstone, preserves live 3–4 moments and the later 2016/777 6th
    moment, and must not resurrect the old 2nd-moment text.
    """
    master = pinned_replay("1990/1341", quiet=True)
    sec = master.find_section("8a")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    labels = [child.label for child in subsections]
    assert labels == ["1", "2", "3", "4", "5", "6"]
    subsection_2 = next(child for child in subsections if child.label == "2")
    assert subsection_2.attrs.get("lawvm_repeal_placeholder") == "1"
    assert irnode_to_text(subsection_2) == ""
    assert all(
        "lääninverovirasto, jonka alueella koronmaksajan kotikunta on" not in irnode_to_text(child)
        for child in subsections
    )


def test_replay_xml_2016_1227_keeps_section_12_subsection_5_after_2022_1149(
    replay_2016_1227_finlex_oracle: Any,
) -> None:
    """Sparse middle-slot replace must preserve the carried tail in 2016/1227 §12."""
    sec = replay_2016_1227_finlex_oracle.find_section("12", chapter_num="2")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4", "5"]
    assert (
        "Yksityisten terveydenhuollon palvelujen antajien valvontaan liittyvistä tarkastuksista"
        in irnode_to_text(subsections[4])
    )


def test_replay_xml_2016_1227_reuses_repealed_section_51_subsection_3_slot(
    replay_2016_1227_finlex_oracle: Any,
) -> None:
    """2022/1149 must fill the repealed 3rd-moment slot without shifting old 4 -> 5."""
    sec = replay_2016_1227_finlex_oracle.find_section("51", chapter_num="5")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4"]
    assert "Edellä 2 momentissa tarkoitetut hoitopaikan potilasasiakirjoissa" in irnode_to_text(subsections[2])
    assert subsections[3].attrs.get("lawvm_repeal_placeholder") == "1"


def test_replay_xml_2009_617_preserves_section_tail_under_2016_533_sparse_section_shells() -> None:
    master = pinned_replay("2009/617", mode="official_consolidation", quiet=True)

    sec15 = master.find_section("15")
    assert sec15 is not None
    subsections15 = [child for child in sec15.children if child.kind is IRNodeKind.SUBSECTION]
    assert len(subsections15) == 3
    assert "1)" in irnode_to_text(subsections15[0])
    assert "Edellä 1 momentissa tarkoitetut tiedot" in irnode_to_text(subsections15[1])

    sec20 = master.find_section("20")
    assert sec20 is not None
    subsections20 = [child for child in sec20.children if child.kind is IRNodeKind.SUBSECTION]
    assert len(subsections20) == 3
    assert "Tunnistusvälineen liikkeelle laskeminen perustuu" in irnode_to_text(subsections20[0])
    assert "Sopimus voi olla voimassa toistaiseksi tai määräaikaisesti" in irnode_to_text(subsections20[1])
    assert "Tunnistusväline myönnetään aina luonnolliselle henkilölle" in irnode_to_text(subsections20[2])


def test_replay_xml_1947_328_keeps_section_1_tail_as_repeal_placeholders_not_old_substantive_text() -> None:
    master = pinned_replay("1947/328", mode="official_consolidation", quiet=True)
    sec = master.find_section("1")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4", "5", "6"]
    assert subsections[3].attrs.get("lawvm_repeal_placeholder") == "1"
    assert subsections[4].attrs.get("lawvm_repeal_placeholder") == "1"
    assert subsections[5].attrs.get("lawvm_repeal_placeholder") == "1"
    assert irnode_to_text(subsections[4]) == ""
    assert irnode_to_text(subsections[5]) == ""


def test_replay_xml_2015_351_applies_insert_before_shifted_replace_for_section_26() -> None:
    replay = pinned_replay("2015/351", mode="official_consolidation", quiet=True)
    sec = replay.find_section("26", "4")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    assert (
        "12 artiklassa tarkoitetusta määrärahasta Maahanmuuttovirastolle tukea"
        in irnode_to_text(subsections[1])
    )
    assert (
        "2 momentissa tarkoitetun avustuksen määrät vuosittain erikseen."
        in irnode_to_text(subsections[2])
    )


def test_replay_xml_2011_715_applies_corrigendum_label_fix_for_2024_33() -> None:
    replay = pinned_replay("2011/715", mode="official_consolidation", quiet=True)

    sec_5a = replay.find_section("5a")
    sec_5b = replay.find_section("5b")

    assert sec_5a is not None
    assert sec_5b is None
    assert "Oikeudenkäyntiavustajalautakunnan henkilöstö" in irnode_to_text(sec_5a)


def test_uncovered_skips_tällä_lailla_kumotaan_repeal_clause_section() -> None:
    """Uncovered recovery must NOT process a repealing statute's own repeal provision.

    Regression test for the 2015/640 bug: the amending act 2015/640 had section 1
    starting with 'Tällä lailla kumotaan tullilain (1466/1994) 21 §:n...' — its own
    repeal clause.  Without the fix, recovery would try to replace section 1 of the
    base act (1994/1466) with this repeal-clause text. The typed coverage sweep
    tags the self-repeal section nonoperative, so it never becomes a candidate.
    """
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

    # Base act has a section 1 with real content
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 §"),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="1",
                           children=(IRNode(kind=IRNodeKind.CONTENT, text="Tätä lakia sovelletaan."),)),
                ),
            ),
        ),
    )
    state = ReplayState(ir=base_ir)
    ctx = _statute_context(state.ir)

    # Repealing amendment: section 1 is "Tällä lailla kumotaan..." (no heading)
    muutos_xml = (
        f'<akn xmlns="{ns}">'
        f'<preamble><formula><blockContainer><block name="insertions">'
        f'kumotaan 21-23 §'
        f'</block></blockContainer></formula></preamble>'
        f'<body>'
        f'<section><num>1 §</num>'
        f'<subsection><content>'
        f'<p>Tällä lailla kumotaan tullilain (1466/1994) 21 §:n edellä oleva väliotsikko.</p>'
        f'</content></subsection></section>'
        f'<section><num>2 §</num>'
        f'<subsection><content><p>Tämä laki tulee voimaan 1 päivänä kesäkuuta 2015.</p></content></subsection>'
        f'</section>'
        f'</body>'
        f'</akn>'
    ).encode()
    muutos_tree = etree.fromstring(muutos_xml)

    # No PEG ops (pure repeal statute) — coverage will see 0 claimed
    ops: list[AmendmentOp] = []

    recovered = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2015/640",
        failed_ops_out=[],
    )

    # The repeal clause section 1 must NOT be recovered as a replace
    section_labels = [r.target_norm for r in recovered if r.is_replace_action]
    assert "1" not in section_labels, (
        f"'Tällä lailla kumotaan' section should be filtered; got replace targets: {section_labels}"
    )


def test_uncovered_heading_replace_does_not_authorize_repeal_clause_body_payload() -> None:
    """Heading-only replacement authority must not admit repeal-list body text.

    Regression for 1996/1195 / 2001/893: the omnibus repeal item for the parent
    statute also repeals a preceding heading. That heading-facet replacement is
    not authority to replace section 1 or 2 with the amendment act's own repeal
    and commencement provisions.
    """
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    base_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 §"),
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Base section one."),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="48",
                children=(IRNode(kind=IRNodeKind.NUM, text="48 §"),),
            ),
        ),
    )
    state = ReplayState(ir=base_ir)
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(
        (
            f'<akn xmlns="{ns}">'
            f'<body>'
            f'<section><num>1 §</num>'
            f'<subsection><content>'
            f'<p>Tällä lailla kumotaan lain (1195/1996) 48 § sekä sen edellä oleva väliotsikko.</p>'
            f'</content></subsection></section>'
            f'<section><num>2 §</num>'
            f'<subsection><content><p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2002.</p></content></subsection>'
            f'</section>'
            f'</body>'
            f'</akn>'
        ).encode()
    )
    ops = [
        AmendmentOp(op_id="", op_type=OpType.REPEAL, target_section="48", target_unit_kind="section"),
        AmendmentOp(
            op_id="",
            op_type=OpType.REPLACE,
            target_section="48",
            target_unit_kind="section",
            target_special="otsikko",
        ),
    ]

    recovered = _recover_uncovered_body_ops(
        state,
        ctx,
        ops,
        muutos_tree,
        "2001/893",
        failed_ops_out=[],
    )

    assert not [r for r in recovered if r.is_replace_action and r.target_norm in {"1", "2"}]


# ---------------------------------------------------------------------------
# Regression tests: multi-väliaikaisesti scope detection (2021/147 pattern)
# ---------------------------------------------------------------------------


def test_extract_temporary_targets_all_vaaliaikaisesti_occurrences() -> None:
    """_extract_temporary_targets_from_johtolause must find ALL väliaikaisesti
    occurrences, not just the first.

    Pattern from 2021/147 (Laki tartuntatautilain muuttamisesta ja väliaikaisesta
    muuttamisesta): the muutetaan clause has 'väliaikaisesti 91 §:n 1 momentti'
    and the lisätään clause has 'väliaikaisesti uusi 58 c–58 h ja 59 a–59 e §'.

    Only scanning the first 'väliaikaisesti' returned frozenset({'91'}), causing
    sections 58c–59e to be created as PERMANENT versions that revived after
    2021/1221 expired — 11 EXTRA sections in 2016/1227.
    """
    from lawvm.finland.frontend_compile import _extract_temporary_targets_from_johtolause

    johto = (
        "muutetaan tartuntatautilain (1227/2016) 3 §:n 5 kohta, 24 §:n 2–4 momentti, "
        "57 §:n otsikko sekä 1 ja 2 momentti, 63 §:n 1 momentti, 68 §:n 2 ja 3 momentti, "
        "69 §:n 1 momentti ja 89 §, väliaikaisesti 91 §:n 1 momentti sekä 92 §, "
        "sellaisena kuin niistä on 91 §:n 1 momentti laissa 727/2020, sekä "
        "lisätään lakiin väliaikaisesti uusi 58 c–58 h ja 59 a–59 e § seuraavasti:"
    )
    result = _extract_temporary_targets_from_johtolause(johto)

    # Both occurrences should be captured
    assert result is not None, "Expected section-scoped frozenset, got None (whole-amendment)"
    assert "91" in result, "§91 (from first väliaikaisesti) should be in scope"
    # 58c through 58h
    for sec in ["58c", "58d", "58e", "58f", "58g", "58h"]:
        assert sec in result, f"§{sec} (from lisätään väliaikaisesti) should be in scope"
    # 59a through 59e
    for sec in ["59a", "59b", "59c", "59d", "59e"]:
        assert sec in result, f"§{sec} (from lisätään väliaikaisesti) should be in scope"


def test_extract_temporary_targets_single_occurrence_still_works() -> None:
    """Single-väliaikaisesti johtolause must still return the single section scope."""
    from lawvm.finland.frontend_compile import _extract_temporary_targets_from_johtolause

    johto = "muutetaan lain 5 § ja väliaikaisesti uusi 21 b § seuraavasti:"
    result = _extract_temporary_targets_from_johtolause(johto)

    assert result is not None
    assert "21b" in result
    assert "5" not in result  # §5 is permanent


def test_oracle_version_future_repeal_only_uses_cutoff_date_for_repeal_only_family() -> None:
    compiled_ops: list[dict[str, object]] = [
        {
            "action": "repeal",
            "source_statute": "2026/45",
            "activation_rule": {
                "kind": "fixed_date",
                "effective_date": "2026-06-19",
                "condition_ref": "",
            },
        }
    ]

    assert _oracle_version_future_repeal_only_uses_cutoff_date(
        compiled_ops=compiled_ops,
        oracle_version_amendment_id="2026/45",
        oracle_cutoff_iso="2026-01-16",
    )


def test_oracle_version_future_repeal_only_uses_cutoff_date_keeps_future_replace_anchor() -> None:
    compiled_ops: list[dict[str, object]] = [
        {
            "action": "replace",
            "source_statute": "2021/1199",
            "activation_rule": {
                "kind": "fixed_date",
                "effective_date": "2021-12-31",
                "condition_ref": "",
            },
        }
    ]

    assert not _oracle_version_future_repeal_only_uses_cutoff_date(
        compiled_ops=compiled_ops,
        oracle_version_amendment_id="2021/1199",
        oracle_cutoff_iso="2021-12-17",
    )


def test_official_consolidation_horizon_uses_oracle_version_non_repeal_op_effective_date() -> None:
    legal_operations = [
        LegalOperation(
            op_id="snapshot_section_11",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "11"),)),
            source=OperationSource(
                statute_id="2024/538",
                effective="2025-01-01",
            ),
        )
    ]

    decision = choose_replay_horizon(
        ReplayHorizonRequest(
            mode="official_consolidation",
            as_of="",
            cutoff_date=dt.date(2024, 9, 26),
            amendment_records=[
                {
                    "statute_id": "2024/538",
                    "included": True,
                    "effective_date": dt.date(2024, 10, 1),
                    "issue_date": dt.date(2024, 9, 26),
                }
            ],
            oracle_version_amendment_id="2024/538",
            compiled_ops=[
                {
                    "source_statute": "2024/538",
                    "action": "replace",
                }
            ],
            legal_operations=legal_operations,
            oracle_reflected_section_original_versions=("2024/538",),
            replay_print=lambda _message: None,
        )
    )

    assert decision.materialize_as_of == "2025-01-01"
    assert decision.expires_as_of == "2025-01-01"
    assert decision.oracle_materialize_as_of == "2025-01-01"


def test_official_consolidation_horizon_does_not_use_unreflected_non_repeal_op_effective_date() -> None:
    legal_operations = [
        LegalOperation(
            op_id="snapshot_section_14",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "14"),)),
            source=OperationSource(
                statute_id="2026/410",
                effective="2026-10-01",
            ),
        )
    ]

    decision = choose_replay_horizon(
        ReplayHorizonRequest(
            mode="official_consolidation",
            as_of="",
            cutoff_date=dt.date(2026, 5, 28),
            amendment_records=[
                {
                    "statute_id": "2026/410",
                    "included": True,
                    "effective_date": dt.date(2026, 5, 29),
                    "issue_date": dt.date(2026, 5, 28),
                }
            ],
            oracle_version_amendment_id="2026/410",
            compiled_ops=[
                {
                    "source_statute": "2026/410",
                    "action": "replace",
                }
            ],
            legal_operations=legal_operations,
            oracle_reflected_section_original_versions=(),
            replay_print=lambda _message: None,
        )
    )

    assert decision.materialize_as_of == "2026-05-29"
    assert decision.expires_as_of == "2026-05-29"
    assert decision.oracle_materialize_as_of == "2026-05-29"


def test_official_consolidation_horizon_splits_future_repeal_expiry_cutoff() -> None:
    legal_operations = [
        LegalOperation(
            op_id="repeal_section_19",
            sequence=0,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "19"),)),
            source=OperationSource(
                statute_id="2005/886",
                effective="2006-01-01",
            ),
        )
    ]

    decision = choose_replay_horizon(
        ReplayHorizonRequest(
            mode="official_consolidation",
            as_of="",
            cutoff_date=dt.date(2005, 11, 11),
            amendment_records=[
                {
                    "statute_id": "2005/886",
                    "included": True,
                    "effective_date": dt.date(2006, 1, 1),
                    "issue_date": dt.date(2005, 11, 11),
                }
            ],
            oracle_version_amendment_id="2005/886",
            compiled_ops=[
                {
                    "source_statute": "2005/886",
                    "action": "repeal",
                }
            ],
            legal_operations=legal_operations,
            oracle_reflected_section_original_versions=(),
            replay_print=lambda _message: None,
        )
    )

    assert decision.materialize_as_of == "2006-01-01"
    assert decision.expires_as_of == "2005-11-11"
    assert decision.oracle_materialize_as_of == "2006-01-01"


def test_extract_temporary_targets_infers_host_section_for_moment_only_scope() -> None:
    """Moment-only temporary clauses must inherit the explicit host section."""
    from lawvm.finland.frontend_compile import _extract_temporary_targets_from_johtolause

    johto = (
        "muutetaan yleisestä asumistuesta annetun lain (938/2014) 25 §:n 2 momentti ja lisätään "
        "51 §:ään, sellaisena kuin se on laeissa 1143/2017 ja 1323/2018, väliaikaisesti uusi "
        "5 momentti seuraavasti:"
    )

    result = _extract_temporary_targets_from_johtolause(johto)

    assert result == frozenset({"51"})


def test_collect_johto_mentioned_section_labels_expands_alpha_suffix_ranges() -> None:
    labels = _collect_johto_mentioned_section_labels(
        "lisätään lakiin uusi 20 a, 21 a–21 c, 23 a § sekä muutetaan 49 a §"
    )

    assert {"20a", "21a", "21b", "21c", "23a", "49a"} <= labels


def test_collect_johto_mentioned_section_labels_grammar_recovers_alpha_suffix_lists() -> None:
    # NEW-better (regex->grammar demotion): the legacy section regex dropped
    # comma-listed alpha-suffix labels; the grammar driver recovers them.
    labels = _collect_johto_mentioned_section_labels(
        "muutetaan patenttiasetuksen 17 a, 17 b, 25 a, 25 b ja 25 c §"
    )

    assert {"17a", "17b", "25a", "25b", "25c"} <= labels


def test_collect_johto_mentioned_section_labels_grammar_recovers_glued_suffix_range() -> None:
    # NEW-better: spaced/glued alpha-suffix range "87 a - 87 c §" the legacy
    # regex could not expand; the grammar driver yields every endpoint.
    labels = _collect_johto_mentioned_section_labels(
        "kumotaan alkoholilain 87 a - 87 c §"
    )

    assert {"87a", "87b", "87c"} <= labels


def test_collect_johto_mentioned_section_labels_anchor_keeps_illative_target() -> None:
    # The grammar deliberately declines the illative insertion target
    # "N §:ään"; the bounded anchor supplements it so the mentioned section is
    # not silently dropped from scope.
    labels = _collect_johto_mentioned_section_labels(
        "lisätään valtiopäiväjärjestyksen 16 §:ään uusi 4 momentti"
    )

    assert "16" in labels


def test_collect_johto_mentioned_section_labels_anchor_keeps_partitive_plural_list() -> None:
    # The lexer does not classify the partitive-plural "§:ien" as a PYKALA
    # marker, so the grammar declines the whole list; the anchor keeps every
    # listed section in scope.
    labels = _collect_johto_mentioned_section_labels(
        "vahvistanut yhtiöjärjestyksen 2, 4, 14, 17, 18, 21 ja 34 §:ien muutetun sanamuodon"
    )

    assert {"2", "4", "14", "17", "18", "21", "34"} <= labels


def test_collect_johto_chapter_mentions_accepts_luvun_otsikko_form() -> None:
    mentions = _collect_johto_chapter_scope_mentions(
        "lisätään 1 §:n edelle uusi 1 luvun otsikko, "
        "lakiin uusi 17 a-17 h § ja niiden edelle uusi 3 luvun otsikko sekä "
        "18 §:n edelle uusi 4 luvun otsikko"
    )

    assert {"1", "3", "4"} <= set(mentions.new_chapter_labels)


def test_collect_johto_chapter_mentions_anaphoric_new_chapter_move_tail() -> None:
    mentions = _collect_johto_chapter_scope_mentions(
        "lisätään lakiin uusi 6 a luku, johon samalla siirretään "
        "muutettu 25, 26 ja 27 §, seuraavasti:"
    )

    assert mentions.new_chapter_labels == frozenset({"6a"})
    assert {
        (moved.section_label, moved.destination_chapter_label)
        for moved in mentions.moved_section_destinations
    } == {("25", "6a"), ("26", "6a"), ("27", "6a")}


def test_replay_xml_2001_101_preserves_section_24_sparse_item_tail_from_2017_169() -> None:
    from lawvm.core.ir_helpers import irnode_to_text

    master = pinned_replay("2001/101", mode="official_consolidation", quiet=True)
    sec = master.find_section("24", chapter_num="7")

    assert sec is not None

    text = " ".join(irnode_to_text(sec).split())
    assert "kudoksien ja solujen sekä kudosnäytteiden irrotus-, talteenotto-" in text
    assert "Lääkealan turvallisuus- ja kehittämiskeskuksen antamasta toimiluvasta" in text
    assert "vaaratilanteiden ja haittavaikutusten ilmoittamismenettelystä" in text
    assert "23 a §:ssä säädetyn tuontitodistuksen muodosta" in text
    assert "EU:n kudoslaitosten luetteloa" in text
    assert "20 h §:n 3 momentissa tarkoitetun sopimuksen tarkemmasta sisällöstä" in text


def test_replay_xml_1996_1093_drops_stale_section_18_item_6_after_2013_1085() -> None:
    from lawvm.core.ir_helpers import irnode_to_text

    master = pinned_replay("1996/1093", mode="official_consolidation", quiet=True)
    sec = master.find_section("18", chapter_num="5")

    assert sec is not None

    # 2013/1085 replaces §18 momentti 2 (dropping the stale item 6) and inserts a
    # new momentti 3; momentti 1 is untouched. The resulting section therefore has
    # exactly three subsections, matching the Finlex consolidated oracle, with the
    # rewritten momentti 2 carrying items 1–5.
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]
    assert [child.label for child in subsections[1].children if child.kind is IRNodeKind.PARAGRAPH] == ["1", "2", "3", "4", "5"]

    text = " ".join(irnode_to_text(sec).split())
    assert "laatii 7 §:ssä tarkoitetun leimikkosuunnitelman" in text
    assert "rikkoo 13 §:n suoja-alueita koskevaa säännöstä" not in text
    assert "Jollei teosta muualla laissa säädetä ankarampaa rangaistusta, metsärikkomuksesta tuomitaan myös se" in text


def test_replay_xml_2017_444_applies_explicit_2023_444_targets_for_sections_10_and_13() -> None:
    """Explicit ``2023/444`` section replaces must survive stripped alakohta residue.

    Regression family: the johtolause parser used to stop at
    ``11 kohdan johdantokappale`` after qualifier stripping left ``ja sekä``
    residue, which dropped the explicit ``3 luvun 10 §:n 1 momentti`` and
    ``13 §:n 3 ja 4 momentti`` replaces. Replay then fell back to stale text or
    uncovered-body materialization.
    """
    master = pinned_replay("2017/444", mode="official_consolidation", quiet=True)

    sec10 = master.find_section("10", chapter_num="3")
    sec13 = master.find_section("13", chapter_num="3")

    assert sec10 is not None
    assert sec13 is not None

    sec10_text = " ".join(irnode_to_text(sec10).split())
    sec13_text = " ".join(irnode_to_text(sec13).split())

    assert "Ilmoitusvelvollisen on sovellettava tehostettua menettelyä asiakkaan tuntemiseksi:" in sec10_text
    assert "11–13 ja 13 a §:ssä tarkoitetuissa tapauksissa" in sec10_text
    assert "tavanomaista suurempi rahanpesun ja terrorismin rahoittamisen riski" in sec10_text

    assert "Edellä 1 momentissa tarkoitetun menettelyn puitteissa poliittinen vaikutusvalta on selvitettävä aina" in sec13_text
    assert "Kun henkilö ei enää toimi merkittävässä julkisessa tehtävässä" in sec13_text
    assert "ilmoitusvelvollisen ylemmän johdon on hyväksyttävä asiakassuhteen aloittaminen" in sec13_text


@pytest.mark.slow
def test_replay_xml_2003_549_replaces_occupied_section_163_without_stale_tail(
    replay_2003_549_finlex_oracle: Any,
) -> None:
    """A complete same-label section INSERT must suppress stale old subsection tail.

    Regression family: `2003/549 <- 2011/682` compiles `163 §` as
    `INSERT 12 luku 163 §`. Replay already replaced the occupied section root,
    but the replacement content did not carry exact whole-section tail policy,
    so PIT materialization kept stale older `3` and `4 momentti` timelines.
    """
    sec = replay_2003_549_finlex_oracle.find_section("163", chapter_num="12")

    assert sec is not None
    assert [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2"]

    text = " ".join(irnode_to_text(sec).split())
    assert "asian uudelleen ratkaiseminen takautuvasti myönnetyn ensisijaisen etuuden" in text.lower()
    assert "3 momentti" not in text.lower()
    assert "4 momentti" not in text.lower()


@pytest.mark.slow
def test_inspect_amendment_2003_549_2010_469_prunes_carried_section_149_subsections() -> None:
    """`2010/469` section 149 must bind owned `1 momentti` edits to slot 1.

    The amendment XML carries later sibling subsections `2–5` inside the same
    section body, even though the johtolause only changes `149 § 1 momentti`
    plus item-level edits under that moment. Current inspection keeps the
    carried sibling slots visible as unassigned source context rather than
    hiding them through pre-replay pruning.
    """
    bundle = build_amendment_bundle("2003/549", "2010/469", mode="official_consolidation")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "149")

    normalized = group["normalized_payload"]
    observations = group["elaboration_observations"]

    assert normalized is not None
    assert normalized["kind"] is IRNodeKind.SECTION
    assert normalized["children"] == 7
    assert [binding["op"] for binding in group["sparse_slot_bindings"]] == [
        "REPLACE 11 luku 149 § johd",
        "REPLACE 11 luku 149 § 1 mom 4 kohta",
        "INSERT 11 luku 149 § 1 mom 5 kohta",
    ]
    assert any(
        observation["kind"] == "ELAB.UNASSIGNED_SPARSE_SLOTS"
        and observation["detail"]["unassigned_slots"] == ("2:2", "3:3", "4:4", "5:5")
        for observation in observations
    )


def test_inspect_amendment_2003_549_2006_1293_keeps_explicit_section_149_item_targets_under_moment_1() -> None:
    """Explicit `1 momentin kohta` targets must not rebase to sibling `4 momentti`.

    Regression family: `2003/549 <- 2006/1293` carries one sparse payload slot
    plus a plain `4 momentti` replace. Payload normalization previously rebound
    explicit item replacements for `1 momentti` to `4 momentti`, which then
    duplicated the item list into subsection 4 for the live statute.
    """
    bundle = build_amendment_bundle("2003/549", "2006/1293", mode="official_consolidation")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "149")

    assert group["ops_raw"] == [
        "REPLACE 11 luku 149 § 1 mom 1 kohta",
        "REPLACE 11 luku 149 § 1 mom 2 kohta",
        "REPLACE 11 luku 149 § 1 mom 3 kohta",
        "REPLACE 11 luku 149 § 4 mom",
    ]
    assert group["ops_after_normalization"] == group["ops_raw"]


def test_inspect_amendment_2007_121_2010_1357_maps_new_45_3_before_moved_old_3() -> None:
    bundle = build_amendment_bundle("2007/121", "2010/1357", mode="official_consolidation")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "45")

    assert "INSERT 5 luku 45 § 3 mom" in group["ops_final"]
    assert "REPLACE 5 luku 45 § 3 mom" not in group["ops_after_normalization"]
    assert any(
        observation["kind"] == "ELAB.REBASE_REPLACED_RENUMBER_SOURCE"
        and observation["detail"]["rebases"] == [
            {
                "from_paragraph": 3,
                "to_paragraph": 4,
                "op_description": "REPLACE 5 luku 45 § 3 mom",
            }
        ]
        for observation in group["elaboration_observations"]
    )
    assert [
        (row["op"], row["slot_label"], row["target_paragraph"])
        for row in group["sparse_slot_bindings"]
        if row["op"] in {"INSERT 5 luku 45 § 3 mom", "RENUMBER 5 luku 45 § 3 mom"}
    ] == [
        ("INSERT 5 luku 45 § 3 mom", "2", 3),
        ("RENUMBER 5 luku 45 § 3 mom", "3", 3),
    ]
    assert any(
        observation["kind"] == "ELAB.INSERT_BEFORE_MOVED_SAME_TARGET_SLOT"
        and observation["detail"]["target_paragraph"] == 3
        for observation in group["elaboration_observations"]
    )
    assert not any(
        observation["kind"] == "ELAB.UNASSIGNED_SPARSE_SLOTS"
        and "2:2" in observation["detail"].get("unassigned_slots", ())
        for observation in group["elaboration_observations"]
    )


def test_replay_2007_121_keeps_stem_host_inserted_sections_out_of_3b_payload() -> None:
    result = replay_xml("2007/121", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(result.materialized_state.ir)

    assert "chapter:3b/section:35a" not in sections
    assert "chapter:3b/section:48a" not in sections
    assert "chapter:3b/section:112a" not in sections
    assert "chapter:4/section:35a" in sections
    assert "chapter:5/section:48a" in sections
    assert "chapter:7/section:112a" in sections
    assert "chapter:7/section:112f" in sections


def test_inspect_amendment_1992_147_1995_337_maps_historical_top_level_kohta_to_subsections() -> None:
    """Historical top-level `kohta` wording can name direct subsection siblings.

    `1995/337` says `4 §:n kohdan 24` and `uudet (29) ja (30) kohdat`, while
    the live/source section models `(1)`, `(2)`, ... as direct subsection
    siblings.  This must not compile as a destructive whole-section replace or
    as items under subsection 1.
    """
    bundle = build_amendment_bundle("1992/147", "1995/337", mode="legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "4")

    assert group["ops_raw"] == [
        "REPLACE 4 § 24 mom",
        "INSERT 4 § 29 mom",
        "INSERT 4 § 30 mom",
    ]
    assert group["ops_after_normalization"] == group["ops_raw"]
    assert group["ops_final"] == group["ops_raw"]
    assert [
        (row["op"], row["slot_label"], row["target_paragraph"])
        for row in group["sparse_slot_bindings"]
    ] == [
        ("REPLACE 4 § 24 mom", "24", 24),
        ("INSERT 4 § 29 mom", "29", 29),
        ("INSERT 4 § 30 mom", "30", 30),
    ]
    assert group["elaboration_observations"] == []


def test_inspect_amendment_2005_579_2014_751_drops_language_variant_plain_replaces_for_section_9() -> None:
    bundle = build_amendment_bundle("2005/579", "2014/751", mode="official_consolidation")
    group9 = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "section"
        and group["target_norm"] == "9"
        and group["target_chapter"] == "1"
    )

    assert group9["ops_final"] == [
        "REPLACE 1 luku 9 § 3 mom 2 kohta",
    ]
    assert any(
        observation["kind"] == "ELAB.MIXED_SPARSE_SLOT_CROSS_PARAGRAPH"
        for observation in group9["elaboration_observations"]
    )


@pytest.mark.slow
def test_inspect_amendment_2014_527_2019_49_keeps_section_149b_between_149a_and_149c() -> None:
    bundle = build_amendment_bundle("2014/527", "2019/49", mode="legal_pit")
    groups = {group["target_norm"]: group for group in bundle["groups"]}

    assert groups["149"]["target_chapter"] == "15"
    assert groups["149a"]["target_chapter"] == "15"
    assert groups["149b"]["target_chapter"] == "15"
    assert groups["149c"]["target_chapter"] == "15"
    assert groups["211b"]["target_chapter"] == "20"


def test_normalize_amendment_1992_1243_2004_254_rehomes_section_71_from_cited_repeal_scope() -> None:
    xml_bytes = get_corpus().read_source("2004/254")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    master = replay_xml(
        parent_id="1992/1243",
        stop_before="2004/254",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    ).state

    phase = normalize_and_compile_ops(
        johto=get_johtolause(xml_bytes),
        muutos_tree=muutos_tree,
        master=master,
        base_ir=None,
        amendment_id="2004/254",
        source_title="Valtioneuvoston asetus valtion talousarviosta annetun asetuksen muuttamisesta",
        used_preamble_body_fallback=False,
        parent_id="1992/1243",
        strict_profile=None,
    )

    op71 = next(op for op in phase.output if op.op_type == "INSERT" and op.target_cols.target_section == "71")

    assert op71.description() == "INSERT 9 luku 71 §"
    assert op71.lo is not None
    assert op71.lo.witness_rule_id == "fi_reinstated_section_scope_from_prior_repeal_address"


@pytest.mark.slow
def test_inspect_amendment_2014_527_2022_490_reports_pre_merge_whole_section_constraint_shape() -> None:
    bundle = build_amendment_bundle("2014/527", "2022/490", mode="official_consolidation")
    group221c = next(group for group in bundle["groups"] if group["target_norm"] == "221c")

    assert group221c["ops_raw"] == ["REPLACE 20 luku 221c § otsikko", "REPLACE 20 luku 221c § 1 mom"]
    assert set(group221c["ops_final"]) == {
        "REPLACE 20 luku 221c § otsikko",
        "REPLACE 20 luku 221c § 1 mom",
    }
    assert group221c["subsection_map"][0]["op"] == "REPLACE 20 luku 221c § otsikko"
    assert group221c["subsection_map"][0]["mapped_payload"] is None
    assert group221c["subsection_map"][1]["op"] == "REPLACE 20 luku 221c § 1 mom"
    assert group221c["subsection_map"][1]["mapped_payload"]["label"] == "1"
    assert group221c["rejected_ops_pre_constraints"] == []
    assert group221c["rejected_ops_post_constraints"] == []


def test_inspect_amendment_1965_40_1989_612_keeps_section_25_1a_in_explicit_chapter() -> None:
    bundle = build_amendment_bundle("1965/40", "1989/612", mode="official_consolidation")
    group1a = next(group for group in bundle["groups"] if group["target_norm"] == "1a")
    group7 = next(group for group in bundle["groups"] if group["target_norm"] == "7")

    assert group1a["target_chapter"] == "25"
    assert group1a["ops_final"] == ["INSERT 25 luku 1a §"]
    assert group7["target_chapter"] == "25"
    assert group7["ops_final"] == ["REPLACE 25 luku 7 §"]


def test_inspect_amendment_1965_40_2004_783_keeps_section_19_12a_in_explicit_chapter() -> None:
    bundle = build_amendment_bundle("1965/40", "2004/783", mode="official_consolidation")
    group12a = next(group for group in bundle["groups"] if group["target_norm"] == "12a")

    assert group12a["target_chapter"] == "19"
    assert group12a["ops_final"] == ["INSERT 19 luku 12a §"]


def test_inspect_amendment_1940_378_1995_1392_aligns_sparse_omission_replace_to_subsection_2() -> None:
    bundle = build_amendment_bundle("1940/378", "1995/1392", mode="official_consolidation")
    group20 = next(group for group in bundle["groups"] if group["target_norm"] == "20")

    assert group20["ops_final"] == ["REPLACE 20 § 2 mom"]
    assert group20["subsection_map"][0]["mapped_payload"]["label"] == "2"
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group20["elaboration_observations"]
    )
    assert all(
        observation["kind"] != "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING"
        for observation in group20["elaboration_observations"]
    )


def test_inspect_amendment_1940_378_1994_318_drops_payloadless_replace_shadowed_by_direct_relabel() -> None:
    bundle = build_amendment_bundle("1940/378", "1994/318", mode="official_consolidation")
    group73 = next(
        group
        for group in bundle["groups"]
        if group["target_norm"] == "73" and group["target_chapter"] == "7"
    )

    assert group73["normalized_payload"] is None
    assert group73["ops_final"] == ["RENUMBER 7 luku 73 §"]
    assert any(
        rejected["reason_code"] == "ELAB.REJECTED_NO_SOURCE_PAYLOAD"
        for rejected in group73["rejected_ops_pre_constraints"]
    )

    group7 = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "chapter" and group["target_norm"] == "7"
    )
    assert group7["raw_payload"]["children"] == 3
    assert group7["normalized_payload"]["children"] > group7["raw_payload"]["children"]
    assert group7["payload_completeness"]["payload_completeness_kind"] == "complete"
    assert group7["payload_completeness"]["tail_policy"] == "replace_if_target_scope_requires"
    assert group7["payload_completeness"]["source_child_labels"] == ("61",)


def test_inspect_amendment_1966_611_1981_20_recovers_heading_tagged_subsection_payload() -> None:
    """Heading-tagged body text may satisfy an explicit subsection replacement."""
    bundle = build_amendment_bundle("1966/611", "1981/20", mode="legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "4")

    assert group["ops_raw"] == ["REPLACE 4 § 1 mom"]
    assert group["ops_final"] == ["REPLACE 4 § 1 mom"]
    assert group["source_pathologies"] == []
    assert group["sparse_slot_bindings"] == [
        {
            "op": "REPLACE 4 § 1 mom",
            "target_paragraph": 1,
            "target_item": "",
            "target_special": "",
            "slot_index": 1,
            "slot_label": "1",
        }
    ]
    assert group["subsection_map"][0]["mapped_payload"]["kind"] is IRNodeKind.SUBSECTION
    assert group["subsection_map"][0]["mapped_payload"]["label"] == "1"
    assert any(
        observation["kind"] == "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD"
        and observation["detail"]["target_paragraph"] == 1
        and observation["detail"]["rule"] == "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD"
        for observation in group["elaboration_observations"]
    )


def test_inspect_amendment_1966_611_1986_193_binds_unlabeled_table_item_rows_by_source_order() -> None:
    bundle = build_amendment_bundle("1966/611", "1986/193", mode="legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "5")

    assert group["ops_final"] == [
        "REPLACE 5 § 1 mom 12 kohta",
        "INSERT 5 § 1 mom 13 kohta",
    ]
    mapped = {entry["op"]: entry["mapped_payload"] for entry in group["subsection_map"]}
    assert "12) rehtorina, apulaisrehtorina" in mapped[
        "REPLACE 5 § 1 mom 12 kohta"
    ]["text"]
    assert "13) johtajana, asuntolanjohtajana" in mapped[
        "INSERT 5 § 1 mom 13 kohta"
    ]["text"]
    assert {
        observation["kind"]
        for observation in group["elaboration_observations"]
    } >= {"ELAB.UNLABELED_TABLE_ITEM_ROW_SOURCE_ORDER"}


def test_inspect_amendment_2020_811_2021_278_promotes_leading_subsection_heading_payload() -> None:
    """A whole-section insert may carry the section heading as its first subsection."""
    bundle = build_amendment_bundle("2020/811", "2021/278", mode="legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "11a")

    assert bundle["compiled_ops"] == ["INSERT 1 luku 11a §"]
    assert group["ops_raw"] == ["INSERT 1 luku 11a §"]
    assert group["ops_final"] == ["INSERT 1 luku 11a §"]
    assert group["source_pathologies"] == []
    assert any(
        observation["kind"] == "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        and observation["detail"]["shifted_subsection_count"] == 1
        and observation["detail"]["rule"] == "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        for observation in group["elaboration_observations"]
    )


def test_inspect_amendment_2022_1393_2024_870_keeps_inline_styled_leading_subsection() -> None:
    """Inline-styled leading subsection text is not promoted to a section heading."""
    bundle = build_amendment_bundle("2022/1393", "2024/870", mode="legal_pit")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "7a")

    assert "INSERT 7a §" in bundle["compiled_ops"]
    assert group["ops_raw"] == ["INSERT 7a §"]
    assert group["ops_final"] == ["INSERT 7a §"]
    assert all(
        observation["kind"] != "ELAB.LEADING_SUBSECTION_HEADING_PAYLOAD"
        for observation in group["elaboration_observations"]
    )


def test_inspect_amendment_1962_420_2024_247_keeps_heading_insert_out_of_subsection_payload() -> None:
    """A same-group heading-facet insert is not subsection body authority."""
    bundle = build_amendment_bundle("1962/420", "2024/247", mode="official_consolidation")
    group12 = next(
        group
        for group in bundle["groups"]
        if group["target_norm"] == "12" and group["target_chapter"] == "3"
    )

    assert group12["ops_raw"] == [
        "REPLACE 3 luku 12 § 1 mom",
        "INSERT 3 luku 12 § otsikko",
    ]
    assert group12["sparse_slot_bindings"] == []
    assert all(
        observation["kind"] != "ELAB.HEADING_TAGGED_SUBSECTION_PAYLOAD"
        for observation in group12["elaboration_observations"]
    )


def test_inspect_amendment_1990_656_2021_652_keeps_source_heading_facet() -> None:
    """Explicit heading op survives sparse projection when raw source has heading."""
    bundle = build_amendment_bundle("1990/656", "2021/652", mode="legal_pit")
    group12 = next(group for group in bundle["groups"] if group["target_norm"] == "12")

    assert group12["ops_final"] == [
        "REPLACE 12 § 1 mom",
        "INSERT 12 § otsikko",
    ]
    assert group12["rejected_ops_post_constraints"] == []
    assert any(
        observation["kind"] == "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET"
        for observation in group12["elaboration_observations"]
    )


def test_inspect_amendment_1993_81_1994_495_recovers_short_pykala_illative_typo() -> None:
    """The source typo `§:än` must not drop an explicit subsection insertion."""
    bundle = build_amendment_bundle("1993/81", "1994/495", mode="legal_pit")

    assert bundle["compiled_ops"] == ["INSERT 2 § 5 mom"]
    group = next(group for group in bundle["groups"] if group["target_norm"] == "2")

    assert group["ops_raw"] == ["INSERT 2 § 5 mom"]
    assert group["ops_final"] == ["INSERT 2 § 5 mom"]
    assert group["sparse_slot_bindings"] == [
        {
            "op": "INSERT 2 § 5 mom",
            "target_paragraph": 5,
            "target_item": "",
            "target_special": "",
            "slot_index": 1,
            "slot_label": "5",
        }
    ]
    mapped_payload = group["subsection_map"][0]["mapped_payload"]
    assert mapped_payload["label"] == "5"
    assert "Euroopan talousalueen valtioiden kansalaisten" in mapped_payload["text"]


def test_inspect_amendment_1988_575_1995_407_applies_after_nojalla_authority_prefix() -> None:
    """A leading authority citation must not hide the later target statute."""
    bundle = build_amendment_bundle("1988/575", "1995/407", mode="legal_pit")

    assert bundle["route"] == {"should_apply": True, "reason": "references_parent", "target_amendment_id": ""}
    assert bundle["compiled_ops"] == ["INSERT 25a §"]
    group = next(group for group in bundle["groups"] if group["target_norm"] == "25a")
    assert group["normalized_payload"]["kind"] is IRNodeKind.SECTION
    assert "Telekuuntelusta, televalvonnasta ja teknisestä tarkkailusta" in group["normalized_payload"]["text"]


def test_inspect_amendment_1998_358_2001_1065_applies_after_nojalla_sellaisina_prefix() -> None:
    """A no-comma ``nojalla sellaisina kuin`` authority prefix must not hide the target."""
    bundle = build_amendment_bundle("1998/358", "2001/1065", mode="legal_pit")

    assert bundle["route"] == {"should_apply": True, "reason": "references_parent", "target_amendment_id": ""}
    assert "REPLACE 2 §" in bundle["compiled_ops"]
    assert "REPLACE 6 §" in bundle["compiled_ops"]
    group = next(group for group in bundle["groups"] if group["target_norm"] == "6")
    assert group["ops_final"] == ["REPLACE 6 §"]
    assert group["normalized_payload"]["kind"] is IRNodeKind.SECTION
    assert "enintään 1 009 euroa 90 %" in group["normalized_payload"]["text"]


def test_build_amendment_bundle_2012_980_2022_604_applies_johtolause_corrigendum_to_repeal_target() -> None:
    bundle = build_amendment_bundle("2012/980", "2022/604", mode="official_consolidation")

    descriptions = bundle["compiled_ops"]

    assert "REPEAL 1 luku 2 § 3 mom" in descriptions
    assert "REPEAL 2 § 2 mom" not in descriptions


def test_emit_restructure_plan_renumber_legal_operations_emits_explicit_renumber_lo() -> None:
    from lawvm.core.ir import LegalAddress
    from lawvm.core.provenance import MigrationEvent
    from lawvm.finland.restructure_plan_replay import (
        FI_RESTRUCTURE_RENUMBER_TIMELINE_RULE_ID,
        emit_restructure_plan_renumber_legal_operations,
    )

    lo_ops: list[LegalOperation] = []
    emitted = emit_restructure_plan_renumber_legal_operations(
        lo_ops_out=lo_ops,
        migration_events=(
            MigrationEvent(
                event_id="mig:test",
                kind="renumber",
                from_address=LegalAddress(path=(("section", "73"),)),
                to_address=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
                effective="1994-07-01",
                source_statute="1994/318",
            ),
        ),
        amendment_id="1994/318",
        source_title="Test",
        amendment_issue_date=dt.date(1994, 3, 30),
        amendment_effective_date=dt.date(1994, 7, 1),
    )

    assert emitted == 1
    assert len(lo_ops) == 1
    assert lo_ops[0].action is StructuralAction.RENUMBER
    assert lo_ops[0].target == LegalAddress(path=(("section", "73"),))
    assert lo_ops[0].destination == LegalAddress(path=(("chapter", "7"), ("section", "61")))
    assert lo_ops[0].witness_rule_id == FI_RESTRUCTURE_RENUMBER_TIMELINE_RULE_ID


def test_emit_restructure_plan_section_snapshot_uses_live_applied_path() -> None:
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.restructure_plan import ExecutedOp, StructuralTransformOp, TransformOpKind
    from lawvm.finland.restructure_plan_replay import (
        FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID,
        emit_restructure_plan_section_snapshot_legal_operations,
    )

    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="209",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="209 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Renumbered section"),
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="V osa"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="4",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                            section,
                        ),
                    ),
                ),
            ),
        ),
    )
    executed = (
        ExecutedOp(
            op=StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:4/chapter:4/section:1",
                destination="part:4/chapter:4/section:209",
                notes=("from_amendment_op",),
            ),
            success=True,
            applied_path=(("part", "5"), ("chapter", "4"), ("section", "209")),
        ),
    )
    lo_ops: list[LegalOperation] = []
    emitted = emit_restructure_plan_section_snapshot_legal_operations(
        lo_ops_out=lo_ops,
        state_ir=body,
        executed_ops=executed,
        amendment_id="2019/371",
        source_title="Test relabel snapshot",
        amendment_issue_date=dt.date(2019, 12, 20),
        amendment_effective_date=dt.date(2020, 1, 1),
    )

    assert emitted == 1
    assert lo_ops[0].action is StructuralAction.INSERT
    assert lo_ops[0].target == LegalAddress(
        path=(("part", "5"), ("chapter", "4"), ("section", "209"))
    )
    assert lo_ops[0].payload is not None
    assert lo_ops[0].witness_rule_id == FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID


def test_emit_restructure_plan_section_snapshot_resolves_post_part_relabel_live_path() -> None:
    """Snapshot emission must follow the final live tree, not relabel-time part frames."""
    from lawvm.core.ir import LegalAddress
    from lawvm.finland.restructure_plan import ExecutedOp, StructuralTransformOp, TransformOpKind
    from lawvm.finland.restructure_plan_replay import (
        emit_restructure_plan_section_snapshot_legal_operations,
    )

    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="209",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="209 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Renumbered section"),
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="V osa"),
                            IRNode(
                                kind=IRNodeKind.CHAPTER,
                                label="4",
                                children=(
                                    IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                                    section,
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executed = (
        ExecutedOp(
            op=StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:4/chapter:4/section:1",
                destination="section:209",
                notes=("from_amendment_op",),
            ),
            success=True,
            applied_path=(("part", "4"), ("chapter", "4"), ("section", "209")),
        ),
    )
    lo_ops: list[LegalOperation] = []
    emitted = emit_restructure_plan_section_snapshot_legal_operations(
        lo_ops_out=lo_ops,
        state_ir=body,
        executed_ops=executed,
        amendment_id="2019/371",
        source_title="Test post-part relabel snapshot",
        amendment_issue_date=dt.date(2019, 12, 20),
        amendment_effective_date=dt.date(2020, 1, 1),
    )

    assert emitted == 1
    assert lo_ops[0].target == LegalAddress(
        path=(("part", "5"), ("chapter", "4"), ("section", "209"))
    )


def test_chapter_part_move_label_reuse_guard_finding_stamps_guard_rule_id() -> None:
    from lawvm.finland.restructure_plan_replay import (
        CHAPTER_PART_MOVE_LABEL_REUSE_SKIP_REASON,
        FI_RESTRUCTURE_CHAPTER_PART_MOVE_LABEL_REUSE_GUARD_RULE_ID,
        chapter_part_move_label_reuse_guard_finding,
    )

    finding = chapter_part_move_label_reuse_guard_finding(
        source_statute="2001/1226",
        chapter_label="4",
        old_part_label="2",
        new_part_label="5",
    )

    assert finding.kind == "APPLY.MOVE_SKIP"
    assert finding.detail["reason_code"] == CHAPTER_PART_MOVE_LABEL_REUSE_SKIP_REASON
    assert (
        finding.detail["witness_rule_id"]
        == FI_RESTRUCTURE_CHAPTER_PART_MOVE_LABEL_REUSE_GUARD_RULE_ID
    )


def test_build_chapter_part_move_timeline_ops_stamps_stable_witness_rule_id() -> None:
    from lawvm.core.ir import IRNode, LegalAddress, OperationSource
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.finland.restructure_plan_replay import (
        ChapterPartMoveTimelineRequest,
        FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID,
        build_chapter_part_move_timeline_ops,
    )

    chapter = IRNode(kind=IRNodeKind.CHAPTER, label="2", children=())
    source = OperationSource(statute_id="1994/318", title="Test", enacted="", effective="")

    ops = build_chapter_part_move_timeline_ops(
        ChapterPartMoveTimelineRequest(
            amendment_id="1994/318",
            chapter_label="2",
            old_part_label="I",
            new_part_label="II",
            payload=chapter,
            source=source,
        )
    )

    assert ops.repeal.action is StructuralAction.REPEAL
    assert ops.repeal.target == LegalAddress(path=(("part", "I"), ("chapter", "2")))
    assert ops.repeal.witness_rule_id == FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID
    assert ops.insert.action is StructuralAction.INSERT
    assert ops.insert.target == LegalAddress(path=(("part", "II"), ("chapter", "2")))
    assert ops.insert.payload is chapter
    assert ops.insert.witness_rule_id == FI_RESTRUCTURE_CHAPTER_PART_MOVE_TIMELINE_RULE_ID


def test_ambiguous_unscoped_additive_fallback_insert_observation() -> None:
    existing_ops = [
        AmendmentOp(
            op_id="c1",
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="c2",
            op_type=OpType.INSERT,
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="2",
        ),
    ]
    fallback_insert = AmendmentOp(
        op_id="fb",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="7",
        extraction_provenance_tags=("extraction_fallback_heuristic",),
    )

    finding = _ambiguous_unscoped_additive_fallback_insert_observation(
        existing_ops,
        fallback_insert,
        amendment_id="2015/1752",
    )

    assert finding is not None
    assert finding.detail["reason_code"] == "ELAB.AMBIGUOUS_UNSCOPED_FALLBACK_INSERT_MULTI_SCOPE"
    assert finding.detail["candidate_chapters"] == ("1", "2")


def test_ambiguous_unscoped_additive_fallback_insert_observation_keeps_unique_scope() -> None:
    existing_ops = [
        AmendmentOp(
            op_id="c1",
            op_type=OpType.REPLACE,
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="1",
        ),
    ]
    fallback_insert = AmendmentOp(
        op_id="fb",
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_paragraph=1,
        target_item="7",
        extraction_provenance_tags=("extraction_fallback_heuristic",),
    )

    finding = _ambiguous_unscoped_additive_fallback_insert_observation(
        existing_ops,
        fallback_insert,
        amendment_id="2015/1752",
    )

    assert finding is None


def test_attach_target_version_selectors_binds_matching_section_ops_only() -> None:
    parse_result = SimpleNamespace(
        target_version_bindings=(
            SimpleNamespace(target_labels=("23",), cited_statute_id="2015/195"),
            SimpleNamespace(target_labels=("24c", "30b", "34a"), cited_statute_id="2018/575"),
        )
    )
    ops = [
        AmendmentOp(op_type=OpType.REPLACE, target_section="23", target_unit_kind="section"),
        AmendmentOp(op_type=OpType.REPLACE, target_section="24c", target_unit_kind="section", target_paragraph=3),
        AmendmentOp(op_type=OpType.REPLACE, target_kind=TargetKind.CHAPTER, target_section="7"),
    ]

    patched, findings = _attach_target_version_selectors(
        ops,
        parse_result=cast(Any, parse_result),
        amendment_id="2018/945",
    )

    assert findings == []
    assert patched[0].target_version_statute_id == "2015/195"
    assert patched[1].target_version_statute_id == "2018/575"
    assert patched[2].target_version_statute_id is None


def test_attach_target_version_selectors_reports_ambiguous_label() -> None:
    parse_result = SimpleNamespace(
        target_version_bindings=(
            SimpleNamespace(target_labels=("24c",), cited_statute_id="2018/575"),
            SimpleNamespace(target_labels=("24c",), cited_statute_id="2019/10"),
        )
    )
    op = AmendmentOp(op_type=OpType.REPLACE, target_section="24c", target_unit_kind="section")

    patched, findings = _attach_target_version_selectors(
        [op],
        parse_result=cast(Any, parse_result),
        amendment_id="2018/945",
    )

    assert patched[0].target_version_statute_id is None
    assert any(
        finding.kind == "ELAB.REJECTED_OPERATION"
        and finding.detail.get("reason_code") == "ELAB.AMBIGUOUS_TARGET_VERSION_SELECTOR"
        and finding.detail.get("target_section") == "24c"
        for finding in findings
    )


def test_restore_heading_facet_for_mixed_scope_section_replaces_rewrites_plain_section_replace() -> None:
    parse_result = parse_clause("muutetaan 8 §:n otsikko ja 3 momentti")
    heading_op = AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="section", target_section="8")
    child_op = AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="section", target_section="8", target_paragraph=3)

    patched, findings = _restore_heading_facet_for_mixed_scope_section_replaces(
        [heading_op, child_op],
        parse_result=parse_result,
        amendment_id="2016/784",
    )

    # Function marks the heading op with preserve_explicit_heading_facet=True
    # but does NOT overwrite target_special — setting it to "otsikko" would
    # cause apply_structure_ops section handler to skip the op entirely
    # (only None / "otsikko_edella" pass through that gate).
    assert patched[0].description() == "REPLACE 8 §"
    assert patched[0].target_cols.target_special is None
    assert patched[0].preserve_explicit_heading_facet is True
    assert patched[1].description() == "REPLACE 8 § 3 mom"
    assert findings == []


def test_restore_heading_facet_preserves_explicit_heading_only_clause() -> None:
    parse_result = parse_clause("muutetaan 27 a §:n edellä olevan luvun otsikko")
    heading_op = AmendmentOp(
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="27a",
        target_special="otsikko",
    )

    patched, findings = _restore_heading_facet_for_mixed_scope_section_replaces(
        [heading_op],
        parse_result=parse_result,
        amendment_id="1996/322",
    )

    assert patched[0].description() == "REPLACE 27a § otsikko"
    assert patched[0].target_cols.target_special == "otsikko"
    assert patched[0].preserve_explicit_heading_facet is True
    assert findings == []


def test_rewrite_later_effective_lo_groups_scopes_deferred_cited_version_ops() -> None:
    lo_ops = [
        LegalOperation(
            op_id="snapshot_section_24c",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "6"), ("section", "24c"))),
            payload=IRNode(kind=IRNodeKind.SECTION, label="24c"),
            source=OperationSource(
                statute_id="2018/945",
                enacted="2018-11-23",
                effective="2019-01-01",
            ),
            group_id="finland-johto:2018/945",
        ),
        LegalOperation(
            op_id="snapshot_section_23",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "6"), ("section", "23"))),
            payload=IRNode(kind=IRNodeKind.SECTION, label="23"),
            source=OperationSource(
                statute_id="2018/945",
                enacted="2018-11-23",
                effective="2018-11-23",
            ),
            group_id="finland-johto:2018/945",
        ),
    ]

    touched = _rewrite_later_effective_lo_groups(
        lo_ops,
        target_source_statute="2018/945",
        amendment_effective_date=dt.date(2018, 11, 23),
    )

    assert touched == {
        "2019-01-01": (LegalAddress(path=(("chapter", "6"), ("section", "24c"))),),
    }
    assert lo_ops[0].group_id == "finland-johto:2018/945:effective:2019-01-01"
    assert lo_ops[1].group_id == "finland-johto:2018/945"


def test_rewrite_compiled_op_activation_rule_effective_for_addresses_limits_to_exact_targets() -> None:
    rows: list[dict[str, object]] = [
        {
            "source_statute": "2018/945",
            "target_unit_kind": "section",
            "target_part": "",
            "target_chapter": "6",
            "target_norm": "24c",
            "activation_rule": {"kind": "fixed_date", "effective_date": "2018-11-23", "condition_ref": ""},
            "is_contingent": False,
        },
        {
            "source_statute": "2018/945",
            "target_unit_kind": "section",
            "target_part": "",
            "target_chapter": "6",
            "target_norm": "23",
            "activation_rule": {"kind": "fixed_date", "effective_date": "2018-11-23", "condition_ref": ""},
            "is_contingent": False,
        },
    ]

    updated = _rewrite_compiled_op_activation_rule_effective_for_addresses(
        rows,
        target_source_statute="2018/945",
        effective_date=dt.date(2019, 1, 1),
        exact_addresses=(LegalAddress(path=(("chapter", "6"), ("section", "24c"))),),
    )

    assert updated is True
    assert rows[0]["activation_rule"]["effective_date"] == "2019-01-01"
    assert rows[1]["activation_rule"]["effective_date"] == "2018-11-23"


def test_reject_overbroad_section_repeal_for_deep_target() -> None:
    child_repeal = AmendmentOp(
        op_id="parsed_child",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="1",
        target_paragraph=3,
        target_item="2",
    )
    repeal = AmendmentOp(
        op_id="fb",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="1",
    )

    kept, findings = _reject_overbroad_section_repeals_for_deep_targets(
        [child_repeal, repeal],
        johto="Tällä päätöksellä kumotaan päätöksen 1 §:n 3.3.2. kohta.",
        amendment_id="2007/180",
    )

    assert kept == [child_repeal]
    assert len(findings) == 1
    assert findings[0].detail["reason_code"] == "ELAB.OVERBROAD_SECTION_REPEAL_FOR_DEEP_TARGET"


def test_reject_overbroad_section_repeal_for_deep_target_keeps_plain_section_repeal() -> None:
    repeal = AmendmentOp(
        op_id="fb",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="1",
    )

    kept, findings = _reject_overbroad_section_repeals_for_deep_targets(
        [repeal],
        johto="Tällä päätöksellä kumotaan päätöksen 1 §.",
        amendment_id="2007/180",
    )

    assert kept == [repeal]
    assert findings == []


def test_reject_overbroad_section_repeal_for_deep_target_keeps_other_section_repeal() -> None:
    child_repeal = AmendmentOp(
        op_id="parsed_child",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_paragraph=1,
        target_item="9",
    )
    repeal_deep_host = AmendmentOp(
        op_id="fb1",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="12",
    )
    repeal_other_section = AmendmentOp(
        op_id="fb2",
        op_type=OpType.REPEAL,
        target_kind=TargetKind.SECTION,
        target_section="12f",
    )

    kept, findings = _reject_overbroad_section_repeals_for_deep_targets(
        [child_repeal, repeal_deep_host, repeal_other_section],
        johto=(
            "Tällä lailla kumotaan 12 §:n 1 momentin 9 kohta sekä "
            "12 f §."
        ),
        amendment_id="2015/521",
    )

    assert kept == [child_repeal, repeal_other_section]
    assert len(findings) == 1
    assert findings[0].detail["target_section"] == "12"


@pytest.mark.slow
def test_inspect_amendment_1994_674_2016_860_keeps_section_1_inside_new_chapter_11a() -> None:
    bundle = build_amendment_bundle("1994/674", "2016/860", mode="official_consolidation")
    group11a = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "chapter" and group["target_norm"] == "11a"
    )

    assert group11a["ops_final"] == ["INSERT 11a luku"]
    assert "1 § Nairobin yleissopimuksen soveltaminen Suomessa" in group11a["normalized_payload"]["text"]
    assert all(
        observation["kind"] != "ELAB.CONTAINER_PRUNED_SHADOWED"
        for observation in group11a["elaboration_observations"]
    )


@pytest.mark.slow
def test_inspect_amendment_1994_674_2019_1401_shows_whole_chapter_replace_not_heading_only() -> None:
    bundle = build_amendment_bundle("1994/674", "2019/1401", mode="official_consolidation")
    group11 = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "chapter" and group["target_norm"] == "11"
    )

    assert "REPLACE 11 luku" in bundle["compiled_ops"]
    assert "REPLACE 11 luku otsikko" not in bundle["compiled_ops"]
    assert group11["ops_final"] == ["REPLACE 11 luku"]
    assert group11["subsection_map"][0]["op"] == "REPLACE 11 luku otsikko"
    assert group11["subsection_map"][0]["mapped_payload"] is None


def test_inspect_amendment_2011_1552_2022_1188_reports_pending_amendment_skip_family() -> None:
    bundle = build_amendment_bundle("2011/1552", "2022/1188", mode="official_consolidation")

    assert bundle["route"]["should_apply"] is False
    assert bundle["route"]["reason"] == "pending_amendment_of_parent_skip"
    assert bundle["route"]["target_amendment_id"] == "2022/631"


def test_inspect_amendment_2011_1552_2022_708_reports_pending_amendment_skip_family() -> None:
    bundle = build_amendment_bundle("2011/1552", "2022/708", mode="official_consolidation")

    assert bundle["route"]["should_apply"] is False
    assert bundle["route"]["reason"] == "pending_amendment_of_parent_skip"
    assert bundle["route"]["target_amendment_id"] == "2020/1233"


def test_process_muutoslaki_2011_1552_composes_pending_amendment_on_processed_target() -> None:
    replay = pinned_replay("2011/1552", mode="official_consolidation", quiet=True)
    findings = list(replay.findings or [])

    assert any(
        str(f.kind or "") == "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET"
        and str(f.source_statute or "") in {"2022/708", "2022/1188"}
        for f in findings
    )
    pending_relations = [
        relation
        for relation in replay.products.effect_relations
        if relation.detail.get("source_finding")
        == "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET"
    ]
    assert {
        relation.target_instrument.instrument_id
        for relation in pending_relations
        if relation.target_instrument is not None
    } == {"2020/1233", "2022/631"}
    assert {
        effect.source_instrument.instrument_id
        for effect in replay.products.source_effects
    } >= {"2022/708", "2022/1188"}


def test_inspect_amendment_2013_588_2025_201_owns_sparse_higher_moment_and_trailing_insert_bindings(
    amendment_bundle_2013_588_2025_201: dict[str, Any],
) -> None:
    bundle = amendment_bundle_2013_588_2025_201
    group21b = next(group for group in bundle["groups"] if group["target_norm"] == "21b")
    group87 = next(group for group in bundle["groups"] if group["target_norm"] == "87")

    assert group21b["ops_final"] == ["REPLACE 4 luku 21b § 2 mom"]
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group21b["elaboration_observations"]
    )
    assert all(
        observation["kind"] not in {"ELAB.AMBIGUOUS_BINDING", "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING"}
        for observation in group21b["elaboration_observations"]
    )

    assert group87["target_part"] == ""
    assert group87["target_chapter"] == "13"
    assert group87["ops_final"] == [
        "REPLACE 13 luku 87 § 1 mom",
        "INSERT 13 luku 87 § 6 mom",
    ]
    assert group87["rejected_ops_pre_constraints"] == []
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group87["elaboration_observations"]
    )
    assert all(
        observation["kind"] != "ELAB.AMBIGUOUS_BINDING"
        for observation in group87["elaboration_observations"]
    )


def test_inspect_amendment_2012_1020_2024_776_does_not_duplicate_section_1_new_sixth_subsection() -> None:
    bundle = build_amendment_bundle("2012/1020", "2024/776", mode="official_consolidation")
    group1 = next(group for group in bundle["groups"] if group["target_norm"] == "1")
    group7 = next(group for group in bundle["groups"] if group["target_norm"] == "7")

    assert group1["ops_final"] == ["INSERT 1 luku 1 § 5 mom"]
    assert group7["ops_final"] == [
        "REPLACE 2 luku 7 § 1 mom 4 kohta",
        "INSERT 2 luku 7 § 1 mom 5 kohta",
        "INSERT 2 luku 7 § 6 mom",
    ]


def test_inspect_amendment_2012_1020_2015_1328_keeps_bare_johdanto_targets_and_later_section_refs_alive() -> None:
    bundle = build_amendment_bundle("2012/1020", "2015/1328", mode="official_consolidation")

    got = {group["target_norm"]: group["ops_final"] for group in bundle["groups"]}

    assert got["1"] == ["REPLACE 1 luku 1 §"]
    assert got["2"] == ["REPLACE 2 luku 2 § johd", "REPLACE 2 luku 2 § otsikko"]
    assert got["5"] == ["REPLACE 2 luku 5 § 1 mom 3 kohta"]
    assert got["9"] == ["REPLACE 3 luku 9 § 3 mom", "REPLACE 3 luku 9 § otsikko"]
    assert got["10"] == ["REPLACE 4 luku 10 §"]
    assert got["11"] == [
        "REPLACE 5 luku 11 § 1 mom 2 kohta",
        "REPEAL 5 luku 11 § 1 mom 4 kohta",
        "REPLACE 5 luku 11 § johd",
    ]


def test_inspect_amendment_2013_588_2025_201_recovers_section_49a_item_10_insert(
    amendment_bundle_2013_588_2025_201: dict[str, Any],
) -> None:
    bundle = amendment_bundle_2013_588_2025_201
    group49a = next(group for group in bundle["groups"] if group["target_norm"] == "49a")

    assert group49a["ops_raw"] == ["REPLACE 5 luku 49a § 1 mom 9 kohta", "INSERT 5 luku 49a § 1 mom 10 kohta"]
    assert group49a["ops_final"] == ["REPLACE 5 luku 49a § 1 mom 9 kohta", "INSERT 5 luku 49a § 1 mom 10 kohta"]


def test_inspect_amendment_2002_780_2003_666_keeps_head_insert_and_renumber_group() -> None:
    bundle = build_amendment_bundle("2002/780", "2003/666", mode="legal_pit")
    group4 = next(group for group in bundle["groups"] if group["target_norm"] == "4")

    assert group4["ops_raw"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]
    assert group4["ops_after_normalization"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]
    assert group4["ops_final"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]


@pytest.mark.slow
def test_replay_xml_2013_588_restores_section_49a_item_10_after_2025_201(
    replay_2013_588_finlex_oracle: Any,
) -> None:
    sec = replay_2013_588_finlex_oracle.materialized_state.find_section("49a", "5")

    assert sec is not None
    sub1 = next(
        child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    para_labels = [child.label for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH]

    assert "10" in para_labels
    assert "tietojen säilyttäminen" in irnode_to_text(sub1)


@pytest.mark.slow
def test_replay_xml_2013_588_routes_section_87_only_under_chapter_13_after_2025_201(
    replay_2013_588_finlex_oracle: Any,
) -> None:
    state = replay_2013_588_finlex_oracle.materialized_state
    sec = state.find_section("87", "13", "5")

    assert sec is not None
    assert state.find_section("87", "11a", "4") is None
    assert state.find_section("87", "7") is None

    sub_labels = [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert sub_labels == ["1", "2", "3", "4", "5", "6"]

    sub6 = next(
        child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "6"
    )
    assert "Jos sähkönmyyntisopimus on tehty kuluttajan kanssa" in irnode_to_text(sub6)


def test_inspect_amendment_2021_82_2024_495_recovers_section_1a_moment_5_and_section_83a() -> None:
    bundle = build_amendment_bundle("2021/82", "2024/495", mode="official_consolidation")
    group1a = next(group for group in bundle["groups"] if group["target_norm"] == "1a")
    group83a = next(group for group in bundle["groups"] if group["target_norm"] == "83a")

    assert group1a["ops_raw"] == ["INSERT 1 luku 1a § 5 mom"]
    assert group1a["ops_final"] == ["INSERT 1 luku 1a § 5 mom"]
    assert group83a["ops_raw"] == ["INSERT 4 luku 83a §"]
    assert group83a["ops_final"] == ["INSERT 4 luku 83a §"]


def test_replay_xml_2021_82_restores_section_1a_fifth_moment_after_2024_495() -> None:
    replay = pinned_replay("2021/82", mode="official_consolidation", quiet=True)
    sec = replay.materialized_state.find_section("1a", "1")

    assert sec is not None
    sub_labels = [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert "5" in sub_labels

    sub2 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")
    sub5 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "5")

    assert "Ajoneuvoon, jota saa käyttää yksinomaan yleiseltä liikenteeltä eristetyllä alueella" in irnode_to_text(sub2)
    assert "Puolustusyhteistyöstä Suomen tasavallan hallituksen ja Amerikan yhdysvaltojen hallituksen välillä" in irnode_to_text(sub5)


def test_replay_xml_2009_1599_restores_section_8_after_2023_280_same_wave_shift_family() -> None:
    replay = pinned_replay("2009/1599", stop_before="2023/152", mode="official_consolidation", quiet=True)
    sec = replay.state.find_section("8", "5")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4"]

    sub2 = irnode_to_text(subsections[1]).strip()
    sub3 = irnode_to_text(subsections[2]).strip()
    sub4 = irnode_to_text(subsections[3]).strip()

    assert sub2.startswith("Osakkeenomistajalla on oikeus tehdä kustannuksellaan")
    assert sub3.startswith("Edellä 1 momentissa tarkoitettuun muutostyöhön")
    assert "Edellä 2 momentissa tarkoitettuun muutostyöhön" in sub3
    assert sub4 == "Tämän pykälän säännöksiä sovelletaan myös osakkeenomistajan lisärakentamistyöhön yhtiön hallinnassa olevissa tiloissa."


def test_inspect_amendment_1996_1266_2012_963_recovers_section_30_replace_from_single_body_section() -> None:
    bundle = build_amendment_bundle("1996/1266", "2012/963", mode="official_consolidation")
    group30 = next(group for group in bundle["groups"] if group["target_norm"] == "30")

    assert group30["ops_raw"] == ["REPLACE 3 luku 30 §"]
    assert group30["ops_final"] == ["REPLACE 3 luku 30 §"]


def test_inspect_amendment_2016_591_2022_296_prunes_foreign_scoped_sections_from_new_chapter() -> None:
    bundle = build_amendment_bundle("2016/591", "2022/296", mode="official_consolidation")
    group3b = next(
        group
        for group in bundle["groups"]
        if group["target_unit_kind"] == "chapter" and group["target_norm"] == "3b"
    )
    group22a = next(group for group in bundle["groups"] if group["target_norm"] == "22a")
    group22b = next(group for group in bundle["groups"] if group["target_norm"] == "22b")

    assert group3b["ops_final"] == ["INSERT 3b luku"]
    assert group22a["ops_final"] == ["INSERT 4 luku 22a §"]
    assert group22b["ops_final"] == ["INSERT 4 luku 22b §"]
    assert any(
        observation["kind"] == "ELAB.CONTAINER_PRUNED_SHADOWED"
        and observation.get("detail", {}).get("pruned_sections") == ["22a", "22b"]
        for observation in group3b["elaboration_observations"]
    )
    assert "22 a §" not in group3b["normalized_payload"]["text"]
    assert "22 b §" not in group3b["normalized_payload"]["text"]


def test_replay_xml_1996_1266_updates_section_30_after_2012_963() -> None:
    replay = pinned_replay("1996/1266", mode="official_consolidation", quiet=True)
    sec = replay.materialized_state.find_section("30")

    assert sec is not None
    text = irnode_to_text(sec)
    assert "Tulli voi hakemuksesta antaa luvan" in text
    assert "Tullihallitus voi hakemuksesta antaa luvan" not in text


def test_inspect_amendment_1959_191_1992_203_keeps_following_targets_after_included_heading() -> None:
    bundle = build_amendment_bundle("1959/191", "1992/203", mode="official_consolidation")
    targets = {
        group["target_norm"]: group
        for group in bundle["groups"]
        if group["target_norm"] in {"50", "51a", "52a", "53", "54", "55", "56", "57"}
    }

    assert targets["50"]["ops_final"] == ["REPLACE 50 §"]
    assert targets["51a"]["ops_final"] == ["REPLACE 51a § 2 mom"]
    assert targets["52a"]["ops_final"] == ["REPLACE 52a §"]
    assert targets["53"]["ops_final"] == ["REPLACE 53 §"]
    assert targets["54"]["ops_final"] == ["REPLACE 54 §"]
    assert targets["55"]["ops_final"] == ["REPLACE 55 §"]
    assert targets["56"]["ops_final"] == ["REPLACE 56 § 1 mom"]
    assert targets["57"]["ops_final"] == ["REPLACE 57 §"]


def test_replay_xml_1959_191_updates_section_53_after_1992_203() -> None:
    replay = pinned_replay(
        "1959/191",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
        stop_before="1994/443",
    )
    sec = replay.materialized_state.find_section("53")

    assert sec is not None
    text = irnode_to_text(sec)
    assert "Ennen 44 ja 47 §:n 2 momentissa mainittuihin toimenpiteisiin" in text
    assert "Ennen 44 ja 45 §:ssä tarkoitetut" not in text




@pytest.mark.slow
def test_replay_xml_2013_588_restores_sections_21a_and_21b_from_2023_497() -> None:
    replay = pinned_replay("2013/588", mode="official_consolidation", quiet=True)

    sec21a = replay.materialized_state.find_section("21a", "4")
    sec21b = replay.materialized_state.find_section("21b", "4")

    assert sec21a is not None
    assert sec21b is not None
    assert [child.label for child in sec21a.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2"]
    assert [child.label for child in sec21b.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2", "3"]

    sec21a_text = " ".join(irnode_to_text(sec21a).split())
    sec21b_text = " ".join(irnode_to_text(sec21b).split())

    assert "Verkkoon pääsyn järjestäminen sähköjärjestelmässä" in sec21a_text
    assert "Verkkoon pääsyn täytäntöönpano sähköverkossa" in sec21b_text
    assert "toimitettava verkon käyttäjille, energiavaraston haltijoille ja asiakkaille tiedot" in sec21b_text
    assert "tehdä pyynnöstä tarjous liittyjälle sähköverkkoon liittämisestä" in sec21b_text
    assert "kieltäytyy liittämisestä taikka siirto- tai jakelupalvelusta" in sec21b_text


def test_inspect_amendment_2013_588_2019_108_keeps_section_87_subsection_replace_after_move_tail() -> None:
    bundle = build_amendment_bundle("2013/588", "2019/108", mode="official_consolidation")
    group11a = next(group for group in bundle["groups"] if group["target_unit_kind"] == "chapter" and group["target_norm"] == "11a")
    group87 = next(group for group in bundle["groups"] if group["target_norm"] == "87")

    assert group87["ops_final"] == ["REPLACE 13 luku 87 § 2 mom"]
    assert any(
        observation["kind"] == "ELAB.CONTAINER_PRUNED_SHADOWED"
        and "87" in observation.get("detail", {}).get("pruned_sections", [])
        for observation in group11a["elaboration_observations"]
    )


@pytest.mark.slow
def test_replay_xml_2013_588_does_not_keep_section_87_under_chapter_11a_after_2019_108(
    replay_2013_588_finlex_oracle: Any,
) -> None:
    materialized = extract_ir_sections(replay_2013_588_finlex_oracle.products.materialized_state.ir)

    assert "part:4/chapter:11a/section:87" not in materialized


def test_inspect_amendment_2013_588_2023_497_owns_sparse_higher_moment_binding_for_section_93() -> None:
    bundle = build_amendment_bundle("2013/588", "2023/497", mode="official_consolidation")
    group93 = next(group for group in bundle["groups"] if group["target_norm"] == "93")

    assert group93["ops_final"] == ["REPLACE 13 luku 93 § 4 mom"]
    assert group93["sparse_slot_bindings"][0]["slot_label"] == "4"
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group93["elaboration_observations"]
    )
    assert all(
        observation["kind"] != "ELAB.AMBIGUOUS_BINDING"
        for observation in group93["elaboration_observations"]
    )


@pytest.mark.slow
def test_replay_xml_2013_588_updates_section_93_subsection_4_after_2023_497(
    replay_2013_588_finlex_oracle: Any,
) -> None:
    sections = extract_ir_sections(replay_2013_588_finlex_oracle.products.materialized_state.ir)
    sec93 = sections["part:5/chapter:13/section:93"]
    sub4 = next(
        child for child in sec93.children if child.kind is IRNodeKind.SUBSECTION and child.label == "4"
    )
    text4 = " ".join(irnode_to_text(sub4).split())

    assert "onko loppukäyttäjällä oikeus irtisanoa sopimus" in text4
    assert "kuluttajan osalta aikaisintaan kuukauden ja muun loppukäyttäjän osalta aikaisintaan kahden viikon" in text4
    assert "Tämän momentin säännöksistä ei saa poiketa loppukäyttäjän vahingoksi." in text4
    assert "onko sopijapuolella oikeus irtisanoa sopimus" not in text4


@pytest.mark.slow
def test_replay_xml_2014_527_keeps_section_221c_subsection_2_after_2022_490() -> None:
    replay = pinned_replay("2014/527", mode="official_consolidation", quiet=True, build_full_products=False)
    sec221c = replay.materialized_state.find_section("221c", "20")

    assert sec221c is not None
    subsections = [child for child in sec221c.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]

    sub2 = next(child for child in subsections if child.label == "2")
    text2 = " ".join(irnode_to_text(sub2).split())

    assert "Edellä 1 momentissa tarkoitetun energiantuotantoyksikön ympäristöluvanvaraisuuteen" in text2
    assert "Eläimistä saatavista sivutuotteista annetussa laissa" in text2


def test_replay_xml_2005_579_preserves_section_9_structure_after_2013_1230_and_2014_751(
    replay_2005_579_finlex_oracle: Any,
) -> None:
    sec = replay_2005_579_finlex_oracle.find_section("9", chapter_num="1")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3"]

    sub1_text = " ".join(irnode_to_text(subsections[0]).split())
    sub2_text = " ".join(irnode_to_text(subsections[1]).split())
    sub3_text = " ".join(irnode_to_text(subsections[2]).split())
    sub2_labels = [child.label for child in subsections[1].children if child.kind is IRNodeKind.PARAGRAPH]
    sub3_labels = [child.label for child in subsections[2].children if child.kind is IRNodeKind.PARAGRAPH]

    assert "Valvonta-asioiden rekisteri voi sisältää tietoja" in sub1_text
    assert "Rekisteriin saadaan tallettaa henkilön henkilöllisyyttä koskevista tiedoista" in sub2_text
    assert sub2_labels == []
    assert sub3_labels == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert "rajavartiolain 31 §:ssä säädetyn tunnistamisen suorittamiseksi" in sub3_text


@pytest.mark.slow
def test_replay_xml_2003_549_keeps_section_149_subsection_4_as_wrapup_only(
    replay_2003_549_finlex_oracle: Any,
) -> None:
    """`149 § 4 momentti` must remain the wrap-up paragraph, not a duplicated item list."""
    sec = replay_2003_549_finlex_oracle.find_section("149", chapter_num="11")

    assert sec is not None
    sub4 = next(
        child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "4"
    )
    sub4_text = " ".join(irnode_to_text(sub4).split())

    assert "Tämän pykälän perusteella avatun teknisen käyttöyhteyden avulla" in sub4_text
    assert "1)" not in sub4_text


@pytest.mark.slow
def test_replay_xml_2003_549_applies_shifted_subsection_insert_for_section_53() -> None:
    """`2009/925` must preserve the shifted old 6 momentti as the new 7 momentti."""
    master = pinned_replay("2003/549", as_of="2010-01-02", mode="official_consolidation", quiet=True)
    sec = master.find_section("53", chapter_num="4")

    assert sec is not None
    sub5 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "5")
    sub6 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "6")
    sub7 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "7")

    sub5_text = " ".join(irnode_to_text(sub5).split())
    sub6_text = " ".join(irnode_to_text(sub6).split())
    sub7_text = " ".join(irnode_to_text(sub7).split())

    assert "1 047,22 euroa jokaiselta täydeltä kuukaudelta" in sub5_text
    assert "3-5 momentissa" in sub6_text or "3–5 momentissa" in sub6_text or "3―5 momentissa" in sub6_text
    assert "alle kolmivuotiaan lapsen hoitamisen vuoksi" in sub7_text


def test_replay_xml_1987_693_restores_inserted_sections_10d_and_10e_from_2002_1184() -> None:
    """`2002/1184` must not drop the long doc-level insert clause for `10 d-10 f §`.

    Real family: the clause parser previously collapsed
    `asetukseen [named heading] edelle uusi 10 b-10 f §, asetukseen uusi 21 a §,
    asetukseen uusi väliotsikko 25 §:n edelle, ...`
    to zero insert ops. Replay then missed `10 d §` and `10 e §` entirely.
    """
    master = pinned_replay("1987/693", mode="official_consolidation", quiet=True)

    sec10d = master.find_section("10d")
    sec10e = master.find_section("10e")

    assert sec10d is not None
    assert sec10e is not None

    sec10d_text = " ".join(irnode_to_text(sec10d).split())
    sec10e_text = " ".join(irnode_to_text(sec10e).split())

    assert "Samaa vaikuttavaa ainetta sisältäville" in sec10d_text
    assert "Erityislupa myönnetään enintään yhden vuoden hoitoa varten" in sec10e_text


def test_replay_xml_2005_579_preserves_section_39_sparse_omission_items_and_later_item_insert(
    replay_2005_579_finlex_oracle: Any,
) -> None:
    """`39 §` must preserve omitted sibling items and the later inserted `8 kohta`."""
    sec = replay_2005_579_finlex_oracle.find_section("39", chapter_num="4")

    assert sec is not None
    sub1 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    sub2 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")

    sub1_labels = [child.label for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH]
    sub2_labels = [child.label for child in sub2.children if child.kind is IRNodeKind.PARAGRAPH]
    sub1_text = " ".join(irnode_to_text(sub1).split())
    sub2_text = " ".join(irnode_to_text(sub2).split())

    assert sub1_labels == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert sub2_labels == ["1", "2", "3"]
    assert "Euroopan unionin jäsenvaltion rajavalvontaa" in sub1_text
    assert "Suomen ja Neuvostoliiton välisellä valtakunnanrajalla" in sub1_text
    assert "yksilöiden suojelusta henkilötietojen automaattisessa tietojenkäsittelyssä tehdyssä yleissopimuksessa" in sub1_text
    assert "rajatarkastuksia korvaavia toimenpiteitä" in sub1_text
    assert "valtion turvallisuuden varmistamiseksi" in sub2_text
    assert "sellaisen rikoksen ennalta estämiseksi tai selvittämiseksi" in sub2_text


def test_extract_temporary_targets_whole_amendment_when_all_ambiguous() -> None:
    """When all väliaikaisesti occurrences yield no valid section labels (statute
    name between adverb and §), the function must still return None (whole-amendment).
    """
    from lawvm.finland.frontend_compile import _extract_temporary_targets_from_johtolause

    # Two occurrences, both with statute names before §
    johto = (
        "muutetaan väliaikaisesti tartuntatautilain 5 § ja "
        "muutetaan väliaikaisesti sosiaalihuoltolain 3 § seuraavasti:"
    )
    result = _extract_temporary_targets_from_johtolause(johto)
    assert result is None, "Statute-name-prefixed occurrences should fall back to whole-amendment"


# ---------------------------------------------------------------------------
# Regression tests: voimaantulosäännös sekä-pattern (2021/147 pattern)
# ---------------------------------------------------------------------------


def test_temporary_section_expiry_override_seka_subsection_pattern() -> None:
    """_temporary_section_expiry_override must handle the 'sekä N §:n M momentti'
    pattern in the voimaantulosäännös.

    Pattern from 2021/147 voimaantulosäännös:
    'Lain 58 c–58 h ja 59 a–59 e § sekä 91 §:n 1 momentti ovat voimassa
     30 päivään kesäkuuta 2021.'

    Previously the regex required '§ ovat voimassa' immediately — the intervening
    'sekä 91 §:n 1 momentti' caused a miss.  As a result, sections 58c–59e and
    §91 did not get an expiry date from the voimaantulosäännös.
    """
    from lxml import etree
    from lawvm.finland.metadata import _temporary_section_expiry_override
    import datetime as dt

    xml_text = """<act>
  <body>
    <section><num>58 c §</num><content><p>Content</p></content></section>
    <section><num>91 §</num><content><p>Content</p></content></section>
  </body>
  <conclusions>
    <hcontainer name="commencement">
      <content>
        <p>Tämä laki tulee voimaan 22 päivänä helmikuuta 2021.
           Lain 58 c–58 h ja 59 a–59 e § sekä 91 §:n 1 momentti ovat voimassa
           30 päivään kesäkuuta 2021.</p>
      </content>
    </hcontainer>
  </conclusions>
</act>"""
    tree = etree.fromstring(xml_text.encode())
    result = _temporary_section_expiry_override(tree, "2021/147")

    assert result is not None, "Should extract section-scoped expiry from sekä-pattern"
    assert result.expiry == dt.date(2021, 6, 30), f"Expected 2021-06-30, got {result.expiry}"
    # Primary group: 58c–58h range and 59a–59e range
    for sec in ["58c", "58d", "58e", "58f", "58g", "58h", "59a", "59b", "59c", "59d", "59e"]:
        assert sec in result.labels, f"§{sec} should be in expiry labels"
    # Secondary group: §91 from the 'sekä 91 §:n 1 momentti' clause
    assert "91" in result.labels, "§91 from sekä-clause should be in expiry labels"


def test_temporary_section_expiry_override_simple_pattern_unchanged() -> None:
    """Simple '§ ovat voimassa' pattern must still work after regex change."""
    from lxml import etree
    from lawvm.finland.metadata import _temporary_section_expiry_override
    import datetime as dt

    xml_text = """<act>
  <conclusions>
    <hcontainer name="commencement">
      <content>
        <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2021.
           Lain 16 a–16 g § ovat voimassa 31 päivään joulukuuta 2021.</p>
      </content>
    </hcontainer>
  </conclusions>
</act>"""
    tree = etree.fromstring(xml_text.encode())
    result = _temporary_section_expiry_override(tree, "2021/701")

    assert result is not None, "Simple pattern should still match"
    assert result.expiry == dt.date(2021, 12, 31)
    for sec in ["16a", "16b", "16c", "16d", "16e", "16f", "16g"]:
        assert sec in result.labels, f"§{sec} should be in expiry labels"


def test_temporary_section_expiry_override_bounded_interval_pattern() -> None:
    """Scoped expiry may state both start and end dates in the same voimassa clause."""
    from lxml import etree
    from lawvm.finland.metadata import _temporary_section_expiry_override
    import datetime as dt

    xml_text = """<act>
  <conclusions>
    <hcontainer name="entryIntoForce">
      <content>
        <p>Tämä asetus tulee voimaan 15 päivänä helmikuuta 2007, ja sen
           5 a-5 c § ovat voimassa 1 päivästä maaliskuuta 31 päivään
           toukokuuta 2007.</p>
      </content>
    </hcontainer>
  </conclusions>
</act>"""
    tree = etree.fromstring(xml_text.encode())
    result = _temporary_section_expiry_override(tree, "2007/158")

    assert result is not None
    assert result.target_mid == "2007/158"
    assert result.labels == {"5a", "5b", "5c"}
    assert result.expiry == dt.date(2007, 5, 31)


def test_temporary_section_expiry_override_ignores_self_scoped_body_text() -> None:
    """Body payload expiry wording is law text, not the amending act's own expiry."""
    from lxml import etree
    from lawvm.finland.metadata import _temporary_section_expiry_override

    xml_text = """<act>
  <body>
    <section>
      <num>61 a §</num>
      <content>
        <p>Lain 61 a § on voimassa 31 päivään joulukuuta 1993.</p>
      </content>
    </section>
  </body>
  <conclusions>
    <hcontainer name="entryIntoForce">
      <content><p>Tämä laki tulee voimaan 1 päivänä tammikuuta 1992.</p></content>
    </hcontainer>
  </conclusions>
</act>"""
    tree = etree.fromstring(xml_text.encode())

    assert _temporary_section_expiry_override(tree, "1991/1673") is None


# ---------------------------------------------------------------------------
# Part-hint routing tests (2003/1274 → 1993/1054 pattern)
# ---------------------------------------------------------------------------


def test_find_chapter_insert_parent_path_uses_part_hint() -> None:
    """part_hint overrides positional heuristic for letter-suffix chapters.

    Regression test for: amendment body wraps chapter "17a" inside
    <part><num>IV OSA</num> but the preceding chapter "17" is in part:3.
    Without the hint, the heuristic would route 17a into part:3.
    With the hint "4", it must route into part:4.
    """
    from lawvm.finland.apply_runtime_support import _find_chapter_insert_parent_path
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    def _ch(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label)

    def _part(label: str, *chapters: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))

    # Statute with parts 1–4, chapter 17 is in part:3, chapters 18–19 in part:4
    master = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            _part("1", _ch("1"), _ch("2")),
            _part("2", _ch("5"), _ch("6")),
            _part("3", _ch("15"), _ch("16"), _ch("17")),
            _part("4", _ch("18"), _ch("19")),
        ),
    )

    # Without hint: positional heuristic picks part:3 (chapter 17 < 17a)
    path_no_hint = _find_chapter_insert_parent_path(master, "17a")
    assert path_no_hint[-1] == ("part", "3"), (
        f"without hint should go to part:3 (has ch17), got {path_no_hint}"
    )

    # With hint "4": must route to part:4
    path_with_hint = _find_chapter_insert_parent_path(master, "17a", part_hint="4")
    assert path_with_hint[-1] == ("part", "4"), (
        f"with hint '4' should go to part:4, got {path_with_hint}"
    )


def test_find_chapter_insert_parent_path_normalizes_roman_suffix_part_hint() -> None:
    """Source-surface IV A OSA must route to canonical part:4a."""
    from lawvm.finland.apply_runtime_support import _find_chapter_insert_parent_path
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    def _ch(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label)

    def _part(label: str, *chapters: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))

    master = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            _part("3", _ch("17")),
            _part("4", _ch("18")),
            _part("4a", _ch("19a")),
            _part("5", _ch("20")),
        ),
    )

    path = _find_chapter_insert_parent_path(master, "19b", part_hint="IV A OSA")
    assert path[-1] == ("part", "4a")


def test_find_chapter_insert_parent_path_hint_nonexistent_part_falls_through() -> None:
    """If hint names a part that doesn't exist, fall through to heuristic."""
    from lawvm.finland.apply_runtime_support import _find_chapter_insert_parent_path
    from lawvm.core.ir import IRNode
    from lawvm.core.semantic_types import IRNodeKind

    def _ch(label: str) -> IRNode:
        return IRNode(kind=IRNodeKind.CHAPTER, label=label)

    def _part(label: str, *chapters: IRNode) -> IRNode:
        return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))

    master = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            _part("1", _ch("1")),
            _part("2", _ch("5")),
            _part("3", _ch("14"), _ch("17")),
            _part("4", _ch("18")),
        ),
    )

    # Canonicalized hint "4a" doesn't exist in master — fall through to heuristic.
    path = _find_chapter_insert_parent_path(master, "17a", part_hint="iva")
    # Heuristic picks part:3 (ch17 < 17a)
    assert path[-1] == ("part", "3"), (
        f"nonexistent hint should fall through to heuristic (part:3), got {path}"
    )


def test_replay_xml_2004_137_restores_section_4_split_moments_from_2017_367() -> None:
    result = pinned_replay("2004/137", mode="official_consolidation", quiet=True)

    sec4 = result.find_section("4")
    assert sec4 is not None

    subs = [c for c in sec4.children if c.kind == IRNodeKind.SUBSECTION]
    assert len(subs) == 5
    assert irnode_to_text(subs[1]) == (
        "Oikeusrekisterikeskuksen on merkittävä rekisteriin päivämäärä ja kellonaika, "
        "jolloin 1 momentin 1 kohdassa tarkoitetut tiedot näkyvät rekisterissä."
    )
    third_text = " ".join(irnode_to_text(subs[2]).split())
    assert "valvontakirjelmät vastaanottavan pesänhoitajan nimi ja yhteystiedot;" in third_text
    assert third_text.startswith(
        "Oikeusrekisterikeskuksen on merkittävä pesänhoitajan ilmoituksen perusteella rekisteriin:"
    )


def test_replay_xml_2016_1503_preserves_section_4_first_moment_tail_once_after_2018_541() -> None:
    result = pinned_replay("2016/1503", mode="official_consolidation", quiet=True)

    sec4 = result.find_section("4")
    assert sec4 is not None

    subs = [c for c in sec4.children if c.kind == IRNodeKind.SUBSECTION]
    assert [c.label for c in subs] == ["1", "2", "3", "4", "5"]

    first_text = " ".join(irnode_to_text(subs[0]).split())
    duplicated_tail = "Maksu voidaan periä enintään yhdeltätoista kalenterikuukaudelta toimintavuoden aikana."
    assert "päiväkotitoimintana ja perhepäivähoitona" in first_text
    assert first_text.count(duplicated_tail) == 1


def test_replay_xml_2007_1024_section_2_no_spurious_third_subsection_after_2022_525() -> None:
    """Regression: 2022/525 item-INSERT into section:2 subsection:2 must not create a
    spurious subsection:3.  The amendment XML carries the full updated subsection:2 content
    (OMISSION + SUBSECTION, no trailing omission) — the johtolause parser failed to extract
    target_item, so the op only carries target_paragraph=2.  The in-place merge path must
    replace subsection:2 in-place, not push it to subsection:3."""
    replay = pinned_replay("2007/1024", as_of="2024-07-02", mode="official_consolidation", quiet=True)
    sec2 = replay.find_section("2")
    assert sec2 is not None

    subs = [c for c in sec2.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subs] == ["1", "2"], (
        f"Expected exactly 2 subsections ['1','2'], got {[s.label for s in subs]!r}"
    )
    sub2_text = irnode_to_text(subs[1])
    assert "Ministeriön toimialaan kuuluvat myös seuraavia valtionyhtiöitä koskevat asiat" in sub2_text
    assert "Finnvera Oyj" in sub2_text
    assert "Työkanava Oy" in sub2_text


def test_replay_xml_1995_509_same_wave_section_relabel_insert_keeps_vacated_label() -> None:
    replay = pinned_replay("1995/509", mode="official_consolidation", quiet=True)

    sec24e = replay.find_section("24e", "6")
    sec24f = replay.find_section("24f", "6")

    assert sec24e is not None
    assert sec24f is not None
    assert "Schengenin tietojärjestelmän keskustietokannasta" in irnode_to_text(sec24e)
    assert "Tietojen poistaminen muista pysyvistä henkilörekistereistä" in irnode_to_text(sec24f)
    assert any(
        event.kind == "renumber"
        and str(event.from_address) == "chapter:6/section:24e"
        and str(event.to_address) == "chapter:6/section:24f"
        and event.effective == "1998-08-21"
        for event in replay.migration_events
    )


def test_replay_xml_2007_1024_section_3_restored_after_2020_818(
    replay_2007_1024_finlex_oracle: Any,
) -> None:
    """Regression: 2020/818 johtolause contained a U+200D zero-width joiner in '3‌ §:n'
    which caused the PEG parser to fail to detect the REPLACE op for section:3 subsection:1.
    After fixing Cf-character stripping in metadata normalisation, section:3 should have 3
    subsections with the correct content."""
    sec3 = replay_2007_1024_finlex_oracle.find_section("3")
    assert sec3 is not None

    subs = [c for c in sec3.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subs] == ["1", "2", "3"], (
        f"Expected subsections ['1','2','3'], got {[s.label for s in subs]!r}"
    )
    sub3_text = irnode_to_text(subs[2])
    assert "Osaston ja toimintayksiköiden sisäisestä organisaatiosta" in sub3_text

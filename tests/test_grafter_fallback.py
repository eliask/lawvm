import datetime as dt
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from lxml import etree

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.compile_result import SourcePathology
from lawvm.core.compile_result import StrictProfile
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.coverage import CoverageClaim, CoverageGap, CoverageReport, CoverageUnit
from lawvm.core.canonical_intent import ExecutionContract, IntentKind, Move, NodeTarget, OccupancyPolicy, Relabel
from lawvm.core.elaboration_context import (
    build_payload_elaboration_context,
    snapshot_replay_lookups,
    snapshot_target_context,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.corpus_store import CorpusStore
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.johtolause.compat import parse_clause, derive_features
from lawvm.finland.kumotaan import (
    _extract_kumotaan_container_refs,
    _extract_kumotaan_chapter_section_map,
    _extract_muutetaan_section_refs,
    _extract_muutetaan_chapter_section_map,
)
from lawvm.finland.ops import _build_canonical_intent
from lawvm.finland.ops import _lo_with_path_update
from lawvm.finland.ops import get_replay_profile
from lawvm.finland.ops import ScopeConfidence
from lawvm.finland.payload_normalize import (
    _container_pruning_is_expected_heading_only,
    _prune_container_payload_sections_shadowed_by_standalone_targets as _prune_container_payload_sections_shadowed_by_standalone_targets_impl,
)
from lawvm.finland.grafter_uncovered import _collect_johto_mentioned_section_labels
from lawvm.finland.grafter import (
    AmendmentOp,
    FailedOp,
    IRNode,
    RepealTargetRef,
    ResolvedOp,
    _apply_uncovered_kumotaan,
    _allow_unscoped_live_section_retarget,
    _pre_create_amendment_chapters,
    _recover_uncovered_body_ops,
    _assign_chapter_scope_from_johtolause,
    apply_ops_to_tree,
    _build_group_surface,
    _coalesce_same_target_mixed_scope_section_groups,
    _chapter_chunks_from_johtolause,
    _compile_group,
    _dedupe_fallback_ops_ir,
    _build_standalone_section_targets,
    _elaborate_group,
    _apply_mutation_boundary_violation_finding,
    _serialize_apply_mutation_event,
    _duplicate_frontend_target_observations,
    _drop_payloadless_source_replace_shadowed_by_same_group_relabel,
    _oracle_version_future_repeal_only_uses_cutoff_date,
    _scope_anchor_dependence_observations,
    _semantic_collapse_move_or_renumber_observations,
    _extract_root_replace_ops_from_body_fallback,
    _find_amend_paragraph,
    _find_muutos_ir,
    _pre_scan_repeal_targets,
    _prune_container_payload_sections_shadowed_by_standalone_targets,
    _retarget_duplicate_body_section_scope_from_close_live_siblings,
    _strip_unjustified_chapter_scope_from_unique_sections,
    _extract_root_insert_ops_fallback,
    _extract_insert_subsection_ops_fallback,
    _extract_kumotaan_section_refs,
    _group_shadow_pruning_section_targets,
    _group_shadow_pruning_foreign_scoped_section_targets,
    _has_single_intro_numbered_item_list_ir,
    _is_suspicious_partial_section_replace_ir,
    _merge_section_with_omission_ir,
    _merge_sparse_alakohta_insert_ir,
    _merge_sparse_alakohta_replace_ir,
    _merge_letter_item_into_content_only_subsection_ir,
    _merge_letter_item_from_content_subsection_ir,
    _stabilize_insert_order,
    _restrict_sec1_fallback_to_parent,
    _resolved_op_is_owned_by_restructure_plan,
    _rewrite_kumotaan_snapshot_replaces_to_repeal,
    _rewrite_later_effective_lo_groups,
    _rewrite_compiled_op_activation_rule_effective_for_addresses,
    _rewrite_lo_op_source_effective,
    _snapshot_op_source,
    _supplement_named_table_row_mixed_clause_ops,
    _tag_named_table_row_single_clause_ops,
    _supplement_missing_repeals_after_item_shift_clause,
    _tag_explicit_item_shift_after_repeal_hints,
    _extract_replace_ops_from_muutetaan_tail,
    extract_johtolause_legal_ops,
    get_corpus,
    get_johtolause,
    parse_ops_fallback_heuristic,
    process_muutoslaki,
    replay_xml,
)
from lawvm.tools.section_keys import extract_ir_sections
from lawvm.finland.frontend_compile import (
    _attach_target_version_selectors,
    _ambiguous_unscoped_additive_fallback_insert_observation,
    _reject_overbroad_section_repeals_for_deep_targets,
    _restore_heading_facet_for_mixed_scope_section_replaces,
    _enrich_ops_from_amendment_tree,
    _retarget_stale_body_scope_for_section_op,
    _extract_enacting_formula_body_replace_ops_fallback,
)
from lawvm.finland.fallback_op_ids import stamp_fallback_op_ids
from lawvm.finland.grafter_uncovered import (
    _uncovered_body_recovery_finding,
    _uncovered_body_recovery_skipped_finding,
)
from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.apply import apply_op
from lawvm.finland.constraints import _find_muutos_node
from lawvm.finland.group_ops import append_compiled_group_ops, normalize_group_ops_for_repeal_reenact
from lawvm.finland.normalize import (
    _extract_insert_section_ops_fallback,
    _merge_missing_insert_supplements,
    _merge_missing_replace_supplements,
    _merge_root_insert_supplements,
)
from lawvm.finland.scope import assign_scope_from_renumber_destinations
from lawvm.finland.source_pathology import build_container_replace_target_absent_pathology
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.tools.inspect_amendment import build_amendment_bundle
from lawvm.tools.trace_section import build_trace_bundle

LEGACY_MOVE_CLAUSE_RESIDUE = pytest.mark.skip(
    reason="Legacy move-clause bridge residue; core keeps move-tail state out of shared carriers.",
)


class _MapCorpus:
    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def read_source(self, statute_id: str) -> bytes | None:
        return self._mapping.get(statute_id)

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


def test_uncovered_body_recovery_finding_is_native_obligation() -> None:
    finding = _uncovered_body_recovery_finding(
        op_id="uncovered_insert_14",
        source_statute="2001/1529",
        target_unit_kind="section",
        target_norm="14",
        target_chapter="5",
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
        list,
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
    assert result.findings() == ()
    assert len(preseeded_pathologies) == 1
    assert len(preseeded_failed_ops) == 1


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


def test_process_muutoslaki_flags_missing_temporal_coverage(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(
        *_args,
        **_kwargs,
    ) -> PhaseResult:
        return PhaseResult(
            output=(SimpleNamespace(resolved_source_statute="1996/1260"),),
            temporal_events=(),
        )

    def fake_apply_ops_to_tree(*_args, **_kwargs):
        return state

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)
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


def test_process_muutoslaki_does_not_flag_when_temporal_coverage_matches(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(
        *_args,
        **_kwargs,
    ) -> PhaseResult:
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

    def fake_apply_ops_to_tree(*_args, **_kwargs):
        return state

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)
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
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = _statute_context(state.ir)
    skipped_op = AmendmentOp(
        op_id="replace_ch_7",
        op_type="REPLACE",
        target_section="7",
        target_unit_kind="chapter",
        source_statute="1996/1261",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[skipped_op])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree(*_args, **_kwargs):
        return state

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)

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
    assert seed_skip[0].detail.get("dropped_count") == 1
    assert seed_skip[0].detail.get("seeded_chapters") == ["7"]
    assert seed_skip[0].detail.get("dropped_ops") == [skipped_op.description()]


def test_process_muutoslaki_observes_sec1_pre_routing_fallback(monkeypatch) -> None:
    state = _replay_state(IRNode(kind=IRNodeKind.BODY))
    ctx = StatuteContext(
        id="1958/370",
        title="Rakennuslaki",
        base_ir=state.ir,
        base_xml_bytes=b"",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree(*_args, **_kwargs):
        return state

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)

    result = process_muutoslaki(
        "1993/949",
        state,
        ctx,
        corpus=_corpus_store({"1993/949": _sec1_fallback_process_muutoslaki_xml()}),
        parent_id="1958/370",
    )

    findings = result.findings()
    sec1 = [finding for finding in findings if finding.kind == "ELAB.SEC1_PRE_ROUTING_FALLBACK"]
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
            op_type="REPLACE",
            target_section="7",
            target_unit_kind="section",
            source_statute="1996/1261",
            uncovered_body_recovery=True,
        ),
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="7"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="7",
        target_chapter=None,
    )
    phase2_op = AmendmentOp(
        op_id="phase2_replace_7",
        op_type="REPLACE",
        target_section="7",
        target_unit_kind="section",
        source_statute="1996/1261",
    )

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[phase2_op])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_recover_uncovered_body_ops(*_args, **_kwargs):
        return [recovered_rop]

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

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter._recover_uncovered_body_ops", fake_recover_uncovered_body_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_op", fake_apply_op)

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

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree(*args, **kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

        mutation_events_out = kwargs.get("mutation_events_out")
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
        return kwargs.get("state", args[0] if args else state)

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)

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

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree(*args, **kwargs):
        mutation_events_out = kwargs.get("mutation_events_out")
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
        return kwargs.get("state", args[0] if args else state)

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)

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

    def fake_normalize_and_compile_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=[])

    def fake_compile_amendment_ops(*_args, **_kwargs) -> PhaseResult:
        return PhaseResult(output=(), temporal_events=())

    def fake_apply_ops_to_tree(*args, **kwargs):
        mutation_events_out = kwargs.get("mutation_events_out")
        source_pathologies_out = kwargs.get("source_pathologies_out")
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
        return kwargs.get("state", args[0] if args else state)

    monkeypatch.setattr("lawvm.finland.grafter.normalize_and_compile_ops", fake_normalize_and_compile_ops)
    monkeypatch.setattr("lawvm.finland.grafter.compile_amendment_ops", fake_compile_amendment_ops)
    monkeypatch.setattr("lawvm.finland.grafter.apply_ops_to_tree", fake_apply_ops_to_tree)

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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, mutation_events_out=None, **_kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

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
        return state

    monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
    monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)

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
    assert replay_boundary_findings[0].detail.get("declared_recovery_rule_ids") == []


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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, mutation_events_out=None, **_kwargs):
        from lawvm.finland.apply_events import ApplyMutationEvent

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
        return state

    monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
    monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)

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
    assert replay_boundary_findings[0].detail == {
        "message": "Apply mutation boundary accounting violated.",
        "violation": "REPLAY_SKIPPED_OP_MUTATED_TREE op_id=skipped_tree_touch helper=apply_op touched=1",
        "barrier_code": "REPLAY_SKIPPED_OP_MUTATED_TREE",
    }


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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)
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
    assert findings[0].detail.get("path") == ["body:?", "section:17", "subsection:1", "paragraph:2"]
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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)
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
    assert recovered[0].detail.get("path") == ["body:?", "section:5", "subsection:1"]
    assert recovered[0].role == "observation"
    assert mismatch[0].detail.get("basis") == "profile_invalid"
    assert mismatch[0].detail.get("path") == ["body:?", "section:6", "subsection:1"]
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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)
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
    assert by_kind["BASE_CROSS_HEADING_HOIST"].detail.get("path") == ["body:?", "chapter:2"]
    assert by_kind["BASE_DUPLICATE_SIBLING_DROP"].detail.get("path") == ["body:?", "section:?"]
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
    )

    def fake_prepare_replay_plan(*_args, **_kwargs):
        return plan

    def fake_execute_replay_plan(*_args, **_kwargs):
        return state

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr("lawvm.finland.grafter.prepare_replay_plan", fake_prepare_replay_plan)
        monkeypatch.setattr("lawvm.finland.grafter.execute_replay_plan", fake_execute_replay_plan)
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
    assert editorial[0].detail.get("path") == ["body:?", "section:4", "content:?"]
    assert editorial[0].role == "observation"
    assert numbering[0].detail.get("basis") == "monotonic_local_repair"
    assert numbering[0].detail.get("path") == ["body:?", "section:8"]
    assert numbering[0].role == "observation"
    assert digit_reset[0].detail.get("basis") == "monotonic_local_repair"
    assert digit_reset[0].detail.get("path") == ["body:?", "section:9", "subsection:1", "paragraph:4"]
    assert digit_reset[0].role == "observation"
    assert duplicate_tail[0].detail.get("basis") == "monotonic_local_repair"
    assert duplicate_tail[0].detail.get("path") == ["body:?", "section:11", "subsection:3"]
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
        op_type="INSERT",
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_paragraph=4,
        scope_provenance_tags=("chapter_scope_carry_forward", "grouped_part_scope"),
        source_statute="2019/371",
    )

    result = _build_group_surface([op], root, "section", "159", "2", "III")

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
        op_type="INSERT",
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_paragraph=4,
        scope_provenance_tags=("grouped_part_scope",),
        source_statute="2019/371",
    )

    result = _build_group_surface([op], root, "section", "159", "2", "III")

    assert result.output.body_ir is None


def test_allow_unscoped_live_section_retarget_requires_carry_forward_scope() -> None:
    explicit_scoped = AmendmentOp(
        op_id="replace_159",
        op_type="REPLACE",
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_from_johtolause",),
        source_statute="2019/371",
    )
    carry_forward_scoped = AmendmentOp(
        op_id="replace_159_cf",
        op_type="REPLACE",
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_carry_forward",),
        source_statute="2019/371",
    )
    carry_forward_tag_with_explicit_carrier = AmendmentOp(
        op_id="replace_159_cf_explicit",
        op_type="REPLACE",
        target_section="159",
        target_unit_kind="section",
        target_chapter="2",
        scope_provenance_tags=("chapter_scope_carry_forward",),
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        op_type="REPLACE",
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
        op_type="REPLACE",
        target_section="18",
        target_unit_kind="section",
        target_chapter="3",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
            op_type="REPLACE",
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
            op_type="INSERT",
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
        get_replay_profile("finlex_oracle"),
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
            op_type="REPLACE",
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
            op_type="INSERT",
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
        get_replay_profile("finlex_oracle"),
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
        op_type="INSERT",
        target_section="8a",
        target_unit_kind="section",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        op_type="REPLACE",
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
        op_type="REPLACE",
        target_section="15",
        target_unit_kind="section",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_stripped_unique_section",
            source="explicit_scope_rewrite",
            confidence="rewritten",
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
        op_type="REPLACE",
        target_section="84",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        op_type="REPLACE",
        target_section="93",
        target_unit_kind="section",
        target_part="3",
        target_paragraph=4,
        scope_confidence=ScopeConfidence(
            tag="part_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        op_type="REPLACE",
        target_section="84",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        op_type="REPLACE",
        target_section="75e",
        target_unit_kind="section",
        target_part="3",
        target_chapter="7",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
    assert result.output[0].op.target_section == "75e"


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
        op_type="RENUMBER",
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
        op_type="REPLACE",
        target_section="5",
        target_unit_kind="section",
        target_chapter="2",
        target_part="III",
        target_special="otsikko",
        source_statute="2019/371",
    )

    result = _build_group_surface([renumber, heading_replace], root, "section", "5", "2", "III")

    assert result.output.body_ir is not None
    assert result.output.body_ir.kind is IRNodeKind.SECTION
    assert result.output.body_ir.label == "159"


def test_elaborate_group_phase1_constraint_filter_records_rejected_op_obligation() -> None:
    muutos_tree = etree.fromstring('<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" />')
    op = AmendmentOp(
        op_id="replace_5",
        op_type="REPLACE",
        target_section="5",
        target_unit_kind="section",
        source_statute="2099/1",
    )
    group_surface_result = _build_group_surface([op], muutos_tree, "section", "5", None, None)
    group_surface = group_surface_result.output
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    lookups = snapshot_replay_lookups(state)
    result = _elaborate_group(
        snapshot_target_context(state, "section", "5", None, lookups),
        lookups,
        group_surface,
        [op],
        set(),
        foreign_scoped_standalone_section_targets=set(),
        target_part=None,
        muutos_tree=muutos_tree,
        johto="ruotsinkielinen sanamuoto",
        profile=get_replay_profile("legal_pit"),
        strict_profile=None,
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
    assert got is not None
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
    assert got is not None
    assert pruned == ["60b"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["59a", "59b"]


def test_container_pruning_heading_only_accepts_plain_container_replace_group() -> None:
    assert _container_pruning_is_expected_heading_only(
        [
            AmendmentOp(
                op_type="REPLACE",
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
    assert got is not None
    assert pruned == ["20a", "20h"]
    assert [c.label for c in got.children if c.kind is IRNodeKind.SECTION] == ["19j"]


def test_prune_container_payload_sections_keeps_foreign_scoped_shadow_in_new_chapter() -> None:
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

    assert changed is False
    assert got is muutos_ir
    assert pruned == []


def test_group_shadow_pruning_foreign_scoped_section_targets_ignores_foreign_replaces() -> None:
    chapter_insert = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="3a",
    )
    foreign_replace_20 = AmendmentOp(
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="20",
        target_chapter="4",
    )
    foreign_replace_21 = AmendmentOp(
        op_type="REPLACE",
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


def test_group_shadow_pruning_foreign_scoped_section_targets_keeps_foreign_inserts() -> None:
    chapter_insert = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="5c",
    )
    foreign_insert_20a = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="section",
        target_section="20a",
        target_chapter="6",
    )
    foreign_insert_20h = AmendmentOp(
        op_type="INSERT",
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


def test_group_shadow_pruning_foreign_scoped_section_targets_ignores_carry_forward_inserts() -> None:
    chapter_insert = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="chapter",
        target_section="3a",
    )
    foreign_insert_16a = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="section",
        target_section="16a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )
    foreign_insert_16b = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="section",
        target_section="16b",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="1",
        target_chapter="11a",
    )
    subsection_insert = AmendmentOp(
        op_type="INSERT",
        target_unit_kind="section",
        target_section="1",
        target_chapter=None,
        target_paragraph=5,
    )

    got = _build_standalone_section_targets([section_insert, subsection_insert])

    assert got == frozenset({(None, "11a", "1")})


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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="1a",
        target_chapter="25",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
            resolved_chapter="25",
        ),
    )

    got = _retarget_stale_body_scope_for_section_op(
        op=op,
        muutos_tree=muutos_tree,
        master=master,
    )

    assert got is None


def test_compile_group_keeps_explicit_chunk_insert_under_matching_body_chapter(
    monkeypatch,
) -> None:
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
        op_type="INSERT",
        target_section="2a",
        target_unit_kind="section",
        target_chapter="2",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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

    monkeypatch.setattr(
        "lawvm.finland.grafter._retarget_duplicate_body_section_scope_from_close_live_siblings",
        lambda **_kwargs: (None, "1"),
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
        op_type="INSERT",
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
        "lawvm.finland.grafter._retarget_duplicate_body_section_scope_from_close_live_siblings",
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
        op_type="INSERT",
        target_section="3a",
        target_unit_kind="section",
        target_chapter="6",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
        "lawvm.finland.grafter._source_body_chapter_for_scoped_section_target",
        lambda **_kwargs: "6",
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter._find_body_section_chapter",
        lambda *_args, **_kwargs: "2",
    )
    monkeypatch.setattr(
        "lawvm.finland.grafter._retarget_duplicate_body_section_scope_from_close_live_siblings",
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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="16a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
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
        "lawvm.finland.grafter._retarget_duplicate_body_section_scope_from_close_live_siblings",
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
    assert rop.op.target_chapter == "5"
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
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="29e",
        target_chapter="5b",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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
    master = pinned_replay("1994/1472", mode="finlex_oracle", stop_before="2019/1554")

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
        op_type="REPLACE",
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
    )
    renumber_op = AmendmentOp(
        op_id="renumber_73_61",
        op_type="RENUMBER",
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
        op_type="REPLACE",
        target_section="73",
        target_unit_kind="section",
        target_chapter="7",
        source_statute="1994/318",
    )
    renumber_op = AmendmentOp(
        op_id="renumber_73_61",
        op_type="RENUMBER",
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


def test_build_amendment_bundle_keeps_scoped_move_targets_as_section_groups() -> None:
    """Scoped move-tail section targets must stay chapter-scoped after PEG migration.

    The old xfail expected a specific container-pruning count and observation.
    That shape is no longer stable or necessary. The real invariant is that the
    moved section targets continue to materialize as separate chapter-scoped
    section groups instead of being lost inside the chapter container payload.
    """
    bundle = build_amendment_bundle("2010/182", "2020/766", "legal_pit")
    chapter5 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "chapter" and g["target_norm"] == "5")
    sec33 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "section" and g["target_norm"] == "33")
    sec34 = next(g for g in bundle["groups"] if g["target_unit_kind"] == "section" and g["target_norm"] == "34")

    assert str(chapter5["normalized_payload"]["kind"].value) == "chapter"
    assert sec33["target_chapter"] == "5"
    assert sec34["target_chapter"] == "5"


def test_build_amendment_bundle_keeps_post_move_clause_trailing_replace_targets() -> None:
    try:
        bundle = build_amendment_bundle("2010/182", "2020/766", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

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


def test_build_amendment_bundle_salvages_malformed_chapter_insert_surface() -> None:
    try:
        bundle = build_amendment_bundle("2014/917", "2020/1207", "legal_pit")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"Finlex archive unavailable in this environment: {exc}")

    compiled = set(bundle["compiled_ops"])

    assert "INSERT 7a luku" in compiled
    assert "INSERT 9 luku 60 § 3 mom" in compiled
    assert "INSERT 10 luku 81a §" in compiled
    assert "INSERT 10 luku 81b §" in compiled
    assert "INSERT 10 luku 81c §" in compiled
    assert "INSERT 12 luku 91a §" in compiled
    assert "INSERT 15 luku 113 §" in compiled
    assert "INSERT 26a luku" in compiled
    assert "INSERT 29 luku 244a §" in compiled
    assert "INSERT 29 luku 244b §" in compiled
    assert "INSERT 37 luku 301a §" in compiled
    assert "INSERT 38 luku 304 § 1 mom 14 kohta" in compiled
    assert "INSERT 38 luku 304 § 1 mom 17 kohta" in compiled


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


def test_build_amendment_bundle_folds_terminal_continuation_subsection_for_2018_441() -> None:
    bundle = build_amendment_bundle("2010/1396", "2018/441", "legal_pit")

    group48 = next(group for group in bundle["groups"] if group["target_norm"] == "48")

    assert group48["subsection_map"][0]["op"] == "REPLACE 48 § otsikko"
    assert group48["subsection_map"][0]["mapped_payload"] is None
    assert group48["subsection_map"][1]["op"] == "REPLACE 48 § 1 mom"
    assert group48["subsection_map"][1]["mapped_payload"]["label"] == "1"
    assert group48["sparse_slot_bindings"] == [
        {
            "op": "REPLACE 48 § 1 mom",
            "slot_index": 1,
            "slot_label": "1",
            "target_paragraph": 1,
            "target_item": "",
            "target_special": "",
        }
    ]


def test_build_amendment_bundle_splits_fused_restarted_subsection_for_2018_441() -> None:
    bundle = build_amendment_bundle("2010/1396", "2018/441", "legal_pit")

    group51 = next(group for group in bundle["groups"] if group["target_norm"] == "51")

    assert set(group51["ops_final"]) == {"REPLACE 51 § 1 mom", "REPLACE 51 § 2 mom"}
    assert [entry["op"] for entry in group51["subsection_map"]] == ["REPLACE 51 § 1 mom", "REPLACE 51 § 2 mom"]
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
        AmendmentOp(op_id="", op_type="REPEAL", target_kind=TargetKind.CHAPTER, target_section="3"),
        AmendmentOp(op_id="", op_type="REPEAL", target_kind=TargetKind.CHAPTER, target_section="3"),
        AmendmentOp(op_id="", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="47", target_paragraph=7),
        AmendmentOp(op_id="", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="47", target_paragraph=7),
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
        AmendmentOp(op_id="renum_ii", op_type="RENUMBER", lo=lo_part_ii),
        AmendmentOp(op_id="renum_iii", op_type="RENUMBER", lo=lo_part_iii),
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
        op_type="REPLACE",
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

    monkeypatch.setattr("lawvm.finland.grafter.apply_op", fake_apply_op)
    monkeypatch.setattr("lawvm.finland.grafter._emit_section_snapshot", fake_emit_section_snapshot)

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
        op_type="REPLACE",
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

    monkeypatch.setattr("lawvm.finland.grafter.apply_op", fake_apply_op)
    monkeypatch.setattr("lawvm.finland.grafter._emit_section_snapshot", fake_emit_section_snapshot)

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
    assert ops[0].target_section == "26a"
    assert ops[0].target_unit_kind == "section"


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

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("INSERT", "9c", None) in got
    assert ("INSERT", "9d", None) in got
    assert ("INSERT", "10b", None) in got


def test_parse_ops_fallback_heuristic_keeps_explicit_targets_in_mixed_container_clause() -> None:
    johto = "lakiin uusi 25 a luku, muutetaan 3 §:n 1 momentti ja 4 §:n 2 momentti, sekä 5 § seuraavasti:"

    ops = parse_ops_fallback_heuristic(johto)

    assert any(op.op_type == "INSERT" and op.target_unit_kind == "chapter" and op.target_section == "25a" for op in ops)
    assert any(op.op_type == "REPLACE" and op.target_section == "3" and op.target_paragraph == 1 for op in ops)
    assert any(op.op_type == "REPLACE" and op.target_section == "4" and op.target_paragraph == 2 for op in ops)


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
    ops = [AmendmentOp(op_id="", op_type="REPEAL", target_kind=TargetKind.CHAPTER, target_section="2a")]
    johto = "Tällä asetuksella kumotaan mielenterveysasetuksen 2 a luku."

    result = _apply_uncovered_kumotaan(state, ctx, ops, johto, "2022/1386")

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
            op_type="REPEAL",
            target_kind=TargetKind.SECTION,
            target_section="24a",
            voimaantulo_repeal=True,
        )
    ]
    johto = "Eduskunnan päätöksen mukaisesti säädetään:"

    result = _apply_uncovered_kumotaan(state, ctx, ops, johto, "2023/739")

    sec24a = result.find_section("24a")
    assert sec24a is not None
    assert sec24a.attrs.get("lawvm_repeal_placeholder") == "1"


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

    import lawvm.finland.grafter as grafter_mod

    orig_children = grafter_mod._amendment_children_by_parent
    orig_reflected = grafter_mod.get_consolidated_oracle_reflected_source_vts_children
    try:
        setattr(
            grafter_mod,
            "_amendment_children_by_parent",
            lambda: {"1986/506": ["1991/806", "1993/872", "1994/1264", "2024/1049"]},
        )
        setattr(
            grafter_mod,
            "get_consolidated_oracle_reflected_source_vts_children",
            lambda _parent_id, corpus=None, selector=None: {"2024/1049"},
        )
        records, cutoff_date, oracle_version = grafter_mod._resolve_applicable_amendment_records(
            "1986/506",
            "legal_pit",
            corpus=corpus,
            selector=cast(Any, _Selector()),
        )
    finally:
        grafter_mod._amendment_children_by_parent = orig_children
        grafter_mod.get_consolidated_oracle_reflected_source_vts_children = orig_reflected

    assert oracle_version == "1994/1264"
    assert cutoff_date == dt.date(2025, 1, 1)
    assert [record["statute_id"] for record in records] == ["1991/806", "1993/872", "1994/1264", "2024/1049"]
    assert records[-1]["selection_basis"] == "oracle_editorial_repeal_stub_override"


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
            op_type="REPEAL",
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_chapter="2",
            target_paragraph=3,
            voimaantulo_repeal=True,
        )
    ]
    johto = "Tällä lailla kumotaan 2 luvun 8 §:n 3 momentti."

    result = _apply_uncovered_kumotaan(state, ctx, ops, johto, "2017/275")

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

    result = _apply_uncovered_kumotaan(
        state,
        ctx,
        [],
        "Tällä lailla kumotaan 1 luvun 5 §.",
        "2017/275",
        lo_ops_out=lo_ops,
        op_source=OperationSource(
            statute_id="2017/275",
            title="Test",
            enacted="2017-05-05",
            effective="2017-05-05",
        ),
    )

    sec5 = result.find_section("5", "1")
    assert sec5 is not None
    assert sec5.attrs.get("lawvm_repeal_placeholder") != "1"
    assert "Valmiussuunnitelma öljyvahingon varalle" in irnode_to_text(sec5)
    assert all(op.op_id != "uncovered_repeal_5" for op in lo_ops)


def test_fallback_recovers_shifted_subsection_insert_and_retargeted_replace() -> None:
    johto = (
        "muutetaan 31 päivänä heinäkuuta 1947 annetun lahjanlupauslain (625/47) "
        "3§:n 2 momentti ja 4§ sekä lisätään 3§:ään uusi 2 momentti, jolloin "
        "muutettu 2 momentti siirtyy 3 momentiksi, seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("INSERT", "3", 2) in got
    assert ("REPLACE", "3", 3) in got
    assert ("REPLACE", "4", None) in got


def test_stabilize_insert_order_prefers_insert_first_when_replace_target_only_exists_after_shift() -> None:
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type="INSERT",
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

    assert [(op.op_type, op.target_paragraph) for op in got] == [
        ("INSERT", 2),
        ("REPLACE", 3),
    ]


def test_stabilize_insert_order_keeps_replace_first_when_live_target_exists() -> None:
    ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="26",
            target_paragraph=5,
        ),
    ]
    target_ctx = SimpleNamespace(
        subsection_slots=tuple(SimpleNamespace(label=str(i)) for i in range(1, 5))
    )

    got = _stabilize_insert_order(ops, cast(Any, target_ctx))

    assert [(op.op_type, op.target_paragraph) for op in got] == [
        ("REPLACE", 3),
        ("INSERT", 3),
        ("INSERT", 5),
    ]


def test_stabilize_insert_order_moves_same_wave_subsection_renumber_after_rebased_replace_family() -> None:
    ops = [
        AmendmentOp(
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=2,
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=3,
            target_guessing_provenance_tags=("rebase_duplicate_target_shifted_replace",),
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="8",
            target_paragraph=2,
        ),
    ]
    target_ctx = SimpleNamespace(
        subsection_slots=tuple(SimpleNamespace(label=str(i)) for i in range(1, 4))
    )

    got = _stabilize_insert_order(ops, cast(Any, target_ctx))

    assert [(op.op_type, op.target_paragraph) for op in got] == [
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
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

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
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("INSERT", "26", 3) in got
    assert ("INSERT", "26", 5) in got


def test_subsection_insert_fallback_expands_plural_momentti_insert_after_provenance() -> None:
    johto = (
        "muutetaan 22 §:n 1 momentti sekä lisätään 22 §:ään, sellaisena kuin se on "
        "osaksi mainitussa asetuksessa 693/2003, uusi 5 ja 6 momentti seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("INSERT", "22", 5) in got
    assert ("INSERT", "22", 6) in got


def test_subsection_insert_fallback_stops_at_next_chapter_scoped_section_ref() -> None:
    johto = (
        "lisätään 6 luvun 1 §:ään, sellaisena kuin se on osaksi laeissa 821/2017, "
        "868/2018 ja 406/2019, uusi 11 momentti, 6 luvun 3 §:n 1 momenttiin uusi "
        "4 kohta ja pykälään, sellaisena kuin se on osaksi laissa 821/2017, uusi "
        "3 momentti sekä lukuun uusi 7 § seuraavasti:"
    )

    ops = _extract_insert_subsection_ops_fallback(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("INSERT", "1", 11) in got
    assert ("INSERT", "1", 3) not in got


def test_insert_section_fallback_expands_letter_suffix_range_inside_lakiin_uusi_clause() -> None:
    johto = "lisätään lakiin uusi 149 a–149 c ja 211 b § seuraavasti:"

    ops = _extract_insert_section_ops_fallback(johto)

    assert [op.description() for op in ops] == [
        "INSERT 149a §",
        "INSERT 149b §",
        "INSERT 149c §",
        "INSERT 211b §",
    ]


def test_insert_section_fallback_keeps_law_level_reinstatement_before_range_clause() -> None:
    johto = (
        "lisätään lakiin siitä lailla 1068/2016 kumotun 149 §:n tilalle uusi 149 § "
        "sekä lakiin uusi 149 a–149 c ja 211 b § seuraavasti:"
    )

    ops = _extract_insert_section_ops_fallback(johto)

    assert [op.description() for op in ops] == [
        "INSERT 149 §",
        "INSERT 149a §",
        "INSERT 149b §",
        "INSERT 149c §",
        "INSERT 211b §",
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
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

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
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("REPEAL", "14", 1) in got
    assert ("REPLACE", "1", 2) in got
    assert ("REPLACE", "5", None) in got
    assert ("REPLACE", "6", None) in got
    assert ("REPLACE", "7", None) in got
    assert ("REPLACE", "13", None) in got


def test_extract_replace_ops_from_muutetaan_tail_recovers_mixed_section_and_moment_targets() -> None:
    johto = "kumotaan vapaakuntakokeilusta annetun lain 5 §, muutetaan 2 §:n 2 momentti ja 15 § seuraavasti:"

    ops = _extract_replace_ops_from_muutetaan_tail(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert got == {
        ("REPLACE", "2", 2),
        ("REPLACE", "15", None),
    }


def test_fallback_recovers_chapter_and_chapter_scoped_inserts() -> None:
    johto = (
        "kumotaan oikeudenkäymiskaaren 26 luvun 1 a §, 1 b § ja 2 a §, muutetaan "
        "2 luvun 8 §:n 2 momentin 1 kohta, 25 luvun 14 b §, 26 luvun otsikko sekä 2, 3 ja 13-16 §, "
        "lisätään 25 luvun 15 §:n 1 momenttiin uusi 4 a kohta, lakiin uusi 25 a luku, "
        "26 lukuun uusi 14 a § sekä lakiin uusi 30 a luku seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_kind, op.target_chapter, op.target_section) for op in ops}

    assert ("INSERT", "L", None, "25a") in got
    assert ("INSERT", "L", None, "30a") in got
    assert ("INSERT", "P", "26", "14a") in got


def test_fallback_recovers_explicit_item_insert_and_prunes_shadowed_parent_subsection() -> None:
    johto = (
        "muutetaan 49 a §:n 1 momentin 9 kohta, lisätään 49 a §:n 1 momenttiin, "
        "sellaisena kuin se on laissa 108/2019, uusi 10 kohta, seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph, op.target_item) for op in ops}

    assert ("INSERT", "49a", 1, "10") in got
    assert ("INSERT", "49a", 1, None) not in got


def test_fallback_preserves_explicit_subsection_and_section_inserts_in_mixed_clause() -> None:
    johto = (
        "muutetaan ajoneuvolain (82/2021) 127 §, sellaisena kuin se on laissa (132/2024), "
        "ja lisätään 1 a §:ään, sellaisena kuin se on laissa 493/2023, uusi 5 momentti "
        "sekä lakiin uusi 83 a § seuraavasti:"
    )

    ops = parse_ops_fallback_heuristic(johto)
    got = {(op.op_type, op.target_section, op.target_paragraph) for op in ops}

    assert ("REPLACE", "127", None) in got
    assert ("INSERT", "1a", 5) in got
    assert ("INSERT", "83a", None) in got
    assert ("REPLACE", "83a", None) not in got


def test_root_insert_supplement_ignores_subsection_insert_clauses() -> None:
    johto = (
        "lisätään Uudenmaan maakunnan luonnonsuojelualueista annetun asetuksen "
        "(332/2021) 3 §:ään uusi 3 momentti seuraavasti:"
    )

    ops = _extract_root_insert_ops_fallback(johto)

    assert ops == []


def test_root_insert_supplement_uses_typed_provenance_without_hint() -> None:
    got = _merge_root_insert_supplements(
        [],
        [AmendmentOp(op_id="fb", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="14b")],
    )

    assert len(got) == 1
    assert got[0].extraction_provenance_tags == ("root_insert_supplement",)


def test_missing_insert_supplement_uses_typed_provenance_without_hint() -> None:
    got = _merge_missing_insert_supplements(
        [AmendmentOp(op_id="base", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")],
        [AmendmentOp(op_id="fb", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)],
    )

    op = got[-1]
    assert op.fallback_provenance is True
    assert op.extraction_provenance_tags == ("fallback_insert_supplement",)


def test_missing_insert_supplement_marks_scoped_winner_when_unscoped_fallback_is_shadowed() -> None:
    scoped = AmendmentOp(
        op_id="base",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="3",
        target_paragraph=2,
        target_chapter="4",
    )
    got = _merge_missing_insert_supplements(
        [scoped],
        [AmendmentOp(op_id="fb", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)],
    )

    assert len(got) == 1
    assert got[0].target_chapter == "4"
    assert got[0].extraction_provenance_tags == ("fallback_insert_supplement_shadowed",)


def test_missing_replace_supplement_uses_typed_provenance_without_hint() -> None:
    got = _merge_missing_replace_supplements(
        [],
        [AmendmentOp(op_id="fb", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7")],
    )

    assert len(got) == 1
    assert got[0].fallback_provenance is True
    assert got[0].extraction_provenance_tags == ("fallback_replace_supplement",)


def test_missing_replace_supplement_marks_scoped_winner_when_unscoped_fallback_is_shadowed() -> None:
    scoped = AmendmentOp(
        op_id="base",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="7",
        target_paragraph=2,
        target_chapter="4",
    )
    got = _merge_missing_replace_supplements(
        [scoped],
        [AmendmentOp(op_id="fb", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7", target_paragraph=2)],
    )

    assert len(got) == 1
    assert got[0].target_chapter == "4"
    assert got[0].extraction_provenance_tags == ("fallback_replace_supplement_shadowed",)


def test_repeal_reenact_normalization_uses_typed_provenance_without_hint() -> None:
    got = normalize_group_ops_for_repeal_reenact(
        [
            AmendmentOp(op_id="rep", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="4"),
            AmendmentOp(op_id="ins", op_type="INSERT", target_kind=TargetKind.SECTION, target_section="4"),
        ]
    )

    assert len(got) == 1
    assert got[0].op_type == "REPLACE"
    assert got[0].extraction_provenance_tags == ("repeal_reenact_normalized",)


def test_repeal_reenact_normalization_leaves_multiple_repeals_unchanged() -> None:
    # Bug regression: amendment with kumotaan ... 2a, 4-7 § sekä muutetaan 2, 3 §
    # must NOT convert any repeal to replace — all repeals are pure repeals.
    ops = [
        AmendmentOp(op_id="rep_2a", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="2a"),
        AmendmentOp(op_id="rep_4", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="4"),
        AmendmentOp(op_id="rep_5", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="5"),
        AmendmentOp(op_id="repl_2", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="2"),
        AmendmentOp(op_id="repl_3", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3"),
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
        AmendmentOp(op_id="rep_7", op_type="REPEAL", target_kind=TargetKind.SECTION, target_section="7"),
        AmendmentOp(op_id="repl_2", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="2"),
    ]
    got = normalize_group_ops_for_repeal_reenact(ops)

    assert got is ops  # unchanged
    assert got[0].op_type == "REPEAL"
    assert got[1].op_type == "REPLACE"


def test_append_compiled_group_ops_omits_resolution_hint_field() -> None:
    compiled_ops = []

    op = AmendmentOp(op_id="op0", op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="4")
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
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="5",
        scope_provenance_tags=("chapter_scope_from_johtolause",),
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
            "action": "replace",
            "source_statute": "",
            "source_title": None,
            "extraction_provenance_tags": [],
            "target_guessing_provenance_tags": [],
            "scope_provenance_tags": ["chapter_scope_from_johtolause"],
            "witness_rule_id": None,
            "target_unit_kind": "section",
            "target_norm": "4",
            "target_chapter": "5",
            "target_part": "",
            "target_paragraph": "",
            "target_item": "",
            "target_special": "",
            "scope_source": "johtolause",
            "scope_confidence": "inferred",
        }
    ]


def test_append_compiled_group_ops_prefers_stored_scope_confidence_over_sidecar_tags() -> None:
    compiled_ops = []

    op = AmendmentOp(
        op_id="op0",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="5",
        scope_provenance_tags=("grouped_chapter_scope",),
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
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

    assert compiled_ops[0]["scope_provenance_tags"] == ["grouped_chapter_scope"]
    assert compiled_ops[0]["scope_source"] == "explicit_chunk"
    assert compiled_ops[0]["scope_confidence"] == "explicit"


def test_duplicate_frontend_target_observations_flags_exact_duplicate_targets() -> None:
    ops = [
        AmendmentOp(op_id="", op_type="REPLACE", target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(
            op_id="",
            op_type="INSERT",
            target_section="98",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            target_paragraph=3,
        ),
        AmendmentOp(
            op_id="",
            op_type="INSERT",
            target_section="98",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            target_paragraph=3,
        ),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="33", target_kind=TargetKind.SECTION, target_paragraph=1),
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
        AmendmentOp(op_id="", op_type="REPLACE", target_section="31", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="32", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="34", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="33", target_kind=TargetKind.SECTION),
        AmendmentOp(op_id="", op_type="REPLACE", target_section="34", target_kind=TargetKind.SECTION),
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
            op_type="INSERT",
            target_section="2",
            target_unit_kind="section",
        )
    ]

    got = _enrich_ops_from_amendment_tree(ops, "2024/1", muutos_tree, master=None)

    assert got[0].op_id
    assert got[0].op_id.startswith("fi:2024/1:")


def test_stamp_fallback_op_ids_mints_deterministic_ids_for_blank_ops() -> None:
    op = AmendmentOp(op_id="", op_type="INSERT", target_section="2", target_unit_kind="section")

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
            op_type="REPLACE",
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="",
            op_type="REPLACE",
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
            target_paragraph=1,
        ),
        AmendmentOp(
            op_id="",
            op_type="REPLACE",
            target_section="3",
            target_kind=TargetKind.SECTION,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="",
            op_type="REPLACE",
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
            op_type="REPLACE",
            target_section="33",
            target_kind=TargetKind.SECTION,
            target_chapter="5",
            scope_provenance_tags=("chapter_scope_carry_forward",),
        ),
        AmendmentOp(
            op_id="",
            op_type="REPLACE",
            target_section="34",
            target_kind=TargetKind.SECTION,
            target_chapter="5",
            scope_provenance_tags=("grouped_chapter_scope", "chapter_scope_from_johtolause"),
        ),
        AmendmentOp(
            op_id="",
            op_type="REPLACE",
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
                "tag": "chapter_scope_from_johtolause",
                "scope_source": "johtolause",
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

    ops = _extract_root_insert_ops_fallback(johto)
    got = [(op.target_chapter, op.target_section) for op in ops]

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

    ops = _extract_root_insert_ops_fallback(johto)
    got = {(op.target_chapter, op.target_section) for op in ops}

    assert (None, "46c") in got
    assert (None, "32") not in got


def test_root_insert_fallback_recovers_combined_root_chapter_and_section_ranges() -> None:
    johto = (
        "lisätään 3 §:ään uusi 6 kohta, 16 §:ään uusi 5 momentti "
        "sekä lakiin uusi 5 a—5 c luku ja 20 a—20 h § seuraavasti:"
    )

    ops = _extract_root_insert_ops_fallback(johto)
    got = {(op.target_kind, op.target_chapter, op.target_section) for op in ops}

    assert ("L", None, "5a") in got
    assert ("L", None, "5b") in got
    assert ("L", None, "5c") in got
    assert ("P", None, "20a") in got
    assert ("P", None, "20h") in got
    assert ("P", None, "16") not in got


def test_root_insert_fallback_recovers_decision_scoped_secondary_section_range() -> None:
    johto = (
        "muutetaan yritystuesta 30 päivänä joulukuuta 1993 annetun valtioneuvoston päätöksen "
        "(1689/93) 1 §:n sekä lisätään päätökseen uuden 14a §:n ja sen edelle uuden väliotsikon "
        "sekä uuden 14b―14d §:n seuraavasti:"
    )

    ops = _extract_root_insert_ops_fallback(johto)
    got = {(op.target_kind, op.target_chapter, op.target_section) for op in ops}

    assert ("P", None, "14a") in got
    assert ("P", None, "14b") in got
    assert ("P", None, "14c") in got
    assert ("P", None, "14d") in got


def test_combined_root_insert_ranges_place_trailing_sections_under_following_chapter() -> None:
    master = pinned_replay("2007/159", mode="finlex_oracle")
    sections = extract_ir_sections(master.ir)

    assert "chapter:6/section:20a" in sections
    assert "chapter:6/section:20h" in sections
    assert "chapter:5c/section:20a" not in sections
    assert "chapter:5c/section:20h" not in sections


def test_replay_xml_2002_1330_prefers_live_substantive_section_8_over_repeal_placeholder_slot() -> None:
    replay = pinned_replay("2002/1330", as_of="2019-04-02", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    section8_paths = sorted(path for path in sections if path.endswith("section:8"))
    assert section8_paths == ["chapter:2a/section:8"]

    sec8 = sections["chapter:2a/section:8"]
    text = irnode_to_text(sec8)
    assert "rekrytointia tukevaan toimintaan" in text
    assert "itsenäistä valmentautumista tarjolle annetun materiaalin perusteella" in text
    assert "julkisesta työvoima- ja yrityspalvelusta annetun lain 4 luvun 12 §:ssä" not in text


def test_replay_xml_2013_588_retargets_explicit_chunk_sections_from_2023_497_to_live_part_chapter() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

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
    replay = pinned_replay("1982/716", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec18 = sections["chapter:3/section:18"]
    text = irnode_to_text(sec18)

    assert "9, 9 a ja 10 §:n nojalla" in text
    assert "alueellisille ympäristökeskuksille" in text
    assert "Suomen Kuntaliitolle" in text
    assert "9 ja 9 a §:n nojalla" not in text


def test_replay_xml_2007_370_drops_stale_section_15_item_7_subitems_after_2015_742() -> None:
    replay = pinned_replay("2007/370", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec15 = sections["chapter:4/section:15"]
    sub1 = next(c for c in sec15.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    para7 = next(c for c in sub1.children if c.kind == IRNodeKind.PARAGRAPH and c.label == "7")

    assert not any(c.kind == IRNodeKind.SUBPARAGRAPH for c in para7.children)
    text = irnode_to_text(para7)
    assert "todistamiskiellosta huolimatta henkilö velvoitetaan todistamaan" in text
    assert "velvoitetaan ilmaisemaan seikka" not in text


def test_replay_xml_1997_133_applies_section_31_intro_replace_from_2026_130() -> None:
    replay = pinned_replay("1997/133", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec31 = sections["chapter:5/section:31"]
    text = " ".join(irnode_to_text(sec31).split())

    assert "Sen lisäksi, mitä kolttalain 37 §:n 1 momentissa säädetään, elinvoimakeskus voi määrätä" in text
    assert "elinkeino-, liikenne- ja ympäristökeskus voi määrätä" not in text


def test_replay_xml_1995_1760_restores_inserted_section_8b_from_2004_1250() -> None:
    replay = pinned_replay("1995/1760", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)

    sec8b = sections["section:8b"]
    text = irnode_to_text(sec8b)

    assert "Tonnistoverovelvollisen ilmoittamisvelvollisuus" in text
    assert "Tonnistoverovelvollisen yhtiön on annettava Konserniverokeskukselle seuraavat tiedot" in text


def test_replay_xml_1940_378_1994_318_does_not_duplicate_section_61_timeline_versions() -> None:
    replay = pinned_replay("1940/378", as_of="1994-07-02", mode="finlex_oracle", quiet=True)
    addr = LegalAddress(path=(("chapter", "7"), ("section", "61")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    versions = replay.timelines[addr].versions

    assert [version.source.statute_id if version.source else None for version in versions] == [
        None,
        "1994/318",
    ]


def test_whole_section_replace_collapses_intro_list_subsections_into_paragraphs() -> None:
    master = pinned_replay("1993/58", mode="finlex_oracle")
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
            <section>
              <num>4 a §</num>
              <subsection><content><p>foo</p></content></subsection>
            </section>
            <section>
              <num>4 b §</num>
              <subsection><content><p>bar</p></content></subsection>
            </section>
          </body>
        </akn>
        """
    )

    # MVR: use the refactored two-step API.
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "2021/1215")
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2021/1215",
        failed_ops_out=[],
    )
    got = state
    for rop in rops:
        assert rop.intent is not None
        assert rop.op.uncovered_body_recovery is True
        got = apply_op(got, None, ctx, None, replay_mode="finlex_oracle", rop=rop)

    assert got.find_section("4a") is not None
    assert got.find_section("4b") is not None
    # MVR: op_ids are mirrored onto ResolvedOp (uncovered_insert_<label>)
    assert [rop.op_id for rop in rops] == ["uncovered_insert_4a", "uncovered_insert_4b"]
    assert [rop.op_id for rop in rops] == [rop.op.op_id for rop in rops]


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
    ops = [AmendmentOp(op_id="", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="7a")]
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

    # MVR: use the refactored two-step API.
    muutos_body_el = muutos_tree.find(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body")
    if muutos_body_el is not None:
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "2020/1207")
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
    from lawvm.finland.grafter import RepealTargetRef

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
    from lawvm.finland.grafter import RepealTargetRef

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
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "2020/1207")
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
            op_type="REPLACE",
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

    monkeypatch.setattr("lawvm.finland.grafter_uncovered.analyze_coverage", _fake_analyze_coverage)

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
            op_type="REPLACE",
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
    skipped = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_PEG_LABEL_COLLISION"]
    assert len(skipped) == 1
    assert skipped[0].detail.get("reason") == "peg_owned_label_collision"
    assert skipped[0].detail.get("target_section") == "55a"
    assert skipped[0].detail.get("target_chapter") == "7a"


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


@pytest.mark.parametrize(
    ("reason", "expected_kind"),
    [
        ("moved_destination_mismatch", "APPLY.UNCOVERED_BODY_MOVED_DESTINATION_MISMATCH"),
        ("body_pairing_guard", "APPLY.UNCOVERED_BODY_BODY_PAIRING_GUARD"),
        ("no_content_ops", "APPLY.UNCOVERED_BODY_NO_CONTENT_OPS"),
        ("would_lose_subsections", "APPLY.UNCOVERED_BODY_WOULD_LOSE_SUBSECTIONS"),
        ("johto_guard", "APPLY.UNCOVERED_BODY_JOHTO_GUARD"),
        ("omission_merge_failed", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_FAILED"),
        ("omission_merge_low_text_ratio", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_LOW_TEXT_RATIO"),
        ("omission_merge_duplicate_subsection_labels", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_DUPLICATE_LABELS"),
        ("omission_merge_would_lose_subsections", "APPLY.UNCOVERED_BODY_OMISSION_MERGE_WOULD_LOSE_SUBSECTIONS"),
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
        ["1993/949"],
        _corpus_store({"1993/949": xml}),
        parent_id="1958/370",
        parent_title="Rakennuslaki",
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
    ops = [AmendmentOp(op_id="ch5_insert", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="5luku")]
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
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute=ctx.id,
                    target_address="20",
                    claim_kind="REPLACE",
                    chapter="5",
                ),
            )
        ]

    monkeypatch.setattr("lawvm.finland.grafter_uncovered.assign_body_units_subtree_aware", _fake_assignments)

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


def test_uncovered_body_dual_run_records_chapter_payload_owned_from_subtree_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    ops = [AmendmentOp(op_id="ch5_insert", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="5luku")]
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
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute=ctx.id,
                    target_address="5",
                    claim_kind="INSERT",
                    chapter="",
                ),
            )
        ]

    def _fake_analyze_coverage(_units: list[CoverageUnit], _claims: list[CoverageClaim], **_kwargs: object) -> CoverageReport:
        return CoverageReport(units=(), claims=(), gaps=())

    monkeypatch.setattr("lawvm.finland.grafter_uncovered.assign_body_units_subtree_aware", _fake_assignments)
    monkeypatch.setattr("lawvm.finland.grafter_uncovered.analyze_coverage", _fake_analyze_coverage)

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

    assert rops == []
    owned = [f for f in findings_out if f.kind == "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED"]
    assert len(owned) == 1
    assert owned[0].detail.get("target_section") == "20"
    assert owned[0].detail.get("target_chapter") == "5"


def test_uncovered_body_dual_run_does_not_blanket_skip_covered_chapter_without_subtree_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    ops = [AmendmentOp(op_id="ch5_insert", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="5luku")]
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
                status="claimed_current",
                claim=ClauseClaim(
                    target_statute=ctx.id,
                    target_address="20",
                    claim_kind="REPLACE",
                    chapter="5",
                ),
            )
        ]

    def _fake_analyze_coverage(_units: list[CoverageUnit], _claims: list[CoverageClaim], **_kwargs: object) -> CoverageReport:
        return CoverageReport(units=(), claims=(), gaps=())

    monkeypatch.setattr("lawvm.finland.grafter_uncovered.assign_body_units_subtree_aware", _fake_assignments)
    monkeypatch.setattr("lawvm.finland.grafter_uncovered.analyze_coverage", _fake_analyze_coverage)

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
            op_type="RENUMBER",
            target_section="73",
            target_kind=TargetKind.SECTION,
            target_chapter="7",
            lo=renumber_lo,
        ),
        AmendmentOp(
            op_id="replace_ch7_heading",
            op_type="REPLACE",
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
            op_type="RENUMBER",
            target_section="4",
            target_kind=TargetKind.SECTION,
            target_chapter="12",
            lo=renumber_lo,
        ),
        AmendmentOp(
            op_id="replace_ch12_heading",
            op_type="REPLACE",
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


def test_process_muutoslaki_2017_320_2019_371_records_relabel_destination_owned_skips_for_leaf_destinations() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)

    phase = process_muutoslaki(
        "2019/371",
        before.replay_fold_state,
        ctx,
        replay_mode="legal_pit",
        parent_id="2017/320",
        corpus=corpus,
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


def test_process_muutoslaki_2017_320_2019_371_no_longer_fails_old_section6_subsection_replaces_after_renumber() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)
    failed: list[FailedOp] = []

    with redirect_stdout(StringIO()):
        process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
            failed_ops_out=failed,
        )

    assert not any(
        f.target_chapter == "12"
        and f.target_section == "6"
        and f.reason_code == "section_not_found"
        for f in failed
    )


def test_process_muutoslaki_2017_320_2019_371_no_longer_fails_old_section8_9_11_replaces_after_part_scoped_section_renumber() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)
    failed: list[FailedOp] = []

    with redirect_stdout(StringIO()):
        process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
            failed_ops_out=failed,
        )

    blocked = {
        (f.target_chapter, f.target_section, f.description)
        for f in failed
        if f.reason_code == "section_not_found"
    }
    assert ("1", "8", "REPLACE 1 luku 8 §") not in blocked
    assert ("1", "9", "REPLACE 1 luku 9 §") not in blocked
    assert ("1", "11", "REPLACE 1 luku 11 §") not in blocked


def test_process_muutoslaki_2017_320_2019_371_no_longer_fails_iia_heading_replaces() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)
    failed: list[FailedOp] = []

    with redirect_stdout(StringIO()):
        process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
            failed_ops_out=failed,
        )

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


def test_process_muutoslaki_2017_320_2019_371_follows_descendant_replace_through_relabel_destination_frame() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)
    failed: list[FailedOp] = []

    with redirect_stdout(StringIO()):
        process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
            failed_ops_out=failed,
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


def test_process_muutoslaki_2017_320_2019_371_uses_destination_payload_surface_for_sparse_source_shells() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)

    with redirect_stdout(StringIO()):
        phase = process_muutoslaki(
            "2019/371",
            before.replay_fold_state,
            ctx,
            replay_mode="legal_pit",
            parent_id="2017/320",
            corpus=corpus,
        )

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


def test_process_muutoslaki_2017_320_2019_371_governs_pending_relabel_gap_failure() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2019/371", quiet=True)
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


def test_replay_xml_2017_320_2018_301_keeps_part_scoped_chapter_4_section_11() -> None:
    corpus = get_corpus()
    orig = corpus.read_source("2017/320")
    if orig is None:
        pytest.skip("corpus archive not available")

    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    before = pinned_replay("2017/320", mode="legal_pit", stop_before="2018/301", quiet=True)

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

    result = apply_op(state, None, ctx, None, replay_mode="finlex_oracle", rop=replace_6)
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
            op_type="REPLACE",
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
    ops = [AmendmentOp(op_id="ch5_insert", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="5luku")]

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
        final_state = apply_op(final_state, None, ctx, None, replay_mode="finlex_oracle", rop=rop)
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
            op_type="INSERT",
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
        final_state = apply_op(final_state, None, ctx, None, replay_mode="finlex_oracle", rop=rop)

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
    ops = [AmendmentOp(op_id="ch5_insert", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="5luku")]
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
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "2020/100")
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
    assert insert_rop[0].op.target_chapter == "7"
    assert insert_rop[0].op.uncovered_body_recovery is True


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
    assert rops[0].op.target_chapter == "6"
    assert rops[0].op.uncovered_body_recovery is True


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
        op_type="INSERT",
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
        op_type="INSERT",
        target_section="32",
        target_kind=TargetKind.SECTION,
        target_chapter="4d",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
            resolved_chapter="4d",
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
        op_type="INSERT",
        target_section="32",
        target_kind=TargetKind.SECTION,
        target_chapter="4d",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_stripped_unique_section",
            source="explicit_scope_rewrite",
            confidence="rewritten",
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
        op_type="INSERT",
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
        op_type="INSERT",
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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _body_chapter_scope_for_section_op(op=op, muutos_tree=muutos_tree, master=master)

    assert got == "6"


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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="8a",
        target_chapter="3",
        target_paragraph=2,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
            resolved_chapter="3",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_chapter == "3"
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
        op_type="INSERT",
        target_unit_kind="section",
        target_section="37a",
        target_chapter="5",
        target_paragraph=2,
        scope_confidence=ScopeConfidence(
            tag="chapter_scope_carry_forward",
            source="carry_forward",
            confidence="inferred",
            resolved_chapter="5",
        ),
        scope_provenance_tags=("chapter_scope_carry_forward",),
    )

    got = _enrich_ops_from_amendment_tree([op], "2024/1", muutos_tree, master=master)

    assert len(got) == 1
    assert got[0].target_chapter == "6"


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
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="8",
        target_paragraph=2,
    )
    scoped = AmendmentOp(
        op_id="scoped",
        op_type="INSERT",
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
    merged_ops = got[(IRNodeKind.SECTION, "8", "2", None)]
    assert [op.op_id for op in merged_ops] == ["bare", "scoped"]
    assert merged_ops[0].scope_provenance_tags[-1] == "mixed_scope_group_merge"
    assert merged_ops[0].target_chapter == "2"
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
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="1",
        target_paragraph=1,
    )
    scoped_insert = AmendmentOp(
        op_id="scoped_insert",
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="4",
        target_chapter="1",
        target_paragraph=7,
    )
    bare_insert = AmendmentOp(
        op_id="bare_insert",
        op_type="INSERT",
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
    assert [op.op_id for op in got[(IRNodeKind.SECTION, "4", "1", None)]] == [
        "scoped_replace",
        "scoped_insert",
    ]


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
        ["2025/1352"],
        corpus,
        parent_id="1996/1260",
        cutoff_date=dt.date(2025, 12, 22),
    )

    assert got == [set()]


def test_pre_scan_repeal_targets_accepts_parent_title_for_vts_scan(monkeypatch) -> None:
    seen: list[str] = []

    def _fake_extract(xml_bytes, parent_id, parent_title=""):
        seen.append(parent_title)
        return []

    # _pre_scan_repeal_targets lives in grafter_uncovered, which has its own
    # `from lawvm.finland.vts import extract_voimaantulo_repeals` binding.
    # Patching the grafter re-export does not affect that module's lookup.
    monkeypatch.setattr(
        "lawvm.finland.grafter_uncovered.extract_voimaantulo_repeals",
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

    got = _pre_scan_repeal_targets(
        ["2025/1352"],
        corpus,
        parent_id="1996/1260",
        parent_title="Sahkoverolaki",
        cutoff_date=dt.date(2025, 12, 22),
    )

    assert got == [set()]
    assert seen == ["Sahkoverolaki"]


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


def test_snapshot_source_falls_back_to_amendment_dates_for_supplement_ops() -> None:
    aop = AmendmentOp(
        op_id="",
        op_type="INSERT",
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

    op = AmendmentOp(op_id="", op_type="REPLACE", target_section="1", target_kind=TargetKind.SECTION)

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
    assert {
        cast(dict, row.get("detail") or {}).get("code")
        for row in rejected
        } == {
            "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "MALFORMED_BROAD_REPLACE_BODY",
            "PARTIAL_WHOLE_SECTION_PAYLOAD",
            "SUBSECTION_TARGET_REBOUND",
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
        cast(dict, row.get("detail") or {}).get("code")
        for row in rejected
    }


def test_replay_xml_1986_609_applies_2021_657_subsection_replace_inside_section_omission_shell() -> None:
    replay = pinned_replay(
        "1986/609",
        mode="finlex_oracle",
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
    replay = pinned_replay("1920/26", mode="finlex_oracle", quiet=True)
    sec6 = replay.find_section("6")
    assert sec6 is not None
    assert sec6.attrs.get("lawvm_repeal_placeholder") == "1"
    assert all(child.kind is IRNodeKind.NUM for child in sec6.children)
    assert replay.find_section("26") is not None


def test_replay_xml_2004_699_preserves_section_31_items_when_2013_984_inserts_subsection_2() -> None:
    replay = pinned_replay("2004/699", mode="finlex_oracle", quiet=True)
    sec31 = replay.find_section("31")

    assert sec31 is not None
    sub1 = next(child for child in sec31.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    sub2 = next(child for child in sec31.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")
    paragraphs = [child for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH]
    assert [child.label for child in paragraphs] == ["1", "2", "3", "4", "5", "6"]
    para5 = next(child for child in paragraphs if child.label == "5")
    assert para5.attrs.get("lawvm_repeal_placeholder") == "1"
    assert "Euroopan keskuspankkiin" in irnode_to_text(sub2)


def test_replay_xml_2004_699_exact_section_replaces_do_not_keep_stale_subsection_tails() -> None:
    replay = pinned_replay("2004/699", mode="finlex_oracle", quiet=True)

    sec7 = replay.find_section("7")
    sec12 = replay.find_section("12")
    sec21 = replay.find_section("21")
    sec23 = replay.find_section("23")
    sec32 = replay.find_section("32")

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
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="1",
            target_chapter="5c",
            target_part="",
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="20a",
            target_chapter="6",
            target_part="",
        ),
        AmendmentOp(
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="20h",
            target_chapter=None,
            target_part="",
        ),
        AmendmentOp(
            op_type="INSERT",
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


def test_replay_xml_2010_1048_repeals_6a_lane_and_keeps_live_18b_26() -> None:
    replay = pinned_replay("2010/1048", mode="finlex_oracle")
    state = replay.materialized_state

    assert state.find("chapter", "6a") is None
    assert state.find_section("15a", "6a") is None
    assert state.find_section("15b", "6a") is None
    assert state.find_section("15c", "6a") is None
    assert state.find_section("18b", "6a") is None
    assert state.find_section("26", "6a") is None
    assert state.find_section("18b", "7") is not None
    assert state.find_section("26", "9") is not None


def test_replay_xml_1991_1144_does_not_duplicate_section_60b_under_chapter_9a() -> None:
    replay = pinned_replay("1991/1144", mode="finlex_oracle")
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
        and row.get("witness_rule_id") == "fi.insertion_heading"
    )

    assert row["target_chapter"] == "4"


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


def test_replay_xml_materializes_1962_420_section_22_only_in_chapter_four() -> None:
    result = pinned_replay("1962/420", mode="legal_pit", quiet=True)

    def _walk_sections(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> list[tuple[tuple[str, str], ...]]:
        found: list[tuple[tuple[str, str], ...]] = []
        if node.kind == IRNodeKind.SECTION and node.label == "22":
            found.append(path)
        for child in node.children:
            found.extend(_walk_sections(child, path + ((child.kind.value, child.label or ""),)))
        return found

    section_paths = _walk_sections(result.state.ir)
    assert section_paths == [(("chapter", "4"), ("section", "22"))]

    section_22 = result.state.find_section("22", "4")
    assert section_22 is not None
    text = irnode_to_text(section_22)
    assert "Voimaantulo" in text
    assert "Tämä laki tulee voimaan" in text


def test_tag_explicit_item_shift_after_repeal_hints_marks_matching_repeal_op() -> None:
    ops = [
        AmendmentOp(
            op_id="repeal_d",
            op_type="REPEAL",
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="d",
        ),
        AmendmentOp(
            op_id="replace_c",
            op_type="REPLACE",
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
            op_type="REPEAL",
            target_section="2",
            target_kind=TargetKind.SECTION,
            target_paragraph=1,
            target_item="d",
        ),
        AmendmentOp(
            op_id="replace_c",
            op_type="REPLACE",
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
        (op.op_type, op.target_section, op.target_paragraph, op.target_item, op.post_repeal_item_shift_label)
        for op in got
    }


def test_supplement_named_table_row_mixed_clause_ops_adds_missing_replace_and_tags_rows() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type="REPEAL",
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

    assert [(op.op_type, op.target_section) for op in got] == [("REPEAL", "1"), ("REPLACE", "1")]
    assert got[0].named_row_targets == ("iitin", "juvan")
    assert got[1].named_row_targets == ("kouvolan", "mikkelin")


def test_supplement_named_table_row_mixed_clause_ops_handles_osalta_wording() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type="REPEAL",
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

    assert [(op.op_type, op.target_section) for op in got] == [("REPEAL", "1"), ("REPLACE", "1")]
    assert got[0].named_row_targets == ("pirkanmaan",)
    assert got[1].named_row_targets == ("tampereen",)


def test_tag_named_table_row_single_clause_ops_tags_single_replace_clause() -> None:
    ops = [
        AmendmentOp(
            op_id="op0",
            op_type="REPLACE",
            target_section="1",
            target_kind=TargetKind.SECTION,
        )
    ]

    got = _tag_named_table_row_single_clause_ops(
        ops,
        "muutetaan päätöksen 1 §:n Iisalmen käräjäoikeutta koskevan kohdan seuraavasti:",
    )

    assert [(op.op_type, op.target_section) for op in got] == [("REPLACE", "1")]
    assert got[0].named_row_targets == ("iisalmen",)


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
    master = pinned_replay("2015/1525", mode="finlex_oracle")
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
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "2000/559")
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "2000/559",
        failed_ops_out=[],
    )
    # Sections 2 and 3 from chapter 45 body must NOT be filtered out
    recovered_labels = {rop.op.target_section for rop in rops}
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
        state, _ = _pre_create_amendment_chapters(state, muutos_body_el, "1995/578")
    rops = _recover_uncovered_body_ops(
        state,
        ctx,
        [],
        muutos_tree,
        "1995/578",
        failed_ops_out=[],
    )
    recovered = {(rop.op.target_chapter, rop.op.target_section) for rop in rops}
    assert ("47", "2") in recovered, f"Section 47/2 was filtered; recovered: {recovered}"
    assert ("48", "7") in recovered, f"Section 48/7 was filtered; recovered: {recovered}"


# ---------------------------------------------------------------------------
# grafter_simple: label-based subsection resolution (Pattern C regression)
# ---------------------------------------------------------------------------


def test_subsection_replace_uses_label_not_position_current_apply_path() -> None:
    """Current subsection replace helper must still resolve by label, not index."""
    # Coverage lives primarily in tests/test_apply.py; keep one fallback-era
    # assertion here so the older grafter regression family still points at the
    # current executor path instead of the deleted grafter_simple module.
    from lawvm.core.tree_ops import resolve as tree_resolve
    from tests.test_apply import _FINLEX_ORACLE, _body, _content, _make_state, _modified, _op, _sec, _sub
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
    op = _op(op_type="REPLACE", target_section="5", target_paragraph=2)

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
    from lawvm.core.tree_ops import dedup_children_by_label

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
    from lawvm.finland.grafter import _emit_structural_dedup_warning

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
    assert finding.detail["duplicates"] == [
        {
            "path": "body",
            "kind": "section",
            "label": "1",
        }
    ]
    assert finding.source_statute == "1976/673"


def test_emit_structural_dedup_warning_noop_when_tree_unchanged() -> None:
    from lawvm.core.ir import IRNode
    from lawvm.finland.grafter import _emit_structural_dedup_warning

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


def test_replay_xml_2014_834_voimaantulo_only_amendment_keeps_section_7a() -> None:
    """Regression: 2014/834 §7a was MISSING from finlex_oracle replay.

    §7a was inserted by 2019/154 with expires='2021-04-30'.  Subsequent amendments
    2021/179 and 2023/197 each extended 8a§ explicitly, and 2025/41 amended only the
    voimaantulosäännös (entry-into-force provision) to extend the whole regulation to
    2029-04-30.

    Bug: the _commencement_expiry_override was only called for SKIPPED amendments, not
    for accepted ones.  Additionally, the fallback in _rewrite_lo_op_source_expiry
    didn't handle the case where the target statute was the parent statute itself (all
    lo_ops carry amendment IDs, not the parent statute ID, as source).

    Fix: call _commencement_expiry_override for accepted amendments after
    apply_ops_to_tree, and clear the expires field in finlex_oracle mode so
    materialization at 9999-12-31 includes the section.
    """
    master = pinned_replay("2014/834", mode="finlex_oracle")
    sections = master.find_section("7a")
    assert sections is not None, "Section 7a must be present in finlex_oracle replay"


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
    master = pinned_replay("1959/324", mode="finlex_oracle")
    sec = master.find_section("4a")
    assert sec is not None
    text = irnode_to_text(sec)

    assert "korkolain 4 §:n 3 momentissa tarkoitetun korkokannan mukainen" in text
    assert "16 prosenttia" not in text


def test_replay_xml_2016_549_section_32_keeps_subsection_1_under_2022_283_root() -> None:
    """Materialized PIT must reattach surviving subsection 1 under the 2022/283 section root."""
    master = pinned_replay("2016/549", mode="finlex_oracle", quiet=True)
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

    new_state, created = _pre_create_amendment_chapters(state, muutos_body_el, "2015/303")

    assert ("", "8a") in created, f"Expected root chapter ref ('', '8a'); got {created}"
    ch8a = new_state.find_chapter("8a")
    assert ch8a is not None, "Chapter 8a must be present in state after pre-creation"


def test_pre_create_amendment_chapters_keeps_part_scope_for_same_label_chapters() -> None:
    """Pre-create must not let chapter labels collide across different parts."""
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

    new_state, created = _pre_create_amendment_chapters(
        state,
        muutos_body_el,
        "2018/301",
        required_labels={("5", "2")},
    )

    assert created == [("5", "2")]
    part_5_path = new_state.find("part", "5")
    assert part_5_path is not None
    part_5 = new_state.resolve(part_5_path)
    assert part_5 is not None
    part_5_chapters = [child.label for child in part_5.children if child.kind is IRNodeKind.CHAPTER]
    assert "2" in part_5_chapters


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
    ops = [AmendmentOp(op_id="", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="3")]

    observations_out: list = []
    restructure_plans_out: list = []
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


def test_recover_uncovered_body_ops_deduplicates_identical_restructure_plan_output() -> None:
    """Repeated recovery for the same amendment must not append the same plan twice."""
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(12))
    ops = [AmendmentOp(op_id="", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="3")]

    restructure_plans_out: list = []

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
        op_type="RENUMBER",
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
        _op_type_seed="RENUMBER",
        _target_address_override=source,
        _destination_address_override=destination,
        intent=Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(source),
            destination=NodeTarget(destination),
            contract=ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace()),
        ),
    )

    assert rop.op.target_paragraph == 9
    assert rop.op.target_chapter == "9"
    assert rop.op.target_part == "11"
    assert rop.resolved_target_scope_chapter_label == "5"
    assert rop.resolved_target_scope_part_label is None
    assert rop.effective_target_paragraph is None
    assert _resolved_op_is_owned_by_restructure_plan(rop, set()) is False


def test_resolved_op_restructure_plan_helper_accepts_exact_owned_signature() -> None:
    source = LegalAddress(path=(("chapter", "5"), ("section", "33")))
    destination = LegalAddress(path=(("chapter", "5"), ("section", "34")))
    op = AmendmentOp(
        op_id="relabel-1",
        op_type="RENUMBER",
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
        _op_type_seed="RENUMBER",
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
        op_type="RENUMBER",
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
        _op_type_seed="RENUMBER",
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
    """Canonical intent move destinations must follow late-waist chapter/part fields."""
    op = AmendmentOp(
        op_id="move-typed-1",
        op_type="REPLACE",
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

    assert rop.op.target_chapter == "9"
    rop.move_clause_target_chapter = "5"
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert isinstance(intent, Move)
    assert intent.destination_parent.path == (("chapter", "5"),)


def test_recover_uncovered_body_ops_no_observation_when_observations_out_is_none() -> None:
    """When observations_out is None the guardrail is silently skipped (backward compat)."""
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY, children=()))
    ctx = _statute_context(state.ir)
    muutos_tree = etree.fromstring(_make_many_section_muutos_xml(12))
    ops = [AmendmentOp(op_id="", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="3")]

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
    ops = [AmendmentOp(op_id="", op_type="INSERT", target_kind=TargetKind.CHAPTER, target_section="3")]

    observations_out: list = []
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
                op_type="REPLACE",
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


def test_replay_xml_2016_1227_keeps_section_12_subsection_5_after_2022_1149() -> None:
    """Sparse middle-slot replace must preserve the carried tail in 2016/1227 §12."""
    master = pinned_replay("2016/1227", mode="finlex_oracle", quiet=True)
    sec = master.find_section("12", chapter_num="2")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4", "5"]
    assert (
        "Yksityisten terveydenhuollon palvelujen antajien valvontaan liittyvistä tarkastuksista"
        in irnode_to_text(subsections[4])
    )


def test_replay_xml_2016_1227_reuses_repealed_section_51_subsection_3_slot() -> None:
    """2022/1149 must fill the repealed 3rd-moment slot without shifting old 4 -> 5."""
    master = pinned_replay("2016/1227", mode="finlex_oracle", quiet=True)
    sec = master.find_section("51", chapter_num="5")

    assert sec is not None
    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4"]
    assert "Edellä 2 momentissa tarkoitetut hoitopaikan potilasasiakirjoissa" in irnode_to_text(subsections[2])
    assert subsections[3].attrs.get("lawvm_repeal_placeholder") == "1"


def test_replay_xml_2009_617_preserves_section_tail_under_2016_533_sparse_section_shells() -> None:
    master = pinned_replay("2009/617", mode="finlex_oracle", quiet=True)

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
    master = pinned_replay("1947/328", mode="finlex_oracle", quiet=True)
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
    replay = pinned_replay("2015/351", mode="finlex_oracle", quiet=True)
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
    replay = pinned_replay("2011/715", mode="finlex_oracle", quiet=True)

    sec_5a = replay.find_section("5a")
    sec_5b = replay.find_section("5b")

    assert sec_5a is not None
    assert sec_5b is None
    assert "Oikeudenkäyntiavustajalautakunnan henkilöstö" in irnode_to_text(sec_5a)


def test_dual_run_skips_tällä_lailla_kumotaan_repeal_clause_section() -> None:
    """Dual-run ad-hoc must NOT process a repealing statute's own repeal provision.

    Regression test for the 2015/640 bug: the amending act 2015/640 had section 1
    starting with 'Tällä lailla kumotaan tullilain (1466/1994) 21 §:n...' — its own
    repeal clause.  Without the fix, the dual-run would try to replace section 1 of
    the base act (1994/1466) with this repeal-clause text.
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


def test_replay_xml_2001_101_preserves_section_24_sparse_item_tail_from_2017_169() -> None:
    from lawvm.core.ir_helpers import irnode_to_text

    master = pinned_replay("2001/101", mode="finlex_oracle", quiet=True)
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

    master = pinned_replay("1996/1093", mode="finlex_oracle", quiet=True)
    sec = master.find_section("18", chapter_num="5")

    assert sec is not None

    subsections = [child for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2", "3", "4"]
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
    master = pinned_replay("2017/444", mode="finlex_oracle", quiet=True)

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


def test_replay_xml_2003_549_replaces_occupied_section_163_without_stale_tail() -> None:
    """A complete same-label section INSERT must suppress stale old subsection tail.

    Regression family: `2003/549 <- 2011/682` compiles `163 §` as
    `INSERT 12 luku 163 §`. Replay already replaced the occupied section root,
    but the replacement content did not carry exact whole-section tail policy,
    so PIT materialization kept stale older `3` and `4 momentti` timelines.
    """
    master = pinned_replay("2003/549", mode="finlex_oracle", quiet=True)
    sec = master.find_section("163", chapter_num="12")

    assert sec is not None
    assert [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION] == ["1", "2"]

    text = " ".join(irnode_to_text(sec).split())
    assert "asian uudelleen ratkaiseminen takautuvasti myönnetyn ensisijaisen etuuden" in text.lower()
    assert "3 momentti" not in text.lower()
    assert "4 momentti" not in text.lower()


def test_inspect_amendment_2003_549_2010_469_prunes_carried_section_149_subsections() -> None:
    """`2010/469` section 149 must bind owned `1 momentti` edits to slot 1.

    The amendment XML carries later sibling subsections `2–5` inside the same
    section body, even though the johtolause only changes `149 § 1 momentti`
    plus item-level edits under that moment. Current inspection keeps the
    carried sibling slots visible as unassigned source context rather than
    hiding them through pre-replay pruning.
    """
    bundle = build_amendment_bundle("2003/549", "2010/469", mode="finlex_oracle")
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
    bundle = build_amendment_bundle("2003/549", "2006/1293", mode="finlex_oracle")
    group = next(group for group in bundle["groups"] if group["target_norm"] == "149")

    assert group["ops_raw"] == [
        "REPLACE 149 § 1 mom 1 kohta",
        "REPLACE 149 § 1 mom 2 kohta",
        "REPLACE 149 § 1 mom 3 kohta",
        "REPLACE 149 § 4 mom",
    ]
    assert group["ops_after_normalization"] == group["ops_raw"]


def test_inspect_amendment_2005_579_2014_751_drops_language_variant_plain_replaces_for_section_9() -> None:
    bundle = build_amendment_bundle("2005/579", "2014/751", mode="finlex_oracle")
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


def test_inspect_amendment_2014_527_2019_49_keeps_section_149b_between_149a_and_149c() -> None:
    bundle = build_amendment_bundle("2014/527", "2019/49", mode="finlex_oracle")
    targets = [group["target_norm"] for group in bundle["groups"]]

    assert "149" in targets
    assert "149a" in targets
    assert "149b" in targets
    assert "149c" in targets


def test_inspect_amendment_2014_527_2022_490_reports_pre_merge_whole_section_constraint_shape() -> None:
    bundle = build_amendment_bundle("2014/527", "2022/490", mode="finlex_oracle")
    group221c = next(group for group in bundle["groups"] if group["target_norm"] == "221c")

    assert group221c["ops_raw"] == ["REPLACE 221c § otsikko", "REPLACE 221c § 1 mom"]
    assert set(group221c["ops_final"]) == {"REPLACE 221c § otsikko", "REPLACE 221c § 1 mom"}
    assert group221c["subsection_map"][0]["op"] == "REPLACE 221c § otsikko"
    assert group221c["subsection_map"][0]["mapped_payload"] is None
    assert group221c["subsection_map"][1]["op"] == "REPLACE 221c § 1 mom"
    assert group221c["subsection_map"][1]["mapped_payload"]["label"] == "1"
    assert group221c["rejected_ops_pre_constraints"] == []
    assert group221c["rejected_ops_post_constraints"] == []


def test_inspect_amendment_1965_40_1989_612_keeps_section_25_1a_in_explicit_chapter() -> None:
    bundle = build_amendment_bundle("1965/40", "1989/612", mode="finlex_oracle")
    group1a = next(group for group in bundle["groups"] if group["target_norm"] == "1a")
    group7 = next(group for group in bundle["groups"] if group["target_norm"] == "7")

    assert group1a["target_chapter"] == "25"
    assert group1a["ops_final"] == ["INSERT 25 luku 1a §"]
    assert group7["target_chapter"] == "25"
    assert group7["ops_final"] == ["REPLACE 25 luku 7 §"]


def test_inspect_amendment_1965_40_2004_783_keeps_section_19_12a_in_explicit_chapter() -> None:
    bundle = build_amendment_bundle("1965/40", "2004/783", mode="finlex_oracle")
    group12a = next(group for group in bundle["groups"] if group["target_norm"] == "12a")

    assert group12a["target_chapter"] == "19"
    assert group12a["ops_final"] == ["INSERT 19 luku 12a §"]


def test_inspect_amendment_1940_378_1995_1392_aligns_sparse_omission_replace_to_subsection_2() -> None:
    bundle = build_amendment_bundle("1940/378", "1995/1392", mode="finlex_oracle")
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
    bundle = build_amendment_bundle("1940/378", "1994/318", mode="finlex_oracle")
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


def test_build_amendment_bundle_2012_980_2022_604_applies_johtolause_corrigendum_to_repeal_target() -> None:
    bundle = build_amendment_bundle("2012/980", "2022/604", mode="finlex_oracle")

    descriptions = bundle["compiled_ops"]

    assert "REPEAL 2 § 3 mom" in descriptions
    assert "REPEAL 2 § 2 mom" not in descriptions


def test_emit_restructure_plan_renumber_legal_operations_emits_explicit_renumber_lo() -> None:
    from lawvm.core.ir import LegalAddress
    from lawvm.core.provenance import MigrationEvent
    from lawvm.finland.grafter import _emit_restructure_plan_renumber_legal_operations

    lo_ops: list = []
    emitted = _emit_restructure_plan_renumber_legal_operations(
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


def test_ambiguous_unscoped_additive_fallback_insert_observation() -> None:
    existing_ops = [
        AmendmentOp(
            op_id="c1",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="1",
        ),
        AmendmentOp(
            op_id="c2",
            op_type="INSERT",
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="2",
        ),
    ]
    fallback_insert = AmendmentOp(
        op_id="fb",
        op_type="INSERT",
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
    assert finding.detail["candidate_chapters"] == ["1", "2"]


def test_ambiguous_unscoped_additive_fallback_insert_observation_keeps_unique_scope() -> None:
    existing_ops = [
        AmendmentOp(
            op_id="c1",
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section="4",
            target_paragraph=1,
            target_chapter="1",
        ),
    ]
    fallback_insert = AmendmentOp(
        op_id="fb",
        op_type="INSERT",
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
        AmendmentOp(op_type="REPLACE", target_section="23", target_unit_kind="section"),
        AmendmentOp(op_type="REPLACE", target_section="24c", target_unit_kind="section", target_paragraph=3),
        AmendmentOp(op_type="REPLACE", target_kind=TargetKind.CHAPTER, target_section="7"),
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
    op = AmendmentOp(op_type="REPLACE", target_section="24c", target_unit_kind="section")

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
    heading_op = AmendmentOp(op_type="REPLACE", target_unit_kind="section", target_section="8")
    child_op = AmendmentOp(op_type="REPLACE", target_unit_kind="section", target_section="8", target_paragraph=3)

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
    assert patched[0].target_special is None
    assert patched[0].preserve_explicit_heading_facet is True
    assert patched[1].description() == "REPLACE 8 § 3 mom"
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
    repeal = AmendmentOp(
        op_id="fb",
        op_type="REPEAL",
        target_kind=TargetKind.SECTION,
        target_section="1",
    )

    kept, findings = _reject_overbroad_section_repeals_for_deep_targets(
        [repeal],
        johto="Tällä päätöksellä kumotaan päätöksen 1 §:n 3.3.2. kohta.",
        amendment_id="2007/180",
    )

    assert kept == []
    assert len(findings) == 1
    assert findings[0].detail["reason_code"] == "ELAB.OVERBROAD_SECTION_REPEAL_FOR_DEEP_TARGET"


def test_reject_overbroad_section_repeal_for_deep_target_keeps_plain_section_repeal() -> None:
    repeal = AmendmentOp(
        op_id="fb",
        op_type="REPEAL",
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


def test_inspect_amendment_1994_674_2016_860_keeps_section_1_inside_new_chapter_11a() -> None:
    bundle = build_amendment_bundle("1994/674", "2016/860", mode="finlex_oracle")
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


def test_inspect_amendment_1994_674_2019_1401_shows_whole_chapter_replace_not_heading_only() -> None:
    bundle = build_amendment_bundle("1994/674", "2019/1401", mode="finlex_oracle")
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
    bundle = build_amendment_bundle("2011/1552", "2022/1188", mode="finlex_oracle")

    assert bundle["route"]["should_apply"] is False
    assert bundle["route"]["reason"] == "pending_amendment_of_parent_skip"
    assert bundle["route"]["target_amendment_id"] == "2022/631"


def test_inspect_amendment_2011_1552_2022_708_reports_pending_amendment_skip_family() -> None:
    bundle = build_amendment_bundle("2011/1552", "2022/708", mode="finlex_oracle")

    assert bundle["route"]["should_apply"] is False
    assert bundle["route"]["reason"] == "pending_amendment_of_parent_skip"
    assert bundle["route"]["target_amendment_id"] == "2020/1233"


def test_process_muutoslaki_2011_1552_composes_pending_amendment_on_processed_target() -> None:
    replay = pinned_replay("2011/1552", mode="finlex_oracle", quiet=True)
    findings = list(replay.findings or [])

    assert any(
        str(f.kind or "") == "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET"
        and str(f.source_statute or "") in {"2022/708", "2022/1188"}
        for f in findings
    )


def test_inspect_amendment_2013_588_2025_201_owns_sparse_higher_moment_and_trailing_insert_bindings() -> None:
    bundle = build_amendment_bundle("2013/588", "2025/201", mode="finlex_oracle")
    group21b = next(group for group in bundle["groups"] if group["target_norm"] == "21b")
    group87 = next(group for group in bundle["groups"] if group["target_norm"] == "87" and group["target_part"] == "5")
    group87_insert = next(
        group
        for group in bundle["groups"]
        if group["target_norm"] == "87" and group["ops_final"] == ["INSERT 13 luku 87 § 6 mom"]
    )

    assert group21b["ops_final"] == ["REPLACE 21b § 2 mom"]
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group21b["elaboration_observations"]
    )
    assert all(
        observation["kind"] not in {"ELAB.AMBIGUOUS_BINDING", "ELAB.LOCAL_DENSE_SUBSECTION_NUMBERING"}
        for observation in group21b["elaboration_observations"]
    )

    assert group87["target_part"] == "5"
    assert group87["target_chapter"] == "13"
    assert group87["ops_final"] == ["REPLACE 13 luku 87 § 1 mom"]
    assert group87_insert["target_chapter"] == "13"
    assert group87_insert["ops_final"] == ["INSERT 13 luku 87 § 6 mom"]
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
    bundle = build_amendment_bundle("2012/1020", "2024/776", mode="finlex_oracle")
    group1 = next(group for group in bundle["groups"] if group["target_norm"] == "1")
    group7 = next(group for group in bundle["groups"] if group["target_norm"] == "7")

    assert group1["ops_final"] == ["INSERT 1 luku 1 § 5 mom", "INSERT 1 luku 1 § 6 mom"]
    assert group7["ops_final"] == [
        "REPLACE 2 luku 7 § 1 mom 4 kohta",
        "INSERT 2 luku 7 § 1 mom 5 kohta",
        "INSERT 2 luku 7 § 6 mom",
    ]


def test_inspect_amendment_2012_1020_2015_1328_keeps_bare_johdanto_targets_and_later_section_refs_alive() -> None:
    bundle = build_amendment_bundle("2012/1020", "2015/1328", mode="finlex_oracle")

    got = {group["target_norm"]: group["ops_final"] for group in bundle["groups"]}

    assert got["1"] == ["REPLACE 1 luku 1 §"]
    assert got["2"] == ["REPLACE 2 luku 2 § otsikko"]
    assert got["11"] == ["REPEAL 5 luku 11 § 1 mom 4 kohta"]


def test_inspect_amendment_2013_588_2025_201_recovers_section_49a_item_10_insert() -> None:
    bundle = build_amendment_bundle("2013/588", "2025/201", mode="finlex_oracle")
    group49a = next(group for group in bundle["groups"] if group["target_norm"] == "49a")

    assert group49a["ops_raw"] == ["REPLACE 5 luku 49a § 1 mom 9 kohta", "INSERT 5 luku 49a § 1 mom 10 kohta"]
    assert group49a["ops_final"] == ["REPLACE 5 luku 49a § 1 mom 9 kohta", "INSERT 5 luku 49a § 1 mom 10 kohta"]


def test_inspect_amendment_2002_780_2003_666_keeps_head_insert_and_renumber_group() -> None:
    bundle = build_amendment_bundle("2002/780", "2003/666", mode="legal_pit")
    group4 = next(group for group in bundle["groups"] if group["target_norm"] == "4")

    assert group4["ops_raw"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]
    assert group4["ops_after_normalization"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]
    assert group4["ops_final"] == ["RENUMBER 4 § 1 mom", "INSERT 4 § 1 mom"]


def test_replay_xml_2013_588_restores_section_49a_item_10_after_2025_201() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)
    sec = replay.materialized_state.find_section("49a", "5")

    assert sec is not None
    sub1 = next(
        child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    para_labels = [child.label for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH]

    assert "10" in para_labels
    assert "tietojen säilyttäminen" in irnode_to_text(sub1)


def test_replay_xml_2013_588_routes_section_87_only_under_chapter_13_after_2025_201() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)
    state = replay.materialized_state
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
    bundle = build_amendment_bundle("2021/82", "2024/495", mode="finlex_oracle")
    group1a = next(group for group in bundle["groups"] if group["target_norm"] == "1a")
    group83a = next(group for group in bundle["groups"] if group["target_norm"] == "83a")

    assert group1a["ops_raw"] == ["INSERT 1 luku 1a § 5 mom"]
    assert group1a["ops_final"] == ["INSERT 1 luku 1a § 5 mom"]
    assert group83a["ops_raw"] == ["INSERT 4 luku 83a §"]
    assert group83a["ops_final"] == ["INSERT 4 luku 83a §"]


def test_replay_xml_2021_82_restores_section_1a_fifth_moment_after_2024_495() -> None:
    replay = pinned_replay("2021/82", mode="finlex_oracle", quiet=True)
    sec = replay.materialized_state.find_section("1a", "1")

    assert sec is not None
    sub_labels = [child.label for child in sec.children if child.kind is IRNodeKind.SUBSECTION]
    assert "5" in sub_labels

    sub2 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")
    sub5 = next(child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "5")

    assert "Ajoneuvoon, jota saa käyttää yksinomaan yleiseltä liikenteeltä eristetyllä alueella" in irnode_to_text(sub2)
    assert "Puolustusyhteistyöstä Suomen tasavallan hallituksen ja Amerikan yhdysvaltojen hallituksen välillä" in irnode_to_text(sub5)


def test_replay_xml_2009_1599_restores_section_8_after_2023_280_same_wave_shift_family() -> None:
    replay = pinned_replay("2009/1599", stop_before="2023/152", mode="finlex_oracle", quiet=True)
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
    bundle = build_amendment_bundle("1996/1266", "2012/963", mode="finlex_oracle")
    group30 = next(group for group in bundle["groups"] if group["target_norm"] == "30")

    assert group30["ops_raw"] == ["REPLACE 30 §"]
    assert group30["ops_final"] == ["REPLACE 30 §"]


def test_replay_xml_1996_1266_updates_section_30_after_2012_963() -> None:
    replay = pinned_replay("1996/1266", mode="finlex_oracle", quiet=True)
    sec = replay.materialized_state.find_section("30")

    assert sec is not None
    text = irnode_to_text(sec)
    assert "Tulli voi hakemuksesta antaa luvan" in text
    assert "Tullihallitus voi hakemuksesta antaa luvan" not in text


def test_inspect_amendment_1959_191_1992_203_keeps_following_targets_after_included_heading() -> None:
    bundle = build_amendment_bundle("1959/191", "1992/203", mode="finlex_oracle")
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
    replay = pinned_replay("1959/191", mode="finlex_oracle", quiet=True)
    sec = replay.materialized_state.find_section("53")

    assert sec is not None
    text = irnode_to_text(sec)
    assert "Ennen 44 ja 47 §:n 2 momentissa mainittuihin toimenpiteisiin" in text
    assert "Ennen 44 ja 45 §:ssä tarkoitetut" not in text




def test_replay_xml_2013_588_restores_sections_21a_and_21b_from_2023_497() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)

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
    bundle = build_amendment_bundle("2013/588", "2019/108", mode="finlex_oracle")
    group11a = next(group for group in bundle["groups"] if group["target_unit_kind"] == "chapter" and group["target_norm"] == "11a")
    group87 = next(group for group in bundle["groups"] if group["target_norm"] == "87")

    assert group87["ops_final"] == ["REPLACE 87 § 2 mom"]
    assert any(
        observation["kind"] == "ELAB.CONTAINER_PRUNED_SHADOWED"
        and "87" in observation.get("detail", {}).get("pruned_sections", [])
        for observation in group11a["elaboration_observations"]
    )


def test_replay_xml_2013_588_does_not_keep_section_87_under_chapter_11a_after_2019_108() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)
    materialized = extract_ir_sections(replay.products.materialized_state.ir)

    assert "part:4/chapter:11a/section:87" not in materialized


def test_inspect_amendment_2013_588_2023_497_owns_sparse_higher_moment_binding_for_section_93() -> None:
    bundle = build_amendment_bundle("2013/588", "2023/497", mode="finlex_oracle")
    group93 = next(group for group in bundle["groups"] if group["target_norm"] == "93")

    assert group93["ops_final"] == ["REPLACE 93 § 4 mom"]
    assert group93["sparse_slot_bindings"][0]["slot_label"] == "4"
    assert any(
        observation["kind"] == "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
        for observation in group93["elaboration_observations"]
    )
    assert all(
        observation["kind"] != "ELAB.AMBIGUOUS_BINDING"
        for observation in group93["elaboration_observations"]
    )


def test_replay_xml_2013_588_updates_section_93_subsection_4_after_2023_497() -> None:
    replay = pinned_replay("2013/588", mode="finlex_oracle", quiet=True)
    sections = extract_ir_sections(replay.products.materialized_state.ir)
    sec93 = sections["part:5/chapter:13/section:93"]
    sub4 = next(
        child for child in sec93.children if child.kind is IRNodeKind.SUBSECTION and child.label == "4"
    )
    text4 = " ".join(irnode_to_text(sub4).split())

    assert "onko loppukäyttäjällä oikeus irtisanoa sopimus" in text4
    assert "kuluttajan osalta aikaisintaan kuukauden ja muun loppukäyttäjän osalta aikaisintaan kahden viikon" in text4
    assert "Tämän momentin säännöksistä ei saa poiketa loppukäyttäjän vahingoksi." in text4
    assert "onko sopijapuolella oikeus irtisanoa sopimus" not in text4


def test_replay_xml_2014_527_keeps_section_221c_subsection_2_after_2022_490() -> None:
    replay = pinned_replay("2014/527", mode="finlex_oracle", quiet=True)
    sec221c = replay.materialized_state.find_section("221c", "20")

    assert sec221c is not None
    subsections = [child for child in sec221c.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]

    sub2 = next(child for child in subsections if child.label == "2")
    text2 = " ".join(irnode_to_text(sub2).split())

    assert "Edellä 1 momentissa tarkoitetun energiantuotantoyksikön ympäristöluvanvaraisuuteen" in text2
    assert "Eläimistä saatavista sivutuotteista annetussa laissa" in text2


def test_replay_xml_2005_579_preserves_section_9_structure_after_2013_1230_and_2014_751() -> None:
    master = pinned_replay("2005/579", mode="finlex_oracle", quiet=True)
    sec = master.find_section("9", chapter_num="1")

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


def test_replay_xml_2003_549_keeps_section_149_subsection_4_as_wrapup_only() -> None:
    """`149 § 4 momentti` must remain the wrap-up paragraph, not a duplicated item list."""
    master = pinned_replay("2003/549", mode="finlex_oracle", quiet=True)
    sec = master.find_section("149", chapter_num="11")

    assert sec is not None
    sub4 = next(
        child for child in sec.children if child.kind is IRNodeKind.SUBSECTION and child.label == "4"
    )
    sub4_text = " ".join(irnode_to_text(sub4).split())

    assert "Tämän pykälän perusteella avatun teknisen käyttöyhteyden avulla" in sub4_text
    assert "1)" not in sub4_text


def test_replay_xml_2003_549_applies_shifted_subsection_insert_for_section_53() -> None:
    """`2009/925` must preserve the shifted old 6 momentti as the new 7 momentti."""
    master = pinned_replay("2003/549", as_of="2010-01-02", mode="finlex_oracle", quiet=True)
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
    master = pinned_replay("1987/693", mode="finlex_oracle", quiet=True)

    sec10d = master.find_section("10d")
    sec10e = master.find_section("10e")

    assert sec10d is not None
    assert sec10e is not None

    sec10d_text = " ".join(irnode_to_text(sec10d).split())
    sec10e_text = " ".join(irnode_to_text(sec10e).split())

    assert "Samaa vaikuttavaa ainetta sisältäville" in sec10d_text
    assert "Erityislupa myönnetään enintään yhden vuoden hoitoa varten" in sec10e_text


def test_replay_xml_2005_579_preserves_section_39_sparse_omission_items_and_later_item_insert() -> None:
    """`39 §` must preserve omitted sibling items and the later inserted `8 kohta`."""
    master = pinned_replay("2005/579", mode="finlex_oracle", quiet=True)
    sec = master.find_section("39", chapter_num="4")

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
    target_mid, labels, expiry = result
    assert expiry == dt.date(2021, 6, 30), f"Expected 2021-06-30, got {expiry}"
    # Primary group: 58c–58h range and 59a–59e range
    for sec in ["58c", "58d", "58e", "58f", "58g", "58h", "59a", "59b", "59c", "59d", "59e"]:
        assert sec in labels, f"§{sec} should be in expiry labels"
    # Secondary group: §91 from the 'sekä 91 §:n 1 momentti' clause
    assert "91" in labels, "§91 from sekä-clause should be in expiry labels"


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
    target_mid, labels, expiry = result
    assert expiry == dt.date(2021, 12, 31)
    for sec in ["16a", "16b", "16c", "16d", "16e", "16f", "16g"]:
        assert sec in labels, f"§{sec} should be in expiry labels"


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

    # Hint "iva" doesn't exist in master — fall through to heuristic
    path = _find_chapter_insert_parent_path(master, "17a", part_hint="iva")
    # Heuristic picks part:3 (ch17 < 17a)
    assert path[-1] == ("part", "3"), (
        f"nonexistent hint should fall through to heuristic (part:3), got {path}"
    )


def test_replay_xml_2004_137_restores_section_4_split_moments_from_2017_367() -> None:
    result = pinned_replay("2004/137", mode="finlex_oracle", quiet=True)

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
    result = pinned_replay("2016/1503", mode="finlex_oracle", quiet=True)

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
    result = pinned_replay("2007/1024", mode="finlex_oracle", quiet=True)

    sec2 = result.find_section("2")
    assert sec2 is not None

    subs = [c for c in sec2.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subs] == ["1", "2"], (
        f"Expected exactly 2 subsections ['1','2'], got {[s.label for s in subs]!r}"
    )
    sub2_text = irnode_to_text(subs[1])
    assert "Ministeriön toimialaan kuuluvat myös seuraavia valtionyhtiöitä koskevat asiat" in sub2_text
    assert "Finnvera Oyj" in sub2_text
    assert "Työkanava Oy" in sub2_text


def test_replay_xml_2007_1024_section_3_restored_after_2020_818() -> None:
    """Regression: 2020/818 johtolause contained a U+200D zero-width joiner in '3‌ §:n'
    which caused the PEG parser to fail to detect the REPLACE op for section:3 subsection:1.
    After fixing Cf-character stripping in metadata normalisation, section:3 should have 3
    subsections with the correct content."""
    result = pinned_replay("2007/1024", mode="finlex_oracle", quiet=True)

    sec3 = result.find_section("3")
    assert sec3 is not None

    subs = [c for c in sec3.children if c.kind == IRNodeKind.SUBSECTION]
    assert [s.label for s in subs] == ["1", "2", "3"], (
        f"Expected subsections ['1','2','3'], got {[s.label for s in subs]!r}"
    )
    sub3_text = irnode_to_text(subs[2])
    assert "Osaston ja toimintayksiköiden sisäisestä organisaatiosta" in sub3_text

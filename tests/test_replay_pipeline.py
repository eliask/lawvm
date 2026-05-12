from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from lawvm.core.ir import IRNode
from lawvm.core.ir import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.tree_ops import check_invariants
from lawvm.finland.chapter_seed import ChapterSeedDiagnostic
from lawvm.finland.replay_pipeline import ReplayPlan, execute_replay_plan, prepare_replay_plan
from lawvm.corpus_store import CorpusStore
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.finland.vts import (
    VTS_SKIPPED_TARGET_RULE_ID,
    VTS_SOURCE_DIAGNOSTIC_RULE_ID,
    VtsSkippedTarget,
    VtsSourceDiagnostic,
)


def _corpus_stub() -> CorpusStore:
    return cast(CorpusStore, object())


def test_execute_replay_plan_records_post_amendment_tree_invariant_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert ctx.id == "test/1"
        return PhaseResult(output=state.with_ir(
            IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1"),
                    IRNode(kind=IRNodeKind.SECTION, label="1"),),
            )
        ))

    final_state = execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
        checkpoint_callback=lambda checkpoint: None,
    )

    assert final_state.ir.kind == IRNodeKind.BODY
    assert any(
        finding.kind == "APPLY.TREE_INVARIANT_VIOLATION"
        and finding.source_statute == "1991/1"
        and finding.detail.get("phase") == "post_amendment"
        and finding.detail.get("barrier_code") == "APPLY.TREE_INVARIANT_VIOLATION"
        and finding.role == "violation"
        and finding.blocking is True
        for finding in findings
    )


def test_execute_replay_plan_collects_phase_result_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert ctx.id == "test/1"
        assert "_adjudications_out" not in kwargs
        return PhaseResult(
            output=state,
            findings=(
                Finding(
                    kind="APPLY.STRICT_REJECTED_UNCOVERED_BODY",
                    role="obligation",
                    stage="process_muutoslaki",
                    detail={"message": "strict rejection"},
                    source_statute=mid,
                    blocking=True,
                ),
            ),
        )

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )

    assert any(
        finding.kind == "APPLY.STRICT_REJECTED_UNCOVERED_BODY"
        and finding.source_statute == "1991/1"
        and finding.role == "obligation"
        and finding.blocking is True
        for finding in findings
    )


def test_execute_replay_plan_records_chapter_seed_repair_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []

    def fake_seed_missing_chapters(ir, mids, corpus, diagnostics_out=None):
        assert mids == ["1991/1"]
        assert diagnostics_out is not None
        diagnostics_out.append(
            ChapterSeedDiagnostic(
                rule_id="fi_chapter_seed_inserted_from_amendment_body",
                family="ontology_normalization",
                phase="payload_normalization",
                reason="seeded missing chapter",
                source_statute="1991/1",
                chapter_label="7",
                blocking=False,
                strict_disposition="block",
                quirks_disposition="apply",
            )
        )
        return ir, {("7", "1991/1")}

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert kwargs.get("chapter_seed_skip") == {("7", "1991/1")}
        return PhaseResult(output=state)

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=fake_seed_missing_chapters,
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )

    assert [(finding.kind, finding.role, finding.blocking) for finding in findings] == [
        ("ELAB.CHAPTER_SEED_REPAIR", "observation", False)
    ]
    assert findings[0].source_statute == "1991/1"
    assert findings[0].detail["rule_id"] == "fi_chapter_seed_inserted_from_amendment_body"
    assert findings[0].detail["chapter_label"] == "7"
    assert findings[0].detail["strict_disposition"] == "block"
    assert findings[0].detail["quirks_disposition"] == "apply"


def test_execute_replay_plan_records_chapter_seed_source_pathology_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []

    def fake_seed_missing_chapters(ir, mids, corpus, diagnostics_out=None):
        assert diagnostics_out is not None
        diagnostics_out.append(
            ChapterSeedDiagnostic(
                rule_id="fi_chapter_seed_source_missing",
                family="source_pathology",
                phase="acquisition",
                reason="source unavailable during chapter seed scan",
                source_statute="1991/1",
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
            )
        )
        return ir, set()

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=lambda mid, state, ctx, **kwargs: PhaseResult(output=state),
        seed_missing_chapters=fake_seed_missing_chapters,
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )

    assert [(finding.kind, finding.role, finding.blocking) for finding in findings] == [
        ("ELAB.CHAPTER_SEED_SOURCE_PATHOLOGY", "obligation", True)
    ]
    assert findings[0].source_statute == "1991/1"
    assert findings[0].detail["rule_id"] == "fi_chapter_seed_source_missing"
    assert findings[0].detail["family"] == "source_pathology"
    assert findings[0].detail["phase"] == "acquisition"
    assert findings[0].detail["strict_disposition"] == "block"


def test_execute_replay_plan_projects_vts_prescan_skipped_targets_as_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Parent Law",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []
    seen_parent_titles: list[str] = []

    def fake_pre_scan(
        mids,
        corpus,
        parent_id,
        *,
        parent_title="",
        cutoff_date=None,
        vts_skipped_targets_out=None,
        vts_source_diagnostics_out=None,
    ):
        assert mids == ["1991/1"]
        assert parent_id == "test/1"
        assert cutoff_date is None
        assert vts_source_diagnostics_out is not None
        assert vts_skipped_targets_out is not None
        seen_parent_titles.append(parent_title)
        vts_skipped_targets_out.append(
            VtsSkippedTarget(
                rule_id=VTS_SKIPPED_TARGET_RULE_ID,
                reason_code="unsupported_subitem_target",
                source_reason="subitem VTS target is not lowerable",
                source_statute="1991/1",
                source_excerpt="1 §:n 2 momentin 3 kohdan a alakohta.",
                target_section="1",
                target_paragraph=2,
                target_item="3",
                target_subitem="a",
            )
        )
        return [set()]

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=lambda mid, state, ctx, **kwargs: PhaseResult(output=state),
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=fake_pre_scan,
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )

    assert seen_parent_titles == ["Parent Law"]
    assert [(finding.kind, finding.role, finding.blocking) for finding in findings] == [
        (VTS_SKIPPED_TARGET_RULE_ID, "observation", False)
    ]
    assert findings[0].stage == "frontend_extraction"
    assert findings[0].source_statute == "1991/1"
    assert findings[0].detail["reason_code"] == "unsupported_subitem_target"
    assert findings[0].detail["prescan_phase"] == "future_repeal_scan"


def test_execute_replay_plan_projects_vts_prescan_source_diagnostics_as_findings() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Parent Law",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    findings: list[Finding] = []

    def fake_pre_scan(
        mids,
        corpus,
        parent_id,
        *,
        parent_title="",
        cutoff_date=None,
        vts_skipped_targets_out=None,
        vts_source_diagnostics_out=None,
    ):
        assert vts_skipped_targets_out is not None
        assert vts_source_diagnostics_out is not None
        assert parent_title == "Parent Law"
        vts_source_diagnostics_out.append(
            VtsSourceDiagnostic(
                rule_id=VTS_SOURCE_DIAGNOSTIC_RULE_ID,
                reason_code="no_candidate_containers",
                source_reason="no body sections available for VTS scan",
                source_statute="1991/1",
                source_excerpt="",
            )
        )
        return [set()]

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=lambda mid, state, ctx, **kwargs: PhaseResult(output=state),
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=fake_pre_scan,
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        findings_out=findings,
    )

    assert [(finding.kind, finding.role, finding.blocking) for finding in findings] == [
        (VTS_SOURCE_DIAGNOSTIC_RULE_ID, "observation", False)
    ]
    assert findings[0].stage == "frontend_extraction"
    assert findings[0].detail["reason_code"] == "no_candidate_containers"
    assert findings[0].detail["prescan_phase"] == "future_repeal_scan"


def test_prepare_replay_plan_dedupes_consecutive_identical_amendment_records() -> None:
    records = [
        {
            "sequence": 24,
            "statute_id": "2003/741",
            "title": "Duplicate repeal",
            "effective_date": "2004-01-01",
            "issue_date": "2003-08-15",
            "sort_mode": "legal_pit",
            "included": True,
        },
        {
            "sequence": 25,
            "statute_id": "2003/741",
            "title": "Duplicate repeal",
            "effective_date": "2004-01-01",
            "issue_date": "2003-08-15",
            "sort_mode": "legal_pit",
            "included": True,
        },
        {
            "sequence": 26,
            "statute_id": "2005/29",
            "title": "Next amendment",
            "effective_date": "2005-04-01",
            "issue_date": "2005-01-21",
            "sort_mode": "legal_pit",
            "included": True,
        },
    ]

    plan = prepare_replay_plan(
        "test/1",
        mode="legal_pit",
        strict_profile=None,
        corpus=cast(CorpusStore, SimpleNamespace(read_source=lambda _sid: b"<body/>")),
        stop_before="",
        label_postprocessor=lambda _sid, label: label,
        get_replay_profile=lambda _mode: SimpleNamespace(normalize_replay_text=False),
        resolve_applicable_amendment_records=lambda _sid, _mode, corpus=None: (records, None, ""),
        get_consolidated_oracle_suspect=lambda _sid, corpus=None: None,
        extract_inline_corrections=lambda xml_bytes, _sid: ([], xml_bytes),
    )

    assert [record["statute_id"] for record in plan.amendment_records] == ["2003/741", "2005/29"]
    assert plan.amendment_ids == ["2003/741", "2005/29"]


def test_execute_replay_plan_does_not_pass_internal_adjudication_sink() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert "_adjudications_out" not in kwargs
        return PhaseResult(output=state)

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
    )


def test_execute_replay_plan_passes_mutation_events_sink_to_process_muutoslaki() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    mutation_events = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert kwargs.get("mutation_events_out") is mutation_events
        mutation_events.append({"mid": mid, "kind": "fake"})
        return PhaseResult(output=state)

    final_state = execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        mutation_events_out=mutation_events,
    )

    assert final_state.ir.kind == IRNodeKind.BODY
    assert mutation_events == [{"mid": "1991/1", "kind": "fake"}]


def test_execute_replay_plan_passes_sparse_leftovers_sink_to_process_muutoslaki() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    leftovers = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert kwargs.get("sparse_leftovers_out") is leftovers
        leftovers.append({"mid": mid, "kind": "fake_leftover"})
        return PhaseResult(output=state)

    final_state = execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        sparse_leftovers_out=leftovers,
    )

    assert final_state.ir.kind == IRNodeKind.BODY
    assert leftovers == [{"mid": "1991/1", "kind": "fake_leftover"}]


def test_execute_replay_plan_passes_sparse_slot_bindings_sink_to_process_muutoslaki() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    bindings = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        assert mid == "1991/1"
        assert kwargs.get("sparse_slot_bindings_out") is bindings
        bindings.append({"mid": mid, "kind": "fake_binding"})
        return PhaseResult(output=state)

    final_state = execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        sparse_slot_bindings_out=bindings,
    )

    assert final_state.ir.kind == IRNodeKind.BODY
    assert bindings == [{"mid": "1991/1", "kind": "fake_binding"}]


def test_execute_replay_plan_collects_temporal_events_from_phase_result() -> None:
    """execute_replay_plan reads temporal authority from the PhaseResult contract."""
    from lawvm.core.compile_result import TemporalEvent, TemporalScope

    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    collected_events: list = []
    event = TemporalEvent(
        event_id="test:1",
        group_id="test",
        kind="commence",
        scope=TemporalScope(target_statute="test/1"),
        effective="1991-01-01",
        source=OperationSource(statute_id="test/1", effective="1991-01-01"),
    )

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        return PhaseResult(output=state, temporal_events=(event,))

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        temporal_events_out=collected_events,
    )

    assert collected_events == [event]


def test_execute_replay_plan_handles_empty_temporal_events_without_side_channels() -> None:
    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    collected_events: list = []

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        return PhaseResult(output=state, temporal_events=())

    execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
        temporal_events_out=collected_events,
    )

    assert collected_events == []


def test_execute_replay_plan_uses_phase_result_contract_without_optional_side_bags() -> None:
    """execute_replay_plan does not require legacy side-bag arguments."""
    from lawvm.core.compile_result import TemporalEvent, TemporalScope

    plan = ReplayPlan(
        parent_id="test/1",
        replay_mode="legal_pit",
        replay_profile=SimpleNamespace(normalize_replay_text=False),
        ctx=StatuteContext(
            id="test/1",
            title="Test",
            base_ir=IRNode(kind=IRNodeKind.BODY),
            base_xml_bytes=b"<body/>",
        ),
        initial_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        amendment_records=[{"statute_id": "1991/1"}],
        amendment_ids=["1991/1"],
        cutoff_date=None,
        oracle_version_amendment_id="",
        oracle_suspect="",
    )
    event = TemporalEvent(
        event_id="test:1",
        group_id="test",
        kind="commence",
        scope=TemporalScope(target_statute="test/1"),
        effective="1991-01-01",
        source=OperationSource(statute_id="test/1", effective="1991-01-01"),
    )

    def fake_process_muutoslaki(mid, state, ctx, **kwargs):
        return PhaseResult(output=state, temporal_events=(event,))

    final_state = execute_replay_plan(
        plan,
        corpus=_corpus_stub(),
        process_muutoslaki=fake_process_muutoslaki,
        seed_missing_chapters=lambda ir, mids, corpus, diagnostics_out=None: (ir, set()),
        pre_scan_repeal_targets=lambda mids, corpus, parent_id, **kwargs: [],
        future_repeals_for_index=lambda schedule: [set() for _ in schedule],
        post_process_tree=lambda ir, normalize: ir,
        check_tree_invariants=check_invariants,
    )
    assert final_state.ir.kind == IRNodeKind.BODY


def test_check_invariants_allows_content_inside_hcontainer() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(IRNode(kind=IRNodeKind.CONTENT),),
            ),),
    )

    assert check_invariants(body) == []

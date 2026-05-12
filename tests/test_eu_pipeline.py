"""EU replay pipeline-focused regression tests."""
from __future__ import annotations

from pathlib import Path
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.ops_parser import EUOpsParser
from lawvm.eu.pipeline import EUReplayPipeline, EUReplayResult, apply_eu_ops
from lawvm.replay_adjudication import CompileAdjudication


def test_discover_affecting_acts_deduplicates_filters_and_sorts(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request_notice(notice):
        calls.append(notice.celex)
        xml = """
        <TREE>
          <AMENDED_BY_WORK>
            <URI><IDENTIFIER>32000R0001</IDENTIFIER></URI>
          </AMENDED_BY_WORK>
          <MODIFIED_BY_WORK>
            <URI><VALUE>https://publications.europa.eu/resource/celex/32000R0003?foo=bar</VALUE></URI>
          </MODIFIED_BY_WORK>
          <AMENDED_BY_WORK>
            <URI><IDENTIFIER>32000R0001</IDENTIFIER></URI>
          </AMENDED_BY_WORK>
          <HAS_CORRIGENDUM_WORK>
            <URI><VALUE>https://publications.europa.eu/resource/celex/0BAD</VALUE></URI>
          </HAS_CORRIGENDUM_WORK>
          <WORK_HAS_MODIFICATION>
            <URI><IDENTIFIER>0NOTDIGIT</IDENTIFIER></URI>
          </WORK_HAS_MODIFICATION>
          <CORRECTED_BY>
            <URI><IDENTIFIER>32000R0000</IDENTIFIER></URI>
          </CORRECTED_BY>
          <CORRECTED_BY>
            <URI><IDENTIFIER>AX32000R0009</IDENTIFIER></URI>
          </CORRECTED_BY>
          <WORK_HAS_MODIFICATION>
            <URI><VALUE>https://publications.europa.eu/resource/celex/32000R0002</VALUE></URI>
          </WORK_HAS_MODIFICATION>
        </TREE>
        """
        return xml.encode("utf-8"), {"ok": True}

    monkeypatch.setattr("lawvm.eu.pipeline._request_notice", fake_request_notice)

    affecting = EUReplayPipeline(cache_dir=Path(".tmp")).discover_affecting_acts("32000R0000")

    assert calls == ["32000R0000"]
    assert affecting == ["32000R0001", "32000R0002", "32000R0003"]


def test_fetch_amendment_text_strips_html_and_entities(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    monkeypatch.setattr(
        "lawvm.eu.pipeline._request_notice",
        lambda notice: (b"<tree/>", {"ok": True}),
    )

    def _fake_manifestation_option(_notice_path, language: str, manifestation_type: str) -> dict[str, object]:
        assert language == "ENG"
        assert manifestation_type == "xhtml"
        return {
            "items": [{"uri": {"value": "http://example.com/eu.xhtml"}}],
            "manifestation_uri": {"value": "http://example.com/fallback.xhtml"},
        }

    monkeypatch.setattr(
        "lawvm.eu.cellar.select_manifestation_option",
        _fake_manifestation_option,
    )

    html_payload = (
        "<div>Alpha&nbsp;<span>Beta</span> &lsquo;Gamma&rsquo; &rsquo;Delta&rsquo;</div>"
    )

    monkeypatch.setattr(
        "lawvm.eu.cellar._request_url",
        lambda url, accept: (html_payload.encode("utf-8"), {"ok": True}),
    )

    text = pipeline.fetch_amendment_text("32016R0679")

    assert "Alpha" in text
    assert "Beta" in text
    assert "'Gamma'" in text
    assert "'Delta'" in text
    assert "<" not in text and ">" not in text


def _baseline_statute() -> IRStatute:
    return IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section 1"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Section 2"),),
        ),
    )


def _duplicate_text_statute() -> IRStatute:
    shared_text = " ".join(["same", "text"] * 45)
    return IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=shared_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text=shared_text),),
        ),
    )


def test_eu_ops_parser_preserves_mixed_corrigendum_and_ordinary_amendment_lanes() -> None:
    ops = EUOpsParser().extract_ops(
        "In Article 1, for: old text read: new text; Article 2 is replaced."
    )

    assert [(op.op_id, op.sequence, op.action.value, str(op.target)) for op in ops] == [
        ("corrigenda-1", 1, "replace", "article:1"),
        ("eu-compat-2-1", 2, "replace", "article:2"),
    ]


def test_eu_ops_parser_does_not_duplicate_corrigendum_only_text() -> None:
    ops = EUOpsParser().extract_ops("In Article 1, for: old text read: new text.")

    assert [(op.op_id, op.sequence, op.action.value, str(op.target)) for op in ops] == [
        ("corrigenda-1", 1, "replace", "article:1"),
    ]


def test_apply_eu_ops_records_payload_and_target_missing_adjudications(capsys) -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="replace-no-payload",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=None,
            source=OperationSource(statute_id="2026/1"),
        ),
        LegalOperation(
            op_id="replace-article-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("article", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert [a.kind for a in adjudications] == [
        "eu_replay_text_payload_missing",
        "eu_replay_target_not_found",
    ]
    assert adjudications[0].op_id == "replace-no-payload"
    assert adjudications[1].detail["target"] == "section:9"
    assert adjudications[1].source_statute == "2026/2"
    assert replayed.metadata["eu_replay_applied_op_count"] == 0
    assert replayed.metadata["eu_replay_skipped_op_count"] == 2
    assert capsys.readouterr().out == ""


def test_apply_eu_ops_records_insert_parent_not_found() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="insert-parent-missing",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "99"), ("paragraph", "1"))),
            payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="inserted"),
            source=OperationSource(statute_id="2026/3"),
        ),
    ]

    apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "eu_replay_parent_not_found"
    assert adjudications[0].detail["parent_kind"] == "section"
    assert adjudications[0].detail["parent_label"] == "99"


def test_apply_eu_ops_records_unsupported_and_unknown_action() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="unsupported-renumber",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="new"),
            source=OperationSource(statute_id="2026/4"),
        ),
    ]

    apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert [a.kind for a in adjudications] == [
        "eu_replay_unsupported_action",
    ]
    assert adjudications[0].op_id == "unsupported-renumber"
    assert adjudications[0].detail["action"] == "renumber"


def test_apply_eu_ops_records_tree_invariant_violation_after_successful_insert() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="insert-duplicate-section",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="duplicate"),
            source=OperationSource(statute_id="2026/10"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert len(replayed.body.children) == 3
    invariant_adjudications = [
        adjudication
        for adjudication in adjudications
        if adjudication.kind == "eu_replay_tree_invariant_violation"
    ]
    assert len(invariant_adjudications) == 1
    assert invariant_adjudications[0].op_id == "insert-duplicate-section"
    assert invariant_adjudications[0].detail["action"] == "insert"
    assert invariant_adjudications[0].detail["target"] == "section:1"
    assert "duplicate section:1" in invariant_adjudications[0].detail["violation"]


def test_apply_eu_ops_records_new_apply_step_duplication_warning() -> None:
    long_text = " ".join(["same", "text"] * 45)
    baseline = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=long_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Different text."),),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="replace-introduces-duplication",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text=long_text),
            source=OperationSource(statute_id="2026/11"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    duplication_adjudications = [
        adjudication
        for adjudication in adjudications
        if adjudication.kind == "text_duplication_warning"
    ]
    assert replayed.metadata["eu_replay_applied_op_count"] == 1
    assert replayed.metadata["eu_replay_skipped_op_count"] == 0
    assert len(duplication_adjudications) == 1
    assert duplication_adjudications[0].op_id == "replace-introduces-duplication"
    assert duplication_adjudications[0].source_statute == "2026/11"
    assert duplication_adjudications[0].detail["phase"] == "apply_op"
    assert duplication_adjudications[0].detail["blocking"] is False
    assert duplication_adjudications[0].detail["strict_disposition"] == "record"
    assert duplication_adjudications[0].detail["quirks_disposition"] == "record"
    assert isinstance(duplication_adjudications[0].detail["action"], str)
    assert duplication_adjudications[0].detail["action"] == "replace"
    assert duplication_adjudications[0].detail["target"] == "section:2"
    assert duplication_adjudications[0].detail["kind"] == "duplicate_full_text"
    assert duplication_adjudications[0].detail["left"] == "section:1"
    assert duplication_adjudications[0].detail["right"] == "section:2"


def test_apply_eu_ops_does_not_repeat_apply_step_duplication_warning_without_new_surface() -> None:
    long_text = " ".join(["same", "text"] * 45)
    baseline = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=long_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Different text."),
                IRNode(kind=IRNodeKind.SECTION, label="3", text="Still different text."),),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="replace-introduces-duplication",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text=long_text),
            source=OperationSource(statute_id="2026/11"),
        ),
        LegalOperation(
            op_id="replace-keeps-surface-stable",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "3"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="3", text="Replaced unique text."),
            source=OperationSource(statute_id="2026/12"),
        ),
    ]

    apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    duplication_adjudications = [
        adjudication
        for adjudication in adjudications
        if adjudication.kind == "text_duplication_warning"
        and adjudication.detail.get("phase") == "apply_op"
    ]
    assert len(duplication_adjudications) == 1
    assert duplication_adjudications[0].op_id == "replace-introduces-duplication"


def test_replay_statute_collects_eu_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return [
            LegalOperation(
                op_id="replace-missing",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "9"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            ),
        ]

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        return "timelines"

    class _Pit(IRStatute):
        pass

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return _Pit(
            statute_id=base.statute_id,
            title=base.title,
            body=base.body,
            supplements=tuple(base.supplements),
            metadata=dict(base.metadata),
        )

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")
    assert len(result.adjudications) == 1
    assert result.adjudications[0].kind == "eu_replay_target_not_found"
    assert result.adjudications[0].op_id == "replace-missing"


def test_apply_eu_ops_maps_article_targets_to_section_kind() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="article-target",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("article", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="replacement"),
            source=OperationSource(statute_id="2026/6"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert not adjudications
    assert [child.text for child in replayed.body.children] == [
        "replacement",
        "Section 2",
    ]


def test_apply_eu_ops_maps_path_kinds_case_insensitive() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="article-target-upper",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("ARTICLE", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="replacement"),
            source=OperationSource(statute_id="2026/8"),
        ),
        LegalOperation(
            op_id="insert-point-upper",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("SECTION", "1"), ("POINT", "9"))),
            payload=IRNode(kind=IRNodeKind.ITEM, label="9", text="inserted"),
            source=OperationSource(statute_id="2026/9"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert not adjudications
    first_section = replayed.body.children[0]
    assert first_section.children == (IRNode(kind=IRNodeKind.ITEM, label="9", text="inserted"),)


def test_apply_eu_ops_maps_insert_parent_and_payload_kinds() -> None:
    baseline = _baseline_statute()
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="insert-point-into-article",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("article", "1"), ("point", "1"))),
            payload=IRNode(kind=IRNodeKind.ITEM, label="1", text="inserted"),
            source=OperationSource(statute_id="2026/7"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert not adjudications
    first_section = replayed.body.children[0]
    assert first_section.children == (IRNode(kind=IRNodeKind.ITEM, label="1", text="inserted"),)


def test_replay_statute_collects_text_duplication_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _duplicate_text_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return []

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")
    duplicate_phases = [
        adjudication.detail.get("phase")
        for adjudication in result.adjudications
        if adjudication.kind == "text_duplication_warning"
    ]

    assert set(duplicate_phases) == {"replay_fold", "materialized"}


def test_replay_statute_collects_apply_step_and_phase_duplication_adjudications(
    monkeypatch,
    tmp_path,
) -> None:
    long_text = " ".join(["same", "text"] * 45)
    baseline = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=long_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Different text."),),
        ),
    )
    duplicated = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=long_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text=long_text),),
        ),
    )
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return [
            LegalOperation(
                op_id="replace-introduces-duplication",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "2"),)),
                payload=IRNode(kind=IRNodeKind.SECTION, label="2", text=long_text),
                source=OperationSource(statute_id="2026/11"),
            ),
        ]

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return duplicated

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")
    duplicate_phases = [
        adjudication.detail.get("phase")
        for adjudication in result.adjudications
        if adjudication.kind == "text_duplication_warning"
    ]

    assert duplicate_phases.count("apply_op") == 1
    assert duplicate_phases.count("replay_fold") == 1
    assert duplicate_phases.count("materialized") == 1
    for adjudication in result.adjudications:
        if adjudication.kind == "text_duplication_warning":
            assert adjudication.detail["blocking"] is False
            assert adjudication.detail["strict_disposition"] == "record"


def test_replay_statute_threads_temporal_events_into_compile_timelines(
    monkeypatch,
    tmp_path,
) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")
    event = TemporalEvent(
        event_id="eu-event",
        kind="commence",
        scope=TemporalScope(),
        effective="2025-01-01",
        source=OperationSource(statute_id="32000R0000", effective="2025-01-01"),
        group_id="g:eu",
    )
    seen: dict[str, object] = {}

    def fake_compile_ops_for_statute(_self, celex: str):
        assert celex == "32000R0000"
        return []

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        seen["temporal_events"] = temporal_events
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "compile_ops_for_statute", fake_compile_ops_for_statute)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result: EUReplayResult = EUReplayPipeline(cache_dir=tmp_path).replay_statute(
        "32000R0000",
        temporal_events=(event,),
    )

    assert seen["temporal_events"] == (event,)
    assert result.temporal_events == (event,)

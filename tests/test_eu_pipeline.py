"""EU replay pipeline-focused regression tests."""
from __future__ import annotations

from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.ops_parser import EUOpsParser
from lawvm.eu.pipeline import EUReplayPipeline, EUReplayResult, apply_eu_ops
from lawvm.replay_adjudication import CompileAdjudication


def test_discover_affecting_acts_deduplicates_filters_and_sorts(monkeypatch, tmp_path) -> None:
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

    pipeline = EUReplayPipeline(cache_dir=tmp_path)
    affecting = pipeline.discover_affecting_acts("32000R0000")

    assert calls == ["32000R0000"]
    assert affecting == ["32000R0001", "32000R0002", "32000R0003"]
    rejection_rows = [
        diagnostic for diagnostic in pipeline.diagnostics if diagnostic.rule_id == "eu_affecting_candidate_celex_rejected"
    ]
    assert [row.exception_type for row in rejection_rows] == [
        "invalid_candidate_celex",
        "invalid_candidate_celex",
        "self_reference_candidate",
        "invalid_candidate_celex",
    ]
    assert [row.detail["reason_code"] for row in rejection_rows] == [
        "invalid_candidate_celex",
        "invalid_candidate_celex",
        "self_reference_candidate",
        "invalid_candidate_celex",
    ]
    assert all(row.strict_disposition == "block" for row in rejection_rows)


def test_discover_affecting_acts_records_acquisition_failure(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    def fake_request_notice(notice):
        raise RuntimeError("cellar unavailable")

    monkeypatch.setattr("lawvm.eu.pipeline._request_notice", fake_request_notice)

    affecting = pipeline.discover_affecting_acts("32000R0000")

    assert affecting == []
    assert len(pipeline.diagnostics) == 1
    diagnostic = pipeline.diagnostics[0]
    assert diagnostic.rule_id == "eu_affecting_discovery_failed"
    assert diagnostic.family == "source_pathology"
    assert diagnostic.phase == "acquisition"
    assert diagnostic.celex == "32000R0000"
    assert diagnostic.exception_type == "RuntimeError"
    assert diagnostic.strict_disposition == "block"
    detail = diagnostic.as_detail()
    assert detail["rule_id"] == "eu_affecting_discovery_failed"
    assert detail["family"] == "source_pathology"
    assert detail["blocking"] is True
    assert detail["quirks_disposition"] == "record"


def test_fetch_amendment_text_strips_html_and_entities(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    monkeypatch.setattr(
        "lawvm.eu.pipeline._request_notice",
        lambda notice: (b"<tree/>", {"ok": True}),
    )

    def _fake_manifestation_option(
        _notice_path,
        language: str,
        manifestation_type: str,
        **_kwargs,
    ) -> dict[str, object]:
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


def test_fetch_amendment_text_records_acquisition_failure(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    monkeypatch.setattr(
        "lawvm.eu.pipeline._request_notice",
        lambda notice: (b"<tree/>", {"ok": True}),
    )

    def fail_manifestation(_notice_path, language: str, manifestation_type: str, **_kwargs):
        raise ValueError("no manifestation")

    monkeypatch.setattr(
        "lawvm.eu.cellar.select_manifestation_option",
        fail_manifestation,
    )

    text = pipeline.fetch_amendment_text("32016R0679")

    assert text == ""
    assert [diagnostic.rule_id for diagnostic in pipeline.diagnostics] == ["eu_amendment_text_fetch_failed"]
    assert pipeline.diagnostics[0].exception_type == "ValueError"


def test_compile_ops_records_empty_affecting_act_text(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    monkeypatch.setattr(
        EUReplayPipeline,
        "discover_affecting_acts",
        lambda _self, celex: ["32000R0001"],
    )
    monkeypatch.setattr(
        EUReplayPipeline,
        "fetch_amendment_text",
        lambda _self, celex: "",
    )

    ops = pipeline.compile_ops_for_statute("32000R0000")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in pipeline.diagnostics] == [
        "eu_amendment_text_empty"
    ]
    diagnostic = pipeline.diagnostics[0]
    assert diagnostic.family == "source_pathology"
    assert diagnostic.phase == "acquisition"
    assert diagnostic.celex == "32000R0001"
    assert diagnostic.exception_type == "not_applicable"
    assert diagnostic.strict_disposition == "block"
    assert diagnostic.quirks_disposition == "record"


def test_compile_ops_does_not_duplicate_empty_text_after_fetch_failure(monkeypatch, tmp_path) -> None:
    pipeline = EUReplayPipeline(cache_dir=tmp_path)

    monkeypatch.setattr(
        EUReplayPipeline,
        "discover_affecting_acts",
        lambda _self, celex: ["32000R0001"],
    )

    def fake_fetch_amendment_text(self: EUReplayPipeline, celex: str) -> str:
        self._record_diagnostic(
            rule_id="eu_amendment_text_fetch_failed",
            celex=celex,
            phase="acquisition",
            reason="simulated fetch failure",
            exc=RuntimeError("network down"),
        )
        return ""

    monkeypatch.setattr(EUReplayPipeline, "fetch_amendment_text", fake_fetch_amendment_text)

    ops = pipeline.compile_ops_for_statute("32000R0000")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in pipeline.diagnostics] == [
        "eu_amendment_text_fetch_failed"
    ]


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
    parser = EUOpsParser()
    ops = parser.extract_ops("In Article 1, for: old text read: new text.")

    assert [(op.op_id, op.sequence, op.action.value, str(op.target)) for op in ops] == [
        ("corrigenda-1", 1, "replace", "article:1"),
    ]
    assert parser.diagnostics == []


def test_eu_ops_parser_records_unparsed_operative_segment() -> None:
    parser = EUOpsParser()

    ops = parser.extract_ops("The first sentence is replaced by the following text.")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in parser.diagnostics] == ["eu_ops_parser_segment_unparsed"]
    assert parser.diagnostics[0].family == "extraction_gap"
    assert parser.diagnostics[0].phase == "extraction"
    assert parser.diagnostics[0].as_detail()["strict_disposition"] == "record"


def test_eu_ops_parser_records_targetless_corrigendum_formula() -> None:
    parser = EUOpsParser()

    ops = parser.extract_ops("For: old text read: new text.")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in parser.diagnostics] == [
        "eu_ops_parser_corrigendum_target_missing"
    ]


def test_eu_ops_parser_records_unsupported_action_segment() -> None:
    parser = EUOpsParser()

    ops = parser.extract_ops("Article 4 is renumbered as Article 5.")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in parser.diagnostics] == [
        "eu_ops_parser_unsupported_action_segment"
    ]
    assert parser.diagnostics[0].family == "unsupported_action"
    assert parser.diagnostics[0].phase == "extraction"
    assert parser.diagnostics[0].blocking is True
    assert parser.diagnostics[0].as_detail() == {
        "rule_id": "eu_ops_parser_unsupported_action_segment",
        "phase": "extraction",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "family": "unsupported_action",
        "reason": (
            "EU parser saw an operative-looking amendment segment with an unsupported "
            "action verb: renumber"
        ),
        "source_excerpt": "Article 4 is renumbered as Article 5.",
    }


def test_eu_ops_parser_records_unknown_operative_segment_with_target() -> None:
    parser = EUOpsParser()

    ops = parser.extract_ops("Article 4 is modified as follows.")

    assert ops == []
    assert [diagnostic.rule_id for diagnostic in parser.diagnostics] == [
        "eu_ops_parser_unknown_operative_segment"
    ]
    assert parser.diagnostics[0].family == "unsupported_action"
    assert parser.diagnostics[0].phase == "extraction"
    assert parser.diagnostics[0].blocking is True
    assert parser.diagnostics[0].as_detail()["strict_disposition"] == "block"


def test_eu_ops_parser_does_not_record_unknown_operative_without_target() -> None:
    parser = EUOpsParser()

    ops = parser.extract_ops("The regulation is modified as follows.")

    assert ops == []
    assert parser.diagnostics == []


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
    assert adjudications[0].detail["rule_id"] == "eu_replay_text_payload_missing"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[1].detail["target"] == "section:9"
    assert adjudications[1].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[1].detail["blocking"] is True
    assert adjudications[1].detail["strict_disposition"] == "block"
    assert adjudications[1].detail["quirks_disposition"] == "record"
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
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_apply_eu_ops_inserts_under_exact_scoped_parent() -> None:
    baseline = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="chapter 1 article 1"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="chapter 2 article 1"),),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="insert-scoped-point",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("article", "1"), ("point", "9"))),
            payload=IRNode(kind=IRNodeKind.ITEM, label="9", text="inserted"),
            source=OperationSource(statute_id="2026/3"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert adjudications == []
    chapter_1_section = replayed.body.children[0].children[0]
    chapter_2_section = replayed.body.children[1].children[0]
    assert chapter_1_section.children == ()
    assert chapter_2_section.children == (IRNode(kind=IRNodeKind.ITEM, label="9", text="inserted"),)


def test_apply_eu_ops_blocks_unscoped_parent_hijack() -> None:
    baseline = IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="chapter 1 article 1"),),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    ops = [
        LegalOperation(
            op_id="insert-missing-scope",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "2"), ("article", "1"), ("point", "9"))),
            payload=IRNode(kind=IRNodeKind.ITEM, label="9", text="inserted"),
            source=OperationSource(statute_id="2026/3"),
        ),
    ]

    replayed = apply_eu_ops(baseline, ops, adjudications_out=adjudications)

    assert replayed.body == baseline.body
    assert replayed.metadata["eu_replay_applied_op_count"] == 0
    assert replayed.metadata["eu_replay_skipped_op_count"] == 1
    assert len(adjudications) == 1
    assert adjudications[0].kind == "eu_replay_insert_parent_scope_unresolved"
    assert adjudications[0].detail["rule_id"] == "eu_replay_insert_parent_scope_unresolved"
    assert adjudications[0].detail["parent_path"] == ("chapter:2", "section:1")
    assert adjudications[0].detail["unscoped_parent_candidates"] == (("chapter:1", "section:1"),)
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


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
    assert adjudications[0].detail["rule_id"] == "eu_replay_unsupported_action"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


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
    assert invariant_adjudications[0].detail["invariant_kind"] == "duplicate_label"
    assert invariant_adjudications[0].detail["invariant_path"] == "body"
    assert invariant_adjudications[0].detail["invariant"]["kind"] == "duplicate_label"
    assert invariant_adjudications[0].detail["invariant"]["child_kind"] == "section"
    assert invariant_adjudications[0].detail["invariant"]["label"] == "1"
    assert invariant_adjudications[0].detail["family"] == "tree_invariant_violation"
    assert invariant_adjudications[0].detail["blocking"] is True
    assert invariant_adjudications[0].detail["strict_disposition"] == "block"
    assert invariant_adjudications[0].detail["quirks_disposition"] == "record"


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


def test_replay_statute_projects_pipeline_diagnostics_to_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_compile_ops_for_statute(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0000"
        self._record_diagnostic(
            rule_id="eu_amendment_text_fetch_failed",
            celex="32000R0001",
            phase="acquisition",
            reason="simulated fetch failure",
            exc=RuntimeError("network down"),
        )
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

    result = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    diagnostic_rows = [
        adjudication
        for adjudication in result.adjudications
        if adjudication.kind == "eu_amendment_text_fetch_failed"
    ]
    assert len(diagnostic_rows) == 1
    assert diagnostic_rows[0].message == "simulated fetch failure"
    assert diagnostic_rows[0].source_statute == "32000R0001"
    assert diagnostic_rows[0].detail["phase"] == "acquisition"
    assert diagnostic_rows[0].detail["strict_disposition"] == "block"
    assert diagnostic_rows[0].detail["exception_type"] == "RuntimeError"


def test_fetch_amendment_text_records_manifestation_option_diagnostics(monkeypatch, tmp_path) -> None:
    from lawvm.eu import cellar

    tree_notice = tmp_path / "32000R0001_tree.xml"
    tree_notice.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<NOTICE>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/no-language</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc-no-language.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <EXPRESSION>
    <URI><VALUE>http://example.test/expression/eng</VALUE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>eng</IDENTIFIER></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <SAMEAS><URI><VALUE>http://example.test/doc.xhtml</VALUE></URI></SAMEAS>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="xhtml">
    <URI><VALUE>http://example.test/doc.xhtml</VALUE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>http://example.test/item.xhtml</VALUE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cellar,
        "_request_url",
        lambda url, accept: (
            b"<html><body>The first sentence is replaced.</body></html>",
            {"url": url, "content_type": "application/xhtml+xml"},
        ),
    )

    pipeline = EUReplayPipeline(cache_dir=tmp_path)
    text = pipeline.fetch_amendment_text("32000R0001")

    assert "The first sentence is replaced." in text
    assert len(pipeline.diagnostics) == 1
    diagnostic = pipeline.diagnostics[0]
    assert diagnostic.rule_id == "eu_cellar_manifestation_option_skipped"
    assert diagnostic.phase == "acquisition"
    assert diagnostic.exception_type == "missing_expression_language"
    cellar_detail = diagnostic.detail["cellar_detail"]
    assert isinstance(cellar_detail, dict)
    assert dict(cellar_detail).get("reason_code") == "missing_expression_language"


def test_replay_statute_projects_parser_diagnostics_to_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_discover_affecting_acts(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0000"
        return ["32000R0001"]

    def fake_fetch_amendment_text(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0001"
        return "The first sentence is replaced by the following text."

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        assert ops == []
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "discover_affecting_acts", fake_discover_affecting_acts)
    monkeypatch.setattr(EUReplayPipeline, "fetch_amendment_text", fake_fetch_amendment_text)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    diagnostic_rows = [
        adjudication
        for adjudication in result.adjudications
        if adjudication.kind == "eu_ops_parser_segment_unparsed"
    ]
    assert len(diagnostic_rows) == 1
    assert diagnostic_rows[0].source_statute == "32000R0001"
    assert diagnostic_rows[0].detail["celex"] == "32000R0001"
    assert diagnostic_rows[0].detail["family"] == "extraction_gap"
    assert diagnostic_rows[0].detail["phase"] == "extraction"
    assert diagnostic_rows[0].detail["strict_disposition"] == "record"


def test_replay_statute_projects_unsupported_parser_action_to_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_discover_affecting_acts(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0000"
        return ["32000R0001"]

    def fake_fetch_amendment_text(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0001"
        return "Article 4 is renumbered as Article 5."

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        assert ops == []
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "discover_affecting_acts", fake_discover_affecting_acts)
    monkeypatch.setattr(EUReplayPipeline, "fetch_amendment_text", fake_fetch_amendment_text)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    diagnostic_rows = [
        adjudication
        for adjudication in result.adjudications
        if adjudication.kind == "eu_ops_parser_unsupported_action_segment"
    ]
    assert len(diagnostic_rows) == 1
    assert diagnostic_rows[0].source_statute == "32000R0001"
    assert diagnostic_rows[0].detail["celex"] == "32000R0001"
    assert diagnostic_rows[0].detail["family"] == "unsupported_action"
    assert diagnostic_rows[0].detail["phase"] == "extraction"
    assert diagnostic_rows[0].detail["blocking"] is True
    assert diagnostic_rows[0].detail["strict_disposition"] == "block"


def test_replay_statute_projects_unknown_operative_parser_segment_to_adjudications(monkeypatch, tmp_path) -> None:
    baseline = _baseline_statute()
    baseline_path = tmp_path / "32000R0000_baseline.xhtml"
    baseline_path.write_text("<dummy/>")

    def fake_discover_affecting_acts(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0000"
        return ["32000R0001"]

    def fake_fetch_amendment_text(self: EUReplayPipeline, celex: str):
        assert celex == "32000R0001"
        return "Article 4 is modified as follows."

    def fake_parse_eu_regulation_ir(_path: object, celex: str) -> IRStatute:
        assert celex == "32000R0000"
        return baseline

    def fake_compile_timelines(_base: IRStatute, ops, temporal_events=()):
        assert ops == []
        return "timelines"

    def fake_materialize_pit(_timelines, as_of: str, base: IRStatute):
        return base

    monkeypatch.setattr(EUReplayPipeline, "discover_affecting_acts", fake_discover_affecting_acts)
    monkeypatch.setattr(EUReplayPipeline, "fetch_amendment_text", fake_fetch_amendment_text)
    monkeypatch.setattr("lawvm.eu.pipeline.parse_eu_regulation_ir", fake_parse_eu_regulation_ir)
    monkeypatch.setattr("lawvm.eu.pipeline.compile_timelines", fake_compile_timelines)
    monkeypatch.setattr("lawvm.eu.pipeline.materialize_pit", fake_materialize_pit)

    result = EUReplayPipeline(cache_dir=tmp_path).replay_statute("32000R0000")

    diagnostic_rows = [
        adjudication
        for adjudication in result.adjudications
        if adjudication.kind == "eu_ops_parser_unknown_operative_segment"
    ]
    assert len(diagnostic_rows) == 1
    assert diagnostic_rows[0].source_statute == "32000R0001"
    assert diagnostic_rows[0].detail["celex"] == "32000R0001"
    assert diagnostic_rows[0].detail["family"] == "unsupported_action"
    assert diagnostic_rows[0].detail["phase"] == "extraction"
    assert diagnostic_rows[0].detail["blocking"] is True
    assert diagnostic_rows[0].detail["strict_disposition"] == "block"


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

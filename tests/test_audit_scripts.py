from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from lawvm.core.phase_result import Finding
from scripts import audit_adjudications, audit_invariants, audit_warnings


def test_audit_adjudications_uses_compile_facade_projection(monkeypatch) -> None:
    def fake_compile_fi_facade(
        sid: str,
        *,
        replay_mode: str,
        compile_mode: str,
    ) -> SimpleNamespace:
        assert sid == "1994/1472"
        assert replay_mode == "legal_pit"
        assert compile_mode == "quirks"
        return SimpleNamespace(
            finding_ledger=(
                Finding(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="elab",
                    detail={"message": "observed", "code": "X"},
                    source_statute="2005/544",
                    blocking=False,
                ),
                Finding(
                    kind="RUNTIME.VIOLATION",
                    role="violation",
                    stage="apply",
                    detail={
                        "message": "tree invariant broken",
                        "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
                    },
                    source_statute="2006/254",
                    blocking=True,
                ),
            )
        )

    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade",
        fake_compile_fi_facade,
    )

    result = audit_adjudications._compile_one("1994/1472")

    assert result.error == ""
    assert [row.adj_kind for row in result.adj_rows] == [
        "ELAB.SOURCE_PATHOLOGY",
        "APPLY.TREE_INVARIANT_VIOLATION",
    ]
    assert [row.failure_kind for row in result.failure_rows] == [
        "APPLY.TREE_INVARIANT_VIOLATION",
    ]


def test_audit_adjudications_prints_grouped_statute_summary(capsys) -> None:
    audit_adjudications._print_summary(
        adj_kind_counts=Counter({"APPLY.TREE_INVARIANT_VIOLATION": 3}),
        statute_kind_counts=Counter({
            ("1997/1339", "APPLY.TREE_INVARIANT_VIOLATION"): 2,
            ("2017/320", "APPLY.TREE_INVARIANT_VIOLATION"): 1,
        }),
        failure_kind_counts=Counter({"APPLY.TREE_INVARIANT_VIOLATION": 1}),
        samples={"APPLY.TREE_INVARIANT_VIOLATION": ["1997/1339"]},
        statute_kind_samples={
            ("1997/1339", "APPLY.TREE_INVARIANT_VIOLATION"): [
                "duplicate subsection labels [2007/111]"
            ],
            ("2017/320", "APPLY.TREE_INVARIANT_VIOLATION"): [
                "duplicate subsection labels [2018/301]"
            ],
        },
        failure_samples={"APPLY.TREE_INVARIANT_VIOLATION": ["1997/1339"]},
        total=2,
        errors={},
        warning_total=0,
    )

    out = capsys.readouterr().out
    assert "Statute/kind groups" in out
    assert "1997/1339" in out
    assert "2017/320" in out
    assert "duplicate subsection labels" in out


def test_audit_invariants_uses_replay_findings_and_meta(monkeypatch) -> None:
    def fake_build_replay_plan_inspection(args: SimpleNamespace) -> dict[str, object]:
        assert args.statute_id == "1994/1472"
        return {
            "amendment_chain": ["2005/544"],
            "oracle_suspect": "",
        }

    def fake_replay_xml(
        sid: str,
        mode: str = "legal_pit",
        *,
        quiet: bool,
        replay_meta_out: dict[str, object],
    ) -> SimpleNamespace:
        assert sid == "1994/1472"
        assert mode == "legal_pit"
        assert quiet is True
        replay_meta_out["invariant_violations"] = [
            "body/section:1: duplicate section:5a (2 times)",
        ]
        replay_meta_out["product_invariant_violations"] = [
            "body/section:2: section out of order: 5 > 2",
        ]
        return SimpleNamespace(
            findings=(
                Finding(
                    kind="RUNTIME.VIOLATION",
                    role="violation",
                    stage="apply",
                    detail={
                        "message": "Replay tree invariant violated.",
                        "violation": "body/section:3: duplicate section:6 (2 times)",
                        "phase": "replay_fold",
                        "barrier_code": "APPLY.TREE_INVARIANT_VIOLATION",
                    },
                    source_statute="2006/254",
                    blocking=True,
                ),
            )
        )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        fake_replay_xml,
    )
    monkeypatch.setattr(
        "lawvm.tools.replay_plan.build_replay_plan_inspection",
        fake_build_replay_plan_inspection,
    )

    rows = audit_invariants._audit_one("1994/1472")
    rows = audit_invariants._annotate_phase_scope(rows)

    assert rows == [
        {
            "statute_id": "1994/1472",
            "status": "violation",
            "violation_type": "duplicate_label",
            "path": "body/section:3",
            "detail": "section:6",
            "source": "finding_ledger",
            "adj_kind": "APPLY.TREE_INVARIANT_VIOLATION",
            "phase": "replay_fold",
            "chain_length": "1",
            "oracle_suspect": "",
            "inferred_phase": "replay_fold",
            "phase_scope": "replay_fold_only",
            "detector_family": "pre_dedup_duplicate_label",
        },
        {
            "statute_id": "1994/1472",
            "status": "violation",
            "violation_type": "duplicate_label",
            "path": "body/section:1",
            "detail": "section:5a",
            "source": "replay_meta_tree",
            "adj_kind": "APPLY.TREE_INVARIANT_VIOLATION",
            "phase": "",
            "chain_length": "1",
            "oracle_suspect": "",
            "inferred_phase": "replay_fold",
            "phase_scope": "replay_fold_only",
            "detector_family": "duplicate_label",
        },
        {
            "statute_id": "1994/1472",
            "status": "violation",
            "violation_type": "sort_order",
            "path": "body/section:2",
            "detail": "section: 5 > 2",
            "source": "replay_meta_product",
            "adj_kind": "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            "phase": "",
            "chain_length": "1",
            "oracle_suspect": "",
            "inferred_phase": "materialized",
            "phase_scope": "materialized_only",
            "detector_family": "sort_order",
        },
    ]


def test_audit_invariants_classifies_illegal_edge() -> None:
    vtype, path, detail = audit_invariants._classify_violation(
        "body/section:1: unexpected paragraph inside section"
    )

    assert vtype == "illegal_edge"
    assert path == "body/section:1"
    assert detail == "paragraph inside section"


def test_audit_invariants_prefers_typed_replay_meta(monkeypatch) -> None:
    def fake_build_replay_plan_inspection(args: SimpleNamespace) -> dict[str, object]:
        return {"amendment_chain": [], "oracle_suspect": ""}

    def fake_replay_xml(
        sid: str,
        mode: str = "legal_pit",
        *,
        quiet: bool,
        replay_meta_out: dict[str, object],
    ) -> SimpleNamespace:
        replay_meta_out["typed_invariant_violations"] = [
            {
                "kind": "unexpected_child_kind",
                "path": "body/section:1",
                "parent_kind": "section",
                "child_kind": "paragraph",
            },
        ]
        replay_meta_out["invariant_violations"] = [
            "body/section:99: duplicate section:5a (2 times)",
        ]
        replay_meta_out["typed_product_tree_invariant_violations"] = {
            "materialized_tree": [
                {
                    "kind": "sort_order",
                    "path": "body",
                    "child_kind": "section",
                    "previous_label": "5",
                    "next_label": "2",
                },
            ],
        }
        replay_meta_out["product_invariant_violations"] = [
            "materialized_tree:body/section:99: duplicate section:6 (2 times)",
        ]
        return SimpleNamespace(findings=())

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        fake_replay_xml,
    )
    monkeypatch.setattr(
        "lawvm.tools.replay_plan.build_replay_plan_inspection",
        fake_build_replay_plan_inspection,
    )

    rows = audit_invariants._annotate_phase_scope(audit_invariants._audit_one("1994/1472"))

    assert [(row["violation_type"], row["path"], row["detail"], row["source"]) for row in rows] == [
        ("illegal_edge", "body/section:1", "paragraph inside section", "replay_meta_tree"),
        ("sort_order", "body", "section: 5 > 2", "replay_meta_product"),
    ]
    assert rows[1]["inferred_phase"] == "materialized"


def test_audit_invariants_error_row_marks_status_error(monkeypatch) -> None:
    def fake_build_replay_plan_inspection(args: SimpleNamespace) -> dict[str, object]:
        return {"amendment_chain": [], "oracle_suspect": ""}

    def fake_replay_xml(
        sid: str,
        mode: str = "legal_pit",
        *,
        quiet: bool,
        replay_meta_out: dict[str, object],
    ) -> SimpleNamespace:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        fake_replay_xml,
    )
    monkeypatch.setattr(
        "lawvm.tools.replay_plan.build_replay_plan_inspection",
        fake_build_replay_plan_inspection,
    )

    rows = audit_invariants._audit_one("1994/1472")

    assert rows[0]["status"] == "error"
    assert rows[0]["violation_type"] == "ERROR"


def test_audit_invariants_phase_scope_marks_both() -> None:
    rows = [
        {
            "statute_id": "2002/1244",
            "violation_type": "duplicate_label",
            "path": "body/section:21c",
            "detail": "paragraph:i",
            "source": "finding_ledger",
            "adj_kind": "APPLY.TREE_INVARIANT_VIOLATION",
            "phase": "replay_fold",
        },
        {
            "statute_id": "2002/1244",
            "violation_type": "duplicate_label",
            "path": "body/section:21c",
            "detail": "paragraph:i",
            "source": "replay_meta_product",
            "adj_kind": "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            "phase": "",
        },
    ]

    annotated = audit_invariants._annotate_phase_scope(rows)

    assert [row["phase_scope"] for row in annotated] == ["both", "both"]
    assert [row["inferred_phase"] for row in annotated] == ["replay_fold", "materialized"]


def test_audit_invariants_detector_family_marks_flattened_sublist_family() -> None:
    rows = [
        {
            "statute_id": "1997/1339",
            "violation_type": "duplicate_label",
            "path": "body/chapter:1/section:4/subsection:1",
            "detail": "paragraph:a",
            "source": "replay_meta_tree",
            "adj_kind": "APPLY.TREE_INVARIANT_VIOLATION",
            "phase": "",
        }
    ]

    annotated = audit_invariants._annotate_phase_scope(rows)

    assert annotated[0]["detector_family"] == "flattened_sublist_family"


def test_audit_invariants_detector_family_marks_illegal_section_child_edge() -> None:
    rows = [
        {
            "statute_id": "2002/672",
            "violation_type": "illegal_edge",
            "path": "body/section:1",
            "detail": "paragraph inside section",
            "source": "replay_meta_tree",
            "adj_kind": "APPLY.TREE_INVARIANT_VIOLATION",
            "phase": "",
        }
    ]

    annotated = audit_invariants._annotate_phase_scope(rows)

    assert annotated[0]["detector_family"] == "illegal_edge_section_child"


def test_audit_invariants_detector_family_marks_base_text_flattened_sublist_family() -> None:
    rows = [
        {
            "statute_id": "2010/54",
            "violation_type": "duplicate_label",
            "path": "body/section:1/subsection:1/paragraph:1",
            "detail": "subparagraph:a",
            "source": "replay_meta_product",
            "adj_kind": "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            "phase": "",
            "chain_length": "0",
            "oracle_suspect": "",
        }
    ]

    annotated = audit_invariants._annotate_phase_scope(rows)

    assert annotated[0]["detector_family"] == "base_text_flattened_sublist_family"


def test_audit_warnings_load_corpus_normalizes_and_deduplicates(tmp_path) -> None:
    corpus = tmp_path / "corpus.txt"
    corpus.write_text(
        "1901/15-001\n1901/15-002\n# comment\n1993/1055\n",
        encoding="utf-8",
    )

    raw = audit_warnings._load_corpus(corpus)
    normalized = audit_warnings._deduplicate_ids(raw)

    assert raw == ["1901/15-001", "1901/15-002", "1993/1055"]
    assert normalized == ["1901/15", "1993/1055"]


def test_audit_invariants_load_corpus_normalizes_and_deduplicates(tmp_path) -> None:
    corpus = tmp_path / "corpus.txt"
    corpus.write_text(
        "1901/15-001\n1901/15-002\n# comment\n1993/1055\n",
        encoding="utf-8",
    )

    ids = audit_invariants.load_corpus(corpus)

    assert ids == ["1901/15", "1993/1055"]

from __future__ import annotations

import csv
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import ConsistencyDivergence
from lawvm.tools import ee_bench


def test_score_one_pair_accepts_current_ir_node_kinds(monkeypatch) -> None:
    def _body(section_text: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            text=section_text,
                        ),
                    ),
                ),
            ),
        )

    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2026-03-24")
    monkeypatch.setattr(
        ee_bench,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            n_ops=1,
            divergences=[],
            replayed=SimpleNamespace(body=_body("Section text")),
            oracle=SimpleNamespace(body=_body("Section text")),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        ),
    )

    result = ee_bench._score_one_pair("g1", "base", "oracle", "Current IR", archive=None)

    assert result.status == "OK"
    assert result.r_secs == 1
    assert result.o_secs == 1
    assert result.sec_match == 1.0


def test_get_sections_uses_ee_comparison_surface_for_kehtetu_section_titles() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        text="Repealed title",
                        attrs={"kehtetu": True},
                        children=(),
                    ),
                ),
            ),
        ),
    )

    assert ee_bench._get_sections(body) == {"chapter:1/section:5": ""}


def test_score_one_pair_counts_missing_empty_oracle_section_as_match(monkeypatch) -> None:
    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2026-03-24")

    replay_body = object()
    oracle_body = object()
    monkeypatch.setattr(
        ee_bench,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            n_ops=1,
            divergences=[],
            replayed=SimpleNamespace(body=replay_body),
            oracle=SimpleNamespace(body=oracle_body),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        ),
    )
    monkeypatch.setattr(
        ee_bench,
        "_get_sections",
        lambda body: {"section:1": "same"} if body is replay_body else {"section:1": "same", "section:2": ""},
    )

    result = ee_bench._score_one_pair("g1", "base", "oracle", "Empty missing", archive=None)

    assert result.sec_match == 1.0
    assert result.r_secs == 1
    assert result.o_secs == 2


def test_score_one_pair_attaches_known_residual_summary(monkeypatch) -> None:
    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2026-03-24")
    monkeypatch.setattr(
        ee_bench,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            n_ops=217,
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(
                        path=(("chapter", "1"), ("section", "6"), ("subsection", "2"))
                    ),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                )
            ],
            replayed=SimpleNamespace(body=object()),
            oracle=SimpleNamespace(body=object()),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        ),
    )
    monkeypatch.setattr(
        ee_bench,
        "_get_sections",
        lambda body: {"section:1": "same"},
    )

    result = ee_bench._score_one_pair("161988", "193936", "13336397", "Liiklusseadus", archive=None)

    assert result.status == "OK"
    assert result.adjudicated_residual_count == 1
    assert result.matched_current_residual_count == 1
    assert result.adjudicated_bucket_counts == "source_oracle_drift=1"
    assert result.unknown_current_residual_count == 0
    assert result.open_current_divergence_count == 0
    assert result.source_basis == "pairwise_terviktekst_delta"
    assert result.benchmark_reporting_stratum == "EE_CORE_COMMENSURABLE"
    assert result.benchmark_reporting_headline_eligible is True


def test_score_one_pair_marks_uninventoried_divergences_as_open(monkeypatch) -> None:
    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2026-03-24")
    monkeypatch.setattr(
        ee_bench,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            n_ops=5,
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(
                        path=(("chapter", "1"), ("section", "1"), ("subsection", "1"))
                    ),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                )
            ],
            replayed=SimpleNamespace(body=object()),
            oracle=SimpleNamespace(body=object()),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        ),
    )
    monkeypatch.setattr(
        ee_bench,
        "_get_sections",
        lambda body: {"section:1": "same"},
    )

    result = ee_bench._score_one_pair("g1", "base-open", "oracle-open", "Open Pair", archive=None)

    assert result.status == "OK"
    assert result.adjudicated_residual_count == 0
    assert result.matched_current_residual_count == 0
    assert result.unknown_current_residual_count == 1
    assert result.open_current_divergence_count == 1


def test_score_one_pair_adjudicates_punctuation_whitespace_only_divergences(monkeypatch) -> None:
    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2026-03-24")
    monkeypatch.setattr(
        ee_bench,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            n_ops=5,
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=(("section", "1"), ("subsection", "1"), ("item", "2"))),
                    divergence_type="MISMATCH",
                    ops_text="item text;",
                    consolidated_text="item text.",
                ),
                ConsistencyDivergence(
                    address=LegalAddress(path=(("section", "2"),)),
                    divergence_type="MISMATCH",
                    ops_text="real replay text",
                    consolidated_text="real oracle text",
                ),
            ],
            replayed=SimpleNamespace(body=object()),
            oracle=SimpleNamespace(body=object()),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        ),
    )
    monkeypatch.setattr(ee_bench, "_get_sections", lambda body: {"section:1": "same"})

    result = ee_bench._score_one_pair("g1", "base-open", "oracle-open", "Open Pair", archive=None)

    assert result.status == "OK"
    assert result.adjudicated_residual_count == 1
    assert result.matched_current_residual_count == 1
    assert result.adjudicated_bucket_counts == "presentation_punctuation_whitespace=1"
    assert result.unknown_current_residual_count == 1
    assert result.open_current_divergence_count == 1


def test_score_one_pair_uses_oracle_effective_date_as_cutoff(monkeypatch) -> None:
    seen: dict[str, str | None] = {}

    monkeypatch.setattr(ee_bench, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_bench, "extract_effective_date", lambda xml_bytes: "2012-02-01")

    def _fake_replay(base_id, *, as_of, archive=None, verbose=False, oracle_id=None):
        seen["as_of"] = as_of
        seen["oracle_id"] = oracle_id
        return SimpleNamespace(
            n_ops=4,
            divergences=[],
            replayed=SimpleNamespace(body=object()),
            oracle=SimpleNamespace(body=object()),
            error="",
            comparison_class="commensurable_delta",
            source_basis="pairwise_terviktekst_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=False),
        )

    monkeypatch.setattr(ee_bench, "replay_ee_to_pit", _fake_replay)
    monkeypatch.setattr(ee_bench, "_get_sections", lambda body: {"section:1": "same"})

    result = ee_bench._score_one_pair(
        "g1", "13247639", "131012012006", "Jälitustegevuse seadus", archive=None
    )

    assert result.status == "OK"
    assert seen == {"as_of": "2012-02-01", "oracle_id": "131012012006"}
    assert result.source_basis == "pairwise_terviktekst_delta"
    assert result.benchmark_reporting_stratum == "EE_CORE_COMMENSURABLE"
    assert result.benchmark_reporting_headline_eligible is True


def test_save_results_and_show_run_round_trips_adjudicated_residual_fields(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(ee_bench, "_BENCH_DIR", tmp_path)
    monkeypatch.setattr(ee_bench, "_HISTORY_CSV", tmp_path / "history.csv")

    results = [
        ee_bench._BenchResult(
            grupi_id="161988",
            base_id="193936",
            oracle_id="13336397",
            title="Liiklusseadus",
            n_ops=217,
            n_divs=7,
            sec_match=0.91,
            r_secs=852,
            o_secs=852,
            status="OK",
            source_basis="pairwise_terviktekst_delta",
            comparison_class="commensurable_delta",
            core_benchmark=True,
            benchmark_reporting_stratum="EE_CORE_COMMENSURABLE",
            benchmark_reporting_headline_eligible=True,
            adjudicated_residual_count=7,
            matched_current_residual_count=7,
            adjudicated_bucket_counts="appendix_display_pathology=1,source_oracle_drift=6",
            unknown_current_residual_count=0,
            open_current_divergence_count=0,
        )
    ]

    ee_bench._save_results(results, "demo")
    ee_bench._show_run("demo")

    out = capsys.readouterr().out
    assert "Results saved:" in out
    assert "matched=7 open=0" in out

    rows = list(csv.DictReader((tmp_path / "demo.csv").open()))
    assert rows[0]["source_basis"] == "pairwise_terviktekst_delta"
    assert rows[0]["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert rows[0]["benchmark_reporting_headline_eligible"] == "1"


def test_print_report_prioritizes_open_unexplained_rows(capsys) -> None:
    results = [
        ee_bench._BenchResult(
            grupi_id="g1",
            base_id="193936",
            oracle_id="13336397",
            title="Liiklusseadus",
            n_ops=217,
            n_divs=7,
            sec_match=0.90,
            r_secs=852,
            o_secs=852,
            status="OK",
            comparison_class="commensurable_delta",
            core_benchmark=True,
            adjudicated_residual_count=7,
            matched_current_residual_count=7,
            adjudicated_bucket_counts="appendix_display_pathology=1,source_oracle_drift=6",
            unknown_current_residual_count=0,
            open_current_divergence_count=0,
        ),
        ee_bench._BenchResult(
            grupi_id="g2",
            base_id="open-pair",
            oracle_id="open-oracle",
            title="Open Pair",
            n_ops=10,
            n_divs=5,
            sec_match=0.95,
            r_secs=10,
            o_secs=10,
            status="OK",
            comparison_class="commensurable_delta",
            core_benchmark=True,
            adjudicated_residual_count=2,
            matched_current_residual_count=2,
            adjudicated_bucket_counts="source_oracle_drift=2",
            unknown_current_residual_count=3,
            open_current_divergence_count=3,
        ),
    ]

    ee_bench._print_report(results, "demo")

    out = capsys.readouterr().out
    assert "Fully adjudicated residual rows: 1" in out
    assert "Open unexplained residual rows: 1" in out
    open_index = out.index("open-pair")
    adjudicated_index = out.index("193936")
    assert open_index < adjudicated_index
    assert "matched=2 open=3" in out
    assert "matched=7 open=0" in out

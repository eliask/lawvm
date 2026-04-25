from types import SimpleNamespace
import sqlite3
import json

from lawvm.tools.frontier import (
    FRESH_ORACLE_CHECK_LIMIT,
    FRESH_SCORE_REFRESH_LIMIT,
    _apply_proof_rebucketing,
    _apply_proof_rebucketing_to_summary,
    _apply_refreshed_scores,
    _build_evidence_bundles,
    _build_proof_report_rows,
    _summarize_proof_rows,
    _bucket_frontier_row,
    _build_frontier,
    _classify_one_sync,
    _contingent_effective_date_signal,
    _compute_fixability,
    _filter_bench_data_to_corpus_ids,
    _html_noncommensurable_signal,
    _html_topology_signal,
    _load_oracle_check_cache,
    _load_strict_run,
    _parse_string_listish,
    _save_corpus_slice,
    _save_evidence_bundles_jsonl,
    _save_proof_report_jsonl,
    _score_one_sync,
    _select_provisional_candidate_refresh_sids,
    _source_pathology_signal,
    _summarize_low_scoring_rows,
    _should_refresh_all_low_scoring,
    _should_refresh_all_low_scoring_scores,
    main,
)


def test_compute_fixability_marks_version_mismatch_suspect() -> None:
    fixability, is_suspect, reason = _compute_fixability(
        sim=0.7,
        oracle_info=None,
        version_gate={"suspect_detail": "2025/716 eff 2026-01-01 > cutoff 2025-06-27"},
        amendments=3,
        exclude_suspect=True,
    )

    assert is_suspect is True
    assert reason == "ORACLE_VERSION_MISMATCH"
    assert fixability < 0.05


def test_build_frontier_excludes_version_suspect_rows() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "2022/1352", "similarity": 0.77, "amendments": 1},
            {"statute_id": "1992/1702", "similarity": 0.65, "amendments": 12},
        ],
        oracle_checks={
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={
            "2022/1352": {
                "suspect_detail": "2025/716 eff 2026-01-01 > cutoff 2025-06-27",
                "pending_detail": "",
            }
        },
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1992/1702"]


def test_compute_fixability_marks_missing_oracle_check_suspect() -> None:
    fixability, is_suspect, reason = _compute_fixability(
        sim=0.7,
        oracle_info=None,
        version_gate=None,
        amendments=3,
        exclude_suspect=True,
    )

    assert is_suspect is True
    assert reason == "NO_ORACLE_CHECK"
    assert fixability < 0.05


def test_build_frontier_excludes_no_oracle_check_rows() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1978/38", "similarity": 0.677, "amendments": 57},
            {"statute_id": "1992/1702", "similarity": 0.65, "amendments": 12},
        ],
        oracle_checks={
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1992/1702"]


def test_compute_fixability_marks_base_drift_suspect() -> None:
    fixability, is_suspect, reason = _compute_fixability(
        sim=0.75,
        oracle_info={
            "suspect_fraction": 0.0,
            "top_diagnosis": "REPLAY_MISSING",
            "replay_bug_count": 9,
            "replay_bug_unblamed_fraction": 1.0,
        },
        version_gate=None,
        amendments=11,
        exclude_suspect=True,
    )

    assert is_suspect is True
    assert reason == "BASE_DRIFT"
    assert fixability < 0.05


def test_compute_fixability_marks_single_unblamed_replay_bug_as_base_drift() -> None:
    fixability, is_suspect, reason = _compute_fixability(
        sim=0.835,
        oracle_info={
            "suspect_fraction": 0.0,
            "top_diagnosis": "REPLAY_MISSING",
            "replay_bug_count": 1,
            "replay_bug_unblamed_count": 1,
            "replay_bug_unblamed_fraction": 1.0,
        },
        version_gate=None,
        amendments=14,
        exclude_suspect=True,
    )

    assert is_suspect is True
    assert reason == "BASE_DRIFT"
    assert fixability < 0.02


def test_build_frontier_excludes_base_drift_rows() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "2016/66", "similarity": 0.738, "amendments": 11},
            {"statute_id": "1994/1205", "similarity": 0.835, "amendments": 14},
            {"statute_id": "1992/1702", "similarity": 0.65, "amendments": 12},
        ],
        oracle_checks={
            "2016/66": {
                "total_divergences": 19,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 19,
                "replay_bug_unblamed_count": 19,
                "replay_bug_unblamed_fraction": 1.0,
            },
            "1994/1205": {
                "total_divergences": 1,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 1,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 1.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1992/1702"]


def test_should_refresh_all_low_scoring_when_candidate_set_is_small() -> None:
    low_scoring = [
        {"statute_id": f"1992/{i}", "similarity": 0.8, "amendments": 3}
        for i in range(FRESH_ORACLE_CHECK_LIMIT)
    ]

    assert _should_refresh_all_low_scoring(low_scoring) is True


def test_should_not_refresh_all_low_scoring_when_candidate_set_is_large() -> None:
    low_scoring = [
        {"statute_id": f"1992/{i}", "similarity": 0.8, "amendments": 3}
        for i in range(FRESH_ORACLE_CHECK_LIMIT + 1)
    ]

    assert _should_refresh_all_low_scoring(low_scoring) is False


def test_should_refresh_all_low_scoring_scores_when_candidate_set_is_small() -> None:
    low_scoring = [
        {"statute_id": f"1992/{i}", "similarity": 0.8, "amendments": 3}
        for i in range(FRESH_SCORE_REFRESH_LIMIT)
    ]

    assert _should_refresh_all_low_scoring_scores(low_scoring) is True


def test_select_provisional_candidate_refresh_sids_prefers_ranked_candidates() -> None:
    bench_data = [
        {"statute_id": "1994/1205", "similarity": 0.835, "amendments": 14},
        {"statute_id": "1992/1702", "similarity": 0.650, "amendments": 12},
        {"statute_id": "2022/1352", "similarity": 0.770, "amendments": 1},
    ]
    oracle_checks = {
        "1994/1205": {
            "total_divergences": 1,
            "suspect_fraction": 0.0,
            "top_diagnosis": "REPLAY_MISSING",
            "replay_bug_count": 1,
            "replay_bug_unblamed_count": 1,
            "replay_bug_unblamed_fraction": 1.0,
        },
        "1992/1702": {
            "total_divergences": 8,
            "suspect_fraction": 0.0,
            "top_diagnosis": "REPLAY_MISSING",
            "replay_bug_count": 8,
            "replay_bug_unblamed_count": 1,
            "replay_bug_unblamed_fraction": 0.125,
        },
    }
    version_gates = {
        "2022/1352": {
            "suspect_detail": "2025/716 eff 2026-01-01 > cutoff 2025-06-27",
            "pending_detail": "",
        }
    }

    selected = _select_provisional_candidate_refresh_sids(
        bench_data=bench_data,
        oracle_checks=oracle_checks,
        version_gates=version_gates,
        strict_data=None,
        score_threshold=0.95,
        top=2,
        exclude_suspect=True,
        limit=2,
    )

    assert selected == ["1992/1702"]


def test_apply_refreshed_scores_replaces_stale_similarity() -> None:
    bench_data = [
        {"statute_id": "2003/605", "similarity": 0.883, "amendments": 8},
        {"statute_id": "1994/1472", "similarity": 0.873, "amendments": 43},
    ]

    refreshed = _apply_refreshed_scores(
        bench_data,
        {
            "2003/605": {"similarity": 1.0, "status": "OK"},
            "1994/1472": {"similarity": -1.0, "status": "ERR"},
        },
    )

    assert refreshed == [
        {"statute_id": "2003/605", "similarity": 1.0, "amendments": 8},
        {"statute_id": "1994/1472", "similarity": 0.873, "amendments": 43},
    ]


def test_classify_one_sync_forwards_mode(monkeypatch, capsys) -> None:
    import lawvm.tools.oracle_check as oracle_check

    def fake_classify(sid: str, mode: str):
        print("noisy replay chatter")
        return {"sid": sid, "mode": mode}

    monkeypatch.setattr(oracle_check, "_classify_statute", fake_classify)

    assert _classify_one_sync("1999/513", mode="legal_pit") == {
        "sid": "1999/513",
        "mode": "legal_pit",
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_score_one_sync_forwards_mode(monkeypatch, capsys) -> None:
    import lawvm.tools.bench as bench

    def fake_score(sid: str, mode: str = "finlex_oracle"):
        print("more noisy replay chatter")
        return sid, 0.5 if mode == "legal_pit" else 0.1, mode

    monkeypatch.setattr(bench, "_score_one", fake_score)

    assert _score_one_sync("1999/513", mode="legal_pit") == (
        "1999/513",
        0.5,
        "legal_pit",
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_build_frontier_excludes_rows_with_no_current_divergence() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "2003/605", "similarity": 0.883, "amendments": 8},
            {"statute_id": "1994/1472", "similarity": 0.873, "amendments": 43},
        ],
        oracle_checks={
            "2003/605": {
                "total_divergences": 0,
                "suspect_fraction": 0.0,
                "top_diagnosis": "UNKNOWN",
                "replay_bug_count": 0,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
            "1994/1472": {
                "total_divergences": 3,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 3,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1994/1472"]


def test_filter_bench_data_to_corpus_ids_restricts_subset() -> None:
    filtered = _filter_bench_data_to_corpus_ids(
        [
            {"statute_id": "1994/1472", "similarity": 0.873, "amendments": 43},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
            {"statute_id": "2003/605", "similarity": 1.0, "amendments": 8},
        ],
        ["1992/1702", "2003/605"],
    )

    assert filtered == [
        {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        {"statute_id": "2003/605", "similarity": 1.0, "amendments": 8},
    ]


def test_save_corpus_slice_writes_seq_parent_csv(tmp_path) -> None:
    path = tmp_path / "slice.csv"
    saved = _save_corpus_slice(
        [
            {"statute_id": "1994/1472", "similarity": 0.873, "amendments": 43},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        str(path),
    )

    assert saved == path
    assert path.read_text(encoding="utf-8") == (
        "seq,parent\n"
        "1,1994/1472\n"
        "2,1992/1702\n"
    )


def test_parse_string_listish_accepts_pipe_and_python_repr() -> None:
    assert _parse_string_listish("ELAB.SOURCE_PATHOLOGY|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY") == [
        "ELAB.SOURCE_PATHOLOGY",
        "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
    ]
    assert _parse_string_listish("['ELAB.SOURCE_PATHOLOGY', 'oracle_suspect']") == [
        "ELAB.SOURCE_PATHOLOGY",
        "oracle_suspect",
    ]


def test_build_frontier_marks_source_pathology_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1994/1472", "similarity": 0.705, "amendments": 43},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1994/1472": {
                "total_divergences": 2,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 2,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data={
            "1994/1472": {
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY", "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["MALFORMED_BROAD_REPLACE_BODY"],
                "source_incomplete": False,
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
            },
        },
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1994/1472"]["source_pathology"] is True
    assert by_sid["1994/1472"]["is_suspect"] is True
    assert by_sid["1994/1472"]["top_diagnosis"] == "SOURCE_PATHOLOGY:MALFORMED_BROAD_REPLACE_BODY"
    assert by_sid["1994/1472"]["source_pathology_codes"] == "MALFORMED_BROAD_REPLACE_BODY"


def test_build_frontier_marks_live_oracle_source_pathology_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1990/1295", "similarity": 0.889, "amendments": 18},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1990/1295": {
                "total_divergences": 36,
                "suspect_fraction": 0.0,
                "top_diagnosis": "MISSING",
                "replay_bug_count": 22,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "source_pathology": True,
                "source_pathology_codes": ["CONTAINER_MEMBERSHIP_MISMATCH"],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1990/1295"]["source_pathology"] is True
    assert by_sid["1990/1295"]["is_suspect"] is True
    assert by_sid["1990/1295"]["top_diagnosis"] == "SOURCE_PATHOLOGY:CONTAINER_MEMBERSHIP_MISMATCH"
    assert by_sid["1990/1295"]["source_pathology_codes"] == "CONTAINER_MEMBERSHIP_MISMATCH"


def test_build_frontier_excludes_source_pathology_rows_when_requested() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1994/1472", "similarity": 0.705, "amendments": 43},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1994/1472": {
                "total_divergences": 2,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 2,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data={
            "1994/1472": {
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["MALFORMED_BROAD_REPLACE_BODY"],
                "source_incomplete": False,
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
            },
        },
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1992/1702"]


def test_source_pathology_signal_unions_live_and_strict_codes() -> None:
    signaled, codes = _source_pathology_signal(
        {
            "source_pathology": True,
            "source_pathology_codes": ["CONTAINER_MEMBERSHIP_MISMATCH"],
        },
        {
            "projection_kinds": ["ELAB.SOURCE_PATHOLOGY"],
            "source_pathology_codes": ["DESTRUCTIVE_SHAPE_LOSS_RISK"],
            "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
        },
    )

    assert signaled is True
    assert codes == [
        "CONTAINER_MEMBERSHIP_MISMATCH",
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]


def test_source_pathology_signal_falls_back_to_structured_oracle_rows() -> None:
    signaled, codes = _source_pathology_signal(
        {
            "source_pathology": True,
            "source_pathology_codes": [],
            "source_pathology_rows": [
                {"code": "CONTAINER_MEMBERSHIP_MISMATCH", "target_label": "8 a §"},
            ],
        },
        None,
    )

    assert signaled is True
    assert codes == ["CONTAINER_MEMBERSHIP_MISMATCH"]


def test_source_pathology_signal_falls_back_to_structured_strict_rows() -> None:
    signaled, codes = _source_pathology_signal(
        None,
        {
            "projection_kinds": ["ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
            "source_pathology_codes": [],
            "source_pathology_rows": [
                {"code": "DESTRUCTIVE_SHAPE_LOSS_RISK", "target_label": "35 §"},
            ],
            "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
        },
    )

    assert signaled is True
    assert codes == ["DESTRUCTIVE_SHAPE_LOSS_RISK"]


def test_source_pathology_signal_ignores_projection_kind_without_codes_or_fail_reason() -> None:
    signaled, codes = _source_pathology_signal(
        None,
        {
            "projection_kinds": ["ELAB.SOURCE_PATHOLOGY"],
            "source_pathology_codes": [],
            "fail_reasons": [],
        },
    )

    assert signaled is False
    assert codes == []


def test_contingent_effective_date_signal_reads_strict_sources() -> None:
    signaled, sources = _contingent_effective_date_signal(
        None,
        {
            "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            "contingent_effective_sources": ["2004/542", "2005/544"],
            "fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
        }
    )

    assert signaled is True
    assert sources == ["2004/542", "2005/544"]


def test_contingent_effective_date_signal_ignores_projection_kind_without_sources_or_fail_reason() -> None:
    signaled, sources = _contingent_effective_date_signal(
        None,
        {
            "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            "contingent_effective_sources": [],
            "fail_reasons": [],
        }
    )

    assert signaled is False
    assert sources == []


def test_contingent_effective_date_signal_unions_live_and_strict_sources() -> None:
    signaled, sources = _contingent_effective_date_signal(
        {
            "contingent_effective_sources": ["1999/1301", "2004/542"],
        },
        {
            "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            "contingent_effective_sources": ["2004/542", "2005/544"],
            "fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
        },
    )

    assert signaled is True
    assert sources == ["1999/1301", "2004/542", "2005/544"]


def test_html_topology_signal_reads_live_missing_and_extra_labels() -> None:
    signaled, missing, extra = _html_topology_signal(
        {
            "html_topology_mismatch": True,
            "html_missing_from_xml": ["4 a §", "8 a §"],
            "html_extra_in_xml": ["15 a §"],
        }
    )

    assert signaled is True
    assert missing == ["4 a §", "8 a §"]
    assert extra == ["15 a §"]


def test_html_topology_signal_ignores_noncommensurable_cases() -> None:
    signaled, missing, extra = _html_topology_signal(
        {
            "html_topology_mismatch": False,
            "html_missing_from_xml": [],
            "html_extra_in_xml": [],
            "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
        }
    )

    assert signaled is False
    assert missing == []
    assert extra == []


def test_html_noncommensurable_signal_reads_reason() -> None:
    reason = _html_noncommensurable_signal(
        {
            "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
        }
    )

    assert reason == "duplicate_unscoped_oracle_labels:section:1"


def test_build_frontier_marks_html_noncommensurable_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1995/540", "similarity": 0.919, "amendments": 16},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1995/540": {
                "total_divergences": 28,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 12,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1995/540"]["is_suspect"] is True
    assert by_sid["1995/540"]["bucket"] == "html_noncommensurable"
    assert by_sid["1995/540"]["top_diagnosis"] == (
        "HTML_NONCOMMENSURABLE:duplicate_unscoped_oracle_labels:section:1"
    )
    assert by_sid["1995/540"]["html_noncommensurable_reason"] == (
        "duplicate_unscoped_oracle_labels:section:1"
    )


def test_build_frontier_marks_html_topology_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1994/1205", "similarity": 0.835, "amendments": 14},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1994/1205": {
                "total_divergences": 1,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 1,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "html_topology_mismatch": True,
                "html_missing_from_xml": ["8 a §", "15 a §"],
                "html_extra_in_xml": [],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1994/1205"]["html_topology_mismatch"] is True
    assert by_sid["1994/1205"]["is_suspect"] is True
    assert by_sid["1994/1205"]["bucket"] == "html_topology"
    assert by_sid["1994/1205"]["top_diagnosis"] == "HTML_TOPOLOGY_MISMATCH"
    assert by_sid["1994/1205"]["html_missing_from_xml"] == "8 a §|15 a §"


def test_build_frontier_marks_contingent_effective_date_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1991/1707", "similarity": 0.826, "amendments": 9},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1991/1707": {
                "total_divergences": 3,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 3,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "contingent_effective_sources": ["2004/542", "2005/544", "2006/1322"],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data={
            "1991/1707": {
                "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
                "contingent_effective_sources": ["2004/542", "2005/544", "2006/1322"],
                "source_pathology_codes": [],
                "source_incomplete": False,
                "fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            },
        },
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1991/1707"]["contingent_effective_date"] is True
    assert by_sid["1991/1707"]["is_suspect"] is True
    assert by_sid["1991/1707"]["bucket"] == "contingent_effective_date"
    assert (
        by_sid["1991/1707"]["top_diagnosis"]
        == "CONTINGENT_EFFECTIVE_DATE:2004/542,2005/544,2006/1322"
    )
    assert by_sid["1991/1707"]["contingent_effective_sources"] == "2004/542|2005/544|2006/1322"


def test_build_frontier_excludes_contingent_effective_date_rows_when_requested() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1991/1707", "similarity": 0.826, "amendments": 9},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1991/1707": {
                "total_divergences": 3,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 3,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "contingent_effective_sources": ["2004/542"],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data={
            "1991/1707": {
                "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
                "contingent_effective_sources": ["2004/542"],
                "source_pathology_codes": [],
                "source_incomplete": False,
                "fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            },
        },
        score_threshold=0.95,
        top=10,
        exclude_suspect=True,
    )

    assert [row["statute_id"] for row in rows] == ["1992/1702"]


def test_bucket_frontier_row_prefers_oracle_version_bucket_over_html_topology() -> None:
    bucket = _bucket_frontier_row(
        oracle_info={
            "suspect_fraction": 0.75,
            "top_diagnosis": "ORACLE_STALE",
            "replay_bug_count": 1,
            "replay_bug_unblamed_count": 0,
            "replay_bug_unblamed_fraction": 0.0,
        },
        version_gate=None,
        _strict_row=None,
        similarity=0.8,
        amendments=3,
        source_pathology=False,
        html_noncommensurable=False,
        html_topology_mismatch=True,
        contingent_effective_date=False,
    )

    assert bucket == "oracle_version_suspect"


def test_build_frontier_marks_live_contingent_effective_date_rows_suspect() -> None:
    rows = _build_frontier(
        bench_data=[
            {"statute_id": "1991/1707", "similarity": 0.826, "amendments": 9},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1991/1707": {
                "total_divergences": 3,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 3,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "contingent_effective_sources": ["1999/1301", "2004/542"],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
        score_threshold=0.95,
        top=10,
        exclude_suspect=False,
    )

    by_sid = {row["statute_id"]: row for row in rows}
    assert by_sid["1991/1707"]["contingent_effective_date"] is True
    assert by_sid["1991/1707"]["is_suspect"] is True
    assert by_sid["1991/1707"]["top_diagnosis"] == "CONTINGENT_EFFECTIVE_DATE:1999/1301,2004/542"


def test_summarize_low_scoring_rows_counts_source_and_base_drift_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1990/1295", "similarity": 0.889, "amendments": 18},
            {"statute_id": "1994/1205", "similarity": 0.835, "amendments": 14},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1990/1295": {
                "total_divergences": 36,
                "suspect_fraction": 0.0,
                "top_diagnosis": "MISSING",
                "replay_bug_count": 22,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "source_pathology": True,
                "source_pathology_codes": ["CONTAINER_MEMBERSHIP_MISMATCH"],
            },
            "1994/1205": {
                "total_divergences": 1,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 1,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 1.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
    )

    assert summary == {
        "total_low": 3,
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 1,
        "html_noncommensurable": 0,
        "html_topology": 0,
        "contingent_effective_date": 0,
        "base_drift": 1,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_summarize_low_scoring_rows_counts_contingent_effective_date_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1991/1707", "similarity": 0.826, "amendments": 9},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1991/1707": {
                "total_divergences": 3,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 3,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data={
            "1991/1707": {
                "projection_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
                "contingent_effective_sources": ["2004/542", "2005/544"],
                "source_pathology_codes": [],
                "source_incomplete": False,
                "fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
            },
        },
    )

    assert summary == {
        "total_low": 2,
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 0,
        "html_noncommensurable": 0,
        "html_topology": 0,
        "contingent_effective_date": 1,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_summarize_low_scoring_rows_counts_html_topology_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1994/1205", "similarity": 0.835, "amendments": 14},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1994/1205": {
                "total_divergences": 1,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 1,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "html_topology_mismatch": True,
                "html_missing_from_xml": ["8 a §"],
                "html_extra_in_xml": [],
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
    )

    assert summary == {
        "total_low": 2,
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 0,
        "html_noncommensurable": 0,
        "html_topology": 1,
        "contingent_effective_date": 0,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_summarize_low_scoring_rows_counts_html_noncommensurable_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1995/540", "similarity": 0.919, "amendments": 16},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1995/540": {
                "total_divergences": 28,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 12,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
                "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
    )

    assert summary == {
        "total_low": 2,
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 0,
        "html_noncommensurable": 1,
        "html_topology": 0,
        "contingent_effective_date": 0,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_summarize_low_scoring_rows_counts_no_oracle_check_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1978/38", "similarity": 0.677, "amendments": 57},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
    )

    assert summary == {
        "total_low": 2,
        "resolved_after_refresh": 0,
        "oracle_version_suspect": 0,
        "no_oracle_check": 1,
        "source_pathology": 0,
        "html_noncommensurable": 0,
        "html_topology": 0,
        "contingent_effective_date": 0,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_summarize_low_scoring_rows_counts_resolved_after_refresh_separately() -> None:
    summary = _summarize_low_scoring_rows(
        low_scoring=[
            {"statute_id": "1974/402", "similarity": 0.918, "amendments": 16},
            {"statute_id": "1992/1702", "similarity": 0.781, "amendments": 12},
        ],
        oracle_checks={
            "1974/402": {
                "total_divergences": 0,
                "suspect_fraction": 0.0,
                "top_diagnosis": "UNKNOWN",
                "replay_bug_count": 0,
                "replay_bug_unblamed_count": 0,
                "replay_bug_unblamed_fraction": 0.0,
            },
            "1992/1702": {
                "total_divergences": 8,
                "suspect_fraction": 0.0,
                "top_diagnosis": "REPLAY_MISSING",
                "replay_bug_count": 8,
                "replay_bug_unblamed_count": 1,
                "replay_bug_unblamed_fraction": 0.125,
            },
        },
        version_gates={},
        strict_data=None,
    )

    assert summary == {
        "total_low": 2,
        "resolved_after_refresh": 1,
        "oracle_version_suspect": 0,
        "no_oracle_check": 0,
        "source_pathology": 0,
        "html_noncommensurable": 0,
        "html_topology": 0,
        "contingent_effective_date": 0,
        "base_drift": 0,
        "other_suspect": 0,
        "candidate": 1,
    }


def test_load_strict_run_reads_source_pathology_codes_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,projection_kinds,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error,source_pathology_codes",
                "1994/1472,0,0,2,ELAB.SOURCE_PATHOLOGY|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,,MALFORMED_BROAD_REPLACE_BODY|DESTRUCTIVE_SHAPE_LOSS_RISK",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("lawvm.tools.frontier._strict_runs_dir", lambda: strict_dir)

    rows = _load_strict_run("demo")

    assert rows is not None
    assert rows["1994/1472"]["source_pathology_codes"] == [
        "MALFORMED_BROAD_REPLACE_BODY",
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]


def test_load_strict_run_reads_source_pathology_rows_json_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,projection_kinds,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error,source_pathology_codes,source_pathology_rows_json",
                '1994/1472,0,0,2,ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,,,\"[{""code"":""DESTRUCTIVE_SHAPE_LOSS_RISK"",""target_label"":""35 §"",""detail"":{""diagnostic_reason"":""partial_body_only""}}]"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("lawvm.tools.frontier._strict_runs_dir", lambda: strict_dir)

    rows = _load_strict_run("demo")

    assert rows is not None
    assert rows["1994/1472"]["source_pathology_rows"] == [
        {
            "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "target_label": "35 §",
            "detail": {"diagnostic_reason": "partial_body_only"},
        }
    ]


def test_load_strict_run_ignores_legacy_adjudication_kinds_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,"
                    "n_projection_rows,adjudication_kinds,fail_reasons,source_incomplete,"
                    "chain_length,source_available,elapsed_s,error"
                ),
                (
                    "1994/1472,0,0,2,"
                    "ELAB.SOURCE_PATHOLOGY|ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY,"
                    "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("lawvm.tools.frontier._strict_runs_dir", lambda: strict_dir)

    rows = _load_strict_run("demo")

    assert rows is not None
    assert rows["1994/1472"]["projection_kinds"] == []


def test_load_strict_run_reads_contingent_effective_sources_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,n_contingent_effective_dates,projection_kinds,source_pathology_codes,contingent_effective_sources,fail_reasons,source_incomplete,chain_length,source_available,elapsed_s,error",
                "1991/1707,0,0,6,0,3,TIME.CONTINGENT_EFFECTIVE_DATE,,2004/542|2005/544|2006/1322,TIME.CONTINGENT_EFFECTIVE_DATE,0,9,9,1.00,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("lawvm.tools.frontier._strict_runs_dir", lambda: strict_dir)

    rows = _load_strict_run("demo")

    assert rows is not None
    assert rows["1991/1707"]["contingent_effective_sources"] == [
        "2004/542",
        "2005/544",
        "2006/1322",
    ]


def test_load_oracle_check_cache_reads_statute_level_signals(tmp_path) -> None:
    db_path = tmp_path / "divergences.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE divergences (
            statute_id TEXT,
            title TEXT,
            overall_score REAL,
            section_score REAL,
            section TEXT,
            diagnosis TEXT,
            blame_source TEXT,
            blame_title TEXT,
            oracle_version TEXT,
            replay_text TEXT,
            oracle_text TEXT
        );
        CREATE TABLE statute_signals (
            statute_id TEXT PRIMARY KEY,
            source_pathology INTEGER,
            source_pathology_codes TEXT,
            source_pathology_rows_json TEXT,
            html_topology_mismatch INTEGER,
            html_missing_from_xml TEXT,
            html_extra_in_xml TEXT,
            html_noncommensurable_reason TEXT,
            contingent_effective_sources TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO divergences VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "1994/1205",
            "Test statute",
            0.83,
            0.83,
            "section:8a",
            "REPLAY_MISSING",
            "1999/1",
            "Test amendment",
            "",
            "replay",
            "oracle",
        ),
    )
    con.execute(
        "INSERT INTO statute_signals VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "1994/1205",
            1,
            "CONTAINER_MEMBERSHIP_MISMATCH|DESTRUCTIVE_SHAPE_LOSS_RISK",
            '[{"code":"CONTAINER_MEMBERSHIP_MISMATCH","detail":{"diagnostic_reason":"scoped_membership_conflict"},"target_label":"8 a §"}]',
            1,
            "8 a §|15 a §",
            "",
            "",
            "2004/542|2005/544",
        ),
    )
    con.commit()
    con.close()

    rows = _load_oracle_check_cache(db_path)

    assert rows["1994/1205"]["total_divergences"] == 1
    assert rows["1994/1205"]["source_pathology"] is True
    assert rows["1994/1205"]["source_pathology_codes"] == [
        "CONTAINER_MEMBERSHIP_MISMATCH",
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]
    assert rows["1994/1205"]["source_pathology_rows"] == [
        {
            "code": "CONTAINER_MEMBERSHIP_MISMATCH",
            "detail": {"diagnostic_reason": "scoped_membership_conflict"},
            "target_label": "8 a §",
        }
    ]
    assert rows["1994/1205"]["html_topology_mismatch"] is True
    assert rows["1994/1205"]["html_missing_from_xml"] == ["8 a §", "15 a §"]


def test_load_oracle_check_cache_reads_html_noncommensurable_reason(tmp_path) -> None:
    db_path = tmp_path / "divergences.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE divergences (
            statute_id TEXT,
            title TEXT,
            overall_score REAL,
            section_score REAL,
            section TEXT,
            diagnosis TEXT,
            blame_source TEXT,
            blame_title TEXT,
            oracle_version TEXT,
            replay_text TEXT,
            oracle_text TEXT
        );
        CREATE TABLE statute_signals (
            statute_id TEXT PRIMARY KEY,
            source_pathology INTEGER,
            source_pathology_codes TEXT,
            source_pathology_rows_json TEXT,
            html_topology_mismatch INTEGER,
            html_missing_from_xml TEXT,
            html_extra_in_xml TEXT,
            html_noncommensurable_reason TEXT,
            contingent_effective_sources TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO divergences VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "1995/540",
            "Test statute",
            0.92,
            0.92,
            "section:1",
            "REPLAY_MISSING",
            "",
            "",
            "",
            "replay",
            "oracle",
        ),
    )
    con.execute(
        "INSERT INTO statute_signals VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "1995/540",
            0,
            "",
            "",
            0,
            "",
            "",
            "duplicate_unscoped_oracle_labels:section:1",
            "",
        ),
    )
    con.commit()
    con.close()

    rows = _load_oracle_check_cache(db_path)

    assert rows["1995/540"]["html_noncommensurable_reason"] == (
        "duplicate_unscoped_oracle_labels:section:1"
    )
    assert rows["1995/540"]["html_extra_in_xml"] == []
    assert rows["1995/540"]["contingent_effective_sources"] == []


def test_frontier_main_refresh_all_oracle_check_overrides_large_pool(monkeypatch, capsys) -> None:
    bench_rows = [
        {"statute_id": f"1992/{i}", "similarity": 0.80, "amendments": 3}
        for i in range(FRESH_ORACLE_CHECK_LIMIT + 5)
    ]
    refreshed_calls: list[list[str]] = []

    monkeypatch.setattr("lawvm.tools.frontier._load_bench_run", lambda label: bench_rows)
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr(
        "lawvm.tools.frontier._run_oracle_checks_parallel",
        lambda sids, workers, mode="finlex_oracle", progress=True: refreshed_calls.append(list(sids)) or {},
    )
    monkeypatch.setattr("lawvm.tools.frontier._should_refresh_all_low_scoring_scores", lambda rows: False)
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr("lawvm.tools.frontier._build_frontier", lambda **kwargs: [])
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": len(bench_rows),
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 0,
        },
    )
    monkeypatch.setattr("lawvm.tools.frontier._print_frontier", lambda *args, **kwargs: None)

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=10,
            exclude_suspect=True,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=True,
            refresh_all_scores=False,
        )
    )

    assert refreshed_calls == [[row["statute_id"] for row in bench_rows]]
    captured = capsys.readouterr()
    assert "forced full refresh" in captured.out


def test_frontier_main_refresh_all_scores_overrides_large_pool(monkeypatch, capsys) -> None:
    bench_rows = [
        {"statute_id": f"1992/{i}", "similarity": 0.80, "amendments": 3}
        for i in range(FRESH_SCORE_REFRESH_LIMIT + 5)
    ]
    refreshed_calls: list[list[str]] = []

    monkeypatch.setattr("lawvm.tools.frontier._load_bench_run", lambda label: bench_rows)
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr("lawvm.tools.frontier._run_oracle_checks_parallel", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "lawvm.tools.frontier._run_score_refresh_parallel",
        lambda sids, workers, mode="finlex_oracle", progress=True: (
            refreshed_calls.append(list(sids))
            or {sid: {"similarity": 0.99, "status": "OK"} for sid in sids}
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr("lawvm.tools.frontier._build_frontier", lambda **kwargs: [])
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": 0,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 0,
        },
    )
    monkeypatch.setattr("lawvm.tools.frontier._print_frontier", lambda *args, **kwargs: None)

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=10,
            exclude_suspect=True,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=False,
            refresh_all_scores=True,
        )
    )

    assert refreshed_calls == [[row["statute_id"] for row in bench_rows]]
    captured = capsys.readouterr()
    assert "Refreshing current scores for all" in captured.out
    assert "forced full refresh" in captured.out


def test_frontier_main_bucket_report_prints_grouped_rows(monkeypatch, capsys) -> None:
    bench_rows = [
        {"statute_id": "1992/1702", "similarity": 0.80, "amendments": 3},
        {"statute_id": "1995/540", "similarity": 0.92, "amendments": 16},
        {"statute_id": "1991/1707", "similarity": 0.79, "amendments": 9},
    ]

    monkeypatch.setattr("lawvm.tools.frontier._load_bench_run", lambda label: bench_rows)
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr("lawvm.tools.frontier._run_oracle_checks_parallel", lambda *args, **kwargs: {})
    monkeypatch.setattr("lawvm.tools.frontier._should_refresh_all_low_scoring_scores", lambda rows: False)
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_frontier",
        lambda **kwargs: [
            {
                "statute_id": "1992/1702",
                "score": 0.80,
                "replay_loss": 0.20,
                "fixability": 0.10,
                "bucket": "candidate",
                "is_suspect": False,
                "top_diagnosis": "REPLAY_MISSING",
                "amendments": 3,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.0,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
            },
            {
                "statute_id": "1995/540",
                "score": 0.92,
                "replay_loss": 0.08,
                "fixability": 0.01,
                "bucket": "html_noncommensurable",
                "is_suspect": True,
                "top_diagnosis": "HTML_NONCOMMENSURABLE:duplicate",
                "amendments": 16,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.04,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
            },
            {
                "statute_id": "1991/1707",
                "score": 0.79,
                "replay_loss": 0.21,
                "fixability": 0.02,
                "bucket": "contingent_effective_date",
                "is_suspect": True,
                "top_diagnosis": "CONTINGENT_EFFECTIVE_DATE:2004/542",
                "amendments": 9,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": True,
                "contingent_effective_sources": "2004/542",
                "projection_kinds": "",
                "suspect_fraction": 0.0,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
            },
        ],
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": len(bench_rows),
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 1,
            "html_topology": 0,
            "contingent_effective_date": 1,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 1,
        },
    )

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=2,
            exclude_suspect=False,
            bucket=None,
            bucket_report=True,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=False,
            refresh_all_scores=False,
        )
    )

    out = capsys.readouterr().out
    assert "Frontier Bucket Report" in out
    assert "=== Honest Frontier: top 2 replay targets" in out
    assert "[candidate] 1 statute(s)" in out
    assert "[html_noncommensurable] 1 statute(s)" in out
    assert "[contingent_effective_date] 1 statute(s)" in out
    assert "1995/540" in out
    assert "duplicate_unscoped_oracle_labels:section:1" in out


def test_frontier_main_json_output_emits_clean_payload(monkeypatch, capsys) -> None:
    bench_rows = [
        {"statute_id": "1992/1702", "similarity": 0.80, "amendments": 3},
        {"statute_id": "1995/540", "similarity": 0.92, "amendments": 16},
    ]

    monkeypatch.setattr("lawvm.tools.frontier._load_bench_run", lambda label: bench_rows)
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr("lawvm.tools.frontier._run_oracle_checks_parallel", lambda *args, **kwargs: {})
    monkeypatch.setattr("lawvm.tools.frontier._should_refresh_all_low_scoring_scores", lambda rows: False)
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_frontier",
        lambda **kwargs: [
            {
                "statute_id": "1992/1702",
                "score": 0.80,
                "replay_loss": 0.20,
                "fixability": 0.10,
                "bucket": "candidate",
                "is_suspect": False,
                "top_diagnosis": "REPLAY_MISSING",
                "amendments": 3,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.0,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
            },
            {
                "statute_id": "1995/540",
                "score": 0.92,
                "replay_loss": 0.08,
                "fixability": 0.01,
                "bucket": "html_noncommensurable",
                "is_suspect": True,
                "top_diagnosis": "HTML_NONCOMMENSURABLE:duplicate",
                "amendments": 16,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.04,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
            },
        ],
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": len(bench_rows),
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 1,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 1,
        },
    )

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=2,
            exclude_suspect=False,
            bucket=None,
            bucket_report=True,
            json_output=True,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=False,
            refresh_all_scores=False,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["label"] == "demo"
    assert payload["summary"]["html_noncommensurable"] == 1
    assert payload["rows"][0]["statute_id"] == "1992/1702"
    assert payload["buckets"]["html_noncommensurable"][0]["statute_id"] == "1995/540"
    assert payload["saved_csv"] == ""


def test_build_proof_report_rows_reads_live_evidence_bundle(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda statute_id, mode="legal_pit": {
            "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
            "proof_tiers": ["PROVED_ORACLE_INCORRECT", "PROVED_SOURCE_PATHOLOGY"],
            "proof_claims": [
                {"kind": "xml_html_topology_drift"},
                {"kind": "TIME.CONTINGENT_EFFECTIVE_DATE"},
            ],
            "section_claims": [
                {
                    "selected_kind": "oracle_section_stale",
                    "selected_inference_rule": "oracle_payload_prefers_previous_state",
                    "defeated_candidate_kinds": [],
                    "defeated_candidates": [],
                },
                {
                    "selected_kind": "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity",
                    "selected_inference_rule": "blamed_amendment_has_same_section_frontend_elaboration_observation",
                    "defeated_candidate_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
                    "defeated_candidates": [
                        {
                            "kind": "UNRESOLVED.preexisting.baseline_residue",
                            "inference_rule": "replay_residue_predates_any_amendment_drop",
                        }
                    ],
                },
            ],
            "strict_fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
        },
    )

    rows = _build_proof_report_rows(
        [{"statute_id": "1991/1707", "bucket": "html_topology", "score": 0.79}],
        mode="legal_pit",
    )

    assert rows == [
        {
            "statute_id": "1991/1707",
            "bucket": "html_topology",
            "score": 0.79,
            "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
            "proof_tiers": ["PROVED_ORACLE_INCORRECT", "PROVED_SOURCE_PATHOLOGY"],
            "proof_kinds": ["xml_html_topology_drift", "TIME.CONTINGENT_EFFECTIVE_DATE"],
            "section_claim_count": 2,
            "selected_section_claim_count": 2,
            "section_claim_kinds": [
                "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity",
                "oracle_section_stale",
            ],
            "statute_only_proof_kinds": [
                "xml_html_topology_drift",
                "TIME.CONTINGENT_EFFECTIVE_DATE",
            ],
            "section_claim_rules": [
                "blamed_amendment_has_same_section_frontend_elaboration_observation",
                "oracle_payload_prefers_previous_state",
            ],
            "defeated_section_claim_kinds": [
                "UNRESOLVED.preexisting.baseline_residue",
            ],
            "defeated_section_claim_rules": [
                "replay_residue_predates_any_amendment_drop",
            ],
            "alternative_replay_match_count": 0,
            "alternative_replay_sections": [],
            "strict_fail_reasons": ["TIME.CONTINGENT_EFFECTIVE_DATE"],
        }
    ]


def test_apply_proof_rebucketing_demotes_candidate_source_pathology_row() -> None:
    rows, proof_rows = _apply_proof_rebucketing(
        [
            {"statute_id": "2010/1257", "bucket": "candidate", "score": 0.82},
            {"statute_id": "1991/1707", "bucket": "html_topology", "score": 0.79},
        ],
        [
            {
                "statute_id": "2010/1257",
                "bucket": "candidate",
                "primary_proof_tier": "PROVED_SOURCE_PATHOLOGY",
            },
            {
                "statute_id": "1991/1707",
                "bucket": "html_topology",
                "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
            },
        ],
        exclude_suspect=False,
    )

    assert rows == [
        {"statute_id": "2010/1257", "bucket": "source_pathology", "score": 0.82},
        {"statute_id": "1991/1707", "bucket": "html_topology", "score": 0.79},
    ]
    assert proof_rows == [
        {
            "statute_id": "2010/1257",
            "bucket": "source_pathology",
            "primary_proof_tier": "PROVED_SOURCE_PATHOLOGY",
        },
        {
            "statute_id": "1991/1707",
            "bucket": "html_topology",
            "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
        },
    ]


def test_apply_proof_rebucketing_demotes_unresolved_drift_rows() -> None:
    rows, proof_rows = _apply_proof_rebucketing(
        [
            {"statute_id": "1992/1702", "bucket": "candidate", "score": 0.78},
            {"statute_id": "1988/161", "bucket": "candidate", "score": 0.81},
        ],
        [
            {
                "statute_id": "1992/1702",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_section_drift"],
            },
            {
                "statute_id": "1988/161",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_replay_drift"],
            },
        ],
        exclude_suspect=False,
    )

    assert rows == [
        {"statute_id": "1992/1702", "bucket": "base_drift", "score": 0.78},
        {"statute_id": "1988/161", "bucket": "other_suspect", "score": 0.81},
    ]
    assert proof_rows == [
        {
            "statute_id": "1992/1702",
            "bucket": "base_drift",
            "primary_proof_tier": "UNRESOLVED",
            "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_section_drift"],
        },
        {
            "statute_id": "1988/161",
            "bucket": "other_suspect",
            "primary_proof_tier": "UNRESOLVED",
            "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_replay_drift"],
        },
    ]


def test_apply_proof_rebucketing_demotes_unresolved_no_strong_claim_rows() -> None:
    rows, proof_rows = _apply_proof_rebucketing(
        [
            {"statute_id": "2003/605", "bucket": "candidate", "score": 0.83},
        ],
        [
            {
                "statute_id": "2003/605",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["no_strong_claim"],
            },
        ],
        exclude_suspect=False,
    )

    assert rows == [
        {"statute_id": "2003/605", "bucket": "other_suspect", "score": 0.83},
    ]
    assert proof_rows == [
        {
            "statute_id": "2003/605",
            "bucket": "other_suspect",
            "primary_proof_tier": "UNRESOLVED",
            "proof_kinds": ["no_strong_claim"],
        },
    ]


def test_apply_proof_rebucketing_demotes_unresolved_preexisting_only_section_claim_rows() -> None:
    rows, proof_rows = _apply_proof_rebucketing(
        [
            {"statute_id": "2023/693", "bucket": "candidate", "score": 0.98},
            {"statute_id": "1970/456", "bucket": "candidate", "score": 0.94},
        ],
        [
            {
                "statute_id": "2023/693",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
                "section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
            },
            {
                "statute_id": "1970/456",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
                "section_claim_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
            },
        ],
        exclude_suspect=False,
    )

    assert rows == [
        {"statute_id": "2023/693", "bucket": "base_drift", "score": 0.98},
        {"statute_id": "1970/456", "bucket": "base_drift", "score": 0.94},
    ]
    assert proof_rows == [
        {
            "statute_id": "2023/693",
            "bucket": "base_drift",
            "primary_proof_tier": "UNRESOLVED",
            "proof_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
            "section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
        },
        {
            "statute_id": "1970/456",
            "bucket": "base_drift",
            "primary_proof_tier": "UNRESOLVED",
            "proof_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
            "section_claim_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
        },
    ]


def test_apply_proof_rebucketing_to_summary_moves_candidate_counts() -> None:
    adjusted = _apply_proof_rebucketing_to_summary(
        {
            "total_low": 3,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 1,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 2,
        },
        [
            {"statute_id": "2010/1257", "bucket": "candidate"},
            {"statute_id": "1991/1707", "bucket": "candidate"},
        ],
        [
            {
                "statute_id": "2010/1257",
                "bucket": "candidate",
                "primary_proof_tier": "PROVED_SOURCE_PATHOLOGY",
            },
            {
                "statute_id": "1991/1707",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
            },
        ],
    )

    assert adjusted["candidate"] == 1
    assert adjusted["source_pathology"] == 2


def test_apply_proof_rebucketing_to_summary_reassigns_unresolved_drift() -> None:
    adjusted = _apply_proof_rebucketing_to_summary(
        {
            "total_low": 2,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 2,
        },
        [
            {"statute_id": "1992/1702", "bucket": "candidate"},
            {"statute_id": "1988/161", "bucket": "candidate"},
        ],
        [
            {
                "statute_id": "1992/1702",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_section_drift"],
            },
            {
                "statute_id": "1988/161",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.address_projection.same_chapter_replay_drift"],
            },
        ],
    )

    assert adjusted["candidate"] == 0
    assert adjusted["base_drift"] == 1
    assert adjusted["other_suspect"] == 1


def test_apply_proof_rebucketing_to_summary_reassigns_unresolved_no_strong_claim() -> None:
    adjusted = _apply_proof_rebucketing_to_summary(
        {
            "total_low": 1,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 1,
        },
        [
            {"statute_id": "2003/605", "bucket": "candidate"},
        ],
        [
            {
                "statute_id": "2003/605",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["no_strong_claim"],
            },
        ],
    )

    assert adjusted["candidate"] == 0
    assert adjusted["other_suspect"] == 1


def test_apply_proof_rebucketing_to_summary_reassigns_unresolved_preexisting_only_section_claims() -> None:
    adjusted = _apply_proof_rebucketing_to_summary(
        {
            "total_low": 2,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 2,
        },
        [
            {"statute_id": "2023/693", "bucket": "candidate"},
            {"statute_id": "1970/456", "bucket": "candidate"},
        ],
        [
            {
                "statute_id": "2023/693",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
                "section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
            },
            {
                "statute_id": "1970/456",
                "bucket": "candidate",
                "primary_proof_tier": "UNRESOLVED",
                "proof_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
                "section_claim_kinds": ["UNRESOLVED.preexisting.same_section_structure_drift"],
            },
        ],
    )

    assert adjusted["candidate"] == 0
    assert adjusted["base_drift"] == 2


def test_summarize_proof_rows_counts_tiers_and_kinds() -> None:
    summary = _summarize_proof_rows(
        [
            {
                "statute_id": "1991/1707",
                "bucket": "html_topology",
                "primary_proof_tier": "PROVED_ORACLE_INCORRECT",
                "proof_kinds": ["xml_html_topology_drift", "TIME.CONTINGENT_EFFECTIVE_DATE"],
                "section_claim_kinds": ["oracle_section_stale"],
                "statute_only_proof_kinds": ["TIME.CONTINGENT_EFFECTIVE_DATE", "xml_html_topology_drift"],
                "section_claim_rules": ["oracle_payload_prefers_previous_state"],
                "defeated_section_claim_kinds": [],
                "defeated_section_claim_rules": [],
                "alternative_replay_sections": ["section:5"],
            },
            {
                "statute_id": "1995/1556",
                "bucket": "candidate",
                "primary_proof_tier": "PROVED_REPLAY_BUG",
                "proof_kinds": ["replay_divergence"],
                "section_claim_kinds": ["replay_divergence", "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity"],
                "statute_only_proof_kinds": [],
                "section_claim_rules": [
                    "blamed_amendment_has_same_section_frontend_elaboration_observation",
                    "replay_divergence_matches_oracle_bug_surface",
                ],
                "defeated_section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
                "defeated_section_claim_rules": ["replay_residue_predates_any_amendment_drop"],
                "alternative_replay_sections": [],
            },
            {
                "statute_id": "1995/540",
                "bucket": "html_noncommensurable",
                "primary_proof_tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                "proof_kinds": ["html_xml_scope_noncommensurable"],
                "section_claim_kinds": [],
                "statute_only_proof_kinds": ["html_xml_scope_noncommensurable"],
                "section_claim_rules": [],
                "defeated_section_claim_kinds": [],
                "defeated_section_claim_rules": [],
                "alternative_replay_sections": [],
            },
        ]
    )

    assert summary["primary_tiers"] == {
        "PROVED_HTML_XML_NONCOMMENSURABLE": 1,
        "PROVED_ORACLE_INCORRECT": 1,
        "PROVED_REPLAY_BUG": 1,
    }
    assert summary["proof_kinds"] == {
        "TIME.CONTINGENT_EFFECTIVE_DATE": 1,
        "html_xml_scope_noncommensurable": 1,
        "replay_divergence": 1,
        "xml_html_topology_drift": 1,
    }
    assert summary["section_claim_kinds"] == {
        "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity": 1,
        "oracle_section_stale": 1,
        "replay_divergence": 1,
    }
    assert summary["statute_only_proof_kinds"] == {
        "TIME.CONTINGENT_EFFECTIVE_DATE": 1,
        "html_xml_scope_noncommensurable": 1,
        "xml_html_topology_drift": 1,
    }
    assert summary["section_claim_rules"] == {
        "blamed_amendment_has_same_section_frontend_elaboration_observation": 1,
        "oracle_payload_prefers_previous_state": 1,
        "replay_divergence_matches_oracle_bug_surface": 1,
    }
    assert summary["defeated_section_claim_kinds"] == {
        "UNRESOLVED.preexisting.baseline_residue": 1,
    }
    assert summary["defeated_section_claim_rules"] == {
        "replay_residue_predates_any_amendment_drop": 1,
    }
    assert summary["alternative_replay_sections"] == {
        "section:5": 1,
    }
    assert summary["bucket_primary_tiers"] == {
        "candidate:PROVED_REPLAY_BUG": 1,
        "html_noncommensurable:PROVED_HTML_XML_NONCOMMENSURABLE": 1,
        "html_topology:PROVED_ORACLE_INCORRECT": 1,
    }


def test_build_evidence_bundles_reads_live_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda statute_id, mode="legal_pit": {
            "statute_id": statute_id,
            "mode": mode,
            "primary_proof_tier": "PROVED_REPLAY_BUG",
        },
    )

    bundles = _build_evidence_bundles(
        [{"statute_id": "1995/1556"}, {"statute_id": "1991/1707"}],
        mode="legal_pit",
    )

    assert bundles == [
        {"statute_id": "1995/1556", "mode": "legal_pit", "primary_proof_tier": "PROVED_REPLAY_BUG"},
        {"statute_id": "1991/1707", "mode": "legal_pit", "primary_proof_tier": "PROVED_REPLAY_BUG"},
    ]


def test_frontier_main_json_output_includes_proof_rows(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.tools.frontier._load_bench_run",
        lambda label: [{"statute_id": "1991/1707", "similarity": 0.79, "amendments": 9}],
    )
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_frontier",
        lambda **kwargs: [
            {
                "statute_id": "1991/1707",
                "score": 0.79,
                "replay_loss": 0.21,
                "fixability": 0.6,
                "is_suspect": False,
                "top_diagnosis": "REPLAY_EXTRA",
                "amendments": 9,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.0,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
                "bucket": "candidate",
            }
        ],
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": 1,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 1,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_proof_report_rows",
        lambda rows, mode: [
            {
                "statute_id": "1991/1707",
                "bucket": "candidate",
                "score": 0.79,
                "primary_proof_tier": "PROVED_REPLAY_BUG",
                "proof_tiers": ["PROVED_REPLAY_BUG"],
                "proof_kinds": ["replay_divergence"],
                "section_claim_count": 2,
                "selected_section_claim_count": 2,
                "section_claim_kinds": ["replay_divergence", "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity"],
                "statute_only_proof_kinds": [],
                "section_claim_rules": [
                    "blamed_amendment_has_same_section_frontend_elaboration_observation",
                    "replay_divergence_matches_oracle_bug_surface",
                ],
                "defeated_section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
                "defeated_section_claim_rules": ["replay_residue_predates_any_amendment_drop"],
                "alternative_replay_match_count": 1,
                "alternative_replay_sections": ["chapter:8/section:41"],
                "strict_fail_reasons": [],
            }
        ],
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_proof_rows",
        lambda rows: {
            "primary_tiers": {"PROVED_REPLAY_BUG": 1},
            "proof_kinds": {"replay_divergence": 1},
            "section_claim_kinds": {
                "replay_divergence": 1,
                "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity": 1,
            },
            "statute_only_proof_kinds": {},
            "section_claim_rules": {
                "blamed_amendment_has_same_section_frontend_elaboration_observation": 1,
                "replay_divergence_matches_oracle_bug_surface": 1,
            },
            "defeated_section_claim_kinds": {
                "UNRESOLVED.preexisting.baseline_residue": 1,
            },
            "defeated_section_claim_rules": {
                "replay_residue_predates_any_amendment_drop": 1,
            },
            "alternative_replay_sections": {
                "chapter:8/section:41": 1,
            },
            "bucket_primary_tiers": {"candidate:PROVED_REPLAY_BUG": 1},
        },
    )

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=5,
            exclude_suspect=False,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=False,
            refresh_all_scores=False,
            bucket=None,
            bucket_report=False,
            proof_report=True,
            proof_summary=True,
            json_output=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["proof_rows"] == [
        {
            "statute_id": "1991/1707",
            "bucket": "candidate",
            "score": 0.79,
            "primary_proof_tier": "PROVED_REPLAY_BUG",
            "proof_tiers": ["PROVED_REPLAY_BUG"],
            "proof_kinds": ["replay_divergence"],
            "section_claim_count": 2,
            "selected_section_claim_count": 2,
            "section_claim_kinds": ["replay_divergence", "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity"],
            "statute_only_proof_kinds": [],
            "section_claim_rules": [
                "blamed_amendment_has_same_section_frontend_elaboration_observation",
                "replay_divergence_matches_oracle_bug_surface",
            ],
            "defeated_section_claim_kinds": ["UNRESOLVED.preexisting.baseline_residue"],
            "defeated_section_claim_rules": ["replay_residue_predates_any_amendment_drop"],
            "alternative_replay_match_count": 1,
            "alternative_replay_sections": ["chapter:8/section:41"],
            "strict_fail_reasons": [],
        }
    ]
    assert payload["proof_summary"] == {
        "primary_tiers": {"PROVED_REPLAY_BUG": 1},
        "proof_kinds": {"replay_divergence": 1},
        "section_claim_kinds": {
            "replay_divergence": 1,
            "UNRESOLVED.source_underdetermined.frontend_elaboration_ambiguity": 1,
        },
        "statute_only_proof_kinds": {},
        "section_claim_rules": {
            "blamed_amendment_has_same_section_frontend_elaboration_observation": 1,
            "replay_divergence_matches_oracle_bug_surface": 1,
        },
        "defeated_section_claim_kinds": {
            "UNRESOLVED.preexisting.baseline_residue": 1,
        },
        "defeated_section_claim_rules": {
            "replay_residue_predates_any_amendment_drop": 1,
        },
        "alternative_replay_sections": {
            "chapter:8/section:41": 1,
        },
        "bucket_primary_tiers": {"candidate:PROVED_REPLAY_BUG": 1},
    }
    assert payload["saved_proof_jsonl"] == ""
    assert payload["saved_evidence_jsonl"] == ""


def test_frontier_main_json_output_demotes_proof_rejected_candidates(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.tools.frontier._load_bench_run",
        lambda label: [{"statute_id": "2010/1257", "similarity": 0.8227, "amendments": 4}],
    )
    monkeypatch.setattr("lawvm.tools.frontier._load_oracle_check_cache", lambda path: {})
    monkeypatch.setattr(
        "lawvm.tools.frontier.get_consolidated_oracle_suspect_cache_only",
        lambda sid: ("", ""),
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_frontier",
        lambda **kwargs: [
            {
                "statute_id": "2010/1257",
                "score": 0.8227,
                "replay_loss": 0.1773,
                "fixability": 0.09,
                "is_suspect": False,
                "top_diagnosis": "ORACLE_STALE",
                "amendments": 4,
                "source_incomplete": False,
                "source_pathology": False,
                "source_pathology_codes": "",
                "html_noncommensurable_reason": "",
                "html_topology_mismatch": False,
                "html_missing_from_xml": "",
                "html_extra_in_xml": "",
                "contingent_effective_date": False,
                "contingent_effective_sources": "",
                "projection_kinds": "",
                "suspect_fraction": 0.0,
                "oracle_version_suspect": "",
                "oracle_version_pending": "",
                "bucket": "candidate",
            }
        ],
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._summarize_low_scoring_rows",
        lambda *args, **kwargs: {
            "total_low": 1,
            "resolved_after_refresh": 0,
            "oracle_version_suspect": 0,
            "no_oracle_check": 0,
            "source_pathology": 0,
            "html_noncommensurable": 0,
            "html_topology": 0,
            "contingent_effective_date": 0,
            "base_drift": 0,
            "other_suspect": 0,
            "candidate": 1,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.frontier._build_proof_report_rows",
        lambda rows, mode: [
            {
                "statute_id": "2010/1257",
                "bucket": "candidate",
                "score": 0.8227,
                "primary_proof_tier": "PROVED_SOURCE_PATHOLOGY",
                "proof_tiers": ["PROVED_SOURCE_PATHOLOGY", "UNRESOLVED"],
                "proof_kinds": ["blamed_source_lacks_payload_support"],
                "section_claim_count": 1,
                "section_claim_kinds": ["blamed_source_lacks_payload_support"],
                "statute_only_proof_kinds": [],
                "section_claim_rules": [],
                "defeated_section_claim_kinds": [],
                "defeated_section_claim_rules": [],
                "alternative_replay_match_count": 0,
                "alternative_replay_sections": [],
                "strict_fail_reasons": ["PARSE.EXTRACTION_FALLBACK"],
            }
        ],
    )

    main(
        SimpleNamespace(
            label="demo",
            mode="legal_pit",
            top=5,
            exclude_suspect=True,
            strict_label=None,
            corpus=None,
            export_low_corpus=None,
            db=None,
            threshold=0.95,
            parallel=1,
            no_save=True,
            refresh_all_oracle_check=False,
            refresh_all_scores=False,
            bucket=None,
            bucket_report=False,
            proof_report=True,
            proof_summary=True,
            json_output=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["candidate"] == 0
    assert payload["summary"]["source_pathology"] == 1
    assert payload["rows"] == []
    assert payload["proof_rows"] == [
        {
            "statute_id": "2010/1257",
            "bucket": "source_pathology",
            "score": 0.8227,
            "primary_proof_tier": "PROVED_SOURCE_PATHOLOGY",
            "proof_tiers": ["PROVED_SOURCE_PATHOLOGY", "UNRESOLVED"],
            "proof_kinds": ["blamed_source_lacks_payload_support"],
            "section_claim_count": 1,
            "section_claim_kinds": ["blamed_source_lacks_payload_support"],
            "statute_only_proof_kinds": [],
            "section_claim_rules": [],
            "defeated_section_claim_kinds": [],
            "defeated_section_claim_rules": [],
            "alternative_replay_match_count": 0,
            "alternative_replay_sections": [],
            "strict_fail_reasons": ["PARSE.EXTRACTION_FALLBACK"],
        }
    ]
    assert payload["proof_summary"] == {
        "primary_tiers": {"PROVED_SOURCE_PATHOLOGY": 1},
        "proof_kinds": {"blamed_source_lacks_payload_support": 1},
        "section_claim_kinds": {"blamed_source_lacks_payload_support": 1},
        "statute_only_proof_kinds": {},
        "section_claim_rules": {},
        "defeated_section_claim_kinds": {},
        "defeated_section_claim_rules": {},
        "alternative_replay_sections": {},
        "bucket_primary_tiers": {"source_pathology:PROVED_SOURCE_PATHOLOGY": 1},
    }


def test_save_proof_report_jsonl_writes_jsonl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lawvm.tools.frontier._frontier_reports_dir", lambda: tmp_path)

    path = _save_proof_report_jsonl(
        [{"statute_id": "1991/1707", "primary_proof_tier": "PROVED_ORACLE_INCORRECT"}],
        "demo",
    )

    assert path == tmp_path / "demo_frontier_proof.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["statute_id"] == "1991/1707"


def test_save_evidence_bundles_jsonl_writes_jsonl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lawvm.tools.frontier._frontier_reports_dir", lambda: tmp_path)

    path = _save_evidence_bundles_jsonl(
        [{"statute_id": "1991/1707", "primary_proof_tier": "PROVED_ORACLE_INCORRECT"}],
        "demo",
    )

    assert path == tmp_path / "demo_frontier_evidence.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["primary_proof_tier"] == "PROVED_ORACLE_INCORRECT"

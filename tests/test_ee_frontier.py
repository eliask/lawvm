from __future__ import annotations

import argparse
import csv
from types import SimpleNamespace

from lawvm.tools import cli, ee_frontier


def test_cli_parser_accepts_ee_frontier() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        ["ee-frontier", "--label", "demo", "--top", "5", "--include-adjudicated", "--json"]
    )

    assert args.command == "ee-frontier"
    assert args.label == "demo"
    assert args.top == 5
    assert args.include_adjudicated is True
    assert args.json is True


def test_build_frontier_payload_prefers_open_rows_and_separates_adjudicated(tmp_path, monkeypatch) -> None:
    run_path = tmp_path / "demo.csv"
    with run_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
                "comparison_class",
                "source_basis",
                "core_benchmark",
                "adjudicated_residual_count",
                "matched_current_residual_count",
                "adjudicated_bucket_counts",
                "unknown_current_residual_count",
                "open_current_divergence_count",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "grupi_id": "g1",
                "base_id": "open-pair",
                "oracle_id": "o1",
                "title": "Open Pair",
                "n_ops": "10",
                "n_divs": "5",
                "sec_match": "0.9500",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "2",
                "matched_current_residual_count": "2",
                "adjudicated_bucket_counts": "source_oracle_drift=2",
                "unknown_current_residual_count": "3",
                "open_current_divergence_count": "3",
            }
        )
        writer.writerow(
            {
                "grupi_id": "g2",
                "base_id": "adjudicated-pair",
                "oracle_id": "o2",
                "title": "Adjudicated Pair",
                "n_ops": "20",
                "n_divs": "7",
                "sec_match": "0.9000",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "7",
                "matched_current_residual_count": "7",
                "adjudicated_bucket_counts": "appendix_display_pathology=1,source_oracle_drift=6",
                "unknown_current_residual_count": "0",
                "open_current_divergence_count": "0",
            }
        )

    dummy_archive = SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(ee_frontier, "open_rt_archive", lambda *args, **kwargs: dummy_archive)
    monkeypatch.setattr(ee_frontier, "fetch_rt_xml", lambda aktviide, archive=None: f"<{aktviide}>".encode("utf-8"))
    monkeypatch.setattr(ee_frontier, "extract_effective_date", lambda xml: "2025-01-01")
    monkeypatch.setattr(
        ee_frontier,
        "plan_ee_oracle_pair",
        lambda **kwargs: SimpleNamespace(
            plan=SimpleNamespace(source_basis=SimpleNamespace(value="pairwise_terviktekst_delta"))
        ),
    )

    payload = ee_frontier.build_frontier_payload(str(run_path), top=10)

    assert payload["open_row_count"] == 1
    assert payload["open_headline_row_count"] == 1
    assert payload["open_nonheadline_row_count"] == 0
    assert payload["adjudicated_nonzero_row_count"] == 1
    assert payload["legacy_unclassified_nonzero_row_count"] == 0
    assert payload["benchmark_reporting_strata_counts"] == {
        "EE_CORE_COMMENSURABLE": 2,
        "EE_BASE_IS_ORACLE": 0,
        "EE_FORWARD_LOOKING_ORACLE": 0,
        "EE_SAME_CHAIN_EDITORIAL_DRIFT": 0,
        "EE_NONCORE_SOURCE_GAP": 0,
    }
    assert payload["benchmark_reporting_headline_row_count"] == 2
    assert payload["comparison_policy"]["non_silent_rule_count"] == 2
    assert payload["comparison_policy"]["non_silent_rule_classes"] == [
        "lexical_institutional_drift",
        "manual_exception",
    ]
    assert payload["rows"][0]["base_id"] == "open-pair"
    assert payload["rows"][0]["source_basis"] == "pairwise_terviktekst_delta"
    assert payload["rows"][0]["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["rows"][0]["benchmark_reporting_headline_eligible"] is True
    assert payload["adjudicated_rows"][0]["base_id"] == "adjudicated-pair"
    assert payload["adjudicated_rows"][0]["source_basis"] == "pairwise_terviktekst_delta"
    assert payload["adjudicated_rows"][0]["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["adjudicated_rows"][0]["benchmark_reporting_headline_eligible"] is True

def test_build_frontier_payload_treats_legacy_nonzero_rows_as_open(tmp_path) -> None:
    run_path = tmp_path / "legacy.csv"
    with run_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "grupi_id": "g1",
                "base_id": "legacy-open",
                "oracle_id": "o1",
                "title": "Legacy Pair",
                "n_ops": "5",
                "n_divs": "4",
                "sec_match": "0.8000",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
            }
        )

    payload = ee_frontier.build_frontier_payload(str(run_path), top=10)

    assert payload["open_row_count"] == 1
    assert payload["open_headline_row_count"] == 0
    assert payload["open_nonheadline_row_count"] == 1
    assert payload["legacy_unclassified_nonzero_row_count"] == 0
    assert payload["rows"][0]["base_id"] == "legacy-open"
    assert payload["rows"][0]["frontier_bucket"] == "open"
    assert payload["rows"][0]["open_current_divergence_count"] == 4
    assert payload["rows"][0]["benchmark_reporting_stratum"] == "EE_NONCORE_SOURCE_GAP"
    assert payload["rows"][0]["benchmark_reporting_headline_eligible"] is False


def test_build_frontier_payload_treats_zeroed_current_residual_fields_as_open(tmp_path) -> None:
    run_path = tmp_path / "transitional.csv"
    with run_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
                "comparison_class",
                "source_basis",
                "core_benchmark",
                "adjudicated_residual_count",
                "matched_current_residual_count",
                "adjudicated_bucket_counts",
                "unknown_current_residual_count",
                "open_current_divergence_count",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "grupi_id": "g1",
                "base_id": "transitional-open",
                "oracle_id": "o1",
                "title": "Transitional Pair",
                "n_ops": "5",
                "n_divs": "4",
                "sec_match": "0.8000",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "0",
                "matched_current_residual_count": "0",
                "adjudicated_bucket_counts": "",
                "unknown_current_residual_count": "0",
                "open_current_divergence_count": "0",
            }
        )

    payload = ee_frontier.build_frontier_payload(str(run_path), top=10)

    assert payload["open_row_count"] == 1
    assert payload["open_headline_row_count"] == 0
    assert payload["open_nonheadline_row_count"] == 1
    assert payload["legacy_unclassified_nonzero_row_count"] == 0
    assert payload["rows"][0]["base_id"] == "transitional-open"
    assert payload["rows"][0]["frontier_bucket"] == "open"
    assert payload["rows"][0]["open_current_divergence_count"] == 4


def test_ee_frontier_main_prints_open_and_adjudicated_buckets(tmp_path, capsys, monkeypatch) -> None:
    run_path = tmp_path / "demo.csv"
    with run_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
                "comparison_class",
                "source_basis",
                "core_benchmark",
                "adjudicated_residual_count",
                "matched_current_residual_count",
                "adjudicated_bucket_counts",
                "unknown_current_residual_count",
                "open_current_divergence_count",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "grupi_id": "g1",
                "base_id": "open-pair",
                "oracle_id": "o1",
                "title": "Open Pair",
                "n_ops": "10",
                "n_divs": "5",
                "sec_match": "0.9500",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "2",
                "matched_current_residual_count": "2",
                "adjudicated_bucket_counts": "source_oracle_drift=2",
                "unknown_current_residual_count": "3",
                "open_current_divergence_count": "3",
            }
        )
        writer.writerow(
            {
                "grupi_id": "g2",
                "base_id": "adjudicated-pair",
                "oracle_id": "o2",
                "title": "Adjudicated Pair",
                "n_ops": "20",
                "n_divs": "7",
                "sec_match": "0.9000",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "7",
                "matched_current_residual_count": "7",
                "adjudicated_bucket_counts": "appendix_display_pathology=1,source_oracle_drift=6",
                "unknown_current_residual_count": "0",
                "open_current_divergence_count": "0",
            }
        )

    dummy_archive = SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(ee_frontier, "open_rt_archive", lambda *args, **kwargs: dummy_archive)
    monkeypatch.setattr(ee_frontier, "fetch_rt_xml", lambda aktviide, archive=None: f"<{aktviide}>".encode("utf-8"))
    monkeypatch.setattr(ee_frontier, "extract_effective_date", lambda xml: "2025-01-01")
    monkeypatch.setattr(
        ee_frontier,
        "plan_ee_oracle_pair",
        lambda **kwargs: SimpleNamespace(
            plan=SimpleNamespace(source_basis=SimpleNamespace(value="pairwise_terviktekst_delta"))
        ),
    )

    ee_frontier.main(
        argparse.Namespace(
            label=str(run_path),
            top=20,
            include_adjudicated=False,
            json=False,
        )
    )

    out = capsys.readouterr().out
    assert "=== EE Frontier ===" in out
    assert "open rows : 1" in out
    assert "headline-eligible : 1" in out
    assert "non-headline      : 0" in out
    assert "adjudicated non-zero rows : 1" in out
    assert "drift     : 2 non-silent comparison rules" in out
    assert "classes  : lexical_institutional_drift=2, manual_exception=0" in out
    assert "rules    : politseiasutus_rename, politsei_plural_rename" in out
    assert "reporting : EE_CORE_COMMENSURABLE=2" in out
    assert "Active frontier rows:" in out
    assert "open-pair" in out
    assert "basis=pairwise_terviktekst_delta" in out
    assert "stratum=EE_CORE_COMMENSURABLE" in out
    assert "Adjudicated non-zero rows:" in out
    assert "adjudicated-pair" in out


def test_build_frontier_payload_prefers_core_commensurable_rows_over_noncore_editorial(tmp_path) -> None:
    run_path = tmp_path / "ranking.csv"
    with run_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "grupi_id",
                "base_id",
                "oracle_id",
                "title",
                "n_ops",
                "n_divs",
                "sec_match",
                "r_secs",
                "o_secs",
                "status",
                "comparison_class",
                "source_basis",
                "core_benchmark",
                "adjudicated_residual_count",
                "matched_current_residual_count",
                "adjudicated_bucket_counts",
                "unknown_current_residual_count",
                "open_current_divergence_count",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "grupi_id": "g1",
                "base_id": "editorial-noise",
                "oracle_id": "o1",
                "title": "Editorial Drift",
                "n_ops": "0",
                "n_divs": "50",
                "sec_match": "0.9500",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "same_chain_editorial_drift",
                "source_basis": "earliest_available_terviktekst",
                "core_benchmark": "0",
                "adjudicated_residual_count": "0",
                "matched_current_residual_count": "0",
                "adjudicated_bucket_counts": "",
                "unknown_current_residual_count": "0",
                "open_current_divergence_count": "50",
            }
        )
        writer.writerow(
            {
                "grupi_id": "g2",
                "base_id": "core-open",
                "oracle_id": "o2",
                "title": "Core Delta",
                "n_ops": "3",
                "n_divs": "5",
                "sec_match": "0.9000",
                "r_secs": "10",
                "o_secs": "10",
                "status": "OK",
                "comparison_class": "commensurable_delta",
                "source_basis": "pairwise_terviktekst_delta",
                "core_benchmark": "1",
                "adjudicated_residual_count": "0",
                "matched_current_residual_count": "0",
                "adjudicated_bucket_counts": "",
                "unknown_current_residual_count": "5",
                "open_current_divergence_count": "5",
            }
        )

    payload = ee_frontier.build_frontier_payload(str(run_path), top=10)

    assert payload["open_headline_row_count"] == 1
    assert payload["open_nonheadline_row_count"] == 1
    assert payload["rows"][0]["base_id"] == "core-open"
    assert payload["rows"][1]["base_id"] == "editorial-noise"
    assert payload["rows"][0]["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["rows"][1]["benchmark_reporting_stratum"] == "EE_SAME_CHAIN_EDITORIAL_DRIFT"

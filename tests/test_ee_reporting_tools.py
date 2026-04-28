from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import lawvm.estonia.fetch as ee_fetch
import lawvm.estonia.replay as ee_replay_impl
from lawvm.core.ir import LegalAddress
import lawvm.core.timeline_consistency as timeline_consistency
from lawvm.core.timeline_consistency import ConsistencyDivergence
from lawvm.estonia.residual_reporting import build_ee_residual_summary
from lawvm.estonia.residual_inventory import EEPairResidualInventory, EEResidualRecord
from lawvm.tools.ee_reporting import (
    EEBenchmarkReportingStratum,
    build_ee_benchmark_reporting_summary,
    classify_ee_benchmark_reporting_stratum,
)
from lawvm.tools import cli, ee_replay, verify_consistency


def test_build_ee_residual_summary_counts_matched_and_unknown() -> None:
    summary = build_ee_residual_summary(
        "193936",
        "13336397",
        [
            "chapter:1/section:6/subsection:2",
            "chapter:99/section:999",
        ],
    )

    assert summary is not None
    assert summary.residual_count == 7
    assert summary.bucket_counts == {
        "appendix_display_pathology": 1,
        "source_oracle_drift": 6,
    }
    assert summary.matched_current_divergence_count == 1
    assert summary.matched_current_bucket_counts == {"source_oracle_drift": 1}
    assert summary.unknown_current_divergence_count == 1
    assert summary.unknown_current_divergence_addresses == ("chapter:99/section:999",)


def test_build_ee_residual_summary_inherits_ancestor_records_from_matched_descendants(monkeypatch) -> None:
    inventory = EEPairResidualInventory(
        base_id="b",
        oracle_id="o",
        statute_title="Test",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:1/subsection:1/item:1",
                bucket="source_oracle_drift",
                evidence="descendant drift",
            ),
            EEResidualRecord(
                address="chapter:1/section:1/subsection:1/item:2",
                bucket="source_oracle_drift",
                evidence="descendant drift",
            ),
        ),
    )
    monkeypatch.setattr(
        "lawvm.estonia.residual_reporting.get_ee_residual_inventory",
        lambda base_id, oracle_id: inventory,
    )

    summary = build_ee_residual_summary(
        "b",
        "o",
        (
            "chapter:1/section:1",
            "chapter:1/section:1/subsection:1",
            "chapter:1/section:1/subsection:1/item:1",
            "chapter:1/section:1/subsection:1/item:2",
        ),
    )

    assert summary is not None
    assert summary.matched_current_divergence_count == 4
    assert summary.unknown_current_divergence_count == 0

def test_build_ee_residual_summary_inherits_mixed_ancestor_records_as_descendant_mix(monkeypatch) -> None:
    inventory = EEPairResidualInventory(
        base_id="b",
        oracle_id="o",
        statute_title="Test",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:1/subsection:1/item:1",
                bucket="source_oracle_drift",
                evidence="oracle drift",
            ),
            EEResidualRecord(
                address="chapter:1/section:1/subsection:1/item:2",
                bucket="source_pathology",
                evidence="source pathology",
            ),
        ),
    )
    monkeypatch.setattr(
        "lawvm.estonia.residual_reporting.get_ee_residual_inventory",
        lambda base_id, oracle_id: inventory,
    )

    summary = build_ee_residual_summary(
        "b",
        "o",
        (
            "chapter:1/section:1",
            "chapter:1/section:1/subsection:1/item:1",
            "chapter:1/section:1/subsection:1/item:2",
        ),
    )

    assert summary is not None
    assert summary.matched_current_divergence_count == 3
    assert summary.unknown_current_divergence_count == 0
    assert summary.record_by_address["chapter:1/section:1"].bucket == "descendant_residual_mix"
    assert "source_oracle_drift=1, source_pathology=1" in summary.record_by_address["chapter:1/section:1"].evidence


def test_build_ee_residual_summary_derives_mixed_container_record_for_maagaas_pair() -> None:
    summary = build_ee_residual_summary(
        "109082022022",
        "108102024012",
        (
            "chapter:3",
            "chapter:3/section:26_7",
            "chapter:3/section:26_7/subsection:1",
            "chapter:3/section:26_7/subsection:2",
            "chapter:3/section:26_7/subsection:2/item:6",
        ),
    )

    assert summary is not None
    assert summary.unknown_current_divergence_count == 0
    assert summary.record_by_address["chapter:3"].bucket == "descendant_residual_mix"
    assert summary.record_by_address["chapter:3/section:26_7"].bucket == "descendant_residual_mix"
    assert summary.record_by_address["chapter:3/section:26_7/subsection:2"].bucket == "source_oracle_drift"
    assert summary.record_by_address["chapter:3/section:26_7/subsection:1"].bucket == "source_pathology"


def test_cli_parser_accepts_verify_consistency_json() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "verify-consistency",
            "--jurisdiction",
            "ee",
            "--base",
            "193936",
            "--consolidated",
            "13336397",
            "--json",
        ]
    )

    assert args.command == "verify-consistency"
    assert args.base == "193936"
    assert args.consolidated == "13336397"
    assert args.json is True


def test_build_ee_consistency_payload_attaches_residual_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        ee_replay_impl,
        "replay_ee_to_pit",
        lambda **kwargs: SimpleNamespace(
            base_id="193936",
            as_of="2011-03-17",
            base_title="Liiklusseadus",
            source_basis="pairwise_terviktekst_delta",
            error="",
            grupi_id="grp",
            oracle_id="13336397",
            comparison_class="commensurable_delta",
            amendments_total=["a1"],
            amendments_applied=["a1"],
            amendments_skipped=[],
            amendments_failed=[],
            n_ops=217,
            oracle=SimpleNamespace(title="Liiklusseadus"),
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
            n_mismatch=1,
            n_ops_missing=0,
            n_con_missing=0,
            timelines={"one": object(), "two": object()},
        ),
    )
    monkeypatch.setattr(timeline_consistency, "ingest_consolidated", lambda con, as_of: {"one": object()})

    payload = verify_consistency._build_ee_consistency_payload(
        Namespace(
            base="193936",
            consolidated="13336397",
            cache_dir=None,
            as_of="2011-03-17",
        )
    )

    assert payload["divergence_count"] == 1
    assert payload["source_basis"] == "pairwise_terviktekst_delta"
    assert payload["comparison_class"] == "commensurable_delta"
    assert payload["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["benchmark_reporting_headline_eligible"] is True
    assert payload["comparison_policy"]["non_silent_rule_count"] == 2
    assert payload["comparison_policy"]["non_silent_rule_names"] == [
        "politseiasutus_rename",
        "politsei_plural_rename",
    ]
    assert payload["ops_count"] == 217
    assert payload["residual_inventory"] is not None
    assert payload["residual_inventory"]["residual_count"] == 7
    assert payload["residual_inventory"]["matched_current_divergence_count"] == 1
    assert payload["residual_inventory"]["unknown_current_divergence_count"] == 0
    assert payload["divergences"][0]["residual_bucket"] == "source_oracle_drift"
    assert "lasteaed-algkoolid" in (payload["divergences"][0]["residual_evidence"] or "")


def test_build_ee_consistency_payload_uses_oracle_effective_date_when_as_of_missing(
    monkeypatch,
) -> None:
    seen: dict[str, str] = {}

    monkeypatch.setattr(ee_fetch, "open_rt_archive", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ee_fetch, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_fetch, "extract_effective_date", lambda xml_bytes: "2025-12-30")

    def _fake_replay(**kwargs):
        seen["as_of"] = kwargs["as_of"]
        return SimpleNamespace(
            base_id="130122025021",
            as_of=kwargs["as_of"],
            base_title="Käibemaksuseadus",
            source_basis="pairwise_terviktekst_delta",
            error="",
            grupi_id="grp",
            oracle_id="130122025022",
            comparison_class="commensurable_delta",
            amendments_total=[],
            amendments_applied=[],
            amendments_skipped=[],
            amendments_failed=[],
            n_ops=0,
            oracle=SimpleNamespace(title="Käibemaksuseadus"),
            divergences=[],
            n_mismatch=0,
            n_ops_missing=0,
            n_con_missing=0,
            timelines={},
            adjudications=[],
        )

    monkeypatch.setattr(
        ee_replay_impl,
        "replay_ee_to_pit",
        _fake_replay,
    )
    monkeypatch.setattr(timeline_consistency, "ingest_consolidated", lambda con, as_of: {})

    payload = verify_consistency._build_ee_consistency_payload(
        Namespace(
            base="130122025021",
            consolidated="130122025022",
            cache_dir=None,
            as_of="0000-00-00",
        )
    )

    assert seen == {"as_of": "2025-12-30"}
    assert payload["as_of"] == "2025-12-30"
    assert payload["source_basis"] == "pairwise_terviktekst_delta"
    assert payload["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["benchmark_reporting_headline_eligible"] is True
    assert payload["comparison_policy"]["non_silent_rule_classes"] == [
        "lexical_institutional_drift",
        "manual_exception",
    ]


def test_print_ee_consistency_payload_includes_residual_inventory_summary(capsys) -> None:
    payload = {
        "base_id": "193936",
        "consolidated_id": "13336397",
        "as_of": "2011-03-17",
        "source_basis": "pairwise_terviktekst_delta",
        "benchmark_reporting_stratum": "EE_CORE_COMMENSURABLE",
        "benchmark_reporting_headline_eligible": True,
        "base_title": "Liiklusseadus",
        "consolidated_title": "Liiklusseadus",
        "comparison_policy": {
            "non_silent_rule_class_count": 2,
            "non_silent_rule_classes": [
                "lexical_institutional_drift",
                "manual_exception",
            ],
            "non_silent_rule_count": 2,
            "non_silent_rule_names": [
                "politseiasutus_rename",
                "politsei_plural_rename",
            ],
            "non_silent_rule_counts_by_class": {
                "lexical_institutional_drift": 2,
                "manual_exception": 0,
            },
        },
        "ops_provisions": 10,
        "consolidated_provisions": 10,
        "divergence_count": 1,
        "mismatch_count": 1,
        "ops_missing_count": 0,
        "consolidated_missing_count": 0,
        "residual_inventory": {
            "residual_count": 7,
            "bucket_counts": {
                "appendix_display_pathology": 1,
                "source_oracle_drift": 6,
            },
            "matched_current_bucket_counts": {"source_oracle_drift": 1},
            "unknown_current_divergence_count": 0,
        },
        "divergences": [
            {
                "address": "chapter:1/section:6/subsection:2",
                "divergence_type": "MISMATCH",
                "ops_text": "replay text",
                "consolidated_text": "oracle text",
                "residual_bucket": "source_oracle_drift",
                "residual_evidence": "evidence",
            }
        ],
    }

    verify_consistency._print_ee_consistency_payload(payload, verbose=False)

    out = capsys.readouterr().out
    assert "basis      : pairwise_terviktekst_delta" in out
    assert "drift      : 2 non-silent comparison rules" in out
    assert "classes   : lexical_institutional_drift=2, manual_exception=0" in out
    assert "rules     : politseiasutus_rename, politsei_plural_rename" in out
    assert "reporting  : EE_CORE_COMMENSURABLE (headline=yes)" in out
    assert "adjudicated residuals: 7 known for this pair" in out
    assert "buckets           : appendix_display_pathology=1, source_oracle_drift=6" in out
    assert "matched current   : source_oracle_drift=1" in out
    assert "chapter:1/section:6/subsection:2  [source_oracle_drift]" in out


def test_ee_reporting_stratum_classifier_maps_expected_cases() -> None:
    assert (
        classify_ee_benchmark_reporting_stratum(
            "pairwise_terviktekst_delta",
            "commensurable_delta",
        )
        == EEBenchmarkReportingStratum.CORE_COMMENSURABLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum("base_is_oracle", "base_is_oracle")
        == EEBenchmarkReportingStratum.BASE_IS_ORACLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum(
            "noncommensurable",
            "forward_looking_oracle",
        )
        == EEBenchmarkReportingStratum.FORWARD_LOOKING_ORACLE
    )
    assert (
        classify_ee_benchmark_reporting_stratum(
            "earliest_available_terviktekst",
            "same_chain_editorial_drift",
        )
        == EEBenchmarkReportingStratum.SAME_CHAIN_EDITORIAL_DRIFT
    )
    assert (
        classify_ee_benchmark_reporting_stratum("noncommensurable", "no_oracle")
        == EEBenchmarkReportingStratum.NONCORE_SOURCE_GAP
    )
    assert build_ee_benchmark_reporting_summary(
        "pairwise_terviktekst_delta",
        "commensurable_delta",
    ) == {
        "benchmark_reporting_stratum": "EE_CORE_COMMENSURABLE",
        "benchmark_reporting_headline_eligible": True,
    }


def test_ee_replay_main_prints_adjudicated_residual_summary(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        ee_replay_impl,
        "replay_ee_to_pit",
        lambda **kwargs: SimpleNamespace(
            base_id="193936",
            as_of="2011-03-17",
            base_title="Liiklusseadus",
            error="",
            grupi_id="grp",
            oracle_id="13336397",
            comparison_class="commensurable_delta",
            amendments_total=["a1"],
            amendments_applied=["a1"],
            amendments_skipped=[],
            amendments_failed=[],
            n_ops=217,
            oracle=object(),
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
            n_mismatch=1,
            n_ops_missing=0,
            n_con_missing=0,
            replayed=None,
        ),
    )

    ee_replay.main(
        Namespace(
            base_id="193936",
            as_of="2011-03-17",
            archive=None,
            verbose=False,
            show_text=False,
        )
    )

    out = capsys.readouterr().out
    assert "adjudicated residuals : 7 known" in out
    assert "matched current    : source_oracle_drift=1" in out
    assert "chapter:1/section:6/subsection:2 [source_oracle_drift]" not in out
    assert "[source_oracle_drift]" in out

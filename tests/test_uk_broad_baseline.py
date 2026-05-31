from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import scripts.uk_broad_baseline as uk_broad_baseline


def test_score_one_reports_too_small_current_as_source_frontier(monkeypatch) -> None:
    class FakeFarchive:
        def __init__(self, _path):
            pass

        def get(self, locator: str) -> bytes | None:
            if locator.endswith("/enacted/data.xml"):
                return b"""<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
    NumberOfProvisions="1">
  <Body><P1 id="section-1"><Pnumber>1</Pnumber><P1para>Text.</P1para></P1></Body>
</Legislation>"""
            if locator.endswith("/data.xml"):
                return b"HTTP 300 Multiple Choices"
            return None

        def close(self) -> None:
            pass

    monkeypatch.setitem(
        sys.modules,
        "farchive",
        SimpleNamespace(Farchive=FakeFarchive),
    )

    row = uk_broad_baseline.score_one("ukpga/1945/9")

    assert row["score_status"] == "source_frontier"
    assert row["source_frontier_reason"] == "oracle_too_small"
    assert row["base_source_status"] == "available"
    assert row["oracle_source_status"] == "too_small"
    assert "error" not in row


def test_summarize_results_counts_frontiers_and_zero_oracle_retention() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "ukpga/1938/22",
                "score_status": "scored",
                "aligned": 0.0,
                "n_replay": 420,
                "n_oracle": 0,
                "n_zero_oracle_retention_eids": 420,
            },
            {
                "statute_id": "ukpga/1992/41",
                "score_status": "scored",
                "aligned": 64.0,
                "aligned_excluding_grounding_collateral": 98.7,
                "n_grounding_collateral": 169,
                "n_replay": 469,
                "n_oracle": 304,
            },
            {
                "statute_id": "ukpga/1986/61",
                "score_status": "scored",
                "aligned": 50.9,
                "aligned_excluding_grounding_collateral": 50.9,
                "n_grounding_collateral": 100,
                "n_replay": 389,
                "n_oracle": 568,
            },
            {
                "statute_id": "ukpga/1961/60",
                "score_status": "scored",
                "aligned": 22.7,
                "aligned_excluding_grounding_collateral": 22.7,
                "unaligned": 22.7,
                "n_grounding_collateral": 0,
                "n_replay": 5,
                "n_oracle": 22,
                "base_source_status": "metadata_only",
            },
            {
                "statute_id": "eur/2019/1841",
                "score_status": "scored",
                "aligned": 61.8,
                "aligned_excluding_grounding_collateral": 61.8,
                "unaligned": 100.0,
                "n_grounding_collateral": 0,
                "n_replay": 34,
                "n_oracle": 21,
            },
            {
                "statute_id": "uksi/2000/1043",
                "score_status": "scored",
                "aligned": 77.7,
                "aligned_excluding_grounding_collateral": 77.7,
                "unaligned": 75.3,
                "n_grounding_collateral": 1,
                "n_replay": 168,
                "n_oracle": 215,
                "n_ops": 0,
            },
            {
                "statute_id": "ukpga/1945/9",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1945/10",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_too_small",
            },
            {
                "statute_id": "ukpga/1946/1",
                "error": "RuntimeError: boom",
            },
        ]
    )

    assert len(summary["scored"]) == 6
    assert len(summary["errored"]) == 1
    assert len(summary["source_frontier"]) == 2
    assert summary["source_frontier_reasons"] == {"base_too_small": 2}
    assert summary["zero_oracle_retention_count"] == 1
    assert summary["zero_oracle_retention_eids"] == 420
    assert summary["triage_buckets"] == {
        "base_metadata_only_frontier": 1,
        "error": 1,
        "high_fidelity_after_grounding": 1,
        "no_compiled_ops_frontier": 1,
        "residual_after_grounding": 1,
        "source_frontier:base_too_small": 2,
        "structural_match_eid_scheme_residual": 1,
        "zero_oracle_retention": 1,
    }


def test_summarize_results_counts_grounding_dominated_residuals() -> None:
    summary = uk_broad_baseline.summarize_results(
        [
            {
                "statute_id": "eur/2019/2018",
                "score_status": "scored",
                "aligned": 17.4,
                "aligned_excluding_grounding_collateral": 41.0,
                "n_grounding_collateral": 165,
                "n_replay": 287,
                "n_oracle": 62,
            },
        ]
    )

    assert summary["triage_buckets"] == {"grounding_dominated_residual": 1}


def test_triage_bucket_for_row_is_added_to_one_row_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        uk_broad_baseline,
        "score_one",
        lambda _statute_id: {
            "statute_id": "ukpga/1961/60",
            "score_status": "scored",
            "aligned": 22.7,
            "aligned_excluding_grounding_collateral": 22.7,
            "unaligned": 22.7,
            "n_replay": 5,
            "n_oracle": 22,
            "base_source_status": "metadata_only",
        },
    )

    assert uk_broad_baseline.main(["--one", "ukpga/1961/60"]) == 0
    row = json.loads(capsys.readouterr().out)

    assert row["triage_bucket"] == "base_metadata_only_frontier"

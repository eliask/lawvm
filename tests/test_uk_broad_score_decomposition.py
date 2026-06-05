from __future__ import annotations

import json

from scripts import uk_broad_score_decomposition as decomposition


def _write_report(tmp_path, rows):
    path = tmp_path / "uk_broad.report.json"
    path.write_text(json.dumps({"rows": rows}))
    return path


def test_decomposition_ranks_bucket_score_drag(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "statute_id": "ukpga/2000/1",
                "score_status": "scored",
                "triage_bucket": "high_fidelity_after_grounding",
                "aligned": 100.0,
                "n_replay": 10,
                "n_oracle": 10,
                "n_common": 10,
            },
            {
                "statute_id": "ukpga/2000/2",
                "score_status": "scored",
                "triage_bucket": "zero_oracle_retention",
                "aligned": 0.0,
                "n_replay": 8,
                "n_oracle": 0,
                "n_common": 0,
                "n_only_in_replayed": 8,
            },
            {
                "statute_id": "ukpga/2000/3",
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 75.0,
                "n_replay": 30,
                "n_oracle": 40,
                "n_common": 30,
                "n_only_in_oracle": 10,
                "n_effects": 4,
                "n_manual_frontier_records": 4,
            },
            {
                "statute_id": "ukpga/1850/1",
                "score_status": "source_frontier",
                "triage_bucket": "source_frontier:base_metadata_only",
            },
        ],
    )

    payload = decomposition.load_decomposition(report)

    assert payload["report_kind"] == "uk_broad_score_decomposition.v1"
    assert payload["summary"]["scored_count"] == 3
    assert payload["summary"]["source_frontier_count"] == 1
    assert payload["summary"]["scored_mean_aligned"] == 175.0 / 3
    assert payload["summary"]["reference_score"] == 100.0
    assert payload["summary"]["loss_points_to_reference"] == 100.0 - (175.0 / 3)
    assert [row["triage_bucket"] for row in payload["buckets"]] == [
        "zero_oracle_retention",
        "manual_compile_frontier_residual",
        "high_fidelity_after_grounding",
    ]
    assert payload["buckets"][0]["loss_points_vs_reference"] == 100.0 / 3
    assert payload["buckets"][1]["n_only_in_oracle"] == 10
    assert "score_bucket_as_replay_authorization" in payload["forbidden_shortcuts"]


def test_explicit_reference_score_overrides_reference_bucket(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 80.0,
            },
            {
                "score_status": "scored",
                "triage_bucket": "manual_compile_frontier_residual",
                "aligned": 100.0,
            },
        ],
    )

    payload = decomposition.load_decomposition(report, reference_score=99.0)

    assert payload["summary"]["reference_score"] == 99.0
    assert payload["summary"]["reference_bucket"] == "high_fidelity_after_grounding"
    assert payload["buckets"][0]["mean_aligned"] == 90.0
    assert payload["buckets"][0]["loss_points_vs_reference"] == 9.0


def test_text_and_tsv_emitters_are_stable(tmp_path) -> None:
    report = _write_report(
        tmp_path,
        [
            {
                "score_status": "scored",
                "triage_bucket": "high_fidelity_after_grounding",
                "aligned": "100.0",
            },
            {
                "score_status": "scored",
                "triage_bucket": "zero_oracle_retention",
                "aligned": "0.0",
            },
        ],
    )
    payload = decomposition.load_decomposition(report)

    text = decomposition._emit_text(payload)
    tsv = decomposition._emit_tsv(payload)

    assert "scored=2 source_frontier=0" in text
    assert "zero_oracle_retention: n=1 mean=0.00 loss=50.00" in text
    assert tsv.splitlines()[0].startswith("triage_bucket\trow_count\tmean_aligned")
    assert "zero_oracle_retention\t1\t0.0000\t50.0000" in tsv

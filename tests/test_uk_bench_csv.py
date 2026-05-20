from __future__ import annotations

import csv
import concurrent.futures
import hashlib
import json
from argparse import Namespace
from pathlib import Path
from typing import cast

from farchive import Farchive
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.tools import uk_bench
from lawvm.tools.uk_bench import _BenchResult, _BenchScoreWitnessRow, _primary_score_mode


def test_uk_bench_commencement_score_requires_commenced_eid_evidence() -> None:
    assert uk_bench._score_commenced_eids(set(), {"section-1"}) == -1.0
    assert uk_bench._score_commenced_eids({"section-1"}, {"section-1", "section-2"}) == 0.5


def test_uk_bench_commencement_oracle_uses_same_temporal_lens() -> None:
    assert uk_bench._commenced_oracle_eids(
        {"section-1", "section-2", "section-3"},
        {"section-1", "section-3", "section-4"},
    ) == {"section-1", "section-3"}
    assert uk_bench._commenced_oracle_eids({"section-1"}, set()) == set()
    assert uk_bench._score_witness_labels("commencement") == (
        "commenced_enacted",
        "commenced_oracle",
    )
    assert uk_bench._score_witness_labels("replay_commencement") == (
        "commenced_replay",
        "commenced_oracle",
    )


def test_uk_bench_primary_score_prefers_commencement_when_active() -> None:
    raw_low_commenced_high = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=5,
        score=0.5,
        status="OK",
        commencement_score=0.9,
        replay_score=0.4,
        replay_commencement_score=0.95,
    )
    raw_high_no_commencement = _BenchResult(
        statute_id="ukpga/2000/2",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=8,
        score=0.8,
        status="OK",
        commencement_score=-1.0,
        replay_score=0.7,
        replay_commencement_score=-1.0,
    )

    assert uk_bench._bench_primary_score(
        raw_low_commenced_high,
        has_commencement=True,
    ) == 0.9
    assert uk_bench._bench_primary_replay_score(
        raw_low_commenced_high,
        has_commencement=True,
    ) == 0.95
    average = uk_bench._average_primary_ok_score(
        [raw_low_commenced_high, raw_high_no_commencement],
        has_commencement=True,
    )
    assert round(average, 6) == 0.85


def test_uk_bench_records_successful_commencement_filter_observations(monkeypatch) -> None:
    from lawvm.uk_legislation import effects as effects_mod
    from lawvm.uk_legislation.effects import UKEffectRecord

    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    statute = IRStatute(
        statute_id="asp/test/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, attrs={"eId": "section-1"}),),
        ),
    )
    effect = UKEffectRecord(
        effect_id="undated-appointed-day",
        effect_type="Appointed Day(s)",
        applied=True,
        requires_applied=False,
        modified="",
        affected_uri="/id/asp/test/1",
        affected_class="ScottishAct",
        affected_year="2025",
        affected_number="1",
        affected_provisions="specified provision(s)",
        affecting_uri="/id/ssi/2025/1",
        affecting_class="ScottishStatutoryInstrument",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="art. 2",
        affecting_title="Test Appointed Day Order",
        in_force_dates=[],
    )

    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: statute)
    monkeypatch.setattr(
        uk_bench,
        "extract_eid_map_bytes",
        lambda _data: {"eid_map": {"section-1": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (1, 0, {}, 0, {}, ()))
    monkeypatch.setattr(
        effects_mod,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )

    result = uk_bench._score_statute(
        {
            "statute_id": "asp/test/1",
            "type": "asp",
            "year": 2025,
            "n_effects": 1,
            "n_effect_feed_pages": 1,
            "enacted_url": "https://www.legislation.gov.uk/asp/test/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/asp/test/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
        do_commencement=True,
    )

    assert result.commencement_score == -1.0
    assert result.effect_feed_observation_count == 1
    assert result.effect_feed_observation_rule_counts == {
        "uk_commencement_undated_effects_block_self_commencement": 1,
    }


def test_uk_bench_score_witness_helper_is_bounded_and_deterministic() -> None:
    rows = uk_bench._build_eid_score_witness_rows(
        comparison_scope="raw",
        left_side="only_in_enacted",
        left_eids={"section-3", "section-1", "section-2", "section-4"},
        right_eids={"section-2", "section-5"},
        sample_limit=2,
    )

    assert [(row.side, row.eid, row.rank) for row in rows] == [
        ("only_in_enacted", "section-1", 1),
        ("only_in_enacted", "section-3", 2),
        ("only_in_oracle", "section-5", 1),
    ]
    assert rows[0].category_total == 3
    assert rows[0].sample_limit == 2
    assert rows[0].truncated is True
    assert rows[0].left_count == 4
    assert rows[0].right_count == 2
    assert rows[0].common_count == 1
    assert rows[0].score_value == 0.25


def test_uk_bench_effect_feed_rows_without_blocking_default_to_rejections(monkeypatch) -> None:
    def fake_load_effects(statute_id, archive, *, parse_rejections_out=None):
        del archive
        assert statute_id == "ukpga/2000/1"
        assert parse_rejections_out is not None
        parse_rejections_out.extend(
            (
                {"rule_id": "legacy_feed_parse_row"},
                {"rule_id": "feed_observation", "blocking": False},
                {"rule_id": "record_observation", "strict_disposition": "record"},
            )
        )
        return [object()]

    monkeypatch.setattr(
        "lawvm.uk_legislation.effects.load_effects_for_statute_from_archive",
        fake_load_effects,
    )

    (
        effect_rows,
        rejection_count,
        rejection_rules,
        observation_count,
        observation_rules,
        observations,
    ) = uk_bench._load_effect_row_counts("ukpga/2000/1", cast(Farchive, object()))

    assert effect_rows == 1
    assert rejection_count == 1
    assert rejection_rules == {"legacy_feed_parse_row": 1}
    assert observation_count == 3
    assert observation_rules == {
        "feed_observation": 1,
        "legacy_feed_parse_row": 1,
        "record_observation": 1,
    }
    assert observations == (
        {"rule_id": "legacy_feed_parse_row"},
        {"rule_id": "feed_observation", "blocking": False},
        {"rule_id": "record_observation", "strict_disposition": "record"},
    )


def test_uk_bench_source_parse_rejections_are_blocking_subset() -> None:
    rows = [
        {"rule_id": "uk_oracle_xml_parse_rejected", "blocking": True},
        {
            "rule_id": "uk_oracle_xml_parse_observed",
            "strict_disposition": "record",
        },
        {"rule_id": "uk_oracle_xml_parse_note", "blocking": False},
    ]

    assert uk_bench._blocking_source_parse_rows(rows) == (rows[0],)


def test_uk_bench_save_load_round_trips_commencement_scores(monkeypatch, tmp_path, capsys) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=2,
        n_effect_feed_pages=2,
        n_effect_rows=7,
        effect_feed_rejection_count=1,
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_count=3,
        effect_feed_observation_rule_counts={
            "uk_effect_feed_pages_absent_recorded": 2,
            "uk_effect_feed_xml_parse_rejected": 1,
        },
        effect_feed_observations=(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "family": "source_pathology",
                "phase": "parse",
                "blocking": True,
            },
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "family": "source_pathology",
                "phase": "acquisition",
                "strict_disposition": "record",
            },
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "family": "source_pathology",
                "phase": "acquisition",
                "blocking": False,
            },
        ),
        effect_feed_count_error="ValueError: bad effect feed",
        bench_exception_count=1,
        bench_exception_rule_counts={"uk_bench_unclassified_exception": 1},
        bench_exception_observations=(
            {
                "rule_id": "uk_bench_unclassified_exception",
                "family": "benchmark_execution",
                "phase": "benchmark",
                "statute_id": "ukpga/2000/1",
                "blocking": True,
            },
        ),
        source_parse_rejection_count=1,
        source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observation_count=1,
        source_parse_observation_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observations=(
            {
                "rule_id": "uk_oracle_xml_parse_rejected",
                "family": "source_pathology",
                "phase": "parse",
                "blocking": True,
            },
        ),
        effect_diagnostics=(
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "family": "source_pathology",
                "phase": "acquisition",
                "blocking": True,
            },
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "source_pathology": "missing_extracted_source",
                "blocking": False,
            },
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "family": "manual_compile_frontier",
                "phase": "lowering",
                "manual_compile_status": "unclassified_frontier",
                "manual_compile_rule_id": "uk_manual_frontier_unclassified",
                "blocking": False,
            },
        ),
        manual_compile_status_counts={"unclassified_frontier": 1},
        manual_compile_rule_counts={"uk_manual_frontier_unclassified": 1},
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
        oracle_alignment_changed_count=4,
        oracle_alignment_oracle_assigned_count=3,
        oracle_alignment_local_fallback_count=1,
        oracle_alignment_transparent_wrapper_cleared_count=2,
        oracle_alignment_before_node_count=10,
        oracle_alignment_after_node_count=11,
        oracle_alignment_node_count_mismatch=True,
        oracle_alignment_match_method_counts={"flat": 3, "local_fallback": 1},
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_source_purity_lane="source_backed_effects_assisted",
        uk_source_semantics_clean=True,
        uk_source_first_candidate=False,
        uk_source_first_candidate_reasons=("applicability_selection_not_feed_applied",),
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        uk_authority_observations=(
            {
                "rule_id": "uk_authority_source_text_only_missing",
                "family": "authority_filter",
                "phase": "lowering",
                "blocking": True,
            },
        ),
        lowering_observation_count=5,
        lowering_observation_rule_counts={
            "uk_effect_payload_missing": 2,
            "uk_effect_lowering_no_ops_rejected": 3,
        },
        lowering_rejection_count=5,
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 2, "uk_effect_lowering_no_ops_rejected": 3},
        blocking_lowering_rejection_count=3,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 3},
        lowering_rejections=(
            {
                "rule_id": "uk_effect_payload_missing",
                "family": "lowering_filter",
                "phase": "lowering",
                "blocking": True,
            },
        ),
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=4,
        score=0.4,
        status="OK",
        n_replayed_eids=11,
        n_replay_common=8,
        replay_score=0.8,
        n_ops=3,
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={"uk_replay_target_not_found": 2},
        replay_adjudications=(
            {
                "kind": "uk_replay_target_not_found",
                "message": "Target missing",
                "source_statute": "ukpga/2000/1",
                "op_id": "op-1",
                "detail": {"target": "section:1"},
            },
        ),
        uk_residual_claim_tier="PROVED_REPLAY_BUG",
        uk_residual_claim_kind="uk_replay_target_not_found",
        uk_residual_claim_comparison_class="commensurable",
        uk_residual_claim_core_comparison=True,
        uk_residual_only_in_replayed_count=2,
        uk_residual_only_in_oracle_count=3,
        uk_residual_section_claim_count=1,
        uk_residual_section_claim_emitted=True,
        text_score=0.55,
        n_text_compared=4,
        replay_text_score=0.65,
        commencement_score=0.7,
        n_commenced_eids=5,
        replay_commencement_score=0.9,
        comparison_class="commensurable",
        core_benchmark=False,
        score_witness_rows=(
            _BenchScoreWitnessRow(
                comparison_scope="raw",
                side="only_in_enacted",
                eid="section-1",
                rank=1,
                category_total=2,
                sample_limit=10,
                truncated=False,
                left_count=10,
                right_count=12,
                common_count=4,
                score_value=0.4,
            ),
        ),
    )

    uk_bench._save_results([result], "demo")
    saved_out = capsys.readouterr().out
    loaded = uk_bench._load_run("demo")
    assert "Score witnesses saved:" in saved_out
    assert "demo.score_witnesses.csv rows=1" in saved_out
    assert "Bench diagnostics saved:" in saved_out
    assert "demo.diagnostics.jsonl rows=11" in saved_out

    with open(bench_dir / "demo.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["score"] == "0.7000"
    assert rows[0]["raw_score"] == "0.4000"
    assert rows[0]["n_effect_feed_pages"] == "2"
    assert rows[0]["n_effect_rows"] == "7"
    assert rows[0]["effect_feed_rejection_count"] == "1"
    assert json.loads(rows[0]["effect_feed_rejection_rule_counts"]) == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert rows[0]["effect_feed_observation_count"] == "3"
    assert json.loads(rows[0]["effect_feed_observation_rule_counts"]) == {
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert rows[0]["effect_feed_count_error"] == "ValueError: bad effect feed"
    assert rows[0]["bench_exception_count"] == "1"
    assert json.loads(rows[0]["bench_exception_rule_counts"]) == {
        "uk_bench_unclassified_exception": 1,
    }
    assert json.loads(rows[0]["bench_exception_observations"]) == [
        {
            "rule_id": "uk_bench_unclassified_exception",
            "family": "benchmark_execution",
            "phase": "benchmark",
            "statute_id": "ukpga/2000/1",
            "blocking": True,
        },
    ]
    assert rows[0]["source_parse_rejection_count"] == "1"
    assert json.loads(rows[0]["source_parse_rejection_rule_counts"]) == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert rows[0]["source_parse_observation_count"] == "1"
    assert json.loads(rows[0]["source_parse_observation_rule_counts"]) == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert rows[0]["enacted_source_status"] == "available"
    assert rows[0]["oracle_source_status"] == "too_small"
    assert rows[0]["enacted_source_size"] == "456"
    assert rows[0]["oracle_source_size"] == "7"
    assert rows[0]["enacted_source_sha256"] == "enacted-sha"
    assert rows[0]["oracle_source_sha256"] == "oracle-sha"
    assert rows[0]["enacted_source_url"] == "https://example.test/ukpga/2000/1/enacted/data.xml"
    assert rows[0]["oracle_source_url"] == "https://example.test/ukpga/2000/1/data.xml"
    assert rows[0]["oracle_alignment_changed_count"] == "4"
    assert rows[0]["oracle_alignment_oracle_assigned_count"] == "3"
    assert rows[0]["oracle_alignment_local_fallback_count"] == "1"
    assert rows[0]["oracle_alignment_transparent_wrapper_cleared_count"] == "2"
    assert rows[0]["oracle_alignment_before_node_count"] == "10"
    assert rows[0]["oracle_alignment_after_node_count"] == "11"
    assert rows[0]["oracle_alignment_node_count_mismatch"] == "1"
    assert json.loads(rows[0]["oracle_alignment_match_method_counts"]) == {
        "flat": 3,
        "local_fallback": 1,
    }
    assert rows[0]["uk_metadata_backfill_enabled"] == "0"
    assert rows[0]["uk_oracle_alignment_enabled"] == "0"
    assert rows[0]["uk_metadata_only_effects_enabled"] == "0"
    assert rows[0]["uk_applicability_mode"] == "effective_date_only"
    assert rows[0]["uk_authority_mode"] == "source_text_only"
    assert rows[0]["uk_source_purity_lane"] == "source_backed_effects_assisted"
    assert rows[0]["uk_source_semantics_clean"] == "1"
    assert rows[0]["uk_source_first_candidate"] == "0"
    assert json.loads(rows[0]["uk_source_first_candidate_reasons"]) == [
        "applicability_selection_not_feed_applied",
    ]
    assert rows[0]["uk_authority_rejection_count"] == "2"
    assert json.loads(rows[0]["uk_authority_rejection_rule_counts"]) == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert rows[0]["lowering_observation_count"] == "5"
    assert json.loads(rows[0]["lowering_observation_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 3,
        "uk_effect_payload_missing": 2,
    }
    assert rows[0]["lowering_rejection_count"] == "5"
    assert json.loads(rows[0]["lowering_rejection_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 3,
        "uk_effect_payload_missing": 2,
    }
    assert rows[0]["blocking_lowering_rejection_count"] == "3"
    assert json.loads(rows[0]["blocking_lowering_rejection_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 3,
    }
    assert rows[0]["replay_adjudication_count"] == "2"
    assert json.loads(rows[0]["replay_adjudication_kind_counts"]) == {
        "uk_replay_target_not_found": 2,
    }
    assert rows[0]["uk_residual_claim_tier"] == "PROVED_REPLAY_BUG"
    assert rows[0]["uk_residual_claim_kind"] == "uk_replay_target_not_found"
    assert rows[0]["uk_residual_claim_comparison_class"] == "commensurable"
    assert rows[0]["uk_residual_claim_core_comparison"] == "1"
    assert rows[0]["uk_residual_only_in_replayed_count"] == "2"
    assert rows[0]["uk_residual_only_in_oracle_count"] == "3"
    assert rows[0]["uk_residual_section_claim_count"] == "1"
    assert rows[0]["uk_residual_section_claim_emitted"] == "1"
    assert rows[0]["replay_commencement_score"] == "0.9000"
    assert json.loads(rows[0]["manual_compile_status_counts"]) == {
        "unclassified_frontier": 1,
    }
    assert json.loads(rows[0]["manual_compile_rule_counts"]) == {
        "uk_manual_frontier_unclassified": 1,
    }
    with open(bench_dir / "demo.score_witnesses.csv", newline="") as handle:
        witness_rows = list(csv.DictReader(handle))
    assert witness_rows == [
        {
            "schema": "uk_bench_score_witness.v1",
            "label": "demo",
            "statute_id": "ukpga/2000/1",
            "comparison_scope": "raw",
            "score_formula": "common/max(left,right)",
            "left_label": "enacted",
            "right_label": "oracle",
            "side": "only_in_enacted",
            "eid": "section-1",
            "rank": "1",
            "category_total": "2",
            "sample_limit": "10",
            "truncated": "0",
            "left_count": "10",
            "right_count": "12",
            "common_count": "4",
            "score_value": "0.4000",
            "comparison_class": "commensurable",
            "core_benchmark": "0",
            "enacted_source_status": "available",
            "oracle_source_status": "too_small",
            "enacted_source_size": "456",
            "oracle_source_size": "7",
            "enacted_source_sha256": "enacted-sha",
            "oracle_source_sha256": "oracle-sha",
            "enacted_source_url": "https://example.test/ukpga/2000/1/enacted/data.xml",
            "oracle_source_url": "https://example.test/ukpga/2000/1/data.xml",
            "uk_metadata_backfill_enabled": "0",
            "uk_oracle_alignment_enabled": "0",
            "uk_metadata_only_effects_enabled": "0",
            "uk_applicability_mode": "effective_date_only",
            "uk_authority_mode": "source_text_only",
        }
    ]
    with open(bench_dir / "demo.diagnostics.jsonl", encoding="utf-8") as handle:
        diagnostic_rows = [json.loads(line) for line in handle]
    replay_diagnostic = [
        row for row in diagnostic_rows if row["diagnostic_lane"] == "replay_adjudication"
    ][0]
    assert replay_diagnostic["replay_adjudication_bucket"] == "replay_bug"
    assert [
        (
            row["schema"],
            row["label"],
            row["statute_id"],
            row["diagnostic_lane"],
            row["index"],
            row["rule_id"],
            row["blocking"],
        )
        for row in diagnostic_rows
    ] == [
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "source_parse",
            0,
            "uk_oracle_xml_parse_rejected",
            True,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "effect_feed",
            0,
            "uk_effect_feed_xml_parse_rejected",
            True,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "effect_feed",
            1,
            "uk_effect_feed_pages_absent_recorded",
            False,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "effect_feed",
            2,
            "uk_effect_feed_pages_absent_recorded",
            False,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "source_acquisition",
            0,
            "uk_affecting_act_xml_missing_rejected",
            True,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "effect_source_pathology",
            0,
            "uk_effect_source_pathology_classified",
            False,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "manual_compile_frontier",
            0,
            "uk_manual_compile_frontier_classified",
            False,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "authority",
            0,
            "uk_authority_source_text_only_missing",
            True,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "lowering",
            0,
            "uk_effect_payload_missing",
            True,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "replay_adjudication",
            0,
            "uk_replay_target_not_found",
            False,
        ),
        (
            "uk_bench_diagnostic.v1",
            "demo",
            "ukpga/2000/1",
            "bench_exception",
            0,
            "uk_bench_unclassified_exception",
            True,
        ),
    ]
    assert diagnostic_rows[0]["record"] == {
        "rule_id": "uk_oracle_xml_parse_rejected",
        "family": "source_pathology",
        "phase": "parse",
        "blocking": True,
    }

    assert len(loaded) == 1
    loaded_result = loaded[0]
    assert loaded_result.score_witness_rows == ()
    assert loaded_result.source_parse_observations == (
        {
            "rule_id": "uk_oracle_xml_parse_rejected",
            "family": "source_pathology",
            "phase": "parse",
            "blocking": True,
        },
    )
    assert loaded_result.effect_diagnostics == (
        {
            "rule_id": "uk_affecting_act_xml_missing_rejected",
            "family": "source_pathology",
            "phase": "acquisition",
            "blocking": True,
        },
        {
            "rule_id": "uk_effect_source_pathology_classified",
            "source_pathology": "missing_extracted_source",
            "blocking": False,
        },
        {
            "rule_id": "uk_manual_compile_frontier_classified",
            "family": "manual_compile_frontier",
            "phase": "lowering",
            "manual_compile_status": "unclassified_frontier",
            "manual_compile_rule_id": "uk_manual_frontier_unclassified",
            "blocking": False,
        },
    )
    assert loaded_result.manual_compile_status_counts == {"unclassified_frontier": 1}
    assert loaded_result.manual_compile_rule_counts == {"uk_manual_frontier_unclassified": 1}
    assert loaded_result.uk_authority_observations == (
        {
            "rule_id": "uk_authority_source_text_only_missing",
            "family": "authority_filter",
            "phase": "lowering",
            "blocking": True,
        },
    )
    assert loaded_result.lowering_rejections == (
        {
            "rule_id": "uk_effect_payload_missing",
            "family": "lowering_filter",
            "phase": "lowering",
            "blocking": True,
        },
    )
    assert loaded_result.replay_adjudications == (
        {
            "kind": "uk_replay_target_not_found",
            "message": "Target missing",
            "source_statute": "ukpga/2000/1",
            "op_id": "op-1",
            "detail": {"target": "section:1"},
        },
    )
    assert loaded_result.uk_residual_claim_tier == "PROVED_REPLAY_BUG"
    assert loaded_result.uk_residual_claim_kind == "uk_replay_target_not_found"
    assert loaded_result.uk_residual_claim_comparison_class == "commensurable"
    assert loaded_result.uk_residual_claim_core_comparison is True
    assert loaded_result.uk_residual_only_in_replayed_count == 2
    assert loaded_result.uk_residual_only_in_oracle_count == 3
    assert loaded_result.uk_residual_section_claim_count == 1
    assert loaded_result.uk_residual_section_claim_emitted is True
    assert loaded_result.score == 0.4
    assert loaded_result.commencement_score == 0.7
    assert loaded_result.n_effect_feed_pages == 2
    assert loaded_result.n_effect_rows == 7
    assert loaded_result.effect_feed_rejection_count == 1
    assert loaded_result.effect_feed_rejection_rule_counts == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert loaded_result.effect_feed_observation_count == 3
    assert loaded_result.effect_feed_observation_rule_counts == {
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert loaded_result.effect_feed_observations == (
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "family": "source_pathology",
            "phase": "parse",
            "blocking": True,
        },
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "family": "source_pathology",
            "phase": "acquisition",
            "strict_disposition": "record",
        },
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "family": "source_pathology",
            "phase": "acquisition",
            "blocking": False,
        },
    )
    assert loaded_result.effect_feed_count_error == "ValueError: bad effect feed"
    assert loaded_result.bench_exception_count == 1
    assert loaded_result.bench_exception_rule_counts == {
        "uk_bench_unclassified_exception": 1,
    }
    assert loaded_result.bench_exception_observations == (
        {
            "rule_id": "uk_bench_unclassified_exception",
            "family": "benchmark_execution",
            "phase": "benchmark",
            "statute_id": "ukpga/2000/1",
            "blocking": True,
        },
    )
    assert loaded_result.source_parse_rejection_count == 1
    assert loaded_result.source_parse_rejection_rule_counts == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert loaded_result.source_parse_observation_count == 1
    assert loaded_result.source_parse_observation_rule_counts == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert loaded_result.enacted_source_status == "available"
    assert loaded_result.oracle_source_status == "too_small"
    assert loaded_result.enacted_source_size == 456
    assert loaded_result.oracle_source_size == 7
    assert loaded_result.enacted_source_sha256 == "enacted-sha"
    assert loaded_result.oracle_source_sha256 == "oracle-sha"
    assert loaded_result.enacted_source_url == "https://example.test/ukpga/2000/1/enacted/data.xml"
    assert loaded_result.oracle_source_url == "https://example.test/ukpga/2000/1/data.xml"
    assert loaded_result.oracle_alignment_changed_count == 4
    assert loaded_result.oracle_alignment_oracle_assigned_count == 3
    assert loaded_result.oracle_alignment_local_fallback_count == 1
    assert loaded_result.oracle_alignment_transparent_wrapper_cleared_count == 2
    assert loaded_result.oracle_alignment_before_node_count == 10
    assert loaded_result.oracle_alignment_after_node_count == 11
    assert loaded_result.oracle_alignment_node_count_mismatch is True
    assert loaded_result.oracle_alignment_match_method_counts == {"flat": 3, "local_fallback": 1}
    assert loaded_result.uk_metadata_backfill_enabled is False
    assert loaded_result.uk_oracle_alignment_enabled is False
    assert loaded_result.uk_metadata_only_effects_enabled is False
    assert loaded_result.uk_applicability_mode == "effective_date_only"
    assert loaded_result.uk_authority_mode == "source_text_only"
    assert loaded_result.uk_source_purity_lane == "source_backed_effects_assisted"
    assert loaded_result.uk_source_semantics_clean is True
    assert loaded_result.uk_source_first_candidate is False
    assert loaded_result.uk_source_first_candidate_reasons == (
        "applicability_selection_not_feed_applied",
    )
    assert loaded_result.uk_authority_rejection_count == 2
    assert loaded_result.uk_authority_rejection_rule_counts == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert loaded_result.lowering_rejection_count == 5
    assert loaded_result.lowering_observation_count == 5
    assert loaded_result.lowering_observation_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 3,
        "uk_effect_payload_missing": 2,
    }
    assert loaded_result.lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 3,
        "uk_effect_payload_missing": 2,
    }
    assert loaded_result.blocking_lowering_rejection_count == 3
    assert loaded_result.blocking_lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 3,
    }
    assert loaded_result.replay_adjudication_count == 2
    assert loaded_result.replay_adjudication_kind_counts == {
        "uk_replay_target_not_found": 2,
    }
    assert loaded_result.replay_score == 0.8
    assert loaded_result.replay_commencement_score == 0.9
    assert loaded_result.n_commenced_eids == 5
    assert loaded_result.comparison_class == "commensurable"
    assert loaded_result.core_benchmark is False


def test_uk_bench_load_legacy_replay_csv_marks_residual_claim_unknown(
    monkeypatch,
    tmp_path,
) -> None:
    bench_dir = tmp_path / "runs"
    bench_dir.mkdir()
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    path = bench_dir / "legacy.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "status",
                "n_replayed_eids",
                "n_replay_common",
                "replay_score",
                "n_ops",
                "replay_error",
                "replay_adjudication_count",
                "replay_adjudication_kind_counts",
            ]
        )
        writer.writerow(
            [
                "ukpga/2000/1",
                "ukpga",
                "2000",
                "1",
                "10",
                "11",
                "9",
                "0.8182",
                "OK",
                "10",
                "8",
                "0.7273",
                "2",
                "",
                "1",
                '{"uk_replay_target_not_found": 1}',
            ]
        )

    loaded = uk_bench._load_run("legacy")

    assert len(loaded) == 1
    assert loaded[0].uk_residual_claim_tier == "UNRESOLVED"
    assert loaded[0].uk_residual_claim_kind == "unknown_legacy_missing"
    assert loaded[0].uk_residual_section_claim_emitted is False


def test_uk_bench_save_results_omits_empty_score_witness_sidecar(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    bench_dir.mkdir()
    stale_sidecar = bench_dir / "empty.score_witnesses.csv"
    stale_sidecar.write_text("stale\n", encoding="utf-8")
    stale_diagnostics = bench_dir / "empty.diagnostics.jsonl"
    stale_diagnostics.write_text("stale\n", encoding="utf-8")
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=1,
        n_oracle_eids=1,
        n_common=1,
        score=1.0,
        status="OK",
    )

    uk_bench._save_results([result], "empty")

    assert (bench_dir / "empty.csv").exists()
    assert not stale_sidecar.exists()
    assert not stale_diagnostics.exists()


def test_uk_bench_history_records_replay_evidence_summary(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    ok_result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=2,
        n_effect_feed_pages=2,
        n_effect_rows=7,
        effect_feed_rejection_count=1,
        effect_feed_observation_count=3,
        effect_feed_observation_rule_counts={"uk_effect_feed_pages_absent_recorded": 3},
        source_acquisition_observation_count=2,
        source_acquisition_observation_rule_counts={
            "uk_affecting_act_xml_cached_recorded": 2,
        },
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_observation_count=5,
        lowering_observation_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 3,
            "uk_effect_payload_missing": 2,
        },
        lowering_rejection_count=5,
        lowering_rejection_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 3,
            "uk_effect_payload_missing": 2,
        },
        blocking_lowering_rejection_count=3,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 3},
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=4,
        score=0.4,
        status="OK",
        enacted_source_status="available",
        oracle_source_status="available",
        replay_score=0.8,
        n_ops=3,
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={"uk_replay_target_not_found": 2},
        uk_residual_claim_tier="PROVED_REPLAY_BUG",
        uk_residual_claim_kind="uk_replay_target_not_found",
        uk_residual_section_claim_count=1,
        uk_residual_section_claim_emitted=True,
        commencement_score=0.7,
        n_commenced_eids=5,
        comparison_class="commensurable",
        core_benchmark=True,
        score_witness_rows=(
            _BenchScoreWitnessRow(
                comparison_scope="raw",
                side="only_in_enacted",
                eid="section-1",
                rank=1,
                category_total=1,
                sample_limit=10,
                truncated=False,
                left_count=10,
                right_count=12,
                common_count=4,
                score_value=0.4,
            ),
        ),
    )
    error_result = _BenchResult(
        statute_id="ukpga/2001/2",
        act_type="ukpga",
        year=2001,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="ERR",
        enacted_source_status="absent",
        oracle_source_status="available",
        source_parse_rejection_count=1,
        source_parse_rejection_rule_counts={"uk_enacted_xml_parse_rejected": 1},
        source_parse_observation_count=1,
        source_parse_observation_rule_counts={"uk_enacted_xml_parse_rejected": 1},
        effect_feed_rejection_count=2,
        effect_feed_observation_count=2,
        effect_feed_observation_rule_counts={"uk_effect_feed_count_error": 2},
        uk_authority_rejection_count=1,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 1},
        lowering_observation_count=1,
        lowering_observation_rule_counts={"uk_effect_payload_missing": 1},
        lowering_rejection_count=1,
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        blocking_lowering_rejection_count=1,
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        replay_error="RuntimeError: replay failed",
        replay_adjudication_count=1,
        replay_adjudication_kind_counts={"uk_replay_error": 1},
        commencement_error="ValueError: commencement failed",
    )

    uk_bench._save_results([ok_result, error_result], "history-demo")

    with open(history_csv, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "label": "history-demo",
            "n_total": "2",
            "n_ok": "1",
            "n_core_ok": "1",
            "row_status_counts": '{"ERR": 1, "OK": 1}',
            "enacted_source_status_counts": '{"absent": 1, "available": 1}',
            "oracle_source_status_counts": '{"available": 2}',
            "score_mode": "commencement",
            "avg_score": "0.7000",
            "n_perfect": "0",
            "avg_raw_score": "0.4000",
            "avg_replay_score": "0.8000",
            "avg_commencement_score": "0.7000",
            "n_commencement_scored": "1",
            "n_replay_scored": "1",
            "n_replay_errors": "1",
            "n_commencement_errors": "1",
            "source_parse_observations": "1",
            "source_parse_observation_rules": '{"uk_enacted_xml_parse_rejected": 1}',
            "source_parse_rejections": "1",
            "source_parse_rejection_rules": '{"uk_enacted_xml_parse_rejected": 1}',
            "effect_source_pathology_counts": "{}",
            "manual_compile_status_counts": "{}",
            "manual_compile_rule_counts": "{}",
            "source_acquisition_observations": "2",
            "source_acquisition_observation_rules": (
                '{"uk_affecting_act_xml_cached_recorded": 2}'
            ),
            "source_acquisition_rejections": "0",
            "source_acquisition_rejection_rules": "{}",
            "bench_exceptions": "0",
            "bench_exception_rules": "{}",
            "effect_feed_observations": "5",
            "effect_feed_observation_rules": (
                '{"uk_effect_feed_count_error": 2, '
                '"uk_effect_feed_pages_absent_recorded": 3}'
            ),
            "effect_feed_rejections": "3",
            "authority_observations": "0",
            "authority_observation_rules": "{}",
            "authority_rejections": "3",
            "authority_rejection_rules": '{"uk_authority_source_text_only_missing": 3}',
            "lowering_observations": "6",
            "lowering_observation_rules": (
                '{"uk_effect_lowering_no_ops_rejected": 3, '
                '"uk_effect_payload_missing": 3}'
            ),
            "lowering_rejections": "6",
            "lowering_rejection_rules": (
                '{"uk_effect_lowering_no_ops_rejected": 3, '
                '"uk_effect_payload_missing": 3}'
            ),
            "blocking_lowering_rejections": "4",
            "blocking_lowering_rejection_rules": (
                '{"uk_effect_lowering_no_ops_rejected": 3, '
                '"uk_effect_payload_missing": 1}'
            ),
            "replay_adjudications": "3",
            "replay_adjudication_kinds": '{"uk_replay_error": 1, "uk_replay_target_not_found": 2}',
            "replay_adjudication_buckets": '{"replay_bug": 2, "unknown": 1}',
            "uk_residual_claim_tiers": '{"PROVED_REPLAY_BUG": 1, "UNRESOLVED": 1}',
            "uk_residual_claim_kinds": '{"not_run": 1, "uk_replay_target_not_found": 1}',
            "uk_residual_section_claims": "1",
            "score_witness_rows": "1",
            "replay_regimes": (
                '{"metadata_backfill=0;oracle_alignment=0;'
                'metadata_only_effects=0;'
                'applicability=effective_date_only;authority=source_text_only": 1, '
                '"metadata_backfill=1;oracle_alignment=1;metadata_only_effects=1;'
                'applicability=effective_date_plus_feed_applied;authority=current_mixed": 1}'
            ),
            "timestamp": rows[0]["timestamp"],
        }
    ]


def test_uk_bench_history_records_all_source_failed_runs(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    no_oracle = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="NO_ORACLE",
        enacted_source_status="available",
        oracle_source_status="too_small",
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )
    no_enacted = _BenchResult(
        statute_id="ukpga/2001/2",
        act_type="ukpga",
        year=2001,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="NO_ENACTED",
        enacted_source_status="absent",
        oracle_source_status="available",
    )

    uk_bench._save_results([no_oracle, no_enacted], "source-failed")

    with open(history_csv, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["label"] == "source-failed"
    assert rows[0]["n_total"] == "2"
    assert rows[0]["n_ok"] == "0"
    assert rows[0]["score_mode"] == "none"
    assert rows[0]["avg_score"] == ""
    assert rows[0]["row_status_counts"] == '{"NO_ENACTED": 1, "NO_ORACLE": 1}'
    assert rows[0]["enacted_source_status_counts"] == '{"absent": 1, "available": 1}'
    assert rows[0]["oracle_source_status_counts"] == '{"available": 1, "too_small": 1}'
    assert rows[0]["replay_regimes"] == (
        '{"metadata_backfill=0;oracle_alignment=0;metadata_only_effects=0;'
        'applicability=effective_date_only;authority=source_text_only": 1, '
        '"metadata_backfill=1;oracle_alignment=1;metadata_only_effects=1;'
        'applicability=effective_date_plus_feed_applied;authority=current_mixed": 1}'
    )


def test_uk_bench_show_history_formats_legacy_and_current_segments(monkeypatch, tmp_path, capsys) -> None:
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    with open(history_csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "n_total", "n_ok", "avg_score", "n_perfect", "timestamp"])
        writer.writerow(["legacy", "10", "8", "0.5000", "3", "2026-05-01 10:00"])
        writer.writerow(list(uk_bench._HISTORY_HEADERS))
        writer.writerow([
            "current",
            "2",
            "1",
            "1",
            '{"ERR": 1, "OK": 1}',
            '{"absent": 1, "available": 1}',
            '{"available": 2}',
            "commencement",
            "0.7000",
            "0",
            "0.4000",
            "0.8000",
            "0.7000",
            "1",
            "1",
            "1",
            "1",
            "1",
            '{"uk_oracle_xml_parse_rejected": 1}',
            "1",
            '{"uk_oracle_xml_parse_rejected": 1}',
            '{"missing_extracted_source": 2}',
            '{"manual_compile_candidate": 2}',
            '{"uk_manual_compile_heading_candidate": 2}',
            "3",
            (
                '{"uk_affecting_act_xml_cached_recorded": 1, '
                '"uk_affecting_act_xml_missing_rejected": 2}'
            ),
            "2",
            '{"uk_affecting_act_xml_missing_rejected": 2}',
            "1",
            '{"uk_bench_unclassified_exception": 1}',
            "3",
            '{"uk_effect_feed_pages_absent_recorded": 3}',
            "1",
            "0",
            "{}",
            "2",
            '{"uk_authority_source_text_only_missing": 2}',
            "5",
            '{"uk_effect_lowering_no_ops_rejected": 3, "uk_effect_payload_missing": 2}',
            "5",
            '{"uk_effect_lowering_no_ops_rejected": 3, "uk_effect_payload_missing": 2}',
            "3",
            '{"uk_effect_lowering_no_ops_rejected": 3}',
            "2",
            '{"uk_replay_target_not_found": 2}',
            '{"replay_bug": 2}',
            '{"PROVED_REPLAY_BUG": 1}',
            '{"uk_replay_target_not_found": 1}',
            "1",
            "1",
            '{"metadata_backfill=0;oracle_alignment=0;metadata_only_effects=1;applicability=effective_date_only;authority=source_text_only": 1}',
            "2026-05-02 10:00",
        ])

    uk_bench._show_history()

    out = capsys.readouterr().out
    assert "=== UK Bench History ===" in out
    assert "legacy: score=50.0% ok=8/10 perfect=3 at=2026-05-01 10:00 schema=legacy" in out
    assert (
        "current: score=70.0% mode=commencement ok=1/2 core=1 perfect=0 "
        "raw=40.0% replay=80.0% commencement=70.0% witness_rows=1 "
        "at=2026-05-02 10:00"
    ) in out
    assert (
        "evidence: source_parse_obs=1 source_parse_rejections=1 "
        "source_acquisition_obs=3 source_acquisition_rejections=2 "
        "bench_exceptions=1 feed_obs=3 "
        "feed_rejections=1 authority_obs=0 authority_blocking_rejections=2 "
        "lowering_obs=5 lowering_rejections=5 blocking_lowering=3 "
        "adjudications=2 residual_section_claims=1"
    ) in out
    assert (
        'source_status: rows={"ERR": 1, "OK": 1} '
        'enacted={"absent": 1, "available": 1} oracle={"available": 2}'
    ) in out
    assert 'feed_observation_rules: {"uk_effect_feed_pages_absent_recorded": 3}' in out
    assert 'source_parse_observation_rules: {"uk_oracle_xml_parse_rejected": 1}' in out
    assert 'source_parse_rejection_rules: {"uk_oracle_xml_parse_rejected": 1}' in out
    assert 'effect_source_pathology_counts: {"missing_extracted_source": 2}' in out
    assert 'manual_compile_status_counts: {"manual_compile_candidate": 2}' in out
    assert 'manual_compile_rule_counts: {"uk_manual_compile_heading_candidate": 2}' in out
    assert (
        'source_acquisition_observation_rules: {"uk_affecting_act_xml_cached_recorded": 1, '
        '"uk_affecting_act_xml_missing_rejected": 2}'
    ) in out
    assert 'source_acquisition_rejection_rules: {"uk_affecting_act_xml_missing_rejected": 2}' in out
    assert 'bench_exception_rules: {"uk_bench_unclassified_exception": 1}' in out
    assert 'authority_blocking_rejection_rules: {"uk_authority_source_text_only_missing": 2}' in out
    assert (
        'lowering_observation_rules: {"uk_effect_lowering_no_ops_rejected": 3, '
        '"uk_effect_payload_missing": 2}'
    ) in out
    assert (
        'lowering_rejection_rules: {"uk_effect_lowering_no_ops_rejected": 3, '
        '"uk_effect_payload_missing": 2}'
    ) in out
    assert 'blocking_lowering_rejection_rules: {"uk_effect_lowering_no_ops_rejected": 3}' in out
    assert 'replay_adjudication_kinds: {"uk_replay_target_not_found": 2}' in out
    assert 'replay_adjudication_buckets: {"replay_bug": 2}' in out
    assert 'uk_residual_claim_tiers: {"PROVED_REPLAY_BUG": 1}' in out
    assert 'uk_residual_claim_kinds: {"uk_replay_target_not_found": 1}' in out
    assert "regimes:" in out


def test_uk_bench_main_history_uses_formatted_renderer(monkeypatch, tmp_path, capsys) -> None:
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    with open(history_csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "n_total", "n_ok", "avg_score", "n_perfect", "timestamp"])
        writer.writerow(["legacy", "10", "8", "0.5000", "3", "2026-05-01 10:00"])

    uk_bench.main(Namespace(history=True))

    out = capsys.readouterr().out
    assert "=== UK Bench History ===" in out
    assert "legacy: score=50.0% ok=8/10 perfect=3 at=2026-05-01 10:00 schema=legacy" in out
    assert "label,n_total,n_ok,avg_score,n_perfect,timestamp" not in out


def test_uk_bench_history_appends_one_current_header_after_legacy_segment(
    monkeypatch,
    tmp_path,
) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    with open(history_csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "n_total", "n_ok", "avg_score", "n_perfect", "timestamp"])
        writer.writerow(["legacy", "10", "8", "0.5000", "3", "2026-05-01 10:00"])

    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        n_enacted_eids=2,
        n_oracle_eids=2,
        n_common=1,
        score=0.5,
        status="OK",
    )

    uk_bench._save_results([result], "current-1")
    uk_bench._save_results([result], "current-2")

    with open(history_csv, newline="") as handle:
        raw_rows = list(csv.reader(handle))
    assert raw_rows.count(list(uk_bench._HISTORY_HEADERS)) == 1
    assert [schema for schema, _row in uk_bench._history_rows()] == [
        "legacy",
        "current",
        "current",
    ]


def test_uk_bench_history_reads_previous_current_header_without_adjudication_buckets(
    monkeypatch,
    tmp_path,
) -> None:
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    previous_current_header = [
        field
        for field in uk_bench._HISTORY_HEADERS
        if field
        not in {
            "replay_adjudication_buckets",
            "uk_residual_claim_tiers",
            "uk_residual_claim_kinds",
            "uk_residual_section_claims",
        }
    ]
    previous_current_row = [
        "previous-current",
        "1",
        "1",
        "1",
        '{"OK": 1}',
        '{"available": 1}',
        '{"available": 1}',
        "raw",
        "1.0000",
        "1",
        "1.0000",
        "1.0000",
        "",
        "0",
        "1",
        "0",
        "0",
        "0",
        "{}",
        "0",
        "{}",
        "{}",
        "{}",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "0",
        "{}",
        "1",
        '{"uk_replay_target_not_found": 1}',
        "0",
        "{}",
        "2026-05-02 10:00",
    ]
    assert len(previous_current_row) == len(previous_current_header)
    with open(history_csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(previous_current_header)
        writer.writerow(previous_current_row)

    rows = uk_bench._history_rows()

    assert rows[0][0] == "current"
    assert rows[0][1]["label"] == "previous-current"
    assert rows[0][1].get("replay_adjudication_buckets", "") == ""


def test_uk_bench_corpus_csv_load_preserves_source_state(monkeypatch, tmp_path) -> None:
    corpus_csv = tmp_path / "bench_corpus.csv"
    monkeypatch.setattr(uk_bench, "_CORPUS_CSV", corpus_csv)
    with open(corpus_csv, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "statute_id",
                "type",
                "year",
                "has_enacted",
                "has_consolidated",
                "n_effects",
                "n_effect_feed_pages",
                "enacted_url",
                "current_url",
                "enacted_source_status",
                "oracle_source_status",
                "enacted_source_size",
                "oracle_source_size",
                "enacted_source_sha256",
                "oracle_source_sha256",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": "2000",
                "has_enacted": "True",
                "has_consolidated": "True",
                "n_effects": "3",
                "n_effect_feed_pages": "2",
                "enacted_url": "archive://uk/ukpga/2000/1/enacted/data.xml",
                "current_url": "archive://uk/ukpga/2000/1/data.xml",
                "enacted_source_status": "available",
                "oracle_source_status": "too_small",
                "enacted_source_size": "456",
                "oracle_source_size": "7",
                "enacted_source_sha256": "enacted-sha",
                "oracle_source_sha256": "oracle-sha",
            }
        )

    loaded = uk_bench._load_corpus_csv()

    assert loaded == [
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "has_enacted": True,
            "has_consolidated": True,
            "n_effects": 3,
            "n_effect_feed_pages": 2,
            "enacted_url": "archive://uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "archive://uk/ukpga/2000/1/data.xml",
            "enacted_source_status": "available",
            "oracle_source_status": "too_small",
            "enacted_source_size": 456,
            "oracle_source_size": 7,
            "enacted_source_sha256": "enacted-sha",
            "oracle_source_sha256": "oracle-sha",
        }
    ]


def test_uk_bench_build_corpus_csv_writes_source_urls(monkeypatch, tmp_path, capsys) -> None:
    corpus_csv = tmp_path / "bench_corpus.csv"
    monkeypatch.setattr(uk_bench, "_CORPUS_CSV", corpus_csv)
    monkeypatch.setattr(
        uk_bench,
        "_build_corpus_index",
        lambda archive, *, types=None: [
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": 2000,
                "has_enacted": True,
                "has_consolidated": True,
                "n_effects": 3,
                "n_effect_feed_pages": 2,
                "enacted_url": "archive://uk/ukpga/2000/1/enacted/data.xml",
                "current_url": "archive://uk/ukpga/2000/1/data.xml",
                "enacted_source_status": "available",
                "oracle_source_status": "too_small",
                "enacted_source_size": 456,
                "oracle_source_size": 7,
                "enacted_source_sha256": "enacted-sha",
                "oracle_source_sha256": "oracle-sha",
            }
        ],
    )

    uk_bench._build_corpus_csv(cast(Farchive, object()))

    with open(corpus_csv, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["enacted_url"] == "archive://uk/ukpga/2000/1/enacted/data.xml"
    assert rows[0]["current_url"] == "archive://uk/ukpga/2000/1/data.xml"
    assert rows[0]["enacted_source_sha256"] == "enacted-sha"
    assert rows[0]["oracle_source_sha256"] == "oracle-sha"
    assert "Written:" in capsys.readouterr().out


def test_uk_bench_compare_prints_primary_score_modes(monkeypatch, tmp_path, capsys) -> None:
    raw = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        enacted_source_status="available",
        oracle_source_status="available",
        source_parse_rejection_count=1,
        source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observation_count=1,
        source_parse_observation_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        effect_feed_rejection_count=1,
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_count=2,
        effect_feed_observation_rule_counts={"uk_effect_feed_pages_absent_recorded": 2},
        oracle_alignment_changed_count=1,
        oracle_alignment_oracle_assigned_count=1,
        oracle_alignment_match_method_counts={"flat": 1},
        oracle_alignment_before_node_count=10,
        oracle_alignment_after_node_count=11,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=5,
        score=0.5,
        status="OK",
        comparison_class="commensurable",
        text_score=0.4,
        replay_text_score=0.5,
    )
    commencement = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        enacted_source_status="available",
        oracle_source_status="available",
        enacted_source_size=456,
        oracle_source_size=789,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
        effect_feed_observation_count=1,
        effect_feed_observation_rule_counts={"uk_effect_feed_pages_absent_recorded": 1},
        effect_source_pathology_counts={"missing_extracted_source": 1},
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_authority_rejection_count=1,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 1},
        replay_adjudication_count=1,
        replay_adjudication_kind_counts={"uk_replay_target_not_found": 1},
        lowering_observation_count=2,
        lowering_observation_rule_counts={"uk_effect_lowering_no_ops_rejected": 2},
        lowering_rejection_count=2,
        lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 2},
        blocking_lowering_rejection_count=2,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 2},
        oracle_alignment_changed_count=3,
        oracle_alignment_local_fallback_count=1,
        oracle_alignment_transparent_wrapper_cleared_count=1,
        oracle_alignment_match_method_counts={"local_fallback": 1, "transparent_wrapper_cleared": 1},
        oracle_alignment_before_node_count=12,
        oracle_alignment_after_node_count=12,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=8,
        score=0.8,
        status="OK",
        comparison_class="commensurable",
        commencement_score=0.7,
        n_commenced_eids=4,
        text_score=0.45,
        replay_text_score=0.65,
    )
    raw_only = _BenchResult(
        statute_id="ukpga/2001/2",
        act_type="ukpga",
        year=2001,
        n_effects=1,
        enacted_source_status="available",
        oracle_source_status="too_small",
        effect_feed_rejection_count=2,
        effect_feed_rejection_rule_counts={"uk_effect_feed_locator_payload_missing_rejected": 2},
        lowering_observation_count=1,
        lowering_observation_rule_counts={"uk_effect_payload_missing": 1},
        lowering_rejection_count=1,
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        oracle_alignment_node_count_mismatch=True,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=6,
        score=0.6,
        status="OK",
        comparison_class="collapsed_subtree_oracle_shape",
        core_benchmark=False,
    )
    new_only = _BenchResult(
        statute_id="ukpga/2002/3",
        act_type="ukpga",
        year=2002,
        n_effects=1,
        enacted_source_status="available",
        oracle_source_status="absent",
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_authority_rejection_count=1,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 1},
        effect_source_pathology_counts={"nonstructural_root_gap": 2},
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={"uk_replay_payload_missing": 2},
        lowering_observation_count=1,
        lowering_observation_rule_counts={"uk_effect_payload_missing": 1},
        lowering_rejection_count=1,
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        blocking_lowering_rejection_count=1,
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=9,
        score=0.9,
        status="OK",
        comparison_class="no_oracle_eids",
        core_benchmark=False,
        commencement_score=0.85,
        n_commenced_eids=7,
    )
    runs = {"raw": [raw, raw_only], "comm": [commencement, new_only]}
    monkeypatch.setattr(uk_bench, "_load_run", lambda label: runs[label])
    bench_dir = tmp_path / "runs"
    bench_dir.mkdir()
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    (bench_dir / "raw.score_witnesses.csv").write_text("label\nraw\nraw2\n", encoding="utf-8")
    (bench_dir / "comm.score_witnesses.csv").write_text("label\ncomm\n", encoding="utf-8")

    uk_bench._compare_runs("raw", "comm")

    assert _primary_score_mode([raw]) == "raw"
    assert _primary_score_mode([commencement]) == "commencement"
    assert _primary_score_mode([raw, commencement]) == "mixed"
    out = capsys.readouterr().out
    assert "Score mode: raw -> commencement" in out
    assert "Only in raw: 1" in out
    assert "Only in comm: 1" in out
    assert f"Score witness sidecars: raw={bench_dir / 'raw.score_witnesses.csv'} rows=2" in out
    assert f"comm={bench_dir / 'comm.score_witnesses.csv'} rows=1" in out
    assert "Row statuses: {'OK': 2} -> {'OK': 2}" in out
    assert (
        "Comparison classes: {'collapsed_subtree_oracle_shape': 1, 'commensurable': 1} -> "
        "{'commensurable': 1, 'no_oracle_eids': 1}"
    ) in out
    assert "Core benchmark rows: 1 -> 1" in out
    assert "Source status enacted: {'available': 2} -> {'available': 2}" in out
    assert "Source status oracle: {'available': 1, 'too_small': 1} -> {'absent': 1, 'available': 1}" in out
    assert (
        "Replay regimes: {'metadata_backfill=1;oracle_alignment=1;"
        "metadata_only_effects=1;"
        "applicability=effective_date_plus_feed_applied;authority=current_mixed': 2} -> "
        "{'metadata_backfill=0;oracle_alignment=0;"
        "metadata_only_effects=1;"
        "applicability=effective_date_only;authority=source_text_only': 2}"
    ) in out
    assert (
        "Source parse observations: 1 {'uk_oracle_xml_parse_rejected': 1} -> 0 {}"
    ) in out
    assert (
        "Source parse rejections: 1 {'uk_oracle_xml_parse_rejected': 1} -> 0 {}"
    ) in out
    assert "Bench exceptions: 0 {} -> 0 {}" in out
    assert (
        "Effect-feed rejections: 3 {'uk_effect_feed_locator_payload_missing_rejected': 2, "
        "'uk_effect_feed_xml_parse_rejected': 1} -> 0 {}"
    ) in out
    assert (
        "Effect-feed observations: 2 {'uk_effect_feed_pages_absent_recorded': 2} -> "
        "1 {'uk_effect_feed_pages_absent_recorded': 1}"
    ) in out
    assert "Authority observations: 0 {} -> 0 {}" in out
    assert "Blocking authority rejections: 0 {} -> 2 {'uk_authority_source_text_only_missing': 2}" in out
    assert (
        "Replay adjudications: 0 {} -> "
        "3 {'uk_replay_payload_missing': 2, 'uk_replay_target_not_found': 1}"
    ) in out
    assert (
        "Lowering observations: 1 {'uk_effect_payload_missing': 1} -> "
        "3 {'uk_effect_lowering_no_ops_rejected': 2, 'uk_effect_payload_missing': 1}"
    ) in out
    assert (
        "Lowering rejections: total=1 blocking=0 {'uk_effect_payload_missing': 1} -> "
        "total=3 blocking=3 {'uk_effect_lowering_no_ops_rejected': 2, "
        "'uk_effect_payload_missing': 1}"
    ) in out
    assert "Blocking lowering rules: {} -> {'uk_effect_lowering_no_ops_rejected': 2, 'uk_effect_payload_missing': 1}" in out
    assert (
        "Oracle alignment: {'changed': 1, 'oracle_assigned': 1, 'local_fallback': 0, "
        "'transparent_wrapper_cleared': 0, 'before_nodes': 10, 'after_nodes': 11, "
        "'node_mismatch_rows': 1} -> "
        "{'changed': 3, 'oracle_assigned': 0, 'local_fallback': 1, "
        "'transparent_wrapper_cleared': 1, 'before_nodes': 12, 'after_nodes': 12, "
        "'node_mismatch_rows': 0}"
    ) in out
    assert (
        "Oracle alignment methods: {'flat': 1} -> "
        "{'local_fallback': 1, 'transparent_wrapper_cleared': 1}"
    ) in out
    assert "Text scores: n=1 avg=40.0% -> n=1 avg=45.0%" in out
    assert "Replay text scores: n=1 avg=50.0% -> n=1 avg=65.0%" in out
    assert "Average: 50.0% -> 70.0%" in out
    assert (
        "ukpga/2000/1: 50.0% -> 70.0% (+20.0%) "
        "status=OK class=commensurable sources=enacted:available/oracle:available "
        "source_sizes=enacted:456/oracle:789 "
        "source_urls=enacted:https://example.test/ukpga/2000/1/enacted/data.xml"
        "/oracle:https://example.test/ukpga/2000/1/data.xml "
        "source_hashes=enacted:enacted-sha/oracle:oracle-sha "
        "regime=metadata_backfill=0;oracle_alignment=0;"
        "metadata_only_effects=1;"
        "applicability=effective_date_only;authority=source_text_only "
        "source_purity=unknown source_clean=0 source_first=0 "
        "source_first_reasons=none "
        "ops=0 source_parse_observations=0 source_parse_rejections=0 "
        "source_pathologies=missing_extracted_source:1 "
        "manual_frontier=none "
        "source_acquisition_observations=0 source_acquisition_rejections=0 "
        "bench_exceptions=0 feed_observations=1 "
        "feed_rejections=0 feed_count_error=0 "
        "authority_observations=0 authority_blocking_rejections=1 "
        "lowering_observations=2 lowering_rejections=2 blocking_lowering=2 "
        "residual_claim=UNRESOLVED/not_run residual_comparison=unknown "
        "residual_sides=replayed:0/oracle:0 residual_section_claims=0 "
        "adjudication_buckets=replay_bug:1 adjudications=1"
    ) in out


def test_uk_bench_loads_legacy_commencement_score_column(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    bench_dir.mkdir()
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    with open(bench_dir / "legacy.csv", "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "commencement_score",
                "n_commenced_eids",
                "status",
                "error",
                "comparison_class",
                "core_benchmark",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "ukpga/2000/1",
                "act_type": "ukpga",
                "year": "2000",
                "n_effects": "2",
                "n_enacted_eids": "10",
                "n_oracle_eids": "12",
                "n_common": "5",
                "score": "0.5000",
                "commencement_score": "0.7000",
                "n_commenced_eids": "4",
                "status": "OK",
                "error": "",
                "comparison_class": "commensurable",
                "core_benchmark": "1",
            }
        )

    loaded = uk_bench._load_run("legacy")

    assert len(loaded) == 1
    assert loaded[0].score == 0.5
    assert loaded[0].commencement_score == 0.7
    assert loaded[0].n_effect_feed_pages == 2
    assert loaded[0].enacted_source_status == "unknown"
    assert loaded[0].oracle_source_status == "unknown"
    assert loaded[0].enacted_source_url == "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml"
    assert loaded[0].oracle_source_url == "https://www.legislation.gov.uk/ukpga/2000/1/data.xml"
    assert loaded[0].n_effect_rows == 2
    assert loaded[0].effect_feed_rejection_count == 0
    assert loaded[0].effect_feed_rejection_rule_counts == {}
    assert loaded[0].effect_feed_observation_count == 0
    assert loaded[0].effect_feed_count_error == ""
    assert loaded[0].source_parse_rejection_count == 0
    assert loaded[0].source_parse_rejection_rule_counts == {}
    assert loaded[0].source_parse_observation_count == 0
    assert loaded[0].source_parse_observation_rule_counts == {}
    assert loaded[0].oracle_alignment_changed_count == 0
    assert loaded[0].oracle_alignment_oracle_assigned_count == 0
    assert loaded[0].oracle_alignment_local_fallback_count == 0
    assert loaded[0].oracle_alignment_transparent_wrapper_cleared_count == 0
    assert loaded[0].oracle_alignment_before_node_count == 0
    assert loaded[0].oracle_alignment_after_node_count == 0
    assert loaded[0].oracle_alignment_node_count_mismatch is False
    assert loaded[0].oracle_alignment_match_method_counts == {}
    assert loaded[0].uk_metadata_backfill_enabled is True
    assert loaded[0].uk_oracle_alignment_enabled is True
    assert loaded[0].uk_applicability_mode == "effective_date_plus_feed_applied"
    assert loaded[0].uk_authority_mode == "current_mixed"
    assert loaded[0].uk_authority_rejection_count == 0
    assert loaded[0].lowering_rejection_count == 0
    assert loaded[0].lowering_rejection_rule_counts == {}
    assert loaded[0].blocking_lowering_rejection_count == 0
    assert loaded[0].blocking_lowering_rejection_rule_counts == {}
    assert loaded[0].n_commenced_eids == 4
    assert loaded[0].core_benchmark is True


def test_uk_bench_load_derives_legacy_core_flag_from_comparison_class(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    bench_dir.mkdir()
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    with open(bench_dir / "legacy-noncore.csv", "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "status",
                "error",
                "comparison_class",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "ukpga/2000/1",
                "act_type": "ukpga",
                "year": "2000",
                "n_effects": "2",
                "n_enacted_eids": "10",
                "n_oracle_eids": "12",
                "n_common": "5",
                "score": "0.5000",
                "status": "OK",
                "error": "",
                "comparison_class": "collapsed_subtree_oracle_shape",
            }
        )

    loaded = uk_bench._load_run("legacy-noncore")

    assert loaded[0].comparison_class == "collapsed_subtree_oracle_shape"
    assert loaded[0].core_benchmark is False


def test_uk_bench_save_load_preserves_replay_and_commencement_errors(monkeypatch, tmp_path) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=2,
        n_effect_feed_pages=2,
        n_effect_rows=0,
        effect_feed_rejection_count=1,
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_count=1,
        effect_feed_observation_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        oracle_alignment_changed_count=4,
        oracle_alignment_oracle_assigned_count=3,
        oracle_alignment_local_fallback_count=1,
        oracle_alignment_transparent_wrapper_cleared_count=2,
        oracle_alignment_before_node_count=10,
        oracle_alignment_after_node_count=10,
        oracle_alignment_match_method_counts={"flat": 3, "local_fallback": 1},
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=4,
        score=0.4,
        status="OK",
        n_ops=-1,
        replay_error="RuntimeError: replay failed",
        replay_adjudication_count=1,
        replay_adjudication_kind_counts={"uk_replay_payload_missing": 1},
        commencement_error="ValueError: commencement failed",
        comparison_class="commensurable",
        uk_authority_observation_count=1,
        uk_authority_observation_rule_counts={"uk_authority_source_text_only_observed": 1},
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_observation_count=3,
        lowering_observation_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 2,
            "uk_effect_payload_missing": 1,
        },
        lowering_rejection_count=3,
        lowering_rejection_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 2,
            "uk_effect_payload_missing": 1,
        },
        blocking_lowering_rejection_count=2,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 2},
    )

    uk_bench._save_results([result], "errors")
    loaded = uk_bench._load_run("errors")

    with open(bench_dir / "errors.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
        assert handle.name
    row = rows[0]
    assert None not in row
    assert row["n_ops"] == "-1"
    assert row["n_effect_feed_pages"] == "2"
    assert row["n_effect_rows"] == "0"
    assert row["effect_feed_rejection_count"] == "1"
    assert json.loads(row["effect_feed_rejection_rule_counts"]) == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert row["effect_feed_observation_count"] == "1"
    assert json.loads(row["effect_feed_observation_rule_counts"]) == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert row["uk_authority_observation_count"] == "1"
    assert json.loads(row["uk_authority_observation_rule_counts"]) == {
        "uk_authority_source_text_only_observed": 1,
    }
    assert row["uk_authority_rejection_count"] == "2"
    assert json.loads(row["uk_authority_rejection_rule_counts"]) == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert json.loads(row["lowering_observation_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
    }
    assert row["oracle_alignment_changed_count"] == "4"
    assert row["oracle_alignment_oracle_assigned_count"] == "3"
    assert row["oracle_alignment_local_fallback_count"] == "1"
    assert row["oracle_alignment_transparent_wrapper_cleared_count"] == "2"
    assert row["oracle_alignment_before_node_count"] == "10"
    assert row["oracle_alignment_after_node_count"] == "10"
    assert json.loads(row["oracle_alignment_match_method_counts"]) == {
        "flat": 3,
        "local_fallback": 1,
    }
    assert json.loads(row["lowering_rejection_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
    }
    assert json.loads(row["blocking_lowering_rejection_rule_counts"]) == {
        "uk_effect_lowering_no_ops_rejected": 2,
    }
    assert row["replay_score"] == ""
    assert row["replay_error"] == "RuntimeError: replay failed"
    assert row["replay_adjudication_count"] == "1"
    assert json.loads(row["replay_adjudication_kind_counts"]) == {
        "uk_replay_payload_missing": 1,
    }
    assert row["commencement_error"] == "ValueError: commencement failed"

    assert len(loaded) == 1
    loaded_result = loaded[0]
    assert loaded_result.n_ops == -1
    assert loaded_result.n_effect_rows == 0
    assert loaded_result.effect_feed_rejection_count == 1
    assert loaded_result.effect_feed_rejection_rule_counts == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert loaded_result.effect_feed_observation_count == 1
    assert loaded_result.effect_feed_observation_rule_counts == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert loaded_result.uk_authority_observation_count == 1
    assert loaded_result.uk_authority_observation_rule_counts == {
        "uk_authority_source_text_only_observed": 1,
    }
    assert loaded_result.uk_authority_rejection_count == 2
    assert loaded_result.uk_authority_rejection_rule_counts == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert loaded_result.oracle_alignment_changed_count == 4
    assert loaded_result.oracle_alignment_oracle_assigned_count == 3
    assert loaded_result.oracle_alignment_local_fallback_count == 1
    assert loaded_result.oracle_alignment_transparent_wrapper_cleared_count == 2
    assert loaded_result.oracle_alignment_before_node_count == 10
    assert loaded_result.oracle_alignment_after_node_count == 10
    assert loaded_result.oracle_alignment_match_method_counts == {"flat": 3, "local_fallback": 1}
    assert loaded_result.lowering_observation_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
    }
    assert loaded_result.lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 2,
        "uk_effect_payload_missing": 1,
    }
    assert loaded_result.blocking_lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 2,
    }
    assert loaded_result.replay_score == -1.0
    assert loaded_result.replay_error == "RuntimeError: replay failed"
    assert loaded_result.replay_adjudication_count == 1
    assert loaded_result.replay_adjudication_kind_counts == {
        "uk_replay_payload_missing": 1,
    }
    assert loaded_result.commencement_error == "ValueError: commencement failed"


def test_uk_bench_diagnostics_preserve_nonblocking_source_acquisition_observations() -> None:
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="OK",
        source_acquisition_rejection_count=1,
        source_acquisition_rejection_rule_counts={
            "uk_affecting_act_xml_missing_rejected": 1,
        },
        effect_diagnostics=(
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            },
            {
                "rule_id": "uk_affecting_act_xml_cached_recorded",
                "phase": "acquisition",
                "blocking": False,
                "strict_disposition": "record",
            },
        ),
    )

    rows = uk_bench._bench_diagnostic_rows_for_result(result, "demo")

    assert [
        (row["diagnostic_lane"], row["index"], row["rule_id"], row["blocking"])
        for row in rows
    ] == [
        ("source_acquisition", 0, "uk_affecting_act_xml_missing_rejected", True),
        ("source_acquisition", 1, "uk_affecting_act_xml_cached_recorded", False),
    ]


def test_uk_bench_diagnostics_use_lane_aware_blocking_for_manual_frontier() -> None:
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="OK",
        effect_diagnostics=(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "phase": "lowering",
                "source_pathology": "missing_extracted_source",
            },
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "phase": "lowering",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
                "strict_disposition": "record",
            },
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "phase": "lowering",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_crossheading_candidate",
            },
        ),
    )

    rows = uk_bench._bench_diagnostic_rows_for_result(result, "demo")

    assert [
        (row["diagnostic_lane"], row["index"], row["rule_id"], row["blocking"])
        for row in rows
    ] == [
        ("effect_source_pathology", 0, "uk_effect_source_pathology_classified", True),
        ("manual_compile_frontier", 0, "uk_manual_compile_frontier_classified", False),
        ("manual_compile_frontier", 1, "uk_manual_compile_frontier_classified", True),
    ]


def test_uk_bench_report_prints_replay_and_commencement_error_lanes(capsys) -> None:
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=2,
        n_effect_feed_pages=2,
        n_effect_rows=0,
        effect_feed_rejection_count=1,
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_count=2,
        effect_feed_observation_rule_counts={
            "uk_effect_feed_pages_absent_recorded": 1,
            "uk_effect_feed_xml_parse_rejected": 1,
        },
        enacted_source_status="available",
        oracle_source_status="available",
        enacted_source_size=456,
        oracle_source_size=789,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
        oracle_alignment_changed_count=4,
        oracle_alignment_oracle_assigned_count=3,
        oracle_alignment_local_fallback_count=1,
        oracle_alignment_transparent_wrapper_cleared_count=2,
        oracle_alignment_match_method_counts={"flat": 3, "local_fallback": 1},
        oracle_alignment_before_node_count=20,
        oracle_alignment_after_node_count=20,
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=4,
        score=0.4,
        status="OK",
        n_ops=-1,
        replay_score=0.2,
        replay_error="RuntimeError: replay failed",
        effect_source_pathology_counts={"missing_extracted_source": 3},
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={"uk_replay_target_not_found": 2},
        commencement_error="ValueError: commencement failed",
        comparison_class="commensurable",
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_observation_count=3,
        lowering_observation_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 2,
            "uk_effect_payload_missing": 1,
        },
        lowering_rejection_count=3,
        lowering_rejection_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 2,
            "uk_effect_payload_missing": 1,
        },
        blocking_lowering_rejection_count=2,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 2},
    )

    uk_bench._print_report([result], "errors")

    out = capsys.readouterr().out
    assert "Row statuses: {'OK': 1}" in out
    assert "Comparison classes: {'commensurable': 1}" in out
    assert "Core benchmark rows: 1" in out
    assert "Score mode: enacted baseline + replay (1 replayed rows)" in out
    assert "Core raw avg: 40.0%" in out
    assert "Source status: enacted={'available': 1} oracle={'available': 1}" in out
    assert ">=90%:" in out
    assert ">=90%%:" not in out
    assert ">=80%:" in out
    assert ">=80%%:" not in out
    assert "With parsed effect rows>0: 0" in out
    assert "With effect-feed pages>0: 1" in out
    assert "Effect-feed observations: rows=1 total=2" in out
    assert (
        "Effect-feed observation rules: uk_effect_feed_pages_absent_recorded=1, "
        "uk_effect_feed_xml_parse_rejected=1"
    ) in out
    assert "Effect-feed blocking rejections: rows=1 total=1" in out
    assert "Effect-feed rejection rules: uk_effect_feed_xml_parse_rejected=1" in out
    assert "All-row blocking authority rejections" not in out
    assert "All-row replay adjudications" not in out
    assert "All-row lowering rejections" not in out
    assert "Blocking authority rejections: 2" in out
    assert "Blocking authority rejection rules: uk_authority_source_text_only_missing=2" in out
    assert "Replay adjudications: 2" in out
    assert "Replay adjudication buckets: replay_bug=2" in out
    assert "Replay adjudication kinds: uk_replay_target_not_found=2" in out
    assert "Lowering observations: 3" in out
    assert (
        "Lowering observation rules: uk_effect_lowering_no_ops_rejected=2, "
        "uk_effect_payload_missing=1"
    ) in out
    assert "Lowering rejections: total=3 blocking=2" in out
    assert (
        "Lowering rejection rules: uk_effect_lowering_no_ops_rejected=2, "
        "uk_effect_payload_missing=1"
    ) in out
    assert "Blocking lowering rejection rules: uk_effect_lowering_no_ops_rejected=2" in out
    assert (
        "Oracle EID alignment: changed=4 oracle_assigned=3 local_fallback=1 "
        "transparent_wrapper_cleared=2 before_nodes=20 after_nodes=20 "
        "node_count_mismatch_rows=0"
    ) in out
    assert "Oracle EID alignment methods: flat=3, local_fallback=1" in out
    assert "Top regressions:" in out
    assert "ukpga/2000/1" in out
    assert "40.0% -> 20.0%  status=OK class=commensurable" in out
    assert "sources=enacted:available/oracle:available" in out
    assert "source_sizes=enacted:456/oracle:789" in out
    assert (
        "source_urls=enacted:https://example.test/ukpga/2000/1/enacted/data.xml"
        "/oracle:https://example.test/ukpga/2000/1/data.xml"
    ) in out
    assert (
        "regime=metadata_backfill=1;oracle_alignment=1;"
        "metadata_only_effects=1;"
        "applicability=effective_date_plus_feed_applied;authority=current_mixed"
    ) in out
    assert (
        "ops=-1 source_parse_observations=0 source_parse_rejections=0 "
        "source_pathologies=missing_extracted_source:3 "
        "manual_frontier=none "
        "source_acquisition_observations=0 source_acquisition_rejections=0 "
        "bench_exceptions=0 feed_observations=2 "
        "feed_rejections=1 feed_count_error=0 "
        "authority_observations=0 authority_blocking_rejections=2 "
        "lowering_observations=3 lowering_rejections=3 blocking_lowering=2 "
        "residual_claim=UNRESOLVED/not_run residual_comparison=unknown "
        "residual_sides=replayed:0/oracle:0 residual_section_claims=0 "
        "adjudication_buckets=replay_bug:2 adjudications=2"
    ) in out
    assert "Replay errors (1):" in out
    assert "ukpga/2000/1: RuntimeError: replay failed" in out
    assert "source_hashes=enacted:enacted-sha/oracle:oracle-sha" in out
    assert "hashes=enacted:enacted-sha oracle:oracle-sha" in out
    assert "Commencement errors (1):" in out
    assert "ukpga/2000/1: ValueError: commencement failed" in out
    assert (
        out.count(
            "sources: enacted=available (456 bytes) "
            "url=https://example.test/ukpga/2000/1/enacted/data.xml "
            "oracle=available (789 bytes) "
            "url=https://example.test/ukpga/2000/1/data.xml"
        )
        >= 2
    )


def test_uk_bench_report_prints_requested_replay_adjudication_samples(capsys) -> None:
    result = _BenchResult(
        statute_id="asp/2000/4",
        act_type="asp",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=8,
        score=0.8,
        status="OK",
        n_ops=1,
        replay_score=0.7,
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={
            "uk_replay_text_match_missing": 2,
        },
        replay_adjudications=(
            {
                "kind": "uk_replay_text_match_missing",
                "message": "missing",
                "source_statute": "asp/2007/10",
                "op_id": "op-1",
                "detail": {
                    "target": "section:70/subsection:1",
                    "target_granularity": "subsection",
                    "text_match": "or by any other person",
                    "replacement_text": "",
                    "source_shape": "",
                    "target_text_preview": "Where any decision ...",
                },
            },
            {
                "kind": "uk_replay_text_match_missing",
                "message": "missing",
                "source_statute": "ssi/2005/610",
                "op_id": "op-2",
                "detail": {
                    "target": "section:35/subsection:1/paragraph:b",
                    "text_match": "independent hospital",
                    "replacement_text": "independent hospital or private psychiatric hospital",
                },
            },
        ),
        comparison_class="commensurable",
    )

    uk_bench._print_report(
        [result],
        "samples",
        replay_adjudication_sample_kinds=("uk_replay_text_match_missing",),
        replay_adjudication_sample_limit=1,
    )

    out = capsys.readouterr().out
    assert "Replay adjudication samples:" in out
    assert "uk_replay_text_match_missing: shown=1 total=2 omitted=1" in out
    assert "asp/2000/4 source=asp/2007/10 op=op-1 target=section:70/subsection:1" in out
    assert "text_match=or by any other person" in out
    assert "op-2" not in out


def test_uk_bench_report_worst_rows_use_commencement_score_when_available(capsys) -> None:
    raw_low_comm_high = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=1,
        score=0.1,
        status="OK",
        n_effect_rows=1,
        n_effect_feed_pages=1,
        commencement_score=0.9,
        n_commenced_eids=9,
        replay_score=0.2,
        replay_commencement_score=0.2,
        comparison_class="commensurable",
        core_benchmark=True,
    )
    raw_high_comm_low = _BenchResult(
        statute_id="ukpga/2000/2",
        act_type="ukpga",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=8,
        score=0.8,
        status="OK",
        n_effect_rows=1,
        n_effect_feed_pages=1,
        commencement_score=0.4,
        n_commenced_eids=4,
        replay_score=0.85,
        replay_commencement_score=0.95,
        comparison_class="commensurable",
        core_benchmark=True,
    )

    uk_bench._print_report([raw_low_comm_high, raw_high_comm_low], "commencement")

    out = capsys.readouterr().out
    assert "Worst 2 core rows (by commenced EID score):" in out
    worst_block = out.split("Worst 2 core rows (by commenced EID score):", 1)[1]
    assert worst_block.index("ukpga/2000/2") < worst_block.index("ukpga/2000/1")
    assert "ukpga/2000/2" in out
    assert "score=40.0% raw=80.0%" in out
    assert "replay=95.0% raw_replay=85.0% ops=0" in out
    assert "Worst 2 core replay rows (by replay commenced EID score):" in out
    worst_replay_block = out.split("Worst 2 core replay rows (by replay commenced EID score):", 1)[1]
    assert worst_replay_block.index("ukpga/2000/1") < worst_replay_block.index("ukpga/2000/2")
    assert "replay=20.0% raw_replay=20.0% ops=0" in out


def test_uk_bench_report_prints_source_unavailable_rows(capsys) -> None:
    results = [
        _BenchResult(
            statute_id="ukpga/2000/1",
            act_type="ukpga",
            year=2000,
            n_effects=0,
            n_enacted_eids=0,
            n_oracle_eids=0,
            n_common=0,
            score=0.0,
            status="NO_ORACLE",
            enacted_source_status="available",
            oracle_source_status="too_small",
            enacted_source_size=456,
            oracle_source_size=7,
            enacted_source_sha256="enacted-sha",
            oracle_source_sha256="oracle-sha",
            enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
            effect_feed_rejection_count=1,
            effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
            effect_feed_observation_count=1,
            effect_feed_observation_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
            comparison_class="no_oracle_eids",
            core_benchmark=False,
        ),
        _BenchResult(
            statute_id="ukpga/2001/2",
            act_type="ukpga",
            year=2001,
            n_effects=0,
            n_enacted_eids=0,
            n_oracle_eids=0,
            n_common=0,
            score=0.0,
            status="NO_ENACTED",
            enacted_source_status="absent",
            oracle_source_status="available",
            enacted_source_size=0,
            oracle_source_size=789,
            enacted_source_sha256="",
            oracle_source_sha256="oracle-2-sha",
            enacted_source_url="https://example.test/ukpga/2001/2/enacted/data.xml",
            oracle_source_url="https://example.test/ukpga/2001/2/data.xml",
            effect_feed_observation_count=2,
            effect_feed_observation_rule_counts={"uk_effect_feed_pages_absent_recorded": 2},
            comparison_class="no_enacted_eids",
            core_benchmark=False,
        ),
    ]

    uk_bench._print_report(results, "sources")

    out = capsys.readouterr().out
    assert "Total: 2, Scored OK: 0, Source-unavailable: 2, Errors: 0" in out
    assert "Row statuses: {'NO_ENACTED': 1, 'NO_ORACLE': 1}" in out
    assert "Comparison classes: {'no_enacted_eids': 1, 'no_oracle_eids': 1}" in out
    assert "Core benchmark rows: 0" in out
    assert "Source status: enacted={'absent': 1, 'available': 1} oracle={'available': 1, 'too_small': 1}" in out
    assert "Effect-feed observations: rows=2 total=3" in out
    assert (
        "Effect-feed observation rules: uk_effect_feed_pages_absent_recorded=2, "
        "uk_effect_feed_xml_parse_rejected=1"
    ) in out
    assert "Effect-feed blocking rejections: rows=1 total=1" in out
    assert "Effect-feed rejection rules: uk_effect_feed_xml_parse_rejected=1" in out
    assert "Source unavailable rows (2):" in out
    assert "ukpga/2000/1: status=NO_ORACLE enacted=available (456 bytes) oracle=too_small (7 bytes)" in out
    assert (
        "sources: enacted=https://example.test/ukpga/2000/1/enacted/data.xml "
        "oracle=https://example.test/ukpga/2000/1/data.xml "
        "hashes=enacted:enacted-sha oracle:oracle-sha"
    ) in out
    assert "ukpga/2001/2: status=NO_ENACTED enacted=absent (0 bytes) oracle=available (789 bytes)" in out
    assert (
        "sources: enacted=https://example.test/ukpga/2001/2/enacted/data.xml "
        "oracle=https://example.test/ukpga/2001/2/data.xml "
        "hashes=enacted:(none) oracle:oracle-2-sha"
    ) in out
    assert "No valid results to report." in out


def test_uk_bench_report_distinguishes_status_ok_from_scored_ok(capsys) -> None:
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=10,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="OK",
        enacted_source_status="available",
        oracle_source_status="available",
        enacted_source_size=456,
        oracle_source_size=789,
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
        comparison_class="no_oracle_eids",
        core_benchmark=False,
    )

    uk_bench._print_report([result], "noncore")

    out = capsys.readouterr().out
    assert "Total: 1, Scored OK: 0, Source-unavailable: 0, Errors: 0" in out
    assert "Status OK rows: 1" in out
    assert "Row statuses: {'OK': 1}" in out
    assert "Comparison classes: {'no_oracle_eids': 1}" in out
    assert "Core benchmark rows: 0" in out


def test_uk_bench_report_prints_err_rows_before_empty_ok_return(capsys) -> None:
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=0,
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="ERR",
        error="ValueError: parse failed",
        bench_exception_count=1,
        bench_exception_rule_counts={"uk_bench_unclassified_exception": 1},
        uk_authority_rejection_count=1,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 1},
        replay_adjudication_count=1,
        replay_adjudication_kind_counts={"uk_replay_error": 1},
        lowering_observation_count=2,
        lowering_observation_rule_counts={"uk_effect_payload_missing": 2},
        lowering_rejection_count=2,
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 2},
        blocking_lowering_rejection_count=1,
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
    )

    uk_bench._print_report([result], "errors")

    out = capsys.readouterr().out
    assert "Total: 1, Scored OK: 0, Source-unavailable: 0, Errors: 1" in out
    assert "Bench exceptions: rows=1 total=1" in out
    assert "Bench exception rules: uk_bench_unclassified_exception=1" in out
    assert "All-row blocking authority rejections: 1" in out
    assert "All-row blocking authority rejection rules: uk_authority_source_text_only_missing=1" in out
    assert "All-row replay adjudications: 1" in out
    assert "All-row replay adjudication kinds: uk_replay_error=1" in out
    assert "All-row lowering observations: 2" in out
    assert "All-row lowering observation rules: uk_effect_payload_missing=2" in out
    assert "All-row lowering rejections: total=2 blocking=1" in out
    assert "All-row lowering rejection rules: uk_effect_payload_missing=2" in out
    assert "All-row blocking lowering rejection rules: uk_effect_payload_missing=1" in out
    assert "Error rows (1):" in out
    assert "bench_exception_rules: uk_bench_unclassified_exception=1" in out
    assert (
        "ukpga/2000/1: ValueError: parse failed "
        "enacted=available (456 bytes) oracle=too_small (7 bytes)"
    ) in out
    assert (
        "sources: enacted=https://example.test/ukpga/2000/1/enacted/data.xml "
        "oracle=https://example.test/ukpga/2000/1/data.xml "
        "hashes=enacted:enacted-sha oracle:oracle-sha"
    ) in out
    assert "No valid results to report." in out


def test_uk_bench_show_run_reports_persisted_evidence_lanes(monkeypatch, tmp_path, capsys) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    result = _BenchResult(
        statute_id="ukpga/2000/1",
        act_type="ukpga",
        year=2000,
        n_effects=2,
        n_effect_feed_pages=2,
        n_effect_rows=7,
        effect_feed_rejection_count=1,
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_count=3,
        effect_feed_observation_rule_counts={
            "uk_effect_feed_pages_absent_recorded": 2,
            "uk_effect_feed_xml_parse_rejected": 1,
        },
        effect_feed_count_error="ValueError: bad effect feed",
        source_parse_rejection_count=1,
        source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observation_count=1,
        source_parse_observation_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observations=(
            {
                "rule_id": "uk_oracle_xml_parse_rejected",
                "family": "source_pathology",
                "phase": "parse",
                "blocking": True,
            },
        ),
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        oracle_alignment_changed_count=4,
        oracle_alignment_oracle_assigned_count=3,
        oracle_alignment_local_fallback_count=1,
        oracle_alignment_transparent_wrapper_cleared_count=2,
        oracle_alignment_match_method_counts={"flat": 3, "local_fallback": 1},
        oracle_alignment_before_node_count=30,
        oracle_alignment_after_node_count=31,
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_observation_count=5,
        lowering_observation_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 3,
            "uk_effect_payload_missing": 2,
        },
        lowering_rejection_count=5,
        lowering_rejection_rule_counts={
            "uk_effect_lowering_no_ops_rejected": 3,
            "uk_effect_payload_missing": 2,
        },
        blocking_lowering_rejection_count=3,
        blocking_lowering_rejection_rule_counts={"uk_effect_lowering_no_ops_rejected": 3},
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_common=4,
        score=0.4,
        status="OK",
        n_replayed_eids=11,
        n_replay_common=8,
        replay_score=0.8,
        n_ops=3,
        commencement_score=0.7,
        n_commenced_eids=5,
        replay_commencement_score=0.9,
        comparison_class="commensurable",
        core_benchmark=False,
        score_witness_rows=(
            _BenchScoreWitnessRow(
                comparison_scope="raw",
                side="only_in_enacted",
                eid="section-1",
                rank=1,
                category_total=1,
                sample_limit=10,
                truncated=False,
                left_count=10,
                right_count=12,
                common_count=4,
                score_value=0.4,
            ),
        ),
    )

    uk_bench._save_results([result], "show")
    uk_bench._show_run("show")

    out = capsys.readouterr().out
    assert "Source status: enacted={'available': 1} oracle={'too_small': 1}" in out
    assert "Source parse observations: rows=1 total=1" in out
    assert "Source parse observation rules: uk_oracle_xml_parse_rejected=1" in out
    assert "Source parse blocking rejections: rows=1 total=1" in out
    assert "Source parse rejection rules: uk_oracle_xml_parse_rejected=1" in out
    assert "Effect-feed observations: rows=1 total=3" in out
    assert (
        "Effect-feed observation rules: uk_effect_feed_pages_absent_recorded=2, "
        "uk_effect_feed_xml_parse_rejected=1"
    ) in out
    assert "Effect-feed blocking rejections: rows=1 total=1" in out
    assert "Effect-feed rejection rules: uk_effect_feed_xml_parse_rejected=1" in out
    assert "Effect-feed count errors: rows=1" in out
    assert "ukpga/2000/1: ValueError: bad effect feed" in out
    assert (
        "Replay regime: metadata_backfill=False oracle_alignment=False "
        "metadata_only_effects=False "
        "applicability=effective_date_only authority=source_text_only"
    ) in out
    assert "Blocking authority rejections: 2" in out
    assert "Blocking authority rejection rules: uk_authority_source_text_only_missing=2" in out
    assert "Lowering observations: 5" in out
    assert (
        "Lowering observation rules: uk_effect_lowering_no_ops_rejected=3, "
        "uk_effect_payload_missing=2"
    ) in out
    assert "Lowering rejections: total=5 blocking=3" in out
    assert (
        "Lowering rejection rules: uk_effect_lowering_no_ops_rejected=3, "
        "uk_effect_payload_missing=2"
    ) in out
    assert "Blocking lowering rejection rules: uk_effect_lowering_no_ops_rejected=3" in out
    assert (
        "Oracle EID alignment: changed=4 oracle_assigned=3 local_fallback=1 "
        "transparent_wrapper_cleared=2 before_nodes=30 after_nodes=31 "
        "node_count_mismatch_rows=0"
    ) in out
    assert "Oracle EID alignment methods: flat=3, local_fallback=1" in out
    assert "Worst 1 non-core rows:" in out
    assert (
        "sources: enacted=available (456 bytes) "
        "url=https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml "
        "oracle=too_small (7 bytes) "
        "url=https://www.legislation.gov.uk/ukpga/2000/1/data.xml"
    ) in out
    assert "Score witness sidecar:" in out
    assert "show.score_witnesses.csv rows=1" in out
    assert "Bench diagnostics sidecar:" in out
    assert "show.diagnostics.jsonl rows=1" in out


def test_uk_bench_show_run_prints_persisted_replay_adjudication_samples(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    bench_dir = tmp_path / "runs"
    history_csv = tmp_path / "history.csv"
    monkeypatch.setattr(uk_bench, "_BENCH_DIR", bench_dir)
    monkeypatch.setattr(uk_bench, "_HISTORY_CSV", history_csv)
    result = _BenchResult(
        statute_id="asp/2000/4",
        act_type="asp",
        year=2000,
        n_effects=1,
        n_enacted_eids=10,
        n_oracle_eids=10,
        n_common=8,
        score=0.8,
        status="OK",
        n_ops=1,
        replay_score=0.7,
        replay_adjudication_count=2,
        replay_adjudication_kind_counts={"uk_replay_text_match_missing": 2},
        replay_adjudications=(
            {
                "kind": "uk_replay_text_match_missing",
                "message": "missing",
                "source_statute": "asp/2007/10",
                "op_id": "op-1",
                "detail": {
                    "target": "section:70/subsection:1",
                    "text_match": "or by any other person",
                },
            },
            {
                "kind": "uk_replay_text_match_missing",
                "message": "missing",
                "source_statute": "ssi/2005/610",
                "op_id": "op-2",
                "detail": {"target": "section:35/subsection:1/paragraph:b"},
            },
        ),
        comparison_class="commensurable",
    )

    uk_bench._save_results([result], "show-samples")
    capsys.readouterr()

    uk_bench._show_run(
        "show-samples",
        replay_adjudication_sample_kinds=("uk_replay_text_match_missing",),
        replay_adjudication_sample_limit=1,
    )

    out = capsys.readouterr().out
    assert "Replay adjudication samples:" in out
    assert "uk_replay_text_match_missing: shown=1 total=2 omitted=1" in out
    assert "asp/2000/4 source=asp/2007/10 op=op-1 target=section:70/subsection:1" in out
    assert "text_match=or by any other person" in out
    assert "op-2" not in out
    assert "Bench diagnostics sidecar:" in out
    assert "show-samples.diagnostics.jsonl rows=2" in out


def test_uk_bench_classifies_with_parsed_effect_rows_not_feed_pages(monkeypatch) -> None:
    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, attrs={"eId": "section-1"}),),
        ),
    )
    oracle_map = {f"node-{i}": f"section-{i}" for i in range(1, 31)}
    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)
    monkeypatch.setattr(uk_bench, "extract_eid_map_bytes", lambda _data: {"eid_map": oracle_map, "text_map": {}})
    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 5,
            "n_effect_feed_pages": 5,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.n_effects == 5
    assert result.n_effect_feed_pages == 5
    assert result.n_effect_rows == 0
    assert result.enacted_source_status == "available"
    assert result.oracle_source_status == "available"
    assert result.enacted_source_size > 100
    assert result.oracle_source_size > 100
    assert result.comparison_class == "oracle_expansion_without_effects"
    assert [(row.comparison_scope, row.side, row.eid) for row in result.score_witness_rows[:3]] == [
        ("raw", "only_in_oracle", "section-10"),
        ("raw", "only_in_oracle", "section-11"),
        ("raw", "only_in_oracle", "section-12"),
    ]
    assert result.score_witness_rows[0].category_total == 29
    assert result.score_witness_rows[0].truncated is True


def test_uk_bench_effect_feed_count_error_is_persisted(monkeypatch) -> None:
    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    def fail_effect_counts(
        _sid: str,
        _archive: object,
    ) -> tuple[int, int, dict[str, int], int, dict[str, int], tuple[dict[str, object], ...]]:
        raise ValueError("bad effect feed")

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Example",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", fail_effect_counts)
    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)
    monkeypatch.setattr(uk_bench, "extract_eid_map_bytes", lambda _data: {"eid_map": {}, "text_map": {}})

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 5,
            "n_effect_feed_pages": 5,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "OK"
    assert result.effect_feed_rejection_count == 1
    assert result.effect_feed_rejection_rule_counts == {"uk_effect_feed_count_error": 1}
    assert result.effect_feed_observation_count == 1
    assert result.effect_feed_observation_rule_counts == {"uk_effect_feed_count_error": 1}
    assert result.effect_feed_observations == (
        {
            "rule_id": "uk_effect_feed_count_error",
            "family": "source_pathology",
            "phase": "acquisition",
            "statute_id": "ukpga/2000/1",
            "reason": "UK effect feed count failed during benchmark preflight.",
            "exception_type": "ValueError",
            "exception_message": "bad effect feed",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )
    assert result.effect_feed_count_error == "ValueError: bad effect feed"


def test_uk_bench_records_available_enacted_source_parse_failure(monkeypatch) -> None:
    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    def fail_enacted_parse(*args, **kwargs) -> IRStatute:
        raise ValueError("bad enacted XML")

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))
    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", fail_enacted_parse)

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "ERR"
    assert result.comparison_class == "exception"
    assert result.enacted_source_status == "available"
    assert result.oracle_source_status == "available"
    assert result.source_parse_rejection_count == 1
    assert result.source_parse_rejection_rule_counts == {"uk_enacted_xml_parse_rejected": 1}
    assert result.source_parse_observation_count == 1
    assert result.source_parse_observation_rule_counts == {"uk_enacted_xml_parse_rejected": 1}
    assert result.error == "ValueError: bad enacted XML"


def test_uk_bench_records_available_oracle_source_parse_failure(monkeypatch) -> None:
    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Example",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))
    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)

    def fail_oracle_parse(_data: bytes) -> dict[str, dict[str, str]]:
        raise ValueError("bad oracle XML")

    monkeypatch.setattr(uk_bench, "extract_eid_map_bytes", fail_oracle_parse)

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "ERR"
    assert result.comparison_class == "exception"
    assert result.enacted_source_status == "available"
    assert result.oracle_source_status == "available"
    assert result.source_parse_rejection_count == 1
    assert result.source_parse_rejection_rule_counts == {"uk_oracle_xml_parse_rejected": 1}
    assert result.source_parse_observation_count == 1
    assert result.source_parse_observation_rule_counts == {"uk_oracle_xml_parse_rejected": 1}
    assert result.bench_exception_count == 0
    assert result.error == "ValueError: bad oracle XML"


def test_uk_bench_records_unclassified_exception_as_typed_observation(monkeypatch) -> None:
    class FakeArchive:
        def get(self, url: str) -> bytes:
            if url.endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 200) + b"</Legislation>"
            raise RuntimeError("archive backend failed")

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "ERR"
    assert result.comparison_class == "exception"
    assert result.error == "RuntimeError: archive backend failed"
    assert result.source_parse_rejection_count == 0
    assert result.bench_exception_count == 1
    assert result.bench_exception_rule_counts == {"uk_bench_unclassified_exception": 1}
    assert result.bench_exception_observations == (
        {
            "rule_id": "uk_bench_unclassified_exception",
            "family": "benchmark_execution",
            "phase": "benchmark",
            "statute_id": "ukpga/2000/1",
            "exception_type": "RuntimeError",
            "exception_message": "archive backend failed",
            "reason": "UK benchmark row failed outside a narrower source/replay diagnostic lane.",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )


def test_uk_bench_worker_archive_open_failure_becomes_typed_row(monkeypatch) -> None:
    def fail_open(_path: str) -> Farchive:
        raise RuntimeError("worker archive open failed")

    monkeypatch.setattr(uk_bench, "Farchive", fail_open)
    monkeypatch.setattr(uk_bench, "_WORKER_DB_PATH", "missing.farchive")
    monkeypatch.setattr(uk_bench, "_WORKER_ALLOW_METADATA_BACKFILL", False)
    monkeypatch.setattr(uk_bench, "_WORKER_ALLOW_ORACLE_ALIGNMENT", False)
    monkeypatch.setattr(uk_bench, "_WORKER_APPLICABILITY_MODE", "effective_date_only")
    monkeypatch.setattr(uk_bench, "_WORKER_AUTHORITY_MODE", "source_text_only")
    monkeypatch.setattr(uk_bench, "_WORKER_ALLOW_METADATA_ONLY_EFFECTS", False)

    result = uk_bench._score_statute_worker(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 2,
            "n_effect_feed_pages": 3,
            "enacted_source_status": "available",
            "oracle_source_status": "unknown",
            "enacted_url": "enacted-url",
            "current_url": "oracle-url",
        }
    )

    assert result.status == "ERR"
    assert result.comparison_class == "exception"
    assert result.error == "RuntimeError: worker archive open failed"
    assert result.n_effects == 2
    assert result.n_effect_feed_pages == 3
    assert result.enacted_source_url == "enacted-url"
    assert result.oracle_source_url == "oracle-url"
    assert result.uk_metadata_backfill_enabled is False
    assert result.uk_oracle_alignment_enabled is False
    assert result.uk_applicability_mode == "effective_date_only"
    assert result.uk_authority_mode == "source_text_only"
    assert result.uk_metadata_only_effects_enabled is False
    assert result.bench_exception_rule_counts == {"uk_bench_unclassified_exception": 1}
    assert result.bench_exception_observations[0]["exception_message"] == "worker archive open failed"


def test_uk_bench_parallel_future_failure_becomes_typed_row(monkeypatch) -> None:
    class FakeFuture:
        def result(self) -> _BenchResult:
            raise RuntimeError("worker future failed")

    class FakePool:
        submitted: list[FakeFuture]

        def __init__(self, max_workers: int):
            self.max_workers = max_workers
            self.submitted = []

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def submit(self, fn, entry):  # noqa: ANN001
            future = FakeFuture()
            self.submitted.append(future)
            return future

    def fake_as_completed(futures):  # noqa: ANN001
        return list(futures)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(concurrent.futures, "as_completed", fake_as_completed)

    class FakeArchive:
        _db_path = "uk.farchive"

    results = uk_bench._run_bench(
        [
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": 2000,
                "n_effects": 0,
            }
        ],
        cast(Farchive, FakeArchive()),
        workers=2,
        allow_metadata_backfill=False,
        allow_oracle_alignment=False,
        applicability_mode="effective_date_only",
        authority_mode="source_text_only",
        allow_metadata_only_effects=False,
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "ERR"
    assert result.error == "RuntimeError: worker future failed"
    assert result.bench_exception_count == 1
    assert result.bench_exception_rule_counts == {"uk_bench_unclassified_exception": 1}
    assert result.uk_metadata_backfill_enabled is False
    assert result.uk_oracle_alignment_enabled is False
    assert result.uk_applicability_mode == "effective_date_only"
    assert result.uk_authority_mode == "source_text_only"
    assert result.uk_metadata_only_effects_enabled is False


def test_uk_bench_parallel_submit_failure_becomes_typed_row(monkeypatch) -> None:
    class FakePool:
        def __init__(self, max_workers: int):
            self.max_workers = max_workers

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def submit(self, fn, entry):  # noqa: ANN001
            raise RuntimeError("worker submit failed")

    def fake_as_completed(futures):  # noqa: ANN001
        return list(futures)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", FakePool)
    monkeypatch.setattr(concurrent.futures, "as_completed", fake_as_completed)

    class FakeArchive:
        _db_path = "uk.farchive"

    results = uk_bench._run_bench(
        [
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": 2000,
                "n_effects": 0,
            }
        ],
        cast(Farchive, FakeArchive()),
        workers=2,
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "ERR"
    assert result.error == "RuntimeError: worker submit failed"
    assert result.bench_exception_count == 1
    assert result.bench_exception_rule_counts == {"uk_bench_unclassified_exception": 1}


def test_uk_bench_sequential_scorer_failure_becomes_typed_row(monkeypatch) -> None:
    def fail_score(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("sequential scorer failed")

    monkeypatch.setattr(uk_bench, "_score_statute", fail_score)

    class FakeArchive:
        _db_path = "uk.farchive"

    results = uk_bench._run_bench(
        [
            {
                "statute_id": "ukpga/2000/1",
                "type": "ukpga",
                "year": 2000,
                "n_effects": 0,
            }
        ],
        cast(Farchive, FakeArchive()),
        workers=1,
        allow_metadata_backfill=False,
        allow_oracle_alignment=False,
        applicability_mode="effective_date_only",
        authority_mode="source_text_only",
        allow_metadata_only_effects=False,
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "ERR"
    assert result.error == "RuntimeError: sequential scorer failed"
    assert result.bench_exception_count == 1
    assert result.bench_exception_rule_counts == {"uk_bench_unclassified_exception": 1}
    assert result.uk_metadata_backfill_enabled is False
    assert result.uk_oracle_alignment_enabled is False
    assert result.uk_applicability_mode == "effective_date_only"
    assert result.uk_authority_mode == "source_text_only"
    assert result.uk_metadata_only_effects_enabled is False


def test_uk_bench_classifies_too_small_oracle_source(monkeypatch) -> None:
    class FakeArchive:
        def get(self, url: str) -> bytes:
            if url.endswith("/enacted/data.xml"):
                return b"<xml>" + (b"x" * 200)
            return b"<short/>"

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "NO_ORACLE"
    assert result.enacted_source_status == "available"
    assert result.oracle_source_status == "too_small"
    assert result.oracle_source_size == len(b"<short/>")
    assert result.enacted_source_sha256 == hashlib.sha256(b"<xml>" + (b"x" * 200)).hexdigest()
    assert result.oracle_source_sha256 == hashlib.sha256(b"<short/>").hexdigest()
    assert result.comparison_class == "no_oracle_eids"


def test_uk_bench_no_enacted_preserves_oracle_source_state(monkeypatch) -> None:
    class FakeArchive:
        def get(self, url: str) -> bytes:
            if url.endswith("/enacted/data.xml"):
                return b"<short/>"
            return b"<xml>" + (b"x" * 200)

    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (0, 0, {}, 0, {}, ()))

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
    )

    assert result.status == "NO_ENACTED"
    assert result.enacted_source_status == "too_small"
    assert result.enacted_source_size == len(b"<short/>")
    assert result.enacted_source_sha256 == hashlib.sha256(b"<short/>").hexdigest()
    assert result.oracle_source_status == "available"
    assert result.oracle_source_size > 100
    assert result.oracle_source_sha256 == hashlib.sha256(b"<xml>" + (b"x" * 200)).hexdigest()
    assert result.comparison_class == "no_enacted_eids"


def test_uk_bench_build_corpus_index_records_source_states() -> None:
    class SimpleRows:
        def __init__(self, rows: list[tuple[str]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[tuple[str]]:
            return self._rows

    class FakeConnection:
        def execute(self, query: str):
            if "%/enacted/data.xml" in query:
                return SimpleRows([
                    ("https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",),
                    ("https://www.legislation.gov.uk/ukpga/2000/2/enacted/data.xml",),
                ])
            if "%/data.feed%" in query:
                return SimpleRows([
                    ("https://www.legislation.gov.uk/changes/affected/ukpga/2000/1/data.feed",),
                ])
            return SimpleRows([
                ("https://www.legislation.gov.uk/ukpga/2000/1/data.xml",),
                ("https://www.legislation.gov.uk/ukpga/2000/2/data.xml",),
            ])

    class FakeArchive:
        _conn = FakeConnection()

        def get(self, url: str) -> bytes | None:
            if url.endswith("/ukpga/2000/1/enacted/data.xml"):
                return b"x" * 100
            if url.endswith("/ukpga/2000/1/data.xml"):
                return b"<short/>"
            if url.endswith("/ukpga/2000/2/enacted/data.xml"):
                return None
            return b"x" * 100

    rows = uk_bench._build_corpus_index(cast(Farchive, FakeArchive()))

    assert rows == [
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "has_enacted": True,
            "has_consolidated": True,
            "n_effects": 1,
            "n_effect_feed_pages": 1,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
            "enacted_source_status": "available",
            "oracle_source_status": "too_small",
            "enacted_source_size": 100,
            "oracle_source_size": len(b"<short/>"),
            "enacted_source_sha256": hashlib.sha256(b"x" * 100).hexdigest(),
            "oracle_source_sha256": hashlib.sha256(b"<short/>").hexdigest(),
        },
        {
            "statute_id": "ukpga/2000/2",
            "type": "ukpga",
            "year": 2000,
            "has_enacted": True,
            "has_consolidated": True,
            "n_effects": 0,
            "n_effect_feed_pages": 0,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/2/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/2/data.xml",
            "enacted_source_status": "absent",
            "oracle_source_status": "available",
            "enacted_source_size": 0,
            "oracle_source_size": 100,
            "enacted_source_sha256": "",
            "oracle_source_sha256": hashlib.sha256(b"x" * 100).hexdigest(),
        },
    ]


def test_uk_bench_replay_regime_threads_compile_and_skips_oracle_adapter(monkeypatch, tmp_path) -> None:
    from lawvm.uk_legislation import oracle_align, uk_amendment_replay

    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, attrs={"eId": "section-1"}),),
        ),
    )
    compile_seen: dict[str, object] = {}
    replay_seen: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, repo_root: Path):
            compile_seen["repo_root"] = repo_root

        def compile_ops_for_statute(
            self,
            statute_id: str,
            *,
            archive: Farchive,
            allow_metadata_backfill: bool,
            applicability_mode: str,
            authority_mode: str,
            allow_metadata_only_effects: bool,
            authority_rejections_out: list[dict[str, object]],
            lowering_rejections_out: list[dict[str, object]],
            effect_diagnostics_out: list[dict[str, object]] | None = None,
        ) -> list[object]:
            compile_seen["statute_id"] = statute_id
            compile_seen["archive"] = archive
            compile_seen["allow_metadata_backfill"] = allow_metadata_backfill
            compile_seen["allow_metadata_only_effects"] = allow_metadata_only_effects
            compile_seen["applicability_mode"] = applicability_mode
            compile_seen["authority_mode"] = authority_mode
            authority_rejections_out.append({"rule_id": "uk_authority_source_text_only_missing"})
            lowering_rejections_out.append({"rule_id": "uk_effect_lowering_no_ops_rejected", "blocking": True})
            lowering_rejections_out.append({"rule_id": "uk_effect_legacy_unmarked_rejected"})
            lowering_rejections_out.append({"rule_id": "uk_effect_nonstructural_no_ops_rejected", "blocking": False})
            if effect_diagnostics_out is not None:
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_effect_source_pathology_classified",
                        "phase": "lowering",
                        "source_pathology": "missing_extracted_source",
                        "blocking": False,
                    }
                )
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_manual_compile_frontier_classified",
                        "phase": "lowering",
                        "manual_compile_status": "source_insufficient",
                        "manual_compile_rule_id": "uk_manual_frontier_missing_payload_source_insufficient",
                        "blocking": False,
                    }
                )
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_affecting_act_xml_missing_rejected",
                        "phase": "acquisition",
                        "blocking": True,
                    }
                )
            return []

    def fake_replay_uk_ops(
        ir: IRStatute,
        ops: list[object],
        *,
        eid_map: dict[str, str],
        text_map: dict[str, str],
        allow_oracle_alignment: bool,
        adjudications_out: list[object],
    ) -> IRStatute:
        replay_seen["ir"] = ir
        replay_seen["ops"] = ops
        replay_seen["eid_map"] = eid_map
        replay_seen["text_map"] = text_map
        replay_seen["allow_oracle_alignment"] = allow_oracle_alignment
        adjudications_out.append(
            CompileAdjudication(
                kind="uk_replay_target_not_found",
                message="Target missing",
                source_statute="ukpga/2000/1",
                op_id="op-1",
                detail={"target": "section:1"},
            )
        )
        return ir

    def fail_align(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("post-replay oracle adapter must be disabled")

    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)
    monkeypatch.setattr(
        uk_bench,
        "extract_eid_map_bytes",
        lambda _data: {"eid_map": {"oracle-node": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (1, 0, {}, 0, {}, ()))
    monkeypatch.setattr(uk_amendment_replay, "UKReplayPipeline", FakePipeline)
    monkeypatch.setattr(uk_amendment_replay, "replay_uk_ops", fake_replay_uk_ops)
    monkeypatch.setattr(oracle_align, "align_uk_replay_to_oracle_with_report", fail_align)

    archive = cast(Farchive, FakeArchive())
    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 1,
            "n_effect_feed_pages": 1,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        archive,
        do_replay=True,
        repo_root=tmp_path,
        allow_metadata_backfill=False,
        allow_oracle_alignment=False,
        applicability_mode="effective_date_only",
        authority_mode="source_text_only",
        allow_metadata_only_effects=False,
    )

    assert compile_seen["repo_root"] == tmp_path
    assert compile_seen["statute_id"] == "ukpga/2000/1"
    assert compile_seen["archive"] is archive
    assert compile_seen["allow_metadata_backfill"] is False
    assert compile_seen["allow_metadata_only_effects"] is False
    assert compile_seen["applicability_mode"] == "effective_date_only"
    assert compile_seen["authority_mode"] == "source_text_only"
    assert replay_seen["allow_oracle_alignment"] is False
    assert result.uk_metadata_backfill_enabled is False
    assert result.uk_oracle_alignment_enabled is False
    assert result.uk_metadata_only_effects_enabled is False
    assert result.uk_authority_rejection_count == 1
    assert result.uk_authority_rejection_rule_counts == {
        "uk_authority_source_text_only_missing": 1,
    }
    assert result.effect_source_pathology_counts == {"missing_extracted_source": 1}
    assert result.manual_compile_status_counts == {"source_insufficient": 1}
    assert result.manual_compile_rule_counts == {
        "uk_manual_frontier_missing_payload_source_insufficient": 1,
    }
    assert result.source_acquisition_observation_count == 1
    assert result.source_acquisition_observation_rule_counts == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert result.source_acquisition_rejection_count == 1
    assert result.source_acquisition_rejection_rule_counts == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert result.uk_authority_observations == (
        {"rule_id": "uk_authority_source_text_only_missing"},
    )
    assert result.lowering_rejections == (
        {"rule_id": "uk_effect_lowering_no_ops_rejected", "blocking": True},
        {"rule_id": "uk_effect_legacy_unmarked_rejected"},
        {"rule_id": "uk_effect_nonstructural_no_ops_rejected", "blocking": False},
    )
    assert [row["rule_id"] for row in result.effect_diagnostics] == [
        "uk_effect_source_pathology_classified",
        "uk_manual_compile_frontier_classified",
        "uk_affecting_act_xml_missing_rejected",
    ]
    assert result.lowering_rejection_count == 3
    assert result.lowering_observation_count == 3
    assert result.lowering_observation_rule_counts == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_effect_nonstructural_no_ops_rejected": 1,
    }
    assert result.lowering_rejection_rule_counts == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_effect_nonstructural_no_ops_rejected": 1,
    }
    assert result.blocking_lowering_rejection_count == 2
    assert result.blocking_lowering_rejection_rule_counts == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert result.replay_adjudication_count == 1
    assert result.replay_adjudication_kind_counts == {
        "uk_replay_target_not_found": 1,
    }
    assert result.replay_adjudications == (
        {
            "kind": "uk_replay_target_not_found",
            "message": "Target missing",
            "source_statute": "ukpga/2000/1",
            "op_id": "op-1",
            "detail": {"target": "section:1"},
        },
    )
    assert result.uk_applicability_mode == "effective_date_only"
    assert result.uk_authority_mode == "source_text_only"
    assert result.uk_authority_rejection_count == 1
    assert result.oracle_alignment_changed_count == 0


def test_uk_bench_replay_preimage_gap_reclassifies_manual_frontier_row() -> None:
    diagnostics = [
        {
            "rule_id": "uk_manual_compile_frontier_classified",
            "effect_id": "key-gap",
            "manual_compile_status": "deterministic_frontend_supported",
            "manual_compile_rule_id": "uk_manual_frontier_deterministic_supported",
            "manual_compile_reason": "The row already lowers to replay operations.",
            "blocking": False,
        },
        {
            "rule_id": "uk_manual_compile_frontier_classified",
            "effect_id": "key-ok",
            "manual_compile_status": "deterministic_frontend_supported",
            "manual_compile_rule_id": "uk_manual_frontier_deterministic_supported",
            "manual_compile_reason": "The row already lowers to replay operations.",
            "blocking": False,
        },
    ]

    uk_bench._apply_replay_preimage_frontier_to_effect_diagnostics(
        diagnostics,
        (
            {
                "kind": "uk_replay_text_monetary_amount_preimage_gap",
                "op_id": "key-gap",
            },
        ),
    )

    assert diagnostics[0]["manual_compile_status"] == "source_insufficient"
    assert (
        diagnostics[0]["manual_compile_rule_id"]
        == "uk_manual_frontier_text_patch_preimage_chain_gap"
    )
    assert diagnostics[0]["replay_adjudication_kind"] == (
        "uk_replay_text_monetary_amount_preimage_gap"
    )
    assert diagnostics[1]["manual_compile_status"] == "deterministic_frontend_supported"
    assert diagnostics[1]["manual_compile_rule_id"] == "uk_manual_frontier_deterministic_supported"


def test_uk_bench_replay_preimage_gap_reclassifies_frontier_shapes() -> None:
    for replay_kind in (
        "uk_replay_heading_text_preimage_gap",
        "uk_replay_text_insert_anchor_preimage_gap",
        "uk_replay_text_parenthetical_omission_preimage_gap",
    ):
        diagnostics = [
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "effect_id": "key-gap",
                "manual_compile_status": "deterministic_frontend_supported",
                "manual_compile_rule_id": "uk_manual_frontier_deterministic_supported",
                "manual_compile_reason": "The row already lowers to replay operations.",
                "blocking": False,
            },
        ]

        uk_bench._apply_replay_preimage_frontier_to_effect_diagnostics(
            diagnostics,
            ({"kind": replay_kind, "op_id": "key-gap"},),
        )

        assert diagnostics[0]["manual_compile_status"] == "source_insufficient"
        assert (
            diagnostics[0]["manual_compile_rule_id"]
            == "uk_manual_frontier_text_patch_preimage_chain_gap"
        )
        assert diagnostics[0]["replay_adjudication_kind"] == replay_kind


def test_uk_bench_score_statute_preserves_compile_diagnostics_on_replay_exception(
    monkeypatch,
    tmp_path,
) -> None:
    from lawvm.uk_legislation import uk_amendment_replay

    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, attrs={"eId": "section-1"}),),
        ),
    )

    class FakePipeline:
        def __init__(self, _repo_root: Path):
            pass

        def compile_ops_for_statute(
            self,
            _statute_id: str,
            *,
            archive: Farchive,
            allow_metadata_backfill: bool,
            applicability_mode: str,
            authority_mode: str,
            allow_metadata_only_effects: bool,
            authority_rejections_out: list[dict[str, object]],
            lowering_rejections_out: list[dict[str, object]],
            effect_diagnostics_out: list[dict[str, object]] | None = None,
        ) -> list[object]:
            assert archive is not None
            assert allow_metadata_backfill is True
            assert allow_metadata_only_effects is True
            assert applicability_mode == "effective_date_plus_feed_applied"
            assert authority_mode == "current_mixed"
            authority_rejections_out.append(
                {
                    "rule_id": "uk_authority_source_text_only_observed",
                    "blocking": False,
                }
            )
            authority_rejections_out.append(
                {
                    "rule_id": "uk_authority_source_text_only_missing",
                    "blocking": True,
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_lowering_no_ops_rejected",
                    "blocking": True,
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_nonstructural_no_ops_rejected",
                    "blocking": False,
                }
            )
            if effect_diagnostics_out is not None:
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_effect_source_pathology_classified",
                        "source_pathology": "missing_extracted_source",
                        "blocking": False,
                    }
                )
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_manual_compile_frontier_classified",
                        "manual_compile_status": "source_insufficient",
                        "manual_compile_rule_id": "uk_manual_frontier_missing_payload_source_insufficient",
                        "blocking": False,
                    }
                )
                effect_diagnostics_out.append(
                    {
                        "rule_id": "uk_affecting_act_xml_missing_rejected",
                        "blocking": True,
                    }
                )
            raise RuntimeError("compile blew up after diagnostics")

    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)
    monkeypatch.setattr(
        uk_bench,
        "extract_eid_map_bytes",
        lambda _data: {"eid_map": {"oracle-node": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (1, 0, {}, 0, {}, ()))
    monkeypatch.setattr(uk_amendment_replay, "UKReplayPipeline", FakePipeline)

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 1,
            "n_effect_feed_pages": 1,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
        do_replay=True,
        repo_root=tmp_path,
    )

    assert result.n_ops == -1
    assert result.replay_error == "RuntimeError: compile blew up after diagnostics"
    assert result.uk_authority_observation_count == 2
    assert result.uk_authority_observation_rule_counts == {
        "uk_authority_source_text_only_missing": 1,
        "uk_authority_source_text_only_observed": 1,
    }
    assert result.uk_authority_rejection_count == 1
    assert result.uk_authority_rejection_rule_counts == {
        "uk_authority_source_text_only_missing": 1,
    }
    assert result.lowering_rejection_count == 2
    assert result.lowering_observation_count == 2
    assert result.lowering_observation_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_effect_nonstructural_no_ops_rejected": 1,
    }
    assert result.lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_effect_nonstructural_no_ops_rejected": 1,
    }
    assert result.blocking_lowering_rejection_count == 1
    assert result.blocking_lowering_rejection_rule_counts == {
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert result.effect_source_pathology_counts == {"missing_extracted_source": 1}
    assert result.manual_compile_status_counts == {"source_insufficient": 1}
    assert result.manual_compile_rule_counts == {
        "uk_manual_frontier_missing_payload_source_insufficient": 1,
    }
    assert result.source_acquisition_observation_count == 1
    assert result.source_acquisition_observation_rule_counts == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert result.source_acquisition_rejection_count == 1
    assert result.source_acquisition_rejection_rule_counts == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert result.uk_authority_observations == (
        {
            "rule_id": "uk_authority_source_text_only_observed",
            "blocking": False,
        },
        {
            "rule_id": "uk_authority_source_text_only_missing",
            "blocking": True,
        },
    )
    assert result.lowering_rejections == (
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "blocking": True,
        },
        {
            "rule_id": "uk_effect_nonstructural_no_ops_rejected",
            "blocking": False,
        },
    )
    assert [row["rule_id"] for row in result.effect_diagnostics] == [
        "uk_effect_source_pathology_classified",
        "uk_manual_compile_frontier_classified",
        "uk_affecting_act_xml_missing_rejected",
    ]


def test_uk_bench_commencement_error_preserves_effect_feed_parse_observations(
    monkeypatch,
) -> None:
    from lawvm.uk_legislation import effects as effects_mod

    class FakeArchive:
        def get(self, _url: str) -> bytes:
            return b"<xml>" + (b"x" * 200)

    enacted_ir = IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, attrs={"eId": "section-1"}),),
        ),
    )

    def fake_load_effects_for_statute_from_archive(
        _statute_id: str,
        _archive: Farchive,
        *,
        parse_rejections_out: list[dict[str, object]] | None = None,
    ) -> list[object]:
        if parse_rejections_out is not None:
            parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "blocking": True,
                }
            )
        raise ValueError("commencement feed parse failed")

    monkeypatch.setattr(uk_bench, "parse_uk_statute_ir_bytes", lambda *args, **kwargs: enacted_ir)
    monkeypatch.setattr(
        uk_bench,
        "extract_eid_map_bytes",
        lambda _data: {"eid_map": {"oracle-node": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr(uk_bench, "_load_effect_row_counts", lambda _sid, _archive: (1, 0, {}, 0, {}, ()))
    monkeypatch.setattr(
        effects_mod,
        "load_effects_for_statute_from_archive",
        fake_load_effects_for_statute_from_archive,
    )

    result = uk_bench._score_statute(
        {
            "statute_id": "ukpga/2000/1",
            "type": "ukpga",
            "year": 2000,
            "n_effects": 1,
            "n_effect_feed_pages": 1,
            "enacted_url": "https://www.legislation.gov.uk/ukpga/2000/1/enacted/data.xml",
            "current_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        },
        cast(Farchive, FakeArchive()),
        do_commencement=True,
    )

    assert result.commencement_error == "ValueError: commencement feed parse failed"
    assert result.effect_feed_rejection_count == 1
    assert result.effect_feed_rejection_rule_counts == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert result.effect_feed_observation_count == 1
    assert result.effect_feed_observation_rule_counts == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }

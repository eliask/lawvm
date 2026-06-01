from __future__ import annotations

from pathlib import Path

from lawvm.tools.uk_misses import (
    _blocking_compile_records,
    _diagnostic_owner_phase_counts,
    _rejection_rule_histogram,
    uk_misses_report_jsonable,
)


def test_uk_misses_splits_blocking_compile_records() -> None:
    rows = [
        {
            "rule_id": "uk_effect_lowering_no_supported_action_rejected",
            "blocking": True,
            "affected_provisions": "s. 1",
            "effect_type": "inserted",
        },
        {
            "rule_id": "uk_effect_undated_applied_si_commencement_date",
            "blocking": False,
            "affected_provisions": "s. 2",
            "effect_type": "repealed",
        },
        {
            "rule_id": "legacy_blocking_without_flag",
            "affected_provisions": "s. 3",
            "effect_type": "substituted",
        },
    ]

    all_histogram = _rejection_rule_histogram(rows)
    blocking_histogram = _rejection_rule_histogram(_blocking_compile_records(rows))

    assert {rule_id: count for rule_id, count, _ in all_histogram} == {
        "legacy_blocking_without_flag": 1,
        "uk_effect_lowering_no_supported_action_rejected": 1,
        "uk_effect_undated_applied_si_commencement_date": 1,
    }
    assert {rule_id: count for rule_id, count, _ in blocking_histogram} == {
        "legacy_blocking_without_flag": 1,
        "uk_effect_lowering_no_supported_action_rejected": 1,
    }


def test_uk_misses_groups_compile_records_by_owner_phase() -> None:
    rows = [
        {"owner_phase": "typed_elaboration"},
        {"rule_id": "uk_effect_feed_empty_recorded"},
        {"rule_id": "uk_replay_existing_target_conflict_gap"},
    ]

    assert _diagnostic_owner_phase_counts(rows) == {
        "effect_metadata_frontend": 1,
        "replay_invariants": 1,
        "typed_elaboration": 1,
    }


def test_uk_misses_report_envelope_preserves_legacy_fields() -> None:
    report = uk_misses_report_jsonable(
        statute_id="ukpga/1978/30",
        db_path=Path("data/uk_legislation.farchive"),
        similarity=0.75,
        replay_compare_eid_count=3,
        oracle_compare_eid_count=4,
        common_eid_count=3,
        only_in_oracle_count=1,
        only_in_replayed_count=0,
        only_in_oracle_buckets={"section-1": ["section-1-a"]},
        only_in_replayed_buckets={},
        blocking_rejection_rule_counts={"uk_effect_target_gap": 1},
        blocking_rejection_owner_phase_counts={"typed_elaboration": 1},
        rejection_rule_counts={"uk_effect_target_gap": 1},
        rejection_owner_phase_counts={"typed_elaboration": 1},
    )

    assert report["report_kind"] == "uk_misses_report"
    assert report["schema"] == "lawvm.uk_misses_report.v1"
    assert (
        report["truth_claim"]
        == "uk_replay_oracle_residual_diagnostics_not_source_truth"
    )
    assert report["replay_claims"] is True
    assert report["agreement_claims"] is True
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["statute_id"] == "ukpga/1978/30"
    assert report["similarity"] == 0.75
    assert report["only_in_oracle_count"] == 1
    assert report["only_in_oracle_buckets"] == {"section-1": ["section-1-a"]}
    assert report["summary"]["blocking_rejection_owner_phase_counts"] == {
        "typed_elaboration": 1,
    }
    assert report["rows"][0]["side"] == "only_in_oracle"
    assert "eid_bucket_as_target_authority" in report["forbidden_shortcuts"]
    assert "mutation_boundary_proof" in report["next_promotion_requires"]

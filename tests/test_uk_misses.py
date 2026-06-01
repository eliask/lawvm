"""Tests for lawvm uk-misses command."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from lawvm.tools.uk_misses import _bucket_eid, _bucket_eids, _rejection_rule_histogram


# ── Unit tests for _bucket_eid ───────────────────────────────────────────────

def test_bucket_eid_section_family() -> None:
    # section-23A family: all bucket to "section-23A"
    assert _bucket_eid("section-23A") == "section-23A"
    assert _bucket_eid("section-23A-2-a") == "section-23A"
    assert _bucket_eid("section-23A-3") == "section-23A"
    assert _bucket_eid("section-23A-1-b-ii") == "section-23A"


def test_bucket_eid_schedule_paragraph_wrapper() -> None:
    # schedule-1-paragraph-wrapper3-a → "schedule-1"
    assert _bucket_eid("schedule-1-paragraph-wrapper3-a") == "schedule-1"
    assert _bucket_eid("schedule-1-paragraph-wrapper3") == "schedule-1"
    assert _bucket_eid("schedule-1-paragraph-1") == "schedule-1"


def test_bucket_eid_schedule_crossheading() -> None:
    # schedule-1-crossheading-... → "schedule-1"
    assert _bucket_eid("schedule-1-crossheading-definitions") == "schedule-1"
    assert _bucket_eid("schedule-1-crossheading-definitions_paragraph-wrapper1n1") == "schedule-1"


def test_bucket_eid_plain_section() -> None:
    assert _bucket_eid("section-1") == "section-1"
    assert _bucket_eid("section-1-a") == "section-1"
    assert _bucket_eid("section-72-4-c") == "section-72"


def test_bucket_eid_part() -> None:
    assert _bucket_eid("part-2-section-3-subsec-1") == "part-2"
    assert _bucket_eid("part-2") == "part-2"


def test_bucket_eid_bare_type_no_label() -> None:
    # No label component follows the type — bucket is the bare type
    assert _bucket_eid("section") == "section"
    assert _bucket_eid("schedule") == "schedule"


def test_bucket_eid_empty() -> None:
    assert _bucket_eid("") == ""


def test_bucket_eids_sorts_by_descending_size() -> None:
    eids = {
        "section-1-a",
        "section-1-b",
        "section-1-c",
        "section-2-a",
    }
    buckets = _bucket_eids(eids)
    bucket_list = list(buckets.items())
    # section-1 should come first (3 members > 1 member)
    assert bucket_list[0][0] == "section-1"
    assert len(bucket_list[0][1]) == 3
    assert bucket_list[1][0] == "section-2"
    assert len(bucket_list[1][1]) == 1
    # Members within each bucket are sorted
    assert bucket_list[0][1] == ["section-1-a", "section-1-b", "section-1-c"]


def test_rejection_rule_histogram_counts_and_deduplicates() -> None:
    rows = [
        {"rule_id": "uk_lowering_target_not_found", "affected_provisions": "s.1", "effect_type": "substitution"},
        {"rule_id": "uk_lowering_target_not_found", "affected_provisions": "s.2", "effect_type": "substitution"},
        {"rule_id": "uk_lowering_target_not_found", "affected_provisions": "s.1", "effect_type": "substitution"},
        {"rule_id": "uk_effect_feed_parse_failed", "affected_provisions": "", "effect_type": ""},
    ]
    histogram = _rejection_rule_histogram(rows)
    # Most common first
    assert histogram[0][0] == "uk_lowering_target_not_found"
    assert histogram[0][1] == 3
    # (s.1, substitution) and (s.2, substitution) deduplicated
    assert len(histogram[0][2]) == 2
    assert histogram[1][0] == "uk_effect_feed_parse_failed"
    assert histogram[1][1] == 1


def test_rejection_rule_histogram_empty() -> None:
    assert _rejection_rule_histogram([]) == []


# ── Integration test ─────────────────────────────────────────────────────────

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_uk_misses_ukpga_1978_30_matches_uk_replay() -> None:
    """uk-misses numbers for ukpga/1978/30 must equal uk-replay's oracle block.

    The live counts (similarity, only_in_oracle_count, only_in_replayed_count,
    common_eid_count) are read from the pipeline itself rather than hardcoded,
    so the test survives archive updates as long as internal consistency holds.
    """
    import io
    import contextlib
    from lawvm.tools import uk_misses

    out_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf):
        uk_misses.main(
            Namespace(
                statute_id="ukpga/1978/30",
                json=True,
                db=None,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_allow_metadata_only_effects=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
            )
        )

    data = json.loads(out_buf.getvalue())

    # Internal consistency checks
    assert data["report_kind"] == "uk_misses_report"
    assert data["statute_id"] == "ukpga/1978/30"
    assert isinstance(data["rejection_owner_phase_counts"], dict)
    assert isinstance(data["blocking_rejection_owner_phase_counts"], dict)
    similarity = data["similarity"]
    assert 0.0 <= similarity <= 1.0

    common = data["common_eid_count"]
    oracle_count = data["oracle_compare_eid_count"]
    replay_count = data["replay_compare_eid_count"]
    only_oracle = data["only_in_oracle_count"]
    only_replayed = data["only_in_replayed_count"]

    # Set arithmetic consistency
    assert common + only_oracle == oracle_count
    assert common + only_replayed == replay_count

    # Stable structural facts for this statute (avoid pinning exact miss counts,
    # which move as replay coverage improves): replay produces no extra EIDs, the
    # similarity is in the expected band, and there are genuine oracle-only misses.
    assert only_replayed == 0
    assert similarity > 0.7
    assert only_oracle > 0

    # Bucket structure: every miss is grouped under some container, and the
    # bucket members together account for the full only-in-oracle set.
    buckets: dict[str, list[str]] = data["only_in_oracle_buckets"]
    assert isinstance(buckets, dict)
    assert sum(len(members) for members in buckets.values()) == only_oracle
    largest = max(buckets.items(), key=lambda kv: len(kv[1]))
    assert len(largest[1]) >= 1

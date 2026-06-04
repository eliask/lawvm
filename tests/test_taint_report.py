"""Tests for ClaimRetractionTaintReport data types (Slice 5).

Covers:
  - Serialization round-trip
  - write_taint_report / read_taint_report
  - find helpers
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from lawvm.core.manual_claims.taint_report import (
    AffectedBuild,
    ClaimRetractionTaintReport,
    InvalidatedPITInterval,
    find_taint_reports_for_build,
    find_taint_reports_for_claim,
    list_all_taint_reports,
    read_taint_report,
    report_from_dict,
    report_to_dict,
    write_taint_report,
)


def _make_report(claim_id: str = "abc" * 21 + "ab", num_builds: int = 1) -> ClaimRetractionTaintReport:
    builds = []
    for i in range(num_builds):
        builds.append(AffectedBuild(
            build_id=f"build-{i:03d}",
            profile="strict_with_attested_claims",
            projection_artifact_path=f"/data/fi/v1/fi_refs__strict_with_attested_claims-{i}.parquet",
            affected_projection_row_hashes=(f"hash{i}a", f"hash{i}b"),
            invalidated_PIT_intervals=(
                InvalidatedPITInterval(
                    target_locator="section:3",
                    interval_start=date(2020, 1, 1),
                    interval_end=None,
                ),
            ),
            dependent_downstream_artifacts=(),
        ))
    return ClaimRetractionTaintReport(
        retracted_claim_id=claim_id,
        retraction_event_id=f"{claim_id}:2026-06-04T00:00:00+00:00",
        retraction_timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        retraction_reason="test retraction",
        affected_builds=tuple(builds),
    )


class TestTaintReportSerdes:

    def test_round_trip_serialization(self):
        report = _make_report("abc" * 21 + "ab", num_builds=2)
        d = report_to_dict(report)
        restored = report_from_dict(d)

        assert restored.retracted_claim_id == report.retracted_claim_id
        assert restored.retraction_reason == report.retraction_reason
        assert len(restored.affected_builds) == 2
        ab = restored.affected_builds[0]
        assert ab.build_id == "build-000"
        assert len(ab.invalidated_PIT_intervals) == 1
        iv = ab.invalidated_PIT_intervals[0]
        assert iv.interval_start == date(2020, 1, 1)
        assert iv.interval_end is None
        assert iv.target_locator == "section:3"

    def test_write_and_read_taint_report(self, tmp_path: Path):
        report = _make_report(num_builds=1)
        written_path = write_taint_report(report, tmp_path)

        assert written_path.exists()
        restored = read_taint_report(written_path)
        assert restored.retracted_claim_id == report.retracted_claim_id
        assert len(restored.affected_builds) == 1

    def test_find_taint_reports_for_claim(self, tmp_path: Path):
        claim_id = "aaa" * 21 + "aa"
        report = _make_report(claim_id=claim_id, num_builds=1)
        write_taint_report(report, tmp_path)

        paths = find_taint_reports_for_claim(tmp_path, claim_id)
        assert len(paths) >= 1

    def test_find_taint_reports_for_build(self, tmp_path: Path):
        report = _make_report(num_builds=1)
        write_taint_report(report, tmp_path)

        paths = find_taint_reports_for_build(tmp_path, "build-000")
        assert len(paths) == 1

    def test_list_all_taint_reports(self, tmp_path: Path):
        report1 = _make_report("aaa" * 21 + "aa", num_builds=1)
        report2 = _make_report("bbb" * 21 + "bb", num_builds=1)
        write_taint_report(report1, tmp_path)
        write_taint_report(report2, tmp_path)

        all_paths = list_all_taint_reports(tmp_path)
        assert len(all_paths) >= 2

    def test_no_affected_builds_writes_top_level(self, tmp_path: Path):
        report = ClaimRetractionTaintReport(
            retracted_claim_id="ccc" * 21 + "cc",
            retraction_event_id="ccc-event",
            retraction_timestamp=datetime(2026, 6, 4, tzinfo=timezone.utc),
            retraction_reason="no builds consumed this",
            affected_builds=(),
        )
        path = write_taint_report(report, tmp_path)
        assert path.exists()
        restored = read_taint_report(path)
        assert len(restored.affected_builds) == 0

    def test_invalidated_pit_interval_with_end_date(self, tmp_path: Path):
        iv = InvalidatedPITInterval(
            target_locator="section:5",
            interval_start=date(2020, 1, 1),
            interval_end=date(2024, 12, 31),
        )
        ab = AffectedBuild(
            build_id="build-dated",
            profile="strict_with_attested_claims",
            projection_artifact_path="/tmp/test.parquet",
            affected_projection_row_hashes=("hash1",),
            invalidated_PIT_intervals=(iv,),
            dependent_downstream_artifacts=(),
        )
        report = ClaimRetractionTaintReport(
            retracted_claim_id="ddd" * 21 + "dd",
            retraction_event_id="ddd-event",
            retraction_timestamp=datetime(2026, 6, 4, tzinfo=timezone.utc),
            retraction_reason="dated interval test",
            affected_builds=(ab,),
        )
        path = write_taint_report(report, tmp_path)
        restored = read_taint_report(path)
        restored_iv = restored.affected_builds[0].invalidated_PIT_intervals[0]
        assert restored_iv.interval_end == date(2024, 12, 31)

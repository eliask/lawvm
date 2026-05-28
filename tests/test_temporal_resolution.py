from __future__ import annotations

from typing import cast

import pytest

from lawvm.core.diagnostic_records import validate_diagnostic_detail
from lawvm.core.temporal_resolution import (
    TEMPORAL_CERTIFIED_UNTRIGGERED,
    TEMPORAL_FUTURE_EFFECTIVE_DATE,
    TEMPORAL_SOURCE_BACKED_OVERRIDE,
    TemporalResolutionStatus,
    TemporalResolutionEvidence,
)


def test_temporal_resolution_evidence_projects_diagnostic_detail() -> None:
    detail = TemporalResolutionEvidence(
        rule_id="test_temporal_source_override",
        phase="lowering",
        reason="source metadata supplied the missing date",
        status=TEMPORAL_SOURCE_BACKED_OVERRIDE,
        effective_date="2025-01-02",
        source_locator="source://instrument",
        authority_layer="SOURCE_METADATA",
        detail={"effect_id": "e1"},
    ).to_diagnostic_detail()

    assert detail == {
        "rule_id": "test_temporal_source_override",
        "phase": "lowering",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "family": "temporal_resolution",
        "reason": "source metadata supplied the missing date",
        "temporal_resolution_status": "source_backed_override",
        "effective_date": "2025-01-02",
        "source_locator": "source://instrument",
        "authority_layer": "SOURCE_METADATA",
        "effect_id": "e1",
    }
    assert validate_diagnostic_detail(detail) == ()


def test_temporal_resolution_evidence_blocks_strict_when_blocking() -> None:
    detail = TemporalResolutionEvidence(
        rule_id="test_temporal_future",
        phase="temporal",
        reason="date is after the point in time",
        status=TEMPORAL_FUTURE_EFFECTIVE_DATE,
        effective_date="2025-03-01",
        as_of="2025-02-01",
        blocking=True,
    ).to_diagnostic_detail()

    assert detail["blocking"] is True
    assert detail["strict_disposition"] == "block"
    assert detail["temporal_resolution_status"] == "future_effective_date"
    assert validate_diagnostic_detail(detail) == ()


def test_temporal_resolution_evidence_projects_certified_untriggered_status() -> None:
    detail = TemporalResolutionEvidence(
        rule_id="test_temporal_certified_untriggered",
        phase="temporal",
        reason="coverage proves no trigger as of the query horizon",
        status=TEMPORAL_CERTIFIED_UNTRIGGERED,
        as_of="2026-04-07",
        source_locator="coverage://commencement-instruments",
    ).to_diagnostic_detail()

    assert detail["temporal_resolution_status"] == "certified_untriggered"
    assert detail["as_of"] == "2026-04-07"
    assert validate_diagnostic_detail(detail) == ()


def test_temporal_resolution_evidence_rejects_reserved_detail_keys() -> None:
    with pytest.raises(ValueError, match="effective_date"):
        TemporalResolutionEvidence(
            rule_id="test_temporal_bad",
            phase="temporal",
            reason="bad detail",
            status=TEMPORAL_SOURCE_BACKED_OVERRIDE,
            effective_date="2025-01-02",
            detail={"effective_date": "2025-01-03"},
        )


def test_temporal_resolution_evidence_requires_dates_for_date_statuses() -> None:
    with pytest.raises(ValueError, match="requires effective_date"):
        TemporalResolutionEvidence(
            rule_id="test_temporal_bad",
            phase="temporal",
            reason="bad detail",
            status=TEMPORAL_FUTURE_EFFECTIVE_DATE,
        )


def test_temporal_resolution_evidence_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        TemporalResolutionEvidence(
            rule_id="test_temporal_bad",
            phase="temporal",
            reason="bad status",
            status=cast(TemporalResolutionStatus, "dateish"),
        )


def test_temporal_resolution_evidence_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="family must be one of"):
        TemporalResolutionEvidence(
            rule_id="test_temporal_bad",
            phase="temporal",
            reason="bad family",
            status=TEMPORAL_SOURCE_BACKED_OVERRIDE,
            effective_date="2025-01-02",
            family="commencement",
        )


def test_certified_untriggered_requires_authority_or_coverage_witness() -> None:
    with pytest.raises(ValueError, match="trigger_coverage_certificate"):
        TemporalResolutionEvidence(
            rule_id="test_temporal_bad",
            phase="temporal",
            reason="unwitnessed non-trigger claim",
            status=TEMPORAL_CERTIFIED_UNTRIGGERED,
            as_of="2026-04-07",
        )

    detail = TemporalResolutionEvidence(
        rule_id="test_temporal_coverage",
        phase="temporal",
        reason="coverage certificate proves no trigger",
        status=TEMPORAL_CERTIFIED_UNTRIGGERED,
        as_of="2026-04-07",
        detail={"trigger_coverage_certificate": "coverage://2026-04-07"},
    ).to_diagnostic_detail()
    assert detail["trigger_coverage_certificate"] == "coverage://2026-04-07"

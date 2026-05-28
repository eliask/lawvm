from __future__ import annotations

import pytest

from lawvm.core.source_lane import (
    SourceLaneAttempt,
    SourceLaneSelectionEvidence,
    source_lane_attempt_from_mapping,
)


def test_source_lane_selection_projects_diagnostic_detail() -> None:
    evidence = SourceLaneSelectionEvidence(
        rule_id="test_source_lane_selected",
        phase="acquisition",
        reason="fallback lane selected with evidence",
        selected_lane="legacy",
        selected_locator="https://example.test/source.pdf",
        attempts=(
            SourceLaneAttempt(
                lane="legacy",
                locator="https://example.test/source.pdf",
                status="valid",
                detail={"payload_digest": "abc"},
            ),
        ),
        detail={"source_id": "source-1"},
    )

    assert evidence.to_diagnostic_detail() == {
        "rule_id": "test_source_lane_selected",
        "family": "source_lane_selection",
        "phase": "acquisition",
        "reason": "fallback lane selected with evidence",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "selected_source_lane": "legacy",
        "selected_source_locator": "https://example.test/source.pdf",
        "source_lane_attempts": (
            {
                "lane": "legacy",
                "status": "valid",
                "locator": "https://example.test/source.pdf",
                "payload_digest": "abc",
            },
        ),
        "source_id": "source-1",
    }


def test_source_lane_attempt_from_mapping_accepts_url_alias() -> None:
    attempt = source_lane_attempt_from_mapping(
        {
            "lane": "official_pdf",
            "url": "https://example.test/source.pdf",
            "status": "valid_pdf",
        }
    )

    assert attempt == SourceLaneAttempt(
        lane="official_pdf",
        locator="https://example.test/source.pdf",
        status="valid_pdf",
    )


def test_source_lane_selection_rejects_unowned_selection_shape() -> None:
    with pytest.raises(ValueError, match="selected_lane"):
        SourceLaneSelectionEvidence(
            rule_id="test_source_lane_selected",
            phase="acquisition",
            reason="fallback lane selected with evidence",
            selected_lane="",
            attempts=(SourceLaneAttempt(lane="legacy", status="valid"),),
        )

    with pytest.raises(ValueError, match="attempts"):
        SourceLaneSelectionEvidence(
            rule_id="test_source_lane_selected",
            phase="acquisition",
            reason="fallback lane selected with evidence",
            selected_lane="legacy",
            attempts=(),
        )

    with pytest.raises(ValueError, match="override source-lane keys"):
        SourceLaneAttempt(lane="legacy", status="valid", detail={"status": "override"})

    with pytest.raises(ValueError, match="override source-lane keys"):
        SourceLaneSelectionEvidence(
            rule_id="test_source_lane_selected",
            phase="acquisition",
            reason="fallback lane selected with evidence",
            selected_lane="legacy",
            attempts=(SourceLaneAttempt(lane="legacy", status="valid"),),
            detail={"selected_source_lane": "override"},
        )


def test_source_lane_selection_requires_selected_lane_to_be_attempted_or_explicit_none() -> None:
    with pytest.raises(ValueError, match="selected_lane must match an attempted lane"):
        SourceLaneSelectionEvidence(
            rule_id="test_source_lane_selected",
            phase="acquisition",
            reason="selected lane was not listed among attempts",
            selected_lane="enacted_xml",
            attempts=(SourceLaneAttempt(lane="current_xml", status="too_small"),),
        )

    evidence = SourceLaneSelectionEvidence(
        rule_id="test_source_lane_failed",
        phase="acquisition",
        reason="all lanes failed",
        selected_lane="no_source_lane_selected_fetch_failed",
        attempts=(SourceLaneAttempt(lane="official_xml", status="fetch_failed"),),
        blocking=True,
        strict_disposition="block",
    )
    assert evidence.to_diagnostic_detail()["selected_source_lane"] == "no_source_lane_selected_fetch_failed"

    routed = SourceLaneSelectionEvidence(
        rule_id="test_source_lane_selected_route",
        phase="acquisition",
        reason="selected route is a more specific variant of the selected lane",
        selected_lane="sec1_fallback_pre_routing",
        attempts=(SourceLaneAttempt(lane="sec1_fallback", status="selected"),),
    )
    assert routed.to_diagnostic_detail()["source_lane_attempts"][0]["status"] == "selected"

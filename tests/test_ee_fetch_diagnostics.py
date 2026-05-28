from __future__ import annotations

from lawvm.estonia.fetch import RTXmlMetadataDiagnostic, RedactionsFeedDiagnostic


def test_redactions_feed_diagnostic_detail_uses_standard_envelope() -> None:
    diagnostic = RedactionsFeedDiagnostic(
        rule_id="ee_redactions_feed_unavailable",
        family="source_pathology",
        phase="acquisition",
        reason="feed unavailable",
        grupi_id="123",
        url="https://example.test/feed",
        exception_type="RuntimeError",
    )

    assert diagnostic.as_detail() == {
        "rule_id": "ee_redactions_feed_unavailable",
        "phase": "acquisition",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "family": "source_pathology",
        "reason": "feed unavailable",
        "grupi_id": "123",
        "url": "https://example.test/feed",
        "exception_type": "RuntimeError",
        "source_lane_selection": {
            "rule_id": "ee_redactions_feed_unavailable",
            "phase": "acquisition",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "family": "source_lane_selection",
            "reason": "feed unavailable",
            "selected_source_lane": "no_source_lane_selected_fetch_failed",
            "selected_source_locator": "",
            "source_lane_attempts": (
                {
                    "lane": "riigi_teataja_redactions_feed",
                    "status": "fetch_failed",
                    "locator": "https://example.test/feed",
                },
            ),
        },
    }


def test_rt_xml_metadata_diagnostic_detail_preserves_nested_detail() -> None:
    diagnostic = RTXmlMetadataDiagnostic(
        rule_id="ee_rt_xml_metadata_parse_failed",
        family="source_pathology",
        phase="extraction",
        reason="metadata parse failed",
        extractor="extract_amendment_refs",
        exception_type="ParseError",
        element_name="muutmismarge",
        detail={"aktViide": "456"},
        blocking=False,
    )

    assert diagnostic.as_detail() == {
        "rule_id": "ee_rt_xml_metadata_parse_failed",
        "phase": "extraction",
        "blocking": False,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "family": "source_pathology",
        "reason": "metadata parse failed",
        "extractor": "extract_amendment_refs",
        "exception_type": "ParseError",
        "element_name": "muutmismarge",
        "detail": {"aktViide": "456"},
    }

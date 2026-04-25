"""Tests for B4: Strict source payload confidence.

Verifies that section claims carry strict_payload_confidence
derived from C1 section-local strict verdicts.
"""
from __future__ import annotations

from lawvm.core.compile_result import SectionStrictVerdict
from lawvm.tools.evidence_claims import _build_section_claims


def _make_section_result(section: str = "1") -> dict:
    return {
        "section": section,
        "diagnosis": "REPLAY_MISSING",
        "blame_source": "2020/100",
        "oracle_text": "oracle",
        "replay_text": "replay",
    }


class TestStrictPayloadConfidence:
    """B4: strict_payload_confidence derived from section strict verdicts."""

    def test_strict_clean_section(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1", amendment_id="2020/100", status="strict_clean",
        )
        claims = _build_section_claims(
            section_results=[_make_section_result()],
            section_strict_verdicts={"1": ssv},
        )
        assert claims[0]["strict_payload_confidence"] == "strict_clean"

    def test_source_incomplete_section(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1", amendment_id="2020/100",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
            status="source_incomplete",
        )
        claims = _build_section_claims(
            section_results=[_make_section_result()],
            section_strict_verdicts={"1": ssv},
        )
        assert claims[0]["strict_payload_confidence"] == "source_incomplete"

    def test_recovery_dependent_section(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1", amendment_id="2020/100",
            barrier_codes=("APPLY.UNCOVERED_BODY_RECOVERY",),
            status="strict_blocked_by_recovery",
        )
        claims = _build_section_claims(
            section_results=[_make_section_result()],
            section_strict_verdicts={"1": ssv},
        )
        assert claims[0]["strict_payload_confidence"] == "recovery_dependent"

    def test_unknown_when_no_verdicts(self) -> None:
        claims = _build_section_claims(
            section_results=[_make_section_result()],
            section_strict_verdicts=None,
        )
        assert claims[0]["strict_payload_confidence"] == "unknown"

    def test_unknown_when_section_not_in_verdicts(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="99", amendment_id="2020/100", status="strict_clean",
        )
        claims = _build_section_claims(
            section_results=[_make_section_result()],
            section_strict_verdicts={"99": ssv},
        )
        assert claims[0]["strict_payload_confidence"] == "unknown"

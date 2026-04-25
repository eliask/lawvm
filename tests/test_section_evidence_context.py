"""Tests for A3: SectionEvidenceContext type and factory.

Verifies that build_section_contexts() correctly bundles per-section
facts from the same raw inputs that _build_section_claims() consumes.
"""
from __future__ import annotations

from typing import cast

from lawvm.core.chain_completeness import ChainCompletenessStatus, CompletenessBlocker
from lawvm.core.compile_result import SectionStrictVerdict
from lawvm.core.section_evidence_context import (
    AlternativeReplayMatch,
    CrossChapterOracleMatch,
    OracleRangeMatch,
    SectionEvidenceContext,
    build_section_contexts,
)


class TestSectionEvidenceContextDefaults:
    """SectionEvidenceContext with default field values."""

    def test_minimal_construction(self) -> None:
        ctx = SectionEvidenceContext(section_label="1")
        assert ctx.section_label == "1"
        assert ctx.diagnosis == ""
        assert ctx.blame_source == ""
        assert ctx.similarity == 0.0
        assert ctx.bisect_support == {}
        assert ctx.strict_verdict is None
        assert ctx.strict_payload_confidence == "unknown"
        assert ctx.invariant_violations == []
        assert ctx.alternative_replay_match is None
        assert ctx.oracle_range_match is None
        assert ctx.cross_chapter_oracle_match is None
        assert ctx.has_timeline_entry is None
        assert ctx.html_noncommensurable_reason == ""
        assert ctx.has_extraction_gap is False
        assert ctx.oracle_suspect_detail == ""
        assert ctx.oracle_text == ""
        assert ctx.replay_text == ""
        assert ctx.oracle_content_absent is False
        assert ctx.has_complete_chain is False
        assert ctx.chain_incomplete_reasons == []
        assert ctx.strict_status == ""
        assert ctx.strict_amendment_id == ""
        assert ctx.strict_barrier_kinds == ()
        assert ctx.strict_barrier_families == ()

    def test_mutable_defaults_are_independent(self) -> None:
        """Each instance gets its own independent containers."""
        a = SectionEvidenceContext(section_label="1")
        b = SectionEvidenceContext(section_label="2")
        a.invariant_violations.append({"kind": "test"})
        assert a.bisect_support is not b.bisect_support
        assert b.bisect_support == {}
        assert b.invariant_violations == []


class TestBuildSectionContextsBasic:
    """build_section_contexts() produces one context per section."""

    def test_one_section(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "oracle_text": "oracle text",
                    "replay_text": "replay text",
                },
            ],
        )
        assert len(contexts) == 1
        assert "1" in contexts
        ctx = contexts["1"]
        assert ctx.section_label == "1"
        assert ctx.diagnosis == "REPLAY_MISSING"
        assert ctx.blame_source == "2020/100"
        assert ctx.oracle_text == "oracle text"
        assert ctx.replay_text == "replay text"
        assert 0.0 <= ctx.similarity <= 1.0

    def test_multiple_sections(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
                {"section": "2", "diagnosis": "REPLAY_MISSING",
                 "oracle_text": "b", "replay_text": "c"},
                {"section": "3", "diagnosis": "ORACLE_STALE",
                 "oracle_text": "d", "replay_text": "e"},
            ],
        )
        assert set(contexts.keys()) == {"1", "2", "3"}

    def test_empty_section_results(self) -> None:
        contexts = build_section_contexts(section_results=[])
        assert contexts == {}

    def test_skips_empty_section_label(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
        )
        assert contexts == {}


class TestBisectSupportAttachment:
    """Bisect support is correctly keyed and attached."""

    def test_bisect_support_attached(self) -> None:
        bisect_row = {
            "section": "1",
            "blame_source": "2020/100",
            "preexisting_before_any_drop": True,
            "baseline_score": 0.95,
            "first_bad_source": "2015/50",
        }
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_bisect=[bisect_row],
        )
        ctx = contexts["1"]
        assert ctx.bisect_support == bisect_row
        assert ctx.bisect_support is not bisect_row
        assert ctx.bisect_support.get("preexisting_before_any_drop") is True

    def test_bisect_support_empty_when_no_match(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_bisect=[
                {"section": "99", "blame_source": "2020/100"},
            ],
        )
        assert contexts["1"].bisect_support == {}


class TestStrictVerdictAttachment:
    """Section strict verdict and payload confidence."""

    def test_strict_clean_verdict(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            status="strict_clean",
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts={"1": ssv},
        )
        ctx = contexts["1"]
        assert ctx.strict_verdict is ssv
        assert ctx.strict_payload_confidence == "strict_clean"

    def test_source_incomplete_verdict(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            status="source_incomplete",
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert contexts["1"].strict_payload_confidence == "source_incomplete"

    def test_recovery_dependent_verdict(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            status="strict_blocked_by_recovery",
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert contexts["1"].strict_payload_confidence == "recovery_dependent"

    def test_degraded_verdict(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            status="internal_failure",
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert contexts["1"].strict_payload_confidence == "degraded"

    def test_no_verdict_gives_unknown(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts=None,
        )
        assert contexts["1"].strict_payload_confidence == "unknown"

    def test_missing_section_in_verdicts_gives_unknown(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_strict_verdicts={
                "99": SectionStrictVerdict(
                    section_label="99",
                    amendment_id="2020/100",
                    status="strict_clean",
                )
            },
        )
        assert contexts["1"].strict_payload_confidence == "unknown"

    def test_strict_projection_properties(self) -> None:
        ctx = SectionEvidenceContext(
            section_label="1",
            strict_verdict=SectionStrictVerdict(
                section_label="1",
                amendment_id="2020/100",
                status="strict_blocked_by_recovery",
                barrier_codes=(
                    "PARSE.EXTRACTION_FALLBACK",
                    "TIME.MISSING_EFFECTIVE_DATE",
                ),
            ),
        )
        assert ctx.strict_status == "strict_blocked_by_recovery"
        assert ctx.strict_amendment_id == "2020/100"
        assert ctx.strict_barrier_kinds == (
            "PARSE.EXTRACTION_FALLBACK",
            "TIME.MISSING_EFFECTIVE_DATE",
        )
        assert ctx.strict_barrier_families == ("extraction", "temporal")


class TestChainCompletenessAttachment:
    def test_chain_reason_projection(self) -> None:
        ctx = SectionEvidenceContext(
            section_label="1",
            chain_completeness=ChainCompletenessStatus(
                section_label="1",
                is_complete=False,
                blockers=[
                    CompletenessBlocker(
                        kind="APPLY.FAILED_OPERATION",
                        scope_kind="section",
                        scope_ref="1",
                        source_statute="2020/100",
                    ),
                ],
            ),
        )
        assert ctx.has_complete_chain is False
        assert ctx.chain_incomplete_reasons == ["APPLY.FAILED_OPERATION:1"]


class TestInvariantViolationsAttachment:
    """Section invariant violations are attached when available."""

    def test_violations_attached(self) -> None:
        violations = {
            "1": [
                {"kind": "content_mismatch", "section_label": "1",
                 "address_path": "section:1", "message": "test"},
            ],
        }
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
        )
        assert len(contexts["1"].invariant_violations) == 1
        assert contexts["1"].invariant_violations[0]["kind"] == "content_mismatch"

    def test_no_violations_gives_empty_list(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            section_invariant_violations=None,
        )
        assert contexts["1"].invariant_violations == []


class TestMatchLookups:
    """Alternative replay, oracle range, and cross-chapter matches."""

    def test_alternative_replay_match_attached(self) -> None:
        alt = cast(
            AlternativeReplayMatch,
            {
                "best_replay_section": "2",
                "best_replay_score": 0.92,
                "same_section_score": 0.4,
            },
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            alternative_replay_matches={"1": alt},
        )
        assert contexts["1"].alternative_replay_match == alt

    def test_oracle_range_match_attached(self) -> None:
        orm = cast(
            OracleRangeMatch,
            {
                "oracle_range_section": "chapter:1/section:1-3",
                "oracle_range_label": "1-3",
            },
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            oracle_range_matches={"1": orm},
        )
        assert contexts["1"].oracle_range_match == orm

    def test_cross_chapter_oracle_match_attached(self) -> None:
        ccm = cast(
            CrossChapterOracleMatch,
            {
                "oracle_section": "chapter:2/section:1",
                "oracle_section_score": 0.97,
                "same_section_score": 0.1,
            },
        )
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            cross_chapter_oracle_matches={"1": ccm},
        )
        assert contexts["1"].cross_chapter_oracle_match == ccm

    def test_missing_matches_give_none(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
        )
        ctx = contexts["1"]
        assert ctx.alternative_replay_match is None
        assert ctx.oracle_range_match is None
        assert ctx.cross_chapter_oracle_match is None


class TestStatuteWideFields:
    """Statute-wide fields propagated to every section context."""

    def test_html_noncommensurable_reason_is_scoped_to_matching_section(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
                {"section": "5", "diagnosis": "OK",
                 "oracle_text": "b", "replay_text": "b"},
            ],
            html_topology={
                "noncommensurable_reason": (
                    "duplicate_unscoped_oracle_labels:section:5,section:7"
                )
            },
        )
        assert contexts["1"].html_noncommensurable_reason == ""
        assert (
            contexts["5"].html_noncommensurable_reason
            == "duplicate_unscoped_oracle_labels:section:5,section:7"
        )

    def test_extraction_gap(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
        )
        assert contexts["1"].has_extraction_gap is True

    def test_no_extraction_gap(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            strict_fail_reasons=["some_other_reason"],
        )
        assert contexts["1"].has_extraction_gap is False

    def test_oracle_suspect_detail(self) -> None:
        detail = "2009/1710 eff 2010-01-01 > cutoff 2009-12-29"
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            oracle_suspect_detail=detail,
        )
        assert contexts["1"].oracle_suspect_detail == detail

    def test_timeline_true(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            timeline_addresses={"section:1", "chapter:1/section:2"},
        )
        assert contexts["1"].has_timeline_entry is True

    def test_timeline_false(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            timeline_addresses={"section:99"},
        )
        assert contexts["1"].has_timeline_entry is False

    def test_timeline_none_when_not_available(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            timeline_addresses=None,
        )
        assert contexts["1"].has_timeline_entry is None

    def test_timeline_suffix_matching(self) -> None:
        """Timeline address matching uses canonical suffix, not containment."""
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
            timeline_addresses={"chapter:1/section:1"},
        )
        assert contexts["1"].has_timeline_entry is True


class TestOracleContentAbsent:
    """oracle_content_absent flag propagation."""

    def test_oracle_content_absent_true(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "EXTRA",
                 "blame_source": "", "oracle_text": "",
                 "replay_text": "something",
                 "oracle_content_absent": True},
            ],
        )
        assert contexts["1"].oracle_content_absent is True

    def test_oracle_content_absent_false(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "a", "replay_text": "a"},
            ],
        )
        assert contexts["1"].oracle_content_absent is False


class TestSimilarityComputation:
    """Similarity is computed from oracle_text and replay_text."""

    def test_identical_texts_give_one(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "same text", "replay_text": "same text"},
            ],
        )
        assert contexts["1"].similarity == 1.0

    def test_empty_texts_give_one(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "OK",
                 "oracle_text": "", "replay_text": ""},
            ],
        )
        assert contexts["1"].similarity == 1.0

    def test_different_texts_give_less_than_one(self) -> None:
        contexts = build_section_contexts(
            section_results=[
                {"section": "1", "diagnosis": "UNKNOWN",
                 "oracle_text": "completely different content here",
                 "replay_text": "nothing alike whatsoever at all"},
            ],
        )
        assert contexts["1"].similarity < 1.0

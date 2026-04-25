"""Tests for A1 typed proof algebra.

Covers:
  1. Unit tests for each rule function (at least 1 per rule)
  2. Parity tests: legacy _build_section_claims() vs typed build_section_claims_typed()
  3. Resolver mechanics
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, cast

from lawvm.core.compile_result import SectionStrictVerdict
from lawvm.core.section_evidence_context import (
    AlternativeReplayMatch,
    CrossChapterOracleMatch,
    OracleRangeMatch,
    SectionEvidenceContext,
)
from lawvm.tools.evidence_claim_algebra import (
    ClaimSelector,
    Defeater,
    PositiveClaim,
    ProofTier,
    resolve,
)
from lawvm.tools.evidence_claims import (
    _build_section_claims,
    build_section_claims_typed,
)
from lawvm.tools.evidence_section_rules import (
    FALLBACK_DEFEATER_RULES,
    FINAL_FALLBACK_RULES,
    PREEMPTIVE_POSITIVE_RULES,
    PRIMARY_POSITIVE_RULES,
    PRIMARY_SINK_RULES,
    PROMOTION_POSITIVE_RULES,
    rule_baseline_alternative_match,
    rule_baseline_same_section_structure_drift,
    rule_blame_sparse_elaboration,
    rule_blame_only_repeal_without_payload,
    rule_blame_payload_prefers_replay,
    rule_blame_source_improved_or_equal,
    rule_cross_chapter_oracle_match_exact,
    rule_cross_chapter_oracle_match_unresolved,
    rule_deterministic_sparse_oracle_stale,
    rule_duplicate_unscoped_oracle_labels_noncommensurable,
    rule_extra_empty_oracle_explicit_content_absent,
    rule_extra_empty_oracle_unverified_absence,
    rule_extraction_gap_defeater,
    rule_first_drop_sparse_elaboration,
    rule_negligible_blame_drop_high_confidence,
    rule_negligible_blame_drop_low_confidence,
    rule_no_blame_has_timeline,
    rule_no_blame_no_timeline,
    rule_oracle_stale_diagnosis,
    rule_oracle_temporal_impossibility,
    rule_preexisting_baseline_high_confidence,
    rule_preexisting_baseline_low_confidence,
    rule_replay_divergence_fallback,
    rule_same_chapter_alternative_match_exact,
    rule_same_chapter_alternative_match_unresolved,
    rule_same_chapter_oracle_range_drift,
    rule_section_recovery_barrier_defeater,
    rule_section_source_barrier_defeater,
    rule_timeline_invariant_violation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    *,
    section_label: str = "section:1",
    diagnosis: str = "UNKNOWN",
    blame_source: str = "2020/100",
    similarity: float = 0.5,
    oracle_text: str = "oracle",
    replay_text: str = "replay",
    oracle_content_absent: bool = False,
    bisect_support: Optional[Dict[str, Any]] = None,
    strict_verdict: SectionStrictVerdict | None = None,
    strict_payload_confidence: str = "unknown",
    invariant_violations: Optional[List[Dict[str, Any]]] = None,
    alternative_replay_match: Optional[Dict[str, Any]] = None,
    oracle_range_match: Optional[Dict[str, Any]] = None,
    cross_chapter_oracle_match: Optional[Dict[str, Any]] = None,
    has_timeline_entry: Optional[bool] = None,
    html_noncommensurable_reason: str = "",
    has_extraction_gap: bool = False,
    oracle_suspect_detail: str = "",
    strict_fail_reasons: Optional[List[str]] = None,
) -> SectionEvidenceContext:
    return SectionEvidenceContext(
        section_label=section_label,
        diagnosis=diagnosis,
        blame_source=blame_source,
        similarity=similarity,
        oracle_text=oracle_text,
        replay_text=replay_text,
        oracle_content_absent=oracle_content_absent,
        bisect_support=bisect_support or {},
        strict_verdict=strict_verdict,
        strict_payload_confidence=strict_payload_confidence,
        invariant_violations=invariant_violations or [],
        alternative_replay_match=cast(
            AlternativeReplayMatch | None, alternative_replay_match
        ),
        oracle_range_match=cast(OracleRangeMatch | None, oracle_range_match),
        cross_chapter_oracle_match=cast(
            CrossChapterOracleMatch | None, cross_chapter_oracle_match
        ),
        has_timeline_entry=has_timeline_entry,
        html_noncommensurable_reason=html_noncommensurable_reason,
        has_extraction_gap=has_extraction_gap,
        oracle_suspect_detail=oracle_suspect_detail,
        strict_fail_reasons=strict_fail_reasons or [],
    )


def _legacy_kwargs(
    section_results: List[Dict],
    **kwargs: Any,
) -> Dict[str, Any]:
    """Build kwargs suitable for both legacy and typed paths."""
    return {"section_results": section_results, **kwargs}


def _assert_parity(kwargs: Dict[str, Any]) -> None:
    """Assert typed path produces bit-identical output to legacy path."""
    legacy = _build_section_claims(**kwargs)
    typed = build_section_claims_typed(**kwargs)
    typed_rows = [r.to_legacy_row() for r in typed]
    assert typed_rows == legacy, (
        f"Parity failure.\n"
        f"Legacy: {json.dumps(legacy, indent=2, default=str)}\n"
        f"Typed:  {json.dumps(typed_rows, indent=2, default=str)}"
    )


# =========================================================================
# Part 1: Unit tests for each rule function
# =========================================================================


class TestPreemptiveRules:
    def test_oracle_stale_diagnosis_fires(self) -> None:
        ctx = _ctx(diagnosis="ORACLE_STALE")
        result = rule_oracle_stale_diagnosis(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result[0].kind == "oracle_section_stale"

    def test_oracle_stale_diagnosis_skips_non_oracle(self) -> None:
        ctx = _ctx(diagnosis="UNKNOWN")
        assert rule_oracle_stale_diagnosis(ctx) == ()

    def test_oracle_temporal_impossibility_fires(self) -> None:
        ctx = _ctx(
            diagnosis="UNKNOWN",
            oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff",
        )
        result = rule_oracle_temporal_impossibility(ctx)
        assert len(result) == 1
        assert result[0].kind == "oracle_temporal_impossibility"

    def test_oracle_temporal_impossibility_skips_no_suspect(self) -> None:
        ctx = _ctx(diagnosis="UNKNOWN")
        assert rule_oracle_temporal_impossibility(ctx) == ()

    def test_oracle_temporal_impossibility_skips_non_replay(self) -> None:
        ctx = _ctx(
            diagnosis="ORACLE_STALE",
            oracle_suspect_detail="some detail",
        )
        assert rule_oracle_temporal_impossibility(ctx) == ()


class TestPrimaryPositiveRules:
    def test_extra_empty_oracle_explicit_content_absent(self) -> None:
        ctx = _ctx(
            diagnosis="EXTRA",
            oracle_text="",
            oracle_content_absent=True,
        )
        result = rule_extra_empty_oracle_explicit_content_absent(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_extra_empty_oracle_skips_if_not_extra(self) -> None:
        ctx = _ctx(diagnosis="UNKNOWN", oracle_text="", oracle_content_absent=True)
        assert rule_extra_empty_oracle_explicit_content_absent(ctx) == ()

    def test_extra_empty_oracle_skips_if_has_oracle_text(self) -> None:
        ctx = _ctx(diagnosis="EXTRA", oracle_text="content", oracle_content_absent=True)
        assert rule_extra_empty_oracle_explicit_content_absent(ctx) == ()

    def test_extra_empty_oracle_skips_if_not_content_absent(self) -> None:
        ctx = _ctx(diagnosis="EXTRA", oracle_text="", oracle_content_absent=False)
        assert rule_extra_empty_oracle_explicit_content_absent(ctx) == ()

    def test_duplicate_unscoped_oracle_labels(self) -> None:
        ctx = _ctx(
            oracle_text="",
            html_noncommensurable_reason="duplicate_unscoped_oracle_labels:section:5",
        )
        result = rule_duplicate_unscoped_oracle_labels_noncommensurable(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE

    def test_duplicate_unscoped_skips_if_has_oracle_text(self) -> None:
        ctx = _ctx(
            oracle_text="content",
            html_noncommensurable_reason="duplicate_unscoped_oracle_labels:section:5",
        )
        assert rule_duplicate_unscoped_oracle_labels_noncommensurable(ctx) == ()

    def test_same_chapter_oracle_range_drift(self) -> None:
        ctx = _ctx(
            oracle_range_match={
                "oracle_range_section": "chapter:1/section:1-2",
                "oracle_range_label": "1-2",
            },
        )
        result = rule_same_chapter_oracle_range_drift(ctx)
        assert len(result) == 1
        assert result[0].kind == "same_chapter_oracle_range_drift"

    def test_same_chapter_oracle_range_drift_skips_empty(self) -> None:
        ctx = _ctx()
        assert rule_same_chapter_oracle_range_drift(ctx) == ()

    def test_cross_chapter_oracle_match_exact(self) -> None:
        ctx = _ctx(
            cross_chapter_oracle_match={
                "oracle_section": "chapter:2/section:1",
                "oracle_section_score": 0.97,
                "same_section_score": 0.1,
            },
        )
        result = rule_cross_chapter_oracle_match_exact(ctx)
        assert len(result) == 1
        assert result[0].kind == "address_relocation_cross_chapter_exact"

    def test_cross_chapter_oracle_match_exact_skips_low_score(self) -> None:
        ctx = _ctx(
            cross_chapter_oracle_match={
                "oracle_section": "chapter:2/section:1",
                "oracle_section_score": 0.85,
                "same_section_score": 0.1,
            },
        )
        assert rule_cross_chapter_oracle_match_exact(ctx) == ()

    def test_preexisting_baseline_high_confidence(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": True,
                "baseline_score": 0.97,
                "first_bad_source": "2019/50",
            },
        )
        result = rule_preexisting_baseline_high_confidence(ctx)
        assert len(result) == 1
        assert result[0].kind == "oracle_editorial_drift_baseline_witness"

    def test_preexisting_baseline_high_skips_low_score(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": True,
                "baseline_score": 0.5,
            },
        )
        assert rule_preexisting_baseline_high_confidence(ctx) == ()

    def test_negligible_blame_drop_high_confidence(self) -> None:
        # NOTE: _has_negligible_blame_drop_on_preexisting_residue requires
        # baseline_score <= 0.75, but this rule requires baseline >= 0.95.
        # These are logically contradictory, so this rule is dead code in
        # practice.  Test confirms it never fires by checking the precondition
        # path that maximizes its chance of firing.
        ctx = _ctx(
            blame_source="2020/100",
            bisect_support={
                "preexisting_before_any_drop": False,
                "baseline_score": 0.5,
                "first_bad_source": "2019/50",
                "blame_source": "2020/100",
                "blame_before_score": 0.45,
                "blame_after_score": 0.445,
            },
        )
        assert ctx.negligible_blame_drop_on_preexisting_residue
        result = rule_negligible_blame_drop_high_confidence(ctx)
        assert len(result) == 0  # baseline < 0.95

    def test_negligible_blame_drop_high_skips_if_preexisting(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": True,
                "baseline_score": 0.97,
            },
        )
        assert rule_negligible_blame_drop_high_confidence(ctx) == ()

    def test_blame_only_repeal_without_payload(self) -> None:
        ctx = _ctx(
            bisect_support={
                "blame_only_repeal_without_payload": True,
                "blame_compiled_actions_for_section": ["repeal"],
            },
        )
        result = rule_blame_only_repeal_without_payload(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_SOURCE_PATHOLOGY

    def test_blame_payload_prefers_replay(self) -> None:
        ctx = _ctx(
            bisect_support={
                "blame_payload_prefers_replay": True,
                "blame_payload_vs_replay_score": 0.95,
                "blame_payload_vs_oracle_score": 0.5,
            },
        )
        result = rule_blame_payload_prefers_replay(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_SOURCE_PATHOLOGY

    def test_deterministic_sparse_oracle_stale(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": False,
                "blame_payload_prefers_replay": False,
                "blame_only_repeal_without_payload": False,
                "first_drop_source": "2018/50",
                "blame_source": "2020/100",
                "worst_drops": [
                    {"source_id": "2018/50"},
                    {"source_id": "2020/100"},
                ],
                "first_drop_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "first_drop_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_count": 1,
                "first_drop_sparse_leftover_count": 0,
                "blame_sparse_leftover_count": 0,
                "first_drop_apply_helpers_for_section": [
                    "_apply_deterministic_subsection_op",
                ],
                "blame_apply_helpers_for_section": [
                    "_apply_deterministic_subsection_op",
                ],
            },
        )
        result = rule_deterministic_sparse_oracle_stale(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_same_chapter_alternative_match_exact(self) -> None:
        ctx = _ctx(
            alternative_replay_match={
                "best_replay_section": "chapter:1/section:2",
                "best_replay_score": 0.96,
                "same_section_score": 0.3,
            },
        )
        result = rule_same_chapter_alternative_match_exact(ctx)
        assert len(result) == 1
        assert result[0].kind == "address_relocation_same_chapter_exact"

    def test_no_blame_no_timeline(self) -> None:
        ctx = _ctx(blame_source="", has_timeline_entry=False)
        result = rule_no_blame_no_timeline(ctx)
        assert len(result) == 1
        assert result[0].kind == "oracle_editorial_drift_no_timeline"

    def test_no_blame_no_timeline_skips_if_has_blame(self) -> None:
        ctx = _ctx(blame_source="2020/100", has_timeline_entry=False)
        assert rule_no_blame_no_timeline(ctx) == ()

    def test_no_blame_no_timeline_skips_if_has_timeline(self) -> None:
        ctx = _ctx(blame_source="", has_timeline_entry=True)
        assert rule_no_blame_no_timeline(ctx) == ()


class TestPrimarySinkRules:
    def test_extra_empty_oracle_unverified_absence(self) -> None:
        ctx = _ctx(
            diagnosis="EXTRA",
            oracle_text="",
            oracle_content_absent=False,
        )
        result = rule_extra_empty_oracle_unverified_absence(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED

    def test_extra_empty_oracle_unverified_skips_if_content_absent(self) -> None:
        ctx = _ctx(
            diagnosis="EXTRA",
            oracle_text="",
            oracle_content_absent=True,
        )
        assert rule_extra_empty_oracle_unverified_absence(ctx) == ()

    def test_cross_chapter_oracle_match_unresolved(self) -> None:
        ctx = _ctx(
            cross_chapter_oracle_match={
                "oracle_section": "chapter:2/section:1",
                "oracle_section_score": 0.85,
                "same_section_score": 0.1,
            },
        )
        result = rule_cross_chapter_oracle_match_unresolved(ctx)
        assert len(result) == 1
        assert "cross_chapter_oracle_drift" in result[0].kind

    def test_cross_chapter_oracle_match_unresolved_skips_high(self) -> None:
        ctx = _ctx(
            cross_chapter_oracle_match={
                "oracle_section_score": 0.97,
            },
        )
        assert rule_cross_chapter_oracle_match_unresolved(ctx) == ()

    def test_preexisting_baseline_low_confidence(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": True,
                "baseline_score": 0.5,
                "first_bad_source": "2019/50",
            },
        )
        result = rule_preexisting_baseline_low_confidence(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED

    def test_negligible_blame_drop_low_confidence(self) -> None:
        ctx = _ctx(
            bisect_support={
                "preexisting_before_any_drop": False,
                "baseline_score": 0.5,
                "first_bad_source": "2019/50",
                "blame_source": "2020/100",
                "blame_before_score": 0.45,
                "blame_after_score": 0.445,
            },
        )
        result = rule_negligible_blame_drop_low_confidence(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED

    def test_baseline_same_section_structure_drift(self) -> None:
        ctx = _ctx(
            bisect_support={
                "baseline_unmatched_oracle_subsections": {
                    "count": 2,
                    "oracle_text_excerpts": ["a", "b"],
                    "max_best_replay_score": 0.3,
                },
            },
        )
        result = rule_baseline_same_section_structure_drift(ctx)
        assert len(result) == 1
        assert "same_section_structure_drift" in result[0].kind

    def test_blame_sparse_elaboration(self) -> None:
        ctx = _ctx(
            bisect_support={
                "blame_sparse_elaboration": True,
                "blame_elaboration_kinds": ["ELAB.A"],
                "blame_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_labels": ["a"],
                "blame_sparse_leftover_count": 0,
                "blame_apply_helpers_for_section": ["h"],
            },
        )
        result = rule_blame_sparse_elaboration(ctx)
        assert len(result) == 1
        assert "elaboration_ambiguity" in result[0].kind

    def test_first_drop_sparse_elaboration(self) -> None:
        ctx = _ctx(
            blame_source="2020/200",
            bisect_support={
                "first_drop_sparse_elaboration": True,
                "first_drop_source": "2019/50",
                "first_drop_elaboration_kinds": ["ELAB.B"],
                "first_drop_sparse_slot_binding_count": 1,
                "first_drop_sparse_slot_binding_labels": ["b"],
                "first_drop_sparse_leftover_count": 0,
                "first_drop_apply_helpers_for_section": ["h2"],
            },
        )
        result = rule_first_drop_sparse_elaboration(ctx)
        assert len(result) == 1

    def test_first_drop_sparse_skips_same_source(self) -> None:
        ctx = _ctx(
            blame_source="2020/100",
            bisect_support={
                "first_drop_sparse_elaboration": True,
                "first_drop_source": "2020/100",
            },
        )
        assert rule_first_drop_sparse_elaboration(ctx) == ()

    def test_blame_source_improved_or_equal(self) -> None:
        ctx = _ctx(
            bisect_support={
                "blame_source_improved_or_equal": True,
                "blame_before_score": 0.8,
                "blame_after_score": 0.85,
            },
        )
        result = rule_blame_source_improved_or_equal(ctx)
        assert len(result) == 1

    def test_baseline_alternative_match(self) -> None:
        ctx = _ctx(
            bisect_support={
                "baseline_alternative_replay_match": {
                    "best_replay_section": "chapter:1/section:3",
                    "best_replay_score": 0.88,
                    "same_section_score": 0.4,
                },
            },
        )
        result = rule_baseline_alternative_match(ctx)
        assert len(result) == 1
        assert "same_chapter_section_drift" in result[0].kind

    def test_same_chapter_alternative_match_unresolved(self) -> None:
        ctx = _ctx(
            alternative_replay_match={
                "best_replay_section": "chapter:1/section:2",
                "best_replay_score": 0.85,
                "same_section_score": 0.3,
            },
        )
        result = rule_same_chapter_alternative_match_unresolved(ctx)
        assert len(result) == 1
        assert "same_chapter_replay_drift" in result[0].kind

    def test_same_chapter_alternative_match_unresolved_skips_exact(self) -> None:
        ctx = _ctx(
            alternative_replay_match={
                "best_replay_score": 0.96,
            },
        )
        assert rule_same_chapter_alternative_match_unresolved(ctx) == ()

    def test_no_blame_has_timeline(self) -> None:
        ctx = _ctx(blame_source="", has_timeline_entry=True)
        result = rule_no_blame_has_timeline(ctx)
        assert len(result) == 1
        assert "baseline_residue" in result[0].kind

    def test_no_blame_has_timeline_none(self) -> None:
        """Timeline unknown (None) should still emit sink."""
        ctx = _ctx(blame_source="", has_timeline_entry=None)
        result = rule_no_blame_has_timeline(ctx)
        assert len(result) == 1

    def test_no_blame_has_timeline_skips_if_has_blame(self) -> None:
        ctx = _ctx(blame_source="2020/100", has_timeline_entry=True)
        assert rule_no_blame_has_timeline(ctx) == ()


class TestFallbackDefeaterRules:
    def test_extraction_gap_defeater(self) -> None:
        ctx = _ctx(
            has_extraction_gap=True,
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
        )
        result = rule_extraction_gap_defeater(ctx)
        assert len(result) == 1
        assert isinstance(result[0], Defeater)
        assert result[0].replacement_sink is not None
        assert "extraction_coverage_gap" in result[0].replacement_sink.kind

    def test_extraction_gap_defeater_skips(self) -> None:
        ctx = _ctx(has_extraction_gap=False)
        assert rule_extraction_gap_defeater(ctx) == ()

    def test_section_source_barrier_defeater(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="section:1",
            amendment_id="2020/100",
            status="source_incomplete",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
        )
        ctx = _ctx(strict_verdict=ssv)
        result = rule_section_source_barrier_defeater(ctx)
        assert len(result) == 1
        assert result[0].replacement_sink is not None

    def test_section_recovery_barrier_defeater(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="section:1",
            amendment_id="2020/200",
            status="strict_blocked_by_recovery",
            barrier_codes=("APPLY.UNCOVERED_BODY_RECOVERY",),
        )
        ctx = _ctx(strict_verdict=ssv)
        result = rule_section_recovery_barrier_defeater(ctx)
        assert len(result) == 1

    def test_recovery_barrier_skips_if_source_barrier_present(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="section:1",
            amendment_id="2020/100",
            status="source_incomplete",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE", "APPLY.UNCOVERED_BODY_RECOVERY"),
        )
        ctx = _ctx(strict_verdict=ssv)
        # Source barrier fires
        assert len(rule_section_source_barrier_defeater(ctx)) == 1
        # Recovery barrier does NOT fire when source barrier is present
        assert rule_section_recovery_barrier_defeater(ctx) == ()


class TestPromotionRules:
    def test_timeline_invariant_violation(self) -> None:
        ctx = _ctx(
            invariant_violations=[
                {
                    "kind": "version_gap",
                    "section_label": "1",
                    "address_path": "p",
                    "message": "test",
                }
            ],
        )
        result = rule_timeline_invariant_violation(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_REPLAY_BUG

    def test_timeline_invariant_violation_skips_empty(self) -> None:
        ctx = _ctx()
        assert rule_timeline_invariant_violation(ctx) == ()


class TestFinalFallbackRules:
    def test_replay_divergence_fallback(self) -> None:
        ctx = _ctx()
        result = rule_replay_divergence_fallback(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_REPLAY_BUG
        assert result[0].kind == "replay_divergence"
        assert "fallback_replay_attribution" in result[0].proof_tags

    def test_replay_divergence_fallback_with_alt(self) -> None:
        ctx = _ctx(
            alternative_replay_match={
                "best_replay_section": "chapter:1/section:2",
                "best_replay_score": 0.8,
                "same_section_score": 0.3,
            },
        )
        result = rule_replay_divergence_fallback(ctx)
        assert result[0].support.get("best_replay_section") == "chapter:1/section:2"


# =========================================================================
# Part 2: Resolver mechanics
# =========================================================================


class TestResolver:
    def test_preemptive_short_circuits(self) -> None:
        """Preemptive positive should short-circuit, skipping primary."""
        ctx = _ctx(diagnosis="ORACLE_STALE")
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        assert result.selected is not None
        assert result.selected.tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result.selected.kind == "oracle_section_stale"
        # Should have exactly 1 candidate (no primary rules evaluated)
        assert len(result.candidates) == 1

    def test_non_replay_non_oracle_produces_empty(self) -> None:
        """MATCH diagnosis should produce no candidates."""
        ctx = _ctx(diagnosis="MATCH")
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        assert len(result.candidates) == 0

    def test_fallback_defeated_by_extraction_gap(self) -> None:
        """When no primary fires, extraction gap should defeat fallback."""
        ctx = _ctx(
            has_extraction_gap=True,
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
        )
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        assert result.selected is not None
        assert result.selected.tier == ProofTier.UNRESOLVED
        assert "extraction_coverage_gap" in result.selected.kind
        assert len(result.suppressed_candidates) == 1

    def test_promotion_adds_replay_bug_alongside_primary(self) -> None:
        """Invariant violation should add PROVED_REPLAY_BUG alongside primary."""
        ctx = _ctx(
            bisect_support={
                "blame_source_improved_or_equal": True,
                "blame_before_score": 0.8,
                "blame_after_score": 0.85,
            },
            invariant_violations=[
                {"kind": "gap", "section_label": "1", "address_path": "p", "message": "x"}
            ],
        )
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        kinds = {c.kind for c in result.candidates}
        assert "timeline_invariant_violation" in kinds
        assert "UNRESOLVED.source_underdetermined.amendment_improves_section" in kinds

    def test_tier_sorting(self) -> None:
        """Candidates should be sorted by tier priority."""
        ctx = _ctx(
            oracle_range_match={
                "oracle_range_section": "chapter:1/section:1-2",
                "oracle_range_label": "1-2",
            },
            bisect_support={
                "blame_payload_prefers_replay": True,
                "blame_payload_vs_replay_score": 0.95,
                "blame_payload_vs_oracle_score": 0.5,
            },
        )
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        tiers = [c.tier for c in result.candidates]
        # PROVED_ORACLE_INCORRECT should come before PROVED_SOURCE_PATHOLOGY
        assert tiers[0] == ProofTier.PROVED_ORACLE_INCORRECT
        assert tiers[1] == ProofTier.PROVED_SOURCE_PATHOLOGY


class TestClaimSelector:
    def test_tag_match(self) -> None:
        claim = PositiveClaim(
            rule_id="test",
            tier=ProofTier.PROVED_REPLAY_BUG,
            kind="replay_divergence",
            inference_rule="test",
            observation_sources=(),
            support={},
            proof_tags=frozenset({"fallback_replay_attribution"}),
        )
        selector = ClaimSelector(tags=frozenset({"fallback_replay_attribution"}))
        assert selector.matches(claim)

    def test_tag_no_match(self) -> None:
        claim = PositiveClaim(
            rule_id="test",
            tier=ProofTier.PROVED_REPLAY_BUG,
            kind="replay_divergence",
            inference_rule="test",
            observation_sources=(),
            support={},
            proof_tags=frozenset({"other_tag"}),
        )
        selector = ClaimSelector(tags=frozenset({"fallback_replay_attribution"}))
        assert not selector.matches(claim)

    def test_empty_selector_matches_all(self) -> None:
        claim = PositiveClaim(
            rule_id="test",
            tier=ProofTier.PROVED_REPLAY_BUG,
            kind="anything",
            inference_rule="test",
            observation_sources=(),
            support={},
        )
        assert ClaimSelector().matches(claim)


# =========================================================================
# Part 3: Parity tests — legacy vs typed
# =========================================================================


class TestParity:
    """Each test runs both legacy and typed paths and asserts identical output."""

    def test_oracle_stale_diagnosis(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "ORACLE_STALE",
                        "blame_source": "",
                        "oracle_text": "old",
                        "replay_text": "new",
                    }
                ],
            )
        )

    def test_oracle_temporal_impossibility(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "oracle",
                        "replay_text": "replay",
                    }
                ],
                oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff",
            )
        )

    def test_extra_content_absent(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "EXTRA",
                        "blame_source": "",
                        "oracle_text": "",
                        "replay_text": "content",
                        "oracle_content_absent": True,
                    }
                ],
            )
        )

    def test_extra_unverified(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "EXTRA",
                        "blame_source": "",
                        "oracle_text": "",
                        "replay_text": "content",
                    }
                ],
            )
        )

    def test_match_diagnosis(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "MATCH",
                        "blame_source": "",
                        "oracle_text": "same",
                        "replay_text": "same",
                    }
                ],
            )
        )

    def test_preexisting_high_baseline(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "preexisting_before_any_drop": True,
                        "baseline_score": 0.97,
                        "first_bad_source": "2019/50",
                    }
                ],
            )
        )

    def test_preexisting_low_baseline(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "preexisting_before_any_drop": True,
                        "baseline_score": 0.5,
                        "first_bad_source": "2019/50",
                    }
                ],
            )
        )

    def test_negligible_blame_drop_high(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/200",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/200",
                        "preexisting_before_any_drop": False,
                        "baseline_score": 0.97,
                        "first_bad_source": "2019/50",
                        "blame_before_score": 0.9,
                        "blame_after_score": 0.895,
                    }
                ],
            )
        )

    def test_negligible_blame_drop_low(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/200",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/200",
                        "preexisting_before_any_drop": False,
                        "baseline_score": 0.5,
                        "first_bad_source": "2019/50",
                        "blame_before_score": 0.45,
                        "blame_after_score": 0.445,
                    }
                ],
            )
        )

    def test_fallback_replay_divergence(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "oracle text",
                        "replay_text": "replay text",
                    }
                ],
            )
        )

    def test_extraction_gap(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "oracle",
                        "replay_text": "replay",
                    }
                ],
                strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
            )
        )

    def test_source_barrier(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="section:1",
            amendment_id="2020/100",
            status="source_incomplete",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
        )
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_strict_verdicts={"section:1": ssv},
            )
        )

    def test_recovery_barrier(self) -> None:
        ssv = SectionStrictVerdict(
            section_label="section:1",
            amendment_id="2020/200",
            status="strict_blocked_by_recovery",
            barrier_codes=("APPLY.UNCOVERED_BODY_RECOVERY",),
        )
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/200",
                        "oracle_text": "oracle text",
                        "replay_text": "replay text",
                    }
                ],
                section_strict_verdicts={"section:1": ssv},
            )
        )

    def test_invariant_violation(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_invariant_violations={
                    "section:1": [
                        {
                            "kind": "gap",
                            "section_label": "1",
                            "address_path": "p",
                            "message": "x",
                        }
                    ]
                },
            )
        )

    def test_alternative_match_exact(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                alternative_replay_matches={
                    "section:1": {
                        "best_replay_section": "chapter:1/section:2",
                        "best_replay_score": 0.96,
                        "same_section_score": 0.3,
                    }
                },
            )
        )

    def test_alternative_match_unresolved(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                alternative_replay_matches={
                    "section:1": {
                        "best_replay_section": "chapter:1/section:2",
                        "best_replay_score": 0.85,
                        "same_section_score": 0.3,
                    }
                },
            )
        )

    def test_oracle_range_match(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                oracle_range_matches={
                    "section:1": {
                        "oracle_range_section": "chapter:1/section:1-2",
                        "oracle_range_label": "1-2",
                    }
                },
            )
        )

    def test_cross_chapter_exact(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "",
                        "replay_text": "r",
                    }
                ],
                cross_chapter_oracle_matches={
                    "section:1": {
                        "oracle_section": "chapter:2/section:1",
                        "oracle_section_score": 0.97,
                        "same_section_score": 0.1,
                    }
                },
            )
        )

    def test_cross_chapter_unresolved(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "",
                        "replay_text": "r",
                    }
                ],
                cross_chapter_oracle_matches={
                    "section:1": {
                        "oracle_section": "chapter:2/section:1",
                        "oracle_section_score": 0.85,
                        "same_section_score": 0.1,
                    }
                },
            )
        )

    def test_no_blame_no_timeline(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                timeline_addresses={"chapter:1/section:99"},
            )
        )

    def test_no_blame_has_timeline(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                timeline_addresses={"chapter:1/section:1"},
            )
        )

    def test_blame_sparse_elaboration(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_sparse_elaboration": True,
                        "blame_elaboration_kinds": ["ELAB.A"],
                        "blame_sparse_slot_binding_count": 2,
                        "blame_sparse_slot_binding_labels": ["a", "b"],
                        "blame_sparse_leftover_count": 0,
                        "blame_apply_helpers_for_section": ["h1"],
                    }
                ],
            )
        )

    def test_first_drop_sparse(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/200",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/200",
                        "first_drop_sparse_elaboration": True,
                        "first_drop_source": "2019/50",
                        "first_drop_elaboration_kinds": ["ELAB.B"],
                        "first_drop_sparse_slot_binding_count": 1,
                        "first_drop_sparse_slot_binding_labels": ["c"],
                        "first_drop_sparse_leftover_count": 0,
                        "first_drop_apply_helpers_for_section": ["h2"],
                    }
                ],
            )
        )

    def test_blame_source_improved(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_source_improved_or_equal": True,
                        "blame_before_score": 0.8,
                        "blame_after_score": 0.85,
                    }
                ],
            )
        )

    def test_baseline_alternative(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "baseline_alternative_replay_match": {
                            "best_replay_section": "chapter:1/section:3",
                            "best_replay_score": 0.88,
                            "same_section_score": 0.4,
                        },
                    }
                ],
            )
        )

    def test_baseline_structure_drift(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "baseline_unmatched_oracle_subsections": {
                            "count": 2,
                            "oracle_text_excerpts": ["a", "b"],
                            "max_best_replay_score": 0.3,
                        },
                    }
                ],
            )
        )

    def test_blame_repeal_without_payload(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_only_repeal_without_payload": True,
                        "blame_compiled_actions_for_section": ["repeal"],
                    }
                ],
            )
        )

    def test_blame_payload_prefers_replay(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "o",
                        "replay_text": "r",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_payload_prefers_replay": True,
                        "blame_payload_vs_replay_score": 0.95,
                        "blame_payload_vs_oracle_score": 0.5,
                    }
                ],
            )
        )

    def test_complex_multi_candidate(self) -> None:
        """Many rules firing at once -- parity must hold."""
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "oracle text",
                        "replay_text": "replay text",
                    }
                ],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "preexisting_before_any_drop": True,
                        "baseline_score": 0.5,
                        "first_bad_source": "2019/50",
                        "blame_source_improved_or_equal": True,
                        "blame_before_score": 0.4,
                        "blame_after_score": 0.45,
                        "baseline_unmatched_oracle_subsections": {
                            "count": 1,
                            "oracle_text_excerpts": ["x"],
                            "max_best_replay_score": 0.3,
                        },
                    }
                ],
                alternative_replay_matches={
                    "section:1": {
                        "best_replay_section": "chapter:1/section:2",
                        "best_replay_score": 0.80,
                        "same_section_score": 0.3,
                    }
                },
                section_invariant_violations={
                    "section:1": [
                        {
                            "kind": "gap",
                            "section_label": "1",
                            "address_path": "p",
                            "message": "x",
                        }
                    ]
                },
            )
        )

    def test_multi_section(self) -> None:
        """Multiple sections in one call — row order must match."""
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "ORACLE_STALE",
                        "blame_source": "",
                        "oracle_text": "a",
                        "replay_text": "b",
                    },
                    {
                        "section": "section:2",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "c",
                        "replay_text": "d",
                    },
                    {
                        "section": "section:3",
                        "diagnosis": "MATCH",
                        "blame_source": "",
                        "oracle_text": "e",
                        "replay_text": "e",
                    },
                ],
            )
        )

    def test_duplicate_unscoped_oracle_labels(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "",
                        "replay_text": "r",
                    }
                ],
                html_topology={
                    "noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
                    "missing_from_xml": [],
                    "extra_in_xml": [],
                },
            )
        )

    def test_duplicate_unscoped_oracle_labels_only_match_target_section(self) -> None:
        _assert_parity(
            _legacy_kwargs(
                [
                    {
                        "section": "section:1",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "",
                        "replay_text": "r1",
                    },
                    {
                        "section": "section:5",
                        "diagnosis": "UNKNOWN",
                        "blame_source": "2020/100",
                        "oracle_text": "",
                        "replay_text": "r5",
                    },
                ],
                html_topology={
                    "noncommensurable_reason": (
                        "duplicate_unscoped_oracle_labels:section:5"
                    ),
                    "missing_from_xml": [],
                    "extra_in_xml": [],
                },
            )
        )

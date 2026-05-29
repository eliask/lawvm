"""Tests for section-chain completeness certificate (attack #9 guard).

Covers:
  1. ChainCompletenessStatus dataclass properties
  2. compute_chain_completeness() — per-section assessment from compile artifacts
  3. Integration with evidence rules: rule_no_blame_no_timeline guarded by chain completeness
  4. Resolver-level integration: incomplete chain → UNRESOLVED not PROVED
  5. Evidence bundle wiring: chain_completeness key in build_evidence_bundle output
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional, cast

import pytest

from lawvm.core.chain_completeness import (
    BOUNDARY_VIOLATION_BLOCKERS,
    ChainCompletenessStatus,
    CompletenessBlocker,
    EXTRACTION_FALLBACK_BLOCKER,
    FAILED_OPERATION_BLOCKER,
    REJECTED_OPERATION_BLOCKER,
    MISSING_EFFECTIVE_DATE_BLOCKER,
    SOURCE_INCOMPLETE_BLOCKER,
    compute_chain_completeness,
)
from lawvm.core.compile_result import SectionStrictVerdict
from lawvm.core.section_evidence_context import SectionEvidenceContext
from lawvm.tools.evidence_claim_algebra import (
    ProofTier,
    resolve,
)
from lawvm.tools.evidence_section_rules import (
    FALLBACK_DEFEATER_RULES,
    FINAL_FALLBACK_RULES,
    PREEMPTIVE_POSITIVE_RULES,
    PRIMARY_POSITIVE_RULES,
    PRIMARY_SINK_RULES,
    PROMOTION_POSITIVE_RULES,
    rule_no_blame_no_timeline,
    rule_no_blame_no_timeline_chain_incomplete,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    *,
    section_label: str = "section:1",
    diagnosis: str = "UNKNOWN",
    blame_source: str = "",
    similarity: float = 0.5,
    oracle_text: str = "oracle",
    replay_text: str = "replay",
    has_timeline_entry: Optional[bool] = False,
    chain_completeness: Optional[ChainCompletenessStatus] = None,
    **kwargs: Any,
) -> SectionEvidenceContext:
    return SectionEvidenceContext(
        section_label=section_label,
        diagnosis=diagnosis,
        blame_source=blame_source,
        similarity=similarity,
        oracle_text=oracle_text,
        replay_text=replay_text,
        has_timeline_entry=has_timeline_entry,
        chain_completeness=chain_completeness,
        **kwargs,
    )


def _resolve(ctx: SectionEvidenceContext):
    """Run the full staged resolver."""
    return resolve(
        ctx,
        preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
        primary_positive_rules=PRIMARY_POSITIVE_RULES,
        primary_sink_rules=PRIMARY_SINK_RULES,
        fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
        promotion_positive_rules=PROMOTION_POSITIVE_RULES,
        final_fallback_rules=FINAL_FALLBACK_RULES,
    )


def _status_from_sources(
    *,
    section_label: str,
    is_complete: bool,
    missing_sources: list[str] | None = None,
    extraction_fallback_sources: list[str] | None = None,
    failed_op_sources: list[str] | None = None,
    unresolved_date_sources: list[str] | None = None,
) -> ChainCompletenessStatus:
    blockers: list[CompletenessBlocker] = []
    for source in missing_sources or []:
        blockers.append(
            CompletenessBlocker(
                kind=SOURCE_INCOMPLETE_BLOCKER,
                scope_kind="section",
                scope_ref=section_label,
                source_statute=source,
            )
        )
    for source in extraction_fallback_sources or []:
        blockers.append(
            CompletenessBlocker(
                kind=EXTRACTION_FALLBACK_BLOCKER,
                scope_kind="section",
                scope_ref=section_label,
                source_statute=source,
            )
        )
    for source in failed_op_sources or []:
        blockers.append(
            CompletenessBlocker(
                kind=FAILED_OPERATION_BLOCKER,
                scope_kind="section",
                scope_ref=section_label,
                source_statute=source,
            )
        )
    for source in unresolved_date_sources or []:
        blockers.append(
            CompletenessBlocker(
                kind=MISSING_EFFECTIVE_DATE_BLOCKER,
                scope_kind="section",
                scope_ref=section_label,
                source_statute=source,
            )
        )
    return ChainCompletenessStatus(section_label=section_label, is_complete=is_complete, blockers=blockers)


def _blocker_sources(status: ChainCompletenessStatus, kind: str) -> list[str]:
    return [blocker.source_statute or blocker.scope_ref for blocker in status.blockers if blocker.kind == kind]


# =========================================================================
# Part 1: ChainCompletenessStatus
# =========================================================================


class TestChainCompletenessStatus:
    def test_blocker_contract_rejects_malformed_records(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            CompletenessBlocker(
                kind=cast(Any, "PYTHON_ORDER_GUESS"),
                scope_kind="section",
                scope_ref="1",
            )

        with pytest.raises(ValueError, match="scope_ref"):
            CompletenessBlocker(
                kind=SOURCE_INCOMPLETE_BLOCKER,
                scope_kind="section",
                scope_ref="",
            )

        with pytest.raises(TypeError, match="blockers"):
            ChainCompletenessStatus(
                section_label="1",
                is_complete=False,
                blockers=cast(Any, ["not-a-blocker"]),
            )

    def test_complete_chain(self) -> None:
        status = ChainCompletenessStatus(
            section_label="1",
            is_complete=True,
        )
        assert status.is_complete is True
        assert status.incompleteness_reasons == []

    def test_incomplete_chain_requires_blocker(self) -> None:
        with pytest.raises(ValueError, match="contradicts the blocker ledger"):
            ChainCompletenessStatus(
                section_label="1",
                is_complete=False,
            )

    def test_missing_sources(self) -> None:
        status = _status_from_sources(
            section_label="1",
            is_complete=False,
            missing_sources=["2020/100", "2021/200"],
        )
        assert status.is_complete is False
        assert "APPLY.SOURCE_INCOMPLETE:2" in status.incompleteness_reasons

    def test_extraction_fallback(self) -> None:
        status = _status_from_sources(
            section_label="1",
            is_complete=False,
            extraction_fallback_sources=["2020/100"],
        )
        assert status.is_complete is False
        assert "PARSE.EXTRACTION_FALLBACK:1" in status.incompleteness_reasons

    def test_failed_ops(self) -> None:
        status = _status_from_sources(
            section_label="1",
            is_complete=False,
            failed_op_sources=["2020/100"],
        )
        assert status.is_complete is False
        assert "APPLY.FAILED_OPERATION:1" in status.incompleteness_reasons

    def test_rejected_ops(self) -> None:
        status = ChainCompletenessStatus(
            section_label="1",
            is_complete=False,
            blockers=[
                CompletenessBlocker(
                    kind=REJECTED_OPERATION_BLOCKER,
                    scope_kind="section",
                    scope_ref="1",
                    source_statute="2020/100",
                )
            ],
        )
        assert status.is_complete is False
        assert "ELAB.STRICT_REJECTED_OPERATION:1" in status.incompleteness_reasons

    def test_boundary_violation_ops(self) -> None:
        status = ChainCompletenessStatus(
            section_label="1",
            is_complete=False,
            blockers=[
                CompletenessBlocker(
                    kind="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
                    scope_kind="section",
                    scope_ref="1",
                    source_statute="2020/100",
                )
            ],
        )
        assert status.is_complete is False
        assert "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET:1" in status.incompleteness_reasons

    def test_unresolved_apply_boundary_blocks_chain_completeness(self) -> None:
        status = ChainCompletenessStatus(
            section_label="1",
            is_complete=False,
            blockers=[
                CompletenessBlocker(
                    kind="REPLAY_APPLY_BOUNDARY_UNRESOLVED",
                    scope_kind="section",
                    scope_ref="1",
                    source_statute="2020/100",
                )
            ],
        )
        assert status.is_complete is False
        assert "REPLAY_APPLY_BOUNDARY_UNRESOLVED:1" in status.incompleteness_reasons

    def test_unresolved_dates(self) -> None:
        status = _status_from_sources(
            section_label="1",
            is_complete=False,
            unresolved_date_sources=["statute_wide"],
        )
        assert status.is_complete is False
        assert "TIME.MISSING_EFFECTIVE_DATE:1" in status.incompleteness_reasons

    def test_multiple_reasons(self) -> None:
        status = _status_from_sources(
            section_label="1",
            is_complete=False,
            missing_sources=["a"],
            failed_op_sources=["b"],
        )
        reasons = status.incompleteness_reasons
        assert len(reasons) == 2
        assert "APPLY.SOURCE_INCOMPLETE:1" in reasons
        assert "APPLY.FAILED_OPERATION:1" in reasons

    def test_blockers_are_authoritative_with_compatibility_views(self) -> None:
        status = ChainCompletenessStatus(
            section_label="section:1",
            is_complete=False,
            blockers=[
                CompletenessBlocker(
                    kind="APPLY.SOURCE_INCOMPLETE",
                    scope_kind="section",
                    scope_ref="section:1",
                    source_statute="2020/100",
                ),
                CompletenessBlocker(
                    kind="TIME.MISSING_EFFECTIVE_DATE",
                    scope_kind="statute",
                    scope_ref="statute_wide",
                ),
            ],
        )
        assert _blocker_sources(status, SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert _blocker_sources(status, MISSING_EFFECTIVE_DATE_BLOCKER) == ["statute_wide"]
        assert len(status.blockers) == 2


# =========================================================================
# Part 2: compute_chain_completeness()
# =========================================================================


class TestComputeChainCompleteness:
    def test_complete_chain_no_issues(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is True
        assert result["section:2"].is_complete is True

    def test_source_incomplete_falls_back_to_statute_wide_when_no_touch_map(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is False
        assert result["section:2"].is_complete is False
        assert "statute_wide" in _blocker_sources(result["section:1"], SOURCE_INCOMPLETE_BLOCKER)

    def test_source_incomplete_only_poisons_touched_sections_when_touch_map_exists(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert _blocker_sources(result["section:1"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert result["section:1"].blockers == (
            CompletenessBlocker(
                kind="APPLY.SOURCE_INCOMPLETE",
                scope_kind="section",
                scope_ref="section:1",
                source_statute="2020/100",
            ),
        )
        assert result["section:2"].is_complete is True

    def test_source_incomplete_poisons_sections_under_neutral_broad_chapter_scope(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:3/section:22",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_unit_kind": "chapter",
                    "target_norm": "3",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert _blocker_sources(result["chapter:3/section:22"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert result["chapter:4/section:21"].is_complete is True

    def test_extraction_fallback_legacy_only_target_kind_rows_fall_back_to_statute_wide(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:3/section:22",
            ],
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_kind": "L",
                    "target_section": "3",
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["root_insert_supplement"],
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], EXTRACTION_FALLBACK_BLOCKER) == ["statute_wide"]
        assert _blocker_sources(result["chapter:3/section:22"], EXTRACTION_FALLBACK_BLOCKER) == ["statute_wide"]

    def test_rejected_operation_only_poisons_touched_sections_when_touch_map_exists(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["ELAB.STRICT_REJECTED_OPERATION"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert _blocker_sources(result["section:1"], REJECTED_OPERATION_BLOCKER) == ["2020/100"]
        assert result["section:2"].is_complete is True

    def test_boundary_violations_only_poison_touched_sections_when_touch_map_exists(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["REPLAY_APPLY_BOUNDARY_UNRESOLVED"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert _blocker_sources(result["section:1"], "REPLAY_APPLY_BOUNDARY_UNRESOLVED") == ["2020/100"]
        assert result["section:2"].is_complete is True

    def test_boundary_violations_fall_back_to_statute_wide_when_no_touch_map(self) -> None:
        for kind in BOUNDARY_VIOLATION_BLOCKERS:
            result = compute_chain_completeness(
                section_labels=["section:1", "section:2"],
                strict_fail_reasons=[kind],
                failed_ops=[],
                compiled_ops=[],
            )
            assert result["section:1"].is_complete is False
            assert result["section:2"].is_complete is False
            assert _blocker_sources(result["section:1"], kind) == ["statute_wide"]

    def test_source_incomplete_uses_top_level_neutral_scope_from_compiled_ops(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:3/section:22",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target_unit_kind": "chapter",
                    "target_norm": "3",
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert _blocker_sources(result["chapter:3/section:22"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]
        assert result["chapter:4/section:21"].is_complete is True

    def test_source_incomplete_prefers_neutral_scope_fields_over_legacy_target_kind(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target_unit_kind": "section",
                    "target_norm": "21",
                    "target_chapter": "4",
                    "target_kind": "L",
                    "target_section": "3",
                },
            ],
        )
        assert result["chapter:3/section:21"].is_complete is True
        assert _blocker_sources(result["chapter:4/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]

    def test_source_incomplete_ignores_legacy_top_level_chapter_alias(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target_unit_kind": "chapter",
                    "chapter": "3",
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]
        assert _blocker_sources(result["chapter:4/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]

    def test_source_incomplete_does_not_expand_broad_chapter_scope_from_target_section_surrogate(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target_unit_kind": "chapter",
                    "target_section": "3",
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]
        assert _blocker_sources(result["chapter:4/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]

    def test_source_incomplete_prefers_top_level_neutral_scope_over_nested_compat_bag(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target_unit_kind": "section",
                    "target_norm": "21",
                    "target_chapter": "4",
                    "target": {
                        "container": "chapter",
                        "chapter": "3",
                        "section": None,
                        "part": None,
                    },
                },
            ],
        )
        assert result["chapter:3/section:21"].is_complete is True
        assert _blocker_sources(result["chapter:4/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["2020/100"]

    def test_source_incomplete_ignores_nested_compat_scope_without_top_level_neutral_fields(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "target": {
                        "target_unit_kind": "chapter",
                        "container": "section",
                        "chapter": "3",
                        "section": None,
                        "part": None,
                    },
                },
            ],
        )
        assert result["chapter:3/section:21"].is_complete is False
        assert _blocker_sources(result["chapter:3/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]
        assert _blocker_sources(result["chapter:4/section:21"], SOURCE_INCOMPLETE_BLOCKER) == ["statute_wide"]

    def test_strict_source_incomplete_flag(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1"],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is False
        assert "statute_wide" in _blocker_sources(result["section:1"], SOURCE_INCOMPLETE_BLOCKER)

    def test_extraction_fallback_statute_wide(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1"],
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is False
        assert "statute_wide" in _blocker_sources(result["section:1"], EXTRACTION_FALLBACK_BLOCKER)

    def test_extraction_fallback_only_poisons_touched_sections_when_touch_map_exists(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert _blocker_sources(result["section:1"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]
        assert result["section:2"].is_complete is True

    def test_extraction_fallback_hint_on_broad_chapter_scope_poisons_descendants(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:3/section:22",
                "chapter:4/section:21",
            ],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_unit_kind": "chapter",
                    "target_norm": "3",
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["root_insert_supplement"],
                },
            ],
        )
        assert _blocker_sources(result["chapter:3/section:21"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]
        assert _blocker_sources(result["chapter:3/section:22"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]
        assert result["chapter:4/section:21"].is_complete is True

    def test_extraction_fallback_hint_uses_top_level_neutral_scope(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "part:I/chapter:3/section:21",
                "part:I/chapter:3/section:22",
                "part:II/chapter:3/section:21",
            ],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["root_insert_supplement"],
                    "target_unit_kind": "part",
                    "target_norm": "I",
                },
            ],
        )
        assert _blocker_sources(result["part:I/chapter:3/section:21"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]
        assert _blocker_sources(result["part:I/chapter:3/section:22"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]
        assert result["part:II/chapter:3/section:21"].is_complete is True

    def test_extraction_fallback_does_not_expand_broad_part_scope_from_target_section_surrogate(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "part:I/chapter:3/section:21",
                "part:II/chapter:3/section:21",
            ],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["root_insert_supplement"],
                    "target_unit_kind": "part",
                    "target_section": "II",
                },
            ],
        )
        assert result["part:I/chapter:3/section:21"].is_complete is True
        assert result["part:II/chapter:3/section:21"].is_complete is True

    def test_extraction_fallback_hint_prefers_neutral_part_scope_fields(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "part:I/chapter:3/section:21",
                "part:II/chapter:3/section:21",
            ],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[
                {
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["root_insert_supplement"],
                    "target_unit_kind": "part",
                    "target_norm": "II",
                    "target_kind": "O",
                    "target_section": "I",
                },
            ],
        )
        assert result["part:I/chapter:3/section:21"].is_complete is True
        assert _blocker_sources(result["part:II/chapter:3/section:21"], EXTRACTION_FALLBACK_BLOCKER) == ["2020/100"]

    def test_failed_op_targets_specific_section(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=[],
            failed_ops=[
                {"target_section": "section:1", "source_statute": "2020/100"},
            ],
            compiled_ops=[],
        )
        # section:1 has a failed op, section:2 does not
        assert result["section:1"].is_complete is False
        assert "2020/100" in _blocker_sources(result["section:1"], FAILED_OPERATION_BLOCKER)
        assert result["section:2"].is_complete is True

    def test_failed_op_with_chapter_qualified_section_poisons_only_that_section(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "chapter:3/section:21",
                "chapter:3/section:22",
            ],
            strict_fail_reasons=[],
            failed_ops=[
                {
                    "target_unit_kind": "section",
                    "target_section": "21",
                    "target_chapter": "3",
                    "source_statute": "2020/100",
                },
            ],
            compiled_ops=[],
        )
        assert _blocker_sources(result["chapter:3/section:21"], FAILED_OPERATION_BLOCKER) == ["2020/100"]
        assert result["chapter:3/section:22"].is_complete is True

    def test_missing_effective_date(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1"],
            strict_fail_reasons=["TIME.MISSING_EFFECTIVE_DATE"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is False
        assert "statute_wide" in _blocker_sources(result["section:1"], MISSING_EFFECTIVE_DATE_BLOCKER)

    def test_missing_effective_date_only_poisons_touched_sections_when_touch_map_exists(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=["TIME.MISSING_EFFECTIVE_DATE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert _blocker_sources(result["section:1"], MISSING_EFFECTIVE_DATE_BLOCKER) == ["2020/100"]
        assert result["section:2"].is_complete is True

    def test_missing_effective_date_on_broad_part_scope_poisons_descendant_sections(self) -> None:
        result = compute_chain_completeness(
            section_labels=[
                "part:I/chapter:3/section:21",
                "part:I/chapter:3/section:22",
                "part:II/chapter:3/section:21",
            ],
            strict_fail_reasons=["TIME.MISSING_EFFECTIVE_DATE"],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_unit_kind": "part",
                    "target_norm": "I",
                    "source_statute": "2020/100",
                },
            ],
        )
        assert _blocker_sources(result["part:I/chapter:3/section:21"], MISSING_EFFECTIVE_DATE_BLOCKER) == ["2020/100"]
        assert _blocker_sources(result["part:I/chapter:3/section:22"], MISSING_EFFECTIVE_DATE_BLOCKER) == ["2020/100"]
        assert result["part:II/chapter:3/section:21"].is_complete is True

    def test_statute_wide_blocker_uses_statute_scope(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1"],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].blockers == (
            CompletenessBlocker(
                kind="APPLY.SOURCE_INCOMPLETE",
                scope_kind="statute",
                scope_ref="statute_wide",
            ),
        )

    def test_extraction_fallback_from_compiled_op_hints(self) -> None:
        result = compute_chain_completeness(
            section_labels=["section:1", "section:2"],
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[
                {
                    "target_section": "section:1",
                    "source_statute": "2020/100",
                    "extraction_provenance_tags": ["extraction_fallback_heuristic"],
                },
            ],
        )
        assert result["section:1"].is_complete is False
        assert "2020/100" in _blocker_sources(result["section:1"], EXTRACTION_FALLBACK_BLOCKER)
        assert result["section:2"].is_complete is True

    def test_source_pathology_reason_alone_does_not_trigger_missing(self) -> None:
        """source-pathology-only strict reasons should NOT mark as source-incomplete."""
        result = compute_chain_completeness(
            section_labels=["section:1"],
            strict_fail_reasons=["SOURCE_PATHOLOGY_DETECTED"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result["section:1"].is_complete is True

    def test_empty_section_labels(self) -> None:
        result = compute_chain_completeness(
            section_labels=[],
            strict_fail_reasons=["APPLY.SOURCE_INCOMPLETE"],
            failed_ops=[],
            compiled_ops=[],
        )
        assert result == {}


# =========================================================================
# Part 3: Rule-level integration
# =========================================================================


class TestRuleNoBlameNoTimelineChainGuard:
    def test_complete_chain_fires_proved(self) -> None:
        """When chain is complete, rule_no_blame_no_timeline emits PROVED."""
        cc = ChainCompletenessStatus(section_label="section:1", is_complete=True)
        ctx = _ctx(chain_completeness=cc)
        result = rule_no_blame_no_timeline(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_none_chain_preserves_legacy_proved_behavior(self) -> None:
        """Absent chain completeness data should preserve legacy behavior."""
        ctx = _ctx(chain_completeness=None)
        result = rule_no_blame_no_timeline(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_incomplete_chain_blocks_proved(self) -> None:
        """When chain is incomplete, rule_no_blame_no_timeline does NOT fire."""
        cc = _status_from_sources(section_label="section:1", is_complete=False, missing_sources=["2020/100"])
        ctx = _ctx(chain_completeness=cc)
        result = rule_no_blame_no_timeline(ctx)
        assert result == ()

    def test_incomplete_chain_emits_unresolved_sink(self) -> None:
        """When chain is incomplete, the chain_incomplete sink fires."""
        cc = _status_from_sources(section_label="section:1", is_complete=False, missing_sources=["2020/100"])
        ctx = _ctx(chain_completeness=cc)
        result = rule_no_blame_no_timeline_chain_incomplete(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED
        assert "chain_incomplete" in result[0].kind

    def test_complete_chain_does_not_emit_sink(self) -> None:
        """When chain is complete, the chain_incomplete sink does NOT fire."""
        cc = ChainCompletenessStatus(section_label="section:1", is_complete=True)
        ctx = _ctx(chain_completeness=cc)
        result = rule_no_blame_no_timeline_chain_incomplete(ctx)
        assert result == ()

    def test_sink_includes_reasons(self) -> None:
        """The UNRESOLVED sink includes incompleteness reasons in support."""
        cc = _status_from_sources(
            section_label="section:1",
            is_complete=False,
            failed_op_sources=["2020/100"],
            extraction_fallback_sources=["statute_wide"],
        )
        ctx = _ctx(chain_completeness=cc)
        result = rule_no_blame_no_timeline_chain_incomplete(ctx)
        assert len(result) == 1
        reasons = result[0].support["chain_incomplete_reasons"]
        assert any("PARSE.EXTRACTION_FALLBACK" in r for r in reasons)
        assert any("APPLY.FAILED_OPERATION" in r for r in reasons)


# =========================================================================
# Part 4: Resolver integration
# =========================================================================


class TestResolverChainCompleteness:
    def test_complete_chain_resolves_to_proved(self) -> None:
        """Full resolver: complete chain → PROVED_ORACLE_INCORRECT."""
        cc = ChainCompletenessStatus(section_label="section:1", is_complete=True)
        ctx = _ctx(chain_completeness=cc)
        result = _resolve(ctx)
        assert result.selected is not None
        assert result.selected.tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result.selected.kind == "oracle_editorial_drift_no_timeline"

    def test_incomplete_chain_resolves_to_unresolved(self) -> None:
        """Full resolver: incomplete chain → UNRESOLVED, not PROVED."""
        cc = _status_from_sources(section_label="section:1", is_complete=False, missing_sources=["2020/100"])
        ctx = _ctx(chain_completeness=cc)
        result = _resolve(ctx)
        assert result.selected is not None
        assert result.selected.tier == ProofTier.UNRESOLVED
        assert "chain_incomplete" in result.selected.kind

    def test_none_chain_resolves_like_legacy(self) -> None:
        """Full resolver: absent chain certificate preserves legacy proof."""
        ctx = _ctx(chain_completeness=None)
        result = _resolve(ctx)
        assert result.selected is not None
        assert result.selected.tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result.selected.kind == "oracle_editorial_drift_no_timeline"


# =========================================================================
# Part 5: Evidence bundle wiring
# =========================================================================


class TestChainCompletenessBundleWiring:
    """Verify chain_completeness is wired into the evidence bundle output."""

    def test_build_section_claims_typed_passes_chain_completeness(self) -> None:
        """build_section_claims_typed accepts chain_completeness_by_section
        and passes it to build_section_contexts, which populates
        SectionEvidenceContext.chain_completeness."""
        from lawvm.core.section_evidence_context import build_section_contexts

        cc_complete = ChainCompletenessStatus(
            section_label="section:1", is_complete=True
        )
        cc_incomplete = _status_from_sources(
            section_label="section:2",
            is_complete=False,
            missing_sources=["statute_wide"],
        )
        chain_map = {"section:1": cc_complete, "section:2": cc_incomplete}

        section_results = [
            {
                "section": "section:1",
                "diagnosis": "MATCH",
                "blame_source": "",
                "replay_text": "text",
                "oracle_text": "text",
            },
            {
                "section": "section:2",
                "diagnosis": "MATCH",
                "blame_source": "",
                "replay_text": "text",
                "oracle_text": "text",
            },
        ]
        contexts = build_section_contexts(
            section_results=section_results,
            chain_completeness_by_section=chain_map,
        )

        assert contexts["section:1"].chain_completeness is cc_complete
        assert contexts["section:1"].has_complete_chain is True
        assert contexts["section:2"].chain_completeness is cc_incomplete
        assert contexts["section:2"].has_complete_chain is False

    def test_chain_completeness_key_in_bundle_output(self) -> None:
        """build_section_claims_typed forwards chain_completeness_by_section
        to contexts; the result dict produced by build_evidence_bundle
        should include a 'chain_completeness' key."""
        # We test at the build_section_claims_typed layer (no replay needed).
        from lawvm.tools.evidence_claims import build_section_claims_typed

        cc = ChainCompletenessStatus(
            section_label="section:1", is_complete=True
        )
        section_results = [
            {
                "section": "section:1",
                "diagnosis": "MATCH",
                "blame_source": "",
                "replay_text": "1 § Some text",
                "oracle_text": "1 § Some text",
            },
        ]
        # Should not raise and should handle chain_completeness_by_section
        results = build_section_claims_typed(
            section_results=section_results,
            chain_completeness_by_section={"section:1": cc},
        )
        assert len(results) == 1

    def test_chain_completeness_summary_structure(self) -> None:
        """The chain_completeness summary dict should have the expected keys."""
        # Simulate what build_evidence_bundle constructs for _chain_completeness_summary
        section_labels = ["section:1", "section:2", "section:3"]
        cc_by_section = compute_chain_completeness(
            section_labels=section_labels,
            strict_fail_reasons=[],
            failed_ops=[],
            compiled_ops=[],
        )
        # Simulate the summary construction from build_evidence_bundle
        chain_length = 5
        source_available = 5
        summary = {
            "chain_length": chain_length,
            "source_available": source_available,
            "source_missing_count": max(0, chain_length - source_available),
            "is_complete": source_available == chain_length and chain_length > 0,
            "section_complete_count": sum(
                1 for s in cc_by_section.values() if s.is_complete
            ),
            "section_incomplete_count": sum(
                1 for s in cc_by_section.values() if not s.is_complete
            ),
        }
        assert summary["chain_length"] == 5
        assert summary["source_available"] == 5
        assert summary["source_missing_count"] == 0
        assert summary["is_complete"] is True
        assert summary["section_complete_count"] == 3
        assert summary["section_incomplete_count"] == 0

    def test_build_evidence_bundle_includes_chain_completeness_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """build_evidence_bundle output dict includes a 'chain_completeness' key
        with the expected structure when section_results are present."""
        from lawvm.tools.classify_result import ClassifyResult
        from lawvm.tools.evidence import build_evidence_bundle

        monkeypatch.setattr(
            "lawvm.tools.evidence._classify_statute",
            lambda statute_id, mode="legal_pit", **_kw: ClassifyResult(
                sid=statute_id,
                title="Test statute",
                mode=mode,
                overall_score=1.0,
                section_score=1.0,
                section_results=[
                    {
                        "section": "section:1",
                        "diagnosis": "MATCH",
                        "blame_source": "",
                        "replay_text": "1 § Text.",
                        "oracle_text": "1 § Text.",
                    }
                ],
                source_pathologies=[],
                contingent_effective_sources=[],
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.replay_xml",
            lambda statute_id, mode="legal_pit", replay_meta_out=None, **_kw: (
                replay_meta_out.update(
                    {
                        "lineage": [
                            {"included": True, "effective_date": "1991-01-01"},
                            {"included": True, "effective_date": "1992-01-01"},
                            {"included": True, "effective_date": "1993-01-01"},
                        ]
                    }
                )
                if replay_meta_out is not None
                else None
            )
            or SimpleNamespace(
                source_adjudication=None,
                materialized_state=SimpleNamespace(ir=None),
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.compile_fi_facade_from_replay",
            lambda **_kw: SimpleNamespace(
                projection_rows=lambda: (),
                summary_projection=lambda: SimpleNamespace(
                    strict_fail_reasons=(),
                ),
                bundle=SimpleNamespace(structural_ops=()),
                source_pathology_rows=lambda: (),
                strict_profile_name="",
                finding_ledger=(),
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence._audit_html_one",
            lambda statute_id: SimpleNamespace(
                noncommensurable_reason="",
                missing_from_xml=[],
                extra_in_xml=[],
                html_error="",
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.get_ground_truth_tree", lambda statute_id: None
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence._corrigendum_support_for_amendments",
            lambda mids: [],
        )

        bundle = build_evidence_bundle("1991/827", mode="legal_pit")

        assert "chain_completeness" in bundle
        cc = bundle["chain_completeness"]
        assert cc is not None
        assert "chain_length" in cc
        assert "source_available" in cc
        assert "is_complete" in cc
        assert "section_complete_count" in cc
        assert "section_incomplete_count" in cc
        # With chain_length=3, source_available=3 → is_complete=True
        assert cc["chain_length"] == 3
        assert cc["source_available"] == 3
        assert cc["is_complete"] is True
        assert cc["section_complete_count"] == 1  # one section, all complete
        assert cc["section_incomplete_count"] == 0

    def test_build_evidence_bundle_preserves_section_strict_verdicts_without_flag_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from lawvm.tools.classify_result import ClassifyResult
        from lawvm.tools.evidence import build_evidence_bundle

        monkeypatch.setattr(
            "lawvm.tools.evidence._classify_statute",
            lambda statute_id, mode="legal_pit", **_kw: ClassifyResult(
                sid=statute_id,
                title="Test statute",
                mode=mode,
                overall_score=0.25,
                section_score=0.25,
                section_results=[
                    {
                        "section": "section:1",
                        "diagnosis": "REPLAY_BUG",
                        "blame_source": "2020/100",
                        "replay_text": "1 § Replay text.",
                        "oracle_text": "1 § Oracle text.",
                    }
                ],
                source_pathologies=[],
                contingent_effective_sources=[],
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.replay_xml",
            lambda statute_id, mode="legal_pit", replay_meta_out=None, **_kw: (
                replay_meta_out.update(
                    {
                        "lineage": [
                            {"included": False, "effective_date": ""},
                        ]
                    }
                )
                if replay_meta_out is not None
                else None
            )
            or SimpleNamespace(
                source_adjudication=None,
                materialized_state=SimpleNamespace(ir=None),
                timelines={},
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.compile_fi_facade_from_replay",
            lambda **_kw: SimpleNamespace(
                projection_rows=lambda: (),
                summary_projection=lambda: SimpleNamespace(
                    strict_fail_reasons=("APPLY.SOURCE_INCOMPLETE",),
                ),
                bundle=SimpleNamespace(structural_ops=()),
                source_pathology_rows=lambda: (),
                strict_profile_name="",
                finding_ledger=(),
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence._section_bisect_support",
            lambda statute_id, mode, section_results, **_kw: [
                {
                    "section": "section:1",
                    "blame_source": "2020/100",
                }
            ],
        )

        def fake_compute_section_strict_verdicts(profile, **kwargs):
            assert "source_completeness_flags" not in kwargs
            assert kwargs["section_blame"] == {"section:1": "2020/100"}
            return {
                "section:1": SectionStrictVerdict(
                    section_label="section:1",
                    amendment_id="2020/100",
                    barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
                    status="source_incomplete",
                )
            }

        monkeypatch.setattr(
            "lawvm.core.compile_result.compute_section_strict_verdicts",
            fake_compute_section_strict_verdicts,
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence._audit_html_one",
            lambda statute_id: SimpleNamespace(
                noncommensurable_reason="",
                missing_from_xml=[],
                extra_in_xml=[],
                html_error="",
            ),
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence.get_ground_truth_tree", lambda statute_id: None
        )
        monkeypatch.setattr(
            "lawvm.tools.evidence._corrigendum_support_for_amendments",
            lambda mids: [],
        )

        bundle = build_evidence_bundle("1991/827", mode="legal_pit", include_bisect=True)

        assert bundle["section_claims"][0]["strict_payload_confidence"] == "source_incomplete"

"""Tests for C1: Section-local strict lineage.

Verifies that per-section strict verdicts are correctly computed from
compile artifacts filtered by blame chain, and that they flow into
evidence section claims to demote from PROVED_REPLAY_BUG.
"""

from __future__ import annotations


from lawvm.core.compile_result import (
    CompileFailure,
    SectionStrictVerdict,
    compute_section_strict_verdicts,
)
from lawvm.core.phase_result import Finding
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.tools.evidence_claims import _build_section_claims
from lawvm.finland.strict_profile import default_finland_strict_profile


def _make_op(
    *,
    action: StructuralAction = StructuralAction.REPLACE,
    statute_id: str = "2020/100",
    target_section: str = "1",
    extraction_provenance_tags: list[str] | None = None,
) -> tuple[dict, LegalOperation]:
    """Create a compiled_op dict and a LegalOperation for the same op."""
    compiled = {
        "op_id": f"op_{statute_id}_{target_section}",
        "description": f"{action.value} {target_section}",
        "source_statute": statute_id,
        "target_kind": "section",
        "target_section": target_section,
        "target_chapter": "",
        "extraction_provenance_tags": list(extraction_provenance_tags or []),
    }
    lo = LegalOperation(
        op_id=f"op_{statute_id}_{target_section}",
        sequence=1,
        action=action,
        target=LegalAddress(path=(("section", target_section),)),
        source=OperationSource(statute_id=statute_id),
    )
    return compiled, lo


class TestSectionStrictVerdictType:
    """Basic type tests for SectionStrictVerdict."""

    def test_clean_verdict(self) -> None:
        v = SectionStrictVerdict(section_label="1", amendment_id="2020/100")
        assert v.is_strict_clean
        assert v.barrier_families == set()
        assert v.barrier_kinds == set()

    def test_verdict_with_barriers(self) -> None:
        v = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
            status="source_incomplete",
        )
        assert not v.is_strict_clean
        assert "source" in v.barrier_families
        assert "APPLY.SOURCE_INCOMPLETE" in v.barrier_kinds


class TestComputeSectionStrictVerdicts:
    """Test compute_section_strict_verdicts filters ops by blame chain."""

    def test_clean_section_no_barriers(self) -> None:
        """Section blamed on amendment with no strict issues → strict_clean."""
        compiled, lo = _make_op(statute_id="2020/100", target_section="1")
        profile = default_finland_strict_profile()
        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[],
            findings=[],
            section_blame={"1": "2020/100"},
        )
        assert "1" in verdicts
        assert verdicts["1"].is_strict_clean
        assert verdicts["1"].amendment_id == "2020/100"

    def test_failed_op_produces_barrier(self) -> None:
        """Section blamed on amendment with a failed op → not strict_clean."""
        compiled, lo = _make_op(statute_id="2020/100", target_section="5")
        failure = CompileFailure(
            source_statute="2020/100",
            description="failed",
            reason="test",
            target_unit_kind="section",
            target_section="5",
        )
        profile = default_finland_strict_profile()
        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[failure],
            findings=[],
            section_blame={"5": "2020/100"},
        )
        assert not verdicts["5"].is_strict_clean
        assert "APPLY.FAILED_OPERATION" in verdicts["5"].barrier_kinds

    def test_extraction_fallback_produces_barrier(self) -> None:
        """Section blamed on amendment with extraction_fallback hint → barrier."""
        compiled, lo = _make_op(
            statute_id="2020/100",
            target_section="3",
            extraction_provenance_tags=["extraction_fallback_heuristic"],
        )
        profile = default_finland_strict_profile()
        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[],
            findings=[],
            section_blame={"3": "2020/100"},
        )
        assert not verdicts["3"].is_strict_clean
        assert "PARSE.EXTRACTION_FALLBACK" in verdicts["3"].barrier_kinds

    def test_sections_isolated_from_each_other(self) -> None:
        """Different sections blamed on different amendments get independent verdicts."""
        compiled1, lo1 = _make_op(statute_id="2020/100", target_section="1")
        compiled2, lo2 = _make_op(
            statute_id="2021/200",
            target_section="2",
            extraction_provenance_tags=["extraction_fallback_heuristic"],
        )
        profile = default_finland_strict_profile()
        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled1, compiled2],
            canonical_ops=[lo1, lo2],
            failed_ops=[],
            findings=[],
            section_blame={"1": "2020/100", "2": "2021/200"},
        )
        assert verdicts["1"].is_strict_clean
        assert not verdicts["2"].is_strict_clean

    def test_adjudication_attributed_to_amendment(self) -> None:
        """Adjudication from blamed amendment produces barrier for that section."""
        compiled, lo = _make_op(statute_id="2020/100", target_section="7")
        finding = Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="replay_fold",
            detail={
                "message": "test pathology",
                "op_id": compiled["op_id"],
                "barrier_code": "APPLY.SOURCE_PATHOLOGY_DETECTED",
            },
            source_statute="2020/100",
            blocking=True,
        )
        profile = default_finland_strict_profile()
        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[],
            findings=[finding],
            section_blame={"7": "2020/100"},
        )
        assert not verdicts["7"].is_strict_clean
        assert "APPLY.SOURCE_PATHOLOGY_DETECTED" in verdicts["7"].barrier_kinds

    def test_section_does_not_inherit_other_section_barriers_from_same_amendment(self) -> None:
        compiled1, lo1 = _make_op(statute_id="2020/100", target_section="1")
        compiled2, lo2 = _make_op(
            statute_id="2020/100",
            target_section="2",
            extraction_provenance_tags=["extraction_fallback_heuristic"],
        )
        finding = Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="replay_fold",
            detail={"message": "section 2 pathology", "op_id": compiled2["op_id"]},
            source_statute="2020/100",
            blocking=False,
        )
        profile = default_finland_strict_profile()

        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled1, compiled2],
            canonical_ops=[lo1, lo2],
            failed_ops=[],
            findings=[finding],
            section_blame={"1": "2020/100", "2": "2020/100"},
        )

        assert verdicts["1"].is_strict_clean
        assert "PARSE.EXTRACTION_FALLBACK" not in verdicts["1"].barrier_kinds
        assert "APPLY.SOURCE_PATHOLOGY_DETECTED" not in verdicts["1"].barrier_kinds
        assert not verdicts["2"].is_strict_clean

    def test_section_does_not_inherit_other_amendment_evidence_from_same_section(self) -> None:
        """Evidence from a different amendment touching the same section must not leak in."""
        compiled_good, lo_good = _make_op(statute_id="2020/100", target_section="2")
        compiled_bad, lo_bad = _make_op(
            statute_id="2021/200",
            target_section="2",
            extraction_provenance_tags=["extraction_fallback_heuristic"],
        )
        failure_bad = CompileFailure(
            source_statute="2021/200",
            description="bad",
            reason="test",
            target_unit_kind="section",
            target_section="2",
        )
        profile = default_finland_strict_profile()

        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled_good, compiled_bad],
            canonical_ops=[lo_good, lo_bad],
            failed_ops=[failure_bad],
            findings=[],
            section_blame={"2": "2020/100"},
        )

        assert verdicts["2"].is_strict_clean
        assert "PARSE.EXTRACTION_FALLBACK" not in verdicts["2"].barrier_kinds
        assert "APPLY.FAILED_OPERATION" not in verdicts["2"].barrier_kinds

    def test_broad_scope_chapter_op_counts_toward_blamed_section(self) -> None:
        """Chapter/part-level ops should not disappear from section-local verdicts."""
        compiled = {
            "op_id": "op_2020_100_ch7",
            "description": "replace chapter 7",
            "source_statute": "2020/100",
            "target_unit_kind": "chapter",
            "target_norm": "7",
            "target_section": "7",
            "target_chapter": "",
            "extraction_provenance_tags": ["extraction_fallback_heuristic"],
        }
        lo = LegalOperation(
            op_id="op_2020_100_ch7",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "7"),)),
            source=OperationSource(statute_id="2020/100"),
        )
        profile = default_finland_strict_profile()

        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[],
            findings=[],
            section_blame={"chapter:7/section:12": "2020/100"},
        )

        assert not verdicts["chapter:7/section:12"].is_strict_clean
        assert "PARSE.EXTRACTION_FALLBACK" in verdicts["chapter:7/section:12"].barrier_kinds

    def test_broad_scope_finding_counts_toward_blamed_section(self) -> None:
        """Chapter/part/supplement-level findings should not disappear from section verdicts."""
        compiled, lo = _make_op(statute_id="2020/100", target_section="12")
        finding = Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage="replay_fold",
            detail={
                "message": "chapter-scope pathology",
                "target_unit_kind": "chapter",
                "target_chapter": "7",
                "barrier_code": "APPLY.SOURCE_PATHOLOGY_DETECTED",
            },
            source_statute="2020/100",
            blocking=True,
        )
        profile = default_finland_strict_profile()

        verdicts = compute_section_strict_verdicts(
            profile,
            compiled_ops=[compiled],
            canonical_ops=[lo],
            failed_ops=[],
            findings=[finding],
            section_blame={"12": "2020/100"},
        )

        assert not verdicts["12"].is_strict_clean
        assert "APPLY.SOURCE_PATHOLOGY_DETECTED" in verdicts["12"].barrier_kinds


class TestSectionStrictInEvidenceClaims:
    """Test that section_strict_verdicts demote PROVED_REPLAY_BUG in _build_section_claims."""

    def test_source_barrier_demotes_to_unresolved(self) -> None:
        """Section with source-family strict barrier → UNRESOLVED, not PROVED_REPLAY_BUG."""
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
            status="source_incomplete",
        )
        claims = _build_section_claims(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "oracle_text": "oracle",
                    "replay_text": "replay",
                },
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert len(claims) == 1
        assert claims[0]["selected_tier"] != "PROVED_REPLAY_BUG"
        assert "section_strict_lineage" in claims[0]["selected_kind"]

    def test_recovery_barrier_demotes_to_unresolved(self) -> None:
        """Section with recovery-family strict barrier → UNRESOLVED."""
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            barrier_codes=("APPLY.UNCOVERED_BODY_RECOVERY",),
            status="strict_blocked_by_recovery",
        )
        claims = _build_section_claims(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "oracle_text": "oracle",
                    "replay_text": "replay",
                },
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert len(claims) == 1
        assert claims[0]["selected_tier"] != "PROVED_REPLAY_BUG"
        assert "section_recovery_barriers" in claims[0]["selected_kind"]

    def test_clean_section_still_proves_replay_bug(self) -> None:
        """Section with clean strict verdict → PROVED_REPLAY_BUG still valid."""
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            status="strict_clean",
        )
        claims = _build_section_claims(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "oracle_text": "oracle",
                    "replay_text": "replay",
                },
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert len(claims) == 1
        assert claims[0]["selected_tier"] == "PROVED_REPLAY_BUG"

    def test_no_verdicts_is_backward_compatible(self) -> None:
        """When section_strict_verdicts is None, behavior is unchanged."""
        claims = _build_section_claims(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "oracle_text": "oracle",
                    "replay_text": "replay",
                },
            ],
            section_strict_verdicts=None,
        )
        assert len(claims) == 1
        assert claims[0]["selected_tier"] == "PROVED_REPLAY_BUG"

    def test_oracle_stale_not_affected_by_strict_barriers(self) -> None:
        """Oracle-stale diagnosis stays PROVED_ORACLE_INCORRECT regardless of strict barriers."""
        ssv = SectionStrictVerdict(
            section_label="1",
            amendment_id="2020/100",
            barrier_codes=("APPLY.SOURCE_INCOMPLETE",),
            status="source_incomplete",
        )
        claims = _build_section_claims(
            section_results=[
                {
                    "section": "1",
                    "diagnosis": "ORACLE_STALE",
                    "blame_source": "2020/100",
                    "oracle_text": "old",
                    "replay_text": "new",
                },
            ],
            section_strict_verdicts={"1": ssv},
        )
        assert claims[0]["selected_tier"] == "PROVED_ORACLE_INCORRECT"

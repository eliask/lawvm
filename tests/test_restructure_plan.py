"""Tests for the StructuralTransformPlan foundation (P8).

Covers:
1. RestructureSignal detection from amendment ops
2. StructuralTransformPlan construction and ordering
3. Subtree-aware body pairing for chapter INSERT ops
4. build_chapter_subtree_coverage helper
5. MOVE/RELABEL execution via execute_restructure_plan
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, cast

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.finland.restructure_plan import (
    ExecutedOp,
    _execute_relabel,
    move_skip_finding,
    RestructureSignal,
    relabel_skip_source_pathology_finding,
    StructuralTransformOp,
    StructuralTransformPlan,
    TransformOpKind,
    build_restructure_plan,
    detect_restructure_signals,
    execute_restructure_plan,
    relabel_skip_finding,
)
from lawvm.finland.body_pairing import (
    ClauseClaim,
    ObservedBodyUnit,
    assign_body_units_subtree_aware,
    build_chapter_subtree_coverage,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_op(
    op_type: Literal["REPLACE", "REPEAL", "INSERT", "RENUMBER"],
    kind: TargetKind,
    section: str,
    chapter: str = "",
) -> AmendmentOp:
    return AmendmentOp(
        op_type=op_type,
        target_kind=kind,
        target_section=section,
        target_chapter=chapter,
    )


def _build_plan(
    statute_id: str,
    amendment_id: str,
    *,
    ops: list[AmendmentOp],
    uncov_ratio: float,
    total_units: int,
    body_unit_ids_by_chapter: dict[tuple[str, str], list[str]] | None = None,
) -> StructuralTransformPlan | None:
    return build_restructure_plan(
        statute_id,
        amendment_id,
        ops=ops,
        uncov_ratio=uncov_ratio,
        total_units=total_units,
        body_unit_ids_by_chapter=body_unit_ids_by_chapter,
    )


def _make_section_unit(label: str, chapter: str = "", part: str = "") -> ObservedBodyUnit:
    return ObservedBodyUnit(
        unit_id=f"section:{chapter}/{label}" if chapter else f"section:{label}",
        kind="section",
        label=label,
        chapter_label=chapter,
        part_label=part,
    )


def _make_chapter_unit(label: str, part: str = "") -> ObservedBodyUnit:
    return ObservedBodyUnit(
        unit_id=f"chapter:{label}",
        kind="chapter",
        label=label,
        chapter_label="",
        part_label=part,
    )


def _make_chapter_insert_claim(
    chapter_label: str,
    statute: str = "1994/1280",
    part: str = "",
) -> ClauseClaim:
    return ClauseClaim(
        target_statute=statute,
        target_address=chapter_label,
        claim_kind="INSERT",
        chapter="",
        part=part,
    )


# ---------------------------------------------------------------------------
# 1. Signal detection
# ---------------------------------------------------------------------------


class TestDetectRestructureSignals:
    def test_no_signals_empty_ops(self) -> None:
        signals = detect_restructure_signals(ops=[], uncov_ratio=0.0, total_units=0)
        assert signals == ()

    def test_no_signals_low_uncovered(self) -> None:
        ops = [_make_op("INSERT", TargetKind.CHAPTER, "3")]
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.2, total_units=20)
        # Chapter insert but low uncov ratio: CHAPTER_INSERT signal only, no HIGH_UNCOVERED
        assert RestructureSignal.CHAPTER_INSERT in signals
        assert RestructureSignal.HIGH_UNCOVERED_BODY not in signals

    def test_chapter_insert_and_high_uncovered(self) -> None:
        ops = [_make_op("INSERT", TargetKind.CHAPTER, "3")]
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.7, total_units=20)
        assert RestructureSignal.CHAPTER_INSERT in signals
        assert RestructureSignal.HIGH_UNCOVERED_BODY in signals

    def test_part_insert_detected(self) -> None:
        ops = [_make_op("INSERT", TargetKind.PART, "II")]
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.0, total_units=0)
        assert RestructureSignal.PART_INSERT in signals

    def test_renumber_detected_as_relabel(self) -> None:
        ops = [_make_op("RENUMBER", TargetKind.SECTION, "5")]
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.0, total_units=0)
        assert RestructureSignal.RELABEL in signals

    def test_descendant_renumber_still_counts_as_relabel_signal(self) -> None:
        op = AmendmentOp(
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="32",
            target_paragraph=1,
        )
        signals = detect_restructure_signals(ops=[op], uncov_ratio=0.0, total_units=0)
        assert RestructureSignal.RELABEL in signals

    def test_high_uncovered_threshold_requires_enough_units(self) -> None:
        ops = [_make_op("INSERT", TargetKind.CHAPTER, "2")]
        # Only 5 total units — below threshold
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.9, total_units=5)
        assert RestructureSignal.HIGH_UNCOVERED_BODY not in signals

    def test_replace_and_repeal_only_no_signal(self) -> None:
        ops = [
            _make_op("REPLACE", TargetKind.SECTION, "3"),
            _make_op("REPEAL", TargetKind.SECTION, "5"),
        ]
        signals = detect_restructure_signals(ops=ops, uncov_ratio=0.1, total_units=10)
        assert signals == ()


# ---------------------------------------------------------------------------
# 2. Plan construction and op ordering
# ---------------------------------------------------------------------------


class TestBuildRestructurePlan:
    def test_returns_none_when_no_signals(self) -> None:
        ops = [_make_op("REPLACE", TargetKind.SECTION, "3")]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.1,
            total_units=10,
        )
        assert plan is None

    def test_builds_plan_on_chapter_insert_high_uncovered(self) -> None:
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
            _make_op("REPLACE", TargetKind.SECTION, "20", chapter="3"),
            _make_op("REPEAL", TargetKind.SECTION, "5"),
        ]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
        )
        assert plan is not None
        assert plan.statute_id == "1994/1280"
        assert plan.amendment_id == "2000/172"
        assert RestructureSignal.CHAPTER_INSERT in plan.signals
        assert RestructureSignal.HIGH_UNCOVERED_BODY in plan.signals
        assert plan.confidence > 0.0
        assert len(plan.ops) > 0

    def test_ops_ordered_correctly(self) -> None:
        """INSERT_SUBTREE ops must come before REPLACE_LEAF, which must come before REPEAL_NODE."""
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
            _make_op("REPEAL", TargetKind.SECTION, "5"),
            _make_op("REPLACE", TargetKind.SECTION, "20", chapter="3"),
        ]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
        )
        assert plan is not None
        ordered = plan.ops_ordered
        kinds = [op.kind for op in ordered]
        # Find positions
        insert_subtree_pos = next((i for i, k in enumerate(kinds) if k == TransformOpKind.INSERT_SUBTREE), None)
        replace_leaf_pos = next((i for i, k in enumerate(kinds) if k == TransformOpKind.REPLACE_LEAF), None)
        repeal_pos = next((i for i, k in enumerate(kinds) if k == TransformOpKind.REPEAL_NODE), None)
        # All must be present
        assert insert_subtree_pos is not None
        assert replace_leaf_pos is not None
        assert repeal_pos is not None
        # Ordering: insert < replace < repeal
        assert insert_subtree_pos < replace_leaf_pos
        assert replace_leaf_pos < repeal_pos

    def test_chapter_insert_carries_subtree_payload_claims(self) -> None:
        """Chapter INSERT op should carry body unit IDs of child sections."""
        ops = [_make_op("INSERT", TargetKind.CHAPTER, "3")]
        body_unit_ids_by_chapter = {
            ("", "3"): ["section:3/20", "section:3/21", "section:3/22"]
        }
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
            body_unit_ids_by_chapter=body_unit_ids_by_chapter,
        )
        assert plan is not None
        insert_ops = [op for op in plan.ops if op.kind == TransformOpKind.INSERT_SUBTREE]
        assert len(insert_ops) == 1
        insert_op = insert_ops[0]
        assert insert_op.target == "chapter:3"
        assert set(insert_op.payload_claim_ids) == {"section:3/20", "section:3/21", "section:3/22"}

    def test_chapter_insert_keeps_part_scope_and_distinct_same_label_payloads(self) -> None:
        ops = [
            AmendmentOp(
                op_type="INSERT",
                target_kind=TargetKind.CHAPTER,
                target_section="2",
                target_part="V",
            ),
        ]
        body_unit_ids_by_chapter = {
            ("4", "2"): ["section:2/10", "section:2/11"],
            ("5", "2"): ["section:2/30", "section:2/31"],
        }
        plan = _build_plan(
            "2017/320",
            "2018/301",
            ops=ops,
            uncov_ratio=0.9,
            total_units=20,
            body_unit_ids_by_chapter=body_unit_ids_by_chapter,
        )

        assert plan is not None
        insert_ops = [op for op in plan.ops if op.kind == TransformOpKind.INSERT_SUBTREE]
        assert insert_ops == [
            StructuralTransformOp(
                kind=TransformOpKind.INSERT_SUBTREE,
                target="part:5/chapter:2",
                payload_claim_ids=("section:2/30", "section:2/31"),
                notes=("chapter_insert_subtree:2_children",),
            )
        ]

    def test_relabel_ops_have_lower_execution_order_than_inserts(self) -> None:
        assert TransformOpKind.RELABEL.execution_order() < TransformOpKind.INSERT_SUBTREE.execution_order()

    def test_plan_has_unexecuted_ops_when_relabel_present(self) -> None:
        ops = [
            _make_op("RENUMBER", TargetKind.SECTION, "5"),
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
        ]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
        )
        assert plan is not None
        assert plan.has_unexecuted_ops is True

    def test_plan_skips_descendant_section_renumber_ops(self) -> None:
        op = AmendmentOp(
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="32",
            target_paragraph=1,
        )
        plan = _build_plan(
            "2006/395",
            "2022/1029",
            ops=[op],
            uncov_ratio=0.0,
            total_units=1,
        )
        assert plan is not None
        assert [candidate for candidate in plan.ops if candidate.kind == TransformOpKind.RELABEL] == []

    def test_plan_keeps_part_scope_for_section_relabel_targets(self) -> None:
        op = AmendmentOp(
            op_type="RENUMBER",
            target_kind=TargetKind.SECTION,
            target_section="5",
            target_chapter="2",
            target_part="III",
        )
        op.lo = LegalOperation(
            op_id="relabel-159",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "5"))),
            destination=LegalAddress(path=(("part", "IV"), ("chapter", "18"), ("section", "159"))),
            source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
        )

        plan = _build_plan(
            "2017/320",
            "2019/371",
            ops=[op],
            uncov_ratio=0.0,
            total_units=1,
        )

        assert plan is not None
        assert plan.ops == (
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:3/chapter:2/section:5",
                destination="part:4/chapter:18/section:159",
                notes=("from_amendment_op",),
            ),
        )

    def test_plan_keeps_part_scope_for_chapter_relabel_targets(self) -> None:
        op = AmendmentOp(
            op_type="RENUMBER",
            target_kind=TargetKind.CHAPTER,
            target_section="2",
            target_part="IV",
        )
        op.lo = LegalOperation(
            op_id="relabel-chapter-18",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("part", "III"), ("chapter", "2"))),
            destination=LegalAddress(path=(("part", "IV"), ("chapter", "18"))),
            source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
        )

        plan = _build_plan(
            "2017/320",
            "2019/371",
            ops=[op],
            uncov_ratio=0.0,
            total_units=1,
        )

        assert plan is not None
        assert plan.ops == (
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:3/chapter:2",
                destination="part:4/chapter:18",
                notes=("from_amendment_op",),
            ),
        )

    def test_plan_no_unexecuted_ops_without_relabel(self) -> None:
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
            _make_op("REPLACE", TargetKind.SECTION, "20"),
        ]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
        )
        assert plan is not None
        assert plan.has_unexecuted_ops is False

    def test_plan_to_dict_is_serializable(self) -> None:
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
            _make_op("REPEAL", TargetKind.SECTION, "5"),
        ]
        plan = _build_plan(
            "1994/1280",
            "2000/172",
            ops=ops,
            uncov_ratio=0.7,
            total_units=20,
        )
        assert plan is not None
        d = plan.to_dict()
        assert d["statute_id"] == "1994/1280"
        assert d["amendment_id"] == "2000/172"
        assert isinstance(d["signals"], list)
        assert isinstance(d["ops"], list)
        assert isinstance(d["confidence"], float)
        assert isinstance(d["has_unexecuted_ops"], bool)
        # Each op should be a dict
        for op_d in cast(list[dict[str, object]], d["ops"]):
            assert "kind" in op_d
            assert "target" in op_d

    def test_duplicate_section_replacements_coalesce_exact_plan_ops(self) -> None:
        """Repeated section-level REPLACE ops for one target should not duplicate plan entries."""
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "3"),
            _make_op("REPLACE", TargetKind.SECTION, "3", chapter="18"),
            _make_op("REPLACE", TargetKind.SECTION, "3", chapter="18"),
            _make_op("REPLACE", TargetKind.SECTION, "5", chapter="18"),
            _make_op("REPLACE", TargetKind.SECTION, "5", chapter="18"),
        ]
        plan = _build_plan(
            "1979/1062",
            "2004/330",
            ops=ops,
            uncov_ratio=0.65,
            total_units=80,
        )
        assert plan is not None
        replace_targets = [
            op.target
            for op in plan.ops
            if op.kind == TransformOpKind.REPLACE_LEAF
        ]
        counts = Counter(replace_targets)
        assert counts["chapter:18/section:3"] == 1
        assert counts["chapter:18/section:5"] == 1

    def test_duplicate_insert_targets_coalesce_exact_plan_ops(self) -> None:
        """Repeated section-level INSERT ops for one target should not duplicate plan entries."""
        ops = [
            _make_op("INSERT", TargetKind.CHAPTER, "15a"),
            _make_op("INSERT", TargetKind.SECTION, "4", chapter="18"),
            _make_op("INSERT", TargetKind.SECTION, "4", chapter="18"),
            _make_op("INSERT", TargetKind.SECTION, "5", chapter="18"),
            _make_op("INSERT", TargetKind.SECTION, "5", chapter="18"),
        ]
        plan = _build_plan(
            "1979/1062",
            "2004/330",
            ops=ops,
            uncov_ratio=0.65,
            total_units=80,
        )
        assert plan is not None
        insert_targets = [
            op.target
            for op in plan.ops
            if op.kind == TransformOpKind.REPLACE_LEAF
        ]
        counts = Counter(insert_targets)
        assert counts["chapter:18/section:4"] == 1
        assert counts["chapter:18/section:5"] == 1


# ---------------------------------------------------------------------------
# 3. Subtree-aware body pairing
# ---------------------------------------------------------------------------


class TestAssignBodyUnitsSubtreeAware:
    def test_plain_assignment_unchanged_when_no_chapter_inserts(self) -> None:
        """Without chapter INSERT claims, behaves identically to assign_body_units."""
        inventory = [
            _make_section_unit("3"),
            _make_section_unit("5"),
        ]
        claims = [
            ClauseClaim(target_statute="1994/1280", target_address="3", claim_kind="REPLACE"),
        ]
        assignments = assign_body_units_subtree_aware(inventory, claims, "1994/1280")
        statuses = {a.body_unit_id: a.status for a in assignments}
        assert statuses["section:3"] == "claimed_current"
        assert statuses["section:5"] == "unmatched"

    def test_chapter_insert_claims_child_sections_as_subtree(self) -> None:
        """Section under a chapter INSERT should be promoted from unmatched to claimed_current."""
        chapter_unit = _make_chapter_unit("3")
        sec20 = _make_section_unit("20", chapter="3")
        sec21 = _make_section_unit("21", chapter="3")
        sec99 = _make_section_unit("99")  # no chapter — should remain unmatched

        inventory = [chapter_unit, sec20, sec21, sec99]
        claims = [_make_chapter_insert_claim("3")]

        assignments = assign_body_units_subtree_aware(inventory, claims, "1994/1280")
        statuses = {a.body_unit_id: a.status for a in assignments}

        # Chapter itself should be claimed_current via the chapter INSERT claim
        assert statuses["chapter:3"] == "claimed_current"
        # Sections under chapter 3 → promoted to claimed_current via subtree rule
        assert statuses["section:3/20"] == "claimed_current"
        assert statuses["section:3/21"] == "claimed_current"
        # Section without chapter → still unmatched
        assert statuses["section:99"] == "unmatched"

    def test_foreign_statute_chapter_insert_not_adopted(self) -> None:
        """Chapter INSERT for a different statute must not claim child sections of current statute."""
        chapter_unit = _make_chapter_unit("3")
        sec20 = _make_section_unit("20", chapter="3")

        inventory = [chapter_unit, sec20]
        # Chapter INSERT for a DIFFERENT statute
        foreign_claim = ClauseClaim(
            target_statute="OTHER/LAW",
            target_address="3",
            claim_kind="INSERT",
            chapter="",
        )
        claims = [foreign_claim]
        assignments = assign_body_units_subtree_aware(inventory, claims, "1994/1280")
        statuses = {a.body_unit_id: a.status for a in assignments}
        # Chapter is claimed_foreign (matches a foreign claim)
        assert statuses["chapter:3"] == "claimed_foreign"
        # Section has no current-statute claim, should be unmatched (no subtree adoption)
        assert statuses["section:3/20"] == "unmatched"

    def test_explicitly_claimed_section_not_overwritten_by_subtree(self) -> None:
        """Individual-claim match takes priority; subtree does not overwrite it."""
        chapter_unit = _make_chapter_unit("3")
        sec20 = _make_section_unit("20", chapter="3")

        inventory = [chapter_unit, sec20]
        claims = [
            _make_chapter_insert_claim("3"),
            ClauseClaim(
                target_statute="1994/1280",
                target_address="20",
                claim_kind="REPLACE",
                chapter="3",
            ),
        ]
        assignments = assign_body_units_subtree_aware(inventory, claims, "1994/1280")
        # Section already claimed_current via individual claim — status should be claimed_current
        sec20_assign = next(a for a in assignments if a.body_unit_id == "section:3/20")
        assert sec20_assign.status == "claimed_current"
        # The individual REPLACE claim should be preserved, not overwritten by INSERT
        assert sec20_assign.claim is not None
        assert sec20_assign.claim.claim_kind == "REPLACE"


# ---------------------------------------------------------------------------
# 4. build_chapter_subtree_coverage
# ---------------------------------------------------------------------------


class TestBuildChapterSubtreeCoverage:
    def test_returns_empty_when_no_chapter_inserts(self) -> None:
        inventory = [_make_section_unit("3"), _make_section_unit("5")]
        claims = [ClauseClaim(target_statute="1994/1280", target_address="3", claim_kind="REPLACE")]
        result = build_chapter_subtree_coverage(inventory, claims, "1994/1280")
        assert result == {}

    def test_groups_child_sections_under_chapter_insert(self) -> None:
        chapter_unit = _make_chapter_unit("3")
        sec20 = _make_section_unit("20", chapter="3")
        sec21 = _make_section_unit("21", chapter="3")
        sec_top = _make_section_unit("1")  # no chapter

        inventory = [chapter_unit, sec20, sec21, sec_top]
        claims = [_make_chapter_insert_claim("3")]
        result = build_chapter_subtree_coverage(inventory, claims, "1994/1280")

        assert ("", "3") in result
        assert set(result[("", "3")]) == {"section:3/20", "section:3/21"}

    def test_only_current_statute_chapter_inserts_counted(self) -> None:
        chapter_unit = _make_chapter_unit("5")
        sec30 = _make_section_unit("30", chapter="5")

        inventory = [chapter_unit, sec30]
        # INSERT for different statute
        foreign_claim = ClauseClaim(
            target_statute="OTHER/LAW",
            target_address="5",
            claim_kind="INSERT",
            chapter="",
        )
        result = build_chapter_subtree_coverage(inventory, [foreign_claim], "1994/1280")
        assert result == {}

    def test_multiple_chapter_inserts(self) -> None:
        ch2 = _make_chapter_unit("2")
        ch5 = _make_chapter_unit("5")
        sec10 = _make_section_unit("10", chapter="2")
        sec11 = _make_section_unit("11", chapter="2")
        sec30 = _make_section_unit("30", chapter="5")

        inventory = [ch2, ch5, sec10, sec11, sec30]
        claims = [_make_chapter_insert_claim("2"), _make_chapter_insert_claim("5")]
        result = build_chapter_subtree_coverage(inventory, claims, "1994/1280")

        assert set(result[("", "2")]) == {"section:2/10", "section:2/11"}
        assert set(result[("", "5")]) == {"section:5/30"}

    def test_same_label_chapters_in_different_parts_stay_separate(self) -> None:
        ch4_2 = _make_chapter_unit("2", part="4")
        ch5_2 = _make_chapter_unit("2", part="5")
        sec4_10 = _make_section_unit("10", chapter="2", part="4")
        sec5_30 = _make_section_unit("30", chapter="2", part="5")

        inventory = [ch4_2, ch5_2, sec4_10, sec5_30]
        claims = [_make_chapter_insert_claim("2", part="5")]
        result = build_chapter_subtree_coverage(inventory, claims, "1994/1280")

        assert result == {
            ("5", "2"): ["section:2/30"],
        }

    def test_groups_all_child_sections_under_part_insert(self) -> None:
        p5 = ObservedBodyUnit(kind="part", unit_id="part:5", label="5")
        ch5_1 = _make_chapter_unit("1", part="5")
        ch5_2 = _make_chapter_unit("2", part="5")
        sec109 = _make_section_unit("109", chapter="1", part="5")
        sec115 = _make_section_unit("115", chapter="2", part="5")

        inventory = [p5, ch5_1, ch5_2, sec109, sec115]
        claims = [
            ClauseClaim(
                target_statute="2001/1226",
                target_address="5",
                claim_kind="INSERT",
                chapter="",
                part="",
            )
        ]

        result = build_chapter_subtree_coverage(inventory, claims, "2001/1226")

        assert result == {
            ("5", "1"): ["section:1/109"],
            ("5", "2"): ["section:2/115"],
        }


# ---------------------------------------------------------------------------
# 5. TransformOpKind ordering invariants
# ---------------------------------------------------------------------------


class TestTransformOpKindOrdering:
    def test_move_is_first(self) -> None:
        assert TransformOpKind.MOVE.execution_order() == 0

    def test_relabel_is_first(self) -> None:
        assert TransformOpKind.RELABEL.execution_order() == 0

    def test_insert_subtree_before_replace_leaf(self) -> None:
        assert TransformOpKind.INSERT_SUBTREE.execution_order() < TransformOpKind.REPLACE_LEAF.execution_order()

    def test_replace_leaf_before_repeal_node(self) -> None:
        assert TransformOpKind.REPLACE_LEAF.execution_order() < TransformOpKind.REPEAL_NODE.execution_order()

    def test_replace_subtree_between_insert_and_leaf(self) -> None:
        insert_order = TransformOpKind.INSERT_SUBTREE.execution_order()
        subtree_order = TransformOpKind.REPLACE_SUBTREE.execution_order()
        leaf_order = TransformOpKind.REPLACE_LEAF.execution_order()
        assert insert_order <= subtree_order <= leaf_order


# ---------------------------------------------------------------------------
# 6. Execution: RELABEL
# ---------------------------------------------------------------------------


def _make_body_with_chapters(*chapters: tuple[str, list[str]]) -> IRNode:
    """Build a body IRNode with chapters containing sections.

    Each chapter is (label, [section_labels]).
    """
    chapter_nodes = []
    for ch_label, sec_labels in chapters:
        sec_nodes = [
            IRNode(kind=IRNodeKind.SECTION, label=lbl, text=f"text of {lbl}")
            for lbl in sec_labels
        ]
        chapter_nodes.append(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label=ch_label,
                children=tuple(sec_nodes),
            )
        )
    return IRNode(kind=IRNodeKind.BODY, children=tuple(chapter_nodes))


def _make_plan(
    ops: list[StructuralTransformOp],
    statute_id: str = "test/1",
    amendment_id: str = "test/2",
) -> StructuralTransformPlan:
    return StructuralTransformPlan(
        statute_id=statute_id,
        amendment_id=amendment_id,
        signals=(RestructureSignal.RELABEL,),
        ops=tuple(ops),
        confidence=0.6,
    )


class TestExecuteRelabel:
    def test_relabel_chapter(self) -> None:
        """RELABEL chapter:3 -> chapter:3a in a tree with chapters 1,2,3,4."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),)),
                IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),)),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="3 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="5", text="text of 5"),
                        IRNode(kind=IRNodeKind.SECTION, label="6", text="text of 6"),
                    ),
                ),
                IRNode(kind=IRNodeKind.CHAPTER, label="4", children=(IRNode(kind=IRNodeKind.NUM, text="4 luku"),)),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="chapter:3",
                destination="chapter:3a",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is True
        # Chapter labels should now be 1, 2, 3a, 4
        chapter_labels = [c.label for c in new_tree.children if c.kind == IRNodeKind.CHAPTER]
        assert chapter_labels == ["1", "2", "3a", "4"]
        # Children of the relabeled chapter should be preserved
        ch3a = next(c for c in new_tree.children if c.label == "3a")
        sec_labels = [s.label for s in ch3a.children if s.kind == IRNodeKind.SECTION]
        assert sec_labels == ["5", "6"]
        num = next(child for child in ch3a.children if child.kind == IRNodeKind.NUM)
        assert num.text == "3a luku"

    def test_relabel_section(self) -> None:
        """RELABEL section:5 -> section:5a."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="3", text="text of 3"),
                        IRNode(kind=IRNodeKind.SECTION, label="4", text="text of 4"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="5",
                            children=(IRNode(kind=IRNodeKind.NUM, text="5 §"), IRNode(kind=IRNodeKind.CONTENT, text="text of 5")),
                        ),
                        IRNode(kind=IRNodeKind.SECTION, label="6", text="text of 6"),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="section:5",
                destination="section:5a",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is True
        ch = new_tree.children[0]
        sec_labels = [s.label for s in ch.children if s.kind == IRNodeKind.SECTION]
        assert sec_labels == ["3", "4", "5a", "6"]
        sec = next(s for s in ch.children if s.kind == IRNodeKind.SECTION and s.label == "5a")
        num = next(child for child in sec.children if child.kind == IRNodeKind.NUM)
        assert num.text == "5a §"

    def test_relabel_section_allows_pre_partification_frame(self) -> None:
        """Part-scoped relabels may resolve against a root-chapter pre-frame."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 §"), IRNode(kind=IRNodeKind.CONTENT, text="text of 1")),
                        ),
                        IRNode(kind=IRNodeKind.SECTION, label="2", text="text of 2"),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:2/chapter:4/section:1",
                destination="section:42",
                notes=("from_amendment_op",),
            ),
        ])

        new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is True
        chapter = next(child for child in new_tree.children if child.kind is IRNodeKind.CHAPTER and child.label == "4")
        section_labels = [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION]
        assert set(section_labels) == {"2", "42"}

    def test_relabel_section_reparents_loose_trailing_sibling_into_explicit_chapter(self) -> None:
        """Chapter-scoped relabel may recover one loose trailing section sibling."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="64", text="text of 64"),
                        IRNode(kind=IRNodeKind.SECTION, label="72", text="text of 72"),
                    ),
                ),
                IRNode(kind=IRNodeKind.SECTION, label="73", text="voimaantulo"),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="chapter:7/section:73",
                destination="chapter:7/section:61",
                notes=("from_amendment_op",),
            ),
        ])

        new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is True
        assert executed[0].note == "reparented loose trailing leaf and relabeled to 61"
        assert all(not (child.kind is IRNodeKind.SECTION and child.label == "73") for child in new_tree.children)
        chapter = next(child for child in new_tree.children if child.kind is IRNodeKind.CHAPTER and child.label == "7")
        section_labels = [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION]
        assert section_labels == ["61", "64", "72"]

    def test_relabel_section_allows_pre_part_relabel_frame(self) -> None:
        """Section relabels may target the amendment's post-part frame."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="iia",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="text of 1"),
                                IRNode(kind=IRNodeKind.SECTION, label="2", text="text of 2"),
                                IRNode(kind=IRNodeKind.SECTION, label="3", text="text of 3"),
                                IRNode(kind=IRNodeKind.SECTION, label="4", text="text of 4"),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="3",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="text of 1"),
                                IRNode(kind=IRNodeKind.SECTION, label="2", text="text of 2"),
                                IRNode(kind=IRNodeKind.SECTION, label="3", text="text of 3"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:3/chapter:1/section:4",
                destination="section:153",
                notes=("from_amendment_op",),
            ),
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:iia",
                destination="part:3",
                notes=("from_amendment_op",),
            ),
        ])

        new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 2
        assert executed[0].success is True
        assert executed[1].success is True
        part_3 = next(child for child in new_tree.children if child.kind is IRNodeKind.PART and child.label == "3")
        chapter = next(child for child in part_3.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        section_labels = [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION]
        assert "153" in section_labels

    def test_relabel_section_without_part_relabel_frame_still_skips(self) -> None:
        """Part-frame fallback must not guess without an owning part relabel."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="iia",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="4", text="text of 4"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:3/chapter:1/section:4",
                destination="section:153",
                notes=("from_amendment_op",),
            ),
        ])

        new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "target_not_found"
        part_iia = next(child for child in new_tree.children if child.kind is IRNodeKind.PART and child.label == "iia")
        chapter = next(child for child in part_iia.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        assert [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION] == ["4"]

    def test_relabel_section_prefers_pre_part_relabel_frame_over_live_same_label_match(self) -> None:
        """Owned post-part-frame targets must not hijack a live same-label section."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="iia",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="source-part text"),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="3",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="destination-part text"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        op = StructuralTransformOp(
            kind=TransformOpKind.RELABEL,
            target="part:3/chapter:1/section:1",
            destination="section:150",
            notes=("from_amendment_op",),
        )

        new_tree, executed = _execute_relabel(
            tree,
            op,
            part_relabel_sources={"3": "iia"},
            source_statute="2019/371",
        )

        assert executed.success is True
        part_iia = next(child for child in new_tree.children if child.kind is IRNodeKind.PART and child.label == "iia")
        part_3 = next(child for child in new_tree.children if child.kind is IRNodeKind.PART and child.label == "3")
        source_chapter = next(child for child in part_iia.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        shadow_chapter = next(child for child in part_3.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        assert [child.label for child in source_chapter.children if child.kind is IRNodeKind.SECTION] == ["150"]
        assert [child.label for child in shadow_chapter.children if child.kind is IRNodeKind.SECTION] == ["1"]

    def test_relabel_reports_consumed_prior_source_in_same_plan(self) -> None:
        """Later post-part-frame relabels must explain when an earlier relabel already consumed the source."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="iia",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="4", text="source text"),
                            ),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="3",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:iia/chapter:1/section:4",
                destination="section:139",
                notes=("from_amendment_op",),
            ),
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:3/chapter:1/section:4",
                destination="section:153",
                notes=("from_amendment_op",),
            ),
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:iia",
                destination="part:3",
                notes=("from_amendment_op",),
            ),
        ])

        new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 3
        assert executed[0].success is True
        assert executed[1].success is False
        assert executed[1].reason_code == "source_consumed_by_prior_relabel"
        assert executed[2].success is True
        part_3 = next(child for child in new_tree.children if child.kind is IRNodeKind.PART and child.label == "3")
        chapter = next(child for child in part_3.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        assert [child.label for child in chapter.children if child.kind is IRNodeKind.SECTION] == ["139"]

    def test_same_parent_relabel_group_executes_safe_found_subset_when_one_source_is_missing(self) -> None:
        """A missing recodification alias must not block the proved same-parent relabel chain."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.PART, label="3", text="old part 3"),
                IRNode(kind=IRNodeKind.PART, label="4", text="old part 4"),
                IRNode(kind=IRNodeKind.PART, label="5", text="old part 5"),
            ),
        )
        plan = _make_plan(
            [
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:iia",
                    destination="part:3",
                    notes=("from_amendment_op",),
                ),
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:3",
                    destination="part:4",
                    notes=("from_amendment_op",),
                ),
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:4",
                    destination="part:5",
                    notes=("from_amendment_op",),
                ),
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:5",
                    destination="part:6",
                    notes=("from_amendment_op",),
                ),
            ],
            amendment_id="2019/371",
        )

        new_tree, executed = execute_restructure_plan(plan, tree)

        by_target = {item.op.target: item for item in executed}
        assert by_target["part:iia"].success is False
        assert by_target["part:iia"].reason_code == "target_part_absent_in_pre_partification_frame"
        assert by_target["part:3"].success is True
        assert by_target["part:4"].success is True
        assert by_target["part:5"].success is True
        assert [child.label for child in new_tree.children if child.kind is IRNodeKind.PART] == ["4", "5", "6"]

    def test_2017_320_2019_371_single_relabel_resolves_pre_part_frame(self) -> None:
        """Live 2019/371 relabel lookup must resolve the owned pre-part source path."""
        from lxml import etree

        from lawvm.finland.frontend_compile import normalize_and_compile_ops
        from lawvm.finland.grafter import get_corpus
        from lawvm.tools.inspect_amendment import _working_johtolause
        from tests.corpus_pin_helpers import pinned_replay

        statute_id = "2017/320"
        source_id = "2019/371"
        corpus = get_corpus()
        xml_bytes = corpus.read_source(source_id)
        assert xml_bytes is not None

        before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
        _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
            statute_id,
            before_master.title,
            source_id,
            xml_bytes,
            "",
        )
        assert should_apply is True

        phase = normalize_and_compile_ops(
            johto,
            etree.fromstring(xml_bytes),
            before_master.replay_fold_state,
            source_id,
            source_title="",
            used_sec1_fallback=used_sec1_fallback,
            parent_id=statute_id,
            strict_profile=None,
        )
        plan = _build_plan(
            statute_id,
            source_id,
            ops=phase.output,
            uncov_ratio=0.0,
            total_units=1,
        )
        assert plan is not None
        op = next(candidate for candidate in plan.ops if candidate.target == "part:3/chapter:1/section:4")

        new_tree, executed = _execute_relabel(
            before_master.replay_fold_state.ir,
            op,
            part_relabel_sources={"3": "iia", "4": "3", "5": "4", "6": "5", "7": "6", "8": "7"},
            source_statute=source_id,
        )

        assert executed.success is True
        root = next((child for child in new_tree.children if child.kind is IRNodeKind.HCONTAINER), new_tree)
        part_iia = next(child for child in root.children if child.kind is IRNodeKind.PART and child.label == "iia")
        chapter_1 = next(child for child in part_iia.children if child.kind is IRNodeKind.CHAPTER and child.label == "1")
        section_labels = [child.label for child in chapter_1.children if child.kind is IRNodeKind.SECTION]
        assert "153" in section_labels

    def test_2017_320_2019_371_relabel_skip_explains_consumed_pre_part_source(self) -> None:
        """Legacy consumed-pre-part fixture is now resolved and should succeed."""
        from lxml import etree

        from lawvm.finland.frontend_compile import normalize_and_compile_ops
        from lawvm.finland.grafter import get_corpus
        from lawvm.tools.inspect_amendment import _working_johtolause
        from tests.corpus_pin_helpers import pinned_replay

        statute_id = "2017/320"
        source_id = "2019/371"
        corpus = get_corpus()
        xml_bytes = corpus.read_source(source_id)
        assert xml_bytes is not None

        before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
        _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
            statute_id,
            before_master.title,
            source_id,
            xml_bytes,
            "",
        )
        assert should_apply is True

        phase = normalize_and_compile_ops(
            johto,
            etree.fromstring(xml_bytes),
            before_master.replay_fold_state,
            source_id,
            source_title="",
            used_sec1_fallback=used_sec1_fallback,
            parent_id=statute_id,
            strict_profile=None,
        )
        plan = build_restructure_plan(
            statute_id,
            source_id,
            ops=phase.output,
            uncov_ratio=0.0,
            total_units=1,
        )
        assert plan is not None

        _new_tree, executed = execute_restructure_plan(plan, before_master.replay_fold_state.ir)
        target_exec = next(item for item in executed if item.op.target == "part:3/chapter:1/section:4")

        assert target_exec.success is True
        assert target_exec.reason_code == ""

    def test_2017_320_2019_371_relabel_skips_classify_sparse_target_families(self) -> None:
        """Live 2019/371 sparse misses should classify leaf-vs-container absence explicitly."""
        from lxml import etree

        from lawvm.finland.frontend_compile import normalize_and_compile_ops
        from lawvm.finland.grafter import get_corpus
        from lawvm.tools.inspect_amendment import _working_johtolause
        from tests.corpus_pin_helpers import pinned_replay

        statute_id = "2017/320"
        source_id = "2019/371"
        corpus = get_corpus()
        xml_bytes = corpus.read_source(source_id)
        assert xml_bytes is not None

        before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
        _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
            statute_id,
            before_master.title,
            source_id,
            xml_bytes,
            "",
        )
        assert should_apply is True

        phase = normalize_and_compile_ops(
            johto,
            etree.fromstring(xml_bytes),
            before_master.replay_fold_state,
            source_id,
            source_title="",
            used_sec1_fallback=used_sec1_fallback,
            parent_id=statute_id,
            strict_profile=None,
        )
        plan = build_restructure_plan(
            statute_id,
            source_id,
            ops=phase.output,
            uncov_ratio=0.0,
            total_units=1,
        )
        assert plan is not None

        _new_tree, executed = execute_restructure_plan(plan, before_master.replay_fold_state.ir)
        by_target = {item.op.target: item for item in executed}

        assert by_target["part:2/chapter:4/section:11"].reason_code == "target_leaf_absent_under_existing_parent"
        assert by_target["part:6/chapter:2/section:7"].reason_code == "target_leaf_absent_under_existing_parent"
        pathology = relabel_skip_source_pathology_finding(
            by_target["part:2/chapter:4/section:11"],
            source_statute=source_id,
        )
        assert pathology is not None
        assert pathology.detail["code"] == "RECODIFICATION_SOURCE_CHAIN_GAP"
        assert pathology.detail["target_label"] == "4 luku 11 §"

    def test_2017_320_2020_1256_vi_chapter_26_28_relabels_execute_in_part_vi(self) -> None:
        """2020/1256 chapter 26-28 renumbers should execute from Part VI, not stale Part V."""
        from lxml import etree

        from lawvm.finland.frontend_compile import normalize_and_compile_ops
        from lawvm.finland.grafter import get_corpus
        from lawvm.tools.inspect_amendment import _working_johtolause
        from tests.corpus_pin_helpers import pinned_replay

        statute_id = "2017/320"
        source_id = "2020/1256"
        corpus = get_corpus()
        xml_bytes = corpus.read_source(source_id)
        assert xml_bytes is not None

        before_master = pinned_replay(statute_id, mode="legal_pit", stop_before=source_id, quiet=True)
        _muutos_tree, johto, used_sec1_fallback, should_apply, _route_reason = _working_johtolause(
            statute_id,
            before_master.title,
            source_id,
            xml_bytes,
            "",
        )
        assert should_apply is True

        phase = normalize_and_compile_ops(
            johto,
            etree.fromstring(xml_bytes),
            before_master.replay_fold_state,
            source_id,
            source_title="",
            used_sec1_fallback=used_sec1_fallback,
            parent_id=statute_id,
            strict_profile=None,
        )
        plan = build_restructure_plan(
            statute_id,
            source_id,
            ops=phase.output,
            uncov_ratio=0.0,
            total_units=1,
        )
        assert plan is not None

        _new_tree, executed = execute_restructure_plan(plan, before_master.replay_fold_state.ir)
        by_target_dest = {(item.op.target, item.op.destination): item for item in executed}

        assert by_target_dest[("part:6/chapter:1", "chapter:26")].success is True
        assert by_target_dest[("part:6/chapter:2", "chapter:27")].success is True
        assert by_target_dest[("part:6/chapter:3", "chapter:28")].success is True
        assert ("part:5/chapter:1", "chapter:26") not in by_target_dest
        assert ("part:5/chapter:2", "chapter:27") not in by_target_dest
        assert ("part:5/chapter:3", "chapter:28") not in by_target_dest

    def test_relabel_missing_target_skips(self, caplog) -> None:
        """RELABEL for nonexistent target should not crash, return tree unchanged."""
        tree = _make_body_with_chapters(("1", ["1", "2"]))
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="chapter:99",
                destination="chapter:99a",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "target_not_found"
        assert "not found" in executed[0].note
        # Tree should be unchanged
        chapter_labels = [c.label for c in new_tree.children if c.kind == IRNodeKind.CHAPTER]
        assert chapter_labels == ["1"]

    def test_relabel_missing_target_log_includes_amendment_id(self, caplog) -> None:
        """Relabel-skip logs should identify the owning amendment."""
        tree = _make_body_with_chapters(("1", ["1", "2"]))
        plan = _make_plan(
            [
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="chapter:99",
                    destination="chapter:99a",
                    notes=("from_amendment_op",),
                ),
            ],
            amendment_id="2020/1256",
        )

        with caplog.at_level("WARNING"):
            _new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is False
        assert (
            "[2020/1256] RELABEL target not found: chapter:99 "
            "(reason=target_not_found"
        ) in caplog.text

    def test_relabel_consumed_source_log_includes_reason(self, caplog) -> None:
        """Consumed-source relabel conflicts should be explicit in warning logs."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 luku"),),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan(
            [
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:5/chapter:1",
                    destination="chapter:22",
                    notes=("from_amendment_op",),
                ),
                StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:5/chapter:1",
                    destination="chapter:26",
                    notes=("from_amendment_op",),
                ),
            ],
            amendment_id="2020/1256",
        )

        with caplog.at_level("WARNING"):
            _new_tree, executed = execute_restructure_plan(plan, tree)

        assert [item.success for item in executed] == [True, False]
        assert executed[1].reason_code == "source_consumed_by_prior_relabel"
        assert (
            "[2020/1256] RELABEL target not found: part:5/chapter:1 "
            "(reason=source_consumed_by_prior_relabel"
        ) in caplog.text

    def test_relabel_missing_leaf_under_existing_parent_reports_specific_reason(self) -> None:
        """Missing leaf under a live parent should not collapse to generic target_not_found."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="7",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="7 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="s1"),
                                IRNode(kind=IRNodeKind.SECTION, label="2", text="s2"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:2/chapter:7/section:11",
                destination="section:77",
                notes=("from_amendment_op",),
            ),
        ])

        _new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "target_leaf_absent_under_existing_parent"

    def test_relabel_missing_container_reports_specific_reason(self) -> None:
        """Missing container beneath a live ancestor should be classified explicitly."""
        tree = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="6",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                                IRNode(kind=IRNodeKind.SECTION, label="1", text="s1"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:6/chapter:2/section:7",
                destination="section:268",
                notes=("from_amendment_op",),
            ),
        ])

        _new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "target_container_absent"

    def test_relabel_missing_part_in_pre_partification_tree_marks_source_gap(self) -> None:
        tree = _make_body_with_chapters(("1", ["1"]), ("2", ["1"]))
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="part:iia",
                destination="part:3",
                notes=("from_amendment_op",),
            ),
        ])

        _new_tree, executed = execute_restructure_plan(plan, tree)

        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "target_part_absent_in_pre_partification_frame"

    def test_relabel_no_destination_skips(self) -> None:
        """RELABEL without destination is a no-op."""
        tree = _make_body_with_chapters(("1", ["1"]))
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="chapter:1",
                destination=None,
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "missing_destination"


class TestRelabelSkipFinding:
    def test_maps_missing_destination(self) -> None:
        finding = relabel_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="chapter:1",
                    destination=None,
                ),
                success=False,
                note="RELABEL op has no destination",
                reason_code="missing_destination",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.kind == "APPLY.RELABEL_SKIP"
        assert finding.detail.get("reason_code") == "missing_destination"
        assert finding.detail.get("target") == "chapter:1"

    def test_maps_group_parent_mismatch(self) -> None:
        finding = relabel_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="chapter:3/section:9",
                    destination="chapter:4/section:10",
                ),
                success=False,
                note="grouped relabel paths do not share one parent",
                reason_code="group_parent_mismatch",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.detail.get("reason_code") == "group_parent_mismatch"
        assert finding.detail.get("grouped") is True

    def test_successful_relabel_produces_no_skip_finding(self) -> None:
        finding = relabel_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="chapter:3",
                    destination="chapter:3a",
                ),
                success=True,
                note="relabeled to 3a",
            ),
            source_statute="2019/371",
        )
        assert finding is None

    def test_missing_reason_code_does_not_reconstruct_from_note(self) -> None:
        finding = relabel_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="chapter:1",
                    destination=None,
                ),
                success=False,
                note="RELABEL op has no destination",
                reason_code="",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.detail.get("reason_code") == "unknown"


class TestRelabelSkipSourcePathologyFinding:
    def test_maps_sparse_recodification_leaf_gap(self) -> None:
        finding = relabel_skip_source_pathology_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:2/chapter:7/section:11",
                    destination="section:77",
                    notes=("from_amendment_op",),
                ),
                success=False,
                note="target not found: part:2/chapter:7/section:11",
                reason_code="target_leaf_absent_under_existing_parent",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.kind == "ELAB.SOURCE_PATHOLOGY"
        assert finding.detail["code"] == "RECODIFICATION_SOURCE_CHAIN_GAP"
        assert finding.detail["target_unit_kind"] == "section"
        assert finding.detail["target_label"] == "7 luku 11 §"
        assert finding.detail["detail"] == {"diagnostic_reason": "target_leaf_absent_under_existing_parent"}

    def test_maps_missing_part_in_pre_partification_tree(self) -> None:
        finding = relabel_skip_source_pathology_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:iia",
                    destination="part:3",
                    notes=("from_amendment_op",),
                ),
                success=False,
                note="target not found: part:iia",
                reason_code="target_part_absent_in_pre_partification_frame",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.kind == "ELAB.SOURCE_PATHOLOGY"
        assert finding.detail["code"] == "RECODIFICATION_SOURCE_CHAIN_GAP"
        assert finding.detail["target_unit_kind"] == "part"
        assert finding.detail["target_label"] == "iia osa"
        assert finding.detail["detail"] == {
            "diagnostic_reason": "target_part_absent_in_pre_partification_frame"
        }

    def test_successful_relabel_has_no_source_pathology(self) -> None:
        finding = relabel_skip_source_pathology_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.RELABEL,
                    target="part:5/chapter:1/section:1",
                    destination="section:216",
                    notes=("from_amendment_op",),
                ),
                success=True,
                note="relabeled to 216",
            ),
            source_statute="2019/371",
        )
        assert finding is None


class TestMoveSkipFinding:
    def test_maps_missing_destination(self) -> None:
        finding = move_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.MOVE,
                    target="chapter:3/section:20",
                    destination=None,
                ),
                success=False,
                note="MOVE op has no destination",
                reason_code="missing_destination",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.kind == "APPLY.MOVE_SKIP"
        assert finding.detail.get("reason_code") == "missing_destination"

    def test_maps_destination_not_found(self) -> None:
        finding = move_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.MOVE,
                    target="chapter:3/section:20",
                    destination="chapter:99",
                ),
                success=False,
                note="destination not found: chapter:99",
                reason_code="destination_not_found",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.detail.get("reason_code") == "destination_not_found"
        assert finding.detail.get("destination") == "chapter:99"

    def test_maps_scoped_source_not_found_without_string_parsing(self) -> None:
        finding = move_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.MOVE,
                    target="chapter:3/section:20",
                    destination="chapter:5",
                ),
                success=False,
                note="source not found: chapter:3/section:20",
                reason_code="source_not_found",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.detail.get("reason_code") == "source_not_found"

    def test_successful_move_produces_no_skip_finding(self) -> None:
        finding = move_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.MOVE,
                    target="chapter:3/section:20",
                    destination="chapter:5",
                ),
                success=True,
                note="moved to chapter:5",
            ),
            source_statute="2019/371",
        )
        assert finding is None

    def test_missing_reason_code_does_not_reconstruct_from_note(self) -> None:
        finding = move_skip_finding(
            ExecutedOp(
                op=StructuralTransformOp(
                    kind=TransformOpKind.MOVE,
                    target="chapter:3/section:20",
                    destination=None,
                ),
                success=False,
                note="MOVE op has no destination",
                reason_code="",
            ),
            source_statute="2019/371",
        )
        assert finding is not None
        assert finding.detail.get("reason_code") == "unknown"


# ---------------------------------------------------------------------------
# 7. Execution: MOVE
# ---------------------------------------------------------------------------


class TestExecuteMove:
    def test_move_section_between_chapters(self) -> None:
        """MOVE section:20 from chapter:3 to chapter:5."""
        tree = _make_body_with_chapters(
            ("3", ["18", "19", "20"]),
            ("5", ["30", "31"]),
        )
        migration_ledger = MigrationLedger()
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.MOVE,
                target="chapter:3/section:20",
                destination="chapter:5",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree, migration_ledger=migration_ledger)
        assert len(executed) == 1
        assert executed[0].success is True
        # Section 20 should no longer be in chapter 3
        ch3 = next(c for c in new_tree.children if c.label == "3")
        ch3_sec_labels = [s.label for s in ch3.children if s.kind == IRNodeKind.SECTION]
        assert "20" not in ch3_sec_labels
        assert ch3_sec_labels == ["18", "19"]
        # Section 20 should be in chapter 5, sorted
        ch5 = next(c for c in new_tree.children if c.label == "5")
        ch5_sec_labels = [s.label for s in ch5.children if s.kind == IRNodeKind.SECTION]
        assert "20" in ch5_sec_labels
        # Should be sorted: 20, 30, 31
        assert ch5_sec_labels == ["20", "30", "31"]
        assert len(migration_ledger.events) == 1
        assert migration_ledger.events[0].kind == "move"
        assert migration_ledger.events[0].from_address.path == (("chapter", "3"), ("section", "20"))
        assert migration_ledger.events[0].to_address.path == (("chapter", "5"), ("section", "20"))

    def test_move_missing_source_skips(self) -> None:
        """MOVE with nonexistent source should not crash."""
        tree = _make_body_with_chapters(
            ("3", ["18", "19"]),
            ("5", ["30"]),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.MOVE,
                target="chapter:3/section:99",
                destination="chapter:5",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "source_not_found"
        assert "not found" in executed[0].note

    def test_move_missing_destination_skips(self) -> None:
        """MOVE with nonexistent destination should not crash."""
        tree = _make_body_with_chapters(
            ("3", ["18", "19"]),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.MOVE,
                target="chapter:3/section:18",
                destination="chapter:99",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "destination_not_found"

    def test_move_no_destination_skips(self) -> None:
        """MOVE without destination is a no-op."""
        tree = _make_body_with_chapters(("3", ["18"]))
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.MOVE,
                target="chapter:3/section:18",
                destination=None,
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "missing_destination"

    def test_move_scoped_missing_source_does_not_fallback_to_other_chapter(self) -> None:
        tree = _make_body_with_chapters(
            ("3", ["18", "19"]),
            ("4", ["20"]),
            ("5", ["30"]),
        )
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.MOVE,
                target="chapter:3/section:20",
                destination="chapter:5",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].success is False
        assert executed[0].reason_code == "source_not_found"

        ch4 = next(c for c in new_tree.children if c.label == "4")
        ch4_sec_labels = [s.label for s in ch4.children if s.kind == IRNodeKind.SECTION]
        assert ch4_sec_labels == ["20"]

        ch5 = next(c for c in new_tree.children if c.label == "5")
        ch5_sec_labels = [s.label for s in ch5.children if s.kind == IRNodeKind.SECTION]
        assert ch5_sec_labels == ["30"]


# ---------------------------------------------------------------------------
# 8. Execution ordering: RELABEL before INSERT_SUBTREE
# ---------------------------------------------------------------------------


class TestExecutionOrdering:
    def test_relabel_before_insert_subtree(self) -> None:
        """RELABEL ops must execute before INSERT_SUBTREE in ordered plan."""
        tree = _make_body_with_chapters(
            ("1", ["1"]),
            ("3", ["5"]),
        )
        plan = _make_plan([
            # INSERT_SUBTREE won't actually execute (skipped by executor),
            # but RELABEL must come first in ops_ordered.
            StructuralTransformOp(
                kind=TransformOpKind.INSERT_SUBTREE,
                target="chapter:4",
                notes=("chapter_insert_subtree",),
            ),
            StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target="chapter:3",
                destination="chapter:3a",
            ),
        ])
        # Verify ops_ordered puts RELABEL first
        ordered = plan.ops_ordered
        assert ordered[0].kind == TransformOpKind.RELABEL
        assert ordered[1].kind == TransformOpKind.INSERT_SUBTREE

        # Execute: only RELABEL should be in executed list
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 1
        assert executed[0].op.kind == TransformOpKind.RELABEL
        assert executed[0].success is True

    def test_non_executable_ops_skipped(self) -> None:
        """INSERT_SUBTREE, REPLACE_LEAF, REPEAL_NODE are not executed."""
        tree = _make_body_with_chapters(("1", ["1"]))
        plan = _make_plan([
            StructuralTransformOp(
                kind=TransformOpKind.INSERT_SUBTREE,
                target="chapter:2",
            ),
            StructuralTransformOp(
                kind=TransformOpKind.REPLACE_LEAF,
                target="section:1",
            ),
            StructuralTransformOp(
                kind=TransformOpKind.REPEAL_NODE,
                target="section:1",
            ),
        ])
        new_tree, executed = execute_restructure_plan(plan, tree)
        assert len(executed) == 0
        # Tree unchanged
        assert new_tree.children == tree.children

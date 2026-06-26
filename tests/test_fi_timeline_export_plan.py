from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.payload_elaboration import PayloadCompletenessWitness
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import OpType, AmendmentOp, ResolvedOp
from lawvm.finland.timeline_export_plan import (
    ParentSnapshotProof,
    TimelineExportMode,
    classify_timeline_export_plan,
)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _item(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(_content(text),),
    )


def _subsection(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=children)


def _section(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=children)


def test_timeline_export_plan_prefers_exact_descendant_for_fragmentary_item_group() -> None:
    op = AmendmentOp(
        op_id="replace-section-2-subsection-1-item-13",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="2",
        target_paragraph=1,
        target_item="13",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_section("2", _subsection("1", _item("13", "new item 13"))),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="2",
        target_chapter=None,
        target_address=LegalAddress(
            path=(("section", "2"), ("subsection", "1"), ("item", "13"))
        ),
        payload_completeness=PayloadCompletenessWitness(
            kind="fragmentary",
            reasons=("unassigned_sparse_payload_slots",),
            tail_policy="preserve_unstated_tail",
        ),
    )

    plan = classify_timeline_export_plan([rop], target_unit_kind="section")

    assert plan.mode is TimelineExportMode.EXACT_DESCENDANT_OPS
    assert plan.parent_snapshot_proof is ParentSnapshotProof.NONE
    assert not plan.authorizes_parent_snapshot
    assert plan.exact_descendant_targets == ("section:2/subsection:1/item:13",)


def test_timeline_export_plan_authorizes_parent_snapshot_for_complete_whole_section() -> None:
    op = AmendmentOp(
        op_id="replace-section-20",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="20",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_section("20", _subsection("1", _content("complete text"))),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="20",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "20"),)),
        payload_completeness=PayloadCompletenessWitness(
            kind="complete",
            reasons=("whole_section_payload",),
            tail_policy="replace_if_target_scope_requires",
        ),
    )

    plan = classify_timeline_export_plan([rop], target_unit_kind="section")

    assert plan.mode is TimelineExportMode.PARENT_SNAPSHOT_WITH_CHILD_SNAPSHOTS
    assert plan.parent_snapshot_proof is ParentSnapshotProof.COMPLETE_WHOLE_UNIT_SOURCE_PAYLOAD
    assert plan.authorizes_parent_snapshot


def test_timeline_export_plan_keeps_temporary_group_in_compat_lane() -> None:
    op = AmendmentOp(
        op_id="temporary-section-5",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="5",
        is_temporary=True,
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=_section("5", _subsection("1", _content("temporary text"))),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="5",
        target_chapter=None,
        target_address=LegalAddress(path=(("section", "5"),)),
        payload_completeness=PayloadCompletenessWitness(
            kind="complete",
            reasons=("temporary_whole_section_payload",),
            tail_policy="replace_if_target_scope_requires",
        ),
    )

    plan = classify_timeline_export_plan([rop], target_unit_kind="section")

    assert plan.mode is TimelineExportMode.TEMPORARY_OVERLAY_COMPAT
    assert plan.parent_snapshot_proof is ParentSnapshotProof.TEMPORARY_OVERLAY_PARENT

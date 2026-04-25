"""Unit tests for lawvm.finland.constraints — op constraint predicates."""

import lxml.etree as etree
from typing import Literal

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.constraints import (
    _FilterCtx,
    _c_internal_list_update_not_whole_section_replace,
    _c_language_variant,
    _c_language_variant_plain_replace_shadowed_by_sparse_item_payload,
    _c_language_variant_replace_shadowed_by_sparse_insert,
    _c_no_heading_payload,
    _c_no_source_payload,
    _c_phantom_subsection,
    _c_replace_when_insert_same_paragraph,
    _c_whole_section_subsumes_children,
    _filter_ops_by_constraints,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.ops import FailedOp
from lawvm.finland.payload_normalize import (
    SparsePayloadSlotBinding,
    SubsectionSlotAssignmentResult,
    SubsectionSlotMap,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _empty_tree() -> "etree._Element":
    return etree.fromstring(f'<act xmlns="{_AKN_NS}"><body/></act>')


def _tree_with_section(num: str) -> "etree._Element":
    return etree.fromstring(
        f'<act xmlns="{_AKN_NS}">'
        f"  <body>"
        f'    <section eId="sec_{num}">'
        f"      <num>{num} §</num>"
        f"      <subsection><content>text</content></subsection>"
        f"    </section>"
        f"  </body>"
        f"</act>"
    )


def _ctx(
    muutos_ir: "IRNode | None" = None,
    johto: str = "",
    slot_assignment: "SubsectionSlotAssignmentResult | None" = None,
    subsec_map: "SubsectionSlotMap | None" = None,
    tree: "etree._Element | None" = None,
) -> _FilterCtx:
    return _FilterCtx(
        muutos_ir=muutos_ir,
        muutos_tree=tree or _empty_tree(),
        johto=johto,
        slot_assignment=slot_assignment,
        subsec_map=subsec_map,
    )


def _assignment_for_op(op: AmendmentOp, sub: IRNode) -> SubsectionSlotAssignmentResult:
    return SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=op.target_item,
                target_special=op.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )


def _op(
    op_type: Literal["REPLACE", "REPEAL", "INSERT", "RENUMBER"] = "REPLACE",
    target_kind: TargetKind = TargetKind.SECTION,
    target_section: str = "3",
    target_paragraph: "int | None" = None,
    target_item: "str | None" = None,
    target_special: "str | None" = None,
) -> AmendmentOp:
    return AmendmentOp(
        op_id="",
        op_type=op_type,
        target_kind=target_kind,
        target_section=target_section,
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
    )


# ---------------------------------------------------------------------------
# _c_language_variant
# ---------------------------------------------------------------------------


def test_c_language_variant_keeps_op_when_has_amendment_section() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir, johto="ruotsinkielinen sanamuoto")
    op = _op()
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_c_language_variant_drops_section_replace_when_lang_variant_only() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type="REPLACE", target_section="5")
    keep, reason = _c_language_variant(op, [op], ctx)
    assert keep is False
    assert "language-variant" in reason


def test_c_language_variant_drops_section_insert_when_lang_variant_only() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type="INSERT", target_section="5")
    keep, reason = _c_language_variant(op, [op], ctx)
    assert keep is False
    assert "language-variant" in reason


def test_c_language_variant_keeps_repeal_even_with_lang_variant() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type="REPEAL", target_section="5")
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_c_language_variant_keeps_renumber_even_with_lang_variant() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type="RENUMBER", target_section="5")
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_c_language_variant_keeps_op_when_johto_is_normal() -> None:
    ctx = _ctx(muutos_ir=None, johto="muutetaan 3 §")
    op = _op()
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_filter_ops_by_constraints_records_rejected_failed_op() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type="REPLACE", target_section="5")
    rejected: list[FailedOp] = []

    filtered = _filter_ops_by_constraints([op], ctx, rejected_ops_out=rejected)

    assert filtered == []
    assert len(rejected) == 1
    assert rejected[0].description == op.description()
    assert "_c_language_variant" in rejected[0].reason
    assert rejected[0].reason_code == "ELAB.REJECTED_LANGUAGE_VARIANT_ONLY"


def test_filter_ctx_does_not_promote_compat_subsec_map_to_slot_assignment() -> None:
    op = _op(target_paragraph=1)
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    ctx = _ctx(subsec_map=SubsectionSlotMap({id(op): sub}))

    assert ctx.slot_assignment is None
    assert ctx.mapped_subsection_for(op) is None
    assert ctx.has_subsection_mapping is False


# ---------------------------------------------------------------------------
# _c_no_source_payload
# ---------------------------------------------------------------------------


def test_c_no_source_payload_drops_replace_when_no_section() -> None:
    ctx = _ctx(muutos_ir=None)
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION)
    keep, reason = _c_no_source_payload(op, [op], ctx)
    assert keep is False
    assert "no source payload" in reason


def test_c_no_source_payload_keeps_op_when_section_present() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION)
    keep, _ = _c_no_source_payload(op, [op], ctx)
    assert keep is True


def test_c_no_source_payload_keeps_repeal_even_without_section() -> None:
    ctx = _ctx(muutos_ir=None)
    op = _op(op_type="REPEAL", target_kind=TargetKind.SECTION)
    keep, _ = _c_no_source_payload(op, [op], ctx)
    assert keep is True


def test_c_no_source_payload_keeps_chapter_level_op_without_section() -> None:
    ctx = _ctx(muutos_ir=None)
    op = _op(op_type="INSERT", target_kind=TargetKind.CHAPTER)
    keep, _ = _c_no_source_payload(op, [op], ctx)
    assert keep is True


# ---------------------------------------------------------------------------
# _c_no_heading_payload
# ---------------------------------------------------------------------------


def test_c_no_heading_payload_drops_otsikko_when_no_heading_child() -> None:
    ir = IRNode(
        kind=IRNodeKind.SECTION, label="3", children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", children=()),)
    )
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_special="otsikko")
    keep, reason = _c_no_heading_payload(op, [op], ctx)
    assert keep is False
    assert "heading" in reason


def test_c_no_heading_payload_keeps_otsikko_when_heading_child_exists() -> None:
    ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Otsikko"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
        ),
    )
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_special="otsikko")
    keep, _ = _c_no_heading_payload(op, [op], ctx)
    assert keep is True


def test_c_no_heading_payload_keeps_normal_replace_regardless() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION)
    keep, _ = _c_no_heading_payload(op, [op], ctx)
    assert keep is True


# ---------------------------------------------------------------------------
# _c_whole_section_subsumes_children
# ---------------------------------------------------------------------------


def test_c_whole_section_subsumes_drops_child_op_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")
    child_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    all_ops = [whole_op, child_op]
    keep, reason = _c_whole_section_subsumes_children(child_op, all_ops, ctx)
    assert keep is False
    assert "covered by whole-section replace" in reason


def test_c_whole_section_subsumes_keeps_insert_child_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")
    insert_op = _op(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    all_ops = [whole_op, insert_op]
    keep, reason = _c_whole_section_subsumes_children(insert_op, all_ops, ctx)
    assert keep is False
    assert "covered by whole-section replace" in reason


def test_c_whole_section_subsumes_keeps_whole_op_itself() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3")
    keep, _ = _c_whole_section_subsumes_children(whole_op, [whole_op], ctx)
    assert keep is True


def test_c_whole_section_subsumes_keeps_child_when_no_whole_op() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    child_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    keep, _ = _c_whole_section_subsumes_children(child_op, [child_op], ctx)
    assert keep is True


def test_c_whole_section_subsumes_keeps_explicit_child_repeal() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="8a")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="8a")
    repeal_op = _op(
        op_type="REPEAL",
        target_kind=TargetKind.SECTION,
        target_section="8a",
        target_paragraph=2,
    )
    keep, reason = _c_whole_section_subsumes_children(repeal_op, [whole_op, repeal_op], ctx)
    assert keep is True
    assert reason == ""


def test_c_whole_section_subsumes_drops_intro_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="7")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7")
    intro_op = _op(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="7",
        target_special="johd",
    )
    keep, reason = _c_whole_section_subsumes_children(intro_op, [whole_op, intro_op], ctx)
    assert keep is True
    assert reason == ""


def test_c_whole_section_subsumes_drops_heading_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="7")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="7")
    heading_op = _op(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="7",
        target_special="otsikko",
    )
    keep, reason = _c_whole_section_subsumes_children(heading_op, [whole_op, heading_op], ctx)
    assert keep is True
    assert reason == ""


def test_c_whole_section_subsumes_keeps_sparse_child_ops_in_mixed_group() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="123")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="123")
    item_op = _op(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="123",
        target_paragraph=1,
        target_item="8",
    )
    insert_op = _op(
        op_type="INSERT",
        target_kind=TargetKind.SECTION,
        target_section="123",
        target_paragraph=2,
    )
    all_ops = [whole_op, item_op, insert_op]

    keep_item, _ = _c_whole_section_subsumes_children(item_op, all_ops, ctx)
    keep_insert, _ = _c_whole_section_subsumes_children(insert_op, all_ops, ctx)

    assert keep_item is True
    assert keep_insert is True


def test_c_whole_section_subsumes_keeps_descendant_ops_when_group_has_heading_replace() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="8")
    ctx = _ctx(muutos_ir=ir)
    heading_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="8", target_special="otsikko")
    child_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="8", target_paragraph=3)

    keep_heading, reason_heading = _c_whole_section_subsumes_children(heading_op, [heading_op, child_op], ctx)
    keep_child, reason_child = _c_whole_section_subsumes_children(child_op, [heading_op, child_op], ctx)

    assert keep_heading is True
    assert reason_heading == ""
    assert keep_child is True
    assert reason_child == ""


# ---------------------------------------------------------------------------
# _c_replace_when_insert_same_paragraph
# ---------------------------------------------------------------------------


def test_c_replace_when_insert_defers_collapse_when_mapping_is_missing() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="5")
    ctx = _ctx(muutos_ir=ir)
    insert_op = _op(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    replace_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    all_ops = [insert_op, replace_op]
    keep, reason = _c_replace_when_insert_same_paragraph(replace_op, all_ops, ctx)
    assert keep is True
    assert reason == ""


def test_c_replace_when_insert_keeps_replace_with_different_paragraph() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="5")
    ctx = _ctx(muutos_ir=ir)
    insert_op = _op(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=3)
    replace_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    all_ops = [insert_op, replace_op]
    keep, _ = _c_replace_when_insert_same_paragraph(replace_op, all_ops, ctx)
    assert keep is True


def test_c_replace_when_insert_drops_only_when_same_payload_subsection_is_mapped() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="11a")
    shared_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="5")
    insert_op = _op(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    replace_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(
            {
                id(insert_op): shared_sub,
                id(replace_op): shared_sub,
            }
        ),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=insert_op.description(),
                op_type=str(insert_op.op_type or ""),
                target_paragraph=insert_op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="5",
            ),
            SparsePayloadSlotBinding(
                op_description=replace_op.description(),
                op_type=str(replace_op.op_type or ""),
                target_paragraph=replace_op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="5",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    ctx = _ctx(
        muutos_ir=ir,
        slot_assignment=assignment,
    )

    keep, reason = _c_replace_when_insert_same_paragraph(replace_op, [insert_op, replace_op], ctx)

    assert keep is False
    assert "INSERT" in reason


def test_c_replace_when_insert_keeps_replace_when_insert_uses_different_payload_subsection() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="11a")
    insert_op = _op(op_type="INSERT", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    replace_op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    ctx = _ctx(
        muutos_ir=ir,
        slot_assignment=_assignment_for_op(insert_op, IRNode(kind=IRNodeKind.SUBSECTION, label="5")),
    )

    keep, reason = _c_replace_when_insert_same_paragraph(replace_op, [insert_op, replace_op], ctx)

    assert keep is True
    assert reason == ""


def test_c_language_variant_replace_shadowed_by_sparse_insert_drops_earlier_replace() -> None:
    ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Muutetun veron kantoerien määräytyminen"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 5 momentti"),),
            ),
        ),
    )
    ctx = _ctx(
        muutos_ir=ir,
        johto="2 §:n 4 momentin ruotsinkielinen sanamuoto sekä lisätään 2 §:ään uusi 5 momentti",
    )
    replace_op = _op(op_type="REPLACE", target_section="2", target_paragraph=4)
    insert_op = _op(op_type="INSERT", target_section="2", target_paragraph=5)

    keep, reason = _c_language_variant_replace_shadowed_by_sparse_insert(
        replace_op,
        [replace_op, insert_op],
        ctx,
    )

    assert keep is False
    assert "language-variant replace shadowed" in reason


def test_c_language_variant_replace_shadowed_by_sparse_insert_keeps_insert() -> None:
    ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="2",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Muutetun veron kantoerien määräytyminen"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 5 momentti"),),
            ),
        ),
    )
    ctx = _ctx(
        muutos_ir=ir,
        johto="2 §:n 4 momentin ruotsinkielinen sanamuoto sekä lisätään 2 §:ään uusi 5 momentti",
    )
    replace_op = _op(op_type="REPLACE", target_section="2", target_paragraph=4)
    insert_op = _op(op_type="INSERT", target_section="2", target_paragraph=5)

    keep, reason = _c_language_variant_replace_shadowed_by_sparse_insert(
        insert_op,
        [replace_op, insert_op],
        ctx,
    )

    assert keep is True
    assert reason == ""


def test_c_language_variant_plain_replace_shadowed_by_sparse_item_payload_drops_plain_replaces() -> None:
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    ir = IRNode(kind=IRNodeKind.SECTION, label="9", children=(sub,))
    replace1 = _op(op_type="REPLACE", target_section="9", target_paragraph=1)
    replace3 = _op(op_type="REPLACE", target_section="9", target_paragraph=3)
    item3_2 = _op(op_type="REPLACE", target_section="9", target_paragraph=3, target_item="2")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(replace1): sub, id(replace3): sub, id(item3_2): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=replace1.description(),
                op_type=str(replace1.op_type or ""),
                target_paragraph=replace1.target_paragraph,
                target_item=replace1.target_item,
                target_special=replace1.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=replace3.description(),
                op_type=str(replace3.op_type or ""),
                target_paragraph=replace3.target_paragraph,
                target_item=replace3.target_item,
                target_special=replace3.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=item3_2.description(),
                op_type=str(item3_2.op_type or ""),
                target_paragraph=item3_2.target_paragraph,
                target_item=item3_2.target_item,
                target_special=item3_2.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    ctx = _ctx(
        muutos_ir=ir,
        johto="9 §:n 1 momentin ja 3 momentin johdantokappaleen ruotsinkielinen sanamuoto, 9 §:n 3 momentin 2 kohta",
        slot_assignment=assignment,
    )

    keep1, reason1 = _c_language_variant_plain_replace_shadowed_by_sparse_item_payload(
        replace1,
        [replace1, replace3, item3_2],
        ctx,
    )
    keep3, reason3 = _c_language_variant_plain_replace_shadowed_by_sparse_item_payload(
        replace3,
        [replace1, replace3, item3_2],
        ctx,
    )
    keep_item, reason_item = _c_language_variant_plain_replace_shadowed_by_sparse_item_payload(
        item3_2,
        [replace1, replace3, item3_2],
        ctx,
    )

    assert keep1 is False
    assert keep3 is False
    assert "shadowed by sparse item payload" in reason1
    assert "shadowed by sparse item payload" in reason3
    assert keep_item is True
    assert reason_item == ""


def test_c_language_variant_plain_replace_shadowed_by_sparse_item_payload_keeps_single_plain_target() -> None:
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="3")
    ir = IRNode(kind=IRNodeKind.SECTION, label="9", children=(sub,))
    replace3 = _op(op_type="REPLACE", target_section="9", target_paragraph=3)
    item3_2 = _op(op_type="REPLACE", target_section="9", target_paragraph=3, target_item="2")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(replace3): sub, id(item3_2): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=replace3.description(),
                op_type=str(replace3.op_type or ""),
                target_paragraph=replace3.target_paragraph,
                target_item=replace3.target_item,
                target_special=replace3.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=item3_2.description(),
                op_type=str(item3_2.op_type or ""),
                target_paragraph=item3_2.target_paragraph,
                target_item=item3_2.target_item,
                target_special=item3_2.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    ctx = _ctx(
        muutos_ir=ir,
        johto="9 §:n 3 momentin johdantokappaleen ruotsinkielinen sanamuoto, 9 §:n 3 momentin 2 kohta",
        slot_assignment=assignment,
    )

    keep, reason = _c_language_variant_plain_replace_shadowed_by_sparse_item_payload(
        replace3,
        [replace3, item3_2],
        ctx,
    )

    assert keep is True
    assert reason == ""


def test_c_internal_list_update_not_whole_section_replace_drops_literal_section_replace() -> None:
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        johto="muutetaan 1 §:ssä olevaa vuoden 1961 huumausaineyleissopimuksen luetteloa I seuraavasti:",
    )
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="1")

    keep, reason = _c_internal_list_update_not_whole_section_replace(op, [op], ctx)

    assert keep is False
    assert "internal section list update" in reason


# ---------------------------------------------------------------------------
# _c_phantom_subsection
# ---------------------------------------------------------------------------


def test_c_phantom_subsection_drops_when_op_id_not_in_subsec_map() -> None:
    op = _op(op_type="REPLACE", target_paragraph=2)
    ctx = _ctx(
        slot_assignment=SubsectionSlotAssignmentResult(
            subsec_map=SubsectionSlotMap(),
            sparse_slot_bindings=(),
            used_subs=(),
            unassigned_payload_slots=(),
        )
    )
    keep, reason = _c_phantom_subsection(op, [op], ctx)
    assert keep is False
    assert "missing subsection payload" in reason


def test_c_phantom_subsection_keeps_op_when_subsec_map_is_none() -> None:
    op = _op(op_type="REPLACE", target_paragraph=2)
    ctx = _ctx(subsec_map=None)
    keep, _ = _c_phantom_subsection(op, [op], ctx)
    assert keep is True


def test_c_phantom_subsection_keeps_when_op_id_in_subsec_map() -> None:
    ir = IRNode(kind=IRNodeKind.SUBSECTION, label="2")
    op = _op(op_type="REPLACE", target_paragraph=2)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): ir}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="2",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )
    ctx = _ctx(slot_assignment=assignment)
    keep, _ = _c_phantom_subsection(op, [op], ctx)
    assert keep is True


def test_filter_ctx_derives_subsec_map_from_slot_assignment() -> None:
    op = _op(op_type="REPLACE", target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="1",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )

    ctx = _ctx(slot_assignment=assignment, subsec_map=None)

    assert ctx.slot_assignment is assignment
    assert ctx.subsec_map is assignment.subsec_map
    assert ctx.has_subsection_mapping is True
    assert ctx.mapped_subsection_for(op) is sub
    assert ctx.has_mapped_subsection(op) is True


# ---------------------------------------------------------------------------
# _filter_ops_by_constraints — integration
# ---------------------------------------------------------------------------


def test_filter_ops_by_constraints_drops_both_child_and_lang_variant() -> None:
    ctx = _ctx(
        muutos_ir=None,
        johto="ruotsinkielinen sanamuoto",
    )
    op_r = _op(op_type="REPLACE", target_section="3")
    op_repeal = _op(op_type="REPEAL", target_section="4")

    rejected: list[FailedOp] = []
    result = _filter_ops_by_constraints([op_r, op_repeal], ctx, rejected_ops_out=rejected)

    # REPLACE with no payload and lang-variant johto -> dropped
    assert op_r not in result
    # REPEAL is kept (c_language_variant passes REPEALs through)
    assert op_repeal in result
    assert len(rejected) == 1
    assert rejected[0].description == op_r.description()


def test_filter_ops_by_constraints_keeps_all_when_section_present() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    op1 = _op(op_type="REPLACE", target_section="3")
    op2 = _op(op_type="INSERT", target_section="3")

    rejected: list[FailedOp] = []
    result = _filter_ops_by_constraints([op1, op2], ctx, rejected_ops_out=rejected)

    assert op1 in result
    assert op2 in result
    assert rejected == []

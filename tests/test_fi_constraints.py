"""Unit tests for lawvm.finland.constraints — op constraint predicates."""

import lxml.etree as etree
from types import SimpleNamespace
from typing import Any, cast

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.constraints import (
    _FilterCtx,
    _c_child_item_insert_covered_by_parent_snapshot,
    _c_fragmentary_parent_insert_shadowed_by_item_insert_payload,
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
from lawvm.finland.ops import OpType, AmendmentOp
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
    source_model: object | None = None,
) -> _FilterCtx:
    return _FilterCtx(
        muutos_ir=muutos_ir,
        muutos_tree=tree or _empty_tree(),
        johto=johto,
        slot_assignment=slot_assignment,
        subsec_map=subsec_map,
        source_model=cast(Any, source_model),
    )


def _assignment_for_op(op: AmendmentOp, sub: IRNode) -> SubsectionSlotAssignmentResult:
    return SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_cols.target_paragraph,
                target_item=op.target_cols.target_item,
                target_special=op.target_cols.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
        ),
        used_subs=(0,),
        unassigned_payload_slots=(),
    )


def _assignment_for_ops(*pairs: tuple[AmendmentOp, IRNode]) -> SubsectionSlotAssignmentResult:
    slot_map = SubsectionSlotMap({id(op): sub for op, sub in pairs})
    return SubsectionSlotAssignmentResult(
        subsec_map=slot_map,
        sparse_slot_bindings=tuple(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_cols.target_paragraph,
                target_item=op.target_cols.target_item,
                target_special=op.target_cols.target_special,
                payload_slot_index=idx,
                payload_slot_label=sub.label or "",
            )
            for idx, (op, sub) in enumerate(pairs, start=1)
        ),
        used_subs=tuple(range(len(pairs))),
        unassigned_payload_slots=(),
    )


def _op(
    op_type: OpType = OpType.REPLACE,
    target_kind: TargetKind = TargetKind.SECTION,
    target_section: str = "3",
    target_paragraph: "int | None" = None,
    target_item: "str | None" = None,
    target_special: "str | None" = None,
    numbered_table_targets: tuple[str, ...] = (),
) -> AmendmentOp:
    return AmendmentOp(
        op_id="",
        op_type=op_type,
        target_kind=target_kind,
        target_section=target_section,
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
        numbered_table_targets=numbered_table_targets,
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
    op = _op(op_type=OpType.REPLACE, target_section="5")
    keep, reason = _c_language_variant(op, [op], ctx)
    assert keep is False
    assert "language-variant" in reason


def test_c_language_variant_drops_section_insert_when_lang_variant_only() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type=OpType.INSERT, target_section="5")
    keep, reason = _c_language_variant(op, [op], ctx)
    assert keep is False
    assert "language-variant" in reason


def test_c_language_variant_keeps_repeal_even_with_lang_variant() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type=OpType.REPEAL, target_section="5")
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_c_language_variant_keeps_renumber_even_with_lang_variant() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type=OpType.RENUMBER, target_section="5")
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_c_language_variant_keeps_op_when_johto_is_normal() -> None:
    ctx = _ctx(muutos_ir=None, johto="muutetaan 3 §")
    op = _op()
    keep, _ = _c_language_variant(op, [op], ctx)
    assert keep is True


def test_filter_ops_by_constraints_records_rejected_failed_op() -> None:
    ctx = _ctx(muutos_ir=None, johto="ruotsinkielinen sanamuoto")
    op = _op(op_type=OpType.REPLACE, target_section="5")
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
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION)
    keep, reason = _c_no_source_payload(op, [op], ctx)
    assert keep is False
    assert "no source payload" in reason


def test_c_no_source_payload_keeps_op_when_section_present() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION)
    keep, _ = _c_no_source_payload(op, [op], ctx)
    assert keep is True


def test_c_no_source_payload_keeps_repeal_even_without_section() -> None:
    ctx = _ctx(muutos_ir=None)
    op = _op(op_type=OpType.REPEAL, target_kind=TargetKind.SECTION)
    keep, _ = _c_no_source_payload(op, [op], ctx)
    assert keep is True


def test_c_no_source_payload_keeps_chapter_level_op_without_section() -> None:
    ctx = _ctx(muutos_ir=None)
    op = _op(op_type=OpType.INSERT, target_kind=TargetKind.CHAPTER)
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
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_special="otsikko")
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
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_special="otsikko")
    keep, _ = _c_no_heading_payload(op, [op], ctx)
    assert keep is True


def test_c_no_heading_payload_keeps_otsikko_when_raw_source_heading_exists() -> None:
    prepared_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1"),),
    )
    raw_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="12",
        children=(
            IRNode(kind=IRNodeKind.NUM, text="12 §"),
            IRNode(kind=IRNodeKind.HEADING, text="Äänestyslipun mitättömyysperusteet"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="1"),
        ),
    )

    class SourceModel:
        def lookup_payload_ir(self, *args: object, **kwargs: object) -> object:
            return SimpleNamespace(payload_ir=raw_ir)

    ctx = _ctx(muutos_ir=prepared_ir, source_model=SourceModel())
    op = _op(
        op_type=OpType.INSERT,
        target_kind=TargetKind.SECTION,
        target_section="12",
        target_special="otsikko",
    )
    keep, _ = _c_no_heading_payload(op, [op], ctx)
    assert keep is True


def test_c_no_heading_payload_keeps_normal_replace_regardless() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION)
    keep, _ = _c_no_heading_payload(op, [op], ctx)
    assert keep is True


# ---------------------------------------------------------------------------
# _c_whole_section_subsumes_children
# ---------------------------------------------------------------------------


def test_c_whole_section_subsumes_drops_child_op_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3")
    child_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    all_ops = [whole_op, child_op]
    keep, reason = _c_whole_section_subsumes_children(child_op, all_ops, ctx)
    assert keep is False
    assert "covered by whole-section replace" in reason


def test_c_whole_section_subsumes_keeps_insert_child_when_whole_replace_exists() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3")
    insert_op = _op(op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    all_ops = [whole_op, insert_op]
    keep, reason = _c_whole_section_subsumes_children(insert_op, all_ops, ctx)
    assert keep is False
    assert "covered by whole-section replace" in reason


def test_c_whole_section_subsumes_keeps_whole_op_itself() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3")
    keep, _ = _c_whole_section_subsumes_children(whole_op, [whole_op], ctx)
    assert keep is True


def test_c_whole_section_subsumes_keeps_child_when_no_whole_op() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="3")
    ctx = _ctx(muutos_ir=ir)
    child_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="3", target_paragraph=2)
    keep, _ = _c_whole_section_subsumes_children(child_op, [child_op], ctx)
    assert keep is True


def test_c_whole_section_subsumes_keeps_child_when_only_whole_op_is_numbered_table_proxy() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="33")
    ctx = _ctx(muutos_ir=ir)
    table_proxy = _op(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="33",
        numbered_table_targets=("11",),
    )
    child_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="33", target_paragraph=2)

    keep, reason = _c_whole_section_subsumes_children(child_op, [table_proxy, child_op], ctx)

    assert keep is True
    assert reason == ""


def test_c_whole_section_subsumes_keeps_child_when_same_group_child_has_numbered_table_witness() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="33")
    ctx = _ctx(muutos_ir=ir)
    table_proxy = _op(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="33",
    )
    child_op = _op(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="33",
        target_paragraph=3,
        numbered_table_targets=("11",),
    )

    keep, reason = _c_whole_section_subsumes_children(child_op, [table_proxy, child_op], ctx)

    assert keep is True
    assert reason == ""


def test_c_whole_section_subsumes_keeps_explicit_child_repeal() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="8a")
    ctx = _ctx(muutos_ir=ir)
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="8a")
    repeal_op = _op(
        op_type=OpType.REPEAL,
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
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="7")
    intro_op = _op(
        op_type=OpType.REPLACE,
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
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="7")
    heading_op = _op(
        op_type=OpType.REPLACE,
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
    whole_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="123")
    item_op = _op(
        op_type=OpType.REPLACE,
        target_kind=TargetKind.SECTION,
        target_section="123",
        target_paragraph=1,
        target_item="8",
    )
    insert_op = _op(
        op_type=OpType.INSERT,
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
    heading_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="8", target_special="otsikko")
    child_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="8", target_paragraph=3)

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
    insert_op = _op(op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    replace_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    all_ops = [insert_op, replace_op]
    keep, reason = _c_replace_when_insert_same_paragraph(replace_op, all_ops, ctx)
    assert keep is True
    assert reason == ""


def test_c_replace_when_insert_keeps_replace_with_different_paragraph() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="5")
    ctx = _ctx(muutos_ir=ir)
    insert_op = _op(op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="5", target_paragraph=3)
    replace_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="5", target_paragraph=2)
    all_ops = [insert_op, replace_op]
    keep, _ = _c_replace_when_insert_same_paragraph(replace_op, all_ops, ctx)
    assert keep is True


def test_c_replace_when_insert_drops_only_when_same_payload_subsection_is_mapped() -> None:
    ir = IRNode(kind=IRNodeKind.SECTION, label="11a")
    shared_sub = IRNode(kind=IRNodeKind.SUBSECTION, label="5")
    insert_op = _op(op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    replace_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
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
                target_paragraph=insert_op.target_cols.target_paragraph,
                target_item=None,
                target_special=None,
                payload_slot_index=1,
                payload_slot_label="5",
            ),
            SparsePayloadSlotBinding(
                op_description=replace_op.description(),
                op_type=str(replace_op.op_type or ""),
                target_paragraph=replace_op.target_cols.target_paragraph,
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
    insert_op = _op(op_type=OpType.INSERT, target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
    replace_op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="11a", target_paragraph=5)
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
    replace_op = _op(op_type=OpType.REPLACE, target_section="2", target_paragraph=4)
    insert_op = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=5)

    keep, reason = _c_language_variant_replace_shadowed_by_sparse_insert(
        replace_op,
        [replace_op, insert_op],
        ctx,
    )

    assert keep is False
    assert "language-variant replace shadowed" in reason


def test_c_language_variant_replace_shadowed_by_sparse_insert_uses_source_payload_after_omission_prepare() -> None:
    prepared_ir = IRNode(
        kind=IRNodeKind.SECTION,
        label="44",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Valtiosihteerin tehtävät"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 momentti"),),
            ),
        ),
    )
    source_payload = IRNode(
        kind=IRNodeKind.SECTION,
        label="44",
        children=(
            IRNode(kind=IRNodeKind.HEADING, text="Valtiosihteerin tehtävät"),
            IRNode(kind=IRNodeKind.OMISSION),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.CONTENT, text="uusi 2 momentti"),),
            ),
        ),
    )
    source_model = SimpleNamespace(
        lookup_payload_ir=lambda *args, **kwargs: SimpleNamespace(payload_ir=source_payload)
    )
    ctx = _ctx(
        muutos_ir=prepared_ir,
        johto="44 §:n otsikon ja 1 momentin ruotsinkielinen sanamuoto sekä lisätään 44 §:ään uusi 2 momentti",
        source_model=source_model,
    )
    replace_op = _op(op_type=OpType.REPLACE, target_section="44", target_paragraph=1)
    insert_op = _op(op_type=OpType.INSERT, target_section="44", target_paragraph=2)

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
    replace_op = _op(op_type=OpType.REPLACE, target_section="2", target_paragraph=4)
    insert_op = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=5)

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
    replace1 = _op(op_type=OpType.REPLACE, target_section="9", target_paragraph=1)
    replace3 = _op(op_type=OpType.REPLACE, target_section="9", target_paragraph=3)
    item3_2 = _op(op_type=OpType.REPLACE, target_section="9", target_paragraph=3, target_item="2")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(replace1): sub, id(replace3): sub, id(item3_2): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=replace1.description(),
                op_type=str(replace1.op_type or ""),
                target_paragraph=replace1.target_cols.target_paragraph,
                target_item=replace1.target_cols.target_item,
                target_special=replace1.target_cols.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=replace3.description(),
                op_type=str(replace3.op_type or ""),
                target_paragraph=replace3.target_cols.target_paragraph,
                target_item=replace3.target_cols.target_item,
                target_special=replace3.target_cols.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=item3_2.description(),
                op_type=str(item3_2.op_type or ""),
                target_paragraph=item3_2.target_cols.target_paragraph,
                target_item=item3_2.target_cols.target_item,
                target_special=item3_2.target_cols.target_special,
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
    replace3 = _op(op_type=OpType.REPLACE, target_section="9", target_paragraph=3)
    item3_2 = _op(op_type=OpType.REPLACE, target_section="9", target_paragraph=3, target_item="2")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(replace3): sub, id(item3_2): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=replace3.description(),
                op_type=str(replace3.op_type or ""),
                target_paragraph=replace3.target_cols.target_paragraph,
                target_item=replace3.target_cols.target_item,
                target_special=replace3.target_cols.target_special,
                payload_slot_index=1,
                payload_slot_label=sub.label or "",
            ),
            SparsePayloadSlotBinding(
                op_description=item3_2.description(),
                op_type=str(item3_2.op_type or ""),
                target_paragraph=item3_2.target_cols.target_paragraph,
                target_item=item3_2.target_cols.target_item,
                target_special=item3_2.target_cols.target_special,
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
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="1")

    keep, reason = _c_internal_list_update_not_whole_section_replace(op, [op], ctx)

    assert keep is False
    assert "internal section list update" in reason


def test_c_fragmentary_parent_insert_shadowed_by_item_insert_payload_drops_parent() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3)
    child = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3, target_item="4a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="intro"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4a", children=(IRNode(kind=IRNodeKind.CONTENT, text="item"),)),
        ),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="2", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )

    keep, reason = _c_fragmentary_parent_insert_shadowed_by_item_insert_payload(parent, [parent, child], ctx)

    assert keep is False
    assert "fragmentary parent subsection insert" in reason


def test_c_fragmentary_parent_insert_shadowed_by_item_insert_payload_keeps_full_snapshot_parent() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="22", target_paragraph=1)
    child = _op(op_type=OpType.INSERT, target_section="22", target_paragraph=1, target_item="4a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="intro"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="one"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4a", children=(IRNode(kind=IRNodeKind.CONTENT, text="four a"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="5", children=(IRNode(kind=IRNodeKind.CONTENT, text="five"),)),
        ),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="22", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )

    keep, reason = _c_fragmentary_parent_insert_shadowed_by_item_insert_payload(parent, [parent, child], ctx)

    assert keep is True
    assert reason == ""


def test_c_child_item_insert_covered_by_parent_snapshot_drops_child() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="209b", target_paragraph=1)
    child = _op(op_type=OpType.INSERT, target_section="209b", target_paragraph=1, target_item="2a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="intro"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="one"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2", children=(IRNode(kind=IRNodeKind.CONTENT, text="two"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2a", children=(IRNode(kind=IRNodeKind.CONTENT, text="two a"),)),
        ),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="209b", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )

    keep, reason = _c_child_item_insert_covered_by_parent_snapshot(child, [parent, child], ctx)

    assert keep is False
    assert "child item insert covered" in reason


def test_c_child_item_insert_covered_by_parent_snapshot_keeps_fragmentary_child() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3)
    child = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3, target_item="4a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="3",
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="intro"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="4a", children=(IRNode(kind=IRNodeKind.CONTENT, text="item"),)),
        ),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="2", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )

    keep, reason = _c_child_item_insert_covered_by_parent_snapshot(child, [parent, child], ctx)

    assert keep is True
    assert reason == ""


def test_filter_ops_by_constraints_records_fragmentary_parent_insert_rejection() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3)
    child = _op(op_type=OpType.INSERT, target_section="2", target_paragraph=3, target_item="4a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="3",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="4a", children=(IRNode(kind=IRNodeKind.CONTENT, text="item"),)),),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="2", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )
    rejected: list[FailedOp] = []

    filtered = _filter_ops_by_constraints([parent, child], ctx, rejected_ops_out=rejected)

    assert filtered == [child]
    assert len(rejected) == 1
    assert rejected[0].reason_code == "ELAB.REJECTED_FRAGMENTARY_PARENT_INSERT_SHADOWED_BY_ITEM_INSERT"


def test_filter_ops_by_constraints_records_child_item_insert_covered_by_parent_snapshot() -> None:
    parent = _op(op_type=OpType.INSERT, target_section="209b", target_paragraph=1)
    child = _op(op_type=OpType.INSERT, target_section="209b", target_paragraph=1, target_item="2a")
    mapped = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", children=(IRNode(kind=IRNodeKind.CONTENT, text="one"),)),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="2a", children=(IRNode(kind=IRNodeKind.CONTENT, text="two a"),)),
        ),
    )
    ctx = _ctx(
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="209b", children=(mapped,)),
        slot_assignment=_assignment_for_ops((parent, mapped), (child, mapped)),
    )
    rejected: list[FailedOp] = []

    filtered = _filter_ops_by_constraints([parent, child], ctx, rejected_ops_out=rejected)

    assert filtered == [parent]
    assert len(rejected) == 1
    assert rejected[0].reason_code == "ELAB.REJECTED_CHILD_ITEM_INSERT_COVERED_BY_PARENT_SNAPSHOT"


# ---------------------------------------------------------------------------
# _c_phantom_subsection
# ---------------------------------------------------------------------------


def test_c_phantom_subsection_drops_when_op_id_not_in_subsec_map() -> None:
    op = _op(op_type=OpType.REPLACE, target_paragraph=2)
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
    op = _op(op_type=OpType.REPLACE, target_paragraph=2)
    ctx = _ctx(subsec_map=None)
    keep, _ = _c_phantom_subsection(op, [op], ctx)
    assert keep is True


def test_c_phantom_subsection_keeps_when_op_id_in_subsec_map() -> None:
    ir = IRNode(kind=IRNodeKind.SUBSECTION, label="2")
    op = _op(op_type=OpType.REPLACE, target_paragraph=2)
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): ir}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_cols.target_paragraph,
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
    op = _op(op_type=OpType.REPLACE, target_kind=TargetKind.SECTION, target_section="14", target_paragraph=1)
    sub = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    assignment = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap({id(op): sub}),
        sparse_slot_bindings=(
            SparsePayloadSlotBinding(
                op_description=op.description(),
                op_type=str(op.op_type or ""),
                target_paragraph=op.target_cols.target_paragraph,
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
    op_r = _op(op_type=OpType.REPLACE, target_section="3")
    op_repeal = _op(op_type=OpType.REPEAL, target_section="4")

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
    op1 = _op(op_type=OpType.REPLACE, target_section="3")
    op2 = _op(op_type=OpType.INSERT, target_section="3")

    rejected: list[FailedOp] = []
    result = _filter_ops_by_constraints([op1, op2], ctx, rejected_ops_out=rejected)

    assert op1 in result
    assert op2 in result
    assert rejected == []


# ---------------------------------------------------------------------------
# Typed FailedOp coordinates: governance reads typed fields, never the
# rendered description (representation-regression leak rank 24).
# ---------------------------------------------------------------------------


def _section_with_item(section: str, subsection: str, item: str) -> IRNode:
    """Build a section node holding one subsection that contains an item."""
    item_node = IRNode(kind=IRNodeKind.PARAGRAPH, label=item)
    sub_node = IRNode(kind=IRNodeKind.SUBSECTION, label=subsection, children=(item_node,))
    return IRNode(kind=IRNodeKind.SECTION, label=section, children=(sub_node,))


def _subsection_snapshot_lo(
    *,
    statute_id: str,
    section: str,
    subsection: str,
    payload: IRNode,
):
    from lawvm.core.ir import LegalAddress, LegalOperation
    from lawvm.core.provenance import OperationSource
    from lawvm.core.semantic_types import StructuralAction

    return LegalOperation(
        op_id=f"snapshot_subsection_{subsection}_from_section_{section}",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section), ("subsection", subsection))),
        payload=payload,
        source=OperationSource(statute_id=statute_id),
    )


def _make_governance(failed_ops, lo_ops, findings_out):
    from lawvm.core.phase_result import Finding
    from lawvm.finland.migration_ledger import MigrationLedger
    from lawvm.finland.process_failed_op_governance import ProcessFailedOpGovernance

    def _record_finding(**kwargs) -> Finding:
        finding = Finding(
            kind=kwargs["kind"],
            role=kwargs.get("role", "observation"),
            stage="test",
            detail=kwargs.get("detail", {}),
            source_statute=str(kwargs.get("source_statute") or ""),
            blocking=kwargs.get("blocking", False),
        )
        findings_out.append(finding)
        return finding

    return ProcessFailedOpGovernance(
        amendment_id="2020/1",
        johto="",
        failed_ops=failed_ops,
        process_findings=[],
        source_pathologies=[],
        lo_ops=lo_ops,
        resolved_ops=[],
        migration_ledger=MigrationLedger(),
        migration_ledger_initial_len=0,
        record_finding=_record_finding,
    )


def test_item_failure_governance_reads_typed_subsection_and_item() -> None:
    """Governance must consume FailedOp.target_subsection / target_item directly."""
    failed = FailedOp.from_scope(
        amendment_id="2020/1",
        # Deliberately unparseable description: if governance still regexed the
        # description it would never match and the failure would not be governed.
        description="opaque rendering with no mom/kohta structure",
        reason="item missing",
        reason_code="section_not_found",
        target_section="76",
        target_unit_kind="section",
        target_subsection="1",
        target_item="4a",
    )
    snapshot = _subsection_snapshot_lo(
        statute_id="2020/1",
        section="76",
        subsection="1",
        payload=_section_with_item("76", "1", "4a"),
    )
    failed_ops = [failed]
    findings: list = []
    gov = _make_governance(failed_ops, [snapshot], findings)

    gov.govern_item_failures_by_parent_subsection_snapshots()

    assert failed_ops == []  # governed out of the failed list
    governed = [f for f in findings if f.kind == "APPLY.FAILED_OPERATION_GOVERNED_BY_PARENT_SNAPSHOT"]
    assert len(governed) == 1
    assert governed[0].detail["target_subsection"] == "1"
    assert governed[0].detail["target_item"] == "4a"


def test_item_failure_without_typed_coords_is_not_governed() -> None:
    """A mom/kohta-shaped description alone must not trigger governance now."""
    failed = FailedOp.from_scope(
        amendment_id="2020/1",
        description="INSERT 76 § 1 mom 4a kohta",
        reason="item missing",
        reason_code="section_not_found",
        target_section="76",
        target_unit_kind="section",
        # No typed subsection/item coordinates populated.
    )
    snapshot = _subsection_snapshot_lo(
        statute_id="2020/1",
        section="76",
        subsection="1",
        payload=_section_with_item("76", "1", "4a"),
    )
    failed_ops = [failed]
    findings: list = []
    gov = _make_governance(failed_ops, [snapshot], findings)

    gov.govern_item_failures_by_parent_subsection_snapshots()

    assert failed_ops == [failed]  # untouched: typed coords required
    assert not [f for f in findings if f.kind == "APPLY.FAILED_OPERATION_GOVERNED_BY_PARENT_SNAPSHOT"]

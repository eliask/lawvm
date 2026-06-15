"""Hypothesis properties for the Finland payload-normalize seam.

These properties target the typed sparse slot-assignment layer:

* sparse slot assignment must stay monotone in source order
* ambiguous bindings must surface typed ambiguity, not guess
* every payload slot must be partitioned into assigned or leftover coverage
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.payload_normalize import _build_subsection_slot_assignment
from lawvm.finland.target_kind import TargetKind


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=(_content(text),))


def _section(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


@st.composite
def dense_local_slot_case(draw) -> tuple[IRNode, list[AmendmentOp], int]:
    slot_count = draw(st.integers(min_value=2, max_value=5))
    target_start = draw(st.integers(min_value=2, max_value=8))
    section_label = draw(st.integers(min_value=1, max_value=50).map(str))
    muutos_ir = _section(
        section_label,
        *(
            _subsection(str(idx), f"slot {idx}: {draw(SHORT_TEXT)}")
            for idx in range(1, slot_count + 1)
        ),
    )
    group_ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section=section_label,
            target_paragraph=target_start + idx - 1,
        )
        for idx in range(1, slot_count + 1)
    ]
    return muutos_ir, group_ops, target_start


@st.composite
def ambiguous_slot_case(draw) -> tuple[IRNode, list[AmendmentOp]]:
    section_label = draw(st.integers(min_value=1, max_value=50).map(str))
    muutos_ir = _section(
        section_label,
        _subsection("1", f"amend 1: {draw(SHORT_TEXT)}"),
        _subsection("1", f"amend duplicate 1: {draw(SHORT_TEXT)}"),
        _subsection("2", f"amend 2: {draw(SHORT_TEXT)}"),
    )
    group_ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section=section_label,
            target_paragraph=1,
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section=section_label,
            target_paragraph=2,
        ),
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section=section_label,
            target_paragraph=2,
            target_special="johd",
        ),
    ]
    return muutos_ir, group_ops


@st.composite
def coverage_partition_case(draw) -> tuple[IRNode, list[AmendmentOp], int]:
    slot_count = draw(st.integers(min_value=3, max_value=6))
    op_count = draw(st.integers(min_value=1, max_value=slot_count - 1))
    section_label = draw(st.integers(min_value=1, max_value=50).map(str))
    muutos_ir = _section(
        section_label,
        *(
            _subsection(str(idx), f"slot {idx}: {draw(SHORT_TEXT)}")
            for idx in range(1, slot_count + 1)
        ),
    )
    group_ops = [
        AmendmentOp(
            op_type="REPLACE",
            target_kind=TargetKind.SECTION,
            target_section=section_label,
            target_paragraph=idx,
        )
        for idx in range(1, op_count + 1)
    ]
    return muutos_ir, group_ops, slot_count


@given(dense_local_slot_case())
@settings(max_examples=80, deadline=None)
def test_sparse_slot_assignment_is_monotone_in_source_order(case: tuple[IRNode, list[AmendmentOp], int]) -> None:
    """Dense local sparse slots must bind monotonically in source order."""
    muutos_ir, group_ops, target_start = case

    got = _build_subsection_slot_assignment(muutos_ir, group_ops)

    assert len(got.sparse_slot_bindings) == len(group_ops)
    assert got.unassigned_payload_slots == ()
    assert [binding.payload_slot_index for binding in got.sparse_slot_bindings] == list(range(1, len(group_ops) + 1))
    assert [binding.target_paragraph for binding in got.sparse_slot_bindings] == [
        target_start + idx for idx in range(len(group_ops))
    ]


@given(ambiguous_slot_case())
@settings(max_examples=50, deadline=None)
def test_sparse_slot_assignment_degrades_on_ambiguity(case: tuple[IRNode, list[AmendmentOp]]) -> None:
    """Ambiguous sparse slots must surface typed ambiguity instead of guessing."""
    muutos_ir, group_ops = case

    got = _build_subsection_slot_assignment(muutos_ir, group_ops)

    assert got.binding_certificates
    cert = got.binding_certificates[0]
    assert cert.admissibility == "ambiguous"
    assert cert.candidate_count == 2
    assert got.unassigned_payload_slots


@given(coverage_partition_case())
@settings(max_examples=80, deadline=None)
def test_sparse_slot_assignment_partitions_all_payload_slots(
    case: tuple[IRNode, list[AmendmentOp], int],
) -> None:
    """Every payload slot must end up assigned or leftover, never both or neither."""
    muutos_ir, group_ops, slot_count = case

    got = _build_subsection_slot_assignment(muutos_ir, group_ops)

    assigned_slots = {binding.payload_slot_index for binding in got.sparse_slot_bindings}
    leftover_slots = {
        int(slot.split(":", 1)[0])
        for slot in got.unassigned_payload_slots
        if slot and slot.split(":", 1)[0].isdigit()
    }

    assert assigned_slots.isdisjoint(leftover_slots)
    assert assigned_slots | leftover_slots == set(range(1, slot_count + 1))

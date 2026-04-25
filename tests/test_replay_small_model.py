"""Small differential replay model properties.

This file covers the P2 tranche from the execution plan with a deliberately
tiny reference model: two disjoint sections, replace/repeal/insert.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import materialize_pit_ex
from tests.test_timeline_properties import _compile_timelines_with_explicit_temporal_authority


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
).filter(lambda s: any(ch.isalpha() for ch in s))


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _base_statute(left_text: str, right_text: str) -> IRStatute:
    return IRStatute(
        statute_id="test/small-model",
        title="Small differential model",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _section("1", left_text),
                _section("2", right_text),
            ),
        ),
    )


def _apply_reference_model(state: dict[str, str], op: LegalOperation) -> None:
    label = op.target.path[-1][1]
    if op.action == StructuralAction.REPLACE and op.payload is not None:
        state[label] = op.payload.text or ""
    elif op.action == StructuralAction.INSERT and op.payload is not None:
        state[label] = op.payload.text or ""
    elif op.action == StructuralAction.REPEAL:
        state.pop(label, None)


@st.composite
def small_replay_case(draw) -> tuple[IRStatute, list[LegalOperation], dict[str, str]]:
    left_text = draw(SHORT_TEXT)
    right_text = draw(SHORT_TEXT)
    left_kind = draw(st.sampled_from((StructuralAction.REPLACE, StructuralAction.REPEAL)))
    right_kind = draw(st.sampled_from((StructuralAction.REPLACE, StructuralAction.REPEAL)))
    left_new = draw(SHORT_TEXT)
    right_new = draw(SHORT_TEXT)

    base = _base_statute(left_text, right_text)
    op1 = LegalOperation(
        op_id="op1",
        sequence=1,
        action=left_kind,
        target=LegalAddress(path=(("section", "1"),)),
        payload=(_section("1", left_new) if left_kind == StructuralAction.REPLACE else None),
        source=OperationSource(statute_id="2001/1", enacted="2005-01-01", effective="2005-01-01"),
        applicability=(),
        provenance_tags=(),
    )
    op2 = LegalOperation(
        op_id="op2",
        sequence=2,
        action=right_kind,
        target=LegalAddress(path=(("section", "2"),)),
        payload=(_section("2", right_new) if right_kind == StructuralAction.REPLACE else None),
        source=OperationSource(statute_id="2001/2", enacted="2005-01-01", effective="2005-01-01"),
        applicability=(),
        provenance_tags=(),
    )
    reference = {"1": left_text, "2": right_text}
    for op in (op1, op2):
        _apply_reference_model(reference, op)
    return base, [op1, op2], reference


@st.composite
def small_insert_case(draw) -> tuple[IRStatute, list[LegalOperation], dict[str, str]]:
    left_text = draw(SHORT_TEXT)
    middle_text = draw(SHORT_TEXT)
    right_text = draw(SHORT_TEXT)
    insert_text = draw(SHORT_TEXT)

    base = IRStatute(
        statute_id="test/small-insert-model",
        title="Small differential insert model",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                _section("1", left_text),
                _section("2", middle_text),
                _section("3", right_text),
            ),
        ),
    )
    op = LegalOperation(
        op_id="insert_2",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"),)),
        payload=_section("2", insert_text),
        source=OperationSource(statute_id="2001/3", enacted="2005-01-01", effective="2005-01-01"),
        applicability=(),
        provenance_tags=(),
    )
    reference = {"1": left_text, "2": middle_text, "3": right_text}
    _apply_reference_model(reference, op)
    return base, [op], reference


@given(small_replay_case())
@settings(max_examples=60, deadline=None)
def test_small_replay_model_matches_real_materialization(
    case: tuple[IRStatute, list[LegalOperation], dict[str, str]]
) -> None:
    """Real replay/materialization must match a tiny disjoint-section reference model."""
    base, ops, reference = case

    timelines = _compile_timelines_with_explicit_temporal_authority(base, ops, base_date="2000-01-01")
    result = materialize_pit_ex(timelines, "2010-01-01", base=base)

    got = [child for child in result.statute.body.children if child.kind == IRNodeKind.SECTION]
    want = [(label, reference[label]) for label in sorted(reference)]

    assert [(child.label, child.text or "") for child in got] == want


@given(small_insert_case())
@settings(max_examples=60, deadline=None)
def test_small_replay_model_matches_real_materialization_for_insert(
    case: tuple[IRStatute, list[LegalOperation], dict[str, str]]
) -> None:
    """A section INSERT must preserve untouched siblings in the tiny reference model."""
    base, ops, reference = case

    timelines = _compile_timelines_with_explicit_temporal_authority(base, ops, base_date="2000-01-01")
    result = materialize_pit_ex(timelines, "2010-01-01", base=base)

    got = [child for child in result.statute.body.children if child.kind == IRNodeKind.SECTION]
    want = [(label, reference[label]) for label in sorted(reference)]

    assert [(child.label, child.text or "") for child in got] == want

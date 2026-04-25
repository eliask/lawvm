"""Metamorphic properties for the smallest stable replay seam.

These properties target the P1 tranche from
notes/PBT_EXECUTION_PLAN_2026-04-12.md:

* disjoint operations commute
* unrelated subtree locality is preserved
* failed ops are replay no-ops

Keep the worlds tiny.  Use explicit IR builders and the public
compile_timelines_ex/materialize_pit_ex seam only.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.timeline import compile_timelines_ex, materialize_pit_ex


SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=24,
)


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=(_content(text),))


def _section(label: str, text: str, sub_text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        text=text,
        children=(_subsection("1", sub_text),),
    )


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=sections)


def _statute(*sections: IRNode) -> IRStatute:
    return IRStatute(
        statute_id="test/replay-metamorphic",
        title="Replay metamorphic test",
        body=_body(*sections),
    )


def _section_payload(label: str, text: str, sub_text: str) -> IRNode:
    return _section(label, text, sub_text)


def _replace_op(
    section_label: str,
    op_id: str,
    *,
    source_id: str,
    group_id: str,
    text: str,
    sub_text: str,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=_section_payload(section_label, text, sub_text),
        group_id=group_id,
        source=OperationSource(
            statute_id=source_id,
            enacted="2024-01-01",
            effective="2024-01-01",
        ),
    )


def _failed_insert_op(section_label: str, op_id: str, *, source_id: str, group_id: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", section_label),)),
        payload=None,
        group_id=group_id,
        source=OperationSource(
            statute_id=source_id,
            enacted="2024-01-01",
            effective="2024-01-01",
        ),
    )


def _temporal_commence(group_id: str, statute_id: str, effective: str) -> TemporalEvent:
    return TemporalEvent(
        event_id=f"{group_id}:commence",
        group_id=group_id,
        kind="commence",
        scope=TemporalScope(target_statute=statute_id),
        effective=effective,
    )


def _node_signature(node: IRNode) -> tuple:
    return (
        str(node.kind),
        node.label,
        node.text,
        tuple(sorted(node.attrs.items())),
        tuple(_node_signature(child) for child in node.children),
    )


def _statute_signature(statute: IRStatute) -> tuple:
    return (
        tuple(_node_signature(child) for child in statute.body.children),
        tuple(_node_signature(child) for child in statute.supplements),
    )


@given(
    left_text=SHORT_TEXT,
    left_sub=SHORT_TEXT,
    right_text=SHORT_TEXT,
    right_sub=SHORT_TEXT,
)
@settings(max_examples=50, deadline=None)
def test_disjoint_operations_commute(
    left_text: str,
    left_sub: str,
    right_text: str,
    right_sub: str,
) -> None:
    """Disjoint section replaces must commute under replay compilation."""
    base = _statute(
        _section("1", "base 1", "base 1.1"),
        _section("2", "base 2", "base 2.1"),
        _section("3", "base 3", "base 3.1"),
    )
    op_left = _replace_op(
        "1",
        "replace-1",
        source_id="2024/1",
        group_id="g:left",
        text=left_text,
        sub_text=left_sub,
    )
    op_right = _replace_op(
        "2",
        "replace-2",
        source_id="2024/2",
        group_id="g:right",
        text=right_text,
        sub_text=right_sub,
    )
    temporal_events = (
        _temporal_commence("g:left", base.statute_id, "2024-01-01"),
        _temporal_commence("g:right", base.statute_id, "2024-01-01"),
    )

    first = compile_timelines_ex(base, [op_left, op_right], base_date="2000-01-01", temporal_events=temporal_events)
    second = compile_timelines_ex(base, [op_right, op_left], base_date="2000-01-01", temporal_events=temporal_events)

    pit_first = materialize_pit_ex(first.timelines, "2025-01-01", base=base).statute
    pit_second = materialize_pit_ex(second.timelines, "2025-01-01", base=base).statute

    assert _statute_signature(pit_first) == _statute_signature(pit_second)


@given(replacement_text=SHORT_TEXT, replacement_sub=SHORT_TEXT)
@settings(max_examples=50, deadline=None)
def test_unrelated_subtree_locality(replacement_text: str, replacement_sub: str) -> None:
    """An op on one section must not mutate unrelated sibling sections."""
    base = _statute(
        _section("1", "base 1", "base 1.1"),
        _section("2", "base 2", "base 2.1"),
        _section("3", "base 3", "base 3.1"),
    )
    op = _replace_op(
        "1",
        "replace-1",
        source_id="2024/1",
        group_id="g:locality",
        text=replacement_text,
        sub_text=replacement_sub,
    )

    result = compile_timelines_ex(
        base,
        [op],
        base_date="2000-01-01",
        temporal_events=(_temporal_commence("g:locality", base.statute_id, "2024-01-01"),),
    )
    pit = materialize_pit_ex(result.timelines, "2025-01-01", base=base).statute

    base_sections = {child.label: child for child in base.body.children if child.kind == IRNodeKind.SECTION}
    pit_sections = {child.label: child for child in pit.body.children if child.kind == IRNodeKind.SECTION}

    assert _node_signature(pit_sections["2"]) == _node_signature(base_sections["2"])
    assert _node_signature(pit_sections["3"]) == _node_signature(base_sections["3"])
    assert _node_signature(pit_sections["1"]) != _node_signature(base_sections["1"])


@given(valid_text=SHORT_TEXT)
@settings(max_examples=50, deadline=None)
def test_failed_insert_is_a_noop(valid_text: str) -> None:
    """A failed insert with no payload must leave the replay result unchanged."""
    base = _statute(
        _section("1", "base 1", "base 1.1"),
        _section("2", "base 2", "base 2.1"),
    )
    valid = _replace_op(
        "1",
        "replace-1",
        source_id="2024/1",
        group_id="g:valid",
        text=valid_text,
        sub_text="updated 1.1",
    )
    failed = _failed_insert_op("99", "failed-insert", source_id="2024/99", group_id="g:failed")
    temporal_events = (
        _temporal_commence("g:valid", base.statute_id, "2024-01-01"),
        _temporal_commence("g:failed", base.statute_id, "2024-01-01"),
    )

    only_valid = compile_timelines_ex(base, [valid], base_date="2000-01-01", temporal_events=temporal_events)
    with_failed = compile_timelines_ex(base, [failed, valid], base_date="2000-01-01", temporal_events=temporal_events)

    pit_only_valid = materialize_pit_ex(only_valid.timelines, "2025-01-01", base=base).statute
    pit_with_failed = materialize_pit_ex(with_failed.timelines, "2025-01-01", base=base).statute

    assert _statute_signature(pit_only_valid) == _statute_signature(pit_with_failed)
    assert any(issue.kind == "missing_insert_payload" for issue in with_failed.issues)

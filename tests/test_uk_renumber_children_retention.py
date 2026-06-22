"""Regression for RENUMBER children-retention by nesting-admissibility.

When a RENUMBER op renumbers ``section:N`` → ``section:N/subsection:M`` (via
``_apply_same_provision_descendant_renumber`` in ``replay_renumber_apply.py``),
the existing children of the source ``section:N`` are split into two groups:

- ``retained_children`` — kept as children of the (now-rewritten) src section
- ``moved_children`` — moved into the new ``subsection:M`` node

Previously, the split was purely ``kind in (HEADING, NUM)`` → retain, else
→ move.  This caused a §1.3 escalation: if the source section already had a
``subsection`` child (from a prior amendment), that ``subsection`` got moved
INTO the new ``subsection:M``, producing ``unexpected subsection inside
subsection``.

The fix gates ``moved_children`` by ``_NESTING_ORDER[destination_kind]``: only
children whose kind is admitted under the destination's nesting order are moved;
the rest stay at the parent level, as siblings of the new subsection.

This was the root cause of the monotone ``all_tree`` failure on
``ukpga/1992/52`` s.279 (46/74 amendments bad, introduced by
``ukpga/2003/43``).

These tests drive the *real* production path end-to-end via ``replay_uk_ops``
(which dispatches into ``_apply_same_provision_descendant_renumber``) and assert
on the resulting tree shape.  Reverting the retained/moved nesting-admissibility
filter in production turns ``test_renumber_retains_non_admitted_children`` RED.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.tree_ops import _NESTING_ORDER
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_amendment_replay import replay_uk_ops


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2003/43", title="Amending Act")


def _renumber_op(
    *,
    target: LegalAddress,
    destination: LegalAddress,
    op_id: str = "uk-renumber-retention-op",
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.RENUMBER,
        target=target,
        destination=destination,
        source=_source(),
        sequence=1,
    )


def _statute_section_with_mixed_children() -> IRStatute:
    """Section 279 with HEADING + NUM + PARAGRAPH (a) + SUBSECTION (4).

    Mirrors the ``ukpga/1992/52`` s.279 shape that exposed the bug: the section
    already carries a ``subsection`` child from a prior amendment.
    """
    return IRStatute(
        statute_id="ukpga/1992/52",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="279",
                    text="Original section text.",
                    children=(
                        IRNode(kind=IRNodeKind.HEADING, label=None, text="My Heading"),
                        IRNode(kind=IRNodeKind.NUM, label=None, text="279"),
                        IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Para a"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Sub 4"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def test_subsection_admits_paragraph_but_not_subsection() -> None:
    """The nesting order must NOT admit subsection→subsection."""
    assert "paragraph" in _NESTING_ORDER.get("subsection", set())
    assert "subsection" not in _NESTING_ORDER.get("subsection", set())


def test_renumber_retains_non_admitted_children() -> None:
    """End-to-end: renumber section:279 → section:279/subsection:1 via the real
    production replay path, then assert the resulting tree shape.

    - HEADING and NUM are RETAINED at the section level.
    - PARAGRAPH is admitted under subsection → MOVED into the new subsection.
    - SUBSECTION is NOT admitted under subsection → RETAINED at the section level
      (this is the behavior the fix introduced; without it the pre-existing
      subsection would be nested INTO the new subsection, producing the
      ``unexpected subsection inside subsection`` escalation).
    """
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section_with_mixed_children()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "279"),)),
        destination=LegalAddress(path=(("section", "279"), ("subsection", "1"))),
    )

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    section = result.body.children[0]
    assert section.label == "279", f"Expected section 279, got {section.label!r}"

    section_child_kinds = [c.kind for c in section.children]

    # The new subsection must exist at the section level.
    new_subs = [
        c for c in section.children
        if c.kind == IRNodeKind.SUBSECTION and c.label == "1"
    ]
    assert len(new_subs) == 1, (
        f"Expected exactly one new subsection (1) at section level, got "
        f"{[(c.kind, c.label) for c in section.children]!r}"
    )
    new_sub = new_subs[0]

    # HEADING and NUM are RETAINED at the section level.
    assert IRNodeKind.HEADING in section_child_kinds, (
        f"HEADING must be retained at section level, got {section_child_kinds!r}"
    )
    assert IRNodeKind.NUM in section_child_kinds, (
        f"NUM must be retained at section level, got {section_child_kinds!r}"
    )

    # PARAGRAPH (admitted under subsection) is MOVED into the new subsection.
    moved_child_kinds = [c.kind for c in new_sub.children]
    assert IRNodeKind.PARAGRAPH in moved_child_kinds, (
        f"PARAGRAPH must be moved into the new subsection, "
        f"got subsection children {[(c.kind, c.label) for c in new_sub.children]!r}"
    )
    assert IRNodeKind.PARAGRAPH not in section_child_kinds, (
        f"PARAGRAPH must NOT remain at the section level, got {section_child_kinds!r}"
    )

    # SUBSECTION (4) is NOT admitted under subsection → RETAINED at section level,
    # NOT nested into the new subsection.  This is the load-bearing assertion that
    # bites when the nesting-admissibility filter is reverted in production.
    retained_subs_4 = [
        c for c in section.children
        if c.kind == IRNodeKind.SUBSECTION and c.label == "4"
    ]
    assert len(retained_subs_4) == 1, (
        f"Pre-existing subsection (4) must be RETAINED at the section level, but "
        f"section children are {[(c.kind, c.label) for c in section.children]!r}"
    )
    nested_subs = [
        c for c in new_sub.children if c.kind == IRNodeKind.SUBSECTION
    ]
    assert not nested_subs, (
        f"Pre-existing subsection (4) must NOT be nested inside the new "
        f"subsection (1) — that is the 'subsection inside subsection' escalation "
        f"the fix prevents; got new-subsection children "
        f"{[(c.kind, c.label) for c in new_sub.children]!r}"
    )

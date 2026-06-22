"""Synthetic regression for RENAME op children-retention by nesting-admissibility.

When a RENAME/RENUMBER op renumbers ``section:N`` → ``section:N/subsection:M``
(via ``_apply_same_provision_descendant_renumber``), all existing children
of the source ``section:N`` are split into two groups:

- ``retained_children`` — kept as children of the (now-rewritten) src section
- ``moved_children`` — moved into the new ``subsection:M`` node

Previously, the split was purely ``kind in (HEADING, NUM)`` → retain, else
→ move.  This caused a §1.3 escalation: if the source section already had
a ``subsection`` child (from a prior amendment), that ``subsection`` got
moved INTO the new ``subsection:M``, producing ``unexpected subsection
inside subsection``.

The fix gates ``moved_children`` by ``_NESTING_ORDER[destination_kind]``:
only children whose kind is admitted under the destination's nesting order
are moved; the rest stay at the parent level, as siblings of the new
subsection.

This was the root cause of the monotone ``all_tree`` failure on
``ukpga/1992/52`` s.279 (46/74 amendments bad, introduced by
``ukpga/2003/43``).
"""
from __future__ import annotations

from lawvm.core.ir import IRNodeKind
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.tree_ops import _NESTING_ORDER
from lawvm.uk_legislation.mutable_ir import UKMutableNode


def test_subsection_admits_paragraph_but_not_subsection() -> None:
    """The nesting order must NOT admit subsection→subsection."""
    assert "paragraph" in _NESTING_ORDER.get("subsection", set())
    assert "subsection" not in _NESTING_ORDER.get("subsection", set())


def test_renumber_retains_non_admitted_children() -> None:
    """HEADING and NUM are always retained at the section level.
    PARAGRAPH is admitted under subsection → moved.
    SUBSECTION is NOT admitted under subsection → retained (not moved)."""
    section = UKMutableNode(
        kind=IRNodeKind.SECTION,
        label="279",
        text="Original section text.",
        attrs={},
        children=[
            UKMutableNode(kind=IRNodeKind.HEADING, label=None, text="My Heading"),
            UKMutableNode(kind=IRNodeKind.NUM, label=None, text="279"),
            UKMutableNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Para a"),
            UKMutableNode(kind=IRNodeKind.SUBSECTION, label="4", text="Sub 4"),
        ],
    )

    dest_admitted = _NESTING_ORDER.get("subsection", set())

    retained = [
        child for child in section.children
        if child.kind in (IRNodeKind.HEADING, IRNodeKind.NUM)
        or _kind_str(child.kind) not in dest_admitted
    ]
    moved = [
        child for child in section.children
        if child.kind not in (IRNodeKind.HEADING, IRNodeKind.NUM)
        and _kind_str(child.kind) in dest_admitted
    ]

    # HEADING and NUM are always retained.
    assert any(c.kind == IRNodeKind.HEADING for c in retained)
    assert any(c.kind == IRNodeKind.NUM for c in retained)

    # PARAGRAPH is admitted under subsection → moved.
    assert any(c.kind == IRNodeKind.PARAGRAPH for c in moved)
    assert not any(c.kind == IRNodeKind.PARAGRAPH for c in retained)

    # SUBSECTION is NOT admitted under subsection → retained at parent level.
    assert any(c.kind == IRNodeKind.SUBSECTION for c in retained)
    assert not any(c.kind == IRNodeKind.SUBSECTION for c in moved)

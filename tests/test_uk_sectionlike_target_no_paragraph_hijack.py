"""Regression: a section-level INSERT target must not be silently rerouted into
a paragraph sibling via the EID-derived parent fallback.

Root cause (ukpga/1978/29 s.75B, source witness ssi/2013/292 reg. 8(3)):
the op ``INSERT section:75b/subsection:1a`` derives a parent eId ``section-75b``.
The eId-derived parent lookup uses sequence-token matching, and
``_get_id_sequence("section-75b") == _get_id_sequence("section-75-b") ==
("section", "75", "b")``.  When the source-named SECTION 75B is absent from the
live tree (e.g. held back upstream), the fallback collapses ``section-75b`` onto
the existing paragraph ``section-75-b`` and silently inserts the subsection 1A as
a child of ``section:75/paragraph:b`` — a forbidden §1.1 silent target hijack
(a section-level target absorbed into a paragraph node with no failed-op signal).

The fix gates the eId-derived parent fallback in
``replay_insert_apply._insert_node_v2`` with
``_eid_candidate_matches_target_leaf``: when the resolved node's kind is not
compatible with the named parent leaf kind (``section`` here), the bind is
refused so the op fails loud with a ``uk_replay_missing_..._parent_shape_gap``
adjudication instead of being silently absorbed.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.uk_amendment_replay import replay_uk_ops


def _statute_section75_with_paragraph_b() -> IRStatute:
    """Section 75 with paragraphs a and b — but NO section 75B in the tree."""
    return IRStatute(
        statute_id="ukpga/1978/29",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="75",
                    text="",
                    attrs={"eId": "section-75"},
                    children=(
                        IRNode(kind=IRNodeKind.HEADING, label=None, text="Heading"),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="a",
                            text="para a",
                            attrs={"eId": "section-75-a"},
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="b",
                            text="para b",
                            attrs={"eId": "section-75-b"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _insert_subsection_into_section_75b() -> LegalOperation:
    return LegalOperation(
        op_id="uk-s75b-1a-insert",
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "75b"), ("subsection", "1a"))),
        payload=IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="1A",
            text="But the duty in subsection (1) does not apply ...",
        ),
        source=OperationSource(statute_id="ssi/2013/292", title="Amending SI"),
        sequence=1,
    )


def test_sectionlike_insert_not_hijacked_into_paragraph_sibling() -> None:
    """INSERT section:75b/subsection:1a must not land inside section:75/paragraph:b.

    The paragraph ``b`` of section 75 must keep zero children, and the op must
    fail loud with a missing-parent-shape gap rather than being silently absorbed.
    """
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section75_with_paragraph_b()
    op = _insert_subsection_into_section_75b()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    section_75 = result.body.children[0]
    assert section_75.label == "75"
    paragraph_b = next(
        c for c in section_75.children
        if c.kind == IRNodeKind.PARAGRAPH and c.label == "b"
    )

    # The subsection 1A must NOT have been silently absorbed into paragraph b.
    nested_subsections = [
        c for c in paragraph_b.children if c.kind == IRNodeKind.SUBSECTION
    ]
    assert not nested_subsections, (
        f"section:75/paragraph:b silently absorbed a section-level subsection "
        f"target — §1.1 hijack regression; paragraph b children: "
        f"{[(c.kind, c.label) for c in paragraph_b.children]!r}"
    )
    assert not paragraph_b.children, (
        f"paragraph b should remain childless, got "
        f"{[(c.kind, c.label) for c in paragraph_b.children]!r}"
    )

    # The op must fail loud: a missing-parent-shape gap (the named SECTION 75B is
    # absent), NOT a downstream tree-invariant violation from a silent absorption.
    kinds = [a.kind for a in adjudications]
    assert any("missing" in k and "parent_shape" in k for k in kinds), (
        f"Expected a missing-parent-shape gap adjudication (fail-loud), got: {kinds!r}"
    )
    assert "uk_replay_tree_invariant_violation" not in kinds, (
        f"A tree-invariant violation means the subsection was silently absorbed "
        f"first and only caught downstream; expected upstream fail-loud instead. "
        f"adjudications: {kinds!r}"
    )

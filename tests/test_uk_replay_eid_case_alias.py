"""Regression for case-insensitive exact EID lookup aliases.

UK emitted node eIds keep uppercase letter suffixes (``section-17-1A``), while
insertion anchors and oracle ``eid_map`` keys stay lowercase.  Before this fix,
a lowercase anchor such as ``section-17-1a`` missed the exact index (which only
stored the uppercase key) and fell through to sequence/suffix lookup.  That
lookup collapsed ``section-17-1a`` with the existing ``section-17-1-a`` paragraph,
misrouting a chained ``insert section:17/subsection:1B`` operation as a child
of subsection 1 instead of section 17, and the provision was clobbered by a
later text edit.

This test pins the exact-index alias that lets lowercase anchors resolve to the
uppercase node.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_executor import UKReplayExecutor


def _make_executor_with_uppercase_inserted_node() -> UKReplayExecutor:
    """Return an executor whose section 1 contains paragraphs a/b and a newly
    inserted subsection 1A with an uppercase-suffix eId."""
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            attrs={},
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    attrs={"eId": "section-1"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="a",
                            attrs={"eId": "section-1-a"},
                        ),
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="b",
                            attrs={"eId": "section-1-b"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute)
    section = executor.statute.body.children[0]
    inserted = UKMutableNode(
        kind=IRNodeKind.SUBSECTION,
        label="1A",
        attrs={"eId": "section-1-1A"},
    )
    section.children.append(inserted)
    executor._record_child_inserted(section, inserted)
    return executor


def test_lower_case_anchor_resolves_to_uppercase_eid_node() -> None:
    """A lowercase anchor for a letter-suffixed node finds the uppercase node."""
    executor = _make_executor_with_uppercase_inserted_node()

    result = executor._cached_exact_eid_lookup("section-1-1a")
    assert result.node is not None
    assert str(result.node.label) == "1A"
    assert result.node.attrs.get("eId") == "section-1-1A"
    assert result.parent is not None
    assert str(result.parent.kind) == "section"


def test_find_node_and_parent_uses_case_alias_before_sequence_match() -> None:
    """`_find_node_and_parent_statute` must not collapse `section-1-1a` with
    the existing `section-1-a` paragraph."""
    executor = _make_executor_with_uppercase_inserted_node()

    result = executor._find_node_and_parent_statute("section-1-1a")
    assert result.node is not None
    assert str(result.node.kind) == "subsection"
    assert str(result.node.label) == "1A"
    assert result.parent is not None
    assert str(result.parent.kind) == "section"


def test_case_alias_marks_ambiguous_when_conflicting_node_exists() -> None:
    """If a distinct node already owns the lowercased eId key, the alias is
    ambiguous and must not silently hijack it."""
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            attrs={},
            children=(
                # A node whose canonical eId is already the lower-case form.
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1a",
                    attrs={"eId": "section-1a"},
                ),
                # A later node whose uppercase eId lowercases to the same key.
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1A",
                    attrs={"eId": "section-1A"},
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute)

    # The lowercase exact key is claimed by two different nodes.
    result = executor._cached_exact_eid_lookup("section-1a")
    assert result.node is None
    assert "section-1a" in executor._eid_lookup_ambiguous

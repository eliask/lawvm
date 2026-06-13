"""Tests for UK schedule paragraph descent through Part/crossheading wrappers.

Real UK schedules nest their paragraphs under ``Part`` partitions which wrap
unlabelled ``crossheading`` nodes:

    schedule:N -> Part -> crossheading -> paragraph:M

Amendment target addresses name only ``schedule:N/paragraph:M`` with no
``part``/``crossheading`` step, so the direct-child match fails.  The descent
must treat ``Part``/``crossheading`` as structurally transparent for the
paragraph step and resolve to the single matching paragraph — without diving
into unrelated subtrees (which would make resolution ambiguous).

Reproduces the ``ukpga/2000/17`` Schedule 6 shape (Cluster C in the UK
target_absent root-cause analysis).

Rule ID verified:
  uk_replay_schedule_partition_transparent_paragraph_resolved
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_executor import UKReplayExecutor
from lawvm.uk_legislation.replay_target_lookup import (
    UK_REPLAY_SCHEDULE_PARTITION_TRANSPARENT_PARAGRAPH_RESOLVED_RULE_ID,
)


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _paragraph(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        text="",
        children=(
            IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="1", text=text),
        ),
    )


def _crossheading(*paragraphs: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CROSSHEADING, label=None, text="", children=tuple(paragraphs))


def _schedule6_part_crossheading() -> IRStatute:
    """schedule:6 -> Part -> crossheading -> paragraph (the real ukpga/2000/17 shape)."""
    return IRStatute(
        statute_id="ukpga/2000/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 6",
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="Part I",
                        text="",
                        children=(
                            _crossheading(_paragraph("1", "Para 1 text.")),
                            _crossheading(_paragraph("2", "Para 2 text.")),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="Part II",
                        text="",
                        children=(
                            _crossheading(_paragraph("8", "Para 8 text.")),
                        ),
                    ),
                ),
            ),
        ),
    )


def _schedule6_duplicate_paragraph_across_parts() -> IRStatute:
    """Two Parts each carry a paragraph labelled '1' — partition-ambiguous."""
    return IRStatute(
        statute_id="ukpga/2000/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 6",
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="Part I",
                        text="",
                        children=(_crossheading(_paragraph("1", "Part I para 1.")),),
                    ),
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="Part II",
                        text="",
                        children=(_crossheading(_paragraph("1", "Part II para 1.")),),
                    ),
                ),
            ),
        ),
    )


def _replace_op(paragraph_label: str) -> LegalOperation:
    return LegalOperation(
        op_id=f"uk-test-partition-{paragraph_label}",
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("schedule", "6"),
                ("paragraph", paragraph_label),
                ("subparagraph", "1"),
            ),
        ),
        payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="1", text="Replaced text."),
        source=_source(),
        sequence=1,
    )


# ---------------------------------------------------------------------------
# Positive: paragraph under Part -> crossheading resolves and applies
# ---------------------------------------------------------------------------

def test_schedule_paragraph_under_part_crossheading_resolves() -> None:
    statute = _schedule6_part_crossheading()
    ex = UKReplayExecutor(statute)

    # Paragraph in Part I (one wrapper level deeper than the schedule root).
    res = ex._find_node_by_target(LegalAddress(path=(("schedule", "6"), ("paragraph", "2"))))
    assert res.node is not None, "schedule:6/paragraph:2 must resolve through Part/crossheading"
    assert str(res.node.kind) == "paragraph"
    assert res.node.label == "2"
    # The recovered parent is the real enclosing crossheading, not the schedule.
    assert res.parent is not None and str(res.parent.kind) == "crossheading"

    # Paragraph in a different Part also resolves.
    res8 = ex._find_node_by_target(LegalAddress(path=(("schedule", "6"), ("paragraph", "8"))))
    assert res8.node is not None and res8.node.label == "8"


def test_schedule_paragraph_partition_descent_applies_and_emits_clean_adjudication() -> None:
    statute = _schedule6_part_crossheading()
    adjudications: list[CompileAdjudication] = []
    ex = UKReplayExecutor(statute, adjudications_out=adjudications)
    op = _replace_op("2")

    res = ex._find_node_by_target(op.target, target_resolution_op=op)
    assert res.node is not None and str(res.node.kind) == "subparagraph"

    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_SCHEDULE_PARTITION_TRANSPARENT_PARAGRAPH_RESOLVED_RULE_ID in rule_ids, (
        f"Expected partition-transparent resolution adjudication in {rule_ids!r}"
    )
    adj = next(
        a for a in adjudications
        if a.kind == UK_REPLAY_SCHEDULE_PARTITION_TRANSPARENT_PARAGRAPH_RESOLVED_RULE_ID
    )
    assert adj.detail["blocking"] is False
    assert adj.detail["quirks_disposition"] == "apply"
    assert adj.detail["family"] == "target_resolution_recovery"
    target_resolution = adj.detail["target_resolution"]
    assert target_resolution["target_resolution_status"] == "recovered"
    assert target_resolution["source_target"] == "schedule:6/paragraph:2/subparagraph:1"


# ---------------------------------------------------------------------------
# Negative: duplicate paragraph label across Parts must not silently resolve
# ---------------------------------------------------------------------------

def test_schedule_paragraph_ambiguous_across_parts_not_resolved() -> None:
    """A paragraph label present under two Parts is ambiguous; do not pick one."""
    statute = _schedule6_duplicate_paragraph_across_parts()
    ex = UKReplayExecutor(statute)

    res = ex._find_node_by_target(LegalAddress(path=(("schedule", "6"), ("paragraph", "1"))))
    assert res.node is None, (
        "Ambiguous schedule:6/paragraph:1 (two Parts) must not be silently resolved"
    )


# ---------------------------------------------------------------------------
# Replay-safety: a directly-addressed schedule paragraph is unaffected
# ---------------------------------------------------------------------------

def test_direct_schedule_paragraph_still_resolves() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="SCHEDULE 6",
                text="",
                children=(_paragraph("3", "Direct para 3."),),
            ),
        ),
    )
    ex = UKReplayExecutor(statute)
    res = ex._find_node_by_target(LegalAddress(path=(("schedule", "6"), ("paragraph", "3"))))
    assert res.node is not None and res.node.label == "3"
    # Parent is the schedule root (direct child), not a wrapper.
    assert res.parent is not None and str(res.parent.kind) == "schedule"

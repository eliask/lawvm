"""Tests for UK recursive-descent target recovery uniqueness gate.

Covers §1.1 "No silent target hijacking" for the
_find_recursive_match / _find_node_by_target call site.

Rule IDs verified:
  uk_replay_target_resolved_by_recursive_descent
  uk_replay_target_ambiguous_recursive_descent

AGENTS.md obligations covered:
  §15.1 synthetic unit test
  §15.2 real corpus regression (see probe note in bench output)
  §15.3 finding/observation test
  §15.4 negative test
  §15.5 strict-mode test
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_target_lookup import (
    UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID,
    UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor, replay_uk_ops


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


# ---------------------------------------------------------------------------
# Statute builders
# ---------------------------------------------------------------------------

def _statute_single_depth_match() -> IRStatute:
    """Section 1 contains a part that contains subsection 2A.

    Direct path 'section:1 / subsection:2A' fails (2A is not a direct child
    of section 1) but exactly one deeper descendant matches.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PART,
                            label="A",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2A",
                                    text="Deep subsection text.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _statute_ambiguous_depth_match() -> IRStatute:
    """Section 1 contains two parts, each with a subsection labelled 2A.

    Direct path 'section:1 / subsection:2A' fails, and two deeper
    descendants match — this should be treated as ambiguous.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PART,
                            label="A",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2A",
                                    text="First part subsection.",
                                ),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.PART,
                            label="B",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2A",
                                    text="Second part subsection.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _statute_direct_path_match() -> IRStatute:
    """Section 1 has a direct subsection 2A child — direct path succeeds."""
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2A",
                            text="Direct subsection text.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _replace_op_for_subsection_2a() -> LegalOperation:
    """Replace op targeting section:1 / subsection:2A."""
    return LegalOperation(
        op_id="uk-test-recursive-descent-replace",
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"), ("subsection", "2A"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2A", text="Replaced text."),
        source=_source(),
        sequence=1,
    )


# ---------------------------------------------------------------------------
# Test: unique recursive-descent recovery emits named adjudication
# ---------------------------------------------------------------------------

def test_unique_recursive_descent_emits_adjudication_and_applies() -> None:
    """Single deeper match: recovery succeeds and adjudication is emitted."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_single_depth_match()
    op = _replace_op_for_subsection_2a()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    # Recovery should have been applied
    body_section = result.body.children[0]
    part = body_section.children[0]
    recovered_node = part.children[0]
    assert recovered_node.text == "Replaced text.", (
        f"Expected replacement text but got {recovered_node.text!r}"
    )

    # Named adjudication must be emitted
    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID in rule_ids, (
        f"Expected {UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID!r} in {rule_ids!r}"
    )

    # Adjudication metadata
    recovery_adj = next(
        a for a in adjudications
        if a.kind == UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID
    )
    assert recovery_adj.detail["blocking"] is False
    assert recovery_adj.detail["strict_disposition"] == "block"
    assert recovery_adj.detail["quirks_disposition"] == "apply"
    assert recovery_adj.detail["family"] == "target_resolution_recovery"
    assert recovery_adj.detail["phase"] == "replay"
    target_resolution = recovery_adj.detail["target_resolution"]
    assert target_resolution["target_resolution_status"] == "recovered"
    assert target_resolution["source_target"] == "section:1/subsection:2A"
    assert target_resolution["selected_target"] == "section:1/part:A/subsection:2A"
    assert target_resolution["scope_confidence"] == "fallback"


# ---------------------------------------------------------------------------
# Test: strict mode — unique recovery adjudication carries strict_disposition=block
# ---------------------------------------------------------------------------

def test_unique_recursive_descent_adjudication_carries_strict_block() -> None:
    """The adjudication emitted for unique recovery has strict_disposition=block.

    The UK replay executor does not currently short-circuit on strict_disposition
    (that is caller policy), but the adjudication must carry the correct metadata
    so strict-mode callers can gate on it.
    """
    adjudications: list[CompileAdjudication] = []
    statute = _statute_single_depth_match()
    op = _replace_op_for_subsection_2a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    recovery_adj = next(
        (a for a in adjudications
         if a.kind == UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID),
        None,
    )
    assert recovery_adj is not None, "Recovery adjudication must be emitted"
    # strict_disposition=block is the gating signal for strict-mode callers
    assert recovery_adj.detail["strict_disposition"] == "block", (
        f"Expected strict_disposition=block, got {recovery_adj.detail['strict_disposition']!r}"
    )
    assert recovery_adj.detail["blocking"] is False, (
        "In quirks mode the adjudication must be non-blocking"
    )


# ---------------------------------------------------------------------------
# Test: ambiguous case refuses and emits blocking adjudication
# ---------------------------------------------------------------------------

def test_ambiguous_recursive_descent_refuses_and_emits_adjudication() -> None:
    """Multiple deeper matches: no recovery, blocking adjudication emitted."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_ambiguous_depth_match()
    op = _replace_op_for_subsection_2a()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    # Neither subsection 2A should have been replaced — no apply happened
    body_section = result.body.children[0]
    for part in body_section.children:
        for child in part.children:
            assert child.text != "Replaced text.", (
                f"Unexpected replacement in {child.label!r}: {child.text!r}"
            )

    # Blocking ambiguity adjudication must be emitted
    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID in rule_ids, (
        f"Expected {UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID!r} in {rule_ids!r}"
    )

    ambig_adj = next(
        a for a in adjudications
        if a.kind == UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID
    )
    # Ambiguous case is always blocking regardless of mode
    assert ambig_adj.detail["blocking"] is True
    assert ambig_adj.detail["family"] == "target_resolution_recovery"
    assert ambig_adj.detail["candidate_count"] == 2
    assert ambig_adj.detail["phase"] == "replay"
    target_resolution = ambig_adj.detail["target_resolution"]
    assert target_resolution["target_resolution_status"] == "ambiguous"
    assert target_resolution["source_target"] == "section:1/subsection:2A"
    assert target_resolution["candidate_count"] == 2
    assert {row["target"] for row in target_resolution["target_candidates"]} == {
        "section:1/part:A/subsection:2A",
        "section:1/part:B/subsection:2A",
    }


# ---------------------------------------------------------------------------
# Test: direct path match — NO recovery adjudication emitted (negative test)
# ---------------------------------------------------------------------------

def test_direct_path_match_emits_no_recursive_descent_adjudication() -> None:
    """When direct path resolves cleanly, no recursive-descent adjudication fires."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_direct_path_match()
    op = _replace_op_for_subsection_2a()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    # Replacement should succeed via direct path
    section = result.body.children[0]
    subsection = section.children[0]
    assert subsection.text == "Replaced text."

    # No recovery adjudications should be emitted
    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID not in rule_ids, (
        "Recovery adjudication must NOT fire when direct path resolves"
    )
    assert UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID not in rule_ids, (
        "Ambiguity adjudication must NOT fire when direct path resolves"
    )


# ---------------------------------------------------------------------------
# Test: 0 recursive matches — no adjudication, no apply (existing behavior)
# ---------------------------------------------------------------------------

def test_no_recursive_match_no_adjudication_emitted() -> None:
    """When recursive descent also finds nothing, no recovery adjudication fires."""
    adjudications: list[CompileAdjudication] = []
    # Statute with section 1 but no subsection 2A at any depth
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Sub three."),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    op = _replace_op_for_subsection_2a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID not in rule_ids
    assert UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID not in rule_ids


# ---------------------------------------------------------------------------
# Test: recovery adjudication includes witness fields
# ---------------------------------------------------------------------------

def test_recursive_descent_adjudication_includes_witness_fields() -> None:
    """Recovery adjudication records original target path and recovered path step."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_single_depth_match()
    op = _replace_op_for_subsection_2a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    recovery_adj = next(
        (a for a in adjudications
         if a.kind == UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID),
        None,
    )
    assert recovery_adj is not None

    detail = recovery_adj.detail
    assert "original_target_path" in detail, "Must record original target path"
    assert "recovered_path_step_kind" in detail, "Must record recovered step kind"
    assert "recovered_path_step_label" in detail, "Must record recovered step label"
    assert detail["recovered_path_step_kind"] == "subsection"
    assert detail["recovered_path_step_label"] == "2A"
    assert detail["recovered_target"] == "section:1/part:A/subsection:2A"


# ---------------------------------------------------------------------------
# Test: UKReplayExecutor._find_node_by_target returns None for ambiguous case
# ---------------------------------------------------------------------------

def test_executor_find_node_by_target_returns_none_for_ambiguous() -> None:
    """Directly test that _find_node_by_target returns None when ambiguous."""
    statute = _statute_ambiguous_depth_match()
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    target = LegalAddress(path=(("section", "1"), ("subsection", "2A")))
    op = _replace_op_for_subsection_2a()
    result = executor._find_node_by_target(
        target,
        allow_recursive_match=True,
        target_resolution_op=op,
    )

    assert result.node is None, (
        f"Expected None node for ambiguous match, got {result.node!r}"
    )
    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID in rule_ids

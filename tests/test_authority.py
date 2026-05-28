from __future__ import annotations

import pytest

from lawvm.core.authority import (
    BranchContext,
    BranchGraphEdge,
    BranchLifecycleEvent,
    LegalBranch,
    PROPOSAL_AUTHORITY,
    UNKNOWN_STATUS,
    branch_edge_kind_for_action,
    branch_graph_edge_from_operation,
    branch_graph_edges_from_operations,
    enacted_materialization_ops,
    branch_materialization_ops,
    branch_overlay_materialization_ops,
)
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction


def _op(op_id: str, *, source: OperationSource | None = None) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "1"),)),
        source=source,
    )


def _branch_source() -> OperationSource:
    return OperationSource(
        statute_id="proposal/example/2026/1",
        authority_layer=PROPOSAL_AUTHORITY,
        legal_status=UNKNOWN_STATUS,
        branch_id="proposal:example:2026-1",
        scenario_id="if_enacted_as_introduced",
    )


def test_default_operation_source_is_enacted_materialization_context() -> None:
    enacted = _op("enacted", source=OperationSource(statute_id="2025/1"))
    sourceless = _op("sourceless")

    assert enacted_materialization_ops([enacted, sourceless]) == (enacted, sourceless)


def test_proposal_operation_does_not_leak_into_enacted_materialization() -> None:
    enacted = _op("enacted", source=OperationSource(statute_id="2025/1"))
    proposal = _op("proposal", source=_branch_source())
    other_proposal = _op(
        "other-proposal",
        source=OperationSource(
            statute_id="proposal/example/2026/2",
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=UNKNOWN_STATUS,
            branch_id="proposal:example:2026-2",
            scenario_id="if_enacted_as_introduced",
        ),
    )
    context = BranchContext(
        authority_layer=PROPOSAL_AUTHORITY,
        legal_status=UNKNOWN_STATUS,
        branch_id="proposal:example:2026-1",
        scenario_id="if_enacted_as_introduced",
    )

    ops = [enacted, proposal, other_proposal]

    assert enacted_materialization_ops(ops) == (enacted,)
    assert branch_materialization_ops(ops, context) == (proposal,)
    assert branch_overlay_materialization_ops(ops, context) == (enacted, proposal)


def test_branch_overlay_default_context_is_enacted_lane() -> None:
    enacted = _op("enacted", source=OperationSource(statute_id="2025/1"))
    proposal = _op("proposal", source=_branch_source())

    assert branch_overlay_materialization_ops([enacted, proposal], BranchContext()) == (enacted,)


def test_non_enacted_branch_context_requires_branch_id() -> None:
    with pytest.raises(ValueError, match="requires a branch_id"):
        BranchContext(authority_layer=PROPOSAL_AUTHORITY)


def test_operation_source_validates_branch_context_at_provenance_boundary() -> None:
    with pytest.raises(ValueError, match="requires a branch_id"):
        OperationSource(
            statute_id="proposal/example/2026/1",
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=UNKNOWN_STATUS,
        )


def test_terminated_branch_requires_terminating_source() -> None:
    with pytest.raises(ValueError, match="requires terminated_by"):
        LegalBranch(
            branch_id="proposal:example:dead",
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status="failed",
        )


def test_branch_graph_edge_projects_branch_export_shape() -> None:
    edge = BranchGraphEdge(
        branch_id="proposal:example:2026-1",
        edge_kind="would_amend",
        scenario_id="if_enacted_as_introduced",
        source_artifact_id="proposal/example/2026/1",
        source_statute_id="proposal/example/2026/1",
        source_unit_id="clause:1",
        target_statute_id="2025/1",
        target_address="section:1",
        operation_id="proposal-op-1",
        authority_layer=PROPOSAL_AUTHORITY,
        legal_status=UNKNOWN_STATUS,
    )

    assert edge.to_dict() == {
        "branch_id": "proposal:example:2026-1",
        "edge_kind": "would_amend",
        "scenario_id": "if_enacted_as_introduced",
        "source_artifact_id": "proposal/example/2026/1",
        "source_statute_id": "proposal/example/2026/1",
        "source_unit_id": "clause:1",
        "target_statute_id": "2025/1",
        "target_address": "section:1",
        "operation_id": "proposal-op-1",
        "authority_layer": "proposal",
        "legal_status": "unknown",
    }


def test_branch_graph_edge_from_operation_ignores_default_enacted_ops() -> None:
    op = _op("enacted-op", source=OperationSource(statute_id="2025/1"))

    assert branch_graph_edge_from_operation(op, target_statute_id="base/1") is None


def test_branch_graph_edge_from_operation_projects_non_enacted_op() -> None:
    op = _op("proposal-op-1", source=_branch_source())

    edge = branch_graph_edge_from_operation(op, target_statute_id="base/1")

    assert edge is not None
    assert edge.edge_kind == "would_repeal"
    assert edge.branch_id == "proposal:example:2026-1"
    assert edge.source_artifact_id == "proposal/example/2026/1"
    assert edge.scenario_id == "if_enacted_as_introduced"
    assert edge.target_statute_id == "base/1"
    assert edge.target_address == "section:1"
    assert edge.operation_id == "proposal-op-1"


def test_branch_graph_edges_from_operations_filters_enacted_ops() -> None:
    enacted = _op("enacted-op", source=OperationSource(statute_id="2025/1"))
    proposal = _op("proposal-op-1", source=_branch_source())

    edges = branch_graph_edges_from_operations(
        [enacted, proposal],
        target_statute_id="base/1",
    )

    assert len(edges) == 1
    assert edges[0].operation_id == "proposal-op-1"


def test_branch_edge_kind_for_action_maps_structural_actions() -> None:
    assert branch_edge_kind_for_action(StructuralAction.INSERT) == "would_insert"
    assert branch_edge_kind_for_action(StructuralAction.REPLACE) == "would_replace"
    assert branch_edge_kind_for_action(StructuralAction.TEXT_REPLACE) == "would_replace"
    assert branch_edge_kind_for_action(StructuralAction.REPEAL) == "would_repeal"
    assert branch_edge_kind_for_action(StructuralAction.TEXT_REPEAL) == "would_repeal"
    assert branch_edge_kind_for_action(StructuralAction.RENUMBER) == "would_amend"


def test_branch_lifecycle_event_projects_non_mutating_status_fact() -> None:
    event = BranchLifecycleEvent(
        event_id="event-1",
        branch_id="proposal:example:2026-1",
        event_kind="withdrawn",
        source_artifact_id="proposal/example/2026/1",
        event_date="2026-05-01",
        resulting_status="withdrawn",
    )

    assert event.to_dict() == {
        "event_id": "event-1",
        "branch_id": "proposal:example:2026-1",
        "event_kind": "withdrawn",
        "source_artifact_id": "proposal/example/2026/1",
        "event_date": "2026-05-01",
        "resulting_status": "withdrawn",
        "derived_enacted_source_id": "",
    }


def test_branch_lifecycle_enacted_event_requires_derived_source() -> None:
    with pytest.raises(ValueError, match="derived_enacted_source_id"):
        BranchLifecycleEvent(
            event_id="event-1",
            branch_id="proposal:example:2026-1",
            event_kind="enacted",
        )

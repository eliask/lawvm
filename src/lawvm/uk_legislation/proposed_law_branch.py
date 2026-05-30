"""UK proposed-law branch graph prototype.

This module intentionally accepts a small structured payload rather than parsing
UK bill sources. It proves the UK frontend can project proposed/draft claims
into the shared branch graph without promoting them into enacted replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from lawvm.core.authority import (
    BRANCH_FAILED,
    BRANCH_INTRODUCED,
    BranchGraphEdge,
    BranchLifecycleEvent,
    LegalBranch,
    LegalStatus,
    PROPOSAL_AUTHORITY,
    UNKNOWN_STATUS,
    branch_graph_edge_from_operation,
    branch_materialization_ops,
    branch_overlay_materialization_ops,
    enacted_materialization_ops,
)
from lawvm.core.branch_projection import (
    BranchImpactProjection,
    branch_impact_projection_from_edges,
    enrich_branch_impact_projection_texts,
)
from lawvm.core.graph import CorpusGraph
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction


@dataclass(frozen=True)
class UKProposedLawOperationSpec:
    """One structured proposed-law claim against an enacted UK target."""

    operation_id: str
    source_unit_id: str
    target_statute_id: str
    target: LegalAddress
    action: StructuralAction
    proposed_node: IRNode | None = None
    current_text: str = ""
    proposed_text: str = ""

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise ValueError("UKProposedLawOperationSpec.operation_id must be non-empty")
        if not self.source_unit_id:
            raise ValueError("UKProposedLawOperationSpec.source_unit_id must be non-empty")
        if not self.target_statute_id:
            raise ValueError("UKProposedLawOperationSpec.target_statute_id must be non-empty")


@dataclass(frozen=True)
class UKProposedLawBranchPayload:
    """Graph-only UK proposed-law branch payload."""

    branch: LegalBranch
    operations: tuple[LegalOperation, ...]
    branch_edges: tuple[BranchGraphEdge, ...]
    lifecycle_events: tuple[BranchLifecycleEvent, ...]
    impact_projection: BranchImpactProjection
    graph: CorpusGraph

    def to_dict(self) -> dict[str, object]:
        return {
            "branch": self.branch.to_dict(),
            "default_enacted_operation_ids": tuple(
                op.op_id for op in enacted_materialization_ops(self.operations)
            ),
            "branch_operation_ids": tuple(
                op.op_id for op in branch_materialization_ops(self.operations, self.branch.to_context())
            ),
            "branch_overlay_operation_ids": tuple(
                op.op_id
                for op in branch_overlay_materialization_ops(self.operations, self.branch.to_context())
            ),
            "branch_edges": tuple(edge.to_dict() for edge in self.branch_edges),
            "branch_lifecycle_events": tuple(event.to_dict() for event in self.lifecycle_events),
            "impact_projection": self.impact_projection.to_dict(),
            "graph_counts": self.graph.to_wire_artifact().payload["counts"],
        }


def build_uk_proposed_law_branch_payload(
    *,
    source_artifact_id: str,
    title: str,
    specs: Sequence[UKProposedLawOperationSpec],
    branch_id: str = "",
    scenario_id: str = "if_enacted_as_introduced",
    introduced_date: str = "",
    legal_status: LegalStatus = UNKNOWN_STATUS,
    failed_event_id: str = "",
    failed_date: str = "",
) -> UKProposedLawBranchPayload:
    """Build a UK proposed-law graph payload from structured operation specs."""

    if not source_artifact_id:
        raise ValueError("source_artifact_id must be non-empty")
    if not specs:
        raise ValueError("at least one proposed-law operation spec is required")
    normalized_branch_id = branch_id or f"proposal:uk:{_slug(source_artifact_id)}"
    branch = LegalBranch(
        branch_id=normalized_branch_id,
        authority_layer=PROPOSAL_AUTHORITY,
        legal_status=legal_status,
        scenario_id=scenario_id,
        source_artifact_id=source_artifact_id,
        title=title,
        terminated_by=failed_event_id if legal_status == "failed" else "",
    )
    operations = tuple(
        _operation_from_spec(
            spec,
            source_artifact_id=source_artifact_id,
            branch=branch,
            sequence=index + 1,
        )
        for index, spec in enumerate(specs)
    )
    branch_edges = tuple(
        edge
        for spec, op in zip(specs, operations, strict=True)
        if (
            edge := branch_graph_edge_from_operation(
                op,
                target_statute_id=spec.target_statute_id,
                source_unit_id=spec.source_unit_id,
            )
        )
        is not None
    )
    lifecycle_events = _lifecycle_events(
        branch=branch,
        source_artifact_id=source_artifact_id,
        introduced_date=introduced_date,
        failed_event_id=failed_event_id,
        failed_date=failed_date,
    )
    projection = enrich_branch_impact_projection_texts(
        branch_impact_projection_from_edges(
            branch,
            branch_edges,
            message="UK proposed-law branch impact projection.",
        ),
        current_text_by_target=_text_map(specs, current=True),
        branch_text_by_target=_text_map(specs, current=False),
    )
    graph = CorpusGraph(
        branches=(branch,),
        branch_edges=branch_edges,
        branch_lifecycle_events=lifecycle_events,
    )
    return UKProposedLawBranchPayload(
        branch=branch,
        operations=operations,
        branch_edges=branch_edges,
        lifecycle_events=lifecycle_events,
        impact_projection=projection,
        graph=graph,
    )


def build_uk_proposed_law_demo_payload() -> UKProposedLawBranchPayload:
    """Return a tiny UK-shaped proposed-law payload for CLI/tests."""

    return build_uk_proposed_law_branch_payload(
        source_artifact_id="uk/bill/2026/example-bill",
        title="Example UK Draft Bill",
        introduced_date="2026-01-15",
        specs=(
            UKProposedLawOperationSpec(
                operation_id="uk-proposal-op-1",
                source_unit_id="clause:1",
                target_statute_id="ukpga/1978/30",
                target=LegalAddress(path=(("section", "1"),)),
                action=StructuralAction.REPLACE,
                proposed_node=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Proposed replacement text for section 1.",
                ),
                current_text="Current enacted text for section 1.",
                proposed_text="Proposed replacement text for section 1.",
            ),
        ),
    )


def _operation_from_spec(
    spec: UKProposedLawOperationSpec,
    *,
    source_artifact_id: str,
    branch: LegalBranch,
    sequence: int,
) -> LegalOperation:
    return LegalOperation(
        op_id=spec.operation_id,
        sequence=sequence,
        action=spec.action,
        target=spec.target,
        payload=spec.proposed_node,
        group_id=spec.source_unit_id,
        source=OperationSource(
            statute_id=source_artifact_id,
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=branch.legal_status,
            branch_id=branch.branch_id,
            scenario_id=branch.scenario_id,
        ),
    )


def _lifecycle_events(
    *,
    branch: LegalBranch,
    source_artifact_id: str,
    introduced_date: str,
    failed_event_id: str,
    failed_date: str,
) -> tuple[BranchLifecycleEvent, ...]:
    events = [
        BranchLifecycleEvent(
            event_id=f"{branch.branch_id}:introduced",
            branch_id=branch.branch_id,
            event_kind=BRANCH_INTRODUCED,
            scenario_id=branch.scenario_id,
            source_artifact_id=source_artifact_id,
            event_date=introduced_date,
            resulting_status=branch.legal_status,
        )
    ]
    if failed_event_id:
        events.append(
            BranchLifecycleEvent(
                event_id=failed_event_id,
                branch_id=branch.branch_id,
                event_kind=BRANCH_FAILED,
                scenario_id=branch.scenario_id,
                source_artifact_id=source_artifact_id,
                event_date=failed_date,
                resulting_status="failed",
            )
        )
    return tuple(events)


def _text_map(
    specs: Sequence[UKProposedLawOperationSpec],
    *,
    current: bool,
) -> Mapping[str, str]:
    return {
        f"{spec.target_statute_id}#{spec.target}": spec.current_text if current else spec.proposed_text
        for spec in specs
    }


def _slug(value: str) -> str:
    return "-".join(part for part in value.replace("_", "-").replace("/", "-").split("-") if part)

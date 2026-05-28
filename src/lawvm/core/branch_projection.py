"""Shared projection contracts for branch/proposal impact views."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from lawvm.core.authority import BranchGraphEdge, LegalBranch, branch_graph_edges_from_operations
from lawvm.core.frozen_values import freeze_mapping

if TYPE_CHECKING:
    from lawvm.core.ir import LegalOperation


@dataclass(frozen=True)
class BranchImpactRow:
    """One branch-local effect projected for UI/API consumers."""

    row_id: str
    branch_id: str
    edge_kind: str
    target_statute_id: str
    scenario_id: str = ""
    target_address: str = ""
    operation_id: str = ""
    source_artifact_id: str = ""
    source_unit_id: str = ""
    current_text: str = ""
    branch_text: str = ""
    status: str = "projected"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.row_id:
            raise ValueError("BranchImpactRow.row_id must be non-empty")
        if not self.branch_id:
            raise ValueError("BranchImpactRow.branch_id must be non-empty")
        if not self.edge_kind:
            raise ValueError("BranchImpactRow.edge_kind must be non-empty")
        if not self.target_statute_id:
            raise ValueError("BranchImpactRow.target_statute_id must be non-empty")
        if not self.status:
            raise ValueError("BranchImpactRow.status must be non-empty")
        if not isinstance(self.detail, Mapping):
            raise ValueError("BranchImpactRow.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = dict(self.detail)
        return data


@dataclass(frozen=True)
class BranchImpactProjection:
    """Branch/proposal impact projection without enacted-state mutation claims."""

    branch: LegalBranch
    rows: tuple[BranchImpactRow, ...] = ()
    status: str = "ok"
    message: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.branch, LegalBranch):
            raise ValueError("BranchImpactProjection.branch must be a LegalBranch")
        if not self.status:
            raise ValueError("BranchImpactProjection.status must be non-empty")
        rows = tuple(self.rows)
        if not all(isinstance(row, BranchImpactRow) for row in rows):
            raise ValueError("BranchImpactProjection.rows must contain BranchImpactRow records")
        mismatched_rows = tuple(
            row.row_id
            for row in rows
            if row.branch_id != self.branch.branch_id
            or ((row.scenario_id or self.branch.scenario_id) and row.scenario_id != self.branch.scenario_id)
        )
        if mismatched_rows:
            raise ValueError(
                "BranchImpactProjection.rows must match the projection branch_id and scenario_id: "
                f"{', '.join(mismatched_rows)}"
            )
        object.__setattr__(self, "rows", rows)
        if not isinstance(self.detail, Mapping):
            raise ValueError("BranchImpactProjection.detail must be a mapping")
        object.__setattr__(self, "detail", freeze_mapping(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "status": self.status,
            "message": self.message,
            "detail": dict(self.detail),
        }


def branch_impact_projection_from_edges(
    branch: LegalBranch,
    edges: Sequence[BranchGraphEdge],
    *,
    status: str = "ok",
    message: str = "",
) -> BranchImpactProjection:
    """Build a branch impact projection from graph edges for one branch."""

    rows = tuple(
        BranchImpactRow(
            row_id=_branch_impact_row_id(edge, index),
            branch_id=edge.branch_id,
            edge_kind=edge.edge_kind,
            scenario_id=edge.scenario_id,
            target_statute_id=edge.target_statute_id,
            target_address=edge.target_address,
            operation_id=edge.operation_id,
            source_artifact_id=edge.source_artifact_id,
            source_unit_id=edge.source_unit_id,
        )
        for index, edge in enumerate(_selected_branch_edges(branch, edges))
    )
    return BranchImpactProjection(
        branch=branch,
        rows=rows,
        status=status,
        message=message,
    )


def branch_impact_projection_from_operations(
    branch: LegalBranch,
    ops: Sequence["LegalOperation"],
    *,
    target_statute_id: str,
    status: str = "ok",
    message: str = "",
) -> BranchImpactProjection:
    """Build a branch impact projection from typed non-enacted operations."""

    return branch_impact_projection_from_edges(
        branch,
        branch_graph_edges_from_operations(ops, target_statute_id=target_statute_id),
        status=status,
        message=message,
    )


def enrich_branch_impact_projection_texts(
    projection: BranchImpactProjection,
    *,
    current_text_by_target: Mapping[str, str],
    branch_text_by_target: Mapping[str, str],
) -> BranchImpactProjection:
    """Attach frontend-supplied current/branch text to projection rows.

    Keys are ``target_statute_id + "#" + target_address``. Missing entries are
    left empty; this helper performs no source lookup and makes no enacted-state
    claim.
    """

    rows = tuple(
        BranchImpactRow(
            row_id=row.row_id,
            branch_id=row.branch_id,
            edge_kind=row.edge_kind,
            scenario_id=row.scenario_id,
            target_statute_id=row.target_statute_id,
            target_address=row.target_address,
            operation_id=row.operation_id,
            source_artifact_id=row.source_artifact_id,
            source_unit_id=row.source_unit_id,
            current_text=current_text_by_target.get(_target_key(row), row.current_text),
            branch_text=branch_text_by_target.get(_target_key(row), row.branch_text),
            status=row.status,
            detail=row.detail,
        )
        for row in projection.rows
    )
    return BranchImpactProjection(
        branch=projection.branch,
        rows=rows,
        status=projection.status,
        message=projection.message,
        detail=projection.detail,
    )


def _selected_branch_edges(
    branch: LegalBranch,
    edges: Sequence[BranchGraphEdge],
) -> tuple[BranchGraphEdge, ...]:
    return tuple(
        sorted(
            (
                edge
                for edge in edges
                if edge.branch_id == branch.branch_id
                and edge.scenario_id == branch.scenario_id
            ),
            key=lambda edge: (
                edge.target_statute_id,
                edge.target_address,
                edge.edge_kind,
                edge.scenario_id,
                edge.source_artifact_id,
                edge.source_unit_id,
                edge.operation_id,
            ),
        )
    )


def _branch_impact_row_id(edge: BranchGraphEdge, index: int) -> str:
    suffix = edge.operation_id or edge.source_unit_id or str(index + 1)
    if edge.scenario_id:
        return f"{edge.branch_id}:{edge.scenario_id}:{edge.edge_kind}:{suffix}"
    return f"{edge.branch_id}:{edge.edge_kind}:{suffix}"


def _target_key(row: BranchImpactRow) -> str:
    return f"{row.target_statute_id}#{row.target_address}"

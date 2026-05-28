from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.authority import BranchGraphEdge, LegalBranch
from lawvm.core.branch_projection import (
    BranchImpactProjection,
    BranchImpactRow,
    branch_impact_projection_from_edges,
    branch_impact_projection_from_operations,
    enrich_branch_impact_projection_texts,
)
from lawvm.core.frozen_values import FrozenDict
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import StructuralAction


def _branch_source(branch_id: str) -> OperationSource:
    return OperationSource(
        statute_id="proposal/example/2026/1",
        authority_layer="proposal",
        legal_status="unknown",
        branch_id=branch_id,
        scenario_id="if_enacted_as_introduced",
    )


def _operation(
    op_id: str,
    *,
    action: StructuralAction,
    source: OperationSource,
    section: str = "1",
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=action,
        target=LegalAddress(path=(("section", section),)),
        source=source,
    )


def test_branch_impact_projection_from_edges_filters_and_orders_branch_rows() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        scenario_id="if_enacted_as_introduced",
        source_artifact_id="proposal/example/2026/1",
    )
    other_branch = LegalBranch(
        branch_id="proposal:example:2026-2",
        authority_layer="proposal",
        source_artifact_id="proposal/example/2026/2",
    )
    later_edge = BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind="would_replace",
        scenario_id=branch.scenario_id,
        source_artifact_id="proposal/example/2026/1",
        source_unit_id="clause:2",
        target_statute_id="base/1",
        target_address="section:2",
        operation_id="op-2",
    )
    earlier_edge = BranchGraphEdge(
        branch_id=branch.branch_id,
        edge_kind="would_insert",
        scenario_id=branch.scenario_id,
        source_artifact_id="proposal/example/2026/1",
        source_unit_id="clause:1",
        target_statute_id="base/1",
        target_address="section:1",
        operation_id="op-1",
    )
    ignored_edge = BranchGraphEdge(
        branch_id=other_branch.branch_id,
        edge_kind="would_repeal",
        target_statute_id="base/1",
        operation_id="op-other",
    )

    projection = branch_impact_projection_from_edges(
        branch,
        [later_edge, ignored_edge, earlier_edge],
    )
    data = projection.to_dict()

    assert [row.row_id for row in projection.rows] == [
        "proposal:example:2026-1:if_enacted_as_introduced:would_insert:op-1",
        "proposal:example:2026-1:if_enacted_as_introduced:would_replace:op-2",
    ]
    assert data["branch"]["branch_id"] == "proposal:example:2026-1"
    assert data["rows"][0]["scenario_id"] == "if_enacted_as_introduced"
    assert data["rows"][0]["target_address"] == "section:1"
    assert data["rows"][1]["source_unit_id"] == "clause:2"


def test_branch_impact_row_requires_target_statute() -> None:
    with pytest.raises(ValueError, match="target_statute_id"):
        BranchImpactRow(
            row_id="row-1",
            branch_id="proposal:example:2026-1",
            edge_kind="would_amend",
            target_statute_id="",
        )


def test_branch_impact_row_requires_status_and_mapping_detail() -> None:
    with pytest.raises(ValueError, match="status"):
        BranchImpactRow(
            row_id="row-1",
            branch_id="proposal:example:2026-1",
            edge_kind="would_amend",
            target_statute_id="base/1",
            status="",
        )


def test_branch_impact_projection_validates_envelope() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        source_artifact_id="proposal/example/2026/1",
    )

    with pytest.raises(ValueError, match="status"):
        BranchImpactProjection(branch=branch, status="")


def test_branch_impact_projection_normalizes_rows_and_freezes_detail() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        source_artifact_id="proposal/example/2026/1",
    )
    row_detail = {"witnesses": ["source"]}
    row = BranchImpactRow(
        row_id="row-1",
        branch_id=branch.branch_id,
        edge_kind="would_amend",
        target_statute_id="base/1",
        detail=row_detail,
    )
    rows = [row]

    projection = BranchImpactProjection(
        branch=branch,
        rows=cast(Any, rows),
        detail={"projection": {"ids": ["row-1"]}},
    )

    rows.clear()
    row_detail["witnesses"].append("mutated")

    assert projection.rows == (row,)
    assert isinstance(row.detail, FrozenDict)
    assert row.detail["witnesses"] == ("source",)
    assert projection.detail["projection"]["ids"] == ("row-1",)


def test_branch_impact_projection_from_operations_uses_branch_edge_mapping() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        scenario_id="if_enacted_as_introduced",
        source_artifact_id="proposal/example/2026/1",
    )
    source = _branch_source(branch.branch_id)
    projection = branch_impact_projection_from_operations(
        branch,
        (
            _operation("op-replace", action=StructuralAction.REPLACE, source=source),
            _operation("op-insert", action=StructuralAction.INSERT, source=source, section="2"),
        ),
        target_statute_id="base/1",
    )

    assert [(row.edge_kind, row.operation_id, row.target_address) for row in projection.rows] == [
        ("would_replace", "op-replace", "section:1"),
        ("would_insert", "op-insert", "section:2"),
    ]
    assert {row.scenario_id for row in projection.rows} == {"if_enacted_as_introduced"}


def test_enrich_branch_impact_projection_texts_uses_target_keys_without_lookup() -> None:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer="proposal",
        source_artifact_id="proposal/example/2026/1",
    )
    projection = branch_impact_projection_from_edges(
        branch,
        (
            BranchGraphEdge(
                branch_id=branch.branch_id,
                edge_kind="would_replace",
                target_statute_id="base/1",
                target_address="section:1",
                operation_id="op-1",
            ),
        ),
    )

    enriched = enrich_branch_impact_projection_texts(
        projection,
        current_text_by_target={"base/1#section:1": "current text"},
        branch_text_by_target={"base/1#section:1": "branch text"},
    )

    assert enriched.rows[0].current_text == "current text"
    assert enriched.rows[0].branch_text == "branch text"
    assert projection.rows[0].current_text == ""

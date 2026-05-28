"""Synthetic branch/authority demo payload.

This command intentionally uses a tiny constructed example. It demonstrates
that proposal operations can be represented, projected, and kept out of the
default enacted operation lane without requiring a jurisdiction-specific
proposal parser.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from lawvm.core.authority import (
    PROPOSAL_AUTHORITY,
    UNKNOWN_STATUS,
    LegalBranch,
    branch_materialization_ops,
    enacted_materialization_ops,
)
from lawvm.core.branch_projection import (
    branch_impact_projection_from_operations,
    enrich_branch_impact_projection_texts,
)
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction


def build_branch_demo_payload() -> dict[str, Any]:
    branch = LegalBranch(
        branch_id="proposal:example:2026-1",
        authority_layer=PROPOSAL_AUTHORITY,
        legal_status=UNKNOWN_STATUS,
        scenario_id="if_enacted_as_introduced",
        source_artifact_id="proposal/example/2026/1",
        title="Example proposal branch",
    )
    enacted_op = LegalOperation(
        op_id="enacted-op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Current enacted text."),
        source=OperationSource(statute_id="base/1"),
    )
    proposal_op = LegalOperation(
        op_id="proposal-op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Proposed branch text."),
        source=OperationSource(
            statute_id="proposal/example/2026/1",
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=UNKNOWN_STATUS,
            branch_id=branch.branch_id,
            scenario_id=branch.scenario_id,
        ),
    )
    ops = (enacted_op, proposal_op)
    projection = enrich_branch_impact_projection_texts(
        branch_impact_projection_from_operations(
            branch,
            ops,
            target_statute_id="base/1",
            message="Synthetic proposal branch impact projection.",
        ),
        current_text_by_target={"base/1#section:1": "Current enacted text."},
        branch_text_by_target={"base/1#section:1": "Proposed branch text."},
    )
    return {
        "branch": branch.to_dict(),
        "default_enacted_operation_ids": tuple(op.op_id for op in enacted_materialization_ops(ops)),
        "branch_operation_ids": tuple(op.op_id for op in branch_materialization_ops(ops, branch.to_context())),
        "impact_projection": projection.to_dict(),
    }


def main(args: object) -> None:
    indent = 2 if getattr(args, "pretty", False) else None
    json.dump(build_branch_demo_payload(), sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")

"""Authority and branch context for legal-state claims.

The default LawVM replay lane is enacted law. Drafts, bills, proposals, and
consultation texts can still be represented as executable claims, but they must
carry an explicit non-enacted branch context so they cannot leak into ordinary
point-in-time materialization by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence

if TYPE_CHECKING:
    from lawvm.core.ir import LegalOperation
    from lawvm.core.semantic_types import StructuralAction


AuthorityLayer = Literal[
    "enacted",
    "proposal",
    "draft",
    "consultation",
    "editorial",
    "oracle",
]

LegalStatus = Literal[
    "commenced",
    "uncommenced",
    "pending_condition",
    "withdrawn",
    "failed",
    "superseded",
    "unknown",
]

BranchEdgeKind = Literal[
    "targets",
    "would_amend",
    "would_insert",
    "would_replace",
    "would_repeal",
    "derived_from",
    "terminated_by",
]

BranchLifecycleKind = Literal[
    "introduced",
    "amended",
    "passed",
    "withdrawn",
    "failed",
    "enacted",
    "superseded",
]

ENACTED_AUTHORITY: AuthorityLayer = "enacted"
PROPOSAL_AUTHORITY: AuthorityLayer = "proposal"
DRAFT_AUTHORITY: AuthorityLayer = "draft"
CONSULTATION_AUTHORITY: AuthorityLayer = "consultation"
EDITORIAL_AUTHORITY: AuthorityLayer = "editorial"
ORACLE_AUTHORITY: AuthorityLayer = "oracle"

COMMENCED_STATUS: LegalStatus = "commenced"
UNCOMMENCED_STATUS: LegalStatus = "uncommenced"
PENDING_CONDITION_STATUS: LegalStatus = "pending_condition"
WITHDRAWN_STATUS: LegalStatus = "withdrawn"
FAILED_STATUS: LegalStatus = "failed"
SUPERSEDED_STATUS: LegalStatus = "superseded"
UNKNOWN_STATUS: LegalStatus = "unknown"

TARGETS_EDGE: BranchEdgeKind = "targets"
WOULD_AMEND_EDGE: BranchEdgeKind = "would_amend"
WOULD_INSERT_EDGE: BranchEdgeKind = "would_insert"
WOULD_REPLACE_EDGE: BranchEdgeKind = "would_replace"
WOULD_REPEAL_EDGE: BranchEdgeKind = "would_repeal"
DERIVED_FROM_EDGE: BranchEdgeKind = "derived_from"
TERMINATED_BY_EDGE: BranchEdgeKind = "terminated_by"

BRANCH_INTRODUCED: BranchLifecycleKind = "introduced"
BRANCH_AMENDED: BranchLifecycleKind = "amended"
BRANCH_PASSED: BranchLifecycleKind = "passed"
BRANCH_WITHDRAWN: BranchLifecycleKind = "withdrawn"
BRANCH_FAILED: BranchLifecycleKind = "failed"
BRANCH_ENACTED: BranchLifecycleKind = "enacted"
BRANCH_SUPERSEDED: BranchLifecycleKind = "superseded"

NON_ENACTED_AUTHORITIES = frozenset({
    PROPOSAL_AUTHORITY,
    DRAFT_AUTHORITY,
    CONSULTATION_AUTHORITY,
})

_AUTHORITY_VALUES = frozenset({
    ENACTED_AUTHORITY,
    PROPOSAL_AUTHORITY,
    DRAFT_AUTHORITY,
    CONSULTATION_AUTHORITY,
    EDITORIAL_AUTHORITY,
    ORACLE_AUTHORITY,
})

_STATUS_VALUES = frozenset({
    COMMENCED_STATUS,
    UNCOMMENCED_STATUS,
    PENDING_CONDITION_STATUS,
    WITHDRAWN_STATUS,
    FAILED_STATUS,
    SUPERSEDED_STATUS,
    UNKNOWN_STATUS,
})

_BRANCH_EDGE_VALUES = frozenset({
    TARGETS_EDGE,
    WOULD_AMEND_EDGE,
    WOULD_INSERT_EDGE,
    WOULD_REPLACE_EDGE,
    WOULD_REPEAL_EDGE,
    DERIVED_FROM_EDGE,
    TERMINATED_BY_EDGE,
})

_BRANCH_LIFECYCLE_VALUES = frozenset({
    BRANCH_INTRODUCED,
    BRANCH_AMENDED,
    BRANCH_PASSED,
    BRANCH_WITHDRAWN,
    BRANCH_FAILED,
    BRANCH_ENACTED,
    BRANCH_SUPERSEDED,
})


@dataclass(frozen=True)
class BranchContext:
    """Authority/status selector for a legal graph or materialization query."""

    authority_layer: AuthorityLayer = ENACTED_AUTHORITY
    legal_status: LegalStatus = COMMENCED_STATUS
    branch_id: str = ""
    scenario_id: str = ""

    def __post_init__(self) -> None:
        if self.authority_layer not in _AUTHORITY_VALUES:
            raise ValueError(f"unsupported authority_layer: {self.authority_layer!r}")
        if self.legal_status not in _STATUS_VALUES:
            raise ValueError(f"unsupported legal_status: {self.legal_status!r}")
        if self.authority_layer in NON_ENACTED_AUTHORITIES and not self.branch_id:
            raise ValueError(
                f"authority_layer={self.authority_layer!r} requires a branch_id"
            )

    @property
    def is_enacted_default(self) -> bool:
        return (
            self.authority_layer == ENACTED_AUTHORITY
            and self.legal_status == COMMENCED_STATUS
            and not self.branch_id
            and not self.scenario_id
        )


DEFAULT_ENACTED_CONTEXT = BranchContext()


@dataclass(frozen=True)
class LegalBranch:
    """Named non-default branch in the legal-state graph."""

    branch_id: str
    authority_layer: AuthorityLayer
    legal_status: LegalStatus = UNKNOWN_STATUS
    scenario_id: str = ""
    parent_branch_id: str = ""
    source_artifact_id: str = ""
    title: str = ""
    terminated_by: str = ""

    def __post_init__(self) -> None:
        if not self.branch_id:
            raise ValueError("LegalBranch.branch_id must be non-empty")
        BranchContext(
            authority_layer=self.authority_layer,
            legal_status=self.legal_status,
            branch_id=self.branch_id,
            scenario_id=self.scenario_id,
        )
        if self.legal_status in {WITHDRAWN_STATUS, FAILED_STATUS, SUPERSEDED_STATUS} and not self.terminated_by:
            raise ValueError(
                f"LegalBranch(status={self.legal_status!r}) requires terminated_by"
            )

    def to_context(self) -> BranchContext:
        return BranchContext(
            authority_layer=self.authority_layer,
            legal_status=self.legal_status,
            branch_id=self.branch_id,
            scenario_id=self.scenario_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "branch_id": self.branch_id,
            "authority_layer": self.authority_layer,
            "legal_status": self.legal_status,
            "scenario_id": self.scenario_id,
            "parent_branch_id": self.parent_branch_id,
            "source_artifact_id": self.source_artifact_id,
            "title": self.title,
            "terminated_by": self.terminated_by,
        }


@dataclass(frozen=True)
class BranchGraphEdge:
    """Graph/export edge for branch-local or proposal-state claims."""

    branch_id: str
    edge_kind: BranchEdgeKind
    scenario_id: str = ""
    source_artifact_id: str = ""
    source_statute_id: str = ""
    source_unit_id: str = ""
    target_statute_id: str = ""
    target_address: str = ""
    operation_id: str = ""
    authority_layer: AuthorityLayer = PROPOSAL_AUTHORITY
    legal_status: LegalStatus = UNKNOWN_STATUS

    def __post_init__(self) -> None:
        if not self.branch_id:
            raise ValueError("BranchGraphEdge.branch_id must be non-empty")
        if self.edge_kind not in _BRANCH_EDGE_VALUES:
            raise ValueError(f"unsupported branch edge kind: {self.edge_kind!r}")
        BranchContext(
            authority_layer=self.authority_layer,
            legal_status=self.legal_status,
            branch_id=self.branch_id,
            scenario_id=self.scenario_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "branch_id": self.branch_id,
            "edge_kind": self.edge_kind,
            "scenario_id": self.scenario_id,
            "source_artifact_id": self.source_artifact_id,
            "source_statute_id": self.source_statute_id,
            "source_unit_id": self.source_unit_id,
            "target_statute_id": self.target_statute_id,
            "target_address": self.target_address,
            "operation_id": self.operation_id,
            "authority_layer": self.authority_layer,
            "legal_status": self.legal_status,
        }


@dataclass(frozen=True)
class BranchLifecycleEvent:
    """Lifecycle fact for a branch/proposal, not an enacted-state mutation."""

    event_id: str
    branch_id: str
    event_kind: BranchLifecycleKind
    scenario_id: str = ""
    source_artifact_id: str = ""
    event_date: str = ""
    resulting_status: LegalStatus = UNKNOWN_STATUS
    derived_enacted_source_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("BranchLifecycleEvent.event_id must be non-empty")
        if not self.branch_id:
            raise ValueError("BranchLifecycleEvent.branch_id must be non-empty")
        if self.event_kind not in _BRANCH_LIFECYCLE_VALUES:
            raise ValueError(f"unsupported branch lifecycle kind: {self.event_kind!r}")
        if self.resulting_status not in _STATUS_VALUES:
            raise ValueError(f"unsupported resulting_status: {self.resulting_status!r}")
        if self.event_kind in {BRANCH_ENACTED, BRANCH_SUPERSEDED} and not self.derived_enacted_source_id:
            raise ValueError(
                f"BranchLifecycleEvent(kind={self.event_kind!r}) requires derived_enacted_source_id"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "branch_id": self.branch_id,
            "event_kind": self.event_kind,
            "scenario_id": self.scenario_id,
            "source_artifact_id": self.source_artifact_id,
            "event_date": self.event_date,
            "resulting_status": self.resulting_status,
            "derived_enacted_source_id": self.derived_enacted_source_id,
        }


def branch_context_from_operation(op: "LegalOperation") -> BranchContext:
    source = op.source
    if source is None:
        return DEFAULT_ENACTED_CONTEXT
    return BranchContext(
        authority_layer=source.authority_layer,
        legal_status=source.legal_status,
        branch_id=source.branch_id,
        scenario_id=source.scenario_id,
    )


def operation_matches_branch_context(op: "LegalOperation", context: BranchContext) -> bool:
    return branch_context_from_operation(op) == context


def enacted_materialization_ops(ops: Sequence["LegalOperation"]) -> tuple["LegalOperation", ...]:
    """Return operations eligible for ordinary enacted/current materialization."""

    return tuple(op for op in ops if branch_context_from_operation(op).is_enacted_default)


def branch_materialization_ops(
    ops: Sequence["LegalOperation"],
    context: BranchContext,
) -> tuple["LegalOperation", ...]:
    """Return operations belonging to the requested legal branch context."""

    return tuple(op for op in ops if operation_matches_branch_context(op, context))


def branch_overlay_materialization_ops(
    ops: Sequence["LegalOperation"],
    context: BranchContext,
) -> tuple["LegalOperation", ...]:
    """Return default enacted operations plus the selected branch context.

    This is a selector only. It does not promote proposal/draft claims into
    enacted law; callers must explicitly pass a non-default branch context.
    """

    if context.is_enacted_default:
        return enacted_materialization_ops(ops)
    return tuple(
        op
        for op in ops
        if branch_context_from_operation(op).is_enacted_default
        or operation_matches_branch_context(op, context)
    )


def branch_graph_edge_from_operation(
    op: "LegalOperation",
    *,
    target_statute_id: str,
    source_unit_id: str = "",
) -> BranchGraphEdge | None:
    """Project a non-enacted branch operation into a graph/export edge.

    Default enacted operations return ``None`` because they are ordinary replay
    inputs, not proposal/draft branch claims.
    """

    context = branch_context_from_operation(op)
    if context.is_enacted_default:
        return None
    if not target_statute_id:
        raise ValueError("branch_graph_edge_from_operation requires target_statute_id")
    source = op.source
    return BranchGraphEdge(
        branch_id=context.branch_id,
        edge_kind=branch_edge_kind_for_action(op.action),
        scenario_id=context.scenario_id,
        source_artifact_id=source.statute_id if source is not None else "",
        source_statute_id=source.statute_id if source is not None else "",
        source_unit_id=source_unit_id or (op.group_id or ""),
        target_statute_id=target_statute_id,
        target_address=str(op.target),
        operation_id=op.op_id,
        authority_layer=context.authority_layer,
        legal_status=context.legal_status,
    )


def branch_graph_edges_from_operations(
    ops: Sequence["LegalOperation"],
    *,
    target_statute_id: str,
) -> tuple[BranchGraphEdge, ...]:
    """Project all non-enacted branch operations into graph/export edges."""

    return tuple(
        edge
        for op in ops
        if (edge := branch_graph_edge_from_operation(op, target_statute_id=target_statute_id))
        is not None
    )


def branch_edge_kind_for_action(action: "StructuralAction") -> BranchEdgeKind:
    """Map a core structural action to a conservative branch graph edge kind."""

    from lawvm.core.semantic_types import StructuralAction

    if action is StructuralAction.INSERT:
        return WOULD_INSERT_EDGE
    if action in {StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL}:
        return WOULD_REPEAL_EDGE
    if action in {
        StructuralAction.REPLACE,
        StructuralAction.HEADING_REPLACE,
        StructuralAction.TEXT_REPLACE,
    }:
        return WOULD_REPLACE_EDGE
    return WOULD_AMEND_EDGE

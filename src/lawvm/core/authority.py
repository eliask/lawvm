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
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "branch_id": self.branch_id,
            "edge_kind": self.edge_kind,
            "source_artifact_id": self.source_artifact_id,
            "source_statute_id": self.source_statute_id,
            "source_unit_id": self.source_unit_id,
            "target_statute_id": self.target_statute_id,
            "target_address": self.target_address,
            "operation_id": self.operation_id,
            "authority_layer": self.authority_layer,
            "legal_status": self.legal_status,
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

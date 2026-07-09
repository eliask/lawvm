"""Lower proposal carriers into the EXISTING replay/branch infrastructure.

A ``ProposalPackage`` (the draft-HE "if enacted, then …" product) names its
effects as product-facing ``CandidateOperation`` / ``ConditionalBranch``
carriers (``lawvm.core.source_document.proposal``). Those are DELIBERATELY not
replay ``LegalOperation`` objects — they describe a conditional effect, they do
not execute. This module projects them into the enacted-law infrastructure —
``core.ir.LegalOperation`` + ``core.branch_authority.{LegalBranch, BranchGraphEdge}``
— so a proposal can be VIEWED as "what would change if enacted" through the same
branch-materialization selectors the enacted lane uses, WITHOUT ever being
replay-authorized.

The hard invariant (proposal.py's authority-laundering countermeasure, the
review §9): the lowered ``LegalOperation`` carries an ``ExecutionAuthorization``
with ``executable=False`` and ``replay_authorized=False`` and lands on a
NON-ENACTED authority layer (``draft``). Only enactment flips that, through a
different lane — never this projector. The branch/edge carriers themselves live
on the ``proposal`` authority layer and model non-enacted ``would_amend`` edges
(``core.branch_authority.branch_edge_kind_for_action``), so nothing here can leak
into ordinary enacted point-in-time materialization.

Discipline (AGENTS.md §1.9, §1.10): typed carriers; the non-authorization
invariant is enforced by construction (the ``ExecutionAuthorization`` two-flag
waist), not merely documented.
"""
from __future__ import annotations

from typing import Tuple

from lawvm.core.branch_authority import (
    DRAFT_AUTHORITY,
    PENDING_CONDITION_STATUS,
    PROPOSAL_AUTHORITY,
    BranchGraphEdge,
    LegalBranch,
    branch_edge_kind_for_action,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import structural_action_from_str
from lawvm.core.source_document.proposal import CandidateOperation, ConditionalBranch

# The authorization rule under which a proposal op is projected — the firewall
# waist stamp that keeps the op OUT of the enacted replay lane. Named after the
# HE-draft producer so a receipt attributes the non-authorization to its source.
PROPOSAL_AUTHORIZATION_RULE_ID = "fi.he_draft.proposal"
PROPOSAL_OWNER_PHASE = "proposal"
PROPOSAL_AUTHORIZATION_STATUS = "proposal"
# A non-replay-authorized ExecutionAuthorization MUST list what would be needed
# to authorize it (``validate_execution_authorization``): enactment through the
# enactment lane, not this projector.
_PROPOSAL_REQUIRED_PROOFS: Tuple[str, ...] = ("enactment_through_enactment_lane",)
_PROPOSAL_SAFE_DEFAULT = "project_as_would_change_view_but_never_replay_a_proposal"
_PROPOSAL_FORBIDDEN_SHORTCUTS: Tuple[str, ...] = (
    "treat_proposal_candidate_op_as_enacted_replay_input",
)


def _parse_provision_ref(target_provision_ref: str) -> LegalAddress:
    """Parse a rendered provision locator into a ``LegalAddress``.

    ``target_provision_ref`` is the ``LegalAddress.__str__`` form the HE-draft
    lowerer stamped on the ``CandidateOperation`` (``he_draft.py`` sets it to
    ``str(op.target)``): path elements ``kind:label`` joined by ``/`` (e.g.
    ``"section:4/subsection:5"``). This is the inverse of that projection —
    split on ``/`` into path elements, each split once on ``:`` into a
    ``(kind, label)`` pair. An empty ref lowers to an empty-path address (the
    statute-root target — no provision resolved from the johtolause preamble).

    A facet/compartment suffix (``LegalAddress.__str__``'s ``/<special>`` or
    ``@<root>`` prefix) is not reconstructed here: the deterministic johtolause
    parser this projector consumes emits plain ``kind:label`` paths, so a
    round-trip of that surface is exact. A path element with no ``:`` (a bare
    label) fails loud rather than silently minting an empty-kind element (the
    ``LegalAddress`` ctor rejects an empty kind anyway — §1.10).
    """
    ref = target_provision_ref.strip()
    if not ref:
        return LegalAddress(path=())
    path: list[Tuple[str, str]] = []
    for element in ref.split("/"):
        if ":" not in element:
            raise ValueError(
                "CandidateOperation.target_provision_ref element "
                f"{element!r} is not a 'kind:label' pair (from {target_provision_ref!r})"
            )
        kind, label = element.split(":", 1)
        path.append((kind, label))
    return LegalAddress(path=tuple(path))


def _proposal_authorization() -> ExecutionAuthorization:
    """The firewall-waist proof stamped on every lowered proposal op.

    ``executable=False`` + ``replay_authorized=False`` is the two-flag promotion
    denial: this op may be VIEWED (a "would change" projection) but never mutate
    the enacted timeline. Enactment authorizes replay through a different lane.
    """
    return ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status=PROPOSAL_AUTHORIZATION_STATUS,
        authorization_rule_id=PROPOSAL_AUTHORIZATION_RULE_ID,
        owner_phase=PROPOSAL_OWNER_PHASE,
        strict_disposition="defer",
        required_proofs=_PROPOSAL_REQUIRED_PROOFS,
        safe_default=_PROPOSAL_SAFE_DEFAULT,
        forbidden_shortcuts=_PROPOSAL_FORBIDDEN_SHORTCUTS,
    )


def candidate_op_to_legal_operation(
    op: CandidateOperation,
    *,
    sequence: int,
) -> LegalOperation:
    """Project one ``CandidateOperation`` into a NON-authorized ``LegalOperation``.

    ``op.action`` maps to a ``StructuralAction`` via the shared boundary mapper
    (``structural_action_from_str`` — "insert" → ``INSERT`` etc.), and
    ``op.target_provision_ref`` parses back into the ``LegalAddress`` the
    johtolause parser originally emitted. The op carries an
    ``ExecutionAuthorization`` denying both executability and replay authority,
    so it can be materialized in a proposal-branch "would change" view but never
    enter the enacted replay lane (the proposal.py review §9 invariant).

    The op is intentionally payload-free: this is a branch-graph / "what would
    change" projection, not a text-materialization input. The verbatim
    ``raw_johtolause`` rides on ``LegalOperation.raw_text`` as evidence footing.
    """
    action = structural_action_from_str(op.action)
    target = _parse_provision_ref(op.target_provision_ref)
    op_id = f"proposal:{op.target_statute_id}:{op.target_provision_ref}:{sequence}"
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=action,
        target=target,
        raw_text=op.raw_johtolause,
        execution_authorization=_proposal_authorization(),
    )


def conditional_branch_to_legal_branch(
    branch: ConditionalBranch,
    *,
    he_id: str,
) -> tuple[LegalBranch, tuple[BranchGraphEdge, ...]]:
    """Project a ``ConditionalBranch`` into a ``LegalBranch`` + ``would_*`` edges.

    The branch lands on the ``draft`` non-enacted authority layer (a consultation
    HE luonnos is the lowest-confidence overlay), with a ``pending_condition``
    status — its effects apply only IF ``branch.condition`` holds. One
    ``BranchGraphEdge`` per candidate op models the non-enacted proposed effect:
    the edge kind is derived conservatively from the op's action
    (``branch_edge_kind_for_action`` — a plain amend/insert/replace/repeal maps to
    the matching ``would_*`` edge), and the edge targets ``op.target_statute_id``
    at the address parsed from ``op.target_provision_ref``. The edges themselves
    sit on the ``proposal`` authority layer, so they are branch-graph claims, not
    enacted replay inputs.

    ``he_id`` names the source HE artifact (e.g. ``"fi:he:VM045:00/2026"``) so
    the branch + its edges attribute back to the draft they were lowered from.
    """
    legal_branch = LegalBranch(
        branch_id=branch.branch_id,
        authority_layer=DRAFT_AUTHORITY,
        legal_status=PENDING_CONDITION_STATUS,
        source_artifact_id=he_id,
        title=branch.condition,
    )
    edges = tuple(
        BranchGraphEdge(
            branch_id=branch.branch_id,
            edge_kind=branch_edge_kind_for_action(structural_action_from_str(op.action)),
            source_artifact_id=he_id,
            target_statute_id=op.target_statute_id,
            target_address=str(_parse_provision_ref(op.target_provision_ref)),
            operation_id=f"proposal:{op.target_statute_id}:{op.target_provision_ref}:{index}",
            authority_layer=PROPOSAL_AUTHORITY,
            legal_status=PENDING_CONDITION_STATUS,
        )
        for index, op in enumerate(branch.candidate_ops)
    )
    return legal_branch, edges


__all__ = [
    "PROPOSAL_AUTHORIZATION_RULE_ID",
    "PROPOSAL_AUTHORIZATION_STATUS",
    "PROPOSAL_OWNER_PHASE",
    "candidate_op_to_legal_operation",
    "conditional_branch_to_legal_branch",
]

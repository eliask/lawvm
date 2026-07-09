"""Proposal-carrier → enacted-infra lowering, WITHOUT replay authorization.

A ``CandidateOperation`` / ``ConditionalBranch`` (the draft-HE "if enacted"
product) lowers into ``core.ir.LegalOperation`` + ``core.branch_authority``
carriers so a proposal is VIEWABLE as "what would change" — but the lowered op
is never replay-authorized (proposal.py's review §9 invariant), and the branch
sits on a non-enacted authority layer.
"""
from __future__ import annotations

from lawvm.core.branch_authority import (
    DRAFT_AUTHORITY,
    NON_ENACTED_AUTHORITIES,
    WOULD_AMEND_EDGE,
    WOULD_INSERT_EDGE,
)
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.source_document import (
    AssuranceTier,
    CandidateOperation,
    ConditionalBranch,
    ProposalAuthorityStatus,
    SourceAnchor,
)
from lawvm.finland.source_document.branch_lowering import (
    PROPOSAL_AUTHORIZATION_RULE_ID,
    candidate_op_to_legal_operation,
    conditional_branch_to_legal_branch,
)

_DIGEST = "a" * 64
_HE_ID = "fi:he:VM045:00/2026"


def _insert_op() -> CandidateOperation:
    return CandidateOperation(
        action="insert",
        target_statute_id="603/2006",
        target_provision_ref="section:4/subsection:5",
        payload_text="Uusi momentti.",
        source_anchor=SourceAnchor(artifact_digest=_DIGEST, locator="//section[4]"),
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        raw_johtolause="lisätään 4 §:ään uusi 5 momentti seuraavasti:",
    )


def test_candidate_op_lowers_to_non_authorized_insert_operation() -> None:
    op = candidate_op_to_legal_operation(_insert_op(), sequence=0)

    # The action maps "insert" -> StructuralAction.INSERT.
    assert op.action is StructuralAction.INSERT
    # The provision ref round-trips into the address the johtolause parser emits.
    assert op.target.path == (("section", "4"), ("subsection", "5"))
    # The verbatim johtolause rides as evidence footing.
    assert op.raw_text == "lisätään 4 §:ään uusi 5 momentti seuraavasti:"

    # The hard invariant: a proposal op is NEVER replay-authorized, and not
    # executable — it may be viewed, never mutate the enacted timeline.
    auth = op.execution_authorization
    assert auth is not None
    assert auth.replay_authorized is False
    assert auth.executable is False
    assert auth.authorization_rule_id == PROPOSAL_AUTHORIZATION_RULE_ID
    assert auth.authorization_status == "proposal"


def test_empty_provision_ref_lowers_to_statute_root_address() -> None:
    op = candidate_op_to_legal_operation(
        CandidateOperation(
            action="replace",
            target_statute_id="603/2006",
            target_provision_ref="",
            payload_text="",
            source_anchor=SourceAnchor(artifact_digest=_DIGEST, locator="body"),
            assurance_tier=AssuranceTier.SINGLE_WITNESS,
        ),
        sequence=1,
    )
    assert op.action is StructuralAction.REPLACE
    assert op.target.path == ()
    assert op.execution_authorization is not None
    assert op.execution_authorization.replay_authorized is False


def test_conditional_branch_lowers_to_draft_branch_and_would_amend_edge() -> None:
    branch = ConditionalBranch(
        branch_id=f"{_HE_ID}:draft",
        condition=f"{_HE_ID} enacted as introduced",
        candidate_ops=(_insert_op(),),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )

    legal_branch, edges = conditional_branch_to_legal_branch(branch, he_id=_HE_ID)

    # The branch lands on a NON-enacted authority layer (draft).
    assert legal_branch.branch_id == f"{_HE_ID}:draft"
    assert legal_branch.authority_layer == DRAFT_AUTHORITY
    assert legal_branch.authority_layer in NON_ENACTED_AUTHORITIES
    assert legal_branch.source_artifact_id == _HE_ID

    # One edge per candidate op; an INSERT projects a would_insert edge that
    # targets 603/2006 (an amend-family edge, never an enacted mutation).
    assert len(edges) == 1
    edge = edges[0]
    assert edge.target_statute_id == "603/2006"
    assert edge.edge_kind in {WOULD_INSERT_EDGE, WOULD_AMEND_EDGE}
    assert edge.edge_kind == WOULD_INSERT_EDGE
    assert edge.target_address == "section:4/subsection:5"
    assert edge.branch_id == f"{_HE_ID}:draft"


def test_would_amend_edge_for_generic_action() -> None:
    # A META action (no dedicated would_* mapping) falls back to would_amend.
    branch = ConditionalBranch(
        branch_id=f"{_HE_ID}:draft",
        condition=f"{_HE_ID} enacted",
        candidate_ops=(
            CandidateOperation(
                action="meta",
                target_statute_id="603/2006",
                target_provision_ref="section:1",
                payload_text="",
                source_anchor=SourceAnchor(artifact_digest=_DIGEST, locator="//section[1]"),
                assurance_tier=AssuranceTier.SINGLE_WITNESS,
            ),
        ),
        authority_status=ProposalAuthorityStatus.CONSULTATION_DRAFT,
    )
    _branch, edges = conditional_branch_to_legal_branch(branch, he_id=_HE_ID)
    assert edges[0].edge_kind == WOULD_AMEND_EDGE
    assert edges[0].target_statute_id == "603/2006"

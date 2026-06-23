"""Promotion-chain integrity gates (audit-registry CHAIN-/PROMOTE- families).

These gates ride the EXISTING EV-05 execution-authorization graph as read-only
checks; they do NOT modify the production apply mutation path. They are invoked
as test-gates / guard-liveness drills over already-resolved carriers (the §0
promotion chain: source witness -> candidate claim -> execution-authorization ->
dry-run/replay proof -> agreement row).

The HIGH-VALUE concrete deliverable is **PROMOTE-02 authorization scope-match**:
an :class:`~lawvm.core.execution_authorization.ExecutionAuthorization` authorizes
EXACTLY the op whose derived identity it was minted for. An authorization minted
for op A reused to gate op B is smuggled authority. ``gate_authorization_scope_match``
is a real strict-blocking gate over that invariant, driven by a fire-drill.

CHAIN-01 / CHAIN-02 / PROMOTE-01 are implemented as gates over the typed link
carriers that EXIST today, with the missing links / multi-hop residual named on
the emitted finding detail (see ``core.promotion_chain`` for the carrier-honesty
note). They are NOT wired into the production apply path (sibling sessions own it);
they gate from tests + drills.
"""

from __future__ import annotations

from typing import Optional

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.phase_result import Finding
from lawvm.core.promotion_chain import (
    DerivedOpAuthorityIdentity,
    DownchainRetractionVerdict,
    PromotionChainLinks,
    check_authorization_scope_match,
    check_downchain_retraction_reopened,
    check_promotion_chain,
    derive_op_authority_identity,
)
from lawvm.finland.ops import ResolvedOp

# ---------------------------------------------------------------------------
# Registered finding codes (see core/observation_registry.py FINDING_REGISTRY).
# ---------------------------------------------------------------------------

# PROMOTE-02 (strict-blocking): an ExecutionAuthorization gating an op whose
# derived identity does not equal the authorization's bound rule_id — authority
# minted for a different op (smuggled authority).
AUTHORIZATION_IDENTITY_MISMATCH_CODE = "PROMOTE.AUTHORIZATION_IDENTITY_MISMATCH"
# CHAIN-01 (strict-blocking): a mutating op's promotion chain is missing a
# MATERIALIZED link (incomplete over the links that exist as typed carriers).
PROMOTION_CHAIN_INCOMPLETE_CODE = "CHAIN.PROMOTION_CHAIN_INCOMPLETE"
# CHAIN-02 (strict-blocking): a promotion-chain link reached with an absent
# materialized predecessor — authority by accumulation, not by climbing.
AUTHORITY_BY_ACCUMULATION_CODE = "CHAIN.AUTHORITY_BY_ACCUMULATION"
# PROMOTE-01 (strict-blocking): a downstream link left standing on a retracted
# predecessor without reopen/taint (immediate one-hop arm; multi-hop residual
# named on the detail).
STALE_DOWNSTREAM_AFTER_RETRACTION_CODE = "PROMOTE.STALE_DOWNSTREAM_AFTER_RETRACTION"


def _op_target_address_key(rop: ResolvedOp) -> tuple[tuple[str, str], ...]:
    address = rop.resolved_target_address
    if address is None or not address.path:
        return ()
    return tuple((str(kind), str(label)) for kind, label in address.path)


def derive_op_identity_for_authorization(rop: ResolvedOp) -> DerivedOpAuthorityIdentity:
    """Derive the §8 op identity an apply-path authorization is minted for.

    Surfaces the op-side identity components that EXIST (op_id, resolved target
    node ids, resolved action family). Only ``rule_id`` (= op_id) is bound on the
    apply-path authorization today; the others are the named PROMOTE-02 residual.
    """
    return derive_op_authority_identity(
        op_id=rop.op_id or "",
        target_address_key=_op_target_address_key(rop),
        action_family=str(rop.resolved_action_type or ""),
    )


def gate_authorization_scope_match(
    *,
    authorization: ExecutionAuthorization,
    rop: ResolvedOp,
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """PROMOTE-02: reject an authorization whose bound rule_id != the op's derived identity.

    The fully-checkable invariant: the authorization's ``authorization_rule_id``
    MUST equal the gated op's derived ``rule_id`` (its ``op_id``). Reusing an
    authorization minted for op A to gate op B is a rule_id mismatch — smuggled
    authority. The apply-path authorization is minted with ``rule_id = op_id`` for
    the SAME op (so production is 0-delta / always matches by construction); the
    drill forges a mismatched authorization to exercise the gate. The deeper
    (input_node_ids/policy_id/candidate_set_hash) binding is the named residual
    carried on the finding detail.
    """
    derived = derive_op_identity_for_authorization(rop)
    verdict = check_authorization_scope_match(authorization, derived)
    if verdict.matched:
        return
    if not is_strict:
        return
    findings_out.append(
        Finding(
            kind=AUTHORIZATION_IDENTITY_MISMATCH_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                "message": (
                    "An ExecutionAuthorization gating a state-mutating op is bound "
                    "to a different op's derived identity (rule_id mismatch); "
                    "authority minted for one op may not gate another "
                    "(smuggled authority, PROMOTE-02 / §1.5 authority analogue)."
                ),
                "op_id": rop.op_id or "",
                "bound_rule_id": verdict.bound_rule_id,
                "derived_rule_id": verdict.derived_rule_id,
                "reason": verdict.reason,
                # Named, bounded residual: the identity components NOT yet bound
                # on the authorization (PART per the carrier-honesty note).
                "unbound_identity_components": list(
                    verdict.unbound_identity_components
                ),
            },
        )
    )


def gate_promotion_chain_links(
    *,
    links: PromotionChainLinks,
    rop: Optional[ResolvedOp],
    is_strict: bool,
    source_statute: str,
    findings_out: list[Finding],
) -> None:
    """CHAIN-01 + CHAIN-02 over a typed promotion-chain link snapshot.

    Emits ``CHAIN.PROMOTION_CHAIN_INCOMPLETE`` (a missing MATERIALIZED link) and
    ``CHAIN.AUTHORITY_BY_ACCUMULATION`` (a link present with an absent materialized
    predecessor). The unmaterialized links (candidate_claim / dry_run_proof /
    agreement_row are not apply-path carriers today) are excluded from the
    completeness requirement and named on the finding detail — the bounded PART.
    """
    verdict = check_promotion_chain(links)
    if not is_strict:
        return
    op_id = rop.op_id if rop is not None else ""
    if not verdict.complete:
        findings_out.append(
            Finding(
                kind=PROMOTION_CHAIN_INCOMPLETE_CODE,
                role="violation",
                stage="apply",
                blocking=True,
                source_statute=source_statute,
                detail={
                    "message": (
                        "A state-mutating op's promotion chain is missing a "
                        "materialized link; every materialized link "
                        "(source witness -> ... -> agreement row) must be present "
                        "(CHAIN-01 completeness)."
                    ),
                    "op_id": op_id or "",
                    "missing_links": list(verdict.missing_links),
                    "unmaterialized_links": list(verdict.unmaterialized_links),
                },
            )
        )
    if not verdict.monotone:
        findings_out.append(
            Finding(
                kind=AUTHORITY_BY_ACCUMULATION_CODE,
                role="violation",
                stage="apply",
                blocking=True,
                source_statute=source_statute,
                detail={
                    "message": (
                        "A promotion-chain link was reached with an absent "
                        "materialized predecessor — authority acquired by "
                        "accumulation, not by climbing the boundary "
                        "(CHAIN-02, never by accumulation)."
                    ),
                    "op_id": op_id or "",
                    "accumulation_links": list(verdict.accumulation_links),
                    "unmaterialized_links": list(verdict.unmaterialized_links),
                },
            )
        )


def gate_downchain_retraction(
    *,
    retracted_link: str,
    downstream_links: tuple[str, ...],
    reopened_links: frozenset[str],
    is_strict: bool,
    source_statute: str,
    op_id: str,
    findings_out: list[Finding],
) -> DownchainRetractionVerdict:
    """PROMOTE-01: reject a downstream link left standing on a retracted predecessor.

    Implements the one-hop arm EV-09's taint machinery supports (the immediate
    downstream link of a retracted link must be reopened/tainted). Returns the
    verdict (carrying the multi-hop sub-chain residual) so callers can read the
    named PART. Under strict, an un-reopened immediate downstream link BLOCKS.
    """
    verdict = check_downchain_retraction_reopened(
        retracted_link=retracted_link,
        downstream_links=downstream_links,
        reopened_links=reopened_links,
    )
    if verdict.immediate_consumers_reopened:
        return verdict
    if not is_strict:
        return verdict
    findings_out.append(
        Finding(
            kind=STALE_DOWNSTREAM_AFTER_RETRACTION_CODE,
            role="violation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                "message": (
                    "A retracted promotion-chain link has a downstream link left "
                    "standing without reopen/taint; the whole sub-chain below a "
                    "retracted link must be re-opened, not just the immediate "
                    "consumer (PROMOTE-01)."
                ),
                "op_id": op_id or "",
                "retracted_link": retracted_link,
                "stale_downstream": list(verdict.stale_downstream),
                # Named PART residual: EV-09 propagates one hop only.
                "multi_hop_residual": verdict.multi_hop_residual,
            },
        )
    )
    return verdict


__all__ = [
    "AUTHORIZATION_IDENTITY_MISMATCH_CODE",
    "PROMOTION_CHAIN_INCOMPLETE_CODE",
    "AUTHORITY_BY_ACCUMULATION_CODE",
    "STALE_DOWNSTREAM_AFTER_RETRACTION_CODE",
    "derive_op_identity_for_authorization",
    "gate_authorization_scope_match",
    "gate_promotion_chain_links",
    "gate_downchain_retraction",
]

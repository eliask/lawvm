"""Promotion-chain integrity gates (audit-registry CHAIN-/PROMOTE- families, §0).

§0 promotion-chain principle: a node earns the right to mutate replay only by
climbing source witness -> candidate claim -> execution-authorization ->
dry-run/replay proof -> agreement row; never by accumulation.

These tests pin the genuinely-checkable parts of the four rows:

* PROMOTE-02 (authorization scope-match) — the concrete strict-blocking gate:
  an authorization gates EXACTLY the op whose derived identity (rule_id = op_id)
  it was minted for. The deeper identity-binding (input_node_ids / policy_id /
  candidate_set_hash) is the NAMED, bounded residual (asserted reported, not
  silently matched).
* CHAIN-02 (monotonicity) — no materialized link present with an absent
  materialized predecessor (authority by accumulation).
* CHAIN-01 (completeness) — every materialized link present; unmaterialized
  links named, not assumed.
* PROMOTE-01 (retraction down-chain) — immediate downstream link reopened
  (one-hop arm); multi-hop sub-chain named as residual.
"""

from __future__ import annotations

from typing import Any, cast

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.promotion_chain import (
    PROMOTE01_MULTI_HOP_RESIDUAL,
    PROMOTE02_UNBOUND_IDENTITY_COMPONENTS,
    PROMOTION_CHAIN_LINKS,
    PromotionChainLinks,
    check_authorization_scope_match,
    check_downchain_retraction_reopened,
    check_promotion_chain,
    derive_op_authority_identity,
)
from lawvm.finland.apply_promotion_chain import (
    AUTHORITY_BY_ACCUMULATION_CODE,
    AUTHORIZATION_IDENTITY_MISMATCH_CODE,
    PROMOTION_CHAIN_INCOMPLETE_CODE,
    STALE_DOWNSTREAM_AFTER_RETRACTION_CODE,
    derive_op_identity_for_authorization,
    gate_authorization_scope_match,
    gate_downchain_retraction,
    gate_promotion_chain_links,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _op_authorization(*, rule_id: str) -> ExecutionAuthorization:
    """An apply-path-shaped authorization bound to ``rule_id`` (= op_id)."""
    return ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="apply_op_authorized",
        authorization_rule_id=rule_id,
        owner_phase="apply",
        strict_disposition="record",
        safe_default="block_until_apply_op_authorization_rule_is_resolved",
        forbidden_shortcuts=("landed_write_existence_as_execution_authorization",),
    )


def _rop(*, op_id: str, target_address: LegalAddress | None = None) -> Any:
    from lawvm.finland.ops import AmendmentOp, ResolvedOp
    from lawvm.finland.target_kind import TargetKind

    op = AmendmentOp(
        op_id=op_id,
        op_type=cast(Any, "REPLACE"),
        target_kind=TargetKind.SECTION,
        target_section="1",
        lo=None,
    )
    return ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        target_norm="1",
        target_unit_kind="section",
        op_id=op_id,
        _op_type_seed="REPLACE",
        _target_address_override=target_address,
    )


# ---------------------------------------------------------------------------
# PROMOTE-02 — authorization scope-match (the concrete deliverable)
# ---------------------------------------------------------------------------


def test_promote02_matched_authorization_passes() -> None:
    rop = _rop(op_id="op_match", target_address=LegalAddress(path=(("section", "1"),)))
    auth = _op_authorization(rule_id="op_match")
    derived = derive_op_identity_for_authorization(rop)
    verdict = check_authorization_scope_match(auth, derived)
    assert verdict.matched
    assert verdict.bound_rule_id == "op_match"
    assert verdict.derived_rule_id == "op_match"


def test_promote02_mismatched_authorization_blocks_under_strict() -> None:
    """An authorization minted for op A reused to gate op B is smuggled authority."""
    rop = _rop(op_id="op_B", target_address=LegalAddress(path=(("section", "1"),)))
    smuggled = _op_authorization(rule_id="op_A")  # minted for a DIFFERENT op
    findings: list[Finding] = []
    gate_authorization_scope_match(
        authorization=smuggled,
        rop=rop,
        is_strict=True,
        source_statute="12/2015",
        findings_out=findings,
    )
    hits = [
        f
        for f in findings
        if f.kind == AUTHORIZATION_IDENTITY_MISMATCH_CODE and f.blocking
    ]
    assert hits, "a mismatched authorization did not block under strict (PROMOTE-02)"
    detail = hits[0].detail
    assert detail["bound_rule_id"] == "op_A"
    assert detail["derived_rule_id"] == "op_B"
    # The deeper identity binding is the NAMED, bounded residual — reported, not
    # silently treated as matching.
    assert (
        tuple(detail["unbound_identity_components"])
        == PROMOTE02_UNBOUND_IDENTITY_COMPONENTS
    )


def test_promote02_matched_authorization_does_not_block() -> None:
    rop = _rop(op_id="op_C", target_address=LegalAddress(path=(("section", "1"),)))
    auth = _op_authorization(rule_id="op_C")
    findings: list[Finding] = []
    gate_authorization_scope_match(
        authorization=auth,
        rop=rop,
        is_strict=True,
        source_statute="12/2015",
        findings_out=findings,
    )
    assert not findings, "scope-match gate fired on a correctly-bound authorization"


def test_promote02_permissive_profile_does_not_block() -> None:
    rop = _rop(op_id="op_B", target_address=LegalAddress(path=(("section", "1"),)))
    smuggled = _op_authorization(rule_id="op_A")
    findings: list[Finding] = []
    gate_authorization_scope_match(
        authorization=smuggled,
        rop=rop,
        is_strict=False,
        source_statute="12/2015",
        findings_out=findings,
    )
    assert not findings, "scope-match gate blocked under a permissive profile"


def test_promote02_derived_identity_surfaces_op_side_residual_carriers() -> None:
    """The op-side identity components that EXIST are surfaced (named residual)."""
    rop = _rop(
        op_id="op_id_carriers",
        target_address=LegalAddress(path=(("section", "5"), ("subsection", "2"))),
    )
    derived = derive_op_identity_for_authorization(rop)
    assert derived.rule_id == "op_id_carriers"
    # input_node_ids are derived from the resolved target address path — these
    # EXIST op-side but are NOT bound on the authorization (the residual).
    assert derived.input_node_ids == ("section:5", "subsection:2")
    assert derived.action_family


# ---------------------------------------------------------------------------
# CHAIN-02 — monotonicity (never by accumulation)
# ---------------------------------------------------------------------------


def _links(
    *,
    source_witness: bool = True,
    candidate_claim: bool = True,
    execution_authorization: bool = True,
    dry_run_proof: bool = True,
    agreement_row: bool = True,
    unmaterialized: tuple[str, ...] = (),
) -> PromotionChainLinks:
    return PromotionChainLinks(
        source_witness=source_witness,
        candidate_claim=candidate_claim,
        execution_authorization=execution_authorization,
        dry_run_proof=dry_run_proof,
        agreement_row=agreement_row,
        unmaterialized_links=unmaterialized,
    )


def test_chain02_authority_by_accumulation_blocks() -> None:
    """execution-authorization present with an absent candidate-claim predecessor."""
    links = _links(candidate_claim=False)  # auth present, predecessor absent
    findings: list[Finding] = []
    gate_promotion_chain_links(
        links=links,
        rop=None,
        is_strict=True,
        source_statute="12/2015",
        findings_out=findings,
    )
    accum = [f for f in findings if f.kind == AUTHORITY_BY_ACCUMULATION_CODE]
    assert accum, "a link reached without its predecessor did not block (CHAIN-02)"
    assert "execution_authorization" in accum[0].detail["accumulation_links"]


def test_chain02_full_chain_is_monotone() -> None:
    verdict = check_promotion_chain(_links())
    assert verdict.monotone
    assert verdict.complete


def test_chain02_unmaterialized_predecessor_is_transparent() -> None:
    """An UNMATERIALIZED predecessor does not gate its successor (named residual)."""
    # candidate_claim is unmaterialized; execution_authorization present with
    # source_witness present — no accumulation break across the unmaterialized gap.
    links = _links(candidate_claim=False, unmaterialized=("candidate_claim",))
    verdict = check_promotion_chain(links)
    assert verdict.monotone, "an unmaterialized predecessor wrongly broke monotonicity"


# ---------------------------------------------------------------------------
# CHAIN-01 — completeness (over materialized links)
# ---------------------------------------------------------------------------


def test_chain01_missing_materialized_link_blocks() -> None:
    links = _links(execution_authorization=False)
    findings: list[Finding] = []
    gate_promotion_chain_links(
        links=links,
        rop=None,
        is_strict=True,
        source_statute="12/2015",
        findings_out=findings,
    )
    incomplete = [f for f in findings if f.kind == PROMOTION_CHAIN_INCOMPLETE_CODE]
    assert incomplete, "a missing materialized link did not block (CHAIN-01)"
    assert "execution_authorization" in incomplete[0].detail["missing_links"]


def test_chain01_unmaterialized_links_excluded_and_named() -> None:
    """Completeness ignores unmaterialized links but NAMES them (bounded PART)."""
    # Only source_witness + execution_authorization are materialized today.
    links = _links(
        candidate_claim=False,
        dry_run_proof=False,
        agreement_row=False,
        unmaterialized=("candidate_claim", "dry_run_proof", "agreement_row"),
    )
    verdict = check_promotion_chain(links)
    assert verdict.complete, "completeness wrongly required an unmaterialized link"
    assert set(verdict.unmaterialized_links) == {
        "candidate_claim",
        "dry_run_proof",
        "agreement_row",
    }
    # The materialized links are exactly the two carried today.
    assert links.materialized_links() == ("source_witness", "execution_authorization")


def test_chain01_link_order_is_the_section0_chain() -> None:
    assert PROMOTION_CHAIN_LINKS == (
        "source_witness",
        "candidate_claim",
        "execution_authorization",
        "dry_run_proof",
        "agreement_row",
    )


# ---------------------------------------------------------------------------
# PROMOTE-01 — retraction propagates down-chain (one-hop arm + named residual)
# ---------------------------------------------------------------------------


def test_promote01_unreopened_downstream_blocks() -> None:
    findings: list[Finding] = []
    verdict = gate_downchain_retraction(
        retracted_link="execution_authorization",
        downstream_links=("dry_run_proof", "agreement_row"),
        reopened_links=frozenset({"dry_run_proof"}),  # agreement_row left standing
        is_strict=True,
        source_statute="12/2015",
        op_id="op_retract",
        findings_out=findings,
    )
    hits = [f for f in findings if f.kind == STALE_DOWNSTREAM_AFTER_RETRACTION_CODE]
    assert hits, "a downstream link standing on a retracted predecessor did not block"
    assert "agreement_row" in hits[0].detail["stale_downstream"]
    # The multi-hop sub-chain depth is the NAMED, bounded residual.
    assert hits[0].detail["multi_hop_residual"] == PROMOTE01_MULTI_HOP_RESIDUAL
    assert not verdict.immediate_consumers_reopened


def test_promote01_all_reopened_passes() -> None:
    findings: list[Finding] = []
    verdict = gate_downchain_retraction(
        retracted_link="execution_authorization",
        downstream_links=("dry_run_proof", "agreement_row"),
        reopened_links=frozenset({"dry_run_proof", "agreement_row"}),
        is_strict=True,
        source_statute="12/2015",
        op_id="op_retract_clean",
        findings_out=findings,
    )
    assert not findings, "retraction gate fired despite all downstream links reopened"
    assert verdict.immediate_consumers_reopened


def test_promote01_check_reports_multi_hop_residual_directly() -> None:
    verdict = check_downchain_retraction_reopened(
        retracted_link="execution_authorization",
        downstream_links=("dry_run_proof",),
        reopened_links=frozenset(),
    )
    assert not verdict.immediate_consumers_reopened
    assert verdict.multi_hop_residual == PROMOTE01_MULTI_HOP_RESIDUAL


# ---------------------------------------------------------------------------
# Registry consistency for the new finding kinds
# ---------------------------------------------------------------------------


def test_new_finding_kinds_are_registered_blocking() -> None:
    from lawvm.core.observation_registry import FINDING_REGISTRY

    for code in (
        AUTHORIZATION_IDENTITY_MISMATCH_CODE,
        PROMOTION_CHAIN_INCOMPLETE_CODE,
        AUTHORITY_BY_ACCUMULATION_CODE,
        STALE_DOWNSTREAM_AFTER_RETRACTION_CODE,
    ):
        spec = FINDING_REGISTRY.get(code)
        assert spec is not None, f"{code} is not registered"
        assert spec.role == "violation"
        assert spec.owner == "apply_promotion_chain"


def test_derive_op_authority_identity_strips_blank_kinds() -> None:
    derived = derive_op_authority_identity(
        op_id="  op_strip  ",
        target_address_key=(("section", "1"), ("", "x")),
        action_family="replace",
    )
    assert derived.rule_id == "op_strip"
    assert derived.input_node_ids == ("section:1",)

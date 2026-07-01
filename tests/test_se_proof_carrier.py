"""SE as a MINTING frontend for the EV-05 execution-authorization proof carrier.

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` §2 / §7.1 (the EV-05 observe
gate) and the EE recipe (``tests/test_ee_proof_carrier.py``). Until now SE's
production profile inherited the NO-OP ``no_op_execution_authorization`` resolver,
so the EV-05 gate read a ~100% firewall hole on every landed SE write. SE now
wires a REAL resolver:

* **EV-05** — ``_se_execution_authorization`` MINTS a typed ExecutionAuthorization
  from each op's affecting-act identity (``op.source.statute_id`` — the official
  SFS act id SE stamps onto every lowered op), or reads a proof already minted
  onto ``op.execution_authorization`` (the generic carrier). An op with a known
  affecting act goes QUIET; an op with no affecting-act identity has unknown
  authority → no proof → the EV-05 gate fires honestly.

AM-01 (provenance acceptance) is NOT wired for SE: SE's mutation-boundary v0
carries no declared_recovery and SE ops carry no Parsed-vs-Recovered /
scope_confidence acceptance signal at the apply seam (only act-identity /
structural ``provenance_tags``). There is no verdict to resolve without
fabricating one, so the default ``no_op_provenance`` resolver keeps that gate a
0-delta no-op. This test therefore exercises EV-05 only.

The EV-05 resolver is OBSERVE-only: its witness routes to
``AppliedOp.observations``, never production ``findings`` — so SE's materialized
statute + adjudications stay byte-identical. SE is NOT flipped to block.
"""
from __future__ import annotations

from lawvm.core.apply_seam import (
    REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
    AppliedOp,
    apply_op,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind
from lawvm.sweden import grafter as se_grafter
from lawvm.sweden.grafter import (
    _mint_se_execution_authorization,
    _se_execution_authorization,
    apply_se_ops,
)


# ── shared fixtures (mirror the SE production op + statute shape) ──────────────


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _replace_op(
    label: str,
    text: str,
    *,
    op_id: str | None = None,
    statute_id: str = "2026:999",
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id or f"se_replace_{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=statute_id) if statute_id else None,
        execution_authorization=execution_authorization,
    )


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _se_profile():
    """Re-derive SE's production profile fields (resolver + modes) without a statute.

    Mirrors the profile built inside ``apply_se_ops`` so the gate can be exercised
    over a single op via ``apply_op`` (the SE materializer is captured by the fold,
    so here we use a thin REPLACE-only materializer sufficient for these cases).
    """
    from lawvm.core.apply_seam import ApplyProfile, MaterializeResult
    from lawvm.core import tree_ops

    def _mat(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        label = op.target.leaf_label()
        path = tree_ops.find(before, "section", label)
        if path is None:
            return MaterializeResult(new_state=before, applied=False)
        assert op.payload is not None
        after = tree_ops.replace_at(before, path, op.payload)
        return MaterializeResult(new_state=after, applied=True)

    return ApplyProfile(
        jurisdiction="se",
        materializer=_mat,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        receipt_helper_prefix="apply_se_ops",
        authorization_resolver=_se_execution_authorization,
    )


# ── EV-05: the SE resolver MINTS a real proof from the affecting-act identity ──


def test_minted_proof_names_the_affecting_act() -> None:
    op = _replace_op("5", "x", statute_id="2024:312")
    proof = _mint_se_execution_authorization(op)
    assert proof is not None
    assert isinstance(proof, ExecutionAuthorization)
    assert proof.authorization_rule_id == "se_affecting_act:2024:312"
    assert proof.replay_authorized is True
    assert proof.detail["affecting_act"] == "2024:312"


def test_no_affecting_act_yields_no_proof() -> None:
    """An op with no source / blank statute_id has UNKNOWN authority — never fabricated."""
    op = _replace_op("5", "x", statute_id="")
    assert _mint_se_execution_authorization(op) is None
    assert _se_execution_authorization(op) is None


def test_resolver_prefers_a_carried_proof() -> None:
    """A proof already minted onto the carrier wins over re-minting from source."""
    carried = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id="carried:rule",
        owner_phase="apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        safe_default="x",
    )
    op = _replace_op("5", "x", statute_id="2024:312", execution_authorization=carried)
    resolved = _se_execution_authorization(op)
    assert resolved is carried
    assert resolved.authorization_rule_id == "carried:rule"


def test_ev05_gate_quiet_for_an_op_with_known_authority() -> None:
    body = _body(_section("5", "Gamla 5."))
    op = _replace_op("5", "Ny 5.", statute_id="2024:312")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_se_profile(), source_statute="2026:999"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]


def test_ev05_gate_fires_for_an_op_with_unknown_authority() -> None:
    body = _body(_section("5", "Gamla 5."))
    op = _replace_op("5", "Ny 5.", statute_id="")  # no affecting act
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_se_profile(), source_statute="2026:999"
    )
    assert applied.applied
    hole = [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    assert len(hole) == 1
    # NEVER on findings (byte-identity).
    assert not any(
        getattr(f, "kind", None) == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for f in applied.findings
    )


# ── Byte-identity: the gate routes to observations, never production output ────


def test_apply_se_ops_output_is_byte_identical_with_resolver() -> None:
    """The EV-05 resolver perturbs neither the materialized body nor adjudications.

    ``apply_se_ops`` wires the resolver onto the production profile. Its witness
    goes to ``AppliedOp.observations`` (drained only via the opt-in
    ``seam_observations_out``); the materialized statute + adjudications the
    byte-identity gates assert on are untouched.
    """
    statute = IRStatute(
        statute_id="2026:999",
        title="Test",
        body=_body(_section("5", "Gamla 5."), _section("6", "Gamla 6.")),
    )
    ops = [
        _replace_op("5", "Ny 5.", op_id="op_known", statute_id="2024:312"),
        _replace_op("6", "Ny 6.", op_id="op_unknown", statute_id=""),
    ]
    adjuds: list = []
    replayed = apply_se_ops(statute, list(ops), adjudications_out=adjuds)
    # The materialized body reflects both writes (the resolver did not block/skip).
    texts = {c.label: c.text for c in replayed.body.children}
    assert texts["5"] == "Ny 5."
    assert texts["6"] == "Ny 6."
    # The OBSERVE lane never leaked into production adjudications.
    assert not any(
        getattr(a, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for a in adjuds
    )


def test_seam_observations_drain_carries_the_unauthorized_residue() -> None:
    """When the caller opts into the drain, the EV-05 unauthorized witness surfaces.

    This is the measurement carrier: ``seam_observations_out`` collects the
    OBSERVE-lane findings the production fold otherwise discards. The op with a
    known affecting act yields ZERO holes; the op with no affecting act yields
    exactly one ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` witness — the
    non-trivial EV-05 measurement in the small.
    """
    statute = IRStatute(
        statute_id="2026:999",
        title="Test",
        body=_body(_section("5", "Gamla 5."), _section("6", "Gamla 6.")),
    )
    ops = [
        _replace_op("5", "Ny 5.", op_id="op_known", statute_id="2024:312"),
        _replace_op("6", "Ny 6.", op_id="op_unknown", statute_id=""),
    ]
    drain: list = []
    apply_se_ops(statute, list(ops), seam_observations_out=drain)
    holes = [
        f
        for f in drain
        if getattr(f, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    assert len(holes) == 1
    assert holes[0].detail["op_id"] == "op_unknown"


def test_se_models_no_provenance_signal_am01_skipped() -> None:
    """SE does NOT wire an AM-01 provenance resolver (no Parsed-vs-Recovered signal).

    Asserts the deliberate SKIP: SE's production profile keeps the kernel-default
    ``no_op_provenance`` resolver, so the AM-01 gate is a 0-delta no-op. The
    grafter exposes no ``_se_op_provenance_acceptance`` symbol (unlike EE).
    """
    assert not hasattr(se_grafter, "_se_op_provenance_acceptance")
    profile = _se_profile()
    from lawvm.core.apply_seam import no_op_provenance

    assert profile.provenance_resolver is no_op_provenance

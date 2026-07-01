"""UK as a MINTING frontend for the EV-05 execution-authorization proof carrier.

Design reference: ``estonia/grafter:_mint_ee_execution_authorization`` (the proven
recipe) and ``core/apply_seam`` (the EV-05 observe gate). Until now the UK
production profile inherited the NO-OP ``authorization_resolver`` on its
``ApplyProfile``, so the EV-05 gate read a ~100% firewall hole. UK now wires a
REAL resolver:

* **EV-05** — ``_uk_execution_authorization`` MINTS a typed ExecutionAuthorization
  from each op's AFFECTING-act identity (``op.source.statute_id``, lowered from
  ``effect.affecting_act_id``), or reads a proof already minted onto
  ``op.execution_authorization`` (the generic carrier). An op with a known
  affecting act goes QUIET; an op with no affecting-act identity has unknown
  authority → no proof → the EV-05 gate fires honestly.

UK has no typed Parsed-vs-Recovered signal on its ops (no
``LegalOperation.scope_confidence`` rider, no ``scope_confidence:`` provenance-tag
rung), so **AM-01 is NOT wired** — EV-05 alone is the win.

The gate is OBSERVE-only: its witness routes to ``AppliedOp.observations`` (drained
only via the opt-in ``seam_observations_out``), never production ``findings`` /
adjudications — so UK's materialized statute + adjudications stay byte-identical.
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
from lawvm.core.phase_result import Finding
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.execution_authorization import (
    _mint_uk_execution_authorization,
    _uk_execution_authorization,
)
from lawvm.uk_legislation.replay_executor import UKReplayExecutor, replay_uk_ops


# ── shared fixtures ───────────────────────────────────────────────────────────


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _statute(*sections: IRNode, statute_id: str = "ukpga/1990/1") -> IRStatute:
    return IRStatute(statute_id=statute_id, title="Test Act", body=_body(*sections))


def _replace_op(
    label: str,
    text: str,
    *,
    op_id: str | None = None,
    statute_id: str = "ukpga/2020/5",
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id or f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=statute_id) if statute_id else None,
        execution_authorization=execution_authorization,
    )


def _uk_profile():
    """The UK production apply profile (re-derived from the executor)."""
    return UKReplayExecutor(_statute(_section("5", "x")))._uk_seam_apply_profile()


# ── EV-05: the UK resolver MINTS a real proof from the affecting-act identity ──


def test_minted_proof_names_the_affecting_act() -> None:
    op = _replace_op("5", "x", statute_id="ukpga/2020/7")
    proof = _mint_uk_execution_authorization(op)
    assert proof is not None
    assert isinstance(proof, ExecutionAuthorization)
    assert proof.authorization_rule_id == "uk_affecting_act:ukpga/2020/7"
    assert proof.replay_authorized is True
    assert proof.executable is True
    assert proof.detail["affecting_act"] == "ukpga/2020/7"


def test_no_affecting_act_yields_no_proof() -> None:
    """An op with no source / blank statute_id has UNKNOWN authority — never fabricated."""
    op = _replace_op("5", "x", statute_id="")
    assert _mint_uk_execution_authorization(op) is None
    assert _uk_execution_authorization(op) is None


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
    op = _replace_op("5", "x", statute_id="ukpga/2020/7", execution_authorization=carried)
    resolved = _uk_execution_authorization(op)
    assert resolved is carried
    assert resolved.authorization_rule_id == "carried:rule"


# ── EV-05: the gate goes quiet for a known authority, fires for an unknown one ─


def test_ev05_gate_quiet_for_an_op_with_known_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="ukpga/2020/7")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_uk_profile(), source_statute="ukpga/2020/7"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]


def test_ev05_gate_fires_for_an_op_with_unknown_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="")  # no affecting act
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_uk_profile(), source_statute=""
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


def test_replay_uk_ops_output_is_byte_identical_with_resolver() -> None:
    """The new resolver perturbs neither the materialized body nor adjudications.

    ``replay_uk_ops`` wires the resolver onto the production profile. Its witness
    goes to ``AppliedOp.observations`` (drained only via the opt-in
    ``seam_observations_out``); the materialized statute + adjudications the
    byte-identity gates assert on are untouched.
    """
    base = _statute(_section("5", "Original 5"), _section("6", "Original 6"))
    ops = [
        _replace_op("5", "New 5", op_id="op_known", statute_id="ukpga/2020/7"),
        _replace_op("6", "New 6", op_id="op_unknown", statute_id=""),
    ]
    adjuds_a: list = []
    replayed = replay_uk_ops(base, list(ops), adjudications_out=adjuds_a)
    texts = {c.label: c.text for c in replayed.body.children}
    assert texts["5"] == "New 5"
    assert texts["6"] == "New 6"
    # The OBSERVE lane never leaked into production adjudications.
    assert not any(
        getattr(a, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for a in adjuds_a
    )


def test_seam_observations_drain_carries_the_ev05_residue() -> None:
    """When the caller opts into the drain, the EV-05 unknown-authority witness surfaces.

    This is the measurement carrier: ``seam_observations_out`` collects the
    OBSERVE-lane findings the production fold otherwise discards. An op with a
    known affecting act yields ZERO EV-05 holes; an op with no affecting-act
    identity yields exactly one ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED``
    witness — the non-trivial EV-05 measurement in the small.
    """
    base = _statute(_section("5", "Original 5"), _section("6", "Original 6"))
    ops = [
        _replace_op("5", "New 5", op_id="op_known", statute_id="ukpga/2020/7"),
        _replace_op("6", "New 6", op_id="op_unknown", statute_id=""),
    ]
    drain: list[Finding] = []
    replay_uk_ops(base, list(ops), seam_observations_out=drain)
    holes = [
        f
        for f in drain
        if getattr(f, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    # Exactly one residue: the unknown-authority op. The known-authority op is quiet.
    assert len(holes) == 1
    assert holes[0].detail.get("op_id") == "op_unknown"
    assert holes[0].detail.get("jurisdiction") == "uk"

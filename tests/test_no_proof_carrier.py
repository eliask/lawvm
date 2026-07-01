"""NO as a MINTING frontend for the EV-05 proof carrier + AM-01 resolver.

Mirrors the proven Estonia recipe (``tests/test_ee_proof_carrier.py``). Until
now both gates inherited a NO-OP resolver on NO's production profile, so EV-05
read a ~100% firewall hole and AM-01 fired nowhere. NO now wires REAL resolvers:

* **EV-05** — ``_no_execution_authorization`` MINTS a typed ExecutionAuthorization
  from each op's affecting-act identity (``op.source.statute_id`` — NO's
  ``source_id``, the act directing the change), or reads a proof already minted
  onto ``op.execution_authorization`` (the generic carrier). An op with a known
  affecting act goes QUIET; an op with no affecting-act identity has unknown
  authority → no proof → the EV-05 gate fires honestly.
* **AM-01** — ``_no_op_provenance_acceptance`` classifies the op as Parsed
  (admitted) or Recovered (refused under strict) from NO's OWN typed
  ``op.scope_confidence`` (``NOScopeConfidence.rung_id``) signal, mirroring FI's
  ``admits``/``mode_for`` WITHOUT importing ``finland/``.

Both are OBSERVE-only: their witnesses route to ``AppliedOp.observations``, never
production ``findings`` — so NO's materialized statute stays byte-identical. NO is
NOT flipped to block on either gate.
"""
from __future__ import annotations

from dataclasses import replace as dc_replace

from lawvm.core.apply_seam import (
    RECOVERED_OP_OBSERVED_FINDING_CODE,
    REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.norway.grafter import (
    _mint_no_execution_authorization,
    _no_execution_authorization,
    _no_op_provenance_acceptance,
    apply_no_ops,
)
from lawvm.norway.scope_confidence import NOScopeConfidence


# ── shared fixtures ───────────────────────────────────────────────────────────


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _replace_op(
    label: str,
    text: str,
    *,
    op_id: str | None = None,
    statute_id: str = "no/lov-2020-77",
    scope_confidence: NOScopeConfidence | None = None,
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id or f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=statute_id) if statute_id else None,
        scope_confidence=scope_confidence,
        execution_authorization=execution_authorization,
    )


def _no_gate_profile() -> ApplyProfile[IRNode]:
    """A minimal profile carrying ONLY the NO resolvers + a trivial materializer.

    The production ``_no_materialize_one`` is nested inside ``apply_no_ops``; for
    the gate quiet/fire unit tests we only need the kernel to land the op and run
    the resolvers, so a trivial REPLACE-by-label materializer suffices.
    """

    def _mat(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        label = op.payload.label if op.payload is not None else ""
        children = tuple(
            op.payload if (op.payload is not None and child.label == label) else child
            for child in before.children
        )
        return MaterializeResult(new_state=dc_replace(before, children=children))

    return ApplyProfile(
        jurisdiction="no",
        materializer=_mat,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        authorization_resolver=_no_execution_authorization,
        provenance_resolver=_no_op_provenance_acceptance,
    )


# ── EV-05: the NO resolver MINTS a real proof from the affecting-act identity ──


def test_minted_proof_names_the_affecting_act() -> None:
    op = _replace_op("5", "x", statute_id="LOV-2020-77")
    proof = _mint_no_execution_authorization(op)
    assert proof is not None
    assert isinstance(proof, ExecutionAuthorization)
    assert proof.authorization_rule_id == "no_affecting_act:LOV-2020-77"
    assert proof.replay_authorized is True
    assert proof.detail["affecting_act"] == "LOV-2020-77"


def test_no_affecting_act_yields_no_proof() -> None:
    """An op with no source / blank statute_id has UNKNOWN authority — never fabricated."""
    op = _replace_op("5", "x", statute_id="")
    assert _mint_no_execution_authorization(op) is None
    assert _no_execution_authorization(op) is None


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
    op = _replace_op("5", "x", statute_id="LOV-2020-77", execution_authorization=carried)
    resolved = _no_execution_authorization(op)
    assert resolved is carried
    assert resolved.authorization_rule_id == "carried:rule"


def test_ev05_gate_quiet_for_an_op_with_known_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="LOV-2020-77")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_no_gate_profile(), source_statute="LOV-2020-77"
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
        body, op, provenance=op.source, profile=_no_gate_profile(), source_statute=""
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


# ── AM-01: the NO resolver computes a Parsed-vs-Recovered verdict ─────────────


def test_parsed_op_is_admitted() -> None:
    """No scope_confidence carrier (or an explicit rung) → Parsed → admitted."""
    op = _replace_op("5", "x")
    acc = _no_op_provenance_acceptance(op)
    assert acc is not None
    assert acc.admitted is True
    assert acc.provenance_kind == "parsed"


def test_explicit_rung_op_is_admitted() -> None:
    """An explicit-source-with-context rung is a Parsed op → admitted."""
    op = _replace_op(
        "5", "x", scope_confidence=NOScopeConfidence(rung_id="explicit_source_with_context")
    )
    acc = _no_op_provenance_acceptance(op)
    assert acc is not None
    assert acc.admitted is True
    assert acc.provenance_kind == "parsed"


def test_recovered_op_is_not_admitted() -> None:
    """A scope_confidence inferred_* rung → Recovered → not admitted under strict."""
    op = _replace_op(
        "5", "x", scope_confidence=NOScopeConfidence(rung_id="inferred_from_live_unique")
    )
    acc = _no_op_provenance_acceptance(op)
    assert acc is not None
    assert acc.admitted is False
    assert acc.provenance_kind == "recovered"
    assert acc.acceptance_mode == "strict"
    assert acc.detail["scope_confidence_rung"] == "inferred_from_live_unique"


def test_am01_gate_fires_for_a_recovered_op() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op(
        "5", "New", scope_confidence=NOScopeConfidence(rung_id="inferred_from_live_unique")
    )
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_no_gate_profile(), source_statute="no/amend"
    )
    assert applied.applied
    rec = [
        f for f in applied.observations if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]
    assert len(rec) == 1
    assert not any(
        getattr(f, "kind", None) == RECOVERED_OP_OBSERVED_FINDING_CODE
        for f in applied.findings
    )


def test_am01_gate_silent_for_a_parsed_op() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New")  # parsed, no inferred rung
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_no_gate_profile(), source_statute="no/amend"
    )
    assert applied.applied
    assert not [
        f for f in applied.observations if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]


# ── Byte-identity: the gates route to observations, never production output ────


def test_apply_no_ops_output_is_byte_identical_with_resolvers() -> None:
    """The two resolvers perturb neither the materialized body nor adjudications.

    ``apply_no_ops`` wires both resolvers onto the production profile. Their
    witnesses go to ``AppliedOp.observations`` (drained only via the opt-in
    ``seam_observations_out``); the materialized statute + adjudications the
    byte-identity gates assert on are untouched. A mixed parsed/recovered op set is
    replayed and the output checked.
    """
    body = _body(_section("5", "Original 5"), _section("6", "Original 6"))
    statute = IRStatute(statute_id="no/t", title="T", body=body)
    ops = [
        _replace_op("5", "New 5", op_id="op_parsed"),
        _replace_op(
            "6",
            "New 6",
            op_id="op_recovered",
            scope_confidence=NOScopeConfidence(rung_id="inferred_from_live_unique"),
        ),
    ]
    adjuds_a: list = []
    replayed = apply_no_ops(statute, list(ops), adjudications_out=adjuds_a)
    # The materialized body reflects both writes (resolvers did not block/skip).
    texts = {c.label: c.text for c in replayed.body.children}
    assert texts["5"] == "New 5"
    assert texts["6"] == "New 6"
    # The OBSERVE lane never leaked into production adjudications.
    assert not any(
        getattr(a, "kind", "") == RECOVERED_OP_OBSERVED_FINDING_CODE for a in adjuds_a
    )
    assert not any(
        getattr(a, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for a in adjuds_a
    )


def test_seam_observations_drain_carries_a_recovered_witness() -> None:
    """When the caller opts into the drain, the AM-01 recovered witness surfaces.

    ``seam_observations_out`` collects the OBSERVE-lane findings the production
    fold otherwise discards. A recovered op yields exactly one
    ``APPLY.RECOVERED_OP_OBSERVED`` witness; the parsed op yields none. Both ops
    carry a known affecting act → ZERO EV-05 holes.
    """
    body = _body(_section("5", "Original 5"), _section("6", "Original 6"))
    statute = IRStatute(statute_id="no/t", title="T", body=body)
    ops = [
        _replace_op("5", "New 5", op_id="op_parsed"),
        _replace_op(
            "6",
            "New 6",
            op_id="op_recovered",
            scope_confidence=NOScopeConfidence(rung_id="inferred_from_live_unique"),
        ),
    ]
    drain: list = []
    apply_no_ops(statute, list(ops), seam_observations_out=drain)
    recovered = [
        f for f in drain if getattr(f, "kind", "") == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]
    assert len(recovered) == 1
    auth_holes = [
        f
        for f in drain
        if getattr(f, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    assert auth_holes == []

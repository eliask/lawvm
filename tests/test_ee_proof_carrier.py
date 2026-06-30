"""EE as the first MINTING frontend for the EV-05 proof carrier + AM-01 resolver.

Design reference: ``notes/PROOF_CARRIER_FINDINGS.md`` (the measurement) and
``notes/B_ENFORCEMENT_STATUS.md`` §2 / §7.1 (the EV-05 + AM-01 observe gates).
Until now both gates inherited a NO-OP resolver on every production profile, so
EV-05 read a ~100% firewall hole and AM-01 fired nowhere. EE now wires REAL
resolvers:

* **EV-05** — ``_ee_execution_authorization`` MINTS a typed ExecutionAuthorization
  from each op's amending-act identity (``op.source.statute_id``), or reads a proof
  already minted onto ``op.execution_authorization`` (the generic carrier). An op
  with a known amending act goes QUIET; an op with no amending-act identity has
  unknown authority → no proof → the EV-05 gate fires honestly.
* **AM-01** — ``_ee_op_provenance_acceptance`` classifies the op as Parsed
  (admitted) or Recovered (refused under strict) from EE's OWN
  ``scope_confidence:<rung>`` provenance-tag signal, mirroring FI's
  ``admits``/``mode_for`` WITHOUT importing ``finland/``.

Both are OBSERVE-only: their witnesses route to ``AppliedOp.observations``, never
production ``findings`` — so EE's materialized statute + adjudications stay
byte-identical. EE is NOT flipped to block on either gate.
"""
from __future__ import annotations

from lawvm.core.apply_seam import (
    RECOVERED_OP_OBSERVED_FINDING_CODE,
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
from lawvm.estonia import grafter as ee_grafter
from lawvm.estonia.grafter import (
    _ee_execution_authorization,
    _ee_op_provenance_acceptance,
    _mint_ee_execution_authorization,
    apply_ee_ops,
)


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
    statute_id: str = "ee/amend-2020",
    provenance_tags: tuple[str, ...] = (),
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id or f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=statute_id) if statute_id else None,
        provenance_tags=provenance_tags,
        execution_authorization=execution_authorization,
    )


def _ee_profile():
    """Re-derive EE's production profile fields (resolvers + modes) without a statute."""
    from lawvm.core.apply_seam import ApplyProfile, MaterializeResult

    def _mat(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=ee_grafter._ee_apply_op(before, op))

    return ApplyProfile(
        jurisdiction="ee",
        materializer=_mat,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        receipt_helper_prefix="apply_ee_ops",
        occupancy_resolver=ee_grafter._ee_section_occupancy,
        occupancy_mode="block",
        authorization_resolver=_ee_execution_authorization,
        provenance_resolver=_ee_op_provenance_acceptance,
    )


# ── EV-05: the EE resolver MINTS a real proof from the amending-act identity ───


def test_minted_proof_names_the_amending_act() -> None:
    op = _replace_op("5", "x", statute_id="RT2020/77")
    proof = _mint_ee_execution_authorization(op)
    assert proof is not None
    assert isinstance(proof, ExecutionAuthorization)
    assert proof.authorization_rule_id == "ee_amending_act:RT2020/77"
    assert proof.replay_authorized is True
    assert proof.detail["amending_act"] == "RT2020/77"


def test_no_amending_act_yields_no_proof() -> None:
    """An op with no source / blank statute_id has UNKNOWN authority — never fabricated."""
    op = _replace_op("5", "x", statute_id="")
    assert _mint_ee_execution_authorization(op) is None
    assert _ee_execution_authorization(op) is None


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
    op = _replace_op("5", "x", statute_id="RT2020/77", execution_authorization=carried)
    resolved = _ee_execution_authorization(op)
    assert resolved is carried
    assert resolved.authorization_rule_id == "carried:rule"


def test_ev05_gate_quiet_for_an_op_with_known_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="RT2020/77")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_ee_profile(), source_statute="RT2020/77"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]


def test_ev05_gate_fires_for_an_op_with_unknown_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="")  # no amending act
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_ee_profile(), source_statute=""
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


# ── AM-01: the EE resolver computes a Parsed-vs-Recovered verdict ─────────────


def test_parsed_op_is_admitted() -> None:
    """No scope_confidence tag (or an explicit rung) → Parsed → admitted."""
    op = _replace_op("5", "x")
    acc = _ee_op_provenance_acceptance(op)
    assert acc is not None
    assert acc.admitted is True
    assert acc.provenance_kind == "parsed"


def test_recovered_op_is_not_admitted() -> None:
    """A scope_confidence:inferred_* rung → Recovered → not admitted under strict."""
    op = _replace_op(
        "5", "x", provenance_tags=("scope_confidence:inferred_from_live_unique",)
    )
    acc = _ee_op_provenance_acceptance(op)
    assert acc is not None
    assert acc.admitted is False
    assert acc.provenance_kind == "recovered"
    assert acc.acceptance_mode == "strict"
    assert acc.detail["scope_confidence_rung"] == "inferred_from_live_unique"


def test_am01_gate_fires_for_a_recovered_op() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op(
        "5", "New", provenance_tags=("scope_confidence:inferred_from_live_unique",)
    )
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_ee_profile(), source_statute="ee/amend"
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
        body, op, provenance=op.source, profile=_ee_profile(), source_statute="ee/amend"
    )
    assert applied.applied
    assert not [
        f for f in applied.observations if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]


# ── Byte-identity: the gates route to observations, never production output ────


def test_apply_ee_ops_output_is_byte_identical_with_resolvers() -> None:
    """The two new resolvers perturb neither the materialized body nor adjudications.

    ``apply_ee_ops`` wires both resolvers onto the production profile. Their
    witnesses go to ``AppliedOp.observations`` (drained only via the opt-in
    ``seam_observations_out``); the materialized statute + adjudications the
    byte-identity gates assert on are untouched. A mixed parsed/recovered op set is
    replayed and the output checked.
    """
    body = _body(_section("5", "Original 5"), _section("6", "Original 6"))
    statute = IRStatute(statute_id="ee/t", title="T", body=body)
    ops = [
        _replace_op("5", "New 5", op_id="op_parsed"),
        _replace_op(
            "6",
            "New 6",
            op_id="op_recovered",
            provenance_tags=("scope_confidence:inferred_from_live_unique",),
        ),
    ]
    adjuds_a: list = []
    replayed = apply_ee_ops(statute, list(ops), adjudications_out=adjuds_a)
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

    This is the measurement carrier: ``seam_observations_out`` collects the
    OBSERVE-lane findings the production fold otherwise discards. A recovered op
    yields exactly one ``APPLY.RECOVERED_OP_OBSERVED`` witness; the parsed op
    yields none — the non-trivial AM-01 measurement in the small.
    """
    body = _body(_section("5", "Original 5"), _section("6", "Original 6"))
    statute = IRStatute(statute_id="ee/t", title="T", body=body)
    ops = [
        _replace_op("5", "New 5", op_id="op_parsed"),
        _replace_op(
            "6",
            "New 6",
            op_id="op_recovered",
            provenance_tags=("scope_confidence:inferred_from_live_unique",),
        ),
    ]
    drain: list = []
    apply_ee_ops(statute, list(ops), seam_observations_out=drain)
    recovered = [
        f for f in drain if getattr(f, "kind", "") == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]
    assert len(recovered) == 1
    # Every landed op carries a known amending act → ZERO EV-05 holes (the measured
    # EE authorized fraction is 100% on the corpus; here both ops are authorized).
    auth_holes = [
        f
        for f in drain
        if getattr(f, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    assert auth_holes == []

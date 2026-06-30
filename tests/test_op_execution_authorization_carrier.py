"""EV-05 PROOF CARRIER on ``core/ir.LegalOperation`` + the generic seam resolver.

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` §2 (the EV-05 observe gate)
and ``notes/CROSS_JURISDICTION_PARITY.md`` (the EV-05 "not-yet-a-fix" row:
"Closing it requires a proof carrier on core/ir.LegalOperation — a framework
change"). THIS is that carrier.

WHAT THIS PROVES. ``LegalOperation`` now carries an additive optional
``execution_authorization: Optional[ExecutionAuthorization]`` rider (``None``
default → every existing construction stays valid and byte-identical), validated
as a typed carrier in ``__post_init__`` (a bare dict / string fails loud — §1.9 /
§1.10). The generic core resolver ``read_op_execution_authorization`` reads it.
Wired onto a profile, the EV-05 observe gate goes QUIET for an op carrying a proof
with a non-empty ``authorization_rule_id`` and FIRES on an op carrying none — the
firewall hole becomes the real unauthorized residue, not ~100% by construction.
"""
from __future__ import annotations

from typing import Any

import pytest

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
    no_op_execution_authorization,
    read_op_execution_authorization,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind, StructuralAction


def _addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _proof(rule_id: str = "act/2025:s3") -> ExecutionAuthorization:
    """A minimal, VALID replay-authorized ExecutionAuthorization proof."""
    return ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id=rule_id,
        owner_phase="apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        safe_default="execute_only_after_authority_is_known",
        required_proofs=(),
    )


def _op(
    op_id: str = "a",
    label: str = "1",
    *,
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"new {label}"),
        source=OperationSource(statute_id="act/2025", effective="2026-01-01"),
        execution_authorization=execution_authorization,
    )


def _body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=tuple(
            IRNode(kind=IRNodeKind.SECTION, label=str(n), text=f"Original {n}")
            for n in (1, 2, 3)
        ),
    )


def _tree_materializer(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
    label = op.target.leaf_label()
    path = tree_ops.find(before, "section", label) if label else None
    if path is None:
        return MaterializeResult(new_state=before, applied=False)
    node = tree_ops.resolve(before, list(path))
    if node is None:
        return MaterializeResult(new_state=before, applied=False)
    new_node = IRNode(kind=node.kind, label=node.label, text="patched")
    return MaterializeResult(new_state=tree_ops.replace_at(before, path, new_node))


def _profile(*, authorization_resolver=no_op_execution_authorization) -> ApplyProfile[IRNode]:
    return ApplyProfile(
        jurisdiction="syn",
        materializer=_tree_materializer,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        authorization_resolver=authorization_resolver,
    )


# ── The carrier round-trips on LegalOperation ─────────────────────────────────


def test_carrier_defaults_to_none() -> None:
    """An op constructed without the rider carries no proof (the production state)."""
    op = _op()
    assert op.execution_authorization is None


def test_carrier_round_trips_a_real_proof() -> None:
    """A real ExecutionAuthorization survives onto the op verbatim."""
    proof = _proof("act/2025:s3")
    op = _op(execution_authorization=proof)
    assert op.execution_authorization is proof
    assert op.execution_authorization.authorization_rule_id == "act/2025:s3"


def _make_legal_operation(**overrides: Any) -> LegalOperation:
    """LegalOperation constructor whose ``overrides`` are typed ``Any``.

    Mirrors ``tests/test_scope_confidence_protocol._make_legal_operation`` — the
    repo convention for a deliberate fire-drill: ``Any`` overrides let a bare
    non-typed value cross the ``LegalOperation`` waist so the test exercises the
    RUNTIME ``__post_init__`` fail-loud gate, not a static type error.
    """
    defaults: dict[str, Any] = {
        "op_id": "z",
        "sequence": 1,
        "action": StructuralAction.REPLACE,
        "target": _addr("1"),
    }
    defaults.update(overrides)
    return LegalOperation(**defaults)


def test_carrier_rejects_a_bare_non_authorization_value() -> None:
    """A non-ExecutionAuthorization rider fails loud at the typed waist (§1.9/§1.10)."""
    with pytest.raises(TypeError):
        _make_legal_operation(execution_authorization={"authorization_rule_id": "x"})
    with pytest.raises(TypeError):
        _make_legal_operation(execution_authorization="replay_authorized")


# ── The generic core resolver reads the carrier ───────────────────────────────


def test_read_op_execution_authorization_reads_the_carrier() -> None:
    """The generic resolver hands back exactly the op's carrier (or None)."""
    assert read_op_execution_authorization(_op()) is None
    proof = _proof()
    assert read_op_execution_authorization(_op(execution_authorization=proof)) is proof


def test_default_resolver_is_no_op() -> None:
    """The kernel default still resolves NO proof (the honest firewall-hole default)."""
    assert no_op_execution_authorization(_op(execution_authorization=_proof())) is None


# ── Wired onto a profile, the EV-05 gate goes quiet / fires by carrier ─────────


def test_carried_proof_quiets_the_ev05_gate() -> None:
    """An op carrying a proof (read via the generic resolver) emits NO EV-05 witness."""
    op = _op(execution_authorization=_proof())
    profile = _profile(authorization_resolver=read_op_execution_authorization)
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]


def test_uncarried_op_still_fires_the_ev05_gate() -> None:
    """An op with NO carried proof (same generic resolver) emits the EV-05 witness."""
    op = _op()  # no execution_authorization
    profile = _profile(authorization_resolver=read_op_execution_authorization)
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    hole = [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]
    assert len(hole) == 1
    # The witness lives on observations, NEVER on findings (byte-identity).
    assert not any(
        getattr(f, "kind", None) == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for f in applied.findings
    )


def test_empty_rule_id_proof_does_not_quiet_the_gate() -> None:
    """A carried proof with a blank rule_id is not a real proof — the gate fires.

    The EV-05 gate requires a non-empty ``authorization_rule_id``;
    ``ExecutionAuthorization`` itself forbids an empty one, so a carrier can never
    smuggle a blank-rule "proof" past the gate. This pins that contract.
    """
    with pytest.raises(ValueError):
        ExecutionAuthorization(
            executable=True,
            replay_authorized=True,
            authorization_status="replay_authorized",
            authorization_rule_id="",  # blank — rejected at construction
            owner_phase="apply",
            strict_disposition="record",
            quirks_disposition=QuirksDisposition.RECORD,
            safe_default="x",
        )

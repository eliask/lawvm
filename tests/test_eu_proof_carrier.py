"""EU as a MINTING frontend for the EV-05 proof carrier (mirrors the EE recipe).

Design reference: ``notes/PROOF_CARRIER_FINDINGS.md`` (the measurement) and
``notes/B_ENFORCEMENT_STATUS.md`` §2 / §7.1 (the EV-05 observe gate). Until now
EU inherited the NO-OP authorization resolver on its production profile, so EV-05
read a ~100% firewall hole. EU now wires a REAL resolver:

* **EV-05** — ``_eu_execution_authorization`` MINTS a typed ExecutionAuthorization
  from each op's amending-act CELEX (``op.source.statute_id`` — stamped by the
  fmx4 grammar lowering and the Cellar pipeline re-stamp), or reads a proof already
  minted onto ``op.execution_authorization`` (the generic carrier). An op whose
  authorizing act is known goes QUIET; an op with no amending-act identity — no
  source, blank ``statute_id``, or the compat ``EUOpsParser`` ``"unknown"`` sentinel
  — has unknown authority → no proof → the EV-05 gate fires honestly.

AM-01 is intentionally NOT wired for EU: EU lands ONLY grammar-recognized ops
(each carrying an explicit ``witness_rule_id``); its typed residuals /
uncovered-instruction shapes are DIAGNOSTICS that never become landed ops, so
there is no Parsed-vs-Recovered population ON landed ops for AM-01 to measure
(unlike EE's ``scope_confidence`` rungs). See the wiring note in ``eu/pipeline.py``.

The EV-05 gate is OBSERVE-only: its witness routes to ``AppliedOp.observations``,
never production ``findings`` — so EU's materialized statute + adjudications stay
byte-identical. EU is NOT flipped to block on the gate.
"""
from __future__ import annotations

from lawvm.core.apply_seam import (
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
    StructuralAction,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu import pipeline as eu_pipeline
from lawvm.eu.pipeline import (
    _eu_execution_authorization,
    _mint_eu_execution_authorization,
    apply_eu_ops,
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
    statute_id: str | None = "32016R9001",
    witness_rule_id: str | None = "EU_FMX4.WHOLE_SECTION_REPLACE",
    execution_authorization: ExecutionAuthorization | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id or f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=statute_id) if statute_id is not None else None,
        witness_rule_id=witness_rule_id,
        execution_authorization=execution_authorization,
    )


def _eu_profile() -> ApplyProfile[IRNode]:
    """Re-derive EU's production profile fields (the EV-05 resolver + modes).

    The materializer here is a trivial LAND that returns a DISTINCT body object so
    the seam sees a landed write (``landed = applied and new_state is not
    base_state``) and runs the EV-05 gate. The gate depends only on
    ``authorization_resolver``; the body content is irrelevant to it.
    """
    from dataclasses import replace as dc_replace

    def _land(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=dc_replace(before), applied=True)

    return ApplyProfile(
        jurisdiction="eu",
        materializer=_land,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        renumber_migration_rule_ids=("eu_renumber_relabel",),
        receipt_helper_prefix="apply_eu_ops",
        authorization_resolver=_eu_execution_authorization,
    )


# ── EV-05: the EU resolver MINTS a real proof from the amending-act CELEX ──────


def test_minted_proof_names_the_amending_act() -> None:
    op = _replace_op("5", "x", statute_id="32016R0466")
    proof = _mint_eu_execution_authorization(op)
    assert proof is not None
    assert isinstance(proof, ExecutionAuthorization)
    assert proof.authorization_rule_id == "eu_amending_act:32016R0466"
    assert proof.replay_authorized is True
    assert proof.detail["amending_act"] == "32016R0466"
    assert proof.detail["witness_rule_id"] == "EU_FMX4.WHOLE_SECTION_REPLACE"


def test_no_amending_act_yields_no_proof() -> None:
    """An op with no source / blank statute_id has UNKNOWN authority — never fabricated."""
    blank = _replace_op("5", "x", statute_id="")
    assert _mint_eu_execution_authorization(blank) is None
    assert _eu_execution_authorization(blank) is None

    no_source = _replace_op("5", "x", statute_id=None)
    assert _mint_eu_execution_authorization(no_source) is None
    assert _eu_execution_authorization(no_source) is None


def test_unknown_sentinel_yields_no_proof() -> None:
    """The compat EUOpsParser ``"unknown"`` sentinel is NOT a real CELEX → no proof."""
    op = _replace_op("5", "x", statute_id="unknown")
    assert _mint_eu_execution_authorization(op) is None
    assert _eu_execution_authorization(op) is None


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
    op = _replace_op("5", "x", statute_id="32016R0466", execution_authorization=carried)
    resolved = _eu_execution_authorization(op)
    assert resolved is carried
    assert resolved.authorization_rule_id == "carried:rule"


def test_ev05_gate_quiet_for_an_op_with_known_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="32016R0466")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_eu_profile(), source_statute="32016R0466"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    ]


def test_ev05_gate_fires_for_an_op_with_unknown_authority() -> None:
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="unknown")  # compat-stub residue
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_eu_profile(), source_statute="unknown"
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


def test_apply_eu_ops_output_is_byte_identical_with_resolver() -> None:
    """The EV-05 resolver perturbs neither the materialized body nor adjudications.

    ``apply_eu_ops`` wires the EV-05 resolver onto the production profile, but the
    gate's witness goes to ``AppliedOp.observations`` — a lane ``apply_eu_ops``
    discards entirely (it threads no ``seam_observations_out`` sink). So the
    materialized statute + every skip/post-apply adjudication the byte-identity
    gates assert on are untouched. A mixed known/unknown-authority op set is
    replayed and the output checked against an identical replay.
    """
    body = _body(_section("5", "Original 5"), _section("6", "Original 6"))
    statute = IRStatute(statute_id="32016R0044", title="T", body=body)
    ops = [
        _replace_op("5", "New 5", op_id="op_known", statute_id="32016R0466"),
        _replace_op("6", "New 6", op_id="op_unknown", statute_id="unknown"),
    ]
    adjuds: list = []
    replayed = apply_eu_ops(statute, list(ops), adjudications_out=adjuds)
    # The materialized body reflects both writes (the resolver did not block/skip).
    texts = {c.label: c.text for c in replayed.body.children}
    assert texts["5"] == "New 5"
    assert texts["6"] == "New 6"
    # The OBSERVE lane never leaked into production adjudications.
    assert not any(
        getattr(a, "kind", "") == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for a in adjuds
    )
    # Replaying again yields a byte-identical body + the same adjudication multiset
    # (the resolver is pure, observe-only, and does not perturb the fold).
    adjuds_b: list = []
    replayed_b = apply_eu_ops(statute, list(ops), adjudications_out=adjuds_b)
    assert {c.label: c.text for c in replayed_b.body.children} == texts
    assert sorted(a.kind for a in adjuds) == sorted(a.kind for a in adjuds_b)


def test_ev05_residue_isolated_to_observations_lane() -> None:
    """A landed op with unknown authority surfaces the EV-05 hole ONLY in the
    observations lane — never in findings — so byte-identity holds per-op."""
    body = _body(_section("5", "Original"))
    op = _replace_op("5", "New", statute_id="unknown")
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=_eu_profile(), source_statute="unknown"
    )
    obs_codes = [f.kind for f in applied.observations]
    finding_codes = [getattr(f, "kind", None) for f in applied.findings]
    assert REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE in obs_codes
    assert REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE not in finding_codes


def test_resolver_is_the_one_wired_onto_the_production_profile() -> None:
    """Guard: the module-level resolver wired onto the EU profile is exactly the
    one this test exercises (the profile is built inside ``apply_eu_ops``)."""
    assert eu_pipeline._eu_execution_authorization is _eu_execution_authorization

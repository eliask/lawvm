"""B-enforcement increment 4: the universal AM-01 provenance-acceptance OBSERVE gate.

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` (the FI-battery → seam mapping
+ the staged enforcement path) and FI's
``finland/apply_resolved_op._gate_provenance_acceptance_at_op`` (the strict-only,
FI-only AM-01 producer): the typed-acceptance closure ``admits(mode_for(profile,
prov), prov)`` over a typed ``OpProvenance`` decides whether a strict consumer may
accept a state-mutating op given HOW it was derived (a ``Recovered``/guessed op a
strict consumer would refuse; a ``Parsed``/grammar-recognized op is always
admitted).

WHAT THIS GATE IS. ``core/apply_seam.apply_op`` now runs a UNIVERSAL, per-profile
provenance-acceptance closure for every state-mutating op. It is the
GENERALIZATION of FI's per-frontend gate. OBSERVE-first (design §5): when a
profile supplies a ``provenance_resolver`` — the typed acceptance machinery
(``OpProvenance`` / ``mode_for`` / ``admits``) is FI-owned and the kernel does NOT
import ``finland/``, so the resolver hands the kernel only the core-neutral
``OpAcceptance`` verdict it already computed — a mutating op whose verdict is NOT
admitted emits one non-blocking ``APPLY.RECOVERED_OP_OBSERVED`` observation to the
SEPARATE :attr:`AppliedOp.observations` lane — NEVER to :attr:`AppliedOp.findings`.
That separation is the byte-identity mechanism: the production
findings/adjudication multiset the five tree seam gates + the US boundary test
assert on is untouched.

THE DEFAULT IS 0-DELTA. The kernel-default resolver ``no_op_provenance`` models no
provenance (returns ``None`` for every op), so all 6 production profiles inherit a
no-op gate — the AM-01 hole, surfaced WITHOUT any production-output change. A
profile that supplies a resolver lights the gate up; this test proves the gate
fires on a not-admitted verdict, stays silent on an admitted one, stays silent
(and 0-delta) when no resolver is supplied, never leaks into ``findings``, and is
silent on a skipped op.
"""
from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    RECOVERED_OP_OBSERVED_FINDING_CODE,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    OpAcceptance,
    apply_op,
    no_op_provenance,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind


# ── A small tree materializer + op corpus shared across the cases ─────────────


def _addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _op(op_id: str, label: str, action: StructuralAction) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=action,
        target=_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"new {label}"),
        source=OperationSource(statute_id="act/2025", effective="2026-01-01"),
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
    """A minimal section-patch materializer that lands a CoW write on the target."""
    label = op.target.leaf_label()
    path = tree_ops.find(before, "section", label) if label else None
    if path is None:
        return MaterializeResult(new_state=before, applied=False)
    node = tree_ops.resolve(before, list(path))
    if node is None:
        return MaterializeResult(new_state=before, applied=False)
    new_node = IRNode(kind=node.kind, label=node.label, text="patched")
    return MaterializeResult(new_state=tree_ops.replace_at(before, path, new_node))


def _profile(
    jurisdiction: str = "syn",
    *,
    provenance_resolver=no_op_provenance,
) -> ApplyProfile[IRNode]:
    """A representative ``boundary_mode="off"`` tree profile (the production shape)."""
    return ApplyProfile(
        jurisdiction=jurisdiction,
        materializer=_tree_materializer,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        provenance_resolver=provenance_resolver,
    )


def _fixed_acceptance(acceptance: OpAcceptance):
    """A synthetic resolver that reports a FIXED acceptance verdict for every op.

    FI is the reference for the real typed-acceptance closure (it computes
    ``admits(mode_for(profile, prov), prov)`` over the op's typed ``OpProvenance``);
    this synthetic resolver pins the verdict so the test drives a known acceptance
    case through the seam — without editing any frontend.
    """

    def _resolver(_op: LegalOperation) -> OpAcceptance:
        return acceptance

    return _resolver


# ── The registry contract for the new observation code ────────────────────────


def test_observation_code_registered_as_observation_role() -> None:
    """The new code is a fresh observation-role twin of the FI strict block."""
    spec = get_finding_spec(RECOVERED_OP_OBSERVED_FINDING_CODE)
    assert spec is not None
    assert spec.role == "observation"
    assert spec.default_enforcement == "warn"
    # The strict-blocking violation twin is a DISTINCT code, unchanged.
    rejected = get_finding_spec("APPLY.RECOVERED_OP_REJECTED_IN_STRICT")
    assert rejected is not None
    assert rejected.role == "violation"
    assert RECOVERED_OP_OBSERVED_FINDING_CODE != "APPLY.RECOVERED_OP_REJECTED_IN_STRICT"


def test_default_resolver_is_no_op_provenance() -> None:
    """The kernel-default profile models no provenance (the 0-delta production case)."""
    profile = _profile()
    assert profile.provenance_resolver is no_op_provenance
    assert no_op_provenance(_op("a", "1", StructuralAction.REPLACE)) is None


# ── The gate fires on a NOT-admitted verdict ──────────────────────────────────


def test_not_admitted_verdict_emits_observation() -> None:
    """A not-admitted acceptance verdict → exactly one non-blocking observation."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(
        provenance_resolver=_fixed_acceptance(
            OpAcceptance(
                admitted=False,
                acceptance_mode="strict",
                provenance_kind="recovered",
                detail={"recovery_surface": "body", "confidence_tier": "heuristic"},
            )
        )
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    obs_list = [
        f
        for f in applied.observations
        if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]
    assert len(obs_list) == 1
    obs = obs_list[0]
    assert isinstance(obs, Finding)
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail["op_id"] == "a"
    assert obs.detail["action"] == "replace"
    assert obs.detail["acceptance_mode"] == "strict"
    assert obs.detail["provenance_kind"] == "recovered"
    # The resolver's jurisdiction-specific diagnostics are folded in verbatim.
    assert obs.detail["recovery_surface"] == "body"
    assert obs.detail["confidence_tier"] == "heuristic"
    assert obs.detail["owner"] == "apply_seam_provenance_acceptance_observe"


# ── The gate stays SILENT on an admitted verdict ──────────────────────────────


def test_admitted_verdict_emits_nothing() -> None:
    """An admitted (Parsed-equivalent) verdict met the closure → no observation."""
    op = _op("p", "2", StructuralAction.REPLACE)
    profile = _profile(
        provenance_resolver=_fixed_acceptance(
            OpAcceptance(admitted=True, acceptance_mode="quirks", provenance_kind="parsed")
        )
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]


# ── 0-delta when no resolver is supplied (the production state) ───────────────


def test_no_resolver_is_zero_delta() -> None:
    """The default (no provenance model) profile emits no provenance observation."""
    op = _op("d", "3", StructuralAction.REPLACE)
    profile = _profile()  # default no_op_provenance
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert not [
        f
        for f in applied.observations
        if f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE
    ]


# ── The witness NEVER leaks into the production findings lane ──────────────────


def test_observation_never_leaks_into_findings() -> None:
    """The not-admitted witness lives on ``observations``, never on ``findings``."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(
        provenance_resolver=_fixed_acceptance(
            OpAcceptance(admitted=False, acceptance_mode="strict", provenance_kind="recovered")
        )
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert any(
        f.kind == RECOVERED_OP_OBSERVED_FINDING_CODE for f in applied.observations
    )
    assert not any(
        getattr(f, "kind", None) == RECOVERED_OP_OBSERVED_FINDING_CODE
        for f in applied.findings
    )


# ── A skipped op (no landed write) is silent ──────────────────────────────────


def test_skipped_op_is_silent() -> None:
    """An op that lands no write resolves no provenance → no observation."""
    op = _op("missing", "99", StructuralAction.REPLACE)  # label not in body
    profile = _profile(
        provenance_resolver=_fixed_acceptance(
            OpAcceptance(admitted=False, acceptance_mode="strict", provenance_kind="recovered")
        )
    )
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert not applied.applied
    assert not applied.observations

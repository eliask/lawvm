"""B-enforcement increment 1: the universal ExecutionAuthorization OBSERVE gate.

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` (the staged enforcement path)
and ``notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md`` EV-05 / FW-01 / OV-01 (the
ExecutionAuthorization-at-apply cluster: "apply_structure_ops/apply_runtime_support
have ZERO references to ExecutionAuthorization" — the firewall TYPE exists but
apply never checked it).

WHAT THIS GATE IS. ``core/apply_seam.apply_op`` now runs a UNIVERSAL,
metric-agnostic ExecutionAuthorization closure for every state-mutating op, for
ALL 6 frontends. OBSERVE-first (design §5): a mutating op carrying no resolvable
``ExecutionAuthorization`` proof emits one non-blocking
``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` observation to the SEPARATE
:attr:`AppliedOp.observations` lane — NEVER to :attr:`AppliedOp.findings`. That
separation is the byte-identity mechanism: the production findings/adjudication
multiset the five tree seam gates + the US boundary test assert on is untouched
(those gates stay green), while the firewall hole becomes visible and gated for
the first time, universally.

THE MEASURED GAP. With no op carrying a proof today
(``core/ir.LegalOperation`` has no authorization field; the kernel-default
resolver ``no_op_execution_authorization`` returns ``None`` for every op), the
gap is ~100% of mutating ops BY CONSTRUCTION — the honest firewall-hole size. The
``test_*_authorization_gap_is_total`` cases MEASURE this per profile and report
the real numbers, and ``test_resolved_authorization_closes_the_gate`` proves the
gate goes quiet once a proof is minted (the increment-2 promotion target).
"""
from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
    no_op_execution_authorization,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind


# ── A small tree materializer + op corpus shared across the profile cases ─────


def _addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _replace(op_id: str, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
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
    """A minimal REPLACE materializer: patch the targeted section's text via CoW."""
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
    jurisdiction: str,
    *,
    authorization_resolver=no_op_execution_authorization,
) -> ApplyProfile[IRNode]:
    """One representative tree profile per frontend tag.

    All 6 production profiles run ``boundary_mode="off"`` and inherit the
    kernel-default ``authorization_resolver`` (no op carries a proof), so this
    fixture mirrors the production seam shape the gap measurement reflects.
    """
    return ApplyProfile(
        jurisdiction=jurisdiction,
        materializer=_tree_materializer,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        authorization_resolver=authorization_resolver,
    )


# The 6 profile tags the seam serves (the 5 tree frontends + US's tag). US runs a
# char-span lane in production, but the ExecutionAuthorization OBSERVE gate is
# metric-agnostic — it fires on any LANDED op regardless of metric — so the
# measurement is representative for all six.
_PROFILE_TAGS = ("no", "se", "ee", "eu", "uk", "us")


def _measure_gap(
    profile: ApplyProfile[IRNode], ops: list[LegalOperation]
) -> tuple[int, int]:
    """Return ``(mutating_ops, ops_without_authorization_observed)``.

    Folds the op set through ``apply_op`` and counts (a) how many ops LANDED a
    write (mutated state) and (b) how many of those emitted the
    ``EVID.REPLAY_AUTHORIZATION_PROOF_OBSERVED`` firewall-hole observation.
    """
    body = _body()
    mutating = 0
    without_auth = 0
    for op in ops:
        applied: AppliedOp[IRNode] = apply_op(
            body, op, provenance=op.source, profile=profile, source_statute="act/2025"
        )
        if applied.applied:
            mutating += 1
            if any(
                f.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
                for f in applied.observations
            ):
                without_auth += 1
        body = applied.new_state
    return mutating, without_auth


def test_no_op_execution_authorization_default_resolves_nothing() -> None:
    """The honest default resolver returns ``None`` for every op (the hole)."""
    op = _replace("x", "1")
    assert no_op_execution_authorization(op) is None


def test_authorization_gap_is_total_per_profile() -> None:
    """MEASUREMENT: every mutating op lacks an ExecutionAuthorization, for all 6
    profiles. The gap is 100% by construction — the honest firewall-hole size."""
    ops = [_replace("a", "1"), _replace("b", "2"), _replace("c", "3")]
    for tag in _PROFILE_TAGS:
        mutating, without_auth = _measure_gap(_profile(tag), ops)
        assert mutating == 3, f"{tag}: expected 3 mutating ops, got {mutating}"
        # 100% firewall-hole: every mutating op is unauthorized today.
        assert without_auth == mutating, (
            f"{tag}: ExecutionAuthorization gap not total "
            f"({without_auth}/{mutating} mutating ops unauthorized)"
        )


def test_observe_lane_is_separate_from_production_findings() -> None:
    """BYTE-IDENTITY MECHANISM: the gate emits ONLY into ``observations``; the
    production ``findings`` lane is untouched (no new authorization finding)."""
    op = _replace("a", "1")
    applied = apply_op(
        _body(), op, provenance=op.source, profile=_profile("no"),
        source_statute="act/2025",
    )
    assert applied.applied
    # The firewall-hole witness lives in the SEPARATE observe lane.
    assert len(applied.observations) == 1
    obs = applied.observations[0]
    assert isinstance(obs, Finding)
    assert obs.kind == REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail["op_id"] == "a"
    assert obs.detail["jurisdiction"] == "no"
    # The production findings lane carries NO authorization finding (the
    # boundary mode is off, so it is empty here — and critically the observe
    # finding did not leak into it).
    assert all(
        getattr(f, "kind", None) != REPLAY_AUTHORIZATION_PROOF_OBSERVED_FINDING_CODE
        for f in applied.findings
    )
    assert all(
        getattr(f, "kind", None)
        != "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED"
        for f in applied.findings
    )


def test_skipped_op_emits_no_observation() -> None:
    """A non-mutating (skipped) op landed no write → no firewall-hole witness."""
    miss = _replace("miss", "999")  # target absent → materializer skips
    applied = apply_op(
        _body(), miss, provenance=miss.source, profile=_profile("no"),
        source_statute="act/2025",
    )
    assert not applied.applied
    assert applied.observations == ()


def test_resolved_authorization_closes_the_gate() -> None:
    """PROMOTION TARGET: once a frontend mints a proof, the gate goes quiet.

    Supplies a resolver that returns a real ``ExecutionAuthorization`` with a
    non-empty ``authorization_rule_id``; the mutating op then emits NO firewall-
    hole observation. This is the increment-2 state the observe gate is staged to
    promote to a strict block per profile."""
    def _resolver(op: LegalOperation) -> ExecutionAuthorization:
        return ExecutionAuthorization(
            executable=True,
            replay_authorized=True,
            authorization_status="replay_authorized",
            authorization_rule_id=op.op_id or "rule",
            owner_phase="apply",
            strict_disposition="record",
            safe_default="execute_only_after_phase_local_gate",
        )

    profile = _profile("no", authorization_resolver=_resolver)
    mutating, without_auth = _measure_gap(profile, [_replace("a", "1")])
    assert mutating == 1
    assert without_auth == 0, "a resolved authorization must close the observe gate"

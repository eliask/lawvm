"""B-enforcement increment 3: the universal LS-03 occupancy-transition OBSERVE gate.

Design reference: ``notes/B_ENFORCEMENT_STATUS.md`` (the FI-battery → seam
mapping + the staged enforcement path) and ``notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md``
LS-03 (occupancy gate-liveness): the occupancy TYPE + the raising
``core/occupancy.validate_transition`` exist (the (action, from)->to
``VALID_TRANSITIONS`` table), but "the type+raise exist but no frontend currently
BLOCKS; the gate is telemetry."

WHAT THIS GATE IS. ``core/apply_seam.apply_op`` now runs a UNIVERSAL,
per-profile occupancy-transition closure for every state-mutating op. It is the
GENERALIZATION of FI's ``finland/apply_resolved_op._gate_occupancy_transition_at_op``
(FI-only + strict-only). OBSERVE-first (design §5): when a profile supplies an
``occupancy_resolver`` (FI is the reference for the (action, from)->to table
semantics; the kernel does NOT import ``finland/``), a mutating op whose (action,
from-occupancy) pair is NOT in ``VALID_TRANSITIONS`` emits one non-blocking
``APPLY.OCCUPANCY_TRANSITION_OBSERVED`` observation to the SEPARATE
:attr:`AppliedOp.observations` lane — NEVER to :attr:`AppliedOp.findings`. That
separation is the byte-identity mechanism: the production findings/adjudication
multiset the five tree seam gates + the US boundary test assert on is untouched.

THE DEFAULT IS 0-DELTA. The kernel-default resolver ``no_op_occupancy`` models
no occupancy (returns ``None`` for every op), so all 6 production profiles inherit
a no-op gate — the LS-03 guard-liveness hole, surfaced WITHOUT any production-
output change. A profile that supplies a resolver lights the gate up; this test
proves the gate fires on a configured bad transition, stays silent on a good one,
and stays silent (and 0-delta) when no resolver is supplied.
"""
from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE,
    STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION,
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
    no_op_occupancy,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.occupancy import OccupancyAction, OccupancyClass
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
    occupancy_resolver=no_op_occupancy,
) -> ApplyProfile[IRNode]:
    """A representative ``boundary_mode="off"`` tree profile (the production shape)."""
    return ApplyProfile(
        jurisdiction=jurisdiction,
        materializer=_tree_materializer,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        occupancy_resolver=occupancy_resolver,
    )


def _fixed_occupancy(occupancy: OccupancyClass):
    """A synthetic resolver that reports a FIXED before-occupancy for every op.

    FI is the reference for the real (action, from)->to table semantics (it reads
    the live slot occupancy); this synthetic resolver pins the before-occupancy so
    the test drives a known (action, from) pair through the SAME core
    ``validate_transition`` table — without editing any frontend.
    """

    def _resolver(
        _op: LegalOperation, _before: object, _after: object
    ) -> OccupancyClass:
        return occupancy

    return _resolver


# ── The registry contract for the new observation code ────────────────────────


def test_observation_code_registered_as_observation_role() -> None:
    """The new code is a fresh observation-role twin of the FI strict block."""
    spec = get_finding_spec(OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE)
    assert spec is not None
    assert spec.role == "observation"
    # The strict-blocking violation twin is a DISTINCT code, unchanged.
    blocked = get_finding_spec("APPLY.OCCUPANCY_TRANSITION_BLOCKED")
    assert blocked is not None
    assert blocked.role == "violation"
    assert OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE != "APPLY.OCCUPANCY_TRANSITION_BLOCKED"


def test_action_map_matches_the_occupancy_modelled_actions() -> None:
    """The structural→occupancy action map mirrors FI's three occupancy actions."""
    assert STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION == {
        "replace": OccupancyAction.REPLACE,
        "insert": OccupancyAction.INSERT,
        "repeal": OccupancyAction.REPEAL,
    }


# ── The gate fires on a configured BAD transition ─────────────────────────────


def test_invalid_transition_emits_observation() -> None:
    """A REPLACE onto an ABSENT slot is an invalid transition → one observation.

    ``(REPLACE, ABSENT)`` is NOT in ``VALID_TRANSITIONS`` (only ``(REPLACE,
    SUBSTANTIVE)`` is), so the configured resolver drives the gate to fire.
    """
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.ABSENT))
    applied: AppliedOp[IRNode] = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    occupancy_obs = [
        f
        for f in applied.observations
        if f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
    ]
    assert len(occupancy_obs) == 1
    obs = occupancy_obs[0]
    assert isinstance(obs, Finding)
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail["op_id"] == "a"
    assert obs.detail["action"] == "replace"
    assert obs.detail["current_occupancy"] == "absent"


def test_repeal_onto_absent_slot_is_invalid() -> None:
    """``(REPEAL, ABSENT)`` is also not a valid transition → observation fires."""
    op = _op("r", "2", StructuralAction.REPEAL)
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.ABSENT))
    applied = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )


# ── The gate stays SILENT on a good transition ────────────────────────────────


def test_valid_transition_emits_no_occupancy_observation() -> None:
    """A REPLACE onto a SUBSTANTIVE slot is valid → no occupancy observation."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.SUBSTANTIVE))
    applied = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert applied.applied
    assert not any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )


def test_insert_onto_tombstone_is_a_valid_reenactment() -> None:
    """``(INSERT, TOMBSTONE)`` is a valid reenactment → no observation."""
    op = _op("i", "1", StructuralAction.INSERT)
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.TOMBSTONE))
    applied = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    assert not any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )


# ── The gate is a 0-delta no-op when no resolver is supplied ───────────────────


def test_no_resolver_means_no_occupancy_gate() -> None:
    """The default resolver models no occupancy → the gate is a no-op (0-delta).

    This is the production state for all 6 frontends today: they inherit
    ``no_op_occupancy``, so even an op that WOULD be an invalid transition under a
    real occupancy model emits nothing.
    """
    op = _op("a", "1", StructuralAction.REPLACE)
    applied = apply_op(
        _body(), op, provenance=op.source, profile=_profile(),
        source_statute="act/2025",
    )
    assert applied.applied
    assert not any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )


def test_no_op_occupancy_default_resolves_nothing() -> None:
    """The honest default resolver returns ``None`` for every op (the hole)."""
    op = _op("a", "1", StructuralAction.REPLACE)
    assert no_op_occupancy(op, _body(), _body()) is None


# ── The gate skips non-occupancy actions even WITH a resolver ──────────────────


def test_renumber_action_is_not_occupancy_gated() -> None:
    """RENUMBER carries no whole-slot occupancy meaning → gate skips it.

    Even with a resolver that reports an occupancy that WOULD be invalid for some
    action, a RENUMBER op (absent from ``STRUCTURAL_ACTION_TO_OCCUPANCY_ACTION``)
    is never validated — mirroring FI's ``action_value is None`` skip.
    """
    # RENUMBER needs a destination; build it directly.
    op = LegalOperation(
        op_id="rn",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=_addr("1"),
        destination=_addr("1b"),
        source=OperationSource(statute_id="act/2025", effective="2026-01-01"),
    )
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.ABSENT))
    applied = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    # Whether or not the RENUMBER landed a write, no occupancy observation fires.
    assert not any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )


# ── BYTE-IDENTITY: the gate never touches production ``findings`` ─────────────


def test_occupancy_observation_never_leaks_into_findings() -> None:
    """The byte-identity crux: the witness lands ONLY in ``observations``."""
    op = _op("a", "1", StructuralAction.REPLACE)
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.ABSENT))
    applied = apply_op(
        _body(), op, provenance=op.source, profile=profile, source_statute="act/2025"
    )
    # The witness IS in the observe lane ...
    assert any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )
    # ... and is ABSENT from the production findings lane (and so is its strict
    # violation twin — the observe code never promotes to authority).
    assert all(
        getattr(f, "kind", None) != OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.findings
    )
    assert all(
        getattr(f, "kind", None) != "APPLY.OCCUPANCY_TRANSITION_BLOCKED"
        for f in applied.findings
    )


def test_skipped_op_emits_no_occupancy_observation() -> None:
    """A non-mutating (skipped) op landed no write → no occupancy witness."""
    miss = _op("miss", "999", StructuralAction.REPLACE)  # target absent → skip
    profile = _profile(occupancy_resolver=_fixed_occupancy(OccupancyClass.ABSENT))
    applied = apply_op(
        _body(), miss, provenance=miss.source, profile=profile,
        source_statute="act/2025",
    )
    assert not applied.applied
    assert not any(
        f.kind == OCCUPANCY_TRANSITION_OBSERVED_FINDING_CODE
        for f in applied.observations
    )

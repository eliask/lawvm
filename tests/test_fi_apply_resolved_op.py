from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_resolved_op import (
    APPLY_RESOLVED_OP_AUDIT_KIND,
    FI_APPLY_RESOLVED_OP_RULE_ID,
    ApplyResolvedOpRequest,
    ApplyResolvedOpSinks,
    apply_resolved_op_with_audit,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.statute import ReplayState, StatuteContext
from lawvm.finland.target_kind import TargetKind
from lawvm.core.ir import IRNode


def _state() -> ReplayState:
    return ReplayState(IRNode(kind=IRNodeKind.BODY))


def _ctx(state: ReplayState) -> StatuteContext:
    return StatuteContext(
        id="100/2010",
        title="Synthetic",
        base_ir=state.ir,
        base_xml_bytes=b"<akn/>",
    )


def _rop(
    *,
    op_type: str = "REPLACE",
    muutos_ir: IRNode | None = None,
) -> ResolvedOp:
    op = AmendmentOp(
        op_id=f"{op_type.lower()}_1",
        op_type=cast(Any, op_type),
        target_kind=TargetKind.SECTION,
        target_section="1",
    )
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=muutos_ir,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )


def _request(state: ReplayState, rop: ResolvedOp) -> ApplyResolvedOpRequest:
    return ApplyResolvedOpRequest(
        state=state,
        ctx=_ctx(state),
        rop=rop,
        amendment_id="12/2015",
        replay_mode="official_consolidation",
    )


def test_apply_resolved_op_audit_records_no_apply_pass() -> None:
    state = _state()
    result = apply_resolved_op_with_audit(
        _request(state, _rop(muutos_ir=None)),
        ApplyResolvedOpSinks(),
    )

    assert result.state is state
    assert result.disposition == "NO_APPLY_PASS"
    assert result.audit.disposition == "NO_APPLY_PASS"
    assert result.audit.op_id == "replace_1"


def test_apply_resolved_op_audit_records_successful_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    new_ir = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1"),))

    def fake_apply_op(current_state: ReplayState, *_args: Any, **_kwargs: Any) -> ReplayState:
        return current_state.with_ir(new_ir)

    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)

    result = apply_resolved_op_with_audit(
        _request(state, _rop(muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"))),
        ApplyResolvedOpSinks(),
    )

    assert result.state.ir is new_ir
    assert result.disposition == "APPLIED"
    assert result.audit.disposition == "APPLIED"


def test_apply_resolved_op_audit_records_failed_apply_from_failed_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    failed_ops: list[Any] = []

    def fake_apply_op(current_state: ReplayState, *_args: Any, **kwargs: Any) -> ReplayState:
        kwargs["failed_ops_out"].append(object())
        return current_state

    monkeypatch.setattr("lawvm.finland.apply_resolved_op.apply_op", fake_apply_op)

    result = apply_resolved_op_with_audit(
        _request(state, _rop(muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"))),
        ApplyResolvedOpSinks(failed_ops_out=failed_ops),
    )

    assert failed_ops
    assert result.state is state
    assert result.disposition == "APPLY_FAILED"
    assert result.audit.disposition == "APPLY_FAILED"


def test_undeclared_touch_emits_blocking_finding_with_strict_profile_none() -> None:
    """Bug [6] bite-proof: an apply whose landed write touches a tree path its
    declared mutation events do NOT explain emits the blocking
    REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET finding EVEN WITH strict_profile=None.

    Before the fix, surfacing this finding was gated on a non-None strict profile,
    so a replay with strict_profile=None silently authorized a write that landed
    outside its declared target. After the fix the finding always fires, so the
    per-replay aggregate's no_boundary_violation conjunct trips for any caller.
    """
    from lawvm.finland.apply_replay_authorization import aggregate_replay_authority

    state = _state()
    # Landed write touches section 1, but NO mutation event is declared for it.
    new_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )

    def fake_apply_op(current_state: ReplayState, *_args: Any, **_kwargs: Any) -> ReplayState:
        return current_state.with_ir(new_ir)

    import lawvm.finland.apply_resolved_op as _mod

    findings: list[Any] = []
    mutation_events: list[Any] = []
    sinks = ApplyResolvedOpSinks(
        findings_out=findings,
        mutation_events_out=mutation_events,
    )
    # strict_profile is None — the previously-bypassed firewall arm.
    request = ApplyResolvedOpRequest(
        state=state,
        ctx=_ctx(state),
        rop=_rop(muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1")),
        amendment_id="12/2015",
        replay_mode="official_consolidation",
        strict_profile=None,
    )

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "apply_op", fake_apply_op)
        result = _mod.apply_resolved_op_with_audit(request, sinks)

    assert result.disposition == "APPLIED"
    boundary_findings = [
        f
        for f in findings
        if f.kind == "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET" and f.blocking
    ]
    assert boundary_findings, "undeclared touch must emit a blocking finding even with strict_profile=None"

    # The firewall now BITES: the per-replay aggregate un-authorizes the replay.
    authority = aggregate_replay_authority(write_receipts=[], findings=findings)
    assert authority.replay_authorized is False


def test_soft_failed_op_that_landed_a_mutation_unauthorizes_replay() -> None:
    """Bug [8] bite-proof: a soft-failed (APPLY_FAILED) op that still landed a tree
    mutation emits the blocking boundary finding, so the per-replay aggregate
    un-authorizes the replay (the aggregate never inspects disposition directly).
    """
    from lawvm.finland.apply_replay_authorization import aggregate_replay_authority

    state = _state()
    new_ir = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )

    def fake_apply_op(current_state: ReplayState, *_args: Any, **kwargs: Any) -> ReplayState:
        # Soft-fail: record a failed op AND land a tree mutation.
        kwargs["failed_ops_out"].append(object())
        return current_state.with_ir(new_ir)

    import lawvm.finland.apply_resolved_op as _mod

    findings: list[Any] = []
    failed_ops: list[Any] = []
    mutation_events: list[Any] = []
    sinks = ApplyResolvedOpSinks(
        findings_out=findings,
        failed_ops_out=failed_ops,
        mutation_events_out=mutation_events,
    )
    request = ApplyResolvedOpRequest(
        state=state,
        ctx=_ctx(state),
        rop=_rop(muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1")),
        amendment_id="12/2015",
        replay_mode="official_consolidation",
    )

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "apply_op", fake_apply_op)
        result = _mod.apply_resolved_op_with_audit(request, sinks)

    assert failed_ops
    assert result.disposition == "APPLY_FAILED"
    blocking = [
        f
        for f in findings
        if f.kind == "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET" and f.blocking
    ]
    assert blocking, "a soft-failed op that landed a mutation must emit a blocking finding"

    authority = aggregate_replay_authority(write_receipts=[], findings=findings)
    assert authority.replay_authorized is False


# ---------------------------------------------------------------------------
# Per-op apply-authority gates (audit lane L1: LS-01, LS-03, EV-05/FW-01, EV-04)
# ---------------------------------------------------------------------------


def _strict_profile() -> Any:
    from lawvm.core.compile_result import StrictProfile

    return StrictProfile(name="l1_per_op_gate_strict")


def _drive_apply(
    *,
    rop: ResolvedOp,
    new_ir: IRNode,
    strict: bool,
    state: ReplayState | None = None,
) -> list[Any]:
    """Drive the production apply_resolved_op_with_audit and return the findings.

    The production gate ``_enforce_per_op_apply_authority`` runs after the landed
    write; ``apply_op`` is stubbed only to land ``new_ir`` so the gate has a real
    before/after to reason over (the same pattern the existing boundary tests use).
    """
    import pytest as _pytest

    import lawvm.finland.apply_resolved_op as _mod

    state = state if state is not None else _state()

    def fake_apply_op(current_state: ReplayState, *_a: Any, **_k: Any) -> ReplayState:
        return current_state.with_ir(new_ir)

    findings: list[Any] = []
    sinks = ApplyResolvedOpSinks(
        findings_out=findings,
        mutation_events_out=[],
    )
    request = ApplyResolvedOpRequest(
        state=state,
        ctx=_ctx(state),
        rop=rop,
        amendment_id="12/2015",
        replay_mode="official_consolidation",
        strict_profile=_strict_profile() if strict else None,
    )
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "apply_op", fake_apply_op)
        _mod.apply_resolved_op_with_audit(request, sinks)
    return findings


def _ls01_rop_target_section_1() -> ResolvedOp:
    """REPLACE rop carrying a typed core LegalOperation targeting section 1."""
    from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
    from lawvm.core.semantic_types import StructuralAction

    lo = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="12/2015"),
    )
    op = AmendmentOp(
        op_id="replace_1",
        op_type=cast(Any, "REPLACE"),
        target_kind=TargetKind.SECTION,
        target_section="1",
        lo=lo,
    )
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )


def _two_section_state() -> tuple[ReplayState, IRNode, IRNode]:
    """A live two-section base (1, 2). Returns (state, sec1_node, sec2_node).

    Replay state is persistent: a real apply that touches one section shares the
    untouched section by identity, so the per-op diff lands on the precise path.
    The fixtures below preserve that by reusing the untouched node by identity.
    """
    sec1 = IRNode(kind=IRNodeKind.SECTION, label="1", text="old one")
    sec2 = IRNode(kind=IRNodeKind.SECTION, label="2", text="old two")
    body = IRNode(kind=IRNodeKind.BODY, children=(sec1, sec2))
    return ReplayState(ir=body), sec1, sec2


def test_ls01_sibling_path_edit_strict_rejects_loud() -> None:
    """LS-01: a write that lands a SIBLING path (section 2) while the op declares
    section 1 → strict emits the blocking per-op boundary violation."""
    state, sec1, _sec2 = _two_section_state()
    rop = _ls01_rop_target_section_1()
    # Op declares section 1, but the landed write changes section 2 (sibling),
    # sharing section 1 by identity (persistent apply).
    sibling_landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(sec1, IRNode(kind=IRNodeKind.SECTION, label="2", text="changed")),
    )
    findings = _drive_apply(rop=rop, new_ir=sibling_landed, strict=True, state=state)
    hits = [
        f
        for f in findings
        if f.kind == "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP" and f.blocking
    ]
    assert hits, "sibling-path edit must strict-reject per-op (LS-01)"
    assert hits[0].detail["out_of_boundary_paths"], "violation must carry the offending paths"
    assert "section:2" in hits[0].detail["out_of_boundary_paths"][0]


def test_ls01_sibling_path_edit_quirks_records_nonblocking() -> None:
    """LS-01: under quirks (permissive) the same escape is recorded, not blocked."""
    state, sec1, _sec2 = _two_section_state()
    rop = _ls01_rop_target_section_1()
    sibling_landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(sec1, IRNode(kind=IRNodeKind.SECTION, label="2", text="changed")),
    )
    findings = _drive_apply(rop=rop, new_ir=sibling_landed, strict=False, state=state)
    blocking = [f for f in findings if f.kind == "APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP"]
    accounting = [
        f
        for f in findings
        if f.kind == "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP" and not f.blocking
    ]
    assert not blocking, "quirks must not block per-op"
    assert accounting, "quirks must still record the boundary escape (accounting)"


def test_ls01_in_boundary_write_is_clean() -> None:
    """LS-01: a write landing exactly the declared target is within boundary (no finding)."""
    state, _sec1, sec2 = _two_section_state()
    rop = _ls01_rop_target_section_1()
    in_boundary = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="amended"), sec2),
    )
    findings = _drive_apply(rop=rop, new_ir=in_boundary, strict=True, state=state)
    assert not [
        f
        for f in findings
        if f.kind
        in ("APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP", "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP")
    ]


def test_ls03_invalid_occupancy_transition_strict_blocks() -> None:
    """LS-03: REPLACE on an ABSENT slot (no valid transition) strict-blocks."""
    rop = _rop(op_type="REPLACE", muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"))
    # prev_state is an empty BODY: section 1 is ABSENT, so REPLACE has no valid
    # occupancy transition. The landed write installs section 1.
    landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    findings = _drive_apply(rop=rop, new_ir=landed, strict=True)
    hits = [
        f for f in findings if f.kind == "APPLY.OCCUPANCY_TRANSITION_BLOCKED" and f.blocking
    ]
    assert hits, "invalid occupancy transition must strict-block (LS-03)"
    assert hits[0].detail["current_occupancy"] == "absent"
    assert hits[0].detail["action"] == "replace"


def test_ls03_invalid_occupancy_transition_quirks_does_not_block() -> None:
    """LS-03: the same invalid transition under quirks does not emit the strict block."""
    rop = _rop(op_type="REPLACE", muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"))
    landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    findings = _drive_apply(rop=rop, new_ir=landed, strict=False)
    assert not [f for f in findings if f.kind == "APPLY.OCCUPANCY_TRANSITION_BLOCKED"]


def test_ev05_op_without_authorization_rule_strict_blocks() -> None:
    """EV-05/FW-01: a state-mutating op with no resolvable authorization rule_id
    (empty op_id) strict-blocks with the authorization-required finding."""
    op = AmendmentOp(
        op_id="",  # no stable identity -> no execution-authorization rule resolves
        op_type=cast(Any, "REPLACE"),
        target_kind=TargetKind.SECTION,
        target_section="1",
    )
    rop = ResolvedOp.from_amendment_op(
        op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )
    # Land a write at the declared target so only the authorization gate is the
    # subject (occupancy: a write at section 1 from absent is the same lane, but
    # an absent REPLACE also trips occupancy; assert the authorization code is present).
    landed = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    findings = _drive_apply(rop=rop, new_ir=landed, strict=True)
    hits = [
        f
        for f in findings
        if f.kind == "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED" and f.blocking
    ]
    assert hits, "an op with no resolvable authorization rule must strict-block (EV-05)"
    assert hits[0].detail["required_proofs"]


def test_ev05_op_with_authorization_rule_is_clean() -> None:
    """EV-05: an op carrying a stable op_id resolves an authorization (no finding)."""
    rop = _ls01_rop_target_section_1()  # op_id="replace_1"
    in_boundary = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="x"),),
    )
    findings = _drive_apply(rop=rop, new_ir=in_boundary, strict=True)
    assert not [f for f in findings if f.kind == "EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED"]


def test_ev04_observation_cannot_enter_apply_authority_source_set() -> None:
    """EV-04: a blocking role=='observation' finding fed to the authority aggregate
    fails loud (observations explain authority, they do not become authority)."""
    from lawvm.core.phase_result import Finding
    from lawvm.finland.apply_replay_authorization import (
        ObservationPromotedToAuthorityError,
        _apply_authority_relevant_findings,
        aggregate_replay_authority,
    )

    # A normal (blocking violation) finding is authority-relevant.
    violation = Finding(
        kind="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        role="violation",
        stage="apply",
        blocking=True,
        detail={},
    )
    assert _apply_authority_relevant_findings([violation]) == (violation,)

    # A non-blocking observation is simply not authority-relevant (filtered out).
    observation = Finding(
        kind="APPLY.MUTATION_BOUNDARY_FINDING_AT_OP",
        role="observation",
        stage="apply",
        blocking=False,
        detail={},
    )
    assert _apply_authority_relevant_findings([observation]) == ()
    # The aggregate still authorizes when only a non-blocking observation is present.
    assert aggregate_replay_authority(
        write_receipts=[], findings=[observation]
    ).replay_authorized is True

    # A blocking observation (the EV-04 defect) is rejected loudly. Finding's own
    # validation forbids constructing one, so we build it via object.__new__ and
    # set fields directly to simulate the latent refactor hazard EV-04 guards
    # against (an observation that somehow became blocking and reached authority).
    promoted = object.__new__(Finding)
    object.__setattr__(promoted, "kind", "APPLY.MUTATION_BOUNDARY_FINDING_AT_OP")
    object.__setattr__(promoted, "role", "observation")
    object.__setattr__(promoted, "stage", "apply")
    object.__setattr__(promoted, "detail", {})
    object.__setattr__(promoted, "source_statute", "")
    object.__setattr__(promoted, "blocking", True)
    with pytest.raises(ObservationPromotedToAuthorityError):
        _apply_authority_relevant_findings([promoted])


def test_apply_resolved_op_audit_serializes_to_observation() -> None:
    state = _state()
    result = apply_resolved_op_with_audit(
        _request(state, _rop(muutos_ir=None)),
        ApplyResolvedOpSinks(),
    )

    observation = result.audit.to_observation()
    assert observation["kind"] == APPLY_RESOLVED_OP_AUDIT_KIND
    assert observation["source_statute"] == "12/2015"
    assert observation["detail"] == {
        "rule_id": FI_APPLY_RESOLVED_OP_RULE_ID,
        "source_effective": result.audit.source_effective,
        "source_expires": result.audit.source_expires,
        "op_id": "replace_1",
        "action_type": "REPLACE",
        "description": result.audit.description,
        "target_unit_kind": "section",
        "target_norm": "1",
        "target_chapter": "",
        "target_part": "",
        "target_paragraph": "",
        "target_item": "",
        "target_special": "",
        "disposition": "NO_APPLY_PASS",
    }

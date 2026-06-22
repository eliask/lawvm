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

"""WAIST #6 — canonical-operation (normalize / effect-lowering) StageResult.

Gates the canonical-op StageResult adapter + the single-channel decline
(ESCALATE-5D): the typed ``StageResult.residuals`` is the SOLE source of the
canonical-op decline that ``compile_amendment_ops`` surfaces (and that gates the
apply path via ``process_findings``).

Cases:
  (a) adapter value-identity + observation-finding passthrough on a clean
      (no-blocking) finding set;
  (b) ``coverage.is_partition()`` with the ESCALATE-3D denominator
      (``total = #emitted ops + #rejected candidate ops``);
  (c) FIRE-DRILL — a blocking canonical-op finding produces a blocking typed
      residual; the reconstructed decline rides that residual, and severing the
      residual makes the decline DISAPPEAR (RED if the decline survived without
      its residual — i.e. the residual were decorative / a parallel channel).
  (d) CALL-SITE GUARD — drives the PRODUCTION ``compile_amendment_ops`` end to
      end over a real corpus amendment and asserts its OWN return is routed
      through the typed residual: live residual surfaces the decline; severing
      the residual drops it. Reverting the call site to
      ``findings=tuple(all_findings)`` makes (d) go RED — the silent-revert
      ((c) alone cannot catch this because it tests the adapter in isolation).
"""

from __future__ import annotations

import dataclasses
from typing import cast

import pytest
from lxml import etree

import lawvm.finland.compile_amendment as compile_amendment
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.stage_result import StageResult
from lawvm.finland.compile_amendment import (
    _CanonicalOpResidualCarrier,
    build_canonical_op_stage,
    compile_amendment_ops,
    reconstruct_findings_from_canonical_op_stage,
)
from lawvm.finland.corpus import get_corpus_store
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest
from lawvm.finland.source_model import AmendmentSourceModel


def _resolved(n: int) -> list[ResolvedOp]:
    """``n`` opaque resolved-op stand-ins.

    The canonical-op stage uses only ``len(resolved)`` (the emitted-op count) and
    re-carries the list verbatim as ``StageResult.value``; the element identity is
    irrelevant to the account, so cheap stand-ins keep the test deterministic.
    """
    return cast("list[ResolvedOp]", [object() for _ in range(n)])


def _observation(kind: str = "ELAB.SOURCE_PATHOLOGY") -> Finding:
    return Finding(
        kind=kind,
        role="observation",
        stage="frontend_compile",
        detail={"message": "informational"},
        source_statute="1/2024",
        blocking=False,
    )


def _blocking_rejection(target: str = "5") -> Finding:
    # A registered blocking obligation — the canonical strict-rejection code.
    return Finding(
        kind="ELAB.STRICT_REJECTED_OPERATION",
        role="obligation",
        stage="_elaborate_group",
        detail={
            "message": "operation rejected by strict profile before apply",
            "target_section": target,
        },
        source_statute="1/2024",
        blocking=True,
    )


# ---------------------------------------------------------------------------
# (a) value-identity + observation passthrough on a clean finding set
# ---------------------------------------------------------------------------


def test_canonical_op_stage_value_identity_clean() -> None:
    resolved = _resolved(3)
    findings = [_observation(), _observation("ELAB.SPARSE_SLOT_BINDING")]

    stage, carriers = build_canonical_op_stage(resolved, findings)

    # value is the emitted ops, unchanged.
    assert stage.value == resolved
    # no blocking finding → no residual, clean coverage, neutral authority.
    assert carriers == ()
    assert stage.residuals == ()
    assert stage.has_blocking_residual is False
    assert stage.coverage.is_clean
    assert stage.authority.is_neutral
    assert stage.evidence.is_empty
    # observation findings pass through onto StageResult.findings (typed).
    assert stage.findings == tuple(findings)

    # reconstruction returns the observation findings verbatim (0-delta shape).
    returned = reconstruct_findings_from_canonical_op_stage(findings, stage, carriers)
    assert returned == tuple(findings)


# ---------------------------------------------------------------------------
# (b) coverage is a partition with the ESCALATE-3D denominator
# ---------------------------------------------------------------------------


def test_canonical_op_stage_coverage_partition_3d_denominator() -> None:
    resolved = _resolved(2)  # 2 emitted ops
    findings = [
        _observation(),
        _blocking_rejection("5"),
        _blocking_rejection("7"),  # 2 rejected candidate ops
    ]

    stage, carriers = build_canonical_op_stage(resolved, findings)

    # ESCALATE-3D: total = #emitted ops + #rejected candidate ops.
    assert stage.coverage.unit == "candidate_ops"
    assert stage.coverage.total == 4
    assert stage.coverage.owned == 2
    assert stage.coverage.violation == 2
    assert stage.coverage.is_partition()
    # rejected lane is signal-bearing → not clean.
    assert stage.coverage.is_clean is False
    # one typed blocking residual per rejected candidate op.
    assert len(stage.residuals) == 2
    assert all(r.kind == "unowned_violation" and r.blocking for r in stage.residuals)
    assert stage.has_blocking_residual is True


# ---------------------------------------------------------------------------
# (c) FIRE-DRILL — the decline rides the typed residual as the SINGLE channel
# ---------------------------------------------------------------------------


def test_fire_drill_decline_rides_typed_residual_single_channel() -> None:
    resolved = _resolved(1)
    blocking = _blocking_rejection("5")
    findings = [_observation(), blocking]

    stage, carriers = build_canonical_op_stage(resolved, findings)

    # The blocking finding became a typed blocking residual carrying that exact
    # Finding (the single source of the decline).
    assert len(carriers) == 1
    assert carriers[0].finding is blocking
    assert carriers[0].residual.blocking is True

    # With the residual LIVE, the reconstructed decline contains the blocking
    # finding (the apply-decline channel fires).
    returned_live = reconstruct_findings_from_canonical_op_stage(
        findings, stage, carriers
    )
    decline_live = [f for f in returned_live if f.blocking]
    assert decline_live == [blocking], "the live residual must yield the decline"

    # FIRE-DRILL: sever the typed residual (test double = StageResult with
    # residuals stripped) while keeping the OLD finding list intact. If the
    # decline still appeared, the residual would be decorative and a parallel
    # channel would be doing the real blocking (the #3 built-then-severed
    # failure). The single-channel design forbids that.
    severed_stage = dataclasses.replace(stage, residuals=())
    returned_severed = reconstruct_findings_from_canonical_op_stage(
        findings, severed_stage, carriers
    )
    decline_severed = [f for f in returned_severed if f.blocking]
    assert decline_severed == [], (
        "severing the typed residual MUST remove the canonical-op decline — "
        "the residual is the sole load-bearing blocking channel"
    )
    # observation findings are NOT part of the decline and survive the severance.
    assert any(f.kind == "ELAB.SOURCE_PATHOLOGY" for f in returned_severed)


# ---------------------------------------------------------------------------
# (d) CALL-SITE GUARD — the production ``compile_amendment_ops`` return MUST be
#     routed through the typed residual, not ``tuple(all_findings)``.
#
# (a)-(c) gate ``build_canonical_op_stage`` + the reconstruction IN ISOLATION.
# They prove the reconstruction reads residuals — but NOT that
# ``compile_amendment_ops`` actually routes its OWN return through that
# reconstruction. A revert of the call site to ``findings=tuple(all_findings)``
# (the #3 built-then-severed failure: interposition built, then severed) is
# SILENT to (a)-(c). These two cases drive the production function end-to-end
# over a real corpus amendment and bite that exact revert.
# ---------------------------------------------------------------------------


def _compile_amendment_ops_over_real_amendment() -> PhaseResult[list[ResolvedOp]]:
    """Drive the production ``compile_amendment_ops`` over a real corpus op set.

    Models the 1995/1084 -> 1985/336 amendment driver already used in
    ``tests/test_fi_guard_liveness.py``: replay the parent up to the amendment,
    normalize/compile the source ops, then hand the resolved-op group to
    ``compile_amendment_ops`` exactly as the replay pipeline does. The returned
    ``PhaseResult`` is the production return whose findings channel is under
    test.
    """
    before = replay_xml(
        request=ReplayXmlRequest(
            parent_id="1985/336",
            mode="official_consolidation",
            stop_before="1995/1084",
            quiet=True,
            build_full_products=False,
        )
    )
    xml = get_corpus_store().read_source("1995/1084")
    assert xml is not None
    tree = etree.fromstring(xml)
    johto = get_johtolause(xml)
    source_model = AmendmentSourceModel.from_tree(tree, source_ref="1995/1084")
    phase = normalize_and_compile_ops(
        johto,
        tree,
        before.state,
        "1995/1084",
        "Asetus harjoittelukouluasetuksen muuttamisesta",
        False,
        parent_id="1985/336",
        source_model=source_model,
    )
    ops = [op for op in phase.output if str(op.target_cols.target_section) in {"29", "31"}]
    return compile_amendment_ops(
        before.state,
        ops,
        source_model,
        johto,
        "official_consolidation",
        source_ref="1995/1084",
        target_statute="1985/336",
    )


# The synthetic blocking decline injected into the production accumulator. It is
# a registered blocking canonical-op code so it is treated as a real decline by
# both the typed-residual path AND a (reverted) ``tuple(all_findings)`` path.
_CALL_SITE_DECLINE_KIND = "ELAB.STRICT_REJECTED_OPERATION"


def _install_blocking_decline_injector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sever_residual: bool,
) -> Finding:
    """Wrap the PRODUCTION ``build_canonical_op_stage`` at its call-site module.

    The wrapper:

    1. appends a blocking decline Finding to the live ``all_findings`` list that
       ``compile_amendment_ops`` passes in — so the decline is present in the
       accumulator a reverted ``tuple(all_findings)`` would return verbatim; and
    2. calls the real builder (producing a genuine carrier + typed residual for
       the injected decline), then optionally STRIPS ``residuals`` from the
       returned stage while KEEPING the carriers.

    With ``sever_residual=False`` the typed residual is live: the single-channel
    return must surface the decline. With ``sever_residual=True`` the residual is
    gone but the carrier (and the accumulator entry) remain: the single-channel
    return must DROP the decline. A call site that bypasses the reconstruction
    and returns ``tuple(all_findings)`` keeps the decline in BOTH cases — so the
    severed case goes RED against that revert.
    """
    real_build = compile_amendment.build_canonical_op_stage
    injected = Finding(
        kind=_CALL_SITE_DECLINE_KIND,
        role="obligation",
        stage="_elaborate_group",
        detail={
            "message": "operation rejected by strict profile before apply",
            "target_section": "29",
        },
        source_statute="1995/1084",
        blocking=True,
    )

    def wrapper(
        resolved: list[ResolvedOp],
        findings: list[Finding],
    ) -> tuple[
        StageResult[list[ResolvedOp]], tuple[_CanonicalOpResidualCarrier, ...]
    ]:
        # Place the decline into the production accumulator itself.
        findings.append(injected)
        stage, carriers = real_build(resolved, findings)
        if sever_residual:
            stage = dataclasses.replace(stage, residuals=())
        return stage, carriers

    monkeypatch.setattr(compile_amendment, "build_canonical_op_stage", wrapper)
    return injected


def test_compile_amendment_ops_surfaces_decline_through_live_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE: with the typed residual present, the decline reaches the return."""
    injected = _install_blocking_decline_injector(monkeypatch, sever_residual=False)

    result = _compile_amendment_ops_over_real_amendment()

    decline = [
        f
        for f in result.findings()
        if f.kind == _CALL_SITE_DECLINE_KIND and f.blocking
    ]
    assert decline == [injected], (
        "compile_amendment_ops must surface the blocking canonical-op decline "
        "when its typed residual is live"
    )


def test_compile_amendment_ops_decline_dies_when_residual_severed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE BITE: sever the typed residual at the production call site.

    The injected decline is still in ``all_findings`` and still has a carrier —
    only its typed residual is gone. Because ``compile_amendment_ops`` routes its
    return through ``reconstruct_findings_from_canonical_op_stage`` (the typed
    residual is the SOLE channel), the decline MUST disappear from the production
    return. If the call site is reverted to ``findings=tuple(all_findings)`` the
    injected decline survives in the return and this assertion goes RED — which
    is exactly the silent-revert this test exists to catch.
    """
    _install_blocking_decline_injector(monkeypatch, sever_residual=True)

    result = _compile_amendment_ops_over_real_amendment()

    decline = [
        f
        for f in result.findings()
        if f.kind == _CALL_SITE_DECLINE_KIND
    ]
    assert decline == [], (
        "severing the typed residual MUST remove the canonical-op decline from "
        "compile_amendment_ops's OWN return — the return is routed through the "
        "typed residual, not tuple(all_findings)"
    )

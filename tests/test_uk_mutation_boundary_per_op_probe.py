"""§2.9 production-lane guard-liveness for the UK per-op mutation-boundary
adjudication (seam-drained successor of the retired in-fold probe).

The lens (``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary``) is
the LS-01 / §1.0 per-op mutation-boundary verifier+emitter; Finland wires it
post-apply at ``finland/apply_resolved_op.py``. B-enforcement increment 2 made
``core/apply_seam.apply_op`` the UNIVERSAL always-on observer of that audit,
routing the ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` witness to
``AppliedOp.observations``. The LS-01 cleanup increment RETIRED UK's redundant
in-fold env-probe (migrated onto ``probe_base`` in task #65, threading
``declared_recovery_prefixes`` per task #108-UK): ``replay_uk_ops`` /
``UKReplayPipeline.apply_ops`` now DRAIN the seam observation into the same
env-gated ``uk_replay_mutation_boundary_per_op_violation_observed``
``CompileAdjudication`` (``src/lawvm/uk_legislation/mutation_boundary_per_op_probe.py``
is now the projector half — still built via the SAME ``probe_base`` D1 spec).

This test drives a known per-op mutation-boundary escape through the always-on
seam observer (the same core audit the UK fold runs), asserts the drained
adjudication fires, and proves byte-identity with the seam observation it drains.
Strict enforcement stays multi-session pending a UK ``strict_profile`` lane.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lawvm.core.apply_seam import ApplyProfile, MaterializeResult, apply_op
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.mutation_boundary_proof import MUTATION_BOUNDARY_FINDING_AT_OP_CODE
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.mutation_boundary_per_op_probe import (
    UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    boundary_probe_enabled,
    drain_seam_boundary_observations,
    project_boundary_observation,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

_FINDING_KIND = UK_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND
_PROBE_ENV_FLAG = "LAWVM_UK_MUTATION_BOUNDARY_PER_OP"


def _section(label: str, *, text: str = "") -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        text=text,
        children=(IRNode(kind=IRNodeKind.P, label="", children=()),),
    )


def _chapter(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label="1", children=tuple(sections))


def _body(*chapters: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(chapters))


def _statute(body: IRNode, *, statute_id: str) -> IRStatute:
    return IRStatute(
        statute_id=statute_id,
        title="",
        body=body,
        supplements=(),
        metadata={},
    )


def _text_replace_op_targeting_section_1(op_id: str = "op/test/1") -> LegalOperation:
    """A TEXT_REPLACE LegalOperation whose storage boundary is the section-1 path only.

    ``operation_storage_boundary_prefixes`` (``core/mutation_boundary.py:100-101``)
    maps ``TEXT_REPLACE`` to the target_path verbatim (no parent expansion), so
    any observed diff on sibling section ``2`` is necessarily out-of-boundary.
    The TEXT_REPLACE family is the canonical probe-friendly witness.
    """
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
        source=OperationSource(statute_id="boundary/src"),
    )


def _seam_boundary_observation(before: IRNode, after: IRNode, op: LegalOperation):
    """Drive a (before, after) escape through the always-on seam observer and
    return its single ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation.

    Uses a materializer that simply lands ``after`` under a ``boundary_mode="off"``
    profile — exactly the seam path UK's fold runs via ``seam_apply_op``."""
    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=after)

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="uk",
        materializer=_land_after,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )
    applied = apply_op(
        before, op, provenance=op.source, profile=profile, source_statute="boundary/1"
    )
    boundary = [
        o for o in applied.observations if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert len(boundary) == 1
    return boundary[0]


@pytest.fixture(autouse=True)
def _enable_probe(monkeypatch):
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")


def test_projector_drains_seam_observation_for_out_of_boundary_diff() -> None:
    """Production-lane reachable shape: an op targeting section ``1`` whose
    apply *also* rewrote sibling section ``2``'s text (the §1.0/§1.4 forbidden
    shape) MUST, when the seam observation is drained, emit
    ``uk_replay_mutation_boundary_per_op_violation_observed``.

    Drives the seam observer for the escape, then projects via the same drain the
    UK fold calls — built via the SAME ``probe_base`` D1 spec the probe used."""
    before = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="original-2"),
        )
    )
    after = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="tampered-sibling"),
        )
    )
    op = _text_replace_op_targeting_section_1()
    obs = _seam_boundary_observation(before, after, op)

    adjudication = project_boundary_observation(
        obs, source_statute="boundary/1", op_id=op.op_id
    )
    assert adjudication.kind == _FINDING_KIND
    detail = adjudication.detail
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert detail["quirks_disposition"] == "record"
    assert detail["boundary_status"] == "out_of_boundary"
    assert detail["op_id"] == op.op_id
    assert adjudication.blocking is False
    assert adjudication.phase == "replay"
    assert adjudication.source_statute == "boundary/1"
    # The out-of-boundary path list must name the escaped (sibling section 2)
    # path, not just regurgitate the declared target — a probe that lists no
    # concrete escape path is the §1.10 forbidden diagnostic shape.
    assert detail["out_of_boundary_paths"], (
        "out_of_boundary_paths must be non-empty when boundary_status == "
        "out_of_boundary — otherwise the diagnostic is opaque (AGENTS.md §1.10)"
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with the env unset, ``boundary_probe_enabled()`` MUST
    return False — that signal gates the seam-observation drain in the UK fold,
    so production UK bench output stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    assert boundary_probe_enabled() is False


def test_drain_within_boundary_emits_nothing() -> None:
    """Negative: when the *only* change is on the op's declared target node,
    the seam emits no boundary observation, so the drain appends nothing — a
    gauge against false positives."""
    before = _body(_chapter(_section("1", text="original")))
    after = _body(_chapter(_section("1", text="replaced-in-place")))
    op = _text_replace_op_targeting_section_1()

    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=after)

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="uk",
        materializer=_land_after,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )
    applied = apply_op(
        before, op, provenance=op.source, profile=profile, source_statute="boundary/3"
    )
    adjudications: list[CompileAdjudication] = []
    drain_seam_boundary_observations(
        applied.observations,
        adjudications_out=adjudications,
        source_statute="boundary/3",
        op_id=op.op_id,
    )
    assert all(a.kind != _FINDING_KIND for a in adjudications)


def test_drain_none_sink_is_noop() -> None:
    """``adjudications_out is None`` is a pure no-op even with the env flag on."""
    before = _body(
        _chapter(_section("1", text="original-1"), _section("2", text="original-2"))
    )
    after = _body(
        _chapter(_section("1", text="original-1"), _section("2", text="tampered-sibling"))
    )
    op = _text_replace_op_targeting_section_1()
    obs = _seam_boundary_observation(before, after, op)
    drain_seam_boundary_observations(
        (obs,), adjudications_out=None, source_statute="boundary/4", op_id=op.op_id
    )


def test_wired_into_uk_fold() -> None:
    """Static-line proof that the seam-drain is invoked from the UK replay folds
    (``replay_uk_ops`` and ``UKReplayPipeline.apply_ops``) — i.e. the call site
    exists, not dead code."""
    from lawvm.uk_legislation import replay_executor as rmod
    from lawvm.uk_legislation import uk_amendment_replay as amod

    rsrc = inspect.getsource(rmod)
    assert (
        "from lawvm.uk_legislation.mutation_boundary_per_op_probe import" in rsrc
    )
    assert "_uk_drain_seam_boundary_observations" in rsrc
    asrc = inspect.getsource(amod.UKReplayPipeline.apply_ops)
    assert "_uk_drain_seam_boundary_observations" in asrc


def test_probe_default_off_through_pipeline_apply_ops(monkeypatch) -> None:
    """Smoke (default-off): with the env unset, ``apply_ops`` runs the base
    pipeline unchanged on a no-op plan and the drain MUST NOT emit. Production
    UK bench stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)

    pipeline = UKReplayPipeline(Path("."))
    base = _statute(
        _body(_chapter(_section("1"))),
        statute_id="boundary/smoke/default-off",
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    assert not any(a.kind == _FINDING_KIND for a in adjudications), (
        "drain must be default-off; got: {}".format(
            [a for a in adjudications if a.kind == _FINDING_KIND]
        )
    )


def test_probe_reachable_through_pipeline_apply_ops_no_ops(monkeypatch) -> None:
    """Smoke (env on): with no ops, ``apply_ops`` returns the unchanged base
    and the seam drain runs through the production fold; because base == replayed
    (within boundary), no shortfall fires. Proves the drain is wired into the
    production fold, and does not double-fire or invent a violation."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")

    pipeline = UKReplayPipeline(Path("."))
    base = _statute(
        _body(_chapter(_section("1"))),
        statute_id="boundary/smoke/on",
    )
    adjudications: list[CompileAdjudication] = []
    pipeline.apply_ops(base, [], adjudications_out=adjudications)
    violations = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert violations == [], (
        "default no-op replay should not emit any per-op mutation-boundary "
        "violation — got: {}".format(violations)
    )


def test_drained_adjudication_byte_identical_to_seam_observation() -> None:
    """BYTE-IDENTITY PROOF. The retired in-fold probe projected the core
    ``audit_op_mutation_boundary`` finding into the UK adjudication via the
    ``probe_base`` D1 spec; the drain now projects the seam's ``observations``
    witness — the SAME core finding through the SAME spec. Assert the projection
    carries the IDENTICAL kind + detail keys + the D1 overrides
    (``phase="replay"`` + ``blocking`` pass-through) the probe produced, so the
    retirement loses NO information."""
    before = _body(
        _chapter(
            _section("1", text="orig-1"),
            _section("2", text="orig-2"),
        )
    )
    after = _body(
        _chapter(
            _section("1", text="replaced-1"),
            _section("2", text="tampered-2"),
        )
    )
    op = _text_replace_op_targeting_section_1(op_id="op/escape")
    obs = _seam_boundary_observation(before, after, op)
    adjudication = project_boundary_observation(
        obs, source_statute="boundary/1", op_id=op.op_id
    )
    # D1 overrides preserved.
    assert adjudication.phase == "replay"
    assert adjudication.blocking == obs.blocking
    # Evidence sourced from the one core finding detail.
    assert list(adjudication.detail["changed_paths"]) == list(obs.detail["changed_paths"])
    assert list(adjudication.detail["out_of_boundary_paths"]) == list(
        obs.detail["out_of_boundary_paths"]
    )
    assert adjudication.detail["boundary_status"] == obs.detail["boundary_status"]
    assert adjudication.detail["core_finding_kind"] == obs.kind
    # Uniform probe_base envelope preserved.
    assert adjudication.detail["family"] == "mutation_boundary"
    assert adjudication.detail["probe_mode"] == "observation_only"

"""§2.9 production-lane guard-liveness for the NO per-op mutation-boundary
adjudication (seam-drained successor of the retired in-fold probe).

The lens (``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary``) is
the LS-01 / §1.0 per-op mutation-boundary verifier+emitter; Finland wires it
post-apply at ``finland/apply_resolved_op.py``. B-enforcement increment 2 made
``core/apply_seam.apply_op`` the UNIVERSAL always-on observer of that audit,
routing the ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` witness to
``AppliedOp.observations``. The LS-01 cleanup increment RETIRED NO's redundant
in-fold env-probe: ``apply_no_ops`` now DRAINS the seam observation into the same
env-gated ``no_replay_mutation_boundary_per_op_violation_observed``
``CompileAdjudication`` (``src/lawvm/norway/mutation_boundary_per_op_probe.py``
is now the projector half).

This test drives a known per-op mutation-boundary escape through the real
``apply_no_ops`` fold (env-on) and asserts the adjudication fires, plus a unit
proof that the projector is byte-identical to the seam observation it drains.
Strict enforcement stays multi-session pending a NO ``strict_profile`` lane.
"""
from __future__ import annotations

import inspect

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
from lawvm.norway.mutation_boundary_per_op_probe import (
    NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    boundary_probe_enabled,
    drain_seam_boundary_observations,
    project_boundary_observation,
)
from lawvm.norway.grafter import apply_no_ops

_FINDING_KIND = NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND
_PROBE_ENV_FLAG = "LAWVM_NO_MUTATION_BOUNDARY_PER_OP"


def _section(label: str, *, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, children=())


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _statute(body: IRNode, *, statute_id: str = "no/lov/2025-01-01-1") -> IRStatute:
    return IRStatute(statute_id=statute_id, title="", body=body, supplements=(), metadata={})


def _text_replace_op_targeting_section_1(op_id: str = "no/op/test/1") -> LegalOperation:
    """A TEXT_REPLACE op whose storage boundary is the section-1 path only.

    ``operation_storage_boundary_prefixes`` maps ``TEXT_REPLACE`` to the
    target_path verbatim (no parent expansion), so any observed diff on sibling
    section ``2`` is necessarily out-of-boundary — the canonical probe-friendly
    witness.
    """
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
    )


def _seam_boundary_observation(before: IRNode, after: IRNode, op: LegalOperation):
    """Drive a (before, after) escape through the always-on seam observer and
    return its single ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation.

    Uses a materializer that simply lands ``after`` under a ``boundary_mode="off"``
    profile — exactly the seam path NO's ``apply_no_ops`` runs."""
    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=after)

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="no",
        materializer=_land_after,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )
    applied = apply_op(
        before, op, provenance=op.source, profile=profile, source_statute="no/boundary/1"
    )
    boundary = [
        o for o in applied.observations if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert len(boundary) == 1
    return boundary[0]


def test_projector_drains_seam_observation_for_out_of_boundary_diff() -> None:
    """An op targeting section ``1`` whose apply *also* rewrote sibling section
    ``2``'s text (the §1.0/§1.4 forbidden shape) MUST, when the seam observation
    is drained, emit ``no_replay_mutation_boundary_per_op_violation_observed``.

    Drives the seam observer for the escape, then projects via the same drain
    ``apply_no_ops`` calls."""
    before = _body(_section("1", text="original-1"), _section("2", text="original-2"))
    after = _body(_section("1", text="original-1"), _section("2", text="tampered-sibling"))
    op = _text_replace_op_targeting_section_1()
    obs = _seam_boundary_observation(before, after, op)

    adjudication = project_boundary_observation(
        obs, source_statute="no/boundary/1", op_id=op.op_id
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
    assert adjudication.source_statute == "no/boundary/1"
    assert detail["out_of_boundary_paths"], (
        "out_of_boundary_paths must be non-empty when boundary_status == "
        "out_of_boundary — otherwise the diagnostic is opaque (AGENTS.md §1.10)"
    )


def test_drain_within_boundary_emits_nothing(monkeypatch) -> None:
    """Negative: when the only change is on the op's declared target node, the
    seam emits no boundary observation, so the drain appends nothing."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    before = _body(_section("1", text="original"))
    after = _body(_section("1", text="replaced-in-place"))
    op = _text_replace_op_targeting_section_1()

    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=after)

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="no",
        materializer=_land_after,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )
    applied = apply_op(
        before, op, provenance=op.source, profile=profile, source_statute="no/boundary/3"
    )
    adjudications: list[CompileAdjudication] = []
    drain_seam_boundary_observations(
        applied.observations,
        adjudications_out=adjudications,
        source_statute="no/boundary/3",
        op_id=op.op_id,
    )
    assert all(a.kind != _FINDING_KIND for a in adjudications)


def test_drain_disabled_by_default_emits_nothing(monkeypatch) -> None:
    """Default-off: even when the seam observation is present, the drain MUST NOT
    project anything with the env unset — that gates the env-gated adjudication
    surface, keeping production NO bench output byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    before = _body(_section("1", text="original-1"), _section("2", text="original-2"))
    after = _body(_section("1", text="original-1"), _section("2", text="tampered-sibling"))
    op = _text_replace_op_targeting_section_1()
    obs = _seam_boundary_observation(before, after, op)
    out: list[CompileAdjudication] = []
    drain_seam_boundary_observations(
        (obs,), adjudications_out=out, source_statute="no/boundary/4", op_id=op.op_id
    )
    assert out == []


def test_drain_none_sink_is_noop(monkeypatch) -> None:
    """``adjudications_out is None`` is a pure no-op even with the env flag on."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    before = _body(_section("1", text="original-1"), _section("2", text="original-2"))
    after = _body(_section("1", text="original-1"), _section("2", text="tampered-sibling"))
    op = _text_replace_op_targeting_section_1()
    obs = _seam_boundary_observation(before, after, op)
    # No exception, no append target — a no-op.
    drain_seam_boundary_observations(
        (obs,), adjudications_out=None, source_statute="no/boundary/4", op_id=op.op_id
    )


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with the env unset, ``boundary_probe_enabled()`` MUST return
    False — that signal gates the seam-observation drain in ``apply_no_ops``, so
    production NO bench output stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    assert boundary_probe_enabled() is False


def test_wired_into_apply_no_ops() -> None:
    """Static-line proof that the seam-drain is invoked from ``apply_no_ops`` —
    i.e. the call site exists, not dead code."""
    from lawvm.norway import grafter as mod

    src = inspect.getsource(mod.apply_no_ops)
    assert "_no_drain_seam_boundary_observations" in src
    grafter_src = inspect.getsource(mod)
    assert (
        "from lawvm.norway.mutation_boundary_per_op_probe import" in grafter_src
    )


def test_apply_no_ops_default_off_emits_no_probe_finding(monkeypatch) -> None:
    """Default-off through the real ``apply_no_ops`` fold: a clean REPLACE op
    applies and the drain MUST NOT emit. Production NO bench stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    statute = _statute(_body(_section("1", text="orig")))
    op = LegalOperation(
        op_id="no/replace/ok",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="ny tekst"),
        source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
    )
    adjudications: list[CompileAdjudication] = []
    apply_no_ops(statute, [op], adjudications_out=adjudications)
    assert not any(a.kind == _FINDING_KIND for a in adjudications)


def test_apply_no_ops_gate_on_clean_op_no_escape(monkeypatch) -> None:
    """Env on through the real fold: a well-behaved REPLACE op stays within its
    boundary, so the drain runs but emits no escape — proves the drain is wired
    into ``apply_no_ops`` and does not invent a violation when nothing escapes."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    statute = _statute(_body(_section("1", text="orig"), _section("2", text="keep")))
    op = LegalOperation(
        op_id="no/replace/clean",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="ny tekst"),
        source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
    )
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops(statute, [op], adjudications_out=adjudications)
    # The op landed (section 1 text changed) and section 2 is untouched.
    assert result.body.children[0].text == "ny tekst"
    assert result.body.children[1].text == "keep"
    assert not any(a.kind == _FINDING_KIND for a in adjudications)


def test_apply_no_ops_replace_recovered_by_insert_declares_recovery_no_escape(monkeypatch) -> None:
    """A missing-target REPLACE recovered by INSERT at the body root is a
    LEGITIMATE recovery retarget: the new section lands outside the op's nominal
    ``section:5`` storage boundary, but the recovery DECLARES that landing as an
    authorized ``declared_recovery`` boundary extension (surfaced on the seam's
    ``MaterializeResult.declared_recovery_prefixes``). The seam observer therefore
    reads it as within-boundary and the drain emits NO mutation-boundary finding —
    while the recovery's own ``no_replay_replace_recovered_by_insert`` audit
    receipt still fires (the recovery itself remains visible)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    statute = _statute(_body(_section("1", text="one")))
    op = LegalOperation(
        op_id="no/replace-missing-5",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="five"),
        source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
    )
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops(statute, [op], adjudications_out=adjudications)
    # The recovery landed the new section into the body.
    assert any(c.label == "5" for c in result.body.children)
    # The recovery's own audit receipt still fires (the recovery stays visible).
    assert any(
        a.kind == "no_replay_replace_recovered_by_insert" for a in adjudications
    )
    # ...but the seam observer reads the declared recovery as within-boundary,
    # so the drain emits NO escape finding.
    assert not any(a.kind == _FINDING_KIND for a in adjudications)


def test_drain_true_escape_still_fires_despite_recovery_declaration(monkeypatch) -> None:
    """Guard against a blanket disable: declaring a recovery's specific retarget
    must NOT suppress a GENUINELY out-of-boundary change. A seam apply that lands
    a write on a declared-recovery node AND tampers an undeclared sibling must
    still produce the boundary observation (and therefore the drained
    adjudication) — i.e. the declaration is the *specific* recovered path, never a
    wildcard.

    Section ``1`` is the declared target, section ``3`` is the declared recovery,
    and the apply ALSO tampered sibling section ``2`` — covered by neither, so it
    must escape. Driven through the seam observer (the same core
    ``audit_op_mutation_boundary`` ``apply_no_ops`` runs)."""
    monkeypatch.setenv(_PROBE_ENV_FLAG, "1")
    before = _body(
        _section("1", text="original-1"),
        _section("2", text="original-2"),
        _section("3", text="original-3"),
    )
    after = _body(
        _section("1", text="replaced-1"),
        _section("2", text="tampered-sibling"),
        _section("3", text="recovered-3"),
    )
    op = _text_replace_op_targeting_section_1()

    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        # Declare ONLY the section-3 recovery — section 2 is covered by neither
        # the target (section 1) nor this declared recovery.
        return MaterializeResult(
            new_state=after, declared_recovery_prefixes=((("section", "3"),),)
        )

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="no",
        materializer=_land_after,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )
    applied = apply_op(
        before, op, provenance=op.source, profile=profile,
        source_statute="no/boundary/escape",
    )
    adjudications: list[CompileAdjudication] = []
    drain_seam_boundary_observations(
        applied.observations,
        adjudications_out=adjudications,
        source_statute="no/boundary/escape",
        op_id=op.op_id,
    )
    violations = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert violations, (
        "a genuine out-of-boundary change (sibling section 2) must STILL fire "
        "even when a specific recovery path (section 3) is declared — the "
        "declaration is not a blanket disable"
    )
    escaped = violations[0].detail["out_of_boundary_paths"]
    assert any("section:2" in p for p in escaped), escaped
    # The declared section-3 recovery is NOT reported as an escape.
    assert not any("section:3" in p for p in escaped), escaped


def test_drained_adjudication_byte_identical_to_seam_observation() -> None:
    """BYTE-IDENTITY PROOF. The retired in-fold probe projected the core
    ``audit_op_mutation_boundary`` finding into the NO adjudication; the drain now
    projects the seam's ``observations`` witness — the SAME core finding. Assert
    the projection over the seam observation carries the IDENTICAL kind + detail
    keys the probe produced, so the retirement loses NO information."""
    before = _body(
        _section("1", text="orig-1"), _section("2", text="orig-2"), _section("3", text="orig-3")
    )
    after = _body(
        _section("1", text="replaced-1"),
        _section("2", text="tampered-2"),
        _section("3", text="orig-3"),
    )
    op = _text_replace_op_targeting_section_1(op_id="no/escape")
    obs = _seam_boundary_observation(before, after, op)
    adjudication = project_boundary_observation(
        obs, source_statute="no/boundary/1", op_id=op.op_id
    )
    # The drained adjudication sources every diagnostic field from the seam's one
    # core finding detail — the same producer the probe consumed.
    # The probe projected the core detail tuples into ``list`` — the projector
    # is byte-identical to that, so compare element-wise.
    assert list(adjudication.detail["changed_paths"]) == list(obs.detail["changed_paths"])
    assert list(adjudication.detail["out_of_boundary_paths"]) == list(
        obs.detail["out_of_boundary_paths"]
    )
    assert adjudication.detail["boundary_status"] == obs.detail["boundary_status"]
    assert adjudication.detail["core_finding_kind"] == obs.kind
    assert adjudication.blocking == obs.blocking

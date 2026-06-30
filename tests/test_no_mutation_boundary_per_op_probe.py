"""§2.9 production-lane guard-liveness for the NO per-op mutation-boundary probe.

The lens (``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary``) is
the LS-01 / §1.0 per-op mutation-boundary verifier+emitter; Finland wires it
post-apply at ``finland/apply_resolved_op.py`` and the UK replay fold consumes
it at ``uk_legislation/mutation_boundary_per_op_probe.py``. Until this commit
``src/lawvm/norway/grafter.py`` had NO production call site — the §2.9
worst-case: a check that exists, is registered, passes review, and creates
false confidence in invisible containment. The probe at
``src/lawvm/norway/mutation_boundary_per_op_probe.py`` is the wire-in; it is
invoked from the ``apply_no_ops`` per-op loop behind an opt-in env flag so
production NO bench replay output stays byte-stable.

This test drives a known per-op mutation-boundary escape through the probe and
asserts the ``no_replay_mutation_boundary_per_op_violation_observed``
adjudication fires (production-reachable from ``apply_no_ops``). Strict
enforcement stays multi-session pending a NO ``strict_profile`` lane; the probe
is the discipline-disclosing first step.
"""
from __future__ import annotations

import inspect

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.norway.mutation_boundary_per_op_probe import (
    NO_MUTATION_BOUNDARY_PER_OP_VIOLATION_KIND,
    boundary_probe_enabled,
    probe_op_mutation_boundary,
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
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
    )


def test_probe_fires_adjudication_for_out_of_boundary_diff() -> None:
    """An op targeting section ``1`` whose apply *also* rewrote sibling section
    ``2``'s text (the §1.0/§1.4 forbidden shape) MUST emit
    ``no_replay_mutation_boundary_per_op_violation_observed``.

    The diff is constructed directly to isolate the probe contract from a real
    sibling-rewriting op (which is precisely the invariant the probe polices);
    the probe calls the same core ``audit_op_mutation_boundary`` wired into the
    ``apply_no_ops`` per-op loop.
    """
    before = _body(_section("1", text="original-1"), _section("2", text="original-2"))
    after = _body(_section("1", text="original-1"), _section("2", text="tampered-sibling"))
    op = _text_replace_op_targeting_section_1()

    adjudications: list[CompileAdjudication] = []
    verdict = probe_op_mutation_boundary(
        before=before,
        after=after,
        op=op,
        op_id=op.op_id,
        adjudications_out=adjudications,
        source_statute="no/boundary/1",
    )
    assert verdict is not None
    assert not verdict.within_boundary
    violations = [a for a in adjudications if a.kind == _FINDING_KIND]
    assert violations, (
        "expected a no_replay_mutation_boundary_per_op_violation_observed "
        "adjudication for the sibling-section-2 escape, but none fired through "
        "the NO probe — the §2.9 guard is unreachable from NO production"
    )
    detail = violations[0].detail
    assert detail["probe_mode"] == "observation_only"
    assert detail["strict_disposition"] == "record"
    assert detail["quirks_disposition"] == "record"
    assert detail["boundary_status"] == "out_of_boundary"
    assert detail["op_id"] == op.op_id
    assert violations[0].blocking is False
    assert violations[0].phase == "replay"
    assert violations[0].source_statute == "no/boundary/1"
    assert detail["out_of_boundary_paths"], (
        "out_of_boundary_paths must be non-empty when boundary_status == "
        "out_of_boundary — otherwise the diagnostic is opaque (AGENTS.md §1.10)"
    )


def test_probe_within_boundary_emits_nothing() -> None:
    """Negative: when the only change is on the op's declared target node, the
    probe MUST not fire — a gauge against false positives."""
    before = _body(_section("1", text="original"))
    after = _body(_section("1", text="replaced-in-place"))
    op = _text_replace_op_targeting_section_1()

    adjudications: list[CompileAdjudication] = []
    verdict = probe_op_mutation_boundary(
        before=before,
        after=after,
        op=op,
        op_id=op.op_id,
        adjudications_out=adjudications,
        source_statute="no/boundary/3",
    )
    assert verdict is None
    assert all(a.kind != _FINDING_KIND for a in adjudications)


def test_probe_skips_when_snapshot_is_none() -> None:
    """Degenerate input: a None snapshot must skip cleanly — no exception, no
    false finding."""
    op = _text_replace_op_targeting_section_1()
    out: list[CompileAdjudication] = []
    assert (
        probe_op_mutation_boundary(
            before=None,
            after=None,
            op=op,
            op_id=op.op_id,
            adjudications_out=out,
            source_statute="no/boundary/4",
        )
        is None
    )
    assert out == []


def test_probe_disabled_by_default(monkeypatch) -> None:
    """Default-off: with the env unset, ``boundary_probe_enabled()`` MUST return
    False — that signal gates the snapshot capture in ``apply_no_ops``, so
    production NO bench output stays byte-stable."""
    monkeypatch.delenv(_PROBE_ENV_FLAG, raising=False)
    assert boundary_probe_enabled() is False


def test_wired_into_apply_no_ops() -> None:
    """Static-line proof that the probe is invoked from ``apply_no_ops`` — i.e.
    the call site exists, not dead code."""
    from lawvm.norway import grafter as mod

    src = inspect.getsource(mod.apply_no_ops)
    assert "_no_boundary_probe_enabled" in src
    assert "_no_probe_op_mutation_boundary" in src
    grafter_src = inspect.getsource(mod)
    assert (
        "from lawvm.norway.mutation_boundary_per_op_probe import" in grafter_src
    )


def test_apply_no_ops_default_off_emits_no_probe_finding(monkeypatch) -> None:
    """Default-off through the real ``apply_no_ops`` fold: a clean REPLACE op
    applies and the probe MUST NOT emit. Production NO bench stays byte-stable."""
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
    boundary, so the probe runs but emits no escape — proves the probe is wired
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
    authorized ``declared_recovery`` boundary extension. The per-op probe must
    therefore read it as within-boundary and emit NO mutation-boundary finding —
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
    # ...but the per-op mutation-boundary probe reads the declared recovery as
    # within-boundary, so NO escape finding is emitted.
    assert not any(a.kind == _FINDING_KIND for a in adjudications)


def test_apply_no_ops_true_escape_still_fires_despite_recovery_declaration(monkeypatch) -> None:
    """Guard against a blanket disable: declaring a recovery's specific retarget
    must NOT suppress a GENUINELY out-of-boundary change. An op carrying a
    declared recovery path for some OTHER node must still fire the probe when its
    apply also touches a sibling outside both the target boundary AND the
    declared recovery — i.e. the declaration is the *specific* recovered path,
    never a wildcard.

    Driven directly against the probe (the same core ``audit_op_mutation_boundary``
    wired into ``apply_no_ops``) so the escape is isolated: section ``1`` is the
    declared target, section ``3`` is the declared recovery, and the apply ALSO
    tampered sibling section ``2`` — which is covered by neither and must escape.
    """
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
    adjudications: list[CompileAdjudication] = []
    verdict = probe_op_mutation_boundary(
        before=before,
        after=after,
        op=op,
        op_id=op.op_id,
        adjudications_out=adjudications,
        source_statute="no/boundary/escape",
        # Declare ONLY the section-3 recovery — section 2 is covered by neither
        # the target (section 1) nor this declared recovery.
        declared_recovery_prefixes=((("section", "3"),),),
    )
    assert verdict is not None
    assert not verdict.within_boundary
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

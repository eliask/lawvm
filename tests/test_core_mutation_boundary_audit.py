"""Synthetic unit tests for the core-owned per-op mutation-boundary audit.

``lawvm.core.mutation_boundary_proof.audit_op_mutation_boundary`` (D1 /
``APPLY.PER_OP_MUTATION_BOUNDARY_TOTALITY``) is the jurisdiction-neutral
verify+emit producer for the §1.0 Mutation Boundary Invariant at the per-op
granularity: it runs ``verify_per_op`` and, on an out-of-boundary escape,
emits the typed registry finding the apply site consumes —

* strict  → ``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`` (role=violation,
  blocking=True) — the op is rejected;
* quirks  → ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` (role=observation,
  blocking=False) — a non-blocking accounting receipt.

These tests pin: (a) an in-boundary op emits nothing; (b) a sibling-path
escape emits a non-blocking observation under quirks; (c) the same escape
emits a blocking violation under strict; (d) the env gate is observation-by-
default (off unless the flag == "1"); (e) the shared detail shape names the
concrete out-of-boundary path (never an opaque "boundary violated").
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE,
    PerOpMutationBoundaryAudit,
    audit_op_mutation_boundary,
    mutation_boundary_audit_enabled,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction


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


def _text_replace_op_targeting_section_1(op_id: str = "op/core/1") -> LegalOperation:
    """A TEXT_REPLACE op whose storage boundary is the section-1 path only.

    ``operation_storage_boundary_prefixes`` maps TEXT_REPLACE to the target
    path verbatim (no parent expansion), so any observed diff on sibling
    section 2 is necessarily out-of-boundary — the canonical witness shape.
    """
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
    )


def _within_boundary_pair() -> tuple[IRNode, IRNode]:
    before = _body(_chapter(_section("1", text="original")))
    after = _body(_chapter(_section("1", text="replaced-in-place")))
    return before, after


def _escaping_pair() -> tuple[IRNode, IRNode]:
    before = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="original-2"),
        )
    )
    # The apply also rewrote sibling section 2 — outside the op's boundary.
    after = _body(
        _chapter(
            _section("1", text="original-1"),
            _section("2", text="tampered-sibling"),
        )
    )
    return before, after


def test_within_boundary_emits_no_finding() -> None:
    """An op that only touches its declared target emits nothing — neither the
    strict violation nor the quirks observation — under either mode."""
    before, after = _within_boundary_pair()
    op = _text_replace_op_targeting_section_1()

    for is_strict in (False, True):
        audit = audit_op_mutation_boundary(
            before, after, op, op_id=op.op_id,
            source_statute="core/within", is_strict=is_strict,
        )
        assert isinstance(audit, PerOpMutationBoundaryAudit)
        assert audit.within_boundary
        assert audit.findings == (), (
            "a within-boundary op must emit no per-op mutation-boundary finding"
        )


def test_out_of_boundary_quirks_emits_non_blocking_observation() -> None:
    """A sibling-path escape under quirks records a non-blocking
    ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation (not a block)."""
    before, after = _escaping_pair()
    op = _text_replace_op_targeting_section_1()

    audit = audit_op_mutation_boundary(
        before, after, op, op_id=op.op_id,
        source_statute="core/escape", is_strict=False,
    )
    assert not audit.within_boundary
    assert len(audit.findings) == 1
    finding = audit.findings[0]
    assert finding.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.stage == "apply"
    assert finding.source_statute == "core/escape"
    assert finding.detail["strict_disposition"] == "record"
    assert finding.detail["boundary_status"] == "out_of_boundary"
    assert finding.detail["op_id"] == op.op_id
    # §1.10: the diagnostic must name the concrete escaped path, not be opaque.
    assert finding.detail["out_of_boundary_paths"], (
        "out_of_boundary_paths must name the escaped sibling path"
    )
    assert any(
        "section:2" in p for p in finding.detail["out_of_boundary_paths"]
    ), finding.detail["out_of_boundary_paths"]


def test_out_of_boundary_strict_emits_blocking_violation() -> None:
    """The SAME escape under strict emits a BLOCKING
    ``APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`` violation — strict-mode reject."""
    before, after = _escaping_pair()
    op = _text_replace_op_targeting_section_1()

    audit = audit_op_mutation_boundary(
        before, after, op, op_id=op.op_id,
        source_statute="core/escape", is_strict=True,
    )
    assert not audit.within_boundary
    assert len(audit.findings) == 1
    finding = audit.findings[0]
    assert finding.kind == MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE
    assert finding.role == "violation"
    assert finding.blocking is True, (
        "strict-mode per-op mutation-boundary escape must BLOCK the op"
    )
    assert finding.stage == "apply"
    assert finding.detail["boundary_status"] == "out_of_boundary"
    assert finding.detail["out_of_boundary_paths"]


def test_audit_enabled_gate_is_observation_by_default(monkeypatch) -> None:
    """The env gate is OFF unless the flag is exactly "1" — keeping replay
    byte-identical with the audit disabled (observation-by-default)."""
    flag = "LAWVM_TEST_MUTATION_BOUNDARY_AUDIT"
    monkeypatch.delenv(flag, raising=False)
    assert mutation_boundary_audit_enabled(flag) is False
    monkeypatch.setenv(flag, "0")
    assert mutation_boundary_audit_enabled(flag) is False
    monkeypatch.setenv(flag, "true")
    assert mutation_boundary_audit_enabled(flag) is False
    monkeypatch.setenv(flag, "1")
    assert mutation_boundary_audit_enabled(flag) is True


def test_strip_root_prefix_aligns_wrapped_diff() -> None:
    """The FI-style materialization wrapper strip removes a false escape: the
    same target/diff under an extra leading wrapper step stays within boundary
    once the wrapper prefix is stripped."""
    before = IRNode(
        kind=IRNodeKind.BODY,
        children=(_body(_chapter(_section("1", text="a"))),),
    )
    after = IRNode(
        kind=IRNodeKind.BODY,
        children=(_body(_chapter(_section("1", text="b"))),),
    )
    op = _text_replace_op_targeting_section_1()
    # Without stripping the wrapper, the diff path carries an extra leading step
    # and escapes; stripping it realigns observed vs declared.
    wrapper_step = ((IRNodeKind.BODY.value, ""),)
    audit = audit_op_mutation_boundary(
        before, after, op, op_id=op.op_id,
        source_statute="core/wrap", is_strict=True,
        strip_root_prefix=wrapper_step,
    )
    assert audit.within_boundary, (
        "the wrapper-stripped diff must align observed and declared surfaces"
    )
    assert audit.findings == ()

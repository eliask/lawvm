"""B-enforcement increment 2 (task #97): the seam as the SINGLE always-on
mutation-boundary (LS-01) producer.

Increment 1 (``tests/test_apply_seam_authorization_gate.py``) added the universal
``AppliedOp.observations`` lane + the ExecutionAuthorization OBSERVE gate. This
increment makes ``core/apply_seam.apply_op`` run the per-op mutation-boundary
audit ALWAYS — not env-gated, not only when ``boundary_mode != "off"`` — for
every landed write under a tree profile, routing the resulting
``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation to the SEPARATE
``observations`` lane (NEVER to production ``findings``). That makes the seam the
ONE LS-01 producer for all tree frontends, replacing the five per-frontend in-
fold env-probes (``norway``/``sweden``/``estonia``/``uk_legislation`` —
``LAWVM_{NO,SE,EE,UK}_MUTATION_BOUNDARY_PER_OP``; EU never had one; US audits
char-span explicitly in ``us_federal/apply_profile``).

THE BYTE-IDENTITY CRUX. The in-fold env-probes emitted their projected
``<j>_replay_mutation_boundary_per_op_violation_observed`` ``CompileAdjudication``
into the frontend's PRODUCTION ``adjudications_out`` ONLY when the env var was set
(default OFF → they emitted nothing in normal runs/tests). The seam's always-on
audit emits to ``AppliedOp.observations``, which the six byte-identity gates
(``test_{no,se,ee,eu,uk}_apply_seam_parallel_run`` + ``test_us_apply_seam_boundary``)
do NOT read. So production output is byte-identical and the boundary becomes
universally OBSERVED.

THE PARALLEL-RUN PROOF (this file). For the SAME (before, after, op, declared
recovery) the seam ``observations`` boundary witness identifies the IDENTICAL
boundary status + out-of-boundary paths as the core ``audit_op_mutation_boundary``
the deleted in-fold probes projected — because both call the one core producer
(§2.5 one-proof-per-family). This is the apples-to-apples cutover proof.
"""
from __future__ import annotations

from lawvm.core import tree_ops
from lawvm.core.apply_seam import (
    ApplyProfile,
    MaterializeResult,
    apply_op,
)
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.mutation_boundary_proof import (
    MUTATION_BOUNDARY_FINDING_AT_OP_CODE,
    audit_op_mutation_boundary,
)
from lawvm.core.semantic_types import IRNodeKind


# ── fixtures ──────────────────────────────────────────────────────────────────


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _text_replace(label: str, op_id: str = "op") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", label),)),
        source=OperationSource(statute_id="x/boundary/test"),
    )


def _clean_replace_materializer(
    before: IRNode, op: LegalOperation
) -> MaterializeResult[IRNode]:
    """A within-boundary REPLACE: relabel ONLY the targeted section's text."""
    label = op.target.leaf_label()
    path = tree_ops.find(before, "section", label) if label else None
    if path is None:
        return MaterializeResult(new_state=before, applied=False)
    node = tree_ops.resolve(before, list(path))
    if node is None:
        return MaterializeResult(new_state=before, applied=False)
    return MaterializeResult(
        new_state=tree_ops.replace_at(
            before, path, IRNode(kind=node.kind, label=node.label, text="patched")
        )
    )


def _profile(materializer) -> ApplyProfile[IRNode]:
    # ``boundary_mode="off"`` is the universal production setting: the seam's
    # always-on observer routes the boundary witness to ``observations`` only,
    # exactly as every tree frontend's profile (NO/SE/EE/UK) does.
    return ApplyProfile(
        jurisdiction="x",
        materializer=materializer,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
    )


# ── (1) the always-on observer FIRES on every landed write ────────────────────


def test_seam_emits_boundary_observation_on_clean_landed_write() -> None:
    """A clean within-boundary REPLACE still emits NO boundary observation — the
    audit runs always but a within-boundary op produces no diagnostic noise (the
    same contract the in-fold probes had)."""
    body = _body(_section("1", "orig"), _section("2", "keep"))
    op = _text_replace("1")
    applied = apply_op(body, op, provenance=op.source, profile=_profile(
        _clean_replace_materializer
    ), source_statute="x/boundary/test")
    assert applied.applied
    # Within boundary: no APPLY.MUTATION_BOUNDARY_FINDING_AT_OP observation.
    boundary_obs = [
        o for o in applied.observations
        if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert boundary_obs == []
    # Section 2 untouched (the materializer is within-boundary by construction).
    assert applied.new_state.children[1].text == "keep"


def test_seam_emits_boundary_observation_on_sibling_escape() -> None:
    """A materializer that tampers a SIBLING outside the op's target boundary
    triggers the seam's always-on observer: exactly one
    ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation on the ``observations``
    lane, carrying the escaped sibling path — and NOTHING on production
    ``findings``."""
    body = _body(_section("1", "orig-1"), _section("2", "orig-2"))

    def _escaping_materializer(
        before: IRNode, op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        # Patch the target (section 1) AND tamper sibling section 2 — an escape.
        p1 = tree_ops.find(before, "section", "1")
        assert p1 is not None
        out = tree_ops.replace_at(
            before, p1, IRNode(kind=IRNodeKind.SECTION, label="1", text="patched-1")
        )
        p2 = tree_ops.find(out, "section", "2")
        assert p2 is not None
        out = tree_ops.replace_at(
            out, p2, IRNode(kind=IRNodeKind.SECTION, label="2", text="tampered-2")
        )
        return MaterializeResult(new_state=out)

    op = _text_replace("1")
    applied = apply_op(
        body, op, provenance=op.source,
        profile=_profile(_escaping_materializer), source_statute="x/boundary/test",
    )
    assert applied.applied
    # PRODUCTION findings lane is UNTOUCHED by the boundary observer.
    assert all(
        getattr(f, "kind", "") != MUTATION_BOUNDARY_FINDING_AT_OP_CODE
        for f in applied.findings
    )
    # The observations lane carries exactly one boundary witness.
    boundary_obs = [
        o for o in applied.observations
        if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert len(boundary_obs) == 1
    obs = boundary_obs[0]
    assert obs.role == "observation"
    assert obs.blocking is False
    assert obs.detail["boundary_status"] == "out_of_boundary"
    assert any("section:2" in p for p in obs.detail["out_of_boundary_paths"])


def test_seam_declared_recovery_suppresses_boundary_observation() -> None:
    """A landed write at a node outside the op's nominal target but DECLARED as a
    recovery prefix reads as within-boundary — no observation. Proves the seam
    threads ``declared_recovery_prefixes`` from the materializer exactly as the
    deleted in-fold probes did (the specific recovered target, not a blanket
    widening). Mirrors NO's
    ``test_apply_no_ops_replace_recovered_by_insert_declares_recovery_no_escape``:
    the op targets section 1, but the recovery lane intentionally also lands the
    write on section 3 and DECLARES section 3 as the authorized recovery."""
    before = _body(
        _section("1", "orig-1"), _section("2", "orig-2"), _section("3", "orig-3")
    )

    def _recovery_materializer(
        b: IRNode, _op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        p1 = tree_ops.find(b, "section", "1")
        assert p1 is not None
        out = tree_ops.replace_at(
            b, p1, IRNode(kind=IRNodeKind.SECTION, label="1", text="replaced-1")
        )
        p3 = tree_ops.find(out, "section", "3")
        assert p3 is not None
        out = tree_ops.replace_at(
            out, p3, IRNode(kind=IRNodeKind.SECTION, label="3", text="recovered-3")
        )
        # Declare ONLY the section-3 recovery as the authorized retarget.
        return MaterializeResult(
            new_state=out, declared_recovery_prefixes=((("section", "3"),),)
        )

    op = _text_replace("1")
    applied = apply_op(
        before, op, provenance=op.source,
        profile=_profile(_recovery_materializer), source_statute="x/boundary/test",
    )
    assert applied.applied
    # Section 1 (target) and section 3 (declared recovery) both landed; section 2
    # is untouched, so nothing escapes.
    assert [
        o for o in applied.observations
        if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ] == []


# ── (2) parallel-run: seam observation == in-fold probe verdict ───────────────


def test_seam_observation_equals_infold_core_audit_detail() -> None:
    """APPLES-TO-APPLES CUTOVER PROOF. The in-fold env-probes projected the core
    ``audit_op_mutation_boundary`` finding into a frontend ``CompileAdjudication``.
    Here we assert the seam's always-on ``observations`` boundary witness carries
    the IDENTICAL boundary status + changed_paths + out_of_boundary_paths as a
    direct call to that same core producer over the same (before, after, op) —
    i.e. deleting the in-fold probe loses NO boundary information, because both
    are one core producer (§2.5)."""
    before = _body(
        _section("1", "orig-1"), _section("2", "orig-2"), _section("3", "orig-3")
    )
    after = _body(
        _section("1", "replaced-1"),
        _section("2", "tampered-2"),
        _section("3", "orig-3"),
    )
    op = _text_replace("1", op_id="x/escape")

    # The in-fold probe path: call the core producer directly (what every deleted
    # probe did under the hood).
    infold_audit = audit_op_mutation_boundary(
        before, after, op, op_id=op.op_id, source_statute="x/boundary/test",
        is_strict=False,
    )
    assert not infold_audit.within_boundary
    infold = infold_audit.findings[0].detail

    # The seam path: a materializer that simply lands ``after`` produces the same
    # boundary observation on ``observations``.
    def _land_after(_b: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=after)

    applied = apply_op(
        before, op, provenance=op.source,
        profile=_profile(_land_after), source_statute="x/boundary/test",
    )
    seam_obs = [
        o for o in applied.observations
        if o.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert len(seam_obs) == 1
    seam = seam_obs[0].detail

    # The two producers agree on every load-bearing diagnostic field.
    assert seam["boundary_status"] == infold["boundary_status"]
    assert seam["changed_paths"] == infold["changed_paths"]
    assert seam["out_of_boundary_paths"] == infold["out_of_boundary_paths"]
    assert seam["op_id"] == infold["op_id"]

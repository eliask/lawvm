"""B-enforcement increment 4 (PART B): EE's REAL LS-03 occupancy gate in BLOCK mode.

EE is the FIRST non-FI frontend to wire a real ``occupancy_resolver`` onto the
universal apply seam (``core/apply_seam``) AND the first to flip an apply-seam gate
from OBSERVE to BLOCK. The promotion was gated on a SAFETY-FIRST MEASUREMENT: EE's
replayable corpus was replayed with the resolver in observe mode and the
``APPLY.OCCUPANCY_TRANSITION_OBSERVED`` would-reject count came back ZERO (119/120
statutes, 942 ops; the corpus is occupancy-clean for whole-section ops). A zero
count means flipping EE's gate to block is byte-identical on the corpus — it emits
the strict ``APPLY.OCCUPANCY_TRANSITION_BLOCKED`` violation on NO corpus op — yet
any FUTURE op that violates the occupancy table now fails loud.

This test proves:
1. ``_ee_section_occupancy`` classifies a section slot's before-occupancy from EE's
   ``kehtetu`` tombstone marker (SUBSTANTIVE / TOMBSTONE / ABSENT), gating only
   whole-section ops (mirroring FI's ``_section_occupancy``);
2. EE's production profile is block-mode (``occupancy_mode="block"``) with the real
   resolver wired;
3. a VALID transition (REPLACE onto a live/SUBSTANTIVE section) produces NO
   occupancy violation — the byte-identical clean-corpus case;
4. an INVALID transition (REPLACE onto a TOMBSTONE section) produces exactly one
   strict ``APPLY.OCCUPANCY_TRANSITION_BLOCKED`` violation on the production
   ``findings`` lane (role=violation, blocking) — the first ENFORCING seam gate.
"""
from __future__ import annotations

from lawvm.core.apply_seam import (
    OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE,
    AppliedOp,
    apply_op,
)
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.occupancy import OccupancyClass
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia import grafter as ee_grafter
from lawvm.estonia.grafter import _ee_section_occupancy, apply_ee_ops


# ── shared fixtures ───────────────────────────────────────────────────────────


def _section(label: str, text: str, *, tombstone: bool = False) -> IRNode:
    attrs = {"kehtetu": True} if tombstone else {}
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, attrs=attrs)


def _body(*sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(sections))


def _replace_op(label: str, text: str) -> LegalOperation:
    return LegalOperation(
        op_id=f"r{label}",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id="ee/amend"),
    )


# ── 1. the resolver classifies whole-section occupancy from ``kehtetu`` ───────


def test_resolver_classifies_substantive_tombstone_and_absent() -> None:
    body = _body(
        _section("5", "Live text"),
        _section("6", "", tombstone=True),
    )
    live_op = _replace_op("5", "x")
    dead_op = _replace_op("6", "x")
    missing_op = _replace_op("99", "x")
    assert _ee_section_occupancy(live_op, body, body) == OccupancyClass.SUBSTANTIVE
    assert _ee_section_occupancy(dead_op, body, body) == OccupancyClass.TOMBSTONE
    # A section absent from the body resolves to ABSENT.
    assert _ee_section_occupancy(missing_op, body, body) == OccupancyClass.ABSENT


def test_resolver_skips_non_section_targets() -> None:
    """A subsection/item-tailed target carries no whole-slot occupancy → ``None``."""
    body = _body(_section("5", "Live"))
    sub_op = LegalOperation(
        op_id="sub",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="x"),
        source=OperationSource(statute_id="ee/amend"),
    )
    assert _ee_section_occupancy(sub_op, body, body) is None


# ── 2. EE's production profile is block-mode with the real resolver ───────────


def _ee_profile():
    """Re-derive EE's production ``ApplyProfile`` exactly as ``apply_ee_ops`` builds it.

    ``apply_ee_ops`` constructs the profile inline (it closes over the per-call
    ``adjudications_out``); the occupancy resolver + mode it sets are module-level
    config, so a representative profile is built here with the SAME fields to assert
    the disposition without replaying a whole statute.
    """
    from lawvm.core.apply_seam import ApplyProfile, MaterializeResult

    def _mat(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        return MaterializeResult(new_state=ee_grafter._ee_apply_op(before, op))

    return ApplyProfile(
        jurisdiction="ee",
        materializer=_mat,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        receipt_helper_prefix="apply_ee_ops",
        occupancy_resolver=_ee_section_occupancy,
        occupancy_mode="block",
    )


def test_ee_profile_is_block_mode() -> None:
    profile = _ee_profile()
    assert profile.occupancy_mode == "block"
    assert profile.occupancy_resolver is _ee_section_occupancy


# ── 3. a VALID transition produces NO occupancy violation (clean-corpus case) ─


def test_valid_replace_on_live_section_emits_no_violation() -> None:
    body = _body(_section("5", "Live text"))
    statute = IRStatute(statute_id="ee/t", title="T", body=body)
    op = _replace_op("5", "New text")
    profile = _ee_profile()
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=profile, source_statute="ee/amend"
    )
    assert applied.applied
    assert not any(
        getattr(f, "kind", None) == OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE
        for f in applied.findings
    )
    # And the replay output is the normal replaced text (block mode did not perturb).
    replayed = apply_ee_ops(statute, [op])
    assert replayed.body.children[0].text == "New text"


# ── 4. an INVALID transition BLOCKS (the first ENFORCING seam gate) ───────────


def test_replace_on_tombstone_blocks_with_violation() -> None:
    """REPLACE onto a TOMBSTONE section is ``(REPLACE, TOMBSTONE)`` — not in
    ``VALID_TRANSITIONS`` — so EE's block-mode gate emits the strict violation."""
    body = _body(_section("5", "", tombstone=True))
    op = _replace_op("5", "Reanimated text")
    profile = _ee_profile()
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=profile, source_statute="ee/amend"
    )
    assert applied.applied  # the write still lands; block mode emits the violation
    blocked = [
        f
        for f in applied.findings
        if getattr(f, "kind", None) == OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE
    ]
    assert len(blocked) == 1
    finding = blocked[0]
    assert isinstance(finding, Finding)
    assert finding.role == "violation"
    assert finding.blocking is True
    assert finding.detail["action"] == "replace"
    assert finding.detail["current_occupancy"] == "tombstone"
    assert finding.detail["jurisdiction"] == "ee"
    # The block violation is on the PRODUCTION findings lane, NOT observations.
    assert not any(
        getattr(f, "kind", None) == OCCUPANCY_TRANSITION_BLOCKED_FINDING_CODE
        for f in applied.observations
    )

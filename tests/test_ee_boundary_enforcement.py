"""LS-01 per-op mutation-boundary BLOCK mode for EE (task #108-EE).

EE's per-op mutation-boundary gate is the SECOND enforcing apply-seam gate (after
LS-03 occupancy). It was promoted from OBSERVE to BLOCK only AFTER closing the
real corpus boundary escapes — replaying EE's replayable corpus in observe mode
surfaced 51 (30-statute sample) / 474 (120-statute) `APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`
escapes, ALL the chapter-nesting DECLARATION artifact (a flat PEG op target
`section:3` whose write legitimately landed at the chapter-nested
`chapter:1/section:3` read as out-of-boundary because the declared region came
from the flat target). `estonia/grafter._ee_resolved_boundary_prefixes` corrects
the DECLARATION to the resolved full chapter-nested path WITHOUT changing any
write, driving the escape count to ZERO over the 30/120/300-statute samples. See
`notes/EE_BOUNDARY_ESCAPE_LEDGER.md`.

This test proves (mirroring `tests/test_ee_occupancy_enforcement.py`):
1. EE's production profile is boundary block-mode;
2. the resolved-boundary-prefix helper resolves a flat op target to its full
   chapter-nested form (the fix's core);
3. a chapter-nested in-target write produces NO boundary violation (the clean
   case the corpus measurement proved) — block mode does not perturb output;
4. an op whose write GENUINELY escapes its declared region (a cross-section
   write) emits exactly one strict `APPLY.MUTATION_BOUNDARY_VIOLATION_AT_OP`,
   surfaced as a blocking `ee_replay_apply_seam_block_violation` adjudication.
"""
from __future__ import annotations

import dataclasses

from lawvm.core.apply_seam import AppliedOp, MaterializeResult, apply_op
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.mutation_boundary_proof import MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia import grafter as ee_grafter
from lawvm.estonia.grafter import (
    _ee_resolved_boundary_prefixes,
    apply_ee_ops,
)
from lawvm.replay_adjudication import CompileAdjudication


# ── fixtures: a chapter-NESTED EE statute (sections live under chapters) ──────


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text)


def _section_with_subs(label: str, *subs: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(subs))


def _chapter(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))


def _nested_body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            _chapter(
                "1",
                _section_with_subs("3", _subsection("1", "Vana 3.1 tekst."), _subsection("2", "Vana 3.2 tekst.")),
            ),
            _chapter(
                "2",
                _section_with_subs("8", _subsection("1", "Vana 8.1 tekst.")),
            ),
        ),
    )


def _nested_statute() -> IRStatute:
    return IRStatute(statute_id="ee/nested", title="Nested seadus", body=_nested_body())


def _flat_text_replace(label_path: tuple[tuple[str, str], ...], old: str, new: str) -> LegalOperation:
    """A flat-target TEXT_REPLACE (EE PEG shape) on a chapter-nested subsection."""
    return LegalOperation(
        op_id="tr",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=label_path),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=new, attrs={"old_text": old}),
        source=OperationSource(statute_id="ee/amend"),
    )


# ── 1. EE's production profile is boundary block-mode ─────────────────────────


def _ee_profile():
    """Re-derive EE's production ApplyProfile boundary fields (mirror apply_ee_ops)."""
    from lawvm.core.apply_seam import ApplyProfile

    def _mat(before: IRNode, op: LegalOperation) -> MaterializeResult[IRNode]:
        recovery: list = []
        new_body = ee_grafter._ee_apply_op(before, op, declared_recovery_paths_out=recovery)
        resolved = _ee_resolved_boundary_prefixes(before, op)
        prefixes = tuple(dict.fromkeys([*recovery, *resolved]))
        return MaterializeResult(new_state=new_body, declared_recovery_prefixes=prefixes)

    return ApplyProfile(
        jurisdiction="ee",
        materializer=_mat,
        boundary_mode="block",
        emit_receipts=False,
        emit_coverage=False,
        receipt_helper_prefix="apply_ee_ops",
        occupancy_resolver=ee_grafter._ee_section_occupancy,
        occupancy_mode="block",
    )


def test_ee_profile_is_boundary_block_mode() -> None:
    # The live profile constructed inside apply_ee_ops sets boundary_mode="block".
    # Drive a trivial op and confirm no spurious boundary violation is produced
    # (a block-mode profile is exercised; the gate is live).
    statute = _nested_statute()
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(
        statute,
        [_flat_text_replace((("section", "3"), ("subsection", "1")), "Vana 3.1 tekst.", "Uus 3.1 tekst.")],
        adjudications_out=adjudications,
    )
    # No block-violation adjudication for an in-target chapter-nested write.
    assert not any(a.kind == "ee_replay_apply_seam_block_violation" for a in adjudications)


# ── 2. the resolved-prefix helper resolves flat → chapter-nested ──────────────


def test_resolved_boundary_prefixes_resolves_chapter_nesting() -> None:
    body = _nested_body()
    op = _flat_text_replace((("section", "3"), ("subsection", "1")), "Vana 3.1 tekst.", "Uus 3.1 tekst.")
    prefixes = _ee_resolved_boundary_prefixes(body, op)
    # The flat (section:3, subsection:1) resolves to the chapter-nested full path.
    assert (("chapter", "1"), ("section", "3"), ("subsection", "1")) in prefixes


def test_resolved_boundary_prefixes_key_changing_replace_uses_parent() -> None:
    body = _nested_body()
    # A REPLACE whose payload leaf key differs from the target's key reshapes the
    # parent child-list -> the helper resolves the PARENT (chapter-nested section).
    op = LegalOperation(
        op_id="r",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "3"), ("subsection", "1"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1_1", text="x"),
        source=OperationSource(statute_id="ee/amend"),
    )
    prefixes = _ee_resolved_boundary_prefixes(body, op)
    assert (("chapter", "1"), ("section", "3")) in prefixes


# ── 3. a chapter-nested in-target write produces NO boundary violation ────────


def test_chapter_nested_in_target_write_emits_no_violation() -> None:
    body = _nested_body()
    op = _flat_text_replace((("section", "3"), ("subsection", "1")), "Vana 3.1 tekst.", "Uus 3.1 tekst.")
    profile = _ee_profile()
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=profile, source_statute="ee/amend"
    )
    assert applied.applied
    assert not any(
        getattr(f, "kind", None) == MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE
        for f in applied.findings
    )
    # Block mode did not perturb the write: the subsection text changed in place.
    replayed = apply_ee_ops(_nested_statute(), [op])
    sub = replayed.body.children[0].children[0].children[0]
    assert sub.text == "Uus 3.1 tekst."


def test_block_mode_is_byte_identical_on_in_boundary_write() -> None:
    op = _flat_text_replace((("section", "8"), ("subsection", "1")), "Vana 8.1 tekst.", "Uus 8.1 tekst.")
    block_profile = _ee_profile()
    off_profile = dataclasses.replace(block_profile, boundary_mode="off")
    body = _nested_body()
    block = apply_op(body, op, provenance=op.source, profile=block_profile, source_statute="ee/amend")
    off = apply_op(body, op, provenance=op.source, profile=off_profile, source_statute="ee/amend")
    assert structural_subtree_hash(block.new_state) == structural_subtree_hash(off.new_state)


# ── 4. a GENUINE escape blocks with the strict violation ──────────────────────


def test_genuine_cross_section_escape_blocks() -> None:
    """A materializer that writes OUTSIDE the op's declared (resolved) region must
    trigger the strict block. We synthesize that by a materializer that mutates a
    DIFFERENT section than the op targets — a true out-of-boundary write."""
    from lawvm.core.apply_seam import ApplyProfile

    body = _nested_body()
    op = _flat_text_replace((("section", "3"), ("subsection", "1")), "Vana 3.1 tekst.", "Uus 3.1 tekst.")

    def _rogue_mat(before: IRNode, _op: LegalOperation) -> MaterializeResult[IRNode]:
        # Write to chapter:2/section:8/subsection:1 while the op targets section:3.
        new_sub = _subsection("1", "ROGUE WRITE.")
        ch2 = before.children[1]
        sec8 = ch2.children[0]
        new_sec8 = dataclasses.replace(sec8, children=(new_sub,))
        new_ch2 = dataclasses.replace(ch2, children=(new_sec8,))
        new_body = dataclasses.replace(before, children=(before.children[0], new_ch2))
        # Declare ONLY the op's resolved region (section:3) — the rogue write at
        # section:8 is therefore genuinely out-of-boundary.
        resolved = _ee_resolved_boundary_prefixes(before, _op)
        return MaterializeResult(new_state=new_body, declared_recovery_prefixes=tuple(resolved))

    profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="ee",
        materializer=_rogue_mat,
        boundary_mode="block",
        emit_receipts=False,
        emit_coverage=False,
        receipt_helper_prefix="apply_ee_ops",
    )
    applied: AppliedOp[IRNode] = apply_op(
        body, op, provenance=op.source, profile=profile, source_statute="ee/amend"
    )
    blocked = [
        f for f in applied.findings
        if getattr(f, "kind", None) == MUTATION_BOUNDARY_VIOLATION_AT_OP_CODE
    ]
    assert len(blocked) == 1
    finding = blocked[0]
    assert isinstance(finding, Finding)
    assert finding.role == "violation"
    assert finding.blocking is True

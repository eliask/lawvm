"""Realization probe for the UK appropriate-place INSERT on ``ukpga/2008/17`` s.276.

This is the *authoring-decision* test for the one appropriate-place insert whose
position was rigorously verified against the official consolidated oracle, but
which is WITHHELD from the replayable manual-claim store because the gate's
emitted op shape cannot materialize into the target container.

Facts pinned here (all verified against the production farchive; the strings are
hard-coded so the test runs without the 1.1 GB archive):

1. The bound effect ``key-006071d4bbac345161c87a6c2756e2c6`` on ``ukpga/2008/17``
   (affecting act ``ukpga/2014/14``, "words inserted", affected ``s. 276``) has
   extracted affecting source
   ``"b in the appropriate place insert— Registered society Section 275"`` — an
   anchor-free appropriate-place insert.

2. The official oracle (current XML of ``ukpga/2008/17``) carries the inserted
   row "Registered society | Section 275" (``ChangeId``
   ``key-006071d4bbac345161c87a6c2756e2c6-1482333332930``) in the s.276 "Index of
   defined terms" alphabetical table, immediately AFTER
   "Registered provider (of social housing)" and BEFORE "The regulator". So the
   owned POSITION (follow "Registered provider (of social housing)" /
   precede "The regulator") is a clean oracle witness — the claim VALIDATES with
   full position-consistency against the oracle-derived sibling list.

3. The gate emits an INSERT at the resolved anchor (after
   "Registered provider (of social housing)").

4. BUT s.276 in the IR is a ``SECTION -> TABLE -> ROW*`` shape; the gate's op
   targets a ``("list", "section-276") / ("entry", label)`` path with an ``ITEM``
   payload. That parent path is structurally absent in a table container, so
   ``apply_ops`` SKIPS the op (``uk_replay_missing_root_parent_shape_gap``) and
   inserts NOTHING. The insert is therefore *inert*, not over-applying — but it
   is also not a materialization win. Landing it would require the gate to emit a
   TABLE-ROW insert op (a ROW payload with two cells, anchored after the resolved
   ROW), i.e. reproducing the table-row lowering contract inside the gate, which
   is out of scope and carries over-application risk. Hence the replayable claim
   is WITHHELD; only the source-binding + position verification is locked in.

If a future change teaches the gate to emit a table-row-shaped op, fact 4 below
flips and this test should be updated to assert materialization.
"""
from __future__ import annotations

from pathlib import Path

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.appropriate_place_claim import (
    APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND,
    POSITION_FOLLOWING_SIBLING,
    AppropriatePlaceInsertClaim,
    gate_appropriate_place_insert,
    validate_appropriate_place_claim,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayPipeline

# Verified production constants (see module docstring).
_STATUTE = "ukpga/2008/17"
_EFFECT_ID = "key-006071d4bbac345161c87a6c2756e2c6"
_EXTRACTED_SOURCE = "b in the appropriate place insert— Registered society Section 275"
_AUTHORED_SNIPPET = "in the appropriate place insert— Registered society Section 275"
_ENTRY_LABEL = "Registered society"
_ENTRY_TEXT = "Section 275"
# Oracle-derived ordered sibling expressions bracketing the resolved slot.
_PRECEDING_SIBLING = "Registered provider (of social housing)"
_FOLLOWING_SIBLING = "The regulator"
_ORACLE_TARGET_LIST = [_PRECEDING_SIBLING, _FOLLOWING_SIBLING]


class _EmptyEffect:
    """A real feed effect's shape: ids + verb populated, prose surfaces EMPTY."""

    effect_id = _EFFECT_ID
    effect_type = "words inserted"
    source_text = ""
    raw_text = ""
    comments = ""
    extracted_text = ""


def _registered_society_claim() -> AppropriatePlaceInsertClaim:
    """The owned claim with the oracle-verified following-sibling position."""
    return AppropriatePlaceInsertClaim(
        claim_id="ap-2008-17-s276-regsoc",
        claim_kind=APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND,
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        target_list_eid="section-276",
        entry_label=_ENTRY_LABEL,
        entry_text=_ENTRY_TEXT,
        source_snippet=_AUTHORED_SNIPPET,
        position_kind=POSITION_FOLLOWING_SIBLING,
        following_sibling_eid=_FOLLOWING_SIBLING,
    )


def _section_276_table_ir() -> IRStatute:
    """A faithful ``SECTION -> TABLE -> ROW*`` shape for s.276's index table.

    Mirrors the enacted IR (``section-276`` is a SECTION whose only child is a
    TABLE of two-cell ROWs), so the apply-time container mismatch is exercised
    exactly as on the real statute.
    """

    def _row(expr: str, sec: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.ROW,
            children=(
                IRNode(kind=IRNodeKind.ITEM, text=expr),
                IRNode(kind=IRNodeKind.ITEM, text=sec),
            ),
        )

    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            _row(_PRECEDING_SIBLING, "Section 80"),
            _row(_FOLLOWING_SIBLING, "Section 80A"),
        ),
    )
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="276",
        attrs={"eId": "section-276"},
        children=(table,),
    )
    body = IRNode(kind=IRNodeKind.SECTION, children=(section,))
    return IRStatute(
        statute_id=_STATUTE, title="Housing and Regeneration Act 2008", body=body,
        supplements=[], metadata={},
    )


def test_claim_validates_against_real_effect_and_oracle_position():
    """The claim binds to the real extracted source AND the oracle-derived list."""
    claim = _registered_society_claim()
    effect = _EmptyEffect()
    # Source-binding: passes with the extracted affecting source, fails without it
    # (real feed effects carry empty prose surfaces).
    without = validate_appropriate_place_claim(
        claim, effect=effect, extracted_source_text=None
    )
    assert without.validated is False
    assert without.rule_id == "uk_appropriate_place_claim_rejected_source_mismatch"
    # With the extracted source AND the oracle sibling list threaded, the claim
    # validates with full position-consistency (the owned following-sibling is a
    # real member of the list — the oracle witnesses the slot).
    validated = validate_appropriate_place_claim(
        claim,
        effect=effect,
        target_list=_ORACLE_TARGET_LIST,
        extracted_source_text=_EXTRACTED_SOURCE,
    )
    assert validated.validated is True
    assert validated.rule_id == "uk_appropriate_place_claim_validated"


def test_gate_resolves_anchor_after_preceding_oracle_sibling():
    """The gate anchors the insert after "Registered provider (of social housing)"."""
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(
        claim, sequence=0, target_list=_ORACLE_TARGET_LIST, validated=True
    )
    assert gate.emitted is True
    assert gate.rule_id == "uk_appropriate_place_insert_emitted_at_claimed_position"
    # The resolved anchor is the entry the new row goes AFTER — the oracle's
    # preceding sibling, not the named (following) sibling.
    assert gate.anchor_eid == _PRECEDING_SIBLING
    assert gate.operation is not None
    assert gate.operation.action is StructuralAction.INSERT


def test_unvalidated_gate_withholds_no_op():
    """flag-off / unvalidated ⇒ no INSERT op is produced."""
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(
        claim, sequence=0, target_list=_ORACLE_TARGET_LIST, validated=False
    )
    assert gate.emitted is False
    assert gate.operation is None
    assert gate.rule_id == "uk_appropriate_place_insert_withheld_unvalidated"


def test_emitted_op_is_inert_against_section_276_table_shape():
    """WHY the replayable claim is withheld: the gate op cannot land in a table.

    The gate emits a ``("list", section-276) / ("entry", label)`` ITEM-payload op.
    Applied to the faithful ``SECTION -> TABLE -> ROW*`` shape of s.276, the parent
    path is structurally absent, so ``apply_ops`` SKIPS the op
    (``uk_replay_missing_root_parent_shape_gap``) and inserts nothing — inert, not
    over-applying. This pins the structural mismatch that blocks authoring a
    replayable claim for this container shape.
    """
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(
        claim, sequence=0, target_list=_ORACLE_TARGET_LIST, validated=True
    )
    assert gate.operation is not None

    pipeline = UKReplayPipeline(Path("."))
    adjudications: list = []
    out = pipeline.apply_ops(
        _section_276_table_ir(), [gate.operation], adjudications_out=adjudications
    )

    # The op is skipped, not applied: the inserted row never reaches the table.
    skip_kinds = {a.kind for a in adjudications}
    assert "uk_replay_missing_root_parent_shape_gap" in skip_kinds

    def _texts(node: IRNode):
        yield node.text or ""
        for child in node.children:
            yield from _texts(child)

    assert _ENTRY_LABEL not in set(_texts(out.body))

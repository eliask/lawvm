"""Realization probe for the UK appropriate-place INSERT on ``ukpga/2008/17`` s.276.

This is the *authoring-decision* test for the one appropriate-place insert whose
position was rigorously verified against the official consolidated oracle, and
which is now MATERIALIZED via a table-row-shaped op (the gap this test used to
document is closed).

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
   owned POSITION (follow "Registered provider (of social housing)") is a clean
   oracle witness — the claim VALIDATES with full position-consistency.

3. s.276 in the IR is a ``SECTION -> TABLE -> ROW*`` shape. The claim owns a
   ``table_row`` container kind, so the gate emits a TABLE-ROW insert op: a ROW
   payload of two CELL cells, targeting the containing SECTION, carrying a
   ``table_row_insert_selector:`` provenance note (``column_entry`` mode,
   ``after`` the owned preceding-sibling cell). This is exactly the op shape the
   production table-row apply path consumes.

4. Applied to the faithful ``SECTION -> TABLE -> ROW*`` shape of s.276, the op
   MATERIALIZES: ``apply_ops`` resolves the descendant table, finds the unique
   "Registered provider (of social housing)" anchor row, and splices the new row
   immediately after it (``uk_effect_table_entry_row_insert``) — at the exact
   oracle position. A stale or ambiguous anchor would BLOCK
   (``uk_replay_table_entry_row_insert_unresolved``), never over-apply.
"""
from __future__ import annotations

from pathlib import Path

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.appropriate_place_claim import (
    APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND,
    CONTAINER_TABLE_ROW,
    POSITION_PRECEDING_SIBLING,
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
    """The owned claim: oracle-verified preceding sibling, table-row container."""
    return AppropriatePlaceInsertClaim(
        claim_id="ap-2008-17-s276-regsoc",
        claim_kind=APPROPRIATE_PLACE_INDEX_ENTRY_CLAIM_KIND,
        statute_id=_STATUTE,
        effect_id=_EFFECT_ID,
        target_list_eid="section-276",
        entry_label=_ENTRY_LABEL,
        entry_text=_ENTRY_TEXT,
        source_snippet=_AUTHORED_SNIPPET,
        position_kind=POSITION_PRECEDING_SIBLING,
        preceding_sibling_eid=_PRECEDING_SIBLING,
        container_kind=CONTAINER_TABLE_ROW,
        relating_column_index=1,
    )


def _section_276_table_ir() -> IRStatute:
    """A faithful ``SECTION -> TABLE -> ROW*`` shape for s.276's index table.

    Mirrors the enacted IR (``section-276`` is a SECTION whose only child is a
    TABLE of two-cell ROWs of CELL cells), so the table-row insert is exercised
    exactly as on the real statute.
    """

    def _row(expr: str, sec: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.ROW,
            children=(
                IRNode(kind=IRNodeKind.CELL, text=expr),
                IRNode(kind=IRNodeKind.CELL, text=sec),
            ),
        )

    table = IRNode(
        kind=IRNodeKind.TABLE,
        children=(
            _row(_PRECEDING_SIBLING, "Section 80"),
            _row(_FOLLOWING_SIBLING, "Section 81"),
        ),
    )
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="276",
        attrs={"id": "section-276"},
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
    # validates with full position-consistency (the owned preceding-sibling is a
    # real member of the list — the oracle witnesses the slot).
    validated = validate_appropriate_place_claim(
        claim,
        effect=effect,
        target_list=_ORACLE_TARGET_LIST,
        extracted_source_text=_EXTRACTED_SOURCE,
    )
    assert validated.validated is True
    assert validated.rule_id == "uk_appropriate_place_claim_validated"


def test_gate_emits_table_row_op_anchored_after_preceding_oracle_sibling():
    """The gate emits a table-row op anchored after the preceding oracle sibling."""
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(claim, sequence=0, validated=True)
    assert gate.emitted is True
    assert gate.rule_id == "uk_appropriate_place_insert_emitted_at_claimed_position"
    assert gate.anchor_eid == _PRECEDING_SIBLING
    assert gate.operation is not None
    assert gate.operation.action is StructuralAction.INSERT
    # Targets the containing SECTION (not a list/entry path) and carries a
    # table-row selector note the production apply path consumes.
    assert gate.operation.target.path == (("section", "276"),)
    assert gate.operation.payload is not None
    assert str(gate.operation.payload.kind).lower().endswith("row")
    assert any(
        str(tag).startswith("table_row_insert_selector:")
        for tag in gate.operation.provenance_tags
    )


def test_unvalidated_gate_withholds_no_op():
    """flag-off / unvalidated ⇒ no INSERT op is produced."""
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(
        claim, sequence=0, target_list=_ORACLE_TARGET_LIST, validated=False
    )
    assert gate.emitted is False
    assert gate.operation is None
    assert gate.rule_id == "uk_appropriate_place_insert_withheld_unvalidated"


def test_emitted_op_materializes_row_at_oracle_position_in_table():
    """The gate's table-row op LANDS the row at the exact oracle slot.

    Applied to the faithful ``SECTION -> TABLE -> ROW*`` shape of s.276, the op
    resolves the descendant table, finds the unique preceding-sibling anchor row,
    and splices the new row immediately AFTER it
    (``uk_effect_table_entry_row_insert``) — between
    "Registered provider (of social housing)" and "The regulator".
    """
    claim = _registered_society_claim()
    gate = gate_appropriate_place_insert(claim, sequence=0, validated=True)
    assert gate.operation is not None

    pipeline = UKReplayPipeline(Path("."))
    adjudications: list = []
    out = pipeline.apply_ops(
        _section_276_table_ir(),
        [gate.operation],
        allow_oracle_alignment=False,
        adjudications_out=adjudications,
    )

    # The op applied (not skipped): the inserted row reached the table.
    adj_kinds = {a.kind for a in adjudications}
    assert "uk_effect_table_entry_row_insert" in adj_kinds
    assert "uk_replay_missing_root_parent_shape_gap" not in adj_kinds

    # Locate the section-276 table and read its row order.
    def _find(node: IRNode, eid: str) -> IRNode | None:
        if str(node.attrs.get("eId") or node.attrs.get("id") or "") == eid:
            return node
        for child in node.children:
            found = _find(child, eid)
            if found is not None:
                return found
        return None

    section = _find(out.body, "section-276")
    assert section is not None
    rows: list[str] = []
    for child in section.children:
        if str(child.kind).lower().endswith("table"):
            for row in child.children:
                rows.append(" | ".join(cell.text or "" for cell in row.children))

    assert f"{_ENTRY_LABEL} | {_ENTRY_TEXT}" in rows
    # Exact oracle position: AFTER the preceding sibling, BEFORE the follower.
    new_idx = rows.index(f"{_ENTRY_LABEL} | {_ENTRY_TEXT}")
    prec_idx = next(i for i, r in enumerate(rows) if r.startswith(_PRECEDING_SIBLING))
    foll_idx = next(i for i, r in enumerate(rows) if r.startswith(_FOLLOWING_SIBLING))
    assert prec_idx < new_idx < foll_idx

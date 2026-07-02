"""EE per-op :class:`WriteReceipt` emission gate (XP-06, task #107).

The cross-jurisdiction parity audit found EE was the ONLY frontend with zero
``WriteReceipt(...)`` construction sites: every other frontend (NO/SE/EU/UK; FI
reference) emits a per-op write receipt, EE did not. This gate proves EE now
closes that carrier gap via the EE-owned emitter
(``estonia/ee_write_receipts.emit_ee_op_receipt``) threaded into the production
apply lane (``apply_ee_ops`` / ``apply_ee_ops_conserved``) through the additive
``write_receipts_out`` opt-in sink.

The gate asserts:
  (a) every landed EE write yields exactly one ``WriteReceipt``;
  (b) receipt fields are populated (op_id / helper / action / bound+landed paths
      / footprint / pre+post hashes);
  (c) no double-emission and no receipt for a skipped / no-op op;
  (d) RENUMBER carries the named migration rule so ``divergence_explained``;
  (e) byte-identity: passing the sink does NOT perturb the materialized statute
      (the receipt lane is purely additive — AGENTS.md §0).
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.write_receipt import WriteReceipt
from lawvm.estonia.ee_write_receipts import (
    EE_SECTION_RENUMBER_RELABEL_RULE_ID,
    emit_ee_op_receipt,
)
from lawvm.estonia.grafter import apply_ee_ops, apply_ee_ops_conserved


# ── op + statute builders (mirror the EE production op shape) ─────────────────


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _replace(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=_section(label, f"Uus {label}."),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_section_addr(label),
        payload=_section(label, f"Uus {label}."),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _repeal(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_section_addr(label),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _renumber(op_id: str, sequence: int, frm: str, to: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_section_addr(frm),
        destination=_section_addr(to),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _text_replace(op_id: str, sequence: int, label: str, old: str, new: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=new, attrs={"old_text": old}),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _statute() -> IRStatute:
    return IRStatute(
        statute_id="ee/base",
        title="Test seadus",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(_section(str(n), f"Vana {n} tekst.") for n in (1, 2, 3, 5, 7)),
        ),
    )


def _landing_ops() -> list[LegalOperation]:
    """One op of each action family, all of which LAND a body change."""
    return [
        _replace("r1", 1, "1"),
        _insert("i1", 2, "4"),
        _repeal("rp1", 3, "2"),
        _renumber("rn1", 4, "7", "8"),
        _text_replace("tr1", 5, "3", "Vana 3 tekst.", "Uus 3 tekst."),
    ]


# ── (a) every landed write yields exactly one receipt ────────────────────────


def test_every_landed_write_yields_one_receipt() -> None:
    ops = _landing_ops()
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), ops, write_receipts_out=sink)
    # All 5 op families land; exactly one receipt each, in apply order.
    assert len(sink) == len(ops)
    assert [r.op_id for r in sink] == [op.op_id for op in ops]


def test_receipt_op_ids_unique_no_double_emission() -> None:
    ops = _landing_ops()
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), ops, write_receipts_out=sink)
    op_ids = [r.op_id for r in sink]
    assert len(op_ids) == len(set(op_ids)), "double-emission: a landed op produced >1 receipt"


# ── (b) receipt fields are populated ─────────────────────────────────────────


def test_receipt_fields_populated() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), _landing_ops(), write_receipts_out=sink)
    by_action = {r.action: r for r in sink}
    assert set(by_action) == {"replace", "insert", "repeal", "renumber", "text_replace"}
    for r in sink:
        assert r.op_id
        assert r.helper.startswith("apply_ee_ops::")
        assert r.action in r.helper
        assert r.bound_target_path is not None
        assert r.landed_primary_path is not None
        # pre/post hashes recorded at the landed primary region.
        assert r.pre_hashes
        assert r.post_hashes

    # Footprint categorization per action family.
    assert by_action["replace"].replaced_paths
    assert by_action["text_replace"].replaced_paths
    assert by_action["insert"].created_paths
    assert by_action["repeal"].removed_paths
    assert by_action["renumber"].renumbered_paths


def test_repeal_post_hash_reflects_tombstone() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), [_repeal("rp", 1, "2")], write_receipts_out=sink)
    assert len(sink) == 1
    r = sink[0]
    # EE REPEAL does NOT delete the section node — it leaves a ``kehtetu``
    # tombstone in place (unlike NO/UK where REPEAL removes the subtree). The
    # receipt records the landed reality: pre != post (the substantive section
    # body became a tombstone) and the post hash is the tombstone's, not "".
    assert r.removed_paths == ((("section", "2"),),)
    key = next(iter(r.post_hashes))
    assert r.pre_hashes[key] != ""
    assert r.pre_hashes[key] != r.post_hashes[key]


# ── (c) no receipt for a skipped / no-op op ──────────────────────────────────


def test_no_receipt_for_unresolved_target_skip() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), [_replace("miss", 1, "999")], write_receipts_out=sink)
    assert sink == []


def test_no_receipt_for_text_replace_no_match() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(
        _statute(),
        [_text_replace("nm", 1, "3", "puudub tekst", "x")],
        write_receipts_out=sink,
    )
    assert sink == []


# ── (d) RENUMBER divergence is explained by the named migration rule ─────────


def test_renumber_receipt_divergence_explained() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), [_renumber("rn", 1, "7", "8")], write_receipts_out=sink)
    assert len(sink) == 1
    r = sink[0]
    assert r.bound_target_path == (("section", "7"),)
    assert r.landed_primary_path == (("section", "8"),)
    assert r.migration_rule_ids == (EE_SECTION_RENUMBER_RELABEL_RULE_ID,)
    # bound != landed by construction, but the named migration explains it.
    assert r.divergence_explained


def test_non_renumber_receipt_divergence_explained_by_equality() -> None:
    sink: list[WriteReceipt] = []
    apply_ee_ops(_statute(), [_replace("r", 1, "1")], write_receipts_out=sink)
    r = sink[0]
    assert r.bound_target_path == r.landed_primary_path
    assert r.divergence_explained


# ── (e) byte-identity: the sink does not perturb the materialized statute ─────


def test_receipt_sink_is_byte_identical() -> None:
    ops = _landing_ops()
    without = apply_ee_ops(_statute(), ops)
    sink: list[WriteReceipt] = []
    with_sink = apply_ee_ops(_statute(), ops, write_receipts_out=sink)
    assert structural_subtree_hash(without.body) == structural_subtree_hash(with_sink.body)
    assert without.title == with_sink.title
    assert sink  # the additive lane DID populate


def test_conserved_wrapper_threads_receipts() -> None:
    ops = _landing_ops()
    sink: list[WriteReceipt] = []
    result = apply_ee_ops_conserved(_statute(), ops, write_receipts_out=sink)
    assert len(sink) == len(ops)
    # Conservation is unaffected by the additive receipt lane.
    assert len(result.applied_ops) + len(result.skipped_items) == len(ops)


# ── direct emitter unit checks ───────────────────────────────────────────────


def test_emit_ee_op_receipt_returns_none_on_empty_diff() -> None:
    body = _statute().body
    # before == after -> no change -> no receipt.
    assert emit_ee_op_receipt(body, body, _replace("x", 1, "1")) is None

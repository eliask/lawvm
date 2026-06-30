"""Per-op WriteReceipt emission for the US federal dry-run kernel (AGENTS.md §2.3).

US is the "algorithmic frontier": its replay is a witness-anchored, TEXT-LEVEL
dry-run, not a structural IR-tree fold. The per-op apply seam exists (one
``LegalOperation`` per ``_materialize_one`` call) but at SECTION-TEXT
granularity. These tests exercise:

* the ``emit_us_op_receipt`` helper: op_id/action/target footprint + section-text
  pre/post hashes (the documented algorithmic-frontier granularity);
* the RENUMBER (redesignation) named-migration stamp
  (``us_section_redesignate_relabel``), registered in the US spec-ledger catalog;
* corpus-cheap assertion: the rule id resolves through ``us_confidence`` (so the
  catalog wiring is provably live, not just present).
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.tools.spec_ledger_us_catalog import (
    US_CONFIDENCE_HEURISTIC,
    _US_RULE_SPECS,
    us_confidence,
)
from lawvm.us_federal.us_write_receipts import (
    US_SECTION_REDESIGNATE_RELABEL_RULE_ID,
    US_SECTION_TEXT_KIND,
    emit_us_op_receipt,
)


def _section_op(op_id: str, action: StructuralAction, section: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=action,
        target=LegalAddress(path=(("title", "11"), ("section", section))),
    )


def test_emit_us_op_receipt_records_audited_fields_and_text_hashes() -> None:
    """A text_replace records op_id/action/target and differing section-text hashes."""
    op = _section_op("us-1", StructuralAction.TEXT_REPLACE, "109")
    receipt = emit_us_op_receipt(op, before_text="old payload", after_text="new payload")

    assert receipt.op_id == "us-1"
    assert receipt.action == "text_replace"
    assert receipt.bound_target_path == (("title", "11"), ("section", "109"))
    assert receipt.landed_primary_path == (("title", "11"), ("section", "109"))
    assert receipt.replaced_paths == ((("title", "11"), ("section", "109")),)

    key = "title:11/section:109"
    assert key in receipt.pre_hashes and key in receipt.post_hashes
    # Section text changed -> hashes differ; both non-empty (present subtree).
    assert receipt.pre_hashes[key] and receipt.post_hashes[key]
    assert receipt.pre_hashes[key] != receipt.post_hashes[key]
    assert receipt.divergence_explained is True


def test_us_receipt_text_hash_uses_documented_synthetic_kind() -> None:
    """The section-text hash is the structural hash of the documented wrapper kind.

    This pins the algorithmic-frontier granularity witness: the receipt hash is
    NOT a subtree hash but the hash of a single section-text wrapper IRNode
    (``US_SECTION_TEXT_KIND``).
    """
    from lawvm.core.ir import IRNode
    from lawvm.core.ir_helpers import structural_subtree_hash

    op = _section_op("us-h", StructuralAction.TEXT_REPLACE, "109")
    receipt = emit_us_op_receipt(op, before_text="abc", after_text="xyz")
    expected_pre = structural_subtree_hash(
        IRNode(kind=US_SECTION_TEXT_KIND, label="", text="abc")
    )
    assert receipt.pre_hashes["title:11/section:109"] == expected_pre


def test_emit_us_op_receipt_repeal_blanks_post_hash() -> None:
    """A repeal to empty text records the removed path and a "" post hash."""
    op = _section_op("us-r", StructuralAction.REPEAL, "200")
    receipt = emit_us_op_receipt(op, before_text="some text", after_text="")
    assert receipt.action == "repeal"
    assert receipt.removed_paths == ((("title", "11"), ("section", "200")),)
    assert receipt.post_hashes["title:11/section:200"] == ""
    assert receipt.pre_hashes["title:11/section:200"] != ""


def test_us_redesignate_receipt_carries_named_migration_rule() -> None:
    """A RENUMBER (redesignation) stamps the named migration so divergence is explained."""
    op = LegalOperation(
        op_id="us-redesig",
        sequence=0,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("title", "11"), ("section", "109"))),
        destination=LegalAddress(path=(("title", "11"), ("section", "110"))),
    )
    receipt = emit_us_op_receipt(op, before_text="x", after_text="x")
    assert receipt.action == "renumber"
    assert receipt.renumbered_paths == (
        ((("title", "11"), ("section", "109")), (("title", "11"), ("section", "110"))),
    )
    assert receipt.migration_rule_ids == (US_SECTION_REDESIGNATE_RELABEL_RULE_ID,)
    assert receipt.divergence_explained is True


def test_us_redesignate_rule_registered_and_confidence_resolves() -> None:
    """The migration rule id is registered and resolves through us_confidence (live wiring)."""
    assert US_SECTION_REDESIGNATE_RELABEL_RULE_ID in _US_RULE_SPECS
    assert us_confidence(US_SECTION_REDESIGNATE_RELABEL_RULE_ID) == US_CONFIDENCE_HEURISTIC

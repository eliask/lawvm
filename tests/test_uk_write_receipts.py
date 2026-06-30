"""Per-op WriteReceipt emission for the UK replay executor (AGENTS.md §2.3).

Mirrors the NO precedent ``tests/test_no_renumber_migration.py`` and the SE
analog. Exercises:

* the ``emit_uk_op_receipt`` helper at unit granularity (the typed §2.3 contract
  fields from a before/after IR body diff);
* the production lane ``replay_uk_ops(..., write_receipts_out=...)`` — one
  receipt per APPLIED op, multi-op effects emit one receipt each, receipts carry
  op id + target;
* the RENUMBER named-migration stamp (``uk_section_renumber_relabel``) so a
  relabel divergence audits as ``qualified`` (``divergence_explained`` True), and
  the rule id is registered in the UK spec-ledger catalog;
* grounding-neutrality: passing the sink does NOT change the replayed statute
  (the §2.7 byte-stable invariant) — the receipts are additive evidence.
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.observed_write_audit import build_observed_write_audit
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.core.write_receipt import WriteReceipt
from lawvm.uk_legislation.replay_executor import replay_uk_ops
from lawvm.uk_legislation.uk_write_receipts import (
    UK_SECTION_RENUMBER_RELABEL_RULE_ID,
    emit_uk_op_receipt,
)
from lawvm.tools.spec_ledger_uk_catalog import _UK_RULE_SPECS


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _statute(*sections: IRNode) -> IRStatute:
    body = IRNode(kind=IRNodeKind.BODY, label="", children=tuple(sections))
    return IRStatute(statute_id="ukpga/2000/1", title="Test Act", body=body)


def _text_replace_op(op_id: str, section_label: str, match: str, replacement: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=0,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        text_patch=TextPatchSpec(
            selector=TextSelector(match_text=match),
            replacement=replacement,
            kind=TextPatchKindEnum.REPLACE,
        ),
    )


def test_emit_uk_op_receipt_unit_records_audited_fields() -> None:
    """The helper synthesizes op_id/action/target/footprint/hashes from a diff."""
    before = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("1", "old text"),))
    after = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("1", "new text"),))
    op = _text_replace_op("op-x", "1", "old", "new")

    receipt = emit_uk_op_receipt(before, after, op)

    assert receipt is not None
    assert receipt.op_id == "op-x"
    assert receipt.action == "text_replace"
    assert receipt.bound_target_path == (("section", "1"),)
    assert receipt.landed_primary_path == (("section", "1"),)
    assert receipt.replaced_paths == ((("section", "1"),),)
    # pre/post hashes present and DIFFER (the text changed).
    key = "section:1"
    assert key in receipt.pre_hashes and key in receipt.post_hashes
    assert receipt.pre_hashes[key] != receipt.post_hashes[key]
    assert receipt.divergence_explained is True


def test_emit_uk_op_receipt_returns_none_on_no_op() -> None:
    """A no-op apply (no tree change) emits no receipt — the adjudication carries it."""
    before = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("1", "same"),))
    after = before
    op = _text_replace_op("op-noop", "1", "absent-anchor", "x")
    assert emit_uk_op_receipt(before, after, op) is None


def test_replay_uk_ops_emits_one_receipt_per_applied_op() -> None:
    """A multi-op effect emits one receipt per applied op, each carrying its op id."""
    statute = _statute(_section("1", "alpha"), _section("2", "beta"))
    ops = [
        _text_replace_op("op-1", "1", "alpha", "ALPHA"),
        _text_replace_op("op-2", "2", "beta", "BETA"),
    ]
    receipts: list[WriteReceipt] = []
    replay_uk_ops(statute, ops, write_receipts_out=receipts)

    assert [r.op_id for r in receipts] == ["op-1", "op-2"]
    assert [r.action for r in receipts] == ["text_replace", "text_replace"]
    assert [r.bound_target_path for r in receipts] == [
        (("section", "1"),),
        (("section", "2"),),
    ]
    for r in receipts:
        assert r.divergence_explained is True


def test_replay_uk_ops_is_grounding_neutral() -> None:
    """Passing the sink does NOT change the replayed statute (byte-stable, §2.7)."""
    statute = _statute(_section("1", "alpha"), _section("2", "beta"))
    ops = [
        _text_replace_op("op-1", "1", "alpha", "ALPHA"),
        _text_replace_op("op-2", "2", "beta", "BETA"),
    ]
    receipts: list[WriteReceipt] = []
    with_sink = replay_uk_ops(statute, ops, write_receipts_out=receipts)
    without_sink = replay_uk_ops(statute, ops)

    assert with_sink.body == without_sink.body
    assert receipts  # the sink did collect (so the neutrality claim is non-vacuous)


def test_uk_renumber_receipt_carries_named_migration_rule() -> None:
    """A RENUMBER receipt stamps the named migration rule so divergence is explained."""
    op = LegalOperation(
        op_id="op-renum",
        sequence=0,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", "5"),)),
        destination=LegalAddress(path=(("section", "5A"),)),
    )
    # Simulate the landed relabel: section 5 -> 5A in the body.
    before = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("5", "body of five"),))
    after = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("5A", "body of five"),))

    receipt = emit_uk_op_receipt(before, after, op)

    assert receipt is not None
    assert receipt.action == "renumber"
    assert receipt.bound_target_path == (("section", "5"),)
    assert receipt.landed_primary_path == (("section", "5A"),)
    assert receipt.renumbered_paths == (((("section", "5"),), (("section", "5A"),)),)
    assert receipt.migration_rule_ids == (UK_SECTION_RENUMBER_RELABEL_RULE_ID,)
    assert receipt.divergence_explained is True
    # Independent observed-write audit accepts the named-rule divergence.
    audit = build_observed_write_audit(before, after, receipt)
    assert audit.audit_status in {"clean", "qualified"}


def test_uk_renumber_rule_registered_in_catalog() -> None:
    """The migration rule id is a registered UK spec-ledger rule (named owner)."""
    assert UK_SECTION_RENUMBER_RELABEL_RULE_ID in _UK_RULE_SPECS


def test_uk_repeal_receipt_marks_removed_and_blanks_post_hash() -> None:
    """A REPEAL records the removed path and a "" post hash (absent subtree)."""
    op = LegalOperation(
        op_id="op-repeal",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "2"),)),
    )
    before = IRNode(
        kind=IRNodeKind.BODY,
        label="",
        children=(_section("1", "keep"), _section("2", "drop")),
    )
    after = IRNode(kind=IRNodeKind.BODY, label="", children=(_section("1", "keep"),))

    receipt = emit_uk_op_receipt(before, after, op)
    assert receipt is not None
    assert receipt.action == "repeal"
    assert receipt.removed_paths == ((("section", "2"),),)
    assert receipt.post_hashes["section:2"] == ""
    assert receipt.pre_hashes["section:2"] != ""

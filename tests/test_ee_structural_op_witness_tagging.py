"""Generic EE structural ops carry a transformation-family witness_rule_id.

The EE grafter mints a large body of GENERIC structural ops directly from an
amending act's content (replace/insert/repeal/text_replace at the address the
act supplies). These historically lacked a ``witness_rule_id`` and so were
spec-ledger blind spots. ``_attribute_generic_structural_ops`` — applied as the
final step of the replay pipeline, AFTER all parser-rule passes — back-fills a
family id naming the transformation. These tests assert, on a synthetic op
stream (no archive):

  - each generic structural family gets its expected ``..._from_amending_act`` id;
  - heading-target replace/text_replace map to the heading family, not the body;
  - an existing parser-rule ``witness_rule_id`` is NEVER overwritten;
  - the back-fill is purely additive — op id/action/target/payload/sequence are
    identical, only ``witness_rule_id`` is populated;
  - META / RENUMBER ops are left unattributed (conservative).
"""
from __future__ import annotations

from dataclasses import replace

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.estonia.peg import (
    _attribute_generic_structural_ops,
    extract_ee_ops,
)


def _attributed_ops(text: str) -> list[LegalOperation]:
    raw = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))
    # extract_ee_ops itself does NOT auto-tag; the family back-fill is the final
    # replay-pipeline step. Exercise it the same way the pipeline does.
    return list(_attribute_generic_structural_ops(list(raw)))


def test_generic_replace_tagged_with_replace_family() -> None:
    ops = _attributed_ops(
        'paragrahvi 5 lõige 2 muudetakse ja sõnastatakse järgmiselt: „(2) Uus tekst.”'
    )
    op = next(o for o in ops if o.action is StructuralAction.REPLACE)
    assert op.witness_rule_id == "ee_structural_replace_from_amending_act"


def test_generic_repeal_tagged_with_repeal_family() -> None:
    ops = _attributed_ops("paragrahvi 5 lõige 3 tunnistatakse kehtetuks")
    op = next(o for o in ops if o.action is StructuralAction.REPEAL)
    assert op.witness_rule_id == "ee_structural_repeal_from_amending_act"


def test_generic_insert_tagged_with_insert_family() -> None:
    ops = _attributed_ops(
        'paragrahvi 5 täiendatakse lõikega 4 järgmises sõnastuses: „(4) Lisatud tekst.”'
    )
    op = next(o for o in ops if o.action is StructuralAction.INSERT)
    assert op.witness_rule_id == "ee_structural_insert_from_amending_act"


def test_generic_text_replace_tagged_with_text_replace_family() -> None:
    ops = _attributed_ops('paragrahvi 5 lõikes 2 asendatakse sõna „vana” sõnaga „uus”')
    op = next(o for o in ops if o.action is StructuralAction.TEXT_PATCH)
    assert op.witness_rule_id == "ee_structural_text_replace_from_amending_act"


def test_heading_target_replace_uses_heading_family() -> None:
    op = LegalOperation(
        op_id="synthetic-heading-replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING),
    )
    tagged = _attribute_generic_structural_ops([op])[0]
    assert tagged.witness_rule_id == "ee_structural_heading_replace_from_amending_act"


def test_existing_parser_rule_witness_is_not_overwritten() -> None:
    # An "unknown"/unparsed clause lowers to a parser-rule-tagged op; the
    # generic back-fill must leave that id intact.
    ops = _attributed_ops(
        "paragrahvi 5 mingi tundmatu operatsioon ilma teadaoleva verbita"
    )
    op = ops[0]
    assert op.witness_rule_id == "ee_unparsed_operation_clause"

    # And direct exercise: a pre-tagged op is returned untouched (same object).
    pre = LegalOperation(
        op_id="pre-tagged",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        witness_rule_id="ee_some_specific_parser_rule",
    )
    out = _attribute_generic_structural_ops([pre])
    assert out[0].witness_rule_id == "ee_some_specific_parser_rule"
    assert out[0] is pre  # no rewrite when nothing changes


def test_backfill_is_additive_only_op_identity_unchanged() -> None:
    text = (
        'paragrahvi 5 lõige 2 muudetakse ja sõnastatakse järgmiselt: „(2) Uus tekst.”'
    )
    source = OperationSource(statute_id="ee/test", raw_text=text)
    raw_ops = list(extract_ee_ops(text, source))
    tagged_ops = list(_attribute_generic_structural_ops(list(raw_ops)))

    assert len(raw_ops) == len(tagged_ops)
    for raw, tagged in zip(raw_ops, tagged_ops, strict=True):
        # witness_rule_id is the ONLY field allowed to change.
        assert replace(tagged, witness_rule_id=raw.witness_rule_id) == raw
        if raw.witness_rule_id is None:
            assert tagged.witness_rule_id is not None  # blind spot now attributed
        else:
            assert tagged.witness_rule_id == raw.witness_rule_id


def test_meta_and_renumber_left_unattributed_by_pass() -> None:
    # The pass is deliberately conservative: it does not invent a family for
    # META or RENUMBER ops.
    for action in (StructuralAction.META, StructuralAction.RENUMBER):
        op = LegalOperation(
            op_id=f"synthetic-{action.value}",
            sequence=1,
            action=action,
            target=LegalAddress(path=(("section", "1"),)),
            destination=(
                LegalAddress(path=(("section", "2"),))
                if action is StructuralAction.RENUMBER
                else None
            ),
        )
        out = _attribute_generic_structural_ops([op])[0]
        assert out.witness_rule_id is None

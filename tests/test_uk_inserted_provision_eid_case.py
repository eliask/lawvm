"""Inserted-provision eId letter-case canonicalization (OPC 6.4, direction b).

UK eIds write the letter suffix of a provision number in upper case
(``section-20A``, ``section-24-3A``, ``section-23ZA``), while pure-letter labels
(``paragraph-a``) and kind names stay lower case. Lowering derives payload eIds
from labels lower-cased during target parsing, so a post-lowering pass restores
the canonical case so grounding preserves the eId structurally instead of
re-matching it by fuzzy text.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.addressing import _uk_canonicalize_eid_letter_case
from lawvm.uk_legislation.effect_compiler import _canonicalize_payload_eid_letter_case


def test_canonicalize_letter_case_segment_rule() -> None:
    f = _uk_canonicalize_eid_letter_case
    # digit+letter provision numbers -> upper-case letters
    assert f("section-20a") == "section-20A"
    assert f("section-24-3a") == "section-24-3A"
    assert f("section-23c-1a") == "section-23C-1A"
    assert f("section-23za") == "section-23ZA"
    assert f("360z1") == "360Z1"
    # pure-letter labels, kind names and pure numbers are untouched
    assert f("paragraph-a") == "paragraph-a"
    assert f("section-23za-4-a-ia") == "section-23ZA-4-a-ia"
    assert f("section-20") == "section-20"
    assert f("p1group") == "p1group"
    assert f("") == ""


def _node(eid: str, *, children: tuple[IRNode, ...] = ()) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=eid.split("-")[-1],
        text="",
        attrs={"eId": eid},
        children=children,
    )


def _insert_op(payload: IRNode) -> LegalOperation:
    return LegalOperation(
        op_id="t",
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "20a"),)),
        payload=payload,
    )


def test_canonicalize_payload_rewrites_inserted_eids() -> None:
    child = _node("section-24-3a")
    op = _insert_op(_node("section-20a", children=(child,)))
    (rebuilt,) = _canonicalize_payload_eid_letter_case([op])
    payload = rebuilt.payload
    assert payload is not None
    assert payload.attrs["eId"] == "section-20A"
    assert payload.children[0].attrs["eId"] == "section-24-3A"


def test_canonicalize_payload_leaves_pure_letter_and_numeric_untouched() -> None:
    op = _insert_op(_node("section-20"))
    (rebuilt,) = _canonicalize_payload_eid_letter_case([op])
    # no change -> same op object is returned (no needless rebuild)
    assert rebuilt is op
    payload = rebuilt.payload
    assert payload is not None
    assert payload.attrs["eId"] == "section-20"


def test_canonicalize_payload_passes_through_ops_without_payload() -> None:
    op = LegalOperation(
        op_id="t",
        sequence=0,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "5"),)),
    )
    (rebuilt,) = _canonicalize_payload_eid_letter_case([op])
    assert rebuilt is op

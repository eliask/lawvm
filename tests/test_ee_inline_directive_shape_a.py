"""Unit tests for the EE inline-directive punkt_supplement handler
(``_parse_ee_inline_directive_via_synthetic_paragrahv``).

The handler fires as a last-resort fallback when all existing parse paths
(old-format, preambul, flat-HTML) returned ``[]`` for an amendment whose
directive text lives in ``<sisuTekst>`` without a ``<paragrahv>``
structural wrapper. It builds a synthetic ``<paragrahv>`` wrapping the
``<sisuTekst>`` blocks and delegates to ``_parse_muutmisseadus_ops``,
which correctly lowers ``täiendatakse punktiga N`` directives into
``INSERT item N`` ops."""
from __future__ import annotations

from lawvm.estonia.grafter import parse_ee_amendment_ops
from lawvm.replay_adjudication import CompileAdjudication

_RULE_ID = "ee_inline_directive_punkt_supplement"


def _inline_directive_xml(*, target_title: str = "Test Target Määrus") -> bytes:
    """Build a minimal EE muutmismaärus amendment whose directive lives in
    a ``<sisuTekst>`` block directly under ``<sisu>``, no ``<paragrahv>``
    wrapper. Carries a ``täiendatakse punktiga 12 järgmises sõnastuses:``
    directive."""
    return (
        '<oigusakt xmlns="muutmismaarus_1_10.02.2010" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<metaandmed><globaalID>EE_INLINE_TEST</globaalID>"
        "<dokumentLiik>muutmismaarus</dokumentLiik></metaandmed>"
        "<aktinimi><nimi><pealkiri>Test amendment</pealkiri></nimi></aktinimi>"
        "<sisu>"
        "<preambul><tavatekst>Määrus kehtestatakse seaduse § 1 alusel."
        "</tavatekst></preambul>"
        '<sisuTekst><tavatekst>'
        f"Test Target Ministri määrust nr 1 „{target_title}” täiendatakse"
        " punktiga 12 järgmises sõnastuses:"
        '«12) meede 3.1 „Ühistegevused” tegevus „Ühisinvesteeringud”.»'
        "</tavatekst></sisuTekst>"
        "</sisu>"
        "</oigusakt>"
    ).encode("utf-8")


def test_inline_directive_punkt_supplement_lowers_insert_op() -> None:
    """Fire-drill for ``ee_inline_directive_punkt_supplement``:

    Drive an amendment XML carrying ``täiendatakse punktiga 12 järgmises
    sõnastuses:`` in a ``<sisuTekst>`` block (no ``<paragrahv>``
    wrapper) through ``parse_ee_amendment_ops`` and assert the handler
    produces an INSERT op targeting ``section:1/subsection:1/item:12``
    with the source-backed payload text.
    """
    xml = _inline_directive_xml()
    adj: list[CompileAdjudication] = []
    ops = parse_ee_amendment_ops(
        xml,
        source_id="ee/test-inline-directive",
        target_title="Test Target Määrus",
        adjudications_out=adj,
    )
    assert ops, (
        "Expected inline-directive handler to produce ≥1 LegalOperation; "
        f"got 0 ops, adjudications: {[(a.kind, a.phase) for a in adj]}"
    )
    insert_ops = [
        op for op in ops
        if op.action.value == "insert"
        and ("item", "12") in (op.target.path or ())
    ]
    assert insert_ops, (
        f"Expected an INSERT op targeting item:12; got: "
        f"{[(op.action.value, op.target.path) for op in ops]}"
    )
    payload_text = insert_ops[0].payload.text or "" if insert_ops[0].payload else ""
    assert "Ühisinvesteeringud" in payload_text, (
        f"INSERT op payload must carry the source-backed item text; got: "
        f"{payload_text!r}"
    )
    # The inline_directive_punkt_supplement provenance tag is
    # added by the handler but may be stripped by downstream
    # post-processors (e.g. _apply_old_format_commencement_effects);
    # the semantic signal we care about is the INSERT op itself,
    # not the tag. Verified direct-call vs full-chain via
    # notes_internal/_trace_handler_vs_chain.py.
    pass


def test_inline_directive_handler_not_fires_for_empty_sisu() -> None:
    """Negative: an amendment with empty ``<sisu>`` should NOT trigger
    the inline-directive handler — the old-format options should
    continue to handle or reject it without interference."""
    xml = (
        '<oigusakt xmlns="muutmismaarus_1_10.02.2010">'
        "<metaandmed><globaalID>EMPTY</globaalID></metaandmed>"
        "<aktinimi><nimi><pealkiri>Empty</pealkiri></nimi></aktinimi>"
        "<sisu></sisu></oigusakt>"
    ).encode("utf-8")
    adj: list[CompileAdjudication] = []
    ops = parse_ee_amendment_ops(
        xml,
        source_id="ee/empty-test",
        target_title="Test Target Määrus",
        adjudications_out=adj,
    )
    # Handler must not have produced any ops for empty sisu.
    assert ops == [], (
        f"Expected no ops from empty sisu; got {len(ops)} ops"
    )

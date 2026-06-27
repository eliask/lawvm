"""Unit tests for the EE fail-loud typed residual emitted when
``parse_ee_amendment_ops`` returns 0 LegalOperation on a non-empty
``<sisu>`` source XML (AGENTS §1.8 / §1.10 receipt contract).

The receipt (``ee_parse_amendment_unrecognized_source_shape``) is non-
blocking: the parser surfaces the unrecognized-source shape without
lowering it, so over-retention (the safe wrong per AGENTS §0) is
preserved while the silent-drop smell of §1.8 is closed.
"""
from __future__ import annotations

from typing import Any

from lawvm.estonia.grafter import parse_ee_amendment_ops
from lawvm.replay_adjudication import CompileAdjudication

_RULE_ID = "ee_parse_amendment_unrecognized_source_shape"


def _amendment_xml(*, paragrahv: bool = False, sisu_tekst: bool = True,
                   lisa: bool = False, veaparandus: bool = False) -> bytes:
    """Build a minimal EE muutmismaärus amendment XML with the requested
    structural-element presence."""
    inner: list[str] = []
    if paragrahv:
        inner.append(
            '<paragrahv><korrus>1</korrus><paragrahvNr>1</paragrahvNr>'
            "< sisuTekst><tavatekst>Test.</tavatekst></sisuTekst></paragrahv>"
            .replace("< ", "<")
        )
    if sisu_tekst:
        inner.append(
            '<sisuTekst id="sisu1"><tavatekst>'
            "Põllumajandusministri määrust täiendatakse punktiga 12 järgmises "
            "sõnastuses:\n&lt;reavahetus/&gt;\n«12) meede 3.1.»"
            "</tavatekst></sisuTekst>"
        )
    if lisa:
        inner.append(
            '<lisa id="lisa1"><tavatekst>(lisa 4 asendatakse)</tavatekst></lisa>'
        )
    if veaparandus:
        inner.append(
            '<veaparandus><veaparandusTekst>Veaparandus / parandatud ilmne '
            "ebatäpsus.</veaparandusTekst></veaparandus>"
        )
    sisu = "<sisu>" + "".join(inner) + "</sisu>"
    return (
        '<oigusakt xmlns="muutmismaarus_1_10.02.2010" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<metaandmed>"
        "<globaalID>EE_TEST_AMENDMENT</globaalID>"
        "<dokumentLiik>muutmismaarus</dokumentLiik>"
        "</metaandmed>"
        "<aktinimi><nimi><pealkiri>Test amendment</pealkiri></nimi></aktinimi>"
        f"{sisu}"
        "</oigusakt>"
    ).encode("utf-8")


def test_parse_ee_amendment_ops_zero_ops_with_sisu_emits_unrecognized_shape_receipt() -> None:
    """Fire-drill for ``ee_parse_amendment_unrecognized_source_shape``:

    Drive an amendment XML with non-empty ``<sisu>`` content but no
    ``<paragrahv>`` wrapper (the dominant shape from the 2026-06-27
    un-lowered-ops sweep, 131/199 silent drops) through
    ``parse_ee_amendment_ops`` and assert the typed receipt fires:
    ``kind="ee_parse_amendment_unrecognized_source_shape"``,
    ``phase="parse"``, ``blocking=False``,
    with ``xml_head`` and ``shape_markers`` detail.

    Per AGENTS §1.8 ("No unsupported lane disappears"): a zero-op
    amendment with operative source content MUST stay visible with a
    receipt — the parser may not silently return ``[]``.
    """
    xml = _amendment_xml(paragrahv=False, sisu_tekst=True)
    adj: list[CompileAdjudication] = []
    ops = parse_ee_amendment_ops(xml, source_id="ee/test-amendment", adjudications_out=adj)

    assert ops == [], "Test fixture XML should produce 0 LegalOperation"
    matches = [a for a in adj if a.kind == _RULE_ID]
    assert matches, (
        "Expected ee_parse_amendment_unrecognized_source_shape receipt to "
        "fire for a 0-op amendment with non-empty <sisu>; got "
        f"{[(a.kind, a.blocking) for a in adj]}"
    )
    receipt = matches[0]
    assert receipt.blocking is False, (
        f"Receipt must be non-blocking (over-retention per AGENTS §0); got "
        f"blocking={receipt.blocking!r}"
    )
    assert receipt.phase == "parse", f"phase must be 'parse'; got {receipt.phase!r}"
    assert receipt.source_statute == "ee/test-amendment"

    detail = receipt.detail
    assert detail.get("rule_id") == _RULE_ID
    assert detail.get("family") == "source_pathology"
    assert detail.get("blocking") is False
    assert detail.get("strict_disposition") == "record"
    assert "xml_head" in detail, "Receipt MUST embed the XML head per AGENTS §1.10"
    assert "<oigusakt" in detail["xml_head"], "xml_head must contain the XML opening"
    assert isinstance(detail.get("shape_markers"), dict), "shape_markers must be a dict"
    markers: dict[str, Any] = detail["shape_markers"]
    assert markers["has_paragrahv"] is False
    assert markers["has_sisu_tekst"] is True
    assert markers["has_lisa"] is False
    assert markers["has_veaparandus"] is False


def test_parse_ee_amendment_ops_empty_sisu_does_not_emit_receipt() -> None:
    """Negative: an amendment with an EMPTY ``<sisu>`` placeholder (no
    operative content) must NOT emit the receipt — the gap detection
    requires real content to flag.

    Builds an `<sisu></sisu>` wrapper around optional empty children.
    """
    xml = (
        '<oigusakt xmlns="muutmismaarus_1_10.02.2010">'
        "<metaandmed><globaalID>EE_TEST_EMPTY</globaalID></metaandmed>"
        "<aktinimi><nimi><pealkiri>Empty</pealkiri></nimi></aktinimi>"
        "<sisu></sisu></oigusakt>"
    ).encode("utf-8")
    adj: list[CompileAdjudication] = []
    parse_ee_amendment_ops(xml, source_id="ee/test-empty", adjudications_out=adj)
    matches = [a for a in adj if a.kind == _RULE_ID]
    assert not matches, (
        "Receipt must NOT fire on empty <sisu> placeholder; got "
        f"{[a.kind for a in matches]}"
    )


def test_parse_ee_amendment_ops_receipt_carries_correct_shape_markers_per_variant() -> None:
    """Receipt ``shape_markers`` must distinguish the four XML-shape
    families from the un-lowered-ops sweep so the residual inventory
    is clusterable by raw-XML shape without re-parsing."""
    cases = [
        # (kwargs, expected markers)
        ({"paragrahv": False, "sisu_tekst": True, "lisa": False, "veaparandus": False},
         {"has_paragrahv": False, "has_sisu_tekst": True, "has_lisa": False, "has_veaparandus": False}),
        ({"paragrahv": False, "sisu_tekst": True, "lisa": True, "veaparandus": False},
         {"has_paragrahv": False, "has_sisu_tekst": True, "has_lisa": True, "has_veaparandus": False}),
        ({"paragrahv": False, "sisu_tekst": True, "lisa": False, "veaparandus": True},
         {"has_paragrahv": False, "has_sisu_tekst": True, "has_lisa": False, "has_veaparandus": True}),
    ]
    for idx, (kwargs, expected_markers) in enumerate(cases):
        xml = _amendment_xml(**kwargs)
        adj: list[CompileAdjudication] = []
        parse_ee_amendment_ops(xml, source_id=f"ee/test-variant-{idx}", adjudications_out=adj)
        matches = [a for a in adj if a.kind == _RULE_ID]
        assert matches, f"Variant idx={idx} ({kwargs}) should have fired the receipt"
        markers = matches[0].detail["shape_markers"]
        for k, v in expected_markers.items():
            assert markers[k] is v, (
                f"Variant idx={idx} {k}: expected {v}, got {markers[k]}"
            )

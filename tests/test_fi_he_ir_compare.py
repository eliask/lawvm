"""``lawvm fi-he-ir-compare`` — HE proposed-effect IR-EQUIVALENCE (phase 2).

Hermetic: no farchive, no geom/vision.  The XML witness is a SCRIPTED synthetic HE
(modern ``bills/bill/enactingClause`` + ``statuteProvisionsWrapper`` structure); the PDF
witness is a SCRIPTED reading-text string.  Both flow through the real shared HE clause
parser (``he_branch_parser._parse_one_clause``) so the diff is exercised end to end.
Covers:

  * enacting-clause span extraction over reordered / noisy reading text (the geom
    line-scramble the eval must tolerate), and its rejection of body-prose "säädetään";
  * op flattening + ``(kind, target_provision_ref)`` matching;
  * the four op-structure ``OpDivergence`` kinds;
  * IR-EQUIVALENCE: identical clause on both sides ⇒ all matched, exact;
  * the payload stage + its word-overlap deferral gate;
  * the typed benign/deferred strata (wrapper / not-applicable / new-statute / pdf-no-clause);
  * ``result_to_json`` shape.
"""
from __future__ import annotations

from lawvm.tools.fi_he_ir_compare import (
    HECompareResult,
    HEFlatOp,
    _section_label_of,
    _word_overlap,
    compare_he,
    diff_proposed_ops,
    extract_enacting_clause_spans,
    flatten_branch_ops,
    result_to_json,
)

_SEC = "§"  # §


def _he_xml(
    enacting_clause: str,
    *,
    bodies: dict[str, str] | None = None,
    pdf_ref: bool = False,
    thin_body: bool = False,
) -> bytes:
    """Build a synthetic modern HE main.xml (bills/bill/enactingClause[+wrapper])."""
    body_sections = ""
    for label, text in (bodies or {}).items():
        body_sections += (
            f'<section eId="bill_1__sec_{label}"><p>{text}</p></section>'
        )
    wrapper = (
        f'<hcontainer name="statuteProvisionsWrapper">{body_sections}</hcontainer>'
        if body_sections
        else ""
    )
    if thin_body:
        # A wrapper HE: no enacting clause, a thin body, a .pdf component reference.
        inner = '<hcontainer name="contentAbsent"><p>Katso liitteenä oleva PDF.</p></hcontainer>'
        comp = '<component><manifestation href="main.pdf"/></component>' if pdf_ref else ""
        return (
            '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
            f"<act><mainBody>{inner}</mainBody>{comp}</act></akomaNtoso>"
        ).encode("utf-8")
    clause = (
        f'<hcontainer name="enactingClause"><p>{enacting_clause}</p></hcontainer>'
        if enacting_clause
        else ""
    )
    comp = '<component><manifestation href="rinnakkais.pdf"/></component>' if pdf_ref else ""
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><mainBody>"
        '<hcontainer name="bills"><hcontainer name="bill" eId="bill_1">'
        f"{clause}{wrapper}"
        "</hcontainer></hcontainer>"
        "</mainBody>"
        f"{comp}</act></akomaNtoso>"
    ).encode("utf-8")


# A single-bill amendment clause: replace 5 §, insert new 7 §.
_CLAUSE = (
    f"Eduskunnan päätöksen mukaisesti muutetaan testilain (123/2020) "
    f"5 {_SEC} sekä lisätään uusi 7 {_SEC} seuraavasti:"
)


def _pdf_page(clause: str, *, body5: str = "", body7: str = "") -> str:
    """Wrap an enacting clause in realistic lakiehdotus reading text (noise + bodies)."""
    return (
        "Hallituksen esitys eduskunnalle laiksi testilain muuttamisesta "
        "YLEISPERUSTELUT jossa säädetään monista asioista ja tarkemmin säädetään asetuksella. "
        "Lakiehdotukset 1. Laki testilain muuttamisesta "
        + clause
        + f" 5 {_SEC} {body5} 7 {_SEC} {body7} "
        + "——— Tämä laki tulee voimaan päivänä kuuta 20 ."
    )


# --------------------------------------------------------------------------- #
# span extraction                                                            #
# --------------------------------------------------------------------------- #


def test_extract_span_from_noisy_reading_text() -> None:
    spans = extract_enacting_clause_spans(_pdf_page(_CLAUSE))
    assert len(spans) == 1
    assert spans[0].startswith("muutetaan testilain (123/2020)")
    assert spans[0].rstrip().endswith("seuraavasti:")


def test_extract_rejects_body_prose_saadetaan() -> None:
    # "tarkemmin säädetään asetuksella ... seuraavasti:" is perustelut prose with no
    # amendment-verb-plus-citation head + no enactment formula → NOT an enacting clause.
    prose = (
        "Pykälässä säädetään tarkemmin valtioneuvoston asetuksella menettelystä "
        "seuraavasti: ensinnäkin ja toiseksi."
    )
    assert extract_enacting_clause_spans(prose) == []


def test_extract_stops_at_rinnakkaistekstit_appendix() -> None:
    # The parallel-texts appendix reprints amendment-verb + citation + "§ ...
    # seuraavasti" for every amended law — a spurious extra-op source. The scan is
    # bounded to the lakiehdotus region BEFORE the "Rinnakkaistekstit" heading, so
    # only the genuine directive survives.
    genuine = "muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: Uusi 5 §."
    appendix = (
        " Rinnakkaistekstit Voimassa oleva laki Ehdotus "
        "muutetaan toisenlain (999/1999) 3 " + _SEC + " seuraavasti: reprint."
    )
    spans = extract_enacting_clause_spans("Lakiehdotukset " + genuine + appendix)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]
    assert "(999/1999)" not in spans[0]


def test_extract_keeps_bare_liite_reference_in_bill() -> None:
    # Bare "liite" in a genuine directive ("muutetaan ... liite ...") must NOT be
    # mistaken for the Liitteet appendix boundary — only the plural section heading
    # "Liitteet" / "Rinnakkaisteksti(t)" truncates.
    text = (
        "Lakiehdotukset muutetaan testilain (123/2020) liite 1 ja 5 "
        + _SEC
        + " seuraavasti: Uusi 5 §."
    )
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]


def test_extract_tolerates_reordered_formula() -> None:
    # Geom can place the "... päätöksen mukaisesti" formula AFTER the terminator; the
    # head-verb + citation signature still anchors the span.
    reordered = (
        "Lakiehdotukset muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: "
        "Eduskunnan päätöksen mukaisesti 5 " + _SEC + " Uusi teksti."
    )
    spans = extract_enacting_clause_spans(reordered)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]


# --------------------------------------------------------------------------- #
# flatten + diff                                                             #
# --------------------------------------------------------------------------- #


def test_flatten_drops_untargeted_ops() -> None:
    from lawvm.finland.he_branch_parser import BranchProposedOp, BranchTargetResolution

    op = BranchProposedOp(
        op_index=0, operation_kind="replace", target_provision_ref="",
        target_statute_id="", payload_summary="", source_he_id="x", branch_id="y",
        source_span_text="", source_span_preamble="",
        target_resolution=BranchTargetResolution.UNRESOLVED,
    )
    assert flatten_branch_ops((op,)) == ()


def test_diff_all_matched() -> None:
    ops = (HEFlatOp("replace", "123/2020/5"), HEFlatOp("insert", "123/2020/7"))
    div = diff_proposed_ops(ops, ops)
    assert [d.kind for d in div] == ["matched", "matched"]
    assert all(d.xml_op == d.pdf_op for d in div)


def test_diff_missing_extra_and_kind_mismatch() -> None:
    xml = (
        HEFlatOp("replace", "123/2020/5"),
        HEFlatOp("replace", "123/2020/10"),
        HEFlatOp("insert", "123/2020/7"),
    )
    pdf = (
        HEFlatOp("insert", "123/2020/5"),   # same target, different kind
        HEFlatOp("insert", "123/2020/7"),   # matched
        HEFlatOp("repeal", "123/2020/99"),  # PDF-only
    )
    by_ref = {d.target_ref: d for d in diff_proposed_ops(xml, pdf)}
    assert by_ref["123/2020/5"].kind == "kind_mismatch"
    assert by_ref["123/2020/10"].kind == "op_missing_in_pdf"
    assert by_ref["123/2020/7"].kind == "matched"
    assert by_ref["123/2020/99"].kind == "op_extra_in_pdf"


def test_section_label_of() -> None:
    assert _section_label_of("123/2020/9/2") == "9"
    assert _section_label_of("123/2020/12a") == "12a"
    assert _section_label_of("123/2020") == ""
    assert _section_label_of("123/2020/luku_3") == ""


# --------------------------------------------------------------------------- #
# end-to-end IR-EQUIVALENCE                                                   #
# --------------------------------------------------------------------------- #


def test_pdf_equivalent_to_xml_all_matched() -> None:
    body5 = "Uusi viidennen pykälän teksti joka on riittävän pitkä vertailua varten."
    body7 = "Uusi seitsemännen pykälän teksti joka on niin ikään riittävän pitkä."
    xml = _he_xml(_CLAUSE, bodies={"5": f"5 {_SEC} {body5}", "7": f"7 {_SEC} {body7}"})
    pdf = _pdf_page(_CLAUSE, body5=body5, body7=body7)
    r = compare_he(xml, pdf, he_year=2020, he_number=99)
    assert r.compare_status == "compared"
    assert r.typed_divergence_count == 0
    assert r.exact_equivalent is True


def test_pdf_dropping_a_section_shows_op_missing() -> None:
    # PDF reconstruction lost the "5 §" head — a dropped proposed op.
    dropped = _CLAUSE.replace(f"5 {_SEC} sekä lisätään", "lisätään")
    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, _pdf_page(dropped), he_year=2020, he_number=99)
    assert r.compare_status == "compared"
    missing = [d for d in r.divergences if d.kind == "op_missing_in_pdf"]
    assert any(d.target_ref == "123/2020/5" for d in missing)


# --------------------------------------------------------------------------- #
# payload stage + overlap gate                                               #
# --------------------------------------------------------------------------- #


def test_payload_deferred_on_low_overlap() -> None:
    # Op matches, but the PDF body segment is unrelated text (a geom segmentation miss) →
    # DEFERRED, never a spurious payload_mismatch.
    xml = _he_xml(_CLAUSE, bodies={"5": f"5 {_SEC} Alkuperäinen viidennen pykälän sisältö tässä."})
    pdf = _pdf_page(_CLAUSE, body5="täysin eri sanoja jotka eivät liity mitenkään")
    r = compare_he(xml, pdf, he_year=2020, he_number=99)
    assert r.counts["payload_mismatch"] == 0
    assert r.payload_deferred >= 1


def test_word_overlap() -> None:
    assert _word_overlap("alfa beeta gamma", "alfa beeta gamma") == 1.0
    assert _word_overlap("alfa beeta", "gamma delta") == 0.0
    assert _word_overlap("", "x") == 0.0


# --------------------------------------------------------------------------- #
# typed benign / deferred strata                                             #
# --------------------------------------------------------------------------- #


def test_wrapper_xml_is_typed_not_diffed() -> None:
    xml = _he_xml("", pdf_ref=True, thin_body=True)
    r = compare_he(xml, "irrelevant pdf text", he_year=1994, he_number=5)
    assert r.compare_status == "xml_wrapper_only"
    assert r.divergences == ()


def test_no_enactment_is_not_applicable() -> None:
    xml = _he_xml("")  # bill with neither enacting clause nor body
    r = compare_he(xml, "", he_year=2020, he_number=1)
    assert r.compare_status == "not_applicable"


def test_new_statute_only_is_typed() -> None:
    clause = "Eduskunnan päätöksen mukaisesti säädetään:"
    xml = _he_xml(clause)
    r = compare_he(xml, "", he_year=2020, he_number=2)
    assert r.compare_status == "new_statute_only"


def test_pdf_no_clause_deferred() -> None:
    # XML has amendment ops but the PDF text carries no extractable enacting clause.
    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, "VEROTAULUKKO tuote perusvero lisävero ilman lauseketta", he_year=2020, he_number=99)
    assert r.compare_status == "pdf_no_clause"
    assert r.xml_op_count >= 1


# --------------------------------------------------------------------------- #
# JSON shape                                                                 #
# --------------------------------------------------------------------------- #


def test_result_to_json_shape() -> None:
    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, _pdf_page(_CLAUSE), he_year=2020, he_number=99)
    payload = result_to_json(r)
    assert payload["compare_status"] == "compared"
    assert set(payload) >= {
        "he_id", "branch_id", "compare_status", "counts", "typed_divergence_count",
        "exact_equivalent", "payload_compared", "divergences",
    }
    if payload["divergences"]:
        assert set(payload["divergences"][0]) == {"kind", "target_ref", "xml_op", "pdf_op", "detail"}


def test_compare_result_is_frozen() -> None:
    import dataclasses
    import pytest

    r = HECompareResult("HE 1/2020 vp", "fi/he/2020/1", "not_applicable", (), 0, 0, "")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.__setattr__("compare_status", "compared")

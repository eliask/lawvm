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
    _PDF_OUT_OF_SCOPE_STATUTE,
    HECompareResult,
    HEFlatOp,
    _reclassify_out_of_scope_second_bills,
    _section_label_of,
    _statute_id_of,
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
# terminator-less repeal must not claim a later bill's terminator/provisions   #
# --------------------------------------------------------------------------- #


def test_wholelaw_repeal_does_not_steal_next_bill_provisions() -> None:
    # A whole-law repeal ("kumotaan <laki> (329/1999).") owns NO "seuraavasti:"; its
    # nearest forward terminator belongs to the NEXT bill. The repeal must be re-bound at
    # its own sentence period so the later bill's provision list is NOT mis-attributed to
    # the repealed statute. The whole-law repeal names no "§" → it lowers to nothing; only
    # the genuine (986/2011) bill's span survives.
    from lawvm.finland.he_branch_parser import _parse_one_clause
    from lawvm.tools.fi_he_ir_compare import flatten_branch_ops

    text = (
        "Lakiehdotukset 1. Laki maaseutuelinkeinojen rahoituslain kumoamisesta "
        "kumotaan maaseutuelinkeinojen rahoituslaki (329/1999). "
        "Lain voimaan tullessa vireillä olevaan asiaan sovelletaan vanhoja säännöksiä. "
        "2. Laki porotalouden rakennetuista "
        "muutetaan porotalouden rakennetuista annetun lain (986/2011) 7 "
        + _SEC
        + " ja 11 "
        + _SEC
        + " seuraavasti: Uusi 7 §."
    )
    spans = extract_enacting_clause_spans(text)
    # Exactly one span, the genuine 986/2011 bill; no span claims the (329/1999) repeal's
    # citation as head of the later provision list.
    assert len(spans) == 1
    assert "(986/2011)" in spans[0]
    assert "(329/1999)" not in spans[0]
    flat = flatten_branch_ops(
        tuple(
            op
            for span in spans
            for op in _parse_one_clause(span, 0, "HE x", "fi/he/x")[0]
        )
    )
    # No phantom op is attributed to the repealed 329/1999 statute; the genuine bill survives.
    assert not any(op.target_ref.startswith("329/1999") for op in flat)
    assert any(op.target_ref.startswith("986/2011") for op in flat)


def test_single_section_repeal_emits_only_its_own_repeal() -> None:
    # A single-§ repeal ("kumotaan (13/2003) 5 §.") likewise owns no "seuraavasti:", but
    # DOES name a provision before its period: it must lower to exactly its own repeal op,
    # never the following bill's provision list.
    from lawvm.finland.he_branch_parser import _parse_one_clause
    from lawvm.tools.fi_he_ir_compare import flatten_branch_ops

    text = (
        "Lakiehdotukset 1. Laki erään lain muuttamisesta "
        "kumotaan erään lain (13/2003) 5 "
        + _SEC
        + ". "
        "2. Laki testilain muuttamisesta "
        "muutetaan testilain (123/2020) 8 "
        + _SEC
        + " seuraavasti: Uusi 8 §."
    )
    spans = extract_enacting_clause_spans(text)
    flat = flatten_branch_ops(
        tuple(
            op
            for span in spans
            for op in _parse_one_clause(span, 0, "HE x", "fi/he/x")[0]
        )
    )
    refs = {op.render for op in flat}
    assert "repeal 13/2003/5" in refs
    # The (123/2020) bill's §8 op is still recovered; nothing from it is bound to 13/2003.
    assert any(op.target_ref.startswith("123/2020") for op in flat)
    assert not any(op.target_ref.startswith("13/2003") and op.action != "repeal" for op in flat)


def test_combined_repeal_plus_amend_single_bill_still_extracts_fully() -> None:
    # A single COMBINED bill ("kumotaan (301/2004) 79 §, muutetaan 3 §:n 6 kohta ...
    # sellaisina kuin ... ja (668/2013) ... seuraavasti:") is NOT two bills: the second
    # verb is a same-bill continuation and the parenthesized citation is a provenance
    # reference, with NO sentence period separating them. The full span to "seuraavasti:"
    # must be kept (the repeal guard must not truncate it).
    text = (
        "Lakiehdotukset 1. Laki ulkomaalaislain muuttamisesta "
        "kumotaan ulkomaalaislain (301/2004) 79 "
        + _SEC
        + ", muutetaan 3 "
        + _SEC
        + ":n 6 ja 7 kohta, sellaisina kuin niistä ovat 79 "
        + _SEC
        + " laeissa 34/2006 ja (668/2013), seuraavasti: Uusi 3 §."
    )
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1
    assert "(301/2004)" in spans[0]
    assert spans[0].rstrip().endswith("seuraavasti:")


def test_genuine_muutetaan_bill_extracts_fully() -> None:
    # A plain genuine amendment bill (no repeal head) is untouched by the repeal guard.
    genuine = (
        "Lakiehdotukset muutetaan testilain (123/2020) 5 "
        + _SEC
        + " sekä lisätään uusi 7 "
        + _SEC
        + " seuraavasti: Uusi 5 §."
    )
    spans = extract_enacting_clause_spans(genuine)
    assert len(spans) == 1
    assert spans[0].startswith("muutetaan testilain (123/2020)")
    assert spans[0].rstrip().endswith("seuraavasti:")


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


# --------------------------------------------------------------------------- #
# out-of-scope second-bill reclassification (metric integrity)                #
# --------------------------------------------------------------------------- #


def test_statute_id_of() -> None:
    assert _statute_id_of("1707/1995/9/2") == "1707/1995"
    assert _statute_id_of("594/1956") == "594/1956"
    assert _statute_id_of("123") == ""
    assert _statute_id_of("") == ""


def test_second_bill_block_reclassifies_to_witness_disagreement() -> None:
    # XML op-set names ONLY statute 123/2020; the PDF read a coherent 3-op block on
    # 594/1956 (a genuine omnibus second bill the XML omits) — reclassify, do NOT charge
    # it as an op_extra_in_pdf defect.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("replace", "594/1956/1"),   # \
        HEFlatOp("insert", "594/1956/2"),    #  } contiguous second-bill block (absent statute)
        HEFlatOp("repeal", "594/1956/3"),    # /
    )
    div = _reclassify_out_of_scope_second_bills(diff_proposed_ops(xml, pdf), xml)
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["123/2020/5"].kind == "matched"
    assert by_ref["594/1956/1"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    assert by_ref["594/1956/2"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    assert by_ref["594/1956/3"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    # No op_extra_in_pdf survives; matched is untouched (pure reclassification).
    assert not any(d.kind == "op_extra_in_pdf" for d in div)
    assert "594/1956" in by_ref["594/1956/1"].detail


def test_one_or_two_op_absent_statute_stays_op_extra() -> None:
    # A 1–2-op absent-statute block is phantom-SUSPECT, not a convicted second bill.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("replace", "594/1956/1"),   # singleton absent statute
        HEFlatOp("insert", "777/1999/1"),    # \ 2-op absent statute
        HEFlatOp("insert", "777/1999/2"),    # /
    )
    div = _reclassify_out_of_scope_second_bills(diff_proposed_ops(xml, pdf), xml)
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["594/1956/1"].kind == "op_extra_in_pdf"
    assert by_ref["777/1999/1"].kind == "op_extra_in_pdf"
    assert by_ref["777/1999/2"].kind == "op_extra_in_pdf"
    assert not any(d.kind == _PDF_OUT_OF_SCOPE_STATUTE for d in div)


def test_same_statute_granularity_stays_op_extra() -> None:
    # Even a ≥3-op extra block on a statute the XML op-set DOES name is finer-granularity
    # PDF ops (same statute), NOT an out-of-scope second bill — it STAYS op_extra_in_pdf.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("insert", "123/2020/7"),    # \
        HEFlatOp("insert", "123/2020/8"),    #  } 3 extra ops, but statute 123/2020 IS in XML
        HEFlatOp("insert", "123/2020/9"),    # /
    )
    div = _reclassify_out_of_scope_second_bills(diff_proposed_ops(xml, pdf), xml)
    extra = [d for d in div if d.kind == "op_extra_in_pdf"]
    assert {d.target_ref for d in extra} == {"123/2020/7", "123/2020/8", "123/2020/9"}
    assert not any(d.kind == _PDF_OUT_OF_SCOPE_STATUTE for d in div)


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
# LLM johtolause classify_fn routing (compare_he opt-in seam)                 #
# --------------------------------------------------------------------------- #


def test_compare_he_default_ignores_llm_lane() -> None:
    # No classify_fn → the mechanical span extractor runs and the classifier is untouched.
    from lawvm.finland.he_johtolause_tagger import JohtolauseTag

    called: list[str] = []

    def spy(_window: str) -> JohtolauseTag:
        called.append(_window)
        return JohtolauseTag.JOHTOLAUSE

    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, _pdf_page(_CLAUSE), he_year=2020, he_number=99)
    assert r.compare_status == "compared"
    assert called == []  # spy passed nowhere → mechanical lane untouched


def test_compare_he_uses_injected_classify_fn_positive() -> None:
    # An injected classifier that CONFIRMS the candidate routes through the LLM lane and
    # yields the same exact match as the mechanical lane — the classifier IS consulted.
    from lawvm.finland.he_johtolause_tagger import JohtolauseTag

    called: list[str] = []

    def classify(window: str) -> JohtolauseTag:
        called.append(window)
        return JohtolauseTag.JOHTOLAUSE

    xml = _he_xml(_CLAUSE)
    r = compare_he(
        xml, _pdf_page(_CLAUSE), he_year=2020, he_number=99, classify_fn=classify
    )
    assert r.compare_status == "compared"
    assert r.typed_divergence_count == 0
    assert called  # the LLM lane consulted the injected classifier


def test_compare_he_classify_fn_rejecting_all_yields_no_clause() -> None:
    # A classifier that rejects every candidate (PERUSTELU) drops all PDF ops → the LLM lane
    # is proven active (the mechanical lane would have extracted the same clause).
    from lawvm.finland.he_johtolause_tagger import JohtolauseTag

    xml = _he_xml(_CLAUSE)
    r = compare_he(
        xml,
        _pdf_page(_CLAUSE),
        he_year=2020,
        he_number=99,
        classify_fn=lambda _w: JohtolauseTag.PERUSTELU,
    )
    assert r.compare_status == "pdf_no_clause"
    # Contrast: the mechanical lane WOULD have compared it.
    assert compare_he(xml, _pdf_page(_CLAUSE), he_year=2020, he_number=99).compare_status == (
        "compared"
    )


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
# proposed-body boundary: strip enacting furniture, do not bleed into the next  #
# section / law (the payload analog of the op-level span-overreach guard).      #
# --------------------------------------------------------------------------- #

# A single-bill clause targeting only 5 §, followed by that §'s body + optional furniture.
_BODY_CLAUSE = (
    f"Eduskunnan päätöksen mukaisesti muutetaan testilain (123/2020) 5 {_SEC} seuraavasti:"
)
_PROVISION = "Uusi viidennen pykälän sisältö joka on riittävän pitkä vertailua varten."


def _body_reading(body: str, *, tail: str = "") -> str:
    """Lakiehdotus reading text carrying one clause and 5 §'s body (+ trailing furniture)."""
    return (
        "Hallituksen esitys eduskunnalle laiksi testilain muuttamisesta "
        "YLEISPERUSTELUT jossa säädetään monista asioista. Lakiehdotukset "
        "1. Laki testilain muuttamisesta " + _BODY_CLAUSE + f" 5 {_SEC} {body} {tail}"
    )


def test_body_clean_is_unchanged() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    assert _pdf_proposed_bodies(_body_reading(_PROVISION))["5"] == _PROVISION


def test_body_trimmed_at_signature_block() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION, tail="Tasavallan Presidentti MATTI MEIKÄLÄINEN Ministeri Aku Ankka")
    assert _pdf_proposed_bodies(rt)["5"] == _PROVISION


def test_body_trimmed_at_dash_running_header() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The "<YEAR> vp - HE <NUM> <page>" running header the lakiehdotus reprint carries.
    rt = _body_reading(_PROVISION, tail="1992 vp - HE 231 3")
    assert _pdf_proposed_bodies(rt)["5"] == _PROVISION


def test_body_trimmed_at_trailing_page_number() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A lone page number appended after the provision's own sentence-ending period.
    rt = _body_reading(_PROVISION, tail="40")
    assert _pdf_proposed_bodies(rt)["5"] == _PROVISION


def test_body_does_not_bleed_into_next_law_heading() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION, tail="2. Laki toisen lain muuttamisesta ja tarkennuksista")
    assert _pdf_proposed_bodies(rt)["5"] == _PROVISION


def test_body_trimmed_at_appended_voimaantulo() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION + " Tämä laki tulee voimaan päivänä kuuta 20 .")
    assert _pdf_proposed_bodies(rt)["5"] == _PROVISION


def test_genuine_voimaantulo_section_is_not_over_trimmed() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A §-body that IS a commencement provision starts with the phrase (no preceding in-body
    # period) — it must be kept whole, never truncated to empty.
    voim = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020 ja on voimassa toistaiseksi."
    assert _pdf_proposed_bodies(_body_reading(voim))["5"] == voim


def test_genuine_presidentin_reference_is_not_trimmed() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A body's own lowercase "tasavallan presidentin asetuksella" is not the signature block.
    body = "Tarkempia säännöksiä annetaan tasavallan presidentin asetuksella tarvittaessa."
    assert _pdf_proposed_bodies(_body_reading(body))["5"] == body


def test_scattered_signature_date_lets_split_word_rejoin() -> None:
    from lawvm.tools.fi_he_ir_compare import _flatten_reading_text

    # The centered signature date scattered INTO an end-of-line hyphenation ("työnan-¬<date>¬
    # tajanaan") must be stripped before de-hyphenation so the word fuses to "työnantajanaan".
    raw = "hänen työnan￾Helsingissä 9 päivänä lokakuuta 1992 \r\ntajanaan oleva laivanvarustaja"
    flat = _flatten_reading_text(raw)
    assert "työnantajanaan" in flat
    assert "Helsingissä" not in flat


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

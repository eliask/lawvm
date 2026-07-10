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
# Design B: bills-heading anchor widens the bound for a mega-johtolause,       #
# while fencing the perustelut out (no op_extra regression).                  #
# --------------------------------------------------------------------------- #


def _overlong_provision_list(min_chars: int) -> str:
    """A provision enumeration long enough to push "seuraavasti:" past ``min_chars``."""
    parts = [f"{i} {_SEC}:n {i % 4 + 1} momentti" for i in range(1, 400)]
    listing = ", ".join(parts)
    assert len(listing) > min_chars, len(listing)
    return listing


def test_mega_johtolause_past_narrow_bound_captured_after_bills_heading() -> None:
    # A STRUCTURAL mega-johtolause enumerates hundreds of provisions, so its "seuraavasti:"
    # terminator lands WELL past the narrow 2400-char default. Anchored at the genuine
    # "Lakiehdotukset N. Laki" bills heading (perustelut fenced out), the head→terminator
    # bound is widened so the whole clause is captured instead of dropped (all-ops-missing).
    listing = _overlong_provision_list(2400)
    clause = f"muutetaan testilain (123/2020) {listing} seuraavasti:"
    assert len(clause) > 2400
    text = (
        "YLEISPERUSTELUT jossa käsitellään ehdotusta laajasti. "
        "Lakiehdotukset 1. Laki testilain muuttamisesta " + clause + f" 5 {_SEC} Uusi teksti."
    )
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1
    assert spans[0].startswith("muutetaan testilain (123/2020)")
    assert spans[0].rstrip().endswith("seuraavasti:")


def test_mega_johtolause_stays_dropped_without_bills_heading() -> None:
    # The widened bound is GATED on the bills heading. The identical overlong clause with NO
    # "Lakiehdotukset N. Laki" heading keeps the narrow default bound, so its distant
    # terminator is (correctly) not reached — a terminator-less / perustelut head can never
    # grab an arbitrarily distant "seuraavasti:".
    listing = _overlong_provision_list(2400)
    clause = f"muutetaan testilain (123/2020) {listing} seuraavasti: Uusi teksti."
    assert extract_enacting_clause_spans(clause) == []


def test_perustelut_false_head_before_bills_heading_not_captured() -> None:
    # A detailed-perustelut sentence carries the SAME amendment-verb + citation + "§" +
    # "seuraavasti" signature as an enacting clause. It sits BEFORE the "Lakiehdotukset N.
    # Laki" bills heading, so anchoring the scan there fences it out — only the genuine
    # directive after the heading is captured, so it contributes no op_extra.
    perustelut = (
        "Yksityiskohtaisissa perusteluissa muutetaan esityksen mukaan viittauslakia "
        f"(999/1999) sen 3 {_SEC} seuraavasti: kuvaillaan muutosta. "
    )
    genuine = f"muutetaan testilain (123/2020) 5 {_SEC} seuraavasti: Uusi 5 §."
    text = perustelut + "Lakiehdotukset 1. Laki testilain muuttamisesta " + genuine
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]
    assert "(999/1999)" not in spans[0]


def test_stray_lakiehdotukset_word_not_anchored_without_numbered_bill() -> None:
    # A capitalized "Lakiehdotukset" word in prose (no numbered "N. Laki" follow) must NOT
    # be taken as the bills heading — otherwise it would wrongly advance the scan start past a
    # genuine directive. Here the only "Lakiehdotukset" is a stray prose word AFTER the
    # genuine bill, so the scan must still open at the region start and capture the bill.
    text = (
        "Lakiehdotukset muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: Uusi 5 §. "
        "Lakiehdotukset ovat valiokunnan mukaan perusteltuja."
    )
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]


def test_bills_heading_anchors_on_new_law_compound_title() -> None:
    # A modern omnibus HE's first bill is often a NEW law with a single-word COMPOUND title
    # ("Lakiehdotukset 1. Yleistukilaki …"), not an amend-bill "N. Laki …".  The bills-heading
    # anchor must still fire so the widened bound is applied — otherwise a LATER bill's long
    # chapter-organized johtolause overflows the narrow default and its whole op-set is dropped
    # (the HE 112/2025 1290/2002 op_missing pattern).  The overlong amend johtolause here sits
    # AFTER the "1. Yleistukilaki" new-law bill and is captured because the heading anchors.
    listing = _overlong_provision_list(2400)
    clause = f"kumotaan työttömyysturvalain (1290/2002) {listing} seuraavasti:"
    assert len(clause) > 2400
    text = (
        "YLEISPERUSTELUT jossa käsitellään ehdotusta laajasti. "
        "Lakiehdotukset 1. Yleistukilaki Eduskunnan päätöksen mukaisesti säädetään: "
        "1 luku Yleiset säännökset 1 § Lain tarkoitus. "
        "2. Laki työttömyysturvalain muuttamisesta " + clause + f" 5 {_SEC} Uusi teksti."
    )
    spans = extract_enacting_clause_spans(text)
    assert any(s.startswith("kumotaan työttömyysturvalain (1290/2002)") for s in spans)


def test_chapter_path_symmetric_across_xml_and_pdf_input_shapes() -> None:
    # SYMMETRY: a "N luvun M §" chapter-organized clause must lower to the SAME luku_N/M ref
    # whether it arrives as XML enacting-clause text ("Eduskunnan päätöksen mukaisesti
    # muutetaan …") or as a bare PDF johtolause span ("muutetaan …").  Both flow through the
    # shared _parse_one_clause, so the chapter path is built identically — the PDF side does
    # not lose the "6 luvun" token.
    from lawvm.finland.he_branch_parser import _parse_one_clause

    xml_style = (
        "Eduskunnan päätöksen mukaisesti muutetaan työttömyysturvalain (1290/2002) "
        f"6 luvun 3 {_SEC} seuraavasti:"
    )
    pdf_style = (
        f"muutetaan työttömyysturvalain (1290/2002) 6 luvun 3 {_SEC} seuraavasti:"
    )
    xml_ops, _ = _parse_one_clause(xml_style, 0, "HE 1/2025 vp", "fi/he/2025/1")
    pdf_ops, _ = _parse_one_clause(pdf_style, 0, "HE 1/2025 vp", "fi/he/2025/1")
    xml_refs = [o.target_provision_ref for o in xml_ops]
    pdf_refs = [o.target_provision_ref for o in pdf_ops]
    assert xml_refs == pdf_refs
    assert "1290/2002/luku_6/3" in xml_refs


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

#: ``_pdf_proposed_bodies`` keys each body by (governing-statute-id, section label); the
#: single-bill ``_BODY_CLAUSE`` amends testilaki (123/2020), so its 5 § body lands here.
_BODY_KEY = ("123/2020", "5")


def _body_reading(body: str, *, tail: str = "") -> str:
    """Lakiehdotus reading text carrying one clause and 5 §'s body (+ trailing furniture)."""
    return (
        "Hallituksen esitys eduskunnalle laiksi testilain muuttamisesta "
        "YLEISPERUSTELUT jossa säädetään monista asioista. Lakiehdotukset "
        "1. Laki testilain muuttamisesta " + _BODY_CLAUSE + f" 5 {_SEC} {body} {tail}"
    )


def test_body_clean_is_unchanged() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    assert _pdf_proposed_bodies(_body_reading(_PROVISION))[_BODY_KEY] == _PROVISION


def test_body_starts_at_lakiehdotus_not_preceding_perustelut_mention() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The detailed perustelut discuss "5 §" with justification prose BEFORE the bill; the
    # genuine provision body follows the johtolause. The section body must START at the
    # LAKIEHDOTUS occurrence, never at the earlier perustelut mention (a wrong-START
    # over-capture would prepend the justification prose to the 5 § body).
    perustelut = (
        f"YKSITYISKOHTAISET PERUSTELUT 5 {_SEC}. Pykalassa ehdotetaan saadettavaksi "
        "vaara perusteluprosaa jota ei saa lukea pykalan sisalloksi."
    )
    rt = (
        "Hallituksen esitys eduskunnalle laiksi testilain muuttamisesta "
        + perustelut
        + " Lakiehdotukset 1. Laki testilain muuttamisesta "
        + _BODY_CLAUSE
        + f" 5 {_SEC} {_PROVISION}"
    )
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_no_enacting_clause_yields_no_body_not_perustelut_prose() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A treaty / new-statute HE whose PDF carries NO amendment johtolause: the detailed
    # perustelut still carry "N §" mentions. With no genuine enacting-clause region the
    # extractor must emit NO body (the payload stage then defers), never a perustelut-prose
    # body opened at the wrong start (the HE 59/1997 §1 / HE 100/2003 §3 defect).
    rt = (
        "Hallituksen esitys eduskunnalle laiksi sopimuksen hyvaksymisesta "
        f"YKSITYISKOHTAISET PERUSTELUT 1 {_SEC}. Pykalassa maaritellaan lain "
        f"soveltamisala ja paljon perusteluprosaa. 3 {_SEC}. Voimaantulo prosaa."
    )
    assert _pdf_proposed_bodies(rt) == {}


def test_genuine_body_is_never_emptied() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A valid single-bill clause + its 5 § body: the body must be present and non-empty
    # (the wrong-START guard must never defer a body that a genuine enacting clause anchors).
    bodies = _pdf_proposed_bodies(_body_reading(_PROVISION))
    assert bodies.get(_BODY_KEY)
    assert bodies[_BODY_KEY] == _PROVISION


def test_body_trimmed_at_spaced_emdash_divider() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A SPACED em-dash divider run ("— — — —", a Finnish statute section-divider / omission
    # marker) after the provision's last sentence is trailing furniture, NOT body text. The
    # contiguous "———" trailer guard misses the spaced form, so it used to bleed into the body;
    # the spaced-dash-run alternative now trims it (the body's own text is kept intact).
    rt = _body_reading(_PROVISION, tail="— — — — Tämä laki tulee voimaan päivänä kuuta 20 .")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_body_with_single_in_sentence_dash_is_not_trimmed() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # Conservative guard: a lone in-sentence em-dash ("kansalaiselle — myös ulkomaalaiselle —")
    # is NOT a divider run (needs 3+ spaced dashes) and must never truncate the body.
    body = "Etuus myönnetään kansalaiselle — myös ulkomaalaiselle — yhtäläisin perustein."
    assert _pdf_proposed_bodies(_body_reading(body))[_BODY_KEY] == body


def test_body_trimmed_at_signature_block() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION, tail="Tasavallan Presidentti MATTI MEIKÄLÄINEN Ministeri Aku Ankka")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_body_trimmed_at_dash_running_header() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The "<YEAR> vp - HE <NUM> <page>" running header the lakiehdotus reprint carries.
    rt = _body_reading(_PROVISION, tail="1992 vp - HE 231 3")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_body_trimmed_at_trailing_page_number() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A lone page number appended after the provision's own sentence-ending period.
    rt = _body_reading(_PROVISION, tail="40")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_body_does_not_bleed_into_next_law_heading() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION, tail="2. Laki toisen lain muuttamisesta ja tarkennuksista")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_body_trimmed_at_appended_voimaantulo() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    rt = _body_reading(_PROVISION + " Tämä laki tulee voimaan päivänä kuuta 20 .")
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == _PROVISION


def test_genuine_voimaantulo_section_is_not_over_trimmed() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A §-body that IS a commencement provision starts with the phrase (no preceding in-body
    # period) — it must be kept whole, never truncated to empty.
    voim = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020 ja on voimassa toistaiseksi."
    assert _pdf_proposed_bodies(_body_reading(voim))[_BODY_KEY] == voim


def test_genuine_presidentin_reference_is_not_trimmed() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A body's own lowercase "tasavallan presidentin asetuksella" is not the signature block.
    body = "Tarkempia säännöksiä annetaan tasavallan presidentin asetuksella tarvittaessa."
    assert _pdf_proposed_bodies(_body_reading(body))[_BODY_KEY] == body


def test_scattered_signature_date_lets_split_word_rejoin() -> None:
    from lawvm.tools.fi_he_ir_compare import _flatten_reading_text

    # The centered signature date scattered INTO an end-of-line hyphenation ("työnan-¬<date>¬
    # tajanaan") must be stripped before de-hyphenation so the word fuses to "työnantajanaan".
    raw = "hänen työnan￾Helsingissä 9 päivänä lokakuuta 1992 \r\ntajanaan oleva laivanvarustaja"
    flat = _flatten_reading_text(raw)
    assert "työnantajanaan" in flat
    assert "Helsingissä" not in flat


# --------------------------------------------------------------------------- #
# next-section TITLE over-capture — the otsikko before the next "N §" bled in.  #
# --------------------------------------------------------------------------- #

# A two-section bill: 5 § body, then the NEXT section's TITLE heading (otsikko), then 6 §.
# In reading order the otsikko precedes "6 §", so a body bounded at the next "N §" NUMBER
# over-captures that title (HE 224/2010: "…1 luvun 1 §:ssä. Poliisimiehen virka-asemaan
# liittyvät säännökset" / "…virkamieslaissa. Erinäiset säännökset").
_TWO_SEC_CLAUSE = (
    f"Eduskunnan päätöksen mukaisesti muutetaan testilain (123/2020) 5 {_SEC} ja 6 {_SEC} "
    "seuraavasti:"
)
_SEC6_KEY = ("123/2020", "6")
_BODY6 = "Kuudennen pykälän oma sisältö joka on niin ikään riittävän pitkä vertailua varten."


def _two_section_reading(body5: str, title: str, body6: str = _BODY6) -> str:
    """Lakiehdotus reading text: 5 §'s body, an optional next-section title, then 6 §'s body."""
    return (
        "Hallituksen esitys eduskunnalle laiksi testilain muuttamisesta "
        "YLEISPERUSTELUT jossa säädetään monista asioista. Lakiehdotukset "
        "1. Laki testilain muuttamisesta " + _TWO_SEC_CLAUSE
        + f" 5 {_SEC} {body5} {title} 6 {_SEC} {body6}"
    )


def test_body_trimmed_at_next_section_title_heading() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The trailing "Erinäiset säännökset" is the NEXT section's otsikko (it precedes "6 §"),
    # NOT part of 5 §'s body — it must be trimmed off, and 6 §'s own body kept intact.
    bodies = _pdf_proposed_bodies(_two_section_reading(_PROVISION, "Erinäiset säännökset"))
    assert bodies[_BODY_KEY] == _PROVISION
    assert bodies[_SEC6_KEY] == _BODY6


def test_body_trimmed_at_multiword_next_section_title() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The real HE 224/2010 §2 case: a multi-word jakso heading before the next "N §".
    title = "Poliisimiehen virka-asemaan liittyvät säännökset"
    bodies = _pdf_proposed_bodies(_two_section_reading(_PROVISION, title))
    assert bodies[_BODY_KEY] == _PROVISION
    assert title not in bodies[_BODY_KEY]


def test_body_with_no_next_section_title_is_unchanged() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A body abutting the next "N §" with NO intervening title (title == "") is left whole:
    # nothing follows its final period, so the trim never fires.
    bodies = _pdf_proposed_bodies(_two_section_reading(_PROVISION, ""))
    assert bodies[_BODY_KEY] == _PROVISION
    assert bodies[_SEC6_KEY] == _BODY6


def test_genuine_trailing_sentence_is_not_trimmed_as_title() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A body whose LAST sentence is short + Capitalized but ends in a period is genuine body
    # prose, NOT a title — it must never be truncated mid-body. Here "Se on tärkeää." trails
    # a period, so the last-period boundary leaves nothing title-shaped to trim.
    body5 = "Ensimmäinen virke pykälän sisällöstä on tässä. Se on tärkeää."
    bodies = _pdf_proposed_bodies(_two_section_reading(body5, "Voimaantulo"))
    assert bodies[_BODY_KEY] == body5


def test_next_section_title_with_digit_left_under_trimmed_not_cut() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # Precision-first: a trailing phrase carrying a digit or ":" is NOT treated as a title
    # (it could be a genuine cross-reference), so it is left under-trimmed — never mis-cut.
    body5 = _PROVISION
    bodies = _pdf_proposed_bodies(_two_section_reading(body5, "Otsikko 3 luvusta"))
    # Body retains its own final sentence (never emptied / truncated mid-sentence).
    assert bodies[_BODY_KEY].startswith(_PROVISION)


def test_looks_like_section_title_discriminator() -> None:
    from lawvm.tools.fi_he_ir_compare import _looks_like_section_title

    assert _looks_like_section_title("Erinäiset säännökset")
    assert _looks_like_section_title("Voimaantulo")
    assert _looks_like_section_title("Poliisimiehen virka-asemaan liittyvät säännökset")
    # Sentence (period), list-intro (colon), §-reference, digit, lowercase start → not a title.
    assert not _looks_like_section_title("Tämä on kokonainen virke.")
    assert not _looks_like_section_title("seuraavat kohdat:")
    assert not _looks_like_section_title("Katso 5 § tarkemmin")
    assert not _looks_like_section_title("noudatetaan soveltuvin osin")
    assert not _looks_like_section_title("")


# --------------------------------------------------------------------------- #
# cross-bill body scoping — an omnibus HE reuses a section number across bills  #
# --------------------------------------------------------------------------- #

# Two bills that BOTH carry a "5 §", each with a DISTINCT body: an omnibus HE routinely
# reuses a section number across its bills. A bare-``label`` body key first-wins-collapsed
# the two into one, so an op matched to bill A's ``.../5`` was compared against bill B's
# "5 §" body — a spurious payload_mismatch. Each body must be scoped to its OWN bill.
# Bill A's body is realistically long (a real bill runs many provisions) so the two johtolauses
# are more than one clause window apart — the regime real omnibus HEs sit in; the two "5 §"
# bodies are then both captured, and the fix must key them distinctly rather than collapse them.
_BODY_A = (
    "Ensimmaisen lain viidennen pykalan sisalto on tassa ainutlaatuinen alfasana. "
    + "Tassa pykalassa saadetaan menettelysta ja sen soveltamisesta yksityiskohtaisesti. " * 40
).strip()
_BODY_B = "Toisen lain viidennen pykalan sisalto on tassa ainutlaatuinen beetasana ja riittava pituus."


def _two_bill_xml() -> bytes:
    """Synthetic omnibus HE: bill A amends 111/2011 §5=alfa, bill B amends 222/2022 §5=beeta."""
    def _bill(eid: str, cite: str, body: str) -> str:
        return (
            f'<hcontainer name="bill" eId="{eid}">'
            f'<hcontainer name="enactingClause"><p>Eduskunnan paatoksen mukaisesti '
            f"muutetaan testilain {cite} 5 {_SEC} seuraavasti:</p></hcontainer>"
            f'<hcontainer name="statuteProvisionsWrapper">'
            f'<section eId="{eid}__sec_5"><p>5 {_SEC} {body}</p></section>'
            "</hcontainer></hcontainer>"
        )

    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><mainBody>"
        '<hcontainer name="bills">'
        + _bill("bill_1", "(111/2011)", _BODY_A)
        + _bill("bill_2", "(222/2022)", _BODY_B)
        + "</hcontainer></mainBody></act></akomaNtoso>"
    ).encode("utf-8")


def _two_bill_reading() -> str:
    return (
        "Hallituksen esitys eduskunnalle laeiksi testilakien muuttamisesta "
        "YLEISPERUSTELUT jossa saadetaan monista asioista. Lakiehdotukset "
        "1. Laki ensimmaisen testilain muuttamisesta "
        f"Eduskunnan paatoksen mukaisesti muutetaan testilain (111/2011) 5 {_SEC} seuraavasti: "
        f"5 {_SEC} {_BODY_A} "
        "2. Laki toisen testilain muuttamisesta "
        f"Eduskunnan paatoksen mukaisesti muutetaan testilain (222/2022) 5 {_SEC} seuraavasti: "
        f"5 {_SEC} {_BODY_B}"
    )


def test_cross_bill_section_reuse_pairs_each_bills_own_body() -> None:
    # The core fix: bill A's "5 §" body and bill B's "5 §" body are keyed by their bill's
    # (statute-id, label), NOT bare label — so each op pairs its OWN bill's body.
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies, _xml_proposed_bodies

    pdf = _pdf_proposed_bodies(_two_bill_reading())
    xml = _xml_proposed_bodies(_two_bill_xml())
    for bodies in (pdf, xml):
        assert bodies[("111/2011", "5")] == _BODY_A
        assert bodies[("222/2022", "5")] == _BODY_B
        # NEVER first-wins-collapsed onto a single bare "5" key that aliases the two bills.
        assert "alfasana" in bodies[("111/2011", "5")]
        assert "beetasana" not in bodies[("111/2011", "5")]
        assert "beetasana" in bodies[("222/2022", "5")]


def test_cross_bill_section_reuse_is_exact_not_a_payload_mismatch() -> None:
    # End to end: both bills' §5 ops match and each pairs its OWN body → zero payload_mismatch,
    # EXACT. Under the bare-label bug bill A's op compared against bill B's body → a mismatch.
    r = compare_he(_two_bill_xml(), _two_bill_reading(), he_year=2014, he_number=19)
    assert r.compare_status == "compared"
    matched = {d.target_ref for d in r.divergences if d.kind == "matched"}
    assert {"111/2011/5", "222/2022/5"} <= matched
    assert [d for d in r.divergences if d.kind == "payload_mismatch"] == []
    assert r.exact_equivalent


def test_single_bill_body_scoping_is_unchanged() -> None:
    # A single-bill HE keeps exactly its one (statute, label) body and compares exact — the
    # scoping change must not perturb the common single-bill case.
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    bodies = _pdf_proposed_bodies(_body_reading(_PROVISION))
    assert bodies == {_BODY_KEY: _PROVISION}
    r = compare_he(_he_xml(_CLAUSE, bodies={"5": f"5 {_SEC} {_PROVISION}"}), _pdf_page(_CLAUSE, body5=_PROVISION), he_year=2020, he_number=7)
    assert r.compare_status == "compared"
    assert [d for d in r.divergences if d.kind == "payload_mismatch"] == []


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


# --------------------------------------------------------------------------- #
# PDF backend unavailability must never masquerade as a clean/empty result.    #
# --------------------------------------------------------------------------- #


def test_pdfium_text_layer_raises_when_backend_missing() -> None:
    # A missing pypdfium2 must surface as the distinct HEReaderUnavailableError — NOT an empty
    # "" text layer (which would read downstream as "0 residual / clean") and NOT a bare
    # uncaught ModuleNotFoundError. Simulate absence by nulling the module in sys.modules.
    import sys

    import pytest

    from lawvm.tools.fi_he_ir_compare import HEReaderUnavailableError, _pdfium_text_layer

    orig = sys.modules.get("pypdfium2", None)
    sys.modules["pypdfium2"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(HEReaderUnavailableError):
            _pdfium_text_layer(b"%PDF-1.4 fake bytes", 5)
    finally:
        if orig is None:
            sys.modules.pop("pypdfium2", None)
        else:
            sys.modules["pypdfium2"] = orig


def test_missing_backend_propagates_not_typed_as_clean_error(monkeypatch) -> None:
    # End to end: on a backend-less machine compare_he_from_farchive must PROPAGATE the
    # unavailability (raise HEReaderUnavailableError) rather than return a HECompareResult with
    # a benign non-compared status ("error" / "pdf_no_clause") that an aggregate sweep would
    # read as clean. Absence of a witness must never read as absence of divergence.
    import sys

    import farchive
    import pytest

    from lawvm.tools.fi_he_ir_compare import HEReaderUnavailableError, compare_he_from_farchive

    class _FakeFarchive:
        def __init__(self, *_a, **_k) -> None:
            pass

        def get(self, key: str) -> bytes:
            if key.endswith("main.xml"):
                return _he_xml(_CLAUSE)  # a real amendment HE with ops to compare
            if key.endswith("main.pdf"):
                return b"%PDF-1.4 small born-digital fake"
            return b""

        def close(self) -> None:
            pass

    monkeypatch.setattr(farchive, "Farchive", _FakeFarchive)
    monkeypatch.setitem(sys.modules, "pypdfium2", None)  # backend unavailable
    with pytest.raises(HEReaderUnavailableError):
        compare_he_from_farchive("ignored.farchive", 2020, 99)

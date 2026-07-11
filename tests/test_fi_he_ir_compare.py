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
    _PDF_CONSEQUENTIAL_REPEAL,
    _PDF_OUT_OF_SCOPE_STATUTE,
    HECompareResult,
    HEFlatOp,
    _asetusluonnos_region,
    _flatten_reading_text,
    _lakiehdotus_region,
    _pdf_proposed_bodies,
    _reclassify_out_of_scope_second_bills,
    _repair_slash_as_one_cites,
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


def test_extract_old_format_two_digit_year_cite() -> None:
    # The pre-2000 typographic convention prints the statute year as TWO digits
    # ("(178/76)" = statute 178, year 1976). The whole 1992–1999 pdf_no_clause stratum
    # used it, and the 4-digit-only cite anchor never matched → the clause was invisible.
    # The 2-digit form is now admitted as the span-detection anchor.
    clause = "muutetaan testilain (178/76) 11 " + _SEC + " seuraavasti: Uusi 11 §."
    spans = extract_enacting_clause_spans("Lakiehdotukset " + clause)
    assert len(spans) == 1
    assert "(178/76)" in spans[0]


def test_extract_two_digit_cite_body_prose_still_rejected() -> None:
    # The 2-digit widening must NOT relax the enacting-clause discipline. A body-prose
    # sentence carrying an amendment verb + a 2-digit cite but NO "§" provision list before
    # the terminator is not a directive and yields no span (the "§" discriminator holds
    # identically for the widened cite form).
    prose = (
        "Ehdotuksessa muutetaan kansanelakelain (347/56) rakennetta merkittavasti "
        "seuraavasti: ensin ja sitten."
    )
    assert extract_enacting_clause_spans(prose) == []


def test_lakiehdotus_region_skips_toc_leader_entry() -> None:
    # An old-format HE opens with a sisällysluettelo listing "Rinnakkaistekstit . . . . 41"
    # (dotted leaders to the page number). That front-matter entry matched the appendix-end
    # marker and truncated the WHOLE bill body away (4 of the 14 pdf_no_clause HEs). A TOC
    # entry — label immediately trailed by a dotted-leader run — must NOT cut the region;
    # only a genuine body heading (followed by parallel-text content) does.
    toc = "Sisallys Lakiehdotukset 3 Rinnakkaistekstit . . . . . . . . . . . 41 "
    body = "Lakiehdotukset muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: Uusi 5 §."
    flat = _flatten_reading_text(toc + body)
    region = _lakiehdotus_region(flat)
    assert "muutetaan testilain (123/2020)" in region
    spans = extract_enacting_clause_spans(toc + body)
    assert len(spans) == 1
    assert "(123/2020)" in spans[0]


# --------------------------------------------------------------------------- #
# pdf_no_clause RECOVERY fallback (reading-fidelity repairs, gated for 0 collateral) #
# --------------------------------------------------------------------------- #


def test_repair_slash_as_one_cites_reconstructs_and_is_safe() -> None:
    # A text layer that renders the "/" of a statute citation as "1" ("(1505/1992)" →
    # "(150511992)") defeated the cite anchor and the whole enacting clause was invisible
    # (HE 69/1997, 82/1997, 108/2000, 73/1996). The repair reconstructs the slash, anchoring
    # the YEAR to the token's last digits so the split is unambiguous.
    assert _repair_slash_as_one_cites("(150511992)") == "(1505/1992)"  # 4-digit year
    assert _repair_slash_as_one_cites("(54311994)") == "(543/1994)"
    assert _repair_slash_as_one_cites("(704175)") == "(704/75)"  # pre-2000 2-digit year
    # SAFETY: a clean citation is untouched, and a parenthesised number whose trailing digits
    # are not a plausible year (no "1" slash-surrogate before a 1600–2099 / 2-digit tail) is left
    # alone rather than mangled into a phantom citation.
    assert _repair_slash_as_one_cites("(1471/1994)") == "(1471/1994)"
    assert _repair_slash_as_one_cites("(123456)") == "(123456)"


def test_slash_as_one_cite_repair_gated_to_fallback() -> None:
    # 0-COLLATERAL discipline: the repair fires ONLY on the aggressive fallback pass, so a
    # currently-detected HE's flattened text is byte-identical (the mangled token is left as-is
    # on the normal path). The aggressive pass reconstructs it.
    text = "muutetaan testilain (150511992) 5 " + _SEC + " seuraavasti: Uusi 5 §."
    assert "(150511992)" in _flatten_reading_text(text)  # normal: unchanged
    assert "(150511992)" not in _flatten_reading_text(text, aggressive=True)
    assert "(1505/1992)" in _flatten_reading_text(text, aggressive=True)


def test_slash_as_one_cite_he_recovered_via_fallback_and_matches() -> None:
    # END TO END: the XML johtolause carries the clean cite "(1505/1992)"; the PDF text layer
    # mangled it to "(150511992)". The normal path finds NO clause → the fallback repairs the
    # cite → the recovered op matches the XML op EXACTLY (no op_missing flood).
    clause = "Eduskunnan päätöksen mukaisesti muutetaan testilain (1505/1992) 5 " + _SEC + " seuraavasti:"
    xml = _he_xml(clause)
    pdf_ok = _pdf_page(clause)
    pdf_mangled = pdf_ok.replace("(1505/1992)", "(150511992)")
    # sanity: without the mangle it compares; the mangle alone would strand it
    assert compare_he(xml, pdf_ok, he_year=1997, he_number=69).compare_status == "compared"
    r = compare_he(xml, pdf_mangled, he_year=1997, he_number=69)
    assert r.compare_status == "compared"
    assert r.counts["op_missing_in_pdf"] == 0
    assert r.exact_equivalent


def test_detected_he_not_perturbed_by_slash_fallback() -> None:
    # 0-COLLATERAL proof: an HE whose real clause is detected NORMALLY must return the normal
    # result even though a mangled cite sits in its perustelut that the fallback WOULD repair
    # into a phantom op. Gating the recoveries to the no-clause fallback prevents that.
    stray = " Perusteluissa muutetaan toisen lain (99911988) 3 " + _SEC + " seuraavasti: x."
    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, _pdf_page(_CLAUSE) + stray, he_year=2020, he_number=99)
    assert r.compare_status == "compared"
    assert not any("999/1988" in d.target_ref for d in r.divergences)


def test_toc_leader_preceding_appendix_entry_kept_only_in_fallback() -> None:
    # An old-format HE's TOC lists "… muuttamisesta . . . . 37 LIITE Rinnakkaistekstit 1. Laki …"
    # — the dotted leader trails the bill TITLE, so the appendix marker "Rinnakkaistekstit"
    # is NOT immediately followed by a leader and the normal after-leader check misses it,
    # cutting the whole bill body away (HE 47/1999, 73/1996, 82/1997). The PRECEDING leader
    # convicts it as a TOC entry — but only on the aggressive fallback (0 collateral otherwise).
    toc = "Sisallys 1. Laki testilain muuttamisesta . . . . . . . . 37 LIITE Rinnakkaistekstit "
    body = "1. Laki Eduskunnan päätöksen mukaisesti muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: Uusi 5 §."
    flat = _flatten_reading_text(toc + body)
    assert "muutetaan testilain (123/2020)" not in _lakiehdotus_region(flat)  # normal: cut
    assert "muutetaan testilain (123/2020)" in _lakiehdotus_region(flat, aggressive=True)


def test_annex_and_chapter_target_recovered_only_in_fallback() -> None:
    # A WHOLE-ANNEX / WHOLE-CHAPTER amendment lists no "§" ("… lain (1471/1994) liite …
    # seuraavasti:", HE 151/2013; "… uusi 4 a luku seuraavasti:", HE 101/2020), so the §-only
    # provision guard dropped the whole clause. The nominative liite/luku target is admitted as
    # an equivalent marker ONLY on the aggressive fallback.
    annex = "Lakiehdotukset muutetaan testilain (1471/1994) liite, sellaisena kuin se on laissa 1235/2011, seuraavasti:"
    chapter = "Lakiehdotukset lisätään tartuntatautilakiin (1227/2016) väliaikaisesti uusi 4 a luku seuraavasti:"
    assert extract_enacting_clause_spans(annex) == []
    assert extract_enacting_clause_spans(chapter) == []
    assert len(extract_enacting_clause_spans(annex, aggressive=True)) == 1
    assert len(extract_enacting_clause_spans(chapter, aggressive=True)) == 1


# --------------------------------------------------------------------------- #
# draft-decree (asetusluonnos) recovery: directives sit AFTER the appendix     #
# --------------------------------------------------------------------------- #

# A law bill, its commencement, a spurious rinnakkaistekstit reprint of that bill, then an
# "Asetusluonnokset" section whose decree carries the SAME amendment grammar under the
# decree-specific "Valtioneuvoston päätöksen mukaisesti" enactment formula.
_LAW_BILL = (
    "Lakiehdotukset 1. Laki testilain muuttamisesta Eduskunnan päätöksen mukaisesti "
    f"muutetaan testilain (123/2020) 5 {_SEC} seuraavasti: 5 {_SEC} Uusi lain teksti. "
    "——— Tämä laki tulee voimaan päivänä kuuta 20 ."
)
_RINNAKKAIS_REPRINT = (
    " Liitteet Rinnakkaistekstit Voimassa oleva laki Ehdotus "
    f"muutetaan testilain (123/2020) 5 {_SEC} seuraavasti: vanha rinnakkaisteksti."
)
_DECREE_BLOCK = (
    " Asetusluonnokset Valtioneuvoston asetus testiasetuksen muuttamisesta "
    f"Valtioneuvoston päätöksen mukaisesti muutetaan testiasetuksen (456/2019) 3 {_SEC} "
    f"seuraavasti: 3 {_SEC} Uusi asetuksen teksti joka on riittävän pitkä vertailua varten. "
    "——— Tämä asetus tulee voimaan päivänä kuuta 20 ."
)


def test_asetusluonnos_decree_recovered_after_rinnakkaistekstit() -> None:
    # The draft decree sits AFTER the rinnakkaistekstit appendix; its genuine amendment
    # directive must be recovered (previously dropped with the appendix as op_missing),
    # while the appendix's spurious REPRINT of the law bill stays excluded.
    text = _LAW_BILL + _RINNAKKAIS_REPRINT + _DECREE_BLOCK
    spans = extract_enacting_clause_spans(text)
    cites = [c for s in spans for c in ("(123/2020)", "(456/2019)") if c in s]
    assert "(456/2019)" in cites  # decree directive recovered
    assert cites.count("(123/2020)") == 1  # law bill once, NOT the appendix reprint


def test_asetusluonnos_region_starts_at_amendment_decree_not_appendix() -> None:
    # The re-appended region is exactly the decree block (from its amendment johtolause),
    # never the intervening rinnakkaistekstit reprint.
    flat = _flatten_reading_text(_LAW_BILL + _RINNAKKAIS_REPRINT + _DECREE_BLOCK)
    from lawvm.tools.fi_he_ir_compare import _LAKIEHDOTUS_END_RE

    cut = _LAKIEHDOTUS_END_RE.search(flat)
    assert cut is not None
    region = _asetusluonnos_region(flat, cut.end())
    assert region.startswith("Valtioneuvoston päätöksen mukaisesti muutetaan")
    assert "(456/2019)" in region
    assert "rinnakkaisteksti" not in region  # the appendix reprint is not pulled in


def test_asetusluonnos_saadetaan_only_section_not_reopened() -> None:
    # A section whose only decree is a NEW enactment ("… päätöksen mukaisesti säädetään …
    # nojalla:") carries no amendment op to recover, so the region is NOT re-opened — this
    # keeps a new-decree's provision bodies (and any trailing reprint) out of the scan.
    new_decree = (
        " Asetusluonnokset Valtioneuvoston asetus uudesta asiasta Valtioneuvoston päätöksen "
        f"mukaisesti säädetään testilain (789/2018) 2 {_SEC}:n nojalla: 1 {_SEC} Uusi säännös."
    )
    flat = _flatten_reading_text(_LAW_BILL + _RINNAKKAIS_REPRINT + new_decree)
    from lawvm.tools.fi_he_ir_compare import _LAKIEHDOTUS_END_RE

    cut = _LAKIEHDOTUS_END_RE.search(flat)
    assert cut is not None
    assert _asetusluonnos_region(flat, cut.end()) == ""
    # and the whole region collapses to the pre-appendix text (unchanged behaviour)
    assert _lakiehdotus_region(flat) == flat[: cut.start()]
    assert "(789/2018)" not in "".join(extract_enacting_clause_spans(flat))


def test_decree_body_sheds_asetus_commencement_clause() -> None:
    # A recovered decree §-body must trim its "Tämä asetus tulee voimaan …" commencement
    # tail exactly as a law body trims "Tämä laki tulee voimaan …" (the XML keeps it a
    # separate section), so the recovered decree op does not surface a spurious payload_mismatch.
    bodies = _pdf_proposed_bodies(_LAW_BILL + _RINNAKKAIS_REPRINT + _DECREE_BLOCK)
    key = ("456/2019", "3")
    assert key in bodies
    assert "tulee voimaan" not in bodies[key]
    assert "Uusi asetuksen teksti" in bodies[key]


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


def test_body_prose_korvataan_crossreference_is_not_a_clause() -> None:
    # "korvataan" is the one amendment-head verb that is ALSO pervasive body prose ("is
    # reimbursed" vs the directive "is replaced"). A section-body cross-reference
    # "... kustannukset korvataan <lain> (767/2005) 10 luvun 7 §:n perusteella; ..." carries
    # the head-verb + citation + "§" + a downstream "seuraavasti:" signature, so it MIMICS an
    # enacting clause and lowered phantom ops on the merely-referenced statute (HE 103/2013:
    # 12 phantom ops on vankeuslaki 767/2005, a law it never amends). Absent the enactment
    # formula, a "korvataan" head is body prose and yields NO span.
    # The body-prose "korvataan (767/2005)" sits DEEP in the section body — far past the bill's
    # own enactment formula (as in real HEs, thousands of chars downstream), so the formula does
    # not corroborate it.
    filler = ("Tässä pykälässä säädetään sairaanhoidon kustannusten korvaamisesta ja "
              "matkakustannuksista sen mukaan kuin jäljempänä tarkemmin luetellaan. ") * 8
    text = (
        "Lakiehdotukset 1. Laki testilain muuttamisesta Eduskunnan päätöksen mukaisesti "
        "muutetaan testilain (123/2020) 5 " + _SEC + " seuraavasti: 5 " + _SEC + " " + filler
        + "Korvataan sairaanhoidon kustannuksia, jos kustannukset korvataan vankeuslain "
        "(767/2005) 10 luvun 7 " + _SEC + ":n perusteella; 7) muut kustannukset seuraavasti: nns."
    )
    joined = "".join(extract_enacting_clause_spans(text))
    assert "(123/2020)" in joined  # the genuine muutetaan bill survives
    assert "(767/2005)" not in joined  # the body-prose korvataan cross-reference is rejected


def test_formula_corroborated_korvataan_directive_is_extracted() -> None:
    # A GENUINE "korvataan" johtolause — introduced by the enactment formula — IS a directive
    # and must be extracted (the gate rejects only the un-corroborated body-prose usage).
    text = (
        "Lakiehdotukset 1. Laki testilain muuttamisesta Eduskunnan päätöksen mukaisesti "
        "korvataan testilain (123/2020) 5 " + _SEC + " seuraavasti: 5 " + _SEC + " Uusi teksti."
    )
    spans = extract_enacting_clause_spans(text)
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


def test_consequential_repeal_span_rebinds_when_next_verb_is_garbled() -> None:
    # HE 114/1998 regression: a consequential repeal in a voimaantulo clause ("Tällä lailla
    # kumotaan … vesilain (264/1961) 17 luvun 10 §.") sits before the NEXT bill, whose
    # amendment verb "muutetaan" is GARBLED by the geom text layer ("m uutetacm") so the
    # verb-head boundary detector cannot see it. The enactment FORMULA ("… päätöksen
    # mukaisesti") that opens the next johtolause is a garble-independent boundary: the span
    # must re-bind at the repeal's OWN sentence period, so the next bill's provisions (2/3/4/7
    # §) are NOT swept onto the repealed statute — only the single §10 repeal survives on it.
    from lawvm.finland.he_branch_parser import _parse_one_clause
    from lawvm.tools.fi_he_ir_compare import flatten_branch_ops

    text = (
        "Lakiehdotukset 1. Laki jostakin muuttamisesta "
        "muutetaan jonkin lain (999/2000) 1 " + _SEC + " seuraavasti: Uusi 1 §. "
        "Tämä laki tulee voimaan 1 päivänä marraskuuta 1999. "
        "Tällä lailla kumotaan 19 päivänä toukokuuta 1961 annetun vesilain (264/1961) 17 "
        "luvun 10 " + _SEC + ", sellaisena kuin se on laissa 467/1987. "
        "2. Laki Ahvenanmaan hallintotuomioistuimesta annetun lain muuttamisesta "
        "Eduskunnan päätöksen mukaisesti m uutetacm Ahvenanmaan hallintotuomioistuimesta "
        "annetun lain (547/1994) 2 " + _SEC + ":n 1 momentti, 3 " + _SEC + ":n 2 momentti "
        "sekä 4 ja 7 " + _SEC + " seuraavasti: Uusi 2 §."
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
    # Only the genuine §17/10 consequential repeal is attributed to 264/1961 — no phantom
    # 2/3/4/7 § cloned from the garbled Ahvenanmaa bill.
    assert {r for r in refs if "264/1961" in r} == {"repeal 264/1961/luku_17/10"}


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
# Amendment-HISTORY id ("sellaisena/sellaisina kuin …") must not become a       #
# phantom op TARGET (HE 139/2013: 26 phantom ops on 668/2013).                  #
# --------------------------------------------------------------------------- #


def test_history_list_id_not_a_phantom_target() -> None:
    # HE 139/2013 shape: a single COMBINED johtolause ("kumotaan <name> (301/2004) … muutetaan
    # 3 § … sellaisina kuin niistä ovat … ja (668/2013), 80 § … seuraavasti:").  668/2013 is
    # merely the MOST RECENT amending act in the provenance list, not an amendment target.  A
    # "§" follows the (668/2013) id, so the provision-mark guard does NOT reject the muutetaan
    # continuation head — the continuation-drop is what prevents the phantom.  Every op must
    # target the GOVERNING act 301/2004; none may target the history id 668/2013.
    from lawvm.finland.he_branch_parser import _parse_one_clause

    text = (
        "Lakiehdotukset 1. Laki ulkomaalaislain muuttamisesta "
        "kumotaan ulkomaalaislain (301/2004) 81 c " + _SEC + ", "
        "muutetaan 3 " + _SEC + ":n 6 kohta, 70 " + _SEC + ", 72 " + _SEC + ", "
        "sellaisina kuin niistä ovat, 70 " + _SEC + " laeissa 34/2006 ja (668/2013), "
        "72 " + _SEC + " osaksi laissa 549/2010, seuraavasti: "
        "3 " + _SEC + " Uusi teksti. 70 " + _SEC + " Uusi teksti. 72 " + _SEC + " Uusi teksti."
    )
    spans = extract_enacting_clause_spans(text)
    pdf_ops: list = []
    for span in spans:
        ops, _ = _parse_one_clause(span, len(pdf_ops), "HE 139/2013 vp", "fi/he/2013/139")
        pdf_ops.extend(ops)
    sids = {o.target_statute_id for o in pdf_ops}
    assert "668/2013" not in sids, sids
    assert sids == {"301/2004"}, sids
    # The governing act's touched provisions are all present (via the opening head's span).
    refs = {o.target_provision_ref for o in pdf_ops}
    assert {"301/2004/70", "301/2004/72", "301/2004/3/1/kohta_6"} <= refs, refs


def test_date_identified_law_opening_head_kept() -> None:
    # An OLD law named by DATE, not a "(NUM/YEAR)" id ("kumotaan 3 päivänä joulukuuta 1895
    # annetun ulosottolain … sellaisina kuin ne ovat … (389/73) …"): its ONLY parenthesised
    # id is a history id, but the head is the OPENING head of its johtolause (no earlier verb
    # head shares its block), so the span must still be created (existence anchor).  Its ops
    # resolve to an EMPTY statute id — the same on both witnesses, so they still match — and the
    # history id 389/73 must NOT be attributed as a target.
    from lawvm.finland.he_branch_parser import _parse_one_clause

    text = (
        "Lakiehdotukset 1. Laki ulosottolain muuttamisesta "
        "kumotaan 3 päivänä joulukuuta 1895 annetun ulosottolain "
        "3 luvun 12 " + _SEC + ":n 3 momentti ja 4 luvun 9 c " + _SEC + ", "
        "sellaisina kuin ne ovat, 3 luvun 12 " + _SEC + ":n 3 momentti laissa (389/73) "
        "ja 4 luvun 9 c " + _SEC + " laissa 197/1996, seuraavasti: "
        "3 luvun 12 " + _SEC + " Uusi teksti. 4 luvun 9 c " + _SEC + " Uusi teksti."
    )
    spans = extract_enacting_clause_spans(text)
    assert len(spans) == 1, spans
    pdf_ops: list = []
    for span in spans:
        ops, _ = _parse_one_clause(span, len(pdf_ops), "HE 106/1995 vp", "fi/he/1995/106")
        pdf_ops.extend(ops)
    sids = {o.target_statute_id for o in pdf_ops}
    assert "389/73" not in sids, sids
    assert sids == {""}, sids
    assert any(o.target_provision_ref == "/luku_3/12/3" for o in pdf_ops), [
        o.target_provision_ref for o in pdf_ops
    ]


def test_extract_statute_citation_skips_history_and_keeps_governing() -> None:
    # Unit-level: the GOVERNING id is cited BEFORE any "sellaisena/sellaisina kuin …" marker and
    # must resolve; every id AFTER the marker (a prior amending act) is excluded.
    from lawvm.finland.he_branch_parser import _extract_statute_citation

    # Governing (301/2004) before the marker resolves; the trailing (668/2013) is ignored.
    gov = _extract_statute_citation(
        "muutetaan ulkomaalaislain (301/2004) 70 "
        + _SEC
        + ", sellaisina kuin se on laissa (668/2013)"
    )
    assert gov is not None and gov[0] == "301/2004", gov

    # History-only directive (no governing paren id before the marker) resolves to nothing.
    hist_only = _extract_statute_citation(
        "muutetaan 3 " + _SEC + ", sellaisina kuin niistä ovat 79 " + _SEC + " ja (668/2013)"
    )
    assert hist_only is None, hist_only

    # Multi-act parenthesised history list — ALL ids after the marker are excluded.
    multi = _extract_statute_citation(
        "muutetaan 3 "
        + _SEC
        + ", sellaisina kuin ne ovat laeissa (34/2006), (380/2006) ja (668/2013)"
    )
    assert multi is None, multi


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


def test_glued_chapter_ordinal_recovers_luku_scope() -> None:
    # GEOM ARTIFACT: a two-column lakiehdotus reprint drops the thin space between a chapter
    # ordinal and its "luku"/"luvu" noun, so the PDF text layer reads "15lukuun"/"16luvun"
    # (glued).  The shared johtolause lexer tokenizes the glued form as a single opaque WORD and
    # drops the chapter, lowering the insert op to a BARE ref ("39/1889/12a") that never pairs
    # with the XML's luku-scoped ref ("39/1889/luku_15/12a") — the same target double-counted as
    # op_missing + op_extra (HE 161/2000).  _flatten_reading_text un-glues the ordinal from PDF-
    # STRUCTURAL signal alone (a digit abutting the chapter-noun stem is never a legitimate
    # token), so the chapter scope is recovered.  Regression guard: the glued reading text must
    # lower to the SAME luku-scoped ref as the spaced form.
    from lawvm.finland.he_branch_parser import _parse_one_clause

    glued = _pdf_page(
        "muutetaan rikoslain (39/1889) 1 luvun 11 §:n 2 momentti sekä lisätään "
        "15lukuun uusi 12 a §, 16luvun 20 §:ään uusi 4 momentti seuraavasti:"
    )
    spans = extract_enacting_clause_spans(glued)
    pdf_ops: list = []
    for span in spans:
        ops, _ = _parse_one_clause(span, len(pdf_ops), "HE 161/2000 vp", "fi/he/2000/161")
        pdf_ops.extend(ops)
    refs = {o.target_provision_ref for o in pdf_ops}
    assert "39/1889/luku_15/12a" in refs
    assert "39/1889/luku_16/20/4" in refs
    # the chapter must NOT have been dropped to a bare ref
    assert "39/1889/12a" not in refs


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


# A lakiehdotus reading text that introduces a genuine second bill on 594/1956 with a REAL
# bill TITLE ("Laki … muuttamisesta"), whose johtolause cites (594/1956). This is the only
# thing that convicts an XML-absent op block a benign second bill (label-independent).
_TITLED_SECOND_BILL_FLAT = (
    "Lakiehdotukset 1. Laki testilain muuttamisesta Eduskunnan päätöksen mukaisesti "
    "muutetaan testilain (594/1956) 1 §, 2 § ja 3 § seuraavasti:"
)


def test_second_bill_block_reclassifies_to_witness_disagreement() -> None:
    # XML op-set names ONLY statute 123/2020; the PDF read a block on 594/1956 GOVERNED BY A
    # REAL BILL TITLE (a genuine omnibus second bill the XML omits) — reclassify to the
    # out-of-scope witness disagreement, do NOT charge it as an op_extra_in_pdf defect.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("replace", "594/1956/1"),   # \
        HEFlatOp("insert", "594/1956/2"),    #  } titled second-bill block (absent statute)
        HEFlatOp("repeal", "594/1956/3"),    # /
    )
    div = _reclassify_out_of_scope_second_bills(
        diff_proposed_ops(xml, pdf), xml, _TITLED_SECOND_BILL_FLAT
    )
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["123/2020/5"].kind == "matched"
    assert by_ref["594/1956/1"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    assert by_ref["594/1956/2"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    assert by_ref["594/1956/3"].kind == _PDF_OUT_OF_SCOPE_STATUTE
    # No op_extra_in_pdf survives; matched is untouched (pure reclassification).
    assert not any(d.kind == "op_extra_in_pdf" for d in div)
    assert "594/1956" in by_ref["594/1956/1"].detail


def test_untitled_absent_statute_block_is_defect_not_benign() -> None:
    # A coherent 3-op block on an XML-absent statute with NEITHER a governing bill title NOR
    # a consequential-repeal head is a phantom target-misresolution DEFECT — block SIZE alone
    # must NOT force-benign it (the HE 114/1998 masked defect). It STAYS op_extra_in_pdf.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("repeal", "594/1956/1"),    # \
        HEFlatOp("repeal", "594/1956/2"),    #  } 3-op absent-statute block, NO governing head
        HEFlatOp("repeal", "594/1956/3"),    # /
    )
    # Flat mentions the statute only as a cross-reference, never a title or "Tällä lailla".
    flat = "muutetaan jonkin lain (123/2020) 5 § seuraavasti: viitaten lakiin (594/1956)"
    div = _reclassify_out_of_scope_second_bills(diff_proposed_ops(xml, pdf), xml, flat)
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["594/1956/1"].kind == "op_extra_in_pdf"
    assert by_ref["594/1956/2"].kind == "op_extra_in_pdf"
    assert by_ref["594/1956/3"].kind == "op_extra_in_pdf"
    assert not any(
        d.kind in (_PDF_OUT_OF_SCOPE_STATUTE, _PDF_CONSEQUENTIAL_REPEAL) for d in div
    )


def test_consequential_repeal_typed_distinctly_not_second_bill() -> None:
    # A repeal of an outside act's provision via a commencement clause ("Tällä lailla
    # kumotaan … (594/1956) …") is a real effect the XML omits — a witness disagreement — but
    # NOT a titled second bill: it is typed pdf_consequential_repeal, not out_of_scope_statute
    # and not a defect. (HE 114/1998 §17/10 on vesilaki 264/1961.)
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("repeal", "594/1956/luku_17/10"),  # consequential repeal, absent statute
    )
    flat = (
        "muutetaan jonkin lain (123/2020) 5 § seuraavasti: ... Tämä laki tulee voimaan 1 "
        "päivänä tammikuuta 2020. Tällä lailla kumotaan vanhan lain (594/1956) 17 luvun 10 §."
    )
    div = _reclassify_out_of_scope_second_bills(diff_proposed_ops(xml, pdf), xml, flat)
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["594/1956/luku_17/10"].kind == _PDF_CONSEQUENTIAL_REPEAL
    assert not any(d.kind == _PDF_OUT_OF_SCOPE_STATUTE for d in div)
    assert not any(d.kind == "op_extra_in_pdf" for d in div)


def test_same_statute_granularity_stays_op_extra() -> None:
    # Even a ≥3-op extra block on a statute the XML op-set DOES name is finer-granularity
    # PDF ops (same statute), NOT an out-of-scope second bill — it STAYS op_extra_in_pdf,
    # regardless of any title text in the reading region.
    xml = (HEFlatOp("replace", "123/2020/5"),)
    pdf = (
        HEFlatOp("replace", "123/2020/5"),   # matched
        HEFlatOp("insert", "123/2020/7"),    # \
        HEFlatOp("insert", "123/2020/8"),    #  } 3 extra ops, but statute 123/2020 IS in XML
        HEFlatOp("insert", "123/2020/9"),    # /
    )
    div = _reclassify_out_of_scope_second_bills(
        diff_proposed_ops(xml, pdf), xml, "Laki testilain muuttamisesta (123/2020)"
    )
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
    # The clean PDF DOES carry an enacting signature (verb + "seuraavasti:"); the LLM
    # classifier just refused to segment it → the honest no-clause sub-cause is
    # "amend verb present but unparsed", NOT a benign blanket bucket.
    assert r.compare_status == "pdf_amend_verb_unparsed"
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


def test_omission_divider_is_elided_body_kept_on_both_sides() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # A dash-run divider has TWO roles. Here it is a MID-body OMISSION divider: substantial
    # RETAINED provision prose follows it before any genuine terminator, so the XML elides the
    # run and keeps the text on BOTH sides. The old first-dash-wins truncation cut the body at
    # the run (dropping "Toinen …" — the dominant payload_mismatch sub-cause); it must now be
    # ELIDED (run -> single space) and the retained tail kept, while the FINAL END divider +
    # commencement clause is still trimmed.
    first = "Ensimmäinen momentti joka on riittävän pitkä vertailua varten."
    second = "Toinen säilytetty momentti joka jatkuu tämän jälkeen samassa pykälässä."
    rt = _body_reading(
        f"{first} — — — — {second}",
        tail="——— Tämä laki tulee voimaan päivänä kuuta 20 .",
    )
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == f"{first} {second}"


def test_omission_divider_before_kohta_marker_is_elided() -> None:
    from lawvm.tools.fi_he_ir_compare import _pdf_proposed_bodies

    # The retained continuation after a MID-body omission divider is often a "N)" kohta list
    # item (an amendment that keeps only some kohdat, eliding the divider between them). A short
    # kohta continuation must still be recognised as retained body (elide), not an END divider.
    first = "Tässä laissa tarkoitetaan riittävän pitkä johdantolause vertailua varten:"
    second = "5) viidennellä kohdalla säilytettyä määritelmää joka jää voimaan;"
    rt = _body_reading(
        f"{first} — — — — {second}",
        tail="——— Tämä laki tulee voimaan päivänä kuuta 20 .",
    )
    assert _pdf_proposed_bodies(rt)[_BODY_KEY] == f"{first} {second}"


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


def test_page_header_at_hyphen_seam_lets_wrapped_word_rejoin() -> None:
    from lawvm.tools.fi_he_ir_compare import _flatten_reading_text
    from lawvm.finland.op_equivalence import text_equivalence

    # A word WRAPPED at a page break has the running header ("2 HE 195/1996 vp") emitted
    # BETWEEN its trailing hyphen and its continuation ("jär-\n2 HE 195/1996 vp\njestämisestä"
    # for "järjestämisestä"). The header is stripped BEFORE de-hyphenation, so the leading
    # page digit no longer masquerades as a numeric compound seam ("40-vuotias") that keeps
    # the hyphen — the trailing hyphen abuts its continuation and the wrapped word rejoins.
    # (The residual mid-word space is closed by WHITESPACE_MIDWORD at comparison time.)
    raw = "oikeuden jär-\r\n2 HE 195/1996 vp\r\njestämisestä ja muusta"
    flat = _flatten_reading_text(raw)
    assert "jär- jestämisestä" not in flat  # no stranded residual hyphen at the seam
    assert "HE 195/1996 vp" not in flat  # header furniture removed
    # End-to-end: the reconstructed body is EXACT-equivalent to the clean XML form.
    eq = text_equivalence("oikeuden järjestämisestä ja muusta", flat)
    assert eq.equal


def test_page_header_at_hyphen_seam_preserves_genuine_compound() -> None:
    from lawvm.tools.fi_he_ir_compare import _flatten_reading_text

    # INVARIANT: the reorder must NEVER fuse a genuine compound hyphen. A "sotilas- ja …"
    # elliptical compound that happens to break at a page seam (header interleaved) is kept
    # by ``dehyphenate``'s corroboration gate — it stays "sotilas- ja", never "sotilasja".
    raw = "valtion sotilas-\r\n5 HE 1/2000 vp\r\nja siviilihenkilöstö palvelee"
    flat = _flatten_reading_text(raw)
    assert "sotilas- ja" in flat
    assert "sotilasja" not in flat


def test_compound_hyphen_distinction_is_never_folded_to_equal() -> None:
    from lawvm.finland.op_equivalence import text_equivalence

    # A GENUINE compound-hyphen difference (one witness dropped the hyphen of an elliptical
    # "X- ja Y" compound) is a REAL difference the quotient must NOT mask — folding it would
    # hide a genuine content divergence. Both canonical elliptical forms must survive as a
    # residual (equal is False), so the fold can never launder a compound distinction.
    assert not text_equivalence("sotilas- ja siviilihenkilöstö", "sotilasja siviilihenkilöstö").equal
    assert not text_equivalence("työ- ja elinkeinoministeriö", "työja elinkeinoministeriö").equal


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
    # XML has amendment ops but the PDF text carries NO enacting directive at all (no
    # amendment head verb, no "seuraavasti:" terminator) → the honest ``pdf_no_amend_verb``
    # sub-cause of the old blanket ``pdf_no_clause``. Deferred, never a benign clean read.
    xml = _he_xml(_CLAUSE)
    r = compare_he(xml, "VEROTAULUKKO tuote perusvero lisävero ilman lauseketta", he_year=2020, he_number=99)
    assert r.compare_status == "pdf_no_amend_verb"
    assert r.xml_op_count >= 1


def test_garbled_layer_typed_garble_suspect_not_benign() -> None:
    # A corrupt-font / broken-CMap PDF text layer (control-code glyphs) that yields no
    # clause MUST type ``garble_suspect`` — a first-class VISION re-read escalation
    # candidate — NEVER a benign no-clause bucket and never a silent raw return.
    xml = _he_xml(_CLAUSE)
    garbled = ("\x01\x02\x14\x0e\x05" * 60) + " " + ("\x07\x10\x08\x12" * 60)
    r = compare_he(xml, garbled, he_year=2020, he_number=99)
    assert r.compare_status == "garble_suspect"
    assert "corruption glyphs" in r.detail
    # It is UN-ACCOUNTED: not in the benign allowlist, and never "compared".
    from lawvm.tools.fi_he_ir_compare import (
        _BENIGN_NONCOMPARED_STATUSES,
        _ESCALATION_PENDING_STATUSES,
    )
    assert r.compare_status not in _BENIGN_NONCOMPARED_STATUSES
    assert r.compare_status in _ESCALATION_PENDING_STATUSES


def test_clean_born_digital_read_is_not_flagged_garble() -> None:
    # 0 false positives: a clean enacting clause reads EXACT — the garble detection must
    # not divert a clean born-digital read. (A stray odd glyph at ~0 density stays clean.)
    xml = _he_xml(_CLAUSE)
    clean = _pdf_page(_CLAUSE) + " sopimusvelvoitteista￾käsittelyssä"  # benign U+FFFE
    r = compare_he(xml, clean, he_year=2020, he_number=99)
    assert r.compare_status == "compared"
    assert r.exact_equivalent


def test_benign_is_an_explicit_allowlist_not_status_ne_error() -> None:
    # The benign partition is an EXPLICIT allowlist: the escalation / pathology strata
    # (which are NOT "error") must NOT be classed benign — killing the old
    # ``status != 'error'`` heuristic that silently absolved a garbled read as benign.
    from lawvm.tools.fi_he_ir_compare import (
        _BENIGN_NONCOMPARED_STATUSES,
        _ESCALATION_PENDING_STATUSES,
        _NON_COMPARED_STATUSES,
        _PATHOLOGY_STATUSES,
    )

    # garble_suspect and the no-clause defects are NON-error yet NON-benign.
    for s in ("garble_suspect", "pdf_no_amend_verb", "pdf_amend_verb_unparsed"):
        assert s != "error"
        assert s not in _BENIGN_NONCOMPARED_STATUSES
        assert s in _NON_COMPARED_STATUSES
    # The three partitions are disjoint and cover the whole non-compared set.
    assert not (set(_BENIGN_NONCOMPARED_STATUSES) & set(_ESCALATION_PENDING_STATUSES))
    assert not (set(_BENIGN_NONCOMPARED_STATUSES) & set(_PATHOLOGY_STATUSES))
    assert set(_NON_COMPARED_STATUSES) == (
        set(_BENIGN_NONCOMPARED_STATUSES)
        | set(_ESCALATION_PENDING_STATUSES)
        | set(_PATHOLOGY_STATUSES)
    )


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

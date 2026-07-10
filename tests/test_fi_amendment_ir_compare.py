"""``lawvm fi-amendment-ir-compare`` — amendment IR-EQUIVALENCE (PDF→ops vs XML→ops).

Hermetic: no vision/LLM server, no farchive. The PDF-text side is supplied by a
SCRIPTED FAKE (``text_fn`` / ``pdf_text_fn``) so both witnesses flow through the
real shared clause parser (``johtolause/api.parse_clause``) and the diff is
exercised end to end. Covers:

  * op flattening + canonical ``target_ref`` rendering out of a real ClauseAST;
  * the operative-johtolause extractor over noisy gazette reading text (incl. the
    historical past-participle ministry form);
  * the four ``OpDivergence`` kinds
    (matched / op_missing_in_pdf / op_extra_in_pdf / kind_mismatch);
  * IR-EQUIVALENCE: identical clause text on both sides ⇒ all matched;
  * typed strata (xml/pdf incomplete) surfaced as status, never silent empty;
  * locator parsing.
"""
from __future__ import annotations

import pytest

from lawvm.tools.fi_amendment_ir_compare import (
    CompareResult,
    FlatOp,
    OperativeClauseNotFound,
    OpDivergence,
    StatuteLocator,
    _pdf_is_annex_by_disjoint_targets,
    amendment_ops_from_clause_text,
    amendment_ops_from_pdf,
    compare_statute,
    diff_amendment_ops,
    extract_operative_johtolause,
    flatten_clause_ast,
    parse_statute_locator,
    result_to_json,
)

# A compact but real Finnish amendment johtolause: three section REPLACEs and one
# section INSERT. Parsed by the shared production parser, this lowers to a stable
# 4-op ClauseAST (replace 7/10/16, insert 2a).
JOHTO = (
    "muutetaan sähkön ja eräiden polttoaineiden valmisteverosta annetun lain "
    "(1260/1996) 7 §, 10 § ja 16 § sekä lisätään uusi 2 a § seuraavasti:"
)


def _gazette_page(johto: str) -> str:
    """Wrap a johtolause in realistic surrounding gazette reading text (noise)."""
    return (
        "N:o 800\nLaki\nsähköverolain muuttamisesta\n"
        "Annettu Helsingissä 25 päivänä elokuuta 1994\n"
        "Eduskunnan päätöksen mukaisesti\n" + johto + "\n7 §\nUusi pykälän teksti.\n"
    )


# --------------------------------------------------------------------------- #
# flattening + target_ref rendering                                          #
# --------------------------------------------------------------------------- #


def test_flatten_renders_canonical_target_refs() -> None:
    ast = amendment_ops_from_clause_text(JOHTO, statute_id="1994/800")
    ops = flatten_clause_ast(ast)
    rendered = {op.render for op in ops}
    assert rendered == {
        "replace section:7",
        "replace section:10",
        "replace section:16",
        "insert section:2a",
    }


# --------------------------------------------------------------------------- #
# operative-clause extraction from PDF reading text                          #
# --------------------------------------------------------------------------- #


def test_extract_operative_johtolause_from_noisy_page() -> None:
    got = extract_operative_johtolause(_gazette_page(JOHTO))
    assert got.startswith("muutetaan")
    assert got.rstrip().endswith("seuraavasti:")


def test_extract_handles_past_participle_ministry_form() -> None:
    # 1990s ministry-decision johtolause uses past participles, not the passive.
    text = (
        "N:o 800\nKauppa- ja teollisuusministeriön päätös\n"
        "Kauppa- ja teollisuusministeriö on\n"
        "muuttanut 7 §:n sekä lisännyt uuden 2 a §:n, seuraavasti:\n7 §\n..."
    )
    got = extract_operative_johtolause(text)
    assert got.startswith("muuttanut")
    assert "seuraavasti" in got


def test_extract_raises_when_no_operative_clause() -> None:
    # An annex-only page (pdf_incomplete): tables, no "... seuraavasti".
    with pytest.raises(OperativeClauseNotFound):
        extract_operative_johtolause("VEROTAULUKKO\nTuote Perusvero Lisävero\nSähkö 1,2 3,4\n")


# --------------------------------------------------------------------------- #
# the four divergence kinds                                                  #
# --------------------------------------------------------------------------- #


def test_diff_all_matched() -> None:
    ops = (FlatOp("replace", "section:7"), FlatOp("insert", "section:2a"))
    div = diff_amendment_ops(ops, ops)
    assert [d.kind for d in div] == ["matched", "matched"]
    assert all(d.xml_op == d.pdf_op for d in div)


def test_diff_op_missing_extra_and_kind_mismatch() -> None:
    xml = (
        FlatOp("replace", "section:7"),
        FlatOp("replace", "section:10"),
        FlatOp("insert", "section:2a"),
    )
    pdf = (
        FlatOp("insert", "section:7"),  # same target, different kind -> kind_mismatch
        FlatOp("insert", "section:2a"),  # matched
        FlatOp("repeal", "section:99"),  # only in PDF -> op_extra_in_pdf
    )
    div = diff_amendment_ops(xml, pdf)
    by_ref = {d.target_ref: d for d in div}
    assert by_ref["section:7"].kind == "kind_mismatch"
    assert "xml=replace" in by_ref["section:7"].detail
    assert by_ref["section:10"].kind == "op_missing_in_pdf"
    assert by_ref["section:10"].pdf_op is None
    assert by_ref["section:2a"].kind == "matched"
    assert by_ref["section:99"].kind == "op_extra_in_pdf"
    assert by_ref["section:99"].xml_op is None


def test_op_divergence_is_frozen() -> None:
    import dataclasses

    d = OpDivergence("matched", "section:7", "replace section:7", "replace section:7", "")
    # Frozen dataclass: the STABLE consumer contract must not be mutated in place.
    assert d.__dataclass_params__.frozen is True  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.__setattr__("kind", "kind_mismatch")


# --------------------------------------------------------------------------- #
# end-to-end IR-EQUIVALENCE via a scripted fake PDF-text side                 #
# --------------------------------------------------------------------------- #


def test_pdf_path_equivalent_to_xml_path_all_matched() -> None:
    xml_ast = amendment_ops_from_clause_text(JOHTO, statute_id="1994/800")
    # PDF side: SAME clause, embedded in noisy gazette text, injected via text_fn.
    pdf_ast = amendment_ops_from_pdf(
        "1994/800", farchive="unused", text_fn=lambda: _gazette_page(JOHTO)
    )
    div = diff_amendment_ops(xml_ast, pdf_ast)
    assert div, "expected a non-empty op diff"
    assert all(d.kind == "matched" for d in div)
    result = CompareResult("1994/800", "fin", "compared", div, len(div), len(div))
    assert result.exact_equivalent is True
    assert result.typed_divergence_count == 0


def test_pdf_dropping_a_section_shows_pdf_missing() -> None:
    xml_ast = amendment_ops_from_clause_text(JOHTO, statute_id="1994/800")
    # PDF reconstruction lost "10 §" — a legally-significant dropped op.
    dropped = JOHTO.replace("7 §, 10 § ja 16 §", "7 § ja 16 §")
    pdf_ast = amendment_ops_from_pdf(
        "1994/800", farchive="unused", text_fn=lambda: _gazette_page(dropped)
    )
    div = diff_amendment_ops(xml_ast, pdf_ast)
    missing = [d for d in div if d.kind == "op_missing_in_pdf"]
    assert any(d.target_ref == "section:10" for d in missing)


def test_pdf_annex_prose_with_operative_verb_is_flagged_not_forced() -> None:
    # Annex prose that merely CONTAINS an operative verb + "seuraavasti" but names
    # no structural §/kohta target must be flagged pdf_annex_only, not lowered to a
    # hollow clause that would diff as all-ops-missing.
    annex = (
        "Liite\nTalletussuojamaksujen laskenta\nVAIHE 2\n"
        "Jos lukumäärää ei voida jakaa tasan, lisätään yksi havainto kuhunkin "
        "riskiluokkaan alimmasta luokasta lähtien seuraavasti:\nVAIHE 3\n"
    )
    with pytest.raises(OperativeClauseNotFound):
        amendment_ops_from_pdf("2017/822", farchive="unused", text_fn=lambda: annex)


def test_annex_disjoint_targets_gate_flags_stray_op_annex() -> None:
    # F-corpus pathology: annex/body prose lowers to a HANDFUL of ops (here 1) that
    # target provisions the amendment never touches. Disjoint targets + fewer ops than
    # the XML enacting clause ⇒ annex-only, not the operative gazette.
    xml_flat = (
        FlatOp("replace", "section:7"),
        FlatOp("replace", "section:10"),
        FlatOp("replace", "section:16"),
    )
    pdf_flat = (FlatOp("replace", "section:3"),)  # stray annex op
    assert _pdf_is_annex_by_disjoint_targets(xml_flat, pdf_flat) is True


def test_annex_gate_does_not_hide_equal_size_misread() -> None:
    # A genuine EQUAL-size reconstruction whose targets are all wrong must NOT be
    # absorbed as annex — it must SURFACE as a typed op_missing/op_extra defect.
    xml_flat = (FlatOp("replace", "section:7"),)
    pdf_flat = (FlatOp("replace", "section:9"),)  # same count, wrong target
    assert _pdf_is_annex_by_disjoint_targets(xml_flat, pdf_flat) is False


def test_annex_gate_passes_when_targets_overlap() -> None:
    # Sharing even one target ⇒ the same enacting clause ⇒ compare normally.
    xml_flat = (
        FlatOp("replace", "section:7"),
        FlatOp("replace", "section:10"),
    )
    pdf_flat = (FlatOp("replace", "section:7"),)
    assert _pdf_is_annex_by_disjoint_targets(xml_flat, pdf_flat) is False


def test_compare_statute_returns_typed_status_never_raises(tmp_path) -> None:
    # compare_statute must surface a TYPED status, never raise past its boundary.
    # With no real farchive the XML read fails -> status "error" (typed), not an
    # exception. (The PDF-annex / xml-frame benign paths are covered directly by
    # the extractor and amendment_ops_from_pdf tests above.)
    missing = str(tmp_path / "no.farchive")
    result = compare_statute(
        StatuteLocator("1994/800", "fin"),
        farchive=missing,
        pdf_text_fn=lambda: "VEROTAULUKKO\nTuote Perusvero\nSähkö 1,2\n",
    )
    assert result.compare_status in {"error", "pdf_annex_only", "xml_frame_only"}
    assert isinstance(result, CompareResult)


def test_result_to_json_shape() -> None:
    xml_ast = amendment_ops_from_clause_text(JOHTO, statute_id="1994/800")
    pdf_ast = amendment_ops_from_pdf(
        "1994/800", farchive="unused", text_fn=lambda: _gazette_page(JOHTO)
    )
    div = diff_amendment_ops(xml_ast, pdf_ast)
    result = CompareResult("1994/800", "fin", "compared", div, len(div), len(div))
    payload = result_to_json(result)
    assert payload["sid"] == "1994/800"
    assert payload["compare_status"] == "compared"
    assert payload["counts"]["matched"] == len(div)
    assert payload["typed_divergence_count"] == 0
    assert payload["exact_equivalent"] is True
    assert len(payload["divergences"]) == len(div)
    assert set(payload["divergences"][0]) == {
        "kind", "target_ref", "xml_op", "pdf_op", "detail"
    }


# --------------------------------------------------------------------------- #
# locator parsing                                                            #
# --------------------------------------------------------------------------- #


def test_parse_statute_locator_forms() -> None:
    assert parse_statute_locator("1994/800") == StatuteLocator("1994/800", "fin")
    assert parse_statute_locator("1994/800", lang="swe") == StatuteLocator("1994/800", "swe")
    assert parse_statute_locator(
        "finlex://sd/1994/800/fin/main.xml"
    ) == StatuteLocator("1994/800", "fin")
    assert parse_statute_locator(
        "finlex://sd/1994/800/swe/media/3024.pdf"
    ) == StatuteLocator("1994/800", "swe")


def test_statute_locator_derived_locators() -> None:
    loc = StatuteLocator("1994/800", "fin")
    assert loc.xml_locator == "finlex://sd/1994/800/fin/main.xml"
    assert loc.media_glob() == "finlex://sd/1994/800/fin/media/%.pdf"

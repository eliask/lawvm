"""Unit tests for lawvm.finland.vts — voimaantulosäännös repeal extraction."""
import re

from lawvm.corpus_store import get_corpus_store
from lawvm.core.observation_registry import get_finding_spec
from lawvm.finland.vts import (
    VTS_SKIPPED_TARGET_RULE_ID,
    VtsSkippedTarget,
    _expand_section_range_vts,
    _parent_title_variants,
    _vts_extract_after_citation,
    extract_voimaantulo_repeals,
)

# ---------------------------------------------------------------------------
# _expand_section_range_vts
# ---------------------------------------------------------------------------


def test_expand_section_range_numeric_range() -> None:
    assert _expand_section_range_vts("12", "14") == ["12", "13", "14"]


def test_expand_section_range_single_item_when_start_equals_end() -> None:
    assert _expand_section_range_vts("5", "5") == ["5"]


def test_expand_section_range_returns_start_when_reversed() -> None:
    # start > end for numeric case → return just start (not expanded)
    result = _expand_section_range_vts("10", "7")
    assert result == ["10"]


def test_expand_section_range_letter_suffix_same_base() -> None:
    # "33a"–"33c" → ["33a", "33b", "33c"]
    result = _expand_section_range_vts("33a", "33c")
    assert result == ["33a", "33b", "33c"]


def test_expand_section_range_letter_suffix_single() -> None:
    result = _expand_section_range_vts("7b", "7b")
    assert result == ["7b"]


def test_expand_section_range_mixed_bases_returns_start() -> None:
    # Different numeric bases — not a valid range
    result = _expand_section_range_vts("3a", "4c")
    assert result == ["3a"]


def test_expand_section_range_non_numeric_returns_start() -> None:
    result = _expand_section_range_vts("abc", "def")
    assert result == ["abc"]


# ---------------------------------------------------------------------------
# _vts_extract_after_citation
# ---------------------------------------------------------------------------


def _cit_re(num: int, year: str) -> "re.Pattern[str]":
    return re.compile(
        r'\(\s*' + re.escape(str(num)) + r'\s*/\s*(?:'
        + re.escape(year) + r'|' + re.escape(year[-2:]) + r')\s*\)',
        re.IGNORECASE,
    )


def test_vts_extract_after_citation_basic() -> None:
    text = "Tällä lailla kumotaan laki (925/1979) 3, 4 ja 5 §."
    cit = _cit_re(925, "1979")
    result = _vts_extract_after_citation(text, cit)
    assert "3" in result
    assert "§" in result


def test_vts_extract_after_citation_truncates_at_sellaisena_kuin() -> None:
    text = "kumotaan (100/2000) 3 § sellaisena kuin se on laissa 500/2001."
    cit = _cit_re(100, "2000")
    result = _vts_extract_after_citation(text, cit)
    assert "sellaisena" not in result
    assert "3 §" in result


def test_vts_extract_after_citation_truncates_at_semicolon() -> None:
    text = "kumotaan (100/2000) 3 §; ja muita säädöksiä"
    cit = _cit_re(100, "2000")
    result = _vts_extract_after_citation(text, cit)
    assert ";" not in result


def test_vts_extract_after_citation_truncates_at_next_citation() -> None:
    text = "kumotaan (100/2000) 3 § (200/2001) toisen lain 5 §"
    cit = _cit_re(100, "2000")
    result = _vts_extract_after_citation(text, cit)
    # Should not reach (200/2001)
    assert "(200/2001)" not in result


def test_vts_extract_after_citation_returns_empty_when_no_match() -> None:
    text = "kumotaan (999/2000) 3 §"
    cit = _cit_re(100, "2020")
    result = _vts_extract_after_citation(text, cit)
    assert result == ""


def test_vts_extract_after_citation_truncates_at_comma_statute_transition() -> None:
    """Comma-based transition: ", sosiaalihuollon... annetun lain" cuts."""
    text = (
        "kumotaan (785/1992) 11 §, "
        "sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain 24 ja 24 a §"
    )
    cit = _cit_re(785, "1992")
    result = _vts_extract_after_citation(text, cit)
    assert "11 §" in result
    assert "24" not in result


def test_vts_extract_after_citation_truncates_at_ja_statute_transition() -> None:
    """Regression: 2023/739 repeals 1992/785 §11 AND 812/2000 §24,24a.

    Text: "...annetun lain 11 § ja sosiaalihuollon ... annetun lain 24 ja 24 a §"
    The "ja" before the second statute name must act as a boundary.
    """
    text = (
        "kumotaan (785/1992) 11 § "
        "ja sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain 24 ja 24 a §"
    )
    cit = _cit_re(785, "1992")
    result = _vts_extract_after_citation(text, cit)
    assert "11 §" in result
    # Must NOT include sections from the other statute
    assert "24" not in result, f"Cross-statute bleed: {result!r}"


def test_vts_extract_after_citation_ja_in_title_not_false_boundary() -> None:
    """'ja' inside a statute title (e.g. 'asemasta ja oikeuksista') must NOT cut."""
    text = "kumotaan (785/1992) 11 ja 13 §"
    cit = _cit_re(785, "1992")
    result = _vts_extract_after_citation(text, cit)
    assert "11" in result
    assert "13" in result


def test_vts_extract_after_citation_numeric_list_ja_not_false_statute_transition() -> None:
    """'43 ja 45–47 kohta' must not be cut at 'ja' even though a statute follows later.

    Regression: the old pattern r'\\s+ja\\s+[a-zäöå0-9]...' matched '43 ja 45–47'
    because '4' (digit) is in [0-9], sending the fragment boundary to before 'ja'.
    The fix requires the first char after 'ja ' to be a letter so numeric-list
    continuations are not treated as statute-name transitions.
    """
    text = (
        "(1115/1993) 2 §:n 1 momentin 34, 35, 37, 38, 40, 42, 43 ja 45–47 kohta "
        "sekä eräiden valtion omistamien alueiden muodostamisesta soidensuojelualueiksi "
        "annetun asetuksen (801/1985) 1 §:n 28 kohta."
    )
    cit = _cit_re(1115, "1993")
    result = _vts_extract_after_citation(text, cit)
    # Must include both sides of "ja" in the item list
    assert "43" in result
    assert "45" in result
    # Must stop before the next citation (801/1985) — the next statute is excluded
    assert "801" not in result


# ---------------------------------------------------------------------------
# extract_voimaantulo_repeals — section (§) repeals
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _vts_xml(repeal_text: str, parent_num: int = 925, parent_year: str = "1979") -> bytes:
    """Build minimal amendment XML with a voimaantulo kumotaan clause."""
    return (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble>'
        f'    <formula name="enactingClause">säädetään seuraavasti:</formula>'
        f'  </preamble>'
        f'  <body>'
        f'    <section eId="sec_1"><num>1 §</num>'
        f'      <subsection><content>Main provision text.</content></subsection>'
        f'    </section>'
        f'    <section eId="sec_2"><num>2 §</num>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan ({parent_num}/{parent_year}) {repeal_text}'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()


def test_extract_voimaantulo_repeals_single_section() -> None:
    xml = _vts_xml("28 §.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    labels = {op.target_section for op in ops}
    assert "28" in labels
    op = next(o for o in ops if o.target_section == "28")
    assert op.op_type == "REPEAL"
    assert op.target_kind == "P"
    assert op.voimaantulo_repeal is True


def test_extract_voimaantulo_repeals_comma_list_of_sections() -> None:
    xml = _vts_xml("64, 66, 68 ja 69 §.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    labels = {op.target_section for op in ops}
    assert {"64", "66", "68", "69"} == labels


def test_extract_voimaantulo_repeals_section_range() -> None:
    xml = _vts_xml("12–14 §.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    labels = {op.target_section for op in ops}
    assert labels == {"12", "13", "14"}


def test_extract_voimaantulo_repeals_keeps_trailing_section_range_after_genitive_subsections_real_corpus() -> None:
    cs = get_corpus_store()
    xml = cs.read_source("2023/741")
    if xml is None:
        return
    ops = extract_voimaantulo_repeals(
        xml,
        "1987/322",
        parent_title="Laki terveydenhuollon järjestämisestä puolustusvoimissa",
    )
    labels = {op.target_section for op in ops if op.target_kind == "P" and op.target_section}
    assert {"10a", "10b", "10c", "10d", "10e", "10f"} <= labels


def test_extract_voimaantulo_repeals_chapter_repeal() -> None:
    xml = _vts_xml("3 luku.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    chapter_ops = [o for o in ops if o.target_kind == "L"]
    assert any(o.target_section == "3" for o in chapter_ops)


def test_extract_voimaantulo_repeals_returns_empty_when_no_match() -> None:
    xml = _vts_xml("7 §.", parent_num=999, parent_year="2000")
    # Parent is 1979/925 but amendment cites 999/2000
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    assert ops == []


def test_extract_voimaantulo_repeals_does_not_mix_parent_citation_from_one_paragraph_with_kumotaan_in_sibling() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_52"><num>52 §</num>'
        f'      <subsection eId="sec_52__subsec_1">'
        f'        <paragraph eId="sec_52__subsec_1__para_1"><num>1)</num><content>'
        f'          <p>sovellettaessa kotikuntalain (201/1994) 2 ja 6 a §:ää lapsen synnyttäneeseen äitiin sovelletaan, '
        f'          mitä mainituissa pykälissä säädetään äidistä, ja tämän lain 3 §:n 1 momentissa tarkoitettuun äitiin '
        f'          sovelletaan, mitä mainituissa pykälissä säädetään isästä;</p>'
        f'        </content></paragraph>'
        f'        <paragraph eId="sec_52__subsec_1__para_2"><num>2)</num><content>'
        f'          <p>etu- ja sukunimilain (946/2017) 28 §:n 2 momentin 4 kohtaa sovelletaan myös, jos äitiys kumotaan tämän lain nojalla.</p>'
        f'        </content></paragraph>'
        f'      </subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(xml, "1994/201", parent_title="Kotikuntalaki")
    assert ops == []


def test_extract_voimaantulo_repeals_keeps_same_paragraph_citation_repeal_after_cross_paragraph_fix() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_52"><num>52 §</num>'
        f'      <subsection eId="sec_52__subsec_1">'
        f'        <paragraph eId="sec_52__subsec_1__para_1"><num>1)</num><content>'
        f'          <p>Tällä lailla kumotaan kotikuntalain (201/1994) 3 §.</p>'
        f'        </content></paragraph>'
        f'        <paragraph eId="sec_52__subsec_1__para_2"><num>2)</num><content>'
        f'          <p>Tämän lain 3 §:n 1 momentissa tarkoitettuun äitiin sovelletaan, mitä mainituissa pykälissä säädetään isästä.</p>'
        f'        </content></paragraph>'
        f'      </subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(xml, "1994/201", parent_title="Kotikuntalaki")
    assert [(op.target_section, op.target_paragraph, op.target_item) for op in ops] == [("3", None, None)]


def test_extract_voimaantulo_repeals_matches_bare_parent_title() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_13"><num>13 §</num>'
        f'      <heading>Voimaantulo</heading>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan sosiaalihuoltolain 27 a―27 c §.'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(xml, "1982/710", parent_title="Sosiaalihuoltolaki")
    labels = {op.target_section for op in ops}
    assert labels == {"27a", "27b", "27c"}


def test_extract_voimaantulo_repeals_real_corpus_2018_253_does_not_false_repeal_kotikuntalaki_section_3() -> None:
    cs = get_corpus_store()
    xml = cs.read_source("2018/253")
    if xml is None:
        return
    ops = extract_voimaantulo_repeals(xml, "1994/201", parent_title="Kotikuntalaki")
    assert not any(op.target_section == "3" and op.target_chapter is None for op in ops)


def test_extract_voimaantulo_repeals_force_except_clause_marks_excluded_section() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">muutetaan haastemieslain (505/1986) 7 §.</formula></preamble>'
        f'  <body>'
        f'    <hcontainer eId="entryIntoForce" name="entryIntoForce">'
        f'      <content>'
        f'        <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2025.</p>'
        f'        <p>Haastemiesasetus (506/1986) jää sen 2 §:ää lukuun ottamatta voimaan tämän lain tullessa voimaan.</p>'
        f'      </content>'
        f'    </hcontainer>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(xml, "1986/506", parent_title="Haastemiesasetus")
    assert len(ops) == 1
    op = ops[0]
    assert op.op_type == "REPEAL"
    assert op.target_kind == "P"
    assert op.target_section == "2"
    assert op.target_paragraph is None
    assert op.voimaantulo_repeal is True


def test_extract_voimaantulo_repeals_matches_conclusions_paragraph_repeal_clause() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_1"><num>1 §</num><subsection><content><p>Pääsisältö.</p></content></subsection></section>'
        f'  </body>'
        f'  <conclusions>'
        f'    <p>Tämä laki tulee voimaan 1 päivänä maaliskuuta 1946. '
        f'    Sillä kumotaan eräistä naapuruussuhteista 13 päivänä helmikuuta 1920 annetun lain 6 §.</p>'
        f'  </conclusions>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "1920/26",
        parent_title="Laki eräistä naapuruussuhteista",
    )
    assert len(ops) == 1
    op = ops[0]
    assert op.target_section == "6"
    assert op.target_kind == "P"
    assert op.voimaantulo_repeal is True


def test_extract_voimaantulo_repeals_matches_parent_title_with_trailing_period() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <conclusions>'
        f'    <p>Sillä kumotaan eräistä naapuruussuhteista 13 päivänä helmikuuta 1920 annetun lain 6 §.</p>'
        f'  </conclusions>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "1920/26",
        parent_title="Laki eräistä naapuruussuhteista.",
    )
    assert len(ops) == 1
    assert ops[0].target_section == "6"


def test_extract_voimaantulo_repeals_does_not_bleed_into_dated_other_statute_after_parent_title() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään seuraavasti:</formula></preamble>'
        f'  <conclusions>'
        f'    <p>Sillä kumotaan eräistä naapuruussuhteista 13 päivänä helmikuuta 1920 annetun lain 6 § '
        f'    ja 3 päivänä toukokuuta 1927 annetun tielain 9 §:n 1 momentti.</p>'
        f'  </conclusions>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "1920/26",
        parent_title="Laki eräistä naapuruussuhteista.",
    )
    got = {(op.target_section, op.target_paragraph) for op in ops}
    assert got == {("6", None)}


def test_extract_voimaantulo_repeals_returns_empty_for_bad_xml() -> None:
    ops = extract_voimaantulo_repeals(b"<not valid xml", "1979/925")
    assert ops == []


def test_extract_voimaantulo_repeals_all_ops_are_repeal_type() -> None:
    xml = _vts_xml("3, 4 ja 5 §.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    assert all(o.op_type == "REPEAL" for o in ops)


def test_extract_voimaantulo_repeals_deduplicates_labels() -> None:
    # Construct XML where same section appears twice (e.g. range + single)
    xml = _vts_xml("3–5 § sekä 4 §.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    labels = [o.target_section for o in ops if o.target_kind == "P"]
    assert len(labels) == len(set(labels)), "Duplicate labels found"


def test_extract_voimaantulo_repeals_subsection_target() -> None:
    xml = _vts_xml("6 §:n 1 momentti.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    assert len(ops) == 1
    op = ops[0]
    assert op.target_kind == "P"
    assert op.target_section == "6"
    assert op.target_paragraph == 1
    assert op.target_item is None
    assert op.voimaantulo_repeal is True


def test_extract_voimaantulo_repeals_subsection_item_target() -> None:
    xml = _vts_xml("6 §:n 1 momentin 3 kohta.")
    ops = extract_voimaantulo_repeals(xml, "1979/925")
    assert len(ops) == 1
    op = ops[0]
    assert op.target_kind == "P"
    assert op.target_section == "6"
    assert op.target_paragraph == 1
    assert op.target_item == "3"


def test_extract_voimaantulo_repeals_records_skipped_alakohta_target() -> None:
    xml = _vts_xml("6 §:n 1 momentin 3 kohdan a alakohta.")
    skipped: list[VtsSkippedTarget] = []
    ops = extract_voimaantulo_repeals(xml, "1979/925", skipped_targets_out=skipped)

    assert ops == []
    assert len(skipped) == 1
    record = skipped[0]
    assert record.rule_id == VTS_SKIPPED_TARGET_RULE_ID
    assert record.reason_code == "unsupported_subitem_target"
    assert record.source_statute == "1979/925"
    assert record.target_section == "6"
    assert record.target_paragraph == 1
    assert record.target_item == "3"
    assert record.target_subitem == "a"
    assert record.blocking is False
    assert get_finding_spec(record.rule_id) is not None
    assert record.as_detail()["reason_code"] == "unsupported_subitem_target"


def test_extract_voimaantulo_repeals_records_skipped_kohta_only_bare_section_target() -> None:
    xml = _vts_xml("6 §:n 3 kohta.")
    skipped: list[VtsSkippedTarget] = []
    ops = extract_voimaantulo_repeals(xml, "1979/925", skipped_targets_out=skipped)

    assert ops == []
    assert len(skipped) == 1
    record = skipped[0]
    assert record.rule_id == VTS_SKIPPED_TARGET_RULE_ID
    assert record.reason_code == "unsafe_kohta_only_bare_section_parse"
    assert record.target_section == "6"
    assert record.target_paragraph is None
    assert record.target_item is None
    assert "whole-section repeal suppressed" in record.source_reason


def test_extract_voimaantulo_repeals_keeps_chapter_scope_across_grouped_refs() -> None:
    xml = _vts_xml(
        "2 luvun 5 § ja 7 §:n 2 momentti, 4 luvun 4 §:n 2 momentti, 5 §:n 3 momentti, "
        "6 §:n 2 momentti ja 8 §:n 3 momentti, 5 luvun 5 §."
    )
    ops = extract_voimaantulo_repeals(xml, "1979/925")

    got = {
        (op.target_section, op.target_chapter, op.target_paragraph)
        for op in ops
        if op.target_kind == "P"
    }

    assert ("5", "2", None) in got
    assert ("7", "2", 2) in got
    assert ("4", "4", 2) in got
    assert ("5", "4", 3) in got
    assert ("6", "4", 2) in got
    assert ("8", "4", 3) in got
    assert ("5", "5", None) in got


# ---------------------------------------------------------------------------
# _parent_title_variants — "laki "-prefix genitive form
# ---------------------------------------------------------------------------


def test_parent_title_variants_laki_prefix_generates_genitive_form() -> None:
    # "Laki X:stä" → also "X:stä annetun lain" (cross-statute prose form)
    variants = _parent_title_variants("Laki sosiaalihuollon asiakkaan asemasta ja oikeuksista")
    assert "laki sosiaalihuollon asiakkaan asemasta ja oikeuksista" in variants
    assert "sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain" in variants


def test_parent_title_variants_laki_prefix_no_duplicate() -> None:
    variants = _parent_title_variants("Laki X:stä")
    assert len(variants) == len(set(variants))


def test_parent_title_variants_non_laki_prefix_unchanged() -> None:
    # A title that doesn't start with "laki " should not get the genitive form
    variants = _parent_title_variants("Sosiaalihuoltolaki")
    assert "sosiaalihuoltolaki" in variants
    # "Sosiaalihuoltolaki" ends with "laki" → also generates "sosiaalihuoltolain"
    assert "sosiaalihuoltolain" in variants
    # But NOT " annetun lain" (because it doesn't start with "laki ")
    assert not any("annetun lain" in v for v in variants)


# ---------------------------------------------------------------------------
# extract_voimaantulo_repeals — title-only cross-statute repeal (2023/739 pattern)
# ---------------------------------------------------------------------------


def test_extract_voimaantulo_repeals_title_only_laki_prefix_pattern() -> None:
    """Repeal text uses '<rest> annetun lain' form without a statute citation.

    This mirrors 2023/739 which repeals 24§ and 24a§ of 2000/812 via:
      "sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain 24 ja 24 a §"
    """
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_15"><num>15 §</num>'
        f'      <heading>Voimaantulo ja siirtymäsäännökset</heading>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan potilaan asemasta ja oikeuksista annetun lain 11 §'
        f'        ja sosiaalihuollon asiakkaan asemasta ja oikeuksista annetun lain'
        f'        24 ja 24 a §, sellaisina kuin ne ovat, 24 § osaksi laissa 603/2022'
        f'        ja 24 a § laissa 290/2016.'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "2000/812",
        parent_title="Laki sosiaalihuollon asiakkaan asemasta ja oikeuksista",
    )
    labels = {op.target_section for op in ops}
    assert "24" in labels, f"24§ not in ops: {labels}"
    assert "24a" in labels, f"24a§ not in ops: {labels}"


def test_extract_voimaantulo_repeals_title_only_with_dated_annetun_lain_phrase() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_1"><num>1 §</num>'
        f'      <heading>Voimaantulo</heading>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan avioliittolain voimaanpanosta 13 päivänä kesäkuuta1929 annetun lain 6 §:n 1 momentti,'
        f'        sellaisena kuin se on 15 päivänä tammikuuta 1971 annetussa laissa (21/71).'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "1929/235",
        parent_title="Laki avioliittolain voimaanpanosta",
    )
    assert len(ops) == 1
    op = ops[0]
    assert op.target_section == "6"
    assert op.target_paragraph == 1


def test_extract_voimaantulo_repeals_does_not_bleed_into_following_sentence() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_12"><num>12 §</num>'
        f'      <heading>Voimaantulo</heading>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan avioliittolain voimaanpanosta 13 päivänä kesäkuuta 1929 annetun lain (235/29) 6 §:n 1 ja 2 momentti.'
        f'        Sovellettaessa tätä lakia sellaisen lapsen osalta, joka on syntynyt ennen 1 päivää tammikuuta 1958, on 4 §:n 2 momentissa säädetty kolmen vuoden aika luettava sanotusta päivästä.'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()
    ops = extract_voimaantulo_repeals(
        xml,
        "1929/235",
        parent_title="Laki avioliittolain voimaanpanosta",
    )
    got = {(op.target_section, op.target_paragraph, op.target_item) for op in ops}
    assert got == {("6", 1, None), ("6", 2, None)}


def test_extract_voimaantulo_repeals_ignores_repealed_amendment_act_reference() -> None:
    xml = (
        f'<act xmlns="{_AKN_NS}">'
        f'  <preamble><formula name="enactingClause">säädetään:</formula></preamble>'
        f'  <body>'
        f'    <section eId="sec_1"><num>1 §</num>'
        f'      <heading>Voimaantulo</heading>'
        f'      <subsection><content>'
        f'        Tällä lailla kumotaan Harmaan talouden selvitysyksiköstä annetun lain'
        f'        6 §:n muuttamisesta annettu laki (923/2017).'
        f'      </content></subsection>'
        f'    </section>'
        f'  </body>'
        f'</act>'
    ).encode()

    ops = extract_voimaantulo_repeals(
        xml,
        "2010/1207",
        parent_title="Laki Harmaan talouden selvitysyksiköstä",
    )

    assert ops == []
